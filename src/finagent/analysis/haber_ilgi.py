"""
HABER DOSYASI — modele giden malzemeyi derler, SIRALAMAZ.

ROL AYRIMI
----------
Ilk surumde bu modul haberleri SAYIP siraliyordu (kagit basina kademe
1-2 haber sayisi / kendi normali). Yanlisti ve olcum gosterdi:

  * Son 2 gunde sembole bagli kademe 1-2 haber sayisi 68. Bu, modelin
    TAMAMINI okuyabilecegi bir hacim — o olcekte saymak, okumaktan
    DAHA KOTU bir arac.
  * Sayac "36 MRNA haberi" diyordu; toplam 68 varken 36'si MRNA demek
    36 sinyal degil, AYNI HABERIN 36 kez yazilmasi demek.
  * Sayac "Faz 3 sonucu aciklandi" ile "MRNA'nin adi gecen liste
    yazisi"ni ayirt edemez. Fark ANLAMDA, sayida degil.

Tarayicinin LLM'siz olmasinin sebebi HACIMDI (1.600 enstruman x her
gun). Haberde o hacim yok. Bu yuzden burada deterministik katmanin isi
siralamak degil, modele EKSIKSIZ ve DOGRU dosya vermek:

    deterministik : hangi haberler var, kim ne kadar tutuyor, fiyat ne yapti
    LLM           : hangisi onemli, NEDEN, pozisyona ne ediyor, ne curutur

BAGLANMAMIS HABER DE VERILIYOR — VE SEBEBI
------------------------------------------
Kanit seviyesindeki haberin %93'u hicbir sembole bagli degil (son 1
gunde 249 haberin 18'i bagli). Sirket adindan sembol cikarma denendi ve
TERK EDILDI (bkz. `collectors/base.py` sonundaki not): Turkce sirket
adlari siradan kelimelerden kuruluyor ve `yukselen`->YKSLN,
`aktuel`->RTALB gibi yanlis baglar uretiyordu. Yanlis sirkete baglamak
hic baglamamaktan kotudur. Eslestirmeyi model yapiyor; dosya
baglanmamis haberleri AYRI BIR BOLUMDE, oldugu gibi tasiyor.

BU KATMAN DOGRULANMADI
----------------------
Fiyat sinyalleri 2026-08-20'de backtest edildi: 24 hucrenin 22'si
sifirdan ayirt edilemedi. Haber tarafi HIC olculemedi — gecmise donuk
haber arsivi yok. Cikti bu ayrimi TASIMAK zorunda: dosya bir FIRSAT
IDDIASI degil, bir DIKKAT SIRASIDIR.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Modele giden basliklarin ust siniri. 120: son 2 gunun kademe 1-2
# hacmi ~350 ve bunun ~70'i sembole bagli; sinir hem bagli hem bagsiz
# tarafi rahatca aliyor, kirpma nadiren devreye giriyor.
AZAMI_BASLIK = 120


def haber_dosyasi(db, sahip: str | None = None, pencere_gun: int = 2,
                  azami: int = AZAMI_BASLIK) -> dict:
    """
    Modele verilecek ham dosya. Siralama YOK, yorum YOK.

    Doner:
      bagli_haberler   — sembole bagli kademe 1-2 haberler + fiyat/pozisyon
      bagsiz_haberler  — sembole baglanamamis kademe 1-2 haberler
      portfoy          — sahip verilmisse: sembol -> agirlik
      kapsam           — kac haber var, kaci kirpildi (SESSIZ KIRPMA YOK)
    """
    # JEV BAGI: `symbols` BOS ama `haber_bag` bir sirket secmis haber
    # BAGLI sayilir ve `baglayan: jev` ile isaretlenir (olasiliksal).
    # Tanim tek yerde — ayni kosul hem listede hem sayimda kullaniliyor.
    ticker = "COALESCE(n.symbols, '') <> ''"
    jev = f"(NOT {ticker} AND b.sembol IS NOT NULL)"
    kaynak = "news n LEFT JOIN haber_bag b ON b.news_id = n.id"
    pencere = "n.tier IN (1,2) AND n.published_at > datetime('now', ?)"
    bagli = db.query(
        f"""SELECT n.id, n.published_at, n.title, n.url, n.publisher, n.tier,
                   CASE WHEN {ticker} THEN n.symbols ELSE b.sembol END AS symbols,
                   n.konu, CASE WHEN {jev} THEN 1 ELSE 0 END AS jev_bagi
            FROM {kaynak}
            WHERE {pencere} AND ({ticker} OR {jev})
            ORDER BY n.tier ASC, n.published_at DESC LIMIT ?""",
        (f"-{pencere_gun} days", azami))
    bagsiz = db.query(
        f"""SELECT n.published_at, n.title, n.url, n.publisher, n.tier, n.konu
            FROM {kaynak}
            WHERE {pencere} AND NOT ({ticker} OR {jev})
            ORDER BY n.tier ASC, n.published_at DESC LIMIT ?""",
        (f"-{pencere_gun} days", azami))

    # SAYIM KIRPMADAN ONCE — kullanici neyi gormedigini bilmeli.
    toplam_bagli = db.query(
        f"""SELECT COUNT(*) n FROM {kaynak}
            WHERE {pencere} AND ({ticker} OR {jev})""",
        (f"-{pencere_gun} days",))[0]["n"]
    toplam_bagsiz = db.query(
        f"""SELECT COUNT(*) n FROM {kaynak}
            WHERE {pencere} AND NOT ({ticker} OR {jev})""",
        (f"-{pencere_gun} days",))[0]["n"]

    semboller = sorted({s for r in bagli
                        for s in (r["symbols"] or "").split(",") if s.strip()})
    baglam = {s: _sembol_baglami(db, s) for s in semboller}
    portfoy = _portfoy(db, sahip) if sahip else {}

    from ..research.haber_jev import OLAY_NOTU, olay_etiketleri
    etiket = olay_etiketleri(db, [r["id"] for r in bagli])
    bagli_liste = []
    for r in bagli:
        d = dict(r)
        nid, jev_bagi = d.pop("id"), d.pop("jev_bagi")
        if jev_bagi:
            d["baglayan"] = "jev"
        olay = {s: etiket[(nid, s)] for s in (d["symbols"] or "").split(",")
                if (nid, s) in etiket}
        if olay:
            d["olay_turu"] = olay
        bagli_liste.append(d)

    return {
        "pencere_gun": pencere_gun,
        "bagli_haberler": bagli_liste,
        "bagsiz_haberler": [dict(r) for r in bagsiz],
        "olay_turu_notu": OLAY_NOTU,
        "sembol_baglami": {k: v for k, v in baglam.items() if v},
        "portfoy_agirliklari": portfoy,
        "kapsam": {
            "bagli_toplam": toplam_bagli, "bagli_verilen": len(bagli),
            "bagsiz_toplam": toplam_bagsiz, "bagsiz_verilen": len(bagsiz),
            "kirpildi": (toplam_bagli > len(bagli)
                         or toplam_bagsiz > len(bagsiz)),
        },
        "ZORUNLU": (
            "Bu dosya SIRALANMAMIS ham malzemedir. Sen sirala. Kurallar: "
            "(1) AYNI OLAYIN farkli yayinlarini TEK aday say — 36 baslik "
            "36 sinyal degildir. "
            "(2) Her aday icin MEKANIZMA yaz: haber sirketin nakit akisini/"
            "rekabetini/duzenlemesini NASIL etkiliyor. Yazamiyorsan aday "
            "degildir. "
            "(3) `bagsiz_haberler`i de oku — kanit seviyesindeki haberin "
            "cogu orada ve hangi sirkete ait oldugunu SEN cozeceksin; "
            "emin degilsen sembol UYDURMA, 'hangi sirket oldugu belirsiz' de. "
            "(4) Fiyat zaten hareket ettiyse SOYLE — `gun_getirisi_%` "
            "haberin fiyata yansiyip yansimadigini gosterir. "
            "(5) DOGRULANMAMIS KATMAN: bu siralamanin gecmis basarisi "
            "OLCULMEDI (gecmise donuk haber arsivi yok). 'Firsat' diye "
            "degil 'once suna bak' diye sun, ve tezi NE CURUTUR yaz. "
            "(6) `olay_turu` ve `baglayan: jev` icin `olay_turu_notu`nu oku: "
            "ikisi de ON ELEMEDIR — baslik baska bir sey soyluyorsa "
            "BASLIGA inan; `olay_turu` yoksa haberi 'onemsiz' sayma."),
    }


def _sembol_baglami(db, sembol: str) -> dict:
    r = db.query(
        """SELECT id, symbol, name, venue FROM instruments
           WHERE symbol = ? LIMIT 1""", (sembol,))
    if not r:
        return {}
    e = r[0]
    seri = db.fiyat_serisi(e["id"], limit=21)
    out = {"ad": e["name"], "venue": e["venue"], "bar": len(seri)}
    if len(seri) >= 2 and seri[-2]["close"]:
        out["gun_getirisi_%"] = round(
            (seri[-1]["close"] / seri[-2]["close"] - 1) * 100, 2)
    if len(seri) >= 21 and seri[0]["close"]:
        out["yirmi_gun_%"] = round(
            (seri[-1]["close"] / seri[0]["close"] - 1) * 100, 2)
    if seri:
        out["son_fiyat"] = seri[-1]["close"]
        out["para_birimi"] = seri[-1]["currency"]
    return out


def _portfoy(db, sahip: str) -> dict:
    """Sembol -> portfoy agirligi (%). Model 'bu beni ne kadar ilgilendirir'i bilsin."""
    out: dict[str, float] = {}
    for hesap in db.hesaplar(sahip):
        poz = db.latest_positions(hesap, sahip)
        toplam = sum((p["market_value"] or 0) for p in poz)
        if not toplam:
            continue
        for p in poz:
            if p["symbol"] == "CASH":
                continue
            out[p["symbol"]] = round(
                out.get(p["symbol"], 0) + (p["market_value"] or 0) / toplam * 100, 2)
    return out
