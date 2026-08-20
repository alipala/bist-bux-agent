"""
BACKTEST — ama ilk cikti isabet tablosu DEGIL, GUC ANALIZI.

NEDEN BU SIRA
-------------
"Sinyal %57 isabet etti" cumlesi, arkasinda kac gozlem oldugu ve o
gozlemlerin gurultusu bilinmeden ANLAMSIZDIR. 60 gozlemde %57, %50'den
ayirt edilemez; ayni sayi 6.000 gozlemde bir kenardir. Bu yuzden once
sorulan soru "kazandi mi" degil:

    ELIMDEKI VERIYLE, VAR OLAN BIR KENARI GOREBILIR MIYDIM?

Cevap hayirsa isabet oranini raporlamak, olcum gibi gorunen bir gurultu
uretmektir — ve bu projede sayilar karar destegine giriyor.

BIST'IN "OLCULEMEZ" CIKMASI BEKLENEN VE DOGRU BIR SONUCTUR. 10 yillik
gecmis bile, gunluk oynakligi %3'e varan bir piyasada kucuk bir kenari
ayirt etmeye yetmeyebilir. Bunu "strateji calismiyor" diye okumak
YANLIS olur; dogru okuma "bu veriyle karar verilemez"dir.

UC ZORUNLU ONLEM
----------------
1. LOOK-AHEAD KAPALI. Sinyaller `Tarayici.fiyat_kurallari` ile, yani
   URETIMDEKI AYNI KODLA uretiliyor ve yalnizca o gune kadarki seri
   veriliyor. Kurallari burada yeniden yazmak en pahali hatayi
   uretirdi: iki tanim ayrisir ve backtest, uretimde CALISMAYAN bir
   stratejiyi "dogrulanmis" diye raporlar.
2. PIYASA ETKISI AYIKLANIYOR. Ham getiri kullanmak BIST'in kendi
   yukselisini sinyale mal ederdi. Olculen sey XU100'e GORE fazla
   getiri.
3. BAGIMSIZLIK VARSAYIMI ZORLANMIYOR. Ayni gun 300 hissede sinyal
   cikmasi 300 bagimsiz gozlem DEGILDIR — hepsi ayni piyasa gunune
   bakiyor. Gozlem birimi GUN; ve ufuk boyu ust uste binen pencereler
   icin gunler ufuk adiminda seyreltiliyor.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict

log = logging.getLogger(__name__)

# %80 guc, %5 iki yonlu anlamlilik icin standart normal katsayilari.
Z_ALFA = 1.959963985
Z_BETA = 0.8416212336

# Bu kadar bagimsiz gunluk gozlem altinda hicbir sey iddia edilmez.
ASGARI_GUN = 20

# Gunluk hareket bu esige ULASTIYSA o gun LIMITTE kapanmis sayilir ve
# o kapanistan giris YAPILAMAZ (tavanda satici, tabanda alici yok).
# BIST limiti ±%10; %9,5 pay birakiyor.
LIMIT_YAKIN = 0.095


def _std(x) -> float:
    if len(x) < 2:
        return 0.0
    ort = sum(x) / len(x)
    return math.sqrt(sum((v - ort) ** 2 for v in x) / (len(x) - 1))


def _endeks_serisi(db, kod: str = "XU100") -> dict[str, float]:
    r = db.query("SELECT id FROM instruments WHERE symbol=? AND venue='INDEX'",
                 (kod,))
    if not r:
        return {}
    return {x["ts"]: x["close"]
            for x in db.fiyat_serisi(r[0]["id"], limit=100000) if x["close"]}


def _evren(db, venue: str = "BIST", asgari_bar: int = 400) -> list:
    """
    Backtest evreni: DERIN serisi olan enstrumanlar.

    Sig serili kagit dislaniyor — 15 barlik yeni bir kotasyon (MASFN,
    QUICK) ne sinyal uretir ne de olcume katki verir, ama "kapsamda"
    gorunerek orani sulandirir.
    """
    return db.query(
        """SELECT i.id, i.symbol, i.name, i.venue, COUNT(*) n
           FROM instruments i JOIN prices p ON p.instrument_id = i.id
           WHERE i.venue = ? GROUP BY i.id HAVING n >= ?
           ORDER BY i.symbol""", (venue, asgari_bar))


def sinyalleri_topla(db, settings, baslangic: str, bitis: str,
                     ufuklar=(1, 5, 20), venue: str = "BIST",
                     limit: int | None = None) -> tuple[list[dict], dict]:
    """
    Gecmisteki her gun icin URETIMDEKI kurallari calistirir.

    Doner: (gozlemler, kapsam). Her gozlem bir sinyal ve onun ILERI
    getirisi — hem ham hem piyasaya gore fazla.
    """
    import pandas as pd

    from ..analysis import compute_indicators
    from ..pulse.screener import BORSA_LIMITI, MIN_BAR, Tarayici

    tarayici = Tarayici(settings, db)
    endeks = _endeks_serisi(db)
    evren = _evren(db, venue)
    if limit:
        evren = evren[:limit]

    gozlemler: list[dict] = []
    kapsam = {"enstruman": 0, "bar": 0, "sinyal": 0,
              "endeks_bar": len(endeks), "atlanan": 0, "islem_dusen": 0,
              "limitte": 0}
    azami = max(ufuklar)
    # `limit` evren sinirini tutuyor — borsa limiti AYRI ADLA.
    borsa_limiti = BORSA_LIMITI.get(venue.upper())
    cfg = settings.get("analysis.indicators", {})

    for e in evren:
        seri = db.fiyat_serisi(e["id"], limit=100000)
        if len(seri) < MIN_BAR + azami:
            kapsam["atlanan"] += 1
            continue
        seri = [dict(r) for r in seri]
        try:
            # GOSTERGELER TUM SERIDE BIR KEZ. Nedensel olduklari icin
            # (rolling/ewm/pct_change ileri bakmiyor) t anindaki deger,
            # seriyi t'de kesip hesaplamakla AYNIDIR — ve 900 bin kez
            # pandas cagirmaktan kurtariyor.
            ind = compute_indicators(pd.DataFrame(seri), cfg)
            rsi_dizi = list(ind["rsi"]) if "rsi" in ind else [None] * len(seri)
        except Exception as ex:                       # noqa: BLE001
            log.warning("[backtest] %s gostergesi hesaplanamadi: %s",
                        e["symbol"], ex)
            kapsam["atlanan"] += 1
            continue

        kapsam["enstruman"] += 1
        kapanislar = [r["close"] for r in seri]
        for t in range(MIN_BAR, len(seri) - azami):
            ts = seri[t]["ts"]
            if ts < baslangic or ts > bitis:
                continue
            kapsam["bar"] += 1
            # URETIMDEKI PENCERE: `fiyat_serisi(id, 300)` son 300 bari
            # veriyor. Backtest de tam onu vermeli, yoksa esikler (sd,
            # SMA, hacim ortalamasi) baska bir pencereden hesaplanir ve
            # olculen strateji uretimdeki olmaz.
            pencere = seri[max(0, t - 299):t + 1]
            rsi = rsi_dizi[t] if t < len(rsi_dizi) else None
            if rsi is not None and rsi != rsi:         # NaN
                rsi = None
            try:
                bulgular = tarayici.fiyat_kurallari(e, pencere, rsi)
            except Exception as ex:                   # noqa: BLE001
                log.debug("[backtest] %s @%s kural hatasi: %s",
                          e["symbol"], ts, ex)
                continue
            if not bulgular:
                continue

            p0 = kapanislar[t]
            if not p0:
                continue
            ileri = {}
            for h in ufuklar:
                p1 = kapanislar[t + h]
                if not p1:
                    continue
                # ILERI PENCEREDE SERMAYE ISLEMI VARSA GOZLEM DUSER.
                # Tarayici sinyal BARINI koruyor (bkz. BORSA_LIMITI) ama
                # bolunme sinyalden SONRAKI gunlerde olabilir: ADEL'in
                # 11:1 bolunmesi ileri getiriyi -%90,8 yapar ve tek bir
                # gozlem butun ortalamayi goturur.
                if borsa_limiti and any(
                        kapanislar[j] and kapanislar[j - 1]
                        and abs(kapanislar[j] / kapanislar[j - 1] - 1) > borsa_limiti
                        for j in range(t + 1, t + h + 1)):
                    kapsam["islem_dusen"] += 1
                    continue
                ham = p1 / p0 - 1
                # PIYASAYA GORE FAZLA GETIRI. Endeks o gunlerde yoksa
                # gozlem DUSURULUYOR, ham getiriye dusulmuyor: ikisini
                # ayni sutunda toplamak, olcumun yarisini sessizce
                # baska bir seye cevirirdi.
                e0, e1 = endeks.get(seri[t]["ts"]), endeks.get(seri[t + h]["ts"])
                if not e0 or not e1:
                    continue
                ileri[h] = ham - (e1 / e0 - 1)
            if not ileri:
                continue

            # SINYAL GUNUNUN KENDI HAREKETI — UYGULANABILIRLIK icin.
            #
            # BIST'te gunluk limit ±%10 ve limitte islem KILITLENIR:
            # tavanda satis tarafi bostur, tabanda alis tarafi. O
            # gunun kapanisindan giris yapilamaz.
            # Olculdu 2026-08-20: `olagandisi_hareket/yukari`
            # sinyallerinin %51'i tavan gununde cikiyor (ortalama
            # +%8,41). Onlari saymak, "1 gunde %1,4 kazandirir" gibi
            # UYGULANAMAZ bir sonuc uretir — ve backtest'in var olma
            # sebebi tam olarak bu tur sonuclari elemek.
            onceki = kapanislar[t - 1] if t else None
            gun_getirisi = (p0 / onceki - 1) if onceki else None
            limitte = (borsa_limiti is not None and gun_getirisi is not None
                       and abs(gun_getirisi) >= LIMIT_YAKIN)
            for b in bulgular:
                kapsam["sinyal"] += 1
                if limitte:
                    kapsam["limitte"] += 1
                gozlemler.append({"ts": ts, "sembol": e["symbol"],
                                  "tur": b["tur"], "yon": b["yon"],
                                  "guc": b["guc"], "fazla": ileri,
                                  "gun_getirisi": gun_getirisi,
                                  "limitte": limitte})
    return gozlemler, kapsam


def guc_analizi(gozlemler: list[dict], ufuklar=(1, 5, 20),
                yalniz_uygulanabilir: bool = False) -> list[dict]:
    """
    Her (tur, yon, ufuk) icin: ne gorebilirdik, ne gorduk.

    `mde_%` — MINIMUM AYIRT EDILEBILIR ETKI. Elimizdeki bagimsiz gozlem
    sayisi ve gurultuyle, %80 guc ve %5 anlamlilikta ancak bu buyuklukte
    bir etkiyi ayirt edebiliriz. Gozlenen etki bunun altindaysa "kenar
    yok" DEMEK DEGILDIR — "bu veriyle bilinemez" demektir. Ikisini ayni
    cumlede soylemek bu projenin en kotu hata sinifi.

    GOZLEM BIRIMI GUN, sinyal degil. Ayni gun 300 hissede cikan sinyal
    300 bagimsiz olcum degil; hepsi ayni piyasa gununun icinde. Gun
    ortalamasi alinip gunler seyreltiliyor (ufuk adiminda), boylece
    ust uste binen ileri pencereler de bagimsizlasiyor.
    """
    out = []
    gruplar: dict[tuple, list] = defaultdict(list)
    for g in gozlemler:
        if yalniz_uygulanabilir and g.get("limitte"):
            continue          # o kapanistan giris yapilamazdi
        gruplar[(g["tur"], g["yon"])].append(g)

    for (tur, yon), grup in sorted(gruplar.items()):
        for h in ufuklar:
            gunluk: dict[str, list[float]] = defaultdict(list)
            for g in grup:
                if h in g["fazla"]:
                    gunluk[g["ts"]].append(g["fazla"][h])
            if not gunluk:
                continue
            gunler = sorted(gunluk)
            # UST USTE BINMEYI KIR: ufuk h ise her h'inci gun alinir.
            secilen = gunler[::h] if h > 1 else gunler
            seri = [sum(gunluk[d]) / len(gunluk[d]) for d in secilen]
            n = len(seri)
            n_sinyal = sum(len(gunluk[d]) for d in secilen)
            if n < 2:
                continue
            ort = sum(seri) / n
            sd = _std(seri)
            se = sd / math.sqrt(n) if n else 0.0
            mde = (Z_ALFA + Z_BETA) * sd / math.sqrt(n) if n else float("inf")
            alt, ust = ort - Z_ALFA * se, ort + Z_ALFA * se

            z = ort / se if se else 0.0
            if n < ASGARI_GUN:
                karar = "OLCULEMEZ (gozlem az)"
            elif alt <= 0 <= ust:
                karar = "sifirdan ayirt edilemiyor"
            else:
                karar = "SIFIRDAN FARKLI"

            # SINYALIN YONU TUTUYOR MU? "yukari" pozitif, "asagi" negatif
            # fazla getiri bekler. Tutmuyorsa sinyal degersiz DEGIL —
            # TERSINE calisiyor olabilir ve bu, gormezden gelinecek bir
            # ayrinti degil bulgunun kendisidir.
            beklenen_isaret = 1 if yon == "yukari" else (-1 if yon == "asagi" else 0)
            yon_tutuyor = (beklenen_isaret == 0
                           or (ort > 0) == (beklenen_isaret > 0))

            out.append({
                "tur": tur, "yon": yon, "ufuk_gun": h,
                "sinyal": n_sinyal, "bagimsiz_gun": n,
                "gozlenen_%": round(ort * 100, 3),
                "ga_alt_%": round(alt * 100, 3), "ga_ust_%": round(ust * 100, 3),
                "mde_%": round(mde * 100, 3),
                "gurultu_sd_%": round(sd * 100, 3),
                "z": round(z, 2), "yon_tutuyor": yon_tutuyor,
                "karar": karar,
            })

    # COKLU TEST DUZELTMESI — HUCRE SAYISI BILINDIKTEN SONRA.
    #
    # 24 ayri hipotez test ediliyor. Duzeltmesiz %5 esikte, hepsi
    # gercekte sifir olsa bile ~1,2 hucrenin "anlamli" cikmasi
    # BEKLENIR. Tek bir yildizli satiri kanit saymak, gurultuyu bulgu
    # diye raporlamaktir. Bonferroni muhafazakar ama burada dogru
    # taraf: bu sayilar karar destegine giriyor.
    k = len(out)
    if k:
        from math import erf, sqrt
        for r in out:
            # iki yonlu p, standart normal
            p = 2 * (1 - 0.5 * (1 + erf(abs(r["z"]) / sqrt(2))))
            r["p"] = round(p, 5)
            r["bonferroni_gecti"] = bool(p * k < 0.05)
    return out


def kosu(db, settings, baslangic: str, bitis: str, ufuklar=(1, 5, 20),
         venue: str = "BIST", limit: int | None = None) -> dict:
    gozlemler, kapsam = sinyalleri_topla(db, settings, baslangic, bitis,
                                         ufuklar, venue, limit)
    tablo = guc_analizi(gozlemler, ufuklar)

    uyarilar = []
    if not kapsam["endeks_bar"]:
        uyarilar.append("XU100 serisi YOK — piyasa etkisi ayiklanamadi, "
                        "sonuclar kullanilamaz")
    olculebilir = [r for r in tablo if r["bagimsiz_gun"] >= ASGARI_GUN]
    if not olculebilir:
        uyarilar.append(f"hicbir hucrede {ASGARI_GUN} bagimsiz gun yok — "
                        "bu veriyle guc analizi bile yapilamaz")
    # COKLU TEST: her satir ayri bir hipotez. Duzeltmesiz bakildiginda
    # 24 hucrede bir tanesinin sansen "anlamli" cikmasi BEKLENIR.
    n_test = len(tablo)
    if n_test:
        uyarilar.append(
            f"{n_test} ayri hipotez test edildi; duzeltmesiz %5 esikte "
            f"sansen ~{n_test * 0.05:.1f} yanlis pozitif BEKLENIR. "
            "Tek bir 'SIFIRDAN FARKLI' satiri kanit sayilmaz.")
    # ISLEM MALIYETI SAYIYA DONUSTURULUYOR, "dahil degil" demek YETMEZ.
    # BIST'te perakende komisyon ~%0,1-0,2 (tek yon) + spread; gidis
    # donus makul bir varsayim %0,4. Bu esigin ALTINDA kalan bir etki
    # istatistiksel olarak "sifirdan farkli" olsa bile PARA KAZANDIRMAZ.
    maliyet = 0.40
    kucuk = [r for r in tablo
             if r["karar"] == "SIFIRDAN FARKLI"
             and abs(r["gozlenen_%"]) < maliyet]
    uyarilar.append(
        f"Islem maliyeti (gidis-donus ~%{maliyet:.2f}) DAHIL DEGIL. "
        f"'SIFIRDAN FARKLI' cikan {len(tablo and kucuk)} hucrenin etkisi "
        f"bu maliyetin ALTINDA — istatistiksel olarak var, PARASAL olarak yok.")
    ters = [r for r in tablo
            if r["karar"] == "SIFIRDAN FARKLI" and not r["yon_tutuyor"]]
    if ters:
        uyarilar.append(
            f"{len(ters)} hucrede etki ANLAMLI ama sinyalin YONU TERS — "
            "tarayici o kosulda yanlis yonu isaret ediyor olabilir.")
    # `_gozlemler` disari veriliyor: cagiran ayni kosuyu FARKLI
    # suzgeclerle (uygulanabilir / hepsi) yeniden analiz edebilsin
    # diye. Ikinci kez toplamak 83 saniye demekti.
    return {"kapsam": kapsam, "guc": tablo, "uyarilar": uyarilar,
            "_gozlemler": gozlemler,
            "pencere": {"baslangic": baslangic, "bitis": bitis}}
