"""
Enstruman kimlik cozumlemesi — arastirmanin dogruluk temeli.

PROBLEM
-------
BUX ekraninda ticker YAZMIYOR, yalnizca sirket adi var. Model addan ticker
tahmin ediyor ve bu tahmin baska bir sirkete ait olabiliyor:

    Portfoydeki "Avantium" (Hollanda, Euronext)
      -> model "AVTX" tahmin etti
      -> AVTX SEC'de "Avalo Therapeutics" (ABD biyotek)

Bu tahmin dogrulanmadan kullanilsaydi, Avantium pozisyonuna bambaska bir
sirketin bilancosu ve haberleri baglanirdi. Finansal analizde bu, yanlis
sayidan daha tehlikeli: rapor tutarli gorunur ama tamamen alakasizdir.

COZUM
-----
Ticker eslesmesi TEK BASINA yeterli sayilmaz. SEC kaydindaki sirket adi ile
enstrumanin adi da uyusmali. Uyusmuyorsa kimlik "eslesmedi" olarak isaretlenir
ve kaynak taramasina SOKULMAZ — kullaniciya sorulur.

Kaynak: SEC'in resmi ticker/CIK/borsa eslesme dosyasi (public, anahtar yok).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

# SEC "adil kullanim" politikasi tanimlanabilir bir User-Agent istiyor.
UA = "finagent-research/1.0 (kisisel portfoy analizi; alipala.ist@gmail.com)"

# Sirket adlarindaki hukuki ekler — eslesmede anlam tasimaz.
_EKLER = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD",
    "LIMITED", "PLC", "NV", "N", "V", "BV", "SA", "AG", "SE", "ASA", "AB",
    "OYJ", "SPA", "GROUP", "GROEP", "HOLDING", "HOLDINGS", "THE", "CLASS",
    "COM", "ADR", "AS", "A", "S",
}


def ad_belirteci(ad: str | None) -> list[str]:
    """'Amazon.com Inc.' -> ['AMAZON']  |  'ING GROEP NV' -> ['ING']"""
    if not ad:
        return []
    parcalar = re.split(r"[^A-Za-z0-9]+", str(ad).upper())
    return [p for p in parcalar if p and p not in _EKLER]


# ETF/fon ihraccilari. Bir enstruman bunlardan birini tasiyorsa SEC SIRKET
# kaydiyla eslestirmek YANLISTIR: "Vanguard S&P 500" ilk belirtec uzerinden
# "Vanguard Green Investment Ltd" ile eslesiyordu — bambaska bir tuzel kisi.
_FON_AILELERI = {
    "ISHARES", "VANGUARD", "XTRACKERS", "SPDR", "LYXOR", "AMUNDI", "INVESCO",
    "VANECK", "HSBC", "UBS", "WISDOMTREE", "FIRST", "RIZE", "GLOBAL",
    "FRANKLIN", "JPMORGAN", "BNP", "DEKA", "COMSTAGE", "SOURCE", "ETFS",
    "WISDOM", "LG", "TABULA", "OSSIAM", "BETASHARES",
}
_FON_IPUCLARI = {"ETF", "ETC", "UCITS", "INDEX", "FUND", "MSCI", "FTSE", "STOXX"}

# Fon adlarinda anlam tasimayan sozcukler. DIKKAT: 'ACC'/'DIST' BURADA YOK —
# dagitan/biriktiren pay siniflarini ayirt ederler, atilirsa yanlis ISIN secilir.
_FON_GURULTU = {"ETF", "ETC", "UCITS", "INDEX", "FUND", "SHARES", "CORE"}


def fon_mu(ad: str | None, asset_type: str | None = None) -> bool:
    if (asset_type or "").lower() in ("etf", "etc", "fund"):
        return True
    belirtecler = set(ad_belirteci(ad)) | set(re.split(r"[^A-Za-z0-9]+", (ad or "").upper()))
    return bool(belirtecler & _FON_AILELERI) or bool(belirtecler & _FON_IPUCLARI)


def _fon_anahtari(ad: str | None) -> frozenset[str]:
    """Fon adini sirasiz belirtec kumesine cevirir. 'S&P' -> 'SP'."""
    if not ad:
        return frozenset()
    duz = str(ad).upper().replace("S&P", "SP").replace("&", " ")
    parcalar = re.split(r"[^A-Za-z0-9]+", duz)
    return frozenset(p for p in parcalar
                     if p and p not in _FON_GURULTU and p not in _EKLER)


def _ayni_sirket(ad_a: str | None, ad_b: str | None) -> bool:
    """
    Iki ad ayni sirketi mi gosteriyor?

    Kural: anlamli ILK belirtec ayni olmali. Gercek ornekler:
        'Avantium'           vs 'Avalo Therapeutics'      -> AVANTIUM != AVALO  ✗
        'NVIDIA'             vs 'NVIDIA CORP'             -> NVIDIA == NVIDIA   ✓
        'Amazon.com'         vs 'AMAZON COM INC'          -> AMAZON == AMAZON   ✓
        'ING'                vs 'ING GROEP NV'            -> ING == ING         ✓
        'Marvell Technology' vs 'Marvell Technology, Inc' -> MARVELL == MARVELL ✓
    """
    a, b = ad_belirteci(ad_a), ad_belirteci(ad_b)
    if not a or not b:
        return False
    # Cok kisa belirtecler (2 harf) tesadufen cakisabilir; tam esitlik iste.
    if len(a[0]) < 3 or len(b[0]) < 3:
        return a[0] == b[0] and (len(a) == 1 or len(b) == 1 or a[1:2] == b[1:2])
    return a[0] == b[0]


@dataclass
class Kimlik:
    symbol: str
    name: str | None = None
    cik: str | None = None
    sec_ticker: str | None = None
    sec_name: str | None = None
    exchange: str | None = None
    ir_url: str | None = None
    isin: str | None = None
    # dogrulandi (SEC) | kap (BIST) | fon (ISIN) | eslesmedi | sec_disi | elle
    status: str = "eslesmedi"
    method: str | None = None
    note: str | None = None

    @property
    def edgar_hazir(self) -> bool:
        return self.status == "dogrulandi" and bool(self.cik)

    @property
    def arastirilabilir(self) -> bool:
        """Birincil kaynak taramasina sokulabilir mi?"""
        return self.status in ("dogrulandi", "fon", "kap", "elle")


class IdentityResolver:
    """SEC ticker/CIK haritasini indirir ve enstrumanlari cozumler."""

    def __init__(self, settings, db=None):
        self.s = settings
        self.db = db
        self.cache_path: Path = settings.root / "data" / "sec_tickers.json"
        self._by_ticker: dict[str, dict] = {}
        self._by_first_token: dict[str, list[dict]] = {}
        self._yuklendi = False

    # ------------------------------------------------------------------
    def _indir(self, max_yas_gun: int = 7) -> dict | None:
        import time
        if self.cache_path.exists():
            yas = (time.time() - self.cache_path.stat().st_mtime) / 86400
            if yas < max_yas_gun:
                try:
                    return json.loads(self.cache_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    log.warning("SEC onbellegi bozuk, yeniden indiriliyor")

        try:
            r = httpx.get(SEC_TICKERS_URL, headers={"User-Agent": UA},
                          timeout=45.0, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except Exception as e:                       # noqa: BLE001
            log.error("SEC ticker listesi alinamadi: %s", e)
            if self.cache_path.exists():             # bayat onbellek > hic yok
                log.warning("Bayat SEC onbellegi kullaniliyor")
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            return None

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def yukle(self) -> bool:
        if self._yuklendi:
            return True
        data = self._indir()
        if not data or "fields" not in data:
            return False

        idx = {f: i for i, f in enumerate(data["fields"])}
        for row in data["data"]:
            kayit = {
                "cik": str(row[idx["cik"]]).zfill(10),
                "name": row[idx["name"]],
                "ticker": (row[idx["ticker"]] or "").upper(),
                "exchange": row[idx["exchange"]],
            }
            if kayit["ticker"]:
                self._by_ticker.setdefault(kayit["ticker"], kayit)
            belirtecler = ad_belirteci(kayit["name"])
            if belirtecler:
                self._by_first_token.setdefault(belirtecler[0], []).append(kayit)

        log.info("SEC haritasi yuklendi: %d ticker, %d ad",
                 len(self._by_ticker), len(self._by_first_token))
        self._yuklendi = True
        return True

    # ------------------------------------------------------------------
    def coz(self, symbol: str, name: str | None = None,
            asset_type: str | None = None, venue: str | None = None) -> Kimlik:
        """Bir enstrumani cozumler. ASLA dogrulanmamis eslesme dondurmez."""
        k = Kimlik(symbol=symbol.upper(), name=name)

        # BIST sirketleri SEC'e tabi degil; birincil kaynaklari KAP'tir.
        # "sec_disi" demek teknik olarak dogru ama YANILTICI olurdu: kaynak
        # yok anlamina gelir, oysa KAP tam anlamiyla Kademe 1'dir.
        if (venue or "").upper() == "BIST":
            k.status = "kap"
            k.method = "borsa"
            k.note = "BIST — birincil kaynak KAP (SEC kaydi beklenmez)"
            return k

        if symbol.upper() == "CASH" or (asset_type or "").lower() == "cash":
            k.status = "sec_disi"
            k.method = "tur"
            k.note = "nakit — sirket dosyalamasi yok"
            return k

        # Fonlar SEC SIRKET kaydiyla eslestirilmez; birincil kaynaklari
        # ihraccinin fon sayfasidir ve anahtari ISIN'dir.
        if fon_mu(name, asset_type):
            return self._fon_coz(k)

        if not self.yukle():
            k.note = "SEC haritasi yuklenemedi"
            return k

        # 1) Ticker uzerinden — AMA adi da dogrula.
        aday = self._by_ticker.get(k.symbol)
        if aday:
            if _ayni_sirket(name, aday["name"]):
                return self._dogrula(k, aday, "ticker+ad")
            # Ticker tuttu ama ad tutmadi -> BASKA SIRKET. Sessizce kullanma.
            k.status = "eslesmedi"
            k.method = "ticker-ad-celiskisi"
            k.note = (f"'{k.symbol}' SEC'de '{aday['name']}' sirketine ait; "
                      f"enstruman adi '{name}'. Ayni sirket degil.")
            log.warning("[kimlik] %s: %s", k.symbol, k.note)
            return k

        # 2) Ad uzerinden — ticker tahmini tutmadiysa isim ne diyor?
        belirtecler = ad_belirteci(name)
        if belirtecler:
            adaylar = self._by_first_token.get(belirtecler[0], [])
            tam = [a for a in adaylar if _ayni_sirket(name, a["name"])]
            # Ayni sirketin birden fazla ticker'i olabilir (ING ve INGVF ->
            # ikisi de ING GROEP NV). Farkli TICKER cokluk degil; farkli CIK
            # cokluktur. CIK'lar tekse belirsizlik yok.
            cikler = {a["cik"] for a in tam}
            if len(cikler) == 1:
                # Birincil kotasyonu tercih et (NYSE/Nasdaq > OTC)
                tam.sort(key=lambda a: 0 if a["exchange"] in ("NYSE", "Nasdaq") else 1)
                return self._dogrula(k, tam[0], "ad")
            if len(cikler) > 1:
                k.status = "eslesmedi"
                k.method = "ad-belirsiz"
                k.note = ("birden fazla SEC sirketi: "
                          + ", ".join(f"{a['ticker']}={a['name']}" for a in tam[:4]))
                return k

        k.status = "sec_disi"
        k.method = "bulunamadi"
        k.note = "SEC kaydi yok (ABD disi kotasyon veya halka acik degil)"
        return k

    # ------------------------------------------------------------------
    def _fon_coz(self, k: Kimlik) -> Kimlik:
        """
        Fonu BUX'un public katalogundaki ISIN'e baglar.

        Ekran goruntusu ISIN vermiyor, sadece kisa ad ("Vanguard S&P 500").
        Katalogda ayni fonun dagitan/biriktiren siniflari ayri ISIN'lerle
        durabiliyor; TAM belirtec eslesmesi arayip birden fazla aday cikarsa
        secim YAPMIYORUZ — yanlis pay sinifi yanlis getiri demek.
        """
        k.status = "sec_disi"
        k.method = "fon"
        if self.db is None:
            k.note = "fon — ISIN eslestirmesi icin katalog gerekli"
            return k

        hedef = _fon_anahtari(k.name)
        if not hedef:
            k.note = "fon — ad okunamadi"
            return k

        adaylar = self.db.query(
            "SELECT symbol, name FROM instruments WHERE venue='BUX' AND asset_type='etf'")
        tam = [r for r in adaylar if _fon_anahtari(r["name"]) == hedef]

        if len(tam) == 1:
            k.isin = tam[0]["symbol"]
            k.sec_name = tam[0]["name"]
            k.status = "fon"
            k.note = f"BUX katalogu: {tam[0]['name']}"
            return k
        if len(tam) > 1:
            k.note = ("BUX katalogunda birden fazla aday: "
                      + ", ".join(f"{r['symbol']}={r['name']}" for r in tam[:4]))
            return k

        k.note = "BUX katalogunda birebir ad eslesmesi yok"
        return k

    # ------------------------------------------------------------------
    def elle_coz(self, symbol: str, name: str | None, ticker: str) -> Kimlik:
        """
        Kullanicinin verdigi ticker ile kimligi kurar — ad dogrulamasi ATLANIR.

        Otomatik kural bilerek muhafazakar: ad tutmuyorsa reddediyor. Ama bazi
        sirketlerin ticaret adi ile resmi unvani koprulenemiyor —
        "SpaceX" vs "SPACE EXPLORATION TECHNOLOGIES CORP" (ilk belirtec
        SPACEX != SPACE). Bu durumda karari INSAN veriyor; biz yalnizca
        ticker'in SEC'de gercekten var oldugunu dogrulayip CIK'i bagliyoruz —
        boylece EDGAR taramasi da calisir hale gelir.
        """
        k = Kimlik(symbol=symbol.upper(), name=name)
        t = ticker.strip().upper()

        if not self.yukle():
            k.status, k.method = "elle", "kullanici"
            k.note = f"kullanici atadi: {t} (SEC haritasi yuklenemedi, CIK yok)"
            return k

        aday = self._by_ticker.get(t)
        if not aday:
            k.status, k.method = "elle", "kullanici"
            k.note = (f"'{t}' SEC listesinde yok. Kayit tutuldu ama EDGAR "
                      "taramasi yapilamaz (ABD disi kotasyon olabilir).")
            return k

        k = self._dogrula(k, aday, "elle-dogrulandi")
        k.note = f"kullanici '{t}' atadi; SEC'de dogrulandi: {aday['name']}"
        return k

    @staticmethod
    def _dogrula(k: Kimlik, aday: dict, method: str) -> Kimlik:
        k.cik = aday["cik"]
        k.sec_ticker = aday["ticker"]
        k.sec_name = aday["name"]
        k.exchange = aday["exchange"]
        k.status = "dogrulandi"
        k.method = method
        return k
