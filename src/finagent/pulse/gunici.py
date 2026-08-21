"""
GUN ICI KOSU — LLM YOK, yalnizca esik kontrolu.

NEDEN VAR
---------
Koruma seviyesi ve tez kosulu gunde dort kez, GUNLUK KAPANISLA kontrol
ediliyordu. Yani bir stop sabah 10:30'da kirildiysa kullanici bunu
17:45 kosusunda ogreniyordu — yedi saat sonra. Kullanicinin bilmek
istedigi an, KIRILDIGI andir.

NEDEN LLM YOK
-------------
Burada yorumlanacak bir sey yok: "kapanis su seviyenin altina indi mi"
bir karsilastirma. Model cagirmak hem pahali (gunde ~16 kosu) hem
gereksiz, ustelik panelin duvar saati sorunu burada kat kat buyurdu.
Deterministik katman hesaplar, LLM yorumlar — bu kosu tamamen ilk
katmanda.

KAPSAM SINIRI ACIKCA BEYAN EDILIYOR
-----------------------------------
Gun ici yalnizca FIYAT SEVIYESI kontrol edilir:
  * koruma seviyeleri (2N-ATR stop)
  * `close` alanina dayanan tez kosullari
RSI/SMA/hacim/CAR tabanli kosullar GUNLUK gostergelerdir ve gun ici
hesaplanmaz — saatlik bardan uretilen bir "RSI14" gunluk RSI ile ayni
ad altinda BASKA bir sey olurdu ve iki katman birbiriyle celisirdi.
O kosullar gunluk kosularda kontrol edilmeye devam ediyor.

SEVIYE YUKSELTILMEZ
-------------------
Ratchet (stop'un yukari kilitlenmesi) GUNLUK kapanisla calisir. Gun ici
yukseltseydik stop gun icinde yukselir ve ayni gunun geri cekilmesiyle
kirilirdi — kendi urettigi alarmi calan bir mekanizma.

SESSIZLIK GECERLI CIKTIDIR
--------------------------
Kirilan bir sey yoksa mesaj GITMEZ. Gunde ~16 kosu x "bugun bir sey
yok" mesaji, bildirimlerin kapatilmasinin en hizli yolu olurdu.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Gun ici katmanin KAPSADIGI borsalar — saatlik verisi olanlar (B4).
# Avrupa kotasyonlari saatlik toplanmiyor, dolayisiyla burada da yok.
KAPSAM_BORSALARI = ("BIST", "ABD")


def acik_borsalar(simdi: datetime | None = None) -> list[str]:
    """
    Kapsamdaki borsalardan su an ACIK olanlar.

    Seans bilgisi `piyasa.seans_durumlari`den — ikinci bir saat tablosu
    yazmak, bu projenin tekrar eden kusur sinifi olurdu (ayni gercek iki
    yerde beyan edilir ve sessizce ayrisir).
    """
    from ..piyasa import seans_durumlari
    return [s["borsa"] for s in seans_durumlari(simdi)
            if s["borsa"] in KAPSAM_BORSALARI and s["acik"]]


class GunIci:
    """Piyasa saatinde calisan, LLM'siz esik kontrolu."""

    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    # ------------------------------------------------------------------
    def calistir(self, bildir: bool = True, sahip: str | None = None,
                 topla: bool = True) -> dict:
        """
        Doner: {"durum": "kapali"|"kostu", ...}

        `topla=False` yalnizca test ve elle kosu icin — gercek kosuda
        saatlik veri tazelenmeden kontrol etmek, BAYAT barla karar
        vermek demektir.
        """
        ayar = self.s.gunici_ayari()
        if not ayar["enabled"]:
            return {"durum": "kapali", "sebep": "ritim.gunici.enabled: false"}

        acik = acik_borsalar()
        if not acik:
            # PIYASA KAPALIYKEN HICBIR SEY YAPILMAZ ve bu bir ARIZA
            # DEGILDIR. Bekci de pencere disinda sessiz kalir.
            log.info("[gunici] kapsamdaki borsalarin hicbiri acik degil — "
                     "kontrol yok")
            return {"durum": "kapali", "sebep": "borsa kapali", "acik": []}

        sahipler = ([sahip] if sahip else list(ayar["alicilar"]))
        if not sahipler:
            raise ValueError(
                "gunici: alici yok. config/settings.yaml -> ritim.gunici")

        toplama = None
        if topla:
            toplama = self._saatlik_tazele()

        sonuc, gonderilen = {}, 0
        for s in sahipler:
            try:
                sonuc[s] = self._sahip(s, bildir)
                gonderilen += sonuc[s].get("gonderilen", 0)
            except Exception as e:                    # noqa: BLE001
                # IZOLASYON: bir sahibin hatasi digerini DURDURMAZ.
                log.exception("[gunici] '%s' kontrolu patladi", s)
                sonuc[s] = {"hata": f"{type(e).__name__}: {e}"}

        self._iz_birak(acik, sahipler, gonderilen)
        return {"durum": "kostu", "acik": acik, "sahipler": sahipler,
                "toplama": toplama, "gonderilen": gonderilen, "sonuc": sonuc}

    # ------------------------------------------------------------------
    def _saatlik_tazele(self) -> dict:
        """
        Saatlik barlari tazeler. HATA KOSUYU DUSURMEZ — eldeki barlarla
        kontrol yine calisir; tazelik kapisi (`GUN_ICI_AZAMI_YAS_DK`)
        bayat veriyle alarm uretilmesini zaten engelliyor.
        """
        try:
            from ..collectors import REGISTRY
            r = REGISTRY["saatlik"](self.s, self.db).run()
            return {"durum": r.status, "satir": r.rows, "ms": r.duration_ms}
        except Exception as e:                        # noqa: BLE001
            log.warning("[gunici] saatlik tazeleme patladi: %s", e)
            return {"durum": "error", "sebep": str(e)[:200]}

    def _sahip(self, sahip: str, bildir: bool) -> dict:
        from .journal import Defter
        from .koruma import Koruma

        koruma = Koruma(self.db)
        kirilan = koruma.gun_ici_kontrol(sahip)
        bozulan = Defter(self.db).gun_ici_tez_kontrol(sahip)

        gonderilen = 0
        if kirilan and self._koruma_bildir(sahip, kirilan, bildir, koruma):
            gonderilen += len(kirilan)
        if bozulan and self._tez_bildir(sahip, bozulan, bildir):
            gonderilen += len(bozulan)
        if not kirilan and not bozulan:
            # SESSIZLIK GECERLI CIKTI: mesaj gitmiyor, log yeter.
            log.info("[gunici/%s] esigi gecen yok — mesaj YOK", sahip)
        return {"koruma_kirilan": len(kirilan), "tez_bozulan": len(bozulan),
                "gonderilen": gonderilen}

    # ------------------------------------------------------------------
    def _koruma_bildir(self, sahip: str, kirilan: list[dict], bildir: bool,
                       koruma) -> bool:
        """Tespit -> TESLIMAT -> damga. Sira sozlesmesi degismiyor."""
        if not bildir:
            log.info("[gunici/%s] bildirim kapali — koruma kirilimi "
                     "damgalanmadi (%d kayit)", sahip, len(kirilan))
            return False
        return self._gonder(sahip, self._koruma_metni(kirilan),
                            lambda: koruma.damgala(kirilan))

    def _tez_bildir(self, sahip: str, bozulan: list[dict],
                    bildir: bool) -> bool:
        from .journal import Defter
        if not bildir:
            log.info("[gunici/%s] bildirim kapali — tez alarmi "
                     "damgalanmadi (%d kayit)", sahip, len(bozulan))
            return False
        defter = Defter(self.db)
        return self._gonder(sahip, self._tez_metni(bozulan),
                            lambda: defter.tez_damgala(bozulan))

    def _gonder(self, sahip: str, metin: str, damgala) -> bool:
        from ..notify import TelegramNotifier

        chatler = self.s.sahip_chatleri(sahip)
        if not chatler:
            log.error("[gunici] '%s' sahibinin chat_id'si YOK — mesaj "
                      "gonderilemedi, damga ATILMADI", sahip)
            return False
        tg = TelegramNotifier(self.s)
        giden = False
        for chat in chatler:
            try:
                giden = tg.send_message(metin, chat_id=chat) or giden
            except Exception as e:                    # noqa: BLE001
                log.warning("[gunici] %s/%s gonderilemedi: %s", sahip, chat, e)
        if not giden:
            log.error("[gunici/%s] ALARM GONDERILEMEDI — damga atilmadi, "
                      "sonraki kosu yeniden deneyecek", sahip)
            return False
        damgala()
        return True

    # ------------------------------------------------------------------
    @staticmethod
    def _kisa(v) -> str:
        if v is None:
            return "?"
        try:
            return f"{float(v):.6g}"
        except (TypeError, ValueError):
            return str(v)

    def _koruma_metni(self, kirilan: list[dict]) -> str:
        e = _esc
        L = ["🛡 <b>GUN ICI · koruma seviyesi kirildi</b>"]
        for k in kirilan:
            pb = k.get("para_birimi") or ""
            L.append(f"\n<b>{e(k['sembol'])}</b> ({e(str(k['hesap']).upper())})")
            L.append(f"Saatlik kapanis <b>{self._kisa(k['kapanis'])} {e(pb)}</b> "
                     f"· stop <code>{self._kisa(k['stop'])}</code> "
                     f"({k['mesafe_pct']:+.1f}%)")
            L.append(f"<i>Bar {e(k['bar_ts'])} UTC · seviye "
                     f"{str(k['kuruldu_ts'])[:10]} tarihinde kuruldu.</i>")
        L.append("\n<i>SEANS ICI bir olcum: gunluk kapanis bunun ustune "
                 "donebilir. Satis tavsiyesi DEGIL; sistem emir gondermez.</i>")
        return "\n".join(L)

    def _tez_metni(self, bozulan: list[dict]) -> str:
        e = _esc
        L = ["🔔 <b>GUN ICI · tez alarmi</b>"]
        for b in bozulan:
            L.append(f"\n<b>{e(b['sembol'])} tezi bozuldu</b>")
            if b.get("tez"):
                L.append(f"<i>{b['olusma_ts']}: {e(str(b['tez'])[:200])}</i>")
            L.append(f"Kosul <code>{e(b['kosul'])}</code> · saatlik "
                     f"{b['alan']}: <b>{self._kisa(b['deger'])}</b>")
        L.append("\n<i>SEANS ICI olculdu. Onceden ACIKCA yazilmis bir esigin "
                 "gerceklestigi bildiriliyor; al/sat tavsiyesi degil.</i>")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def _iz_birak(self, acik: list, sahipler: list, gonderilen: int) -> None:
        """
        Kosu izi — bekcinin KANITI. Isin SONUNDA yaziliyor: yarim kalan
        kosu iz birakmaz ve bekci bunu yakalar.
        """
        import json
        from pathlib import Path
        try:
            dizin = Path(self.s.bot_state_dir) / "kosu"
            dizin.mkdir(parents=True, exist_ok=True)
            (dizin / "gunici.json").write_text(json.dumps({
                "kip": "gunici",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "acik_borsalar": list(acik), "sahipler": list(sahipler),
                "gonderilen": gonderilen,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as e:                        # noqa: BLE001
            log.warning("[gunici] kosu izi yazilamadi: %s", e)


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
