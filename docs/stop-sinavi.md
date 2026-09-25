# Stop sınavı — Donchian girişinde stop nerede olmalı?

Başlangıç: 2026-09-25. Dal: `faz2a-stop-sinavi`. Ali'nin sorusu: "stop
hesaplamasını ayrıca yapmasını da ekle, yani nerede stop en iyi olmalı".

## §0 ÖN KAYIT — sonuçlar görülmeden yazıldı

Bu bölüm sınav KOŞULMADAN önce yazıldı ve sonuçlar geldikten sonra
DEĞİŞTİRİLMEZ. Değişirse ayrı alt bölümde, gerekçe ve tarihle.

### Soru

Donchian 20/10 giriş kuralı sabit kalırken, stop'un türü ve mesafesi
işlem başına beklentiyi ve kuyruk kaybını nasıl değiştiriyor? Bugünkü 2N
sabit stop'tan daha iyisi var mı?

**Bu bir kenar sınavı değil.** Donchian'ın rastgele girişe göre kenarı yok
(`strateji-kenari-yok`). Soru, aynı giriş kuralıyla hangi stop'un daha az
kötü sonuç verdiği.

### Adaylar (sabit, sonradan eklenmez)

| kol | stop |
|---|---|
| S0 | 2N sabit (üretimdeki kural, taban) |
| S1 | 1,5N sabit |
| S2 | 3N sabit |
| S3 | 2N izleyen: girişten beri en yüksek kapanışın 2N altı, yalnızca yükselir |
| S4 | 3N izleyen |
| S5 | stop yok (yalnızca 10 gün dip çıkışı) |

N girişteki 20 günlük ATR ve işlem boyunca sabit. İzleyen stop'ta i.
barın stop'u yalnızca i−1'e kadarki kapanışlardan hesaplanır (ileriye
bakma yok). Çıkış fiyatı üretimdeki kuralla aynı: stop'a değen barda
`min(stop, açılış)`.

### Veri ve dönemler

- Evren ve likidite eşiği `run.py trend --piyasa abd` ile aynı (S&P 500 +
  Nasdaq 100, bugünkü üyelik; hayatta kalma yanlılığı var ve bütün
  kolları aynı şekilde etkiler).
- Maliyet %0,40 gidiş-dönüş.
- **Seçim dönemi (IS):** girişi 2016-01-01 → 2021-12-31 olan işlemler.
- **Doğrulama dönemi (OOS):** girişi 2022-01-01 → 2026-09-24 olan
  işlemler. Seçim yapılırken OOS'a BAKILMAZ.

### Ölçütler

Birincil: işlem başına net beklenti. İkincil: p5 işlem getirisi, en kötü
işlem, aylık kümelenmiş t, en kötü ay, işlem sayısı, stop'la çıkış payı.

### Karar kuralı

1. **Seçim (yalnız IS):** IS beklentisi en yüksek kol seçilir. S0 ise
   karar "2N sabit kalır" ve kural biter.
2. **Doğrulama (yalnız OOS):** seçilen kol S0'ın yerine ancak ÜÇÜ de
   sağlanırsa geçer:
   - (a) OOS beklentisi S0'dan yüksek,
   - (b) iki kolun işlem açılan ortak aylarında aylık ortalama getiri
     farkının t değeri ≥ 2,0 (eşleştirilmiş),
   - (c) OOS p5 değeri S0'ınkinden 1 puandan fazla kötü değil (beklentiyi
     çok daha derin kuyruk kaybıyla satın alan bir stop "daha iyi stop"
     sayılmaz).
3. Geçmezse **2N sabit kalır.** Canlı sisteme (alarm, stop emri) yalnızca
   geçen kol bağlanır.

Altı kol, tek seçim + ayrık doğrulama: çoklu karşılaştırma yanlılığını IS
seçimi taşır, OOS yalnızca tek adayı sınar.

## §1 Sonuç (2026-09-25, `scripts/stop_sinavi.py`)

Veri: 24 Eyl db kopyası, 511 sembol (kısa seri yok). **Çapraz kontrol:**
S0'ın 2016-2026 işlem sayısı (31.326) takvim sınavının taban koluyla
birebir aynı; motor değişikliği üretimdeki kuralı bozmadı.

### Seçim dönemi (IS, 2016-2021)

| kol | işlem | beklenti % | p5 % | t (ay) | en kötü ay % | stop'la çıkış % | ort. gün |
|---|---|---|---|---|---|---|---|
| S0 2N sabit | 16.868 | 0,495 | −6,99 | 0,33 | −5,35 | 29,6 | 16,8 |
| S1 1,5N | 18.115 | 0,359 | −6,01 | −0,02 | −4,82 | 44,3 | 15,0 |
| S2 3N | 16.110 | 0,626 | −7,88 | 0,57 | −6,63 | 10,7 | 18,1 |
| S3 2N izleyen | 19.242 | 0,298 | −6,50 | 0,01 | −4,92 | 74,4 | 13,0 |
| S4 3N izleyen | 16.707 | 0,527 | −7,70 | 0,39 | −6,59 | 35,3 | 16,8 |
| **S5 stopsuz** | 15.979 | **0,662** | −8,14 | 0,55 | −6,92 | 0 | 18,4 |

Seçilen: **S5** (IS beklentisi en yüksek).

### Doğrulama dönemi (OOS, 2022 → 24 Eyl 2026)

| kol | işlem | beklenti % | p5 % | t (ay) | S0'a karşı eşleştirilmiş t (57 ay) |
|---|---|---|---|---|---|
| S0 | 14.458 | 0,282 | −8,05 | −0,92 | — |
| S1 | 15.614 | 0,168 | −6,84 | −1,00 | 0,26 |
| S2 | 13.772 | 0,385 | −9,27 | −0,82 | 0,17 |
| S3 | 16.820 | −0,108 | −7,65 | −1,99 | −1,84 |
| S4 | 14.401 | 0,126 | −9,10 | −1,44 | −2,33 |
| S5 | 13.634 | 0,464 | −9,52 | −0,67 | 1,05 |

S5 için şartlar: (a) beklenti 0,464 > 0,282 ✓ · (b) t = 1,05 < 2,0 ✗ ·
(c) p5 −9,52, S0'dan 1,47 puan kötü (tolerans 1) ✗.

### KARAR: 2N sabit stop KALIR.

Canlıya (Faz 2c GTC STP emri) bağlanacak seviye: giriş − 2N.

### Ne öğrendik (betimsel, karar vermez)

1. **Stop beklenti kazandırmıyor, kuyruğu biçimliyor.** Stop gevşedikçe
   beklenti yükseliyor ve p5 derinleşiyor; iki dönemde de aynı sıra.
   Hiçbir kol S0'dan anlamlı biçimde iyi değil (en yüksek |t| OOS'ta
   S4'ün −2,33'ü, yani daha KÖTÜ yönde).
2. **İzleyen stop bu girişle zararlı.** S3 iki dönemde de en kötü
   beklenti; işlemlerin %74-79'unu stop'la kapatıyor, trendi erken kesiyor.
3. **En kötü işlem her kolda aynı** (IS −82,7, OOS −49,7): gece boşluğu.
   Hiçbir stop mesafesi açılış boşluğundan korumaz; stop emri gerçek bir
   koruma ama sınırı var ve bu sınır mesajlarda söylenmeli.
4. Hiçbir kolun kendi t değeri 2'ye yaklaşmıyor: bu sınav Donchian'a kenar
   kazandırmaz (`strateji-kenari-yok` geçerli).
