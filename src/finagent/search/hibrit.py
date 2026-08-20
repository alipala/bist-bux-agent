"""
Hibrit siralama — Reciprocal Rank Fusion (RRF).

Iki arama yolu iki farkli hatayi yapiyor ve olculdu (altin kume,
2026-08-20, recall@3):

                    tam_kelime  parafraz  kelime_yok
    FTS5 trigram        100%       80%        20%
    gomme               100%       40%        50%

Leksik yol, kelimenin GECMEDIGI turu bulamaz; anlam yolu, kelimesi
gecen ama baglami farkli turlarda daha zayif. Ikisi ayni sirayi uretse
birlestirmenin anlami olmazdi — farkli olmalari, birlestirmenin
gerekcesidir.

NEDEN RRF, SKOR BIRLESTIRME DEGIL
---------------------------------
Iki yolun skorlari AYNI OLCEKTE DEGIL: `bm25` negatif ve sinirsiz,
kosinus [-1, 1]. Bunlari toplamak icin once normalize etmek gerekir ve
her normalize secimi (min-max, z-skor) sonuca kendi varsayimini
sokar — ustelik tek sorgunun sonuc kumesi uzerinde hesaplanan bir
min-max, sorgudan sorguya farkli davranir. RRF yalnizca SIRAYI kullanir,
skoru hic okumaz; olcek sorunu ortadan kalkar.

`K` = 60 standart baslangic degeri. Buyudukce ilk siralarin agirligi
azalir (1/61 ile 1/62 arasindaki fark, 1/2 ile 1/3 arasindakinden cok
daha kucuktur), yani K bir "ilk siralara ne kadar guveniyoruz"
ayaridir. Altin kume sonucu iyilesmiyorsa OYNANMAYACAK — bir sabiti
olcume gore ayarlamak, olcumu kendi cevabina uydurmaktir.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

__all__ = ["rrf", "RRF_K"]

RRF_K = 60


def rrf_puanlari(*siralamalar: Iterable[int], k: int = RRF_K) -> dict[int, float]:
    """
    RRF puanlari — FORMULUN TEK YERI.

    `rrf()` bunu cagiriyor. Onceki surumde formul iki fonksiyonda
    AYRI AYRI yaziliydi ve kasitli bozma bunu yakaladi: `rrf`'teki
    `1/(k+sira)` ifadesi `1/sira` yapildiginda test GECTI, cunku
    dogrulama `rrf_puanlari`'na bakiyordu ve o dokunulmamisti. Ayni
    gercegin iki yerde beyan edilmesi bu projenin tekrar eden kusur
    sinifi; test eklemek yerine ikinci beyan KALDIRILDI.
    """
    if k <= 0:
        raise ValueError(f"RRF k pozitif olmali, {k!r} verildi")
    puan: dict[int, float] = defaultdict(float)
    for liste in siralamalar:
        for sira, kimlik in enumerate(liste, start=1):
            puan[kimlik] += 1.0 / (k + sira)
    return dict(puan)


def rrf(*siralamalar: Sequence[int], k: int = RRF_K) -> list[int]:
    """
    Birden cok siralamayi tek siralamada birlestirir.

    Her liste ID'lerin ALAKA SIRASI olmali (en iyisi basta). Bir ID kac
    listede gecerse o kadar cok puan toplar; ustte gectikce daha cok.

    BOS LISTE GECERLI BIR GIRDI: bir yol hic sonuc bulamamis olabilir
    ve bu, digerinin sonucunu gecersiz kilmaz. Ama liste EKSIK degil
    BOS gelmeli — cagiran bir hatayi bos listeye cevirirse, hibrit onu
    "o yol bir sey bulamadi" diye okur ve arizayi sessizce yutar.
    """
    puan = rrf_puanlari(*siralamalar, k=k)
    # Esitlikte ilk listede once gecen kazanir: sozluk ekleme sirasini
    # korur ve Python'un siralamasi KARARLI. Belirsiz bir esitlik
    # bozma, olcumu kosudan kosuya oynatirdi.
    return sorted(puan, key=lambda i: puan[i], reverse=True)
