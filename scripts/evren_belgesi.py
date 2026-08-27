"""
`docs/ibkr-evren.md` uretir — Adim 1'in teslimati.

SESSIZ KIRPMA YASAK: serisi olmayan sembol tabloda SEBEBIYLE listelenir.
Sebep TAHMIN EDILMEZ, Yahoo'ya tek bir dogrulama cagrisi ile SORULUR.
"""
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "src")
from finagent.config import load_settings                    # noqa: E402
from finagent.storage import Database                        # noqa: E402
from finagent.collectors.prices import yahoo_veri            # noqa: E402
from finagent.research.identity import ayni_sirket           # noqa: E402

CIKTI = "docs/ibkr-evren.md"

s = load_settings()
db = Database(s.db_path)
ayar = s.strateji_ayari(db)
evren = db.endeks_uyeleri(ayar["endeksler"])
asgari_bar = ayar["asgari_bar"]
esik = ayar["asgari_devir"]

conid = {r["instrument_id"]: r["conid"] for r in db.query(
    "SELECT instrument_id, conid FROM identities "
    "WHERE conid IS NOT NULL AND conid <> ''")}
endeks_ad = {}
for r in db.query("SELECT instrument_id, index_name FROM index_members"):
    endeks_ad.setdefault(r["instrument_id"], []).append(r["index_name"])


def devir_medyani(seri, n=20):
    d = [(b["close"] or 0) * (b["volume"] or 0) for b in seri[-n:]
         if b["close"] and b["volume"]]
    return statistics.median(d) if d else None


satirlar, serisiz = [], []
for e in evren:
    seri = db.fiyat_serisi(e["id"], 3000)
    if not seri:
        serisiz.append(e)
        continue
    k = db.fiyat_kaynagi(e["id"])
    satirlar.append({
        "sembol": e["symbol"], "ad": e["name"],
        "endeks": "/".join(sorted(x[:3] for x in endeks_ad.get(e["id"], []))),
        "bar": len(seri), "ilk": seri[0]["ts"], "son": seri[-1]["ts"],
        "ccy": (k or {}).get("currency"), "kaynak": (k or {}).get("source"),
        "devir": devir_medyani(seri),
        "conid": conid.get(e["id"]),
    })

# --- SEBEP SORULUYOR, TAHMIN EDILMIYOR -------------------------------
print(f"serisiz {len(serisiz)} sembol — sebep Yahoo'ya soruluyor", file=sys.stderr)
sebepler = []
for e in serisiz:
    sembol = (e["symbol"] or "").upper()
    adaylar = [sembol] + ([sembol.replace(".", "-")] if "." in sembol else [])
    sebep = "Yahoo'da seri yok"
    for aday in adaylar:
        try:
            sat, meta = yahoo_veri(aday, "10y", ad_gerek=True)
        except Exception as ex:                              # noqa: BLE001
            sebep = f"cagri hatasi: {type(ex).__name__}"
            continue
        if not sat:
            continue
        # KURAL COLLECTOR'INKIYLE AYNI OLMALI: iki ad da soruluyor.
        # Burada yalnizca `shortName`e bakmak, belgeye YANLIS bir sebep
        # yazardi ("ad eslesmedi" derken aslinda longName denenmemis).
        adlari = [meta.get("shortName"), meta.get("longName")]
        if not any(ayni_sirket(e["name"], o) for o in adlari if o):
            sebep = (f"ad eslesmedi (bizde {e['name']!r}, Yahoo short="
                     f"{adlari[0]!r} long={adlari[1]!r})")
        else:
            sebep = f"ad ESLESTI ama yazilmamis — {aday}, {len(sat)} bar"
        break
    sebepler.append((sembol, e["name"], sebep))
    time.sleep(0.1)

# ----------------------------------------------------------------------
derin = [r for r in satirlar if r["bar"] >= asgari_bar]
usd = [r for r in satirlar if (r["ccy"] or "") == "USD"]
likit = [r for r in usd if r["devir"] and r["devir"] >= esik.get("USD", 0)]
conidli = [r for r in satirlar if r["conid"]]

bugun = datetime.now(timezone.utc).date().isoformat()
o = []
o.append("# IBKR strateji evreni — ölçüm\n")
o.append(f"**Üretildi:** {bugun} · `scripts/evren_belgesi.py`  ")
o.append("**Kaynak belge:** `docs/finagent-strateji-motoru.md` (Adım 1)  ")
o.append(f"**Evren tanımı:** `ibkr.strateji.endeksler` = "
         f"{', '.join(ayar['endeksler'])}\n")
o.append("Bu dosya ÜRETİLİR, elle yazılmaz. Her sayı veritabanından "
         "okundu; seri bulunamayan sembollerin sebebi Yahoo'ya tek tek "
         "SORULDU (tahmin edilmedi).\n")
o.append("## Özet\n")
o.append("| Ölçüt | Sayı | Oran |")
o.append("|---|---|---|")
n = len(evren)
for ad, kume in (("Endeks üyesi (tekil)", evren),
                 ("Fiyat serisi var", satirlar),
                 (f"≥{asgari_bar} bar (backtest derinliği)", derin),
                 ("USD kote", usd),
                 (f"USD kote + devir ≥ {esik.get('USD', 0):,.0f}", likit),
                 ("conid çözülmüş", conidli)):
    o.append(f"| {ad} | {len(kume)} | %{100*len(kume)/n:.1f} |")
o.append("")

if sebepler:
    o.append(f"## Serisi olmayan {len(sebepler)} sembol — sebebiyle\n")
    o.append("| Sembol | Katalog adı | Sebep |")
    o.append("|---|---|---|")
    for sym, ad, sebep in sorted(sebepler):
        o.append(f"| `{sym}` | {ad} | {sebep} |")
    o.append("")
else:
    o.append("## Serisi olmayan sembol yok\n")

o.append(f"## {len(satirlar)} sembol — tam tablo\n")
o.append("`devir` = son 20 barın (kapanış × hacim) medyanı, kotasyon "
         "para biriminde. `bar`/`ilk` `db.fiyat_serisi()`'nden — yani "
         "kaynak seçimi uygulanmış TEK seriden.\n")
o.append("| Sembol | Ad | Endeks | Bar | İlk tarih | Son | Kur | Kaynak | "
         "Devir (medyan) | conid |")
o.append("|---|---|---|---|---|---|---|---|---|---|")
for r in sorted(satirlar, key=lambda x: x["sembol"]):
    d = f"{r['devir']:,.0f}" if r["devir"] else "—"
    o.append(f"| `{r['sembol']}` | {r['ad']} | {r['endeks']} | {r['bar']} | "
             f"{r['ilk']} | {r['son']} | {r['ccy'] or '—'} | "
             f"{r['kaynak'] or '—'} | {d} | {r['conid'] or '—'} |")
o.append("")

with open(CIKTI, "w", encoding="utf-8") as f:
    f.write("\n".join(o))
print(f"yazildi: {CIKTI} ({len(satirlar)} satir, {len(sebepler)} serisiz)")
