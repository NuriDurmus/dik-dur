# Dik-Dur

Webcam ile oturuş duruşunu (boyun, bel ve ikisinin birlikte öne eğilmesi) izler. Duruş bozulunca ekran parlaklığını düşürür, düzelince eski haline getirir. Günlük duruş raporu tutar. Windows 10/11.

## Kullanım
- `dist\dik-dur\dik-dur.exe` dosyasını çalıştırın. Uygulama sistem tepsisinde açılır. İlk açılışta 5 sn içinde dik oturun, kalibrasyon otomatik yapılır.
- Tepsi simgesine çift tıklayınca **Rapor** açılır. Sağ tıklayınca **Ayarlar**, Duraklat, Yeniden kalibre et ve Çıkış seçenekleri çıkar.
- Simge renkleri: yeşil iyi duruş, turuncu duruş bozuluyor, kırmızı ışık düşürüldü, gri kişi yok / duraklatıldı / ekran kilitli, mavi kalibrasyon bekleniyor.

## Nasıl ölçer
MediaPipe Pose (lite) modeli kameradan 3 boyutlu iskelet tahmini çıkarır. Açılar kameraya göre değil, vücudun kendi önü ve sol-sağ eksenine göre hesaplanır.

| Ölçü | Ne | Işığı düşürür mü |
|---|---|---|
| Boyun öne | omuz ortası → kulak ortası, öne eğilme | evet |
| Bel öne | kalça ortası → omuz ortası, öne eğilme (kalçalar görünmeli) | evet |
| Kombine | boyun + bel toplamı (ikisi de tek başına eşik altında olsa bile) | evet |
| Yana yatma | gövde ve boyunun sola/sağa yatması, yönü kaydedilir | evet |
| Baş yana eğik | kulak hattının eğimi | evet |
| Omuz asimetrisi | omuz hattının eğimi | evet |
| Baş düşük / ekrana yakın | yalnız önden kamerada, 2B ölçü | evet |
| Baş dönüklüğü | başın gövdeye göre yana dönük olması (ör. yandaki monitör) | hayır, sadece rapor |
| Kesintisiz oturma | 2 dk'dan uzun ayrılmak mola sayılır; 50 dk'da mola hatırlatması | hayır, bildirim |

Yanal ölçüler kamera yandayken güvenilmez olduğu için o durumda kaydedilmez.

**Bakış bölgeleri (ikinci ekran):** Laptop gibi daha alçakta ya da yanda duran bir ekrana bakarken başın eğik olması kötü duruş sayılmaz. Her ekranın kendi baş referansı vardır. Uygulama baş yönüne (sağ-sol ve yukarı-aşağı) bakarak hangi ekrana bakıldığını anlar. Gövde ölçüleri (omuz, bel, çökme) her zaman ana kalibrasyona göre değerlendirilir. Bölgeler üç yolla tanımlanır:
- Kurulum sihirbazının "İkinci ekran" adımında
- Ayarlar'daki "Şu an baktığım ekranı öğret" düğmesiyle
- Otomatik olarak: gövde dikken son 30 dakikada aynı yöne toplam 3 dakika bakılırsa

Boyun aşırı eğikse (örneğin telefona bakarken) o yön bölge olarak öğrenilmez. Yeniden kalibrasyon bölgeleri sıfırlar.

Tepki süresi: duruş bozulduğunda 3 sn sonra ışık düşer (geçiş yaklaşık 1 sn). Düzelince yaklaşık 1 sn içinde geri gelir. Duruş iyiyken saniyede 2 kare, sınıra yakınken veya ışık düşükken saniyede 4 kare işlenir.

Bu açılar kullanıcının kendi kalibrasyonundaki duruşa göre değerlendirilir. Tıbbi ölçüm değildir.

## Gizlilik
- Görüntü hiçbir zaman diske yazılmaz. Sadece açılar ve durum bilgisi kaydedilir.
- Tüm veriler `%APPDATA%\dik-dur\` klasöründe durur: `config.json`, `dikdur.db` (SQLite) ve `dikdur.log`. 180 günden eski kayıtlar silinir (`retention_days` ayarı).
- Uygulama Python seviyesinde ağ bağlantısını kapatır. Native kütüphaneler için de kesin garanti isterseniz giden bağlantıyı Windows Güvenlik Duvarı'ndan engelleyin (yönetici PowerShell):
  ```
  New-NetFirewallRule -DisplayName "Dik-Dur engelle" -Direction Outbound -Action Block -Program "<yol>\dik-dur.exe"
  ```
- Ekran kilitliyken ve uygulama duraklatılmışken kamera kapatılır.

## Geliştirme
| Dosya | Görev |
|---|---|
| `app.py` | Giriş noktası, kamera döngüsü, tepsi, yaşam döngüsü |
| `posture.py` | Açı hesapları, skor ve durum makinesi (saf mantık, test edilebilir) |
| `store.py` | Ayarlar, SQLite, rapor özeti |
| `brightness.py` | Monitör parlaklığı, çökme sonrası kurtarma |
| `ui.py` | Ayar ve rapor pencereleri (customtkinter) |

```
pip install --no-deps -r requirements-lock.txt
python test_posture.py
python app.py
```
Exe derlemek için `build.bat` çalıştırın. Temiz bir sanal ortam kurar, sabitlenmiş sürümleri yükler, testleri çalıştırır ve derler.

Model dosyası `models/pose_landmarker_lite.task` (Google MediaPipe, Apache 2.0). Uygulama başlarken dosyanın SHA-256 özeti doğrulanır.
