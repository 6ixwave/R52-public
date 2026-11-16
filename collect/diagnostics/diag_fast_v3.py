"""diag_fast_v3.py — Точечные фиксы по результатам DFV2.
WB: basket-01..60 + 498 workaround. Ozon: headers + mobile. Farpost: FULL HTML grep. Avito: session + API.
Run: C:\Python315\python.exe diag_fast_v3.py
"""
import json, logging, re, sys, time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("DFV3")
R = {}
def rec(p, s, ok, d, dt):
    R[f"{p}:{s}"] = {"ok": ok, "detail": str(d)[:500], "s": round(dt, 1)}
    log.info("[%s] %s %s (%.1fs) %s", p, s, "OK" if ok else "FAIL", dt, str(d)[:150])

def now(): return datetime.now(timezone(timedelta(hours=10))).isoformat()

# ============================================================
# WB: basket-01..60 + price 498 + HTML
# ============================================================
def wb_test():
    nm = 1097975275
    vol, part = nm // 100000, nm // 1000
    from curl_cffi import requests as cr
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/",
         "Origin": "https://www.wildberries.ru",
         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

    # W1: Basket CDN — proper 01-60 range
    t0 = time.time()
    path = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
    found_host = None
    last_code = 0
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            r = cr.get(url, impersonate="chrome", timeout=6, headers=H)
            last_code = r.status_code
            if r.status_code == 200:
                d = r.json()
                if d.get("nm_id") == nm:
                    found_host = host
                    title = d.get("imt_name", "")[:60]
                    rec("WB", "card_cdn", True, f"host={host} vol={vol} title={title} keys={len(d)}", time.time()-t0)
                    with open("dfv3_wb_card.json", "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False)
                    break
        except Exception:
            continue
    if not found_host:
        rec("WB", "card_cdn", False, f"60 hosts tried, last_code={last_code} vol={vol}", time.time()-t0)

    # W2: Price — try with session + cookies from HTML first
    t0 = time.time()
    price_ok = False
    # First: get HTML page to collect cookies
    try:
        s = cr.Session(impersonate="chrome")
        html_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        hr = s.get(html_url, timeout=12, headers={"Accept": "text/html", "User-Agent": H["User-Agent"]})
        log.info("WB HTML warmup: %d, %dB", hr.status_code, len(hr.content))
        # Extract price from HTML
        html = hr.text
        prices = []
        m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
        if m: prices.append(f"og={m.group(1)}")
        m = re.search(r'"basic"\s*:\s*(\d+)', html)
        if m: prices.append(f"basic={m.group(1)}")
        m = re.search(r'"product"\s*:\s*(\d+)', html)
        if m: prices.append(f"product={m.group(1)}")
        # JSON-LD in HTML
        for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S):
            try:
                ld = json.loads(m.group(1))
                s2 = json.dumps(ld, ensure_ascii=False)
                if "offers" in s2 or '"price"' in s2:
                    prices.append(f"ld={s2[:150]}")
                    break
            except: pass
        vis = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:150000])
        if vis: prices.append(f"vis={vis[0].replace(' ','')}")
        rec("WB", "html_warmup", len(prices) > 0, f"HTTP={hr.status_code} len={len(html)} prices={prices[:5]}", time.time()-t0)
        if prices:
            price_ok = True
    except Exception as e:
        log.info("WB HTML warmup err: %s", e)

    # Then: price API with session (cookies)
    if not price_ok:
        for label, url in [
            ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
            ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
            ("search_v1", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
            ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}"),
        ]:
            try:
                r = s.get(url, timeout=10, headers=H)
                if r.status_code == 200:
                    d = r.json()
                    prods = d.get("products") or d.get("data", {}).get("items", [])
                    if prods:
                        p = prods[0]
                        sizes = p.get("sizes", [])
                        pr = sizes[0].get("price", {}) if sizes else {}
                        rec("WB", f"price_{label}", True, f"basic={pr.get('basic')} product={pr.get('product')} qty={sizes[0].get('qty') if sizes else '?'}", time.time()-t0)
                        price_ok = True
                        break
                    if "data" in d:
                        rec("WB", f"price_{label}", True, f"data_keys={list(d['data'].keys())[:8]}", time.time()-t0)
                        price_ok = True
                        break
                else:
                    log.info("WB price %s: %d", label, r.status_code)
            except Exception as e:
                log.info("WB price %s err: %s", label, str(e)[:60])
    if not price_ok:
        rec("WB", "price_all", False, "all endpoints failed", time.time()-t0)

    # W3: Photo CDN
    t0 = time.time()
    photos = []
    try:
        for n in range(1, 6):
            url = f"https://ir1.wbstatic.net/large/1/{nm}/{n}.jpg"
            r = cr.get(url, impersonate="chrome", timeout=4, headers={"Referer": "https://www.wildberries.ru/"})
            if r.status_code == 200 and len(r.content) > 1000:
                photos.append(url)
        # basket CDN photos
        if found_host:
            for img_n in range(1, 6):
                url = f"https://{found_host}/vol{vol}/part{part}/{nm}/1/images/big/{img_n}/"
                r = cr.get(url, impersonate="chrome", timeout=4)
                if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
                    photos.append(url)
        rec("WB", "photos", len(photos) > 0, f"count={len(photos)} first={photos[0][:60] if photos else '?'}", time.time()-t0)
    except Exception as e:
        rec("WB", "photos", False, str(e)[:80], time.time()-t0)

# ============================================================
# OZON: better headers + mobile + composer variants
# ============================================================
def ozon_test():
    oid = "3481320800"
    from curl_cffi import requests as cr

    # O1: Composer API with full browser headers
    t0 = time.time()
    composer_urls = [
        f"https://api.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/",
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/",
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/",
    ]
    H_full = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://www.ozon.ru/product/{oid}/",
        "Origin": "https://www.ozon.ru",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    for url in composer_urls:
        try:
            r = cr.get(url, impersonate="chrome", timeout=10, headers=H_full, verify=False)
            if r.status_code == 200:
                d = r.json()
                s = json.dumps(d, ensure_ascii=False)
                pm = re.search(r'"price"\s*:\s*"?([\d.]+)"?', s)
                nm = re.search(r'"name"\s*:\s*"([^"]{5,80})"', s)
                rec("OZON", "composer_api", True, f"host={url.split('/')[2]} price={pm.group(1) if pm else '?'} name={nm.group(1)[:40] if nm else '?'} len={len(s)}", time.time()-t0)
                with open("dfv3_ozon_composer.json", "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                return
            else:
                log.info("OZON composer %s: %d", url.split('/')[2], r.status_code)
        except Exception as e:
            log.info("OZON composer %s err: %s", url.split('/')[2], str(e)[:60])
    rec("OZON", "composer_api", False, f"all failed, last={r.status_code if 'r' in dir() else '?'}", time.time()-t0)

    # O2: HTML with session (warmup → product)
    t0 = time.time()
    try:
        s = cr.Session(impersonate="chrome")
        # Warmup: main page
        s.get("https://www.ozon.ru/", timeout=10, headers={"Accept": "text/html", "User-Agent": H_full["User-Agent"]})
        time.sleep(1)
        # Product page
        r = s.get(f"https://www.ozon.ru/product/{oid}/", timeout=12,
                  headers={"Accept": "text/html", "User-Agent": H_full["User-Agent"],
                           "Referer": "https://www.ozon.ru/"})
        html = r.text
        prices = []
        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
                items = d if isinstance(d, list) else [d]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        offers = item.get("offers", {})
                        if isinstance(offers, list): offers = offers[0] if offers else {}
                        p = offers.get("price", "")
                        if p: prices.append(f"ld={p}")
                        prices.append(f"name={item.get('name','')[:40]}")
                        break
            except: pass
        # og:price
        m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
        if m: prices.append(f"og={m.group(1)}")
        # data-price
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html[:200000]):
            prices.append(f"dp={m.group(1)}")
        # Visible
        vm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:150000])
        if vm: prices.append(f"vis={vm[0].replace(' ','')}")
        # __STATE__
        sm = re.search(r'window\.__STATE__\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.S)
        state_found = bool(sm)
        rec("OZON", "html_session", len(prices) > 0,
            f"HTTP={r.status_code} len={len(html)} prices={prices[:5]} state={state_found}", time.time()-t0)
        if state_found:
            with open("dfv3_ozon_state.txt", "w", encoding="utf-8") as f:
                f.write(sm.group(1)[:10000])
    except Exception as e:
        rec("OZON", "html_session", False, str(e)[:100], time.time()-t0)

    # O3: Mobile URL
    t0 = time.time()
    try:
        r = cr.get(f"https://m.ozon.ru/product/{oid}/", impersonate="chrome", timeout=10,
                   headers={"Accept": "text/html",
                            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"})
        html = r.text
        pm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:80000])
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        rec("OZON", "mobile", r.status_code == 200 and (pm or tm),
            f"HTTP={r.status_code} len={len(html)} title={tm.group(1)[:50] if tm else '?'} prices={pm[:3]}", time.time()-t0)
    except Exception as e:
        rec("OZON", "mobile", False, str(e)[:80], time.time()-t0)

# ============================================================
# FARPOST: FULL HTML, multiple price patterns
# ============================================================
def farpost_test():
    lid = "138859452"
    from curl_cffi import requests as cr
    H = {"Accept": "text/html", "Referer": "https://www.farpost.ru/",
         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}

    t0 = time.time()
    try:
        url = f"https://www.farpost.ru/vladivostok/{lid}.html"
        r = cr.get(url, impersonate="chrome", timeout=15, headers=H)
        ct = (r.headers.get("Content-Type") or "").lower()
        if "1251" in ct or "cp1251" in ct:
            html = r.content.decode("windows-1251", errors="replace")
        else:
            # Try to detect from meta
            raw_head = r.content[:500].decode("ascii", errors="ignore")
            if "windows-1251" in raw_head.lower():
                html = r.content.decode("windows-1251", errors="replace")
            else:
                html = r.text

        log.info("FP %s: HTTP=%d len=%d ct=%s", lid, r.status_code, len(html), ct[:50])

        # Save FULL HTML for analysis
        with open("dfv3_fp_full.html", "w", encoding="utf-8") as f:
            f.write(html)

        prices = []
        # 1. data-bulletin-price
        for m in re.finditer(r'data-bulletin-price=["\'](\d+)["\']', html):
            prices.append(f"attr={m.group(1)}")
        # 2. JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
                items = d if isinstance(d, list) else [d]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") in ("Product", "Offer"):
                        offers = item.get("offers", {})
                        if isinstance(offers, list): offers = offers[0] if offers else {}
                        p = offers.get("price", "")
                        if p: prices.append(f"ld={p}")
                        prices.append(f"ld_name={item.get('name','')[:50]}")
            except: pass
        # 3. viewbull-summary-price
        m = re.search(r'viewbull-summary-price__value[^>]*>([\d\s\u00a0]+)', html)
        if m: prices.append(f"viewbull={m.group(1).strip()}")
        # 4. Any "руб" or "₽" in first 100KB
        vm = re.findall(r'>(\d[\d\s]{2,12})\s*(?:₽|руб(?:\.|лей)?)\s*<', html[:100000])
        if vm: prices.append(f"vis={vm[0].replace(' ','')}")
        # 5. data-field="price"
        m = re.search(r'data-field=["\']price["\'][^>]*>([\d\s\u00a0]+)', html)
        if m: prices.append(f"df_price={m.group(1).strip()}")
        # 6. meta itemprop="price"
        m = re.search(r'itemprop=["\']price["\'][^>]*content=["\']([\d.]+)', html)
        if m: prices.append(f"meta={m.group(1)}")
        # 7. og:price:amount
        m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
        if m: prices.append(f"og={m.group(1)}")
        # 8. Search for "price" keyword context
        price_ctx = re.findall(r'.{0,40}price.{0,40}', html[:50000], re.I)
        # Filter for interesting ones (with numbers)
        price_ctx_num = [c for c in price_ctx if re.search(r'\d{2,}', c)][:5]

        rec("FARPOST", "full_html", len(prices) > 0,
            f"HTTP={r.status_code} len={len(html)} prices={prices[:6]} ctx={price_ctx_num[:2]}", time.time()-t0)
    except Exception as e:
        rec("FARPOST", "full_html", False, str(e)[:100], time.time()-t0)

    # F2: Second listing
    t0 = time.time()
    try:
        lid2 = "138952176"
        url2 = f"https://www.farpost.ru/vladivostok/{lid2}.html"
        r = cr.get(url2, impersonate="chrome", timeout=12, headers=H)
        html = r.content.decode("windows-1251", errors="replace") if "1251" in (r.headers.get("Content-Type") or "").lower() else r.text
        prices2 = []
        for m in re.finditer(r'data-bulletin-price=["\'](\d+)["\']', html):
            prices2.append(f"attr={m.group(1)}")
        vm = re.findall(r'>(\d[\d\s]{2,12})\s*(?:₽|руб)', html[:80000])
        if vm: prices2.append(f"vis={vm[0].replace(' ','')}")
        rec("FARPOST", "html2", r.status_code == 200,
            f"lid={lid2} len={len(html)} prices={prices2[:4]}", time.time()-t0)
    except Exception as e:
        rec("FARPOST", "html2", False, str(e)[:80], time.time()-t0)

# ============================================================
# AVITO: session + multiple approaches
# ============================================================
def avito_test():
    aid = "1045231001"
    from curl_cffi import requests as cr

    # A1: Session-based desktop
    t0 = time.time()
    try:
        s = cr.Session(impersonate="chrome")
        H = {"Accept": "text/html", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        # Warmup
        s.get("https://www.avito.ru/", timeout=10, headers=H)
        time.sleep(2)
        # Product
        r = s.get(f"https://www.avito.ru/moskva/ad-{aid}", timeout=12, headers={**H, "Referer": "https://www.avito.ru/moskva/"})
        html = r.text
        prices = []
        tm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
                items = d if isinstance(d, list) else [d]
                for item in items:
                    if isinstance(item, dict) and "price" in json.dumps(item):
                        prices.append(f"ld={json.dumps(item, ensure_ascii=False)[:100]}")
            except: pass
        vm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:100000])
        if vm: prices.append(f"vis={vm[0].replace(' ','')}")
        # __STATE__ or __NEXT_DATA__
        sm = re.search(r'window\.__STATE__\s*=\s*(\{.+?\})\s*;', html, re.S)
        nm = re.search(r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*</script>', html, re.S)
        rec("AVITO", "session_desktop", r.status_code == 200 and (prices or tm),
            f"HTTP={r.status_code} len={len(html)} title={tm.group(1)[:40] if tm else '?'} prices={prices[:3]} state={bool(sm or nm)}", time.time()-t0)
    except Exception as e:
        rec("AVITO", "session_desktop", False, str(e)[:100], time.time()-t0)

    # A2: Avito API (internal)
    t0 = time.time()
    try:
        s = cr.Session(impersonate="chrome")
        apis = [
            f"https://www.avito.ru/api/v1/listings/{aid}",
            f"https://api.avito.ru/v1/listings/{aid}",
            f"https://www.avito.ru/moskva/ad-{aid}?format=json",
        ]
        for url in apis:
            try:
                r = s.get(url, timeout=8, headers={"Accept": "application/json", "Referer": "https://www.avito.ru/"})
                if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                    d = r.json()
                    rec("AVITO", "api", True, f"url={url[:60]} keys={list(d.keys())[:8]}", time.time()-t0)
                    return
            except: continue
        rec("AVITO", "api", False, f"tried {len(apis)}", time.time()-t0)
    except Exception as e:
        rec("AVITO", "api", False, str(e)[:80], time.time()-t0)

    # A3: Different city (spb less protected?)
    t0 = time.time()
    try:
        r = cr.get(f"https://www.avito.ru/spb/ad-{aid}", impersonate="chrome", timeout=10,
                   headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"})
        html = r.text
        vm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:80000])
        tm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        rec("AVITO", "city_spb", r.status_code == 200 and (vm or tm),
            f"HTTP={r.status_code} len={len(html)} title={tm.group(1)[:40] if tm else '?'} prices={vm[:2]}", time.time()-t0)
    except Exception as e:
        rec("AVITO", "city_spb", False, str(e)[:80], time.time()-t0)

# ============================================================
def main():
    t0 = time.time()
    log.info("DFV3 START %s", now())
    wb_test()
    ozon_test()
    farpost_test()
    avito_test()
    dt = time.time() - t0
    log.info("DFV3 DONE %.1fs", dt)
    out = {"time": now(), "total_s": round(dt, 1), "results": R}
    with open("dfv3_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    log.info("Saved dfv3_results.json")
    for p in ("WB", "OZON", "FARPOST", "AVITO"):
        ok = sum(1 for k, v in R.items() if k.startswith(p + ":") and v["ok"])
        tot = sum(1 for k in R if k.startswith(p + ":"))
        log.info("  %-8s %d/%d OK", p, ok, tot)

if __name__ == "__main__":
    main()
