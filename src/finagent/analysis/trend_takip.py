"""
DONCHIAN TREND TAKIBI — kitabin KENDI cercevesiyle sinama.

NEDEN AYRI BIR MODUL
--------------------
Tarayicidaki `sma50_kirilimi` kaba bir trend sinyali ve 2026-08-20
backtest'inde sifirdan ayirt edilemedi. Ama bu "trend takibi
calismiyor" DEMEK DEGIL — olcum cercevesi kitabinkiyle uyusmuyordu:

  * UFUK YANLIS. Sabit 1/5/20 gunluk pencerede olculdu; trend takibi
    AYLARLA calisir ve 20 gun onun icin gurultu.
  * CIKIS KURALI YOKTU. Trend takibinin yarisi cikistir (10 gunluk dip,
    2N stop). Stop'suz bir trend sistemi trend sistemi degildir.
  * ORTALAMA GETIRI TEK BASINA YANILTIR. Kitabin acik iddiasi: islemlerin
    COGU zarar eder, az sayida buyuk kazanc her seyi tasir. Yani asil
    olculmesi gereken sey KUYRUK.

KURALLAR (Kaplumbaga Sistem 1)
------------------------------
  giris  : kapanis, ONCEKI 20 gunun en yuksegini asarsa
  cikis  : kapanis, ONCEKI 10 gunun en dusugunun altina inerse
  stop   : giristen 2N asagi (N = 20 gunluk ATR)
  yon    : YALNIZCA UZUN. BIST'te acik satis pratikte kisitli; kitabin
           kazancinin yarisini olusturan dusen trend tarafi BIZDE YOK
           ve bunu sonucta acikca soylemek zorundayiz.

DURUSTLUK KAYITLARI
-------------------
1. LOOK-AHEAD YOK. t gunundeki karar yalnizca t'ye kadarki barlardan;
   Donchian penceresi t'nin KENDISINI dislar.
2. SERMAYE ISLEMI FILTRESI. BIST gunluk limiti asan bar fiyat hareketi
   degildir (bkz. `screener.BORSA_LIMITI`); boyle bir bar iceren islem
   DUSURULUR — ADEL'in 11:1 bolunmesi tek basina ortalamayi goturuyordu.
3. HAYATTA KALMA YANLILIGI VAR VE GIDERILEMEDI. Evren BUGUN kote olan
   kagitlardan kuruluyor; borsadan DUSMUS sirketler veride yok. Bu,
   sonucu YUKARI cekiyor ve buyuklugu olculemiyor. Sonuc bu payla
   okunmali.
4. ISLEM MALIYETI PARAMETRE, varsayilan gidis-donus %0,40.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

GIRIS_PENCERE = 20
CIKIS_PENCERE = 10
ATR_PENCERE = 20
STOP_N = 2.0

# Gunluk hareket bu esige ULASTIYSA o gun LIMITTE kapanmis sayilir ve o
# kapanistan GIRIS YAPILAMAZ. BIST limiti ±%10; %9,5 pay birakiyor.
LIMIT_YAKIN = 0.095


def _atr(seri, i, pencere=ATR_PENCERE):
    """Wilder olmayan basit ATR — `i` DAHIL degil, yalnizca oncesi."""
    if i < pencere + 1:
        return None
    tr = []
    for j in range(i - pencere, i):
        y, d, onceki = seri[j]["high"], seri[j]["low"], seri[j - 1]["close"]
        if y is None or d is None or onceki is None:
            return None
        tr.append(max(y - d, abs(y - onceki), abs(d - onceki)))
    return sum(tr) / len(tr) if tr else None


def _ayar():
    """Uretim esigini okumak icin ayar — testte enjekte edilebilir."""
    from ..config import load_settings
    return load_settings()


def _tabanda(kapanis, i: int, limit_yakin: float) -> bool:
    """
    `i` bari TABANDA (limit-down) mi kapandi? Kilitliyse SATILAMAZ.

    Tavan girisinin AYNASI (`LIMIT_YAKIN`): tavanda satis tarafi bos,
    tabanda alis tarafi bos. Ikisi de "fiyat var ama islem yok" hali.
    """
    if i <= 0 or i >= len(kapanis):
        return False
    onceki, bugun = kapanis[i - 1], kapanis[i]
    if not onceki or not bugun:
        return False
    return (bugun / onceki - 1) <= -limit_yakin


def _devir(seri, i: int, pencere: int = 20) -> float | None:
    """
    Giris anindaki GUNLUK TL DEVIR medyani (kapanis x hacim).

    MEDYAN, ORTALAMA DEGIL: tek bir blok islem ortalamayi kata cikarir
    ve illikit bir kagidi likit gosterir.

    `i` DAHIL DEGIL — yalnizca oncesi. Giris barinin kendi hacmini
    kullanmak, karari verirken henuz olusmamis bir sayiya bakmaktir.
    """
    if i < pencere:
        return None
    d = []
    for j in range(i - pencere, i):
        k, h = seri[j].get("close"), seri[j].get("volume")
        if k and h:
            d.append(k * h)
    if len(d) < pencere // 2:
        return None
    d.sort()
    return d[len(d) // 2]


def _cikis_sebebi(seri, kapanis, i: int, stop: float | None) -> str | None:
    """
    `i` barinda kuralin CIKIS sarti olustu mu? Sebep ya da None.

    TEK KOPYA. Bu test hem gecmisi yuruten `_yurut`ta hem de canli
    pozisyon icin `cikis_karari()`nde kullaniliyor. Ikinci bir kopya
    yazmak bu deponun en pahali dersini tekrarlamak olurdu: kopyalar
    ayrisir ve ayrisan tarafin hangisi oldugu ancak para kaybedilince
    anlasilir.

    SIRA ONEMLI: once stop, sonra Donchian. Ayni barda ikisi birden
    olusabilir ve stop DAHA KOTU cikis fiyatini temsil eder; once onu
    saymak sonucu iyimser yonde bozmaz.

    `stop` None ise yalnizca Donchian testi yapilir — kuralin girmedigi
    bir pozisyona 2N stop UYDURMAK, olmayan bir seviyeyi varmis gibi
    gostermek olurdu.
    """
    bar = seri[i]
    if (stop is not None and bar.get("low") is not None
            and bar["low"] <= stop):
        return "2N stop"
    onceki = [k for k in kapanis[i - CIKIS_PENCERE:i] if k]
    if len(onceki) >= CIKIS_PENCERE and bar["close"] < min(onceki):
        return "10 gun dip"
    return None


def cikis_karari(seri, stop: float | None = None) -> dict | None:
    """
    Serinin SON barinda cikis sarti olustu mu? Doner: sozluk ya da None.

    NEDEN VAR (2026-08-29). Motor yalnizca `AL` uretiyordu. Zarar kesme
    tarafi tahmin defterinin kosulundan (`close < stop`) tez alarmiyla
    geliyordu ama Donchian'in ASIL cikisi — 10 gunluk dip — hicbir yerde
    izlenmiyordu: giris gunundeki tabloda BIR KEZ gosterilip
    unutuluyordu.

    Trend takibinde kenar buyuk olcude cikistadir. Kural bir kagidi
    yukselirken tutar ve 10 gunluk dibe donunce birakir; o sinyal
    verilmezse pozisyon SURESIZ kalir ve sistem "al" deyip susan bir
    seye doner.

    `stop` biliniyorsa (tahmin defterinden) 2N testi de yapilir.
    Bilinmiyorsa YALNIZCA Donchian — ve cagiran taraf bunu kullaniciya
    SOYLEMELI.
    """
    seri = [dict(r) for r in seri]
    if len(seri) < CIKIS_PENCERE + 1:
        return None
    kapanis = [r["close"] for r in seri]
    i = len(seri) - 1
    if seri[i]["close"] is None:
        return None
    sebep = _cikis_sebebi(seri, kapanis, i, stop)
    if not sebep:
        return None
    onceki = [k for k in kapanis[i - CIKIS_PENCERE:i] if k]
    return {
        "sebep": sebep,
        "bar_ts": str(seri[i]["ts"])[:10],
        "kapanis": seri[i]["close"],
        "dip": min(onceki) if len(onceki) >= CIKIS_PENCERE else None,
        "stop": stop,
        "stop_bilinmiyor": stop is None,
    }


def _yurut(seri, borsa_limiti: float | None = 0.12,
           taban_kilidi: float | None = LIMIT_YAKIN,
           asgari_devir: float | None = None, *,
           giris_engeli: set[str] | frozenset[str] | None = None,
           stop_n: float | None = STOP_N,
           izleyen: bool = False,
           ) -> tuple[list[dict], dict | None]:
    """
    Kuralin seri boyunca YURUTULMESI. Doner: (kapanan islemler, ACIK pozisyon).

    ISLEMLER ILE ACIK POZISYON AYNI DONGUDEN CIKIYOR — bilerek.
    `islemler()` kapanmamis islemi DUSURUYOR ("uydurma bir cikis fiyati
    yazmaktansa islemi dusurmek dogru") ve bu dogru, ama "SU AN
    POZISYONDA MIYIZ" sorusunu cevapsiz birakiyordu. Sorunun bedeli
    olculdu (2026-08-28): gunluk tarama 2026-04-10'da 85 KIRILIM
    raporladi, kuralin TAZE GIRISI ise 4'tu — kalan 81'i kuralin ZATEN
    TUTTUGU pozisyonlardi. Yani defter kurali degil, kuralin
    tekrarlarini olcuyordu.

    Ikinci bir dongu YAZILMADI: giris/cikis mantigi (10 gun dip, 2N
    stop, gap-down, taban ertelemesi) TEK yerde kalmali — bu deponun en
    pahali dersi.

    Bir enstrumanda Donchian 20/10 + 2N kurallarinin urettigi ISLEMLER.

    Her islem: giris/cikis tarihi, getiri, cikis SEBEBI, tutulan gun.

    `taban_kilidi` — limit-down gunlerinde cikis ERTELENIR. None
    verilirse eski (iyimser) davranis; yalnizca ONCE/SONRA karsilastirmasi
    icin, uretimde KULLANILMAZ.

    `giris_engeli` — kapanisindan GIRIS YAPILMAYACAK gunler ('YYYY-MM-DD').
    Takvim filtresinin (`analysis.olay_takvimi.giris_engeli`) ve onun
    rastgele kontrolunun TEK baglanti noktasi. None/bos = uretimdeki
    kural, davranis BIREBIR ayni. Engel yalnizca GIRISI etkiler: acik
    pozisyonun cikisi, stop'u ve Donchian dibi degismez — filtre "o gun
    yeni risk alma" der, "tuttugun pozisyonu sat" demez.
    Engellenen gunde kirilim ertesi gun hala suruyorsa giris ertesi gun
    olur; bu kasitli: filtre sinyali silmez, ERTELER.

    `stop_n` / `izleyen` — STOP SINAVININ (`docs/stop-sinavi.md`) tek
    baglanti noktasi. Varsayilanlar uretimdeki kural (2N sabit), davranis
    BIREBIR ayni. `stop_n=None` stop YOK (yalnizca Donchian dibi).
    `izleyen=True`: stop, girisTEN BERI en yuksek kapanisin `stop_n * N`
    altina yukselir, asla inmez; i. barin stop'u YALNIZCA i-1'e kadarki
    kapanislardan (ileriye bakma yok). N giriste sabitlenir.
    """
    kapanis = [r["close"] for r in seri]
    out: list[dict] = []
    i, n = ATR_PENCERE + 1, len(seri)
    pozisyon = None

    while i < n:
        bar = seri[i]
        if bar["close"] is None:
            i += 1
            continue

        if pozisyon is None:
            onceki = [k for k in kapanis[i - GIRIS_PENCERE:i] if k]
            if len(onceki) < GIRIS_PENCERE:
                i += 1
                continue
            if bar["close"] > max(onceki):
                if giris_engeli and str(bar["ts"])[:10] in giris_engeli:
                    i += 1
                    continue
                N = _atr(seri, i)
                # LIKIDITE KAPISI — URETIM EVRENIYLE AYNI ESIK.
                #
                # Tarayici 50M TL gunluk hacim altindaki kagidi HIC
                # taramiyor (`screener.evren`), yani bu backtest o
                # kagitlarda uretilemeyecek islemleri sayiyordu.
                # Olculdu 2026-08-21: karin %55,9'u en iyi %5'lik
                # kuyruktan geliyor ve o kuyrugun illikit kagitlarda
                # yogunlasmasi, "kenar" sanilan seyin uygulanamaz
                # olmasi demek.
                #
                # ESIK GIRIS ANINDAN OLCULUYOR, bugunku hacimden DEGIL:
                # bugun likit olan bir kagit 2016'da olmayabilir ve
                # bugunun hacmiyle gecmisi suzmek GELECEGE BAKMAKTIR.
                if asgari_devir:
                    d = _devir(seri, i)
                    if d is None or d < asgari_devir:
                        i += 1
                        continue
                if N and N > 0:
                    # GIRIS GUNU TAVANDAYSA O KAPANISTAN ALINAMAZ.
                    #
                    # BIST'te gunluk limit ±%10 ve limitte islem
                    # KILITLENIR: tavanda satis tarafi bostur. Kirilim
                    # sinyali tam da tavan gununde cikma egilimindedir
                    # — olculdu 2026-08-21: OZATD'nin +%2492'lik
                    # "islemi" 35 tavan gunu iceriyor ve girisi de o
                    # gunlerden birinde. Sayilirsa backtest, YAPILAMAYAN
                    # bir islemi kar diye raporlar.
                    onceki_kapanis = kapanis[i - 1]
                    tavanda = (onceki_kapanis and
                               (bar["close"] / onceki_kapanis - 1) >= LIMIT_YAKIN)
                    pozisyon = {"giris_ts": bar["ts"], "giris": bar["close"],
                                "stop": (bar["close"] - stop_n * N
                                         if stop_n is not None else None),
                                "N": N, "zirve": bar["close"],
                                "giris_i": i, "girisde_tavan": bool(tavanda)}
            i += 1
            continue

        # --- pozisyondayiz: once STOP, sonra Donchian cikisi ----------
        # IZLEYEN STOP: bu barin stop'u DUNE kadarki en yuksek kapanistan.
        # `zirve` bir onceki turda (i-1'in kapanisiyla) guncellendi; bugunun
        # kapanisi ancak bu bar degerlendirildikten SONRA zirveye girer.
        if izleyen and stop_n is not None and pozisyon["stop"] is not None:
            pozisyon["stop"] = max(pozisyon["stop"],
                                   pozisyon["zirve"] - stop_n * pozisyon["N"])
        sebep = _cikis_sebebi(seri, kapanis, i, pozisyon["stop"])
        if sebep is None and bar["close"] is not None:
            pozisyon["zirve"] = max(pozisyon["zirve"], bar["close"])
        if sebep is None:
            i += 1
            continue

        # CIKIS FIYATI: STOP'TAN DEGIL, GERCEKTEN OLABILECEK FIYATTAN.
        #
        # Iki ayri gercekcilik sorunu vardi ve ikisi de BEKLENTIYI TEK
        # YONLU sisiriyordu — kayip tarafi oldugundan iyi gorunuyordu:
        #
        # 1. GAP-DOWN. `low <= stop` ise cikis TAM stop fiyatindan
        #    sayiliyordu. Ama bar stop'un ALTINDA aciliyorsa o fiyattan
        #    satamazsin; dolum aciliştadir. `min(stop, acilis)`.
        #
        # 2. TABAN KILIDI. BIST'te gunluk limit ±%10 ve limitte islem
        #    KILITLENIR: tabanda ALIS tarafi bostur, cikamazsin. Giris
        #    tarafinda bu ZATEN modellenmisti (`girisde_tavan`) ama
        #    cikista MODELLENMEMISTI — yani sistem tavanda alamiyor
        #    ama tabanda satabiliyordu. Asimetri, tam da kayiplari
        #    kucuk gosteren yonde.
        #
        # Cozum: kilitli barda cikilmaz, ISLEM GORULEBILEN ilk bara
        # ertelenir ve orada ACILISTAN cikilir.
        j, ertelenen = i, 0
        while (taban_kilidi and j < n
               and _tabanda(kapanis, j, taban_kilidi)):
            j += 1
            ertelenen += 1
        if j >= n:
            # Seri kilitliyken bitti: bu islem KAPANMADI, sayilmaz.
            # Uydurma bir cikis fiyati yazmaktansa islemi dusurmek
            # dogru — "eksik veri, yanlis veriden iyidir".
            break
        cikis_bar = seri[j]
        if sebep == "2N stop":
            acilis = cikis_bar.get("open") or cikis_bar["close"]
            cikis = min(pozisyon["stop"], acilis)
        else:
            cikis = (cikis_bar.get("open") or cikis_bar["close"]) \
                if ertelenen else cikis_bar["close"]
        i = j

        # SERMAYE ISLEMI ICEREN ISLEM DUSER: o bar fiyat hareketi degil.
        kirli = False
        if borsa_limiti:
            # DONGU DEGISKENI `k`: `j` yukarida CIKIS BARININ indeksi ve
            # burada golgelenirse erteleme bilgisi sessizce kaybolur.
            for k in range(pozisyon["giris_i"] + 1, i + 1):
                a, b = kapanis[k], kapanis[k - 1]
                if a and b and abs(a / b - 1) > borsa_limiti:
                    kirli = True
                    break
        if not kirli:
            out.append({
                "giris_ts": pozisyon["giris_ts"],
                # CIKIS TARIHI ERTELENEN BARDAN: `bar` sinyalin
                # gorulduğu bar ve taban kilidinde cikis GUNLER SONRA
                # gerceklesiyor. `bar["ts"]` yazmak, olmayan bir gunde
                # cikilmis gibi gosterirdi.
                "cikis_ts": cikis_bar["ts"],
                "giris": pozisyon["giris"], "cikis": cikis,
                # STOP KAYDEDILIYOR: "stop'un altinda cikildi mi, ne
                # kadar" sorusu (takvim filtresinin birincil olcutu)
                # ancak seviye islemle birlikte tasinirsa cevaplanir.
                "stop": pozisyon["stop"],
                "getiri": cikis / pozisyon["giris"] - 1,
                "gun": i - pozisyon["giris_i"], "sebep": sebep,
                "N_orani": pozisyon["N"] / pozisyon["giris"],
                "girisde_tavan": pozisyon.get("girisde_tavan", False),
                # KAC GUN CIKILAMADI. Sifirdan buyukse o islem taban
                # serisine yakalanmis demektir ve ozet bunu sayiyor.
                "taban_ertelemesi": ertelenen,
            })
        pozisyon = None
        i += 1
    return out, pozisyon


def islemler(seri, borsa_limiti: float | None = 0.12,
             taban_kilidi: float | None = LIMIT_YAKIN,
             asgari_devir: float | None = None, *,
             giris_engeli: set[str] | frozenset[str] | None = None,
             stop_n: float | None = STOP_N,
             izleyen: bool = False,
             ) -> list[dict]:
    """KAPANAN islemler. Acik pozisyon icin `acik_pozisyon()`."""
    return _yurut(seri, borsa_limiti, taban_kilidi, asgari_devir,
                  giris_engeli=giris_engeli, stop_n=stop_n,
                  izleyen=izleyen)[0]


def acik_pozisyon(seri, borsa_limiti: float | None = 0.12,
                  taban_kilidi: float | None = LIMIT_YAKIN,
                  asgari_devir: float | None = None) -> dict | None:
    """
    Serinin SONUNDA kural pozisyonda mi? Degilse None.

    NEDEN GEREKLI: gunluk tarama "kapanis > 20G yuksek" diyor ve
    POZISYON DURUMUNU BILMIYOR. Bir hisse trende girip 20 gunluk
    yukseginin ustunde kaldikca HER GUN yeniden sinyal veriyordu.
    Olculdu (2026-08-28, alti gun): 318 kirilimin yalnizca 49'u (%15)
    kuralin TAZE girisiydi.

    Bu, belge §2.2'nin olctugu sey ile motorun urettigi seyin
    AYRISMASIYDI: "~247 sinyal/ay" rakami `islemler()`ten geliyor
    (pozisyon farkinda), motor ise ayda ~750-2500 satir yaziyordu ve
    cogu AYNI ACIK POZISYONUN tekrariydi. Karne o tekrarlari bagimsiz
    gozlem sayardi.
    """
    return _yurut(seri, borsa_limiti, taban_kilidi, asgari_devir)[1]


def _yuzdelik(x, p):
    if not x:
        return None
    s = sorted(x)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[k]


# ----------------------------------------------------------------------
# KONTROL ANALIZLERI — "kenar GERCEKTEN kuralin mi?"
#
# 2026-08-21'de bu iki analiz KOSULDU ve sonucu commit mesajina yazildi
# ("kenarin YARISI piyasa surukletmesi") ama KODA ALINMADI. Yani
# projenin en onemli bulgusu TEKRARLANAMAZ durumdaydi: parametre ya da
# veri degistiginde yeniden olculemezdi ve tek kayit bir metin satiriydi.
#
# Bir sonucu tekrarlayamiyorsan ona sahip degilsin.
# ----------------------------------------------------------------------

def rastgele_kontrol(seri, islem_sayisi: int, tutma_gun: float,
                     tur: int = 200, tohum: int = 20260821,
                     maliyet: float = 0.004) -> dict:
    """
    AYNI SERIDE, AYNI SAYIDA, AYNI SUREDE RASTGELE giris.

    SORDUGU SORU: kural mi kazandirdi, yoksa kagit o donemde zaten
    yukseldigi icin mi? Bir trend sisteminin ürettigi getiri, ayni
    kagitta rastgele girip ayni sure tutmakla AYNI cikiyorsa, kural
    hicbir sey eklememis demektir — olculen sey PIYASA SURUKLETMESIDIR.

    TOHUM SABIT: ayni veriyle ayni sonuc. Rastgelelik burada bir
    KONTROL GRUBU, belirsizlik kaynagi degil; her kosuda baska sayi
    verseydi "kenar daraldi mi" sorusu cevaplanamazdi.
    """
    import random

    kapanis = [b.get("close") for b in seri]
    n = len(kapanis)
    tut = max(1, int(round(tutma_gun)))
    if n < tut + 2 or islem_sayisi <= 0:
        return {"tur": 0}
    rnd = random.Random(tohum)
    tur_ort = []
    # ISABET SAYACI — TUM ornekler uzerinden, tur ortalamasi DEGIL.
    #
    # NEDEN EKLENDI: karne "isabet %" konusuyor, bu fonksiyon ise
    # yalnizca ORTALAMA GETIRI donduruyordu. Ikisinin farkini almak
    # elmayla armut karsilastirmak olurdu — ve o fark, kullaniciya
    # "kural rastgeleyi su kadar geciyor" diye okunacakti.
    #
    # ISABET TUR ORTALAMASINDAN TURETILEMEZ: bir turun ortalamasi
    # pozitif olabilir ama iceriginin cogu negatif (tek buyuk kazanc
    # tasir) — trend takibinde TAM BEKLENEN sey bu. Bu yuzden sayac
    # tekil islemler uzerinde.
    pozitif = ornek = 0
    for _ in range(tur):
        g = []
        for _ in range(islem_sayisi):
            i = rnd.randrange(0, n - tut - 1)
            a, b = kapanis[i], kapanis[i + tut]
            if a and b and a > 0:
                net = b / a - 1 - maliyet
                g.append(net)
                ornek += 1
                pozitif += 1 if net > 0 else 0
        if g:
            tur_ort.append(sum(g) / len(g))
    if not tur_ort:
        return {"tur": 0}
    tur_ort.sort()
    return {
        "tur": len(tur_ort),
        "tutma_gun": tut,
        "ortalama_%": round(sum(tur_ort) / len(tur_ort) * 100, 3),
        "medyan_%": round(_yuzdelik(tur_ort, 0.5) * 100, 3),
        # %5-%95 ARALIK: kuralin beklentisi bu araligin ICINDEyse
        # "kural bir sey ekliyor" DENEMEZ.
        "p5_%": round(_yuzdelik(tur_ort, 0.05) * 100, 3),
        "p95_%": round(_yuzdelik(tur_ort, 0.95) * 100, 3),
        # HAM getiri isabeti (maliyet dusulmus). Defterin `isabet`i
        # PIYASAYA GORE DUZELTILMIS (`anormal > 0`); karsilastirilirken
        # AYNI TABAN kullanilmali ve hangi taban oldugu YAZILMALI.
        "ornek": ornek,
        "isabet_%": round(pozitif / ornek * 100, 1) if ornek else None,
    }


def aylik_kumelenme(islemler_: list[dict], maliyet: float = 0.004) -> dict:
    """
    Islemler AYA gore kumelenip ay ortalamalari olculur.

    NEDEN: 12.436 islem BAGIMSIZ GOZLEM DEGIL. Ayni ay icinde acilan
    yuzlerce islem TEK bir piyasa hareketini konusuyor; islem sayisiyla
    hesaplanan bir guven araligi, olmayan bir kesinlik uretir (defterin
    kumelenme duzeltmesiyle AYNI ders, bkz. `journal.karne`).

    Dogru gozlem birimi AY: kac ayda pozitif, aylar arasi dagilim ne.
    """
    import math

    aylar: dict[str, list[float]] = {}
    for t in islemler_:
        ay = str(t.get("giris_ts", ""))[:7]
        if len(ay) == 7:
            aylar.setdefault(ay, []).append(t["getiri"] - maliyet)
    if not aylar:
        return {"ay": 0}
    ort = {a: sum(v) / len(v) for a, v in aylar.items()}
    d = sorted(ort.values())
    n = len(d)
    ortalama = sum(d) / n
    sapma = (math.sqrt(sum((x - ortalama) ** 2 for x in d) / (n - 1))
             if n > 1 else 0.0)
    return {
        "ay": n,
        "islem": sum(len(v) for v in aylar.values()),
        "ay_ortalamasi_%": round(ortalama * 100, 3),
        "aylar_arasi_sapma_%": round(sapma * 100, 3),
        "pozitif_ay_%": round(sum(1 for x in d if x > 0) / n * 100, 1),
        "en_kotu_ay_%": round(d[0] * 100, 2),
        "en_iyi_ay_%": round(d[-1] * 100, 2),
        # AY BAZLI t: gozlem birimi AY oldugunda kac sigma?
        "t_ay": (round(ortalama / (sapma / math.sqrt(n)), 2)
                 if sapma > 0 and n > 1 else None),
        # TEK AYIN KATKISI: en iyi ay cikarilinca ne kaliyor?
        "en_iyi_ay_haric_%": (round(sum(d[:-1]) / (n - 1) * 100, 3)
                              if n > 1 else None),
    }


def ozet(hepsi: list[dict], maliyet: float = 0.004,
         yalniz_uygulanabilir: bool = False) -> dict:
    """
    Kitabin ISRAR ETTIGI olcutler — isabet orani TEK BASINA degil.

    `maliyet` gidis-donus, getiriden DUSULUR: %0,40 makul bir BIST
    perakende varsayimi (komisyon + spread).
    """
    if yalniz_uygulanabilir:
        hepsi = [x for x in hepsi if not x.get("girisde_tavan")]
    if not hepsi:
        return {"islem": 0}
    g = [x["getiri"] - maliyet for x in hepsi]
    kazanan = [x for x in g if x > 0]
    kaybeden = [x for x in g if x <= 0]
    ort_kazanc = sum(kazanan) / len(kazanan) if kazanan else 0.0
    ort_kayip = sum(kaybeden) / len(kaybeden) if kaybeden else 0.0

    # KUYRUK: kitabin tezi "az sayida buyuk kazanc her seyi tasir".
    # Olculecek sey: toplam KARIN yuzde kaci en iyi %5 islemden geliyor.
    sirali = sorted(g, reverse=True)
    ust5 = sirali[:max(1, len(sirali) // 20)]
    toplam_kar = sum(x for x in g if x > 0)
    return {
        "islem": len(g),
        "isabet_%": round(len(kazanan) / len(g) * 100, 1),
        "ort_kazanc_%": round(ort_kazanc * 100, 2),
        "ort_kayip_%": round(ort_kayip * 100, 2),
        "kazanc_kayip_orani": (round(ort_kazanc / abs(ort_kayip), 2)
                               if ort_kayip else None),
        "beklenti_%": round(sum(g) / len(g) * 100, 3),
        "medyan_%": round(_yuzdelik(g, 0.5) * 100, 2),
        "en_iyi_%": round(max(g) * 100, 1),
        "en_kotu_%": round(min(g) * 100, 1),
        "ust_%5_kar_payi_%": (round(sum(x for x in ust5 if x > 0)
                                    / toplam_kar * 100, 1)
                              if toplam_kar else None),
        "ort_tutma_gun": round(sum(x["gun"] for x in hepsi) / len(hepsi), 1),
        "stopla_cikis_%": round(
            sum(1 for x in hepsi if x["sebep"] == "2N stop") / len(hepsi) * 100, 1),
    }


def sinirlar(piyasa: str, asgari_devir: float) -> list[tuple[str, str]]:
    """
    Koşumun KONTROLLERI ve SINIRLARI — SAF: db yok, saat yok, IO yok.

    NEDEN AYRI FONKSIYON: bu metinler `run.py` icindeydi ve mutasyon
    turu bir kacak buldu — `if args.piyasa == "abd":` dali `if False`
    yapildiginda testim hala GECIYORDU, cunku dosyada METNI ariyordu,
    DALI degil. Bu deponun kayitli dersi: "kaba metin aramasi yanlis
    soruyu soruyordu; dogru soru davranisin ne oldugu".

    Doner: [(tur, metin)] — tur "+" (uygulanan kontrol) veya "!" (sinir).
    """
    abd = piyasa == "abd"
    birim = "USD" if abd else "TL"
    s = [("+", f"Likidite esigi: giris anindaki 20 gunluk medyan devir >= "
               f"{asgari_devir:,.0f} {birim} (uretim tarayicisiyla AYNI)."),
         ("+", "Taban (limit-down) gunlerinde cikis ERTELENIYOR; "
               "stop dolumu min(stop, acilis)."),
         ("!", "HAYATTA KALMA YANLILIGI giderilemedi: evren BUGUN kote "
               "olan kagitlardan kuruluyor.")]
    if abd:
        s.append(("!", "Getiriler NOMINAL USD. ABD enflasyonu donem boyunca "
                       "~%2-3/yil; TRY'deki gibi sonucu belirleyici DEGIL "
                       "ama sifir da degil."))
    else:
        s.append(("!", "Getiriler NOMINAL TRY. Enflasyon arindirilmadi "
                       "(TUFE verisi yok)."))
    s.append(("!", "PORTFOY DUZEYI GETIRI OLCULMUYOR: bunlar ISLEM BASINA "
                   "rakamlar. Sermaye kisiti, es zamanli pozisyon sayisi ve "
                   "equity curve hesaplanmiyor — 'al-tut'tan iyi mi' sorusu "
                   "BU CIKTIYLA CEVAPLANAMAZ."))
    return s


def kosu(db, baslangic: str, bitis: str, venue: str = "BIST",
         limit: int | None = None, maliyet: float = 0.004,
         asgari_devir: float | None = None, settings=None,
         endeksler: tuple[str, ...] | None = None,
         kiyas_kod: str = "XU100") -> dict:
    """
    `asgari_devir` — giris anindaki gunluk TL devir esigi. Verilmezse
    URETIM ESIGINDEN okunur (`sources.isyatirim.min_hacim_tl`, 50M):
    tarayici o esigin altindaki kagidi HIC taramiyor, yani backtest
    onlari sayarsa URETILEMEYECEK islemleri olcmus olur.

    Acikca 0 verilerek kapatilabilir — ONCE/SONRA karsilastirmasi icin.

    `endeksler` — evreni endeks uyeligiyle daraltir (`_evren` docstring'i
    hayatta kalma yanliligini anlatiyor; ORADAN OKU).

    `kiyas_kod` — "al-tut" kiyasinin endeksi. VARSAYILAN XU100 VE BU
    BIST ICIN DOGRU, baska piyasa icin DEGIL: ABD koşumunu XU100 ile
    kiyaslamak, olcuyu baska bir ulkenin para birimi ve enflasyonuyla
    almak olurdu.
    """
    from ..pulse.screener import BORSA_LIMITI
    from .backtest import _evren

    if asgari_devir is None:
        # ESIGIN BIRIMI VENUE'YE BAGLI. Varsayilan esik TL cinsinden
        # (`min_hacim_tl`, 50M) ve USD kote bir seriye uygulanirsa
        # SESSIZCE yanlis suzer — 50M USD devir, S&P 500'un bile
        # yalnizca ust dilimini birakir ve sonuc "likit hisselerde kural
        # calisiyor" diye okunurdu. Sessiz yanlis yerine GURULTULU RET.
        if venue.upper() != "BIST":
            raise ValueError(
                f"'{venue}' icin asgari_devir ACIKCA verilmeli: varsayilan "
                "esik TL cinsinden (`sources.isyatirim.min_hacim_tl`) ve "
                "farkli para biriminde kote bir evrene uygulanirsa sessizce "
                "yanlis suzer. USD evreni icin uretim degeri: "
                "`ibkr.strateji.asgari_devir.USD`.")
        try:
            asgari_devir = float((settings or _ayar()).get(
                "sources.isyatirim.min_hacim_tl", 50_000_000))
        except Exception:                             # noqa: BLE001
            asgari_devir = 50_000_000.0
    borsa_limiti = BORSA_LIMITI.get(venue.upper())
    evren = _evren(db, venue, endeksler=endeksler)
    if limit:
        evren = evren[:limit]

    hepsi: list[dict] = []
    kapsam = {"enstruman": 0, "atlanan": 0}
    seriler = {}
    for e in evren:
        seri = [dict(r) for r in db.fiyat_serisi(e["id"], limit=100000)]
        if len(seri) < 300:
            kapsam["atlanan"] += 1
            continue
        kapsam["enstruman"] += 1
        seriler[e["symbol"]] = seri
        for t in islemler(seri, borsa_limiti, asgari_devir=asgari_devir):
            if baslangic <= t["giris_ts"] <= bitis:
                hepsi.append({**t, "sembol": e["symbol"]})

    # KIYAS: ayni donemde AL-TUT. Strateji "kazandi" diyebilmek icin
    # hicbir sey yapmamaktan iyi olmali.
    from .backtest import _endeks_serisi
    endeks = _endeks_serisi(db, kiyas_kod)
    gunler = sorted(d for d in endeks if baslangic <= d <= bitis)
    al_tut = ((endeks[gunler[-1]] / endeks[gunler[0]] - 1) * 100
              if len(gunler) > 1 else None)

    o = ozet(hepsi, maliyet)

    # KONTROL GRUBU: ayni serilerde, ayni sayida, ayni surede RASTGELE
    # giris. Kuralin beklentisi bunun %5-%95 araligina dusuyorsa
    # "kural bir sey ekliyor" DENEMEZ.
    #
    # Sembol basina paylastiriliyor: tum islemleri tek bir seride
    # taklit etmek, o serinin kendi trendini kontrol grubuna tasirdi.
    kontrol = {"tur": 0}
    if hepsi and seriler:
        import collections
        sayac = collections.Counter(t["sembol"] for t in hepsi)
        toplam, agirlik = [], 0
        for sem, adet in sayac.items():
            r = rastgele_kontrol(seriler[sem], adet, o.get("ort_tutma_gun", 20),
                                 tur=50, maliyet=maliyet)
            if r.get("tur"):
                toplam.append((adet, r["ortalama_%"]))
                agirlik += adet
        if agirlik:
            kontrol = {
                "tur": 50, "sembol": len(toplam),
                # ISLEM SAYISIYLA AGIRLIKLI: cok islem uretmis bir
                # sembolun kontrolu de o kadar agirlik tasimali.
                "ortalama_%": round(
                    sum(a * v for a, v in toplam) / agirlik, 3),
            }
            kontrol["kural_farki_%"] = round(
                o.get("beklenti_%", 0) - kontrol["ortalama_%"], 3)

    return {"kapsam": kapsam, "ozet": o,
            # `if al_tut` DEGIL: 0.0 falsy'dir ve YATAY SEYREDEN bir
            # endeks "kiyas YOK" diye raporlanirdi. "Sifir" ile "veri
            # yok" ayri seyler — bu deponun `yanlis-yok-beyani` sinifi.
            "al_tut_endeks_%": round(al_tut, 1) if al_tut is not None else None,
            "pencere": {"baslangic": baslangic, "bitis": bitis},
            "rastgele_kontrol": kontrol,
            "aylik": aylik_kumelenme(hepsi, maliyet),
            "asgari_devir": asgari_devir,
            "_islemler": hepsi}
