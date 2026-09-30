"""
JEV (TypeSafe "System One") istemcisi — ince HTTP katmani.

Jev metin URETMEZ: bir `state` ve tipli sorular (Choice / Score / Noul)
alir, olasilik + `confidence` doner. Bu depoda NE SORULDUGU burada DEGIL,
`research/haber_jev.py`de; bu modul yalnizca tasir.

NEDEN RESMI SDK DEGIL
---------------------
`typesafe-sdk` 0.7.x `httpx2` ve `pydantic>=2.12` istiyor. Canli venv'de
`claude-agent-sdk` pydantic'e bagli; surum yukseltmesinin onu etkileyip
etkilemedigi OLCULMEDI. Tek uclu bir API icin o riski almaya degmez:
`httpx` zaten bagimlilik.

MODEL SABIT, TAKMA AD DEGIL
---------------------------
`jev-latest` yeni surum ciktiginda HABERSIZ kayar; esikler (0.80, 0.60)
jev-1.13.0'da olculdu. Surum degisikligi bilincli bir adim olmali:
yeni surumde olcum tekrarlanir, sonra buradaki sabit degisir.

HATA SINIFLARI AYRI — cagiran farkli davranmali
-----------------------------------------------
  JevYok       : anahtar tanimli degil -> kaynak "atlandi", ariza degil.
  JevGecersiz  : 422, istek bicimi hatali -> O KALEM bir daha denenmez.
  JevHatasi    : ag / 401 / 429 / 5xx, denemeler tukendi -> kosu erken
                 durur; kalemler bir sonraki kosuda yeniden denenir.
Hicbiri "bu haber onemsiz" anlamina GELMEZ. Siniflandirilamayan haber
siniflandirilmamis kalir; okuyan taraf bunu ayri soyler.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
ANAHTAR_ENV = "TYPESAFE_API_KEY"

# 429 hiz siniri, 529 asiri yuk (API belgesi), 5xx gecici sunucu hatasi.
TEKRAR_DURUMLARI = {429, 500, 502, 503, 504, 529}


class JevHatasi(Exception):
    """Ag / yetki / servis hatasi — denemeler tukendi."""


class JevYok(JevHatasi):
    """TYPESAFE_API_KEY tanimli degil."""


class JevGecersiz(JevHatasi):
    """422: istek bicimi hatali. Ayni istegi tekrarlamak ise yaramaz."""


def anahtar() -> str | None:
    return (os.getenv(ANAHTAR_ENV) or "").strip() or None


def _varsayilan_post(govde: dict, anahtar_: str, zaman_asimi: float):
    import httpx
    return httpx.post(URL, json=govde, timeout=zaman_asimi,
                      headers={"Authorization": f"Bearer {anahtar_}"})


def sor(state, sorular: dict, *, model: str = MODEL, zaman_asimi: float = 30.0,
        deneme: int = 4, _post=None, _uyu=time.sleep) -> dict:
    """
    Tek istek: `state` + `sorular` -> ham yanit sozlugu
    ({"model", "answers", "usage"}).

    `_post(govde, anahtar, zaman_asimi)` ve `_uyu` testler icin enjekte
    edilir; `_post` `.status_code`, `.json()`, `.headers`, `.text` tasiyan
    bir nesne donmeli ya da ag hatasi firlatmali.
    """
    a = anahtar()
    if not a:
        raise JevYok(f"{ANAHTAR_ENV} tanimli degil")
    post = _post or _varsayilan_post
    govde = {"state": state, "model": model, "questions": sorular}
    son = ""
    for i in range(deneme + 1):
        try:
            r = post(govde, a, zaman_asimi)
        except Exception as e:                       # noqa: BLE001 — ag hatasi
            son = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                return r.json()
            son = f"HTTP {r.status_code}: {(r.text or '')[:200]}"
            if r.status_code == 422:
                raise JevGecersiz(son)
            if r.status_code not in TEKRAR_DURUMLARI:
                raise JevHatasi(son)
        if i < deneme:
            _uyu(min(2 ** i, 16))
    raise JevHatasi(f"{deneme + 1} denemede basarisiz — {son}")
