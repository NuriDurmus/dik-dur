# Dik-Dur: Duruş Takipli Ekran Parlaklığı Uygulaması — Analiz

## 1. Amaç

Webcam görüntüsünden kullanıcının dik oturup oturmadığını algıla. Eşik açı aşılırsa (kamburlaşma/öne eğilme) ekran parlaklığını **%50 düşür**. Duruş düzelince parlaklığı **önceki değerine geri getir**. Masaüstü uygulaması, düşük CPU kullanımı, hazır/pratik kütüphaneler.

**Kapsam dışı (şimdilik):** istatistik/raporlama, bulut, çoklu kullanıcı, mobil, ses uyarısı.

## 2. Temel Teknik Sorular ve Kararlar

### 2.1 Duruş nasıl ölçülür?

Webcam genelde **karşıdan** bakar; "omurga açısı" doğrudan görülemez. Seçenekler:

| Yöntem | Kamera açısı | Artı | Eksi |
|---|---|---|---|
| **A. Kulak–omuz açısı (CVA)** | Yandan | Klinik olarak en anlamlı ölçü | Webcam yandan bakmaz; kullanıcı kamerayı yana koymak zorunda |
| **B. Pose landmark + kalibrasyon (önerilen)** | Karşıdan | Standart webcam yeter | Mutlak açı değil, "kendi dik duruşuna göre sapma" |
| C. Sadece yüz kutusu (boyut/konum) | Karşıdan | En hafif | Güvenilmez, omuz bilgisi yok |

**Karar: B.** Açılış kalibrasyonu: kullanıcı dik otururken 3 sn ölçüm → baz değerler kaydedilir. Sonra sapma izlenir. Karşıdan görünüşte kullanılabilecek metrikler (MediaPipe Pose landmark'larından):

1. **Baş öne eğilme (ana metrik):** burun ile omuz hattı orta noktası arasındaki dikey mesafe / omuz genişliği. Kamburlaşınca baş öne-aşağı gelir, oran düşer.
2. **Yakınlaşma:** omuz genişliğinin (piksel) baza göre artışı → gövde kameraya doğru eğilmiş.
3. **Omuz eğimi:** sol/sağ omuz arası çizginin yatay ile açısı → yana yatma.
4. **Baş yan eğimi:** kulaklar arası çizgi açısı.

Skor: her metrik bazdan sapma yüzdesi; ağırlıklı toplam (veya basitçe max). Kullanıcıya **tek bir hassasiyet kaydırıcısı** ("eşik açı" yerine "sapma toleransı") sun. İstenirse kullanıcıya yandan kamera modu için A seçeneği ikinci mod olarak eklenebilir (kulak–omuz–dikey açısı, ör. >~20° öne = kötü).

> Not: "Belli açı" isteği korunuyor: metrik 1 ve 3 doğrudan açıya çevrilebilir (`atan2`), ayarlarda derece cinsinden gösterilir.

### 2.2 Pose motoru

| Kütüphane | CPU maliyeti | Not |
|---|---|---|
| **MediaPipe Pose (BlazePose, lite/`model_complexity=0`)** | Düşük (CPU'da ~5–15 ms/kare) | Hazır Python paketi, omuz/kulak/burun landmark'ları, kurulum kolay |
| MoveNet Lightning (TFLite/ONNX) | Düşük | Daha az landmark, entegrasyon biraz daha uzun |
| OpenPose / YOLO-pose | Yüksek | Gereksiz ağır |

**Karar: MediaPipe Pose, lite model.** (Sürüm notu: `mp.solutions.pose` eski API; yeni `PoseLandmarker` Tasks API'si var. Kurulum sırasında güncel paket sürümüne göre doğrulanmalı — bu belge web'den doğrulanmadan hazırlandı.)

### 2.3 CPU'yu yormamak

- **Düşük kare hızı:** 1–2 FPS yeterli (duruş yavaş değişir). Kare yakalamadan sonra `sleep`.
- **Düşük çözünürlük:** 320×240 yakalama; pose için fazlası gerekmiyor.
- **Uyarlanabilir örnekleme:** duruş iyiyken 1 FPS, kötüye giderken 3 FPS; sürekli kötüyse 1 FPS.
- **Kamerayı sadece örnekleme anında oku** (`cap.grab()` ile tampon boşalt, `retrieve` sadece gerektiğinde) veya her turda aç/kapa (LED yanıp söner, istenmez) — kamerayı açık tutup düşük FPS tercih edilir.
- **Kişi yoksa** (landmark yok) FPS'i 0.2'ye düşür.
- **Ekran kilitli / oturum kilitliyken** duraklat (Windows `WTS_SESSION_LOCK` olayı).
- Hedef: boşta **<%3–5 CPU**, tek çekirdek, bellek ~150–250 MB (MediaPipe + OpenCV yüküyle).

### 2.4 Parlaklık kontrolü (Windows 11)

| Yöntem | Kapsam | Artı | Eksi |
|---|---|---|---|
| **`screen-brightness-control` (WMI + DDC/CI)** | Dizüstü paneli + DDC/CI destekli harici monitörler | Gerçek donanım parlaklığı, hazır paket | Bazı harici monitörlerde DDC/CI kapalı/yavaş (komut ~100ms–1sn) |
| `monitorcontrol` (DDC/CI) | Harici monitörler | Düşük seviye kontrol | Sadece harici |
| **Yarı saydam siyah örtü penceresi (overlay)** | Her ekran | Her donanımda çalışır, anında | Gerçek parlaklık değil (güç tasarrufu yok), tıklamaları geçirecek şekilde ayarlanmalı |
| Gamma ramp (`SetDeviceGammaRamp`) | Her ekran | Basit | Bazı sürücüler/oyunlar sıfırlar, renk kayması |

**Karar:** Birincil `screen-brightness-control`; başarısız olursa (WMI desteklemiyorsa) **overlay'e otomatik düşüş**. Parlaklığı birden değil **~1 sn'de kademeli** (fade) değiştir — ani değişim rahatsız edici.

**"%50 düşür" tanımı:** `yeni = mevcut × 0.5` (göreli). Mevcut değer, düşürmeden hemen önce okunup saklanır; geri dönüşte o değer yazılır. Ayarlanabilir kılın (varsayılan %50).

## 3. Davranış Mantığı (Durum Makinesi)

```
 NORMAL ──(kötü duruş, süre ≥ T_kötü)──▶ DİMLENMİŞ
   ▲                                          │
   └──(iyi duruş, süre ≥ T_iyi)───────────────┘
```

- **T_kötü (varsayılan 5 sn):** anlık kamburlaşmada ışık titremesin.
- **T_iyi (varsayılan 2 sn):** düzelince hızlı dönsün.
- **Histerezis:** düşürme eşiği ≠ geri alma eşiği (ör. %15 sapmada düşür, %10'un altına inince geri al). Eşik civarında yanıp sönmeyi önler.
- **Yumuşatma:** metriklere EMA (α≈0.3) veya son N karenin medyanı.
- **Kişi tespit edilmedi:** durumu değiştirme; uzun süre yoksa (30 sn) **geri yükle** ve bekle (kullanıcı kalkmış, ekran karanlık kalmasın).
- **Kullanıcı elle parlaklık değiştirirse:** dimlenmiş durumdayken okunan değer beklenenden farklıysa bunu "yeni baz" kabul et, geri dönüşte ezme.
- **Uygulama kapanırken/çökerken:** `atexit` + sinyal yakalayıcıyla parlaklığı mutlaka geri yükle. Baz değeri diske de yaz (çökme sonrası açılışta "dimli kaldı" kurtarması).

## 4. Mimari ve Teknoloji Yığını

### 4.1 Karşılaştırma

| Yığın | Artı | Eksi |
|---|---|---|
| **Python + MediaPipe + OpenCV + pystray (önerilen)** | Hızlı geliştirme, tüm parçalar hazır, sistem tepsisi kolay | Paket boyutu büyük (PyInstaller ~150–300 MB), Python runtime |
| Electron + MediaPipe JS/TF.js | Güzel arayüz | Chromium ağır (RAM/CPU), "çok yormasın" şartına ters |
| C#/.NET + ONNX Runtime (MoveNet) | En hafif ve yerel, küçük exe | Daha fazla işçilik, parlaklık için P/Invoke gerekir |

**Karar: Python.** Şart "pratik kütüphaneler"; en kısa yol bu. Hafiflik ileride sorun olursa pose kısmı ONNX'e/C#'a taşınabilir.

### 4.2 Bileşenler

- `capture` — OpenCV `VideoCapture`, düşük çözünürlük, FPS sınırı
- `pose` — MediaPipe Pose → landmark → duruş skoru
- `posture` — kalibrasyon, eşik, histerezis, durum makinesi (saf mantık, test edilebilir)
- `brightness` — `get()/set()/fade()`; WMI/DDC → overlay fallback
- `tray` — `pystray` sistem tepsisi: Duraklat/Devam, Yeniden kalibre et, Ayarlar, Çıkış (arayüz yok denecek kadar az)
- `config` — JSON dosyası (`%APPDATA%\dik-dur\config.json`): eşik, T_kötü/T_iyi, dim oranı, FPS, kamera indeksi, kalibrasyon baz değerleri

Tek süreç, tek ana döngü iş parçacığı + tepsi iş parçacığı. Ayarlar için ilk sürümde sade bir `tkinter` penceresi veya doğrudan JSON; ayrı UI framework'ü gerekmez.

### 4.3 Önerilen bağımlılıklar

```
mediapipe
opencv-python        # (opencv-python-headless pencere açmayacağımız için de olur)
screen-brightness-control
pystray
Pillow               # pystray ikonu için
pyinstaller          # paketleme (geliştirme bağımlılığı)
```

## 5. Riskler ve Açık Noktalar

| Risk | Etki | Önlem |
|---|---|---|
| Karşıdan görüntüde duruş ölçümü yaklaşık | Yanlış alarm/kaçırma | Kişiye özel kalibrasyon + hassasiyet ayarı; ileride yandan kamera modu |
| Harici monitörde DDC/CI yok veya yavaş | Parlaklık değişmez | Overlay fallback; ilk açılışta yetenek testi |
| Webcam LED'i sürekli yanar, gizlilik algısı | Kullanıcı rahatsızlığı | Görüntü kaydedilmez/diske yazılmaz, tepsi ikonunda "kamera aktif" göstergesi, kolay duraklat |
| Kamera başka uygulamada (Teams/Zoom) kullanılıyor | Açılamaz / çakışma | Açılamazsa duraklat ve tekrar dene; toplantıda (kamera meşgulse) otomatik pasif |
| Gözlük, kötü ışık, kafa kısmen kadraj dışı | Landmark kaybı | Güven skoru (`visibility`) eşiği; düşük güvende karar verme |
| Dizüstü kapağı kapalı / harici monitör modu | Kamera yok | Kişi yok durumuna düş, dokunma |
| Çökme anında parlaklık düşük kalması | Kullanıcı mağdur olur | Baz değer diske yaz + açılışta kurtarma + `atexit` |
| Kurumsal bilgisayarda (Enterprise) WMI/kamera politikası | Çalışmayabilir | İlk testte kontrol et |

## 6. Doğrulama / Test Planı

1. **Saf mantık birim testleri:** durum makinesi (sentetik skor dizisiyle: geçici dalgalanma dimlemez, kalıcı kötü duruş dimler, düzelince geri döner, histerezis).
2. **Kalibrasyon:** aynı kişide farklı günlerde baz değer sapması makul mü?
3. **Performans ölçümü:** Görev Yöneticisi / `psutil` ile 10 dk boşta CPU ve RAM; hedef <%5 CPU.
4. **Parlaklık:** dizüstü paneli, harici monitör (DDC/CI açık/kapalı), çift monitör.
5. **Dayanıklılık:** süreci zorla sonlandır → parlaklık düşük kalıyor mu, açılışta kurtarılıyor mu.
6. **Kullanıcı testi:** 1 hafta kendi kullanımında yanlış alarm sayısı.

## 7. Yol Haritası

| Aşama | İçerik | Çıktı |
|---|---|---|
| **0. Prototip (1 gün)** | Webcam + MediaPipe → konsola duruş skoru | Metriklerin güvenilir olup olmadığı kanıtı |
| **1. MVP (2–3 gün)** | Kalibrasyon + durum makinesi + `screen-brightness-control` + tepsi + JSON ayar | Çalışan uygulama |
| **2. Sağlamlaştırma** | Overlay fallback, kilit/oturum algısı, çökme kurtarma, uyarlanabilir FPS | Günlük kullanılabilir |
| **3. Paketleme** | PyInstaller tek klasör/exe, Windows ile başlangıçta çalıştır (Startup kısayolu) | Kurulum gerektirmeyen dağıtım |
| **4. İsteğe bağlı** | Yandan kamera modu, ayar penceresi, günlük duruş istatistiği, mola hatırlatma | — |

## 8. Karar Gerektiren Sorular

1. **Kamera nerede duruyor?** Karşıdan (standart webcam) varsayıldı. Yandan kamera mümkünse ölçüm çok daha doğru olur.
2. **Harici monitör mü, dizüstü paneli mi?** Parlaklık yöntemini belirler (DDC/CI vs WMI).
3. **"%50"** mevcut parlaklığın yarısı mı, mutlak %50 mi? Burada göreli yarısı varsayıldı.
4. **Dağıtım:** sadece kendi kullanımın mı, başkalarına da verilecek mi? (Paketleme ve imzalama çabasını etkiler.)

## 9. Özet Öneri

**Python + MediaPipe Pose (lite) + OpenCV + `screen-brightness-control` + `pystray`**, 1–2 FPS örnekleme, kullanıcıya özel kalibrasyonla "baş öne eğilme / omuz eğimi" sapması ölçülür; histerezisli ve gecikmeli durum makinesi parlaklığı kademeli olarak yarıya indirir, duruş düzelince eski değere döndürür. Her durumda çıkışta/çökmede parlaklık geri yüklenir.
