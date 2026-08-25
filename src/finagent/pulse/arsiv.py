"""
GIDEN MESAJI SOHBET ARSIVINE YAZ.

NEDEN VAR (2026-08-25, sema 17). Arsiv bugune kadar TEK YONLUYDU:
`sohbet_kaydi`'ya yazan tek yer `listener._sohbet` idi. Botun kendi
PROAKTIF mesajlari — sabah taramasi, ogle/kapanis ozeti, nabiz, gun ici
taktik kartlari, koruma ve tez alarmlari — kullaniciya gidiyor ve
HICBIRI kayit birakmiyordu.

Yani konusmanin yarisi hafizada yoktu. Somut sonucu: Ali sabah
raporundan bir satir alintilayip "bu ne demek" dediginde model o cumleyi
KURDUGUNU bilmiyordu; "dun ne demistin" sorusu taktik kartlarini hic
kapsamiyordu; ve `hatirlanan` katmanina bunun bir OLGU olarak elle
yazilmasi gerekmisti (kayit #4, 25 Agustos).

TEK UYGULAMA, IKI CAGIRAN. `pulse/runner.py` ve `pulse/gunici.py`
ayri ayri yazsalardi ayrisirlardi — bu deponun tekrar eden hata sinifi
("ayni kural iki kopya"). Fonksiyon burada, ikisi de bunu cagiriyor.
"""
from __future__ import annotations

import html
import logging
import re

log = logging.getLogger(__name__)

# Telegram'a HTML gidiyor, arsive DUZ METIN. Iki sebep:
#   1. FTS5 indeksi trigram: `<b>` etiketleri terim gibi indekslenir ve
#      "b" ucgenleri her satirda gecerdi.
#   2. `_sohbet` yolu da modelin MARKDOWN'ini sakliyor, HTML'ini degil.
#      Iki yolun ayni bicimde yazmasi sart, yoksa ayni arsivde iki ayri
#      metin turu olur ve okuyan hangisiyle karsilastigini bilemez.
_ETIKET = re.compile(r"<[^>]+>")


def duz_metin(html_metin: str) -> str:
    """HTML mesaji arsive yazilacak duz metne cevirir."""
    metin = _ETIKET.sub("", html_metin or "")
    # Etiketler gittikten SONRA cozuluyor: once cozseydik icerikteki
    # `&lt;b&gt;` gercek etikete donusur ve sonra silinirdi — kullanicinin
    # gordugu metin arsivde EKSILIRDI.
    return html.unescape(metin).strip()


def arsivle(db, chat_id, sahip: str, metin: str, kaynak: str) -> int | None:
    """
    Giden proaktif mesaji arsive yazar. Doner: satir id'si ya da None.

    YALNIZCA TESLIMATTAN SONRA CAGRILIR. Gonderilmemis bir mesaji
    "soyledim" diye kaydetmek bu projenin en kotu hata sinifi olurdu:
    model sonraki turda Ali'nin HIC GORMEDIGI bir cumleye atifta
    bulunurdu. Cagiranlar `giden` bayragini kontrol ediyor.

    TEK CHAT'E YAZILIR. Bir sahibin birden cok chat_id'si olabilir ve
    mesaj hepsine gider; arsive her biri icin ayri satir yazilsaydi
    `sohbet_ara(sahip)` ayni mesaji N kez donerdi. Arsiv KONUSMAYI
    tutuyor, TESLIMATI degil — teslimat izi logda.

    HATA YUTULUYOR AMA SESSIZ DEGIL. Arsiv yazimi basarisiz olursa kosu
    devam etmeli (mesaj zaten gitti, is bitti); ama `log.error` birakiyor
    cunku sessizce kaybolan bir arsiv satiri, aylar sonra "bunu hic
    soylememissin" cevabina donusur ve sebebi bulunamaz.
    """
    duz = duz_metin(metin)
    if not duz:
        return None
    try:
        return db.sohbet_kaydet(chat_id, "assistant", duz,
                                sahip=sahip, kaynak=kaynak)
    except Exception as e:                            # noqa: BLE001
        log.error("[arsiv] giden mesaj YAZILAMADI (kaynak=%s, sahip=%s): %s",
                  kaynak, sahip, e)
        return None
