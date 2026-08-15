"""
Tiingo — YALNIZCA doviz kuru yedegi. Varsayilan KAPALI.

NEDEN BU KADAR DAR
------------------
2026-08-15'te gercek cagrilarla olculdu:

  `ASML`        -> Nasdaq ADR'si donuyor, Amsterdam kotasyonu DEGIL
  `ASML.AS`     -> 404          (Euronext kapsami YOK)
  `ASML-AS`     -> 404
  haber API'si  -> {"detail": "You do not have permission to access
                    the News API"}  (ucretsiz katmanda kapali)
  `fx/eurusd`   -> CALISIYOR

Portfoyun %95'i Amsterdam'da kote. Tiingo orayi gormuyor, haber de
kapali. Geriye kalan tek isleyen parca FX; onu da Alpha Vantage zaten
veriyor. Bu yuzden Tiingo yalnizca AV kotasi (gunde 25) dolarsa devreye
girecek bir YEDEK olarak duruyor.

Kota avantaji gercek: Tiingo ucretsiz katmanda gunde 1.000 istek, saatte
50 — AV'nin 25'ine gore cok genis. Sorun kota degil, KAPSAM.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

BASE = "https://api.tiingo.com/tiingo/fx"


class TiingoCollector(BaseCollector):
    name = "tiingo"
    needs_browser = False

    def collect(self) -> CollectorResult:
        anahtar = os.environ.get("TIINGO_API_KEY", "").strip()
        if not anahtar:
            return CollectorResult(self.name, "skipped", 0,
                                   "TIINGO_API_KEY tanimli degil (.env)")

        ciftler = self.s.get("sources.tiingo.fx_pairs") or ["eurusd", "usdtry"]
        gun = int(self.s.get("sources.tiingo.fx_days", 90))
        bas = (datetime.now(timezone.utc).date()
               - __import__("datetime").timedelta(days=gun)).isoformat()

        import httpx
        toplam, notlar = 0, []
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "finagent/1.0"}) as http:
            for cift in ciftler:
                try:
                    r = http.get(f"{BASE}/{cift}/prices",
                                 params={"startDate": bas, "resampleFreq": "1day",
                                         "token": anahtar})
                    r.raise_for_status()
                    veri = r.json()
                except Exception as e:                # noqa: BLE001
                    notlar.append(f"{cift}: HATA {type(e).__name__}")
                    continue
                if not isinstance(veri, list) or not veri:
                    notlar.append(f"{cift}: bos")
                    continue
                base, quote = cift[:3].upper(), cift[3:].upper()
                satir = []
                for x in veri:
                    ts = (x.get("date") or "")[:10]
                    fiyat = x.get("close") or x.get("midPrice")
                    if ts and fiyat:
                        satir.append((ts, base, quote, float(fiyat), self.name))
                if satir:
                    with self.db.tx() as c:
                        c.executemany(
                            """INSERT INTO fx_rates (ts, base, quote, rate, source)
                               VALUES (?,?,?,?,?)
                               ON CONFLICT(ts, base, quote, source) DO UPDATE
                               SET rate=excluded.rate""", satir)
                    toplam += len(satir)
                    notlar.append(f"{base}/{quote}: {len(satir)}")

        durum = "ok" if toplam and not any("HATA" in x for x in notlar) else (
            "partial" if toplam else "error")
        return CollectorResult(self.name, durum, toplam, " · ".join(notlar) or None)
