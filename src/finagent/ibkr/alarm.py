"""
SUNUCU TARAFI ALARMLAR (IBKR MCP Faz 2b).

NEDEN
-----
Botun butun uyarilari BU MAC'te kosuyor: nabiz, gun ici kosu, tez alarmi.
Mac uyursa, ag duserse ya da CPGW 401 verirse (24 Eyl'de 3 saat) stop'un
kirildigini kimse soylemez. IBKR'nin sunucusunda duran bir alarm bunlarin
hicbirine bagli degil: kosul IBKR'de degerlendirilir, e-posta IBKR'den gider.

IKI ALARM TURU
--------------
  stop          kaniti olan 2N stop'u olan her IBKR pozisyonu:
                son fiyat <= stop. Seviye `pulse.strateji._pozisyon_stopu`
                (maliyet kaydedilen girise %5 icinde degilse stop BILINMIYOR
                ve alarm KURULMAZ — olmayan bir korumayi varmis gibi
                gostermek olurdu). Stop sinavi (docs/stop-sinavi.md) 2N'i
                sabit birakti.
  gunluk_zarar  hesabin gunluk K/Z'si <= -k*sigma. sigma hesabin KENDI
                gunluk getirisinden (`hesap_getirisi`, Faz 3). En az
                ASGARI_GUN gozlem yoksa esik UYDURULMAZ.

KIMIN ALARMINA DOKUNULUR
------------------------
Yalnizca `ibkr_alarm` tablosunda id'si olanlara. Sunucudaki diger alarmlar
kullanicinin — sayilir, soylenir, DOKUNULMAZ. Silinecek/guncellenecek her
id bu tablodan gelir; `yurut` bunu ayrica dogrular.

YAZMA ONCESI KAYIT
------------------
create cagrisindan ONCE satir 'belirsiz' yazilir. Zaman asiminda istek
sunucuya ulasmis olabilir (`DurumBilinmiyorHatasi`); satir yoksa bir sonraki
plan ayni alarmi yeniden kurar ve kullanici IKI e-posta alir. Mutabakat
'belirsiz' satiri sunucuda AD + KOSUL ile arar: bulursa sahiplenir, yoksa
'kurulmadi' der. Silme icin ayni kalip ('siliniyor').

KAYIP KENDILIGINDEN KURULMAZ
----------------------------
Sunucuda olmayan ve bizim silmedigimiz alarm ya TETIKLENDI ya da kullanici
ELLE SILDI. Ikisinde de sessizce yeniden kurmak yanlis: ilkinde asil is
pozisyona bakmak, ikincisinde kullanicinin karari cignenir. `/alarm yenile`
acikca ister.

OLCULEN (25 Eyl, deneme alarmlari kurulup silindi)
--------------------------------------------------
  create_alert -> {"id": "<24 hex>"}
  get_alerts   -> {"alerts": [{"id", "name", "condition": {"contract_id",
                  "exchange", "condition_type", "operator": "lte", "value"},
                  "status": "ACTIVE"}]}   (e-posta ve sure GORUNMEZ)
  delete_alert -> {"ids": [...]}
  DAILY_PNL eksi degeri kabul ediyor. Yuzdenin TABANI (onceki gunun net
  varligi mi) tetiklenmeden DOGRULANAMADI — mesajda soyleniyor.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os

log = logging.getLogger(__name__)

TIP = "ibkr_alarm"
AD_ONEKI = "FA"

SIGMA_KAT = 3.0
ASGARI_GUN = 20
PENCERE_GUN = 60
YUVARLAMA = 0.5          # esik yarim puana yuvarlanir
ESIK_TOLERANS = 0.5      # gunluk esik bu kadar degismedikce guncellenmez (puan)
STOP_TOLERANS = 0.005    # fiyat (bir kurusun yarisi)

# Alarmin e-posta adresi. Izlenen `settings.yaml`a DEGIL, `.env`e: kisisel
# veri, depoya girmez.
EPOSTA_ENV = "IBKR_ALARM_EPOSTA"


class AlarmHatasi(Exception):
    """Plan kurulamadi — sebep mesajda, kullaniciya gidebilir."""


def eposta() -> str:
    """
    Alarm e-postasi. YOKSA HATA: e-postasiz alarm yalnizca IBKR Desktop'ta
    bildirim uretir (arac belgesi) ve bu Mac'teki hicbir seye dayanmayan
    bir uyari isteniyordu; sessizce e-postasiz kurmak amaci bozar.
    """
    e = (os.getenv(EPOSTA_ENV) or "").strip()
    if not e or "@" not in e or " " in e:
        raise AlarmHatasi(
            f"Alarm e-postası tanımlı değil (.env içinde {EPOSTA_ENV}). "
            "E-postasız alarm yalnızca IBKR Desktop açıksa haber verir.")
    return e


# ----------------------------------------------------------------------
# HEDEFLER — ne kurulu OLMALI
# ----------------------------------------------------------------------

def gunluk_esik(getiriler: list[float]) -> tuple[float | None, str]:
    """
    SAF. Gunluk getiri kesirleri (eskiden yeniye) -> (esik yuzdesi, gerekce).
    Esik NEGATIF yuzde (ornek -3.5). Hesap fonlanmadan onceki bastaki sifir
    gunler atilir (olculdu: hesap ilk iki gun 6 EUR, getiri 0).
    """
    i = next((k for k, g in enumerate(getiriler) if g), len(getiriler))
    seri = getiriler[i:][-PENCERE_GUN:]
    n = len(seri)
    if n < ASGARI_GUN:
        return None, (f"hesabın {n} günlük getirisi var, en az {ASGARI_GUN} "
                      "gerekiyor — eşik uydurulmadı")
    ort = sum(seri) / n
    sigma = math.sqrt(sum((g - ort) ** 2 for g in seri) / (n - 1))
    ham = SIGMA_KAT * sigma * 100
    esik = max(YUVARLAMA, round(ham / YUVARLAMA) * YUVARLAMA)
    return -esik, (f"son {n} günde günlük oynaklık %{_tr(sigma * 100)}; "
                   f"{SIGMA_KAT:g}σ ≈ %{_tr(ham)} → %{_tr(esik, 1)}")


def _tr(x: float, basamak: int = 2) -> str:
    return f"{x:.{basamak}f}".replace(".", ",")


def hedefler(db, settings, sahip: str) -> tuple[list[dict], list[str]]:
    """
    Kurulu olmasi gereken alarmlar + kurulamayanlarin sebepleri.
    Doner: (hedefler, notlar). Her hedef: anahtar, tur, sembol,
    instrument_id, conid, ad, kosul_tipi, operator, deger, gerekce.
    """
    from ..bot.emirakis import EmirHatasi, _conid
    from ..pulse.strateji import _pozisyon_stopu

    out, notlar = [], []
    # `strateji_ayari(db)` DEGIL: o endeks uyeligini de dogruluyor ve endeks
    # tablosundaki bir sorun koruma alarmini da durdururdu. Gereken tek alan.
    paralar = [str(p).upper() for p in
               (settings.get("ibkr.strateji.para_birimleri") or [])]
    for p in db.latest_positions("ibkr", sahip):
        sembol = p["symbol"]
        if (sembol or "").upper().startswith("CASH") or (p["asset_type"] or "") == "cash":
            continue
        if not p["quantity"] or float(p["quantity"]) <= 0:
            continue
        stop = _pozisyon_stopu(db, p["instrument_id"], p["avg_cost"], sahip)
        if stop is None:
            notlar.append(f"{sembol}: 2N stop'u bilinmiyor (pozisyon kuralın "
                          "kaydettiği bir girişle eşleşmiyor) — alarm kurulmadı")
            continue
        pb = (p["currency"] or "").upper()
        if paralar and pb not in paralar:
            notlar.append(f"{sembol}: pozisyon {pb or '?'} cinsinden, stop "
                          f"{'/'.join(paralar)} serisinden — alarm kurulmadı")
            continue
        try:
            conid, _ = _conid(db, sembol.upper())
        except EmirHatasi:
            notlar.append(f"{sembol}: IBKR kimliği (conid) yok — alarm kurulmadı")
            continue
        out.append({
            "anahtar": f"stop:{p['instrument_id']}", "tur": "stop",
            "sembol": sembol, "instrument_id": p["instrument_id"],
            "conid": int(conid), "ad": f"{AD_ONEKI} {sembol} stop",
            "kosul_tipi": "LAST", "operator": "LTE", "deger": round(float(stop), 2),
            "gerekce": f"2N stop; ortalama maliyet {_tr(float(p['avg_cost']))} {pb}"})

    rows = db.query("""SELECT gunluk FROM hesap_getirisi WHERE hesap = 'ibkr'
                       ORDER BY tarih""")
    esik, gerekce = gunluk_esik([float(r["gunluk"]) for r in rows])
    if esik is None:
        notlar.append(f"Günlük zarar alarmı: {gerekce}")
    else:
        out.append({"anahtar": "gunluk_zarar", "tur": "gunluk_zarar",
                    "sembol": None, "instrument_id": None, "conid": None,
                    "ad": f"{AD_ONEKI} gunluk zarar", "kosul_tipi": "DAILY_PNL",
                    "operator": "LTE", "deger": esik, "gerekce": gerekce})
    return out, notlar


# ----------------------------------------------------------------------
# PLAN — saf
# ----------------------------------------------------------------------

def _ayni_deger(tur: str, a: float, b: float) -> bool:
    tol = ESIK_TOLERANS if tur == "gunluk_zarar" else STOP_TOLERANS
    return abs(float(a) - float(b)) < tol


def _kosul_eslesir(satir: dict, alarm: dict) -> bool:
    k = alarm.get("condition") or {}
    return (str(alarm.get("name") or "") == satir["ad"]
            and str(k.get("condition_type") or "").upper() == satir["kosul_tipi"]
            and str(k.get("operator") or "").upper() == satir["operator"]
            and k.get("value") is not None
            and abs(float(k["value"]) - float(satir["deger"])) < 1e-6)


def plan(hedef: list[dict], satirlar: list[dict], sunucu: list[dict],
         yenile: bool = False) -> dict:
    """
    SAF. hedef + bizim satirlarimiz + sunucudaki alarmlar -> plan.

    Once MUTABAKAT (satirlarin durumu sunucuya gore), sonra ISLEMLER.
    `satirlar`: `ibkr_alarm` satirlari (silindi/kurulmadi HARIC).
    Doner: {"durum_degisimi": [(satir_id, yeni_durum, alert_id|None,
            deger|None)], "olustur": [hedef], "guncelle": [(satir, hedef)],
            "sil": [satir], "kayip_atlanan": [satir], "yabanci": int}
    """
    sid = {str(a.get("id")): a for a in sunucu if a.get("id")}
    bizim_idler = {str(r["alert_id"]) for r in satirlar if r.get("alert_id")}
    degisim, aktif, kayip = [], {}, {}

    for r in satirlar:
        d, aid = r["durum"], (str(r["alert_id"]) if r.get("alert_id") else None)
        if d in ("aktif", "siliniyor"):
            if aid in sid:
                # SUNUCU DOGRUDUR: guncelleme zaman asimina ugradiysa deger
                # sunucudakidir.
                sv = (sid[aid].get("condition") or {}).get("value")
                deger = float(sv) if sv is not None else float(r["deger"])
                yeni = dict(r, deger=deger, durum="aktif")
                if d != "aktif" or abs(deger - float(r["deger"])) > 1e-9:
                    degisim.append((r["id"], "aktif", aid, deger))
                aktif[r["anahtar"]] = yeni
            else:
                yeni_d = "silindi" if d == "siliniyor" else "kayip"
                degisim.append((r["id"], yeni_d, aid, None))
                if yeni_d == "kayip":
                    kayip[r["anahtar"]] = dict(r, durum="kayip")
        elif d == "belirsiz":
            aday = [a for a in sunucu if str(a.get("id")) not in bizim_idler
                    and _kosul_eslesir(r, a)]
            if len(aday) == 1:
                aid = str(aday[0]["id"])
                bizim_idler.add(aid)
                degisim.append((r["id"], "aktif", aid, None))
                aktif[r["anahtar"]] = dict(r, durum="aktif", alert_id=aid)
            else:
                # 0: istek ulasmamis. >1: hangisi bizim BELIRSIZ -> hicbirine
                # dokunulmaz, satir kurulmadi sayilir ve plan yenisini onerir
                # (kullanici onaylamadan hicbir sey olmaz).
                degisim.append((r["id"], "kurulmadi", None, None))
        elif d == "kayip":
            kayip.setdefault(r["anahtar"], r)

    hedef_a = {h["anahtar"]: h for h in hedef}
    olustur, guncelle, sil, atlanan = [], [], [], []
    for a, h in hedef_a.items():
        m = aktif.get(a)
        if m is not None:
            if not _ayni_deger(h["tur"], m["deger"], h["deger"]):
                guncelle.append((m, h))
            continue
        k = kayip.get(a)
        if k is not None and not yenile and _ayni_deger(h["tur"], k["deger"], h["deger"]):
            atlanan.append(k)
            continue
        olustur.append(h)
    for a, m in aktif.items():
        if a not in hedef_a:
            sil.append(m)
    return {"durum_degisimi": degisim, "olustur": olustur, "guncelle": guncelle,
            "sil": sil, "kayip_atlanan": atlanan,
            "yabanci": len([i for i in sid if i not in bizim_idler])}


def parmak_izi(p: dict) -> str:
    """Onaylanan plan ile yurutulecek plan AYNI mi? (islem + anahtar + deger)"""
    k = sorted([("olustur", h["anahtar"], h["deger"]) for h in p["olustur"]]
               + [("guncelle", m["alert_id"], h["deger"]) for m, h in p["guncelle"]]
               + [("sil", m["alert_id"], m["deger"]) for m in p["sil"]])
    return hashlib.sha256(json.dumps(k).encode()).hexdigest()[:16]


def islem_var_mi(p: dict) -> bool:
    return bool(p["olustur"] or p["guncelle"] or p["sil"])


# ----------------------------------------------------------------------
# ARGUMANLAR — kapi bunlari BIREBIR karsilastirir
# ----------------------------------------------------------------------

def _not(h: dict) -> str:
    if h["tur"] == "stop":
        return (f"finagent: {h['sembol']} son fiyatı 2N stop seviyesine "
                f"({_tr(h['deger'])}) indi. Kural çıkış diyor; emir otomatik "
                "GÖNDERİLMEDİ.")
    return (f"finagent: hesabın bugünkü kaybı %{_tr(-h['deger'], 1)} eşiğini "
            f"geçti ({SIGMA_KAT:g}σ, hesabın kendi oynaklığı).")


def arguman(h: dict, e_posta: str, alert_id: str | None = None) -> dict:
    """create_alert / update_alert argumani. `alert_id` verilirse update."""
    a = {"symbol": h["ad"], "condition_type": h["kosul_tipi"],
         "operator": h["operator"], "value": float(h["deger"])}
    if h["tur"] == "stop":
        # REGULAR: seans disi ince islemler (bir kac hisselik baski)
        # alarmi tetiklemesin; kural da gunluk barin dibine bakiyor.
        a.update({"contract_id": int(h["conid"]), "exchange": "SMART",
                  "active_hours": "REGULAR"})
    # Iki tur de TEK SEFERLIK: tetiklenen alarm sunucudan duser, mutabakat
    # onu 'kayip' yapar ve kendiliginden yeniden kurulmaz (modul basligi).
    a.update({"tif": "UNTIL_TRIGGERED", "email": e_posta, "email_note": _not(h)})
    if alert_id is not None:
        a = {"id": str(alert_id), **a}
    return a


# ----------------------------------------------------------------------
# DB
# ----------------------------------------------------------------------

def satirlar(db, sahip: str) -> list[dict]:
    return [dict(r) for r in db.query(
        """SELECT * FROM ibkr_alarm WHERE sahip = ?
             AND durum NOT IN ('silindi', 'kurulmadi') ORDER BY id""", (sahip,))]


def _durum_yaz(db, satir_id: int, durum: str, alert_id=None, deger=None) -> None:
    sets, arg = ["durum = ?", "guncelleme_ts = datetime('now')"], [durum]
    if alert_id is not None:
        sets.append("alert_id = ?"); arg.append(str(alert_id))
    if deger is not None:
        sets.append("deger = ?"); arg.append(float(deger))
    with db.tx() as c:
        c.execute(f"UPDATE ibkr_alarm SET {', '.join(sets)} WHERE id = ?",
                  (*arg, satir_id))


def mutabakat_yaz(db, p: dict) -> None:
    for sid, durum, aid, deger in p["durum_degisimi"]:
        _durum_yaz(db, sid, durum, aid, deger)


# ----------------------------------------------------------------------
# HAZIRLA / YURUT
# ----------------------------------------------------------------------

def _sunucu(_cagir) -> list[dict]:
    from .mcp_kanal import McpYanitBicimi
    v = _cagir("get_alerts").veri
    if not isinstance(v, dict) or not isinstance(v.get("alerts"), list):
        raise McpYanitBicimi(f"get_alerts beklenmeyen bicim: {str(v)[:200]}")
    return v["alerts"]


def durum_ve_plan(db, settings, sahip: str, yenile: bool = False, _cagir=None):
    """Sunucuyu okur, mutabakati YAZAR, plani doner: (plan, hedefler, notlar)."""
    from . import mcp_kanal
    cagir = _cagir or mcp_kanal.cagir
    h, notlar = hedefler(db, settings, sahip)
    sunucu = _sunucu(cagir)
    p = plan(h, satirlar(db, sahip), sunucu, yenile=yenile)
    mutabakat_yaz(db, p)
    return p, h, notlar


def _satir(h: dict) -> str:
    if h["tur"] == "stop":
        return (f"• <b>{h['sembol']}</b>: son fiyat ≤ <b>{_tr(h['deger'])}</b> "
                f"<i>({h['gerekce']})</i>")
    return (f"• <b>Hesap</b>: günlük kayıp ≥ <b>%{_tr(-h['deger'], 1)}</b> "
            f"<i>({h['gerekce']})</i>")


def metin(p: dict, notlar: list[str], e_posta: str, aktif: list[dict]) -> str:
    L = ["🔔 <b>IBKR sunucu alarmları</b>", ""]
    if aktif:
        L.append("<b>Kurulu (bizim)</b>")
        L += [_satir(r | {"gerekce": "kurulu"}) for r in aktif]
        L.append("")
    if p["olustur"]:
        L.append("<b>Kurulacak</b>")
        L += [_satir(h) for h in p["olustur"]]
        L.append("")
    if p["guncelle"]:
        L.append("<b>Güncellenecek</b>")
        L += [f"{_satir(h)} — şu an {_tr(m['deger'])}" for m, h in p["guncelle"]]
        L.append("")
    if p["sil"]:
        L.append("<b>Silinecek</b> <i>(pozisyon artık yok)</i>")
        L += [f"• {m['ad']} ({_tr(m['deger'])})" for m in p["sil"]]
        L.append("")
    if p["kayip_atlanan"]:
        L.append("<b>Kayıp</b> <i>(tetiklendi ya da elle silindi — yeniden "
                 "kurulmadı; istersen <code>/alarm yenile</code>)</i>")
        L += [f"• {m['ad']} ({_tr(m['deger'])})" for m in p["kayip_atlanan"]]
        L.append("")
    if notlar:
        L.append("<b>Kurulamayanlar</b>")
        L += [f"• {n}" for n in notlar]
        L.append("")
    if p["yabanci"]:
        L.append(f"<i>IBKR'de senin kurduğun {p['yabanci']} alarm daha var — "
                 "onlara dokunulmaz.</i>")
        L.append("")
    if islem_var_mi(p):
        L.append(f"📧 Tetiklenince <b>{e_posta}</b> adresine e-posta gider.")
        L.append("⚠️ Bu alarmlar yalnızca <b>IBKR Desktop</b>'ta görünür; "
                 "mobil uygulamada, TWS'de ve Client Portal'da görünmez.")
        L.append("⚠️ Alarm yalnızca HABER verir, emir göndermez. Gece "
                 "boşluğunda fiyat stop'un çok altında açılabilir.")
        if any(h["tur"] == "gunluk_zarar" for h in p["olustur"]) or \
                any(h["tur"] == "gunluk_zarar" for _, h in p["guncelle"]):
            L.append("<i>Günlük K/Z yüzdesinin IBKR'deki tabanı (önceki günün "
                     "net varlığı varsayılıyor) tetiklenmeden doğrulanamadı.</i>")
        L.append("")
        L.append("Butona basmadan hiçbir şey kurulmaz.")
    else:
        L.append("✅ Değişiklik gerekmiyor.")
    return "\n".join(L).rstrip()


def hazirla(s, db, sahip: str, yenile: bool = False, _cagir=None):
    """(metin, veri|None). veri None ise yapilacak is yok (buton yok)."""
    e = eposta()
    p, _, notlar = durum_ve_plan(db, s, sahip, yenile=yenile, _cagir=_cagir)
    aktif = [r for r in satirlar(db, sahip) if r["durum"] == "aktif"]
    m = metin(p, notlar, e, aktif)
    if not islem_var_mi(p):
        return m, None
    return m, {"parmak_izi": parmak_izi(p), "yenile": bool(yenile)}


def yurut(s, db, veri: dict, sahip: str, _cagir=None) -> str:
    """
    Onaylanan plani yurutur. Plan onaydan bu yana DEGISTIYSE hicbir sey
    yapilmaz (fiyat/pozisyon/sunucu degismis olabilir; kullanici gormedigi
    bir plani onaylamis olmaz).
    """
    from . import mcp_kanal
    from .istemci import DurumBilinmiyorHatasi
    cagir = _cagir or mcp_kanal.cagir
    e = eposta()
    p, _, _ = durum_ve_plan(db, s, sahip, yenile=bool(veri.get("yenile")), _cagir=cagir)
    if parmak_izi(p) != veri.get("parmak_izi"):
        return ("⚠️ Alarm planı onaydan bu yana değişti — hiçbir şey yapılmadı. "
                "Güncel plan için <code>/alarm</code>.")

    # DOKUNULACAK HER ID BIZIM: plan bunu zaten garanti ediyor; burada
    # ikinci kez, yazma aninda.
    bizim = {r["alert_id"] for r in satirlar(db, sahip)
             if r["durum"] == "aktif" and r.get("alert_id")}
    for m in p["sil"] + [m for m, _ in p["guncelle"]]:
        if m["alert_id"] not in bizim:
            raise AlarmHatasi(f"{m['alert_id']} bizim alarmımız değil — dokunulmadı")

    L, tamam = ["🔔 <b>Alarm işlemleri</b>", ""], True
    try:
        if p["sil"]:
            for m in p["sil"]:
                _durum_yaz(db, m["id"], "siliniyor")
            cagir("delete_alert", {"ids": [m["alert_id"] for m in p["sil"]]})
            for m in p["sil"]:
                _durum_yaz(db, m["id"], "silindi")
                L.append(f"🗑 {m['ad']} silindi")
        for m, h in p["guncelle"]:
            cagir("update_alert", arguman(h, e, alert_id=m["alert_id"]))
            _durum_yaz(db, m["id"], "aktif", deger=h["deger"])
            L.append(f"✏️ {h['ad']}: {_tr(m['deger'])} → {_tr(h['deger'])}")
        for h in p["olustur"]:
            with db.tx() as c:
                cur = c.execute(
                    """INSERT INTO ibkr_alarm (sahip, anahtar, tur, sembol,
                       instrument_id, conid, ad, kosul_tipi, operator, deger,
                       durum, gerekce) VALUES (?,?,?,?,?,?,?,?,?,?, 'belirsiz', ?)""",
                    (sahip, h["anahtar"], h["tur"], h["sembol"], h["instrument_id"],
                     h["conid"], h["ad"], h["kosul_tipi"], h["operator"],
                     float(h["deger"]), h["gerekce"]))
                satir_id = cur.lastrowid
            try:
                v = cagir("create_alert", arguman(h, e)).veri
            except mcp_kanal.McpAracCagrilmadi:
                _durum_yaz(db, satir_id, "kurulmadi")
                raise
            aid = v.get("id") if isinstance(v, dict) else None
            if not aid:
                # Satir 'belirsiz' KALIR: mutabakat ad + kosulla bulur.
                raise mcp_kanal.McpYanitBicimi(f"create_alert id donmedi: {str(v)[:200]}")
            _durum_yaz(db, satir_id, "aktif", alert_id=aid)
            L.append(f"✅ {h['ad']} kuruldu")
    except DurumBilinmiyorHatasi as ex:
        tamam = False
        L.append(f"⛔️ İstek IBKR'ye ULAŞMIŞ OLABİLİR ({ex}). Tekrar denemeden "
                 "önce <code>/alarm</code> ile durumu gör — mutabakat kurulup "
                 "kurulmadığını sunucudan okur.")
    except Exception as ex:                                  # noqa: BLE001
        tamam = False
        log.exception("[alarm] yurutme yarida kaldi")
        L.append(f"⛔️ Yarıda kaldı: {type(ex).__name__}: {ex}\n"
                 "<i>Durum için <code>/alarm</code>.</i>")
    if tamam:
        L += ["", f"📧 Bildirim: {e} · yalnızca IBKR Desktop'ta görünür."]
    return "\n".join(L)


# ----------------------------------------------------------------------
# NABIZ HATIRLATMASI — yerel, AGSIZ
# ----------------------------------------------------------------------

def yerel_sapma(db, settings, sahip: str) -> list[str]:
    """
    Hedef alarmlar ile BIZIM KAYITLARIMIZ ayrisiyor mu? Sunucu OKUNMAZ
    (nabizda baglayici cagrisi yok); sunucu tarafindaki kayiplari `/alarm`
    mutabakati bulur. Kurulamayan hedefler (stop bilinmiyor, veri az) burada
    SAYILMAZ: her gece ayni cumleyi tekrarlamak gurultu olurdu.
    """
    h, _ = hedefler(db, settings, sahip)
    rows = satirlar(db, sahip)
    canli = {r["anahtar"]: r for r in rows if r["durum"] in ("aktif", "belirsiz")}
    kayip = {r["anahtar"]: r for r in rows if r["durum"] == "kayip"}
    out = []
    for x in h:
        ad = x["sembol"] or "Hesap günlük zarar"
        m = canli.get(x["anahtar"])
        if m is None:
            k = kayip.get(x["anahtar"])
            if k is None or not _ayni_deger(x["tur"], k["deger"], x["deger"]):
                out.append(f"{ad}: alarm kurulu değil")
        elif not _ayni_deger(x["tur"], m["deger"], x["deger"]):
            out.append(f"{ad}: alarm {_tr(m['deger'])}, olması gereken {_tr(x['deger'])}")
    hedef_a = {x["anahtar"] for x in h}
    for a, m in canli.items():
        if a not in hedef_a:
            out.append(f"{m['ad']}: pozisyon yok, alarm silinmeli")
    return out


def hatirlatma(db, settings, sahip: str, durum_yolu) -> str | None:
    """
    Nabiz icin: sapma VARSA ve son hatirlatmadan FARKLIYSA metin, yoksa
    None. Ayni sapma her gece tekrarlanmaz (kullanici bilerek kurmamis
    olabilir); farkli bir sapma yeniden soylenir.
    """
    from pathlib import Path
    sapma = yerel_sapma(db, settings, sahip)
    p = Path(durum_yolu)
    iz = hashlib.sha256(json.dumps(sorted(sapma)).encode()).hexdigest()[:16]
    try:
        onceki = json.loads(p.read_text(encoding="utf-8")).get("iz")
    except (OSError, ValueError, AttributeError):
        onceki = None
    if iz != onceki:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"iz": iz, "sapma": sapma}), encoding="utf-8")
        except OSError as e:
            log.warning("[alarm] hatirlatma izi yazilamadi: %s", e)
    if not sapma or iz == onceki:
        return None
    return ("🔔 <b>IBKR sunucu alarmları güncel değil</b>\n"
            + "\n".join(f"• {s}" for s in sapma)
            + "\n\nPlanı görmek ve kurmak için <code>/alarm</code>.")
