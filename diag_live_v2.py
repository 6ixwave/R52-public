"""Live-диагностика v2: DNS → connectivity → search, per-source timeout 35s."""
import sys, os, time, logging, socket, traceback
from pathlib import Path
from urllib.parse import quote

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag2")

def flush():
    sys.stdout.flush()
    sys.stderr.flush()

def dns_check(host, timeout=5):
    """Быстрая DNS-проверка."""
    try:
        t0 = time.monotonic()
        socket.setdefaulttimeout(timeout)
        ip = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        elapsed = time.monotonic() - t0
        return f"OK {ip[0][4][0]} ({elapsed*1000:.0f}ms)"
    except Exception as e:
        return f"FAIL: {type(e).__name__}: {str(e)[:60]}"

def http_check(url, timeout=10, headers=None):
    """Базовый GET: статус + время."""
    try:
        from curl_cffi import requests as cffi_req
        t0 = time.monotonic()
        h = headers or {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
        resp = cffi_req.get(url, impersonate="chrome", timeout=timeout, headers=h)
        elapsed = time.monotonic() - t0
        return f"HTTP {resp.status_code} ({elapsed:.1f}s, {len(resp.content)}B)"
    except ImportError:
        import requests
        t0 = time.monotonic()
        h = headers or {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
        resp = requests.get(url, timeout=(5, timeout), headers=h)
        elapsed = time.monotonic() - t0
        return f"HTTP {resp.status_code} ({elapsed:.1f}s, {len(resp.content)}B)"
    except Exception as e:
        return f"ERR: {type(e).__name__}: {str(e)[:80]}"

def search_with_timeout(name, factory, query, page=1, page_size=5, timeout=35):
    """collect_by_search с жёстким таймаутом через signal (Windows: thread)."""
    import threading
    result = {"status": "TIMEOUT", "count": 0, "ids": [], "elapsed": timeout, "error": "timeout"}
    def worker():
        t0 = time.monotonic()
        try:
            collector = factory()
            items = collector.collect_by_search(query, page=page, page_size=page_size)
            elapsed = time.monotonic() - t0
            ids = [str(i.get("fields", {}).get("listing_id", "")) for i in items[:5]]
            ids = [i for i in ids if i]
            result.update(status="OK" if items else "EMPTY", count=len(items), ids=ids[:3], elapsed=elapsed, error="")
        except Exception as e:
            elapsed = time.monotonic() - t0
            result.update(status="ERROR", count=0, ids=[], elapsed=elapsed, error=f"{type(e).__name__}: {str(e)[:100]}")
    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout=timeout)
    if th.is_alive():
        log.warning(f"{name}: TIMEOUT after {timeout}s (thread still alive)")
    return result

def main():
    log.info("=" * 72)
    log.info("LIVE DIAG V2 — %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 72)
    flush()

    # === ЭТАП 1: DNS ===
    log.info("\n--- ЭТАП 1: DNS ---")
    hosts = {
        "WB": "www.wildberries.ru",
        "WB-search": "search.wb.ru",
        "WB-catalog": "catalog.wb.ru",
        "OZON": "www.ozon.ru",
        "AVITO": "www.avito.ru",
        "FARPOST": "www.farpost.ru",
        "ALI": "www.aliexpress.com",
        "ALI-RU": "aliexpress.ru",
    }
    dns_results = {}
    for label, host in hosts.items():
        r = dns_check(host)
        dns_results[label] = r
        log.info(f"  {label:<12} {host:<28} → {r}")
    flush()

    # === ЭТАП 2: HTTP connectivity ===
    log.info("\n--- ЭТАП 2: HTTP (GET /, 10s timeout) ---")
    http_urls = {
        "WB": "https://www.wildberries.ru/",
        "OZON": "https://www.ozon.ru/",
        "AVITO": "https://www.avito.ru/",
        "FARPOST": "https://www.farpost.ru/",
        "ALI": "https://www.aliexpress.com/",
    }
    http_results = {}
    for label, url in http_urls.items():
        r = http_check(url, timeout=10)
        http_results[label] = r
        log.info(f"  {label:<12} → {r}")
    flush()

    # === ЭТАП 3: Search (per-source 35s) ===
    log.info("\n--- ЭТАП 3: collect_by_search (35s each, direct) ---")
    search_results = []

    sources = [
        ("WB", lambda: __import__("r52.collectors.wb", fromlist=["WBCollector"]).WBCollector(use_proxies=False), "наушники"),
        ("OZON", lambda: __import__("r52.collectors.ozon", fromlist=["OzonCollector"]).OzonCollector(use_proxies=False), "наушники"),
        ("AVITO", lambda: __import__("r52.collectors.avito", fromlist=["AvitoCollector"]).AvitoCollector(use_proxies=False), "наушники"),
        ("FARPOST", lambda: __import__("r52.collectors.farpost", fromlist=["FarpostCollector"]).FarpostCollector(use_proxies=False), "наушники"),
        ("ALI_COM", lambda: __import__("r52.collectors.aliexpress", fromlist=["AliExpressCollector"]).AliExpressCollector(use_proxies=False), "headphones"),
        ("ALI_RU", lambda: __import__("r52.collectors.aliexpress", fromlist=["AliExpressCollector"]).AliExpressCollector(use_proxies=False, locale="ru"), "наушники"),
    ]

    for name, factory, query in sources:
        log.info(f"\n  [{name}] query={query!r} ...")
        flush()
        r = search_with_timeout(name, factory, query)
        search_results.append((name, r))
        log.info(f"  [{name}] {r['status']} | {r['count']} items | {r['elapsed']:.1f}s | IDs: {r['ids']} | {r['error']}")
        flush()

    # === СВОДКА ===
    log.info("\n" + "=" * 72)
    log.info("СВОДКА")
    log.info("=" * 72)
    log.info(f"{'Источник':<12} {'DNS':<30} {'HTTP /':<30} {'Search':<40}")
    log.info("-" * 112)
    for name, r in search_results:
        dns_str = dns_results.get(name, dns_results.get(name.replace("_COM","").replace("_RU",""), "-"))
        http_str = http_results.get(name, "-")
        search_str = f"{r['status']} {r['count']} items {r['elapsed']:.0f}s"
        if r['error']:
            search_str += f" [{r['error'][:30]}]"
        log.info(f"{name:<12} {dns_str:<30} {http_str:<30} {search_str:<40}")
    log.info("=" * 72)
    ok = sum(1 for _, r in search_results if r["status"] == "OK" and r["count"] > 0)
    log.info(f"Итого live: {ok}/{len(search_results)}")
    flush()

if __name__ == "__main__":
    main()
