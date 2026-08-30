"""
S18.7'nin sordugu soru: trend filtresinin dusus avantaji ZAMANLAMADAN mi
geliyor, yoksa sadece DAHA AZ YATIRIMDA KALMAKTAN mi?

Kural ~%70 yatirimda. Sabit %70 ETF / %30 nakit portfoyu de mekanik
olarak dususun ~%70'ini gorur. Dairesel kaydirma ZAMANLAMAYI sinar ama
"bu karmasiklik gerekli mi" sorusunu SORMAZ. Bu betik onu soruyor.

AYRICA S18.3'un buldugu kusur burada duzeltildi: eski betik portfoyu
yalnizca 21 barlik donem SONUNDA degerliyordu, yani ay ici dusup ay
sonu toparlanan bir kayip azami dususa HIC girmiyordu. Burada portfoy
HER GUNLUK KAPANISTA degerleniyor.

SABIT KIYAS ADIL TUTULUYOR: her piyasada kuralin KENDI yatirimda kalma
orani kullaniliyor (%70 varsayilmiyor), ve sabit portfoye ISLEM
MALIYETI YAZILMIYOR — yani alternatif kasten AVANTAJLI. Kural ancak
bunu da geciyorsa karmasikligi hak eder.
"""
import statistics
import sys

sys.path.insert(0, "/Users/alipala/github/bist-bux-agent/src")

from finagent.config import load_settings                          # noqa: E402
from finagent.storage.db import Database                           # noqa: E402

ADIM, GERIYE = 21, 252
GECIKME = 1                    # S10: sinyal kapanisi = ayni kapanis DEGIL
TEK_YON = 0.00615
PIYASALAR = ["SPX", "NDXC", "N225", "FTSE", "DAX", "TSX", "HSI", "AXJO"]


def _durumlar(kap):
    """Her 21 barda bir: 12 aylik getiri > 0 mi."""
    return [(kap[i - GERIYE] and kap[i] and kap[i - GERIYE] > 0
             and kap[i] / kap[i - GERIYE] - 1 > 0)
            for i in range(GERIYE, len(kap) - ADIM)][::ADIM]


def _gunluk(kap, pay_fn, maliyetli=True):
    """
    GUNLUK degerleme. `pay_fn(adim) -> 0..1` o donemin maruziyeti.
    Doner: (son deger, azami dusus, islem, ort maruziyet).
    """
    v, onceki, islem, tepe, kotu, paylar = 1.0, 0.0, 0, 1.0, 0.0, []
    for adim, i in enumerate(range(GERIYE, len(kap) - ADIM, ADIM)):
        pay = pay_fn(adim)
        if pay is None:
            break
        paylar.append(pay)
        if maliyetli and abs(pay - onceki) > 1e-9:
            v *= 1 - TEK_YON * abs(pay - onceki)
            islem += 1
        onceki = pay
        for g in range(ADIM):                       # HER GUN degerle
            j = i + GECIKME + g
            if j + 1 >= len(kap) or not kap[j] or not kap[j + 1]:
                break
            v *= 1 + pay * (kap[j + 1] / kap[j] - 1)
            tepe = max(tepe, v)
            kotu = min(kotu, v / tepe - 1)
    return v, kotu, islem, (sum(paylar) / len(paylar) if paylar else 0)


def main() -> int:
    db = Database(load_settings().db_path)
    print(f"{'piyasa':<7}{'yil':>5}{'maruz':>7}   "
          f"{'KURAL':>16}   {'SABIT ayni maruz':>18}   {'AL-TUT':>16}")
    print(f"{'':19}   {'yillik':>7}{'dusus':>9}   {'yillik':>8}{'dusus':>10}"
          f"   {'yillik':>7}{'dusus':>9}")
    print("-" * 88)
    kural_kazandi = 0
    oranlar = []
    sabit70 = []
    for kod in PIYASALAR:
        r = db.query("SELECT id FROM instruments WHERE symbol=? "
                     "AND venue='INDEX'", (kod,))
        if not r:
            continue
        kap = [float(x["close"]) if x["close"] else None
               for x in db.fiyat_serisi(r[0]["id"], limit=100000)]
        d = _durumlar(kap)
        vk, dk, isl, maruz = _gunluk(kap, lambda a: float(d[a]) if a < len(d) else None)
        # SABIT: ayni ortalama maruziyet, ZAMANLAMA YOK, MALIYET YOK
        vs, ds, _, _ = _gunluk(kap, lambda a: maruz if a < len(d) else None,
                               maliyetli=False)
        va, da, _, _ = _gunluk(kap, lambda a: 1.0 if a < len(d) else None,
                               maliyetli=False)
        yil = len(d) * ADIM / 252
        fk, fs, fa = (vk ** (1 / yil) - 1, vs ** (1 / yil) - 1,
                      va ** (1 / yil) - 1)
        # S20.5 HAKLI: kuralin gerceklesmis maruziyeti ANCAK DONEM
        # BITINCE bilinir; urun kiyasi olarak kullanilamaz. ONCEDEN
        # DONDURULMUS %70/%30, aylik yeniden dengeleme ve MALIYETLI —
        # uygulanabilir alternatif budur.
        v7, d7, _, _ = _gunluk(kap, lambda a: 0.70 if a < len(d) else None)
        f7 = v7 ** (1 / (len(d) * ADIM / 252)) - 1
        sabit70.append((kod, f7, d7, dk > d7, dk / d7 if d7 else 1))
        iyi = dk > ds                     # kural dususu SABITTEN de az mi
        kural_kazandi += iyi
        oranlar.append(dk / ds if ds else 1)
        print(f"{kod:<7}{yil:>5.0f}{maruz * 100:>6.0f}%   "
              f"{fk * 100:>6.1f}%{dk * 100:>8.1f}%   "
              f"{fs * 100:>7.1f}%{ds * 100:>9.1f}%   "
              f"{fa * 100:>6.1f}%{da * 100:>8.1f}%   "
              f"{'KURAL' if iyi else 'sabit'}")
    # CIFT SAYIDA GOZLEMDE `x[len(x)//2]` MEDYAN DEGILDIR:
    # ust orta degeri secer. 8 piyasada %98,86 verip %99 diye
    # yuvarlaniyordu; gercek medyan %93,79. Denetleyen ikinci agent
    # buldu (S20.4). `statistics.median` tek dogru kapi.
    print("-" * 88)
    print(f"\nKural, AYNI MARUZIYETTEKI SABIT portfoyden daha az dustu: "
          f"{kural_kazandi}/8 piyasa")
    print(f"medyan (kural dususu / sabit dususu) = "
          f"%{statistics.median(oranlar) * 100:.1f}   "
          f"[%100 = zamanlamanin katkisi YOK]")

    print("\nONCEDEN DONDURULMUS %70/%30 (aylik, MALIYETLI) — urun kiyasi:")
    for kod, f7, d7, iyi, _o in sabit70:
        print(f"  {kod:<6} sabit70 yillik %{f7 * 100:>5.1f}  dusus %{d7 * 100:>6.1f}"
              f"   -> {'KURAL' if iyi else 'sabit70'}")
    kz = sum(1 for x in sabit70 if x[3])
    print(f"  kural daha az dustu: {kz}/8   medyan oran "
          f"%{statistics.median([x[4] for x in sabit70]) * 100:.1f}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
