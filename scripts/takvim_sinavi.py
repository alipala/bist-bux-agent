#!/usr/bin/env python
"""
TAKVIM SINAVI — `docs/takvim-filtresi.md` §0 ON KAYDININ uygulanisi.

Kollar, kontrol, olcutler ve karar kurali ON KAYITTA; burada yalnizca
uygulaniyor. Bir sabit on kayitla ayrisirsa YANLIS olan bu dosyadir.

    T   engelsiz (uretimdeki kural)
    E1  bilanco tepki gunu girisin sonraki 1 islem gununde -> girme
    E5  ayni, 5 islem gunu
    M1  CPI / NFP / FOMC tepki gunu sonraki 1 islem gununde -> girme

Her engelli kol icin 40 rastgele kontrol: ayni sembolde, SINAV
PENCERESI ICINDE, engelle AYNI SAYIDA rastgele gun.

MOTOR TEK: islemler `trend_takip.islemler(..., giris_engeli=)` ile
uretiliyor — uretimdeki donguyle ayni. Evren, likidite esigi ve borsa
limiti `run.py trend --piyasa abd` ile AYNI kaynaktan.

Kullanim (canli db'ye YAZMAZ, yalnizca okur):
    .venv/bin/python scripts/takvim_sinavi.py --db KOPYA.db --cikti SONUC.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finagent.analysis import olay_takvimi as ot  # noqa: E402
from finagent.analysis.trend_takip import (_yuzdelik, aylik_kumelenme,  # noqa: E402
                                           islemler)

# --- ON KAYIT SABITLERI (docs/takvim-filtresi.md §0) -------------------
BAS, BIT = "2016-01-01", "2026-09-24"
MALIYET = 0.004
TUR = 40
TOHUM0 = 20260924
KOLLAR = {"E1": ("bilanco", 1), "E5": ("bilanco", 5), "M1": ("makro", 1)}
MAKRO_FILTRE_OLAYLARI = ("ABD TUFE (CPI)", "ABD istihdam raporu (NFP)")
# ----------------------------------------------------------------------


def _metrik(isl: list[dict]) -> dict:
    """On kayittaki olcutler. Birincil: stop alti bosluk kaybi + beklenti."""
    if not isl:
        return {"islem": 0}
    g = [t["getiri"] - MALIYET for t in isl]
    alt = [(t["stop"] - t["cikis"]) / t["giris"] for t in isl
           if t["sebep"] == "2N stop" and t["cikis"] < t["stop"]]
    ay = aylik_kumelenme(isl, MALIYET)
    return {"islem": len(g),
            "beklenti_%": sum(g) / len(g) * 100,
            "stop_alti_toplam_%": sum(alt) * 100,
            "stop_alti_n": len(alt),
            "stop_alti_ort_%": (sum(alt) / len(alt) * 100) if alt else 0.0,
            "p5_%": _yuzdelik(g, 0.05) * 100,
            "en_kotu_%": min(g) * 100,
            "t_ay": ay.get("t_ay")}


def _sembol_isi(arg) -> dict:
    """
    TEK sembol: tum kollar + tum kontroller. Isci sureclerde kosar; db
    YOK, girdi tamamen parametrede (saf).
    """
    idx, sem, seri, bilanco, makro, borsa_limiti, asgari_devir = arg
    gunler = [str(b["ts"])[:10] for b in seri]
    pencere = [g for g in gunler if BAS <= g <= BIT]
    tep = {"bilanco": ot.tepki_kumesi(bilanco, gunler),
           "makro": ot.tepki_kumesi(makro, gunler)}

    def _kosu(engel):
        return [t for t in islemler(seri, borsa_limiti, asgari_devir=asgari_devir,
                                    giris_engeli=engel)
                if BAS <= str(t["giris_ts"])[:10] <= BIT]

    out = {"sembol": sem, "T": _kosu(None), "engel_sayisi": {}, "kol": {},
           "kontrol": {}}
    for kol, (tur, K) in KOLLAR.items():
        # ENGEL PENCEREYLE KESILIR: kontrol pencere icinden cekiliyor,
        # filtre de ayni kumeyle sinirli olmali — yoksa iki kol farkli
        # sayida ETKILI gun engeller.
        engel = ot.giris_engeli(gunler, tep[tur], K) & set(pencere)
        out["engel_sayisi"][kol] = len(engel)
        out["kol"][kol] = _kosu(engel)
        out["kontrol"][kol] = [
            _kosu(ot.rastgele_engel(pencere, len(engel),
                                    TOHUM0 + 1000 * r + idx))
            for r in range(TUR)]
    # ETKI OLCUMU (betimsel, karar vermez): pencere + ATR isinma payi.
    bas_i = max(0, next((i for i, g in enumerate(gunler) if g >= BAS),
                        len(gunler)) - 25)
    alt = seri[bas_i:]
    out["bosluk"] = {"bilanco": ot.bosluklar(alt, tep["bilanco"])}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--isci", type=int, default=4)
    a = ap.parse_args()

    from finagent.analysis.backtest import _evren
    from finagent.collectors.takvim import MAKRO_ZAMAN
    from finagent.config import load_settings
    from finagent.pulse.screener import BORSA_LIMITI
    from finagent.storage.db import Database

    t0 = time.time()
    db = Database(Path(a.db))
    s = load_settings()
    # `run.py trend --piyasa abd` ile AYNI uc ayar.
    evren = _evren(db, "BUX", endeksler=("S&P 500", "Nasdaq 100"))
    asgari_devir = float(s.get("ibkr.strateji.asgari_devir.USD", 1_000_000))
    borsa_limiti = BORSA_LIMITI.get("BUX")
    if a.limit:
        evren = evren[:a.limit]

    yer = ",".join("?" * len(MAKRO_FILTRE_OLAYLARI))
    makro_hepsi = [dict(r) for r in db.query(
        f"""SELECT tarih, kaynak, olay FROM takvim
            WHERE (kaynak IN ('alfred', 'fred') AND olay IN ({yer}))
               OR kaynak = 'fed'""", MAKRO_FILTRE_OLAYLARI)]
    for m in makro_hepsi:
        m["zaman"] = MAKRO_ZAMAN[m["kaynak"]]
    # Olay turu basina ayri kume — etki olcumu icin (betimsel).
    tur_olaylari: dict[str, list[dict]] = {}
    for r in db.query("SELECT tarih, kaynak, olay FROM takvim "
                      "WHERE kaynak IN ('alfred', 'fred', 'fed')"):
        tur_olaylari.setdefault(r["olay"], []).append(
            {"tarih": r["tarih"], "zaman": MAKRO_ZAMAN[r["kaynak"]]})

    isler, kapsam = [], {"evren": len(evren), "kisa_seri": 0,
                         "bilancosuz": []}
    makro_seriler = {}
    for idx, e in enumerate(evren):
        seri = [dict(r) for r in db.fiyat_serisi(e["id"], limit=100000)]
        if len(seri) < 300:
            kapsam["kisa_seri"] += 1
            continue
        bil = [dict(r) for r in db.query(
            "SELECT tarih, zaman, saat FROM bilanco_takvimi "
            "WHERE instrument_id = ? AND kaynak = 'yahoo'", (e["id"],))]
        if not bil:
            kapsam["bilancosuz"].append(e["symbol"])
        isler.append((idx, e["symbol"], seri, bil, makro_hepsi,
                      borsa_limiti, asgari_devir))
        makro_seriler[e["symbol"]] = seri
    print(f"veri yuklendi: {len(isler)} sembol · {time.time() - t0:.0f} sn",
          flush=True)

    with Pool(a.isci) as p:
        sonuc = p.map(_sembol_isi, isler, chunksize=4)
    print(f"kosu bitti: {time.time() - t0:.0f} sn", flush=True)

    # --- toplama ---------------------------------------------------------
    rapor = {"on_kayit": {"BAS": BAS, "BIT": BIT, "MALIYET": MALIYET,
                          "TUR": TUR, "TOHUM0": TOHUM0, "KOLLAR": KOLLAR},
             "kapsam": {**kapsam, "sembol": len(sonuc),
                        "bilancosuz_n": len(kapsam["bilancosuz"])}}
    T = [t for s_ in sonuc for t in s_["T"]]
    rapor["T"] = _metrik(T)
    rapor["kollar"] = {}
    for kol in KOLLAR:
        f = _metrik([t for s_ in sonuc for t in s_["kol"][kol]])
        kontrol = [_metrik([t for s_ in sonuc for t in s_["kontrol"][kol][r]])
                   for r in range(TUR)]
        k_alt = [k["stop_alti_toplam_%"] for k in kontrol]
        k_bek = [k["beklenti_%"] for k in kontrol]
        a_ = f["stop_alti_toplam_%"] < min(k_alt)
        b_ = f["beklenti_%"] >= statistics.median(k_bek)
        rapor["kollar"][kol] = {
            "filtre": f,
            "engelli_gun": sum(s_["engel_sayisi"][kol] for s_ in sonuc),
            "kontrol_stop_alti_min_%": min(k_alt),
            "kontrol_stop_alti_medyan_%": statistics.median(k_alt),
            "kontrol_beklenti_medyan_%": statistics.median(k_bek),
            "kontrol_beklenti_p5_p95_%": [_yuzdelik(k_bek, .05), _yuzdelik(k_bek, .95)],
            "kontrol_islem_medyan": statistics.median(k["islem"] for k in kontrol),
            "filtre_stop_alti_sirasi": 1 + sum(1 for x in k_alt
                                               if x <= f["stop_alti_toplam_%"]),
            "a_stop_alti_hepsinden_dusuk": a_,
            "b_beklenti_medyandan_dusuk_degil": b_,
            "GECTI": bool(a_ and b_),
        }

    # ETKI OLCUMU (betimsel)
    bil_olay = [x for s_ in sonuc for x in s_["bosluk"]["bilanco"][0]]
    bil_diger = [x for s_ in sonuc for x in s_["bosluk"]["bilanco"][1]]
    etki = {"bilanco": {"olay": ot.dagilim(bil_olay), "diger": ot.dagilim(bil_diger)}}
    for olay, kayit in sorted(tur_olaylari.items()):
        o_, d_ = [], []
        for sem, seri in makro_seriler.items():
            gunler = [str(b["ts"])[:10] for b in seri]
            bas_i = max(0, next((i for i, g in enumerate(gunler) if g >= BAS),
                                len(gunler)) - 25)
            x, y = ot.bosluklar(seri[bas_i:], ot.tepki_kumesi(kayit, gunler))
            o_ += x
            d_ += y
        etki[olay] = {"olay": ot.dagilim(o_), "diger": ot.dagilim(d_)}
    rapor["etki"] = etki
    rapor["sure_sn"] = round(time.time() - t0)

    Path(a.cikti).write_text(json.dumps(rapor, ensure_ascii=False, indent=1,
                                        default=str))
    _yazdir(rapor)
    return 0


def _yazdir(r: dict) -> None:
    print(f"\nKapsam: {r['kapsam']}")
    print("\nETKI (normalize acilis boslugu, N cinsinden; betimsel):")
    for k, v in r["etki"].items():
        o, d = v["olay"], v["diger"]
        if o.get("n"):
            print(f"  {k:<34} olay n={o['n']:>6} medyan {o['medyan_N']:.3f} "
                  f">1N %{o['buyuk_bosluk_%']:>5} | diger medyan "
                  f"{d['medyan_N']:.3f} >1N %{d['buyuk_bosluk_%']}")
    t = r["T"]
    print(f"\nT (taban): islem {t['islem']} · beklenti %{t['beklenti_%']:.3f} · "
          f"stop alti toplam %{t['stop_alti_toplam_%']:.1f} "
          f"(n={t['stop_alti_n']}) · p5 %{t['p5_%']:.2f} · t_ay {t['t_ay']}")
    for kol, v in r["kollar"].items():
        f = v["filtre"]
        print(f"\n{kol}: engelli gun {v['engelli_gun']} · islem {f['islem']} "
              f"(kontrol medyan {v['kontrol_islem_medyan']})")
        print(f"   stop alti toplam  filtre %{f['stop_alti_toplam_%']:.1f}  "
              f"kontrol min %{v['kontrol_stop_alti_min_%']:.1f} medyan "
              f"%{v['kontrol_stop_alti_medyan_%']:.1f}  sira "
              f"{v['filtre_stop_alti_sirasi']}/41")
        print(f"   beklenti          filtre %{f['beklenti_%']:.3f}  kontrol "
              f"medyan %{v['kontrol_beklenti_medyan_%']:.3f}  p5-p95 "
              f"{[round(x, 3) for x in v['kontrol_beklenti_p5_p95_%']]}")
        print(f"   (a) {v['a_stop_alti_hepsinden_dusuk']}  "
              f"(b) {v['b_beklenti_medyandan_dusuk_degil']}  ->  "
              f"{'GECTI' if v['GECTI'] else 'GECMEDI'}")


if __name__ == "__main__":
    raise SystemExit(main())
