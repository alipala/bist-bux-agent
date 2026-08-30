"""
SINAV B — ADIL KONTROL ile.

ILK KONTROLUM BOZUKTU: her ay bagimsiz zar atiyordu, yani ayda %42
durum degistiriyor (2p(1-p), p=0,70) ve yilda ~5 islem yapiyordu.
Kural yilda 1 islem yapiyor. Yani kontrole yilda ~%3 fazladan komisyon
yukluyordum ve kuralin ustunlugunun bir kismi KONTROLUN CEZASIYDI.

ADIL KONTROL: kuralin KENDI durum dizisini rastgele bir noktadan
DONDUR (dairesel kaydirma). Boylece:
  - islem sayisi AYNI
  - piyasada kalma orani AYNI
  - blok uzunluklari AYNI
  - YALNIZCA ZAMANLAMA rastgele
Kuralin kenari gercek bir zamanlama becerisiyse, ayni takvimi baska
tarihlere kaydirmak onu YOK ETMELI.
"""
import random
import sys

sys.path.insert(0, "/Users/alipala/github/bist-bux-agent/src")

from finagent.analysis.momentum import momentum_skoru               # noqa: E402
from finagent.config import load_settings                           # noqa: E402
from finagent.storage.db import Database                            # noqa: E402

GERIYE, ADIM = 252, 21
TEK_YON = 0.00615
TOHUM = 20260830
ALT = [("TAMAMI 1929+", "1900-01-01"), ("savas sonrasi 1950+", "1950-01-01"),
       ("modern 1990+", "1990-01-01"), ("son 25 yil 2000+", "2000-01-01")]


def _getiri(kapanis, ts, durumlar):
    """Verilen durum dizisini yurut. Doner: (egri, islem)."""
    v, poz, islem, egri = 1.0, False, 0, []
    for adim, i in enumerate(range(GERIYE, len(kapanis) - ADIM, ADIM)):
        if adim >= len(durumlar):
            break
        tut = durumlar[adim]
        if tut != poz:
            v *= 1 - TEK_YON
            islem += 1
            poz = tut
        if poz:
            a, b = kapanis[i], kapanis[i + ADIM]
            if a and b:
                v *= b / a
        egri.append((ts[i + ADIM], v))
    return egri, islem


def _durumlar(kapanis, fn):
    return [fn(i) for i in range(GERIYE, len(kapanis) - ADIM, ADIM)]


def _dusus(egri):
    tepe, kotu = egri[0][1], 0.0
    for _, v in egri:
        tepe = max(tepe, v)
        kotu = min(kotu, v / tepe - 1)
    return round(kotu * 100, 1)


s = load_settings()
db = Database(s.db_path)
iid = db.query("SELECT id FROM instruments WHERE symbol='SPX' "
               "AND venue='INDEX'")[0]["id"]
seri = db.fiyat_serisi(iid, limit=100000)
ts_all = [r["ts"] for r in seri]
kap_all = [float(r["close"]) if r["close"] else None for r in seri]
print(f"SPX {len(kap_all)} bar · {ts_all[0]} .. {ts_all[-1]}\n")
print(f"{'donem':<21}{'yil':>6}{'KURAL':>8}{'dusus':>8}"
      f"{'AL-TUT':>8}{'dusus':>8}{'KAYD.MEDYAN':>12}{'FARK':>7}{'isl/yil':>9}")
print("-" * 88)

for ad, bas in ALT:
    ts = [t for t in ts_all if t >= bas]
    if len(ts) < GERIYE + 3 * ADIM:
        continue
    kapanis = kap_all[len(ts_all) - len(ts):]
    d_kural = _durumlar(kapanis, lambda i: (momentum_skoru(
        kapanis, i, GERIYE, 0) or 0) > 0)
    kural, islem = _getiri(kapanis, ts, d_kural)
    altut, _ = _getiri(kapanis, ts, [True] * len(d_kural))

    # ADIL KONTROL: ayni diziyi rastgele noktadan dairesel kaydir
    rnd = random.Random(TOHUM)
    n = len(d_kural)
    sonlar = []
    for _ in range(200):
        k = rnd.randrange(n)
        kaydirilmis = d_kural[k:] + d_kural[:k]
        sonlar.append(_getiri(kapanis, ts, kaydirilmis)[0][-1][1])
    sonlar.sort()
    kontrol = sonlar[len(sonlar)//2]        # MEDYAN — dagilim CARPIK

    yil = n * ADIM / 252
    fk = kural[-1][1] ** (1 / yil) - 1
    fa = altut[-1][1] ** (1 / yil) - 1
    fr = kontrol ** (1 / yil) - 1
    ustunde = sum(1 for x in sonlar if x < kural[-1][1]) / len(sonlar)
    print(f"{ad:<21}{yil:>6.1f}{fk * 100:>7.1f}%{_dusus(kural):>7}%"
          f"{fa * 100:>7.1f}%{_dusus(altut):>7}%{fr * 100:>11.1f}%"
          f"{(fk - fr) * 100:>6.1f}%{islem / yil:>9.2f}")
    print(f"{'':21}kural, kaydirilmis 200 turun %{ustunde * 100:.0f}'i kuralin ALTINDA")
db.close()
