"""
Telegram emir akisi — GERCEK PARA. LLM'den TETIKLENMEZ.

NEDEN ARAC DEGIL KOMUT
----------------------
Bu akisin giris noktasi `/emir` KOMUTU. `bot/tools.py`ye bir arac olarak
eklenmedi ve eklenmeyecek: model oneri uretir, emri INSAN baslatir.
Bir test `tools.py`nin emir modulunu ice aktarmadigini zorluyor.

Dogal dil de kasten DISARIDA. Bu depoda dogal dil VARSAYILAN ve komut
istisna; burada tersi. Gerekce olculdu — "sil sunu" cumlesi bir zamanlar
SON PORTFOY KAYDINI SILMISTI. Cizgisiz bir cumlenin gercek para
harcamasi kabul edilemez.

IKI KAPI, IKISI DE GEREKLI
--------------------------
    1. HAZIRLIK   `/emir` -> onkontrol -> ozet + [ONAYLA] butonu
                  Engel varsa BUTON HIC CIKMAZ.
    2. YURUTME    butona basildiginda onkontrol YENIDEN kosar.

Ikinci kosum gereksiz gorunebilir ama degil: hazirlik ile onay arasinda
fiyat oynar, bakiye degisir, baska bir emir girebilir. Birinci kosum
insanin NEYI onayladigini gostermek icin; ikincisi hala yapilabilir mi
diye sormak icin.

ONAY KISA OMURLU — `onay.py`nin 24 SAATI DEGIL
----------------------------------------------
`onay.py` butonlari 24 saat yasiyor ve gunluk okuma icin dogru. Emirde
felaket olurdu: gece hazirlanan bir emir sabah basildiginda referans
fiyat saatlerce eski olur. Burada omur 5 DAKIKA ve yasi ayrica
kontrol ediliyor.

DEFTER GONDERIMDEN ONCE ACILIR
------------------------------
Emir satiri `durum='hazirlandi'` ile ONCE yazilir. Gonderim zaman
asimina ugrarsa ortada hicbir kayit olmazdi ve "gonderdik mi" sorusunun
cevabi kaybolurdu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..ibkr import emir as E
from ..ibkr import onkontrol as OK
from ..ibkr.istemci import DurumBilinmiyorHatasi, IbkrHatasi, Istemci
from ..ibkr.piyasa import Piyasa
from ..ibkr.portfoy import Portfoy

log = logging.getLogger(__name__)

TIP = "ibkr_emir"

# Onayin azami yasi. `onay.py`nin 24 saatiyle KARISTIRILMASIN.
ONAY_OMRU_SN = 300.0

YON_ESLEME = {"AL": "BUY", "ALIS": "BUY", "BUY": "BUY",
              "SAT": "SELL", "SATIS": "SELL", "SELL": "SELL"}

KULLANIM = ("<b>Kullanim:</b> <code>/emir SEMBOL AL|SAT ADET [FIYAT]</code>\n"
            "Ornek: <code>/emir NVDA AL 5 214.50</code> (limit)\n"
            "Fiyat yazilmazsa PIYASA emri olur — gercek zamanli veri "
            "yoksa engellenir.")


class EmirHatasi(Exception):
    """Kullaniciya GOSTERILECEK hata; iz dokumu degil."""


def komut_coz(arg: str) -> dict:
    """`NVDA AL 5 214.50` -> alanlar. Anlasilmazsa SEBEBIYLE patlar."""
    parca = (arg or "").split()
    if len(parca) < 3:
        raise EmirHatasi(KULLANIM)
    sembol = parca[0].upper()
    yon = YON_ESLEME.get(parca[1].upper())
    if not yon:
        raise EmirHatasi(f"Yon {parca[1]!r} anlasilmadi — AL ya da SAT.")
    try:
        adet = float(parca[2].replace(",", "."))
    except ValueError:
        raise EmirHatasi(f"Adet {parca[2]!r} sayi degil.") from None
    if adet <= 0:
        raise EmirHatasi("Adet pozitif olmali.")
    fiyat = None
    if len(parca) > 3:
        try:
            fiyat = float(parca[3].replace(",", "."))
        except ValueError:
            raise EmirHatasi(f"Fiyat {parca[3]!r} sayi degil.") from None
        if fiyat <= 0:
            raise EmirHatasi("Fiyat pozitif olmali.")
    return {"sembol": sembol, "yon": yon, "adet": adet, "fiyat": fiyat,
            "tur": "LMT" if fiyat is not None else "MKT"}


def _conid(db, sembol: str) -> tuple[str, int | None]:
    r = db.query("""SELECT i.id, d.conid FROM instruments i
                    JOIN identities d ON d.instrument_id = i.id
                    WHERE UPPER(i.symbol) = ? AND d.conid IS NOT NULL
                      AND d.conid <> ''""", (sembol,))
    if not r:
        raise EmirHatasi(
            f"{sembol} icin IBKR kimligi (conid) yok.\n"
            "<i>Once: <code>python run.py collect --site ibkrkimlik</code></i>")
    if len({x["conid"] for x in r}) > 1:
        # Birden cok conid = belirsizlik. Emirde belirsizlik kabul edilemez.
        raise EmirHatasi(f"{sembol} icin birden fazla conid var — elle cozulmeli.")
    return str(r[0]["conid"]), int(r[0]["id"])


def _istek(s, db, coz: dict, hesap: str) -> tuple[E.EmirIstegi, int | None]:
    conid, iid = _conid(db, coz["sembol"])
    return E.EmirIstegi(hesap=hesap, conid=conid, yon=coz["yon"],
                        tur=coz["tur"], adet=coz["adet"],
                        fiyat=coz["fiyat"], sure="DAY"), iid


def _hesap(istemci: Istemci) -> str:
    hesaplar = Portfoy(istemci).hesaplar()
    if not hesaplar:
        raise EmirHatasi("IBKR hesabi bulunamadi.")
    if len(hesaplar) > 1:
        raise EmirHatasi("Birden fazla hesap var — secim mantigi yok.")
    return hesaplar[0].kimlik


def _ozet_metni(coz: dict, istek: E.EmirIstegi, k: OK.Onkontrol,
                kagit_mi: bool | None, on: "E.Onizleme | None" = None) -> str:
    yon_tr = "AL" if istek.yon == "BUY" else "SAT"
    satir = [
        "🧾 <b>IBKR EMIR ONAYI</b>",
        "",
        f"<b>{coz['sembol']}</b> — {yon_tr} {istek.adet:g} adet",
        f"Tur: {istek.tur}" + (f" @ {istek.fiyat}" if istek.fiyat else ""),
    ]
    if k.referans_fiyat:
        satir.append(f"Canli referans: {k.referans_fiyat:.2f} "
                     f"<i>({k.referans_kip})</i>")
    if on and on.tutar:
        # IBKR'NIN KENDI RAKAMI. Kendi carpimimizi gostermek yerine
        # kaynagi gosteriyoruz — komisyon dahil.
        satir.append(f"Tutar: {on.tutar}")
        if on.komisyon and on.komisyon != "—":
            satir.append(f"Komisyon: {on.komisyon}   Toplam: {on.toplam}")
    elif k.tahmini_tutar:
        satir.append(f"Tahmini tutar: {k.tahmini_tutar:,.2f} "
                     f"{k.para_birimi or ''}".rstrip())
    # HESABIN NE OLDUGU YAZIYOR. `.env`de "paper" yazmasi bir sey
    # kanitlamaz; buradaki ifade sunucudan gelen kanita dayaniyor.
    satir.append("Hesap: " + {True: "KAGIT", False: "🔴 CANLI",
                              None: "belirsiz"}[kagit_mi])
    if k.uyarilar:
        satir += ["", "⚠️ <b>Uyarilar</b>"] + [f"• {u}" for u in k.uyarilar]
    if k.engeller:
        satir += ["", "⛔️ <b>Engeller</b>"] + [f"• {e}" for e in k.engeller]
        satir += ["", "<i>Emir gonderilmeyecek.</i>"]
    else:
        satir += ["", f"<i>Onay {int(ONAY_OMRU_SN // 60)} dakika gecerli. "
                      "Butona basmadan once rakamlari kontrol et.</i>"]
    return "\n".join(satir)


# ----------------------------------------------------------------------
def hazirla(s, db, arg: str, sahip: str) -> tuple[str, dict | None]:
    """
    `/emir ...` -> (kullaniciya metin, onaya konacak veri | None).

    `None` doner = onay ISTENMIYOR (engel var ya da hata). Bu durumda
    buton GOSTERILMEZ; basilamayan bir butonu gostermek, engeli
    tavsiye gibi okutur.
    """
    coz = komut_coz(arg)
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        hesap = _hesap(istemci)
        istek, iid = _istek(s, db, coz, hesap)
        istek.dogrula()
        k = OK.dogrula(istemci, istek, db=db, sahip=sahip)
        # IBKR'YE KENDISI SOR: gondermeden once kabul eder mi, kac
        # komisyon keser. Tahmin etmektense kaynaktan sormak.
        on = E.onizle(istemci, istek) if k.gonderilebilir else None
        if on and on.hata:
            k.engeller.append(f"IBKR onizlemesi reddetti: {on.hata}")
        hesaplar = Portfoy(istemci).hesaplar()
        kagit = next((h.kagit_mi for h in hesaplar if h.kimlik == hesap), None)
    finally:
        istemci.kapat()

    metin = _ozet_metni(coz, istek, k, kagit, on)
    if not k.gonderilebilir:
        return metin, None

    # DEFTER SATIRI SIMDI ACILIYOR — gonderimden once.
    satir_id = db.emir_yaz(
        sahip=sahip, hesap=hesap, instrument_id=iid, conid=istek.conid,
        yon=istek.yon, tur=istek.tur, adet=istek.adet, fiyat=istek.fiyat,
        sure=istek.sure, para_birimi=k.para_birimi,
        referans_fiyat=k.referans_fiyat, referans_kip=k.referans_kip,
        parmak_izi=istek.parmak_izi(), durum="hazirlandi",
        not_="; ".join(k.uyarilar) or None)

    return metin, {
        "satir_id": satir_id, "sembol": coz["sembol"], "hesap": hesap,
        "conid": istek.conid, "yon": istek.yon, "tur": istek.tur,
        "adet": istek.adet, "fiyat": istek.fiyat, "sure": istek.sure,
        "parmak_izi": istek.parmak_izi(),
        "hazirlik_ts": datetime.now(timezone.utc).timestamp(),
    }


def _yeniden_istek(veri: dict) -> E.EmirIstegi:
    return E.EmirIstegi(hesap=veri["hesap"], conid=veri["conid"],
                        yon=veri["yon"], tur=veri["tur"], adet=veri["adet"],
                        fiyat=veri.get("fiyat"), sure=veri.get("sure", "DAY"))


def yurut(s, db, veri: dict, sahip: str) -> str:
    """
    Butona basildi. onkontrol YENIDEN kosar, sonra gonderilir.

    Her cikis yolu deftere yaziliyor — sessiz sonuc yok.
    """
    satir_id = veri.get("satir_id")
    yas = datetime.now(timezone.utc).timestamp() - float(veri.get("hazirlik_ts") or 0)
    if yas > ONAY_OMRU_SN:
        db.emir_guncelle(satir_id, durum="suresi_doldu")
        return (f"⏱ <b>Onay suresi doldu</b> ({yas / 60:.0f} dk).\n"
                "Referans fiyat eskidi — emri yeniden hazirla.")

    istek = _yeniden_istek(veri)
    # PARMAK IZI KONTROLU: onay dosyasi diskte duruyor ve arada
    # degistirilmis olabilir. Fis, ONAYLANAN emre bagli.
    if istek.parmak_izi() != veri.get("parmak_izi"):
        db.emir_guncelle(satir_id, durum="reddedildi",
                         not_="onay verisi degismis")
        return "⛔️ Onay verisi emirle uyusmuyor — emir gonderilmedi."

    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        k = OK.dogrula(istemci, istek, db=db, sahip=sahip)
        if not k.gonderilebilir:
            db.emir_guncelle(satir_id, durum="engellendi",
                             not_="; ".join(k.engeller))
            return ("⛔️ <b>Emir gonderilmedi</b> — onaydan sonra kosullar "
                    "degisti:\n" + "\n".join(f"• {e}" for e in k.engeller))

        fis = E.OnayFisi(parmak_izi=istek.parmak_izi(), kim=sahip)
        db.emir_guncelle(
            satir_id, onay_ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            onay_kim=sahip, durum="onaylandi",
            gonderim_ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))

        try:
            sonuc = E.gonder(istemci, istek, fis)
        except DurumBilinmiyorHatasi:
            # EN TEHLIKELI DAL. Emir ULASMIS OLABILIR; yeniden
            # gondermek CIFT EMIR demek. Mutabakat yapiliyor ve karar
            # insana birakiliyor.
            db.emir_guncelle(satir_id, durum="bilinmiyor")
            bulunan = E.mutabakat(istemci, istek)
            if bulunan:
                db.emir_guncelle(satir_id, durum="teyit_bekliyor",
                                 emir_id=str(bulunan[0].get("orderId") or ""),
                                 not_="zaman asimi; mutabakatta bulundu")
                return ("⚠️ <b>Baglanti koptu ama emir IBKR'ye ULASMIS.</b>\n"
                        f"Acik emir bulundu: {bulunan[0].get('orderId')}\n"
                        "<i>YENIDEN GONDERME.</i>")
            return ("⚠️ <b>Emrin durumu BILINMIYOR.</b>\n"
                    "Baglanti koptu; emir ulasmis olabilir ama acik "
                    "emirlerde gorunmuyor.\n"
                    "<i>Yeniden gondermeden once IBKR'den kontrol et. "
                    "Kayit deftere 'bilinmiyor' olarak yazildi.</i>")
        except (E.EmirReddedildi, IbkrHatasi) as e:
            db.emir_guncelle(satir_id, durum="reddedildi", not_=str(e))
            return f"⛔️ <b>Emir reddedildi</b>\n{e}"

        if isinstance(sonuc, E.OnayMesaji):
            # IBKR TEYIT ISTIYOR. Emir HENUZ CALISMIYOR.
            db.emir_guncelle(satir_id, durum="teyit_bekliyor",
                             onay_mesaji=sonuc.metin())
            return ("❓ <b>IBKR teyit istiyor</b> — emir HENUZ CALISMIYOR:\n\n"
                    f"<i>{sonuc.metin()}</i>\n\n"
                    "<b>Bu uyari bilerek bastirilmadi.</b> Devam etmek "
                    "istersen tekrar <code>/emir</code> ile hazirla ya da "
                    "IBKR arayuzunden teyit et.\n"
                    f"<code>messageId: {sonuc.id}</code>")

        db.emir_guncelle(satir_id, durum="kabul", emir_id=sonuc.emir_id,
                         ibkr_durum=sonuc.durum)
        return (f"✅ <b>Emir gonderildi</b>\n"
                f"{veri['sembol']} — {veri['yon']} {veri['adet']:g}\n"
                f"IBKR emir no: <code>{sonuc.emir_id}</code>\n"
                f"Durum: {sonuc.durum}")
    finally:
        istemci.kapat()


# ======================================================================
# IPTAL ve DEGISTIRME
# ======================================================================
IPTAL_TIP = "ibkr_iptal"
DEGISTIR_TIP = "ibkr_degistir"


def _acik_emri_bul(istemci: Istemci, emir_id: str) -> dict:
    """
    Acik emirler arasinda ara. BULUNAMAZSA sessiz gecilmez.

    Sebep: olmayan bir emri iptal etmeye calismak "iptal edildi" gibi
    okunabilir. Emir dolmus ya da zaten iptal edilmis olabilir ve bu
    kullanicinin BILMESI gereken bir sey.
    """
    for e in E.acik_emirler(istemci):
        if str(e.get("orderId") or "") == str(emir_id):
            return e
    raise EmirHatasi(
        f"{emir_id} numarali ACIK emir bulunamadi.\n"
        "<i>Dolmus, iptal edilmis ya da baska bir hesapta olabilir.</i>")


def _emir_satiri(e: dict) -> str:
    yon = "AL" if str(e.get("side") or "").upper() == "BUY" else "SAT"
    f = e.get("price")
    return (f"<b>{e.get('ticker') or e.get('conid')}</b> — {yon} "
            f"{e.get('totalSize') or e.get('remainingQuantity') or '?'} "
            f"{e.get('orderType') or ''}" + (f" @ {f}" if f else "") +
            f"\nDurum: {e.get('status') or '?'}  "
            f"No: <code>{e.get('orderId')}</code>")


def iptal_hazirla(s, db, emir_id: str, sahip: str) -> tuple[str, dict | None]:
    """Iptal ONAYA sunulur — model kendi basina iptal edemez."""
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        hesap = _hesap(istemci)
        e = _acik_emri_bul(istemci, emir_id)
    finally:
        istemci.kapat()
    metin = ("🗑 <b>EMIR IPTALI ONAYI</b>\n\n" + _emir_satiri(e) +
             "\n\n<i>IBKR iptali GARANTI ETMEZ: yanit 'istek alindi' "
             "demektir. Borsadaki bir emir (muzayede vb.) iptal "
             "edilemeyebilir.</i>")
    return metin, {"emir_id": str(emir_id), "hesap": hesap,
                   "ozet": _emir_satiri(e),
                   "hazirlik_ts": datetime.now(timezone.utc).timestamp()}


def iptal_yurut(s, db, veri: dict, sahip: str) -> str:
    yas = datetime.now(timezone.utc).timestamp() - float(veri.get("hazirlik_ts") or 0)
    if yas > ONAY_OMRU_SN:
        return f"⏱ Onay suresi doldu ({yas / 60:.0f} dk) — yeniden dene."
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        y = E.iptal(istemci, veri["hesap"], veri["emir_id"])
    except DurumBilinmiyorHatasi:
        return ("⚠️ <b>Iptal isteginin durumu BILINMIYOR.</b>\n"
                "<i>Acik emirlere bakip teyit et.</i>")
    except IbkrHatasi as e:
        return f"⛔️ Iptal edilemedi: {e}"
    finally:
        istemci.kapat()
    # IBKR'nin kendi uyarisi: bu "istek alindi", "iptal edildi" DEGIL.
    return ("✅ <b>Iptal istegi gonderildi</b>\n"
            f"{veri.get('ozet') or veri['emir_id']}\n"
            f"<code>{y.get('msg') or ''}</code>\n"
            "<i>Iptalin gerceklestigini acik emirlerden dogrula.</i>")


def degistir_hazirla(s, db, emir_id: str, adet: float | None,
                     fiyat: float | None, sahip: str) -> tuple[str, dict | None]:
    """
    Degistirme ONAYA sunulur.

    IBKR TUM alanlarin yeniden gonderilmesini istiyor, o yuzden mevcut
    emir OKUNUP uzerine yaziliyor — eksik alan, o alanin silinmesi degil
    REDDEDILME sebebi.
    """
    if adet is None and fiyat is None:
        raise EmirHatasi("Degisecek bir sey yok: adet ya da fiyat ver.")
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        hesap = _hesap(istemci)
        e = _acik_emri_bul(istemci, emir_id)
    finally:
        istemci.kapat()

    yeni_adet = adet if adet is not None else (
        e.get("totalSize") or e.get("remainingQuantity"))
    yeni_fiyat = fiyat if fiyat is not None else e.get("price")
    tur = str(e.get("origOrderType") or e.get("orderType") or "LMT").upper()
    if tur.startswith("LIMIT"):
        tur = "LMT"
    elif tur.startswith("MARKET"):
        tur = "MKT"
    govde = {
        "conid": int(e.get("conid")),
        "side": str(e.get("side") or "").upper(),
        "orderType": tur,
        "quantity": float(yeni_adet),
        "tif": str(e.get("timeInForce") or "DAY").upper(),
    }
    if tur == "LMT":
        if yeni_fiyat in (None, ""):
            raise EmirHatasi("Limit emri fiyatsiz olamaz.")
        govde["price"] = float(yeni_fiyat)

    metin = ("✏️ <b>EMIR DEGISIKLIGI ONAYI</b>\n\n"
             "<b>Once</b>\n" + _emir_satiri(e) + "\n\n"
             f"<b>Sonra</b>\nAdet: {govde['quantity']:g}"
             + (f"   Fiyat: {govde['price']}" if "price" in govde else "") +
             "\n\n<i>IBKR degistirmeyi yeni emirden FARKLI kurallara tabi "
             "tutabilir.</i>")
    return metin, {"emir_id": str(emir_id), "hesap": hesap, "govde": govde,
                   "ozet": _emir_satiri(e),
                   "hazirlik_ts": datetime.now(timezone.utc).timestamp()}


def degistir_yurut(s, db, veri: dict, sahip: str) -> str:
    yas = datetime.now(timezone.utc).timestamp() - float(veri.get("hazirlik_ts") or 0)
    if yas > ONAY_OMRU_SN:
        return f"⏱ Onay suresi doldu ({yas / 60:.0f} dk) — yeniden dene."
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        fis = E.OnayFisi(parmak_izi="", kim=sahip)
        try:
            sonuc = E.degistir(istemci, veri["hesap"], veri["emir_id"],
                               veri["govde"], fis)
        except DurumBilinmiyorHatasi:
            return ("⚠️ <b>Degisiklik istegi zaman asimina ugradi.</b>\n"
                    "Emir DEGISMIS OLABILIR — acik emirlere bak. "
                    "<i>Yeniden gonderme.</i>")
        except IbkrHatasi as e:
            return f"⛔️ Degistirilemedi: {e}"
    finally:
        istemci.kapat()
    if isinstance(sonuc, E.OnayMesaji):
        return ("❓ <b>IBKR teyit istiyor</b> — degisiklik HENUZ GECERLI DEGIL:\n\n"
                f"<i>{sonuc.metin()}</i>\n\n<code>messageId: {sonuc.id}</code>")
    return (f"✅ <b>Emir degistirildi</b>\n"
            f"No: <code>{sonuc.emir_id}</code>  Durum: {sonuc.durum}")
