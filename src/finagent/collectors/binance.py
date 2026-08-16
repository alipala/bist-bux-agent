"""
Binance fiyat serisi — kripto teknik analizinin on kosulu.

ANAHTAR GEREKMIYOR
------------------
Kullanilan uclarin hepsi PUBLIC: `klines`, `ticker/24hr`, `exchangeInfo`.
Ne API anahtari ne oturum gerekiyor — 2026-08-15'te olculdu, hepsi 200
donuyor. Bu yuzden hesap kimlik bilgisi ISTENMEDI: portfoy bakiyeleri
Telegram ekran goruntusu kanalindan geliyor (BUX ile ayni yol).

Anahtar yalnizca HESAP uclari icin gerekir (bakiye, emir) — ve o uclara
bu projenin ihtiyaci yok.

IKI AYRI COZUNURLUK
-------------------
* GUNLUK bar -> `prices` tablosu. Mevcut butun analiz (SMA/RSI/olay
  calismasi) gunluk bar varsayar; kripto de ayni tabloya gunluk yazar.
* SAATLIK bar -> `prices_hourly` tablosu. AYRI tutulmasinin sebebi
  schema.sql'de yaziyor: `prices`'i okuyan hicbir sorgu `source` filtresi
  kullanmiyor, saatlik satirlar oraya karissa RSI/SMA/CAR sessizce yanlis
  hesaplanirdi.

KRIPTO 7/24
-----------
Borsa tatili yok, hafta sonu bosluk yok. Gunluk bar UTC 00:00'da kapanir.
Bu, hisse serisinden farkli bir varsayim: bosluk gormemek NORMAL.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

KLINES = "https://api.binance.com/api/v3/klines"
TICKER = "https://api.binance.com/api/v3/ticker/24hr"

# Binance tek istekte en fazla 1000 mum veriyor; daha uzun gecmis icin
# `endTime` ile GERIYE dogru sayfalaniyor (bkz. _sayfali).
SAYFA_SINIRI = 1000
GUNLUK_LIMIT = 1100        # ~3 yil
SAATLIK_LIMIT = 720        # 30 gun


def _ts_gun(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _ts_saat(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:00")


def _mumlari_coz(ham: list, saatlik: bool) -> list[dict]:
    """
    Binance kline dizisi -> dict. Alan sirasi:
      0 acilis_ms, 1 open, 2 high, 3 low, 4 close, 5 volume,
      6 kapanis_ms, 7 quote_volume, 8 trades, ...

    SON MUM ATILIR: hala olusmakta olan mumdur. Kapanmamis mumun 'close'
    degeri o anki fiyattir ve bir sonraki cagrida degisir — kapanmis bar
    gibi kaydedilirse gostergeler her calismada baska sonuc verir.
    """
    out = []
    for m in ham[:-1]:
        try:
            out.append({
                "ts": (_ts_saat if saatlik else _ts_gun)(int(m[0])),
                "open": float(m[1]), "high": float(m[2]), "low": float(m[3]),
                "close": float(m[4]), "volume": float(m[5]),
                "quote_volume": float(m[7]), "trades": int(m[8]),
            })
        except (TypeError, ValueError, IndexError):
            continue
    return out


class BinanceCollector(BaseCollector):
    name = "binance"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import httpx

        hedefler = self.db.research_targets(kripto=True)
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0,
                                   "kripto hedefi yok — once portfoy/favori "
                                   "ekran goruntusu gonder")

        kimlikler = {r["symbol"]: r for r in self.db.identities()}
        gunluk_limit = int(self.s.get("sources.binance.daily_bars", GUNLUK_LIMIT))
        saatlik_limit = int(self.s.get("sources.binance.hourly_bars", SAATLIK_LIMIT))

        toplam, atlanan, basarisiz = 0, [], []
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "finagent/1.0"}) as http:
            for h in hedefler:
                cift = self._cift(h, kimlikler.get(h["symbol"]))
                if not cift:
                    atlanan.append(h["symbol"])
                    continue
                try:
                    toplam += self._cek(http, cift, h["id"], "1d",
                                        gunluk_limit, saatlik=False)
                    toplam += self._cek(http, cift, h["id"], "1h",
                                        saatlik_limit, saatlik=True)
                except Exception as e:              # noqa: BLE001
                    log.warning("[binance] %s alinamadi: %s", cift, e)
                    basarisiz.append(h["symbol"])

        notlar = []
        if atlanan:
            notlar.append(f"kimlik yok, atlandi: {', '.join(atlanan[:8])}")
        if basarisiz:
            notlar.append(f"alinamadi: {', '.join(basarisiz[:8])}")
        durum = "ok" if toplam and not (atlanan or basarisiz) else (
            "partial" if toplam else "error")
        return CollectorResult(self.name, durum, toplam,
                               " · ".join(notlar) or None)

    # ------------------------------------------------------------------
    @staticmethod
    def _cift(_hedef, kimlik) -> str | None:
        """
        KIMLIGI DOGRULANMAMIS SEMBOLDEN VERI CEKILMEZ.

        Kriptoda sembol cakismasi kuraldir; "ROSE" adiyla birden fazla token
        vardir. Yanlis cifti cekmek eksik veri cekmekten tehlikelidir: butun
        gostergeler hesaplanir ve hepsi tutarli sekilde yanlis olur.
        """
        if kimlik is None:
            return None
        if kimlik["status"] != "dogrulandi":
            return None
        cift = kimlik["pair"] if "pair" in kimlik.keys() else None
        return cift or None

    @staticmethod
    def _sayfali(http, cift: str, aralik: str, hedef: int) -> list:
        """
        1000 mum sinirini asmak icin GERIYE dogru sayfalar.

        Binance mumlari ARTAN sirada dondurur, yani ilk eleman parcanin EN
        ESKISI. Bir sonraki sayfa icin `endTime` oraya kuruluyor ve gelen
        parca basa ekleniyor; sonucta tek bir artan dizi olusuyor.

        SON MUMU BURADA ATMIYORUZ. `_mumlari_coz` yalnizca dizinin en
        sonundaki mumu atar (henuz kapanmamis olan). Sayfa basina atsaydik
        her 1000 barda bir KAPANMIS mum kaybolur, seride sessiz delikler
        acilirdi — SMA200 ve olay penceresi bunu fark ettirmeden yanlis
        hesaplardi.
        """
        import time

        ham: list = []
        end: int | None = None
        while len(ham) < hedef:
            p = {"symbol": cift, "interval": aralik,
                 "limit": min(SAYFA_SINIRI, hedef - len(ham))}
            if end is not None:
                p["endTime"] = end
            r = http.get(KLINES, params=p)
            if r.status_code == 429:          # hiz siniri: bekle, tekrar dene
                time.sleep(float(r.headers.get("Retry-After", 2)))
                continue
            r.raise_for_status()
            parca = r.json() or []
            if not parca:
                break
            ham = parca + ham
            end = int(parca[0][0]) - 1
            if len(parca) < p["limit"]:       # borsada daha eski veri yok
                break
        return ham

    def _cek(self, http, cift: str, instrument_id: int, aralik: str,
             limit: int, saatlik: bool) -> int:
        barlar = _mumlari_coz(self._sayfali(http, cift, aralik, limit), saatlik)
        if not barlar:
            return 0
        if saatlik:
            return self.db.upsert_prices_hourly(instrument_id, barlar, self.name)
        # PARA BIRIMI YAZILMAK ZORUNDA. Parite USDT ile bittigi icin kotasyon
        # tanim geregi USDT'dir — ama `prices.currency` bos birakilirsa seri
        # "birimsiz" olur ve EUR/TRY serileriyle yan yana kullanildiginda
        # her seviye yanlis cikar. Bu tam olarak 17 pozisyonun 14'unu
        # bozan hataydi; orada da eksik olan sey bu tek alandi.
        kotasyon = "USDT" if cift.endswith("USDT") else None
        return self.db.upsert_prices(instrument_id, barlar, self.name,
                                     currency=kotasyon)
