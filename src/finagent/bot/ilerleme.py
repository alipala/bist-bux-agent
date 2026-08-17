"""
ILERLEME GOSTERGESI — "bot beni duydu mu?" sorusunu ortadan kaldirir.

SORUN OLCULDU
-------------
Sohbet cevabi 30-60 saniye suruyor (`listener.py` yorumu bunu zaten
yaziyordu) ve o sure boyunca kullaniciya HICBIR sey gorunmuyordu.
Kodda `chat_action(chat_id, "typing")` vardi ama TEK KEZ cagriliyordu
ve **Telegram'in "yaziyor…" gostergesi ~5 SANIYE sonra soner**. Yani
kullanici 5 saniye gosterge goruyor, ardindan 25-55 saniye sessizlik —
"mesajim dusmedi galiba" hissi tam buradan geliyor.

UC MEKANIZMA, UCU DE AYNI SAGLAMLIKTA DEGIL
-------------------------------------------
    sendChatAction      EFEMER  ~5 sn sonra soner, yenilenmezse kaybolur
    setMessageReaction  KALICI  anlik onay verir ama ILERLEME gostermez
    mesaj + editMessage KALICI  tek saglam olan: durum bir MESAJDA durur

Bu yuzden omurga UCUNCUSU: hemen bir "durum mesaji" gonderilir ve is
ilerledikce DUZENLENIR. "yaziyor…" ise 4 saniyede bir yenilenerek
suslemesi olarak korunur (native gostergeyi canli tutar).

ILERLEME SAHTE DEGIL
--------------------
`chat.py`'in akis dongusu her arac cagrisini zaten goruyor. Bu sinif
oraya baglaniyor, yani kullanici "Yukleniyor…" gibi bir animasyon
degil GERCEKTEN ne yapildigini goruyor: "portfoyune bakiyorum",
"ASML'in gostergelerini okuyorum". Sahte bir spinner, veri yokken bile
donerdi — bu donmez.

BOZULURSA CEVABI BOZMAZ
-----------------------
Gosterge bir SUSTUR, is degil. Buradaki her Telegram cagrisi
yutuluyor: durum mesaji gonderilemese de, duzenlenemese de, silinemese
de asil cevap normal yolundan gider. Tersi kabul edilemezdi — kozmetik
bir katmanin cevabi dusurmesi.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# Telegram "yaziyor…" gostergesi ~5 sn yasiyor; 4 sn'de bir yenilemek
# kesintisiz gosteriyor ve saniyede bir istekten cok daha seyrek.
NABIZ_SN = 4.0

# Telegram mesaj duzenlemede saniyede ~1 istege izin veriyor. 1,5 sn
# guvenli taraf; ayrica AYNI metinle duzenleme 400 "message is not
# modified" doner, o yuzden tekrar da eleniyor.
DUZENLEME_ARALIGI_SN = 1.5

BASLANGIC = "⏳ Bakiyorum…"


def _sade_ad(arac: str) -> str:
    """
    Arac adi -> kullanicinin anlayacagi cumle.

    ELLE YAZILMIYOR: karsiliklar `yetenekler.SADE`'den geliyor ve orasi
    zaten TESTLE eksiksiz tutuluyor. Ikinci bir liste acmak, bu projenin
    tekrar eden kusur sinifi olurdu (arac eklenir, liste guncellenmez,
    kullaniciya bos ya da yanlis etiket gider).
    """
    try:
        from .yetenekler import SADE
        aciklama = SADE.get(arac)
    except Exception:                                  # noqa: BLE001
        aciklama = None
    if not aciklama:
        return arac.replace("_", " ")
    # SADE aciklamalari "x — y" bicimli; kisa yani yeter.
    return aciklama.split("—")[0].strip().rstrip(":")


class Ilerleme:
    """
    Baglam yoneticisi: girerken durum mesajini gonderir, cikarken siler.

        with Ilerleme(tg, chat_id) as ilerleme:
            sonuc = motor.cevapla(..., ilerleme=ilerleme.arac_gordu)
        # buradan sonra normal cevap gonderilir
    """

    def __init__(self, tg, chat_id, baslangic: str = BASLANGIC,
                 aktif: bool = True):
        self.tg = tg
        self.chat_id = chat_id
        self.baslangic = baslangic
        self.aktif = aktif
        self.message_id: int | None = None
        self._dur = threading.Event()
        self._is_parcacigi: threading.Thread | None = None
        self._son_metin = ""
        self._son_yazma = 0.0
        self._araclar: list[str] = []
        self._kilit = threading.Lock()

    # ------------------------------------------------------------------
    def __enter__(self) -> "Ilerleme":
        if not self.aktif:
            return self
        try:
            self.message_id = self.tg.send_message_id(self.baslangic,
                                                      chat_id=self.chat_id)
            self._son_metin = self.baslangic
        except Exception as e:                         # noqa: BLE001
            log.warning("ilerleme mesaji gonderilemedi: %s", e)
        self._is_parcacigi = threading.Thread(target=self._nabiz, daemon=True)
        self._is_parcacigi.start()
        return self

    def __exit__(self, *_) -> bool:
        self._dur.set()
        if self._is_parcacigi:
            # Kisa join: nabiz is parcacigi daemon ve yalnizca uyuyor;
            # beklemek cevabi geciktirmemeli.
            self._is_parcacigi.join(timeout=0.2)
        self.temizle()
        return False                                   # istisnayi yutma

    # ------------------------------------------------------------------
    def _nabiz(self) -> None:
        """
        "yaziyor…" gostergesini canli tutar.

        AYRI IS PARCACIGI SART: bot tek is parcacikli ve `cevapla()`
        dongusu bloklu calisiyor; ayni is parcacigindan periyodik bir
        sey gondermenin yolu yok.
        """
        while not self._dur.is_set():
            try:
                self.tg.chat_action(self.chat_id, "typing")
            except Exception:                          # noqa: BLE001
                pass                                   # gosterge, is degil
            self._dur.wait(NABIZ_SN)

    # ------------------------------------------------------------------
    def arac_gordu(self, arac: str) -> None:
        """
        `chat.py` akis dongusunden cagrilir — model bir arac cagirdiginda.

        Bu, ilerlemeyi GERCEK yapan baglanti noktasi: kullaniciya
        "calisiyorum" degil "portfoyune bakiyorum" denir.
        """
        with self._kilit:
            if arac in self._araclar:
                return                                 # ayni araci tekrar yazma
            self._araclar.append(arac)
            sira = len(self._araclar)
        self.guncelle(f"🔎 {_sade_ad(arac)}…",
                      alt=f"<i>{sira} adim</i>" if sira > 1 else None)

    def guncelle(self, metin: str, alt: str | None = None) -> None:
        """
        Durum mesajini degistirir. HIZ SINIRI ve TEKRAR ELEMESI iceride.

        Cagiran taraf bunlari dusunmek zorunda kalmamali: bir arac
        dongusu saniyede birkac kez cagirabilir ve her biri Telegram'a
        istek olsaydi hem hiz sinirina takilirdi hem de ayni metin 400
        "message is not modified" uretirdi.
        """
        if not self.aktif or self.message_id is None:
            return
        tam = f"{metin}\n{alt}" if alt else metin
        simdi = time.monotonic()
        with self._kilit:
            if tam == self._son_metin:
                return
            if simdi - self._son_yazma < DUZENLEME_ARALIGI_SN:
                return
            self._son_metin, self._son_yazma = tam, simdi
        try:
            self.tg.edit_message(self.message_id, tam, chat_id=self.chat_id)
        except Exception as e:                         # noqa: BLE001
            log.debug("ilerleme guncellenemedi: %s", e)

    # ------------------------------------------------------------------
    def temizle(self) -> None:
        """
        Durum mesajini siler.

        SILINIYOR, "bitti"ye cevrilmiyor: asil cevap hemen ardindan
        geliyor ve ekranda kalan bir "⏳" satiri gurultudur. Silme
        basarisiz olursa sorun degil — kullanici yalnizca cevabin
        ustunde bir durum satiri gorur, cevap yine tam gelir.
        """
        if self.message_id is None:
            return
        try:
            self.tg.delete_message(self.message_id, chat_id=self.chat_id)
        except Exception as e:                         # noqa: BLE001
            log.debug("ilerleme mesaji silinemedi: %s", e)
        finally:
            self.message_id = None
