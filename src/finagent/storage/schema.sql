PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS instruments (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    venue       TEXT NOT NULL,          -- BIST | BUX | MIDAS
    name        TEXT,
    asset_type  TEXT,                   -- equity | etf | fund | index | cash
    currency    TEXT,
    isin        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (symbol, venue)
);

-- Arastirma kimligi. Ekran goruntusunden gelen sembol TAHMINDIR ve yanlis
-- sirkete gidebilir: "Avantium" -> AVTX tahmin edildi, ama AVTX SEC'de
-- "Avalo Therapeutics" (bambaska bir biyotek). Bu yuzden cozumleme ayri
-- tutuluyor ve DOGRULANMADAN kaynak taramasinda kullanilmiyor.
CREATE TABLE IF NOT EXISTS identities (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    cik           TEXT,                 -- SEC kayit numarasi (EDGAR anahtari)
    sec_ticker    TEXT,
    sec_name      TEXT,
    exchange      TEXT,
    ir_url        TEXT,                 -- SEC'e tabi olmayanlar icin IR sayfasi
    status        TEXT NOT NULL,        -- dogrulandi | eslesmedi | sec_disi | elle
    method        TEXT,                 -- nasil cozuldu
    note          TEXT,
    resolved_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Endeks uyeligi: BUX'un "Discover" ekrani evreni endekslere gore
-- gruplandiriyor. Bu tablo KATALOG icindir; arastirma hedefi degildir.
CREATE TABLE IF NOT EXISTS index_members (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    index_name    TEXT NOT NULL,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (instrument_id, index_name)
);
CREATE INDEX IF NOT EXISTS idx_idxmem_name ON index_members(index_name);

-- Arastirma hedefleri: portfoyde olmayan ama izlenen adaylar.
CREATE TABLE IF NOT EXISTS watchlist (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL DEFAULT 'aday',   -- aday | portfoy
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    note          TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ts            TEXT    NOT NULL,     -- ISO 'YYYY-MM-DD'
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source        TEXT    NOT NULL,
    PRIMARY KEY (instrument_id, ts, source)
);
CREATE INDEX IF NOT EXISTS idx_prices_ts ON prices(ts);

-- VARSAYILAN YOK — BILEREK.
--
-- `DEFAULT 'ali'` bugun zararsiz gorunuyordu (tek sahip) ama Faz B'de
-- panel kisi basina kosarken bir INSERT yolunda sahip parametresi
-- unutulursa sorgu PATLAMAZ, sessizce ilk sahibe yazardi: ikinci
-- kisinin tahminleri birincinin defterine duser ve hicbir sey hata
-- vermez. Bu, cok kullanicili katmanin engellemek icin var oldugu
-- hatanin ta kendisi. Eksik INSERT GURULTULU patlamali.
--
-- SAHIP: cok kullanicili katmanin TEK ayrimi.
--
-- Piyasa verisi (fiyat, haber, temel) ORTAKTIR ve bir kez toplanir;
-- kisisel olan yalnizca portfoy ve ondan turenler. `sahip` bir
-- PARAMETREDIR, ortam durumu degil — Database nesnesinde "gecerli
-- kullanici" YOKTUR, ihtiyaci olan sorgu parametre olarak alir.
--
-- ANAHTARA GIRIYOR: iki kisi ayni gun ayni enstrumani tutabilir ve
-- ikisi de kaydedilmelidir. Sahip anahtarda olmasaydi ikincinin
-- yazmasi birincinin satirini EZERDI.
CREATE TABLE IF NOT EXISTS positions (
    sahip         TEXT    NOT NULL,     -- settings.yaml telegram.sahipler
    snapshot_ts   TEXT    NOT NULL,     -- ISO datetime
    account       TEXT    NOT NULL,     -- bux | midas
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    quantity REAL, avg_cost REAL, last_price REAL,
    market_value REAL, pnl_abs REAL, pnl_pct REAL,
    currency      TEXT,
    PRIMARY KEY (sahip, snapshot_ts, account, instrument_id)
);
-- Sahip ONDE: portfoy sorgularinin hepsi once sahibe suzuyor.
CREATE INDEX IF NOT EXISTS idx_positions_acct
    ON positions(sahip, account, snapshot_ts);

-- Sirket/duzenleyici aciklamalari. KADEME 1 = birincil kaynak.
--   source: kap (BIST) | sec (EDGAR) | ir (sirket basin bulteni)
-- Basin haberleri buraya DEGIL, news tablosuna gider — karistirilmamali.
CREATE TABLE IF NOT EXISTS disclosures (
    id           TEXT PRIMARY KEY,                -- kaynak id / url hash
    published_at TEXT,
    symbol       TEXT,
    company      TEXT,
    category     TEXT,
    title        TEXT,
    url          TEXT,
    body         TEXT,
    source       TEXT NOT NULL DEFAULT 'kap',
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_disc_pub ON disclosures(published_at);
CREATE INDEX IF NOT EXISTS idx_disc_sym ON disclosures(symbol, published_at);

-- Temel veri: SEC XBRL companyfacts (KADEME 1 — sirketin kendi dosyaladigi).
--
-- DIKKAT — DONEM SEMANTIGI: ayni kavram ayni dosyalamada BIRDEN FAZLA
-- donem icin gelir. NVDA'nin 2026 Q2 10-Q'sunda "Revenues" hem 181 gunluk
-- (6 aylik kumulatif) hem 90 gunluk (ceyregin kendisi) degeri tasiyor.
-- "Son degeri al" demek 6 aylik ile 3 ayligi karsilastirmak demektir ve
-- "gelir %49 dustu" gibi tamamen yanlis sonuc uretir. Bu yuzden start/end
-- ve gun sayisi SAKLANIR; sorgular donem bandina gore filtreler.
--
-- frame: SEC'in takvim donemi normalizasyonu (CY2025Q2, CY2025). Bos ise
-- kayit kumulatif/ara donemdir — karsilastirmaya SOKULMAZ.
CREATE TABLE IF NOT EXISTS fundamentals (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    concept       TEXT    NOT NULL,        -- us-gaap kavrami (Revenues, NetIncomeLoss...)
    unit          TEXT    NOT NULL,        -- USD, USD/shares, shares
    period_start  TEXT,                    -- bilanco kalemlerinde NULL (anlik)
    period_end    TEXT    NOT NULL,
    days          INTEGER,                 -- period_end - period_start; NULL = anlik
    val           REAL    NOT NULL,
    form          TEXT,                    -- 10-K | 10-Q | 20-F ...
    fy            INTEGER,                 -- MALI yil (takvim degil!)
    fp            TEXT,                    -- Q1..Q4 | FY
    frame         TEXT,                    -- CY2025Q2 gibi; NULL = kumulatif
    filed         TEXT,
    accn          TEXT,
    PRIMARY KEY (instrument_id, concept, period_end, days, form, unit)
);
CREATE INDEX IF NOT EXISTS idx_fund_lookup ON fundamentals(instrument_id, concept, period_end);

-- Yukaridaki PRIMARY KEY ANLIK kalemleri KORUYAMAZ. SQLite'ta UNIQUE
-- indekste NULL'lar birbirinden FARKLI sayilir; `days IS NULL` olan
-- bilanco kalemleri hicbir zaman cakismaz ve her toplama calismasinda
-- YENIDEN EKLENIR. Olculdu: tek bir StockholdersEquity donemi 10 satira
-- cikmisti (10 collector calismasi).
--
-- COALESCE ile NULL'i -1'e cevirerek gercek bir tekillik saglanir.
-- `days IS NULL = anlik` semantigi KORUNUYOR; degisen sadece indeks.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fund_key ON fundamentals(
    instrument_id, concept, period_end, COALESCE(days, -1), form, unit);

CREATE TABLE IF NOT EXISTS news (
    id           TEXT PRIMARY KEY,                -- sha1(url)
    published_at TEXT,
    source       TEXT,
    title        TEXT,
    url          TEXT,
    summary      TEXT,
    symbols      TEXT,                            -- 'THYAO,ASELS'
    publisher    TEXT,                            -- ASIL yayinci (teslim eden site degil)
    tier         INTEGER NOT NULL DEFAULT 0,      -- 2=ajans/finans basini, 3=toplayici,
                                                  -- 4=promosyon, 0=bilinmeyen
    -- KONU, KADEMEDEN AYRI BIR EKSEN. Kademe GUVENILIRLIGI olcer,
    -- konu ALAKAYI. AA ve Ekonomim kademe 2'dir ama akislarinda spor ve
    -- magazin de var: "TFF 18 yas alti duzenlemesi" mesru bir yayincidan
    -- gelir ve finansal degeri sifirdir. Ikisi ayrilmadan gundem bolumu
    -- kurulamiyordu — bkz. research/konular.py.
    konu         TEXT,                            -- makro_tr | makro_global |
                                                  -- emtia_enerji | jeopolitik |
                                                  -- sirket | alakasiz | belirsiz
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news(published_at);
CREATE INDEX IF NOT EXISTS idx_news_konu ON news(konu, published_at DESC);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id          INTEGER PRIMARY KEY,
    run_ts      TEXT NOT NULL,
    model       TEXT,
    scope       TEXT,          -- daily | adhoc:THYAO
    input_stats TEXT,          -- JSON
    output_md   TEXT,
    status      TEXT,
    error       TEXT,
    sahip       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_runs (
    id           INTEGER PRIMARY KEY,
    run_ts       TEXT NOT NULL,
    collector    TEXT NOT NULL,
    status       TEXT NOT NULL,   -- ok | partial | error
    rows_written INTEGER DEFAULT 0,
    duration_ms  INTEGER,
    error        TEXT
);

-- Harici API gunluk kota sayaci (UTC gun bazli).
-- Birden fazla kosunun kotayi ortaklasa tuketmemesi icin: her _cagir
-- sonrasi arttirilir, collect basinda kalan kotaya gore karar verilir.
CREATE TABLE IF NOT EXISTS api_kota (
    kaynak  TEXT NOT NULL,
    gun     TEXT NOT NULL,   -- UTC tarih: '2026-08-25'
    istek   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kaynak, gun)
);

-- ---------------------------------------------------------------------
-- SAATLIK BARLAR — bilerek AYRI TABLO.
--
-- `prices` tablosunu sorgulayan HICBIR yer `source`'a gore filtrelemiyor
-- ve hepsi GUNLUK bar varsayiyor (indicators, events, chat teknik ozeti).
-- Saatlik satirlar oraya karisirsa RSI/SMA/CAR sessizce yanlis hesaplanir
-- ve tutarli gorunur — eksik veriden tehlikelidir. Bu yuzden ayri.
--
-- Kripto 7/24 islem gorur: gunluk bar UTC 00:00'da kapanir, bosluk yoktur.
CREATE TABLE IF NOT EXISTS prices_hourly (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    -- 'YYYY-MM-DD HH:MM' (UTC). DAKIKA KORUNUYOR, kirpilmiyor.
    --
    -- Kripto barlari her zaman :00'da kapanir ama BORSA barlari
    -- kapanmaz: BIST'in 60 dakikalik barlari yerel 09:30/10:30/11:30,
    -- yani UTC 06:30/07:30/08:30. `%H:00` ile yazsaydik ucu de :00'a
    -- kirpilir ve bar 30 dakika YANLIS etiketlenirdi — ustelik ayni
    -- saate dusen gercek bir barla CAKISMA riski dogardi.
    ts            TEXT NOT NULL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        REAL,
    quote_volume  REAL,               -- USDT cinsinden hacim (yalnizca kripto)
    trades        INTEGER,
    source        TEXT NOT NULL,
    -- PARA BIRIMI (sema 14). Tablo kripto-yalnizken ortuk USDT idi.
    -- Gun ici katman BIST (TRY) ve ABD (USD) barlarini da buraya
    -- yaziyor; etiketsiz seri, gunluk `prices` tablosunda 17 pozisyonun
    -- 14'unu bozan kusur sinifinin aynisini burada acardi.
    -- NULL = etiketsiz eski kripto satiri; USDT VARSAYILMAZ.
    currency      TEXT,
    PRIMARY KEY (instrument_id, ts, source)
);

CREATE INDEX IF NOT EXISTS ix_prices_hourly_ts
    ON prices_hourly (instrument_id, ts DESC);

-- ---------------------------------------------------------------------
-- DOVIZ KURLARI. Portfoy EUR (BUX), fiyat serileri USD (Yahoo), kripto
-- USDT (Binance), Midas TRY olacak. Kur serisi olmadan bunlar TEK TABLODA
-- BIRLESTIRILEMEZ — birlestirilirse sayi sessizce yanlis cikar.
CREATE TABLE IF NOT EXISTS fx_rates (
    ts       TEXT NOT NULL,          -- 'YYYY-MM-DD'
    base     TEXT NOT NULL,          -- 'EUR'
    quote    TEXT NOT NULL,          -- 'USD'  ->  1 EUR = <rate> USD
    rate     REAL NOT NULL,
    source   TEXT NOT NULL,
    PRIMARY KEY (ts, base, quote, source)
);
CREATE INDEX IF NOT EXISTS ix_fx_lookup ON fx_rates (base, quote, ts DESC);

-- ---------------------------------------------------------------------
-- PROAKTIF KATMAN: sinyaller ve TAHMIN DEFTERI
--
-- Tahmin defteri bu sistemin en onemli parcasi. Kendi isabetini
-- olcmeyen bir tavsiye sistemi, kendini kandirma makinesidir.
-- Olculdu: gunluk al-satta %50 isabet ayda -%4.2 (komisyon), %55 isabet
-- +%5.6 getiriyor. Yani her sey isabet oraninin 50 mi 55 mi oldugunda
-- dugumleniyor ve bu VARSAYILAMAZ.
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY,
    olusma_ts     TEXT NOT NULL,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    tur           TEXT NOT NULL,      -- trend_kirilimi | hacim_anomalisi | ...
    yon           TEXT,               -- yukari | asagi | notr
    guc           REAL,               -- 0-1, deterministik skor
    kanit         TEXT,               -- JSON: hangi sayilar tetikledi
    fiyat         REAL,               -- sinyal anindaki kapanis
    para_birimi   TEXT,
    -- PIYASA sinyali 'ortak', PORTFOY sinyali kisiye ait.
    --
    -- rsi_ucu / sma50_kirilimi / hacim_anomalisi / olagandisi_hareket /
    -- olay_etkisi fiyattan turuyor, kisiden degil: BIR KEZ hesaplanir.
    -- yogunlasma / acik_zarar portfoyden turuyor, kisiye ozeldir.
    -- Okuma yuklemi her yerde ayni: WHERE sahip IN ('ortak', ?)
    --
    -- NULL DEGIL 'ortak' SENTINELI: SQLite UNIQUE kisitinda NULL'lar
    -- birbirinden FARKLI sayilir; sahip NULL olsaydi ayni piyasa
    -- sinyali her kosuda yeniden yazilirdi (fundamentals'ta tam bu
    -- oldu, 10 kopya).
    sahip         TEXT NOT NULL,
    UNIQUE (olusma_ts, instrument_id, tur, sahip)
);
CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals (olusma_ts DESC);
CREATE INDEX IF NOT EXISTS ix_signals_sahip ON signals (sahip, olusma_ts DESC);

-- Ajan panelinin URETTIGI tahmin. Sinyalden AYRI: sinyal deterministik
-- bir gozlem, tahmin ise bir IDDIA ve puanlanir.
-- AJAN ANAHTARIN PARCASI. Onceden (olusma_ts, instrument_id, ufuk_gun)
-- benzersizdi ve `kaydet()` ayni sembole bakan ajanlardan yalnizca EN
-- YUKSEK GUVENLI olani tutup digerlerini ATIYORDU. Bunun uc sonucu vardi:
--   1. Projenin kendi ilkesi olan "celiski en degerli ciktidir" deftere
--      hic gecmiyordu — teknik "asagi", risk "yukari" dediginde biri
--      kalici olarak siliniyordu.
--   2. `ajan_karnesi()` yalnizca HAYATTA KALAN tahminleri sayiyordu;
--      yani ajan karneleri yapisal olarak yanliydi.
--   3. Yuksek guven sistematik olarak seciliyordu, dolayisiyla karne
--      panelin degil EN IDDIALI AJANIN karnesiydi.
-- 'hakem' de bir ajan degeridir: kullanicinin OKUDUGU sey hakem ozetidir
-- ve olculmesi gereken sey odur.
CREATE TABLE IF NOT EXISTS predictions (
    id            INTEGER PRIMARY KEY,
    olusma_ts     TEXT NOT NULL,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ajan          TEXT NOT NULL DEFAULT 'bilinmiyor',  -- teknik|temel|olay|risk|hakem
    -- Tahmini DOGURAN sinyal. Bag olmadan backtest (signal_stats) ile
    -- defter iki ayri ada kalir: "bu sinyal tipi tarihsel olarak ne yapti"
    -- ile "bizim bu tipteki isabetimiz ne" birbirine baglanamaz. Ayrica
    -- panelin ELEME yapip yapmadigini olcmeyi saglar (kendisine verilen
    -- her sinyale gorus mu uretiyor, yoksa seciyor mu).
    signal_id     INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    yon           TEXT NOT NULL,      -- yukari | asagi | notr
    ufuk_gun      INTEGER NOT NULL,   -- kac gun sonra olculecek
    guven         REAL,               -- 0-1
    gerekce       TEXT,
    -- Tez ve gecersizlesme (P2-10). Kolonlar SIMDI aciliyor cunku kolon
    -- eklemek bedava, goc tekrari degil; yazma/kontrol mantigi sonra gelir.
    tez                  TEXT,        -- neden bu gorus
    gecersizlesme_kosulu TEXT,        -- makine-okunur, or. "close < 142.5"
    izlenecek_esik       TEXT,
    -- Tez bozulma bildirimi BIR KEZ gider. Doluysa tekrar gonderilmez;
    -- aksi halde esigin altinda kalan bir kagit HER GUN alarm uretir ve
    -- kullanici bildirimleri kapatir — alarmin degeri nadirliginde.
    tez_bozuldu_ts       TEXT,
    baslangic_fiyat REAL NOT NULL,
    para_birimi   TEXT,
    -- puanlama (ufuk dolunca doldurulur)
    olcum_ts      TEXT,
    bitis_fiyat   REAL,
    getiri_pct    REAL,
    piyasa_getiri_pct REAL,           -- ayni donemde vekil endeks
    anormal_pct   REAL,               -- getiri - beta*piyasa
    isabet        INTEGER,            -- 1 dogru, 0 yanlis, NULL olculmedi
    sahip         TEXT NOT NULL,
    -- TAKTIK SOZLESMESI (sema 15). KOLONLAR SONDA:
    -- `ALTER TABLE ADD COLUMN` kolonu tablonun SONUNA ekler ve
    -- goc yolu ile schema.sql'in urettigi sema OZDES olmali
    -- (`test_goc_semasi_ile_schema_sql_ozdes`). Ortaya koymak
    -- iki yolu ayristiriyordu. Bir yon iddiasi tek basina eyleme
    -- donusmez: nereden girilecegi ve nerede yanlis oldugunun
    -- anlasilacagi yazilmadan kullanilamaz.
    --
    -- SEVIYELER MODEL TARAFINDAN HESAPLANMAZ, SECILIR: `pulse.seviye`
    -- olculen adaylari (son kapanis, 2N stop, Donchian, SMA) uretiyor
    -- ve dogrulama, bunlardan birine uymayan seviyeyi REDDEDIYOR.
    -- `*_kaynak` hangi olcumden geldigini tasiyor — "bu sayi nereden
    -- geldi" sorusunun cevabi kayitta duruyor.
    -- GUN SONU OLCUMU (sema 28) — UFUK PUANLAMASINDAN AYRI.
    --
    -- Ufuk puanlamasi "tez dogru muydu" diye sorar ve cevabi 3-30 gun
    -- sonra gelir: 179 taktigin yalnizca 1'i olculmustu. Bu alanlar
    -- farkli bir soruyu cevaplar — taktik UYGULANABILIR miydi, SEANSI
    -- gecti mi — ve cevap ayni aksam gelir.
    --
    -- IKISI AYNI KOVAYA KONMAZ: gun sonu kolay, ufuk zor.
    --
    -- `gun_sonu_taban`: o gun ayni borsada AYNI TESTI gecen kagitlarin
    -- orani. Test ayni olmak zorunda; ilk yazimda degildi ve %76,3'e
    -- karsi %34,7 gibi 42 puanlik SAHTE bir kenar uretti (ayni teste
    -- cevrilince fark 2,6 puana dustu).
    --
    -- SIRA GOC ILE AYNI OLMAK ZORUNDA (`sahip`ten sonra, `taktik_tur`
    -- oncesi): duman testi iki semanin KOLON LISTESINI karsilastiriyor
    -- ve liste esitliginde SIRA da sayiliyor.
    gun_sonu_sonuc       TEXT,
    gun_sonu_ts          TEXT,
    gun_sonu_taban       REAL,
    gun_sonu_endeks      REAL,
    taktik_tur           TEXT,   -- alim | koruma | satis | bekle
    taktik_giris         REAL,
    taktik_stop          REAL,
    taktik_giris_kaynak  TEXT,
    taktik_stop_kaynak   TEXT,
    -- Taktigin GIRIS SEVIYESI ufuk icinde GERCEKTEN gorulduyse 1, hic
    -- gorulmediyse 0, taktik olmayan satirlarda NULL.
    --
    -- Taktik KOSULLU bir talimattir ("85,20'ye toparlarsa al"). Kosul
    -- gerceklesmediyse kullanici HICBIR SEY YAPMAMISTIR ve o satiri
    -- isabet/kacirma diye puanlamak, verilmemis bir tavsiyeyi olcmek
    -- olur. Fren (`taktikci.FREN_*`) bu sayiya baktigi icin ayrim
    -- SUTUN duzeyinde tutuluyor.
    taktik_tetiklendi    INTEGER,
    -- YAYIM DAMGASI (sema 29) — `olusma_ts`in YERINE GECMEZ, YANINDA DURUR.
    --
    -- OLCULEN KUSUR (2026-09-01): `olusma_ts` bir TARIHTIR (1438 satirin
    -- 1438'i 10 karakter) ve gun sonu olcumu gecikme kuralini onun
    -- uzerinden uyguluyordu:
    --
    --     [b for b in barlar if str(b["ts"])[:16] > "2026-09-01"]
    --      -> "2026-09-01 09:00" > "2026-09-01"  ->  True
    --
    -- Yani AYNI GUNUN TUM BARLARI geciyordu, taktigin yayimindan
    -- SAATLER ONCEKILER dahil. Modul bunu acikca yasakliyor (Lag 0).
    -- Olculdu: 72 taktik yeniden hesaplandiginda ayakta orani %83,3'ten
    -- %82,0'ye dustu ve 4 taktik `ayakta`dan `giris_tetiklenmedi`ye
    -- gecti — yayimdan ONCEKI barlarla "girilmis" sayilanlar.
    --
    -- `olusma_ts` NEDEN TARIH KALIYOR: `DO NOTHING` catismasi ona
    -- dayaniyor ("gunun ilk paneli kazanir", `journal.py`). Damgaya
    -- cevirmek gunde dort panelin dordunu de ayri satir yapardi ve
    -- "sabah ne demistin" sorusunun cevabini bozardi. Yeni kolon o
    -- karari hic ellemiyor.
    --
    -- NULL = eski satir; olcum `olusma_ts`e duser, yani DAVRANIS
    -- DEGISMEZ. Gecmise damga UYDURULMUYOR.
    yayim_ts             TEXT,
    -- TARANAN BARIN TARIHI (sema 30) — `olusma_ts` (tarama gunu) DEGIL.
    --
    -- OLCULEN KUSUR (2026-09-03 ve 2026-09-07): strateji motoru 22:15'te
    -- tarar; 3 Eylul'de toplama sureci cokup `strateji_fiyat` calismadi,
    -- 7 Eylul ABD tatiliydi. Iki gece de EN SON BAR bir onceki gunun
    -- bariydi ve motor ayni kirilimi (REGN, WFC, F, VST) ikinci kez
    -- yazdi — ayni giris fiyati, ayni stop, yeni `olusma_ts`. Defter
    -- kuralin kirilimini degil, TEKRARINI olcuyordu (14 satir = 10 tekil).
    -- Cakisma anahtari `olusma_ts` uzerinden oldugu icin bunu goremezdi.
    -- Bar tarihi burada tutulur; tarama ayni bari ikinci kez yazmaz.
    -- NULL = bar tarihi tasimayan ajanlar (panel, taktik).
    bar_ts               TEXT,
    -- OLCUM NOTU (sema 30): puanlayicinin bu satir icin NEDEN olcum
    -- yapmadigini ya da olcumu NASIL duzelttigini soyledigi yer.
    -- Bos beyan yerine sebep: "seri sicramasi 2026-08-26 x0,101 —
    -- kaynak yeniden cekilene kadar puanlanmadi" ya da "taban x0,1
    -- yeniden olceklendi (kaynak seriyi yeniden tabanladi)".
    -- Gerekce `analysis/tutarlilik.py` basinda, olculmus vakalarla.
    olcum_notu           TEXT,

    UNIQUE (olusma_ts, instrument_id, ufuk_gun, ajan, sahip)
);
-- SAHIP BILEREK YOK. `puanla()` Faz B'de TUM sahiplerin vadesi dolmus
-- tahminlerini TEK KOSUDA olcecek: deterministik, LLM'siz, kisi basina
-- kosturmanin faydasi yok. Yani bu sorgu kasitli olarak sahipten
-- bagimsiz ve indekse sahip eklemek onu YAVASLATIR. "Tutarlilik" adina
-- eklemeyin.
CREATE INDEX IF NOT EXISTS ix_pred_olcum ON predictions (olcum_ts, olusma_ts);
CREATE INDEX IF NOT EXISTS ix_pred_ajan ON predictions (sahip, ajan, olusma_ts);

-- ---------------------------------------------------------------------
-- PANEL KOSUSUNUN HAM CIKTISI.
--
-- `_json_cek()` ayristiramadiginda BOS donuyor ve o turdaki tahminler
-- deftere hic girmiyordu — sayacsiz, logsuz, retry'siz. Bu bir SECILIM
-- YANLILIGI uretiyor: bicimi bozan kosular olcum disi kaliyor ve
-- yorumun uzun/karmasik (yani belirsiz) oldugu durumlarda bicimin
-- bozulma olasiligi daha yuksekse, karne sistematik olarak IYIMSER cikar.
--
-- Olculdu 2026-08-16: "simdiye kadar kac turda bos dondu" sorusu
-- GERIYE DONUK CEVAPLANAMADI, cunku iz yok. Sayac ileriye donuk cozer;
-- HAM METIN saklamak, bugun sormadigimiz sorulari da cozer.
CREATE TABLE IF NOT EXISTS panel_runs (
    id            INTEGER PRIMARY KEY,
    run_ts        TEXT NOT NULL,
    ajan          TEXT NOT NULL,      -- teknik|temel|olay|risk|hakem
    ham_metin     TEXT,               -- modelin TAM cevabi, kirpilmadan
    json_durum    TEXT NOT NULL,      -- ok | bos | ajan_hatasi
    gorus_sayisi  INTEGER NOT NULL DEFAULT 0,
    -- Deftere YAZILAMAYAN gorusler, sebebiyle. `atildi` bayragi yerine
    -- sayac tutuluyor: gorusun kendisi zaten `ham_metin` icinde duruyor,
    -- yani kayip yok. Kullanilmayan bir kolon acmak, az once belgeledigimiz
    -- "olu konfigurasyon" sinifinin ta kendisi olurdu.
    atilan_sembol_yok INTEGER NOT NULL DEFAULT 0,
    atilan_seri_yok   INTEGER NOT NULL DEFAULT 0,
    atilan_cakisma    INTEGER NOT NULL DEFAULT 0,
    hata          TEXT,
    sahip         TEXT NOT NULL
);
-- Sahip ONDE: Faz B'de panel_runs sorgulari once sahibe suzuyor.
-- DIKKAT: `CREATE INDEX IF NOT EXISTS` MEVCUT indeksi yeniden
-- TANIMLAMAZ. Tanimi degistirmek icin tablonun yeniden kurulmasi
-- ya da acik DROP INDEX gerekir.
CREATE INDEX IF NOT EXISTS ix_panel_runs_ts
    ON panel_runs (sahip, run_ts DESC);

-- ---------------------------------------------------------------------
-- TEMETTU odemeleri. BIST tarafinda hic temettu verisi yoktu; Midas'in
-- hisse detay sayfasi tarihce veriyor. Getiri hesabinda temettu ihmal
-- edilirse toplam getiri SISTEMATIK olarak dusuk cikar.
CREATE TABLE IF NOT EXISTS dividends (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    odeme_tarihi  TEXT NOT NULL,          -- 'YYYY-MM-DD'
    verim_pct     REAL,                   -- o tarihteki temettu verimi
    fiyat         REAL,                   -- temettu tarihindeki fiyat
    brut          REAL,
    net           REAL,
    para_birimi   TEXT,
    kaynak        TEXT NOT NULL,
    PRIMARY KEY (instrument_id, odeme_tarihi, kaynak)
);
CREATE INDEX IF NOT EXISTS ix_div_tarih ON dividends (instrument_id, odeme_tarihi DESC);

-- ---------------------------------------------------------------------
-- ORTAKLIK YAPISI. Kimin elinde ne kadar pay var — halka aciklik ve
-- kontrol yogunlasmasi. Midas hisse sayfasindaki pasta grafik Chart.js
-- ile ciziliyor ve veri JS BELLEGINDE duruyor (piksel okumaya gerek yok).
CREATE TABLE IF NOT EXISTS ownership (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ortak         TEXT NOT NULL,
    pay_pct       REAL NOT NULL,
    olcum_tarihi  TEXT NOT NULL,
    kaynak        TEXT NOT NULL,
    PRIMARY KEY (instrument_id, ortak, olcum_tarihi, kaynak)
);

-- ---------------------------------------------------------------------
-- BILDIRIM DURUMU — ayni uyariyi tekrar tekrar gondermemek icin.
--
-- Portfoy riskleri (yogunlasma, acik_zarar) DURUM'dur, olay degil:
-- ASML portfoyun %40'iysa bu bugun de yarin da dogrudur. Bastirma
-- olmadan gunde iki hafif kosu x her gun ayni cumleyi gonderir ve
-- kullanici bildirimleri kapatir — alarmin degeri NADIRLIGINDEN gelir.
--
-- Tez alarmindaki `tez_bozuldu_ts` ile ayni problem, baska kanaldan.
-- SAHIP ANAHTARIN PARCASI. Iki kisi de ASML tutuyorsa ikisinin
-- yogunlasma orani FARKLI ve ikisi de kendi alarmini almali. Sahipsiz
-- anahtarda A'nin bastirma satiri B'ninkini EZER: B ya kendi riskini
-- hic gormez ya da A ertesi gun gereksiz alarm alir. Tablo tam da
-- bildirim yorgunlugunu cozmek icin kuruldu; sahipsiz hali, cozmeye
-- calistigi seyi baska bicimde uretiyordu.
--
-- VARSAYILAN YOK: eksik INSERT gurultulu patlamali.
CREATE TABLE IF NOT EXISTS bildirim_durumu (
    sahip           TEXT NOT NULL,
    instrument_id   INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    tur             TEXT NOT NULL,      -- yogunlasma | acik_zarar
    son_deger       REAL,               -- en son BILDIRILEN deger
    son_bildirim_ts TEXT NOT NULL,
    PRIMARY KEY (sahip, instrument_id, tur)
);

-- ---------------------------------------------------------------------
-- KORUMA SEVIYESI (sema 13) — pozisyon basina 2N-ATR stop.
--
-- NEDEN `predictions`'A YAZILMIYOR: bir stop TAHMIN DEGILDIR. Oraya
-- yazsaydik `puanla()` onu yon tahmini sanip isabet olcerdi ve
-- `ajan_karnesi` sistemin isabet oranini bir risk kuralıyla kirletirdi.
-- Ayni ayrim `NetKarTTM`in seriden dislanmasi ve saatlik barlarin ayri
-- tabloda durmasiyla ayni sinifta: KAVRAM AYRIYSA TABLO DA AYRI.
--
-- NEDEN TAHMIN DEGIL: koruma seviyesi bir KOSUL BEYANIDIR — "fiyat
-- buranin altina inerse haber ver". Isabet orani olculmeden de
-- durustce sunulabilir, tipki tez bozulmasi gibi (bkz. pulse/tez.py).
-- Sistem hicbir zaman emir gondermez; seviye bir OLCUMDUR, tavsiye
-- degil.
--
-- `stop` YALNIZCA YUKARI HAREKET EDER (ratchet). Fiyat yukseldikce
-- 2N asagisi da yukselir ve kazanci kilitler; fiyat duserse seviye
-- YERINDE KALIR. Asagi da hareket etseydi stop hicbir zaman
-- kirilmazdi — kendi kendini gecersiz kilan bir koruma olurdu.
--
-- SAHIP ANAHTARIN PARCASI: iki kisi ayni kagidi farkli maliyetle
-- tutuyor olabilir ve her biri kendi alarmini almali.
CREATE TABLE IF NOT EXISTS koruma (
    sahip         TEXT NOT NULL,
    hesap         TEXT NOT NULL,      -- bux | midas | binance
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    kuruldu_ts    TEXT NOT NULL,
    guncellendi_ts TEXT,
    referans_fiyat REAL,              -- seviyenin kuruldugu andaki kapanis
    n             REAL NOT NULL,      -- 20 gunluk ATR
    stop          REAL NOT NULL,      -- referans - 2N (yalnizca yukari guncellenir)
    para_birimi   TEXT,               -- SERININ para birimi (pozisyonunki farkli olabilir)
    bozuldu_ts    TEXT,               -- kirildiginda damgalanir; teslimattan SONRA
    PRIMARY KEY (sahip, hesap, instrument_id)
);

-- ---------------------------------------------------------------------
-- SOHBET ARSIVI — append-only, hicbir zaman budanmaz.
--
-- Modelin GORDUGU gecmis ile SAKLANAN gecmis AYRI seylerdir ve bu tablo
-- ikincisidir. Model penceresi bilerek dar: son 8 tur, en fazla 6 saat
-- (chat.py MAX_GECMIS / GECMIS_TAZELIK_SAAT) — cunku Telegram'in "Clear
-- Messages"i tamamen istemci tarafidir, bota haber gitmez ve eski turlari
-- baglama koymak modelin, kullanicinin artik goremedigi bir konusmanin
-- devami olarak cevap vermesine yol acar.
--
-- Ama o dar pencere ARSIVLEME islevi de goruyordu ve tek kalici kayit
-- oydu: `data/bot/sohbet/<chat_id>.json` her yazmada son 8 turu tutup
-- gerisini ATIYOR. "Gecen hafta ne sormustum, ne cevap vermisti"
-- sorusunun GUVENILIR bir cevabi yoktu. Burasi o cevap.
--
-- CHAT_ID ZORUNLU, SAHIP DEGIL. Sohbetin degismez kimligi chat_id;
-- `sahip` ondan TURETILIR (settings.yaml -> telegram.sahipler). Turetme
-- basarisiz olursa kaydi DUSURMEK ya da bir varsayilana yazmak yerine
-- sahip NULL birakilir: arsivin tek isi kaybetmemek, ve NULL bir satir
-- yanlis kisiye yazilmis bir satirdan cok daha az zararlidir. Okuma
-- daima `WHERE sahip = ?` ile suzulur, yani NULL satir kimsenin
-- gecmisine SIZMAZ — anomali olarak gorunur.
--
-- METIN KIRPILMAZ. Yuvarlanan pencere asistan cevabini 1500 karakterde
-- kesiyor (baglami sismesin diye); arsiv TAM metni tutar.
CREATE TABLE IF NOT EXISTS sohbet_kaydi (
    id      INTEGER PRIMARY KEY,
    ts      TEXT    NOT NULL,      -- UTC ISO-8601
    chat_id TEXT    NOT NULL,
    sahip   TEXT,                  -- turetilmis; cozulemezse NULL (yukari bak)
    rol     TEXT    NOT NULL,      -- user | assistant
    metin   TEXT    NOT NULL,
    gorsel  INTEGER NOT NULL DEFAULT 0,  -- mesaja ekran goruntusu eslik etti mi
    araclar TEXT,                  -- asistan turunda cagrilan arac adlari (virgullu)
    -- ANLAM VEKTORU (M4/T5). 768 float32 = 3072 bayt; 156 satir icin
    -- ~480 KB. Bu buyukluk icin ayri bir vektor deposu (sqlite-vec,
    -- faiss) kurmak, veriden buyuk bir altyapi tasimak olurdu.
    gomme       BLOB,
    -- HANGI MODELLE URETILDI. Bos birakilamaz bir alan degil, AYIRT
    -- EDICI bir alan: model ya da onek semasi degisirse eski vektorler
    -- GECERSIZDIR. Karisik bir vektor uzayinda arama, bos sonuctan
    -- KOTUDUR — makul gorunen yanlis turlar doner ve hicbir sey
    -- yanlis oldugunu soylemez. Arama bu alani denetler ve farkli
    -- model gorurse SESSIZCE ATLAMAZ, hata verir.
    gomme_model TEXT,
    gomme_ts    TEXT,
    -- MESAJ NEREDEN GELDI: 'sohbet' | 'sabah' | 'ogle' | 'kapanis' |
    -- 'nabiz' | 'gunici'.
    --
    -- ARSIV BUGUNE KADAR TEK YONLUYDU. Yazan tek yer `_sohbet` idi;
    -- botun kendi PROAKTIF mesajlari (sabah taramasi, gun ici taktik,
    -- kapanis ozeti, nabiz) kullaniciya gidiyor ve hicbir kayit
    -- birakmiyordu. Yani konusmanin yarisi hafizada yoktu: model
    -- kendi kurdugu cumleyi hatirlamiyordu.
    --
    -- KOLON AYIRT EDICI, SUZGEC DEGIL. Okuma varsayilan olarak HEPSINI
    -- getirir — "dun ne demistin" sorusunun cevabi cogu zaman bir
    -- proaktif mesajdir. Kolon, gerektiginde ayirmak ve sayabilmek
    -- icin var (or. "bu hafta kac taktik karti gonderdim").
    --
    -- VARSAYILAN 'sohbet' GERIYE DONUK DOGRU: bu kolondan onceki her
    -- satir gercekten sohbetten geldi, doldurma gerekmiyor.
    kaynak      TEXT NOT NULL DEFAULT 'sohbet'
);
CREATE INDEX IF NOT EXISTS ix_sohbet_sahip ON sohbet_kaydi (sahip, ts DESC);

-- ---------------------------------------------------------------------
-- KONUSMA <-> ENSTRUMAN KOPRUSU (sema 19)
--
-- NEDEN: arsiv turlarinin %62'sinde bilinen bir sembol geciyor (olculdu
-- 2026-08-25, 290 satirda 248 farkli sembol) ama `sohbet_kaydi`'da
-- sembol kolonu YOKTU. Yani "ASELSAN hakkinda ne demistin" bir JOIN
-- degil bir METIN ARAMASIYDI, ve o arama ancak model `sohbet_arsivi`
-- aracini cagirmaya karar ederse calisiyordu. Sistemin EN IYI BILDIGI
-- sey (enstruman kimligi) konusma hafizasina hic baglanmamisti.
--
-- KOPRU TABLOSU, KOLON DEGIL: bir tur birden cok sembolden bahsedebilir
-- ("ROSE'u satip ADA'ya gecsem?"). Virgullu bir kolon aramada LIKE'a
-- doner ve `sohbet_kaydi.symbols` alaninin haberde yol actigi tuzagin
-- aynisini uretirdi.
--
-- CASCADE: arsiv satiri silinirse (or. /unut arsiv) koprusu de gider.
-- Yetim satir arama sonucuna girip JOIN'de duserse "sonuc var ama
-- gosterilemiyor" gibi sessiz bir eksilme olurdu.
CREATE TABLE IF NOT EXISTS sohbet_sembol (
    kayit_id      INTEGER NOT NULL
                  REFERENCES sohbet_kaydi(id) ON DELETE CASCADE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    PRIMARY KEY (kayit_id, instrument_id)
);
-- Okuma yonu: "bu sembol hangi turlarda gecti", en yeniden eskiye.
CREATE INDEX IF NOT EXISTS ix_sohbet_sembol_ins
    ON sohbet_sembol (instrument_id, kayit_id DESC);
CREATE INDEX IF NOT EXISTS ix_sohbet_chat  ON sohbet_kaydi (chat_id, ts DESC);

-- ---------------------------------------------------------------------
-- ARSIV METIN INDEKSI (FTS5, trigram)
--
-- NEDEN: `sohbet_ara` bugune kadar sorgunun TAMAMINI tek bir
-- `metin LIKE '%...%'` kalibi yapiyordu. Olculdu (2026-08-20, altin
-- kume): 15 dogal sorgunun 13'u SIFIR satir dondurdu — "altın hesabı
-- kaç TL" diye bir dize arsivde gecmiyor. Cok kelimeli sorgu YAPISAL
-- OLARAK calismiyordu. Ikinci ariza sapkada: 'altın' 20 satir,
-- 'altin' 1 satir.
--
-- NEDEN TRIGRAM: Turkce eklemeli. `unicode61` kelime sinirindan
-- boler ve "altın" ile "altını" ayri terim olur; trigram alt-dize
-- eslestirir, ek sorunu ortadan kalkar.
--
-- NEDEN BAGIMSIZ TABLO (content='' / external content DEGIL):
-- indekse NORMALIZE metin, tabloda HAM metin duruyor — ikisi ayri.
-- Dis icerik kipinde bu ayrilik sessiz kaliyor; olculdu: icerik
-- tablosunu degistirip indeksi guncellemedim, `integrity-check`
-- GECTI ve kolon secince indeksin dedigi ile metnin dedigi ayristi,
-- hicbir uyari cikmadi. 189 KB'lik bir arsiv icin bu risk gereksiz.
--
-- NEDEN `leksik()` BIR UDF: normalizasyonun TEK uygulamasi olsun diye.
-- Tetikleyici de arama sorgusu da `search/normalize.py`'daki ayni
-- fonksiyondan geciyor, yani indeks ile sorgu AYRISAMAZ — bu test
-- edilen degil, YAPISAL bir garanti. Fonksiyon `Database.__init__`'te
-- kaydediliyor ve kod tabaninda tek bir `sqlite3.connect` var.
-- UDF'siz bir baglanti yazmaya kalkarsa `no such function: leksik`
-- ile SESLI patlar; indeksin sessizce eskimesinden iyidir.
CREATE VIRTUAL TABLE IF NOT EXISTS sohbet_fts USING fts5(
    metin,
    tokenize='trigram'
);

-- Uc tetikleyici. Amac: "yeni tur eklendiginde indeks sessizce
-- eskimesin". Uygulama katmaninda esitlemek de mumkundu (tek bir
-- INSERT yolu var) ama IKINCI bir yazma yolu eklendigi gun sessizce
-- bozulurdu; tetikleyici o gunu de kapsiyor.
CREATE TRIGGER IF NOT EXISTS sohbet_fts_ekle
AFTER INSERT ON sohbet_kaydi BEGIN
    INSERT INTO sohbet_fts(rowid, metin) VALUES (new.id, leksik(new.metin));
END;

CREATE TRIGGER IF NOT EXISTS sohbet_fts_sil
AFTER DELETE ON sohbet_kaydi BEGIN
    DELETE FROM sohbet_fts WHERE rowid = old.id;
END;

-- Sil-sonra-ekle: bagimsiz FTS5 tablosu duz DELETE destekliyor, yani
-- dis icerik kipindeki "'delete' komutuna ESKI metni ver" tuzagi
-- burada yok. O tuzak gercek: eski metin yanlis verilirse indekste
-- yetim terimler kalir ve hicbir denetim bunu bildirmez.
CREATE TRIGGER IF NOT EXISTS sohbet_fts_guncelle
AFTER UPDATE OF metin ON sohbet_kaydi BEGIN
    DELETE FROM sohbet_fts WHERE rowid = old.id;
    INSERT INTO sohbet_fts(rowid, metin) VALUES (new.id, leksik(new.metin));
END;

-- ---------------------------------------------------------------------
-- OGRETILEN IPUCLARI — ayni ozelligi iki kez anlatmamak icin.
--
-- Bot kendi yeteneklerini ogretiyor (bkz. bot/yetenekler.py). Ogretme
-- ANI degerlidir ama TEKRARI zararlidir: her cevabin altina ayni ipucu
-- eklenirse kullanici ipuclarini okumayi tamamen birakir. Bu, risk
-- alarmlarinda bir kez yasandi ve `bildirim_durumu` ile cozuldu; ayni
-- kalip, ayni gerekce.
--
-- SAHIP ANAHTARIN PARCASI: ikinci kisi de kendi ipucunu ALMALI. Sahipsiz
-- anahtarda A'ya anlatilan sey B'ye hic anlatilmazdi.
CREATE TABLE IF NOT EXISTS ogretilen (
    sahip     TEXT NOT NULL,
    kod       TEXT NOT NULL,      -- yetenekler.IPUCLARI anahtari
    ilk_ts    TEXT NOT NULL,
    PRIMARY KEY (sahip, kod)
);

-- EKONOMIK TAKVIM — "yarin ne var".
--
-- KAYNAKLARIN COGU ERISILEMIYOR ve bu tablo bunu GIZLEMEZ: `takvim_kaynak`
-- her kosuda her kaynagi yeniden dener ve sonucu yazar. Olculdu
-- (2026-08-17): TUIK'in yeni portali kendi API'sine 403 donuyor
-- (`/api/tr/press/latest`), TCMB "Takvim" ve BLS sayfalari tarayicida bile
-- bos geliyor, ECB'nin index'i takvimi HTML'de tasimiyor. Yalnizca Fed
-- FOMC duz HTTP ile ve temiz ayrisiyor.
--
-- Neden tabloya yaziliyor: "kaynak calismiyor" bilgisi bir yorumda degil
-- VERIDE dursun ki gun geldiginde acildigi FARK EDILSIN. Sessiz bir
-- bosluk, olmayan bir bolumden daha kotudur.
CREATE TABLE IF NOT EXISTS takvim (
    tarih      TEXT NOT NULL,               -- 'YYYY-MM-DD'
    kaynak     TEXT NOT NULL,               -- 'fed' | 'tcmb' | 'tuik' | ...
    bolge      TEXT,                        -- 'ABD' | 'Turkiye' | 'Euro Bolgesi'
    olay       TEXT NOT NULL,
    onem       TEXT,                        -- 'yuksek' | 'orta'
    url        TEXT,
    guncelleme TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tarih, kaynak, olay)
);
CREATE INDEX IF NOT EXISTS idx_takvim_tarih ON takvim(tarih);

CREATE TABLE IF NOT EXISTS takvim_kaynak (
    kaynak     TEXT PRIMARY KEY,
    durum      TEXT NOT NULL,               -- 'ok' | 'engelli' | 'bos'
    ayrinti    TEXT,                        -- HTTP kodu / neden
    url        TEXT,
    son_deneme TEXT NOT NULL DEFAULT (datetime('now'))
);

-- TUIK SDMX KATALOGU. 408 veri akisi var ve hangisinin ne oldugunu
-- ELDE TUTMAK sart: aksi halde yeni bir seri eklemek her seferinde
-- 400 KB'lik dataflow listesini yeniden indirip elle aramak demek.
-- Katalog ayrica sohbetten aranabilir kilar ("TUIK'te konut verisi
-- var mi").
CREATE TABLE IF NOT EXISTS tuik_dataflow (
    id         TEXT PRIMARY KEY,            -- DF_YIUFE_EDO_V1
    surum      TEXT,
    ad_tr      TEXT,
    ad_en      TEXT,
    aciklama   TEXT,
    dsd        TEXT,                        -- bagli veri yapisi tanimi
    guncelleme TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tuik_df_ad ON tuik_dataflow(ad_tr);

-- MAKRO GOSTERGE SERISI (TUIK ve ileride baska kurumlar).
--
-- `prices` KULLANILMIYOR: bunlar bir enstrumanin fiyati degil, bir
-- OLCUM. `fundamentals` da uymuyor; orada donem semantigi sirkete ait.
-- Ayri tablo, cunku ayri anlam.
--
-- `donem` metin: TUIK aylik 'YYYY-MM', ceyreklik 'YYYY-QN', yillik
-- 'YYYY' donduruyor. Tarihe cevirmek ceyreklik seride hangi gune
-- denk geldigi kararini bize yukler; kaynagin bicimi KORUNUYOR.
CREATE TABLE IF NOT EXISTS makro_seri (
    kod        TEXT NOT NULL,               -- TR_YIUFE_YILLIK
    donem      TEXT NOT NULL,               -- '2026-07'
    deger      REAL NOT NULL,
    kaynak     TEXT NOT NULL,               -- 'tuik'
    guncelleme TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (kod, donem, kaynak)
);
CREATE INDEX IF NOT EXISTS idx_makro_seri_kod ON makro_seri(kod, donem DESC);

-- ---------------------------------------------------------------------
-- KALICI GERCEKLER — sohbetten damitilan, dokum DEGIL.
--
-- NEDEN AYRI KATMAN
--   `sohbet_kaydi` bir DOKUMDUR: ne konusuldugunu tutar, neyin GECERLI
--   oldugunu degil. Calisma penceresi ise dar (son 8 tur / 6 saat) ve
--   olmak zorunda: Telegram'in "Clear Messages"i istemci tarafi oldugu
--   icin eski turlari baglama koymak, kullanicinin artik goremedigi bir
--   konusmanin devami olarak cevap vermek demek.
--
--   Arada bir bosluk kaliyordu ve OLCULDU: 2026-08-19 11:29'da kullanici
--   "genel olarak ta musteri olarak satis fiyatimi cekmen gerekir
--   hesaplarken" dedi. Bu KALICI bir kural. Hicbir yere yazilmadi, alti
--   saat sonra pencereden dustu ve ertesi gun ayni hesap yine paritenin
--   ortasiyla yapilirdi. 25 tablonun hicbiri bunu tutmuyordu.
--
-- HALUSINASYONA KARSI: her kayit KAYNAK TURUNU tasiyor (`kaynak_ts`).
-- Model bir seyi hatirlarken "19 Agustos'ta soyle demistin" diye
-- ALINTILAR; kaydi olmayan bir sey icin "sanirim soyle demistin"
-- diyemez, cunku kayit ya vardir ya yoktur.
--
-- SILINMEZ, GECERSIZLESIR: ayni (sahip, tur, konu) icin yeni bir kayit
-- eskisini `gecerli = 0` yapar. "Ne zaman fikir degistirdi" sorusu
-- cevaplanabilir kalir; DELETE onu imkansiz kilardi.
CREATE TABLE IF NOT EXISTS hatirlanan (
    id             INTEGER PRIMARY KEY,
    sahip          TEXT NOT NULL,          -- VARSAYILAN YOK (bkz. positions)
    tur            TEXT NOT NULL,          -- tercih | olgu | karar
    konu           TEXT NOT NULL,          -- CAKISMA ANAHTARI, kisa: "altin fiyati"
    icerik         TEXT NOT NULL,          -- tam cumle, kullanicinin dilinde
    kaynak_ts      TEXT,                   -- hangi sohbet turundan geldi
    olusma_ts      TEXT NOT NULL,
    gecerli        INTEGER NOT NULL DEFAULT 1,
    gecersiz_ts    TEXT,
    gecersiz_sebep TEXT,                   -- "yeni kayit #12" | "kullanici unuttu"
    -- ISARETCI: DEGER BURADA DEGIL, KAYNAGINDA YASAR.
    --
    -- OLCULEN ARIZA (2026-08-25). ASML birim maliyeti IKI YERDE
    -- duruyordu: `positions.avg_cost` (canli, ekran goruntusunden
    -- guncelleniyor) ve `hatirlanan #3` (24 Agustos'ta donmus). Zaten
    -- kaymislardi — 20 Agustos anlik goruntusu 713,06, 24 Agustos
    -- 713,05. Ustelik kayit bir EMIR tasiyordu: "bir daha 'kayitli
    -- degil' deme, yuzdeden geriye turetme".
    --
    -- Ali bir alim daha yapsa `positions` guncellenir, `hatirlanan`
    -- AYNI KALIR ve model her turda eski sayiyi KESIN DOGRU diye
    -- emir almis olarak okurdu. Bu, deponun kendi en kotu hata sinifi
    -- (beyan edilen durumun gercek durumdan sessizce ayrismasi),
    -- hafiza katmaninin ICINE yerlestirilmis hali.
    --
    -- KURAL: kaynagi OLMAYAN sey (tercih, karar) deger olarak yasar;
    -- kaynagi OLAN olgu icin yalnizca isaretci tutulur ve deger okuma
    -- aninda `hatirlanan_coz` ile canlidan gelir. Cozulemezse bayat
    -- deger BASILMAZ, "kaynaga ulasamadim" denir.
    --
    -- `kaynak_tablo` KAPALI LISTEDEN (`HATIRLANAN_KAYNAKLARI`);
    -- `kaynak_anahtar` bicimi kaynaga ozel ("sahip|SEMBOL|alan").
    kaynak_tablo   TEXT,
    kaynak_anahtar TEXT,
    -- Kaynagi OLMAYAN olgular icin son teyit ani. Silinmez, ZAYIFLAR:
    -- bir ay onceki beyani bugunku olcum gibi sunmak, beyan ile olcumu
    -- karistirmaktir. Tercih ve kararlar yaslanmaz — onlar
    -- kullanicinin sozudur, bir olcum degil.
    dogrulama_ts   TEXT
);
-- Okuma her turda oluyor (otomatik geri cagirma): sahip + gecerli ONDE.
CREATE INDEX IF NOT EXISTS ix_hatirlanan_sahip
    ON hatirlanan (sahip, gecerli, tur, konu);

-- ---------------------------------------------------------------------
-- IBKR EMIRLERI (sema 22). GERCEK PARA — bu tablo bir DEFTERDIR.
--
-- NEDEN AYRI TABLO: `positions` bir ANLIK GORUNTU tutuyor ("su an elimde
-- ne var"). Emir ise bir OLAY ("su anda sunu yapmak istedim, su cevabi
-- aldim"). Ikisini karistirmak, portfoy tablosuna niyet yazmak olurdu.
--
-- HER SATIR BIR EMRIN TAM HIKAYESI: ne istendi, kim onayladi, ne zaman
-- gonderildi, IBKR ne dedi. Sonradan "bu emri neden verdik" sorusu
-- sorulacak ve cevabi BURADA olmali — bugunku fiyata bakarak degil.
--
-- `parmak_izi` onay fisiyle AYNI ozet. Onaylanan emir ile gonderilen
-- emrin ayni sey oldugu boylece sonradan da DOGRULANABILIR; kanit
-- kodda degil, kayitta.
--
-- `durum` yasam dongusu:
--   hazirlandi -> onaylandi -> gonderildi -> {kabul, teyit_bekliyor,
--                                             reddedildi, bilinmiyor}
-- `bilinmiyor` OZEL: POST zaman asimina ugradi, emir ULASMIS OLABILIR.
-- O satir mutabakat yapilana kadar KAPANMAZ.
CREATE TABLE IF NOT EXISTS emirler (
    id            INTEGER PRIMARY KEY,
    sahip         TEXT    NOT NULL,
    hesap         TEXT    NOT NULL,      -- IBKR hesap kimligi (U…/DU…)
    instrument_id INTEGER REFERENCES instruments(id) ON DELETE SET NULL,
    conid         TEXT    NOT NULL,
    yon           TEXT    NOT NULL,      -- BUY | SELL
    tur           TEXT    NOT NULL,      -- LMT | MKT
    adet          REAL    NOT NULL,
    fiyat         REAL,                  -- LMT'de dolu, MKT'de NULL
    sure          TEXT    NOT NULL,      -- DAY | GTC | IOC | OPG
    para_birimi   TEXT,
    -- KARAR ANININ KANITI. Emir sonradan incelenirken "o an fiyat neydi"
    -- sorusu bugunku fiyata bakilarak cevaplanamaz.
    referans_fiyat REAL,
    referans_kip   TEXT,                 -- gercek_zamanli | gecikmeli | donmus
    parmak_izi    TEXT    NOT NULL,
    olusma_ts     TEXT    NOT NULL,
    onay_ts       TEXT,
    onay_kim      TEXT,
    gonderim_ts   TEXT,
    emir_id       TEXT,                  -- IBKR order_id
    durum         TEXT    NOT NULL,
    ibkr_durum    TEXT,                  -- IBKR'nin kendi durum metni
    -- IBKR'nin teyit istedigi mesaj(lar). BASTIRILMIYOR, SAKLANIYOR:
    -- "hangi uyariyi gorup yine de onayladim" sorusunun cevabi.
    onay_mesaji   TEXT,
    not_          TEXT
);
CREATE INDEX IF NOT EXISTS ix_emirler_sahip ON emirler (sahip, olusma_ts DESC);
-- Acik uclu emirleri bulmak icin: `bilinmiyor` ve `teyit_bekliyor`
-- satirlari kapanana kadar her acilista goze carpmali.
CREATE INDEX IF NOT EXISTS ix_emirler_durum ON emirler (durum);

-- =====================================================================
-- TUR OLCUMU (sema 27) — bir sohbet turunun GERCEK maliyeti.
--
-- NEDEN VAR: 2026-08-31'de Ali sordu — "hangi modeli kullaniyoruz, baglam
-- penceresini nasil yonetiyoruz?". Cevaplanamadi: hicbir yerde token
-- sayaci YOKTU. `data/bot.log` icinde 272 'token' eslesmesi vardi ve
-- hepsi Python traceback'lerindeki degisken adlariydi.
--
-- Oysa SDK bunu ZATEN donduruyor: `ResultMessage.usage`,
-- `total_cost_usd`, `model_usage`, `num_turns`, `duration_ms`. Akis
-- dongusu `content` alani olmadigi icin o mesaji `continue` ile
-- atliyordu. Bu deponun en sik kalibi: kaynak var, YAZIM YOLU yok.
--
-- NEDEN LOG YETMEZ: asil sorular ZAMANSAL ve KARSILASTIRMALI —
-- "hangi arac baglami sisiriyor", "cache ne kadar tutuyor", "pencere
-- kac turda soguk basliyor". Bunlar grep'le degil SQL'le cevaplanir.
--
-- BAGLAM ATFI ayri sutunlarda: turun girdisi tek bir sayi degil, dort
-- katmanin toplami (sistem promptu + hafiza + pencere + arac ciktilari).
-- Hangi katmanin buyudugu bilinmeden "baglam sisti" bir teshis degil.
CREATE TABLE IF NOT EXISTS tur_olcumu (
    id            INTEGER PRIMARY KEY,
    ts            TEXT    NOT NULL,
    sahip         TEXT,
    chat_id       TEXT,
    model         TEXT,
    -- SDK'nin BEYANI. Tahmin degil; bosluk NULL kalir, sifir YAZILMAZ:
    -- "olculmedi" ile "sifirdi" ayri seylerdir.
    giris_token   INTEGER,
    cikis_token   INTEGER,
    cache_yazma   INTEGER,
    cache_okuma   INTEGER,
    maliyet_usd   REAL,
    sure_ms       INTEGER,
    api_sure_ms   INTEGER,
    tur_sayisi    INTEGER,               -- SDK num_turns (arac turu)
    durdurma      TEXT,                  -- stop_reason
    hatali        INTEGER,               -- 1 = is_error
    -- BIZIM tarafimizdan olculen baglam katmanlari (karakter).
    sistem_krk    INTEGER,
    istem_krk    INTEGER,
    pencere_krk   INTEGER,
    pencere_tur   INTEGER,
    soguk_baslama INTEGER,               -- 1 = pencere BOSTU
    arac_sayisi   INTEGER,
    araclar       TEXT
);
CREATE INDEX IF NOT EXISTS ix_tur_olcumu_ts ON tur_olcumu (ts DESC);
CREATE INDEX IF NOT EXISTS ix_tur_olcumu_sahip ON tur_olcumu (sahip, ts DESC);
