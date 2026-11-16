"""Диагностика v8: WB price fix, Ozon multi-strategy, Avito live ID hunt."""
import json, logging, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag8")


def diag_wb():
    """WB: 3 стратегии для цены."""
    from curl_cffi import requests as cffi
    results = {}
    nm_id = 100000001
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    host = "basket-05.wbbasket.ru"

    # S1: Полный дамп data + no_return_map как цена
    try:
        url = f"https://{host}{path}"
        r = cffi.get(url, impersonate="chrome", timeout=15,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        d = r.json()
        data_field = d.get("data", {})
        log.info("WB S1: data field = %s", json.dumps(data_field, ensure_ascii=False)[:400])
        selling = d.get("selling", {})
        nrm = selling.get("no_return_map")
        log.info("WB S1: no_return_map=%s (=%s руб)", nrm, f"{nrm/100:.2f}" if nrm else "?")
        results["s1_nrm"] = {"value": nrm, "rub": round(nrm/100, 2) if nrm else None,
                             "data_keys": list(data_field.keys()) if isinstance(data_field, dict) else type(data_field).__name__}
        # Дамп media
        media = d.get("media", [])
        log.info("WB S1: media=%d items", len(media) if isinstance(media, list) else 0)
        if isinstance(media, list) and media:
            results["s1_media_sample"] = str(media[0])[:200]
    except Exception as e:
        results["s1_nrm"] = {"error": str(e)[:100]}

    # S2: Попробовать ДРУГОЙ путь basket (full product, не card.json)
    try:
        # Полный JSON товара (не card.json, а "info" без /card.json)
        path2 = f"/vol{vol}/part{part}/{nm_id}/info/ru/"
        url2 = f"https://{host}{path2}"
        r2 = cffi.get(url2, impersonate="chrome", timeout=15,
                      headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        log.info("WB S2: full info path -> HTTP %d, %d bytes", r2.status_code, len(r2.content))
        if r2.status_code == 200:
            d2 = r2.json()
            log.info("WB S2: keys=%s", list(d2.keys())[:15])
            # Ищем цены
            prices = []
            def walk_p(obj, p=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k.lower() in ("price", "priceu", "priced", "basic", "product", "card", "wallet", "oldprice") and isinstance(v, (int, float)) and v > 0:
                            prices.append(f"{p}.{k}={v}")
                        walk_p(v, f"{p}.{k}")
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:5]):
                        walk_p(v, f"{p}[{i}]")
            walk_p(d2)
            results["s2_full"] = {"status": 200, "keys": list(d2.keys())[:15], "prices": prices[:15]}
        else:
            results["s2_full"] = {"status": r2.status_code}
    except Exception as e:
        results["s2_full"] = {"error": str(e)[:100]}

    # S3: Другой продукт (дороже) — ищем nm_id с ценами
    try:
        # Пробуем nm_id = 150000001 (другой vol)
        nm2 = 150000001
        vol2 = nm2 // 100000  # 1500
        part2 = nm2 // 1000   # 150000
        path3 = f"/vol{vol2}/part{part2}/{nm2}/info/ru/card.json"
        # Ищем хост
        found_host = None
        for n in range(1, 11):
            h = f"basket-{n:02d}.wbbasket.ru"
            try:
                rr = cffi.get(f"https://{h}{path3}", impersonate="chrome", timeout=8,
                              headers={"Accept": "application/json"})
                if rr.status_code == 200:
                    found_host = h
                    d3 = rr.json()
                    sz = d3.get("sizes", [])
                    sl = d3.get("selling", {})
                    log.info("WB S3: nm=%d host=%s sizes=%d selling_keys=%s", nm2, h, len(sz), list(sl.keys())[:8])
                    if sz:
                        sp = sz[0].get("price", {})
                        log.info("WB S3: sizes[0].price = %s", json.dumps(sp, ensure_ascii=False)[:200])
                    results["s3_other"] = {"nm_id": nm2, "host": h, "sizes": len(sz),
                                           "selling_keys": list(sl.keys())[:10],
                                           "size0_price": sp if sz else None,
                                           "name": d3.get("imt_name", "?")[:50]}
                    break
            except:
                continue
        if not found_host:
            results["s3_other"] = {"error": "host not found for vol=1500"}
    except Exception as e:
        results["s3_other"] = {"error": str(e)[:100]}

    return results


def diag_ozon():
    """Ozon: 3 стратегии обхода FAB."""
    from curl_cffi import requests as cffi
    results = {}
    lid = "2795954097"

    # S1: Mobile API (m.ozon.ru)
    try:
        urls = [
            f"https://m.ozon.ru/product/{lid}/",
            f"https://api.ozon.ru/api/entrypoint/pdp/{lid}",
            f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/",
        ]
        headers = {
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        }
        for u in urls:
            try:
                r = cffi.get(u, impersonate="chrome", timeout=12, headers=headers, verify=False)
                log.info("OZON S1: %s -> HTTP %d, %d bytes", u.split("/")[-1][:30], r.status_code, len(r.content))
                key = u.split("/")[-1][:20]
                if r.status_code == 200:
                    t = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', r.text, re.I)
                    results[f"s1_{key}"] = {"status": 200, "len": len(r.content),
                                            "title": t.group(1)[:60] if t else None}
                else:
                    results[f"s1_{key}"] = {"status": r.status_code, "len": len(r.content),
                                            "snippet": r.text[:150]}
            except Exception as e:
                results[f"s1_{u.split('/')[-1][:15]}"] = {"error": str(e)[:80]}
            time.sleep(0.5)
    except Exception as e:
        results["s1"] = {"error": str(e)[:100]}

    # S2: Другой продукт (дешевле, может быть меньше защиты)
    try:
        lid2 = "1000000001"  # популярный товар
        url2 = f"https://www.ozon.ru/product/{lid2}/"
        r2 = cffi.get(url2, impersonate="chrome", timeout=15,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
                      verify=False)
        log.info("OZON S2: product %s -> HTTP %d, %d bytes", lid2, r2.status_code, len(r2.content))
        t2 = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', r2.text, re.I)
        fab = "challenge" in r2.text[:3000].lower()
        results["s2_other_id"] = {"id": lid2, "status": r2.status_code, "len": len(r2.content),
                                  "fab": fab, "title": t2.group(1)[:60] if t2 else None}
    except Exception as e:
        results["s2_other_id"] = {"error": str(e)[:100]}

    # S3: Playwright с stealth (viewport, locale, UA)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )
            # Убираем navigator.webdriver
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = ctx.new_page()
            log.info("OZON S3: PW stealth -> homepage...")
            resp = page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            log.info("OZON S3: homepage status=%d", resp.status if resp else 0)
            # Cookie check
            cookies = ctx.cookies()
            log.info("OZON S3: cookies=%d (%s)", len(cookies), [c["name"] for c in cookies[:5]])
            # Product
            log.info("OZON S3: PW stealth -> product...")
            resp2 = page.goto(f"https://www.ozon.ru/product/{lid}/", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            html = page.content()
            log.info("OZON S3: product status=%d len=%d", resp2.status if resp2 else 0, len(html))
            t3 = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', html, re.I)
            fab3 = "challenge" in html[:5000].lower()
            results["s3_pw_stealth"] = {
                "status": resp2.status if resp2 else 0,
                "len": len(html), "fab": fab3,
                "title": t3.group(1)[:60] if t3 else None,
                "cookies": len(cookies),
            }
            browser.close()
    except Exception as e:
        log.exception("OZON S3 error")
        results["s3_pw_stealth"] = {"error": str(e)[:150]}

    return results


def diag_avito():
    """Avito: 3 стратегии для живого ID."""
    import requests
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    results = {}

    # S1: Разные search URL (Avito меняет структуру)
    search_urls = [
        "https://www.avito.ru/moskovskaya/moskva/telefony",
        "https://www.avito.ru/moskovskaya/moskva/elektronika/telefony",
        "https://www.avito.ru/moskovskaya/moskva/vse-obyavleniya?s=1&q=iphone+15",
        "https://www.avito.ru/search/iphone-15",
    ]
    for u in search_urls:
        try:
            r = requests.get(u, headers=headers, timeout=(5, 15), allow_redirects=True)
            log.info("AVITO S1: %s -> HTTP %d, %d bytes", u.split("/")[-1][:30], r.status_code, len(r.text))
            if r.status_code == 200 and len(r.text) > 1000:
                # Ищем ID объявлений
                ids = re.findall(r'avito\.ru/ad/(\d{6,})', r.text)
                if not ids:
                    ids = re.findall(r'href=["\'][^"\']*?/(\d{7,})["\']', r.text)
                unique = list(dict.fromkeys(ids))[:10]
                log.info("AVITO S1: found %d IDs: %s", len(unique), unique[:5])
                results[f"s1_{u.split('/')[-1][:20]}"] = {"status": 200, "ids": unique, "len": len(r.text)}
                if unique:
                    break
            elif r.status_code == 200:
                results[f"s1_{u.split('/')[-1][:20]}"] = {"status": 200, "len": len(r.text)}
        except Exception as e:
            results[f"s1_{u.split('/')[-1][:20]}"] = {"error": str(e)[:80]}
        time.sleep(0.5)

    # S2: Прямые ID (популярные товары, могут быть живыми)
    test_ids = ["1000000001", "500000001", "700000001", "800000001"]
    for tid in test_ids:
        try:
            r = requests.get(f"https://www.avito.ru/ad/{tid}", headers=headers, timeout=(5, 10))
            log.info("AVITO S2: ID %s -> HTTP %d, %d bytes", tid, r.status_code, len(r.text))
            if r.status_code == 200 and len(r.text) > 2000:
                t = re.search(r'<meta[^>]*og:title[^>]*content=["\']([^"\']+)', r.text, re.I)
                p = re.search(r'<meta[^>]*og:price:amount[^>]*content=["\']([\d.]+)', r.text, re.I)
                results["s2_live_id"] = {"id": tid, "status": 200,
                                         "title": t.group(1)[:60] if t else None,
                                         "price": p.group(1) if p else None}
                log.info("AVITO S2 LIVE: id=%s title=%s price=%s", tid,
                         results["s2_live_id"]["title"], results["s2_live_id"]["price"])
                break
        except Exception as e:
            pass
        time.sleep(0.5)

    # S3: Avito API (если есть)
    try:
        api_url = f"https://www.avito.ru/api/search/iphone-15"
        r3 = requests.get(api_url, headers={**headers, "Accept": "application/json"}, timeout=(5, 10))
        log.info("AVITO S3: API -> HTTP %d, %d bytes", r3.status_code, len(r3.content))
        if r3.status_code == 200:
            results["s3_api"] = {"status": 200, "snippet": r3.text[:200]}
        else:
            results["s3_api"] = {"status": r3.status_code}
    except Exception as e:
        results["s3_api"] = {"error": str(e)[:80]}

    return results


def main():
    t0 = time.monotonic()
    log.info("=== DIAG8 START %s ===", datetime.now(timezone(timedelta(hours=10))).isoformat())
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
    log.info("=== DIAG8 DONE in %.1fs ===", total)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"DIAG8_DONE total={total:.0f}s")


if __name__ == "__main__":
    main()
