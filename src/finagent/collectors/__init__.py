from .alphavantage import AlphaVantageCollector
from .base import BaseCollector, CollectorResult
from .isyatirim import IsYatirimCollector
from .kap import KapCollector
from .binance import BinanceCollector
from .bist import BistCollector
from .bistgecmis import BistGecmisCollector
from .bux import BuxCollector
from .edgar import EdgarCollector
from .cgfiyat import CoinGeckoFiyatCollector
from .coingecko import CoinGeckoCollector
from .ibkrkimlik import IbkrKimlikCollector
from .ibkrportfoy import IbkrPortfoyCollector
from .indices import IndicesCollector
from .kripto import KriptoIdentityCollector
from .makro import MakroCollector
from .kriptoevren import KriptoEvrenCollector
from .midas import MidasCollector
from .midasbilanco import MidasBilancoCollector
from .news import NewsCollector
from .prices import PriceCollector
from .saatlik import SaatlikCollector
from .stocknews import StockNewsCollector
from .strateji_fiyat import StratejiFiyatCollector
from .takvim import TakvimCollector
from .bilancotakvim import BilancoTakvimCollector
from .tuik import TuikCollector
from .tiingo import TiingoCollector
from .xbrl import XbrlCollector

REGISTRY = {
    "alphavantage": AlphaVantageCollector,
    "isyatirim": IsYatirimCollector,
    "kap": KapCollector,
    "bist": BistCollector,
    "bistgecmis": BistGecmisCollector,
    # Kripto zinciri — SIRA ONEMLI: evren -> kimlik -> fiyat -> tokenomik.
    "kriptoevren": KriptoEvrenCollector,
    "kripto": KriptoIdentityCollector,
    "binance": BinanceCollector,
    "cgfiyat": CoinGeckoFiyatCollector,
    "coingecko": CoinGeckoCollector,
    "bux": BuxCollector,
    "edgar": EdgarCollector,
    "ibkr": IbkrPortfoyCollector,
    # Kimlik cozumu fiyattan AYRI — kripto zincirindeki gibi
    # (kriptoevren -> kripto -> binance). conid bir kez cozulur.
    "ibkrkimlik": IbkrKimlikCollector,
    "indices": IndicesCollector,
    "midas": MidasCollector,
    "midasbilanco": MidasBilancoCollector,
    "makro": MakroCollector,
    "news": NewsCollector,
    "prices": PriceCollector,
    # Strateji evreni AYRI: gunde uc kez degil, YALNIZCA taramanin
    # kostugu kipte (nabiz) tazeleniyor. Gerekcesi modul basliginda.
    "strateji_fiyat": StratejiFiyatCollector,
    "saatlik": SaatlikCollector,
    "stocknews": StockNewsCollector,
    "takvim": TakvimCollector,
    "bilancotakvim": BilancoTakvimCollector,
    "tuik": TuikCollector,
    "tiingo": TiingoCollector,
    "xbrl": XbrlCollector,
}

# Her collector NEYI tazeler — kullaniciya/modele gorunen aciklama.
#
# NEDEN BURADA: sohbet araci `veri_topla` bu listeyi ELLE tasiyordu ve
# 19 kaynagin yalnizca 8'ini sayiyordu. Eksikler arasinda `isyatirim`
# vardi — BIST fiyatlarinin TEK kaynagi. Sonuc olculdu (2026-08-17):
# kullanici "fiyatlari tazele" dedi, model listede gordugu `prices`i
# calistirdi, `prices` BIST sembollerini borsa soneki olmadigi icin
# reddetti, ve model UC MESAJ boyunca "boru hatti bozuk" dedi. Bozuk
# olan boru hatti degil, LISTEYDI.
#
# Eksiksizligi duman testi zorunlu tutuyor.
KAPSAM = {
    "alphavantage": "ABD hisse fiyat serisi (yedek kaynak)",
    "isyatirim":    "BIST fiyat/kapanis serisi — BIST'in TEK fiyat kaynagi",
    "kap":          "KAP resmi sirket bildirimleri (BIST)",
    "bist":         "BIST sirket katalogu (fiyat DEGIL)",
    "bistgecmis":   "BIST DERIN gecmisi (Yahoo .IS) — isyatirim 13,5 ay verirken yillar; backtest icin",
    "kriptoevren":  "kripto evreni: hangi coin'ler kapsamda",
    "kripto":       "kripto kimligi: sembol -> CoinGecko/Binance eslesmesi",
    "binance":      "kripto gunluk + saatlik fiyat (Binance)",
    "cgfiyat":      "Binance'te olmayan coin'lerin fiyati (CoinGecko)",
    "coingecko":    "kripto tokenomik: arz, piyasa degeri, FDV",
    "bux":          "BUX enstruman katalogu",
    "edgar":        "SEC dosyalamalari (ABD)",
    "ibkr":         "IBKR portfoyu: pozisyonlar + para birimi basina nakit (oturum yoksa ATLAR)",
    "ibkrkimlik":   "sembol -> IBKR conid eslemesi (fiyat ve emrin ON KOSULU)",
    "indices":      "endeks uyelikleri ve endeks fiyatlari",
    "midas":        "Midas hisse sayfasi verisi (BIST)",
    "midasbilanco": "BIST bilanco/temettu/ortaklik verisi",
    "makro":        "makro panel: endeks, altin/gumus/petrol, kur, faiz, VIX",
    "news":         "genel haber akisi",
    "prices":       "BUX/ABD hisse fiyat serisi (Yahoo) — BIST'i KAPSAMAZ",
    "strateji_fiyat": "Donchian evreninin (S&P 500 + Nasdaq 100) fiyat serisi — YALNIZCA nabiz kipinde",
    "saatlik":      "SAATLIK hisse serisi (BIST .IS + ABD) — gun ici katmanin temeli",
    "stocknews":    "hisse haberleri, kaynak kademesiyle",
    "takvim":       "ekonomik takvim: FOMC, TCMB, ABD CPI/istihdam/PCE/GDP/PPI (FRED); TUIK engelli, her kosuda yeniden denenir",
    "bilancotakvim": "ABD sirketlerinin bilanco aciklama gunleri (Alpha Vantage + portfoy icin Yahoo, saatiyle)",
    "tuik":         "TUIK makro gostergeleri (SDMX): Yi-UFE, issizlik, ekonomik guven",
    "tiingo":       "ABD hisse fiyat serisi (yedek kaynak)",
    "xbrl":         "ABD hisse temel verisi (XBRL)",
}

__all__ = ["REGISTRY", "KAPSAM", "BaseCollector", "CollectorResult"]
