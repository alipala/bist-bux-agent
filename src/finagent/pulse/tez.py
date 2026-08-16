"""
TEZ GECERSIZLESME KOSULU — dar bir gramer ve onu uygulayan ayristirici.

NEDEN AL/SAT SINYALI DEGIL DE BU
--------------------------------
Al/sat sinyali bir TAHMINDIR; isabet orani olculmeden durustce sunulamaz
ve su an olculmus isabet YOK (ilk puanlama 20 Agustos civari). Tez
bozulmasi ise bir KOSUL KONTROLUDUR: sistemin daha once ACIKCA beyan
ettigi bir esigin gerceklesip gerceklesmedigini soyler. Tahmin
icermedigi icin isabet orani bilinmeden de durusttur.

Kullanici acisindan karara yakinlik ayni, epistemik yuk cok daha dusuk.

NEDEN SERBEST METIN DEGIL
-------------------------
"fiyat duserse" gibi bir kosul KONTROL EDILEMEZ. Kabul edilirse alan
dolar, kontrol her gun calisir, hep False doner ve kullanici "tez hala
gecerli" saniyor olur. Yani uydurulmus bir kosul, HIC kosul olmamasindan
KOTUDUR — bu, projenin olu konfigurasyon sinifinin yeni bir bicimi.

Gramere uymayan kosul KAYDEDILMEZ ve reddedilme sayilir.

ALAN-ALAN KARSILASTIRMASI YOK
-----------------------------
`close < sma50` ilk surumde kabul edilmiyor: iki taraf da zamana gore
degistigi icin "bozuldu" ani belirsizlesir ve esik etrafinda salinan bir
kagit her gun alarm uretir. Sabit esik yeterli ve dogrulanabilir.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Kosulda kullanilabilecek alanlar. HAKEM prompt'undaki gramer bu
# listeden URETILIYOR (bkz. agents.py) — elle yazilsaydi beyan ile
# gercek sessizce ayrisirdi, ki bu projenin tekrar eden kusur sinifi.
ALANLAR = ("close", "rsi14", "sma20", "sma50", "sma200", "hacim_kat", "car_t")

KOSUL = re.compile(r"^(" + "|".join(ALANLAR) + r")\s*([<>])\s*(-?\d+(?:\.\d+)?)$")


def gramer_metni() -> str:
    """HAKEM prompt'una gomulecek gramer aciklamasi — TEK KAYNAK."""
    return (
        "<alan> <op> <sayi>   ornek: close < 1520 · rsi14 > 75\n"
        f"  alan : {' | '.join(ALANLAR)}\n"
        "  op   : < veya >\n"
        "  sayi : ondalik sayi, PARA BIRIMI YAZMA (birim alanin kendisinden gelir)\n"
        "  Alan-alan karsilastirmasi (close < sma50) KABUL EDILMEZ.\n"
        "  Uygun bir esik goremiyorsan null birak — uydurma esik, hic esikten kotudur."
    )


def kosul_ayristir(metin: str | None) -> tuple[str, str, float] | None:
    """
    Gramere uyuyorsa (alan, op, esik), uymuyorsa None.

    None donmesi bir HATA DEGIL, bir REDDIR: cagiran taraf kosulu
    kaydetmez ve reddi sayar.
    """
    if not metin:
        return None
    m = KOSUL.match(str(metin).strip().lower())
    if not m:
        return None
    return m.group(1), m.group(2), float(m.group(3))


def _hacim_kat(seri: list) -> float | None:
    """Son hacim / onceki 20 gunun ortalamasi."""
    hacim = [r["volume"] or 0 for r in seri]
    if len(hacim) < 21 or sum(hacim[-21:-1]) <= 0:
        return None
    return hacim[-1] / (sum(hacim[-21:-1]) / 20)


def alan_degeri(db, instrument_id: int, alan: str) -> float | None:
    """
    Kosul alaninin GUNCEL degeri.

    IKI KURAL:
      * Gosterge TEK MOTORDAN gelir (`analysis.indicators`). Tarayicinin
        kendi RSI'ini hesaplamasi MSFT'de 84,8 vs 70,9 farki uretmisti;
        ikinci bir hesap yolu acilmayacak.
      * Fiyat `db.fiyat_serisi()` uzerinden okunur, dogrudan `prices`
        sorgulanmaz — para birimi karisir.
    """
    seri = db.fiyat_serisi(instrument_id, 300)
    if not seri:
        return None
    if alan == "close":
        return seri[-1]["close"]
    if alan == "hacim_kat":
        return _hacim_kat(seri)
    if alan == "car_t":
        # Olay etkisi ayri bir olcum; kosul icin son olcumun t degeri.
        r = db.query(
            """SELECT val FROM fundamentals WHERE instrument_id = ?
               AND concept = 'CAR_T' ORDER BY period_end DESC LIMIT 1""",
            (instrument_id,))
        return r[0]["val"] if r else None

    # PROJENIN TEK gosterge motoru — `teknik` aracinin kullandigi yol.
    import pandas as pd
    from ..analysis import compute_indicators, technical_snapshot
    df = pd.DataFrame([dict(r) for r in seri]).sort_values("ts")
    t = technical_snapshot("", compute_indicators(df))
    return t.get(alan)


def tetiklendi_mi(deger: float | None, op: str, esik: float) -> bool:
    if deger is None:
        return False
    return deger < esik if op == "<" else deger > esik
