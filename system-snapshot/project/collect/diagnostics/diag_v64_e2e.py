"""v64: E2E по каждой площадке с ИСПРАВЛЕННЫМ подходом.

Стратегия:
- Farpost: полный slug-URL (не /bulletin/)
- WB: integration_test уже OK, проверяем цену через basket CDN (collector logic)
- Ozon: composer API + Playwright fallback
- Avito: Playwright mobile, категория → клик

Запуск: C:\Python315\python.exe diag_v64_e2e.py
"""
import json
import re
import time
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "diag_v64_out.json"

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    import requests as cffi_requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

results = {}

# ============================================================
# 1. FARPOST: E2E с реальным slug-URL
# ============================================================
print("=" * 60)
print("1. FARPOST: slug URL E2E")
print("=" * 60)

# Реальные URL из v63:
FP_URLS = [
    "https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/triangle-icelynx-ti501-205-55-r16-94t-xl-130693618.html",
    "https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/gruzovye-shiny-295-80r22-5-tl-18pr-goodride-icetrac-n2-138279778.html",
]

# Warm-up: GET main page for cookies
try:
    s = cffi_requests.Session()
    r_warm = s.get("https://www.farpost.ru/", headers=HEADERS, timeout=15, impersonate="chrome")
    print(f"  warm-up: {r_warm.status_code} cookies={list(s.cookies.keys())}")
    time.sleep(1)

    for i, fp_url in enumerate(FP_URLS):
        print(f"\n  [{i+1}] {fp_url[:80]}...")
        r = s.get(fp_url, headers=HEADERS, timeout=25, impersonate="chrome")
        print(f"      status={r.status_code} len={len(r.text)}")
        (BASE / f"diag_v64_fp_{i+1}.html").write_text(r.text[:100000], encoding="utf-8")

        # Quick parse check
        has_jsonld = 'application/ld+json' in r.text
        has_og_title = 'og:title' in r.text
        has_price = re.search(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', r.text)
        title_m = re.search(r'<title>(.*?)</title>', r.text, re.DOTALL)
        og_title_m = re.search(r'property="og:title"\s+content="([^"]*)"', r.text)

        print(f"      jsonld={has_jsonld} og={has_og_title} price={bool(has_price)}")
        print(f"      <title>: {(title_m.group(1)[:80] if title_m else '—')}")
        print(f"      og:title: {(og_title_m.group(1)[:80] if og_title_m else '—')}")
        if has_price:
            print(f"      price: {has_price.group(0)}")

        results[f"farpost_{i+1}"] = {
            "url": fp_url, "status": r.status_code, "len": len(r.text),
            "jsonld": has_jsonld, "og": has_og_title,
            "price": has_price.group(0) if has_price else None,
            "title": title_m.group(1)[:200] if title_m else None,
            "og_title": og_title_m.group(1)[:200] if og_title_m else None,
        }
        time.sleep(2)
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    results["farpost_error"] = str(e)

# ============================================================
# 2. WB: Price via collector's basket logic
# ============================================================
print("\n" + "=" * 60)
print("2. WB: price extraction diagnostic")
print("=" * 60)

# The collector determines basket host from nm. Let's replicate:
# Read wb.py to find the basket URL construction
wb_nm = "199298871"
nm_int = int(wb_nm)

# From wb.py: vol = nm // 10^7, part = (nm // 10^5) % 100
# But ALL returned 404. Let's try the "new" approach:
# Maybe it's: https://basket-{N}.wbbasket.ru/vol{V}/part{P}/{nm}/info/ru/card.json
# where V and P come from a different formula or a lookup API

# Try: the "nm" to "vol/part" mapping might come from the detail page HTML
# or from a specific API endpoint
wb_price_results = []

# Strategy A: Try the static-basket with different path patterns
patterns = [
    # Old formula
    f"vol{nm_int // 10**7}/part{(nm_int // 10**5) % 100:02d}",
    # Alternative: vol0/part{nm%100}
    f"vol0/part{nm_int % 100:02d}",
    # Alternative: vol{nm//10^7}/part{nm%100}
    f"vol{nm_int // 10**7}/part{nm_int % 100:02d}",
    # New: maybe no vol/part at all
    f"vol0/part00",
]

for pat in patterns:
    for host in [1, 5, 10, 21, 30, 50]:
        url = f"https://basket-{host}.wbbasket.ru/{pat}/{wb_nm}/info/ru/card.json"
        try:
            r = cffi_requests.get(url, headers={"Accept": "application/json"},
                                  timeout=6, impersonate="chrome")
            ok = r.status_code == 200 and len(r.text) > 50
            if ok or r.status_code not in (404,):
                print(f"  {pat} h={host}: {r.status_code} len={len(r.text)}")
                wb_price_results.append({"pat": pat, "host": host, "status": r.status_code, "len": len(r.text)})
                if ok:
                    (BASE / f"diag_v64_wb_{host}_{pat.replace('/','_')}.json").write_text(r.text, encoding="utf-8")
        except:
            pass
        time.sleep(0.2)

# Strategy B: WB "new" price API (2024+): 
# https://card.wb.ru/cards/v2/detail?nm=... (was 403)
# Try with different headers
print("  --- card.wb.ru with browser headers ---")
try:
    r = cffi_requests.get(f"https://card.wb.ru/cards/v2/detail?nm={wb_nm}",
                          headers={**HEADERS, "Referer": "https://www.wildberries.ru/"},
                          timeout=10, impersonate="chrome")
    print(f"  card.wb.ru (referer): {r.status_code} len={len(r.text)}")
    wb_price_results.append({"api": "card.wb.ru+referer", "status": r.status_code, "len": len(r.text)})
    if r.status_code == 200:
        (BASE / "diag_v64_wb_api.json").write_text(r.text[:10000], encoding="utf-8")
except Exception as e:
    print(f"  card.wb.ru: {e}")

# Strategy C: Try the "basket" with the nm directly (no vol/part)
print("  --- basket direct ---")
for host in [21, 22, 23]:
    for path_pat in [
        f"vol0/part{(nm_int // 10**5) % 100:02d}/{wb_nm}",
        f"vol{(nm_int // 10**7):02d}/part{(nm_int // 10**5) % 100:02d}/{wb_nm}",
    ]:
        url = f"https://basket-{host}.wbbasket.ru/{path_pat}/info/ru/card.json"
        try:
            r = cffi_requests.get(url, headers={"Accept": "application/json"},
                                  timeout=6, impersonate="chrome")
            if r.status_code == 200:
                print(f"  FOUND: {url} → {r.status_code} len={len(r.text)}")
                wb_price_results.append({"url": url, "status": 200, "len": len(r.text)})
                (BASE / "diag_v64_wb_found.json").write_text(r.text, encoding="utf-8")
        except:
            pass
        time.sleep(0.2)

results["wb_price"] = wb_price_results

# ============================================================
# 3. OZON: Composer API
# ============================================================
print("\n" + "=" * 60)
print("3. OZON: composer API + product page")
print("=" * 60)

# Ozon has a semi-public composer API:
# GET https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{id}/
# Or the newer: https://www.ozon.ru/api/entrypoint/pwa.home.aspx
ozon_ids_to_try = ["1600562267", "4605111975"]  # from web_search

ozon_results = {}

# 3a: Composer API
for oid in ozon_ids_to_try:
    api_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
    try:
        r = cffi_requests.get(api_url, headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": f"https://www.ozon.ru/product/{oid}/",
        }, timeout=15, impersonate="chrome")
        print(f"  composer /{oid}: {r.status_code} len={len(r.text)}")
        ozon_results[f"composer_{oid}"] = {
            "status": r.status_code, "len": len(r.text),
            "snippet": r.text[:500]
        }
        if r.status_code == 200 and len(r.text) > 100:
            (BASE / f"diag_v64_ozon_api_{oid}.json").write_text(r.text[:50000], encoding="utf-8")
    except Exception as e:
        print(f"  composer /{oid}: {e}")
        ozon_results[f"composer_{oid}"] = {"error": str(e)}
    time.sleep(1)

# 3b: Try the PWA API endpoint
pwa_url = f"https://www.ozon.ru/api/entrypoint/pwa.home.aspx?url=/product/{ozon_ids_to_try[0]}/"
try:
    r = cffi_requests.get(pwa_url, headers={**HEADERS, "Accept": "application/json"},
                          timeout=15, impersonate="chrome")
    print(f"  pwa.home: {r.status_code} len={len(r.text)}")
    ozon_results["pwa"] = {"status": r.status_code, "len": len(r.text), "snippet": r.text[:300]}
except Exception as e:
    ozon_results["pwa"] = {"error": str(e)}

# 3c: Try with a longer warmup (main → wait → product)
print("  --- Ozon with 5s warmup ---")
try:
    s2 = cffi_requests.Session()
    r1 = s2.get("https://www.ozon.ru/", headers=HEADERS, timeout=20, impersonate="chrome")
    print(f"  main: {r1.status_code} cookies={list(s2.cookies.keys())}")
    time.sleep(5)  # Longer wait
    r2 = s2.get(f"https://www.ozon.ru/product/{ozon_ids_to_try[0]}/",
                headers=HEADERS, timeout=30, impersonate="chrome")
    print(f"  product: {r2.status_code} len={len(r2.text)}")
    ozon_results["warmup5s"] = {
        "main": r1.status_code, "product": r2.status_code,
        "product_len": len(r2.text),
        "has_widget": 'data-widget' in r2.text,
        "has_jsonld": 'ld+json' in r2.text,
    }
    if len(r2.text) > 50000:
        (BASE / "diag_v64_ozon_warm.html").write_text(r2.text[:100000], encoding="utf-8")
except Exception as e:
    ozon_results["warmup5s"] = {"error": str(e)}

results["ozon"] = ozon_results

# ============================================================
# 4. AVITO: Playwright mobile + category-first
# ============================================================
print("\n" + "=" * 60)
print("4. AVITO: Playwright mobile")
print("=" * 60)

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ]
        )
        # Mobile context
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            locale="ru-RU",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
        )
        page = ctx.new_page()

        # Step 1: Main page
        print("  → avito.ru main (mobile)...")
        resp1 = page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        print(f"     status={resp1.status if resp1 else '?'} len={len(page.content())}")

        # Step 2: Category page (telefony)
        print("  → category: moskva/telefony...")
        resp2 = page.goto("https://www.avito.ru/moskva/telefony", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        cat_content = page.content()
        print(f"     status={resp2.status if resp2 else '?'} len={len(cat_content)}")
        (BASE / "diag_v64_avito_cat.html").write_text(cat_content[:80000], encoding="utf-8")

        # Extract ad links from category
        ad_links = re.findall(r'href="(/[^"]*?/\d{8,12})"', cat_content)
        print(f"     ad links found: {len(ad_links)} → {ad_links[:5]}")

        # Step 3: Click into first ad (or use known URL)
        target_ad = ad_links[0] if ad_links else "/moskva/telefony/iphone_14_128_gb_8341658309"
        ad_url = f"https://www.avito.ru{target_ad}" if target_ad.startswith("/") else target_ad
        print(f"  → ad: {ad_url[:80]}")
        resp3 = page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        ad_content = page.content()
        print(f"     status={resp3.status if resp3 else '?'} len={len(ad_content)}")
        (BASE / "diag_v64_avito_ad.html").write_text(ad_content[:80000], encoding="utf-8")

        # Parse
        has_jsonld = 'application/ld+json' in ad_content
        has_preloaded = '__PRELOADED_STATE__' in ad_content or 'data-state' in ad_content
        has_og = 'og:title' in ad_content
        price_m = re.search(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', ad_content)
        title_m = re.search(r'<title>(.*?)</title>', ad_content, re.DOTALL)
        h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', ad_content, re.DOTALL)

        print(f"     jsonld={has_jsonld} preloaded={has_preloaded} og={has_og}")
        print(f"     title: {(title_m.group(1)[:80] if title_m else '—')}")
        print(f"     h1: {(h1_m.group(1)[:80] if h1_m else '—')}")
        print(f"     price: {price_m.group(0) if price_m else '—'}")

        results["avito"] = {
            "main_status": resp1.status if resp1 else None,
            "cat_status": resp2.status if resp2 else None,
            "cat_len": len(cat_content),
            "ad_url": ad_url,
            "ad_status": resp3.status if resp3 else None,
            "ad_len": len(ad_content),
            "jsonld": has_jsonld, "preloaded": has_preloaded, "og": has_og,
            "price": price_m.group(0) if price_m else None,
            "title": title_m.group(1)[:200] if title_m else None,
            "h1": h1_m.group(1)[:200] if h1_m else None,
            "ad_links_from_cat": ad_links[:10],
        }

        browser.close()
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    results["avito"] = {"error": f"{type(e).__name__}: {e}"}
    results["avito_trace"] = traceback.format_exc()[:2000]

# ============================================================
# Save
# ============================================================
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n{'='*60}")
print(f"Saved: {OUT}")
print("DONE")
