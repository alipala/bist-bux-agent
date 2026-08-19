"""
BEKLEYEN ONAY DEPOSU — `data/bot/pending/` dizininin yasam dongusu.

NEDEN AYRI MODUL
----------------
Onay dosyalarinin acilmasi `tools.py`de, okunmasi/silinmesi `listener.py`de
ve toplu islenmesi bir ucuncu yerdeydi. Ucunde de kalip AYNIYDI ve ucu de
AYNI HATAYI yapiyordu: dosyayi ISTEN ONCE siliyordu.

    parsed = json.loads(pending.read_text())
    pending.unlink()          # <-- is daha baslamadi
    ... yazma ...             # <-- burada olursek istek BUHAR OLDU

Surec sert olurse (OOM/kill) `.bitti` yazilmaz, dinleyici isi YENIDEN
DENER — ama onay dosyasi artik yok. Yeniden deneme "bu istek artik
gecerli degil" der, kullanici hicbir sey gormez ve veritabanina yazilip
yazilmadigi BILINMEZ. Sessiz basarisizligin tam tarifi.

UC DURUM, DOSYA ADIYLA GORUNUR
------------------------------
    <token>.json        bekliyor   — onay verilebilir
    <token>.isleniyor   sahiplenildi — is ucusta (ya da surec olduyse asili)
    <token>.hata        basarisiz  — sebebi icinde, elle incelenebilir

Gecis ATOMIK (`os.replace`): iki kez basilan buton ikinci seferde dosyayi
sahiplenemez, "zaten isleniyor" cevabini alir. Onceki tasarimda ikinci
basis dosyayi zaten silinmis buluyor ve "gecersiz istek" diyordu — dogru
cevap degil, cunku istek gecersiz DEGILDI, sadece tuketilmisti.

IKI YAS SINIRI, IKI FARKLI SORU
-------------------------------
    TAZE (15 dk)  "kaydet" YAZISI hangi istege baglanir?
    OMUR  (24 sa) toplu/otomatik yollar hangi istegi hala isler?

Ikisi ayri karar. Bir gun onceki okuma HALA gecerlidir (butonuna
basilabilir) ama "kaydet" kelimesi ona baglanmamalidir: kullanici o
kelimeyi yazarken KAFASINDA baska bir sey vardir. Suresi dolan dosya
SESSIZCE SILINMEZ — `/bekleyen` onu gosterir, silmek `/unut` ister;
sessiz silme, sessiz yazmanin ikizidir.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Dogal dilde "kaydet" YALNIZCA bu kadar yeni bir istege baglanir.
# Daha eskisi icin bot ozeti yeniden gosterir ve butona basilmasini ister.
TAZE = timedelta(minutes=15)

# Bundan eski istekler toplu (`/onayla`) ve dogal dil yollarina GIRMEZ.
# Butonu hala calisir — kullanici o mesaja bakip basiyorsa ne onayladigini
# GORUYOR demektir; tehlike gormeden onaylanan yollarda.
OMUR = timedelta(hours=24)

_BEKLIYOR, _ISLENIYOR, _HATA = ".json", ".isleniyor", ".hata"


@dataclass(frozen=True)
class Onay:
    """Sahiplenilmis ya da bekleyen tek bir onay istegi."""
    token: str
    yol: Path
    veri: dict
    yas_sn: float

    @property
    def tip(self) -> str:
        # `_tip` tasimayan ESKI dosyalar pozisyon okumasidir (tek tip vardi).
        return self.veri.get("_tip", "pozisyon")

    @property
    def taze_mi(self) -> bool:
        return self.yas_sn <= TAZE.total_seconds()

    @property
    def suresi_doldu_mu(self) -> bool:
        return self.yas_sn > OMUR.total_seconds()


def yas_metni(saniye: float) -> str:
    if saniye < 60:
        return "az once"
    if saniye < 3600:
        return f"{int(saniye // 60)} dakika once"
    if saniye < 86400:
        return f"{int(saniye // 3600)} saat once"
    return f"{int(saniye // 86400)} gun once"


class OnayDeposu:
    """
    `pending/` dizini uzerinde durum tutmayan (stateless) kapi.

    Durum tutmuyor cunku `pending_dir` calisma sirasinda degisebiliyor
    (testler dogrudan atiyor) ve iki farkli SUREC ayni dizine bakiyor:
    dinleyici ile worker. Tek gercek DISKTIR; bellekte kopya tutmak o
    iki gorusu ayirir.
    """

    def __init__(self, dizin: Path):
        self.dizin = Path(dizin)

    # --- yazma ---------------------------------------------------------
    def yaz(self, token: str, veri: dict) -> Path:
        self.dizin.mkdir(parents=True, exist_ok=True)
        yol = self.dizin / f"{token}{_BEKLIYOR}"
        yol.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
        return yol

    # --- okuma ---------------------------------------------------------
    def _yukle(self, yol: Path) -> Onay | None:
        try:
            veri = json.loads(yol.read_text(encoding="utf-8"))
            yas = max(0.0, time.time() - yol.stat().st_mtime)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("onay dosyasi okunamadi (%s): %s", yol, e)
            return None
        if not isinstance(veri, dict):
            return None
        return Onay(token=yol.stem, yol=yol, veri=veri, yas_sn=yas)

    def oku(self, token: str) -> dict | None:
        """
        Istegin verisi — HANGI DURUMDA olursa olsun.

        Butonun etiketi bunu okuyor. Yalnizca `.json`a bakilirsa hata
        almis bir `sil_son` istegi icin uretilen TEKRAR DENE butonu
        varsayilana duser ve uzerinde "✅ Kaydet" yazar — yikici bir
        islemi kaydetme gibi gosteren tam olarak o hata.
        """
        for son in (_BEKLIYOR, _ISLENIYOR, _HATA):
            o = self.dizin / f"{token}{son}"
            if o.exists():
                yuklu = self._yukle(o)
                if yuklu is not None:
                    return yuklu.veri
        return None

    def durum(self, token: str) -> str:
        """'bekliyor' | 'isleniyor' | 'hata' | 'yok'."""
        for son, ad in ((_BEKLIYOR, "bekliyor"), (_ISLENIYOR, "isleniyor"),
                        (_HATA, "hata")):
            if (self.dizin / f"{token}{son}").exists():
                return ad
        return "yok"

    def bekleyenler(self, chat_id=None, tipler=None,
                    azami_yas: timedelta | None = None,
                    tek_sahipli: bool = False) -> list[Onay]:
        """
        Bekleyen istekler, ESKIDEN YENIYE.

        `chat_id` verilirse yalnizca o sohbetinkiler. Suzmezsek A'nin
        bekleyen okumasi B'nin `/onayla` komutuyla A'nin portfoyune
        yazilirdi.

        `_chat_id` tasimayan ESKI dosyalar sahipsiz sayilir ve yalnizca
        `tek_sahipli=True` iken islenir — cok kullanicida atlanir, cunku
        kime ait oldugu BILINMIYOR ve tahmin etmek yanlis yazma riski.
        """
        out: list[Onay] = []
        for yol in sorted(self.dizin.glob(f"*{_BEKLIYOR}")):
            o = self._yukle(yol)
            if o is None:
                continue
            if tipler is not None and o.tip not in tipler:
                continue
            if azami_yas is not None and o.yas_sn > azami_yas.total_seconds():
                continue
            if chat_id is not None:
                sahibi = o.veri.get("_chat_id")
                if sahibi is None:
                    if not tek_sahipli:
                        continue
                elif str(sahibi) != str(chat_id):
                    continue
            out.append(o)
        out.sort(key=lambda o: o.yas_sn, reverse=True)
        return out

    # --- yasam dongusu -------------------------------------------------
    def sahiplen(self, token: str) -> Onay | None:
        """
        Istegi ATOMIK olarak sahiplenir: `.json` -> `.isleniyor`.

        `os.replace` tek bir dosya sistemi islemidir; iki surec ayni anda
        denerse yalnizca biri basarir. Ikinci basis None alir ve
        `durum()` ona NEDEN alamadigini soyler.

        ONCE SAHIPLEN, SONRA CALIS. Ters sirada — eski davranis — is
        ortasinda olen surec istegi de beraberinde goturuyordu.
        """
        kaynak = self.dizin / f"{token}{_BEKLIYOR}"
        hedef = self.dizin / f"{token}{_ISLENIYOR}"
        try:
            os.replace(kaynak, hedef)
        except OSError:
            return None
        return self._yukle(hedef)

    def tamamla(self, onay: Onay) -> None:
        onay.yol.unlink(missing_ok=True)

    def hataya_dus(self, onay: Onay, sebep: str) -> Path | None:
        """
        Basarisiz istegi `.hata` olarak SAKLAR — silmez.

        Silmek "hic olmamis" demek olurdu; oysa veritabanina KISMEN
        yazilmis olabilir. Dosya diskte kalirsa ne istendigi sonradan
        okunabilir.
        """
        hedef = self.dizin / f"{onay.token}{_HATA}"
        try:
            veri = dict(onay.veri)
            veri["_hata"] = sebep[:500]
            hedef.write_text(json.dumps(veri, ensure_ascii=False),
                             encoding="utf-8")
            onay.yol.unlink(missing_ok=True)
            return hedef
        except OSError as e:
            log.error("onay hata dosyasi yazilamadi (%s): %s", onay.token, e)
            return None

    def geri_koy(self, onay: Onay) -> bool:
        """Sahiplenilen istegi yeniden BEKLIYOR yapar (is hic baslamadiysa)."""
        try:
            os.replace(onay.yol, self.dizin / f"{onay.token}{_BEKLIYOR}")
            return True
        except OSError:
            return False

    def sil(self, token: str) -> int:
        """Bir istegin TUM izlerini siler (`/unut` ve iptal icin)."""
        n = 0
        for son in (_BEKLIYOR, _ISLENIYOR, _HATA):
            yol = self.dizin / f"{token}{son}"
            if yol.exists():
                yol.unlink(missing_ok=True)
                n += 1
        return n

    def asili_isler(self, esik: timedelta = timedelta(minutes=30)) -> list[Onay]:
        """
        Sahiplenilmis ama bitmemis istekler — surec sert olduyse burada.

        Bekcinin bakmasi gereken yer burasi: `.isleniyor` dosyasi
        esikten eskiyse o is ya cok uzun suruyor ya da SUREC OLDU.

        ESIK 30 DK, 10 DEGIL: onay arkasindaki isler her zaman kisa
        degil — `rapor` tipi collector kosuyor ve dakikalar suruyor.
        Dar esik, calisan bir isi "yarim kalmis" diye raporlardi ve bu
        projenin gecmisi zaten YANLIS ALARMLA dolu.
        """
        out = []
        for yol in sorted(self.dizin.glob(f"*{_ISLENIYOR}")):
            o = self._yukle(yol)
            if o is not None and o.yas_sn > esik.total_seconds():
                out.append(o)
        return out
