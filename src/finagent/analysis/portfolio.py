"""Portfoy metrikleri — anlik goruntu + BUGUNKU fiyatla canli degerleme."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _turetilmis_pnl(market_value: float | None, pnl_pct: float | None) -> float | None:
    """
    Mutlak K/Z'yi deger ve yuzdeden geri hesaplar.

    NEDEN GEREKLI: ekran goruntusu okuyucusu `pnl_abs` alanini cogu zaman
    getiremiyor (BUX ekraninda mutlak K/Z yazmiyor, yuzde yaziyor). Onceden
    `pnl or 0.0` yaziliyordu ve rapor "Toplam K/Z 0.00" diye BEYAN ediyordu —
    bilinmeyen bir degeri sifir diye sunmak, en kotu turden sessiz yalan.

    Aritmetik: deger = maliyet x (1 + pct/100)  ->  maliyet = deger / (1+pct/100)
               K/Z   = deger - maliyet = deger x pct / (100 + pct)
    """
    if market_value is None or pnl_pct is None:
        return None
    payda = 100.0 + pnl_pct
    if abs(payda) < 1e-9:                     # -%100: maliyetin tamami silinmis
        return None
    return market_value * pnl_pct / payda


def _canli_fiyat(db, instrument_id: int) -> dict | None:
    """
    Enstrumanin son kapanisi + bir onceki kapanis (gunluk degisim icin).

    `fiyat_serisi()` uzerinden okunur: `prices` ayni enstruman icin farkli
    KAYNAK ve farkli PARA BIRIMINDE seri tutabiliyor ve dogrudan sorgu
    ikisini karistirir.
    """
    seri = db.fiyat_serisi(instrument_id, 2)
    if not seri:
        return None
    son = seri[-1]
    onceki = seri[-2] if len(seri) > 1 else None
    kapanis = son["close"]
    if kapanis is None:
        return None
    degisim = None
    if onceki is not None and onceki["close"]:
        degisim = (kapanis / onceki["close"] - 1) * 100
    return {
        "kapanis": float(kapanis),
        "tarih": son["ts"],
        "para_birimi": son["currency"],
        "kaynak": son["source"],
        "gun_degisim_%": None if degisim is None else round(degisim, 2),
    }


def portfolio_summary(db, accounts: list[str], sahip: str) -> dict:
    result: dict = {"hesaplar": {}, "toplam": {}}
    grand_value = 0.0
    grand_value_bugun = 0.0
    grand_pnl = 0.0
    pnl_var = False
    pnl_eksik = 0
    bugun_eksik: list[str] = []

    for acct in accounts:
        rows = db.latest_positions(acct, sahip)
        if not rows:
            result["hesaplar"][acct] = {"durum": "pozisyon verisi yok"}
            continue

        positions = []
        total_val = 0.0
        total_val_bugun = 0.0
        total_pnl = 0.0
        hesap_pnl_var = False
        for r in rows:
            mv = r["market_value"] or 0.0
            total_val += mv

            # --- K/Z: NULL ile 0 AYRI SEYLER -----------------------------
            pnl = r["pnl_abs"]
            pnl_kaynak = "ekran" if pnl is not None else None
            if pnl is None:
                pnl = _turetilmis_pnl(r["market_value"], r["pnl_pct"])
                pnl_kaynak = "turetilmis" if pnl is not None else None
            if pnl is not None:
                total_pnl += pnl
                hesap_pnl_var = True
            else:
                pnl_eksik += 1

            # --- BUGUNKU fiyatla canli deger -----------------------------
            # Anlik goruntu gunlerce eski olabiliyor (ekran goruntusu ne
            # zaman gonderildiyse o). Adet elimizde, bugunku kapanis da —
            # "veri eski" demek yerine YENIDEN DEGERLEMEK dogrusu.
            canli = _canli_fiyat(db, r["instrument_id"])
            deger_bugun = None
            fiyat_notu = None
            if r["asset_type"] == "cash" or r["symbol"] == "CASH":
                deger_bugun = round(mv, 2)    # nakit hareket etmez
                total_val_bugun += deger_bugun
            elif canli and r["quantity"]:
                birim = round(r["quantity"] * canli["kapanis"], 2)
                seri_ccy = (canli["para_birimi"] or "").upper()
                poz_ccy = (r["currency"] or "").upper()
                if seri_ccy and poz_ccy and seri_ccy != poz_ccy:
                    # PARA BIRIMI ESLESMIYORSA HAM CARPMA YAPILMAZ; kurla
                    # CEVRILIR ve KULLANILAN KUR YAZILIR. 17 pozisyonun
                    # 14'unde yanlis fiyat tam bu adim atlandigi icin
                    # olusmustu. Cevirmemek de secenek degil: portfoyun
                    # yarisi Yahoo'da USD kote ve cevrilmezse "canli
                    # deger" pozisyonlarin yalnizca yarisini kapsar.
                    kur = db.fx_kuru(seri_ccy, poz_ccy)
                    if kur:
                        deger_bugun = round(birim * kur["rate"], 2)
                        total_val_bugun += deger_bugun
                        fiyat_notu = (f"{seri_ccy}->{poz_ccy} @ {kur['rate']:.4f} "
                                      f"({kur['ts']}, {kur['kaynak']})")
                    else:
                        fiyat_notu = (f"seri {seri_ccy}, pozisyon {poz_ccy} — "
                                      f"kur bulunamadi, cevrilmedi")
                else:
                    deger_bugun = birim
                    total_val_bugun += deger_bugun
            if deger_bugun is None:
                bugun_eksik.append(r["symbol"])
                total_val_bugun += mv        # elde ne varsa o; toplam bozulmasin

            positions.append({
                "sembol": r["symbol"],
                "adet": r["quantity"],
                "ort_maliyet": r["avg_cost"],
                "son_fiyat": r["last_price"],
                "deger": round(mv, 2),
                "deger_bugun": deger_bugun,
                "kar_zarar": None if pnl is None else round(pnl, 2),
                "kar_zarar_kaynagi": pnl_kaynak,
                "kar_zarar_%": round(r["pnl_pct"], 2) if r["pnl_pct"] is not None else None,
                "para_birimi": r["currency"],
                "son_kapanis": canli["kapanis"] if canli else None,
                "son_kapanis_tarih": canli["tarih"] if canli else None,
                "gun_degisim_%": canli["gun_degisim_%"] if canli else None,
                "fiyat_notu": fiyat_notu,
            })

        for p in positions:
            p["agirlik_%"] = round(p["deger"] / total_val * 100, 2) if total_val else None

        top = max(positions, key=lambda p: p["deger"], default=None)
        result["hesaplar"][acct] = {
            "snapshot": rows[0]["snapshot_ts"],
            "pozisyon_sayisi": len(positions),
            "toplam_deger": round(total_val, 2),
            "toplam_deger_bugunku_fiyatla": round(total_val_bugun, 2),
            "toplam_kar_zarar": round(total_pnl, 2) if hesap_pnl_var else None,
            "toplam_kar_zarar_%": round(total_pnl / (total_val - total_pnl) * 100, 2)
                                   if (hesap_pnl_var and (total_val - total_pnl)) else None,
            "en_buyuk_pozisyon": top["sembol"] if top else None,
            "yogunlasma_%": top["agirlik_%"] if top else None,
            "pozisyonlar": positions,
        }
        grand_value += total_val
        grand_value_bugun += total_val_bugun
        grand_pnl += total_pnl
        pnl_var = pnl_var or hesap_pnl_var

    result["toplam"] = {
        "deger": round(grand_value, 2),
        "deger_bugunku_fiyatla": round(grand_value_bugun, 2),
        # BILINMIYORSA None. `0.00` yazmak bilinmeyeni sifir diye sunmaktir.
        "kar_zarar": round(grand_pnl, 2) if pnl_var else None,
        "kar_zarar_durumu": (
            "hicbir pozisyonda mutlak K/Z yok (ekran goruntusu yalnizca yuzde veriyor)"
            if not pnl_var else
            (f"{pnl_eksik} pozisyonda mutlak K/Z hesaplanamadi"
             if pnl_eksik else "tam")),
        "not": ("HESAP ICINDE fiyatlar pozisyonun para birimine cevrildi (kullanilan "
                "kur her satirin `fiyat_notu` alaninda). HESAPLAR ARASI toplam "
                "CEVRILMEDI: BUX EUR, Binance USDT ve ikisi burada toplanmis "
                "durumda. Tek para biriminde bir toplam isteyen taraf `fx` "
                "araciyla cevirmeli."),
    }
    if bugun_eksik:
        result["toplam"]["bugunku_fiyat_bulunamayan"] = sorted(set(bugun_eksik))
    return result
