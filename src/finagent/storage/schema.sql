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
