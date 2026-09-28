"""
BULUT YEDEGI (IBKR MCP Faz 6) — CPGW giris istediginde OKUMA surecleri.

Ali (28 Eyl): "IBKR login dustugunde her bir prosesin yerine bulut
calisiyor olmali." Bu modul, yerel ag gecidinin (CPGW, localhost:5001)
her OKUMA ucu icin bulut karsiligini TEK yerde toplar. Donuslerin sekli
CPGW sarmalayicilarininkiyle ayni tutulur; cagiran, kanali (`kanal`)
SOYLER.

KARAR KODDA: once CPGW denenir; YALNIZCA `yerel_dustu(e)` (401/403 ya da
ag gecidine ulasilamiyor) ise bulut. Baska bir hata (bozuk yanit, hiz
siniri) yedege gecirmez — o hata SAKLANMAZ, oldugu gibi soylenir.

KAPSAM DISI — BILEREK
---------------------
* EMIR (gonder, iptal, degistir, /stop, whatif): bulutta canli emir araci
  YOK. `create_order_instruction` canli emir degil, IBKR uygulamasinda
  onaylanacak bir taslak; ayri karar.
* Mutabakatta "emir dustu/iptal mi" KARARI: acik emir listesinin bulut
  bicimi DOLU bir ornekle olculmedi (28 Eyl: `{"orders": []}`). Bulut
  yolu yalnizca DOLUM KANITINI (islem gecmisi) okur; yokluk kanit sayilmaz.
* Kimlik (conid) cozumu: 518 sembollu evren sembol basina ~13 sn olurdu,
  ve yanlis conid YANLIS HISSE demek. Kimlik saatlerce bekleyebilir.
"""
from __future__ import annotations

import logging

from .istemci import UlasilamadiHatasi, YetkiHatasi

log = logging.getLogger(__name__)

# `top-status` -> CPGW `Kotasyon.kip` sozlugu (ayni kelimeler: arac
# aciklamalari ve prompt bunlara bagli).
KIP = {"REALTIME": "gercek_zamanli", "DELAYED": "gecikmeli",
       "FROZEN": "donmus", "FROZEN_DELAYED": "donmus"}


def yerel_dustu(e: BaseException) -> bool:
    """CPGW'nin OTURUMU/ERISIMI mi dustu? Yalnizca o zaman bulut."""
    return isinstance(e, (YetkiHatasi, UlasilamadiHatasi))


def _sayi(x):
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def kotasyon_ayristir(snap: dict) -> dict:
    """
    SAF. `get_price_snapshot` (last, bid_ask, top_status, prior_close) ->
    `ibkr_fiyat` ile ayni alanlar. OLCULEN (25 Eyl, QCOM): `top-status`
    REALTIME. `last.price` bos olabilir (arac belgesi: ayni fiyattan
    islemde yalnizca ts degisir) -> None birakilir, UYDURULMAZ.
    Para birimi yanitta YOK -> None ve soylenir.
    """
    snap = snap or {}
    son = _sayi((snap.get("last") or {}).get("price"))
    ba = snap.get("bid-ask") or {}
    alis, satis = _sayi(ba.get("bid")), _sayi(ba.get("ask"))
    orta = (alis + satis) / 2 if alis and satis and satis >= alis else None
    durum = str((snap.get("top-status") or {}).get("status") or "").upper()
    if durum == "REJECT":
        raise ValueError("IBKR bulut kotasyonu reddetti (REJECT)")
    kip = KIP.get(durum, "bilinmiyor")
    if son is None and orta is None:
        raise ValueError(f"bulut kotasyonunda fiyat yok (durum {durum or '?'})")
    return {"son": son, "alis": alis, "satis": satis, "orta": orta,
            "onceki_kapanis": _sayi((snap.get("prior-close") or {}).get("price")
                                    if isinstance(snap.get("prior-close"), dict)
                                    else snap.get("prior-close")),
            "kip": kip, "gercek_zamanli": kip == "gercek_zamanli",
            "veri_durumu": durum or None, "para_birimi": None,
            "kanal": "bulut"}


KOTASYON_ALANLARI = ["last", "bid_ask", "top_status", "prior_close"]


async def kotasyon_async(conid: int, _cagir_async=None) -> dict:
    from . import mcp_kanal
    c = _cagir_async or mcp_kanal.cagir_async
    r = await c("get_price_snapshot", {"contract_id": int(conid),
                                       "market_data_names": KOTASYON_ALANLARI})
    return kotasyon_ayristir(r.veri)


def hesap_ozeti_ayristir(ozet: dict, bakiye: dict) -> dict:
    """
    SAF. Bulut `get_account_summary` + `get_account_balances` -> CPGW
    `Portfoy.ozet()` / `nakit()` ile ayni anahtarlar. OLCULEN bicim
    (25 Eyl fiksturu): ozet duz sayilar + `currency`; bakiye satirlari
    para birimi basina, `BASE` toplam satiri (atlanir).
    """
    ozet = ozet or {}
    pb = ozet.get("currency")
    esle = {"netliquidation": "net_liquidation", "totalcashvalue": "total_cash_value",
            "availablefunds": "available_funds", "buyingpower": "buying_power",
            "excessliquidity": "excess_liquidity",
            "equitywithloanvalue": "equity_with_loan_value"}
    out = {k: (_sayi(ozet.get(b)), pb) for k, b in esle.items() if b in ozet}
    nakit = {str(r.get("currency")).upper(): _sayi(r.get("cash_balance"))
             for r in (bakiye or {}).get("balances") or []
             if r.get("currency") and str(r.get("currency")).upper() != "BASE"}
    return {"ozet": out, "nakit": nakit, "para_birimi": pb}


async def hesap_ozeti_async(_cagir_async=None) -> dict:
    from . import mcp_kanal
    c = _cagir_async or mcp_kanal.cagir_async
    o = (await c("get_account_summary")).veri
    b = (await c("get_account_balances")).veri
    return hesap_ozeti_ayristir(o, b)


def toplam_netlik(_cagir=None) -> tuple[float | None, str | None]:
    """Senkron (nabiz). CPGW `Portfoy.toplam_netlik` karsiligi."""
    from . import mcp_kanal
    o = (_cagir or mcp_kanal.cagir)("get_account_summary").veri or {}
    return _sayi(o.get("net_liquidation")), o.get("currency")


def acik_emirler_ayristir(v) -> dict:
    """
    SAF. `get_account_orders` -> liste. DOLU bir yanit OLCULMEDI (28 Eyl
    yalnizca `{"orders": []}`); anahtar adlari tahmin EDILMEZ: bilinen
    adaylar okunur, satirin HAMI da tasinir. Bos liste "acik emir yok"
    KANITI degil (`bos-yanit-yokluk-kaniti-degil`) — cagiran boyle soyler.
    """
    satirlar = (v or {}).get("orders") if isinstance(v, dict) else None
    if not isinstance(satirlar, list):
        raise ValueError(f"get_account_orders beklenmeyen bicim: {str(v)[:160]}")

    def al(r, *adlar):
        return next((r[a] for a in adlar if r.get(a) not in (None, "")), None)
    return {"emirler": [{
        "emir_no": al(r, "order_id", "orderId", "id"),
        "sembol": al(r, "symbol", "ticker"),
        "yon": al(r, "side"), "tur": al(r, "order_type", "orderType"),
        "adet": al(r, "quantity", "size", "totalSize"),
        "fiyat": al(r, "price", "limit_price"), "durum": al(r, "status"),
        "ham": r} for r in satirlar if isinstance(r, dict)],
        "kanal": "bulut",
        "not": ("Bulut acik emir biciminin DOLU ornegi henuz olculmedi; "
                "'ham' alani IBKR'nin yanitidir. Bos liste 'emir yok' "
                "KANITI degildir.")}


async def acik_emirler_async(_cagir_async=None) -> dict:
    from . import mcp_kanal
    c = _cagir_async or mcp_kanal.cagir_async
    return acik_emirler_ayristir((await c("get_account_orders")).veri)


def islemler(_cagir=None, donem: str = "DAYS_30") -> list[dict] | None:
    """
    Senkron. Bulut islem gecmisi — CPGW `/iserver/account/trades` ile
    AYNI anahtarlar (olculdu 28 Eyl: order_id, price, commission,
    net_amount, trade_time, size, side, symbol). Okunamazsa None
    ("bilmiyorum"), bos liste DEGIL.
    """
    from . import mcp_kanal
    try:
        v = (_cagir or mcp_kanal.cagir)("get_account_trades", {"period": donem}).veri
    except Exception as e:                                # noqa: BLE001
        log.warning("[bulut] islem gecmisi okunamadi: %s", e)
        return None
    t = v.get("trades") if isinstance(v, dict) else None
    return [r for r in t if isinstance(r, dict)] if isinstance(t, list) else None
