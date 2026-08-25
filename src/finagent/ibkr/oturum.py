"""
IBKR brokerage oturumunun bakimi — bot surecinin icinde yasar.

UC KATMAN, UCU DE AYRI KIRILIR
------------------------------
    1. Gateway sureci      java, localhost:5001
    2. Kimlik dogrulama    tarayicidan ELLE giris (gunde bir)
    3. Brokerage oturumu   /iserver uclari yalnizca buna bagli

Ikisi acik ucuncusu kapaliyken /portfolio CALISIR ama /iserver BOS DONER.
Disaridan bakinca "veri yok" gibi gorunur — bu deponun en kotu hata sinifi.
Bu yuzden durum UC AYRI BAYRAK olarak tutuluyor, tek bir "baglandi mi"
sorusuna indirgenmiyor.

IKI SAAT VAR VE IKISI AYNI SEY DEGIL — OLCULDU 2026-08-25
---------------------------------------------------------
    Hareketsizlik  ~5-6 dakika istek gelmezse oturum duser. /tickle bunu
                   sifirlar. IBKR "yaklasik 60 saniyede bir" diyor; biz
                   50 saniyelik esikle bakiyoruz (dongu ~20 sn'de bir
                   dondugu icin pratikte ~61 saniyede bir tikliyor).

    SSO omru       /tickle yanitindaki `ssoExpires`. ~10 dakikalik bir
                   jeton omru; duvar saatiyle 1:1 geri sayiyor ve
                   /tickle onu SIFIRLAMIYOR. Sifira yaklasinca
                   KENDILIGINDEN yenileniyor:

                       23:27:31  103 ->  42 sn  (-61)
                       23:28:32   42 -> 587 sn  (+545)   <-- yenilendi
                       23:29:33  587 -> 526 sn  (-61)
                       23:30:34  526 -> 465 sn  (-61)

                   Yenilenme sirasinda oturum KESINTIYE UGRAMADI
                   (`authenticated` true kaldi). Yani bu saatin sifira
                   inmesi bir ariza belirtisi DEGIL.

Bu davranis TAHMIN EDILEMEZDI: ilk iki okumada deger dusuyordu ve makul
yorum "her istek sifirlamiyor, demek ki oturum 10 dakikada olecek" idi —
yanlis olurdu. Once olcum (bkz. yavasligin sebebini olc: tahmin isabetim
0/4).

Gunluk ELLE giris bunlardan AYRI ve cok daha uzun bir saat (IBKR: 24 saat,
bolgesel gece yarisinda sifirlanir).

KENDILIGINDEN TOPARLANMA — AMA KULLANICIYI KAPI DISARI ETMEDEN
--------------------------------------------------------------
IBKR diyor ki: `connected: true` + `authenticated: false` ise oturum zaman
asimina ugramistir ve `/iserver/auth/ssodh/init` YENIDEN KURAR. Yani
6 dakikalik dusus ELLE GIRIS GEREKTIRMEZ, kendiliginden toparlanir.

`compete` parametresi baska brokerage oturumlarini DUSURUR. Varsayilanimiz
`false` ve bu bilincli: Ali tarayicidan Client Portal'a girdiginde bot
onu sessizce disari atmamali. Bunun yerine `competing: true` gorulur,
kullaniciya SOYLENIR ve karar ona birakilir. Sessizce birbirini dusuren
iki istemci, teshis edilmesi en zor ariza turudur.

BOTU ASLA DUSURMEZ
------------------
`tik()` hicbir kosulda disari istisna sizdirmaz. IBKR'nin kapali olmasi
Telegram botunun cevap vermemesi anlamina gelemez.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .istemci import (
    HizHatasi,
    IbkrHatasi,
    Istemci,
    UlasilamadiHatasi,
    YetkiHatasi,
)

log = logging.getLogger(__name__)

# IBKR "yaklasik 60 saniyede bir" diyor, zaman asimi ~5-6 dakika.
# 50 saniye: bir tikleme kacsa bile ikincisi hala penceredeymis olur.
TIKLEME_ARALIGI_SN = 50.0

# Durum sorgusu tiklemeden daha seyrek — /iserver/auth/status'un ilan
# edilmis bir hiz siniri yok ama bedava da degil.
DURUM_ARALIGI_SN = 50.0


@dataclass
class Durum:
    """Uc katmanin bayraklari. `bilinmiyor` ile `kapali` AYRI seylerdir."""

    ulasilabilir: bool = False        # 1. katman: gateway ayakta mi
    kimlik_dogrulandi: bool = False   # 2. katman: giris yapildi mi
    bagli: bool = False               # IBKR arkaucuna baglanti
    rakip_oturum: bool = False        # baska yerde acik oturum var
    oturum_bitis_sn: int | None = None
    mesaj: str = ""
    olcum_ts: float = field(default_factory=time.monotonic)

    @property
    def kullanilabilir(self) -> bool:
        """`/iserver` uclari kullanilabilir mi? TEK dogru soru budur."""
        return self.ulasilabilir and self.kimlik_dogrulandi and self.bagli


class Oturum:
    """
    Bot dongusunden `tik()` cagrilir; gerisini kendisi ayarlar.

    `bildir(anahtar, mesaj)` Bekci'nin imzasiyla ayni — ayni bildirim
    turunu tekrarlamama sorumlulugu ORADA, burada degil.
    """

    def __init__(self, istemci: Istemci, bildir=None, yaris: bool = False):
        self.istemci = istemci
        self._bildir = bildir
        self._yaris = yaris                 # ssodh/init -> compete
        self.durum = Durum()
        self._son_tikleme = 0.0
        self._son_durum = 0.0
        # `ssoExpires` olcumunun onceki degeri. `self.durum`da TUTULAMAZ:
        # `durumu_oku()` her cagrida taze bir Durum kuruyor.
        self._onceki_bitis: int | None = None
        self._onceki_kullanilabilir: bool | None = None
        self._onceki_rakip = False

    # ------------------------------------------------------------------
    def durumu_oku(self, zorla: bool = False) -> Durum:
        """
        `/iserver/auth/status` -> Durum. Hata durumunda da Durum doner;
        istisna FIRLATMAZ.
        """
        simdi = time.monotonic()
        if not zorla and (simdi - self._son_durum) < DURUM_ARALIGI_SN:
            return self.durum
        self._son_durum = simdi

        d = Durum()
        try:
            y = self.istemci.post("/iserver/auth/status", {})
            d.ulasilabilir = True
            if isinstance(y, dict):
                d.kimlik_dogrulandi = bool(y.get("authenticated"))
                d.bagli = bool(y.get("connected"))
                d.rakip_oturum = bool(y.get("competing"))
                d.mesaj = str(y.get("message") or "")
        except YetkiHatasi:
            # 401 = gateway AYAKTA ama giris yapilmamis. Bu, "ulasilamadi"
            # ile karistirilmamali: cozumu farkli (biri java baslatmak,
            # digeri tarayicidan giris).
            d.ulasilabilir = True
            d.mesaj = "giris yapilmamis"
        except UlasilamadiHatasi as e:
            d.mesaj = str(e)
        except (HizHatasi, IbkrHatasi) as e:
            d.ulasilabilir = True
            d.mesaj = str(e)

        # `oturum_bitis_sn` DEVREDILIYOR. auth/status bu degeri dondurmuyor
        # (yalnizca /tickle donduruyor), ve her sorguda taze bir Durum
        # kuruldugu icin devredilmezse SILINIRDI. Ilk surumde tam bu oldu:
        # olcum satiri hic basilmadi cunku karsilastirilacak "onceki"
        # deger her seferinde None'a donuyordu.
        d.oturum_bitis_sn = self.durum.oturum_bitis_sn
        self.durum = d
        return d

    # ------------------------------------------------------------------
    def kur(self) -> bool:
        """
        Brokerage oturumunu yeniden kur (`/iserver/auth/ssodh/init`).

        YALNIZCA `connected` iken anlamlidir: IBKR'ye gore bu durum
        "oturum zaman asimina ugradi ama arkauc baglantisi duruyor"
        demek ve elle giris GEREKTIRMEZ.
        """
        try:
            y = self.istemci.post(
                "/iserver/auth/ssodh/init",
                {"publish": True, "compete": bool(self._yaris)},
            )
        except IbkrHatasi as e:
            log.info("[ibkr] oturum kurulamadi: %s", e)
            return False
        ok = bool(isinstance(y, dict) and y.get("authenticated"))
        log.info("[ibkr] ssodh/init -> %s", "ACIK" if ok else "kurulamadi")
        if ok:
            self.durumu_oku(zorla=True)
        return ok

    # ------------------------------------------------------------------
    def _tikle(self) -> None:
        """
        `/tickle` — hareketsizlik saatini sifirlar.

        Yan urun: `ssoExpires`. Her tiklemede gecen duvar saatiyle
        birlikte logluyoruz — iki saatin gercekten nasil davrandigini
        TAHMIN degil OLCUM ile ogrenmek icin.
        """
        # Olcum durumu OTURUM NESNESINDE tutuluyor, `self.durum`da degil:
        # `durumu_oku()` her cagrida taze bir Durum kuruyor ve oradaki
        # deger silinirdi.
        onceki = self._onceki_bitis
        gecen = time.monotonic() - self._son_tikleme if self._son_tikleme else 0.0
        try:
            y = self.istemci.post("/tickle", {})
        except IbkrHatasi as e:
            log.debug("[ibkr] tickle basarisiz: %s", e)
            return
        self._son_tikleme = time.monotonic()

        if isinstance(y, dict):
            ms = y.get("ssoExpires")
            if isinstance(ms, (int, float)):
                yeni = int(ms / 1000)
                self.durum.oturum_bitis_sn = yeni
                self._onceki_bitis = yeni
                # HER tikleme loglaniyor, yalnizca fark olustugunda degil:
                # "hic satir yok" durumu "tickle calismiyor" ile "tickle
                # calisiyor ama fark yok"u ayirt ettirmiyordu — ve ilk
                # surumde tam bu yuzden bir hata gozden kacti.
                if onceki is None:
                    log.info("[ibkr] tickle: ssoExpires %d sn (ilk olcum)", yeni)
                else:
                    log.info("[ibkr] tickle: ssoExpires %d -> %d sn "
                             "(gecen %.0f sn, fark %+d)",
                             onceki, yeni, gecen, yeni - onceki)
            # /tickle yaniti auth/status'u de tasiyor — bedava tazeleme.
            iserver = y.get("iserver") or {}
            durum = iserver.get("authStatus") if isinstance(iserver, dict) else None
            if isinstance(durum, dict):
                self.durum.kimlik_dogrulandi = bool(durum.get("authenticated"))
                self.durum.bagli = bool(durum.get("connected"))
                self.durum.rakip_oturum = bool(durum.get("competing"))
                self.durum.ulasilabilir = True

    # ------------------------------------------------------------------
    def tik(self) -> None:
        """
        Bot dongusunun cagirdigi tek metot. ISTISNA SIZDIRMAZ.

        Dongu ~20 saniyede bir doner; kendi araligimizi kendimiz tutuyoruz.
        """
        try:
            self._tik()
        except Exception:                                  # noqa: BLE001
            # IBKR'nin herhangi bir arizasi Telegram botunu susturamaz.
            log.exception("[ibkr] tik basarisiz — dinleyici devam ediyor")

    def _tik(self) -> None:
        simdi = time.monotonic()
        if self._son_tikleme and (simdi - self._son_tikleme) < TIKLEME_ARALIGI_SN:
            return

        d = self.durumu_oku()

        # Zaman asimina ugramis ama arkauc baglantisi duran oturum:
        # ELLE GIRIS GEREKMEZ, kendiliginden toparlanir.
        if d.ulasilabilir and d.bagli and not d.kimlik_dogrulandi:
            log.info("[ibkr] oturum dusmus ama baglanti duruyor — yeniden kuruluyor")
            self.kur()
            d = self.durum

        if d.kullanilabilir:
            self._tikle()
        else:
            # Tiklemeye calismasak bile damgayi ilerlet, yoksa her
            # dongude durum sorgusu yapariz.
            self._son_tikleme = simdi

        self._gecisleri_bildir()

    # ------------------------------------------------------------------
    def _gecisleri_bildir(self) -> None:
        """
        Yalnizca DEGISIMDE haber ver. Her turda "IBKR kapali" yazmak,
        kullaniciyi bildirimleri kapatmaya iter — ve kapatilan bildirim
        hic olmayan bildirimden kotudur.
        """
        d = self.durum
        simdi_ok = d.kullanilabilir
        onceki = self._onceki_kullanilabilir
        self._onceki_kullanilabilir = simdi_ok

        if onceki is None:
            return                       # ilk olcum: sessiz

        if onceki and not simdi_ok:
            if not d.ulasilabilir:
                sebep = ("Gateway'e ulasilamiyor — java sureci durmus olabilir.\n"
                         "<code>cd ~/Downloads/clientportal.gw && "
                         "bin/run.sh root/conf.finagent.yaml</code>")
            elif not d.kimlik_dogrulandi:
                sebep = ("Giris dusmus. Tarayicidan yeniden gir:\n"
                         "https://localhost:5001")
            else:
                sebep = d.mesaj or "sebep bilinmiyor"
            self._haber("ibkr_oturum", f"🔌 <b>IBKR oturumu kapandi</b>\n{sebep}")
        elif not onceki and simdi_ok:
            self._haber("ibkr_oturum_geldi", "🔌 <b>IBKR oturumu acildi.</b>")

        # Rakip oturum AYRI bir bildirim: oturum hala calisiyor olabilir
        # ama her an dusebilir, ve sebebi kullanicinin kendi davranisi.
        if d.rakip_oturum and not self._onceki_rakip:
            self._haber("ibkr_rakip", (
                "⚠️ <b>IBKR: baska bir yerde oturum var</b>\n"
                "Telefon / Client Portal / TWS. IBKR bir kullanici adina "
                "TEK oturum veriyor; API oturumu her an dusebilir."))
        self._onceki_rakip = d.rakip_oturum

    def _haber(self, anahtar: str, mesaj: str) -> None:
        if not self._bildir:
            return
        try:
            self._bildir(anahtar, mesaj)
        except Exception:                                  # noqa: BLE001
            log.exception("[ibkr] bildirim gonderilemedi")
