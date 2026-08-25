"""
IBKR katmani birim testleri — AG YOK, CANLI KANAL YOK, UZUN UYKU YOK.

    .venv/bin/python tests/test_ibkr.py

Bu dosyada gercek bir HTTP istegi yapilmaz ve gercek `time.sleep`
cagrilmaz. Hiz sinirinin dogru calistigini uyku suresini OLCEREK degil
HESAPLANAN degeri yakalayarak dogruluyoruz — 5 saniyelik bir sinir icin
5 saniye beklemek testi kullanilamaz yapardi.

CANLI KANAL YOK: `Oturum` bildirimleri enjekte edilen bir cagrilabilire
gonderiyor; testte listeye yaziliyor. Bu depoda testin Ali'ye GERCEK mesaj
gonderdigi bir vaka yasandi (bkz. tests/test_smoke.py ust aciklamasi), o
yuzden burada Telegram'a giden hicbir yol yok.

BAGIMLILIK YOK: depo konvansiyonu duz fonksiyon + asagidaki kosucu.
pytest ile de calisir ama onu GEREKTIRMEZ.
"""
from __future__ import annotations

import sys
import time as _time
from contextlib import contextmanager
from pathlib import Path

import httpx

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from finagent.ibkr.istemci import (  # noqa: E402
    GENEL_ARALIK_SN,
    DurumBilinmiyorHatasi,
    HizHatasi,
    IbkrHatasi,
    Istemci,
    UlasilamadiHatasi,
    YetkiHatasi,
)
from finagent.ibkr.oturum import Durum, Oturum  # noqa: E402


@contextmanager
def firlatir(tip):
    """pytest.raises'in bagimliliksiz esdegeri."""
    try:
        yield
    except tip:
        return
    except Exception as e:                                 # noqa: BLE001
        raise AssertionError(f"{tip.__name__} bekleniyordu, {type(e).__name__} geldi")
    raise AssertionError(f"{tip.__name__} bekleniyordu, istisna gelmedi")


def yakin(a, b, tol=0.01):
    assert abs(a - b) <= tol, f"{a} != {b} (tolerans {tol})"


# ----------------------------------------------------------------------
# Taklit HTTP katmani
# ----------------------------------------------------------------------
class SahteYanit:
    def __init__(self, kod=200, veri=None, govde=b"{}"):
        self.status_code = kod
        self._veri = veri
        self.content = govde if veri is None else b"x"

    def json(self):
        if self._veri is None:
            raise ValueError("json yok")
        return self._veri


class SahteOturum:
    """`httpx.Client` yerine gecer; cagrilari kaydeder."""

    def __init__(self, yanitlar=None, firlat=None):
        self.cagrilar: list[tuple[str, str]] = []
        self._yanitlar = yanitlar or {}
        self._firlat = firlat

    def request(self, yontem, url, **kw):
        self.cagrilar.append((yontem, url))
        if self._firlat:
            raise self._firlat
        for parca, y in self._yanitlar.items():
            if parca in url:
                return y
        return SahteYanit(200, {})


def _istemci(sahte=None, taban="https://localhost:5001/v1/api"):
    c = Istemci(taban)
    c._istemci = sahte or SahteOturum()
    return c


@contextmanager
def sahte_saat():
    """
    `time.sleep` cagrilmaz; suresi kaydedilir ve saat ILERLETILIR — ki
    bir sonraki cagri "zaten bekledik" gorsun.
    """
    uykular: list[float] = []
    saat = {"t": 1000.0}
    yedek_m, yedek_s = _time.monotonic, _time.sleep

    def uyu(sn):
        uykular.append(sn)
        saat["t"] += sn

    _time.monotonic = lambda: saat["t"]
    _time.sleep = uyu
    try:
        yield uykular
    finally:
        _time.monotonic, _time.sleep = yedek_m, yedek_s


# ----------------------------------------------------------------------
# Hiz siniri  —  429 = 10 dakika IP ceza kutusu, tekrari KALICI engel
# ----------------------------------------------------------------------
def test_uc_araliginda_en_uzun_onek_kazanir():
    """
    `/iserver/account/orders` birden fazla kurala uyabilir; EN OZEL
    kural gecerli olmali.
    """
    c = Istemci()
    assert c._uc_araligi("/iserver/account/orders") == ("/iserver/account/orders", 5.0)
    # Kurali olmayan uc: yalnizca genel sinir gecerli.
    assert c._uc_araligi("/iserver/marketdata/snapshot") == ("", 0.0)


def test_genel_sinir_ardarda_cagrida_uygulanir():
    c = _istemci()
    with sahte_saat() as uykular:
        c.get("/iserver/marketdata/snapshot")
        c.get("/iserver/marketdata/snapshot")
    assert len(uykular) == 1, f"beklenen 1 bekleme, gelen {uykular}"
    yakin(uykular[0], GENEL_ARALIK_SN)


def test_yavas_uc_bes_saniye_bekletir():
    """/portfolio/accounts 5 saniyede BIR istek (IBKR pacing tablosu)."""
    c = _istemci()
    with sahte_saat() as uykular:
        c.get("/portfolio/accounts")
        c.get("/portfolio/accounts")
    assert len(uykular) == 1
    yakin(uykular[0], 5.0)


def test_farkli_uclar_birbirini_bes_saniye_bekletmez():
    """Uc bazli sinir UCA ozel; baska bir uc onun damgasini yememeli."""
    c = _istemci()
    with sahte_saat() as uykular:
        c.get("/portfolio/accounts")
        c.get("/iserver/marketdata/snapshot")
    assert not uykular or uykular[0] < 1.0, f"gereksiz bekleme: {uykular}"


# ----------------------------------------------------------------------
# Hata siniflandirmasi
# ----------------------------------------------------------------------
def test_post_zaman_asimi_DURUM_BILINMIYOR_olur():
    """
    EN KRITIK TEST. Zaman asimina ugrayan POST sunucuya ULASMIS OLABILIR.
    Bunu siradan bir baglanti hatasi saymak cagirani yeniden denemeye
    davet eder — gercek parayla CIFT EMIR demektir.
    """
    c = _istemci(SahteOturum(firlat=httpx.TimeoutException("timeout")))
    with firlatir(DurumBilinmiyorHatasi):
        c.post("/iserver/account/DU1/orders", [{"conid": 1}])


def test_get_zaman_asimi_sadece_ulasilamadi():
    """GET idempotent; POST ile ayni istisna sinifina girmemeli."""
    c = _istemci(SahteOturum(firlat=httpx.TimeoutException("timeout")))
    with firlatir(UlasilamadiHatasi):
        c.get("/portfolio/accounts")


def test_http_kodlari_dogru_siniflaniyor():
    for kod, beklenen in ((401, YetkiHatasi), (403, YetkiHatasi),
                          (429, HizHatasi), (500, IbkrHatasi)):
        c = _istemci(SahteOturum({"": SahteYanit(kod)}))
        with firlatir(beklenen):
            c.get("/portfolio/accounts")


def test_bozuk_json_sessizce_bos_sayilmaz():
    """
    Bozuk yaniti "veri yok" saymak, bu depodaki en kotu hata sinifina
    (yanlis "yok" beyani) giden yol. Acikca patlamali.
    """
    c = _istemci(SahteOturum({"": SahteYanit(200, None, govde=b"<html>")}))
    with firlatir(IbkrHatasi):
        c.get("/portfolio/accounts")


def test_sertifika_dogrulamasi_yalnizca_localhostta_kapali():
    """
    Taban URL disari cikarsa `verify=False` GERCEK bir guvenlik acigi
    olur. Yerel istisna yerel kalmali.
    """
    assert _istemci(taban="https://localhost:5001/v1/api")._yerel_mi() is True
    assert _istemci(taban="https://127.0.0.1:5001/v1/api")._yerel_mi() is True
    assert _istemci(taban="https://api.ibkr.com/v1/api")._yerel_mi() is False


# ----------------------------------------------------------------------
# Oturum
# ----------------------------------------------------------------------
def test_kullanilabilir_ucunu_de_ister():
    """`kullanilabilir` tek dogru soru — ve ucu birden gerekiyor."""
    assert Durum(True, True, True).kullanilabilir is True
    assert Durum(True, True, False).kullanilabilir is False
    assert Durum(True, False, True).kullanilabilir is False
    assert Durum(False, True, True).kullanilabilir is False


def test_401_ulasilamadi_DEGIL_giris_yapilmamis():
    """
    Bu ayrim COZUMU degistiriyor: biri java baslatmak, digeri
    tarayicidan giris yapmak. Birlestirmek kullaniciyi yanlis isi
    yapmaya gonderir.
    """
    o = Oturum(_istemci(SahteOturum({"auth/status": SahteYanit(401)})))
    d = o.durumu_oku(zorla=True)
    assert d.ulasilabilir is True
    assert d.kimlik_dogrulandi is False
    assert "giris" in d.mesaj


def test_gateway_kapaliysa_ulasilamadi():
    o = Oturum(_istemci(SahteOturum(firlat=httpx.ConnectError("yok"))))
    d = o.durumu_oku(zorla=True)
    assert d.ulasilabilir is False
    assert d.kimlik_dogrulandi is False


def test_bagli_ama_dogrulanmamis_oturum_KENDILIGINDEN_kurulur():
    """
    IBKR: `connected: true` + `authenticated: false` = oturum zaman
    asimina ugramis ama arkauc baglantisi duruyor; ssodh/init yeniden
    kurar, ELLE GIRIS GEREKMEZ. Gunde bir elle giris zaten yeterince
    kisitliyken, 6 dakikalik her dususte kullaniciyi tarayiciya
    gondermek bu katmani kullanilamaz yapardi.
    """
    sahte = SahteOturum({
        "auth/status": SahteYanit(200, {"authenticated": False, "connected": True}),
        "ssodh/init": SahteYanit(200, {"authenticated": True, "connected": True}),
    })
    o = Oturum(_istemci(sahte))
    o._tik()
    assert any("ssodh/init" in u for _, u in sahte.cagrilar), \
        "bagli-ama-dogrulanmamis durumda init cagrilmaliydi"


def test_yaris_varsayilan_olarak_KAPALI():
    """
    `compete: true` baska oturumlari DUSURUR. Varsayilan acik olsaydi,
    Ali tarayicidan Client Portal'a girdiginde bot onu sessizce disari
    atardi — teshisi en zor ariza turu.
    """
    o = Oturum(_istemci(SahteOturum({"ssodh/init": SahteYanit(200, {"authenticated": True})})))
    o.kur()
    assert o._yaris is False


def test_tik_asla_istisna_sizdirmaz():
    """IBKR arizasi Telegram botunu susturamaz."""
    class Patlak:
        def post(self, *a, **k):
            raise RuntimeError("beklenmeyen")

        def get(self, *a, **k):
            raise RuntimeError("beklenmeyen")

    Oturum(Patlak()).tik()          # patlarsa test kirmizi olur


def test_bildirim_yalnizca_DEGISIMDE_gider():
    """
    Her turda "IBKR kapali" yazmak kullaniciyi bildirimleri kapatmaya
    iter — ve kapatilan bildirim, hic olmayan bildirimden kotudur.
    """
    haberler: list[tuple[str, str]] = []
    o = Oturum(_istemci(), bildir=lambda a, m: haberler.append((a, m)))

    o.durum = Durum(True, True, True)
    o._onceki_kullanilabilir = None
    o._gecisleri_bildir()
    assert haberler == [], "ilk olcum sessiz olmali"

    o._gecisleri_bildir()
    assert haberler == [], "degisim yokken bildirim gitmemeli"

    o.durum = Durum(True, False, True, mesaj="giris yapilmamis")
    o._gecisleri_bildir()
    assert [a for a, _ in haberler] == ["ibkr_oturum"]

    o.durum = Durum(True, True, True)
    o._gecisleri_bildir()
    assert [a for a, _ in haberler] == ["ibkr_oturum", "ibkr_oturum_geldi"]


def test_rakip_oturum_ayrica_ve_BIR_KEZ_bildirilir():
    """
    Rakip oturum, oturum HALA calisirken de olabilir — ama her an
    dusebilir ve sebebi kullanicinin kendi davranisi. Ayri bildirim.
    """
    haberler: list[tuple[str, str]] = []
    o = Oturum(_istemci(), bildir=lambda a, m: haberler.append((a, m)))
    o._onceki_kullanilabilir = True
    o.durum = Durum(True, True, True, rakip_oturum=True)
    o._gecisleri_bildir()
    assert [a for a, _ in haberler] == ["ibkr_rakip"]
    o._gecisleri_bildir()
    assert len(haberler) == 1, "ayni rakip oturum tekrar bildirilmemeli"


# ----------------------------------------------------------------------
# Canli / kagit ayrimi — betikteki KANIT kontrolu
# ----------------------------------------------------------------------
def test_canli_kagit_ayrimi_KANITA_dayanir():
    """
    IBKR'de canli/kagit bir bayrak DEGIL, ayri bir kullanici adi.
    `.env`'de "paper" yazmasi hicbir sey kanitlamaz; tek mesru kanit
    /portfolio/accounts yanitidir.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ibkr_baglanti", KOK / "scripts" / "ibkr_baglanti.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    kagit_mi = mod._kagit_mi

    assert kagit_mi({"accountId": "DU1234567", "type": "DEMO"}) is True
    assert kagit_mi({"accountId": "DU1234567"}) is True
    assert kagit_mi({"accountId": "U2812345", "type": "INDIVIDUAL"}) is False
    # Bilinmiyor "canli" DEGIL: emin olmadigimizda canli varsaymak,
    # kagit sandigi hesaba gercek emir gondermenin yoludur.
    assert kagit_mi({}) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum IBKR testleri gecti.")
