#!/usr/bin/env python3
"""
TEK SEFERLIK ONARIM — araci adiyla acilmis hayalet enstrumanlari
katalogdaki gercek kayda baglar.

NEDEN GEREKLI: `insert_positions` 2026-08-18'e kadar enstrumani
kosulsuz `venue = account.upper()` ile acti. `bux` ve `binance` icin
tesadufen dogruydu (ikisinin de kendi katalogu var); `midas` icin
degildi — Midas bir BIST araci kurumu. Sonuc: 18 Agustos 07:41'de
`TRALT/MIDAS` (0 fiyat bari) acildi ve 10 adetlik pozisyon ona baglandi,
oysa `TRALT/BIST` 285 barla duruyordu.

KAPI ARTIK KAPALI (`Database.pozisyon_enstrumani`); bu betik yalnizca
GECMISI temizler. Idempotent: onarilacak sey kalmayinca hicbir sey
yapmaz.

    .venv/bin/python scripts/pozisyon_venue_onar.py           # yalnizca RAPOR
    .venv/bin/python scripts/pozisyon_venue_onar.py --uygula  # yaz
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.config import load_settings                     # noqa: E402
from finagent.storage.db import (                             # noqa: E402
    Database, HESAP_VENUE, KRIPTO_VENUE, POZISYONSUZ_VENUE,
)


def hayaletler(db: Database) -> list[dict]:
    """
    Pozisyonu tasiyan ama fiyat serisi OLMAYAN enstrumanlardan, ayni
    sembolun serisi DOLU bir karsiligi bulunanlar.

    Iki yonlu sartin ikisi de gerekli: seri yoklugu tek basina "hayalet"
    demek degil (yeni eklenmis olabilir), karsiligin varligi da tek
    basina yetmez (mesru olarak iki farkli piyasada islem goruyor
    olabilir). Ikisi birden ancak KOPYA ile aciklanir.
    """
    satirlar = db.query(
        """SELECT DISTINCT i.id, i.symbol, i.venue, p.account
             FROM positions p JOIN instruments i ON i.id = p.instrument_id
            WHERE NOT EXISTS (SELECT 1 FROM prices q
                               WHERE q.instrument_id = i.id)""")
    out = []
    for r in satirlar:
        tercih = HESAP_VENUE.get(r["account"].lower(), r["account"].upper())
        kripto_mu = tercih in KRIPTO_VENUE
        adaylar = [
            a for a in db.query(
                """SELECT i.id, i.venue,
                          (SELECT COUNT(*) FROM prices q
                            WHERE q.instrument_id = i.id) AS bar
                     FROM instruments i WHERE UPPER(i.symbol) = ?""",
                (r["symbol"].upper(),))
            if a["id"] != r["id"] and a["bar"] > 0
            and a["venue"] not in POZISYONSUZ_VENUE
            and ((a["venue"] in KRIPTO_VENUE) == kripto_mu)
        ]
        adaylar.sort(key=lambda a: (a["venue"] != tercih, a["venue"]))
        if adaylar:
            out.append({"eski": r["id"], "sembol": r["symbol"],
                        "eski_venue": r["venue"], "hesap": r["account"],
                        "yeni": adaylar[0]["id"],
                        "yeni_venue": adaylar[0]["venue"],
                        "bar": adaylar[0]["bar"]})
    return out


def main() -> int:
    uygula = "--uygula" in sys.argv
    s = load_settings()
    db = Database(s.db_path)

    isler = hayaletler(db)
    if not isler:
        print("Onarilacak hayalet enstruman yok.")
        return 0

    for h in isler:
        print(f"  {h['sembol']:8} {h['hesap']:8} "
              f"{h['eski_venue']}(id {h['eski']}, 0 bar) -> "
              f"{h['yeni_venue']}(id {h['yeni']}, {h['bar']} bar)")

    if not uygula:
        print(f"\n{len(isler)} kayit onarilacak. Yazmak icin: --uygula")
        return 0

    tasinan = silinen = 0
    for h in isler:
        with db.tx() as c:
            # HEDEFTE AYNI ANAHTAR VARSA once onu temizle: positions PK'si
            # (sahip, snapshot_ts, account, instrument_id) ve cakisirsa
            # UPDATE patlar. Kopyanin verisi zaten hedefte duruyor.
            c.execute(
                """DELETE FROM positions WHERE instrument_id = ?
                     AND (sahip, snapshot_ts, account) IN
                         (SELECT sahip, snapshot_ts, account FROM positions
                           WHERE instrument_id = ?)""", (h["eski"], h["yeni"]))
            cur = c.execute(
                "UPDATE positions SET instrument_id = ? WHERE instrument_id = ?",
                (h["yeni"], h["eski"]))
            tasinan += cur.rowcount
            # Izleme listesi de tasinsin, sonra hayalet dussun. CASCADE
            # zaten silerdi ama o zaman izleme kaydi SESSIZCE kaybolurdu.
            c.execute(
                "INSERT OR IGNORE INTO watchlist (instrument_id, kind, note) "
                "SELECT ?, kind, note FROM watchlist WHERE instrument_id = ?",
                (h["yeni"], h["eski"]))
            c.execute("DELETE FROM instruments WHERE id = ?", (h["eski"],))
            silinen += 1

    print(f"\n{tasinan} pozisyon tasindi, {silinen} hayalet enstruman silindi.")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
