# Haber bağlama — fizibilite ve tasarım

**Durum: KURULMADI.** Bu belge ölçümü ve tasarımı taşıyor; kod yok.
Ölçümler 2026-09-01'de bu makinede yapıldı, hepsi tekrar üretilebilir.

> ## ⚠️ ÖNCE §0'I OKUYUN
>
> Bu belgenin ilk sürümü **yanlış yönü ölçüyordu** ve özelliği
> olduğundan değerli gösteriyordu. §0 ters koşullu ölçümü taşıyor ve
> tasarımı **dar sürüme** çekiyor. §3-§8 hâlâ geçerli ama kapsam §0'ın
> çizdiği sınırla okunmalı.

---

## 0. Düzeltme — belge yanlış yönü ölçmüştü

### 0.1 Hangi soru sorulmalıydı

§1.1 şunu ölçüyor: **bildirim varsa hareket büyük mü?** Cevap evet,
p = 0,0005. Ama kullanıcının yaşadığı durum bu değil. Kullanıcı bir
hareket görüyor ve soruyor: **kaynak bulabilecek miyim?**

Bu ters koşullu olasılık ve ilk sürümde hiç ölçülmemişti. Ölçüldü:

| pencere | tüm barlarda taban | en büyük %10 harekette | kazanç |
|---|---:|---:|---:|
| **0-2 saat** (savunulabilir atıf) | %2,9 | **%4,9** | 1,7x — ama +2 puan |
| 0-6 saat | %6,0 | %7,8 | 1,3x |
| **0-24 saat** (zayıf atıf) | %18,5 | **%20,8** | **1,1x — neredeyse hiç** |

Yalnız KAP ile bakıldığında daha da düşük: en büyük %10 harekette
%3,3, en büyük %1'de %1,3 — yani **en şiddetli hareketlerde KAP hiç
yok**, taban oranın seviyesinde.

### 0.2 İki sonuç, ikisi de kötü

**Atıfın savunulabilir olduğu yerde kapsam yok.** 2 saatlik pencerede
büyük hareketlerin ancak %5'inde kaynak var. 20 taktikten 19'unda
"akışımda yok" yazılacak.

**Kapsamın olduğu yerde atıf savunulamaz.** 24 saatte %21 bulunuyor —
ama rastgele bir barda da %18,5. **1,1x.** Yani geniş pencerede bulunan
başlık, o hareketle ilgili olmaktan çok **tesadüf**.

Bu tam olarak bu belgenin girişinde uyardığı şey. Belge tuzağa karşı
tasarım savunmaları yazmış ama **tuzağın büyüklüğünü ölçmemişti**.
Ölçünce görünen: geniş pencerede özellik esas olarak hikâye üretir.

### 0.3 Kapsam DARALTILDI

Ölçüm daha iyi bir özellik tarif ediyor — belgede yazan değil:

> Her adaya bağlam **ekleme**. Yalnızca **dar pencerede (0-2 saat)
> kaynak VARSA** bir satır ekle; yoksa **sus**.

Bu, §8'in "haber yokken susmak" maddesini **tersine çeviriyor** ve
ölçüm bunu haklı çıkarıyor:

* %5'lik vakada satır gerçekten bilgi taşıyor
* %95'te hiçbir maliyet yok — ne token, ne gürültü, ne "yok" beyanı riski
* `sonra` kovasına, `adaylar()` zenginleştirmesine ve **Faz 3'e gerek
  kalmıyor**

Kullanıcının somut kazancı: birkaç günde bir, bir taktik mesajında
fazladan bir satır — "bu düşüş gürültü değil, arkasında şu var". Yön
bilgisi vermiyor (§1.2), yalnızca hareketin tesadüfi olup olmadığını
söylüyor.

**İş, bu belgede tarif edilenin yaklaşık üçte biri.**

### 0.4 Geniş sürüm YAPILMAMALI

Her adaya bağlam eklemek, 24 saatlik pencerede %1,1'lik bir kazançla
hikâye üretmek demektir. Bakım maliyeti gerçek, getirisi ince, ve
`haberler` aracı sorulduğunda bunu zaten yapıyor.

### 0.5 Öncelik

Aynı emekle daha yüksek getirili işler ölçüldü ve **önce onlar
yapıldı**: piyasa saati kontrolü (bir hata sınıfını kapattı, commit
`f62b9be`) ve mesaj+link çakışmasının ölçümü (`8aaa034`). Haber
bağlama sırada üçüncü.

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

### 3.1 Hareketten sonrakiler HİÇ toplanmıyor

Hareketten **sonra** yayımlanan bir başlık o hareketi açıklayamaz.
İkisini aynı listede vermek, tam olarak "sonradan hikâye kurmak"tır.

İlk sürüm bunu iki kovaya (`once` / `sonra`) ayırmayı öneriyordu. Dar
sürümde (§0.3) **`sonra` kovası tamamen kalktı**: sorgu yalnızca
`bar_ts`ten öncekileri çekiyor. Karışma riski kodda değil sorguda
çözülüyor — toplanmayan veri sızamaz.

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

**DAR SÜRÜM** (§0.3). Sözleşme, boş dönüşü bir *sonuç* değil
**sessizlik** yapıyor.

```python
# OLCULDU, SECILMEDI (2026-09-01, BIST 7.416 saatlik bar):
#   0-1s once KAP VAR -> medyan |hareket| %0,710 (n=68)
#              YOK    -> %0,384 (n=7348)   permutasyon p=0,0005
#   Etki pencere uzadikca soner: 1s 1,85x · 2s 1,65x · 4s 1,57x
#
# PENCERE GENISLETILEMEZ. 24 saatte kapsam %21'e cikiyor ama TABAN da
# %18,5 — kazanc 1,1x, yani bulunan kaynak TESADUF. Genis pencere bu
# modulu bir hikaye ureticisine cevirir (bkz. §0).
PENCERE_SAAT = 2

# OLCULDU: bildirimli barlarda yukari 31, asagi 25.
# KAP hareketin BUYUKLUGUNU aciklar, YONUNU DEGIL.
YON_TAHMIN_ETMEZ = True

def baglam(db, instrument_id, bar_ts, *, pencere_saat=PENCERE_SAAT) -> dict | None:
    """
    Dar pencerede kaynak VARSA sozluk, YOKSA None.

    None = "soylenecek sey yok" ve cagiran SUSAR. Olculdu: buyuk
    hareketlerin ancak %5'inde kaynak var, yani None NORMAL DURUM.
    Bos bir sozluk dondurup her mesaja "akisimda yok" satiri koymak,
    20 mesajin 19'una gurultu eklemek olurdu.

    {
      "pencere_saat": 2,
      "kaynak": [...],     # bar_ts'ten ONCE yayimlananlar, kademe etiketli
      "onculuk_dk": 37,    # en yakin kaynak hareketten kac dk once
      "ZORUNLU": "Yon cikarma. Olculdu: bildirim yonu tahmin etmiyor.",
    }
    """
```

`sonra` kovası **kalktı**: dar sürümde modül yalnızca öncekileri
döndürüyor, sonrakiler hiç toplanmıyor. Karışma riski kodda değil
sorguda çözülüyor.

### 4.1 Tek okuma kapısı

`Database.kaynak_penceresi(symbol, bas_ts, bit_ts, limit)` — `news` +
`disclosures`, tek sorgu, kademe etiketli. **`haberler` aracı da buna
bağlanacak.**

Sorguyu kopyalamak `[[ayni-kural-iki-kopya]]` dersini tekrar etmek
olurdu: LLY `prices`'ta düzeltilmişti, `identity`'deki ikizi SEC
dosyalamalarını sessizce düşürmeye devam etti.

---

## 5. Nereye bağlanıyor — TEK kanal, modelsiz

İlk sürüm iki kanal öneriyordu (mesaj satırı + modele bağlam). Dar
sürümde **model prompt'una hiçbir şey eklenmiyor**: satır doğrudan
`_taktik_metni`'ne giriyor.

```
🎯 GUN ICI TAKTIK
KBORU  20,08  -%3,2  (XU100 -%0,4 · göreli -%2,8)
   📄 KAP 11:23 · Pay Alım Satım Bildirimi — hareketten 37 dk önce
```

Bu satır **deterministik**: kaynak, damga, öncelik süresi. Model yok,
yorum yok, token maliyeti yok.

Modele vermemenin gerekçesi ölçümde: vakaların %95'inde kaynak
olmadığı için prompt'a eklenecek şey çoğunlukla boşluk olurdu — dört
ajanın bağlamına, hiçbir şey söylemeyen bir alan. Kullanıcı ayrıntı
isterse `haberler` aracı zaten var ve orada **açıkça sormuş** oluyor.

---

## 6. Fazlar

**DAR SÜRÜM — üç faz, dördüncüsü iptal** (§0.3).

| Faz | İş | Neden bu sırada |
|---|---|---|
| **0** | ABD/BUX için §1.1 **ve §0.1'i** tekrarla | Sinyal yalnızca BIST+KAP için kanıtlı, ve orada bile ters koşullu zayıf. **Atlanamaz.** |
| **1** | `kaynak_penceresi()` + `haberler` aracını ona bağla | Davranış değişmemeli; çıktı öncesi/sonrası birebir aynı |
| **2** | `haber_baglam.py` + testler + mutasyon | Dar sözleşme: kaynak yoksa `None` |
| ~~3~~ | ~~`adaylar()` zenginleştirmesi~~ | **İPTAL.** Dar sürümde model prompt'una hiçbir şey eklenmiyor; satır deterministik olarak `_taktik_metni`'ne giriyor. Token maliyeti sıfır. |

**Faz 0'ın kabul ölçütü sertleşti.** Yalnızca "etki var mı" değil,
**ters koşullu kapsam** da ölçülecek: ABD'de büyük hareketlerin kaçında
2 saatlik pencerede kaynak var? BIST'te bu %5. ABD'de de bu
seviyedeyse özellik yine dar kalır; belirgin biçimde düşükse
**ABD tarafı hiç bağlanmaz** ve bu söylenir.

---

## 7. Testler

```
test_haber_baglam_SONRAKI_kaynak_HIC_TOPLANMIYOR             <- cekirdek
test_haber_baglam_YON_TAHMIN_ETMEDIGINI_beyan_ediyor
test_haber_baglam_KAYNAK_YOKSA_None_donuyor_ve_mesaj_SUSUYOR
test_haber_baglam_KAP_kademesi_haberden_AYRI
test_haber_baglam_TEK_OKUMA_KAPISI                           (kaynak duzeyi)
test_haber_baglam_PENCERE_OLCULDU_ve_GENISLETILEMIYOR
test_haber_baglam_KABLO_KACISI_yok                           (AST)
scripts/mutasyon_haber_baglam.py                             (~8 mutasyon)
```

En kritik iki mutasyon şunlar olacak: **pencere 24 saate genişletiliyor**
(kazanç 1,1x'e düşer, hikâye üreticisi olur) ve **kaynak yokken boş
sözlük dönüyor** (20 mesajın 19'una "akışımda yok" gürültüsü girer).

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

~~**Haber yokken susmak.**~~ **BU MADDE İPTAL EDİLDİ (§0.3).** İlk
sürümde "vakaların ~%47'sinde kaynak yok ve bu söylenmeli" yazıyordu.
Ters koşullu ölçüm bunu tersine çevirdi: dar pencerede kaynak
bulunamama oranı **%95**. Her mesaja "akışımda yok" yazmak, 20 mesajın
19'una gürültü eklemek olurdu. Dar sürümde modül **susuyor**.

Not: "akışımda yok" ile "haber yok" ayrımı hâlâ geçerli — ama o ayrım
`haberler` aracının işi, çünkü orada kullanıcı **açıkça sormuş**
oluyor. Sorulmadan verilen sessizlik bir "yok" beyanı değildir.

---

## 9. Ölçümler nasıl tekrar üretilir

Aşağıdaki sorgu/betikler belgenin bütün sayılarını üretir.

**Küçük kayma beklenir ve hata değildir.** Sorgular `date('now','-14
days')` ile *kayan* bir pencere kullanıyor; birkaç saat sonra yeniden
koşturulduğunda taban %2,9 yerine %3,0 çıkabilir. Aynı gün içinde
yapılan bağımsız iki koşumda §0.1 tablosu 1,65x / 1,32x / **1,12x**
verdi — **sonuç değişmiyor**, ve kararı belirleyen şey son hanedeki
rakam değil bu oranların büyüklük mertebesi.

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

**§0.1 — TERS KOŞULLU: büyük hareketin önünde kaynak var mı?**
Belgenin en önemli sayısı bu ve ilk sürümde eksikti.

```python
# Barlari |hareket| buyuklugune gore sirala; her dilimde onundeki
# pencerede KAP ya da haber olan barlarin oranini say. Tum barlardaki
# taban oranla kiyasla — kazanc 1,1x ise bulunan kaynak TESADUFTUR.
veri = []
for b in barlar:
    h = abs(b["cl"]/b["o"] - 1) * 100
    var = any((b["s"], kova(b["ts"], j)) in kap or
              (b["s"], kova(b["ts"], j)) in hab
              for j in range(pencere + 1))
    veri.append((h, var))
veri.sort(key=lambda x: -x[0])
taban = sum(1 for v in veri if v[1]) / len(veri)
for pay in (0.05, 0.10, 0.25):
    d = veri[:int(len(veri) * pay)]
    print(pay, sum(1 for v in d if v[1]) / len(d), "vs taban", taban)
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
