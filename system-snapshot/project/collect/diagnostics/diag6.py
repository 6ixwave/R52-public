"""Диагностика 3 проблемных площадок: WB card.json, Ozon SSL, Avito URL."""
import json, logging, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag6")

def diag_wb():
    """Дамп card.json: все ключи, selling, sizes."""
    from curl_cffi import requests as cffi
    log.info("WB: fetching card.json for 100000001...")
    host = "basket-05.wbbasket.ru"
    path = "/vol1000/part1000/100000001/info/ru/card.json"
    url = f"https://{host}{path}"
    try:
        r = cffi.get(url, impersonate="chrome", timeout=15,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        log.info("WB card.json: HTTP %d, %d bytes", r.status_code, len(r.content))
        if r.status_code == 200:
            d = r.json()
            log.info("WB card.json top-level keys (%d): %s", len(d), list(d.keys()))
            selling = d.get("selling")
            if selling:
                log.info("WB selling keys: %s", list(selling.keys()) if isinstance(selling, dict) else type(selling))
                if isinstance(selling, dict):
                    for k, v in selling.items():
                        log.info("  selling[%s] = %s (type=%s)", k, repr(v)[:80], type(v).__name__)
            else:
                log.info("WB: NO 'selling' key in card.json")
            sizes = d.get("sizes", [])
            log.info("WB sizes: %d items", len(sizes))
            if sizes:
                s0 = sizes[0]
                log.info("WB sizes[0] keys: %s", list(s0.keys()) if isinstance(s0, dict) else type(s0))
                if isinstance(s0, dict):
                    for k, v in s0.items():
                        log.info("  sizes[0][%s] = %s (type=%s)", k, repr(v)[:80], type(v).__name__)
            # Ищем цену в любом месте
            price_found = []
            def find_prices(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k.lower() in ("price", "priceu", "priced", "oldprice", "basic", "product", "card", "wallet") and isinstance(v, (int, float)) and v > 0:
                            price_found.append(f"{path}.{k}={v}")
                        find_prices(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:5]):
                        find_prices(v, f"{path}[{i}]")
            find_prices(d)
            log.info("WB price-like fields: %s", price_found[:20] if price_found else "NONE FOUND")
            return {"ok": True, "keys": list(d.keys()), "selling_keys": list(selling.keys()) if selling else None,
                    "price_fields": price_found[:20]}
        else:
            return {"ok": False, "status": r.status_code}
    except Exception as e:
        log.exception("WB diag error")
        return {"ok": False, "error": str(e)}

def diag_ozon():
    """Ozon: curl_cffi verify=False на api.ozon.ru + www.ozon.ru."""
    from curl_cffi import requests as cffi
    lid = "2795954097"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.ozon.ru/",
    }
    results = {}
    # 1. api.ozon.ru with verify=False
    try:
        url1 = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/"
        log.info("OZON: trying api.ozon.ru (verify=False)...")
        r1 = cffi.get(url1, impersonate="chrome", timeout=15, headers=headers, verify=False)
        log.info("OZON api.ozon.ru: HTTP %d, %d bytes", r1.status_code, len(r1.content))
        if r1.status_code == 200:
            d = r1.json()
            log.info("OZON api.ozon.ru JSON keys: %s", list(d.keys())[:10] if isinstance(d, dict) else type(d))
            results["api_v2"] = {"status": 200, "keys": list(d.keys())[:10] if isinstance(d, dict) else str(type(d))}
        else:
            results["api_v2"] = {"status": r1.status_code, "body": r1.text[:200]}
    except Exception as e:
        log.info("OZON api.ozon.ru error: %s", str(e)[:120])
        results["api_v2"] = {"error": str(e)[:120]}

    # 2. www.ozon.ru composer (same API, different domain)
    try:
        url2 = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/"
        log.info("OZON: trying www.ozon.ru composer...")
        r2 = cffi.get(url2, impersonate="chrome", timeout=15, headers=headers)
        log.info("OZON www composer: HTTP %d, %d bytes", r2.status_code, len(r2.content))
        if r2.status_code == 200:
            d2 = r2.json()
            results["www_composer"] = {"status": 200, "keys": list(d2.keys())[:10] if isinstance(d2, dict) else str(type(d2))}
        else:
            results["www_composer"] = {"status": r2.status_code, "body": r2.text[:200]}
    except Exception as e:
        log.info("OZON www composer error: %s", str(e)[:120])
        results["www_composer"] = {"error": str(e)[:120]}

    # 3. www.ozon.ru entrypoint
    try:
        url3 = f"https://www.ozon.ru/api/entrypoint/pdp/{lid}"
        log.info("OZON: trying entrypoint...")
        r3 = cffi.get(url3, impersonate="chrome", timeout=15, headers=headers)
        log.info("OZON entrypoint: HTTP %d, %d bytes", r3.status_code, len(r3.content))
        results["entrypoint"] = {"status": r3.status_code, "body": r3.text[:200]}
    except Exception as e:
        results["entrypoint"] = {"error": str(e)[:120]}

    return results

def diag_avito():
    """Avito: пробуем 4 URL-паттерна для ID 482962662."""
    import requests
    lid = "482962662"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    urls = [
        f"https://www.avito.ru/ad/{lid}",
        f"https://www.avito.ru/ad/{lid}/",
        f"https://m.avito.ru/ad/{lid}",
        f"https://www.avito.ru/moskovskaya/moskva/iphone-15-128-gb-2-sim/{lid}/",
    ]
    results = {}
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=(5, 15), allow_redirects=True)
            final_url = r.url
            log.info("AVITO %s -> HTTP %d (%d bytes) final=%s", u.split("/")[-1], r.status_code, len(r.text), final_url[:80])
            results[u] = {"status": r.status_code, "len": len(r.text), "final": final_url[:100]}
            if r.status_code == 200 and len(r.text) > 500:
                # Быстрая проверка: есть ли title/price
                import re
                t = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', r.text, re.I)
                p = re.search(r'<meta[^>]*og:price:amount[^>]*content=["\']([\d.]+)', r.text, re.I)
                results[u]["title"] = t.group(1)[:60] if t else None
                results[u]["price"] = p.group(1) if p else None
                log.info("AVITO FOUND: title=%s price=%s", results[u].get("title"), results[u].get("price"))
                break
        except Exception as e:
            results[u] = {"error": str(e)[:100]}
            log.info("AVITO %s error: %s", u, str(e)[:80])
        time.sleep(1)
    return results

def main():
    t0 = time.monotonic()
    log.info("=== DIAG6 START %s ===", datetime.now(timezone(timedelta(hours=10))).isoformat())
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {
            pool.submit(diag_wb): "WB",
            pool.submit(diag_ozon): "OZON",
            pool.submit(diag_avito): "AVITO",
        }
        for f in as_completed(futs):
            src = futs[f]
            try:
                results[src] = f.result()
            except Exception as e:
                results[src] = {"error": str(e)[:100]}
    total = time.monotonic() - t0
    log.info("=== DIAG6 DONE in %.1fs ===", total)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))

if __name__ == "__main__":
    main()
