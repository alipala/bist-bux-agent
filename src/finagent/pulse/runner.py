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
        if not guclu:
            log.info("[nabiz] esigi gecen sinyal yok — sessiz kaliniyor")
            return {"sinyal": len(sinyaller), "guclu": 0, "karne": karne,
                    "ozet": None, "tahmin": 0}

        if not panel:
            return {"sinyal": len(sinyaller), "guclu": len(guclu),
                    "karne": karne, "ozet": None, "tahmin": 0,
                    "sinyaller": guclu[:PANEL_ADAY]}

        # 3) Ajan paneli + hakem
        import anyio
        from .agents import Panel
        sonuc = anyio.run(Panel(self.s, self.db).calistir, guclu[:PANEL_ADAY])

        # 4) Tahminleri deftere yaz
        n_tahmin = defter.kaydet(sonuc.get("gorusler") or [])
        log.info("[nabiz] %d tahmin kaydedildi", n_tahmin)

        # 5) Bildirim
        if bildir and sonuc.get("ozet"):
            self._gonder(sonuc["ozet"], karne, n_tahmin)

        return {"sinyal": len(sinyaller), "guclu": len(guclu), "karne": karne,
                "ozet": sonuc.get("ozet"), "tahmin": n_tahmin,
                "ajanlar": sonuc.get("ajanlar", {})}

    # ------------------------------------------------------------------
    def _gonder(self, ozet: str, karne: dict, n_tahmin: int) -> None:
        from ..notify import TelegramNotifier
        from ..notify.telegram import md_to_tg_html

        bas = f"📊 <b>Gunluk nabiz</b> · {datetime.now(timezone.utc):%d.%m.%Y}\n\n"
        alt = []
        if karne.get("olcum"):
            a = karne["guven_araligi_%"]
            alt.append(f"\n\n<i>Karne: {karne['olcum']} olcum, isabet "
                       f"%{karne['isabet_%']} (guven araligi %{a[0]}-%{a[1]})</i>")
            if not karne.get("yeterli_mi"):
                alt.append("\n<i>⚠️ Ornekem yetersiz — bu orandan sonuc cikarma.</i>")
        else:
            alt.append("\n\n<i>Karne: henuz puanlanmis tahmin yok.</i>")
        if n_tahmin:
            alt.append(f"\n<i>{n_tahmin} yeni tahmin deftere yazildi; "
                       f"vadesi dolunca puanlanacak.</i>")
        try:
            TelegramNotifier(self.s).send_message(
                bas + md_to_tg_html(ozet) + "".join(alt))
        except Exception as e:                        # noqa: BLE001
            log.warning("[nabiz] bildirim gonderilemedi: %s", e)
