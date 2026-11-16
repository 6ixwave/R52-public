"""diag24: 5 быстрых — FP cat parse, FP PW card, WB card.json, WB card.wb, OZ PW."""
import json, re, time
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
    print(f"[{name}] {'PASS' if ok else 'FAIL'} ({el}s) {detail[:250]}", flush=True)
    time.sleep(0.3)

# ============================================================
# 1. FP: PARSE RENDERED CATEGORY (from d23 dump or fresh)
# ============================================================
def fp_cat_parse():
    """Parse the rendered category HTML — find title + price + id patterns."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    url = "https://www.farpost.ru/vladivostok/auto/misc/"
    r = s.get(url, timeout=15)
    if r.status_code != 200:
        return False, f"HTTP={r.status_code}"
    html = r.content.decode("windows-1251", errors="replace")
    
    # IDs
    ids = re.findall(r'href="([^"]*?)(\d{5,})\.html"', html)
    # Prices — MULTIPLE patterns
    p1 = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
    p2 = re.findall(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\xa0\u20bd]+)', html)
    p3 = re.findall(r'>\s*([\d]{2,}(?:[\d\s\xa0]*[\d])?)\s*(?:₽|руб)', html)
    p4 = re.findall(r'<span[^>]*class="[^"]*(?:price|cost)[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL)
    # Titles — MULTIPLE patterns
    t1 = re.findall(r'data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
    t2 = re.findall(r'<h[23][^>]*class="[^"]*subject[^"]*"[^>]*>(.*?)</h[23]>', html, re.DOTALL)
    t3 = re.findall(r'<a[^>]*href="[^"]*?\d{5,}\.html"[^>]*class="[^"]*(?:title|subject|name)[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)
    t4 = re.findall(r'<title>([^<]+)</title>', html)
    # Any h2/h3
    t5 = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL)
    
    # Clean
    def clean(s):
        return re.sub(r'<[^>]+>', '', s).strip()[:80]
    
    all_titles = [clean(t) for t in (t1+t2+t3+t5) if clean(t)]
    all_prices = [p.strip() for p in (p1+p2+p3+p4) if p.strip()]
    
    ok = len(ids) >= 5 and len(all_prices) >= 3
    detail = (f"ids={len(ids)} p1={len(p1)} p2={len(p2)} p3={len(p3)} p4={len(p4)} "
              f"t1={len(t1)} t2={len(t2)} t3={len(t3)} t5={len(t5)} "
              f"len={len(html)}")
    if all_prices:
        detail += f" p_sample={all_prices[:3]}"
    if all_titles:
        detail += f" t_sample={all_titles[:2]}"
    
    # Save
    with open(BASE / "d24_fp_cat.json", "w", encoding="utf-8") as f:
        json.dump({
            "n_ids": len(ids), "ids_sample": [i[1] for i in ids[:10]],
            "prices": all_prices[:20], "titles": all_titles[:10],
            "html_len": len(html)
        }, f, ensure_ascii=False, indent=2)
    return ok, detail

# ============================================================
# 2. FP: PLAYWRIGHT CARD — longer verify wait (20s)
# ============================================================
def fp_pw_card():
    """Playwright card: wait 20s for verify JS."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.goto("https://www.farpost.ru/", timeout=20000)
        page.wait_for_timeout(2000)
        page.goto("https://www.farpost.ru/vladivostok/134693091.html",
                  timeout=30000, wait_until="domcontentloaded")
        # Wait up to 20s for verify
        resolved = False
        for i in range(7):
            page.wait_for_timeout(3000)
            if "/verify" not in page.url:
                resolved = True
                break
        html = page.content()
        price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        jsonlds = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        jld_price = False
        for j in jsonlds:
            try:
                d = json.loads(j)
                if isinstance(d, dict) and d.get("@type") == "Product":
                    jld_price = True
                    break
            except: pass
        title_m = re.search(r'<title>([^<]+)</title>', html)
        
        ok = resolved and (bool(price_m) or jld_price)
        detail = (f"resolved={resolved} url={page.url[:60]} price={price_m.group(1) if price_m else 'N/A'} "
                  f"jsonld={jld_price} title={title_m.group(1)[:50] if title_m else ''} len={len(html)}")
        browser.close()
        return ok, detail

# ============================================================
# 3. WB: card.json (exact diag20 URL)
# ============================================================
def wb_card():
    """basket-42 card.json — the exact URL that worked in diag20."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    vol = nm_id // 10000   # 10979
    part = nm_id // 1000   # 1097975
    url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    time.sleep(2)
    r = s.get(url, timeout=12)
    if r.status_code != 200:
        # Try other hosts
        for h in [43, 44, 100, 200]:
            u = f"https://basket-{h}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
            try:
                r2 = s.get(u, timeout=8)
                if r2.status_code == 200:
                    r = r2; url = u; break
            except: continue
    if r.status_code != 200:
        return False, f"HTTP={r.status_code} url={url[:60]}"
    data = r.json()
    keys = list(data.keys())
    txt = json.dumps(data)
    pm = re.search(r'"(?:price|salePriceW|salePriceM|priceU|priceD|oldPrice)"\s*:\s*"?(\d+)', txt)
    with open(BASE / "d24_wb_card.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    ok = len(keys) >= 5
    detail = f"host={url.split('/')[2]} keys={len(keys)} price={pm.group(1) if pm else 'N/A'} k={keys[:6]}"
    return ok, detail

# ============================================================
# 4. WB: card.wb.ru v2
# ============================================================
def wb_card_wb():
    """card.wb.ru/cards/v2/detail — main price API."""
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    nm_id = 1097975275
    url = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
    r = s.get(url, timeout=12, headers={
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    })
    if r.status_code != 200:
        return False, f"HTTP={r.status_code} len={len(r.content)}"
    data = r.json()
    cards = data.get("data", {}).get("cards", [])
    if cards:
        c = cards[0]
        price = c.get("price", {})
        name = c.get("name", "")[:50]
        return True, f"OK price={price} name={name}"
    return True, f"OK len={len(r.content)} sample={r.text[:200]}"

# ============================================================
# 5. OZON: Playwright — shorter, 2 products
# ============================================================
def oz_pw():
    """Ozon PW: 2 products, 15s each max."""
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
        
        # Warm-up
        page.goto("https://www.ozon.ru/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        
        results_oz = []
        for sku in [3481320800, 2067947177]:
            url = f"https://www.ozon.ru/product/{sku}/"
            try:
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                html = page.content()
                title_m = re.search(r'<title>([^<]+)</title>', html)
                jsonlds = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
                price_val = None
                for j in jsonlds:
                    try:
                        d = json.loads(j)
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            off = d.get("offers", {})
                            if isinstance(off, dict): price_val = off.get("price")
                            elif isinstance(off, list) and off: price_val = off[0].get("price")
                    except: pass
                results_oz.append({
                    "sku": sku,
                    "title": title_m.group(1)[:50] if title_m else "",
                    "jsonlds": len(jsonlds),
                    "price": price_val,
                    "len": len(html),
                })
            except Exception as e:
                results_oz.append({"sku": sku, "err": str(e)[:80]})
        
        browser.close()
        ok = any(r.get("price") for r in results_oz)
        detail = f" {json.dumps(results_oz, ensure_ascii=False)[:200]}"
        return ok, detail

# ============================================================
print(f"=== diag24: {time.strftime('%H:%M:%S')} ===", flush=True)
run("FP:cat_parse", fp_cat_parse)
run("FP:pw_card", fp_pw_card)
run("WB:card_json", wb_card)
run("WB:card_wb", wb_card_wb)
run("OZ:pw", oz_pw)

el = round(time.time() - t0, 1)
results["_meta"] = {"total_s": el, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
out = BASE / "d24_results.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nDONE {el}s -> {out}", flush=True)
