"""
Midas — pozisyonlar ekran goruntusu kanalindan gelir.

Midas'in da web arayuzu YOK (2026-08-14 dogrulandi):
  * www.getmidas.com/giris -> 404
  * web.getmidas.com       -> Cloudflare 1016 (origin DNS yok)
  * app.getmidas.com       -> yalnizca mobil deep-link yonlendiricisi
  * tum CTA'lar App Store / Google Play

BUX'tan farki: Midas public bir enstruman katalogu da yayinlamiyor, bu yuzden
cekilecek hicbir sey yok. Portfoy icin Telegram ekran goruntusu akisini kullan:
    python run.py bot

Collector kayitli kaliyor ki `collect` calistirildiginda kullanici sessizce
eksik veri degil, NEDENINI aciklayan bir satir gorsun.
"""
from __future__ import annotations

from .base import BaseCollector, CollectorResult


class MidasCollector(BaseCollector):
    name = "midas"
    needs_browser = False

    def collect(self) -> CollectorResult:
        return CollectorResult(
            self.name, "skipped", 0,
            "Midas mobil-only (web arayuzu yok) -> pozisyonlari `python run.py bot` "
            "ile ekran goruntusu gondererek guncelle",
        )
