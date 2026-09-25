"""
GERCEK GETIRI KARNESI (IBKR MCP Faz 3) — hesabin para giris-cikisindan
arindirilmis getirisi (TWR), S&P 500 ile AYNI PARA BIRIMINDE.

NEDEN
-----
Defter "tahmin dogru muydu" sorusunu olcuyor; hesabin gercekte ne
kazandirdigini olcmuyor. Ali'nin plani "IBKR'de kanitlanirsa BUX'taki
parayi tasirim" — o kararin olcutu bu sayi. IBKR Portfolio Analyst bunu
bulut baglayicisindan veriyor (`get_pa_performance_all_periods`); CPGW'de
karsiligi yok.

HESAP
-----
PA her donem icin KUMULATIF getiri (`cps`, donem basindan, kesir) veriyor.
Gunluk getiri ardisik iki noktadan turetilir ve saklanir:

    r_0 = cps_0            r_t = (1 + cps_t) / (1 + cps_{t-1}) - 1

Her donem (1A, yilbasindan beri, baslangictan beri) bu zincirden hesaplanir:
tek kaynak, tek hesap. Fonlama oncesi gunlerde cps 0 -> r 0; zincire etkisi
YOK, ayrica dislanmiyor (dislamak, "fonlama gunu" icin bir esik uydurmak
olurdu).

GERIYE DONUK DUZELTME
---------------------
PA gecmis gunleri yeniden hesaplayabilir (olculdu 25 Eyl: ayni gunun `1D`
degeri iki cekiliste farkliydi — gun ici kesinlesmemis nokta). Her
cekiliste gecmis gunler karsilastirilir; 1e-6'dan buyuk fark REVIZYON
olarak sayilir, loga yazilir ve son deger saklanir. Ayni sinif kusur
8 Eyl'de fiyat serisinde defteri bozmustu (`seri-tabani-ve-ayni-bar`).

KIYAS
-----
VUSA (Vanguard S&P 500, EUR, BUX'ta islem goren). Hesap EUR, kiyas EUR:
kur cevirisi yok. USD cinsinden SPY ile kiyaslamak deponun para birimi
tuzagini tekrarlamak olurdu. VUSA dagitimli: seri yalnizca FIYAT getirisi
(temettu yilda ~%1-1,5 eksik) — mesajda yazilir. Hizalama iki ucta da
"o gune kadarki son kapanis" (as-of): tatil farki bir tarafi kaydirmaz.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

REVIZYON_TOLERANSI = 1e-6
KIYAS_SEMBOL = "VUSA"
KIYAS_VENUE = "BUX"
KIYAS_PB = "EUR"
# "Bu bir olcum, kanit degil" satirinin kalktigi esikler. Kucuk hesap ve
# kisa sure: tek bir hisse hareketi getiriyi tasir. Oran taban olmadan ya da
# siniri olmadan hicbir yere gitmez (`gun-sonu-sicili`).
KANIT_ASGARI_NAV = 1000.0
KANIT_ASGARI_GUN = 90

# Tercih sirasi: en uzun gecmis. Hesap bir yildan gencken 1Y == YTD.
DONEM_TERCIHI = ("1Y", "YTD", "1M", "MTD", "7D", "1D")


def _tarih(s: str) -> str:
    s = str(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]


def pa_gunluk(veri) -> dict:
    """
    SAF. PA yaniti -> {"olcu", "para_birimi", "satirlar": [(tarih, nav, r)]}.

    En uzun donem kullanilir (`DONEM_TERCIHI`). Paralel diziler esit
    uzunlukta degilse ya da hic donem yoksa ValueError: bozuk bir yaniti
    kismen okuyup "getiri" uretmek, uydurma bir sayi olurdu.
    """
    if not isinstance(veri, dict):
        raise ValueError("PA yaniti sozluk degil")
    hesaplar = (veri.get("accounts") or {})
    if len(hesaplar) != 1:
        raise ValueError(f"PA yaniti {len(hesaplar)} hesap iceriyor; tek hesap bekleniyor")
    hesap = next(iter(hesaplar.values()))
    donemler = hesap.get("periods") or {}
    secilen = next((d for d in DONEM_TERCIHI if d in donemler), None)
    if not secilen:
        raise ValueError("PA yanitinda bilinen bir donem yok")
    d = donemler[secilen]
    cps, nav, gun = d.get("cps") or [], d.get("nav") or [], d.get("dates") or []
    if not (len(cps) == len(nav) == len(gun)) or not cps:
        raise ValueError(f"PA {secilen}: paralel diziler bozuk "
                         f"(cps {len(cps)}, nav {len(nav)}, dates {len(gun)})")
    satirlar, onceki = [], 0.0
    for c, n, g in zip(cps, nav, gun):
        r = (1.0 + float(c)) / (1.0 + onceki) - 1.0
        satirlar.append((_tarih(g), float(n), r))
        onceki = float(c)
    return {"olcu": veri.get("portfolio_measure") or "?",
            "para_birimi": hesap.get("base_currency"),
            "donem": secilen, "satirlar": satirlar}


def yaz(db, hesap: str, pa: dict) -> dict:
    """
    UPSERT + revizyon tespiti. Doner: {"yeni", "revize": [(tarih, eski, yeni)]}.
    """
    eski = {r["tarih"]: r["gunluk"] for r in db.query(
        "SELECT tarih, gunluk FROM hesap_getirisi WHERE hesap = ?", (hesap,))}
    yeni, revize = 0, []
    with db.tx() as c:
        for tarih, nav, r in pa["satirlar"]:
            if tarih in eski:
                if abs(eski[tarih] - r) > REVIZYON_TOLERANSI:
                    revize.append((tarih, eski[tarih], r))
            else:
                yeni += 1
            c.execute(
                """INSERT INTO hesap_getirisi
                     (hesap, tarih, nav, gunluk, olcu, para_birimi)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(hesap, tarih) DO UPDATE SET
                     nav = excluded.nav, gunluk = excluded.gunluk,
                     olcu = excluded.olcu, para_birimi = excluded.para_birimi,
                     cekilis_ts = datetime('now')""",
                (hesap, tarih, nav, r, pa["olcu"], pa["para_birimi"]))
    if revize:
        log.warning("[getiri] %s: %d gecmis gun REVIZE edildi: %s", hesap,
                    len(revize), ", ".join(f"{t} {a:+.6f}->{b:+.6f}"
                                           for t, a, b in revize[:5]))
    return {"yeni": yeni, "revize": revize}


def _zincir(getiriler) -> float:
    toplam = 1.0
    for r in getiriler:
        toplam *= 1.0 + r
    return toplam - 1.0


def _kiyas_kapanislari(db) -> list[tuple[str, float]]:
    """VUSA'nin EUR kapanislari, artan tarih. `db.fiyat_serisi` — tek yol."""
    r = db.query("SELECT id FROM instruments WHERE symbol = ? AND venue = ?",
                 (KIYAS_SEMBOL, KIYAS_VENUE))
    if not r:
        return []
    return [(str(b["ts"])[:10], float(b["close"]))
            for b in db.fiyat_serisi(r[0]["id"], 100000, tercih_ccy=KIYAS_PB)
            if b["close"]]


def _asof(kapanis: list[tuple[str, float]], tarih: str) -> float | None:
    """`tarih`e kadarki (dahil) SON kapanis. Yoksa None."""
    son = None
    for t, c in kapanis:
        if t > tarih:
            break
        son = c
    return son


def ozet(db, hesap: str, bugun: str | None = None) -> dict:
    """
    Donemler: 1A (son 30 takvim gunu), yilbasindan beri, baslangictan beri.
    Her biri: hesap %, VUSA % (ayni pencere, as-of), fark (puan).
    VUSA penceresi hesaplanamazsa None — "0" DEGIL.
    """
    satirlar = [dict(r) for r in db.query(
        """SELECT tarih, nav, gunluk, olcu, para_birimi FROM hesap_getirisi
           WHERE hesap = ? ORDER BY tarih""", (hesap,))]
    if not satirlar:
        return {"hesap": hesap, "donemler": {}, "gun": 0, "not": "getiri verisi yok"}
    son = satirlar[-1]
    bitis = son["tarih"]
    bugun = bugun or date.today().isoformat()
    kiyas = _kiyas_kapanislari(db)
    ilk = (date.fromisoformat(satirlar[0]["tarih"]) - timedelta(days=1)).isoformat()
    pencereler = {
        "1A": (date.fromisoformat(bitis) - timedelta(days=30)).isoformat(),
        "yilbasi": f"{int(bitis[:4]) - 1}-12-31",
        "baslangic": ilk,
    }
    donemler = {}
    for ad, bas in pencereler.items():
        # KIYAS PENCERESI HESABIN VAR OLDUGU DONEMLE SINIRLI. SAHADA
        # BULUNDU (25 Eyl): hesap 24 Agu'da acildi, "yilbasindan" satiri
        # hesap icin 24 Agu'dan, VUSA icin 31 Ara'dan olculuyordu — iki
        # aylik bir hesap BUTUN YILIN endeks getirisiyle (+%15,08)
        # kiyaslaniyordu. Pencere hesabin baslangicindan once basliyorsa
        # baslangica kirpilir; kirpilinca "baslangictan" ile AYNI olan
        # donem ayrica GOSTERILMEZ (ayni sayi iki etiketle yaniltir).
        if bas < ilk:
            if ad != "baslangic":
                continue
            bas = ilk
        icerik = [s["gunluk"] for s in satirlar if s["tarih"] > bas]
        if not icerik:
            continue
        h = _zincir(icerik) * 100
        k0, k1 = _asof(kiyas, bas), _asof(kiyas, bitis)
        k = (k1 / k0 - 1) * 100 if (k0 and k1) else None
        donemler[ad] = {"hesap_%": round(h, 2),
                        "kiyas_%": round(k, 2) if k is not None else None,
                        "fark_puan": round(h - k, 2) if k is not None else None,
                        "gun": len(icerik)}
    gun = len(satirlar)
    return {"hesap": hesap, "bitis": bitis, "nav": son["nav"],
            "para_birimi": son["para_birimi"], "olcu": son["olcu"],
            "gun": gun, "donemler": donemler,
            "kanit_degil": son["nav"] < KANIT_ASGARI_NAV or gun < KANIT_ASGARI_GUN}


def _yuzde(x) -> str:
    return "—" if x is None else f"{x:+.1f}".replace(".", ",") + "%"


def mesaj(o: dict) -> str | None:
    """
    Haftalik nabiz satirlari. Veri yoksa None (mesaj GONDERILMEZ).

    KULLANICIYA GIDEN METIN TAM TURKCE (`mesaj-bicimi-ve-gundem`): ASCII
    kurali koda uygulanir, mesaja degil — ASCII mesaj okunmuyordu.
    """
    d = o.get("donemler") or {}
    if not d:
        return None
    h = lambda k, a: _yuzde((d.get(a) or {}).get(k))    # noqa: E731
    pb = o["para_birimi"]
    nav = f"{o['nav']:.0f}"
    # Yalnizca HESAPLANABILEN donemler; hesap yilbasindan sonra acildiysa
    # "yilbasindan" yok (bkz. `ozet`), 1 aydan gencse "1 ay" yok.
    etiket = (("1A", "1 ay"), ("yilbasi", "yılbaşından"), ("baslangic", "başlangıçtan"))
    var = [(a, e) for a, e in etiket if a in d]
    satir = [
        f"📈 <b>IBKR gerçek getiri</b> ({o['olcu']}, {pb}, {o['bitis']})",
        "Hesap: " + " · ".join(f"{e} {h('hesap_%', a)}" for a, e in var)
        + f" · net varlık {nav} {pb}",
        "S&P 500 (VUSA, EUR, fiyat): "
        + " · ".join(f"{e} {h('kiyas_%', a)}" for a, e in var),
    ]
    fark = (d.get("baslangic") or {}).get("fark_puan")
    if fark is not None:
        satir.append(f"Fark (başlangıçtan): {fark:+.1f} puan".replace(".", ","))
    if o.get("kanit_degil"):
        satir.append(f"<i>Hesap {KANIT_ASGARI_NAV:.0f} {pb} altında ya da "
                     f"{KANIT_ASGARI_GUN} günden kısa ({o['gun']} gün): bu bir "
                     "ÖLÇÜM, kanıt değil. VUSA temettüsüz fiyat getirisi.</i>")
    else:
        satir.append("<i>VUSA temettüsüz fiyat getirisi (yılda ~%1-1,5 eksik).</i>")
    return "\n".join(satir)
