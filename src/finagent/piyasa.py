"""
BORSA SEANSLARI — "acik mi, kapali mi" sorusunun TEK kaynagi.

NEDEN VAR
---------
2026-08-19 18:14'te Ali'ye giden bildirimin basligi "🕕 Kapanis"ti ve
listenin ilk satiri AMZN'di. AMZN o saatte ABD'de ISLEM GORUYORDU —
kapanisa 3 saat 46 dakika vardi. Baslik hesaplanmis bir piyasa durumu
degildi; `{"ogle": "🕕 Kapanis"}` seklinde 18:00 slotunun TAKMA ADIYDI.
Slot BIST icin adlandirilmisti (ogle kosusu isyatirim/midas/kap topluyor)
ve BIST icin dogruydu — ama mesaj uc ayri borsadan enstruman tasiyordu.

Seans saatleri `bot/tools.py::saat` icinde ZATEN vardi ve dogruydu; nabiz
katmani onlari OKUMUYORDU. Bu, projede ucuncu kez ayni kalip: veri var,
okuyan yok (bkz. `takvim` araci, makro haber akisi). Cozum tabloyu
buyutmek degil, TEK KAYNAGA baglamak.

BIR ENSTRUMANIN BORSASI NEREDEN BILINIR
---------------------------------------
`identities.exchange` KULLANILMIYOR ve bu bilincli. O alan "sirket hangi
borsada kote" sorusuna cevap veriyor; bizim sorumuz farkli: **elimizdeki
BARIN fiyati hangi seansta olusuyor.** Ikisi ayrilabiliyor — ADYEN'in
kimliginde 'OTC' yaziyor ve serisi gercekten `ADYYF` (ABD tezgahustu)
sembolunden geliyor, oysa sirket Amsterdam'da kote. INGA'da da ayni:
kimlik 'NYSE' diyor, seri `ING` ADR'sinden USD olarak geliyor.

Dogru kaynak, seriyi CEKEN Yahoo sembolu:

    AMZN  -> AMZN      USD   ABD            (dogrulanmis SEC ticker'i)
    AVTX  -> AVTX.AS   EUR   Amsterdam
    CNDX  -> EXXT.DE   EUR   Frankfurt
    ADYEN -> ADYYF     USD   ABD

`collectors/prices.py::_yahoo_sembolu` bunu SAF bir fonksiyon olarak
uretiyor (girdi: `instruments` satiri + `identities` satiri), yani ayni
sonuc her yerde yeniden hesaplanabilir. Sonek eslemesi de tek anlamli:
Yahoo'da `.AS` Euronext Amsterdam, `.DE` XETRA demektir.

UC KAPI, UCU DE KONTROL — TAHMIN YOK
------------------------------------
1. `venue` YAPISAL OLARAK kesinse onu kullan: BIST kotasyonlari Is
   Yatirim/Midas'tan gelir ve hepsi Istanbul seansidir; BINANCE/CRYPTO
   7/24'tur, seansi yoktur.
2. Degilse fiyat serisinin KAYNAGI 'yahoo' MI diye BAK. Degilse (or.
   alphavantage) Yahoo sembolu o bari aciklamaz -> BILINMIYOR.
3. Yahoo sembolunun soneki tanidik mi? Degilse -> BILINMIYOR ve LOGLANIR.

Her adimda cevap "bilmiyorum" olabilir ve o zaman HICBIR SEY IDDIA
EDILMEZ. Yanlis borsa adi yazmak, borsa adi yazmamaktan kotudur.

TATIL TAKVIMI YOK — ve bu her ciktida SOYLENIYOR. "Acik" burada
"hafta ici ve seans saatleri icinde" demektir, "bugun tatil degil"
demek DEGILDIR.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TATIL_UYARISI = ("TATIL TAKVIMI YOK. 'acik' yalnizca hafta ici ve seans "
                 "saatleri icinde demektir; resmi tatilde de 'acik' "
                 "gorunur. Kesinlik gerekiyorsa bunu belirt.")

# (ad, IANA saat dilimi, acilis, kapanis, para birimi)
#
# Amsterdam 17:40: surekli islem 17:30'da biter, kapanis muzayedesi
# ~17:40'a kadar surer — `bot/tools.py::saat` bu degeri kullaniyordu ve
# tasinirken DEGISTIRILMEDI. XETRA surekli islem 09:00-17:30 (CET).
SEANSLAR: tuple[tuple[str, str, time, time, str], ...] = (
    ("BIST",      "Europe/Istanbul",  time(10, 0), time(18, 0),  "TRY"),
    ("Amsterdam", "Europe/Amsterdam", time(9, 0),  time(17, 40), "EUR"),
    ("Frankfurt", "Europe/Berlin",    time(9, 0),  time(17, 30), "EUR"),
    ("ABD",       "America/New_York", time(9, 30), time(16, 0),  "USD"),
)

# 7/24 isleyen piyasa: seans YOK, dolayisiyla "kapandi" da yok.
SUREKLI = "KRIPTO"

# Yahoo sembol soneki -> SEANSLAR'daki ad.
#
# YALNIZCA SEANSI TANIMLI OLANLAR. `.PA`/`.L`/`.MI` bilerek yok: adini
# yazip saatini bilmemek, "Paris" deyip acik mi kapali mi soyleyememek
# demek olurdu. Tanimsiz sonek BILINMIYOR'a duser ve loglanir.
SONEK_BORSA = {
    ".AS": "Amsterdam",     # Euronext Amsterdam
    ".DE": "Frankfurt",     # XETRA
    ".IS": "BIST",          # Borsa Istanbul
}

# `venue` degeri tek basina borsayi BELIRLIYORSA. BUX bilerek yok:
# araci kurum bir piyasa degildir ve altindaki kotasyon ABD de olabilir
# Amsterdam da (bkz. hayalet-enstruman dersi).
VENUE_BORSA = {
    "BIST": "BIST",
    "BINANCE": SUREKLI,
    "CRYPTO": SUREKLI,
}


def seans_durumlari(simdi: datetime | None = None) -> list[dict]:
    """
    Her borsanin O ANDAKI durumu. Saat diliminden bagimsiz, UTC'den turer.

    `durum`: acik | kapandi | acilmadi | hafta sonu
    `kapanisa_dk` / `acilisa_dk`: yalnizca anlamli olduklarinda dolu.
    """
    an = (simdi or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out = []
    for ad, tz, ac, kapa, ccy in SEANSLAR:
        yerel = an.astimezone(ZoneInfo(tz))
        hafta_ici = yerel.weekday() < 5
        simdi_dk = yerel.hour * 60 + yerel.minute
        ac_dk, kapa_dk = ac.hour * 60 + ac.minute, kapa.hour * 60 + kapa.minute
        acik = hafta_ici and ac_dk <= simdi_dk < kapa_dk
        kayit = {
            "borsa": ad, "para_birimi": ccy, "saat_dilimi": tz,
            "yerel_saat": yerel.strftime("%H:%M"),
            "gun": yerel.strftime("%A"),
            "acilis": ac.strftime("%H:%M"), "kapanis": kapa.strftime("%H:%M"),
            "seans": f"{ac.strftime('%H:%M')}-{kapa.strftime('%H:%M')} "
                     f"({tz.split('/')[-1]} saati)",
            "acik": acik,
            "durum": "acik" if acik else
                     ("hafta sonu" if not hafta_ici else
                      ("acilmadi" if simdi_dk < ac_dk else "kapandi")),
        }
        if acik:
            kayit["kapanisa_dk"] = kapa_dk - simdi_dk
            # ACILISTAN BU YANA GECEN SURE. Kullanicinin sordugu sey
            # "kapanisa ne kaldi" degil "acildi mi, ne zaman acildi":
            # kapanis saati zaten `seans` alaninda duruyor ve her
            # satirda tekrar etmesi gurultu.
            kayit["acilali_dk"] = simdi_dk - ac_dk
        elif hafta_ici and simdi_dk < ac_dk:
            kayit["acilisa_dk"] = ac_dk - simdi_dk
        elif hafta_ici:
            kayit["kapanali_dk"] = simdi_dk - kapa_dk
        out.append(kayit)
    return out


def _sure(dk: int) -> str:
    """
    Dakikayi kisa okunur sureye cevirir: 66 -> '1s 6dk', 45 -> '45dk',
    60 -> '1s' (tam saatte '0dk' yazmak gurultu).
    """
    dk = max(0, int(dk))
    if dk < 60:
        return f"{dk}dk"
    saat, kalan = divmod(dk, 60)
    return f"{saat}s" if kalan == 0 else f"{saat}s {kalan}dk"


def borsa_coz(db, instrument_id: int, venue: str | None,
              kimlik=None) -> str | None:
    """
    Bir enstrumanin BARININ olustugu borsa — bilinmiyorsa None.

    Modul basligindaki UC KAPI burada. Hicbir adimda tahmin yok; her
    adim ya kesin bir cevap verir ya da None'a duser.
    """
    b = VENUE_BORSA.get((venue or "").upper())
    if b:
        return b                       # 1) venue yapisal olarak belirliyor

    kaynak = db.fiyat_kaynagi(instrument_id)
    if not kaynak:
        return None
    ad = kaynak.get("source")

    # 2a) IKINCI YAHOO KAYNAGI. `prices.py::_borsa_kotasyonlari` yerel
    # borsa kotasyonunu AYRI kaynak adiyla yaziyor (`yahoo_borsa`) ve
    # sembolu `sources.prices.borsa_sonekleri`nden turetiyor: pozisyonun
    # PARA BIRIMI -> sonek. ADYEN/ASML/INGA'nin secilen serisi budur;
    # `_yahoo_sembolu`ye sorulsaydi ABD kotasyonu cikardi ve YANLIS
    # borsa yazilirdi (bar EUR, Amsterdam seansi).
    if ad == "yahoo_borsa":
        return _sonekten(_borsa_soneki(db, kaynak.get("currency")))

    if ad != "yahoo":
        # Bar Yahoo'dan gelmiyorsa Yahoo sembolu onu ACIKLAMAZ.
        return None

    from .collectors.prices import PriceCollector
    satir = db.query("SELECT id, symbol, venue, asset_type, currency "
                     "FROM instruments WHERE id = ?", (instrument_id,))
    if not satir:
        return None
    if kimlik is None:
        k = db.query("SELECT * FROM identities WHERE instrument_id = ?",
                     (instrument_id,))
        kimlik = k[0] if k else None

    yahoo = PriceCollector._yahoo_sembolu(satir[0], kimlik)
    if not yahoo:
        return None

    if "." not in yahoo:
        # SEC'de dogrulanmis sade ticker -> ABD kotasyonu (bkz.
        # `_yahoo_sembolu`: "SEC'de dogrulanmis -> ABD kotasyonu var").
        return "ABD"

    # SESSIZ DUSME YOK: tanimsiz sonek bir KAPSAM BOSLUGUDUR ve
    # gorunmezse hic kapanmaz — `_sonekten` logluyor.
    return _sonekten(yahoo[yahoo.rindex("."):])


def _borsa_soneki(db, para_birimi: str | None) -> str | None:
    """
    `yahoo_borsa` serisinin soneki — ayaridan, TAHMINDEN degil.

    Ayni sozluk `collectors/prices.py::_borsa_kotasyonlari` icinde
    seriyi CEKERKEN kullaniliyor; buradan ikinci kez okumak, iki tarafin
    ayni cevaba varmasini garanti eder. Ayar degisirse (or. `.PA`
    eklenirse) ikisi birlikte degisir.
    """
    if not para_birimi:
        return None
    try:
        harita = db.s.get("sources.prices.borsa_sonekleri")  # pragma: no cover
    except AttributeError:
        harita = None
    if not harita:
        from .config import load_settings
        harita = load_settings().get("sources.prices.borsa_sonekleri")
    return (harita or {"EUR": ".AS"}).get(str(para_birimi).upper())


def _sonekten(sonek: str | None) -> str | None:
    if not sonek:
        return None
    borsa = SONEK_BORSA.get(sonek.upper())
    if borsa is None:
        log.info("[piyasa] tanimsiz borsa soneki %s — borsa bilinmiyor", sonek)
    return borsa


def durum_satiri(simdi: datetime | None = None) -> str:
    """
    Bildirimlerin basina konan tek satirlik seans ozeti.

    NE SOYLUYOR: borsa acik mi, ve NE ZAMANDIR oyle.

    Onceki surum acik borsalar icin "→ 18:00 (7s 54dk)" yaziyordu, yani
    KAPANISA KALAN SUREYI. Kullanicinin sordugu sey o degil: "acildi mi,
    ne zaman acildi". Kapanis saati zaten sabit ve her satirda tekrar
    etmesi gurultu; acilisin USTUNDEN GECEN SURE ise degisen ve bilgi
    tasiyan sey (or. "1 dakika once acildi" ile "6 saattir acik" ayni
    cumleyi kurmaz).
    """
    parca = []
    for s in seans_durumlari(simdi):
        if s["durum"] == "acik":
            # "kapali" kelimesi burada YOK cunku ACIK yaziyor; asagida da
            # "acilacak" derken "kapali" demek gereksiz tekrar.
            parca.append(f"{s['borsa']} <b>ACIK</b> {s['acilis']}'dan beri "
                         f"({_sure(s['acilali_dk'])})")
        elif s["durum"] == "kapandi":
            parca.append(f"{s['borsa']} kapandi {s['kapanis']} "
                         f"({_sure(s['kapanali_dk'])} once)")
        elif s["durum"] == "acilmadi":
            parca.append(f"{s['borsa']} acilir {s['acilis']} "
                         f"({_sure(s['acilisa_dk'])} sonra)")
        else:
            parca.append(f"{s['borsa']} hafta sonu")
    return " · ".join(parca)
