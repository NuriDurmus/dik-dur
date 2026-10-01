"""Kurulum sihirbazı: canlı önizlemeyle adım adım kalibrasyon ve eşiklerin otomatik ayarı."""
import logging
from tkinter import messagebox

import customtkinter as ctk

from posture import SENSITIVITY, tune
from store import save_config
from ui import BAD, GOOD, MUTED, WARN, capture_progress, capture_state, cfg_label, font, window

log = logging.getLogger("dikdur")

PREVIEW_W, PREVIEW_H = 360, 270
SKELETON = [(11, 12), (7, 8), (11, 23), (12, 24), (23, 24)]   # çizilecek iskelet çizgileri
KEYPOINTS = (0, 7, 8, 11, 12, 23, 24)


def preview_image(app):
    """Kameranın son karesi + iskelet, ayna görüntüsü. Yalnızca bellekte, hiçbir yere yazılmaz."""
    from PIL import Image, ImageDraw, ImageOps
    if not app.preview:
        im = Image.new("RGB", (PREVIEW_W, PREVIEW_H), "#202020")
        ImageDraw.Draw(im).text((PREVIEW_W // 2 - 50, PREVIEW_H // 2), "Kamera bekleniyor...", fill="#aaa")
        return im
    rgb, lms = app.preview
    im = ImageOps.mirror(Image.fromarray(rgb)).resize((PREVIEW_W, PREVIEW_H))
    if lms:
        d = ImageDraw.Draw(im)
        pt = lambda i: ((1 - lms[i].x) * PREVIEW_W, lms[i].y * PREVIEW_H)
        seen = lambda i: lms[i].visibility > 0.5
        for a, b in SKELETON:
            if seen(a) and seen(b):
                d.line([pt(a), pt(b)], fill=GOOD, width=3)
        for i in KEYPOINTS:
            x, y = pt(i)
            d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=GOOD if seen(i) else "#777")
    return im


def visibility(app):
    """(yüz, omuzlar, kalçalar) görünüyor mu."""
    lms = app.preview[1] if app.preview else None
    if not lms:
        return False, False, False
    v = lambda *ix: all(lms[i].visibility > 0.5 for i in ix)
    return v(0, 7) or v(0, 8), v(11, 12), v(23, 24)


def wizard_window(root, app):
    win = window(root, "Dik-Dur · Kurulum", "820x620")
    win.resizable(False, False)
    app.hold, app.capture = True, None       # sihirbaz boyunca ışık/kayıt yok, önizleme açık
    st = {"step": 0, "sens": "Dengeli", "tuned": {}}

    def close():
        app.hold, app.capture, app.preview = False, None, None
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close)

    head = ctk.CTkFrame(win, fg_color="transparent")
    head.pack(fill="x", padx=24, pady=(20, 0))
    step_lbl = ctk.CTkLabel(head, text="", font=font(12), text_color=MUTED)
    step_lbl.pack(anchor="w")
    title = ctk.CTkLabel(head, text="", font=font(24, "bold"))
    title.pack(anchor="w")
    prog = ctk.CTkProgressBar(head, progress_color=GOOD, height=6)
    prog.pack(fill="x", pady=(8, 0))

    mid = ctk.CTkFrame(win, fg_color="transparent")
    mid.pack(fill="both", expand=True, padx=24, pady=16)
    cam = ctk.CTkLabel(mid, text="")
    cam.pack(side="left", anchor="n")
    side = ctk.CTkFrame(mid, fg_color="transparent")
    side.pack(side="left", fill="both", expand=True, padx=(20, 0))

    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill="x", padx=24, pady=(0, 20))
    ghost = dict(fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray85", "gray25"))
    back = ctk.CTkButton(bar, text="← Geri", width=90, **ghost)
    back.pack(side="left")
    nxt = ctk.CTkButton(bar, text="İleri →", width=120)
    nxt.pack(side="right")
    skip = ctk.CTkButton(bar, text="Atla", width=80, **ghost)

    live = {}          # adım içinde periyodik güncellenen öğeler

    def clear():
        for w in side.winfo_children():
            w.destroy()
        live.clear()
        skip.pack_forget()

    def text(t, size=13, color=None, bold=False, pady=(0, 10)):
        lbl = ctk.CTkLabel(side, text=t, font=font(size, "bold" if bold else "normal"), text_color=color,
                           wraplength=380, justify="left", anchor="w")
        lbl.pack(anchor="w", pady=pady)
        return lbl

    def measure_block(kind):
        status = text("Hazır olduğunuzda Ölç'e basın.", 14, bold=True, pady=(6, 10))
        pbar = ctk.CTkProgressBar(side, progress_color=GOOD, height=8, width=380)
        pbar.set(0)
        pbar.pack(anchor="w", pady=(0, 12))
        btn = ctk.CTkButton(side, text="Ölç", width=120)
        btn.pack(anchor="w")
        def start():
            btn.configure(state="disabled")
            app.request_capture(kind, 3)
        btn.configure(command=start)
        live["capture"] = (kind, status, btn, pbar)

    # --- adımlar
    def s_camera():
        text("Kameranın sizi rahatça görebildiğinden emin olalım.")
        live["checks"] = [text("", 14, pady=(0, 4)) for _ in range(3)]
        text("İpucu: Kamerayı ekranın üstüne, tam karşınıza koyun. İki omzunuz da görünmeli. "
             "Kalçalarınız da görünürse bel açısı doğrudan ölçülür; görünmezse bel, omuzların aşağı "
             "kaymasından izlenir.", 12, MUTED, pady=(10, 10))
        row = ctk.CTkFrame(side, fg_color="transparent")
        row.pack(anchor="w")
        ctk.CTkLabel(row, text="Kamera", font=font(13)).pack(side="left", padx=(0, 10))
        var = ctk.StringVar(value=str(app.cfg["camera"]))
        def pick(v):
            app.cfg["camera"] = int(v)
            app.reopen_cam = True
        ctk.CTkSegmentedButton(row, values=["0", "1", "2", "3"], variable=var, command=pick).pack(side="left")

    def s_upright():
        text("Dik oturun", 15, bold=True)
        text("•  Kalçanızı sandalyenin arkasına yaslayın, belinizi dikleştirin\n"
             "•  Omuzlarınız gevşek ve aynı hizada olsun\n"
             "•  Başınızı dik tutup ekrana düz bakın\n"
             "•  Ölçüm sırasında 3 saniye kıpırdamayın")
        measure_block("upright")

    def s_zone():
        text("Başka ekranlarınız var mı?", 15, bold=True)
        text("Her ek ekran için (ör. laptop, yandaki monitör): dik oturmaya devam edin, o ekrana bakın ve "
             "Ölç'e basın. Birden fazla ekranınız varsa her biri için tekrarlayın. O ekranlara bakarken "
             "başınızın eğik olması kötü duruş sayılmaz. Tek ekransa atlayın; sık baktığınız ekranlar "
             "sonradan otomatik de öğrenilir.", 12)
        measure_block("zone")
        live["zones"] = text("", 13, pady=(12, 0))
        skip.pack(side="right", padx=8)

    def s_slouch():
        text("Şimdi her zamanki kötü duruşunuza geçin", 15, bold=True)
        text("Kamburlaşın, sandalyede yayılın ya da ekrana doğru uzanın; kendinizi en çok hangisinde "
             "yakalıyorsanız. Uygulama eşikleri bu farka göre kendisi ayarlayacak.")
        measure_block("slouch")
        skip.pack(side="right", padx=8)

    def s_lean():
        text("Bir yana yaslanın", 15, bold=True)
        text("Dirseğinizi kolçağa dayayıp gövdenizi bir yana yatırın ya da başınızı yana eğin. "
             "İsterseniz atlayın; varsayılan yana yatma eşiği kullanılır.")
        measure_block("lean")
        skip.pack(side="right", padx=8)

    def s_finish():
        text("Ne kadar hassas olsun?", 15, bold=True)
        text("Rahat: sadece belirgin kötü duruşta uyarır.  Dengeli: kötü duruşa giden yolun yarısında.  "
             "Sıkı: küçük sapmalarda bile.", 12, MUTED)
        var = ctk.StringVar(value=st["sens"])
        table = ctk.CTkLabel(side, text="", font=font(13), justify="left", anchor="w", wraplength=380)

        def recompute(_=None):
            st["sens"] = var.get()
            poses = [app.captures[k] for k in ("slouch", "lean") if k in app.captures]
            st["tuned"] = tune(app.cfg["baseline"], poses, SENSITIVITY[st["sens"]]) if poses else {}
            if not poses:
                table.configure(text="Kötü duruş ölçülmediği için varsayılan eşikler kullanılacak.")
            elif not st["tuned"]:
                table.configure(text="Kötü duruş dik duruştan belirgin ayrışmadı; varsayılan eşikler kalacak. "
                                     "Geri dönüp kötü duruşa daha belirgin geçerek tekrar ölçebilirsiniz.")
            else:
                table.configure(text="Sizin duruşunuzdan hesaplanan eşikler:\n" + "\n".join(
                    f"•  {cfg_label(k)}: {cfg_label(k, app.cfg[k])} → {cfg_label(k, v)}"
                    for k, v in st["tuned"].items()))

        ctk.CTkSegmentedButton(side, values=list(SENSITIVITY), variable=var, command=recompute).pack(
            anchor="w", pady=(0, 10))
        table.pack(anchor="w", pady=(0, 12))
        st["remind"] = ctk.BooleanVar(value=app.cfg["break_remind_min"] > 0)
        st["auto"] = ctk.BooleanVar(value=app.cfg["autostart"])
        ctk.CTkSwitch(side, text="50 dakikada bir mola hatırlat", variable=st["remind"], font=font(13),
                      progress_color=GOOD).pack(anchor="w", pady=4)
        ctk.CTkSwitch(side, text="Windows açılınca başlat", variable=st["auto"], font=font(13),
                      progress_color=GOOD).pack(anchor="w", pady=4)
        text("Sonradan yanlış alarm olursa tepsi menüsündeki “Yanlış alarm” ile uygulama eşiği kendisi "
             "gevşetir; kaçırırsa “Şu an kötü oturuyorum” ile sıkılaştırır.", 12, MUTED, pady=(10, 0))
        recompute()

    STEPS = [("Kamera", s_camera), ("Dik duruş", s_upright), ("İkinci ekran", s_zone), ("Kötü duruş", s_slouch),
             ("Yana yatma", s_lean), ("Hassasiyet", s_finish)]

    def finish():
        try:
            app.cfg.update(st["tuned"])
            app.cfg["break_remind_min"] = (app.cfg["break_remind_min"] or 50) if st["remind"].get() else 0
            if st["auto"].get() != app.cfg["autostart"]:
                app.set_autostart(st["auto"].get())
                app.cfg["autostart"] = st["auto"].get()
            save_config(app.cfg)
        except Exception as e:
            log.exception("sihirbaz ayarları kaydedilemedi")
            return messagebox.showerror("Dik-Dur", f"Ayarlar kaydedilemedi: {e}", parent=win)
        log.info("sihirbaz tamamlandı: %s %s", st["sens"], st["tuned"])
        close()
        app.notify("Kurulum tamam. Dik-Dur duruşunuzu izliyor.")
        app.ui.put("home")

    def go(i):
        st["step"] = i
        app.capture = None
        clear()
        name, build = STEPS[i]
        step_lbl.configure(text=f"Adım {i + 1} / {len(STEPS)}")
        title.configure(text=name)
        prog.set((i + 1) / len(STEPS))
        build()
        back.configure(state="normal" if i else "disabled", command=lambda: go(i - 1))
        last = i == len(STEPS) - 1
        nxt.configure(text="Bitir ✓" if last else "İleri →", command=finish if last else lambda: go(i + 1))
        skip.configure(command=lambda: go(i + 1))

    def tick():
        if not win.winfo_exists():
            return
        im = preview_image(app)
        cam.configure(image=ctk.CTkImage(light_image=im, dark_image=im, size=(PREVIEW_W, PREVIEW_H)))
        if "checks" in live:
            face, shoulders, hips = visibility(app)
            m = app.last[0] if app.last else None
            for lbl, okv, txt, optional in zip(live["checks"], (face, shoulders, hips),
                                               ("Yüz görünüyor", "İki omuz görünüyor", "Kalçalar görünüyor"),
                                               (False, False, True)):
                lbl.configure(text=("✓  " if okv else "–  " if optional else "✗  ") + txt
                              + (" (isteğe bağlı)" if optional and not okv else ""),
                              text_color=GOOD if okv else MUTED if optional else BAD)
            if m and m["side"]:
                live["checks"][2].configure(text="–  Kamera yanda: yana yatma ölçülemez", text_color=WARN)
            nxt.configure(state="normal" if face and shoulders else "disabled")
        elif "capture" in live:
            kind, status, btn, pbar = live["capture"]
            t, color, done, _ = capture_state(app)
            status.configure(text=t, text_color=color)
            pbar.set(capture_progress(app))
            if done:
                btn.configure(state="normal", text="Bir ekran daha ekle" if kind == "zone" else "Tekrar ölç")
            if "zones" in live:
                names = ["Ana ekran"] + [z["name"] for z in (app.cfg["baseline"] or {}).get("zones", [])]
                live["zones"].configure(text="Tanımlı ekranlar:\n" + "\n".join(f"  {i + 1}. {n}"
                                                                               for i, n in enumerate(names)))
            # dik duruş zorunlu (kalibrasyon); kötü duruş adımları atlanabilir
            nxt.configure(state="normal" if kind != "upright" or app.cfg["baseline"] else "disabled")
        else:
            nxt.configure(state="normal")
        win.after(150, tick)

    go(0)
    tick()
    return win
