"""
OLAY TAKVIMI — tarihi ONCEDEN BILINEN olaylarin fiyat serisindeki izi.

NE ICIN VAR
-----------
Donchian kuralinin stop'u fiyat stop seviyesinden GECERKEN korur. Fiyat
stop'un altinda ACILIRSA cikis acilistan olur ve stop'un vaat ettigi
kayip siniri asilir. Canli defterde olculdu (2026-09-24): INTC'nin stop'u
96,79, cikis 95,82. Bu tur bosluklarin bir kismi TARIHI ONCEDEN BILINEN
olaylarda olusur: sirketin bilancosu, CPI, istihdam raporu, FOMC karari.

Bu modul ne olacagini TAHMIN ETMEZ; ne ZAMAN belirsizlik gelecegini
serinin kendi islem gunlerine esler. Sonuc yon degil, bir gun kumesidir.

SAF CEKIRDEK
------------
Buradaki her fonksiyon saftir: db yok, ag yok, saat yok. Girdi islem
gunleri listesi ve olay kayitlari; cikti gun kumeleri ve sayilar. Boylece
sentetik seriyle, veritabani olmadan test edilebilirler. Veriyi getiren
kabuk `collectors/bilancotakvim.py` ve `collectors/takvim.py`da.

TEK TANIM
---------
"Tepki gunu" tanimi YALNIZCA `tepki_gunleri()`nde. Etki olcumu, giris
engeli ve canli uyari ayni fonksiyondan turer. Bu deponun en pahali
dersi: ayni kural iki yerde yazilinca kopyalar ayrisir.
"""
from __future__ import annotations

import bisect
import random
from datetime import datetime

# ABD seans saatleri, ABD DOGU SAATIYLE. Olay saatleri de bu saat
# diliminde saklanir (`bilanco_takvimi.saat`); karsilastirma ayni
# saat diliminde yapilmazsa seans oncesi bir aciklama "seans ici" sayilir.
SEANS_ACILIS = "09:30"
SEANS_KAPANIS = "16:00"


def zaman_sinifi(saat: str | None) -> str | None:
    """
    'HH:MM' (ABD Dogu) -> 'once' | 'seans' | 'sonra' | None.

    None = SAAT BILINMIYOR. Bilinmeyen saat 'seans' sayilmaz: "bilmiyoruz"
    ile "seans icinde" ayri seyler ve ikisi farkli tepki gunu uretir.
    """
    if not saat:
        return None
    s = str(saat).strip()[:5]
    if len(s) != 5 or s[2] != ":":
        return None
    if s < SEANS_ACILIS:
        return "once"
    if s >= SEANS_KAPANIS:
        return "sonra"
    return "seans"


def tepki_gunleri(tarih: str, zaman: str | None,
                  islem_gunleri: list[str]) -> list[str]:
    """
    Aciklamanin fiyata ILK yansidigi islem gunu(leri).

    `islem_gunleri` ARTAN sirali 'YYYY-MM-DD' listesi — serinin KENDI
    barlari. Tatil takvimi uydurmak yerine serinin gercekten islem
    gordugu gunler kullanilir; hafta sonu ya da tatile dusen aciklama
    kendiliginden sonraki islem gunune kayar.

      'once'  -> tarih >= ilk islem gunu (ayni gun acilis boslugu)
      'sonra' -> tarih <  ilk islem gunu (ertesi gun acilis boslugu)
      'seans' / None -> IKISI DE. Hangisi oldugu bilinmiyorsa ikisi de
                        risklidir; birini secmek, digerini sessizce
                        "guvenli" ilan etmek olurdu.

    Seri olayi kapsamiyorsa (tarih son bardan sonra) bos liste doner.
    """
    if not islem_gunleri or not tarih:
        return []
    t = str(tarih)[:10]
    ayni = bisect.bisect_left(islem_gunleri, t)      # ilk gun >= t
    ertesi = bisect.bisect_right(islem_gunleri, t)   # ilk gun >  t
    out: list[str] = []
    if zaman == "once":
        adaylar = [ayni]
    elif zaman == "sonra":
        adaylar = [ertesi]
    else:
        adaylar = [ayni, ertesi]
    for i in adaylar:
        if i < len(islem_gunleri) and islem_gunleri[i] not in out:
            out.append(islem_gunleri[i])
    return out


def tepki_kumesi(olaylar: list[dict], islem_gunleri: list[str]) -> set[str]:
    """
    Olay kayitlarinin (`tarih`, `zaman` anahtarli) tepki gunleri birlesimi.

    `zaman` yoksa `saat`ten turetilir — iki yoldan gelen kayit (takvim
    kaynagi saat verir, sinif vermez; SEC kabul saati verir) ayni
    tanimdan gecsin diye.
    """
    out: set[str] = set()
    for o in olaylar:
        z = o.get("zaman")
        if z is None and o.get("saat"):
            z = zaman_sinifi(o.get("saat"))
        out.update(tepki_gunleri(o.get("tarih"), z, islem_gunleri))
    return out


def giris_engeli(islem_gunleri: list[str], tepkiler: set[str],
                 pencere: int) -> set[str]:
    """
    Kapanisindan GIRIS YAPILMAMASI gereken gunler.

    `d` gununun kapanisindan girilirse `d`nin kendi tepkisi ZATEN
    yasanmistir; risk `(d, d+pencere]` araligindaki tepki gunleridir.
    Yani tepki gunu `j` ise engellenen gunler `j-pencere .. j-1`.

    `j`nin KENDISI engellenmez: tepki gununun kapanisi boslugu coktan
    fiyatlamistir, o fiyattan girmek risk degil bilgi. Ayrim ONEMLI:
    bilanco sonrasi yukari kirilimlar tam o gun olusur ve `j`yi de
    engelleyen bir tanim onlari sessizce disarida birakirdi.
    """
    if pencere <= 0 or not tepkiler:
        return set()
    konum = {g: i for i, g in enumerate(islem_gunleri)}
    out: set[str] = set()
    for g in tepkiler:
        j = konum.get(g)
        if j is None:
            continue
        for k in range(max(0, j - pencere), j):
            out.add(islem_gunleri[k])
    return out


def rastgele_engel(islem_gunleri: list[str], adet: int,
                   tohum: int) -> set[str]:
    """
    KONTROL GRUBU: ayni seride AYNI SAYIDA rastgele engelli gun.

    Soru: filtrenin faydasi OLAY GUNLERINDEN mi geliyor, yoksa yalnizca
    daha az islem yapmaktan mi? Daha az islem tek basina kaybi kucultur;
    filtre ancak ayni sayida rastgele gunu atlamaktan IYIYSE bir sey
    ekliyordur (bkz. `strateji-kenari-yok`: besinci "bulgu" tam olarak
    bu kontrolun yoklugundan cikmisti).

    TOHUM SABIT: ayni veri ayni kontrolu uretir.
    """
    if adet <= 0 or not islem_gunleri:
        return set()
    adet = min(adet, len(islem_gunleri))
    return set(random.Random(tohum).sample(islem_gunleri, adet))


def olay_etkisi(seri: list[dict], tepkiler: set[str],
                atr_pencere: int = 20) -> dict:
    """
    Tepki gunlerinde acilis boslugu diger gunlerden BUYUK MU?

    Olcu, hissenin KENDI oynakligiyla normalize bosluk:
        |acilis / onceki kapanis - 1| / (N / onceki kapanis)
    N = `trend_takip._atr` (o bar HARIC, yalnizca oncesi — ileriye
    bakma yok). Normalize etmeden karsilastirmak, oynak hisselerin
    olaylarini "etkili" gostermek olurdu.

    `buyuk_bosluk_%`: boslugun 1N'yi astigi gunlerin payi. 2N stop
    icin anlamli esik bu: 1N'lik tek acilis boslugu stop mesafesinin
    yarisini tek hamlede yer.
    """
    olay, diger = bosluklar(seri, tepkiler, atr_pencere)
    return {"olay": _dagilim(olay), "diger": _dagilim(diger),
            "oran_medyan": (round(_medyan(olay) / _medyan(diger), 2)
                            if olay and diger and _medyan(diger) else None)}


def bosluklar(seri: list[dict], tepkiler: set[str],
              atr_pencere: int = 20) -> tuple[list[float], list[float]]:
    """
    Normalize acilis bosluklari, (tepki gunleri, diger gunler) olarak.

    `olay_etkisi`nin ham hali: bircok sembolu HAVUZLAMAK icin (sinav
    betigi) ozet degil liste gerekir — sembol basina medyanlarin
    medyani, havuzun medyani DEGILDIR.
    """
    from .trend_takip import _atr

    olay, diger = [], []
    for i in range(1, len(seri)):
        b, o = seri[i], seri[i - 1]
        ac, onceki = b.get("open"), o.get("close")
        if not ac or not onceki:
            continue
        N = _atr(seri, i, atr_pencere)
        if not N or N <= 0:
            continue
        x = abs(ac / onceki - 1) / (N / onceki)
        (olay if str(b["ts"])[:10] in tepkiler else diger).append(x)
    return olay, diger


def dagilim(x: list[float]) -> dict:
    """Havuzlanmis bosluk listesinin ozeti (medyan, p90, >1N payi)."""
    return _dagilim(x)


def _medyan(x: list[float]) -> float:
    """GERCEK medyan — cift sayida gozlemde iki orta degerin ortalamasi.

    `x[len(x)//2]` cift sayida USTTEKI orta degerdir; bu depoda dort
    yerde bu hata vardi (bkz. `strateji-kenari-yok` §20).
    """
    s = sorted(x)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _dagilim(x: list[float]) -> dict:
    if not x:
        return {"n": 0}
    s = sorted(x)
    return {"n": len(s),
            "medyan_N": round(_medyan(s), 3),
            "ortalama_N": round(sum(s) / len(s), 3),
            "p90_N": round(s[min(len(s) - 1, int(0.9 * (len(s) - 1)))], 3),
            "buyuk_bosluk_%": round(sum(1 for v in s if v > 1.0) / len(s) * 100, 1)}


def gun_farki(bugun: str, hedef: str) -> int:
    """Takvim gunu farki — canli uyari metni icin ('3 gun sonra')."""
    a = datetime.strptime(str(bugun)[:10], "%Y-%m-%d").date()
    b = datetime.strptime(str(hedef)[:10], "%Y-%m-%d").date()
    return (b - a).days
