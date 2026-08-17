"""
Haber KONU siniflandirmasi — kademeden AYRI bir eksen.

NEDEN GEREKLI
-------------
2026-08-17 gun sonu raporunda dunya gundemi ve Turkiye gundemi yoktu.
Malzeme VARDI: o gun kademe 2 akisinda "Borsa gunu dususle kapatti",
"Butce uygulama sonuclari aciklandi", "Iran, Hurmuz Bogazi'nda tanker
alikoydu", "Cin'de konut fiyatlari dustu", "Japonya'da BoJ ikilemi",
"Avrupa'da dogalgaz fiyatlari yukselisle basladi" basliklari duruyordu.

Uc sey engelledi:
  1. Bundle tek bir `LIMIT 40` ile haber cekiyordu ve `ORDER BY tier`
     oldugu icin sirket dosyalamalari listeyi dolduruyordu.
  2. Konu ekseni YOKTU: "MasterChef pizza tarifi" ile "Bakan Simsek:
     mali disiplini koruyoruz" ayni kovadaydi.
  3. Raporun cikti yapisinda gundeme ayrilmis bolum yoktu.

Bu modul (2)'yi cozer.

KADEME GUVENILIRLIGI OLCER, ALAKAYI DEGIL
-----------------------------------------
AA ve Ekonomim mesru yayincilardir, o yuzden kademe 2'dirler — ama
akislarinda spor, magazin ve asayis de var. "TFF'den 18 yas alti
duzenleme" kademe 2 bir kaynaktan gelir ve finansal olarak sifir deger
tasir. Iki ekseni ayirmak sart.

TURKCE EK TUZAGI — OLCULDU VE DUZELTILDI
----------------------------------------
Ilk surumde tum kaliplar `\\b(kelime)\\b` seklindeydi. Turkce sondan
eklemeli bir dil ve kapanis `\\b`'si eki goren yerde BASARISIZ oluyor:

    \\benflasyon\\b   "enflasyon" ✓   "enflasyonu" ✗   "enflasyonda" ✗
    \\bbilanço\\b     "bilanço"   ✓   "bilançosu"  ✗

Sonuc: "Sok Marketler'in 6 aylik BILANCOSU aciklandi" siniflanamadi,
"piyasalar ... basladi" hicbir kaliba dusmedi. Sessizce eslesmeyen bir
kalip, hic olmayan bir kaliptan KOTUDUR — var sanilir ve kimse bakmaz.
Artik ek tolere ediliyor (`_ek`); yalnizca kisa/ambigu kisaltmalar tam
eslesme istiyor (`_tam`) cunku "abd" gibi bir kok ek toleransiyla
alakasiz kelimelere yapisir.

DETERMINISTIK, LLM'SIZ — BILEREK
--------------------------------
`proaktif-nabiz` ilke 1: deterministik eleme once, model sonra yorumlar.
Gunde ~200 basligi modele siniflandirmak hem pahali hem tekrarlanamaz.
Kural seti burada acik yazili ve testle kilitli.

COZULEMEYEN KUYRUK GORUNUR KALIR
--------------------------------
`bilinmeyen_yayincilar()` ile ayni gerekce: bir basligi sessizce atmak,
akisin "calisiyor" gibi gorunup gundem uretmemesine yol acar. Siniflanamayan
baslik `belirsiz` olur ve sayisi rapora dusulur.
"""
from __future__ import annotations

import re

# Turkce ek harfleri. IGNORECASE acik oldugu icin buyuk harfler de kapsanir.
_EK = r"[a-zçğıöşü]*"


def _ek(*kelimeler: str) -> re.Pattern:
    """Ek tolere eden kalip: 'enflasyon' -> enflasyonu, enflasyonda, ..."""
    return re.compile(rf"\b(?:{'|'.join(kelimeler)}){_EK}", re.IGNORECASE)


def _tam(*kelimeler: str) -> re.Pattern:
    """
    TAM eslesme. Kisa kisaltmalar icin ZORUNLU: ek toleransiyla "abd"
    "abdal"a, "aa" "aatf"ye, "ism" "isman"a yapisir.
    """
    return re.compile(rf"\b(?:{'|'.join(kelimeler)})\b", re.IGNORECASE)


# --- ELEME: finansal olarak degersiz icerik ------------------------------
ALAKASIZ = [
    _ek("futbol", "süper lig", "super lig", "tff", "fifa", "uefa",
        "teknik direktör", "teknik direktor", "beşiktaş", "besiktas",
        "fenerbahçe", "fenerbahce", "galatasaray", "trabzonspor",
        "basketbol", "voleybol", "olimpiyat", "derbi", "penaltı", "penalti",
        "şampiyona", "sampiyona"),
    _tam("maç", "mac", "gol", "lig"),
    _ek("masterchef", "survivor", "tarifi", "yemek tarif", "pizza",
        "burç", "burc", "astroloji", "magazin", "evlendi", "boşandı",
        "bosandi", "diyet", "zayıfla", "zayifla", "cilt bakım", "cilt bakim",
        "rüya tabiri", "ruya tabiri", "maç özeti", "mac ozeti"),
    _ek("cinayet", "gözaltı", "gozalti", "tutukland", "uyuşturucu",
        "uyusturucu", "hırsız", "hirsiz", "dolandırıcı", "dolandirici",
        "cenaze", "son yolculuğuna", "son yolculuguna", "helikopter düş",
        "helikopter dus", "deprem", "sel felaket", "fırtına", "firtina",
        "el konuldu", "şüpheli", "supheli"),
    _ek("kurultay", "milletvekil", "belediye başkan", "belediye baskan",
        "seçim kaybet", "secim kaybet", "sandalye sayısı", "sandalye sayisi"),
    _tam("chp", "ak parti", "akp", "mhp", "iyi parti", "dem parti"),
    _ek("öğrenci", "ogrenci", "üniversite sınav", "universite sinav",
        "müfredat", "mufredat"),
    _tam("yks", "lgs"),
]

# --- EMTIA / ENERJI ------------------------------------------------------
EMTIA_ENERJI = [
    _ek("petrol", "brent", "ham petrol", "rafineri",
        "doğal gaz", "dogal gaz", "doğalgaz", "dogalgaz", "boru hattı",
        "boru hatti", "hürmüz", "hurmuz", "tanker", "akaryakıt", "akaryakit",
        "benzin", "motorin", "elektrik fiyat", "gümüş", "gumus",
        "emtia", "buğday", "bugday", "kömür", "komur", "karbon emisyon",
        "uranyum", "altın fiyat", "altin fiyat", "gram altın", "gram altin",
        "ons altın", "ons altin", "külçe altın", "kulce altin"),
    # EK TOLERANSI OLMAYAN KOKLER — ek eklenince gundelik kelimeye
    # donusuyorlar ve OLCULDU:
    #   "altın"  + ek -> "altında"  ("beklentilerin ALTINDA gelen buyume"
    #                                 haberi emtia sayildi)
    #   "varil"  + ek -> "varıldı"  ("ateskese VARILDI" haberi emtia sayildi)
    # Baslikta bu kokler neredeyse her zaman yalin ya da bir isim
    # tamlamasinin basinda gecer ("Altin, bakir, uranyum...", "altin
    # fiyatlari"), o yuzden tam eslesme kayip yaratmiyor.
    _tam("altın", "altin", "bakır", "bakir", "varil", "tahıl", "tahil",
         "wti", "opec", "opek", "lng", "ons"),
]

# --- MAKRO: ONCE KONU, SONRA BOLGE ---------------------------------------
#
# "Enflasyon", "merkez bankasi", "faiz karari", "butce" TR'de de dunyada
# da gecer. Ilk surumde bunlar dogrudan `makro_tr` sayildi ve olcum hemen
# yakaladi: "Brezilya Merkez Bankasi", "Japonya'da BoJ ikilemi" ve
# "Fed'in politika durusu Hazine getirilerini yuksek tutuyor" hepsi
# TURKIYE gundemine dustu. Cozum iki asamali: once MAKRO MU, sonra
# HANGI BOLGE.
MAKRO_ORTAK = [
    _ek("enflasyon", "deflasyon", "tüfe", "tufe",
        "merkez bankası", "merkez bankasi", "politika faizi", "faiz kararı",
        "faiz karari", "faiz indirim", "faiz artır", "faiz artir",
        "tahvil getiri", "getiri eğrisi", "getiri egrisi",
        "resesyon", "durgunluk", "büyüme oran", "buyume oran",
        "işsizlik oran", "issizlik oran", "istihdam rapor",
        "bütçe açığ", "butce acig", "bütçe denge", "bütçe uygulama",
        "merkezi yönetim bütçe", "cari açık", "cari acik", "cari denge",
        "dış ticaret açığ", "dis ticaret acig",
        "çekirdek enflasyon", "cekirdek enflasyon",
        "tarife", "gümrük vergi", "gumruk vergi", "ticaret savaş",
        "ticaret savas", "kredi derecelendirme", "kredi notu",
        "mali disiplin", "maliye politika", "para politika",
        "bütçe disiplin", "butce disiplin", "vergi düzenleme",
        "borsa gün", "borsa güne", "borsa hafta", "endeks kapan",
        "dolar endeksi", "döviz kuru", "doviz kuru",
        "ihracat", "ithalat", "sanayi üretim", "sanayi uretim",
        "konut satış", "konut satis", "konut fiyat", "perakende satış",
        "perakende satis", "tüketici güven", "tuketici guven",
        "konut inşaatçı", "konut insaatci",
        "kapasite kullanım", "kapasite kullanim"),
    _tam("pmi", "cpi", "ppi", "dxy", "gsyh", "gsyih"),
    # COK KELIMELI KALIP AYRI: "piyasalar ... basladi" gibi araya kelime
    # giren yapilar tek alternasyonda ifade edilemiyor.
    re.compile(r"\b(?:borsalar|piyasalar|endeksler)\w*\s.{0,40}?"
               r"(kapa|başla|basla|yüksel|yuksel|düş|dus|geriled|rekor)",
               re.IGNORECASE),
]

# BOLGE ISARETLERI. Yabanci isaret varsa global kazanir — "Avrupa
# borsalari" ve "Kanada'da enflasyon" TR gundemi degildir.
YABANCI_ISARET = [
    _ek("federal reserve", "powell", "lagarde", "bank of england",
        "dünya bankası", "dunya bankasi", "amerika", "wall street",
        "avrupa", "euro bölge", "euro bolge", "küresel", "kuresel",
        "nasdaq", "dow jones", "nikkei", "hang seng"),
    # ULKE ADLARI. Olculdu: "Nijerya'da tuketici enflasyonu %15,43'e
    # geriledi" TR gundemine dusuyordu — Nijerya listede yoktu, TR
    # isareti de yoktu ve yayin dili Turkce oldugu icin TR varsayildi.
    # Yayin dili varsayilani ancak ULKE ADI GECMEYEN haberler icin
    # dogru bir kestirim; ulke gecen her haber once buradan elenmeli.
    _ek("almanya", "fransa", "ingiltere", "japonya", "hindistan", "rusya",
        "brezilya", "kanada", "meksika", "güney kore", "guney kore",
        "asya", "nijerya", "mısır", "misir", "arjantin", "güney afrika",
        "guney afrika", "endonezya", "vietnam", "polonya", "macaristan",
        "isviçre", "isvicre", "hollanda", "belçika", "belcika", "italya",
        "ispanya", "yunanistan", "israil", "suudi", "katar", "avustralya",
        "isveç", "isvec", "norveç", "norvec", "danimarka", "irlanda",
        "portekiz", "avusturya", "çekya", "cekya", "romanya", "bulgaristan",
        "ukrayna", "iran ", "pakistan", "tayland", "filipin", "malezya",
        "singapur", "nijer", "kenya", "fas ", "cezayir", "tunus"),
    _tam("fed", "fomc", "ecb", "boj", "pboc", "imf", "oecd", "abd",
         "çin", "cin", "bae", "s&p 500", "dax", "ab"),
]

TR_ISARET = [
    _ek("türkiye", "turkiye", "borsa istanbul", "hazine ve maliye",
        "şimşek", "simsek", "asgari ücret", "asgari ucret",
        "kur korumalı", "kur korumali", "türk lirası", "turk lirasi",
        "bakanlığ", "bakanlig", "hükümet", "hukumet"),
    _tam("tcmb", "tüik", "tuik", "bddk", "spk", "ppk", "kkm", "bist",
         "tl", "lira", "bakan", "meclis"),
]

# Yalnizca TR'de ya da yalnizca disarida gecen, bolge testine ihtiyaci
# olmayan terimler.
MAKRO_TR_OZEL = [
    _ek("kur korumalı", "kur korumali", "asgari ücret", "asgari ucret",
        "hazine ve maliye"),
    _tam("tcmb", "tüik", "tuik", "ppk", "kkm", "bddk",
         "bist 100", "bist100", "bist 30", "bist30"),
]
MAKRO_GLOBAL_OZEL = [
    _ek("federal reserve", "powell", "lagarde", "bank of england",
        "dünya bankası", "dunya bankasi", "tarım dışı", "tarim disi",
        "non-farm", "nonfarm"),
    _tam("fed", "fomc", "ecb", "boj", "pboc", "imf", "oecd"),
]

# --- JEOPOLITIK ----------------------------------------------------------
JEOPOLITIK = [
    _ek("savaş", "savas", "ateşkes", "ateskes", "yaptırım", "yaptirim",
        "ambargo", "birleşmiş milletler", "birlesmis milletler",
        "israil", "filistin", "gazze", "ukrayna", "tayvan", "kuzey kore",
        "suriye", "putin", "netanyahu", "zelenski", "askeri", "füze",
        "fuze", "insansız hava", "insansiz hava", "savunma sanayi"),
    _tam("nato", "iran", "irak", "iha", "sİha", "siha", "ab"),
    # LIDER ADI TEK BASINA YETMEZ. "Trump" piyasa basliklarinda gunde
    # onlarca kez geciyor ("Bitcoin'de Trump beklentisi", "Trump'in balo
    # salonu") ve tek basina kalinca jeopolitik kovasini dolduruyordu.
    # Catisma baglami ARANIR.
    re.compile(r"\btrump\w*\b.{0,60}?(savaş|savas|ateşkes|ateskes|yaptırım|"
               r"yaptirim|tarife|gümrük|gumruk|asker|füze|fuze|tehdit|"
               r"ilan ed)", re.IGNORECASE),
]

# --- SIRKET OLAYI --------------------------------------------------------
SIRKET_OLAYI = [
    _ek("bilanço", "bilanco", "finansal rapor", "çeyrek sonuç",
        "ceyrek sonuc", "sermaye artırım", "sermaye artirim", "halka arz",
        "temettü", "temettu", "kar payı", "kar payi", "geri alım",
        "geri alim", "satın al", "satin al", "birleşme", "birlesme",
        "devralma", "iştirak", "istirak", "ihale", "sözleşme", "sozlesme",
        "yatırım kararı", "yatirim karari", "özel durum açıklama",
        "ozel durum aciklama", "faaliyet rapor"),
]

# TURKCE YAYIN AKISLARI. Bolge testi isaretsiz kaldiginda karar bunlara
# gore verilir: Turkce bir ekonomi akisindaki isaretsiz bir makro haber
# ("Mikro ihracat kargolarinin transit surecleri basitlestirilecek")
# neredeyse her zaman Turkiye gundemidir. Ingilizce akista tersi.
TR_YAYIN = re.compile(
    r"(anadolu ajans|aa ?-|ekonomim|dünya|dunya|bloomberg ?ht|"
    r"investing tr|trt|hürriyet|hurriyet|milliyet|habertürk|"
    r"haberturk|patronlar|foreks|matriks|bigpara)", re.IGNORECASE)

# SIRA sonucu belirler: once eleme, sonra en dar tanimli konu.
_SIRA: list[tuple[str, list]] = [
    ("alakasiz", ALAKASIZ),
    ("emtia_enerji", EMTIA_ENERJI),
    ("makro_tr", MAKRO_TR_OZEL),
    ("makro_global", MAKRO_GLOBAL_OZEL),
    ("_makro", MAKRO_ORTAK),          # bolge testine gider
    ("jeopolitik", JEOPOLITIK),
    ("sirket", SIRKET_OLAYI),
]

KONULAR = ("makro_tr", "makro_global", "emtia_enerji", "jeopolitik",
           "sirket", "alakasiz", "belirsiz")

KONU_ETIKET = {
    "makro_tr":     "Turkiye makro gundemi",
    "makro_global": "Dunya makro gundemi",
    "emtia_enerji": "Emtia ve enerji",
    "jeopolitik":   "Jeopolitik",
    "sirket":       "Sirket haberi",
    "alakasiz":     "Finansal olarak alakasiz",
    "belirsiz":     "Siniflandirilamadi",
}

# Rapora GIRMEYEN konular. Ayri sabit, cunku "hangi konu gundem sayilir"
# sorusunun cevabi tek yerde durmali.
GUNDEM_KONULARI = ("makro_tr", "makro_global", "emtia_enerji", "jeopolitik")


def konu(baslik: str | None, ozet: str | None = None,
         semboller: str | list | None = None,
         yayinci: str | None = None) -> str:
    """
    Basliktan (ve varsa ozetten) konu etiketi.

    `semboller` DOLUYSA once ona bakilmaz, BILEREK: "Nvidia, SB Energy'ye
    1,5 milyar dolar yatirim yapacak" hem sirket hem enerji haberidir ama
    makro/jeopolitik kalip bir sirket olayindan DAHA GENIS bir olayi
    isaret eder ve rapor icin o oncelikli. Tek istisna `alakasiz`:
    sembolu olan bir haber alakasiz sayilmaz ("Besiktas'in sponsoru Tera
    Holding oldu" gercekten bir sirket olayidir).

    BASLIK ONCE, OZET YEDEK. Ozet basliga esit agirlikta okununca
    RSS govdesindeki tesaduf bir kelime konuyu belirliyordu; olculdu:
      "Japonya'da dusuk buyume ile yuksek enflasyonun BoJ'u ... ikilemde"
      -> ozetteki "beklentilerin ALTINDA" yuzunden EMTIA sayildi
      "Trump'in damadi Kushner, Netanyahu ile bir araya geldi"
      -> ozetteki "VARILDI" yuzunden EMTIA sayildi
    Bir haberin NE HAKKINDA oldugunu basligi soyler; ozet ancak baslik
    hicbir kaliba dusmediginde devreye girer.
    """
    baslik = (baslik or "").strip()
    ozet = (ozet or "").strip()
    if not (baslik or ozet):
        return "belirsiz"

    sembollu = bool(semboller) and str(semboller).strip() not in ("", "[]", "null")

    sonuc = _esle(baslik, sembollu, yayinci) if baslik else "belirsiz"
    if sonuc == "belirsiz" and ozet:
        sonuc = _esle(f"{baslik} {ozet}", sembollu, yayinci)
    if sonuc == "belirsiz" and sembollu:
        return "sirket"
    return sonuc


def _esle(metin: str, sembollu: bool, yayinci: str | None) -> str:
    for ad, kaliplar in _SIRA:
        if not any(k.search(metin) for k in kaliplar):
            continue
        if ad == "alakasiz" and sembollu:
            return "sirket"
        if ad == "_makro":
            return _makro_bolgesi(metin, yayinci)
        return ad
    return "belirsiz"


def _makro_bolgesi(metin: str, yayinci: str | None = None) -> str:
    """
    Ortak makro terimi bulundu — hangi ulkenin gundemi?

    YABANCI ISARET ONCE KAZANIR. "Avrupa borsalari Italya haric dususle
    kapatti" cumlesinde hem "borsa" hem "avrupa" var; TR isareti once
    denenirse Turkiye gundemine duser. Olcumde tam bu oldu: Brezilya,
    Japonya ve Fed haberleri `makro_tr` cikti.

    Hicbir isaret yoksa YAYINCININ DILI karar verir. Olculdu: "Mikro
    ihracat kargolarinin hava yolu transit surecleri basitlestirilecek"
    ne yabanci ne TR isareti tasiyor ama AA Ekonomi'den geliyor ve
    elbette Turkiye gundemi. Kardes basligi ("Ticaret Bakanligi'ndan...")
    "Bakanligi" sayesinde dogru dusuyordu — yani ayrim tesadufe kalmisti.
    """
    if any(k.search(metin) for k in YABANCI_ISARET):
        return "makro_global"
    if any(k.search(metin) for k in TR_ISARET):
        return "makro_tr"
    if yayinci and TR_YAYIN.search(yayinci):
        return "makro_tr"
    return "makro_global"


def konu_dagilimi(db, gun: int = 7) -> dict[str, int]:
    """Konu bazinda haber sayisi — kapsam beyaninin girdisi."""
    return {r["konu"] or "belirsiz": r["n"] for r in db.query(
        """SELECT konu, COUNT(*) n FROM news
           WHERE published_at >= datetime('now', ?) AND tier IN (1,2)
           GROUP BY konu ORDER BY n DESC""", (f"-{gun} days",))}


def siniflanmamis_basliklar(db, gun: int = 7, limit: int = 20) -> list[str]:
    """
    `belirsiz` kalan basliklar.

    GORUNUR OLMALI: sessizce dusen bir baslik, akisin "calisiyor" gibi
    gorunup gundem uretmemesine yol acar. Liste kisaldikca kural seti
    guclenir; gorunmezse hic guclenmez.
    """
    return [r["title"] for r in db.query(
        """SELECT title FROM news
           WHERE published_at >= datetime('now', ?) AND tier IN (1,2)
             AND (konu IS NULL OR konu = 'belirsiz')
           ORDER BY published_at DESC LIMIT ?""", (f"-{gun} days", limit))]
