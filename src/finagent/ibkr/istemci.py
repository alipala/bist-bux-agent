"""
IBKR Web API HTTP istemcisi — HIZ SINIRI ICERIDE.

NEDEN HIZ SINIRI BURADA, CAGIRAN TARAFTA DEGIL
----------------------------------------------
IBKR asma cezasini IP'ye kesiyor, istege degil:

    "When a rate limit is exceeded, the Web API will return a 429...
     Violator IP addresses may be put in a PENALTY BOX FOR 10 MINUTES.
     Repeat violator IP addresses may be PERMANENTLY BLOCKED."

Yani tek bir dikkatsiz dongu, botu on dakika kor birakir; tekrari kalici
engel. Bu depoda cagiran taraf cok olacak (portfoy, fiyat, emir, teshis) —
her birinin ayri ayri dogru davranmasini ummak yerine sinir TEK KAPIDA.

IKI SINIR VAR, IKISI DE UYGULANIYOR
-----------------------------------
    Genel : CPGW uzerinden 10 istek/sn   (dogrudan api.ibkr.com'da 50 —
            BIZ CPGW kullaniyoruz, dusuk olan gecerli)
    Uc bazli: bazi uclar cok daha yavas. Ornegin /portfolio/accounts ve
            /iserver/orders 5 saniyede BIR istek.

Uc bazli sinirlar bir "en erken calisabilir" damgasiyla tutuluyor; istek
gerekirse UYUYOR. Sessizce dusurulmuyor — cagiran veriyi bekliyor.

SERTIFIKA DOGRULAMASI KAPALI, VE BU BILINCLI
--------------------------------------------
Gateway kendinden imzali sertifika kullaniyor; IBKR bunu "beklenen" diyor.
Dogrulanmayan bacak yalnizca kullanici ile KENDI localhost'u arasinda;
localhost'tan IBKR'ye giden bacak TLS ile korunuyor. Taban URL localhost
disina cikarsa bu varsayim COKER — bu yuzden `_yerel_mi` kontrolu var.

EMIR POST'U ASLA YENIDEN DENENMEZ
---------------------------------
GET'ler guvenli (idempotent), yeniden denenebilir. POST oyle degil: zaman
asimina ugrayan bir emir gonderimi SUNUCUYA ULASMIS OLABILIR. Yeniden
denemek cift emir demektir — gercek parayla. Bu yuzden `post()` varsayilan
olarak YENIDEN DENEMEZ ve zaman asimi ayri bir hata tipiyle donuyor ki
cagiran "bilmiyorum" durumunu ayirt edebilsin.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# macOS'ta 5000 portunu Control Center (AirPlay) tutuyor; IBKR SSS'i port
# degistirmeyi oneriyor. Bu depo 5001 kullaniyor (config/ibkr/conf.finagent.yaml).
VARSAYILAN_TABAN = "https://localhost:5001/v1/api"

# IBKR'nin tek istedigi baslik — gateway digerlerini kendisi ekliyor.
BASLIKLAR = {"User-Agent": "finagent/1.0", "Content-Type": "application/json"}

# CPGW genel siniri: 10 istek/sn. %20 pay birakiliyor.
GENEL_ARALIK_SN = 0.12

# Uc bazli asgari araliklar (saniye). Kaynak: IBKR "Pacing Limitations".
# Anahtar, yolun BASLANGICI olarak eslesiyor.
UC_ARALIKLARI: dict[str, float] = {
    "/portfolio/accounts": 5.0,
    "/portfolio/subaccounts": 5.0,
    "/iserver/orders": 5.0,
    "/iserver/account/orders": 5.0,       # ayni sinir, farkli yol adi
    "/iserver/trades": 5.0,
    "/iserver/account/pnl/partitioned": 5.0,
    "/iserver/scanner/run": 1.0,
    "/iserver/scanner/params": 900.0,     # 15 dakika
    "/pa/": 900.0,
    "/tickle": 1.0,
    "/sso/validate": 60.0,
}

ZAMAN_ASIMI_SN = 15.0


def _govde_ozeti(y, azami: int = 400) -> str:
    """
    Hata govdesinden OKUNABILIR sebep. Gizli alan sizdirmaz.

    IBKR hatayi cogunlukla {"error": "..."} ya da {"message": "..."}
    olarak donduruyor; bazen duz metin. Hepsi tolere ediliyor cunku
    ONEMLI OLAN sebebin kullaniciya ULASMASI.
    """
    try:
        d = y.json()
    except Exception:                                      # noqa: BLE001
        ham = (getattr(y, "text", "") or "").strip()
        return ham[:azami] or "(govde bos)"
    if isinstance(d, dict):
        for anahtar in ("error", "message", "msg", "detail", "text"):
            v = d.get(anahtar)
            if isinstance(v, str) and v.strip():
                return v.strip()[:azami]
    return str(d)[:azami]


class IbkrHatasi(Exception):
    """IBKR katmani taban hatasi."""


class UlasilamadiHatasi(IbkrHatasi):
    """Gateway'e hic ulasilamadi — java sureci calismiyor olabilir."""


class YetkiHatasi(IbkrHatasi):
    """401/403 — tarayicidan giris yapilmamis ya da oturum dusmus."""


class HizHatasi(IbkrHatasi):
    """429 — CEZA KUTUSU RISKI. Cagiran GERI CEKILMELI."""


class DurumBilinmiyorHatasi(IbkrHatasi):
    """
    POST zaman asimina ugradi. Istek sunucuya ULASMIS OLABILIR.

    Bu hata GORULDUGUNDE yeniden denemek YASAK; once mutabakat yapilmali
    (emir icin: /iserver/account/orders ile acik emirlere bakilir).
    """


class Istemci:
    """
    Tek kapi. Butun IBKR istekleri buradan gecer.

    Is parcacigi guvenli: hiz sinir damgalari kilit altinda tutuluyor,
    cunku bot dinleyicisi ile isci surecler ayni istemciyi paylasabilir.
    """

    def __init__(self, taban: str | None = None, zaman_asimi: float = ZAMAN_ASIMI_SN):
        self.taban = (taban or VARSAYILAN_TABAN).rstrip("/")
        self._zaman_asimi = zaman_asimi
        self._kilit = threading.Lock()
        self._son_istek = 0.0                 # genel sinir icin
        self._uc_damgalari: dict[str, float] = {}
        self._istemci: httpx.Client | None = None

    # ------------------------------------------------------------------
    def _yerel_mi(self) -> bool:
        return self.taban.startswith(("https://localhost", "https://127.0.0.1"))

    def _oturum(self) -> httpx.Client:
        if self._istemci is None:
            # Sertifika dogrulamasi YALNIZCA localhost icin kapali. Taban
            # URL disari cikarsa dogrulama ACIK olmali — aksi halde
            # "gecici cozum" sessizce gercek bir guvenlik acigina donusur.
            self._istemci = httpx.Client(
                verify=not self._yerel_mi(),
                timeout=self._zaman_asimi,
                headers=BASLIKLAR,
            )
        return self._istemci

    def kapat(self) -> None:
        if self._istemci is not None:
            self._istemci.close()
            self._istemci = None

    # ------------------------------------------------------------------
    def _uc_araligi(self, yol: str) -> tuple[str, float]:
        """Yola uyan EN UZUN onek kazanir (en ozel kural)."""
        en_iyi, sure = "", 0.0
        for onek, sn in UC_ARALIKLARI.items():
            if yol.startswith(onek) and len(onek) > len(en_iyi):
                en_iyi, sure = onek, sn
        return en_iyi, sure

    def _bekle(self, yol: str) -> None:
        """Genel ve uc bazli sinirlarin IKISINI de uygular."""
        with self._kilit:
            simdi = time.monotonic()

            genel_hedef = self._son_istek + GENEL_ARALIK_SN
            onek, sure = self._uc_araligi(yol)
            uc_hedef = (self._uc_damgalari.get(onek, 0.0) + sure) if onek else 0.0

            hedef = max(genel_hedef, uc_hedef)
            uyku = hedef - simdi
            if uyku > 0:
                # Uzun beklemeler GORULMELI: 15 dakikalik bir sinire
                # takilan cagri, sessizce donmus gibi gorunmemeli.
                if uyku > 2:
                    log.info("[ibkr] hiz siniri: %s icin %.1f sn bekleniyor", yol, uyku)
                time.sleep(uyku)
                simdi = hedef

            self._son_istek = simdi
            if onek:
                self._uc_damgalari[onek] = simdi

    # ------------------------------------------------------------------
    def _cagir(self, yontem: str, yol: str, **kw) -> Any:
        if not yol.startswith("/"):
            yol = "/" + yol
        self._bekle(yol)
        url = f"{self.taban}{yol}"
        try:
            y = self._oturum().request(yontem, url, **kw)
        except httpx.TimeoutException as e:
            if yontem.upper() == "POST":
                raise DurumBilinmiyorHatasi(f"POST {yol} zaman asimi") from e
            raise UlasilamadiHatasi(f"{yontem} {yol} zaman asimi") from e
        except httpx.RequestError as e:
            raise UlasilamadiHatasi(f"gateway'e ulasilamadi: {type(e).__name__}") from e

        if y.status_code in (401, 403):
            raise YetkiHatasi(f"{yol} -> HTTP {y.status_code} (giris gerekiyor)")
        if y.status_code == 429:
            # CEZA KUTUSU UYARISI. Bunu debug seviyesinde loglamak, on
            # dakikalik korlugun sebebini gizlerdi.
            log.error("[ibkr] 429 — HIZ SINIRI ASILDI (%s). IP 10 dk ceza "
                      "kutusuna girebilir; tekrari KALICI engel.", yol)
            raise HizHatasi(f"{yol} -> 429")
        if y.status_code >= 400:
            # IBKR'NIN SEBEBI YUTULMAZ.
            #
            # Ilk surum yalnizca "-> HTTP 400" diyordu ve sahada tam bir
            # kore donusturdu: emir reddedildi, kullanici "HTTP 400"
            # gordu, SEBEBI hicbir yerde yoktu (2026-08-26, ilk canli
            # emir denemesi). Oysa IBKR govdede acikca yaziyordu.
            #
            # "Arizayi acik et" ilkesini kendi istemcimde cignemisim:
            # hata mesaji, hatanin NE OLDUGUNU soylemiyorsa hata mesaji
            # degildir.
            raise IbkrHatasi(f"{yol} -> HTTP {y.status_code}: "
                             f"{_govde_ozeti(y)}")

        if not y.content:
            return None
        try:
            return y.json()
        except ValueError as e:
            # Bozuk JSON'u "veri yok" saymak, bu depodaki en kotu hata
            # sinifina (yanlis "yok" beyani) giden yol. Acikca patlat.
            raise IbkrHatasi(f"{yol} -> JSON cozulemedi") from e

    # ------------------------------------------------------------------
    def get(self, yol: str, params: dict | None = None) -> Any:
        return self._cagir("GET", yol, params=params)

    def post(self, yol: str, govde: Any = None) -> Any:
        """
        POST — YENIDEN DENEME YOK.

        Zaman asimi `DurumBilinmiyorHatasi` olarak cikar. Cagiran bunu
        yakalayip MUTABAKAT yapmali, yeniden denememeli.
        """
        return self._cagir("POST", yol, json=govde if govde is not None else {})

    def delete(self, yol: str) -> Any:
        return self._cagir("DELETE", yol)
