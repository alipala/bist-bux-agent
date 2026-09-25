#!/usr/bin/env python
"""
STOP SINAVI — `docs/stop-sinavi.md` §0 ON KAYDININ uygulanisi.

Kollar, donemler, olcutler ve karar kurali ON KAYITTA; burada yalnizca
uygulaniyor. Bir sabit on kayitla ayrisirsa YANLIS olan bu dosyadir.

    S0  2N sabit (uretim)     S3  2N izleyen
    S1  1,5N sabit            S4  3N izleyen
    S2  3N sabit              S5  stop yok (yalnizca 10 gun dip)

MOTOR TEK: islemler `trend_takip.islemler(..., stop_n=, izleyen=)` ile
uretiliyor — uretimdeki donguyle ayni. Evren, likidite esigi ve borsa
limiti `run.py trend --piyasa abd` ile AYNI kaynaktan.

Kullanim (canli db'ye YAZMAZ, yalnizca okur):
    .venv/bin/python scripts/stop_sinavi.py --db KOPYA.db --cikti SONUC.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finagent.analysis.trend_takip import (_yuzdelik, aylik_kumelenme,  # noqa: E402
                                           islemler)

# --- ON KAYIT SABITLERI (docs/stop-sinavi.md §0) -----------------------
IS_ = ("2016-01-01", "2021-12-31")
OOS = ("2022-01-01", "2026-09-24")
MALIYET = 0.004
KOLLAR = {"S0": (2.0, False), "S1": (1.5, False), "S2": (3.0, False),
          "S3": (2.0, True), "S4": (3.0, True), "S5": (None, False)}
TABAN = "S0"
T_ESIGI = 2.0
P5_TOLERANS_PUAN = 1.0
# ----------------------------------------------------------------------


def _metrik(isl: list[dict]) -> dict:
    if not isl:
        return {"islem": 0}
    g = [t["getiri"] - MALIYET for t in isl]
    ay = aylik_kumelenme(isl, MALIYET)
    return {"islem": len(g),
            "beklenti_%": sum(g) / len(g) * 100,
            "p5_%": _yuzdelik(g, 0.05) * 100,
            "en_kotu_%": min(g) * 100,
            "t_ay": ay.get("t_ay"),
            "en_kotu_ay_%": ay.get("en_kotu_ay_%"),
            "ay": ay.get("ay"),
            "stop_payi_%": sum(1 for t in isl if t["sebep"] == "2N stop") / len(g) * 100,
            "ort_gun": sum(t.get("gun", 0) or 0 for t in isl) / len(g)}


def _aylik_ort(isl: list[dict]) -> dict[str, float]:
    aylar: dict[str, list[float]] = {}
    for t in isl:
        aylar.setdefault(str(t["giris_ts"])[:7], []).append(t["getiri"] - MALIYET)
    return {a: sum(v) / len(v) for a, v in aylar.items()}


def eslestirilmis_t(aday: list[dict], taban: list[dict]) -> dict:
    """
    SAF. Iki kolun ORTAK aylarinda (aday ay ort. - taban ay ort.) farkinin
    t degeri. Gozlem birimi AY (islemler bagimsiz degil).
    """
    a, b = _aylik_ort(aday), _aylik_ort(taban)
    ortak = sorted(set(a) & set(b))
    d = [a[m] - b[m] for m in ortak]
    n = len(d)
    if n < 2:
        return {"ortak_ay": n, "fark_ort_%": None, "t": None}
    o = sum(d) / n
    s = math.sqrt(sum((x - o) ** 2 for x in d) / (n - 1))
    return {"ortak_ay": n, "fark_ort_%": o * 100,
            "t": (o / (s / math.sqrt(n))) if s > 0 else None}


def karar(is_metrik: dict, oos_metrik: dict, oos_t: dict) -> dict:
    """
    SAF. §0 karar kurali. `is_metrik`/`oos_metrik`: kol -> _metrik;
    `oos_t`: kol -> eslestirilmis_t (S0'a karsi, OOS).
    """
    secilen = max(is_metrik, key=lambda k: is_metrik[k].get("beklenti_%", -1e9))
    if secilen == TABAN:
        return {"secilen_IS": secilen, "gecti": False,
                "sonuc": "2N sabit kalir (IS'te en iyi kol zaten S0)"}
    o, t0 = oos_metrik[secilen], oos_metrik[TABAN]
    tt = oos_t[secilen]["t"]
    a = o["beklenti_%"] > t0["beklenti_%"]
    b = tt is not None and tt >= T_ESIGI
    c = o["p5_%"] >= t0["p5_%"] - P5_TOLERANS_PUAN
    gecti = a and b and c
    return {"secilen_IS": secilen, "a_beklenti": a, "b_t": b, "c_p5": c,
            "gecti": gecti,
            "sonuc": (f"{secilen} 2N'in yerine gecer" if gecti
                      else f"{secilen} OOS'ta gecemedi -> 2N sabit kalir")}


def _sembol_isi(arg) -> dict:
    sem, seri, borsa_limiti, asgari_devir = arg
    out = {"sembol": sem}
    for kol, (n, iz) in KOLLAR.items():
        out[kol] = [t for t in islemler(seri, borsa_limiti, asgari_devir=asgari_devir,
                                        stop_n=n, izleyen=iz)
                    if IS_[0] <= str(t["giris_ts"])[:10] <= OOS[1]]
    return out


def _donem(isl, d):
    return [t for t in isl if d[0] <= str(t["giris_ts"])[:10] <= d[1]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--isci", type=int, default=4)
    a = ap.parse_args()

    from finagent.analysis.backtest import _evren
    from finagent.config import load_settings
    from finagent.pulse.screener import BORSA_LIMITI
    from finagent.storage.db import Database

    t0 = time.time()
    db = Database(Path(a.db))
    s = load_settings()
    evren = _evren(db, "BUX", endeksler=("S&P 500", "Nasdaq 100"))
    asgari_devir = float(s.get("ibkr.strateji.asgari_devir.USD", 1_000_000))
    borsa_limiti = BORSA_LIMITI.get("BUX")
    if a.limit:
        evren = evren[:a.limit]
    isler, kisa = [], 0
    for e in evren:
        seri = [dict(r) for r in db.fiyat_serisi(e["id"], limit=100000)]
        if len(seri) < 300:
            kisa += 1
            continue
        isler.append((e["symbol"], seri, borsa_limiti, asgari_devir))
    print(f"veri yuklendi: {len(isler)} sembol · {time.time() - t0:.0f} sn", flush=True)
    with Pool(a.isci) as p:
        sonuc = p.map(_sembol_isi, isler, chunksize=4)
    print(f"kosu bitti: {time.time() - t0:.0f} sn", flush=True)

    tum = {k: [t for s_ in sonuc for t in s_[k]] for k in KOLLAR}
    rapor = {"on_kayit": {"IS": IS_, "OOS": OOS, "MALIYET": MALIYET,
                          "KOLLAR": KOLLAR, "T_ESIGI": T_ESIGI,
                          "P5_TOLERANS_PUAN": P5_TOLERANS_PUAN},
             "kapsam": {"evren": len(evren), "sembol": len(sonuc), "kisa_seri": kisa}}
    for ad, d in (("IS", IS_), ("OOS", OOS)):
        rapor[ad] = {k: _metrik(_donem(tum[k], d)) for k in KOLLAR}
        rapor[ad + "_eslestirilmis_t"] = {
            k: eslestirilmis_t(_donem(tum[k], d), _donem(tum[TABAN], d))
            for k in KOLLAR if k != TABAN}
    rapor["karar"] = karar(rapor["IS"], rapor["OOS"], rapor["OOS_eslestirilmis_t"])
    Path(a.cikti).write_text(json.dumps(rapor, ensure_ascii=False, indent=1))
    print(json.dumps(rapor["karar"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
