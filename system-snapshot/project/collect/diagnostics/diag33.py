#!/usr/bin/env python3
"""diag33: FP category item parse + Ozon quick + WB Playwright. Target <90s."""
import json, re, time, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path(__file__).parent / "d33_results.json"
results = {}
t0 = time.time()

def log(tag, msg):
    el = time.time() - t0
    print(f"[{el:.1f}s] {tag}: {msg}", flush=True)

def safe_get(url, headers=None, timeout=12):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    try:
        resp = urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return -1, str(e).encode()

def decode_smart(raw):
    for enc in ["utf-8", "windows-1251", "cp1251"]:
        try:
            t = raw.decode(enc)
            if '\ufffd' not in t[:200]:
                return t, enc
        except:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-forced"

# ============ FP: category item structure ============
def fp_cat_items():
    """Parse the 316KB category page for individual item blocks."""
    url = "https://m.farpost.ru/vladivostok/auto/misc"
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    code, raw = safe_get(url, h, timeout=15)
    if code != 200:
        return False, f"HTTP={code}", None
    html, enc = decode_smart(raw)
    
    # Find all <div class="item"> or <li class="item"> blocks
    # First, let's see the structure around "item" class
    # Look for patterns: data-bull-id, data-id, href with ID, price spans
    
    # Method 1: find all blocks with class containing "item"
    item_blocks = re.findall(r'<(?:div|li|a)[^>]*class="[^"]*item[^"]*"[^>]*>(.*?)(?=<(?:div|li|a)[^>]*class="[^"]*item[^"]*"|$)', html, re.S)
    
    # Method 2: look for specific data attributes
    data_bull = re.findall(r'data-bull(?:etin)?-?id="(\d+)"', html)
    data_id = re.findall(r'data-id="(\d+)"', html)
    
    # Method 3: links to bulletin pages
    bull_hrefs = re.findall(r'href="([^"]*?/(\d{6,12})[^"]*)"', html)
    
    # Method 4: price patterns in context
    # Look for price elements: <span class="...price...">12345</span>
    price_spans = re.findall(r'class="[^"]*(?:price|cost|amount)[^"]*"[^>]*>\s*([\d\s.,]+)\s*(?:\u20bd|руб|₽|р\.)?', html)
    
    # Method 5: look for JSON data embedded in the page
    # Farpost often embeds data in <script> as JSON
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    json_blobs = []
    for s in scripts:
        s_stripped = s.strip()
        if s_stripped.startswith('{') or s_stripped.startswith('['):
            try:
                obj = json.loads(s_stripped)
                json_blobs.append(obj)
            except:
                pass
    
    # Method 6: look for the "items" container and parse its children
    items_container = re.search(r'<(?:div|ul)[^>]*class="[^"]*items[^"]*"[^>]*>(.*?)(?:</(?:div|ul)>\s*</(?:div|ul)>)', html, re.S)
    
    # Method 7: broader - find all price-like numbers near bulletin IDs
    # Pattern: near an ID, find a number that looks like a price
    id_price_pairs = re.findall(r'(\d{6,12})[^<]{0,200}?([\d,]{4,12})\s*(?:\u20bd|руб|₽|р\.)', html)
    
    # Method 8: look for specific Farpost markup patterns
    # Farpost items often: <a href="/vladivostok/auto/misc/12345">Title</a> ... <span>Price</span>
    fp_items = re.findall(
        r'<a[^>]*href="[^"]*?/(\d{6,12})[^"]*"[^>]*>(.*?)</a>',
        html, re.S
    )
    
    # Method 9: data-qa or data-test attributes
    data_qa = re.findall(r'data-qa="([^"]*)"', html)
    data_test = re.findall(r'data-test="([^"]*)"', html)
    
    # Get first 3 item blocks in detail (raw HTML snippet)
    sample_blocks = []
    for m in re.finditer(r'<(?:div|li)[^>]*class="[^"]*item[^"]*"[^>]*>', html):
        start = m.start()
        end = min(start + 800, len(html))
        sample_blocks.append(html[start:end])
        if len(sample_blocks) >= 3:
            break
    
    detail = (f"enc={enc} len={len(html)} items={len(item_blocks)} dbull={len(data_bull)} "
              f"did={len(data_id)} bhrefs={len(bull_hrefs)} pspans={len(price_spans)} "
              f"jblobs={len(json_blobs)} idprice={len(id_price_pairs)} fpitems={len(fp_items)} "
              f"dqa={len(data_qa)} dtest={len(data_test)}")
    
    extract = {
        "encoding": enc,
        "html_len": len(html),
        "item_blocks_count": len(item_blocks),
        "data_bull_ids": data_bull[:20],
        "data_ids": data_id[:20],
        "bull_hrefs_sample": bull_hrefs[:15],
        "price_spans": price_spans[:20],
        "json_blobs_keys": [list(b.keys())[:10] if isinstance(b, dict) else "list" for b in json_blobs[:5]],
        "id_price_pairs": id_price_pairs[:15],
        "fp_items_sample": [(i[0], re.sub(r'<[^>]+>', '', i[1]).strip()[:60]) for i in fp_items[:15]],
        "sample_blocks": sample_blocks,
        "data_qa_sample": data_qa[:20],
    }
    
    ok = len(fp_items) > 5 or len(price_spans) > 5 or len(id_price_pairs) > 5
    return ok, detail, extract

# ============ FP: www.farpost.ru query page ============
def fp_query_page():
    """The og:url showed ?query=ID pattern. Try www.farpost.ru with query param."""
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    urls = [
        "https://www.farpost.ru/vladivostok/auto/misc/?query=134693091",
        "https://www.farpost.ru/vladivostok/auto/misc/?query=121063385",
    ]
    cards = []
    for u in urls:
        code, raw = safe_get(u, h, timeout=12)
        if code != 200:
            log(f"FP:q", f"{u.split('query=')[1]}: HTTP={code}")
            continue
        html, enc = decode_smart(raw)
        
        # Look for Product JSON-LD
        jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        jld_parsed = []
        for j in jlds:
            try:
                jld_parsed.append(json.loads(j.strip()))
            except:
                pass
        
        # Prices
        prices = re.findall(r'(\d{3,12})\s*(?:\u20bd|руб|₽|р\.)', html)
        # Subject/title
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        # Bulletin-specific data
        bull_data = re.findall(r'data-bull(?:etin)?-?id="(\d+)"', html)
        price_data = re.findall(r'data-price="(\d+)"', html)
        
        entry = {
            "query": u.split("query=")[1],
            "http": code,
            "enc": enc,
            "len": len(html),
            "jld_count": len(jld_parsed),
            "jld_types": [j.get("@type", "?") for j in jld_parsed if isinstance(j, dict)],
            "prices": prices[:10],
            "h1": re.sub(r'<[^>]+>', '', h1.group(1)).strip()[:80] if h1 else None,
            "bull_data": bull_data[:5],
            "price_data": price_data[:5],
        }
        cards.append(entry)
        log(f"FP:q", f"{entry['query']}: len={len(html)} jld={len(jld_parsed)} p={len(prices)} h1={entry['h1']!r:.50}")
    
    ok = any(c.get("prices") or c.get("jld_count", 0) > 1 for c in cards)
    return ok, f"cards={len(cards)}", cards

# ============ OZON: quick confirm ============
def ozon_quick():
    """Quick PW check - just confirm JSON-LD is still there."""
    from playwright.sync_api import sync_playwright
    url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(8000)
        html = page.content()
        browser.close()
    
    jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    jld_parsed = []
    for j in jlds:
        try:
            jld_parsed.append(json.loads(j.strip()))
        except:
            pass
    
    product = None
    for j in jld_parsed:
        if isinstance(j, dict) and j.get("@type") == "Product":
            product = j
            break
    
    price = product.get("offers", {}).get("price") if product else None
    name = product.get("name", "")[:80] if product else None
    
    detail = f"len={len(html)} jld={len(jld_parsed)} price={price} name={name!r}"
    ok = product is not None and price is not None
    return ok, detail, {"price": price, "name": name, "html_len": len(html), "jsonld": product}

# ============ WB: Playwright product page ============
def wb_playwright():
    """WB basket CDN is flaky. Try Playwright on the product page directly."""
    from playwright.sync_api import sync_playwright
    nm = 1097975275
    url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
        )
        page = ctx.new_page()
        
        # Capture API responses
        api_responses = []
        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct and resp.status == 200:
                try:
                    body = resp.text()
                    if len(body) > 100:
                        api_responses.append({"url": resp.url[:120], "len": len(body), "preview": body[:300]})
                except:
                    pass
        
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(10000)
        
        html = page.content()
        
        # Extract from page
        jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        jld_parsed = []
        for j in jlds:
            try:
                jld_parsed.append(json.loads(j.strip()))
            except:
                pass
        
        # WB often has price in specific elements
        prices = re.findall(r'(\d[\d\s]*)\s*(?:\u20bd|руб|₽)', html)
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        name = re.sub(r'<[^>]+>', '', h1.group(1)).strip()[:80] if h1 else None
        
        # Look for __NEXT_DATA__ or similar
        next_data = re.search(r'__NEXT_DATA__\s*=\s*(\{.*?\})\s*</script>', html, re.S)
        
        browser.close()
    
    detail = (f"len={len(html)} jld={len(jld_parsed)} prices={len(prices)} "
              f"name={name!r} api={len(api_responses)} next={bool(next_data)}")
    
    extract = {
        "html_len": len(html),
        "jld": jld_parsed[:3],
        "prices": prices[:15],
        "name": name,
        "api_responses": api_responses[:10],
        "has_next_data": bool(next_data),
    }
    
    ok = len(html) > 50000 and (len(prices) > 0 or len(jld_parsed) > 0)
    return ok, detail, extract

# ============ RUN ============
if __name__ == "__main__":
    tests = [
        ("FP:cat_items", fp_cat_items),
        ("FP:query", fp_query_page),
        ("OZ:quick", ozon_quick),
        ("WB:pw", wb_playwright),
    ]
    
    for tag, fn in tests:
        try:
            ok, detail, extra = fn()
            results[tag] = {"ok": ok, "detail": detail, "s": round(time.time() - t0, 1)}
            if extra:
                results[tag]["data"] = extra
            log(tag, f"{'PASS' if ok else 'FAIL'}: {detail}")
        except Exception as e:
            import traceback
            results[tag] = {"ok": False, "detail": f"EXC: {e}", "s": round(time.time() - t0, 1)}
            log(tag, f"EXC: {e}\n{traceback.format_exc()}")
    
    results["_meta"] = {"total_s": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{time.time()-t0:.1f}s] DONE -> {OUT.name}", flush=True)
