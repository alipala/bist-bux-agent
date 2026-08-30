"""
Saglamlik sinavi icin uzun tarihli DUNYA ENDEKSLERI.

`venue='INDEX'` — tarayici bu venue'yu ZATEN disliyor (screener.evren:
`i.venue <> 'INDEX'`), yani gunluk taramaya sizmiyor.

`sources.prices.indices` listesine EKLENMIYOR: bunlar arastirma serisi,
her gun tazelenmesi gerekmiyor ve nabiz koşumunu uzatirlardi.

Kaynak adi 'yahoo_endeks_arsiv' — ayni enstrumanin baska bir kotasyonu
varsa EZMESIN (`prices` anahtari (instrument_id, ts, source) ve para
birimi anahtarda YOK).
"""
import sys

sys.path.insert(0, "/Users/alipala/github/bist-bux-agent/src")

from finagent.collectors.prices import yahoo_gunluk                # noqa: E402
from finagent.config import load_settings                          # noqa: E402
from finagent.storage.db import Database                           # noqa: E402

KAYNAK = "yahoo_endeks_arsiv"

ENDEKSLER = [
    ("SPX",   "^GSPC",     "S&P 500",           "USD"),
    ("NDXC",  "^IXIC",     "Nasdaq Composite",  "USD"),
    ("N225",  "^N225",     "Nikkei 225",        "JPY"),
    ("FTSE",  "^FTSE",     "FTSE 100",          "GBP"),
    ("DAX",   "^GDAXI",    "DAX",               "EUR"),
    ("TSX",   "^GSPTSE",   "S&P/TSX Composite", "CAD"),
    ("HSI",   "^HSI",      "Hang Seng",         "HKD"),
    ("AXJO",  "^AXJO",     "S&P/ASX 200",       "AUD"),
]


def main() -> int:
    s = load_settings()
    db = Database(s.db_path)
    db.init_schema()
    print(f"{'kod':<7}{'yahoo':<11}{'yazilan':>9}{'bar':>8}{'ilk':>13}{'son':>13}")
    print("-" * 61)
    for kod, yahoo, ad, ccy in ENDEKSLER:
        iid = db.upsert_instrument(kod, "INDEX", ad, "index", ccy)
        try:
            n = yahoo_gunluk(db, yahoo, iid, "max", ccy, kaynak=KAYNAK)
        except Exception as e:                                     # noqa: BLE001
            print(f"{kod:<7}{yahoo:<11}  HATA: {str(e)[:38]}")
            continue
        r = db.query("SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b FROM prices "
                     "WHERE instrument_id=? AND source=?", (iid, KAYNAK))[0]
        print(f"{kod:<7}{yahoo:<11}{n:>9}{r['n']:>8}{str(r['a']):>13}"
              f"{str(r['b']):>13}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
