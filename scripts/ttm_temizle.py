#!/usr/bin/env python3
"""
`midas` collector'unun yazdigi TTM net kar satirlarini duzeltir.

SORUN (olculdu 2026-08-17): Midas detay sayfasindaki "Net Kâr" SON 12 AY
(TTM) degeridir — takvim donemi degil. Collector bunu su sekilde
yaziyordu:
    concept='NetKar', period_end=<toplama gunu>, period_start=NULL, days=NULL

Uc ayri zarari vardi:
  1. `midasbilanco` ayni `NetKar` adiyla GERCEK takvim donemlerini
     yaziyor. TTM satiri `period_end` en buyuk oldugu icin "en son
     donem" gibi siralaniyordu: ASELS icin gercek H1-2026 kari 14,4
     milyar iken sorgu 41,2 milyar (TTM) donduruyordu.
  2. `days=NULL` oldugu icin `finansal_seri(donem="anlik")` filtresi
     (`days IS NULL`) bu AKIM buyuklugunu BILANCO ANLIK KALEMI olarak
     servis ediyordu.
  3. Projenin "farkli uzunluktakiler karsilastirilmaz" korumasi
     `days` uzerinden calisiyor; NULL iken hic ateslenemiyordu.

DUZELTME: concept -> `NetKarTTM`, days=365, period_start=period_end-365,
fp='TTM'. Ayri kavram adi, iki serinin karismasini YAPISAL olarak
imkansiz kiliyor.

TTM'in DOGRULANMASI: AKBNK, EREGL ve GARAN icin
FY2025 - H1'2025 + H1'2026 formulu %0,0 sapmayla tutuyor.

Idempotent — tekrar kosturulabilir.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.config import load_settings          # noqa: E402
from finagent.storage.db import Database           # noqa: E402


def main() -> int:
    db = Database(load_settings().db_path)
    db.init_schema()

    kirli = db.query(
        "SELECT rowid, instrument_id, period_end, val FROM fundamentals "
        "WHERE form='midas' AND concept='NetKar'")
    print(f"duzeltilecek satir: {len(kirli)}")
    if not kirli:
        print("temiz.")
        # Yine de sonucu goster.
        n = db.query("SELECT COUNT(*) c FROM fundamentals "
                     "WHERE concept='NetKarTTM'")[0]["c"]
        print(f"mevcut NetKarTTM satiri: {n}")
        db.close()
        return 0

    etkilenen = {r["instrument_id"] for r in kirli}
    with db.tx() as c:
        for r in kirli:
            son = str(r["period_end"])[:10]
            try:
                bas = (date.fromisoformat(son) - timedelta(days=365)).isoformat()
            except ValueError:
                bas = None
            # ON CONFLICT anahtarinin parcasi olan `days` degistigi icin
            # dogrudan UPDATE yeterli; ayni (instrument, NetKarTTM,
            # period_end, 365, midas, TRY) satiri zaten varsa cakisir —
            # o durumda eskisini SIL, cunku degeri aynidir.
            try:
                c.execute(
                    "UPDATE fundamentals SET concept='NetKarTTM', days=365, "
                    "period_start=?, fp='TTM' WHERE rowid=?", (bas, r["rowid"]))
            except Exception:                       # noqa: BLE001
                c.execute("DELETE FROM fundamentals WHERE rowid=?", (r["rowid"],))

    print(f"duzeltildi: {len(kirli)} satir, {len(etkilenen)} sirket")
    print()
    print("=== SONRASI: ornek sirket serisi ===")
    ornek = db.query(
        """SELECT i.symbol, f.concept, f.period_start, f.period_end, f.days,
                  f.val, f.form
           FROM fundamentals f JOIN instruments i ON i.id=f.instrument_id
           WHERE f.instrument_id = ? AND f.concept IN ('NetKar','NetKarTTM')
           ORDER BY f.period_end DESC LIMIT 8""", (sorted(etkilenen)[0],))
    for r in ornek:
        print(f"  {r['symbol']:<7} {r['concept']:<10} "
              f"{str(r['period_start']):<12} -> {r['period_end']}  "
              f"days={str(r['days']):<5} {r['val']:>18,.0f}  {r['form']}")

    kalan = db.query("SELECT COUNT(*) c FROM fundamentals "
                     "WHERE form='midas' AND concept='NetKar'")[0]["c"]
    print(f"\nkalan kirli satir: {kalan}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
