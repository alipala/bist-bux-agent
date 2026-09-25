"""
IBKR GERCEK GETIRI (IBKR MCP Faz 3) — Portfolio Analyst TWR serisi.

Bulut baglayicisindan (`get_pa_performance_all_periods`) gunde bir cekilir;
gunluk getiri turetilip `hesap_getirisi`ne yazilir, gecmis gun revizyonlari
sayilir. Hesap ve ozet mantigi `ibkr/getiri.py`de; burasi yalnizca kabuk.

CPGW'de KARSILIGI YOK: bu toplayicinin tek kanali baglayici. Baglayici
yoksa hata metni `mcp_kanal`in sebebe gore cozumu soyleyen mesajidir
(`BaglayiciYokHatasi`), sessiz bos degil.
"""
from __future__ import annotations

import logging

from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

HESAP = "ibkr"


class IbkrGetiriCollector(BaseCollector):
    name = "ibkrgetiri"
    needs_browser = False

    def collect(self, _cagir=None) -> CollectorResult:
        from ..ibkr import mcp_kanal
        from ..ibkr.getiri import pa_gunluk, yaz
        from ..ibkr.istemci import IbkrHatasi

        if not self.s.get("ibkr.acik"):
            return CollectorResult(self.name, "skipped", 0, "ibkr.acik kapali")
        try:
            r = (_cagir or mcp_kanal.cagir)("get_pa_performance_all_periods")
        except IbkrHatasi as e:
            return CollectorResult(self.name, "error", 0,
                                   f"{type(e).__name__}: {str(e)[:400]}")
        try:
            pa = pa_gunluk(r.veri)
        except ValueError as e:
            return CollectorResult(self.name, "error", 0, f"PA yaniti okunamadi: {e}")
        s = yaz(self.db, HESAP, pa)
        not_ = (f"{len(pa['satirlar'])} gun ({pa['donem']}, {pa['olcu']}, "
                f"{pa['para_birimi']}), yeni {s['yeni']}, revize {len(s['revize'])}")
        return CollectorResult(self.name, "ok", len(pa["satirlar"]), not_,
                               data={"revize": len(s["revize"])})
