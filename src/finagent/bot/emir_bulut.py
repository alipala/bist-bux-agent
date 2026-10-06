"""
BULUT EMIR AKISI — IBKR TALIMATI (`create_order_instruction`).

NEDEN (2026-10-06): bot buluta (Railway) tasiniyor ve orada yerel ag gecidi
(CPGW) YOK. IBKR'nin bulut baglanti noktasi CANLI EMIR VEREMEZ — IBKR'nin
kendi belgesi: "The AI can generate an instruction based on your request,
but it cannot place orders on your behalf." Talimat, IBKR uygulamasindaki
"AI Instructions" sekmesinde INSANIN gonderdigi bir taslaktir.

Yani bulutta UC kapi var, ucuncusu IBKR'nin kendisi:
    1. HAZIRLIK   `/emir` -> on kontrol (bulut verisiyle) -> ozet + [ONAYLA]
    2. ONAY       buton -> on kontrol YENIDEN -> IBKR'de TALIMAT olusur
    3. GONDERIM   Ali IBKR uygulamasinda talimati emre cevirir

`emirakis`in doktrini AYNEN gecerli (engel varsa buton yok; onay 5 dk ve
parmak izine bagli; defter satiri istekten ONCE acilir; zaman asiminda
YENIDEN GONDERILMEZ, mutabakat yapilir). Farklar:

* YALNIZCA `ibkr.sahip`. Bulut baglantisi TEK hesabin OAuth onayi; baska
  bir sahibin o hesaba talimat yazmasi kabul edilemez (CPGW yolundaki
  `/emir`de bu kontrol eksikti — 6 Eki mimari incelemesi, madde 3).
* YALNIZCA LIMIT/MARKET ve DAY/GTC/OPG. Talimat STP ve IOC TASIMIYOR
  (arac semasi); `/stop` bulutta reddedilir, sebebiyle.
* IBKR on izlemesi (`/whatif`, komisyon) YOK — bulutta karsiligi yok;
  tutar kendi carpimimiz ve oyle YAZILIR.
* Acik emir/talimat listesinin DOLU bicimi henuz olculmedi (6 Eki: ikisi
  de bos). Cift emir kontrolu okunabilen alanlarla yapilir; okunamazsa
  ENGEL degil UYARI — ucuncu kapi (insan, IBKR ekrani) yine de var.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
from datetime import datetime, timezone

from ..ibkr import onkontrol as OK
from ..ibkr.istemci import DurumBilinmiyorHatasi, IbkrHatasi

log = logging.getLogger(__name__)

KANAL_TIPI = "talimat"
TALIMAT_TURU = {"LMT": "LIMIT", "MKT": "MARKET"}
TALIMAT_SURELERI = frozenset({"DAY", "GTC", "OPG"})


def _senkron(fn):
    """Async isi senkron cagiran icin calistirir. Calisan bir olay dongusu
    icindeysek (sohbet araci) AYRI IS PARCACIGINDA — `anyio.run` ic ice
    calismaz."""
    import asyncio
    import anyio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return anyio.run(fn)
    with concurrent.futures.ThreadPoolExecutor(1) as h:
        return h.submit(anyio.run, fn).result()


def _cagir(arac: str, arg: dict | None = None):
    from ..ibkr import mcp_kanal
    return mcp_kanal.cagir_async(arac, arg or {})


_cagir_gercek = _cagir     # testler `_cagir`i degistirip geri koyar


def sahip_kontrol(s, sahip: str) -> None:
    hesap_sahibi = s.get("ibkr.sahip") if s else None
    if not hesap_sahibi or sahip != hesap_sahibi:
        from .emirakis import EmirHatasi
        raise EmirHatasi("IBKR talimati yalnizca hesabin sahibi tarafindan "
                         "verilebilir (<code>ibkr.sahip</code>).")


def _bulut_hesap(db, sahip: str) -> str:
    """Defterin `hesap` alani. Bulut yaniti hesap kimligi TASIMIYOR; ayni
    sahibin son kaydindaki hesap (tek hesap) — yoksa 'bulut'."""
    r = db.query("SELECT hesap FROM emirler WHERE sahip=? AND hesap NOT IN ('', 'bulut') "
                 "ORDER BY id DESC LIMIT 1", (sahip,))
    return r[0]["hesap"] if r else "bulut"


def parmak_izi(v: dict) -> str:
    ham = json.dumps({k: v.get(k) for k in ("conid", "yon", "tur", "adet",
                                             "fiyat", "sure")},
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(("talimat|" + ham).encode()).hexdigest()


def _liste(veri, anahtar: str) -> list | None:
    """`{"instructions": [...]}` / `{"orders": [...]}` -> liste. Bicim
    tanimsizsa None (okunamadi) — bos liste ILE KARISTIRILMAZ."""
    if isinstance(veri, dict) and isinstance(veri.get(anahtar), list):
        return veri[anahtar]
    if isinstance(veri, list):
        return veri
    return None


def _ayni_kagit_yon(kayit: dict, conid: str, yon: str) -> bool:
    c = str(kayit.get("contract_id_ex") or kayit.get("contract_id")
            or kayit.get("conid") or "").split("@")[0]
    y = str(kayit.get("side") or "").upper()
    return c == str(conid) and y in (yon, yon[:1])


async def onkontrol_async(db, conid: str, yon: str, tur: str, adet: float,
                          fiyat: float | None) -> OK.Onkontrol:
    """`onkontrol.dogrula`nin bulut karsiligi — AYNI esikler, AYNI cumleler."""
    from ..ibkr import bulut as B
    from ..ibkr.portfoy import mcp_pozisyonlari
    k = OK.Onkontrol()

    # --- canli fiyat ve veri kipi ---
    try:
        q = await B.kotasyon_async(int(conid), _cagir_async=lambda a, g: _cagir(a, g))
    except (IbkrHatasi, ValueError) as e:
        k.engeller.append(f"canli fiyat alinamadi ({e}) — referanssiz talimat verilmez")
        return k._sonlandir()
    k.referans_fiyat = q["son"] if q["son"] is not None else q["orta"]
    k.referans_kip = q["kip"]
    if not q["gercek_zamanli"]:
        if tur == "MKT":
            k.engeller.append(f"piyasa emri + {q['kip']} veri — ne odeyecegin BILINMIYOR")
        else:
            k.uyarilar.append(f"referans fiyat {q['kip']} (gercek zamanli degil)")

    seans = OK._seans_durumu(db, conid)
    if seans is not None and not seans["acik"]:
        ne_zaman = f"{seans['borsa']} {seans['durum']} · seans {seans['seans']}"
        if tur == "MKT":
            k.engeller.append(f"piyasa KAPALI ({ne_zaman}) — piyasa emri acilis "
                              "fiyatindan doner, ne odeyecegin BILINMIYOR")
        else:
            k.uyarilar.append(f"piyasa KAPALI ({ne_zaman}) — emir acilisa kadar "
                              "kuyrukta bekler")

    if tur == "LMT" and fiyat and k.referans_fiyat:
        sapma = abs(fiyat - k.referans_fiyat) / k.referans_fiyat * 100
        if sapma > OK.AZAMI_KAYMA_PCT:
            k.engeller.append(f"limit {fiyat} canli {k.referans_fiyat:.2f}'den "
                              f"%{sapma:.1f} uzak (sinir %{OK.AZAMI_KAYMA_PCT})")
        elif sapma > OK.AZAMI_KAYMA_PCT / 2:
            k.uyarilar.append(f"limit canli fiyattan %{sapma:.1f} uzak")
    birim = fiyat if tur == "LMT" and fiyat else k.referans_fiyat
    if birim:
        k.tahmini_tutar = birim * adet

    # --- bekleyen TALIMAT ve acik EMIR (ayni kagit + yon) ---
    for arac, anahtar, ad in (("get_order_instructions", "instructions", "talimat"),
                              ("get_account_orders", "orders", "acik emir")):
        try:
            liste = _liste((await _cagir(arac)).veri, anahtar)
        except IbkrHatasi as e:
            liste, sebep = None, str(e)
        else:
            sebep = "bicim tanimsiz"
        if liste is None:
            k.uyarilar.append(f"{ad} listesi okunamadi ({sebep[:80]}) — cift "
                              "kontrolu YAPILAMADI, IBKR ekraninda bak")
            continue
        cakisan = [x for x in liste if isinstance(x, dict)
                   and _ayni_kagit_yon(x, conid, yon)
                   and str(x.get("status") or "").lower() not in
                   ("filled", "cancelled", "canceled", "inactive")]
        if cakisan:
            k.engeller.append(f"ayni kagitta {len(cakisan)} bekleyen {ad} var — "
                              "once onlara bak")

    # --- hesap: alim gucu, eldeki adet, yogunlasma ---
    try:
        oz = await B.hesap_ozeti_async(_cagir_async=lambda a, g=None: _cagir(a, g))
        k.para_birimi = oz.get("para_birimi")
        guc = (oz["ozet"].get("buyingpower") or (None, None))[0]
        netlik = (oz["ozet"].get("netliquidation") or (None, None))[0]
    except IbkrHatasi as e:
        k.uyarilar.append(f"alim gucu okunamadi: {e}")
        guc = netlik = None
    if yon == "BUY" and k.tahmini_tutar is not None and guc is not None:
        if k.tahmini_tutar > guc:
            k.engeller.append(f"alim gucu yetmiyor: {k.tahmini_tutar:.2f} > {guc:.2f}")
        elif netlik and k.tahmini_tutar > netlik * (1 - OK.NAKIT_REZERV_PCT / 100):
            k.uyarilar.append(f"emir sonrasi nakit rezervi %{OK.NAKIT_REZERV_PCT}'in altina duser")
    try:
        pozlar = mcp_pozisyonlari((await _cagir("get_account_positions")).veri)
    except IbkrHatasi as e:
        if yon == "SELL":
            # Elde ne oldugunu bilmeden SATIS: aciga satis riski -> ENGEL.
            k.engeller.append(f"pozisyonlar okunamadi ({e}) — satista eldeki adet "
                              "dogrulanamadi")
        pozlar = []
    mevcut = next((p for p in pozlar if str(p.conid) == str(conid)), None)
    elde = mevcut.adet if mevcut and mevcut.adet else 0.0
    if yon == "SELL" and adet > elde:
        k.engeller.append(f"elde {elde:g} adet var, {adet:g} satilmak isteniyor — "
                          "aciga satis kapsam disi")
    if yon == "BUY" and netlik and k.tahmini_tutar:
        oran = ((mevcut.piyasa_degeri or 0.0) if mevcut else 0.0)
        oran = (oran + k.tahmini_tutar) / netlik * 100
        if oran > OK.AZAMI_POZISYON_PCT:
            k.uyarilar.append(f"emir sonrasi pozisyon agirligi %{oran:.1f} "
                              f"(sinir %{OK.AZAMI_POZISYON_PCT})")
    return k._sonlandir()


def _metin(coz: dict, v: dict, k: OK.Onkontrol) -> str:
    from .emirakis import ONAY_OMRU_SN, _esc
    yon_tr = "AL" if v["yon"] == "BUY" else "SAT"
    satir = ["🧾 <b>IBKR TALIMAT ONAYI</b> <i>(bulut)</i>", "",
             f"<b>{_esc(coz['sembol'])}</b> — {yon_tr} {v['adet']:g} adet",
             f"Tur: {v['tur']}" + (f" @ {v['fiyat']}" if v.get("fiyat") else ""),
             f"Sure: <b>{v['sure']}</b>" + (" <i>(seans sonunda duser)</i>"
                                            if v["sure"] == "DAY" else
                                            " <i>(iptal edilene kadar gecerli)</i>"
                                            if v["sure"] == "GTC" else "")]
    if k.referans_fiyat:
        satir.append(f"Canli referans: {k.referans_fiyat:.2f} <i>({k.referans_kip})</i>")
    if k.tahmini_tutar:
        satir.append(f"Tahmini tutar: {k.tahmini_tutar:,.2f} {k.para_birimi or ''}".rstrip()
                     + " <i>(kendi hesabimiz; IBKR on izlemesi bulutta yok)</i>")
    satir.append("Aracı kurum: <b>IBKR</b> · <b>TALIMAT</b> — onaylarsan IBKR "
                 "uygulamasinda <i>AI Instructions</i> altinda belirir; CANLI EMRE "
                 "sen cevirirsin.")
    if k.uyarilar:
        satir += ["", "⚠️ <b>Uyarilar</b>"] + [f"• {_esc(u)}" for u in k.uyarilar]
    if k.engeller:
        satir += ["", "⛔️ <b>Engeller</b>"] + [f"• {_esc(e)}" for e in k.engeller]
        satir += ["", "<i>Talimat olusturulmayacak.</i>"]
    else:
        satir += ["", f"<i>Onay {int(ONAY_OMRU_SN // 60)} dakika gecerli.</i>"]
    return "\n".join(satir)


def hazirla(s, db, coz: dict, sahip: str, kanal: str | None = None
            ) -> tuple[str, dict | None]:
    from .emirakis import EmirHatasi, _conid, _emir_satiri_ac, varsayilan_sure
    sahip_kontrol(s, sahip)
    if coz["tur"] not in TALIMAT_TURU:
        raise EmirHatasi(
            f"{coz['tur']} bulutta verilemez: IBKR talimati yalnizca LIMIT ve "
            "MARKET tasiyor. Koruma icin <code>/alarm</code> kur ya da stop'u "
            "IBKR uygulamasindan gir.")
    sure = coz.get("sure") or varsayilan_sure(s)
    if sure not in TALIMAT_SURELERI:
        raise EmirHatasi(f"Sure {sure} talimatta yok — {sorted(TALIMAT_SURELERI)}.")
    conid, iid = _conid(db, coz["sembol"])
    v = {"kanal_tipi": KANAL_TIPI, "sembol": coz["sembol"], "conid": conid,
         "yon": coz["yon"], "tur": coz["tur"], "adet": float(coz["adet"]),
         "fiyat": coz.get("fiyat"), "sure": sure}
    k = _senkron(lambda: onkontrol_async(db, conid, v["yon"], v["tur"], v["adet"],
                                         v["fiyat"]))
    metin = _metin(coz, v, k)
    if not k.gonderilebilir:
        return metin, None
    v["parmak_izi"] = parmak_izi(v)
    v["satir_id"] = _emir_satiri_ac(
        db, sahip=sahip, hesap=_bulut_hesap(db, sahip), instrument_id=iid,
        conid=conid, yon=v["yon"], tur=v["tur"], adet=v["adet"], fiyat=v["fiyat"],
        sure=sure, para_birimi=k.para_birimi, referans_fiyat=k.referans_fiyat,
        referans_kip=k.referans_kip, parmak_izi=v["parmak_izi"],
        durum="hazirlandi", not_="; ".join(["bulut talimati"] + k.uyarilar),
        kanal=kanal)
    v["hazirlik_ts"] = datetime.now(timezone.utc).timestamp()
    return metin, v


def _talimat_bilgisi(veri) -> tuple[str | None, str | None]:
    """Olusturma yanitindan (kimlik, baglanti). Bicim OLCULMEDI: bilinen
    anahtar adlari denenir; bulunamazsa None — UYDURULMAZ, ham metin
    deftere yazilir."""
    if not isinstance(veri, dict):
        return None, None
    d = veri.get("instruction") if isinstance(veri.get("instruction"), dict) else veri
    kimlik = d.get("id") or d.get("instruction_id")
    url = next((str(v) for k2, v in d.items()
                if isinstance(v, str) and ("url" in k2.lower() or "link" in k2.lower())), None)
    return (str(kimlik) if kimlik is not None else None), url


def yurut(s, db, veri: dict, sahip: str) -> str:
    from .emirakis import ONAY_OMRU_SN, _esc
    sahip_kontrol(s, sahip)
    satir_id = veri.get("satir_id")
    yas = datetime.now(timezone.utc).timestamp() - float(veri.get("hazirlik_ts") or 0)
    if yas > ONAY_OMRU_SN:
        db.emir_guncelle(satir_id, durum="suresi_doldu")
        return (f"⏱ <b>Onay suresi doldu</b> ({yas / 60:.0f} dk).\n"
                "Referans fiyat eskidi — talimati yeniden hazirla.")
    if parmak_izi(veri) != veri.get("parmak_izi"):
        db.emir_guncelle(satir_id, durum="reddedildi", not_="onay verisi degismis")
        return "⛔️ Onay verisi talimatla uyusmuyor — olusturulmadi."
    k = _senkron(lambda: onkontrol_async(db, veri["conid"], veri["yon"], veri["tur"],
                                         veri["adet"], veri.get("fiyat")))
    if not k.gonderilebilir:
        db.emir_guncelle(satir_id, durum="engellendi", not_="; ".join(k.engeller))
        return ("⛔️ <b>Talimat olusturulmadi</b> — onaydan sonra kosullar degisti:\n"
                + "\n".join(f"• {_esc(e)}" for e in k.engeller))
    arg = {"contract_id_ex": str(veri["conid"]), "side": veri["yon"],
           "order_type": TALIMAT_TURU[veri["tur"]], "quantity": float(veri["adet"]),
           "time_in_force": veri["sure"]}
    if veri["tur"] == "LMT":
        arg["limit_price"] = float(veri["fiyat"])
    simdi = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.emir_guncelle(satir_id, onay_ts=simdi, onay_kim=sahip, durum="onaylandi",
                     gonderim_ts=simdi)
    try:
        r = _senkron(lambda: _cagir("create_order_instruction", arg))
    except DurumBilinmiyorHatasi:
        # Istek IBKR'ye ULASMIS OLABILIR: yeniden olusturmak CIFT TALIMAT.
        db.emir_guncelle(satir_id, durum="bilinmiyor",
                         not_="talimat zaman asimi — IBKR'de AI Instructions'a bak")
        return ("⚠️ <b>Talimatin durumu BILINMIYOR.</b>\nIstek IBKR'ye ulasmis "
                "olabilir. <i>Yeniden olusturmadan once IBKR uygulamasinda "
                "AI Instructions'a bak.</i>")
    except IbkrHatasi as e:
        db.emir_guncelle(satir_id, durum="reddedildi", not_=str(e)[:300])
        return f"⛔️ <b>Talimat reddedildi</b>\n{_esc(e)}"
    kimlik, url = _talimat_bilgisi(r.veri)
    db.emir_guncelle(satir_id, durum=KANAL_TIPI,
                     not_=(f"talimat {kimlik or '?'}"
                           + ("" if kimlik else f" | ham: {r.ham[:200]}")))
    return ("✅ <b>Talimat IBKR'de hazir</b> — HENUZ EMIR DEGIL.\n"
            f"{_esc(veri['sembol'])} {'AL' if veri['yon'] == 'BUY' else 'SAT'} "
            f"{veri['adet']:g}" + (f" @ {veri['fiyat']}" if veri.get("fiyat") else "")
            + f" · {veri['sure']}\n"
            "IBKR uygulamasi → <b>Orders &amp; Trades → AI Instructions</b>'tan "
            "gozden gecirip gonder."
            + (f"\n<a href=\"{_esc(url)}\">Talimati ac</a>" if url else ""))


def talimat_eslestir(satirlar, islemler: list[dict] | None) -> list[tuple[int, dict]]:
    """
    SAF. `talimat` durumundaki defter satirini IBKR'de olusan EMIRLE
    eslestirir: ayni sembol + yon + adet, talimattan SONRA, ve ADAY TEK.

    Bulut islem kaydinda conid YOK (olculdu 6 Eki: symbol, side, size,
    order_id, trade_time...). Belirsizlikte (0 ya da >1 aday) HICBIR SEY
    yazilmaz — yanlis eslesme yanlis dolum demek; yokluk kanit degil.
    """
    cikti = []
    kullanilan: set = set()
    for r in satirlar:
        r = dict(r)
        if r.get("durum") != KANAL_TIPI or r.get("emir_id"):
            continue
        sembol = str(r.get("symbol") or "").upper()
        once = str(r.get("onay_ts") or r.get("olusma_ts") or "")
        adaylar = {}
        for t in islemler or []:
            no = str(t.get("order_id") or "")
            if not no or no in kullanilan:
                continue
            if (str(t.get("symbol") or "").upper() == sembol
                    and str(t.get("side") or "").upper() == r.get("yon")
                    and str(t.get("trade_time") or "") >= once[:19]):
                adaylar.setdefault(no, []).append(t)
        uygun = [no for no, ts in adaylar.items()
                 if abs(sum(float(x.get("size") or 0) for x in ts) - float(r["adet"])) < 1e-9]
        if len(uygun) == 1:
            kullanilan.add(uygun[0])
            cikti.append((r["id"], adaylar[uygun[0]][0]))
    return cikti
