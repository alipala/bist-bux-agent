#!/usr/bin/env python
"""
Sema 30 oncesi yazilmis STRATEJI satirlarina `bar_ts` geri doldurur.

NEDEN: `strateji.ayni_bar_suzgeci` ayni (sahip, sembol, bar) uclusunu
defterden okuyarak eliyor. Eski satirlarda `bar_ts` NULL; suzgec onlari
GOREMEZ ve ilk gece (yeni bar gelmezse) F/VST ucuncu kez yazilirdi.

NASIL: kural gorusu `giris = seviyeler.son_kapanis`i AYNEN yaziyor
(`baslangic_fiyat` = o barin `close`u, ayni kaynaktan, ayni float).
Dolayisiyla bar = `olusma_ts`e kadar olan `yahoo/USD` barlari icinde
kapanisi baslangica TAM esit olan en son bar. Tolerans YOK: %0,2 pay
denendi, WFC'de 3 Eylul (89,19) ile 2 Eylul (89,27) ikisini de
yakaliyordu. Tam esitlikle 108/108 cozuldu, 34'u onceki gunun bari
(3 ve 7 Eylul tekrarlari) — olculen kusurun kendisi.

Cozulemeyen satir UYDURULMAZ, NULL kalir ve sayisi soylenir.

Kullanim:
    .venv/bin/python scripts/bar_ts_backfill.py            # kuru
    .venv/bin/python scripts/bar_ts_backfill.py --uygula
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finagent.config import load_settings  # noqa: E402
from finagent.storage.db import Database  # noqa: E402

SORGU = """
SELECT p.id, p.ajan, substr(p.olusma_ts, 1, 10) olusma, i.symbol,
       (SELECT MAX(x.ts) FROM prices x
         WHERE x.instrument_id = p.instrument_id AND x.source = 'yahoo'
           AND x.currency = 'USD' AND x.ts <= p.olusma_ts
           AND ABS(x.close - p.baslangic_fiyat) < 1e-6 * p.baslangic_fiyat) bar
FROM predictions p JOIN instruments i ON i.id = p.instrument_id
WHERE p.ajan LIKE 'strateji%' AND p.bar_ts IS NULL
ORDER BY p.olusma_ts, i.symbol, p.ajan"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true")
    args = ap.parse_args()
    s = load_settings()
    db = Database(s.db_path)
    db.init_schema()
    satirlar = db.query(SORGU)
    cozulen = [r for r in satirlar if r["bar"]]
    onceki = [r for r in cozulen if r["bar"] < r["olusma"]]
    print(f"bar_ts bos strateji satiri: {len(satirlar)} · cozulen {len(cozulen)} · "
          f"cozulemeyen {len(satirlar) - len(cozulen)} · onceki gunun bari {len(onceki)}")
    for r in onceki:
        if r["ajan"] == "strateji_secilen":
            print(f"  tekrar: {r['olusma']} {r['symbol']} -> bar {r['bar']}")
    if not args.uygula:
        print("KURU KOSU — yazmak icin --uygula")
        return 0
    with db.tx() as c:
        c.executemany("UPDATE predictions SET bar_ts=? WHERE id=? AND bar_ts IS NULL",
                      [(r["bar"], r["id"]) for r in cozulen])
    kalan = db.query("SELECT COUNT(*) n FROM predictions WHERE ajan LIKE 'strateji%' "
                     "AND bar_ts IS NULL")[0]["n"]
    print(f"yazildi: {len(cozulen)} · hala bos: {kalan}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
