"""
SINAV C — saglamlik izgarasi. 8 piyasa x 7 geriye-bakis = 56 hucre.

OKUMA KURALI (koşumdan ONCE donduruldu, `docs/momentum-sinavi.md`):
EN IYI HUCRE SECILMEYECEK. 56 denemede biri iyi cikar. Sorulan soru
izgaranin KAC hucresinde kural (i) kaydirilmis kontrolu geciyor ve
(ii) dususu azaltiyor. Rastgelelik altinda beklenen ~%50.

Kontrol = DAIRESEL KAYDIRMA: kuralin kendi durum dizisi rastgele bir
noktadan donduruluyor. Islem sayisi, piyasada kalma orani ve blok
uzunluklari AYNI kaliyor; yalnizca ZAMANLAMA rastgele. Bagimsiz zar
atan bir kontrol kurala gore ~5 kat fazla islem yapar ve komisyonla
HAKSIZ cezalanir — bu hata ilk turda yasandi ve boyle duzeltildi.
"""
import random
import sys

sys.path.insert(0, "/Users/alipala/github/bist-bux-agent/src")

from finagent.config import load_settings                          # noqa: E402
from finagent.storage.db import Database                           # noqa: E402

ADIM = 21                     # aylik kontrol — SABIT
TEK_YON = 0.00615             # %1,23 gidis-donus -> tek yon
TOHUM = 20260830
TUR = 200

PIYASALAR = ["SPX", "NDXC", "N225", "FTSE", "DAX", "TSX", "HSI", "AXJO"]
GERIYELER = [(63, "3a"), (126, "6a"), (189, "9a"), (252, "12a"),
             (315, "15a"), (378, "18a"), (504, "24a")]


def _yurut(kapanis, durumlar, geriye):
    v, poz = 1.0, False
    tepe, kotu = 1.0, 0.0
    for adim, i in enumerate(range(geriye, len(kapanis) - ADIM, ADIM)):
        if adim >= len(durumlar):
            break
        if durumlar[adim] != poz:
            v *= 1 - TEK_YON
            poz = durumlar[adim]
        if poz:
            a, b = kapanis[i], kapanis[i + ADIM]
            if a and b:
                v *= b / a
        tepe = max(tepe, v)
        kotu = min(kotu, v / tepe - 1)
    return v, kotu


def _durumlar(kapanis, geriye):
    """12 aylik getiri > 0 -> tut. GELECEGE BAKMIYOR: i ve i-geriye."""
    out = []
    for i in range(geriye, len(kapanis) - ADIM, ADIM):
        a, b = kapanis[i - geriye], kapanis[i]
        out.append(bool(a and b and a > 0 and b / a - 1 > 0))
    return out


def main() -> int:
    s = load_settings()
    db = Database(s.db_path)
    seriler = {}
    for kod in PIYASALAR:
        r = db.query("SELECT id FROM instruments WHERE symbol=? "
                     "AND venue='INDEX'", (kod,))
        if not r:
            print(f"  ! {kod} katalogda yok")
            continue
        seri = db.fiyat_serisi(r[0]["id"], limit=100000)
        seriler[kod] = [float(x["close"]) if x["close"] else None
                        for x in seri]

    gecen_kontrol = gecen_dusus = ikisi = toplam = 0
    print(f"{'piyasa':<7}{'yil':>5}  " + "".join(f"{ad:>9}" for _, ad in GERIYELER))
    print("-" * 76)
    for kod in PIYASALAR:
        kap = seriler.get(kod)
        if not kap:
            continue
        satir_k, satir_d = [], []
        yil = None
        for geriye, _ad in GERIYELER:
            if len(kap) < geriye + 40 * ADIM:
                satir_k.append("     kisa")
                satir_d.append("     kisa")
                continue
            d_kural = _durumlar(kap, geriye)
            n = len(d_kural)
            yil = n * ADIM / 252
            v_k, dus_k = _yurut(kap, d_kural, geriye)
            v_a, dus_a = _yurut(kap, [True] * n, geriye)
            rnd = random.Random(TOHUM)
            turlar = [_yurut(kap, d_kural[k:] + d_kural[:k], geriye)
                      for k in (rnd.randrange(n) for _ in range(TUR))]
            sonlar = sorted(x[0] for x in turlar)
            dususlar = sorted(x[1] for x in turlar)
            medyan = sonlar[len(sonlar) // 2]
            # AYNI MARUZIYETTEKI dusus: al-tut ile kiyas MEKANIK
            # (kural %30 nakitte, al-tut hep yatirimda). Kaydirilmis
            # kontrol AYNI ORANDA nakitte, yani fark ZAMANLAMADAN gelir.
            dus_medyan = dususlar[len(dususlar) // 2]

            yk = v_k ** (1 / yil) - 1
            ym = medyan ** (1 / yil) - 1
            kontrol_gecti = v_k > medyan
            dusus_gecti = dus_k > dus_medyan     # AYNI maruziyete gore
            toplam += 1
            gecen_kontrol += kontrol_gecti
            gecen_dusus += dusus_gecti
            ikisi += kontrol_gecti and dusus_gecti
            isaret = "+" if kontrol_gecti else "-"
            satir_k.append(f"{isaret}{(yk - ym) * 100:>7.1f}%")
            # SAYI ile ISARET AYNI KIYASTAN gelmeli. Ilk halinde isaret
            # kaydirilmis kontrole, sayi al-tut'a bakiyordu — okuyan
            # kisi iki farkli olcuyu tek sutun sanardi.
            satir_d.append(f"{'+' if dusus_gecti else '-'}"
                           f"{(dus_k - dus_medyan) * 100:>7.1f}%")
        print(f"{kod:<7}{yil or 0:>5.0f}  " + "".join(satir_k) + "   <- kontrole gore")
        print(f"{'':<12}" + "".join(satir_d) + "   <- dusus (KAYDIRILMISA gore)")

    print("-" * 76)
    print(f"\nIZGARA OZETI ({toplam} hucre):")
    print(f"  kaydirilmis kontrolu GECEN : {gecen_kontrol}/{toplam} "
          f"(%{gecen_kontrol / toplam * 100:.0f})   [sansta beklenen ~%50]")
    print(f"  dususu AZALTAN (adil)      : {gecen_dusus}/{toplam} "
          f"(%{gecen_dusus / toplam * 100:.0f})")
    print(f"  IKISI BIRDEN               : {ikisi}/{toplam} "
          f"(%{ikisi / toplam * 100:.0f})   [sansta beklenen ~%25]")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
