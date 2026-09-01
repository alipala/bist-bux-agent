"""
GUN ICI ADAY TARAYICISI — deterministik, LLM YOK.

NE OLCUYOR
----------
"Bugun seans icinde, bu kagitta OLAGANDISI bir sey oldu mu?"

Olcut GUNLUK tarayicinin aynisi ve ayni sabitlerden geliyor
(`screener.SIGMA_HAREKET`): hareket, kagidin KENDI gunluk oynakliginin
kac katiysa o kadar dikkate deger. Sabit yuzde kullanmak kripto
mikro-kapta her saat sinyal uretir, AEX'te hicbir zaman uretmez.

Ikinci bir esik sabiti YAZILMADI: iki katmanda iki farkli "olagandisi"
tanimi olsaydi kullanici gun ici ve gun sonu mesajlarinda CELISEN iki
sey okurdu.

NEYE GORE OLCULUYOR
-------------------
    hareket = son SAATLIK kapanis / ONCEKI GUNUN kapanisi - 1

Onceki gunun kapanisi, bugunun barlarindan DEGIL gunluk seriden
aliniyor. Bugunun ilk saatlik bariyla karsilastirmak "gun icinde ne
oldu" sorusunu degil "son bir saatte ne oldu" sorusunu cevaplardi.

BES KAPI — hepsi "olmayan olayi bildirme" ilkesinin parcasi
-----------------------------------------------------------
1. KAPSAM     — venue TEK BASINA yetmez. `venue` ARACI KURUMDUR, borsa
                degil: ASML/ADYEN/INGA/VUSA `venue='BUX'` ama Amsterdam
                kotasyonu. Kapsam `collectors.saatlik.BORSA` ile AYNI
                uclu kuraldan geliyor (venue + BEKLENEN para birimi +
                sonek). Ilk yazimda yalnizca venue'ye bakiyordum ve
                Avrupa kagitlari sadece saatlik SERISI OLMADIGI icin
                eleniyordu — yani kapi degil kazaydi; B4 bir gun Avrupa
                toplarsa sessizce sizarlardi.
2. TAZELIK    — saatlik bar bayatsa aday uretilmez (`koruma` ile ayni
                esik: bayat barla "su an sunu yapiyor" denmez).
3. PARA BIRIMI— saatlik bar, gunluk seri ve borsanin BEKLENEN birimi
                UCU BIRDEN eslesmeli; TRY bir kapanisi USD bir barla
                kiyaslamak sessizce sacma bir yuzde uretir.
4. SERMAYE ISLEMI — gunluk seri son kesintisiz segmentten olculuyor;
                bolunmeyi asan bir oynaklik paydasi esigi aylarca bozar.
5. LIMIT KILIDI — tavan/taban kilidindeki kagit ISLEM GORMEZ. A5'te
                olculdu: cikis tetiklerinin %6,76'si taban barina
                dusuyor ve o barda cikis MUMKUN DEGIL. Kilitli kagida
                "al" ya da "sat" demek uygulanamaz taktiktir.

SESSIZLIK GECERLI CIKTIDIR. Esigi gecen yoksa bos liste doner ve
cagiran taraf LLM'i HIC cagirmaz.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Saatlik bar bu kadar dakikadan eskiyse aday uretilmez.
# `koruma.Koruma.GUN_ICI_AZAMI_YAS_DK` ile AYNI deger olmali; oradan
# okunuyor ki iki katman ayrisamasin.
def _azami_yas_dk() -> float:
    from .koruma import Koruma
    return float(Koruma.GUN_ICI_AZAMI_YAS_DK)


# Oynaklik hesabi icin gereken en az gunluk bar. `screener.MIN_BAR` ile
# ayni gerekce: altinda tek bir aykiri gun paydayi tasir.
def _min_bar() -> int:
    from .screener import MIN_BAR
    return int(MIN_BAR)


def _gunluk_oynaklik(seri, limit: float | None) -> float | None:
    """
    Gunluk getirilerin standart sapmasi — sermaye islemi gunleri HARIC.

    `screener.fiyat_kurallari` ile AYNI hesap; oradaki gerekce burada da
    gecerli: tek bir bolunme paydayi sisirip o kagitta aylarca esigi
    bozuyor.
    """
    kapanis = [b["close"] for b in seri if b["close"]]
    if len(kapanis) < _min_bar():
        return None
    g = []
    for i in range(1, len(kapanis)):
        if not kapanis[i - 1]:
            continue
        d = kapanis[i] / kapanis[i - 1] - 1
        if limit and abs(d) > limit:
            continue                      # sermaye islemi: oynakliga girmez
        g.append(d)
    if len(g) < 2:
        return None
    ort = sum(g) / len(g)
    sd = math.sqrt(sum((x - ort) ** 2 for x in g) / (len(g) - 1))
    return sd if sd > 0 else None


def _kilitli(bar, hareket: float, limit: float | None) -> bool:
    """
    Kagit tavan/taban KILIDINDE mi?

    Iki kosul birlikte aranir, cunku tek basina her biri yanilir:
      * hareket limite YAKIN (`LIMIT_YAKIN`) — ama %9,6 dusup gun icinde
        serbestce islem goren kagit da bu esigi gecer;
      * son barda `high == low` — ama tek printli likit olmayan bar da
        boyledir.
    Ikisi ayni anda ancak fiyat PINLENDIGINDE olur.

    `limit`i olmayan borsada (ABD) sabit gunluk limit YOKTUR; orada bu
    kapi hic calismaz ve `False` doner. ABD'deki volatilite duraklamasi
    (LULD) sabit yuzdeye dayanmadigi icin buradan TESPIT EDILEMEZ ve
    edilebiliyormus gibi davranilmiyor.
    """
    if not limit:
        return False
    from ..analysis.trend_takip import LIMIT_YAKIN
    if abs(hareket) < LIMIT_YAKIN:
        return False
    yuksek, dusuk = bar["high"], bar["low"]
    return bool(yuksek and dusuk and yuksek == dusuk)


def _bar_yasi_dk(ts: str, simdi: datetime | None = None) -> float | None:
    try:
        an = datetime.strptime(str(ts), "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    return ((simdi or datetime.now(timezone.utc)) - an).total_seconds() / 60


def endeks_karsilastir(db, instrument_id: int, hareket: float,
                       simdi: datetime | None = None) -> dict:
    """
    Bu hareket HISSEYE MI OZGU, yoksa PIYASA GENELI mi?

    NEDEN VAR (2026-09-01, Ali sordu): "eger bu dususler oldu ise ilgili
    haberi cekmesi ve o habere dayandirmasi gerekmez mi?" Haberden ONCE
    sorulacak soru bu, cunku cevabi UYDURULAMAZ: BIST 100 %2 duserken
    AGROT %8 dustuyse fark gercektir. "X yuzunden dustu" cumlesi ise
    cogu zaman sonradan kurulmus bir hikayedir.

    Ve eylem degisiyor: piyasa geneli bir dususte tek hisseye taktik
    vermek, olcumu degil gurultuyu takip etmektir.

    REFERANS YOKSA SESSIZCE ATLANMAZ, SEBEBI SOYLENIR. Bu, bugun bu
    depoda defalarca yakalanan kusurun tersi: eksik veriyi yok saymak
    yerine BEYAN etmek. Cagiran taraf "karsilastirildi ve fark yok" ile
    "karsilastirilamadi"yi ayirt edebilmeli.
    """
    vekil = db.piyasa_vekili(instrument_id)
    if not vekil:
        # Enstrumanin KENDISI vekil (BTC gibi) — kendine kiyas anlamsiz.
        return {"endeks_yok": "kendisi piyasa vekili"}

    vid = vekil["instrument_id"]
    # ONCE SAATLIK: kriptoda vekil (BTC) saatlik geliyor ve gun ici
    # karsilastirma ancak o zaman AYNI PENCEREDE olur.
    seri = [dict(x) for x in db.saatlik_seri(vid, limit=3)]
    if len(seri) >= 2:
        simdiki, onceki = seri[-1]["close"], seri[-2]["close"]
    else:
        # GUNLUKTE GECICI BAR DAHIL: seans surerken yazilmis bar tam da
        # aradigimiz "su anki endeks seviyesi"dir (sema 26).
        g = [dict(x) for x in db.fiyat_serisi(vid, 3, gecici_dahil=True)]
        if len(g) < 2:
            return {"endeks_yok": f"{vekil['sembol']} gun ici verisi yok"}
        # SON BAR BUGUNUN OLMAK ZORUNDA.
        #
        # ILK YAZIMDA YOKTU VE SESSIZCE YANLIS URETTI (2026-09-01):
        # XU100'un bugune ait bari yoktu, fonksiyon 31 ve 28 Agustos'u
        # aldi ve DUNUN endeks hareketini (-%2,1) BUGUNUN gun ici hisse
        # hareketiyle karsilastirdi. Sayi makul gorunuyordu — bu yuzden
        # sessizce yanlis kalirdi.
        #
        # Pencereler ayni olmali: aday "onceki kapanis -> su an" olcuyor,
        # vekil de oyle olcmeli.
        bugun = (simdi or datetime.now(timezone.utc)).date().isoformat()
        if str(g[-1]["ts"])[:10] != bugun:
            return {"endeks_yok": (f"{vekil['sembol']} bugune ait deger yok "
                                   f"(son {str(g[-1]['ts'])[:10]})")}
        simdiki, onceki = g[-1]["close"], g[-2]["close"]

    if not simdiki or not onceki:
        return {"endeks_yok": f"{vekil['sembol']} fiyati okunamadi"}

    endeks_hareket = simdiki / onceki - 1
    return {
        "endeks": vekil["sembol"],
        "endeks_hareket_%": round(endeks_hareket * 100, 2),
        # GORELI HAREKET: hisseye ozgu kisim. Basit fark kullaniliyor,
        # beta ile olceklenmis artik DEGIL — gun ici tek barda beta
        # tahmini gurultuden ibarettir ve olculmus bir sayinin yanina
        # tahmini bir sayi koymak olurdu.
        "goreli_%": round((hareket - endeks_hareket) * 100, 2),
    }


def adaylar(db, sahip: str | None = None, simdi: datetime | None = None
            ) -> tuple[list[dict], dict]:
    """
    Gun ici esigi gecen enstrumanlar. Doner: (adaylar, rapor).

    `sahip` verilirse aday uzerinde "bu kiside pozisyon var mi" bayragi
    tasinir — taktikci `alim` ile `satis` arasinda secim yaparken bunu
    BILMEK zorunda; bilmezse elde olan bir kagida "al" der.
    """
    from ..analysis.karsilastirma import borsa_limiti, son_kesintisiz
    from ..collectors.saatlik import BORSA
    from .screener import SIGMA_HAREKET

    azami_yas = _azami_yas_dk()
    rapor = {"taranan": 0, "atlanan": {}, "esigi_gecen": 0,
             "sigma_esigi": SIGMA_HAREKET}

    def _atla(sebep: str) -> None:
        rapor["atlanan"][sebep] = rapor["atlanan"].get(sebep, 0) + 1

    pozisyonlu = set()
    if sahip:
        for hesap in db.hesaplar(sahip):
            for p in db.latest_positions(hesap, sahip):
                if (p["quantity"] or 0) > 0:
                    pozisyonlu.add(p["instrument_id"])

    out = []
    # KAPSAM: `collectors.saatlik.BORSA` ile AYNI kaynak. Toplayicinin
    # topladigi kume ile tarayicinin taradigi kume AYRISAMAZ.
    for venue, (sonek, beklenen_birim) in BORSA.items():
      for r in db.query(
            """SELECT i.id, i.symbol, i.venue, i.name FROM instruments i
               WHERE i.venue = ?
                 AND (i.id IN (SELECT instrument_id FROM positions)
                      OR i.id IN (SELECT instrument_id FROM watchlist))
               ORDER BY i.symbol""", (venue,)):
        rapor["taranan"] += 1
        limit = borsa_limiti(venue)

        # SONEKLI SEMBOL, SONEK EKLEMEYEN BORSANIN KOTASYONU DEGILDIR
        # (SHELL.AS -> Amsterdam). Toplayicidaki kapinin aynisi.
        if not sonek and "." in r["symbol"]:
            _atla("sonekli sembol (bu borsanin kotasyonu degil)")
            continue

        saatlik = db.saatlik_seri(r["id"], limit=3)
        if not saatlik:
            _atla("saatlik seri yok")
            continue
        son_bar = saatlik[-1]
        yas = _bar_yasi_dk(son_bar["ts"], simdi)
        if yas is None or yas > azami_yas:
            _atla("saatlik bar bayat")
            continue

        gunluk_ham = [dict(x) for x in db.fiyat_serisi(r["id"], 300)]
        gunluk, _ = son_kesintisiz(gunluk_ham, limit)
        if len(gunluk) < _min_bar():
            _atla("gunluk seri kisa")
            continue
        # UC BIRIM DE ESLESMELI: saatlik bar, gunluk seri ve borsanin
        # BEKLEDIGI birim. Ucuncusu olmadan EUR kotasyonlu bir BUX
        # kagidi (ASML) kendi icinde tutarli oldugu icin gecerdi.
        birimler = {son_bar["currency"] or None,
                    gunluk[-1].get("currency") or None}
        if birimler != {beklenen_birim}:
            _atla("para birimi uyusmuyor")
            continue

        onceki_kapanis = gunluk[-1]["close"]
        simdiki = son_bar["close"]
        if not onceki_kapanis or not simdiki:
            _atla("kapanis yok")
            continue
        # ONCEKI GUNUN KAPANISI: saatlik barin tarihi gunluk serinin son
        # tarihiyle AYNIYSA o gunluk bar BUGUNUN barid ir ve kiyas
        # kendisiyle olurdu. Bir onceki gunluk bara duselim.
        if str(son_bar["ts"])[:10] == str(gunluk[-1]["ts"])[:10]:
            if len(gunluk) < 2 or not gunluk[-2]["close"]:
                _atla("onceki gun kapanisi yok")
                continue
            onceki_kapanis = gunluk[-2]["close"]

        hareket = simdiki / onceki_kapanis - 1
        if limit and abs(hareket) > limit:
            # Gunluk limiti asan "hareket" fiyat hareketi DEGILDIR.
            _atla("limit disi hareket")
            continue
        sd = _gunluk_oynaklik(gunluk, limit)
        if not sd:
            _atla("oynaklik hesaplanamadi")
            continue
        z = hareket / sd
        if abs(z) < SIGMA_HAREKET:
            continue

        kilit = _kilitli(son_bar, hareket, limit)
        pozisyonda = r["id"] in pozisyonlu
        if kilit and not pozisyonda:
            # Kilitli VE elde degil: alinamaz, satilacak bir sey de yok.
            # Elde OLSAYDI dusurulmezdi — sahibinin pozisyonunun
            # kilitlendigini BILMESI gerekir (taktik olarak degil,
            # gozlem olarak; asagida `kilitli` bayragi tasiniyor).
            _atla("limit kilidi (pozisyon yok)")
            continue

        rapor["esigi_gecen"] += 1
        out.append({
            "instrument_id": r["id"], "sembol": r["symbol"], "ad": r["name"],
            "venue": venue, "para_birimi": son_bar["currency"],
            "simdiki_fiyat": simdiki, "onceki_kapanis": onceki_kapanis,
            "gun_ici_hareket_%": round(hareket * 100, 2),
            "sigma": round(z, 2),
            "gunluk_oynaklik_%": round(sd * 100, 2),
            "bar_ts": str(son_bar["ts"]),
            "bar_yasi_dk": round(yas),
            "pozisyonda": pozisyonda,
            "kilitli": kilit,
            # HISSEYE MI OZGU, PIYASA GENELI MI. Referans yoksa
            # `endeks_yok` alani SEBEBIYLE geliyor — sessizce atlanmiyor.
            **endeks_karsilastir(db, r["id"], hareket, simdi),
        })
    out.sort(key=lambda x: -abs(x["sigma"]))
    if out:
        log.info("[gunici-tarayici] %d aday: %s", len(out),
                 [(x["sembol"], x["sigma"]) for x in out[:6]])
    return out, rapor
