#!/usr/bin/env python3
"""
Yuvarlanan sohbet penceresinde HENUZ DURAN turlari kalici arsive tasir.

TEK SEFERLIK ama IDEMPOTENT. `data/bot/sohbet/<chat_id>.json` son 8 turu
tutup gerisini atiyor; arsiv tablosu bugun kuruldu. Yani o dosyalarda
duran turlar, arsivin baslangicindan ONCEKI son kalintilar — bir daha
yazilamazlar. Dosya bir sonraki mesajda uzerine yazilmadan alinmali.

NE GERI GELMEZ: dosyadan cikmis eski turlar. Onlar kayip; bu betik
kaybi telafi etmiyor, ELDE KALANI kurtariyor.

Ayni ts+chat_id+rol ucusu zaten varsa ATLANIR — betik tekrar tekrar
kosturulabilir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.config import load_settings          # noqa: E402
from finagent.storage.db import Database           # noqa: E402


def main() -> int:
    s = load_settings()
    db = Database(s.db_path)
    db.init_schema()

    kok = s.root / "data" / "bot" / "sohbet"
    if not kok.is_dir():
        print(f"sohbet dizini yok: {kok}")
        return 0

    yazilan = atlanan = damgasiz = 0
    for yol in sorted(kok.glob("*.json")):
        chat_id = yol.stem
        sahip = s.sahip_bul(chat_id)
        try:
            turlar = json.loads(yol.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  {yol.name}: okunamadi ({e})")
            continue

        for t in turlar:
            if not isinstance(t, dict):
                continue
            ts, rol = t.get("ts"), t.get("rol")
            metin = t.get("metin")
            if not ts:
                # DAMGASIZ tur eski bicimden kaliyor. Uydurma bir zaman
                # damgasi atmak arsivi yalanci yapardi; atla ve SOYLE.
                damgasiz += 1
                continue
            if rol not in ("user", "assistant") or not metin:
                continue
            var = db.query(
                """SELECT 1 FROM sohbet_kaydi
                   WHERE ts=? AND chat_id=? AND rol=? LIMIT 1""",
                (ts, chat_id, rol))
            if var:
                atlanan += 1
                continue
            db.sohbet_kaydet(chat_id, rol, metin, sahip=sahip, ts=ts)
            yazilan += 1

        print(f"  {yol.name}: sahip={sahip or 'YOK'}, {len(turlar)} tur okundu")

    print(f"\nyazilan: {yazilan}  zaten vardi: {atlanan}  damgasiz atlandi: {damgasiz}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
