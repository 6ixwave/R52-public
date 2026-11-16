#!/usr/bin/env python3
"""diag34: FP item pairing + WB API from d33 + Ozon E2E-ready extract. Target <90s."""
import json, re, time, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path(__file__).parent / "d34_results.json"
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

# ============ FP: pair data-bull-id with prices ============
def fp_pair_items():
    """Category page has 50 data-bull-id and 51 price spans. Pair them."""
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
    
    # Strategy 1: find data-bull-id and nearby price in same block
    # Look for pattern: element with data-bull-id="X" ... price span
    # First, find all positions of data-bull-id
    bull_positions = [(m.start(), m.group(1)) for m in re.finditer(r'data-bull(?:etin)?-?id="(\d+)"', html)]
    
    # Find all price spans with positions
    price_positions = [(m.start(), m.group(1)) for m in re.finditer(r'class="[^"]*(?:price|cost|amount)[^"]*"[^>]*>\s*([\d\s.,]+)', html)]
    
    # Pair: for each bull-id, find the closest price after it
    paired = []
    for bpos, bid in bull_positions:
        # Find closest price after this position (within 500 chars)
        best_price = None
        best_dist = 9999
        for ppos, pval in price_positions:
            dist = ppos - bpos
            if 0 < dist < 500 and dist < best_dist:
                best_dist = dist
                best_price = pval
        if best_price:
            paired.append({"id": bid, "price": best_price, "dist": best_dist})
    
    # Strategy 2: JSON blobs - look for item arrays
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    json_items = []
    for s in scripts:
        s_stripped = s.strip()
        if s_stripped.startswith('{') or s_stripped.startswith('['):
            try:
                obj = json.loads(s_stripped)
                if isinstance(obj, dict):
                    # Look for items/bulletins arrays
                    for key in ["items", "bulletins", "offers", "results", "list", "data"]:
                        if key in obj and isinstance(obj[key], list):
                            json_items.append({"key": key, "count": len(obj[key]), "sample": obj[key][:3]})
                    # Also check nested
                    for k, v in obj.items():
                        if isinstance(v, dict):
                            for key2 in ["items", "bulletins", "offers", "results", "list"]:
                                if key2 in v and isinstance(v[key2], list):
                                    json_items.append({"key": f"{k}.{key2}", "count": len(v[key2]), "sample": v[key2][:3]})
                elif isinstance(obj, list) and len(obj) > 5:
                    json_items.append({"key": "root_list", "count": len(obj), "sample": obj[:3]})
            except:
                pass
    
    # Strategy 3: look for structured blocks
    # Each item might be in a <div> or <li> with both ID and price
    block_pattern = re.findall(
        r'<(?:div|li|tr)[^>]*>(?:(?!</(?:div|li|tr)>).)*?data-bull(?:etin)?-?id="(\d+)"(?:(?!</(?:div|li|tr)>).)*?</(?:div|li|tr)>',
        html, re.S
    )
    
    # Strategy 4: find title near each bull-id
    titles_near = []
    for bpos, bid in bull_positions[:10]:
        snippet = html[max(0,bpos-200):bpos+500]
        title_m = re.search(r'<(?:a|h[1-6]|span)[^>]*>([^<]{10,100})</(?:a|h[1-6]|span)>', snippet)
        if title_m:
            titles_near.append({"id": bid, "title": title_m.group(1).strip()[:80]})
    
    detail = (f"bulls={len(bull_positions)} prices={len(price_positions)} paired={len(paired)} "
              f"jitems={len(json_items)} blocks={len(block_pattern)} titles={len(titles_near)}")
    
    extract = {
        "paired": paired[:30],
        "json_items": json_items[:10],
        "block_ids": block_pattern[:20],
        "titles_near": titles_near[:15],
        "sample_bull_snippet": html[max(0,bull_positions[0][0]-100):bull_positions[0][0]+400] if bull_positions else None,
    }
    
    ok = len(paired) > 5 or len(json_items) > 0
    return ok, detail, extract

# ============ FP: deeper category with www ============
def fp_www_cat():
    """Try www.farpost.ru category (not m.) - might have richer markup."""
    url = "https://www.farpost.ru/vladivostok/auto/misc"
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    code, raw = safe_get(url, h, timeout=15)
    if code != 200:
        return False, f"HTTP={code}", None
    html, enc = decode_smart(raw)
    
    bull_ids = re.findall(r'data-bull(?:etin)?-?id="(\d+)"', html)
    prices = re.findall(r'class="[^"]*(?:price|cost)[^"]*"[^>]*>\s*([\d\s.,]+)', html)
    verify = "verify" in html.lower() or "/verify" in html
    jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    
    # Look for item links with titles
    item_links = re.findall(r'<a[^>]*href="[^"]*?/(\d{6,12})[^"]*"[^>]*title="([^"]*)"', html)
    item_links2 = re.findall(r'<a[^>]*href="[^"]*?/(\d{6,12})[^"]*"[^>]*>([^<]{10,100})<', html)
    
    detail = (f"enc={enc} len={len(html)} bulls={len(bull_ids)} prices={len(prices)} "
              f"verify={verify} jld={len(jlds)} ilinks={len(item_links)}/{len(item_links2)}")
    
    extract = {
        "bull_ids": bull_ids[:30],
        "prices": prices[:30],
        "item_links": item_links[:20],
        "item_links2": [(i[0], i[1].strip()[:60]) for i in item_links2[:20]],
        "verify": verify,
    }
    
    ok = len(bull_ids) > 10 and len(prices) > 10
    return ok, detail, extract

# ============ WB: use API responses from d33 ============
def wb_api_probe():
    """d33 WB:pw captured 3 API responses. Probe those endpoints directly."""
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }
    nm = 1097975275
    
    # Common WB API endpoints
    urls = [
        f"https://card.wb.ru/cards/v2/detail?nm={nm}",
        f"https://card.wb.ru/cards/detail?nm={nm}",
        f"https://catalog.wb.ru/cards/v2/detail?nm={nm}",
        f"https://catalog.wb.ru/cards/detail?nm={nm}",
        f"https://wbx-content-v2.wbstatic.net/card/v2/{nm}.json",
        f"https://wbx-content-v2.wbstatic.net/cards/v2/{nm}.json",
        f"https://static-basket-01.wbbasket.ru/vol10979/part1097975/{nm}.json",
        f"https://static-basket-02.wbbasket.ru/vol10979/part1097975/{nm}.json",
        f"https://static-basket-03.wbbasket.ru/vol10979/part1097975/{nm}.json",
        f"https://static-basket-04.wbbasket.ru/vol10979/part1097975/{nm}.json",
        f"https://static-basket-05.wbbasket.ru/vol10979/part1097975/{nm}.json",
    ]
    
    hits = []
    for u in urls:
        code, data = safe_get(u, h, timeout=8)
        if code == 200 and len(data) > 50:
            try:
                j = json.loads(data)
                # Check structure
                if "data" in j and "cards" in j["data"]:
                    cards = j["data"]["cards"]
                    if cards:
                        c = cards[0]
                        name = c.get("name", "")
                        # Price from sizes
                        price = None
                        if c.get("sizes"):
                            for sz in c["sizes"]:
                                if sz.get("price", {}).get("basic", {}).get("d"):
                                    price = sz["price"]["basic"]["d"]
                                    break
                        if not price:
                            price = c.get("salePriceU", c.get("priceU"))
                        hits.append({"url": u.split('/')[2], "name": name[:60], "price": price, "len": len(data)})
                        log(f"WB:api", f"HIT {u.split('/')[2]}: {name[:40]} price={price}")
                elif "name" in j:
                    hits.append({"url": u.split('/')[2], "name": j.get("name","")[:60], "price": j.get("salePriceU", j.get("priceU")), "len": len(data)})
                    log(f"WB:api", f"HIT {u.split('/')[2]}: {j.get('name','')[:40]} price={j.get('salePriceU', j.get('priceU'))}")
            except:
                hits.append({"url": u.split('/')[2], "len": len(data), "non_json": True})
        elif code not in (404, -1):
            log(f"WB:api", f"{u.split('/')[2]}: HTTP={code} len={len(data)}")
    
    ok = len(hits) > 0
    return ok, f"tried={len(urls)} hits={len(hits)}", hits[:10]

# ============ OZON: E2E-ready structured extract ============
def ozon_e2e():
    """Final Ozon extract in the exact format needed for Inbox."""
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
    product = None
    for j in jlds:
        try:
            obj = json.loads(j.strip())
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                product = obj
                break
        except:
            pass
    
    if not product:
        return False, "no_product_jld", None
    
    offers = product.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    
    record = {
        "source": "ozon",
        "sku": product.get("sku", "3481320800"),
        "name": product.get("name", ""),
        "brand": product.get("brand", ""),
        "price": float(offers.get("price", 0)),
        "currency": offers.get("priceCurrency", "RUB"),
        "availability": offers.get("availability", ""),
        "rating": float(product.get("aggregateRating", {}).get("ratingValue", 0)) if isinstance(product.get("aggregateRating"), dict) else None,
        "reviews": int(product.get("aggregateRating", {}).get("reviewCount", 0)) if isinstance(product.get("aggregateRating"), dict) else None,
        "url": url,
        "image": product.get("image", ""),
        "description": product.get("description", "")[:500],
    }
    
    detail = f"sku={record['sku']} price={record['price']} name={record['name'][:50]!r} brand={record['brand']}"
    ok = record["price"] > 0 and record["name"]
    return ok, detail, record

# ============ RUN ============
if __name__ == "__main__":
    tests = [
        ("FP:pair", fp_pair_items),
        ("FP:www_cat", fp_www_cat),
        ("WB:api", wb_api_probe),
        ("OZ:e2e", ozon_e2e),
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
