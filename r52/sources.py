"""Точка подключения сборщиков данных с маркетплейсов.

Подключает 6 источников: WB, OZON, AVITO, FARPOST, ALIEXPRESS, ALIEXPRESS_RU.
Сборщики работают в subprocess (сеть изолирована от ядра).
Результат — JSON-файлы в папке приёма (FolderWatcher).

Правила:
- только публичные GET-запросы, без cookie/токенов;
- прокси-ротация + rate limiting (8–20 с между запросами);
- остановка при CAPTCHA / 403 / 429 без повторов в обход;
- сеть только в subprocess: ядро остаётся без внешней сети;
- результат проходит нормализацию и проверки intake/eye.py.
"""

import logging
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger("r52.sources")

# Ключи источников, известные системе
SOURCE_KEYS = ("WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU")


class Collector(Protocol):
    source: str
    title: str

    def collect(self, app, ctx, params):
        """Возвращает сводку результата. Наблюдения записываются через входящие."""


# --- Реестр ---
REGISTRY: dict[str, type] = {}
_TITLES: dict[str, str] = {}


def _register_collectors():
    """Ленивая регистрация: импортирует коллекторы при первом обращении."""
    if REGISTRY:
        return
    try:
        from r52.collectors import (
            WBCollector, OzonCollector, AvitoCollector, FarpostCollector,
            AliExpressCollector, AliExpressRUCollector,
        )
        REGISTRY["WB"] = WBCollector
        _TITLES["WB"] = "Wildberries"
        REGISTRY["OZON"] = OzonCollector
        _TITLES["OZON"] = "Ozon"
        REGISTRY["AVITO"] = AvitoCollector
        _TITLES["AVITO"] = "Avito"
        REGISTRY["FARPOST"] = FarpostCollector
        _TITLES["FARPOST"] = "Farpost"
        REGISTRY["ALIEXPRESS"] = AliExpressCollector
        _TITLES["ALIEXPRESS"] = "AliExpress"
        REGISTRY["ALIEXPRESS_RU"] = AliExpressRUCollector
        _TITLES["ALIEXPRESS_RU"] = "AliExpress RU"
        log.info("Sources: зарегистрировано %d сборщиков", len(REGISTRY))
    except ImportError as e:
        log.warning("Sources: ошибка импорта коллекторов: %s", e)


_register_collectors()


def collector_for(source: str) -> Optional[type]:
    """Возвращает класс сборщика по ключу или None."""
    _register_collectors()
    return REGISTRY.get(source.upper())


def collectors_status(source_keys: tuple[str, ...] = SOURCE_KEYS) -> list[dict]:
    """Статус всех источников: READY / NOT_IMPLEMENTED."""
    _register_collectors()
    return [
        {
            "source": key,
            "collector": "READY" if key.upper() in REGISTRY else "NOT_IMPLEMENTED",
            "title": _TITLES.get(key.upper(), key),
        }
        for key in source_keys
    ]


def sync_sources_to_db(conn):
    """Синхронизирует статус сборщиков в таблице source (collector = READY/NOT_IMPLEMENTED)."""
    _register_collectors()
    for key in SOURCE_KEYS:
        status = "READY" if key in REGISTRY else "NOT_IMPLEMENTED"
        conn.execute(
            "UPDATE source SET collector = ? WHERE key = ?",
            (status, key),
        )
    ready = sum(1 for k in SOURCE_KEYS if k in REGISTRY)
    log.info("Sources: БД синхронизирована (%d READY)", ready)


def run_source(source: str, listing_ids: list[str], output_dir: Path,
               data_dir: Optional[Path] = None, use_proxies: bool = True) -> dict:
    """Запускает сборщик в subprocess. Возвращает сводку."""
    _register_collectors()
    from r52.collectors.runner import run_collector_subprocess
    return run_collector_subprocess(source, listing_ids, output_dir, data_dir, use_proxies)
