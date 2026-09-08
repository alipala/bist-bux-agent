# Panel kesilmesi — hakemin yarısı üretilmiyordu (8 Eylül 2026)

## Bulgu

1–8 Eylül arasında hakem **42 kez koştu, 22'si süre sınırında kesildi**
(%52). Kesilen koşuda:

- kullanıcıya giden model yorumu **hiç üretilmedi**,
- o koşunun çağrıları tahmin defterine **hiç girmedi**,
- `panel_runs.ham_metin` **sıfır uzunluktaydı** (22 satırın 22'sinde).

Şemanın kendi gerekçesi "atılan görüş kaybolmaz, ham metin duruyor"
diyordu; **kesilme durumunda bu doğru değildi.** 27 Ağustos'ta hiç hakem
satırı yok. 8 Eylül'de `yuksel` gün boyu hakem çıktısı almadı.

Hiçbir alarm çalmadı: kesilme her koşuda ERROR olarak loglanıyordu ama
loga bakan yoktu, karne de kayıp koşuları görmüyordu — sadece küçük bir
örneklem gösteriyordu. Kayıp rastgele değil: **uzun süren, yani daha çok
adayın olduğu koşuları** vuruyor.

## Kök sebep — üç kat, hepsi ölçüldü

**1. Bütçe bayatlamıştı.** `panel_butce_sn: 900` değeri 20 Ağustos'ta
"sahip başına 282 sn" ölçümünden türetilmişti. 55 koşuda yeniden ölçüldü
(`panel_runs` damgaları, ajan satırı → hakem satırı farkı):

| faz | medyan | p90 | azami |
|---|---|---|---|
| ajan fazı (4 ajan paralel) | 234 s | 289 s | 319 s |
| hakem | 185 s | 222 s | 257 s |
| toplam | 410 s | 455 s | 516 s |

900 / 2 sahip = **450 s/sahip** — p90'ın (455) altında. Panel yapısal
olarak kesiliyordu.

**2. Kabuk bütçesi paneli daha da kısıyordu.** Toplama uzayınca
`_panel_butcesi` üçüncü sınırı (kabuğun öldürme anı) uyguluyor: 8 Eylül
sabahı 892 → 776, öğle 898 → 873 → ikinci sahibe 437.

**3. Hakemin payı oran'dı, rezervasyon değil.** `AJAN_PAYI = 0.70`
ajanlara bütçenin %70'ini veriyor, hakem **artanı** alıyordu:
437 × 0,3 ≈ **131 s**, ihtiyaç 257 s. Ajanlar paylarını sonuna kadar
kullandığında hakem garantili kesiliyordu.

## Düzeltme

**A. Kısmi çıktı artık kurtarılıyor.** `parcalar` listesi coroutine'in
yereliydi; `CancelScope` iptali onu da öldürüyordu. Liste artık
**çağırana ait** (`tampon` parametresi) ve iptalden sağ çıkıyor. Kesilme
anında metin `panel_runs`'a yazılıyor; JSON bloğu tamamlanmışsa görüşler
de kurtarılıp deftere giriyor. `_json_cek` yarım bloğu zaten reddettiği
için uydurma riski yok (test bunu koruyor).

**B. Hakem payı rezervasyon.** `HAKEM_ASGARI_SN = 260` (ölçülen azami
257) taban; `HAKEM_AZAMI_PAY = 0.60` tavan. Ajan son tarihi hakemden
geriye hesaplanıyor. Büyük bütçede davranış değişmiyor (1800 → 540 s,
eskisiyle aynı); dar bütçede **ajanlar kısılıyor, hakem değil** — bir
ajanın düşmesi beş girdiden birini, hakemin düşmesi tüm çıktıyı ve tüm
defter satırlarını götürüyor.

**C. Bütçeler ölçümden yeniden türetildi.**

| kip | panel (eski→yeni) | kabuk (eski→yeni) | sahip payı |
|---|---|---|---|
| sabah | 900 → 1200 | 1500 → 2100 | 600 s |
| öğle | 900 → 1200 | 1200 → 1800 | 600 s |
| kapanış | 900 → 1200 | 2400 → 3000 | 600 s |
| nabız | 1800 (aynı) | 4200 → 4800 | 900 s |

Kabuk bütçeleri canlı logdan ölçülen toplama süreleriyle türetildi
(sabah 604 s, öğle 207 s, kapanış 1549 s, nabız 2637 s) + panel + 120 s
teslimat payı.

**D. Kayıp artık görünür.** İki ayrı kanal:
- `json_durum='kesildi'` — "model sustu" ile "biz kestik" ayrı durumlar;
  ikisi `bos` kovasında birleşince 22 kesilme model sessizliği gibi
  okunuyordu.
- Bekçi 8. ölçütü `panel_kesiliyor()` — son 12 koşunun üçte birinden
  fazlası kesikse bildirim (en az 6 koşu; örneklem yetersizken oran
  uydurulmaz).
- `Defter.karne()` artık `kosu_kapsami` döndürüyor: kaç koşu üretti, kaçı
  kesildi, kayıp yüzdesi. **Sıfır ölçümde de** beyan ediliyor — "0 ölçüm"
  ile "hepsi kesildi" ayrı şeyler.

## Doğrulama

- 8 yeni duman testi (rezervasyon aritmetiği, bütçe ↔ ölçüm bağı, tampon
  hayatta kalması, `kesildi` durumu, yarım JSON reddi, bekçi eşiği ve
  küçük örneklem sessizliği, karne kapsamı).
- Canlı veride: karne `22/60 koşu, %36,7 kayıp` diyor; bekçi %58 ile
  alarm veriyor. İkisi de düzeltmeden önce sessizdi.
- `test_panel_butceleri_olculen_ihtiyaci_karsiliyor` bütçenin sessizce
  küçültülmesini engelliyor — asıl kusur tam olarak buydu.
