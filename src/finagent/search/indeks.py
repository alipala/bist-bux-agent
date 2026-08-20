"""
Gomme indeksleme — `Gomme` ile `Database` arasindaki tek koprü.

Ayri bir modul cunku iki tarafi da tanimasi gereken TEK yer burasi:
`gomme.py` veritabanini bilmez, `db.py` Ollama'yi bilmez. Ikisini
birbirine baglayan kod bir ucune sizarsa, o uc digerini test etmeden
degistirilemez hale gelir.
"""

from __future__ import annotations

import logging
import time

from .gomme import Gomme

__all__ = ["gomme_indeksle"]

log = logging.getLogger(__name__)


def gomme_indeksle(db, gomme: Gomme, *, limit: int | None = None) -> dict:
    """
    Gommesi eksik (ya da BASKA modelden gelen) satirlari indeksler.

    IDEMPOTENT: "eksik" kumesi her cagrida veritabanindan yeniden
    hesaplaniyor, yani iki kez calistirmak ikinci seferde hicbir sey
    yapmaz. Yarida kesilirse yazilanlar yerinde kalir ve sonraki kosu
    kaldigi yerden devam eder — tek islemde yazmak, 156 satirin
    150'sini uretip Ollama'nin takilmasi halinde HEPSINI cope atardi.

    KIRPILAN SATIR SAYILIYOR VE DONULUYOR. Model ~4616 karakterde
    sessizce kesiyor (olculdu); cagiran bunu raporlayabilsin diye
    sayi burada uretiliyor. Sessiz kayip beyan edilmeyen kayiptir.
    """
    eksik = db.sohbet_gomme_eksikler(gomme.model, limit=limit)
    rapor = {"eksik": len(eksik), "yazilan": 0, "kirpilan": 0,
             "sure_sn": 0.0, "model": gomme.model}
    if not eksik:
        return rapor

    rapor["kirpilan"] = sum(1 for r in eksik if Gomme.kirpildi(r["metin"]))
    basla = time.perf_counter()
    vektorler = gomme.belgeler([r["metin"] for r in eksik])
    rapor["yazilan"] = db.sohbet_gomme_yaz(
        zip((r["id"] for r in eksik), vektorler), gomme.model)
    rapor["sure_sn"] = round(time.perf_counter() - basla, 2)

    log.info("gomme indeksi: %s satir yazildi (%s kirpildi) %.2f sn — model %s",
             rapor["yazilan"], rapor["kirpilan"], rapor["sure_sn"], gomme.model)
    return rapor
