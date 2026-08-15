"""
Alpha Vantage — Yahoo/SEC/Binance'in KAPATAMADIGI dort bosluk.

NEDEN MCP SUNUCUSU OLARAK DEGIL COLLECTOR OLARAK
------------------------------------------------
Alpha Vantage'in resmi bir MCP sunucusu var ve baglanabilirdik. Baglamadik:
ucretsiz anahtar GUNDE 25 ISTEK ve SANIYEDE 1 ISTEK ile sinirli (ikisi de
2026-08-15'te olculdu). Sohbet ajanimiz tek soruda 12 arac cagirabiliyor —
MCP olarak baglansaydi gunluk kota iki turda biter, gerisi sessizce hata
donerdi. Bu, "veri yok" yanlis beyaninin yeni bir bicimi olurdu.

Zamanlanmis collector olarak ayni kota rahat yetiyor: gunde ~10 istek.

HANGI BOSLUKLAR (hepsi gercek cagriyla dogrulandi)
--------------------------------------------------
1. DOVIZ KURU — en kritigi. Portfoy EUR, Yahoo serisi USD ve arada hicbir
   donusum yoktu. 17 pozisyonun 14'unde ~%15,7 sapma olculdu; bu sayi
   EUR/USD kurunun kendisi. Model "SMA50 = 206.52" derken hangi para
   biriminde oldugu bilinmiyordu.
2. AVRUPA KOTASYONU — ASML ve Adyen Amsterdam'da EUR kote. Yahoo bize ABD
   dolari serisini veriyordu. `ASML.AMS` cagrildi: 2026-08-14 kapanis
   1579.60 EUR; portfoydeki deger/adet de tam olarak 1579.60. Birebir.
3. HISSE SAYISI — piyasa degeri ve F/K icin sart. `OVERVIEW` Avrupa
   sembolunde BOS donuyor ama hisse sayisi sirket duzeyinde bir olgudur,
   borsaya ozgu degil: ABD kotasyonundan alinabiliyor (ASML -> 384.100.000).
4. KRIPTO HABERI — `NEWS_SENTIMENT` kripto sembollerini kapsiyor. ROSE'un
   veritabaninda SIFIR haberi vardi, dolayisiyla kriptoda olay-etki analizi
   hic yapilamiyordu.

KAYNAK KADEMESI
---------------
AV haber akisinda Cointelegraph, Motley Fool gibi yayincilar var. Mevcut
kademe kurali AYNEN uygulanir; kademe 3-4 kanit sayilmaz.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from ..research.sources import kademe
from ..storage.db import sha1
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

BASE = "https://www.alphavantage.co/query"

# Ucretsiz anahtarda saniyede 1 istek siniri var — olculdu, asilinca
# veri yerine "Information" mesaji donuyor ve SESSIZCE bos kalirdik.
ISTEK_ARASI_SN = 1.5

# Avrupa kotasyonu olan sembollerin AV karsiligi. Yahoo bunlarda dolar
# serisi veriyordu; buradan yerli para biriminde geliyor.
AMS_HARITASI = {
    "ASML": "ASML.AMS", "ADYEN": "ADYEN.AMS", "INGA": "INGA.AMS",
    "ABN": "ABN.AMS", "PRX": "PRX.AMS", "HEIA": "HEIA.AMS",
    "AD": "AD.AMS", "PHIA": "PHIA.AMS", "UNA": "UNA.AMS",
    "WKL": "WKL.AMS", "RAND": "RAND.AMS", "AKZA": "AKZA.AMS",
}


# PIYASA VEKILLERI — olay calismasinda piyasa modeli (alfa/beta) icin.
#
# Ham endeks sembolleri AV'de CALISMIYOR (olculdu: `^NDX` bos {} donuyor,
# `AEX.AMS` "Invalid API call"). Bu yuzden endeksi TAKIP EDEN ETF'ler
# vekil olarak kullaniliyor — getiri serisi pratikte endeksinkiyle ayni
# ve olay calismasinda gereken sey zaten getiri serisi.
#
# Her vekilin PARA BIRIMI onemli: piyasa modeli hisse getirisini piyasa
# getirisine regresyon eder; ikisi ayni para biriminde olmazsa beta
# kur hareketini de icine cekerdi.
PIYASA_VEKILLERI = {
    "QQQ":  {"av": "QQQ",       "ad": "Nasdaq 100 (QQQ vekil)",  "ccy": "USD"},
    "SPY":  {"av": "SPY",       "ad": "S&P 500 (SPY vekil)",     "ccy": "USD"},
    "IAEX": {"av": "IAEX.AMS",  "ad": "AEX (iShares IAEX vekil)", "ccy": "EUR"},
}


class AlphaVantageError(RuntimeError):
    pass


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AlphaVantageCollector(BaseCollector):
    name = "alphavantage"
    needs_browser = False

    def collect(self) -> CollectorResult:
        anahtar = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
        if not anahtar:
            return CollectorResult(self.name, "skipped", 0,
                                   "ALPHA_VANTAGE_API_KEY tanimli degil (.env)")

        import httpx
        self._anahtar = anahtar
        self._istek = 0
        butce = int(self.s.get("sources.alphavantage.daily_budget", 20))

        toplam, notlar = 0, []
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "finagent/1.0"}) as http:
            self._http = http
            for ad, fn in (("fx", self._fx),
                           ("endeks", self._endeksler),
                           ("avrupa_fiyat", self._avrupa_fiyatlar),
                           ("hisse_sayisi", self._hisse_sayisi),
                           ("kripto_haber", self._kripto_haber)):
                if self._istek >= butce:
                    notlar.append(f"{ad}: gunluk butce doldu ({butce})")
                    continue
                try:
                    n, not_ = fn()
                    toplam += n
                    notlar.append(f"{ad}: {n}" + (f" ({not_})" if not_ else ""))
                except Exception as e:                # noqa: BLE001
                    log.warning("[alphavantage] %s basarisiz: %s", ad, e)
                    notlar.append(f"{ad}: HATA {e}")

        durum = "ok" if toplam and not any("HATA" in x for x in notlar) else (
            "partial" if toplam else "error")
        return CollectorResult(self.name, durum, toplam,
                               f"{self._istek} istek · " + " · ".join(notlar))

    # ------------------------------------------------------------------
    def _cagir(self, **params) -> dict:
        """
        Tek AV cagrisi. Kota ve hiz sinirini burada yonetiyoruz.

        AV hata durumunda HTTP 200 + {"Information"/"Note"/"Error Message"}
        donuyor. Bunu sessizce bos veri saymak, kotanin bittigini fark
        etmeden "veri yok" demek olurdu — acikca hataya cevriliyor.
        """
        if self._istek:
            time.sleep(ISTEK_ARASI_SN)
        self._istek += 1
        r = self._http.get(BASE, params={**params, "apikey": self._anahtar})
        r.raise_for_status()
        d = r.json()
        for alan in ("Information", "Note", "Error Message"):
            if alan in d:
                raise AlphaVantageError(f"{alan}: {str(d[alan])[:160]}")
        return d

    # --- 1) doviz kuru ------------------------------------------------
    def _fx(self) -> tuple[int, str | None]:
        ciftler = self.s.get("sources.alphavantage.fx_pairs") or [
            ["EUR", "USD"], ["USD", "TRY"]]
        toplam = 0
        for base, quote in ciftler:
            d = self._cagir(function="FX_DAILY", from_symbol=base,
                            to_symbol=quote, outputsize="compact")
            seri = d.get("Time Series FX (Daily)") or {}
            satir = [(ts, float(v["4. close"])) for ts, v in seri.items()]
            if satir:
                toplam += self._fx_yaz(base, quote, satir)
        return toplam, None

    def _fx_yaz(self, base: str, quote: str, satir) -> int:
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO fx_rates (ts, base, quote, rate, source)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(ts, base, quote, source) DO UPDATE
                   SET rate=excluded.rate""",
                [(ts, base, quote, rate, self.name) for ts, rate in satir])
        return len(satir)

    # --- 2) piyasa vekilleri (endeks) ---------------------------------
    def _endeksler(self) -> tuple[int, str | None]:
        """
        Endeks vekili serilerini ceker. Olay calismasinin ORTALAMA-
        DUZELTILMIS modelden PIYASA MODELINE gecmesi bunlara bagli:
        piyasa hareketi ayiklanmadan anormal getiri olcumu zayif kaliyor
        (NVDA'da 5 olay gununun hicbiri |t|>2 cikmamisti).
        """
        istenen = self.s.get("sources.alphavantage.indices") or ["QQQ", "IAEX"]
        toplam, alinan = 0, []
        for kod in istenen:
            tanim = PIYASA_VEKILLERI.get(kod)
            if not tanim:
                continue
            iid = self.db.upsert_instrument(kod, "INDEX", tanim["ad"],
                                            "index", tanim["ccy"])
            d = self._cagir(function="TIME_SERIES_DAILY", symbol=tanim["av"],
                            outputsize="full")
            seri = d.get("Time Series (Daily)") or {}
            barlar = [{"ts": ts, "open": float(v["1. open"]),
                       "high": float(v["2. high"]), "low": float(v["3. low"]),
                       "close": float(v["4. close"]),
                       "volume": float(v["5. volume"])}
                      for ts, v in seri.items()]
            if barlar:
                toplam += self.db.upsert_prices(iid, barlar, self.name,
                                                currency=tanim["ccy"])
                alinan.append(f"{kod}({len(barlar)})")
        return toplam, ", ".join(alinan) or None

    # --- 3) Avrupa kotasyonlari ---------------------------------------
    def _avrupa_fiyatlar(self) -> tuple[int, str | None]:
        """
        Portfoydeki Amsterdam kotasyonlarini YERLI para biriminde ceker.

        Yahoo bunlarda dolar serisi veriyordu ve EUR portfoy degerleriyle
        yan yana kullanildigi icin her seviye yanlis para birimindeydi.
        """
        hedefler = []
        for h in self.db.research_targets():
            sem = (h["symbol"] or "").upper().split(".")[0]
            av = AMS_HARITASI.get(sem)
            if av:
                hedefler.append((h["id"], sem, av))
        if not hedefler:
            return 0, "Amsterdam kotasyonu bulunamadi"

        limit = int(self.s.get("sources.alphavantage.eu_per_run", 4))
        toplam, alinan = 0, []
        for iid, sem, av in hedefler[:limit]:
            d = self._cagir(function="TIME_SERIES_DAILY", symbol=av,
                            outputsize="compact")
            seri = d.get("Time Series (Daily)") or {}
            barlar = [{"ts": ts, "open": float(v["1. open"]),
                       "high": float(v["2. high"]), "low": float(v["3. low"]),
                       "close": float(v["4. close"]),
                       "volume": float(v["5. volume"])}
                      for ts, v in seri.items()]
            if barlar:
                toplam += self.db.upsert_prices(iid, barlar, self.name,
                                                currency="EUR")
                alinan.append(sem)
        atlanan = len(hedefler) - len(hedefler[:limit])
        return toplam, (f"{','.join(alinan)}"
                        + (f"; {atlanan} sembol siradaki calismaya" if atlanan else ""))

    # --- 3) hisse sayisi ----------------------------------------------
    def _hisse_sayisi(self) -> tuple[int, str | None]:
        """
        OVERVIEW ile hisse sayisi ve piyasa degeri.

        AVRUPA SEMBOLUNDE BOS DONUYOR (olculdu: ASML.AMS -> {}). Ama hisse
        sayisi SIRKET duzeyinde bir olgudur, borsaya ozgu degil; bu yuzden
        ABD kotasyonu (sade ticker) kullaniliyor ve boyle calisiyor
        (ASML -> 384.100.000).
        """
        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        adaylar = []
        for h in self.db.research_targets():
            k = kimlikler.get(h["symbol"])
            if not k or k["status"] != "dogrulandi":
                continue
            tic = k["sec_ticker"] or h["symbol"]
            if tic and "." not in tic:
                adaylar.append((h["id"], h["symbol"], tic))
        if not adaylar:
            return 0, "dogrulanmis ABD kotasyonu yok"

        # Her calismada birkac tanesi — kota gunluk 25 ile sinirli.
        limit = int(self.s.get("sources.alphavantage.overview_per_run", 3))
        eskiler = self._en_bayat(adaylar, limit)
        toplam, alinan = 0, []
        for iid, sem, tic in eskiler:
            d = self._cagir(function="OVERVIEW", symbol=tic)
            if not d.get("Symbol"):
                continue
            an = _bugun()
            satir = []
            for alan, kavram in (("SharesOutstanding", "HisseSayisi"),
                                 ("MarketCapitalization", "PiyasaDegeri")):
                try:
                    deger = float(d[alan])
                except (KeyError, TypeError, ValueError):
                    continue
                satir.append((iid, kavram, d.get("Currency") or "USD", None, an,
                              None, deger, "alphavantage", None, None, None,
                              an, tic))
            if satir:
                toplam += self.db.upsert_fundamentals(satir)
                alinan.append(sem)
        return toplam, ",".join(alinan) or None

    def _en_bayat(self, adaylar, limit):
        """En uzun suredir guncellenmeyenleri sec — kota adil dagilsin."""
        skor = []
        for iid, sem, tic in adaylar:
            r = self.db.query(
                """SELECT MAX(period_end) son FROM fundamentals
                   WHERE instrument_id=? AND form='alphavantage'""", (iid,))
            skor.append((r[0]["son"] or "", iid, sem, tic))
        skor.sort()
        return [(iid, sem, tic) for _, iid, sem, tic in skor[:limit]]

    # --- 4) kripto haberi ---------------------------------------------
    def _kapsam_yolu(self):
        return self.s.root / "data" / "av_kripto_kapsami.json"

    def _bilinen_kapsam(self) -> set[str]:
        """
        AV'nin haber indeksinde OLDUGUNU GORDUGUMUZ kripto sembolleri.

        Neden ogrenilen bir kume: AV kucuk kaplari indekslemiyor ve
        desteklenmeyen TEK bir ticker gonderince TUM istek
        "Invalid inputs" ile basarisiz oluyor (olculdu: ROSE/ENJ ile
        birlikte gonderilen major'lar da geldi). Yani listeyi once
        suzmek zorundayiz.

        Tohum: 2026-08-15'te gercek cevapta gorulen semboller.
        """
        import json as _j
        tohum = {"BTC", "ETH", "BNB", "ADA", "DOT", "AVAX", "SOL", "XRP"}
        try:
            return tohum | set(_j.loads(self._kapsam_yolu().read_text()))
        except (OSError, ValueError):
            return tohum

    def _kapsam_yaz(self, kume: set[str]) -> None:
        import json as _j
        try:
            self._kapsam_yolu().write_text(
                _j.dumps(sorted(kume)), encoding="utf-8")
        except OSError as e:                          # noqa: BLE001
            log.warning("[alphavantage] kapsam yazilamadi: %s", e)

    def _haberi_en_bayat(self, semboller, adet):
        """
        En uzun suredir haber alinmayan sembolleri sec.

        Gunluk kota sinirli oldugu icin her calismada hepsini
        sorgulayamiyoruz; donusumlu gidiyoruz ki hicbiri ac kalmasin.
        """
        skor = []
        for s in semboller:
            r = self.db.query(
                """SELECT MAX(published_at) son FROM news
                   WHERE source='alphavantage' AND (',' || symbols || ',') LIKE ?""",
                (f"%,{s},%",))
            skor.append((r[0]["son"] or "", s))
        skor.sort()
        return [s for _, s in skor[:adet]]

    def _kripto_haber(self) -> tuple[int, str | None]:
        semboller = [r["symbol"] for r in self.db.research_targets(kripto=True)]
        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        dogrulanmis = [s for s in semboller
                       if (kimlikler.get(s) or {}) and
                       kimlikler[s]["status"] == "dogrulandi"]
        if not dogrulanmis:
            return 0, "dogrulanmis kripto yok"

        kapsam = self._bilinen_kapsam()
        gecerli = [s for s in dogrulanmis if s in kapsam]
        kapsamsiz = [s for s in dogrulanmis if s not in kapsam]
        if not gecerli:
            return 0, ("AV haber indeksinde hicbiri yok: "
                       + ", ".join(kapsamsiz))

        # SEMBOL BASINA TEK ISTEK. AV'de coklu `tickers` VE anlamina
        # geliyor: 8 sembol birlikte gonderilince "hepsini AYNI ANDA gecen
        # makale" araniyor ve items=0 donuyor (olculdu). Uc sembolle 50
        # sonuc gelmesi yaniltmisti — kripto ozet yazilari uc majoru
        # birden aniyor, sekizi anmiyor.
        basina = int(self.s.get("sources.alphavantage.news_per_run", 2))
        sirali = self._haberi_en_bayat(gecerli, basina)
        akis, sorgulanan = [], []
        for sem in sirali:
            d = self._cagir(function="NEWS_SENTIMENT", tickers=f"CRYPTO:{sem}",
                            limit=50, sort="LATEST")
            sorgulanan.append(sem)
            akis.extend(d.get("feed") or [])
        if not akis:
            return 0, f"haber donmedi ({', '.join(sorgulanan)})"

        # Cevapta gorulen her kripto ticker'i kapsama eklenir: bir dahaki
        # calismada suzgecten gecer ve istek bosa gitmez.
        gorulen = {t.get("ticker", "").replace("CRYPTO:", "")
                   for x in akis for t in (x.get("ticker_sentiment") or [])
                   if str(t.get("ticker", "")).startswith("CRYPTO:")}
        if gorulen - kapsam:
            self._kapsam_yaz(kapsam | gorulen)

        rows = []
        for x in akis:
            yayinci = (x.get("source") or "").strip()
            ilgili = [t.get("ticker", "").replace("CRYPTO:", "")
                      for t in (x.get("ticker_sentiment") or [])]
            ilgili = [s for s in ilgili if s in dogrulanmis]
            if not ilgili:
                continue
            ham = x.get("time_published") or ""     # 20260815T093000
            try:
                ts = datetime.strptime(ham, "%Y%m%dT%H%M%S").strftime(
                    "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = None
            rows.append({
                "url": x.get("url"), "title": x.get("title"),
                "source": "alphavantage", "published_at": ts,
                "summary": (x.get("summary") or "")[:600],
                "symbols": ilgili, "publisher": yayinci,
                # Kademe MEVCUT kurala gore: AV'den gelmesi kaynagi
                # guvenilir yapmaz. Cointelegraph/Motley Fool burada da
                # kanit degildir.
                "tier": kademe(yayinci),
            })
        if not rows:
            return 0, "eslesen sembol yok"
        n = self.db.upsert_news(rows)
        kanit = sum(1 for r in rows if r["tier"] in (1, 2))
        not_ = f"{', '.join(sorgulanan)} · kanit sayilabilir {kanit}/{n}"
        if kapsamsiz:
            # ACIKCA soyluyoruz: sessizce atlanan sembol, kullaniciya
            # "haber yok" diye yansiyor ve bu yaniltici olur.
            not_ += f" · AV kapsaminda DEGIL: {', '.join(kapsamsiz)}"
        return n, not_
