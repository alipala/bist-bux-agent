#!/usr/bin/env python3
"""
Arsiv arama olcum kosumu — altin kumeye karsi recall@3 ve MRR.

    .venv/bin/python scripts/arama_olc.py like
    .venv/bin/python scripts/arama_olc.py like fts5 gomme hibrit

NEDEN AYRI BIR KOSUM, DUMAN TESTI DEGIL
---------------------------------------
Bu bir DOGRULUK testi degil, bir OLCUM. Duman testi "bozuk mu" diye
sorar ve ikili cevap verir; burada sorulan "ne kadar iyi" ve cevap bir
sayi. Ayrica canli veritabanini ve (gomme icin) ayakta bir Ollama
sunucusunu gerektiriyor — ikisi de duman testinin sozlesmesini bozardi
("testler gercek is yapmamali", README kural 3).

ZAMANA BAGLILIK YOK
-------------------
`gun=3650` sabit. Varsayilan 30 gun BUGUN calisirdi (arsiv 16-19 Agu,
bugun 20 Agu) ama olcumu takvime bagli hale getirirdi. Bu projede tam
bu yuzden iki test curudu ve suite'i kilitledi; ayni tuzak burada
kurulmuyor.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from finagent.storage.db import Database          # noqa: E402

# Altin kume `gun` penceresine takilmamali — bkz. modul basligi.
GUN = 3650

# Kac sonuc istenir. recall@3 icin 3 yeterdi ama MRR'in anlamli olmasi
# icin ilk isabet 3'un disinda da bulunabilmeli; aksi halde "4. sirada
# buldu" ile "hic bulamadi" ayni puani alir ve yontemler arasindaki
# gercek fark gizlenir.
K = 20


# ----------------------------------------------------------------------
# Olcum
# ----------------------------------------------------------------------
@dataclass
class Sonuc:
    kategori: str
    varyant: str            # "sapkali" | "sapkasiz"
    sorgu: str
    isabet_sirasi: int | 0  # 1-tabanli; 0 = hic bulamadi
    donen: int


def _isabet_sirasi(donen_idler: list[int], kabul: list[int]) -> int:
    """Ilk kabul edilen id'nin 1-tabanli sirasi; yoksa 0."""
    kabul_kumesi = set(kabul)
    for sira, tid in enumerate(donen_idler, start=1):
        if tid in kabul_kumesi:
            return sira
    return 0


def olc(ara: Callable[[str, str, int], list[int]], kume: dict) -> list[Sonuc]:
    """
    `ara(sorgu, sahip, k) -> [tur_id, ...]` imzasindaki her yontemi
    altin kumeye karsi kosturur. Yontem hakkinda BASKA hicbir sey
    varsaymaz — LIKE, FTS5, gomme ve hibrit ayni kapidan gecer, yoksa
    karsilastirma gecersiz olur.
    """
    cikti: list[Sonuc] = []
    for kayit in kume["kayitlar"]:
        for varyant, alan in (("sapkali", "sorgu"), ("sapkasiz", "sorgu_ascii")):
            sorgu = kayit[alan]
            donen = ara(sorgu, kayit["sahip"], K)
            cikti.append(Sonuc(
                kategori=kayit["kategori"],
                varyant=varyant,
                sorgu=sorgu,
                isabet_sirasi=_isabet_sirasi(donen, kayit["kabul_edilen_idler"]),
                donen=len(donen),
            ))
    return cikti


def ozet(sonuclar: list[Sonuc]) -> dict:
    """recall@3 ve MRR — HER KATEGORI AYRI.

    Tek ortalama hangi hata sinifinin cozuldugunu gizler: leksik bir
    yontem `tam_kelime`de %100 yapip `kelime_yok`da %0 kalabilir ve
    ortalama "fena degil" gorunur.
    """
    def hesapla(alt: list[Sonuc]) -> dict:
        if not alt:
            return {"recall@3": None, "mrr": None, "n": 0}
        r3 = sum(1 for s in alt if 1 <= s.isabet_sirasi <= 3) / len(alt)
        mrr = sum((1.0 / s.isabet_sirasi) if s.isabet_sirasi else 0.0
                  for s in alt) / len(alt)
        return {"recall@3": r3, "mrr": mrr, "n": len(alt)}

    out = {"hepsi": hesapla(sonuclar)}
    for kat in ("tam_kelime", "parafraz", "kelime_yok"):
        out[kat] = hesapla([s for s in sonuclar if s.kategori == kat])
    for v in ("sapkali", "sapkasiz"):
        out[f"varyant_{v}"] = hesapla([s for s in sonuclar if s.varyant == v])
    return out


# ----------------------------------------------------------------------
# Yontemler
# ----------------------------------------------------------------------
def yontem_like(db: Database) -> Callable[[str, str, int], list[int]]:
    """
    BUGUNKU DAVRANIS — hicbir sey degistirilmeden.

    `sohbet_ara` ALAKA SIRALAMASI YAPMAZ: eslesenlerin en YENI
    `limit` tanesini alip KRONOLOJIK (eskiden yeniye) dondurur. Yani
    "ilk 3" burada "en alakali 3" degil, "gosterilen ilk 3". Olcum
    modelin GERCEKTE gordugu sirayla yapilir, yoksa baseline oldugundan
    iyi gorunur.
    """
    def ara(sorgu: str, sahip: str, k: int) -> list[int]:
        satirlar = db.sohbet_ara(sahip, gun=GUN, sorgu=sorgu, limit=k)
        return [r["id"] for r in satirlar]
    return ara


def yontem_fts5(db: Database) -> Callable[[str, str, int], list[int]]:
    """
    T4 — trigram metin indeksi, `bm25` sirasiyla.

    LIKE'tan iki yapisal farki var: sorgu terimlere ayriliyor (tek bir
    dev `%...%` kalibi degil) ve sonuc ALAKA sirasinda geliyor.
    """
    def ara(sorgu: str, sahip: str, k: int) -> list[int]:
        satirlar = db.sohbet_ara_fts(sahip, gun=GUN, sorgu=sorgu, limit=k)
        return [r["id"] for r in satirlar]
    return ara


YONTEMLER: dict[str, Callable[[Database], Callable]] = {
    "like": yontem_like,
    "fts5": yontem_fts5,
}


# ----------------------------------------------------------------------
def _yuzde(x: float | None) -> str:
    return "  —  " if x is None else f"{x*100:5.1f}"


def _ondalik(x: float | None) -> str:
    return " — " if x is None else f"{x:.3f}"


def main(argv: list[str]) -> int:
    istenen = argv[1:] or ["like"]
    bilinmeyen = [a for a in istenen if a not in YONTEMLER]
    if bilinmeyen:
        print(f"bilinmeyen yontem: {bilinmeyen}; "
              f"gecerli: {sorted(YONTEMLER)}", file=sys.stderr)
        return 2

    kume = json.loads((KOK / "tests/altin_kume.json").read_text(encoding="utf-8"))
    db = Database(KOK / "data/finagent.db")

    basliklar = ["tam_kelime", "parafraz", "kelime_yok"]
    print(f"\naltin kume: {len(kume['kayitlar'])} kayit x 2 varyant "
          f"= {len(kume['kayitlar'])*2} sorgu   (gun={GUN}, K={K})\n")
    print(f"{'yontem':<14}" + "".join(f"{'r@3 '+b:>16}" for b in basliklar)
          + f"{'MRR':>8}{'r@3 hepsi':>12}")
    print("-" * (14 + 16*3 + 8 + 12))

    tum: dict[str, dict] = {}
    for ad in istenen:
        sonuclar = olc(YONTEMLER[ad](db), kume)
        o = ozet(sonuclar)
        tum[ad] = o
        print(f"{ad:<14}"
              + "".join(f"{_yuzde(o[b]['recall@3']):>16}" for b in basliklar)
              + f"{_ondalik(o['hepsi']['mrr']):>8}"
              + f"{_yuzde(o['hepsi']['recall@3']):>12}")

    print("\nvaryant kirilimi (r@3 / MRR):")
    for ad in istenen:
        o = tum[ad]
        print(f"  {ad:<12} sapkali  {_yuzde(o['varyant_sapkali']['recall@3'])} / "
              f"{_ondalik(o['varyant_sapkali']['mrr'])}"
              f"    sapkasiz {_yuzde(o['varyant_sapkasiz']['recall@3'])} / "
              f"{_ondalik(o['varyant_sapkasiz']['mrr'])}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
