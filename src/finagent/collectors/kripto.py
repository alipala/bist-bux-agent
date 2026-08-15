"""
Kripto kimlik cozumu — diger kripto collector'larinin ON KOSULU.

Hisse tarafinda bu isi EDGAR yapiyor (sirket -> CIK). Kriptoda karsiligi:
sembol -> (Binance cifti, CoinGecko coin'i). Ayrintili gerekce
`research/crypto_identity.py` docstring'inde.

SIRA ONEMLI: kripto -> binance -> coingecko. Kimlik cozulmeden fiyat da
piyasa degeri de cekilmez; ikisi de kimligi "dogrulandi" olmayan
enstrumani sessizce atlar.
"""
from __future__ import annotations

import logging

from ..research.crypto_identity import CryptoResolver
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)


class KriptoIdentityCollector(BaseCollector):
    name = "kripto"
    needs_browser = False

    def collect(self) -> CollectorResult:
        import httpx

        hedefler = self.db.research_targets(kripto=True)
        if not hedefler:
            return CollectorResult(self.name, "skipped", 0,
                                   "kripto enstrumani yok — once ekran "
                                   "goruntusu gonder")

        with httpx.Client(timeout=45, follow_redirects=True,
                          headers={"User-Agent": "finagent/1.0"}) as http:
            r = CryptoResolver(http)
            r.coingecko([h["symbol"] for h in hedefler])   # tek toplu istek

            sayac: dict[str, int] = {}
            korunan = []
            for h in hedefler:
                k = r.coz(h["symbol"], h["name"])
                sayac[k["status"]] = sayac.get(k["status"], 0) + 1
                if not self.db.save_crypto_identity(h["id"], k):
                    korunan.append(h["symbol"])   # elle atanmis, ezilmedi

        dogru = sayac.get("dogrulandi", 0)
        notlar = " · ".join(f"{d}: {n}" for d, n in sorted(sayac.items()))
        if korunan:
            notlar += f" · elle atanmis korundu: {', '.join(korunan)}"
        return CollectorResult(self.name,
                               "ok" if dogru == len(hedefler) else "partial",
                               len(hedefler), notlar)
