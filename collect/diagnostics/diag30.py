#!/usr/bin/env python3
"""diag30: deep extraction from confirmed/near-confirmed endpoints.
FP: m.farpost.ru JSON-LD + embedded state + multiple IDs
OZ: mobile PW retry with longer wait + desktop PW fallback
WB: basket sweep (01-60) + static-basket
AV: specific offer page + API from robots hints
"""
import json, re, time, sys, os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path(__file__).parent / "d30_results.json"
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

# ============ FARPOST MOBILE: deep JSON-LD + state ============
def fp_mobile_deep():
    """m.farpost.ru gave 48KB, 2 JSON-LD, no verify. Extract everything."""
    urls = [
        "https://m.farpost.ru/vladivostok/auto/misc/134693091",
        "https://m.farpost.ru/vladivostok/auto/misc/121063385",
        "https://m.farpost.ru/vladivostok/dom/47461666",
    ]
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
    }
    all_data = []
    for u in urls:
        code, data = safe_get(u, h, timeout=15)
        if code != 200:
            log(f"FP:mob", f"{u.split('/')[-1]}: HTTP={code}")
            continue
        html = data.decode("utf-8", errors="replace")
        
        # JSON-LD
        jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        jld_parsed = []
        for j in jlds:
            try:
                jld_parsed.append(json.loads(j.strip()))
            except:
                jld_parsed.append({"raw": j[:200]})
        
        # Embedded state
        states = {}
        for pat_name, pat in [
            ("__NEXT_DATA__", r'__NEXT_DATA__\s*=\s*(\{.*?\})\s*;?\s*</script>'),
            ("__INITIAL_STATE__", r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;'),
            ("window.__state", r'window\.__[a-zA-Z_]*state[a-zA-Z_]*__\s*=\s*(\{.*?\})\s*;'),
            ("data-props", r'data-(?:page-)?props="([^"]{10,})"'),
            ("script_id_json", r'<script[^>]*id="([a-z_]+)"[^>]*>(\{.*?\})\s*</script>'),
        ]:
            m = re.search(pat, html, re.S)
            if m:
                states[pat_name] = m.group(1)[:300]
        
        # Prices
        prices = re.findall(r'(\d{3,12})\s*(?:\u20bd|руб|₽|RUB)', html)
        prices2 = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', html)
        
        # Title
        title_m = re.search(r'<title>(.*?)</title>', html, re.S)
        og_title = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        
        # Links to other bulletins
        bull_links = re.findall(r'href="[^"]*(?:/(\d{6,12}))"', html)
        
        entry = {
            "url": u,
            "http": code,
            "len": len(html),
            "jsonld": jld_parsed,
            "states": states,
            "prices": prices[:15],
            "prices_json": prices2[:15],
            "title": title_m.group(1).strip() if title_m else None,
            "og_title": og_title.group(1) if og_title else None,
            "h1": h1.group(1).strip()[:100] if h1 else None,
            "bull_links": bull_links[:10],
            "has_verify": "verify" in html.lower(),
        }
        all_data.append(entry)
        
        ok = len(jld_parsed) > 0 or len(prices) > 0 or len(prices2) > 0
        log(f"FP:mob", f"{u.split('/')[-1]}: len={len(html)} jld={len(jld_parsed)} p={len(prices)} p2={len(prices2)} states={list(states.keys())}")
    
    # Also try category page on mobile
    cat_urls = [
        "https://m.farpost.ru/vladivostok/auto/misc",
        "https://m.farpost.ru/vladivostok/dom",
    ]
    for cu in cat_urls:
        code, data = safe_get(cu, h, timeout=15)
        html = data.decode("utf-8", errors="replace") if code == 200 else ""
        prices = re.findall(r'(\d{3,12})\s*(?:\u20bd|руб|₽)', html)
        bull_ids = re.findall(r'href="[^"]*?/(\d{6,12})[^"]*"', html)
        jld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S)
        verify = "verify" in html.lower()
        detail = f"HTTP={code} len={len(html)} prices={len(prices)} bulls={len(bull_ids)} jld={len(jld)} verify={verify}"
        log(f"FP:mob_cat", f"{cu.split('/')[-2]}/{cu.split('/')[-1]}: {detail}")
        all_data.append({"url": cu, "http": code, "len": len(html), "prices": prices[:10], "bull_ids": bull_ids[:15], "jld_count": len(jld), "verify": verify, "type": "category"})
    
    ok_any = any(d.get("jsonld") or d.get("prices") or d.get("prices_json") for d in all_data)
    return ok_any, f"items={len(all_data)}", all_data

# ============ OZON: mobile PW with longer wait ============
def ozon_mobile_retry():
    from playwright.sync_api import sync_playwright
    url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    
    # Strategy 1: mobile viewport, longer wait
    configs = [
        {
            "name": "mobile_long",
            "viewport": {"width": 390, "height": 844},
            "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "wait": 12000,
        },
        {
            "name": "desktop_long",
            "viewport": {"width": 1920, "height": 1080},
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "wait": 12000,
        },
    ]
    
    best = None
    for cfg in configs:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
                ctx = browser.new_context(
                    viewport=cfg["viewport"],
                    user_agent=cfg["ua"],
                    locale="ru-RU",
                    extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(cfg["wait"])
                # Try to trigger lazy load
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                html = page.content()
                browser.close()
            
            jlds = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
            jld_parsed = []
            for j in jlds:
                try:
                    jld_parsed.append(json.loads(j.strip()))
                except:
                    pass
            
            prices = re.findall(r'(\d{2,7})\s*(?:\u20bd|руб|₽)', html)
            prices_json = re.findall(r'"price"\s*:\s*"?([\d.]+)"?', html)
            name_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
            name = name_m.group(1).strip()[:80] if name_m else None
            
            detail = f"len={len(html)} jld={len(jld_parsed)} prices={len(prices)} pj={len(prices_json)} name={name!r}"
            log(f"OZ:{cfg['name']}", detail)
            
            entry = {
                "config": cfg["name"],
                "len": len(html),
                "jsonld": jld_parsed,
                "prices": prices[:20],
                "prices_json": prices_json[:20],
                "name": name,
            }
            
            if best is None or len(html) > best.get("len", 0):
                best = entry
            
            if len(jld_parsed) > 0 and (len(prices) > 0 or len(prices_json) > 0):
                return True, f"{cfg['name']}: {detail}", entry
        except Exception as e:
            log(f"OZ:{cfg['name']}", f"EXC: {e}")
    
    return best is not None and best.get("len", 0) > 50000, f"best={best.get('len',0) if best else 0}", best

# ============ WB: basket sweep 01-60 ============
def wb_basket_sweep():
    nm = 1097975275
    vol = 10979
    part = 1097975
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    
    found = []
    for i in range(1, 61):
        for prefix in ["basket", "static-basket"]:
            host = f"{prefix}-{i:02d}.wbbasket.ru"
            u = f"https://{host}/vol{vol}/part{part}/{nm}.json"
            code, data = safe_get(u, h, timeout=8)
            if code == 200 and len(data) > 100:
                try:
                    j = json.loads(data)
                    name = j.get("name", "")
                    price = j.get("salePriceU", j.get("priceU", j.get("price", None)))
                    found.append({"host": host, "name": name[:60], "price": price, "len": len(data)})
                    log(f"WB:sweep", f"HIT {host}: {name[:40]} price={price}")
                    return True, f"host={host} name={name!r:.50} price={price}", j
                except:
                    found.append({"host": host, "len": len(data), "non_json": True})
            elif code not in (404, -1):
                log(f"WB:sweep", f"{host}: HTTP={code} len={len(data)}")
    
    # Also try wbx-content
    for ver in ["v1", "v2", "v3"]:
        u = f"https://wbx-content-{ver}.wbstatic.net/card/{nm}.json"
        code, data = safe_get(u, h, timeout=10)
        if code == 200 and len(data) > 100:
            try:
                j = json.loads(data)
                return True, f"wbx-{ver}: len={len(data)}", j
            except:
                pass
    
    return len(found) > 0, f"tried=120 found={len(found)}", found[:10]

# ============ AVITO: specific offer + API hints ============
def avito_deep():
    """Try specific known offer patterns and API endpoints."""
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    
    # 1. Try a known offer URL pattern (from search results)
    offer_urls = [
        "https://www.avito.ru/vladivostok/smartfony-ASgBAgICAkSSA8YQ",
        "https://m.avito.ru/vladivostok/smartfony",
    ]
    
    results_data = []
    for u in offer_urls:
        code, data = safe_get(u, h, timeout=15)
        html = data.decode("utf-8", errors="replace") if code == 200 else ""
        offers = re.findall(r'data-qa="offer-card"|class="[^"]*offer[^"]*card[^"]*"', html)
        prices = re.findall(r'(\d{4,12})\s*(?:\u20bd|руб|₽)', html)
        # Look for JSON in scripts
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
        json_scripts = [s[:200] for s in scripts if '"offers"' in s or '"price"' in s or '"title"' in s]
        detail = f"HTTP={code} len={len(html)} offers={len(offers)} prices={len(prices)} json_scripts={len(json_scripts)}"
        log(f"AV:offer", f"{u.split('/')[2]}: {detail}")
        results_data.append({"url": u, "http": code, "len": len(html), "offers": len(offers), "prices": prices[:10], "json_scripts": json_scripts[:3]})
    
    # 2. Try Avito API (public endpoints)
    api_urls = [
        "https://www.avito.ru/api/geo/v1/geo/regions",
        "https://www.avito.ru/api/offers/v1/offers?region=2&category=182&limit=5",
        "https://api.avito.ru/offers/v1/offers?region=2&category=182",
    ]
    for u in api_urls:
        code, data = safe_get(u, {**h, "Accept": "application/json"}, timeout=10)
        text = data.decode("utf-8", errors="replace")[:500]
        log(f"AV:api", f"{u.split('/')[2]}:{u.split('/')[3] if len(u.split('/'))>3 else ''}: HTTP={code} len={len(data)}")
        results_data.append({"url": u, "http": code, "len": len(data), "preview": text[:200]})
    
    ok = any(r.get("offers", 0) > 0 or r.get("http") == 200 and r.get("len", 0) > 1000 for r in results_data)
    return ok, f"items={len(results_data)}", results_data

# ============ RUN ============
if __name__ == "__main__":
    tests = [
        ("FP:mobile_deep", fp_mobile_deep),
        ("OZ:mobile_retry", ozon_mobile_retry),
        ("WB:basket_sweep", wb_basket_sweep),
        ("AV:deep", avito_deep),
    ]
    
    for tag, fn in tests:
        try:
            ok, detail, extra = fn()
            results[tag] = {"ok": ok, "detail": detail, "s": round(time.time() - t0, 1)}
            if extra:
                results[tag]["data"] = extra if isinstance(extra, (list, dict)) else {"val": extra}
            log(tag, f"{'PASS' if ok else 'FAIL'}: {detail}")
        except Exception as e:
            import traceback
            results[tag] = {"ok": False, "detail": f"EXC: {e}", "s": round(time.time() - t0, 1)}
            log(tag, f"EXC: {e}\n{traceback.format_exc()}")
    
    results["_meta"] = {"total_s": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{time.time()-t0:.1f}s] DONE -> {OUT.name}", flush=True)
