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
    """Metin icinde watchlist sembollerini kelime siniriyla arar."""
    if not text:
        return []
    up = text.upper()
    return [s for s in watchlist if re.search(rf"\b{re.escape(s)}\b", up)]
