"""Автономные сборщики данных с маркетплейсов.

Каждый сборщик работает в отдельном subprocess (сеть не попадает в ядро).
Результат — JSON-файлы в папке приёма, которые FolderWatcher подхватывает
автоматически. Прокси-пул, rate limiting и ретраи — в base.py.

Подключённые источники: WB, OZON, AVITO, FARPOST, ALIEXPRESS, ALIEXPRESS_RU.
"""

from .base import BaseCollector
from .proxy_pool import ProxyPool
from .wb import WBCollector
from .ozon import OzonCollector
from .avito import AvitoCollector
from .farpost import FarpostCollector
from .aliexpress import AliExpressCollector, AliExpressRUCollector
from .runner import run_collector_subprocess

__all__ = [
    "BaseCollector",
    "ProxyPool",
    "WBCollector",
    "OzonCollector",
    "AvitoCollector",
    "FarpostCollector",
    "AliExpressCollector",
    "AliExpressRUCollector",
    "run_collector_subprocess",
]

# Реестр: source_key → (CollectorClass, display_name)
COLLECTOR_REGISTRY = {
    "WB": (WBCollector, "Wildberries"),
    "OZON": (OzonCollector, "Ozon"),
    "AVITO": (AvitoCollector, "Avito"),
    "FARPOST": (FarpostCollector, "Farpost"),
    "ALIEXPRESS": (AliExpressCollector, "AliExpress"),
    "ALIEXPRESS_RU": (AliExpressRUCollector, "AliExpress RU"),
}
