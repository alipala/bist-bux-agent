"""
GUN ICI KOSU — iki katman: esik kontrolu (LLM'siz) + taktik (tek cagri).

NEDEN VAR
---------
Koruma seviyesi ve tez kosulu gunde dort kez, GUNLUK KAPANISLA kontrol
ediliyordu. Yani bir stop sabah 10:30'da kirildiysa kullanici bunu
17:45 kosusunda ogreniyordu — yedi saat sonra. Kullanicinin bilmek
istedigi an, KIRILDIGI andir.

IKI KATMAN, KESIN SIRAYLA
-------------------------
1. ESIK KONTROLU — LLM'siz. "Kapanis su seviyenin altina indi mi" bir
   karsilastirmadir; model cagirmak hem pahali (gunde ~16 kosu) hem
   gereksiz. Koruma kirilimi ve tez bozulmasi BURADA tespit edilir ve
   TESLIM EDILIR.
2. TAKTIK (B6) — tek LLM cagrisi, yalnizca deterministik tarayici bir
   aday bulduysa.

SIRA SOZLESME: taktik katmani patlasa, yavaslasa, tavana takilsa ya da
duvar saatine carpsa bile koruma/tez alarmlari coktan gitmis olur.
Onceden yazilmis bir esigin gerceklestigini bildirmek, yorum uretmekten
DAHA ONEMLI ve daha kesin bir istir.

Bu dosya bir zamanlar "LLM YOK" diyordu ve B6'ya kadar dogruydu.
Degisince baslik da degisti: koda uymayan bir beyan, bu projede en
pahaliya mal olan hata sinifi.

KAPSAM SINIRI ACIKCA BEYAN EDILIYOR
-----------------------------------
Gun ici yalnizca FIYAT SEVIYESI kontrol edilir:
  * koruma seviyeleri (2N-ATR stop)
  * `close` alanina dayanan tez kosullari
RSI/SMA/hacim/CAR tabanli kosullar GUNLUK gostergelerdir ve gun ici
hesaplanmaz — saatlik bardan uretilen bir "RSI14" gunluk RSI ile ayni
ad altinda BASKA bir sey olurdu ve iki katman birbiriyle celisirdi.
O kosullar gunluk kosularda kontrol edilmeye devam ediyor.

SEVIYE YUKSELTILMEZ
-------------------
Ratchet (stop'un yukari kilitlenmesi) GUNLUK kapanisla calisir. Gun ici
yukseltseydik stop gun icinde yukselir ve ayni gunun geri cekilmesiyle
kirilirdi — kendi urettigi alarmi calan bir mekanizma.

SESSIZLIK GECERLI CIKTIDIR
--------------------------
Kirilan bir sey yoksa mesaj GITMEZ. Gunde ~16 kosu x "bugun bir sey
yok" mesaji, bildirimlerin kapatilmasinin en hizli yolu olurdu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .arsiv import arsivle

log = logging.getLogger(__name__)

# Gun ici katmanin KAPSADIGI borsalar — saatlik verisi olanlar (B4).
# Avrupa kotasyonlari saatlik toplanmiyor, dolayisiyla burada da yok.
KAPSAM_BORSALARI = ("BIST", "ABD")

# Kabuk oldurmeden once teslimata birakilan pay (saniye).
#
# Taktik cagrisi bittikten SONRA yapilacak is var: Telegram gonderimi,
# defter yazimi, kosu izi. Bunlarin ortasinda oldurulmek en kotu
# sonucu verir — mesaj gider, damga yazilmaz, ayni taktik bir sonraki
# kosuda TEKRAR gonderilir. 30 sn, olculen teslimat suresinin (~1-2 sn)
# kat kat ustunde ve secilme sebebi bu marj.
TESLIMAT_PAYI_SN = 30


def acik_borsalar(simdi: datetime | None = None) -> list[str]:
    """
    Kapsamdaki borsalardan su an ACIK olanlar.

    Seans bilgisi `piyasa.seans_durumlari`den — ikinci bir saat tablosu
    yazmak, bu projenin tekrar eden kusur sinifi olurdu (ayni gercek iki
    yerde beyan edilir ve sessizce ayrisir).
    """
    from ..piyasa import seans_durumlari
    return [s["borsa"] for s in seans_durumlari(simdi)
            if s["borsa"] in KAPSAM_BORSALARI and s["acik"]]


def en_uzun_acik_dk(simdi: datetime | None = None) -> int | None:
    """
    Kapsamdaki ACIK borsalardan EN UZUN suredir acik olaninin dakikasi.
    Hicbiri acik degilse None.

    NEDEN VAR — BEKCININ SABAH YANLIS ALARMI (olculdu 2026-08-25 09:00:13,
    Ali'ye gitti): `gunici_sessiz` iz yasini MUTLAK olcuyordu, oysa iz
    YALNIZCA piyasa acikken yaziliyor (`calistir` kapaliyken `_iz_birak`a
    HIC ULASMADAN donuyor). Gece boyunca kosu her 30 dk calisti ve dogru
    sekilde "hicbiri acik degil" dedi — ama iz tazelenmedi.

        son iz      24 Agu 21:45  (ABD acikken, son gercek kosu)
        BIST acildi 25 Agu 09:00 CEST
        bekci bakti 25 Agu 09:00:13   -> "son iz 675 dk once" ALARM
        ilk gercek kosu 09:16          -> iz nihayet yazildi

    Yani alarm, acilistan 13 saniye sonra, HENUZ KOSU VAKTI GELMEDEN
    caldi. Bu her islem sabahi tekrarlanirdi.

    DOGRU OLCUT IZ YASI DEGIL, "PIYASA ACILALI NE KADAR OLDU": bir kosu
    ancak acilistan sonra vadesi gelirse beklenebilir. En UZUN suredir
    acik olani aliyoruz — BIST yeni acilmis ama ABD saatlerdir acikken
    susmak, gercek bir arizayi gizlerdi.
    """
    from ..piyasa import seans_durumlari
    yaslar = [s.get("acilali_dk") for s in seans_durumlari(simdi)
              if s["borsa"] in KAPSAM_BORSALARI and s["acik"]]
    yaslar = [y for y in yaslar if y is not None]
    return max(yaslar) if yaslar else None


class GunIci:
    """Piyasa saatinde calisan, LLM'siz esik kontrolu."""

    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    # ------------------------------------------------------------------
    def calistir(self, bildir: bool = True, sahip: str | None = None,
                 topla: bool = True) -> dict:
        """
        Doner: {"durum": "kapali"|"kostu", ...}

        `topla=False` yalnizca test ve elle kosu icin — gercek kosuda
        saatlik veri tazelenmeden kontrol etmek, BAYAT barla karar
        vermek demektir.
        """
        ayar = self.s.gunici_ayari()
        if not ayar["enabled"]:
            return {"durum": "kapali", "sebep": "ritim.gunici.enabled: false"}

        acik = acik_borsalar()
        if not acik:
            # PIYASA KAPALIYKEN HICBIR SEY YAPILMAZ ve bu bir ARIZA
            # DEGILDIR. Bekci de pencere disinda sessiz kalir.
            log.info("[gunici] kapsamdaki borsalarin hicbiri acik degil — "
                     "kontrol yok")
            return {"durum": "kapali", "sebep": "borsa kapali", "acik": []}

        sahipler = ([sahip] if sahip else list(ayar["alicilar"]))
        if not sahipler:
            raise ValueError(
                "gunici: alici yok. config/settings.yaml -> ritim.gunici")

        toplama = None
        if topla:
            toplama = self._saatlik_tazele()

        sonuc, gonderilen = {}, 0
        for sira, s in enumerate(sahipler):
            try:
                # BUTCE HER SAHIPTEN ONCE YENIDEN HESAPLANIR. Bastan
                # bolmek, ilk sahip hizli bittiginde kalan sureyi CÖPE
                # atardi; yavas bittiginde ise ikinciye olmayan bir
                # sure vaat ederdi.
                butce = self._taktik_butcesi(ayar, len(sahipler) - sira)
                sonuc[s] = self._sahip(s, bildir, butce)
                gonderilen += sonuc[s].get("gonderilen", 0)
            except Exception as e:                    # noqa: BLE001
                # IZOLASYON: bir sahibin hatasi digerini DURDURMAZ.
                log.exception("[gunici] '%s' kontrolu patladi", s)
                sonuc[s] = {"hata": f"{type(e).__name__}: {e}"}

        self._iz_birak(acik, sahipler, gonderilen)
        return {"durum": "kostu", "acik": acik, "sahipler": sahipler,
                "toplama": toplama, "gonderilen": gonderilen, "sonuc": sonuc}

    # ------------------------------------------------------------------
    def _taktik_butcesi(self, ayar: dict, kalan_sahip: int) -> float:
        """
        Bu sahibin taktik cagrisina birakilan saniye. Yoksa 0.

        UC SINIRIN EN KUCUGU:
          1. Ayardaki `taktik.sure_sn` — istenen tavan
          2. Kabugun OLDURME anina kalan sure eksi teslimat payi
          3. Kalan sahip sayisina bolunmus adil pay

        Ikincisi olmadan Python kendi sinirini bilemez ve kabuk onu
        teslimatin ORTASINDA oldurebilir. Ucuncusu olmadan ilk sahip
        butun sureyi yiyip ikinciye hic birakmaz — panelde OLCULEN
        arizanin ta kendisi.
        """
        import os
        import time

        from .runner import KOSU_BITIS_ENV

        istenen = float(ayar.get("taktik_sure_sn") or 0)
        if istenen <= 0:
            return 0.0
        ham = os.getenv(KOSU_BITIS_ENV)
        if not ham:
            # DAMGA YOKSA AYARDAKI SINIR UYGULANIR ve bu BEYAN edilir.
            # Elle kosuda (`run.py gunici`) kabuk yok, dolayisiyla damga
            # da yok — o kosuyu engellemek yanlis olurdu.
            log.info("[gunici] %s yok — taktik butcesi ayardan: %.0fsn",
                     KOSU_BITIS_ENV, istenen)
            return istenen
        try:
            kalan = float(ham) - time.time()
        except (TypeError, ValueError):
            log.warning("[gunici] %s okunamadi (%r) — ayardaki sinir "
                        "uygulaniyor", KOSU_BITIS_ENV, ham)
            return istenen
        pay = (kalan - TESLIMAT_PAYI_SN) / max(1, kalan_sahip)
        butce = min(istenen, pay)
        if butce < istenen:
            log.info("[gunici] taktik butcesi kabuga gore kisildi: "
                     "%.0f -> %.0f sn (kalan %.0f, sahip %d)",
                     istenen, butce, kalan, kalan_sahip)
        return max(0.0, butce)

    # ------------------------------------------------------------------
    def _saatlik_tazele(self) -> dict:
        """
        Saatlik barlari tazeler. HATA KOSUYU DUSURMEZ — eldeki barlarla
        kontrol yine calisir; tazelik kapisi (`GUN_ICI_AZAMI_YAS_DK`)
        bayat veriyle alarm uretilmesini zaten engelliyor.
        """
        try:
            from ..collectors import REGISTRY
            r = REGISTRY["saatlik"](self.s, self.db).run()
            return {"durum": r.status, "satir": r.rows, "ms": r.duration_ms}
        except Exception as e:                        # noqa: BLE001
            log.warning("[gunici] saatlik tazeleme patladi: %s", e)
            return {"durum": "error", "sebep": str(e)[:200]}

    def _sahip(self, sahip: str, bildir: bool,
               taktik_butcesi: float = 0.0) -> dict:
        from .journal import Defter
        from .koruma import Koruma

        koruma = Koruma(self.db)
        kirilan = koruma.gun_ici_kontrol(sahip)
        bozulan = Defter(self.db).gun_ici_tez_kontrol(sahip)

        # TARAMA ONCE KOSAR ama MESAJ URETMEZ: deterministik, ucuz ve
        # sonucunu IKI katman kullaniyor (koruma mesajindaki kilit
        # uyarisi + taktik adaylari). Iki kez taramak, ayni gercegin iki
        # yerde hesaplanip sessizce ayrismasi demek olurdu.
        aday, tarama = self._tara(sahip)
        kilitli = {a["sembol"].upper() for a in aday if a.get("kilitli")}

        gonderilen = 0
        # KORUMA VE TEZ ONCE. Ikisi de "onceden yazilmis bir esik
        # gerceklesti" diyor ve LLM'siz; taktik katmani patlasa,
        # yavaslasa ya da tavana takilsa bile bunlar TESLIM EDILMIS
        # olmali. Sira sozlesmesi: tespit -> teslimat -> damga.
        if kirilan and self._koruma_bildir(sahip, kirilan, bildir, koruma,
                                           kilitli):
            gonderilen += len(kirilan)
        if bozulan and self._tez_bildir(sahip, bozulan, bildir):
            gonderilen += len(bozulan)

        taktik = self._taktik(sahip, bildir, aday, tarama, taktik_butcesi)
        gonderilen += taktik.get("gonderilen", 0)

        if not kirilan and not bozulan and not taktik.get("gonderilen"):
            # SESSIZLIK GECERLI CIKTI: mesaj gitmiyor, log yeter.
            log.info("[gunici/%s] esigi gecen yok — mesaj YOK", sahip)
        return {"koruma_kirilan": len(kirilan), "tez_bozulan": len(bozulan),
                "taktik": taktik, "gonderilen": gonderilen}

    # ------------------------------------------------------------------
    def _tara(self, sahip: str) -> tuple[list, dict]:
        """
        Gun ici aday taramasi. HATA KOSUYU DUSURMEZ.

        Tarama patlarsa koruma ve tez alarmlari yine gider — onlar bu
        katmana bagli DEGIL. Kaybedilen tek sey taktik ve kilit uyarisi
        olur, ve bu BEYAN edilir.
        """
        try:
            from .gunici_tarayici import adaylar as tara
            return tara(self.db, sahip)
        except Exception as e:                        # noqa: BLE001
            log.exception("[gunici/%s] aday taramasi patladi", sahip)
            return [], {"hata": f"{type(e).__name__}: {e}"[:200]}

    def _taktik(self, sahip: str, bildir: bool, aday: list,
                tarama: dict, butce: float) -> dict:
        """
        B6 gun ici taktik katmani. KAPALIYSA ya da ADAY YOKSA sessiz.

        HATA IZOLE: buradaki hicbir ariza koruma/tez alarmlarini
        etkilemez — onlar zaten TESLIM EDILMIS oluyor (bkz. `_sahip`).
        """
        ayar = self.s.gunici_ayari()
        if not ayar.get("taktik_enabled"):
            return {"durum": "kapali",
                    "sebep": "ritim.gunici.taktik.enabled: false"}
        try:
            return self._taktik_kos(sahip, bildir, aday, tarama, butce)
        except Exception as e:                        # noqa: BLE001
            log.exception("[gunici/%s] taktik katmani patladi", sahip)
            return {"durum": "hata", "sebep": f"{type(e).__name__}: {e}"[:200]}

    def _taktik_kos(self, sahip: str, bildir: bool, aday: list,
                    tarama: dict, butce: float) -> dict:
        import anyio

        from .taktikci import Taktikci

        if butce <= 0:
            # BUTCE KALMADI: cagri YAPILMAZ. Yarim kalan bir cagri hem
            # para harcar hem teslimat payini yer.
            return {"durum": "atlandi", "sebep": "taktik butcesi kalmadi",
                    "tarama": tarama, "gonderilen": 0}
        tk = Taktikci(self.s, self.db, sure_siniri_sn=butce)
        hazir = tk.hazirla(sahip, aday)
        durum = {"durum": "kostu", "tarama": tarama, "hazirlik": hazir,
                 "gonderilen": 0}

        if not hazir["cagir"]:
            log.info("[gunici/%s] taktikci CAGRILMADI: %s",
                     sahip, hazir["sebep"])
            return durum

        taktikler, rapor = anyio.run(
            tk.uret, sahip, hazir["yeni_adaylar"], hazir["kalan"])
        durum["uretim"] = rapor
        uygulanabilir = [t for t in taktikler if t["tur"] != "bekle"]
        if not uygulanabilir:
            log.info("[gunici/%s] uygulanabilir taktik yok (gecerli=%d, "
                     "reddedilen=%s)", sahip, rapor["gecerli"],
                     rapor["reddedilen"])
            return durum
        if not bildir:
            log.info("[gunici/%s] bildirim kapali — %d taktik deftere "
                     "YAZILMADI", sahip, len(uygulanabilir))
            return durum

        from .journal import Defter
        defter = Defter(self.db)

        # GOLGE TURLER — URETILIR, OLCULUR, GONDERILMEZ (2 Eki, Ali onayi).
        # `teslim=0` ile yazilir: karne ve fren olcmeye devam eder ama
        # `emir_kanit` bunu "botun onerisi" saymaz (kullaniciya gitmedi).
        # Gonderim yok, dolayisiyla teslimat-damga sirasi burada sorun
        # degil: yazilmazsa bir sonraki kosu ayni taktigi tekrar uretmez
        # (`_bugun_semboller` defterden okuyor), yani yazim hemen yapilir.
        golge_tur = set(self.s.gunici_ayari().get("taktik_golge_turler") or ())
        golge = [t for t in uygulanabilir if t["tur"] in golge_tur]
        gidecek = [t for t in uygulanabilir if t["tur"] not in golge_tur]
        if golge:
            defter.kaydet(golge, sahip, teslim=0)
            durum["golge"] = len(golge)
            log.info("[gunici/%s] %d taktik GOLGEDE yazildi, gonderilmedi "
                     "(%s)", sahip, len(golge),
                     ", ".join(sorted({t["tur"] for t in golge})))
        if not gidecek:
            return durum

        # TESPIT -> TESLIMAT -> DAMGA. Defter yazimi mesaj GITTIKTEN
        # sonra; ters sirada gonderilemeyen bir taktik `DO NOTHING`
        # yuzunden bir daha ASLA denenmezdi (ROSE tezinde bu yasandi).
        if self._gonder(sahip, self._taktik_metni(gidecek, hazir),
                        lambda: defter.kaydet(gidecek, sahip, teslim=1)):
            durum["gonderilen"] += len(gidecek)
        return durum

    # ------------------------------------------------------------------
    def _koruma_bildir(self, sahip: str, kirilan: list[dict], bildir: bool,
                       koruma, kilitli: set | None = None) -> bool:
        """Tespit -> TESLIMAT -> damga. Sira sozlesmesi degismiyor."""
        if not bildir:
            log.info("[gunici/%s] bildirim kapali — koruma kirilimi "
                     "damgalanmadi (%d kayit)", sahip, len(kirilan))
            return False
        return self._gonder(sahip, self._koruma_metni(kirilan, kilitli),
                            lambda: koruma.damgala(kirilan))

    def _tez_bildir(self, sahip: str, bozulan: list[dict],
                    bildir: bool) -> bool:
        from .journal import Defter
        if not bildir:
            log.info("[gunici/%s] bildirim kapali — tez alarmi "
                     "damgalanmadi (%d kayit)", sahip, len(bozulan))
            return False
        defter = Defter(self.db)
        return self._gonder(sahip, self._tez_metni(bozulan),
                            lambda: defter.tez_damgala(bozulan))

    def _gonder(self, sahip: str, metin: str, damgala) -> bool:
        from ..notify import TelegramNotifier

        chatler = self.s.sahip_chatleri(sahip)
        if not chatler:
            log.error("[gunici] '%s' sahibinin chat_id'si YOK — mesaj "
                      "gonderilemedi, damga ATILMADI", sahip)
            return False
        tg = TelegramNotifier(self.s)
        giden = False
        for chat in chatler:
            try:
                giden = tg.send_message(metin, chat_id=chat) or giden
            except Exception as e:                    # noqa: BLE001
                log.warning("[gunici] %s/%s gonderilemedi: %s", sahip, chat, e)
        if not giden:
            log.error("[gunici/%s] ALARM GONDERILEMEDI — damga atilmadi, "
                      "sonraki kosu yeniden deneyecek", sahip)
            return False
        # ARSIVE YAZ — DAMGADAN ONCE, TESLIMATTAN SONRA.
        #
        # Bu kartlar (taktik, koruma, tez) bugune kadar HICBIR kayit
        # birakmiyordu; kullanici gorup soruyordu, model kendi
        # gonderdigi karti hatirlamiyordu. Gerekce `pulse/arsiv.py`
        # icinde. `arsivle` hatayi yutuyor: arsiv yazilamazsa kosu
        # devam etmeli, cunku mesaj ZATEN GITTI ve damga atilmali.
        arsivle(self.db, chatler[0], sahip, metin, "gunici")
        damgala()
        return True

    # ------------------------------------------------------------------
    @staticmethod
    def _kisa(v) -> str:
        """
        Fiyati TURKCE yazar. Onceden `%.6g` ile "2.52" uretiyordu ve
        AYNI MESAJDA "-%6,7" ile yan yana duruyordu — bir mesajda iki
        ondalik ayraci, kullaniciyi "2.52 mi 2,52 mi" diye tereddute
        dusurur. Hassasiyet degerin kendisinden turuyor.
        """
        if v is None:
            return "?"
        from .runner import _fiyat_tr
        return _fiyat_tr(v)

    def _koruma_metni(self, kirilan: list[dict],
                      kilitli: set | None = None) -> str:
        from .runner import _yuzde_tr
        e = _esc
        kilitli = kilitli or set()
        L = ["🛡 <b>GUN ICI · koruma seviyesi kirildi</b>"]
        for k in kirilan:
            pb = k.get("para_birimi") or ""
            L.append(f"\n<b>{e(k['sembol'])}</b> ({e(str(k['hesap']).upper())})")
            L.append(f"Saatlik kapanis <b>{self._kisa(k['kapanis'])} {e(pb)}</b> "
                     f"· stop <code>{self._kisa(k['stop'])}</code> "
                     f"({_yuzde_tr(k['mesafe_pct'], 1, ok=True)})")
            if str(k["sembol"]).upper() in kilitli:
                # KIRILDI **VE** CIKILAMIYOR. Bu, kirilma haberinden
                # AYRI bir gercek: A5'te olculdu, cikis tetiklerinin
                # %6,76'si kilitli bara dusuyor ve o barda emir
                # gerceklesmez. Ayri mesaj olarak GONDERILMIYOR — her
                # gun ici kosuda tekrarlanir ve spam olurdu; bilgi tam
                # burada, eyleme donusecegi yerde duruyor.
                L.append("🔒 <b>LIMIT KILIDI:</b> kagit su an pinli — "
                         "bu seviyeden CIKIS GERCEKLESMEYEBILIR.")
            L.append(f"<i>Bar {e(k['bar_ts'])} UTC · seviye "
                     f"{str(k['kuruldu_ts'])[:10]} tarihinde kuruldu.</i>")
        L.append("\n<i>SEANS ICI bir olcum: gunluk kapanis bunun ustune "
                 "donebilir. Satis tavsiyesi DEGIL; sistem emir gondermez.</i>")
        return "\n".join(L)

    def _tez_metni(self, bozulan: list[dict]) -> str:
        from .journal import Defter
        from .tez import ALAN_ADI, okunabilir
        e = _esc
        L = ["🔔 <b>GUN ICI · tez alarmi</b>"]
        # OLAY BASINA BIR BLOK — gerekce `Defter.tez_gruplari`.
        # Gun ici kanal da AYNI defter satirlarini okuyor; burada
        # gruplamamak, ayni gurultuyu seans icinde uretmek olurdu.
        # (Cizim burada AYRI kaliyor: gun ici mesaj saatlik bardan
        # olcuyor ve "Simdi saatlik ..." diyor; `runner.tez_bloklari`
        # gunluk metni yaziyor. Ortak olan GRUPLAMA, metin degil.)
        for b in Defter.tez_gruplari(bozulan):
            L.append(f"\n<b>{e(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {e(str(b['tez'])[:200])}</i>")
            # HAM GRAMER DEGIL, OKUNABILIR CUMLE. Defterde kosul
            # oldugu gibi duruyor (denetim izi); kullanicinin okudugu
            # sey "kapanis 0,0055 altina inerse" olmali.
            L.append("Onceden yazilan kosul: <b>"
                     + e(str(okunabilir(b["kosul"]))) + "</b>")
            L.append(f"Simdi saatlik {e(ALAN_ADI.get(b['alan'], b['alan']))}: "
                     f"<b>{self._kisa(b['deger'])}</b>")
            kaynaklar = [k for k in (b.get("kaynaklar") or []) if k]
            if len(kaynaklar) > 1:
                L.append(f"<i>Ayni esik {e(', '.join(kaynaklar))} "
                         f"kayitlarinda yaziliydi ({b.get('kayit', 1)} kayit, "
                         "tek olay).</i>")
        L.append("\n<i>SEANS ICI olculdu. Onceden ACIKCA yazilmis bir esigin "
                 "gerceklestigi bildiriliyor; al/sat tavsiyesi degil.</i>")
        return "\n".join(L)

    def _taktik_metni(self, taktikler: list[dict], hazir: dict) -> str:
        """
        Taktik mesaji. Her satirda SEVIYENIN KAYNAGI ve boyutlama var;
        basinda karnenin durumu.
        """
        from .boyutlama import satir as boyut_satiri
        from .runner import _tr as _tr_fiyat_ham, _yuzde_tr
        from .seviye import kaynak_adi
        from .tez import okunabilir

        def _tr_fiyat(v):
            # Fiyat hassasiyeti VARLIGA GORE: ROSE 0,0055 ile
            # ASML 1.512 ayni basamak sayisini kullanamaz.
            try:
                f = float(v)
            except (TypeError, ValueError):
                return str(v)
            ondalik = 0 if f == int(f) else \
                min(len(f'{f!r}'.split('.')[-1]), 8)
            return _tr_fiyat_ham(f, ondalik)


        e = _esc
        L = ["🎯 <b>GUN ICI TAKTIK</b>"]
        if hazir.get("olculmemis"):
            # OLCULMEMIS OLDUGU HER MESAJDA YAZAR. Bu katmanin isabeti
            # henuz bilinmiyor ve bilinmiyor demek, biliniyormus gibi
            # davranmaktan durusttur.
            # BAND KULLANICI DILINDE (2026-09-01, Ali bildirdi).
            #
            # Onceki hali: "Bu katmanin isabeti henuz OLCULMEMIS. Karne
            # dolana kadar bu taktikleri olculmus bir basari orani
            # DESTEKLEMIYOR." Ali: "bu bir kullanici olarak bir sey
            # ifade etmiyor."
            #
            # Hakliydi: cumle DURUSTU ama EYLEME donusmuyordu. Uc sey
            # eksikti — nerede duruyoruz (1/20), NEDEN olculmedi (29
            # taktik ufkunu bekliyor, katman bozuk degil), ve bununla
            # NE YAPILMALI (tavsiye degil, dikkat cekme).
            # ESIK TEK KAYNAKTAN. Sabiti burada TEKRAR YAZMAK, iki
            # kopyanin zamanla ayrisma kusurunu davet ederdi — bu depoda
            # olculmus bir kalip (`[[ayni-kural-iki-kopya]]`).
            from .taktikci import FREN_ASGARI_OLCUM

            k = hazir.get("karne") or {}
            olcum = int(k.get("olcum") or 0)
            esik = FREN_ASGARI_OLCUM
            # BEKLEYEN SAYISI IKI YOLDAN GELEBILIYOR ve ikisi de gecerli:
            # karne acikca `bekleyen` veriyorsa o kullanilir; vermiyorsa
            # (canli veride None geliyor) verilen taktik sayisindan
            # turetilir. Birini secip digerini yok saymak, alan dolu
            # oldugunda onu SESSIZCE atmak olurdu.
            verilen = sum((k.get("venue_kirilimi") or {}).values())
            bekleyen = k.get("bekleyen")
            if bekleyen is None:
                bekleyen = max(verilen - olcum, 0)
            verilen = verilen or (int(bekleyen) + olcum)
            L.append(
                f"<i>⚠️ Bu katmanin sicili henuz YOK — <b>{olcum}/{esik}</b> "
                "olculmus taktik"
                # "29'i" mi "29'u" mu — Turkce sayi eki sesli uyumuna
                # gore degisiyor ve her sayida farkli. "tanesi" eki
                # OLMAYAN bir kalip; sorunu cozmek yerine ATLATIYOR.
                + (f" ({verilen} taktik verildi, {bekleyen} tanesi henuz "
                   "ufkunu doldurmadi)" if bekleyen else "")
                + ". Bunu <b>tavsiye degil, DIKKAT CEKME</b> olarak oku: "
                  "hareketi olctum, ama bu taktigin tutup tutmadigini "
                  "henuz kimse olcmedi.</i>")
        elif hazir.get("fren"):
            L.append(f"<i>🚦 {e(hazir['tavan_gerekcesi'])}</i>")
        else:
            L.append(f"<i>Taktik karnesi: {e(hazir['tavan_gerekcesi'])}</i>")

        for t in taktikler:
            pb = t.get("para_birimi") or ""
            a = t.get("aday") or {}
            L.append(f"\n<b>{e(t['sembol'])} · {e(t['tur'].upper())}</b>")
            if a.get("gun_ici_hareket_%") is not None:
                # SIGMA ACIKLANIYOR: "-3.02σ" bir uzman kisaltmasi.
                # Kullanici mesajlarin "anlayacagimiz sekilde" olmasini
                # istedi (2026-08-21); sayi kalsin ama NE OLDUGU yazsin.
                L.append(f"Gun ici {_yuzde_tr(a['gun_ici_hareket_%'], 2, ok=True)}"
                         f" — kendi gunluk oynakliginin "
                         f"<b>{_tr_fiyat_ham(abs(float(a.get('sigma') or 0)), 1)}"
                         f" kati</b>"
                         f"\n<i>Son bar {e(str(a.get('bar_ts')))} UTC</i>")
            # SEVIYE KAYNAGI INSAN DILINDE. `donchian_giris` bir KOD
            # ANAHTARI; defterde oyle kaliyor (denetim izi makine
            # okunur olmali) ama mesajda "20 gunun en yuksek kapanisi"
            # yaziyor. Kullanici mesajlarin "anlayacagimiz sekilde"
            # olmasini istedi (2026-08-21).
            for alan, etiket in (("giris", "Giris"), ("stop", "Stop")):
                if t.get(alan) is None:
                    continue
                kaynak = kaynak_adi(t.get(f"{alan}_kaynak"))
                L.append(f"{etiket} <code>{_tr_fiyat(t[alan])} {e(pb)}</code>"
                         + (f" <i>({e(kaynak)})</i>" if kaynak else ""))
            bs = boyut_satiri(t.get("giris"), t.get("stop"), pb)
            if bs:
                # KACIS UYGULANMAZ: `boyutlama.satir` ZATEN HTML uretiyor
                # (`<b>%25.0</b>`). `_esc`ten gecirmek onu ikinci kez
                # kacislar ve kullanici ham `&lt;b&gt;` okur. Panelin
                # `runner._taktik_satirlari` ile AYNI sozlesme.
                L.append(bs)
            if t.get("gerekce"):
                L.append(f"<i>{e(str(t['gerekce'])[:220])}</i>")
            if t.get("gecersizlesme_kosulu"):
                # KOSUL OKUNABILIR YAZILIR. Defterde ham gramer duruyor
                # ("close < 83.035"); kullanicinin okudugu cumle ise
                # ayni esigi HASSASIYET KAYBETMEDEN anlatiyor.
                L.append("Bu taktik su durumda gecersiz: <b>"
                         + e(str(okunabilir(t["gecersizlesme_kosulu"])))
                         + "</b>")
        L.append("\n<i>Sistem EMIR GONDERMEZ. Seviyeler olculen "
                 "degerlerdir, tahmin degil; hangi olcumden geldigi "
                 "parantezde yaziyor.</i>")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def _iz_birak(self, acik: list, sahipler: list, gonderilen: int) -> None:
        """
        Kosu izi — bekcinin KANITI. Isin SONUNDA yaziliyor: yarim kalan
        kosu iz birakmaz ve bekci bunu yakalar.
        """
        import json
        from pathlib import Path
        try:
            dizin = Path(self.s.bot_state_dir) / "kosu"
            dizin.mkdir(parents=True, exist_ok=True)
            (dizin / "gunici.json").write_text(json.dumps({
                "kip": "gunici",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "acik_borsalar": list(acik), "sahipler": list(sahipler),
                "gonderilen": gonderilen,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as e:                        # noqa: BLE001
            log.warning("[gunici] kosu izi yazilamadi: %s", e)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
