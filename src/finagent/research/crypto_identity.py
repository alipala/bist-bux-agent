"""
Kripto kimlik cozumu — sembolun HANGI coin oldugunu kesinlestirir.

NEDEN HISSE TARAFINDAN DAHA KRITIK
----------------------------------
Kriptoda ticker cakismasi kural disi degil, NORMDUR. Ayni sembolu kullanan
onlarca token vardir ve hicbir merkezi otorite bunu engellemez:

    "ROSE"  -> Oasis Network  ama baska aglarda ayni sembollu tokenlar var
    "SOL"   -> Solana         / Solice / SOLA ...
    "GVT"   -> Genesis Vision / Genevieve / ...

Hisse tarafinda ticker tahmini "Avantium" icin Avalo Therapeutics'in 500
gunluk fiyatini cekmisti. Kriptoda ayni hata daha kolay yapilir ve daha
sessizdir. Bu yuzden ayni kural: SEMBOL TEK BASINA YETMEZ, AD DA TUTMALI.

IKI OTORITE
-----------
* Binance `exchangeInfo` — bir ciftin GERCEKTEN islem gordugunun kaniti.
  Fiyat serisi buradan gelecegi icin belirleyici olan budur.
* CoinGecko `/coins/markets` — sembol -> KANONIK coin. Piyasa degeri ve arz
  buradan gelir; yanlis coin'e baglanirsa piyasa degeri de yanlis olur.

Ikisi ayni coin'i gostermiyorsa kimlik "eslesmedi" isaretlenir ve
HICBIR collector o enstrumandan veri cekmez.

NEDEN /coins/list DEGIL /coins/markets
--------------------------------------
Ilk denemede `/coins/list` kullanildi ve OLCULEREK yanlis oldugu goruldu:
sarmalanmis (wrapped/bridged/peg) klonlar hem ayni sembolu hem benzer adi
tasidigi icin ad eslestirmesi onlari kabul ediyordu.

    ADA -> binance-peg-cardano                    (dogrusu: cardano)
    ETH -> bridged-binance-peg-ethereum-opbnb     (dogrusu: ethereum)
    BNB -> anubis-bridged-bnb-anubis              (dogrusu: binancecoin)

Bu klonlarin piyasa degeri gercek coin'in binde biri kadardir; oradan
gelen "piyasa degeri" sayisi tamamen yanlis ama tutarli gorunurdu.
`/coins/markets` sembol basina piyasa degerine gore SIRALI donuyor, yani
kanonik coin'i kendisi veriyor. Ad kontrolu yine de korunuyor: otoriteyi
degistirdik ama dogrulamayi kaldirmadik.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

BINANCE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
CG_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

# Fiyatlandirma birimi. USDT en derin likiditeye sahip; USDC yedek.
KOTASYON = ("USDT", "USDC", "BUSD")

# Kripto degil ama Binance'te cift olarak gorunen fiat/stabil semboller.
# Bunlar "coin analizi" kapsamina girmez: piyasa degeri/arz kavrami yok.
FIAT = {"EUR", "TRY", "USD", "GBP", "BRL", "ARS", "JPY"}
STABIL = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"}


def _ad_anahtari(ad: str | None) -> str:
    """'Oasis Network' ~ 'oasis network' ~ 'Oasis  Network'."""
    if not ad:
        return ""
    return " ".join("".join(
        c if c.isalnum() else " " for c in str(ad).casefold()).split())


def _ayni_coin(bizim_ad: str | None, cg_ad: str | None, sembol: str) -> bool:
    """
    Adlar ayni coin'i mi gosteriyor?

    Kabul edilen tek esneklik SUS EKLERIDIR ("Enjin Coin" ~ "Enjin",
    "Oasis Network" ~ "Oasis"). Bunun otesi REDDEDILIR.

    ALT-DIZE KURALI BILEREK YOK. Once "biri digerini iceriyorsa kabul"
    yaziliydi ve OLCULEREK yanlis cikti: BTC sembolune "Bitcoin Cash" adi
    verildiginde ("bitcoin" ⊂ "bitcoin cash") KABUL ediyordu. Oysa Bitcoin
    Cash BCH'dir, BTC degil. "Cash", "Classic", "Gold", "SV" gibi kelimeler
    sus degil AYIRT EDICIDIR — tam da ayirmasi gereken seyi siliyordu.

    Ikisi de bossa kabul EDILMEZ: ad yoksa dogrulama da yok demektir.
    """
    a, b = _ad_anahtari(bizim_ad), _ad_anahtari(cg_ad)
    if not a or not b:
        return False
    if a == b:
        return True
    ekler = {"coin", "token", "network", "protocol", "chain"}
    ax = " ".join(w for w in a.split() if w not in ekler)
    bx = " ".join(w for w in b.split() if w not in ekler)
    return bool(ax) and ax == bx


class CryptoResolver:
    """
    Binance ciftleri ve CoinGecko coin listesini BIR KEZ indirir, sonra
    her sembol icin bellekten cozer. Iki liste de buyuk (~3k cift,
    ~18k coin) — sembol basina istek atmak hem yavas hem gereksiz.
    """

    def __init__(self, http):
        self.http = http
        self._ciftler: dict[str, list[str]] | None = None   # base -> [pair, ...]
        self._cg: dict[str, dict] = {}                      # symbol -> kanonik coin

    # ------------------------------------------------------------------
    def ciftler(self) -> dict[str, list[str]]:
        if self._ciftler is None:
            r = self.http.get(BINANCE_INFO, timeout=30)
            r.raise_for_status()
            out: dict[str, list[str]] = {}
            for s in r.json().get("symbols", []):
                if s.get("status") != "TRADING":
                    continue
                if s.get("quoteAsset") not in KOTASYON:
                    continue
                out.setdefault(s["baseAsset"].upper(), []).append(s["symbol"])
            self._ciftler = out
            log.info("[kripto] Binance'te islem goren %d taban varlik", len(out))
        return self._ciftler

    # COINGECKO ISTEK BASINA EN FAZLA 50 SEMBOL KABUL EDIYOR.
    # Olculdu (2026-08-20) dogrudan API'ye sorularak:
    #   symbols=btc,eth,aave         -> 200
    #   symbols=<60 sembol>          -> 400
    #   {"error":"The 'include_tokens=all' parameter allows a maximum of
    #    50 symbols per request..."}
    # Sinirin ALTINDA kalinmali; 40, listeye birkac coin eklenince
    # sessizce tekrar patlamamak icin pay birakiyor.
    CG_AZAMI_SEMBOL = 40

    def coingecko(self, semboller) -> dict[str, dict]:
        """
        Verilen sembollerin KANONIK coin kaydini toplu ceker ve onbellekler.

        PARCALI GIDER. Once tek istekte gidiyordu ve kripto evreni 50
        sembolu asinca butun cagri 400 ile duserdi. Olculen zarar
        (2026-08-20) bir collector'la sinirli DEGILDI: `kripto` 6/6
        kosuda `error` verdi, ve kimlik cozumu bu cagriya bagli oldugu
        icin `binance` ile `coingecko` da her kosuda "kimlik yok,
        atlandi: BDX, BGB, BTW, CC, CRO, ETHFI, EUR, FIGR_HELOC" dedi.
        Yani TEK bir sinir asimi UC collector'i sessizce sakatliyordu.

        BIR PARCANIN HATASI DIGERLERINI DUSURMEZ: kalanlar yazilir ve
        hangi parcanin dustugu loglanir. Hepsini birden kaybetmek, bu
        arizanin ta kendisiydi.
        """
        eksik = sorted({(s or "").upper() for s in semboller} - set(self._cg) - {""})
        if not eksik:
            return self._cg

        n = self.CG_AZAMI_SEMBOL
        parcalar = [eksik[i:i + n] for i in range(0, len(eksik), n)]
        cozulen, dusen = 0, 0
        for parca in parcalar:
            try:
                r = self.http.get(CG_MARKETS, timeout=45, params={
                    "vs_currency": "usd",
                    "symbols": ",".join(s.lower() for s in parca),
                    "order": "market_cap_desc",
                    "per_page": 250,
                })
                r.raise_for_status()
            except Exception as e:                # noqa: BLE001
                dusen += len(parca)
                log.warning("[kripto] CoinGecko parcasi alinamadi (%d sembol): %s",
                            len(parca), e)
                continue
            for c in r.json() or []:
                sym = (c.get("symbol") or "").upper()
                onceki = self._cg.get(sym)
                # Ayni sembolde birden fazla kayit gelirse piyasa degeri BUYUK
                # olani kanonik kabul edilir — klonlar hep kucuktur.
                if onceki is None or (c.get("market_cap") or 0) > (onceki.get("market_cap") or 0):
                    self._cg[sym] = c
            cozulen += len(parca)

        for s in eksik:                       # bulunamayanlari da onbellege yaz
            self._cg.setdefault(s, {})
        log.info("[kripto] CoinGecko: %d sembol %d parcada cozuldu%s",
                 cozulen, len(parcalar),
                 f" ({dusen} sembol dusen parcada kaldi)" if dusen else "")
        return self._cg

    # ------------------------------------------------------------------
    def coz(self, sembol: str, ad: str | None = None) -> dict:
        """
        Doner: {status, symbol, name, pair, coingecko_id, note}

        status:
          dogrulandi — cift Binance'te islem goruyor VE ad CoinGecko ile tutuyor
          fiat       — EUR/TRY gibi; coin degil, analiz kapsami disi
          stabil     — USDT/USDC; fiyat analizi anlamsiz
          cift_yok   — Binance'te islem goren cift yok (delisted olabilir)
          ad_yok     — cift var ama dogrulanacak ad yok -> ELLE onay gerekir
          eslesmedi  — ad CoinGecko'daki coin ile TUTMUYOR -> veri cekilmez
        """
        s = (sembol or "").strip().upper()
        if not s:
            return {"status": "eslesmedi", "symbol": s, "note": "sembol bos"}

        if s in FIAT:
            return {"status": "fiat", "symbol": s, "name": ad,
                    "note": "fiat para birimi — coin analizi uygulanmaz"}
        if s in STABIL:
            return {"status": "stabil", "symbol": s, "name": ad,
                    "note": "stabilcoin — fiyat analizi anlamsiz"}

        ciftler = self.ciftler().get(s) or []
        if not ciftler:
            # BINANCE CIFTI YOK, AMA COINGECKO KAYDI OLABILIR — ve
            # genellikle VARDIR. 'CRYPTO' venue'su tam da bunun icin
            # var: ilk 100'de olup Binance'te listelenmeyen referans
            # coinler (HYPE, XMR, CRO, KAS, OKB...).
            #
            # Bu dal eskiden CoinGecko'ya HIC bakmadan donuyordu, yani
            # `coingecko_id` asla yazilmiyordu. Olculdu (2026-08-20):
            # 24 `cift_yok` kaydinin 24'unde de id BOSTU ve `coingecko`
            # collector'i "kimlik yok, atlandi" deyip geciyordu — yani
            # referans evreninin tokenomigi ve fiyati BUTUNUYLE eksikti,
            # hicbir yerde soylenmeden.
            #
            # Ad dogrulamasi GEVSETILMIYOR: id ancak ad tutuyorsa
            # yaziliyor. Klon-coin tuzagi burada da gecerli.
            coin = self.coingecko([s]).get(s) or {}
            out = {"status": "cift_yok", "symbol": s, "name": ad,
                   "note": "Binance'te islem goren USDT/USDC cifti yok"}
            if coin and ad and _ayni_coin(ad, coin.get("name"), s):
                out["coingecko_id"] = coin.get("id")
                out["name"] = coin.get("name")
                out["market_cap_rank"] = coin.get("market_cap_rank")
                out["note"] += " — CoinGecko kaydi VAR, tokenomik cekilebilir"
            return out
        # USDT ciftini tercih et; yoksa ilk bulunan.
        cift = next((p for p in ciftler if p.endswith("USDT")), ciftler[0])

        coin = self.coingecko([s]).get(s) or {}
        if not coin:
            return {"status": "eslesmedi", "symbol": s, "name": ad, "pair": cift,
                    "note": "CoinGecko'da bu sembol yok — piyasa degeri dogrulanamaz"}

        if not ad:
            # Cift var ama dogrulayacak ad yok. Kanonik aday bulunmus olsa
            # bile OTOMATIK KABUL ETMIYORUZ — dogrulama, adin tutmasidir.
            return {"status": "ad_yok", "symbol": s, "pair": cift,
                    "adaylar": [f"{coin.get('id')} ({coin.get('name')})"],
                    "note": "ad bilinmiyor — /kimlik ile onayla"}

        if _ayni_coin(ad, coin.get("name"), s):
            return {"status": "dogrulandi", "symbol": s, "name": coin.get("name"),
                    "pair": cift, "coingecko_id": coin.get("id"),
                    "market_cap_rank": coin.get("market_cap_rank"), "note": None}

        return {"status": "eslesmedi", "symbol": s, "name": ad, "pair": cift,
                "adaylar": [f"{coin.get('id')} ({coin.get('name')})"],
                "note": f"ad tutmuyor: bizde '{ad}', CoinGecko'da "
                        f"'{coin.get('name')}'"}
