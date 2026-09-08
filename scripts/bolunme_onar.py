#!/usr/bin/env python
"""
IKI TABANLI FIYAT SERILERINI ONARIR ve yanlis olculmus tahminleri
yeniden acar.

NE OLDU (2026-09-08)
--------------------
BLCYT 1 Eylul'de 10:1 bolundu. Yahoo da Is Yatirim da gecmisi geriye
donuk yeniden tabanliyor (25 Agu kapanisi artik 2,11 olarak geliyor).
Bizim tazeleme penceremiz 5 gun; bolunmeden sonraki kosular yalnizca
26 Agu-1 Eyl barlarini yeniden yazdi, 25 Agu ve oncesi ESKI tabanda
kaldi. Seri tek kaynak icinde iki tabanli oldu:

    yahoo_bist  2026-08-25  21.10   |  2026-08-26  2.132

Ayni kalip AKFIS, SDTTR, ORGE, DNISI ve CVKMD'de (Agustos 2026, hepsi
BIST kurumsal islem). Taktik defteri BLCYT'yi 22,60 -> 2,25 = "-%90"
diye puanladi ve fren girdisine oyle girdi.

KALICI DUZELTME KODDA (bu betik onun yerine gecmez):
  * `analysis/tutarlilik.py`  — sicrama olcutu, tek tanim
  * collector'lar (`isyatirim`, `bistgecmis`) sicramali sembolu TAM
    gecmisle yeniden cekiyor (kendini onarma)
  * `journal.puanla` sicramanin ustunden olcum yapmiyor; taban
    ayrismissa katsayiyla uzlastiriyor

BU BETIK ne yapar: bugun sicramali olan serileri collector'larin KENDI
yoluyla hemen yeniden ceker (gece kosusunu beklemeden), sonra iki
tabanla OLCULMUS tahminleri yeniden acar ve puanlayiciyi kosturur.

NEDEN "SATIRI SIL" DEGIL: kayit temizligi duzeltme sayilmaz (bkz.
hayalet enstruman notlari). Satir kaliyor; eski olcum `olcum_notu`ya
yaziliyor; yeni olcum ayni satira, dogru tabanla.

Kullanim:
    .venv/bin/python scripts/bolunme_onar.py            # kuru kosu
    .venv/bin/python scripts/bolunme_onar.py --uygula   # yaz
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finagent.analysis.tutarlilik import SICRAMA_ESIGI, sicramali_semboller  # noqa: E402
from finagent.config import load_settings  # noqa: E402
from finagent.storage.db import Database  # noqa: E402

log = logging.getLogger("bolunme_onar")


def _yahoo_onar(s, db, semboller: list[str]) -> dict[str, int]:
    import warnings

    import yfinance as yf

    from finagent.collectors import bistgecmis as BG

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    c = BG.BistGecmisCollector(s, db)
    period = s.get("sources.bistgecmis.period", "10y")
    out = {}
    for sym in semboller:
        try:
            df = c._tek_cek(yf, f"{sym}{BG.SONEK}", period)
            out[sym] = c._cerceveyi_yaz(sym, df)
        except Exception as e:                            # noqa: BLE001
            log.warning("yahoo_bist %s cekilemedi: %s", sym, e)
            out[sym] = -1
    return out


def _isyatirim_onar(s, db, semboller: list[str]) -> dict[str, int]:
    from finagent.collectors import isyatirim as IY

    c = IY.IsYatirimCollector(s, db)
    lookback = int(s.get("analysis.lookback_days", 250))
    end = date.today()
    start = end - timedelta(days=int(lookback * 1.6) + 10)
    base = s.get("sources.isyatirim.base_url", "https://www.isyatirim.com.tr")
    out = {}
    for sym in semboller:
        url = IY.ENDPOINT.format(base=base, symbol=sym,
                                 start=start.strftime("%d-%m-%Y"),
                                 end=end.strftime("%d-%m-%Y"))
        rows = c._fetch(url, sym)
        if not rows:
            log.warning("isyatirim %s: %s", sym,
                        "cekilemedi" if rows is None else "kaynakta seri yok")
            out[sym] = -1
            continue
        iid = db.upsert_instrument(sym, "BIST", asset_type="equity", currency="TRY")
        out[sym] = db.upsert_prices(iid, rows, source=c.name, currency="TRY")
    return out


def _iki_tabanla_olculenler(db, semboller: list[str]) -> list:
    """
    Olculmus ama baslangic/bitis orani sicrama esigini asan satirlar —
    yani iki tabanla olculmus olanlar. Yalnizca onarilan sembollerde.
    """
    if not semboller:
        return []
    return db.query(
        f"""SELECT p.id, i.symbol, p.ajan, p.sahip, p.olusma_ts, p.olcum_ts,
                   p.baslangic_fiyat, p.bitis_fiyat, p.getiri_pct, p.isabet
            FROM predictions p JOIN instruments i ON i.id = p.instrument_id
            WHERE p.olcum_ts IS NOT NULL AND p.bitis_fiyat > 0
              AND p.baslangic_fiyat > 0
              AND UPPER(i.symbol) IN ({','.join('?' * len(semboller))})
              AND (p.bitis_fiyat / p.baslangic_fiyat >= ?
                   OR p.bitis_fiyat / p.baslangic_fiyat <= 1.0 / ?)
            ORDER BY p.id""",
        (*[x.upper() for x in semboller], SICRAMA_ESIGI, SICRAMA_ESIGI))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--uygula", action="store_true",
                    help="yaz; verilmezse yalnizca ne yapacagini soyler")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    s = load_settings()
    db = Database(s.db_path)
    db.init_schema()

    kaynaklar = ("yahoo_bist", "isyatirim")
    once = {k: sicramali_semboller(db, k, venue="BIST") for k in kaynaklar}
    print("== ONCE: sicramali seriler ==")
    for k, m in once.items():
        for sym, liste in sorted(m.items()):
            print(f"  {k:11s} {sym:7s} " + " · ".join(
                f"{x['onceki_ts']}->{x['ts']} x{x['oran']:g}" for x in liste))
    hepsi = sorted({sym for m in once.values() for sym in m})
    if not hepsi:
        print("sicramali seri yok — yapilacak is yok")
        return 0

    olculen = _iki_tabanla_olculenler(db, hepsi)
    print(f"\n== iki tabanla OLCULMUS tahmin: {len(olculen)} ==")
    for r in olculen:
        print(f"  #{r['id']} {r['symbol']} {r['ajan']}/{r['sahip']} "
              f"{r['olusma_ts']} -> {r['olcum_ts']}  "
              f"{r['baslangic_fiyat']:g} -> {r['bitis_fiyat']:g} "
              f"({r['getiri_pct']:+.1f}%, isabet={r['isabet']})")

    if not args.uygula:
        print("\nKURU KOSU — yazmak icin --uygula")
        return 0

    print("\n== yeniden cekiliyor ==")
    y = _yahoo_onar(s, db, sorted(once["yahoo_bist"]))
    i = _isyatirim_onar(s, db, sorted(once["isyatirim"]))
    for sym in hepsi:
        print(f"  {sym:7s} yahoo_bist={y.get(sym, '-')} isyatirim={i.get(sym, '-')} satir")

    sonra = {k: sicramali_semboller(db, k, venue="BIST") for k in kaynaklar}
    print("\n== SONRA: sicramali seriler ==")
    kalan = sorted({sym for m in sonra.values() for sym in m})
    if kalan:
        for k, m in sonra.items():
            for sym, liste in sorted(m.items()):
                print(f"  {k:11s} {sym:7s} " + " · ".join(
                    f"{x['onceki_ts']}->{x['ts']} x{x['oran']:g}" for x in liste))
        print("  (kalanlar: kaynak henuz yeniden tabanlamamis olabilir; "
              "puanlayici bunlarin ustunden olcum yapmaz)")
    else:
        print("  yok — iki kaynak da tek tabanda")

    # YENIDEN AC: yalnizca artik sicramasiz olan sembollerdeki satirlar.
    onarilan = [sym for sym in hepsi if sym not in kalan]
    acilacak = [r for r in olculen if r["symbol"].upper() in onarilan]
    if acilacak:
        with db.tx() as c:
            for r in acilacak:
                c.execute(
                    """UPDATE predictions
                       SET olcum_ts=NULL, bitis_fiyat=NULL, getiri_pct=NULL,
                           piyasa_getiri_pct=NULL, anormal_pct=NULL, isabet=NULL,
                           taktik_tetiklendi=NULL,
                           olcum_notu=?
                       WHERE id=?""",
                    (f"bolunme onarimi ({date.today().isoformat()}): iki tabanla "
                     f"olculmustu ({r['baslangic_fiyat']:g} -> {r['bitis_fiyat']:g}, "
                     f"{r['getiri_pct']:+.1f}%), yeniden acildi", r["id"]))
        print(f"\n{len(acilacak)} tahmin yeniden acildi: "
              + ", ".join(f"#{r['id']}" for r in acilacak))

    from finagent.pulse.journal import Defter
    rapor = Defter(db).puanla()
    print(f"\npuanlayici: {rapor['olculen_toplam']} olculdu, "
          f"{rapor['tetiklenmeyen_toplam']} tetiklenmedi, "
          f"{rapor['sicrama_bekleyen']} sicrama bekliyor")
    for r in acilacak:
        y2 = db.query("SELECT olcum_ts, baslangic_fiyat, bitis_fiyat, getiri_pct, "
                      "anormal_pct, isabet, olcum_notu FROM predictions WHERE id=?",
                      (r["id"],))[0]
        print(f"  #{r['id']} {r['symbol']}: olcum={y2['olcum_ts']} "
              f"getiri={y2['getiri_pct']} anormal={y2['anormal_pct']} "
              f"isabet={y2['isabet']} · {y2['olcum_notu']}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
