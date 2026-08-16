# Konsensus turu — dış incelemeciye

Bağlam: `finagent-analiz-feedback.md` (senin incelemen) ve
`finagent-analiz-degerlendirme.md` (benim doğrulamam, kod + veritabanı
üzerinden). Bu belge **tamamını tekrar okumanı istemiyor** — yalnızca
anlaşamadığımız, senin göremediğin ve karar bekleyen noktaları
topluyor. Her başlığın altında sana somut bir soru var.

Önce şunu söyleyeyim: incelemenin çoğu doğru çıktı ve maddelerin çoğunu
kod satırıyla teyit ettim. Aşağısı itiraz listesi olduğu için orantısız
biçimde "hayır" ağırlıklı görünüyor; öyle okunmasın.

---

## 1. Anlaşamadığımız iki nokta

### 1.1 P0-3'ü (karneyi panele geri ver) P0'dan çıkarıyorum

**Ölçüm:** `predictions` tablosunda 26 kayıt var, **0 tanesi
puanlanmış**. Hepsi 2026-08-15'te oluştu, ufuk 5 gün → olgunlaşma
2026-08-20. Ölçüm döngüsü henüz tek tur tamamlamadı.

**İtirazım:** bugün enjekte edilecek cümle "n=0" olur. n=3'te
"%33 isabet" enjekte etmek daha kötü — model bunu prior gibi kullanır ve
sahte kesinlik üretir. Defterin kendi tasarımı n<20'de "yetersiz" diyor;
aynı disiplin geri beslemeye de uygulanmalı. Kodu şimdi yazılabilir ama
**eşikli** olmalı (n<20 ise enjeksiyon yok).

**Soru:** Eşikli yazıp P0'da bırakmayı mı tercih edersin, yoksa
defter olgunlaşana kadar hiç yazmamayı mı? Eşiği 20'den farklı bir yerde
görüyorsan gerekçesi nedir?

### 1.2 P1-8'in gerekçesi yanlış, hedefi de ölçülmemiş

Sen *"her enstrümanda en az bir düşüş rejimi bulunsun"* diyorsun ve
"2022 ayı piyasası hiçbir seride yok" gerekçesini veriyorsun.

**Ölçüm:** 250+ barlı **300 sembolün 285'inde** en az %20'lik zirve-dip
düşüşü var (ROSE −%54, ENJ −%48). Yani bireysel düşüş bol.

Eksik olan **makro rejim çeşitliliği**: elde tek bir faiz/likidite
rejimi var. Sonucun değişmiyor ama gerekçe böyle yazılmazsa biri
"zaten düşüş var" deyip maddeyi haklı olarak reddedebilir.

Ayrıca hedefin fizibilitesi **ölçülmemiş**. Bilinen tavanlar:

| Kaynak | Durum |
|---|---|
| Binance klines | 2017'ye kadar var, sayfalama zaten yazıldı → 8 yıl mümkün, ucuz |
| Yahoo `range: "10y"` | denenmedi, tarayıcı üzerinden çalışması muhtemel |
| İş Yatırım | **bilinmiyor** — sende 5+ yıl varsayılmış, doğrulanmadı |
| CoinGecko ücretsiz | 365 günle sınırlı (ölçüldü: 1095 gün → HTTP 401) |

BIST kritik: 251 sembolde yalnızca **13,5 ay** var ve TRY şoku
rejimleri tam orada olurdu.

**Soru:** "En az iki farklı makro rejim" formülasyonunu kabul ediyor
musun? İş Yatırım'ın derinliğini varsayımla mı yazmıştın, yoksa bir
dayanağın var mıydı?

---

## 2. Senin göremediğin bir kusur sınıfı: ölü konfigürasyon

§1.1 tablonu `config/settings.yaml`'dan okuyup olgu gibi sunmuşsun.
Ama o değerlerin bir kısmı **hiç okunmuyor**.

Binance ayarları YAML'da `watchlist.binance.daily_bars` altında;
`collectors/binance.py` ise `sources.binance.daily_bars` okuyor. Yol
tutmuyor → `None` → kod varsayılanı kazanıyor. `daily_bars: 1000` satırı
**hiçbir zaman etkili olmadı**.

Taradım: kodun okuduğu 62 ayar yolunun **22'sinin YAML'da karşılığı
yok**. İki farklı durum var:

- **Yanlış yol** — YAML'da değer var, kod başka yere bakıyor.
  Ayarlanabilir görünen ama ayarlanamayan düğme. En zararlısı.
- **Hiç tanımlanmamış** — kod varsayılanı çalışıyor, davranış doğru,
  düğme belgesiz. Alpha Vantage'ın 6 anahtarı böyle ve pratik sonucu
  var: `daily_budget` düğmesi olmadığı için kota aşımı ayarla
  çözülemiyor, collector her gece `error` dönüyor.

Bu, senin §2.6'da doğru teşhis ettiğin "bayat prompt" sorununun
konfigürasyon karşılığı: **kaynağa bakıp olgu sanmak.**

**Soru:** Bunu kendi listende nereye koyardın? Ben "hemen, tek dosya"
diye 3. sıraya aldım. Sence prompt konsolidasyonuyla (P2-11) birlikte
tek bir "tek kaynak" maddesine mi bağlanmalı?

---

## 3. Listene eklediğim iki madde

### 3.1 Sinyal → tahmin bağı yok

§5.8'de soruyorsun ama maddeye çevirmemişsin. `signals` 23 kayıt,
`predictions` 26 kayıt, **aralarında ilişki yok**: bir tahminin hangi
sinyalden doğduğu kayıtlı değil.

Bu bağ olmadan P0-1'in üreteceği taban oranlar tahminlere
bağlanamaz — backtest ile defter iki ayrı ada olarak kalır.
`predictions.signal_id` P0-2 göçüne dahil edilmeli (ikisi tek göç olur).

**Soru:** Katılıyor musun? Gözden mi kaçtı, yoksa bilerek mi dışarıda
bıraktın?

### 3.2 Uygulanabilirlik bayrağı çıktıya taşınmıyor

İncelemenden sonra eklenen bir katman var: ilk 100'de olup Binance'te
listelenmeyen 21 coin (HYPE, XMR, OKB, KAS...) `venue='CRYPTO'` ve
`alinabilir: false` ile **taranıyor ama tavsiye konusu olamıyor** —
kullanıcının verebileceği emir yok.

Bayrak tarayıcıda ve panel prompt'unda var; **günlük rapor şablonunda ve
sohbet katmanında yok**. Bunun senin §3.5'teki "pozisyon bağlamı"
maddenle kesişimi var: bir gözlem sunulurken uygulanabilirliği de
taşınmalı.

**Soru:** Bunu ayrı bir madde mi, yoksa §3.5'in bir parçası mı görürsün?

---

## 4. Karar bekleyenler — senin görüşün belirleyici olabilir

### 4.1 Persona (senin §2.7 / §5.14)

Bilinçli ayrım **değil**, birikmiş sürüklenme: `strategist.py` kuralı,
sohbet katmanı tavsiye vermeye başlamadan önce yazıldı ve orada kaldı.

Yine de sonuç savunulabilir bir yere düşmüş: günlük rapor toplu tarama,
sohbet tek konuya odaklı ve kullanıcı bağlamı taşıyor. Önerim ayrımı
**korumak ama yazmak** — rapor = gözlem, sohbet = görüş, panel = gözlem
+ gerekçe. Emir iletme yetkisi üçünde de yok.

**Soru:** Tek politikaya indirmeyi mi savunuyorsun, yoksa yazılı ayrım
yeterli mi? Kullanıcı tarafında "aynı sistemin üç ağzı" sorunu yazılı
ayrımla çözülür mü, sence çözülmez mi?

### 4.2 Prompt testleri (senin §5.13)

Mevcut duman testleri prompt sürüklenmesini **yakalamıyor** — üç bayat
satır testlerden geçerek bugüne geldi. İki test öneriyorum:

1. Kademe kuralları üç katmanda da **tek metinden** geliyor mu
2. Prompt'ta "YOK" diye beyan edilen her veri katmanı gerçekten araç
   listesinde **yok** mu

İkincisi `chat.py` kural 9'u ("FX serisi veride YOK", oysa `fx` aracı
var) otomatik yakalardı.

**Soru:** İkinci testi genelleştirmenin daha iyi bir yolunu görüyor
musun? "Prompt'ta beyan edilen yetenek listesi ile gerçek araç listesi
tutarlı" gibi daha güçlü bir değişmez kurulabilir mi?

---

## 5. Sana açık sorum

Değerlendirmemde kendi yazdığım koda karşı **savunmacı davrandığım** bir
yer var mı? Özellikle bakmanı istediğim üç nokta:

1. §1.1'e "tablo eski" demem — Binance derinliğini üç yıla ben çıkardım,
   yani kendi işimi savunuyor olabilirim. Ama tabloda düzeltilen şey
   **ölçülmüş bir olgu** (en eski bar 2023-08-13); yine de sonucun
   (tek rejim) doğru olduğunu kabul ettim. Yeterince kabul ettim mi?
2. P0-3'ü sona atmam — "şu an ölçülecek bir şey yok" gerekçesi doğru
   ama, bu aynı zamanda **benim henüz üretmediğim** bir sonucu
   ertelemek anlamına geliyor. Rasyonalizasyon mu?
3. Ölü konfigürasyon bulgusunu §B'de ayrı başlık yapmam — gerçek bir
   kusur ama senin listendeki maddelerden daha mı önemli, yoksa ben
   kendi bulduğuma fazla mı ağırlık verdim?

Bu üçünde sert olabilirsin; işe yarayan şey mutabakat değil, doğru
sıralama.

---

## 6. Mutabık olduğumuz çekirdek

Kayıt için, tartışmaya kapalı olanlar:

- Ölçüm döngüsü kapatılmadan geri kalan her şey körlemesine (§4 P0 mantığı)
- Defter kullanıcının okuduğu şeyi (hakem özetini) ölçmüyor — kritik
- `_json_cek` sessiz başarısızlığı seçilim yanlılığı üretiyor
- Temel analiz aritmetiği prompt'ta olmamalı (`valuation.py`)
- Korelasyon tahmin edilmemeli, ölçülmeli (`risk.py`)
- Tez + geçersizleşme koşulu, günlük sinyal üretmekten daha değerli
- Olay çalışmasında yayın saati kaydırması gerekli — **veri zaten elde**
  (kademe 1'de 11/11, kademe 2'de 62/63 saatli)
- Backtest'te look-ahead yasağı; `fiyat_serisi`'ne opsiyonel `bitis`
  parametresi gerekiyor (13 çağrı noktası, hiçbiri değişmez)
- Senin tek cümlelik özetin doğru

Bir tek eklemem var: ölçüm döngüsü **bozuk değil, henüz başlamamış**.
Bu, P0-2'yi daha acil yapıyor — göç şu an 26 kayıt taşıyor, bir hafta
sonra aynı düzeltme atılacak veri demek.
