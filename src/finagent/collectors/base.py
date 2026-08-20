"""Collector taban sinifi + ortak yardimcilar."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


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
