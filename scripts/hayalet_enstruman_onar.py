#!/usr/bin/env python
"""
22 AGUSTOS TICKER AVININ BIRAKTIGI 19 KAYDI ONARIR.

NE OLDU
-------
22 Agustos aksami portfoy cesitlendirmesi konusulurken bir ticker avi
yapildi: SXLE, SXLP, SXLV, GDX, XLE ve borsa sonekli varyantlari
denendi. `izlemeye_al` o sirada sembolun VAR OLUP OLMADIGINI hic
sormadan kalici kayit aciyordu (kok neden `bot/tools.py` icinde
kapatildi). Sonuc: 19 enstruman, hepsi `venue=BUX`, hepsi ADSIZ.

Kayitlar `watchlist` uzerinden toplama kapsamina giriyor, yani `prices`
her kosuda onlari deneyip `partial` donuyordu. 23 Agustos 13:57'de
bekci "3 kosudur eksik toplama" alarmi verdi.

NEDEN "HEPSINI SIL" DEGIL
-------------------------
Bu depoda ayni ders iki kez alindi (bkz. hayalet enstruman notlari):
kayit temizligi duzeltme SAYILMAZ, ve silmeden ONCE ne olduklarina
bakilmali. Bakildi (2026-08-23, Yahoo'ya tek tek soruldu) ve cikan sey
"hepsi cop" DEGILDI:

    SXLE  SXLB  SXLI  SXLU  -> sade sembol olu, ama `.AS` CALISIYOR
    SXLV.DE                 -> `.DE` olu, `.AS` CALISIYOR
    XLE                     -> ABD kotasyonu GECERLI, sadece ADSIZDI
    BRK-B                   -> gecerli ama BRK.B (id 302) ZATEN VAR
    ETFP.AS                 -> hicbir borsada kotasyon YOK

Yani 19'un 13'u gercek enstrumandi; eksik olan sey ad ya da dogru
borsa sonegiydi.

UC ISLEM
--------
  A) AD GERI DOLDUR — verisi gelen ama adsiz kalanlar. Ad eksikligi
     masum degil: adsiz kayit kimlik dogrulamasini KALICI olarak
     dusuruyor (`ayni_sirket` karsilastiracak bir sey bulamiyor) ve
     `research_targets` tekilligi de ada bagli oldugu icin ayni sirket
     iki kez taraniyor.
  B) SEMBOL DUZELT — olu sembolu CALISAN kotasyonla degistir.
  C) SIL — baska bir kaydin kopyasi olanlar ve hicbir borsada
     bulunmayanlar.

Kullanim:
    .venv/bin/python scripts/hayalet_enstruman_onar.py          # KURU
    .venv/bin/python scripts/hayalet_enstruman_onar.py --yaz    # uygula
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.config import load_settings                      # noqa: E402
from finagent.storage.db import Database                       # noqa: E402

# A) id -> yalnizca ad yazilacak (sembol dogru, verisi geliyor)
AD_DOLDUR = ["2B78.DE", "EIMI.L", "GDX.MI", "GOLD.AS", "IWDA.AS",
             "SGLN.L", "SXLP.L", "XLE"]

# B) olu sembol -> calisan kotasyon
SEMBOL_DUZELT = {
    "SXLE":    "SXLE.AS",
    "SXLB":    "SXLB.AS",
    "SXLI":    "SXLI.AS",
    "SXLU":    "SXLU.AS",
    "SXLV.DE": "SXLV.AS",
}

# C2) VERISI OLDUGU HALDE silinecekler — HER BIRI ICIN IKIZ ADI SART.
#
# Asagidaki `SIL` dongusu bar sayisi > 0 olan kayda DOKUNMUYOR ve bu
# koruma dogru: veri kaybi geri alinamaz. Ama sade (soneksiz) sembol
# ikizleri tam olarak bu tuzaga giriyordu — barlari VAR ama Yahoo
# onlari TANIMIYOR (hangi borsa oldugu yazmiyor), yani her kosuda
# "sembol yok" deyip `partial` uretiyorlar.
#
# Koruma genel olarak gevsetilmedi: silinebilmesi icin IKIZIN ADI
# acikca yazilmali ve o ikizin BAR SAYISI EN AZ o kadar olmali.
# Kosul saglanmazsa kayit DURUR.
SIL_IKIZLI = {
    "SXLV": "SXLV.AS",   # ayni fon, Amsterdam kotasyonu (BUX oradan)
    "2B78": "2B78.DE",   # ayni fon, Xetra kotasyonu
}

# C) sembol -> silme gerekcesi
SIL = {
    "BRK-B":   "BRK.B (id 302) ayni enstruman ve kimligi DOGRULANMIS "
               "(cik 0001067983, sec_ticker BRK-B) — fiyati o kayit ceker",
    "GDX.AS":  "GDX.MI ayni fon ve 503 bari VAR",
    "SXLP":    "SXLP.L ayni fon ve 505 bari VAR",
    "SXLP.DE": "SXLP.L ayni fon ve 505 bari VAR",
    "SXLE.DE": "SXLE.AS ile degistirilen kaydin kopyasi",
    "ETFP.AS": "hicbir borsada kotasyon bulunamadi (.L/.MI/.DE/.AS/.PA/.SW)",
}


def _yahoo_adi(sembol: str) -> str | None:
    from finagent.collectors.prices import yahoo_veri
    try:
        satirlar, meta = yahoo_veri(sembol, "5d", ad_gerek=True)
    except Exception as e:                                     # noqa: BLE001
        print(f"    ! {sembol} sorgulanamadi: {e}")
        return None
    if not satirlar:
        return None
    return (meta or {}).get("shortName") or None


def main() -> int:
    yaz = "--yaz" in sys.argv
    db = Database(load_settings().db_path)
    print("KURU CALISMA — hicbir sey yazilmayacak\n" if not yaz
          else "YAZMA KIPI\n")

    def bul(sem):
        r = db.query("SELECT id, symbol, name FROM instruments WHERE symbol=?",
                     (sem,))
        return r[0] if r else None

    # --- GUVENLIK: pozisyona bagli hicbir kayda dokunulmaz -------------
    hepsi = AD_DOLDUR + list(SEMBOL_DUZELT) + list(SIL)
    for sem in hepsi:
        r = bul(sem)
        if not r:
            continue
        n = db.query("SELECT COUNT(*) c FROM positions WHERE instrument_id=?",
                     (r["id"],))[0]["c"]
        if n:
            print(f"DUR: {sem} bir POZISYONA bagli ({n} kayit). "
                  "Bu betik pozisyonlu kayda dokunmaz.")
            return 1

    print("A) AD GERI DOLDUR")
    for sem in AD_DOLDUR:
        r = bul(sem)
        if not r:
            print(f"  - {sem:<10} katalogda yok, atlandi")
            continue
        if (r["name"] or "").strip():
            print(f"  = {sem:<10} adi zaten var: {r['name']!r}")
            continue
        ad = _yahoo_adi(sem)
        if not ad:
            print(f"  ! {sem:<10} Yahoo ad dondurmedi, DOKUNULMADI")
            continue
        print(f"  + {sem:<10} ad <- {ad!r}")
        if yaz:
            db.query("UPDATE instruments SET name=? WHERE id=?", (ad, r["id"]))

    print("\nB) SEMBOL DUZELT (olu -> calisan kotasyon)")
    for eski, yeni in SEMBOL_DUZELT.items():
        r = bul(eski)
        if not r:
            print(f"  - {eski:<10} katalogda yok, atlandi")
            continue
        if bul(yeni):
            print(f"  ! {eski:<10} -> {yeni} ZATEN VAR; celismemek icin "
                  "dokunulmadi (elle bakilmali)")
            continue
        ad = _yahoo_adi(yeni)
        if not ad:
            print(f"  ! {eski:<10} -> {yeni} veri dondurmedi, DOKUNULMADI")
            continue
        print(f"  ~ {eski:<10} -> {yeni:<10} ad <- {ad!r}")
        if yaz:
            db.query("UPDATE instruments SET symbol=?, name=? WHERE id=?",
                     (yeni, ad, r["id"]))

    print("\nC) SIL (kopya ya da hicbir yerde yok)")
    for sem, gerekce in SIL.items():
        r = bul(sem)
        if not r:
            print(f"  - {sem:<10} katalogda yok, atlandi")
            continue
        bar = db.query("SELECT COUNT(*) c FROM prices WHERE instrument_id=?",
                       (r["id"],))[0]["c"]
        if bar:
            print(f"  ! {sem:<10} {bar} bari VAR — silinmedi, elle bakilmali")
            continue
        print(f"  x {sem:<10} {gerekce}")
        if yaz:
            # SIRA ONEMLI: once bagli kayitlar, sonra enstruman
            # (yabanci anahtar acik).
            db.query("DELETE FROM watchlist WHERE instrument_id=?", (r["id"],))
            db.query("DELETE FROM identities WHERE instrument_id=?", (r["id"],))
            db.query("DELETE FROM instruments WHERE id=?", (r["id"],))

    print("\nC2) SADE SEMBOL IKIZI SIL (verisi olsa da — ikiz sart)")
    for sade, ikiz in SIL_IKIZLI.items():
        r, t = bul(sade), bul(ikiz)
        if not r:
            print(f"  - {sade:<10} katalogda yok, atlandi")
            continue
        if not t:
            print(f"  ! {sade:<10} IKIZ {ikiz} YOK — silinmedi")
            continue
        bar_r = db.query("SELECT COUNT(*) c FROM prices WHERE instrument_id=?",
                         (r["id"],))[0]["c"]
        bar_t = db.query("SELECT COUNT(*) c FROM prices WHERE instrument_id=?",
                         (t["id"],))[0]["c"]
        if bar_t < bar_r:
            print(f"  ! {sade:<10} ikiz {ikiz} DAHA AZ bar tasiyor "
                  f"({bar_t} < {bar_r}) — silinmedi, veri kaybi olurdu")
            continue
        print(f"  x {sade:<10} -> {ikiz} ({bar_t} bar, sade sembolde {bar_r}); "
              "sade sembolu Yahoo TANIMIYOR")
        if yaz:
            db.query("DELETE FROM watchlist WHERE instrument_id=?", (r["id"],))
            db.query("DELETE FROM identities WHERE instrument_id=?", (r["id"],))
            db.query("DELETE FROM prices WHERE instrument_id=?", (r["id"],))
            db.query("DELETE FROM instruments WHERE id=?", (r["id"],))

    if yaz:
        db._conn.commit()
        print("\nYAZILDI.")
    else:
        print("\n(kuru calisma — uygulamak icin --yaz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
