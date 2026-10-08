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

KULLANIM = ("<b>Kullanim:</b> "
            "<code>/emir SEMBOL AL|SAT ADET [FIYAT] [DAY|GTC]</code>\n"
            "Ornek: <code>/emir NVDA AL 5 214.50 GTC</code>\n"
            "Fiyat yazilmazsa PIYASA emri olur — gercek zamanli veri "
            "yoksa engellenir.\n"
            "Sure yazilmazsa <code>ibkr.varsayilan_sure</code> ayari, o da "
            "yoksa <b>DAY</b> (seans sonunda duser).")


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
    # SURE (TIF) FIYATTAN SONRA — AMA FIYATSIZ DA YAZILABILIR.
    #
    # `/emir VRT SAT 0.38 288 GTC` ve `/emir VRT SAT 1 GTC` ikisi de
    # gecerli. Ayrim SAYI OLUP OLMAMASINDAN: bir jeton `SURELER`
    # kumesindeyse suredir, degilse fiyat olmak zorundadir. Konuma gore
    # ayirmak, fiyatsiz emirde "Fiyat 'GTC' sayi degil" gibi YANLIS bir
    # hata verirdi — sebebi yanlis soyleyen hata, hata yoklugundan kotu.
    kalan = list(parca[3:])
    sure = None
    if kalan and kalan[-1].upper() in E.SURELER:
        sure = kalan.pop().upper()
    fiyat = None
    if kalan:
        try:
            fiyat = float(kalan[0].replace(",", "."))
        except ValueError:
            # Yazim hatasini SURE olarak ta taniyalim ki mesaj dogru olsun.
            raise EmirHatasi(
                f"{kalan[0]!r} anlasilmadi — fiyat bir sayi, sure ise "
                f"{sorted(E.SURELER)} icinden biri olmali.") from None
        if fiyat <= 0:
            raise EmirHatasi("Fiyat pozitif olmali.")
    # ARTAN JETON SESSIZCE DUSMEZ.
    #
    # Ilk yazimda dusuyordu ve testi yazarken yakalandi: `SAT 1 288 GTS`
    # (yazim hatasi) sessizce fiyat=288, sure=None uretiyordu — yani
    # kullanici GTC yazdigini sanirken DAY emri gidiyordu. Emirde sessiz
    # varsayilan, yanlis varsayilandan kotudur.
    if kalan[1:]:
        raise EmirHatasi(
            f"Anlasilmayan {kalan[1:]!r} — sure {sorted(E.SURELER)} "
            "icinden biri olmali.")
    return {"sembol": sembol, "yon": yon, "adet": adet, "fiyat": fiyat,
            "sure": sure,
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


def varsayilan_sure(s) -> str:
    """
    Sure (TIF) verilmediginde kullanilacak deger.

    ONCELIK: emirde yazan > ayar > "DAY".

    NEDEN AYAR VAR: Ali 4 Eylul'de VRT 288 limit satisini girdi, emir
    seans sonunda `cancelled` oldu ve her sabah yeniden girmesi gerekti.
    Cozum icin IBKR mobil uygulamasinda "Time in Force = GTC" presetini
    kurdu — AMA O PRESET BU KANALI KAPSAMIYOR. Uygulama preseti yalnizca
    uygulamadan girilen emirlerin varsayilanidir; biz emri REST ucuna
    `tif` alanini ACIKCA yazarak gonderiyoruz (`EmirIstegi.govde`), yani
    ne yazarsak o gecerli olur. Presetin bize etkisi SIFIR.

    VARSAYILAN "DAY" KALIYOR. Ayari GTC yapmak her emri kalici hale
    getirir; unutulan bir emir haftalarca defterde bekler. Bu yuzden
    degisiklik BILINCLI olmali — ve secilen sure onay ekraninda HER
    ZAMAN yaziyor (bkz. `_ozet_metni`), sessiz varsayilan yok.
    """
    ham = str((s.get("ibkr.varsayilan_sure") if s else None) or "DAY").upper()
    if ham not in E.SURELER:
        # AYAR BOZUKSA SESSIZCE GTC'YE DUSULMEZ. Guvenli taraf DAY.
        log.warning("[emir] ibkr.varsayilan_sure=%r gecersiz — DAY kullanildi",
                    ham)
        return "DAY"
    return ham


def _istek(s, db, coz: dict, hesap: str) -> tuple[E.EmirIstegi, int | None]:
    conid, iid = _conid(db, coz["sembol"])
    # SURE ARTIK SABIT DEGIL. Onceden burada `sure="DAY"` YAZILIYDI ve
    # kullanicinin GTC vermesinin HICBIR yolu yoktu — oysa `EmirIstegi`,
    # dogrulama (`SURELER`), parmak izi, IBKR govdesi, defter satiri ve
    # onay fisi BASTAN BERI `sure` tasiyordu. Tek eksik bu satirdi:
    # kablo kacisinin bu depodaki en dar hali.
    return E.EmirIstegi(hesap=hesap, conid=conid, yon=coz["yon"],
                        tur=coz["tur"], adet=coz["adet"],
                        fiyat=coz["fiyat"],
                        sure=coz.get("sure") or varsayilan_sure(s)), iid


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
        *([f"<i>Stop: fiyat {istek.fiyat}'e inerse PİYASA emriyle satar "
           "(2N stop). Tetiklenene kadar hiçbir şey olmaz.</i>"]
          if istek.tur == "STP" else []),
        # SURE ONAY EKRANINDA YAZIYOR — ONCEDEN HIC YAZMIYORDU.
        #
        # Kullanici "seans sonunda dusecek mi, yoksa iptal edene kadar
        # duracak mi" sorusunun cevabini GORMEDEN onayliyordu. Para
        # ekranindaki sessiz alan, yanlis alandan daha tehlikelidir:
        # yanlis olan fark edilir, olmayan edilmez.
        f"Sure: <b>{istek.sure}</b>" + (
            " <i>(seans sonunda duser)</i>" if istek.sure == "DAY"
            else " <i>(iptal edilene kadar gecerli)</i>"
            if istek.sure == "GTC" else ""),
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
def hazirla(s, db, arg: str, sahip: str,
            kanal: str | None = None) -> tuple[str, dict | None]:
    """
    `/emir ...` -> (kullaniciya metin, onaya konacak veri | None).

    `None` doner = onay ISTENMIYOR (engel var ya da hata). Bu durumda
    buton GOSTERILMEZ; basilamayan bir butonu gostermek, engeli
    tavsiye gibi okutur.

    `kanal`: emrin hazirlandigi kapi (`emir_kanit.KANALLAR`) — deftere
    yazilir. Cagiran bilir; burada TAHMIN EDILMEZ.
    """
    return _hazirla(s, db, komut_coz(arg), sahip, kanal)


STOP_KULLANIM = ("Kullanım: <code>/stop SEMBOL</code> — IBKR'deki pozisyonun "
                 "TAMAMI için 2N stop emri (GTC) hazırlar; göndermez.")


def stop_coz(db, arg: str, sahip: str) -> dict:
    """
    `/stop SEMBOL` -> emir alanlari. Adet ve seviye KODDAN: adet IBKR'deki
    son anlik goruntunun TAMAMI, seviye kuralin kaydettigi 2N stop
    (`_pozisyon_stopu`: maliyet girise %5 icinde degilse BILINMIYOR).
    Kullanici sayi YAZMAZ — elle yazilan stop'ta bir basamak hatasi ya
    aninda satis (ustunde) ya da hic korumamak (cok altinda) demek.
    Stop sinavi 2N'i sabit birakti (`docs/stop-sinavi.md`).
    """
    from ..pulse.strateji import _pozisyon_stopu
    parca = (arg or "").split()
    if len(parca) != 1:
        raise EmirHatasi(STOP_KULLANIM)
    sembol = parca[0].upper()
    poz = [p for p in db.latest_positions("ibkr", sahip)
           if (p["symbol"] or "").upper() == sembol and p["quantity"]
           and float(p["quantity"]) > 0]
    if not poz:
        raise EmirHatasi(f"{sembol}: IBKR'de pozisyon yok (son anlık görüntü).")
    p = poz[0]
    stop = _pozisyon_stopu(db, p["instrument_id"], p["avg_cost"], sahip)
    if stop is None:
        raise EmirHatasi(
            f"{sembol}: 2N stop'u bilinmiyor — pozisyon kuralın kaydettiği bir "
            "girişle eşleşmiyor. Stop seviyesi UYDURULMAZ.")
    return {"sembol": sembol, "yon": "SELL", "adet": float(p["quantity"]),
            "fiyat": round(float(stop), 2), "sure": "GTC", "tur": "STP"}


def stop_hazirla(s, db, arg: str, sahip: str,
                 kanal: str | None = None) -> tuple[str, dict | None]:
    """`/stop SEMBOL` — `/emir` ile BIREBIR ayni yol (onkontrol, onizleme,
    defter, onay, yurutmede onkontrol YENIDEN)."""
    return _hazirla(s, db, stop_coz(db, arg, sahip), sahip, kanal)


def _politika_uyarilari(s, db, sahip: str, sembol: str, k) -> list[str]:
    """
    Yatirim politikasi (IPS, 9 Eki) — alim emrinde UYARI, engel DEGIL.
    Hicbir hata emir yolunu dusurmez; politika okunamazsa soylenir.
    """
    try:
        from ..analysis import ips
        from ..analysis.tema import _eur
        tutar = None
        if k.tahmini_tutar and k.para_birimi:
            tutar = _eur(db, float(k.tahmini_tutar), k.para_birimi)
        r = ips.alim_kontrolu(db, s, sahip, sembol, tutar)
        if r is None:
            return []
        return [f"Politika: {x}" for x in r["ihlaller"]]
    except Exception as e:                                # noqa: BLE001
        log.warning("[emir] politika denetlenemedi: %s", e)
        return [f"Politika denetlenemedi ({type(e).__name__}) — tavanlara kendin bak"]


def _hazirla(s, db, coz: dict, sahip: str,
             kanal: str | None = None) -> tuple[str, dict | None]:
    istemci = Istemci(s.get("ibkr.taban_url", None))
    try:
        hesap = _hesap(istemci)
        istek, iid = _istek(s, db, coz, hesap)
        istek.dogrula()
        k = OK.dogrula(istemci, istek, db=db, sahip=sahip)
        if istek.yon == "BUY":
            k.uyarilar.extend(_politika_uyarilari(s, db, sahip, coz["sembol"], k))
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
    satir_id = _emir_satiri_ac(db, sahip=sahip, hesap=hesap, instrument_id=iid,
                               conid=istek.conid, yon=istek.yon, tur=istek.tur,
                               adet=istek.adet, fiyat=istek.fiyat,
                               sure=istek.sure, para_birimi=k.para_birimi,
                               referans_fiyat=k.referans_fiyat,
                               referans_kip=k.referans_kip,
                               parmak_izi=istek.parmak_izi(),
                               durum="hazirlandi",
                               not_="; ".join(notlar) or None, kanal=kanal)

    return metin, {
        "satir_id": satir_id, "sembol": coz["sembol"], "hesap": hesap,
        "conid": istek.conid, "yon": istek.yon, "tur": istek.tur,
        "adet": istek.adet, "fiyat": istek.fiyat, "sure": istek.sure,
        "parmak_izi": istek.parmak_izi(),
        "hazirlik_ts": datetime.now(timezone.utc).timestamp(),
    }


def _emir_satiri_ac(db, **alanlar) -> int:
    """
    Emir satirini acar ve KANITINI toplar (`pulse/emir_kanit`).

    KANIT EMRI ASLA DURDURMAZ. Toplama yalnizca okuma + bir INSERT;
    basarisiz olursa satir yine acilir, eksiklik LOGA yazilir (sessiz
    degil) ve `scripts/emir_kanit_geriye.py` ile sonradan doldurulabilir.
    """
    satir_id = db.emir_yaz(**alanlar)
    try:
        from ..pulse.emir_kanit import topla
        topla(db, satir_id)
    except Exception as e:                                # noqa: BLE001
        log.warning("[emir] kanit toplanamadi (satir %s): %s", satir_id, e)
    return satir_id


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


def _degistirme_suresi(db, emir_id: str, acik: dict) -> tuple[str, str]:
    """
    Degistirmede gonderilecek SURE (tif) ve kaynagi: ("DAY", "defter").

    IBKR degistirmede ILK EMIRDEKI degerlerin AYNEN gonderilmesini
    istiyor. OLCULEN KUSUR (2026-10-02, ETN 255922803): sure IBKR'nin
    acik emir listesinden kopyalaniyordu; IBKR DAY emri orada "CLOSE"
    diye bildirdi, degistirme `tif: CLOSE` ile gitti ve IBKR "null time
    in force is not supported" diye reddetti. Ilk emir `tif: DAY` ile
    gitmisti — dogru deger BIZIM defterimizde (`emirler.sure`).

    Sira:
      (1) IBKR'nin bildirdigi deger, bizim GONDEREBILDIGIMIZ bir sureyse
          (`SURELER`) — canli emrin gercegi odur (emir sonradan IBKR
          uygulamasindan GTC'ye cevrilmis olabilir; defter bunu bilmez);
      (2) IBKR tanimadigimiz bir deger bildirirse (CLOSE) DEFTER — emri
          biz verdiysek ilk gonderdigimiz sureyi biliyoruz;
      (3) ikisi de yoksa DEGISTIRME GONDERILMEZ.
    Eskiden bilinmeyen sure "DAY" varsayiliyordu: GTC bir emir yalnizca
    adedi degisti diye gun emrine donusup aksam duserdi. Tahmin yerine
    durup kullaniciya iptal+yeni emir yolunu soylemek.
    """
    defter = None
    if db is not None:
        r = db.query("SELECT sure FROM emirler WHERE emir_id = ? "
                     "ORDER BY id DESC LIMIT 1", (str(emir_id),))
        if r and str(r[0]["sure"] or "").upper() in E.SURELER:
            defter = str(r[0]["sure"]).upper()
    ibkr = str(acik.get("timeInForce") or "").upper()
    if ibkr in E.SURELER:
        if defter and defter != ibkr:
            log.warning("[emir] %s: sure defterde %s, IBKR %s — IBKR'nin "
                        "(canli) degeri kullaniliyor", emir_id, defter, ibkr)
        return ibkr, "ibkr"
    if defter:
        if ibkr:
            log.info("[emir] %s: IBKR sureyi %r bildirdi (gonderilemez) — "
                     "defterdeki %s kullaniliyor", emir_id, ibkr, defter)
        return defter, "defter"
    log.warning("[emir] %s: sure bilinmiyor (IBKR %r, defterde yok) — "
                "degistirme GONDERILMEDI", emir_id, ibkr or None)
    raise EmirHatasi(
        f"Bu emrin süresi güvenle bilinmiyor (IBKR \"{ibkr or 'boş'}\" "
        "bildiriyor, emir defterimizde kaydı yok). Değiştirme "
        "<b>gönderilmedi</b> — yanlış süreyle IBKR ya reddeder ya da emri "
        "gün emrine çevirir.\n<i>Yol: emri iptal edip istediğin limitle "
        "yeni emir ver.</i>")


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
    # STOP EMRI BU YOLDAN DEGISTIRILMEZ: asagidaki govde fiyati YALNIZCA
    # LMT icin tasiyor. Bir STP buradan gecseydi stop fiyati govdeden
    # DUSERDI — IBKR reddetse de etmese de "stop'u degistirdim" cumlesi
    # yalan olurdu.
    if tur not in ("LMT", "MKT"):
        raise EmirHatasi(
            f"{tur} emrinin değiştirilmesi kapsam dışı — iptal edip "
            "<code>/stop SEMBOL</code> ile yeniden kur.")
    sure, sure_kaynak = _degistirme_suresi(db, emir_id, e)
    govde = {
        "conid": int(e.get("conid")),
        "side": str(e.get("side") or "").upper(),
        "orderType": tur,
        "quantity": float(yeni_adet),
        "tif": sure,
    }
    if tur == "LMT":
        if yeni_fiyat in (None, ""):
            raise EmirHatasi("Limit emri fiyatsiz olamaz.")
        govde["price"] = float(yeni_fiyat)

    metin = ("✏️ <b>EMIR DEGISIKLIGI ONAYI</b>\n\n"
             "<b>Once</b>\n" + _emir_satiri(e) + "\n\n"
             f"<b>Sonra</b>\nAdet: {govde['quantity']:g}"
             + (f"   Fiyat: {govde['price']}" if "price" in govde else "")
             + f"   Sure: <b>{govde['tif']}</b> "
             + f"<i>({'emir defterimizden' if sure_kaynak == 'defter' else 'IBKR bildirdi'})</i>" +
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
    """Sohbet araci — metin doner. Yapisal ozet icin `mutabakat_ozetli`."""
    return mutabakat_ozetli(s, db, sahip)[0]


def mutabakat_ozetli(s, db, sahip: str) -> tuple[str, dict]:
    """
    Defteri IBKR ile karsilastirir ve GUVENLE kapatilabilecekleri kapatir.

    METIN + YAPISAL OZET birlikte donuyor. Ozet ZAMANLANMIS kosu icin
    eklendi: metni ayristirarak "bir sey oldu mu" sorusunu cevaplamak,
    bicim degisince SESSIZCE bozulacak bir bagimlilik olurdu — bu depoda
    ayni hata `dolum_fiyat`in duz metne yazilmasinda bir kez yasandi.

    Ozetin en onemli alani `dolum_yazildi`: bu kosuda KAC satira gercek
    dolum fiyati islendi. Sifirdan buyuk olmasi, canli olcumun ilk kez
    mumkun hale geldigi andir.

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
    from ..ibkr.bulut import yerel_dustu

    istemci = Istemci(s.get("ibkr.taban_url", None))
    bulut_notu = None
    try:
        hesap = _hesap(istemci)
        satirlar = db.kapanmamis_emirler(sahip)
        # KAPANMIS emirlerin numaralari da geciriliyor: "defterde var mi"
        # sorusu KAPANMAMISLARLA cevaplanamaz.
        tum = {str(r["emir_id"]) for r in db.emirler(sahip, limit=500)
               if r["emir_id"]}
        kararlar = M.kos(istemci, satirlar, hesap=hesap, bilinen_nolar=tum)
    except Exception as e:                                # noqa: BLE001
        # CPGW DUSTU -> BULUT (Faz 6): yalnizca DOLUM KANITI. Baska hata
        # yedege gecirmez, oldugu gibi yukselir.
        if not yerel_dustu(e):
            raise
        from ..ibkr.bulut import islemler as bulut_islemler
        satirlar = db.kapanmamis_emirler(sahip)
        # "gerceklesti ama dolum verisi yok" satirlari da kurtarma adayi.
        satirlar = list(satirlar) + [r for r in db.emirler(sahip, limit=200)
                                     if r["durum"] == "gerceklesti"
                                     and r["dolum_fiyat"] is None and r["emir_id"]]
        kararlar, dogrulanamayan = M.kos_bulut(satirlar, bulut_islemler())
        bulut_notu = (f"☁️ Yerel ağ geçidi giriş istiyor ({type(e).__name__}); "
                      "mutabakat BULUTTAN yapıldı — yalnızca dolum kanıtı okundu."
                      + (f" {dogrulanamayan} emrin açık/iptal durumu doğrulanamadı "
                         "(bunun için yerel giriş gerekli); hiçbiri kapatılmadı."
                         if dogrulanamayan else ""))
    finally:
        istemci.kapat()

    bos_ozet = {"karar": 0, "yazilan": 0, "dolum_yazildi": 0,
                "cozulemeyen": 0, "defterde_yok": 0,
                "kanal": "bulut" if bulut_notu else "yerel"}
    if not kararlar:
        if bulut_notu:
            # BULUTTA "TEMIZ" DENEMEZ: acik emir listesi okunmadi.
            return (bulut_notu + "\nDolum kanıtı bulunan emir yok.", bos_ozet)
        return ("✅ <b>Mutabakat temiz</b> — defterde kapanmamis emir yok, "
                "IBKR'de de defterde olmayan acik emir yok.", bos_ozet)

    yazilan = dolum_yazildi = 0
    for k in kararlar:
        # DOLUM SAYACI YAZIMDAN ONCE: `emir_guncelle` sonrasi bakmak,
        # "bu kosuda mi yazildi yoksa zaten mi vardi" sorusunu
        # cevaplayamazdi.
        if k.alanlar.get("dolum_fiyat") is not None:
            dolum_yazildi += 1
        if k.satir_id > 0 and k.yeni_durum:
            db.emir_guncelle(k.satir_id, durum=k.yeni_durum, **k.alanlar)
            yazilan += 1
        elif k.satir_id > 0 and k.alanlar:
            db.emir_guncelle(k.satir_id, **k.alanlar)

    satir_metin = "\n".join(f"• {k.aciklama}" for k in kararlar)
    metin = ("🔍 <b>Emir defteri ↔ IBKR mutabakati</b>\n\n" + satir_metin)
    if bulut_notu:
        metin += "\n\n" + bulut_notu

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
    return metin, {"karar": len(kararlar), "yazilan": yazilan,
                   "dolum_yazildi": dolum_yazildi,
                   "cozulemeyen": len(acikta),
                   "defterde_yok": sum(1 for k in kararlar if k.satir_id <= 0),
                   "kanal": "bulut" if bulut_notu else "yerel"}


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
