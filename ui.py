"""Ayar ve rapor pencereleri (customtkinter, modern koyu/açık tema)."""
import logging
import time
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox

import customtkinter as ctk

from store import advice, report, save_config

log = logging.getLogger("dikdur")

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("green")

FONT = "Segoe UI Variable Display"
GOOD, BAD, WARN, MUTED = "#2fbf71", "#e5534b", "#f0a030", "#8b949e"
TEXT = ("gray10", "gray90")   # varsayılan metin rengi (açık, koyu); configure() None kabul etmez
DAYS_TR = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]

# grup -> (anahtar, etiket, min, max, gösterim çarpanı, birim)
SLIDERS = {
    "Boyun ve bel": [
        ("neck_deg", "Boyun öne eğilme eşiği", 5, 40, 1, "°"),
        ("trunk_deg", "Bel öne eğilme eşiği (kalçalar görünürse)", 5, 40, 1, "°"),
        ("sink_tol", "Bel çökmesi: omuzların aşağı kayması", 5, 50, 100, "%"),
        ("combined_deg", "Boyun + bel toplam eşiği", 5, 60, 1, "°"),
    ],
    "Yana yatma": [
        ("lateral_deg", "Gövde / boyun yana yatma eşiği", 3, 30, 1, "°"),
        ("head_tilt_deg", "Başı yana eğme eşiği", 5, 40, 1, "°"),
        ("tilt_deg", "Omuz asimetrisi eşiği", 3, 30, 1, "°"),
    ],
    "Ekran ve baş": [
        ("drop_tol", "Baş düşüklüğü toleransı (boyun öne uzama)", 5, 60, 100, "%"),
        ("nod_deg", "Başı aşağı eğme sınırı (ekranın altı serbest)", 20, 80, 1, "°"),
        ("tolerance", "Ekrana yaklaşma toleransı", 5, 40, 100, "%"),
        ("yaw_gate", "Baş bu kadar dönükken baş ölçülerini yok say", 5, 40, 1, "°"),
        ("yaw_deg", "Baş yana dönük sayılma açısı (sadece rapor)", 10, 60, 1, "°"),
    ],
    "Davranış": [
        ("dim_ratio", "Parlaklık düşürme", 10, 90, 100, "%"),
        ("bad_secs", "Kötü duruşta bekleme", 1, 30, 1, " sn"),
        ("good_secs", "Düzelince geri alma", 1, 15, 1, " sn"),
        ("absent_secs", "Kişi yokken geri alma", 10, 120, 1, " sn"),
        ("break_remind_min", "Mola hatırlatma (kesintisiz oturma)", 0, 120, 1, " dk"),
    ],
}

PART_TR = {"neck": "boyun öne", "trunk": "bel öne", "combined": "boyun + bel", "lateral": "yana yatma",
           "head_tilt": "baş yana eğik", "shoulder": "omuz asimetrisi", "drop": "boyun öne uzamış",
           "near": "ekrana yakın", "sink": "bel çökmüş", "nod": "baş çok aşağı eğik"}
CAUSES = [("neck", "Boyun öne", BAD), ("trunk", "Bel öne", WARN), ("both", "Boyun + bel", "#a371f7"),
          ("lateral", "Yana yatma", "#3b82f6"), ("near", "Ekrana yakın", "#14b8a6"),
          ("other", "Diğer", MUTED)]


SLIDER_INFO = {k: (label, mul, unit) for items in SLIDERS.values() for k, label, _, _, mul, unit in items}


def cfg_label(key, value=None):
    """Ayar adı ya da değeri okunur biçimde: cfg_label('neck_deg') / cfg_label('neck_deg', 15)."""
    label, mul, unit = SLIDER_INFO.get(key, (key, 1, ""))
    return label if value is None else f"{value * mul:.0f}{unit}"


def describe(d):
    """pose_deviation çıktısını insan diline çevirir."""
    out = []
    if d.get("neck", 0) >= 2: out.append(f"boyun {d['neck']:.0f}° öne")
    if d.get("trunk", 0) >= 2: out.append(f"bel {d['trunk']:.0f}° öne")
    if d.get("sink", 0) >= 0.03: out.append(f"omuzlar %{100 * d['sink']:.0f} aşağı")
    lat = max(d.get("neck_lat", 0), d.get("trunk_lat", 0))
    if lat >= 2: out.append(f"yana {lat:.0f}°")
    if d.get("head_tilt", 0) >= 3: out.append(f"baş {d['head_tilt']:.0f}° eğik")
    if d.get("shoulder_tilt", 0) >= 2: out.append(f"omuz {d['shoulder_tilt']:.0f}° eğik")
    return " · ".join(out) or "belirgin bir fark ölçülmedi"


def capture_state(app):
    """(metin, renk, bitti mi, sonuç) — pencereler ölçüm durumunu bununla gösterir."""
    c = app.capture
    if c is None:
        return "Hazır olduğunuzda Ölç'e basın.", TEXT, False, None
    if c["result"] is None:
        left = c["at"] - time.monotonic()
        return (f"Pozisyonu alın… {int(left) + 1}" if left > 0 else "Ölçülüyor, kıpırdamayın…"), WARN, False, None
    ok, res = c["result"]
    if not ok:
        return res, BAD, True, c["result"]
    return (res if isinstance(res, str) else "Ölçüldü: " + describe(res)), GOOD, True, c["result"]


MEASURE_SECS = 3.5   # do_capture: 8 örnek x 0.4 sn + işleme


def capture_progress(app):
    """0-1: geri sayım + ölçümün ne kadarı tamamlandı (ilerleme çubuğu için)."""
    c = app.capture
    if c is None:
        return 0.0
    if c["result"] is not None:
        return 1.0
    now = time.monotonic()
    total = 3 + MEASURE_SECS                      # 3 sn geri sayım + ölçüm
    elapsed = 3 - (c["at"] - now) if now < c["at"] else 3 + (now - c["at"])
    return max(0.0, min(0.97, elapsed / total))


def fmt_dur(sec):
    m = int(sec // 60)
    return f"{m // 60} sa {m % 60:02d} dk" if m >= 60 else f"{m} dk"


def fmt_deg(v):
    return "–" if v is None else f"{v:.0f}°"


def fmt_lat(v):
    """İşaretli yanal açı: + = kişinin solu."""
    return "–" if v is None else f"{abs(v):.0f}° {'sol' if v > 0 else 'sağ'}" if abs(v) >= 1 else "0°"


def font(size, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def card(parent, **kw):
    return ctk.CTkFrame(parent, corner_radius=14, **kw)


def canvas_bg():
    return "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#dbdbdb"   # CTkFrame rengi


def window(root, title, size):
    win = ctk.CTkToplevel(root)
    win.title(title)
    win.geometry(size)
    win.after(250, lambda: (win.lift(), win.focus_force()))   # CTkToplevel bazen arkada açılır
    return win


# --- Ayarlar -----------------------------------------------------------------
def settings_window(root, app):
    win = window(root, "Dik-Dur · Ayarlar", "540x760")
    win.resizable(False, True)
    body = ctk.CTkScrollableFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=(16, 0))

    ctk.CTkLabel(body, text="Ayarlar", font=font(24, "bold")).pack(anchor="w")
    ctk.CTkLabel(body, text="Eşikler kalibrasyondaki dik duruşunuza göre sapmadır. Kaydedince hemen uygulanır.",
                 font=font(12), text_color=MUTED, wraplength=480, justify="left").pack(anchor="w", pady=(0, 12))

    # canlı durum kartı
    live = card(body)
    live.pack(fill="x", pady=(0, 12))
    dot = ctk.CTkLabel(live, text="●", font=font(28), text_color=MUTED)
    dot.grid(row=0, column=0, rowspan=2, padx=(16, 10), pady=12, sticky="n")
    status = ctk.CTkLabel(live, text="", font=font(15, "bold"), anchor="w")
    status.grid(row=0, column=1, sticky="w", pady=(12, 0))
    detail = ctk.CTkLabel(live, text="", font=font(12), text_color=MUTED, anchor="w", justify="left")
    detail.grid(row=1, column=1, sticky="w", pady=(0, 12))
    fb = ctk.CTkFrame(live, fg_color="transparent")
    fb.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12))
    small = dict(height=28, font=font(12), fg_color="transparent", border_width=1,
                 text_color=("gray10", "gray90"))
    ctk.CTkButton(fb, text="Yanlış alarm — gevşet", command=app.feedback_false_alarm, **small).pack(side="left", padx=4)
    ctk.CTkButton(fb, text="Kötü oturuyorum — yakala", command=app.feedback_missed, **small).pack(side="left", padx=4)
    ctk.CTkButton(fb, text="Kurulum sihirbazı", command=lambda: app.ui.put("wizard"), **small).pack(side="left", padx=4)

    def tick():
        if not win.winfo_exists():
            return
        m, s, parts = app.last or (None, None, None)
        b = app.cfg["baseline"]
        sitting = app.breaks.sitting_secs(time.monotonic())
        if not m:
            dot.configure(text_color=MUTED); status.configure(text="Kişi görünmüyor")
            detail.configure(text="Yüz ve omuzlar kamerada görünmeli.")
        else:
            bad = s is not None and s > 1
            dot.configure(text_color=BAD if bad else GOOD)
            top = sorted(parts.items(), key=lambda kv: -kv[1])[:3] if parts else []
            status.configure(text=f"Duruşunuzu düzeltin · {PART_TR.get(top[0][0], top[0][0])}" if bad else "Duruş iyi")
            detail.configure(text=(
                "Eşiğe yakınlık: " + ", ".join(f"{PART_TR.get(k, k)} %{100 * v:.0f}" for k, v in top) + "\n"
                f"Öne: boyun {fmt_deg(m['neck'])}, bel {fmt_deg(m['trunk'])}   ·   "
                f"Yana: boyun {fmt_lat(m['neck_lat'])}, bel {fmt_lat(m['trunk_lat'])}\n"
                f"Baş eğimi {fmt_lat(m['head_tilt'])}  ·  omuz {fmt_lat(m['shoulder_tilt'])}  ·  "
                f"baş dönüşü {fmt_lat(m['head_yaw'])}\n"
                f"Bakılan: {app.last_zone or 'diğer yön (baş ölçüleri yok sayılıyor)'}  ·  "
                f"kesintisiz oturma {fmt_dur(sitting)}  ·  {'yandan' if m['side'] else 'önden'} kamera"
                + ("" if b else "  ·  kalibrasyon yok")))
        win.after(500, tick)

    values = {}
    for group, items in SLIDERS.items():
        ctk.CTkLabel(body, text=group, font=font(14, "bold")).pack(anchor="w", pady=(4, 6))
        sl = card(body)
        sl.pack(fill="x", pady=(0, 12))
        sl.grid_columnconfigure(0, weight=1)
        for r, (key, label, lo, hi, mul, unit) in enumerate(items):
            pad = (12 if r == 0 else 6, 0)
            ctk.CTkLabel(sl, text=label, font=font(13)).grid(row=2 * r, column=0, sticky="w", padx=16, pady=pad)
            val = ctk.CTkLabel(sl, text="", font=font(13, "bold"))
            val.grid(row=2 * r, column=1, sticky="e", padx=16, pady=pad)
            var = tk.DoubleVar(value=min(hi, max(lo, app.cfg[key] * mul)))
            upd = lambda *_, v=var, l=val, u=unit, k=key: l.configure(
                text="Kapalı" if k == "break_remind_min" and v.get() == 0 else f"{v.get():.0f}{u}")
            ctk.CTkSlider(sl, from_=lo, to=hi, number_of_steps=hi - lo, variable=var, command=upd,
                          progress_color=GOOD).grid(row=2 * r + 1, column=0, columnspan=2, sticky="ew",
                                                    padx=12, pady=(0, 6 if r < len(items) - 1 else 14))
            upd()
            values[key] = (var, mul)

    # bakış bölgeleri (ikinci ekran)
    ctk.CTkLabel(body, text="Bakış bölgeleri (ikinci ekran)", font=font(14, "bold")).pack(anchor="w", pady=(4, 6))
    zc = card(body)
    zc.pack(fill="x", pady=(0, 12))
    ctk.CTkLabel(zc, text="Laptop gibi daha alçakta ya da yanda duran ekranlara bakarken başınızın eğik olması "
                          "normaldir. Her ekran kendi referansıyla değerlendirilir.",
                 font=font(12), text_color=MUTED, wraplength=460, justify="left").pack(anchor="w", padx=16, pady=(12, 6))
    zlist = ctk.CTkFrame(zc, fg_color="transparent")
    zlist.pack(fill="x", padx=16)

    def draw_zones():
        for w in zlist.winfo_children():
            w.destroy()
        zones = (app.cfg["baseline"] or {}).get("zones", [])
        if not zones:
            ctk.CTkLabel(zlist, text="Henüz ek ekran yok (sadece ana ekran).", font=font(13),
                         text_color=MUTED).pack(anchor="w")
        for z in zones:
            row = ctk.CTkFrame(zlist, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=f"•  {z['name']}  ({'otomatik öğrenildi' if z['source'] == 'auto' else 'el ile'})",
                         font=font(13)).pack(side="left")
            ctk.CTkButton(row, text="Sil", width=50, height=26, fg_color="transparent", border_width=1,
                          text_color=BAD, command=lambda n=z["name"]: (app.delete_zone(n), draw_zones())).pack(side="right")
    draw_zones()

    zrow = ctk.CTkFrame(zc, fg_color="transparent")
    zrow.pack(fill="x", padx=16, pady=(8, 4))
    zstatus = ctk.CTkLabel(zc, text="", font=font(12), wraplength=460, justify="left")
    zbar = ctk.CTkProgressBar(zc, progress_color=GOOD, height=6)

    def teach():
        teach_btn.configure(state="disabled")
        zstatus.pack(anchor="w", padx=16)
        zbar.pack(fill="x", padx=16, pady=(4, 8))
        app.request_capture("zone", 3)
        def poll():
            if not win.winfo_exists():
                return
            t, color, done, result = capture_state(app)
            zstatus.configure(text=t.replace("Pozisyonu alın", "Dik oturup diğer ekrana bakın"), text_color=color)
            zbar.set(capture_progress(app))
            if done:
                teach_btn.configure(state="normal")
                draw_zones()
                return
            win.after(200, poll)
        poll()

    teach_btn = ctk.CTkButton(zrow, text="Şu an baktığım ekranı öğret (3 sn)", command=teach,
                              state="normal" if app.cfg["baseline"] else "disabled")
    teach_btn.pack(side="left")
    zone_auto = ctk.BooleanVar(value=app.cfg["auto_zones"])
    ctk.CTkSwitch(zc, text="Sık baktığım ekranları otomatik öğren", variable=zone_auto, font=font(13),
                  progress_color=GOOD).pack(anchor="w", padx=16, pady=(6, 12))

    # kamera + başlangıç
    ctk.CTkLabel(body, text="Sistem", font=font(14, "bold")).pack(anchor="w", pady=(4, 6))
    sysc = card(body)
    sysc.pack(fill="x", pady=(0, 12))
    ctk.CTkLabel(sysc, text="Kamera", font=font(13)).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))
    cam_var = ctk.StringVar(value=str(app.cfg["camera"]))
    ctk.CTkSegmentedButton(sysc, values=["0", "1", "2", "3"], variable=cam_var).grid(
        row=0, column=1, sticky="e", padx=16, pady=(12, 6))
    auto_var = ctk.BooleanVar(value=app.cfg["autostart"])
    ctk.CTkSwitch(sysc, text="Windows açılınca başlat", font=font(13), variable=auto_var,
                  progress_color=GOOD).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(6, 12))
    sysc.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(body, text=f"Dik-Dur {app.VERSION} · veriler yalnızca bu bilgisayarda",
                 font=font(11), text_color=MUTED).pack(anchor="w", pady=(0, 8))

    def save():
        new = {k: round(v.get()) / mul for k, (v, mul) in values.items()}
        new["break_remind_min"] = int(new["break_remind_min"])
        new["camera"] = int(cam_var.get())
        new["autostart"] = bool(auto_var.get())
        new["auto_zones"] = bool(zone_auto.get())
        try:
            if new["autostart"] != app.cfg["autostart"]:
                app.set_autostart(new["autostart"])
            if new["camera"] != app.cfg["camera"]:
                app.reopen_cam = True
            app.cfg.update(new)          # Posture aynı dict'i kullanır: anında etkili
            save_config(app.cfg)
        except Exception as e:
            log.exception("ayarlar kaydedilemedi")
            return messagebox.showerror("Dik-Dur", f"Ayarlar kaydedilemedi: {e}", parent=win)
        win.destroy()

    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill="x", padx=16, pady=16)
    ctk.CTkButton(bar, text="Kalibre et…", fg_color="transparent", border_width=1,
                  text_color=("gray10", "gray90"), command=lambda: app.ui.put("calibrate")).pack(side="left")
    ctk.CTkButton(bar, text="Kaydet", width=110, command=save).pack(side="right")
    ctk.CTkButton(bar, text="İptal", width=90, fg_color="transparent", text_color=("gray10", "gray90"),
                  hover_color=("gray85", "gray25"), command=win.destroy).pack(side="right", padx=8)
    tick()
    return win


# --- Ana pencere ----------------------------------------------------------------
def home_window(root, app):
    win = window(root, f"Dik-Dur {app.VERSION}", "860x560")
    win.minsize(760, 520)
    ghost = dict(fg_color="transparent", text_color=TEXT, hover_color=("gray80", "gray25"), anchor="w",
                 height=38, font=font(14))

    nav = ctk.CTkFrame(win, width=220, corner_radius=0)
    nav.pack(side="left", fill="y")
    nav.pack_propagate(False)
    ctk.CTkLabel(nav, text="Dik-Dur", font=font(24, "bold")).pack(anchor="w", padx=20, pady=(22, 0))
    ctk.CTkLabel(nav, text="duruş asistanı", font=font(12), text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 18))

    def nav_btn(text, cmd, **kw):
        b = ctk.CTkButton(nav, text=text, command=cmd, **{**ghost, **kw})
        b.pack(fill="x", padx=10, pady=2)
        return b

    open_ = lambda name: app.ui.put(name)
    nav_btn("📊   Rapor", lambda: open_("report"))
    nav_btn("⚙   Ayarlar", lambda: open_("settings"))
    nav_btn("🧭   Kurulum sihirbazı", lambda: open_("wizard"))
    nav_btn("🎯   Hızlı kalibrasyon", lambda: open_("calibrate"))
    pause = nav_btn("", lambda: (app.toggle_pause(), refresh_pause()))

    def refresh_pause():
        pause.configure(text="▶   Devam et" if app.paused else "⏸   Duraklat")
    refresh_pause()

    def reset():
        if messagebox.askyesno("Baştan kur", "Kalibrasyon, tanımlı ekranlar ve tüm eşikler sıfırlanacak, kurulum "
                               "sihirbazı açılacak.\n\nGeçmiş duruş kayıtlarınız silinmez. Devam edilsin mi?",
                               parent=win):
            app.reset_setup()
            open_("wizard")

    ctk.CTkFrame(nav, fg_color="transparent").pack(fill="both", expand=True)
    nav_btn("↺   Baştan kur", reset, text_color=WARN)
    nav_btn("⏻   Çıkış", lambda: app.quit(), text_color=BAD)
    ctk.CTkLabel(nav, text="Pencereyi kapatınca uygulama\nsistem tepsisinde çalışmaya devam eder.",
                 font=font(11), text_color=MUTED, justify="left").pack(anchor="w", padx=20, pady=(8, 16))

    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(side="left", fill="both", expand=True, padx=24, pady=22)
    ctk.CTkLabel(body, text="Şu an", font=font(24, "bold")).pack(anchor="w")

    st = card(body)
    st.pack(fill="x", pady=(10, 12))
    dot = ctk.CTkLabel(st, text="●", font=font(44), text_color=MUTED)
    dot.grid(row=0, column=0, rowspan=3, padx=(20, 14), pady=14)
    head = ctk.CTkLabel(st, text="", font=font(20, "bold"), anchor="w")
    head.grid(row=0, column=1, sticky="w", pady=(16, 0))
    sub = ctk.CTkLabel(st, text="", font=font(13), text_color=MUTED, anchor="w", justify="left")
    sub.grid(row=1, column=1, sticky="w")
    fb = ctk.CTkFrame(st, fg_color="transparent")
    fb.grid(row=2, column=1, sticky="w", pady=(8, 16))
    small = dict(height=30, font=font(12), fg_color="transparent", border_width=1, text_color=TEXT)
    ctk.CTkButton(fb, text="Yanlış alarm — gevşet", command=app.feedback_false_alarm, **small).pack(side="left", padx=(0, 8))
    ctk.CTkButton(fb, text="Kötü oturuyorum — yakala", command=app.feedback_missed, **small).pack(side="left")

    ctk.CTkLabel(body, text="Bugün", font=font(18, "bold")).pack(anchor="w", pady=(4, 0))
    today_row = ctk.CTkFrame(body, fg_color="transparent")
    today_row.pack(fill="x", pady=(8, 0))
    tiles = []
    for i, title in enumerate(("Oturma", "Dik duruş", "En uzun oturma", "Bozulma")):
        today_row.grid_columnconfigure(i, weight=1, uniform="t")
        c = card(today_row)
        c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
        ctk.CTkLabel(c, text=title, font=font(12), text_color=MUTED).pack(anchor="w", padx=14, pady=(10, 0))
        v = ctk.CTkLabel(c, text="–", font=font(20, "bold"))
        v.pack(anchor="w", padx=14, pady=(0, 12))
        tiles.append(v)

    def stats():
        if not win.winfo_exists():
            return
        try:
            t = report(1).get(date.today())
        except Exception:
            log.exception("günlük özet okunamadı")
            t = None
        if t and t["sit"]:
            pct = 100 * t["upright"] / t["sit"]
            vals = (fmt_dur(t["sit"]), f"%{pct:.0f}", fmt_dur(t["longest"]), str(t["slouch"]))
            tiles[1].configure(text_color=pct_color(pct) or TEXT)
        else:
            vals = ("–",) * 4
        for lbl, v in zip(tiles, vals):
            lbl.configure(text=v)
        win.after(30000, stats)

    def tick():
        if not win.winfo_exists():
            return
        refresh_pause()
        color, text = app.STATUS.get(app.status or "absent", ("gray", ""))
        dot.configure(text_color=color if color != "gray" else MUTED)
        head.configure(text=text or "Başlatılıyor…")
        m, s, parts = app.last or (None, None, None)
        if m and parts:
            top = max(parts, key=parts.get)
            sub.configure(text=(
                f"Bakılan: {app.last_zone or 'diğer yön'}  ·  kesintisiz oturma "
                f"{fmt_dur(app.breaks.sitting_secs(time.monotonic()))}\n"
                f"Boyun {fmt_deg(m['neck'])} öne, {fmt_lat(m['neck_lat'])} yana  ·  "
                f"omuz {fmt_lat(m['shoulder_tilt'])}  ·  en yakın eşik: {PART_TR.get(top, top)} %{100 * parts[top]:.0f}"))
        elif not app.cfg["baseline"]:
            sub.configure(text="Kurulum yapılmamış. Sol menüden Kurulum sihirbazı'nı açın.")
        else:
            sub.configure(text="Yüz ve omuzlar kamerada görünmüyor.")
        win.after(500, tick)

    tick()
    stats()
    return win


# --- Kalibrasyon ---------------------------------------------------------------
def calibration_window(root, app):
    win = window(root, "Dik-Dur · Kalibrasyon", "480x370")
    win.resizable(False, False)
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(body, text="Kalibrasyon", font=font(24, "bold")).pack(anchor="w")
    ctk.CTkLabel(body, text=(
        "Uygulama duruşunuzu bu referansa göre değerlendirir. Başlatmadan önce:\n\n"
        "•  Kalçanızı sandalyenin arkasına yaslayın, belinizi dikleştirin\n"
        "•  Omuzlarınız gevşek ve aynı hizada olsun\n"
        "•  Başınızı dik tutup ekrana düz bakın\n"
        "•  Ölçüm sırasında 3 saniye kıpırdamayın"),
        font=font(13), justify="left", anchor="w").pack(anchor="w", pady=(10, 16))
    card_ = card(body)
    card_.pack(fill="x")
    status = ctk.CTkLabel(card_, text="Hazır olduğunuzda başlatın.", font=font(14, "bold"),
                          wraplength=400, justify="left")
    status.pack(anchor="w", padx=16, pady=(16, 8))
    bar_ = ctk.CTkProgressBar(card_, progress_color=GOOD, height=8)
    bar_.set(0)
    bar_.pack(fill="x", padx=16, pady=(0, 16))
    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill="x", padx=24, pady=(0, 20))
    start = ctk.CTkButton(bar, text="Başlat", width=120)
    start.pack(side="right")
    ctk.CTkButton(bar, text="Kapat", width=90, fg_color="transparent", text_color=("gray10", "gray90"),
                  hover_color=("gray85", "gray25"), command=win.destroy).pack(side="right", padx=8)

    def poll():
        if not win.winfo_exists():
            return
        text, color, done, result = capture_state(app)
        status.configure(text=text, text_color=color)
        bar_.set(capture_progress(app))
        if done:
            start.configure(state="normal", text="Tekrar dene" if not result[0] else "Yeniden ölç")
            if result[0]:
                win.after(2500, lambda: win.winfo_exists() and win.destroy())
            return
        win.after(200, poll)

    def begin():
        start.configure(state="disabled")
        app.recalibrate(3)
        poll()

    start.configure(command=begin)
    return win


# --- Rapor -------------------------------------------------------------------
def stat_row(parent, items):
    """items: (başlık, değer, alt metin, renk) listesi, eşit genişlikte kartlar."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(0, 12))
    for col, (title, value, sub, color) in enumerate(items):
        row.grid_columnconfigure(col, weight=1, uniform="c")
        c = card(row)
        c.grid(row=0, column=col, sticky="nsew", padx=6)
        ctk.CTkLabel(c, text=title, font=font(12), text_color=MUTED).pack(anchor="w", padx=14, pady=(12, 0))
        ctk.CTkLabel(c, text=value, font=font(22, "bold"), text_color=color).pack(anchor="w", padx=14)
        ctk.CTkLabel(c, text=sub, font=font(11), text_color=MUTED).pack(anchor="w", padx=14, pady=(0, 12))


def section(parent, title, subtitle=None):
    c = card(parent)
    c.pack(fill="x", padx=6, pady=(0, 12))
    ctk.CTkLabel(c, text=title, font=font(15, "bold")).pack(anchor="w", padx=16, pady=(12, 0 if subtitle else 6))
    if subtitle:
        ctk.CTkLabel(c, text=subtitle, font=font(11), text_color=MUTED).pack(anchor="w", padx=16, pady=(0, 6))
    return c


def causes_section(parent, causes):
    """Kötü duruş süresinin nedene göre dağılımı: yatay yığılı çubuk + açıklama."""
    total = sum(causes.values())
    c = section(parent, "Kötü duruşun nedeni", "bugünkü kötü duruş süresinin dağılımı")
    W = 860
    cv = tk.Canvas(c, width=W, height=18, bg=canvas_bg(), highlightthickness=0)
    cv.pack(anchor="w", padx=16)
    x = 0
    for key, _, color in CAUSES:
        w = causes[key] / total * W
        if w >= 1:
            cv.create_rectangle(x, 0, x + w, 18, fill=color, outline="")
        x += w
    lg = ctk.CTkFrame(c, fg_color="transparent")
    lg.pack(anchor="w", padx=16, pady=(6, 12))
    for key, label, color in CAUSES:
        if causes[key]:
            ctk.CTkLabel(lg, text=f"● {label} %{100 * causes[key] / total:.0f} ({fmt_dur(causes[key])})",
                         text_color=color, font=font(12)).pack(side="left", padx=(0, 14))


def bar_chart(parent, hours):
    """Saatlik oturma (gri) ve dik (yeşil) dakikalar. 07–23 arası gösterilir."""
    dark = ctk.get_appearance_mode() == "Dark"
    W, H, pad = 860, 190, 28
    cv = tk.Canvas(parent, width=W, height=H, bg=canvas_bg(), highlightthickness=0)
    shown = range(7, 24)
    bw = (W - 2 * pad) / len(shown)
    base = H - 26
    for g in (0, 30, 60):                      # kılavuz çizgiler (dk)
        y = base - g / 60 * (base - 16)
        cv.create_line(pad, y, W - pad, y, fill="#444" if dark else "#c4c4c4", dash=(2, 4))
        cv.create_text(pad - 6, y, text=str(g), anchor="e", fill=MUTED, font=(FONT, 8))
    for i, hr in enumerate(shown):
        sit, up = hours[hr]
        x0 = pad + i * bw + bw * 0.18
        x1 = pad + (i + 1) * bw - bw * 0.18
        for val, color in ((sit, "#555" if dark else "#b5b5b5"), (up, GOOD)):
            h = min(val, 3600) / 3600 * (base - 16)
            if h > 0:
                cv.create_rectangle(x0, base - h, x1, base, fill=color, outline="")
        cv.create_text((x0 + x1) / 2, base + 12, text=f"{hr:02d}", fill=MUTED, font=(FONT, 9))
    return cv


def pct_color(p):
    return None if p is None else GOOD if p >= 70 else BAD if p < 40 else None


def lat_text(d):
    tot = d["lat_left"] + d["lat_right"]
    if not tot:
        return "yön belirsiz"
    side, v = ("sol", d["lat_left"]) if d["lat_left"] >= d["lat_right"] else ("sağ", d["lat_right"])
    return f"çoğunlukla {side} (%{100 * v / tot:.0f})"


def report_window(root, app):
    win = window(root, "Dik-Dur · Duruş Raporu", "960x820")
    body = ctk.CTkScrollableFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=16)

    def refresh():
        for w in body.winfo_children():
            w.destroy()
        days = report(7, yaw_deg=app.cfg["yaw_deg"])
        today = date.today()
        t = days.get(today)

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x", padx=6)
        ctk.CTkLabel(head, text="Duruş Raporu", font=font(26, "bold")).pack(side="left")
        ctk.CTkButton(head, text="↻  Yenile", width=100, command=refresh).pack(side="right")
        ctk.CTkLabel(body, text=f"Bugün · {today.strftime('%d.%m.%Y')} {DAYS_TR[today.weekday()]}",
                     font=font(13), text_color=MUTED).pack(anchor="w", padx=6, pady=(0, 12))

        if t and t["sit"]:
            pct = 100 * t["upright"] / t["sit"]
            stat_row(body, [
                ("Oturma süresi", fmt_dur(t["sit"]), "kamera önünde", None),
                ("Dik duruş", f"%{pct:.0f}", fmt_dur(t["upright"]), pct_color(pct)),
                ("Pozisyon bozulması", str(t["slouch"]), f"{t['dims']} kez ışık düşürüldü", None),
                ("Ort. öne eğilme", f"{fmt_deg(t['neck'])} / {fmt_deg(t['trunk'])}", "boyun / bel", None),
            ])
            longest = t["longest"]
            stat_row(body, [
                ("En uzun kesintisiz oturma", fmt_dur(longest), f"{t['breaks']} mola verildi",
                 BAD if longest >= 3600 else WARN if longest >= 3000 else None),
                ("Yana yatma", fmt_dur(t["causes"]["lateral"]),
                 f"{lat_text(t)} · ort. {fmt_deg(t['lat'])}", None),
                ("Ekrana fazla yakın", fmt_dur(t["causes"]["near"]), "göz yorgunluğu riski", None),
                ("Baş yana dönük", fmt_dur(t["yaw"]), f"{app.cfg['yaw_deg']:.0f}°+ boyun dönüşü", None),
            ])
            if sum(t["causes"].values()):
                causes_section(body, t["causes"])
        else:
            stat_row(body, [("Bugün", "Kayıt yok", "kamera önünde oturunca dolacak", None)])

        if t and t["zones"]:
            tot = sum(t["zones"].values())
            zs = section(body, "Bakış dağılımı", "bugün hangi ekrana ne kadar baktınız")
            ctk.CTkLabel(zs, text="   ·   ".join(f"{n} %{100 * v / tot:.0f} ({fmt_dur(v)})"
                                               for n, v in sorted(t["zones"].items(), key=lambda kv: -kv[1])),
                         font=font(13), wraplength=840, justify="left").pack(anchor="w", padx=16, pady=(0, 14))

        tips = section(body, "Öneriler", "son 7 güne göre · genel ergonomi bilgisidir, tıbbi tavsiye değildir")
        tip_list = advice(days)
        for i, tip in enumerate(tip_list):
            ctk.CTkLabel(tips, text=f"•  {tip}", font=font(13), wraplength=840, justify="left").pack(
                anchor="w", padx=16, pady=(0, 14 if i == len(tip_list) - 1 else 6))

        ch = section(body, "Saatlik dağılım")
        lg = ctk.CTkFrame(ch, fg_color="transparent")
        lg.pack(anchor="w", padx=16)
        ctk.CTkLabel(lg, text="● dik", text_color=GOOD, font=font(12)).pack(side="left")
        ctk.CTkLabel(lg, text="   ● oturma (dk / saat)", text_color=MUTED, font=font(12)).pack(side="left")
        bar_chart(ch, t["hours"] if t else [[0, 0]] * 24).pack(padx=8, pady=(0, 12))

        wk = section(body, "Son 7 gün")
        grid = ctk.CTkFrame(wk, fg_color="transparent")
        grid.pack(fill="x", padx=8, pady=(0, 12))
        heads = ["Gün", "Oturma", "Dik %", "Bozulma", "Yana yatma", "En uzun", "Mola", "Boyun", "Bel"]
        for c, h in enumerate(heads):
            grid.grid_columnconfigure(c, weight=1)
            ctk.CTkLabel(grid, text=h, font=font(12, "bold"), text_color=MUTED).grid(row=0, column=c, pady=(0, 4))
        for r in range(7):
            d = today - timedelta(days=r)
            v = days.get(d)
            pct = 100 * v["upright"] / v["sit"] if v and v["sit"] else None
            row = [f"{d.strftime('%d.%m')} {DAYS_TR[d.weekday()]}"] + (["–"] * 8 if not v else [
                fmt_dur(v["sit"]), f"%{pct:.0f}" if pct is not None else "–", str(v["slouch"]),
                fmt_dur(v["causes"]["lateral"]), fmt_dur(v["longest"]), str(v["breaks"]),
                fmt_deg(v["neck"]), fmt_deg(v["trunk"])])
            for c, txt in enumerate(row):
                ctk.CTkLabel(grid, text=txt, font=font(13), text_color=pct_color(pct) if c == 2 else None).grid(
                    row=r + 1, column=c, pady=3)

    refresh()
    return win


def mainloop(app):
    root = ctk.CTk()
    root.withdraw()
    root.report_callback_exception = lambda *exc: log.error("arayüz hatası", exc_info=exc)
    open_ = {}

    def show(name, factory):
        w = open_.get(name)
        if w is not None and w.winfo_exists():
            w.deiconify(); w.lift(); w.focus_force()
        else:
            open_[name] = factory(root, app)

    def poll():
        while not app.ui.empty():
            cmd = app.ui.get()
            if cmd == "quit":
                return root.destroy()
            from wizard import wizard_window
            show(cmd, {"settings": settings_window, "report": report_window,
                       "calibrate": calibration_window, "wizard": wizard_window,
                       "home": home_window}[cmd])
        root.after(200, poll)

    poll()
    root.mainloop()
