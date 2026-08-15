"""
Olay-etki analizi (event study) — bir haberin fiyata etkisini OLCER.

YONTEM
------
Klasik olay calismasi:
  1. Olay gunu (t=0) belirlenir
  2. Olaydan ONCEKI "tahmin penceresi"nde (varsayilan 120 gun, olaydan
     10 gun once biten) beklenen getiri modeli kurulur
  3. Olay penceresinde (varsayilan -1..+3 gun) gerceklesen getiri ile
     beklenen getiri farki = ANORMAL GETIRI (AR)
  4. AR'lerin toplami = KUMULATIF ANORMAL GETIRI (CAR)

Beklenen getiri modeli: tahmin penceresindeki ORTALAMA gunluk getiri
(mean-adjusted). Piyasa modeli (alfa/beta regresyonu) daha iyi olurdu ama
elimizde endeks serisi yok — o yuzden bilerek daha zayif ama DURUST olan
model kullaniliyor ve bu ciktida belirtiliyor.

NE SOYLER, NE SOYLEMEZ
----------------------
Soyler : "Olay penceresinde anormal getiri %X'ti ve bu, normal gunluk
          oynakligin Y katiydi."
SOYLEMEZ: "Bu haber fiyati %X etkiledi." Korelasyon nedensellik degildir.
Ayni gun baska onlarca sey olmus olabilir; tek bir haberin etkisini
izole etmek bu veriyle mumkun degil. Cikti bu ayrimi korumak zorunda.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta

log = logging.getLogger(__name__)

TAHMIN_PENCERESI = 120     # gun, beklenen getiri modeli icin
BOSLUK = 10                # olaydan kac gun once tahmin penceresi bitsin
OLAY_ONCESI = 1            # t-1
OLAY_SONRASI = 3           # t+3


def _getiriler(barlar: list[dict]) -> list[tuple[str, float]]:
    """[(tarih, gunluk_getiri)] — kapanistan kapanisa."""
    out = []
    for onceki, simdi in zip(barlar, barlar[1:]):
        a, b = onceki.get("close"), simdi.get("close")
        if a and b and a > 0:
            out.append((simdi["ts"], b / a - 1.0))
    return out


def olay_etkisi(barlar: list[dict], olay_tarihi: str,
                oncesi: int = OLAY_ONCESI, sonrasi: int = OLAY_SONRASI) -> dict | None:
    """
    barlar: [{'ts','close',...}] ARTAN tarih sirali
    olay_tarihi: 'YYYY-MM-DD'

    Yetersiz veri varsa None doner — kismi veriyle sayi uretmek, sayi
    uretmemekten kotudur.
    """
    seri = _getiriler(sorted(barlar, key=lambda x: x["ts"]))
    if len(seri) < 40:
        return None

    tarihler = [t for t, _ in seri]
    # Olay gunu tatil/haftasonu olabilir; SONRAKI islem gununu al.
    idx = next((i for i, t in enumerate(tarihler) if t >= olay_tarihi), None)
    if idx is None:
        return None

    bas = max(0, idx - BOSLUK - TAHMIN_PENCERESI)
    son = max(0, idx - BOSLUK)
    tahmin = [g for _, g in seri[bas:son]]
    if len(tahmin) < 30:
        return None

    ort = sum(tahmin) / len(tahmin)
    var = sum((g - ort) ** 2 for g in tahmin) / (len(tahmin) - 1)
    sapma = math.sqrt(var) if var > 0 else 0.0

    p_bas, p_son = max(0, idx - oncesi), min(len(seri), idx + sonrasi + 1)
    pencere = seri[p_bas:p_son]
    if not pencere:
        return None

    ar = [(t, g - ort) for t, g in pencere]
    car = sum(a for _, a in ar)

    # t-istatistigi: CAR / (sapma * sqrt(n)). |t| > 2 kabaca %5 anlamlilik.
    n = len(ar)
    t_ist = (car / (sapma * math.sqrt(n))) if sapma > 0 else None

    return {
        "olay_tarihi": olay_tarihi,
        "islem_gunu": tarihler[idx],
        "pencere": f"t{-oncesi:+d}..t{sonrasi:+d}",
        "gun_sayisi": n,
        "anormal_getiri_gunluk": [
            {"tarih": t, "ar_%": round(a * 100, 2)} for t, a in ar],
        "car_%": round(car * 100, 2),
        "beklenen_gunluk_getiri_%": round(ort * 100, 3),
        "gunluk_oynaklik_%": round(sapma * 100, 2),
        "t_istatistigi": round(t_ist, 2) if t_ist is not None else None,
        "anlamli_mi": (abs(t_ist) > 2 if t_ist is not None else None),
        "model": "ortalama-duzeltilmis (mean-adjusted); piyasa modeli icin "
                 "endeks serisi gerekir, elimizde yok",
        "uyari": ("Bu bir KORELASYON olcumudur, nedensellik degil. Ayni "
                  "pencerede baska etkenler de olabilir; tek bir haberin "
                  "etkisi bu veriyle izole EDILEMEZ."),
    }


def haber_etkileri(db, instrument_id: int, sembol: str,
                   limit: int = 6, gun: int = 120) -> list[dict]:
    """
    Bir enstrumanin son KADEME 1-2 haberleri icin olay etkisi hesaplar.

    Yalnizca kanit sayilan kaynaklar (kademe 1-2) kullanilir: toplayici ve
    promosyon icerigi zaten olay degil, gurultudur — onlar icin anormal
    getiri hesaplamak sahte bir kesinlik uretirdi.
    """
    # TEK kaynaktan: ayni enstrumanda farkli para biriminde seri olabilir
    # (ASML: Yahoo USD + Alpha Vantage EUR). Karisirsa gunluk getiriler
    # para birimi siciramalariyla dolar ve CAR tamamen anlamsizlasir.
    barlar = [dict(r) for r in db.fiyat_serisi(instrument_id, 1000)]
    if len(barlar) < 40:
        return []

    sinir = (date.today() - timedelta(days=gun)).isoformat()
    olaylar = db.query(
        """SELECT published_at, title, url, publisher, tier, 'haber' AS tur
           FROM news
           WHERE tier IN (1,2) AND (',' || symbols || ',') LIKE ?
             AND published_at >= ?
           UNION ALL
           SELECT published_at, title, url, source AS publisher, 1 AS tier,
                  'dosyalama' AS tur
           FROM disclosures WHERE symbol = ? AND published_at >= ?
           ORDER BY published_at DESC LIMIT ?""",
        # `limit` GUN sayisidir, satir sayisi degil: bir gunde onlarca haber
        # olabiliyor. Gruplamaya yetecek kadar satir cekip gunu kirpiyoruz.
        (f"%,{sembol},%", sinir, sembol, sinir, limit * 12))

    # AYNI GUNUN olaylari TEK olcumdur. Ayni pencereye dusen iki haberin
    # ayri ayri CAR'i yok — ikisi de ayni sayiyi verir. Ayri satir olarak
    # sunmak, bagimsiz iki kanit varmis izlenimi yaratir ve modeli "iki
    # haber de fiyati %3 oynatti" demeye iter. Gune gore gruplaniyor.
    gunler: dict[str, list] = {}
    for o in olaylar:
        gun_str = (o["published_at"] or "")[:10]
        if gun_str:
            gunler.setdefault(gun_str, []).append(o)

    out = []
    for gun_str in sorted(gunler, reverse=True)[:limit]:
        etki = olay_etkisi(barlar, gun_str)
        if not etki:
            continue
        etki["olaylar"] = [
            {"baslik": o["title"], "url": o["url"], "yayinci": o["publisher"],
             "kademe": o["tier"], "tur": o["tur"]}
            for o in gunler[gun_str]
        ]
        etki["not"] = ("Bu gunun TUM olaylari ayni pencereye dusuyor; asagidaki "
                       "CAR gune aittir, tek bir baslige DEGIL.")
        out.append(etki)
    return out
