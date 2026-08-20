"""
Turkce metin normalizasyonu — YALNIZCA leksik taraf icin.

Iki fonksiyon, iki ayri is. Ikisi de saf: I/O yok, bagimlilik yok,
durum yok. Ayni girdi her zaman ayni cikti.

  tr_lower  Turkce kucultme. Python'un `.lower()`'i Turkce'de YANLIS.
  tr_fold   Sapka katlama. Kullanicinin yazamadigi harfi arayabilmesi icin.

NEREDE KULLANILIR — VE NEREDE KULLANILMAZ
-----------------------------------------
FTS5 indeksine yazilan metin de, FTS5'e giden sorgu da `tr_fold(tr_lower(x))`
den gecer. Ikisinden biri gecmezse sapkasiz sorgu sapkali belgeyi ISKALAR;
bu, iki tarafi ayri normalize etmenin klasik sessiz hatasidir.

GOMME TARAFINA HAM METIN GIDER. embeddinggemma sapkali Turkce'yi zaten
biliyor; katlamak bilgi yok eder. Olculdu (2026-08-20, 768 boyut,
L2-normalize vektorler):

    "Altin hesabimda 181 gram var..." vs sapkali hali   kosinus 0.7977
    ayni cumle           vs  "The quick brown fox..."   kosinus 0.1332

Yani sapka farki gommede 0.20'lik gercek bir kayip — ama alakasiz metin
0.13'te kaliyor, yani gomme sapkasiz sorguyu yine de tanir. Katlamak bu
tolerans yerine bilgi kaybi koyardi.
"""

from __future__ import annotations

import unicodedata

__all__ = ["tr_lower", "tr_fold", "leksik"]


# `İ` -> `i`, `I` -> `ı`. Kucultmeden ONCE yapilir; sonra yapilamaz cunku
# kucultme bilgiyi coktan yok etmis olur.
_BUYUK_ESLESME = {
    "İ": "i",   # İ  LATIN CAPITAL LETTER I WITH DOT ABOVE
    "I": "ı",  # I -> ı
}

# Sapka katlama tablosu. Iki grup:
#
#   1) Turkce'ye ozgu alti harf — belgede istenen liste.
#   2) DUZELTME cifti: â î û. Belgede yoktu, arsiv olcumu ekletti.
#
# Ikinci grubun gerekcesi sayida: `sohbet_kaydi`'nda (2026-08-20, 156 satir)
#
#     'â' 222 kez        'kâr' 121 kez  <->  'kar ' 2 kez
#     'î'   4 kez        'û'     0 kez
#
# Arsiv "kâr/zarar" diye yaziyor. Telefon klavyesinde `â` yazan yok;
# kullanici "kar zarar" yazar. Katlanmazsa 121 kayit ISKALANIR. `û`
# arsivde hic gecmiyor ama tablonun eksik kalmasi icin sebep degil —
# yarin gecerse ayni tuzak kurulur.
#
# BUYUK HARFLER DE TABLODA. Sozlesme `tr_fold(tr_lower(x))` olsa da
# `tr_fold` tek basina cagrilinca sessizce yarim is yapmamali.
_KATLAMA = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
    # `ı`nin buyugu `I`, `i`nin buyugu `İ` — Turkce'de capraz. Katlamada
    # `İ` -> `I` olmali; `I` zaten ASCII, tabloda isi yok.
    "Ç": "C", "Ğ": "G", "İ": "I", "Ö": "O", "Ş": "S", "Ü": "U",
    "â": "a", "î": "i", "û": "u",
    "Â": "A", "Î": "I", "Û": "U",
})


def tr_lower(s: str) -> str:
    """
    Turkce kucultme.

    PYTHON'UN `.lower()`'I IKI AYRI SEKILDE BOZUYOR — ikisi de olculdu:

        "IŞIK".lower()      -> "işik"      ('ışık' olmaliydi)
        "İSTANBUL".lower()  -> "i̇stanbul"  ('istanbul' olmaliydi)

    Ikincisi daha sinsi: gorunuste dogru ama 8 harf icin 9 KOD NOKTASI
    uretiyor — `i` (U+0069) + BIRLESIK NOKTA (U+0307). Kullanicinin
    yazdigi duz `i` ile ASLA esleşmez, ve trigram indeksinde uc harflik
    her pencereyi kaydirir. Ekranda fark gorunmedigi icin hata
    "arama neden bulmuyor" diye ortaya cikar, kok neden gorunmez.

    SIRA ONEMLI:
      1. NFC — `I` + U+0307 dizisi tek `İ`ye BIRLESIR. Metin dis
         kaynaktan (Telegram, ekran goruntusu OCR'i, kopyala-yapistir)
         ayrisik gelebilir; olculdu, geliyor da.
      2. Buyuk harf eslemesi — `İ`->`i`, `I`->`ı`.
      3. `.lower()` — geri kalan her sey icin dogru calisir.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    for buyuk, kucuk in _BUYUK_ESLESME.items():
        s = s.replace(buyuk, kucuk)
    return s.lower()


def tr_fold(s: str) -> str:
    """
    Sapka katlama: `ç->c ğ->g ı->i ö->o ş->s ü->u â->a î->i û->u`.

    Amac tek: kullanicinin YAZAMADIGI harfi aramasini mumkun kilmak.
    "altin" yazan "altını" bulmali. Kayip bilincli — `açık`/`acık`
    ayrimi leksik tarafta feda edilir, gomme tarafinda korunur.
    """
    if not s:
        return ""
    return s.translate(_KATLAMA)


def leksik(s: str) -> str:
    """
    Leksik boru hatti — FTS5'e giden HER metin ve HER sorgu bundan gecer.

    Iki tarafi ayri ayri `tr_fold(tr_lower(...))` yazmak yerine tek ad
    var, cunku bu ikisinin AYNI olmasi bir dogruluk kosulu; iki cagri
    yerinde birbirinden bagimsiz degisebilir, tek fonksiyon degisemez.
    """
    return tr_fold(tr_lower(s))
