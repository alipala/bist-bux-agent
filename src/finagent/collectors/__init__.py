from .base import BaseCollector, CollectorResult
from .isyatirim import IsYatirimCollector
from .kap import KapCollector
from .binance import BinanceCollector
from .bist import BistCollector
from .bux import BuxCollector
from .edgar import EdgarCollector
from .coingecko import CoinGeckoCollector
from .indices import IndicesCollector
from .kripto import KriptoIdentityCollector
from .midas import MidasCollector
from .news import NewsCollector
from .prices import PriceCollector
from .stocknews import StockNewsCollector
from .xbrl import XbrlCollector

REGISTRY = {
    "isyatirim": IsYatirimCollector,
    "kap": KapCollector,
    "bist": BistCollector,
    # Kripto zinciri — SIRA ONEMLI: kimlik once cozulur.
    "kripto": KriptoIdentityCollector,
    "binance": BinanceCollector,
    "coingecko": CoinGeckoCollector,
    "bux": BuxCollector,
    "edgar": EdgarCollector,
    "indices": IndicesCollector,
    "midas": MidasCollector,
    "news": NewsCollector,
    "prices": PriceCollector,
    "stocknews": StockNewsCollector,
    "xbrl": XbrlCollector,
}
__all__ = ["REGISTRY", "BaseCollector", "CollectorResult"]
