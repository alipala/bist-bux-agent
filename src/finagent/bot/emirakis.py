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

import html
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


def _esc(x) -> str:
    """
    DIS METNI Telegram HTML'ine sokmadan once kacir.

    SAHADA ISIRDI (26 Agu): IBKR'nin "Confirm Mandatory Cap Price"
    uyarisi HAM HTML iceriyor (`<h4>...</h4>`) ve Telegram mesaji
    reddetti: `can't parse entities: Unsupported start tag "h4"`.
    Mesaj yalnizca `_gonder`in SADELESTIRME yedegi sayesinde ulasti —
    yani sansla. Yedek son care olmali, tasarim degil.

    Kural: IBKR'den (ya da herhangi bir dis kaynaktan) gelen her metin
    parcasi bicimlendirmeye girmeden once buradan gecer. Bizim yazdigimiz
    etiketler kacirilmaz, ONLARIN gonderdigi icerik kacirilir.
    """
    return html.escape(str(x if x is not None else ""))


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


def _baska_hesapta_var_mi(db, sembol: str, sahip: str) -> str | None:
    """
    Ayni sembol BASKA bir aracı kurumda tutuluyor mu?

    Emir yalnizca IBKR'ye gidebilir (BUX/Midas'in API'si yok), ama
    kullanici "NVDA'dan al" derken BUX pozisyonunu buyuttugunu
    sanabilir. Uyari bunu onlemek icin.
    """
    r = db.query("""SELECT DISTINCT p.account FROM positions p
                    JOIN instruments i ON i.id = p.instrument_id
                    WHERE UPPER(i.symbol) = ? AND p.sahip = ?
                      AND p.account <> 'ibkr' AND p.quantity > 0""",
                 (sembol.upper(), sahip))
    return ", ".join(sorted(x["account"] for x in r)) or None


def _ozet_metni(coz: dict, istek: E.EmirIstegi, k: OK.Onkontrol,
                kagit_mi: bool | None, on: "E.Onizleme | None" = None,
                baska_hesap: str | None = None) -> str:
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
    # HANGI ARACI KURUM OLDUGU ACIKCA YAZIYOR.
    #
    # NEDEN: Ali'nin BUX hesabinda 21 pozisyonun 16'si artik IBKR
    # conid'i tasiyor ve conid'ler ABD listesine (USD) cozuldu — BUX
    # pozisyonlari ise EUR. "NVDA'dan al" dendiginde emir IBKR'ye,
    # NASDAQ'a, DOLARLA gider; kullanici BUX pozisyonuna ekleme
    # yaptigini sanabilir. Emir sadece IBKR'ye gidebilir (BUX'un API'si
    # yok) ama bu, kullanicinin BILMESI gereken bir sey.
    satir.append("Aracı kurum: <b>IBKR</b>  ·  Hesap: "
                 + {True: "KAGIT", False: "🔴 CANLI",
                    None: "belirsiz"}[kagit_mi])
    if baska_hesap:
        satir.append(f"⚠️ Ayni sembol <b>{baska_hesap}</b> hesabinda da var — "
                     "bu emir ORAYA DEGIL, IBKR'ye gider.")
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

    metin = _ozet_metni(coz, istek, k, kagit, on,
                        _baska_hesapta_var_mi(db, coz["sembol"], sahip))
    if not k.gonderilebilir:
        return metin, None

    # ONIZLEMENIN KOMISYONU DEFTERE YAZILIYOR.
    #
    # `/whatif` ZATEN cagriliyordu (yukarida `on`) ve sonucu yalnizca
    # kullaniciya GOSTERILIYORDU — kayit altina alinmiyordu. Oysa
    # "gondermeden once IBKR ne dedi" ile "gerceklesince ne oldu"
    # karsilastirmasi, dolum sapmasinin komisyon ayagi.
    # Tahmin edilmiyor, SORULUYOR — ve artik sorulanin cevabi da
    # saklaniyor.
    notlar = list(k.uyarilar)
    if on and (on.komisyon or on.tutar):
        notlar.append(f"onizleme: tutar {on.tutar or '—'} "
                      f"komisyon {on.komisyon or '—'} "
                      f"toplam {on.toplam or '—'}")

    # DEFTER SATIRI SIMDI ACILIYOR — gonderimden once.
    satir_id = db.emir_yaz(
        sahip=sahip, hesap=hesap, instrument_id=iid, conid=istek.conid,
        yon=istek.yon, tur=istek.tur, adet=istek.adet, fiyat=istek.fiyat,
        sure=istek.sure, para_birimi=k.para_birimi,
        referans_fiyat=k.referans_fiyat, referans_kip=k.referans_kip,
        parmak_izi=istek.parmak_izi(), durum="hazirlandi",
        not_="; ".join(notlar) or None)

    return metin, {
        "satir_id": satir_id, "sembol": coz["sembol"], "hesap": hesap,
        "conid": istek.conid, "yon": istek.yon, "tur": istek.tur,
        "adet": istek.adet, "fiyat": istek.fiyat, "sure": istek.sure,
        "parmak_izi": istek.parmak_izi(),
        "hazirlik_ts": datetime.now(timezone.utc).timestamp(),
    }


def _askidaki_emir(istemci: Istemci, istek: E.EmirIstegi) -> str | None:
    """
    Teyit bekleyen emrin IBKR numarasi.

    Olculdu (2026-08-26): teyit mesaji `order_id` TASIMIYOR ama emir
    IBKR'de `status: "Inactive"` olarak DURUYOR. Numarayi simdi almazsak
    kullanici o emri iptal etmek istediginde elinde bir kimlik olmaz.
    """
    try:
        for e in E.mutabakat(istemci, istek):
            if str(e.get("status") or "").lower() in ("inactive", "presubmitted"):
                return str(e.get("orderId") or "") or None
    except IbkrHatasi:
        pass
    return None


def _teyit_istegi(mesaj: "E.OnayMesaji", satir_id, veri: dict,
                  emir_no: str | None) -> tuple[str, dict]:
    """
    IBKR'nin uyarisini GOSTERIP ikinci onayi ister.

    ONCEKI SURUM BURADA CIKMAZ BIRAKIYORDU: "IBKR arayuzunden teyit et"
    diyordu. Uyariyi bastirmamanin butun anlami karari INSANA birakmak;
    insana EVET DEME YOLU vermezsek koruma degil engel olur. Sahada
    ilk gercek emirde ortaya cikti.
    """
    metin = ("❓ <b>IBKR teyit istiyor</b> — emir HENUZ CALISMIYOR\n\n"
             f"<i>{_esc(mesaj.metin())}</i>\n\n"
             "<b>Bu uyari bilerek bastirilmadi</b> — IBKR'nin kendi "
             "koruması. Devam etmek istersen onayla.")
    if emir_no:
        metin += f"\n\nIBKR emir no: <code>{emir_no}</code> (su an Inactive)"
    return metin, {"mesaj_id": mesaj.id, "satir_id": satir_id,
                   "sembol": veri.get("sembol"), "emir_no": emir_no,
                   "hazirlik_ts": datetime.now(timezone.utc).timestamp()}


def teyit_yurut(s, db, veri: dict, sahip: str) -> "str | tuple[str, dict]":
    """
    `/iserver/reply/{id}` — IBKR'nin uyarisini onaylar.

    ZINCIRLENEBILIR: teyit yanitinda BASKA bir uyari gelebilir; o zaman
    yine onay istenir. Sonsuz donguye girmez cunku her tur INSANIN
    butonuna bagli.
    """
    satir_id = veri.get("satir_id")
    yas = datetime.now(timezone.utc).timestamp() - float(veri.get("hazirlik_ts") or 0)
    if yas > ONAY_OMRU_SN:
        db.emir_guncelle(satir_id, durum="suresi_doldu",
                         not_="teyit onayi eskidi")
        return (f"⏱ <b>Teyit suresi doldu</b> ({yas / 60:.0f} dk).\n"
                "Emir IBKR'de <i>Inactive</i> olarak durabilir — "
                "<i>acik emirlere bak</i>.")
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        try:
            sonuc = E.teyit_et(istemci, veri["mesaj_id"])
        except DurumBilinmiyorHatasi:
            db.emir_guncelle(satir_id, durum="bilinmiyor",
                             not_="teyit zaman asimi")
            return ("⚠️ <b>Teyidin durumu BILINMIYOR.</b>\n"
                    "<i>Acik emirlere bakip kontrol et; yeniden "
                    "gonderme.</i>")
        except IbkrHatasi as e:
            db.emir_guncelle(satir_id, not_=f"teyit reddedildi: {e}")
            return f"⛔️ <b>Teyit edilemedi</b>\n{_esc(e)}"
    finally:
        istemci.kapat()

    if isinstance(sonuc, E.OnayMesaji):
        # USTUNE YAZMA, EKLE: IBKR ikinci (ve ucuncu) uyariyi
        # zincirleyebiliyor ve her biri ayri bir onay kaydi.
        db.emir_uyari_ekle(satir_id, sonuc.metin(), sonuc.id,
                           sonuc.mesaj_kodlari)
        return _teyit_istegi(sonuc, satir_id, veri, veri.get("emir_no"))

    db.emir_guncelle(satir_id, durum="kabul", emir_id=sonuc.emir_id,
                     ibkr_durum=sonuc.durum)
    return (f"✅ <b>Emir gonderildi</b>\n"
            f"{veri.get('sembol') or ''} — IBKR emir no: "
            f"<code>{sonuc.emir_id}</code>\nDurum: {sonuc.durum}")


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
                    "degisti:\n" + "\n".join(f"• {_esc(e)}"
                                              for e in k.engeller))

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
            return f"⛔️ <b>Emir reddedildi</b>\n{_esc(e)}"

        if isinstance(sonuc, E.OnayMesaji):
            # IBKR TEYIT ISTIYOR. Emir HENUZ CALISMIYOR — ama IBKR'de
            # 'Inactive' olarak DURUYOR. Onu bulup deftere yaziyoruz ki
            # numarasi kaybolmasin.
            askidaki = _askidaki_emir(istemci, istek)
            db.emir_guncelle(satir_id, durum="teyit_bekliyor",
                             emir_id=askidaki or None)
            db.emir_uyari_ekle(satir_id, sonuc.metin(), sonuc.id,
                               sonuc.mesaj_kodlari)
            return _teyit_istegi(sonuc, satir_id, veri, askidaki)

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
TEYIT_TIP = "ibkr_teyit"
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


def _defter_satiri(db, emir_id: str) -> int | None:
    """
    IBKR emir numarasindan DEFTER satirini bulur.

    Iptal/degistirme akislari IBKR'ye yaziyor ama DEFTERE yazmiyordu:
    `iptal_yurut` `db` parametresini aliyor ve HIC KULLANMIYORDU. Yani
    Ali emri iptal ettiginde defterde satir `kabul` olarak kaliyordu —
    tam da dun kapattigimiz "defter IBKR'den ayrisiyor" sinifinin
    kendisi, bu sefer YAZMA yolunda. Asili satir zararsiz degil:
    `onkontrol` "ayni kagitta acik emir var" diye yeni emri engelliyor.
    """
    if db is None or not emir_id:
        return None
    r = db.query("SELECT id FROM emirler WHERE emir_id = ? "
                 "ORDER BY id DESC LIMIT 1", (str(emir_id),))
    return int(r[0]["id"]) if r else None


def _emir_satiri(e: dict) -> str:
    yon = "AL" if str(e.get("side") or "").upper() == "BUY" else "SAT"
    f = e.get("price")
    return (f"<b>{_esc(e.get('ticker') or e.get('conid'))}</b> — {yon} "
            f"{_esc(e.get('totalSize') or e.get('remainingQuantity') or '?')} "
            f"{_esc(e.get('orderType') or '')}"
            + (f" @ {_esc(f)}" if f else "") +
            f"\nDurum: {_esc(e.get('status') or '?')}  "
            f"No: <code>{_esc(e.get('orderId'))}</code>")


def iptal_hazirla(s, db, emir_id: str, sahip: str) -> tuple[str, dict | None]:
    """Iptal ONAYA sunulur — model kendi basina iptal edemez."""
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        hesap = _hesap(istemci)
        e = _acik_emri_bul(istemci, emir_id)
    finally:
        istemci.kapat()
    # HENUZ GONDERILMEMIS EMIR IPTAL EDILEMEZ.
    #
    # Olculdu (2026-08-26): teyit bekleyen emir `status: "Inactive"`,
    # `order_ccp_status: "Pending Submit"` ve durum ucu 404 doner —
    # yani IBKR'de bir EMIR KAYDI yok, yalnizca kuyrukta bir bilet var.
    # DELETE cagrisi "Order is inactive" ile 400 veriyor. Kullaniciyi
    # ham hatayla birakmak yerine ne oldugunu ve cikis yolunu soyluyoruz.
    ccp = str(e.get("order_ccp_status") or "").lower()
    if str(e.get("status") or "").lower() == "inactive" or "pending" in ccp:
        raise EmirHatasi(
            "⏸ <b>Bu emir HENUZ GONDERILMEDI</b> — IBKR'nin teyidini "
            "bekliyor (<code>Inactive / Pending Submit</code>).\n\n"
            "IBKR canli olmayan bir emri IPTAL ETMIYOR: ortada iptal "
            "edilecek bir emir yok.\n\n"
            "<b>Iki yol var:</b>\n"
            "• <i>teyit et</i> dersen IBKR'nin uyarisini onaylarim, emir "
            "canliya gecer — sonra iptal edilebilir.\n"
            "• Dokunmazsan gun sonunda kendiliginden duser (DAY emri, "
            "hic gonderilmedi).")

    metin = ("🗑 <b>EMIR IPTALI ONAYI</b>\n\n" + _emir_satiri(e) +
             "\n\n<i>IBKR iptali GARANTI ETMEZ: yanit 'istek alindi' "
             "demektir. Borsadaki bir emir (muzayede vb.) iptal "
             "edilemeyebilir.</i>")
    return metin, {"emir_id": str(emir_id), "hesap": hesap,
                   "ozet": _emir_satiri(e),
                   "satir_id": _defter_satiri(db, str(emir_id)),
                   "hazirlik_ts": datetime.now(timezone.utc).timestamp()}


def iptal_yurut(s, db, veri: dict, sahip: str) -> str:
    yas = datetime.now(timezone.utc).timestamp() - float(veri.get("hazirlik_ts") or 0)
    if yas > ONAY_OMRU_SN:
        return f"⏱ Onay suresi doldu ({yas / 60:.0f} dk) — yeniden dene."
    satir_id = veri.get("satir_id") or _defter_satiri(db, veri.get("emir_id"))
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        y = E.iptal(istemci, veri["hesap"], veri["emir_id"])
    except DurumBilinmiyorHatasi:
        if satir_id:
            db.emir_guncelle(satir_id, durum="bilinmiyor",
                             not_="iptal istegi zaman asimi")
        return ("⚠️ <b>Iptal isteginin durumu BILINMIYOR.</b>\n"
                "<i>Acik emirlere bakip teyit et.</i>")
    except IbkrHatasi as e:
        if satir_id:
            db.emir_guncelle(satir_id, not_=f"iptal reddedildi: {e}")
        return f"⛔️ Iptal edilemedi: {_esc(e)}"
    finally:
        istemci.kapat()

    # DEFTERE `iptal_edildi` DEGIL `iptal_istendi` YAZILIYOR.
    #
    # IBKR'nin kendi uyarisi: yanit "istek alindi" demek, "emir iptal
    # edildi" DEGIL. Borsadaki bir emir (acilis muzayedesi vb.) iptal
    # edilemeyebilir — ve piyasa acilirken iptal ile dolum YARISIR.
    # `iptal_edildi` yazmak, dogrulanmamis bir sonucu olgu diye kaydetmek
    # olurdu. Kesinlesmesini mutabakat yapiyor.
    if satir_id:
        db.emir_guncelle(satir_id, durum="iptal_istendi",
                         not_=f"iptal istegi gonderildi ({sahip})")
    return ("✅ <b>Iptal istegi gonderildi</b>\n"
            f"{veri.get('ozet') or veri['emir_id']}\n"
            f"<code>{_esc(y.get('msg') or '')}</code>\n"
            "<i>Bu 'istek alindi' demek, 'iptal edildi' DEGIL. Deftere "
            "<b>iptal_istendi</b> yazdim; kesinlesince mutabakat "
            "kapatir.</i>")


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
                   "satir_id": _defter_satiri(db, str(emir_id)),
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
            return f"⛔️ Degistirilemedi: {_esc(e)}"
    finally:
        istemci.kapat()
    satir_id = veri.get("satir_id") or _defter_satiri(db, veri.get("emir_id"))
    if isinstance(sonuc, E.OnayMesaji):
        # MESAJ KIMLIGI DEFTERE YAZILIYOR — yoksa bu dal CIKMAZ olurdu.
        # Ayni kusur emir gonderiminde yasandi: teyit kimligi yalnizca
        # ekranda kalinca emir ne teyit ne iptal edilebiliyordu.
        if satir_id:
            db.emir_uyari_ekle(satir_id, sonuc.metin(), sonuc.id,
                               sonuc.mesaj_kodlari)
            db.emir_guncelle(satir_id, durum="teyit_bekliyor")
        return ("❓ <b>IBKR teyit istiyor</b> — degisiklik HENUZ GECERLI "
                f"DEGIL:\n\n<i>{_esc(sonuc.metin())}</i>\n\n"
                "<i>'teyit et' dersen onayina sunarim.</i>")
    if satir_id:
        g = veri.get("govde") or {}
        yeni = {"ibkr_durum": sonuc.durum,
                "not_": f"degistirildi ({sahip})"}
        if g.get("price") is not None:
            yeni["fiyat"] = g["price"]
        if g.get("quantity") is not None:
            yeni["adet"] = g["quantity"]
        db.emir_guncelle(satir_id, **yeni)
    return (f"✅ <b>Emir degistirildi</b>\n"
            f"No: <code>{_esc(sonuc.emir_id)}</code>  "
            f"Durum: {_esc(sonuc.durum)}")


def bekleyen_teyit_hazirla(s, db, sahip: str,
                           emir_no: str | None = None) -> tuple[str, dict | None]:
    """
    DEFTERDE asili duran teyidi kurtarir.

    Sahada gerekti (2026-08-26): `messageId` yalnizca onay DOSYASINDA
    yasiyordu, dosya tuketilince emir ne teyit ne iptal edilebiliyordu.
    Artik ID deftere yaziliyor ve buradan yeniden onaya sunulabiliyor.
    """
    satirlar = [r for r in db.emirler(sahip, durum="teyit_bekliyor", limit=20)
                if r["mesaj_id"]]
    if emir_no:
        satirlar = [r for r in satirlar if str(r["emir_id"] or "") == str(emir_no)]
    if not satirlar:
        raise EmirHatasi("Teyit bekleyen (ve mesaj kimligi kayitli) emir yok.")
    if len(satirlar) > 1:
        liste = ", ".join(f"{r['symbol']}#{r['emir_id']}" for r in satirlar)
        raise EmirHatasi(f"Birden fazla teyit bekliyor: {liste} — hangisi?")
    r = satirlar[0]
    metin = ("❓ <b>Bekleyen IBKR teyidi</b> — emir HENUZ CALISMIYOR\n\n"
             f"<i>{_esc(r['onay_mesaji'] or '')}</i>\n\n"
             f"{_esc(r['symbol'] or '')} — {r['yon']} {r['adet']:g} @ {r['fiyat']}\n"
             + (f"IBKR emir no: <code>{_esc(r['emir_id'])}</code>\n"
                if r["emir_id"] else "")
             + "\nOnaylarsan emir canliya gecer.")
    return metin, {"mesaj_id": r["mesaj_id"], "satir_id": r["id"],
                   "sembol": r["symbol"], "emir_no": r["emir_id"],
                   "hazirlik_ts": datetime.now(timezone.utc).timestamp()}


# ----------------------------------------------------------------------
def mutabakat_calistir(s, db, sahip: str) -> str:
    """
    Defteri IBKR ile karsilastirir ve GUVENLE kapatilabilecekleri kapatir.

    NE YAPAR / NE YAPMAZ — ayrim bilincli:
      YAPAR   IBKR'yi okur, defteri duzeltir, farki RAPOR EDER.
      YAPMAZ  IBKR'ye tek bir yazma cagrisi bile gondermez. Iptal ve
              teyit para hareketidir; bu depoda para hareketi yalnizca
              onay butonundan gecer. Mutabakatin "temizlik yapiyorum"
              diye emir iptal etmesi, tam da onay mimarisini delen sey
              olurdu.

    Defter YALNIZCA IBKR bir seyi KANITLADIGINDA kapaniyor:
    dolum goruldu, IBKR 'cancelled/expired' dedi, ya da hem acik
    emirlerde hem islem gecmisinde iz YOK. "Acik emirlerde gorunmuyor"
    tek basina YETMEZ — dolmus emir de gorunmez.
    """
    from ..ibkr import mutabakat as M

    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        hesap = _hesap(istemci)
        satirlar = db.kapanmamis_emirler(sahip)
        # KAPANMIS emirlerin numaralari da geciriliyor: "defterde var mi"
        # sorusu KAPANMAMISLARLA cevaplanamaz.
        tum = {str(r["emir_id"]) for r in db.emirler(sahip, limit=500)
               if r["emir_id"]}
        kararlar = M.kos(istemci, satirlar, hesap=hesap, bilinen_nolar=tum)
    finally:
        istemci.kapat()

    if not kararlar:
        return ("✅ <b>Mutabakat temiz</b> — defterde kapanmamis emir yok, "
                "IBKR'de de defterde olmayan acik emir yok.")

    yazilan = 0
    for k in kararlar:
        if k.satir_id > 0 and k.yeni_durum:
            db.emir_guncelle(k.satir_id, durum=k.yeni_durum, **k.alanlar)
            yazilan += 1
        elif k.satir_id > 0 and k.alanlar:
            db.emir_guncelle(k.satir_id, **k.alanlar)

    satir_metin = "\n".join(f"• {k.aciklama}" for k in kararlar)
    metin = ("🔍 <b>Emir defteri ↔ IBKR mutabakati</b>\n\n" + satir_metin)

    if yazilan:
        metin += f"\n\n<i>{yazilan} defter satiri guncellendi.</i>"

    teyitlik = [k for k in kararlar if k.eylem == "teyit"]
    iptallik = [k for k in kararlar if k.eylem == "iptal"]
    oneri = []
    if teyitlik:
        oneri.append("<i>askida kalan icin: <b>teyit et</b> de — emir "
                     "canliya gecer, sonra iptal edilebilir.</i>")
    if iptallik:
        no = iptallik[0].emir_no or ""
        oneri.append(f"<i>iptal icin: <b>{_esc(no)} numarali emri iptal et</b> "
                     "de — onayina sunarim.</i>")
    if oneri:
        metin += "\n\n" + "\n".join(oneri)

    # KAPATILAMAYANLARI GIZLEME. Bir satirin acik kalmasi, mutabakatin
    # basarisizligi degil DURUSTLUGUDUR — ama gorulmezse unutulur.
    acikta = [k for k in kararlar
              if k.satir_id > 0 and not k.yeni_durum
              and k.kod in ("S7_celiski", "S9_zaman_asimi_eslesmedi",
                            "S10_dogrulanamadi")]
    if acikta:
        metin += (f"\n\n⚠️ <b>{len(acikta)} satir cozulemedi</b> ve BILEREK "
                  "acik birakildi — uydurma bir duruma yazmaktansa acik "
                  "kalsin.")

    sapma = dolum_sapmasi_metni(db, sahip)
    if sapma:
        metin += "\n\n" + sapma
    return metin


def dolum_sapmasi_metni(db, sahip: str, limit: int = 5) -> str | None:
    """
    ISTENEN vs GERCEKLESEN dolum tablosu. Dolmus emir yoksa None.

    SAF-ISH: yalnizca okuyor. Sayilar `_tr` uzerinden gecmiyor cunku
    burasi <pre> blogu ve HIZALAMA bilgi tasiyor — ama ondalik AYIRAC
    yine mesajin geri kaliyla ayni olsun diye `_tr` KULLANILIYOR.
    """
    from ..pulse.runner import _tr

    satirlar = db.dolum_sapmalari(sahip, limit=limit)
    if not satirlar:
        return None
    L = [f"{'SEMBOL':<8}{'ISTENEN':>10}{'DOLUM':>10}{'SAPMA':>9}{'KOM %':>8}"]
    for r in satirlar:
        # HESAPLANAMAYANI SIFIR YAZMA: MKT emrinde referans fiyat yok
        # ve "%0,00" yazmak, emri KUSURSUZ dolmus gosterirdi.
        sapma = f"%{_tr(r['sapma_pct'])}" if r["sapma_pct"] is not None else "—"
        kom = f"%{_tr(r['komisyon_pct'])}" if r["komisyon_pct"] is not None else "—"
        L.append(f"{str(r['symbol'] or '?'):<8}"
                 f"{(_tr(r['istenen']) if r['istenen'] is not None else 'MKT'):>10}"
                 f"{_tr(r['gerceklesen']):>10}{sapma:>9}{kom:>8}")
    return ("📏 <b>Dolum sapmasi</b> (istenen → gerceklesen)\n"
            "<pre>" + _esc("\n".join(L)) + "</pre>\n"
            "<i>Komisyon yuzdesi emrin KENDI tutarina gore. Kucuk emirde "
            "yuksek cikmasi kuralin degil BOYUTUN sonucu.</i>")
