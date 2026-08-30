"""
Kesitsel momentum (12-1) — Donchian'a ALTERNATIF kural sinifi.

NEDEN BU KURAL: Ali'nin baglayici kisiti ZEKA degil MALIYET. Olculdu
(2026-08-30, IBKR `/whatif`): gidis-donus %1,23 (Tiered, 2 pozisyon).
Donchian 20/10 ortalama 16 GUN tutuyor, yani yilda ~15 tur ve ~%18,5
maliyet. Ceyreklik yenileme ~4 tur ve ~%4,9 birakiyor. Ayni kenar, ucte
bir maliyetle.

KURAL (koşumdan ONCE donduruldu):
    sinyal    = close[t-ATLA] / close[t-GERIYE] - 1      (12-1 ay)
    kapi      = sinyal > 0                                (dusen piyasada alma)
    yenileme  = her YENILEME barinda
    secim     = en yuksek N
    maliyet   = YALNIZCA DEGISEN kisma (devir orani)

MALIYET DEVIR ORANIYLA: ceyrek sonunda portfoyun bir kismi AYNI kalir
ve o kisim islem gormez. Tum portfoye maliyet yazmak, momentumu
haksizca cezalandirirdi.

HAYATTA KALMA YANLILIGI — BU KURALDA DAHA AGIR: evren bugunun endeks
uyeleri, yani "gecmiste kazanmis" olmakla secilmis bir kume. Momentum
tam olarak "gecmiste kazanani al" diyor. Rastgele kontrol KUME
duzeyindeki yanliligi temizler; bu mekanik guclenmeyi TAM temizlemez.
Sonuc bu uyariyla okunmali.
"""
from __future__ import annotations

import random

# --------------------------------------------------------------------
# DONDURULMUS PARAMETRELER — koşumdan once yazildi, sonucu gorup
# degistirilmeyecek. Degisiklik gerekirse YENI BIR PENCERE gerekir.
GERIYE = 252          # ~12 ay
ATLA = 21             # ~1 ay — kisa vadeli tersine donusu disla
YENILEME = 63         # ~3 ay
ASGARI_BAR = GERIYE + 1
TOHUM = 20260830
# --------------------------------------------------------------------


# ==================== SAF CEKIRDEK (I/O yok, saat yok) ==============

def momentum_skoru(kapanis: list[float | None], i: int,
                   geriye: int = GERIYE, atla: int = ATLA) -> float | None:
    """
    `i` barindaki 12-1 momentum skoru. GELECEGE BAKMIYOR: yalnizca
    i-atla ve i-geriye konumlarini okuyor, ikisi de i'nin gerisinde.

    None doner: yeterli gecmis yok ya da fiyat eksik/sifir.
    """
    if i < geriye:
        return None
    a, b = kapanis[i - geriye], kapanis[i - atla]
    if not a or not b or a <= 0:
        return None
    return b / a - 1.0


def secim(skorlar: dict[str, float | None], n: int) -> list[str]:
    """
    En yuksek `n` skor — SINYAL > 0 kapisiyla.

    Kapi neden var: kapisiz momentum, dusen bir piyasada "en az
    dusenleri" satin alir. Sinyal negatifken pozisyon ACILMAZ;
    portfoy o ceyrek eksik kalir (nakit), uydurma bir aday secilmez.

    ESITLIK SEMBOLE GORE COZULUYOR — kayan sozluk sirasi sonucu
    degistirmesin diye. Tekrarlanabilirlik kontrol grubunun on sarti.
    """
    uygun = [(s, v) for s, v in skorlar.items() if v is not None and v > 0]
    uygun.sort(key=lambda x: (-x[1], x[0]))
    return [s for s, _ in uygun[:n]]


def donem_getirisi(kapanis: list[float | None], bas: int, son: int) -> float | None:
    """`bas` kapanisindan `son` kapanisina getiri (oran, yuzde degil)."""
    if bas < 0 or son >= len(kapanis) or bas >= son:
        return None
    a, b = kapanis[bas], kapanis[son]
    if not a or not b or a <= 0:
        return None
    return b / a - 1.0


def devir_orani(onceki: list[str], yeni: list[str]) -> float:
    """
    Portfoyun DEGISEN orani. 0.0 = hicbir sey degismedi (maliyet yok),
    1.0 = tamami degisti.

    Bos portfoyden bos portfoye gecis 0.0 — islem yok, maliyet yok.
    """
    if not yeni and not onceki:
        return 0.0
    kalan = len(set(onceki) & set(yeni))
    return 1.0 - kalan / max(len(yeni), len(onceki))


def donem_sonucu(kapanislar: dict[str, list[float | None]],
                 secilen: list[str], bas: int, son: int) -> float | None:
    """
    Bir donemin ESIT AGIRLIKLI getirisi. Secim bossa 0.0 (nakit).

    Serisi eksik olan sembol ATLANIYOR ve kalanlar esit agirlikli
    aliniyor; sessizce 0 saymak, o pozisyonu "getirisiz ama tutulmus"
    gibi gosterirdi.
    """
    if not secilen:
        return 0.0
    g = [x for x in (donem_getirisi(kapanislar[s], bas, son)
                     for s in secilen if s in kapanislar) if x is not None]
    return sum(g) / len(g) if g else None


def yurut(kapanislar: dict[str, list[float | None]], n: int,
          yenileme: int = YENILEME, geriye: int = GERIYE, atla: int = ATLA,
          maliyet: float = 0.0123,
          secici=None) -> list[dict]:
    """
    Kuralin seri boyunca YURUTULMESI. SAF: db yok, saat yok.

    `secici(skorlar, n, i) -> list[str]` verilmezse `secim` kullanilir.
    Kontrol grubu buradan giriyor — AYNI donemlerde, AYNI sayida,
    farkli SECIMLE. Boylece kontrol ile kural ayni takvimi, ayni
    maliyeti ve ayni esit-agirlik kuralini paylasiyor.
    """
    uzunluk = max((len(v) for v in kapanislar.values()), default=0)
    if uzunluk <= geriye + yenileme:
        return []
    sec = secici or (lambda skor, adet, _i: secim(skor, adet))

    donemler: list[dict] = []
    onceki: list[str] = []
    i = geriye
    while i + yenileme < uzunluk:
        skorlar = {s: momentum_skoru(k, i, geriye, atla)
                   for s, k in kapanislar.items() if len(k) > i}
        secilen = sec(skorlar, n, i)
        ham = donem_sonucu(kapanislar, secilen, i, i + yenileme)
        if ham is not None:
            devir = devir_orani(onceki, secilen)
            donemler.append({
                "bar": i, "secilen": list(secilen), "adet": len(secilen),
                "ham": ham, "devir": devir,
                "maliyet": devir * maliyet,
                "net": ham - devir * maliyet,
            })
            onceki = secilen
        i += yenileme
    return donemler


def rastgele_secici(evren: list[str], tohum: int = TOHUM):
    """
    KONTROL GRUBU: ayni donemlerde ayni sayida RASTGELE sembol.

    `secim` kadar sembol seciyor — DAHA FAZLA DEGIL. Kural sinyal
    kapisi yuzunden 12 sembol bulduysa kontrol de 12 seciyor; 20
    seceydi kontrol daha genis cesitlendirilmis olur ve oynakligi
    dusup HAKSIZ avantaj kazanirdi.

    TOHUM SABIT: ayni veri ayni sonucu versin.
    """
    rnd = random.Random(tohum)

    def _sec(skorlar, n, _i):
        kural = secim(skorlar, n)
        aday = sorted(s for s, v in skorlar.items() if v is not None)
        if not aday or not kural:
            return []
        return rnd.sample(aday, min(len(kural), len(aday)))

    return _sec


def ozet(donemler: list[dict]) -> dict:
    """Donem bazli ozet. GOZLEM BIRIMI DONEM, islem DEGIL."""
    if not donemler:
        return {"donem": 0}
    net = [d["net"] for d in donemler]
    ort = sum(net) / len(net)
    sapma = (sum((x - ort) ** 2 for x in net) / (len(net) - 1)) ** 0.5 \
        if len(net) > 1 else 0.0
    return {
        "donem": len(donemler),
        "ort_net_%": round(ort * 100, 3),
        "ort_ham_%": round(sum(d["ham"] for d in donemler) / len(donemler) * 100, 3),
        "ort_maliyet_%": round(
            sum(d["maliyet"] for d in donemler) / len(donemler) * 100, 3),
        "ort_devir_%": round(
            sum(d["devir"] for d in donemler) / len(donemler) * 100, 1),
        "sapma_%": round(sapma * 100, 3),
        "pozitif_donem_%": round(
            sum(1 for x in net if x > 0) / len(net) * 100, 1),
        "ort_pozisyon": round(
            sum(d["adet"] for d in donemler) / len(donemler), 1),
        # t: donem bazli. ISLEM SAYISIYLA hesaplanan bir t, ayni donemin
        # pozisyonlarini BAGIMSIZ GOZLEM sayardi — degiller.
        "t": (round(ort / (sapma / len(net) ** 0.5), 2)
              if sapma > 0 and len(net) > 1 else None),
        "bilesik_%": round(
            (__import__("math").prod(1 + x for x in net) - 1) * 100, 1),
    }
