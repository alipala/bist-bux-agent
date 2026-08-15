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

log = logging.getLogger(__name__)

CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         "{sym}?range={aralik}&interval=1d")
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
        finally:
            pg.close()

        durum = "partial" if basarisiz else "ok"
        return CollectorResult(self.name, durum if toplam else "error", toplam,
                               ("alinamadi: " + ", ".join(basarisiz[:8]))
                               if basarisiz else None)

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
        return None if sembol.startswith("~") else sembol

    def _cek(self, pg, yahoo: str, instrument_id: int, aralik: str) -> int:
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

        para = (r.get("meta") or {}).get("currency")
        if para:
            with self.db.tx() as c:
                c.execute("UPDATE instruments SET currency=COALESCE(currency,?) WHERE id=?",
                          (para, instrument_id))
        return self.db.upsert_prices(instrument_id, satirlar, "yahoo")
