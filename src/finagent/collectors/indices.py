"""
Endeks bilesenleri -> BUX enstruman katalogu.

NEDEN
-----
BUX'un "Discover" ekrani evreni ENDEKSLERE gore duzenliyor:
Nasdaq 100, S&P 500, AEX, BEL20, DAX, CAC 40, IBEX 35...
Endeks bilesenleri ise public ve makine-okunur. Yuzlerce hisseyi ekran
goruntusunden OCR ile okumak yerine dogrudan listeden almak hem daha hizli
hem de hatasiz: OCR'de "Avantium" -> AVTX gibi yanlis ticker tahminleri
oluyordu, burada ticker listenin kendisinde yaziyor.

KAYNAGIN ROLU
-------------
Wikipedia burada yalnizca KESIF tohumudur, kanit degildir. Bu listeden gelen
her sirket ayrica SEC'in resmi ticker/CIK dosyasina karsi dogrulanir
(research/identity.py) ve raporda hicbir zaman kaynak olarak gosterilmez.

KAPSAM KARARI
-------------
Bu ~660 sirket KATALOGA girer (aranabilir evren), ARASTIRMA HEDEFI olmaz.
Hepsi icin gunluk EDGAR + basin taramasi yapmak hem cok pahali hem gereksiz;
arastirma hedefi = portfoy + kullanicinin sectigi adaylar.
"""
from __future__ import annotations

import io
import logging
import re

import httpx

from ..research.identity import UA
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

# Tarihsel/istatistik tablolari ELE: "Period in BEL 20" kolonlu tablo eski
# uyeleri listeliyor (Ablynx, 2018'de cikmis) ve satir sayisi guncel tablodan
# fazla oldugu icin "en buyuk tabloyu sec" kurali yanlis tabloyu seciyordu.
_RED_IPUCLARI = ("period in", "former", "date removed", "removed", "closing level",
                 "change in index", "milestone", "year")
_KABUL_IPUCLARI = ("in index since", "index weighting", "security", "company",
                   "gics", "icb sector", "sector")


class IndicesCollector(BaseCollector):
    name = "indices"
    needs_browser = False

    def collect(self) -> CollectorResult:
        endeksler = self.s.get("sources.indices.list") or []
        if not endeksler:
            return CollectorResult(self.name, "skipped", 0, "endeks tanimli degil")

        import pandas as pd

        toplam, basarisiz = 0, []
        with httpx.Client(headers={"User-Agent": UA}, timeout=40.0,
                          follow_redirects=True) as client:
            for e in endeksler:
                ad, url = e.get("name"), e.get("url")
                if not (ad and url):
                    continue
                try:
                    r = client.get(url)
                    r.raise_for_status()
                    tablo = self._bilesenler(pd.read_html(io.StringIO(r.text)),
                                             beklenen=e.get("expect"))
                    if tablo is None:
                        basarisiz.append(f"{ad} (tablo bulunamadi)")
                        continue
                    n = self._kaydet(tablo, ad, e.get("suffix"))
                    toplam += n
                    log.info("[indices] %s: %d sirket", ad, n)
                except Exception as ex:              # noqa: BLE001
                    log.warning("[indices] %s alinamadi: %s", ad, ex)
                    basarisiz.append(f"{ad} ({type(ex).__name__})")

        durum = "partial" if basarisiz else "ok"
        return CollectorResult(self.name, durum if toplam else "error", toplam,
                               "; ".join(basarisiz) or None)

    # ------------------------------------------------------------------
    @staticmethod
    def _bilesenler(tablolar, beklenen: int | None):
        """
        Guncel bilesen tablosunu secer.

        En buyuk tabloyu almak YANLIS: tarihsel uyelik tablolari daha uzun
        olabiliyor. Bunun yerine puanlama yapiyoruz.
        """
        en_iyi, en_iyi_puan = None, -1
        for t in tablolar:
            kolonlar = " ".join(str(c).lower() for c in t.columns)
            if not any(k in kolonlar for k in ("ticker", "symbol")):
                continue
            if any(k in kolonlar for k in _RED_IPUCLARI):
                continue
            if len(t) < 5:
                continue
            puan = sum(2 for k in _KABUL_IPUCLARI if k in kolonlar)
            if beklenen:
                # Beklenen bilesen sayisina yakinlik en guclu sinyal
                puan += max(0, 12 - abs(len(t) - beklenen))
            if puan > en_iyi_puan:
                en_iyi, en_iyi_puan = t, puan
        return en_iyi

    def _kaydet(self, tablo, endeks: str, suffix: str | None) -> int:
        kolonlar = {str(c).lower(): c for c in tablo.columns}
        tk = next((kolonlar[k] for k in kolonlar if "ticker" in k or "symbol" in k), None)
        ak = next((kolonlar[k] for k in kolonlar
                   if k in ("company", "security", "name", "company name")), None)
        if tk is None:
            return 0

        n = 0
        for _, satir in tablo.iterrows():
            sembol = _sembol_temizle(str(satir[tk]), suffix)
            ad = str(satir[ak]).strip() if ak is not None else None
            if not sembol or (ad in (None, "nan", "")):
                ad = ad if ad not in ("nan", "") else None
            if not sembol:
                continue

            iid = self.db.upsert_instrument(sembol, "BUX", name=ad,
                                            asset_type="equity")
            self.db.add_index_member(iid, endeks)
            n += 1
        return n


def _sembol_temizle(ham: str, suffix: str | None) -> str | None:
    """
    'Euronext Brussels: ABI' -> 'ABI.BR'   |   'ABN.AS' -> 'ABN.AS'
    'MMM' -> 'MMM'

    Borsa soneki KORUNUR: ayni kisa kod farkli borsalarda farkli sirket
    olabiliyor, sonek olmadan katalogda cakisirlar.
    """
    if not ham or ham.lower() == "nan":
        return None
    s = ham.strip()
    # "Euronext Brussels: ABI" gibi onekleri at
    if ":" in s:
        s = s.split(":")[-1].strip()
    s = re.sub(r"\[.*?\]", "", s)              # dipnot isaretleri
    s = s.replace("\xa0", " ").strip().upper()
    if not s or " " in s and len(s.split()) > 1:
        s = s.split()[0]
    if not re.fullmatch(r"[A-Z0-9._-]{1,14}", s):
        return None
    if suffix and "." not in s:
        s = f"{s}{suffix}"
    return s
