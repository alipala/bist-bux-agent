#!/usr/bin/env python
"""
IBKR anlik kotasyon teshisi — VERI KIPINI de soyler.

NEDEN VAR
---------
"Fiyat geldi mi?" yeterli bir soru degil. IBKR ayni ucu uc farkli kipte
besliyor ve aradaki fark emir anlaminda buyuk:

    R  gercek zamanli
    D  15-20 dakika GECIKMELI
    Z  donmus (kapanis degeri)
    N  abone degil
    O  yillik "Market Data API Agreement" imzalanmamis

Gecikmeli fiyati gercek zamanliymis gibi kullanmak, yanlis fiyattan daha
kotudur: yanlis oldugu BILINMEZ. Bu betik kipi her satirda gosteriyor.

KULLANIM
    python scripts/ibkr_fiyat.py                # portfoy + izleme listesi
    python scripts/ibkr_fiyat.py AMZN KO ASML   # secili semboller
    python scripts/ibkr_fiyat.py --json

Fiyat yazmiyor, yalnizca okuyor. Acilan her piyasa verisi hatti cikista
birakiliyor.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "src"))

from finagent.config import load_settings          # noqa: E402
from finagent.ibkr.istemci import Istemci          # noqa: E402
from finagent.ibkr.piyasa import Piyasa            # noqa: E402
from finagent.storage.db import Database           # noqa: E402


def _hedefler(db, secilen: list[str]) -> dict[str, str]:
    """conid -> sembol. conid'i olmayan sembol sorulamaz."""
    sql = """SELECT i.symbol, d.conid
             FROM identities d JOIN instruments i ON i.id = d.instrument_id
             WHERE d.conid IS NOT NULL AND d.conid <> ''"""
    satirlar = db.query(sql)
    if secilen:
        istenen = {s.upper() for s in secilen}
        satirlar = [r for r in satirlar if r["symbol"].upper() in istenen]
        bulunan = {r["symbol"].upper() for r in satirlar}
        for eksik in sorted(istenen - bulunan):
            print(f"  ! {eksik}: conid yok "
                  f"(once: python run.py collect --site ibkrkimlik)")
    return {r["conid"]: r["symbol"] for r in satirlar}


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_kip = "--json" in sys.argv

    s = load_settings()
    db = Database(s.db_path)
    hedef = _hedefler(db, argv)
    db.close()
    if not hedef:
        print("  conid'i olan sembol yok.")
        return 1

    ist = Istemci(s.get("ibkr.taban_url", None))
    try:
        with Piyasa(ist) as p:
            kotasyonlar = p.kotasyon(list(hedef))
    finally:
        ist.kapat()

    if json_kip:
        print(json.dumps({
            hedef[c]: {"conid": c, "son": q.son, "alis": q.alis,
                       "satis": q.satis, "orta": q.orta, "hacim": q.hacim,
                       "kip": q.kip, "erisim": q.erisim}
            for c, q in kotasyonlar.items()}, ensure_ascii=False))
        return 0

    print()
    print(f"  {'sembol':<10} {'son':>10} {'alis':>10} {'satis':>10} "
          f"{'hacim':>14}  kip")
    print("  " + "-" * 66)
    kipler: dict[str, int] = {}
    for c, q in sorted(kotasyonlar.items(), key=lambda kv: hedef[kv[0]]):
        kipler[q.kip] = kipler.get(q.kip, 0) + 1
        f = lambda v: "-" if v is None else f"{v:,.2f}"     # noqa: E731
        print(f"  {hedef[c]:<10} {f(q.son):>10} {f(q.alis):>10} "
              f"{f(q.satis):>10} {f(q.hacim):>14}  {q.kip}")

    print()
    print("  kip dagilimi: " + ", ".join(f"{k}={v}" for k, v in
                                         sorted(kipler.items())))
    if kipler.get("gecikmeli") or kipler.get("abone_degil"):
        print()
        print("  -> Gercek zamanli veri gelmiyor. IBKR ABD hisse/ETF icin")
        print("     ucretsiz gercek zamanli akis veriyor ama hesapta ETKIN")
        print("     olmasi gerekiyor. Client Portal -> Settings ->")
        print("     Market Data Subscriptions; ayrica yillik 'Market Data")
        print("     API Agreement' imzalanmis olmali.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
