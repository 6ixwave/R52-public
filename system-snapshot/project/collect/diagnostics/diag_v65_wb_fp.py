"""v65: WB basket (правильная формула) + Farpost price + Ozon Playwright.

Запуск: C:\Python315\python.exe diag_v65_wb_fp.py
"""
import json
import re
import time
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "diag_v65_out.json"

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    import requests as cffi_requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
JSON_H = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

results = {}

# ============================================================
# 1. WB: basket CDN с ПРАВИЛЬНОЙ формулой из wb.py
#    vol = nm // 100000, part = nm // 1000
# ============================================================
print("=" * 60)
print("1. WB: basket CDN (collector formula)")
print("=" * 60)

wb_nm = 199298871
vol = wb_nm // 100000   # 1992
part = wb_nm // 1000    # 199298
path = f"/vol{vol}/part{part}/{wb_nm}/info/ru/card.json"
print(f"  nm={wb_nm} vol={vol} part={part}")
print(f"  path={path}")

wb_basket = []
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}{path}"
    try:
        r = cffi_requests.get(url, headers=JSON_H, timeout=6, impersonate="chrome")
        ok = r.status_code == 200 and len(r.text) > 100
        if ok:
            print(f"  FOUND: {host} → {r.status_code} len={len(r.text)}")
            wb_basket.append({"host": host, "status": 200, "len": len(r.text)})
            (BASE / "diag_v65_wb_card.json").write_text(r.text[:20000], encoding="utf-8")
            # Parse card.json
            try:
                card = json.loads(r.text)
                print(f"    keys: {list(card.keys())[:15]}")
                print(f"    name: {card.get('name','?')[:60]}")
                print(f"    brand: {card.get('brand','?')}")
                print(f"    img: {str(card.get('img',''))[:80]}")
                # Price might be in card.json too
                for k in ["price", "priceU", "salePriceU", "discount", "sizes"]:
                    if k in card:
                        print(f"    {k}: {str(card[k])[:100]}")
            except json.JSONDecodeError:
                print(f"    JSON parse failed")
            break
        elif r.status_code != 404:
            print(f"  {host}: {r.status_code} len={len(r.text)}")
            wb_basket.append({"host": host, "status": r.status_code, "len": len(r.text)})
    except Exception as e:
        if "resolve" not in str(e).lower():
            print(f"  {host}: ERR {str(e)[:60]}")
    time.sleep(0.15)
else:
    print("  NOT FOUND in 60 hosts")

results["wb_basket"] = {"path": path, "found": len(wb_basket) > 0, "hosts": wb_basket[:5]}

# WB: Price API (separate from card.json)
print("\n  --- WB Price API ---")
# From wb.py _fetch_price: try multiple endpoints
price_endpoints = [
    f"https://card.wb.ru/cards/v2/detail?nm={wb_nm}",
    f"https://static-basket-01.wbbasket.ru/vol{vol}/part{part}/{wb_nm}/info/ru/card.json",
]
wb_price = []
for ep in price_endpoints:
    try:
        r = cffi_requests.get(ep, headers=JSON_H, timeout=8, impersonate="chrome")
        print(f"  {ep[:60]}: {r.status_code} len={len(r.text)}")
        wb_price.append({"url": ep, "status": r.status_code, "len": len(r.text)})
        if r.status_code == 200 and len(r.text) > 50:
            (BASE / "diag_v65_wb_price.json").write_text(r.text[:10000], encoding="utf-8")
    except Exception as e:
        print(f"  {ep[:60]}: ERR {str(e)[:60]}")
    time.sleep(0.5)

# WB: Also try the "new" basket with vol0 pattern
print("  --- WB vol0 pattern ---")
vol0_path = f"/vol0/part{part}/{wb_nm}/info/ru/card.json"
for n in [1, 5, 10, 21, 30, 40, 50]:
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}{vol0_path}"
    try:
        r = cffi_requests.get(url, headers=JSON_H, timeout=5, impersonate="chrome")
        if r.status_code == 200:
            print(f"  vol0 FOUND: {host} len={len(r.text)}")
            break
    except:
        pass
    time.sleep(0.1)

results["wb_price"] = wb_price

time.sleep(2)

# ============================================================
# 2. FARPOST: detailed price extraction from saved HTML
# ============================================================
print("\n" + "=" * 60)
print("2. FARPOST: price extraction from HTML")
print("=" * 60)

fp_html = (BASE / "diag_v64_fp_1.html").read_text(encoding="utf-8")
print(f"  HTML len: {len(fp_html)}")

# Multiple price patterns
fp_price_patterns = {
    "data-bulletin-price": r'data-bulletin-price=["\']?(\d+)',
    "price-block": r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d\s]{2,10})',
    "jsonld-offer": r'"price"\s*:\s*"?(\d+\.?\d*)"?',
    "jsonld-lowPrice": r'"lowPrice"\s*:\s*"?(\d+\.?\d*)"?',
    "meta-price": r'property="product:price:amount"\s+content="(\d+\.?\d*)"',
    "og-price": r'property="og:price:amount"\s+content="(\d+\.?\d*)"',
    "span-price": r'<span[^>]*class="[^"]*(?:price|cost)[^"]*"[^>]*>([^<]+)</span>',
    "b-price": r'<b[^>]*>(\d[\d\s]{2,10})\s*(?:₽|руб)',
    "div-price": r'data-price=["\']?(\d+)',
    "any-rub": r'(\d[\d\s]{2,10})\s*(?:₽|руб\.)',
    "any-rub2": r'(\d{3,12})\s*₽',
}

fp_prices = {}
for name, pat in fp_price_patterns.items():
    matches = re.findall(pat, fp_html, re.IGNORECASE)
    fp_prices[name] = matches[:5]
    if matches:
        print(f"  {name}: {matches[:3]}")

# JSON-LD block
ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', fp_html, re.DOTALL)
print(f"\n  JSON-LD blocks: {len(ld_blocks)}")
for i, block in enumerate(ld_blocks):
    try:
        ld = json.loads(block)
        if isinstance(ld, dict):
            t = ld.get("@type", "?")
            print(f"  [{i}] type={t}")
            if t == "Product" or "offers" in ld:
                print(f"      name: {ld.get('name','?')[:60]}")
                offers = ld.get("offers", {})
                if isinstance(offers, dict):
                    offers = [offers]
                for o in (offers if isinstance(offers, list) else []):
                    print(f"      offer: price={o.get('price')} cur={o.get('priceCurrency')} avail={o.get('availability','?')[-30:]}")
        elif isinstance(ld, list):
            for item in ld:
                t = item.get("@type", "?")
                print(f"  [{i}] list type={t}")
                if t == "Product":
                    print(f"      name: {item.get('name','?')[:60]}")
                    offers = item.get("offers", {})
                    if isinstance(offers, dict):
                        offers = [offers]
                    for o in (offers if isinstance(offers, list) else []):
                        print(f"      offer: price={o.get('price')} cur={o.get('priceCurrency')}")
    except json.JSONDecodeError:
        print(f"  [{i}] parse error: {block[:100]}")

results["farpost_price"] = fp_prices
results["farpost_jsonld"] = len(ld_blocks)

time.sleep(2)

# ============================================================
# 3. OZON: Playwright (same approach as Avito)
# ============================================================
print("\n" + "=" * 60)
print("3. OZON: Playwright")
print("=" * 60)

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()

        # Step 1: Main page
        print("  → ozon.ru main...")
        resp1 = page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        print(f"     status={resp1.status if resp1 else '?'} len={len(page.content())}")

        # Step 2: Product page (try a fresh ID from search)
        # Use 4605111975 (iPhone 17 Pro Max from web_search)
        ozon_id = "4605111975"
        prod_url = f"https://www.ozon.ru/product/{ozon_id}/"
        print(f"  → product: {prod_url}")
        resp2 = page.goto(prod_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        prod_content = page.content()
        print(f"     status={resp2.status if resp2 else '?'} len={len(prod_content)}")
        (BASE / "diag_v65_ozon_pw.html").write_text(prod_content[:100000], encoding="utf-8")

        # Parse
        has_jsonld = 'application/ld+json' in prod_content
        has_widget = 'data-widget' in prod_content
        has_state = 'window.__STATE' in prod_content or '__INITIAL_STATE' in prod_content
        has_next = '__NEXT_DATA__' in prod_content
        price_m = re.search(r'(\d[\d\s]{2,10})\s*₽', prod_content)
        title_m = re.search(r'<title>(.*?)</title>', prod_content, re.DOTALL)
        h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', prod_content, re.DOTALL)

        print(f"     jsonld={has_jsonld} widget={has_widget} state={has_state} next={has_next}")
        print(f"     title: {(title_m.group(1)[:80] if title_m else '—')}")
        print(f"     h1: {(h1_m.group(1)[:80] if h1_m else '—')}")
        print(f"     price: {price_m.group(0) if price_m else '—'}")

        # If 404/redirect, try another ID
        if resp2 and resp2.status == 404:
            print(f"  → 404, trying 1600562267...")
            resp3 = page.goto("https://www.ozon.ru/product/1600562267/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            prod_content2 = page.content()
            print(f"     status={resp3.status if resp3 else '?'} len={len(prod_content2)}")
            (BASE / "diag_v65_ozon_pw2.html").write_text(prod_content2[:100000], encoding="utf-8")

        results["ozon_pw"] = {
            "main_status": resp1.status if resp1 else None,
            "prod_status": resp2.status if resp2 else None,
            "prod_id": ozon_id,
            "prod_len": len(prod_content),
            "jsonld": has_jsonld, "widget": has_widget,
            "state": has_state, "next": has_next,
            "price": price_m.group(0) if price_m else None,
            "title": title_m.group(1)[:200] if title_m else None,
            "h1": h1_m.group(1)[:200] if h1_m else None,
        }

        browser.close()
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    results["ozon_pw"] = {"error": f"{type(e).__name__}: {e}"}
    results["ozon_trace"] = traceback.format_exc()[:2000]

# ============================================================
# Save
# ============================================================
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n{'='*60}")
print(f"Saved: {OUT}")
print("DONE")
