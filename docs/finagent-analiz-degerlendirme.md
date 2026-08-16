# Dış incelemenin değerlendirmesi

Kaynak: `docs/finagent-analiz-feedback.md`
Değerlendiren: Claude Code (kodu ve veritabanını görebilen taraf)
Tarih: 2026-08-16 · Ölçümler `data/finagent.db` üzerinde o gün alındı.

Her iddia kodda ve veride kontrol edildi. Üç kategori kullandım:
**Doğrulandı** (ölçtüm, gerçek) · **Kısmen doğru** (sorun var ama teşhis ya da
önem derecesi kayıyor) · **Yanlış** (kodda öyle değil).

Kısa hüküm: **inceleme yüksek kaliteli.** 14 maddenin çoğu gerçek ve
uygulanabilir; iki maddede olgu hatası var, bir yerde sonuç doğru ama
gerekçe yanlış. İncelemenin tamamen kaçırdığı bir kusur sınıfı da var —
§B'de.

---

## A. Madde madde hüküm

### A.1 Doğrulandı — itiraz yok

| Madde | Doğrulama |
|---|---|
| §1.2 Geçmiş araçları yok | `ARAC_ADLARI` 18 araç; `karne`, `tahmin_gecmisi`, `gecmis_analiz`, `sinyal_gecmisi`, `taban_oran` — **beşi de yok** |
| §1.3 Karne panele geri beslenmiyor | `runner.py:46` karneyi hesaplıyor, `:72` panele **görüşleri** veriyor; `ORTAK_KURALLAR`'da karne geçmiyor |
| §1.4 Backtest yok | `src/` ve `run.py` genelinde `backtest` geçen tek dosya yok |
| §1.5 Makro katman yok | `collectors/` içinde VIX/DXY/TÜFE/TCMB geçen satır yok |
| §2.1 Defter en yüksek güveni tutuyor | `journal.py:75` — `en_iyi[anahtar]["guven"] >= guven` ise atlıyor |
| §2.1 `ajan_karnesi` yanlı | `journal.py:225` `gerekce LIKE '[ajan]%'` — yalnızca **hayatta kalan** tahminleri sayıyor |
| §2.1 Hakem puanlanmıyor | `runner.py:72` `defter.kaydet(sonuc["gorusler"])` — hakem özeti deftere hiç girmiyor |
| §2.2 `_json_cek` sessiz | `agents.py:123` — sayaç yok, log yok, retry yok |
| §2.3 Kalibrasyon ölçülmüyor | `guven` yalnızca `kaydet()` içinde geçiyor; `karne()` kovalamıyor, Brier yok |
| §2.4 `valuation.py` yok | `analysis/` = `events.py`, `indicators.py`, `portfolio.py`, `strategist.py` |
| §2.5 `portfolio.py` bayat not | `portfolio.py:56` — *"FX dönüşümü v2'de eklenecek"*, oysa `fx_rates` ve `fx` aracı var |
| §2.6 `chat.py` bayat kural | `chat.py:79` — *"FX serisi veride YOK"* |
| §2.6 `strategist.py` kripto körü | `strategist.py:20-21` — *"BIST hisseleri ve Avrupa ETF'leri üzerine çalışıyorsun"* |
| §2.7 Persona üç ağızlı | `strategist.py:32` "kesin al/sat tavsiyesi verme" ↔ `chat.py:120` "görüş VER, kaçamak yapma" |
| §3.2 Tez kaydı yok | `predictions` kolonları: yön, ufuk, güven, gerekçe, fiyatlar, isabet. `tez`, `gecersizlesme_kosulu`, `izlenecek_esik`, `ajan` **yok** |
| §2.13 Olay saati kullanılmıyor | `events.py` `published_at`'i tarih olarak alıyor |

§2.13 için ek bir bulgu, maddeyi **güçlendiriyor**: veri zaten elimizde.
Kademe 1 haberlerin **11/11**'inde, kademe 2'nin **62/63**'ünde saat bilgisi
dolu. Yani düzeltme yeni veri toplamayı gerektirmiyor, sadece kaydırma
mantığı yazmayı.

### A.2 Kısmen doğru — sonuç doğru, gerekçe kayıyor

**§1.1 Geçmiş derinliği.** Sonuç doğru, tablo eski ve bir iddiası yanlış.

Tablodaki "Binance `daily_bars: 1000` (~2.7 yıl)" artık geçerli değil:
sayfalama eklendi, gerçek derinlik **3 yıl**. Ölçülen durum:

| Venue | Sembol | En eski bar | Derinlik |
|---|---|---|---|
| BINANCE | 48 | 2023-08-13 | 3 yıl |
| BUX | 19 | 2024-08-14 | 2 yıl |
| INDEX | 3 | 2024-08-14 | 2 yıl |
| CRYPTO (referans) | 21 | 2025-08-17 | 1 yıl |
| BIST | 251 | 2025-06-30 | **13,5 ay** |

Asıl düzeltme şurada: inceleme *"2022 ayı piyasası, 2020 çöküşü hiçbir
seride yok"* diyor ve buradan "düşüş rejimi yok" sonucuna gidiyor.
Ölçtüm — **300 sembolün 285'inde en az %20'lik zirve-dip düşüşü var.**
Bireysel düşüş bolca var (ROSE −%54, ENJ −%48).

Eksik olan şey bireysel düşüş değil, **makro rejim çeşitliliği**: elde tek
bir faiz/likidite rejimi var. Bu ayrım P1-8'i doğrudan etkiliyor —
tarihi derinleştirmenin satın aldığı şey "herhangi bir düşüş bulmak"
değil, **farklı bir makro rejim** bulmak. Gerekçe böyle yazılmazsa, biri
"zaten düşüş var" deyip maddeyi haklı olarak reddedebilir.

**§2.4 Temel analiz aritmetiği prompt'ta.** Doğru, ama durum incelemenin
sandığından **daha kötü**. Ölçtüm: kayıtlı 14 sohbet turunda `finansallar`
aracı **hiç çağrılmadı** (`ara`, `gunun_hareketlileri`, `izlemeye_al`,
`pozisyon_kaydet` de öyle). Yani temel analiz yolu sadece "prompt'ta
hesaplanıyor" değil, pratikte **hiç çalışmıyor**. Örneklem küçük (14 tur),
ama `teknik` 18 kez çağrılırken `finansallar`ın sıfırda kalması rastlantı
gibi durmuyor.

### A.3 Bağlam eksik — kusur sayılmamalı

**§5.5'in ima ettiği "ölçüm yok" durumu.** `predictions` tablosunda 26
kayıt var ve **hiçbiri puanlanmamış**. Bu bir arıza değil: 26'sı da
**2026-08-15**'te oluştu ve ufukları 5 gün — olgunlaşma tarihi
2026-08-20. Ölçüm döngüsü henüz bir tur bile tamamlamadı.

Bu, incelemenin önceliklendirmesini etkiliyor (bkz. §C).

---

## B. İncelemenin kaçırdığı kusur: ölü konfigürasyon

İnceleme §1.1 tablosunu `config/settings.yaml`'dan okuyup olgu gibi
sunmuş. Ama o değerlerin bir kısmı **hiç okunmuyor.**

`settings.yaml` Binance ayarlarını `watchlist.binance.daily_bars` altında
tutuyor; `collectors/binance.py` ise `sources.binance.daily_bars` okuyor.
Yol tutmuyor → ayar `None` dönüyor → kod varsayılanı kazanıyor. Yani
o dosyadaki `daily_bars: 1000  # ~2.7 yıl` satırı **hiçbir zaman etkili
olmadı** ve yorumu da artık yanlış.

Kodun okuduğu 62 ayar yolunu YAML'a karşı taradım: **22'sinin karşılığı
yok.**

```
analysis.llm.chat_max_turns              bot/chat.py
analysis.llm.panel_max_turns             pulse/agents.py
analysis.llm.collect_timeout_sn          bot/tools.py
analysis.llm.max_buffer_mb               bot/chat.py
browser.user_agent                       browser/session.py
sources.alphavantage.daily_budget        collectors/alphavantage.py
sources.alphavantage.eu_per_run          collectors/alphavantage.py
sources.alphavantage.fx_pairs            collectors/alphavantage.py
sources.alphavantage.indices             collectors/alphavantage.py
sources.alphavantage.news_per_run        collectors/alphavantage.py
sources.alphavantage.overview_per_run    collectors/alphavantage.py
sources.binance.daily_bars               collectors/binance.py
sources.binance.hourly_bars              collectors/binance.py
sources.cgfiyat.days                     collectors/cgfiyat.py
sources.cgfiyat.max_per_run              collectors/cgfiyat.py
sources.kriptoevren.min_turnover_pct     collectors/kriptoevren.py
sources.kriptoevren.min_volume_usd       collectors/kriptoevren.py
sources.kriptoevren.top_n                collectors/kriptoevren.py
sources.midas.detay_per_run              collectors/midas.py
sources.midasbilanco.per_run             collectors/midasbilanco.py
sources.tiingo.fx_days                   collectors/tiingo.py
sources.tiingo.fx_pairs                  collectors/tiingo.py
```

İki farklı durum var ve ayrılmalı:

- **Yanlış yol** (`sources.binance.*` ↔ `watchlist.binance.*`) — YAML'da
  değer var, kod başka yere bakıyor. Ayarlanabilir görünen ama
  ayarlanamayan bir düğme. En zararlısı bu.
- **Hiç tanımlanmamış** (`sources.kriptoevren.*`, `sources.cgfiyat.*` —
  bugün eklediklerim, ve Alpha Vantage'ın 6 anahtarı). Kod varsayılanı
  çalışıyor, davranış doğru, ama düğme belgesiz.

Alpha Vantage'ınki pratik bir sonuç doğuruyor: `daily_budget` düğmesi
YAML'da olmadığı için kota aşımı ayarla çözülemiyor ve collector her
gece `error` dönüyor (ücretsiz katman günde 25 istek). **Veri deliği
yok** — FX'i Tiingo ve İş Yatırım da veriyor, EUR/USD üçünde de 1,157
çıkıyor — ama her gece sahte alarm üretiyor ve `error` etiketinin
anlamını aşındırıyor.

Bu kusur sınıfı, incelemenin §2.6'da doğru teşhis ettiği "bayat prompt"
sorununun konfigürasyon karşılığı: **kaynağa bakıp olgu sanmak.**
Dış incelemeci bunu yapamazdı; ancak kodu çalıştırarak görülüyor.

---

## C. Sorulara cevaplar (§5)

**1. Bar derinliği dağılımı.** Serisi olan 342 enstrüman. Min 7, medyan
**282**, maks 1.099 bar. **282 enstrümanda 500 bardan az.** Yalnızca 60
enstrümanda ≥500 bar var.

Bu, backtest'in kullanılabilir evrenini ciddi biçimde daraltıyor: 250
bardan uzun 300 sembol var, ama 500+ barlı yalnızca 60. BIST tarafı
(251 sembol) 13,5 aylık — yani BIST'te 5 günlük ufuklu bir sinyal tipi
için bile örneklem ince kalır.

**2. Düşüş rejimi.** 250+ barlı 300 sembolün **285'inde** en az %20
düşüş var. 42 sembol ölçülemeyecek kadar kısa. Yani bireysel düşüş bol;
eksik olan makro rejim çeşitliliği (bkz. A.2).

**3. `fiyat_kaynagi()` çoklu kaynak.** Şu an tek kaynağa düşen seçim
baskın: kripto tarafı `binance`/`cgfiyat` ayrık, BUX tarafı `prices`
ağırlıklı. Bugün para birimi eksiği **sıfıra** indi (127.262 barın
tamamında `currency` dolu). Kaynak çakışması ASML'de belgelenmişti
(Yahoo USD ↔ Alpha Vantage EUR); Alpha Vantage kotası dolu olduğu için
şu an yeni çakışma üretmiyor.

**4. Kimliği çözülmemiş enstrüman.** `identities`: 83 `dogrulandi`,
4 `elle`, 2 `sec_disi`, 1 `fiat`, 1 `cift_yok`. Yani kimlik yüzünden kör
kalan evren **pratikte yok** — bugün CNDX/VUSA/RBOT/AVTX elle
çözüldüğünde son büyük boşluk kapandı.

**5. `predictions` durumu.** 26 kayıt, **0 puanlanmış** (hepsi
2026-08-15, ufuk 5 gün, olgunlaşma 2026-08-20). Gerekçe önekinden ajan
dağılımı: `risk` 11, `teknik` 9, `temel` 5, `olay` 1.

Kaç görüşün atıldığı **loglardan çıkarılamıyor** — `kaydet()` atarken iz
bırakmıyor. Bu, P0-2'ye eklenmesi gereken bir ayrıntı: geçişte yalnızca
şemayı değil, **atma olayının kendisini de** kayda geçirmeliyiz.

**6. `_json_cek()` boş dönüş sayısı.** **Ölçülemiyor.** Fonksiyon iz
bırakmıyor ve panel çıktısı ayrıca saklanmıyor; `analysis_runs`'ta 6
kayıt var ama ajan ham metni yok. Yani "kaç turda kaybedildi" sorusu
geriye dönük cevaplanamaz — ancak sayaç eklendikten **sonrası** için
cevaplanabilir. Bu, P0-2'nin ilgili maddesini "iyi olur"dan
"önce yapılmalı"ya taşıyor.

**7. Araç kullanımı.** Kayıtlı 14 turda: `teknik` 18, `fiyat_serisi` 17,
`haberler` 11, `grafik` 4, `veri_topla` 4, `portfoy` 3, `olay_etkisi` 3,
`kaynak_goruntusu` 2, `tokenomik` 2, `kimlik`/`fx`/`saatlik`/
`veri_durumu` 1'er.

**Hiç çağrılmayanlar (5):** `ara`, `finansallar`, `gunun_hareketlileri`,
`izlemeye_al`, `pozisyon_kaydet`.

`pozisyon_kaydet` ve `izlemeye_al` için bu normal (yazma araçları,
nadiren gerekir). `finansallar`ın sıfırda olması ise A.2'de anlatıldığı
gibi §2.4'ü güçlendiriyor. Örneklem küçük, kesin konuşmuyorum.

**8. Sinyal tipi dağılımı.** `signals` tablosunda 23 kayıt — 30 günlük
dağılım çıkarmaya yetmiyor. Sistem 2026-08-15'te üretmeye başladı.

**9. Bütçe.** Backtest **LLM'siz** olacağı için abonelik maliyeti
gerçekten sıfır: `screener.py` ve `indicators.py` saf hesap, model
çağrısı yok. Doğrulanabilir. Makro collector da LLM'siz. Bütçeyi yiyen
tek şey panel (4 ajan + hakem) ve sohbet; ikisi de değişmiyor.

Ama **duvar saati** bütçesi ayrı bir kısıt: gecelik toplayıcı zinciri şu
an 13,7 dk. CoinGecko referans katmanı hız sınırı yüzünden tur başına
10 istekle sınırlandı; makro collector eklenirse aynı sınıf sorun
tekrar bakılmalı.

**10. Look-ahead.** `db.fiyat_serisi(instrument_id, limit)` — **tarih üst
sınırı yok**, en yeni bardan geriye gidiyor. Backtest bu imzayla
yazılırsa **look-ahead kesin sızar**. Çağrı noktası **13**. Doğru çözüm
`limit`'i bozmadan opsiyonel `bitis` parametresi eklemek; 13 çağrı
noktasının hiçbiri değişmez, yalnızca backtest yeni parametreyi kullanır.

**11. Göç gerektirenler.** Şema göçü isteyenler: **P0-2** (`predictions`'a
`ajan`; SQLite `ALTER TABLE ADD COLUMN` destekliyor, mevcut 26 kayıt
korunur — ve şu an 26 kayıtla göç yapmak, altı ay sonra binlerce kayıtla
yapmaktan çok ucuz), **P0-1** (`signal_stats` yeni tablo), **P2-10**
(`tez`, `gecersizlesme_kosulu`, `izlenecek_esik`), **P1-7** (makro
serisi için tablo ya da `fundamentals` yeniden kullanımı).
Tek dosyada bitenler: **P0-4** (tools.py), **P2-13** (events.py),
**P1-5** (yeni `valuation.py` + tools.py), ölü konfigürasyon temizliği
(settings.yaml).

**12. Sıralama — kısmen katılmıyorum.** Ayrıntı §D'de.

**13. Prompt testleri.** Mevcut duman testleri prompt sürüklenmesini
**yakalamıyor** — §2.6'daki üç bayat satır testlerden geçerek bugüne
geldi. Evet, prompt'a özel test yazılmalı; ama refactor'dan önce.
Önerdiğim iki test: (a) kademe kurallarının üç katmanda da tek metinden
geldiği, (b) prompt'ta "YOK" diye beyan edilen her veri katmanının
gerçekten araç listesinde bulunmadığı. İkincisi `chat.py` kural 9'u
otomatik yakalardı.

**14. Persona.** Bilinçli bir ayrım **değil**, birikmiş bir sürüklenme.
`strategist.py` kuralı, sohbet katmanı tavsiye vermeye başlamadan önce
yazıldı ve orada kaldı. Yine de sonuç tesadüfen savunulabilir bir yere
düşmüş: günlük rapor toplu bir tarama, sohbet ise tek konuya odaklı ve
kullanıcı bağlamı taşıyor. Önerim ayrımı **korumak ama yazmak**:
rapor = gözlem, sohbet = görüş, panel = gözlem + gerekçe. Emir iletme
yetkisi üçünde de yok ve olmayacak.

**15. Katılmadığım madde.** Bir tanesinin sırasına katılmıyorum
(**P0-3**), bir tanesinin gerekçesini düzeltiyorum (**P1-8**). İkisi de
§D'de.

---

## D. Sıralama — benim önerim ve iki itiraz

### İtiraz 1: P0-3 (karneyi panele geri ver) şu an **zararlı**

İnceleme bunu P0'a koymuş. Ama puanlanmış tahmin sayısı **sıfır** ve
5 günlük ufukla haftada ~5-25 tahmin olgunlaşacak. Panele bugün
enjekte edilecek cümle şu olurdu:

> "Bu sinyal tipindeki geçmiş isabetin: n=0."

n=3'te "%33 isabet" enjekte etmek daha kötü: model bunu bir prior gibi
kullanır ve **sahte kesinlik** üretir. Defterin kendi tasarımı n<20'de
"yetersiz" diyor — aynı disiplin geri beslemeye de uygulanmalı.

P0-3'ün kodu şimdi yazılabilir, ama **eşikli** olmalı: n≥20 değilse
enjeksiyon hiç yapılmasın. Sıralamada P0 değil, defter olgunlaştıktan
sonra.

### İtiraz 2: P1-8'in gerekçesi düzeltilmeli, hedefi ölçülmeli

"Her enstrümanda en az bir düşüş rejimi bulunsun" hedefi zaten
karşılanmış görünüyor (285/300). Doğru hedef: **en az bir farklı makro
rejim.** Ve bunun ne kadar mümkün olduğu **ölçülmeden** planlanmamalı:

- Binance klines 2017'ye kadar gidiyor → sayfalama zaten var, 3 yıl
  yerine 8 yıl teknik olarak mümkün. Ucuz.
- Yahoo `range: "10y"` → denenmeli, tarayıcı üzerinden çalışıyor.
- İş Yatırım'ın 5 yıl verip vermediği **bilinmiyor**; incelemede
  varsayılmış. Önce ölçülmeli.
- CoinGecko ücretsiz katman 365 günle sınırlı (ölçüldü: 1095 gün
  istendiğinde HTTP 401). Referans coin'ler derinleşemez.

BIST tarafı burada kritik: 251 sembolde 13,5 ay var ve TRY şoku
rejimleri tam da orada olurdu.

### Önerdiğim sıra

1. **P0-2 (defter) — hemen.** 26 kayıtla göç yapmak bedava; ölçüm
   döngüsü bozuk çalışmaya devam ederse biriken her tahmin yanlı olur.
   `_json_cek` sayacı bu adıma dahil, çünkü sayaç olmadan §5.6
   geriye dönük cevaplanamıyor.
2. **P2-13 (olay yayın saati) — hemen.** Veri elde (kademe 1'de 11/11,
   kademe 2'de 62/63 saatli). Şu an ürettiğimiz CAR sayılarında sessiz
   bir yanlılık var; ucuz ve mevcut çıktıyı düzeltiyor.
3. **Ölü konfigürasyon temizliği — hemen.** §B. Tek dosya, düğmelerin
   gerçekten düğme olması.
4. **Bayat prompt satırları — hemen.** `chat.py` kural 9, `strategist.py`
   başlığı, `portfolio.py:56`. Bunlar P2-11 refactor'unu **beklememeli**;
   üçü on dakikalık ve şu an modele yanlış bilgi veriyorlar.
5. **P0-4 (geçmiş araçları).** Ucuz, tek dosya, kullanıcıya doğrudan
   değer: "iki hafta önce ne demiştin, tuttu mu" cevaplanır hale gelir.
6. **P1-8 ölçümü, sonra dolumu.** Önce her kaynağın gerçek tavanı
   ölçülür, sonra dolum yapılır.
7. **P0-1 (backtest).** 6'dan sonra. `fiyat_serisi`'ne opsiyonel `bitis`
   parametresi ilk iş.
8. **P1-5 (valuation.py).** Temel analiz yolunu prompt'tan koda taşır.
   `finansallar` aracının hiç çağrılmaması (§C.7) bunu ayrıca haklı
   çıkarıyor: araç ham değer döndüğü için işe yaramıyor olabilir.
9. **P2-10 (tez + bozulma alarmı).** Katılıyorum, tavsiye kalitesinde en
   yüksek getirili madde. Ama P0-2 göçüyle **aynı anda** yapılırsa tek
   şema değişikliğine iner.
10. **P1-6 (risk.py), P1-7 (makro), P1-9 (ETF look-through), P0-3 (eşikli
    geri besleme), P2-12 (post-mortem), P2-14 (maliyet modeli).**

İncelemenin sırasından tek yapısal farkım: **P0-2'yi tek başına ilk
sıraya alıyorum ve P0-3'ü sona atıyorum**; aradaki boşluğu ucuz ve
kesin faydalı düzeltmelerle (2-5) dolduruyorum. İncelemenin
"P1-8 → P0-1" mantığına katılıyorum — backtest'i tarih dolumundan önce
yapmak anlamsız.

---

## E. İncelemede eksik kalan iki soru

**E.1 Sinyal→tahmin dönüşümü ölçülmüyor.** İnceleme §5.8'de soruyor ama
maddeye çevirmiyor. `signals` 23 kayıt, `predictions` 26 kayıt, aralarında
bağ yok: bir tahminin **hangi sinyalden** doğduğu kayıtlı değil.
Bu bağ olmadan P0-1'in ürettiği taban oranlar tahminlere bağlanamaz —
yani backtest ile defter ayrı iki ada olarak kalır. `predictions`'a
`signal_id` eklenmesi P0-2 göçüne dahil edilmeli.

**E.2 Referans/alınabilir ayrımı incelemede yok.** İnceleme bugün
eklenen katmanı görmemiş: 21 coin `venue='CRYPTO'` ve
`alinabilir: false` ile taranıyor ama tavsiye konusu olamıyor.
Bu ayrımın §3.5'teki "pozisyon bağlamı" maddesiyle kesişimi var:
kullanıcıya bir gözlem sunulurken **uygulanabilirliği** de taşınmalı.
Şu an bayrak tarayıcıda ve panel prompt'unda var, ama günlük rapor
şablonunda ve sohbet katmanında yok.

---

## F. Özet

İncelemenin tek cümlelik özeti doğru: sistem **veri toplama ve dürüstlük**
tarafında güçlü, **kendi geçmişini kullanma ve söylediğini ölçme**
tarafında yarı yolda.

Buna ekleyeceğim tek şey: ölçüm döngüsü **bozuk değil, henüz
başlamamış**. 26 tahminin hiçbiri olgunlaşmadı. Bu, P0-2'yi daha da
acil yapıyor — döngü ilk meyvesini vermeden önce düzeltilirse, biriken
veri baştan temiz olur. Bir hafta sonra aynı düzeltme, atılacak veri
anlamına gelir.
