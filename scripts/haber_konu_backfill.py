#!/usr/bin/env python3
"""
Mevcut haberlere KONU etiketi yazar. IDEMPOTENT.

NEDEN GEREKLI: `news.konu` kolonu sema 9'da eklendi; ondan onceki tum
kayitlar NULL. Gundem bolumu son 7 gune bakiyor, yani kolon eklendigi
gun rapor "Turkiye gundemi yok" derdi — veri VARKEN. Bu, projedeki en
kotu hata sinifinin (yanlis "yok" beyani) goc kaynakli bir bicimi.

KULLANIM
    python scripts/haber_konu_backfill.py            # kuru calisma
    python scripts/haber_konu_backfill.py --yaz      # yazar
    python scripts/haber_konu_backfill.py --yaz --hepsi   # mevcutlari da ezer

`--hepsi` verilmezse yalnizca NULL/bos olanlar doldurulur; kural seti
degistiginde tamamini yeniden siniflandirmak icin `--hepsi` gerekir.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.config import load_settings           # noqa: E402
from finagent.research.konular import KONULAR, konu  # noqa: E402
from finagent.storage import Database               # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaz", action="store_true", help="degisiklikleri kaydet")
    ap.add_argument("--hepsi", action="store_true",
                    help="mevcut etiketleri de yeniden hesapla")
    ap.add_argument("--ornek", type=int, default=12,
                    help="her konudan kac ornek gosterilsin")
    args = ap.parse_args()

    s = load_settings()
    db = Database(s.db_path)
    db.init_schema()

    kosul = "" if args.hepsi else "WHERE konu IS NULL OR konu = ''"
    rows = db.query(f"SELECT id, title, summary, symbols, konu, publisher, source FROM news {kosul}")
    print(f"islenecek haber: {len(rows)}")

    sayac: Counter = Counter()
    ornekler: dict[str, list[str]] = {k: [] for k in KONULAR}
    guncel = []
    for r in rows:
        yeni = konu(r["title"], r["summary"], r["symbols"],
                    r["publisher"] or r["source"])
        sayac[yeni] += 1
        if len(ornekler[yeni]) < args.ornek:
            ornekler[yeni].append((r["title"] or "")[:88])
        if yeni != r["konu"]:
            guncel.append((yeni, r["id"]))

    for k in KONULAR:
        if not sayac[k]:
            continue
        print(f"\n=== {k}  ({sayac[k]}) ===")
        for t in ornekler[k]:
            print(f"    {t}")

    print(f"\ndegisecek satir: {len(guncel)}")
    if args.yaz and guncel:
        with db.tx() as c:
            c.executemany("UPDATE news SET konu = ? WHERE id = ?", guncel)
        print("yazildi.")
    elif not args.yaz:
        print("(kuru calisma — yazmak icin --yaz)")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
