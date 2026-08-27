#!/usr/bin/env python
"""
§8 — TEK ATISLIK DIS SINAV. Canli riske DOKUNMAZ, deftere YAZMAZ.

NE YAPAR
--------
Dondurulmus parametrelerle (belge §8.A) gecmis bir pencerede kurali ve
LLM kolunu YAN YANA kosturur, sonucu raporlar.

  1. Pencereden TOHUMLU RASTGELE karar gunleri secer.
  2. Her gun icin `strateji.tara(bitis=gun)` -> o gunun kirilimlari.
     Tarih kesmesi TEK KAPIDAN (`db.fiyat_serisi(bitis=)`).
  3. Her gun icin LLM koluna TEK cagri (dondurulmus prompt).
  4. Her sinyalin GERCEK sonucunu kuralin kendi cikis kurallariyla
     olcer (10 gun dip / 2N stop) — `trend_takip.islemler`den.
  5. Eslestirilmis kiyas: LLM "bekle" dediklerinin sonucu, "al"
     dediklerininkiyle karsilastirilir. ISARET TESTI.
  6. Rastgele kontrol ayni sembollerde, ayni sayida, ayni surede.

PENCERE BIR KEZ KULLANILIR
--------------------------
Sonucu gorup parametre ayarlarsan pencere YANAR. `--dogrulama` kipi
Haziran ONCESI gunlerde kosar ve MEKANIGI sinar; sinav penceresine
dokunmaz.

NE KANITLAMAZ
-------------
Kuralin KENARI oldugunu. 3 aylik gozlem birimiyle `t` hesaplanamaz
(belge §2.8) ve bu 4 ay sonra da boyle olacak. Sinav boru hattinin
gorulmemis veride calistigini ve LLM'in katkisinin isaretini olcer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finagent.analysis.trend_takip import islemler, rastgele_kontrol  # noqa: E402
from finagent.config import load_settings                            # noqa: E402
from finagent.pulse import strateji as ST                            # noqa: E402
from finagent.pulse import strateji_llm as SL                        # noqa: E402
from finagent.pulse.screener import BORSA_LIMITI                     # noqa: E402
from finagent.storage import Database                                # noqa: E402


def karar_gunleri(db, evren, bas: str, bit: str, adet: int,
                  tohum: int) -> list[str]:
    """
    Pencereden TOHUMLU RASTGELE karar gunleri. "Ilginc gunleri sec" YOK.

    GUN SECILIYOR, SINYAL DEGIL: LLM cagrisi gun basina TEK ve o gunun
    butun kirilimlarini goruyor. Sinyal secseydik ayni gun icin birden
    cok cagri gerekirdi.

    BEDELI YAZILIYOR: gun bazli secim KUMELENME uretir — ayni gunun
    kirilimlari bagimsiz gozlem DEGIL (belge §2.8: "6.208 islem
    BAGIMSIZ GOZLEM DEGIL; ayni ayin yuzlerce islemi TEK hareketi
    konusuyor"). Bu yuzden gun sayisi mumkun oldugunca YUKSEK tutuluyor.
    """
    # Islem gunleri VERIDEN, takvimden DEGIL: hangi gunlerde bar var?
    r = db.query(
        """SELECT DISTINCT ts FROM prices
           WHERE ts >= ? AND ts <= ? AND instrument_id IN
             (SELECT instrument_id FROM index_members)
           ORDER BY ts""", (bas, bit))
    gunler = [str(x["ts"])[:10] for x in r]
    if len(gunler) <= adet:
        return gunler
    return sorted(random.Random(tohum).sample(gunler, adet))


def sonuc_bul(seri, giris_ts: str, ayar) -> dict | None:
    """
    Bir sinyalin GERCEK sonucu — kuralin KENDI cikis kurallarindan.

    `islemler()` yeniden yazilmiyor, CAGRILIYOR: cikis mantigi
    (10 gun dip / 2N stop, gap-down, erteleme) tek yerde durmali.
    """
    limit = BORSA_LIMITI.get("BUX")
    for t in islemler(seri, limit, taban_kilidi=limit,
                      asgari_devir=ayar["asgari_devir"].get("USD")):
        if t["giris_ts"] == giris_ts:
            return t
    return None


def isaret_testi(a: list[float], b: list[float]) -> dict:
    """
    Iki kumenin medyanlari farkli mi — DAGILIM VARSAYIMI YOK.

    `t` testi degil: getiri dagilimi KUYRUK BASKIN (belge §2.8, karin
    %55,9'u en iyi %5'ten) ve normallik varsayimi burada YANLIS olur.
    Isaret testi yalnizca "hangi taraf daha sik kazandi" diyor.
    """
    if not a or not b:
        return {"gecerli": False, "sebep": "kume bos"}
    from math import comb
    ma, mb = statistics.median(a), statistics.median(b)
    # Iki bagimsiz kume: birlesik medyana gore isaret sayimi.
    ortak = statistics.median(a + b)
    ka = sum(1 for x in a if x > ortak)
    kb = sum(1 for x in b if x > ortak)
    n = len(a) + len(b)
    k = ka + kb
    # Iki tarafli binom p-degeri (p=0.5) — kaba ama dogru yonde.
    if n == 0:
        return {"gecerli": False, "sebep": "n=0"}
    kuyruk = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return {"gecerli": True, "medyan_a": round(ma * 100, 3),
            "medyan_b": round(mb * 100, 3), "n_a": len(a), "n_b": len(b),
            "ustunde_a": ka, "ustunde_b": kb,
            "p_iki_tarafli": round(min(1.0, 2 * kuyruk), 4)}


def kos(gunler: list[str], llm_ac: bool = True) -> dict:
    s = load_settings()
    db = Database(s.db_path)
    ayar = s.strateji_ayari(db)
    evren = db.endeks_uyeleri(ayar["endeksler"])
    seri_onbellek: dict[int, list] = {}

    ciftler, gun_ozet = [], []
    for gun in gunler:
        t = ST.tara(db, s, evren, bitis=gun)
        g = t["gorusler"]
        if not g:
            gun_ozet.append({"gun": gun, "kirilim": 0})
            continue

        yorum = {}
        if llm_ac:
            r = asyncio.run(SL.yorumla(s, g, int(ayar["ufuk_gun"])))
            if r["hata"]:
                gun_ozet.append({"gun": gun, "kirilim": len(g),
                                 "llm_hata": r["hata"]})
                continue
            yorum = {x["sembol"]: x for x in r["gorusler"]}

        iid = {e["symbol"]: e["id"] for e in evren}
        for x in g:
            sem = x["sembol"]
            if sem not in seri_onbellek:
                seri_onbellek[sem] = [dict(z) for z in
                                      db.fiyat_serisi(iid[sem], 100000)]
            islem = sonuc_bul(seri_onbellek[sem], gun, ayar)
            if not islem:
                # Pencere sonunda KAPANMAMIS islem: sonucu YOK, ve
                # uydurma bir sonuc yazmaktansa CIFT DUSER.
                continue
            y = yorum.get(sem)
            ciftler.append({
                "gun": gun, "sembol": sem,
                "getiri": islem["getiri"], "gun_tutma": islem["gun"],
                "sebep": islem["sebep"],
                "llm": (y["tur"] if y else None),
                "llm_guven": (y["guven"] if y else None),
            })
        gun_ozet.append({"gun": gun, "kirilim": len(g),
                         "yorum": len(yorum)})
    return {"ciftler": ciftler, "gunler": gun_ozet, "ayar": ayar,
            "seri": seri_onbellek}


def rapor(cikti: dict, tohum: int) -> str:
    c = cikti["ciftler"]
    L = [f"KARAR GUNU: {len(cikti['gunler'])} · CIFT: {len(c)}"]
    if not c:
        return "\n".join(L + ["cift yok"])

    hepsi = [x["getiri"] for x in c]
    L.append(f"KURAL (tumu)     n={len(hepsi)} "
             f"ort %{statistics.mean(hepsi)*100:.3f} "
             f"medyan %{statistics.median(hepsi)*100:.3f} "
             f"isabet %{100*sum(1 for x in hepsi if x>0)/len(hepsi):.0f}")

    al = [x["getiri"] for x in c if x["llm"] == "alim"]
    bekle = [x["getiri"] for x in c if x["llm"] == "bekle"]
    if al and bekle:
        L.append(f"LLM 'al'         n={len(al)} "
                 f"ort %{statistics.mean(al)*100:.3f} "
                 f"medyan %{statistics.median(al)*100:.3f} "
                 f"isabet %{100*sum(1 for x in al if x>0)/len(al):.0f}")
        L.append(f"LLM 'bekle'      n={len(bekle)} "
                 f"ort %{statistics.mean(bekle)*100:.3f} "
                 f"medyan %{statistics.median(bekle)*100:.3f} "
                 f"isabet %{100*sum(1 for x in bekle if x>0)/len(bekle):.0f}")
        L.append("ISARET TESTI     " + json.dumps(isaret_testi(al, bekle),
                                                  ensure_ascii=False))
    else:
        L.append("LLM kolu YOK ya da tek tarafli — eslestirilmis kiyas yapilamadi")

    # RASTGELE KONTROL: ayni sembollerde, ayni sayida, ayni surede.
    tut = statistics.mean(x["gun_tutma"] for x in c)
    sayac: dict[str, int] = {}
    for x in c:
        sayac[x["sembol"]] = sayac.get(x["sembol"], 0) + 1
    toplam, agirlik = [], 0
    for sem, adet in sayac.items():
        k = rastgele_kontrol(cikti["seri"][sem], adet, tut, tohum=tohum)
        if k.get("tur"):
            toplam.append((adet, k["ortalama_%"], k.get("isabet_%") or 0))
            agirlik += adet
    if agirlik:
        ro = sum(a * o for a, o, _ in toplam) / agirlik
        ri = sum(a * i for a, _, i in toplam) / agirlik
        L.append(f"RASTGELE KONTROL sembol={len(toplam)} "
                 f"ort %{ro:.3f} isabet %{ri:.0f} (tutma {tut:.0f} bar)")
        L.append(f"KURALA KALAN     %{statistics.mean(hepsi)*100 - ro:.3f}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="§8 dis sinav")
    ap.add_argument("--baslangic", default="2026-06-01")
    ap.add_argument("--bitis", default="2026-08-27")
    ap.add_argument("--gun", type=int, default=15)
    ap.add_argument("--tohum", type=int, default=None,
                    help="varsayilan: ibkr.strateji.secim_tohumu")
    ap.add_argument("--llm-yok", action="store_true",
                    help="yalnizca kural kolu — LLM cagrisi YAPILMAZ")
    ap.add_argument("--dogrulama", action="store_true",
                    help="HAZIRAN ONCESI kos; sinav penceresine DOKUNMAZ")
    a = ap.parse_args()

    if a.dogrulama:
        a.baslangic, a.bitis = "2026-03-01", "2026-05-29"
        print("DOGRULAMA KIPI — sinav penceresine DOKUNULMUYOR\n")

    s = load_settings()
    db = Database(s.db_path)
    ayar = s.strateji_ayari(db)
    tohum = a.tohum if a.tohum is not None else int(ayar["secim_tohumu"])
    evren = db.endeks_uyeleri(ayar["endeksler"])

    gunler = karar_gunleri(db, evren, a.baslangic, a.bitis, a.gun, tohum)
    print(f"pencere {a.baslangic} -> {a.bitis} · {len(gunler)} karar gunu "
          f"(tohum {tohum})")
    print("gunler:", ", ".join(gunler), "\n")

    cikti = kos(gunler, llm_ac=not a.llm_yok)
    print(rapor(cikti, tohum))
    print("\nGUN BAZLI:", json.dumps(cikti["gunler"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
