"""
Surec duzeyi kaynak sinirlari.

NEDEN VAR (olculdu 2026-09-08)
------------------------------
macOS'ta launchd ile baslayan surecin acik dosya siniri 256 (`launchctl
limit maxfiles` -> soft 256, hard unlimited). Toplama zinciri tek
surecte yirmi collector kosturuyor: yuzlerce HTTP cagrisi, Playwright,
sqlite, log dosyalari. 3 Eylul 2026 gecesi `yfinance`in threads=True
sizintisi (her toplu cagri ~40 tanitici) siniri doldurdu ve surec
`midas`ta "OSError: [Errno 24] Too many open files" ile oldu — hatayi
LOGLAYAN kod da ayni hatayla patladi, `prices` ve `strateji_fiyat`
hic calismadi, strateji motoru dunku barla tarama yapti.

Sizinti kapatildi (`threads=False`). Burasi IKINCI KEMER: sinir hard
limite kadar (varsayilan 4096) yukseltilir. Yukseltilemezse SESSIZ
kalinmaz, loglanir ve kosu devam eder — sinir dusukken calismak,
hic calismamaktan iyidir.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

VARSAYILAN_HEDEF = 4096


def dosya_siniri_yukselt(hedef: int = VARSAYILAN_HEDEF) -> tuple[int, int]:
    """
    RLIMIT_NOFILE yumusak sinirini `hedef`e (ya da sert sinire) cikarir.
    Doner: (eski_yumusak, yeni_yumusak). Degisiklik yoksa ikisi esit.
    """
    try:
        import resource
    except ImportError:                                   # Windows
        return 0, 0
    try:
        yumusak, sert = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError) as e:
        log.warning("[sistem] dosya siniri okunamadi: %s", e)
        return 0, 0
    if yumusak >= hedef:
        return yumusak, yumusak
    yeni = hedef if sert == resource.RLIM_INFINITY else min(hedef, sert)
    if yeni <= yumusak:
        return yumusak, yumusak
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (yeni, sert))
    except (ValueError, OSError) as e:
        log.warning("[sistem] dosya siniri %d -> %d yukseltilemedi: %s",
                    yumusak, yeni, e)
        return yumusak, yumusak
    log.info("[sistem] dosya siniri %d -> %d", yumusak, yeni)
    return yumusak, yeni
