from .alphavantage import AlphaVantageCollector
from .base import BaseCollector, CollectorResult
from .isyatirim import IsYatirimCollector
from .kap import KapCollector
from .binance import BinanceCollector
from .bist import BistCollector
from .bux import BuxCollector
from .edgar import EdgarCollector
from .cgfiyat import CoinGeckoFiyatCollector
from .coingecko import CoinGeckoCollector
from .indices import IndicesCollector
from .kripto import KriptoIdentityCollector
from .makro import MakroCollector
from .kriptoevren import KriptoEvrenCollector
from .midas import MidasCollector
from .midasbilanco import MidasBilancoCollector
from .news import NewsCollector
from .prices import PriceCollector
from .stocknews import StockNewsCollector
from .takvim import TakvimCollector
from .tuik import TuikCollector
from .tiingo import TiingoCollector
from .xbrl import XbrlCollector

REGISTRY = {
    "alphavantage": AlphaVantageCollector,
    "isyatirim": IsYatirimCollector,
    "kap": KapCollector,
    "bist": BistCollector,
    # Kripto zinciri — SIRA ONEMLI: evren -> kimlik -> fiyat -> tokenomik.
    "kriptoevren": KriptoEvrenCollector,
    "kripto": KriptoIdentityCollector,
    "binance": BinanceCollector,
    "cgfiyat": CoinGeckoFiyatCollector,
    "coingecko": CoinGeckoCollector,
    "bux": BuxCollector,
    "edgar": EdgarCollector,
    "indices": IndicesCollector,
    "midas": MidasCollector,
    "midasbilanco": MidasBilancoCollector,
    "makro": MakroCollector,
    "news": NewsCollector,
    "prices": PriceCollector,
    "stocknews": StockNewsCollector,
    "takvim": TakvimCollector,
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
    "kriptoevren":  "kripto evreni: hangi coin'ler kapsamda",
    "kripto":       "kripto kimligi: sembol -> CoinGecko/Binance eslesmesi",
    "binance":      "kripto gunluk + saatlik fiyat (Binance)",
    "cgfiyat":      "Binance'te olmayan coin'lerin fiyati (CoinGecko)",
    "coingecko":    "kripto tokenomik: arz, piyasa degeri, FDV",
    "bux":          "BUX enstruman katalogu",
    "edgar":        "SEC dosyalamalari (ABD)",
    "indices":      "endeks uyelikleri ve endeks fiyatlari",
    "midas":        "Midas hisse sayfasi verisi (BIST)",
    "midasbilanco": "BIST bilanco/temettu/ortaklik verisi",
    "makro":        "makro panel: endeks, altin/gumus/petrol, kur, faiz, VIX",
    "news":         "genel haber akisi",
    "prices":       "BUX/ABD hisse fiyat serisi (Yahoo) — BIST'i KAPSAMAZ",
    "stocknews":    "hisse haberleri, kaynak kademesiyle",
    "takvim":       "ekonomik takvim (FOMC); TUIK/TCMB/BLS engelli, her kosuda yeniden denenir",
    "tuik":         "TUIK makro gostergeleri (SDMX): Yi-UFE, issizlik, ekonomik guven",
    "tiingo":       "ABD hisse fiyat serisi (yedek kaynak)",
    "xbrl":         "ABD hisse temel verisi (XBRL)",
}

__all__ = ["REGISTRY", "KAPSAM", "BaseCollector", "CollectorResult"]
