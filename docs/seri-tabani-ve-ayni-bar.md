# Seri tabanı, aynı bar ve dosya tanıtıcı sızıntısı — 8 Eylül 2026

Tahmin defterinin "ne dedi, ne oldu" sorusuna verdiği cevabı bozan üç
kusur aynı gün ölçüldü ve kapatıldı. Üçü de "veri VARKEN yanlış ölçmek"
sınıfından; hiçbiri LLM kaynaklı değil.

## 1. Kaynak geçmişi yeniden tabanlıyor, biz 5 günlük pencereyle bakıyoruz

**Ne ölçüldü.** BLCYT 1 Eylül'de 10:1 bölündü. Yahoo da İş Yatırım da
geçmişi geriye dönük düzeltiyor (25 Ağu kapanışı artık 2,11). Tazeleme
penceresi 5 gün olduğu için yalnızca 26 Ağu–1 Eyl barları yeniden
yazıldı; öncesi eski tabanda kaldı. Tek kaynakta iki taban:

    yahoo_bist  2026-08-25  21.10  |  2026-08-26  2.132

Taktik #455 (22,60 → 2,25) **−%90 / anormal −89** diye puanlandı; taktik
karnesinin ortalama anormalinin yarısından fazlası bu tek satırdı ve fren
girdisine öyle girdi. 400 günlük pencerede `yahoo_bist`'te 13,
`isyatirim`'de 5 sembol aynı durumdaydı (AKFIS, SDTTR, ORGE, CVKMD…).

**Kalıcı düzeltme.**
- `analysis/tutarlilik.py` — sıçrama ölçütü TEK yerde (ardışık bar oranı
  ≥ 1,5 ya da ≤ 1/1,5; bölme `(enstruman, kaynak, para birimi)`).
- `isyatirim` ve `bistgecmis` sıçramalı sembolü **tam geçmişle** yeniden
  çekiyor (kendini onarma). `bistgecmis` ayrıca tazeleme aralığını boşluğa
  göre seçiyor (`tazeleme_periyodu`): evrenden düşüp geri gelen sembolde
  "5d" boşluğu sonsuza kadar bırakıyordu.
- `journal.puanla` sıçramanın üstünden ölçüm yapmıyor: satır bekler
  (`olcum_ts` NULL), sebep `olcum_notu`'ya yazılır, seri onarılınca
  ölçülür. Seri tek tabana çekilmiş ama satırın `baslangic_fiyat`ı eski
  tabandaysa katsayı serinin tabanına uzlaştırılır — giriş ve stop
  seviyeleri de aynı katsayıyla (aksi halde tetik kapısı eski tabandaki
  seviyeyi yeni tabanda arar).
- Şema 30: `predictions.olcum_notu`.
- Tek seferlik: `scripts/bolunme_onar.py` (kuru koşu varsayılan).

**Bilerek yapılmayan.** %20 bedelsiz (x1,2) eşiğin altında kalır; onu
yakalayacak eşik BIST'in sıradan sert günlerini de yakalar. `midas` günlük
barı kurumsal işlemi 4–6 gün gecikmeli yansıtıyor (BLCYT midas'ta 1 Eyl,
piyasada 26 Ağu); sığ olduğu için `fiyat_kaynagi` zaten eliyor. Tez
alarmı koşulları (`close < 83.035`) eski tabanda kalabilir — ayrı iş.

## 2. Evren günlük hacimle dalgalanıyor, defterin beklediği seri dalgalanamaz

**Ne ölçüldü.** `isyatirim._semboller()` likidite eşiğini SON GÜNÜN
hacminden alıyor. ARZUM 18 Ağu'da 178M TL ile evrene girdi, panel tahmin
yazdı, hacim 16M'ye düşünce evrenden çıktı; serisi 21 Ağu'da durdu.
`fiyat_kaynagi` sığ `midas` serisini eliyor (doğru), derin seri ölü.
97 BIST kâğıdı bu durumdaydı; 69 tahmin puanlanamıyordu; karne küçük
kâğıtları sessizce dışarıda bırakıyordu.

**Kalıcı düzeltme.** Açık tahmini (`olcum_ts IS NULL`) olan BIST kâğıdı
evrende kalır ve bütçe önceliğinde (`_kapsam()`) — `bistgecmis` aynı
listeyi kullandığı için iki kaynakta da.

## 3. Strateji motoru aynı barı ikinci kez yazıyordu

**Ne ölçüldü.** 3 Eylül gecesi toplama süreci `midas`'ta
`OSError: [Errno 24] Too many open files` ile öldü (launchd soft limit
256); `prices` ve `strateji_fiyat` hiç çalışmadı, nabız DÜNKÜ barla
tarama yaptı. 7 Eylül ABD tatiliydi. İki gece de REGN/WFC ve F/VST aynı
giriş fiyatıyla ikinci kez yazıldı ve seçildi (14 seçilen satırın 4'ü
tekrar). `_pozisyonda` giriş barını "bugünün kırılımı" saydığı için
(doğru kural) yakalayamazdı.

**Sızıntının kaynağı ölçüldü:** `yf.download(threads=True)` her toplu
çağrıda ~40 tanıtıcı bırakıyor (4 → 59 → 75); `threads=False` sabit ~21.

**Kalıcı düzeltme.**
- `bistgecmis` ve `saatlik`: `threads=False` (test kaynakta korur).
- `sistem.dosya_siniri_yukselt()` — `run.py` girişinde soft limit 4096
  (ikinci kemer).
- `pipeline.collect`: bir collector kabuk dışında patlarsa zincir devam
  eder, boşluk `collector_runs`'a yazılır.
- Şema 30: `predictions.bar_ts`; `strateji.ayni_bar_suzgeci` aynı
  (sahip, sembol, bar) üçlüsünü seçimden ve LLM'den önce eler; mesaj
  "N kırılım dünkü barla AYNI" der ve `strateji_fiyat`'ı işaret eder.

## Doğrulama

- 13 yeni duman testi (`tutarlilik`, `puanla` üç senaryo, `ayni_bar`,
  mesaj, evren, tazeleme, threads, pipeline, rlimit, şema).
- Canlıda: `scripts/bolunme_onar.py` çıktısı (önce/sonra sıçrama listesi
  ve #455'in yeni ölçümü) commit mesajında.
