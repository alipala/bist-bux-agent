"""
BIST sirket katalogu — KAP'in kendi listesinden.

NEDEN BU KAYNAK
---------------
BIST tarafinda enstruman katalogu YOKTU: settings.yaml'daki 10 sembol
elle yazilmisti ve isimleri bile bostu. Isim olmayinca haber eslestirmesi
ve sohbet katmanindaki enstruman tespiti calismiyor (ikisi de ada bakar).

KAP (Kamuyu Aydinlatma Platformu) borsanin resmi kamuyu aydinlatma
platformudur; BIST sirket listesi orada birincil kaynaktir — Wikipedia
veya bir veri saglayicisindan almaya gerek yok.

Sayfa kod + tam ticaret unvani veriyor:
    ACSEL | ACISELSAN ACIPAYAM SELULOZ SANAYI VE TICARET A.S. | DENIZLI | <denetci>
"""
from __future__ import annotations

import logging
import re

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

# BIST kodlari 4-6 buyuk harf/rakam. Basliklari ve sehir/denetci
# hucrelerini disarida birakmak icin kalip siki tutuldu.
_KOD = re.compile(r"^[A-Z0-9]{3,6}$")


class BistCollector(BaseCollector):
    name = "bist"
    needs_browser = True

    def collect(self) -> CollectorResult:
        url = self.sel("companies_url") or "https://www.kap.org.tr/tr/bist-sirketler"

        with self.browser.page(url) as pg:
            try:
                pg.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            try:
                pg.wait_for_selector("tr", timeout=45000)
            except Exception:
                return CollectorResult(self.name, "error", 0, "sirket tablosu yuklenmedi")

            # Liste uzun ve tembel yukleniyor; satir sayisi sabitlenene kadar kaydir.
            onceki = -1
            for _ in range(30):
                n = pg.evaluate("document.querySelectorAll('tr').length")
                if n == onceki:
                    break
                onceki = n
                pg.mouse.wheel(0, 4000)
                pg.wait_for_timeout(200)
            pg.wait_for_timeout(800)

            satirlar = pg.evaluate("""() => [...document.querySelectorAll('tr')]
                .map(r => [...r.querySelectorAll('td')].map(td => td.innerText.trim()))
                .filter(c => c.length >= 2)""")

        n, gorulen = 0, set()
        for hucreler in satirlar:
            kod = (hucreler[0] or "").strip().upper()
            ad = (hucreler[1] or "").strip()
            if not _KOD.fullmatch(kod) or not ad or kod in gorulen:
                continue
            gorulen.add(kod)
            self.db.upsert_instrument(kod, "BIST", name=ad,
                                      asset_type="equity", currency="TRY")
            n += 1

        if not n:
            return CollectorResult(self.name, "partial", 0,
                                   f"{len(satirlar)} satir okundu ama kod cikarilamadi")
        log.info("[bist] %d sirket", n)
        return CollectorResult(self.name, "ok", n)
