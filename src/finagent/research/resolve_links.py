"""
Google News yonlendirme linklerini GERCEK yayinci URL'sine cevirir.

NEDEN
-----
Google News RSS, haberi `news.google.com/rss/articles/CBMi...` bicimindeki
opak bir baglantiyla veriyor. Rapor bu linki kaynak olarak gosterirse
"dogrulanabilir referans" sarti karsilanmaz: tiklayan kisi yayincinin
sayfasini degil bir yonlendiriciyi gorur.

Cozum denemeleri:
  * HTTP redirect takibi -> CALISMIYOR (sayfa JS/Angular ile yonlendiriyor)
  * Token'i base64 cozme -> CALISMIYOR (icinde URL yok, opak sunucu kimligi:
    'AU_yqLMz_0gl...')
  * Tarayici ile acip son URL'yi okumak -> CALISIYOR (~1.2 sn/link)

Proje zaten tarayici otomasyonu uzerine kurulu; ucuncu yol dogal olani.

AB CEREZ DUVARI
---------------
Avrupa IP'sinden ilk istek consent.google.com'a dusuyor ve link cozulmuyor.
Onay cerezleri (SOCS/CONSENT) onceden yazilarak bu adim atlaniyor.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

GOOGLE_ONAY_CEREZLERI = [
    {"name": "SOCS",
     "value": ("CAISNQgQEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjQwNjE2"
               "LjA2X3AwGgJlbiADGgYIgLC-tgY"),
     "domain": ".google.com", "path": "/"},
    {"name": "CONSENT", "value": "YES+cb.20240101-07-p0.en+FX+410",
     "domain": ".google.com", "path": "/"},
]


def google_link_mi(url: str | None) -> bool:
    return bool(url) and "news.google.com" in url


def resolve_batch(browser, urls: list[str], timeout_ms: int = 20000,
                  son_tarih: float | None = None,
                  durum: dict | None = None) -> dict[str, str]:
    """
    Yonlendirme linklerini toplu cozer. {eski_url: yeni_url}

    Cozulemeyenler sonuca KONULMAZ — cagiran taraf eski linki korur.
    Yanlis bir URL uretmektense yonlendirici linki birakmak daha durustur.

    `son_tarih` (epoch sn) verilirse o an gelince DURUR; cozulmeyen
    linkler ayni kuralla eski haliyle kalir. Sayfa basina 20 sn'ye kadar
    suren bu dongu 5 Eki'de nabzin suresini yiyen kisimdi. Kac link
    cozulmeden kaldi `durum["kesilen"]`e yazilir (saate bakip tahmin
    edilmesin diye).
    """
    import time
    if not urls:
        return {}

    try:
        browser.context.add_cookies(GOOGLE_ONAY_CEREZLERI)
    except Exception as e:                            # noqa: BLE001
        log.warning("Google onay cerezleri yazilamadi: %s", e)

    out: dict[str, str] = {}
    for i, url in enumerate(urls):
        if son_tarih is not None and time.time() >= son_tarih:
            log.warning("[link] sure siniri — %d/%d link cozulmeden kaldi",
                        len(urls) - i, len(urls))
            if durum is not None:
                durum["kesilen"] = len(urls) - i
            break
        try:
            with browser.page() as pg:
                pg.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                for _ in range(25):
                    if "google.com" not in pg.url:
                        break
                    pg.wait_for_timeout(400)
                if "google.com" not in pg.url and pg.url.startswith("http"):
                    out[url] = pg.url.split("#")[0]
        except Exception as e:                        # noqa: BLE001
            log.debug("link cozulemedi (%s): %s", url[:60], e)
    log.info("[link] %d/%d yonlendirme cozuldu", len(out), len(urls))
    return out
