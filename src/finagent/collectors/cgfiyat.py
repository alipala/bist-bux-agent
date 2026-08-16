"""
CoinGecko gunluk fiyat serisi — Binance'te LISTELENMEYEN coin'ler icin.

NEDEN AYRI BIR COLLECTOR
------------------------
Ilk 100'un ucte biri Binance'te yok: borsa tokenlari (LEO, OKB, CRO,
KCS, GT, WBT, BGB, HTX) kendi borsalarinda, XMR gizlilik nedeniyle
delist edilmis, HYPE/KAS/PI/XDC/FLR hic listelenmemis. Bunlar
ALINAMAZ ama piyasanin ucte birini olusturuyor. Gormezsek sermayenin
nereye dondugunu degil, yalnizca nereden ciktigini goruruz — ve
rotasyonu genel zayiflik diye okuruz.

Bu yuzden `venue='CRYPTO'` ve `watchlist.kind='referans'`: taranirlar,
ama bir islem onerisinin konusu OLAMAZLAR.

UC ONEMLI FARK — HEPSI KAYDEDILIYOR
-----------------------------------
1. KAYNAK BASKA. CoinGecko fiyati borsalar arasi HACIM AGIRLIKLI
   BILESIK; Binance tek borsanin defteri. Ikisi ayni seride
   karistirilirsa gostergeler sessizce bozulur. `source='coingecko'`
   yazildigi ve `fiyat_kaynagi()` tek kaynak sectigi icin karisamazlar.

2. PARA BIRIMI USD, USDT DEGIL. Fark kucuktur (~%0,1) ama sifir
   degildir; USDT peg'inden sapabiliyor. Etiket dogru olmali, "nasil
   olsa yakin" diye USDT yazmak seriyi sessizce yalanci yapar.

3. OHLC YOK, YALNIZCA KAPANIS + HACIM. `/ohlc` ucu 365 gun icin 4
   gunluk mum donduruyor (olculdu: 92 mum) — gunluk gosterge icin
   kullanilamaz. `market_chart` gunluk nokta veriyor; open/high/low
   NULL kalir. Mevcut gostergelerin (SMA, RSI, getiri, olay etkisi)
   hepsi kapanistan hesaplandigi icin bu bir kayip degil, ama
   "en dusuk/en yuksek" soran bir analiz bu coin'lerde CALISMAZ.

HIZ SINIRI GERCEK
-----------------
Olculdu: pes pese 5 istekte 3'uncuden sonra 429. Istekler arasi
bekleme SART; 429 gorulurse geri cekilip tekrar deneniyor.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

CHART = "https://api.coingecko.com/api/v3/coins/{cid}/market_chart"

VARSAYILAN_GUN = 365
BEKLEME_SN = 2.5          # istekler arasi
AZAMI_DENEME = 3
VARSAYILAN_KOTA = 10       # tur basina istek — hiz siniri gercek maliyet


class CoinGeckoFiyatCollector(BaseCollector):
    name = "cgfiyat"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import httpx

        hepsi = [h for h in self.db.research_targets(kripto=True)
                 if h["venue"] == "CRYPTO"]
        if not hepsi:
            return CollectorResult(self.name, "skipped", 0,
                                   "referans coin yok — once kriptoevren")

        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        tam_gun = int(self.s.get("sources.cgfiyat.days", VARSAYILAN_GUN))
        kota = int(self.s.get("sources.cgfiyat.max_per_run", VARSAYILAN_KOTA))
        hedefler, ertelenen, guncel = self._sirala(hepsi, kota)

        toplam, atlanan, basarisiz = 0, [], []
        with httpx.Client(timeout=45, follow_redirects=True,
                          headers={"User-Agent": "finagent/1.0"}) as http:
            for i, h in enumerate(hedefler):
                cid = self._coingecko_id(kimlikler.get(h["symbol"]))
                if not cid:
                    atlanan.append(h["symbol"])
                    continue
                try:
                    barlar = self._seri(http, cid,
                                        self._kac_gun(h["id"], tam_gun))
                except Exception as e:                 # noqa: BLE001
                    log.warning("[cgfiyat] %s alinamadi: %s", h["symbol"], e)
                    basarisiz.append(h["symbol"])
                    continue
                if barlar:
                    toplam += self.db.upsert_prices(h["id"], barlar, self.name,
                                                    currency="USD")
                if i < len(hedefler) - 1:
                    time.sleep(BEKLEME_SN)

        notlar = [f"{len(hedefler)} cekildi, {guncel} zaten guncel"]
        # SESSIZ KISITLAMA YASAK: kota yuzunden atlanan coin SAYILIYOR ve
        # YAZILIYOR. Yazilmasaydi cikti "hepsi tazelendi" gibi okunurdu.
        if ertelenen:
            notlar.append(f"kota ({kota}) nedeniyle ertelendi: "
                          f"{', '.join(ertelenen[:10])}")
        if atlanan:
            notlar.append(f"coingecko_id yok: {', '.join(atlanan[:8])}")
        if basarisiz:
            notlar.append(f"alinamadi: {', '.join(basarisiz[:8])}")
        # "Yapacak is yoktu" bir ARIZA DEGILDIR. Bunu `partial` saymak, her
        # gece sahte bir uyari uretir ve `partial`in anlamini asindirir.
        if atlanan or basarisiz:
            durum = "partial" if toplam else "error"
        else:
            durum = "ok"
        return CollectorResult(self.name, durum, toplam,
                               " · ".join(notlar) or None)

    # ------------------------------------------------------------------
    def _sirala(self, hepsi: list, kota: int) -> tuple[list, list[str], int]:
        """
        EN BAYAT OLAN ONCE, ve tur basina KOTA.

        Gerekce olculdu 2026-08-16: 21 coin'in tam cekimi 5 dk 56 sn surdu.
        Maliyet veri boyutu degil CoinGecko'nun hiz siniri — ucretsiz
        katmanda istek basina ~12-17 sn'ye denk geliyor ve payload'i
        kucultmek bunu degistirmiyor. Nabzin toplayici butcesi 13,7 dk,
        ExitTimeOut 20 dk; sinirsiz birakilirsa launchd isi ORTASINDA
        oldururdu — Is Yatirim'da yasanan sessiz basarisizligin aynisi.

        Referans veri BAGLAM, karar girdisi degil; birkac gunluk gecikme
        kabul edilebilir. Kota ile her coin en gec ~3 gunde bir tazeleniyor.
        Zaten guncel olan coin ISTEK HARCAMAZ.
        """
        bugun = datetime.now(UTC).date().isoformat()
        olculu = []
        guncel = 0
        for h in hepsi:
            r = self.db.query(
                "SELECT MAX(ts) son FROM prices WHERE instrument_id=? "
                "AND source=?", (h["id"], self.name))
            son = (r[0]["son"] or "") if r else ""
            if son and son >= self._dun(bugun):
                guncel += 1
                continue
            olculu.append((son, h))
        olculu.sort(key=lambda x: x[0])          # bos string = hic veri yok, once
        secilen = [h for _, h in olculu[:kota]]
        ertelenen = [h["symbol"] for _, h in olculu[kota:]]
        return secilen, ertelenen, guncel

    @staticmethod
    def _dun(bugun_iso: str) -> str:
        from datetime import date, timedelta
        return (date.fromisoformat(bugun_iso) - timedelta(days=1)).isoformat()

    def _kac_gun(self, instrument_id: int, tam: int) -> int:
        """
        Bosluk kadar iste, 365 gun degil. Hiz sinirini degistirmez ama
        gereksiz veri tasimaz ve CoinGecko tarafinda daha ucuz.
        """
        r = self.db.query("SELECT MAX(ts) son FROM prices WHERE instrument_id=? "
                          "AND source=?", (instrument_id, self.name))
        son = (r[0]["son"] or "") if r else ""
        if not son:
            return tam
        from datetime import date
        bosluk = (datetime.now(UTC).date() - date.fromisoformat(son[:10])).days
        return max(2, min(tam, bosluk + 3))

    @staticmethod
    def _coingecko_id(kimlik) -> str | None:
        if kimlik is None:
            return None
        return (kimlik["coingecko_id"]
                if "coingecko_id" in kimlik.keys() else None) or None

    def _seri(self, http, cid: str, gun: int) -> list[dict]:
        for deneme in range(AZAMI_DENEME):
            r = http.get(CHART.format(cid=cid),
                         params={"vs_currency": "usd", "days": gun,
                                 "interval": "daily"})
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 6)) * (deneme + 1))
                continue
            r.raise_for_status()
            return self._coz(r.json() or {})
        raise RuntimeError("hiz siniri asilamadi (429)")

    @staticmethod
    def _coz(d: dict) -> list[dict]:
        """
        BUGUNUN NOKTASI ATILIR. `interval=daily` son deger olarak O ANKI
        fiyati veriyor — Binance'in kapanmamis mumuyla ayni sorun. Kapanmis
        bar gibi kaydedilirse gostergeler her kosuda baska sonuc verir.
        """
        hacim = {int(t): v for t, v in (d.get("total_volumes") or [])}
        bugun = datetime.now(UTC).date()
        out = []
        for t, fiyat in (d.get("prices") or []):
            t = int(t)
            g = datetime.fromtimestamp(t / 1000, UTC).date()
            if g >= bugun:
                continue
            out.append({"ts": g.isoformat(), "close": float(fiyat),
                        "volume": hacim.get(t)})
        return out
