"""
TUR OLCUMU — bir sohbet turunun gercek maliyeti, SDK'nin BEYANINDAN.

NEDEN VAR (2026-08-31)
----------------------
Ali sordu: "hangi modeli kullaniyoruz, baglam penceresini nasil
yonetiyoruz?" Cevaplanamadi — hicbir yerde token sayaci YOKTU.
`data/bot.log` icinde 272 'token' eslesmesi vardi ve hepsi Python
traceback'lerindeki degisken adlariydi.

Oysa SDK bunu ZATEN donduruyor. `ResultMessage` su alanlari tasiyor:

    usage · total_cost_usd · model_usage · num_turns
    duration_ms · duration_api_ms · stop_reason · is_error

`chat.py`'nin akis dongusu `content` alani olmayan mesaji `continue`
ile atliyordu, yani veri tam oradan gecip cope gidiyordu. Bu deponun
en sik kalibi: KAYNAK VAR, YAZIM YOLU YOK — dolum fiyatinda da,
seans saatlerinde de ayni sey olmustu.

HESAPLAMA YOK, SAYIM YOK
------------------------
Bu modul token TAHMIN ETMIYOR. Karakter/4 gibi bir kestirim uretmek,
olculmus bir sayinin yanina uydurma bir sayi koymak olurdu. Yalnizca
SDK ne diyorsa o okunuyor; alan yoksa None kaliyor.

NONE ILE SIFIR AYRI SEYDIR
--------------------------
Bir alan gelmediyse NULL yaziliyor, 0 DEGIL. "Olculmedi" ile "sifirdi"
karistirilirsa ortalamalar sessizce bozulur — bu depoda ayni hata
`dolum_fiyat` ve `sure_sn`de iki kez yasandi.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# `usage` sozlugundeki adlar -> bizim kolonlar. SDK bu adlari
# Anthropic API'sinden aynen tasiyor.
USAGE_ESLEME = (
    ("giris_token", "input_tokens"),
    ("cikis_token", "output_tokens"),
    ("cache_yazma", "cache_creation_input_tokens"),
    ("cache_okuma", "cache_read_input_tokens"),
)


def sonuc_mesaji_mi(mesaj: Any) -> bool:
    """
    Bu, turun SONUC mesaji mi?

    Sinif ADINA bakilmiyor, ALANLARA bakiliyor: SDK surumler arasinda
    sinif adini degistirebilir ama `usage` + `duration_ms` ikilisi bu
    mesaji benzersiz yapiyor. Ad kontrolu sessizce bozulurdu.
    """
    return (hasattr(mesaj, "usage")
            and hasattr(mesaj, "duration_ms")
            and getattr(mesaj, "content", None) is None)


def _sayi(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ondalik(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def turdan_olcum(mesaj: Any) -> dict:
    """
    `ResultMessage`ten olcum sozlugu. Okunamayan alan None kalir.

    ASLA ISTISNA FIRLATMAZ: olcum bir YARDIMCI katman. Sayaç patlayip
    kullanicinin cevabini dusurmesi, olculmemis olmaktan cok daha kotu
    olurdu. (`_mutabakat_kosumu`daki kural 1 ile ayni gerekce.)
    """
    out: dict = {}
    try:
        ham = getattr(mesaj, "usage", None)
        if isinstance(ham, dict):
            for kolon, anahtar in USAGE_ESLEME:
                out[kolon] = _sayi(ham.get(anahtar))
        elif ham is not None:
            for kolon, anahtar in USAGE_ESLEME:
                out[kolon] = _sayi(getattr(ham, anahtar, None))

        out["maliyet_usd"] = _ondalik(getattr(mesaj, "total_cost_usd", None))
        out["sure_ms"] = _sayi(getattr(mesaj, "duration_ms", None))
        out["api_sure_ms"] = _sayi(getattr(mesaj, "duration_api_ms", None))
        out["tur_sayisi"] = _sayi(getattr(mesaj, "num_turns", None))
        durdurma = getattr(mesaj, "stop_reason", None)
        out["durdurma"] = str(durdurma) if durdurma is not None else None
        hatali = getattr(mesaj, "is_error", None)
        out["hatali"] = 1 if hatali else (0 if hatali is not None else None)
    except Exception as e:                            # noqa: BLE001
        log.warning("[olcum] sonuc mesaji okunamadi: %s", e)
    return out


def log_satiri(o: dict) -> str:
    """
    Tek satirlik ozet — `bot.log` icin.

    OLCULMEYEN ALAN '?' ile gosteriliyor, 0 ile DEGIL. Log'a bakan
    insan "cache hic kullanilmadi" ile "cache olculmedi" arasindaki
    farki gormeli.
    """
    def g(ad, bicim="{}"):
        v = o.get(ad)
        return bicim.format(v) if v is not None else "?"

    return (f"[olcum] giris={g('giris_token')} cikis={g('cikis_token')} "
            f"cache_oku={g('cache_okuma')} cache_yaz={g('cache_yazma')} "
            f"maliyet={g('maliyet_usd', '{:.4f}')}$ "
            f"sure={g('sure_ms')}ms arac_turu={g('tur_sayisi')} "
            f"| baglam: sistem={g('sistem_krk')} istem={g('istem_krk')} "
            f"pencere={g('pencere_krk')}krk/{g('pencere_tur')}tur"
            f"{' SOGUK' if o.get('soguk_baslama') else ''} "
            f"| arac={g('arac_sayisi')}")
