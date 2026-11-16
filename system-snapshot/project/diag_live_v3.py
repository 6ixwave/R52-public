"""Live-диагностика v3: фикс ID-экстракции, ALI_RU конструктор, per-source 40s."""
import sys, os, time, logging, socket, threading, traceback
from pathlib import Path
from urllib.parse import quote

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag3")

def flush():
    sys.stdout.flush(); sys.stderr.flush()

def extract_ids(items, max_n=5):
    """Извлечь listing_id из JARVIS_BR02 формата: observation.id."""
    ids = []
    for i in items[:max_n]:
        obs = i.get("observation", {})
        lid = str(obs.get("id", "") or obs.get("fields", {}).get("listing_id", "") or "")
        if lid:
            ids.append(lid)
    return ids

def search_with_timeout(name, factory, query, page=1, page_size=5, timeout=40):
    result = {"status": "TIMEOUT", "count": 0, "ids": [], "elapsed": timeout, "error": "timeout"}
    def worker():
        t0 = time.monotonic()
        try:
            collector = factory()
            items = collector.collect_by_search(query, page=page, page_size=page_size)
            elapsed = time.monotonic() - t0
            ids = extract_ids(items)
            result.update(status="OK" if items else "EMPTY", count=len(items), ids=ids, elapsed=elapsed, error="")
        except Exception as e:
            elapsed = time.monotonic() - t0
            result.update(status="ERROR", count=0, ids=[], elapsed=elapsed, error=f"{type(e).__name__}: {str(e)[:120]}")
    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout=timeout)
    if th.is_alive():
        log.warning(f"{name}: TIMEOUT after {timeout}s")
    return result

def main():
    log.info("=" * 72)
    log.info("LIVE DIAG V3 — %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 72)
    flush()

    sources = [
        ("WB", lambda: __import__("r52.collectors.wb", fromlist=["WBCollector"]).WBCollector(use_proxies=False), "наушники"),
        ("OZON", lambda: __import__("r52.collectors.ozon", fromlist=["OzonCollector"]).OzonCollector(use_proxies=False), "наушники"),
        ("AVITO", lambda: __import__("r52.collectors.avito", fromlist=["AvitoCollector"]).AvitoCollector(use_proxies=False), "наушники"),
        ("FARPOST", lambda: __import__("r52.collectors.farpost", fromlist=["FarpostCollector"]).FarpostCollector(use_proxies=False), "наушники"),
        ("ALI_COM", lambda: __import__("r52.collectors.aliexpress", fromlist=["AliExpressCollector"]).AliExpressCollector(use_proxies=False), "headphones"),
        ("ALI_RU", lambda: __import__("r52.collectors.aliexpress", fromlist=["AliExpressRUCollector"]).AliExpressRUCollector(use_proxies=False), "наушники"),
    ]

    search_results = []
    for name, factory, query in sources:
        log.info(f"\n  [{name}] query={query!r} ...")
        flush()
        r = search_with_timeout(name, factory, query)
        search_results.append((name, r))
        log.info(f"  [{name}] {r['status']} | {r['count']} items | {r['elapsed']:.1f}s | IDs: {r['ids']} | {r['error']}")
        flush()

    log.info("\n" + "=" * 72)
    log.info("СВОДКА")
    log.info("=" * 72)
    log.info(f"{'Источник':<12} {'Статус':<10} {'Кол-во':<6} {'Время':<8} {'Первые ID':<40} {'Ошибка'}")
    log.info("-" * 110)
    for name, r in search_results:
        id_str = ", ".join(r["ids"][:3]) if r["ids"] else "-"
        log.info(f"{name:<12} {r['status']:<10} {r['count']:<6} {r['elapsed']:<8.1f} {id_str:<40} {r['error'][:40]}")
    log.info("=" * 72)
    ok = sum(1 for _, r in search_results if r["status"] == "OK" and r["count"] > 0)
    log.info(f"Итого live: {ok}/{len(search_results)}")
    flush()

if __name__ == "__main__":
    main()
