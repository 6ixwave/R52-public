#!/usr/bin/env python3
"""diag31: focused extraction from confirmed endpoints.
OZ: desktop PW 12s wait -> 1MB page -> JSON-LD + price
FP: m.farpost.ru JSON-LD parse (2 blocks per card)
WB: basket sweep 1-20 + wbx-content
AV: quick mobile category
"""
import json, re, time, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path(__file__).parent / "d31_results.json"
results = {}
t0 = time.time()

def log(tag, msg):
    el = time.time() - t0
    print(f"[{el:.1f}s] {tag}: {msg}", flush=True)

def safe_get(url, headers=None, timeout=15):
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

# ============ OZON: desktop PW confirmed 1MB ============
def ozon_desktop_extract():
    """Desktop PW with 12s wait gave 1MB, 158 prices, 1 JLD. Extract properly."""
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
        page.wait_for_timeout(12000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        html = page.content()
        
        # Also grab specific elements
        try:
            price_el = page.query_selector('[data-qa="price-action"]')
            price_text = price_el.inner_text() if price_el else None
        except:
            price_text = None
        
        try:
            title_el = page.query_selector('h1')
            title_text = title_el.inner_text() if title_el else None
        except:
            title_text = None
        
        browser.close()
    
    # Parse JSON-LD
    jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    jld_parsed = []
    for j in jlds:
        try:
            jld_parsed.append(json.loads(j.strip()))
        except:
            jld_parsed.append({"raw_preview": j[:500]})
    
    # Extract all price patterns
    prices_rub = re.findall(r'(\d[\d\s]*)\s*(?:\u20bd|руб|₽)', html)
    prices_json = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', html)
    prices_old = re.findall(r'"oldPrice"\s*:\s*"?([\d.]+)"?', html)
    
    # Find the main product price (usually first or in specific widget)
    price_widget = re.findall(r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d\s]*)\s*(?:\u20bd|₽)', html, re.S)
    
    # Product name from h1
    h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    name = h1_m.group(1).strip()[:120] if h1_m else None
    
    # Brand
    brand_m = re.search(r'"brand"\s*:\s*"([^"]+)"', html)
    brand = brand_m.group(1) if brand_m else None
    
    # Rating
    rating_m = re.findall(r'"rating"\s*:\s*([\d.]+)', html)
    
    # Reviews count
    reviews_m = re.findall(r'"(?:reviewCount|ratingCount)"\s*:\s*(\d+)', html)
    
    # SKU
    sku_m = re.search(r'/product/.*?-(\d{6,12})/', url)
    sku = sku_m.group(1) if sku_m else "3481320800"
    
    detail = (f"len={len(html)} jld={len(jld_parsed)} prices_rub={len(prices_rub)} "
              f"pj={len(prices_json)} name={name!r:.60} brand={brand} "
              f"rating={rating_m[:3]} reviews={reviews_m[:2]} price_el={price_text!r}")
    
    extract = {
        "html_len": len(html),
        "jsonld": jld_parsed,
        "prices_rub": prices_rub[:30],
        "prices_json": prices_json[:30],
        "prices_old": prices_old[:10],
        "price_widget": price_widget[:10],
        "price_element": price_text,
        "name": name,
        "brand": brand,
        "rating": rating_m[:5],
        "reviews": reviews_m[:5],
        "sku": sku,
    }
    
    ok = len(html) > 500000 and (len(jld_parsed) > 0 or len(prices_rub) > 10)
    return ok, detail, extract

# ============ FARPOST MOBILE: parse JSON-LD blocks ============
def fp_mobile_jld():
    """m.farpost.ru cards have 2 JSON-LD each. Parse them properly."""
    urls = [
        ("134693091", "https://m.farpost.ru/vladivostok/auto/misc/134693091"),
        ("121063385", "https://m.farpost.ru/vladivostok/auto/misc/121063385"),
    ]
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    
    all_cards = []
    for bid, u in urls:
        code, data = safe_get(u, h, timeout=15)
        if code != 200:
            log(f"FP:jld", f"{bid}: HTTP={code}")
            continue
        html = data.decode("utf-8", errors="replace")
        
        # All JSON-LD
        jlds_raw = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        jld_parsed = []
        for j in jlds_raw:
            try:
                obj = json.loads(j.strip())
                jld_parsed.append(obj)
            except Exception as e:
                jld_parsed.append({"error": str(e), "raw": j[:300]})
        
        # Look for price in any form
        prices = re.findall(r'(\d{2,12})\s*(?:\u20bd|руб|₽|RUB)', html)
        prices_json = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', html)
        prices_offer = re.findall(r'"(?:price|amount)"\s*:\s*"?([\d.]+)"?', html)
        
        # Title / subject
        title_m = re.search(r'<title>(.*?)</title>', html, re.S)
        h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        og_title = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        
        # Look for data in meta tags
        metas = re.findall(r'<meta[^>]*(?:property|name)="([^"]*)"[^>]*content="([^"]*)"', html)
        
        # Embedded data in scripts (not JSON-LD)
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
        data_scripts = []
        for s in scripts:
            if len(s) > 100 and ('price' in s.lower() or 'bulletin' in s.lower() or 'subject' in s.lower()):
                data_scripts.append(s[:500])
        
        entry = {
            "id": bid,
            "http": code,
            "len": len(html),
            "jsonld": jld_parsed,
            "prices": prices[:15],
            "prices_json": prices_json[:15],
            "prices_offer": prices_offer[:15],
            "title": title_m.group(1).strip() if title_m else None,
            "h1": h1_m.group(1).strip()[:100] if h1_m else None,
            "og_title": og_title.group(1) if og_title else None,
            "metas": metas[:15],
            "data_scripts": data_scripts[:3],
        }
        all_cards.append(entry)
        
        log(f"FP:jld", f"{bid}: len={len(html)} jld={len(jld_parsed)} p={len(prices)} pj={len(prices_json)} title={title_m.group(1).strip()[:50] if title_m else 'N'}")
    
    # Category page
    cat_url = "https://m.farpost.ru/vladivostok/auto/misc"
    code, data = safe_get(cat_url, h, timeout=15)
    html = data.decode("utf-8", errors="replace") if code == 200 else ""
    jlds_cat = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    jld_cat_parsed = []
    for j in jlds_cat:
        try:
            jld_cat_parsed.append(json.loads(j.strip()))
        except:
            pass
    prices_cat = re.findall(r'(\d{3,12})\s*(?:\u20bd|руб|₽)', html)
    # Look for bulletin links
    bull_links = re.findall(r'href="(?:https?://m\.farpost\.ru)?/vladivostok/[^"]*?/(\d{6,12})"', html)
    
    log(f"FP:cat", f"len={len(html)} jld={len(jld_cat_parsed)} prices={len(prices_cat)} bulls={len(bull_links)}")
    
    ok = any(c.get("jsonld") for c in all_cards)
    return ok, f"cards={len(all_cards)} cat_bulls={len(bull_links)}", {"cards": all_cards, "category": {"len": len(html), "jld": jld_cat_parsed, "prices": prices_cat[:20], "bulls": bull_links[:20]}}

# ============ WB: quick basket sweep 1-20 ============
def wb_quick_sweep():
    nm = 1097975275
    vol = 10979
    part = 1097975
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    
    for i in range(1, 21):
        for prefix in ["basket", "static-basket"]:
            host = f"{prefix}-{i:02d}.wbbasket.ru"
            u = f"https://{host}/vol{vol}/part{part}/{nm}.json"
            code, data = safe_get(u, h, timeout=6)
            if code == 200 and len(data) > 100:
                try:
                    j = json.loads(data)
                    name = j.get("name", "")
                    price = j.get("salePriceU", j.get("priceU", None))
                    log(f"WB:sweep", f"HIT {host}: {name[:50]} price={price}")
                    return True, f"host={host} name={name!r:.50} price={price}", j
                except:
                    pass
    
    # wbx-content
    for ver in ["v1", "v2", "v3"]:
        u = f"https://wbx-content-{ver}.wbstatic.net/card/{nm}.json"
        code, data = safe_get(u, h, timeout=8)
        if code == 200 and len(data) > 100:
            try:
                j = json.loads(data)
                return True, f"wbx-{ver}: len={len(data)}", j
            except:
                pass
    
    return False, "sweep_1_20_failed", None

# ============ AVITO: quick mobile ============
def avito_quick():
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html",
    }
    urls = [
        "https://m.avito.ru/vladivostok/smartfony",
        "https://m.avito.ru/vladivostok/avtomobili",
    ]
    for u in urls:
        code, data = safe_get(u, h, timeout=12)
        html = data.decode("utf-8", errors="replace") if code == 200 else ""
        offers = re.findall(r'data-qa="offer-card"|class="[^"]*offer[^"]*"', html)
        prices = re.findall(r'(\d{4,12})\s*(?:\u20bd|руб|₽)', html)
        detail = f"HTTP={code} len={len(html)} offers={len(offers)} prices={len(prices)}"
        log(f"AV:quick", f"{u.split('/')[2]}: {detail}")
        if code == 200 and len(offers) > 0:
            return True, f"{u.split('/')[2]}: {detail}", {"offers": offers[:10], "prices": prices[:10]}
    return False, "avito_quick_failed", None

# ============ RUN ============
if __name__ == "__main__":
    tests = [
        ("OZ:desktop", ozon_desktop_extract),
        ("FP:mobile_jld", fp_mobile_jld),
        ("WB:quick_sweep", wb_quick_sweep),
        ("AV:quick", avito_quick),
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
