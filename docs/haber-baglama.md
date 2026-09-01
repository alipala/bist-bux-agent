# Haber bağlama — fizibilite ve tasarım

**Durum: KURULMADI.** Bu belge ölçümü ve tasarımı taşıyor; kod yok.
Ölçümler 2026-09-01'de bu makinede yapıldı, hepsi tekrar üretilebilir.

Soru şu: gün içi bir hareket görüldüğünde, o hareketin *nedenini* haber
ve KAP bildirimiyle ilişkilendirebilir miyiz?

`endeks_karsilastir` bu sorunun **önündeki** soruyu zaten cevaplıyor
("hisseye mi özgü, piyasa geneli mi") ve kendi docstring'inde şunu
söylüyor:

> "X yüzünden düştü" cümlesi çoğu zaman sonradan kurulmuş bir hikâyedir.

Bu katmanı naif kurarsak **tam o hikâyeyi üreten makineyi** yazmış
oluruz. Belgenin çoğu bunu engellemekle ilgili.

---

## 1. Ölçüldü, varsayılmadı

### 1.1 Sinyal gerçek — KAP bildirimi büyük hareketin önünde geliyor

14 günün 7.416 BIST saatlik barı, önündeki 0-1 saatte KAP bildirimi
olup olmamasına göre ayrıldı:

```
bildirim VAR:  n=  68   medyan |hareket| %0,710   ortalama %1,274
bildirim YOK:  n=7348   medyan |hareket| %0,384   ortalama %0,758

medyan farkı +0,325 puan
permütasyon testi (2000 karıştırma):  p = 0,0005   ANLAMLI
```

**Etki pencere uzadıkça sönüyor** — bu önemli, çünkü uydurma bir
korelasyonun değil gerçek bir nedenselliğin imzası:

| pencere | bildirimli medyan | bildirimsiz | oran |
|---|---:|---:|---:|
| 1 saat | %0,710 | %0,384 | **1,85x** |
| 2 saat | %0,635 | %0,385 | 1,65x |
| 4 saat | %0,604 | %0,385 | 1,57x |

Yani pencere **1-2 saat**, 24 değil. Bir sabit seçilecekse ölçüm bunu
söylüyor.

### 1.2 Ama yön tahmin etmiyor

```
bildirimli barlarda:  yukarı 31,  aşağı 25
```

**KAP hareketin BÜYÜKLÜĞÜNÜ açıklar, NEREYE gideceğini değil.** Katman
bunu beyan etmezse model "KAP geldi, yukarı gider" der — ve bu ölçümle
çelişir.

### 1.3 Kapsam yeterli

Son 7 gün:

```
news:          1.809 `sirket` haberi, 1.764'ü sembollü
               111 farklı sembol, 109'u enstrümanlarla eşleşiyor
               (BUX 63, BIST 42, BINANCE 4)
disclosures:   281 sembol, 233'ü BIST enstrümanıyla eşli
damgalar:      news 8.288/8.290 saatli · disclosures 2.156/2.156
```

KAP gün içinde de akıyor (UTC): 07:00'de 214, 08:00'de 141, kapanış
civarında (15:00) 383. Yani seans içi bildirim gerçek ve bol.

### 1.4 Taktiklerin kaçında kaynak var

| | aynı gün haber | aynı gün KAP | ±1 gün, ikisinden biri |
|---|---:|---:|---:|
| BIST (57 taktik) | %28 | %25 | **%53** |
| BUX (37 taktik) | %57 | — | **%68** |

**"Yok" dalı vakaların yaklaşık yarısında çalışacak.** Bu deponun en
kötü hata sınıfı tam orada (`[[yanlis-yok-beyani]]`): cümle
**"akışımda yok"** olmalı, "haber yok" **değil**. `haberler` aracı bu
ayrımı zaten yapıyor; sözü oradan alınacak.

---

## 2. Ne ölçülMEDİ — ve neden Faz 0 pazarlık konusu değil

**ABD/BUX tarafı için hiçbir şey ölçülmedi.** §1.1'in tamamı BIST + KAP.
Elde ABD için yalnızca `news` var (KAP Borsa İstanbul'a özgü) ve haber
akışının bir bildirimle aynı önceliğe sahip olduğu **varsayım**.

Ölçmeden bağlamak, bu oturumda altı kez çürütülen türden bir varsayım
olur. Faz 0 aynı yöntemi ABD için tekrarlar; etki yoksa katman
**BIST-only** çıkar ve bunu söyler.

---

## 3. Tasarımın çekirdeği: üç iddia, üç ayrı kanıt eşiği

| İddia | Gereken kanıt | Yapabilir miyiz |
|---|---|---|
| "Bu sembolde bugün şu var" | varlık + damga | **evet, ölçülebilir** |
| "Bu, hareketten **önce** geldi" | `kaynak_ts < bar_ts` | **evet, ölçülebilir** |
| "Hareket **bunun yüzünden**" | nedensellik | **hayır** |
| "O yüzden **yukarı** gider" | yön | **hayır — ölçüldü, yok** |

Modül ilk ikisini üretir, son ikisini **üretmez**. Ve bu ayrım prompt'ta
değil **alan adlarında** yaşamalı — prompt'a bırakılan her ayrım zamanla
aşınıyor.

### 3.1 `once` / `sonra` ayrımı özelliğin kendisidir

Hareketten **sonra** yayımlanan bir başlık o hareketi açıklayamaz.
İkisini aynı listede vermek, tam olarak "sonradan hikâye kurmak"tır.
Çoğu naif uygulama bunları karıştırır; ayırmak tek yapısal savunma.

### 3.2 Demirleme BAR'a, taktiğe değil

`predictions.olusma_ts` bir **tarih** (şema 29 öncesi 1438/1438 satır
10 karakter); `yayim_ts` yeni ve eski satırlarda NULL. Adayın `bar_ts`i
ise dakika taşıyor.

Öncelik/sonralık ancak bar damgasıyla anlamlı. Taktiğe demirlemek
"önce/sonra" sorusunu ölçülemez hale getirir.

### 3.3 KAP ile haber aynı şey değil

KAP, şirketin **kendi** dosyaladığı bildirimdir — kademe 1. Bir
toplayıcı başlığı kademe 3-4. `news.tier` bu bilgiyi zaten taşıyor;
çıktı da taşımalı.

### 3.4 `endeks_karsilastir` ile ilişki: etiketler, kapı tutmaz

Piyasa geneli bir düşüşte şirket başlığı gürültüdür. Ama veriyi
**gizlemek** bu deponun tekrar tekrar yakalandığı hata. Aday satırı
`hareket_turu: hisseye_ozgu | piyasa_geneli | kiyaslanamadi` etiketi
taşır; hard-gate yok.

---

## 4. Modül sözleşmesi

`src/finagent/pulse/haber_baglam.py` — `gun_sonu.py`'nin kardeşi.

```python
# OLCULDU, SECILMEDI (2026-09-01, BIST 7.416 saatlik bar):
#   0-1s once KAP VAR -> medyan |hareket| %0,710 (n=68)
#              YOK    -> %0,384 (n=7348)   permutasyon p=0,0005
#   Etki pencere uzadikca soner: 1s 1,85x · 2s 1,65x · 4s 1,57x
PENCERE_SAAT = 2

# OLCULDU: bildirimli barlarda yukari 31, asagi 25.
# KAP hareketin BUYUKLUGUNU aciklar, YONUNU DEGIL.
YON_TAHMIN_ETMEZ = True

def baglam(db, instrument_id, bar_ts, *, pencere_saat=PENCERE_SAAT) -> dict:
    """
    {
      "pencere_saat": 2,
      "once":  [...],   # bar_ts'ten ONCE  — aciklayici OLABILIR
      "sonra": [...],   # bar_ts'ten SONRA — aciklayamaz, AYRI KOVA
      "kaynak_yok": None | "akisimda yok (kapsamda, cekim denendi)",
      "ZORUNLU": "Yon cikarma. Olculdu: bildirim yonu tahmin etmiyor.",
    }
    """
```

### 4.1 Tek okuma kapısı

`Database.kaynak_penceresi(symbol, bas_ts, bit_ts, limit)` — `news` +
`disclosures`, tek sorgu, kademe etiketli. **`haberler` aracı da buna
bağlanacak.**

Sorguyu kopyalamak `[[ayni-kural-iki-kopya]]` dersini tekrar etmek
olurdu: LLY `prices`'ta düzeltilmişti, `identity`'deki ikizi SEC
dosyalamalarını sessizce düşürmeye devam etti.

---

## 5. Nereye bağlanıyor — iki kanal, biri modelsiz

En güçlü koruma prompt değil, **mesajın kendisi**:

```
🎯 GUN ICI TAKTIK
KBORU  20,08  -%3,2  (XU100 -%0,4 · göreli -%2,8)
   📄 KAP 11:23 · Pay Alım Satım Bildirimi — hareketten 37 dk önce
```

Bu satır **deterministik**: kaynak, damga, öncelik süresi. Model yok,
yorum yok. Aynı veri modele de gider ki çelişmesin — ama modelin
katkısı olmadan da satır doğru.

---

## 6. Fazlar

| Faz | İş | Neden bu sırada |
|---|---|---|
| **0** | ABD/BUX için §1.1'i tekrarla | Sinyal yalnızca BIST+KAP için kanıtlı. **Atlanamaz.** |
| **1** | `kaynak_penceresi()` + `haberler` aracını ona bağla | Davranış değişmemeli; çıktı öncesi/sonrası birebir aynı |
| **2** | `haber_baglam.py` + testler + mutasyon | |
| **3** | `adaylar()` ve `_taktik_metni`'ne bağla | Token deltası `bot/olcum.py` ile ölçülecek — panel bütçesi gerçek bir kısıt |

---

## 7. Testler

```
test_haber_baglam_ONCE_ve_SONRA_ayri_kovada
test_haber_baglam_SONRAKI_kaynak_ONCE_kovasina_SIZMIYOR      <- cekirdek
test_haber_baglam_YON_TAHMIN_ETMEDIGINI_beyan_ediyor
test_haber_baglam_BOS_donus_AKISIMDA_YOK_diyor
test_haber_baglam_KAP_kademesi_haberden_AYRI
test_haber_baglam_TEK_OKUMA_KAPISI                           (kaynak duzeyi)
test_haber_baglam_PENCERE_OLCULDU_secilmedi
test_haber_baglam_KABLO_KACISI_yok                           (AST)
scripts/mutasyon_haber_baglam.py                             (~10 mutasyon)
```

---

## 8. Yapmayı önermediklerim

**Nedensellik iddiası.** Ölçülemez. "Bu haber yüzünden düştü" cümlesi
üretilmeyecek; üretilecek olan "bu kaynak hareketten 37 dk önce geldi".

**Yön çıkarımı.** Ölçüldü, yok (31/25). Bir gün ölçüm tersini
söylerse o zaman konuşulur.

**24 saatlik pencere.** Ölçüm 1-2 saat diyor. Geniş pencere hem etkiyi
seyreltir hem alakasız başlıkları içeri alır.

**Model geneli bir "neden" özeti.** Katmanın işi kaynağı **göstermek**;
yorumu modele bırakmak ayrı bir karar ve ayrı bir riski var.

**Haber yokken susmak.** Vakaların ~%47'sinde kaynak yok ve bu
söylenmeli — sessizlik "haber yok" diye okunur.

---

## 9. Ölçümler nasıl tekrar üretilir

Aşağıdaki iki sorgu/betik belgenin bütün sayılarını üretir.

**§1.1 ve §1.2 — bildirim önündeki hareket:**

```python
# BIST saatlik barlari, onundeki 0-1 saatte KAP bildirimi olup olmamasina
# gore ayir; medyanlari ve permutasyon p-degerini hesapla.
barlar = c.execute("""
    SELECT i.symbol s, h.ts, h.open o, h.close cl
    FROM prices_hourly h JOIN instruments i ON i.id=h.instrument_id
    WHERE i.venue='BIST' AND h.ts >= date('now','-14 days')
      AND h.open>0 AND h.close>0""").fetchall()
bildirim = {(r["s"], str(r["p"])[:13]) for r in c.execute(
    """SELECT symbol s, published_at p FROM disclosures
       WHERE published_at >= date('now','-15 days') AND symbol IS NOT NULL""")}
# bar icin: any((sembol, saat-k) in bildirim for k in range(0, pencere+1))
```

**§1.4 — taktiklerin kaçında kaynak var:**

```sql
WITH t AS (
  SELECT p.olusma_ts g, i.symbol s, i.venue v
  FROM predictions p JOIN instruments i ON i.id=p.instrument_id
  WHERE p.taktik_tur IN ('alim','koruma')
    AND p.olusma_ts >= date('now','-14 days'))
SELECT t.v, COUNT(*),
  SUM(EXISTS(SELECT 1 FROM news n WHERE substr(n.published_at,1,10)=t.g
             AND (','||n.symbols||',') LIKE '%,'||t.s||',%')),
  SUM(EXISTS(SELECT 1 FROM disclosures d WHERE substr(d.published_at,1,10)=t.g
             AND d.symbol=t.s))
FROM t GROUP BY t.v;
```
