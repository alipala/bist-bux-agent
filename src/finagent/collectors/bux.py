"""
BUX (ABN AMRO) — islem gorebilen ETF/ETC evreni.

TASARIM DEGISIKLIGI (2026-08-14)
--------------------------------
Bu collector eskiden portfoy kaziyordu. BUX'un WEB ARAYUZU YOK:
  * app.getbux.com  -> DNS'te cozulmuyor (ERR_NAME_NOT_RESOLVED)
  * bux.com/login   -> sadece WordPress admin (403)
  * bux.com'daki tum CTA'lar app.adjust.com uzerinden App Store'a gidiyor
BUX yalnizca mobil uygulama. Login'li kazima teknik olarak imkansiz.

  -> POZISYONLAR artik Telegram'a atilan ekran goruntusunden geliyor
     (src/finagent/bot/listener.py + src/finagent/vision/screenshot.py).

Bu collector ise BUX'un PUBLIC yayinladigi "Our ETFs" katalogunu okur:
"hangi varliklara girip cikabilirim" sorusunun login gerektirmeyen cevabi.
Sayfa ISIN + isim + ihracci veriyor (~210 benzersiz enstruman).
"""
from __future__ import annotations

import logging
import re

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

_ISSUER = re.compile(r"\(([^)]+)\)\s*$")

# NOT: ETF/ETC ayrimi isimden TAHMIN EDILMIYOR. Denendi ve yanlis cikti:
# "VanEck Gold Miners" bir madencilik HISSE ETF'i, emtia ETC'si degil —
# isimde 'gold' gecmesi emtia demek degil. Sayfa turu ayrica belirtmedigi
# icin hepsi "etf" yazilir; yanlis etiket, etiketsizden kotudur.


class BuxCollector(BaseCollector):
    name = "bux"
    needs_browser = True

    def collect(self) -> CollectorResult:
        if not self.require_selectors("universe_url", "universe_row",
                                      "universe_name", "universe_isin"):
            return CollectorResult(self.name, "skipped", 0, "selector'lar tanimli degil")

        url = self.sel("universe_url")
        row_sel = self.sel("universe_row")

        with self.browser.page(url) as pg:
            try:
                pg.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            try:
                pg.wait_for_selector(row_sel, timeout=45000)
            except Exception:
                return CollectorResult(self.name, "error", 0,
                                       f"enstruman satiri eslesmedi: {row_sel}")

            # Liste tembel yukleniyor (lazy render) — sona kadar kaydirmadan
            # yalnizca ilk ekran dolusu enstruman DOM'a giriyor.
            prev = -1
            for _ in range(25):
                n = len(pg.query_selector_all(row_sel))
                if n == prev:
                    break
                prev = n
                pg.mouse.wheel(0, 3000)
                pg.wait_for_timeout(250)
            pg.wait_for_timeout(1000)

            name_sel, isin_sel = self.sel("universe_name"), self.sel("universe_isin")
            items = []
            for el in pg.query_selector_all(row_sel):
                nd, idd = el.query_selector(name_sel), el.query_selector(isin_sel)
                if not (nd and idd):
                    continue
                items.append((nd.inner_text().strip(), idd.inner_text().strip().upper()))

        n = 0
        seen: set[str] = set()
        for full_name, isin in items:
            # ISIN bicimi: 2 harf ulke + 9 alfanumerik + 1 kontrol hanesi
            if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin) or isin in seen:
                continue
            seen.add(isin)

            issuer_m = _ISSUER.search(full_name)
            issuer = issuer_m.group(1).strip() if issuer_m else None
            short = _ISSUER.sub("", full_name).strip() or full_name

            self.db.upsert_instrument(
                symbol=isin,                     # BUX bu sayfada ticker vermiyor
                venue="BUX",
                name=f"{short} ({issuer})" if issuer else short,
                asset_type="etf",
                currency="EUR",
                isin=isin,
            )
            n += 1

        if not n:
            return CollectorResult(self.name, "partial", 0,
                                   f"{len(items)} satir bulundu ama ISIN cikarilamadi")
        log.info("[bux] %d benzersiz enstruman (%d ham satir)", n, len(items))
        return CollectorResult(self.name, "ok", n)
