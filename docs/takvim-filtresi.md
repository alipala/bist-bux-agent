# Takvim filtresi — bilanço ve makro olay günlerinde giriş engeli

Başlangıç: 2026-09-24. Dal: `takvim-filtresi`.

## §0 ÖN KAYIT — sonuçlar görülmeden yazıldı

Bu bölüm backtest KOŞULMADAN önce yazıldı ve sonuçlar geldikten sonra
DEĞİŞTİRİLMEZ. Değiştirilmesi gerekirse değişiklik ayrı bir alt bölümde,
gerekçesiyle ve tarihle yazılır. Sebep bu deponun tekrar eden dersi:
ölçüt sonuçtan sonra seçilirse her sonuç "anlamlı" gösterilebilir
(bkz. `strateji-kenari-yok`: beşinci "bulgu" yanlış kontrol seçimiydi).

### Hipotez

Donchian 20/10 + 2N kuralının kayıplarının bir kısmı, stop'un
KORUYAMADIĞI boşluklardan (gap) geliyor: fiyat stop'un altında açılınca
çıkış stop'tan değil açılıştan olur. Bu boşlukların önemli kısmı TARİHİ
ÖNCEDEN BİLİNEN olaylarda oluşuyorsa, o olaylardan hemen önce yeni
pozisyon AÇMAMAK kuyruk kaybını küçültür.

**Bu bir kenar hipotezi DEĞİL, risk hipotezi.** Geçse bile Donchian'ın
rastgele girişe göre kenarı olmadığı sonucunu değiştirmez; yalnızca
"aynı kural daha az derin kaybeder" der. Otonom al-sat kararı bu
sınavdan ÇIKMAZ.

### Kollar (sabit, sonradan eklenmez)

| kol | engel | pencere |
|---|---|---|
| T | yok (taban, üretimdeki kural) | — |
| E1 | hissenin kendi bilançosunun TEPKİ GÜNÜ, girişten sonraki 1 işlem günü içinde | K=1 |
| E5 | aynı, 5 işlem günü içinde | K=5 |
| M1 | CPI, istihdam raporu (NFP) veya FOMC kararı, girişten sonraki 1 işlem günü içinde | K=1 |

Her engelli kol için KONTROL: aynı sembolde, AYNI SAYIDA, rastgele
seçilmiş günlerde giriş engeli. 40 tohum (sabit: 20260924 + i).
Kontrol şart çünkü "daha az işlem yapmak" TEK BAŞINA kaybı azaltır;
filtrenin katkısı ancak rastgele atlamadan fazlaysa vardır.

### Tepki günü

- Açıklama seans ÖNCESİ (ABD saatiyle 09:30'dan önce) → aynı gün.
- Açıklama seans SONRASI (16:00 ve sonrası) → sonraki işlem günü.
- Saat bilinmiyor ya da seans içi → İKİSİ de tepki günü sayılır
  (ihtiyatlı; hangisi olduğu bilinmiyorsa ikisi de risklidir).

Giriş, `d` gününün KAPANIŞINDAN yapılır. Dolayısıyla `d` gününün kendi
tepkisi girişten ÖNCE yaşanmıştır ve engel sebebi DEĞİLDİR; engel
yalnızca `(d, d+K]` aralığındaki tepki günleri için geçerlidir.

### Ölçütler

Birincil:
1. **Stop altı boşluk kaybı toplamı** — stop'la kapanan ve çıkışı
   stop'un altında olan işlemlerde `(stop − çıkış) / giriş` toplamı.
   Hipotezin doğrudan ölçtüğü şey bu.
2. **Beklenti** — işlem başına net getiri (maliyet %0,40 gidiş-dönüş).

İkincil (rapor edilir, karar vermez): p5 işlem getirisi, en kötü işlem,
işlem sayısı, aylık kümelenmiş t (`aylik_kumelenme.t_ay`).

### Karar kuralı

Bir kol GEÇER, ancak ve ancak:

- (a) Stop altı boşluk kaybı toplamı, 40 rastgele kontrolün HEPSİNDEN
  düşük (ampirik p ≤ 1/41 ≈ 0,024 — üç kol için Bonferroni'ye yakın
  düzeltme: 0,05/3 ≈ 0,017 değil, bu yüzden (b) de şart), VE
- (b) Beklentisi, rastgele kontrollerin MEDYANINDAN düşük değil
  (filtre, aynı sayıda rastgele günü atlamaktan daha pahalı olmamalı).

Geçmeyen kol üretime bağlanmaz. Geçen kol üretime bağlanabilir ama
bu karar kullanıcınındır; varsayılan KAPALI kalır.

### Veri ve bilinen sınırlar

- Evren: `ibkr.strateji.endeksler` (S&P 500 + Nasdaq 100, bugünkü
  üyelik). Hayatta kalma yanlılığı var ve giderilemez; iki kol da aynı
  evrende koştuğu için FARK büyük ölçüde sadeleşir (`backtest._evren`).
- Bilanço geçmişi: Yahoo (kademe 2). SEC 8-K Madde 2.02 (kademe 1)
  bu oturumda ağ filtresi yüzünden alınamadı. **Sonuç, SEC ile
  örtüşen dönemde tarih uyumu ≥ %95 ölçülene kadar GEÇİCİDİR.**
- Makro geçmiş: CPI ve NFP ALFRED'den (FRED arşivi) 2016+ tam; FOMC
  yalnızca Fed sayfasının kapsadığı 2021+ için var. M1 kolu 2021
  öncesinde yalnızca CPI+NFP ile koşar ve bu raporda belirtilir.
- ALFRED yayın günleri revizyon günlerini de içerebilir (ör. mevsimsellik
  katsayısı güncellemesi); yıllık sayım raporlanır.

## §1 Uygulama notları (sınavdan önce, sonucu etkilemeyen)

- **Duman koşusu beyanı.** Asıl sınavdan önce betik 10 sembollük, EKSİK
  veriyle (5 sembolde bilanço geçmişi henüz yoktu) bir kez koşuldu —
  yalnızca betiğin çalıştığını görmek için. §0 bu koşudan sonra
  DEĞİŞTİRİLMEDİ.
- **FRED/ALFRED User-Agent.** Tanımadığı UA'yı (Chrome taklidi da,
  `finagent/1.0` da) askıda bırakıyor; kütüphane varsayılanı 0,1-0,5
  saniyede 200 alıyor. İlk saha koşusu bu yüzden iki kaynağı `engelli`
  yazdı. Kod artık UA ayarlamıyor ve bunu bir test + mutasyon koruyor.
- **Ağ filtresi.** Oturum boyunca ağdaki Zyxel USG FLEX 200H `.gov`
  DNS'ini kendi sunucusuna yönlendirdi (sertifika
  `dnsft.cloud.zyxel.com`). SEC bu yüzden alınamadı. Gece koşuları
  (launchd) aynı gün SEC ve Fed'e ulaşmıştı; filtre aralıklı.
- **Kaynak uyumu (makro).** 2026'nın geçmiş aylarında FRED ileri
  takvimi ile ALFRED arşivi 46/46 tarihte birebir aynı; ALFRED'de fazla
  tarih yok. Yıllık 13 tarih görülen yıllar (CPI 2019-2024) revizyon
  günü içeriyor olabilir — M1 kolu bu yüzden en fazla birkaç fazla gün
  engelliyor, azını değil.

## §2 Sonuçlar — 2026-09-24

Evren 511 sembol (S&P 500 + Nasdaq 100, 300+ bar), pencere 2016-01-01 →
2026-09-24, maliyet %0,40 gidiş-dönüş, bilanço geçmişi Yahoo
(522 enstrüman, 44.296 satır, düşen 0).

### Etki ölçümü (betimsel) — normalize açılış boşluğu, N cinsinden

| olay | tepki günü medyan | diğer günler medyan | >1N boşluk (olay / diğer) |
|---|---|---|---|
| **şirketin bilançosu** | **0,943** | 0,174 | **%48,0 / %1,7** |
| CPI | 0,202 | 0,176 | %3,3 / %2,4 |
| NFP | 0,211 | 0,176 | %2,7 / %2,4 |
| FOMC | 0,200 | 0,176 | %3,5 / %2,4 |
| GDP | 0,174 | 0,177 | %2,9 / %2,4 |
| PCE | 0,176 | 0,177 | %2,4 / %2,4 |
| PPI | 0,174 | 0,177 | %2,5 / %2,4 |

Bilanço tepki günü tipik bir günün **5,4 katı** boşluk üretiyor ve
günlerin **yarısında** boşluk 1N'yi aşıyor (2N stop mesafesinin yarısı
tek açılışta). Makro yayınlar tek tek hisse açısından neredeyse
sıradan günler: CPI/NFP/FOMC medyanı %15-20 büyük, PCE/PPI/GDP fark yok.

### Kollar

| kol | işlem | stop altı toplam | kontrol min / medyan | sıra | beklenti | kontrol medyan | karar |
|---|---|---|---|---|---|---|---|
| T | 31.326 | %4.351,8 | — | — | %0,397 | — | taban |
| **E1** | 31.114 | **%4.081,6** | %4.275,9 / %4.340,5 | **1/41** | **%0,405** | %0,397 | **GEÇTİ** |
| E5 | 30.310 | %2.989,4 | %4.199,7 / %4.288,2 | 1/41 | %0,382 | %0,395 | geçmedi (b) |
| M1 | 30.420 | %4.301,8 | %4.124,4 / %4.237,4 | 34/41 | %0,396 | %0,389 | geçmedi (a) |

Tabanın aylık kümelenmiş t değeri **-0,52**: kural tek başına kenar
üretmiyor. Bu sınav o sonucu değiştirmedi ve değiştirmesi beklenmiyordu.

### Mekanizma (betimsel) — E1'in engellediği girişler neydi?

| | bilançodan 1 gün önce giriş | diğer girişler |
|---|---|---|
| işlem | 586 (%1,9) | 30.740 |
| ilk gün stop'la çıkış | **%25,3** | %1,9 |
| stop'un altından çıkış | **%16,2** | %4,7 |
| beklenti (net) | **-%0,02** | +%0,40 |

## §3 Yorum

1. **E1 gerçek ama dar bir risk kuralı.** Etki kontrolden ayırt
   ediliyor (40/40 rastgele kontrolün hepsinden iyi) ve mekanizması
   görünür: bilançodan bir gün önce açılan pozisyonun dörtte biri ilk
   gün stop'la kapanıyor. Ama işlemlerin yalnızca %1,9'una dokunuyor;
   işlem başına beklenti farkı ~0,01 puan. **Kenar yaratmıyor, bir kayıp
   sınıfını kesiyor.**
2. **E5 kuyruğu daha çok kesiyor ama pahalı.** Stop altı kaybı %31
   azaltıyor, fakat aynı sayıda rastgele günü atlamaktan daha düşük
   beklenti bırakıyor: bilançodan 2-5 gün önceki kırılımların bir kısmı
   iyi işlemler. Ön kayıttaki (b) şartı tam bunu yakalamak içindi.
3. **Makro takvim tek tek hisse için filtre değeri taşımıyor.** CPI,
   NFP, FOMC günlerindeki boşluk farkı küçük ve M1 kontrolden kötü
   (34/41). Bu, "FED ve CPI sığ takvimler" gözleminin ölçülmüş hali:
   hisse bazında asıl planlı belirsizlik şirketin kendi bilançosu.
4. **Otonom al-sat kararı değişmedi.** Donchian'ın rastgele girişe göre
   kenarı yok (`strateji-kenari-yok`); E1 onu daha az kötü yapar, kârlı
   yapmaz.

## §4 Açık maddeler

- **SEC doğrulaması (kademe 1).** Sonuç Yahoo'ya (kademe 2) dayanıyor
  ve §0 gereği **geçici**. `.gov` erişilebilir olduğunda 8-K Madde 2.02
  kabul zamanlarıyla örtüşen dönemde tarih uyumu ölçülmeli (≥ %95).
  Not: `acceptanceDateTime` alanının saat dilimi DOĞRULANMADI — Yahoo
  saatleriyle karşılaştırılarak belirlenmeli, varsayılmamalı.
- **Canlıya bağlama kullanıcının kararı.** E1 geçti; üretime bağlamak
  `pulse/strateji.py`'nin giriş kararına bilanço takvimini (AV + Yahoo,
  saati bilinmiyorsa iki gün) bağlamayı gerektirir. Varsayılan KAPALI
  bir ayarla yapılmalı. Henüz YAPILMADI.
- **Test edilmeyen varyant.** "Bilançodan önce ÇIK" (açık pozisyonu
  kapatmak) ayrı bir hipotez; bu sınavın verisiyle sonradan eklenirse
  ön kayıtsız olur. Denenecekse önce kendi ön kaydı yazılmalı.
- **BIST bilanço takvimi yok.** KAP'tan gelmeli; Donchian ABD evreninde
  çalıştığı için sınavı etkilemiyor, ama Midas portföyü için canlı
  uyarı kapsamı dışında (`kapsam_disi` alanı bunu söylüyor).
- **FOMC geçmişi 2021+.** Fed sayfası 2021 öncesini taşımıyor; M1 kolu
  2016-2020'de yalnızca CPI+NFP ile koştu.
