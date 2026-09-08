"""
Fiyat serisi TUTARLILIGI — bir seri kendi icinde AYNI TABANDA mi?

NEDEN VAR (olculdu 2026-09-08)
------------------------------
BLCYT 1 Eylul'de 10:1 bolundu. Iki kaynak da (Yahoo, Is Yatirim)
gecmisi GERIYE DONUK yeniden tabanliyor: bolunmeden sonra 25 Agustos
kapanisi 21,10 degil 2,11 olarak geliyor. Bizim tazeleme penceremiz ise
5 gun; yani bolunmeden sonraki kosular yalnizca 26 Agu-1 Eyl barlarini
yeniden yazdi, 25 Agustos ve oncesi ESKI tabanda kaldi. Sonuc, tek bir
kaynak icinde iki taban:

    2026-08-25  21.10     (eski taban, hic yeniden cekilmedi)
    2026-08-26   2.132    (yeni taban, 5 gunluk pencereye girdi)

Bu seriden hesaplanan HER SEY yanlis: SMA/RSI, tarayici sinyali, ve
tahmin defterinin puani — BLCYT taktigi 22,60 -> 2,25 = "-%90" diye
puanlandi ve fren girdisine oyle girdi. Ayni kalip AKFIS, SDTTR, ORGE,
DNISI, CVKMD'de de var (Agustos 2026, hepsi BIST bedelsiz/bolunme).

TEK TANIM. Sicrama olcutu hem collector'larin kendini onarmasinda
(sicramali sembolu TAM gecmisle yeniden cek) hem puanlayicinin
kendini korumasinda (sicramanin ustunden puanlama) kullaniliyor. Iki
yerde ayri esik yazmak, bu deponun tekrar eden kusur sinifi olan
"ayni kural iki kopya" olurdu.

ESIK NEDEN 1,5: BIST'te gunluk fiyat siniri %10; ABD'de sinir yok ama
tek gunde x1,5 / /1,5 hareket bolunme, bedelsiz ya da veri hatasi
disinda cok nadir. %20 bedelsiz (x1,2) BU ESIGIN ALTINDA KALIR ve
yakalanmaz — bilerek: onu yakalayacak esik BIST'in siradan sert
gunlerini de yakalar ve puanlayiciyi gereksiz susturur. Tam cozum
kaynagin kurumsal islem takvimi; bu modul onun yerine gecmiyor, en
buyuk ve en sik zarari kapatiyor.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

# Ardisik iki bar arasinda bu katsayidan buyuk (ya da tersinden kucuk)
# oran "sicrama" sayilir.
SICRAMA_ESIGI = 1.5

# Sicrama taramasinin varsayilan penceresi (gun). Tahmin defterinin en
# uzun ufku 90 islem gunu; 400 takvim gunu onu fazlasiyla kapsar ve
# `fiyat_serisi(iid, 400)` ile ayni derinlik.
VARSAYILAN_PENCERE_GUN = 400


def sicramalar(barlar, esik: float = SICRAMA_ESIGI) -> list[dict]:
    """
    Artan tarihli bar listesinde ardisik kapanislar arasindaki
    sicramalar. Saf: db yok.

    Doner: [{"onceki_ts", "ts", "onceki", "sonraki", "oran"}] — bos
    liste = tutarli seri. `close` yoksa ya da pozitif degilse o cift
    ATLANIR (sicrama uydurulmaz).
    """
    out: list[dict] = []
    onceki = None
    for b in barlar:
        try:
            kapanis = float(b["close"]) if b["close"] is not None else None
        except (TypeError, ValueError, KeyError):
            kapanis = None
        if kapanis is None or kapanis <= 0:
            continue
        if onceki is not None and onceki["close"] > 0:
            oran = kapanis / onceki["close"]
            if oran >= esik or oran <= 1.0 / esik:
                out.append({"onceki_ts": onceki["ts"], "ts": b["ts"],
                            "onceki": onceki["close"], "sonraki": kapanis,
                            "oran": round(oran, 4)})
        onceki = {"ts": b["ts"], "close": kapanis}
    return out


def sicramali_semboller(db, source: str, venue: str | None = None,
                        baslangic_ts: str | None = None,
                        esik: float = SICRAMA_ESIGI) -> dict[str, list[dict]]:
    """
    Verilen KAYNAKTA serisi sicramali enstrumanlar — `{sembol: [sicrama]}`.

    Kaynak SUZGECI ZORUNLU: `prices` ayni enstruman icin birden fazla
    kaynak ve para birimi tutuyor; kaynak/para birimi karistiran bir
    LAG, ASML'nin USD ve EUR barlarini yan yana koyup olmayan sicrama
    uretir (olculdu: MSFT 390 -> 6,9 "sicramasi" tamamen buydu).
    Bolme `(instrument_id, source, currency)` uzerinden.

    Gecici (seans ici) barlar DISARIDA: yarim bar sicrama degildir.
    """
    if not baslangic_ts:
        baslangic_ts = (date.today()
                        - timedelta(days=VARSAYILAN_PENCERE_GUN)).isoformat()
    venue_sql = " AND i.venue = ?" if venue else ""
    params: list = [source, baslangic_ts]
    if venue:
        params.append(venue)
    params += [esik, esik]
    satirlar = db.query(
        f"""WITH s AS (
              SELECT p.instrument_id, p.ts, p.close,
                     LAG(p.close) OVER (PARTITION BY p.instrument_id, p.currency
                                        ORDER BY p.ts) onceki,
                     LAG(p.ts) OVER (PARTITION BY p.instrument_id, p.currency
                                     ORDER BY p.ts) onceki_ts
              FROM prices p
              WHERE p.source = ? AND COALESCE(p.gecici, 0) = 0
                AND p.ts >= ? AND p.close > 0)
            SELECT i.symbol, s.onceki_ts, s.ts, s.onceki, s.close sonraki,
                   s.close / s.onceki oran
            FROM s JOIN instruments i ON i.id = s.instrument_id
            WHERE s.onceki > 0{venue_sql}
              AND (s.close / s.onceki >= ? OR s.close / s.onceki <= 1.0 / ?)
            ORDER BY i.symbol, s.ts""", tuple(params))
    out: dict[str, list[dict]] = {}
    for r in satirlar:
        out.setdefault(str(r["symbol"]).upper(), []).append(
            {"onceki_ts": r["onceki_ts"], "ts": r["ts"],
             "onceki": r["onceki"], "sonraki": r["sonraki"],
             "oran": round(float(r["oran"]), 4)})
    return out
