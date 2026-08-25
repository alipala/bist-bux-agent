"""
Sembol -> IBKR `conid` cozumu. TAHMIN ETMEZ, DOGRULAR.

NEDEN SEMBOL YETMIYOR
---------------------
IBKR emirde ve fiyatta sembol degil `conid` istiyor, ve ayni sembol
BIRDEN COK SIRKETE denk gelebiliyor. Canli olcum (2026-08-25,
`/trsrv/stocks?symbols=AMZN`) UC ayri kayit dondurdu:

    AMAZON.COM INC          -> NASDAQ 3691937 / MEXI / EBS
    LS 1X AMZN              -> LSEETF 493546040     <-- KALDIRACLI ETF
    AMAZON.COM INC - CDR    -> TSE 532497536        <-- Kanada depo sertifikasi

Ucu de "AMZN" sembolunden geliyor. Yanlisini secmek, Amazon almak isterken
kaldiracli bir ETF almaktir. Bu deponun kendi vakalari ayni sinifta:
AVTX -> Avalo, RBOT -> Vicarious.

AD KAPISI GEREKLI AMA YETMIYOR — OLCULDU
-----------------------------------------
Katalog adiyla karsilastirinca (`research.identity.ayni_sirket`):

    'Amazon.com, Inc.' vs 'LS 1X AMZN'               -> eslesmedi  ✓ elendi
    'Amazon.com, Inc.' vs 'AMAZON.COM INC - CDR'     -> ESLESTI    ✗ elenmedi
    'ASML Holding N.V.' vs 'ASML HOLDING NV'         -> ESLESTI  (Amsterdam)
    'ASML Holding N.V.' vs 'ASML HOLDING NV-NY REG'  -> ESLESTI  (NASDAQ)

Yani ad kapisi kaldiracli ETF'i eliyor ama CDR'yi ve ayni adi tasiyan
IKINCI borsayi elemiyor. ASML'de AYNI ADLA iki kayit var (Amsterdam ve
Tokyo). Ad tek basina yeterli olsaydi bu ikisi ayirt edilemezdi.

BORSA KURALI: ABD LISTESI TERCIH, VE TEKLIK SART
------------------------------------------------
Katalogdaki soneksiz sembol ('ASML', 'AMZN') zaten Yahoo'da ABD listesine
denk geliyor ve `prices` serisi oradan geliyor. Ayni enstrumana IBKR
tarafinda baska bir borsayi baglamak, fiyat serisi ile emir enstrumaninin
AYRISMASI demekti — bu deponun en pahali hata sinifi.

Ayrica ucretsiz gercek zamanli IBKR verisi yalnizca ABD listelerini
kapsiyor.

    1. `assetClass == "STK"` olanlar
    2. adi katalog adiyla ORTUSENLER (ad kapisi)
    3. onlarin sozlesmeleri arasinda `isUS: true` olan TEK BIR tane varsa -> o
    4. hic ABD listesi yoksa ve TOPLAM tek sozlesme varsa -> o
    5. baska her durumda -> COZULMEZ, sebebiyle raporlanir

Belirsizi bos birakmak, yanlis baglamaktan iyidir: eksik conid emir
gondermeyi ENGELLER, yanlis conid YANLIS HISSEYI aldirir.

KATALOGDA AD YOKSA COZULMEZ
---------------------------
`prices._ad_dogrulayarak` ile ayni kural: "Katalogda ADI OLMAYAN kagitta
da yazilmaz: dogrulayacak bir sey yoksa dogrulanmis sayilmaz."

SONEK ATILMIYOR — SONEK BIR BILGI, GURULTU DEGIL
------------------------------------------------
Katalogda Yahoo sonekli semboller var ('ABN.AS', '4GLD.DE', 'EIMI.L') ve
IBKR bu bicimi TANIMIYOR: sonekli sorgu bos liste donduruyor.

Soneki KORLEMESINE atmak tehlikeli olurdu. Olculdu (2026-08-26):

    'BRK'  -> BROOKS MACDONALD GROUP PLC   (LSE)
              BROOKSIDE ENERGY LTD          (ASX)
              BERKSHIRE HATHAWAY INC-CDR    (TSE)
              SSIF BRK FINANCIAL GROUP SA   (BVB)

'BRK.B' bir BORSA soneki degil, HISSE SINIFI. Soneki atsaydik dort ayri
sirketle karsilasirdik ve hicbiri aradigimiz degil.

Ama sonek gercekten borsa gosteriyorsa BILGIDIR ve atilmaz, KULLANILIR:

    'ABN.AS'  -> 'ABN'  + borsa AEB  sarti -> ABN AMRO BANK NV-CVA  ✓
    '4GLD.DE' -> '4GLD' + borsa IBIS sarti -> XETRA-GOLD            ✓
    'IWDA.AS' -> 'IWDA' + borsa AEB  sarti -> iki borsadan DOGRUSU  ✓
                 ('IWDA' tek basina LSEETF ve AEB donduruyor;
                  sonek olmadan hangisi oldugu BELIRSIZ kalirdi)

Yani kural: sonek YALNIZCA asagidaki haritada varsa taban sembol
denenir, ve o zaman da borsa sarti EK BIR KAPIDIR — ad kapisinin
yerine gecmez, ustune biner. Haritada olmayan sonek ('.B') hic
denenmez.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..research.identity import ayni_sirket
from .istemci import IbkrHatasi, Istemci

log = logging.getLogger(__name__)

# `/trsrv/stocks` virgulle ayrilmis liste kabul ediyor. Ilan edilmis bir
# uc-bazli hiz siniri yok (genel 10 istek/sn gecerli); yiginlamak yine de
# istek sayisini dusuruyor.
YIGIN = 20

# Yahoo soneki -> o sonege karsilik gelen IBKR borsa kodlari.
# Kaynak: IBKR piyasa verisi sayfasindaki borsa listesi ("Borsa Italiana
# BVME/IDEM", "Frankfurt Stock Exchange and XETRA FWB/IBIS/XETRA",
# "Euronext AEB/SBF/MATIF/BELFOX", "LSE LSE", "SWISS Exchange EBS/VIRTX").
#
# BURAYA YALNIZCA BORSA SONEKLERI GIRER. '.B' gibi hisse SINIFI sonekleri
# ASLA — 'BRK.B' icin taban sembol dort ayri sirket donduruyor.
SONEK_BORSA: dict[str, frozenset[str]] = {
    "AS": frozenset({"AEB"}),                       # Amsterdam
    "DE": frozenset({"IBIS", "FWB", "XETRA"}),      # Xetra / Frankfurt
    "L": frozenset({"LSE", "LSEETF"}),              # Londra
    "MI": frozenset({"BVME", "BVME.ETF"}),          # Milano
    "PA": frozenset({"SBF"}),                       # Paris
    "SW": frozenset({"EBS", "VIRTX"}),              # Isvicre
    "BR": frozenset({"ENEXT.BE"}),                  # Brüksel
}


def _taban_ve_borsa(sembol: str) -> tuple[str, frozenset[str] | None]:
    """
    'ABN.AS' -> ('ABN', {'AEB'})   |   'BRK.B' -> ('BRK.B', None)

    Sonek haritada yoksa sembol OLDUGU GIBI birakilir; taban denemesi
    yapilmaz.
    """
    if "." not in sembol:
        return sembol, None
    taban, _, sonek = sembol.rpartition(".")
    borsalar = SONEK_BORSA.get(sonek.upper())
    if not taban or borsalar is None:
        return sembol, None
    return taban, borsalar


@dataclass
class ConidSonucu:
    sembol: str
    conid: str | None
    borsa: str | None
    sebep: str

    @property
    def cozuldu(self) -> bool:
        return self.conid is not None


def _kayitlari_coz(sembol: str, katalog_adi: str | None, kayitlar,
                   borsalar: frozenset[str] | None = None) -> ConidSonucu:
    """
    Tek sembolun IBKR yanitini karara cevirir. AG ERISIMI YOK.

    `borsalar` verildiyse (sonekli sembol) secim O BORSALARLA sinirli ve
    ABD tercihi UYGULANMAZ: sonek zaten hangi borsa oldugunu soyluyor.
    """
    if not katalog_adi or not str(katalog_adi).strip():
        return ConidSonucu(sembol, None, None, "katalogda ad yok — dogrulanamaz")
    if not isinstance(kayitlar, list) or not kayitlar:
        return ConidSonucu(sembol, None, None, "IBKR'de bulunamadi")

    eslesme = []
    adlar = []
    for k in kayitlar:
        if not isinstance(k, dict):
            continue
        if str(k.get("assetClass") or "").upper() != "STK":
            continue
        ad = str(k.get("name") or "").strip()
        adlar.append(ad)
        if ayni_sirket(katalog_adi, ad):
            for s in (k.get("contracts") or []):
                if isinstance(s, dict) and s.get("conid"):
                    eslesme.append((ad, s))

    if not eslesme:
        gorulen = ", ".join(a for a in adlar[:3] if a) or "yok"
        return ConidSonucu(sembol, None, None,
                           f"ad eslesmedi (bizde {katalog_adi!r}, IBKR: {gorulen})")

    if borsalar is not None:
        # SONEK BORSAYI SOYLUYOR. ABD tercihi burada uygulanmaz: 'IWDA.AS'
        # icin taban sembol LSEETF ve AEB donduruyor, ve dogru olan
        # sonegin gosterdigi (AEB). ABD tercihi devrede olsaydi bu sembol
        # sessizce YANLIS borsaya baglanabilirdi.
        uygun = [(a, s) for a, s in eslesme
                 if str(s.get("exchange") or "").upper() in borsalar]
        if len(uygun) == 1:
            ad, s = uygun[0]
            return ConidSonucu(sembol, str(s["conid"]), s.get("exchange"),
                               f"sonek borsasi ({ad}, {s.get('exchange')})")
        if not uygun:
            gorulen = ", ".join(str(s.get("exchange")) for _, s in eslesme)
            return ConidSonucu(sembol, None, None,
                               f"sonek borsasi ({'/'.join(sorted(borsalar))}) "
                               f"yok; gorulen: {gorulen}")
        return ConidSonucu(sembol, None, None,
                           "sonek borsasinda birden cok sozlesme")

    abd = [(a, s) for a, s in eslesme if s.get("isUS")]
    if len(abd) == 1:
        ad, s = abd[0]
        return ConidSonucu(sembol, str(s["conid"]), s.get("exchange"),
                           f"ABD listesi ({ad})")
    if len(abd) > 1:
        borsalar = ", ".join(str(s.get("exchange")) for _, s in abd)
        return ConidSonucu(sembol, None, None,
                           f"birden cok ABD listesi: {borsalar}")
    if len(eslesme) == 1:
        ad, s = eslesme[0]
        return ConidSonucu(sembol, str(s["conid"]), s.get("exchange"),
                           f"tek liste ({ad}, {s.get('exchange')})")
    borsalar = ", ".join(str(s.get("exchange")) for _, s in eslesme)
    return ConidSonucu(sembol, None, None,
                       f"ABD listesi yok, birden cok borsa: {borsalar}")


def conid_coz(istemci: Istemci, hedefler: dict[str, str | None]) -> list[ConidSonucu]:
    """
    `{sembol: katalog_adi}` -> sonuc listesi. Yiginlar halinde sorar.

    Ag hatasi TUM yigini dusurmez: yigin basarisiz olursa o yigindaki
    semboller sebebiyle isaretlenir, digerleri devam eder.
    """
    cikti: list[ConidSonucu] = []
    semboller = [s for s in hedefler if s and s.strip()]

    # Her sembol icin IBKR'ye SORULACAK ad ve (varsa) borsa sarti.
    # Sonekli sembolde taban sorulur ama sonuc sonegin borsasiyla
    # SINIRLANIR — sonek atilmiyor, KULLANILIYOR.
    sorgu: dict[str, tuple[str, frozenset[str] | None]] = {}
    for s in semboller:
        sorgu[s] = _taban_ve_borsa(s)

    # Ayni taban sembol birden cok katalog sembolunden gelebilir
    # (or. 'SXLE.AS' ve 'SXLE.L'); tek kez soruluyor.
    tabanlar = sorted({t for t, _ in sorgu.values()})
    yanit: dict[str, object] = {}
    for i in range(0, len(tabanlar), YIGIN):
        parca = tabanlar[i:i + YIGIN]
        try:
            veri = istemci.get("/trsrv/stocks", {"symbols": ",".join(parca)})
        except IbkrHatasi as e:
            log.info("[ibkr] conid yigini basarisiz (%s): %s", len(parca), e)
            for t in parca:
                yanit[t] = e
            continue
        if not isinstance(veri, dict):
            for t in parca:
                yanit[t] = "bicim"
            continue
        for t in parca:
            yanit[t] = veri.get(t)

    for s in semboller:
        taban, borsalar = sorgu[s]
        v = yanit.get(taban)
        if isinstance(v, IbkrHatasi):
            cikti.append(ConidSonucu(s, None, None, f"istek basarisiz: {v}"))
        elif v == "bicim":
            cikti.append(ConidSonucu(s, None, None, "yanit bicimi beklenmedik"))
        else:
            cikti.append(_kayitlari_coz(s, hedefler.get(s), v, borsalar))
    return cikti
