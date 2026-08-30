"""
Kural vs AL-TUT: getiri DEGIL, DUSUS karsilastirmasi.

"Al-tut'tan az kazandi" tek basina eleme sebebi degil — daha az
dususle daha az kazanmak mesru bir tercihtir. Olculmeden bilinemez.
"""
import sys

sys.path.insert(0, "/Users/alipala/github/bist-bux-agent/src")

from finagent.analysis.momentum_kosu import _takvim, kosu           # noqa: E402
from finagent.analysis.backtest import _endeks_serisi               # noqa: E402
from finagent.config import load_settings                           # noqa: E402
from finagent.storage.db import Database                            # noqa: E402

BAS, BIT = "1998-12-22", "2026-08-28"


def _dusus(egri):
    """En derin tepe-dip dususu (%) ve nerede oldugu."""
    tepe, en_kotu, nerede = egri[0][1], 0.0, None
    for ts, v in egri:
        tepe = max(tepe, v)
        d = v / tepe - 1
        if d < en_kotu:
            en_kotu, nerede = d, ts
    return round(en_kotu * 100, 1), nerede


s = load_settings()
db = Database(s.db_path)
takvim = _takvim(db, "SPX", BAS, BIT)
spx = _endeks_serisi(db, "SPX")
r = kosu(db, BAS, BIT, 2, venue="ETF", endeksler=None, asgari_bar=252)
donemler = r["_donemler"]

# Kural egrisi: donem netlerini bilesikle
kural, v = [], 1.0
for d in donemler:
    v *= 1 + d["net"]
    kural.append((takvim[d["bar"] + 63], v))

# AL-TUT egrisi: AYNI tarihlerde SPX
al, taban = [], spx[takvim[donemler[0]["bar"]]]
for d in donemler:
    t = takvim[d["bar"] + 63]
    al.append((t, spx[t] / taban))

print(f"pencere {kural[0][0]} .. {kural[-1][0]}  ·  {len(kural)} ceyrek\n")
print(f"{'':<12}{'bilesik':>10}{'yillik':>9}{'en derin dusus':>17}{'dip tarihi':>13}")
print("-" * 62)
yil = (len(kural) * 63) / 252
for ad, egri in (("KURAL", kural), ("AL-TUT SPX", al)):
    son = egri[-1][1]
    d, ne = _dusus(egri)
    print(f"{ad:<12}{(son - 1) * 100:>9.1f}%{(son ** (1 / yil) - 1) * 100:>8.1f}%"
          f"{d:>16}%{str(ne):>13}")

# En kotu ceyrekler ve nakitte kalinan donemler
net = sorted(donemler, key=lambda x: x["net"])
print(f"\nkuralin en kotu 3 ceyregi: "
      + ", ".join(f"{takvim[d['bar']]} %{d['net'] * 100:.1f} ({d['secilen']})"
                  for d in net[:3]))
nakit = [d for d in donemler if d["adet"] == 0]
print(f"sinyal kapisi NAKITTE tuttu: {len(nakit)}/{len(donemler)} ceyrek"
      + (" -> " + ", ".join(takvim[d["bar"]] for d in nakit[:8]) if nakit else ""))

# Buyuk ayi piyasalarinda ne oldu
print("\nBUYUK DUSUSLERDE (ceyrek net, kural vs SPX):")
for et, a, b in (("dot-com 2000-02", "2000-01-01", "2002-12-31"),
                 ("kriz 2008-09", "2008-01-01", "2009-06-30"),
                 ("covid 2020", "2020-01-01", "2020-06-30"),
                 ("2022", "2022-01-01", "2022-12-31")):
    kg = [d for d in donemler if a <= takvim[d["bar"]] <= b]
    if not kg:
        continue
    kk = 1.0
    for d in kg:
        kk *= 1 + d["net"]
    t0, t1 = takvim[kg[0]["bar"]], takvim[kg[-1]["bar"] + 63]
    sp = spx[t1] / spx[t0] - 1
    print(f"  {et:<18} kural %{(kk - 1) * 100:>7.1f}   SPX %{sp * 100:>7.1f}"
          f"   ({len(kg)} ceyrek)")
db.close()
