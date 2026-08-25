"""
Sembol -> IBKR `conid` eslemesini kataloga yazar.

NEDEN AYRI COLLECTOR
--------------------
Kripto tarafinda ayni ayrim var: `kriptoevren` -> `kripto` (kimlik) ->
`binance` (fiyat). Kimlik cozumu fiyattan AYRI cunku ritmi farkli —
kimlik bir kez cozulur ve degismez, fiyat her gun tazelenir. Her fiyat
kosumunda kimlik yeniden cozmek hem gereksiz istek hem de sessiz kayma
riski.

`conid` ZINCIRIN ILK HALKASI
----------------------------
IBKR'de fiyat da emir de sembolle degil `conid` ile isteniyor. Bu
collector calismadan 5. adim (fiyat) ve 6. adim (emir) hicbir sey
yapamaz.

COZULEMEYEN SESSIZ KALMAZ
-------------------------
Belirsiz kalan sembol bos birakiliyor — ama SEBEBIYLE raporlaniyor.
Eksik conid emir gondermeyi ENGELLER (guvenli taraf); yanlis conid
YANLIS HISSEYI aldirir. Ikisi arasinda secim kolay.

Liste kirpiliyorsa KIRPILDIGI SOYLENIYOR: bu depoda bir kez, 8'de
kesilen bir liste "hepsi bu" gibi okundu ve gercekte 12 tane vardi.
"""
from __future__ import annotations

import logging

from ..ibkr.istemci import Istemci
from ..ibkr.kimlik import conid_coz
from ..ibkr.oturum import Oturum
from .base import BaseCollector, CollectorResult

log = logging.getLogger(__name__)

GOSTERILEN = 8


class IbkrKimlikCollector(BaseCollector):
    name = "ibkrkimlik"
    needs_browser = False

    def collect(self) -> CollectorResult:
        if not bool(self.s.get("ibkr.acik", False)):
            return CollectorResult(self.name, "skipped", 0, "ibkr.acik kapali")

        hedefler = self.db.conidsiz_hedefler()
        if not hedefler:
            return CollectorResult(self.name, "ok", 0, "conidsiz hedef yok")

        istemci = Istemci(self.s.get("ibkr.taban_url", None))
        try:
            # `/trsrv/stocks` brokerage oturumu istemiyor gibi gorunse de
            # gateway kimlik dogrulanmadan HICBIR sey vermiyor (401).
            durum = Oturum(istemci).durumu_oku(zorla=True)
            if not durum.kullanilabilir:
                sebep = ("gateway calismiyor" if not durum.ulasilabilir
                         else "giris yapilmamis" if not durum.kimlik_dogrulandi
                         else durum.mesaj or "brokerage oturumu kapali")
                return CollectorResult(self.name, "skipped", 0, sebep)

            istek = {h["symbol"]: h["name"] for h in hedefler}
            kimlik_id = {h["symbol"]: h["id"] for h in hedefler}
            sonuclar = conid_coz(istemci, istek)
        finally:
            istemci.kapat()

        yazilan, cozulemeyen = 0, []
        for s in sonuclar:
            if s.cozuldu:
                if self.db.save_conid(kimlik_id[s.sembol], s.conid):
                    yazilan += 1
                    log.info("[ibkrkimlik] %s -> %s (%s)",
                             s.sembol, s.conid, s.sebep)
            else:
                cozulemeyen.append(f"{s.sembol} ({s.sebep})")

        not_ = None
        if cozulemeyen:
            # SESSIZ KIRPMA YOK.
            not_ = "cozulemedi: " + "; ".join(cozulemeyen[:GOSTERILEN])
            if len(cozulemeyen) > GOSTERILEN:
                not_ += (f" (+{len(cozulemeyen) - GOSTERILEN} daha, "
                         f"toplam {len(cozulemeyen)})")
        durum_kod = "partial" if cozulemeyen else "ok"
        return CollectorResult(self.name, durum_kod, yazilan, not_)
