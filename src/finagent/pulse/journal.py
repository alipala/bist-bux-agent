"""
TAHMIN DEFTERI — sistemin kendi isabetini olctugu yer.

NEDEN EN ONEMLI PARCA BU
------------------------
Olculdu: gunluk al-satta %50 isabet ayda -%4.2 getiriyor (komisyon),
%55 isabet +%5.6. Yani her sey isabet oraninin 50 mi 55 mi olduguna
bagli — ve bu VARSAYILAMAZ, olculmesi gerekir.

Kendi tahminlerini kaydetmeyen bir tavsiye sistemi, sonradan yalnizca
tutan tahminleri hatirlar. Bu bir hafiza kusuru degil, sistematik bir
yanilgidir ve tek caresi ONCEDEN yazmaktir.

PUANLAMA: HAM GETIRI DEGIL, ANORMAL GETIRI
------------------------------------------
"Yukari" dedik ve hisse %3 yukseldi — isabet mi? Piyasa ayni donemde %4
yukseldiyse HAYIR. Bu yuzden puanlama piyasa vekiline gore duzeltilmis
getiriyi kullanir (beta ile). Aksi halde boga piyasasinda her "yukari"
tahmini isabet gorunur ve sistem kendini iyi sanir.

NOTR TAHMIN
-----------
"notr" bir tahmin de puanlanir: hareket, olculen gunluk oynakligin
altinda kaldiysa isabettir. Boylece "bir sey olmayacak" demek de
sorumluluk dogurur; bedava kacamak degildir.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

VARSAYILAN_UFUK = 5          # islem gunu
NOTR_BANDI = 1.0             # kac gunluk-sigma icinde kalirsa "notr" isabet


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Defter:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------
    def kaydet(self, gorusler: list[dict]) -> int:
        """
        Panelin yapisal goruslerini tahmin olarak yazar.

        Ayni gun + ayni enstruman + ayni ufuk icin TEK kayit tutulur;
        birden fazla ajan ayni sembole bakarsa en YUKSEK guvenli olan
        kalir (celiskiyi hakem zaten raporluyor, defter tek kayit ister).
        """
        if not gorusler:
            return 0
        ts = _bugun()
        en_iyi: dict[tuple, dict] = {}
        for g in gorusler:
            sem = str(g.get("sembol", "")).strip().upper()
            if not sem or g.get("yon") not in ("yukari", "asagi", "notr"):
                continue
            e = self.db.query(
                "SELECT id FROM instruments WHERE UPPER(symbol)=? LIMIT 1", (sem,))
            if not e:
                continue
            iid = e[0]["id"]
            seri = self.db.fiyat_serisi(iid, 2)
            if not seri or not seri[-1]["close"]:
                continue
            ufuk = int(g.get("ufuk_gun") or VARSAYILAN_UFUK)
            anahtar = (iid, ufuk)
            guven = float(g.get("guven") or 0.5)
            if anahtar in en_iyi and en_iyi[anahtar]["guven"] >= guven:
                continue
            en_iyi[anahtar] = {
                "iid": iid, "yon": g["yon"], "ufuk": ufuk, "guven": guven,
                "gerekce": f"[{g.get('ajan','?')}] {g.get('gerekce','')}"[:400],
                "fiyat": seri[-1]["close"], "ccy": seri[-1]["currency"]}

        if not en_iyi:
            return 0
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO predictions
                   (olusma_ts, instrument_id, yon, ufuk_gun, guven, gerekce,
                    baslangic_fiyat, para_birimi)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(olusma_ts, instrument_id, ufuk_gun) DO UPDATE SET
                     yon=excluded.yon, guven=excluded.guven,
                     gerekce=excluded.gerekce""",
                [(ts, v["iid"], v["yon"], v["ufuk"], v["guven"], v["gerekce"],
                  v["fiyat"], v["ccy"]) for v in en_iyi.values()])
        return len(en_iyi)

    # ------------------------------------------------------------------
    def puanla(self) -> dict:
        """
        Ufku dolmus tahminleri olcer.

        Piyasa vekili varsa beta ile duzeltilmis ANORMAL getiri
        kullanilir; yoksa ham getiri ve bu kayitta belirtilir.
        """
        bekleyen = self.db.query(
            """SELECT * FROM predictions WHERE isabet IS NULL
               ORDER BY olusma_ts""")
        olculen, kayitlar = 0, []
        for p in bekleyen:
            seri = self.db.fiyat_serisi(p["instrument_id"], 400)
            sonrasi = [r for r in seri if r["ts"] > p["olusma_ts"]]
            if len(sonrasi) < p["ufuk_gun"]:
                continue                       # ufuk dolmamis, bekle
            bitis = sonrasi[p["ufuk_gun"] - 1]
            if not bitis["close"] or not p["baslangic_fiyat"]:
                continue
            getiri = (bitis["close"] / p["baslangic_fiyat"] - 1) * 100

            piyasa_g, anormal = None, getiri
            vekil = self.db.piyasa_vekili(p["instrument_id"])
            if vekil:
                pg, beta = self._piyasa(vekil["instrument_id"], p["olusma_ts"],
                                        bitis["ts"], p["instrument_id"])
                if pg is not None:
                    piyasa_g = pg
                    anormal = getiri - (beta or 1.0) * pg

            esik = self._notr_esigi(p["instrument_id"], p["ufuk_gun"])
            if p["yon"] == "yukari":
                isabet = 1 if anormal > 0 else 0
            elif p["yon"] == "asagi":
                isabet = 1 if anormal < 0 else 0
            else:
                isabet = 1 if abs(anormal) <= esik else 0

            kayitlar.append((bitis["ts"], bitis["close"], round(getiri, 3),
                             round(piyasa_g, 3) if piyasa_g is not None else None,
                             round(anormal, 3), isabet, p["id"]))
            olculen += 1

        if kayitlar:
            with self.db.tx() as c:
                c.executemany(
                    """UPDATE predictions SET olcum_ts=?, bitis_fiyat=?,
                       getiri_pct=?, piyasa_getiri_pct=?, anormal_pct=?, isabet=?
                       WHERE id=?""", kayitlar)
        return {"olculen": olculen, **self.karne()}

    def _piyasa(self, vekil_id, bas_ts, bitis_ts, hisse_id):
        """Vekilin ayni donemdeki getirisi ve hissenin betasi."""
        v = self.db.fiyat_serisi(vekil_id, 400)
        bas = [r for r in v if r["ts"] <= bas_ts]
        son = [r for r in v if r["ts"] <= bitis_ts]
        if not bas or not son or not bas[-1]["close"]:
            return None, None
        pg = (son[-1]["close"] / bas[-1]["close"] - 1) * 100

        h = self.db.fiyat_serisi(hisse_id, 300)
        eslesme = {r["ts"]: r["close"] for r in v}
        y, x = [], []
        for a, b in zip(h, h[1:]):
            if b["ts"] in eslesme and a["ts"] in eslesme and a["close"] and eslesme[a["ts"]]:
                y.append(b["close"] / a["close"] - 1)
                x.append(eslesme[b["ts"]] / eslesme[a["ts"]] - 1)
        if len(y) < 30:
            return pg, 1.0
        ox = sum(x) / len(x); oy = sum(y) / len(y)
        sxx = sum((v_ - ox) ** 2 for v_ in x)
        if sxx <= 0:
            return pg, 1.0
        beta = sum((a - ox) * (b - oy) for a, b in zip(x, y)) / sxx
        return pg, beta

    def _notr_esigi(self, instrument_id, ufuk) -> float:
        """Ufuk boyunca beklenen tipik hareket (1 sigma), yuzde."""
        seri = self.db.fiyat_serisi(instrument_id, 200)
        g = [b["close"] / a["close"] - 1 for a, b in zip(seri, seri[1:])
             if a["close"] and b["close"]]
        if len(g) < 30:
            return 2.0
        o = sum(g) / len(g)
        sd = math.sqrt(sum((x - o) ** 2 for x in g) / (len(g) - 1))
        return sd * math.sqrt(ufuk) * 100 * NOTR_BANDI

    # ------------------------------------------------------------------
    def karne(self, gun: int = 180) -> dict:
        """
        Isabet karnesi. Az sayida olcumle guven araligi COK genis olur;
        bu yuzden hem sayi hem aralik doner ve 20'nin altinda ACIKCA
        "yetersiz ornek" denir.
        """
        sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
        r = self.db.query(
            """SELECT COUNT(*) n, SUM(isabet) d, AVG(anormal_pct) ort
               FROM predictions WHERE isabet IS NOT NULL AND olusma_ts >= ?""",
            (sinir,))[0]
        n, dogru = r["n"] or 0, r["d"] or 0
        if not n:
            return {"olcum": 0, "not": "henuz puanlanmis tahmin yok"}
        p = dogru / n
        # Wilson skor araligi — kucuk orneklemde normal yaklasimdan durust.
        z = 1.96
        payda = 1 + z * z / n
        merkez = (p + z * z / (2 * n)) / payda
        yayilim = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / payda
        return {
            "olcum": n, "dogru": dogru, "isabet_%": round(p * 100, 1),
            "guven_araligi_%": [round(max(0, merkez - yayilim) * 100, 1),
                                round(min(1, merkez + yayilim) * 100, 1)],
            "ortalama_anormal_getiri_%": round(r["ort"] or 0, 2),
            "yeterli_mi": n >= 20,
            "not": ("ORNEKLEM YETERSIZ — bu sayilardan sonuc cikarma"
                    if n < 20 else
                    "Komisyon sonrasi basabas ~%55 isabet gerektiriyor"),
        }

    def ajan_karnesi(self, gun: int = 180) -> list[dict]:
        """Hangi ajanin gorusu daha cok tutuyor — gerekce onekinden."""
        sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%d")
        out = []
        for ajan in ("teknik", "temel", "olay", "risk"):
            r = self.db.query(
                """SELECT COUNT(*) n, SUM(isabet) d FROM predictions
                   WHERE isabet IS NOT NULL AND olusma_ts >= ?
                     AND gerekce LIKE ?""", (sinir, f"[{ajan}]%"))[0]
            if r["n"]:
                out.append({"ajan": ajan, "olcum": r["n"],
                            "isabet_%": round((r["d"] or 0) / r["n"] * 100, 1)})
        return out
