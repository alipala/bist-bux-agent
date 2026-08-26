"""
IBKR portfoyunu veritabanina yazar — pozisyonlar + nakit.

NEDEN ONAY YOK (ekran goruntusu yolundan farki)
-----------------------------------------------
`positions` tablosuna bugune kadar YALNIZCA `listener.py` yaziyordu ve her
yazim insan onayindan geciyordu. Sebep OCR: ekran goruntusundeki sayi
YANLIS OKUNMUS olabilir, o yuzden insan gozu son kapi.

IBKR'de veri API'den ve KESIN geliyor — okunmuyor, aliniyor. Onay kapisi
belirsiz veriyi ve EYLEMI korumak icin var, mekanik bir senkronu degil.
Emir gonderimi (6. adim) elbette onaydan gececek; bu collector yalnizca
OKUYOR.

OTURUM YOKSA "ATLA VE SOYLE", SESSIZ GECME YOK
----------------------------------------------
IBKR bireysel hesapta gunde bir ELLE giris istiyor ve otomasyonu
desteklemiyor. Yani bu collector'in oturumsuz calismasi BEKLENEN bir
durum, `error` degil. Ama `skipped` da sessiz olmamali: sebep
raporlaniyor, cunku "veri gelmedi" ile "oturum yoktu" ayri seyler ve
bu depoda en pahali hata sinifi veri VARKEN yok demek.

SAHIP PARAMETRE, VARSAYILANI YOK
--------------------------------
`insert_positions` sahibi zorunlu tutuyor: "Yanlis kisinin portfoyune
yazmak bu isin tek gercek tehlikesi; sessiz varsayilan onu kaza degil
TASARIM haline getirirdi." IBKR hesabi bir kisiye ait; ayar
`ibkr.sahip`te ve yoksa collector CALISMAZ.

NAKIT DE BIR SATIR
------------------
Depo nakdi zaten pozisyon satiri olarak tutuyor: symbol='CASH',
asset_type='cash', quantity=NULL, tutar `market_value`da, para birimi
`currency`de. IBKR cok para birimli — ledger her para birimini AYRI
donduruyor ve her biri kendi satirini aliyor. `BASE` satiri toplam
oldugu icin `portfoy.nakit()` onu zaten disarida birakiyor; buraya
yazmak toplami iki kez saymak olurdu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..ibkr.istemci import Istemci, UlasilamadiHatasi, YetkiHatasi
from ..ibkr.oturum import Oturum
from ..ibkr.portfoy import Portfoy
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

HESAP = "ibkr"


class IbkrPortfoyCollector(BaseCollector):
    name = "ibkr"
    needs_browser = False

    def collect(self) -> CollectorResult:
        if not bool(self.s.get("ibkr.acik", False)):
            return CollectorResult(self.name, "skipped", 0, "ibkr.acik kapali")

        sahip = (self.s.get("ibkr.sahip") or "").strip().lower()
        if not sahip:
            # Sessiz varsayilan YOK. Yanlis portfoye yazmaktansa hic
            # yazmamak dogru.
            return CollectorResult(self.name, "error", 0,
                                   "ibkr.sahip tanimli degil — kimin portfoyu?")

        istemci = Istemci(self.s.get("ibkr.taban_url", None))
        try:
            return self._topla(istemci, sahip)
        finally:
            istemci.kapat()

    # ------------------------------------------------------------------
    def _topla(self, istemci: Istemci, sahip: str) -> CollectorResult:
        # --- oturum kapisi ---
        #
        # DIKKAT: BROKERAGE OTURUMU ARANMIYOR, VE BU BILINCLI.
        #
        # IBKR oturumu IKI KATMANLI: disardaki "salt okuma" oturumu
        # `/portfolio` uclarini acar; brokerage oturumu ise `/iserver`
        # (piyasa verisi + emir) icin gerekir. Ikincisi BIRINCISI
        # AYAKTAYKEN dusebiliyor — canli olcum (2026-08-26):
        #
        #     /iserver/auth/status -> authenticated:false connected:false
        #     /portfolio/accounts  -> CALISIYOR, hesap donuyor
        #
        # Ilk surum `durum.kullanilabilir` (yani brokerage) sart
        # kosuyordu ve bu durumda portfoyu ATLIYORDU — okunabilir veri
        # DURURKEN "atlandi" demek, bu deponun en kotu hata sinifinin
        # ta kendisi. Artik kapi DOGRU KATMANDA: `/portfolio/accounts`
        # denenir, cevabi kendisi soyler.
        durum = Oturum(istemci).durumu_oku(zorla=True)
        p = Portfoy(istemci)
        try:
            hesaplar = p.hesaplar()
        except YetkiHatasi:
            return CollectorResult(
                self.name, "skipped", 0,
                "giris yapilmamis — tarayicidan giris gerekiyor")
        except UlasilamadiHatasi as e:
            return CollectorResult(self.name, "skipped", 0,
                                   f"gateway calismiyor: {e}")
        if not hesaplar:
            return CollectorResult(self.name, "error", 0, "hesap listesi bos")

        h = hesaplar[0]
        if len(hesaplar) > 1:
            # Birden fazla hesapta sessizce ilkini secmek yanlis portfoye
            # bakmanin en kolay yolu; su an tek hesap var ama bu kod
            # bir gun iki hesapla karsilasirsa DURMALI.
            return CollectorResult(
                self.name, "error", 0,
                f"{len(hesaplar)} hesap var, secim mantigi yok: "
                + ", ".join(x.kimlik for x in hesaplar))

        anlik = datetime.now(timezone.utc).isoformat(timespec="seconds")
        satirlar: list[dict] = []
        uyari: list[str] = []

        # --- pozisyonlar ---
        for poz in p.pozisyonlar(h.kimlik):
            if not poz.sembol:
                # Sembolsuz pozisyon YAZILMAZ: `pozisyon_enstrumani`
                # bos sembolle bir enstruman uydururdu.
                uyari.append(f"sembolsuz pozisyon (conid={poz.conid})")
                continue
            satirlar.append(poz.db_satiri())

        # --- nakit: her para birimi AYRI satir, AMA AYRI ENSTRUMAN ---
        #
        # SESSIZ VERI KAYBI, SAHADA OLCULDU (26 Agu): `positions`
        # birincil anahtari (sahip, snapshot_ts, account, instrument_id)
        # ve PARA BIRIMI ICINDE YOK. Iki nakit satiri (EUR 2,06 ve
        # USD -0,00) ayni `CASH` enstrumanina baglandi, ikincisi
        # birincisini EZDI — ustelik `ON CONFLICT ... DO UPDATE` sette
        # `currency` YOK, yani tutar USD'den geldi ama etiket EUR kaldi:
        #
        #     defterde: CASH / EUR / 0,00      IBKR'de: EUR 2,06
        #
        # Ne biri ne oteki: FRANKENSTEIN satir. Yanlis etiketli para
        # rakami, eksik para rakamindan kotudur.
        #
        # Ayni hata sinifinin UCUNCU tekrari: `prices` PK'sinda da para
        # birimi yoktu ve EUR serisi USD serisini ezmisti. Cozum de ayni:
        # KOTASYON BASINA AYRI KIMLIK. Taban para birimi `CASH` kalir
        # (mevcut davranis, diger hesaplar etkilenmez); digerleri
        # `CASH.<PB>` olur. `asset_type='cash'` HEPSINDE duruyor, cunku
        # asagi akistaki nakit suzgeclerinin cogu ona bakiyor.
        #
        # SIFIR BAKIYE YAZILMIYOR: bilgi tasimiyor ve her para birimi
        # icin satir acmak defteri sisirir. Atlanani LOGLUYORUZ —
        # sessiz atlama bu depoda ayri bir hata sinifi.
        taban_pb = (h.para_birimi or "").upper()
        for pb, n in p.nakit(h.kimlik).items():
            if n.nakit is None:
                continue
            if pb.upper() != taban_pb and not n.nakit:
                log.info("[ibkr] %s nakdi 0 — satir yazilmadi", pb)
                continue
            sembol = "CASH" if pb.upper() == taban_pb else f"CASH.{pb.upper()}"
            satirlar.append({
                "symbol": sembol,
                "asset_type": "cash",
                "quantity": None,
                "avg_cost": None,
                "last_price": None,
                "market_value": n.nakit,
                "pnl_abs": None,
                "pnl_pct": None,
                "currency": pb,
                "name": None,
            })

        if not satirlar:
            return CollectorResult(self.name, "ok", 0, "portfoy bos")

        n = self.db.insert_positions(HESAP, anlik, satirlar, sahip)
        durum_kod = "partial" if uyari else "ok"
        return CollectorResult(
            self.name, durum_kod, n, "; ".join(uyari) or None,
            data={"hesap_kagit_mi": h.kagit_mi, "para_birimi": h.para_birimi,
                  # Brokerage oturumu portfoy icin SART DEGIL ama
                  # bilinmesi faydali: kapaliysa fiyat ve emir calismaz.
                  "brokerage_oturumu": durum.kullanilabilir})
