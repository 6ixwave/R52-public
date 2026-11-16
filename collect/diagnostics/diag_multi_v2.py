"""
Multi-strategy diagnostic v2 — 4 платформы, fastest paths first.
Цель: найти ОДИН рабочий метод на каждую платформу.
Только HTTP (curl_cffi), без Playwright.
"""
import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
except ImportError:
    import requests as cffi_req
    USE_CFFI = False

results = {}
lock = threading.Lock()

def log(msg):
    with lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def http_get(url, headers=None, timeout=12, impersonate="chrome"):
    """GET → (status_code, headers_dict, body_text_truncated)."""
    if USE_CFFI:
        kw = {"impersonate": impersonate, "timeout": timeout}
        if headers:
            kw["headers"] = headers
        try:
            resp = cffi_req.get(url, **kw)
            body = resp.text[:6000] if resp.status_code == 200 else resp.text[:3000]
            return resp.status_code, dict(resp.headers), body
        except Exception as e:
            return 0, {}, str(e)[:500]
    else:
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        if headers:
            h.update(headers)
        try:
            resp = cffi_req.get(url, headers=h, timeout=timeout)
            body = resp.text[:6000] if resp.status_code == 200 else resp.text[:3000]
            return resp.status_code, dict(resp.headers), body
        except Exception as e:
            return 0, {}, str(e)[:500]


# ============ WILDBERRIES ============
def wb_strategies():
    log("=== WB START ===")
    wb_res = {}
    nm_id = 199298871
    vol = nm_id // 100000   # 1992
    part = nm_id // 1000    # 199298

    # S1: Basket CDN — перебор номеров (DNS-ошибка vs 404 vs 200)
    basket_numbers = [19, 25, 30, 35, 40, 45, 50, 10, 20, 7, 15, 3, 12, 22, 33, 44, 8, 14, 28, 38]
    for bn in basket_numbers:
        url = f"https://basket-{bn}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/{nm_id}-info.json"
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "application/json",
            "Referer": "https://www.wildberries.ru/",
            "Origin": "https://www.wildberries.ru",
        }, timeout=8)
        dt = round(time.time() - t0, 1)
        entry = {"status": code, "time": dt, "size": len(body)}
        if code == 200:
            entry["preview"] = body[:600]
            wb_res[f"basket-{bn}"] = entry
            log(f"WB basket-{bn}: 200 OK ({dt}s) — FOUND!")
            break
        elif code == 0:
            entry["error"] = body[:200]
        else:
            entry["preview"] = body[:150]
        wb_res[f"basket-{bn}"] = entry
    else:
        log("WB basket: none returned 200")

    # S2: wbx-content CDN
    for host in ["wbx-content-v2.wbcontent.ru", "wbx-content.wbcontent.ru", "wbx-content-v3.wbcontent.ru"]:
        url = f"https://{host}/cards/v2/detail?nm={nm_id}"
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "application/json",
            "Referer": "https://www.wildberries.ru/",
        }, timeout=8)
        dt = round(time.time() - t0, 1)
        wb_res[f"wbx-{host}"] = {"status": code, "time": dt, "preview": body[:400]}
        if code == 200:
            log(f"WB wbx {host}: 200!")
            break

    # S3: Price API variants
    price_urls = [
        f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
        f"https://card.wb.ru/cards/detail?nm={nm_id}",
        f"https://wbx-content-v2.wbcontent.ru/cards/v2/detail?nm={nm_id}&spp=30",
    ]
    for url in price_urls:
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
            "Origin": "https://www.wildberries.ru",
        }, timeout=8)
        dt = round(time.time() - t0, 1)
        wb_res[f"price-{url[8:45]}"] = {"status": code, "time": dt, "preview": body[:400]}
        if code == 200:
            log(f"WB price: 200 at {url[:50]}")
            break

    # S4: Альтернативный ID (популярный товар)
    for test_id in [208615567, 210000000, 195000000]:
        vol2 = test_id // 100000
        part2 = test_id // 1000
        # Несколько формул basket number
        for formula, bn2 in [("vol//100%100", (vol2 // 100) % 100),
                             ("vol%50", vol2 % 50),
                             ("vol%100", vol2 % 100)]:
            if bn2 == 0:
                bn2 = 50
            url = f"https://basket-{bn2}.wbbasket.ru/vol{vol2}/part{part2}/{test_id}/info/{test_id}-info.json"
            t0 = time.time()
            code, hdrs, body = http_get(url, timeout=8)
            dt = round(time.time() - t0, 1)
            wb_res[f"alt-{test_id}-f{formula}"] = {"status": code, "time": dt, "preview": body[:200]}
            if code == 200:
                log(f"WB alt {test_id} basket-{bn2} ({formula}): 200!")
                break
        else:
            continue
        break

    log("=== WB DONE ===")
    return wb_res


# ============ OZON ============
def ozon_strategies():
    log("=== OZON START ===")
    oz_res = {}
    pid = 1600562267

    # S1: HTML desktop (chrome impersonation)
    t0 = time.time()
    code, hdrs, body = http_get(
        f"https://www.ozon.ru/product/{pid}/",
        headers={"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ru-RU,ru;q=0.9"},
        impersonate="chrome"
    )
    dt = round(time.time() - t0, 1)
    oz_res["html-desktop"] = {"status": code, "time": dt, "size": len(body), "preview": body[:500]}
    # og: tags even in 403?
    og = re.findall(r'<meta[^>]*property=["\']og:(title|description|image)["\'][^>]*content=["\']([^"\']+)', body, re.I)
    if og:
        oz_res["html-desktop"]["og_tags"] = og[:5]

    # S2: Mobile URL
    t0 = time.time()
    code, hdrs, body = http_get(
        f"https://m.ozon.ru/product/{pid}/",
        headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
        impersonate="chrome"
    )
    dt = round(time.time() - t0, 1)
    oz_res["html-mobile"] = {"status": code, "time": dt, "size": len(body), "preview": body[:500]}
    og2 = re.findall(r'<meta[^>]*property=["\']og:(title|description|image)["\'][^>]*content=["\']([^"\']+)', body, re.I)
    if og2:
        oz_res["html-mobile"]["og_tags"] = og2[:5]

    # S3: Composer API variants
    composer_urls = [
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/",
        f"https://api.ozon.ru/composer-api.bx/page/json/v1?url=/product/{pid}/",
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{pid}%2F",
    ]
    for url in composer_urls:
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }, timeout=10)
        dt = round(time.time() - t0, 1)
        oz_res[f"composer-{url[20:55]}"] = {"status": code, "time": dt, "preview": body[:500]}
        if code == 200:
            log(f"Ozon composer 200: {url[:50]}")
            break

    # S4: Try different product ID (popular)
    for test_pid in [2100000000, 1500000000, 1700000000]:
        t0 = time.time()
        code, hdrs, body = http_get(
            f"https://www.ozon.ru/product/{test_pid}/",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            impersonate="chrome"
        )
        dt = round(time.time() - t0, 1)
        oz_res[f"alt-{test_pid}"] = {"status": code, "time": dt, "size": len(body), "preview": body[:300]}
        if code == 200:
            log(f"Ozon alt {test_pid}: 200!")
            break

    log("=== OZON DONE ===")
    return oz_res


# ============ AVITO ============
def avito_strategies():
    log("=== AVITO START ===")
    av_res = {}
    ad_ids = [1096783029, 1080000000, 1075000000]

    # S1: Mobile URL
    for aid in ad_ids:
        t0 = time.time()
        code, hdrs, body = http_get(
            f"https://m.avito.ru/ad/{aid}",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            impersonate="chrome"
        )
        dt = round(time.time() - t0, 1)
        av_res[f"mobile-{aid}"] = {"status": code, "time": dt, "size": len(body), "preview": body[:400]}
        if code == 200:
            log(f"Avito mobile {aid}: 200!")
            break

    # S2: www with full browser headers
    t0 = time.time()
    code, hdrs, body = http_get(
        f"https://www.avito.ru/moscow/ad/{ad_ids[0]}",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
        impersonate="chrome"
    )
    dt = round(time.time() - t0, 1)
    av_res["www-full"] = {"status": code, "time": dt, "size": len(body), "preview": body[:400]}

    # S3: Category page (find live ads)
    cat_urls = [
        "https://www.avito.ru/moscow/telefony",
        "https://www.avito.ru/moscow/iphone",
        "https://m.avito.ru/moscow/telefony",
    ]
    for url in cat_urls:
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"
        }, impersonate="chrome")
        dt = round(time.time() - t0, 1)
        ad_links = re.findall(r'avito\.ru/[^"]*?/(\d{8,})', body)
        av_res[f"cat-{url[30:60]}"] = {"status": code, "time": dt, "size": len(body),
                                        "ads_found": len(ad_links), "preview": body[:300]}
        if code == 200 and ad_links:
            av_res[f"cat-{url[30:60]}"]["sample_ads"] = ad_links[:5]
            log(f"Avito cat {url[30:50]}: 200, {len(ad_links)} ads")
            break

    # S4: Try with different city (Vladivostok — local)
    t0 = time.time()
    code, hdrs, body = http_get(
        "https://www.avito.ru/vladivostok/telefony",
        headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
        impersonate="chrome"
    )
    dt = round(time.time() - t0, 1)
    ad_links_v = re.findall(r'avito\.ru/[^"]*?/(\d{8,})', body)
    av_res["cat-vladivostok"] = {"status": code, "time": dt, "size": len(body),
                                  "ads_found": len(ad_links_v), "preview": body[:300]}

    log("=== AVITO DONE ===")
    return av_res


# ============ FARPOST ============
def farpost_strategies():
    log("=== FARPOST START ===")
    fp_res = {}

    # S1: Main Vladivostok page → extract item links
    t0 = time.time()
    code, hdrs, body = http_get(
        "https://www.farpost.ru/vladivostok/",
        headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
        impersonate="chrome"
    )
    dt = round(time.time() - t0, 1)
    item_urls = re.findall(r'href="([^"]*?/(\d{8,})\.html)"', body)
    fp_res["main-vlad"] = {"status": code, "time": dt, "size": len(body),
                           "items_found": len(item_urls), "preview": body[:400]}
    if item_urls:
        fp_res["main-vlad"]["first_items"] = item_urls[:5]
        log(f"Farpost main: 200, {len(item_urls)} items")

    # S2: Category page
    cat_urls = [
        "https://www.farpost.ru/vladivostok/telefony/",
        "https://www.farpost.ru/vladivostok/elektronika/",
        "https://www.farpost.ru/vladivostok/",
    ]
    for url in cat_urls:
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"
        }, impersonate="chrome")
        dt = round(time.time() - t0, 1)
        items = re.findall(r'href="([^"]*?/(\d{8,})\.html)"', body)
        fp_res[f"cat-{url[30:60]}"] = {"status": code, "time": dt, "size": len(body),
                                        "items_found": len(items), "preview": body[:300]}
        if code == 200 and items:
            fp_res[f"cat-{url[30:60]}"]["first_items"] = items[:5]
            log(f"Farpost cat: {len(items)} items")
            break

    # S3: Try known IDs with /vladivostok/{id}.html
    test_ids = [18530297982, 139122082, 18000000000, 17500000000, 16000000000]
    for tid in test_ids:
        t0 = time.time()
        code, hdrs, body = http_get(
            f"https://www.farpost.ru/vladivostok/{tid}.html",
            headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
            impersonate="chrome"
        )
        dt = round(time.time() - t0, 1)
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', body)
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', body)
        fp_res[f"item-{tid}"] = {"status": code, "time": dt, "size": len(body),
                                  "price": price_m.group(1) if price_m else None,
                                  "title": title_m.group(1)[:100] if title_m else None,
                                  "preview": body[:300]}
        if code == 200:
            log(f"Farpost {tid}: 200! price={price_m.group(1) if price_m else '?'}")

    # S4: Search (correct URL format)
    search_urls = [
        "https://www.farpost.ru/vladivostok/search/?q=iphone",
        "https://www.farpost.ru/search/?q=iphone&city=vladivostok",
        "https://www.farpost.ru/vladivostok/?q=iphone",
        "https://www.farpost.ru/vladivostok/telefony/?q=iphone",
    ]
    for url in search_urls:
        t0 = time.time()
        code, hdrs, body = http_get(url, headers={
            "Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"
        }, impersonate="chrome")
        dt = round(time.time() - t0, 1)
        items = re.findall(r'href="([^"]*?/(\d{8,})\.html)"', body)
        fp_res[f"search-{url[30:65]}"] = {"status": code, "time": dt, "size": len(body),
                                           "items": len(items), "preview": body[:300]}
        if code == 200 and items:
            log(f"Farpost search {url[30:55]}: {len(items)} items")
            break

    log("=== FARPOST DONE ===")
    return fp_res


# ============ MAIN ============
def main():
    t_start = time.time()
    log(f"MULTI-V2 START (curl_cffi={USE_CFFI})")

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_wb = pool.submit(wb_strategies)
        f_oz = pool.submit(ozon_strategies)
        f_av = pool.submit(avito_strategies)
        f_fp = pool.submit(farpost_strategies)

        results["WB"] = f_wb.result()
        results["OZON"] = f_oz.result()
        results["AVITO"] = f_av.result()
        results["FARPOST"] = f_fp.result()

    elapsed = round(time.time() - t_start, 1)
    log(f"MULTI-V2 DONE in {elapsed}s")

    with open("diag_multi_v2_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log("Saved: diag_multi_v2_result.json")

    # Quick summary
    log("--- SUMMARY ---")
    for plat in ["WB", "OZON", "AVITO", "FARPOST"]:
        ok = [k for k, v in results[plat].items() if v.get("status") == 200]
        log(f"  {plat}: {len(ok)}/{len(results[plat])} OK → {ok[:3]}")


if __name__ == "__main__":
    main()
