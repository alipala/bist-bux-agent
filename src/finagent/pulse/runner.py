"""
NABIZ — proaktif dongunun orkestratoru.

    tarayici (deterministik)  ->  panel (4 ajan, paralel)  ->  hakem
         |                              |                        |
      signals                     predictions              Telegram

TASARIM KARARLARI
-----------------
* SESSIZLIK GECERLIDIR. Esik gecen sinyal yoksa panel hic calismaz ve
  bildirim gonderilmez. Her gun bir sey soylemek zorunda olan sistem
  gurultu uretir.
* PUANLAMA HER KOSUDA ONCE. Once vadesi dolmus tahminler olculur, sonra
  yenileri uretilir; boylece karne her zaman guncel ve ozet mesajinda
  "su ana kadarki isabetim su" diyebiliyoruz.
* LLM YALNIZCA ADAYLAR ICIN. Tarayici 55 enstrumani deterministik tarar,
  panel yalnizca en guclu birkacini yorumlar.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# PORTFOY RISKI, PIYASA SINYALI DEGILDIR. Ikisi ayni `sinyaller`
# listesinden geliyor ama bildirimde ayri bolumlere gider; tek yerde
# tanimli olmasi, birinde unutulup cift sayilmasini engelliyor.
RISK_TURLERI = ("yogunlasma", "acik_zarar")

_AY_KISA = ("Oca", "Sub", "Mar", "Nis", "May", "Haz",
            "Tem", "Agu", "Eyl", "Eki", "Kas", "Ara")


def _tr(v, basamak: int = 2) -> str:
    """
    Turkce sayi: ondalik VIRGUL. '19.91' bir Turk okuyucuda 19 bin 910
    gibi okunabilir; sohbet katmani zaten '-%19,91' yaziyor ve
    bildirimin ondan farkli konusmasi icin sebep yok.
    """
    return f"{v:,.{basamak}f}".replace(",", "\x00").replace(".", ",") \
                              .replace("\x00", ".")


def _yuzde_tr(v, basamak: int = 2) -> str:
    """'-%19,91' — isaret ONDE, yuzde isareti sayidan ONCE (TR yazimi)."""
    isaret = "-" if v < 0 else "+"
    return f"{isaret}%{_tr(abs(v), basamak)}"


def _tarih_kisa(ts) -> str | None:
    """
    ISO tarihten '19 Agu'. Ayristirilamiyorsa None — YANLIS TARIH
    YAZMAKTANSA hic yazma. Bildirimde tarih olmamasinin bedeli 20
    gunluk bir olayin 'bugun' sanilmasiydi; yanlis tarihin bedeli
    daha buyuk olur.
    """
    from datetime import date
    try:
        g = date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None
    return f"{g.day} {_AY_KISA[g.month - 1]}"


def _gun_farki_bugune(ts) -> int | None:
    """
    Bir zaman damgasinin BUGUNE gore yasi (gun). Ayristirilamazsa None.

    UYDURMA SAYI YOK: bilinmeyen tarih 0 gun sayilirsa bayat veri TAZE
    gorunur — `screener._gun_farki` ile ayni disiplin.
    """
    from datetime import date
    try:
        g = date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None
    return (date.today() - g).days


def _kisa(v) -> str:
    """Kripto kurus altinda; sabit 2 hane seriyi duzlestirir."""
    if v is None:
        return "-"
    a = abs(float(v))
    nd = 2 if a >= 100 else 4 if a >= 1 else 6 if a >= 0.01 else 8
    return f"{float(v):.{nd}f}"

# Panele gidecek en guclu sinyal sayisi. Fazlasi hem pahali hem
# odaksiz — 12 gozlem zaten 25 satirlik bir ozete zor sigiyor.
PANEL_ADAY = 12

# PORTFOYE AYRILAN ASGARI SLOT.
#
# Olculdu 2026-08-16: esigi gecen 100 gozlemin 84'u BIST, ve saf "en guclu
# 12" secimi panele 10 BIST + 2 BUX gonderiyordu. Oysa portfoy BUX ve
# BINANCE; BIST'te tek pozisyon yok ve Midas hesabinda bakiye de yok, yani
# panelin kapasitesinin %83'u ISLEM YAPILAMAYAN kagitlara gidiyordu.
#
# Sebep BIST'in daha ilginc olmasi degil, daha KALABALIK olmasi: 251 BIST
# sembolune karsi 19 BUX + 67 kripto. Guc siralamasi evren buyuklugunu
# olculmemis bir agirlik gibi iceri sokuyor.
#
# Cozum: sahip olunan enstrumanlarin sinyalleri once yerlestirilir, kalan
# slotlar guce gore doldurulur. Gerekce, tarayicinin portfoy risklerini
# ayri uretmesiyle ayni: mevcut sermayeye yonelik bir gozlem, esit
# guclu ama sahip olunmayan bir gozlemden daha degerlidir — uzerine
# islem yapmak yeni sermaye gerektirmez ve mevcut riski dogrudan ilgilendirir.
PORTFOY_ASGARI_SLOT = 5

# Bu gucun altindaki sinyal tek basina bildirime deger degil.
BILDIRIM_ESIGI = 0.55

# SURE BUTCESI — son sahibin ORTASINDA kesilmektense panelini ATLA.
#
# Olculdu 2026-08-18: panel sahip basina ~282 sn (yuksel 20:46:05 ->
# 20:48:11), iki sahiple ~9,4 dk. Sahip sayisi artarsa ya da bir panel
# takilirsa kabugun duvar saati sureci ORTADAN keser ve o sahip ne
# cikti ne aciklama alir.
#
# Bu esik asildiginda kalan sahiplerin paneli atlanir ve kendilerine
# SOYLENIR. Sessiz atlama YOK: "bugun mesaj gelmedi" ile "bugun panel
# kosamadi" ayri seyler ve ikincisi kullanicinin bilmesi gerekendir.
#
# GERCEK DEGER KIP BASINA AYARDAN gelir
# (`ritim.kipler.<kip>.panel_butce_sn`). Buradaki sabit yalnizca
# TAVANDIR: ayar bundan buyuk bir deger verse bile kabugun duvar saati
# devreye girer, o yuzden ustune cikmanin anlami yok. Env degiskeni
# testler ve elle kosular icin duruyor.
PANEL_SURE_BUTCESI_SN = float(__import__("os").getenv(
    "NABIZ_PANEL_BUTCE_SN", "1800"))


class Nabiz:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def calistir(self, bildir: bool = True, panel: bool = True,
                 kip: str = "nabiz", sahip: str | None = None) -> dict:
        """
        ORTAK FAZ bir kez, KISISEL FAZ her sahip icin SIRAYLA.

        Piyasa verisi kisiden bagimsiz: puanlama ve piyasa taramasi tek
        kez kosar, sinyaller 'ortak' yazilir. Kisisel olan yalnizca
        portfoy riski, tez kontrolu, panel ve bildirim.

        SIRAYLA, PARALEL DEGIL: iki es zamanli SDK oturumu abonelik hiz
        limitine takilir ve birbirini bozar. Sira DETERMINISTIK
        (yapilandirmadaki yazim sirasi) — rastgele sira, limit
        doldugunda hep ayni kisinin magdur olup olmadigini gizler.

        `sahip` verilirse YALNIZCA o kisi kosar (elle calistirma ve
        test icin). Verilmezse KIPIN ALICILARI — tum sahipler DEGIL.

        ALICI LISTESI KIP BASINA (ritim v2 §3.3). Alici olmayan sahip
        icin HICBIR SEY kosmaz: ne tez kontrolu, ne portfoy riski, ne
        defter yazimi. O kisinin kontrolu kendi kipinde yapilir.
        Bilinmeyen kip BURADA duser — `ritim_kip` varsayilana DUSMEZ.
        """
        ayar = self.s.ritim_kip(kip)
        sahipler = [sahip] if sahip else list(ayar["alicilar"])
        if not sahipler:
            # SESSIZ NO-OP DEGIL. Sahipsiz kosu hicbir sey uretmez ama
            # "calisti" gorunur; bu, bildirimlerin neden gelmedigini
            # gunlerce gizleyebilir. (`ritim_kip` bos aliciyi zaten
            # reddediyor; buraya ancak `sahip=""` ile gelinir.)
            raise ValueError(
                f"nabiz: {kip!r} kipinin alicisi yok. "
                "config/settings.yaml -> ritim.kipler")
        # KIP BASINA PANEL BUTCESI, ust sinir modul sabiti.
        panel_butce = min(float(ayar["panel_butce_sn"]), PANEL_SURE_BUTCESI_SN)

        try:
            ortak = self._ortak_faz(kip)
        except Exception as e:                        # noqa: BLE001
            # ORTAK FAZ PATLARSA kisisel faz anlamsiz — piyasa taramasi
            # olmadan gundem uretilemez. HERKESE bildirilir, sessizce
            # yutulmaz.
            log.exception("[%s] ORTAK FAZ patladi", kip)
            if bildir:
                self._herkese_bildir(
                    f"🔴 <b>{kip} ortak fazi patladi</b>\n\n"
                    f"<i>{_esc(type(e).__name__)}: {_esc(str(e)[:300])}</i>\n\n"
                    "Piyasa taramasi olmadan kisisel analiz uretilemedi; "
                    "bu kosuda kimse icin panel calismadi.")
            raise

        import time
        basladi = time.monotonic()
        sonuclar, basarisiz, atlanan = {}, [], []
        for s in sahipler:
            gecen = time.monotonic() - basladi
            if panel and gecen > panel_butce:
                # BUTCE DOLDU: paneli atla ama SOYLE. Deterministik
                # adimlar (tez, portfoy riski) yine kosar — ucuz ve
                # kullanicinin en cok isine yarayan cikti onlar.
                log.warning("[%s] sure butcesi doldu (%.0f sn) — '%s' paneli "
                            "atlaniyor", kip, gecen, s)
                atlanan.append(s)
                try:
                    sonuclar[s] = self._kisisel_faz(s, kip, bildir, False,
                                                    ortak)
                except Exception as e:                # noqa: BLE001
                    log.exception("[%s] '%s' hafif kosu da patladi", kip, s)
                    sonuclar[s] = {"hata": f"{type(e).__name__}: {e}"}
                if bildir:
                    self._sahibe_bildir(
                        s, f"🟡 <b>{kip}: panel kosamadi</b>\n\n"
                        f"Sure butcesi doldu ({gecen/60:.0f} dk). Tez alarmi "
                        "ve portfoy riski kontrol edildi; model yorumu bu "
                        "kosuda uretilmedi.")
                continue
            try:
                sonuclar[s] = self._kisisel_faz(
                    s, kip, bildir, panel, ortak)
            except Exception as e:                    # noqa: BLE001
                # IZOLASYON: bir sahibin hatasi digerini DURDURMAZ.
                log.exception("[%s] sahip '%s' kosusu patladi", kip, s)
                basarisiz.append(s)
                sonuclar[s] = {"hata": f"{type(e).__name__}: {e}"}
                if bildir:
                    self._sahibe_bildir(s, self._hata_metni(kip, e))

        # KOSU IZI. Bir kosunun CALISTIGINI baska hicbir kayit tek basina
        # soyleyemiyordu: `signals` tarih-bazli ve kip tasimiyor,
        # `collector_runs` sohbetten tetiklenen toplamalarla karisiyor,
        # `panel_runs` yalnizca LLM panelinde yaziliyor. Bu yuzden ogle
        # kosusunun 17 Agustos'ta hic calismadigi GUNLERCE gorunmedi.
        # Iz burada, yani isin SONUNDA birakiliyor; yarim kalan kosu iz
        # birakmaz ve bekci bunu yakalar (bot/watchdog.py).
        self._iz_birak(kip, sahipler, ortak)

        return {"kip": kip, "sahipler": sahipler, "basarisiz": basarisiz,
                "panel_atlanan": atlanan,
                "ortak": {k: v for k, v in ortak.items() if k != "sinyaller"},
                "sonuc": sonuclar,
                # Tek sahipli kurulumda BUGUNKU sozlesme korunuyor:
                # cagiranlar (run.py, testler) duz alanlari okuyor.
                **(sonuclar[sahipler[0]] if len(sahipler) == 1
                   and "hata" not in sonuclar[sahipler[0]] else {})}

    def _iz_birak(self, kip: str, sahipler: list, ortak: dict) -> None:
        """Kosu izi — ASLA kosuyu dusurmez, yalnizca gozetim icin."""
        import json
        from pathlib import Path
        try:
            dizin = Path(self.s.root) / "data" / "bot" / "kosu"
            dizin.mkdir(parents=True, exist_ok=True)
            (dizin / f"{kip}.json").write_text(json.dumps({
                "kip": kip,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sahipler": list(sahipler),
                "piyasa_sinyali": int(ortak.get("piyasa_sinyali", 0)),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as e:                        # noqa: BLE001
            log.warning("[%s] kosu izi yazilamadi: %s", kip, e)

    # ------------------------------------------------------------------
    def _ortak_faz(self, kip: str) -> dict:
        """
        Kisiden BAGIMSIZ adimlar — bir kez kosar.

        1. `puanla()` TUM sahiplerin vadesi dolmus tahminlerini olcer.
           Deterministik ve LLM'siz; kisi basina kosturmak ayni isi N
           kere yapardi ve `ix_pred_olcum` bu yuzden bilerek sahipsiz.
        2. Piyasa taramasi BIR KEZ; sinyaller 'ortak' yazilir. Sinyal
           sayisi sahip sayisiyla ARTMAZ.
        """
        from .journal import Defter
        from .screener import Tarayici

        Defter(self.db).puanla()          # sahipsiz: hepsini puanlar
        tarayici = Tarayici(self.s, self.db)
        piyasa = tarayici.tara()          # sahipsiz: portfoy riski YOK
        tarayici.kaydet(piyasa)           # hepsi 'ortak'
        log.info("[%s] ortak faz: %d piyasa sinyali", kip, len(piyasa))
        return {"sinyaller": piyasa, "piyasa_sinyali": len(piyasa),
                "tarayici": tarayici}

    def _kisisel_faz(self, sahip: str, kip: str, bildir: bool,
                     panel: bool, ortak: dict) -> dict:
        """
        Bir sahibin adimlari. ADIM BAZINDA KISMI BASARI.

        Deterministik adimlar (portfoy riski, tez kontrolu) ile LLM
        adimlari (panel, hakem) AYRI sarilir: panel patlarsa tez alarmi
        yine gitmeli — tez kontrolu modele hic bagli degil ve
        kullanicinin en cok isine yarayan cikti o.
        """
        from .journal import Defter
        from ..llm import anlasilir_hata

        defter = Defter(self.db)

        # --- deterministik adimlar ---------------------------------------
        tarayici = ortak["tarayici"]
        portfoy = tarayici.portfoy_taramasi(sahip)
        if portfoy:
            tarayici.kaydet(portfoy, sahip)
        bozulan = defter.tez_kontrol(sahip)
        karne = defter.puanla(sahip)      # yalnizca karne; olcum ortakta

        sinyaller = list(ortak["sinyaller"]) + portfoy
        sinyaller.sort(key=lambda x: -x["guc"])
        guclu = [x for x in sinyaller if x["guc"] >= BILDIRIM_ESIGI]
        log.info("[%s/%s] %d sinyal (portfoy %d), tez %d",
                 kip, sahip, len(sinyaller), len(portfoy), len(bozulan))

        if not panel:
            return self._hafif(kip, bildir, sinyaller, guclu, bozulan,
                               karne, sahip)

        # RISK BILDIRIMI PANEL YOLUNDA DA VAR — onceden YOKTU.
        # `_yeni_riskler` (ve dolayisiyla `bildirim_durumu` bastirmasi)
        # yalnizca `_hafif` dalindaydi; `panel: true` yapilan an
        # yogunlasma/acik_zarar alarmlari TAMAMEN kaybolurdu.
        riskler = self._yeni_riskler(
            [x for x in sinyaller if x["tur"] in RISK_TURLERI], sahip,
            yaz=bildir)

        # --- LLM adimlari — AYRI sarili ----------------------------------
        # Panel patlasa da OZET GIDER: alarm bolumu yukarida, panelden
        # BAGIMSIZ hesaplandi. "Tez kontrolu modele hic bagli degil"
        # ilkesi, mesaj katmaninda da gecerli olmali.
        panel_notu, sonuc, n_tahmin, hakem_id = None, {}, 0, None
        if not guclu:
            log.info("[%s/%s] esigi gecen sinyal yok — panel kosmadi",
                     kip, sahip)
            panel_notu = "Panel: esigi gecen sinyal yok, model calistirilmadi."
        else:
            try:
                sonuc, n_tahmin, hakem_id = self._panel_fazi(
                    sahip, kip, guclu, defter)
            except Exception as e:                    # noqa: BLE001
                log.exception("[%s/%s] panel patladi", kip, sahip)
                panel_notu = ("Panel calismadi: "
                              + anlasilir_hata(e, self.s)[:300]
                              + " — tez alarmi ve portfoy riski etkilenmedi.")

        if bildir:
            self._ozet_bildir(kip, sahip, bozulan=bozulan, riskler=riskler,
                              sade=sonuc.get("sade"), ozet=sonuc.get("ozet"),
                              karne=karne, n_tahmin=n_tahmin,
                              hakem_id=hakem_id, panel_notu=panel_notu)

        cikti = {"sinyal": len(sinyaller), "guclu": len(guclu),
                 "karne": karne, "ozet": sonuc.get("ozet"),
                 "tahmin": n_tahmin, "tez_bozuldu": len(bozulan),
                 "risk": len(riskler), "ajanlar": sonuc.get("ajanlar", {})}
        if panel_notu and guclu:
            cikti["panel_hatasi"] = panel_notu
        return cikti

    def _panel_fazi(self, sahip, kip, guclu, defter) -> tuple:
        """
        Paneli kosturur ve deftere yazar. MESAJ GONDERMEZ.

        Gonderim `_kisisel_faz`'a tasindi: panel patlasa bile ozet
        gitmeli ve alarm bolumu panelden BAGIMSIZ hesaplanmali.
        Doner: (panel sonucu, yazilan tahmin sayisi, hakem satir id'si)
        """
        import anyio
        from .agents import Panel

        gundem = self._gundem(guclu, sahip)
        sonuc = anyio.run(Panel(self.s, self.db, sahip).calistir, gundem)

        # Hakemin cagrisi AYRICA kaydedilir: kullanicinin OKUDUGU sey odur.
        rapor = defter.kaydet(sonuc.get("gorusler") or [], sahip)
        hakem_rapor = defter.kaydet(sonuc.get("hakem_gorusler") or [], sahip)
        n_tahmin = rapor["yazilan"] + hakem_rapor["yazilan"]
        log.info("[%s/%s] tahmin: ajanlar %s · hakem %s",
                 kip, sahip, rapor, hakem_rapor)
        self._atilanlari_isle(rapor, hakem_rapor,
                              sonuc.get("panel_idleri") or {})
        hakem_id = (sonuc.get("panel_idleri") or {}).get("hakem")
        return sonuc, n_tahmin, hakem_id

    def _hata_metni(self, kip: str, e: Exception) -> str:
        from ..llm import anlasilir_hata
        return (f"🔴 <b>{kip} kosusu patladi</b>\n\n"
                f"<i>{_esc(anlasilir_hata(e, self.s)[:400])}</i>")

    # ------------------------------------------------------------------
    # BILDIRIM YONLENDIRME — tek dogruluk kaynagi `telegram.sahipler`.
    #
    # Ikinci bir yonlendirme ayari ACILMADI: iki liste kacinilmaz olarak
    # ayrisir ve "kosu calisti ama mesaj kimseye gitmedi" durumunu
    # uretir. Yetkilendirme ve yonlendirme AYNI esleme.
    # ------------------------------------------------------------------
    def _sahibe_bildir(self, sahip: str, metin: str,
                       reply_markup: dict | None = None) -> bool:
        """
        Bir sahibin TUM sohbetlerine gonderir. Doner: en az biri gitti mi.

        Gonderim basarisizligi (ag, blok, gecersiz chat_id) DIGER sahibi
        etkilemez; yalnizca loglanir ve donus degerine yansir.

        `reply_markup` yalnizca ILK sohbete konur: buton bir SATIR ID'si
        tasiyor ve ayni id'yi birden cok sohbete koymak, ikinci sohbetin
        de ayni teknik detayi acmasi demek — sahip ayni oldugu icin
        yetki sorunu degil ama tekrar eden buton gurultudur.
        """
        from ..notify import TelegramNotifier

        chatler = self.s.sahip_chatleri(sahip)
        if not chatler:
            # Kosup bildirimi kaybetmek, hic kosmamaktan KOTU: LLM
            # butcesi harcanir, cikti kimseye gitmez.
            log.error("[bildirim] '%s' sahibinin chat_id'si eslemede YOK — "
                      "mesaj gonderilemedi", sahip)
            return False
        tg = TelegramNotifier(self.s)
        giden = False
        for i, chat in enumerate(chatler):
            try:
                giden = tg.send_message(
                    metin, chat_id=chat,
                    reply_markup=reply_markup if i == 0 else None) or giden
            except Exception as e:                    # noqa: BLE001
                log.warning("[bildirim] %s/%s gonderilemedi: %s",
                            sahip, chat, e)
        return giden

    def _herkese_bildir(self, metin: str) -> None:
        """Sistem olaylari (ortak faz hatasi, kesinti) — TUM sahiplere."""
        for sahip in self.s.sahip_listesi:
            self._sahibe_bildir(sahip, metin)

    # ------------------------------------------------------------------
    def _hafif(self, kip, bildir, sinyaller, guclu, bozulan, karne,
               sahip: str | None = None) -> dict:
        """
        HAFIF KIP — LLM YOK.

        Sabah ve oglen kosulari icin. Icerik yoruma ihtiyac duymuyor:
        "ROSE gunluk oynakliginin 2,8 kati dustu, hacim teyitli, portfoy
        agirligin %18" cumlesi deterministik ve TAM. Modelden gecirmek
        onu daha dogru yapmaz, yalnizca daha uzun yapar ve butceyi uce
        katlar. Projenin kurucu ayriminin devami: deterministik katman
        hesaplar, LLM yorumlar; yorumlanacak bir sey yoksa cagrilmaz.

        BILDIRIM ESIGI DAHA DAR: yalnizca SAHIP OLUNAN enstrumanlar.
        Sabah 09:30'da BIST'te bir kagidin hareket etmesi, uzerinde
        pozisyonun yoksa acil degil ve aksam paneli zaten bakacak;
        portfoyunde bir sey olmasi acildir.
        """
        sahibin = self.db.sahip_pozisyon_idleri(sahip) if sahip else set()
        # RISKLER SINYAL LISTESINE GIRMEZ. Ikisi de `sinyaller` icinden
        # geliyor ve `guclu` filtresi turu ayirt etmiyordu: `yogunlasma`
        # ve `acik_zarar` hem madde listesine hem ⚠️ risk bolumune
        # dusuyordu — AYNI SEY IKI KEZ. Gruplama bunu gorunur yapti:
        # USDT/TRALT/ASML/NOW kanit satiri olmayan bos bloklar olarak
        # cikti, cunku bu turlerin bar bazli bir kaniti yok.
        portfoyde = [x for x in guclu
                     if x.get("instrument_id") in sahibin
                     and x.get("tur") not in RISK_TURLERI]

        # IKI SUZGEC, IKI AYRI SORU — sirasi onemli:
        #   1. TAZE MI?   Eski bir olayin etkisi bugunun haberi degildir.
        #   2. YENI MI?   Ayni barin ayni sinyali iki kez bildirilmez.
        # Once tazelik: bayat bir sinyali "yeni" diye kaydedip sonra
        # elemek, bastirma tablosuna hic bildirilmemis bir satir yazardi.
        taze, bayat = self._taze_sinyaller(portfoyde)
        # `yaz=bildir`: bildirim gitmiyorsa "bildirildi" isareti de
        # konmaz — yoksa `--no-notify` ile yapilan bir olcum kosusu bir
        # sonraki GERCEK kosuyu susturur.
        portfoy_sinyali = self._yeni_sinyaller(taze, sahip, yaz=bildir)
        riskler = self._yeni_riskler(
            [x for x in sinyaller if x["tur"] in RISK_TURLERI], sahip,
            yaz=bildir)

        log.info("[%s] hafif kip: %d sinyal, portfoyde %d (bayat %d, tekrar "
                 "%d, bildirilecek %d), risk %d, tez %d",
                 kip, len(sinyaller), len(portfoyde), len(bayat),
                 len(taze) - len(portfoy_sinyali), len(portfoy_sinyali),
                 len(riskler), len(bozulan))

        if bildir and (bozulan or portfoy_sinyali or riskler):
            self._hafif_bildir(kip, bozulan, portfoy_sinyali, riskler,
                               sahip)
        elif bildir:
            # SESSIZLIK GECERLI CIKTI. "Bugun bir sey olmadi" mesaji
            # gondermek, bildirimin degerini asindiran seydir.
            log.info("[%s] kriter saglanmadi — mesaj YOK", kip)

        return {"kip": kip, "sinyal": len(sinyaller), "guclu": len(guclu),
                "portfoy_sinyali": len(portfoy_sinyali),
                "risk": len(riskler), "tez_bozuldu": len(bozulan),
                "karne": karne, "ozet": None, "tahmin": 0}

    # Risk bildiriminin tekrari icin esik, YUZDE PUANI.
    #
    # Neden oynakliga gore OLCEKLENMIYOR (projenin her yerdeki
    # disiplininin aksine): bu iki deger de PORTFOY ANLIK GORUNTUSUNDEN
    # geliyor — `yogunlasma` pozisyon degerlerinden, `acik_zarar`
    # `pnl_pct` alanindan. Ikisi de yalnizca YENI EKRAN GORUNTUSU
    # geldiginde degisir; arada BASAMAK FONKSIYONUDUR, gunluk fiyat
    # oynakligiyla suruklenmez. Dolayisiyla asil is tekillestirmede;
    # esik yalnizca goruntuden goruntuye onemsiz farklarda tekrar
    # bildirimi engelliyor. Oynakliga gore olcekleme burada olmayan
    # bir hareketi modellemek olurdu.
    RISK_TEKRAR_ESIGI = 3.0

    def _yeni_riskler(self, riskler: list[dict], sahip: str,
                      yaz: bool = True) -> list[dict]:
        """
        Yalnizca DURUMU DEGISEN riskleri dondurur.

        Portfoy riski bir olay degil DURUMDUR: ASML portfoyun %40'iysa
        bu bugun de yarin da dogru. Bastirma olmadan gunde iki hafif
        kosu ayni cumleyi tekrarlar ve kullanici bildirimleri kapatir.
        Tez alarmindaki `tez_bozuldu_ts` ile ayni problem.

        `yaz=False` ise SONUC AYNI ama BASTIRMA TABLOSUNA DOKUNULMAZ.
        Gerekce olculdu: `--no-notify` kosulari (kip suresi olcumu,
        elle deneme, test) tabloyu dolduruyordu ve BIR SONRAKI GERCEK
        kosu o kayitlar yuzunden susuyordu. "Bildirildi" isareti ancak
        mesaj GERCEKTEN gittiginde konmali.
        """
        if not riskler:
            return []
        # SAHIBE GORE SUZ. Sahipsiz okuma, A'nin bastirma satirini B'nin
        # riski sanip B'yi susturuyordu — tablo tam da bunu engellemek
        # icin var.
        onceki = {(r["instrument_id"], r["tur"]): r["son_deger"]
                  for r in self.db.query(
                      "SELECT instrument_id, tur, son_deger FROM "
                      "bildirim_durumu WHERE sahip = ?", (sahip,))}
        yeni, yazilacak = [], []
        for r in riskler:
            deger = self._risk_degeri(r)
            if deger is None:
                continue
            anahtar = (r["instrument_id"], r["tur"])
            eski_deger = onceki.get(anahtar)
            if eski_deger is not None and \
                    abs(deger - eski_deger) < self.RISK_TEKRAR_ESIGI:
                continue                     # durum degismedi, SUS
            yeni.append(r)
            yazilacak.append((r["instrument_id"], r["tur"], deger))
        if yazilacak and yaz:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO bildirim_durumu
                       (sahip, instrument_id, tur, son_deger, son_bildirim_ts)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(sahip, instrument_id, tur) DO UPDATE SET
                         son_deger = excluded.son_deger,
                         son_bildirim_ts = excluded.son_bildirim_ts""",
                    [(sahip, i, tur, d, ts) for i, tur, d in yazilacak])
        if len(riskler) != len(yeni):
            log.info("[nabiz] risk bildirimi bastirildi: %d/%d degismemis",
                     len(riskler) - len(yeni), len(riskler))
        return yeni

    @staticmethod
    def _risk_degeri(r: dict) -> float | None:
        """Riskin izlenen SAYISI — turu belirler."""
        k = r.get("kanit") or {}
        return k.get("agirlik_%") if r["tur"] == "yogunlasma" else k.get("kz_%")

    # ------------------------------------------------------------------
    # BILDIRIM SUZGECLERI
    # ------------------------------------------------------------------
    # Bildirime girecek olay etkisi EN FAZLA bu kadar eski olabilir.
    #
    # `analysis/events.py::haber_etkileri` 120 GUNLUK pencereye bakiyor
    # ve bu ANALIZ icin dogru: "bu kagit haberlere nasil tepki veriyor"
    # sorusunun cevabi uzun gecmis ister. BILDIRIM baska bir sey soyler —
    # "su an dikkat et". 2026-08-19'da 30 Temmuz'daki AMZN olayi 20
    # gundur her kosuda bildirime dusuyordu ve tarihi de yazilmadigi icin
    # BUGUNUN haberi gibi okunuyordu.
    #
    # 3 GUN: bir olayin fiyata yansimasi icin olculen pencere zaten
    # t+1..t+3 (bkz. `analysis/events.py` olay penceresi). Bundan
    # eskisinde "su an dikkat et" demenin dayanagi kalmiyor.
    BILDIRIM_OLAY_AZAMI_GUN = 3

    def _taze_sinyaller(self, sinyaller: list[dict]) -> tuple[list, list]:
        """
        (bildirilebilir, bayat) — YASI OLCULEBILEN ve gecmis sinyalleri ayirir.

        Yalnizca `olay_etkisi` yaslanir: digerleri zaten SON BARIN
        olayidir, yasi barin kendisidir. Yasi OLCULEMEYEN olay
        (`olay_gun_once` None) bayat sayilir — bilinmeyen bir tarihi
        "taze" varsaymak, tam da bu hatanin kaynagiydi.
        """
        taze, bayat = [], []
        for s in sinyaller:
            if s.get("tur") != "olay_etkisi":
                taze.append(s)
                continue
            gun = (s.get("kanit") or {}).get("olay_gun_once")
            if gun is None or gun > self.BILDIRIM_OLAY_AZAMI_GUN:
                bayat.append(s)
                log.info("[nabiz] %s olay_etkisi bildirilmedi: olay %s "
                         "(%s gun once)", s.get("sembol"),
                         (s.get("kanit") or {}).get("olay_tarihi"), gun)
            else:
                taze.append(s)
        return taze, bayat

    # Sinyal turu -> (izlenen kanit alani, anahtara giren kimlik alani)
    #
    # ANAHTAR NEDEN TARIH ICERIYOR: bu sinyaller DURUM degil OLAYDIR ve
    # kimlikleri sayilari degil, ait olduklari bardir. Yalnizca degere
    # bakan bir bastirma su hatayi yapardi: AVTX bugun -%19,91 dustu
    # (bildirildi), yarin -%19,50 daha duser (fark 0,41 puan, esigin
    # altinda) ve IKINCI COKUS SUSTURULURDU. Tarih anahtarda oldugu icin
    # yeni bar = yeni satir = yeni bildirim.
    SINYAL_IZLEME = {
        "olagandisi_hareket": ("gunluk_getiri_%", "bar_ts"),
        "hacim_anomalisi":    ("hacim_kati",      "bar_ts"),
        "sma50_kirilimi":     ("kapanis",         "bar_ts"),
        "rsi_ucu":            ("rsi14",           "bar_ts"),
        "olay_etkisi":        ("car_%",           "olay_tarihi"),
    }

    def _sinyal_anahtari(self, s: dict) -> str | None:
        """`bildirim_durumu.tur` sutununa yazilacak anahtar."""
        alanlar = self.SINYAL_IZLEME.get(s.get("tur"))
        if not alanlar:
            return None
        _, kimlik_alani = alanlar
        kimlik = (s.get(kimlik_alani)
                  or (s.get("kanit") or {}).get(kimlik_alani))
        if not kimlik:
            # KIMLIKSIZ SINYAL BASTIRILMAZ. Sabit bir anahtar uydurmak,
            # farkli barlarin sinyallerini ayni satira yazip ikincisini
            # susturmak olurdu.
            return None
        # `sinyal:` oneki ZORUNLU: ayni tablo `yogunlasma`/`acik_zarar`
        # risk satirlarini da tutuyor ve anahtar uzaylari karismamali.
        return f"sinyal:{s['tur']}:{kimlik}"

    def _sinyal_degeri(self, s: dict) -> float | None:
        alanlar = self.SINYAL_IZLEME.get(s.get("tur"))
        if not alanlar:
            return None
        deger = (s.get("kanit") or {}).get(alanlar[0])
        return float(deger) if isinstance(deger, (int, float)) else None

    def _sinyal_esigi(self, s: dict) -> float:
        """
        AYNI anahtar icinde yeniden bildirim icin gereken degisim.

        Anahtar tarihi icerdigi icin bu esik yalnizca GUN ICI surukleniye
        bakar: sabah kismi bar (-%5), aksam tam bar (-%19,91). Ikincisi
        gercekten yeni bilgidir ve bildirilmelidir.

        Esikler turun KENDI biriminde; ortak bir sayi yok, cunku "5"
        yuzde puaninda buyuk, hacim katinda kucuk, RSI'da ortadir.
        """
        tur = s.get("tur")
        if tur == "olagandisi_hareket":
            # Enstrumanin KENDI oynakligi: %1'lik kayma USDTRY'de buyuk,
            # bir memecoin'de gurultudur. Taban 0,5 puan — oynakligi
            # sifira yakin bir seride her kirinti bildirim uretmesin.
            return max(0.5, float(s.get("gunluk_oynaklik_%") or 0))
        if tur == "hacim_anomalisi":
            # Goreli: 2x -> 3x haberdir, 11x -> 12x degildir.
            olcek = abs(self._sinyal_degeri(s) or 1.0)
            return max(0.5, 0.5 * olcek)
        if tur == "rsi_ucu":
            return 5.0
        if tur == "olay_etkisi":
            return 2.0
        if tur == "sma50_kirilimi":
            # KIRILIM BIR ANDIR, seviye degil. Ayni barda "daha cok
            # kirildi" diye bir sey yok; fiyat oynadi diye tekrar
            # bildirmek yanlis olur.
            return float("inf")
        return float("inf")

    def _yeni_sinyaller(self, sinyaller: list[dict], sahip: str,
                        yaz: bool = True) -> list[dict]:
        """
        Yalnizca DAHA ONCE BILDIRILMEMIS (ya da anlamli degismis) sinyaller.

        `_yeni_riskler` ile ayni tabloyu ve ayni gerekceyi paylasiyor;
        fark, riskin bir DURUM, sinyalin bir OLAY olmasi — o yuzden
        anahtar tarih iceriyor (bkz. `SINYAL_IZLEME`).

        Bu suzgec yoktu: 2026-08-19'da sabah 09:31 kosusu portfoyde 10
        sinyal bildirdi, aksam 18:14 kosusu ayni gunun barlarindan 16
        sinyal bildirdi. Ali ayni gun ayni haberi iki kez aldi.

        `yaz=False` ise SONUC AYNI ama BASTIRMA TABLOSUNA DOKUNULMAZ.
        Gerekce olculdu: `--no-notify` kosulari (kip suresi olcumu,
        elle deneme, test) tabloyu dolduruyordu ve BIR SONRAKI GERCEK
        kosu o kayitlar yuzunden susuyordu. "Bildirildi" isareti ancak
        mesaj GERCEKTEN gittiginde konmali.
        """
        if not sinyaller:
            return []
        onceki = {(r["instrument_id"], r["tur"]): r["son_deger"]
                  for r in self.db.query(
                      "SELECT instrument_id, tur, son_deger FROM "
                      "bildirim_durumu WHERE sahip = ?", (sahip,))}
        yeni, yazilacak = [], []
        for s in sinyaller:
            anahtar = self._sinyal_anahtari(s)
            deger = self._sinyal_degeri(s)
            if anahtar is None or deger is None:
                # BASTIRILAMAYAN SINYAL BILDIRILIR. Suzgecin bilmedigi
                # bir tur eklendiginde sessizlik degil GURULTU olsun:
                # eksik bildirim, tekrar bildirimden pahalidir.
                yeni.append(s)
                log.info("[nabiz] %s/%s bastirma disi (anahtar/deger yok)",
                         s.get("sembol"), s.get("tur"))
                continue
            eski = onceki.get((s["instrument_id"], anahtar))
            if eski is not None and abs(deger - eski) < self._sinyal_esigi(s):
                continue
            yeni.append(s)
            yazilacak.append((s["instrument_id"], anahtar, deger))
        if yazilacak and yaz:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO bildirim_durumu
                       (sahip, instrument_id, tur, son_deger, son_bildirim_ts)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(sahip, instrument_id, tur) DO UPDATE SET
                         son_deger = excluded.son_deger,
                         son_bildirim_ts = excluded.son_bildirim_ts""",
                    [(sahip, i, t, d, ts) for i, t, d in yazilacak])
        return yeni

    # Koşunun ADI — piyasa durumu HAKKINDA HICBIR IDDIA TASIMAZ.
    #
    # Eskiden "ogle" -> "🕕 Kapanis"ti ve bu, 18:00 slotunun takma
    # adiydi. Mesaj uc ayri borsadan enstruman tasidigi icin baslik
    # listenin ilk satiri (AMZN, ABD seansi ACIK) icin YANLIS bir durum
    # ilan ediyordu. Buradaki adlar artik yalnizca KOSUNUN AMACINI
    # soyluyor (hangi kaynaklar icin zamanlandigini); piyasalarin
    # gercek durumu bir alt satirda OLCULEREK yaziliyor.
    # Ritim v2 saatleri (Europe/Amsterdam): sabah 08:00, ogle 12:30,
    # kapanis 17:45, nabiz 22:15. Adlar KOSUNUN AMACINI soyluyor,
    # piyasa durumunu DEGIL — durum bir alt satirda olculuyor.
    KOSU_ADI = {"sabah": "🌅 Sabah taramasi",
                "ogle": "🕛 Gun ortasi taramasi",
                "kapanis": "🔔 Avrupa kapanisi sonrasi tarama",
                "nabiz": "📊 Gece nabzi"}

    # Tek mesajda gosterilecek en fazla ENSTRUMAN (sinyal degil).
    # Gruplama sonrasi olctu: 19 Agustos bildirimi 4 satir yerine 2
    # enstruman olurdu. Tasarsa SESSIZ KESILMEZ, sayisi yazilir.
    HAFIF_AZAMI_ENSTRUMAN = 6
    HAFIF_AZAMI_RISK = 3

    def _hafif_bildir(self, kip, bozulan, portfoy_sinyali, riskler,
                      sahip: str) -> None:
        from ..piyasa import durum_satiri

        # TEK SAAT OKUMASI. Basliktaki zaman ile seans satiri ayri ayri
        # `now()` cagirsaydi, dakika sinirinda birbiriyle celisen iki
        # zaman yazabilirdi — kucuk ama tam da bu mesajin sikayet
        # konusu olan sinifindan bir tutarsizlik.
        simdi = datetime.now(timezone.utc)
        yerel = simdi.astimezone()
        L = [f"<b>{self.KOSU_ADI.get(kip, kip)}</b> · "
             f"{yerel.strftime('%d.%m.%Y %H:%M')}",
             f"<i>{durum_satiri(simdi)}</i>",
             "<i>Tatil takvimi yok: 'acik' = hafta ici ve seans saati.</i>"]

        for b in bozulan:
            L.append(f"\n🔔 <b>{_esc(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {_esc(str(b['tez'])[:200])}</i>")
            L.append(f"Kosul <code>{_esc(b['kosul'])}</code> · "
                     f"su anki {b['alan']}: <b>{_kisa(b['deger'])}</b>")

        gruplar = self._sinyal_gruplari(portfoy_sinyali)
        for grup in gruplar[:self.HAFIF_AZAMI_ENSTRUMAN]:
            L.append("\n" + self._grup_metni(grup))
        if len(gruplar) > self.HAFIF_AZAMI_ENSTRUMAN:
            # SESSIZ KESME YOK: eksik oldugu soylenmeyen liste, TAM
            # sanilir. Ayni ders `isyatirim` kesilmesinde ogrenildi.
            L.append(f"\n<i>… ve {len(gruplar) - self.HAFIF_AZAMI_ENSTRUMAN} "
                     "enstrumanda daha sinyal var.</i>")

        for r in riskler[:self.HAFIF_AZAMI_RISK]:
            k = r.get("kanit") or {}
            L.append(f"\n⚠️ <b>{_esc(r['sembol'])}</b> {r['tur']}"
                     + (f" · agirlik %{k.get('agirlik_%')}" if k.get("agirlik_%")
                        else "")
                     + (f" · K/Z %{k.get('kz_%')}" if k.get("kz_%") else ""))
        if len(riskler) > self.HAFIF_AZAMI_RISK:
            L.append(f"\n<i>… ve {len(riskler) - self.HAFIF_AZAMI_RISK} risk "
                     "daha.</i>")

        L.append("\n<i>Bu koşuda model calismadi — yalnizca olculen esikler.</i>")
        self._sahibe_bildir(sahip, "\n".join(L))

    @staticmethod
    def _sinyal_gruplari(sinyaller: list[dict]) -> list[list[dict]]:
        """
        Sinyalleri ENSTRUMANDA toplar; guclu enstruman once.

        Onceden satir basina BIR SINYAL yaziliyordu ve `[:4]` ile
        kesiliyordu. 19 Agustos'ta AVTX tek basina uc satir tuttu —
        bildirimin %75'i — ve dorduncu sinyali (RSI) sessizce dustu.
        Oysa ucu de AYNI OLAYIN olcumuydu: ayni gunun -%19,91'i.
        Hacim anomalisinin kaniti bile ayni sayiyi tasiyor.
        """
        gruplar: dict = {}
        for s in sinyaller:
            gruplar.setdefault(s.get("instrument_id"), []).append(s)
        for g in gruplar.values():
            g.sort(key=lambda x: -(x.get("guc") or 0))
        return sorted(gruplar.values(),
                      key=lambda g: -(g[0].get("guc") or 0))

    def _grup_metni(self, grup: list[dict]) -> str:
        """Tek enstruman, tek blok: kimlik satiri + KANIT satiri."""
        bas = grup[0]
        ok = {"yukari": "🔺", "asagi": "🔻"}.get(bas.get("yon"), "•")

        from ..piyasa import borsa_coz
        try:
            borsa = borsa_coz(self.db, bas.get("instrument_id"),
                              bas.get("venue"))
        except Exception as e:                        # noqa: BLE001
            # Borsa cozumu bir SUS bilgisidir; bildirimi dusurmemeli.
            log.warning("[nabiz] borsa cozulemedi (%s): %s",
                        bas.get("sembol"), e)
            borsa = None

        kimlik = [f"{ok} <b>{_esc(bas.get('sembol'))}</b>"]
        if bas.get("ad") and str(bas["ad"]).upper() != str(bas.get("sembol")).upper():
            kimlik.append(_esc(str(bas["ad"])[:40]))
        # BORSA YALNIZCA BILINIYORSA yazilir. Bilinmeyeni "BUX" diye
        # yazmak yanlis olurdu: BUX bir araci kurum, piyasa degil.
        if borsa:
            kimlik.append(borsa)
        kimlik.append("portfoyunde")

        kanitlar = [m for m in (self._kanit_metni(s) for s in grup) if m]
        bar = _tarih_kisa(bas.get("bar_ts"))
        onek = f"{bar} bari: " if bar else ""
        return (" · ".join(kimlik)
                + (f"\n   {onek}" + " · ".join(kanitlar) if kanitlar else ""))

    @staticmethod
    def _kanit_metni(s: dict) -> str | None:
        """
        Sinyalin SAYISI. `_hafif`in docstring'i bunu zaten vaat ediyordu
        ("ROSE gunluk oynakliginin 2,8 kati dustu, hacim teyitli") ama
        mesaja yalnizca `(tur, yon)` yaziliyordu — kanit veritabaninda
        kaliyor, kullaniciya ulasmiyordu.
        """
        k = s.get("kanit") or {}
        tur = s.get("tur")
        if tur == "olagandisi_hareket":
            g, sig = k.get("gunluk_getiri_%"), k.get("sigma")
            if not isinstance(g, (int, float)):
                return None
            metin = _yuzde_tr(g)
            if isinstance(sig, (int, float)):
                metin += f" ({'+' if sig >= 0 else '-'}{_tr(abs(sig), 1)}σ)"
            return metin
        if tur == "hacim_anomalisi":
            v = k.get("hacim_kati")
            return (f"hacim {_tr(v, 1)}×"
                    if isinstance(v, (int, float)) else None)
        if tur == "rsi_ucu":
            v = k.get("rsi14")
            return f"RSI {_tr(v, 1)}" if isinstance(v, (int, float)) else None
        if tur == "sma50_kirilimi":
            yon = "yukari" if s.get("yon") == "yukari" else "asagi"
            return f"SMA50 {yon} kirildi"
        if tur == "olay_etkisi":
            car, t = k.get("car_%"), k.get("t")
            if not isinstance(car, (int, float)):
                return None
            metin = f"olay etkisi CAR {_yuzde_tr(car, 1)}"
            if isinstance(t, (int, float)):
                metin += f" (t {'+' if t >= 0 else '-'}{_tr(abs(t), 1)})"
            gun = k.get("olay_gun_once")
            tarih = _tarih_kisa(k.get("olay_tarihi"))
            if tarih:
                # OLAYIN TARIHI HER ZAMAN YAZILIR. Tazelik suzgeci
                # zaten eskiyi eliyor, ama gecen 1-3 gunluk olay da
                # "bugun oldu" diye okunmamali.
                metin += f" — olay {tarih}"
                if isinstance(gun, int) and gun > 0:
                    metin += f", {gun} gun once"
            return metin
        return None

    def _gundem(self, guclu: list[dict], sahip: str | None = None) -> list[dict]:
        """
        Panele gidecek gozlemleri secer: once PORTFOY, sonra guc.

        Saf "en guclu N" secimi evren buyuklugunu gizli bir agirlik gibi
        iceri sokuyor. Olculdu: 251 BIST sembolu 19 BUX ve 67 kripto
        sembolunu bogup panelin 12 slotunun 10'unu aliyordu — ustelik
        BIST'te tek pozisyon ve Midas'ta bakiye YOK, yani kapasitenin
        %83'u islem yapilamayan kagitlara gidiyordu.

        Sahip olunan enstrumanlara PORTFOY_ASGARI_SLOT kadar yer ayrilir;
        o kadar sinyal yoksa artan slot geri verilir — kota doldurmak icin
        zayif sinyal YUKSELTILMEZ. Kalan yerler yine guce gore dolar,
        yani BIST tamamen disarida kalmaz.
        """
        if not guclu:
            return []
        sahibin = self.db.sahip_pozisyon_idleri(sahip) if sahip else set()
        portfoy = [x for x in guclu if x.get("instrument_id") in sahibin]
        secilen = portfoy[:PORTFOY_ASGARI_SLOT]
        kimlik = {id(x) for x in secilen}
        for x in guclu:                        # kalan slotlar guce gore
            if len(secilen) >= PANEL_ADAY:
                break
            if id(x) not in kimlik:
                secilen.append(x)
                kimlik.add(id(x))
        # Guc sirasi korunur ki hakem ve ajanlar onemi siralamadan okusun.
        secilen.sort(key=lambda x: -x["guc"])
        log.info("[nabiz] gundem: %d gozlem (portfoy %d), venue %s",
                 len(secilen), sum(1 for x in secilen
                                   if x.get("instrument_id") in sahibin),
                 {v: sum(1 for x in secilen if x["venue"] == v)
                  for v in sorted({x["venue"] for x in secilen})})
        return secilen

    def _atilanlari_isle(self, rapor: dict, hakem_rapor: dict,
                         panel_idleri: dict) -> None:
        """
        Defterin attigi gorusleri o kosunun `panel_runs` SATIRLARINA yazar.

        ID ILE, ZAMAN DAMGASIYLA DEGIL. Once "o kosunun en son run_ts'i"
        araniyordu ve bu, panellerin SIRAYLA kosmasi sayesinde dogruydu —
        tasarimdan degil TESADUFTEN. Iki sahibin damgasi ayni saniyeye
        duserse sayaclar BASKASININ satirina yazilirdi. `panel_idleri`
        yazan tarafin dondurdugu gercek satir kimlikleri; eslesme
        varsayimi tamamen ortadan kalkiyor.

        HER AJAN KENDI SAYISINI TASIR: kosunun toplamini tek satira
        yazmak, "yanlis dagitilmis bir sayi hic yazilmamis olandan
        kotudur" kuralini cignerdi.
        """
        if not panel_idleri:
            return
        try:
            with self.db.tx() as c:
                for kaynak in (rapor, hakem_rapor):
                    for ajan, d in (kaynak.get("ajan_bazli") or {}).items():
                        satir = panel_idleri.get(ajan)
                        if satir is None:
                            # Ajan panelde kosmadiysa yazacak satir yok;
                            # sayiyi baska satira ITMEK yanlis olurdu.
                            log.warning("[nabiz] '%s' icin panel_runs satiri "
                                        "yok, sayac yazilmadi", ajan)
                            continue
                        c.execute(
                            """UPDATE panel_runs SET
                                 atilan_sembol_yok = ?, atilan_seri_yok = ?,
                                 atilan_cakisma = ?
                               WHERE id = ?""",
                            (d["atilan_sembol_yok"], d["atilan_seri_yok"],
                             d["atilan_cakisma"], satir))
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] atilan sayaclari yazilamadi: %s", e)

    # ------------------------------------------------------------------
    # OZET MESAJI — her kosuda, her aliciya, TEK mesaj.
    #
    # NEDEN TEK MESAJ VE NEDEN PANELDEN BAGIMSIZ
    #   Once iki ayri mesaj gidiyordu: tez alarmi (deterministik) ve
    #   panel ozeti (LLM). Ritim v2 gunde dort kosu getiriyor, yani
    #   gunde sekiz mesaj. Ustelik PANEL YOLUNDA risk bildirimi HIC
    #   YOKTU: tazelik suzgeci, bastirma, seans satiri ve gruplama
    #   yalnizca `_hafif` dalinda vardi (runner.py:421-433). `panel:
    #   true` yapilan an sabah ve ogle kosulari o duzeltmelerin DISINA
    #   cikardi.
    #
    #   Cozum: tek gövde, ve ALARM BOLUMU PANELDEN ONCE hesaplaniyor.
    #   Panel patlasa bile mesaj gider ve tez/risk satirlari icinde
    #   olur — "tez kontrolu modele hic bagli degil" ilkesi korunur.
    # ------------------------------------------------------------------
    def _ozet_bildir(self, kip: str, sahip: str, *, bozulan: list[dict],
                     riskler: list[dict], sade: str | None, ozet: str | None,
                     karne: dict, n_tahmin: int, hakem_id: int | None,
                     panel_notu: str | None = None) -> None:
        from ..notify.telegram import md_to_tg_html
        from ..piyasa import durum_satiri

        # TEK SAAT OKUMASI: baslik ile seans satiri dakika sinirinda
        # birbiriyle celisen iki zaman yazmasin.
        simdi = datetime.now(timezone.utc)
        yerel = simdi.astimezone()
        L = [f"<b>{self.KOSU_ADI.get(kip, kip)}</b> · "
             f"{yerel.strftime('%d.%m.%Y %H:%M')}",
             f"<i>{durum_satiri(simdi)}</i>",
             "<i>Tatil takvimi yok: 'acik' = hafta ici ve seans saati.</i>"]

        for satir in self._portfoy_satirlari(sahip):
            L.append(satir)
        for satir in self._makro_satirlari():
            L.append(satir)

        # --- ALARM: deterministik, panelden BAGIMSIZ --------------------
        for b in bozulan:
            L.append(f"\n🔔 <b>{_esc(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {_esc(str(b['tez'])[:200])}</i>")
            L.append(f"Kosul <code>{_esc(b['kosul'])}</code> · "
                     f"su anki {b['alan']}: <b>{_kisa(b['deger'])}</b>")
        for r in riskler[:self.HAFIF_AZAMI_RISK]:
            k = r.get("kanit") or {}
            L.append(f"\n⚠️ <b>{_esc(r['sembol'])}</b> {r['tur']}"
                     + (f" · agirlik %{k.get('agirlik_%')}" if k.get("agirlik_%")
                        else "")
                     + (f" · K/Z %{k.get('kz_%')}" if k.get("kz_%") else ""))
        if len(riskler) > self.HAFIF_AZAMI_RISK:
            L.append(f"\n<i>… ve {len(riskler) - self.HAFIF_AZAMI_RISK} risk "
                     "daha.</i>")

        # --- PANEL ------------------------------------------------------
        govde = md_to_tg_html(sade if sade else (ozet or ""))
        if panel_notu:
            L.append(f"\n🧠 <i>{_esc(panel_notu)}</i>")
        elif govde.strip():
            L.append(f"\n🧠 <b>Panel</b>\n{govde}")
        else:
            # SESSIZLIK GECERLI CIKTI ama SESSIZ MESAJ DEGIL: kullanici
            # gunde dort mesaj bekliyor; gitmeyen mesaj bekciye
            # "kosmadi" gibi, kullaniciya "bozuk" gibi gorunur.
            L.append("\n🧠 <i>Panel: one cikan bir sey bulmadi.</i>")

        L.extend(self._karne_satirlari(karne, n_tahmin))
        markup = None
        if hakem_id:
            # BUTON SATIR ID'SI TASIR, zaman damgasi degil: iki sahibin
            # damgasi ayni saniyeye duserse damga tabanli arama
            # BASKASININ teknik detayini acardi.
            markup = {"inline_keyboard": [[
                {"text": "🔍 Teknik detay",
                 "callback_data": f"det:{hakem_id}"}]]}
        self._sahibe_bildir(sahip, "\n".join(L), reply_markup=markup)

    def _portfoy_satirlari(self, sahip: str) -> list[str]:
        """
        Hesap basina gunluk degisim. OLCULEMEYEN HESAP SATIR YAZMAZ.

        `positions` anlik goruntuleri PARCALI oldugu icin "son iki
        snapshot farki" bir sey olcmez (olculdu: 18/2/1/4 satir).
        Dogru olcum bugunku pozisyonlarin fiyat serisinden iki kapanisi
        — `analysis.portfolio.gunluk_degisim`.
        """
        from ..analysis.portfolio import gunluk_degisim
        # HESAP LISTESI VERIDEN TURUYOR, elle yazilmiyor. Sabit bir
        # ("bux","midas","binance") demeti, yeni bir hesap eklendiginde
        # (or. bir TEFAS hesabi) SESSIZCE eksik kalirdi — bu projenin
        # tekrar eden kusur sinifi.
        try:
            hesaplar = [r["account"] for r in self.db.query(
                "SELECT DISTINCT account FROM positions WHERE sahip = ? "
                "ORDER BY account", (sahip,))]
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] %s hesap listesi okunamadi: %s", sahip, e)
            return []
        out = []
        for hesap in hesaplar:
            try:
                d = gunluk_degisim(self.db, hesap, sahip)
            except Exception as e:                    # noqa: BLE001
                log.warning("[nabiz] %s/%s portfoy satiri: %s", sahip, hesap, e)
                continue
            if not d:
                continue
            if d.get("yetersiz_kapsam"):
                # SESSIZ ATLAMA YOK ama SAYI DA YOK: portfoyun %80'ini
                # fiyatlayamiyorsak "portfoy +%0,4" YANLIS BEYANDIR.
                out.append(f"\n📊 <b>{hesap.upper()}</b> gunluk degisim "
                           f"olculemedi (kapsam %{d['kapsam'] * 100:.0f}).")
                continue
            satir = (f"\n📊 <b>{hesap.upper()}</b> {_yuzde_tr(d['degisim_%'])} "
                     f"{d['para_birimi']}")
            if d.get("en_cok"):
                satir += (f" · en cok {_esc(d['en_cok'][0])} "
                          f"{_yuzde_tr(d['en_cok'][1])}")
            if d.get("en_az"):
                satir += (f" · en az {_esc(d['en_az'][0])} "
                          f"{_yuzde_tr(d['en_az'][1])}")
            out.append(satir)
            # NE OLCULDUGU BEYAN EDILIYOR: kur etkisi disarida.
            out.append(f"<i>{d['not']} · {d['tarih']}</i>")
        return out

    # MAKRO SATIRI BAYATLIK SINIRI (gun). Son bar bundan eskiyse SAYI
    # GOSTERILMEZ, tarih yazilir. Iki gun hafta sonunu da kapsar; daha
    # genisi "gram altin 4.512" derken uc gun onceki fiyati soylemek
    # olurdu ve bu, `yanlis-yok-beyani`nin tersi kadar kotu bir sinif:
    # BAYAT VERIYI TAZE GIBI SUNMAK.
    MAKRO_AZAMI_BAYATLIK_GUN = 2

    def _makro_satirlari(self) -> list[str]:
        """
        Ayarda secilen MAKRO kodlarinin tek satirlik ozeti.

        DEGISIM ONCEKI GUNUN KAPANISINA GORE, kipin onceki kosusuna
        gore DEGIL. Aksi halde 12:30 satiri 08:00'e gore %0,0 gosterir
        ve hicbir bilgi tasimaz.

        BU SATIR YORUM DEGIL, UC SAYIDIR. "Altin yukselisde" gibi bir
        sifat yazilmaz — makro panelin `_MAKRO_UYARI` disiplini burada
        da gecerli.
        """
        kodlar = self.s.get("ritim.ozet_makro") or []
        if not kodlar:
            return []                                  # bos liste = satir yok
        parca = []
        for kod in kodlar:
            try:
                p = self._makro_parcasi(str(kod))
            except Exception as e:                     # noqa: BLE001
                log.warning("[nabiz] makro %s okunamadi: %s", kod, e)
                continue
            if p:
                parca.append(p)
        return [f"\n🌍 {' · '.join(parca)}"] if parca else []

    def _makro_parcasi(self, kod: str) -> str | None:
        """Tek makro kodun metni; seri yoksa None, bayatsa TARIH yazar."""
        r = self.db.query(
            """SELECT i.id, i.currency FROM instruments i
               WHERE i.venue = 'MAKRO' AND UPPER(i.symbol) = ? LIMIT 1""",
            (kod.upper(),))
        if not r:
            return None                                # kod yok: sessizce atla
        # PARA BIRIMI ENSTRUMANDAN, ELLE YAZILMAZ.
        ccy = (r[0]["currency"] or "").upper()
        seri = self.db.fiyat_serisi(r[0]["id"], 5)
        if not seri or seri[-1]["close"] is None:
            return None
        son = seri[-1]
        yas = _gun_farki_bugune(son["ts"])
        if yas is not None and yas > self.MAKRO_AZAMI_BAYATLIK_GUN:
            # SAYI YOK, TARIH VAR. Bayat veriyi taze gibi sunmak,
            # hic gostermemekten kotudur.
            return (f"{_esc(kod)}: veri bayat "
                    f"({_tarih_kisa(son['ts']) or son['ts'][:10]})")
        # ONCEKI GUNUN kapanisi — ayni gunun baska bir bari degil.
        gun = str(son["ts"])[:10]
        onceki = next((b for b in reversed(seri[:-1])
                       if str(b["ts"])[:10] < gun and b["close"]), None)
        metin = f"{_esc(kod)} {_tr(son['close'])}"
        if ccy:
            metin += f" {_esc(ccy)}"
        if onceki:
            metin += f" {_yuzde_tr((son['close'] / onceki['close'] - 1) * 100)}"
        return metin

    @staticmethod
    def _karne_satirlari(karne: dict, n_tahmin: int) -> list[str]:
        alt = []
        if karne.get("olcum"):
            a = karne["guven_araligi_%"]
            alt.append(f"\n<i>Karne (hakem cagrilari): {karne['olcum']} olcum, "
                       f"isabet %{karne['isabet_%']} "
                       f"(guven araligi %{a[0]}-%{a[1]}, "
                       f"{karne.get('aralik_ornegi', karne['olcum'])} "
                       f"bagimsiz kume)</i>")
            if not karne.get("yeterli_mi"):
                alt.append("<i>⚠️ Ornekem yetersiz — bu orandan sonuc "
                           "cikarma.</i>")
        else:
            # SABIT METIN DEGIL, defterin KENDI notu.
            alt.append(f"\n<i>Karne: {karne.get('not', 'olcum yok')}</i>")
        if n_tahmin:
            alt.append(f"<i>{n_tahmin} yeni tahmin deftere yazildi; "
                       f"vadesi dolunca puanlanacak.</i>")
        return alt
