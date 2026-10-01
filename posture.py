"""Duruş ölçümü ve karar mantığı. Saf Python: kamera, model, disk veya UI bağımlılığı yok.

Öne eğilme  : MediaPipe 3B dünya koordinatlarından, çizginin dikeyle toplam açısı.
Yana yatma  : 2B görüntü düzleminden. Tek kamerayla derinlik tahmini zayıf; 3B'den yanal
              açı çıkarmak öne eğilmeyi yana yatma gibi gösteriyordu (kamera tam karşıda
              değilken 20°+ hata). Sola-sağa hareket ise görüntüde doğrudan görünür.
Pozitif yanal açı = kişinin SOLUNA doğru (kameraya dönük kişinin solu görüntünün sağıdır).
"""
import math

BREAK_SECS = 120.0   # bu kadar kamerada görünmemek = mola


def _mid(a, b):
    return ((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)


def angle_from_vertical(lo, hi):
    """lo->hi vektörünün dikeyle toplam açısı (derece). MediaPipe dünya koordinatında y aşağı."""
    v = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    n = math.sqrt(sum(c * c for c in v)) or 1e-9
    return math.degrees(math.acos(max(-1.0, min(1.0, -v[1] / n))))


def metrics(img, world, w, h):
    """img: 2B landmark'lar (0-1), world: 3B (metre, kalça merkezli). Görünmüyorsa None.

    neck / trunk        : boyun / bel öne eğilme (3B, kalçalar görünürse bel)
    neck_lat / trunk_lat: boyun / gövde yana yatma (2B)
    head_tilt           : kulak hattı eğimi, başı yana yatırma (2B)
    shoulder_tilt       : omuz hattı eğimi, omuz asimetrisi (2B)
    head_yaw            : başın kameraya göre sağa/sola dönüklüğü (2B, ham; + = sola)
    pitch               : burnun kulak hizasına göre yüksekliği / omuz genişliği (2B). Aşağı
                          bakınca azalır: hangi ekrana (ör. daha alçaktaki laptop) bakıldığını ayırır
    drop / sw           : baş düşmesi / ekrana yakınlık için 2B ölçüler
    sh_y                : omuz hattının görüntüdeki yüksekliği (genişliğe oranlı). Kalçalar
                          görünmese de sandalyede çökmeyi/kaymayı (bel dik değil) yakalar
    Kamera yandaysa 2B ölçüler anlamsız: None.
    """
    v = lambda i: img[i].visibility
    if v(0) < 0.5 or max(v(11), v(12)) < 0.5 or max(v(7), v(8)) < 0.3:
        return None
    ws = world
    sh3 = _mid(ws[11], ws[12])
    hips = min(v(23), v(24)) > 0.5
    side = abs(ws[11].z - ws[12].z) > abs(ws[11].x - ws[12].x)   # omuzlar derinlik ekseninde
    m = {"neck": angle_from_vertical(sh3, _mid(ws[7], ws[8])),
         "trunk": angle_from_vertical(_mid(ws[23], ws[24]), sh3) if hips else None,
         "neck_lat": None, "trunk_lat": None, "head_tilt": None, "shoulder_tilt": None,
         "head_yaw": None, "pitch": None, "side": side, "drop": None, "sw": None, "sh_y": None}
    if side:
        return m
    p = lambda i: (img[i].x * w, img[i].y * h)                       # piksel
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    lean = lambda lo, hi: math.degrees(math.atan2(hi[0] - lo[0], lo[1] - hi[1]))   # dikeyden, + = sol
    line = lambda r, l: math.degrees(math.atan2(l[1] - r[1], l[0] - r[0]))         # yataydan, + = sol aşağı
    ls, rs, le, re, nose = p(11), p(12), p(7), p(8), p(0)
    sh, ear = mid(ls, rs), mid(le, re)
    sw = math.hypot(ls[0] - rs[0], ls[1] - rs[1])
    if sw < 1 or ls[0] <= rs[0]:                     # omuzlar ters: kişi kameraya dönük değil
        return m
    m["neck_lat"] = lean(sh, ear)
    if hips:
        m["trunk_lat"] = lean(mid(p(23), p(24)), sh)
    m["head_tilt"] = line(re, le)
    m["shoulder_tilt"] = line(rs, ls)
    # baş döndükçe kulaklar arası mesafe küçülür ve oran 90°'ye doyar; önden bakıştaki baş
    # yarıçapı ~ omuz genişliğinin %20'si: payda bunun altına inmesin ki büyük dönüşler ayırt edilsin
    half = max(abs(le[0] - re[0]) / 2, 0.2 * sw)
    m["head_yaw"] = math.degrees(math.asin(max(-1.0, min(1.0, (nose[0] - ear[0]) / half))))
    m["pitch"] = (ear[1] - nose[1]) / sw            # küçük = aşağı bakıyor
    m["drop"] = (sh[1] - nose[1]) / sw              # büyük = dik
    m["sw"] = sw / w
    m["sh_y"] = sh[1] / w
    return m


def _has(m, base, k):
    return m.get(k) is not None and base.get(k) is not None


HEAD_KEYS = ("neck", "neck_lat", "head_tilt", "drop")   # baş yönüne göre değişen ölçüler
NOD_DEG = 240   # pitch değişimi -> baş eğme açısı (°); 0.125 oran ~ 30° baş eğme
NOD_MAX = 30    # baş eğmeye verilen en büyük boyun payı (°)
PITCH_SCALE = 100   # pitch oranını yaw derecesiyle karşılaştırılabilir yapar (0.1 oran ~ 10°)


def zones_of(base):
    """Ana kalibrasyon + öğrenilmiş bakış bölgeleri: [(ad, baş referansı)]."""
    return [("Ana ekran", base)] + [(z["name"], z) for z in base.get("zones", [])]


def zone_distance(m, z):
    d = abs(m["head_yaw"] - z["head_yaw"])
    if m.get("pitch") is not None and z.get("pitch") is not None:
        d += abs(m["pitch"] - z["pitch"]) * PITCH_SCALE
    return d


def yaw_tol(z, cfg):
    """Bölgenin sağ-sol toleransı. Kameraya göre çok dönük bakışta açı ölçümü daha oynak:
    tolerans dönüşle birlikte genişler (90°'de iki katı)."""
    return cfg["yaw_gate"] * (1 + abs(z["head_yaw"]) / 90)


def match_zone(m, base, cfg):
    """(bölge adı, baş referansı) ya da baş hiçbir bölgeye dönük değilse (None, None).

    Baş yana dönükken bir kulak arkada kalır, kulak/burun konumları kayar: baştan ölçülen
    değerler sahte sapma gösterir (ölçüldü: 35°+ dönüşte dik otururken %87 "kötü").
    Bölge seçimi sağ-sol + yukarı-aşağı bakışa göre en yakın olan; sağ-sol farkı yaw_gate'i
    aşan bölgeler aday değildir. Bölge dışı aşağı bakış (ör. telefona) en yakın bölgeye göre
    değerlendirilir, yani yakalanır.
    """
    if m.get("head_yaw") is None or base.get("head_yaw") is None:
        return "Ana ekran", base
    cands = [(n, z) for n, z in zones_of(base)
             if z.get("head_yaw") is not None and abs(m["head_yaw"] - z["head_yaw"]) <= yaw_tol(z, cfg)]
    if not cands:
        return None, None
    return min(cands, key=lambda nz: zone_distance(m, nz[1]))


def score_parts(m, base, cfg):
    """Her sapma kendi eşiğine bölünmüş (>1 = o ölçüt tek başına kötü).

    neck/trunk: öne eğilme. combined: boyun + bel toplamı — ikisi de tek başına eşiği
    aşmasa da birlikte kötü duruşu yakalar (ör. 10° + 10°).
    lateral: gövde/boyun yana yatma, head_tilt: baş yana eğik, shoulder: omuz asimetrisi,
    drop: baş aşağı düşmüş, near: ekrana fazla yaklaşmış, sink: sandalyede çökme.
    Baş ölçüleri bakılan bölgenin (ekranın) referansına, gövde ölçüleri ana kalibrasyona göre.
    """
    _, hb = match_zone(m, base, cfg)
    head_ok = hb is not None
    p = {}
    # Ekranın altına bakmak için başı eğmek (burun kulaklara göre iner) kötü duruş değildir;
    # boynu öne uzatmak (kulaklar omuzlara göre iner) kötüdür. Başın eğilme miktarı kadar boyun
    # eşiğine pay verilir (en fazla NOD_MAX°), baş düşüklüğü ise kulak yüksekliğinden ölçülür.
    nod = 0.0
    if head_ok and _has(m, hb, "pitch"):
        raw = max(0.0, (hb["pitch"] - m["pitch"]) * NOD_DEG)
        nod = min(NOD_MAX, raw)
        p["nod"] = raw / cfg["nod_deg"]      # telefona bakar gibi çok eğik baş ("text neck")
    d_neck = max(0.0, m["neck"] - hb["neck"] - nod) if head_ok else 0.0
    if head_ok:
        p["neck"] = d_neck / cfg["neck_deg"]
    if _has(m, base, "trunk"):
        d_trunk = max(0.0, m["trunk"] - base["trunk"])
        p["trunk"] = d_trunk / cfg["trunk_deg"]
        p["combined"] = (d_neck + d_trunk) / cfg["combined_deg"]
    lat = [abs(m["trunk_lat"] - base["trunk_lat"])] if _has(m, base, "trunk_lat") else []
    if head_ok and _has(m, hb, "neck_lat"):
        lat.append(abs(m["neck_lat"] - hb["neck_lat"]))
    if lat:
        p["lateral"] = max(lat) / cfg["lateral_deg"]
    if head_ok and _has(m, hb, "head_tilt"):
        p["head_tilt"] = abs(m["head_tilt"] - hb["head_tilt"]) / cfg["head_tilt_deg"]
    if _has(m, base, "shoulder_tilt"):
        p["shoulder"] = abs(m["shoulder_tilt"] - base["shoulder_tilt"]) / cfg["tilt_deg"]
    if _has(m, base, "drop") and base["drop"] and base.get("sw"):
        if head_ok and hb.get("drop"):
            if _has(m, hb, "pitch"):         # kulakların omuzlardan yüksekliği = drop - pitch
                ref, cur = hb["drop"] - hb["pitch"], m["drop"] - m["pitch"]
            else:
                ref, cur = hb["drop"], m["drop"]
            if ref > 0:
                p["drop"] = max(0.0, (ref - cur) / ref) / cfg["drop_tol"]
        p["near"] = max(0.0, (m["sw"] - base["sw"]) / base["sw"]) / cfg["tolerance"]
    if _has(m, base, "sh_y") and base.get("sw"):
        # omuzlar kalibrasyona göre omuz genişliğinin şu kadarı aşağı kaydı: sandalyede çökme.
        # Geriye yaslanmak (sırt dik) omuzları pek indirmez; cezalandırılmaz.
        p["sink"] = max(0.0, (m["sh_y"] - base["sh_y"]) / base["sw"]) / cfg["sink_tol"]
    return p or {"neck": 0.0}


BODY_PARTS = ("trunk", "sink", "shoulder", "near")


def body_ok(parts, cfg):
    """Gövde (bel, omuz, ekrana uzaklık) iyi mi — bakış bölgesi öğrenmenin ön şartı."""
    return all(parts.get(k, 0) < cfg["restore_at"] for k in BODY_PARTS)


def score(m, base, cfg):
    """>1 kötü duruş: ölçütlerin en kötüsü."""
    return max(score_parts(m, base, cfg).values())


# ölçüt -> rapor kategorisi
CATEGORY = {"neck": "neck", "drop": "neck", "nod": "neck", "trunk": "trunk", "combined": "both",
            "lateral": "lateral", "head_tilt": "lateral", "shoulder": "lateral", "near": "near",
            "sink": "trunk"}


def cause(parts):
    """Kötü duruşun nedeni: neck | trunk | both | lateral | near | None (kötü değilse)."""
    if parts.get("neck", 0) > 1 and max(parts.get("trunk", 0), parts.get("sink", 0)) > 1:
        return "both"
    worst = max(parts, key=parts.get)
    return CATEGORY.get(worst, "other") if parts[worst] > 1 else None


def lateral_side(m):
    """Yana yatmanın yönü: 'left' | 'right' | None. En büyük yanal sapmanın yönü."""
    vals = [m[k] for k in ("trunk_lat", "neck_lat", "shoulder_tilt") if m[k] is not None]
    if not vals:
        return None
    v = max(vals, key=abs)
    return "left" if v > 0 else "right"


CALIB_KEYS = ("neck", "trunk", "neck_lat", "trunk_lat", "head_tilt", "shoulder_tilt", "head_yaw", "pitch",
              "drop", "sw", "sh_y")
# dik duruşta vücut ekseninde yanal açılar ~0 olmalı; bunu aşan "dik" kabul edilmez
CALIB_MAX_LATERAL = {"neck_lat": 8, "trunk_lat": 8, "head_tilt": 10, "shoulder_tilt": 8}
CALIB_MAX_SPREAD = 6.0   # örnekler arası boyun açısı oynaklığı (°): fazlası = kişi hareket ediyor
TURNED = 25.0            # baş kameraya göre bundan fazla dönükse (ör. kamera laptopta, ekran yanda)
                         # bir kulak arkada kalır: baştan ölçülen yanal değerler yanıltıcıdır
HEAD_LATERAL = ("neck_lat", "head_tilt")
LAT_TR = {"neck_lat": "boyun yana", "trunk_lat": "gövde yana", "head_tilt": "baş yana eğik",
          "shoulder_tilt": "omuzlar eğik"}


def calibrate(samples):
    """Ölçümlerden referans duruş. (base, None) ya da (None, kullanıcıya hata mesajı)."""
    import statistics
    if len(samples) < 4:
        return None, "yüz ve omuzlar görünmüyor"
    necks = [s["neck"] for s in samples]
    if max(necks) - min(necks) > CALIB_MAX_SPREAD:
        return None, "hareket ettiniz, kalibrasyon sırasında sabit durun"
    base = {}
    for k in CALIB_KEYS:
        vals = [s[k] for s in samples if s.get(k) is not None]
        base[k] = statistics.median(vals) if len(vals) >= len(samples) // 2 else None
    turned = base.get("head_yaw") is not None and abs(base["head_yaw"]) > TURNED
    for k, lim in CALIB_MAX_LATERAL.items():
        if turned and k in HEAD_LATERAL:
            continue                # baş çok dönük: bu açıdaki değer olduğu gibi referans olur
        if base[k] is not None and abs(base[k]) > lim:
            return None, (f"yana eğik görünüyorsunuz ({LAT_TR[k]} {abs(base[k]):.0f}°). "
                          "Omuzlarınızı düzleştirip dik oturun ve tekrar ölçün")
    return base, None


class Posture:
    """Gecikmeli + histerezisli durum makinesi. update() -> 'dim' | 'restore' | None.

    `now` monoton saat olmalı (time.monotonic): duvar saati kayarsa süreler bozulmaz.
    """

    def __init__(self, cfg):
        self.cfg, self.dimmed = cfg, False
        self.since = None          # mevcut durumun (bad/good/absent) başlangıcı
        self.kind = None

    def update(self, s, now):
        c = self.cfg
        kind = "absent" if s is None else "bad" if s > 1 else "good" if s < c["restore_at"] else self.kind
        if kind != self.kind:
            self.kind, self.since = kind, now
        held = now - self.since if self.since is not None else 0
        if not self.dimmed and kind == "bad" and held >= c["bad_secs"]:
            self.dimmed = True
            return "dim"
        if self.dimmed and ((kind == "good" and held >= c["good_secs"])
                            or (kind == "absent" and held >= c["absent_secs"])):
            self.dimmed = False
            return "restore"
        return None


class BreakTimer:
    """Kesintisiz oturma süresi. update() mola hatırlatma zamanı geldiyse True döner.

    İlk hatırlatma `remind_secs` sonra, mola verilmezse her `repeat_secs`'te bir tekrar.
    BREAK_SECS boyunca görünmemek mola sayılır ve sayaç sıfırlanır.
    """

    def __init__(self, repeat_secs=15 * 60):
        self.repeat = repeat_secs
        self.start = self.last_seen = self.reminded = None

    def update(self, present, now, remind_secs):
        if not present:
            return False
        if self.last_seen is None or now - self.last_seen >= BREAK_SECS:
            self.start, self.reminded = now, None          # yeni oturum (mola verilmiş)
        self.last_seen = now
        if not remind_secs:
            return False
        due = self.start + remind_secs if self.reminded is None else self.reminded + self.repeat
        if now >= due:
            self.reminded = now
            return True
        return False

    def sitting_secs(self, now):
        if self.last_seen is None or now - self.last_seen >= BREAK_SECS:
            return 0.0
        return now - self.start


# --- Kendi kendine ayar -------------------------------------------------------
# skor ölçütü -> ilgili eşik ayarı
PART_CFG = {"neck": "neck_deg", "trunk": "trunk_deg", "combined": "combined_deg",
            "lateral": "lateral_deg", "head_tilt": "head_tilt_deg", "shoulder": "tilt_deg",
            "drop": "drop_tol", "near": "tolerance", "sink": "sink_tol", "nod": "nod_deg"}
# örnek ölçüsü -> eşik ayarı (sihirbazda kötü duruştan eşik hesabı için)
TUNE_CFG = {"neck": "neck_deg", "trunk": "trunk_deg", "neck_lat": "lateral_deg",
            "trunk_lat": "lateral_deg", "head_tilt": "head_tilt_deg", "shoulder_tilt": "tilt_deg",
            "sink": "sink_tol", "combined": "combined_deg"}
# eşiklerin inebileceği en düşük değer: ölçüm gürültüsünün üstünde kalmak için
FLOOR = {"neck_deg": 5, "trunk_deg": 5, "combined_deg": 8, "lateral_deg": 3, "head_tilt_deg": 4,
         "tilt_deg": 3, "sink_tol": 0.06, "tolerance": 0.05, "drop_tol": 0.08, "nod_deg": 15}
SENSITIVITY = {"Rahat": 0.7, "Dengeli": 0.5, "Sıkı": 0.35}   # kötü duruşa giden yolun ne kadarında uyarsın


def pose_deviation(samples, base):
    """Bir duruşun (örneklerin medyanı) kalibrasyondan sapmaları: öne açılar işaretli, yanal mutlak."""
    import statistics
    def med(k):
        v = [s[k] for s in samples if s.get(k) is not None]
        return statistics.median(v) if v else None
    d = {}
    for k in ("neck", "trunk"):
        if med(k) is not None and base.get(k) is not None:
            d[k] = med(k) - base[k]
    for k in ("neck_lat", "trunk_lat", "head_tilt", "shoulder_tilt"):
        if med(k) is not None and base.get(k) is not None:
            d[k] = abs(med(k) - base[k])
    if med("sh_y") is not None and base.get("sh_y") is not None and base.get("sw"):
        d["sink"] = (med("sh_y") - base["sh_y"]) / base["sw"]
    if d.get("neck", 0) > 0 and d.get("trunk", 0) > 0:
        d["combined"] = d["neck"] + d["trunk"]
    return d


def tune(base, poses, frac):
    """Kötü duruş örneklerinden eşikler: her belirgin sapmanın `frac` katı (en düşük FLOOR).

    poses: kötü duruş örnek listelerinin listesi (kambur, yana yatma...). Bir ölçüt birden fazla
    pozda değiştiyse en küçük (en hassas) eşik alınır. Belirgin değişmeyen ölçütlere dokunulmaz.
    """
    out = {}
    for samples in poses:
        for k, dev in pose_deviation(samples, base).items():
            ck = TUNE_CFG[k]
            if dev >= FLOOR[ck] * 1.5:
                out[ck] = round(min(out.get(ck, float("inf")), max(FLOOR[ck], frac * dev)), 3)
    return out


def adjust(cfg, parts, target, limits, only_worst):
    """Eşikleri, mevcut duruşun skoru `target` olacak şekilde ölçekler. [(ayar, eski, yeni)] döner.

    Yanlış alarm: target < restore_at, sınırı aşan tüm ölçütler gevşer (en az %10).
    Kaçırılan kötü duruş: target > 1, yalnızca eşiğe en yakın ölçüt sıkılaşır (en az %10).
    """
    keys = [max(parts, key=parts.get)] if only_worst else [k for k, v in parts.items() if v > target]
    changes = {}
    for k in keys:
        ck = PART_CFG[k]
        if parts[k] <= 0 or ck in changes:
            continue
        old = cfg[ck]
        new = old * parts[k] / target
        new = max(new, old * 1.1) if target < 1 else min(new, old * 0.9)
        lo, hi = limits[ck]
        new = round(min(hi, max(lo, FLOOR.get(ck, lo), new)), 3)
        if new != old:
            changes[ck] = (old, new)
    for ck, (_, new) in changes.items():
        cfg[ck] = new
    return [(ck, o, n) for ck, (o, n) in changes.items()]


# --- Bakış bölgeleri (ikinci ekran) -------------------------------------------
# Bir bölgenin baş referansı ana kalibrasyondan en fazla bu kadar sapabilir; fazlası ekran değil
# kötü duruştur (ör. telefona eğilmek) ve öğrenilmez.
ZONE_MAX = {"neck": 30, "neck_lat": 10, "head_tilt": 12}
MAX_ZONES = 4


def zone_name(z, base):
    dy = z["head_yaw"] - base["head_yaw"]
    dp = (z["pitch"] - base["pitch"]) if z.get("pitch") is not None and base.get("pitch") is not None else 0
    dirs = [w for w, on in (("sol", dy > 10), ("sağ", dy < -10), ("aşağı", dp < -0.06), ("yukarı", dp > 0.06)) if on]
    taken = {o["name"] for o in base.get("zones", [])}
    n = 2                           # ana ekran 1. ekrandır
    while any(t.startswith(f"Ekran {n} ") or t == f"Ekran {n}" for t in taken):
        n += 1
    return f"Ekran {n} (" + ("-".join(dirs) or "ana ekranın yakını") + ")"


def make_zone(samples, base, name=None, source="auto"):
    """Örneklerden bakış bölgesi. (bölge, None) ya da (None, kullanıcıya hata mesajı)."""
    import statistics
    import time as _t
    samples = [s for s in samples if s.get("head_yaw") is not None]
    if len(samples) < 4:
        return None, "yüz ve omuzlar görünmüyor"
    z = {}
    for k in HEAD_KEYS + ("head_yaw", "pitch"):
        v = [s[k] for s in samples if s.get(k) is not None]
        z[k] = statistics.median(v) if v else None
    far = base.get("head_yaw") is not None and abs(z["head_yaw"] - base["head_yaw"]) > TURNED
    for k, lim in ZONE_MAX.items():
        if far and k in HEAD_LATERAL:
            continue                # farklı yöne bakarken kulak konumları zaten farklı görünür
        if z.get(k) is not None and base.get(k) is not None:
            dev = z[k] - base[k] if k == "neck" else abs(z[k] - base[k])
            if dev > lim:
                return None, ("bu bakışta boyun çok eğik; ekranı yükseltmeyi deneyin" if k == "neck"
                              else "baş yana eğik; dik oturup ekrana düz bakın")
    z["name"] = name or zone_name(z, base)
    z["source"], z["created"] = source, _t.time()
    return z, None


def add_zone(base, z):
    """Bölgeyi ekler; aynı yöne bakan eski bölgenin yerini alır. En fazla MAX_ZONES."""
    replaced = [o for o in base.get("zones", []) if zone_distance(z, o) < 12]
    if replaced and z.get("source") == "auto":
        z["name"] = replaced[0]["name"]   # aynı ekranı yeniden öğrendi: adı korunsun
    zones = [o for o in base.get("zones", []) if o not in replaced]
    zones.append(z)
    base["zones"] = zones[-MAX_ZONES:]


class ZoneLearner:
    """Gövde dik dururken sık bakılan ama bilinen bir bölgeye uymayan baş yönlerini öğrenir.

    Son `window` sn içinde aynı yöne (sağ-sol 8°, yukarı-aşağı 0.08 dilimleri) toplam `need` sn
    bakıldıysa o yön yeni bölge olur. update() yeni bölgeyi (dict) ya da None döner.
    """

    def __init__(self, window=1800, need=180):
        from collections import deque
        self.window, self.need = window, need
        self.buf = deque()          # (t, dt, dilim, ölçüm)
        self.last_t = None

    def update(self, m, parts, base, cfg, now):
        dt = min(2.0, now - self.last_t) if self.last_t is not None else 0.5
        self.last_t = now
        while self.buf and now - self.buf[0][0] > self.window:
            self.buf.popleft()
        if (m is None or parts is None or m.get("head_yaw") is None or m.get("pitch") is None
                or base.get("head_yaw") is None or not body_ok(parts, cfg)):
            return None
        if any(z.get("head_yaw") is not None and zone_distance(m, z) <= cfg["yaw_gate"]
               for _, z in zones_of(base)):
            return None             # zaten bilinen bir bölgeye bakıyor
        key = (round(m["head_yaw"] / 8), round(m["pitch"] / 0.08))
        self.buf.append((now, dt, key, m))
        if sum(d for _, d, k, _ in self.buf if k == key) < self.need:
            return None
        samples = [x for _, _, k, x in self.buf if k == key]
        self.buf = type(self.buf)(x for x in self.buf if x[2] != key)
        z, _ = make_zone(samples, base)
        return z
