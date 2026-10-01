"""Çalıştır: python test_posture.py  (kamera/model/ağ gerekmez)"""
import json, sqlite3, tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as P

import store
from posture import (BREAK_SECS, BreakTimer, Posture, angle_from_vertical, cause, lateral_side,
                     metrics, score, score_parts)
from store import DEFAULTS, advice, db_connect, insert_sample, summarize

cfg = {**DEFAULTS, "bad_secs": 5.0, "good_secs": 2.0}   # testler bu sürelere göre yazıldı

# açılar: dik çizgi 0°, 45° öne eğik 45° (y aşağı)
assert round(angle_from_vertical((0, 0, 0), (0, -1, 0))) == 0
assert round(angle_from_vertical((0, 0, 0), (0, -1, -1))) == 45

# --- skor ve neden ---
Z = {"neck_lat": 0, "trunk_lat": 0, "head_tilt": 0, "shoulder_tilt": 0, "head_yaw": 0}
base = {"neck": 10, "trunk": 5, "drop": 1.0, "sw": 0.5, **Z}
ok = {"neck": 10, "trunk": 5, "drop": 1.0, "sw": 0.5, "side": False, **Z}
assert score(ok, base, cfg) == 0
for bad, why in [({"neck": 30}, "neck"), ({"trunk": 25}, "trunk"), ({"drop": 0.7}, "neck"),
                 ({"sw": 0.6}, "near"), ({"trunk_lat": 12}, "lateral"), ({"neck_lat": -12}, "lateral"),
                 ({"head_tilt": 20}, "lateral"), ({"shoulder_tilt": -12}, "lateral"),
                 ({"neck": 30, "trunk": 30}, "both")]:
    p = score_parts({**ok, **bad}, base, cfg)
    assert max(p.values()) > 1 and cause(p) == why, (bad, p, cause(p))
assert cause(score_parts(ok, base, cfg)) is None
assert score({**ok, "head_yaw": 60}, base, cfg) == 0                   # baş dönüşü sadece kayıt

# boyun + bel kombine: ikisi de tek başına eşik altında (12° < 15°), toplam 24° > 20°
p = score_parts({**ok, "neck": 22, "trunk": 17}, base, cfg)
assert p["neck"] < 1 and p["trunk"] < 1 and p["combined"] > 1 and cause(p) == "both"

side = {**ok, "drop": None, "sw": None, "trunk": None, "side": True,
        **dict.fromkeys(Z, None)}
assert score(side, base, cfg) == 0 and score({**side, "neck": 30}, base, cfg) > 1
assert set(score_parts(side, base, cfg)) == {"neck"}                   # yandan: yanal ölçü yok

assert lateral_side({**ok, "trunk_lat": 5}) == "left" and lateral_side({**ok, "trunk_lat": -5}) == "right"
assert lateral_side({**ok, "trunk_lat": None, "neck_lat": -3}) == "right"

# --- metrics: kameraya dönük, dik oturan sahte iskelet ---
# 2B: x görüntüde sağ (= kameraya dönük kişinin solu), y aşağı. 3B: kalça merkezli, y aşağı.
IMG = {0: (0.5, 0.3), 7: (0.53, 0.32), 8: (0.47, 0.32), 11: (0.6, 0.6), 12: (0.4, 0.6),
       23: (0.56, 0.95), 24: (0.44, 0.95)}
WORLD = {0: (0, -0.75, -0.1), 7: (0.07, -0.7, 0), 8: (-0.07, -0.7, 0), 11: (0.2, -0.5, 0),
         12: (-0.2, -0.5, 0), 23: (0.1, 0, 0), 24: (-0.1, 0, 0)}

def skeleton(img=None, world=None):
    im = [P(x=0.5, y=0.5, z=0, visibility=1.0) for _ in range(33)]
    for i, (x, y) in {**IMG, **(img or {})}.items():
        im[i] = P(x=x, y=y, z=0, visibility=1.0)
    ws = [P(x=0, y=0, z=0) for _ in range(33)]
    for i, (x, y, z) in {**WORLD, **(world or {})}.items():
        ws[i] = P(x=x, y=y, z=z)
    return metrics(im, ws, 320, 240)

m = skeleton()
assert not m["side"] and m["drop"] > 0
for k in ("neck", "trunk", "neck_lat", "trunk_lat", "head_tilt", "shoulder_tilt", "head_yaw"):
    assert abs(m[k]) < 1, (k, m[k])
assert skeleton(world={7: (0.07, -0.65, -0.15), 8: (-0.07, -0.65, -0.15)})["neck"] > 30   # baş öne
# öne eğilme / derinlik hatası yanal ölçüleri bozmaz — 3B yöntemindeki hata buydu
m = skeleton(world={7: (0.07, -0.65, -0.15), 8: (-0.07, -0.65, -0.15), 11: (0.2, -0.5, 0.1)})
assert abs(m["neck_lat"]) < 1 and abs(m["head_tilt"]) < 1
m = skeleton({7: (0.61, 0.32), 8: (0.55, 0.32), 0: (0.58, 0.3)})          # baş sola kaydı
assert m["neck_lat"] > 15 and lateral_side(m) == "left"
m = skeleton({11: (0.7, 0.6), 12: (0.5, 0.6), 7: (0.63, 0.32), 8: (0.57, 0.32), 0: (0.6, 0.3)})  # gövde sola
assert m["trunk_lat"] > 10 and lateral_side(m) == "left"
m = skeleton({7: (0.53, 0.36), 8: (0.47, 0.28)})                          # sol kulak aşağıda
assert m["head_tilt"] > 20
m = skeleton({11: (0.6, 0.65), 12: (0.4, 0.55)})                          # sol omuz aşağıda
assert m["shoulder_tilt"] > 10
m = skeleton({0: (0.525, 0.3)})                                           # sola bakıyor
assert m["head_yaw"] > 30
# baş çok dönünce kulaklar üst üste biner: açı yine de doymadan artmaya devam eder
y1 = skeleton({0: (0.53, 0.3), 7: (0.51, 0.32), 8: (0.49, 0.32)})["head_yaw"]
y2 = skeleton({0: (0.535, 0.3), 7: (0.51, 0.32), 8: (0.49, 0.32)})["head_yaw"]
assert 40 < y1 < y2 < 90, (y1, y2)
m = skeleton(world={11: (0.02, -0.5, -0.2), 12: (-0.02, -0.5, 0.2)})      # yandan kamera
assert m["side"] and m["head_tilt"] is None and m["drop"] is None

# --- durum makinesi ---
p = Posture(cfg)
assert p.update(2, 0) is None and p.update(2, 4) is None               # 5 sn dolmadı
assert p.update(2, 5) == "dim"
assert p.update(0.8, 6) is None                                        # histerezis bandı: hala dimli
assert p.update(0.2, 7) is None and p.update(0.2, 9) == "restore"      # 2 sn iyi -> geri
p = Posture(cfg)                                                       # anlık kamburlaşma dimlemez
assert p.update(2, 0) is None and p.update(0.1, 2) is None and p.update(2, 3) is None
assert p.update(2, 7) is None
p = Posture(cfg)                                                       # kişi gitti -> geri al
p.update(2, 0); assert p.update(2, 5) == "dim"
assert p.update(None, 6) is None and p.update(None, 36) == "restore"

# --- mola hatırlatma ---
b = BreakTimer(repeat_secs=600)
sit = lambda t0, t1: [t for t in range(t0, t1, 30) if b.update(True, t, 3000)]   # 30 sn'de bir ölçüm
assert sit(0, 4000) == [3000, 3600]                                    # 50 dk'da, sonra 10 dk'da bir
assert not b.update(False, 4000, 3000)
assert sit(4000 + int(BREAK_SECS), 4000 + int(BREAK_SECS) + 2990) == []   # mola verildi: sayaç sıfırlandı
assert 2900 < b.sitting_secs(4000 + BREAK_SECS + 2970) < 3000
assert not BreakTimer().update(True, 99999, 0)                         # 0 = kapalı

# --- rapor özeti ---
def row(ts, state="good", cause=None, dimmed=0, **kw):
    r = {"ts": ts, "neck": 10, "trunk": None, "score": 0.1, "state": state, "dimmed": dimmed,
         "cause": cause, "trunk_lat": None, "neck_lat": None, "shoulder_tilt": None, "head_yaw": None,
         "zone": "Ana ekran"}
    r.update(kw)
    return r

# 10:00'dan itibaren 60 sn dik, 30 sn boyun kötü (dim), 100 sn ara, 10 sn sola yatma,
# 300 sn ara (mola), 20 sn dik ama baş 40° dönük
t0 = datetime(2026, 10, 1, 10).timestamp()
rows = [row(t0 + i) for i in range(60)]
rows += [row(t0 + 60 + i, "bad", "neck", int(i >= 5), neck=30, trunk=20) for i in range(30)]
rows += [row(t0 + 190 + i, "bad", "lateral", neck_lat=8) for i in range(10)]
rows += [row(t0 + 500 + i, head_yaw=40) for i in range(20)]
d = summarize(rows)[datetime(2026, 10, 1).date()]
assert d["sit"] == 60 + 39 + 19 + 20, d["sit"]                     # boşluklar 10 sn'ye kırpılır
assert d["upright"] == 80 and d["slouch"] == 1 and d["dims"] == 1
assert d["causes"]["neck"] == 39 and d["causes"]["lateral"] == 19
assert d["lat_left"] == d["causes"]["lateral"] and d["lat_right"] == 0
assert d["yaw"] == 20 and d["breaks"] == 1 and d["longest"] == 199
assert d["trunk"] == 20 and d["lat"] == 8 and d["hours"][10][0] == d["sit"]

# öneriler
day = summarize([row(t0 + i * 5, "bad", "lateral", trunk_lat=-6) for i in range(800)])
tips = advice(day)
assert any("sağ" in t for t in tips) and any("kesintisiz" in t for t in tips), tips
assert "10 dakika" in advice(summarize(rows))[0]

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # şema göçü: v1 veritabanı açılınca yeni kolonlar eklenir, eski veri korunur
    old = sqlite3.connect(tmp / "v1.db")
    old.execute("CREATE TABLE samples(ts REAL, neck REAL, trunk REAL, score REAL, state TEXT, dimmed INTEGER, side INTEGER)")
    old.execute("INSERT INTO samples VALUES (1,2,3,4,'good',0,0)"); old.commit(); old.close()
    con = db_connect(tmp / "v1.db")
    r = con.execute("SELECT * FROM samples").fetchone()
    assert r["neck"] == 2 and r["cause"] is None and r["head_yaw"] is None
    assert con.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    insert_sample(con, {"ts": 2, "neck": 5, "head_yaw": 12.5, "state": "good"})
    assert con.execute("SELECT head_yaw FROM samples WHERE ts=2").fetchone()[0] == 12.5
    con.close()

    # config: bozuk JSON -> varsayılan + yedek; aralık dışı değer yok sayılır
    store.DATA, store.CONFIG = tmp, tmp / "config.json"
    store.CONFIG.write_text("{bozuk")
    assert store.load_config() == DEFAULTS and (tmp / "config.bad.json").exists()
    store.CONFIG.write_text(json.dumps({"dim_ratio": 5, "neck_deg": 20, "camera": True, "x": 1,
                                        "baseline": {"neck": 10}}))
    c = store.load_config()
    assert c["dim_ratio"] == 0.5 and c["neck_deg"] == 20 and c["camera"] == 0 and "x" not in c
    assert c["baseline"] is None                                       # yanal ölçüsüz eski kalibrasyon
    store.save_config(c)
    assert store.load_config() == c and not (tmp / "config.json.tmp").exists()
print("OK")

# --- kalibrasyon doğrulama ---
from posture import calibrate
up = {"neck": 12, "trunk": None, "neck_lat": 1, "trunk_lat": None, "head_tilt": 2, "shoulder_tilt": -1, "head_yaw": 20, "sh_y": 0.6,
      "drop": 0.4, "sw": 0.35, "side": False}
base, err = calibrate([dict(up, neck=12 + i % 3) for i in range(8)])
assert err is None and base["neck"] == 13 and base["trunk"] is None
assert calibrate([up] * 3)[1]                                           # az örnek
assert "hareket" in calibrate([dict(up, neck=10 + 2 * i) for i in range(8)])[1]
assert "yana" in calibrate([dict(up, neck_lat=17)] * 8)[1]             # bugünkü hatalı kalibrasyon
assert "yana" in calibrate([dict(up, head_tilt=-14)] * 8)[1]
print("OK kalibrasyon")

# --- bel çökmesi (kalçalar görünmeden) ---
base2 = {"neck": 10, "trunk": 5, "drop": 1.0, "sw": 0.4, "sh_y": 0.6, **Z}
up2 = {**ok, "sh_y": 0.6, "sw": 0.4}
assert score(up2, base2, cfg) == 0
slump = {**up2, "sh_y": 0.6 + 0.4 * 0.3}                               # omuzlar genişliğin %30'u kadar indi
p = score_parts(slump, base2, cfg)
assert p["sink"] > 1 and cause(p) == "trunk"
assert score_parts({**up2, "sh_y": 0.55}, base2, cfg)["sink"] == 0     # yukarı çıkmak sorun değil
assert cause(score_parts({**slump, "neck": 30}, base2, cfg)) == "both"
assert skeleton({11: (0.6, 0.75), 12: (0.4, 0.75)})["sh_y"] > skeleton()["sh_y"]
print("OK cokme")

# --- kendi kendine ayar ---
from posture import adjust, pose_deviation, tune
from store import LIMITS
b3 = {"neck": 10, "trunk": None, "neck_lat": 0, "trunk_lat": None, "head_tilt": 0, "shoulder_tilt": 0,
      "sh_y": 0.6, "sw": 0.4, "drop": 1.0, "head_yaw": 0}
slouch = [dict(b3, neck=24, sh_y=0.6 + 0.4 * 0.3, side=False) for _ in range(8)]   # boyun +14°, omuz %30 aşağı
lean = [dict(b3, neck_lat=12, shoulder_tilt=1, side=False) for _ in range(8)]
d = pose_deviation(slouch, b3)
assert round(d["neck"]) == 14 and round(d["sink"], 2) == 0.3 and "trunk" not in d
t = tune(b3, [slouch, lean], 0.5)
assert t == {"neck_deg": 7.0, "sink_tol": 0.15, "lateral_deg": 6.0}, t       # omuz 1° değişti: dokunulmadı
assert tune(b3, [slouch], 0.35)["neck_deg"] == 5                               # FLOOR altına inmez
c = {**DEFAULTS}
ch = adjust(c, {"neck": 1.4, "sink": 1.2, "lateral": 0.3}, 0.6, LIMITS, only_worst=False)
assert {k for k, _, _ in ch} == {"neck_deg", "sink_tol"} and c["neck_deg"] == 35.0  # 15*1.4/0.6
ch = adjust(c, {"neck": 0.5, "sink": 0.8}, 1.2, LIMITS, only_worst=True)
assert ch == [("sink_tol", 0.4, round(0.4 * 0.8 / 1.2, 3))] and c["neck_deg"] == 35.0   # ilk adımda 0.4 olmuştu
assert adjust(c, {"neck": 0.0}, 1.2, LIMITS, only_worst=True) == []          # kalibrasyonla aynı: yapılacak yok
print("OK ayar")

# --- baş dönükken baş ölçüleri karara katılmaz ---
bb = {"neck": 10, "trunk": None, "neck_lat": 3, "trunk_lat": None, "head_tilt": 0, "shoulder_tilt": 0,
      "sh_y": 0.6, "sw": 0.4, "drop": 1.0, "head_yaw": 0}
turned = {**bb, "side": False, "head_yaw": 30, "neck": 18, "neck_lat": 17, "head_tilt": 9, "drop": 0.8}
assert score(turned, bb, cfg) < 1                                      # sadece baş çevirdi: iyi
assert score({**turned, "head_yaw": 5}, bb, cfg) > 1                   # aynı sapmalar baş düzken: kötü
assert score({**turned, "sh_y": 0.6 + 0.4 * 0.3}, bb, cfg) > 1         # baş dönük + çökme: yine yakalanır
assert score({**turned, "shoulder_tilt": 15}, bb, cfg) > 1             # omuz asimetrisi de
assert score({**turned, "head_yaw": None}, bb, cfg) > 1                # dönüş ölçülemiyorsa her şey sayılır
print("OK bas donusu")

# --- bakış bölgeleri (ikinci ekran) ---
from posture import ZoneLearner, add_zone, make_zone, match_zone
zb = {"neck": 10, "trunk": None, "neck_lat": 0, "trunk_lat": None, "head_tilt": 0, "shoulder_tilt": 0,
      "sh_y": 0.6, "sw": 0.4, "drop": 1.0, "head_yaw": 0, "pitch": 0.1}
laptop = {**zb, "side": False, "head_yaw": -20, "pitch": -0.1, "neck": 26, "drop": 0.75}   # sağ-aşağıdaki laptop
assert score(laptop, zb, cfg) < 1                              # bölge yokken: 20° dönük baş yok sayılır
below = {**laptop, "head_yaw": 0}                               # tam alttaki laptop: bölge yokken kötü
assert score(below, zb, cfg) > 1
z, err = make_zone([below] * 8, zb, source="manual")
assert err is None and z["name"] == "Ekran 2 (aşağı)" and z["neck"] == 26
add_zone(zb, z)
assert match_zone(below, zb, cfg)[0] == "Ekran 2 (aşağı)" and score(below, zb, cfg) < 1   # artık normal
assert match_zone({**zb, "side": False}, zb, cfg)[0] == "Ana ekran"                       # ana ekrana bakış
assert score({**below, "neck": 45}, zb, cfg) > 1                # laptop bölgesinde daha da kamburlaşma: yakalanır
assert score({**below, "sh_y": 0.6 + 0.4 * 0.3}, zb, cfg) > 1   # laptopa bakarken çökme: yakalanır
phone = {**below, "pitch": -0.35, "neck": 48, "drop": 0.5}      # telefona eğilme: en yakın bölgeye göre kötü
assert score(phone, zb, cfg) > 1
assert make_zone([phone] * 8, zb)[1]                            # ve bölge olarak öğrenilmez
assert make_zone([{**below, "head_tilt": 20}] * 8, zb)[1]       # baş yana eğikken de
add_zone(zb, dict(z, neck=27))                                  # aynı yöne yeni bölge eskisinin yerini alır
assert len(zb["zones"]) == 1 and zb["zones"][0]["neck"] == 27

# otomatik öğrenme: gövde dik, 3 dk aynı yöne bakış -> bölge
zb2 = {k: v for k, v in zb.items() if k != "zones"}
L = ZoneLearner(need=180)
good_body = {"neck": 2.0, "sink": 0.0, "shoulder": 0.1}
assert all(L.update(below, good_body, zb2, cfg, t * 0.5) is None for t in range(300))      # 150 sn: henüz değil
assert all(L.update(below, {**good_body, "sink": 1.5}, zb2, cfg, 150 + t * 0.5) is None
           for t in range(200))                                 # gövde çökmüşken öğrenmez
learned = [L.update(below, good_body, zb2, cfg, 250 + t * 0.5) for t in range(80)]
z2 = next(x for x in learned if x)
assert z2["name"] == "Ekran 2 (aşağı)" and z2["source"] == "auto"
assert ZoneLearner(need=10).update({**zb, "side": False}, good_body, zb2, cfg, 0) is None   # ana ekrana bakış öğrenilmez
print("OK bolgeler")

# --- baş kameraya göre çok dönükken kalibrasyon (kamera laptopta, ana ekran yanda) ---
side_up = dict(up, head_yaw=80, neck_lat=13, head_tilt=-11)
base, err = calibrate([side_up] * 8)
assert err is None and base["neck_lat"] == 13                         # baş yanal ölçüleri olduğu gibi referans
assert "omuz" in calibrate([dict(side_up, shoulder_tilt=12)] * 8)[1]   # gövde/omuz yine denetlenir
assert "boyun yana" in calibrate([dict(up, neck_lat=13)] * 8)[1]       # baş düzken denetim sürer
base["zones"] = []
lap, err = make_zone([dict(side_up, head_yaw=5, pitch=-0.1, neck_lat=1, head_tilt=0)] * 8, base)
assert err is None                                                     # farklı yöndeki laptop kabul edilir
add_zone(base, lap)
c2 = {**DEFAULTS}
assert match_zone(dict(side_up, head_yaw=97), base, c2)[0] == "Ana ekran"     # 80±~23 tolerans
assert match_zone(dict(side_up, head_yaw=8, pitch=-0.1), base, c2)[0] == lap["name"]
print("OK donuk kamera")

# --- birden fazla ekran ---
mb = {**zb, "zones": []}
for yaw, pitch in ((40, 0.1), (-40, 0.1), (0, -0.1)):            # sol, sağ, alttaki laptop
    zz, err = make_zone([{**zb, "side": False, "head_yaw": yaw, "pitch": pitch}] * 8, mb, source="manual")
    assert err is None
    add_zone(mb, zz)
assert [x["name"] for x in mb["zones"]] == ["Ekran 2 (sol)", "Ekran 3 (sağ)", "Ekran 4 (aşağı)"]
for yaw, pitch, want in ((42, 0.1, "Ekran 2 (sol)"), (-38, 0.1, "Ekran 3 (sağ)"), (1, -0.09, "Ekran 4 (aşağı)"),
                         (0, 0.1, "Ana ekran"), (90, 0.1, None)):
    assert match_zone({**zb, "side": False, "head_yaw": yaw, "pitch": pitch}, mb, cfg)[0] == want, (yaw, want)
again, _ = make_zone([{**zb, "side": False, "head_yaw": 41, "pitch": 0.1}] * 8, mb)   # sol ekran otomatik tekrar
add_zone(mb, again)
assert len(mb["zones"]) == 3 and mb["zones"][-1]["name"] == "Ekran 2 (sol)"         # yer değiştirdi, ad korundu
print("OK coklu ekran")

# --- aşağı bakma: baş eğme hoş görülür, boyun uzatma yakalanır ---
nb = {"neck": 10, "trunk": None, "neck_lat": 0, "trunk_lat": None, "head_tilt": 0, "shoulder_tilt": 0,
      "sh_y": 0.6, "sw": 0.4, "drop": 0.6, "pitch": 0.0, "head_yaw": 0}
look = {**nb, "side": False}
nodded = {**look, "pitch": -0.12, "drop": 0.48, "neck": 30}     # başı ~29° eğdi: burun 0.12 indi, boyun şişti
assert score(nodded, nb, cfg) < 1, score_parts(nodded, nb, cfg)
crane = {**look, "drop": 0.42, "neck": 30}                      # boyun öne uzadı: kulaklar da indi, pitch aynı
assert score(crane, nb, cfg) > 1 and cause(score_parts(crane, nb, cfg)) == "neck"
assert score({**nodded, "neck": 60}, nb, cfg) > 1               # baş eğik + boyun çok uzamış: yine kötü
phone_look = {**look, "pitch": -0.25}                          # telefona bakar gibi ~60° eğik
assert cause(score_parts(phone_look, nb, cfg)) == "neck" and score(phone_look, nb, cfg) > 1
print("OK asagi bakma")

# --- parlaklık: dışarıdan kısılan ekran "orijinal" sanılmaz ---
import tempfile as _tf, brightness as _br
class FakeSBC:
    def __init__(self, vals): self.v = list(vals)
    def get_brightness(self, display=None): return list(self.v)
    def set_brightness(self, v, display=0): self.v[display] = v
    def list_monitors(self): return ["laptop", "dell"]
_br.STATE = __import__("pathlib").Path(_tf.mkdtemp()) / "state.json"
fs = FakeSBC([100, 100]); b = _br.Brightness(fs)
b.observe(0)
fs.v[0] = 50                                   # Lenovo/Windows laptopu kıstı (bakmayınca)
b.observe(60)
b.dim(0.5); assert fs.v == [50, 50]            # %50 karartma normal düzeye göre
b.restore(); assert fs.v == [100, 100], fs.v   # laptop kısık kalmadı
fs.v[0] = 40; b.observe(100); b.observe(100 + 600)   # kullanıcı 10 dk kısık tuttu: kendi ayarı
b.dim(0.5); b.restore(); assert fs.v == [40, 100], fs.v
class Swallow(FakeSBC):                        # ilk yazmayı yutan sürücü
    n = 0
    def set_brightness(self, v, display=0):
        self.n += 1
        if not (display == 0 and self.n == 4): self.v[display] = v
fs2 = Swallow([100, 100]); b2 = _br.Brightness(fs2); b2.dim(0.5); b2.restore()
assert fs2.v == [100, 100], fs2.v              # doğrulama + tekrar deneme
print("OK parlaklik")
