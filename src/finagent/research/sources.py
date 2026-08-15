"""
Kaynak guvenilirlik siniflandirmasi.

NEDEN GEREKLI
-------------
Enstruman bazli haber akisinin buyuk cogunlugu SEO icerik ciftligidir.
Olculdu (2026-08-15, Google News, son 3 gun):

    NVIDIA       -> MarketBeat 41, Seeking Alpha 4, TradingView 3,
                    Kalkine 3, TechStock2 3 ... CNBC yalnizca 1
    Novo Nordisk -> MarketBeat 10, Stocktwits 5, Pluang 4 ... CNBC 3
    Shell        -> Kalkine 7, AD HOC NEWS 4, JournalArta 3, Reuters 3

Filtresiz verilirse model, otomatik uretilmis pazarlama metinleri uzerinden
"analiz" yapar ve sonuc ikna edici ama dayanaksiz olur.

TEMEL ILKE
----------
Haberi TESLIM EDEN site degil, ASIL YAZAN kurulus onemlidir.
Yahoo Finance'te cikan bir Reuters haberi Reuters'tir; ayni yerde cikan bir
Motley Fool yazisi pazarlamadir.

IZIN LISTESI (blocklist DEGIL)
------------------------------
Cop yayinci sayisi sinirsiz ve her gun yenisi turuyor; guvenilir olanlarin
kumesi ise kucuk ve sayilabilir. Bu yuzden varsayilan REDDET'tir: listede
olmayan yayinci "bilinmeyen" kademesine duser ve kanit olarak kullanilmaz.
"""
from __future__ import annotations

import re

# --- KADEME 2: haber ajanslari, buyuk finans gazeteleri, ciddi meslek basini
# Sirketin kendisi degil ama teyit sureci, kunye ve duzeltme politikasi olan
# yayin organlari.
KADEME2 = {
    # ajanslar
    "reuters", "bloomberg", "associated press", "ap news", "agence france",
    "afp", "dow jones", "nikkei", "kyodo",
    # finans gazeteleri / dergileri
    "financial times", "wall street journal", "wsj", "barron", "the economist",
    "cnbc", "marketwatch", "fortune", "forbes", "business insider",
    "het financieele dagblad", "fd.nl", "handelsblatt", "les echos",
    "borsen", "dagens industri", "the times", "the telegraph", "the guardian",
    "de tijd", "nrc", "trouw", "volkskrant",
    # arastirma / veri kuruluslari
    "morningstar", "s&p global", "moody", "fitch ratings",
    # meslek basini (sektorel, ciddi)
    "fierce pharma", "fierce biotech", "medwatch", "endpoints news",
    "the information", "semianalysis", "tom's hardware", "anandtech",
    "aviation week", "just-auto", "automotive news", "oil price",
    # TR
    "anadolu ajansi", "aa", "bloomberg ht", "dunya", "ekonomim",
}

# --- KADEME 3: toplayici / sendikator.
# Kendi haberciligi yok; baskasinin icerigini tasir. Yalnizca ASIL KAYNAGI
# belirtiliyorsa ve o kaynak Kademe 2 ise yukari tasinir.
KADEME3 = {
    "yahoo finance", "yahoo", "investing.com", "msn", "tradingview",
    "marketscreener", "stocktitan", "stock titan", "quartr", "finanzen",
    "google news", "smartkarma", "the globe and mail", "nasdaq.com", "nasdaq",
}

# --- KADEME 4: gorus / promosyon / icerik ciftligi.
# Kanit olarak ASLA kullanilmaz. Bir kismi acikca abonelik pazarlamasi
# (Motley Fool, Zacks), bir kismi otomatik uretim (MarketBeat, Kalkine).
KADEME4 = {
    "motley fool", "fool.com", "zacks", "seeking alpha", "marketbeat",
    "kalkine", "tipranks", "benzinga", "24/7 wall st", "simply wall st",
    "simplywall.st", "investorplace", "stocktwits", "moomoo", "pluang",
    "insider monkey", "gurufocus", "barchart", "techstock", "wccftech",
    "breakingthenews", "ad hoc news", "journalarta", "financhill",
    "stocknews", "vested finance", "public.com", "etoro",
}

KADEME_ETIKET = {
    1: "birincil (sirket/duzenleyici)",
    2: "haber ajansi / finans basini",
    3: "toplayici",
    4: "gorus / promosyon",
    0: "bilinmeyen",
}


def _norm(ad: str | None) -> str:
    if not ad:
        return ""
    s = str(ad).casefold().strip()
    s = re.sub(r"\s*[-–|]\s*(com|net|org)\b", "", s)
    return re.sub(r"\s+", " ", s)


def _iceriyor(ad: str, kume: set[str]) -> bool:
    return any(k in ad for k in kume)


def kademe(publisher: str | None) -> int:
    """
    Yayinci -> guvenilirlik kademesi.

    0 = bilinmeyen (varsayilan; kanit olarak kullanilmaz)
    """
    ad = _norm(publisher)
    if not ad:
        return 0
    # Once 4: 'Yahoo Finance'te yayinlanan Motley Fool' gibi durumlarda
    # promosyon etiketi toplayici etiketini EZMELI.
    if _iceriyor(ad, KADEME4):
        return 4
    if _iceriyor(ad, KADEME2):
        return 2
    if _iceriyor(ad, KADEME3):
        return 3
    return 0


def sirket_kaynagi(publisher: str | None, sirket_adi: str | None) -> bool:
    """
    Yayinci, sirketin KENDI yayin organi mi?

    Olcumde bunlar "bilinmeyen" kademesine dusuyordu ama aslinda en birincil
    kaynaklar: 'Amazon Web Services (AWS)', 'ING Think', 'Microsoft'.
    Sirketin kendi blogu/haber odasi = Kademe 1.

    Kural: ILK anlamli belirtec ayni olmali. Yalnizca "iceriyor" demek
    tehlikeli olurdu — 'Royal Dutch Shell Plc .com' Shell'e ait degil,
    onu elestiren bagimsiz bir sitedir; ilk belirteci ROYAL oldugu icin
    bu kural onu dogru sekilde disarida birakir.
    """
    from .identity import ad_belirteci

    p, s = ad_belirteci(publisher), ad_belirteci(sirket_adi)
    if not p or not s:
        return False
    return p[0] == s[0] and len(p[0]) >= 3


# Sirketin KURUMSAL kanallari — yatirimciya yonelik duyuru yollari.
_IR_IZLERI = ("/press", "/news", "/newsroom", "/investor", "/ir/", "/ir-",
              "/media", "/announcement", "/releases", "/press-release",
              "press.", "news.", "investor.", "ir.")
# Ayni sirketin MUHENDISLIK/URUN/TUKETICI icerigi — yatirim kanidi degil.
# Once bunlar kontrol edilir: 'nvidia.com/en-us/geforce/news/...' yolu
# '/news' iceriyor ama icerigi oyun duyurusu ('Gears of War Beta'),
# yatirimci bildirimi degil.
_BLOG_IZLERI = ("/blogs/", "/blog/", "/developer", "/devblog", "/docs",
                "/engineering", "/support/", "/tutorials", "/learn/",
                "/geforce", "/gaming", "/games", "/shop", "/store",
                "/products/", "/drivers", "/community")


def sirket_kaynagi_kademe(publisher: str | None, sirket_adi: str | None,
                          url: str | None) -> int | None:
    """
    Sirketin kendi kanaliysa hangi kademe?

    1 = kurumsal duyuru (IR / haber odasi / basin bulteni) -> birincil kanit
    3 = ayni sirketin muhendislik/urun blogu -> bilgi, kanit degil

    Gerekce: olcumde Amazon'un AWS muhendislik blogu 54 kayitla Kademe 1'i
    doldurdu ("AWS Certificate Manager will discontinue email validation").
    Sirketin kendi yayini olmasi onu YATIRIM kanidi yapmaz; kurumsal duyuru
    kanali ile gelistirici blogu ayni sey degildir.
    """
    if not sirket_kaynagi(publisher, sirket_adi):
        return None
    u = (url or "").casefold()
    if any(iz in u for iz in _BLOG_IZLERI):
        return 3
    if any(iz in u for iz in _IR_IZLERI):
        return 1
    # Yol belirsizse temkinli davran: kanit sayma.
    return 3


def guvenilir(publisher: str | None) -> bool:
    """Analizde KANIT olarak kullanilabilir mi?"""
    return kademe(publisher) in (1, 2)


def ozet(sayaclar: dict[int, int]) -> str:
    return " · ".join(f"K{k}: {sayaclar[k]}" for k in sorted(sayaclar) if sayaclar[k])
