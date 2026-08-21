"""
TAKTIK SEVIYELERI — modelin SECECEGI, HESAPLAMAYACAGI sayilar.

NEDEN KODDA
-----------
Olculdu 2026-08-18: model bir fiyat bari bile GORMEDEN 335 pencerelik
bir istatistik tablosu yazdi ve "guvenim yuksek" dedi; sayilar
kalibreliydi ve TAMAMEN UYDURMAYDI (ADA oynaklik uydurma %76,4 /
gercek %76,5). Dersi net: modelin hesap yapmaya CALISMASI, aracin ona
hesap degil ham veri vermesinin belirtisidir.

Bir taktik "girisi 132,50, stop'u 127,80" diyorsa o iki sayi
NEREDEN geldigini gosterebilmeli. Bu modul onlari uretir; hakem
yalnizca ARALARINDAN SECER ve secimini gerekcelendirir. Secmedigi bir
sayiyi yazarsa dogrulama onu REDDEDER (`taktik.dogrula`).

HANGI SEVIYELER VE NEDEN
------------------------
Hepsi ZATEN projede olculen seyler; yeni bir gosterge icat edilmedi:
  * `son_kapanis`     — `db.fiyat_serisi` (tek mesru yol)
  * `n` ve `stop_2n`  — 20 gunluk ATR, koruma katmaninin kullandigi
                        stop (`pulse.koruma`). Iki katmanda iki ayri
                        stop tanimi olmasi sessiz ayrisma demekti.
  * `donchian_giris`  — onceki 20 gunun en yuksegi (kirilim girisi)
  * `donchian_cikis`  — onceki 10 gunun en dusugu (trend cikisi)
  * `sma20/50/200`    — projenin TEK gosterge motorundan
                        (`analysis.indicators`); tarayicinin kendi
                        RSI'ini hesaplamasi MSFT'de 84,8 vs 70,9 farki
                        uretmisti, ikinci bir hesap yolu acilmayacak.

SERMAYE ISLEMI KAPISI HER SEVIYEDE
----------------------------------
Seviyeler son KESINTISIZ segmentten hesaplanir. Bolunmeyi asan bir
SMA200 hicbir gun gorulmemis bir ortalamadir ve o seviyeye dayanan bir
taktik, olmayan bir fiyata gore kurulur.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Donchian pencereleri — `analysis.trend_takip` ile AYNI. Iki yerde iki
# farkli pencere, backtest'in olctugu kuralla taktigin onerdigi kuralin
# ayrismasi demekti.
GIRIS_PENCERE = 20
CIKIS_PENCERE = 10

# Seviye uretmek icin gereken en az bar. ATR 20 gunluk pencere + onceki
# kapanis istiyor; 60 SMA50'yi de anlamli kiliyor.
ASGARI_BAR = 60


def _yuvarla(x, basamak: int = 6):
    """Fiyat hassasiyeti VARLIGA GORE: ROSE 0,0055 USD, ASML 1512 EUR."""
    if x is None:
        return None
    try:
        return float(f"{float(x):.{basamak}g}")
    except (TypeError, ValueError):
        return None


def seviyeler(db, instrument_id: int) -> dict | None:
    """
    Bir enstrumanin OLCULEN seviyeleri. Hesaplanamiyorsa None.

    Doner: {"son_kapanis", "para_birimi", "n", "stop_2n",
            "donchian_giris", "donchian_cikis", "sma20/50/200", ...}
    """
    from ..analysis.karsilastirma import borsa_limiti, son_kesintisiz
    from ..analysis.trend_takip import _atr, STOP_N

    e = db.query("SELECT symbol, venue FROM instruments WHERE id = ?",
                 (instrument_id,))
    if not e:
        return None
    venue = e[0]["venue"]
    seri = [dict(r) for r in db.fiyat_serisi(instrument_id, 300)]
    seri, sermaye = son_kesintisiz(seri, borsa_limiti(venue))
    if len(seri) < ASGARI_BAR:
        return None
    kapanis = [b["close"] for b in seri if b["close"]]
    if len(kapanis) < ASGARI_BAR:
        return None

    n = _atr(seri, len(seri) - 1)
    son = kapanis[-1]
    out = {
        "sembol": e[0]["symbol"], "venue": venue,
        "son_kapanis": _yuvarla(son),
        "para_birimi": seri[-1].get("currency"),
        "bar_ts": str(seri[-1]["ts"])[:10],
        # ONCEKI pencere: bugunun barini DISLAR. Iceri alsaydik "bugun
        # zaten kirdi" diyen bir giris seviyesi uretirdik.
        "donchian_giris": _yuvarla(max(kapanis[-GIRIS_PENCERE - 1:-1])),
        "donchian_cikis": _yuvarla(min(kapanis[-CIKIS_PENCERE - 1:-1])),
    }
    if n and n > 0:
        out["n"] = _yuvarla(n)
        out["stop_2n"] = _yuvarla(son - STOP_N * n)
    for pencere in (20, 50, 200):
        if len(kapanis) >= pencere:
            out[f"sma{pencere}"] = _yuvarla(
                sum(kapanis[-pencere:]) / pencere)
    if sermaye.get("sermaye_islemi"):
        # SESSIZ KESME YOK: seviyeler kisaltilmis bir segmentten
        # geliyorsa okuyan taraf bunu bilmeli.
        out["sermaye_islemi"] = sermaye["sermaye_islemi"]
        out["segment_baslangici"] = sermaye.get("segment_baslangici")
    return out


# Bir taktigin gecerli sayilmasi icin seviyenin olculenlerden birine
# bu orandan daha yakin olmasi gerekir.
#
# %0,5 secildi: model bazen yuvarliyor (132,4567 -> 132,50) ve bunu
# "uydurma" saymak, dogru secimi cezalandirmak olurdu. Ama %0,5, en dar
# 2N mesafesinin (olculdu: VUSA %1,9) dortte birinden kucuk — yani iki
# ayri seviyeyi birbirine karistiracak kadar genis DEGIL.
TOLERANS = 0.005

# Taktik turleri. `bekle` bir taktiktir: "simdi bir sey yapma" da bir
# karardir ve sessizlikten farklidir (sessizlik = bakilmadi).
TURLER = ("alim", "koruma", "satis", "bekle")


def _yakin(deger, aday) -> bool:
    if deger is None or aday is None or not aday:
        return False
    try:
        return abs(float(deger) / float(aday) - 1) <= TOLERANS
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def dogrula(taktik: dict, olculen: dict) -> tuple[bool, str | None]:
    """
    Taktik OLCULEN seviyelere dayaniyor mu? Doner: (gecerli, sebep).

    UYDURMA SEVIYE REDDEDILIR. Bu, "sayilari araca tasi" dersinin
    taktik katmanindaki karsiligi: model bir seviye YAZABILIR ama
    HESAPLAYAMAZ — yazdigi sey kendisine verilenlerden biri olmali.
    """
    tur = str(taktik.get("tur") or "").strip().lower()
    if tur not in TURLER:
        return False, f"tur gecersiz: {taktik.get('tur')!r}"
    if tur == "bekle":
        # `bekle` seviye GEREKTIRMEZ — zaten bir islem onermiyor.
        return True, None

    adaylar = {k: olculen.get(k) for k in
               ("son_kapanis", "donchian_giris", "donchian_cikis",
                "stop_2n", "sma20", "sma50", "sma200")}
    for alan in ("giris", "stop"):
        deger = taktik.get(alan)
        if deger is None:
            if tur == "koruma" and alan == "giris":
                continue          # koruma girisi zaten var olan pozisyon
            return False, f"{alan} yok"
        if not any(_yakin(deger, a) for a in adaylar.values()):
            return False, (f"{alan}={deger} olculen seviyelerin HICBIRINE "
                           f"uymuyor ({sorted(k for k, v in adaylar.items() if v)})")
    # STOP GIRISIN ALTINDA OLMALI (uzun yonlu): ustunde bir "stop",
    # aninda tetiklenen ve hicbir sey korumayan bir seviyedir.
    g, st = taktik.get("giris"), taktik.get("stop")
    if tur == "alim" and g and st and float(st) >= float(g):
        return False, f"stop ({st}) giristen ({g}) yuksek — koruma etmez"
    return True, None


def oturt(taktik: dict, olculen: dict) -> dict:
    """
    Modelin yazdigi seviyeyi OLCULEN degere oturtur ve KAYNAGINI yazar.

    EN YAKIN aday secilir, ilk eslesen DEGIL. Iki seviye birbirine
    tolerans kadar yakin olabilir (olculdu: duz artan bir seride
    `son_kapanis` 159,5 ile `donchian_giris` 159,0 arasinda %0,31 var,
    tolerans %0,5) ve sozluk sirasina gore secmek YANLIS kaynak
    yazdiriyordu. `*_kaynak` alaninin tum amaci "bu sayi nereden geldi"
    sorusuna dogru cevap vermek; yanlis bir koken, koken yazmamaktan
    kotudur.

    BURADA, `agents.Panel` icinde DEGIL: ayni oturtma hem panel
    hakeminde hem gun ici taktikcide gerekiyor ve iki kopya olsaydi
    biri duzeltilip digeri sessizce eski kalirdi.
    """
    adaylar = {k: olculen.get(k) for k in
               ("son_kapanis", "donchian_giris", "donchian_cikis",
                "stop_2n", "sma20", "sma50", "sma200")}
    out = {}
    for alan in ("giris", "stop"):
        deger = taktik.get(alan)
        if deger is None:
            continue
        eslesen = [(abs(float(deger) / float(a) - 1), ad, a)
                   for ad, a in adaylar.items() if _yakin(deger, a)]
        if not eslesen:
            continue
        _, ad, aday = min(eslesen)
        out[alan] = aday
        out[f"{alan}_kaynak"] = ad
    return out


def dosya(db, semboller: list[str]) -> dict:
    """
    Hakem promptuna gomulecek seviye dosyasi: {sembol: seviyeler}.

    Seviyesi hesaplanamayan sembol DISARIDA kalir ve bu, hakemin o
    sembol icin taktik uretmesini yapisal olarak engeller — dogrulama
    zaten reddederdi, ama once teklif etmemesi daha temiz.
    """
    out = {}
    for sem in semboller:
        r = db.query(
            "SELECT id FROM instruments WHERE UPPER(symbol) = ? LIMIT 1",
            (str(sem).upper(),))
        if not r:
            continue
        try:
            s = seviyeler(db, r[0]["id"])
        except Exception as e:                        # noqa: BLE001
            log.warning("[seviye] %s hesaplanamadi: %s", sem, e)
            continue
        if s:
            out[s["sembol"]] = s
    return out


# OLCUM ADI -> INSAN DILI.
#
# `donchian_giris` bir KOD ANAHTARIDIR. Mesajda oldugu gibi gorunmesi,
# kullanicinin "gurultusuz ve anlayacagimiz sekilde" istegine aykiri
# (2026-08-21). Anahtar DEFTERDE oldugu gibi kaliyor — denetim izi
# makine okunur kalmali; degisen yalnizca EKRANDA gorunen ad.
KAYNAK_ADI = {
    "son_kapanis": "son kapanis",
    "donchian_giris": f"{GIRIS_PENCERE} gunun en yuksek kapanisi",
    "donchian_cikis": f"{CIKIS_PENCERE} gunun en dusuk kapanisi",
    # IC ICE PARANTEZ YOK: mesajda zaten parantez icinde
    # gosteriliyor, aciklamayi da parantezle vermek
    # "(2N-ATR stop (kagidin kendi oynakligi))" uretiyordu.
    "stop_2n": "2N-ATR stop — kagidin kendi oynakligi",
    "sma20": "20 gunluk ortalama",
    "sma50": "50 gunluk ortalama",
    "sma200": "200 gunluk ortalama",
}


def kaynak_adi(anahtar: str | None) -> str | None:
    """Olcum anahtarinin okunabilir adi; bilinmiyorsa anahtarin kendisi."""
    if not anahtar:
        return None
    return KAYNAK_ADI.get(str(anahtar), str(anahtar))
