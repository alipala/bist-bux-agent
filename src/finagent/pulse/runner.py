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
# Olculdu 2026-08-16: iki sahiple tam nabiz 8,5 dk (511 sn); panel basina
# ~4 dk. Toplama zinciri ayrica 7,8 dk, yani gecelik toplam ~16 dk ve
# run_pulse.sh'in duvar saati siniri 45 dk — rahat. Ama sahip sayisi
# artarsa ya da bir panel takilirsa bekci sureci ORTADAN keser ve o
# sahip ne cikti ne aciklama alir.
#
# Bu esik asildiginda kalan sahiplerin paneli atlanir ve kendilerine
# SOYLENIR. Sessiz atlama YOK: "bugun mesaj gelmedi" ile "bugun panel
# kosamadi" ayri seyler ve ikincisi kullanicinin bilmesi gerekendir.
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
        test icin). Verilmezse yapilandirmadaki tum sahipler.
        """
        sahipler = [sahip] if sahip else self.s.sahip_listesi
        if not sahipler:
            # SESSIZ NO-OP DEGIL. Sahipsiz kosu hicbir sey uretmez ama
            # "calisti" gorunur; bu, bildirimlerin neden gelmedigini
            # gunlerce gizleyebilir.
            raise ValueError(
                "nabiz: yapilandirilmis sahip yok. "
                "config/settings.yaml -> telegram.sahipler")

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
            if panel and gecen > PANEL_SURE_BUTCESI_SN:
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

        return {"kip": kip, "sahipler": sahipler, "basarisiz": basarisiz,
                "panel_atlanan": atlanan,
                "ortak": {k: v for k, v in ortak.items() if k != "sinyaller"},
                "sonuc": sonuclar,
                # Tek sahipli kurulumda BUGUNKU sozlesme korunuyor:
                # cagiranlar (run.py, testler) duz alanlari okuyor.
                **(sonuclar[sahipler[0]] if len(sahipler) == 1
                   and "hata" not in sonuclar[sahipler[0]] else {})}

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

        if bildir and bozulan:
            self._tez_bildir(bozulan, sahip)

        if not guclu:
            log.info("[%s/%s] esigi gecen sinyal yok — sessiz", kip, sahip)
            return {"sinyal": len(sinyaller), "guclu": 0, "karne": karne,
                    "ozet": None, "tahmin": 0, "tez_bozuldu": len(bozulan)}

        # --- LLM adimlari — AYRI sarili ----------------------------------
        try:
            return self._panel_fazi(sahip, kip, bildir, guclu, bozulan,
                                    karne, len(sinyaller), defter)
        except Exception as e:                        # noqa: BLE001
            log.exception("[%s/%s] panel patladi", kip, sahip)
            if bildir:
                self._sahibe_bildir(
                    sahip,
                    f"🟡 <b>{kip}: panel calismadi</b>\n\n"
                    f"<i>{_esc(anlasilir_hata(e, self.s)[:400])}</i>\n\n"
                    "Deterministik adimlar tamamlandi; tez alarmi ve "
                    "portfoy riski etkilenmedi.")
            return {"sinyal": len(sinyaller), "guclu": len(guclu),
                    "karne": karne, "ozet": None, "tahmin": 0,
                    "tez_bozuldu": len(bozulan),
                    "panel_hatasi": f"{type(e).__name__}: {e}"}

    def _panel_fazi(self, sahip, kip, bildir, guclu, bozulan, karne,
                    n_sinyal, defter) -> dict:
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
        self._atilanlari_isle(rapor, hakem_rapor)

        if bildir and sonuc.get("ozet"):
            self._gonder(sonuc["ozet"], karne, n_tahmin, sahip,
                         sade=sonuc.get("sade"))

        return {"sinyal": n_sinyal, "guclu": len(guclu), "karne": karne,
                "ozet": sonuc.get("ozet"), "tahmin": n_tahmin,
                "tez_bozuldu": len(bozulan),
                "ajanlar": sonuc.get("ajanlar", {})}

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
    def _sahibe_bildir(self, sahip: str, metin: str) -> bool:
        """
        Bir sahibin TUM sohbetlerine gonderir. Doner: en az biri gitti mi.

        Gonderim basarisizligi (ag, blok, gecersiz chat_id) DIGER sahibi
        etkilemez; yalnizca loglanir ve donus degerine yansir.
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
        for chat in chatler:
            try:
                giden = tg.send_message(metin, chat_id=chat) or giden
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
        portfoy_sinyali = [x for x in guclu
                           if x.get("instrument_id") in sahibin]
        riskler = self._yeni_riskler(
            [x for x in sinyaller if x["tur"] in ("yogunlasma", "acik_zarar")])

        log.info("[%s] hafif kip: %d sinyal, portfoyde %d, risk %d, tez %d",
                 kip, len(sinyaller), len(portfoy_sinyali), len(riskler),
                 len(bozulan))

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

    def _yeni_riskler(self, riskler: list[dict]) -> list[dict]:
        """
        Yalnizca DURUMU DEGISEN riskleri dondurur.

        Portfoy riski bir olay degil DURUMDUR: ASML portfoyun %40'iysa
        bu bugun de yarin da dogru. Bastirma olmadan gunde iki hafif
        kosu ayni cumleyi tekrarlar ve kullanici bildirimleri kapatir.
        Tez alarmindaki `tez_bozuldu_ts` ile ayni problem.
        """
        if not riskler:
            return []
        onceki = {(r["instrument_id"], r["tur"]): r["son_deger"]
                  for r in self.db.query(
                      "SELECT instrument_id, tur, son_deger FROM bildirim_durumu")}
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
        if yazilacak:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.db.tx() as c:
                c.executemany(
                    """INSERT INTO bildirim_durumu
                       (instrument_id, tur, son_deger, son_bildirim_ts)
                       VALUES (?,?,?,?)
                       ON CONFLICT(instrument_id, tur) DO UPDATE SET
                         son_deger = excluded.son_deger,
                         son_bildirim_ts = excluded.son_bildirim_ts""",
                    [(i, tur, d, ts) for i, tur, d in yazilacak])
        if len(riskler) != len(yeni):
            log.info("[nabiz] risk bildirimi bastirildi: %d/%d degismemis",
                     len(riskler) - len(yeni), len(riskler))
        return yeni

    @staticmethod
    def _risk_degeri(r: dict) -> float | None:
        """Riskin izlenen SAYISI — turu belirler."""
        k = r.get("kanit") or {}
        return k.get("agirlik_%") if r["tur"] == "yogunlasma" else k.get("kz_%")

    def _tez_bildir(self, bozulan: list[dict], sahip: str) -> None:
        self._hafif_bildir("nabiz", bozulan, [], [], sahip)

    def _hafif_bildir(self, kip, bozulan, portfoy_sinyali, riskler,
                      sahip: str) -> None:
        BASLIK = {"sabah": "🌅 Sabah", "ogle": "🕕 Kapanis",
                  "nabiz": "📊 Nabiz"}.get(kip, kip)
        L = [f"<b>{BASLIK}</b>"]

        for b in bozulan:
            L.append(f"\n🔔 <b>{_esc(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {_esc(str(b['tez'])[:200])}</i>")
            L.append(f"Kosul <code>{_esc(b['kosul'])}</code> · "
                     f"su anki {b['alan']}: <b>{_kisa(b['deger'])}</b>")
        for x in portfoy_sinyali[:4]:
            L.append(f"\n• <b>{_esc(x['sembol'])}</b> ({x['tur']}, "
                     f"{x.get('yon', '')}) — portfoyunde")
        for r in riskler[:3]:
            k = r.get("kanit") or {}
            L.append(f"\n⚠️ <b>{_esc(r['sembol'])}</b> {r['tur']}"
                     + (f" · agirlik %{k.get('agirlik_%')}" if k.get("agirlik_%")
                        else "")
                     + (f" · K/Z %{k.get('kz_%')}" if k.get("kz_%") else ""))
        L.append("\n<i>Bu koşuda model calismadi — yalnizca olculen esikler.</i>")
        self._sahibe_bildir(sahip, "\n".join(L))

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

    def _atilanlari_isle(self, rapor: dict, hakem_rapor: dict) -> None:
        """
        Defterin attigi goruslerin sayisini o kosunun `panel_runs`
        satirlarina yazar.

        HER AJAN KENDI SAYISINI TASIR. Ilk surumde kosunun toplami tek bir
        ajan satirina yaziliyordu ve bu, docstring'in kendi kuralini
        ("yanlis dagitilmis bir sayi, hic yazilmamis olandan kotudur")
        cigniyordu: `SELECT ajan, atilan_sembol_yok FROM panel_runs`
        sorgusu dort ajanin toplamini id'si en kucuk ajanin sanirdi.

        `Defter.kaydet()` artik `ajan_bazli` kirilim donduruyor — dusurme
        aninda `ajan` zaten sozlukte oldugu icin bu bilgi hic kaybolmuyordu,
        yalnizca tasinmiyordu.
        """
        try:
            with self.db.tx() as c:
                for kaynak in (rapor, hakem_rapor):
                    for ajan, d in (kaynak.get("ajan_bazli") or {}).items():
                        c.execute(
                            """UPDATE panel_runs SET
                                 atilan_sembol_yok = ?, atilan_seri_yok = ?,
                                 atilan_cakisma = ?
                               WHERE id = (SELECT MAX(id) FROM panel_runs
                                           WHERE ajan = ?)""",
                            (d["atilan_sembol_yok"], d["atilan_seri_yok"],
                             d["atilan_cakisma"], ajan))
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] atilan sayaclari yazilamadi: %s", e)

    def _gonder(self, ozet: str, karne: dict, n_tahmin: int, sahip: str,
                sade: str | None = None) -> None:
        """
        SADE katman gonderilir, teknik detay BUTONLA gelir.

        Teknik katman yeniden URETILMEZ — `panel_runs.ham_metin`'den
        okunur. Ikinci bir model cagrisi, olculen sey ile soylenen sey
        arasinda bir suruklenme kanali acardi; bu dongunun ana temasi
        tam olarak buydu.

        Sade katman ayristirilamadiysa TAM TEKNIK mesaj gider: sessizce
        yarim mesaj gondermektense tamamini gonder.
        """
        from ..notify import TelegramNotifier
        from ..notify.telegram import md_to_tg_html

        bas = f"📊 <b>Gunluk nabiz</b> · {datetime.now(timezone.utc):%d.%m.%Y}\n\n"
        alt = []
        if karne.get("olcum"):
            a = karne["guven_araligi_%"]
            alt.append(f"\n\n<i>Karne (hakem cagrilari): {karne['olcum']} olcum, "
                       f"isabet %{karne['isabet_%']} "
                       f"(guven araligi %{a[0]}-%{a[1]})</i>")
            if not karne.get("yeterli_mi"):
                alt.append("\n<i>⚠️ Ornekem yetersiz — bu orandan sonuc cikarma.</i>")
        else:
            # SABIT METIN DEGIL, defterin KENDI notu. `karne()` "hic olcum
            # yok" ile "hakem cagrisi henuz puanlanmadi ama 30 ajan tahmini
            # puanlandi" ayrimini ozenle kuruyor; sabit cumle bu ayrimi
            # kullaniciya HIC ulastirmiyordu. Defter katmaninda dogru olan
            # bir sey, bildirim katmaninda yeniden yanlis beyan ediliyordu.
            alt.append(f"\n\n<i>Karne: {karne.get('not', 'olcum yok')}</i>")
        if n_tahmin:
            alt.append(f"\n<i>{n_tahmin} yeni tahmin deftere yazildi; "
                       f"vadesi dolunca puanlanacak.</i>")
        govde = md_to_tg_html(sade if sade else ozet)
        markup = None
        if sade:
            son = self.db.query(
                "SELECT MAX(run_ts) t FROM panel_runs "
                "WHERE ajan='hakem' AND sahip=?", (sahip,))
            ts = son[0]["t"] if son and son[0]["t"] else ""
            if ts:
                markup = {"inline_keyboard": [[
                    {"text": "🔍 Teknik detay", "callback_data": f"det:{ts}"}]]}
        # Yonlendirme SAHIBE gore; markup varsa ilk sohbete gider.
        from ..notify import TelegramNotifier
        tg = TelegramNotifier(self.s)
        for chat in self.s.sahip_chatleri(sahip) or []:
            try:
                tg.send_message(bas + govde + "".join(alt),
                                reply_markup=markup, chat_id=chat)
            except Exception as e:                    # noqa: BLE001
                log.warning("[nabiz] %s/%s bildirim gonderilemedi: %s",
                            sahip, chat, e)
