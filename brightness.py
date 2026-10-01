"""Monitör parlaklığı (WMI + DDC/CI). Orijinal değer diske yazılır: çökme sonrası geri yüklenir."""
import json, logging, threading, time

from store import STATE, atomic_write

log = logging.getLogger("dikdur")


EXTERNAL_DROP = 0.6     # normalin bu oranının altına ani düşüş: başka yazılımın karartması
ACCEPT_AFTER = 600      # düşük değer bu kadar sn sürerse kullanıcının kendi ayarı kabul edilir


class Brightness:
    """Tüm monitörleri tek tek yönetir (her biri kendi değerine döner).

    Windows / üretici yazılımları (ör. Lenovo, "başka yere bakınca karart") laptop ekranını
    kendiliğinden kısabilir. O an karartılırsa kısık değer "orijinal" sanılır ve geri yüklemede
    ekran kısık kalır. Bunu önlemek için karartılmamışken normal düzey izlenir (observe).
    """

    def __init__(self, sbc=None):
        if sbc is None:
            import screen_brightness_control as sbc
        self.sbc, self.orig, self.lock = sbc, None, threading.Lock()
        self.normal, self.low_since = None, []
        if STATE.exists():                       # önceki çökmeden/kapanmadan kurtarma
            try:
                orig = json.loads(STATE.read_text(encoding="utf-8"))["orig"]
                log.info("önceki oturumdan kalan parlaklık geri yükleniyor: %s", orig)
                self._set(orig)
            except Exception:
                log.exception("parlaklık kurtarma başarısız")
            finally:
                STATE.unlink(missing_ok=True)

    def _get(self):
        return [int(v) for v in self.sbc.get_brightness()]

    def _set(self, vals, n=None):
        n = len(self.sbc.list_monitors()) if n is None else n
        if n != len(vals):
            log.warning("monitör sayısı değişti (%d -> %d)", len(vals), n)
        for i, v in enumerate(vals[:n]):
            self.sbc.set_brightness(max(0, min(100, int(v))), display=i)

    def _fade(self, target, secs, steps):
        """DDC/CI (harici monitör) komut başına ~150 ms sürer: adım sayısı az tutulur."""
        if steps == 1:
            return self._set(target, len(target))
        cur = self._get()
        n = len(cur)                     # monitör listesi geçiş başına bir kez (WMI sorgusu yavaş)
        for i in range(1, steps + 1):
            t0 = time.monotonic()
            self._set([c + (t - c) * i / steps for c, t in zip(cur, target)], n)
            time.sleep(max(0.0, secs / steps - (time.monotonic() - t0)))

    def observe(self, now):
        """Karartılmamışken (döngüden ~60 sn'de bir) ekranların normal parlaklığını öğren."""
        with self.lock:
            if self.orig is not None:
                return
            cur = self._get()
            if self.normal is None or len(cur) != len(self.normal):
                self.normal, self.low_since = list(cur), [None] * len(cur)
                return
            for i, (c, n) in enumerate(zip(cur, self.normal)):
                if c >= n * EXTERNAL_DROP:
                    self.normal[i], self.low_since[i] = c, None
                elif self.low_since[i] is None:
                    self.low_since[i] = now
                elif now - self.low_since[i] >= ACCEPT_AFTER:
                    self.normal[i], self.low_since[i] = c, None

    def dim(self, ratio):
        with self.lock:
            # orig zaten varsa (önceki dim yarıda kaldıysa) üzerine yazma:
            # yoksa yarı karanlık değer "orijinal" sanılır ve ekran kalıcı kararır
            if self.orig is None:
                cur = self._get()
                normal = self.normal if self.normal and len(self.normal) == len(cur) else cur
                self.orig = [n if c < n * EXTERNAL_DROP else c for c, n in zip(cur, normal)]
                if self.orig != cur:
                    log.warning("ekran dışarıdan kısılmış görünüyor (%s); normal düzey %s esas alındı",
                                cur, self.orig)
                atomic_write(STATE, json.dumps({"orig": self.orig}))
            self._fade([max(1, round(v * (1 - ratio))) for v in self.orig], 0.4, 3)

    def restore(self):
        with self.lock:
            if self.orig is None:
                return
            self._fade(self.orig, 0, 1)        # düzelince anında geri dön
            time.sleep(0.2)
            got = self._get()
            if any(abs(g - o) > 2 for g, o in zip(got, self.orig)):   # bazı sürücüler komutu yutar
                log.warning("geri yükleme tutmadı (%s, beklenen %s), tekrar deneniyor", got, self.orig)
                self._set(self.orig, len(self.orig))
            self.orig = None
            STATE.unlink(missing_ok=True)
