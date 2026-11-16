"""diag25: 7 коротких стратегий — FP x3, WB x2, OZ x1, AVITO x1.
Ключевое: точные паттерны из реального HTML d23_fp_cat_pw.html:
  - data-bulletin-id="134693091"
  - data-role="bulletin-link" href="/vladivostok/auto/misc/...-134693091.html"
  - data-role="price" > 500<i class="currency currency__rub">₽</i>
  - data-price="500&lt;i...&gt;₽&lt;/i&gt;"
  - class="bull-item bull-item_inline"
"""
import json, re, time, traceback
from pathlib import Path

results = {}
t0 = time.time()
BASE = Path(__file__).parent

def run(name, fn):
    ts = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"exc: {type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}"
    el = round(time.time() - ts, 1)
    results[name] = {"ok": ok, "detail": detail, "s": el}
    print(f"[{name}] {'PASS' if ok else 'FAIL'} ({el}s) {detail[:300]}", flush=True)
    time.sleep(0.5)

# ============================================================
# 1. FP: curl_cffi category — parse with EXACT patterns from real HTML
# ============================================================
def fp_cat_cffi():
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    url = "https://www.farpost.ru/vladivostok/auto/misc/"
    r = s.get(url, timeout=20)
    if r.status_code != 200:
        return False, f"HTTP={r.status_code} len={len(r.content)}"
    raw = r.content
    try:
        html = raw.decode("windows-1251")
    except:
        html = raw.decode("utf-8", errors="replace")
    
    with open(BASE / "d25_fp_cat.html", "wb") as f:
        f.write(raw)
    
    # EXACT patterns from d23 HTML
    # IDs: data-bulletin-id="134693091"
    ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html)
    # Links: data-role="bulletin-link" href="/vladivostok/...-134693091.html"
    links = re.findall(r'data-role="bulletin-link"\s+href="([^"]+)"', html)
    # Price: data-role="price" > 500<i...>
    prices_role = re.findall(r'data-role="price"[^>]*>(.*?)</div>', html, re.DOTALL)
    # Price: data-price="500&lt;i..."
    prices_attr = re.findall(r'data-price="([^"]*)"', html)
    # Titles from link text
    titles = re.findall(r'data-role="bulletin-link"[^>]*>(.*?)</a>', html, re.DOTALL)
    # Images
    imgs = re.findall(r'src="(https://static\.baza\.farpost\.ru/v/[^"]+)"', html)
    # Container
    items = re.findall(r'class="bull-item[^"]*"', html)
    # data-doc-id
    doc_ids = re.findall(r'data-doc-id=["\'](\d+)["\']', html)
    
    # Clean titles
    clean_titles = [re.sub(r'<[^>]+>', '', t).strip()[:60] for t in titles]
    # Clean prices
    clean_prices = [re.sub(r'<[^>]+>', '', p).strip() for p in prices_role]
    
    n = len(ids)
    ok = n >= 3
    detail = (f"len={len(html)} ids={n} links={len(links)} prices_role={len(prices_role)} "
              f"prices_attr={len(prices_attr)} titles={len(titles)} imgs={len(imgs)} "
              f"items={len(items)} doc_ids={len(doc_ids)} "
              f"sample_ids={ids[:5]} sample_prices={clean_prices[:3]} sample_titles={clean_titles[:2]}")
    return ok, detail

# ============================================================
# 2. FP: Playwright category — full render, extract all listings
# ============================================================
def fp_cat_pw():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        page.goto("https://www.farpost.ru/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.goto("https://www.farpost.ru/vladivostok/auto/misc/", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        
        if "/verify" in page.url:
            page.wait_for_timeout(5000)
        
        html = page.content()
        with open(BASE / "d25_fp_cat_pw.html", "w", encoding="utf-8") as f:
            f.write(html)
        
        ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html)
        links = re.findall(r'data-role="bulletin-link"\s+href="([^"]+)"', html)
        prices = re.findall(r'data-role="price"[^>]*>(.*?)</div>', html, re.DOTALL)
        titles = re.findall(r'data-role="bulletin-link"[^>]*>(.*?)</a>', html, re.DOTALL)
        clean_prices = [re.sub(r'<[^>]+>', '', p).strip() for p in prices]
        clean_titles = [re.sub(r'<[^>]+>', '', t).strip()[:60] for t in titles]
        
        browser.close()
        ok = len(ids) >= 5
        detail = (f"url={page.url[:60]} len={len(html)} ids={len(ids)} links={len(links)} "
                  f"prices={len(prices)} titles={len(titles)} "
                  f"sample_ids={ids[:5]} p={clean_prices[:3]} t={clean_titles[:2]}")
        return ok, detail

# ============================================================
# 3. FP: Playwright card 134693091 — verify + parse
# ============================================================
def fp_card_pw():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        page.goto("https://www.farpost.ru/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.goto("https://www.farpost.ru/vladivostok/134693091.html", timeout=30000, wait_until="domcontentloaded")
        for _ in range(5):
            page.wait_for_timeout(3000)
            if "/verify" not in page.url:
                break
        page.wait_for_timeout(2000)
        html = page.content()
        
        # Price: data-bulletin-price (card page)
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        # Price: viewbull-summary-price__value
        price_m2 = re.search(r'class="[^"]*summary-price[^"]*"[^>]*>(.*?)</', html, re.DOTALL)
        # JSON-LD
        jsonlds = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        jld_price = None
        jld_title = ""
        for j in jsonlds:
            try:
                d = json.loads(j)
                if isinstance(d, dict) and d.get("@type") == "Product":
                    jld_title = d.get("name", "")[:60]
                    off = d.get("offers", {})
                    if isinstance(off, dict):
                        jld_price = off.get("price") or off.get("lowPrice")
                    elif isinstance(off, list) and off:
                        jld_price = off[0].get("price")
            except:
                pass
        title_m = re.search(r'<title>([^<]+)</title>', html)
        
        browser.close()
        ok = bool(price_m or jld_price or price_m2)
        detail = (f"url={page.url[:60]} bp={price_m.group(1) if price_m else 'N/A'} "
                  f"sp={re.sub(chr(60)+'[^>]*'+chr(62),'',price_m2.group(1)).strip()[:30] if price_m2 else 'N/A'} "
                  f"jld_p={jld_price} jld_t={jld_title} "
                  f"title={title_m.group(1)[:50] if title_m else ''} len={len(html)}")
        return ok, detail

# ============================================================
# 4. WB: card.wb.ru/cards/v2/detail
# ============================================================
def wb_v2():
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    url = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
    r = s.get(url, timeout=15, headers={
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
    })
    if r.status_code != 200:
        return False, f"HTTP={r.status_code} len={len(r.content)}"
    data = r.json()
    cards = data.get("data", {}).get("cards", [])
    if not cards:
        return False, f"no cards, keys={list(data.keys())}"
    c = cards[0]
    price = c.get("price", {})
    name = c.get("name", "")[:60]
    brand = c.get("brand", "")
    ok = bool(price)
    detail = f"name={name} brand={brand} price={price}"
    return ok, detail

# ============================================================
# 5. WB: basket CDN card.json (multi-host)
# ============================================================
def wb_basket():
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    vol = nm_id // 10000
    part = nm_id // 1000
    for h in [42, 43, 44, 45, 100, 101, 200, 201]:
        url = f"https://basket-{h}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        try:
            r = s.get(url, timeout=8)
            if r.status_code == 200:
                data = r.json()
                txt = json.dumps(data)
                pm = re.search(r'"(?:price|salePriceW|salePriceM|priceU|priceD|oldPrice)"\s*:\s*"?(\d+)', txt)
                return True, f"host={h} keys={len(data.keys())} price={pm.group(1) if pm else 'N/A'}"
        except:
            continue
    return False, "all hosts failed"

# ============================================================
# 6. OZON: Playwright — JSON-LD product page
# ============================================================
def oz_pw():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.goto("https://www.ozon.ru/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        
        sku = 3481320800
        page.goto(f"https://www.ozon.ru/product/{sku}/", timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        html = page.content()
        
        jsonlds = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        price_val = None
        title_val = ""
        for j in jsonlds:
            try:
                d = json.loads(j)
                if isinstance(d, dict) and d.get("@type") == "Product":
                    title_val = d.get("name", "")[:60]
                    off = d.get("offers", {})
                    if isinstance(off, dict):
                        price_val = off.get("price")
                    elif isinstance(off, list) and off:
                        price_val = off[0].get("price")
            except:
                pass
        if not price_val:
            pm = re.search(r'data-price=["\']([\d.]+)["\']', html)
            if pm:
                price_val = pm.group(1)
        
        title_m = re.search(r'<title>([^<]+)</title>', html)
        browser.close()
        ok = bool(price_val)
        detail = (f"sku={sku} jld={len(jsonlds)} price={price_val} "
                  f"title={title_val or (title_m.group(1)[:50] if title_m else '')} len={len(html)}")
        return ok, detail

# ============================================================
# 7. AVITO: Playwright — category page
# ============================================================
def av_pw():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.goto("https://www.avito.ru/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        
        url = "https://www.avito.ru/vladivostok/smartfony"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        html = page.content()
        
        ids = re.findall(r'href="[^"]*?/a\d+/[^"]*?-(\d+)"', html)
        ids2 = re.findall(r'data-id=["\'](\d+)["\']', html)
        prices = re.findall(r'data-price=["\'](\d+)["\']', html)
        titles = re.findall(r'<h[23][^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h[23]>', html, re.DOTALL)
        
        browser.close()
        ok = len(ids) >= 3 or len(ids2) >= 3
        detail = (f"ids={len(ids)} ids2={len(ids2)} prices={len(prices)} titles={len(titles)} "
                  f"len={len(html)} sample={ids[:5] or ids2[:5]}")
        return ok, detail

# ============================================================
print(f"=== diag25: {time.strftime('%H:%M:%S')} ===", flush=True)
run("FP:cat_cffi", fp_cat_cffi)
run("FP:cat_pw", fp_cat_pw)
run("FP:card_pw", fp_card_pw)
run("WB:v2_detail", wb_v2)
run("WB:basket", wb_basket)
run("OZ:pw", oz_pw)
run("AV:pw", av_pw)

el = round(time.time() - t0, 1)
results["_meta"] = {"total_s": el, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
out = BASE / "d25_results.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nDONE {el}s -> {out}", flush=True)
