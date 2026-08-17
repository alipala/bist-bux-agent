#!/usr/bin/env python3
"""
Mevcut haberlerin YAYINCI ve KADEME alanlarini yeniden hesaplar.

NEDEN GEREKTI (olculdu 2026-08-17): 1.103 haberin 405'i kademe 0
("bilinmeyen") idi, yani KANIT SAYILMIYORDU. Iki ayri sebep vardi:
  1. `news` collector'i `publisher` ve `tier` alanlarini HIC yazmiyordu.
     181 haber `publisher=NULL` ile duruyordu; kademe fonksiyonu yayinci
     adini hic gormedigi icin AA, BloombergHT, Dunya, WSJ gibi mesru
     yayincilar da "bilinmeyen"e dusuyordu.
  2. Kademe tablolarinda TR yayincilari, tel servisleri ve kripto
     basini eksikti.

IDEMPOTENT ve KADEME DUSURMEZ dısında bir sey yapmaz — asagidaki
kurallara bak. Kaynak listeleri degistikce tekrar kosturulabilir.

DIKKAT — BIR KEZ YANLIS YAPILDI: kademeyi dogrudan `kademe(publisher)`
ile ezmek KADEME 1'I SILER, cunku `kademe()` asla 1 dondurmez; 1
`sirket_kaynagi_kademe()` ile atanir (sirketin kendi IR/haber odasi).
Bu betik once genel kademeyi hesaplar, SONRA sirket kanallarini geri
kazanir. Sirasi degistirilmemeli.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.config import load_settings                        # noqa: E402
from finagent.research.sources import (bilinmeyen_yayincilar,     # noqa: E402
                                       kademe, sirket_kaynagi_kademe)
from finagent.storage.db import Database                          # noqa: E402


def main() -> int:
    s = load_settings()
    db = Database(s.db_path)
    db.init_schema()

    onceki = {r["tier"]: r["n"] for r in db.query(
        "SELECT tier, COUNT(*) n FROM news GROUP BY tier")}

    # 1) publisher bos ise source'tan doldur, genel kademeyi hesapla.
    n_pub = n_tier = 0
    with db.tx() as c:
        for r in db.query("SELECT id, source, publisher, tier FROM news"):
            pub = r["publisher"] or r["source"]
            if not r["publisher"] and pub:
                c.execute("UPDATE news SET publisher=? WHERE id=?",
                          (pub, r["id"]))
                n_pub += 1
            k = kademe(pub)
            if k != r["tier"]:
                c.execute("UPDATE news SET tier=? WHERE id=?", (k, r["id"]))
                n_tier += 1

    # 2) SIRKET KANALLARINI GERI KAZAN. Adim 1 bunlari sifirlar.
    adlar = {r["symbol"]: r["name"] for r in db.query(
        "SELECT symbol, name FROM instruments WHERE name IS NOT NULL")}
    n1 = n3 = 0
    with db.tx() as c:
        for r in db.query("SELECT id, publisher, url, symbols, tier FROM news "
                          "WHERE tier = 0 AND publisher IS NOT NULL"):
            for sem in (r["symbols"] or "").split(","):
                ad = adlar.get(sem.strip().upper())
                if not ad:
                    continue
                k = sirket_kaynagi_kademe(r["publisher"], ad, r["url"])
                if k is None:
                    continue
                c.execute("UPDATE news SET tier=? WHERE id=?", (k, r["id"]))
                n1 += (k == 1)
                n3 += (k == 3)
                break

    sonraki = {r["tier"]: r["n"] for r in db.query(
        "SELECT tier, COUNT(*) n FROM news GROUP BY tier")}

    print(f"publisher dolduruldu : {n_pub}")
    print(f"kademe guncellendi   : {n_tier}")
    print(f"sirket kanali        : kademe1={n1}, kademe3={n3}")
    print("\nkademe   once   sonra")
    for k in range(5):
        print(f"  {k}    {onceki.get(k, 0):>6} {sonraki.get(k, 0):>7}")
    kanit_once = onceki.get(1, 0) + onceki.get(2, 0)
    kanit_son = sonraki.get(1, 0) + sonraki.get(2, 0)
    print(f"\nKANIT SAYILABILIR (kademe 1-2): {kanit_once} -> {kanit_son}")

    kalan = bilinmeyen_yayincilar(db, 15)
    if kalan:
        print(f"\nHala cozulemeyen ({sum(x['haber'] for x in kalan)} haber, "
              "ilk 15 yayinci):")
        for x in kalan:
            print(f"  {x['yayinci'][:44]:<44} {x['haber']:>4}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
