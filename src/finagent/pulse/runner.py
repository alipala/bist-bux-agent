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


class Nabiz:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def calistir(self, bildir: bool = True, panel: bool = True,
                 kip: str = "nabiz", sahip: str | None = None) -> dict:
        """
        FAZ A: tek sahip. `sahip` verilmezse yapilandirmadaki TEK sahip
        kullanilir; birden fazla sahip varsa ACIK HATA — Faz B'de dongu
        gelene kadar hangisinin kastedildigi TAHMIN EDILMEZ.
        """
        from .journal import Defter
        from .screener import Tarayici

        if sahip is None:
            liste = self.s.sahip_listesi
            if len(liste) != 1:
                raise ValueError(
                    f"nabiz: sahip belirtilmeli (yapilandirmada {len(liste)} "
                    f"sahip var: {liste}). Cok sahipli dongu Faz B.")
            sahip = liste[0]

        defter = Defter(self.db)

        # 1) Once VADESI DOLMUS tahminleri puanla — TUM sahipler icin
        #    (deterministik, LLM yok), karne ise bu sahibe ait.
        karne = defter.puanla(sahip)
        log.info("[%s] puanlama: %s", kip, karne)

        # 2) Deterministik tarama
        tarayici = Tarayici(self.s, self.db)
        sinyaller = tarayici.tara(sahip)
        tarayici.kaydet(sinyaller, sahip)
        log.info("[%s] %d sinyal", kip, len(sinyaller))

        # 3) TEZ KONTROLU — her kipte, LLM'siz.
        #
        # Bu bir tahmin degil kosul kontrolu: daha once ACIKCA beyan
        # edilmis bir esigin gerceklesip gerceklesmedigi. Isabet orani
        # olculmemis olsa da durustce bildirilebilmesinin sebebi bu.
        bozulan = defter.tez_kontrol(sahip)

        guclu = [x for x in sinyaller if x["guc"] >= BILDIRIM_ESIGI]

        if not panel:
            return self._hafif(kip, bildir, sinyaller, guclu, bozulan, karne,
                               sahip)

        gundem = self._gundem(guclu, sahip)
        if not guclu:
            log.info("[%s] esigi gecen sinyal yok — sessiz kaliniyor", kip)
            if bildir and bozulan:
                self._tez_bildir(bozulan)
            return {"sinyal": len(sinyaller), "guclu": 0, "karne": karne,
                    "ozet": None, "tahmin": 0, "tez_bozuldu": len(bozulan)}

        # 3) Ajan paneli + hakem
        import anyio
        from .agents import Panel
        sonuc = anyio.run(Panel(self.s, self.db, sahip).calistir, gundem)

        # 4) Tahminleri deftere yaz — AJANLAR + HAKEM AYRI.
        #
        # Hakemin cagrisi ayrica kaydedilir cunku KULLANICININ OKUDUGU sey
        # odur. Ajanlarin goruslerini puanlamak "panel ne kadar isabetli"
        # sorusunu cevaplar; hakemi puanlamak "sana gonderdigim ozet ne
        # kadar isabetli" sorusunu cevaplar. Ikincisi olculmezse karne,
        # kullanicinin gordugu tavsiyenin isabetini olcmemis olur.
        rapor = defter.kaydet(sonuc.get("gorusler") or [], sahip)
        hakem_rapor = defter.kaydet(sonuc.get("hakem_gorusler") or [], sahip)
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
        if bildir and bozulan:
            self._tez_bildir(bozulan)
        if bildir and sonuc.get("ozet"):
            self._gonder(sonuc["ozet"], karne, n_tahmin,
                         sade=sonuc.get("sade"))

        return {"sinyal": len(sinyaller), "guclu": len(guclu), "karne": karne,
                "ozet": sonuc.get("ozet"), "tahmin": n_tahmin,
                "ajanlar": sonuc.get("ajanlar", {})}

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
            self._hafif_bildir(kip, bozulan, portfoy_sinyali, riskler)
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

    def _tez_bildir(self, bozulan: list[dict]) -> None:
        self._hafif_bildir("nabiz", bozulan, [], [])

    def _hafif_bildir(self, kip, bozulan, portfoy_sinyali, riskler) -> None:
        from ..notify import TelegramNotifier

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
        try:
            TelegramNotifier(self.s).send_message("\n".join(L))
        except Exception as e:                        # noqa: BLE001
            log.warning("[%s] bildirim gonderilemedi: %s", kip, e)

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

    def _gonder(self, ozet: str, karne: dict, n_tahmin: int,
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
                "SELECT MAX(run_ts) t FROM panel_runs WHERE ajan='hakem'")
            ts = son[0]["t"] if son and son[0]["t"] else ""
            if ts:
                markup = {"inline_keyboard": [[
                    {"text": "🔍 Teknik detay", "callback_data": f"det:{ts}"}]]}
        try:
            TelegramNotifier(self.s).send_message(
                bas + govde + "".join(alt), reply_markup=markup)
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] bildirim gonderilemedi: %s", e)
