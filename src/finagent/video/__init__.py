"""
Video katmani — YouTube transkripti ve Instagram reel'i.

IKI MODUL, IKI AYRI ISIM UZAYI
------------------------------
Her iki modulde de `getir`, `kimlik_coz` ve `KADEME` var ve ISIMLERI
AYNI. Duz `from .instagram import *` yapmak, YouTube'un `getir`ini
sessizce Instagram'inkiyle degistirirdi — cagiran taraf hicbir hata
gormeden YANLIS modulu kullanirdi.

Bu yuzden YouTube adlari (eski, yerlesik) CIPLAK kaliyor, Instagram
adlari `ig_` onekli veriliyor. Modulun kendisi de `instagram` adiyla
disa aciliyor ki acik cagri (`instagram.getir(...)`) mumkun olsun.
"""
from . import instagram
from .instagram import InstagramHatasi
from .instagram import KADEME as IG_KADEME
from .instagram import getir as ig_getir
from .instagram import hazir as ig_hazir
from .instagram import kimlik_coz as ig_kimlik_coz
from .transkript import (KADEME, TranskriptHatasi, diller, getir,
                         kimlik_coz)

__all__ = [
    # YouTube — eski, yerlesik adlar korunuyor
    "KADEME", "TranskriptHatasi", "diller", "getir", "kimlik_coz",
    # Instagram
    "instagram", "InstagramHatasi", "IG_KADEME",
    "ig_getir", "ig_hazir", "ig_kimlik_coz",
]
