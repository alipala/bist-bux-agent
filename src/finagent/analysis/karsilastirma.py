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


def _getiriler(barlar) -> dict[date, float]:
    """{tarih: gunluk getiri} — kapanistan kapanisa."""
    out: dict[date, float] = {}
    onceki = None
    for b in barlar:
        k = b["close"]
        if k is None or k <= 0:
            onceki = None                     # zinciri KIR, atlama uydurma
            continue
        if onceki is not None:
            out[_tarih(b["ts"])] = k / onceki - 1.0
        onceki = k
    return out


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


def getiri_ozeti(barlar) -> dict:
    """Tek serinin ozeti: kapsam, getiri, oynaklik, en derin dusus."""
    if len(barlar) < 2:
        return {"hata": f"yeterli bar yok ({len(barlar)})"}
    kapanis = [b["close"] for b in barlar if b["close"]]
    g = list(_getiriler(barlar).values())
    yb = _yillik_bar(barlar)

    # En derin dusus (drawdown): zirveden dibe, seri boyunca.
    zirve, en_kotu = kapanis[0], 0.0
    for k in kapanis:
        zirve = max(zirve, k)
        en_kotu = min(en_kotu, k / zirve - 1.0)

    return {
        "ilk_tarih": str(_tarih(barlar[0]["ts"])),
        "son_tarih": str(_tarih(barlar[-1]["ts"])),
        "bar": len(barlar),
        "ilk_fiyat": round(kapanis[0], 8),
        "son_fiyat": round(kapanis[-1], 8),
        "para_birimi": _al(barlar[-1], "currency"),
        "toplam_getiri_pct": round((kapanis[-1] / kapanis[0] - 1.0) * 100, 2),
        "yillik_oynaklik_pct": round(_std(g) * math.sqrt(yb) * 100, 1),
        "ortalama_gunluk_hareket_pct": round(
            (sum(abs(v) for v in g) / len(g)) * 100, 2) if g else None,
        "en_derin_dusus_pct": round(en_kotu * 100, 1),
        "yillik_bar_varsayimi": round(yb),
    }


def korelasyon(a_barlar, b_barlar) -> dict:
    """
    Iki serinin gunluk getiri korelasyonu + beta (b, a'ya gore).

    TARIH HIZALAMASI ZORUNLU. Iki diziyi indeksle yan yana koymak en
    sinsi hata: kripto yilda ~365, BIST ~250 bar uretir. Hizalanmadan
    hesaplanan bir korelasyon rakam olarak MAKUL gorunur ve tamamen
    anlamsizdir. Burada ORTAK TARIHLERDE ic birlesim yapiliyor.
    """
    ga, gb = _getiriler(a_barlar), _getiriler(b_barlar)
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


def korelasyon_matrisi(seriler: dict) -> dict:
    """{sembol: barlar} -> ikili korelasyon matrisi (ortak tarihlerde)."""
    adlar = sorted(seriler)
    mat: dict[str, dict[str, float | None]] = {}
    for a in adlar:
        mat[a] = {}
        for b in adlar:
            if a == b:
                mat[a][b] = 1.0
                continue
            r = korelasyon(seriler[a], seriler[b])
            mat[a][b] = r.get("korelasyon")
        # None kalan hucre "hesaplanamadi" demek, "0" DEMEK DEGIL.
    return mat


def pencere_istatistigi(barlar, hedef_pct: float, stop_pct: float,
                        ufuk_bar: int) -> dict:
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
    kapanis = [b["close"] for b in barlar if b["close"] and b["close"] > 0]
    n = len(kapanis)
    if n < ufuk_bar + ASGARI_ORTAK:
        return {"hata": f"{n} bar, en az {ufuk_bar + ASGARI_ORTAK} gerekir "
                        f"({ufuk_bar} ufuk + {ASGARI_ORTAK} pencere)"}

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
