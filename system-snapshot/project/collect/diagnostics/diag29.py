#!/usr/bin/env python3
"""diag29: focused extraction from confirmed working endpoints.
Ozon: pw_mobile JSON-LD + price parse
WB: basket-42 full JSON + wbx-content + catalog session
Farpost: card HTML embedded state + m.farpost
Avito: m.avito + specific offer + sitemap
"""
import json, re, time, sys, os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser

OUT = Path(__file__).parent / "d29_results.json"
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

# ============ OZON: pw_mobile deep extract ============
def ozon_mobile_pw():
    from playwright.sync_api import sync_playwright
    url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            locale="ru-RU"
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        html = page.content()
        browser.close()
    
    # Extract JSON-LD
    jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    jld_data = []
    for j in jlds:
        try:
            jld_data.append(json.loads(j.strip()))
        except:
            pass
    
    # Extract prices
    prices = re.findall(r'(\d{2,7})\s*(?:\u20bd|руб|₽)', html)
    prices2 = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', html)
    prices3 = re.findall(r'data-price="(\d+)"', html)
    
    # Check for product name
    name_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    name = name_m.group(1).strip() if name_m else None
    og_title = re.search(r'property="og:title"\s+content="([^"]*)"', html)
    
    # Check if skeleton or real
    skeleton = 'skeleton' in html.lower() or 'loading' in html.lower()
    real_content = len(html) > 100000
    
    # Try to find product JSON in scripts
    prod_json = re.findall(r'window\.__[A-Z_]+__\s*=\s*(\{.*?\});?\s*</script>', html, re.S)
    
    detail = f"len={len(html)} jld={len(jld_data)} prices={len(prices)} p2={len(prices2)} p3={len(prices3)} name={name!r:.60} og={og_title.group(1) if og_title else 'N'} skel={skeleton} prod_json={len(prod_json)}"
    
    # Save full extraction
    extract = {
        "html_len": len(html),
        "jsonld": jld_data,
        "prices_regex": prices[:20],
        "prices_json": prices2[:20],
        "prices_data": prices3[:20],
        "name": name,
        "og_title": og_title.group(1) if og_title else None,
        "skeleton": skeleton,
        "prod_json_count": len(prod_json),
    }
    
    ok = len(jld_data) > 0 and (len(prices) > 0 or len(prices2) > 0)
    return ok, detail, extract

# ============ OZON: composer API with session ============
def ozon_composer_session():
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urlopen
    # Step 1: get cookies from main page
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    req = Request("https://www.ozon.ru/", headers=h)
    try:
        resp = urlopen(req, timeout=10)
        cookies = dict(resp.headers)
        set_cookies = resp.headers.get_all('Set-Cookie', [])
    except Exception as e:
        return False, f"main_page_fail: {e}", None
    
    # Step 2: try composer with cookies
    sku = 3481320800
    urls = [
        f"https://api.ozon.ru/composer-api.bx/p/product/{sku}/v1",
        f"https://www.ozon.ru/api/composer-api.bx/p/product/{sku}/v1",
        f"https://api.ozon.ru/composer-api.bx/p/{sku}",
    ]
    for u in urls:
        req2 = Request(u, headers={**h, "Referer": "https://www.ozon.ru/", "Accept": "application/json"})
        try:
            resp2 = urlopen(req2, timeout=10)
            data = resp2.read()
            if len(data) > 50:
                try:
                    j = json.loads(data)
                    return True, f"HTTP=200 len={len(data)} keys={list(j.keys())[:5]}", j
                except:
                    return True, f"HTTP=200 len={len(data)} non-json", None
            else:
                return False, f"HTTP=200 len={len(data)}", None
        except HTTPError as e:
            continue
        except Exception as e:
            continue
    return False, "all_composer_failed", None

# ============ WB: basket-42 full JSON ============
def wb_basket_full():
    nm = 1097975275
    vol = 10979
    part = 1097975
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    
    # basket-42 confirmed working in d28
    urls = [
        f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm}.json",
        f"https://static-basket-42.wbbasket.ru/vol{vol}/part{part}/{nm}.json",
        f"https://wbx-content-v2.wbstatic.net/card/{nm}.json",
        f"https://wbx-content-v1.wbstatic.net/card/{nm}.json",
    ]
    for u in urls:
        code, data = safe_get(u, h, timeout=12)
        if code == 200 and len(data) > 100:
            try:
                j = json.loads(data)
                name = j.get("name", "")
                price = j.get("salePriceU", j.get("priceU", j.get("price", None)))
                brand = j.get("brand", "")
                detail = f"host={u.split('/')[2]} len={len(data)} name={name!r:.50} price={price} brand={brand}"
                return True, detail, j
            except:
                return True, f"host={u.split('/')[2]} len={len(data)} non-json", None
        else:
            log(f"WB:basket", f"{u.split('/')[2]}: HTTP={code} len={len(data)}")
    return False, "all_basket_hosts_failed", None

# ============ WB: catalog.wb.ru with warmup ============
def wb_catalog_warmup():
    nm = 1097975275
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
        "Origin": "https://www.wildberries.ru",
    }
    # Warmup: hit main page first
    safe_get("https://www.wildberries.ru/", h, timeout=10)
    time.sleep(1)
    
    urls = [
        f"https://catalog.wb.ru/cards/v2/detail?nm={nm}",
        f"https://catalog.wb.ru/cards/detail?nm={nm}",
        f"https://card.wb.ru/cards/v2/detail?nm={nm}",
    ]
    for u in urls:
        code, data = safe_get(u, h, timeout=12)
        if code == 200 and len(data) > 50:
            try:
                j = json.loads(data)
                cards = j.get("data", {}).get("cards", [])
                if cards:
                    c = cards[0]
                    price = c.get("salePriceU", c.get("priceU", c.get("sizes", [{}])[0].get("price", {}).get("basic", {}).get("d", None)))
                    name = c.get("name", "")
                    return True, f"HTTP=200 len={len(data)} name={name!r:.50} price={price}", j
            except:
                pass
        log(f"WB:catalog", f"{u.split('/')[2]}: HTTP={code} len={len(data)}")
    return False, "catalog_all_failed", None

# ============ FARPOST: card embedded state ============
def farpost_card_state():
    # Use the 198KB card page - look for embedded React/Next state
    url = "https://www.farpost.ru/vladivostok/auto/misc/134693091"
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    code, data = safe_get(url, h, timeout=15)
    if code != 200:
        return False, f"HTTP={code}", None
    
    html = data.decode("utf-8", errors="replace")
    
    # Look for various state patterns
    patterns = {
        "__NEXT_DATA__": r'__NEXT_DATA__\s*=\s*(\{.*?\})\s*;?\s*</script>',
        "__INITIAL_STATE__": r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;',
        "__APP_STATE__": r'window\.__APP_STATE__\s*=\s*(\{.*?\})\s*;',
        "window.state": r'window\.state\s*=\s*(\{.*?\})\s*;',
        "data-page-props": r'data-page-props="([^"]*)"',
        "script_json": r'<script[^>]*id="(__[A-Z_]+__)"[^>]*>(\{.*?\})</script>',
        "nuxt": r'window\.__NUXT__\s*=\s*(\{.*?\})',
    }
    
    found = {}
    for name, pat in patterns.items():
        m = re.search(pat, html, re.S)
        if m:
            found[name] = m.group(1)[:500]
    
    # Look for price in any form
    price_patterns = [
        r'"price"\s*:\s*"?(\d+)"?',
        r'data-price="(\d+)"',
        r'class="[^"]*price[^"]*"[^>]*>[\s]*([\d\s]+)',
        r'(\d{4,10})\s*(?:\u20bd|руб|₽)',
    ]
    prices_found = []
    for pp in price_patterns:
        prices_found.extend(re.findall(pp, html))
    
    # Check for bulletin ID references
    bull_ids = re.findall(r'bull(?:etin)?[_-]?id[=:]\s*"?(\d+)"?', html)
    subj = re.findall(r'subject[^>]*>(.*?)<', html, re.S)
    
    # code-challenge (Altcha)
    challenge = re.search(r'data-code-challenge="([^"]*)"', html)
    
    detail = f"len={len(html)} states={list(found.keys())} prices={prices_found[:10]} bulls={bull_ids[:5]} subj={subj[:3]} challenge={challenge.group(1)[:30] if challenge else 'N'}"
    
    extract = {"states": found, "prices": prices_found[:20], "bull_ids": bull_ids[:10], "subjects": subj[:5], "challenge": challenge.group(1) if challenge else None}
    ok = len(prices_found) > 0 or len(found) > 0
    return ok, detail, extract

# ============ FARPOST: mobile subdomain ============
def farpost_mobile():
    urls = [
        "https://m.farpost.ru/vladivostok/auto/misc/134693091",
        "https://farpost.ru/vladivostok/auto/misc/134693091?mobile=1",
        "https://www.farpost.ru/vladivostok/auto/misc/134693091?format=json",
    ]
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/json",
    }
    for u in urls:
        code, data = safe_get(u, h, timeout=12)
        html = data.decode("utf-8", errors="replace") if code == 200 else ""
        prices = re.findall(r'(\d{3,10})\s*(?:\u20bd|руб|₽)', html)
        jld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S)
        verify = "verify" in html.lower() or "/verify" in html
        detail = f"HTTP={code} len={len(data)} prices={len(prices)} jld={len(jld)} verify={verify}"
        if code == 200 and (len(prices) > 0 or len(jld) > 0):
            return True, f"{u.split('/')[2]}: {detail}", {"prices": prices[:10], "jld": jld[:2]}
        log(f"FP:mobile", f"{u.split('/')[2]}: {detail}")
    return False, "mobile_all_failed", None

# ============ AVITO: mobile + specific offer ============
def avito_mobile():
    # Try mobile site and specific offer
    urls = [
        "https://m.avito.ru/vladivostok/smartfony.html",
        "https://www.avito.ru/vladivostok/smartfony-ASgBAgICAkSSA8YQ?cid=182",
        "https://m.avito.ru/vladivostok/nedvizhimost.html",
    ]
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    for u in urls:
        code, data = safe_get(u, h, timeout=15)
        html = data.decode("utf-8", errors="replace") if code == 200 else ""
        offers = re.findall(r'data-qa="offer-card"|class="[^"]*offer[^"]*card[^"]*"', html)
        prices = re.findall(r'(\d{4,10})\s*(?:\u20bd|руб|₽|₽)', html)
        links = re.findall(r'href="(/vladivostok/[^"]+)"', html)
        detail = f"HTTP={code} len={len(data)} offers={len(offers)} prices={len(prices)} links={len(links)}"
        if code == 200 and len(offers) > 0:
            return True, f"{u.split('/')[2]}: {detail}", {"offers": offers[:10], "prices": prices[:10]}
        log(f"AV:mobile", f"{u.split('/')[2]}: {detail}")
    return False, "avito_mobile_failed", None

# ============ AVITO: sitemap ============
def avito_sitemap():
    urls = [
        "https://www.avito.ru/sitemap.xml",
        "https://www.avito.ru/robots.txt",
    ]
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    for u in urls:
        code, data = safe_get(u, h, timeout=10)
        text = data.decode("utf-8", errors="replace")[:2000]
        detail = f"HTTP={code} len={len(data)}"
        log(f"AV:sitemap", f"{u.split('/')[-1]}: {detail} preview={text[:100]!r}")
        if code == 200:
            return True, f"{u.split('/')[-1]}: {detail}", text
    return False, "sitemap_failed", None

# ============ RUN ALL ============
if __name__ == "__main__":
    tests = [
        ("OZ:pw_mobile", ozon_mobile_pw),
        ("OZ:composer", ozon_composer_session),
        ("WB:basket_full", wb_basket_full),
        ("WB:catalog_warmup", wb_catalog_warmup),
        ("FP:card_state", farpost_card_state),
        ("FP:mobile", farpost_mobile),
        ("AV:mobile", avito_mobile),
        ("AV:sitemap", avito_sitemap),
    ]
    
    for tag, fn in tests:
        try:
            ok, detail, extra = fn()
            results[tag] = {"ok": ok, "detail": detail, "s": round(time.time() - t0, 1)}
            if extra and tag in ("OZ:pw_mobile", "WB:basket_full", "FP:card_state"):
                results[tag]["extract"] = extra
            log(tag, f"{'PASS' if ok else 'FAIL'}: {detail}")
        except Exception as e:
            results[tag] = {"ok": False, "detail": f"EXC: {e}", "s": round(time.time() - t0, 1)}
            log(tag, f"EXC: {e}")
    
    results["_meta"] = {"total_s": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{time.time()-t0:.1f}s] DONE -> {OUT.name}", flush=True)
