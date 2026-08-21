"""
COK SEMBOLLU / CAPRAZ VARLIK hesaplari — modelin KAFASINDA yapamadigi is.

NEDEN VAR (olculdu 2026-08-18 16:54): Ali "ADA, AVAX, SOL'u karsilastir,
1 ayda %5 cikar mi" diye sordu. 31 aracin hicbiri cok sembollu degildi,
hepsi tek sembol tek atis. Model bileşimi kendi yapmaya calisti:
`fiyat_serisi` uc kez cagirdi (sonuclar ~54 KB olduklari icin dosyaya
tasindi ve modele YOL verildi, VERI degil), sonra `Read`/`Bash`/`Agent`
ile o dosyalari okuyup hesaplamayi denedi. Uc arac da izinli listede
yok: **Bash 20 kez, Write 3, Read 1 kez reddedildi.** Model tek bir
fiyat bari gormedi ve yine de 335 pencerelik bir istatistik tablosu
yaziп "guvenim yuksek" dedi. Sayilar uydurmaydi.

DERS: hesap ARACTA durur. Modelin hesap yapmaya CALISMASI, aracin ona
hesap degil ham veri vermesinin belirtisiydi. Bu modul o boslugu
kapatiyor: buradaki her fonksiyon SAYI dondurur, bar dizisi dondurmez —
yan fayda olarak sonuclar kucuk kalir ve dosyaya tasinmaz.

Fonksiyonlar SAF: veritabani bilmezler, bar listesi alir sayi verirler.
Cagiran taraf seriyi `db.fiyat_serisi()` ile ceker (tek mesru yol —
ayni enstrumanda birden fazla para biriminde seri olabiliyor).
"""
from __future__ import annotations

import math
from datetime import date

# En az bu kadar ortak gozlem olmadan korelasyon/beta BEYAN EDILMEZ.
# 30 secildi cunku altinda tek bir aykiri gun katsayiyi tasiyor ve
# "0,84 korelasyon" gibi kesin gorunen bir sayi uretiyor.
ASGARI_ORTAK = 30


def _al(bar, anahtar, varsayilan=None):
    """
    Bar alani oku — `sqlite3.Row` VE `dict` ile calisir.

    `db.fiyat_serisi()` `sqlite3.Row` donduruyor ve Row'da `.get()` YOKTUR;
    testler ise duz dict veriyor. Tek erisim noktasi olmadan bu fark
    "testte gecti, canlida AttributeError" seklinde patlar.
    """
    try:
        v = bar[anahtar]
    except (KeyError, IndexError):
        return varsayilan
    return varsayilan if v is None else v


def _tarih(ts: str) -> date:
    return date.fromisoformat(str(ts)[:10])


def borsa_limiti(venue) -> float | None:
    """
    Bu borsanin gunluk fiyat limiti (oran) — yoksa None.

    TEK TANIM `pulse.screener.BORSA_LIMITI`; burada yalnizca okunuyor.
    Ikinci bir kopya yazmak, bu projenin tekrar eden kusur sinifi olurdu
    (beyan ile gercegin sessizce ayrismasi).

    None DONMESI ONEMLI: kriptoda ve ABD/Avrupa hisselerinde gunluk
    limit YOKTUR ve %20'lik bir gun GERCEK bir harekettir. Oralarda
    "buyuk hareket = sermaye islemi" varsayimi gercek veriyi silerdi.
    """
    from ..pulse.screener import BORSA_LIMITI
    return BORSA_LIMITI.get((venue or "").upper())


def sermaye_islemleri(barlar, limit: float | None) -> list[dict]:
    """
    Serideki sermaye islemi (bolunme/birlesme/bedelsiz) barlari.

    Olcut: gunluk degisim borsanin GUNLUK LIMITINI asiyorsa o bar bir
    fiyat hareketi DEGILDIR. BIST'te limit ±%10; %12 esigi gercek limit
    gunlerini (%10-11) elemeden bolunmeleri yakaliyor.
    """
    if not limit or len(barlar) < 2:
        return []
    out = []
    for i in range(1, len(barlar)):
        a, b = barlar[i - 1]["close"], barlar[i]["close"]
        if not a or not b or a <= 0 or b <= 0:
            continue
        oran = b / a
        if abs(oran - 1.0) > limit:
            out.append({"ts": str(barlar[i]["ts"])[:10], "i": i, "oran": oran,
                        "onceki": a, "sonraki": b})
    return out


def son_kesintisiz(barlar, limit: float | None) -> tuple[list, dict]:
    """
    SON SERMAYE ISLEMINDEN BUYANA kesintisiz segment. Doner: (barlar, rapor).

    NEDEN DUZELTME DEGIL DE KESME — OLCUME DAYANIYOR
    ------------------------------------------------
    Ilk tasarim seriyi geriye donuk DUZELTIYORDU (bolunme oraniyla
    olceklemek). Vazgecildi, cunku olcum bunun guvenli olmadigini
    gosterdi: BIST'te gunluk limiti asan barlarin dagilimi (tum evren,
    2026-08-21) TEMIZ BIR AYRIM VERMIYOR —

        %12-15   655 bar     %25-35    26 bar
        %15-20   878 bar     %35-50    12 bar
        %20-25   210 bar     >%50      28 bar

    Alt kovalar bedelsiz sermaye artirimlariyla GERCEK ekstrem gunlerin
    KARISIMI. Somut ornek: ADEL 2020-03-12'de -%14,2 — bu COVID cokusu,
    bolunme degil. Duzeltme yapsaydik o tarihten ONCEKI tum gecmisi
    kalici olarak %14 kaydirirdik.

    MALIYET ASIMETRIK:
      * Duzeltmede yanlis pozitif -> TUM gecmis kalici bozulur.
      * Kesmede/dislamada yanlis pozitif -> bir gunluk veri kaybi.
    Belirsiz bir tespitle yapilacak dogru is, ihtiyatli olandir. Bu,
    projenin "eksik seri, yanlis seriden iyidir" ilkesinin ta kendisi.

    SEVIYE isteyen hesaplar (SMA, RSI, en derin dusus) bu segmenti
    kullanir: bolunmeyi ASAN bir SMA200, hicbir gun gorulmemis bir
    ortalamadir. Segment kisa kalirsa cagiran taraf bunu SOYLER.
    """
    olaylar = sermaye_islemleri(barlar, limit)
    rapor = {"sermaye_islemi": len(olaylar),
             "tarihler": [o["ts"] for o in olaylar]}
    if not olaylar:
        return list(barlar), rapor
    kes = olaylar[-1]["i"]
    rapor["kesildi"] = True
    rapor["atilan_bar"] = kes
    rapor["segment_baslangici"] = str(barlar[kes]["ts"])[:10]
    return list(barlar[kes:]), rapor


def _getiriler(barlar, limit: float | None = None) -> dict[date, float]:
    """
    {tarih: gunluk getiri} — kapanistan kapanisa.

    `limit` verilirse SERMAYE ISLEMI gunleri DISARIDA kalir: 335,50'den
    30,75'e inen bir bar bir fiyat hareketi degil, 11'e 1 bolunmedir ve
    getiri serisine girerse oynakligi, korelasyonu ve toplam getiriyi
    goturur. Zincir o gunde KIRILIR (bir sonraki gun yeni bir baslangic
    sayilir), boylece bolunmeyi ATLAYAN sahte bir getiri de uretilmez.
    """
    out: dict[date, float] = {}
    onceki = None
    for b in barlar:
        k = b["close"]
        if k is None or k <= 0:
            onceki = None                     # zinciri KIR, atlama uydurma
            continue
        if onceki is not None:
            g = k / onceki - 1.0
            if limit and abs(g) > limit:
                onceki = k                    # sermaye islemi: gunu ATLA
                continue
            out[_tarih(b["ts"])] = g
        onceki = k
    return out


def zincir_getiri(barlar, limit: float | None = None) -> float | None:
    """
    Toplam getiri — ILK/SON DEGIL, gunluk getirilerin CARPIMI.

    Ilk fiyati son fiyata bolmek bolunmenin ustunden atlar ve ADEL'de
    "-%82,2" gibi tamamen uydurma bir sayi verir. Zincirleme, sermaye
    islemi gunlerini dislayip geri kalani carpar.
    """
    g = list(_getiriler(barlar, limit).values())
    if not g:
        return None
    c = 1.0
    for x in g:
        c *= (1.0 + x)
    return (c - 1.0) * 100.0


def _yillik_bar(barlar) -> float:
    """
    Seriden TURETILEN yillik bar sayisi — sabit 252 YAZMA.

    Kripto 7/24 (~365 bar/yil), BIST ~250, AEX ~255. Oynakligi sabit
    252 ile yillıklastirmak kriptoyu SISTEMATIK olarak dusuk gosterir
    (365/252 = 1,20 kat) ve iki varlik sinifi yan yana konunca
    karsilastirma sessizce yanlis cikar. Seriden turetmek kendini
    duzeltiyor.
    """
    if len(barlar) < 2:
        return 252.0
    gun = (_tarih(barlar[-1]["ts"]) - _tarih(barlar[0]["ts"])).days
    if gun <= 0:
        return 252.0
    return max(1.0, len(barlar) / (gun / 365.25))


def _std(x: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    ort = sum(x) / n
    return math.sqrt(sum((v - ort) ** 2 for v in x) / (n - 1))


def getiri_ozeti(barlar, limit: float | None = None) -> dict:
    """
    Tek serinin ozeti: kapsam, getiri, oynaklik, en derin dusus.

    `limit` — borsanin gunluk fiyat limiti (`borsa_limiti(venue)`).
    Verilirse sermaye islemleri hesaba KATILMAZ:
      * getiri ve oynaklik o gunler DISLANARAK hesaplanir (zincirleme)
      * en derin dusus SON KESINTISIZ segmentte olculur — bolunmeyi
        asan bir zirve-dip mesafesi %96 gibi tamamen sahte cikar
        (ADEL'de olculdu: -%96,5 -> gercekte -%63,5)
    """
    if len(barlar) < 2:
        return {"hata": f"yeterli bar yok ({len(barlar)})"}
    kapanis = [b["close"] for b in barlar if b["close"]]
    g = list(_getiriler(barlar, limit).values())
    yb = _yillik_bar(barlar)

    # EN DERIN DUSUS SEVIYE olcusudur: getiri dislamasi onu duzeltmez,
    # cunku bolunme sonrasi fiyat oncekinin 11'de biri ve zirve-dip
    # mesafesi otomatik olarak %90 cikar. Bu yuzden SON KESINTISIZ
    # segmentte olculuyor.
    seg, seg_rapor = son_kesintisiz(barlar, limit)
    seg_k = [b["close"] for b in seg if b["close"]] or kapanis
    zirve, en_kotu = seg_k[0], 0.0
    for k in seg_k:
        zirve = max(zirve, k)
        en_kotu = min(en_kotu, k / zirve - 1.0)

    out = {
        "ilk_tarih": str(_tarih(barlar[0]["ts"])),
        "son_tarih": str(_tarih(barlar[-1]["ts"])),
        "bar": len(barlar),
        "ilk_fiyat": round(kapanis[0], 8),
        "son_fiyat": round(kapanis[-1], 8),
        "para_birimi": _al(barlar[-1], "currency"),
        "toplam_getiri_pct": (round(zincir_getiri(barlar, limit), 2)
                              if g else None),
        "yillik_oynaklik_pct": round(_std(g) * math.sqrt(yb) * 100, 1),
        "ortalama_gunluk_hareket_pct": round(
            (sum(abs(v) for v in g) / len(g)) * 100, 2) if g else None,
        "en_derin_dusus_pct": round(en_kotu * 100, 1),
        "yillik_bar_varsayimi": round(yb),
    }
    if seg_rapor.get("sermaye_islemi"):
        out["sermaye_islemi"] = seg_rapor["sermaye_islemi"]
        out["sermaye_islemi_tarihleri"] = seg_rapor["tarihler"]
        # ILK FIYAT ILE TOPLAM GETIRI ARTIK BIRBIRINE BOLUNMUYOR:
        # okuyan taraf "17,14'ten 33,36'ya %94" diye kontrol edip
        # tutmadigini gorurse veriye guvenmez. Acikca soyleniyor.
        out["not_getiri"] = (
            "toplam getiri ILK/SON fiyattan DEGIL, gunluk getirilerin "
            "carpimindan hesaplandi; sermaye islemi gunleri disarida. "
            "ilk_fiyat/son_fiyat orani bu sayiyi VERMEZ.")
        out["en_derin_dusus_penceresi"] = seg_rapor.get("segment_baslangici")
    return out


def korelasyon(a_barlar, b_barlar, a_limit: float | None = None,
               b_limit: float | None = None) -> dict:
    """
    Iki serinin gunluk getiri korelasyonu + beta (b, a'ya gore).

    LIMITLER AYRI: iki seri farkli borsalarda olabilir (BIST'in gunluk
    limiti var, kriptonun yok). Tek bir limit gecirmek, kripto tarafinda
    GERCEK %20'lik gunleri sermaye islemi sanip silerdi.

    TARIH HIZALAMASI ZORUNLU. Iki diziyi indeksle yan yana koymak en
    sinsi hata: kripto yilda ~365, BIST ~250 bar uretir. Hizalanmadan
    hesaplanan bir korelasyon rakam olarak MAKUL gorunur ve tamamen
    anlamsizdir. Burada ORTAK TARIHLERDE ic birlesim yapiliyor.
    """
    ga = _getiriler(a_barlar, a_limit)
    gb = _getiriler(b_barlar, b_limit)
    ortak = sorted(set(ga) & set(gb))
    if len(ortak) < ASGARI_ORTAK:
        return {"hata": f"ortak gozlem {len(ortak)} < {ASGARI_ORTAK} — "
                        "korelasyon beyan edilmedi",
                "ortak_gun": len(ortak)}
    x = [ga[t] for t in ortak]
    y = [gb[t] for t in ortak]
    sx, sy = _std(x), _std(y)
    if sx == 0 or sy == 0:
        return {"hata": "seride hareket yok", "ortak_gun": len(ortak)}
    ox, oy = sum(x) / len(x), sum(y) / len(y)
    kov = sum((a - ox) * (b - oy) for a, b in zip(x, y)) / (len(ortak) - 1)
    beta = kov / (sx * sx)

    # BETA SISMESI — olculdu 2026-08-18. `maruziyet` ilk kosusunda USDTRY
    # portfoy betasini **-9,41** verdi. Sayi implausible ve sebebi
    # metodolojik: beta = kov/var(a) ve USDTRY gunluk oynakligi %0,09,
    # ROSE'un %5,68'inin 60'ta biri. Kucuk bir varyansa bolmek betayi
    # sisiriyor; cikan sayi "buyuk maruziyet" DEGIL, bolmenin eseri.
    #
    # Iki karsi onlem:
    #  1. Iki serinin oynakligi DA donuyor — okuyan taraf olcegi gorur.
    #  2. `bir_sigma_etki_pct`: "a bir standart sapma oynarsa b tarihsel
    #     olarak yuzde kac oynadi". Bu VERININ ICINDE bir ifade; "a %1
    #     oynarsa" ise USDTRY icin 11 sigmalik bir EKSTRAPOLASYON.
    guvenilir = sx >= sy / 3 if sy > 0 else True
    return {
        "ortak_gun": len(ortak),
        "ilk_ortak": str(ortak[0]),
        "son_ortak": str(ortak[-1]),
        "korelasyon": round(kov / (sx * sy), 3),
        "beta": round(beta, 3),
        "a_gunluk_oynaklik_pct": round(sx * 100, 3),
        "b_gunluk_oynaklik_pct": round(sy * 100, 3),
        "bir_sigma_etki_pct": round(beta * sx * 100, 2),
        "beta_guvenilir_mi": guvenilir,
        "beta_uyarisi": None if guvenilir else (
            f"a'nin oynakligi (%{sx*100:.2f}) b'nin ({sy*100:.2f}) "
            "ucte birinden kucuk — beta buyuk cikar ama bu maruziyet "
            "degil olcek farkidir. KORELASYONA ve bir_sigma_etki_pct'ye bak."),
        # PARA BIRIMI UYARISI: TRY serisi ile USD serisinin getiri
        # korelasyonu kur hareketini DE icerir. Bastirmak yerine beyan.
        "para_birimleri": sorted({str(_al(a_barlar[-1], "currency")),
                                  str(_al(b_barlar[-1], "currency"))}),
    }


def korelasyon_matrisi(seriler: dict, limitler: dict | None = None) -> dict:
    """{sembol: barlar} -> ikili korelasyon matrisi (ortak tarihlerde)."""
    adlar = sorted(seriler)
    lim = limitler or {}
    mat: dict[str, dict[str, float | None]] = {}
    for a in adlar:
        mat[a] = {}
        for b in adlar:
            if a == b:
                mat[a][b] = 1.0
                continue
            r = korelasyon(seriler[a], seriler[b], lim.get(a), lim.get(b))
            mat[a][b] = r.get("korelasyon")
        # None kalan hucre "hesaplanamadi" demek, "0" DEMEK DEGIL.
    return mat


def pencere_istatistigi(barlar, hedef_pct: float, stop_pct: float,
                        ufuk_bar: int, limit: float | None = None) -> dict:
    """
    "N gunde %X kara gecer miyim" sorusunun GERCEK cevabi.

    Her bar bir giris kabul edilir, sonraki `ufuk_bar` bara bakilir ve
    sayilir: hedefe degdi mi, once stop'a mi dustu, ufuk SONUNDA nerede.

    LOOK-AHEAD YOK: pencere t+1'den baslar (giris barinin kendi kapanisi
    hedefe dahil edilmez) ve son `ufuk_bar` bar TAM pencere kuramadigi
    icin DISARIDA BIRAKILIR. Kac pencere kullanildigi doner — modelin
    uydurdugu tabloda "335 pencere" yaziyordu ve aritmetigi dogruydu
    (365-30), sayilar uydurmaydi; o yuzden burada sayim da BEYAN EDILIR.

    ONCELIK SIRASI ONEMLI: ayni gun icinde hem hedef hem stop gorulebilir.
    Gunluk kapanis serisiyle hangisinin ONCE oldugu BILINEMEZ, o yuzden
    kapanis bazli bakiliyor ve bu sinir beyan ediliyor.
    """
    # SERMAYE ISLEMI ICEREN PENCERE HESABA GIRMEZ.
    #
    # Bu hesap SEVIYE karsilastiriyor ("giristen %10 asagi dustu mu"),
    # yani bir bolunme penceresi otomatik olarak "stop'a dustu" sayilir.
    # ADEL 2 Ocak 2024'te 335,50'den 30,75'e "dustu": o tarihi kapsayan
    # her pencere sahte bir stop uretirdi. Cozum, son kesintisiz
    # segmentte hesaplamak — kesilen kisim BEYAN ediliyor.
    seg, seg_rapor = son_kesintisiz(barlar, limit)
    barlar = seg
    kapanis = [b["close"] for b in barlar if b["close"] and b["close"] > 0]
    n = len(kapanis)
    if n < ufuk_bar + ASGARI_ORTAK:
        eksik = {"hata": f"{n} bar, en az {ufuk_bar + ASGARI_ORTAK} gerekir "
                         f"({ufuk_bar} ufuk + {ASGARI_ORTAK} pencere)"}
        if seg_rapor.get("kesildi"):
            eksik["sebep"] = (
                f"seri {seg_rapor['segment_baslangici']} tarihindeki sermaye "
                "isleminden kesildi; oncesi seviye olarak "
                "karsilastirilabilir degil")
            eksik.update({k: seg_rapor[k] for k in
                          ("sermaye_islemi", "tarihler")})
        return eksik

    hedef, stop = 1 + hedef_pct / 100.0, 1 - abs(stop_pct) / 100.0
    hedefe_dokundu = stop_once = sonunda_hedefte = 0
    son_getiri: list[float] = []
    dusus: list[float] = []
    pencere = 0

    for i in range(n - ufuk_bar):
        giris = kapanis[i]
        ileri = kapanis[i + 1: i + 1 + ufuk_bar]
        if len(ileri) < ufuk_bar:
            break
        pencere += 1
        oran = [k / giris for k in ileri]

        h_idx = next((j for j, o in enumerate(oran) if o >= hedef), None)
        s_idx = next((j for j, o in enumerate(oran) if o <= stop), None)
        if h_idx is not None:
            hedefe_dokundu += 1
        if s_idx is not None and (h_idx is None or s_idx < h_idx):
            stop_once += 1
        if oran[-1] >= hedef:
            sonunda_hedefte += 1
        son_getiri.append((oran[-1] - 1) * 100)
        dusus.append((min(oran) - 1) * 100)

    def medyan(x):
        s = sorted(x)
        m = len(s) // 2
        return round(s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2, 2)

    # BASABAS ISABET: kazan +hedef, kaybet -stop ise bu kurgunun kara
    # gecmesi icin gereken en az isabet orani. Modelin cevabinda bu
    # hesap DOGRUYDU, girdileri uydurmaydi — girdiyi burasi veriyor.
    basabas = abs(stop_pct) / (hedef_pct + abs(stop_pct)) * 100

    return {
        "pencere_sayisi": pencere,
        "kullanilan_bar": n,
        "ufuk_bar": ufuk_bar,
        "hedef_pct": hedef_pct,
        "stop_pct": -abs(stop_pct),
        "hedefe_dokundu_pct": round(hedefe_dokundu / pencere * 100, 1),
        "hedeften_once_stop_pct": round(stop_once / pencere * 100, 1),
        "ufuk_sonunda_hedefte_pct": round(sonunda_hedefte / pencere * 100, 1),
        "ufuk_sonu_getiri_medyan_pct": medyan(son_getiri),
        "pencere_ici_dusus_medyan_pct": medyan(dusus),
        "basabas_isabet_pct": round(basabas, 1),
        "beklenen_deger_pozitif_mi":
            (hedefe_dokundu / pencere * 100) > basabas,
        "sinir": "gunluk KAPANIS serisi — ayni gun icinde hedef ve stop "
                 "birlikte gorulduyse hangisinin once oldugu bilinemez",
    }
