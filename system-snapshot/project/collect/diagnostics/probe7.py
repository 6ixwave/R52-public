r"""probe7.py — WB CDN prices, Ozon Playwright, Avito real listing.
Run: C:\Python315\python.exe probe7.py
"""
import sys, os, json, time, re, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("P7")

RESULTS = {}
def mark(src, label, ok, detail=""):
    RESULTS[f"{src}:{label}"] = {"ok": ok, "detail": detail}
    log.info("[%s] %s → %s %s", src, label, "PASS" if ok else "FAIL", detail[:150])

# ============================================================
# WB: CDN price endpoints + card.json sizes
# ============================================================
def wb_cdn_prices(nm_id=1097975275):
    log.info("=== WB CDN PRICES (nm_id=%d) ===", nm_id)
    from curl_cffi import requests as cr

    vol = nm_id // 100000
    part = nm_id // 1000
    host_num = 42  # known from probe6

    host = f"basket-{host_num:02d}.wbbasket.ru"
    base = f"https://{host}/vol{vol}/part{part}/{nm_id}"
    h = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}

    # S0: card.json — check sizes field
    try:
        r = cr.get(f"{base}/info/ru/card.json", impersonate="chrome", timeout=10, headers=h)
        if r.status_code == 200:
            d = r.json()
            sizes = d.get("sizes", [])
            log.info("WB card.json: keys=%s sizes_count=%d", list(d.keys())[:15], len(sizes))
            if sizes:
                s0 = sizes[0]
                log.info("WB sizes[0]: %s", json.dumps(s0, ensure_ascii=False)[:200])
                price_in_sizes = s0.get("price", {})
                mark("WB", "card_sizes", bool(price_in_sizes),
                     f"sizes={len(sizes)} price0={json.dumps(price_in_sizes)[:80]}")
            else:
                # Check top-level price fields
                top_prices = {k: d[k] for k in d if "price" in k.lower() or k in ("priceU","priceD")}
                mark("WB", "card_sizes", False, f"no sizes, top_prices={top_prices}")
            # Save full card.json for reference
            with open("wb_p7_card.json","w",encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        else:
            mark("WB", "card_sizes", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "card_sizes", False, str(e)[:80])

    # S1: CDN prices.json
    for sub in ("info/ru/prices.json", "prices.json", "info/prices.json", "data.json"):
        try:
            r = cr.get(f"{base}/{sub}", impersonate="chrome", timeout=8, headers=h)
            if r.status_code == 200:
                d = r.json()
                mark("WB", f"cdn_{sub.replace('/','_')}", True,
                     f"keys={list(d.keys())[:10]} sample={json.dumps(d, ensure_ascii=False)[:100]}")
            elif r.status_code != 404:
                mark("WB", f"cdn_{sub.replace('/','_')}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"cdn_{sub.replace('/','_')}", False, str(e)[:60])
        time.sleep(0.3)

    # S2: static-basket CDN (images host also serves data)
    try:
        s_host = f"static-basket-{host_num:02d}.wbbasket.ru"
        r = cr.get(f"https://{s_host}/vol{vol}/part{part}/{nm_id}/data.json",
                   impersonate="chrome", timeout=8, headers=h)
        if r.status_code == 200:
            d = r.json()
            mark("WB", "static_data", True, f"keys={list(d.keys())[:10]}")
        else:
            mark("WB", "static_data", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "static_data", False, str(e)[:60])

    # S3: search.wb.ru/cards/v5/detail (different from /v5/search!)
    for ver in ("v1","v2","v5"):
        try:
            u = f"https://search.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&nm={nm_id}"
            r = cr.get(u, impersonate="chrome", timeout=10, headers={
                "Accept": "application/json", "Referer": "https://www.wildberries.ru/",
                "Accept-Language": "ru-RU,ru;q=0.9"})
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data",{}).get("items",[]) or d.get("products",[])
                if prods:
                    p = prods[0]
                    prices = []
                    for s in p.get("sizes",[])[:2]:
                        pr = s.get("price",{})
                        for k in ("basic","product","card","wallet","old"):
                            v = pr.get(k)
                            if v and v > 0: prices.append(f"{k}={v}")
                    mark("WB", f"search_detail_{ver}", True, f"items={len(prods)} prices={prices[:6]}")
                    return
                mark("WB", f"search_detail_{ver}", False, f"200 no items keys={list(d.keys())[:5]}")
            else:
                mark("WB", f"search_detail_{ver}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"search_detail_{ver}", False, str(e)[:60])
        time.sleep(0.5)

    # S4: WB "static" price endpoint (used by mobile app)
    try:
        u = f"https://static-basket-{host_num:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/prices.json"
        r = cr.get(u, impersonate="chrome", timeout=8, headers=h)
        if r.status_code == 200:
            d = r.json()
            mark("WB", "static_prices", True, f"keys={list(d.keys())[:8]} data={json.dumps(d)[:100]}")
        else:
            mark("WB", "static_prices", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "static_prices", False, str(e)[:60])

    # S5: card.wb.ru with NO dest (or dest=-1)
    for dest in ("0", "-1", "-1257786"):
        try:
            u = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest={dest}&nm={nm_id}"
            r = cr.get(u, impersonate="chrome", timeout=10, headers={
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.wildberries.ru/catalog/",
                "Origin": "https://www.wildberries.ru",
            })
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data",{}).get("items",[]) or d.get("products",[])
                if prods:
                    mark("WB", f"card_nospp_dest{dest}", True, f"items={len(prods)}")
                    return
                mark("WB", f"card_nospp_dest{dest}", False, f"200 no items")
            else:
                mark("WB", f"card_nospp_dest{dest}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"card_nospp_dest{dest}", False, str(e)[:60])
        time.sleep(0.5)

    time.sleep(1)

# ============================================================
# OZON: Playwright quick test
# ============================================================
def ozon_playwright(listing_id="3481320800"):
    log.info("=== OZON PLAYWRIGHT (id=%s) ===", listing_id)

    # Try using the existing Playwright helper from r52
    try:
        from r52.collectors.ozon import OzonCollector
        col = OzonCollector(data_dir=None, use_proxies=False)
        url = f"https://www.ozon.ru/product/{listing_id}/"
        t0 = time.time()
        result = col.collect_product(listing_id, url, f"OZ-{listing_id}")
        dt = time.time() - t0
        if result:
            obs = result.get("observation", {})
            prices = obs.get("price_candidates", [])
            title = obs.get("fields",{}).get("title",{}).get("value","")
            mark("OZON", "playwright_full", True,
                 f"{dt:.1f}s title={title[:40]} prices={len(prices)} status={obs.get('status')}")
            # Save
            with open("ozon_p7_result.json","w",encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        else:
            mark("OZON", "playwright_full", False, f"{dt:.1f}s result=None")
        col.session.close()
    except Exception as e:
        mark("OZON", "playwright_full", False, f"{type(e).__name__}: {str(e)[:100]}")

    # Fallback: direct Playwright (if r52 helper failed)
    if not RESULTS.get("OZON:playwright_full",{}).get("ok"):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale="ru-RU",
                    viewport={"width":1920,"height":1080},
                )
                page = ctx.new_page()
                page.goto(f"https://www.ozon.ru/product/{listing_id}/", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                html = page.content()
                # Check
                og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
                ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL|re.I)
                detail = f"len={len(html)}"
                if og_title: detail += f" t={og_title.group(1)[:40]}"
                if og_price: detail += f" p={og_price.group(1)}"
                detail += f" ld={len(ld_blocks)}"
                mark("OZON", "pw_direct", bool(og_title or og_price), detail)
                with open("ozon_p7_pw.html","w",encoding="utf-8") as f:
                    f.write(html[:100000])
                browser.close()
        except Exception as e:
            mark("OZON", "pw_direct", False, f"{type(e).__name__}: {str(e)[:100]}")

    time.sleep(1)

# ============================================================
# AVITO: find real listing + fetch
# ============================================================
def avito_real():
    log.info("=== AVITO REAL ===")
    from curl_cffi import requests as cr

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    listing_id = None

    # S1: Category page (with warm-up to main page first)
    try:
        # Warm-up: main page
        r0 = cr.get("https://www.avito.ru/", impersonate="chrome", timeout=12, headers=headers)
        log.info("AVITO warmup: HTTP %d len=%d", r0.status_code, len(r0.text))
        time.sleep(2)
    except Exception as e:
        log.info("AVITO warmup: %s", str(e)[:60])

    # S2: Category with specific URL patterns
    cat_urls = [
        "https://www.avito.ru/moskva/telefony-i-smartfony",
        "https://www.avito.ru/moskva/telefony",
        "https://www.avito.ru/moskva/electronics/telefony",
    ]
    for cu in cat_urls:
        try:
            r = cr.get(cu, impersonate="chrome", timeout=15, headers=headers)
            if r.status_code == 200 and len(r.text) > 5000:
                html = r.text
                # Find ad IDs: pattern /moskva/.../1234567890 or data-id
                ids = re.findall(r'href="[^"]*?/(\d{9,12})/?"', html)
                if not ids:
                    ids = re.findall(r'"id"\s*:\s*"?(\d{9,12})"?', html)
                if not ids:
                    ids = re.findall(r'data-ad-id=["\'](\d+)', html)
                if ids:
                    listing_id = ids[0]
                    mark("AVITO", f"cat_{cu.split('/')[-1]}", True, f"id={listing_id} n={len(ids)}")
                    break
                else:
                    mark("AVITO", f"cat_{cu.split('/')[-1]}", False, f"len={len(html)} no IDs")
            else:
                mark("AVITO", f"cat_{cu.split('/')[-1]}", False, f"HTTP {r.status_code} len={len(r.text)}")
        except Exception as e:
            mark("AVITO", f"cat_{cu.split('/')[-1]}", False, str(e)[:60])
        time.sleep(2)

    if not listing_id:
        # S3: Try known IDs (from various sources)
        for test_id in ("1100000001","1099999999","1150000001","1120000001"):
            try:
                u = f"https://www.avito.ru/ad/{test_id}"
                r = cr.get(u, impersonate="chrome", timeout=12, headers=headers)
                if r.status_code == 200 and len(r.text) > 5000:
                    html = r.text
                    # Check if it's a real ad (not 404 page)
                    if "Такого объявления" not in html and "404" not in html[:500]:
                        listing_id = test_id
                        mark("AVITO", f"try_{test_id}", True, f"len={len(html)}")
                        break
                    else:
                        mark("AVITO", f"try_{test_id}", False, "404-like page")
                else:
                    mark("AVITO", f"try_{test_id}", False, f"HTTP {r.status_code}")
            except Exception as e:
                mark("AVITO", f"try_{test_id}", False, str(e)[:50])
            time.sleep(1.5)

    if not listing_id:
        mark("AVITO", "find", False, "no listing found")
        return

    log.info("AVITO target: %s", listing_id)

    # S4: Fetch the ad
    for prefix in ("www","m"):
        try:
            u = f"https://{prefix}.avito.ru/ad/{listing_id}"
            r = cr.get(u, impersonate="chrome", timeout=15, headers=headers)
            if r.status_code == 200 and len(r.text) > 3000:
                html = r.text
                has_captcha = any(x in html.lower() for x in ("hcaptcha","smartcaptcha","cf-challenge","captcha"))
                ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL|re.I)
                product_ld = None
                for block in ld_blocks:
                    try:
                        d = json.loads(block.strip())
                        if isinstance(d,dict) and d.get("@type")=="Product":
                            product_ld = d; break
                    except: pass
                og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)

                detail = f"len={len(html)} cap={has_captcha} ld={len(ld_blocks)}"
                if og_title: detail += f" t={og_title.group(1)[:40]}"
                if og_price: detail += f" p={og_price.group(1)}"
                if product_ld:
                    off = product_ld.get("offers",{})
                    detail += f" ld_p={off.get('price','?')}"
                mark("AVITO", f"fetch_{prefix}", True, detail)
                with open(f"avito_p7_{prefix}.html","w",encoding="utf-8") as f:
                    f.write(html[:100000])
                break
            else:
                mark("AVITO", f"fetch_{prefix}", False, f"HTTP {r.status_code} len={len(r.text)}")
        except Exception as e:
            mark("AVITO", f"fetch_{prefix}", False, str(e)[:60])
        time.sleep(2)

# ============================================================
if __name__ == "__main__":
    t0 = time.time()
    wb_cdn_prices()
    ozon_playwright()
    avito_real()
    dt = time.time() - t0
    log.info("=== PROBE7 DONE in %.1fs ===", dt)
    with open("probe7_results.json","w",encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    for k,v in RESULTS.items():
        log.info("  %s: %s %s", k, "PASS" if v["ok"] else "FAIL", v["detail"][:120])
