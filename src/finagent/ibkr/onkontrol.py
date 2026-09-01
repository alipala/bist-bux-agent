"""
Emir oncesi dogrulama — ONAYDAN SONRA, GONDERIMDEN HEMEN ONCE.

NEDEN ONAYDAN SONRA TEKRAR
--------------------------
Insan onayi bir ANIN karari. Onayla gonderim arasinda saniyeler ya da
dakikalar gecebilir ve o arada fiyat oynar, bakiye degisir, ayni kagida
baska bir emir girmis olabilir. Onay "ne yapmak istedigimi" soyluyor;
bu modul "hala yapilabilir mi" diye soruyor. Ikisi ayri sorudur.

ENGEL ILE UYARI AYRI SEYLER
---------------------------
    ENGEL   emir GONDERILMEZ. Karar koda ait, cunku bu kosullarda
            gondermenin makul bir aciklamasi yok.
    UYARI   emir gonderilebilir ama insan GORMELI. Karar insana ait.

Her seyi engel yapmak katmani kullanilamaz kilar; her seyi uyari yapmak
korumayi sustan ibaret birakir.

PIYASA EMRI + GERCEK ZAMANLI OLMAYAN VERI = ENGEL
-------------------------------------------------
Limit emrinde fiyati SEN soyluyorsun; gecikmeli veri kotu bir limit
fiyatina yol acar ama ust sinir bellidir — uyari yetiyor.

Piyasa emrinde fiyati PIYASA soyluyor ve elindeki tek referans 15 dakika
eskiyse ne odeyecegini BILMIYORSUN demektir. Bu, uyarilabilir bir risk
degil; bilinmeyen bir tutari pesinen kabul etmektir.

AYNI KAGITTA ACIK EMIR VARSA ENGEL
----------------------------------
Cift emir bu isin en pahali kazasi ve en sessiz olani: iki emir de
gecerli gorunur, ikisi de dolar, pozisyon iki katina cikar. Ayni
conid+yonde acik emir varken yenisini gondermek icin once eskisini
gormek gerekir.

SATISTA ELDE OLANDAN FAZLASI ENGEL
----------------------------------
Aciga satis kapsam disi (`risk.allow_short_selling` kavraminin IBKR
tarafindaki karsiligi). Elde 3 lot varken 5 satmak, farkinda olmadan
acik pozisyon acmaktir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .emir import EmirIstegi
from .istemci import IbkrHatasi, Istemci
from .oturum import Oturum
from .piyasa import Kotasyon, Piyasa
from .portfoy import Portfoy

log = logging.getLogger(__name__)

# Limit fiyatinin canli fiyattan azami sapmasi. Asilirsa ENGEL:
# onaylanan fiyat artik piyasayla ilgisiz demektir.
AZAMI_KAYMA_PCT = 5.0

# Tek pozisyonun portfoydeki azami agirligi.
AZAMI_POZISYON_PCT = 10.0

# Emirden sonra elde kalmasi gereken asgari nakit orani.
NAKIT_REZERV_PCT = 10.0


@dataclass
class Onkontrol:
    durum: str = "gecti"                     # gecti | uyari | engellendi
    engeller: list[str] = field(default_factory=list)
    uyarilar: list[str] = field(default_factory=list)
    referans_fiyat: float | None = None
    referans_kip: str = ""
    tahmini_tutar: float | None = None
    para_birimi: str | None = None

    @property
    def gonderilebilir(self) -> bool:
        return not self.engeller

    def _sonlandir(self) -> "Onkontrol":
        self.durum = ("engellendi" if self.engeller
                      else "uyari" if self.uyarilar else "gecti")
        return self


def _kotasyon(istemci: Istemci, conid: str) -> Kotasyon | None:
    try:
        with Piyasa(istemci) as p:
            return p.kotasyon([conid]).get(str(conid))
    except IbkrHatasi as e:
        log.info("[ibkr] onkontrol kotasyon alamadi: %s", e)
        return None


def _seans_durumu(db, conid: str, simdi=None) -> dict | None:
    """
    Emrin gittigi borsa SU AN acik mi? BILINMIYORSA None — HUKUM YOK.

    NEDEN `identities.exchange` KULLANILMIYOR
    -----------------------------------------
    Olculdu (2026-09-01): conid tasiyan 501 enstrumanin 451'inde o alan
    BOS, dolu olanlar da bizim kendi adlarimiz. Ustelik `piyasa.py` onu
    kullanmayi ACIKCA reddediyor: o alan "sirket hangi borsada kote"
    sorusuna cevap veriyor, bizimki farkli — "BARIN fiyati hangi
    seansta olusuyor".

    NEDEN IBKR'NIN BORSA KODU KULLANILMIYOR
    ---------------------------------------
    `Kotasyon.borsa` IBKR'den geliyor ama bu kodlarin (NASDAQ, AEB,
    IBIS...) hicbiri bu depoda GOZLENMEDI — gateway kapaliyken
    dogrulanamazdi ve eslestirme tablosu TAHMIN olurdu.

    Tek mesru yol `piyasa.borsa_coz`: bilmiyorsa None diyor.
    Olculdu — izleme listesindeki (gercekci emir evreni) 44
    enstrumanin 42'sinde borsa cozuluyor (%95,5).

    TATIL TAKVIMI YOK: resmi tatilde seans saatleri "acik" gorunur,
    yani bu kontrol tatili KACIRIR. Yon GUVENLI — yanlislikla ENGEL
    koymaz, yalnizca uyarmayi atlar.
    """
    if db is None or not conid:
        return None
    try:
        from ..piyasa import borsa_coz, seans_durumlari
        satir = db.query(
            "SELECT instrument_id, venue FROM identities d "
            "JOIN instruments i ON i.id = d.instrument_id "
            "WHERE d.conid = ?", (str(conid),))
        if not satir:
            return None
        borsa = borsa_coz(db, satir[0]["instrument_id"], satir[0]["venue"])
        if not borsa:
            return None
        return next((d for d in seans_durumlari(simdi) if d["borsa"] == borsa),
                    None)
    except Exception as e:                            # noqa: BLE001
        # KONTROLUN ARIZASI EMRI ENGELLEMEZ. Bu bir EK koruma; kendi
        # hatasi yuzunden mesru bir emri durdurmasi, korumadan daha
        # buyuk bir zarar olurdu.
        log.info("[ibkr] seans durumu cozulemedi: %s: %s", type(e).__name__, e)
        return None


def dogrula(istemci: Istemci, istek: EmirIstegi, db=None, sahip: str | None = None,
            azami_kayma_pct: float = AZAMI_KAYMA_PCT,
            azami_pozisyon_pct: float = AZAMI_POZISYON_PCT,
            nakit_rezerv_pct: float = NAKIT_REZERV_PCT,
            simdi=None) -> Onkontrol:
    """
    Gonderimden hemen once calisir. VERI YAZMAZ, EMIR GONDERMEZ.

    `simdi` YALNIZCA TEST ICIN ve enjekte edilebilir olmasi SART: piyasa
    saati kontrolu duvar saatine bakiyor, sabitlenmezse test sabah gecip
    aksam kalirdi. Bu deponun olculmus tuzagi (`_b6_baz`, 2026-09-01).
    """
    k = Onkontrol()

    # --- oturum ---
    d = Oturum(istemci).durumu_oku(zorla=True)
    if not d.kullanilabilir:
        k.engeller.append(
            "IBKR oturumu kapali" if d.ulasilabilir else "gateway calismiyor")
        return k._sonlandir()
    if d.rakip_oturum:
        # Emir gonderilebilir ama oturum her an dusebilir; insan bilmeli.
        k.uyarilar.append("baska bir yerde acik IBKR oturumu var")

    # --- hesap KANITI ---
    p = Portfoy(istemci)
    try:
        hesaplar = p.hesaplar()
    except IbkrHatasi as e:
        k.engeller.append(f"hesap listesi alinamadi: {e}")
        return k._sonlandir()
    hedef = next((h for h in hesaplar if h.kimlik == istek.hesap), None)
    if hedef is None:
        # Emir, sunucunun TANIMADIGI bir hesaba gidiyor.
        k.engeller.append(f"hesap {istek.hesap} bu oturumda yok")
        return k._sonlandir()
    k.para_birimi = hedef.para_birimi

    # --- canli fiyat ve VERI KIPI ---
    q = _kotasyon(istemci, istek.conid)
    if q is None or not q.kullanilabilir:
        k.engeller.append("canli fiyat alinamadi — referanssiz emir gonderilmez")
        return k._sonlandir()
    k.referans_fiyat = q.son if q.son is not None else q.orta
    k.referans_kip = q.kip
    if not q.gercek_zamanli:
        if istek.tur == "MKT":
            # Ne odeyecegini bilmeden piyasa emri vermek, uyarilabilir
            # bir risk degil.
            k.engeller.append(
                f"piyasa emri + {q.kip} veri — ne odeyecegin BILINMIYOR")
        else:
            k.uyarilar.append(f"referans fiyat {q.kip} (gercek zamanli degil)")

    # --- PIYASA SAATI ---
    #
    # LIMIT EMRI ENGELLENMEZ ve bu bilincli: seans disinda limit emri
    # birakip acilisa kuyruga sokmak MESRU ve yaygin bir kullanim.
    # Engellemek katmani kullanilamaz kilardi.
    #
    # PIYASA EMRI ENGELLENIR — modulun kendi doktrini geregi. Kapali
    # piyasada MKT, acilis SEANSININ belirleyecegi bir fiyata razi
    # olmaktir; acilis boslugu (gap) gorulmeden kabul edilir. Bu,
    # "MKT + gercek zamanli olmayan veri = ENGEL" kuralinin ayni
    # gerekcesi: ne odeyecegini BILMIYORSUN.
    #
    # `q.kip == "donmus"` bunu KISMEN yakaliyordu ama ayni sey degil:
    # o VERININ durumunu soyluyor (abonelik yoksa seans acikken de
    # donmus gelir), bu ise BORSANIN durumunu. Ikisi ayrismali ki
    # kullaniciya giden cumle dogru olsun — "veri donmus" bir veri
    # sorunu gibi okunur, "piyasa kapali" ise EYLEME donusur.
    seans = _seans_durumu(db, istek.conid, simdi)
    if seans is not None and not seans["acik"]:
        ne_zaman = (f"{seans['borsa']} {seans['durum']}"
                    f" · seans {seans['seans']}")
        if istek.tur == "MKT":
            k.engeller.append(
                f"piyasa KAPALI ({ne_zaman}) — piyasa emri acilis "
                "fiyatindan doner, ne odeyecegin BILINMIYOR")
        else:
            k.uyarilar.append(
                f"piyasa KAPALI ({ne_zaman}) — emir acilisa kadar "
                "kuyrukta bekler")

    # --- kayma: onaylanan limit hala piyasayla ilgili mi ---
    if istek.tur == "LMT" and istek.fiyat and k.referans_fiyat:
        sapma = abs(istek.fiyat - k.referans_fiyat) / k.referans_fiyat * 100
        if sapma > azami_kayma_pct:
            k.engeller.append(
                f"limit {istek.fiyat} canli {k.referans_fiyat:.2f}'den "
                f"%{sapma:.1f} uzak (sinir %{azami_kayma_pct})")
        elif sapma > azami_kayma_pct / 2:
            k.uyarilar.append(f"limit canli fiyattan %{sapma:.1f} uzak")

    birim = istek.fiyat if istek.tur == "LMT" and istek.fiyat else k.referans_fiyat
    if birim:
        k.tahmini_tutar = birim * istek.adet

    # --- CIFT EMIR ---
    from .emir import mutabakat
    try:
        cakisan = mutabakat(istemci, istek)
    except IbkrHatasi:
        cakisan = []
    acik = [e for e in cakisan
            if str(e.get("status") or "").lower() not in
            ("filled", "cancelled", "canceled", "inactive")]
    if acik:
        kimlikler = ", ".join(str(e.get("orderId")) for e in acik[:3])
        k.engeller.append(
            f"ayni kagitta {len(acik)} acik emir var ({kimlikler}) — "
            "once onlara bak")

    # --- ALIM GUCU ---
    try:
        ozet = p.ozet(istek.hesap)
    except IbkrHatasi as e:
        k.uyarilar.append(f"alim gucu okunamadi: {e}")
        ozet = {}
    guc = (ozet.get("buyingpower") or (None, None))[0]
    netlik = (ozet.get("netliquidation") or (None, None))[0]
    if istek.yon == "BUY" and k.tahmini_tutar is not None and guc is not None:
        if k.tahmini_tutar > guc:
            k.engeller.append(
                f"alim gucu yetmiyor: {k.tahmini_tutar:.2f} > {guc:.2f}")
        elif netlik and k.tahmini_tutar > netlik * (1 - nakit_rezerv_pct / 100):
            k.uyarilar.append(
                f"emir sonrasi nakit rezervi %{nakit_rezerv_pct}'in altina duser")

    # --- YOGUNLASMA ve SATISTA ELDEKI ADET ---
    try:
        pozisyonlar = p.pozisyonlar(istek.hesap)
    except IbkrHatasi:
        pozisyonlar = []
    mevcut = next((x for x in pozisyonlar if str(x.conid) == str(istek.conid)), None)
    elde = mevcut.adet if mevcut and mevcut.adet else 0.0

    if istek.yon == "SELL" and istek.adet > elde:
        k.engeller.append(
            f"elde {elde:g} adet var, {istek.adet:g} satilmak isteniyor — "
            "aciga satis kapsam disi")

    if istek.yon == "BUY" and netlik and k.tahmini_tutar:
        mevcut_deger = (mevcut.piyasa_degeri or 0.0) if mevcut else 0.0
        oran = (mevcut_deger + k.tahmini_tutar) / netlik * 100
        if oran > azami_pozisyon_pct:
            k.uyarilar.append(
                f"emir sonrasi pozisyon agirligi %{oran:.1f} "
                f"(sinir %{azami_pozisyon_pct})")

    return k._sonlandir()
