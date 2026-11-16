"""diag23: 6 стратегий — FP PW category+XHR, FP verify dump, WB card retry, WB card.wb, OZ PW stealth."""
import json, re, time, os
from pathlib import Path

results = {}
t0 = time.time()
BASE = Path(__file__).parent

def run(name, fn):
    ts = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        import traceback
        ok, detail = False, f"exc: {type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
    el = round(time.time() - ts, 1)
    results[name] = {"ok": ok, "detail": detail, "s": el}
    print(f"[{name}] {'PASS' if ok else 'FAIL'} ({el}s) {detail[:200]}", flush=True)
    time.sleep(0.5)

# ============================================================
# 1. FP: PLAYWRIGHT CATEGORY — render JS, extract listings
# ============================================================
def fp_pw_category():
    """Playwright: load category page, wait for JS to render listings."""
    from playwright.sync_api import sync_playwright
    xhrs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        
        def on_resp(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "json" in ct and resp.status == 200:
                    xhrs.append(resp.url[:120])
            except:
                pass
        page.on("response", on_resp)
        
        # Load category
        url = "https://www.farpost.ru/vladivostok/auto/misc/"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # Wait for JS rendering
        page.wait_for_timeout(8000)
        
        html = page.content()
        # Extract listings
        ids = re.findall(r'href="([^"]*?(\d{5,})\.html)"', html)
        prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
        titles = re.findall(r'data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        titles = [re.sub(r'<[^>]+>', '', t).strip()[:80] for t in titles]
        # Also: look for price in spans
        prices2 = re.findall(r'class="[^"]*price[^"]*"[^>]*>([\d\s\xa0]+[₽]?)', html)
        
        ok = len(ids) >= 3 and (len(prices) + len(prices2)) >= 2
        detail = (f"ids={len(ids)} p_attr={len(prices)} p_cls={len(prices2)} "
                  f"titles={len(titles)} xhrs={len(xhrs)} len={len(html)}")
        if ids:
            detail += f" first_id={ids[0][1]}"
        if titles:
            detail += f" first_t={titles[0][:40]}"
        if xhrs:
            detail += f" xhr1={xhrs[0]}"
        
        # Save rendered HTML
        with open(BASE / "d23_fp_cat_pw.html", "w", encoding="utf-8") as f:
            f.write(html)
        # Save XHR list
        with open(BASE / "d23_fp_xhrs.json", "w", encoding="utf-8") as f:
            json.dump(xhrs, f, indent=2)
        
        browser.close()
        return ok, detail

# ============================================================
# 2. FP: VERIFY PAGE DUMP — analyze JS challenge
# ============================================================
def fp_verify_dump():
    """Playwright: load /verify, dump full HTML, find JS logic."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        # Go to card → get redirected to /verify
        page.goto("https://www.farpost.ru/vladivostok/134693091.html",
                  timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        
        html = page.content()
        final_url = page.url
        has_verify = "/verify" in final_url
        
        # Analyze: find scripts, forms, buttons, meta refresh
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        forms = re.findall(r'<form[^>]*>', html)
        buttons = re.findall(r'<button[^>]*>(.*?)</button>', html, re.DOTALL)
        iframes = re.findall(r'<iframe[^>]*src=["\']([^"\']+)["\']', html)
        meta_refresh = re.search(r'http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)', html, re.I)
        cookie_js = re.findall(r'(?:document\.cookie|setCookie|Set-Cookie)\s*[:=]\s*([^;]{0,100})', html)
        # Look for challenge token
        token_m = re.search(r'(?:token|challenge|captcha|verify)[^=]*=["\']([^"\']{8,})["\']', html, re.I)
        
        # Check if there's a "click to continue" button
        click_btn = re.search(r'<(?:button|a|div)[^>]*(?:class|id)=["\'][^"\']*(?:continue|verify|check|pass|go)[^"\']*["\'][^>]*>(.*?)</(?:button|a|div)>', html, re.I | re.DOTALL)
        
        # Save full verify page
        with open(BASE / "d23_fp_verify.html", "w", encoding="utf-8") as f:
            f.write(html)
        
        # Try clicking any button/link that looks like "continue"
        if has_verify and (click_btn or buttons):
            # Try clicking
            try:
                btn = page.query_selector("button, a.verify, a.continue, [class*='verify'], [class*='check']")
                if btn:
                    btn.click()
                    page.wait_for_timeout(5000)
                    final_url = page.url
                    has_verify = "/verify" in final_url
                    html2 = page.content()
                    price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html2)
                    if not has_verify and price_m:
                        return True, f"CLICK solved! price={price_m.group(1)}"
            except:
                pass
        
        ok = (not has_verify) or (len(scripts) > 0)
        detail = (f"verify={has_verify} url={final_url[:60]} scripts={len(scripts)} "
                  f"forms={len(forms)} btns={len(buttons)} iframes={len(iframes)} "
                  f"meta_ref={bool(meta_refresh)} cookies_js={cookie_js[:3]} "
                  f"token={token_m.group(1)[:30] if token_m else 'N/A'} "
                  f"len={len(html)}")
        browser.close()
        return ok, detail

# ============================================================
# 3. WB: CARD.JSON RETRY (exact diag20 approach)
# ============================================================
def wb_card_retry():
    """Retry card.json with delay — worked in diag20."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    vol = nm_id // 10000  # 10979
    part = nm_id // 1000  # 1097975
    url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    
    time.sleep(3)  # cooldown
    r = s.get(url, timeout=12)
    if r.status_code != 200:
        # Try different basket hosts
        for h in [42, 43, 44, 100, 200, 500]:
            u2 = f"https://basket-{h}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
            try:
                r2 = s.get(u2, timeout=8)
                if r2.status_code == 200:
                    r = r2
                    url = u2
                    break
            except:
                continue
    if r.status_code != 200:
        return False, f"HTTP={r.status_code} url={url[:60]}"
    
    data = r.json()
    keys = list(data.keys())
    txt = json.dumps(data)
    # Find price
    pm = re.search(r'"(?:price|salePriceW|salePriceM|priceU|priceD)"\s*:\s*"?(\d+)', txt)
    # Save
    with open(BASE / "d23_wb_card.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    ok = len(keys) >= 5
    detail = f"host={url.split('/')[2]} keys={len(keys)} price={pm.group(1) if pm else 'N/A'} k={keys[:8]}"
    return ok, detail

# ============================================================
# 4. WB: card.wb.ru with cffi + proper headers
# ============================================================
def wb_card_wb():
    """card.wb.ru/cards/v2/detail — the main price API."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    urls = [
        f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
        f"https://card.wb.ru/cards/v1/detail?nm={nm_id}",
        f"https://card.wb.ru/cards/v2/detail?nm={nm_id}&spp=30",
    ]
    for u in urls:
        try:
            r = s.get(u, timeout=10, headers={
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.wildberries.ru/",
                "Origin": "https://www.wildberries.ru",
            })
            if r.status_code == 200:
                data = r.json()
                cards = data.get("data", {}).get("cards", [])
                if cards:
                    c = cards[0]
                    price_d = c.get("price", {}).get("product", 0)
                    price_u = c.get("price", {}).get("basic", 0)
                    name = c.get("name", "")[:50]
                    return True, f"OK {u.split('/')[2]} priceD={price_d} priceU={price_u} name={name}"
                return True, f"OK len={len(r.content)} sample={r.text[:200]}"
            else:
                print(f"  card.wb [{u.split('/')[2]}]: HTTP {r.status_code}", flush=True)
        except Exception as e:
            print(f"  card.wb: {e}", flush=True)
    return False, "all card.wb.ru endpoints failed"

# ============================================================
# 5. OZON: Playwright with stealth + longer wait
# ============================================================
def oz_pw_stealth():
    """Ozon Playwright: longer wait, check if interstitial resolves."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        # Remove navigator.webdriver
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()
        
        # Warm-up: main page first
        page.goto("https://www.ozon.ru/", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        
        # Product page
        url = "https://www.ozon.ru/product/3481320800/"
        page.goto(url, timeout=35000, wait_until="domcontentloaded")
        # Wait for interstitial to resolve
        for i in range(6):
            page.wait_for_timeout(4000)
            title = page.title()
            if "соединени" not in title.lower() and title:
                break
        
        html = page.content()
        title_m = re.search(r'<title>([^<]+)</title>', html)
        jsonlds = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        price_val = None
        for j in jsonlds:
            try:
                d = json.loads(j)
                if isinstance(d, dict) and d.get("@type") == "Product":
                    offers = d.get("offers", {})
                    if isinstance(offers, dict):
                        price_val = offers.get("price")
                    elif isinstance(offers, list) and offers:
                        price_val = offers[0].get("price")
            except:
                pass
        
        ok = (price_val is not None) or (title_m and "соединени" not in title_m.group(1).lower())
        detail = (f"title={title_m.group(1)[:60] if title_m else ''} "
                  f"jsonlds={len(jsonlds)} price={price_val} len={len(html)}")
        browser.close()
        return ok, detail

# ============================================================
# 6. FP: XHR INTERCEPT — find the API that loads listings
# ============================================================
def fp_xhr_intercept():
    """Playwright: intercept ALL network requests on category page."""
    from playwright.sync_api import sync_playwright
    all_reqs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        
        def on_req(req):
            all_reqs.append({"url": req.url[:150], "method": req.method})
        page.on("request", on_req)
        
        page.goto("https://www.farpost.ru/vladivostok/auto/misc/",
                  timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        
        # Filter: JSON/XHR requests (not static assets)
        api_reqs = [r for r in all_reqs if any(x in r["url"] for x in ["api", "json", "search", "list", "bulletin", "catalog"])]
        # Also: POST requests
        post_reqs = [r for r in all_reqs if r["method"] == "POST"]
        
        # Save all
        with open(BASE / "d23_fp_all_reqs.json", "w", encoding="utf-8") as f:
            json.dump(all_reqs, f, indent=2)
        
        ok = len(api_reqs) > 0 or len(post_reqs) > 0
        detail = (f"total={len(all_reqs)} api={len(api_reqs)} post={len(post_reqs)}")
        if api_reqs:
            detail += f" api1={api_reqs[0]['url'][:80]}"
        if post_reqs:
            detail += f" post1={post_reqs[0]['url'][:80]}"
        browser.close()
        return ok, detail

# ============================================================
print(f"=== diag23: {time.strftime('%H:%M:%S')} ===", flush=True)
run("FP:pw_category", fp_pw_category)
run("FP:xhr_intercept", fp_xhr_intercept)
run("FP:verify_dump", fp_verify_dump)
run("WB:card_retry", wb_card_retry)
run("WB:card_wb", wb_card_wb)
run("OZ:pw_stealth", oz_pw_stealth)

el = round(time.time() - t0, 1)
results["_meta"] = {"total_s": el, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
out = BASE / "d23_results.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nDONE {el}s -> {out}", flush=True)
