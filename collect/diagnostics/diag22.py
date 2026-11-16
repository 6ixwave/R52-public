"""diag22: 6 стратегий — FP cat structure, FP PW card, WB card dump, WB search v5, OZ PW wait, AV cat."""
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
        ok, detail = False, f"exc: {type(e).__name__}: {e}"
    el = round(time.time() - ts, 1)
    results[name] = {"ok": ok, "detail": detail, "s": el}
    print(f"[{name}] {'PASS' if ok else 'FAIL'} ({el}s) {detail}", flush=True)

# ============================================================
# 1. FP: CATEGORY — dump HTML structure around first 3 cards
# ============================================================
def fp_cat_structure():
    """Fetch category, find exact HTML pattern for listing cards."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    url = "https://www.farpost.ru/vladivostok/auto/misc/"
    r = s.get(url, timeout=15)
    if r.status_code != 200:
        return False, f"HTTP={r.status_code}"
    html = r.content.decode("windows-1251", errors="replace")
    
    # Find all bulletin IDs
    ids = re.findall(r'href="([^"]*?(\d{5,})\.html)"', html)
    # Find all prices (multiple patterns)
    prices_attr = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
    prices_class = re.findall(r'class="[^"]*price[^"]*__value[^"]*"[^>]*>([\d\s\xa0]+)', html)
    prices_class = [p.replace(" ","").replace("\xa0","") for p in prices_class]
    # Find titles
    titles = re.findall(r'data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
    titles = [re.sub(r'<[^>]+>','',t).strip()[:80] for t in titles]
    # Find images
    imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*baza\.farpost\.ru[^"\']*)["\']', html)
    
    # Dump a 2000-char window around first card ID
    sample_html = ""
    if ids:
        first_id = ids[0][1]
        idx = html.find(first_id)
        if idx > 0:
            sample_html = html[max(0,idx-500):idx+1500]
    
    # Save full category HTML for analysis
    with open(BASE / "d22_fp_cat.html", "w", encoding="utf-8") as f:
        f.write(html)
    # Save structure summary
    summary = {
        "n_ids": len(ids),
        "n_prices_attr": len(prices_attr),
        "n_prices_class": len(prices_class),
        "n_titles": len(titles),
        "n_imgs": len(imgs),
        "first_5_ids": [i[1] for i in ids[:5]],
        "first_5_prices_attr": prices_attr[:5],
        "first_5_prices_class": prices_class[:5],
        "first_5_titles": titles[:5],
        "first_3_imgs": imgs[:3],
        "html_len": len(html),
    }
    with open(BASE / "d22_fp_cat_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    ok = len(ids) >= 5 and (len(prices_attr) >= 3 or len(prices_class) >= 3)
    detail = (f"ids={len(ids)} p_attr={len(prices_attr)} p_cls={len(prices_class)} "
              f"titles={len(titles)} imgs={len(imgs)} len={len(html)}")
    return ok, detail

# ============================================================
# 2. FP: PLAYWRIGHT CARD — let JS solve /verify
# ============================================================
def fp_pw_card():
    """Playwright: load card, wait for verify JS to redirect back."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        # Warm-up
        page.goto("https://www.farpost.ru/", timeout=20000)
        page.wait_for_timeout(2000)
        # Card
        url = "https://www.farpost.ru/vladivostok/134693091.html"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # Wait up to 15s for verify to resolve
        for i in range(5):
            page.wait_for_timeout(3000)
            cur = page.url
            if "/verify" not in cur:
                break
        html = page.content()
        final_url = page.url
        has_verify = "/verify" in final_url
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        jsonlds = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        jsonld_price = any('"price"' in j for j in jsonlds)
        title_m = re.search(r'<title>([^<]+)</title>', html)
        
        ok = (not has_verify) and (bool(price_m) or jsonld_price)
        detail = (f"url={final_url[:70]} verify={has_verify} "
                  f"price={price_m.group(1) if price_m else 'N/A'} "
                  f"jsonld={jsonld_price} title={title_m.group(1)[:50] if title_m else ''} "
                  f"len={len(html)}")
        # Save page if successful
        if ok:
            with open(BASE / "d22_fp_card_pw.html", "w", encoding="utf-8") as f:
                f.write(html)
        browser.close()
        return ok, detail

# ============================================================
# 3. WB: CARD.JSON FULL DUMP
# ============================================================
def wb_card_full():
    """Dump card.json, find ALL numeric fields that could be price."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    vol = nm_id // 10000
    part = nm_id // 1000
    url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    r = s.get(url, timeout=10)
    if r.status_code != 200:
        return False, f"HTTP={r.status_code}"
    data = r.json()
    with open(BASE / "d22_wb_card.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Find all numeric fields
    def find_numbers(obj, path=""):
        nums = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                nums.extend(find_numbers(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:3]):
                nums.extend(find_numbers(v, f"{path}[{i}]"))
        elif isinstance(obj, (int, float)) and obj > 100:
            nums.append((path, obj))
        return nums
    
    nums = find_numbers(data)
    # Filter: price-like paths or values in range 100-1000000
    price_like = [(p, v) for p, v in nums if any(x in p.lower() for x in ['price','cost','sale','discount','old'])]
    all_nums = [(p, v) for p, v in nums if 100 <= v <= 100000000]
    
    ok = len(price_like) > 0
    detail = f"keys={list(data.keys())} price_like={price_like[:5]} nums_100m={all_nums[:8]}"
    return ok, detail

# ============================================================
# 4. WB: search.wb.ru v5 (different from card.wb.ru)
# ============================================================
def wb_search_v5():
    """search.wb.ru exactmatch v5 — returns products with priceD/priceU."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    url = (f"https://search.wb.ru/exactmatch/ru/common/v5/search"
           f"?appType=1&curr=rub&dest=-1257786&query=&nm={nm_id}"
           f"&resultset=catalog&sort=popular&spp=30&spw=1")
    r = s.get(url, timeout=12, headers={
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    })
    if r.status_code != 200:
        return False, f"HTTP={r.status_code} len={len(r.content)}"
    data = r.json()
    # Structure varies: data.data.products[] or data.products[]
    d = data.get("data", data)
    prods = d.get("products", [])
    if not prods:
        txt = json.dumps(data)[:300]
        return True, f"OK but no products key. len={len(r.content)} sample={txt}"
    p = prods[0]
    price_d = p.get("priceD", 0)
    price_u = p.get("priceU", 0)
    name = p.get("name", "")[:60]
    brand = p.get("brand", "")
    ok = price_d > 0 or price_u > 0
    detail = f"n={len(prods)} priceD={price_d} priceU={price_u} name={name} brand={brand}"
    return ok, detail

# ============================================================
# 5. OZON: Playwright with longer wait + network capture
# ============================================================
def oz_pw_wait():
    """Ozon Playwright: longer wait, capture XHR for price."""
    from playwright.sync_api import sync_playwright
    captured = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        
        def on_response(resp):
            try:
                if "json" in (resp.headers.get("content-type","")) and resp.status == 200:
                    url = resp.url
                    if any(x in url for x in ["composer","pdp","product","price"]):
                        body = resp.text()[:500]
                        captured.append({"url": url[:100], "body": body})
            except:
                pass
        page.on("response", on_response)
        
        # Warm-up
        page.goto("https://www.ozon.ru/", timeout=25000)
        page.wait_for_timeout(4000)
        # Product
        url = "https://www.ozon.ru/product/3481320800/"
        page.goto(url, wait_until="networkidle", timeout=35000)
        page.wait_for_timeout(5000)
        
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
            except: pass
        
        ok = (price_val is not None) or len(captured) > 0
        detail = (f"title={title_m.group(1)[:50] if title_m else ''} "
                  f"jsonlds={len(jsonlds)} price={price_val} "
                  f"xhr={len(captured)} len={len(html)}")
        if captured:
            detail += f" first_xhr={captured[0]['url']}"
        browser.close()
        return ok, detail

# ============================================================
# 6. AVITO: category with proper headers
# ============================================================
def av_cat():
    """Avito category page with mobile UA + proper headers."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    # Try mobile first (less likely to be blocked)
    urls = [
        "https://www.avito.ru/moskva/smartfony",
        "https://m.avito.ru/moskva/smartfony",
    ]
    for u in urls:
        try:
            r = s.get(u, timeout=12, headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            html = r.text
            # Find ad IDs: /ad/123456 or data-ad-id
            ids = re.findall(r'/ad/(\d{8,})', html)
            ids2 = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
            prices = re.findall(r'data-price=["\'](\d+)["\']', html)
            if r.status_code == 200 and (ids or ids2):
                return True, f"OK {u} ids={len(ids)+len(ids2)} prices={len(prices)} len={len(html)}"
            if r.status_code == 429:
                continue
        except Exception as e:
            continue
    return False, "all AV category URLs failed (429 or no IDs)"

# ============================================================
print(f"=== diag22: {time.strftime('%H:%M:%S')} ===", flush=True)
run("FP:cat_structure", fp_cat_structure)
run("FP:pw_card", fp_pw_card)
run("WB:card_full", wb_card_full)
run("WB:search_v5", wb_search_v5)
run("OZ:pw_wait", oz_pw_wait)
run("AV:category", av_cat)

el = round(time.time() - t0, 1)
results["_meta"] = {"total_s": el, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
out = BASE / "d22_results.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nDONE {el}s -> {out}", flush=True)
