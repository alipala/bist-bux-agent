# finagent — Analitik Güç Değerlendirmesi ve Geliştirme Listesi

Dış inceleme. Kaynak: repo (README, `src/finagent/**`, `config/settings.yaml`, `tests/test_smoke.py`).
Amaç: ajanın **(a) geçmişe bakma gücü, (b) analiz gücü ve tutarlılığı, (c) danışman olarak
tavsiye kalitesi** üzerine somut, uygulanabilir geri bildirim.

---

## 0. Önce hakkını verelim

Bunlar tartışılmasın, korunsun — çoğu projede olmayan şeyler:

- **Deterministik katman / LLM katmanı ayrımı.** RSI, SMA, CAR, ağırlık pandas'ta hesaplanıyor;
  model okuyor. LLM düşerse rapor yine çıkıyor (`_fallback_note`).
- **Tek gösterge motoru.** Tarayıcının kendi RSI'ı silinmiş; `_gosterge()` proje motorunu çağırıyor.
  Bu, ölçülmüş bir hata sınıfının kapatılması (MSFT 84.8 vs 70.9).
- **Kimlik ada göre doğrulanıyor.** Avantium/Avalo vakası doğru teşhis: yanlış veri eksik veriden kötü.
- **Kademeli kaynak (allowlist).** Kademe 3-4'ün kanıt sayılmaması, LLM finans araçlarının
  en yaygın hatasını baştan kesiyor.
- **Olay çalışmasında piyasa modeli + örtüşen pencere tekilleştirme.** ADYEN vakası doğru yakalanmış.
- **Tahmin defteri.** Kendi isabetini ölçmeye çalışan sistem sayısı çok az. Ham getiri yerine
  anormal getiriyle puanlama, Wilson aralığı, n<20'de "sonuç çıkarma" uyarısı — hepsi doğru.
- **`can_use_tool` kapısı.** `allowed_tools`'un güvenlik sınırı olmadığının ölçülmüş olması ve
  gerçek kapının konması, ileride emir gönderebilen bir MCP bağlanırsa hayat kurtarır.
- **README §11 "Return expectations are bounded by arithmetic".** Bir aracın kullanıcısına
  "aylık %20-30 hedefi Sharpe 21 ister" demesi, bu projenin en değerli cümlesi.

Aşağıdakiler bu temelin üstüne yazılmış; temeli değiştirme önerisi değil.

---

## 1. Geçmişe bakma gücü — **zayıf**. En büyük açık burası.

### 1.1 Fiyat geçmişi tek rejim içinde
| Kaynak | Derinlik |
|---|---|
| Yahoo (`sources.prices.range`) | `2y` |
| Binance | `daily_bars: 1000` (~2.7 yıl) |
| İş Yatırım | `lookback_days: 250` × 1.6 ≈ 410 gün |
| CoinGecko bileşiği | en fazla 1 yıl (kodda not düşülmüş) |
| Analiz pencereleri | screener 300 bar, defter 400, olay çalışması 1000 |

Yani elde **2024-2026 arası tek bir rejim** var. 2022 ayı piyasası, 2020 çöküşü,
2018 TRY şoku hiçbir seride yok. Sonuç: sistem "bu kurulum daha önce ne yaptı"
sorusunu **hiçbir zaman** cevaplayamaz — ne kendi kendine, ne sorulduğunda.

### 1.2 Kendi geçmişine bakamıyor
`predictions`, `signals`, `analysis_runs` tabloları doluyor ama **hiçbir araç bunları okumuyor.**
`ARAC_ADLARI` içinde `karne`, `tahmin_gecmisi`, `gecmis_analiz` yok. Pratik sonuç:

> "İki hafta önce NVDA için ne demiştin, tuttu mu?" → model cevaplayamaz.

Sohbet hafızası da son 8 tur + asistan cevabı 1500 karaktere kırpılmış. Yani ajanın
**hiçbir kalıcı kurumsal hafızası yok**; sadece bir veri tabanı sorgulayıcısı.

### 1.3 Karne ölçülüyor ama geri beslenmiyor — döngü açık
`runner.calistir()`: `karne = defter.puanla()` → Telegram mesajının altına yazılıyor.
`Panel.calistir()` ve `_hakem()` karneyi **görmüyor**. Yani sistem kendi isabetini
kullanıcıya raporluyor ama **kendisi öğrenmiyor**. Defterin varlık sebebi buysa,
şu an yarısı çalışıyor.

### 1.4 Backtest yok
`screener.py` docstring'i: *"tarayıcı çıktısı TEKRARLANABILIR... dolayısıyla geçmişe dönük
test edilebilir."* Doğru — ama **test eden kod yok**. Sonuç: `rsi_ucu`, `sma50_kirilimi`,
`hacim_anomalisi`, `olagandisi_hareket`, `olay_etkisi` sinyal tiplerinin **taban oranı bilinmiyor.**
Panel bu sinyalleri yorumlarken elinde tek prior yok; her sinyale sıfırdan bakıyor.

### 1.5 Makro / rejim katmanı hiç yok
Faiz, TÜFE, VIX, DXY, US 10y, tahvil eğrisi, TCMB politika faizi — hiçbiri yok.
`fx_rates` sadece EUR/USD ve USD/TRY. Bunun iki somut maliyeti var:

- **BIST tarafı sistematik olarak yanlış okunuyor.** TL enflasyonu altında nominal
  getiri anlamsız. XU100 vekili var ama reel getiri ve USD bazlı getiri yok.
  "THYAO %40 yükseldi" cümlesi, aynı dönem TÜFE %35 ise bir şey söylemiyor.
- **Rejim körlüğü.** VIX 12 iken ile VIX 35 iken aynı RSI eşiği kullanılıyor.

---

## 2. Analiz gücü ve tutarlılık — **iyi iskelet, ölçüm katmanı eksik**

### 2.1 Defter, kullanıcının okuduğu şeyi ölçmüyor  ⚠️ kritik
- Kullanıcıya giden çıktı **hakem özeti**. Puanlanan şey **ajanların JSON'u**.
  Hakem bastırıyor/öne çıkarıyor ama **hiç puanlanmıyor**. Yani karne, kullanıcının
  gördüğü tavsiyenin isabetini ölçmüyor.
- `Defter.kaydet()` aynı `(instrument_id, ufuk)` için **en yüksek güvenli görüşü tutup
  diğerlerini atıyor.** Bu üç şeyi bozuyor:
  1. Projenin kendi ilkesi olan *"çelişki en değerli çıktıdır"* deftere hiç geçmiyor.
  2. `ajan_karnesi()` `gerekce LIKE '[ajan]%'` ile çalıştığı için **sadece hayatta kalan
     tahminleri** sayıyor → ajan karneleri yapısal olarak yanlı.
  3. Yüksek güven sistematik olarak seçildiği için karne, panelin değil **en iddialı ajanın**
     karnesi oluyor.

### 2.2 JSON ayrıştırma sessizce başarısız oluyor  ⚠️
`_json_cek()` üç kalıbı deneyip `{}` dönüyor. O turdaki tahminler **deftere hiç girmiyor**,
ve bunun **sayacı yok, logu yok, retry'ı yok**. Seçilim yanlılığı: formatı bozan koşular
ölçüm dışı kalıyor. Yorumun uzun/karmaşık olduğu (yani belirsiz) durumlarda formatın bozulma
olasılığı daha yüksekse, karne sistematik olarak iyimser çıkar.

### 2.3 Kalibrasyon hiç ölçülmüyor
`guven` (0-1) kaydediliyor ama `karne()` güven kovalarına ayırmıyor. Elde zaten veri var,
tek eksik iki sorgu:
- Güven kovası bazında isabet (0.5-0.6 / 0.6-0.7 / 0.7-0.8 / 0.8+) — kalibrasyon eğrisi.
- **Brier skoru** — ikili isabetten çok daha bilgilendirici, çünkü aşırı güveni cezalandırıyor.

Ayrıca isabet ikili: büyüklük yok, işlem maliyeti yok, `ortalama_anormal_getiri_%` var ama
dağılımı/t-testi yok. "%55 başabaş" notu doğru ama ölçüm o notu test edecek biçimde değil.

### 2.4 Temel analiz aritmetiği LLM'de — kendi ilkesine aykırı  ⚠️
`chat.py` kural 11: *"TTM = yıl + yeni çeyrek - geçen yılın aynı çeyreği"* — bu bir **hesap
talimatı**, modele veriliyor. Aynı prompt'un kural 10'u ise "gostergeleri yeniden hesaplama,
yorumla" diyor. Çelişki. Sonuç:

- F/K, EV/FAVÖK, marj trendi, ROE, net borç/FAVÖK **hiçbir yerde deterministik hesaplanmıyor.**
- `analysis/` altında `valuation.py` yok. `temel` ajanı XBRL ham değerlerinden kafadan oran çıkarıyor.
- "Hesabını GÖSTER" kuralı bunu güvenli hale getirmiyor; sadece görünür yapıyor.

### 2.5 Portföy riski anlatılıyor, hesaplanmıyor  ⚠️
`risk` ajanına *"aynı yöne bakan pozisyonlar, para birimi uyumsuzluğu"* deniyor ama:
- **Korelasyon aracı yok.** Model korelasyonu "tahmin ediyor" (NVDA ve ASML ikisi de yarı iletken →
  çıkarım, ölçüm değil).
- Portföy oynaklığı, max drawdown, benchmark'a göre getiri, katkı analizi (contribution),
  faktör maruziyeti — hiçbiri yok.
- `portfolio.py` hâlâ `"FX dönüşümü v2'de eklenecek"` diyor; oysa `fx_rates` tablosu ve
  `fx` aracı **var**. Aynı hatanın prompt versiyonu için bkz. 2.6.
- ETF look-through yok (README'de bilinen sınır olarak dürüstçe yazılmış) → gerçek sektör
  maruziyeti ölçülemiyor, sadece çıkarılıyor.

### 2.6 Prompt sürüklenmesi — kurallar üç yerde kopyalanmış ve ayrışmış  ⚠️
Kademe kuralları `strategist.py`, `chat.py`, `pulse/agents.py` içinde ayrı ayrı yazılı.
Sürüklenme başlamış, iki somut örnek:

| Yer | Ne diyor | Gerçek |
|---|---|---|
| `chat.py` kural 9 | "FX serisi veride **YOK**" | `fx` aracı ve `fx_rates` tablosu var. `tools.py` bu hatayı **zaten bir kez düzeltmiş** (koşulsuz uyarı → koşullu), prompt'ta kalmış. Model aynı cevapta hem "FX yok" deyip hem kuru kullanabilir. |
| `strategist.py` | "BIST hisseleri ve Avrupa ETF'leri üzerine çalışıyorsun" | Portföyün önemli kısmı **kripto**. Günlük rapor şablonunda kripto bölümü yok, tokenomik yok, 7/24 uyarısı yok. |

Günlük rapor şablonunda ayrıca **karne bölümü** ve **"düne göre ne değişti"** bölümü yok.
İkincisi, "geçmişe bakma" sorusunun en ucuz cevabı.

### 2.7 Persona tutarsızlığı
- `strategist.py` kural 3: *"Kesin al/sat tavsiyesi verme."*
- `chat.py` kural 18: *"Ali senden GÖRÜŞ istiyor ve görüş VER. Kaçamak yapma."*
- `HAKEM`: emir önerme, gözlem ver.

Üç katman, üç farklı danışman. Kullanıcı açısından aynı sistemin üç ağzı var. Bilinçli bir
seçimse yazılmalı; değilse tek politika olmalı.

---

## 3. Tavsiye kalitesi — **dürüst, ama çözünürlüğü düşük**

Doğru olanlar: emir yok, kaldıraç önerisi yok, korelasyon≠nedensellik disiplini, sessizliğin
geçerli çıktı olması, getiri beklentisi aritmetiği.

Eksikler:

1. **Taban oran yok.** Bir analistin en güçlü cümlesi *"bu kurulum bu evrende tarihsel olarak
   yazı-tura"* cümlesidir. Sistem bunu söyleyemiyor (bkz. 1.4).
2. **Tez kaydı yok.** `predictions` sadece yön + güven + ufuk tutuyor. Şunlar yok:
   giriş gerekçesi, **geçersizleşme koşulu**, izlenecek eşik, tezin ömrü.
   Dolayısıyla **"tez bozuldu" bildirimi de yok** — halbuki bu, günlük sinyal üretmekten
   çok daha yüksek beklenen değerli bir çıktı.
3. **Ufuk sabit 5 gün.** Temel analize dayalı bir görüşün ufku 5 gün olamaz; olay etkisininki
   5 gün olabilir. Ajan bazında varsayılan ufuk farklı olmalı.
4. **Maliyet yok.** Komisyon, spread, BUX/Midas ücretleri, kur dönüşüm maliyeti, TR'de stopaj —
   hiçbiri hesaba girmiyor. "%55 başabaş" notu doğru ama sisteme gömülü değil.
5. **Pozisyon bağlamı yok.** Bilinçli olarak boyut önerilmiyor — doğru. Ama "bu enstrümanın
   günlük oynaklığı %5.7, portföyünün %X'i zaten aynı faktörde" bilgisi verilmeden çıplak
   gözlem sunmak, kullanıcıyı boyutlandırmada yalnız bırakıyor.

---

## 4. Yapılacaklar — öncelik sırasıyla

### P0 — Ölçüm döngüsünü kapat (bunlar olmadan diğerleri körlemesine)

**P0-1. Backtest harness.**
`run.py backtest --tur rsi_ucu --ufuk 5 --baslangic 2024-01-01`
Depolanmış fiyatlardan, `screener.py`'nin **aynı fonksiyonlarıyla** geçmiş günleri yeniden tarat,
her sinyal tipi için anormal getiri dağılımı çıkar (n, medyan, ortalama, %25/%75, isabet oranı,
Wilson aralığı). Sonuç yeni bir `signal_stats` tablosuna.
→ Panel ve sohbet için `taban_oran(tur, venue)` aracı.
*Kritik uyarı: look-ahead yasak. O gün mevcut olan bar dışında hiçbir şey kullanılmamalı;
`fiyat_serisi` çağrılarına tarih üst sınırı parametresi eklenmesi gerekebilir.*

**P0-2. Defteri düzelt.**
- `predictions` birincil anahtarına `ajan` ekle → her ajanın görüşü ayrı kaydedilsin, çelişki korunsun.
- Hakemin nihai çağrısını `ajan='hakem'` olarak ayrıca kaydet → **kullanıcının okuduğu şey ölçülsün**.
- `_json_cek()` boş dönerse: sayaç + `WARNING` log + 1 retry ("yalnızca JSON blogunu tekrar ver").
  Kaç turda kaybedildiğini `collector_runs` benzeri bir tabloya yaz.
- `karne()`'ye güven kovası kırılımı + **Brier skoru** ekle.

**P0-3. Karneyi panele geri ver.**
`ORTAK_KURALLAR`'a çalışma anında enjekte et:
> "Bu sinyal tipindeki geçmiş isabetin: n=37, %51 (aralık %35-%67). Örneklem yetersizse
> bunu bir bilgi olarak kullan, gerekçe olarak değil."

**P0-4. Geçmiş araçları.** `tahmin_gecmisi(sembol)`, `karne(gun)`, `gecmis_analiz(tarih|sembol)`,
`sinyal_gecmisi(sembol)`. Böylece "iki hafta önce ne demiştin, tuttu mu" cevaplanabilir hale gelir.

### P1 — Analitik derinlik

**P1-5. `analysis/valuation.py`.** TTM, F/K, EV/FAVÖK, brüt/faaliyet/net marj trendi, ROE,
net borç/FAVÖK, nakit akışı/net kâr. Dönem uzunluğu kontrolü **kodda**, prompt'ta değil.
`finansallar` aracı ham değer yerine hesaplanmış oranları dönsün. `chat.py` kural 11 silinsin.

**P1-6. `analysis/risk.py`.** Pozisyonlar arası korelasyon matrisi (90/250 gün), portföy
oynaklığı, max drawdown, benchmark'a göre getiri ve katkı analizi.
→ `korelasyon(semboller)` ve `portfoy_risk()` araçları. `risk` ajanı artık ölçüyor, tahmin etmiyor.

**P1-7. Makro katman.** Yeni collector: TCMB politika faizi + TÜİK TÜFE (BIST reel getiri için),
US 10y, VIX, DXY, EUR/TRY. → `makro()` aracı + BIST için **reel ve USD bazlı getiri** kolonu.

**P1-8. Tarihi derinleştir.** Tek seferlik dolum scripti:
İş Yatırım 5+ yıl, Yahoo `range: "10y"`, Binance sayfalama ile 1000 bar sınırının ötesi.
Hedef: her enstrümanda **en az bir düşüş rejimi** bulunsun. Backtest (P0-1) bunsuz anlamsız.

**P1-9. ETF look-through.** iShares/Vanguard günlük holdings CSV'leri public.
CNDX/VUSA/RBOT bileşenleri çekilirse README §11'deki bilinen sınır kapanır ve gerçek
sektör/isim yoğunlaşması **ölçülebilir** hale gelir.

### P2 — Tavsiye kalitesi

**P2-10. Tez kaydı ve bozulma alarmı.** `predictions`'a `tez`, `gecersizlesme_kosulu`
(makine-okunur, ör. `close < 142.5` veya `rsi14 > 75`), `izlenecek_esik` ekle.
Her gün deterministik kontrol → koşul tetiklenirse bildirim: *"NVDA tezi bozuldu: kapanış 141.2 < 142.5."*
Bu, günlük yeni sinyal üretmekten daha değerli.

**P2-11. Prompt konsolidasyonu.** `src/finagent/prompts/` paketi:
`kaynak_kademesi.py`, `para_birimi.py`, `belirsizlik.py`, `olay_etki.py` — tek kaynak, rol bazlı
kompozisyon. Aynı anda temizlenecek bayat satırlar: `chat.py` kural 9 ("FX yok"),
`strategist.py` başlığı (kripto eklenmeli), `portfolio.py` "FX v2'de" notu.
Günlük rapor şablonuna **`## Kripto`**, **`## Karne`** ve **`## Düne Göre Ne Değişti`** bölümleri.

**P2-12. Haftalık post-mortem.** Puanlanmış tahminleri okuyup sistematik hata örüntüsü arayan
ayrı bir koşu ("kripto tarafında sürekli yukarı diyorsun", "RSI ucunda isabet %38").
Küçük örneklem uyarısı zorunlu.

**P2-13. Olay çalışmasında yayın saati.** Şu an olay günü `published_at`'in tarih kısmından
alınıyor. Kapanış sonrası çıkan haberde t günü getirisi haberden **önce** oluşmuş oluyor.
Borsa saatine göre kapanış sonrası haberleri t+1'e kaydır (KAP ve SEC'te saat bilgisi var).

**P2-14. Maliyet modeli.** `config`'e komisyon/spread/stopaj; karne "brüt isabet" yerine
**maliyet sonrası beklenen değer** de raporlasın.

---

## 5. Claude Code'a sorular

Bunlar repoyu ve **veriyi** gören taraftan cevap ister; dışarıdan tahmin edilemez.

### Veri gerçekliği
1. `prices` tablosunda enstrüman başına bar sayısı: min / medyan / maks, kaynak bazında dağılım nedir?
   Kaç sembolde **500 bardan az** var? Backtest için kullanılabilir evren kaç enstrüman?
2. Portföydeki ve izleme listesindeki her enstrüman için en eski bar tarihi nedir?
   En az bir düşüş rejimi içeren kaç sembol var?
3. `fiyat_kaynagi()` çoklu kaynak seçimi kaç enstrümanda devreye giriyor?
   Hangi sembollerde farklı para biriminde iki seri var ve seçim doğru mu?
4. Kimliği `eslesmedi` olan kaç enstrüman var? Bunlar taranabilir evrenin yüzde kaçını kör bırakıyor?

### Ölçüm sağlığı
5. `predictions` tablosunda şu an kaç kayıt var, kaçı puanlanmış? Ajan dağılımı ne?
   "En yüksek güvenli görüşü tut" kuralı yüzünden **kaç görüş atıldı** (loglardan veya
   panel çıktılarından çıkarılabilir mi)?
6. `_json_cek()` şimdiye kadar kaç turda boş döndü? `data/bot.log` ve pulse loglarında
   ajan çıktısı var ama tahmin yazılmamış koşuları sayabilir misin?
7. Panel koşusu başına kaç araç çağrısı yapılıyor, hangi araçlar **hiç** çağrılmıyor?
   (Hiç çağrılmayan araç = kötü tanımlanmış araç; ya açıklaması ya varlığı yanlış.)
8. Screener sinyal tiplerinin son 30 gündeki dağılımı: tür bazında kaç sinyal üretildi,
   kaçı 0.55 eşiğini geçti, kaçı tahmine dönüştü? Hangi tür panelin zamanını en çok yiyor?

### Bütçe ve fizibilite
9. Abonelik kullanımı: pulse (~9 dk) + günlük sohbet şu an limitin ne kadarını yiyor?
   P0-1 (backtest) ve P1-7 (makro) eklenince bütçe tutar mı? Backtest LLM'siz olduğu için
   maliyeti sıfır olmalı — doğrulayabilir misin?
10. P0-1'i look-ahead sızdırmadan yazmak için `db.fiyat_serisi()`'ne tarih üst sınırı eklemek
    gerekiyor mu, yoksa mevcut imza yetiyor mu? Kaç çağrı noktası etkilenir?

### Öncelik ve mimari
11. Bu 14 maddeden hangileri **tek dosyada** biter, hangileri şema göçü (migration) ister?
    Şema değişikliği gerektirenler için mevcut `predictions` verisi korunabilir mi?
12. Sıralama konusunda hemfikir misin? Benim önerim: **P0-2 (defter) → P1-8 (tarih dolumu) →
    P0-1 (backtest) → P0-3 (geri besleme) → P0-4 (geçmiş araçları)**. Backtest'i tarih dolumundan
    önce yapmak anlamsız görünüyor; katılıyor musun, yoksa mevcut 2 yıllık veri ilk sonuç için yeterli mi?
13. P2-11 (prompt konsolidasyonu) refactor riski taşıyor — mevcut smoke testler prompt
    değişikliğini yakalar mı? Prompt'a özel test yazmalı mıyız (ör. "kademe kuralları üç katmanda
    da aynı metinden geliyor" testi)?
14. Persona sorusu (bkz. §2.7): strateji katmanı tavsiye vermiyor, sohbet veriyor. Bu bilinçli mi?
    Tek politikaya indirilmeli mi, yoksa "günlük rapor = gözlem, sohbet = görüş" ayrımı korunmalı mı?
15. Senin bu listede **katılmadığın** madde var mı? Kod tarafında görüp benim dışarıdan
    kaçırdığım, bu önceliklendirmeyi değiştirecek bir şey var mı?

---

## 6. Tek cümlelik özet

Sistem **veri toplama, doğruluk disiplini ve dürüstlük** tarafında olması gerekenin üstünde;
**kendi geçmişini kullanma, taban oran üretme ve söylediğini ölçme** tarafında henüz yarı yolda.
Defterin varlığı doğru içgüdü — eksik olan, o defterin hem panele geri beslenmesi hem de
kullanıcının gerçekten okuduğu çıktıyı (hakem özetini) ölçmesi.
