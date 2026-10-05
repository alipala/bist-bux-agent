#!/usr/bin/env python3
"""
YANLISLIKLA ACILMIS ENSTRUMANI DOGRU KAYDA BIRLESTIRIR — varsayilan KURU.

    .venv/bin/python scripts/enstruman_birlestir.py 1830:233 1831:234 ...
    .venv/bin/python scripts/enstruman_birlestir.py ... --yaz

NEDEN (2026-10-05): 2 Ekim'de sohbet modeli BUX pozisyonlarini uydurma
sembollerle yazdi (`TESLA`, `PALANTIR`, `SERVICENOW`, `4GLD`, `NAKIT`) ve
her biri YENI bir enstruman acti. Kagitlarin fiyat gecmisi, stop seviyesi
ve tahmin gecmisi eski kayitlarda kaldi. Bu betik pozisyonlari ESKI
(dogru) kayda tasir ve kopyayi siler.

TABLO BAZINDA KURAL — BILINMEYEN TABLODA DURUR
  tasinir   positions, sohbet_sembol (ayni anahtar varsa kopya atilir)
  koruma    hedefte satir varsa kaynaginki SILINIR (hedefin stop gecmisi
            korunur; kirilmis olani koruma kendi kuraliyla yeniden kurar),
            yoksa TASINIR
  silinir   prices, prices_hourly, fundamentals, identities — kopya veri;
            hedefin kendi verisi var
  baska bir tabloda kaynaga bag varsa (predictions, signals, emirler,
  watchlist...) betik HICBIR SEY YAZMADAN durur: anlamini bilmeden
  tasimak/silmek veri kaybidir.

Hedefin fiyat serisi YOKSA durur: serisiz bir hedefe birlestirmek hatanin
kendisini tekrarlar. Tek islem (transaction): ya hepsi ya hicbiri.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

TASINIR = ("positions", "sohbet_sembol")
SILINIR = ("prices", "prices_hourly", "fundamentals", "identities")
OZEL = ("koruma",)


def _tablolar(c) -> list[str]:
    return [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND sql LIKE '%instrument_id%'")]


def plan(c, ciftler: list[tuple[int, int]]) -> tuple[list[str], list[str]]:
    """(islem_satirlari, engeller). Engel varsa YAZILMAZ."""
    islem, engel = [], []
    tablolar = _tablolar(c)
    for k, h in ciftler:
        ks = c.execute("SELECT symbol, venue FROM instruments WHERE id=?", (k,)).fetchone()
        hs = c.execute("SELECT symbol, venue FROM instruments WHERE id=?", (h,)).fetchone()
        if not ks or not hs:
            engel.append(f"{k}->{h}: enstruman yok ({ks}, {hs})")
            continue
        if k == h:
            engel.append(f"{k}: kaynak ve hedef ayni")
            continue
        hedef_nakit = c.execute(
            "SELECT COALESCE(asset_type,'')='cash' FROM instruments WHERE id=?",
            (h,)).fetchone()[0]
        seri = c.execute("SELECT COUNT(*) FROM prices WHERE instrument_id=?",
                         (h,)).fetchone()[0]
        if not seri and not hedef_nakit:
            engel.append(f"{k}->{h}: hedef {hs[0]} FIYAT SERISIZ")
        islem.append(f"== {ks[0]} ({k}, {ks[1]})  ->  {hs[0]} ({h}, {hs[1]})")
        for t in tablolar:
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE instrument_id=?",
                          (k,)).fetchone()[0]
            if not n:
                continue
            if t in TASINIR:
                islem.append(f"   {t}: {n} satir TASINIR")
                if t == "positions":
                    cak = c.execute(
                        """SELECT COUNT(*) FROM positions a JOIN positions b
                           ON a.sahip=b.sahip AND a.snapshot_ts=b.snapshot_ts
                          AND a.account=b.account
                           WHERE a.instrument_id=? AND b.instrument_id=?""",
                        (k, h)).fetchone()[0]
                    if cak:
                        engel.append(f"{k}->{h}: ayni anlik goruntude ikisi "
                                     f"birden var ({cak}) — elle bak")
            elif t in SILINIR:
                islem.append(f"   {t}: {n} satir SILINIR (kopya)")
            elif t == "koruma":
                hk = c.execute("SELECT COUNT(*) FROM koruma WHERE instrument_id=?",
                               (h,)).fetchone()[0]
                islem.append(f"   koruma: {n} satir "
                             + ("SILINIR (hedefin stop gecmisi korunur)"
                                if hk else "TASINIR"))
            else:
                engel.append(f"{k}: bilinmeyen tabloda {n} bag: {t}")
        islem.append(f"   instruments: {k} SILINIR")
    return islem, engel


def uygula(c, ciftler: list[tuple[int, int]]) -> None:
    for k, h in ciftler:
        c.execute("UPDATE positions SET instrument_id=? WHERE instrument_id=?", (h, k))
        c.execute("""INSERT OR IGNORE INTO sohbet_sembol (kayit_id, instrument_id)
                     SELECT kayit_id, ? FROM sohbet_sembol WHERE instrument_id=?""",
                  (h, k))
        c.execute("DELETE FROM sohbet_sembol WHERE instrument_id=?", (k,))
        if c.execute("SELECT COUNT(*) FROM koruma WHERE instrument_id=?",
                     (h,)).fetchone()[0]:
            c.execute("DELETE FROM koruma WHERE instrument_id=?", (k,))
        else:
            c.execute("UPDATE koruma SET instrument_id=? WHERE instrument_id=?", (h, k))
        for t in SILINIR:
            c.execute(f"DELETE FROM {t} WHERE instrument_id=?", (k,))
        kalan = [t for t in _tablolar(c) if c.execute(
            f"SELECT COUNT(*) FROM {t} WHERE instrument_id=?", (k,)).fetchone()[0]]
        if kalan:
            raise RuntimeError(f"{k}: silmeden once hala bag var: {kalan}")
        c.execute("DELETE FROM instruments WHERE id=?", (k,))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ciftler", nargs="+", help="kaynak:hedef (enstruman id)")
    ap.add_argument("--db", default="data/finagent.db")
    ap.add_argument("--yaz", action="store_true", help="YAZ (varsayilan kuru)")
    a = ap.parse_args(argv)
    ciftler = [tuple(int(x) for x in s.split(":")) for s in a.ciftler]
    c = sqlite3.connect(a.db)
    islem, engel = plan(c, ciftler)
    print("\n".join(islem))
    if engel:
        print("\nENGEL — hicbir sey yazilmadi:\n  " + "\n  ".join(engel))
        return 2
    if not a.yaz:
        print("\nKURU CALISMA — yazmak icin --yaz")
        return 0
    try:
        c.execute("BEGIN IMMEDIATE")
        uygula(c, ciftler)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    print("\nYAZILDI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
