"""Диагностика v7: WB card.json dump, Ozon Playwright warmup, Avito live ID."""
import json, logging, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag7")


def diag_wb():
    """WB: правильный путь card.json + полный дамп структуры."""
    from curl_cffi import requests as cffi
    nm_id = 100000001
    vol = nm_id // 100000   # 1000
    part = nm_id // 1000    # 100000  (НЕ 1000!)
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    # E2E нашёл basket-05, пробуем его
    host = "basket-05.wbbasket.ru"
    url = f"https://{host}{path}"
    log.info("WB: %s", url)
    try:
        r = cffi.get(url, impersonate="chrome", timeout=15,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        log.info("WB card.json: HTTP %d, %d bytes", r.status_code, len(r.content))
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "path": path}
        d = r.json()
        log.info("WB top keys (%d): %s", len(d), list(d.keys()))

        # selling
        selling = d.get("selling")
        if selling:
            log.info("WB selling: %s", json.dumps(selling, ensure_ascii=False)[:500])

        # sizes
        sizes = d.get("sizes", [])
        log.info("WB sizes: %d items", len(sizes))
        if sizes:
            log.info("WB sizes[0]: %s", json.dumps(sizes[0], ensure_ascii=False)[:500])

        # Ищем ВСЕ числовые поля > 100 (потенциальные цены)
        price_fields = []
        def walk(obj, p=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (int, float)) and v > 100 and v < 10_000_000:
                        price_fields.append(f"{p}.{k}={v}")
                    walk(v, f"{p}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj[:10]):
                    walk(v, f"{p}[{i}]")
        walk(d)
        log.info("WB price-like (>100): %d fields", len(price_fields))
        for pf in price_fields[:30]:
            log.info("  %s", pf)

        return {"ok": True, "top_keys": list(d.keys()),
                "selling": selling, "sizes_count": len(sizes),
                "sizes_0": sizes[0] if sizes else None,
                "price_fields": price_fields[:30]}
    except Exception as e:
        log.exception("WB diag error")
        return {"ok": False, "error": str(e)[:200]}


def diag_ozon():
    """Ozon: Playwright с warmup (homepage → product)."""
    from r52.collectors.pw_fetch import PwFetcher
    lid = "2795954097"
    product_url = f"https://www.ozon.ru/product/{lid}/"
    results = {}

    try:
        pw = PwFetcher(headless=True)
        # Warmup: заходим на главную, ждём cookie
        log.info("OZON PW: warmup homepage...")
        home = pw.get("https://www.ozon.ru/", extra_wait_ms=2000)
        log.info("OZON PW: homepage status=%d len=%d", home.status if home else 0,
                 len(home.html) if home else 0)

        # Ждём cookie установления
        time.sleep(2)

        # Теперь product page
        log.info("OZON PW: product page...")
        prod = pw.get(product_url, extra_wait_ms=5000)
        log.info("OZON PW: product status=%d len=%d", prod.status if prod else 0,
                 len(prod.html) if prod else 0)

        if prod and prod.status == 200 and len(prod.html) > 2000:
            html = prod.html
            # Быстрая проверка данных
            t = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', html, re.I)
            p = re.search(r'<meta[^>]*og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
            imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru[^"\']+)', html)
            log.info("OZON PW: title=%s price=%s imgs=%d",
                     t.group(1)[:50] if t else None,
                     p.group(1) if p else None, len(imgs))
            results["playwright"] = {
                "status": 200, "len": len(html),
                "title": t.group(1)[:80] if t else None,
                "price": p.group(1) if p else None,
                "images": len(imgs),
            }
        elif prod:
            # Может быть challenge page
            has_fab = "challenge" in prod.html[:3000].lower() or "fab" in prod.html[:3000].lower()
            results["playwright"] = {
                "status": prod.status, "len": len(prod.html),
                "fab_challenge": has_fab,
                "snippet": prod.html[:300],
            }
            log.info("OZON PW: fab=%d snippet=%s", has_fab, prod.html[:200])
        else:
            results["playwright"] = {"status": 0, "error": "no result"}

        pw.close()
    except Exception as e:
        log.exception("OZON PW error")
        results["playwright"] = {"error": str(e)[:200]}

    # Бонус: попробуем curl_cffi на HTML-страницу (не API)
    try:
        from curl_cffi import requests as cffi
        log.info("OZON cffi: HTML page...")
        r = cffi.get(product_url, impersonate="chrome", timeout=20,
                     headers={"Accept": "text/html,application/xhtml+xml",
                              "Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("OZON cffi HTML: HTTP %d, %d bytes", r.status_code, len(r.content))
        has_fab = "challenge" in r.text[:3000].lower()
        t = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', r.text, re.I)
        results["cffi_html"] = {
            "status": r.status_code, "len": len(r.content),
            "fab": has_fab,
            "title": t.group(1)[:80] if t else None,
        }
    except Exception as e:
        results["cffi_html"] = {"error": str(e)[:150]}

    return results


def diag_avito():
    """Avito: ищем живой ID через search + пробуем несколько ID."""
    import requests
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    results = {}

    # Стратегия 1: поиск на Avito (search page)
    try:
        search_url = "https://www.avito.ru/moskovskaya/moskva/telefony/iphone-15"
        log.info("AVITO: search page...")
        r = requests.get(search_url, headers=headers, timeout=(5, 20), allow_redirects=True)
        log.info("AVITO search: HTTP %d, %d bytes", r.status_code, len(r.text))
        if r.status_code == 200:
            # Ищем ID объявлений в HTML
            ids = re.findall(r'avito\.ru/[^"\'/]*(\d{6,})', r.text)
            unique_ids = list(dict.fromkeys(ids))[:10]
            log.info("AVITO search: found %d IDs: %s", len(unique_ids), unique_ids[:5])
            results["search"] = {"status": 200, "ids": unique_ids}

            # Пробуем первый найденный ID
            if unique_ids:
                test_id = unique_ids[0]
                test_url = f"https://www.avito.ru/ad/{test_id}"
                log.info("AVITO: testing ID %s...", test_id)
                time.sleep(1)
                r2 = requests.get(test_url, headers=headers, timeout=(5, 15))
                log.info("AVITO test %s: HTTP %d, %d bytes", test_id, r2.status_code, len(r2.text))
                if r2.status_code == 200 and len(r2.text) > 500:
                    t = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', r2.text, re.I)
                    p = re.search(r'<meta[^>]*og:price:amount[^>]*content=["\']([\d.]+)', r2.text, re.I)
                    results["test_id"] = {
                        "id": test_id, "status": 200,
                        "title": t.group(1)[:80] if t else None,
                        "price": p.group(1) if p else None,
                    }
                    log.info("AVITO LIVE: id=%s title=%s price=%s",
                             test_id, results["test_id"]["title"], results["test_id"]["price"])
                else:
                    results["test_id"] = {"id": test_id, "status": r2.status_code}
        else:
            results["search"] = {"status": r.status_code}
    except Exception as e:
        log.info("AVITO search error: %s", str(e)[:100])
        results["search"] = {"error": str(e)[:100]}

    # Стратегия 2: пробуем ID из Festima (482962662) через redirect
    try:
        r3 = requests.get("https://www.avito.ru/ad/482962662", headers=headers,
                          timeout=(5, 10), allow_redirects=True)
        log.info("AVITO 482962662: HTTP %d final=%s", r3.status_code, r3.url[:80])
        results["festima_id"] = {"status": r3.status_code, "final": r3.url[:100]}
    except Exception as e:
        results["festima_id"] = {"error": str(e)[:100]}

    return results


def main():
    t0 = time.monotonic()
    log.info("=== DIAG7 START %s ===", datetime.now(timezone(timedelta(hours=10))).isoformat())
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
                results[src] = {"error": str(e)[:200]}
    total = time.monotonic() - t0
    log.info("=== DIAG7 DONE in %.1fs ===", total)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"DIAG7_DONE total={total:.0f}s")


if __name__ == "__main__":
    main()
