"""
DONCHIAN TREND TAKIBI — kitabin KENDI cercevesiyle sinama.

NEDEN AYRI BIR MODUL
--------------------
Tarayicidaki `sma50_kirilimi` kaba bir trend sinyali ve 2026-08-20
backtest'inde sifirdan ayirt edilemedi. Ama bu "trend takibi
calismiyor" DEMEK DEGIL — olcum cercevesi kitabinkiyle uyusmuyordu:

  * UFUK YANLIS. Sabit 1/5/20 gunluk pencerede olculdu; trend takibi
    AYLARLA calisir ve 20 gun onun icin gurultu.
  * CIKIS KURALI YOKTU. Trend takibinin yarisi cikistir (10 gunluk dip,
    2N stop). Stop'suz bir trend sistemi trend sistemi degildir.
  * ORTALAMA GETIRI TEK BASINA YANILTIR. Kitabin acik iddiasi: islemlerin
    COGU zarar eder, az sayida buyuk kazanc her seyi tasir. Yani asil
    olculmesi gereken sey KUYRUK.

KURALLAR (Kaplumbaga Sistem 1)
------------------------------
  giris  : kapanis, ONCEKI 20 gunun en yuksegini asarsa
  cikis  : kapanis, ONCEKI 10 gunun en dusugunun altina inerse
  stop   : giristen 2N asagi (N = 20 gunluk ATR)
  yon    : YALNIZCA UZUN. BIST'te acik satis pratikte kisitli; kitabin
           kazancinin yarisini olusturan dusen trend tarafi BIZDE YOK
           ve bunu sonucta acikca soylemek zorundayiz.

DURUSTLUK KAYITLARI
-------------------
1. LOOK-AHEAD YOK. t gunundeki karar yalnizca t'ye kadarki barlardan;
   Donchian penceresi t'nin KENDISINI dislar.
2. SERMAYE ISLEMI FILTRESI. BIST gunluk limiti asan bar fiyat hareketi
   degildir (bkz. `screener.BORSA_LIMITI`); boyle bir bar iceren islem
   DUSURULUR — ADEL'in 11:1 bolunmesi tek basina ortalamayi goturuyordu.
3. HAYATTA KALMA YANLILIGI VAR VE GIDERILEMEDI. Evren BUGUN kote olan
   kagitlardan kuruluyor; borsadan DUSMUS sirketler veride yok. Bu,
   sonucu YUKARI cekiyor ve buyuklugu olculemiyor. Sonuc bu payla
   okunmali.
4. ISLEM MALIYETI PARAMETRE, varsayilan gidis-donus %0,40.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

GIRIS_PENCERE = 20
CIKIS_PENCERE = 10
ATR_PENCERE = 20
STOP_N = 2.0

# Gunluk hareket bu esige ULASTIYSA o gun LIMITTE kapanmis sayilir ve o
# kapanistan GIRIS YAPILAMAZ. BIST limiti ±%10; %9,5 pay birakiyor.
LIMIT_YAKIN = 0.095


def _atr(seri, i, pencere=ATR_PENCERE):
    """Wilder olmayan basit ATR — `i` DAHIL degil, yalnizca oncesi."""
    if i < pencere + 1:
        return None
    tr = []
    for j in range(i - pencere, i):
        y, d, onceki = seri[j]["high"], seri[j]["low"], seri[j - 1]["close"]
        if y is None or d is None or onceki is None:
            return None
        tr.append(max(y - d, abs(y - onceki), abs(d - onceki)))
    return sum(tr) / len(tr) if tr else None


def islemler(seri, borsa_limiti: float | None = 0.12) -> list[dict]:
    """
    Bir enstrumanda Donchian 20/10 + 2N kurallarinin urettigi ISLEMLER.

    Her islem: giris/cikis tarihi, getiri, cikis SEBEBI, tutulan gun.
    """
    kapanis = [r["close"] for r in seri]
    out: list[dict] = []
    i, n = ATR_PENCERE + 1, len(seri)
    pozisyon = None

    while i < n:
        bar = seri[i]
        if bar["close"] is None:
            i += 1
            continue

        if pozisyon is None:
            onceki = [k for k in kapanis[i - GIRIS_PENCERE:i] if k]
            if len(onceki) < GIRIS_PENCERE:
                i += 1
                continue
            if bar["close"] > max(onceki):
                N = _atr(seri, i)
                if N and N > 0:
                    # GIRIS GUNU TAVANDAYSA O KAPANISTAN ALINAMAZ.
                    #
                    # BIST'te gunluk limit ±%10 ve limitte islem
                    # KILITLENIR: tavanda satis tarafi bostur. Kirilim
                    # sinyali tam da tavan gununde cikma egilimindedir
                    # — olculdu 2026-08-21: OZATD'nin +%2492'lik
                    # "islemi" 35 tavan gunu iceriyor ve girisi de o
                    # gunlerden birinde. Sayilirsa backtest, YAPILAMAYAN
                    # bir islemi kar diye raporlar.
                    onceki_kapanis = kapanis[i - 1]
                    tavanda = (onceki_kapanis and
                               (bar["close"] / onceki_kapanis - 1) >= LIMIT_YAKIN)
                    pozisyon = {"giris_ts": bar["ts"], "giris": bar["close"],
                                "stop": bar["close"] - STOP_N * N, "N": N,
                                "giris_i": i, "girisde_tavan": bool(tavanda)}
            i += 1
            continue

        # --- pozisyondayiz: once STOP, sonra Donchian cikisi ----------
        sebep = None
        if bar["low"] is not None and bar["low"] <= pozisyon["stop"]:
            cikis, sebep = pozisyon["stop"], "2N stop"
        else:
            onceki = [k for k in kapanis[i - CIKIS_PENCERE:i] if k]
            if len(onceki) >= CIKIS_PENCERE and bar["close"] < min(onceki):
                cikis, sebep = bar["close"], "10 gun dip"
        if sebep is None:
            i += 1
            continue

        # SERMAYE ISLEMI ICEREN ISLEM DUSER: o bar fiyat hareketi degil.
        kirli = False
        if borsa_limiti:
            for j in range(pozisyon["giris_i"] + 1, i + 1):
                a, b = kapanis[j], kapanis[j - 1]
                if a and b and abs(a / b - 1) > borsa_limiti:
                    kirli = True
                    break
        if not kirli:
            out.append({
                "giris_ts": pozisyon["giris_ts"], "cikis_ts": bar["ts"],
                "giris": pozisyon["giris"], "cikis": cikis,
                "getiri": cikis / pozisyon["giris"] - 1,
                "gun": i - pozisyon["giris_i"], "sebep": sebep,
                "N_orani": pozisyon["N"] / pozisyon["giris"],
                "girisde_tavan": pozisyon.get("girisde_tavan", False),
            })
        pozisyon = None
        i += 1
    return out


def _yuzdelik(x, p):
    if not x:
        return None
    s = sorted(x)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[k]


def ozet(hepsi: list[dict], maliyet: float = 0.004,
         yalniz_uygulanabilir: bool = False) -> dict:
    """
    Kitabin ISRAR ETTIGI olcutler — isabet orani TEK BASINA degil.

    `maliyet` gidis-donus, getiriden DUSULUR: %0,40 makul bir BIST
    perakende varsayimi (komisyon + spread).
    """
    if yalniz_uygulanabilir:
        hepsi = [x for x in hepsi if not x.get("girisde_tavan")]
    if not hepsi:
        return {"islem": 0}
    g = [x["getiri"] - maliyet for x in hepsi]
    kazanan = [x for x in g if x > 0]
    kaybeden = [x for x in g if x <= 0]
    ort_kazanc = sum(kazanan) / len(kazanan) if kazanan else 0.0
    ort_kayip = sum(kaybeden) / len(kaybeden) if kaybeden else 0.0

    # KUYRUK: kitabin tezi "az sayida buyuk kazanc her seyi tasir".
    # Olculecek sey: toplam KARIN yuzde kaci en iyi %5 islemden geliyor.
    sirali = sorted(g, reverse=True)
    ust5 = sirali[:max(1, len(sirali) // 20)]
    toplam_kar = sum(x for x in g if x > 0)
    return {
        "islem": len(g),
        "isabet_%": round(len(kazanan) / len(g) * 100, 1),
        "ort_kazanc_%": round(ort_kazanc * 100, 2),
        "ort_kayip_%": round(ort_kayip * 100, 2),
        "kazanc_kayip_orani": (round(ort_kazanc / abs(ort_kayip), 2)
                               if ort_kayip else None),
        "beklenti_%": round(sum(g) / len(g) * 100, 3),
        "medyan_%": round(_yuzdelik(g, 0.5) * 100, 2),
        "en_iyi_%": round(max(g) * 100, 1),
        "en_kotu_%": round(min(g) * 100, 1),
        "ust_%5_kar_payi_%": (round(sum(x for x in ust5 if x > 0)
                                    / toplam_kar * 100, 1)
                              if toplam_kar else None),
        "ort_tutma_gun": round(sum(x["gun"] for x in hepsi) / len(hepsi), 1),
        "stopla_cikis_%": round(
            sum(1 for x in hepsi if x["sebep"] == "2N stop") / len(hepsi) * 100, 1),
    }


def kosu(db, baslangic: str, bitis: str, venue: str = "BIST",
         limit: int | None = None, maliyet: float = 0.004) -> dict:
    from ..pulse.screener import BORSA_LIMITI
    from .backtest import _evren

    borsa_limiti = BORSA_LIMITI.get(venue.upper())
    evren = _evren(db, venue)
    if limit:
        evren = evren[:limit]

    hepsi: list[dict] = []
    kapsam = {"enstruman": 0, "atlanan": 0}
    for e in evren:
        seri = [dict(r) for r in db.fiyat_serisi(e["id"], limit=100000)]
        if len(seri) < 300:
            kapsam["atlanan"] += 1
            continue
        kapsam["enstruman"] += 1
        for t in islemler(seri, borsa_limiti):
            if baslangic <= t["giris_ts"] <= bitis:
                hepsi.append({**t, "sembol": e["symbol"]})

    # KIYAS: ayni donemde AL-TUT. Strateji "kazandi" diyebilmek icin
    # hicbir sey yapmamaktan iyi olmali.
    from .backtest import _endeks_serisi
    endeks = _endeks_serisi(db)
    gunler = sorted(d for d in endeks if baslangic <= d <= bitis)
    al_tut = ((endeks[gunler[-1]] / endeks[gunler[0]] - 1) * 100
              if len(gunler) > 1 else None)

    return {"kapsam": kapsam, "ozet": ozet(hepsi, maliyet),
            "al_tut_endeks_%": round(al_tut, 1) if al_tut else None,
            "pencere": {"baslangic": baslangic, "bitis": bitis},
            "_islemler": hepsi}
