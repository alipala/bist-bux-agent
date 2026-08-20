"""
YETENEK HARITASI — botun kendini anlatmasi.

NEDEN URETILIYOR, YAZILMIYOR
----------------------------
Bu projenin tekrar eden kusur sinifi: BEYAN EDILEN durumun GERCEK
durumdan sessizce ayrilmasi. Elle yazilan bir "neler yapabilirim" metni
tam olarak bu: bir arac eklendigi anda eksik, bir arac kaldirildiginda
YALAN olur ve kimse fark etmez. Mevcut `/yardim` metni bunu zaten
yasadi (`/hepsi` komutu listede hic yoktu).

Bu yuzden:
  * ARAC LISTESI koddan gelir (`ToolBox.araclar()` -> ad + sema).
  * KOMUT LISTESI burada TEK YERDE tanimlidir ve `_on_text`'in gercek
    dallanmasiyla TEST EDILEREK karsilastirilir (bkz. duman testleri).
  * Elle yazilan TEK sey her aracin SADE KARSILIGI (`SADE`), cunku
    araclarin kendi aciklamalari MODELE yazilmis ("hesap bos
    birakilirsa TUM hesaplar doner"), kullaniciya degil.
  * `SADE` eksiksizligi TESTLE ZORUNLU: karsiligi olmayan bir arac
    eklenirse test kirilir. Rehberin curumesini engelleyen sey budur.

BAGLAMA GIRMEZ, CAGRILIR
------------------------
Bu katalog prompt'a KONMAZ. Envanter 4293 -> 317 karaktere indirilirken
ogrenilen sey: asil zarar token degil DIKKAT SEYRELMESI. Model
gerektiginde `neler_yapabilirim` aracini cagirir.
"""
from __future__ import annotations

# ---------------------------------------------------------------------
# Araclarin SADE karsiligi. Anahtar = aracin gercek adi.
# Eksik/fazla anahtar duman testinde patlar.
SADE: dict[str, str] = {
    "veri_durumu": "elimde ne var, ne yok — neyin eksik oldugunu soyler",
    "portfoy": "kayitli pozisyonlarin ve agirliklari",
    "ara": "sembol/sirket arama — 'hangi kod hangi sirket'",
    "teknik": "gunluk gostergeler: ortalamalar, RSI, trend, oynaklik",
    "saatlik": "kriptoda saatlik hareket (1s / 24s / 7g)",
    "tokenomik": "coin'in arzi, piyasa degeri, tavan/dip",
    "finansallar": "hissenin bilanco ve kar verisi (ABD hisseleri)",
    "haberler": "haberler ve resmi bildirimler — kaynak kademesiyle",
    "fiyat_getir": "kapsamda olmayan bir kagidin fiyatini ANINDA ceker",
    "haber_firsatlari": "son haberlerden aday cikarir — hangi kagida bakmali",
    "gundem": "Turkiye/dunya makro gundemi, emtia ve jeopolitik — sembolsuz",
    "kaynak_kademesi": "bir kaynak guvenilir mi — web sonuclarini kontrol eder",
    "olay_etkisi": "haber gunlerinde fiyat gercekten kimildadi mi",
    "takvim": "yaklasan resmi olaylar — PPK, Fed, enflasyon raporu",
    "karsilastir": "birkac kagidi yan yana koyar — hangisi daha oynak, "
                   "hangileri birlikte hareket ediyor",
    "iliski": "iki sey birbirini etkiliyor mu — petrol ve borsa, dolar ve altin",
    "pencere_istatistigi": "gecmiste 'su surede su kadar kar' kac kez tuttu",
    "maruziyet": "portfoyun butunu dolara/altina/petrole/faize ne kadar bagli",
    "fiyat_serisi": "belirli bir tarihteki fiyat, ham seri",
    "fx": "kur cevirme (EUR/USD/TRY) — karsilastirmadan once",
    "grafik": "grafik cizer: fiyat, karsilastirma, portfoy dagilimi",
    "kaynak_goruntusu": "kaynak sayfanin canli ekran goruntusu (capraz kontrol)",
    "gunun_hareketlileri": "BIST'te bugun en cok artan/azalan",
    "kimlik": "bu sembol gercekten hangi sirket/coin, nasil dogrulandi",
    "pozisyon_kaydet": "portfoye pozisyon yazar — HER ZAMAN onayina sunarak",
    "hatirla": "kalici bir kuralini/olguni hatirlar — onayina sunarak",
    "izlemeye_al": "yeni sembolu takibe alir, verisi toplanmaya baslar",
    "veri_topla": "veriyi tazeler (fiyat, haber, kripto, bilanco)",
    "gecmis_gorus": "daha once ne dedim ve tuttu mu",
    "gecmis_ozet": "daha once gonderdigim ozet ve raporlar",
    "sohbet_arsivi": "gecmis sohbetlerimiz — ne sormustun, ne demistim",
    "hatirladiklarin": "kalici olarak neleri bildigim (kurallar, olgular)",
    "bekleyen_okumalar": "onay bekleyen ekran goruntusu okumalari",
    "izleme_listesi": "hangi sembolleri takip ediyorum",
    "endeks_uyeleri": "bir endeksin uye hisseleri (BIST 100, S&P 500...)",
    "saat": "su anki saat ve hangi borsa acik",
    "rapor_uret": "tam gunluk raporu uretir (onayina sunarak)",
    "son_kaydi_sil": "son portfoy kaydini geri alir (onayina sunarak)",
    "neler_yapabilirim": "bu rehberin kendisi",
    "ipucu": "sana bir ozelligi ilk kez anlatirken kullandigim not",
}

# ---------------------------------------------------------------------
# Komutlar. TEK KAYNAK — `_on_text`'teki dallanma ile test karsilastirir.
KOMUTLAR: dict[str, str] = {
    "yardim": "kisa yardim",
    "start": "kisa yardim",
    "help": "kisa yardim",
    "rehber": "gezinilebilir rehber (bu menu)",
    "durum": "veritabani ozeti — ne kadar veri var",
    "portfoy": "portfoyun",
    "evren": "BUX katalogunda arama",
    "aday": "katalogdaki bir kagidi arastirma kapsamina al",
    "haber": "kaynak taramasi ya da bir sembolun kaynaklari",
    "etki": "haberin fiyata olculebilir etkisi var mi",
    "takip": "izleme listesi",
    "kimlik": "ISIM = TICKER seklinde kimligi elle ata",
    "rapor": "veri topla + tam rapor uret",
    "ozet": "mevcut veriden rapor (toplamadan)",
    "bekleyen": "onay bekleyen ekran goruntusu okumalari",
    "onayla": "bekleyen okumalari kaydet",
    "hepsi": "bekleyenlerin HEPSINI kaydet",
    "sil": "SON kaydi geri al",
    "hatirladiklarin": "kalici olarak neleri bildigim; `unut <no>` ile tek tek gecersizlestir",
    "temizle": "indirilen medyayi ve eski kayitlari sil",
    "unut": "modelin gordugu sohbet hafizasini temizle",
}

# ---------------------------------------------------------------------
# Rehber konulari. `araclar` alanlari GERCEK arac adlaridir; testte
# `SADE` ve canli arac listesiyle karsilastirilir.
KONULAR: dict[str, dict] = {
    "portfoy": {
        "emoji": "💼",
        "baslik": "Portfoyum",
        "giris": (
            "Ekran goruntusu at, okurum. <b>BUX, Binance ve Midas</b> — "
            "ucu de ayri hesap olarak durur.\n"
            "Okudugumu <b>onayina sunmadan hicbir sey yazmam.</b>"),
        "araclar": ["portfoy", "pozisyon_kaydet", "bekleyen_okumalar",
                    "son_kaydi_sil", "grafik", "fx"],
        "komutlar": ["portfoy", "bekleyen", "onayla", "hepsi", "sil"],
        "dene": ["portfoy ekraninin resmini at, \"bunlari portfoyume ekle\" yaz",
                 "portfoyumun en buyuk riski ne?",
                 "portfoyumun dagilimini ciz"],
        "not": ("Portfoy tek ekrana sigmiyorsa arka arkaya birkac gorsel at "
                "— 20 dakika icinde gelenler TEK portfoy olarak birlesir."),
    },
    "analiz": {
        "emoji": "📊",
        "baslik": "Analiz",
        "giris": ("Bir kagit ya da coin hakkinda ne biliyorsam onu, "
                  "<b>bilmediklerimi de soyleyerek</b> anlatirim."),
        "araclar": ["teknik", "fiyat_serisi", "finansallar", "olay_etkisi",
                    "gunun_hareketlileri", "grafik", "rapor_uret"],
        "komutlar": ["etki", "rapor", "ozet"],
        "dene": ["ASELSAN nasil gidiyor?",
                 "ASML ile NVDA'yi karsilastir",
                 "NVDA'da bu hafta ne oldu, fiyata etkisi olculebilir mi?"],
        "not": ("Bir sayiyi hafizamdan soylemem — aracla cekerim. "
                "Cekemezsem \"yok\" derim, uydurmam."),
    },
    "veri": {
        "emoji": "🔍",
        "baslik": "Veri",
        "giris": ("Neyin var neyin yok oldugunu sorabilirsin. "
                  "Veri bayatsa tazeleyebilirim."),
        "araclar": ["veri_durumu", "saat", "ara", "kimlik", "endeks_uyeleri",
                    "haberler",
                    "veri_topla", "izlemeye_al", "izleme_listesi",
                    "kaynak_goruntusu"],
        "komutlar": ["durum", "evren", "aday", "haber", "takip", "kimlik"],
        "dene": ["elinde ASELSAN hakkinda ne var?",
                 "kripto fiyatlarini tazele",
                 "BTC'yi izlemeye al"],
        "not": ("Haberlerde <b>kaynak kademesi</b> var: 1 = resmi beyan, "
                "2 = ajans/finans basini, 3-4 = toplayici (kanit saymam)."),
    },
    "kripto": {
        "emoji": "₿",
        "baslik": "Kripto",
        "giris": ("Binance'te islem goren coin'ler + CoinGecko ilk 100. "
                  "Fiyat, saatlik hareket ve arz verisi."),
        "araclar": ["saatlik", "tokenomik", "teknik", "fiyat_serisi"],
        "komutlar": [],
        "dene": ["BTC son 24 saatte ne yapti?",
                 "SOL'un arzi ne kadar, kac tanesi dolasimda?",
                 "ROSE nasil gidiyor?"],
        "not": ("Kriptoda F/K yoktur; karsiligi <b>tokenomik</b>tir. "
                "Ayrica saatlik ve gunluk gostergeler AYRI olceklerdir, "
                "karistirmam."),
    },
    "gecmis": {
        "emoji": "📈",
        "baslik": "Gecmisim",
        "giris": ("Ne konustugumuzu ve ne dedigimi saklarim. "
                  "Sohbet kaydi <b>budanmaz</b>."),
        "araclar": ["sohbet_arsivi", "gecmis_gorus", "gecmis_ozet",
                    "hatirladiklarin", "hatirla"],
        "komutlar": ["unut"],
        "dene": ["bana ASELSAN hakkinda daha once ne demistin?",
                 "gecen hafta ne konusmustuk?",
                 "tahminlerin tuttu mu?"],
        "not": ("Gecmiste soyledigim sey bir <b>alinti</b>dir, olcum degil "
                "— bugunku sayilari yeniden cekerim. "
                "<code>/unut</code> hafizami temizler, arsiv kalir."),
    },
    "komutlar": {
        "emoji": "⌨️",
        "baslik": "Komutlar",
        "giris": ("<b>Hicbirini ezberlemek zorunda degilsin</b> — hepsi "
                  "duz cumleyle de yapilir. Komut icin <b>/</b> gerekir; "
                  "cizgisiz yazdigin her sey bana gelir. Kisayol isteyene:"),
        "araclar": [],
        "komutlar": None,          # None = HEPSI (tek kaynak: KOMUTLAR)
        "dene": [],
        "not": ("Sesli mesaj da olur (cihazda yaziya cevrilir). "
                "<code>/sil</code> ve <code>/unut</code> sesle "
                "<b>calistirilmaz</b> — yanlis duyulan tek kelime veri siler."),
    },
}

# ---------------------------------------------------------------------
# OGRETME ANI. Model bunlardan birini KOD ile ister; ayni kod bir kisiye
# BIR KEZ gider (bkz. `ogretilen` tablosu). Tekrar eden ipucu, risk
# alarmlarinda yasanan bildirim yorgunlugunun aynisini uretir.
IPUCLARI: dict[str, str] = {
    "ekran_goruntusu": (
        "💡 Portfoyunu ekran goruntusuyle paylasabilirsin — resmi at, "
        "\"portfoyume ekle\" yaz. BUX, Binance ve Midas'i ayri tutarim."),
    "gorsel_aciklama": (
        "💡 Gorsele bir aciklama yazarsan ona gore is yaparim "
        "(\"bunlar bende var mi\", \"bu coin nasil\")."),
    "grafik": (
        "💡 Bunu cizdirebilirsin de: \"grafigini ciz\" ya da "
        "\"portfoyumun dagilimini ciz\"."),
    "veri_tazele": (
        "💡 Veri bayatladiysa \"fiyatlari tazele\" diyebilirsin; "
        "collector'lari kendim calistiririm."),
    "gecmis": (
        "💡 Gecmisi bana sorabilirsin: \"gecen hafta ne konusmustuk\", "
        "\"bana ne demistin\" — sohbet kaydim budanmiyor."),
    "sesli": (
        "💡 Sesli mesaj da gonderebilirsin; cihazda yaziya ceviririm, "
        "ses disari cikmaz."),
    "izleme": (
        "💡 Takip etmedigim bir sembolu \"izlemeye al\" dersen verisi "
        "toplanmaya baslar."),
    "kaynak": (
        "💡 Bir sayidan suphelenirsen \"kaynak goruntusunu al\" de — "
        "kaynagin canli ekran goruntusunu cekip yan yana koyarim."),
    "rehber": (
        "💡 Neler yapabildigimin tamamini <code>/rehber</code> ile "
        "gezebilirsin."),
}


# ---------------------------------------------------------------------
def arac_adlari(toolbox) -> list[str]:
    """Canli arac listesi — TEK GERCEK KAYNAK."""
    return [t.name for t in toolbox.araclar()]


def konu_metni(konu: str, veri_notu: str = "") -> str:
    """Bir rehber konusunun Telegram metni."""
    k = KONULAR[konu]
    L = [f"<b>{k['emoji']} {k['baslik'].upper()}</b>", "", k["giris"], ""]

    if k["araclar"]:
        L.append("<b>Neler yapabilirim</b>")
        L += [f"• {SADE[a]}" for a in k["araclar"]]
        L.append("")

    komutlar = KOMUTLAR if k["komutlar"] is None else {
        c: KOMUTLAR[c] for c in k["komutlar"]}
    if komutlar:
        L.append("<b>Kisayol</b>")
        # Ayni aciklamayi paylasan takma adlar (yardim/start/help) tek satir.
        gorulen: dict[str, list[str]] = {}
        for ad, acik in komutlar.items():
            gorulen.setdefault(acik, []).append(ad)
        L += [f"<code>{' /'.join('/' + a for a in adlar)}</code> — {acik}"
              for acik, adlar in gorulen.items()]
        L.append("")

    if k["dene"]:
        L.append("<b>Dene</b>")
        L += [f"<i>“{d}”</i>" for d in k["dene"]]
        L.append("")

    if k.get("not"):
        L.append(k["not"])
    if veri_notu:
        L += ["", veri_notu]
    return "\n".join(L).strip()


def menu_markup() -> dict:
    """Konu butonlari — ikili satirlar."""
    dugmeler = [{"text": f"{k['emoji']} {k['baslik']}",
                 "callback_data": f"reh:{ad}"} for ad, k in KONULAR.items()]
    satirlar = [dugmeler[i:i + 2] for i in range(0, len(dugmeler), 2)]
    return {"inline_keyboard": satirlar}


def menu_metni() -> str:
    return ("<b>📚 Neler yapabilirim?</b>\n\n"
            "Bir baslik sec, ya da <b>direkt sor</b> — komut ezberlemene "
            "gerek yok.\n"
            "<i>“ASELSAN'i nasil analiz edersin?”</i>")


def ozet(toolbox, konu: str | None = None) -> dict:
    """
    `neler_yapabilirim` aracinin verisi.

    Arac listesi CANLI toolbox'tan geliyor; boylece "yapabilirim" diye
    beyan edilen sey ile gercekten cagrilabilen sey AYNI kaynaktan.
    """
    canli = set(arac_adlani_guvenli(toolbox))
    if konu and konu in KONULAR:
        k = KONULAR[konu]
        return {
            "konu": k["baslik"],
            "yapabildiklerim": [SADE[a] for a in k["araclar"] if a in canli],
            "kisayollar": (list(KOMUTLAR) if k["komutlar"] is None
                           else k["komutlar"]),
            "ornek_istekler": k["dene"],
            "bilinmesi_gereken": k.get("not", "").replace("<b>", "").replace(
                "</b>", "").replace("<code>", "").replace("</code>", ""),
        }
    return {
        "konular": {ad: k["baslik"] for ad, k in KONULAR.items()},
        "yapabildiklerim": [SADE[a] for a in sorted(canli) if a in SADE],
        "not": ("Kullanici komut ezberlemek zorunda DEGIL. Bir konuyu "
                "anlatirken once SORUSUNU cevapla, rehberi ders haline "
                "getirme. Ayrinti icin bu araci `konu` ile tekrar cagir."),
    }


def arac_adlani_guvenli(toolbox) -> list[str]:
    try:
        return arac_adlari(toolbox)
    except Exception:                                 # noqa: BLE001
        return list(SADE)
