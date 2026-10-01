"""Yerel kalıcılık: ayarlar (JSON), duruş kayıtları (SQLite) ve rapor özeti."""
import json, logging, os, sqlite3, statistics, time
from datetime import datetime
from pathlib import Path

from posture import BREAK_SECS, lateral_side

log = logging.getLogger("dikdur")

DATA = Path(os.environ.get("APPDATA") or Path.home()) / "dik-dur"
CONFIG, STATE, DB, LOG = DATA / "config.json", DATA / "state.json", DATA / "dikdur.db", DATA / "dikdur.log"

DEFAULTS = {
    "camera": 0,
    "neck_deg": 15.0,        # boyun açısı bazdan bu kadar artarsa kötü
    "trunk_deg": 15.0,       # gövde (bel) açısı bazdan bu kadar artarsa kötü (kalçalar görünürse)
    "combined_deg": 20.0,    # boyun + bel sapmalarının toplamı bunu aşarsa kötü
    "sink_tol": 0.2,         # omuzlar omuz genişliğinin bu kadarı aşağı kayarsa: bel çökmüş
    "lateral_deg": 10.0,     # gövde/boyun yana yatma sapması
    "head_tilt_deg": 15.0,   # başı yana yatırma sapması
    "tilt_deg": 10.0,        # omuz asimetrisi (omuz hattı eğimi) sapması
    "yaw_deg": 30.0,         # baş gövdeye göre bundan fazla dönükse "yana bakıyor" (sadece kayıt)
    "yaw_gate": 12.0,        # baş bundan fazla dönükken baş ölçüleri karara katılmaz (sahte sapma)
    "break_remind_min": 50,  # kesintisiz oturmada mola hatırlatması (0 = kapalı)
    "tolerance": 0.15,       # ekrana yaklaşma göreli sapması (sadece önden kamera)
    "drop_tol": 0.25,        # baş düşüklüğü (kulakların omuzlara göre inmesi) göreli sapması
    "nod_deg": 45.0,         # bakılan ekrana göre başı bundan fazla aşağı eğmek kötü (telefon vb.)
    "dim_ratio": 0.5,        # parlaklığı bu oranda düşür
    "bad_secs": 3.0,         # kötü duruş bu kadar sürerse dimle
    "good_secs": 1.0,        # iyi duruş bu kadar sürerse geri al
    "absent_secs": 30.0,     # kişi yoksa bu süre sonra geri al
    "restore_at": 0.7,       # histerezis: skor bunun altına inince "iyi"
    "retention_days": 180,   # bundan eski kayıtlar silinir
    "autostart": False,
    "auto_zones": True,      # sık bakılan ekranları (bakış bölgeleri) otomatik öğren
    "baseline": None,        # kalibrasyonla dolar
}

# elle düzenlenmiş/bozuk config'e karşı geçerli aralıklar
LIMITS = {
    "camera": (0, 9), "neck_deg": (1, 90), "trunk_deg": (1, 90), "combined_deg": (1, 180), "tilt_deg": (1, 90),
    "lateral_deg": (1, 90), "sink_tol": (0.05, 1), "head_tilt_deg": (1, 90), "yaw_deg": (5, 90), "yaw_gate": (3, 60), "break_remind_min": (0, 240),
    "tolerance": (0.01, 1), "drop_tol": (0.05, 1), "nod_deg": (10, 90), "dim_ratio": (0.05, 0.95), "bad_secs": (0, 600), "good_secs": (0, 600),
    "absent_secs": (1, 3600), "restore_at": (0.1, 1), "retention_days": (1, 3650),
}


def atomic_write(path, text):
    """Yarım yazılmış dosya bırakmaz: önce geçici dosya, sonra atomik yer değiştirme."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_config():
    DATA.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    raw = {}
    if CONFIG.exists():
        try:
            raw = json.loads(CONFIG.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("kök JSON nesne değil")
        except (OSError, ValueError) as e:
            log.warning("config okunamadı, varsayılanlar kullanılıyor: %s", e)
            os.replace(CONFIG, CONFIG.with_name("config.bad.json"))
            raw = {}
    for k, v in raw.items():
        if k not in DEFAULTS:
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                log.warning("config %s=%r geçersiz, varsayılan kullanılıyor", k, v)
                continue
        cfg[k] = v
    cfg["camera"] = int(cfg["camera"])
    cfg["autostart"] = cfg["autostart"] is True
    cfg["auto_zones"] = cfg["auto_zones"] is not False
    b = cfg["baseline"]
    if b is not None and not (isinstance(b, dict) and isinstance(b.get("neck"), (int, float))
                              and "pitch" in b):
        cfg["baseline"] = None                 # eski sürüm (yanal ölçü yok) / bozuk: yeniden kalibre
    return cfg


def save_config(cfg):
    atomic_write(CONFIG, json.dumps(cfg, indent=2))


# --- SQLite ------------------------------------------------------------------
SCHEMA_VERSION = 5
COLUMNS = {   # ad -> tip; yeni kolonlar eski veritabanına otomatik eklenir
    "ts": "REAL", "neck": "REAL", "trunk": "REAL", "score": "REAL", "state": "TEXT",
    "dimmed": "INTEGER", "side": "INTEGER", "cause": "TEXT",                       # v1-v2
    "neck_lat": "REAL", "trunk_lat": "REAL", "head_tilt": "REAL",                 # v3: yanal
    "shoulder_tilt": "REAL", "head_yaw": "REAL", "closer": "REAL",
    "sink": "REAL",                                                               # v4
    "zone": "TEXT",                                                               # v5: bakılan ekran
}


def db_connect(path=DB):
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS samples(ts REAL)")
    have = {r[1] for r in con.execute("PRAGMA table_info(samples)")}
    for col, typ in COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE samples ADD COLUMN {col} {typ}")
    con.execute("CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts)")
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.commit()
    return con


def insert_sample(con, row):
    cols = [c for c in COLUMNS if c in row]
    con.execute(f"INSERT INTO samples({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [row[c] for c in cols])


def prune(con, days):
    n = con.execute("DELETE FROM samples WHERE ts < ?", (time.time() - days * 86400,)).rowcount
    con.commit()
    if n:
        log.info("%d eski kayıt silindi (> %d gün)", n, days)


GAP = 10.0   # ölçümler arası bu kadardan uzun boşluk = o arada oturmuyordu
CAUSE_KEYS = ("neck", "trunk", "both", "lateral", "near", "other")


def _new_day():
    return {"sit": 0.0, "upright": 0.0, "slouch": 0, "dims": 0,
            "neck": [], "trunk": [], "lat": [],
            "hours": [[0.0, 0.0] for _ in range(24)],
            "causes": dict.fromkeys(CAUSE_KEYS, 0.0),
            "lat_left": 0.0, "lat_right": 0.0, "yaw": 0.0,
            "longest": 0.0, "breaks": 0, "zones": {}}


def summarize(rows, yaw_deg=30.0):
    """rows: ts sıralı satırlar (dict / sqlite3.Row). Gün bazında özet döner.

    sit: kamera önünde geçen süre (sn), upright: bunun 'good' olan kısmı,
    slouch: iyi->kötü geçiş sayısı, dims: parlaklık düşürme sayısı,
    hours: saat başına [oturma, dik] sn, causes: kötü duruş süresinin nedene göre dağılımı,
    lat_left/lat_right: yana yatma süresinin yönü, yaw: başın yana dönük kaldığı süre,
    longest: en uzun kesintisiz oturma, breaks: mola sayısı (>= BREAK_SECS ara),
    neck/trunk: ort. öne eğilme, lat: ort. mutlak yana yatma (derece).
    """
    days = {}
    sess_start = None
    for i, r in enumerate(rows):
        ts = r["ts"]
        when = datetime.fromtimestamp(ts)
        d = days.setdefault(when.date(), _new_day())
        prev = rows[i - 1] if i else None
        nxt = rows[i + 1]["ts"] if i + 1 < len(rows) else ts + 1
        dt = min(nxt - ts, GAP)

        # oturum / mola
        if prev is None or ts - prev["ts"] >= BREAK_SECS:
            if prev is not None:
                pd = days[datetime.fromtimestamp(sess_start).date()]
                pd["longest"] = max(pd["longest"], prev["ts"] - sess_start)
                if datetime.fromtimestamp(prev["ts"]).date() == when.date():
                    d["breaks"] += 1
            sess_start = ts

        hr = d["hours"][when.hour]
        d["sit"] += dt; hr[0] += dt
        if r["state"] == "good":
            d["upright"] += dt; hr[1] += dt
        elif r["state"] == "bad":
            c = r["cause"] if r["cause"] in d["causes"] else "other"
            d["causes"][c] += dt
            if c == "lateral":
                side = lateral_side(r)
                if side:
                    d["lat_" + side] += dt
        if r["zone"]:
            d["zones"][r["zone"]] = d["zones"].get(r["zone"], 0.0) + dt
        if r["head_yaw"] is not None and abs(r["head_yaw"]) > yaw_deg:
            d["yaw"] += dt
        if prev is not None and ts - prev["ts"] <= GAP:
            d["slouch"] += prev["state"] == "good" and r["state"] == "bad"
            d["dims"] += not prev["dimmed"] and bool(r["dimmed"])
        d["neck"].append(r["neck"])
        if r["trunk"] is not None:
            d["trunk"].append(r["trunk"])
        lat = [abs(r[k]) for k in ("trunk_lat", "neck_lat") if r[k] is not None]
        if lat:
            d["lat"].append(max(lat))
    if rows:
        pd = days[datetime.fromtimestamp(sess_start).date()]
        pd["longest"] = max(pd["longest"], rows[-1]["ts"] + 1 - sess_start)
    for d in days.values():
        for k in ("neck", "trunk", "lat"):
            d[k] = statistics.fmean(d[k]) if d[k] else None
    return days


def db_read(path=DB):
    """Salt okunur bağlantı: şema yazmaz, döngünün açık yazma işlemiyle kilitlenmez (WAL)."""
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def report(days_back=7, path=DB, yaw_deg=30.0):
    # ponytail: 7 günlük ham satırı Python'da özetliyor (~200k satır, ~1 sn);
    # yavaşlarsa günlük özet tablosuna geç
    if not Path(path).exists():
        return {}
    con = db_read(path)
    try:
        rows = con.execute("SELECT * FROM samples WHERE ts >= ? ORDER BY ts",
                           (time.time() - days_back * 86400,)).fetchall()
    finally:
        con.close()
    return summarize(rows, yaw_deg)


def advice(days):
    """Son günlerin özetinden genel ergonomi önerileri (tıbbi tavsiye değildir)."""
    sit = sum(d["sit"] for d in days.values())
    if sit < 600:
        return ["Öneri için en az 10 dakikalık kayıt gerekiyor."]
    bad = {k: sum(d["causes"][k] for d in days.values()) for k in CAUSE_KEYS}
    bad_total = sum(bad.values()) or 1
    share = lambda k: bad[k] / bad_total
    upright = sum(d["upright"] for d in days.values()) / sit
    longest = max(d["longest"] for d in days.values())
    left = sum(d["lat_left"] for d in days.values())
    right = sum(d["lat_right"] for d in days.values())
    yaw = sum(d["yaw"] for d in days.values()) / sit
    tips = []
    if longest >= 3600:
        tips.append(f"En uzun kesintisiz oturmanız {int(longest // 60)} dk. Her 30–50 dakikada kalkıp "
                    "2–3 dakika yürüyün; uzun süre hareketsiz oturmak bel ve dolaşım için risklidir.")
    if share("neck") >= 0.3:
        tips.append("Boyun öne eğilmesi baskın: monitörün üst kenarını göz hizasına getirin, "
                    "çenenizi hafifçe geri çekin. Dizüstü kullanıyorsanız yükseltici + harici klavye işe yarar.")
    if share("trunk") >= 0.3:
        tips.append("Bel öne kayıyor: sırtınızı tamamen sandalyeye yaslayın, bel desteği kullanın, "
                    "ayaklarınız yere düz bassın ve dizler kalçayla aynı hizada olsun.")
    if share("both") >= 0.2:
        tips.append("Boyun ve bel birlikte eğiliyor (ekrana doğru çökme): ekranı biraz yaklaştırın veya "
                    "yazı boyutunu büyütün ki öne uzanmanız gerekmesin.")
    if share("lateral") >= 0.2 and left + right:
        side, pct = ("sol", left) if left >= right else ("sağ", right)
        tips.append(f"Yana yatma sık ve çoğunlukla {side} tarafa (%{100 * pct / (left + right):.0f}): "
                    "kolçak yüksekliğini, fare/klavye konumunu ve ağırlığı tek kalçaya verip vermediğinizi kontrol edin.")
    if share("near") >= 0.15:
        tips.append("Ekrana fazla yaklaşıyorsunuz: ekran 50–70 cm uzakta olmalı. Yaklaşma göz yorgunluğu "
                    "işareti olabilir; 20-20-20 kuralı: 20 dakikada bir, 20 saniye, 6 metre uzağa bakın.")
    if yaw >= 0.15:
        tips.append(f"Oturma sürenizin %{100 * yaw:.0f}'ında başınız yana dönük: en çok baktığınız monitörü tam karşınıza alın.")
    if not tips:
        tips.append("Harika gidiyorsunuz!" if upright >= 0.8 else
                    "Belirgin tek bir sorun yok; dik duruş oranınızı artırmak için eşikleri biraz sıkılaştırabilirsiniz.")
    return tips
