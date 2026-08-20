"""
Fiyat gecmisi (OHLCV) — teknik analizin on kosulu.

NEDEN TARAYICI
--------------
Ayni uc nokta httpx ile 429 donuyor, tarayicidan 200 donuyor. Sebep kimlik
DEGIL: normal bir ziyaret sirasinda olusan cerezler ve tarayici basliklari
eksik oldugu icin engelleniyor. Kullanici girisi GEREKMIYOR — bu dogrulandi;
giris bilgisi istemeye de gerek yok.

(Stooq alternatifi denendi: proof-of-work engeli, ardindan "Access denied".)

SEMBOL ESLESTIRMESI
-------------------
Yahoo ABD kotasyonlarini sade ticker ile ("NVDA"), Avrupa kotasyonlarini
borsa sonekiyle ("ADYEN.AS", "AIR.PA") tanir. Kimligi SEC'de dogrulanmis
enstrumanlarda sec_ticker kullanilir (ABD kotasyonu kesin); digerlerinde
katalog sembolu zaten sonekli gelir.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .base import BaseCollector, CollectorResult


def _bugun_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()

log = logging.getLogger(__name__)

CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         "{sym}?range={aralik}&interval=1d")

# PIYASA VEKILLERI — olay calismasindaki piyasa modeli (alfa/beta) icin.
#
# Neden Yahoo, Alpha Vantage degil: AV'de ham endeks sembolu calismiyor
# (`^NDX` bos donuyor) ve ucretsiz kota gunde 25 istek — endeks serisi
# her gun tazelenmesi gereken uzun bir seri, kotayi bosa yer. Yahoo'da
# kota yok ve HISSE SERILERIMIZ DE Yahoo'dan geliyor: piyasa modeli iki
# seriyi ayni gunlerde eslestirmek zorunda, ayni kaynak olmasi hizalamayi
# garantiler.
ENDEKSLER = {
    "QQQ":  ("QQQ",   "Nasdaq 100 (QQQ vekil)",         "USD"),
    "SPX":  ("^GSPC", "S&P 500",                        "USD"),
    "AEX":  ("^AEX",  "AEX",                            "EUR"),
}
ISINMA = "https://finance.yahoo.com/quote/NVDA"     # cerez olustursun diye


class PriceCollector(BaseCollector):
    name = "prices"
    needs_browser = True

    def collect(self) -> CollectorResult:
        hedefler = self.db.research_targets()
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0, "arastirma hedefi yok")

        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        aralik = self.s.get("sources.prices.range", "2y")

        toplam, basarisiz = 0, []
        pg = self.browser.context.new_page()
        try:
            pg.goto(ISINMA, wait_until="domcontentloaded", timeout=40000)
            pg.wait_for_timeout(2500)

            for h in hedefler:
                # BASKA COLLECTOR'IN ISI ARIZA DEGILDIR.
                #
                # BIST kagitlarinin serisini `isyatirim` cekiyor ve Yahoo
                # sade BIST sembolunu zaten REDDEDIYOR (bkz.
                # `_yahoo_sembolu`: soneksiz sembol baska sirkete denk
                # gelebilir). Ama bu red her kosuda "alinamadi" diye
                # raporlaniyordu: olculdu (2026-08-20), `prices` 23
                # kosunun 19'unda SIRF bu yuzden `partial` dondu ve
                # "ALTIN_GRAM, DEVA, KGYO, MASFN, QUICK, TERA, TRALT
                # (sembol yok)" satiri her seferinde tekrarladi.
                # Kalici bir sahte alarm, GERCEK arizayi gomer.
                # MAKRO da ayni: ALTIN_GRAM paritesini `makro` cekiyor,
                # Yahoo'da o sembol zaten yok.
                if (h["venue"] or "").upper() in ("BIST", "MAKRO"):
                    continue
                yahoo = self._yahoo_sembolu(h, kimlikler.get(h["symbol"]))
                if not yahoo:
                    basarisiz.append(f"{h['symbol']} (sembol yok)")
                    continue
                try:
                    n = self._cek(pg, yahoo, h["id"], aralik)
                    toplam += n
                    if not n:
                        basarisiz.append(f"{h['symbol']} (bos)")
                except Exception as e:          # noqa: BLE001
                    log.warning("[prices] %s alinamadi: %s", h["symbol"], e)
                    basarisiz.append(h["symbol"])
            # Piyasa vekilleri AYNI oturumda, sayfa KAPANMADAN once.
            for kod, n in self._endeksleri_cek(pg, aralik).items():
                toplam += n
                if not n:
                    basarisiz.append(f"endeks:{kod}")
            toplam += self._borsa_kotasyonlari(pg, hedefler, aralik)
        finally:
            pg.close()
        durum = "partial" if basarisiz else "ok"
        return CollectorResult(self.name, durum if toplam else "error", toplam,
                               ("alinamadi: " + ", ".join(basarisiz[:8]))
                               if basarisiz else None)

    def _borsa_kotasyonlari(self, pg, hedefler, aralik: str) -> int:
        """
        Pozisyonun PARA BIRIMINDEKI yerel borsa kotasyonunu IKINCI kaynak
        olarak ceker (ASML -> ASML.AS, EUR).

        NEDEN GEREKLI: `fiyat_kaynagi()` pozisyonun para birimiyle
        eslesen kaynagi seciyor ve EUR serisi Alpha Vantage'dan geliyordu.
        AV'nin ucretsiz kotasi (gunde 25) her gece tukeniyor; olculdu
        (2026-08-17): ASML'nin EUR serisi 14 Agustos'ta kalmis, yani
        portfoyun %41'inin GUNLUK HAREKETI raporda hic gorunmuyordu.
        Yahoo ayni kotasyonu kotasiz ve TAZE veriyor: ASML.AS 1.621,20 EUR
        vs AV'nin bayat 1.579,60'i — gorunmeyen hareket %2,6.

        KIMLIK KONTROLU ZORUNLU. Sonek eklemek bir TAHMINDIR ve bu
        projede tahmin edilen sembol iki kez baska sirkete denk geldi
        (AVTX -> Avalo Therapeutics, RBOT -> Vicarious Surgical).

        AD VE PARA BIRIMI KONTROLU YETMEDI — SAHADA OLCULDU. Ilk surumde
        yalnizca bu ikisi araniyordu ve `TSLA.AS` ile `MSFT.AS` her iki
        kontrolu de GECTI: Yahoo ikisini de EUR cinsinden ve "TESLA" /
        "MICROSOFT" adiyla donduruyor. Ama fiyatlar 7,22 EUR ve 8,57 EUR
        — bunlar hisse degil, Amsterdam'da islem goren SERTIFIKA/tracker
        urunleri. Sonuc: TSLA pozisyonu 144,88 EUR yerine 3,54 EUR
        degerlendi. Yanlis fiyat eksik fiyattan tehlikelidir; her sey
        hesaplanir ve hepsi yanlis cikar.

        Bu yuzden DORDUNCU kosul: FIYAT MAKULLUK KONTROLU. Aday
        kotasyonun son kapanisi, var olan referans seriye (ABD
        kotasyonu) kurla cevrilip karsilastirilir; sapma %10'u asarsa
        AYNI ENSTRUMAN DEGILDIR ve yazilmaz. Referans seri yoksa da
        yazilmaz — dogrulanamayan bir tahmini kabul etmektense o
        enstrumanda EUR serisi olmasin.
        """
        from ..storage.db import _ad_anahtari

        sonekler = self.s.get("sources.prices.borsa_sonekleri") or {"EUR": ".AS"}
        yazilan = 0
        for h in hedefler:
            poz = self.db.query(
                """SELECT currency FROM positions WHERE instrument_id = ?
                   ORDER BY snapshot_ts DESC LIMIT 1""", (h["id"],))
            ccy = (poz[0]["currency"] if poz else None) or ""
            sonek = sonekler.get(ccy.upper())
            sembol = (h["symbol"] or "").upper()
            if not sonek or "." in sembol or sembol.startswith("~"):
                continue
            # Bu para biriminde ZATEN taze bir seri varsa ikinci kez cekme.
            var = self.db.query(
                """SELECT MAX(p.ts) son FROM prices p
                   WHERE p.instrument_id = ? AND p.currency = ?""", (h["id"], ccy))
            if var and var[0]["son"] and var[0]["son"] >= _bugun_iso():
                continue
            try:
                n = self._kotasyon_yaz(pg, f"{sembol}{sonek}", h, ccy, aralik,
                                       _ad_anahtari)
                yazilan += n
            except Exception as e:                      # noqa: BLE001
                log.debug("[prices] %s%s kotasyonu alinamadi: %s", sembol, sonek, e)
        return yazilan

    def _kotasyon_yaz(self, pg, yahoo: str, hedef, ccy: str, aralik: str,
                      ad_anahtari) -> int:
        pg.goto(CHART.format(sym=yahoo, aralik=aralik),
                wait_until="domcontentloaded", timeout=30000)
        data = json.loads(pg.inner_text("body"))
        sonuc = (data.get("chart") or {}).get("result") or []
        if not sonuc:
            return 0
        meta = (sonuc[0].get("meta") or {})
        if (meta.get("currency") or "").upper() != ccy.upper():
            return 0
        bizim, onlarin = ad_anahtari(hedef["name"]), ad_anahtari(meta.get("shortName"))
        if not (bizim and onlarin and (bizim in onlarin or onlarin in bizim)):
            log.info("[prices] %s atlandi: ad eslesmedi (bizde %r, Yahoo %r)",
                     yahoo, hedef["name"], meta.get("shortName"))
            return 0
        if not self._fiyat_makul(hedef["id"], meta, ccy):
            return 0
        # AYRI KAYNAK ADI ZORUNLU — bkz. yahoo_gunluk docstring.
        return yahoo_gunluk(pg, self.db, yahoo, hedef["id"], aralik,
                            currency=ccy, kaynak="yahoo_borsa")

    # Referanstan izin verilen en buyuk sapma. %10 secildi: iki borsanin
    # kapanis saatleri farkli (Amsterdam 17:30, New York 22:00 TRT) ve
    # aradaki gun ici hareket + kur farki birkac yuzdeyi bulabilir.
    # Sertifika/tracker urunleri ise KAT KAT farkli fiyatlanir (TSLA.AS
    # 7,22 EUR vs TSLA 339,30 USD — 40 kat), yani esik hassas olmak
    # zorunda degil, yalnizca BUYUKLUK MERTEBESINI ayirmali.
    SAPMA_ESIGI = 0.10

    def _fiyat_makul(self, instrument_id: int, meta: dict, ccy: str) -> bool:
        """Aday kotasyonun fiyati, var olan referans seriyle tutuyor mu?"""
        aday = meta.get("regularMarketPrice") or meta.get("previousClose")
        if not aday:
            return False
        ref = self.db.query(
            """SELECT close, currency FROM prices
               WHERE instrument_id = ? AND currency IS NOT NULL AND currency <> ?
               ORDER BY ts DESC LIMIT 1""", (instrument_id, ccy.upper()))
        if not ref or not ref[0]["close"]:
            log.info("[prices] %s: karsilastirilacak referans seri yok, "
                     "kotasyon KABUL EDILMEDI", meta.get("symbol"))
            return False
        kur = self.db.fx_kuru(ref[0]["currency"], ccy)
        if not kur:
            log.info("[prices] %s: %s->%s kuru yok, kotasyon KABUL EDILMEDI",
                     meta.get("symbol"), ref[0]["currency"], ccy)
            return False
        beklenen = ref[0]["close"] * kur["rate"]
        if not beklenen:
            return False
        sapma = abs(aday / beklenen - 1)
        if sapma > self.SAPMA_ESIGI:
            log.warning("[prices] %s ATLANDI: fiyat tutmuyor — aday %.4f %s, "
                        "referanstan beklenen %.4f %s (sapma %%%.1f). Bu buyuk "
                        "olasilikla hisse degil sertifika/tracker.",
                        meta.get("symbol"), aday, ccy, beklenen, ccy, sapma * 100)
            return False
        return True

    def _endeksleri_cek(self, pg, aralik: str) -> dict:
        istenen = self.s.get("sources.prices.indices") or ["QQQ", "AEX"]
        out = {}
        for kod in istenen:
            tanim = ENDEKSLER.get(kod)
            if not tanim:
                continue
            yahoo, ad, ccy = tanim
            iid = self.db.upsert_instrument(kod, "INDEX", ad, "index", ccy)
            try:
                out[kod] = self._cek(pg, yahoo, iid, aralik, currency=ccy)
            except Exception as e:              # noqa: BLE001
                log.warning("[prices] endeks %s alinamadi: %s", kod, e)
                out[kod] = 0
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _yahoo_sembolu(hedef, kimlik) -> str | None:
        if hedef["asset_type"] == "cash" or hedef["symbol"] == "CASH":
            return None

        # KIMLIGI COZULEMEYEN ENSTRUMANDAN FIYAT CEKILMEZ.
        # Sembol bir TAHMINDIR ve baska sirkete ait olabilir: "Avantium"
        # icin tahmin edilen AVTX, Yahoo'da "Avalo Therapeutics" (ABD
        # biyotek). Ilk denemede tam bu oldu — 500 bar cekildi ve hepsi
        # yanlis sirketin fiyatiydi (3.59-22.87 USD; Avantium Amsterdam'da
        # ~2-4 EUR). Yanlis fiyat, eksik fiyattan cok daha tehlikeli:
        # RSI, SMA, getiri — hepsi hesaplanir ve hepsi yanlistir.
        if kimlik is not None and kimlik["status"] == "eslesmedi":
            return None

        # SEC'de dogrulanmis -> ABD kotasyonu var, sade ticker calisir.
        if kimlik is not None and kimlik["status"] in ("dogrulandi", "elle") \
                and kimlik["sec_ticker"]:
            return kimlik["sec_ticker"]

        sembol = (hedef["symbol"] or "").upper()
        # Ekran goruntusunden gelen gecici anahtarlar (~ONEKLI) kullanilamaz.
        if sembol.startswith("~"):
            return None

        # SADE SEMBOL TEK BASINA GUVENLI DEGIL — ikinci kez isbatlandi.
        # AVTX'ten sonra RBOT: kimligi dogru sekilde "fon" (iShares
        # Automation & Robotics) isaretliydi ama kod buraya dusup ham
        # sembolu Yahoo'ya verdi. Yahoo'da RBOT = Vicarious Surgical,
        # 6 SENTLIK baska bir sirket. Ekranda 19.01 EUR olan ETF icin
        # 0.06 USD'lik seri cekildi (%99.7 sapma) ve tum gostergeler
        # bu seriden hesaplandi.
        #
        # Artik yalnizca AMBIGU OLMAYAN sembol kabul ediliyor: borsa
        # sonekli olanlar (ABN.AS, ADYEN.AS) tek bir kotasyonu gosterir.
        # Soneksiz sade sembol, dogrulanmis bir SEC ticker'i yoksa
        # REDDEDILIR — eksik seri, yanlis seriden iyidir.
        if "." in sembol:
            return sembol
        return None

    def _cek(self, pg, yahoo: str, instrument_id: int, aralik: str,
             currency: str | None = None) -> int:
        return yahoo_gunluk(pg, self.db, yahoo, instrument_id, aralik, currency)


def yahoo_gunluk(pg, db, yahoo: str, instrument_id: int, aralik: str,
                 currency: str | None = None, kaynak: str = "yahoo") -> int:
    """
    Yahoo chart ucundan gunluk OHLCV cekip `prices`e yazar.

    MODUL SEVIYESINDE, cunku iki collector kullaniyor (`prices` ve
    `makro`). Ayni cekim mantigini iki yere kopyalamak bu projenin
    tekrar eden kusur sinifi: iki yerde beyan edilen gercek ayrisiyor
    (para birimi yazma adimi bir tarafta unutulur ve seri etiketsiz kalir).

    `kaynak` PARAMETRESI SESSIZ VERI KAYBINI ONLUYOR. `prices` birincil
    anahtari (instrument_id, ts, source) — PARA BIRIMI ANAHTARDA YOK.
    Ayni enstrumanin iki farkli kotasyonu ayni `source` adiyla
    yazilirsa ikincisi birincisini EZER. Sahada olcuLDU: ASML'nin
    Amsterdam (EUR) serisi `source='yahoo'` ile yazilinca ABD (USD)
    serisinin 502 barindan 9'u kaldi. Kotasyon basina AYRI kaynak adi
    kullanilmali.
    """
    pg.goto(CHART.format(sym=yahoo, aralik=aralik),
            wait_until="domcontentloaded", timeout=30000)
    data = json.loads(pg.inner_text("body"))
    sonuc = (data.get("chart") or {}).get("result") or []
    if not sonuc:
        return 0

    r = sonuc[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs = q.get("open") or [], q.get("high") or []
    lows, closes = q.get("low") or [], q.get("close") or []
    vols = q.get("volume") or []

    satirlar = []
    for i, t in enumerate(ts):
        kapanis = closes[i] if i < len(closes) else None
        # Yahoo bazi barlari null dondurur (tatil, veri boslugu, son
        # gunun henuz kapanmamis olmasi). Kapanissiz bar teknik
        # gostergeyi bozar — yazma.
        if kapanis is None:
            continue
        satirlar.append({
            "ts": datetime.fromtimestamp(t, timezone.utc).date().isoformat(),
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": kapanis,
            "volume": vols[i] if i < len(vols) else None,
        })
    if not satirlar:
        return 0

    # PARA BIRIMI KAYNAGIN KENDI BEYANINDAN. Onceden bu deger okunuyor
    # ama seriye YAZILMIYORDU; seri etiketsiz kaldigi icin USD fiyatlar
    # EUR portfoy degerleriyle yan yana kullanildi ve 17 pozisyonun
    # 14'unde ~%15.7 (EUR/USD kuru kadar) sapma olustu.
    para = (r.get("meta") or {}).get("currency") or currency
    if para:
        with db.tx() as c:
            c.execute("UPDATE instruments SET currency=COALESCE(currency,?) WHERE id=?",
                      (para, instrument_id))
    return db.upsert_prices(instrument_id, satirlar, kaynak, currency=para)
