"""
BILANCO ONCESI FIYATLANAN HAREKET (IBKR MCP Faz 4).

Portfoydeki hissenin bilancosu yaklasinca, opsiyon piyasasinin o bilanco
icin FIYATLADIGI hareket olculur: "ASML 14 Eki, piyasa ±%X fiyatliyor".
Bu bir TAHMIN DEGIL — opsiyon alicilarinin zaten odedigi beklenti; YON
icermez. E1 filtresine GIRMEZ (orada kullanilmasi yeni bir hipotezdir ve
once kendi on kaydiyla sinanmali).

HESAP (ATM straddle)
--------------------
  1. vade: bilanconun EN GEC tepki gunune esit ya da sonraki ILK vade,
     `trading_class` hissenin kendi sembolune esit olan satirlardan
  2. fiyat: hissenin son fiyati (yoksa alis-satis ortasi)
  3. ATM: fiyata en yakin strike (esitlikte dusuk olan)
  4. hareket = (call orta + put orta) / hisse fiyati

Vade bilancodan uzaksa bilanco disi oynaklik da icerir; vadeye kac gun
kaldigi SAKLANIR ve soylenir.

OLCULEN (25 Eyl, Faz 0): opsiyon fiyatlari FROZEN_DELAYED (OPRA aboneligi
yok) ama alis-satis VAR; IV alanlari GECERSIZ -> hesap IV kullanmaz.
`trading_class` HER satirda dolu (plandaki "bos olani sec" kurali yanlisti).

ONCE YAZ, SONRA OLC: bilanco gectikten sonra gerceklesen tepki gunu
hareketi ayni satira yazilir. Onceden kaydedilmeyen beklenti sonradan
yalnizca tutan ornekleriyle hatirlanir (defterin disiplini).
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)

# Strike araligi fiyatin ±%7'si, TAM SAYIYA yuvarlanarak: kapi argumani
# BIREBIR karsilastiriyor; ondalikli bir sinir (176.76) model tarafindan
# yuvarlanirsa kapi reddeder. Tam sayi her iki tarafta da ayni yazilir.
STRIKE_PAYI = 0.07


class BeklentiHesaplanamadi(Exception):
    """Veri eksik ya da belirsiz — sebep mesajda; uydurma sayi uretilmez."""


def en_gec_tepki(tarihler: list[str], zaman: str | None) -> str:
    """
    SAF. Bilanconun fiyata yansiyabilecegi EN GEC islem gunu.

    Vade bu gunu KAPSAMALI: erken bir vade secilirse straddle bilancoyu hic
    icermez ve "piyasa bilanco icin %X fiyatliyor" cumlesi yanlis olur.
    Kaynaklar farkli tarih veriyorsa en gec tarih; saat 'once' degilse
    (sonra, seans, bilinmiyor) tepki ertesi islem gunu olabilir.
    Tanim `olay_takvimi.tepki_gunleri`nden — sinavla ayni.
    """
    from ..analysis import olay_takvimi as ot
    son = max(tarihler)
    gunler = [son] + ot.ileri_islem_gunleri(son, 3)
    return max(ot.tepki_gunleri(son, zaman, gunler) or [son])


def opsiyon_fiyati(snap: dict) -> tuple[float | None, str | None]:
    """
    SAF. (fiyat, kaynak). Once alis-satis ORTASI; yoksa YALNIZCA
    `is_close` isaretli son islem (seansin kapanis islemi).

    OLCULDU (25 Eyl 15:16, ABD acilisindan once): ASML ATM opsiyonlarinda
    `bid-ask` BOS, `last` dolu ve `is_close: true` (arac belgesi: "kotasyon
    yoksa bos nesne donebilir"). Yalnizca ortaya bakmak o saatte hic olcum
    uretmiyordu. Kapanis islemi gercek bir piyasa fiyati; `is_close` OLMAYAN
    son islem ise gun icinde bayatlamis olabilir ve KABUL EDILMEZ. Kaynak
    her satirda saklanir ve mesajda soylenir.
    """
    o = _orta(snap)
    if o is not None:
        return o, "orta"
    son = (snap or {}).get("last") or {}
    if son.get("is_close") and son.get("price") and float(son["price"]) > 0:
        return float(son["price"]), "kapanis_islemi"
    return None, None


def _orta(snap: dict) -> float | None:
    ba = (snap or {}).get("bid-ask") or {}
    b, a = ba.get("bid"), ba.get("ask")
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return None
    return (float(b) + float(a)) / 2.0


def hisse_fiyati(snap: dict) -> float | None:
    """SAF. Son islem fiyati; yoksa alis-satis ortasi."""
    son = ((snap or {}).get("last") or {}).get("price")
    if son:
        return float(son)
    return _orta(snap)


def vade_sec(params: dict, sembol: str, en_erken: str) -> dict:
    """
    SAF. `en_erken` ('YYYY-MM-DD') tarihine esit ya da sonraki ILK vade.

    `trading_class` hissenin KENDI sembolune esit olmali ('2QCOM' gibi
    siniflar AYRI kontratlar — arac belgesi). Ayni tarihte ayni sinifta
    birden fazla satir varsa BELIRSIZ -> hata (tahminle secilmez).
    """
    hedef = en_erken.replace("-", "")
    kok = (sembol or "").upper()
    adaylar = [e for e in (params or {}).get("expirations") or []
               if str(e.get("trading_class") or "").upper() == kok
               and str(e.get("date") or "") >= hedef]
    if not adaylar:
        raise BeklentiHesaplanamadi(
            f"{sembol}: {en_erken} sonrasi '{kok}' sinifinda vade yok")
    ilk = min(e["date"] for e in adaylar)
    ayni = [e for e in adaylar if e["date"] == ilk]
    if len(ayni) > 1:
        raise BeklentiHesaplanamadi(
            f"{sembol}: {ilk} vadesinde {len(ayni)} satir — belirsiz, secilmedi")
    return ayni[0]


def strike_araligi(fiyat: float) -> tuple[int, int]:
    return (int(math.floor(fiyat * (1 - STRIKE_PAYI))),
            int(math.ceil(fiyat * (1 + STRIKE_PAYI))))


def atm_sec(zincir: dict, fiyat: float) -> dict:
    """SAF. Fiyata en yakin strike satiri (esitlikte DUSUK strike)."""
    satirlar = [r for r in (zincir or {}).get("contracts") or []
                if r.get("strike") not in (None, "")]
    if not satirlar:
        raise BeklentiHesaplanamadi("opsiyon zinciri bos (aralikta strike yok)")
    return min(satirlar, key=lambda r: (abs(float(r["strike"]) - fiyat),
                                        float(r["strike"])))


def durum_birlestir(*snaps) -> str:
    """En KOTU veri durumu: REALTIME < DELAYED < FROZEN < ... (etiket icin)."""
    sira = ["REALTIME", "DELAYED", "FROZEN", "FROZEN_DELAYED", "REJECT"]
    durumlar = [((s or {}).get("top-status") or {}).get("status") or "BILINMIYOR"
                for s in snaps]
    return max(durumlar, key=lambda d: sira.index(d) if d in sira else len(sira))


def hesapla(conid: int, sembol: str, en_erken: str, _cagir) -> dict:
    """
    Bir hisse icin fiyatlanan hareket. `_cagir` = `mcp_kanal.cagir` imzasi.
    Her cagrinin argumani BURADA kurulur (kapi birebir karsilastirir).
    Eksik/belirsiz veride `BeklentiHesaplanamadi` — sifir hareket UYDURULMAZ.
    """
    params = _cagir("get_option_parameters", {"underlying_contract_id": int(conid)}).veri
    vade = vade_sec(params, sembol, en_erken)
    hs = _cagir("get_price_snapshot", {"contract_id": int(conid),
                                       "market_data_names": ["last", "bid_ask", "top_status"]}).veri
    fiyat = hisse_fiyati(hs)
    if not fiyat:
        raise BeklentiHesaplanamadi(f"{sembol}: hisse fiyati yok")
    alt, ust = strike_araligi(fiyat)
    zincir = _cagir("get_option_data", {"expiration_id": vade["id"],
                                        "min_strike": alt, "max_strike": ust}).veri
    atm = atm_sec(zincir, fiyat)
    borsa = zincir.get("exchange")
    arg = lambda cid: {"contract_id": int(cid), "exchange": borsa,       # noqa: E731
                       "market_data_names": ["bid_ask", "last", "top_status"]}
    c = _cagir("get_price_snapshot", arg(atm["call_contract_id"])).veri
    p = _cagir("get_price_snapshot", arg(atm["put_contract_id"])).veri
    (co, ck), (po, pk) = opsiyon_fiyati(c), opsiyon_fiyati(p)
    if co is None or po is None:
        raise BeklentiHesaplanamadi(
            f"{sembol}: ATM opsiyonun ne alis-satisi ne kapanis islemi var "
            f"(call {co}, put {po})")
    return {"conid": int(conid), "fiyat": fiyat, "vade": vade["date"],
            "strike": float(atm["strike"]), "call_orta": co, "put_orta": po,
            "hareket_pct": round((co + po) / fiyat * 100.0, 3),
            "veri_durumu": durum_birlestir(c, p),
            # IKISI DE ortadan degilse en zayif kaynak yazilir.
            "fiyat_kaynagi": "orta" if ck == pk == "orta" else "kapanis_islemi"}


def gerceklesen(kapanislar: list[tuple[str, float]], tepki_gunu: str) -> float | None:
    """
    SAF. Tepki gunu kapanisinin bir onceki islem gunu kapanisina gore
    MUTLAK degisimi (%). Tepki gunu bari yoksa None ("henuz yok", sifir
    degil). Straddle da kapanistan kapanisa fiyatlanir; ayni olcu.
    """
    onceki = None
    for t, c in kapanislar:
        if t == tepki_gunu:
            return round(abs(c / onceki - 1.0) * 100.0, 3) if onceki else None
        if t > tepki_gunu:
            return None
        onceki = c
    return None
