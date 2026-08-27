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


def islemler(seri, borsa_limiti: float | None = 0.12,
             taban_kilidi: float | None = LIMIT_YAKIN,
             asgari_devir: float | None = None) -> list[dict]:
    """
    Bir enstrumanda Donchian 20/10 + 2N kurallarinin urettigi ISLEMLER.

    Her islem: giris/cikis tarihi, getiri, cikis SEBEBI, tutulan gun.

    `taban_kilidi` — limit-down gunlerinde cikis ERTELENIR. None
    verilirse eski (iyimser) davranis; yalnizca ONCE/SONRA karsilastirmasi
    icin, uretimde KULLANILMAZ.
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
                                "stop": bar["close"] - STOP_N * N, "N": N,
                                "giris_i": i, "girisde_tavan": bool(tavanda)}
            i += 1
            continue

        # --- pozisyondayiz: once STOP, sonra Donchian cikisi ----------
        sebep = None
        if bar["low"] is not None and bar["low"] <= pozisyon["stop"]:
            sebep = "2N stop"
        else:
            onceki = [k for k in kapanis[i - CIKIS_PENCERE:i] if k]
            if len(onceki) >= CIKIS_PENCERE and bar["close"] < min(onceki):
                sebep = "10 gun dip"
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
    return out


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


def kosu(db, baslangic: str, bitis: str, venue: str = "BIST",
         limit: int | None = None, maliyet: float = 0.004,
         asgari_devir: float | None = None, settings=None) -> dict:
    """
    `asgari_devir` — giris anindaki gunluk TL devir esigi. Verilmezse
    URETIM ESIGINDEN okunur (`sources.isyatirim.min_hacim_tl`, 50M):
    tarayici o esigin altindaki kagidi HIC taramiyor, yani backtest
    onlari sayarsa URETILEMEYECEK islemleri olcmus olur.

    Acikca 0 verilerek kapatilabilir — ONCE/SONRA karsilastirmasi icin.
    """
    from ..pulse.screener import BORSA_LIMITI
    from .backtest import _evren

    if asgari_devir is None:
        try:
            asgari_devir = float((settings or _ayar()).get(
                "sources.isyatirim.min_hacim_tl", 50_000_000))
        except Exception:                             # noqa: BLE001
            asgari_devir = 50_000_000.0
    borsa_limiti = BORSA_LIMITI.get(venue.upper())
    evren = _evren(db, venue)
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
    endeks = _endeks_serisi(db)
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
            "al_tut_endeks_%": round(al_tut, 1) if al_tut else None,
            "pencere": {"baslangic": baslangic, "bitis": bitis},
            "rastgele_kontrol": kontrol,
            "aylik": aylik_kumelenme(hepsi, maliyet),
            "asgari_devir": asgari_devir,
            "_islemler": hepsi}
