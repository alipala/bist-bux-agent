#!/usr/bin/env python
"""
Sema 37 oncesi emirlere `emir_kanit` geri doldurur.

NEDEN: kanit emir HAZIRLANIRKEN toplaniyor; 2 Ekim'den onceki 16 emirde
hic yok. Kanit yalnizca o anda kayitta ne oldugunu soyler ve kayit
geriye donuk degismiyor (tahmin defteri ve sohbet arsivi ekleme-yalniz),
yani sonradan toplamak ayni sonucu verir — `emir_kanit.topla` emrin
`olusma_ts`inden geriye bakiyor, bugunden degil.

`kanal` ve `beyan` DOLDURULMAZ. Kanal o gun yazilmadi ve kayittan kesin
cikmiyor; beyan Ali'nin niyeti. Ikisi de NULL kalir ("bilinmiyor").

Kullanim:
    .venv/bin/python scripts/emir_kanit_geriye.py           # kuru: yalnizca gosterir
    .venv/bin/python scripts/emir_kanit_geriye.py --yaz
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finagent.config import load_settings  # noqa: E402
from finagent.pulse.emir_kanit import topla  # noqa: E402
from finagent.storage.db import Database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaz", action="store_true",
                    help="kanitlari yaz (varsayilan: yalnizca goster)")
    a = ap.parse_args()
    db = Database(load_settings().db_path)
    db.init_schema()
    emirler = db.query(
        """SELECT e.id, i.symbol, e.yon, e.durum, e.olusma_ts
           FROM emirler e LEFT JOIN instruments i ON i.id = e.instrument_id
           WHERE NOT EXISTS (SELECT 1 FROM emir_kanit k WHERE k.emir_id = e.id)
           ORDER BY e.id""")
    toplam, bos = 0, 0
    for e in emirler:
        ks = topla(db, e["id"], yaz=a.yaz)
        toplam += len(ks)
        bos += not ks
        ozet = {}
        for k in ks:
            ozet[k["tur"]] = ozet.get(k["tur"], 0) + 1
        oneri = ", ".join(
            f"{k['ajan']}:{k['yon']}{'' if k['uyumlu'] else '(uyumsuz)'}"
            f"@{k['saat_once']:.0f}s" for k in ks if k["tur"] == "oneri")
        video = ", ".join(f"{k['saat_once']:.1f}s" for k in ks
                          if k["tur"] == "video")
        print(f"#{e['id']:>3} {e['symbol'] or '?':6} {e['yon']:4} "
              f"{e['durum']:13} {e['olusma_ts'][:16]}  "
              f"oneri={ozet.get('oneri', 0)} [{oneri}]  "
              f"video={ozet.get('video', 0)} [{video}]  "
              f"danisma={ozet.get('danisma', 0)}")
    print(f"\n{len(emirler)} emir, {toplam} kanit, {bos} emirde kanit YOK"
          + ("" if a.yaz else "  — KURU KOSU, hicbir sey yazilmadi"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
