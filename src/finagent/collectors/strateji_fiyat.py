"""
Strateji evreninin fiyat serisi — `prices`ten AYRI COLLECTOR.

NEDEN AYRILDI (2026-08-28, sahada olculdu). Evren `prices` icindeyken
518 sembol GUNDE UC KEZ tazeleniyordu (sabah, kapanis, nabiz) — oysa
Donchian taramasi YALNIZCA nabiz kipinde kosuyor. Sabah ve kapanis
koşularinin cektigi verinin HICBIR ALICISI yoktu.

Bedeli baska bir katmanda odendi:

    prices    80 sn -> 861 sn (duzeltmeden sonra 567 sn)
    sabah paneli sahip basina  450 sn -> 243 sn
    28 Agu sabahi panel HIC kosmadi (pay 61 sn < asgari 120)

Hakem — dort ajanin gorusunu tek yargiya ceviren sentez katmani —
450 sn'de bile kesiliyordu; 243 sn'de sansi yok.

COZUM KIP FARKINDALIGI DEGIL. Collector'a "hangi kipten cagrildin"
sordurmak, ayni sinifi iki davranisa bolerdi ve test edilmesi zor bir
gizli durum yaratirdi. Sistemde ZATEN kip basina kaynak listesi var
(`ritim.kipler.<kip>.kaynaklar`) — dogru cozum bu listenin isini
yapmasina izin vermek: ayri collector, yalnizca `nabiz`de kayitli.

GOVDE PAYLASILIYOR, KOPYALANMIYOR. `PriceCollector`in ad kapisi,
sinif soneki varyanti, BIST/MAKRO elemesi, hata siniflandirmasi ve
kirpma bildirimi OLDUGU GIBI miras aliniyor. Ikinci bir kopya bu
deponun tekrar eden kusur sinifi olurdu: kopyalar ayrisir.
"""
from __future__ import annotations

import logging

from .prices import PriceCollector

log = logging.getLogger(__name__)


class StratejiFiyatCollector(PriceCollector):
    name = "strateji_fiyat"
    needs_browser = False

    def _hedefleri_hazirla(self) -> list:
        """
        Strateji evreni + tazeleme plani.

        Plan BURADA kuruluyor, `collect()`te degil: hedef listesi ile
        aralik karari AYNI kaynaktan (`strateji_evreni()`) turuyor ve
        ikisini ayirmak, birinin guncellenip digerinin unutulmasina
        acik kapi birakirdi.
        """
        evren = self.strateji_evreni()
        self._strateji_idler = {h["id"] for h in evren}
        self._strateji_araliklari = self._tazeleme_plani(evren)
        return list(evren)

    def _bos_sebep(self) -> str:
        # "KAPALI" ile "BOS" ayni cumleye giriyor cunku ikisi de ARIZA
        # DEGIL: `enabled: false` bilincli bir karar, bos evren ise
        # `index_members` henuz doldurulmamis demek. Ikisinde de
        # `skipped` donuyor ve bekci `skipped`i ariza saymiyor.
        return "strateji evreni kapali ya da bos"

    def _ek_seriler(self, hedefler) -> tuple[int, list]:
        """
        Endeks serisi ve borsa kotasyonu BU COLLECTOR'IN ISI DEGIL.

        Ikisi de `prices`e ait ve orada her kosuda zaten cekiliyor.
        Burada tekrarlamak, ayni seriyi gunde bir kez daha yazmak ve
        `strateji_fiyat`i endeks bir hata verdiginde `partial`
        gostermek olurdu — yani yanlis collector'a alarm yazdirmak.
        """
        return 0, []
