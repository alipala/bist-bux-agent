# Durum — defter turu kapandı, sırada ölçüm katmanı

Tarih: 2026-08-16 · Son commit: `b5d41fb` · 98 duman testi geçiyor
Kaynak belgeler: `finagent-analiz-feedback.md` → `finagent-analiz-degerlendirme.md`
→ `finagent-analiz-konsensus.md` → `finagent-analiz-konsensus-yanit.md`
→ `finagent-goc-inceleme.md`

Bu belge **ne bitti, ne kaldı** sorusunu cevaplıyor. Amaç dış incelemecinin
baştan okumadan sıradaki turu planlayabilmesi.

---

## 1. Bu turda yapılanlar

### 1.1 Kripto evreni (`f4cf1d3`, `6d2bf55`) — incelemeden önce

Kripto tarafı 10 coin'e sıkışmıştı; evren artık piyasa değeri sıralamasından
geliyor.

| | Önce | Şimdi |
|---|---|---|
| Alınabilir coin | 10 | **46** (Binance, 3 yıl günlük + saatlik) |
| Referans coin | — | **21** (`venue='CRYPTO'`, CoinGecko, 1 yıl) |
| Fiyat barı | ~17k | **127.262** · birimsiz **0** |

Üç süzgeç de kaynaktan: işlem görürlük `exchangeInfo`'dan, stablecoin
CoinGecko kategorisinden, likidite iki farklı ölçütle (alınabilirde mutlak
hacim, referansta devir hızı — LEO 8,6 mlr $ ama günde %0,006 döner).

Referans coin'ler `alinabilir: false` bayrağı taşıyor: taranıyor ama tavsiye
konusu olamıyor. Ek olarak BUX'ta serisi olmayan 4 pozisyon çözüldü (%22,25),
her biri portföyün ima ettiği birim fiyatla doğrulanarak — bu test CNDX.AS'i
eledi (adı uyuyor, fiyatı 5,89 kat sapıyor), doğrusu EXXT.DE.

### 1.2 Defter göçü (`ff774a1`) — P0-2

`predictions` benzersizliğine `ajan` girdi. Tablo yeniden kurulup kopyalandı
(SQLite kısıt değiştiremiyor), 26 kayıt korundu, `ajan` gerekçe önekinden
geri kazanıldı.

Eklenen kolonlar: `ajan`, `signal_id`, `tez`, `gecersizlesme_kosulu`,
`izlenecek_esik`. Son üçü P2-10 için şimdiden açık.

Yeni `panel_runs` tablosu: her ajanın **ham metni**, JSON durumu, görüş
sayısı, atılan sayaçları, tanısal not.

Hakem artık yapısal çıktı veriyor ve `ajan='hakem'` olarak ayrıca puanlanıyor.
`ajan_karnesi` `gerekce LIKE` yerine kolondan okuyor.

**Kanıt (gerçek koşu):** çelişki korundu.
```
AMZN    temel:yukari  ↔  hakem/olay/teknik/risk:notr
EGGUB   teknik:yukari ↔  olay:notr
GOODY   teknik:yukari ↔  hakem/olay:notr
```
Eski şemada AMZN tek satıra çökerdi.

### 1.3 Kümelenme düzeltmesi (`153860d`) — kendi bulduğumuz kusur

`ajan` anahtara girince aynı fiyat hareketi hakkında 5 tahmin oluşabildi.
Karne bunları bağımsız gözlem sayıyordu: **56 tahmin, 26 farklı (enstrüman,
gün) kümesi.** Wilson aralığı ~1,47 kat dar çıkıyordu.

Karne artık yalnızca hakemi ölçüyor — hem istatistiksel olarak temiz
(enstrüman-gün başına tek çağrı) hem de kullanıcının okuduğu şey. Sonuç
`bagimsiz_kume` alanını da döndürüyor: bağımsızlık bir daha kırılırsa iki
sayı tutmaz.

### 1.4 Göç güvenliği (`79f57de`) — inceleme §1a, §1b

- `try/finally`: FK denetimi bağlantı ömrü boyunca kapalı kalamaz
- Sayım denetimi işlemin içinde ve `DROP`'tan önce → `raise` geri sarıyor

**Bunu kanıtlarken üçüncü kusur çıktı:** Python `sqlite3` eski kipte
(`isolation_level=''`) işlemi yalnızca DML için açıyor; `ALTER`/`CREATE`
otomatik commit oluyordu. Arıza enjekte edildiğinde:
```
predictions       0 kayit   ← canli tablo BOS
predictions_eski  3 kayit   ← veri burada
```
Ve az önce eklediğim "artık tabloyu sil" temizliği bir sonraki açılışta o
3 kaydı **kalıcı olarak silecekti**. Kurtarılabilir bir yarım göç, veri
kaybına dönüşüyordu.

Düzeltme: göç kendi işlemini açıyor (`BEGIN IMMEDIATE`); artık tablo
silinmiyor **inceleniyor** ve canlıdan çok kayıt tutuyorsa geri alınıyor;
kurtarma "zaten göç edilmiş mi" kontrolünden **önce** çalışıyor.

### 1.5 Kalan inceleme maddeleri (`b5d41fb`) — §1c, §2, §3, §4, §5

| Madde | Ne yapıldı |
|---|---|
| §1c | Göç şeması ↔ `schema.sql` özdeşlik testi (ikisi de kurulup `PRAGMA table_info` karşılaştırılıyor) |
| §2 | Sessizlik (`json_durum='ok' AND gorus_sayisi=0`) ve JSON⊆özet ihlali `panel_runs.hata`'ya yazılıyor; taşma kontrolü büyük harf duyarlı |
| §3 | `_gonder()` artık `karne["not"]`'u kullanıyor; `olculen` → `olculen_toplam`; **hakem–panel sapma metriği** eklendi |
| §4 | `atilan_*` sayaçları `panel_runs`'a UPDATE ediliyor |
| §5 | n=1'de aralık genişliği testi **ayrı teste** geri kondu |

---

## 2. Şu anki ölçülen durum

```
tahmin        64  (puanlanmis 0 — hepsi 15-16 Agu, ufuk 5 gun)
ajan dagilim  risk 20 · teknik 20 · hakem 8 · olay 8 · temel 8
signal_id     31 tahmin sinyale bagli
tez dolu      8   (hakem cagrilari)
panel_runs    10  (2 kosu x 5 ajan)
signals       166
fiyat bari    127.262 · birimsiz 0
testler       98
```

**Ölçüm döngüsü hâlâ meyve vermedi.** İlk puanlama 20 Ağustos civarı.

---

## 3. Kalanlar — mutabık kalınan sırayla

### 3.1 Hemen sıradaki

**A. İş Yatırım derinlik ölçümü.** P1-8'in takvimi buna bağlı ve şu an
**bilinmiyor** — incelemede 5+ yıl varsayılmıştı, dayanağı yoktu (yanıt
belgesinde kabul edildi). Ölçülecek tavanlar:

| Kaynak | Durum |
|---|---|
| Binance klines | 2017'ye kadar var, sayfalama yazıldı → 8 yıl mümkün, ucuz |
| Yahoo `range: "10y"` | denenmedi |
| İş Yatırım | **bilinmiyor — ölçülecek** |
| CoinGecko ücretsiz | 365 gün (ölçüldü: 1095 gün → HTTP 401) |

Hedef "her enstrümanda bir düşüş" değil (285/300 sembolde zaten var),
**en az iki farklı makro rejim**. BIST kritik: 251 sembolde 13,5 ay.

**B. Backtest (P0-1).** Derinlik dolumundan sonra. İki tasarım kararı
mutabık:
- `db.fiyat_serisi()`'ne opsiyonel `bitis` parametresi (look-ahead; 13 çağrı
  noktası, hiçbiri değişmez)
- İlk çıktı isabet tablosu **değil güç analizi**: tür × venue kırılımında kaç
  sinyal-günü var, hangi hücre konuşulabilir. `signal_stats`'ta `n` ve
  `guven_araligi` zorunlu alan; `taban_oran` aracı aralığı da döndürsün.
  BIST hücresinin "ölçülemez" çıkması muhtemel ve doğru cevap.

**C. P0-3 (geri besleme).** Kaynağı defter değil **`signal_stats`** (yanıt
belgesi §1.1'de düzeltildi) — backtest bittiği gün hazır. Bilinmezlik beyanı
**her zaman** enjekte edilir, nokta tahmini n≥20'de. Tetikleyici: herhangi bir
sinyal tipinde n≥20 olduğu gün defter kaynağı da devreye girer.

### 3.2 Sonraki

- **P0-4** geçmiş araçları: `tahmin_gecmisi`, `karne`, `gecmis_analiz`,
  `sinyal_gecmisi`, `taban_oran`
- **P1-5** `analysis/valuation.py` — TTM/F-K/EV-FAVÖK kodda, prompt'ta değil.
  Ek gerekçe: `finansallar` aracı kayıtlı 14 turda **hiç çağrılmadı**
- **P1-6** `analysis/risk.py` — korelasyon ölçülsün, tahmin edilmesin
- **P1-7** makro katman (TCMB, TÜFE, VIX, DXY, US 10y) → BIST reel getiri
- **P1-9** ETF look-through (iShares/Vanguard holdings CSV public)
- **P2-10** tez + bozulma alarmı — **kolonlar hazır**, yazma/kontrol mantığı
  kaldı
- **P2-11** prompt konsolidasyonu; yetenek listesi **üretilsin**, elle
  yazılmasın. Bayat satırlar hâlâ duruyor: `chat.py` kural 9 ("FX yok" —
  `fx` aracı var), `strategist.py` başlığı (kripto yok),
  `portfolio.py:56` ("FX v2'de")
- **P2-12** haftalık post-mortem
- **P2-13** olay yayın saati kaydırması — **veri elde** (kademe 1'de 11/11,
  kademe 2'de 62/63 saatli)
- **P2-14** maliyet modeli

### 3.3 Ölü konfigürasyon

Kodun okuduğu 62 ayar yolunun **22'sinin YAML'da karşılığı yok**. İki tür:
yanlış yol (`sources.binance.*` ↔ `watchlist.binance.*` — ayarlanabilir
görünen ama ayarlanamayan düğme) ve hiç tanımlanmamış (Alpha Vantage'ın 6
anahtarı dahil).

Mutabakat: çıktı temizlik değil **değişmez test** olmalı — `s.get()` ile
okunan her yol ya YAML'da tanımlı ya açıkça kayıtlı bir kod varsayılanı.

**Pratik sonuç:** Alpha Vantage `daily_budget` düğmesi olmadığı için kota
aşımı ayarla çözülemiyor; collector her gece `error` dönüyor. Veri deliği
**yok** (FX'i Tiingo ve İş Yatırım da veriyor, EUR/USD üçünde de 1,157), ama
her gece sahte alarm üretiyor.

### 3.4 Hâlâ yok

- **Web arama aracı.** Kriptoda hissedekinden can yakıcı: hissede bilanço ve
  SEC dosyalaması var, kriptoda projenin ne yaptığı ancak internetten gelir.
  Eklenirse kaynak kademe disiplini korunmalı — web yalnızca **keşif**,
  sayısal iddia bizim kaynaklarımızdan doğrulanır.
- **Sigorta/finans bilançosu** (ANSGR, TURSG, DSTKF, KTLEV) — farklı tablo
  yapısı, dördü de portföyde yok, düşük öncelik.

---

## 4. Dış incelemeciye sorular

1. **§4'ün sayaç dağıtımı.** `kaydet()` tüm ajanların görüşlerini tek liste
   alıyor, dolayısıyla atılanın hangi ajandan geldiği raporda yok. Ajan başına
   sayı uydurmak yerine koşunun toplamını tek satıra yazdım. Doğru taviz mi,
   yoksa `kaydet()` ajan bazında mı raporlamalı?

2. **Hakem sapma metriği** berabere kalan oylamaları dışarıda bırakıyor
   (bölünmüş panelin karşılaştırılacak yönü yok). Bu, hakemin en çok değer
   kattığı durumu — çelişkiyi çözdüğü anı — ölçüm dışı bırakıyor olabilir.
   Daha iyi bir tanım var mı?

3. **Sessizlik ölçümü** şu an yalnızca sayıyor. Yanıt belgesindeki asıl öneri
   ikinci adımdı: sessiz günlerin sonraki anormal hareketlerini konuşulan
   günlerle karşılaştırmak. Bu backtest'ten önce mi sonra mı?

4. **Yarın 22:15** ilk gözetimsiz koşu. Bu turda değişen her şeyin (göç,
   hakem JSON'u, sayaçlar, karne notu) ilk gerçek sınavı. Prova ettim ama
   gözetimsiz koşu ayrı. Kontrol listesinde eklemem gereken bir şey var mı?

5. **Katılmadığın bir şey var mı** — özellikle karnenin yalnızca hakemi
   ölçmesi kararında. Ajanların 4/4 tuttuğu ama hakemin ıskaladığı bir günde
   karne %0 gösterecek; bunu istenen davranış saydık, ama karne tek başına
   okunduğunda panelin iyi çalıştığını gizliyor.
