"""
DETERMINISTIK TARAYICI — proaktif katmanin ilk asamasi. LLM YOK.

NEDEN LLM'SIZ
-------------
Evrende ~1.600 enstruman var. Hepsini modele sormak hem pahali hem
gereksiz: "SMA50'nin altina dustu mu" sorusunun cevabi bir karsilastirma,
muhakeme degil. Sayisal eleme burada yapilir; model yalnizca elemeden
gecen 3-5 adayi YORUMLAR. Bu, projenin bastan beri surdurdugu ayrimin
devami: deterministik katman hesaplar, LLM yorumlar.

Ikinci fayda: tarayici cikitisi TEKRARLANABILIR. Ayni veriyle ayni
sinyaller cikar, dolayisiyla gecmise donuk test edilebilir.

ESIKLER NEDEN BOYLE
-------------------
Her esik, olculen oynakliga GORE tanimli — sabit yuzde degil. %5 gunluk
hareket AEX'te olaganustu, ROSE'da siradan (gunluk oynaklik %5.69).
Sabit esik kullanmak, kripto tarafini surekli sinyal ureten bir gurultu
kaynagina cevirirdi.

SESSIZLIK GECERLI BIR CIKTIDIR
------------------------------
Her gun sinyal uretmek ZORUNDA olan sistem gurultu uretir. Esik
gecilmezse bos liste doner ve bildirim gonderilmez.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# --- esikler (oynakliga gore olceklenir) -----------------------------
SIGMA_HAREKET = 2.0      # gunluk getiri kac sigma ise "olagandisi"
HACIM_KATI = 2.0         # 20 gun ortalamasinin kac kati ise anomali
RSI_ASIRI_ALIM = 72
RSI_ASIRI_SATIM = 28
CAR_T_ESIGI = 1.5        # olay etkisinde bakmaya deger t
YOGUNLASMA_ESIGI = 25.0  # tek pozisyon portfoyun %'si
ZARAR_ESIGI = -20.0      # pozisyonda realize olmamis zarar %

MIN_BAR = 60             # bu kadar bar yoksa taranmaz


def _yuzde(a, b):
    return None if not b else (a / b - 1) * 100


class Tarayici:
    """
    Tum izlenen evreni tarar, esikleri gecen GOZLEMLERI dondurur.

    Doner: [{"instrument_id","sembol","tur","yon","guc","kanit","fiyat",...}]
    `guc` 0-1 arasi ve TURLER ARASI KARSILASTIRILABILIR olacak sekilde
    normalize edilir; hakem katmani adaylari buna gore siralar.
    """

    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    # ------------------------------------------------------------------
    def evren(self) -> list:
        """
        Taranacak enstrumanlar = portfoy ∪ izleme listesi ∪ endeks uyeleri
        ∪ LIKIDITESI YETERLI BIST kagitlari.

        Fiyat serisi olmayan enstruman taranmaz: hesaplanacak bir sey yok.

        LIKIDITE SUZGECI neden var: katalogda 729 BIST kagidi var ve
        hepsini taramak 200-400 sinyal uretirdi. Her gun "400 sey oldu"
        demek, hicbir sey dememekle ayni. Olculdu (625 kagit): medyan
        gunluk hacim 33M TL; 50M esigi daha likit yariyi aliyor.
        Esigin altindaki kagit gunde birkac islem goruyor — orada gunluk
        %5 hareket bilgi degil, tek bir emrin izidir.
        """
        esik = float(self.s.get("sources.isyatirim.min_hacim_tl", 50_000_000))
        return self.db.query("""
            SELECT DISTINCT i.id, i.symbol, i.name, i.venue
            FROM instruments i
            WHERE i.venue <> 'INDEX' AND EXISTS (
                SELECT 1 FROM prices p WHERE p.instrument_id = i.id)
              AND (
                i.id IN (SELECT instrument_id FROM positions)
                OR i.id IN (SELECT instrument_id FROM watchlist)
                OR i.id IN (SELECT instrument_id FROM index_members)
                OR i.id IN (
                    SELECT f.instrument_id FROM fundamentals f
                    WHERE f.concept = 'GunlukHacimTL' AND f.val >= ?))
            ORDER BY i.symbol""", (esik,))

    def tara(self) -> list[dict]:
        out: list[dict] = []
        for e in self.evren():
            try:
                out.extend(self._enstruman(e))
            except Exception as ex:                   # noqa: BLE001
                log.warning("[tarayici] %s taranamadi: %s", e["symbol"], ex)
        out.extend(self._portfoy_riskleri())
        out.sort(key=lambda x: -x["guc"])
        return out

    # ------------------------------------------------------------------
    def _enstruman(self, e) -> list[dict]:
        seri = self.db.fiyat_serisi(e["id"], 300)
        if len(seri) < MIN_BAR:
            return []
        kapanis = [r["close"] for r in seri if r["close"]]
        if len(kapanis) < MIN_BAR:
            return []
        ccy = seri[-1]["currency"]
        son = kapanis[-1]

        getiriler = [kapanis[i] / kapanis[i - 1] - 1
                     for i in range(1, len(kapanis)) if kapanis[i - 1]]
        ort = sum(getiriler) / len(getiriler)
        sd = math.sqrt(sum((g - ort) ** 2 for g in getiriler) / (len(getiriler) - 1))
        if sd <= 0:
            return []

        bulgular = []
        # ALINABILIR MI — bulgunun uzerinde TASINIR.
        #
        # venue='CRYPTO' coin'ler (HYPE, XMR, OKB, KAS...) ilk 100'de ama
        # Binance'te listelenmemis: kullanicinin bu coin'e verebilecegi bir
        # emir YOK. Yine de taraniyorlar, cunku sermayenin nereye dondugunu
        # gormek icin gerekliler — gormezsek rotasyonu genel zayiflik diye
        # okuruz. Ama bayrak tasinmazsa panel bunlara "al" der ve
        # uygulanamaz bir tavsiye uretir; bu, yanlis tavsiyeden farksizdir
        # cunku kullanicinin zamanini ayni sekilde harcar.
        ortak = {"instrument_id": e["id"], "sembol": e["symbol"],
                 "ad": e["name"], "venue": e["venue"], "fiyat": son,
                 "para_birimi": ccy, "gunluk_oynaklik_%": round(sd * 100, 2),
                 "alinabilir": e["venue"] != "CRYPTO"}

        # --- 1) olagandisi gunluk hareket (oynakliga GORE) ------------
        g1 = getiriler[-1]
        z = g1 / sd
        if abs(z) >= SIGMA_HAREKET:
            bulgular.append({**ortak, "tur": "olagandisi_hareket",
                             "yon": "yukari" if z > 0 else "asagi",
                             "guc": min(1.0, abs(z) / 4),
                             "kanit": {"gunluk_getiri_%": round(g1 * 100, 2),
                                       "sigma": round(z, 2),
                                       "not": "esik oynakliga gore, sabit yuzde degil"}})

        # --- 2) hacim anomalisi ---------------------------------------
        hacim = [r["volume"] or 0 for r in seri]
        if len(hacim) >= 21 and sum(hacim[-21:-1]) > 0:
            ortalama = sum(hacim[-21:-1]) / 20
            kat = hacim[-1] / ortalama if ortalama else 0
            if kat >= HACIM_KATI:
                bulgular.append({**ortak, "tur": "hacim_anomalisi",
                                 "yon": "yukari" if g1 > 0 else "asagi",
                                 "guc": min(1.0, kat / 5),
                                 "kanit": {"hacim_kati": round(kat, 2),
                                           "gunluk_getiri_%": round(g1 * 100, 2)}})

        # --- 3) hareketli ortalama kirilimi ---------------------------
        if len(kapanis) >= 200:
            sma50 = sum(kapanis[-50:]) / 50
            sma200 = sum(kapanis[-200:]) / 200
            onceki50 = sum(kapanis[-51:-1]) / 50
            kirdi = (kapanis[-2] < onceki50 <= son) or (kapanis[-2] > onceki50 >= son)
            if kirdi:
                yukari = son > sma50
                bulgular.append({**ortak, "tur": "sma50_kirilimi",
                                 "yon": "yukari" if yukari else "asagi",
                                 "guc": 0.5 + (0.2 if (son > sma200) == yukari else 0),
                                 "kanit": {"sma50": round(sma50, 6),
                                           "sma200": round(sma200, 6),
                                           "kapanis": son,
                                           "not": "SMA200 ile ayni yonde ise guc yuksek"}})

        # --- 4) RSI ucu ------------------------------------------------
        rsi = self._gosterge(seri, "rsi14")
        if rsi is not None and (rsi >= RSI_ASIRI_ALIM or rsi <= RSI_ASIRI_SATIM):
            bulgular.append({**ortak, "tur": "rsi_ucu",
                             "yon": "asagi" if rsi >= RSI_ASIRI_ALIM else "yukari",
                             "guc": min(1.0, abs(rsi - 50) / 40),
                             "kanit": {"rsi14": round(rsi, 1),
                                       "not": "TEK BASINA sinyal degil; guclu trendde "
                                              "RSI haftalarca ucta kalabilir"}})

        # --- 5) olay etkisi -------------------------------------------
        try:
            from ..analysis.events import haber_etkileri
            for etki in haber_etkileri(self.db, e["id"], e["symbol"], limit=2):
                t = etki.get("t_istatistigi")
                if t is None or abs(t) < CAR_T_ESIGI:
                    continue
                bulgular.append({**ortak, "tur": "olay_etkisi",
                                 "yon": "yukari" if etki["car_%"] > 0 else "asagi",
                                 "guc": min(1.0, abs(t) / 3),
                                 "kanit": {"olay_tarihi": etki["olay_tarihi"],
                                           "car_%": etki["car_%"], "t": t,
                                           "model": etki["model"],
                                           "olay_sayisi": len(etki.get("olaylar", []))}})
        except Exception as ex:                       # noqa: BLE001
            log.debug("olay etkisi atlandi (%s): %s", e["symbol"], ex)

        return bulgular

    # ------------------------------------------------------------------
    def _portfoy_riskleri(self) -> list[dict]:
        """
        Portfoy duzeyi riskler. Bunlar "firsat" degil ama proaktif
        katmanin en yuksek beklenen degerli ciktisi: yogunlasma, tek bir
        sinyalin telafi edemeyecegi bir risktir.
        """
        out = []
        for hesap in {r["account"] for r in self.db.query(
                "SELECT DISTINCT account FROM positions")}:
            poz = self.db.latest_positions(hesap)
            toplam = sum((p["market_value"] or 0) for p in poz)
            if not toplam:
                continue
            for p in poz:
                deger = p["market_value"] or 0
                agirlik = deger / toplam * 100
                if agirlik >= YOGUNLASMA_ESIGI:
                    out.append({
                        "instrument_id": p["instrument_id"], "sembol": p["symbol"],
                        "ad": p["name"], "venue": hesap.upper(),
                        "fiyat": p["last_price"], "para_birimi": p["currency"],
                        # Portfoydeki kagit TANIM GEREGI alinabilir — zaten
                        # senin. Bayrak burada da yazilmali: eksik birakilinca
                        # `alinabilir` None oluyor ve tuketen taraf bunu
                        # "alinamaz" diye okuyor (olculdu: ROSE ve ASML
                        # referans sayildi).
                        "alinabilir": True,
                        "tur": "yogunlasma", "yon": "notr",
                        "guc": min(1.0, agirlik / 50),
                        "kanit": {"hesap": hesap, "agirlik_%": round(agirlik, 1),
                                  "deger": deger, "hesap_toplami": round(toplam, 2),
                                  "esik_%": YOGUNLASMA_ESIGI}})
                kz = p["pnl_pct"]
                if kz is not None and kz <= ZARAR_ESIGI:
                    out.append({
                        "instrument_id": p["instrument_id"], "sembol": p["symbol"],
                        "ad": p["name"], "venue": hesap.upper(),
                        "fiyat": p["last_price"], "para_birimi": p["currency"],
                        # Portfoydeki kagit TANIM GEREGI alinabilir — zaten
                        # senin. Bayrak burada da yazilmali: eksik birakilinca
                        # `alinabilir` None oluyor ve tuketen taraf bunu
                        # "alinamaz" diye okuyor (olculdu: ROSE ve ASML
                        # referans sayildi).
                        "alinabilir": True,
                        "tur": "acik_zarar", "yon": "notr",
                        "guc": min(1.0, abs(kz) / 50),
                        "kanit": {"hesap": hesap, "kz_%": kz, "agirlik_%": round(agirlik, 1),
                                  "not": "Zarar tek basina satis gerekcesi DEGIL; "
                                         "tez hala gecerli mi diye bakilmali"}})
        return out

    # ------------------------------------------------------------------
    def _gosterge(self, seri, alan):
        """
        Gostergeyi PROJENIN TEK motorundan alir.

        Tarayici once kendi RSI'ini hesapliyordu (basit ortalama). Ajan
        paneli bunu yakaladi: uc ajan bagimsiz olarak tarayicinin RSI'inin
        sistematik YUKSEK oldugunu bildirdi ve olculdu — MSFT 84.8 vs
        70.9, NVDA 75.4 vs 63.0. Sebep, RSI'in Wilder yumusatmasiyla
        degil duz ortalamayla hesaplanmasiydi.

        Ayni gostergenin iki tanimi olmasi, tarayicinin `teknik` aracinin
        CELISECEGI bir esikte sinyal uretmesi demekti. Artik tek tanim.
        """
        try:
            import pandas as pd
            from ..analysis import compute_indicators, technical_snapshot
            df = pd.DataFrame([dict(r) for r in seri]).sort_values("ts")
            ozet = technical_snapshot("", compute_indicators(
                df, self.s.get("analysis.indicators", {})))
            return ozet.get(alan)
        except Exception as e:                        # noqa: BLE001
            log.warning("[tarayici] gosterge hesaplanamadi: %s", e)
            return None

    # ------------------------------------------------------------------
    def kaydet(self, bulgular: list[dict]) -> int:
        """Sinyalleri diske yazar (idempotent: ayni gun ayni tur tek kayit)."""
        if not bulgular:
            return 0
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO signals
                   (olusma_ts, instrument_id, tur, yon, guc, kanit, fiyat, para_birimi)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(olusma_ts, instrument_id, tur) DO UPDATE SET
                     yon=excluded.yon, guc=excluded.guc, kanit=excluded.kanit,
                     fiyat=excluded.fiyat""",
                [(ts, b["instrument_id"], b["tur"], b["yon"], b["guc"],
                  json.dumps(b["kanit"], ensure_ascii=False), b.get("fiyat"),
                  b.get("para_birimi")) for b in bulgular])
        return len(bulgular)
