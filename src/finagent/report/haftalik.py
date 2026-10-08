"""
HAFTALIK RAPOR — "Bu hafta ne kacirdim" (gorsel).

NEDEN VAR
---------
Ali 2026-10-08: finansal okuryazarligini bu botla gelistirmek istiyor; "bu
hafta ne kacirdim" sorusunu Telegram'da gorsel bir rapor olarak gormek
istedi. Telegram mesaji HTML sayfa gosteremez; rapor HTML olarak kurulur,
Chromium (Playwright) ile PNG kartlara cevrilir ve fotograf olarak gider.
Ayni sablon ileride Mini App sayfasi olarak kullanilabilir.

HESAP YENIDEN YAZILMAZ
----------------------
Veri katmaninin bilinen tuzaklari var (para birimi, seri tabani, sermaye
islemi, karne ajan varsayilani). Rapor sohbetin kullandigi yollari cagirir:

  * seri         `ToolBox._seri_id` (tek mesru seri yolu + sermaye kapisi)
  * getiri       `karsilastirma.getiri_ozeti` (zincirleme, sermaye gunu disi)
  * haber        `haber_ilgi.haber_dosyasi` — YAN ETKISIZ (`haberler` araci
                 bayat sembolde canli RSS ceker; rapor bunu yapmamali)
  * karne        `journal.ajan_karnesi` (ajan ZORUNLU, ayni gun tabani)
  * emir sonucu  `tools.emir_sonucu`, beyan etiketi `emir_kanit.BEYANLAR`
  * bilanco      `bilancotakvim.yaklasan_bilancolar`

DURUSTLUK KURALLARI
-------------------
* Ayni haftanin haberi "SEBEP" diye sunulmaz: hareketin sebebi oldugu
  OLCULMEDI (hafiza: haber-baglama-fizibilitesi — buyuk hareketlerin
  %95'inde kaynak yok). Etiket "haftanin haberi".
* Olculemeyen pozisyon SESSIZCE dusmez: sebebiyle `olculemeyen`e yazilir.
* Karne tabaniyla birlikte verilir; %50'ye karsi DEGIL (hafiza: dort ajan
  incelemesi — %50 kiyasi yanlis).
* Golge tahminler (`teslim=0`, kullaniciya gitmeyen) sayilmaz.
"""
from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

HAFTA_GUN = 7
SERI_BAR = 20                # 7 takvim gunu + tatil/hafta sonu payi
BAYAT_GUN = 4                # son bar bitisten bu kadar eskiyse "bayat"
PORTFOY_HABERLI = 5          # haber satiriyla gosterilen en buyuk hareket
PORTFOY_KISA = 14            # geri kalani: kisa liste
RADAR_SATIR = 6
SATILAN_SATIR = 5
# TAVAN/TABAN KILIDI: haftanin barlarinin en az yarisi high==low ise kagit
# kilitli (olculdu 8 Eki: radarin ilk 6'si PASEU/HEDEF/BIGEN... ±%40-60,
# hepsi ardisik tavan/taban — islem yapilamayan hareket). Isaretlenir,
# elenmez: kacirilan sey yine kacirilmistir ama alinamazdi.
KILIT_ORAN = 0.5
HABER_AZAMI = 600
# HESAP BAYATLIGI (Ali 8 Eki): BUX/Midas/Binance'in API'si yok, pozisyon
# listesi ekran goruntusunden geliyor. Olculdu 8 Eki: Midas 17 Eyl, Binance
# 3 Eyl'de kalmisti ve rapor o hesaplardaki kagitlari "senin" diye
# gosteriyordu, bunu SOYLEMEDEN. Fiyat taze, pozisyon listesi bayat olabilir.
HESAP_BAYAT_GUN = 7
KART_GENISLIK = 540          # CSS px; 2x olcekle 1080 px PNG

AJAN_ETIKET = {"hakem": "Panel görüşü (hakem)",
               "taktik": "Gün içi taktik (alım/satış)",
               "strateji_secilen": "Strateji önerisi"}

Seri = Callable[[int, int], tuple]

# NAKIT BENZERI: para birimi ve sabit coin pozisyonlari "hisse" degil; haftalik
# getirileri anlamsiz (EUR'nun serisi yok, USDT'ninki 18 Agu'da kalmis —
# olculdu 8 Eki). Olculemeyen listesine dusup gurultu yapmasinlar.
NAKIT_BENZERI = frozenset({"EUR", "USD", "TRY", "GBP", "CHF", "USDT", "USDC",
                           "BUSD", "FDUSD", "DAI"})

# Jev olay etiketi (`haber_jev.OLAY_GORUNEN`). "ilgisiz" haber o sirketin
# haftasi olarak GOSTERILMEZ (olculdu 8 Eki: NVDA'ya "Microsoft brings more
# AI to PCs" eslesmisti); "belirsiz" en sona.
_ILGISIZ = "ilgisiz"
_BELIRSIZ = "belirsiz"


# ---------------------------------------------------------------------------
# hesap
# ---------------------------------------------------------------------------

def _tarih(ts) -> str:
    return str(ts)[:10]


def haftalik_getiri(barlar: list, limit, bas: str, bit: str) -> dict:
    """
    SAF. `bas` gunu (dahil) ve oncesindeki SON kapanistan, `bit`e kadarki
    son kapanisa getiri. Doner: {"getiri_%", "para_birimi", "taban_tarih",
    "son_tarih"} ya da {"neden": "..."} (olculemedi — sessiz dusmez).
    """
    from ..analysis.karsilastirma import getiri_ozeti
    if not barlar:
        return {"neden": "fiyat serisi yok"}
    barlar = [b for b in barlar if _tarih(b["ts"]) <= bit]
    taban_i = None
    for i, b in enumerate(barlar):
        if _tarih(b["ts"]) <= bas:
            taban_i = i
    if taban_i is None:
        return {"neden": "hafta oncesine ait bar yok"}
    son = barlar[-1]
    if _tarih(son["ts"]) <= bas:
        return {"neden": f"bu hafta yeni bar yok (son bar {_tarih(son['ts'])})"}
    oz = getiri_ozeti(barlar[taban_i:], limit)
    if oz.get("toplam_getiri_pct") is None:
        return {"neden": oz.get("hata") or "getiri hesaplanamadi"}
    out = {"getiri_%": oz["toplam_getiri_pct"], "para_birimi": oz.get("para_birimi"),
           "taban_tarih": oz["ilk_tarih"], "son_tarih": oz["son_tarih"]}
    # BAR `sqlite3.Row` OLABILIR (`.get()` YOK — bkz. karsilastirma._al); ilk
    # surum `.get` kullandi, dict'li test gecti, canli veri AttributeError
    # verdi (8 Eki). Erisim tek noktadan.
    from ..analysis.karsilastirma import _al
    hafta = barlar[taban_i + 1:]
    kilitli = [b for b in hafta if _al(b, "high") is not None and _al(b, "low") is not None
               and _al(b, "high") == _al(b, "low")]
    if hafta and len(kilitli) >= KILIT_ORAN * len(hafta):
        out["kilitli"] = True
    if (date.fromisoformat(bit) - date.fromisoformat(oz["son_tarih"])).days > BAYAT_GUN:
        out["bayat"] = True
    if oz.get("sermaye_islemi"):
        out["sermaye_islemi"] = oz["sermaye_islemi"]
    return out


def _haber_dizini(dosya: dict) -> dict[str, list[dict]]:
    """Sembol -> haberler (dosyanin sirasi: kademe once, sonra yeni)."""
    d: dict[str, list[dict]] = {}
    for h in dosya.get("bagli_haberler") or []:
        for s in (h.get("symbols") or "").split(","):
            s = s.strip()
            if s:
                d.setdefault(s, []).append(h)
    return d


def _haftanin_haberi(sym: str, dizin: dict) -> tuple[dict | None, int]:
    """
    En iyi haber: etiketi anlamli olan > etiketsiz > belirsiz; ilgisiz ELENIR.
    Grup icinde dosyanin sirasi (kademe once, sonra yeni) korunur.
    """
    def sira(h):
        o = (h.get("olay_turu") or {}).get(sym)
        return 2 if o == _BELIRSIZ else 1 if o is None else 0
    liste = [h for h in dizin.get(sym) or []
             if (h.get("olay_turu") or {}).get(sym) != _ILGISIZ]
    if not liste:
        return None, 0
    h = sorted(liste, key=sira)[0]
    return ({"baslik": (h.get("title") or "")[:140],
             "yayinci": h.get("publisher"),
             "tarih": _tarih(h.get("published_at")),
             "kademe": h.get("tier"),
             "olay": (h.get("olay_turu") or {}).get(sym),
             "jev": h.get("baglayan") == "jev"}, len(liste))


def _olc(db, seri: Seri, iid: int, bas: str, bit: str) -> dict:
    try:
        barlar, limit, _ = seri(iid, SERI_BAR)
    except Exception as e:                                  # noqa: BLE001
        log.warning("[haftalik] seri okunamadi (%s): %s", iid, e)
        return {"neden": f"seri okunamadi: {type(e).__name__}"}
    return haftalik_getiri(list(barlar), limit, bas, bit)


def topla(db, sahip: str, seri: Seri, bugun: date | None = None) -> dict:
    """
    Raporun VERISI. Ag cagrisi yok; yalnizca veritabani. `seri` ToolBox'in
    `_seri_id`'si (testte taklit).
    """
    if not sahip:
        raise ValueError("sahip zorunlu")
    bugun = bugun or date.today()
    bas = (bugun - timedelta(days=HAFTA_GUN)).isoformat()
    bit = bugun.isoformat()

    from ..analysis.haber_ilgi import haber_dosyasi
    try:
        dosya = haber_dosyasi(db, sahip, pencere_gun=HAFTA_GUN, azami=HABER_AZAMI)
    except Exception as e:                                  # noqa: BLE001
        log.warning("[haftalik] haber dosyasi okunamadi: %s", e)
        dosya = {"bagli_haberler": [], "kapsam": {"hata": str(e)}}
    dizin = _haber_dizini(dosya)

    # --- 1. portfoy -------------------------------------------------------
    tutulan: dict[int, dict] = {}
    satilan: set[int] = set()        # adet 0 satiri olan, hicbir hesapta tutulmayan
    hesap_durumu: list[dict] = []
    for hesap in db.hesaplar(sahip):
        satirlar = db.latest_positions(hesap, sahip)
        if satirlar:
            son = _tarih(satirlar[0]["snapshot_ts"])
            yas = (bugun - date.fromisoformat(son)).days
            hesap_durumu.append({"hesap": hesap, "son": son, "gun": yas,
                                 "bayat": yas > HESAP_BAYAT_GUN})
        for p in satirlar:
            # ADET 0 = SATILMIS (anlik goruntu satiri kalir, adet sifirlanir;
            # `koruma` ile ayni kural). Olculdu 8 Eki: ilk rapor AVGO/MRVL/ASELS'i
            # "senin" diye gosterdi, ucu de adet 0'di.
            if ((p["asset_type"] or "").lower() == "cash" or p["symbol"] == "CASH"
                    or (p["symbol"] or "").upper() in NAKIT_BENZERI):
                continue
            if (p["quantity"] or 0) <= 0:
                satilan.add(p["instrument_id"])
                continue
            k = tutulan.setdefault(p["instrument_id"], {
                "sembol": p["symbol"], "ad": p["name"], "hesaplar": []})
            if hesap not in k["hesaplar"]:
                k["hesaplar"].append(hesap)
    portfoy, olculemeyen = [], []
    for iid, k in tutulan.items():
        g = _olc(db, seri, iid, bas, bit)
        if "neden" in g:
            olculemeyen.append({"sembol": k["sembol"], "neden": g["neden"]})
            continue
        haber, n = _haftanin_haberi(k["sembol"], dizin)
        portfoy.append({**k, **g, "haber": haber, "haber_sayisi": n})
    portfoy.sort(key=lambda x: abs(x["getiri_%"]), reverse=True)
    # Pozisyon YALNIZCA bayat hesaplarda tutuluyorsa satiri isaretlenir:
    # elde olup olmadigi bilinmiyor (satilmis olabilir).
    bayat = {h["hesap"]: h["son"] for h in hesap_durumu if h["bayat"]}
    for p in portfoy:
        if p["hesaplar"] and all(h in bayat for h in p["hesaplar"]):
            p["portfoy_tarihi"] = min(bayat[h] for h in p["hesaplar"])

    # --- 2. radar: izlenen ama tutulmayan (kripto haric) -----------------
    radar, radar_olculemeyen = [], 0
    for r in db.research_targets(kripto=False):
        if r["id"] in tutulan:
            continue
        g = _olc(db, seri, r["id"], bas, bit)
        if "neden" in g or g.get("bayat"):
            radar_olculemeyen += 1
            continue
        haber, n = _haftanin_haberi(r["symbol"], dizin)
        radar.append({"sembol": r["symbol"], "ad": r["name"], **g,
                      "haber": haber, "haber_sayisi": n,
                      # Sattigin kagit radarda KALIR ve isaretlenir: "sattiktan
                      # sonra ne yapti" tam da kacirilan seydir.
                      "satildi": r["id"] in satilan and r["id"] not in tutulan})
    radar_taranan = len(radar) + radar_olculemeyen
    radar.sort(key=lambda x: abs(x["getiri_%"]), reverse=True)
    # SATTIKLARIN ayri: kilitli BIST kagitlari radarin tepesini doldurunca
    # (olculdu 8 Eki) sattigin AVGO/MRVL listede gorunmuyordu.
    radar_satilan = [r for r in radar if r["satildi"]][:SATILAN_SATIR]
    radar = [r for r in radar if not r["satildi"]]

    # --- 3. tahminler -----------------------------------------------------
    hafta = []
    for r in db.query(
            """SELECT ajan, COUNT(*) n, SUM(isabet) dogru FROM predictions
               WHERE sahip = ? AND isabet IS NOT NULL
                 AND olcum_ts > ? AND olcum_ts <= ?
                 AND ajan IN ('hakem', 'taktik', 'strateji_secilen')
                 AND (teslim IS NULL OR teslim = 1)
                 AND (ajan <> 'taktik' OR taktik_tur IN ('alim', 'satis'))
               GROUP BY ajan""", (sahip, bas, bit + "T23:59:59")):
        hafta.append({"ajan": r["ajan"], "etiket": AJAN_ETIKET[r["ajan"]],
                      "olgunlasan": r["n"], "dogru": r["dogru"] or 0})
    from ..pulse.journal import ajan_karnesi
    karne = []
    for ajan in AJAN_ETIKET:
        try:
            k = ajan_karnesi(db, sahip, ajan)
        except Exception as e:                              # noqa: BLE001
            log.warning("[haftalik] %s karnesi okunamadi: %s", ajan, e)
            continue
        if not k.get("olcum"):
            continue
        karne.append({"ajan": ajan, "etiket": AJAN_ETIKET[ajan],
                      "olcum": k["olcum"], "isabet_%": k.get("isabet_%"),
                      "taban_%": k.get("taban_%"),
                      "ayrilir": k.get("tabandan_ayrilir_mi"),
                      "yeterli": k.get("yeterli_mi")})

    # --- 4. kararlar ------------------------------------------------------
    from ..bot.tools import emir_sonucu
    from ..pulse.emir_kanit import BEYANLAR
    emirler = []
    for e in db.emirler(sahip, limit=50):
        if _tarih(e["olusma_ts"]) <= bas:
            continue
        emirler.append({"sembol": e["symbol"], "yon": e["yon"], "adet": e["adet"],
                        "tur": e["tur"], "fiyat": e["fiyat"],
                        "sonuc": emir_sonucu(e["durum"], e["dolum_fiyat"]),
                        "beyan": BEYANLAR.get(e["beyan"] or "")})

    # --- 5. onumuzdeki hafta ---------------------------------------------
    try:
        from ..collectors.bilancotakvim import yaklasan_bilancolar
        bilanco = [{"sembol": b["sembol"], "tarih": b["tarih"],
                    "zaman": b.get("zaman"),
                    "fiyatlanan_%": b.get("fiyatlanan_hareket_%")}
                   for b in yaklasan_bilancolar(db, sahip, HAFTA_GUN)["bilancolar"]]
    except Exception as e:                                  # noqa: BLE001
        log.warning("[haftalik] bilancolar okunamadi: %s", e)
        bilanco = None

    return {
        "sahip": sahip, "bas": bas, "bit": bit,
        "hesap_durumu": hesap_durumu,
        "uretim": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "portfoy": portfoy, "portfoy_olculemeyen": olculemeyen,
        "radar": radar[:RADAR_SATIR], "radar_satilan": radar_satilan,
        "radar_taranan": radar_taranan,
        "radar_olculemeyen": radar_olculemeyen,
        "tahmin_hafta": hafta, "karne": karne,
        "emirler": emirler, "bilanco": bilanco,
        "haber_kapsam": dosya.get("kapsam") or {},
    }


# ---------------------------------------------------------------------------
# sunum
# ---------------------------------------------------------------------------

def _yuzde(v, isaret: bool = True) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):.1f}".replace(".", ",")
    if not isaret:
        return f"%{s}"
    return ("+" if v > 0 else "−" if v < 0 else "") + f"%{s}"


def _sayi(v) -> str:
    if v is None:
        return "—"
    return f"{v:g}".replace(".", ",")


def _tr_tarih(iso: str) -> str:
    aylar = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl",
             "Eki", "Kas", "Ara"]
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {aylar[d.month - 1]}"


def html_uret(veri: dict) -> str:
    """Veriden rapor HTML'i (her `.kart` ayri PNG olur)."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)),
                      autoescape=select_autoescape(["html"]))
    env.filters["yuzde"] = _yuzde
    env.filters["trt"] = _tr_tarih
    env.filters["sayi"] = _sayi
    p = veri["portfoy"]
    return env.get_template("haftalik.html").render(
        v=veri, haberli=p[:PORTFOY_HABERLI],
        kisa=p[PORTFOY_HABERLI:PORTFOY_HABERLI + PORTFOY_KISA],
        kalan=max(0, len(p) - PORTFOY_HABERLI - PORTFOY_KISA),
        genislik=KART_GENISLIK)


def goruntule(html_metni: str, hedef: Path, onek: str) -> list[Path]:
    """
    HTML -> kart basina PNG. Chromium (Playwright, kalici profil DEGIL:
    tarayici toplayicilarinin profil kilidiyle cakismaz). DB'ye dokunmaz;
    is parcaciginda calistirilabilir.
    """
    from playwright.sync_api import sync_playwright
    hedef.mkdir(parents=True, exist_ok=True)
    yollar: list[Path] = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            pg = b.new_page(viewport={"width": KART_GENISLIK, "height": 800},
                            device_scale_factor=2)
            pg.set_content(html_metni, wait_until="load")
            kartlar = pg.locator(".kart")
            for i in range(kartlar.count()):
                y = hedef / f"{onek}_{i + 1}.png"
                kartlar.nth(i).screenshot(path=str(y))
                yollar.append(y)
        finally:
            b.close()
    if not yollar:
        raise RuntimeError("rapor sablonu hic kart uretmedi")
    return yollar


def metin_ozeti(veri: dict) -> str:
    """Gorsel uretilemezse giden Telegram HTML metni (sessiz kalmaz)."""
    e = html.escape
    s = [f"<b>Bu hafta ne kaçırdım</b> — {_tr_tarih(veri['bas'])}–{_tr_tarih(veri['bit'])}"]
    b = [h for h in veri.get("hesap_durumu", []) if h["bayat"]]
    if b:
        s.append("⚠️ Portföy bilgisi eski: " + ", ".join(
            f"{e(h['hesap'])} {_tr_tarih(h['son'])}" for h in b)
            + " — o hesaplarda sattığın kağıt hâlâ görünebilir.")
    if veri["portfoy"]:
        s.append("\n<b>Portföyün haftası</b>")
        for p in veri["portfoy"][:PORTFOY_HABERLI + PORTFOY_KISA]:
            h = p.get("haber")
            s.append(f"• {e(p['sembol'])} {_yuzde(p['getiri_%'])}"
                     + (f" — <i>{e(h['baslik'])}</i>" if h else ""))
    if veri["radar"]:
        s.append("\n<b>Radarında olup kaçan</b>")
        s += [f"• {e(r['sembol'])} {_yuzde(r['getiri_%'])}" for r in veri["radar"]]
    if veri["karne"]:
        s.append("\n<b>Botun karnesi (180 gün)</b>")
        s += [f"• {e(k['etiket'])}: {_yuzde(k['isabet_%'], False)} "
              f"(taban {_yuzde(k['taban_%'], False)}, n={k['olcum']})"
              for k in veri["karne"]]
    return "\n".join(s)


def ozet(veri: dict) -> dict:
    """Modele giden KISA ozet (gorselin tekrari degil)."""
    p = veri["portfoy"]
    return {
        "hafta": f"{veri['bas']} .. {veri['bit']}",
        "olculen_pozisyon": len(p),
        "olculemeyen": veri["portfoy_olculemeyen"],
        "en_iyi": ({"sembol": max(p, key=lambda x: x["getiri_%"])["sembol"],
                    "getiri_%": max(x["getiri_%"] for x in p)} if p else None),
        "en_kotu": ({"sembol": min(p, key=lambda x: x["getiri_%"])["sembol"],
                     "getiri_%": min(x["getiri_%"] for x in p)} if p else None),
        "haberli_hareket": sum(1 for x in p if x["haber"]),
        "radar_ilk": [{"sembol": r["sembol"], "getiri_%": r["getiri_%"],
                       **({"kilitli": True} if r.get("kilitli") else {})}
                      for r in veri["radar"][:3]],
        "sattiklarin": [{"sembol": r["sembol"], "getiri_%": r["getiri_%"]}
                        for r in veri.get("radar_satilan", [])],
        "bayat_hesaplar": [{"hesap": h["hesap"], "son_portfoy": h["son"],
                            "gun": h["gun"]}
                           for h in veri.get("hesap_durumu", []) if h["bayat"]],
        "emir_sayisi": len(veri["emirler"]),
        "beyansiz_emir": sum(1 for x in veri["emirler"] if not x["beyan"]),
    }


def uret(db, sahip: str, seri: Seri, hedef: Path,
         bugun: date | None = None) -> tuple[dict, list[Path] | None, str | None]:
    """
    Veri + gorseller. Doner (veri, yollar, hata). Gorsel uretilemezse
    yollar None ve hata dolu — cagiran METIN ozetine duser, susmaz.
    """
    veri = topla(db, sahip, seri, bugun)
    onek = f"haftalik_{sahip}_{veri['bit']}"
    try:
        return veri, goruntule(html_uret(veri), hedef, onek), None
    except Exception as e:                                  # noqa: BLE001
        log.warning("[haftalik] gorsel uretilemedi: %s", e)
        return veri, None, f"{type(e).__name__}: {e}"
