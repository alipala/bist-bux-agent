"""
Bekci — sistemin kendi kesintilerini FARK EDIP RAPOR ETMESI.

TEMEL SINIR: COKEN SISTEM "COKTUM" DIYEMEZ
------------------------------------------
Bu modul her seyi cozmez, cozemez. Mac kapaliyken ya da internet yokken
hicbir yerel surec mesaj gonderemez. Yapabilecegi tek durust sey:
GERI DONDUGUNDE ne kadar sure kapali kaldigini SOYLEMEK.

"Su anda kapaliyim" bildirimi ancak DISARIDAN bir izleyiciyle mumkun
(dead man's switch); bunun icin `disari_ping()` var ve ayri bir servise
periyodik sinyal gonderir — sinyal kesilirse O servis haber verir.

UC KATMAN
---------
1. KALP ATISI  Bot her dongude zaman damgasi yazar. Acilista onceki damga
   ile simdi karsilastirilir; fark buyukse kesinti yasanmis demektir.
2. NABIZ GOZCUSU  Zamanlanmis nabiz calisti mi? Calismadiysa SESSIZ
   BASARISIZLIK olur — en tehlikeli arizadir, cunku hicbir sey olmamis
   gibi gorunur.
3. DIS PING  Makine tamamen kapandiginda haber alabilmenin tek yolu.

SPAM ONLEME
-----------
Bot yapilandirma hatasiyla surekli yeniden basliyorsa (launchd
ThrottleInterval 60 sn), her kalkista mesaj atmak dakikada bir bildirim
demektir. Bu yuzden bildirimler `SESSIZLIK_SURESI` boyunca susturulur ve
kisa yeniden baslatmalar (deploy gibi) esik altinda kalir.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Bu suren altindaki kesinti RAPOR EDILMEZ: elle yeniden baslatma,
# guncelleme, kisa bir cokme. Gurultu yapmanin anlami yok.
ONEMLI_KESINTI = timedelta(minutes=10)

# Ayni turden bildirim bu sure icinde TEKRARLANMAZ.
SESSIZLIK_SURESI = timedelta(hours=6)

# Kalp atisi bu araliktan seyrek yazilmaz (long-poll 50 sn surdugu icin
# dongu basi yazmak zaten ~1 dakikada bir demek).
KALP_ARALIGI = timedelta(minutes=2)


def _simdi() -> datetime:
    return datetime.now(timezone.utc)


class Bekci:
    def __init__(self, settings, db, state_dir):
        self.s = settings
        self.db = db
        self.dosya = state_dir / "watchdog.json"
        self._son_yazim = None

    # ------------------------------------------------------------------
    def _oku(self) -> dict:
        try:
            return json.loads(self.dosya.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _yaz(self, veri: dict) -> None:
        try:
            self.dosya.write_text(json.dumps(veri, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except OSError as e:                          # noqa: BLE001
            log.warning("[bekci] durum yazilamadi: %s", e)

    # --- 1) kalp atisi ------------------------------------------------
    def kalp_at(self, cevrimici: bool = True) -> None:
        """
        Dongu basi cagrilir. Diske her seferinde yazmaz — long-poll zaten
        ~1 dk surdugu icin dakikada bir yazim gereksiz I/O olurdu.

        `cevrimici=False`: Telegram'a ulasilamiyor. Bunu AYRI kaydediyoruz
        cunku "bot calisiyordu ama internet yoktu" ile "bot oluydu" farkli
        arizalar ve kullaniciya farkli sey soylenmeli.
        """
        n = _simdi()
        if self._son_yazim and n - self._son_yazim < KALP_ARALIGI:
            return
        self._son_yazim = n
        d = self._oku()
        d["son_kalp"] = n.isoformat()
        d["pid"] = os.getpid()
        if cevrimici:
            d["son_cevrimici"] = n.isoformat()
        self._yaz(d)

    def kesinti(self) -> dict | None:
        """
        Acilista cagrilir. Onceki kalp atisiyla simdi arasindaki fark.

        Doner: {"sure_dk", "bas", "son", "tur"} veya None (kesinti yok /
        ilk calistirma / esigin altinda).
        """
        d = self._oku()
        ham = d.get("son_kalp")
        if not ham:
            return None                              # ilk kez calisiyor
        try:
            son = datetime.fromisoformat(ham)
        except ValueError:
            return None
        n = _simdi()
        fark = n - son
        if fark < ONEMLI_KESINTI:
            return None

        # Internet mi gitti, sistem mi kapandi? son_cevrimici ile ayirt
        # ediyoruz: bot calisiyor ama Telegram'a ulasamiyorsa kalp atisi
        # ilerler, son_cevrimici ilerlemez.
        tur = "sistem kapali"
        cevrimici_ham = d.get("son_cevrimici")
        if cevrimici_ham:
            try:
                if datetime.fromisoformat(cevrimici_ham) < son - timedelta(minutes=2):
                    tur = "baglanti kopuk (bot calisiyordu)"
            except ValueError:
                pass
        return {"sure_dk": int(fark.total_seconds() // 60),
                "bas": son, "son": n, "tur": tur}

    # --- 2) nabiz gozcusu ---------------------------------------------
    def kacirilan_nabiz(self) -> dict | None:
        """
        Bugun nabiz calismasi gerekiyorduysa CALISTI MI?

        Sessiz basarisizlik en tehlikeli ariza: hicbir sey olmamis gibi
        gorunur, sen de veri geldigini sanirsin. Bu yuzden bot, ayakta
        oldugu surece zamanlanmis isi de gozetliyor.
        """
        n = _simdi()
        if n.weekday() >= 5:
            return None                              # hafta sonu zaten calismaz
        # Nabiz 22:15'te baslar ve ~9 dk surer; 23:00'ten once yargilamayiz.
        if n.hour < 23:
            return None
        bugun = n.strftime("%Y-%m-%d")
        r = self.db.query(
            "SELECT COUNT(*) c FROM signals WHERE olusma_ts = ?", (bugun,))
        if r and r[0]["c"]:
            return None
        return {"gun": bugun,
                "not": "Zamanlanmis nabiz bugun sinyal uretmedi. Ya calismadi "
                       "ya da hata aldi."}

    # --- bildirim (susturmali) ----------------------------------------
    def bildir(self, anahtar: str, mesaj: str) -> bool:
        """
        Ayni turden bildirimi SESSIZLIK_SURESI boyunca tekrarlamaz.

        Gerekce: bot yapilandirma hatasiyla surekli yeniden basliyorsa
        (launchd 60 sn'de bir dener) her kalkista mesaj atmak dakikada
        bir bildirim demektir.
        """
        d = self._oku()
        gecmis = d.setdefault("bildirimler", {})
        n = _simdi()
        onceki = gecmis.get(anahtar)
        if onceki:
            try:
                if n - datetime.fromisoformat(onceki) < SESSIZLIK_SURESI:
                    log.info("[bekci] '%s' susturuldu (yakin zamanda gonderildi)",
                             anahtar)
                    return False
            except ValueError:
                pass
        try:
            from ..notify import TelegramNotifier
            TelegramNotifier(self.s).send_message(mesaj)
        except Exception as e:                        # noqa: BLE001
            log.warning("[bekci] bildirim gonderilemedi: %s", e)
            return False
        gecmis[anahtar] = n.isoformat()
        self._yaz(d)
        return True

    # --- 3) dis izleyici ----------------------------------------------
    def disari_ping(self) -> None:
        """
        Dead man's switch. Makine TAMAMEN kapandiginda haber alabilmenin
        tek yolu: disaridaki bir servise duzenli sinyal gonderilir, sinyal
        kesilince O SERVIS uyari yollar.

        `.env` -> HEARTBEAT_URL tanimliysa calisir, yoksa sessizce atlanir.
        healthchecks.io ucretsiz katmani bu ise yeterli.
        """
        url = os.environ.get("HEARTBEAT_URL", "").strip()
        if not url:
            return
        try:
            import httpx
            httpx.get(url, timeout=10)
        except Exception as e:                        # noqa: BLE001
            log.debug("[bekci] dis ping basarisiz: %s", e)
