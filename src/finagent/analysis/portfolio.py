"""Portfoy metrikleri — BUX + Midas pozisyonlarindan."""
from __future__ import annotations


def portfolio_summary(db, accounts: list[str]) -> dict:
    result: dict = {"hesaplar": {}, "toplam": {}}
    grand_value = 0.0
    grand_pnl = 0.0

    for acct in accounts:
        rows = db.latest_positions(acct)
        if not rows:
            result["hesaplar"][acct] = {"durum": "pozisyon verisi yok"}
            continue

        positions = []
        total_val = 0.0
        total_pnl = 0.0
        for r in rows:
            mv = r["market_value"] or 0.0
            pnl = r["pnl_abs"] or 0.0
            total_val += mv
            total_pnl += pnl
            positions.append({
                "sembol": r["symbol"],
                "adet": r["quantity"],
                "ort_maliyet": r["avg_cost"],
                "son_fiyat": r["last_price"],
                "deger": round(mv, 2),
                "kar_zarar": round(pnl, 2),
                "kar_zarar_%": round(r["pnl_pct"], 2) if r["pnl_pct"] is not None else None,
                "para_birimi": r["currency"],
            })

        for p in positions:
            p["agirlik_%"] = round(p["deger"] / total_val * 100, 2) if total_val else None

        top = max(positions, key=lambda p: p["deger"], default=None)
        result["hesaplar"][acct] = {
            "snapshot": rows[0]["snapshot_ts"],
            "pozisyon_sayisi": len(positions),
            "toplam_deger": round(total_val, 2),
            "toplam_kar_zarar": round(total_pnl, 2),
            "toplam_kar_zarar_%": round(total_pnl / (total_val - total_pnl) * 100, 2)
                                   if (total_val - total_pnl) else None,
            "en_buyuk_pozisyon": top["sembol"] if top else None,
            "yogunlasma_%": top["agirlik_%"] if top else None,
            "pozisyonlar": positions,
        }
        grand_value += total_val
        grand_pnl += total_pnl

    result["toplam"] = {
        "deger": round(grand_value, 2),
        "kar_zarar": round(grand_pnl, 2),
        "not": "Farkli para birimleri CEVRILMEDEN toplandi. Kur icin `fx` araci ve `fx_rates` tablosu VAR; bu toplami tek para biriminde isteyen taraf oradan cevirmeli.",
    }
    return result
