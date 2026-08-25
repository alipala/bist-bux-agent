# IBKR planı — belge değerlendirmesi

**Değerlendirilen belge:** `docs/IBKR AI Trading Assistant — Claude Opus 4.8 Build Prompt Sequence.md`
(GPT-5.6-Sol, yüksek efor — bu depoyu görmeden yazılmış)

**Kaynak:** interactivebrokers.com/docs/web-api altındaki 40+ sayfa, resmî Markdown
sürümleriyle indirildi (`.md` uzantısı ekleyerek). Ayrıca `ibkrcampus.com` "Getting
Started" sayfası. Aşağıdaki her iddia bu sayfalardan geliyor; **doğrulanmadığı**
yerleri açıkça işaretledim.

---

## Özet hüküm

Belge **mimari olarak sağlam, IBKR gerçeğinden kopuk**. Güvenlik tasarımı (LLM
öneri üretir → insan onaylar → deterministik kod yürütür), göstergelerin Python'da
hesaplanması, conid'in kanonik kimlik olması, emir POST'unun asla otomatik yeniden
denenmemesi — bunların hepsi doğru ve iyi düşünülmüş.

Ama **IBKR'ye nasıl bağlanılacağı hiç yazmıyor.** `IBKR_BASE_URL` diye bir ortam
değişkeni koyup geçiyor. Bireysel hesap için bu tek satır, projenin en büyük
kısıtını gizliyor: **bağlantı otomatikleştirilemiyor.**

İkinci sorun: belge bu depoyu bilmiyor. Öneri gövdesi, onay kapısı, risk motoru,
pozisyon tablosu, analiz kaydı, enstrüman kimliği — hepsinin bu depoda çalışan ve
test edilmiş bir karşılığı zaten var. Sıfırdan ikinci bir uygulama kurmak
[ayni-kural-iki-kopya] hafızasındaki hatayı tekrarlar: kopyalar ayrışır.

---

## 1. En büyük hata: kimlik doğrulama

Belge kimlik doğrulamadan hiç bahsetmiyor. Gerçek şu — ve üç ayrı resmî sayfada
aynı şekilde yazıyor:

| Yöntem | Desteklenen hesap tipi |
|---|---|
| **Client Portal Gateway (CPGW)** | **Bireysel hesaplar** |
| OAuth 1.0a | Danışman, broker, prop trading, hedge fon, üçüncü taraf geliştirici |
| OAuth 2.0 | Danışman, broker, prop trading, hedge fon |

> "**OAuth 2.0 is not available to Individual account structures**."
> — `authentication/oauth-2/register`

> "**Retail** — For retail and individual clients, Authentication to our WebAPI is
> managed using the **Client Portal Gateway**, a small java program used to route
> local web requests with appropriate authentication."
> — ibkrcampus.com/docs "Getting Started"

Yani senin için tek yol CPGW. Bunun anlamı:

- **Java süreci** yerelde çalışacak (`bin/run.sh root/conf.yaml`).
- Taban URL `https://api.ibkr.com/v1/api` **değil**, `https://localhost:5000/v1/api`.
- Sertifika kendinden imzalı → `verify=False`. IBKR bunu "beklenen" diyor.
- **macOS'ta 5000 portu çakışıyor** (AirPlay). IBKR'nin kendi SSS'i port değiştirmeyi
  öneriyor.
- Her API çağrısı, tarayıcıda giriş yapılan **aynı makineden** gitmeli.

Ve kritik cümle:

> "Interactive Brokers **does not support an automated authentication process** for
> Client Portal Gateway." … "Clients must reauthenticate using the Client Portal
> Gateway **daily**."
> — `authentication/cpgw/client-portal-gateway-faq`

**Her gün elle tarayıcıdan giriş yapman gerekiyor.** Bu, bu deponun tüm ruhuna
aykırı: burada her şey launchd altında, sen uyurken çalışıyor. IBKR katmanı
zamanlanmış koşuya giremez — ya da girerse, "oturum yok" ile sessizce
başarısız olur. [yanlis-yok-beyani] hafızasındaki hata sınıfının tam adayı.

---

## 2. Oturum ömrü — planda hiç yok

Belge PROMPT 2'de "session state kontrol et" diyor ve geçiyor. Gerçek gereksinim:

| Uç | Yöntem | İş |
|---|---|---|
| `/iserver/auth/status` | POST | Kimlik doğrulandı mı, rakip oturum var mı |
| `/iserver/auth/ssodh/init` | POST | Brokerage oturumu başlat, gövde `{"publish":true,"compete":true}` |
| `/tickle` | POST | **~60 saniyede bir** — yoksa oturum ölür |
| `/logout` | POST | Kapat |
| `/iserver/reauthenticate` | — | **Kullanma, deprecated** |

Süreler:

- **5–6 dakika** istek gelmezse oturum zaman aşımına uğrar. `/tickle` yaklaşık her
  dakika çağrılmalı.
- **24 saat** azami; New York / Zug / Hong Kong gece yarısında sıfırlanır.
- `/iserver` uçları **her gün ~01:00 yerel saatte** bakıma girer (bölgene göre CEST).
- Web API genel bakım: **Cumartesi akşamları**.

Planda `/tickle` döngüsü yok. Onsuz uygulama her 6 dakikada bir ölür. Bu, planın
en somut uygulama eksiği.

---

## 3. Tek oturum kuralı — telefonun API'yi öldürür

> "Only a **single active brokerage session** can exist for any username **across all
> IBKR services**. If you are logged in to either Client Portal, TWS, or IBKR Mobile,
> make sure to log out and try reauthenticating your session Web API again."
> — `authentication/multiple-sessions`

Telefonundaki IBKR uygulamasını açmak API oturumunu düşürür. IBKR'nin çözümü:
**ikinci bir kullanıcı adı aç** (aynı hesap, ayrı kullanıcı). Ama:

> "market data services are **user-specific** and any username subscribed will be
> assessed a **separate market data subscription fee**."

Yani ikinci kullanıcı adı = ikinci piyasa verisi aboneliği ücreti.

`/iserver/auth/status` yanıtındaki `competing: true` alanı bunu tespit ediyor —
planda bu alan hiç geçmiyor, ama izleme katmanının ana sinyali olmalı.

---

## 4. "Paper mode" bir bayrak değil, ayrı bir kullanıcı adı

Plan `IBKR_MODE=paper` ve `ENABLE_LIVE_TRADING=false` ortam değişkenleriyle
güvenlik sağladığını sanıyor. Sağlamıyor.

> "Users may notice that unlike other parts of Interactive Brokers, **there is no
> slider to indicate a live or Paper account login**. As such, customers must use
> their **specific Paper username** to authenticate."
> — `authentication/paper`

Kağıt hesap = ayrı kullanıcı adı + ayrı şifre (Client Portal → Settings → Paper
Trading Account'tan alınır, şifre sıfırlanabilir). Hangi hesaba bağlandığını
**tarayıcıya ne yazdığın** belirler; uygulamanın `.env`'i değil.

Dolayısıyla planın canlı-işlem koruması **hiçbir şey korumuyor**: `.env`'de
`paper` yazarken canlı kullanıcı adıyla giriş yapılmışsa, emir canlı hesaba gider.

**Doğru koruma:** emir göndermeden önce `/portfolio/accounts` çağır, dönen
`accountId`/`type` alanına bak. Kağıt hesap kimlikleri `DU` ile başlıyor ve
`"type": "DEMO"` dönüyor (resmî örnekte böyle). Ortam değişkeni değil, **kanıt**
denetle. [pozisyon-yazma-semantigi] hafızasındaki "ayrım kanıtta" ilkesi.

Not: kağıt hesabın piyasa verisi, canlı hesabın aboneliklerine **bağlanmalı**
(Paper Trading Account ayarlarında "linking your Market Data" seçeneği var).
Bağlanmazsa kağıt hesapta veri gelmez.

---

## 5. Piyasa verisi: "ön-uçuş" isteği — planın en sinsi eksiği

Plan `GET /api/market/{conid}/snapshot` diyor ve tek çağrıda fiyat bekliyor.
Gerçek:

> "In order for the desired data to be available for snapshotting on request, a
> **'pre-flight' request** must be made to IServer to begin its consumption of the
> instrument's live data stream. **This initial request will not deliver any data**,
> but rather makes the stream available for future snapshot requests."
> — `market-data/top-of-book-snapshots`

İlk çağrı sadece conid listesi döner:

```json
[{"conid": 265598, "conidEx": "265598"}]
```

Fiyat ikinci çağrıda gelir. Ayrıca `/iserver/accounts` **snapshot'tan önce** en az
bir kez çağrılmış olmalı.

Bu neden sinsi: planın `DataQualityStatus` modeli ilk yanıtı `INVALID` işaretler,
analizi `INSUFFICIENT_DATA` yapar ve **veri VARKEN yok der**. Tam olarak
[yanlis-yok-beyani] hafızasındaki hata. Adaptörde açık ön-uçuş + kısa bekleme +
yeniden çağrı olmalı.

Alan etiketleri sayısal ve dokümante:

| Etiket | Anlam | Etiket | Anlam |
|---|---|---|---|
| `31` | Son fiyat | `87` | Hacim |
| `84` | Alış (bid) | `7295` | Açılış |
| `86` | Satış (ask) | `7296` | Kapanış |
| `85` | Ask size | `7741` | Önceki kapanış |
| `88` | Bid size | `7293`/`7294` | 52H yüksek/düşük |
| `7059` | Son işlem adedi | `6509` | **Veri erişilebilirliği** |

**Değerler string geliyor** ve binlik ayraç içerebiliyor (`"88": "1,300"` — resmî
örnekten). Doğrudan `float()` çağırmak patlar. Bu deponun
[para-birimi-ve-sembol-tuzagi] dersinin aynısı: ham değeri asla doğrudan sayı sanma.

---

## 6. Piyasa verisi aboneliği ve maliyeti

> **Düzeltme (2026-08-25).** Bu bölümün ilk hâli "abonelik almazsan gecikmeli veri
> alırsın" diyordu. **Yanlıştı.** ABD hisseleri ve ETF'leri için gerçek zamanlı
> veri ücretsiz. Aşağıdakiler
> `interactivebrokers.com/en/pricing/market-data-pricing.php` sayfasından
> doğrulandı.

### Ücretsiz olan

> "**Free Streaming Data on US-listed Stocks and ETFs** – IBKR clients receive
> **free real-time streaming market data on all US-listed stocks and ETFs** from
> Cboe One and IEX. (Non-consolidated)"

Fiyat tablosunda satır aynen şöyle:
`US Real-Time Non-Consolidated Streaming Quotes | United States |
Complimentary - No Subscription Required` — hem Non-Professional hem Professional için.

Ayrıca: **ayda 100 ücretsiz snapshot** ("up to 100 free snapshot quotes per month",
yani $1,00 aylık muafiyet ÷ $0,01).

### Ücretli olan ve ne kazandırdığı

Ücretsiz akış **non-consolidated**: sadece Cboe One + IEX. NBBO değil.

> "Non-consolidated real-time data only provides data from **some exchanges** and
> **does not show the NBBO**."

Consolidated (NBBO) için, Non-Professional fiyatları:

| Servis | Aylık | Not |
|---|---|---|
| US Securities Snapshot and Futures Value Bundle | **$10,00** | Ön koşul. **Aylık $30 komisyonda muaf** |
| US Equity and Options Add-On Streaming Bundle | **$4,50** | NYSE + NASDAQ + Network B + OPRA streaming |
| **Toplam** | **$14,50** | Komisyon eşiği tutarsa etkin **$4,50** |

(Professional statüde add-on bundle $125,00 — statü doğrulaması önemli.)

### Üçüncü yol: snapshot başına ödeme — ve bu bizim desenimize uyuyor

> "US-listed equities and ETFs are **USD 0.01 per request**… All IBKR accounts
> receive a USD 1.00 monthly waiver… **If a client's snapshot quote fees within a
> month equal the cost of the real-time streaming service, they will automatically
> be upgraded to the real-time streaming service for that month.**"

Ve kritik cümle:

> "**Snapshot data for US-listed equities and ETFs provides** the size, quantity, and
> market identifications for the **National Best Bid and Offer (NBBO)** and Last Sale
> from the **Consolidated Tapes A, B, and C**."

Yani **$0,01'lik snapshot consolidated/NBBO**. Ve maliyet kendiliğinden tavanlanıyor:
bir ayda $14,50'yi bulursa IBKR seni o ay streaming aboneliğine yükseltiyor. Yani
snapshot yolunun aylık maliyeti $14,50'yi **aşamaz**.

Bu deponun kullanım deseni (günlük tarama, [arastirma-kapsami-30-50]: portföy +
30-50 aday) tam buna oturuyor: günde ~40 snapshot × 21 iş günü ≈ 840 snapshot ≈
$8,40/ay — ve tavan zaten $14,50.

### Yine de kalan iki tuzak

**`6509` alanı hâlâ okunmalı.** Ücretsiz olması, geldiğini garanti etmiyor:

| Kod | Anlam |
|---|---|
| `R` | Gerçek zamanlı |
| `D` | 15–20 dk gecikmeli |
| `Z` / `Y` | Donmuş / gecikmeli donmuş (kapanış) |
| `N` | Abone değil |
| `O` | **Yıllık "Market Data API Agreement" imzalanmamış** |

`O` hâlâ geçerli bir engel — Client Portal'dan elle onaylanmalı, ücretle ilgisi yok.
Ve ABD dışı ürünler ile vadeli işlemler için "Free **Delayed** Market Data"
geçerli, yani orada gecikme gerçek.

Bu yüzden ChatGPT'nin önerdiği gibi Claude'a çıplak sayı değil, **kanıtlı** sayı
gitmeli — bu deponun kaynak kademesi kültürüyle de aynı:

```json
{"price": 42.10, "availability": "RpB", "realtime": true,
 "consolidated": false, "source": "ibkr", "received_at": "..."}
```

### Ve iki analizin de kaçırdığı asıl kısıt: **100 eşzamanlı hat**

> "All clients initially receive **100 concurrent lines of real-time market data**
> (which can be displayed in TWS or **via the API**) and always have a **minimum of
> 100 lines** of data."

Sonraki aylarda tahsis: `max(aylık komisyon ÷ 8, özsermaye × 100 ÷ 1.000.000, 100)`.

Bu doğrudan §5'teki ön-uçuş desenini vuruyor: her ön-uçuş isteği o conid için bir
**akış açıyor** ve bir hat tüketiyor. 40 aday tararsan 40 hat açılır. Okuduktan
sonra kapatılmazsa hatlar sızar ve 100'e dayanır — sonra yeni semboller sessizce
veri döndürmez. Yani **yok** beyanı üretir, ki bu deponun en kötü hata sınıfı.

Çözüm dokümante: `/iserver/marketdata/unsubscribe` (tek) ve
`/iserver/marketdata/unsubscribeall`. Adaptörde ön-uçuş → oku → **abonelikten çık**
üçlüsü zorunlu, ve açık hat sayısı sayılmalı.

Son olarak: `hasTradingPermissions` (`7768`) alanı var — o enstrümanda işlem yetkin
var mı, emir göndermeden önce bakılabilir.

---

## 7. Hız sınırları ve ceza kutusu — planda hiç yok

> "a global request rate limit of **50 requests per second** for each authenticated
> username… Users making requests via the **CP Gateway** tool are restricted to
> **10 requests per second**."
> "Violator IP addresses may be put in a **penalty box for 10 minutes**… Repeat
> violator IP addresses may be **permanently blocked**."
> — `usage-and-availability/pacing-limitations`

Sen CPGW kullanacaksın → **10 req/s**, 50 değil.

Uç bazlı sınırlar, planı doğrudan ilgilendirenler:

| Uç | Sınır |
|---|---|
| `/iserver/marketdata/snapshot` | 10 req/s |
| `/iserver/orders` | **1 istek / 5 saniye** |
| `/iserver/trades` | **1 istek / 5 saniye** |
| `/portfolio/accounts` | **1 istek / 5 saniye** |
| `/tickle` | 1 req/s |
| `/iserver/marketdata/history` | **5 eşzamanlı istek**, azami **1000 bar** |

PROMPT 13 emirden önce "market snapshot, buying power, position, open orders — hepsini
yeniden çek" diyor. Bu tek başına `/portfolio/accounts` (1/5sn) + `/iserver/account/orders`
(1/5sn) demek. Ard arda iki emir denemesi 429 yer. **10 dakikalık ceza kutusu**, bu
depodaki zamanlanmış koşular için gerçek bir risk — [zamanlanmis-kosu-gozetimi]
hafızasındaki "koruma logda kaç kez çalıştı" ölçütü buraya da lazım.

Planda hız sınırlama katmanı **yok**. Olmalı, ve merkezî olmalı.

---

## 8. Emir akışında eksik adım: onay mesajları

Planın en büyük **işlevsel** boşluğu. `submit_approved_paper_order()` POST atıyor ve
`order_id` bekliyor. Gerçekte POST şunu dönebilir:

```json
[{
  "id": "07a13a5a-4a48-44a5-bb25-5ab37b79186c",
  "message": ["The following order \"BUY 100 AAPL NASDAQ.NMS @ 165.0\" price exceeds
               the Percentage constraint of 3%. Are you sure you want to submit this order?"],
  "isSuppressed": false,
  "messageIds": ["o163"]
}]
```

Emir **çalışmıyor**. `POST /iserver/reply/{id}` gövdesi `{"confirmed":true}` ile
onaylanana kadar askıda. Ve bu zincirlenebilir — birden fazla mesaj arka arkaya gelebilir.

Bu, planın güvenlik mimarisiyle doğrudan çatışıyor: IBKR **ikinci bir onay** talep
ediyor ve plan bunu bilmiyor. İki seçenek:

1. `POST /iserver/questions/suppress` ile oturum başında bastır (IBKR bunu öneriyor:
   "submit this list at the beginning of your brokerage session") — ama o zaman
   IBKR'nin "fat finger" korumalarını kaybedersin.
2. Mesajı **insana göster**, onay ekranının parçası yap.

Bu depodaki onay kültürü (`onay.py`, iki yaş sınırı, atomik `os.replace`) 2. seçeneğe
çok daha uygun. **Önerim: 2.**

Ayrıca planın "POST'u asla otomatik yeniden deneme" kuralı doğru ama eksik: reply
POST'u bir **yeniden deneme değil, devam adımı**. Kod bu ikisini ayırt etmeli, yoksa
ya emir asılı kalır ya çift emir gider.

---

## 9. Küçük ama kırıcı ayrıntılar

- **Yeni emir gövdesi JSON dizisi, değiştirme gövdesi JSON nesnesi.** Aynı uç ailesi,
  farklı şekil. Planda geçmiyor; kolay hata.
- **Değiştirmede tüm alanlar yeniden gönderilmeli**, sadece değişen değil.
- **İptal "iptal edildi" demek değil**: `{"msg": "Request was submitted"}` — IBKR
  açıkça uyarıyor, borsa iptali reddedebilir. Planın durum makinesi bunu ayırmıyor.
- **Benzer yollar karıştırılıyor**: `POST /iserver/account/{accountId}/orders` (yeni
  emir) ile `GET /iserver/account/orders` (açık emirler) neredeyse aynı.
- **Pozisyonlar**: doğru uç `/portfolio2/{accountId}/positions` (önbelleksiz).
  `/portfolio/accounts` **önce** çağrılmış olmalı. (IBKR'nin kendi Python örneğinde
  `:accountId` string'i interpole edilmemiş bir hata var — örnekleri körlemesine
  kopyalama.)
- **Geçmiş veri**: `period=1y&bar=1d` geçerli (adım tablosu izin veriyor), ama
  azami 1000 nokta. Hacim `volumeFactor` ile ölçekli, fiyatlar `priceFactor` ile.
  Bar zaman damgası **operatör saat dilimi** epoch'u — UTC değil. Plan "UTC'de sakla"
  diyor, doğru; ama dönüşüm açık yazılmalı.
- **2FA**: IB Key, Handy Key, SMS, DSC+ destekli. **Security Code Card desteklenmiyor.**
- **Kimlik çözümü**: `/trsrv/stocks?symbols=AAPL`. Aynı hisse her piyasa/para birimi
  için **farklı conid** alıyor (AAPL USD ≠ AAPL MXN). Bu deponun
  [para-birimi-ve-sembol-tuzagi] hafızasındaki tuzağın IBKR'deki tam karşılığı.

---

## 10. Belgenin DOĞRU olan kısımları

Bunlar gerçekten iyi ve korunmalı:

- **conid kanonik kimlik.** IBKR de aynısını söylüyor: "conids are persistent for the
  life of an instrument… we recommend retrieving conids and **storing them locally**
  prior to trading."
- **Göstergeler Python'da, LLM'de değil.** Doğru, ve bu depo zaten böyle yapıyor.
- **Emir POST'u asla otomatik yeniden denenmez; zaman aşımı = `EXECUTION_STATE_UNKNOWN`
  → IBKR açık emirlerine karşı mutabakat.** Bu, belgenin en olgun kısmı ve IBKR'nin
  iptal semantiğiyle birebir uyumlu.
- **Onay karması (`proposal_hash`) + nonce; öneri değişirse onay geçersiz.** Doğru.
- **Parada `Decimal`.** Doğru.
- **Risk motoru LLM'den ayrı, deterministik; pozisyon boyutunu LLM belirlemez.** Doğru,
  ve bu deponun [tahmin-defteri-ve-getiri-gercekligi] / "yüzde, asla tutar" ilkesiyle aynı.
- **AI yüküne hesap kimliği/token sızmadığını test etmek.** Doğru ve değerli.
- **Veri kalitesi durumları + kaynak/zaman damgası/tazelik zorunluluğu.** Doğru;
  bu deponun kaynak kademesi kültürüyle örtüşüyor.
- **Analiz kaydı: dünkü öneriyi bugünkü fiyata bakmadan açıklayabilmek.** Doğru ve
  backtest için şart.
- **PROMPT 20 (düşmanca kod incelemesi).** İyi fikir; bu depoda zaten dış inceleme
  döngüsü var.

---

## 11. Claude katmanı: üç düzeltme

| Belge | Gerçek |
|---|---|
| `model: claude-opus-4-8` | Güncel Opus **`claude-opus-5`**. 4.8 hâlâ var ama bir sürüm geride. |
| `ANTHROPIC_API_KEY` + `anthropic` SDK | Bu depo **`claude-agent-sdk`** kullanıyor ve Claude Max **aboneliğiyle** gidiyor. API anahtarı **aboneliği gölgeler** — [claude-max-abonelik-kullan] hafızasında yazılı. |
| "structured JSON output… doğrulama başarısızsa bir kez tekrar dene" | `output_config.format` (json_schema) ile şema **API katmanında** garanti ediliyor. Elle tekrar-deneme mantığı büyük ölçüde gereksiz. |

**Belgenin doğru bildiği bir şey:** `CLAUDE_ANALYSIS_EFFORT=high` / `xhigh`. Efor
gerçekten var: `output_config: {effort: "low"|"medium"|"high"|"xhigh"|"max"}`,
varsayılan `high`. `AI_MODE: STANDARD|DEEP` eşlemesi mantıklı.

---

## 12. Bu depoyla çakışma

Belge `ibkr-ai-trader/` diye **yeni bir depo** kuruyor. Şu an bu depoda zaten var olanlar:

| Belgenin istediği | Bu depoda karşılığı |
|---|---|
| `TradeProposal` + `ApprovalRecord` + onay uçları | `bot/onay.py` — atomik `os.replace`, çift-tıklama korumalı, iki yaş sınırı (15 dk tazelik / 24 sa ömür), üç durum dosya adında görünür |
| `PortfolioSnapshot` / `Position` | `positions` tablosu — `sahip`, `account` (bux\|midas), `instrument_id`, para birimi |
| `Instrument` (+conid) | `instruments` (`symbol`,`venue`) + `identities` (CIK, borsa, çözüm yöntemi/durumu) |
| `RiskPolicy` / pozisyon boyutu | Yüzde tabanlı boyutlandırma + `koruma` tablosu (2N ATR stop, yalnız yukarı güncellenir) |
| `AnalysisRecord` | `analysis_runs` + `predictions` (tez, geçersizleşme koşulu, ufuk, güven) |
| Streamlit paneli | Telegram botu — doğal dil varsayılan, onay butonlu |
| `HistoricalSeries` | `prices` / `prices_hourly` + `db.fiyat_serisi()` (seri okumanın **tek meşru yolu**) |

İkinci bir kopya kurmak, [ayni-kural-iki-kopya] hafızasındaki vakayı tekrarlar:
LLY düzeltmesi `prices`'ta yapıldı, `identities`'teki ikizi SEC dosyalamalarını
sessizce düşürdü.

**Alternatif:** IBKR'yi **yeni bir toplayıcı + yeni bir hesap değeri** olarak ekle.

- `instruments.venue` → `IBKR` (veya `NASDAQ`/`NYSE`)
- `positions.account` → `ibkr` (üçüncü değer, `bux`/`midas` yanına)
- `identities` → `conid` sütunu (göç gerekir; [goc-kaliplari-ve-tuzaklari] hafızası:
  DDL geri sarılmaz, testler canlı db'yi göç ettirebilir)
- `collectors/ibkr.py` → `midas.py` ile aynı desen
- Emir gönderimi → yeni bir araç, `onay.py`'ye bağlı, **`tools.py`'de LLM'e açık değil**

Bu yol belgenin güvenlik mimarisini korur ama sıfırdan kurmaz.

---

## Kararlar

### Verilenler (2026-08-25)

| # | Karar | Sonucu |
|---|---|---|
| **K1** | **Baştan canlı işlem** | Kağıt ara katmanı yok. Belgenin "canlı yasak" mimarisi tersine döner — aşağıdaki §13'e bak. |
| **K2** | **Sabah bir giriş, gün içi otomatik** | Sen CPGW'yi açıp giriş yaparsın; bot `/tickle` ile günü tutar, düşerse Telegram'dan haber verir. Zamanlanmış koşularda IBKR adımı "oturum yoksa **atla ve söyle**". |
| **K4** | **Gerçek zamanlı veri** | §6'daki düzeltmeden sonra: ABD hisse/ETF için **ücretsiz** (non-consolidated). NBBO gerekirse $14,50/ay veya snapshot başına $0,01 (tavan yine $14,50). Karar **para kararı olmaktan çıktı**, teknik karara döndü — aşağıda K4b. |
| **K5** | **Bu depoya entegrasyon** | `collectors/ibkr.py` + `positions.account='ibkr'` + `identities.conid`. Onay/risk/analiz katmanları mevcut olanı kullanır. Emir aracı LLM'e **açılmaz**. |

### Sonradan kapananlar (2026-08-25, ikinci tur)

**K3. Ayrı kullanıcı adı — GEREKMİYOR.** Ali telefondan IBKR'ye bakmıyor. Tek
kullanıcı adı yeter. `competing: true` yine de izlenmeli (Client Portal'a
tarayıcıdan girmek de aynı etkiyi yapıyor).

**K4b. Başlangıçta ücretsiz akış.** Cboe One + IEX, non-consolidated, $0.
Günlük bar üzerinden RSI/MACD/SMA/ATR için fazlasıyla yeterli. Emir katmanı
gerçekten devreye girdiğinde tekrar bakılacak: limit fiyatı NBBO'ya bakmalı ve
$0,01'lik snapshot zaten consolidated — ayda ilk 100 bedava, tavan $14,50.
Kod `6509` alanını okuyup ne geldiğini raporlasın; karar ölçüme dayansın.

**K5b. Arayüz: Telegram.** Streamlit yok. Bu, K5'i (bu depoya entegrasyon)
teknik bir zorunluluğa çeviriyor — Telegram botu bu depo. Ayrıca tek brokerage
oturumu kuralı, IBKR bağlantısının **tek bir sürece** ait olmasını gerektiriyor;
iki uygulama aynı oturum için yarışır.

**K7. IBKR'de Borsa İstanbul — büyük ihtimalle YOK.** *(kanıt seviyesi: güçlü,
kesin değil)* IBKR'nin piyasa verisi fiyat sayfası tüm dünya borsalarını
bölge bölge listeliyor; EMEA bölümünde Bükreş, Budapeşte, Ljubljana, Prag,
Varşova, Tel Aviv gibi küçük piyasalar var ama **Türkiye/İstanbul/BIST hiç
geçmiyor** (`Turkey`, `Istanbul`, `BIST`, `TRY` → sıfır eşleşme).
Uyarı: bu bir *piyasa verisi* listesi, birebir *işlem ürünü* listesi değil.
Diğer iki IBKR sayfası (`products-stocks.php`, `f=exchanges`) JS ile
yükleniyor — oradaki "sıfır eşleşme" **kanıt sayılmaz** (Varşova ve Prag da
sıfır çıktı, yani sayfa boş geliyor).
**Sonuç:** IBKR katmanı pratikte ABD/global hisse evreni. Mevcut BIST/BUX
evreninden ayrı. KAP / İş Yatırım / TÜİK oraya uygulanmıyor; buna karşılık
EDGAR/SEC katmanı ve `identities.cik` zaten var.

**K8. Hesap: IBKR Pro ✅, bakiye ~6 EUR ⚠️**
Pro şartı sağlanıyor. Ama 6 EUR ile:
- IBKR kesirli hisse (fractional) destekliyor, **asgari 1 USD**'lik alım mümkün.
- Ama Fixed komisyon **emir başına asgari 1 USD**. 5 USD'lik emirde komisyon %20.
- Ayrıca $30/ay komisyon muafiyeti (NBBO bundle için) bu bakiyeyle imkânsız.

**6 EUR yatırım için değil, tek bir şey için yeter: zinciri gerçek parayla bir
kez uçtan uca kanıtlamak.** Emir katmanı bu yüzden inşa edilir ama son sıraya
konur; 1–5. adımlar (salt okuma + analiz) bakiyeden bağımsız olarak bugün
tam çalışır.

**K6. Emir onay mesajları: GÖSTER.** K1 canlı olduğu için IBKR'nin "fat finger"
uyarıları gerçek parayı koruyor; `/iserver/questions/suppress` ile bastırmak
bedava bir korumayı çöpe atmak olur. `onay.py` akışının parçası yapılacak.

---

## 13. K1 "canlı" kararının değiştirdikleri

Belge canlı işlemi mimari olarak yasaklıyordu; artık yasak kalkıyor. Ama **kalkan
tek şey yasak** — korumaların hepsi kalmalı, çoğu sıkılaşmalı:

1. **`ENABLE_LIVE_TRADING` bayrağı çöp.** Yerine **kanıt denetimi**: emir öncesi
   `/portfolio/accounts` çağır, dönen `accountId` beklenen hesap mı? Ortam
   değişkeni değil, sunucudan dönen kimlik. [pozisyon-yazma-semantigi] ilkesi.
2. **Onay ömrü kısalmalı.** `onay.py` butonları 24 saat yaşıyor — bir gün önceki
   okuma için doğru, **canlı emir için felaket**. Emir onayları dakikalar
   mertebesinde olmalı + onay anındaki fiyata karşı kayma (slippage) eşiği.
3. **Çift emir riski artık gerçek para.** Zaman aşımı → `EXECUTION_STATE_UNKNOWN`
   → `/iserver/account/orders` ile mutabakat. Belgenin bu kısmı doğruydu, şimdi
   *zorunlu*.
4. **Emir onay mesajları gösterilmeli** (K6). IBKR'nin koruması bedava, atmayalım.
5. **Kağıt hesap kaybolmuyor** — aynı kod, farklı kullanıcı adı. Prova için
   kullanılabilir; hangisine bağlı olduğunu `.env` değil, `DU`/`DEMO` kanıtı söyler.
6. **Emir aracı `tools.py`'de LLM'e açılmaz.** Belgenin altın kuralı burada aynen
   geçerli: LLM önerir, risk motoru izin verir, **yalnızca insan onaylar**.

---

## 14. Bir sonraki dilim

Sıra (K2 gereği her adım "oturum yoksa atla ve söyle" davranışında):

1. **CPGW kurulumu** — Java, port değişikliği (macOS 5000 çakışması), giriş,
   `scripts/ibkr_baglanti.py` ile `authenticated / connected / competing` raporu.
2. **Oturum katmanı** — `/tickle` döngüsü, `ssodh/init`, düşüş tespiti + Telegram
   bildirimi. [kesinti-izleme] kanallarına eklenir.
3. **Salt okuma** — `/portfolio/accounts` → `/portfolio2/{id}/positions` +
   `/ledger` + `/summary`. `positions` tablosuna `account='ibkr'` olarak yazılır,
   para birimi ayrımıyla ([para-birimi-ve-sembol-tuzagi]).
4. **Enstrüman kimliği** — `/trsrv/stocks` → conid; `identities` tablosuna göç.
5. **Piyasa verisi** — ön-uçuş + oku + **abonelikten çık**, hat sayacı, `6509`
   → veri kalitesi.
6. **Emir katmanı** — en son, ayrı bir gözden geçirmeyle.

Emir katmanına geçmeden önce 1–5 çalışmalı ve testleri olmalı.

---

## Ek: doğrulanmış uç haritası

Taban URL: `https://localhost:5000/v1/api` (CPGW)

```
Oturum
  POST /iserver/auth/status                  kimlik + competing bayrağı
  POST /iserver/auth/ssodh/init              {"publish":true,"compete":true}
  POST /tickle                               ~60 sn'de bir
  POST /logout

Kimlik
  GET  /trsrv/stocks?symbols=AAPL            sembol → conid (piyasa başına ayrı conid)
  GET  /iserver/accounts                     snapshot'tan ÖNCE zorunlu

Piyasa verisi
  GET  /iserver/marketdata/snapshot?conids=&fields=   ön-uçuş + ikinci çağrı
  GET  /iserver/marketdata/history?conid=&period=1y&bar=1d   ≤1000 bar, 5 eşzamanlı

Portföy
  GET  /portfolio/accounts                   diğer /portfolio çağrılarından ÖNCE
  GET  /portfolio2/{accountId}/positions     önbelleksiz pozisyonlar
  GET  /portfolio/{accountId}/ledger         para birimi bazlı nakit + BASE
  GET  /portfolio/{accountId}/summary        özsermaye + teminat

Emir
  POST   /iserver/account/{accountId}/orders          gövde JSON DİZİSİ
  POST   /iserver/reply/{messageId}                   {"confirmed":true}
  POST   /iserver/questions/suppress                  {"messageIds":["o163"]}
  POST   /iserver/account/{accountId}/order/{orderId} değiştir — gövde JSON NESNESİ
  DELETE /iserver/account/{accountId}/order/{orderId} iptal talebi (iptal ≠ garanti)
  GET    /iserver/account/orders                      açık emirler (1/5sn)
  GET    /iserver/account/{accountId}/order/status/{orderId}
```
