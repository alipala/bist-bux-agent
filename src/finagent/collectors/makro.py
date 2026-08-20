"""
MAKRO / KAPANIS PANELI — endeks, emtia, kur, faiz.

NEDEN VAR
---------
2026-08-17 gun sonu raporunda dunya gundemi, Turkiye gundemi ve emtia
hakkinda tek satir yoktu. Sebebi prompt degildi: `venue='INDEX'` altinda
yalnizca UC enstruman vardi (QQQ, AEX, XU100). Altin, gumus, petrol,
dolar endeksi, VIX, ABD 10 yillik — hicbiri veritabaninda YOKTU. Bir
piyasa notu bunlar olmadan yazilamaz; model de haklı olarak yazmadi.

SEMBOLLER GERCEK CAGRIYLA DOGRULANDI (2026-08-17). Belgeye guvenilmedi:
  * `XAUUSD=X` ve `XAGUSD=X` Yahoo'da YOK ("symbol may be delisted") —
    spot altin/gumus icin FX tarzi sembol calismiyor.
  * `GC=F` / `SI=F` (vadeli) calisiyor.
  * `TTF=F` (Avrupa dogalgazi) calisiyor ve EUR cinsinden geliyor.

ALTIN ICIN IKI FIYAT, BILEREK
-----------------------------
Vadeli ile spot AYNI SEY DEGIL: olculdu, GC=F (Aralik-26) 4475,50 USD
iken spot destekli PAXG 4368,81 USD — %2,4 fark (tasima maliyeti).
Turkiye'de "gram altin" SPOT'tan turer. Bu yuzden:
  ALTIN_ONS    spot vekili (PAXG/USDT, Binance) — gram hesabinin girdisi
  ALTIN_VADELI GC=F, uluslararasi likit gosterge
  ALTIN_GRAM   ALTIN_ONS x USDTRY / 31,1035 — TURETILMIS
Ikisini tek satirda birlestirmek, TTM dersinin aynisini tekrarlamak
olurdu: karismayi yapisal olarak imkansiz kil, bant genisletme.

`venue='MAKRO'`: `screener.evren()` yalnizca 'INDEX'i disliyordu; MAKRO
da tarama disinda tutuluyor. Bunlar sinyal uretilecek kagitlar degil,
BAGLAM enstrumanlari — "TUPRS yukselmis" cumlesinin yaninda Brent'in ne
yaptigi durmali, ama Brent icin "al/sat" onerisi uretilmemeli.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult
from .prices import yahoo_gunluk

log = logging.getLogger(__name__)

TROY_ONS_GRAM = 31.1034768

# kod -> (yahoo sembolu, ad, grup)
PANEL: dict[str, tuple[str, str, str]] = {
    # --- endeksler ---
    "SPX":     ("^GSPC",     "S&P 500",                        "endeks"),
    "NDX":     ("^NDX",      "Nasdaq 100",                     "endeks"),
    "DAX":     ("^GDAXI",    "DAX",                            "endeks"),
    "SX5E":    ("^STOXX50E", "Euro Stoxx 50",                  "endeks"),
    # --- emtia ---
    "ALTIN_VADELI": ("GC=F", "Altin (vadeli, ons)",            "emtia"),
    "GUMUS":   ("SI=F",      "Gumus (vadeli, ons)",            "emtia"),
    "BRENT":   ("BZ=F",      "Brent petrol (varil)",           "emtia"),
    "WTI":     ("CL=F",      "WTI petrol (varil)",             "emtia"),
    "BAKIR":   ("HG=F",      "Bakir (libre)",                  "emtia"),
    "TTF":     ("TTF=F",     "Avrupa dogalgaz (TTF)",          "emtia"),
    # --- kur / faiz / risk istahi ---
    "USDTRY":  ("USDTRY=X",  "USD/TRY",                        "kur"),
    "EURTRY":  ("EURTRY=X",  "EUR/TRY",                        "kur"),
    "EURUSD":  ("EURUSD=X",  "EUR/USD",                        "kur"),
    "DXY":     ("DX-Y.NYB",  "Dolar endeksi (DXY)",            "kur"),
    "US10Y":   ("^TNX",      "ABD 10 yillik tahvil faizi (%)", "faiz"),
    "VIX":     ("^VIX",      "VIX oynaklik endeksi",           "risk"),
}

# Yahoo'dan gelen kurlar `fx_rates`e de yazilir. Alpha Vantage'in gunluk
# 25 istek kotasi var ve FX orada sik sik bayat kaliyor (canli portfoy
# degerlemesinde EUR/USD 3 gun eskiydi); Yahoo'da kota yok.
FX_YAZ = {"USDTRY": ("USD", "TRY"), "EURUSD": ("EUR", "USD"),
          "EURTRY": ("EUR", "TRY")}

# Spot altin vekili — Binance'te zaten toplanan, fiziki altinla
# desteklenen tokenlar. Sirayla denenir.
SPOT_ALTIN_VEKILLERI = ("PAXG", "XAUT")


class MakroCollector(BaseCollector):
    name = "makro"
    # TARAYICI ARTIK GEREKMIYOR — `yahoo_gunluk` yfinance kullaniyor.
    # Bu collector tarayiciyi SIRF Yahoo'nun 429'unu asmak icin aciyordu.
    needs_browser = False

    def collect(self) -> CollectorResult:
        aralik = self.s.get("sources.makro.range", "1y")
        istenen = self.s.get("sources.makro.panel") or list(PANEL)
        bilinmeyen = [k for k in istenen if k not in PANEL]
        istenen = [k for k in istenen if k in PANEL]

        toplam, basarisiz = 0, []
        for kod in istenen:
            yahoo, ad, grup = PANEL[kod]
            iid = self.db.upsert_instrument(kod, "MAKRO", name=ad,
                                            asset_type=grup)
            try:
                n = yahoo_gunluk(self.db, yahoo, iid, aralik)
                toplam += n
                if n:
                    if kod in FX_YAZ:
                        self._fx_yaz(kod, iid)
                else:
                    basarisiz.append(f"{kod} (bos)")
            except Exception as e:                   # noqa: BLE001
                log.warning("[makro] %s (%s) alinamadi: %s", kod, yahoo, e)
                basarisiz.append(kod)

        turetilen, turetme_notu = self._gram_altin()
        toplam += turetilen

        notlar = []
        if bilinmeyen:
            notlar.append("panelde tanimsiz kod: " + ", ".join(bilinmeyen))
        if basarisiz:
            notlar.append("alinamadi: " + ", ".join(basarisiz[:8]))
        if turetme_notu:
            notlar.append(turetme_notu)

        durum = "ok" if not (basarisiz or bilinmeyen) else ("partial" if toplam else "error")
        return CollectorResult(self.name, durum, toplam,
                               " · ".join(notlar) if notlar else None)

    # ------------------------------------------------------------------
    def _fx_yaz(self, kod: str, instrument_id: int) -> None:
        """Kur serisini `fx_rates`e de yazar — portfoy cevrimi oradan okuyor."""
        base, quote = FX_YAZ[kod]
        satir = [(r["ts"], r["close"]) for r in
                 self.db.query("""SELECT ts, close FROM prices
                                  WHERE instrument_id=? AND source='yahoo'
                                    AND close IS NOT NULL
                                  ORDER BY ts DESC LIMIT 400""", (instrument_id,))]
        if not satir:
            return
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO fx_rates (ts, base, quote, rate, source)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(ts, base, quote, source) DO UPDATE
                   SET rate=excluded.rate""",
                [(ts, base, quote, rate, "yahoo") for ts, rate in satir])

    # ------------------------------------------------------------------
    def _gram_altin(self) -> tuple[int, str | None]:
        """
        ALTIN_ONS (spot vekili) ve ALTIN_GRAM (TRY) serilerini turetir.

        TURETILMIS SERI AYRI KAVRAM ADIYLA saklanir ve kaynagi
        'turetilmis' yazilir. Ayni seride vadeli ile spotu yan yana
        koymak, `NetKarTTM` dersinin aynisini tekrarlamak olurdu.

        UYARI RAPORA TASINIR: bu "uluslararasi parite gram"dir. Turkiye
        ic piyasasinda gram altin bunun UZERINE primle islem gorur;
        ikisini esit saymak sistematik hata olur.
        """
        vekil = None
        for sembol in SPOT_ALTIN_VEKILLERI:
            r = self.db.query(
                "SELECT id FROM instruments WHERE symbol=? AND venue='BINANCE'",
                (sembol,))
            if r and self.db.fiyat_serisi(r[0]["id"], 5):
                vekil = (sembol, r[0]["id"])
                break
        if not vekil:
            return 0, ("gram altin turetilemedi: spot vekili yok "
                       f"({'/'.join(SPOT_ALTIN_VEKILLERI)} serisi bulunamadi)")

        sembol, vekil_id = vekil
        ons = {r["ts"]: r["close"] for r in self.db.fiyat_serisi(vekil_id, 400)
               if r["close"]}
        if not ons:
            return 0, "gram altin turetilemedi: spot vekili serisi bos"

        # ONS serisi: spot vekilinin kendisi, MAKRO altinda ayri kimlikle.
        ons_id = self.db.upsert_instrument(
            "ALTIN_ONS", "MAKRO", name=f"Altin spot vekili ({sembol}, ons)",
            asset_type="emtia", currency="USD")
        n = self.db.upsert_prices(
            ons_id, [{"ts": t, "close": v, "open": None, "high": None,
                      "low": None, "volume": None} for t, v in ons.items()],
            "turetilmis", currency="USD")

        # GRAM: ons x USDTRY / 31,1035. Kur AYNI GUNUN kuru olmali —
        # baska gunun kuruyla carpmak fiyat hareketiyle kur hareketini
        # birbirine karistirir.
        kur = {r["ts"]: r["rate"] for r in self.db.query(
            "SELECT ts, rate FROM fx_rates WHERE base='USD' AND quote='TRY'")}
        gram = [{"ts": t, "close": v * kur[t] / TROY_ONS_GRAM, "open": None,
                 "high": None, "low": None, "volume": None}
                for t, v in ons.items() if t in kur]
        if not gram:
            return n, "gram altin turetilemedi: USD/TRY serisi ortusmuyor"

        gram_id = self.db.upsert_instrument(
            "ALTIN_GRAM", "MAKRO",
            name="Gram altin paritesi (TRY) — uluslararasi, yurtici prim HARIC",
            asset_type="emtia", currency="TRY")
        n += self.db.upsert_prices(gram_id, gram, "turetilmis", currency="TRY")
        return n, (f"gram altin {sembol} spot vekilinden turetildi "
                   f"({len(gram)} gun); yurtici prim DAHIL DEGIL")
