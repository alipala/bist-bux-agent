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


def test_yaris_varsayilan_olarak_KAPALI():
    """
    `compete: true` baska oturumlari DUSURUR. Varsayilan acik olsaydi,
    Ali tarayicidan Client Portal'a girdiginde bot onu sessizce disari
    atardi — teshisi en zor ariza turu.
    """
    o = Oturum(_istemci(SahteOturum({"ssodh/init": SahteYanit(200, {"authenticated": True})})))
    o.kur()
    assert o._yaris is False


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


def test_govde_DIZI_olarak_gider():
    """
    Yeni emirde govde JSON DIZISI, degistirmede NESNE. Ayni aile, farkli
    sekil — karistirmak sessiz 400 uretir.
    """
    kaydedilen = {}

    class Kaydeden(SahteOturum):
        def request(self, yontem, url, **kw):
            kaydedilen["govde"] = kw.get("json")
            return SahteYanit(200, [{"order_id": "1", "order_status": "S"}])

    E.gonder(_istemci(Kaydeden()), ISTEK, _fis())
    g = kaydedilen["govde"]
    assert isinstance(g, list), "govde dizi degil"
    assert g[0]["conid"] == 265598 and isinstance(g[0]["conid"], int)
    assert g[0]["side"] == "BUY" and g[0]["orderType"] == "LMT"
    assert g[0]["tif"] == "DAY" and g[0]["price"] == 165.0


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


def test_emir_modulu_LLM_ARAC_YUZEYINE_girmiyor():
    """
    ALTIN KURAL. Model oneri uretir, emir GONDERMEZ. `tools.py` bu
    modulu ice aktarmamali.
    """
    kaynak = (KOK / "src" / "finagent" / "bot" / "tools.py").read_text()
    for yasak in ("ibkr.emir", "ibkr import emir", "from ..ibkr.emir"):
        assert yasak not in kaynak, f"tools.py emir modulunu ice aktariyor: {yasak}"


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
        "sembol": "NVDA", "yon": "BUY", "adet": 5.0, "fiyat": 214.5, "tur": "LMT"}
    assert EA.komut_coz("ko sat 3")["tur"] == "MKT"
    for kotu in ("", "NVDA", "NVDA AL", "NVDA TUT 5", "NVDA AL x",
                 "NVDA AL 0", "NVDA AL -3", "NVDA AL 5 abc", "NVDA AL 5 0"):
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

    assert "teyit istiyor" in m
    assert "HENUZ CALISMIYOR" in m
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


def test_emir_akisi_LLM_ARAC_YUZEYINDE_YOK():
    """Model emir baslatamamali."""
    kaynak = (KOK / "src" / "finagent" / "bot" / "tools.py").read_text()
    assert "emirakis" not in kaynak
    assert "ibkr_emir" not in kaynak


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum IBKR testleri gecti.")
