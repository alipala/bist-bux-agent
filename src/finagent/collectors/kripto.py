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
            korunan, ilgi_bekleyen = [], []
            for h in hedefler:
                k = r.coz(h["symbol"], h["name"])
                sayac[k["status"]] = sayac.get(k["status"], 0) + 1
                if not self._sonuc_dogru(k["status"], h["venue"]):
                    ilgi_bekleyen.append(f"{h['symbol']}({k['status']})")
                if not self.db.save_crypto_identity(h["id"], k):
                    korunan.append(h["symbol"])   # elle atanmis, ezilmedi

        notlar = " · ".join(f"{d}: {n}" for d, n in sorted(sayac.items()))
        if korunan:
            notlar += f" · elle atanmis korundu: {', '.join(korunan)}"
        if ilgi_bekleyen:
            notlar += " · ILGI BEKLIYOR: " + ", ".join(ilgi_bekleyen[:8])
        return CollectorResult(self.name,
                               "partial" if ilgi_bekleyen else "ok",
                               len(hedefler), notlar)

    @staticmethod
    def _sonuc_dogru(status: str, venue: str | None) -> bool:
        """
        Bu sonuc DOGRU bir sonuc mu, yoksa ilgi mi bekliyor?

        Eskiden olcut `dogrulandi == len(hedefler)` idi, yani `fiat`,
        `stabil` ve `cift_yok` da BASARISIZLIK sayiliyordu. Ucu de
        DOGRU birer sonuctur: EUR gercekten fiat, USDT gercekten
        stabilcoin, ve referans coinlerin (venue='CRYPTO' — ilk 100'de
        olup Binance'te listelenmeyen HYPE, XMR, OKB...) tanimi geregi
        Binance cifti YOKTUR.

        Sonuc: collector her kosuda `partial` donuyordu ve bu, gercek
        bir arizayi ayirt edilemez kiliyordu — `prices`in BIST'i her
        kosuda "alinamadi" diye raporlamasiyla ayni sahte alarm sinifi.

        `cift_yok` yalnizca BINANCE venue'sunde sorundur: orada islem
        gordugu varsayilan bir kagidin cifti yoksa ya delist olmustur
        ya da sembol yanlistir — ikisi de soylenmeli.
        """
        if status in ("dogrulandi", "fiat", "stabil"):
            return True
        if status == "cift_yok":
            return (venue or "").upper() != "BINANCE"
        return False                       # ad_yok, eslesmedi: elle bakilmali
