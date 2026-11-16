"""Live-диагностика поисковых маршрутов всех 6 источников.

Запуск: python diag_live_search.py
Тестирует collect_by_search с use_proxies=False (direct) для каждого источника.
Результат: таблица статусов + тайминги + первые ID.
"""
import sys, os, time, logging, traceback
from pathlib import Path

# Настройка проекта
PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("diag")

RESULTS = []

def test_source(name, factory, query="наушники", page=1, page_size=5, timeout=45):
    """Запустить collect_by_search и вернуть (status, count, first_ids, elapsed, error)."""
    t0 = time.monotonic()
    try:
        collector = factory()
        items = collector.collect_by_search(query, page=page, page_size=page_size)
        elapsed = time.monotonic() - t0
        ids = [str(i.get("fields", {}).get("listing_id", "")) for i in items[:page_size]]
        ids = [i for i in ids if i]
        status = "OK" if items else "EMPTY"
        RESULTS.append((name, status, len(items), ids[:3], f"{elapsed:.1f}s", ""))
        log.info(f"{name}: {status} | {len(items)} items | {elapsed:.1f}s | IDs: {ids[:3]}")
        return status, items
    except Exception as e:
        elapsed = time.monotonic() - t0
        err = f"{type(e).__name__}: {str(e)[:120]}"
        RESULTS.append((name, "ERROR", 0, [], f"{elapsed:.1f}s", err))
        log.warning(f"{name}: ERROR | {elapsed:.1f}s | {err}")
        if os.environ.get("DIAG_TRACE"):
            traceback.print_exc()
        return "ERROR", e

def main():
    log.info("=" * 70)
    log.info("LIVE SEARCH DIAGNOSTIC — %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("Query: 'наушники', page=1, page_size=5, use_proxies=False (direct)")
    log.info("=" * 70)

    # --- WB ---
    try:
        from r52.collectors.wb import WBCollector
        test_source("WB", lambda: WBCollector(use_proxies=False))
    except Exception as e:
        RESULTS.append(("WB", "IMPORT_ERR", 0, [], "0s", str(e)[:100]))

    # --- OZON ---
    try:
        from r52.collectors.ozon import OzonCollector
        test_source("OZON", lambda: OzonCollector(use_proxies=False))
    except Exception as e:
        RESULTS.append(("OZON", "IMPORT_ERR", 0, [], "0s", str(e)[:100]))

    # --- AVITO ---
    try:
        from r52.collectors.avito import AvitoCollector
        test_source("AVITO", lambda: AvitoCollector(use_proxies=False))
    except Exception as e:
        RESULTS.append(("AVITO", "IMPORT_ERR", 0, [], "0s", str(e)[:100]))

    # --- FARPOST ---
    try:
        from r52.collectors.farpost import FarpostCollector
        test_source("FARPOST", lambda: FarpostCollector(use_proxies=False))
    except Exception as e:
        RESULTS.append(("FARPOST", "IMPORT_ERR", 0, [], "0s", str(e)[:100]))

    # --- ALIEXPRESS (COM) ---
    try:
        from r52.collectors.aliexpress import AliExpressCollector
        test_source("ALIEXPRESS", lambda: AliExpressCollector(use_proxies=False), query="headphones")
    except Exception as e:
        RESULTS.append(("ALIEXPRESS", "IMPORT_ERR", 0, [], "0s", str(e)[:100]))

    # --- ALIEXPRESS_RU ---
    try:
        from r52.collectors.aliexpress import AliExpressCollector
        test_source("ALIEXPRESS_RU", lambda: AliExpressCollector(use_proxies=False, locale="ru"), query="наушники")
    except Exception as e:
        RESULTS.append(("ALIEXPRESS_RU", "IMPORT_ERR", 0, [], "0s", str(e)[:100]))

    # --- Сводка ---
    log.info("")
    log.info("=" * 70)
    log.info("СВОДКА")
    log.info("=" * 70)
    log.info(f"{'Источник':<14} {'Статус':<10} {'Кол-во':<6} {'Время':<8} {'Первые ID':<30} {'Ошибка'}")
    log.info("-" * 70)
    for name, status, count, ids, elapsed, err in RESULTS:
        id_str = ", ".join(ids) if ids else "-"
        log.info(f"{name:<14} {status:<10} {count:<6} {elapsed:<8} {id_str:<30} {err[:40]}")
    log.info("=" * 70)

    ok_count = sum(1 for r in RESULTS if r[1] == "OK" and r[2] > 0)
    log.info(f"Итого: {ok_count}/{len(RESULTS)} источников дали товары")

if __name__ == "__main__":
    main()
