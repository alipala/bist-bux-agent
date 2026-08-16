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


class Nabiz:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def calistir(self, bildir: bool = True, panel: bool = True) -> dict:
        from .journal import Defter
        from .screener import Tarayici

        defter = Defter(self.db)

        # 1) Once VADESI DOLMUS tahminleri puanla.
        karne = defter.puanla()
        log.info("[nabiz] puanlama: %s", karne)

        # 2) Deterministik tarama
        tarayici = Tarayici(self.s, self.db)
        sinyaller = tarayici.tara()
        tarayici.kaydet(sinyaller)
        log.info("[nabiz] %d sinyal", len(sinyaller))

        guclu = [x for x in sinyaller if x["guc"] >= BILDIRIM_ESIGI]
        gundem = self._gundem(guclu)
        if not guclu:
            log.info("[nabiz] esigi gecen sinyal yok — sessiz kaliniyor")
            return {"sinyal": len(sinyaller), "guclu": 0, "karne": karne,
                    "ozet": None, "tahmin": 0}

        if not panel:
            return {"sinyal": len(sinyaller), "guclu": len(guclu),
                    "karne": karne, "ozet": None, "tahmin": 0,
                    "sinyaller": gundem}

        # 3) Ajan paneli + hakem
        import anyio
        from .agents import Panel
        sonuc = anyio.run(Panel(self.s, self.db).calistir, gundem)

        # 4) Tahminleri deftere yaz — AJANLAR + HAKEM AYRI.
        #
        # Hakemin cagrisi ayrica kaydedilir cunku KULLANICININ OKUDUGU sey
        # odur. Ajanlarin goruslerini puanlamak "panel ne kadar isabetli"
        # sorusunu cevaplar; hakemi puanlamak "sana gonderdigim ozet ne
        # kadar isabetli" sorusunu cevaplar. Ikincisi olculmezse karne,
        # kullanicinin gordugu tavsiyenin isabetini olcmemis olur.
        rapor = defter.kaydet(sonuc.get("gorusler") or [])
        hakem_rapor = defter.kaydet(sonuc.get("hakem_gorusler") or [])
        n_tahmin = rapor["yazilan"] + hakem_rapor["yazilan"]
        log.info("[nabiz] tahmin: ajanlar %s · hakem %s", rapor, hakem_rapor)

        # ATILANLAR PANEL_RUNS'A YAZILIR — yalnizca loga degil.
        #
        # Sayaclar modul sinirinin yanlis tarafinda doguyor: `panel_runs`
        # satirini Panel yaziyor, atilanlari Defter sayiyor, ikisini
        # runner birlestiriyor. Yazilmazlarsa uc kolon surekli 0 kalir ve
        # "bayrak yerine sayac koydum" gerekcesi kendi kendini curutur:
        # kullanilmayan kolon, tam olarak kacindigimiz olu konfigurasyon.
        self._atilanlari_isle(rapor, hakem_rapor)

        # 5) Bildirim
        if bildir and sonuc.get("ozet"):
            self._gonder(sonuc["ozet"], karne, n_tahmin)

        return {"sinyal": len(sinyaller), "guclu": len(guclu), "karne": karne,
                "ozet": sonuc.get("ozet"), "tahmin": n_tahmin,
                "ajanlar": sonuc.get("ajanlar", {})}

    # ------------------------------------------------------------------
    def _gundem(self, guclu: list[dict]) -> list[dict]:
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
        sahip = {r["instrument_id"] for r in self.db.query(
            """SELECT DISTINCT instrument_id FROM positions p
               WHERE p.snapshot_ts = (SELECT MAX(snapshot_ts) FROM positions
                                      WHERE account = p.account)""")}
        portfoy = [x for x in guclu if x.get("instrument_id") in sahip]
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
                                   if x.get("instrument_id") in sahip),
                 {v: sum(1 for x in secilen if x["venue"] == v)
                  for v in sorted({x["venue"] for x in secilen})})
        return secilen

    def _atilanlari_isle(self, rapor: dict, hakem_rapor: dict) -> None:
        """
        Defterin attigi goruslerin sayisini o kosunun `panel_runs`
        satirlarina yazar.

        DAGITIM SORUNU: `kaydet()` tum ajanlarin goruslerini TEK LISTE
        olarak aliyor, dolayisiyla atilanin hangi ajandan geldigi rapora
        yansimiyor. Ajan basina dogru sayiyi uydurmak yerine, ajan
        gorusleri o kosunun 'hakem' OLMAYAN son satirlarina toplu yazilir
        ve hakeminki hakem satirina. Yanlis dagitilmis bir sayi, hic
        yazilmamis bir sayidan daha kotudur.
        """
        try:
            son = self.db.query(
                "SELECT MAX(run_ts) t FROM panel_runs")[0]["t"]
            if not son:
                return
            with self.db.tx() as c:
                c.execute(
                    """UPDATE panel_runs SET
                         atilan_sembol_yok = ?, atilan_seri_yok = ?,
                         atilan_cakisma = ?
                       WHERE run_ts = ? AND ajan = 'hakem'""",
                    (hakem_rapor["atilan_sembol_yok"],
                     hakem_rapor["atilan_seri_yok"],
                     hakem_rapor["atilan_cakisma"], son))
                # Ajan tarafi: kosunun ILK ajan satirina toplu yazilir.
                ilk = c.execute(
                    "SELECT MIN(id) FROM panel_runs WHERE ajan <> 'hakem'"
                    " AND run_ts = (SELECT MAX(run_ts) FROM panel_runs"
                    "               WHERE ajan <> 'hakem')").fetchone()[0]
                if ilk is not None:
                    c.execute(
                        """UPDATE panel_runs SET
                             atilan_sembol_yok = ?, atilan_seri_yok = ?,
                             atilan_cakisma = ?
                           WHERE id = ?""",
                        (rapor["atilan_sembol_yok"], rapor["atilan_seri_yok"],
                         rapor["atilan_cakisma"], ilk))
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] atilan sayaclari yazilamadi: %s", e)

    def _gonder(self, ozet: str, karne: dict, n_tahmin: int) -> None:
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
        try:
            TelegramNotifier(self.s).send_message(
                bas + md_to_tg_html(ozet) + "".join(alt))
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] bildirim gonderilemedi: %s", e)
