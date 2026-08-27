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

KENDILIGINDEN TOPARLANMA — OLCULDU, VE ILK SURUM IKI KEZ YANILIYORDU
--------------------------------------------------------------------
Dusen brokerage oturumu `/iserver/auth/ssodh/init` ile TARAYICI GIRISI
OLMADAN dirilebiliyor. Olculdu 2026-08-26 06:30, oturum saatler once
dusmusken:

    compete=false -> {"passed": false}
                     fail: "Force compete capability must be used
                            together with compete flag"
    compete=true  -> {"passed": true, "authenticated": true}
                     /iserver/accounts -> CALISIYOR

Ilk surum bunu IKI AYRI SEBEPTEN kaciriyordu:

  1. KOSUL: yalnizca `connected: true` iken init deniyordu (IBKR'nin
     belgesindeki cumleye dayanarak). Sahada oturum `connected: false`
     olarak dustu ve init HIC DENENMEDI.
  2. PARAMETRE: `compete` varsayilani `false`di. Gerekce dogruydu —
     Ali Client Portal'dayken bot onu sessizce disari atmasin — ama
     uygulama yanlisti: bayragi kapatmak korumayi saglamiyor, INIT'I
     TAMAMEN engelliyordu. `compete` bir saldiri bayragi degil, GEREKLI
     bir yetenek.

Koruyucu niyet KORUNUYOR, dogru yere tasindi:
    `competing: true`  -> baska yerde ACIK oturum var; `yaris` acik
                          degilse DOKUNMA, kullaniciya soyle.
    `competing: false` -> dusurulecek kimse yok; `compete: true`
                          zararsiz ve gerekli.

Bunun pratik anlami buyuk: gunluk ELLE giris her dususte degil, yalnizca
DIS (salt okuma) oturumu da oldugunde gerekiyor.

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

# BASARISIZ `ssodh/init` GERI CEKILMESI — OLCULEN ARIZADAN DOGDU.
#
# 2026-08-27: oturum 12:44'te normal sekilde bitti (`ssoExpires` sifira
# indi ve bu kez yenilenmedi). Sonrasinda `_tik()` kimlik yokken HER
# TIK'te `kur()` cagirdi: geri cekilme yok, ust sinir yok. Bot 7,5 saat
# boyunca dakikada bir denedi — gateway logunda o gun 4.528 istek,
# 1.256'si `auth/status`, ve gateway `retry 7 ... giving up` durumuna
# dustu. O sirada Ali'nin TAZE girisleri de reddedildi
# (`sso/validate?gw=1` -> 401 Access Denied): basarisiz kurtarma
# denemesi, kurtarmayi IMKANSIZ kildi.
#
# `istemci.py` bu riski zaten yaziyor — "Violator IP addresses may be
# put in a PENALTY BOX FOR 10 MINUTES. Repeat violator IP addresses may
# be PERMANENTLY BLOCKED" — ve hiz siniri TEK KAPIDA toplanmisti. Ama
# basarisiz init DONGUSU o kapidan gecmiyordu: her istek tek basina
# sinirin altindaydi, sorun ISTEK HIZI degil ISRARDI.
#
# HARD STOP YOK, USTEL GERI CEKILME VAR: sert durus kendini iyilestiren
# bir yolu kapatirdi (kimlik gecerliyken brokerage oturumu gecici bir
# sebeple dusmusse ilerideki bir deneme TUTAR). Tavan 30 dakika: 7,5
# saatte ~450 deneme yerine ~6.
INIT_TABAN_SN = 60.0
INIT_AZAMI_SN = 1800.0


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

    def __init__(self, istemci: Istemci, bildir=None, yaris: bool = False,
                 onceki_kullanilabilir: bool | None = None):
        self.istemci = istemci
        self._bildir = bildir
        self._yaris = yaris                 # ssodh/init -> compete
        self.durum = Durum()
        self._son_tikleme = 0.0
        self._son_durum = 0.0
        # `ssoExpires` olcumunun onceki degeri. `self.durum`da TUTULAMAZ:
        # `durumu_oku()` her cagrida taze bir Durum kuruyor.
        self._onceki_bitis: int | None = None
        self._onceki_kullanilabilir: bool | None = onceki_kullanilabilir
        self._onceki_rakip = False
        # Ust uste basarisiz `kur()` sayisi ve bir sonraki denemenin en
        # erken zamani (monotonic). Bkz. INIT_TABAN_SN gerekcesi.
        self._init_hata = 0
        self._init_sonraki = 0.0

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
    def kur(self) -> bool | None:
        """
        Brokerage oturumunu yeniden kur (`/iserver/auth/ssodh/init`).

        UC DEGER DONER, IKI DEGIL:
            True  -> oturum kuruldu
            False -> DENENDI, olmadi        (geri cekilmeyi tetikler)
            None  -> HIC DENENMEDI, atlandi (geri cekilmeyi TETIKLEMEZ)

        Ayrim `_tik`in geri cekilmesi icin sart. Rakip oturumda hicbir
        istek gitmiyor; onu "basarisiz deneme" saymak, Ali telefondan
        cikinca botun 30 dakika bosuna beklemesi demekti — yani koruma
        mekanizmasi toparlanmayi geciktirirdi. `bool(None)` zaten
        yanlis (falsy), o yuzden "kuruldu mu" diye soran cagiran taraf
        icin davranis DEGISMIYOR.

        `compete` BIR SALDIRI BAYRAGI DEGIL, GEREKLI BIR YETENEK.
        Olculdu (2026-08-26 06:30) — `compete: false` ile:

            {"passed": false, ...}
            fail: "Force compete capability must be used together
                   with compete flag"

        Ayni an `compete: true` ile:

            {"passed": true, "authenticated": true, "connected": true}
            /iserver/accounts -> CALISIYOR

        Yani brokerage oturumu TARAYICI GIRISI OLMADAN dirildi. Ilk
        surum `compete`i varsayilan `false` yapmisti — gerekce dogruydu
        (Ali Client Portal'a girdiginde bot onu sessizce disari
        atmasin) ama UYGULAMA yanlisti: bayragi kapatmak korumayi
        saglamiyor, INIT'I TAMAMEN engelliyordu.

        DOGRU KURAL, koruyucu NIYETI aynen tutar:
          * `competing: true`  -> baska yerde ACIK bir oturum VAR.
            `yaris` acik degilse DOKUNMA; kullaniciya soylenir.
          * `competing: false` -> dusurulecek kimse yok, `compete: true`
            zararsiz ve GEREKLI.
        """
        rakip = bool(self.durum.rakip_oturum)
        if rakip and not self._yaris:
            log.info("[ibkr] rakip oturum var, init ATLANDI (yaris kapali)")
            return None
        try:
            y = self.istemci.post(
                "/iserver/auth/ssodh/init",
                {"publish": True, "compete": True},
            )
        except IbkrHatasi as e:
            log.info("[ibkr] oturum kurulamadi: %s", e)
            return False
        ok = bool(isinstance(y, dict) and y.get("authenticated"))
        # `fail` ALANI OKUNUYOR: IBKR neden olmadigini orada yaziyor ve
        # ilk surum onu gormezden geliyordu — "kurulamadi" deyip
        # sebebini yutmak, HTTP 400 govdesini atmakla ayni hataydi.
        if not ok and isinstance(y, dict) and y.get("fail"):
            log.info("[ibkr] ssodh/init reddetti: %s", y["fail"])
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

        # BROKERAGE OTURUMU DUSMUSSE YENIDEN KURMAYI DENE.
        #
        # Ilk surum `d.bagli` SART kosuyordu ("connected true iken
        # anlamlidir" diye). Sahada oturum `connected: false` olarak
        # dustu ve init HIC DENENMEDI — oysa `compete: true` ile
        # denenince DIRILDI. Yanlis kosul, calisan bir kurtarma yolunu
        # gorunmez yapmisti.
        #
        # Gateway'e ulasilamiyorsa denemenin anlami yok; 401 ise dis
        # oturum da olmustur ve init de 401 alir — `kur()` sessizce
        # basarisiz olur, dongu bildirime birakir.
        if d.kimlik_dogrulandi:
            # Kimlik geri geldi (elle giris ya da basarili init): geri
            # cekilme sifirlanir, yoksa bir sonraki dususte bot 30 dakika
            # bosuna beklerdi.
            self._init_sifirla()
        elif d.ulasilabilir:
            if simdi >= self._init_sonraki:
                log.info("[ibkr] brokerage oturumu kapali — init deneniyor "
                         "(ust uste basarisiz: %d)", self._init_hata)
                sonuc = self.kur()
                if sonuc:
                    self._init_sifirla()
                elif sonuc is False:
                    # `None` = hic denenmedi (rakip oturum). Istek
                    # gitmediyse geri cekilecek bir sey de yok.
                    self._init_geri_cekil(simdi)
                d = self.durum
            else:
                # SESSIZ ATLAMA DEGIL: kac saniye kaldigi loglaniyor.
                # "Neden denemiyor" sorusunun cevabi gorunur olmali.
                log.debug("[ibkr] init geri cekilmede — %.0f sn kaldi "
                          "(ust uste basarisiz: %d)",
                          self._init_sonraki - simdi, self._init_hata)

        if d.kullanilabilir:
            self._tikle()
        else:
            # Tiklemeye calismasak bile damgayi ilerlet, yoksa her
            # dongude durum sorgusu yapariz.
            self._son_tikleme = simdi

        self._gecisleri_bildir()

    # ------------------------------------------------------------------
    def _init_sifirla(self) -> None:
        if self._init_hata:
            log.info("[ibkr] init geri cekilmesi sifirlandi "
                     "(%d basarisiz denemeden sonra)", self._init_hata)
        self._init_hata = 0
        self._init_sonraki = 0.0

    def _init_geri_cekil(self, simdi: float) -> None:
        """Basarisiz denemeden sonra bekleme suresini IKIYE KATLAR."""
        self._init_hata += 1
        bekleme = min(INIT_TABAN_SN * (2 ** (self._init_hata - 1)),
                      INIT_AZAMI_SN)
        self._init_sonraki = simdi + bekleme
        log.info("[ibkr] init basarisiz (%d.) — sonraki deneme %.0f sn sonra",
                 self._init_hata, bekleme)

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
            # ILK OLCUM SESSIZ — ama yalnizca GERCEKTEN gecmis yoksa.
            #
            # SAHADA ISIRDI (27 Agu 00:01): oturum 21:56'da dustu ve
            # "kapandi" mesaji GITTI. Ali tarayicidan girdi, oturum
            # geri geldi — ama tam o aralikta bot yeniden baslatildi.
            # Yeni surecte `_onceki_kullanilabilir` None oldugu icin
            # ilk olcum sessiz gecti ve "OTURUM GELDI" mesaji HIC
            # GONDERILMEDI. Ali kapandi mesajini aldi, geldi mesajini
            # bekledi, gelmedi.
            #
            # Kusur "ilk olcum sessiz" kuralinda degil, gecmisin
            # BELLEKTE tutulmasindaydi: surec restart'i onu siliyor,
            # oysa "en son ne bildirdim" DISKTE duruyor. Cagiran taraf
            # artik o kaydi okuyup tohumluyor (`onceki_kullanilabilir`).
            return

        if onceki and not simdi_ok:
            if not d.ulasilabilir:
                sebep = ("Gateway'e ulasilamiyor — java sureci durmus olabilir.\n"
                         "<code>cd ~/Downloads/clientportal.gw && "
                         "bin/run.sh root/conf.finagent.yaml</code>")
            elif d.rakip_oturum:
                # SEBEBE GORE TAVSIYE. Bu dal olmadan mesaj "tarayicidan
                # yeniden gir" diyordu — ve rakip oturum durumunda bu
                # TAM TERS tavsiye: Ali zaten tarayicida, yeniden giris
                # iki istemciyi birbirini dusuren bir salincaga sokar.
                # Yanlis tesbit degil, yanlis TAVSIYE: alarm dogru caliyor
                # ama gosterdigi kapi yanlis kapi.
                sebep = ("Sebep: <b>baska bir yerde oturum actin</b> "
                         "(Client Portal / telefon / TWS). IBKR bir "
                         "kullanici adina TEK brokerage oturumu veriyor.\n\n"
                         "• Oradan cikinca bot kendi kendine toparlar — "
                         "burada bir sey yapmana gerek yok.\n"
                         "• Ikisini AYNI ANDA istiyorsan tek yol IKINCI "
                         "BIR KULLANICI ADI (IBKR'nin kendi cozumu).\n\n"
                         "<i>Portfoy/bakiye okumasi calismaya devam "
                         "ediyor; duşen yalnizca emir ve anlik fiyat "
                         "katmani.</i>")
            elif not d.kimlik_dogrulandi:
                sebep = ("Giris dusmus ve kendiligimden toparlayamadim.\n"
                         "Tarayicidan yeniden gir: https://localhost:5001")
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
                "TEK brokerage oturumu veriyor; API oturumu her an "
                "dusebilir.\n\n"
                "<i>Botu KASTEN disari atmadim: rakip oturum gorulunce "
                "init denenmiyor, yoksa ikimiz birbirimizi dusurup "
                "dururduk. Isin bitince cikman yeterli.</i>"))
        self._onceki_rakip = d.rakip_oturum

    def _haber(self, anahtar: str, mesaj: str, gecis: bool = True) -> None:
        if not self._bildir:
            return
        try:
            # OTURUM BILDIRIMLERININ HEPSI DURUM DEGISIMI: `_gecisleri_bildir`
            # yalnizca durum degistiginde cagiriyor. Bunu bekciye SOYLEMEK
            # sart, yoksa 6 saatlik tekrar penceresi "dustu" ve "geldi"yi
            # ayni tekrar sanip yutuyor — 26 Agu'da 11 dakikalik kesinti
            # boyle sessiz gecti.
            self._bildir(anahtar, mesaj, gecis=gecis)
        except Exception:                                  # noqa: BLE001
            log.exception("[ibkr] bildirim gonderilemedi")
