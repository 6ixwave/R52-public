"""diag_multi12.py — 12 коротких стратегий: WB price, Ozon JSON-LD/Composer, Farpost cookies, Avito."""
import json, re, sys, time, ssl, socket
from datetime import datetime

results = {}
t0 = time.time()

def log(tag, msg):
    line = f"[{tag}] {msg}"
    print(line, flush=True)

def safe_json(data):
    """Make JSON-serializable."""
    if isinstance(data, dict):
        return {k: safe_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [safe_json(x) for x in data]
    if isinstance(data, (int, float, str, bool)) or data is None:
        return data
    return str(data)

# ============================================================
# WB: nm_id=1097975275, basket-42, vol10, part1097
# ============================================================
WB_NM = 1097975275
WB_VOL = WB_NM // 100000  # 10
WB_PART = WB_NM // 1000   # 1097
WB_BASKET = "basket-42.wbbasket.ru"

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
JSON_H = {"Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9", "Referer": "https://www.wildberries.ru/", "Origin": "https://www.wildberries.ru"}
HTML_H = {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ru-RU,ru;q=0.9", "Referer": "https://www.wildberries.ru/"}

def wb_get(url, headers=None, timeout=12):
    if HAS_CFFI:
        kw = {"impersonate": "chrome", "timeout": timeout}
        if headers: kw["headers"] = headers
        return cffi_req.get(url, **kw)
    h = {"User-Agent": UA}
    if headers: h.update(headers)
    return cffi_req.get(url, headers=h, timeout=timeout)

# --- WB:1 size.json (per-size prices from basket CDN) ---
try:
    t = time.time()
    url = f"https://{WB_BASKET}/vol{WB_VOL}/part{WB_PART}/{WB_NM}/info/ru/size.json"
    r = wb_get(url, JSON_H)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            sizes = data.get("sizes", [])
            if sizes:
                s0 = sizes[0]
                pr = s0.get("price", {})
                if pr:
                    detail += f" sizes[0].price={json.dumps(pr)}"
                    ok = True
            if not ok:
                detail += f" keys={list(data.keys())[:10]}"
        results["WB:1_size_json"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
    else:
        results["WB:1_size_json"] = {"ok": False, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:1_size_json"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:1", results.get("WB:1_size_json", {}).get("detail","?"))

# --- WB:2 HTML detail.aspx (embedded state / prices) ---
try:
    t = time.time()
    url = f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx"
    r = wb_get(url, HTML_H, timeout=20)
    html = r.text if r.status_code == 200 else ""
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if html:
        # Check for price patterns
        price_re = re.compile(r'"(?:price|priceU|priceD|basic|product)"\s*:\s*(\d{4,})')
        prices_found = price_re.findall(html)
        if prices_found:
            detail += f" prices={prices_found[:8]}"
            ok = True
        # __NEXT_DATA__ or state
        if "__NEXT_DATA__" in html:
            detail += " NEXT_DATA=yes"
        if "window.__STATE__" in html or "window.__INITIAL_STATE__" in html:
            detail += " STATE=yes"
        # og:price
        og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if og:
            detail += f" og_price={og.group(1)}"
            ok = True
        # data-price
        dp = re.findall(r'data-price=["\'](\d+)["\']', html)
        if dp:
            detail += f" data_price={dp[:5]}"
            ok = True
        # Visible price text
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis:
            detail += f" vis_price={vis[:5]}"
            ok = True
        # Save snippet for analysis
        with open("d12_wb_html_head.html", "w", encoding="utf-8") as f:
            f.write(html[:20000])
    results["WB:2_html"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:2_html"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:2", results.get("WB:2_html", {}).get("detail","?"))

# --- WB:3 web API v1 detail ---
try:
    t = time.time()
    url = f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={WB_NM}&curr=rub&dest=-1257786&spp=30"
    r = wb_get(url, JSON_H)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            d = data.get("data", data)
            pr = d.get("prices") or d.get("price") or d.get("priceU")
            if pr:
                detail += f" prices={json.dumps(pr)[:200]}"
                ok = True
            else:
                detail += f" keys={list(d.keys())[:12]}"
    results["WB:3_web_api"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:3_web_api"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:3", results.get("WB:3_web_api", {}).get("detail","?"))

# --- WB:4 card.wb.ru v5 with full browser headers ---
try:
    t = time.time()
    url = f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"
    h = {**JSON_H, "User-Agent": UA, "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"', "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"', "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site"}
    r = wb_get(url, h)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        prods = data.get("data", {}).get("items", []) or data.get("products", [])
        if prods:
            p = prods[0]
            sizes = p.get("sizes", [])
            if sizes:
                pr = sizes[0].get("price", {})
                detail += f" price={json.dumps(pr)[:200]}"
                ok = True
            else:
                detail += f" keys={list(p.keys())[:10]}"
    results["WB:4_card_v5"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:4_card_v5"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:4", results.get("WB:4_card_v5", {}).get("detail","?"))

# --- WB:5 basket CDN card.json re-check (look for hidden price fields) ---
try:
    t = time.time()
    url = f"https://{WB_BASKET}/vol{WB_VOL}/part{WB_PART}/{WB_NM}/info/ru/card.json"
    r = wb_get(url, JSON_H)
    detail = f"HTTP={r.status_code}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        # Check ALL numeric fields > 1000 at top level and in selling
        top_nums = {k:v for k,v in data.items() if isinstance(v,(int,float)) and v>1000}
        selling = data.get("selling", {})
        sell_nums = {k:v for k,v in selling.items() if isinstance(v,(int,float)) and v>1000} if isinstance(selling, dict) else {}
        detail += f" top_nums={top_nums} sell_nums={sell_nums}"
        # Check if there's a "price" or "prices" key anywhere
        price_keys = [k for k in data.keys() if "price" in k.lower()]
        detail += f" price_keys={price_keys}"
        if price_keys or top_nums:
            ok = True
    results["WB:5_card_recheck"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:5_card_recheck"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:5", results.get("WB:5_card_recheck", {}).get("detail","?"))

# ============================================================
# OZON: sku=3481320800
# ============================================================
OZ_SKU = "3481320800"
OZ_URL = f"https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-{OZ_SKU}/"

# --- OZ:1 CFFI product page (check JSON-LD in 200 or 403) ---
try:
    t = time.time()
    r = wb_get(OZ_URL, HTML_H, timeout=18)
    html = r.text
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    # JSON-LD
    ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if ld_blocks:
        for lb in ld_blocks:
            try:
                ld = json.loads(lb.strip())
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict):
                        offers = [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price"):
                            detail += f" LD_price={o['price']} {o.get('priceCurrency','?')}"
                            ok = True
            except: pass
    # og:price
    og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    if og:
        detail += f" og={og.group(1)}"
        ok = True
    # data-price
    dp = re.findall(r'data-price=["\'](\d+)["\']', html)
    if dp:
        detail += f" dp={dp[:3]}"
        ok = True
    # Visible price
    vis = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html)
    if vis:
        detail += f" vis={vis[:5]}"
        ok = True
    results["OZ:1_cffi_page"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:1_cffi_page"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:1", results.get("OZ:1_cffi_page", {}).get("detail","?"))

# --- OZ:2 Composer via www.ozon.ru (not api.ozon.ru) ---
try:
    t = time.time()
    url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{OZ_SKU}/"
    h = {**JSON_H, "User-Agent": UA}
    r = wb_get(url, h, timeout=15)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        # Look for price in blocks
        blocks = data.get("page", {}).get("blocks", [])
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict) and b.get("type") in ("webPrice","price","pdp-price","priceBlock"):
                    props = b.get("props", {}) or b.get("data", {})
                    pr = props.get("price") or props
                    if isinstance(pr, dict):
                        for pk in ("price","current","basic","value","min"):
                            pv = pr.get(pk)
                            if isinstance(pv,(int,float)) and pv>0:
                                detail += f" block_price={pk}={pv}"
                                ok = True
        if not ok:
            detail += f" top_keys={list(data.keys())[:8]}"
    results["OZ:2_composer_www"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:2_composer_www"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:2", results.get("OZ:2_composer_www", {}).get("detail","?"))

# --- OZ:3 PDP entry via www ---
try:
    t = time.time()
    url = f"https://www.ozon.ru/api/entrypoint/pdp/{OZ_SKU}"
    h = {**JSON_H, "User-Agent": UA}
    r = wb_get(url, h, timeout=15)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        detail += f" keys={list(data.keys())[:10]}"
        # Deep search for price
        txt = json.dumps(data)[:5000]
        pm = re.findall(r'"(?:price|current|basic)"\s*:\s*(\d{3,})', txt)
        if pm:
            detail += f" prices={pm[:5]}"
            ok = True
    results["OZ:3_pdp_entry"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:3_pdp_entry"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:3", results.get("OZ:3_pdp_entry", {}).get("detail","?"))

# --- OZ:4 Playwright (if available) ---
try:
    t = time.time()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        resp = page.goto(OZ_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        html = page.content()
        status = resp.status if resp else 0
        detail = f"HTTP={status} len={len(html)}"
        ok = False
        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        for lb in ld_blocks:
            try:
                ld = json.loads(lb.strip())
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict): offers = [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price"):
                            detail += f" LD={o['price']} {o.get('priceCurrency','?')}"
                            ok = True
            except: pass
        # data-price, visible
        dp = re.findall(r'data-price=["\'](\d+)["\']', html)
        if dp: detail += f" dp={dp[:3]}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        browser.close()
    results["OZ:4_playwright"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:4_playwright"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:4", results.get("OZ:4_playwright", {}).get("detail","?"))

# ============================================================
# FARPOST: 138859452
# ============================================================
FP_ID = "138859452"

# --- FP:1 Warm-up + cookies → announcement ---
try:
    t = time.time()
    # Step 1: warm-up to get cookies
    jar = cffi_req.Session() if not HAS_CFFI else None
    if HAS_CFFI:
        jar = cffi_req.Session(impersonate="chrome")
    # Warm-up
    r1 = jar.get("https://www.farpost.ru/", timeout=10)
    cookies = dict(jar.cookies)
    detail = f"warmup HTTP={r1.status_code} cookies={list(cookies.keys())}"
    # Step 2: announcement with cookies
    time.sleep(1)
    r2 = jar.get(f"https://www.farpost.ru/bulletin/{FP_ID}", timeout=15, allow_redirects=True)
    html = r2.text
    detail += f" | ann HTTP={r2.status_code} len={len(r2.content)} url={r2.url[:80]}"
    ok = False
    # Check for verify
    if "verify" in r2.url:
        detail += " VERIFY_REDIRECT"
    # Prices
    ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    for lb in ld_blocks:
        try:
            ld = json.loads(lb.strip())
            if isinstance(ld, dict) and ld.get("@type") in ("Product","Offer"):
                pr = ld.get("offers",{}).get("price") or ld.get("price")
                if pr:
                    detail += f" LD_price={pr}"
                    ok = True
        except: pass
    dp = re.findall(r'data-price=["\']?(\d+)["\']?', html)
    if dp: detail += f" dp={dp[:3]}"; ok = True
    og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    if og: detail += f" og={og.group(1)}"; ok = True
    vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
    if vis: detail += f" vis={vis[:5]}"; ok = True
    # Check for captcha
    if "captcha" in html.lower() or "recaptcha" in html.lower():
        detail += " CAPTCHA"
    results["FP:1_warmup_cookies"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:1_warmup_cookies"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:1", results.get("FP:1_warmup_cookies", {}).get("detail","?"))

# --- FP:2 Direct with fresh session, no redirect follow ---
try:
    t = time.time()
    if HAS_CFFI:
        r = cffi_req.get(f"https://www.farpost.ru/bulletin/{FP_ID}", impersonate="chrome", timeout=15, allow_redirects=False)
    else:
        r = cffi_req.get(f"https://www.farpost.ru/bulletin/{FP_ID}", timeout=15, allow_redirects=False, headers={"User-Agent": UA})
    loc = r.headers.get("Location", "")
    detail = f"HTTP={r.status_code} loc={loc[:100]} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        html = r.text
        dp = re.findall(r'data-price=["\']?(\d+)["\']?', html)
        if dp: detail += f" dp={dp[:3]}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
    results["FP:2_direct_noredir"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:2_direct_noredir"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:2", results.get("FP:2_direct_noredir", {}).get("detail","?"))

# --- FP:3 Try /vladivostok/ path (city-specific) ---
try:
    t = time.time()
    if HAS_CFFI:
        r = cffi_req.get(f"https://www.farpost.ru/vladivostok/{FP_ID}.h", impersonate="chrome", timeout=15)
    else:
        r = cffi_req.get(f"https://www.farpost.ru/vladivostok/{FP_ID}.h", timeout=15, headers={"User-Agent": UA})
    html = r.text
    detail = f"HTTP={r.status_code} len={len(r.content)} url={r.url[:80]}"
    ok = False
    if r.status_code == 200:
        dp = re.findall(r'data-price=["\']?(\d+)["\']?', html)
        if dp: detail += f" dp={dp[:3]}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        if "verify" in r.url: detail += " VERIFY"
        if "captcha" in html.lower(): detail += " CAPTCHA"
    results["FP:3_city_path"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:3_city_path"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:3", results.get("FP:3_city_path", {}).get("detail","?"))

# ============================================================
# AVITO: try a known listing
# ============================================================
AV_URL = "https://www.avito.ru/vladivostok/smartfony-Ad0VKgIAOAMQwAAAKoC_AgAeAM0.html"

# --- AV:1 CFFI direct ---
try:
    t = time.time()
    r = wb_get(AV_URL, HTML_H, timeout=15)
    html = r.text
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if html:
        dp = re.findall(r'data-price=["\']?(\d+)["\']?', html)
        if dp: detail += f" dp={dp[:3]}"; ok = True
        og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if og: detail += f" og={og.group(1)}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        if "captcha" in html.lower() or "hcaptcha" in html.lower():
            detail += " CAPTCHA"
    results["AV:1_cffi"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["AV:1_cffi"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("AV:1", results.get("AV:1_cffi", {}).get("detail","?"))

# --- AV:2 Mobile UA ---
try:
    t = time.time()
    if HAS_CFFI:
        r = cffi_req.get(AV_URL, impersonate="safari", timeout=15,
                        headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    else:
        r = cffi_req.get(AV_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "Accept": "text/html"})
    html = r.text
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if html:
        dp = re.findall(r'data-price=["\']?(\d+)["\']?', html)
        if dp: detail += f" dp={dp[:3]}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        if "captcha" in html.lower(): detail += " CAPTCHA"
    results["AV:2_mobile"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["AV:2_mobile"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("AV:2", results.get("AV:2_mobile", {}).get("detail","?"))

# ============================================================
# SAVE
# ============================================================
results["total_s"] = round(time.time() - t0, 1)
results["time"] = datetime.now().isoformat()

with open("d12_results.json", "w", encoding="utf-8") as f:
    json.dump(safe_json(results), f, ensure_ascii=False, indent=2)

print(f"\n=== TOTAL: {results['total_s']}s ===", flush=True)
print(json.dumps(safe_json(results), ensure_ascii=False, indent=2), flush=True)
