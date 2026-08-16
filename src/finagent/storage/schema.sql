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

CREATE TABLE IF NOT EXISTS positions (
    snapshot_ts   TEXT    NOT NULL,     -- ISO datetime
    account       TEXT    NOT NULL,     -- bux | midas
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    quantity REAL, avg_cost REAL, last_price REAL,
    market_value REAL, pnl_abs REAL, pnl_pct REAL,
    currency      TEXT,
    PRIMARY KEY (snapshot_ts, account, instrument_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_acct ON positions(account, snapshot_ts);

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
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news(published_at);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id          INTEGER PRIMARY KEY,
    run_ts      TEXT NOT NULL,
    model       TEXT,
    scope       TEXT,          -- daily | adhoc:THYAO
    input_stats TEXT,          -- JSON
    output_md   TEXT,
    status      TEXT,
    error       TEXT
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
    ts            TEXT NOT NULL,      -- 'YYYY-MM-DD HH:00' (UTC)
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        REAL,
    quote_volume  REAL,               -- USDT cinsinden hacim
    trades        INTEGER,
    source        TEXT NOT NULL,
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
    UNIQUE (olusma_ts, instrument_id, tur)
);
CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals (olusma_ts DESC);

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
    UNIQUE (olusma_ts, instrument_id, ufuk_gun, ajan)
);
CREATE INDEX IF NOT EXISTS ix_pred_olcum ON predictions (olcum_ts, olusma_ts);
CREATE INDEX IF NOT EXISTS ix_pred_ajan ON predictions (ajan, olusma_ts);

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
    hata          TEXT
);
CREATE INDEX IF NOT EXISTS ix_panel_runs_ts ON panel_runs (run_ts DESC);

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
CREATE TABLE IF NOT EXISTS bildirim_durumu (
    instrument_id   INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    tur             TEXT NOT NULL,      -- yogunlasma | acik_zarar
    son_deger       REAL,               -- en son BILDIRILEN deger
    son_bildirim_ts TEXT NOT NULL,
    PRIMARY KEY (instrument_id, tur)
);
