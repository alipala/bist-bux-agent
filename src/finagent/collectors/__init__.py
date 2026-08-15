from .base import BaseCollector, CollectorResult
from .isyatirim import IsYatirimCollector
from .kap import KapCollector
from .bist import BistCollector
from .bux import BuxCollector
from .edgar import EdgarCollector
from .indices import IndicesCollector
from .midas import MidasCollector
from .news import NewsCollector
from .prices import PriceCollector
from .stocknews import StockNewsCollector
from .xbrl import XbrlCollector

REGISTRY = {
    "isyatirim": IsYatirimCollector,
    "kap": KapCollector,
    "bist": BistCollector,
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
