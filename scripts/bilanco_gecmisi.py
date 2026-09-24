#!/usr/bin/env python
"""
Strateji evreninin (S&P 500 + Nasdaq 100) ve portfoyun BILANCO GECMISI.

NEDEN AYRI BETIK: gunluk `bilancotakvim` collector'u AV'den ileri takvimi
TEK cagriyla aliyor ve Yahoo'yu yalnizca portfoy icin cagiriyor. Takvim
sinavi (`docs/takvim-filtresi.md`) ise 518 sembolun ~10 yillik gecmisini
istiyor: sembol basina bir Yahoo cagrisi, ~8-10 dakika. Bunu her gece
nabiz butcesinden odemek gereksiz — gecmis degismez.

KALDIGI YERDEN DEVAM EDER: son `--tazelik` gun icinde Yahoo satiri
yazilmis sembol atlanir (`--zorla` ile hepsi). Dusen semboller UYDURULMAZ;
sayisi ve adi sonda yazilir. "Bu sembolde bilanco yok" ile "istek dustu"
ayri seyler.

KADEME: Yahoo kademe 2. SEC 8-K Madde 2.02 (kademe 1) ile karsilastirma
ACIK MADDE: 2026-09-24 oturumunda agdaki guvenlik duvari `.gov` DNS'ini
yonlendiriyordu (bkz. belge §4).

Kullanim:
    .venv/bin/python scripts/bilanco_gecmisi.py --db data/finagent.db
    .venv/bin/python scripts/bilanco_gecmisi.py --db KOPYA.db --limit 20
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finagent.collectors.bilancotakvim import (  # noqa: E402
    YAHOO_ARASI_SN, portfoy_enstrumanlari, yahoo_adayi, yahoo_gecmis, yaz)
from finagent.config import load_settings  # noqa: E402
from finagent.storage.db import Database  # noqa: E402


def hedefler(db, settings) -> list[dict]:
    """Evren + portfoy, tekil. Evren `strateji_ayari`ndan — tek kaynak."""
    ayar = settings.strateji_ayari(db)
    out, gorulen = [], set()
    sonekler = settings.get("sources.prices.borsa_sonekleri") or {}
    # Endeks uyeleri ABD listesi: yalin sembol (para birimi USD sayilir).
    for r in db.endeks_uyeleri(ayar["endeksler"]):
        if r["id"] not in gorulen:
            gorulen.add(r["id"])
            out.append({"id": r["id"], "symbol": r["symbol"],
                        "yahoo": yahoo_adayi(r["symbol"], "USD", sonekler)})
    for r in portfoy_enstrumanlari(db, list(settings.sahip_listesi or [])):
        if r["id"] not in gorulen:
            gorulen.add(r["id"])
            out.append({"id": r["id"], "symbol": r["symbol"],
                        "yahoo": yahoo_adayi(r["symbol"], r["currency"], sonekler)})
    return [h for h in out if h["yahoo"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True,
                    help="yazilacak veritabani (ACIKCA verilir; varsayilan yok)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tazelik", type=int, default=7,
                    help="bu kadar gun icinde doldurulmus sembolu atla")
    ap.add_argument("--zorla", action="store_true")
    a = ap.parse_args()

    db = Database(Path(a.db))
    db.init_schema()
    settings = load_settings()
    liste = hedefler(db, settings)
    if a.limit:
        liste = liste[:a.limit]

    atlanan, yazilan, bos, dusen = 0, 0, [], []
    for i, h in enumerate(liste, 1):
        if not a.zorla and db.query(
                """SELECT 1 FROM bilanco_takvimi
                   WHERE instrument_id = ? AND kaynak = 'yahoo'
                     AND son_gorulme >= datetime('now', ?) LIMIT 1""",
                (h["id"], f"-{a.tazelik} days")):
            atlanan += 1
            continue
        try:
            satirlar = yahoo_gecmis(h["yahoo"], limit=100)
        except Exception as e:                            # noqa: BLE001
            dusen.append(f"{h['symbol']}: {type(e).__name__}: {str(e)[:60]}")
            time.sleep(YAHOO_ARASI_SN)
            continue
        if not satirlar:
            bos.append(h["symbol"])
        yazilan += yaz(db, [{**s, "instrument_id": h["id"]} for s in satirlar],
                       "yahoo")
        if i % 50 == 0:
            print(f"  {i}/{len(liste)} · {yazilan} satir · "
                  f"{len(dusen)} dustu · {len(bos)} bos", flush=True)
        time.sleep(YAHOO_ARASI_SN)

    print(f"\nhedef {len(liste)} · atlanan (taze) {atlanan} · "
          f"yazilan satir {yazilan}")
    print(f"BOS donen {len(bos)}: {', '.join(bos[:30])}")
    print(f"DUSEN {len(dusen)}:")
    for d in dusen[:40]:
        print("   ", d)
    db.close()
    return 0 if not dusen else 1


if __name__ == "__main__":
    raise SystemExit(main())
