"""
GECE NABZININ ACILISI — ①②③ (10 Eki 2026, Ali + dort uzman incelemesi).

NEDEN AYRI BIR ACILIS
---------------------
Dort uzman incelemesi (9 Eki) nabzi 4/10 buldu: en onemli bilgi (ASML
paranin %40'i, 14 Ekim bilancosu) 63 satirin 40'incisindaydi; mesaj
hesap yuzdeleri, makro ve 12 genel haberle basliyordu. Ali: "hissizlik
yasatmasin, okunabilir faydali bir sey istiyorum". Acilis uc soruyu
cevaplar, bu sirayla:

  ① Bugun parama ne oldu?        — EURO ile, kagit kagit, ayni gunun haberiyle
  ② Onumuzdeki gunlerde ne var?   — bilancolar, opsiyon piyasasinin
                                    bekledigi hareketin EURO karsiligi
  ③ Bir sey yapmam gerekiyor mu?  — plan adimi ya da "hayir"

SICAKLIK METINDE, SAYI OLCUMDE. Cumleler sablon (model yazmiyor): her sayi
olculen veriden gelir, model bir rakami yuvarlayamaz ya da tasiyamaz (9 Eki
"ucte iki" / "4/4" vakalari). Ton degisken sablonla verilir.

HABER SEBEP DEGIL. "Ayni gunun haberi" yazilir; hareketin sebebi oldugu
OLCULMEDI (`haber-baglama-fizibilitesi`: buyuk hareketlerin %95'inde kaynak
yok). Kullaniciya bu cumleyle soylenir, sessizce ima edilmez.

HICBIR PARCA NABZI DUSURMEZ: her bolum kendi hatasini yutar ve satir
uretmez; loglanir.
"""
from __future__ import annotations

import html as _html
import logging
from datetime import date

log = logging.getLogger(__name__)


class html:                                   # noqa: N801 — yerel ad alani
    """Telegram HTML: yalnizca < > & kacirilir; kesme isareti duz kalir
    (`html.escape` varsayilaniyla "ASML&#x27;in" yaziliyordu)."""
    @staticmethod
    def escape(s) -> str:
        return _html.escape(str(s), quote=False)

AY_TAM = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
GUN_ADI = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma",
           "Cumartesi", "Pazar")

# Ton esikleri (portfoyun gunluk yuzde degisimi, mutlak).
SAKIN_PCT = 0.5
HAREKETLI_PCT = 1.5
# Bir kagit brut hareketin bu payindan fazlasini yaptiysa "cogu ondan".
BASKIN_PAY = 0.5
# Euro olarak gosterilmeye deger en kucuk kalem.
ASGARI_EUR = 1.0
AZAMI_KALEM = 3
# Plan adimi bu kadar gun icindeyse metni ③'te ALINTILANIR.
PLAN_YAKIN_GUN = 3
ONUMUZDEKI_GUN = 7
NAKIT_TURLERI = ("cash",)
HESAP_ADI = {"bux": "BUX", "ibkr": "IBKR", "midas": "Midas", "binance": "Binance"}


def tarih_tr(d: date, gun_adi: bool = True) -> str:
    s = f"{d.day} {AY_TAM[d.month - 1]}"
    return f"{s} {GUN_ADI[d.weekday()]}" if gun_adi else s


def _sayi(v: float, basamak: int = 0) -> str:
    s = f"{abs(v):,.{basamak}f}".replace(",", "\x00").replace(".", ",") \
                                .replace("\x00", ".")
    return s


def _eur_isaretli(v: float) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + _sayi(v) + " €"


def _yuzde_isaretli(v: float, basamak: int = 1) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + "%" + _sayi(v, basamak)


# ---------------------------------------------------------------------------
# ① bugun paran
# ---------------------------------------------------------------------------

def gunun_parasi(db, sahip: str, bugun: date | None = None) -> dict:
    """
    Bugunku pozisyonlarin BUGUNKU fiyat hareketi, EURO olarak.

    Kalem = sembol (hesaplar birlesir). Yalnizca son bari BUGUNE ait
    kagitlar sayilir; bugun islem gormeyen (tatil, bayat seri) kagit
    `bugun_yok`a gider — dunku hareketi bugunun gibi gostermek, 9 Eki gram
    altin vakasinin aynisi olurdu. KUR ETKISI HARIC: iki kapanisa ayni
    guncel kur (`gunluk_degisim` ile ayni olcu).

    Doner: {"toplam_eur", "toplam_%", "kalemler": [{sembol, eur, yuzde}],
    "bugun_yok": [...], "cevrilemeyen": [...], "bayat_hesaplar": [...],
    "portfoy_eur"}.
    """
    from ..analysis.portfolio import _canli_fiyat
    from ..analysis.tema import _eur
    bugun = bugun or date.today()
    bugun_s = bugun.isoformat()
    kalem: dict[str, dict] = {}
    bugun_yok, cevrilemeyen, bayat = set(), set(), []
    portfoy_eur = 0.0
    for hesap in db.hesaplar(sahip):
        satirlar = db.latest_positions(hesap, sahip)
        if not satirlar:
            continue
        son = str(satirlar[0]["snapshot_ts"])[:10]
        try:
            yas = (bugun - date.fromisoformat(son)).days
        except ValueError:
            yas = None
        if yas is not None and yas > 7:
            bayat.append({"hesap": hesap, "gun": yas})
        for r in satirlar:
            mv = r["market_value"] or 0
            if mv > 0:
                e = _eur(db, float(mv), r["currency"])
                if e is not None:
                    portfoy_eur += e
            if (r["asset_type"] or "") in NAKIT_TURLERI or r["symbol"] == "CASH":
                continue
            adet = r["quantity"]
            if not adet or adet <= 0:
                continue
            c = _canli_fiyat(db, r["instrument_id"])
            if not c or not c["onceki_kapanis"]:
                bugun_yok.add(r["symbol"])
                continue
            if str(c["tarih"])[:10] != bugun_s:
                bugun_yok.add(r["symbol"])
                continue
            fark = float(adet) * (c["kapanis"] - c["onceki_kapanis"])
            e = _eur(db, fark, c["para_birimi"])
            if e is None:
                cevrilemeyen.add(r["symbol"])
                continue
            k = kalem.setdefault(r["symbol"], {"sembol": r["symbol"], "eur": 0.0,
                                               "yuzde": c["gun_degisim_%"]})
            k["eur"] += e
    kalemler = sorted(kalem.values(), key=lambda k: -abs(k["eur"]))
    toplam = sum(k["eur"] for k in kalemler)
    dun = portfoy_eur - toplam
    return {"toplam_eur": round(toplam, 2),
            "toplam_%": round(toplam / dun * 100, 2) if dun > 0 else None,
            "kalemler": [{**k, "eur": round(k["eur"], 2)} for k in kalemler],
            "bugun_yok": sorted(bugun_yok), "cevrilemeyen": sorted(cevrilemeyen),
            "bayat_hesaplar": bayat, "portfoy_eur": round(portfoy_eur, 2)}


def gunun_haberleri(db, sahip: str, semboller: list[str]) -> dict[str, dict]:
    """
    Sembol -> ayni gunun en iyi haberi (haftalik raporla AYNI secim
    kurali: `haftalik._haftanin_haberi` — ilgisiz elenir, kademe once).
    Tek kopya: kural iki yerde yazilirsa ayrisir (`ayni-kural-iki-kopya`).
    """
    from ..analysis.haber_ilgi import haber_dosyasi
    from ..report.haftalik import _haber_dizini, _haftanin_haberi
    dizin = _haber_dizini(haber_dosyasi(db, sahip, pencere_gun=1))
    out = {}
    for s in semboller:
        h, _ = _haftanin_haberi(s, dizin)
        if h:
            out[s] = h
    return out


def bugun_bolumu(ad: str, p: dict, haberler: dict[str, dict]) -> list[str]:
    """SAF. ① metni."""
    e = html.escape
    kalemler = [k for k in p["kalemler"] if abs(k["eur"]) >= ASGARI_EUR]
    L = []
    if not p["kalemler"]:
        L.append(f"İyi akşamlar {e(ad)}. Bugün portföyündeki kağıtların hiçbiri "
                 "işlem görmedi ya da bugünün fiyatı henüz yok.")
        return L
    t, yz = p["toplam_eur"], p.get("toplam_%")
    ton = ("sakin bir gündü" if yz is not None and abs(yz) < SAKIN_PCT
           else "hareketli bir gündü" if yz is not None and abs(yz) >= HAREKETLI_PCT
           else "orta karar bir gündü")
    if abs(t) < ASGARI_EUR:
        cumle = "paran neredeyse hiç değişmedi"
    else:
        cumle = (f"paran <b>{_sayi(t)} € {'arttı' if t > 0 else 'geriledi'}</b>"
                 + (f" ({_yuzde_isaretli(yz)})" if yz is not None else ""))
    L.append(f"İyi akşamlar {e(ad)}. Bugün {ton}: {cumle}.")
    brut = sum(abs(k["eur"]) for k in p["kalemler"]) or 1.0
    if kalemler and abs(kalemler[0]["eur"]) / brut >= BASKIN_PAY:
        k = kalemler[0]
        # EK YOK: "ASML'den / NVDA'dan" kisaltmada unlu uyumu belirsiz.
        L.append(f"Değişimin çoğu tek kağıttan geldi: {e(k['sembol'])} "
                 f"({_eur_isaretli(k['eur'])}).")
    elif len(kalemler) > 1:
        L.append("Değişim birkaç kağıda dağıldı.")
    gosterilen = kalemler[:AZAMI_KALEM]
    for k in gosterilen:
        satir = f"• <b>{e(k['sembol'])}</b> {_eur_isaretli(k['eur'])}"
        if k.get("yuzde") is not None:
            satir += f" ({_yuzde_isaretli(k['yuzde'])})"
        h = haberler.get(k["sembol"])
        if h:
            satir += (f" — <i>{e(h['baslik'])}</i>"
                      + (f" ({e(h['yayinci'])})" if h.get("yayinci") else ""))
        L.append(satir)
    kalan = [k for k in p["kalemler"] if k not in gosterilen]
    if kalan:
        L.append(f"• Diğer {len(kalan)} kağıt: "
                 f"{_eur_isaretli(sum(k['eur'] for k in kalan))}")
    dip = ["fiyat hareketi, kur etkisi hariç"]
    if any(k["sembol"] in haberler for k in gosterilen):
        dip.append("haber aynı günün haberi, hareketin sebebi olduğu ölçülmedi")
    if p["bayat_hesaplar"]:
        dip.append("eski adet: " + ", ".join(
            f"{HESAP_ADI.get(b['hesap'], e(b['hesap']))} {b['gun']} gün"
            for b in p["bayat_hesaplar"]))
    dipnot = "; ".join(dip)
    L.append(f"<i>{dipnot[:1].upper()}{dipnot[1:]}.</i>")
    return L


# ---------------------------------------------------------------------------
# ② onumuzdeki gunler
# ---------------------------------------------------------------------------

def _pozisyon_eur(db, sahip: str) -> dict[str, float]:
    from ..analysis.ips import _pozisyonlar
    poz, _, _ = _pozisyonlar(db, sahip)
    out: dict[str, float] = {}
    for x in poz:
        out[x["sembol"]] = out.get(x["sembol"], 0.0) + x["eur"]
    return out


def onumuzdeki_gunler(db, settings, sahip: str, bugun: date | None = None,
                      gun: int = ONUMUZDEKI_GUN) -> dict:
    """
    Pencereye dusen bilancolar + opsiyon piyasasinin fiyatladigi hareketin
    EURO karsiligi (pozisyon EUR x %) + o kagidi kapsayan PLAN adimi.
    Plan adiminin tarihi HATIRLATMA gunudur, son gun degil (ips) — bu yuzden
    adim TARIHSIZ anilir.
    """
    from ..collectors.bilancotakvim import yaklasan_bilancolar
    bugun = bugun or date.today()
    y = yaklasan_bilancolar(db, sahip, gun)
    deger = _pozisyon_eur(db, sahip)
    plan_sem = set()
    try:
        from ..analysis.ips import politika
        p = politika(settings, sahip) or {}
        for a in p.get("plan") or []:
            if str(a["tarih"]) >= bugun.isoformat():
                plan_sem |= {str(s).upper() for s in a.get("semboller") or []}
    except Exception as ex:                               # noqa: BLE001
        log.warning("[aksam] plan okunamadi: %s", ex)
    out = []
    for b in y.get("bilancolar") or []:
        pct = b.get("fiyatlanan_hareket_%")
        eur = deger.get(b["sembol"])
        out.append({"sembol": b["sembol"], "tarih": b["tarih"],
                    "zaman": b.get("zaman"),
                    "tarihler": b.get("kaynaklar_ayrisiyor"),
                    "hareket_%": pct,
                    "eur_etkisi": (round(eur * pct / 100) if pct and eur else None),
                    "planda": b["sembol"].upper() in plan_sem})
    return {"bilancolar": out, "tarih_bilinmiyor": y.get("tarih_bilinmiyor") or []}


ZAMAN = {"once": "seans öncesi", "sonra": "seans sonrası"}


def onumuzdeki_bolumu(o: dict) -> list[str]:
    """SAF. ② metni."""
    e = html.escape
    L = [f"\n📅 <b>Önümüzdeki {ONUMUZDEKI_GUN} gün</b>"]
    if not o["bilancolar"]:
        L.append("Portföyündeki kağıtlarda bilanço açıklaması yok.")
        return L
    for b in o["bilancolar"]:
        try:
            d = tarih_tr(date.fromisoformat(b["tarih"]))
        except ValueError:
            d = e(b["tarih"])
        s = f"• <b>{d}</b> — {e(b['sembol'])} bilançosu"
        if b.get("zaman") in ZAMAN:
            s += f" ({ZAMAN[b['zaman']]})"
        if b.get("hareket_%"):
            s += (f": piyasa ±%{_sayi(b['hareket_%'], 1)} hareket bekliyor"
                  + (f", bu sende <b>≈ ±{_sayi(b['eur_etkisi'])} €</b>"
                     if b.get("eur_etkisi") else ""))
        if b.get("tarihler"):
            s += (" <i>(kaynaklar farklı tarih veriyor: "
                  + ", ".join(e(t) for t in b["tarihler"]) + ")</i>")
        L.append(s)
        if b.get("planda"):
            L.append("  <i>Planında bu kağıt için bir adım var.</i>")
    L.append("<i>Beklenen hareket opsiyon fiyatlarından; yön içermez.</i>")
    return L


# ---------------------------------------------------------------------------
# ③ yapacaklarin
# ---------------------------------------------------------------------------

def yapacaklar(settings, sahip: str, bugun: date | None = None) -> dict:
    """Sahibin sonraki plan adimi (varsa). Politika yoksa bos."""
    from ..analysis.ips import politika, siradaki_adimlar
    bugun = bugun or date.today()
    p = politika(settings, sahip)
    if not p:
        return {"adim": None}
    adimlar = siradaki_adimlar(p, bugun, azami=1)
    return {"adim": adimlar[0] if adimlar else None}


def yapacaklar_bolumu(y: dict, uyari: int, bugun: date | None = None) -> list[str]:
    """SAF. ③ metni. `uyari`: bu mesajdaki tez/risk uyarisi sayisi."""
    e = html.escape
    bugun = bugun or date.today()
    L = []
    a = y.get("adim")
    yakin = False
    if a:
        try:
            d = date.fromisoformat(a["hatirlatma_tarihi"])
            yakin = (d - bugun).days <= PLAN_YAKIN_GUN
        except ValueError:
            d = None
    if a and yakin:
        L.append(f"\n📌 <b>Planındaki sıradaki adım</b> "
                 f"(hatırlatma: {tarih_tr(d)})")
        L.append(e(a["metin"]))
    if uyari:
        L.append(f"\n⚠️ Aşağıda <b>{uyari} uyarı</b> var; göz atmanda fayda var.")
    elif not (a and yakin):
        L.append("\n✅ <b>Bugün yapman gereken bir şey yok.</b>")
    if a and not yakin and d:
        # METIN ALINTILANMAZ: plan metni hatirlatma gunune gore yazilmis
        # ("Yarin (13 Ekim) kapanisa kadar...") ve gunler once okununca
        # "yarin" yanlis gune isaret eder. Uzaktaki adimin TARIHI ve
        # KAGITLARI yazilir; metin o gun ayri mesajla gelir.
        kagit = ", ".join(e(x) for x in a.get("semboller") or [])
        L.append(f"<i>Planındaki sıradaki adım: {tarih_tr(d)}"
                 + (f" ({kagit})" if kagit else "") + ".</i>")
    return L


# ---------------------------------------------------------------------------
# birlestirme
# ---------------------------------------------------------------------------

def acilis(db, settings, sahip: str, uyari: int = 0,
           bugun: date | None = None) -> list[str]:
    """①②③ — her bolum kendi hatasini yutar; hicbiri nabzi dusurmez."""
    bugun = bugun or date.today()
    ad = settings.gorunen_ad(sahip)
    L: list[str] = []
    try:
        p = gunun_parasi(db, sahip, bugun)
        try:
            semboller = [k["sembol"] for k in p["kalemler"][:AZAMI_KALEM]]
            haberler = gunun_haberleri(db, sahip, semboller) if semboller else {}
        except Exception as ex:                           # noqa: BLE001
            log.warning("[aksam] haberler okunamadi: %s", ex)
            haberler = {}
        L.extend(bugun_bolumu(ad, p, haberler))
    except Exception as ex:                               # noqa: BLE001
        log.warning("[aksam] ① bugun paran kurulamadi (%s): %s", sahip, ex)
        L.append(f"İyi akşamlar {html.escape(ad)}.")
    try:
        L.extend(onumuzdeki_bolumu(onumuzdeki_gunler(db, settings, sahip, bugun)))
    except Exception as ex:                               # noqa: BLE001
        log.warning("[aksam] ② onumuzdeki gunler kurulamadi (%s): %s", sahip, ex)
    try:
        L.extend(yapacaklar_bolumu(yapacaklar(settings, sahip, bugun), uyari, bugun))
    except Exception as ex:                               # noqa: BLE001
        log.warning("[aksam] ③ yapacaklar kurulamadi (%s): %s", sahip, ex)
    return L
