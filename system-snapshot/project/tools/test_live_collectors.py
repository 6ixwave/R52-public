"""Live-тест шести сборщиков R-52 2.12 (после правок).

Запуск: python tools/test_live_collectors.py
Проверяет collect_by_search для каждого источника, печатает сводку.
"""
import json
import logging
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("test_live")

RESULTS = {}

def test_source(name, factory, query="наушники", page=1, page_size=5):
    """Запускает collect_by_search и возвращает сводку."""
    t0 = time.monotonic()
    entry = {
        "source": name,
        "query": query,
        "page": page,
        "page_size": page_size,
        "status": "UNKNOWN",
        "count": 0,
        "ids": [],
        "titles": [],
        "prices": [],
        "elapsed": 0.0,
        "error": None,
        "error_type": None,
    }
    try:
        collector = factory()
        results = collector.collect_by_search(query, page=page, page_size=page_size)
        entry["count"] = len(results)
        for r in results[:page_size]:
            obs = r.get("observation", {})
            entry["ids"].append(obs.get("id", ""))
            fields = obs.get("fields", {})
            t = fields.get("title", {}).get("value", "") if isinstance(fields.get("title"), dict) else ""
            p = fields.get("price", {}).get("value", "") if isinstance(fields.get("price"), dict) else ""
            entry["titles"].append(t[:60])
            entry["prices"].append(str(p))
        entry["status"] = "OK" if results else "EMPTY"
    except Exception as e:
        entry["status"] = "ERROR"
        entry["error"] = str(e)[:200]
        entry["error_type"] = type(e).__name__
    finally:
        entry["elapsed"] = round(time.monotonic() - t0, 1)
    RESULTS[name] = entry
    return entry


def main():
    log.info("=== LIVE COLLECTOR TEST R-52 2.12 ===")
    log.info("Дата: %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("")

    # --- WB ---
    log.info("--- WB ---")
    try:
        from r52.collectors.wb import WBCollector
        test_source("WB", lambda: WBCollector(use_proxies=False))
    except Exception as e:
        RESULTS["WB"] = {"source": "WB", "status": "IMPORT_ERROR", "error": str(e)[:200], "elapsed": 0}
        log.warning("WB import: %s", e)

    # --- OZON ---
    log.info("--- OZON ---")
    try:
        from r52.collectors.ozon import OzonCollector
        test_source("OZON", lambda: OzonCollector(use_proxies=False))
    except Exception as e:
        RESULTS["OZON"] = {"source": "OZON", "status": "IMPORT_ERROR", "error": str(e)[:200], "elapsed": 0}
        log.warning("OZON import: %s", e)

    # --- AVITO ---
    log.info("--- AVITO ---")
    try:
        from r52.collectors.avito import AvitoCollector
        test_source("AVITO", lambda: AvitoCollector(use_proxies=False))
    except Exception as e:
        RESULTS["AVITO"] = {"source": "AVITO", "status": "IMPORT_ERROR", "error": str(e)[:200], "elapsed": 0}
        log.warning("AVITO import: %s", e)

    # --- FARPOST ---
    log.info("--- FARPOST ---")
    try:
        from r52.collectors.farpost import FarpostCollector
        test_source("FARPOST", lambda: FarpostCollector(use_proxies=False))
    except Exception as e:
        RESULTS["FARPOST"] = {"source": "FARPOST", "status": "IMPORT_ERROR", "error": str(e)[:200], "elapsed": 0}
        log.warning("FARPOST import: %s", e)

    # --- ALIEXPRESS ---
    log.info("--- ALIEXPRESS ---")
    try:
        from r52.collectors.aliexpress import AliExpressCollector
        test_source("ALIEXPRESS", lambda: AliExpressCollector(use_proxies=False))
    except Exception as e:
        RESULTS["ALIEXPRESS"] = {"source": "ALIEXPRESS", "status": "IMPORT_ERROR", "error": str(e)[:200], "elapsed": 0}
        log.warning("ALIEXPRESS import: %s", e)

    # --- ALIEXPRESS_RU ---
    log.info("--- ALIEXPRESS_RU ---")
    try:
        from r52.collectors.aliexpress import AliExpressRUCollector
        test_source("ALIEXPRESS_RU", lambda: AliExpressRUCollector(use_proxies=False))
    except Exception as e:
        RESULTS["ALIEXPRESS_RU"] = {"source": "ALIEXPRESS_RU", "status": "IMPORT_ERROR", "error": str(e)[:200], "elapsed": 0}
        log.warning("ALIEXPRESS_RU import: %s", e)

    # --- Сводка ---
    log.info("")
    log.info("=== СВОДКА ===")
    for name, e in RESULTS.items():
        st = e.get("status", "?")
        cnt = e.get("count", 0)
        el = e.get("elapsed", 0)
        err = (e.get("error") or "")[:60]
        log.info("%-15s %-12s %3d items  %5.1fs  %s", name, st, cnt, el, err)

    # Сохранить
    out = Path(__file__).parent / "test_live_collectors_results.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Результаты: %s", out)


if __name__ == "__main__":
    main()
