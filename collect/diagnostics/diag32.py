#!/usr/bin/env python3
"""diag32: fast focused extraction. Total target < 90s.
OZ: desktop PW -> JSON-LD full parse + price identification
FP: m.farpost.ru -> JSON-LD parse with encoding fix
WB: basket sweep 1-10 (fast)
"""
import json, re, time, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path(__file__).parent / "d32_results.json"
results = {}
t0 = time.time()

def log(tag, msg):
    el = time.time() - t0
    print(f"[{el:.1f}s] {tag}: {msg}", flush=True)

def safe_get(url, headers=None, timeout=10):
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
    """Try multiple encodings."""
    for enc in ["utf-8", "windows-1251", "cp1251", "latin-1"]:
        try:
            t = raw.decode(enc)
            if '\ufffd' not in t[:200]:
                return t, enc
        except:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-forced"

# ============ OZON: desktop PW + full JSON-LD ============
def ozon_full():
    from playwright.sync_api import sync_playwright
    url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(10000)
        html = page.content()
        browser.close()
    
    # JSON-LD full
    jlds_raw = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    jld_parsed = []
    for j in jlds_raw:
        try:
            obj = json.loads(j.strip())
            jld_parsed.append(obj)
        except Exception as e:
            jld_parsed.append({"err": str(e), "raw": j[:500]})
    
    # Identify product price from JSON-LD
    product_price = None
    product_name = None
    product_brand = None
    product_rating = None
    product_reviews = None
    product_sku = "3481320800"
    
    for jld in jld_parsed:
        if isinstance(jld, dict):
            t = jld.get("@type", "")
            if t in ("Product", "ProductGroup"):
                product_name = jld.get("name", product_name)
                product_brand = (jld.get("brand") or {}).get("name", product_brand) if isinstance(jld.get("brand"), dict) else jld.get("brand", product_brand)
                offers = jld.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                product_price = offers.get("price", offers.get("lowPrice", product_price))
                product_rating = jld.get("aggregateRating", {}).get("ratingValue") if isinstance(jld.get("aggregateRating"), dict) else product_rating
                product_reviews = jld.get("aggregateRating", {}).get("reviewCount") if isinstance(jld.get("aggregateRating"), dict) else product_reviews
            elif t == "BreadcrumbList":
                pass
    
    # Also search in raw HTML for price patterns
    # Ozon typically has: <span class="...">284 ₽</span> or data attributes
    price_patterns = [
        r'"price"\s*:\s*"?(\d+)"?',
        r'"salePrice"\s*:\s*"?(\d+)"?',
        r'"oldPrice"\s*:\s*"?(\d+)"?',
        r'data-price="(\d+)"',
        r'"value"\s*:\s*"?(\d{2,7})"?\s*,\s*"currency"',
    ]
    all_prices = []
    for pp in price_patterns:
        all_prices.extend(re.findall(pp, html))
    
    # First price is usually the product price
    main_price = all_prices[0] if all_prices else None
    
    # Name from h1
    h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    h1_name = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip()[:150] if h1_m else None
    
    detail = (f"len={len(html)} jld={len(jld_parsed)} jld_types={[j.get('@type','?') if isinstance(j,dict) else '?' for j in jld_parsed]} "
              f"jld_price={product_price} html_price={main_price} name={h1_name!r:.60} brand={product_brand} "
              f"rating={product_rating} reviews={product_reviews} all_prices={all_prices[:10]}")
    
    extract = {
        "html_len": len(html),
        "jsonld_full": jld_parsed,
        "product_price_jld": product_price,
        "product_price_html": main_price,
        "all_prices_first10": all_prices[:10],
        "name_jld": product_name,
        "name_h1": h1_name,
        "brand": product_brand,
        "rating": product_rating,
        "reviews": product_reviews,
        "sku": product_sku,
    }
    
    ok = len(html) > 500000 and (product_price is not None or main_price is not None)
    return ok, detail, extract

# ============ FARPOST MOBILE: encoding fix + JLD parse ============
def fp_mobile_fix():
    urls = [
        ("134693091", "https://m.farpost.ru/vladivostok/auto/misc/134693091"),
        ("121063385", "https://m.farpost.ru/vladivostok/auto/misc/121063385"),
    ]
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    
    cards = []
    for bid, u in urls:
        code, raw = safe_get(u, h, timeout=12)
        if code != 200:
            log(f"FP:fix", f"{bid}: HTTP={code}")
            continue
        
        # Try multiple encodings
        html, enc_used = decode_smart(raw)
        
        # Also check charset meta
        charset_m = re.search(r'charset=["\']?([\w-]+)', html[:2000], re.I)
        charset_decl = charset_m.group(1) if charset_m else "unknown"
        
        # JSON-LD
        jlds_raw = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        jld_parsed = []
        for j in jlds_raw:
            try:
                obj = json.loads(j.strip())
                jld_parsed.append(obj)
            except Exception as e:
                jld_parsed.append({"err": str(e), "raw": j[:300]})
        
        # Prices - try broader patterns
        prices = re.findall(r'(\d{2,12})\s*(?:\u20bd|руб|₽|RUB|р\.)', html)
        prices_json = re.findall(r'"(?:price|amount|value)"\s*:\s*"?([\d.]+)"?', html)
        # Look for price in meta
        meta_price = re.findall(r'<meta[^>]*(?:price|amount)[^>]*content="([^"]*)"', html, re.I)
        
        # Title with proper encoding
        title_m = re.search(r'<title>(.*?)</title>', html, re.S)
        h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        
        # All meta tags
        metas = re.findall(r'<meta[^>]*(?:property|name)="([^"]*)"[^>]*content="([^"]*)"', html)
        
        # Look for data in any script
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
        interesting = []
        for s in scripts:
            if len(s) > 50 and any(k in s.lower() for k in ['price', 'bulletin', 'subject', 'offer', 'item']):
                interesting.append(s[:400])
        
        entry = {
            "id": bid,
            "encoding": enc_used,
            "charset_decl": charset_decl,
            "len": len(html),
            "jsonld": jld_parsed,
            "prices": prices[:15],
            "prices_json": prices_json[:15],
            "meta_price": meta_price[:5],
            "title": title_m.group(1).strip()[:100] if title_m else None,
            "h1": h1_m.group(1).strip()[:100] if h1_m else None,
            "metas": metas[:20],
            "interesting_scripts": interesting[:3],
        }
        cards.append(entry)
        
        log(f"FP:fix", f"{bid}: enc={enc_used} charset={charset_decl} len={len(html)} jld={len(jld_parsed)} p={len(prices)} pj={len(prices_json)} title={title_m.group(1).strip()[:60] if title_m else 'N'}")
    
    # Category with encoding
    cat_url = "https://m.farpost.ru/vladivostok/auto/misc"
    code, raw = safe_get(cat_url, h, timeout=15)
    cat_html, cat_enc = decode_smart(raw) if code == 200 else ("", "?")
    
    # Bulletin links - broader pattern
    bull_links = re.findall(r'href="(?:https?://m?\.farpost\.ru)?(/[a-z]+/\d+/[^"]*?/(\d{6,12}))"', cat_html)
    bull_ids = [b[1] for b in bull_links]
    # Also try relative links
    bull_ids2 = re.findall(r'href="/(?:[a-z]+/\d+/)?[^"]*?/(\d{6,12})[^"]*"', cat_html)
    
    # Prices in category
    cat_prices = re.findall(r'(\d{3,12})\s*(?:\u20bd|руб|₽|р\.)', cat_html)
    
    # JSON-LD in category
    cat_jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', cat_html, re.S)
    cat_jld_parsed = []
    for j in cat_jlds:
        try:
            cat_jld_parsed.append(json.loads(j.strip()))
        except:
            pass
    
    # Look for item blocks
    item_blocks = re.findall(r'class="[^"]*(?:item|bulletin|offer|card)[^"]*"', cat_html, re.I)
    
    log(f"FP:cat", f"enc={cat_enc} len={len(cat_html)} bulls={len(bull_ids)}/{len(bull_ids2)} prices={len(cat_prices)} jld={len(cat_jld_parsed)} items={len(item_blocks)}")
    
    ok = any(c.get("jsonld") for c in cards) or len(bull_ids) > 0
    return ok, f"cards={len(cards)} bulls={len(bull_ids)} cat_prices={len(cat_prices)}", {
        "cards": cards,
        "category": {
            "enc": cat_enc, "len": len(cat_html),
            "bull_ids": bull_ids[:20], "bull_ids2": bull_ids2[:20],
            "prices": cat_prices[:20], "jld": cat_jld_parsed[:5],
            "item_blocks": item_blocks[:10],
        }
    }

# ============ WB: fast sweep 1-10 ============
def wb_fast():
    nm = 1097975275
    vol = 10979
    part = 1097975
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    
    hits = []
    for i in range(1, 11):
        for prefix in ["basket", "static-basket"]:
            host = f"{prefix}-{i:02d}.wbbasket.ru"
            u = f"https://{host}/vol{vol}/part{part}/{nm}.json"
            code, data = safe_get(u, h, timeout=5)
            if code == 200 and len(data) > 100:
                try:
                    j = json.loads(data)
                    hits.append({"host": host, "name": j.get("name","")[:60], "price": j.get("salePriceU", j.get("priceU"))})
                    log(f"WB:fast", f"HIT {host}: {j.get('name','')[:40]} price={j.get('salePriceU', j.get('priceU'))}")
                except:
                    hits.append({"host": host, "len": len(data)})
            elif code not in (404, -1):
                log(f"WB:fast", f"{host}: HTTP={code} len={len(data)}")
    
    # wbx-content quick
    for ver in ["v1", "v2"]:
        u = f"https://wbx-content-{ver}.wbstatic.net/card/{nm}.json"
        code, data = safe_get(u, h, timeout=6)
        if code == 200 and len(data) > 100:
            try:
                j = json.loads(data)
                hits.append({"host": f"wbx-{ver}", "name": j.get("name","")[:60], "price": j.get("salePriceU", j.get("priceU"))})
                log(f"WB:fast", f"HIT wbx-{ver}")
            except:
                pass
    
    ok = len(hits) > 0
    return ok, f"tried=20+2 hits={len(hits)}", hits[:10]

# ============ RUN ============
if __name__ == "__main__":
    tests = [
        ("OZ:full", ozon_full),
        ("FP:mobile_fix", fp_mobile_fix),
        ("WB:fast", wb_fast),
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
