"""Collector taban sinifi + ortak yardimcilar."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# TOPLAMA SON TARIHI (epoch sn). Zamanli kosu (`run_kosu.sh`) bunu
# `kabuk butcesi - panel butcesi - teslimat payi` olarak kurar: toplama
# panelin ve mesajin suresine DOKUNAMAZ.
#
# NEDEN (olculdu 5 Eki): nabiz toplamasi 4106 sn surdu (son uc haftanin
# en kotusu 3011), kabuk 4800 sn'de sureci oldurdu ve panel, mesaj ve
# iz HIC uretilmedi. Toplamanin kendi siniri yoktu; uzayan toplama
# gecenin TUM yorumunu goturuyordu. Yarim veri, hic yorum olmamasindan
# iyidir. Ortam degiskeni yoksa (elle kosu, test) sinir YOK.
TOPLAMA_BITIS_ENV = "TOPLAMA_BITIS_TS"

# Kesilen/atlanan collector'un notuna giren SABIT ifade. Nabiz mesaji
# bu kosunun kayitlarinda BU METNI arar — ikinci bir liste tutulmaz.
TOPLAMA_SURESI_NOTU = "TOPLAMA SURESI DOLDU"


def toplama_kalan_sn(simdi: float | None = None) -> float | None:
    """Toplama son tarihine kalan saniye; sinir yoksa None.

    Bozuk deger SINIRSIZ sayilir ve loglanir: bozuk bir ortam degiskeni
    yuzunden butun toplamayi atlamak, hic sinir olmamasindan kotudur.
    """
    import os
    ham = os.environ.get(TOPLAMA_BITIS_ENV)
    if not ham:
        return None
    try:
        bitis = float(ham)
    except ValueError:
        log.warning("%s gecersiz (%r) — toplama SINIRSIZ", TOPLAMA_BITIS_ENV, ham)
        return None
    return bitis - (time.time() if simdi is None else simdi)


@dataclass
class CollectorResult:
    name: str
    status: str = "ok"          # ok | partial | error | skipped
    rows: int = 0
    error: str | None = None
    duration_ms: int = 0
    data: dict[str, Any] = field(default_factory=dict)


class BaseCollector:
    """Alt siniflar sadece `collect()` yazar; zamanlama/loglama burada."""

    name = "base"
    needs_browser = False

    def __init__(self, settings, db, browser=None):
        self.s = settings
        self.db = db
        self.browser = browser

    def collect(self) -> CollectorResult:  # pragma: no cover - abstract
        raise NotImplementedError

    @staticmethod
    def sure_doldu() -> bool:
        """Toplama son tarihi gectiyse True — uzun dongu her adimda sorar."""
        kalan = toplama_kalan_sn()
        return kalan is not None and kalan <= 0

    def run(self) -> CollectorResult:
        t0 = time.perf_counter()
        try:
            res = self.collect()
        except Exception as e:                      # noqa: BLE001
            log.exception("[%s] hata", self.name)
            res = CollectorResult(name=self.name, status="error", error=f"{type(e).__name__}: {e}")
        res.duration_ms = int((time.perf_counter() - t0) * 1000)
        self.db.log_collector_run(self.name, res.status, res.rows, res.duration_ms, res.error)
        icon = {"ok": "✓", "partial": "~", "skipped": "-", "error": "✗"}.get(res.status, "?")
        log.info("[%s] %s %s satir (%d ms) %s", self.name, icon, res.rows,
                 res.duration_ms, res.error or "")
        return res

    # --- strateji evreni ------------------------------------------------
    # BURADA, `prices`in ICINDE DEGIL — IKI COLLECTOR OKUYOR.
    #
    # Evrene fiyat serisi de (`prices`) conid de (`ibkrkimlik`) lazim ve
    # ikisi ayni soruyu soruyor: "strateji evreni kim?". Iki yere
    # yazilsaydi kopyalar AYRISIRDI — bu deponun en pahali dersi
    # (`ayni kural iki kopya`: LLY prices'ta duzeltildi, identity'deki
    # ikizi iki gun daha yasadi ve SEC dosyalamalarini sessizce dusurdu).
    def strateji_evreni(self) -> list:
        """
        `ibkr.strateji.endeksler` uyeleri — kapali ya da tanimsizsa BOS.

        `research_targets()`e GIRMEZ ve girmemeli:
        `collectors/indices.py`'nin KAPSAM KARARI notuna gore endeks
        uyeleri KATALOGA girer, ARASTIRMA HEDEFI olmaz (gunluk EDGAR +
        basin taramasi pahali). O gerekce fiyat serisini ve conid'i
        BAGLAMIYOR: Donchian 20/10 + 2N yalnizca OHLCV istiyor — haber,
        bilanco, EDGAR ve LLM cagrisi yok.

        TANIMSIZ ile YANLIS TANIMLI AYRI SEYLER. Blok hic yoksa strateji
        motoru kurulmamis demektir ve sessizce bos donulur; blok VARSA
        bicimi dogrulanir ve hatasi SOYLENIR — bozuk bir ayarda sessizce
        bos evrene dusmek, motoru "kosuyor" gosterirdi.

        Para birimi SUZULMUYOR, bilerek: bir enstrumanin para birimini
        seriyi cekmeden ONCE bilmiyoruz (kaynagin kendi beyanindan
        geliyor, bkz. `yahoo_gunluk`). Para birimi kapisi strateji
        tarafinda, seri ELDEYKEN uygulanir.
        """
        if self.s.get("ibkr.strateji") is None:
            return []
        ayar = self.s.strateji_ayari(self.db)
        if not ayar["enabled"]:
            return []
        return self.db.endeks_uyeleri(ayar["endeksler"])

    # --- selector yardimcisi -------------------------------------------
    def sel(self, key: str) -> str | None:
        v = self.s.sel(f"{self.name}.{key}")
        return None if v in (None, "", "TODO") else v

    def require_selectors(self, *keys: str) -> bool:
        missing = [k for k in keys if not self.sel(k)]
        if missing:
            log.warning(
                "[%s] selector eksik: %s -> `python run.py discover --site %s` calistir.",
                self.name, ", ".join(missing), self.name,
            )
            return False
        return True


# ----------------------------------------------------------------------
_NUM_CLEAN = re.compile(r"[^0-9,.\-]")


def parse_number(text: str | None) -> float | None:
    """
    '1.234,56 TL' -> 1234.56   |   '1,234.56' -> 1234.56
    '%-3,21' -> -3.21          |   '—', '', None -> None

    Hem TR (1.234,56) hem EN (1,234.56) formatini ayirt eder:
    son gorulen ayirici ondalik kabul edilir.
    """
    if text is None:
        return None
    t = _NUM_CLEAN.sub("", str(text).strip())
    if not t or t in {"-", ",", "."}:
        return None

    neg = t.startswith("-")
    t = t.lstrip("-")

    last_comma, last_dot = t.rfind(","), t.rfind(".")
    if last_comma > last_dot:            # TR: ondalik virgul
        t = t.replace(".", "").replace(",", ".")
    elif last_dot > last_comma:          # EN: ondalik nokta
        t = t.replace(",", "")
    else:                                # tek tur ayirici yok
        t = t.replace(",", "").replace(".", "")

    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def extract_symbols(text: str, watchlist: list[str]) -> list[str]:
    """
    Metin icinde watchlist sembollerini kelime siniriyla arar.

    ESLESME BUYUK-KUCUK HARFE DUYARLI, bilerek. Onceden metin
    `upper()` edilip aranıyordu ve bu, gundelik kelimeyle cakisan her
    sembolu yanlis pozitife aciyordu:

        "gram altin rekor kirdi"  -> GRAM  (Toncoin'in yeni sembolu)
        "adalet", "sol goruş"     -> ADA, SOL

    29 BIST sembolu de gundelik Turkce kelimeyle cakisiyor (HEDEF, KENT,
    LIDER, BIZIM...). Basliklarda enstruman TICKER olarak yaziliyor ve
    ticker daima BUYUK HARF: "SASA'dan sermaye artirimi", "(NVDA)".
    Kucuk harfli gecis sembol degil kelimedir.
    """
    if not text:
        return []
    return [s for s in watchlist
            if re.search(rf"\b{re.escape(s.upper())}\b", text)]


# SIRKET ADINDAN SEMBOL CIKARMA DENENDI VE TERK EDILDI (2026-08-20).
#
# Amac gercekti: kanit seviyesindeki (kademe 1-2) haberin %93'u hicbir
# sembole bagli degildi — son 1 gunde 249 haberin yalnizca 18'i. "Turk
# Hava Yollari ucak siparisi verdi" diyen bir haber, THYAO ticker olarak
# gecmedigi icin hicbir yere baglanmiyordu.
#
# Ad -> sembol haritasi kuruldu ve OLCULDU. Iki turde de yanlis cikti:
#   5 harf esigi : `align` (Align Technology) "buyukelci atandi"
#                  haberine, `state`/`street` (State Street) rastgele
#                  Ingilizce metne baglandi. 400 haberin 295'i eslesti
#                  ama cogu COPTU.
#   7 harf esigi : precision arttı, ama Turkce hala sizdi —
#                  `yukselen`->YKSLN, `aktuel`->RTALB, `trabzon`->TLMAN,
#                  `kuresel`->GLCVY. Turkce sirket adlari SIRADAN
#                  kelimelerden kuruluyor ve bir kelime listesi bunu
#                  kapatamaz.
#
# DERS: "bu haber hangi sirket hakkinda" ANLAMSAL bir sorudur, dizgi
# eslestirmesi degil. Yanlis sirkete baglamak hic baglamamaktan KOTUDUR
# — kullanici o hisseye bakar, ilgisiz cikar, katmana guveni gider.
# Cozum LLM tarafinda: derleyici baglanmamis kademe 1-2 haberleri
# oldugu gibi modele veriyor, model eslestirmeyi yapiyor. TICKER
# eslestirmesi (yukarida) kaliyor: yuksek isabetli ve ucuz.
