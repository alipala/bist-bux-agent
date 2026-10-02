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
import time
import time as _time
from contextlib import contextmanager
from pathlib import Path

import httpx

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

# Dis kapi: testler gercek Jev API'sine gitmemeli (bkz. test_smoke
# `_yan_etki_kapisi`). Bos dize = anahtar yok; dotenv bos dizeyi EZMEZ.
import os as _os_kapi  # noqa: E402
_os_kapi.environ["TYPESAFE_API_KEY"] = ""

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

    def close(self):
        """`Istemci.kapat()` bunu cagiriyor — taklit de kapanabilmeli."""

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


def test_RAKIP_OTURUM_VARKEN_init_ATLANIR():
    """
    `compete: true` baska oturumlari DUSURUR. Ali Client Portal'daysa
    bot onu sessizce disari atmamali — teshisi en zor ariza turu.

    Koruma `compete` bayragini kapatmakla DEGIL, rakip oturumu GORUP
    dokunmamakla saglaniyor: bayragi kapatmak init'i tamamen
    engelliyordu (IBKR: "Force compete capability must be used together
    with compete flag").
    """
    sahte = SahteOturum({"ssodh/init": SahteYanit(200, {"authenticated": True})})
    o = Oturum(_istemci(sahte))
    o.durum = Durum(True, False, True, rakip_oturum=True)
    # `None` = HIC DENENMEDI. `False`dan ayri, cunku `_tik`in geri
    # cekilmesi yalnizca GERCEKTEN denenip basarisiz olan istekte
    # devreye girmeli: rakip oturumda hicbir istek gitmiyor ve onu
    # "basarisiz" saymak, Ali telefondan cikinca botun 30 dakika
    # bosuna beklemesi demekti.
    assert o.kur() is None, "rakip oturum varken init denendi"
    assert not any("ssodh/init" in u for _, u in sahte.cagrilar)

    # `yaris` acikca istenmisse dokunulur.
    sahte2 = SahteOturum({"ssodh/init": SahteYanit(200, {"authenticated": True})})
    o2 = Oturum(_istemci(sahte2), yaris=True)
    o2.durum = Durum(True, False, True, rakip_oturum=True)
    assert o2.kur() is True


def test_init_COMPETE_TRUE_gonderir():
    """
    OLCULDU: `compete: false` -> {"passed": false}, fail: "Force compete
    capability must be used together with compete flag".
    `compete: true` -> dirildi.
    """
    kaydedilen = {}

    class Kaydeden(SahteOturum):
        def request(self, yontem, url, **kw):
            if "ssodh/init" in url:
                kaydedilen["govde"] = kw.get("json")
                return SahteYanit(200, {"authenticated": True})
            return super().request(yontem, url, **kw)

    Oturum(_istemci(Kaydeden())).kur()
    assert kaydedilen["govde"]["compete"] is True
    assert kaydedilen["govde"]["publish"] is True


def test_BAGLI_OLMASA_DA_init_denenir():
    """
    Ilk surum `connected: true` SART kosuyordu ve sahada oturum
    `connected: false` dustu — init HIC DENENMEDI. Yanlis kosul,
    calisan bir kurtarma yolunu gorunmez yapmisti.
    """
    sahte = SahteOturum({
        "auth/status": SahteYanit(200, {"authenticated": False,
                                        "connected": False}),
        "ssodh/init": SahteYanit(200, {"authenticated": True,
                                       "connected": True}),
    })
    o = Oturum(_istemci(sahte))
    o._tik()
    assert any("ssodh/init" in u for _, u in sahte.cagrilar), \
        "connected=false iken init denenmedi"


def test_durum_sorgusu_SSOEXPIRES_OLCUMUNU_SILMEZ():
    """
    GERILEME TESTI — ilk surumde bu hata VARDI ve sahada yakalandi.

    `durumu_oku()` her cagrida taze bir `Durum` kuruyor. `ssoExpires`
    yalnizca /tickle yanitinda geliyor, auth/status'ta yok. Devredilmezse
    her durum sorgusu olcumu siliyordu; `_tikle()` karsilastiracak bir
    "onceki" deger bulamiyor ve OLCUM SATIRI HIC BASILMIYORDU. Iki
    dakika boyunca oturum ayakta gorunuyordu ama olcum yoktu — sessiz
    bir kayip.
    """
    sahte = SahteOturum({
        "auth/status": SahteYanit(200, {"authenticated": True, "connected": True}),
        "tickle": SahteYanit(200, {"ssoExpires": 540000}),
    })
    o = Oturum(_istemci(sahte))
    o._tikle()
    assert o.durum.oturum_bitis_sn == 540

    o.durumu_oku(zorla=True)
    assert o.durum.oturum_bitis_sn == 540, \
        "durum sorgusu ssoExpires olcumunu sildi"


def test_ikinci_tikleme_OLCUMU_KARSILASTIRIR():
    """
    Iki saat var (hareketsizlik zaman asimi / SSO omru) ve ikincisinin
    nasil davrandigini BILMIYORUZ. Ogrenmenin tek yolu her tiklemede
    degeri ve gecen sureyi kaydetmek — bu depoda tahmin isabetim kotu.
    """
    sahte = SahteOturum({"tickle": SahteYanit(200, {"ssoExpires": 540000})})
    o = Oturum(_istemci(sahte))
    assert o._onceki_bitis is None
    o._tikle()
    assert o._onceki_bitis == 540, "ilk olcum kaydedilmedi"
    o._tikle()
    assert o._onceki_bitis == 540, "ikinci olcum karsilastirma icin kalmali"


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
    o = Oturum(_istemci(), bildir=lambda a, m, **k: haberler.append((a, m)))

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
    o = Oturum(_istemci(), bildir=lambda a, m, **k: haberler.append((a, m)))
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


# ----------------------------------------------------------------------
# Portfoy okuma
# ----------------------------------------------------------------------
from finagent.ibkr.portfoy import Portfoy, _sayi  # noqa: E402

HESAP_YANITI = SahteYanit(200, [{
    "accountId": "U2812345", "id": "U2812345", "type": "INDIVIDUAL",
    "currency": "EUR", "brokerageAccess": True,
}])


def _portfoy(ek=None):
    yanitlar = {"portfolio/accounts": HESAP_YANITI}
    yanitlar.update(ek or {})
    sahte = SahteOturum(yanitlar)
    return Portfoy(_istemci(sahte)), sahte


def test_sembol_DESCRIPTION_alanindan_gelir():
    """
    IBKR pozisyon semasinda `symbol` DIYE BIR ALAN YOK — sembol
    `description` icinde ("Contract's local symbol"). Naif bir
    `r.get("symbol")` sessizce bos donerdi ve pozisyonlar isimsiz
    yazilirdi.
    """
    p, _ = _portfoy({"positions": SahteYanit(200, [{
        "conid": 265598, "description": "AAPL", "position": 10,
        "avgPrice": 100.0, "marketPrice": 110.0, "marketValue": 1100.0,
        "unrealizedPnl": 100.0, "currency": "USD", "assetClass": "STK",
    }])})
    poz = p.pozisyonlar()
    assert len(poz) == 1
    assert poz[0].sembol == "AAPL"
    assert poz[0].conid == "265598"


def test_avgCost_NULL_ise_avgPrice_kullanilir():
    """IBKR'nin KENDI ornek yanitinda avgCost null, avgPrice dolu."""
    p, _ = _portfoy({"positions": SahteYanit(200, [{
        "description": "AAPL", "position": 10,
        "avgCost": None, "avgPrice": 262.24,
    }])})
    assert p.pozisyonlar()[0].ort_maliyet == 262.24


def test_SIFIR_adetli_pozisyon_yazilmaz():
    """
    Kapanmis pozisyon satirda kalabiliyor. Portfoye sifir adetli satir
    yazmak "elimde var" gibi gorunur.
    """
    p, _ = _portfoy({"positions": SahteYanit(200, [
        {"description": "AAPL", "position": 0, "avgPrice": 100.0},
        {"description": "MSFT", "position": 5, "avgPrice": 100.0},
    ])})
    assert [x.sembol for x in p.pozisyonlar()] == ["MSFT"]


def test_maliyet_bilinmiyorsa_pnl_yuzdesi_UYDURULMAZ():
    """
    Maliyet yoksa piyasa degerinden geri hesaplamak, uydurulmus bir
    maliyetten uydurulmus bir getiri uretirdi.
    """
    p, _ = _portfoy({"positions": SahteYanit(200, [{
        "description": "AAPL", "position": 10,
        "avgCost": None, "avgPrice": None, "unrealizedPnl": 100.0,
    }])})
    poz = p.pozisyonlar()[0]
    assert poz.pnl_abs == 100.0
    assert poz.pnl_pct is None


def test_BASE_bir_para_birimi_degil():
    """
    Ledger'da `BASE` anahtari taban para biriminde TOPLAM. Para birimi
    sanip listeye katmak, toplami iki kez saymak olurdu.
    """
    p, _ = _portfoy({"ledger": SahteYanit(200, {
        "EUR": {"cashbalance": 6.0, "netliquidationvalue": 6.0},
        "USD": {"cashbalance": 100.0, "netliquidationvalue": 100.0},
        "BASE": {"cashbalance": 106.0, "netliquidationvalue": 106.0},
    })})
    assert sorted(p.nakit()) == ["EUR", "USD"], "BASE para birimi sayilmamali"


def test_hesap_listesi_pozisyondan_ONCE_cagrilir():
    """
    IBKR sarti: "/portfolio/accounts ... must be called prior to this
    endpoint." Sirayi SINIF tutuyor, cagiranin hatirlamasi gerekmiyor.
    """
    p, sahte = _portfoy({"positions": SahteYanit(200, [])})
    p.pozisyonlar()
    yollar = [u for _, u in sahte.cagrilar]
    assert any("portfolio/accounts" in u for u in yollar)
    assert yollar.index(next(u for u in yollar if "portfolio/accounts" in u)) \
        < yollar.index(next(u for u in yollar if "positions" in u))


def test_hesap_listesi_ONBELLEKLENIR():
    """/portfolio/accounts 5 saniyede BIR istekle sinirli."""
    p, sahte = _portfoy({"positions": SahteYanit(200, [])})
    p.pozisyonlar()
    p.pozisyonlar()
    n = sum(1 for _, u in sahte.cagrilar if "portfolio/accounts" in u)
    assert n == 1, f"hesap listesi {n} kez cagrildi"


def test_IKI_hesapta_sessizce_ilkini_SECMEZ():
    """Sessizce ilkini secmek, yanlis portfoye bakmanin en kolay yolu."""
    sahte = SahteOturum({"portfolio/accounts": SahteYanit(200, [
        {"accountId": "U111", "type": "INDIVIDUAL"},
        {"accountId": "DU222", "type": "DEMO"},
    ])})
    p = Portfoy(_istemci(sahte))
    with firlatir(IbkrHatasi):
        p.pozisyonlar()
    # Ama hesap ACIKCA belirtilirse calisir.
    p2 = Portfoy(_istemci(SahteOturum({
        "portfolio/accounts": SahteYanit(200, [
            {"accountId": "U111", "type": "INDIVIDUAL"},
            {"accountId": "DU222", "type": "DEMO"}]),
        "positions": SahteYanit(200, [])})))
    assert p2.pozisyonlar("DU222") == []
    # Var olmayan hesap da sessizce baskasina dusmez.
    with firlatir(IbkrHatasi):
        p2.pozisyonlar("U999")


def test_sayi_cozumu_BILINMIYOR_ile_SIFIRI_ayirir():
    """
    IBKR sayilari tutarsiz donduruyor (portfoyde float, piyasa
    verisinde binlik ayracli STRING). Cozulemeyen deger None olmali —
    0.0 degil, cunku "bilmiyorum" ile "sifir" ayri seylerdir.
    """
    assert _sayi("1,300") == 1300.0
    assert _sayi(6.0) == 6.0
    assert _sayi("") is None
    assert _sayi("C168.42") is None       # bozuk/onekli deger sifir DEGIL
    assert _sayi(None) is None
    assert _sayi(0) == 0.0


# ----------------------------------------------------------------------
# conid cozumu — CANLI VERIDEN alinmis yanitlarla
# ----------------------------------------------------------------------
from finagent.ibkr.kimlik import (  # noqa: E402
    SONEK_BORSA, _kayitlari_coz, _taban_ve_borsa,
)

# Asagidaki yanitlar 2026-08-25/26'da GERCEK `/trsrv/stocks` cagrilarindan
# alindi — uydurulmadi. Tuzaklarin hepsi gercek.
AMZN_YANITI = [
    {"name": "AMAZON.COM INC", "assetClass": "STK", "contracts": [
        {"conid": 3691937, "exchange": "NASDAQ", "isUS": True},
        {"conid": 38708590, "exchange": "MEXI", "isUS": False},
        {"conid": 305691292, "exchange": "EBS", "isUS": False}]},
    {"name": "LS 1X AMZN", "assetClass": "STK", "contracts": [
        {"conid": 493546040, "exchange": "LSEETF", "isUS": False}]},
    {"name": "AMAZON.COM INC - CDR", "assetClass": "STK", "contracts": [
        {"conid": 532497536, "exchange": "TSE", "isUS": False}]},
]

ASML_YANITI = [
    {"name": "ASML HOLDING NV", "assetClass": "STK", "contracts": [
        {"conid": 117589399, "exchange": "AEB", "isUS": False}]},
    {"name": "ASML HOLDING NV-NY REG SHS", "assetClass": "STK", "contracts": [
        {"conid": 117902840, "exchange": "NASDAQ", "isUS": True}]},
    {"name": "ASML HOLDING NV", "assetClass": "STK", "contracts": [
        {"conid": 762277717, "exchange": "TSE", "isUS": False}]},
]

IWDA_YANITI = [
    {"name": "ISHARES CORE MSCI WORLD", "assetClass": "STK", "contracts": [
        {"conid": 65071241, "exchange": "LSEETF", "isUS": False},
        {"conid": 100292038, "exchange": "AEB", "isUS": False}]},
]


def test_KALDIRACLI_ETF_ad_kapisinda_elenir():
    """
    'AMZN' sorgusu 'LS 1X AMZN' de donduruyor — kaldiracli bir ETF.
    Yanlisini secmek, Amazon almak isterken kaldiracli ETF almaktir.
    """
    s = _kayitlari_coz("AMZN", "Amazon.com, Inc.", AMZN_YANITI)
    assert s.conid == "3691937"
    assert s.borsa == "NASDAQ"


def test_CDR_ad_kapisini_GECER_borsa_kurali_eler():
    """
    'AMAZON.COM INC - CDR' katalog adiyla ORTUSUYOR (belirtec altkumesi),
    yani ad kapisi onu ELEMIYOR. Eleyen sey ABD listesi kurali.
    Bu, ad kapisinin tek basina yetmediginin kanitidir.
    """
    assert ayni_sirket_kontrol("Amazon.com, Inc.", "AMAZON.COM INC - CDR") is True
    s = _kayitlari_coz("AMZN", "Amazon.com, Inc.", AMZN_YANITI)
    assert s.conid != "532497536", "CDR secildi"


def ayni_sirket_kontrol(a, b):
    from finagent.research.identity import ayni_sirket
    return ayni_sirket(a, b)


def test_AYNI_ADLA_IKI_BORSA_ad_kapisiyla_ayirt_edilemez():
    """
    ASML'de IKI kayit AYNI adi tasiyor ('ASML HOLDING NV' — Amsterdam ve
    Tokyo). Ad tek basina yeterli olsaydi bu ikisi ayirt edilemezdi;
    ABD listesi kurali NASDAQ'i seciyor.
    """
    s = _kayitlari_coz("ASML", "ASML Holding N.V.", ASML_YANITI)
    assert s.conid == "117902840"
    assert s.borsa == "NASDAQ"


def test_katalogda_ad_yoksa_COZULMEZ():
    """
    `prices._ad_dogrulayarak` ile ayni kural: dogrulanacak bir sey yoksa
    dogrulanmis sayilmaz.
    """
    s = _kayitlari_coz("AMZN", None, AMZN_YANITI)
    assert s.conid is None and "ad yok" in s.sebep
    s = _kayitlari_coz("AMZN", "   ", AMZN_YANITI)
    assert s.conid is None


def test_ad_hic_tutmuyorsa_COZULMEZ():
    """Vicarious/Avalo sinifi: benzeyen sembol, baska sirket."""
    s = _kayitlari_coz("RBOT", "iShares Automation & Robotics", [
        {"name": "VICARIOUS SURGICAL INC-A", "assetClass": "STK",
         "contracts": [{"conid": 1, "exchange": "NYSE", "isUS": True}]}])
    assert s.conid is None
    assert "ad eslesmedi" in s.sebep


def test_SONEK_borsa_haritasinda_yoksa_TABAN_DENENMEZ():
    """
    'BRK.B' bir borsa soneki DEGIL, hisse sinifi. Olculdu: 'BRK' taban
    sembolu dort ayri sirket donduruyor (Brooks Macdonald, Brookside
    Energy, Berkshire CDR, SSIF BRK) — hicbiri aradigimiz degil.
    """
    assert _taban_ve_borsa("BRK.B") == ("BRK.B", None)
    assert "B" not in SONEK_BORSA


def test_SONEK_borsayi_soyluyorsa_KULLANILIR():
    assert _taban_ve_borsa("ABN.AS") == ("ABN", frozenset({"AEB"}))
    assert _taban_ve_borsa("4GLD.DE")[0] == "4GLD"
    assert "IBIS" in _taban_ve_borsa("4GLD.DE")[1]
    assert _taban_ve_borsa("AMZN") == ("AMZN", None)


def test_SONEK_iki_borsa_arasindan_DOGRUSUNU_secer():
    """
    'IWDA' taban sembolu LSEETF ve AEB donduruyor. Sonek ('.AS') hangisi
    oldugunu soyluyor. Sonek olmasaydi BELIRSIZ kalirdi.
    """
    taban, borsalar = _taban_ve_borsa("IWDA.AS")
    s = _kayitlari_coz("IWDA.AS", "iShares Core MSCI World", IWDA_YANITI, borsalar)
    assert s.conid == "100292038"
    assert s.borsa == "AEB"


def test_SONEK_borsasinda_ABD_TERCIHI_uygulanmaz():
    """
    Sonek zaten borsayi soyluyor; ABD tercihi devrede olsaydi sonekli
    sembol sessizce YANLIS borsaya baglanabilirdi.
    """
    yanit = [{"name": "X CORP", "assetClass": "STK", "contracts": [
        {"conid": 11, "exchange": "NYSE", "isUS": True},
        {"conid": 22, "exchange": "AEB", "isUS": False}]}]
    s = _kayitlari_coz("X.AS", "X Corp", yanit, frozenset({"AEB"}))
    assert s.conid == "22", "sonek AEB derken ABD listesi secildi"


def test_sonek_borsasi_yoksa_COZULMEZ():
    """GOLD.AS vakasi: katalog Amsterdam diyor, IBKR'de NYSE/SBF var."""
    yanit = [{"name": "GOLD CORP", "assetClass": "STK", "contracts": [
        {"conid": 11, "exchange": "NYSE", "isUS": True},
        {"conid": 33, "exchange": "SBF", "isUS": False}]}]
    s = _kayitlari_coz("GOLD.AS", "Gold Corp", yanit, frozenset({"AEB"}))
    assert s.conid is None
    assert "sonek borsasi" in s.sebep


def test_STK_disi_varlik_siniflari_dikkate_alinmaz():
    s = _kayitlari_coz("X", "X Corp", [
        {"name": "X CORP", "assetClass": "OPT",
         "contracts": [{"conid": 9, "exchange": "NASDAQ", "isUS": True}]}])
    assert s.conid is None


# ----------------------------------------------------------------------
# save_conid — DAR yazici
# ----------------------------------------------------------------------
def _gecici_db():
    import tempfile
    from finagent.storage.db import Database
    yol = Path(tempfile.mkdtemp()) / "t.db"
    d = Database(yol)
    d.init_schema()
    return d


def test_save_conid_SEC_ALANLARINA_DOKUNMAZ():
    """
    GERILEME KORUMASI. `save_identity` cik/sec_name/status/method'un
    HEPSINI yaziyor; conid icin o kullanilsaydi SEC'te cozulmus kimlik
    ezilirdi. Bu hata bu depoda bir kez yasandi (EDGAR kullanicinin
    `/kimlik` duzeltmesini siliyordu).
    """
    d = _gecici_db()
    iid = d.upsert_instrument("AMZN", "BUX", "Amazon.com, Inc.")
    d._conn.execute(
        """INSERT INTO identities (instrument_id, cik, sec_name, status, method)
           VALUES (?,?,?,?,?)""", (iid, "0001018724", "AMAZON COM INC",
                                   "dogrulandi", "ticker+ad"))
    d._conn.commit()

    assert d.save_conid(iid, "3691937") is True
    r = d.query("SELECT * FROM identities WHERE instrument_id=?", (iid,))[0]
    assert r["conid"] == "3691937"
    assert r["cik"] == "0001018724", "cik ezildi"
    assert r["sec_name"] == "AMAZON COM INC", "sec_name ezildi"
    assert r["status"] == "dogrulandi", "status ezildi"
    assert r["method"] == "ticker+ad", "method ezildi"
    d.close()


def test_save_conid_MEVCUDU_EZMEZ():
    """Elle duzeltilmis baglantiyi her kosuda yeniden yazmak, sessiz
    kayma icin davetiye."""
    d = _gecici_db()
    iid = d.upsert_instrument("AMZN", "BUX", "Amazon.com, Inc.")
    assert d.save_conid(iid, "111") is True
    assert d.save_conid(iid, "222") is False, "mevcut conid ezildi"
    assert d.query("SELECT conid FROM identities WHERE instrument_id=?",
                   (iid,))[0]["conid"] == "111"
    assert d.save_conid(iid, "222", zorla=True) is True
    d.close()


def test_conidsiz_hedefler_BIST_ve_KRIPTOYU_DISLAR():
    """
    IBKR'de Borsa Istanbul YOK. BIST sembollerini sormak her kosuda
    "bulunamadi" uretirdi — kalici sahte alarm gercek arizayi gomer.
    """
    d = _gecici_db()
    ids = {}
    for sem, venue in (("AMZN", "BUX"), ("THYAO", "BIST"), ("BTC", "BINANCE"),
                       ("CASH", "BUX"), ("XU100", "INDEX")):
        ids[sem] = d.upsert_instrument(sem, venue, f"{sem} A.S.")
        d._conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)",
                        (ids[sem],))
    d._conn.commit()
    bulunan = {r["symbol"] for r in d.conidsiz_hedefler()}
    assert bulunan == {"AMZN"}, f"beklenmeyen hedefler: {bulunan}"
    d.close()


# ----------------------------------------------------------------------
# Piyasa verisi — CANLI OLCUMDEN alinmis yanitlar
# ----------------------------------------------------------------------
from finagent.ibkr.piyasa import Kotasyon, Piyasa, _alan  # noqa: E402

# Gercek yanit (2026-08-26). Hacim IKI bicimde: "25.5M" ve 25500000.0
AMZN_KOTASYON = {
    "conid": 3691937, "6509": "DPB", "31": "260.99",
    "87": "25.5M", "87_raw": 25500000.0, "7741": "262.07",
    "_updated": 1787695955731,
}
# Piyasa kapaliyken Avrupa kagidi: `son` YOK ama alis/satis VAR.
ABN_KOTASYON = {
    "conid": 212289339, "6509": "ZB", "84": "40.70", "86": "41.11",
}


class SahtePiyasaOturumu:
    """
    Snapshot ucunu taklit eder: ILK cagri on-ucus (yalnizca conid),
    sonrakiler veri. Cagrilan yollari kaydeder.
    """

    def close(self):
        pass

    def __init__(self, veri: dict[str, dict], on_ucus: int = 1):
        self.veri = veri
        self._kalan_on_ucus = on_ucus
        self.cagrilar: list[tuple[str, str]] = []

    def request(self, yontem, url, **kw):
        self.cagrilar.append((yontem, url))
        if "unsubscribeall" in url:
            return SahteYanit(200, {"unsubscribed": True})
        if "unsubscribe" in url:
            return SahteYanit(200, {"success": True})
        if "iserver/accounts" in url:
            return SahteYanit(200, {"accounts": ["U1"]})
        if "snapshot" in url:
            if self._kalan_on_ucus > 0:
                self._kalan_on_ucus -= 1
                return SahteYanit(200, [{"conid": int(c), "conidEx": c}
                                        for c in self.veri])
            return SahteYanit(200, list(self.veri.values()))
        return SahteYanit(200, {})


def _piyasa(veri, on_ucus=1, azami=80):
    sahte = SahtePiyasaOturumu(veri, on_ucus)
    p = Piyasa(_istemci(sahte), azami_hat=azami)
    return p, sahte


def test_ON_UCUS_bos_yaniti_FIYAT_SANILMAZ():
    """
    Ilk istek yalnizca conid donuyor. Onu veri saymak bos kotasyon
    yazmak olurdu; kod ikinci isteği yapmali.
    """
    p, sahte = _piyasa({"3691937": AMZN_KOTASYON})
    k = p.kotasyon(["3691937"])
    assert k["3691937"].son == 260.99
    snapshotlar = [u for _, u in sahte.cagrilar if "snapshot" in u]
    assert len(snapshotlar) >= 2, "on-ucus sonrasi ikinci istek yapilmadi"


def test_HACIM_raw_alanindan_okunur():
    """
    "25.5M" cozulemez; tolere eden bir cozumleyici 25.5 okuyup hacmi
    BIR MILYON KAT kucuk gosterirdi. `_raw` sonekinden belgede hic
    bahsedilmiyor — olcumle bulundu.
    """
    assert _alan(AMZN_KOTASYON, "87") == 25500000.0
    # `_raw` yoksa duz alana dusulur.
    assert _alan({"31": "91.66"}, "31") == 91.66
    # Cozulemeyen deger None — 0.0 DEGIL.
    assert _alan({"87": "11.9M"}, "87") is None


def test_HATLAR_HER_ZAMAN_birakilir():
    """
    IBKR hesap basina 100 escamanli hat veriyor ve her on-ucus bir hat
    tuketiyor. Birakilmazsa birikir, 100'e dayanir, yeni semboller
    SESSIZCE bos doner — veri VARKEN "yok" denir.

    Bu yuzden birakma `finally`de: sizinti YAPISAL olarak imkansiz,
    "unutmamaya" dayanmiyor.
    """
    veri = {"3691937": AMZN_KOTASYON}
    p, sahte = _piyasa(veri)
    with p:
        p.kotasyon(["3691937"])
        assert p.acik_hat == 1
    assert p.acik_hat == 0
    assert any("unsubscribeall" in u for _, u in sahte.cagrilar)


def test_ISTISNA_atsa_bile_hatlar_birakilir():
    p, sahte = _piyasa({"3691937": AMZN_KOTASYON})
    try:
        with p:
            p.kotasyon(["3691937"])
            raise RuntimeError("is patladi")
    except RuntimeError:
        pass
    assert p.acik_hat == 0, "istisna yolunda hat sizdi"
    assert any("unsubscribeall" in u for _, u in sahte.cagrilar)


def test_HAT_TAVANI_asilmaz():
    """
    Tavani asmaktansa EKSIK donmek dogru: 100'u asan istekler sessizce
    bos donuyor ve bu "veri yok" gibi gorunuyor.
    """
    veri = {str(i): {"conid": i, "31": "1.0", "6509": "RPB"}
            for i in range(1, 6)}
    p, _ = _piyasa(veri, azami=3)
    with p:
        k = p.kotasyon(list(veri))
        assert p.acik_hat <= 3
    # Sorulan her conid yanitta VAR — sessizce dusurulmuyor.
    assert set(k) == set(veri)


def test_iserver_accounts_snapshottan_ONCE():
    """IBKR sarti; cagiranin hatirlamasi gerekmiyor."""
    p, sahte = _piyasa({"3691937": AMZN_KOTASYON})
    with p:
        p.kotasyon(["3691937"])
    yollar = [u for _, u in sahte.cagrilar]
    assert yollar.index(next(u for u in yollar if "iserver/accounts" in u)) \
        < yollar.index(next(u for u in yollar if "snapshot" in u))


def test_VERI_KIPI_gizlenmez():
    """
    Gecikmeli fiyati gercek zamanliymis gibi sunmak, yanlis fiyattan
    daha kotu: yanlis oldugu BILINMEZ.
    """
    assert Kotasyon("1", erisim="RPB").kip == "gercek_zamanli"
    assert Kotasyon("1", erisim="RPB").gercek_zamanli is True
    assert Kotasyon("1", erisim="DPB").kip == "gecikmeli"
    assert Kotasyon("1", erisim="DPB").gercek_zamanli is False
    assert Kotasyon("1", erisim="ZB").kip == "donmus"
    assert Kotasyon("1", erisim="NPB").kip == "abone_degil"
    assert Kotasyon("1", erisim="O").kip == "anlasma_imzalanmamis"
    assert Kotasyon("1", erisim="").kip == "bilinmiyor"


def test_SON_YOKSA_da_alis_satis_varsa_fiyat_VARDIR():
    """
    OLCULDU: piyasa kapaliyken Avrupa kagitlari `son` vermiyor ama
    alis/satis veriyor. Yalnizca `son`a bakmak, elimizde fiyat dururken
    "veri yok" beyani olurdu.
    """
    q = Piyasa._kotasyon("212289339", ABN_KOTASYON)
    assert q.son is None
    assert q.alis == 40.70 and q.satis == 41.11
    assert q.kullanilabilir is True
    assert q.orta == pytest_yakin(40.905)


def pytest_yakin(v):
    class _Y:
        def __eq__(self, other):
            return abs(other - v) < 1e-9
    return _Y()


def test_orta_fiyat_UYDURULMAZ():
    assert Kotasyon("1", son=10.0).orta is None
    assert Kotasyon("1", alis=10.0).orta is None
    assert Kotasyon("1", alis=10.0, satis=11.0).orta == 10.5


def test_veri_GELMEYEN_conid_sessizce_dusurulmez():
    """
    "sorduk ama gelmedi" ile "hic sormadik" ayri seyler. Cagiran bunu
    ayirt edebilmeli.
    """
    p, _ = _piyasa({"3691937": AMZN_KOTASYON})
    with p:
        k = p.kotasyon(["3691937", "999999"])
    assert set(k) == {"3691937", "999999"}
    assert k["999999"].kullanilabilir is False


# ----------------------------------------------------------------------
# Emir — GERCEK PARA. Her test bir bedelin karsiligi.
# ----------------------------------------------------------------------
from finagent.ibkr import emir as E  # noqa: E402

ISTEK = E.EmirIstegi(hesap="U1", conid="265598", yon="BUY", tur="LMT",
                     adet=1, fiyat=165.0)


def _fis(istek=ISTEK, kim="ali"):
    return E.OnayFisi(parmak_izi=istek.parmak_izi(), kim=kim)


def test_ONAY_FISI_OLMADAN_gonderilemez():
    """
    YAPISAL KORUMA. `gonder()` bool almiyor — alsaydi
    `gonder(..., onaylandi=True)` yazan tek satir korumayi delerdi.
    """
    import inspect
    imza = inspect.signature(E.gonder).parameters
    assert "fis" in imza
    assert imza["fis"].default is inspect.Parameter.empty, \
        "onay fisinin varsayilani var — zorunlu olmali"
    # Fis tipini de kontrol et: bool gecirilemez.
    with firlatir(AttributeError):
        E.gonder(_istemci(), ISTEK, True)          # type: ignore[arg-type]


def test_ONAYDAN_SONRA_DEGISEN_emir_REDDEDILIR():
    """
    EN KRITIK TEST. Onay 1 adet icin verildi; kod 100 adet gondermeye
    kalkarsa fis TUTMAZ. "Onaylandi" bayragi tasiyip icerigi degistirmek
    imkansiz olmali.
    """
    fis = _fis(ISTEK)                              # 1 adet icin onay
    degistirilmis = E.EmirIstegi(hesap="U1", conid="265598", yon="BUY",
                                 tur="LMT", adet=100, fiyat=165.0)
    with firlatir(E.EmirReddedildi):
        E.gonder(_istemci(), degistirilmis, fis)


def test_her_alan_parmak_izini_DEGISTIRIR():
    """Adet, fiyat, yon, tur, sure, conid, hesap — hepsi ize girmeli."""
    temel = ISTEK.parmak_izi()
    from dataclasses import replace
    for alan, deger in (("adet", 2), ("fiyat", 166.0), ("yon", "SELL"),
                        ("tur", "MKT"), ("sure", "GTC"),
                        ("conid", "8314"), ("hesap", "U2")):
        d = {alan: deger}
        if alan == "tur" and deger == "MKT":
            d["fiyat"] = None
        assert replace(ISTEK, **d).parmak_izi() != temel, \
            f"{alan} parmak izini degistirmiyor"


def test_ESKIMIS_fis_REDDEDILIR():
    """
    `onay.py`nin buton omru 24 saat — gunluk okuma icin dogru, canli
    emir icin felaket. Fiyat gun icinde yuzde onlarca oynar.
    """
    eski = E.OnayFisi(parmak_izi=ISTEK.parmak_izi(),
                      verildi=time.monotonic() - 10_000)
    with firlatir(E.EmirReddedildi):
        E.gonder(_istemci(), ISTEK, eski)


def test_gecersiz_emirler_KAPIDA_durdurulur():
    """Gonderilmeden reddedilmeli; sebep NET olmali."""
    kotu = [
        dict(hesap="", conid="1", yon="BUY", tur="MKT", adet=1),
        dict(hesap="U1", conid="", yon="BUY", tur="MKT", adet=1),
        dict(hesap="U1", conid="1", yon="AL", tur="MKT", adet=1),
        dict(hesap="U1", conid="1", yon="BUY", tur="STP", adet=1),
        dict(hesap="U1", conid="1", yon="BUY", tur="MKT", adet=0),
        dict(hesap="U1", conid="1", yon="BUY", tur="MKT", adet=-5),
        dict(hesap="U1", conid="1", yon="BUY", tur="LMT", adet=1),        # fiyatsiz LMT
        dict(hesap="U1", conid="1", yon="BUY", tur="MKT", adet=1, fiyat=5),  # fiyatli MKT
        dict(hesap="U1", conid="1", yon="BUY", tur="MKT", adet=1, sure="XXX"),
    ]
    for kw in kotu:
        with firlatir(E.EmirReddedildi):
            E.EmirIstegi(**kw).dogrula()


def test_ONAY_MESAJI_gonderildi_SANILMAZ():
    """
    IBKR emir POST'una `order_id` yerine teyit mesaji donebilir ve emir
    o teyit edilene kadar CALISMAZ. Ikisini ayirt etmemek, askida bir
    emri "gonderildi" sanmak olurdu.
    """
    sahte = SahteOturum({"orders": SahteYanit(200, [{
        "id": "07a13a5a-4a48", "isSuppressed": False,
        "message": ['BUY 100 AAPL @ 165.0 price exceeds the Percentage '
                    'constraint of 3%. Are you sure?'],
        "messageIds": ["o163"]}])})
    s = E.gonder(_istemci(sahte), ISTEK, _fis())
    assert isinstance(s, E.OnayMesaji)
    assert s.id == "07a13a5a-4a48"
    assert s.mesaj_kodlari == ["o163"]
    assert "3%" in s.metin()


def test_kabul_yaniti_EMIR_KIMLIGI_tasir():
    sahte = SahteOturum({"orders": SahteYanit(200, [{
        "order_id": "987654", "order_status": "Submitted"}])})
    s = E.gonder(_istemci(sahte), ISTEK, _fis())
    assert isinstance(s, E.EmirYaniti)
    assert s.emir_id == "987654" and s.durum == "Submitted"


def test_govde_ORDERS_ile_sarilir():
    """
    GERILEME TESTI — VE BU TEST BIR ZAMANLAR YANLISI KILITLIYORDU.

    Ilk hali "govde duz DIZI olmali" diyordu, cunku IBKR'nin ANLATI
    sayfasi ("New Order Example") oyle gosteriyor. Sahada emir HTTP 400
    aldi ve REFERANS sayfasi ({"orders": [...]}) dogru cikti. Iki
    sayfa CELISIYOR.

    Ders: yanlis varsayimi kodlayan test sahte guven verir. Test yesildi,
    kod yanlisti, ve hata testten degil GERCEK PARADAN dondu.
    """
    kaydedilen = {}

    class Kaydeden(SahteOturum):
        def request(self, yontem, url, **kw):
            kaydedilen["govde"] = kw.get("json")
            return SahteYanit(200, [{"order_id": "1", "order_status": "S"}])

    E.gonder(_istemci(Kaydeden()), ISTEK, _fis())
    g = kaydedilen["govde"]
    assert isinstance(g, dict) and "orders" in g, \
        "govde {'orders': [...]} degil — duz dizi HTTP 400 aliyor"
    o = g["orders"][0]
    assert o["conid"] == 265598 and isinstance(o["conid"], int)
    assert o["side"] == "BUY" and o["orderType"] == "LMT"
    assert o["tif"] == "DAY" and o["price"] == 165.0


def test_200_ILE_GELEN_HATA_basari_SANILMAZ():
    """
    IBKR gecersiz emri HTTP 200 + {"error": "..."} ile donduruyor —
    HTTP katmaninda BASARI gibi gorunuyor. Olculdu: gecersiz conid ->
    200 {"error": "no sec defs returned forSecDef ..."}.
    """
    sahte = SahteOturum({"orders": SahteYanit(200, {
        "error": "no sec defs returned forSecDef reqId=resolve"})})
    with firlatir(E.EmirYanitHatasi):
        E.gonder(_istemci(sahte), ISTEK, _fis())


def test_HTTP_HATASI_IBKR_SEBEBINI_tasir():
    """
    Ilk surum yalnizca "-> HTTP 400" diyordu ve sahada tam bir kore
    donusturdu: emir reddedildi, sebep hicbir yerde yoktu. Oysa IBKR
    govdede yaziyordu: "Bad Request: Missing order parameters".
    """
    sahte = SahteOturum({"": SahteYanit(400, {"error": "Missing order parameters"})})
    try:
        _istemci(sahte).get("/portfolio/accounts")
        raise AssertionError("hata bekleniyordu")
    except IbkrHatasi as e:
        assert "Missing order parameters" in str(e), \
            f"IBKR'nin sebebi mesajda yok: {e}"


def test_MKT_emrinde_fiyat_alani_GITMEZ():
    istek = E.EmirIstegi(hesap="U1", conid="1", yon="SELL", tur="MKT", adet=3)
    assert "price" not in istek.govde()


def test_ZAMAN_ASIMI_yeniden_denemeye_DAVET_ETMEZ():
    """
    Zaman asimina ugrayan emir POST'u sunucuya ULASMIS OLABILIR.
    Siradan bir baglanti hatasi olarak gorunseydi cagiran yeniden
    denerdi — gercek parayla CIFT EMIR.
    """
    sahte = SahteOturum(firlat=httpx.TimeoutException("timeout"))
    with firlatir(E.DurumBilinmiyorHatasi):
        E.gonder(_istemci(sahte), ISTEK, _fis())


def test_MUTABAKAT_ayni_conid_ve_yonu_bulur():
    """`DurumBilinmiyorHatasi` sonrasi tek dogru hamle."""
    sahte = SahteOturum({"account/orders": SahteYanit(200, {"orders": [
        {"conid": 265598, "side": "BUY", "orderId": 1, "status": "Submitted"},
        {"conid": 265598, "side": "SELL", "orderId": 2, "status": "Submitted"},
        {"conid": 8314, "side": "BUY", "orderId": 3, "status": "Submitted"},
    ]})})
    bulunan = E.mutabakat(_istemci(sahte), ISTEK)
    assert [b["orderId"] for b in bulunan] == [1]


def test_mutabakat_BOS_donerse_de_kod_kendi_basina_GONDERMEZ():
    """
    Bos liste "emir gitmedi" ANLAMINA GELMEZ — emir henuz gorunmuyor da
    olabilir. Karar insana ait; `mutabakat` yalnizca bilgi doner,
    yeniden gonderme YAPMAZ.
    """
    import inspect
    kaynak = inspect.getsource(E.mutabakat)
    assert "gonder(" not in kaynak, "mutabakat kendi basina emir gonderiyor"


def test_ONIZLEME_govdesi_UCUNCU_SEKIL():
    """
    `/orders` duz DIZI isterken `/whatif` `{"orders": [...]}` istiyor.
    Ayni aile, UCUNCU bir sekil (degistirme ucu ise NESNE). Karistirmak
    sessiz 400 uretir.
    """
    kaydedilen = {}

    class Kaydeden(SahteOturum):
        def request(self, yontem, url, **kw):
            kaydedilen["url"] = url
            kaydedilen["govde"] = kw.get("json")
            return SahteYanit(200, {"amount": {
                "amount": "4.25 USD (0.05 Shares)", "commission": "0.04 USD",
                "total": "4.29 USD"}, "error": None, "warns": []})

    on = E.onizle(_istemci(Kaydeden()), ISTEK)
    assert "whatif" in kaydedilen["url"]
    g = kaydedilen["govde"]
    assert isinstance(g, dict) and isinstance(g["orders"], list), \
        "whatif govdesi {'orders': [...]} olmali"
    assert on.komisyon == "0.04 USD" and on.hata is None


def test_ONIZLEME_hatasi_ENGELE_donusur():
    """
    IBKR onizlemede reddediyorsa emir zaten gitmeyecek — buton
    cikarmanin anlami yok. Olculdu: 1 lot KO icin
    "Available converted to base: 6.00 EUR ... needed 75.25 EUR".
    """
    sahte = SahteOturum({"whatif": SahteYanit(200, {
        "amount": {"amount": "85 USD (1 Shares)", "commission": "—"},
        "error": "Available converted to base: 6.00 EUR Cash needed: 75.25 EUR"})})
    on = E.onizle(_istemci(sahte), ISTEK)
    assert on.hata and "6.00 EUR" in on.hata


def test_ONAY_MESAJLARI_BASTIRILMIYOR():
    """
    `/iserver/questions/suppress` bedava bir fat-finger korumasini
    kapatir. Kod hicbir yerde CAGIRMAMALI.

    Duz metin aramasi yetmiyordu: modul docstring'i bu ucu ADIYLA
    aniyor (neden KULLANILMADIGINI anlatmak icin) ve test kendi
    aciklamamiza takiliyordu. Aranan sey metin degil CAGRI — o yuzden
    AST'ye bakiliyor.
    """
    import ast
    import inspect
    agac = ast.parse(inspect.getsource(E))
    metinler = [d.value for d in ast.walk(agac)
                if isinstance(d, ast.Constant) and isinstance(d.value, str)]
    # Docstring'ler ayri: yalnizca IFADE olarak duran metinleri dis birak.
    # `clean=True` (varsayilan) metni kirpip dedent ediyor ve ham
    # sabitle ARTIK ESLESMIYOR — ilk deneme tam bu yuzden kirmizi kaldi.
    docstringler = {ast.get_docstring(n, clean=False) for n in ast.walk(agac)
                    if isinstance(n, (ast.Module, ast.FunctionDef,
                                      ast.AsyncFunctionDef, ast.ClassDef))}
    kod_metinleri = [m for m in metinler if m not in docstringler]
    assert not any("questions/suppress" in m for m in kod_metinleri), \
        "kod fat-finger korumalarini bastiriyor"


# ----------------------------------------------------------------------
# Emir oncesi dogrulama
# ----------------------------------------------------------------------
from finagent.ibkr import onkontrol as OK  # noqa: E402


class SahteOnkontrolOturumu:
    """
    Onkontrol'un dokundugu TUM uclari taklit eder. Varsayilan senaryo
    saglikli; testler tek tek bozuyor.
    """

    def close(self):
        pass

    def __init__(self, **degis):
        self.d = {
            "auth": {"authenticated": True, "connected": True, "competing": False},
            "hesaplar": [{"accountId": "U1", "type": "INDIVIDUAL", "currency": "USD"}],
            "kotasyon": {"conid": 265598, "31": "165.00", "6509": "RPB"},
            "acik_emirler": {"orders": []},
            "ozet": {"buyingpower": {"amount": 100000.0, "currency": "USD"},
                     "netliquidation": {"amount": 100000.0, "currency": "USD"}},
            "pozisyonlar": [],
        }
        self.d.update(degis)
        self._on_ucus = True

    def request(self, yontem, url, **kw):
        if "auth/status" in url:
            return SahteYanit(200, self.d["auth"])
        if "portfolio/accounts" in url:
            return SahteYanit(200, self.d["hesaplar"])
        if "unsubscribe" in url:
            return SahteYanit(200, {"unsubscribed": True})
        if "iserver/accounts" in url:
            return SahteYanit(200, {"accounts": ["U1"]})
        if "snapshot" in url:
            if self._on_ucus:
                self._on_ucus = False
                return SahteYanit(200, [{"conid": 265598}])
            return SahteYanit(200, [self.d["kotasyon"]])
        if "account/orders" in url:
            return SahteYanit(200, self.d["acik_emirler"])
        if "summary" in url:
            return SahteYanit(200, self.d["ozet"])
        if "positions" in url:
            return SahteYanit(200, self.d["pozisyonlar"])
        return SahteYanit(200, {})


def _ok(istek=None, **degis):
    istek = istek or E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 165.0)
    return OK.dogrula(_istemci(SahteOnkontrolOturumu(**degis)), istek)


def test_onkontrol_saglikli_senaryoda_GECER():
    k = _ok()
    assert k.gonderilebilir is True
    assert k.durum == "gecti", f"engeller={k.engeller} uyarilar={k.uyarilar}"
    assert k.referans_fiyat == 165.0
    assert k.tahmini_tutar == 1650.0


def test_PIYASA_EMRI_gercek_zamanli_olmayan_veriyle_ENGELLENIR():
    """
    Limitte fiyati SEN soyluyorsun, ust sinir belli — uyari yeter.
    Piyasa emrinde fiyati PIYASA soyluyor; referansin 15 dakika eskiyse
    ne odeyecegini BILMIYORSUN. Bu uyarilabilir bir risk degil.
    """
    mkt = E.EmirIstegi("U1", "265598", "BUY", "MKT", 10)
    k = _ok(mkt, kotasyon={"conid": 265598, "31": "165.00", "6509": "DPB"})
    assert not k.gonderilebilir
    assert any("BILINMIYOR" in e for e in k.engeller)

    # Ayni veriyle LIMIT emri: uyari, engel DEGIL.
    k2 = _ok(kotasyon={"conid": 265598, "31": "165.00", "6509": "DPB"})
    assert k2.gonderilebilir is True
    assert any("gecikmeli" in u for u in k2.uyarilar)


def test_KAYMA_sinirini_asan_limit_ENGELLENIR():
    """Onaylanan fiyat artik piyasayla ilgisizse emir gonderilmemeli."""
    uzak = E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 200.0)   # +%21
    k = _ok(uzak)
    assert not k.gonderilebilir
    assert any("uzak" in e for e in k.engeller)


def test_CIFT_EMIR_engellenir():
    """
    Cift emir bu isin en pahali ve en SESSIZ kazasi: iki emir de gecerli
    gorunur, ikisi de dolar, pozisyon iki katina cikar.
    """
    k = _ok(acik_emirler={"orders": [
        {"conid": 265598, "side": "BUY", "orderId": 111, "status": "Submitted"}]})
    assert not k.gonderilebilir
    assert any("acik emir" in e for e in k.engeller)

    # DOLMUS emir engel DEGIL — o artik acik degil.
    k2 = _ok(acik_emirler={"orders": [
        {"conid": 265598, "side": "BUY", "orderId": 111, "status": "Filled"}]})
    assert k2.gonderilebilir is True


def test_ALIM_GUCU_yetmiyorsa_engellenir():
    k = _ok(ozet={"buyingpower": {"amount": 100.0, "currency": "USD"},
                  "netliquidation": {"amount": 100.0, "currency": "USD"}})
    assert not k.gonderilebilir
    assert any("alim gucu" in e for e in k.engeller)


def test_ELDE_OLANDAN_FAZLA_SATIS_engellenir():
    """Aciga satis kapsam disi; 3 lot varken 5 satmak acik pozisyondur."""
    sat = E.EmirIstegi("U1", "265598", "SELL", "LMT", 5, 165.0)
    k = _ok(sat, pozisyonlar=[{"conid": 265598, "description": "AAPL",
                               "position": 3, "avgPrice": 100.0}])
    assert not k.gonderilebilir
    assert any("aciga satis" in e for e in k.engeller)

    # Elindeki kadar satmak SORUN DEGIL.
    k2 = _ok(E.EmirIstegi("U1", "265598", "SELL", "LMT", 3, 165.0),
             pozisyonlar=[{"conid": 265598, "description": "AAPL",
                           "position": 3, "avgPrice": 100.0}])
    assert k2.gonderilebilir is True


def test_TANIMAYAN_HESABA_emir_engellenir():
    """Emir, sunucunun tanimadigi bir hesaba gidiyorsa durmali."""
    k = _ok(E.EmirIstegi("U_YOK", "265598", "BUY", "LMT", 1, 165.0))
    assert not k.gonderilebilir
    assert any("bu oturumda yok" in e for e in k.engeller)


def test_oturum_kapaliysa_engellenir():
    k = _ok(auth={"authenticated": False, "connected": False})
    assert not k.gonderilebilir


def test_canli_fiyat_yoksa_REFERANSSIZ_gonderilmez():
    k = _ok(kotasyon={"conid": 265598, "6509": "NPB"})   # abone degil, fiyat yok
    assert not k.gonderilebilir
    assert any("referanssiz" in e for e in k.engeller)


def test_YOGUNLASMA_uyari_engel_DEGIL():
    """
    Agirlik sinirini asmak bir TERCIH sorusu; karar insana ait.
    Her seyi engel yapmak katmani kullanilamaz kilar.
    """
    buyuk = E.EmirIstegi("U1", "265598", "BUY", "LMT", 100, 165.0)  # 16.500
    k = _ok(buyuk, ozet={"buyingpower": {"amount": 100000.0, "currency": "USD"},
                         "netliquidation": {"amount": 100000.0, "currency": "USD"}})
    assert k.gonderilebilir is True
    assert any("agirligi" in u for u in k.uyarilar)


def test_RAKIP_OTURUM_uyari_verir():
    k = _ok(auth={"authenticated": True, "connected": True, "competing": True})
    assert k.gonderilebilir is True
    assert any("baska bir yerde" in u for u in k.uyarilar)


def test_onkontrol_EMIR_GONDERMEZ():
    """Dogrulama katmani emir gondermemeli — yalnizca okur."""
    import ast
    import inspect
    agac = ast.parse(inspect.getsource(OK))
    cagrilar = {n.func.id for n in ast.walk(agac)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "gonder" not in cagrilar
    assert "teyit_et" not in cagrilar


# ----------------------------------------------------------------------
# Emir defteri
# ----------------------------------------------------------------------
def test_emir_defteri_SAHIPSIZ_yazmaz():
    d = _gecici_db()
    with firlatir(ValueError):
        d.emir_yaz(hesap="U1", conid="1", yon="BUY", tur="MKT", adet=1,
                   sure="DAY", parmak_izi="x", durum="hazirlandi")
    d.close()


def test_emir_defteri_ACIK_UCLU_satirlari_gosterir():
    """
    `bilinmiyor` = POST zaman asimina ugradi, emir ULASMIS OLABILIR.
    Bu satirlar sessizce durursa gonderilmis bir emri unutmus oluruz.
    """
    d = _gecici_db()
    ortak = dict(sahip="ali", hesap="U1", conid="1", yon="BUY", tur="MKT",
                 adet=1, sure="DAY", parmak_izi="x")
    a = d.emir_yaz(durum="kabul", **ortak)
    b = d.emir_yaz(durum="bilinmiyor", **ortak)
    c = d.emir_yaz(durum="teyit_bekliyor", **ortak)
    acik = {r["id"] for r in d.acik_uclu_emirler()}
    assert acik == {b, c}, f"beklenmeyen: {acik} (kapali olan {a})"
    d.close()


# ----------------------------------------------------------------------
# Telegram emir akisi
# ----------------------------------------------------------------------
from finagent.bot import emirakis as EA  # noqa: E402


def test_komut_cozumu():
    assert EA.komut_coz("NVDA AL 5 214.50") == {
        "sembol": "NVDA", "yon": "BUY", "adet": 5.0, "fiyat": 214.5,
        "sure": None, "tur": "LMT"}
    assert EA.komut_coz("ko sat 3")["tur"] == "MKT"
    for kotu in ("", "NVDA", "NVDA AL", "NVDA TUT 5", "NVDA AL x",
                 "NVDA AL 0", "NVDA AL -3", "NVDA AL 5 abc", "NVDA AL 5 0"):
        with firlatir(EA.EmirHatasi):
            EA.komut_coz(kotu)

    # SURE (TIF) — fiyatla birlikte de, fiyatsiz da yazilabiliyor.
    assert EA.komut_coz("VRT SAT 0.38 288 GTC")["sure"] == "GTC"
    assert EA.komut_coz("VRT SAT 0.38 288 gtc")["sure"] == "GTC"   # kucuk harf
    piyasa = EA.komut_coz("VRT SAT 1 GTC")
    assert piyasa["sure"] == "GTC" and piyasa["fiyat"] is None \
        and piyasa["tur"] == "MKT", piyasa

    # YAZIM HATASI SESSIZCE DAY'E DUSMEZ. Kullanici GTC yazdigini
    # sanirken gun emri gitmesi, emirde en kotu sessizlik turu.
    for kotu in ("VRT SAT 1 288 GTS", "VRT SAT 1 288 GTC fazla",
                 "VRT SAT 1 GTC 288"):
        with firlatir(EA.EmirHatasi):
            EA.komut_coz(kotu)


def test_onay_omru_ONAY_PY_DEN_COK_DAHA_KISA():
    """
    `onay.py` butonlari 24 saat yasiyor — gunluk okuma icin dogru, emir
    icin felaket: gece hazirlanan emir sabah basildiginda referans fiyat
    saatlerce eski olur.
    """
    from finagent.bot.onay import OMUR
    assert EA.ONAY_OMRU_SN < OMUR.total_seconds() / 100


def test_SURESI_DOLAN_onay_gondermez():
    db = _gecici_db()
    istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 5, 165.0)
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="265598", yon="BUY",
                      tur="LMT", adet=5, fiyat=165.0, sure="DAY",
                      parmak_izi=istek.parmak_izi(), durum="hazirlandi")
    veri = {"satir_id": sid, "sembol": "NVDA", "hesap": "U1",
            "conid": "265598", "yon": "BUY", "tur": "LMT", "adet": 5,
            "fiyat": 165.0, "sure": "DAY", "parmak_izi": istek.parmak_izi(),
            "hazirlik_ts": 0}                      # 1970 — cok eski
    m = EA.yurut(_ayar(), db, veri, "ali")
    assert "suresi doldu" in m.lower()
    assert db.emirler("ali")[0]["durum"] == "suresi_doldu"
    db.close()


def test_ONAY_VERISI_DEGISTIRILMISSE_gondermez():
    """
    Onay dosyasi DISKTE duruyor. Arada adet degistirilirse parmak izi
    tutmaz ve emir gonderilmez.
    """
    db = _gecici_db()
    istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 5, 165.0)
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="265598", yon="BUY",
                      tur="LMT", adet=5, fiyat=165.0, sure="DAY",
                      parmak_izi=istek.parmak_izi(), durum="hazirlandi")
    veri = {"satir_id": sid, "sembol": "NVDA", "hesap": "U1",
            "conid": "265598", "yon": "BUY", "tur": "LMT",
            "adet": 500,                           # <-- diskte degistirildi
            "fiyat": 165.0, "sure": "DAY", "parmak_izi": istek.parmak_izi(),
            "hazirlik_ts": time.time()}
    m = EA.yurut(_ayar(), db, veri, "ali")
    assert "uyusmuyor" in m
    assert db.emirler("ali")[0]["durum"] == "reddedildi"
    db.close()


class _Ayar:
    """`Settings.get` yeter — emirakis baska bir sey okumuyor."""

    def __init__(self, taban):
        self._t = taban

    def get(self, k, d=None):
        return self._t if k == "ibkr.taban_url" else d


def _ayar(taban="https://localhost:5001/v1/api"):
    return _Ayar(taban)


def _akis_db_ve_veri(db):
    istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 165.0)
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="265598", yon="BUY",
                      tur="LMT", adet=10, fiyat=165.0, sure="DAY",
                      parmak_izi=istek.parmak_izi(), durum="hazirlandi")
    return {"satir_id": sid, "sembol": "NVDA", "hesap": "U1",
            "conid": "265598", "yon": "BUY", "tur": "LMT", "adet": 10,
            "fiyat": 165.0, "sure": "DAY", "parmak_izi": istek.parmak_izi(),
            "hazirlik_ts": time.time()}


def _akisi_kos(db, veri, **degis):
    """`Istemci`yi taklitle degistirip akisi kosar."""
    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = SahteOnkontrolOturumu(**degis)

    IST.Istemci.__init__ = sahte_init
    try:
        return EA.yurut(_ayar(), db, veri, "ali")
    finally:
        IST.Istemci.__init__ = orij


def test_IBKR_TEYIT_ISTERSE_emir_GONDERILDI_SANILMAZ():
    """
    IBKR teyit isterse emir HENUZ CALISMIYOR. "Gonderildi" demek,
    askidaki bir emri calisiyor sanmak olurdu.
    """
    db = _gecici_db()
    veri = _akis_db_ve_veri(db)

    class Teyitli(SahteOnkontrolOturumu):
        def request(self, yontem, url, **kw):
            if yontem == "POST" and url.endswith("/orders"):
                return SahteYanit(200, [{
                    "id": "abc-123", "message": ["fiyat %3 sinirini asiyor"],
                    "messageIds": ["o163"]}])
            return super().request(yontem, url, **kw)

    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = Teyitli()

    IST.Istemci.__init__ = sahte_init
    try:
        m = EA.yurut(_ayar(), db, veri, "ali")
    finally:
        IST.Istemci.__init__ = orij

    # ARTIK (metin, teyit_onayi) DONUYOR — cikmaz birakmiyor.
    assert isinstance(m, tuple), "teyit onayi uretilmedi"
    metin, teyit = m
    assert "teyit istiyor" in metin
    assert "HENUZ CALISMIYOR" in metin
    assert teyit["mesaj_id"] == "abc-123"
    r = db.emirler("ali")[0]
    assert r["durum"] == "teyit_bekliyor"
    assert "sinirini asiyor" in (r["onay_mesaji"] or "")
    db.close()


def test_ZAMAN_ASIMI_deftere_BILINMIYOR_yazar():
    """
    En tehlikeli dal. Emir ULASMIS OLABILIR; kod kendi basina yeniden
    gondermemeli ve kullaniciya bunu ACIKCA soylemeli.
    """
    db = _gecici_db()
    veri = _akis_db_ve_veri(db)

    class ZamanAsimi(SahteOnkontrolOturumu):
        def request(self, yontem, url, **kw):
            if yontem == "POST" and url.endswith("/orders"):
                raise httpx.TimeoutException("timeout")
            return super().request(yontem, url, **kw)

    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = ZamanAsimi()

    IST.Istemci.__init__ = sahte_init
    try:
        m = EA.yurut(_ayar(), db, veri, "ali")
    finally:
        IST.Istemci.__init__ = orij

    assert "BILINMIYOR" in m
    assert "Yeniden gondermeden" in m or "YENIDEN GONDERME" in m
    assert db.emirler("ali")[0]["durum"] == "bilinmiyor"
    db.close()


def test_emir_komutu_YALNIZCA_EGIK_CIZGIYLE():
    """
    Bu depoda dogal dil VARSAYILAN, komut istisna. Emirde TERSI:
    cizgisiz bir cumlenin gercek para harcamasi kabul edilemez.
    Olculdu — "sil sunu" bir zamanlar son portfoy kaydini silmisti.
    """
    import ast
    import inspect
    from finagent.bot import listener
    kaynak = inspect.getsource(listener.FinBot._on_text)
    agac = ast.parse(kaynak.lstrip().replace("\n    ", "\n"))
    # `cmd` yalnizca metin "/" ile basladiginda doluyor; "emir"
    # karsilastirmasi cmd uzerinde olmali, ham metin uzerinde degil.
    cmd_karsilastirmalari = set()
    for d in ast.walk(agac):
        if (isinstance(d, ast.Compare) and isinstance(d.left, ast.Name)
                and d.left.id == "cmd"):
            for k in d.comparators:
                if isinstance(k, ast.Constant):
                    cmd_karsilastirmalari.add(k.value)
                elif isinstance(k, (ast.Tuple, ast.List, ast.Set)):
                    cmd_karsilastirmalari |= {
                        e.value for e in k.elts if isinstance(e, ast.Constant)}
    assert "emir" in cmd_karsilastirmalari


def test_MODEL_EMIR_GONDEREMEZ_yalnizca_onaya_sunar():
    """
    ALTIN KURAL — ve artik dogal dil de var, o yuzden test DAHA ONEMLI.

    Bu testin ONCEKI hali `tools.py` icinde "ibkr.emir" METNINI ariyordu
    ve okuma araclari eklenince kirmizi oldu — cunku `acik_emirler` de o
    modulde ve onu ice aktarmak MESRU. Kaba metin aramasi yanlis soruyu
    soruyordu; dogru soru "hangi ISIMLER ice aktarildi".

    Model IBKR araclarini cagirabiliyor ama para hareketi yapan uc arac
    YALNIZCA ONAY DOSYASI birakiyor. Gonderme yolu `emirakis.*_yurut`
    ve oraya SADECE `listener._onay_yurut` gidiyor — yani insanin
    butonu. `tools.py` o fonksiyonlari ICE AKTARMAMALI.
    """
    import ast
    kaynak = (KOK / "src" / "finagent" / "bot" / "tools.py").read_text()
    agac = ast.parse(kaynak)
    ice_aktarilan = set()
    for d in ast.walk(agac):
        if isinstance(d, ast.ImportFrom):
            ice_aktarilan |= {a.name for a in d.names}
    for yasak in ("yurut", "iptal_yurut", "degistir_yurut", "gonder",
                  "teyit_et", "degistir"):
        assert yasak not in ice_aktarilan, \
            f"tools.py gonderme yolunu ice aktariyor: {yasak}"
    # Yalnizca *_hazirla ice aktarilabilir.
    assert {"hazirla", "iptal_hazirla", "degistir_hazirla"} & ice_aktarilan


def test_para_hareketi_araclari_STAGE_EDER_GONDERMEZ():
    """
    Uc aracin da govdesinde `_stage` cagrisi olmali ve `gonder` cagrisi
    OLMAMALI. Aciklamalarinda da "GONDERMEZ" gecmeli — model kullaniciya
    "emir verdim" dememeli.
    """
    import inspect
    from finagent.bot.tools import ToolBox
    kaynak = inspect.getsource(ToolBox.araclar)
    for arac in ("ibkr_emir_hazirla", "ibkr_emir_iptal", "ibkr_emir_degistir"):
        i = kaynak.index(f'@tool("{arac}"')
        # Bir sonraki @tool'a ya da sonuna kadar olan blok
        j = kaynak.find("@tool(", i + 10)
        blok = kaynak[i:j if j > 0 else len(kaynak)]
        assert "_stage(" in blok, f"{arac} onay dosyasi birakmiyor"
        assert "GONDERMEZ" in blok or "SUNAR" in blok, \
            f"{arac} aciklamasi gonderme yapmadigini soylemiyor"


def test_okuma_araclari_ONAY_ISTEMEZ():
    """Fiyat/bakiye/acik emir okumak risksiz — onay sormak gurultu olurdu."""
    import inspect
    from finagent.bot.tools import ToolBox
    kaynak = inspect.getsource(ToolBox.araclar)
    for arac in ("ibkr_durum", "ibkr_fiyat", "ibkr_acik_emirler",
                 "ibkr_emir_gecmisi"):
        i = kaynak.index(f'@tool("{arac}"')
        j = kaynak.find("@tool(", i + 10)
        assert "_stage(" not in kaynak[i:j], f"{arac} gereksiz onay istiyor"


def test_PORTFOY_brokerage_oturumu_KAPALIYKEN_de_toplanir():
    """
    GERILEME TESTI — canli gozlemle bulundu (2026-08-26).

    IBKR oturumu IKI KATMANLI: dis "salt okuma" oturumu `/portfolio`yu
    acar, brokerage oturumu `/iserver`i. Ikincisi BIRINCISI AYAKTAYKEN
    dusebiliyor:

        /iserver/auth/status -> authenticated:false connected:false
        /portfolio/accounts  -> CALISIYOR

    Ilk surum brokerage sart kosuyordu ve bu durumda portfoyu ATLIYORDU.
    Okunabilir veri DURURKEN "atlandi" demek, bu deponun en kotu hata
    sinifi.
    """
    from finagent.collectors.ibkrportfoy import IbkrPortfoyCollector

    class BrokerageDusuk(SahteOturum):
        def request(self, yontem, url, **kw):
            if "auth/status" in url:
                return SahteYanit(200, {"authenticated": False,
                                        "connected": False})
            if "portfolio/accounts" in url:
                return SahteYanit(200, [{"accountId": "U1",
                                         "type": "INDIVIDUAL",
                                         "currency": "EUR"}])
            if "ledger" in url:
                return SahteYanit(200, {"EUR": {"cashbalance": 6.0},
                                        "BASE": {"cashbalance": 6.0}})
            if "positions" in url:
                return SahteYanit(200, [])
            return SahteYanit(200, {})

    class Ayar(_Ayar):
        def get(self, k, d=None):
            return {"ibkr.acik": True, "ibkr.sahip": "ali"}.get(k, super().get(k, d))

    db = _gecici_db()
    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = BrokerageDusuk()

    IST.Istemci.__init__ = sahte_init
    try:
        r = IbkrPortfoyCollector(Ayar(None), db).collect()
    finally:
        IST.Istemci.__init__ = orij

    assert r.status == "ok", f"atlandi: {r.status} / {r.error}"
    assert r.rows == 1, "nakit satiri yazilmadi"
    assert r.data.get("brokerage_oturumu") is False
    db.close()


def test_FIYAT_PARA_BIRIMI_TASIR():
    """
    Kotasyon ucu para birimi DONDURMUYOR; ayri bir sozlesme cagrisiyla
    aliniyor. Para birimsiz sayi bu depoda kabul edilemez — en pahali
    hata (17 pozisyonun 14'unde ~%15,7 sapma) tam olarak USD serinin
    EUR portfoy degerleriyle yan yana konmasiydi.
    """
    class Sozlesmeli(SahtePiyasaOturumu):
        def request(self, yontem, url, **kw):
            if "contract/" in url and "/info" in url:
                return SahteYanit(200, {"currency": "USD",
                                        "listing_exchange": "NASDAQ"})
            return super().request(yontem, url, **kw)

    sahte = Sozlesmeli({"3691937": AMZN_KOTASYON})
    p = Piyasa(_istemci(sahte))
    with p:
        q = p.kotasyon(["3691937"])["3691937"]
    assert q.para_birimi == "USD", "fiyat para birimsiz dondu"
    assert q.borsa == "NASDAQ"


def test_sozlesme_bilgisi_ONBELLEKLENIR():
    """Gun icinde degismez; her kotasyonda yeniden sormak israf."""
    sayac = {"n": 0}

    class Sayan(SahtePiyasaOturumu):
        def request(self, yontem, url, **kw):
            if "contract/" in url and "/info" in url:
                sayac["n"] += 1
                return SahteYanit(200, {"currency": "USD"})
            return super().request(yontem, url, **kw)

    p = Piyasa(_istemci(Sayan({"3691937": AMZN_KOTASYON})))
    with p:
        p.kotasyon(["3691937"])
        p._sozlesme_bilgisi("3691937")
        p._sozlesme_bilgisi("3691937")
    assert sayac["n"] == 1, f"sozlesme {sayac['n']} kez soruldu"


def test_ONAY_EKRANI_ARACI_KURUMU_SOYLER_ve_CAKISMAYI_UYARIR():
    """
    Ali'nin BUX hesabinda 21 pozisyonun 16'si IBKR conid'i tasiyor ve o
    conid'ler ABD listesine (USD) cozuldu — BUX pozisyonlari EUR.
    "NVDA'dan al" dendiginde emir IBKR'ye, dolarla gider; kullanici BUX
    pozisyonunu buyuttugunu sanabilir.
    """
    from finagent.bot.emirakis import _baska_hesapta_var_mi, _ozet_metni
    db = _gecici_db()
    iid = db.upsert_instrument("NVDA", "BUX", "NVIDIA Corporation")
    db.insert_positions("bux", "2026-08-26T00:00:00+00:00",
                        [{"symbol": "NVDA", "quantity": 3.7,
                          "currency": "EUR"}], "ali")
    assert _baska_hesapta_var_mi(db, "NVDA", "ali") == "bux"
    assert _baska_hesapta_var_mi(db, "YOKBOYLE", "ali") is None

    metin = _ozet_metni({"sembol": "NVDA"}, ISTEK, OK.Onkontrol(),
                        kagit_mi=False, baska_hesap="bux")
    assert "IBKR" in metin, "aracı kurum yazmiyor"
    assert "CANLI" in metin
    assert "bux" in metin and "ORAYA DEGIL" in metin
    db.close()


def test_IBKR_UYARISI_CIKMAZ_BIRAKMAZ_teyit_onayi_uretir():
    """
    SAHADA BULUNDU (2026-08-26, ilk gercek emir). Onceki surum
    "IBKR arayuzunden teyit et" diyip birakiyordu — yani uyariyi
    gosteriyor ama EVET DEME YOLU vermiyordu.

    Uyariyi bastirmamanin butun anlami karari INSANA birakmak. Insana
    yol vermezsek koruma degil ENGEL olur.
    """
    db = _gecici_db()
    veri = _akis_db_ve_veri(db)

    class Teyitli(SahteOnkontrolOturumu):
        def request(self, yontem, url, **kw):
            if yontem == "POST" and url.endswith("/orders"):
                return SahteYanit(200, [{
                    "id": "e76c4e1f", "messageIds": ["o163"],
                    "message": ['BUY 0.05 KO NYSE @ 91.00 price exceeds '
                                'the Percentage constraint of 3%.']}])
            if "account/orders" in url:
                return SahteYanit(200, {"orders": [
                    {"conid": 265598, "side": "BUY", "orderId": 296869242,
                     "status": "Inactive"}]})
            return super().request(yontem, url, **kw)

    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = Teyitli()

    IST.Istemci.__init__ = sahte_init
    try:
        sonuc = EA.yurut(_ayar(), db, veri, "ali")
    finally:
        IST.Istemci.__init__ = orij

    assert isinstance(sonuc, tuple), "teyit onayi uretilmedi (cikmaz)"
    metin, teyit = sonuc
    assert "HENUZ CALISMIYOR" in metin
    assert teyit["mesaj_id"] == "e76c4e1f"
    r = db.emirler("ali")[0]
    assert r["durum"] == "teyit_bekliyor"
    # ASKIDAKI EMRIN NUMARASI KAYBOLMAMALI: teyit mesaji order_id
    # tasimiyor ama emir IBKR'de Inactive olarak duruyor.
    assert r["emir_id"] == "296869242", "askidaki emir numarasi kayboldu"
    db.close()


def test_TEYIT_kabul_edilince_emir_KABUL_olur():
    db = _gecici_db()
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="265598", yon="BUY",
                      tur="LMT", adet=0.05, fiyat=91.0, sure="DAY",
                      parmak_izi="x", durum="teyit_bekliyor")
    veri = {"mesaj_id": "e76c4e1f", "satir_id": sid, "sembol": "KO",
            "hazirlik_ts": time.time()}

    class Kabul(SahteOturum):
        def request(self, yontem, url, **kw):
            if "reply/" in url:
                return SahteYanit(200, {"order_id": "296869242",
                                        "order_status": "Submitted"})
            return SahteYanit(200, {})

    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = Kabul()

    IST.Istemci.__init__ = sahte_init
    try:
        m = EA.teyit_yurut(_ayar(), db, veri, "ali")
    finally:
        IST.Istemci.__init__ = orij

    assert isinstance(m, str) and "Emir gonderildi" in m
    r = db.emirler("ali")[0]
    assert r["durum"] == "kabul" and r["emir_id"] == "296869242"
    db.close()


def test_TEYIT_ZINCIRLENEBILIR_ikinci_uyari_yine_onay_ister():
    """
    Teyit yanitinda BASKA bir uyari gelebilir. Sonsuz donguye girmez:
    her tur INSANIN butonuna bagli.
    """
    db = _gecici_db()
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="265598", yon="BUY",
                      tur="LMT", adet=0.05, fiyat=91.0, sure="DAY",
                      parmak_izi="x", durum="teyit_bekliyor")
    veri = {"mesaj_id": "ilk", "satir_id": sid, "sembol": "KO",
            "hazirlik_ts": time.time()}

    class Zincir(SahteOturum):
        def request(self, yontem, url, **kw):
            if "reply/" in url:
                return SahteYanit(200, [{"id": "ikinci",
                                         "message": ["baska bir uyari"],
                                         "messageIds": ["o164"]}])
            return SahteYanit(200, {})

    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = Zincir()

    IST.Istemci.__init__ = sahte_init
    try:
        sonuc = EA.teyit_yurut(_ayar(), db, veri, "ali")
    finally:
        IST.Istemci.__init__ = orij

    assert isinstance(sonuc, tuple)
    assert sonuc[1]["mesaj_id"] == "ikinci"
    db.close()


def test_PENDING_SUBMIT_emri_iptal_YERINE_ACIKLANIR():
    """
    SAHADA BULUNDU (2026-08-26). Teyit bekleyen emir IBKR'de
    `status: Inactive`, `order_ccp_status: Pending Submit` ve durum ucu
    404 doner — yani ortada EMIR KAYDI yok, kuyrukta bir bilet var.
    DELETE "Order is inactive" ile 400 veriyor.

    Kullaniciyi ham hatayla birakmak yerine ne oldugunu ve CIKIS YOLUNU
    soylemek gerekiyor.
    """
    sahte = SahteOturum({
        "portfolio/accounts": SahteYanit(200, [{"accountId": "U1",
                                                "type": "INDIVIDUAL"}]),
        "account/orders": SahteYanit(200, {"orders": [
            {"orderId": 296869242, "conid": 8894, "ticker": "KO",
             "side": "BUY", "totalSize": 0.05, "price": "91.00",
             "status": "Inactive", "order_ccp_status": "Pending Submit"}]}),
    })
    from finagent.ibkr import istemci as IST
    orij = IST.Istemci.__init__

    def sahte_init(self, taban=None, zaman_asimi=15.0):
        orij(self, taban, zaman_asimi)
        self._istemci = sahte

    IST.Istemci.__init__ = sahte_init
    db = _gecici_db()
    try:
        try:
            EA.iptal_hazirla(_ayar(), db, "296869242", "ali")
            raise AssertionError("aciklama bekleniyordu")
        except EA.EmirHatasi as e:
            assert "HENUZ GONDERILMEDI" in str(e)
            assert "teyit et" in str(e), "cikis yolu soylenmiyor"
    finally:
        IST.Istemci.__init__ = orij
        db.close()


def test_ASKIDAKI_TEYIT_DEFTERDEN_kurtarilir():
    """
    `messageId` yalnizca onay DOSYASINDA yasiyordu; dosya tuketilince
    emir ne teyit ne iptal edilebiliyordu. Artik deftere yaziliyor
    (sema 23) ve buradan yeniden onaya sunulabiliyor.
    """
    db = _gecici_db()
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="8894", yon="BUY",
                      tur="LMT", adet=0.05, fiyat=91.0, sure="DAY",
                      parmak_izi="x", durum="teyit_bekliyor",
                      mesaj_id="e76c4e1f", emir_id="296869242",
                      onay_mesaji="price exceeds the Percentage constraint")
    metin, veri = EA.bekleyen_teyit_hazirla(_ayar(), db, "ali")
    assert veri["mesaj_id"] == "e76c4e1f"
    assert veri["satir_id"] == sid
    assert "296869242" in metin and "Percentage" in metin

    # Mesaj kimligi OLMAYAN satir kurtarilamaz — sessizce uydurulmaz.
    db.emir_guncelle(sid, mesaj_id=None)
    with firlatir(EA.EmirHatasi):
        EA.bekleyen_teyit_hazirla(_ayar(), db, "ali")
    db.close()


# ----------------------------------------------------------------------
# MUTABAKAT — defter ile IBKR'nin ayrismasi
# ----------------------------------------------------------------------
from finagent.ibkr import mutabakat as MB  # noqa: E402


def _satir(**k):
    t = {"id": 1, "emir_id": "296869242", "durum": "teyit_bekliyor",
         "conid": "8894", "yon": "BUY", "symbol": "KO", "mesaj_id": None,
         "olusma_ts": "2026-08-26T00:11:37"}
    t.update(k)
    return t


def test_DOLMUS_emir_ACIK_LISTEDE_YOK_diye_OLU_SAYILMAZ():
    """
    BU TESTIN VARLIK SEBEBI TEK CUMLE: dolmus bir emir de acik
    emirlerde gorunmez.

    "Acik listede yoksa kapat/iptal et" kurali burada bir ALIMI
    'dustu' diye kapatirdi; pozisyon portfoye hic girmez, elimizdeki
    hisseyi elimizde degil sanardik. Yoklugu KANIT saymak, bu deponun
    en kotu hata sinifinin para tarafindaki hali.
    """
    k = MB.karar(_satir(), acik=None, dstat=None, dolum_var=True,
                 simdi_ts=0)
    assert k.yeni_durum == "gerceklesti", k
    assert k.eylem is None, "dolmus emir icin eylem onerildi"


def test_dolum_BAKILAMADIYSA_defter_KAPATILMAZ():
    """
    `dolum_var=None` = "islem gecmisini okuyamadim". Bu, "dolum yok"
    ile AYNI SEY DEGIL ve ayni sayilirsa sessizce yanlis kapatma olur.
    """
    k = MB.karar(_satir(), acik=None, dstat=None, dolum_var=None,
                 simdi_ts=0)
    assert k.yeni_durum is None, k
    assert "dogrulanamadi" in k.kod


def test_IBKR_izini_kaybettiyse_ve_dolum_YOKSA_defter_kapanir():
    """Ali'nin vakasi: 296869242 ne acik listede ne islem gecmisinde."""
    k = MB.karar(_satir(), acik=None, dstat=None, dolum_var=False,
                 simdi_ts=0)
    assert k.yeni_durum == "dustu"
    assert k.eylem is None, "olu emre iptal cagrisi onerildi"


def test_IPTAL_EDILMIS_emir_icin_IPTAL_CAGRISI_ONERILMEZ():
    """IBKR 'cancelled' demisse gonderilecek iptal yok — dun 400 aldik."""
    k = MB.karar(_satir(), acik=None, dstat={"order_status": "Cancelled"},
                 dolum_var=None, simdi_ts=0)
    assert k.yeni_durum == "dustu" and k.eylem is None


def test_ASKIDAKI_emir_TEYIT_onerir_IPTAL_DEGIL():
    """
    Olculdu (26 Agu): `Inactive` + `Pending Submit` emir DELETE'i
    "Order is inactive" ile reddediyor. Dogru sira once TEYIT.
    """
    k = MB.karar(_satir(mesaj_id="e76c4e1f"), acik=None,
                 dstat={"order_status": "Inactive",
                        "order_ccp_status": "Pending Submit"},
                 dolum_var=None, simdi_ts=0)
    assert k.eylem == "teyit", k
    assert k.yeni_durum is None, "askidaki emir kapatildi"


def test_CANLI_emir_iptal_onerir_ve_defteri_kabul_yapar():
    acik = {"orderId": "2141314594", "status": "PreSubmitted",
            "totalSize": 0.05, "filledQuantity": 0}
    k = MB.karar(_satir(emir_id="2141314594"), acik=acik,
                 dstat={"order_status": "PreSubmitted", "cum_fill": "0.0",
                        "total_size": "0.05"},
                 dolum_var=None, simdi_ts=0)
    assert k.kod == "S2_canli" and k.eylem == "iptal"
    assert k.yeni_durum == "kabul"


def test_CELISKI_durumunda_HICBIR_SEY_YAZILMAZ():
    """
    Durum ucu "canli" diyor, acik emirlerde yok. Iki kaynak celisiyorsa
    birini secip yazmak, celiskiyi COZMEK degil GIZLEMEK olur.
    """
    k = MB.karar(_satir(), acik=None,
                 dstat={"order_status": "Submitted"}, dolum_var=None,
                 simdi_ts=0)
    assert k.kod == "S7_celiski" and k.yeni_durum is None


def test_KISMI_dolum_ne_kapanir_ne_gerceklesti_sayilir():
    k = MB.karar(_satir(), acik={"orderId": "1", "status": "Submitted"},
                 dstat={"order_status": "Submitted", "cum_fill": "0.02",
                        "total_size": "0.05"}, dolum_var=None, simdi_ts=0)
    assert k.yeni_durum == "kismi" and k.eylem == "iptal"


def test_zaman_asiminda_ESLESEN_acik_emir_deftere_yazilir():
    """`bilinmiyor` satiri: conid+yon eslesirse emir numarasi geri gelir."""
    k = MB.karar(_satir(emir_id=None, durum="bilinmiyor"),
                 acik={"orderId": "999", "conid": "8894", "side": "BUY",
                       "status": "Submitted"},
                 dstat=None, dolum_var=None, simdi_ts=0)
    assert k.yeni_durum == "kabul" and k.alanlar["emir_id"] == "999"


def test_zaman_asiminda_ESLESME_YOKSA_uydurulmaz():
    k = MB.karar(_satir(emir_id=None, durum="bilinmiyor"), acik=None,
                 dstat=None, dolum_var=None, simdi_ts=0)
    assert k.yeni_durum is None
    assert "kapatmiyorum" in k.aciklama


def test_DEFTERDE_OLMAYAN_acik_emir_BILDIRILIR():
    """
    Ali IBKR arayuzunden emir girebilir. Bizim yazmadigimiz bir emir de
    gercek para; sessizce yok sayilmasi 'yanlis yok beyani' olurdu.
    """
    class _Ist:
        def get(self, yol, params=None):
            if "orders" in yol:
                return {"orders": [{"orderId": "555", "ticker": "AAPL",
                                    "status": "Submitted"}]}
            return {}
    kararlar = MB.kos(_Ist(), [], simdi_ts=0, hesap="U1")
    assert any(k.kod == "S14_defterde_yok" for k in kararlar), kararlar


def test_mutabakat_IBKR_YE_YAZMA_CAGRISI_YAPMAZ():
    """
    MIMARI KILIT. Mutabakat "temizlik" adina emir iptal ederse, onay
    butonu mimarisi delinmis olur. Modul DELETE ya da iptal/teyit
    fonksiyonlarini ICE AKTARMIYOR bile.
    """
    import ast
    import inspect
    kaynak = inspect.getsource(MB)
    agac = ast.parse(kaynak)
    yasak = {"iptal", "teyit_et", "gonder", "degistir"}
    for d in ast.walk(agac):
        if isinstance(d, ast.ImportFrom):
            for a in d.names:
                assert a.name not in yasak, f"mutabakat {a.name} ice aktariyor"
        if isinstance(d, ast.Attribute):
            assert d.attr not in ("delete", "post"), \
                f"mutabakat yazma cagrisi yapiyor: .{d.attr}"


def test_islem_gecmisi_EN_FAZLA_BIR_KEZ_cekilir():
    """
    `/iserver/trades` 5 sn/istek sinirli — satir basina cekmek uc satirda
    15 saniye ederdi.

    Acik emir listesi BILEREK dolu: bos liste artik "guvenilmez" sayiliyor
    ve o dalda islem gecmisine hic bakilmiyor (bkz.
    test_BOS_acik_emir_LISTESI_CANLI_emri_OLDURMEZ).
    """
    sayac = {"trades": 0}

    class _Ist:
        def get(self, yol, params=None):
            if "trades" in yol:
                sayac["trades"] += 1
                return []
            if "orders" in yol:
                return {"orders": [{"orderId": 777, "conid": 1, "side": "BUY",
                                    "status": "Submitted"}]}
            raise IbkrHatasi("404")
    satirlar = [_satir(id=1, emir_id="a"), _satir(id=2, emir_id="b"),
                _satir(id=3, emir_id="c")]
    MB.kos(_Ist(), satirlar, simdi_ts=0, hesap="U1")
    assert sayac["trades"] == 1, sayac


def test_islemler_OKUNAMAZSA_None_doner_BOS_LISTE_DEGIL():
    class _Ist:
        def get(self, yol, params=None):
            raise IbkrHatasi("baglanti yok")
    assert MB.islemler(_Ist()) is None


# ----------------------------------------------------------------------
# Uyari EKLENIR, uzerine yazilmaz
# ----------------------------------------------------------------------
def test_IKINCI_uyari_BIRINCIYI_SILMEZ():
    """
    SAHADA OLDU (emir 2141314594): IBKR once yuzde kisitini, teyitten
    SONRA "Mandatory Cap Price"i sordu. Ilk surum `onay_mesaji=` ile
    atiyordu ve defterde yalnizca sonuncusu kaliyordu — o kolonun tek
    varlik sebebi "hangi uyariyi gorup yine de onayladim" iken.
    """
    db = _gecici_db()
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="8894", yon="BUY",
                      tur="LMT", adet=0.05, sure="DAY", parmak_izi="x",
                      durum="teyit_bekliyor")
    db.emir_uyari_ekle(sid, "price exceeds the Percentage constraint of 3%",
                       "e76c4e1f", ["o163"])
    db.emir_uyari_ekle(sid, "Confirm Mandatory Cap Price", "9555bebd", [])

    r = db.query("SELECT * FROM emirler WHERE id=?", (sid,))[0]
    assert "Percentage" in r["onay_mesaji"], "ilk uyari silindi"
    assert "Mandatory" in r["onay_mesaji"], "ikinci uyari yazilmadi"
    assert "[1]" in r["onay_mesaji"] and "[2]" in r["onay_mesaji"]
    assert "o163" in r["onay_mesaji"], "mesaj kodu kaydedilmedi"
    # Cevap verilecek olan SONUNCUSU — /iserver/reply son soruyu yanitlar.
    assert r["mesaj_id"] == "9555bebd"
    db.close()


def test_kapanmamis_emirler_KABUL_edilmisi_de_getirir():
    """
    `acik_uclu_emirler` yalnizca teyit/bilinmiyor doner. Kabul edilmis
    bir emir de dolabilir ya da dusebilir; takip edilmezse defter
    IBKR'den sessizce ayrisir — sahada oyle oldu.

    2026-08-31 GUNCELLEMESI: "gerceklesti" TEK BASINA yeterli degil.
    Dolum verisi YAZILMIS satir disarida kalir; YAZILMAMIS olan geri
    gelir, cunku o satirda kagit el degistirmis ama KANIT alinmamistir
    (VRT 1473988529 tam boyle kilitlendi). Bu yuzden test artik ikisini
    AYIRIYOR — eskiden ikisi de ayni kovadaydi ve fark olculmuyordu.
    """
    db = _gecici_db()
    ortak = dict(sahip="ali", hesap="U1", conid="8894", yon="BUY",
                 tur="LMT", adet=0.05, sure="DAY", parmak_izi="x")
    db.emir_yaz(durum="kabul", **ortak)
    db.emir_yaz(durum="teyit_bekliyor", **ortak)
    # DOLUMU YAZILMIS — gercekten bitmis, gelmemeli
    tam = db.emir_yaz(durum="gerceklesti", **ortak)
    db.emir_guncelle(tam, dolum_fiyat=90.99, dolum_komisyon=0.05)
    # DOLUMU YAZILMAMIS — kanit eksik, GELMELI
    db.emir_yaz(durum="gerceklesti", **ortak)

    gelen = db.kapanmamis_emirler("ali")
    durumlar = sorted(r["durum"] for r in gelen)
    assert durumlar == ["gerceklesti", "kabul", "teyit_bekliyor"], durumlar
    kilitli = [r for r in gelen if r["durum"] == "gerceklesti"]
    assert len(kilitli) == 1 and kilitli[0]["dolum_fiyat"] is None, \
        "dolumu YAZILMIS satir da geri geliyor — her kosuda tekrar islenir"
    db.close()


# ----------------------------------------------------------------------
# Telegram HTML kacisi
# ----------------------------------------------------------------------
def test_IBKR_metnindeki_HTML_ETIKETI_KACIRILIR():
    """
    SAHADA ISIRDI: IBKR'nin uyarisi `<h4>Confirm Mandatory Cap Price</h4>`
    iceriyor ve Telegram mesaji reddetti (`Unsupported start tag "h4"`).
    Mesaj yalnizca sadelestirme yedegi sayesinde ulasti — yani sansla.
    """
    mesaj = E.OnayMesaji(id="9555bebd",
                         metinler=["<h4>Confirm Mandatory Cap Price</h4>"
                                   "IB may set a cap & floor."],
                         mesaj_kodlari=[])
    metin, _ = EA._teyit_istegi(mesaj, 1, {"sembol": "KO"}, "2141314594")
    assert "<h4>" not in metin, "ham IBKR etiketi mesaja sizdi"
    assert "&lt;h4&gt;" in metin
    assert "&amp;" in metin, "& kacirilmadi"
    # Bizim kendi bicimlendirmemiz KACIRILMAMALI — yoksa mesaj duz metne doner.
    assert "<b>" in metin and "<i>" in metin


def test_acik_emir_satirinda_IBKR_alanlari_kacirilir():
    e = {"ticker": "A&B <x>", "side": "BUY", "totalSize": 1,
         "orderType": "LMT", "status": "Sub<b>", "orderId": "1"}
    m = EA._emir_satiri(e)
    assert "<x>" not in m and "&amp;" in m



def test_BOS_acik_emir_LISTESI_CANLI_emri_OLDURMEZ():
    """
    SAHADA OLDU VE EN PAHALISIYDI (26 Agu). Mutabakatin ilk canli
    kosumu, IBKR arayuzunde `PreSubmitted` duran 2141314594 numarali
    emri "dustu" diye kapatti.

    Sebep: `/iserver/account/orders` o cagrida BOS liste dondu (saniyeler
    sonra ayni oturumda emri donduruyordu). Ben boslugu "acik emir yok"
    saydim.

    Bu tam olarak piyasa verisindeki ON-UCUS davranisinin ayni sinifi ve
    bu depoda ucuncu tekrari: BOS YANIT, YOKLUK KANITI DEGILDIR. Kural
    artik kodda: liste bos ya da okunamazsa hicbir satir yokluk
    gerekcesiyle kapatilmaz.
    """
    class _Ist:
        def __init__(self):
            self.cagri = 0

        def get(self, yol, params=None):
            if "orders" in yol:
                self.cagri += 1
                return {"orders": []}          # her zaman bos
            raise IbkrHatasi("404")

    satir = _satir(id=3, emir_id="2141314594", durum="kabul")
    kararlar = MB.kos(_Ist(), [satir], simdi_ts=0, hesap="U1")
    assert len(kararlar) == 1
    k = kararlar[0]
    assert k.yeni_durum is None, f"CANLI emir kapatildi: {k}"
    assert k.kod == "S15_liste_guvenilmez", k


def test_bos_liste_BIR_KEZ_yeniden_sorulur():
    """
    Bos liste gecici olabilir. Tek bir yeniden sorma, sahadaki hatanin
    en ucuz kapisiydi — ve ikinci cagri emri getiriyor.
    """
    class _Ist:
        def __init__(self):
            self.n = 0

        def get(self, yol, params=None):
            self.n += 1
            if self.n == 1:
                return {"orders": []}
            return {"orders": [{"orderId": 2141314594, "status": "PreSubmitted",
                                "conid": 8894, "side": "BUY"}]}
    ist = _Ist()
    e = E.acik_emirler(ist, "U1")
    assert ist.n == 2, "bos liste yeniden sorulmadi"
    assert len(e) == 1


def test_liste_DOLU_ama_emir_yoksa_defter_kapanabilir():
    """
    Ayrimin diger yuzu: liste GUVENILIR (dolu) ve bizim emrimiz orada
    yoksa, bu gercek bir kanit. Dun geceki 296869242 boyle kapandi.
    """
    class _Ist:
        def get(self, yol, params=None):
            if "orders" in yol:
                return {"orders": [{"orderId": 2141314594, "conid": 8894,
                                    "side": "BUY", "status": "PreSubmitted"}]}
            if "trades" in yol:
                return []
            raise IbkrHatasi("404")

    satir = _satir(id=2, emir_id="296869242", durum="teyit_bekliyor")
    (k,) = [x for x in MB.kos(_Ist(), [satir], simdi_ts=0, hesap="U1")
            if x.satir_id == 2]
    assert k.yeni_durum == "dustu", k



def test_RAKIP_OTURUMDA_alarm_TARAYICIYA_GIR_DEMEZ():
    """
    Alarm dogru caliyordu ama YANLIS KAPIYI gosteriyordu.

    Ali Client Portal'a girince brokerage oturumu duser ve eski mesaj
    "Tarayicidan yeniden gir: https://localhost:5001" diyordu. Bu tam
    ters tavsiye: Ali ZATEN tarayicida; yeniden giris iki istemciyi
    birbirini dusuren bir salincaga sokar. Dogru cumle "oradan cik" ya
    da "ikinci kullanici adi".
    """
    haberler = []
    o = Oturum(_istemci(SahteOturum()), bildir=lambda a, m, **k: haberler.append((a, m)))
    o._onceki_kullanilabilir = True
    o.durum = Durum(ulasilabilir=True, kimlik_dogrulandi=False, bagli=True,
                    rakip_oturum=True)
    o._gecisleri_bildir()

    (_, mesaj) = [h for h in haberler if h[0] == "ibkr_oturum"][0]
    assert "localhost:5001" not in mesaj, "rakip oturumda tarayiciya yonlendirdi"
    assert "IKINCI" in mesaj and "kullanici adi" in mesaj
    assert "Portfoy" in mesaj, "hala calisan katman soylenmedi"


def test_RAKIPSIZ_dususte_tarayici_yolu_HALA_gosterilir():
    """Diger dal bozulmamali: gercekten giris gerekiyorsa adres verilir."""
    haberler = []
    o = Oturum(_istemci(SahteOturum()), bildir=lambda a, m, **k: haberler.append((a, m)))
    o._onceki_kullanilabilir = True
    o.durum = Durum(ulasilabilir=True, kimlik_dogrulandi=False, bagli=True,
                    rakip_oturum=False)
    o._gecisleri_bildir()
    (_, mesaj) = [h for h in haberler if h[0] == "ibkr_oturum"][0]
    assert "localhost:5001" in mesaj



# ----------------------------------------------------------------------
# Iptal DEFTERE de yaziliyor
# ----------------------------------------------------------------------
def test_IPTAL_defteri_GUNCELLER():
    """
    `iptal_yurut` `db` parametresini aliyor ve HIC KULLANMIYORDU: Ali
    emri iptal edince IBKR'ye gidiyordu ama defterde satir `kabul`
    kaliyordu. "Defter IBKR'den ayrisiyor" sinifinin YAZMA yolundaki
    hali — ve asili satir zararsiz degil, `onkontrol` onu gorup yeni
    emri ENGELLIYOR.
    """
    db = _gecici_db()
    sid = db.emir_yaz(sahip="ali", hesap="U1", conid="8894", yon="BUY",
                      tur="LMT", adet=0.05, fiyat=91.0, sure="DAY",
                      parmak_izi="x", durum="kabul", emir_id="2141314594")

    class _Ist:
        def delete(self, yol):
            return {"msg": "Request Submitted"}

        def kapat(self):
            pass

    import finagent.bot.emirakis as _EA
    eski = _EA.Istemci
    _EA.Istemci = lambda *a, **k: _Ist()
    try:
        metin = _EA.iptal_yurut(
            _ayar(), db,
            {"emir_id": "2141314594", "hesap": "U1", "satir_id": sid,
             "hazirlik_ts": _time.time()},
            "ali")
    finally:
        _EA.Istemci = eski

    r = db.query("SELECT * FROM emirler WHERE id=?", (sid,))[0]
    # `iptal_edildi` DEGIL: IBKR'nin yaniti "istek alindi" demek.
    assert r["durum"] == "iptal_istendi", r["durum"]
    assert "iptal_istendi" in metin
    db.close()


def test_IPTAL_ISTEGI_dogrulanunca_DUSTU_degil_IPTAL_EDILDI_yazilir():
    """
    Niyet korunmali: "dustu" (kendiliginden oldu) ile "iptal ettim"
    ayni sey degil. Alti ay sonra "bu emir neden gerceklesmedi"
    sorusunun cevabi defterde durmali.
    """
    k = MB.karar(_satir(durum="iptal_istendi"), acik=None,
                 dstat={"order_status": "Cancelled"}, dolum_var=None,
                 simdi_ts=0)
    assert k.yeni_durum == "iptal_edildi" and k.kod == "S17_iptal_onaylandi"


def test_IPTAL_YETISMEZSE_dolum_YUKSEK_SESLE_soylenir():
    """
    Piyasa acilisinda iptal ile dolum YARISIR. Dolum kazanirsa
    kullanicinin kafasindaki durum (iptal ettim) ile gercek durum
    (kagit elimde) TERS olur. Sessizce 'gerceklesti' yazmak en pahali
    surprizi gomerdi.
    """
    k = MB.karar(_satir(durum="iptal_istendi"), acik=None,
                 dstat={"order_status": "Filled", "cum_fill": "0.05",
                        "total_size": "0.05"}, dolum_var=None, simdi_ts=0)
    assert k.yeni_durum == "gerceklesti"
    assert k.kod == "S16_iptal_yetismedi"
    assert "iptal yetismedi" in k.aciklama and "⚠️" in k.aciklama


def test_IPTAL_GECMEZSE_emir_hala_canli_diye_uyarilir():
    """IBKR iptali GARANTI ETMIYOR — gecmediyse kullanici bilmeli."""
    k = MB.karar(_satir(durum="iptal_istendi"),
                 acik={"orderId": "1", "status": "Submitted"},
                 dstat={"order_status": "Submitted"}, dolum_var=None,
                 simdi_ts=0)
    assert k.kod == "S18_iptal_gecmedi"
    assert k.yeni_durum is None, "iptal gecmemisken defter kapatildi"
    assert k.eylem == "iptal"


def test_iptal_istendi_satiri_MUTABAKATA_GIRER():
    """Kapanmamis sayilmali, yoksa kimse sonuclandirmaz."""
    db = _gecici_db()
    db.emir_yaz(sahip="ali", hesap="U1", conid="8894", yon="BUY", tur="LMT",
                adet=0.05, sure="DAY", parmak_izi="x", durum="iptal_istendi")
    assert len(db.kapanmamis_emirler("ali")) == 1
    db.close()



# ----------------------------------------------------------------------
# 26 Agustos aksami: bes kusur
# ----------------------------------------------------------------------
def test_ISLEM_GECMISI_days_ACIKCA_verilir():
    """
    `days` OTURUMDA YAPISIYOR. Olculdu (26 Agu), ayni oturumda:

        days=7 -> 3 islem · paramsiz -> 3   (7'yi devraldi)
        days=1 -> 0 islem · paramsiz -> 0   (1'i DEVRALDI)
        days=7 -> 3 islem · paramsiz -> 3

    Parametresiz cagri, en son kim ne verdiyse onu miras aliyor. Ilk
    surum parametresiz cagiriyordu — yani dolum KANITININ kendisi
    baskasinin biraktigi filtreye bagliydi. Bos donunce DOLMUS bir emir
    "izi yok" diye kapatilirdi: modulun onlemek icin yazildigi senaryo.
    """
    gorulen = {}

    class _Ist:
        def get(self, yol, params=None):
            gorulen["yol"], gorulen["params"] = yol, params
            return []

    MB.islemler(_Ist())
    assert gorulen["params"], "days parametresi HIC gonderilmedi"
    assert int(gorulen["params"]["days"]) >= 2, gorulen


def test_DOLUM_FIYATI_IBKR_BEYANINDAN_okunur():
    """
    Bot bir kez "IBKR bana tam dolum fiyatini dondurmuyor" deyip nakit
    farkindan GERIYE HESAPLADI (91,00 tahmin; gercegi 90,99). Oysa
    `/iserver/account/trades` acikca veriyor. Kaynak varken cikarim
    yapmak ayri bir hata sinifi: uydurma degil, BEYAN EDILMISI
    gormezden gelme.
    """
    gecmis = [{"order_id": 2141314594, "price": "90.99",
               "commission": "0.05", "net_amount": 4.5495, "size": 0.05}]
    kayit = MB.dolum_kaydi(gecmis, "2141314594")
    assert kayit and kayit["price"] == "90.99"
    assert MB.dolum_kaydi(gecmis, "999") is None
    assert MB.dolum_kaydi(None, "2141314594") is None


def test_dolum_kaydi_KARARIN_aciklamasina_girer():
    """Gerceklesen emir, fiyatini ve komisyonunu SOYLEMELI."""
    class _Ist:
        def get(self, yol, params=None):
            if "orders" in yol:
                return {"orders": [{"orderId": 1, "status": "Submitted"}]}
            if "trades" in yol:
                return [{"order_id": 2141314594, "price": "90.99",
                         "commission": "0.05", "net_amount": 4.5495}]
            raise IbkrHatasi("404")

    satir = _satir(id=3, emir_id="2141314594", durum="kabul")
    (k,) = [x for x in MB.kos(_Ist(), [satir], simdi_ts=0, hesap="U1")
            if x.satir_id == 3]
    assert k.yeni_durum == "gerceklesti", k
    assert "90.99" in k.aciklama, k.aciklama


def test_NAKIT_para_birimleri_BIRBIRINI_EZMEZ():
    """
    SESSIZ PARA HATASI, SAHADA OLCULDU (26 Agu). `positions` PK'si
    (sahip, snapshot_ts, account, instrument_id) — PARA BIRIMI YOK.
    EUR 2,06 ve USD -0,00 ayni `CASH` enstrumanina yazildi, ikincisi
    birincisini ezdi; ustelik `ON CONFLICT ... DO UPDATE` sette
    `currency` olmadigi icin TUTAR USD'den, ETIKET EUR'dan kaldi:

        defterde CASH/EUR/0,00        IBKR'de EUR 2,06

    Ne biri ne oteki. Ayni sinifin ucuncu tekrari (`prices` PK'sinda da
    para birimi yoktu). Cozum ayni: kotasyon basina AYRI KIMLIK.
    """
    from finagent.collectors.ibkrportfoy import IbkrPortfoyCollector as C
    from finagent.collectors.ibkrportfoy import satirlari_kur
    import inspect
    # Kural 25 Eyl'de iki kanalin (CPGW + bulut yedegi) ORTAK fonksiyonuna
    # tasindi; toplayici onu KULLANMALI (kablo), kural da orada durmali.
    assert "satirlari_kur(" in inspect.getsource(C), "toplayici ortak kurali kullanmiyor"
    kaynak = inspect.getsource(satirlari_kur)
    assert 'f"CASH.{pb.upper()}"' in kaynak, \
        "taban disi para birimi icin ayri sembol uretilmiyor"
    # `asset_type` HEPSINDE 'cash' kalmali: asagi akistaki nakit
    # suzgeclerinin cogu sembole degil ONA bakiyor.
    assert '"asset_type": "cash"' in kaynak


def test_pozisyon_kaydet_HESAP_LISTESI_URETILIR():
    """
    Liste "(bux, binance, midas)" diye SABIT yaziliydi ve `ibkr`
    eklendiginde guncellenmedi. Ayni dersin ayni dosyada bir kopyasi
    zaten vardi (arac aciklamasi `HESAP_VENUE`den uretiliyor) — kural
    iki yere yazilmis, biri duzeltilmis, IKIZI UNUTULMUS.
    """
    import inspect
    from finagent.bot import tools as T
    kaynak = inspect.getsource(T)
    assert 'hesap not in ("bux", "binance", "midas")' not in kaynak, \
        "hesap listesi hala elle yazili"
    assert "hesap not in HESAP_VENUE" in kaynak


def test_ARACI_SENKRON_hesapta_hata_COZUMU_SOYLER():
    """
    Onceki hali sadece "gecersiz hesap: 'ibkr'" deyip birakiyordu;
    model dogru cozumu (collector'u kosturmak) bulamadi ve kullaniciya
    "yazamiyorum" dedi. Hata mesaji cozumu de soylemeli — bu deponun
    kendi kurali.
    """
    import inspect
    from finagent.bot import tools as T
    from finagent.storage.db import ARACI_SENKRON, HESAP_VENUE
    assert set(ARACI_SENKRON) <= set(HESAP_VENUE)
    kaynak = inspect.getsource(T)
    assert "ARACI_SENKRON" in kaynak
    assert "veri_topla" in kaynak.split("ARACI_SENKRON")[2][:600], \
        "hata ipucu dogru araci adlandirmiyor"



def test_KAPANMIS_emir_DISARIDAN_GIRILMIS_sayilmaz():
    """
    SAHADA CIKTI (26 Agu, mutabakatin canli kosumu): gerceklesmis KO
    emri icin "IBKR'de acik emir var ama BIZIM defterde YOK" dendi ve
    IPTALI onerildi. Emir defterde DURUYORDU — sadece `gerceklesti`
    oldugu icin `kapanmamis_emirler()` onu dondurmuyordu.

    "Bu emir bizim mi" sorusu ACIK satirlarla cevaplanamaz. Yanlis
    cevabi iki kat pahali: hem yanlis beyan, hem dolmus bir emre iptal
    onerisi.
    """
    class _Ist:
        def get(self, yol, params=None):
            if "orders" in yol:
                return {"orders": [{"orderId": 2141314594, "ticker": "KO",
                                    "status": "Filled"}]}
            if "trades" in yol:
                return []
            raise IbkrHatasi("404")

    # Defterde KAPANMIS olarak duruyor -> `satirlar` bos, ama biliniyor.
    kararlar = MB.kos(_Ist(), [], simdi_ts=0, hesap="U1",
                      bilinen_nolar={"2141314594"})
    assert not [k for k in kararlar if k.kod == "S14_defterde_yok"], kararlar
    assert not kararlar, "bilinen emir icin bildirim uretildi"


def test_DISARIDAN_gelen_DOLMUS_emre_IPTAL_ONERILMEZ():
    """
    IBKR dolmus emirleri gun boyu acik emir listesinde tutuyor.
    Gercekten bizim olmayan ama SONUCLANMIS bir emir icin "iptal et"
    demek anlamsiz — bildirilir, eylem onerilmez.
    """
    class _Ist:
        def get(self, yol, params=None):
            if "orders" in yol:
                return {"orders": [{"orderId": 999, "ticker": "AAPL",
                                    "status": "Filled"}]}
            if "trades" in yol:
                return []
            raise IbkrHatasi("404")

    (k,) = MB.kos(_Ist(), [], simdi_ts=0, hesap="U1")
    assert k.kod == "S15b_disarida_sonuclanmis"
    assert k.eylem is None, "dolmus emre iptal onerildi"

    # Gercekten ACIK olan, bilinmeyen emir -> iptal ONERILIR.
    class _Ist2(_Ist):
        def get(self, yol, params=None):
            if "orders" in yol:
                return {"orders": [{"orderId": 888, "ticker": "AAPL",
                                    "status": "Submitted"}]}
            return super().get(yol, params)

    (k2,) = MB.kos(_Ist2(), [], simdi_ts=0, hesap="U1")
    assert k2.kod == "S14_defterde_yok" and k2.eylem == "iptal"



def test_RESTART_SONRASI_toparlanma_SOYLENIR():
    """
    SAHADA ISIRDI (27 Agu 00:01). Oturum 21:56'da dustu, "kapandi"
    mesaji GITTI. Ali girdi, oturum geldi — ama tam o aralikta bot
    yeniden baslatildi. Yeni surecte gecmis BELLEKTE oldugu icin
    `_onceki_kullanilabilir` None'di, "ilk olcum sessiz" kurali devreye
    girdi ve "OTURUM GELDI" mesaji HIC gonderilmedi.

    Kusur "ilk olcum sessiz"te degil, gecmisin bellekte tutulmasindaydi:
    "en son ne bildirdim" DISKTE duruyor ve restart'tan sag cikiyor.
    """
    haberler = []
    o = Oturum(_istemci(SahteOturum()),
               bildir=lambda a, m, **k: haberler.append((a, m)),
               onceki_kullanilabilir=False)      # en son "kapandi" demistik
    o.durum = Durum(ulasilabilir=True, kimlik_dogrulandi=True, bagli=True)
    o._gecisleri_bildir()
    assert any(a == "ibkr_oturum_geldi" for a, _ in haberler), haberler


def test_GERCEKTEN_ILK_kosuda_hala_sessiz():
    """
    Diger dal bozulmamali: gecmisi OLMAYAN bir kurulumda acilista
    "oturum geldi" demek gurultudur.
    """
    haberler = []
    o = Oturum(_istemci(SahteOturum()),
               bildir=lambda a, m, **k: haberler.append((a, m)))
    o.durum = Durum(ulasilabilir=True, kimlik_dogrulandi=True, bagli=True)
    o._gecisleri_bildir()
    assert not haberler, haberler


def test_BASARISIZ_INIT_USTEL_GERI_CEKILIR_sonsuza_denemez():
    """
    OLCULEN ARIZA (2026-08-27). Oturum 12:44'te normal sekilde bitti
    (`ssoExpires` sifira indi, bu kez yenilenmedi). Sonrasinda `_tik()`
    kimlik yokken HER TIK'te `kur()` cagirdi — geri cekilme yok, ust
    sinir yok. Bot 7,5 saat dakikada bir denedi; gateway logunda o gun
    4.528 istek, 1.256'si `auth/status`, ve gateway
    `retry 7 ... giving up` durumuna dustu.

    ASIL BEDEL: o sirada Ali'nin TAZE tarayici girisleri de reddedildi
    (`sso/validate?gw=1` -> 401 Access Denied). Yani basarisiz kurtarma
    denemesi kurtarmayi IMKANSIZ kildi — koruma, korudugu seyi kirdi.
    """
    import finagent.ibkr.oturum as O

    sahte = SahteOturum({"auth/status": SahteYanit(401),
                         "ssodh/init": SahteYanit(401)})
    with sahte_saat():
        o = Oturum(_istemci(sahte))
        # 6 saat boyunca 20 saniyede bir donen bot dongusu.
        for _ in range(6 * 60 * 3):
            o.tik()
            _time.sleep(20)
        init = sum(1 for _, u in sahte.cagrilar if "ssodh/init" in u)

    # Geri cekilmesiz surumde 6 saatte ~360 deneme olurdu (dakikada bir).
    # Ustel + 30 dk tavanla: 60,120,240,480,960,1800,1800... -> ~14.
    assert init <= 20, f"init {init} kez denendi — geri cekilme calismiyor"
    assert init >= 3, f"init yalnizca {init} kez denendi — hic denemiyor"

    # ARALIK GERCEKTEN BUYUYOR MU: ustel, sabit degil.
    o2 = Oturum(_istemci(SahteOturum()))
    beklenen = [O.INIT_TABAN_SN * (2 ** k) for k in range(5)]
    for i, bekle in enumerate(beklenen, start=1):
        o2._init_geri_cekil(0.0)
        assert o2._init_hata == i
        assert o2._init_sonraki == min(bekle, O.INIT_AZAMI_SN), \
            (i, o2._init_sonraki, bekle)
    # TAVAN: sonsuza kadar buyumez.
    for _ in range(20):
        o2._init_geri_cekil(0.0)
    assert o2._init_sonraki == O.INIT_AZAMI_SN

    # KIMLIK GERI GELINCE SIFIRLANIR — ve bu `_tik()` YOLUNDAN
    # sinaniyor, `_init_sifirla()`yi elle cagirarak DEGIL.
    #
    # Ilk surumde bu testin son bloğu dogrudan `_init_sifirla()`
    # cagiriyordu ve mutasyon testi onu YAKALAYAMADI: `_tik` icindeki
    # sifirlama tamamen silinse bile test yesil kaliyordu. Yesil test
    # tek basina kanit degil — kanit, dogru YOLU gecen testtir.
    #
    # Bedeli somut: sifirlama olmazsa Ali tarayicidan girip oturum
    # geldikten SONRA, oturum bir daha dustugunde bot 30 dakika
    # bekler — cunku sayac hala tavanda.
    yanit = {"v": SahteYanit(401)}

    class Degisken(SahteOturum):
        def request(self, yontem, url, **kw):
            self.cagrilar.append((yontem, url))
            return yanit["v"] if "auth/status" in url or "ssodh/init" in url \
                else SahteYanit(200, {})

    s3 = Degisken()
    with sahte_saat():
        o3 = Oturum(_istemci(s3))
        for _ in range(30):                    # basarisiz dene, sayac dolsun
            o3.tik()
            _time.sleep(60)
        assert o3._init_hata >= 3, o3._init_hata

        # Ali tarayicidan girdi: auth/status artik 200 ve authenticated.
        yanit["v"] = SahteYanit(200, {"authenticated": True, "connected": True})
        o3.tik()
        assert o3._init_hata == 0, \
            "kimlik geri geldi ama geri cekilme sayaci sifirlanmadi"
        assert o3._init_sonraki == 0.0

        # Ve oturum yeniden duserse HEMEN denenir, 30 dakika sonra degil.
        yanit["v"] = SahteYanit(401)
        oncesi = sum(1 for _, u in s3.cagrilar if "ssodh/init" in u)
        _time.sleep(60)
        o3.tik()
        assert sum(1 for _, u in s3.cagrilar if "ssodh/init" in u) > oncesi, \
            "sifirlamadan sonra init hemen denenmedi"


def test_RAKIP_OTURUM_geri_cekilmeyi_TETIKLEMEZ():
    """
    `kur()` rakip oturumda HIC ISTEK ATMADAN donuyor. Onu "basarisiz
    deneme" saymak, Ali telefondan cikinca botun 30 dakika bosuna
    beklemesi demekti — koruma mekanizmasi toparlanmayi GECIKTIRIRDI.

    Ayrim `kur()`in donus degerinde: None = hic denenmedi.
    """
    sahte = SahteOturum({"auth/status": SahteYanit(
        200, {"authenticated": False, "connected": True, "competing": True})})
    with sahte_saat():
        o = Oturum(_istemci(sahte))          # yaris KAPALI (varsayilan)
        for _ in range(10):
            o.tik()
            _time.sleep(60)
    assert not any("ssodh/init" in u for _, u in sahte.cagrilar), \
        "rakip oturum varken init denendi"
    assert o._init_hata == 0, \
        "hic istek atilmadigi halde geri cekilme sayaci arttı"


VRT_ISLEM = {
    "execution_id": "00012978.6a95fa10.01.01", "symbol": "VRT", "side": "B",
    "size": 0.38, "price": "257.80", "commission": "0.35",
    "net_amount": 97.964, "trade_time": "20260831-14:13:36",
    "conid": 402783527, "order_id": 1473988529,
}


class _DolumIstemcisi:
    """Islem gecmisinde VRT dolumu OLAN sahte istemci."""

    def __init__(self, acik=None):
        self.trades_cagrisi = 0
        self._acik = acik if acik is not None else [{
            "orderId": 1473988529, "conid": 402783527, "side": "BUY",
            "status": "Filled", "totalSize": 0.38, "filledQuantity": 0.38,
        }]

    def get(self, yol, params=None):
        if "trades" in yol:
            self.trades_cagrisi += 1
            return [dict(VRT_ISLEM)]
        if "orders" in yol:
            return {"orders": list(self._acik)}
        raise IbkrHatasi("404")


def test_DOLMUS_ilan_edilen_emirde_islem_kaydi_HER_DURUMDA_araniyor():
    """
    OLCULEN KUSUR (2026-08-31, VRT emri 1473988529).

    `kos()` "gerceklesti" karari veriyordu ama `gecmis` BASKA bir
    kosula bagliydi. O kosul atesletmeyince `gecmis` None kaliyor,
    `dolum_kaydi(None, ...)` SESSIZCE None donuyor ve satir dolum
    fiyati ALINMADAN kapaniyordu. Karar ile KANIT ayri kosullara
    bagliydi.

    Sonucu agirdi: satir kapaninca `kapanmamis_emirler` onu disliyor
    ve dolum bir daha ASLA olculemiyordu.
    """
    ist = _DolumIstemcisi()
    kararlar = MB.kos(ist, [_satir(id=9, emir_id="1473988529",
                                   durum="kabul", symbol="VRT")],
                      hesap="U1", bilinen_nolar={"1473988529"})
    assert len(kararlar) == 1, kararlar
    k = kararlar[0]
    assert k.yeni_durum == "gerceklesti", k
    # KANIT KOLONLARA GIRDI — `not_` metnine degil
    assert k.alanlar.get("dolum_fiyat") == 257.80, k.alanlar
    assert k.alanlar.get("dolum_komisyon") == 0.35, k.alanlar
    assert k.alanlar.get("dolum_ts"), k.alanlar

    # YAPISAL: "gerceklesti" dalinin KENDISI gecmisi cekmeli. Cekim
    # yalnizca yukaridaki kosula bagli kalirsa kusur geri gelir.
    import ast
    import inspect
    import textwrap
    agac = ast.parse(textwrap.dedent(inspect.getsource(MB.kos)))
    for dugum in ast.walk(agac):
        if (isinstance(dugum, ast.If)
                and "gerceklesti" in ast.dump(dugum.test)):
            assert "islemler" in ast.dump(ast.Module(body=dugum.body,
                                                     type_ignores=[])), \
                "'gerceklesti' dali islem gecmisini CEKMIYOR"
            break
    else:
        raise AssertionError("'gerceklesti' dali bulunamadi")


def test_KILITLI_satir_kurtariliyor_ve_YENIDEN_KARAR_VERILMIYOR():
    """
    "gerceklesti" ama `dolum_fiyat` NULL olan satir BITMIS DEGIL:
    kagit el degistirdi, kanit alinmadi. Sahada IKI satir boyle
    kilitlendi (VRT 1473988529 ve daha eski KO).

    YENIDEN KARAR VERILMIYOR: durum zaten dogru. Yeni bir `yeni_durum`
    turetmek, dogru bir durumu yeniden hesaplamak olurdu ve o hesap
    yanlis cikabilirdi.
    """
    ist = _DolumIstemcisi()
    kararlar = MB.kos(ist, [_satir(id=5, emir_id="1473988529",
                                   durum="gerceklesti", dolum_fiyat=None,
                                   symbol="VRT")],
                      hesap="U1", bilinen_nolar={"1473988529"})
    assert len(kararlar) == 1, kararlar
    k = kararlar[0]
    assert k.kod == "S1b_dolum_kurtarildi", k.kod
    assert k.yeni_durum is None, "kurtarma YENIDEN DURUM yaziyor"
    assert k.alanlar.get("dolum_fiyat") == 257.80, k.alanlar
    assert k.alanlar.get("dolum_komisyon") == 0.35, k.alanlar
    assert k.eylem is None, "kurtarma bir EYLEM oneriyor"


def test_DOLUMU_TAM_olan_kapanmis_satir_TEKRAR_islenmiyor():
    """
    Kurtarma yolu yalnizca EKSIK satir icin. Dolumu yazilmis kapanmis
    bir satir her kosuda yeniden islenirse, mutabakat her seferinde
    ayni satiri raporlar ve `/iserver/trades` bosuna cekilir.
    """
    ist = _DolumIstemcisi()
    kararlar = MB.kos(ist, [_satir(id=5, emir_id="1473988529",
                                   durum="gerceklesti", dolum_fiyat=257.80,
                                   symbol="VRT")],
                      hesap="U1", bilinen_nolar={"1473988529"})
    assert kararlar == [], kararlar
    assert ist.trades_cagrisi == 0, "gereksiz islem gecmisi cagrisi"


def test_ISLEM_KAYDI_YOKSA_kurtarma_SESSIZ_GECMEZ():
    """
    IBKR'nin islem penceresi kayabilir. O zaman dolum KALICI olarak
    olculemez ve bu LOGLANMALI — sessizce gecilirse kayip fark
    edilmez.
    """
    class _Bos(_DolumIstemcisi):
        def get(self, yol, params=None):
            if "trades" in yol:
                self.trades_cagrisi += 1
                return []
            return super().get(yol, params)

    ist = _Bos()
    kararlar = MB.kos(ist, [_satir(id=5, emir_id="1473988529",
                                   durum="gerceklesti", dolum_fiyat=None,
                                   symbol="VRT")],
                      hesap="U1", bilinen_nolar={"1473988529"})
    assert kararlar == [], kararlar
    assert ist.trades_cagrisi == 1, "islem gecmisine HIC bakilmadi"

    import inspect
    g = inspect.getsource(MB.kos)
    assert "log.warning" in g and "islem kaydi" in g, \
        "kayip dolum SESSIZCE geciliyor"


def test_kapanmamis_emirler_DOLUMU_EKSIK_kapanmis_satiri_DONDURUR():
    """
    GIRIS KAPISI. `kos()` ne kadar dogru olursa olsun, satir buraya
    girmiyorsa hicbir sey olmaz — sahada tam boyle oldu.
    """
    import tempfile
    from pathlib import Path as _P

    from finagent.storage.db import Database

    with tempfile.TemporaryDirectory() as d:
        db = Database(_P(d) / "t.db")
        db.init_schema()
        with db.tx() as c:
            for i, (durum, dolum) in enumerate([
                    ("gerceklesti", None),      # KILITLI — gelmeli
                    ("gerceklesti", 257.80),    # tam — GELMEMELI
                    ("kabul", None),            # acik — gelmeli
                    ("dustu", None),            # bitmis — GELMEMELI
            ], start=1):
                c.execute(
                    "INSERT INTO emirler (id,sahip,hesap,conid,yon,tur,"
                    "adet,sure,parmak_izi,olusma_ts,durum,dolum_fiyat)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i, "ali", "U1", "8894", "BUY", "LMT", 1.0, "DAY",
                     f"pi{i}", "2026-08-31T00:00:00", durum, dolum))
        gelen = {r["id"] for r in db.kapanmamis_emirler("ali")}
        assert gelen == {1, 3}, gelen
        db.close()


# =====================================================================
# PIYASA SAATI KONTROLU
# =====================================================================

def _seans_db(d, sembol="KO", venue="BUX", kaynak="yahoo", conid="265598"):
    """
    Borsasi COZULEBILEN bir enstruman kurar.

    `borsa_coz` zinciri: venue VENUE_BORSA'da degil -> `fiyat_kaynagi`
    'yahoo' -> Yahoo sembolunde nokta yok -> "ABD". Canli veride KO ve
    VRT tam bu yoldan cozuluyor.

    KIMLIK SATIRI CANLIDAKININ AYNISI OLMAK ZORUNDA. Ilk yazimda
    yalnizca `conid` yaziliyordu ve `_yahoo_sembolu` None donuyordu
    (SEC dogrulamasi istiyor: `status='dogrulandi'` + `sec_ticker`).
    Fixture cozulmedigi icin test HICBIR SEYI olcmuyordu — kontrol
    kapali oldugu halde "gecti" diyecekti.
    """
    import pathlib as _p

    from finagent.storage.db import Database
    db = Database(_p.Path(d) / "s.db")
    db.init_schema()
    iid = db.upsert_instrument(symbol=sembol, venue=venue, name=sembol,
                               asset_type="equity", currency="USD")
    db.upsert_prices(iid, [{"ts": "2026-08-31", "open": 100.0, "high": 101.0,
                            "low": 99.0, "close": 100.0, "volume": 1}],
                     kaynak, currency="USD")
    with db.tx() as c:
        c.execute(
            """INSERT INTO identities
               (instrument_id, cik, sec_ticker, sec_name, exchange, status,
                method, conid)
               VALUES (?,?,?,?,'NYSE','dogrulandi','ticker+ad',?)""",
            (iid, "0000021344", sembol, sembol, conid))
    return db, iid


def _an(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso)


# ABD seansi 09:30-16:00 New York. Persembe secildi (hafta ici).
ABD_ACIK = "2026-09-03T17:00:00+00:00"      # 13:00 NY — seans ortasi
ABD_KAPALI = "2026-09-03T23:30:00+00:00"    # 19:30 NY — kapanmis


def test_piyasa_saati_KAPALIYKEN_limit_UYARIR_engellemez():
    """
    Seans disinda limit emri birakip acilisa kuyruga sokmak MESRU ve
    yaygin. Engellemek katmani kullanilamaz kilardi — modulun kendi
    doktrini: "Her seyi engel yapmak katmani kullanilamaz kilar."
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db, _ = _seans_db(d)
        istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 165.0)
        k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=db,
                       simdi=_an(ABD_KAPALI))
        assert k.gonderilebilir is True, f"limit ENGELLENDI: {k.engeller}"
        assert any("piyasa KAPALI" in u for u in k.uyarilar), k.uyarilar
        assert any("kuyrukta bekler" in u for u in k.uyarilar), k.uyarilar
        db.close()


def test_piyasa_saati_KAPALIYKEN_piyasa_emri_ENGELLENIR():
    """
    Kapali piyasada MKT, acilis SEANSININ belirleyecegi bir fiyata
    razi olmaktir; acilis boslugu gorulmeden kabul edilir.

    "MKT + gercek zamanli olmayan veri = ENGEL" kuralinin ayni
    gerekcesi: ne odeyecegini BILMIYORSUN.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db, _ = _seans_db(d)
        istek = E.EmirIstegi("U1", "265598", "BUY", "MKT", 10)
        k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=db,
                       simdi=_an(ABD_KAPALI))
        assert not k.gonderilebilir, "kapali piyasada MKT gecti"
        assert any("piyasa KAPALI" in e for e in k.engeller), k.engeller
        db.close()


def test_piyasa_saati_ACIKKEN_hicbir_sey_soylemez():
    """Seans acikken bu kontrol SUSMALI — gurultu korumayi degersizlestirir."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db, _ = _seans_db(d)
        istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 165.0)
        k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=db,
                       simdi=_an(ABD_ACIK))
        assert not any("piyasa KAPALI" in u for u in k.uyarilar), k.uyarilar
        assert k.gonderilebilir is True
        db.close()


def test_piyasa_saati_BORSA_COZULEMEZSE_HUKUM_VERMEZ():
    """
    `borsa_coz` bilmiyorsa None diyor ve bu kontrol de SUSUYOR.

    Olculdu (2026-09-01): conid tasiyan 501 enstrumanin 445'inde borsa
    cozulemiyor (cogu hic izlenmeyen S&P 500 uyesi). Cozulemeyeni
    "kapali" saymak, mesru emirleri KAPALI PIYASA diye engellerdi;
    "acik" saymak korumayi yalan yapardi. Ucuncu sik: hukum verme.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        # `alphavantage` kaynagi -> `borsa_coz` "Yahoo degil" deyip None.
        db, _ = _seans_db(d, kaynak="alphavantage")
        from finagent.piyasa import borsa_coz
        assert borsa_coz(db, 1, "BUX") is None, "fixture cozuluyor, tuzak yok"

        istek = E.EmirIstegi("U1", "265598", "BUY", "MKT", 10)
        k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=db,
                       simdi=_an(ABD_KAPALI))
        assert not any("piyasa KAPALI" in x
                       for x in k.engeller + k.uyarilar), \
            f"borsa bilinmiyorken HUKUM VERDI: {k.engeller} {k.uyarilar}"
        db.close()


def test_piyasa_saati_DB_YOKSA_sessizce_atlanir_HATA_SAYILMAZ():
    """
    `dogrula` `db=None` ile de cagrilabiliyor. O durumda kontrol yok —
    ve bu bir ARIZA DEGIL, "sorulmadi" demek.

    LOG DA OLCULUYOR ve sebebi su: `db is None` kapisi kaldirilsa
    `db.query` AttributeError atar, genis `except` onu yutar ve sonuc
    AYNI None olur. Yani davranissal olarak ayirt edilemez — mutasyon
    turunde "esdeger mutant". Ayirt eden tek sey LOG: beklenen bir
    durumu her emirde istisna olarak loglamak, gercek arizalari
    gurultuye gomer.
    """
    import logging

    kayitlar = []

    class _Yakala(logging.Handler):
        def emit(self, r):
            kayitlar.append(r.getMessage())

    h = _Yakala()
    lg = logging.getLogger("finagent.ibkr.onkontrol")
    # SEVIYE DE DUSURULMELI: handler eklemek yetmiyor. Varsayilan
    # etkin seviye WARNING ve `log.info` kaydi handler'a HIC ulasmiyor;
    # test her sey bozukken bile yesil kalirdi.
    onceki = lg.level
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        istek = E.EmirIstegi("U1", "265598", "BUY", "MKT", 10)
        k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=None,
                       simdi=_an(ABD_KAPALI))
    finally:
        lg.removeHandler(h)
        lg.setLevel(onceki)

    assert not any("piyasa KAPALI" in x for x in k.engeller + k.uyarilar)
    assert not any("seans durumu cozulemedi" in m for m in kayitlar), \
        f"db yoklugu ISTISNA olarak loglandi: {kayitlar}"


def test_piyasa_saati_KONTROLUN_ARIZASI_emri_engellemez():
    """
    Bu bir EK koruma. Kendi hatasi yuzunden mesru bir emri durdurmasi,
    korumadan daha buyuk bir zarar olurdu.
    """
    class _Patlak:
        def query(self, *a, **k):
            raise RuntimeError("db dustu")

    istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 165.0)
    k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=_Patlak(),
                   simdi=_an(ABD_KAPALI))
    assert k.gonderilebilir is True, f"kontrolun arizasi emri durdurdu: {k.engeller}"


def test_piyasa_saati_KABLO_KACISI_yok():
    """
    YAPISAL: `dogrula`nin `db` parametresi VARSAYILAN None. Cagiran onu
    gecmezse kontrol sessizce hicbir sey yapmaz — bu deponun bir
    numarali ariza kalibi.
    """
    import ast
    import inspect
    import textwrap

    from finagent.bot import emirakis

    kaynak = textwrap.dedent(inspect.getsource(emirakis))
    agac = ast.parse(kaynak)
    # SUZGEC `OK.` ONEKINE BAKIYOR. Yalnizca `attr == "dogrula"` demek
    # `istek.dogrula()`yi (EmirIstegi'nin kendi kapisi) da yakaliyor ve
    # o zaten anahtar kelime almiyor — test kendi gurultusune takiliyordu.
    cagrilar = [d for d in ast.walk(agac)
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr == "dogrula"
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "OK"]
    assert cagrilar, "emirakis onkontrolu HIC cagirmiyor"
    for c in cagrilar:
        assert any(kw.arg == "db" for kw in c.keywords), \
            "onkontrol `db` GECILMEDEN cagriliyor — piyasa saati kontrolu olu"


def test_piyasa_saati_HAFTA_SONU_de_kapali_sayilir():
    """Cumartesi seans yok; `durum` 'hafta sonu' ve mesajda gorunur."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        db, _ = _seans_db(d)
        istek = E.EmirIstegi("U1", "265598", "BUY", "LMT", 10, 165.0)
        k = OK.dogrula(_istemci(SahteOnkontrolOturumu()), istek, db=db,
                       simdi=_an("2026-09-05T17:00:00+00:00"))   # Cumartesi
        assert any("hafta sonu" in u for u in k.uyarilar), k.uyarilar
        db.close()


def test_GTC_ISTEGIN_TA_ICINE_kadar_gidiyor_ve_ONAY_EKRANINDA_yaziyor():
    """
    OLCULEN KUSUR (2026-09-05, Ali bildirdi): "IBKR mobil uygulamasinda
    GTC presetini kurdum ama agent hala GTC emri veremem diyor."

    KOK SEBEP `emirakis._istek` icinde TEK SATIRDI: `sure="DAY"` SABIT
    yaziliydi. Oysa GTC destegi BASTAN BERI vardi —

        E.SURELER          {"DAY","GTC","IOC","OPG"}   dogrulama
        EmirIstegi.sure    alan
        .govde()["tif"]    IBKR govdesi
        .parmak_izi()      onay fisi
        db.emir_yaz(sure=) defter
        _coz(...sure=...)  onay sonrasi yeniden kurma

    Yani kablo BIR UCTAN OBUR UCA dosenmisti; yalnizca girisi baglayan
    satir sabitti. Bu deponun bir numarali ariza kalibi.

    MOBIL PRESET BU KANALI KAPSAMIYOR ve kapsayamaz: `govde()` `tif`
    alanini ACIKCA yaziyor, yani REST ucuna ne gonderirsek o gecerli.
    Uygulama preseti yalnizca uygulamadan girilen emirlerin varsayilani.
    """
    from finagent.bot import emirakis as EA

    # 1) SURE ISTEGE, ISTEKTEN DE IBKR GOVDESINE GIDIYOR.
    istek = E.EmirIstegi(hesap="U1", conid="265598", yon="SELL",
                         tur="LMT", adet=0.38, fiyat=288.0, sure="GTC")
    istek.dogrula()
    assert istek.govde()["tif"] == "GTC", istek.govde()

    # 2) PARMAK IZI SUREYI TASIYOR: ayni emrin DAY ve GTC hali AYNI fis
    #    olamaz, yoksa onaylanan sey ile gonderilen sey ayrisirdi.
    gun = E.EmirIstegi(hesap="U1", conid="265598", yon="SELL",
                       tur="LMT", adet=0.38, fiyat=288.0, sure="DAY")
    assert istek.parmak_izi() != gun.parmak_izi(), \
        "DAY ve GTC ayni parmak izini uretiyor — onay fisi sureyi tasimiyor"

    # 3) `_istek` ARTIK SABIT YAZMIYOR (kusurun kendisi).
    class _Ayar:
        def get(self, k, v=None): return {"ibkr.varsayilan_sure": "DAY"}.get(k, v)

    class _Db:
        def query(self, *a, **k):
            return [{"id": 1, "conid": "265598"}]

    coz = EA.komut_coz("VRT SAT 0.38 288 GTC")
    yapilan, _ = EA._istek(_Ayar(), _Db(), coz, "U1")
    assert yapilan.sure == "GTC", yapilan.sure

    # 4) SURE VERILMEZSE AYAR, O DA YOKSA DAY — ve ayar BOZUKSA DAY.
    coz2 = EA.komut_coz("VRT SAT 0.38 288")
    assert EA._istek(_Ayar(), _Db(), coz2, "U1")[0].sure == "DAY"

    class _GtcAyar:
        def get(self, k, v=None): return {"ibkr.varsayilan_sure": "gtc"}.get(k, v)
    assert EA._istek(_GtcAyar(), _Db(), coz2, "U1")[0].sure == "GTC", \
        "ayar okunmuyor — 'bir kez kur, unut' calismiyor"

    class _BozukAyar:
        def get(self, k, v=None): return {"ibkr.varsayilan_sure": "SONSUZ"}.get(k, v)
    assert EA._istek(_BozukAyar(), _Db(), coz2, "U1")[0].sure == "DAY", \
        "bozuk ayar guvenli tarafa DUSMUYOR"

    # 5) ONAY EKRANI SUREYI YAZIYOR — para ekraninda sessiz alan olmaz.
    class _OK:
        gonderilebilir = True
        engeller: list = []
        uyarilar: list = []
        referans_fiyat = None
        referans_kip = None
        tahmini_tutar = None
        para_birimi = "USD"

    metin = EA._ozet_metni(coz, yapilan, _OK(), False, None, None)
    assert "GTC" in metin, f"onay ekraninda sure yok:\n{metin}"
    assert "iptal edilene kadar" in metin, metin
    gun_metni = EA._ozet_metni(coz2, EA._istek(_Ayar(), _Db(), coz2, "U1")[0],
                               _OK(), False, None, None)
    assert "DAY" in gun_metni and "seans sonunda duser" in gun_metni, gun_metni


def test_emir_ARACI_sureyi_KOMUTA_gecirmeyi_unutmuyor():
    """
    Arac ile `/emir` komutu AYNI cozumleyiciden geciyor. Arac `sure`
    parametresini alip komut dizesine EKLEMEZSE, arac komuttan daha az
    sey yapabilir hale gelir — ve tam olarak oyleydi: model
    kullaniciya "bendeki emir araci yalnizca dort sey aliyor" dedi.
    """
    import inspect
    from finagent.bot.tools import ToolBox

    kaynak = inspect.getsource(ToolBox.araclar)
    # Pencere dekoratorden govdenin sonuna kadar: sema dekoratorde,
    # okuma govdede. Ikisi de AYNI pencerede olmali.
    i = kaynak.index('@tool("ibkr_emir_hazirla"')
    govde = kaynak[i:i + 3000]
    assert '"sure": str' in govde, "arac semasinda `sure` parametresi yok"
    assert 'args.get("sure")' in govde, "`sure` argumani hic okunmuyor"
    assert 'arg += f" {sure}"' in govde, \
        "`sure` komut dizesine EKLENMIYOR — arac onu sessizce dusuruyor"


# ---------------------------------------------------------------------------
# IBKR BULUT BAGLAYICISI KANALI (Faz 0, 2026-09-25)
#
# `ibkr/mcp_kanal.py` modeli yalnizca TETIKLEYICI olarak kullanir: arguman
# kodda, veri aracin HAM sonucunda. Testler ağa cikmaz; sahte SDK, gercek
# SDK'nin olculen davranisini taklit eder (kapiya sor -> izin varsa kanca)
# ve modelin METNI olarak kasten uydurma bir sayi uretir. Fikstürler gercek
# baglayicidan yakalandi; hesap degerleri SENTETIK (depo uzak sunucuda).
# ---------------------------------------------------------------------------

MCP_FIKSTUR = KOK / "tests" / "mcp_fikstur"


def _mcp_fikstur(ad: str) -> str:
    return (MCP_FIKSTUR / f"{ad}.json").read_text(encoding="utf-8")


class ToolResultBlock:                      # ad SDK ile ayni: modul adla taniyor
    def __init__(self, content, is_error=None, tool_use_id=None):
        self.content, self.is_error = content, is_error
        self.tool_use_id = tool_use_id


class ToolUseBlock:
    def __init__(self, name, id_):
        self.name, self.id = name, id_


class TextBlock:
    def __init__(self, text):
        self.text = text


class _McpMesaj:
    def __init__(self, content):
        self.content = content


class ResultMessage:
    def __init__(self, num_turns=3):
        self.num_turns, self.subtype = num_turns, "success"


def _sahte_sorgu(istek_ad=None, istek_arg=None, yanit=None, hata=None,
                 bulundu=True, bekle_sn=0.0, yakala=None):
    """
    Gercek SDK'nin OLCULEN sirasi: ToolSearch sonucu (tool_reference) ->
    model araci ister -> `can_use_tool` -> izin varsa PostToolUse /
    PostToolUseFailure kancasi -> modelin metni -> ResultMessage.
    `istek_ad/arg` None ise model istenen araci istenen argumanla ister.
    """
    async def q(prompt, options):
        import anyio
        if yakala is not None:
            yakala["options"] = options
        async for m in prompt:
            ilk = m["message"]["content"]
            break
        ad = ilk.split("select:")[1].split()[0]
        arg = __import__("json").loads(ilk.split("argumanlarla BIR KEZ cagir: ")[1].split("\n")[0])
        if bekle_sn:
            await anyio.sleep(bekle_sn)
        yield _McpMesaj([ToolUseBlock("ToolSearch", "ts_1")])
        ref = ([{"type": "tool_reference", "tool_name": ad}] if bulundu
               else [{"type": "text", "text": "No matching deferred tools found"}])
        yield _McpMesaj([ToolResultBlock(ref, tool_use_id="ts_1")])
        if bulundu:
            iad, iarg = istek_ad or ad, arg if istek_arg is None else istek_arg
            izin = await options.can_use_tool(iad, iarg, None)
            if type(izin).__name__ == "PermissionResultAllow":
                olay = "PostToolUseFailure" if hata else "PostToolUse"
                girdi = {"tool_name": iad, "tool_input": iarg}
                girdi.update({"error": hata} if hata else {"tool_response": yanit})
                for eslesen in options.hooks.get(olay, []):
                    if eslesen.matcher == iad:
                        for h in eslesen.hooks:
                            await h(girdi, "tu_1", None)
        # MODELIN METNI — kasten uydurma. Veri buradan OKUNMAMALI.
        yield _McpMesaj([TextBlock('TAMAM {"net_liquidation": 999999}')])
        yield ResultMessage()
    return q


def _mcp_cagir(arac, arg=None, **kw):
    import anyio
    from finagent.ibkr import mcp_kanal as K
    return anyio.run(lambda: K.cagir_async(arac, arg, **kw))


def test_mcp_SECILEN_12_arac_ve_JOKER_YOK():
    """
    Plan 34 aractan 12'sini aldi. Alinmayan bir arac (orn. `delete_watchlist`
    — geri alinamaz) bu kanaldan HIC cagrilamamali; ve hicbir kaynak dosyada
    `..._IBKR__*` gibi bir joker gecmemeli: joker, yarin eklenecek bir yazma
    aracini da acar.
    """
    import re
    from finagent.ibkr import mcp_kanal as K
    assert len(K.SECILEN) == 15   # 12 + Faz 5 (tema, arama) + Faz 6 (islem gecmisi); OKUMA
    assert sum(1 for f, y in K.SECILEN.values() if y) == 3, "yazma araclari: create/update/delete_alert"
    assert set(K.OKUMA_ARACLARI) == {K.ONEK + a for a in (
        "get_account_positions", "get_account_balances",
        "get_account_summary", "get_account_orders")}
    for alinmayan in ("delete_watchlist", "create_order_instruction",
                      "provide_customer_feedback", "set_alert_status"):
        try:
            K.tam_ad(alinmayan)
            raise AssertionError(f"{alinmayan} kanaldan cagrilabiliyor")
        except ValueError:
            pass
    for yol in (KOK / "src").rglob("*.py"):
        metin = yol.read_text(encoding="utf-8")
        assert not re.search(r"Interactive_Brokers_IBKR__\*", metin), f"joker: {yol}"


def test_mcp_kapi_yalnizca_BEKLENEN_arac_ve_arguman():
    from finagent.ibkr.mcp_kanal import ONEK, kapi_karari
    ad = ONEK + "get_price_snapshot"
    arg = {"contract_id": 273544, "market_data_names": ["last"]}
    assert kapi_karari(ad, dict(arg), ad, arg)[0]
    assert kapi_karari(ad, {**arg, "exchange": None}, ad, arg)[0], \
        "None alan arguman degisikligi degil"
    assert not kapi_karari(ad, {**arg, "contract_id": 265598}, ad, arg)[0]
    assert not kapi_karari(ad, {"contract_id": 273544}, ad, arg)[0]
    assert not kapi_karari(ONEK + "delete_alert", {"ids": ["1"]}, ad, arg)[0]
    assert not kapi_karari("Bash", {"command": "ls"}, ad, arg)[0]
    assert kapi_karari("ToolSearch", {"query": "x"}, ad, arg)[0]
    # AYNI ARGUMANLA BASKA ARAC. Argumansiz araclar cok (pozisyon, emir,
    # alarm listesi); ad kontrolu olmasa arguman kontrolu bunlari AYIRAMAZ.
    # Mutasyon turu bu boslugu buldu: ilk testteki "baska arac" ornekleri
    # hep farkli argumanliydi.
    poz, emir = ONEK + "get_account_positions", ONEK + "get_account_orders"
    assert not kapi_karari(emir, {}, poz, {})[0]


def test_mcp_ham_ayristirici_OLCULEN_IKI_BICIM_ve_bos_YOK_DEMEZ():
    """
    OLCULDU: cogu arac duz JSON metni donduruyor, `get_price_snapshot`
    icerik blogu LISTESI donduruyor. Ikisi de ayni veriye ayrismali; bos ya
    da JSON olmayan yanit `McpYanitBicimi` — "veri yok" DEGIL.
    """
    import json
    from finagent.ibkr.mcp_kanal import McpYanitBicimi, ham_ayristir
    d = ham_ayristir(_mcp_fikstur("get_account_summary"))
    assert d["currency"] == "EUR" and "net_liquidation" in d
    blok = json.loads(_mcp_fikstur("get_price_snapshot_call"))
    assert isinstance(blok, list) and blok[0]["type"] == "text", "fikstur olculen bicimi kaybetmis"
    s = ham_ayristir(blok)
    assert s["top-status"]["status"] == "FROZEN_DELAYED" and s["bid-ask"]["bid"] > 0
    assert ham_ayristir({"a": 1}) == {"a": 1}
    # Ayni blok listesi JSON METNI olarak gelirse de acilmali (Faz 4'te
    # bulundu: bir kez cozulunce hala liste kaliyordu).
    assert ham_ayristir(_mcp_fikstur("get_price_snapshot_call")) == s
    for bozuk in ("", "   ", "Error: bir sey oldu", [], None):
        try:
            ham_ayristir(bozuk)
            raise AssertionError(f"bozuk yanit kabul edildi: {bozuk!r}")
        except McpYanitBicimi:
            pass
    assert ham_ayristir(_mcp_fikstur("get_account_orders")) == {"orders": []}, \
        "bos LISTE gecerli veridir (acik emir yok), bicim hatasi degil"


def test_mcp_cagri_veriyi_HAM_SONUCTAN_alir_modelin_METNINDEN_DEGIL():
    """
    Sahte model metinde `net_liquidation: 999999` yaziyor; donen veri
    fiksturdeki deger olmali. Ayrica OLCULEN TUZAK: arac `allowed_tools`ta
    olursa SDK kapiyi atlar — liste BOS olmali.
    """
    import json
    yakala = {}
    r = _mcp_cagir("get_account_summary", None,
                   _sorgu=_sahte_sorgu(yanit=_mcp_fikstur("get_account_summary"),
                                       yakala=yakala))
    beklenen = json.loads(_mcp_fikstur("get_account_summary"))["net_liquidation"]
    assert r.veri["net_liquidation"] == beklenen != 999999
    assert r.tur == 3 and not r.reddedilen
    assert yakala["options"].allowed_tools == [], \
        "arac allowed_tools'ta: SDK onu otomatik onaylar, arguman kapisi OLU"
    assert yakala["options"].can_use_tool is not None


def test_mcp_model_ARGUMANI_DEGISTIRIRSE_arac_CALISMAZ():
    from finagent.ibkr.mcp_kanal import McpAracCagrilmadi, ONEK
    istenen = {"contract_id": 273544, "market_data_names": ["last"]}
    for ad, arg in ((None, {"contract_id": 265598, "market_data_names": ["last"]}),
                    (ONEK + "get_account_positions", {})):
        try:
            _mcp_cagir("get_price_snapshot", istenen,
                       _sorgu=_sahte_sorgu(istek_ad=ad, istek_arg=arg,
                                           yanit='{"sizinti": true}'))
            raise AssertionError("farkli istek calisti")
        except McpAracCagrilmadi as e:
            assert "reddetti" in str(e), str(e)


def test_mcp_hata_TURLERI_korunur():
    """
    Yetki dususu `YetkiHatasi`, baglayicinin oturumda hic olmamasi
    `BaglayiciYokHatasi` (YetkiHatasi alt sinifi), okumada zaman asimi
    `UlasilamadiHatasi` (yeniden denenebilir), YAZMADA zaman asimi
    `DurumBilinmiyorHatasi` (istek ulasmis olabilir, yeniden deneme yasak).
    """
    from finagent.ibkr.istemci import (DurumBilinmiyorHatasi, UlasilamadiHatasi,
                                       YetkiHatasi)
    from finagent.ibkr.mcp_kanal import BaglayiciYokHatasi

    def _bekle(sinif, arac, arg, **kw):
        try:
            _mcp_cagir(arac, arg, **kw)
            raise AssertionError(f"{sinif.__name__} beklendi")
        except sinif as e:
            return e

    _bekle(YetkiHatasi, "get_account_positions", None,
           _sorgu=_sahte_sorgu(hata="HTTP 401 Unauthorized: token expired"))
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:          # gercek ~/.claude'a BAKILMAZ
        e = _bekle(BaglayiciYokHatasi, "get_account_positions", None,
                   _sorgu=_sahte_sorgu(bulundu=False),
                   _onbellek_yolu=Path(d) / "yok.json")
    assert "claude.ai" in str(e) and e.onbellek_kaydi is None
    e = _bekle(UlasilamadiHatasi, "get_account_positions", None, sure_sn=0.2,
               _sorgu=_sahte_sorgu(yanit="{}", bekle_sn=1.0))
    assert not isinstance(e, DurumBilinmiyorHatasi)
    e = _bekle(DurumBilinmiyorHatasi, "create_alert",
               {"symbol": "QCOM", "condition_type": "LAST", "operator": "LTE",
                "value": 180.0, "contract_id": 273544}, sure_sn=0.2,
               _sorgu=_sahte_sorgu(yanit="{}", bekle_sn=1.0))
    assert "ULASMIS OLABILIR" in str(e)


def _sahte_varlik(eslesenler=None, bicim_bozuk=False, cagirma=False):
    """ToolSearch `select:` akisini taklit eder; ham sonuc OLCULEN bicimde."""
    async def q(prompt, options):
        async for m in prompt:
            ilk = m["message"]["content"]
            break
        adlar = ilk.split("select:")[1].split()[0].split(",")
        if not cagirma:
            yanit = ("bozuk" if bicim_bozuk else
                     {"matches": adlar if eslesenler is None else eslesenler,
                      "query": "select:...", "total_deferred_tools": 239})
            for e in options.hooks.get("PostToolUse", []):
                if e.matcher == "ToolSearch":
                    for h in e.hooks:
                        await h({"tool_name": "ToolSearch", "tool_response": yanit}, "t", None)
        yield _McpMesaj([TextBlock("TAMAM")])
        yield ResultMessage()
    return q


def test_mcp_arac_varligi_EKSIK_araci_ve_BICIM_bozuklugunu_soyler():
    """
    Faz 0.6: IBKR baglayicisi araclari haber vermeden degistirebilir.
    Kontrol hicbir IBKR aracini cagirmaz; ToolSearch'un HAM sonucuna bakar.
    Baglayici tamamen duserse eslesme bos doner -> 12'si de eksik.
    """
    import anyio
    from finagent.ibkr import mcp_kanal as K
    v = anyio.run(lambda: K.arac_varligi_async(_sorgu=_sahte_varlik()))
    assert v["eksik"] == [] and len(v["bulunan"]) == 15
    kayip = [K.ONEK + a for a in K.SECILEN if a != "create_alert"]
    v = anyio.run(lambda: K.arac_varligi_async(_sorgu=_sahte_varlik(eslesenler=kayip)))
    assert v["eksik"] == [K.ONEK + "create_alert"]
    v = anyio.run(lambda: K.arac_varligi_async(_sorgu=_sahte_varlik(eslesenler=[])))
    assert len(v["eksik"]) == 15, "baglayici dustugunde HEPSI eksik gorunmeli"
    for kw, sinif in (({"cagirma": True}, K.McpAracCagrilmadi),
                      ({"bicim_bozuk": True}, K.McpYanitBicimi)):
        try:
            anyio.run(lambda: K.arac_varligi_async(_sorgu=_sahte_varlik(**kw)))
            raise AssertionError(f"{sinif.__name__} beklendi")
        except sinif:
            pass


def _sahte_ikisi(pozisyon_yaniti, varlik=True, patla=False):
    """Gece gozlemi iki cagri yapar: varlik (ToolSearch) + okuma (arac)."""
    arac = _sahte_sorgu(yanit=pozisyon_yaniti)
    var = _sahte_varlik()

    def q(prompt, options):
        if patla:
            raise RuntimeError("SDK patladi")
        if "PostToolUse" in options.hooks and \
                options.hooks["PostToolUse"][0].matcher == "ToolSearch":
            return var(prompt, options)
        return arac(prompt, options)
    return q


def test_mcp_gece_gozlemi_ESLESMEYI_olcer_ve_ASLA_patlamaz():
    """
    Faz 0.3: gozlem nabizdan sonra kosar ve SESSIZDIR. Uc durum ayrilir:
    eslesiyor (True), eslesmiyor (False), bilinmiyor (None — kanallardan
    biri dustu). "Bilinmiyor" asla "eslesmiyor" diye yazilmaz. Ve SDK
    patlasa bile istisna disari sizmaz.
    """
    import json, tempfile
    from finagent.ibkr.mcp_gozlem import gece_gozlemi, ozet
    yanit = json.dumps({"positions": [{"contract_id": 273544, "position": 0.12}]})
    with tempfile.TemporaryDirectory() as d:
        yol = Path(d) / "g.jsonl"
        k = gece_gozlemi(None, yol, _sorgu=_sahte_ikisi(yanit),
                         _cpgw=lambda s: [[273544, 0.12]])
        assert k["eslesme"] is True and k["varlik"]["eksik"] == []
        assert k["okuma"]["pozisyon"] == [[273544, 0.12]]
        k = gece_gozlemi(None, yol, _sorgu=_sahte_ikisi(yanit),
                         _cpgw=lambda s: [[273544, 0.5]])
        assert k["eslesme"] is False

        def _cpgw_dustu(s):
            raise RuntimeError("401")
        k = gece_gozlemi(None, yol, _sorgu=_sahte_ikisi(yanit), _cpgw=_cpgw_dustu)
        assert k["eslesme"] is None and k["cpgw"]["hata"] == "RuntimeError"
        k = gece_gozlemi(None, yol, _sorgu=_sahte_ikisi(yanit, patla=True),
                         _cpgw=lambda s: [[273544, 0.12]])
        assert k["eslesme"] is None and k["okuma"]["hata"] == "RuntimeError"
        assert k["varlik"]["hata"] == "RuntimeError"
        satirlar = yol.read_text().splitlines()
        assert len(satirlar) == 4
        # Elle kosumlar ozete GIRMEZ (kabul olcutu launchd baglami).
        assert ozet(yol)["gece_kosusu"] == 0


def test_mcp_gozlemi_nabizdan_SONRA_ZARARSIZ_ve_kip_karari_AYARDA():
    """
    Kablo: gozlem gercekten gece zincirinde. Zararsizlik: nabiz satirindan
    SONRA, `|| true` ile — cokse bile kosu cikis kodu degismez. Ve depo
    kurali: betik kip ADIYLA dallanmaz; hangi kipte olculecegi
    `ritim.kipler.<kip>.mcp_gozlem` ayarinda. Yalnizca nabiz acik.
    """
    import copy
    from finagent.config import load_settings
    metin = (KOK / "scripts" / "run_kosu.sh").read_text(encoding="utf-8")
    kod = "\n".join(s for s in metin.splitlines() if not s.lstrip().startswith("#"))
    nabiz = kod.index('run.py nabiz --kip "$KIP"')
    satir = next(s for s in kod.splitlines() if "run.py mcp-gozlem" in s)
    assert kod.index("run.py mcp-gozlem") > nabiz, "gozlem nabizdan ONCE kosuyor"
    assert '--kip "$KIP"' in satir and satir.rstrip().endswith("|| true"), satir
    s = load_settings()
    acik = [k for k in s.ritim_kipleri if s.ritim_kip(k).get("mcp_gozlem")]
    assert acik == ["nabiz"], acik
    s.raw = copy.deepcopy(s.raw)
    s.raw["ritim"]["kipler"]["nabiz"]["mcp_gozlem"] = "evet"
    try:
        s.ritim_kip("nabiz")
        raise AssertionError("bool olmayan mcp_gozlem kabul edildi")
    except ValueError:
        pass



def test_mcp_BAGLAYICI_YOK_mesaji_iki_sebebi_AYIRIR_ve_dogru_cozumu_soyler():
    """
    OLCULDU 2026-09-25 (Faz 0.5): baglanti kesilip geri baglandiginda
    baglayici GERI GELMEDI. Claude Code, kesinti sirasinda IBKR'yi
    `~/.claude/mcp-needs-auth-cache.json`a "yetki gerekiyor" diye yazmisti;
    kayit durdukca yeni oturumlar IBKR'ye hic baglanmiyor. Eski mesaj
    "yeniden baglayin" diyordu — bu durumda ISE YARAMAYAN tek oneri.

    Uc hal, uc mesaj:
      kayit var       -> satiri silmek (yeniden baglamak COZMEZ)
      kayit yok       -> claude.ai'dan yeniden baglamak
      dosya okunamadi -> ikisini de soyle, hangisi oldugunu UYDURMA
    """
    from finagent.ibkr.mcp_kanal import AUTH_ANAHTARI, baglayici_yok_mesaji
    kayit = {"timestamp": 1_000_000, "id": "mcpsrv_x"}
    m = baglayici_yok_mesaji("get_account_positions", kayit, None,
                             simdi_ms=1_000_000 + 12 * 60000)
    assert "12 dk once" in m and AUTH_ANAHTARI in m and "silinmesi" in m
    assert "COZMEZ" in m, "yeniden baglamanin ise yaramadigi soylenmeli"
    m = baglayici_yok_mesaji("get_account_positions", None, None)
    assert "yeniden baglayin" in m and "silin" not in m
    m = baglayici_yok_mesaji("get_account_positions", None, "okunamadi: ValueError")
    assert "bilinemedi" in m and "yeniden baglayin" in m and "silinmeli" in m


def test_mcp_auth_onbellegi_YALNIZCA_OKUNUR_ve_okunamamak_KAYIT_YOK_sayilmaz():
    import json, os, tempfile
    from finagent.ibkr.mcp_kanal import AUTH_ANAHTARI, auth_onbellek_kaydi
    with tempfile.TemporaryDirectory() as d:
        yol = Path(d) / "c.json"
        assert auth_onbellek_kaydi(yol) == (None, None), "dosya yok = kayit yok"
        yol.write_text("{bozuk")
        k, n = auth_onbellek_kaydi(yol)
        assert k is None and n and n.startswith("okunamadi"), \
            "okunamayan dosya 'kayit yok' sayilirsa yanlis cozum onerilir"
        yol.write_text(json.dumps({AUTH_ANAHTARI: {"timestamp": 5, "id": "x"},
                                   "claude.ai Canva": {"timestamp": 6}}))
        once = (yol.stat().st_mtime_ns, yol.read_text())
        k, n = auth_onbellek_kaydi(yol)
        assert k == {"timestamp": 5, "id": "x"} and n is None
        assert (yol.stat().st_mtime_ns, yol.read_text()) == once, \
            "Claude Code'un ic dosyasina YAZILDI"


def test_mcp_BAGLAYICI_YOK_kanaldan_SEBEBIYLE_cikar_ve_gozleme_GIRER():
    """
    Kablo: kanal onbellegi okuyup hataya koyuyor, gece gozlemi de onu
    satira yaziyor — kaydin kendiliginden dusme suresi uretimde olculsun.
    """
    import json, tempfile
    from finagent.ibkr.mcp_kanal import AUTH_ANAHTARI, BaglayiciYokHatasi
    from finagent.ibkr.mcp_gozlem import _hata
    with tempfile.TemporaryDirectory() as d:
        yol = Path(d) / "c.json"
        yol.write_text(json.dumps({AUTH_ANAHTARI: {"timestamp": 7, "id": "x"}}))
        try:
            _mcp_cagir("get_account_positions", None,
                       _sorgu=_sahte_sorgu(bulundu=False), _onbellek_yolu=yol)
            raise AssertionError("BaglayiciYokHatasi beklendi")
        except BaglayiciYokHatasi as e:
            assert e.onbellek_kaydi == {"timestamp": 7, "id": "x"}
            assert "silinmesi" in str(e)
            h = _hata(e)
            assert h["auth_onbellek_kaydi"] == {"timestamp": 7, "id": "x"}
            assert "silinmesi" in h["mesaj"], "mesaj gozlemde KIRPILMIS"


# ---------------------------------------------------------------------------
# FAZ 1 — YEDEK OKUMA KANALI (2026-09-25)
#
# CPGW okunamayinca portfoy toplayicisi ve sohbet IBKR'yi bulut
# baglayicisindan okur ve SOYLER. Karar kodda; CPGW saglamken hicbir sey
# degismez. Ve 0.7: claude.ai baglayicilari model oturumlarindan GIZLI.
# ---------------------------------------------------------------------------

def test_faz1_IKI_KANAL_ayni_pozisyondan_BIREBIR_ayni_satiri_uretir():
    """
    Asagi akis (db, mesaj, arac) kanali BILMEMELI. Ayni pozisyon CPGW
    bicimiyle de baglayici bicimiyle de gelse ayni satir yazilmali; getiri
    yuzdesi ayni formulden (`pnl_yuzde`). Ayrisirsa ayni hisse iki ayri
    getiri gosterir.
    """
    from finagent.ibkr.portfoy import Portfoy, mcp_nakit, mcp_pozisyonlari
    from finagent.collectors.ibkrportfoy import satirlari_kur

    class _Ist:
        def get(self, yol):
            if yol == "/portfolio/accounts":
                return [{"accountId": "U1", "currency": "EUR"}]
            if yol.startswith("/portfolio2/"):
                return [{"conid": 273544, "description": "QCOM", "position": 0.12,
                         "avgCost": 167.97, "marketPrice": 196.4, "marketValue": 23.568,
                         "unrealizedPnl": 3.41, "currency": "USD", "assetClass": "STK"},
                        {"conid": 1, "description": "KAPALI", "position": 0}]
            if yol.endswith("/ledger"):
                return {"BASE": {"cashbalance": 101.3},
                        "EUR": {"cashbalance": 3.38}, "USD": {"cashbalance": 111.71}}
            raise AssertionError(yol)
    p = Portfoy(_Ist())
    cpgw = satirlari_kur(p.pozisyonlar("U1"), p.nakit("U1"), "EUR")
    mcp_veri = {"positions": [
        {"contract_id": 273544, "contract_description": "QCOM", "position": 0.12,
         "market_price": 196.4, "market_value": 23.568, "currency": "USD",
         "average_price": 167.97, "unrealized_pnl": 3.41, "asset_class": "STK"},
        {"contract_id": 1, "contract_description": "KAPALI", "position": 0}]}
    bak = {"balances": [{"currency": "BASE", "cash_balance": 101.3},
                        {"currency": "EUR", "cash_balance": 3.38},
                        {"currency": "USD", "cash_balance": 111.71}]}
    mcp = satirlari_kur(mcp_pozisyonlari(mcp_veri), mcp_nakit(bak), "EUR")
    assert cpgw == mcp, f"\nCPGW {cpgw}\nMCP  {mcp}"
    semboller = sorted(r["symbol"] for r in mcp[0])
    assert semboller == ["CASH", "CASH.USD", "QCOM"], "BASE/sifir adet sizmis"


def test_faz1_gercek_baglayici_yanitlari_ESLENIYOR():
    """Faz 0'da yakalanan GERCEK bicim (anonim degerler) eslenebiliyor."""
    from finagent.ibkr.mcp_kanal import ham_ayristir
    from finagent.ibkr.portfoy import mcp_nakit, mcp_pozisyonlari
    poz = mcp_pozisyonlari(ham_ayristir(_mcp_fikstur("get_account_positions")))
    assert len(poz) == 1 and poz[0].sembol == "QCOM" and poz[0].conid == "273544"
    assert poz[0].pnl_pct is not None
    nk = mcp_nakit(ham_ayristir(_mcp_fikstur("get_account_balances")))
    assert set(nk) == {"EUR", "USD"}, "BASE bir para birimi degil"


def test_faz1_yedek_ayari_ACIKCA_yazilir_ve_bool_olmali():
    import copy
    from finagent.config import load_settings
    from finagent.ibkr.yedek import yedek_acik
    s = load_settings()
    assert yedek_acik(s) is True, "canli ayar acik olmali (Ali'nin karari)"
    s.raw = copy.deepcopy(s.raw)
    del s.raw["ibkr"]["mcp_yedek"]
    assert yedek_acik(s) is False, "anahtar yokken yeni kanal SESSIZCE acilmamali"
    s.raw["ibkr"]["mcp_yedek"] = "evet"
    try:
        yedek_acik(s)
        raise AssertionError("bool olmayan deger kabul edildi")
    except ValueError:
        pass


def test_faz1_CPGW_yoklamasi_DOGRU_KATMANA_bakar_bos_listeyi_OKUNUR_saymaz():
    from finagent.ibkr.istemci import UlasilamadiHatasi, YetkiHatasi
    from finagent.ibkr.yedek import cpgw_okuma_durumu

    def _ist(sonuc):
        class I:
            istenen = []
            def get(self, yol):
                I.istenen.append(yol)
                if isinstance(sonuc, Exception):
                    raise sonuc
                return sonuc
            def kapat(self):
                pass
        return I()
    i = _ist([{"accountId": "U1", "currency": "EUR"}])
    assert cpgw_okuma_durumu(None, _istemci=i)[0] is True
    assert i.istenen == ["/portfolio/accounts"], \
        "olcu /iserver degil /portfolio olmali (iki katmanli oturum)"
    assert cpgw_okuma_durumu(None, _istemci=_ist(YetkiHatasi("401")))[0] is False
    assert cpgw_okuma_durumu(None, _istemci=_ist(UlasilamadiHatasi("x")))[0] is False
    ok, sebep = cpgw_okuma_durumu(None, _istemci=_ist([]))
    assert ok is False and "bos" in sebep, "bos liste 'okunur' sayilmamali"


def test_faz1_yoklama_ONBELLEGI_hiz_sinirini_korur_ve_BOZUKTA_yeniden_yoklar():
    """
    `/portfolio/accounts` 5 sn'de 1 istek; her sohbet mesaji ayri surecte.
    60 sn icinde ikinci yoklama YAPILMAMALI; sure dolunca ya da dosya
    bozuksa yapilmali (onbellek hiz icin, dogruluk icin degil).
    """
    import tempfile
    from finagent.ibkr.yedek import cpgw_okuma_durumu_onbellekli as ob
    sayac = []

    def yokla(s):
        sayac.append(1)
        return False, "CPGW giris istiyor (401)"
    with tempfile.TemporaryDirectory() as d:
        yol = Path(d) / "c.json"
        assert ob(None, yol, _yokla=yokla, _simdi=1000.0) == (False, "CPGW giris istiyor (401)")
        r = ob(None, yol, _yokla=yokla, _simdi=1030.0)
        assert r[0] is False and "(onbellek)" in r[1] and len(sayac) == 1
        ob(None, yol, _yokla=yokla, _simdi=1061.0)
        assert len(sayac) == 2, "sure dolunca yeniden yoklanmali"
        yol.write_text("{bozuk")
        ob(None, yol, _yokla=yokla, _simdi=1062.0)
        assert len(sayac) == 3, "bozuk onbellek yoklamayi engellememeli"


def _faz1_toplayici(yedek: bool):
    import copy, tempfile
    from finagent.config import load_settings
    from finagent.storage.db import Database
    from finagent.collectors.ibkrportfoy import IbkrPortfoyCollector
    s = load_settings(); s.raw = copy.deepcopy(s.raw)
    s.raw["ibkr"]["mcp_yedek"] = yedek
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "t.db"); db.init_schema()
    return IbkrPortfoyCollector(s, db, browser=None), db


def test_faz1_toplayici_CPGW_dusunce_bulut_ile_YAZAR_ve_KANALI_soyler():
    from finagent.ibkr.mcp_kanal import ham_ayristir
    from finagent.ibkr.portfoy import mcp_nakit, mcp_pozisyonlari
    from finagent.ibkr.yedek import McpPortfoy
    mp = McpPortfoy(
        pozisyonlar=mcp_pozisyonlari(ham_ayristir(_mcp_fikstur("get_account_positions"))),
        nakit=mcp_nakit(ham_ayristir(_mcp_fikstur("get_account_balances"))),
        taban_pb="EUR")
    c, db = _faz1_toplayici(True)
    r = c._yedek_ya_da_atla("ali", "gateway calismiyor: baglanti reddedildi",
                            _mcp_portfoy=lambda: mp)
    assert r.status == "partial" and r.rows == 3, (r.status, r.rows, r.error)
    assert "kanal=mcp" in r.error and "gateway calismiyor" in r.error
    s = sorted(x["symbol"] for x in db.latest_positions("ibkr", "ali"))
    assert s == ["CASH", "CASH.USD", "QCOM"], s


def test_faz1_toplayici_yedek_KAPALIYKEN_atlar_DUSERSE_iki_sebebi_de_soyler():
    from finagent.ibkr.mcp_kanal import BaglayiciYokHatasi
    from finagent.ibkr.yedek import McpPortfoy
    c, db = _faz1_toplayici(False)
    r = c._yedek_ya_da_atla("ali", "giris yapilmamis",
                            _mcp_portfoy=lambda: (_ for _ in ()).throw(AssertionError("cagrilmamali")))
    assert r.status == "skipped" and r.error == "giris yapilmamis"
    c, db = _faz1_toplayici(True)

    def _dus():
        raise BaglayiciYokHatasi("claude.ai'dan yeniden baglayin")
    r = c._yedek_ya_da_atla("ali", "giris yapilmamis", _mcp_portfoy=_dus)
    assert r.status == "skipped" and "giris yapilmamis" in r.error \
        and "BaglayiciYokHatasi" in r.error and "yeniden baglayin" in r.error
    r = c._yedek_ya_da_atla("ali", "x", _mcp_portfoy=lambda: McpPortfoy([], {}, None))
    assert r.status == "error" and "taban para birimi" in r.error
    assert db.latest_positions("ibkr", "ali") == []


def test_faz1_sohbet_karari_YALNIZCA_CPGW_okunamazken_acar():
    import copy
    from finagent.config import load_settings
    from finagent.bot.chat import ibkr_yedek_karari
    from finagent.ibkr.mcp_kanal import OKUMA_ARACLARI
    s = load_settings()
    assert ibkr_yedek_karari(s, _durum=lambda x: (True, "CPGW okunabilir")) == (False, "")
    acik, notu = ibkr_yedek_karari(s, _durum=lambda x: (False, "CPGW giris istiyor (401)"))
    assert acik and "401" in notu and "BELIRT" in notu and "EMIR verilemez" in notu
    assert all(a in notu for a in OKUMA_ARACLARI)
    s.raw = copy.deepcopy(s.raw)
    s.raw["ibkr"]["mcp_yedek"] = False
    assert ibkr_yedek_karari(s, _durum=lambda x: (False, "x"))[0] is False
    s.raw["ibkr"]["mcp_yedek"] = "evet"
    assert ibkr_yedek_karari(s, _durum=lambda x: (False, "x"))[0] is False, \
        "bozuk ayar sohbeti dusurmemeli, yedek kapali sayilmali"


def test_faz1_sohbet_KABLOSU_karar_arac_listesine_ve_gizlemeye_bagli():
    """
    Kablo: `_sor` karari cagiriyor, araclari listeye ekliyor ve gizlemeyi
    karara bagliyor. Yapisal (AST) — `_sor` gercek SDK olmadan kosturulamiyor.
    """
    import ast
    agac = ast.parse((KOK / "src/finagent/bot/chat.py").read_text(encoding="utf-8"))
    sor = next(n for n in ast.walk(agac)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_sor")
    cagrilar = [n for n in ast.walk(sor) if isinstance(n, ast.Call)]
    adlar = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in cagrilar}
    assert "ibkr_yedek_karari" in adlar, "karar _sor icinde cagrilmiyor"
    kaynak = ast.unparse(sor)
    assert "araclar += list(OKUMA_ARACLARI)" in kaynak
    sdk = [c for c in cagrilar if getattr(c.func, "id", None) == "sdk_ortami"]
    assert sdk and any(k.arg == "claudeai_baglayicilari" and
                       isinstance(k.value, ast.Name) and k.value.id == "ibkr_bulut"
                       for c in sdk for k in c.keywords), \
        "gizleme karara bagli degil: yedek modda IBKR de gizlenir"


def test_07_HER_model_oturumu_claudeai_baglayicilarini_GIZLER():
    """
    0.7: Bot guvenilmeyen metin okuyor; gomulu bir talimat kapi bozuldugu
    gun Gmail'den e-posta gonderebilirdi. Model araci hic gormezse kapi
    bozulsa da kullanamaz. `src` altindaki HER `ClaudeAgentOptions(` cagrisi
    `**sdk_ortami(...)` gecmeli — yeni bir cagri noktasi unutamaz. Istisna
    yalnizca IBKR'ye IHTIYAC duyan baglayici kanali.
    """
    import ast
    from finagent.llm import CLAUDEAI_BAGLAYICI_ENV, sdk_ortami
    assert sdk_ortami() == {"env": {CLAUDEAI_BAGLAYICI_ENV: "false"}}
    assert sdk_ortami(claudeai_baglayicilari=True) == {}
    istisna = {"src/finagent/ibkr/mcp_kanal.py"}
    eksik, sayi = [], 0
    for yol in sorted((KOK / "src").rglob("*.py")):
        goreli = str(yol.relative_to(KOK))
        for n in ast.walk(ast.parse(yol.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "ClaudeAgentOptions":
                if goreli in istisna:
                    continue
                sayi += 1
                if not any(k.arg is None and isinstance(k.value, ast.Call)
                           and getattr(k.value.func, "id", None) == "sdk_ortami"
                           for k in n.keywords):
                    eksik.append(f"{goreli}:{n.lineno}")
    assert sayi >= 9, f"cagri noktasi sayisi dustu ({sayi}) — tarama bozuk olabilir"
    assert not eksik, f"gizleme uygulanmayan model oturumu: {eksik}"


# ---------------------------------------------------------------------------
# FAZ 3 — GERCEK GETIRI KARNESI (2026-09-25)
# ---------------------------------------------------------------------------

def _faz3_db():
    import tempfile
    from finagent.storage.db import Database
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "t.db"); db.init_schema()
    return db


def test_faz3_gunluk_zincir_PA_kumulatifini_YENIDEN_URETIR():
    """
    Kabul olcutu: saklanan gunluk getiriler carpildiginda PA'nin kendi
    kumulatif getirisi cikmali (sahada 3e-16). Fikstur gercek bicim, anonim
    degerler — zincir kurali degerden bagimsiz.
    """
    import json
    from finagent.ibkr.getiri import pa_gunluk, _zincir
    veri = json.loads(_mcp_fikstur("get_pa_performance_all_periods"))
    pa = pa_gunluk(veri)
    assert pa["donem"] == "1Y" and pa["olcu"] == "TWR" and pa["para_birimi"] == "EUR"
    cps = veri["accounts"]["account"]["periods"]["1Y"]["cps"]
    assert abs(_zincir([r for _, _, r in pa["satirlar"]]) - cps[-1]) < 1e-9
    assert pa["satirlar"][0][0] == "2026-08-24", "tarih YYYY-MM-DD'ye cevrilmeli"
    bozuk = json.loads(json.dumps(veri))
    bozuk["accounts"]["account"]["periods"]["1Y"]["nav"].pop()
    for b in (bozuk, {"accounts": {"a": {}, "b": {}}}, {"accounts": {"a": {"periods": {}}}}):
        try:
            pa_gunluk(b)
            raise AssertionError("bozuk PA yaniti kabul edildi")
        except ValueError:
            pass


def test_faz3_gecmis_gun_REVIZYONU_sayilir_ve_son_deger_kalir():
    """Sahada olculdu: ayni gunun noktasi iki cekiliste farkliydi."""
    from finagent.ibkr.getiri import yaz
    db = _faz3_db()
    pa = {"olcu": "TWR", "para_birimi": "EUR",
          "satirlar": [("2026-09-24", 100.0, 0.01), ("2026-09-25", 101.0, -0.000164)]}
    assert yaz(db, "ibkr", pa) == {"yeni": 2, "revize": []}
    pa["satirlar"][1] = ("2026-09-25", 101.0, -0.000140)
    s = yaz(db, "ibkr", pa)
    assert s["yeni"] == 0 and len(s["revize"]) == 1 and s["revize"][0][0] == "2026-09-25"
    assert db.query("SELECT gunluk FROM hesap_getirisi WHERE tarih='2026-09-25'")[0][0] == -0.000140
    assert yaz(db, "ibkr", pa)["revize"] == [], "tolerans icindeki ayni deger revizyon degil"


def _faz3_seri(db, hesap_bas="2026-08-24", gun=25, gunluk=0.005, kiyas_bas="2025-12-31"):
    from datetime import date, timedelta
    from finagent.ibkr.getiri import yaz
    b = date.fromisoformat(hesap_bas)
    satirlar, t = [], b
    while len(satirlar) < gun:
        if t.weekday() < 5:
            satirlar.append((t.isoformat(), 120.0, gunluk))
        t += timedelta(days=1)
    yaz(db, "ibkr", {"olcu": "TWR", "para_birimi": "EUR", "satirlar": satirlar})
    iid = db.upsert_instrument("VUSA", "BUX", "Vanguard S&P 500", "etf", "EUR")
    k, t, fiyat = [], date.fromisoformat(kiyas_bas), 100.0
    while t.isoformat() <= satirlar[-1][0]:
        if t.weekday() < 5:
            k.append({"ts": t.isoformat(), "open": fiyat, "high": fiyat, "low": fiyat,
                      "close": fiyat, "volume": 1})
            fiyat *= 1.001
        t += timedelta(days=1)
    db.upsert_prices(iid, k, "yahoo", currency="EUR")
    return satirlar


def test_faz3_KIYAS_PENCERESI_hesabin_omruyle_sinirli():
    """
    SAHADA BULUNDU (25 Eyl): 24 Agu'da acilan hesap, "yilbasindan" satirinda
    BUTUN YILIN VUSA getirisiyle (+%15,08) kiyaslaniyordu. Kiyas ve hesap
    ayni gunden olculmeli; hesap yilbasindan sonra acildiysa "yilbasindan"
    GOSTERILMEZ.
    """
    from datetime import date
    from finagent.ibkr.getiri import ozet
    db = _faz3_db()
    satirlar = _faz3_seri(db)
    o = ozet(db, "ibkr")
    assert "yilbasi" not in o["donemler"], "hesap yilbasindan sonra acildi"
    b = o["donemler"]["baslangic"]
    # VUSA gunluk %0,1: hesap omru boyunca (23 Agu -> son gun) as-of oran
    gun_sayisi = sum(1 for s in satirlar)
    beklenen = (1.001 ** gun_sayisi - 1) * 100
    assert abs(b["kiyas_%"] - beklenen) < 0.2, (b, beklenen)
    assert b["kiyas_%"] < 5, "kiyas yilbasindan olculuyor (~%20 olurdu)"


def test_faz3_KANIT_DEGIL_uyarisi_ve_TURKCE_mesaj():
    from finagent.ibkr.getiri import mesaj, ozet
    db = _faz3_db()
    _faz3_seri(db)
    m = mesaj(ozet(db, "ibkr"))
    assert "IBKR gerçek getiri" in m and "başlangıçtan" in m and "gercek" not in m, m
    assert "kanıt değil" in m and "25 gün" in m
    assert mesaj({"donemler": {}}) is None, "veri yoksa mesaj GITMEZ"


def test_faz3_haftalik_mesaj_GUN_KIP_SAHIP_ayardan_ve_NABZI_DUSURMEZ():
    import copy
    from datetime import date
    from finagent.config import load_settings
    from finagent.pulse.runner import Nabiz
    db = _faz3_db()
    _faz3_seri(db)
    s = load_settings(); s.raw = copy.deepcopy(s.raw)
    s.raw["ibkr"]["sahip"] = "ali"
    n = Nabiz(s, db)
    giden = []
    n._sahibe_bildir = lambda h, m, **kw: giden.append((h, m))
    cuma, persembe = date(2026, 9, 25), date(2026, 9, 24)
    assert n._getiri_karnesi_gonder("nabiz", ["ali"], bugun=persembe) is None
    assert n._getiri_karnesi_gonder("sabah", ["ali"], bugun=cuma) is None, "yanlis kip"
    assert n._getiri_karnesi_gonder("nabiz", ["yuksel"], bugun=cuma) is None, \
        "hesap sahibi olmayana gitmemeli"
    m = n._getiri_karnesi_gonder("nabiz", ["ali", "yuksel"], bugun=cuma)
    assert m and giden == [("ali", m)]
    s.raw["sources"]["ibkrgetiri"]["mesaj_gunu"] = "cuma"
    assert n._getiri_karnesi_gonder("nabiz", ["ali"], bugun=cuma) is None
    s.raw["sources"]["ibkrgetiri"]["mesaj_gunu"] = 4
    n.db = None                                       # ozet patlasin
    assert n._getiri_karnesi_gonder("nabiz", ["ali"], bugun=cuma) is None, \
        "hata nabzi DUSURMEMELI"


def test_faz3_toplayici_hatayi_TURUYLE_soyler_ve_KABLOSU_bagli():
    import ast, json
    from finagent.config import load_settings
    from finagent.collectors.ibkrgetiri import IbkrGetiriCollector
    from finagent.ibkr.mcp_kanal import BaglayiciYokHatasi, McpSonuc
    db = _faz3_db()
    c = IbkrGetiriCollector(load_settings(), db, browser=None)
    veri = json.loads(_mcp_fikstur("get_pa_performance_all_periods"))
    r = c.collect(_cagir=lambda a: McpSonuc(a, {}, veri, "", 1.0))
    assert r.status == "ok" and r.rows == 25, (r.status, r.error)

    def _dus(a):
        raise BaglayiciYokHatasi("claude.ai'dan yeniden baglayin")
    r = c.collect(_cagir=_dus)
    assert r.status == "error" and "BaglayiciYokHatasi" in r.error
    r = c.collect(_cagir=lambda a: McpSonuc(a, {}, {"accounts": {}}, "", 1.0))
    assert r.status == "error" and "okunamadi" in r.error
    s = load_settings()
    assert "ibkrgetiri" in s.ritim_kip("nabiz")["kaynaklar"]
    agac = ast.parse((KOK / "src/finagent/pulse/runner.py").read_text(encoding="utf-8"))
    cal = next(n for n in ast.walk(agac) if isinstance(n, ast.FunctionDef) and n.name == "calistir")
    assert any(getattr(x.func, "attr", None) == "_getiri_karnesi_gonder"
               for x in ast.walk(cal) if isinstance(x, ast.Call)), \
        "haftalik mesaj nabiz akisina bagli degil"


# ---------------------------------------------------------------------------
# FAZ 4 — BILANCO ONCESI FIYATLANAN HAREKET (2026-09-25)
# ---------------------------------------------------------------------------

def test_faz4_VADE_bilancoyu_KAPSAR_ve_turev_sinifi_SECMEZ():
    """
    Erken bir vade secilirse straddle bilancoyu hic icermez. Ve `trading_class`
    HER satirda dolu (Faz 0 olcumu): '2ASML' gibi siniflar AYRI kontrat.
    """
    from finagent.ibkr.beklenti import BeklentiHesaplanamadi, en_gec_tepki, vade_sec
    assert en_gec_tepki(["2026-10-14"], "once") == "2026-10-14"
    assert en_gec_tepki(["2026-10-14"], None) == "2026-10-15", "saat bilinmiyorsa ertesi gun"
    assert en_gec_tepki(["2026-10-14"], "seans") == "2026-10-15"
    assert en_gec_tepki(["2026-10-16"], "sonra") == "2026-10-19", "Cuma sonrasi -> Pazartesi"
    assert en_gec_tepki(["2026-10-14", "2026-10-15"], "once") == "2026-10-15", "kaynaklar ayrisirsa en gec"
    p = {"expirations": [
        {"id": "a", "date": "20261009", "trading_class": "ASML"},
        {"id": "b", "date": "20261016", "trading_class": "2ASML"},
        {"id": "c", "date": "20261016", "trading_class": "ASML"},
        {"id": "d", "date": "20261023", "trading_class": "ASML"}]}
    assert vade_sec(p, "ASML", "2026-10-15")["id"] == "c"
    assert vade_sec(p, "ASML", "2026-10-16")["id"] == "c", "tepki gunu vadeye esitse kapsar"
    for par, sem in (({"expirations": [{"id": "x", "date": "20261016", "trading_class": "ASML"},
                                       {"id": "y", "date": "20261016", "trading_class": "ASML"}]}, "ASML"),
                     (p, "QCOM"), (p, "ASML")):
        try:
            vade_sec(par, sem, "2026-10-15" if par is not p or sem == "QCOM" else "2026-11-01")
            raise AssertionError("belirsiz/olmayan vade secildi")
        except BeklentiHesaplanamadi:
            pass


def test_faz4_opsiyon_fiyati_ORTA_once_yoksa_YALNIZCA_kapanis_islemi():
    """
    OLCULDU: acilis oncesi ATM ASML opsiyonlarinda bid-ask BOS, last dolu ve
    is_close. is_close OLMAYAN son islem bayat olabilir -> kabul edilmez.
    """
    from finagent.ibkr.beklenti import opsiyon_fiyati
    assert opsiyon_fiyati({"bid-ask": {"bid": 7.85, "ask": 8.5}, "last": {"price": 9, "is_close": True}}) \
        == ((7.85 + 8.5) / 2, "orta")
    assert opsiyon_fiyati({"bid-ask": {}, "last": {"price": 73.55, "is_close": True}}) == (73.55, "kapanis_islemi")
    assert opsiyon_fiyati({"bid-ask": {}, "last": {"price": 73.55, "is_close": False}}) == (None, None)
    assert opsiyon_fiyati({"bid-ask": {"bid": 0, "ask": 1}, "last": {}}) == (None, None)
    assert opsiyon_fiyati({"bid-ask": {"bid": 2, "ask": 1}}) == (None, None), "ters kotasyon"


def _faz4_sahte_cagir(kayit):
    """Faz 0'da yakalanan GERCEK QCOM yanitlari; argumanlar kaydedilir."""
    import json
    from finagent.ibkr.mcp_kanal import McpSonuc, ham_ayristir
    zincir = ham_ayristir(_mcp_fikstur("get_option_data"))
    call_ids = {int(r["call_contract_id"]) for r in zincir["contracts"]}

    def cagir(arac, arg):
        kayit.append((arac, arg))
        if arac == "get_option_parameters":
            v = ham_ayristir(_mcp_fikstur("get_option_parameters"))
        elif arac == "get_option_data":
            v = zincir
        elif arg["contract_id"] == 273544:
            v = ham_ayristir(_mcp_fikstur("get_price_snapshot_hisse"))
        elif arg["contract_id"] in call_ids:
            v = ham_ayristir(_mcp_fikstur("get_price_snapshot_call"))
        else:
            v = ham_ayristir(_mcp_fikstur("get_price_snapshot_put"))
        return McpSonuc(arac, arg, v, "", 1.0)
    return cagir


def test_faz4_hesap_GERCEK_yanitlarla_ve_ARGUMANLAR_kapiya_uygun():
    """
    Strike sinirlari TAM SAYI: kapi argumani birebir karsilastiriyor; model
    176.76'yi yuvarlarsa cagri reddedilirdi. Hareket = (call + put) / fiyat.
    """
    from finagent.ibkr.beklenti import hesapla
    kayit = []
    # Tepki gunu 12 Eki (Pzt): 09 Eki vadesi ONCE doluyor, bilancoyu
    # kapsamaz -> ilk kapsayan 16 Eki.
    s = hesapla(273544, "QCOM", "2026-10-12", _faz4_sahte_cagir(kayit))
    araclar = [a for a, _ in kayit]
    assert araclar == ["get_option_parameters", "get_price_snapshot", "get_option_data",
                       "get_price_snapshot", "get_price_snapshot"], araclar
    zarg = kayit[2][1]
    assert isinstance(zarg["min_strike"], int) and isinstance(zarg["max_strike"], int), zarg
    assert zarg["expiration_id"].endswith("/20261016/QCOM/1"), "ilk KAPSAYAN vade (09 Eki degil)"
    assert s["strike"] == 197.5 and s["vade"] == "20261016"
    assert abs(s["hareket_pct"] - (s["call_orta"] + s["put_orta"]) / s["fiyat"] * 100) < 1e-3  # 3 ondalik yuvarlama
    assert s["fiyat_kaynagi"] == "orta" and s["veri_durumu"] == "FROZEN_DELAYED"


def test_faz4_GERCEKLESEN_tepki_gunu_kapanistan_olculur():
    from finagent.ibkr.beklenti import gerceklesen
    k = [("2026-10-13", 100.0), ("2026-10-14", 104.0), ("2026-10-15", 91.0)]
    assert gerceklesen(k, "2026-10-15") == 12.5, "|91/104 - 1| = %12,5 (mutlak)"
    assert gerceklesen(k, "2026-10-16") is None, "bar yoksa 'henuz yok' — sifir DEGIL"
    assert gerceklesen(k, "2026-10-13") is None, "onceki kapanis yoksa olculemez"


def _faz4_kurulum(bilancolar):
    """Portfoyde hisseler, conid'leri ve taze bilanco takvimi."""
    import copy, tempfile, datetime as _dt
    from finagent.config import load_settings
    from finagent.storage.db import Database
    from finagent.collectors.bilancotakvim import yaz
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "t.db"); db.init_schema()
    bugun = _dt.date.today()
    for i, (sem, gun) in enumerate(bilancolar):
        iid = db.upsert_instrument(sem, "BUX", sem + " Inc", "equity", "USD")
        db.query("INSERT INTO positions (sahip, snapshot_ts, account, instrument_id, quantity) "
                 "VALUES ('ali', ?, 'bux', ?, 1)", (_dt.datetime.now().isoformat(timespec="seconds"), iid))
        db.query("INSERT INTO identities (instrument_id, conid, status) VALUES (?, ?, 'dogrulandi')",
                 (iid, 273544 if sem == "QCOM" else 1000 + i))
        yaz(db, [{"instrument_id": iid, "tarih": (bugun + _dt.timedelta(days=gun)).isoformat(),
                  "zaman": "once"}], "alphavantage")
    db._conn.commit()
    s = load_settings(); s.raw = copy.deepcopy(s.raw)
    return s, db


def test_faz4_toplayici_BUTCEYI_asmaz_AYNI_GUN_tekrar_olcmez_ve_ERTELENENI_soyler():
    """
    Bilanco sezonunda portfoyun yarisi ayni hafta aciklayabilir; hisse basina
    ~60 sn. Gece basina en fazla `azami_sembol`, en yakin once; ertelenen
    SOYLENIR. Ayni gun ikinci kosu tekrar olcmez.
    """
    from finagent.collectors.bilancobeklenti import BilancoBeklentiCollector
    s, db = _faz4_kurulum([("QCOM", 2), ("AAA", 3), ("BBB", 4), ("CCC", 5), ("DDD", 6), ("EEE", 30)])
    s.raw["sources"]["bilancobeklenti"]["azami_sembol"] = 2
    kayit = []
    c = BilancoBeklentiCollector(s, db, browser=None)
    r = c.collect(_cagir=_faz4_sahte_cagir(kayit))
    assert "olculen 1/2" in r.error, r.error              # AAA'nin conid'i sahte -> dustu degil, hesap
    assert "3 hisse sonraki geceye ertelendi" in r.error and "EEE" not in r.error, \
        "pencere disi (30 gun) hedef olmamali; en yakin 2 olculmeli"
    n1 = len(kayit)
    params = [b["underlying_contract_id"] for a, b in kayit if a == "get_option_parameters"]
    assert len(params) == 2, f"butce 2 hisse, baglayiciya {len(params)} hisse icin gidildi"
    r2 = c.collect(_cagir=_faz4_sahte_cagir(kayit))
    assert db.query("SELECT COUNT(*) FROM bilanco_beklentisi")[0][0] >= 1
    assert not any(a == "get_option_parameters" and b.get("underlying_contract_id") == 273544
                   for a, b in kayit[n1:]), "ayni gun QCOM ikinci kez olculdu"


def test_faz4_GERCEKLESEN_bilanco_sonrasi_AYNI_satira_yazilir():
    from finagent.collectors.bilancobeklenti import BilancoBeklentiCollector
    s, db = _faz4_kurulum([("QCOM", 2)])
    iid = db.query("SELECT id FROM instruments WHERE symbol='QCOM'")[0][0]
    db.query("""INSERT INTO bilanco_beklentisi (instrument_id, bilanco_tarih, olcum_gunu,
                tepki_gunu, hareket_pct) VALUES (?, '2026-01-14', '2026-01-10', '2026-01-14', 6.0)""", (iid,))
    db.upsert_prices(iid, [{"ts": t, "open": c, "high": c, "low": c, "close": c, "volume": 1}
                           for t, c in (("2026-01-13", 100.0), ("2026-01-14", 108.0))],
                     "yahoo", currency="USD")
    db._conn.commit()
    BilancoBeklentiCollector(s, db, browser=None).collect(_cagir=_faz4_sahte_cagir([]))
    g = db.query("SELECT gerceklesen_pct FROM bilanco_beklentisi WHERE bilanco_tarih='2026-01-14'")[0][0]
    assert g == 8.0, g


def test_faz4_KABLO_paket_istem_nabiz_ve_E1_filtresine_SIZMAZ():
    import ast
    from finagent.config import load_settings
    from finagent.analysis.strategist import SYSTEM_PROMPT
    k = load_settings().ritim_kip("nabiz")["kaynaklar"]
    assert "bilancobeklenti" in k and k.index("bilancobeklenti") > k.index("bilancotakvim"), \
        "beklenti takvimden SONRA kosmali (hedefler takvimden)"
    assert "fiyatlanan_hareket_%" in SYSTEM_PROMPT
    kaynak = (KOK / "src/finagent/pulse/strateji.py").read_text(encoding="utf-8")
    assert "bilanco_beklentisi" not in kaynak and "hareket_pct" not in kaynak, \
        "fiyatlanan hareket E1'e girdi — on kayitsiz yeni hipotez"
    from finagent.collectors.bilancotakvim import yaklasan_bilancolar
    s, db = _faz4_kurulum([("QCOM", 2)])
    from finagent.collectors.bilancobeklenti import BilancoBeklentiCollector
    BilancoBeklentiCollector(s, db, browser=None).collect(_cagir=_faz4_sahte_cagir([]))
    b = yaklasan_bilancolar(db, "ali", gun=7)["bilancolar"][0]
    assert b["fiyatlanan_hareket_%"] and b["opsiyon_fiyat_kaynagi"] == "orta"



# ======================================================================
# FAZ 2b — SUNUCU TARAFI ALARMLAR
# ======================================================================

class _SahteAlarmSunucusu:
    """IBKR alarm uclarinin OLCULEN bicimleri (25 Eyl deneme alarmlari)."""

    def __init__(self, alarmlar=None, zaman_asimi=None):
        self.alarmlar = {a["id"]: a for a in (alarmlar or [])}
        self.kayit = []
        self.zaman_asimi = zaman_asimi or set()   # {"create_alert", ...}
        self._n = 0

    def __call__(self, arac, arg=None, **kw):
        from finagent.ibkr.mcp_kanal import McpSonuc
        from finagent.ibkr.istemci import DurumBilinmiyorHatasi
        arg = dict(arg or {})
        self.kayit.append((arac, arg))
        if arac == "get_alerts":
            v = {"alerts": [dict(a) for a in self.alarmlar.values()]}
        elif arac == "create_alert":
            self._n += 1
            aid = f"{self._n:024x}"
            self.alarmlar[aid] = {"id": aid, "name": arg["symbol"], "status": "ACTIVE",
                                  "condition": {k: v for k, v in (
                                      ("contract_id", arg.get("contract_id")),
                                      ("exchange", arg.get("exchange")),
                                      ("condition_type", arg["condition_type"]),
                                      ("operator", arg["operator"].lower()),
                                      ("value", arg["value"])) if v is not None}}
            if "create_alert" in self.zaman_asimi:
                raise DurumBilinmiyorHatasi("zaman asimi (sahte)")
            v = {"id": aid}
        elif arac == "update_alert":
            a = self.alarmlar[arg["id"]]
            a["condition"]["value"] = arg["value"]
            v = {"id": arg["id"]}
        elif arac == "delete_alert":
            for i in arg["ids"]:
                self.alarmlar.pop(i)
            v = {"ids": arg["ids"]}
        else:
            raise AssertionError(f"beklenmeyen arac {arac}")
        return McpSonuc(arac, arg, v, "", 1.0)


def _faz2b_kurulum(stoplar=(("QCOM", 167.97, 159.264),), gunluk=None):
    """IBKR pozisyonlari (maliyet), kuralin kaydettigi girisler, hesap getirisi."""
    import copy, tempfile, datetime as _dt, os
    from finagent.config import load_settings
    from finagent.storage.db import Database
    os.environ["IBKR_ALARM_EPOSTA"] = "test@example.com"
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "t.db"); db.init_schema()
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    for i, (sem, maliyet, stop) in enumerate(stoplar):
        iid = db.upsert_instrument(sem, "BUX", sem + " Inc", "equity", "USD")
        db.query("INSERT INTO positions (sahip, snapshot_ts, account, instrument_id, quantity, "
                 "avg_cost, currency) VALUES ('ali', ?, 'ibkr', ?, 0.12, ?, 'USD')",
                 (ts, iid, maliyet))
        db.query("INSERT INTO identities (instrument_id, conid, status) VALUES (?, ?, 'dogrulandi')",
                 (iid, 273544 if sem == "QCOM" else 2000 + i))
        if stop is not None:
            db.query("INSERT INTO predictions (sahip, instrument_id, ajan, olusma_ts, yon, ufuk_gun, "
                     "baslangic_fiyat, taktik_giris, taktik_stop) "
                     "VALUES ('ali', ?, 'strateji', ?, 'yukari', 20, ?, ?, ?)",
                     (iid, ts, maliyet, maliyet, stop))
    for k, g in enumerate(gunluk if gunluk is not None else
                          [0.0, 0.0] + [(-1) ** j * 0.01 for j in range(23)]):
        db.query("INSERT INTO hesap_getirisi (hesap, tarih, nav, gunluk) VALUES ('ibkr', ?, 100, ?)",
                 ((_dt.date(2026, 8, 1) + _dt.timedelta(days=k)).isoformat(), g))
    db._conn.commit()
    s = load_settings(); s.raw = copy.deepcopy(s.raw)
    return s, db


def test_faz2b_gunluk_esik_KENDI_oynakliktan_ve_AZ_veride_UYDURMAZ():
    from finagent.ibkr.alarm import gunluk_esik
    e, g = gunluk_esik([0.0, 0.0] + [0.01] * 19)
    assert e is None and "19" in g, "bastaki fonlama sifirlari atilmali; 19 < 20"
    e, g = gunluk_esik([(-1) ** j * 0.01 for j in range(24)])
    # sigma ~1,02 -> 3 sigma ~3,07 -> yarim puana: 3,0
    assert e == -3.0, (e, g)
    # Olculen hesap serisi (25 Eyl, 23 gun): sigma %1,215 -> 3 sigma %3,65 -> %3,5
    olculen = [-0.000778, -0.000403, 0.000461, -0.002063, -0.008224, 0.006015, 0.033882,
               0.036231, -0.000809, 0.023883, 0.001258, 0.002299, -0.0037, 0.010332,
               -0.003453, 0.005274, 0.008917, 0.000141, 0.026386, 0.004843, 0.004621,
               -0.00231, -0.00014]
    assert gunluk_esik(olculen)[0] == -3.5


def test_faz2b_arguman_OLCULEN_semaya_uygun():
    from finagent.ibkr.alarm import arguman
    stop = {"tur": "stop", "ad": "FA QCOM stop", "kosul_tipi": "LAST", "operator": "LTE",
            "deger": 159.26, "conid": 273544, "sembol": "QCOM"}
    a = arguman(stop, "x@y.z")
    assert a["contract_id"] == 273544 and a["exchange"] == "SMART" and a["active_hours"] == "REGULAR"
    assert a["email"] == "x@y.z" and a["tif"] == "UNTIL_TRIGGERED" and "id" not in a
    assert "GÖNDERİLMEDİ" in a["email_note"]
    g = arguman({"tur": "gunluk_zarar", "ad": "FA gunluk zarar", "kosul_tipi": "DAILY_PNL",
                 "operator": "LTE", "deger": -3.5}, "x@y.z", alert_id="abc")
    assert g["id"] == "abc" and "contract_id" not in g and "exchange" not in g
    assert g["value"] == -3.5


def _h(anahtar="stop:1", deger=159.26, tur="stop"):
    return {"anahtar": anahtar, "tur": tur, "sembol": "QCOM", "instrument_id": 1, "conid": 273544,
            "ad": "FA QCOM stop" if tur == "stop" else "FA gunluk zarar",
            "kosul_tipi": "LAST" if tur == "stop" else "DAILY_PNL", "operator": "LTE",
            "deger": deger, "gerekce": "t"}


def _r(id_, durum, alert_id=None, deger=159.26, anahtar="stop:1", ad="FA QCOM stop"):
    return {"id": id_, "anahtar": anahtar, "tur": "stop", "ad": ad, "kosul_tipi": "LAST",
            "operator": "LTE", "deger": deger, "alert_id": alert_id, "durum": durum}


def _a(aid, value=159.26, name="FA QCOM stop"):
    return {"id": aid, "name": name, "status": "ACTIVE",
            "condition": {"condition_type": "LAST", "operator": "lte", "value": value}}


def test_faz2b_plan_YABANCI_alarma_dokunmaz_KAYIBI_kendiliginden_kurmaz():
    from finagent.ibkr.alarm import plan
    # Kullanicinin kendi alarmi bizimkiyle AYNI ad ve kosulda bile: bizim degil.
    p = plan([], [], [_a("elle")])
    assert not p["sil"] and not p["guncelle"] and p["yabanci"] == 1
    p = plan([_h()], [], [_a("elle")])
    assert [h["anahtar"] for h in p["olustur"]] == ["stop:1"] and p["yabanci"] == 1
    # Bizim aktif alarm sunucudan dusmus -> kayip; ayni deger -> KURULMAZ.
    p = plan([_h()], [_r(1, "aktif", "a1")], [])
    assert p["durum_degisimi"] == [(1, "kayip", "a1", None)]
    assert not p["olustur"] and len(p["kayip_atlanan"]) == 1
    assert plan([_h()], [_r(1, "kayip", "a1")], [])["olustur"] == []
    # yenile -> kurulur; stop DEGISTIYSE (yeni islem) -> kurulur.
    assert len(plan([_h()], [_r(1, "kayip", "a1")], [], yenile=True)["olustur"]) == 1
    assert len(plan([_h(deger=170.0)], [_r(1, "kayip", "a1")], [])["olustur"]) == 1


def test_faz2b_plan_guncelle_sil_ve_SUNUCU_DEGERI_dogrudur():
    from finagent.ibkr.alarm import plan
    p = plan([_h()], [_r(1, "aktif", "a1")], [_a("a1")])
    assert not (p["olustur"] or p["guncelle"] or p["sil"]) and not p["durum_degisimi"]
    p = plan([_h(deger=170.0)], [_r(1, "aktif", "a1")], [_a("a1")])
    assert len(p["guncelle"]) == 1 and p["guncelle"][0][0]["alert_id"] == "a1"
    # Guncelleme zaman asimina ugramis ama sunucuya ulasmis: deger SUNUCUDAN.
    p = plan([_h(deger=170.0)], [_r(1, "aktif", "a1")], [_a("a1", value=170.0)])
    assert not p["guncelle"] and p["durum_degisimi"] == [(1, "aktif", "a1", 170.0)]
    # Pozisyon kapandi -> YALNIZCA bizim alarm silinir.
    p = plan([], [_r(1, "aktif", "a1")], [_a("a1"), _a("elle")])
    assert [m["alert_id"] for m in p["sil"]] == ["a1"]
    # Silme zaman asimi: sunucuda HALA var -> aktif + yeniden sil onerilir; yoksa silindi.
    p = plan([], [_r(1, "siliniyor", "a1")], [_a("a1")])
    assert (1, "aktif", "a1", 159.26) in p["durum_degisimi"] and len(p["sil"]) == 1
    assert plan([], [_r(1, "siliniyor", "a1")], [])["durum_degisimi"] == [(1, "silindi", "a1", None)]


def test_faz2b_plan_BELIRSIZ_satir_ad_ve_kosulla_SAHIPLENILIR_cift_kurulmaz():
    from finagent.ibkr.alarm import plan
    p = plan([_h()], [_r(1, "belirsiz")], [_a("a9")])
    assert p["durum_degisimi"] == [(1, "aktif", "a9", None)] and not p["olustur"] and p["yabanci"] == 0
    p = plan([_h()], [_r(1, "belirsiz")], [])
    assert p["durum_degisimi"] == [(1, "kurulmadi", None, None)] and len(p["olustur"]) == 1
    # Iki aday -> hangisi bizim BELIRSIZ: sahiplenilmez, ikisi de yabanci kalir.
    p = plan([_h()], [_r(1, "belirsiz")], [_a("a8"), _a("a9")])
    assert p["durum_degisimi"] == [(1, "kurulmadi", None, None)] and p["yabanci"] == 2
    # Deger farkli -> bizim degil.
    p = plan([_h()], [_r(1, "belirsiz")], [_a("a9", value=150.0)])
    assert p["durum_degisimi"] == [(1, "kurulmadi", None, None)]


def test_faz2b_UCTAN_UCA_kur_tekrar_kurma_kayip_yenile():
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum()
    srv = _SahteAlarmSunucusu([_a("elle", value=1.0, name="benim alarmim")])
    metin, veri = A.hazirla(s, db, "ali", _cagir=srv)
    assert veri and "159,26" in metin and "%3,0" in metin and "IBKR Desktop" in metin
    assert "1 alarm daha var" in metin and "test@example.com" in metin
    assert not any(a != "get_alerts" for a, _ in srv.kayit), "hazirla YAZDI"
    sonuc = A.yurut(s, db, veri, "ali", _cagir=srv)
    assert sonuc.count("kuruldu") == 2, sonuc
    olusan = [b for a, b in srv.kayit if a == "create_alert"]
    stop = next(b for b in olusan if b["condition_type"] == "LAST")
    assert stop["contract_id"] == 273544 and stop["value"] == 159.26 and stop["email"] == "test@example.com"
    assert "elle" in srv.alarmlar and len(srv.alarmlar) == 3
    # Ikinci kez: is yok.
    metin, veri = A.hazirla(s, db, "ali", _cagir=srv)
    assert veri is None and "Değişiklik gerekmiyor" in metin
    # Kullanici stop alarmini ELLE sildi (ya da tetiklendi): kayip, KURULMAZ.
    sid = next(i for i, a in srv.alarmlar.items() if a["name"] == "FA QCOM stop")
    srv.alarmlar.pop(sid)
    metin, veri = A.hazirla(s, db, "ali", _cagir=srv)
    assert veri is None and "Kayıp" in metin and "/alarm yenile" in metin
    metin, veri = A.hazirla(s, db, "ali", yenile=True, _cagir=srv)
    assert veri and "Kurulacak" in metin
    assert "kuruldu" in A.yurut(s, db, veri, "ali", _cagir=srv)
    assert sum(1 for a in srv.alarmlar.values() if a["name"] == "FA QCOM stop") == 1


def test_faz2b_yurut_PLAN_DEGISTIYSE_hicbir_sey_yapmaz():
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum()
    srv = _SahteAlarmSunucusu()
    _, veri = A.hazirla(s, db, "ali", _cagir=srv)
    # Onayla yurut arasinda pozisyon kapandi.
    db.query("DELETE FROM positions"); db._conn.commit()
    n = len(srv.kayit)
    sonuc = A.yurut(s, db, veri, "ali", _cagir=srv)
    assert "değişti" in sonuc and not any(a != "get_alerts" for a, _ in srv.kayit[n:])
    # Ayni onay IKI KEZ: ikincisi plan artik bos oldugu icin reddedilir.
    s, db = _faz2b_kurulum()
    srv = _SahteAlarmSunucusu()
    _, veri = A.hazirla(s, db, "ali", _cagir=srv)
    A.yurut(s, db, veri, "ali", _cagir=srv)
    assert "değişti" in A.yurut(s, db, veri, "ali", _cagir=srv)
    assert len(srv.alarmlar) == 2


def test_faz2b_ZAMAN_ASIMI_cift_alarm_URETMEZ():
    """create zaman asimina ugradi ama sunucuda kuruldu: satir once yazildigi
    icin sonraki mutabakat onu ad + kosulla sahiplenir; ikinci kopya yok."""
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum(gunluk=[])
    srv = _SahteAlarmSunucusu(zaman_asimi={"create_alert"})
    _, veri = A.hazirla(s, db, "ali", _cagir=srv)
    sonuc = A.yurut(s, db, veri, "ali", _cagir=srv)
    assert "ULAŞMIŞ OLABİLİR" in sonuc
    assert db.query("SELECT durum FROM ibkr_alarm")[0][0] == "belirsiz"
    srv.zaman_asimi = set()
    metin, veri = A.hazirla(s, db, "ali", _cagir=srv)
    assert veri is None, metin
    assert db.query("SELECT durum, alert_id FROM ibkr_alarm")[0]["durum"] == "aktif"
    assert len(srv.alarmlar) == 1


def test_faz2b_STOPU_BILINMEYEN_pozisyona_alarm_KURULMAZ_ve_SOYLENIR():
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum(stoplar=(("QCOM", 167.97, 159.264), ("AAA", 50.0, None)), gunluk=[0.01] * 5)
    h, notlar = A.hedefler(db, s, "ali")
    assert [x["sembol"] for x in h] == ["QCOM"]
    assert any("AAA" in n and "bilinmiyor" in n for n in notlar)
    assert any("Günlük zarar" in n and "uydurulmadı" in n for n in notlar)


def test_faz2b_EPOSTASIZ_kurulmaz_ve_KOMUT_yalnizca_IBKR_sahibine():
    import os
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum()
    os.environ.pop("IBKR_ALARM_EPOSTA", None)
    try:
        A.hazirla(s, db, "ali", _cagir=_SahteAlarmSunucusu())
        raise AssertionError("e-postasiz plan kuruldu")
    except A.AlarmHatasi as e:
        assert "IBKR_ALARM_EPOSTA" in str(e)
    import inspect
    from finagent.bot import listener as L
    kaynak = inspect.getsource(L.FinBot._alarm_komutu)
    assert 'self.s.get("ibkr.sahip")' in kaynak



def test_faz2b_NABIZ_hatirlatmasi_AGSIZ_ve_AYNI_sapmayi_TEKRARLAMAZ():
    import tempfile
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum()
    yol = Path(tempfile.mkdtemp()) / "h.json"
    m = A.hatirlatma(db, s, "ali", yol)
    assert m and "QCOM: alarm kurulu değil" in m and "/alarm" in m
    assert A.hatirlatma(db, s, "ali", yol) is None, "ayni sapma ikinci gece tekrarlandi"
    srv = _SahteAlarmSunucusu()
    _, veri = A.hazirla(s, db, "ali", _cagir=srv)
    A.yurut(s, db, veri, "ali", _cagir=srv)
    assert A.yerel_sapma(db, s, "ali") == []
    assert A.hatirlatma(db, s, "ali", yol) is None
    # Pozisyon kapandi -> YENI sapma -> soylenir.
    db.query("DELETE FROM positions"); db._conn.commit()
    m = A.hatirlatma(db, s, "ali", yol)
    assert m and "silinmeli" in m


def test_faz2b_NABIZ_yalnizca_AYARDAKI_kipte_ve_IBKR_sahibine():
    from unittest.mock import MagicMock, patch
    from finagent.pulse.runner import Nabiz
    s, db = _faz2b_kurulum()
    r = Nabiz.__new__(Nabiz)
    r.s, r.db = s, db
    r._sahibe_bildir = MagicMock()
    with patch("finagent.ibkr.alarm.hatirlatma", return_value="x") as h:
        assert r._alarm_hatirlat("sabah", ["ali"]) is None and not h.called
        assert r._alarm_hatirlat(s.get("ibkr.alarm_hatirlatma_kipi"), ["esi"]) is None
        assert r._alarm_hatirlat(s.get("ibkr.alarm_hatirlatma_kipi"), ["ali"]) == "x"
    r._sahibe_bildir.assert_called_once()
    assert r._sahibe_bildir.call_args[0][0] == "ali"
    # Ariza nabzi dusurmez.
    with patch("finagent.ibkr.alarm.hatirlatma", side_effect=RuntimeError("x")):
        assert r._alarm_hatirlat(s.get("ibkr.alarm_hatirlatma_kipi"), ["ali"]) is None



# ======================================================================
# FAZ 2c — GTC STOP EMRI
# ======================================================================

def test_faz2c_STP_yalnizca_SAT_ve_stop_fiyatli_govdede_price_TETIK():
    stp = E.EmirIstegi("U1", "273544", "SELL", "STP", 0.12, 159.26, "GTC")
    stp.dogrula()
    g = stp.govde()
    assert g["orderType"] == "STP" and g["price"] == 159.26 and g["tif"] == "GTC"
    assert g["quantity"] == 0.12 and g["side"] == "SELL"
    for kotu, parca in ((E.EmirIstegi("U1", "273544", "BUY", "STP", 1, 150.0), "SAT"),
                        (E.EmirIstegi("U1", "273544", "SELL", "STP", 1), "fiyatsiz")):
        try:
            kotu.dogrula()
            raise AssertionError(f"gecersiz STP gecti: {kotu}")
        except E.EmirReddedildi as e:
            assert parca in str(e), e
    # Ayni fiyatli LMT ile AYNI parmak izi olamaz (onay fisi turu de kapsar).
    assert stp.parmak_izi() != E.EmirIstegi("U1", "273544", "SELL", "LMT", 0.12,
                                            159.26, "GTC").parmak_izi()


def test_faz2c_onkontrol_STOP_canli_fiyatin_USTUNDEYSE_ENGEL_altindaysa_BOSLUK_uyarisi():
    poz = [{"conid": 265598, "description": "AAPL", "position": 3, "avgPrice": 100.0}]
    ust = _ok(E.EmirIstegi("U1", "265598", "SELL", "STP", 3, 165.0, "GTC"), pozisyonlar=poz)
    assert not ust.gonderilebilir and any("ANINDA" in e for e in ust.engeller), ust.engeller
    alt = _ok(E.EmirIstegi("U1", "265598", "SELL", "STP", 3, 150.0, "GTC"), pozisyonlar=poz)
    assert alt.gonderilebilir, alt.engeller
    assert any("PIYASA" in u and "ALTINDA" in u for u in alt.uyarilar), alt.uyarilar
    assert alt.tahmini_tutar == 450.0, "STP tutari stop fiyatindan"
    # Kismi koruma SOYLENIR.
    kismi = _ok(E.EmirIstegi("U1", "265598", "SELL", "STP", 1, 150.0, "GTC"), pozisyonlar=poz)
    assert any("yalnizca 1" in u for u in kismi.uyarilar), kismi.uyarilar
    # Elde olandan fazla stop = aciga satis -> engel (SAT kurali STP'yi de kapsar).
    fazla = _ok(E.EmirIstegi("U1", "265598", "SELL", "STP", 5, 150.0, "GTC"), pozisyonlar=poz)
    assert any("aciga satis" in e for e in fazla.engeller)


def test_faz2c_stop_coz_ADET_ve_SEVIYE_koddan_BILINMEYEN_stop_uydurulmaz():
    from finagent.bot.emirakis import EmirHatasi, stop_coz
    s, db = _faz2b_kurulum(stoplar=(("QCOM", 167.97, 159.264), ("AAA", 50.0, None)))
    c = stop_coz(db, "qcom", "ali")
    assert c == {"sembol": "QCOM", "yon": "SELL", "adet": 0.12, "fiyat": 159.26,
                 "sure": "GTC", "tur": "STP"}, c
    for arg, parca in (("AAA", "UYDURULMAZ"), ("ZZZ", "pozisyon yok"),
                       ("", "Kullanım"), ("QCOM 150", "Kullanım")):
        try:
            stop_coz(db, arg, "ali")
            raise AssertionError(f"{arg!r} gecti")
        except EmirHatasi as e:
            assert parca in str(e), (arg, e)
    # Baska sahibin pozisyonu bu sahibin stop'u DEGIL.
    try:
        stop_coz(db, "QCOM", "esi")
        raise AssertionError("baska sahip")
    except EmirHatasi as e:
        assert "pozisyon yok" in str(e)


def test_faz2c_STOP_emri_DEGISTIRME_yolundan_gecmez():
    """Degistirme govdesi fiyati yalnizca LMT icin tasiyor; STP buradan
    gecseydi stop fiyati DUSERDI."""
    from unittest.mock import patch
    from finagent.bot import emirakis as EA
    s, db = _faz2b_kurulum()
    acik = {"orderId": 7, "conid": 273544, "side": "SELL", "origOrderType": "STOP",
            "totalSize": 0.12, "price": 159.26, "timeInForce": "GTC"}
    with patch.object(EA, "_hesap", return_value="U1"), \
         patch.object(EA, "_acik_emri_bul", return_value=acik), \
         patch.object(EA, "Istemci"):
        try:
            EA.degistir_hazirla(s, db, "7", 0.1, None, "ali")
            raise AssertionError("STP degistirme yolundan gecti")
        except EA.EmirHatasi as e:
            assert "/stop" in str(e)
        acik["origOrderType"] = "LIMIT"
        metin, veri = EA.degistir_hazirla(s, db, "7", 0.1, None, "ali")
        assert veri["govde"]["orderType"] == "LMT" and veri["govde"]["price"] == 159.26


def test_faz2c_KOMUT_stop_emir_ile_AYNI_kapidan():
    import inspect
    from finagent.bot import listener as L, yetenekler as Y
    kaynak = inspect.getsource(L.FinBot._on_text) if hasattr(L.FinBot, "_on_text") \
        else inspect.getsource(L.FinBot)
    assert 'self._emir_komutu(arg, chat_id, stop=True)' in kaynak
    assert "stop" in Y.KOMUTLAR
    from finagent.bot import emirakis as EA
    assert "_hazirla(s, db, stop_coz(db, arg, sahip), sahip, kanal)" in inspect.getsource(EA.stop_hazirla)



# ======================================================================
# FAZ 5 — TEMA YOGUNLASMASI (Ali: B secenegi)
# ======================================================================

# OLCULEN `search_contracts` yaniti (25 Eyl, ASML), kisaltilmis.
_ASML_ARAMA = [
    {"underlying_contract_id": 117902840, "exchange": "NASDAQ", "symbol": "ASML",
     "description": "ASML HOLDING NV-NY REG SHS", "country_code": "US"},
    {"underlying_contract_id": 117589399, "exchange": "AEB", "symbol": "ASML",
     "description": "ASML HOLDING NV", "country_code": "NL"},
    {"underlying_contract_id": 999, "exchange": "X", "symbol": "ASML",
     "description": "ASMLX LEVERAGED FUND", "country_code": "US"},
    {"underlying_contract_id": 998, "exchange": "Y", "symbol": "ASMLL",
     "description": "ASML HOLDING 2X", "country_code": "US"},
]


def test_faz5_alternatif_listeleme_AYNI_SIRKET_disina_cikmaz():
    from finagent.analysis.tema import alternatif_listeleme
    assert alternatif_listeleme(_ASML_ARAMA, 117902840, "ASML") == [117589399]
    # GERCEK yanit (25 Eyl) bir sarmalayici icinde geliyor.
    import json
    gercek = json.loads((Path(__file__).parent / "mcp_fikstur" /
                         "search_contracts_asml.json").read_text())
    assert isinstance(gercek, dict) and "results" in gercek
    assert 117589399 in alternatif_listeleme(gercek, 117902840, "ASML")
    # Bizim conid sonucta yoksa sirketi dogrulayamayiz -> tahmin YOK.
    assert alternatif_listeleme(_ASML_ARAMA, 555, "ASML") == []
    assert alternatif_listeleme({"hata": 1}, 117902840, "ASML") == []


def _faz5_kurulum():
    import copy, tempfile, datetime as _dt
    from finagent.config import load_settings
    from finagent.storage.db import Database
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "t.db"); db.init_schema()
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    ids = {}
    for sem, tur, hesap, deger, pb, conid in (
            ("ASML", "equity", "bux", 2000.0, "EUR", 117902840),
            ("NVDA", "equity", "bux", 700.0, "EUR", 4815747),
            ("QCOM", "stk", "ibkr", 100.0, "USD", 273544),
            ("CNDX", None, "bux", 500.0, "EUR", 75961314),
            ("INGA", None, "bux", 80.0, "EUR", 240601748),
            ("BNB", "crypto", "binance", 50.0, "EUR", None),
            ("THYAO", "equity", "midas", 1000.0, "TRY", None),
            ("MRNA", "equity", "bux", 200.0, "EUR", 344809106)):
        iid = db.upsert_instrument(sem, "BUX", sem, tur, pb)
        ids[sem] = iid
        db.query("INSERT INTO positions (sahip, snapshot_ts, account, instrument_id, quantity, "
                 "market_value, currency) VALUES ('ali', ?, ?, ?, 1, ?, ?)",
                 (ts, hesap, iid, deger, pb))
        if conid:
            db.query("INSERT INTO identities (instrument_id, conid, status) VALUES (?, ?, 'dogrulandi')",
                     (iid, conid))
    db.query("INSERT INTO fx_rates (ts, base, quote, rate, source) VALUES (?, 'USD', 'EUR', 0.85, 't')", (ts,))
    db._conn.commit()
    s = load_settings(); s.raw = copy.deepcopy(s.raw)
    return s, db, ids


def _faz5_cagir(kayit, bos=(117902840,), hata=()):
    from finagent.ibkr.mcp_kanal import McpSonuc
    from finagent.ibkr.istemci import UlasilamadiHatasi
    T = {117589399: ["Semiconductor Equipment", "Semiconductor Chips", "AI Infrastructure"],
         4815747: ["AI Chips", "Semiconductor Chips", "Data Centers"],
         273544: ["Smartphones", "Semiconductor Chips"],
         344809106: ["Biotech R&D"], 240601748: ["Commercial Banking"]}
    def c(arac, arg=None, **kw):
        kayit.append((arac, dict(arg or {})))
        if arac == "search_contracts":
            # OLCULEN bicim: sarmalayici.
            return McpSonuc(arac, arg, {"results": _ASML_ARAMA, "totals": {}}, "", 1.0)
        cid = arg["contract_id"]
        if cid in hata:
            raise UlasilamadiHatasi("sahte")
        v = [] if cid in bos else [{"name": n} for n in T.get(cid, [])]
        return McpSonuc(arac, arg, {"linked_themes": v}, "", 1.0)
    return c


def test_faz5_toplayici_YENI_sirketi_ceker_ANA_LISTELEMEYE_duser_ve_TEKRAR_CAGIRMAZ():
    import json
    from finagent.collectors.sirkettema import SirketTemaCollector
    s, db, ids = _faz5_kurulum()
    kayit = []
    c = SirketTemaCollector(s, db, browser=None)
    r = c.collect(_cagir=_faz5_cagir(kayit))
    # Turu BOS olanlar (CNDX fon, INGA hisse) da sorulur; IBKR ayirir.
    assert r.status == "ok" and "cekilen 6/6" in r.error, r.error
    a = db.query("SELECT durum, conid, temalar FROM sirket_tema WHERE instrument_id = ?",
                 (ids["ASML"],))[0]
    assert a["durum"] == "tamam" and a["conid"] == 117589399, "ASML ana listelemeye dusmedi"
    assert "Semiconductor Chips" in json.loads(a["temalar"])
    # Portfoy DEGISMEDI -> baglayici HIC cagrilmaz.
    n = len(kayit)
    r = c.collect(_cagir=_faz5_cagir(kayit))
    assert len(kayit) == n and "yeni sirket yok" in r.error


def test_faz5_toplayici_BUTCE_ertelenen_ve_HATA_ertesi_gece():
    from finagent.collectors.sirkettema import SirketTemaCollector
    s, db, ids = _faz5_kurulum()
    s.raw["sources"]["sirkettema"]["azami_sembol"] = 2
    kayit = []
    c = SirketTemaCollector(s, db, browser=None)
    # Sira sembole gore: ilk gece ASML + CNDX; CNDX'te ag hatasi.
    r = c.collect(_cagir=_faz5_cagir(kayit, hata=(75961314,)))
    assert "4 sirket sonraki geceye ertelendi" in r.error and r.status == "partial", r.error
    durum = lambda s_: db.query("SELECT durum FROM sirket_tema WHERE instrument_id = ?",
                                (ids[s_],))[0][0]
    assert durum("CNDX") == "hata"
    # Ertesi kosu 20 saat DOLMADAN: hata tekrar denenmez, ertelenenler cekilir.
    n = len(kayit)
    c.collect(_cagir=_faz5_cagir(kayit))
    assert durum("INGA") == "tamam" and durum("CNDX") == "hata"
    assert 75961314 not in {a.get("contract_id") for _, a in kayit[n:]}
    # Hata 20 saatten eskiyse yeniden denenir.
    db.query("UPDATE sirket_tema SET cekilis_ts = datetime('now', '-1 day') WHERE durum = 'hata'")
    db._conn.commit()
    c.collect(_cagir=_faz5_cagir(kayit))
    assert not db.query("SELECT 1 FROM sirket_tema WHERE durum = 'hata'")


def test_faz5_yogunlasma_EUR_cevirir_TOPLAMAZ_ve_SINIRLARI_soyler():
    from finagent.analysis.tema import yogunlasma
    from finagent.collectors.sirkettema import SirketTemaCollector
    s, db, ids = _faz5_kurulum()
    SirketTemaCollector(s, db, browser=None).collect(_cagir=_faz5_cagir([]))
    y = yogunlasma(db, "ali")
    # THYAO (TRY) kuru yok -> HESABA GIRMEZ, adiyla soylenir.
    assert any("THYAO" in c for c in y["cevrilemeyen"])
    assert y["toplam_eur"] == 2000 + 700 + 85 + 500 + 200 + 80 + 50
    # INGA'nin turu bos ama IBKR tema verdi -> hisse; CNDX vermedi -> fon/bilinmeyen.
    assert y["hisse_eur"] == 2000 + 700 + 85 + 200 + 80
    t = {x["tema"]: x for x in y["temalar"]}
    ch = t["Semiconductor Chips"]
    assert ch["sirketler"] == ["ASML", "NVDA", "QCOM"]
    assert ch["toplam_%"] == round(2785 / 3615 * 100, 1)
    assert y["temalar"][0]["tema"] == "Semiconductor Chips"
    assert sum(x["toplam_%"] for x in y["temalar"]) > 100, "ornek toplanmazligi sinamali"
    assert y["fon_ya_da_sinifi_bilinmeyen"] == ["CNDX"] and y["kripto"] == ["BNB"]
    assert "TOPLANMAZ" in y["not"]
    # Tema verisi olmayan hisse SOYLENIR ('bos' 'tema yok' demek degil).
    db.query("UPDATE sirket_tema SET durum = 'bos', temalar = '[]' WHERE instrument_id = ?",
             (ids["MRNA"],)); db._conn.commit()
    assert "MRNA" in yogunlasma(db, "ali")["tema_verisi_yok"]



def test_gozlem_BAGLAMI_deponun_TEK_kosu_kaynagi_kuralindan():
    """
    25 Eyl launchd nabzi "elle" yazildi: gozlem XPC_SERVICE_NAME'e
    bakiyordu. Kural artik deponun TEK kurali `db.kosu_kaynagi()`
    (`scripts/_ortak.sh` FINAGENT_KOSU_KAYNAK=zamanlanmis). Yanlis etiket
    1 Eki'deki "5/5 launchd kosusu" olcutunu sifira dusururdu.
    """
    import json, os, tempfile
    from unittest.mock import patch
    from finagent.ibkr.mcp_gozlem import gece_gozlemi, ozet
    yanit = json.dumps({"positions": [{"contract_id": 273544, "position": 0.12}]})
    with tempfile.TemporaryDirectory() as d:
        yol = Path(d) / "g.jsonl"
        with patch.dict(os.environ, {"FINAGENT_KOSU_KAYNAK": "zamanlanmis",
                                     "XPC_SERVICE_NAME": "0"}):
            k = gece_gozlemi(None, yol, _sorgu=_sahte_ikisi(yanit),
                             _cpgw=lambda s: [[273544, 0.12]])
        assert k["baglam"] == "launchd", k["baglam"]
        with patch.dict(os.environ, {"FINAGENT_KOSU_KAYNAK": "",
                                     "XPC_SERVICE_NAME": "com.alipala.finagent.nabiz"}):
            k = gece_gozlemi(None, yol, _sorgu=_sahte_ikisi(yanit),
                             _cpgw=lambda s: [[273544, 0.12]])
        assert k["baglam"] == "elle", "XPC artik olcut degil"
        assert ozet(yol)["gece_kosusu"] == 1
    # Betik gercekten ortak ortami yukluyor (kablo).
    kos = (Path(__file__).resolve().parents[1] / "scripts" / "run_kosu.sh").read_text()
    assert "_ortak.sh" in kos



def test_faz2b_TEK_TEK_secim_yalnizca_secileni_kurar_SECILMEYENI_hatirlar():
    """Ali 26 Eyl: "yalnizca QCOM'u kur". Secilmeyen kurulum 'reddedildi':
    ayni seviye bir daha onerilmez, gece hatirlatmasi da susar; `yenile` geri getirir."""
    import tempfile
    from finagent.ibkr import alarm as A
    s, db = _faz2b_kurulum()
    srv = _SahteAlarmSunucusu()
    metin, veri = A.hazirla(s, db, "ali", _cagir=srv)
    k = veri["kalemler"]
    assert [x["etiket"] for x in k] == ["QCOM stop", "Günlük zarar"], k
    assert "yalnızca birini" in metin
    sonuc = A.yurut(s, db, veri, "ali", _cagir=srv, secim=0)
    assert "FA QCOM stop kuruldu" in sonuc and "bir daha önerilmeyecek" in sonuc, sonuc
    assert [a["name"] for a in srv.alarmlar.values()] == ["FA QCOM stop"]
    metin, veri2 = A.hazirla(s, db, "ali", _cagir=srv)
    assert veri2 is None and "İstemediğin" in metin, metin
    assert A.yerel_sapma(db, s, "ali") == [], "reddedilen gece hatirlatmasina dondu"
    metin, veri3 = A.hazirla(s, db, "ali", yenile=True, _cagir=srv)
    assert veri3 and [x["etiket"] for x in veri3["kalemler"]] == ["Günlük zarar"]
    # Gecersiz secim hicbir sey yapmaz.
    s, db = _faz2b_kurulum()
    srv = _SahteAlarmSunucusu()
    _, veri = A.hazirla(s, db, "ali", _cagir=srv)
    try:
        A.yurut(s, db, veri, "ali", _cagir=srv, secim=5)
        raise AssertionError("gecersiz secim yurutuldu")
    except A.AlarmHatasi:
        pass
    assert not srv.alarmlar and not db.query("SELECT 1 FROM ibkr_alarm")


def test_faz2b_TEK_TEK_secim_BUTONLARI_ve_CALLBACK_ayni_kapidan():
    import tempfile
    from unittest.mock import MagicMock, patch
    from finagent.bot.listener import FinBot
    from finagent.bot.onay import OnayDeposu
    bot = FinBot.__new__(FinBot)
    bot.pending_dir = Path(tempfile.mkdtemp())
    kalem = [{"kod": "olustur:stop:1", "etiket": "QCOM stop"},
             {"kod": "olustur:gunluk_zarar", "etiket": "Günlük zarar"}]
    OnayDeposu(bot.pending_dir).yaz("t1", {"_tip": "ibkr_alarm", "_token": "t1",
                                           "_sahip": "ali", "_chat_id": 1,
                                           "parmak_izi": "x", "kalemler": kalem})
    kb = bot._onay_markup("t1")["inline_keyboard"]
    veriler = [r[0]["callback_data"] for r in kb]
    assert veriler == ["ok:t1", "als:t1:0", "als:t1:1", "no:t1"], veriler
    assert "als" in FinBot.ONAY_CALLBACK
    # Tek kalemli planda secim butonu YOK.
    OnayDeposu(bot.pending_dir).yaz("t2", {"_tip": "ibkr_alarm", "_token": "t2",
                                           "kalemler": kalem[:1]})
    assert len(bot._onay_markup("t2")["inline_keyboard"]) == 1
    # Callback: `als:t1:1` -> ayni sahiplenme kapisi, secim=1 yurutucuye gider.
    bot._authorised = lambda c: True
    bot.tg = MagicMock(); bot.s = MagicMock(); bot.db = MagicMock()
    bot._gonder = MagicMock()
    with patch("finagent.ibkr.alarm.yurut", return_value="tamam") as y:
        bot._on_callback({"id": "c", "data": "als:t1:1",
                          "message": {"chat": {"id": 1}, "message_id": 5}})
    assert y.call_args.kwargs["secim"] == 1, y.call_args
    # Baska tipteki istege yamanmis `als` YURUTULMEZ, istek geri konur.
    OnayDeposu(bot.pending_dir).yaz("t3", {"_tip": "ibkr_emir", "_token": "t3", "_sahip": "ali"})
    with patch("finagent.bot.emirakis.yurut") as ey:
        bot._on_callback({"id": "c", "data": "als:t3:0",
                          "message": {"chat": {"id": 1}, "message_id": 6}})
    assert not ey.called and OnayDeposu(bot.pending_dir).durum("t3") == "bekliyor"



# ======================================================================
# FAZ 6 — CPGW DUSUNCE BULUT (Ali 28 Eyl: "her surec buluttan")
# ======================================================================

def _fikstur(ad):
    import json
    v = json.loads((Path(__file__).parent / "mcp_fikstur" / ad).read_text())
    from finagent.ibkr.mcp_kanal import ham_ayristir
    return ham_ayristir(v) if isinstance(v, list) else v


def test_faz6_kotasyon_OLCULEN_bicim_KIP_ve_UYDURMA_YOK():
    from finagent.ibkr.bulut import kotasyon_ayristir
    k = kotasyon_ayristir(_fikstur("get_price_snapshot_hisse.json"))
    assert k["son"] == 196.4 and k["kip"] == "gercek_zamanli" and k["kanal"] == "bulut"
    assert k["alis"] == 196.3 and k["para_birimi"] is None, "para birimi uydurulmamali"
    g = kotasyon_ayristir({"last": {"price": None}, "bid-ask": {"bid": 10, "ask": 12},
                           "top-status": {"status": "DELAYED"}})
    assert g["son"] is None and g["orta"] == 11 and g["kip"] == "gecikmeli"
    for kotu in ({"top-status": {"status": "REJECT"}, "last": {"price": 5}},
                 {"top-status": {"status": "REALTIME"}}):
        try:
            kotasyon_ayristir(kotu)
            raise AssertionError(f"gecti: {kotu}")
        except ValueError:
            pass


def test_faz6_hesap_ozeti_ve_acik_emirler_OLCULEN_bicimden():
    from finagent.ibkr.bulut import acik_emirler_ayristir, hesap_ozeti_ayristir
    h = hesap_ozeti_ayristir(_fikstur("get_account_summary.json"),
                             _fikstur("get_account_balances.json"))
    assert h["ozet"]["netliquidation"] == (976.2789, "EUR")
    assert h["ozet"]["buyingpower"] == (858.61, "EUR")
    assert "BASE" not in h["nakit"] and h["nakit"]["USD"] == 38.4582
    a = acik_emirler_ayristir(_fikstur("get_account_orders.json"))
    assert a["emirler"] == [] and "KANITI degildir" in a["not"]
    try:
        acik_emirler_ayristir({"x": 1})
        raise AssertionError("bozuk bicim gecti")
    except ValueError:
        pass


def _islem(no, size, price=90.99, kom=0.05):
    # OLCULEN anahtarlar (28 Eyl, get_account_trades); degerler sentetik.
    return {"trade_id": f"t{no}", "symbol": "KO", "side": "BUY", "size": size,
            "price": price, "commission": kom, "net_amount": size * price,
            "trade_time": "2026-09-21T14:42:20Z", "order_id": no}


def test_faz6_mutabakat_BULUT_yalnizca_KANITLA_yazar():
    from finagent.ibkr.mutabakat import kos_bulut
    satirlar = [
        {"id": 1, "emir_id": "100", "durum": "gonderildi", "adet": 2, "symbol": "KO"},
        {"id": 2, "emir_id": "200", "durum": "gonderildi", "adet": 2, "symbol": "KO"},
        {"id": 3, "emir_id": "300", "durum": "gonderildi", "adet": 1, "symbol": "KO"},
        {"id": 4, "emir_id": "400", "durum": "gerceklesti", "dolum_fiyat": None,
         "adet": 1, "symbol": "KO"},
        {"id": 5, "emir_id": None, "durum": "hazirlandi", "adet": 1, "symbol": "KO"},
    ]
    gecmis = [_islem(100, 1), _islem(100, 1), _islem(200, 1), _islem(400, 1, 50.0)]
    k, dog = kos_bulut(satirlar, gecmis)
    d = {x.satir_id: x for x in k}
    assert d[1].yeni_durum == "gerceklesti" and d[1].alanlar["dolum_fiyat"] == 90.99
    assert d[1].alanlar["dolum_komisyon"] == 0.05 and d[1].alanlar["dolum_ts"]
    assert d[2].yeni_durum is None and "KISMEN" in d[2].aciklama
    assert 3 not in d, "izi olmayan emir icin karar URETILDI (yokluk kanit degil)"
    assert d[4].kod == "S1b_dolum_kurtarildi" and d[4].alanlar["dolum_fiyat"] == 50.0
    assert dog == 2
    k, dog = kos_bulut(satirlar, None)
    assert k == [] and dog == 4, "okunamayan gecmisten karar uretildi"


def test_faz6_mutabakat_CPGW_401de_BULUTA_gecer_baska_hatada_GECMEZ():
    import tempfile
    from unittest.mock import patch
    from finagent.bot import emirakis as EA
    from finagent.ibkr.istemci import YetkiHatasi, HizHatasi
    s, db, _ = _faz5_kurulum()
    iid = db.query("SELECT id FROM instruments LIMIT 1")[0][0]
    sid = db.emir_yaz(sahip="ali", hesap="U1", instrument_id=iid, conid="1", yon="BUY",
                      tur="LMT", adet=1, fiyat=91.0, sure="DAY", para_birimi="USD",
                      referans_fiyat=91.0, referans_kip="t", parmak_izi="p",
                      durum="gonderildi", not_=None)
    db.emir_guncelle(sid, emir_id="100")
    with patch.object(EA, "Istemci"), \
         patch.object(EA, "_hesap", side_effect=YetkiHatasi("401")), \
         patch("finagent.ibkr.bulut.islemler", return_value=[_islem(100, 1)]):
        metin, ozet = EA.mutabakat_ozetli(s, db, "ali")
    assert ozet["kanal"] == "bulut" and ozet["dolum_yazildi"] == 1, ozet
    assert "BULUTTAN" in metin
    r = db.query("SELECT durum, dolum_fiyat FROM emirler WHERE id = ?", (sid,))[0]
    assert r["durum"] == "gerceklesti" and r["dolum_fiyat"] == 90.99
    with patch.object(EA, "Istemci"), \
         patch.object(EA, "_hesap", side_effect=HizHatasi("429")):
        try:
            EA.mutabakat_ozetli(s, db, "ali")
            raise AssertionError("hiz siniri buluta gecirdi")
        except HizHatasi:
            pass


def test_faz6_SOHBET_ibkr_fiyat_CPGW_401de_BULUTTAN_ve_KANALI_soyler():
    import asyncio, json, tempfile
    from unittest.mock import patch
    from finagent.bot.tools import ToolBox
    from finagent.ibkr.istemci import YetkiHatasi, HizHatasi
    s, db, _ = _faz5_kurulum()
    s.raw.setdefault("ibkr", {})["acik"] = True
    arac = {t.name: t for t in ToolBox(s, db, Path(tempfile.mkdtemp()) / "p",
                                       sahip="ali", chat_id="1").araclar()}

    class Dusuk:
        def __init__(self, *a, **k): pass
        def __enter__(self): raise YetkiHatasi("401")
        def __exit__(self, *a): return False

    async def sahte(conid, **k):
        from finagent.ibkr.bulut import kotasyon_ayristir
        assert conid == 273544, "conid bizim kaydimizdan gelmeli"
        return kotasyon_ayristir(_fikstur("get_price_snapshot_hisse.json"))
    with patch("finagent.ibkr.piyasa.Piyasa", Dusuk), \
         patch("finagent.ibkr.bulut.kotasyon_async", sahte):
        v = json.loads(asyncio.run(arac["ibkr_fiyat"].handler({"sembol": "QCOM"}))
                       ["content"][0]["text"])
    assert v["kanal"] == "bulut" and v["son"] == 196.4 and v["kip"] == "gercek_zamanli", v
    assert v["para_birimi"] is None and "PARA BIRIMI" in v["not"]

    class Hizli(Dusuk):
        def __enter__(self): raise HizHatasi("429")
    with patch("finagent.ibkr.piyasa.Piyasa", Hizli), \
         patch("finagent.ibkr.bulut.kotasyon_async", sahte):
        v = json.loads(asyncio.run(arac["ibkr_fiyat"].handler({"sembol": "QCOM"}))
                       ["content"][0]["text"])
    assert "kanal" not in v and "hata" in v, "hiz siniri buluta gecirdi"


def test_faz6_STRATEJI_adeti_CPGW_401de_BULUT_hesap_degeriyle():
    from unittest.mock import MagicMock, patch
    from finagent.ibkr.istemci import YetkiHatasi
    from finagent.pulse.runner import Nabiz
    r = Nabiz.__new__(Nabiz)
    r.s, r.db = MagicMock(), MagicMock()
    r.db.fx_kuru.return_value = {"rate": 1.0}
    g = {"giris": 100.0, "stop": 90.0, "seviyeler": {"para_birimi": "EUR"}}
    with patch("finagent.ibkr.istemci.Istemci", side_effect=YetkiHatasi("401")), \
         patch("finagent.ibkr.bulut.toplam_netlik", return_value=(1000.0, "EUR")):
        r._adet_hesapla([g], {"risk_payi_pct": 1.0})
    assert g.get("adet") and "adet_sebep" not in g, g
    g2 = dict(g)
    with patch("finagent.ibkr.istemci.Istemci", side_effect=YetkiHatasi("401")), \
         patch("finagent.ibkr.bulut.toplam_netlik", side_effect=RuntimeError("yok")):
        r._adet_hesapla([g2], {"risk_payi_pct": 1.0})
    assert g2.get("adet_sebep") == "hesap degeri okunamadi"


def test_faz6_ibkr_durum_ve_acik_emirler_BULUT_yedegi_KABLOSU():
    import inspect
    from finagent.bot.tools import ToolBox
    k = inspect.getsource(ToolBox.araclar)
    for arac, cagri in (("ibkr_durum", "hesap_ozeti_async"),
                        ("ibkr_acik_emirler", "acik_emirler_async")):
        i = k.index(f'@tool("{arac}"'); j = k.find("@tool(", i + 10)
        assert cagri in k[i:j], f"{arac} bulut yedegine bagli degil"
    from finagent.pulse import runner
    assert "from ..ibkr.bulut import toplam_netlik" in inspect.getsource(runner)



# ======================================================================
# DOGAL DIL: stop ve alarm (Ali 28 Eyl: "keyword degil, duz yazdigimi anlasin")
# ======================================================================

def _dd_araclar():
    import tempfile
    from finagent.bot.tools import ToolBox
    s, db = _faz2b_kurulum()
    s.raw.setdefault("ibkr", {})["acik"] = True
    s.raw["ibkr"]["sahip"] = "ali"
    pend = Path(tempfile.mkdtemp()) / "p"
    tb = ToolBox(s, db, pend, sahip="ali", chat_id="1")
    return tb, {t.name: t for t in tb.araclar()}, pend


def _cagir_arac(arac, args):
    import asyncio, json
    return json.loads(asyncio.run(arac.handler(args))["content"][0]["text"])


def test_dogal_dil_STOP_araci_ONAYA_SUNAR_gondermez_ve_komutla_AYNI_govde():
    from unittest.mock import patch
    from finagent.bot import emirakis as EA
    from finagent.bot.onay import OnayDeposu
    tb, a, pend = _dd_araclar()
    veri = {"satir_id": 1, "sembol": "QCOM", "tur": "STP", "fiyat": 159.26}
    with patch.object(EA, "stop_hazirla", return_value=("OZET", veri)) as sh:
        v = _cagir_arac(a["ibkr_stop_hazirla"], {"sembol": "qcom"})
    assert sh.call_args.args[2] == "QCOM" and v["durum"] == "ONAY BEKLIYOR", v
    o = OnayDeposu(pend).oku(v["token"])
    assert o["_tip"] == EA.TIP and o["tur"] == "STP" and o["_sahip"] == "ali"
    with patch.object(EA, "stop_hazirla", return_value=("ENGEL", None)):
        v = _cagir_arac(a["ibkr_stop_hazirla"], {"sembol": "QCOM"})
    assert v["durum"] == "ENGELLENDI" and "token" not in v
    with patch.object(EA, "stop_hazirla", side_effect=EA.EmirHatasi("stop bilinmiyor")):
        v = _cagir_arac(a["ibkr_stop_hazirla"], {"sembol": "AAA"})
    assert "stop bilinmiyor" in v["hata"]


def test_dogal_dil_ALARM_araci_ayri_is_parcaciginda_ONAYA_SUNAR_ve_SECIM_klavyesi():
    import anyio
    from unittest.mock import patch
    from finagent.bot.listener import FinBot
    from finagent.ibkr import alarm as A
    tb, a, pend = _dd_araclar()
    kalem = [{"kod": "olustur:stop:1", "etiket": "QCOM stop"},
             {"kod": "olustur:gunluk_zarar", "etiket": "Günlük zarar"}]

    def sahte(s, db, sahip, yenile=False):
        # Gercek `hazirla` bulutu `anyio.run` ile cagiriyor: olay dongusu
        # ICINDE calisirsa patlar. Burada da ayni cagri yapiliyor.
        anyio.run(anyio.sleep, 0)
        return ("PLAN", {"parmak_izi": "x", "yenile": yenile, "kalemler": kalem})
    with patch.object(A, "hazirla", sahte):
        v = _cagir_arac(a["ibkr_alarm_plani"], {"yenile": True})
    assert v["durum"] == "ONAY BEKLIYOR", v
    bot = FinBot.__new__(FinBot); bot.pending_dir = pend
    kb = [r[0]["callback_data"] for r in bot._onay_markup(v["token"])["inline_keyboard"]]
    assert kb[1] == f"als:{v['token']}:0" and len(kb) == 4, kb
    with patch.object(A, "hazirla", return_value=("DEGISIKLIK YOK METNI", None)):
        v = _cagir_arac(a["ibkr_alarm_plani"], {})
    assert v["durum"] == "DEGISIKLIK YOK" and "token" not in v
    tb.s.raw["ibkr"]["sahip"] = "esi"
    v = _cagir_arac(a["ibkr_alarm_plani"], {})
    assert "bagli degil" in v["hata"]


def test_dogal_dil_ALARM_araci_GERCEK_govde_ve_GERCEK_db_ile_calisir():
    """Sahada (28 Eyl) sahte `hazirla` ile gecen test, gercek yolda sqlite
    is parcacigi hatasini KACIRDI. Bu test gercek `alarm.hazirla`yi gercek
    veritabaniyla, yalnizca IBKR sunucusu sahte olarak kosar."""
    from unittest.mock import patch
    from finagent.bot.onay import OnayDeposu
    tb, a, pend = _dd_araclar()
    srv = _SahteAlarmSunucusu()
    with patch("finagent.ibkr.mcp_kanal.cagir", srv):
        v = _cagir_arac(a["ibkr_alarm_plani"], {})
    assert v.get("durum") == "ONAY BEKLIYOR", v
    assert "159,26" in v["ozet"]
    assert len(OnayDeposu(pend).oku(v["token"])["kalemler"]) == 2
    with patch("finagent.ibkr.mcp_kanal.cagir", side_effect=RuntimeError("ag")):
        v = _cagir_arac(a["ibkr_alarm_plani"], {})
    assert "kurulu degil' DEME" in (v.get("ipucu") or ""), v


def test_dogal_dil_araclari_HICBIR_SEY_GONDERMEZ():
    import inspect
    from finagent.bot.tools import ToolBox
    k = inspect.getsource(ToolBox.araclar)
    for arac in ("ibkr_stop_hazirla", "ibkr_alarm_plani"):
        i = k.index(f'@tool("{arac}"'); j = k.find("@tool(", i + 10)
        blok = k[i:j]
        assert "_stage(" in blok and "ONAYA" in blok
        for yasak in ("yurut", "gonder(", "create_alert", "cagir("):
            assert yasak not in blok, f"{arac} icinde {yasak}"


# ======================================================================
# EMIR KAYNAGI (sema 37): kanal her kapidan, kanit emri durdurmaz,
# gecmis araci kaynagi tasir.
# ======================================================================

def test_emir_KANAL_her_kapidan_DOGRU_yaziliyor():
    """
    KABLO KACISI SINIFI: kanal parametresi var ama bir kapi onu
    gecirmiyorsa o kapidan gelen emirler sessizce NULL kalir. Bes kapinin
    besi de olculur: /emir, /stop, strateji butonu, iki arac.
    """
    from unittest.mock import patch
    from finagent.bot import emirakis as EA
    from finagent.bot.listener import FinBot

    tb, a, _ = _dd_araclar()
    veri = {"satir_id": 1, "sembol": "QCOM", "tur": "LMT", "fiyat": 1.0}
    with patch.object(EA, "hazirla", return_value=("OZET", veri)) as h:
        _cagir_arac(a["ibkr_emir_hazirla"],
                    {"sembol": "QCOM", "yon": "AL", "adet": 1, "fiyat": 1.0})
    assert h.call_args.kwargs.get("kanal") == "sohbet", h.call_args
    with patch.object(EA, "stop_hazirla", return_value=("OZET", veri)) as h:
        _cagir_arac(a["ibkr_stop_hazirla"], {"sembol": "QCOM"})
    assert h.call_args.kwargs.get("kanal") == "sohbet_stop", h.call_args

    s, db = tb.s, tb.db
    s.raw.setdefault("telegram", {})["sahipler"] = {"111": "ali"}
    bot = FinBot.__new__(FinBot)
    bot.s, bot.db = s, db
    bot.allowed = {111}
    giden = []

    class _Tg:
        def send_message(_self, m, chat_id=None, **k):
            giden.append(m); return True

        def answer_callback_query(_self, *a, **k):
            pass

    bot.tg = _Tg()
    bot._gonder = lambda m, c, reply_markup=None, kritik=False: giden.append(m)
    for cagri, beklenen in (
            (lambda: bot._emir_komutu("QCOM AL 1 1", 111), "komut"),
            (lambda: bot._emir_komutu("QCOM", 111, stop=True), "komut_stop"),
            (lambda: bot._on_callback({"id": "1", "data": "stremir:QCOM:1:1",
                                       "message": {"chat": {"id": 111}}}),
             "strateji_butonu")):
        with patch.object(EA, "hazirla", return_value=("E", None)) as h, \
             patch.object(EA, "stop_hazirla", return_value=("E", None)) as sh:
            cagri()
        cag = h.call_args or sh.call_args
        assert cag and cag.kwargs.get("kanal") == beklenen, (beklenen, cag)


def test_emir_KANITI_emri_DURDURMAZ_ve_kanal_YAZILIR():
    """
    Kanit toplama patlarsa emir satiri yine acilir (emir para hareketi;
    kanit yan kayit). Hata SESSIZ degil: log'a duser.
    """
    from unittest.mock import patch
    from finagent.bot import emirakis as EA
    from finagent.pulse import emir_kanit as K
    db = _gecici_db()
    alan = dict(sahip="ali", hesap="U1", conid="265598", yon="BUY", tur="LMT",
                adet=1, fiyat=1.0, sure="DAY", parmak_izi="p", durum="hazirlandi",
                kanal="komut")
    with patch.object(K, "topla", side_effect=RuntimeError("bozuk")):
        sid = EA._emir_satiri_ac(db, **alan)
    r = db.query("SELECT kanal, durum FROM emirler WHERE id = ?", (sid,))[0]
    assert (r["kanal"], r["durum"]) == ("komut", "hazirlandi"), dict(r)

    # Normal yolda topla GERCEKTEN cagriliyor (kablo).
    with patch.object(K, "topla") as t:
        sid2 = EA._emir_satiri_ac(db, **alan)
    assert t.call_args.args[1] == sid2, t.call_args
    db.close()


def test_emir_modullerinde_AYNI_ADLI_iki_fonksiyon_YOK():
    """
    OLCULEN KAZA (2026-10-02, gelistirme sirasinda): emirakis'e
    `_defter_satiri(db, **alanlar)` eklendi; dosyada ayni adli BASKA bir
    fonksiyon (emir no -> satir) daha asagida duruyordu ve Python ikinciyi
    tutar. `_hazirla` yanlis fonksiyonu cagirip HER emir hazirligini
    TypeError ile dusurecekti. Tek bir test yakaladi; sinifi kapatan bu.
    """
    import ast
    kok = Path(__file__).resolve().parents[1] / "src" / "finagent"
    for yol in ("bot/emirakis.py", "pulse/emir_kanit.py", "ibkr/emir.py"):
        agac = ast.parse((kok / yol).read_text(encoding="utf-8"))
        adlar = [d.name for d in agac.body
                 if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))]
        cift = {a for a in adlar if adlar.count(a) > 1}
        assert not cift, f"{yol}: ayni adli fonksiyon {cift}"


def test_emir_GECMISI_SONUCU_acikca_soyler_dolmayana_sonuc_YAZMAZ():
    """
    C2 (2026-10-02): "kabul" tek basina "tamamlandi" gibi okunuyordu;
    model dolmamis ETN emri icin uc cevapta "sonuc daha iyiydi" yazdi.
    Dolum alanlari kayitta vardi, arac dondurmuyordu.
    """
    tb, a, _ = _dd_araclar()
    db = tb.db
    iid = db.query("SELECT id FROM instruments WHERE symbol='QCOM'")[0]["id"]
    for durum, dolum in (("kabul", None), ("gerceklesti", 162.5),
                         ("dustu", None)):
        db.emir_yaz(sahip="ali", hesap="U1", instrument_id=iid,
                    conid="273544", yon="BUY", tur="LMT", adet=1, fiyat=1.0,
                    sure="DAY", parmak_izi=durum, durum=durum,
                    dolum_fiyat=dolum)
    v = _cagir_arac(a["ibkr_emir_gecmisi"], {"limit": 10})
    son = {e["durum"]: e for e in v["emirler"]}
    assert "DOLMADI" in son["kabul"]["sonuc"] and "YOK" in son["kabul"]["sonuc"]
    assert son["gerceklesti"]["sonuc"] == "DOLDU @ 162.5", son["gerceklesti"]
    assert son["gerceklesti"]["dolum_fiyat"] == 162.5
    assert "ISLEM OLMADI" in son["dustu"]["sonuc"]
    for alan in ("ibkr_durum", "dolum_ts", "dolum_komisyon"):
        assert alan in son["kabul"], alan


def test_emir_SONUCU_koddaki_HER_durumu_taniyor():
    """
    Emir defterine yazilan her `durum` degeri sonuc tablosunda olmali;
    tanimayan durum "BILINMEYEN DURUM" der — yeni bir durum eklenip
    tabloya yazilmazsa bu test duser.
    """
    import re
    from finagent.bot.tools import _EMIR_SONUCU
    kok = Path(__file__).resolve().parents[1] / "src" / "finagent"
    bulunan = set()
    for yol in ("bot/emirakis.py", "ibkr/mutabakat.py", "ibkr/emir.py"):
        bulunan |= set(re.findall(r'durum="([a-z_]+)"',
                                  (kok / yol).read_text(encoding="utf-8")))
    eksik = bulunan - set(_EMIR_SONUCU)
    assert not eksik, f"sonuc tablosunda olmayan emir durumu: {eksik}"


def test_emir_GECMISI_kaynagi_tasir_ve_bos_beyani_UYDURMAZ():
    from finagent.pulse.emir_kanit import beyan_yaz
    tb, a, _ = _dd_araclar()
    db = tb.db
    iid = db.query("SELECT id FROM instruments WHERE symbol='QCOM'")[0]["id"]
    s1 = db.emir_yaz(sahip="ali", hesap="U1", instrument_id=iid, conid="273544",
                     yon="BUY", tur="LMT", adet=1, fiyat=1.0, sure="DAY",
                     parmak_izi="a", durum="kabul", kanal="sohbet")
    db.query("INSERT INTO emir_kanit (emir_id, tur, ref_tablo, ref_id, ts, "
             "saat_once, ajan, yon, uyumlu) VALUES (?, 'oneri', 'predictions', "
             "1, '2026-10-01', 5.0, 'strateji', 'yukari', 1)", (s1,))
    db._conn.commit()
    v = _cagir_arac(a["ibkr_emir_gecmisi"], {"limit": 5})
    e = v["emirler"][0]
    assert e["kanal"] == "sohbet" and e["beyan"] is None, e
    assert e["kanit"][0]["ajan"] == "strateji", e
    assert "BILINMIYOR" in v["kaynak_notu"], v
    assert beyan_yaz(db, s1, "ali", "video")
    e = _cagir_arac(a["ibkr_emir_gecmisi"], {"limit": 5})["emirler"][0]
    assert e["beyan"] == "Video/reels", e


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum IBKR testleri gecti.")
