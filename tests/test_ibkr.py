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
    assert len(K.SECILEN) == 12
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
    assert v["eksik"] == [] and len(v["bulunan"]) == 12
    kayip = [K.ONEK + a for a in K.SECILEN if a != "create_alert"]
    v = anyio.run(lambda: K.arac_varligi_async(_sorgu=_sahte_varlik(eslesenler=kayip)))
    assert v["eksik"] == [K.ONEK + "create_alert"]
    v = anyio.run(lambda: K.arac_varligi_async(_sorgu=_sahte_varlik(eslesenler=[])))
    assert len(v["eksik"]) == 12, "baglayici dustugunde HEPSI eksik gorunmeli"
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("\nTum IBKR testleri gecti.")
