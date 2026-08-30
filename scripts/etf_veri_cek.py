"""
Sektor ETF'leri + uzun SPX tarihi. `venue='ETF'` — tarayici bu venue'yu
ALMIYOR (whitelist: positions/watchlist/index_members/likit BIST).

Kaynak adi 'yahoo_etf': `prices` birincil anahtari (instrument_id, ts,
source) ve para birimi anahtarda YOK. Ayri ad, ASML vakasindaki sessiz
ezmeyi onluyor.
"""
import sys

sys.path.insert(0, "/Users/alipala/github/bist-bux-agent/src")

from finagent.collectors.prices import yahoo_gunluk                # noqa: E402
from finagent.config import load_settings                          # noqa: E402
from finagent.storage.db import Database                           # noqa: E402

SEKTORLER = [
    ("XLK", "Technology Select Sector SPDR"),
    ("XLF", "Financial Select Sector SPDR"),
    ("XLE", "Energy Select Sector SPDR"),
    ("XLV", "Health Care Select Sector SPDR"),
    ("XLI", "Industrial Select Sector SPDR"),
    ("XLP", "Consumer Staples Select Sector SPDR"),
    ("XLU", "Utilities Select Sector SPDR"),
    ("XLB", "Materials Select Sector SPDR"),
    ("XLY", "Consumer Discretionary Select Sector SPDR"),
    ("XLRE", "Real Estate Select Sector SPDR"),
    ("XLC", "Communication Services Select Sector SPDR"),
]

s = load_settings()
db = Database(s.db_path)
db.init_schema()

print(f"{'sembol':<7}{'yazilan':>9}{'toplam bar':>12}{'ilk':>13}{'son':>13}")
print("-" * 54)
for sem, ad in SEKTORLER:
    iid = db.upsert_instrument(sem, "ETF", ad, "etf", "USD")
    try:
        n = yahoo_gunluk(db, sem, iid, "max", "USD", kaynak="yahoo_etf")
    except Exception as e:                                        # noqa: BLE001
        print(f"{sem:<7}  HATA: {e}")
        continue
    r = db.query("SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b FROM prices "
                 "WHERE instrument_id=? AND source='yahoo_etf'", (iid,))[0]
    print(f"{sem:<7}{n:>9}{r['n']:>12}{str(r['a']):>13}{str(r['b']):>13}")

# UZUN SPX — mevcut 'SPX' enstrumanina AYRI kaynak adiyla, 10 yillik
# seriyi EZMEDEN. ^GSPC 1927'ye kadar gidiyor (iddia degil, olculecek).
spx = db.upsert_instrument("SPX", "INDEX", "S&P 500", "index", "USD")
try:
    n = yahoo_gunluk(db, "^GSPC", spx, "max", "USD", kaynak="yahoo_spx_uzun")
    r = db.query("SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b FROM prices "
                 "WHERE instrument_id=? AND source='yahoo_spx_uzun'", (spx,))[0]
    print(f"\n{'SPX(uzun)':<7}{n:>9}{r['n']:>12}{str(r['a']):>13}{str(r['b']):>13}")
except Exception as e:                                            # noqa: BLE001
    print(f"\nSPX uzun HATA: {e}")
db.close()
