"""Live-диагностика v4: прямой вывод в файл, per-source timeout, DNS+HTTP+search+pagination."""
import sys, os, time, socket, threading, traceback, json
from pathlib import Path
from urllib.parse import quote

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OUT_FILE = PROJECT / "diag_live_v4_output.txt"
_log_lines = []

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _log_lines.append(line)
    print(line, flush=True)

def flush_file():
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")

def dns_check(host):
    """Проверить DNS-резолвинг хоста."""
    try:
        t0 = time.monotonic()
        results = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        elapsed = time.monotonic() - t0
        ips = list(set(r[4][0] for r in results))
        return {"ok": True, "ips": ips[:3], "elapsed": elapsed}
    except socket.gaierror as e:
        return {"ok": False, "error": str(e), "elapsed": time.monotonic() - t0}

def http_probe(url, headers=None, timeout=12):
    """Быстрый GET-запрос: статус, размер, заголовки."""
    try:
        import requests
        h = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        t0 = time.monotonic()
        resp = requests.get(url, headers=h, timeout=timeout, allow_redirects=True)
        elapsed = time.monotonic() - t0
        return {
            "status": resp.status_code,
            "size": len(resp.content),
            "elapsed": elapsed,
            "final_url": str(resp.url),
            "content_type": (resp.headers.get("Content-Type") or "")[:80],
        }
    except Exception as e:
        return {"status": -1, "error": f"{type(e).__name__}: {str(e)[:100]}", "elapsed": time.monotonic() - t0}

def search_with_timeout(name, factory, query, page=1, page_size=5, timeout=40):
    """Запустить collect_by_search в потоке с ограничением времени."""
    result = {"status": "TIMEOUT", "count": 0, "ids": [], "elapsed": timeout, "error": "timeout"}
    def worker():
        t0 = time.monotonic()
        try:
            collector = factory()
            items = collector.collect_by_search(query, page=page, page_size=page_size)
            elapsed = time.monotonic() - t0
            ids = []
            for i in items[:5]:
                obs = i.get("observation", {})
                lid = str(obs.get("id", "") or obs.get("fields", {}).get("listing_id", "") or "")
                if lid:
                    ids.append(lid)
            result.update(status="OK" if items else "EMPTY", count=len(items), ids=ids, elapsed=elapsed, error="")
        except Exception as e:
            elapsed = time.monotonic() - t0
            result.update(status="ERROR", count=0, ids=[], elapsed=elapsed,
                          error=f"{type(e).__name__}: {str(e)[:150]}")
        finally:
            try:
                collector.close()
            except Exception:
                pass
    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout=timeout)
    if th.is_alive():
        log(f"  {name}: THREAD still alive after {timeout}s (daemon, will die)")
    return result

def main():
    log("=" * 72)
    log("LIVE DIAG V4 — %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("Python: %s" % sys.version.split()[0])
    log("=" * 72)
    flush_file()

    # ─── 1. DNS ───
    log("\n── DNS CHECK ──")
    dns_targets = {
        "WB": ["catalog.wb.ru", "search.wb.ru", "u-search.wb.ru"],
        "OZON": ["www.ozon.ru"],
        "AVITO": ["www.avito.ru"],
        "FARPOST": ["www.farpost.ru"],
        "ALI_COM": ["www.aliexpress.com"],
        "ALI_RU": ["ru.aliexpress.com"],
    }
    for src, hosts in dns_targets.items():
        for h in hosts:
            r = dns_check(h)
            if r["ok"]:
                log(f"  DNS {src:<10} {h:<28} OK {r['elapsed']:.2f}s → {r['ips']}")
            else:
                log(f"  DNS {src:<10} {h:<28} FAIL {r['error'][:60]}")
        flush_file()

    # ─── 2. HTTP PROBE (быстрый GET на поисковую страницу) ───
    log("\n── HTTP PROBE ──")
    probes = [
        ("WB", "https://catalog.wb.ru/exactmatch/ru/common/v4/search?query=test&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"),
        ("OZON", "https://www.ozon.ru/search/?text=test&from_global=true&page=1"),
        ("AVITO", "https://www.avito.ru/rossiya?q=test&p=1"),
        ("FARPOST", "https://www.farpost.ru/dir?query=test&page=1"),
        ("ALI_COM", "https://www.aliexpress.com/wholesale?SearchText=test&page=1"),
        ("ALI_RU", "https://ru.aliexpress.com/wholesale?SearchText=test&page=1"),
    ]
    for name, url in probes:
        r = http_probe(url)
        if r.get("status") == 200:
            log(f"  {name:<10} HTTP 200 | {r['size']} bytes | {r['elapsed']:.1f}s | {r['content_type'][:40]}")
        else:
            status = r.get("status", "?")
            err = r.get("error", "")
            log(f"  {name:<10} HTTP {status} | {r.get('size','?')} bytes | {r['elapsed']:.1f}s | {err[:60]}")
        flush_file()

    # ─── 3. SEARCH (collect_by_search, page=1) ───
    log("\n── SEARCH PAGE 1 ──")
    sources = [
        ("WB", lambda: __import__("r52.collectors.wb", fromlist=["WBCollector"]).WBCollector(use_proxies=False), "наушники"),
        ("OZON", lambda: __import__("r52.collectors.ozon", fromlist=["OzonCollector"]).OzonCollector(use_proxies=False), "наушники"),
        ("AVITO", lambda: __import__("r52.collectors.avito", fromlist=["AvitoCollector"]).AvitoCollector(use_proxies=False), "наушники"),
        ("FARPOST", lambda: __import__("r52.collectors.farpost", fromlist=["FarpostCollector"]).FarpostCollector(use_proxies=False), "наушники"),
        ("ALI_COM", lambda: __import__("r52.collectors.aliexpress", fromlist=["AliExpressCollector"]).AliExpressCollector(use_proxies=False), "headphones"),
        ("ALI_RU", lambda: __import__("r52.collectors.aliexpress", fromlist=["AliExpressRUCollector"]).AliExpressRUCollector(use_proxies=False), "наушники"),
    ]
    search_p1 = []
    for name, factory, query in sources:
        log(f"  [{name}] search page=1 query={query!r} ...")
        flush_file()
        r = search_with_timeout(name, factory, query, page=1, page_size=5, timeout=40)
        search_p1.append((name, r))
        log(f"  [{name}] {r['status']} | n={r['count']} | {r['elapsed']:.1f}s | IDs={r['ids'][:3]} | {r['error'][:80]}")
        flush_file()

    # ─── 4. PAGINATION (page=2 для тех, кто дал OK на p1) ───
    log("\n── PAGINATION PAGE 2 ──")
    search_p2 = []
    for name, factory, query in sources:
        p1_r = next((r for n, r in search_p1 if n == name), None)
        if p1_r is None or p1_r["status"] != "OK" or p1_r["count"] == 0:
            log(f"  [{name}] skip p2 (p1={p1_r['status'] if p1_r else 'N/A'})")
            continue
        log(f"  [{name}] search page=2 ...")
        flush_file()
        r = search_with_timeout(name, factory, query, page=2, page_size=5, timeout=40)
        search_p2.append((name, r))
        # Проверка: новые ID не пересекаются с p1
        overlap = set(r["ids"]) & set(p1_r["ids"])
        log(f"  [{name}] p2: {r['status']} | n={r['count']} | {r['elapsed']:.1f}s | IDs={r['ids'][:3]} | overlap={len(overlap)} | {r['error'][:60]}")
        flush_file()

    # ─── 5. СВОДКА ───
    log("\n" + "=" * 72)
    log("СВОДКА")
    log("=" * 72)
    log(f"{'Источник':<10} {'P1 Статус':<10} {'P1 n':<5} {'P1 Время':<9} {'P2 Статус':<10} {'P2 n':<5} {'P2 Время':<9} {'Ошибка'}")
    log("-" * 100)
    p2_map = {n: r for n, r in search_p2}
    for name, r1 in search_p1:
        r2 = p2_map.get(name)
        s2 = f"{r2['status']}" if r2 else "skip"
        n2 = str(r2["count"]) if r2 else "-"
        t2 = f"{r2['elapsed']:.1f}s" if r2 else "-"
        log(f"{name:<10} {r1['status']:<10} {r1['count']:<5} {r1['elapsed']:<8.1f}s {s2:<10} {n2:<5} {t2:<9} {r1['error'][:50]}")
    log("=" * 72)
    ok_count = sum(1 for _, r in search_p1 if r["status"] == "OK" and r["count"] > 0)
    log(f"Live OK (p1): {ok_count}/{len(search_p1)}")
    log(f"Live OK (p2): {sum(1 for _, r in search_p2 if r['status'] == 'OK' and r['count'] > 0)}/{len(search_p2)}")
    log("=" * 72)
    flush_file()
    log("DONE. Output saved to: %s" % OUT_FILE)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        flush_file()
    finally:
        # Финальный flush на случай, если main() уже записал
        flush_file()
