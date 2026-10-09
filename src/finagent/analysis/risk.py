"""
RISK BUTCESI VE SENARYO (plan adim 3/5, 9 Eki) — "ne kadar kaybedebilirim,
risk nerede toplaniyor, X olursa ne olur".

NE YAPAR
--------
Bugunku portfoyu (hesaplar arasi, EUR) son ~1 yilin fiyat hareketleriyle
"bu portfoyu bir yildir tutsaydin" diye YENIDEN OYNATIR ve olcer:
  * oynaklik, en kotu gun, en kotu ay, "20 aydan 1'inde" ay kaybi, en derin dusus
    -> yatirim politikasindaki tahammulle (-%10 hedef, -%20 azami) kiyas
  * RISK KATKISI: her kalemin portfoy oynakligina payi (agirlik DEGIL) ve
    birlikte hareket eden kalemlerin KUMESI
  * SENARYO: Nasdaq / BIST soku -> portfoy betasi x sok; dolar / TL soku ->
    kayit para birimi agirligi x sok (muhasebe, tahmin degil)
  * TARIHSEL STRES: bugunku karisim 2022 ve 2020 Mart'ta ne yapardi

NE YAPMAZ — tahmin. Hicbir sayi "olacak" demiyor; hepsi gecmis hareketin
bugunku agirliklarla tekrari. YENI TAHMIN KATMANI YOK (plan karari).

KANIT (Ali'ye soylenen olcut): beta modeli, beta yalnizca O GUNDEN ONCEKI
veriyle hesaplanip gercek cokus gunlerine uygulandiginda gercek kaybi
modelin kendi belirsizlik bandi icinde vermeli -> `geriye_sinama`.

BILINEN SINIRLAR (ciktida beyan edilir)
  * Sabit agirlik: bugunku agirliklar gecmise uygulanir (alim-satim yok sayilir).
  * Kur: getiriler EUR'ya kayit para birimi serisiyle cevrilir. EUR'da
    islem goren ama ABD hissesi tutan fonlarin (CNDX, VUSA) dolar riski
    fiyatin ICINDE — kur senaryosunun muhasebe kisminda SAYILMAZ (alt sinir).
  * Pencere kisa (~1 yil, EURUSD serisi 2025-08'den): 2022 gibi bir ayi
    piyasasi bu pencerede YOK; onu `tarihsel_stres` ayrica olcer.

Fonksiyonlar SAF (db bilmez); `portfoy_riski` tek db girisidir.
"""
from __future__ import annotations

import bisect
import logging
import math
from datetime import date, timedelta

from . import karsilastirma as K

log = logging.getLogger(__name__)

PENCERE_GUN = 365          # takvim gunu; geriye oynatma penceresi
AY_GUN = 30                # takvim gunu; "bir ay" penceresi
ASGARI_GOZLEM = 120        # bunun altinda istatistik BEYAN EDILMEZ
KUME_ESIGI = 0.6           # bu korelasyonun ustundeki kalemler ayni kume
SINAMA_PENCERE = 250       # geriye sinamada beta icin EN FAZLA onceki gozlem
SINAMA_GUN = 6             # geriye sinamada faktorun en kotu kac gunu
# Faktor portfoyun gunluk hareketinin en az bu kadarini aciklamiyorsa
# senaryo sayisi verilmez. NEDEN IKINCI KAPI: 3-6 gunluk geriye sinama
# TEK BASINA zayif — testte bagimsiz (rastgele) bir faktor naif tahmini
# sans eseri gecti (0,66'ya 0,82 puan). Canli: Nasdaq R² 0,58, BIST 0,10.
ASGARI_R2 = 0.2

# Kur cevirisi: kayit para birimi -> (EUR basina kac birim) serisi.
KUR_SERISI = {"USD": "EURUSD", "USDT": "EURUSD", "TRY": "EURTRY"}

# Senaryolar. `tur`: 'beta' -> portfoy getirisinin faktore regresyonu;
# 'kur' -> o para biriminde kayitli kalemlerin EUR degeri dogrudan.
SENARYOLAR = (
    {"ad": "Nasdaq -%20", "tur": "beta", "faktor": "QQQ", "sok": -0.20},
    {"ad": "BIST -%20", "tur": "beta", "faktor": "XU100", "sok": -0.20},
    {"ad": "Dolar euroya karsi -%10", "tur": "kur", "para": ("USD", "USDT"), "sok": -0.10},
    {"ad": "USDTRY +%15 (TL -%13)", "tur": "kur", "para": ("TRY",), "sok": 1 / 1.15 - 1},
)

STRES_DONEMLERI = (
    {"ad": "2022 teknoloji ayisi", "bas": "2021-12-31", "bit": "2022-10-14"},
    {"ad": "2020 Mart covid", "bas": "2020-02-19", "bit": "2020-03-23"},
)


def _t(ts) -> date:
    return date.fromisoformat(str(ts)[:10])


# ---------------------------------------------------------------- EUR getiri

def kur_ceviricisi(fx_barlar) -> callable:
    """
    SAF. `fx_barlar` (EUR basina birim, artan) -> f(tarih) = o gun ya da
    ONCESINDEKI son kur (hafta sonu/tatil icin ileri tasima). Seri o
    tarihten once baslamiyorsa None.
    """
    tar = [_t(b["ts"]) for b in fx_barlar if b["close"]]
    deg = [float(b["close"]) for b in fx_barlar if b["close"]]

    def f(t: date):
        i = bisect.bisect_right(tar, t) - 1
        return deg[i] if i >= 0 else None
    return f


def eur_getirileri(barlar, limit: float | None = None, kur=None) -> dict[date, float]:
    """
    SAF. {tarih: EUR cinsinden gunluk getiri}.

    SERMAYE ISLEMI KONTROLU YEREL GETIRIDE yapilir (limit borsanin kendi
    para birimindeki tavan): once ceviri yapilsaydi, tavan yapan bir BIST
    hissesi + TL hareketi %10,3 cikip BOLUNME sanilarak silinirdi.
    `kur` None ise seri zaten EUR'dur.
    """
    out: dict[date, float] = {}
    onceki = None
    for b in barlar:
        k = b["close"]
        if k is None or k <= 0:
            onceki = None
            continue
        t = _t(b["ts"])
        if onceki is not None:
            k0, t0 = onceki
            g = k / k0 - 1.0
            if limit and abs(g) > limit:
                onceki = (k, t)
                continue
            if kur is not None:
                f0, f1 = kur(t0), kur(t)
                if not f0 or not f1:
                    onceki = (k, t)
                    continue                  # kur yoksa gun YOK, uydurma yok
                g = (1.0 + g) * f0 / f1 - 1.0
            out[t] = g
        onceki = (k, t)
    return out


def kapsam_basi(kalemler: list[dict], esik: float = 0.9) -> date | None:
    """
    SAF. Getirisi olan kalemlerin agirligi ILK KEZ `esik`e ulastigi tarih.
    Daha oncesinde seri kismi: kur serisi (EURUSD 2025-08) baslamadan USD
    kalemleri 0 sayilir ve beta SAHTE DUSUK cikar (olculdu 9 Eki: Mart'ta
    0,42, kapsam tamamlaninca 0,96).
    """
    ilk = sorted((min(k["getiri"]), k["agirlik"]) for k in kalemler if k["getiri"])
    w = 0.0
    for t, a in ilk:
        w += a
        if w >= esik - 1e-9:
            return t
    return None


def portfoy_getirisi(kalemler: list[dict], bas: date, bit: date) -> dict[date, float]:
    """
    SAF. kalemler: [{agirlik, getiri: {tarih: r}}]. Bas..bit arasindaki
    her tarihte agirlikli toplam. O gun hareketi olmayan kalem (tatil,
    hafta sonu) 0 sayilir — fiyati degismemistir.
    """
    gunler = sorted({t for k in kalemler for t in k["getiri"] if bas < t <= bit})
    return {t: sum(k["agirlik"] * k["getiri"].get(t, 0.0) for k in kalemler)
            for t in gunler}


# ---------------------------------------------------------------- istatistik

def _bilesik(x: list[float]) -> float:
    c = 1.0
    for v in x:
        c *= 1.0 + v
    return c - 1.0


def _yuzdelik(x: list[float], q: float) -> float:
    s = sorted(x)
    i = q * (len(s) - 1)
    a, b = int(math.floor(i)), int(math.ceil(i))
    return s[a] + (s[b] - s[a]) * (i - a)


def kayip_istatistigi(seri: dict[date, float]) -> dict:
    """
    SAF. Portfoy getiri serisinden kayip olculeri (yuzde). Gozlem
    ASGARI_GOZLEM'den azsa {'hata': ...}.

    'kotu_ay_20de1': kayan 30 takvim gunluk getirilerin %5'lik dilimi —
    ortusen pencereler, BAGIMSIZ 240 ay DEGIL; ciktida yazilir.
    """
    gun = sorted(seri)
    g = [seri[t] for t in gun]
    if len(g) < ASGARI_GOZLEM:
        return {"hata": f"gozlem {len(g)} < {ASGARI_GOZLEM} — risk beyan edilmedi",
                "gozlem": len(g)}
    yil = max((gun[-1] - gun[0]).days / 365.25, 1 / 365.25)
    yb = len(g) / yil
    # AY TAKVIMLE: kripto hafta sonlari seriye gun ekliyor; "21 gozlem"
    # o zaman ~3 hafta olurdu. Her pencere (t-30 gun, t].
    aylik, bitis, j = [], [], 0
    for i, t in enumerate(gun):
        if (t - gun[0]).days < AY_GUN:
            continue
        while (t - gun[j]).days >= AY_GUN:
            j += 1
        aylik.append(_bilesik(g[j:i + 1]))
        bitis.append(t)
    if not aylik:
        return {"hata": "bir aylik pencere yok", "gozlem": len(g)}
    seviye, zirve, dusus = 1.0, 1.0, 0.0
    for v in g:
        seviye *= 1.0 + v
        zirve = max(zirve, seviye)
        dusus = min(dusus, seviye / zirve - 1.0)
    en_kotu_gun = min(range(len(g)), key=lambda i: g[i])
    en_kotu_ay = min(range(len(aylik)), key=lambda i: aylik[i])
    return {
        "ilk": str(gun[0]), "son": str(gun[-1]), "gozlem": len(g),
        "yillik_oynaklik_%": round(K._std(g) * math.sqrt(yb) * 100, 1),
        "en_kotu_gun_%": round(g[en_kotu_gun] * 100, 2),
        "en_kotu_gun": str(gun[en_kotu_gun]),
        "en_kotu_ay_%": round(aylik[en_kotu_ay] * 100, 1),
        "en_kotu_ay_bitis": str(bitis[en_kotu_ay]),
        "kotu_ay_20de1_%": round(_yuzdelik(aylik, 0.05) * 100, 1),
        "en_derin_dusus_%": round(dusus * 100, 1),
        # BUGUN zirvenin ne kadar altinda: e2e'de model en derin dususu
        # "simdiki durum" sanip uydurma bir zirve degeri turetti (9 Eki).
        "simdi_zirveden_%": round((seviye / zirve - 1.0) * 100, 1),
        "donem_getirisi_%": round((seviye - 1.0) * 100, 1),
    }


def risk_katkisi(kalemler: list[dict], seri: dict[date, float]) -> list[dict]:
    """
    SAF. Her kalemin portfoy VARYANSINA payi (toplami %100):
        pay_i = w_i * kov(r_i, r_p) / var(r_p)
    Agirlik %15 olan bir kagit riskin %25'ini tasiyabilir; tersi de olur
    (altin, tahvil). Negatif pay = portfoyu DENGELIYOR.
    """
    gun = sorted(seri)
    rp = [seri[t] for t in gun]
    n = len(rp)
    if n < ASGARI_GOZLEM:
        return []
    op = sum(rp) / n
    var = sum((v - op) ** 2 for v in rp) / (n - 1)
    if var <= 0:
        return []
    out = []
    for k in kalemler:
        ri = [k["getiri"].get(t, 0.0) for t in gun]
        oi = sum(ri) / n
        kov = sum((a - oi) * (b - op) for a, b in zip(ri, rp)) / (n - 1)
        out.append({"sembol": k["sembol"], "agirlik_%": round(k["agirlik"] * 100, 1),
                    "risk_payi_%": round(k["agirlik"] * kov / var * 100, 1)})
    return sorted(out, key=lambda x: -x["risk_payi_%"])


def kumeler(kalemler: list[dict], katki: list[dict], esik: float = KUME_ESIGI) -> list[dict]:
    """
    SAF. Gunluk getiri korelasyonu `esik`in ustundeki kalemleri tek
    baglantiyla ayni kumeye toplar; kumenin agirligi ve RISK payi. Tek
    elemanli kumeler donmez (yogunlasma yalnizca birlikte hareket edende).
    Korelasyon ASGARI ortak gozlem altindaysa baglanti KURULMAZ.
    """
    adlar = [k["sembol"] for k in kalemler]
    ebeveyn = {a: a for a in adlar}

    def kok(a):
        while ebeveyn[a] != a:
            ebeveyn[a] = ebeveyn[ebeveyn[a]]
            a = ebeveyn[a]
        return a
    for i, a in enumerate(kalemler):
        for b in kalemler[i + 1:]:
            r = _kor(a["getiri"], b["getiri"])
            if r is not None and r >= esik:
                ebeveyn[kok(a["sembol"])] = kok(b["sembol"])
    pay = {x["sembol"]: x for x in katki}
    grup: dict[str, list[str]] = {}
    for a in adlar:
        grup.setdefault(kok(a), []).append(a)
    out = []
    for uyeler in grup.values():
        if len(uyeler) < 2:
            continue
        out.append({"kalemler": sorted(uyeler, key=lambda s: -pay.get(s, {}).get("agirlik_%", 0)),
                    "agirlik_%": round(sum(pay.get(s, {}).get("agirlik_%", 0) for s in uyeler), 1),
                    "risk_payi_%": round(sum(pay.get(s, {}).get("risk_payi_%", 0) for s in uyeler), 1)})
    return sorted(out, key=lambda x: -x["risk_payi_%"])


def _kor(a: dict, b: dict) -> float | None:
    ortak = sorted(set(a) & set(b))
    if len(ortak) < K.ASGARI_ORTAK:
        return None
    x, y = [a[t] for t in ortak], [b[t] for t in ortak]
    sx, sy = K._std(x), K._std(y)
    if not sx or not sy:
        return None
    ox, oy = sum(x) / len(x), sum(y) / len(y)
    return sum((p - ox) * (q - oy) for p, q in zip(x, y)) / ((len(x) - 1) * sx * sy)


def beta(portfoy: dict[date, float], faktor: dict[date, float],
         once: date | None = None, azami: int | None = None) -> dict | None:
    """
    SAF. Portfoyun faktore regresyon betasi, ortak gunlerde. `once`
    verilirse YALNIZCA o tarihten ONCEKI gunler (geriye sinamanin look-ahead
    kapisi); `azami` -> son N ortak gun. Doner {beta, r2, artik_std, gozlem}.
    """
    ortak = sorted(t for t in set(portfoy) & set(faktor) if once is None or t < once)
    if azami:
        ortak = ortak[-azami:]
    if len(ortak) < ASGARI_GOZLEM:
        return None
    x, y = [faktor[t] for t in ortak], [portfoy[t] for t in ortak]
    n = len(x)
    ox, oy = sum(x) / n, sum(y) / n
    vx = sum((v - ox) ** 2 for v in x) / (n - 1)
    if vx <= 0:
        return None
    kov = sum((a - ox) * (b - oy) for a, b in zip(x, y)) / (n - 1)
    b = kov / vx
    artik = [yy - oy - b * (xx - ox) for xx, yy in zip(x, y)]
    vy = sum((v - oy) ** 2 for v in y) / (n - 1)
    sa = K._std(artik)
    return {"beta": round(b, 3), "r2": round((kov * kov) / (vx * vy), 3) if vy else None,
            "artik_std": sa, "gozlem": n}


def geriye_sinama(portfoy: dict[date, float], faktor: dict[date, float],
                  gunler: list[date], pencere: int = SINAMA_PENCERE) -> dict:
    """
    SAF. KANIT: her test gununde beta YALNIZCA o gunden onceki `pencere`
    gozlemle hesaplanir, tahmin = beta x faktor hareketi, gercekle
    kiyaslanir. Bant = tahmin ± 2 x artik std (modelin kendi belirsizligi).
    Doner {satirlar, bant_ici, toplam, ort_mutlak_hata_puan}.
    """
    satir = []
    for d in gunler:
        if d not in portfoy or d not in faktor:
            continue
        b = beta(portfoy, faktor, once=d, azami=pencere)
        if b is None:
            continue
        tahmin = b["beta"] * faktor[d]
        bant = 2 * b["artik_std"]
        gercek = portfoy[d]
        satir.append({"gun": str(d), "faktor_%": round(faktor[d] * 100, 2),
                      "beta": b["beta"], "tahmin_%": round(tahmin * 100, 2),
                      "gercek_%": round(gercek * 100, 2),
                      "bant_puan": round(bant * 100, 2),
                      "bant_ici": abs(gercek - tahmin) <= bant})
    if not satir:
        return {"satirlar": [], "toplam": 0, "bant_ici": 0, "ort_mutlak_hata_puan": None,
                "naif_hata_puan": None, "ise_yariyor": False}
    hata = sum(abs(s["gercek_%"] - s["tahmin_%"]) for s in satir) / len(satir)
    naif = sum(abs(s["gercek_%"]) for s in satir) / len(satir)
    return {"satirlar": satir, "toplam": len(satir),
            "bant_ici": sum(1 for s in satir if s["bant_ici"]),
            "ort_mutlak_hata_puan": round(hata, 2),
            "naif_hata_puan": round(naif, 2),
            "ise_yariyor": hata < naif}


def sinama_ozeti(portfoy: dict[date, float], faktor: dict[date, float],
                 bas: date, adet: int = SINAMA_GUN) -> dict | None:
    """
    SAF. Faktorun `bas`tan sonraki EN KOTU `adet` gununde geriye sinama.
    Gun yoksa None.
    """
    gunler = sorted((t for t in faktor if t > bas and t in portfoy),
                    key=lambda t: faktor[t])[:adet]
    if not gunler:
        return None
    r = geriye_sinama(portfoy, faktor, sorted(gunler))
    return r if r["toplam"] else None


def _dogrudan_agirlik(kalemler: list[dict], sen: dict, kapsanan: float) -> float | None:
    """O piyasada DOGRUDAN tutulan agirlik (portfoy payi): BIST icin TL kalemler."""
    para = {"XU100": "TRY"}.get(sen.get("faktor"))
    if para is None:
        return None
    return sum(k["agirlik"] for k in kalemler if k.get("para") == para) * kapsanan


def senaryo_etkisi(sen: dict, toplam_eur: float, betalar: dict,
                   para_agirligi: dict[str, float], kanit: dict | None = None,
                   dogrudan: float | None = None) -> dict:
    """
    SAF. Tek senaryonun EUR ve yuzde etkisi. 'kur' turunde o para
    birimindeki AGIRLIK x sok.

    'beta' turunde sayi YALNIZCA iki kapi gecilirse verilir: R² >=
    ASGARI_R2 VE geriye sinama (`kanit.ise_yariyor`): model gecmis cokus gunlerinde "hic etkilenmez"
    tahmininden kotuyse beta SAHTE bir iliskidir ve senaryo sayisi
    gosterilmez — yerine, varsa, o piyasadaki DOGRUDAN payin muhasebe
    etkisi (`dogrudan` x sok) verilir.
    """
    if sen["tur"] == "beta":
        b = betalar.get(sen["faktor"])
        if not b:
            return {"ad": sen["ad"], "olculemedi": f"{sen['faktor']} serisiyle yeterli ortak gun yok"}
        out = {"ad": sen["ad"], "beta": b["beta"], "r2": b["r2"]}
        if kanit:
            out["kanit"] = kanit
        zayif = (b["r2"] or 0) < ASGARI_R2
        if zayif or not kanit or not kanit.get("ise_yariyor"):
            out["olculemedi"] = (
                f"faktor portfoyun gunluk hareketinin yalnizca %{(b['r2'] or 0) * 100:.0f}'ini "
                "acikliyor — iliski zayif, sayi verilmedi" if zayif else
                "beta modeli gecmis cokus gunlerinde 'hic etkilenmez' tahmininden "
                "iyi degil — iliski guvenilmez, sayi verilmedi" if kanit else
                "geriye sinama yapilamadi — sayi verilmedi")
            if dogrudan is not None:
                etki = dogrudan * sen["sok"]
                out.update({"etki_%": round(etki * 100, 1), "etki_eur": round(etki * toplam_eur),
                            "maruz_%": round(dogrudan * 100, 1),
                            "yontem": "yalnizca o piyasada DOGRUDAN tutulan kalemler (muhasebe)"})
            return out
        etki = b["beta"] * sen["sok"]
        out.update({"etki_%": round(etki * 100, 1), "etki_eur": round(etki * toplam_eur),
                    "yontem": "gecmis 1 yilin betasi x sok; geriye sinamada naif tahminden iyi"})
        return out
    w = sum(para_agirligi.get(p, 0.0) for p in sen["para"])
    etki = w * sen["sok"]
    return {"ad": sen["ad"], "etki_%": round(etki * 100, 1), "etki_eur": round(etki * toplam_eur),
            "maruz_%": round(w * 100, 1),
            "yontem": "o para biriminde kayitli kalemlerin EUR degeri (muhasebe; "
                      "EUR'da islem goren ABD fonlarinin dolar riski HARIC — alt sinir)"}


def stres(kalemler_seviye: list[dict], donem: dict) -> dict:
    """
    SAF. Bugunku agirliklarla bir tarihsel donemin bilesik getirisi.
    kalemler_seviye: [{sembol, agirlik, getiri: {tarih: r} (YEREL para)}].
    Yalnizca donemin TAMAMINI kapsayan kalemler sayilir; kapsanan agirlik
    beyan edilir ve sonuc o agirliga gore OLCEKLENMEZ (kapsanmayan 0 sayilmaz,
    disarida birakilir — oran kapsanan kisim icindir).
    """
    bas, bit = date.fromisoformat(donem["bas"]), date.fromisoformat(donem["bit"])
    kapsanan, w_top, toplam = [], 0.0, 0.0
    for k in kalemler_seviye:
        g = k["getiri"]
        if not g or min(g) > bas + timedelta(days=7) or max(g) < bit:
            continue
        r = _bilesik([v for t, v in sorted(g.items()) if bas < t <= bit])
        kapsanan.append({"sembol": k["sembol"], "getiri_%": round(r * 100, 1)})
        w_top += k["agirlik"]
        toplam += k["agirlik"] * r
    if w_top <= 0:
        return {"ad": donem["ad"], "olculemedi": "donemi kapsayan seri yok"}
    return {"ad": donem["ad"], "donem": f"{donem['bas']}..{donem['bit']}",
            "kapsanan_getiri_%": round(toplam / w_top * 100, 1),
            "kapsanan_agirlik_%": round(w_top * 100, 1),
            "kalemler": sorted(kapsanan, key=lambda x: x["getiri_%"]),
            "not": "yerel para birimiyle (kur hareketi haric); yalnizca donemin "
                   "tamaminda serisi olan kalemler"}


def tahammul_kiyasi(ist: dict, politika: dict | None) -> dict | None:
    """SAF. Olculen kayiplarin politikadaki tahammulle kiyasi."""
    if not politika or "hata" in ist:
        return None
    tol = politika.get("tahammul") or {}
    hedef, azami = tol.get("hedef_%"), tol.get("azami_%")
    if hedef is None and azami is None:
        return None
    dusus = abs(ist["en_derin_dusus_%"])
    return {"hedef_%": hedef, "azami_%": azami, "olculen_en_derin_%": ist["en_derin_dusus_%"],
            "hedef_asildi": hedef is not None and dusus > hedef,
            "azami_asildi": azami is not None and dusus > azami}


# ---------------------------------------------------------------- db girisi

def _seri(db, instrument_id: int, venue, n: int = 600):
    barlar = db.fiyat_serisi(instrument_id, n)
    return barlar, K.borsa_limiti(venue)


def _faktor(db, sembol: str, n: int = 600) -> dict[date, float]:
    r = db.query("""SELECT id, venue FROM instruments WHERE UPPER(symbol) = ?
                    ORDER BY CASE venue WHEN 'INDEX' THEN 0 WHEN 'MAKRO' THEN 1 ELSE 2 END
                    LIMIT 1""", (sembol,))
    if not r:
        return {}
    b, lim = _seri(db, r[0]["id"], r[0]["venue"], n)
    return eur_getirileri(b, lim)               # faktor kendi para biriminde


# Tarihsel streste TL kalemleri DISARIDA: EURTRY serisi 2025'ten basliyor,
# yerel TL getirisi ise enflasyonla sisik (KCHOL 2022'de "+%81") — kayip
# senaryosunda kazanc gibi okunurdu.
STRES_PARA = ("EUR", "USD", "USDT")


def kalemleri_hazirla(db, poz: list[dict], bugun: date) -> dict:
    """
    poz: [{sembol, instrument_id, eur}] (nakit HARIC). Doner {kalemler
    (EUR getiri, kapsananda agirlik toplami 1), seviye (yerel getiri,
    stres icin), seri_yok, para_agirligi, kapsanan, toplam}.
    """
    toplam = sum(x["eur"] for x in poz)
    kurlar = {}
    for para, sym in KUR_SERISI.items():
        r = db.query("SELECT id FROM instruments WHERE UPPER(symbol)=? AND venue='MAKRO'", (sym,))
        if r:
            kurlar[para] = kur_ceviricisi(db.fiyat_serisi(r[0]["id"], 2000))
    # Ayni sembol iki hesapta olabilir -> getiri bir kez, agirlik toplanir.
    birlesik: dict[str, dict] = {}
    for x in poz:
        k = birlesik.setdefault(x["sembol"], {**x, "eur": 0.0})
        k["eur"] += x["eur"]
    kalemler, seviye, seri_yok, para_agirligi = [], [], [], {}
    bas = bugun - timedelta(days=PENCERE_GUN)
    for x in birlesik.values():
        w = x["eur"] / toplam
        v = db.query("SELECT venue FROM instruments WHERE id = ?", (x["instrument_id"],))
        barlar, lim = _seri(db, x["instrument_id"], v[0]["venue"] if v else None, 2600)
        barlar = [b for b in barlar if _t(b["ts"]) <= bugun]
        para = str((barlar[-1]["currency"] if barlar else "") or "").upper()
        para_agirligi[para or "?"] = para_agirligi.get(para or "?", 0.0) + w
        kucuk = x["eur"] < 1.0                # 1 €'dan kucuk kalem gurultu
        if len(barlar) < 30:
            if not kucuk:
                seri_yok.append(x["sembol"])
            continue
        kur = None if para in ("EUR", "") else kurlar.get(para)
        if para not in ("EUR", "") and kur is None:
            seri_yok.append(f"{x['sembol']} ({para} kuru yok)")
            continue
        g = eur_getirileri(barlar, lim, kur)
        if len([t for t in g if t > bas]) < ASGARI_GOZLEM // 2:
            if not kucuk:
                seri_yok.append(x["sembol"])
            continue
        kalemler.append({"sembol": x["sembol"], "agirlik": w, "getiri": g, "para": para})
        if para in STRES_PARA:
            seviye.append({"sembol": x["sembol"], "agirlik": w,
                           "getiri": eur_getirileri(barlar, lim)})
    kapsanan = sum(k["agirlik"] for k in kalemler)
    # Kapsanan kisim icinde yeniden agirliklandir: kapsanmayan kalem 0
    # getiri sayilirsa risk SAHTE DUSUK cikar.
    for k in kalemler:
        k["agirlik"] = k["agirlik"] / kapsanan if kapsanan else 0.0
    return {"kalemler": kalemler, "seviye": seviye, "seri_yok": seri_yok,
            "para_agirligi": para_agirligi, "kapsanan": kapsanan, "toplam": toplam}


def portfoy_riski(db, settings, sahip: str, bugun: date | None = None) -> dict | None:
    """
    TEK GIRIS. Politikasi olmayan sahip icin None (tahammul kiyasi
    politikadan gelir). Pozisyonlar `ips._pozisyonlar` (tek EUR kaynagi).
    """
    from . import ips
    p = ips.politika(settings, sahip)
    if p is None:
        return None
    bugun = bugun or date.today()
    poz, cevrilemeyen, _ = ips._pozisyonlar(db, sahip)
    poz = [x for x in poz if ips.sinif_bul(p, x["sembol"], x["asset_type"]) != "nakit"]
    if sum(x["eur"] for x in poz) <= 0:
        return {"toplam_eur": 0, "not": "degerlenebilir pozisyon yok"}
    h = kalemleri_hazirla(db, poz, bugun)
    kalemler, toplam, kapsanan = h["kalemler"], h["toplam"], h["kapsanan"]
    if not kalemler:
        return {"toplam_eur": round(toplam, 2), "not": "serisi olan kalem yok",
                "seri_yok": h["seri_yok"]}
    kb = kapsam_basi(kalemler) or bugun
    bas = max(bugun - timedelta(days=PENCERE_GUN), kb)
    seri = portfoy_getirisi(kalemler, bas, bugun)
    uzun = portfoy_getirisi(kalemler, kb, bugun)
    ist = kayip_istatistigi(seri)
    katki = risk_katkisi(kalemler, seri)
    kume = kumeler(kalemler, katki)
    betalar, kanit = {}, {}
    for s in sorted({x["faktor"] for x in SENARYOLAR if x["tur"] == "beta"}):
        f = _faktor(db, s)
        betalar[s] = beta(seri, f)
        kanit[s] = sinama_ozeti(uzun, f, bas)
    senaryo = [senaryo_etkisi(s, toplam, betalar, h["para_agirligi"], kanit.get(s.get("faktor")),
                              _dogrudan_agirlik(kalemler, s, h["kapsanan"]))
               for s in SENARYOLAR]
    stresler = [stres(h["seviye"], d) for d in STRES_DONEMLERI]
    tol = {"tahammul": {"hedef_%": p.get("tahammul_hedef_pct"), "azami_%": p["tahammul_pct"]}}
    return {
        "toplam_eur": round(toplam, 2),
        "kapsanan_agirlik_%": round(kapsanan * 100, 1),
        "seri_yok": h["seri_yok"],
        "cevrilemeyen": cevrilemeyen,
        "kayip": ist,
        "tahammul": tahammul_kiyasi(ist, tol),
        "kotu_ay_eur": (round(ist["kotu_ay_20de1_%"] / 100 * toplam)
                        if "hata" not in ist else None),
        "risk_katkisi": katki,
        "kumeler": kume,
        "senaryolar": senaryo,
        "tarihsel_stres": stresler,
        "para_agirligi_%": {k: round(v * 100, 1) for k, v in
                            sorted(h["para_agirligi"].items(), key=lambda kv: -kv[1])},
        "not": ("Bugunku agirliklar son 1 yila sabit uygulandi (alim-satim yok "
                "sayildi); tahmin degil, gecmisin tekrari. 'kotu_ay_20de1' kayan "
                "30 gunluk pencerelerden (ortusen). Senaryo betasi gecmis 1 yildan; "
                "kur senaryosu kayit para birimiyle — EUR'da islem goren ABD "
                "fonlarinin dolar riski fiyatin icinde, muhasebede SAYILMADI."),
    }


def arac_ozeti(r: dict) -> dict:
    """
    SAF. Sohbet araci icin KUCUK cikti: risk payi ilk 6, stres ve kanit
    satirlari kisaltilmis. Tam sonuc ~8 KB; model tek bakista okumali.
    """
    if not r or "kayip" not in r:
        return r or {}
    out = {k: v for k, v in r.items() if k not in ("risk_katkisi", "senaryolar", "tarihsel_stres")}
    out["risk_katkisi_ilk"] = r["risk_katkisi"][:6]
    sen = []
    for s in r["senaryolar"]:
        x = {k: v for k, v in s.items() if k != "kanit"}
        k = s.get("kanit")
        if k:
            x["kanit"] = {"sinanan_gun": k["toplam"], "model_hata_puan": k["ort_mutlak_hata_puan"],
                          "hic_etkilenmez_hata_puan": k["naif_hata_puan"],
                          "ise_yariyor": k["ise_yariyor"],
                          "gunler": [f"{z['gun']}: tahmin {z['tahmin_%']} / gercek {z['gercek_%']}"
                                     for z in k["satirlar"]]}
        sen.append(x)
    out["senaryolar"] = sen
    out["tarihsel_stres"] = [{k: v for k, v in s.items() if k != "kalemler"} |
                             ({"en_kotu_kalemler": s["kalemler"][:3]} if s.get("kalemler") else {})
                             for s in r["tarihsel_stres"]]
    return out


def kart_ozeti(r: dict | None) -> dict | None:
    """
    SAF. Haftalik rapor karti icin: kotu ay €, en derin dusus vs tahammul,
    en buyuk risk payi, olculebilen senaryolar. Olculemeyen senaryo KARTA
    GIRMEZ (sayisi yok); stres yalnizca kapsanan agirlik >= %50 ise.
    """
    if not r or "kayip" not in r or "hata" in r["kayip"]:
        return None
    k = r["kayip"]
    senaryo = [{"ad": s["ad"], "etki_%": s["etki_%"], "etki_eur": s["etki_eur"]}
               for s in r["senaryolar"] if "olculemedi" not in s and "etki_%" in s
               and abs(s["etki_eur"]) >= 10]
    stres_ = [{"ad": s["ad"], "getiri_%": s["kapsanan_getiri_%"],
               "kapsanan_%": s["kapsanan_agirlik_%"]}
              for s in r["tarihsel_stres"]
              if "olculemedi" not in s and s["kapsanan_agirlik_%"] >= 50]
    ilk = r["risk_katkisi"][:3]
    # STRES TAHAMMULU ASIYORSA KARTIN ANA MESAJI odur: 1 yillik pencere
    # (boga yili) -%12,6 gosterirken 2022 benzeri bir yil -%46 (9 Eki).
    azami = (r.get("tahammul") or {}).get("azami_%")
    asan = [s for s in stres_ if azami and -s["getiri_%"] > azami]
    stres_asim = None
    if asan:
        en = min(asan, key=lambda s: s["getiri_%"])
        stres_asim = {**en, "kat": round(-en["getiri_%"] / azami, 1)}
    return {"toplam_eur": r["toplam_eur"], "kotu_ay_%": k["kotu_ay_20de1_%"],
            "kotu_ay_eur": r["kotu_ay_eur"], "en_kotu_ay_%": k["en_kotu_ay_%"],
            "en_derin_%": k["en_derin_dusus_%"], "tahammul": r["tahammul"],
            "risk_ilk": ilk, "senaryolar": senaryo, "stres": stres_,
            "olculemeyen": [s["ad"] for s in r["senaryolar"] if "olculemedi" in s],
            "stres_asim": stres_asim,
            "kume": r["kumeler"][0] if r["kumeler"] and r["kumeler"][0]["risk_payi_%"] >= 10 else None}
