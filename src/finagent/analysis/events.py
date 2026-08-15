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

BEKLENEN GETIRI MODELI — IKI SECENEK
------------------------------------
1. PIYASA MODELI (tercih edilen). Tahmin penceresinde enstruman getirisi
   piyasa getirisine regresyon edilir:  r_i = alfa + beta * r_m + e
   Beklenen getiri = alfa + beta * r_m ; anormal getiri = artik (e).
   Boylece "o gun butun piyasa dustugu icin dustu" durumu AYIKLANIR.
   Piyasa vekili para birimine gore secilir (bkz. db.piyasa_vekili):
   EUR->AEX, USD->QQQ, USDT->BTC.

2. ORTALAMA-DUZELTILMIS (yedek). Vekil serisi yoksa tahmin penceresindeki
   ORTALAMA gunluk getiri kullanilir.

Fark olculebilir: piyasa hareketi ayiklanmadan artik oynaklik yuksek
kaliyor ve gercek olaylar gurultuye gomuluyordu — NVDA'da 5 olay gununun
hicbiri |t|>2 cikmamisti. Hangi modelin kullanildigi ciktida HER ZAMAN
belirtilir.

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


def _regresyon(y: list[float], x: list[float]) -> tuple[float, float, float]:
    """
    Basit OLS: y = alfa + beta*x. Doner (alfa, beta, R^2).

    Piyasa varyansi sifirsa beta tanimsiz olurdu; o durumda beta=0 ve
    model ortalama-duzeltilmise coker — sessizce sacma bir beta
    uretmektense duruslugu koruyoruz.
    """
    n = len(y)
    ort_y, ort_x = sum(y) / n, sum(x) / n
    sxx = sum((xi - ort_x) ** 2 for xi in x)
    if sxx <= 0:
        return ort_y, 0.0, 0.0
    sxy = sum((xi - ort_x) * (yi - ort_y) for xi, yi in zip(x, y))
    beta = sxy / sxx
    alfa = ort_y - beta * ort_x
    syy = sum((yi - ort_y) ** 2 for yi in y)
    r2 = (sxy ** 2 / (sxx * syy)) if syy > 0 else 0.0
    return alfa, beta, r2


def olay_etkisi(barlar: list[dict], olay_tarihi: str,
                oncesi: int = OLAY_ONCESI, sonrasi: int = OLAY_SONRASI,
                piyasa: list[dict] | None = None) -> dict | None:
    """
    barlar: [{'ts','close',...}] ARTAN tarih sirali
    piyasa: ayni bicimde PIYASA VEKILI serisi (varsa piyasa modeli kurulur)
    olay_tarihi: 'YYYY-MM-DD'

    Yetersiz veri varsa None doner — kismi veriyle sayi uretmek, sayi
    uretmemekten kotudur.
    """
    seri = _getiriler(sorted(barlar, key=lambda x: x["ts"]))
    if len(seri) < 40:
        return None

    # Piyasa getirileri TARIHE GORE eslestirilir. Indeksle hisse ayni
    # gunlerde islem gormeyebilir (yerel tatiller); hizalanmamis iki
    # seriyi regresyona sokmak beta'yi bozar.
    piyasa_map = dict(_getiriler(sorted(piyasa, key=lambda x: x["ts"]))) if piyasa else {}

    tarihler = [t for t, _ in seri]
    # Olay gunu tatil/haftasonu olabilir; SONRAKI islem gununu al.
    idx = next((i for i, t in enumerate(tarihler) if t >= olay_tarihi), None)
    if idx is None:
        return None

    bas = max(0, idx - BOSLUK - TAHMIN_PENCERESI)
    son = max(0, idx - BOSLUK)
    tahmin = seri[bas:son]
    if len(tahmin) < 30:
        return None

    p_bas, p_son = max(0, idx - oncesi), min(len(seri), idx + sonrasi + 1)
    pencere = seri[p_bas:p_son]
    if not pencere:
        return None

    # Tahmin penceresinde piyasa getirisi de olan gunler
    ortak = [(t, g, piyasa_map[t]) for t, g in tahmin if t in piyasa_map]
    piyasa_modeli = len(ortak) >= 30 and all(t in piyasa_map for t, _ in pencere)

    if piyasa_modeli:
        alfa, beta, r2 = _regresyon([g for _, g, _ in ortak],
                                    [m for _, _, m in ortak])
        artiklar = [g - (alfa + beta * m) for _, g, m in ortak]
        ort_a = sum(artiklar) / len(artiklar)
        var = sum((a - ort_a) ** 2 for a in artiklar) / (len(artiklar) - 1)
        sapma = math.sqrt(var) if var > 0 else 0.0
        ar = [(t, g - (alfa + beta * piyasa_map[t])) for t, g in pencere]
        model_ad = "piyasa modeli (alfa/beta regresyonu)"
        model_detay = {"alfa_gunluk_%": round(alfa * 100, 4),
                       "beta": round(beta, 3),
                       "r_kare": round(r2, 3),
                       "tahmin_gun_sayisi": len(ortak)}
        ort = ort_a
    else:
        g_ler = [g for _, g in tahmin]
        ort = sum(g_ler) / len(g_ler)
        var = sum((g - ort) ** 2 for g in g_ler) / (len(g_ler) - 1)
        sapma = math.sqrt(var) if var > 0 else 0.0
        ar = [(t, g - ort) for t, g in pencere]
        model_ad = ("ortalama-duzeltilmis (piyasa vekili serisi yok veya "
                    "gunler hizalanmadi)")
        model_detay = {"tahmin_gun_sayisi": len(tahmin)}

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
        "artik_oynaklik_%": round(sapma * 100, 2),
        "t_istatistigi": round(t_ist, 2) if t_ist is not None else None,
        "anlamli_mi": (abs(t_ist) > 2 if t_ist is not None else None),
        "model": model_ad,
        "model_detay": model_detay,
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

    # PIYASA VEKILI: varsa alfa/beta modeli kurulur, piyasa geneli
    # hareketi anormal getiriden AYIKLANIR.
    vekil = db.piyasa_vekili(instrument_id)
    piyasa = ([dict(r) for r in db.fiyat_serisi(vekil["instrument_id"], 1000)]
              if vekil else None)

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
        etki = olay_etkisi(barlar, gun_str, piyasa=piyasa)
        if not etki:
            continue
        if vekil:
            etki["piyasa_vekili"] = vekil["sembol"]
        etki["olaylar"] = [
            {"baslik": o["title"], "url": o["url"], "yayinci": o["publisher"],
             "kademe": o["tier"], "tur": o["tur"]}
            for o in gunler[gun_str]
        ]
        etki["not"] = ("Bu gunun TUM olaylari ayni pencereye dusuyor; asagidaki "
                       "CAR gune aittir, tek bir baslige DEGIL.")
        out.append(etki)
    return out
