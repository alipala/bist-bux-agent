"""
BIST fiyat/tarihsel seri — Is Yatirim acik veri ucu.

Neden burasi: BIST icin login gerektirmeyen, JSON donen ve gunluk OHLC
veren en stabil kaynak. Ucun sozlesmesi degisirse `_fetch_via_browser`
fallback'i devreye girer (kalici oturumdaki gercek tarayici ustunden
ayni istegi atar; boylece UA/cerez/TLS parmak izi normal kullaniciyla ayni olur).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

ENDPOINT = ("{base}/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
            "?hisse={symbol}&startdate={start}&enddate={end}")

# JSON alan adlari -> kendi semamiz
FIELD_MAP = {
    "ts": "HGDG_TARIH",
    "close": "HGDG_KAPANIS",
    "high": "HGDG_MAX",
    "low": "HGDG_MIN",
    "volume": "HGDG_HACIM",
    "open": "HGDG_ACILIS",
}


class IsYatirimCollector(BaseCollector):
    name = "isyatirim"
    needs_browser = False          # browser varsa fallback icin kullanilir

    def collect(self) -> CollectorResult:
        symbols = self.s.bist_watchlist
        if not symbols:
            return CollectorResult(self.name, "skipped", 0, "watchlist.bist bos")

        lookback = int(self.s.get("analysis.lookback_days", 250))
        end = date.today()
        start = end - timedelta(days=int(lookback * 1.6) + 10)  # tatil/haftasonu payi
        base = self.s.get("sources.isyatirim.base_url", "https://www.isyatirim.com.tr")

        total, failed = 0, []
        for sym in symbols:
            url = ENDPOINT.format(
                base=base, symbol=sym,
                start=start.strftime("%d-%m-%Y"), end=end.strftime("%d-%m-%Y"),
            )
            rows = self._fetch(url, sym)
            if rows is None:
                failed.append(sym)
                continue
            iid = self.db.upsert_instrument(sym, "BIST", asset_type="equity", currency="TRY")
            total += self.db.upsert_prices(iid, rows, source=self.name)

        status = "ok" if not failed else ("error" if len(failed) == len(symbols) else "partial")
        err = f"cekilemeyen: {', '.join(failed)}" if failed else None
        return CollectorResult(self.name, status, total, err)

    # ------------------------------------------------------------------
    def _fetch(self, url: str, symbol: str) -> list[dict] | None:
        payload = self._fetch_via_httpx(url)
        if payload is None and self.browser is not None:
            log.info("[%s] %s icin tarayici fallback'i deneniyor", self.name, symbol)
            payload = self._fetch_via_browser(url)
        if payload is None:
            return None
        return self._normalize(payload)

    def _fetch_via_httpx(self, url: str) -> list | None:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/default.aspx",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            r = httpx.get(url, headers=headers, timeout=25.0, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except Exception as e:                       # noqa: BLE001
            log.debug("httpx basarisiz: %s", e)
            return None
        return data.get("value") if isinstance(data, dict) else None

    def _fetch_via_browser(self, url: str) -> list | None:
        try:
            with self.browser.page("https://www.isyatirim.com.tr/") as pg:
                data = pg.evaluate(
                    """async (u) => {
                         const r = await fetch(u, {headers: {'X-Requested-With':'XMLHttpRequest'}});
                         if (!r.ok) return null;
                         return await r.json();
                       }""",
                    url,
                )
            return data.get("value") if isinstance(data, dict) else None
        except Exception as e:                       # noqa: BLE001
            log.debug("browser fallback basarisiz: %s", e)
            return None

    @staticmethod
    def _normalize(values: list) -> list[dict]:
        out: list[dict] = []
        for v in values or []:
            raw_ts = v.get(FIELD_MAP["ts"])
            if not raw_ts:
                continue
            # '01-02-2025' veya '2025-02-01T00:00:00' gelebilir
            ts = str(raw_ts)[:10]
            if "-" in ts and len(ts.split("-")[0]) == 2:
                d, m, y = ts.split("-")
                ts = f"{y}-{m}-{d}"
            close = v.get(FIELD_MAP["close"])
            if close in (None, 0):
                continue
            out.append({
                "ts": ts,
                "open": v.get(FIELD_MAP["open"]),
                "high": v.get(FIELD_MAP["high"]),
                "low": v.get(FIELD_MAP["low"]),
                "close": close,
                "volume": v.get(FIELD_MAP["volume"]),
            })
        out.sort(key=lambda r: r["ts"])
        return out
