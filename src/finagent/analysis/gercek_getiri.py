"""
GERCEK GETIRI VE MALIYET (plan adim 4/5, 9 Eki) — "yatirdigim paraya gore
ne kazandim, ayni parayi endekse koysam ne olurdu, bana neye mal oldu".

KAYNAK: araci kurumun ISLEM DOKUMU (BUX "Export transactions" CSV). Ali
9 Eki'de uc kategori verdi (deposits, withdrawals, trades); BUX gunde 3
export izin veriyor, kalanlar (fees, dividends, interest, corporate
actions) sonra gelecek. Kod eksik kategoriyi ADIYLA soyler.

OLCULER
-------
* PARA-AGIRLIKLI GETIRI (MWR / XIRR): yalnizca DIS akislar (yatirma,
  cekme) + bugunku deger. Ucret ve temettu zaten degerin icinde — bu
  yuzden MWR uc kategoriyle TAM olculur, eksik kategori onu bozmaz.
* KIYAS — "AYNI PARAYI AYNI GUNLERDE X'E KOYSAYDIN": her yatirma o gunun
  kapanisindan X alir, her cekme X satar; bugunku degerle kiyas. Getiri
  yuzdesi kiyaslamaktan dogrudur: zamanlama ikisinde de AYNI.
* MUTABAKAT (KANIT): dokumden yeniden kurulan adetler son ekran kaydiyla
  karsilastirilir. Tutmayan kalem, dokumde eksik islem (bolunme, ayri
  kategorideki alim) demektir ve ADIYLA soylenir.
* NAKIT ZINCIRI: dokumdeki her nakit satiri islem SONRASI bakiyeyi
  tasiyor; ardisik satirlar arasindaki fark, aktarilmamis bir hareket
  (ucret -0,99/-1,99, temettu, faiz) demektir. Toplami "aciklanamayan"
  diye verilir — ucret diye ETIKETLENMEZ (olculmedi).
* KUR MAKASI: dovizli alim/satimda kurumun uyguladigi kur, ayni gunun
  piyasa kapanisiyla (EURUSD) kiyaslanir. Gun ici zamanlama farki da
  icindedir — tek islemde gurultu, toplamda tahmin; boyle beyan edilir.

SAF fonksiyonlar db bilmez; `hesap_ozeti` ve `aktar` tek db girisleri.
"""
from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import date, datetime

log = logging.getLogger(__name__)

# BUX dokumunun basligi (9 Eki olculdu). Ilk 6 alan ZORUNLU; kalanlar
# yoksa bos sayilir.
BUX_ALANLAR = {
    "Transaction Time (CET)": "ts", "Transaction Category": "kategori",
    "Transaction Type": "tur", "Transfer Type": "transfer",
    "Transaction Amount": "tutar", "Transaction Currency": "para",
    "Cash Balance Amount": "bakiye", "Asset Id": "isin", "Asset Name": "varlik",
    "Asset Quantity": "adet", "Asset Price": "fiyat", "Asset Currency": "varlik_para",
    "Currency Pair": "kur_cifti", "Exchange Rate": "kur",
    "Profit And Loss Amount": "kar_zarar", "Transaction Description": "aciklama",
}
BUX_ZORUNLU = ("Transaction Time (CET)", "Transaction Category", "Transfer Type",
               "Transaction Amount", "Transaction Currency")
SAYISAL = ("tutar", "bakiye", "adet", "fiyat", "kur", "kar_zarar")
DIS_AKIS = ("deposits", "withdrawals")
# Dokumde olabilecek ama henuz aktarilmamis kategoriler — eksikse soylenir.
BEKLENEN_KATEGORI = ("deposits", "withdrawals", "trades", "fees", "dividends",
                     "interest", "corporate_actions")
KIYASLAR = (("VUSA", "S&P 500"), ("CNDX", "Nasdaq 100"))
ADET_TOLERANS = 1e-5
# Kur makasi bu kadar islemden azsa BEYAN EDILMEZ: kapanis kuruyla kiyas
# gun ici hareketi de iceriyor (9 Eki: 7 islemde ortalama +%0,15,
# agirlikli -%0,05 — isaret bile tutmuyor, gurultu makastan buyuk).
KUR_ASGARI_ISLEM = 10


class DokumHatasi(ValueError):
    """Dosya BUX dokumu degil ya da okunamiyor — kullaniciya aynen soylenir."""


# ---------------------------------------------------------------- okuma

def bux_mu(metin: str) -> bool:
    ilk = (metin or "").lstrip("﻿").splitlines()[:1]
    return bool(ilk) and all(a in ilk[0] for a in BUX_ZORUNLU)


def bux_oku(metin: str) -> list[dict]:
    """
    SAF. BUX CSV metni -> normal satirlar. Baslik eksikse DokumHatasi.
    Sayi okunamayan zorunlu alan satiri DUSURMEZ, hata verir: sessiz
    atlama, getiriyi sessizce degistirirdi.
    """
    metin = (metin or "").lstrip("﻿")
    if not bux_mu(metin):
        raise DokumHatasi("BUX islem dokumu degil (beklenen baslik yok)")
    out = []
    for i, r in enumerate(csv.DictReader(io.StringIO(metin)), start=2):
        if not any((v or "").strip() for v in r.values()):
            continue
        s = {}
        for alan, ad in BUX_ALANLAR.items():
            v = (r.get(alan) or "").strip()
            if ad in SAYISAL:
                if v == "":
                    s[ad] = None
                    continue
                try:
                    s[ad] = float(v)
                except ValueError:
                    raise DokumHatasi(f"satir {i}: '{alan}' sayi degil: {v!r}") from None
            else:
                s[ad] = v
        if not s["ts"] or not s["kategori"] or not s["transfer"] or s["tutar"] is None:
            raise DokumHatasi(f"satir {i}: zorunlu alan bos")
        try:
            datetime.fromisoformat(s["ts"])
        except ValueError:
            raise DokumHatasi(f"satir {i}: zaman okunamadi: {s['ts']!r}") from None
        s["isin"] = s["isin"] or ""
        out.append(s)
    if not out:
        raise DokumHatasi("dosyada islem satiri yok")
    return out


# ---------------------------------------------------------------- yazma

def _anahtar(r: dict) -> tuple:
    return (r["ts"], r["transfer"], round(float(r["tutar"]), 8), r.get("isin") or "")


def aktarim_plani(db, sahip: str, hesap: str, satirlar: list[dict]) -> dict:
    """Onay metni icin: kac yeni, kac zaten var, kategori sayilari, donem."""
    var = {(r["ts"], r["transfer"], round(float(r["tutar"]), 8), r["isin"]) for r in db.query(
        "SELECT ts, transfer, tutar, isin FROM hesap_hareketi WHERE sahip=? AND hesap=?",
        (sahip, hesap))}
    yeni = [r for r in satirlar if _anahtar(r) not in var]
    kat: dict[str, int] = defaultdict(int)
    for r in yeni:
        kat[r["kategori"]] += 1
    ts = sorted(r["ts"] for r in satirlar)
    return {"hesap": hesap, "toplam": len(satirlar), "yeni": len(yeni),
            "zaten_var": len(satirlar) - len(yeni), "kategoriler": dict(kat),
            "ilk": ts[0][:10], "son": ts[-1][:10]}


def aktar(db, sahip: str, hesap: str, satirlar: list[dict]) -> int:
    """INSERT OR IGNORE; yeni yazilan satir sayisi. Tek islem (atomik)."""
    if not sahip or not hesap:
        raise ValueError("sahip ve hesap zorunlu")
    once = db.query("SELECT COUNT(*) n FROM hesap_hareketi WHERE sahip=? AND hesap=?",
                    (sahip, hesap))[0]["n"]
    alanlar = ["ts", "kategori", "tur", "transfer", "tutar", "para", "bakiye", "isin",
               "varlik", "adet", "fiyat", "varlik_para", "kur_cifti", "kur",
               "kar_zarar", "aciklama"]
    with db._conn:
        db._conn.executemany(
            f"INSERT OR IGNORE INTO hesap_hareketi (sahip, hesap, {', '.join(alanlar)}) "
            f"VALUES (?, ?, {', '.join('?' * len(alanlar))})",
            [(sahip, hesap, *[r.get(a) if a != "isin" else (r.get("isin") or "")
                              for a in alanlar]) for r in satirlar])
    sonra = db.query("SELECT COUNT(*) n FROM hesap_hareketi WHERE sahip=? AND hesap=?",
                     (sahip, hesap))[0]["n"]
    return sonra - once


def hareketler(db, sahip: str, hesap: str) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT * FROM hesap_hareketi WHERE sahip=? AND hesap=? ORDER BY ts, rowid",
        (sahip, hesap))]


# ---------------------------------------------------------------- hesap

def _gun(ts) -> date:
    return date.fromisoformat(str(ts)[:10])


def xirr(akislar: list[tuple[date, float]]) -> float | None:
    """
    SAF. Yatirimci gozuyle akislar (yatirma -, cekme ve son deger +) ->
    yillik getiri (kesir). Isaret degismiyorsa ya da kok yoksa None.
    Ikiye bolme: Newton'un patlayabildigi seyrek akislarda kararli.
    """
    if len(akislar) < 2 or all(a >= 0 for _, a in akislar) or all(a <= 0 for _, a in akislar):
        return None
    t0 = min(t for t, _ in akislar)

    def f(r):
        return sum(a / (1.0 + r) ** ((t - t0).days / 365.25) for t, a in akislar)
    lo, hi = -0.99, 10.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        m = (lo + hi) / 2
        fm = f(m)
        if flo * fm <= 0:
            hi, fhi = m, fm
        else:
            lo, flo = m, fm
    return (lo + hi) / 2


def dis_akislar(satirlar: list[dict]) -> list[tuple[date, float]]:
    """SAF. Hesaba giren (+) / cikan (-) EUR para, gun bazinda (hesap gozuyle)."""
    out = []
    for r in satirlar:
        if r["kategori"] in DIS_AKIS and r["transfer"].startswith("CASH"):
            if (r["para"] or "").upper() != "EUR":
                raise ValueError(f"EUR olmayan dis akis: {r['para']} — ceviri yazilmadi")
            out.append((_gun(r["ts"]), float(r["tutar"])))
    return out


def kiyas_degeri(akislar: list[tuple[date, float]], fiyat) -> float | None:
    """
    SAF. Ayni akislar X'e: yatirma o gunun kapanisindan alir, cekme satar.
    `fiyat(gun)` o gun ya da oncesindeki son kapanis. Fiyat yoksa None.
    Bugunku deger = adet (cagiran bugunun fiyatiyla carpar).
    """
    adet = 0.0
    for t, a in akislar:
        p = fiyat(t)
        if not p:
            return None
        adet += a / p
    return adet


def nakit_zinciri(satirlar: list[dict]) -> dict:
    """
    SAF. EUR nakit satirlarinin bakiye zinciri. Her satir islem SONRASI
    bakiyeyi tasir; onceki bakiye + tutar ile tutmuyorsa arada AKTARILMAMIS
    bir hareket vardir. Doner {kopukluk, aciklanamayan_eur, en_buyuk}.
    """
    bak, kop, toplam, buyuk = None, 0, 0.0, []
    for r in satirlar:
        if not r["transfer"].startswith("CASH") or (r["para"] or "").upper() != "EUR" \
                or r.get("bakiye") is None:
            continue
        beklenen = (bak or 0.0) + float(r["tutar"])
        fark = round(float(r["bakiye"]) - beklenen, 2)
        if bak is not None and abs(fark) > 0.011:
            kop += 1
            toplam += fark
            buyuk.append({"gun": str(r["ts"])[:10], "fark_eur": fark})
        bak = float(r["bakiye"])
    buyuk.sort(key=lambda x: abs(x["fark_eur"]), reverse=True)
    return {"kopukluk": kop, "aciklanamayan_eur": round(toplam, 2), "en_buyuk": buyuk[:5]}


def yeniden_kurulan_adetler(satirlar: list[dict]) -> dict[str, dict]:
    """SAF. ISIN -> {varlik, adet}; alim +, satim -."""
    out: dict[str, dict] = {}
    for r in satirlar:
        if r["transfer"] not in ("ASSET_TRADE_BUY", "ASSET_TRADE_SELL") or r.get("adet") is None:
            continue
        k = out.setdefault(r["isin"] or r["varlik"], {"varlik": r["varlik"], "adet": 0.0})
        k["adet"] += float(r["adet"]) * (1 if r["transfer"] == "ASSET_TRADE_BUY" else -1)
    return out


def mutabakat(dokum: dict[str, dict], kayit: dict[str, float]) -> dict:
    """
    SAF. Dokumden kurulan adetleri son ekran kaydiyla eslestirir — ADETLE
    (ISIN<->sembol eslemesi kayitta yok; 6 ondalik adet tesadufen tutmaz).
    Doner {eslesen, kayitta_eslesmeyen, dokumde_eslesmeyen}.
    """
    acik = {k: v for k, v in dokum.items() if abs(v["adet"]) > ADET_TOLERANS}
    eslesen, kayitta_yok = [], []
    kullanilan = set()
    for sem, adet in kayit.items():
        if adet is None or adet <= ADET_TOLERANS:
            continue
        bul = next((k for k, v in acik.items() if k not in kullanilan
                    and abs(v["adet"] - adet) <= ADET_TOLERANS), None)
        if bul:
            kullanilan.add(bul)
            eslesen.append(sem)
        else:
            kayitta_yok.append({"sembol": sem, "kayit_adet": adet})
    dokumde = [{"varlik": v["varlik"], "dokum_adet": round(v["adet"], 6)}
               for k, v in acik.items() if k not in kullanilan]
    _yorumla(kayitta_yok, dokumde)
    return {"eslesen": sorted(eslesen), "kayitta_eslesmeyen": kayitta_yok,
            "dokumde_eslesmeyen": dokumde}


def _yorumla(kayitta: list[dict], dokumde: list[dict]) -> None:
    """
    SAF, YERINDE. Her eslesmeyen kaleme SAYIDAN cikan aciklama (`yorum`).
    NEDEN (9 Eki, bulut testi): model eslesmeme sebebini TAHMIN etti ("6 Eki
    satislari dokumde yok") ve yanildi — o satislar dokumdeydi; eksik olan
    ALIMLARDI. Sebep artik veriden: eksi adet = satis alimdan fazla (eksik
    alim kaydi); tam sayi oran = bolunme olasi; esi yok = dokumde islem yok.
    """
    for d in dokumde:
        if d["dokum_adet"] < 0:
            d["yorum"] = ("dokumde satilan adet alinandan fazla: en az bir ALIM kaydi "
                          "eksik (aktarilmamis kategori ya da export disi)")
    for k in kayitta:
        for d in dokumde:
            if d["dokum_adet"] > 0 and not d.get("yorum"):
                oran = k["kayit_adet"] / d["dokum_adet"]
                n = round(oran)
                if n >= 2 and abs(oran - n) < 0.001:
                    k["yorum"] = d["yorum"] = (
                        f"kayit/dokum orani {n}: {n}'e 1 hisse bolunmesi olasi "
                        "(corporate actions dokumu aktarilmadi)")
                    k["dokumdeki_karsiligi"] = d["varlik"]
                    break
        else:
            k.setdefault("yorum", "dokumde bu kalemin islemi bulunamadi (farkli kategori "
                                  "ya da dokum disi edinim)")
    for d in dokumde:
        d.setdefault("yorum", "kayitta bu adetle eslesen kalem yok")


def kur_makasi(satirlar: list[dict], piyasa) -> dict | None:
    """
    SAF. EURUSD ile cevrilen islemlerde kurumun kuru vs gunun kapanisi.
    `piyasa(gun)` -> EURUSD kapanisi (yoksa None). Maliyet: EUR tutarin
    makas kadari (aleyhe yon). Doner {islem, kapsanan, ort_makas_%, tahmini_eur}.
    """
    olc, eur_top, maliyet, kapsam_disi = [], 0.0, 0.0, 0
    for r in satirlar:
        if r["transfer"] not in ("CASH_DEBIT", "CASH_CREDIT") or r.get("kur_cifti") != "EURUSD" \
                or not r.get("kur"):
            continue
        p = piyasa(_gun(r["ts"]))
        if not p:
            kapsam_disi += 1
            continue
        # Alimda EUR -> USD: kurum DUSUK kur verirse aleyhe (az USD).
        # Satimda USD -> EUR: kurum YUKSEK kur verirse aleyhe (az EUR).
        alim = r["transfer"] == "CASH_DEBIT"
        makas = (p - r["kur"]) / p if alim else (r["kur"] - p) / p
        olc.append(makas)
        eur = abs(float(r["tutar"]))
        eur_top += eur
        maliyet += eur * makas
    if not olc:
        return None if not kapsam_disi else {"islem": kapsam_disi, "kapsanan": 0,
                                             "guvenilir": False}
    return {"islem": len(olc) + kapsam_disi, "kapsanan": len(olc),
            "guvenilir": len(olc) >= KUR_ASGARI_ISLEM,
            "ort_makas_%": round(sum(olc) / len(olc) * 100, 2),
            "agirlikli_makas_%": round(maliyet / eur_top * 100, 2) if eur_top else None,
            "tahmini_eur": round(maliyet, 2),
            "not": "gunun KAPANIS kuruyla kiyas; gun ici kur hareketi de icinde — "
                   "tek islemde gurultu, toplamda tahmin"}


# ---------------------------------------------------------------- db girisi

def _asof(db, sembol: str, venue: str | None = None):
    r = db.query(f"""SELECT id FROM instruments WHERE UPPER(symbol)=?
                     {'AND venue=?' if venue else ''} LIMIT 1""",
                 (sembol, venue) if venue else (sembol,))
    if not r:
        return None
    seri = [(_gun(b["ts"]), float(b["close"])) for b in db.fiyat_serisi(r[0]["id"], 3000)
            if b["close"]]
    if not seri:
        return None
    import bisect
    gunler = [t for t, _ in seri]

    def f(t: date):
        i = bisect.bisect_right(gunler, t) - 1
        return seri[i][1] if i >= 0 else None
    f.ilk = gunler[0]
    f.son = gunler[-1]
    return f


def _son_kayit(db, sahip: str, hesap: str) -> tuple[str | None, float | None, dict[str, float]]:
    """Son ekran kaydi: (zaman, EUR deger, sembol->adet). Nakit dahil."""
    from .tema import _eur
    satir = db.latest_positions(hesap, sahip)
    if not satir:
        return None, None, {}
    deger, adet = 0.0, {}
    for r in satir:
        if r["market_value"]:
            e = _eur(db, float(r["market_value"]), r["currency"])
            if e is None:
                return str(satir[0]["snapshot_ts"]), None, {}
            deger += e
        if r["quantity"] is not None:
            adet[r["symbol"]] = float(r["quantity"])
    return str(satir[0]["snapshot_ts"]), round(deger, 2), adet


def hesap_ozeti(db, sahip: str, hesap: str = "bux") -> dict | None:
    """
    Tek hesap: MWR, net yatirilan, kazanc, kiyaslar, mutabakat, nakit
    zinciri, kur makasi, eksik kategoriler. Dokum yoksa None.
    """
    s = hareketler(db, sahip, hesap)
    if not s:
        return None
    akis = dis_akislar(s)
    ts, deger, adet = _son_kayit(db, sahip, hesap)
    out: dict = {"hesap": hesap, "dokum_ilk": s[0]["ts"][:10], "dokum_son": s[-1]["ts"][:10],
                 "yatirilan_eur": round(sum(a for _, a in akis if a > 0), 2),
                 "cekilen_eur": round(-sum(a for _, a in akis if a < 0), 2),
                 "net_yatirilan_eur": round(sum(a for _, a in akis), 2),
                 "kategoriler": sorted({r["kategori"] for r in s}),
                 "eksik_kategoriler": [k for k in BEKLENEN_KATEGORI
                                       if k not in {r["kategori"] for r in s}]}
    if deger is None:
        out["olculemedi"] = "son ekran kaydi yok ya da EUR'ya cevrilemedi"
        return out
    bugun = _gun(ts)
    # Kayit dokumden ESKIYSE sonraki akislar degere yansimamistir; MWR yanlis olur.
    sonraki = [a for t, a in akis if t > bugun]
    out.update({"deger_eur": deger, "deger_tarihi": ts[:16],
                "kazanc_eur": round(deger - out["net_yatirilan_eur"], 2)})
    if sonraki:
        out["uyari"] = (f"son ekran kaydi ({ts[:10]}) dokumden ESKI: sonrasinda "
                        f"{len(sonraki)} para hareketi var — getiri yanlis olur, once "
                        "guncel ekran goruntusu")
    r = xirr([(t, -a) for t, a in akis] + [(bugun, deger)])
    out["mwr_yillik_%"] = round(r * 100, 1) if r is not None else None
    out["sure_yil"] = round((bugun - akis[0][0]).days / 365.25, 2) if akis else None
    kiyas = []
    for sem, ad in KIYASLAR:
        f = _asof(db, sem)
        if f is None or not akis or f.ilk > akis[0][0]:
            kiyas.append({"ad": ad, "sembol": sem,
                          "olculemedi": "fiyat serisi ilk yatirmadan sonra basliyor"})
            continue
        a = kiyas_degeri(akis, f)
        p = f(bugun)
        if a is None or not p:
            kiyas.append({"ad": ad, "sembol": sem, "olculemedi": "fiyat yok"})
            continue
        kd = round(a * p, 2)
        kr = xirr([(t, -x) for t, x in akis] + [(bugun, kd)])
        kiyas.append({"ad": ad, "sembol": sem, "ayni_akislarla_eur": kd,
                      "fark_eur": round(deger - kd, 2),
                      "mwr_yillik_%": round(kr * 100, 1) if kr is not None else None})
    out["kiyas"] = kiyas
    out["mutabakat"] = mutabakat(yeniden_kurulan_adetler(s), adet)
    out["nakit_zinciri"] = nakit_zinciri(s)
    fx = _asof(db, "EURUSD", "MAKRO")
    out["kur_makasi"] = kur_makasi(s, fx) if fx else None
    out["not"] = ("MWR yalnizca yatirma/cekme ve bugunku degerle olculur; ucret ve "
                  "temettu degerin icinde. Kiyas: ayni paralar ayni gunlerde endekse "
                  "(fiyat getirisi; VUSA temettusu haric). Kisa sure ve yogun "
                  "pozisyonla fark BECERI KANITI DEGIL.")
    return out


def portfoy_ozeti(db, sahip: str) -> dict:
    """Tum hesaplar: dokumu olan (BUX) MWR; IBKR kendi TWR'si; digerleri olculmedi."""
    hesaplar = list(db.hesaplar(sahip))
    out = {"hesaplar": [], "olculmedi": []}
    dokumlu = {r["hesap"] for r in db.query(
        "SELECT DISTINCT hesap FROM hesap_hareketi WHERE sahip=?", (sahip,))}
    for h in hesaplar:
        if h in dokumlu:
            out["hesaplar"].append(hesap_ozeti(db, sahip, h))
        elif h == "ibkr":
            try:
                from ..ibkr.getiri import ozet as ibkr_ozet
                o = ibkr_ozet(db, "ibkr")
                if o.get("donemler"):
                    out["hesaplar"].append({"hesap": "ibkr", "olcu": "TWR (IBKR Portfolio Analyst)",
                                            **o})
                    continue
            except Exception as e:                       # noqa: BLE001
                log.warning("[getiri] ibkr ozeti okunamadi: %s", e)
            out["olculmedi"].append({"hesap": h, "sebep": "getiri verisi yok"})
        else:
            out["olculmedi"].append({"hesap": h, "sebep": "islem dokumu aktarilmadi"})
    return out
