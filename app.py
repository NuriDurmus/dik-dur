"""Dik-Dur giriş noktası: kamera döngüsü, sistem tepsisi, yaşam döngüsü.

Tamamen yerel çalışır: görüntü diske yazılmaz, Python seviyesinde ağ bağlantısı engellenir.
"""
import atexit, ctypes, hashlib, logging, logging.handlers, queue, socket, sys, threading, time
from ctypes import wintypes
from pathlib import Path

from brightness import Brightness
from posture import (BreakTimer, Posture, ZoneLearner, add_zone, adjust, body_ok, calibrate, cause,
                     make_zone, match_zone, metrics, pose_deviation, score_parts)
from store import DATA, DEFAULTS, LIMITS, LOG, db_connect, insert_sample, load_config, prune, save_config

__version__ = "1.5.1"
log = logging.getLogger("dikdur")

FROZEN = getattr(sys, "frozen", False)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
MODEL = HERE / "models" / "pose_landmarker_lite.task"
MODEL_SHA256 = "59929E1D1EE95287735DDD833B19CF4AC46D29BC7AFDDBBF6753C459690D574A"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# --- Sistem yardımcıları ------------------------------------------------------
def block_network():
    """Python soketleriyle dışarı bağlantıyı imkansız kılar.
    ponytail: native kod (C++ DLL) bunu aşabilir; kesin garanti için Windows Güvenlik Duvarı kuralı (README)."""
    def no_net(*a, **k):
        raise OSError("dik-dur: network disabled")
    for n in ("connect", "connect_ex", "sendto"):
        setattr(socket.socket, n, no_net)


def setup_logging():
    DATA.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    threading.excepthook = lambda a: log.error("thread hatası", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def message_box(text, error=True):
    user32.MessageBoxW(None, text, "Dik-Dur", 0x10 if error else 0x40)


_mutex = None


def single_instance():
    """İkinci kopya parlaklıkla çakışır; isimli mutex ile engelle."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, "Local\\dik-dur-single-instance")
    return ctypes.get_last_error() != 183          # ERROR_ALREADY_EXISTS


user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.SwitchDesktop.argtypes = user32.CloseDesktop.argtypes = [wintypes.HANDLE]


def session_locked():
    """Kilit ekranında giriş masaüstüne geçilemez. Kilitliyken kamera kapatılır (gizlilik + CPU)."""
    h = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not h:
        return True
    try:
        return not user32.SwitchDesktop(h)
    finally:
        user32.CloseDesktop(h)


def model_ok():
    return MODEL.exists() and hashlib.sha256(MODEL.read_bytes()).hexdigest().upper() == MODEL_SHA256


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(on):
    import winreg
    cmd = (f'"{sys.executable}" --tray' if FROZEN else
           f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}" --tray')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Dik-Dur", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "Dik-Dur")
            except FileNotFoundError:
                pass


def icon_image(color):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((6, 6, 58, 58), fill=color)
    return im


# (renk, ipucu metni)
STATUS = {
    "good": ("#2fbf71", "Duruş iyi"),
    "bad": ("#f0a030", "Duruş bozuluyor"),
    "dimmed": ("#e5534b", "Duruşunuzu düzeltin — ışık düşürüldü"),
    "absent": ("gray", "Kişi görünmüyor"),
    "paused": ("gray", "Duraklatıldı"),
    "locked": ("gray", "Ekran kilitli — kamera kapalı"),
    "nocam": ("#f0a030", "Kamera açılamadı"),
    "uncal": ("#3b82f6", "Kalibrasyon bekleniyor"),
}


# --- Uygulama ---------------------------------------------------------------
class App:
    VERSION = __version__
    STATUS = STATUS
    set_autostart = staticmethod(set_autostart)

    def __init__(self):
        import pystray
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        self.cfg = load_config()
        self.br = Brightness()
        self.post = Posture(self.cfg)
        self.breaks = BreakTimer()
        self.zones = ZoneLearner()
        self.paused = False
        self.last = None                     # son ölçüm (ayar penceresinde canlı gösterim)
        self.reopen_cam = False
        self.read_fails = 0
        self.br_failed = False
        self.status = None
        self.ui = queue.Queue()              # tepsi/döngü -> tkinter iş parçacığı
        self.capture = None                  # {"kind", "at", "result"}: pencereden istenen duruş ölçümü
        self.captures = {}                   # tür -> örnekler (sihirbaz eşik hesabı için)
        self.hold = False                    # sihirbaz açık: ışık/kayıt yok, önizleme var
        self.preview = None                  # (kare, landmark'lar) — yalnızca hold'dayken, bellekte
        self.last_zone = None                # şu an bakılan bölge (ekran)
        self.stop = threading.Event()
        self.landmarker = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=mpt.BaseOptions(model_asset_path=str(MODEL)),
                running_mode=vision.RunningMode.IMAGE))
        self.icons = {k: icon_image(c) for k, (c, _) in STATUS.items()}
        M = pystray.MenuItem
        self.icon = pystray.Icon("dik-dur", self.icons["absent"], "Dik-Dur", pystray.Menu(
            M("Ana pencere", lambda *_: self.ui.put("home"), default=True),
            M("Rapor", lambda *_: self.ui.put("report")),
            M("Ayarlar", lambda *_: self.ui.put("settings")),
            M(lambda i: "Devam" if self.paused else "Duraklat", self.toggle_pause),
            M("Yanlış alarm — eşiği gevşet", self.feedback_false_alarm),
            M("Şu an kötü oturuyorum — yakala", self.feedback_missed),
            pystray.Menu.SEPARATOR,
            M("Kurulum sihirbazı…", lambda *_: self.ui.put("wizard")),
            M("Hızlı kalibrasyon…", lambda *_: self.ui.put("calibrate")),
            M("Çıkış", self.quit)))
        atexit.register(self.cleanup)

    # --- tepsi / UI komutları
    def toggle_pause(self, *_):
        self.paused = not self.paused
        log.info("duraklat=%s", self.paused)

    def request_capture(self, kind, delay=3):
        """Pencereden çağrılır: `delay` sn sonra duruşu ölç. kind: upright (kalibrasyon) | slouch | lean."""
        self.capture = {"kind": kind, "at": time.monotonic() + delay, "result": None}

    def recalibrate(self, delay=3):
        self.request_capture("upright", delay)

    def reset_setup(self):
        """Baştan kurulum: kalibrasyon, ekranlar ve eşikler varsayılana döner. Geçmiş kayıtlar kalır."""
        keep = {k: self.cfg[k] for k in ("camera", "autostart", "retention_days")}
        self.undim()
        self.cfg.clear()                     # aynı dict: Posture vb. referanslar geçerli kalır
        self.cfg.update(DEFAULTS, **keep)
        save_config(self.cfg)
        self.captures, self.zones, self.post.kind, self.last = {}, ZoneLearner(), None, None
        log.info("kurulum sıfırlandı")

    def delete_zone(self, name):
        base = self.cfg["baseline"]
        base["zones"] = [z for z in base.get("zones", []) if z["name"] != name]
        save_config(self.cfg)
        log.info("bakış bölgesi silindi: %s", name)

    # --- geri bildirimle kendi kendine ayar
    def _feedback(self, target, only_worst, empty_msg):
        m, s, parts = self.last or (None, None, None)
        if not parts:
            return self.notify("Şu an kamerada görünmüyorsunuz.")
        changes = adjust(self.cfg, parts, target, LIMITS, only_worst)
        if not changes:
            return self.notify(empty_msg)
        save_config(self.cfg)
        log.info("geri bildirim ayarı: %s", changes)
        from ui import cfg_label
        self.notify("Ayarlandı: " + ", ".join(f"{cfg_label(k)} {cfg_label(k, o)} → {cfg_label(k, n)}"
                                              for k, o, n in changes))

    def feedback_false_alarm(self, *_):
        self.undim()
        self.post.kind = None
        self._feedback(0.6, False, "Şu an eşiği aşan bir ölçü yok; ayar gerekmedi.")

    def feedback_missed(self, *_):
        self._feedback(1.2, True, "Bu duruş kalibrasyondakiyle aynı görünüyor. Dik otururken yeniden kalibre edin.")

    def quit(self, *_):
        log.info("çıkış")
        self.stop.set()
        self.icon.stop()
        self.ui.put("quit")

    def notify(self, text):
        try:
            self.icon.notify(text)
        except Exception:
            log.exception("bildirim gösterilemedi")

    def set_status(self, key):
        if key != self.status:
            self.status = key
            self.icon.icon = self.icons[key]
            self.icon.title = f"Dik-Dur — {STATUS[key][1]}"

    # --- parlaklık
    def apply(self, act):
        try:
            if act == "dim" and self.br.normal is None:
                self.br.observe(time.monotonic())
            self.br.dim(self.cfg["dim_ratio"]) if act == "dim" else self.br.restore()
            self.br_failed = False
            log.info("parlaklık: %s", act)
        except Exception as e:                # DDC/WMI desteklenmiyor, monitör çıkarıldı vb.
            log.exception("parlaklık ayarlanamadı (%s)", act)
            self.post.dimmed = False
            if not self.br_failed:            # aynı hatayı her döngüde bildirme
                self.br_failed = True
                self.notify(f"Parlaklık ayarlanamadı: {e}")
            try:
                self.br.restore()
            except Exception:
                pass

    def undim(self):
        if self.post.dimmed:
            self.post.dimmed = False
            self.apply("restore")

    def cleanup(self):
        try:
            self.br.restore()
        except Exception:
            log.exception("çıkışta parlaklık geri yüklenemedi")

    # --- kamera / ölçüm
    def open_camera(self):
        import cv2
        cap = cv2.VideoCapture(int(self.cfg["camera"]), cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        if cap.isOpened():
            log.info("kamera %s açıldı", self.cfg["camera"])
            return cap
        cap.release()
        return None

    def measure(self, cap):
        """(kare okundu mu, ölçüm ya da None). Sihirbaz açıkken kare önizleme için bellekte tutulur."""
        import cv2, mediapipe as mp
        for _ in range(2):          # tampondaki eski kareleri at
            cap.grab()
        ok, frame = cap.retrieve()
        if not ok or frame is None:
            return False, None
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self.landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        self.preview = (rgb, res.pose_landmarks[0] if res.pose_landmarks else None) if self.hold else None
        if not res.pose_landmarks:
            return True, None
        return True, metrics(res.pose_landmarks[0], res.pose_world_landmarks[0], w, h)

    def do_capture(self, cap):
        c = self.capture
        self.undim()
        samples = []
        for _ in range(8):
            _, m = self.measure(cap)
            if m:
                samples.append(m)
            time.sleep(0.4)
        if c["kind"] == "upright":
            base, err = calibrate(samples)
            if err:
                log.warning("kalibrasyon reddedildi: %s", err)
                c["result"] = (False, err[0].upper() + err[1:] + "."
                               + (" Önceki kalibrasyon kullanılmaya devam ediyor." if self.cfg["baseline"] else ""))
                return
            self.cfg["baseline"] = base          # yeni kalibrasyon: eski bakış bölgeleri geçersiz
            save_config(self.cfg)
            self.captures = {"upright": samples}
            self.zones = ZoneLearner()
            log.info("kalibrasyon: %s", base)
            c["result"] = (True, f"Kalibrasyon tamam. Boyun {base['neck']:.0f}°"
                           + (f", bel {base['trunk']:.0f}°." if base["trunk"] is not None else
                              ". Kalçalar görünmediği için bel, omuzların aşağı kaymasından izlenecek."))
        elif c["kind"] == "zone":
            base = self.cfg["baseline"]
            body = [score_parts(s, base, self.cfg) for s in samples]
            if sum(body_ok(p, self.cfg) for p in body) < len(body) // 2 + 1:
                c["result"] = (False, "Gövdeniz dik değil; dik oturup diğer ekrana bakarak tekrar deneyin.")
                return
            z, err = make_zone(samples, base, source="manual")
            if err:
                c["result"] = (False, err[0].upper() + err[1:] + ".")
                return
            add_zone(base, z)
            save_config(self.cfg)
            log.info("bakış bölgesi eklendi (el ile): %s", z)
            c["result"] = (True, f"“{z['name']}” öğrenildi. Bu ekrana bakarken baş duruşu buna göre değerlendirilecek.")
        elif len(samples) < 4:
            c["result"] = (False, "Yüz ve omuzlar görünmüyor.")
        else:
            self.captures[c["kind"]] = samples
            c["result"] = (True, pose_deviation(samples, self.cfg["baseline"]))

    def step(self, cap, db):
        """Tek döngü adımı. (kamera, bekleme_sn) döner."""
        if self.paused or session_locked():
            if cap:
                cap.release()
            self.undim()
            self.post.kind = None                 # devamda eski "kötü" süresi sayılmasın
            self.set_status("paused" if self.paused else "locked")
            return None, 1
        if self.reopen_cam and cap:
            cap.release(); cap = None
        self.reopen_cam = False
        if cap is None:
            cap = self.open_camera()
            if cap is None:                     # başka uygulama kullanıyor / takılı değil
                self.set_status("nocam")
                return None, 10
        if self.capture and self.capture["result"] is None and time.monotonic() >= self.capture["at"]:
            self.do_capture(cap)
            return cap, 0.1
        if not self.cfg["baseline"] and not self.hold:
            self.set_status("uncal")
            return cap, 1

        ok, m = self.measure(cap)
        if not ok:                              # kamera çıkarıldı / uyku sonrası bozuldu
            self.read_fails += 1
            if self.read_fails >= 3:
                log.warning("kamera okunamıyor, yeniden açılacak")
                cap.release()
                self.read_fails = 0
                return None, 2
            return cap, 1
        self.read_fails = 0
        base = self.cfg["baseline"]
        parts = score_parts(m, base, self.cfg) if m and base else None
        s = max(parts.values()) if parts else None
        zone = match_zone(m, base, self.cfg)[0] if m and base else None
        self.last = (m, s, parts)
        self.last_zone = zone
        if self.hold:                           # sihirbaz: sadece önizleme, karar/kayıt yok
            self.undim()
            self.post.kind = None
            return cap, 0.15
        act = self.post.update(s, time.monotonic())
        if act:
            self.apply(act)
        if m and self.cfg["auto_zones"]:
            z = self.zones.update(m, parts, base, self.cfg, time.monotonic())
            if z:
                add_zone(base, z)
                save_config(self.cfg)
                log.info("bakış bölgesi öğrenildi (otomatik): %s", z)
                self.notify(f"Sık baktığınız yeni bir ekran öğrenildi: {z['name']}. Oraya bakarken "
                            "baş duruşu buna göre değerlendirilecek. Ayarlar > Bakış bölgeleri'nden silebilirsiniz.")
        if m:
            yaw_rel = (m["head_yaw"] - base["head_yaw"]
                       if m["head_yaw"] is not None and base.get("head_yaw") is not None else None)
            insert_sample(db, {
                "ts": time.time(), "score": s, "state": self.post.kind, "dimmed": int(self.post.dimmed),
                "side": int(m["side"]), "cause": cause(parts), "zone": zone or "Diğer yön",
                "head_yaw": yaw_rel,
                "closer": m["sw"] / base["sw"] - 1 if m["sw"] and base.get("sw") else None,
                **{k: m[k] for k in ("neck", "trunk", "neck_lat", "trunk_lat",
                                     "head_tilt", "shoulder_tilt")},
                "sink": (m["sh_y"] - base["sh_y"]) / base["sw"]
                        if m["sh_y"] is not None and base.get("sh_y") is not None and base.get("sw") else None})
        if self.breaks.update(m is not None, time.monotonic(), self.cfg["break_remind_min"] * 60):
            mins = self.breaks.sitting_secs(time.monotonic()) / 60
            log.info("mola hatırlatması (%.0f dk)", mins)
            self.notify(f"{mins:.0f} dakikadır kesintisiz oturuyorsunuz. "
                        "Kalkıp birkaç dakika yürüyün, boyun ve omuzlarınızı esnetin.")
        self.set_status("dimmed" if self.post.dimmed else "absent" if s is None
                        else "bad" if self.post.kind == "bad" else "good")
        # adaptif örnekleme: kişi yoksa 0.5 FPS, iyiyse 2, sınıra yakın / ışık düşükken 4 FPS
        # (düzelince hızlı fark etmek için). Model ~15 ms/kare: 4 FPS'de bile tek çekirdeğin ~%6'sı
        return cap, 2 if s is None else 0.25 if (s > 0.5 or self.post.dimmed) else 0.5

    def loop(self):
        db = db_connect()
        prune(db, self.cfg["retention_days"])
        cap, last_commit, last_observe = None, time.monotonic(), 0.0
        try:
            while not self.stop.is_set():
                try:
                    cap, wait = self.step(cap, db)
                except Exception:                 # döngü asla sessizce ölmesin
                    log.exception("döngü hatası")
                    self.undim()
                    if cap:
                        cap.release()
                    cap, wait = None, 5
                if time.monotonic() - last_commit > 30:   # diske her saniye değil, 30 sn'de bir
                    db.commit(); last_commit = time.monotonic()
                if time.monotonic() - last_observe > 60 and not self.post.dimmed:
                    try:
                        self.br.observe(time.monotonic())
                    except Exception:
                        log.exception("parlaklık okunamadı")
                    last_observe = time.monotonic()
                self.stop.wait(wait)
        finally:
            if cap:
                cap.release()
            db.commit()
            db.close()

    def run(self):
        import ui
        t = threading.Thread(target=self.loop, name="loop")
        t.start()
        self.icon.run_detached()          # tepsi kendi iş parçacığında
        if self.cfg["autostart"]:
            try:
                set_autostart(True)           # eski sürümün kaydını güncelle (--tray)
            except OSError:
                log.exception("başlangıç kaydı güncellenemedi")
        if not self.cfg["baseline"]:
            self.ui.put("wizard")
        elif "--tray" not in sys.argv:    # Windows açılışında sessiz; elle açınca ana pencere
            self.ui.put("home")
        try:
            ui.mainloop(self)             # ana iş parçacığı: pencereler
        finally:
            self.stop.set()
            t.join(timeout=10)
            self.cleanup()


def main():
    setup_logging()
    block_network()
    log.info("Dik-Dur %s başlıyor", __version__)
    if not single_instance():
        message_box("Dik-Dur zaten çalışıyor (sistem tepsisine bakın).", error=False)
        return
    if not model_ok():
        log.error("model eksik veya bütünlüğü bozuk: %s", MODEL)
        message_box("Model dosyası eksik veya değiştirilmiş. Uygulamayı yeniden kurun.")
        return
    try:
        App().run()
    except Exception:
        log.exception("ölümcül hata")
        message_box(f"Beklenmeyen hata. Ayrıntılar:\n{LOG}")
        sys.exit(1)


if __name__ == "__main__":
    main()
