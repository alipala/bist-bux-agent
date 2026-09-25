"""
TEMA YOGUNLASMASI (IBKR MCP Faz 5).

Soru: portfoyun ne kadari AYNI hikayeye bagli? Olculdu (25 Eyl,
docs/tema-olcumu.md): toplam portfoyun %55'i "Semiconductor Chips"
temasinda — ASML, NVDA, MRVL, QCOM, AVGO. Bu oran hicbir bot ciktisinda
gorunmuyordu; sektor siniflandirmasi hic yoktu.

KURAL (Ali, 25 Eyl, B secenegi): haftalik sabit satir YOK. Temalar
yalnizca portfoye yeni sirket girince cekilir (`collectors/sirkettema`);
yogunlasma, soruldugunda ANLIK pozisyonlardan hesaplanir.

OKUMA KURALLARI
---------------
* Bir sirket birden cok temaya bagli: yuzdeler TOPLANMAZ.
* Fonlar (CNDX, VUSA ...) tema tasimaz; ICERIKLERI acilmaz. Sonuc bu
  yuzden bir ALT SINIR: CNDX'in icindeki NVDA sayilmiyor. Soylenir.
* Degerler EUR'ya `db.fx_kuru` ile cevrilir; kuru olmayan pozisyon
  HESABA GIRMEZ ve adiyla soylenir (cevrilmemis toplam bu deponun en eski
  hata sinifi — bkz. `maruziyet` araci).
"""
from __future__ import annotations

import json

HISSE_TURLERI = ("equity", "stk")
NAKIT_TURLERI = ("cash",)
# TURU BOS enstrumanlar (BUX'ta fonlar VE bazi hisseler: AVTX, INGA —
# olculdu 25 Eyl). Tur uydurulmaz: conid'i varsa tema istenir; IBKR tema
# verirse hisse sayilir, vermezse "sinifi bilinmiyor" olarak SOYLENIR.
BILINMEYEN_TUR = ("",)


def _eur(db, deger: float, pb: str) -> float | None:
    pb = (pb or "").upper()
    if pb == "EUR":
        return deger
    if not pb:
        return None
    k = db.fx_kuru("USD" if pb == "USDT" else pb, "EUR")
    return deger * float(k["rate"]) if k and k.get("rate") else None


def yogunlasma(db, sahip: str, ilk: int = 5) -> dict:
    """
    Sahibin TUM hesaplarindaki son anlik goruntulerden tema yogunlasmasi.
    Doner: {toplam_eur, hisse_eur, temalar: [{tema, toplam_%, hisse_%,
    sirketler}], tema_verisi_yok: [...], cevrilemeyen: [...], fon: [...],
    not}. Pozisyon yoksa {"toplam_eur": 0}.
    """
    kayit = {r["instrument_id"]: r for r in db.query(
        "SELECT instrument_id, durum, temalar FROM sirket_tema")}
    toplam = hisse = 0.0
    tema_deger: dict[str, float] = {}
    tema_sirket: dict[str, list[str]] = {}
    yok, cevrilemeyen, fon, kripto = [], [], [], []
    for hesap in db.hesaplar(sahip):
        for p in db.latest_positions(hesap, sahip):
            deger = p["market_value"] or 0
            if deger <= 0:
                continue
            e = _eur(db, float(deger), p["currency"])
            if e is None:
                cevrilemeyen.append(f"{p['symbol']} ({p['currency'] or '?'})")
                continue
            toplam += e
            tur = (p["asset_type"] or "").lower()
            if tur in NAKIT_TURLERI or (p["symbol"] or "").upper().startswith("CASH"):
                continue
            r = kayit.get(p["instrument_id"])
            temalar = json.loads(r["temalar"] or "[]") if r and r["durum"] == "tamam" else []
            if tur not in HISSE_TURLERI and not (tur in BILINMEYEN_TUR and temalar):
                (kripto if tur == "crypto" else fon).append(p["symbol"])
                continue
            hisse += e
            if not temalar:
                yok.append(p["symbol"])
                continue
            for t in temalar:
                tema_deger[t] = tema_deger.get(t, 0.0) + e
                tema_sirket.setdefault(t, []).append(p["symbol"])
    if toplam <= 0:
        return {"toplam_eur": 0}
    sirali = sorted(tema_deger.items(), key=lambda kv: (-kv[1], kv[0]))[:ilk]
    return {
        "toplam_eur": round(toplam, 2), "hisse_eur": round(hisse, 2),
        "temalar": [{"tema": t, "toplam_%": round(v / toplam * 100, 1),
                     "hisse_%": round(v / hisse * 100, 1) if hisse else None,
                     "sirketler": sorted(set(tema_sirket[t]))}
                    for t, v in sirali],
        "tema_verisi_yok": sorted(set(yok)),
        "cevrilemeyen": cevrilemeyen,
        # "fon" KESIN DEGIL: turu bos ve IBKR'nin tema vermedigi her sey
        # burada (fonlar, sertifikalar, ana listelemesi bulunamayan hisse).
        "fon_ya_da_sinifi_bilinmeyen": sorted(set(fon)),
        "kripto": sorted(set(kripto)),
        "not": ("Bir sirket birden cok temada: yuzdeler TOPLANMAZ. Fonlarin "
                "icerigi acilmadi -> gercek yogunlasma bundan YUKSEK olabilir. "
                "Tema IBKR'nin siniflandirmasi; tavsiye degil."),
    }


def alternatif_listeleme(sonuc, conid: int, sembol: str) -> list[int]:
    """
    SAF. `search_contracts` sonucundan AYNI sirketin DIGER listelemeleri.

    OLCULDU (25 Eyl): ASML'in ABD conid'i tema dondurmedi, Amsterdam conid'i
    alti tema dondurdu — tema sirketin ANA listelemesine bagli. Yanlis
    sirkete tema yapistirmamak icin iki sart: sembol BIREBIR ayni ve
    aciklamanin ILK KELIMESI bizim listelemeninkiyle ayni. Bizim conid
    sonuclarda yoksa karsilastirma yapilamaz -> bos liste (tahmin yok).
    """
    # OLCULEN bicim (25 Eyl): {"results": [...], "totals": ...}. Ilk
    # yazimda duz liste varsayilmisti, test fiksturu de oyle yazilmisti —
    # sahada ASML'in ana listelemesine HIC dusulmedi.
    if isinstance(sonuc, dict):
        sonuc = sonuc.get("results")
    satirlar = sonuc if isinstance(sonuc, list) else []
    bizim = next((r for r in satirlar
                  if str(r.get("underlying_contract_id")) == str(conid)), None)
    if bizim is None:
        return []
    kok = (str(bizim.get("description") or "").split() or [""])[0].upper()
    if not kok:
        return []
    out = []
    for r in satirlar:
        cid = r.get("underlying_contract_id")
        if (cid is None or str(cid) == str(conid)
                or str(r.get("symbol") or "").upper() != sembol.upper()):
            continue
        if (str(r.get("description") or "").split() or [""])[0].upper() == kok:
            out.append(int(cid))
    return out
