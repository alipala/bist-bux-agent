"""
NABIZ — proaktif dongunun orkestratoru.

    tarayici (deterministik)  ->  panel (4 ajan, paralel)  ->  hakem
         |                              |                        |
      signals                     predictions              Telegram

TASARIM KARARLARI
-----------------
* SESSIZLIK GECERLIDIR. Esik gecen sinyal yoksa panel hic calismaz ve
  bildirim gonderilmez. Her gun bir sey soylemek zorunda olan sistem
  gurultu uretir.
* PUANLAMA HER KOSUDA ONCE. Once vadesi dolmus tahminler olculur, sonra
  yenileri uretilir; boylece karne her zaman guncel ve ozet mesajinda
  "su ana kadarki isabetim su" diyebiliyoruz.
* LLM YALNIZCA ADAYLAR ICIN. Tarayici 55 enstrumani deterministik tarar,
  panel yalnizca en guclu birkacini yorumlar.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .arsiv import arsivle

log = logging.getLogger(__name__)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# PORTFOY RISKI, PIYASA SINYALI DEGILDIR. Ikisi ayni `sinyaller`
# listesinden geliyor ama bildirimde ayri bolumlere gider; tek yerde
# tanimli olmasi, birinde unutulup cift sayilmasini engelliyor.
RISK_TURLERI = ("yogunlasma", "acik_zarar")

# KULLANICIYA GIDEN AY ADLARI — TAM TURKCE. Depodaki ASCII kurali
# kaynak/yorum/commit icin; Ali'nin telefonda okudugu metin icin degil.
# Kural yanlis yere uygulaninca mesaj yari Turkce yari ASCII cikiyordu
# (sablonlar "olculdu", panel "ölçüldü") ve Ali 2026-09-02'de "text
# formati hic ama hic okunur degil" dedi.
_AY_KISA = ("Oca", "Şub", "Mar", "Nis", "May", "Haz",
            "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


def _tr(v, basamak: int = 2) -> str:
    """
    Turkce sayi: ondalik VIRGUL. '19.91' bir Turk okuyucuda 19 bin 910
    gibi okunabilir; sohbet katmani zaten '-%19,91' yaziyor ve
    bildirimin ondan farkli konusmasi icin sebep yok.
    """
    return f"{v:,.{basamak}f}".replace(",", "\x00").replace(".", ",") \
                              .replace("\x00", ".")


# YON ISARETI — TEK KAYNAK. Butun mesajlar buradan okur.
#
# RENK DOGRU OLMALI: eskiden 🔺 / 🔻 kullaniliyordu ve IKISI DE
# KIRMIZI (U+1F53A "red triangle pointed up", U+1F53B "…down"). Yani
# "MRVL 🔺+%5,79" bir KAZANCI kirmizi gosteriyordu — kullanici
# 2026-08-21'de bunu bildirdi ve haklıydi. Finansal okumada renk
# sekilden once algilanir; yanlis renk, dogru sayiyi yanlis okutur.
#
# NEDEN DAIRE, NEDEN OK DEGIL: Unicode'da YESIL OK YOK. Renk tasiyan
# tek grup renkli daire/kare; oklar (⬆️ ⬇️) tema rengine dusuyor ve
# yesil/kirmizi ayrimi kayboluyor. Bu yuzden RENGI daire, YONU sayinin
# +/- isareti tasiyor: "🟢 +%5,79" / "🔴 -%23,55".
#
# DURAGAN ⚪: yuvarlama sonrasi sifira duseni "yukseldi" gibi
# gostermek olmayan bir hareket iddia etmekti.
YON_ISARETI = {"yukari": "🟢", "asagi": "🔴", "notr": "⚪"}


def yon_isareti(v) -> str:
    """Sayinin yonune gore renk isareti. `None` -> duragan."""
    try:
        s = float(v)
    except (TypeError, ValueError):
        return YON_ISARETI["notr"]
    if s > 0:
        return YON_ISARETI["yukari"]
    return YON_ISARETI["asagi"] if s < 0 else YON_ISARETI["notr"]


def _yuzde_tr(v, basamak: int = 2, ok: bool = False) -> str:
    """
    '-%19,91' — isaret ONDE, yuzde isareti sayidan ONCE (TR yazimi).

    `ok=True` ise basina RENK ISARETI konur: 🟢 / 🔴 / ⚪.

    NEDEN ISARET: eksi isareti tek karakter ve uzun bir satirin
    ortasinda KACIYOR — kullanici "%1,89 ne, asagi mi yukari mi" diye
    sordu (2026-08-20). Isaret bir SEMBOL, sifat DEGIL: makro satirinin
    "yorum yazma" disiplinini bozmaz, cunku hicbir sey yorumlamiyor.

    SIFIR AYRI ISARET ALIR: 🟢%0,00 "yukseldi" gibi okunurdu.
    """
    isaret = "-" if v < 0 else "+"
    metin = f"{isaret}%{_tr(abs(v), basamak)}"
    if not ok:
        return metin
    # Yuvarlama SONRASI sifira duseni notr say: '+%0,00' yaninda yukari
    # isareti, olmayan bir hareket iddia ederdi. Isaret de dusuyor —
    # '-%0,00' okunaksiz ve tasidigi bilgi zaten notr isarette.
    yuvarlanmis = round(float(v), basamak)
    if yuvarlanmis == 0:
        return f"{YON_ISARETI['notr']} %{_tr(0, basamak)}"
    return f"{yon_isareti(yuvarlanmis)} {metin}"


def _fiyat_tr(v) -> str:
    """
    Fiyati TURKCE yazar, HASSASIYET KAYBETMEDEN.

    Sabit basamak sayisi kullanilamaz: ROSE 0,0055 USD ile ASML
    1.512 EUR ayni kalibi paylasamaz. Basamak sayisi DEGERIN KENDISINDEN
    turuyor.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    ondalik = 0 if f == int(f) else min(len(f"{f!r}".split(".")[-1]), 8)
    return _tr(f, ondalik)


def _kosul_okunabilir(kosul):
    """
    Ham gramer KULLANICIYA GITMEZ.

    OLCULEN KUSUR (2026-08-21): gun ici mesajinda duzeltilmisti ama
    GUNLUK OZET atlanmisti — kullanici "Kosul close > 10.50 · su anki
    close: 10.5800" gorup "bu ne demek" diye sordu. Ayni satirda IKI
    ayri ondalik yazimi da vardi (10.50 / 10.5800).
    """
    from .tez import okunabilir
    return okunabilir(kosul)


def _alan_adi(alan):
    from .tez import ALAN_ADI
    return ALAN_ADI.get(alan, alan)


def _tarih_kisa(ts) -> str | None:
    """
    ISO tarihten '19 Agu'. Ayristirilamiyorsa None — YANLIS TARIH
    YAZMAKTANSA hic yazma. Bildirimde tarih olmamasinin bedeli 20
    gunluk bir olayin 'bugun' sanilmasiydi; yanlis tarihin bedeli
    daha buyuk olur.
    """
    from datetime import date
    try:
        g = date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None
    return f"{g.day} {_AY_KISA[g.month - 1]}"


def _gun_farki_bugune(ts) -> int | None:
    """
    Bir zaman damgasinin BUGUNE gore yasi (gun). Ayristirilamazsa None.

    UYDURMA SAYI YOK: bilinmeyen tarih 0 gun sayilirsa bayat veri TAZE
    gorunur — `screener._gun_farki` ile ayni disiplin.
    """
    from datetime import date
    try:
        g = date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None
    return (date.today() - g).days


def _kisa(v) -> str:
    """Kripto kurus altinda; sabit 2 hane seriyi duzlestirir."""
    if v is None:
        return "-"
    a = abs(float(v))
    nd = 2 if a >= 100 else 4 if a >= 1 else 6 if a >= 0.01 else 8
    return f"{float(v):.{nd}f}"

# Panele gidecek en guclu sinyal sayisi. Fazlasi hem pahali hem
# odaksiz — 12 gozlem zaten 25 satirlik bir ozete zor sigiyor.
PANEL_ADAY = 12

# PORTFOYE AYRILAN ASGARI SLOT.
#
# Olculdu 2026-08-16: esigi gecen 100 gozlemin 84'u BIST, ve saf "en guclu
# 12" secimi panele 10 BIST + 2 BUX gonderiyordu. Oysa portfoy BUX ve
# BINANCE; BIST'te tek pozisyon yok ve Midas hesabinda bakiye de yok, yani
# panelin kapasitesinin %83'u ISLEM YAPILAMAYAN kagitlara gidiyordu.
#
# Sebep BIST'in daha ilginc olmasi degil, daha KALABALIK olmasi: 251 BIST
# sembolune karsi 19 BUX + 67 kripto. Guc siralamasi evren buyuklugunu
# olculmemis bir agirlik gibi iceri sokuyor.
#
# Cozum: sahip olunan enstrumanlarin sinyalleri once yerlestirilir, kalan
# slotlar guce gore doldurulur. Gerekce, tarayicinin portfoy risklerini
# ayri uretmesiyle ayni: mevcut sermayeye yonelik bir gozlem, esit
# guclu ama sahip olunmayan bir gozlemden daha degerlidir — uzerine
# islem yapmak yeni sermaye gerektirmez ve mevcut riski dogrudan ilgilendirir.
PORTFOY_ASGARI_SLOT = 5

# Bu gucun altindaki sinyal tek basina bildirime deger degil.
BILDIRIM_ESIGI = 0.55

# SURE BUTCESI — son sahibin ORTASINDA kesilmektense panelini ATLA.
#
# Olculdu 2026-08-18: panel sahip basina ~282 sn (yuksel 20:46:05 ->
# 20:48:11), iki sahiple ~9,4 dk. Sahip sayisi artarsa ya da bir panel
# takilirsa kabugun duvar saati sureci ORTADAN keser ve o sahip ne
# cikti ne aciklama alir.
#
# Bu esik asildiginda kalan sahiplerin paneli atlanir ve kendilerine
# SOYLENIR. Sessiz atlama YOK: "bugun mesaj gelmedi" ile "bugun panel
# kosamadi" ayri seyler ve ikincisi kullanicinin bilmesi gerekendir.
#
# GERCEK DEGER KIP BASINA AYARDAN gelir
# (`ritim.kipler.<kip>.panel_butce_sn`). Buradaki sabit yalnizca
# TAVANDIR: ayar bundan buyuk bir deger verse bile kabugun duvar saati
# devreye girer, o yuzden ustune cikmanin anlami yok. Env degiskeni
# testler ve elle kosular icin duruyor.
PANEL_SURE_BUTCESI_SN = float(__import__("os").getenv(
    "NABIZ_PANEL_BUTCE_SN", "1800"))

# KABUGUN OLDURME ANI — `run_kosu.sh` epoch saniye olarak gecirir.
#
# NEDEN VAR: yukaridaki butceler kosunun KENDI icindeki paylasimi
# duzenliyor ama kabugun duvar saatiyle hicbir bagi yok. Toplama uzarsa
# (2026-08-21: tuik tek basina 201 sn) panel butcesi degismedigi icin
# TOPLAM sure kabugun sinirini asabilir — ve kabuk sureci oldurdugunde
# kosu izini, tahminleri ve mesaji birlikte kaybediyoruz.
#
# Bu degisken varsa panel butcesi "kabuk beni ne zaman olduruyor"
# bilgisine gore KISILIR. Yoksa (elle kosu, test) yalnizca kip butcesi
# gecerlidir.
KOSU_BITIS_ENV = "KOSU_BITIS_TS"

# Kosunun SONUNDAKI islere ayrilan pay: defter yazimi, ozet mesaji ve
# kosu izi. Panel bu payin icine giremez.
#
# 120 sn olculerek secildi: 2026-08-21 kosusunda panel sonrasi adimlar
# (iki `defter.kaydet`, `_ozet_bildir`, `_iz_birak`) toplam 3 sn surdu;
# pay 40 kat. Cimri bir pay, tam da korumaya calistigi seyi — sonucun
# yazilmasini — riske atardi.
TESLIMAT_PAYI_SN = 120.0

# Bir panelin anlamli calisabilmesi icin gereken en az sure. Altinda
# panel ATLANIR ve sebebi SOYLENIR: 30 saniyede baslayip kesilen bir
# panel, hem butceyi harcar hem hicbir sey uretmez.
ASGARI_PANEL_SN = 120.0

# Kirilim tablosunda gosterilecek en fazla satir. Kirpma OLABILIR ama
# KIRPILDIGI YAZILIR: 2026-08-23'te kullanici 8 satir gordu ve gercekte
# 12 vardi — mesaj "hepsi bu" gibi okundu. 25 secildi: Telegram'in 4096
# karakterlik sinirinin altinda kalir ve tabloyu telefonda okunur tutar.
STRATEJI_TABLO_SATIR = 25


def _kirp(metin, n: int) -> str:
    """
    Duzyaziyi n karakterde keser — ve KESILDIGINI SOYLER.

    AD `_kisa` DEGIL — o ISIM ZATEN ALINMIS ve BASKA BIR SEY YAPIYOR
    (kripto icin sabit haneli SAYI bicimlendirici, ~170. satir). Ilk
    yazimda `_kisa` konuldu ve tanim oncekini SESSIZCE GOLGELEDI;
    testte `_kisa() missing 1 required positional argument` ile
    patladi. Iki farkli isi ayni ada koymak, bu deponun tekrar eden
    kusur sinifinin bir baska yuzu.

    OLCULEN KUSUR (2026-08-28 kirilim tablosu). LLM gerekcesi 120
    karakterde kesiliyordu ve kesildigi SOYLENMIYORDU; kullaniciya
    giden satir kelime ortasinda bitti:

        "... Risk/k"

    Kirpmak makul (Telegram 4096 karakter), KIRPILDIGINI GIZLEMEK degil
    — bu deponun tekrar eden kusur sinifi ve ayni gun `prices` ile
    `bistgecmis`te kapatilmisti. Ucuncu kopya burada duruyordu.

    Not: sozcuk sinirina hizalamiyoruz. Basit kesme + acik isaret,
    "akilli" bir kesmeden daha dogru davranir — akilli kesme uzun bir
    sozcukte sessizce cok fazla atabilir.
    """
    s = str(metin)
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _risk_satiri(r: dict) -> str:
    """
    Portfoy riski satiri — HESAP ADIYLA.

    OLCULEN KUSUR (2026-08-28). Ali sabah "CASH yogunlasma · agirlik
    %95.8", aksam "%63.1" gordu ve nakit oraninin bir gunde 33 puan
    dustugunu sandi. Dusmemisti: ikisi FARKLI HESAPTI.

        ibkr %95,8 · midas %63,1 · bux %5,9 · binance %0

    `screener._portfoy_riskleri` hesap basina hesapliyor ve `kanit`
    icinde `hesap` alanini ZATEN tasiyordu — bu satir onu DUSURUYORDU.
    Ustelik risk bildirimi degismeyenleri bastirdigi icin her kosuda
    BASKA bir hesabin satiri gorunuyor; hesap adi olmadan iki mesaj
    ayni olcunun iki degeri gibi okunuyor.

    TEK KOPYA: ayni sekiz satir `_hafif_mesaj` ve `_gunluk_mesaj`da
    IKI KEZ yaziliydi ve bu duzeltme yalnizca birine uygulansaydi
    kopyalar ayrisirdi — bu deponun tekrar eden kusur sinifi.
    """
    k = r.get("kanit") or {}
    hesap = str(k.get("hesap") or "").strip()
    return (f"⚠️ <b>{_esc(r['sembol'])}</b> {r['tur']}"
            + (f" · <b>{_esc(hesap.upper())}</b>" if hesap else "")
            + (f" · agirlik %{k.get('agirlik_%')}" if k.get("agirlik_%")
               else "")
            + (f" · K/Z %{k.get('kz_%')}" if k.get("kz_%") else ""))


def _tablo_fiyat(v) -> str:
    """
    Sabit genisliklı tablo sutunu icin fiyat — SABIT 2 HANE.

    `_fiyat_tr` BILEREK degisken hane kullaniyor ("ROSE 0,0055 USD ile
    ASML 1.512 EUR ayni kalibi paylasamaz") ve DUZYAZIDA dogru olan o.
    Tabloda ise hizalama BILGI TASIYOR: 189,63 ile 174,117 alt alta
    gelince goz basamaklari karsilastiramaz. Ikinci bir sayi yazimi
    ACILMIYOR — ayni `_tr`, yalnizca hane sayisi acikca veriliyor.

    1'in altindaki degerde `_fiyat_tr`ye DUSULUYOR: 0,0055'i "0,01"
    diye yazmak fiyati YANLIS gostermek olurdu. Strateji evreninde
    (S&P 500 + Nasdaq 100) boyle bir fiyat yok ama kural evrene degil
    SAYIYA bakmali.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return _tr(f, 2) if abs(f) >= 1 else _fiyat_tr(f)


def _devir_kisa(v) -> str:
    """
    206.000.000 -> '206M'. Rakamlar `_tr`den geciyor: mesajin geri kalani
    "6.959,05" derken bu sutunun "206.0" demesi, ayni mesajda IKI ayri
    sayi yazimi olurdu (`boyutlama.satir` ile ayni gerekce).
    """
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    for bolen, ek in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(f) >= bolen:
            return f"{_tr(f / bolen, 0)}{ek}"
    return _tr(f, 0)


def strateji_mesaji(sonuc: dict, secilen: list[dict], ayar: dict) -> str:
    """
    Gunluk kirilim tablosu — SAF: db yok, ag yok, saat yok.

    Saf oldugu icin test onu Telegram'a HIC cikmadan sinayabiliyor
    (`[[test-canli-kanala-yazdi]]`: testler canli kanala yazmayacak).

    UC KURAL, ucu de olculmus bir kusurdan geliyor:

    1. KIRPMA VARSA SOYLENIR. Tablo uzunsa kirpilir ama kac satirin
       kirpildigi YAZILIR (`prices.py:149-158` ayni disiplin).
    2. TARANAMAYANLAR SEBEBIYLE yazilir. "0 kirilim" ile "bakilamadi"
       ayni cumleye toplanamaz.
    3. SIFIR KIRILIMDA DA MESAJ GIDER. Sessizlik ile "bakilmadi" ayirt
       edilemez olurdu; kural konusmadigi gun SUSAR ve bunu soyler.

    EMIR BUTONU YOK (Adim 6'da aciliyor): `/emir` komut satiri METIN
    olarak veriliyor, kullanici kopyaliyor.
    """
    # Etiket TEK KAYNAKTAN (`strateji.POZISYON_SEBEBI`): burada dize
    # tekrarlamak, etiket degistiginde aciklamayi sessizce YANLIS sayaca
    # baglardi. Ice aktarma fonksiyon icinde — modul yuklenirken
    # `strateji`yi cekmemek icin (cagri yerlerinin geri kalani da oyle).
    from .strateji import AYNI_BAR_SEBEBI, POZISYON_SEBEBI

    gorusler = sonuc.get("gorusler") or []
    sayaclar = dict(sonuc.get("sayaclar") or {})
    taranan = int(sonuc.get("taranan") or 0)
    # "Kirilim yok" TARANAMAYAN DEGIL: bakildi ve bir sey yoktu.
    kirilimsiz = sayaclar.pop("kirilim yok", 0)
    # "Ayni bar" da taranamayan degil: kirilim var, DUN yazildi.
    ayni_bar = sayaclar.pop(AYNI_BAR_SEBEBI, 0)
    taranamayan = sum(sayaclar.values())

    tarih = None
    for g in gorusler:
        tarih = _tarih_kisa((g.get("seviyeler") or {}).get("bar_ts"))
        if tarih:
            break

    L = [f"📊 <b>STRATEJI — {_esc(tarih)} kirilimlari</b>" if tarih
         else "📊 <b>STRATEJI — gunluk kirilimlar</b>"]
    ozet = (f"{taranan} sembol tarandi · {len(gorusler)} kirilim")
    if gorusler:
        ozet += (f" · {len(secilen)} secildi "
                 f"(tohum {ayar.get('secim_tohumu')})")
    L.append(ozet)

    if ayni_bar:
        # YENI BAR GELMEDI — bunu soylemek, bos tabloyu aciklamaktan
        # fazlasi: `strateji_fiyat` bu kosuda calismadiysa (3 Eylul:
        # toplama sureci coktu) sebebi buradan gorunur.
        L.append(f"\n<i>{ayni_bar} kirilim {_esc(tarih) if tarih else 'dunku'} "
                 "bariyla AYNI, dun deftere yazildi — yeniden yazilmadi ve "
                 "secime girmedi. Yeni bar gelmediyse toplama bu kosuda "
                 "<code>strateji_fiyat</code>'i calistirmamis olabilir.</i>")
    if not gorusler and not ayni_bar:
        # KURAL KONUSMADIGI GUN SUSAR — ve sustugunu soyler.
        L.append("\n<i>Kural bugun konusmadi: hicbir sembol 20 gunluk "
                 "yuksegini asmadi. Sifir kirilimli gun ariza degildir.</i>")
    elif gorusler:
        gosterilen = gorusler[:STRATEJI_TABLO_SATIR]
        satirlar = [f"{'SEMBOL':<7}{'KAPANIS':>10}{'20G YUK':>10}"
                    f"{'STOP(2N)':>10}{'10G DIP':>10}{'DEVIR':>8}"]
        for g in gosterilen:
            sv = g.get("seviyeler") or {}
            satirlar.append(
                f"{str(g.get('sembol') or '?'):<7}"
                f"{_tablo_fiyat(g.get('giris')):>10}"
                f"{_tablo_fiyat(sv.get('donchian_giris')):>10}"
                f"{_tablo_fiyat(g.get('stop')):>10}"
                f"{_tablo_fiyat(sv.get('donchian_cikis')):>10}"
                f"{_devir_kisa(sv.get('devir')):>8}")
        L.append("\n<pre>" + _esc("\n".join(satirlar)) + "</pre>")
        kirpilan = len(gorusler) - len(gosterilen)
        if kirpilan:
            L.append(f"<i>Tabloda {len(gosterilen)} satir gosterildi, "
                     f"{kirpilan} satir kirpildi (toplam {len(gorusler)}).</i>")

    if secilen:
        L.append("\n▸ <b>Secilenler</b>")
        for g in secilen:
            L.append(f"\n<b>{_esc(g.get('sembol'))}</b> · giris "
                     f"{_fiyat_tr(g.get('giris'))} · stop "
                     f"{_fiyat_tr(g.get('stop'))}")
            olcu = _boyut_satiri(g, ayar)
            if olcu:
                L.append(olcu)
            L.append(_emir_satiri(g))

    if taranamayan:
        detay = ", ".join(f"{_esc(k)}: {v}" for k, v in
                          sorted(sayaclar.items(), key=lambda x: -x[1]))
        L.append(f"\n<i>Taranamayan: {taranamayan} sembol ({detay})</i>")
        # SAYI DOGRUYDU, OKUYUS YANLISTI. 28 Agustos tablosunda "zaten
        # pozisyonda: 167" satiri vardi ve Ali'nin GERCEK IBKR hesabinda
        # iki satir vardi (CASH + KO 0,05 adet). Ifade "senin
        # portfoyunde 167 kagit var" gibi okunuyor; oysa bu KURALIN
        # gecmisten simule ettigi defter. Etiket degistirildi
        # ("kural zaten tutuyor") ve ayrim ACIKCA yaziliyor — bir sayiyi
        # duzeltmek yetmez, okuyanin modelini duzeltmek gerekir.
        if sayaclar.get(POZISYON_SEBEBI):
            L.append(f"<i>«{POZISYON_SEBEBI}» = kuralin SIMULE ETTIGI "
                     "defter, senin portfoyun degil. Cikis sinyali "
                     "yalnizca GERCEK pozisyonlar icin uretilir.</i>")
    if kirilimsiz and gorusler:
        L.append(f"<i>Kirilimi olmayan: {kirilimsiz} sembol.</i>")

    # CIKIS GIRISTEN ONCE OKUNMALI — o yuzden EN USTE degil ama
    # LLM/karne blogundan ONCE. Elde tutulan bir kagidin satis sinyali,
    # yeni bir alim onerisinden her zaman daha acildir.
    cikis = _cikis_satirlari(sonuc.get("cikis"))
    if cikis:
        L.append("\n" + cikis)

    llm = _llm_satirlari(sonuc.get("llm"), gorusler)
    if llm:
        L.append("\n" + llm)

    karne = _karne_satirlari(sonuc.get("fren"))
    if karne:
        L.append("\n" + karne)
    return "\n".join(L)


def _cikis_satirlari(cikis: list[dict] | None) -> str | None:
    """
    Kuralin CIKIS dedigi POZISYONLAR.

    NEDEN VAR (2026-08-29). Motor yalnizca `AL` uretiyordu: 2N stop
    tahmin defterinin kosuluyla tez alarmina dusuyordu ama Donchian'in
    asil cikisi — 10 gunluk dip — giris gunundeki tabloda BIR KEZ
    gosterilip unutuluyordu. Trend takibinde kenar buyuk olcude
    cikistadir; sinyal verilmezse pozisyon suresiz kalir.

    SESSIZ KALINIR (None): cikis yoksa satir yazilmaz. Bu blok bir
    DURUM raporu degil, bir EYLEM cagrisi.

    STOP BILINMIYORSA SOYLENIR. Pozisyon kuraldan girilmemisse onu
    koruyan 2N stop'u BILMIYORUZ ve uydurmak, olmayan bir korumayi
    varmis gibi gostermek olurdu.
    """
    if not cikis:
        return None
    L = [f"🔻 <b>CIKIS — {len(cikis)} pozisyon</b>"]
    for c in cikis:
        sembol = _esc(str(c.get("sembol")))
        sebep = _esc(str(c.get("sebep")))
        satir = f"  <b>{sembol}</b> — {sebep}"
        kapanis, dip = c.get("kapanis"), c.get("dip")
        if c.get("sebep") == "10 gun dip" and kapanis is not None \
                and dip is not None:
            satir += f" · kapanis {kapanis:g} &lt; 10G dip {dip:g}"
        elif c.get("stop") is not None and kapanis is not None:
            satir += f" · kapanis {kapanis:g} · stop {float(c['stop']):g}"
        L.append(satir)
        adet = c.get("adet")
        if adet:
            # TAM CIKIS: Turtle System 1'de pozisyon kademeli
            # kapatilmaz. Kismi adet yazmak, test edilmemis IKINCI bir
            # kural eklemek olurdu.
            L.append(f"<code>/emir {sembol} SAT {adet:g}</code>")
        if c.get("stop_bilinmiyor"):
            L.append("  <i>2N stop bilinmiyor: bu pozisyon kuraldan "
                     "girilmemis, yalnizca 10 gun dip kurali uygulandi.</i>")
    L.append("<i>Cikis FRENDEN ETKILENMEZ: fren giris tavanini dusurur, "
             "korumayi kismaz.</i>")
    return "\n".join(L)


def _llm_satirlari(llm: dict | None, gorusler: list[dict]) -> str | None:
    """
    Model yorumu — KURALIN YANINDA, onun yerine DEGIL (belge §7).

    Mesaj ikisini YAN YANA gosteriyor cunku okuyanin gormesi gereken
    sey "model ne dedi" degil, "model kuraldan NEREDE ayrildi".
    Ayrildiklari satirlar isaretleniyor; ayni seyi soyledikleri yerde
    gosterilecek bir bilgi yok.

    HATA GIZLENMIYOR: model cagrilamadiysa sebep yaziliyor. "Model
    yorum vermedi" ile "model cagrilamadi" ayri seyler ve ikincisi bir
    arizadir.
    """
    if not llm:
        return None
    if llm.get("hata"):
        return ("🤖 <i>LLM yorumu alinamadi: "
                f"{_esc(llm['hata'])}. Kural karari etkilenmedi.</i>")
    y = llm.get("gorusler") or []
    if not y:
        return "🤖 <i>LLM hicbir sembol icin gorus vermedi.</i>"

    al = [g for g in y if g.get("tur") == "alim"]
    bekle = [g for g in y if g.get("tur") == "bekle"]
    L = [f"🤖 <b>LLM yorumu</b> — {len(al)} onay, {len(bekle)} bekle "
         f"({len(gorusler)} kirilimda)"]
    # AYRILDIKLARI YER ONEMLI: kural hepsine "alim" diyor, model
    # "bekle" dediginde ayrisma vardir ve olculecek olan odur.
    for g in bekle[:8]:
        L.append(f"  ⏸ <b>{_esc(g.get('sembol'))}</b> — "
                 f"{_esc(_kirp(g.get('gerekce'), 120))}")
    if len(bekle) > 8:
        L.append(f"  <i>(+{len(bekle) - 8} bekle daha)</i>")
    L.append("<i>Model sinyali BASTIRMIYOR: karari ayri satir olarak "
             "deftere yaziliyor (<code>strateji_llm</code>) ve karnesi "
             "ayri olculuyor. Emir KURALIN dedigine gore kuruluyor.</i>")
    return "\n".join(L)


def _karne_satirlari(fren: dict | None) -> str | None:
    """
    Karne + KONTROL GRUBU + fren durumu. Kontrolsuz karne YAYINLANMAZ.

    `42ab2fd`in dersi: BIST'te olculen kenarin YARISI piyasa
    suruklemesiydi. Rastgele kontrol satiri olmadan bu blok
    gosterilmez — "isabet %38" tek basina kullaniciyi yanlis
    yonlendirir, cunku ayni donemde rastgele girmek de %38 verebilir.

    IKI TABAN AYRI YAZILIYOR: defterin isabeti PIYASAYA GORE
    duzeltilmis, rastgele kontrol HAM getiri. Hangi sayinin hangi
    tabandan geldigi yazilmazsa okuyan taraf ikisini toplar.
    """
    if not fren:
        return None
    k = fren.get("karne") or {}
    st, sec, rnd = k.get("strateji") or {}, k.get("secilen") or {}, \
        k.get("rastgele") or {}

    if fren.get("olculmemis"):
        # OLCULMEMIS, IYIMSER VARSAYILMAZ. "Henuz olculmedi" demek,
        # olculmus gibi davranmaktan durusttur.
        # "OLCULMEMIS" ILE "YAZILMAMIS" AYNI SEY DEGIL — SOYLENIYOR.
        #
        # OLCULEN KUSUR (2026-08-29): kullanici "kural para kazandiriyor
        # mu" diye sordu; model karnenin 0 olcumune bakip "motorun
        # sinyalleri deftere YAZILMIYOR" dedi ve zaten yapilan bir isi
        # oneri diye sundu. Gercekte 14 satir yazilmisti, yalnizca
        # `ufuk_gun` dolmamisti. Sayinin YOKLUGU olcumun yoklugunu
        # anlatir, VERININ yoklugunu degil.
        k = fren.get("karne") or {}
        yaz = (k.get("strateji") or {}).get("yazilan") or 0
        bekleyen = (k.get("strateji") or {}).get("olcum_bekleyen") or 0
        satir = ["📋 <b>Karne: OLCULMEMIS</b>",
                 f"<i>{_esc(fren.get('gerekce'))}</i>"]
        if yaz:
            satir.append(
                f"<i>Sinyaller deftere YAZILIYOR: {yaz} kayit"
                + (f", {bekleyen}'i olgunlasmayi bekliyor" if bekleyen else "")
                + ". «Olculmedi» demek «yazilmadi» demek DEGIL.</i>")
        return "\n".join(satir)

    L = [f"📋 <b>Karne</b> (son {k.get('pencere_gun')} gun)"]
    L.append(f"  strateji        %{_tr(st.get('isabet_%'))} "
             f"({st.get('olcum')} olcum)")
    if sec.get("olcum"):
        L.append(f"  strateji_secilen %{_tr(sec.get('isabet_%'))} "
                 f"({sec.get('olcum')} olcum)")
    else:
        L.append("  strateji_secilen —  (henuz olcum yok)")
    L.append(f"  rastgele giris  %{_tr(rnd.get('isabet_%'))} "
             f"({rnd.get('sembol')} sembol)")
    fark = k.get("fark_%")
    L.append(f"  <b>fark          %{_tr(fark)}</b>"
             if fark is not None else "  fark          —")
    L.append("<i>Isabet iki TABANDA olculuyor: karnenin ilk iki satiri "
             "PIYASAYA GORE duzeltilmis, rastgele kontrol HAM getiri. "
             f"Fark ham tabandan (strateji ham %{_tr(st.get('ham_isabet_%'))}).</i>")
    if fren.get("fren"):
        L.append(f"⛔ <b>FREN ACIK</b> — gunluk tavan {fren.get('tavan')}. "
                 f"<i>{_esc(fren.get('gerekce'))}</i>")
    return "\n".join(L)


def _boyut_satiri(gorus: dict, ayar: dict) -> str | None:
    """
    Boyut TEK MOTORDAN: `pulse.boyutlama.boyut()`. `0.10/sigma` gibi
    IKINCI bir boyutlama formulu YAZILMAYACAK — mevcut formul gercek
    2N stop mesafesine dayaniyor ve daha dogru.
    """
    from .boyutlama import satir
    return satir(gorus.get("giris"), gorus.get("stop"),
                 (gorus.get("seviyeler") or {}).get("para_birimi"),
                 float(ayar.get("risk_payi_pct") or 1.0))


def _emir_satiri(gorus: dict) -> str:
    """
    Kopyalanabilir `/emir` komutu — SOZDIZIMI KODDAN DOGRULANDI.

    `emirakis.komut_coz` sunu bekliyor: `SEMBOL AL|SAT ADET [FIYAT]`.
    Plan belgesi ornekte `... AL <adet> LMT 221.07` yaziyordu ve o komut
    CALISMAZDI: `komut_coz` dorduncu parcayi FIYAT sanip `float("LMT")`
    deneyip "Fiyat 'LMT' sayi degil" derdi. Fiyat verilince tur zaten
    LMT oluyor. Yanlis bir komut satiri, `[[yanlis-ipucu]]` dersinin
    ta kendisi: kullaniciyi dogru araca degil YANLIS KAPIYA yollar.

    ADET HESAPLANABILIYORSA YAZILIR, YOKSA `<adet>` YER TUTUCUSU KALIR
    VE SEBEBI SOYLENIR. Hesap `Nabiz._adet_hesapla`da: hesabin net
    likidite degeri x `boyutlama.boyut()` x kur. Bu fonksiyon SAF ve
    IBKR'ye erisemez; uydurma bir adet yazmak, hesaplanmis gibi gorunen
    bir sayi vermek olurdu.
    """
    sembol = str(gorus.get("sembol") or "?")
    fiyat = gorus.get("giris")
    try:
        # Komut satirinda NOKTA: `/emir` ayristirici virgulu de kabul
        # ediyor ama Turkce bicimli sayi (1.234,56) binlik ayiracla
        # bozulurdu. Burada MAKINE okuyacak, insan degil.
        fiyat_metni = f"{float(fiyat):g}"
    except (TypeError, ValueError):
        return "<i>Fiyat okunamadi — /emir satiri uretilmedi.</i>"
    if not gorus.get("conid"):
        # CONID YOKSA KOMUT CALISMAZ (`emirakis._conid` reddediyor).
        # Calismayacak bir komutu vermek, kullaniciyi hataya yollamak.
        return (f"<i>{_esc(sembol)} icin conid yok — emir gonderilemez. "
                "<code>run.py collect --site ibkrkimlik</code></i>")

    adet = gorus.get("adet")
    if adet:
        satir = (f"<code>/emir {_esc(sembol)} AL {adet:g} "
                 f"{fiyat_metni}</code>")
        if gorus.get("adet_tutar"):
            satir += (f"\n<i>Adet hesaptan: {gorus['adet_tutar']:g} "
                      f"{_esc(gorus.get('adet_pb') or '')} · "
                      "boyutlama.boyut()'tan</i>")
        return satir
    sebep = gorus.get("adet_sebep")
    return (f"<code>/emir {_esc(sembol)} AL &lt;adet&gt; {fiyat_metni}</code>"
            + (f"\n<i>Adet hesaplanamadi: {_esc(sebep)}</i>" if sebep else ""))


def strateji_butonlari(secilen: list[dict]) -> dict | None:
    """
    Secilen sinyaller icin TEK DOKUNUSLUK emir hazirligi butonu.

    BUTON EMIR GONDERMIYOR — `emirakis.hazirla`yi cagiriyor, o da onay
    dosyasi birakiyor. Akis degismiyor: hazirla -> onkontrol -> [ONAYLA]
    -> onkontrol YENIDEN -> gonder. Onay mimarisi bu deponun tek gercek
    guvenligi ve buton onun DISINDAN gecmiyor.

    ADETSIZ SINYALE BUTON KONMUYOR: basilamayan bir butonu gostermek,
    engeli tavsiye gibi okutur (`emirakis.hazirla` docstring'i ayni
    kurali koyuyor).

    `callback_data` 64 BAYT ve icine sembol+fiyat SIGIYOR; `pending/`
    deposuna yazilmiyor. Gerekce `vid` butonuyla ayni: saklanacak durum
    yok, kimlik butonun kendisinde ve bot yeniden baslasa bile buton
    calismaya devam eder.
    """
    tuslar = []
    for g in secilen or []:
        adet, sembol = g.get("adet"), g.get("sembol")
        if not (adet and sembol and g.get("conid") and g.get("giris")):
            continue
        veri = f"stremir:{sembol}:{adet:g}:{float(g['giris']):g}"
        if len(veri.encode()) > 64:          # Telegram siniri
            continue
        tuslar.append([{"text": f"📝 {sembol} emri hazirla",
                        "callback_data": veri}])
    return {"inline_keyboard": tuslar} if tuslar else None


class Nabiz:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def calistir(self, bildir: bool = True, panel: bool = True,
                 kip: str = "nabiz", sahip: str | None = None) -> dict:
        """
        ORTAK FAZ bir kez, KISISEL FAZ her sahip icin SIRAYLA.

        Piyasa verisi kisiden bagimsiz: puanlama ve piyasa taramasi tek
        kez kosar, sinyaller 'ortak' yazilir. Kisisel olan yalnizca
        portfoy riski, tez kontrolu, panel ve bildirim.

        SIRAYLA, PARALEL DEGIL: iki es zamanli SDK oturumu abonelik hiz
        limitine takilir ve birbirini bozar. Sira DETERMINISTIK
        (yapilandirmadaki yazim sirasi) — rastgele sira, limit
        doldugunda hep ayni kisinin magdur olup olmadigini gizler.

        `sahip` verilirse YALNIZCA o kisi kosar (elle calistirma ve
        test icin). Verilmezse KIPIN ALICILARI — tum sahipler DEGIL.

        ALICI LISTESI KIP BASINA (ritim v2 §3.3). Alici olmayan sahip
        icin HICBIR SEY kosmaz: ne tez kontrolu, ne portfoy riski, ne
        defter yazimi. O kisinin kontrolu kendi kipinde yapilir.
        Bilinmeyen kip BURADA duser — `ritim_kip` varsayilana DUSMEZ.
        """
        ayar = self.s.ritim_kip(kip)
        sahipler = [sahip] if sahip else list(ayar["alicilar"])
        if not sahipler:
            # SESSIZ NO-OP DEGIL. Sahipsiz kosu hicbir sey uretmez ama
            # "calisti" gorunur; bu, bildirimlerin neden gelmedigini
            # gunlerce gizleyebilir. (`ritim_kip` bos aliciyi zaten
            # reddediyor; buraya ancak `sahip=""` ile gelinir.)
            raise ValueError(
                f"nabiz: {kip!r} kipinin alicisi yok. "
                "config/settings.yaml -> ritim.kipler")
        try:
            ortak = self._ortak_faz(kip)
        except Exception as e:                        # noqa: BLE001
            # ORTAK FAZ PATLARSA kisisel faz anlamsiz — piyasa taramasi
            # olmadan gundem uretilemez. HERKESE bildirilir, sessizce
            # yutulmaz.
            log.exception("[%s] ORTAK FAZ patladi", kip)
            if bildir:
                self._herkese_bildir(
                    f"🔴 <b>{kip} ortak fazi patladi</b>\n\n"
                    f"<i>{_esc(type(e).__name__)}: {_esc(_kirp(e, 300))}</i>\n\n"
                    "Piyasa taramasi olmadan kisisel analiz uretilemedi; "
                    "bu kosuda kimse icin panel calismadi.")
            raise

        # STRATEJI TABLOSU PANELDEN ONCE GIDER.
        #
        # Deterministik ve ucuz (518 sembol 4,1 sn); panel ise LLM'e
        # bagli, pahali ve butce doldugunda ATLANABILIYOR. Sonra
        # gonderilseydi, panel butcesi dolan bir kosuda kirilim tablosu
        # da kaybolurdu — oysa o tablonun modelle hicbir ilgisi yok.
        strateji = (ortak or {}).get("strateji")
        if bildir and strateji:
            # YALNIZCA HESAP SAHIBINE — KIPIN TUM ALICILARINA DEGIL.
            #
            # SAHADA GORULDU (2026-08-27 22:59, ilk gercek kosu): tablo
            # `ritim.kipler.nabiz.alicilar`in HEPSINE gitti, yani
            # yuksel'e de. Icinde `/emir PAYX AL <adet> 126.48` gibi
            # KOPYALANABILIR komutlar var ve `/emir` TEK IBKR hesabini
            # kullaniyor (`emirakis._hesap`) — kim yazarsa yazsin emir
            # ALI'NIN hesabina gider. Yani baska birine, baskasinin
            # hesabinda islem yapan bir komut satiri gonderilmis oldu.
            #
            # Tablo piyasa bilgisi ve paylasilabilir; ama EMIR SATIRI
            # hesaba bagli. Ikisini ayirmak yerine mesajin tamamini
            # hesap sahibine vermek dogru: defter satirlari da zaten
            # `ibkr.sahip` adina yaziliyor (Adim 4), yani karne, emir
            # ve mesaj AYNI kisiyi gosteriyor. Belge §7 de "Ali'ye TEK
            # mesaj" diyor — cogul degil.
            hedef = (self.s.get("ibkr.sahip") or "").strip().lower()
            if not hedef:
                log.error("[%s] `ibkr.sahip` yok — strateji tablosu "
                          "GONDERILMEDI (kime gidecegi belirsiz)", kip)
            elif hedef not in sahipler:
                # Kipin alicisi degilse gondermek, o kisinin kendi
                # kipinde alacagi mesaji ERKEN vermek olurdu.
                log.info("[%s] strateji tablosu atlandi: `%s` bu kipin "
                         "alicisi degil", kip, hedef)
            else:
                metin = strateji_mesaji(strateji, strateji["secilen"],
                                        strateji["ayar"])
                # `kaynak=kip`: bu bir ANALIZ ciktisi, sistem uyarisi
                # degil — model kendi soyledigini hatirlamali.
                self._sahibe_bildir(
                    hedef, metin, kaynak=kip,
                    reply_markup=strateji_butonlari(strateji["secilen"]))

        # MUTABAKAT — DOLUM PENCERESI DAR, KACIRILIRSA GERI ALINAMIYOR.
        #
        # OLCULEN KUSUR (2026-08-31). `mutabakat.kos()` gercek dolum
        # fiyatini ve komisyonunu KOLONLARA yaziyor ve mekanizma
        # calisiyor — ama tek cagirani sohbetteki `ibkr_mutabakat`
        # araciydi. HICBIR zamanlanmis kosuda yoktu. Sonuc: 4 emrin
        # dordunde de `dolum_fiyat` NULL, yani SLIPPAGE SIFIR GOZLEM.
        # Bu oturumdaki her maliyet sayisi `/whatif` ONIZLEMESINDEN
        # geliyor; gerceklesen hic olculmedi.
        #
        # Geriye donuk alinamiyor: KO emrinin dolumu icin IBKR'nin
        # islem penceresi 0 kayit dondurdu (olculdu, §Adim 5). Yani
        # kacan dolum KALICI OLARAK kayip. Zamanli kosmasinin sebebi bu.
        #
        # PARA HAREKETI YOK: `mutabakat_ozetli` IBKR'ye tek bir yazma
        # cagrisi bile gondermiyor (kendi docstring'i bunu beyan
        # ediyor). Onay mimarisi degismiyor.
        # SAAT ONCE BASLIYOR — mutabakat DA butcenin icinde.
        #
        # Ilk yazimda cagri `basladi`dan ONCEYDI ve bir test yakaladi.
        # Mutabakat gercek duvar saati yiyor (IBKR gidis-donusu); onu
        # sayacin disinda birakmak, panelin kalan sureyi OLDUGUNDAN
        # BUYUK gormesine yol acardi. Bu, 2026-08-21'de olculen kusurun
        # ta kendisi: `_panel_butcesi` yanlis ANDA bakiyordu, toplama
        # 320 sn yedi ve kabuk surec grubunu oldurdu.
        import time
        basladi = time.monotonic()

        self._mutabakat_kosumu(kip, sahipler, bildir)
        # BAYAT VERI KONTROLU DE SAYACIN ICINDE. Mutabakatta ogrenilen
        # ders (2026-08-21): butce sayacinin DISINDA kosan bir adim,
        # panelin kalan sureyi OLDUGUNDAN BUYUK gormesine yol aciyor.
        self._bayat_veri_uyarisi(kip, sahipler, bildir)
        self._gun_sonu_olcumu(kip)
        # OLCUMDEN HEMEN SONRA BILDIRIM — SIRA ONEMLI.
        #
        # Once olcum, sonra rapor: tersi olsaydi bildirim BIR KOSU
        # GERIDEN gider ve aksam olculen taktikler ancak ertesi aksam
        # duyurulurdu. Ikisi ayri fonksiyon cunku olcum HER kipte,
        # bildirim yalnizca NABIZDA calisiyor.
        self._gun_sonu_bildirimi(kip, sahipler, bildir)
        sonuclar, basarisiz, atlanan = {}, [], []
        for sira, s in enumerate(sahipler):
            # BUTCE BURADA HESAPLANIR — KOSUNUN BASINDA DEGIL.
            #
            # OLCULEN KUSUR (2026-08-21, ogle 12:50 ve sabah 08:25):
            # `_panel_butcesi` ORTAK FAZDAN ONCE bir kez cagriliyordu. O
            # anda kabuk son tarihine 1200 sn vardi, 900 sn'lik panel
            # butcesi sigiyordu ve kisilma yapilmadi. Sonra toplama +
            # ortak faz 320 sn yedi; panel yine TAM 900'unu istedi ve
            # toplam 1220 > 1200 oldu. Kabuk surec grubunu oldurdu:
            # ne iz, ne tahmin, ne mesaj.
            #
            # Kisilma uyarisi o gune kadar HIC calmamisti (logda 0 kez)
            # — koruma vardi ama BAKTIGI AN yanlisti. Simdi her sahipten
            # once, GERCEK duvar saatine gore yeniden hesaplaniyor;
            # boylece toplama uzarsa da, ilk sahip yavas biterse de
            # ikinci sahip kalan sureyi dogru goruyor.
            kalan = self._panel_butcesi(ayar,
                                        harcanan=time.monotonic() - basladi)
            # ADIL PAY: kalan sure, KALAN SAHIP SAYISINA bolunur.
            #
            # Onceden boyle bir bolusme YOKTU ve birinci sahip butun
            # butceyi yiyebiliyordu — 2026-08-21'de tam bu oldu: ali'nin
            # paneli 1055 sn kostu, yuksel'inki HIC baslamadi ve kabuk
            # ikisini birden oldurdu. Pay, panelin toplamda `panel_butce`
            # icinde kalmasini GARANTI eder.
            pay = kalan / max(1, len(sahipler) - sira)
            if panel and pay < ASGARI_PANEL_SN:
                # BUTCE DOLDU: paneli atla ama SOYLE. Deterministik
                # adimlar (tez, portfoy riski) yine kosar — ucuz ve
                # kullanicinin en cok isine yarayan cikti onlar.
                log.warning("[%s] panel butcesi yetmiyor (pay %.0f sn < %.0f) "
                            "— '%s' paneli atlaniyor", kip, pay,
                            ASGARI_PANEL_SN, s)
                atlanan.append(s)
                try:
                    sonuclar[s] = self._kisisel_faz(s, kip, bildir, False,
                                                    ortak)
                except Exception as e:                # noqa: BLE001
                    log.exception("[%s] '%s' hafif kosu da patladi", kip, s)
                    sonuclar[s] = {"hata": f"{type(e).__name__}: {e}"}
                if bildir:
                    self._sahibe_bildir(
                        s, f"🟡 <b>{kip}: panel kosamadi</b>\n\n"
                        f"Panel icin ayrilan sure doldu (kalan {kalan/60:.0f} "
                        "dk). Tez alarmi ve portfoy riski kontrol edildi; "
                        "model yorumu bu kosuda uretilmedi.\n"
                        + self._butce_teshisi(ayar))
                continue
            try:
                sonuclar[s] = self._kisisel_faz(
                    s, kip, bildir, panel, ortak, panel_payi=pay)
            except Exception as e:                    # noqa: BLE001
                # IZOLASYON: bir sahibin hatasi digerini DURDURMAZ.
                log.exception("[%s] sahip '%s' kosusu patladi", kip, s)
                basarisiz.append(s)
                sonuclar[s] = {"hata": f"{type(e).__name__}: {e}"}
                if bildir:
                    self._sahibe_bildir(s, self._hata_metni(kip, e))

        # KOSU IZI. Bir kosunun CALISTIGINI baska hicbir kayit tek basina
        # soyleyemiyordu: `signals` tarih-bazli ve kip tasimiyor,
        # `collector_runs` sohbetten tetiklenen toplamalarla karisiyor,
        # `panel_runs` yalnizca LLM panelinde yaziliyor. Bu yuzden ogle
        # kosusunun 17 Agustos'ta hic calismadigi GUNLERCE gorunmedi.
        # Iz burada, yani isin SONUNDA birakiliyor; yarim kalan kosu iz
        # birakmaz ve bekci bunu yakalar (bot/watchdog.py).
        self._iz_birak(kip, sahipler, ortak)

        return {"kip": kip, "sahipler": sahipler, "basarisiz": basarisiz,
                "panel_atlanan": atlanan,
                # `strateji` OZETLENIYOR: ham hali 518 sembolluk
                # seviyeleri tasiyor ve donus degeri loglara/testlere
                # gidiyor. Sayilar kalıyor, govde degil.
                "ortak": {k: (self._strateji_ozeti(v) if k == "strateji" else v)
                          for k, v in ortak.items() if k != "sinyaller"},
                "sonuc": sonuclar,
                # Tek sahipli kurulumda BUGUNKU sozlesme korunuyor:
                # cagiranlar (run.py, testler) duz alanlari okuyor.
                **(sonuclar[sahipler[0]] if len(sahipler) == 1
                   and "hata" not in sonuclar[sahipler[0]] else {})}

    def _butce_teshisi(self, ayar: dict) -> str:
        """
        Panel neden sigmadi — SAYIYLA, mesajin icinde.

        NEDEN VAR (2026-08-29). "Panel kosamadi" mesaji ARIZAYI
        soyluyordu ama SEBEBINI degil. 28 Agustos gecesi sebebi bulmak
        icin `pulse.log`da collector sureleri toplandi, `collector_runs`
        sorgulandi ve panel butcesi satirlari elle karsilastirildi —
        yirmi dakikalik log arkeolojisi. Ayni sayi mesajin icinde
        olabilirdi.

        Bu, bu deponun tekrar eden dersinin bir baska yuzu: bir uyari
        "ne oldu"yu soyleyip "neden"i saklarsa, bir sonraki sefer yine
        ayni kazi yapilir. Ve butce YENIDEN bayatlayacak — toplama her
        yeni kaynakla buyuyor.

        Kabuk son tarihi yoksa (elle kosum) SESSIZ: uydurma bir oran
        yazmaktansa hicbir sey yazmamak dogru.
        """
        import os
        import time

        ham = os.getenv(KOSU_BITIS_ENV)
        try:
            toplam = float(ayar["kabuk_butce_sn"])
            bitis = float(ham)
        except (TypeError, ValueError, KeyError):
            return ""
        harcanan = toplam - (bitis - time.time())
        if harcanan <= 0 or toplam <= 0:
            return ""
        return (f"\n<i>Kosunun {toplam:.0f} sn'lik butcesinin "
                f"{harcanan:.0f} sn'si panel sirasi gelmeden harcandi "
                f"(%{100 * harcanan / toplam:.0f}). Toplama uzadiysa "
                f"<code>ritim.kipler.{ayar.get('kip', '')}.kabuk_butce_sn</code> "
                "yeniden turetilmeli.</i>")

    def _panel_butcesi(self, ayar: dict, harcanan: float = 0.0) -> float:
        """
        SU ANDAN itibaren panele kalan sure. `harcanan` = panelin
        simdiye kadar yedigi saniye.

        UC SINIRIN EN KUCUGU:
          1. kipin kendi butcesi eksi HARCANAN
          2. modul tavani (`PANEL_SURE_BUTCESI_SN`) eksi HARCANAN
          3. KABUGUN OLDURME ANI eksi teslimat payi

        HER SAHIPTEN ONCE YENIDEN CAGRILIR. Kosunun basinda bir kez
        hesaplamak, toplama fazinin suresini butceden HIC dusmuyordu ve
        2026-08-21'de iki kosuyu birden oldurttu (bkz. `calistir`).
        Ucuncu sinir duvar saatinden okundugu icin, arada gecen HER SEY
        — toplama, ortak faz, onceki sahibin paneli — kendiliginde
        hesaba giriyor.

        UCUNCUSU 2026-08-21'de eklendi ve asil garantiyi o veriyor.
        Ilk ikisi kosunun kendi ic paylasimini duzenliyordu ama kabugun
        duvar saatinden HABERSIZDI: toplama uzayinca (tuik 201 sn) panel
        yine tam butcesini istedi, toplam 1500 sn'yi asti ve kabuk surec
        grubunu oldurdu. Oldurulen kosu iz birakmaz, tahmin yazmaz, mesaj
        gondermez — yani en pahali kayip bicimi.

        Negatif ya da cok kucuk cikabilir; cagiran taraf bunu "panel
        atlandi ve SOYLENDI" olarak isliyor. Sessizce sifira dusurmuyoruz:
        panelin kosmamasi, kosup hicbir sey uretmemesinden iyidir.
        """
        import os
        import time

        butce = (min(float(ayar["panel_butce_sn"]), PANEL_SURE_BUTCESI_SN)
                 - max(0.0, harcanan))
        ham = os.getenv(KOSU_BITIS_ENV)
        if not ham:
            return max(0.0, butce)
        try:
            bitis = float(ham)
        except (TypeError, ValueError):
            # BOZUK DEGER SESSIZCE YOK SAYILMAZ: kabuk bir sey gecirmis
            # ama okunamiyorsa, koruma calisiyor sanip korumasiz kosmak
            # tam olarak kacinilmak istenen durum.
            log.error("[nabiz] %s okunamadi (%r) — kabuk son tarihi "
                      "UYGULANMIYOR", KOSU_BITIS_ENV, ham)
            return butce
        kabuk_kalan = bitis - time.time() - TESLIMAT_PAYI_SN
        if kabuk_kalan < butce:
            log.warning("[nabiz] panel butcesi kabuk son tarihine gore "
                        "%.0f sn -> %.0f sn kisildi (harcanan %.0f sn)",
                        butce, max(0.0, kabuk_kalan), harcanan)
            return max(0.0, kabuk_kalan)
        return max(0.0, butce)

    def _iz_birak(self, kip: str, sahipler: list, ortak: dict) -> None:
        """Kosu izi — ASLA kosuyu dusurmez, yalnizca gozetim icin."""
        import json
        from pathlib import Path
        try:
            # `bot_state_dir` — `root`tan DEGIL. Bu dizin bekcinin
            # kanitidir ve `BOT_STATE_DIR` ile tasinabilmeli; aksi
            # halde bir alt surec testi gercek izin ustune yazar
            # (olculdu, bkz. `Settings.bot_state_dir`).
            dizin = Path(self.s.bot_state_dir) / "kosu"
            dizin.mkdir(parents=True, exist_ok=True)
            (dizin / f"{kip}.json").write_text(json.dumps({
                "kip": kip,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sahipler": list(sahipler),
                "piyasa_sinyali": int(ortak.get("piyasa_sinyali", 0)),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as e:                        # noqa: BLE001
            log.warning("[%s] kosu izi yazilamadi: %s", kip, e)

    # ------------------------------------------------------------------
    # Bu gun sayisindan ESKI anlik goruntu "bayat" sayilir ve SORULUR.
    # 2 gun: bugun ve dun bayat DEGIL. 1 olsaydi dun cekilmis bir
    # goruntu icin de sorulurdu ve gurultu, gercek bayatligi gomerdi.
    BAYAT_VERI_ESIK_GUN = 2

    # Bayat veri yalnizca BU KIPTE soruluyor. Nabiz 22:15'te kosuyor,
    # yani ABD kapanisindan (22:00) hemen SONRA — gunun son borsasi da
    # kapanmis, adetler artik degismeyecek. Sabah sormak "bugun islem
    # yaparsan yine bayatlar" demekti; gun ici sormak seansi bolerdi.
    BAYAT_VERI_KIPI = "nabiz"

    def _gun_sonu_olcumu(self, kip: str) -> dict:
        """
        Taktiklerin GUN SONU olcumu — seansi kapanmis olanlar icin.

        KIP SUZGECI YOK ve bu bilincli: modulun kendisi her taktik icin
        `seans_kapandi_mi` bakiyor, yani kapanmamis borsayi zaten
        atliyor. Kipe gore kisitlamak, ABD kapanisini (22:00) yalnizca
        nabza baglamak demekti — BIST 18:00'de kapaniyor ve kapanis
        kosusu 17:45'te. Her kosumda cagirmak, olcumun BORSAYA gore
        yapilmasini sagliyor.

        SESSIZ: bu bir DEFTER BAKIMI adimi, mesaj uretmiyor. Sonuclar
        karnede toplaniyor; her kosuda "3 taktik olculdu" demek gunde
        dort bildirim ve gercek olayi gurultuye gomer.

        NABZI DUSURMEZ (kural 1) — arizasi piyasa taramasini iptal
        etmemeli.
        """
        try:
            from . import gun_sonu
            return gun_sonu.olc(self.db)
        except Exception as e:                        # noqa: BLE001
            log.warning("[%s] gun sonu olcumu basarisiz: %s: %s",
                        kip, type(e).__name__, e)
            return {"durum": "hata", "sebep": str(e)[:120]}

    # Gun sonu SICILI yalnizca bu kipte bildiriliyor. Olcumun KENDISI
    # her kosuda calisiyor (`_gun_sonu_olcumu`, kip suzgeci yok) — bu
    # ayri bir sey: sonucu SOYLEMEK.
    #
    # Nabiz 22:15'te kosuyor, yani BIST kapanisindan (18:00) sonra.
    # ABD 23:00'te kapaniyor, yani ABD taktikleri ERTESI SABAH olculur
    # ve o aksamki bildirimde kendi tarihiyle gorunur. Bu, ozel bir
    # durum gerektirmiyor: bildirim "bugun OLCULENLER"i raporluyor,
    # "bugun VERILENLER"i degil.
    GUN_SONU_BILDIRIM_KIPI = "nabiz"

    def _gun_sonu_bildirimi(self, kip: str, sahipler: list,
                            bildir: bool) -> dict:
        """
        Gun sonu sicilini SAHIBINE bildirir — gunde bir kez.

        NEDEN VAR (2026-09-01): olcum sema 28'den beri calisiyordu ve
        `karne` yazilmisti, ama KIMSE CAGIRMIYORDU. Ne mesajda ne
        aracta tek okuyucusu vardi. Bu deponun bir numarali ariza
        kalibi: fonksiyon dogru, test edilmis, kablo bagli degil.

        UC KURAL (`_mutabakat_kosumu` ile ayni):
          1. NABZI DUSURMEZ — bakim adimi, piyasa taramasini iptal etmez.
          2. YALNIZCA SAHIBINE — canli veride iki sahip var (ali 42
             olcum, yuksel 30); karistirmak baskasinin sicilini
             gostermek olurdu.
          3. SOYLEYECEK SEY YOKSA SUSAR — "bugun 0 taktik olculdu"
             demek gunde bir gereksiz bildirim ve gercek olayi
             gurultuye gomer.

        ORAN TEK BASINA GITMEZ. Kiyassiz isabet orani tesadufu beceri
        gibi gosterir; bu yuzden taban oran AYNI CUMLEDE duruyor ve
        fark anlamli degilse oran HIC yazilmiyor.
        """
        if kip != self.GUN_SONU_BILDIRIM_KIPI:
            return {"durum": "atlandi", "sebep": f"{kip} bu bildirimin kipi degil"}
        if not bildir:
            return {"durum": "atlandi", "sebep": "bildirim kapali"}
        try:
            from . import gun_sonu
            gonderilen = dusen = 0
            for sahip in sahipler:
                gun = gun_sonu.gunun_olcumu(self.db, sahip)
                if not gun["adet"]:
                    log.info("[%s] gun sonu bildirimi ATLANDI (%s): "
                             "bugun islenen taktik yok", kip, sahip)
                    continue                    # KURAL 3: sessiz kal
                metin = self._gun_sonu_metni(
                    gun, gun_sonu.karne(self.db, sahip=sahip))
                if not metin:
                    continue
                # SONUC LOGLANIYOR — "cagirdim" DEGIL, "GITTI".
                #
                # 1 Eylul gecesi bu bildirim ilk kez kostu ve gittigi
                # SANILDI: hata yoktu, kuru kosum mesaji uretiyordu ve o
                # gece iki sahipte de olcum vardi. Ama HICBIR SEY
                # yazilmadigi icin kanit yoktu — "muhtemel" kanit degil.
                # `_sahibe_bildir` zaten "en az biri gitti mi" doner;
                # eksik olan tek sey o degeri OKUMAKTI.
                if self._sahibe_bildir(sahip, metin):
                    gonderilen += 1
                    log.info("[%s] gun sonu bildirimi GONDERILDI (%s): "
                             "%d taktik islendi, %d karakter",
                             kip, sahip, gun["adet"], len(metin))
                else:
                    dusen += 1
                    log.error("[%s] gun sonu bildirimi GONDERILEMEDI (%s) — "
                              "olcum yapildi ama kullaniciya ULASMADI",
                              kip, sahip)
            return {"durum": "ok", "gonderilen": gonderilen, "dusen": dusen}
        except Exception as e:                        # noqa: BLE001
            # KURAL 1: genis yakalama bilincli.
            log.warning("[%s] gun sonu bildirimi basarisiz: %s: %s",
                        kip, type(e).__name__, e)
            return {"durum": "hata", "sebep": str(e)[:120]}

    @staticmethod
    def _gun_sonu_metni(gun: dict, karne: dict) -> str:
        """
        Bildirim metni. ORAN ILE TABAN AYRILAMAZ.

        Metin uc katman tasiyor ve ucu de gerekli:
          * AKIS   — bugun ne olculdu (yeni bilgi)
          * STOK   — 30 gunluk sicil (baglam)
          * SINIR  — sayinin ne ifade ETMEDIGI (durustluk)

        Ucuncusu olmadan mesaj EKSILTIYLE YALAN SOYLER: canli veride
        %82,7'ye karsi %72,1 duruyor ve okuyan bunu bir kenar sanir;
        oysa tek yonlu binom p = 0,057, yani fark henuz gurultuden
        ayirt edilemiyor.
        """
        from .gun_sonu import AYAKTA, DAYANDI, GIRIS_YOK, OLCULEMEDI, STOP_YENDI

        ETIKET = {AYAKTA: "ayakta", DAYANDI: "stop dayandı",
                  STOP_YENDI: "stop yendi",
                  GIRIS_YOK: "giriş tetiklenmedi", OLCULEMEDI: "ölçülemedi"}
        d = gun["dagilim"]
        yer = ", ".join(f"{v} {k}" for k, v in sorted(gun["venue"].items()))
        # "OLCULDU" DEGIL "ISLENDI" — BASLIK GOVDEYLE CELISIYORDU.
        # Ali 2026-09-02'de bildirdi: mesaj "11 taktik olculdu" deyip
        # hemen altinda "8 olculemedi · 3 giris tetiklenmedi" yaziyordu.
        # `adet` = gun_sonu_sonuc YAZILAN satir sayisi, yani ELE ALINAN;
        # kacinin gercekten puanlandigi ALTTAKI dagilimda. Basligin
        # "olculdu" demesi o gun sifir puanlama olan bir kosuyu basarili
        # gibi gosteriyordu.
        L = [f"📋 <b>Gün sonu</b> · {gun['adet']} taktik işlendi ({yer})"]

        # Sonuc dagilimi — SIFIR OLANLAR YAZILMIYOR.
        parca = [f"{v} {ETIKET.get(k, k)}"
                 for k, v in sorted(d.items(), key=lambda x: -x[1])]
        L.append("   " + " · ".join(parca))

        # TAKTIK TARIHI OLCUM GUNUNDEN FARKLIYSA SOYLENIR. ABD
        # taktikleri ertesi sabah olculuyor; "bugun olculdu" ile "bugun
        # verildi" ayni sey degil ve karistirilmasi kolay.
        # COK TARIH VARSA ARALIK YAZILIYOR, LISTE DEGIL.
        #
        # Ilk yazimda hepsi virgulle diziliyordu ve test 27 tarihlik bir
        # satir uretti. Sahada bu kacinilmaz: 2026-09-01'de biriken 72
        # taktik TEK KOSUDA olculdu ve mesaj okunmaz olurdu. Satirin isi
        # "bunlar bugunun taktikleri DEGIL" demek; bunun icin aralik
        # yeter.
        tarihler = gun.get("taktik_tarihleri") or []
        if tarihler and tarihler != [gun.get("olcum_gunu")]:
            ozet = (", ".join(tarihler) if len(tarihler) <= 3
                    else f"{tarihler[0]} - {tarihler[-1]} ({len(tarihler)} gün)")
            L.append(f"   <i>taktik tarihleri: {ozet}</i>")

        olcum, taban = karne.get("olcum") or 0, karne.get("taban_%")
        if not olcum:
            return "\n".join(L)

        # `_yuzde_tr` KULLANILMIYOR ve bu bilincli: o fonksiyon bir
        # DEGISIMI yaziyor ve basina isaret koyuyor ("+%82,70"). Ayakta
        # kalma orani bir SEVIYE; "+" isareti onu yukselmis gibi
        # gosterirdi.
        ayakta = karne.get("ayakta") or 0
        oran = f"%{_tr(karne['oran_%'], 1)}"
        L.append(f"\n30 gün: <b>{ayakta}/{olcum}</b> ayakta ({oran})")
        if taban is None:
            L.append("<i>Kıyaslanacak taban oran hesaplanamadı — çıplak oran "
                     "yanıltır, bu sayıyı tek başına okuma.</i>")
            return "\n".join(L)

        # TABAN AYNI CUMLENIN DEVAMINDA. Ayri bir satira almak, birinin
        # digeri olmadan alintilanmasini kolaylastirirdi.
        #
        # "piyasanin %78,2'i" YAZILMIYOR: Turkce iyelik eki son rakamin
        # OKUNUSUNA gore degisiyor (2 -> 'si, 1 -> 'i, 3 -> 'u) ve tek
        # bir cumle icin ek motoru yazmak gereksiz. Cumle ek
        # GEREKTIRMEYECEK bicimde kuruluyor — ayrica "bu oran" iki
        # sayinin AYNI SEYI olctugunu daha acik soyluyor.
        L[-1] += f" — aynı gün piyasada bu oran <b>%{_tr(taban, 1)}</b>."
        fark = _tr(karne.get("taban_farki_puan") or 0, 1)
        if karne.get("taban_farki_anlamli") is True:
            L.append(f"<i>Fark {fark} puan ve tesadüfle açıklanamıyor "
                     f"(p={karne['taban_farki_p']}, n={olcum}).</i>")
        else:
            gerek = karne.get("ayni_oranla_gereken_n")
            L.append(
                f"<i>Fark {fark} puan; n={olcum}'de tesadüften AYIRT "
                "EDİLEMİYOR"
                + (f" — aynı tempoda ~{gerek} ölçüm gerekir." if gerek else ".")
                + " Bu sayıyı bir başarı oranı gibi okuma.</i>")
        return "\n".join(L)

    def _bayat_veri_uyarisi(self, kip: str, sahipler: list,
                            bildir: bool) -> dict:
        """
        Bayat pozisyon anlik goruntusu varsa SAHIBINE SORAR.

        NEDEN VAR (2026-09-01, Ali'nin istegi): "OZELLIKLE BAYAT BIR
        VERI ISTEMIYORUM. Eger bayat veri varsa agent bana SORSUN gun
        bitmeden."

        Sahada olculen hal: IBKR 5, BUX 8, Binance 14 gun eski anlik
        goruntuyle raporlaniyordu. Tarama bunu SOYLUYORDU ama yalnizca
        kucuk bir dipnot olarak; kimse o dipnota bakip ekran goruntusu
        gondermiyordu. Dipnot BILGI verir, SORU is yaptirir.

        `_mutabakat_kosumu` ile AYNI UC KURAL: nabzi asla dusurmez,
        yalnizca sahibine gider, soyleyecek sey yoksa susar.

        ISTENEN IS HESABA GORE FARKLI ve bu ayrim `portfolio.ADET_KAYNAGI`
        uzerinden geliyor: IBKR'de ekran goruntusu YOK, orada gereken
        sey OTURUM. Ali'ye yanlis is yaptirmamak icin cumle ayriliyor.
        """
        if kip != self.BAYAT_VERI_KIPI:
            return {"durum": "atlandi", "sebep": f"{kip} bu kontrolun kipi degil"}
        # ESIK AYARDAN — 0 KAPATIR.
        #
        # Testler icin sart: fixture'lar SABIT tarihli pozisyon yaziyor
        # (`_fazb_db`: 2026-08-16) ve o tarih her gun biraz daha
        # bayatliyor. Kontrol ayarla kapatilamasaydi, mesaj sayisi
        # olcen her test bir gun kendiliginden kirilirdi — zamana bagli
        # test, bu oturumda `_b6_baz`ta bir kez yasandi.
        esik = int(self.s.get("ritim.bayat_veri_esik_gun",
                              self.BAYAT_VERI_ESIK_GUN))
        if esik <= 0:
            return {"durum": "atlandi", "sebep": "bayat_veri_esik_gun kapali"}
        try:
            from ..analysis.portfolio import ADET_KAYNAGI
            toplam = 0
            for sahip in sahipler:
                bayat = self.db.bayat_hesaplar(
                    sahip, esik_gun=esik)
                if not bayat:
                    continue
                toplam += len(bayat)
                satir = []
                for b in bayat:
                    ad = str(b["hesap"]).lower()
                    if ADET_KAYNAGI.get(ad) == "api":
                        ne = "IBKR oturumu açıkken tazelenmeli (ekran görüntüsü DEĞİL)"
                    else:
                        ne = "güncel ekran görüntüsü gönder"
                    satir.append(
                        f"• <b>{b['hesap'].upper()}</b> — son {b['son_ts']}, "
                        f"<b>{b['yas_gun']} gün</b> önce · {ne}")
                self._sahibe_bildir(sahip, (
                    "📸 <b>Portföy adetleri bayat</b>\n\n"
                    + "\n".join(satir)
                    + "\n\n<i>Fiyatlar güncel; bayat olan ADETLER. Arada "
                      "işlem yaptıysan yüzdeler ve ağırlıklar yanlış "
                      "çıkar — ve bunu VERİDEN bilemem.</i>"))
            return {"durum": "ok", "bayat": toplam}
        except Exception as e:                        # noqa: BLE001
            # GENIS YAKALAMA BILINCLI (kural 1): bu bir BAKIM adimi,
            # arizasi piyasa taramasini iptal etmemeli.
            log.warning("[%s] bayat veri kontrolu basarisiz: %s: %s",
                        kip, type(e).__name__, e)
            return {"durum": "hata", "sebep": str(e)[:120]}

    def _mutabakat_kosumu(self, kip: str, sahipler: list, bildir: bool) -> dict:
        """
        IBKR mutabakati — ZAMANLI. Dolum penceresi kacmasin diye.

        UC KURAL:

        1. NABZI ASLA DUSURMEZ. IBKR oturumu kopuksa, hesap yoksa, ag
           gittiyse: loglanir ve devam edilir. Mutabakat bir ANALIZ
           adimi degil, bir DEFTER BAKIMI adimi; onun arizasi piyasa
           taramasini ve paneli iptal etmemeli.

        2. YALNIZCA HESAP SAHIBINE. `/emir` tek IBKR hesabini kullaniyor
           (`emirakis._hesap`), yani defter satirlari `ibkr.sahip`
           adina. Baskasina gondermek, baskasinin hesabindaki emirleri
           gostermek olurdu — strateji tablosunda yasanan kusurun aynisi.

        3. SESSIZ OLDUGUNDA SESSIZ KALIR. Her kosuda "mutabakat temiz"
           mesaji atmak, gunde dort bildirim demek ve gercek bir olay
           geldiginde onu gurultuye gomer. Mesaj YALNIZCA bir sey
           DEGISTIYSE gider.
        """
        hedef = (self.s.get("ibkr.sahip") or "").strip().lower()
        if not hedef:
            log.info("[%s] mutabakat atlandi: `ibkr.sahip` tanimsiz", kip)
            return {"durum": "atlandi", "sebep": "ibkr.sahip yok"}
        if hedef not in sahipler:
            return {"durum": "atlandi", "sebep": f"{hedef} bu kipin alicisi degil"}

        try:
            from ..bot.emirakis import mutabakat_ozetli
            metin, ozet = mutabakat_ozetli(self.s, self.db, hedef)
        except Exception as e:                            # noqa: BLE001
            # GENIS YAKALAMA BILINCLI (kural 1). IBKR katmani
            # `IbkrHatasi` disinda da patlayabiliyor: oturum kopmasi,
            # httpx zaman asimi, JSON sekli. Hangisi olursa olsun
            # nabiz devam etmeli.
            log.warning("[%s] mutabakat yapilamadi: %s: %s",
                        kip, type(e).__name__, e)
            return {"durum": "hata", "sebep": f"{type(e).__name__}: {e}"}

        log.info("[%s] mutabakat: %s karar, %s satir yazildi, "
                 "%s DOLUM kaydedildi, %s cozulemedi",
                 kip, ozet["karar"], ozet["yazilan"],
                 ozet["dolum_yazildi"], ozet["cozulemeyen"])

        # HABER VAR MI? Dolum yazildiysa, defter degistiyse, bir satir
        # cozulemediyse ya da IBKR'de bizim defterde OLMAYAN emir varsa.
        haber = any(ozet[k] for k in
                    ("dolum_yazildi", "yazilan", "cozulemeyen", "defterde_yok"))
        if bildir and haber:
            onek = ("💰 <b>Dolum kaydedildi</b>\n\n"
                    if ozet["dolum_yazildi"] else "")
            self._sahibe_bildir(hedef, onek + metin, kaynak=kip)
        return {"durum": "ok", **ozet, "bildirildi": bool(bildir and haber)}

    # ------------------------------------------------------------------
    def _ortak_faz(self, kip: str) -> dict:
        """
        Kisiden BAGIMSIZ adimlar — bir kez kosar.

        1. `puanla()` TUM sahiplerin vadesi dolmus tahminlerini olcer.
           Deterministik ve LLM'siz; kisi basina kosturmak ayni isi N
           kere yapardi ve `ix_pred_olcum` bu yuzden bilerek sahipsiz.
        2. Piyasa taramasi BIR KEZ; sinyaller 'ortak' yazilir. Sinyal
           sayisi sahip sayisiyla ARTMAZ.
        """
        from .journal import Defter
        from .screener import Tarayici

        Defter(self.db).puanla()          # sahipsiz: hepsini puanlar
        tarayici = Tarayici(self.s, self.db)
        piyasa = tarayici.tara()          # sahipsiz: portfoy riski YOK
        tarayici.kaydet(piyasa)           # hepsi 'ortak'
        log.info("[%s] ortak faz: %d piyasa sinyali", kip, len(piyasa))
        strateji = self._strateji_taramasi(kip)
        if strateji:
            # TARAMA VE DEFTERE YAZIM AYRI: tarama saf okuma, yazim yan
            # etkili. Ayirmasaydik her elle kosu ve her test canli
            # deftere satir atardi.
            strateji["defter"] = self._strateji_deftere_yaz(strateji)
        return {"sinyaller": piyasa, "piyasa_sinyali": len(piyasa),
                "tarayici": tarayici, "strateji": strateji}

    def _strateji_deftere_yaz(self, strateji: dict) -> dict:
        """
        Kirilimlari tahmin defterine yazar. TASARIMIN MERKEZI BURASI.

        HER KIRILIM YAZILIR — SECILSIN YA DA SECILMESIN (`ajan='strateji'`).
        Kural TAM GENISLIKTE olculur (~247 sinyal/ay, olculdu); hesap ise
        yalnizca onay bant genisligi kadarini isler (~40/ay). Ikincisi
        birincinin YANSIZ bir alt-orneklemi oldugu icin (tohumlu rastgele
        secim, "en iyi" DEGIL) ikisi karsilastirilabilir kalir.

        SECILENLER AYRICA `ajan='strateji_secilen'` olarak IKINCI BIR
        SATIRLA yazilir. `UNIQUE (olusma_ts, instrument_id, ufuk_gun,
        ajan, sahip)` kisitina `ajan` dahil oldugu icin cakisma olmaz ve
        iki karne ayri ayri okunur.

        SAHIP `ibkr.sahip`TEN — VARSAYILAN YOK. Bu satirlarin karnesi
        emrin gidecegi hesabin karnesi; baska birinin defterine yazmak
        `insert_positions`in uyardigi tehlikenin ta kendisi ("yanlis
        kisinin portfoyune yazmak bu isin tek gercek tehlikesi; sessiz
        varsayilan onu kaza degil TASARIM haline getirirdi").

        YENI PUANLAMA KODU YOK: `Defter.puanla()` zaten `taktik_giris`
        dolu satirlari `_tetiklendi` yolundan geciriyor ve `karar()` o
        alani dolduruyor.
        """
        from .journal import Defter

        sahip = (self.s.get("ibkr.sahip") or "").strip().lower()
        if not sahip:
            log.error("[strateji] `ibkr.sahip` tanimli degil — deftere "
                      "YAZILMADI. Kimin karnesi olacagi belirsizken yazmak, "
                      "yanlis kisinin defterine yazmaktir.")
            return {"yazilan": 0, "hata": "ibkr.sahip yok"}

        gorusler = strateji.get("gorusler") or []
        if not gorusler:
            return {"yazilan": 0}
        secilen = {g.get("sembol") for g in (strateji.get("secilen") or [])}
        # `seviyeler` DEFTERE GITMEZ: `kaydet` sozlesmesinde yok ve
        # 518 sembolluk govdeyi tasimasi gereksiz.
        tam = [{k: v for k, v in g.items() if k not in ("seviyeler", "conid")}
               for g in gorusler]
        ikinci = [{**g, "ajan": "strateji_secilen"}
                  for g in tam if g.get("sembol") in secilen]
        # LLM KOLU UCUNCU SATIR OLARAK. `UNIQUE`e `ajan` dahil oldugu
        # icin cakismiyor ve karnesi AYRI okunuyor — eslestirilmis
        # kiyasin veri tarafi bu (belge §7).
        llm = [{k: v for k, v in g.items() if k != "seviyeler"}
               for g in ((strateji.get("llm") or {}).get("gorusler") or [])]

        defter = Defter(self.db)
        rapor = defter.kaydet(tam + ikinci + llm, sahip)

        # DORT SAYAC SIFIR OLMALI. Sifir degilse SEBEP BULUNUP
        # DUZELTILECEK — kabul edilip gecilmeyecek. Sessizce dusen bir
        # gorus, karneyi yansiz olmaktan cikarir: olculen sey artik
        # "kural" degil "kuralin yazilabilen kismi" olur.
        dusen = {k: rapor.get(k, 0) for k in
                 ("atilan_sembol_yok", "atilan_seri_yok", "atilan_cakisma",
                  "kosul_reddi") if rapor.get(k)}
        if dusen:
            log.error("[strateji] deftere yazimda GORUS DUSTU: %s — "
                      "karne artik yansiz degil, sebep bulunmali", dusen)
        log.info("[strateji] deftere yazildi: %d satir (%d tam + %d secilen "
                 "+ %d llm), sahip=%s", rapor.get("yazilan", 0), len(tam),
                 len(ikinci), len(llm), sahip)
        return rapor

    def _strateji_taramasi(self, kip: str) -> dict | None:
        """
        Donchian 20/10 + 2N gunluk kirilim taramasi. ORTAK FAZDA, cunku
        kisiden BAGIMSIZ: ayni piyasa, ayni kirilimlar. Sahip basina
        kosturmak ayni isi N kere yapardi.

        YALNIZCA `ibkr.strateji.kip` ILE ESLESEN KIPTE. Varsayilan
        'nabiz' (22:15 Amsterdam, ABD kapanisi sonrasi); 'kapanis'
        (17:45) SECILMEDI cunku o saatte ABD piyasasi ACIK ve gunluk
        bar YARIM — `yfinance` seans icinde kapanmamis bar donduruyor
        (olculdu: 27 Agu 14:18'de ASML.AS'nin o gunku bari geldi).

        ARIZA TARAMAYI DUSURMEZ, ama SESSIZ de kalmaz: None doner ve
        sebep loglanir. Nabiz'in geri kalani (panel, tez alarmi,
        koruma) strateji motoruna bagli degil.
        """
        if self.s.get("ibkr.strateji") is None:
            return None
        try:
            ayar = self.s.strateji_ayari(self.db)
        except ValueError as e:
            # BOZUK AYAR SESSIZCE ATLANMAZ. Sessiz atlama, motoru
            # "kosuyor" gosterirken hicbir sey uretmemesi demekti.
            log.error("[%s] strateji ayari gecersiz — tarama YAPILMADI: %s",
                      kip, e)
            return None
        if not ayar["enabled"] or kip != ayar["kip"]:
            return None

        from . import strateji as ST
        evren = self.db.endeks_uyeleri(ayar["endeksler"])
        sonuc = ST.tara(self.db, self.s, evren)

        # FREN AYARDAN DEGIL KARNEDEN: tavan `gunluk_emir_tavani`in
        # KENDISI degil, karneye gore duzeltilmis hali. Ayardaki degeri
        # dogrudan kullansaydik fren HIC devreye girmezdi — yazilmis
        # ama baglanmamis bir koruma, korumasizliktan KOTUDUR cunku
        # var sanilir.
        sahip = (self.s.get("ibkr.sahip") or "").strip().lower()
        # AYNI BAR IKINCI KEZ YAZILMAZ — secimden ve LLM'den ONCE.
        # Yeni bar gelmemis bir gecede (cokmus toplama, tatil) tarama
        # dunku kirilimlari aynen bulur; onlar zaten defterde. Elenen
        # sayi `sayaclar`a girer ve mesajda gorunur — sessiz kirpma yok.
        # Gerekce `strateji.ayni_bar_suzgeci`.
        kalan, ayni_bar = ST.ayni_bar_suzgeci(self.db, sonuc["gorusler"], sahip)
        if ayni_bar:
            sonuc = {**sonuc, "gorusler": kalan,
                     "sayaclar": {**sonuc["sayaclar"],
                                  ST.AYNI_BAR_SEBEBI: len(ayni_bar)}}
        fren = ST.tavan(self.db, self.s, sahip) if sahip else None
        etkin_tavan = (fren["tavan"] if fren
                       else int(ayar["gunluk_emir_tavani"]))
        secilen = ST.secim(sonuc["gorusler"], etkin_tavan,
                           ayar["secim_tohumu"])
        # CONID YALNIZCA SECILENLER ICIN: `/emir` satiri calisacak mi
        # sorusunun cevabi. Cevabi bilmeden komut vermek, kullaniciyi
        # hataya yollamak olurdu.
        for g in secilen:
            g["conid"] = self._conid(g.get("sembol"))
        self._adet_hesapla(secilen, ayar)

        # LLM YORUM KOLU — PARALEL, SUZGEC DEGIL.
        #
        # `secilen` YUKARIDA hesaplandi ve bu SIRA ONEMLI: model
        # yorumu secimden SONRA aliniyor, yani secime dokunamiyor.
        # Once cagirip sonra secseydik, ileride biri "modelin
        # begendiklerini sec" diye tek satir ekleyebilirdi ve olculen
        # sey artik kural olmazdi (belge §7).
        llm = self._strateji_llm(sonuc["gorusler"], ayar)

        # CIKIS — GIRISTEN AYRI VE FRENDEN BAGIMSIZ.
        #
        # Fren giris tavanini dusurur; CIKISI ASLA kismaz. Karne kotu
        # oldugu icin "satma" demek, korumayi tam gerekli oldugu anda
        # kapatmak olurdu. Ayni sebeple `gunluk_emir_tavani` de cikisa
        # uygulanmiyor: kural kac kagitta cikis diyorsa hepsi soylenir.
        try:
            cikis = ST.cikislar(self.db, self.s, sahip) if sahip else []
        except Exception as e:                        # noqa: BLE001
            # ARIZA GIRIS TARAFINI DUSURMEZ — ama SESSIZ de kalmaz.
            log.exception("[%s] cikis taramasi patladi: %s", kip, e)
            cikis = []
        log.info("[%s] strateji: %d sembol tarandi, %d kirilim, %d secildi "
                 "(tavan %d%s), %d cikis", kip, sonuc["taranan"],
                 len(sonuc["gorusler"]), len(secilen), etkin_tavan,
                 ", FREN" if (fren and fren["fren"]) else "", len(cikis))
        return {**sonuc, "secilen": secilen, "ayar": ayar, "fren": fren,
                "llm": llm, "cikis": cikis}

    def _strateji_llm(self, gorusler: list[dict], ayar: dict) -> dict | None:
        """
        LLM yorum kolu — `ibkr.strateji.llm_yorumu` acikken.

        HATA SINYALI DUSURMEZ: model cagrilamazsa kural kolu yine
        yazilir ve mesaj yine gider. Yorum bir EKLENTIDIR, on kosul
        degil. Sebep raporlaniyor, sessizce yutulmuyor.
        """
        if not ayar.get("llm_yorumu") or not gorusler:
            return None
        import asyncio

        from ..llm import kullanilabilir
        from . import strateji_llm as SL

        var, sebep = kullanilabilir(self.s)
        if not var:
            log.info("[strateji_llm] LLM kullanilamiyor: %s", sebep)
            return {"gorusler": [], "hata": sebep}
        try:
            return asyncio.run(SL.yorumla(self.s, gorusler,
                                          int(ayar.get("ufuk_gun") or 14)))
        except Exception as e:                             # noqa: BLE001
            log.warning("[strateji_llm] kol patladi: %s: %s",
                        type(e).__name__, e)
            return {"gorusler": [], "hata": f"{type(e).__name__}: {e}"}

    def _adet_hesapla(self, secilen: list[dict], ayar: dict) -> None:
        """
        Secilen sinyaller icin EMIR ADEDI — tek dokunuslu buton icin.

        ADET UYDURULMAZ. Uc girdi de gerekli:
          1. hesabin net likidite degeri (IBKR'den, taban para biriminde)
          2. `boyutlama.boyut()` -> portfoyun yuzde kaci (2N stop
             mesafesinden; IKINCI bir formul YAZILMIYOR)
          3. hesap para birimi -> enstruman para birimi kuru

        Uclusunden biri eksikse `adet` YAZILMAZ ve SEBEBI yazilir.
        Mesaj o zaman `<adet>` yer tutucusuna doner — hesaplanmis gibi
        gorunen bir sayi vermektense boslugu SOYLEMEK dogru.

        AG HATASI TARAMAYI DUSURMEZ: IBKR kapaliyken kirilim tablosu
        yine gitmeli, yalnizca butonu tasimadan.
        """
        if not secilen:
            return
        from .boyutlama import boyut

        netlik = pb = None
        try:
            from ..ibkr.istemci import Istemci
            from ..ibkr.oturum import Oturum
            from ..ibkr.portfoy import Portfoy
            istemci = Istemci(self.s.get("ibkr.taban_url", None))
            try:
                if Oturum(istemci).durumu_oku(zorla=True).kullanilabilir:
                    netlik, pb = Portfoy(istemci).toplam_netlik()
            finally:
                istemci.kapat()
        except Exception as e:                             # noqa: BLE001
            log.info("[strateji] hesap degeri okunamadi: %s", e)

        risk = float(ayar.get("risk_payi_pct") or 1.0)
        for g in secilen:
            if not netlik or netlik <= 0:
                g["adet_sebep"] = "hesap degeri okunamadi"
                continue
            b = boyut(g.get("giris"), g.get("stop"), risk)
            if not b:
                g["adet_sebep"] = "boyut hesaplanamadi"
                continue
            hedef_pb = (g.get("seviyeler") or {}).get("para_birimi")
            kur = 1.0
            if hedef_pb and pb and hedef_pb.upper() != pb.upper():
                k = self.db.fx_kuru(pb, hedef_pb)
                if not k:
                    # KUR YOKSA ADET YAZILMAZ. Kuru 1 varsaymak,
                    # EUR hesapta USD emri icin %15 yanlis boyut demek.
                    g["adet_sebep"] = f"{pb}->{hedef_pb} kuru yok"
                    continue
                kur = k["rate"]
            tutar = netlik * kur * float(b["pozisyon_payi_pct"]) / 100.0
            adet = round(tutar / float(g["giris"]), 4)
            if adet <= 0:
                # SIFIR ADET EMIR DEGILDIR. Hesap bu boyut icin cok
                # kucuk demektir ve bunu SOYLEMEK, 0 yazip IBKR'ye
                # reddettirmekten anlasilir.
                g["adet_sebep"] = (f"hesaba gore adet sifirin altinda "
                                   f"({tutar:.2f} {hedef_pb or ''})")
                continue
            g["adet"] = adet
            g["adet_tutar"] = round(tutar, 2)
            g["adet_pb"] = hedef_pb
        log.info("[strateji] adet hesabi: netlik=%s %s, %d/%d sinyalde adet var",
                 netlik, pb, sum(1 for g in secilen if g.get("adet")),
                 len(secilen))

    @staticmethod
    def _strateji_ozeti(v):
        """Donus degerine SAYILAR girer, 518 sembolluk govde girmez."""
        if not isinstance(v, dict):
            return v
        return {"taranan": v.get("taranan"),
                "kirilim": len(v.get("gorusler") or []),
                "secilen": [g.get("sembol") for g in (v.get("secilen") or [])],
                "sayaclar": v.get("sayaclar"),
                "defter": v.get("defter")}

    def _conid(self, sembol) -> str | None:
        if not sembol:
            return None
        r = self.db.query(
            """SELECT d.conid FROM instruments i
               JOIN identities d ON d.instrument_id = i.id
               WHERE UPPER(i.symbol) = ? AND d.conid IS NOT NULL
                 AND d.conid <> ''""", (str(sembol).upper(),))
        # BIRDEN COK CONID = BELIRSIZLIK, emirde kabul edilemez
        # (`emirakis._conid` de reddediyor). Belirsizi bos birakmak,
        # yanlis baglamaktan iyidir.
        return str(r[0]["conid"]) if len({x["conid"] for x in r}) == 1 else None

    def _kisisel_faz(self, sahip: str, kip: str, bildir: bool,
                     panel: bool, ortak: dict,
                     panel_payi: float = PANEL_SURE_BUTCESI_SN) -> dict:
        """
        Bir sahibin adimlari. ADIM BAZINDA KISMI BASARI.

        Deterministik adimlar (portfoy riski, tez kontrolu) ile LLM
        adimlari (panel, hakem) AYRI sarilir: panel patlarsa tez alarmi
        yine gitmeli — tez kontrolu modele hic bagli degil ve
        kullanicinin en cok isine yarayan cikti o.

        `panel_payi` — BU SAHIBIN panelinin duvar saati (saniye).
        `calistir` kalan butceyi kalan sahip sayisina bolerek veriyor.
        Varsayilan yalnizca dogrudan cagiran testler icin ve modul
        tavanina esit; gercek kosuda her zaman acikca geciliyor.
        """
        from .journal import Defter
        from .koruma import Koruma
        from ..llm import anlasilir_hata

        defter = Defter(self.db)

        # --- deterministik adimlar ---------------------------------------
        tarayici = ortak["tarayici"]
        portfoy = tarayici.portfoy_taramasi(sahip)
        if portfoy:
            tarayici.kaydet(portfoy, sahip)
        bozulan = defter.tez_kontrol(sahip)

        # KORUMA SEVIYELERI — LLM'siz, kenar kaniti GEREKTIRMEZ.
        #
        # Once bakim (kur/yukselt/yeniden kur), sonra kontrol. Sira
        # onemli: once kontrol edip sonra guncelleseydik, bugun yukselen
        # bir stop bugunun kapanisiyla kirilmis gorunebilirdi.
        #
        # Bakim ve kontrol AYRI SARILI: seviye hesabi bir kagitta
        # patlarsa kirilim kontrolu yine kosmali — bu katmanin varlik
        # sebebi tam olarak o kirilimi haber vermek.
        koruma, kirilan = Koruma(self.db), []
        try:
            k_rapor = koruma.guncelle(sahip)
            log.info("[%s/%s] koruma: %d kuruldu, %d yukseltildi, "
                     "%d yeniden kuruldu, %d atlandi", kip, sahip,
                     k_rapor["kurulan"], k_rapor["yukseltilen"],
                     k_rapor["yeniden_kurulan"], len(k_rapor["atlanan"]))
        except Exception as e:                        # noqa: BLE001
            log.exception("[%s/%s] koruma bakimi patladi", kip, sahip)
        try:
            kirilan = koruma.kontrol(sahip)
        except Exception as e:                        # noqa: BLE001
            log.exception("[%s/%s] koruma kontrolu patladi", kip, sahip)

        karne = defter.puanla(sahip)      # yalnizca karne; olcum ortakta

        sinyaller = list(ortak["sinyaller"]) + portfoy
        sinyaller.sort(key=lambda x: -x["guc"])
        guclu = [x for x in sinyaller if x["guc"] >= BILDIRIM_ESIGI]
        log.info("[%s/%s] %d sinyal (portfoy %d), tez %d",
                 kip, sahip, len(sinyaller), len(portfoy), len(bozulan))

        # --- ALARMLAR HER SEYDEN ONCE GIDER -----------------------------
        # Gonderilirse ozette TEKRARLANMAZ; gonderilemezse ozete kalir ve
        # damga da atilmaz, yani bir sonraki kosu yeniden dener.
        gitti = self._tez_teslim(sahip, kip, bozulan, defter, bildir)
        kalan_tez = [] if gitti else bozulan
        self._koruma_teslim(sahip, kip, kirilan, koruma, bildir)

        if not panel:
            return self._hafif(kip, bildir, sinyaller, guclu, bozulan,
                               karne, sahip, ozetteki_tez=kalan_tez)

        # RISK BILDIRIMI PANEL YOLUNDA DA VAR — onceden YOKTU.
        # `_yeni_riskler` (ve dolayisiyla `bildirim_durumu` bastirmasi)
        # yalnizca `_hafif` dalindaydi; `panel: true` yapilan an
        # yogunlasma/acik_zarar alarmlari TAMAMEN kaybolurdu.
        riskler = self._yeni_riskler(
            [x for x in sinyaller if x["tur"] in RISK_TURLERI], sahip,
            yaz=bildir)

        # --- LLM adimlari — AYRI sarili ----------------------------------
        # Panel patlasa da OZET GIDER: alarm bolumu yukarida, panelden
        # BAGIMSIZ hesaplandi. "Tez kontrolu modele hic bagli degil"
        # ilkesi, mesaj katmaninda da gecerli olmali.
        panel_notu, sonuc, n_tahmin, hakem_id = None, {}, 0, None
        teknik_ariza: dict | None = None
        # HABER DE PANELI TETIKLER. Onceden kapi yalnizca `guclu`ydu:
        # fiyat esigi gecilmediginde panel kosmuyordu ve kullaniciya o
        # gun hicbir yorum gitmiyordu. Fiyat esikleri (dogru olarak)
        # siki — 2026-08-20 backtest'i o sinyallerin 24 hucresinin
        # 22'sinde sifirdan ayirt edilemedigini gosterdi — ama haber her
        # gun var ve kademe 1-2 haberi olan bir kagit yorumu HAK EDER.
        if not guclu and not self._haber_var():
            log.info("[%s/%s] ne sinyal ne kademe 1-2 haber — panel kosmadi",
                     kip, sahip)
            panel_notu = ("Panel: esigi gecen sinyal ve kademe 1-2 haber yok, "
                          "model calistirilmadi.")
        else:
            try:
                sonuc, n_tahmin, hakem_id = self._panel_fazi(
                    sahip, kip, guclu, defter, panel_payi)
            except Exception as e:                    # noqa: BLE001
                log.exception("[%s/%s] panel patladi", kip, sahip)
                # TEKNIK ARIZA PIYASA NOTUNUN ICINE GOMULMEZ.
                #
                # Onceden buradaki uzun teshis metni ozet mesajin
                # govdesine `🧠 <i>…</i>` diye ekleniyordu ve piyasa
                # satirlarinin arasinda KAYBOLUYORDU. Olculdu
                # 2026-08-24: kullanici mesaji okudu, arizayi ancak
                # sorunca fark etti. Bir sistem arizasi ile bir piyasa
                # gozlemi ayni tipografiyle sunulursa, ikincisi
                # birincisini gizler.
                #
                # Ozette KISA bir isaret kaliyor (kullanici panelin
                # neden bos oldugunu orada gorsun), AYRINTI ayri
                # mesajda.
                teknik_ariza = {
                    "baslik": "Model paneli calismadi",
                    "nerede": f"{kip} kosusu",
                    "ham": _kirp(e, 300),
                    "teshis": _kirp(anlasilir_hata(e, self.s), 400),
                }
                panel_notu = ("🧠 Panel calismadi — ayrintisi ayri mesajda. "
                              "Tez alarmi ve portfoy riski ETKILENMEDI.")
            else:
                # KESILEN AJAN SESSIZ KALMAZ. Yarim bir panel, tam bir
                # panel gibi okunursa kullanici olmayan bir kapsamli
                # degerlendirmeye guvenir.
                kesilen = sonuc.get("kesilen") or []
                if kesilen:
                    # AJAN ADLARI KULLANICIYA BIR SEY SOYLEMIYOR.
                    # "risk, teknik, olay, temel, hakem" bir IC MIMARI
                    # listesi; kullanici 2026-08-21'de "bunlar ne
                    # anlama geliyor" diye sordu. Onemli olan hangi
                    # parcanin adi degil, NE KAYBEDILDIGI.
                    from .agents import AJANLAR
                    hepsi = len(kesilen) > len(AJANLAR)
                    panel_notu = (
                        ("🧠 Model yorumu bu kosuda URETILEMEDI"
                         if hepsi else "🧠 Model yorumu EKSIK kaldi")
                        + f" — analiz icin ayrilan {panel_payi / 60:.0f} "
                        "dakika doldu. Yukaridaki fiyat, alarm ve portfoy "
                        "bilgileri BUNDAN ETKILENMEDI: onlar olcumle "
                        "uretiliyor, modelle degil.")

        if bildir:
            self._ozet_bildir(kip, sahip, bozulan=kalan_tez, riskler=riskler,
                              sade=sonuc.get("sade"), ozet=sonuc.get("ozet"),
                              karne=karne, n_tahmin=n_tahmin,
                              hakem_id=hakem_id, panel_notu=panel_notu,
                              taktikler=sonuc.get("taktikler"))
            # ARIZA PIYASA NOTUNDAN SONRA VE AYRI. Once ne oldugu
            # (piyasa), sonra neyin bozuldugu (sistem) — okuma sirasi
            # onem sirasiyla ayni, ama IKI mesaj oldugu icin ariza
            # gurultuye karismiyor.
            if teknik_ariza:
                self._teknik_ariza_bildir(sahip, teknik_ariza)

        cikti = {"sinyal": len(sinyaller), "guclu": len(guclu),
                 "karne": karne, "ozet": sonuc.get("ozet"),
                 "tahmin": n_tahmin, "tez_bozuldu": len(bozulan),
                 "risk": len(riskler), "ajanlar": sonuc.get("ajanlar", {})}
        if panel_notu and guclu:
            cikti["panel_hatasi"] = panel_notu
        return cikti

    def _tez_teslim(self, sahip: str, kip: str, bozulan: list[dict],
                    defter, bildir: bool) -> bool:
        """
        Tez alarmini PANELDEN ONCE gonderir, sonra damgalar.

        SIRA SOZLESMESI: tespit -> TESLIMAT -> damga. Onceki sirada
        (tespit -> damga -> ... -> teslimat) arada olen bir kosu alarmi
        KALICI olarak yutuyordu, cunku `tez_kontrol` damgalanmis satiri
        bir daha getirmiyor. Olculdu 2026-08-21: ROSE'un tezi 08:07:15'te
        bozuldu, damga yazildi, kosu 08:25:01'de oldurruldu ve o alarm
        artik hicbir kosuda cikmayacakti.

        AYRI MESAJ, bilerek. Ozetin bir satiri olarak kalsaydi panelin
        arkasinda beklemek zorundaydi — duzeltmeye calistigimiz seyin ta
        kendisi. Gunde dort mesaj sozu bozulmuyor: tez bozulmasi NADIR
        bir olay (tasarim geregi, bkz. `journal.tez_kontrol`), her kosuda
        degil.

        Teslim edilemezse (ag, blok) damga ATILMAZ ve `False` doner:
        alarm ozete kalir ve bir sonraki kosu yeniden dener. En kotu
        ihtimalle ayni alarm iki kez gider; kaybolmaz.
        """
        if not bozulan:
            return False
        if not bildir:
            # `--no-notify` bir OLCUM kosusudur: gonderilmeyen alarm
            # damgalanirsa gercek kosu onu bir daha gormez.
            log.info("[%s/%s] bildirim kapali — tez alarmi damgalanmadi "
                     "(%d kayit bekliyor)", kip, sahip, len(bozulan))
            return False

        L = [f"🔔 <b>{self.KOSU_ADI.get(kip, kip)} · tez alarmi</b>"]
        for b in bozulan:
            L.append(f"\n<b>{_esc(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {_esc(_kirp(b['tez'], 200))}</i>")
            L.append("Önceden yazılan koşul: <b>"
                     + _esc(str(_kosul_okunabilir(b["kosul"]))) + "</b>")
            L.append(f"Şu anki {_esc(_alan_adi(b['alan']))}: "
                     f"<b>{_fiyat_tr(b['deger'])}</b>")
        L.append("\n<i>Bu bir al/sat tavsiyesi degil: daha once ACIKCA "
                 "yazilmis bir esigin gerceklestigi bildiriliyor.</i>")

        if not self._sahibe_bildir(sahip, "\n".join(L), kaynak=kip):
            log.error("[%s/%s] TEZ ALARMI GONDERILEMEDI — damga atilmadi, "
                      "sonraki kosu yeniden deneyecek: %s", kip, sahip,
                      [b.get("sembol") for b in bozulan])
            return False
        defter.tez_damgala(bozulan)
        return True

    def _koruma_teslim(self, sahip: str, kip: str, kirilan: list[dict],
                       koruma, bildir: bool) -> bool:
        """
        Koruma seviyesi kirilimini PANELDEN ONCE gonderir, sonra damgalar.

        Tez alarmiyla AYNI sira sozlesmesi ve ayni gerekce: tespit ->
        TESLIMAT -> damga. 2026-08-21 sabahinda damga once atildigi icin
        ROSE'un alarmi kalici olarak kaybolmustu.

        AYRI MESAJ: bir stop kirilimi, gunun ozetinin arkasinda
        beklemesi gereken bir sey degil — kullanicinin bilmek istedigi
        an, kirildigi andir.
        """
        if not kirilan or not bildir:
            if kirilan:
                log.info("[%s/%s] bildirim kapali — koruma kirilimi "
                         "damgalanmadi (%d kayit bekliyor)",
                         kip, sahip, len(kirilan))
            return False

        L = [f"🛡 <b>{self.KOSU_ADI.get(kip, kip)} · koruma seviyesi kirildi</b>"]
        for k in kirilan:
            pb = k.get("para_birimi") or ""
            L.append(f"\n<b>{_esc(k['sembol'])}</b> "
                     f"({_esc(str(k['hesap']).upper())})")
            L.append(f"Kapanis <b>{_kisa(k['kapanis'])} {_esc(pb)}</b> · "
                     f"stop <code>{_kisa(k['stop'])}</code> "
                     f"({k['mesafe_pct']:+.1f}%)")
            L.append(f"<i>Seviye {str(k['kuruldu_ts'])[:10]} tarihinde "
                     f"kuruldu; 2N = {_kisa(2 * k['n'])} {_esc(pb)} "
                     f"(20 gunluk ortalama gunluk salinimin iki kati).</i>")
        L.append("\n<i>Bu bir SATIS TAVSIYESI DEGIL: onceden olculmus bir "
                 "esigin gerceklestigi bildiriliyor. Sistem emir gondermez. "
                 "Seviye kirildiktan sonra bu pozisyon icin koruma KAPALI — "
                 "fiyat esigin ustune donerse yeniden kurulur.</i>")

        if not self._sahibe_bildir(sahip, "\n".join(L), kaynak=kip):
            log.error("[%s/%s] KORUMA ALARMI GONDERILEMEDI — damga "
                      "atilmadi, sonraki kosu yeniden deneyecek: %s",
                      kip, sahip, [k["sembol"] for k in kirilan])
            return False
        koruma.damgala(kirilan)
        return True

    def _haber_var(self, gun: int = 2) -> bool:
        """Son `gun` gunde kanit seviyesinde (kademe 1-2) haber var mi?"""
        try:
            return bool(self.db.query(
                """SELECT 1 FROM news WHERE tier IN (1,2)
                   AND published_at > datetime('now', ?) LIMIT 1""",
                (f"-{gun} days",)))
        except Exception as e:                        # noqa: BLE001
            log.debug("[nabiz] haber kontrolu yapilamadi: %s", e)
            return False

    def _panel_fazi(self, sahip, kip, guclu, defter,
                    panel_payi: float) -> tuple:
        """
        Paneli kosturur ve deftere yazar. MESAJ GONDERMEZ.

        Gonderim `_kisisel_faz`'a tasindi: panel patlasa bile ozet
        gitmeli ve alarm bolumu panelden BAGIMSIZ hesaplanmali.
        Doner: (panel sonucu, yazilan tahmin sayisi, hakem satir id'si)

        `panel_payi` ZORUNLU ve VARSAYILANI YOK: bu paneli kimin ne kadar
        surede kesecegi cagiranin acik karari olmali. Varsayilan
        konsaydi, 2026-08-21'de oldugu gibi sinirsiz kosan bir panel yine
        mumkun olurdu.
        """
        import anyio
        from .agents import Panel

        gundem = self._gundem(guclu, sahip)
        # HABER DOSYASI DA GUNDEME GIRER. Fiyat esikleri (dogru olarak)
        # siki ve cogu gun gecilmiyor; haber ise HER GUN var. Paneli
        # yalnizca fiyat sinyaline baglamak, sessiz gunlerde kullaniciya
        # hicbir sey gitmemesi demekti. Hata YUTULUYOR: dosya
        # derlenemezse panel eski haliyle kosar — bir haber hatasinin
        # tum paneli dusurmesi, kazanci goturur.
        haber = None
        try:
            from ..analysis.haber_ilgi import haber_dosyasi
            haber = haber_dosyasi(self.db, sahip=sahip, pencere_gun=2)
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] haber dosyasi derlenemedi: %s", e)
        sonuc = anyio.run(
            lambda: Panel(self.s, self.db, sahip,
                          sure_siniri_sn=panel_payi).calistir(gundem, haber))

        # Hakemin cagrisi AYRICA kaydedilir: kullanicinin OKUDUGU sey odur.
        rapor = defter.kaydet(sonuc.get("gorusler") or [], sahip)
        hakem_rapor = defter.kaydet(sonuc.get("hakem_gorusler") or [], sahip)
        n_tahmin = rapor["yazilan"] + hakem_rapor["yazilan"]
        log.info("[%s/%s] tahmin: ajanlar %s · hakem %s",
                 kip, sahip, rapor, hakem_rapor)
        self._atilanlari_isle(rapor, hakem_rapor,
                              sonuc.get("panel_idleri") or {})
        hakem_id = (sonuc.get("panel_idleri") or {}).get("hakem")
        return sonuc, n_tahmin, hakem_id

    def _hata_metni(self, kip: str, e: Exception) -> str:
        from ..llm import anlasilir_hata
        return (f"🔴 <b>{kip} kosusu patladi</b>\n\n"
                f"<i>{_esc(_kirp(anlasilir_hata(e, self.s), 400))}</i>")

    # ------------------------------------------------------------------
    # BILDIRIM YONLENDIRME — tek dogruluk kaynagi `telegram.sahipler`.
    #
    # Ikinci bir yonlendirme ayari ACILMADI: iki liste kacinilmaz olarak
    # ayrisir ve "kosu calisti ama mesaj kimseye gitmedi" durumunu
    # uretir. Yetkilendirme ve yonlendirme AYNI esleme.
    # ------------------------------------------------------------------
    def _sahibe_bildir(self, sahip: str, metin: str,
                       reply_markup: dict | None = None,
                       kaynak: str | None = None) -> bool:
        """
        Bir sahibin TUM sohbetlerine gonderir. Doner: en az biri gitti mi.

        Gonderim basarisizligi (ag, blok, gecersiz chat_id) DIGER sahibi
        etkilemez; yalnizca loglanir ve donus degerine yansir.

        `reply_markup` yalnizca ILK sohbete konur: buton bir SATIR ID'si
        tasiyor ve ayni id'yi birden cok sohbete koymak, ikinci sohbetin
        de ayni teknik detayi acmasi demek — sahip ayni oldugu icin
        yetki sorunu degil ama tekrar eden buton gurultudur.

        `kaynak` — VERILIRSE mesaj sohbet arsivine de yazilir.

        NEDEN VARSAYILANI None (yani "arsivleme"): bu yoldan iki AYRI
        sinif mesaj geciyor. ANALIZ (sabah ozeti, koruma alarmi, tez
        alarmi) hafizaya ait — model kendi soyledigini hatirlamali.
        SISTEM UYARISI (kosu hatasi, teknik ariza) ait DEGIL: arsive
        girerse "gecen hafta ne konustuk" sorusunun cevabi bakim
        mesajlarina doner. Ayrimi cagiran yapiyor cunku burada
        anlasilamaz.

        ARSIV TESLIMATTAN SONRA: `giden` yanlissa hicbir sey yazilmaz.
        Gonderilmemis bir mesaji "soyledim" diye kaydetmek, bu projenin
        en kotu hata sinifi — model sonraki turda Ali'nin hic gormedigi
        bir cumleye atifta bulunurdu.
        """
        from ..notify import TelegramNotifier

        chatler = self.s.sahip_chatleri(sahip)
        if not chatler:
            # Kosup bildirimi kaybetmek, hic kosmamaktan KOTU: LLM
            # butcesi harcanir, cikti kimseye gitmez.
            log.error("[bildirim] '%s' sahibinin chat_id'si eslemede YOK — "
                      "mesaj gonderilemedi", sahip)
            return False
        tg = TelegramNotifier(self.s)
        giden = False
        for i, chat in enumerate(chatler):
            try:
                giden = tg.send_message(
                    metin, chat_id=chat,
                    reply_markup=reply_markup if i == 0 else None) or giden
            except Exception as e:                    # noqa: BLE001
                log.warning("[bildirim] %s/%s gonderilemedi: %s",
                            sahip, chat, e)
        if giden and kaynak:
            arsivle(self.db, chatler[0], sahip, metin, kaynak)
        return giden

    def _herkese_bildir(self, metin: str) -> None:
        """Sistem olaylari (ortak faz hatasi, kesinti) — TUM sahiplere."""
        for sahip in self.s.sahip_listesi:
            self._sahibe_bildir(sahip, metin)

    # Teknik ariza mesajinin GORSEL IMZASI. Piyasa notu emoji + tablo
    # gibi okunur; ariza mesaji BLOK gibi okunmali ki goz onu ayirsin.
    ARIZA_CIZGI = "━━━━━━━━━━━━━━━━━━━━"

    def teknik_ariza_metni(self, ariza: dict) -> str:
        """
        Teknik ariza mesajini kurar — PIYASA NOTUNDAN AYRI BICIMDE.

        NEDEN AYRI MESAJ VE AYRI BICIM (Ali istedi, 2026-08-24):
        ariza metni ozet mesajin govdesine `🧠 <i>…</i>` diye
        ekleniyordu ve piyasa satirlarinin arasinda kayboluyordu. Bir
        SISTEM arizasi ile bir PIYASA gozlemi ayni tipografiyle
        sunulursa ikincisi birincisini gizler.

        Telegram'da renk yok; ayrimi UC sey tasiyor: kirmizi daire,
        yatay cizgi ve "bu piyasa notu DEGIL" cumlesi.

        DORT BASLIK, ve ucu bu deponun tekrar eden dersinden:
          NE OLDU        — olgu
          HAM HATA       — <code> icinde, YORUMSUZ
          ETKILENMEYEN   — "her sey bozuldu" panigini onler
          NE YAPMALI     — teshis; BILINMIYORSA bilinmedigini soyler
        """
        L = [f"🔴 <b>TEKNIK ARIZA</b>", self.ARIZA_CIZGI,
             "<i>Bu mesaj piyasa notu DEGIL — sistemin kendi arizasi.</i>", ""]
        L.append(f"<b>NE OLDU</b>\n{_esc(ariza['baslik'])}"
                 + (f" ({_esc(ariza['nerede'])})" if ariza.get("nerede") else ""))
        if ariza.get("ham"):
            L.append(f"\n<b>HAM HATA</b>\n<code>{_esc(ariza['ham'])}</code>")
        etkilenen = ariza.get("etkilenen") or ["Model yorumu uretilmedi"]
        L.append("\n<b>ETKILENEN</b>\n"
                 + "\n".join(f"• {_esc(x)}" for x in etkilenen))
        # ETKILENMEYEN LISTESI SABIT DEGIL, GEREKCELI: bu satirlar
        # olcumle uretiliyor ve modele HIC bagli degil. Kullanicinin
        # "her sey coktu mu" sorusunu pesinen cevapliyor.
        etkilenmeyen = ariza.get("etkilenmeyen") or [
            "Tez alarmi ve portfoy riski",
            "Fiyat, haber ve bilanco toplama",
            "Karne (isabet olcumu)"]
        L.append("\n<b>ETKILENMEYEN</b>\n"
                 + "\n".join(f"• {_esc(x)}" for x in etkilenmeyen)
                 + "\n<i>Bunlar olcumle uretiliyor, modelle degil.</i>")
        if ariza.get("teshis"):
            L.append(f"\n<b>NE YAPMALI</b>\n{_esc(ariza['teshis'])}")
        return "\n".join(L)

    def _teknik_ariza_bildir(self, sahip: str, ariza: dict) -> None:
        try:
            self._sahibe_bildir(sahip, self.teknik_ariza_metni(ariza))
        except Exception as e:                        # noqa: BLE001
            # ARIZA MESAJI PATLARSA KOSU DUSMEZ — ama sessiz de kalmaz.
            # Bir hata bildirimini bildirememek, hatanin kendisinden
            # daha sinsi bir sessizlik uretir.
            log.error("[bildirim] teknik ariza mesaji gonderilemedi: %s", e)

    # ------------------------------------------------------------------
    def _hafif(self, kip, bildir, sinyaller, guclu, bozulan, karne,
               sahip: str | None = None,
               ozetteki_tez: list[dict] | None = None) -> dict:
        """
        HAFIF KIP — LLM YOK.

        `ozetteki_tez` — ozet mesajinda GOSTERILECEK tez alarmlari.
        `bozulan` sayim icin (kac tez bozuldu), `ozetteki_tez` gosterim
        icin: alarm zaten ayri bir mesajla gittiyse burasi BOS gelir ve
        ayni sey iki kez yazilmaz. Verilmezse `bozulan` kullanilir —
        eski davranis, dogrudan cagiran testler icin.

        Sabah ve oglen kosulari icin. Icerik yoruma ihtiyac duymuyor:
        "ROSE gunluk oynakliginin 2,8 kati dustu, hacim teyitli, portfoy
        agirligin %18" cumlesi deterministik ve TAM. Modelden gecirmek
        onu daha dogru yapmaz, yalnizca daha uzun yapar ve butceyi uce
        katlar. Projenin kurucu ayriminin devami: deterministik katman
        hesaplar, LLM yorumlar; yorumlanacak bir sey yoksa cagrilmaz.

        BILDIRIM ESIGI DAHA DAR: yalnizca SAHIP OLUNAN enstrumanlar.
        Sabah 09:30'da BIST'te bir kagidin hareket etmesi, uzerinde
        pozisyonun yoksa acil degil ve aksam paneli zaten bakacak;
        portfoyunde bir sey olmasi acildir.
        """
        sahibin = self.db.sahip_pozisyon_idleri(sahip) if sahip else set()
        # RISKLER SINYAL LISTESINE GIRMEZ. Ikisi de `sinyaller` icinden
        # geliyor ve `guclu` filtresi turu ayirt etmiyordu: `yogunlasma`
        # ve `acik_zarar` hem madde listesine hem ⚠️ risk bolumune
        # dusuyordu — AYNI SEY IKI KEZ. Gruplama bunu gorunur yapti:
        # USDT/TRALT/ASML/NOW kanit satiri olmayan bos bloklar olarak
        # cikti, cunku bu turlerin bar bazli bir kaniti yok.
        portfoyde = [x for x in guclu
                     if x.get("instrument_id") in sahibin
                     and x.get("tur") not in RISK_TURLERI]

        # IKI SUZGEC, IKI AYRI SORU — sirasi onemli:
        #   1. TAZE MI?   Eski bir olayin etkisi bugunun haberi degildir.
        #   2. YENI MI?   Ayni barin ayni sinyali iki kez bildirilmez.
        # Once tazelik: bayat bir sinyali "yeni" diye kaydedip sonra
        # elemek, bastirma tablosuna hic bildirilmemis bir satir yazardi.
        taze, bayat = self._taze_sinyaller(portfoyde)
        # `yaz=bildir`: bildirim gitmiyorsa "bildirildi" isareti de
        # konmaz — yoksa `--no-notify` ile yapilan bir olcum kosusu bir
        # sonraki GERCEK kosuyu susturur.
        portfoy_sinyali = self._yeni_sinyaller(taze, sahip, yaz=bildir)
        riskler = self._yeni_riskler(
            [x for x in sinyaller if x["tur"] in RISK_TURLERI], sahip,
            yaz=bildir)

        log.info("[%s] hafif kip: %d sinyal, portfoyde %d (bayat %d, tekrar "
                 "%d, bildirilecek %d), risk %d, tez %d",
                 kip, len(sinyaller), len(portfoyde), len(bayat),
                 len(taze) - len(portfoy_sinyali), len(portfoy_sinyali),
                 len(riskler), len(bozulan))

        gosterilecek_tez = bozulan if ozetteki_tez is None else ozetteki_tez
        if bildir and (gosterilecek_tez or portfoy_sinyali or riskler):
            self._hafif_bildir(kip, gosterilecek_tez, portfoy_sinyali, riskler,
                               sahip)
        elif bildir:
            # SESSIZLIK GECERLI CIKTI. "Bugun bir sey olmadi" mesaji
            # gondermek, bildirimin degerini asindiran seydir.
            log.info("[%s] kriter saglanmadi — mesaj YOK", kip)

        return {"kip": kip, "sinyal": len(sinyaller), "guclu": len(guclu),
                "portfoy_sinyali": len(portfoy_sinyali),
                "risk": len(riskler), "tez_bozuldu": len(bozulan),
                "karne": karne, "ozet": None, "tahmin": 0}

    # Risk bildiriminin tekrari icin esik, YUZDE PUANI.
    #
    # Neden oynakliga gore OLCEKLENMIYOR (projenin her yerdeki
    # disiplininin aksine): bu iki deger de PORTFOY ANLIK GORUNTUSUNDEN
    # geliyor — `yogunlasma` pozisyon degerlerinden, `acik_zarar`
    # `pnl_pct` alanindan. Ikisi de yalnizca YENI EKRAN GORUNTUSU
    # geldiginde degisir; arada BASAMAK FONKSIYONUDUR, gunluk fiyat
    # oynakligiyla suruklenmez. Dolayisiyla asil is tekillestirmede;
    # esik yalnizca goruntuden goruntuye onemsiz farklarda tekrar
    # bildirimi engelliyor. Oynakliga gore olcekleme burada olmayan
    # bir hareketi modellemek olurdu.
    RISK_TEKRAR_ESIGI = 3.0

    def _yeni_riskler(self, riskler: list[dict], sahip: str,
                      yaz: bool = True) -> list[dict]:
        """
        Yalnizca DURUMU DEGISEN riskleri dondurur.

        Portfoy riski bir olay degil DURUMDUR: ASML portfoyun %40'iysa
        bu bugun de yarin da dogru. Bastirma olmadan gunde iki hafif
        kosu ayni cumleyi tekrarlar ve kullanici bildirimleri kapatir.
        Tez alarmindaki `tez_bozuldu_ts` ile ayni problem.

        `yaz=False` ise SONUC AYNI ama BASTIRMA TABLOSUNA DOKUNULMAZ.
        Gerekce olculdu: `--no-notify` kosulari (kip suresi olcumu,
        elle deneme, test) tabloyu dolduruyordu ve BIR SONRAKI GERCEK
        kosu o kayitlar yuzunden susuyordu. "Bildirildi" isareti ancak
        mesaj GERCEKTEN gittiginde konmali.
        """
        if not riskler:
            return []
        # SAHIBE GORE SUZ. Sahipsiz okuma, A'nin bastirma satirini B'nin
        # riski sanip B'yi susturuyordu — tablo tam da bunu engellemek
        # icin var.
        onceki = {(r["instrument_id"], r["tur"]): r["son_deger"]
                  for r in self.db.query(
                      "SELECT instrument_id, tur, son_deger FROM "
                      "bildirim_durumu WHERE sahip = ?", (sahip,))}
        yeni, yazilacak = [], []
        for r in riskler:
            deger = self._risk_degeri(r)
            if deger is None:
                continue
            anahtar = (r["instrument_id"], r["tur"])
            eski_deger = onceki.get(anahtar)
            if eski_deger is not None and \
                    abs(deger - eski_deger) < self.RISK_TEKRAR_ESIGI:
                continue                     # durum degismedi, SUS
            yeni.append(r)
            yazilacak.append((r["instrument_id"], r["tur"], deger))
        if yazilacak and yaz:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO bildirim_durumu
                       (sahip, instrument_id, tur, son_deger, son_bildirim_ts)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(sahip, instrument_id, tur) DO UPDATE SET
                         son_deger = excluded.son_deger,
                         son_bildirim_ts = excluded.son_bildirim_ts""",
                    [(sahip, i, tur, d, ts) for i, tur, d in yazilacak])
        if len(riskler) != len(yeni):
            log.info("[nabiz] risk bildirimi bastirildi: %d/%d degismemis",
                     len(riskler) - len(yeni), len(riskler))
        return yeni

    @staticmethod
    def _risk_degeri(r: dict) -> float | None:
        """Riskin izlenen SAYISI — turu belirler."""
        k = r.get("kanit") or {}
        return k.get("agirlik_%") if r["tur"] == "yogunlasma" else k.get("kz_%")

    # ------------------------------------------------------------------
    # BILDIRIM SUZGECLERI
    # ------------------------------------------------------------------
    # Bildirime girecek olay etkisi EN FAZLA bu kadar eski olabilir.
    #
    # `analysis/events.py::haber_etkileri` 120 GUNLUK pencereye bakiyor
    # ve bu ANALIZ icin dogru: "bu kagit haberlere nasil tepki veriyor"
    # sorusunun cevabi uzun gecmis ister. BILDIRIM baska bir sey soyler —
    # "su an dikkat et". 2026-08-19'da 30 Temmuz'daki AMZN olayi 20
    # gundur her kosuda bildirime dusuyordu ve tarihi de yazilmadigi icin
    # BUGUNUN haberi gibi okunuyordu.
    #
    # 3 GUN: bir olayin fiyata yansimasi icin olculen pencere zaten
    # t+1..t+3 (bkz. `analysis/events.py` olay penceresi). Bundan
    # eskisinde "su an dikkat et" demenin dayanagi kalmiyor.
    BILDIRIM_OLAY_AZAMI_GUN = 3

    def _taze_sinyaller(self, sinyaller: list[dict]) -> tuple[list, list]:
        """
        (bildirilebilir, bayat) — YASI OLCULEBILEN ve gecmis sinyalleri ayirir.

        Yalnizca `olay_etkisi` yaslanir: digerleri zaten SON BARIN
        olayidir, yasi barin kendisidir. Yasi OLCULEMEYEN olay
        (`olay_gun_once` None) bayat sayilir — bilinmeyen bir tarihi
        "taze" varsaymak, tam da bu hatanin kaynagiydi.
        """
        taze, bayat = [], []
        for s in sinyaller:
            if s.get("tur") != "olay_etkisi":
                taze.append(s)
                continue
            gun = (s.get("kanit") or {}).get("olay_gun_once")
            if gun is None or gun > self.BILDIRIM_OLAY_AZAMI_GUN:
                bayat.append(s)
                log.info("[nabiz] %s olay_etkisi bildirilmedi: olay %s "
                         "(%s gun once)", s.get("sembol"),
                         (s.get("kanit") or {}).get("olay_tarihi"), gun)
            else:
                taze.append(s)
        return taze, bayat

    # Sinyal turu -> (izlenen kanit alani, anahtara giren kimlik alani)
    #
    # ANAHTAR NEDEN TARIH ICERIYOR: bu sinyaller DURUM degil OLAYDIR ve
    # kimlikleri sayilari degil, ait olduklari bardir. Yalnizca degere
    # bakan bir bastirma su hatayi yapardi: AVTX bugun -%19,91 dustu
    # (bildirildi), yarin -%19,50 daha duser (fark 0,41 puan, esigin
    # altinda) ve IKINCI COKUS SUSTURULURDU. Tarih anahtarda oldugu icin
    # yeni bar = yeni satir = yeni bildirim.
    SINYAL_IZLEME = {
        "olagandisi_hareket": ("gunluk_getiri_%", "bar_ts"),
        "hacim_anomalisi":    ("hacim_kati",      "bar_ts"),
        "sma50_kirilimi":     ("kapanis",         "bar_ts"),
        "rsi_ucu":            ("rsi14",           "bar_ts"),
        "olay_etkisi":        ("car_%",           "olay_tarihi"),
    }

    def _sinyal_anahtari(self, s: dict) -> str | None:
        """`bildirim_durumu.tur` sutununa yazilacak anahtar."""
        alanlar = self.SINYAL_IZLEME.get(s.get("tur"))
        if not alanlar:
            return None
        _, kimlik_alani = alanlar
        kimlik = (s.get(kimlik_alani)
                  or (s.get("kanit") or {}).get(kimlik_alani))
        if not kimlik:
            # KIMLIKSIZ SINYAL BASTIRILMAZ. Sabit bir anahtar uydurmak,
            # farkli barlarin sinyallerini ayni satira yazip ikincisini
            # susturmak olurdu.
            return None
        # `sinyal:` oneki ZORUNLU: ayni tablo `yogunlasma`/`acik_zarar`
        # risk satirlarini da tutuyor ve anahtar uzaylari karismamali.
        return f"sinyal:{s['tur']}:{kimlik}"

    def _sinyal_degeri(self, s: dict) -> float | None:
        alanlar = self.SINYAL_IZLEME.get(s.get("tur"))
        if not alanlar:
            return None
        deger = (s.get("kanit") or {}).get(alanlar[0])
        return float(deger) if isinstance(deger, (int, float)) else None

    def _sinyal_esigi(self, s: dict) -> float:
        """
        AYNI anahtar icinde yeniden bildirim icin gereken degisim.

        Anahtar tarihi icerdigi icin bu esik yalnizca GUN ICI surukleniye
        bakar: sabah kismi bar (-%5), aksam tam bar (-%19,91). Ikincisi
        gercekten yeni bilgidir ve bildirilmelidir.

        Esikler turun KENDI biriminde; ortak bir sayi yok, cunku "5"
        yuzde puaninda buyuk, hacim katinda kucuk, RSI'da ortadir.
        """
        tur = s.get("tur")
        if tur == "olagandisi_hareket":
            # Enstrumanin KENDI oynakligi: %1'lik kayma USDTRY'de buyuk,
            # bir memecoin'de gurultudur. Taban 0,5 puan — oynakligi
            # sifira yakin bir seride her kirinti bildirim uretmesin.
            return max(0.5, float(s.get("gunluk_oynaklik_%") or 0))
        if tur == "hacim_anomalisi":
            # Goreli: 2x -> 3x haberdir, 11x -> 12x degildir.
            olcek = abs(self._sinyal_degeri(s) or 1.0)
            return max(0.5, 0.5 * olcek)
        if tur == "rsi_ucu":
            return 5.0
        if tur == "olay_etkisi":
            return 2.0
        if tur == "sma50_kirilimi":
            # KIRILIM BIR ANDIR, seviye degil. Ayni barda "daha cok
            # kirildi" diye bir sey yok; fiyat oynadi diye tekrar
            # bildirmek yanlis olur.
            return float("inf")
        return float("inf")

    def _yeni_sinyaller(self, sinyaller: list[dict], sahip: str,
                        yaz: bool = True) -> list[dict]:
        """
        Yalnizca DAHA ONCE BILDIRILMEMIS (ya da anlamli degismis) sinyaller.

        `_yeni_riskler` ile ayni tabloyu ve ayni gerekceyi paylasiyor;
        fark, riskin bir DURUM, sinyalin bir OLAY olmasi — o yuzden
        anahtar tarih iceriyor (bkz. `SINYAL_IZLEME`).

        Bu suzgec yoktu: 2026-08-19'da sabah 09:31 kosusu portfoyde 10
        sinyal bildirdi, aksam 18:14 kosusu ayni gunun barlarindan 16
        sinyal bildirdi. Ali ayni gun ayni haberi iki kez aldi.

        `yaz=False` ise SONUC AYNI ama BASTIRMA TABLOSUNA DOKUNULMAZ.
        Gerekce olculdu: `--no-notify` kosulari (kip suresi olcumu,
        elle deneme, test) tabloyu dolduruyordu ve BIR SONRAKI GERCEK
        kosu o kayitlar yuzunden susuyordu. "Bildirildi" isareti ancak
        mesaj GERCEKTEN gittiginde konmali.
        """
        if not sinyaller:
            return []
        onceki = {(r["instrument_id"], r["tur"]): r["son_deger"]
                  for r in self.db.query(
                      "SELECT instrument_id, tur, son_deger FROM "
                      "bildirim_durumu WHERE sahip = ?", (sahip,))}
        yeni, yazilacak = [], []
        for s in sinyaller:
            anahtar = self._sinyal_anahtari(s)
            deger = self._sinyal_degeri(s)
            if anahtar is None or deger is None:
                # BASTIRILAMAYAN SINYAL BILDIRILIR. Suzgecin bilmedigi
                # bir tur eklendiginde sessizlik degil GURULTU olsun:
                # eksik bildirim, tekrar bildirimden pahalidir.
                yeni.append(s)
                log.info("[nabiz] %s/%s bastirma disi (anahtar/deger yok)",
                         s.get("sembol"), s.get("tur"))
                continue
            eski = onceki.get((s["instrument_id"], anahtar))
            if eski is not None and abs(deger - eski) < self._sinyal_esigi(s):
                continue
            yeni.append(s)
            yazilacak.append((s["instrument_id"], anahtar, deger))
        if yazilacak and yaz:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO bildirim_durumu
                       (sahip, instrument_id, tur, son_deger, son_bildirim_ts)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(sahip, instrument_id, tur) DO UPDATE SET
                         son_deger = excluded.son_deger,
                         son_bildirim_ts = excluded.son_bildirim_ts""",
                    [(sahip, i, t, d, ts) for i, t, d in yazilacak])
        return yeni

    # Koşunun ADI — piyasa durumu HAKKINDA HICBIR IDDIA TASIMAZ.
    #
    # Eskiden "ogle" -> "🕕 Kapanis"ti ve bu, 18:00 slotunun takma
    # adiydi. Mesaj uc ayri borsadan enstruman tasidigi icin baslik
    # listenin ilk satiri (AMZN, ABD seansi ACIK) icin YANLIS bir durum
    # ilan ediyordu. Buradaki adlar artik yalnizca KOSUNUN AMACINI
    # soyluyor (hangi kaynaklar icin zamanlandigini); piyasalarin
    # gercek durumu bir alt satirda OLCULEREK yaziliyor.
    # Ritim v2 saatleri (Europe/Amsterdam): sabah 08:00, ogle 12:30,
    # kapanis 17:45, nabiz 22:15. Adlar KOSUNUN AMACINI soyluyor,
    # piyasa durumunu DEGIL — durum bir alt satirda olculuyor.
    KOSU_ADI = {"sabah": "🌅 Sabah taramasi",
                "ogle": "🕛 Gun ortasi taramasi",
                "kapanis": "🔔 Avrupa kapanisi sonrasi tarama",
                "nabiz": "📊 Gece nabzi"}

    # Tek mesajda gosterilecek en fazla ENSTRUMAN (sinyal degil).
    # Gruplama sonrasi olctu: 19 Agustos bildirimi 4 satir yerine 2
    # enstruman olurdu. Tasarsa SESSIZ KESILMEZ, sayisi yazilir.
    HAFIF_AZAMI_ENSTRUMAN = 6
    HAFIF_AZAMI_RISK = 3

    def _hafif_bildir(self, kip, bozulan, portfoy_sinyali, riskler,
                      sahip: str) -> None:
        from ..piyasa import durum_satiri

        # TEK SAAT OKUMASI. Basliktaki zaman ile seans satiri ayri ayri
        # `now()` cagirsaydi, dakika sinirinda birbiriyle celisen iki
        # zaman yazabilirdi — kucuk ama tam da bu mesajin sikayet
        # konusu olan sinifindan bir tutarsizlik.
        simdi = datetime.now(timezone.utc)
        yerel = simdi.astimezone()
        L = [f"<b>{self.KOSU_ADI.get(kip, kip)}</b> · "
             f"{yerel.strftime('%d.%m.%Y %H:%M')}",
             f"<i>{durum_satiri(simdi)}</i>",
             "<i>Tatil takvimi yok: 'açık' = hafta içi ve seans saati.</i>"]

        for b in bozulan:
            L.append(f"\n🔔 <b>{_esc(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {_esc(_kirp(b['tez'], 200))}</i>")
            L.append("Önceden yazılan koşul: <b>"
                     + _esc(str(_kosul_okunabilir(b["kosul"]))) + "</b>")
            L.append(f"Şu anki {_esc(_alan_adi(b['alan']))}: "
                     f"<b>{_fiyat_tr(b['deger'])}</b>")

        gruplar = self._sinyal_gruplari(portfoy_sinyali)
        for grup in gruplar[:self.HAFIF_AZAMI_ENSTRUMAN]:
            L.append("\n" + self._grup_metni(grup))
        if len(gruplar) > self.HAFIF_AZAMI_ENSTRUMAN:
            # SESSIZ KESME YOK: eksik oldugu soylenmeyen liste, TAM
            # sanilir. Ayni ders `isyatirim` kesilmesinde ogrenildi.
            L.append(f"\n<i>… ve {len(gruplar) - self.HAFIF_AZAMI_ENSTRUMAN} "
                     "enstrumanda daha sinyal var.</i>")

        for r in riskler[:self.HAFIF_AZAMI_RISK]:
            L.append("\n" + _risk_satiri(r))
        if len(riskler) > self.HAFIF_AZAMI_RISK:
            L.append(f"\n<i>… ve {len(riskler) - self.HAFIF_AZAMI_RISK} risk "
                     "daha.</i>")

        L.append("\n<i>Bu koşuda model calismadi — yalnizca olculen esikler.</i>")
        self._sahibe_bildir(sahip, "\n".join(L), kaynak=kip)

    @staticmethod
    def _sinyal_gruplari(sinyaller: list[dict]) -> list[list[dict]]:
        """
        Sinyalleri ENSTRUMANDA toplar; guclu enstruman once.

        Onceden satir basina BIR SINYAL yaziliyordu ve `[:4]` ile
        kesiliyordu. 19 Agustos'ta AVTX tek basina uc satir tuttu —
        bildirimin %75'i — ve dorduncu sinyali (RSI) sessizce dustu.
        Oysa ucu de AYNI OLAYIN olcumuydu: ayni gunun -%19,91'i.
        Hacim anomalisinin kaniti bile ayni sayiyi tasiyor.
        """
        gruplar: dict = {}
        for s in sinyaller:
            gruplar.setdefault(s.get("instrument_id"), []).append(s)
        for g in gruplar.values():
            g.sort(key=lambda x: -(x.get("guc") or 0))
        return sorted(gruplar.values(),
                      key=lambda g: -(g[0].get("guc") or 0))

    def _grup_metni(self, grup: list[dict]) -> str:
        """Tek enstruman, tek blok: kimlik satiri + KANIT satiri."""
        bas = grup[0]
        # IKINCI BIR ESLEME YAZILMAZ. Buradaki tablo bir zamanlar
        # `_yuzde_tr`inkinden AYRIYDI (notr icin "•" vs "▪️") — ayni
        # gercek iki yerde beyan edilince sessizce ayrisiyor, bu
        # projenin tekrar eden kusur sinifi. Tek kaynak: `YON_ISARETI`.
        ok = YON_ISARETI.get(bas.get("yon"), YON_ISARETI["notr"])

        from ..piyasa import borsa_coz
        try:
            borsa = borsa_coz(self.db, bas.get("instrument_id"),
                              bas.get("venue"))
        except Exception as e:                        # noqa: BLE001
            # Borsa cozumu bir SUS bilgisidir; bildirimi dusurmemeli.
            log.warning("[nabiz] borsa cozulemedi (%s): %s",
                        bas.get("sembol"), e)
            borsa = None

        kimlik = [f"{ok} <b>{_esc(bas.get('sembol'))}</b>"]
        if bas.get("ad") and str(bas["ad"]).upper() != str(bas.get("sembol")).upper():
            kimlik.append(_esc(_kirp(bas["ad"], 40)))
        # BORSA YALNIZCA BILINIYORSA yazilir. Bilinmeyeni "BUX" diye
        # yazmak yanlis olurdu: BUX bir araci kurum, piyasa degil.
        if borsa:
            kimlik.append(borsa)
        kimlik.append("portfoyunde")

        kanitlar = [m for m in (self._kanit_metni(s) for s in grup) if m]
        bar = _tarih_kisa(bas.get("bar_ts"))
        onek = f"{bar} bari: " if bar else ""
        return (" · ".join(kimlik)
                + (f"\n   {onek}" + " · ".join(kanitlar) if kanitlar else ""))

    @staticmethod
    def _kanit_metni(s: dict) -> str | None:
        """
        Sinyalin SAYISI. `_hafif`in docstring'i bunu zaten vaat ediyordu
        ("ROSE gunluk oynakliginin 2,8 kati dustu, hacim teyitli") ama
        mesaja yalnizca `(tur, yon)` yaziliyordu — kanit veritabaninda
        kaliyor, kullaniciya ulasmiyordu.
        """
        k = s.get("kanit") or {}
        tur = s.get("tur")
        if tur == "olagandisi_hareket":
            g, sig = k.get("gunluk_getiri_%"), k.get("sigma")
            if not isinstance(g, (int, float)):
                return None
            metin = _yuzde_tr(g, ok=True)
            if isinstance(sig, (int, float)):
                metin += f" ({'+' if sig >= 0 else '-'}{_tr(abs(sig), 1)}σ)"
            return metin
        if tur == "hacim_anomalisi":
            v = k.get("hacim_kati")
            return (f"hacim {_tr(v, 1)}×"
                    if isinstance(v, (int, float)) else None)
        if tur == "rsi_ucu":
            v = k.get("rsi14")
            return f"RSI {_tr(v, 1)}" if isinstance(v, (int, float)) else None
        if tur == "sma50_kirilimi":
            yon = "yukari" if s.get("yon") == "yukari" else "asagi"
            return f"SMA50 {yon} kirildi"
        if tur == "olay_etkisi":
            car, t = k.get("car_%"), k.get("t")
            if not isinstance(car, (int, float)):
                return None
            metin = f"olay etkisi CAR {_yuzde_tr(car, 1, ok=True)}"
            if isinstance(t, (int, float)):
                metin += f" (t {'+' if t >= 0 else '-'}{_tr(abs(t), 1)})"
            gun = k.get("olay_gun_once")
            tarih = _tarih_kisa(k.get("olay_tarihi"))
            if tarih:
                # OLAYIN TARIHI HER ZAMAN YAZILIR. Tazelik suzgeci
                # zaten eskiyi eliyor, ama gecen 1-3 gunluk olay da
                # "bugun oldu" diye okunmamali.
                metin += f" — olay {tarih}"
                if isinstance(gun, int) and gun > 0:
                    metin += f", {gun} gun once"
            return metin
        return None

    def _gundem(self, guclu: list[dict], sahip: str | None = None) -> list[dict]:
        """
        Panele gidecek gozlemleri secer: once PORTFOY, sonra guc.

        Saf "en guclu N" secimi evren buyuklugunu gizli bir agirlik gibi
        iceri sokuyor. Olculdu: 251 BIST sembolu 19 BUX ve 67 kripto
        sembolunu bogup panelin 12 slotunun 10'unu aliyordu — ustelik
        BIST'te tek pozisyon ve Midas'ta bakiye YOK, yani kapasitenin
        %83'u islem yapilamayan kagitlara gidiyordu.

        Sahip olunan enstrumanlara PORTFOY_ASGARI_SLOT kadar yer ayrilir;
        o kadar sinyal yoksa artan slot geri verilir — kota doldurmak icin
        zayif sinyal YUKSELTILMEZ. Kalan yerler yine guce gore dolar,
        yani BIST tamamen disarida kalmaz.
        """
        if not guclu:
            return []
        sahibin = self.db.sahip_pozisyon_idleri(sahip) if sahip else set()
        portfoy = [x for x in guclu if x.get("instrument_id") in sahibin]
        secilen = portfoy[:PORTFOY_ASGARI_SLOT]
        kimlik = {id(x) for x in secilen}
        for x in guclu:                        # kalan slotlar guce gore
            if len(secilen) >= PANEL_ADAY:
                break
            if id(x) not in kimlik:
                secilen.append(x)
                kimlik.add(id(x))
        # Guc sirasi korunur ki hakem ve ajanlar onemi siralamadan okusun.
        secilen.sort(key=lambda x: -x["guc"])
        log.info("[nabiz] gundem: %d gozlem (portfoy %d), venue %s",
                 len(secilen), sum(1 for x in secilen
                                   if x.get("instrument_id") in sahibin),
                 {v: sum(1 for x in secilen if x["venue"] == v)
                  for v in sorted({x["venue"] for x in secilen})})
        return secilen

    def _atilanlari_isle(self, rapor: dict, hakem_rapor: dict,
                         panel_idleri: dict) -> None:
        """
        Defterin attigi gorusleri o kosunun `panel_runs` SATIRLARINA yazar.

        ID ILE, ZAMAN DAMGASIYLA DEGIL. Once "o kosunun en son run_ts'i"
        araniyordu ve bu, panellerin SIRAYLA kosmasi sayesinde dogruydu —
        tasarimdan degil TESADUFTEN. Iki sahibin damgasi ayni saniyeye
        duserse sayaclar BASKASININ satirina yazilirdi. `panel_idleri`
        yazan tarafin dondurdugu gercek satir kimlikleri; eslesme
        varsayimi tamamen ortadan kalkiyor.

        HER AJAN KENDI SAYISINI TASIR: kosunun toplamini tek satira
        yazmak, "yanlis dagitilmis bir sayi hic yazilmamis olandan
        kotudur" kuralini cignerdi.
        """
        if not panel_idleri:
            return
        try:
            with self.db.tx() as c:
                for kaynak in (rapor, hakem_rapor):
                    for ajan, d in (kaynak.get("ajan_bazli") or {}).items():
                        satir = panel_idleri.get(ajan)
                        if satir is None:
                            # Ajan panelde kosmadiysa yazacak satir yok;
                            # sayiyi baska satira ITMEK yanlis olurdu.
                            log.warning("[nabiz] '%s' icin panel_runs satiri "
                                        "yok, sayac yazilmadi", ajan)
                            continue
                        c.execute(
                            """UPDATE panel_runs SET
                                 atilan_sembol_yok = ?, atilan_seri_yok = ?,
                                 atilan_cakisma = ?
                               WHERE id = ?""",
                            (d["atilan_sembol_yok"], d["atilan_seri_yok"],
                             d["atilan_cakisma"], satir))
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] atilan sayaclari yazilamadi: %s", e)

    # ------------------------------------------------------------------
    # OZET MESAJI — her kosuda, her aliciya, TEK mesaj.
    #
    # NEDEN TEK MESAJ VE NEDEN PANELDEN BAGIMSIZ
    #   Once iki ayri mesaj gidiyordu: tez alarmi (deterministik) ve
    #   panel ozeti (LLM). Ritim v2 gunde dort kosu getiriyor, yani
    #   gunde sekiz mesaj. Ustelik PANEL YOLUNDA risk bildirimi HIC
    #   YOKTU: tazelik suzgeci, bastirma, seans satiri ve gruplama
    #   yalnizca `_hafif` dalinda vardi (runner.py:421-433). `panel:
    #   true` yapilan an sabah ve ogle kosulari o duzeltmelerin DISINA
    #   cikardi.
    #
    #   Cozum: tek gövde, ve ALARM BOLUMU PANELDEN ONCE hesaplaniyor.
    #   Panel patlasa bile mesaj gider ve tez/risk satirlari icinde
    #   olur — "tez kontrolu modele hic bagli degil" ilkesi korunur.
    # ------------------------------------------------------------------
    def _taktik_satirlari(self, taktikler: list[dict] | None) -> list[str]:
        """
        TAKTIK BLOGU — "ne yapayim" sorusunun yapisal cevabi.

        Her satir: tur, giris, stop, stop mesafesi ve %1 risk icin
        pozisyon payi. TUTAR/ADET YOK — gerekcesi `pulse.boyutlama`
        basinda: portfoy degeri ekran goruntusunden geliyor ve bayat
        olabilir; oran bayatliktan etkilenmez.

        Seviyelerin NEREDEN geldigi de yaziliyor — ama ANAHTAR ADIYLA
        DEGIL, OKUNABILIR ADIYLA ("20 gunun en yuksek kapanisi").
        Anahtar defterde oldugu gibi kaliyor; denetim izi makine
        okunur olmali, kullanicinin okudugu cumle degil.
        """
        if not taktikler:
            return []
        from .boyutlama import satir as boyut_satiri
        from .seviye import kaynak_adi
        from .tez import okunabilir

        ETIKET = {"alim": "🟢 ALIM", "koruma": "🛡 KORUMA",
                  "satis": "🔴 SATIS"}
        L = ["\n🎯 <b>Taktik</b>"]
        for t in taktikler[:self.AZAMI_TAKTIK]:
            pb = t.get("para_birimi") or ""
            L.append(f"\n{ETIKET.get(t['tur'], t['tur'])} "
                     f"<b>{_esc(t.get('sembol'))}</b>"
                     + (f" · ufuk {t['ufuk_gun']} gun"
                        if t.get("ufuk_gun") else ""))
            for alan, etiket in (("giris", "Giris"), ("stop", "Stop")):
                if t.get(alan) is None:
                    continue
                kaynak = kaynak_adi(t.get(f"{alan}_kaynak"))
                L.append(f"{etiket} <code>{_fiyat_tr(t[alan])}"
                         + (f" {_esc(pb)}" if pb else "") + "</code>"
                         + (f" <i>({_esc(kaynak)})</i>" if kaynak else ""))
            bs = boyut_satiri(t.get("giris"), t.get("stop"))
            if bs:
                L.append(bs)
            if t.get("gecersizlesme_kosulu"):
                L.append("Bu taktik su durumda gecersiz: <b>"
                         + _esc(str(okunabilir(t["gecersizlesme_kosulu"])))
                         + "</b>")
        if len(taktikler) > self.AZAMI_TAKTIK:
            L.append(f"\n<i>… ve {len(taktikler) - self.AZAMI_TAKTIK} taktik "
                     "daha (defterde).</i>")
        L.append("\n<i>Seviyeler OLCULEN degerlerden secildi, model "
                 "hesaplamadi. Tavsiye degil; sistem emir gondermez.</i>")
        return L

    # Mesajda gosterilecek en fazla taktik. Gerisi deftere yaziliyor —
    # "gunde en cok 2-3 taktik" karari (2026-08-21) mesaj katmaninda
    # uygulaniyor, uretim katmaninda degil: olculmesi gereken sey
    # hakemin TUM cagrilari.
    AZAMI_TAKTIK = 3

    def _ozet_bildir(self, kip: str, sahip: str, *, bozulan: list[dict],
                     riskler: list[dict], sade: str | None, ozet: str | None,
                     karne: dict, n_tahmin: int, hakem_id: int | None,
                     panel_notu: str | None = None,
                     taktikler: list[dict] | None = None) -> None:
        from ..notify.telegram import md_to_tg_html
        from ..piyasa import durum_satiri

        # TEK SAAT OKUMASI: baslik ile seans satiri dakika sinirinda
        # birbiriyle celisen iki zaman yazmasin.
        simdi = datetime.now(timezone.utc)
        yerel = simdi.astimezone()
        L = [f"<b>{self.KOSU_ADI.get(kip, kip)}</b> · "
             f"{yerel.strftime('%d.%m.%Y %H:%M')}",
             f"<i>{durum_satiri(simdi)}</i>",
             "<i>Tatil takvimi yok: 'açık' = hafta içi ve seans saati.</i>"]

        for satir in self._portfoy_satirlari(sahip):
            L.append(satir)
        for satir in self._makro_satirlari():
            L.append(satir)

        # GUNDEM YALNIZCA GECE NABZINDA.
        #
        # Pencere 24 saat; dort kosuda birden gosterilseydi ayni uc
        # baslik gunde dort kez tekrarlanirdi. Ali'nin bu mesajlar icin
        # tekrarlanan sikayeti zaten bu: gurultu, degiseni gizliyor.
        # Aksam kosusu gunun TAMAMINI gormus tek kosu.
        if kip == self.GUN_SONU_BILDIRIM_KIPI:
            L.extend(self._gundem_satirlari())

        # --- ALARM: deterministik, panelden BAGIMSIZ --------------------
        for b in bozulan:
            L.append(f"\n🔔 <b>{_esc(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {_esc(_kirp(b['tez'], 200))}</i>")
            L.append("Önceden yazılan koşul: <b>"
                     + _esc(str(_kosul_okunabilir(b["kosul"]))) + "</b>")
            L.append(f"Şu anki {_esc(_alan_adi(b['alan']))}: "
                     f"<b>{_fiyat_tr(b['deger'])}</b>")
        for r in riskler[:self.HAFIF_AZAMI_RISK]:
            L.append("\n" + _risk_satiri(r))
        if len(riskler) > self.HAFIF_AZAMI_RISK:
            L.append(f"\n<i>… ve {len(riskler) - self.HAFIF_AZAMI_RISK} risk "
                     "daha.</i>")

        # --- PANEL ------------------------------------------------------
        govde = md_to_tg_html(sade if sade else (ozet or ""))
        if panel_notu:
            L.append(f"\n🧠 <i>{_esc(panel_notu)}</i>")
        elif govde.strip():
            L.append(f"\n🧠 <b>Panel</b>\n{govde}")
        else:
            # SESSIZLIK GECERLI CIKTI ama SESSIZ MESAJ DEGIL: kullanici
            # gunde dort mesaj bekliyor; gitmeyen mesaj bekciye
            # "kosmadi" gibi, kullaniciya "bozuk" gibi gorunur.
            L.append("\n🧠 <i>Panel: öne çıkan bir şey bulmadı.</i>")

        # TAKTIK PANELDEN SONRA, KARNEDEN ONCE: once ne oldugu, sonra ne
        # yapilabilecegi, en sonda "bu sistemin isabeti ne" — okuma
        # sirasi karar sirasiyla ayni olmali.
        L.extend(self._taktik_satirlari(taktikler))
        L.extend(self._karne_satirlari(karne, n_tahmin))
        markup = None
        if hakem_id:
            # BUTON SATIR ID'SI TASIR, zaman damgasi degil: iki sahibin
            # damgasi ayni saniyeye duserse damga tabanli arama
            # BASKASININ teknik detayini acardi.
            markup = {"inline_keyboard": [[
                {"text": "🔍 Teknik detay",
                 "callback_data": f"det:{hakem_id}"}]]}
        self._sahibe_bildir(sahip, "\n".join(L), reply_markup=markup,
                            kaynak=kip)

    def _portfoy_satirlari(self, sahip: str) -> list[str]:
        """
        Hesap basina gunluk degisim. OLCULEMEYEN HESAP SATIR YAZMAZ.

        `positions` anlik goruntuleri PARCALI oldugu icin "son iki
        snapshot farki" bir sey olcmez (olculdu: 18/2/1/4 satir).
        Dogru olcum bugunku pozisyonlarin fiyat serisinden iki kapanisi
        — `analysis.portfolio.gunluk_degisim`.
        """
        from ..analysis.portfolio import gunluk_degisim
        # HESAP LISTESI VERIDEN TURUYOR, elle yazilmiyor. Sabit bir
        # ("bux","midas","binance") demeti, yeni bir hesap eklendiginde
        # (or. bir TEFAS hesabi) SESSIZCE eksik kalirdi — bu projenin
        # tekrar eden kusur sinifi.
        try:
            hesaplar = [r["account"] for r in self.db.query(
                "SELECT DISTINCT account FROM positions WHERE sahip = ? "
                "ORDER BY account", (sahip,))]
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] %s hesap listesi okunamadi: %s", sahip, e)
            return []
        out, notlar = [], []
        for hesap in hesaplar:
            try:
                d = gunluk_degisim(self.db, hesap, sahip)
            except Exception as e:                    # noqa: BLE001
                log.warning("[nabiz] %s/%s portfoy satiri: %s", sahip, hesap, e)
                continue
            if not d:
                continue
            if d.get("yetersiz_kapsam"):
                # SESSIZ ATLAMA YOK ama SAYI DA YOK: portfoyun %80'ini
                # fiyatlayamiyorsak "portfoy +%0,4" YANLIS BEYANDIR.
                out.append(f"\n📊 <b>{hesap.upper()}</b> günlük değişim "
                           f"ölçülemedi (kapsam %{d['kapsam'] * 100:.0f}).")
                continue
            # HESAP BASLIGI KENDI SATIRINDA, DETAY ALTINDA.
            #
            # Olculdu 2026-09-02 (Ali'nin ekran goruntusu): baslik, en
            # iyi ve en kotu tek satirda ` · ` ile diziliyordu ve
            # telefonda ORTASINDAN sariyordu — "en iyi USDT +" bir
            # satirda, "%0,01" digerinde kaliyordu. Yuzde isareti ile
            # sayisi ayri satira dusunce satir okunamaz hale geliyor.
            # Kirilim noktasini SATIR SONUNA koymak, sarmayi rastgele
            # olmaktan cikariyor.
            satir = (f"\n📊 <b>{hesap.upper()}</b>  "
                     f"{_yuzde_tr(d['degisim_%'], ok=True)} "
                     f"{d['para_birimi']}")
            # TEK POZISYONLU HESAPTA "en cok/en az" AYNI SAYIYI TEKRAR
            # EDER. Olculdu 2026-08-20: Midas'ta tek pozisyon var ve
            # satir "MIDAS +%6,14 · en cok TRALT +%6,14" diye cikti —
            # ikinci yari sifir bilgi tasiyor. Ayrimin anlamli olmasi
            # icin en az IKI farkli hareket gerekiyor.
            if d.get("en_cok") and d.get("en_az"):
                # "en cok / en az" MUGLAK: neyin en cogu — adet mi,
                # tutar mi, getiri mi? Olculen sey GETIRI, o yuzden
                # "en iyi / en kotu". Kullanici 2026-08-21'de mesajlarin
                # "anlayacagimiz sekilde" olmasini istedi.
                # ISARET YALNIZCA SATIRIN BASLIK SAYISINDA.
                #
                # Kullanici 2026-08-21'de "yesil/kirmizi semboller cok
                # fazla" dedi ve haklıydi: uc hesap x uc sayi = dokuz
                # isaret, ustune makro. Isaret her yerde olunca hicbir
                # yerde dikkat cekmiyor — vurgu SEYREK oldugunda vurgudur.
                #
                # Detay kalemlerde gerekmiyor: "en iyi"/"en kotu"
                # kelimeleri yonu ZATEN soyluyor, +/- isareti de duruyor.
                satir += (f"\n     en iyi {_esc(d['en_cok'][0])} "
                          f"{_yuzde_tr(d['en_cok'][1])}"
                          f" · en kötü {_esc(d['en_az'][0])} "
                          f"{_yuzde_tr(d['en_az'][1])}")
            elif d.get("en_cok"):
                # Tek kalem: adini yaz, yuzdesini TEKRARLAMA.
                satir += f"\n     tek kalem: {_esc(d['en_cok'][0])}"
            out.append(satir)
            # NE OLCULDUGU BEYAN EDILIYOR: kur etkisi disarida VE
            # adetlerin tarihi ayri yaziliyor.
            #
            # Fiyat gunluk tazeleniyor, ADET yalnizca yeni bir ekran
            # goruntusu geldiginde degisiyor. Ikisi ayni satirda
            # gorununce AYNI TAZELIKTE saniliyor — olculdu 2026-08-20:
            # bux fiyatlari 19 Agustos, adetleri 14 Agustos (alti gun).
            # Arada islem yapildiysa agirliklar yanlis ve bunu VERIDEN
            # bilemeyiz; bilemedigimiz seyi iddia etmek yerine TARIHI
            # soyluyoruz.
            # TEKRAR EDEN KISIM SATIR SATIR YAZILMAZ.
            #
            # Olculdu 2026-08-21: uc hesabin UCUNDE de ayni cumle vardi
            # ("kur etkisi haric (fiyat hareketi)") ve mesajin ucte biri
            # bu tekrardan olusuyordu. Kullanici "gurultusuz olsun"
            # dedi; ayni bilgiyi uc kez soylemek okumayi zorlastirir ve
            # DEGISEN kismi (tarihler) gozden kacirir.
            #
            # TARIHLER hesap basina KALIYOR cunku gercekten farklilar
            # (binance 18 Agu, bux 20 Agu). Ortak cumle sona tasiniyor —
            # ama yalnizca HEPSI AYNIYSA; farklilarsa yerinde kalir,
            # cunku o zaman tasimak yanlis beyan olurdu.
            notlar.append(d["not"])
            alt = f"fiyat {_tarih_kisa(d['tarih']) or d['tarih']}"
            if d.get("adet_tarihi"):
                alt += f" · adet {_tarih_kisa(d['adet_tarihi']) or d['adet_tarihi']}"
                yas = d.get("adet_yas_gun")
                if yas and yas > self.ADET_BAYATLIK_UYARI_GUN:
                    # CUMLE KAYNAGA GORE. Ali 2026-09-01'de bildirdi:
                    # IBKR icin "ekran goruntusu" deniyordu, oysa orada
                    # ekran goruntusu YOK — canli API var. Yanlis cumle
                    # kullaniciya YANLIS IS yaptirir.
                    if d.get("adet_kaynagi") == "api":
                        alt += (f" · {yas} gün önce tazelendi — API canlı, "
                                "OTURUM AÇIKKEN tazelenmeli")
                    else:
                        alt += (f" · {yas} gün önceki ekran görüntüsü, "
                                "arada işlem yaptıysan ağırlıklar eski")
            out.append(f"     <i>{alt}</i>")
        if out and notlar:
            if len(set(notlar)) == 1:
                out.append(f"<i>Yüzdeler: {notlar[0]}.</i>")
            else:
                out.append("<i>Yüzdeler hesaba göre farklı ölçüldü: "
                           + "; ".join(sorted(set(notlar))) + ".</i>")
        return out

    # ADET ANLIK GORUNTUSU BU KADAR ESKIYSE UYARI YAZILIR.
    #
    # 1 gun: fiyat bari ile adet ayni gunden ya da bir gun farkliysa
    # (hafta sonu, gece kosusu) uyari GURULTU olur. Iki gun ve otesi
    # kullanicinin bilmesi gereken bir seydir — bu arada islem yapmis
    # olabilir ve agirliklar onu yansitmaz.
    ADET_BAYATLIK_UYARI_GUN = 1

    # MAKRO SATIRI BAYATLIK SINIRI (gun). Son bar bundan eskiyse SAYI
    # GOSTERILMEZ, tarih yazilir. Iki gun hafta sonunu da kapsar; daha
    # genisi "gram altin 4.512" derken uc gun onceki fiyati soylemek
    # olurdu ve bu, `yanlis-yok-beyani`nin tersi kadar kotu bir sinif:
    # BAYAT VERIYI TAZE GIBI SUNMAK.
    MAKRO_AZAMI_BAYATLIK_GUN = 2

    def _makro_satirlari(self) -> list[str]:
        """
        Ayarda secilen MAKRO kodlarinin tek satirlik ozeti.

        DEGISIM ONCEKI GUNUN KAPANISINA GORE, kipin onceki kosusuna
        gore DEGIL. Aksi halde 12:30 satiri 08:00'e gore %0,0 gosterir
        ve hicbir bilgi tasimaz.

        BU SATIR YORUM DEGIL, UC SAYIDIR. "Altin yukselisde" gibi bir
        sifat yazilmaz — makro panelin `_MAKRO_UYARI` disiplini burada
        da gecerli.
        """
        kodlar = self.s.get("ritim.ozet_makro") or []
        if not kodlar:
            return []                                  # bos liste = satir yok
        parca, dipnot = [], []
        self._makro_dipnot = None
        for kod in kodlar:
            try:
                p = self._makro_parcasi(str(kod))
            except Exception as e:                     # noqa: BLE001
                log.warning("[nabiz] makro %s okunamadi: %s", kod, e)
                continue
            if p:
                parca.append(p)
                if getattr(self, "_makro_dipnot", None):
                    dipnot.append(self._makro_dipnot)
        if not parca:
            return []
        out = [f"\n🌍 {' · '.join(parca)}"]
        if dipnot:
            # UYARILAR TEK SATIRDA, AMA DUSURULMEDEN. Altinda iki fiyat
            # var (uluslararasi parite vs yurtici prim); hangisini
            # gosterdigimizi soylememek yanlis beyan olurdu.
            out.append(f"<i>{_esc('; '.join(dict.fromkeys(dipnot)))}</i>")
        return out

    # GUNDEM BLOGU — kac saat geriye bakilir ve konu basina kac satir.
    #
    # 24 saat: "bugun ne oldu" sorusunun penceresi. Daha genisi dunun
    # haberini bugunmus gibi gosterirdi; daha dari, aksam kosusunda
    # sabahin haberini dusururdu.
    GUNDEM_PENCERE_SAAT = 24
    GUNDEM_KONU_BASI = 3
    # IKI BASLIK AYNI HABER MI — UC OLCUT, HEPSI OLCULEREK SECILDI.
    #
    # Ayni gelisme uc yayincidan uc farkli cumleyle geliyor ve Turkce
    # EKLER sozcugu degistiriyor ("Bej Kitap raporu" / "Bej Kitabi",
    # "faaliyet" / "faaliyetin"). Tam sozcuk esitligi bu ucunu AYRI
    # sayiyordu; olculdu 2026-09-02 (canli haber akisi, son 24 saat):
    #
    #   olcut                          makro_global 21 baslik -> kume
    #   tam sozcuk + jaccard           21  (Bej Kitap 3'e BOLUNDU)
    #   5-harf kok  + jaccard          20  (2 birlesti, 3'uncu ayri)
    #   5-harf kok  + KAPSAMA          16  (Bej Kitap 3'u de birlesti)
    #
    # KAPSAMA (kesisim / kisa olanin uzunlugu) secildi cunku basliklar
    # cok farkli uzunlukta; Jaccard uzun basligi cezalandiriyor.
    #
    # AMA KAPSAMA TEK BASINA COK GEVSEK: kisa bir baslik uzun bir
    # baslige tamamen "girebiliyor" ve iki AYRI olay birlesiyordu —
    # "Borsa gune dususle basladi" (sabah) ile "Borsa gunu dususle
    # kapatti" (aksam) ayni sayilmisti. ASGARI ORTAK SOZCUK sarti bunu
    # kaldirdi ve dogru birlesmelerin hicbirini bozmadi.
    GUNDEM_KOK_HARF = 5
    GUNDEM_BENZERLIK = 0.5
    GUNDEM_ASGARI_ORTAK = 3
    GUNDEM_ASGARI_SOZCUK = 4

    # Konu anahtari -> mesajdaki baslik. `KONU_ETIKET` ASCII ve
    # "Turkiye makro gundemi" gibi UZUN; mesajda satir basi olacagi icin
    # kisa ve tam Turkce karsiligi burada.
    GUNDEM_BASLIK = {
        "makro_tr":     "Türkiye",
        "makro_global": "Dünya",
        "jeopolitik":   "Jeopolitik",
        "emtia_enerji": "Emtia ve enerji",
    }

    @staticmethod
    def _haber_anahtari(baslik: str) -> frozenset:
        """
        Baslik -> anlamli sozcuk kumesi. AYNI HABERI TANIMAK ICIN.

        Ayni gelisme uc ayri yayincidan uc satir olarak geliyor (olculdu
        2026-09-02: "Fed'in Bej Kitap raporu yayimlandi" / "Fed'in Bej
        Kitabi ... arttigini ortaya koydu" / "Fed Bej Kitap: ABD'de
        ekonomik faaliyetler ilimli artti"). Uctu de gosterilseydi blok
        tek haberle dolardi.
        """
        from ..search.normalize import tr_fold
        import re as _re
        sozcukler = _re.split(r"[^a-z0-9]+", tr_fold(str(baslik or "").lower()))
        # KOKE KIRPILIYOR: Turkce eki sozcugu degistiriyor ve tam
        # esitlik ayni haberi ayri sayiyordu (kitap/kitabi,
        # faaliyet/faaliyetin). Kirpma uzunlugu olculerek secildi.
        return frozenset(s[:Nabiz.GUNDEM_KOK_HARF]
                         for s in sozcukler if len(s) > 2)

    def _gundem_satirlari(self) -> list[str]:
        """
        "Bugun ne oldu" blogu — KONU ekseninde, sembole bagli DEGIL.

        Ali 2026-09-02'de istedi: "o gunun kayda deger borsa haberlerinin
        de ozetleri olsa harika olur ... ne onemli bir global haber, ne
        onemli Avrupa Amerika Turkiye gibi".

        VERI ZATEN VARDI, OKUYAN YOKTU. `news.konu` sema 20'den beri
        doluyor ve gunluk RAPOR onu `_gundem_kovalari` ile kullaniyor;
        NABIZ MESAJI hic okumuyordu. Bu, deponun en cok tekrar eden
        kalibi (bkz. `yanlis-yok-beyani`: "yeni katman IKI tuketiciye
        baglanir") — burada zarar sessizdi, cunku eksik olan sey
        kullanicinin hic gormedigi bir bolumdu.

        AVRUPA/AMERIKA AYRIMI YOK VE UYDURULMUYOR. Elimizdeki eksen
        `makro_tr` / `makro_global`; kita ayrimi icin siniflandirici
        yeniden olculmeli. Yayincidan cikarmak YANLIS olurdu — WSJ,
        ECB hakkinda da yaziyor.

        KADEME 1-2 (kanit sayilabilir kaynak) — `_gundem_kovalari` ile
        AYNI kapi. Iki yerde iki farkli guvenilirlik esigi olsaydi ayni
        haber raporda "kanit", mesajda "gurultu" sayilirdi.
        """
        from ..research.konular import GUNDEM_KONULARI

        try:
            rows = self.db.query(
                f"""SELECT konu, tier, publisher, source, title, published_at
                    FROM news
                    WHERE tier IN (1, 2)
                      AND konu IN ({','.join('?' * len(GUNDEM_KONULARI))})
                      AND published_at >= datetime('now', ?)
                    ORDER BY tier, published_at DESC""",
                (*GUNDEM_KONULARI, f"-{self.GUNDEM_PENCERE_SAAT} hours"))
        except Exception as e:                         # noqa: BLE001
            log.warning("[nabiz] gundem blogu okunamadi: %s", e)
            return []
        if not rows:
            return []

        kovalar: dict[str, list[dict]] = {}
        for r in rows:
            anahtar = self._haber_anahtari(r["title"])
            # COK KISA BASLIK KIYASA GIRMEZ. Uc-dort sozcuklu bir baslik
            # kapsama olcutunde her uzun basligin icine "giriyor".
            if len(anahtar) < self.GUNDEM_ASGARI_SOZCUK:
                continue
            kova = kovalar.setdefault(r["konu"], [])
            for onceki in kova:
                ortak = len(anahtar & onceki["anahtar"])
                kisa = min(len(anahtar), len(onceki["anahtar"])) or 1
                if (ortak >= self.GUNDEM_ASGARI_ORTAK
                        and ortak / kisa >= self.GUNDEM_BENZERLIK):
                    # AYNI HABER: yayinci sayisi ONEMIN OLCUSU.
                    onceki["kaynak"].add(r["publisher"] or r["source"] or "?")
                    break
            else:
                kova.append({"anahtar": anahtar, "baslik": r["title"],
                             "kaynak": {r["publisher"] or r["source"] or "?"}})

        out: list[str] = []
        for konu_adi in GUNDEM_KONULARI:
            kova = kovalar.get(konu_adi) or []
            if not kova:
                continue
            # COK KAYNAKLI HABER ONCE: ayni gelismeyi kac yayincinin
            # yazdigi, onemin OLCULEBILIR tek isareti.
            kova.sort(key=lambda h: -len(h["kaynak"]))
            for h in kova[: self.GUNDEM_KONU_BASI]:
                n = len(h["kaynak"])
                ek = f" <i>({n} kaynak)</i>" if n > 1 else ""
                out.append(f"• <b>{self.GUNDEM_BASLIK[konu_adi]}</b> — "
                           f"{_esc(_kirp(h['baslik'], 150))}{ek}")
        if not out:
            return []
        return [f"\n🗞 <b>Bugün ne oldu</b>"] + out

    @staticmethod
    def _makro_adi(kod: str, ad: str | None, ccy: str) -> tuple[str, str | None]:
        """
        Gosterilecek AD ve (varsa) DIPNOT. Doner: (ad, dipnot).

        `ALTIN_GRAM` bir VERI ANAHTARIDIR, insan adi degil; mesajda
        oldugu gibi gorunmesi kullanicinin "gurultusuz ve anlayacagimiz
        sekilde" istegine aykiri (2026-08-21).

        AMA UYARI DUSURULMEZ. `instruments.name` bazen
        "Gram altin paritesi (TRY) — uluslararasi, yurtici prim HARIC"
        gibi bir KAYIT tasiyor ve o kuyruk dogrulugun kendisi: altinda
        iki ayri fiyat var ve hangisini gosterdigimizi soylememek
        yanlis beyandir. Kisa ad SATIRA, uyari DIPNOTA gidiyor —
        okunabilirlik icin dogruluk feda edilmiyor.
        """
        if not ad:
            return kod, None
        parca = [p.strip() for p in str(ad).split(" — ", 1)]
        kisa, dipnot = parca[0], (parca[1] if len(parca) > 1 else None)
        # "(TRY)" ekini duser: para birimi zaten sayinin yaninda yaziyor
        # ve iki kez gormek gurultu.
        if ccy and kisa.upper().endswith(f"({ccy})"):
            kisa = kisa[: kisa.rfind("(")].strip()
        return (kisa or kod), dipnot

    def _makro_parcasi(self, kod: str) -> str | None:
        """Tek makro kodun metni; seri yoksa None, bayatsa TARIH yazar."""
        r = self.db.query(
            """SELECT i.id, i.currency, i.name FROM instruments i
               WHERE i.venue = 'MAKRO' AND UPPER(i.symbol) = ? LIMIT 1""",
            (kod.upper(),))
        if not r:
            return None                                # kod yok: sessizce atla
        # PARA BIRIMI ENSTRUMANDAN, ELLE YAZILMAZ.
        ccy = (r[0]["currency"] or "").upper()
        ad, _dn = self._makro_adi(kod, r[0]["name"], ccy)
        # DIPNOT SAHIPSIZ KALMAZ: tek basina "yurtici prim HARIC"
        # hangi sayiya ait belli degil.
        self._makro_dipnot = f"{ad}: {_dn}" if _dn else None
        seri = self.db.fiyat_serisi(r[0]["id"], 5)
        if not seri or seri[-1]["close"] is None:
            return None
        son = seri[-1]
        yas = _gun_farki_bugune(son["ts"])
        if yas is not None and yas > self.MAKRO_AZAMI_BAYATLIK_GUN:
            # SAYI YOK, TARIH VAR. Bayat veriyi taze gibi sunmak,
            # hic gostermemekten kotudur.
            return (f"{_esc(ad)}: veri bayat "
                    f"({_tarih_kisa(son['ts']) or son['ts'][:10]})")
        # ONCEKI GUNUN kapanisi — ayni gunun baska bir bari degil.
        gun = str(son["ts"])[:10]
        onceki = next((b for b in reversed(seri[:-1])
                       if str(b["ts"])[:10] < gun and b["close"]), None)
        metin = f"{_esc(ad)} {_tr(son['close'])}"
        if ccy:
            metin += f" {_esc(ccy)}"
        if onceki:
            metin += f" {_yuzde_tr((son['close'] / onceki['close'] - 1) * 100, ok=True)}"
        return metin

    @staticmethod
    def _karne_satirlari(karne: dict, n_tahmin: int) -> list[str]:
        alt = []
        if karne.get("olcum"):
            a = karne["guven_araligi_%"]
            alt.append(f"\n<i>Karne (hakem cagrilari): {karne['olcum']} olcum, "
                       f"isabet %{karne['isabet_%']} "
                       f"(guven araligi %{a[0]}-%{a[1]}, "
                       f"{karne.get('aralik_ornegi', karne['olcum'])} "
                       f"bagimsiz kume)</i>")
            if not karne.get("yeterli_mi"):
                alt.append("<i>⚠️ Ornekem yetersiz — bu orandan sonuc "
                           "cikarma.</i>")
        else:
            # SABIT METIN DEGIL, defterin KENDI notu.
            alt.append(f"\n<i>Karne: {karne.get('not', 'olcum yok')}</i>")
        if n_tahmin:
            alt.append(f"<i>{n_tahmin} yeni tahmin deftere yazildi; "
                       f"vadesi dolunca puanlanacak.</i>")
        return alt
