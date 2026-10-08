"""
YATIRIM POLITIKASI (IPS) — sahibin yazili kurallari ve onlara uyum.

NEDEN VAR
---------
Ali 9 Eki: "uzman bir portfoy yonetimi sirketi olsan ne eklerdin" sorusuna
verilen planin 1. adimi. Olcumler alim tarafinda kenar olmadigini
gostermisti (bkz. hafiza: strateji-kenari-yok); bir sirketin degeri ise
surecten gelir: yazili hedef, dagilim, tavanlar. Ali'nin cevaplari
(9 Eki): tahammul en fazla -%20, Oneri A dagilimi, tek hisse tavani %5,
tek tema tavani %20, Kasim sonu 4.000 € cekim, kaldirac/opsiyon gercek
parayla yok (once ogrenme), video/reel/haber kaynakli kararda danisman
kontrolu, kaynak beyani ZORUNLU DEGIL.

KURAL DEGIL UYARI
-----------------
Politika EMRI ENGELLEMEZ; ihlali SOYLER. Karar sahibin. Engelleyen bir
politika, kullaniciyi botu atlatip dogrudan uygulamaya gitmeye iter ve
o zaman hicbir kontrol kalmaz.

HESAP YENIDEN YAZILMAZ
----------------------
EUR cevrimi `analysis.tema._eur` (kuru olmayan pozisyon hesaba GIRMEZ ve
adiyla soylenir), tema yogunlasmasi `analysis.tema.yogunlasma` (fon
icerikleri acilmaz -> sonuc ALT SINIR, soylenir). Pozisyonlar hesap
basina son anlik goruntuden; adet/deger 0 olan satilmistir.

SINIF NEREDEN
-------------
BUX ETF'leri, SpaceX, ING kayitta TURSUZ duruyor (olculdu 9 Eki). Sinif
once politikadaki acik listeden (`siniflar`), sonra `asset_type`ten
(equity/stk -> tek_hisse, crypto -> kripto, cash -> nakit) gelir; hicbiri
degilse `siniflanmamis` olarak ADIYLA raporlanir — sessizce bir sinifa
atanmaz.
"""
from __future__ import annotations

import json
import logging
from datetime import date

log = logging.getLogger(__name__)

SINIFLAR = ("genis_etf", "tahvil", "tek_hisse", "altin", "kripto",
            "tematik_etf")
ZORUNLU = ("hedef", "tavan", "sapma_puan", "tahammul_pct")
DANISMAN_KAYNAKLARI = ("video", "reel", "haber")


def politika(settings, sahip: str) -> dict | None:
    """
    Sahibin politikasi, DOGRULANMIS. Tanimli degilse None (bir arıza
    degil: politikasi olmayan sahip vardir). Tanimli ama bozuksa
    ValueError — yanlis bir tavanla "uyumlu" demek, hic dememekten kotu.
    """
    if not sahip:
        raise ValueError("sahip zorunlu")
    p = (settings.get("ips") or {}).get(sahip)
    if p is None:
        return None
    if not isinstance(p, dict):
        raise ValueError(f"ips.{sahip} sozluk olmali")
    eksik = [k for k in ZORUNLU if k not in p]
    if eksik:
        raise ValueError(f"ips.{sahip} eksik alan: {', '.join(eksik)}")
    hedef = p["hedef"]
    if not isinstance(hedef, dict) or set(hedef) - set(SINIFLAR):
        raise ValueError(f"ips.{sahip}.hedef bilinmeyen sinif: "
                         f"{sorted(set(hedef) - set(SINIFLAR))}")
    toplam = sum(float(v) for v in hedef.values())
    if abs(toplam - 100) > 0.01:
        raise ValueError(f"ips.{sahip}.hedef toplami 100 olmali, {toplam}")
    for ad in ("tek_hisse", "tema"):
        v = (p["tavan"] or {}).get(ad)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 100:
            raise ValueError(f"ips.{sahip}.tavan.{ad} 0-100 arasi sayi olmali: {v!r}")
    for ad in ("sapma_puan", "tahammul_pct"):
        v = p[ad]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ValueError(f"ips.{sahip}.{ad} pozitif sayi olmali: {v!r}")
    siniflar = p.get("siniflar") or {}
    if set(siniflar) - set(SINIFLAR):
        raise ValueError(f"ips.{sahip}.siniflar bilinmeyen sinif: "
                         f"{sorted(set(siniflar) - set(SINIFLAR))}")
    return p


def _tr(v: float) -> str:
    """Kullaniciya giden yuzde: 18.2 -> '18,2', 5.0 -> '5'."""
    return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def _sembol_sinifi(p: dict) -> dict[str, str]:
    return {str(s).upper(): sinif
            for sinif, liste in (p.get("siniflar") or {}).items()
            for s in (liste or [])}


def sinif_bul(p: dict, sembol: str, asset_type: str | None) -> str:
    """Once acik liste, sonra kayit turu; ikisi de yoksa 'siniflanmamis'."""
    acik = _sembol_sinifi(p).get((sembol or "").upper())
    if acik:
        return acik
    tur = (asset_type or "").lower()
    if tur in ("equity", "stk"):
        return "tek_hisse"
    if tur == "crypto":
        return "kripto"
    if tur == "cash" or (sembol or "").upper().startswith("CASH"):
        return "nakit"
    return "siniflanmamis"


def _pozisyonlar(db, sahip: str) -> tuple[list[dict], list[str], list[dict]]:
    """(EUR'lu pozisyonlar, cevrilemeyenler, hesap tarihleri)."""
    from .tema import _eur
    out, cevrilemeyen, hesaplar = [], [], []
    for hesap in db.hesaplar(sahip):
        satir = db.latest_positions(hesap, sahip)
        if satir:
            hesaplar.append({"hesap": hesap, "son": str(satir[0]["snapshot_ts"])[:10]})
        for r in satir:
            deger = r["market_value"] or 0
            if deger <= 0 or (r["quantity"] is not None and r["quantity"] <= 0):
                continue
            e = _eur(db, float(deger), r["currency"])
            if e is None:
                cevrilemeyen.append(f"{r['symbol']} ({r['currency'] or '?'})")
                continue
            out.append({"sembol": r["symbol"], "instrument_id": r["instrument_id"],
                        "hesap": hesap, "asset_type": r["asset_type"], "eur": e})
    return out, cevrilemeyen, hesaplar


def durum(db, settings, sahip: str) -> dict | None:
    """
    Politikaya gore portfoyun bugunku hali: sinif dagilimi ve sapmasi,
    tek hisse ve tema tavan ihlalleri, siniflanmamis/cevrilemeyen
    kalemler, hesap tarihleri. Politika yoksa None.
    """
    p = politika(settings, sahip)
    if p is None:
        return None
    poz, cevrilemeyen, hesaplar = _pozisyonlar(db, sahip)
    toplam = sum(x["eur"] for x in poz if sinif_bul(p, x["sembol"], x["asset_type"]) != "nakit")
    if toplam <= 0:
        return {"toplam_eur": 0, "hesaplar": hesaplar,
                "not": "degerlenebilir pozisyon yok (ekran goruntusu gonderilmemis olabilir)"}
    sinif_eur: dict[str, float] = {}
    hisse: dict[str, float] = {}
    siniflanmamis = []
    for x in poz:
        s = sinif_bul(p, x["sembol"], x["asset_type"])
        if s == "nakit":
            continue
        if s == "siniflanmamis":
            siniflanmamis.append(x["sembol"])
        sinif_eur[s] = sinif_eur.get(s, 0.0) + x["eur"]
        if s == "tek_hisse":
            hisse[x["sembol"]] = hisse.get(x["sembol"], 0.0) + x["eur"]
    sapma = float(p["sapma_puan"])
    dagilim = []
    for s in list(SINIFLAR) + (["siniflanmamis"] if siniflanmamis else []):
        hedef = float(p["hedef"].get(s, 0))
        pay = sinif_eur.get(s, 0.0) / toplam * 100
        if not hedef and not pay:
            continue
        dagilim.append({"sinif": s, "eur": round(sinif_eur.get(s, 0.0), 2),
                        "pay_%": round(pay, 1), "hedef_%": hedef,
                        "fark_puan": round(pay - hedef, 1),
                        "sapma_asildi": abs(pay - hedef) > sapma})
    tavan_h = float(p["tavan"]["tek_hisse"])
    hisse_ihlal = [{"sembol": k, "pay_%": round(v / toplam * 100, 1), "tavan_%": tavan_h}
                   for k, v in sorted(hisse.items(), key=lambda kv: -kv[1])
                   if v / toplam * 100 > tavan_h]
    tema_ihlal = []
    try:
        from .tema import yogunlasma
        y = yogunlasma(db, sahip, ilk=10)
        tavan_t = float(p["tavan"]["tema"])
        tema_ihlal = [{"tema": t["tema"], "pay_%": t["toplam_%"], "tavan_%": tavan_t,
                       "sirketler": t["sirketler"]}
                      for t in y.get("temalar") or [] if t["toplam_%"] > tavan_t]
    except Exception as e:                                # noqa: BLE001
        log.warning("[ips] tema yogunlasmasi okunamadi: %s", e)
    return {
        "toplam_eur": round(toplam, 2),
        "dagilim": dagilim,
        "tek_hisse_ihlali": hisse_ihlal,
        "tema_ihlali": tema_ihlal,
        "siniflanmamis": sorted(set(siniflanmamis)),
        "cevrilemeyen": cevrilemeyen,
        "hesaplar": hesaplar,
        "not": ("Tema payi bir ALT SINIR: ETF'lerin icindeki sirketler sayilmadi. "
                "Pozisyonlar hesap basina SON ekran goruntusunden; eski tarihli "
                "hesapta satilmis kagit hala gorunebilir."),
    }


def alim_kontrolu(db, settings, sahip: str, sembol: str, tutar_eur: float | None,
                  kaynak: str | None = None) -> dict | None:
    """
    Varsayimsal alimin politikaya etkisi. `tutar_eur` yoksa yalnizca mevcut
    pay ve kurallar. Doner: {ihlaller, uyarilar, ...}; politika yoksa None.
    """
    p = politika(settings, sahip)
    if p is None:
        return None
    sem = (sembol or "").strip().upper()
    if not sem:
        raise ValueError("sembol zorunlu")
    poz, _, _ = _pozisyonlar(db, sahip)
    d = durum(db, settings, sahip) or {}
    toplam = float(d.get("toplam_eur") or 0)
    ek = float(tutar_eur or 0)
    ins = db.query("SELECT id, asset_type FROM instruments WHERE UPPER(symbol) = ? "
                   "ORDER BY id LIMIT 1", (sem,))
    asset_type = ins[0]["asset_type"] if ins else None
    sinif = sinif_bul(p, sem, asset_type)
    mevcut = sum(x["eur"] for x in poz if (x["sembol"] or "").upper() == sem)
    yeni_toplam = toplam + ek
    yeni_pay = (mevcut + ek) / yeni_toplam * 100 if yeni_toplam else None
    ihlaller, uyarilar = [], []
    tavan_h = float(p["tavan"]["tek_hisse"])
    if sinif == "tek_hisse" and yeni_pay is not None and yeni_pay > tavan_h:
        ihlaller.append(f"{sem}: alımdan sonra portföydeki payı %{_tr(yeni_pay)} "
                        f"olur (tek hisse tavanı %{_tr(tavan_h)})")
    if sinif == "siniflanmamis":
        uyarilar.append(f"{sem} politikadaki hiçbir sınıfa atanmamış; tavan "
                        "denetimi yapılamadı")
    # Tema: kagidin kendi temalari, bugunku paylarina alim eklenerek.
    tema_sonuc = []
    if ins and sinif == "tek_hisse":
        r = db.query("SELECT durum, temalar FROM sirket_tema WHERE instrument_id = ?",
                     (ins[0]["id"],))
        temalar = json.loads(r[0]["temalar"] or "[]") if r and r[0]["durum"] == "tamam" else []
        try:
            from .tema import yogunlasma
            mevcut_t = {t["tema"]: t["toplam_%"] for t in
                        (yogunlasma(db, sahip, ilk=50).get("temalar") or [])}
        except Exception:                                 # noqa: BLE001
            mevcut_t = {}
        tavan_t = float(p["tavan"]["tema"])
        for t in temalar:
            eski = mevcut_t.get(t, 0.0) / 100 * toplam
            yeni = (eski + ek) / yeni_toplam * 100 if yeni_toplam else 0
            tema_sonuc.append({"tema": t, "yeni_pay_%": round(yeni, 1)})
            if yeni > tavan_t:
                ihlaller.append(f"'{t}' teması: alımdan sonra %{_tr(yeni)} olur "
                                f"(tema tavanı %{_tr(tavan_t)}; ETF içerikleri sayılmadı, "
                                "gerçek pay daha yüksek olabilir)")
        if not temalar:
            uyarilar.append(f"{sem} için tema verisi yok; tema tavanı denetlenemedi")
    hedef = float(p["hedef"].get(sinif, 0))
    if sinif in SINIFLAR and not hedef:
        uyarilar.append(f"{sem} '{sinif}' sınıfında; politikada bu sınıfın hedefi %0")
    # Yaklasan bilanco (14 gun): kisa vadede buyuk hareket beklenir.
    if ins:
        b = db.query("SELECT MIN(tarih) t FROM bilanco_takvimi WHERE instrument_id = ? "
                     "AND tarih >= date('now') AND tarih <= date('now', '+14 days')",
                     (ins[0]["id"],))
        if b and b[0]["t"]:
            uyarilar.append(f"{sem} bilançosu {b[0]['t']}: yönü bilinmez, büyük hareket beklenir")
    danisman = None
    if (kaynak or "").lower() in DANISMAN_KAYNAKLARI:
        danisman = [
            "Kaynagin kademesi ne? Video/reel kademe 4: KANIT DEGIL, fikir.",
            "Bu bilgi fiyata yansidi mi? Son gunlerin hareketine bak.",
            "Karsi tez: bu fikri ne curutur? Yazilamiyorsa alim fikri hazir degil.",
            "Politika tavanlari (yukarida) ve hedef dagilimdaki yeri.",
        ]
    return {"sembol": sem, "sinif": sinif, "mevcut_eur": round(mevcut, 2),
            "tutar_eur": ek or None,
            "yeni_pay_%": round(yeni_pay, 1) if yeni_pay is not None else None,
            "temalar": tema_sonuc, "ihlaller": ihlaller, "uyarilar": uyarilar,
            "danisman_kontrolu": danisman,
            "kaldirac_opsiyon": p.get("kaldirac_opsiyon"),
            "not": "Politika engellemez, soyler. Karar sahibin."}


def ozet_metni(p: dict) -> str:
    """Modele giden kisa politika metni (sistem bağlamı)."""
    h = ", ".join(f"{k} %{v:g}" for k, v in p["hedef"].items())
    return (f"Hedef dagilim: {h}. Tavanlar: tek hisse %{p['tavan']['tek_hisse']:g}, "
            f"tek tema %{p['tavan']['tema']:g}. Tahammul: kotu bir yilda en fazla "
            f"-%{p['tahammul_pct']:g}. Dengeleme esigi ±{p['sapma_puan']:g} puan. "
            + (f"Amac: {p['amac']}. " if p.get("amac") else "")
            + (f"Kaldirac/opsiyon: {p['kaldirac_opsiyon']}. " if p.get("kaldirac_opsiyon") else ""))


# ---------------------------------------------------------------------------
# plan hatirlatmalari
# ---------------------------------------------------------------------------

def bugunku_adimlar(p: dict, kip: str, bugun: date, gonderilen: set[str]) -> list[dict]:
    """
    SAF. Tarihi bugun ya da GECMIS, kipi uyan, daha once gonderilmemis
    adimlar. Gecmis tarih dahil: o gunun kosusu dusmusse hatirlatma
    kaybolmasin (bir sonraki uygun kosuda gider).
    """
    out = []
    for a in p.get("plan") or []:
        anahtar = f"{a['tarih']}|{a.get('kip', 'sabah')}|{a['metin'][:40]}"
        if anahtar in gonderilen or a.get("kip", "sabah") != kip:
            continue
        if date.fromisoformat(str(a["tarih"])) <= bugun:
            out.append({**a, "_anahtar": anahtar})
    return out


def siradaki_adimlar(p: dict, bugun: date, azami: int = 3) -> list[dict]:
    """SAF. Bugun ve sonrasi plan adimlari (modele baglam: 'Kasim'da cekim
    var' bilgisi olmadan verilen tavsiye eksik kalir — olculdu 9 Eki e2e)."""
    gelecek = sorted((a for a in p.get("plan") or []
                      if date.fromisoformat(str(a["tarih"])) >= bugun),
                     key=lambda a: str(a["tarih"]))
    # "tarih" HATIRLATMA gunudur, son gun DEGIL (son gun metinde yazar).
    # Olculdu 9 Eki e2e: model "tarih"i son gun okudu ("19 Ekim'e kadar").
    return [{"hatirlatma_tarihi": str(a["tarih"]), "metin": a["metin"]}
            for a in gelecek[:azami]]


def adim_metni(db, sahip: str, adim: dict) -> str:
    """Adim + kayittaki durum ("hala elde" / "satilmis gorunuyor")."""
    import html
    e = html.escape
    satir = [f"🗓 <b>Plan adımı</b> — {e(str(adim['tarih']))}", "", e(adim["metin"])]
    sem = [str(s).upper() for s in adim.get("semboller") or []]
    if sem:
        eldeki = db.sahip_eldeki_idleri(sahip)
        id_sem = {r["id"]: r["symbol"].upper() for r in db.query(
            "SELECT id, symbol FROM instruments WHERE UPPER(symbol) IN (%s)"
            % ",".join("?" * len(sem)), tuple(sem))}
        hala = sorted({s for i, s in id_sem.items() if i in eldeki})
        bitti = sorted(set(sem) - set(hala))
        satir.append("")
        if hala:
            satir.append("Kayıtta hâlâ elde: <b>" + e(", ".join(hala)) + "</b>")
        if bitti:
            satir.append("Kayıtta satılmış/yok: " + e(", ".join(bitti)))
        satir.append("<i>Kayıt son ekran görüntüsüne göre; işleminden sonra "
                     "görüntü gönderirsen güncellenir.</i>")
    satir.append("\n<i>Plan senin onayladığın politikadan; emri sen verirsin, "
                 "sistem BUX'ta işlem yapamaz.</i>")
    return "\n".join(satir)
