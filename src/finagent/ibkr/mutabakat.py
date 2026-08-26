"""
Emir defteri ile IBKR'yi KARSILASTIRIR — ve aralarindaki farki kapatir.

NEDEN VAR
---------
Defterde bir satir `teyit_bekliyor` olarak asili kaldi (26 Agu, emir
296869242) ve IBKR'nin acik emirlerinde YOKTU. Yani defter "bu emir
duruyor" diyordu, IBKR "boyle bir emir yok" diyordu. Ikisi ayristi ve
kimse fark etmedi; fark eden Ali oldu.

Asili satir zararsiz degil: `onkontrol` "ayni kagitta acik emir var"
diye YENI emri ENGELLIYOR. Yani olu bir defter satiri, canli bir emri
bloke ediyor.

EN TEHLIKELI KISAYOL — VE BU MODUL ONU YAPMIYOR
-----------------------------------------------
"Acik emirlerde yoksa iptal et" kurali YANLIS ve pahali:

    DOLMUS bir emir de acik emirlerde GORUNMEZ.

O kurali kodlasaydik, gerceklesmis bir alimi "dustu" diye kapatirdik;
pozisyon portfoye hic girmezdi ve elimizde olmayan bir hisseyi olmayan
sanardik. Bu, deponun en kotu hata sinifinin (veri VARKEN "yok" demek)
para tarafindaki karsiligi.

Bu yuzden kural su: **yoklugu KANIT saymiyoruz.** Bir satiri olu ilan
etmeden once IKI soru soruluyor:
    1. Durum ucu ne diyor? (`Filled` / `Cancelled` / `Inactive` ...)
    2. Islem gecmisinde bu emrin dolumu VAR MI?
Ikisi de okunamiyorsa satir KAPATILMAZ; "dogrulanamadi" denir ve
karar insana birakilir.

BU MODUL IBKR'YE YAZMAZ
-----------------------
Ne iptal gonderir ne teyit. Yalnizca DEFTERI duzeltir ve gerekiyorsa
"su emri iptal etmek/teyit etmek ister misin" diye ONERIR. Para
hareketi her zaman onay butonundan gecer — mutabakatin kendisi de
otomatik bir emir eylemine donusemez.

KARAR TABLOSU tamamen SAF: `karar()` ag gormuyor, girdisi dict.
Boylece on bir senaryonun hepsi taklitle sinaniyor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .emir import acik_emirler, durum as emir_durumu
from .istemci import IbkrHatasi, Istemci

log = logging.getLogger(__name__)

# Defterde KAPANMIS sayilan durumlar — mutabakat bunlara dokunmaz.
BITMIS = {"gerceklesti", "dustu", "iptal_edildi", "reddedildi",
          "engellendi", "suresi_doldu"}

# IBKR'nin "bu emir artik yok" dedigi statuler.
OLU_STATU = {"cancelled", "canceled", "expired", "rejected", "apicancelled"}

# "Emir canli ve borsada" statuleri.
CANLI_STATU = {"submitted", "presubmitted", "pendingsubmit", "pendingcancel"}

# Hic onaylanmamis emir bileti. Ne calisir ne iptal edilir.
ASKI_STATU = {"inactive", "warn"}

# `hazirlandi` satiri onaylanmadan bu kadar beklerse dusurulur.
HAZIRLIK_OMRU_SN = 3600.0


def _sayi(x: Any) -> float | None:
    try:
        if x is None or (isinstance(x, str) and not x.strip()):
            return None
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _statu(*adaylar: Any) -> str:
    for a in adaylar:
        s = str(a or "").strip().lower().replace(" ", "").replace("_", "")
        if s and s not in ("0", "none"):
            return s
    return ""


@dataclass
class Karar:
    """
    Bir defter satiri icin varilan sonuc.

    `yeni_durum is None` -> DEFTERE YAZMA. Bu bilincli bir secenek:
    "bilmiyorum" da gecerli bir sonuctur ve uydurma bir duruma
    yazilmasindan iyidir.
    """

    satir_id: int
    kod: str                       # senaryo kimligi (testler bunu tutuyor)
    aciklama: str                  # kullaniciya gidecek TEK cumle
    yeni_durum: str | None = None
    alanlar: dict = field(default_factory=dict)
    eylem: str | None = None       # None | "teyit" | "iptal"
    emir_no: str | None = None


def _dolum(acik: dict | None, dstat: dict | None) -> tuple[float | None, float | None]:
    """(dolan, toplam) — hangisi okunabiliyorsa oradan."""
    dolan = toplam = None
    if dstat:
        dolan = _sayi(dstat.get("cum_fill"))
        toplam = _sayi(dstat.get("total_size")) or _sayi(dstat.get("size"))
    if acik:
        if dolan is None:
            dolan = _sayi(acik.get("filledQuantity"))
        if toplam is None:
            toplam = (_sayi(acik.get("totalSize"))
                      or _sayi(acik.get("remainingQuantity")))
    return dolan, toplam


def karar(satir: dict, acik: dict | None, dstat: dict | None,
          dolum_var: bool | None, simdi_ts: float,
          liste_guvenilir: bool = True) -> Karar:
    """
    Girdiler:

        satir            defter satiri (dict)
        acik             IBKR acik emir kaydi, yoksa None
        dstat            durum ucunun yaniti, okunamadiysa None
        dolum_var        islem gecmisinde bu emrin dolumu var mi?
                         None = BAKILAMADI (bilinmiyor — 'yok' DEGIL)
        simdi_ts         duvar saati
        liste_guvenilir  acik emir listesi OKUNDU ve DOLU muydu?
                         False ise "listede yok" bir sey KANITLAMAZ.
    """
    sid = int(satir["id"])
    no = str(satir.get("emir_id") or "") or None
    defter = str(satir.get("durum") or "")
    sembol = satir.get("symbol") or satir.get("conid") or "?"

    # ---------- EMIR NUMARASI YOK ----------
    if not no:
        if defter == "bilinmiyor":
            if acik:
                return Karar(sid, "S8_zaman_asimi_eslesti",
                             f"{sembol}: baglanti kopmustu, IBKR'de "
                             f"{acik.get('orderId')} numarali eslesen emir "
                             "bulundu — defter guncellendi.",
                             yeni_durum="kabul",
                             alanlar={"emir_id": str(acik.get("orderId") or ""),
                                      "ibkr_durum": _statu(acik.get("status")),
                                      "not_": "mutabakat: conid+yon esleşmesi"},
                             emir_no=str(acik.get("orderId") or ""))
            return Karar(sid, "S9_zaman_asimi_eslesmedi",
                         f"{sembol}: gonderim zaman asimina ugramisti ve "
                         "IBKR'de esleşen acik emir YOK. Dolmus da olabilir; "
                         "defteri kendiligimden kapatmiyorum.")
        if defter in ("hazirlandi", "onaylandi"):
            yas = simdi_ts - _ts(satir.get("olusma_ts"), simdi_ts)
            if yas > HAZIRLIK_OMRU_SN:
                return Karar(sid, "S12_hazirlik_bayat",
                             f"{sembol}: onaylanmadan bekleyen hazirlik "
                             "satiri dusuruldu.",
                             yeni_durum="suresi_doldu",
                             alanlar={"not_": "mutabakat: onaylanmadi"})
            return Karar(sid, "S13_hazirlik_taze",
                         f"{sembol}: hazirlik satiri, henuz taze.")
        if defter == "teyit_bekliyor":
            return Karar(sid, "S3b_askida_numarasiz",
                         f"{sembol}: IBKR teyit bekliyor ama emir numarasi "
                         "yok. Teyit edilirse canliya gecer.",
                         eylem="teyit" if satir.get("mesaj_id") else None)
        return Karar(sid, "S0_dokunulmadi", f"{sembol}: degisiklik yok.")

    # ---------- EMIR NUMARASI VAR ----------
    st = _statu(dstat.get("order_status") if dstat else None,
                acik.get("status") if acik else None)
    ccp = _statu(dstat.get("order_ccp_status") if dstat else None,
                 acik.get("order_ccp_status") if acik else None)
    dolan, toplam = _dolum(acik, dstat)

    # 1) DOLUM — her seyden once. Dolmus emir iptal EDILMEZ.
    if st == "filled" or (dolan is not None and toplam and dolan >= toplam):
        return Karar(sid, "S1_gerceklesti",
                     f"{sembol}: emir {no} GERCEKLESTI"
                     + (f" ({dolan:g} adet)" if dolan else "") +
                     ". Portfoye islenmesi icin ayrica onayina sunacagim.",
                     yeni_durum="gerceklesti",
                     alanlar={"ibkr_durum": st or "Filled",
                              "not_": "mutabakat: dolum tespit edildi"},
                     emir_no=no)

    # 2) KISMI DOLUM — ne olu ne tam. Kalan hala canli olabilir.
    if dolan is not None and dolan > 0 and toplam and dolan < toplam:
        return Karar(sid, "S11_kismi",
                     f"{sembol}: emir {no} KISMI doldu ({dolan:g}/{toplam:g}). "
                     "Kalan kisim hala acik.",
                     yeni_durum="kismi",
                     alanlar={"ibkr_durum": st or "PartiallyFilled"},
                     eylem="iptal", emir_no=no)

    # 3) IBKR "bu emir oldu" diyor.
    if st in OLU_STATU:
        return Karar(sid, "S5_olu",
                     f"{sembol}: emir {no} IBKR'de <b>{st}</b> — defter "
                     "kapatildi. Iptal cagrisi GONDERILMEDI, gerek yok.",
                     yeni_durum="dustu",
                     alanlar={"ibkr_durum": st,
                              "not_": f"mutabakat: IBKR statusu {st}"},
                     emir_no=no)

    # 4) ASKIDA — gonderilmemis bilet. Iptal EDILEMEZ, once teyit gerekir.
    if st in ASKI_STATU or "pendingsubmit" in ccp:
        return Karar(sid, "S3_askida",
                     f"{sembol}: emir {no} ASKIDA (<code>{st or 'inactive'}</code>) "
                     "— IBKR'nin teyidini bekliyor, bu haliyle ne calisir ne "
                     "iptal edilebilir.",
                     alanlar={"ibkr_durum": st or "Inactive"},
                     eylem="teyit" if satir.get("mesaj_id") else None,
                     emir_no=no)

    # 5) CANLI.
    if st in CANLI_STATU:
        if acik:
            return Karar(sid, "S2_canli",
                         f"{sembol}: emir {no} CANLI (<code>{st}</code>).",
                         yeni_durum=None if defter == "kabul" else "kabul",
                         alanlar={"ibkr_durum": st}, eylem="iptal", emir_no=no)
        # Durum ucu "canli" diyor ama acik emirlerde yok. CELISKI.
        return Karar(sid, "S7_celiski",
                     f"⚠️ {sembol}: emir {no} icin durum ucu "
                     f"<code>{st}</code> diyor ama acik emirlerde YOK. "
                     "Celiskiyi cozemedim, deftere DOKUNMADIM.",
                     emir_no=no)

    # 6) IBKR'DE HICBIR IZ YOK. Once dolum sorusu — 'yok' KANIT DEGIL.
    if acik is None and not dstat:
        if not liste_guvenilir:
            # LISTE BOSTU YA DA OKUNAMADI. "Listede yok" ancak liste
            # GUVENILIRSE bir sey soyler. Sahada bu ayrimin yoklugu
            # CANLI bir emri "dustu" diye kapatti (26 Agu, 2141314594).
            return Karar(sid, "S15_liste_guvenilmez",
                         f"⚠️ {sembol}: emir {no} — IBKR'nin acik emir "
                         "listesi bos/okunamaz geldi, yoklugu KANIT "
                         "saymiyorum. Defter degistirilmedi.",
                         emir_no=no)
        if dolum_var is True:
            return Karar(sid, "S4_gecmiste_dolmus",
                         f"{sembol}: emir {no} acik emirlerde yok ama islem "
                         "gecmisinde DOLUMU VAR — gerceklesmis.",
                         yeni_durum="gerceklesti",
                         alanlar={"not_": "mutabakat: islem gecmisinde bulundu"},
                         emir_no=no)
        if dolum_var is None:
            return Karar(sid, "S10_dogrulanamadi",
                         f"⚠️ {sembol}: emir {no} IBKR'de gorunmuyor ama "
                         "islem gecmisi OKUNAMADI. Dolmus olabilir — defteri "
                         "kapatmiyorum.",
                         emir_no=no)
        return Karar(sid, "S6_iz_yok",
                     f"{sembol}: emir {no} IBKR'de yok, islem gecmisinde de "
                     "dolumu yok — defter kapatildi.",
                     yeni_durum="dustu",
                     alanlar={"not_": "mutabakat: IBKR'de bulunamadi, "
                                      "dolum da yok"},
                     emir_no=no)

    return Karar(sid, "S0_dokunulmadi", f"{sembol}: emir {no} — durum "
                 f"okunamadi (<code>{st or '?'}</code>), dokunulmadi.",
                 emir_no=no)


def _ts(deger: Any, varsayilan: float) -> float:
    try:
        return datetime.fromisoformat(str(deger)).replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return varsayilan


# ----------------------------------------------------------------------
def islemler(istemci: Istemci) -> list[dict] | None:
    """
    Islem gecmisi. `None` = OKUNAMADI — BOS LISTE ILE AYNI SEY DEGIL.

    Bu ayrim modulun tamaminin dayandigi sey: bos liste "dolum yok"
    demektir, `None` "bilmiyorum" demektir ve ikincisinde defter
    KAPATILMAZ.
    """
    try:
        y = istemci.get("/iserver/account/trades")
    except IbkrHatasi as e:
        log.warning("[ibkr] islem gecmisi okunamadi: %s", e)
        return None
    if isinstance(y, list):
        return [r for r in y if isinstance(r, dict)]
    if isinstance(y, dict) and isinstance(y.get("trades"), list):
        return [r for r in y["trades"] if isinstance(r, dict)]
    return None


def _dolum_var_mi(gecmis: list[dict] | None, emir_no: str,
                  conid: Any) -> bool | None:
    if gecmis is None:
        return None
    for t in gecmis:
        if str(t.get("order_ref") or t.get("orderId") or "") == str(emir_no):
            return True
    # Emir numarasi tasimayan kayitlar icin conid ikinci kapi. Tek basina
    # KANIT sayilmiyor cunku ayni kagitta baska islem olabilir — bu yuzden
    # yalnizca POZITIF yonde kullaniliyor.
    for t in gecmis:
        if conid and str(t.get("conid") or "") == str(conid):
            return True
    return False


def kos(istemci: Istemci, satirlar: list, simdi_ts: float | None = None,
        hesap: str | None = None) -> list[Karar]:
    """
    Kapanmamis her defter satiri icin bir `Karar` uretir. IBKR'ye YAZMAZ.

    Islem gecmisi EN FAZLA BIR KEZ cekiliyor ve yalnizca gerekirse —
    `/iserver/trades` 5 saniyede bir istekle sinirli.
    """
    simdi_ts = simdi_ts if simdi_ts is not None else datetime.now(
        timezone.utc).timestamp()
    try:
        acik = acik_emirler(istemci, hesap)
    except IbkrHatasi as e:
        log.warning("[ibkr] mutabakat: acik emirler okunamadi: %s", e)
        acik = None

    def _acik_bul(satir) -> dict | None:
        if acik is None:
            return None
        no = str(satir["emir_id"] or "") if satir["emir_id"] else ""
        if no:
            for e in acik:
                if str(e.get("orderId") or "") == no:
                    return e
            return None
        for e in acik:
            if (str(e.get("conid") or "") == str(satir["conid"])
                    and str(e.get("side") or "").upper() == str(satir["yon"])):
                return e
        return None

    # BOS LISTE "acik emir yok" DEGIL, "bilmiyorum". `acik_emirler`
    # zaten bir kez yeniden soruyor; buna ragmen bossa listeye
    # GUVENILMEZ ve hicbir satir yoklugu yuzunden kapatilmaz.
    liste_guvenilir = bool(acik)
    if not liste_guvenilir:
        log.warning("[ibkr] mutabakat: acik emir listesi guvenilmez "
                    "(bos ya da okunamadi) — hicbir satir yokluk "
                    "gerekcesiyle KAPATILMAYACAK")

    gecmis: list[dict] | None = None
    gecmis_cekildi = False
    kararlar: list[Karar] = []

    for satir in satirlar:
        s = dict(satir)
        if s.get("durum") in BITMIS:
            continue
        bulunan = _acik_bul(s)
        dstat = None
        if s.get("emir_id") and hesap:
            try:
                dstat = emir_durumu(istemci, hesap, str(s["emir_id"])) or None
            except IbkrHatasi:
                dstat = None            # 404 dahil — "yok" demek DEGIL

        dolum = None
        if (s.get("emir_id") and bulunan is None and not dstat
                and liste_guvenilir):
            if not gecmis_cekildi:
                gecmis, gecmis_cekildi = islemler(istemci), True
            dolum = _dolum_var_mi(gecmis, str(s["emir_id"]), s.get("conid"))

        kararlar.append(karar(s, bulunan, dstat, dolum, simdi_ts,
                              liste_guvenilir))

    # Defterde OLMAYAN acik emirler: Ali IBKR arayuzunden girmis olabilir.
    # Sessizce yok sayilmiyor — bizim yazmadigimiz bir emir de gercek para.
    if acik:
        bilinen = {str(dict(x).get("emir_id") or "") for x in satirlar}
        for e in acik:
            no = str(e.get("orderId") or "")
            if no and no not in bilinen:
                kararlar.append(Karar(
                    -1, "S14_defterde_yok",
                    f"ℹ️ {e.get('ticker') or e.get('conid')}: IBKR'de "
                    f"{no} numarali acik emir var ama BIZIM defterde yok "
                    "(disaridan girilmis olabilir).",
                    eylem="iptal", emir_no=no))
    return kararlar
