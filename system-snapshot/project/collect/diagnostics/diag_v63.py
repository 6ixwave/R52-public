"""v63: Целенаправленная диагностика по каждой площадке.

Запуск: C:\Python315\python.exe diag_v63.py
Результат: diag_v63_out.json + сохранённые HTML
"""
import json
import re
import time
import sys
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    import requests as cffi_requests

BASE = Path(__file__).parent
OUT = BASE / "diag_v63_out.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

results = {}

# ============================================================
# 1. FARPOST: сохранить категорию, найти bulletin ID
# ============================================================
print("=== 1. FARPOST: category parse ===")
try:
    r = cffi_requests.get(
        "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/",
        headers=HEADERS, timeout=25, impersonate="chrome"
    )
    print(f"  status={r.status_code} len={len(r.text)}")
    (BASE / "diag_v63_fp_cat.html").write_text(r.text, encoding="utf-8")

    # Farpost link patterns (try multiple):
    # /krasnoyarsk/auto/wheel/tire/shiny-139122082.html
    # /bulletin/139122082
    # data-id="139122082"
    patterns = {
        "slug_html": r'href="[^"]*?/([a-z0-9-]+?)-(\d{8,12})\.html"',
        "bulletin": r'/bulletin/(\d+)',
        "data_id": r'data-id["\s:=]+(\d{8,12})',
        "item_id": r'data-item-id["\s:=]+(\d{8,12})',
        "generic_num": r'>(\d{9,12})<',
    }
    fp_ids = {}
    for name, pat in patterns.items():
        found = re.findall(pat, r.text)
        if isinstance(found[0], tuple) if found else False:
            # slug_html returns tuples (slug, id)
            ids = [t[1] for t in found[:20]]
        else:
            ids = found[:20]
        fp_ids[name] = ids
        print(f"  {name}: {len(ids)} → {ids[:5]}")

    # Also look for any /krasnoyarsk/.../....html links
    all_links = re.findall(r'href="(/[^"]+\.html)"', r.text)
    print(f"  all .html links: {len(all_links)}")
    print(f"  sample: {all_links[:5]}")

    # Look for bulletin in data attributes
    data_attrs = re.findall(r'data-[a-z-]*id["\s:=]+["\']?(\d{8,12})', r.text)
    print(f"  data-*id: {len(data_attrs)} → {data_attrs[:5]}")

    results["farpost"] = {
        "status": r.status_code, "len": len(r.text),
        "patterns": fp_ids,
        "all_html_links": all_links[:30],
        "data_attrs": data_attrs[:10],
    }
except Exception as e:
    print(f"  ERROR: {e}")
    results["farpost"] = {"error": str(e)}

time.sleep(2)

# ============================================================
# 2. OZON: session warmup → product page
# ============================================================
print("\n=== 2. OZON: session warmup ===")
try:
    # Step 1: GET main page to collect cookies
    s = cffi_requests.Session()
    r_main = s.get("https://www.ozon.ru/", headers=HEADERS, timeout=20, impersonate="chrome")
    print(f"  main: {r_main.status_code} len={len(r_main.text)} cookies={list(s.cookies.keys())}")
    time.sleep(1)

    # Step 2: GET category (to get more cookies)
    r_cat = s.get("https://www.ozon.ru/category/smartfony-15502/",
                  headers=HEADERS, timeout=20, impersonate="chrome")
    print(f"  category: {r_cat.status_code} len={len(r_cat.text)}")
    (BASE / "diag_v63_ozon_cat.html").write_text(r_cat.text[:50000], encoding="utf-8")

    # Extract product IDs from category
    ozon_ids = re.findall(r'/product/(\d{6,})/', r_cat.text)
    seen = set()
    fresh = []
    for i in ozon_ids:
        if i not in seen:
            seen.add(i)
            fresh.append(i)
    print(f"  category IDs: {len(fresh)} → {fresh[:5]}")
    time.sleep(1)

    # Step 3: GET a product page
    test_id = fresh[0] if fresh else "1600562267"
    r_prod = s.get(f"https://www.ozon.ru/product/{test_id}/",
                   headers=HEADERS, timeout=25, impersonate="chrome")
    print(f"  product {test_id}: {r_prod.status_code} len={len(r_prod.text)}")
    (BASE / f"diag_v63_ozon_{test_id}.html").write_text(r_prod.text[:100000], encoding="utf-8")

    # Check for data-widget, JSON-LD, __NEXT_DATA__
    has_widget = 'data-widget' in r_prod.text
    has_jsonld = 'application/ld+json' in r_prod.text
    has_next = '__NEXT_DATA__' in r_prod.text
    has_state = 'window.__STATE' in r_prod.text or 'window.__INITIAL_STATE' in r_prod.text
    print(f"  markers: widget={has_widget} jsonld={has_jsonld} next={has_next} state={has_state}")

    results["ozon"] = {
        "main_status": r_main.status_code,
        "cat_status": r_cat.status_code,
        "cat_len": len(r_cat.text),
        "product_id": test_id,
        "product_status": r_prod.status_code,
        "product_len": len(r_prod.text),
        "markers": {"widget": has_widget, "jsonld": has_jsonld,
                    "next": has_next, "state": has_state},
        "ids_found": fresh[:10],
        "cookies": list(s.cookies.keys()),
    }
except Exception as e:
    print(f"  ERROR: {e}")
    results["ozon"] = {"error": str(e)}

time.sleep(2)

# ============================================================
# 3. WB: basket CDN — перебор структур
# ============================================================
print("\n=== 3. WB: basket CDN structure scan ===")
wb_nm = "199298871"
nm_int = int(wb_nm)

# Different vol/part calculations:
# Old: vol = nm // 10^7, part = (nm // 10^5) % 100
# New: maybe vol = nm // 10^7, part = (nm // 10^4) % 100?
# Or: vol0/partXX where XX = nm % 100?
structures = [
    f"vol{nm_int // 10000000}/part{(nm_int // 100000) % 100:02d}",   # vol19/part92
    f"vol{nm_int // 10000000}/part{(nm_int // 10000) % 100:02d}",    # vol19/part98
    f"vol0/part{(nm_int // 100000) % 100:02d}",                       # vol0/part92
    f"vol0/part{(nm_int // 10000) % 100:02d}",                        # vol0/part98
    f"vol{nm_int // 100000000}/part{(nm_int // 100000) % 100:02d}",  # vol1/part92
]
# Deduplicate
structures = list(dict.fromkeys(structures))

wb_results = []
for struct in structures:
    for host in [21, 22, 23]:
        url = f"https://basket-{host}.wbbasket.ru/{struct}/{wb_nm}/info/ru/card.json"
        try:
            r = cffi_requests.get(url, headers={"Accept": "application/json"},
                                  timeout=8, impersonate="chrome")
            ok = r.status_code == 200 and len(r.text) > 100
            print(f"  {struct} host={host}: {r.status_code} len={len(r.text)}")
            wb_results.append({"struct": struct, "host": host,
                               "status": r.status_code, "len": len(r.text)})
            if ok:
                (BASE / f"diag_v63_wb_{struct.replace('/','_')}.json").write_text(r.text, encoding="utf-8")
                print(f"    FOUND! Saved.")
                break
        except Exception as e:
            print(f"  {struct} host={host}: ERR {e}")
        time.sleep(0.3)
    else:
        continue
    break

# Also try the "new" WB API: static-basket
for host in ["static-basket", "static-basket-01"]:
    for struct in structures[:2]:
        url = f"https://{host}.wbbasket.ru/{struct}/{wb_nm}/info/ru/card.json"
        try:
            r = cffi_requests.get(url, headers={"Accept": "application/json"},
                                  timeout=8, impersonate="chrome")
            print(f"  {host}/{struct}: {r.status_code} len={len(r.text)}")
            wb_results.append({"host": host, "struct": struct,
                               "status": r.status_code, "len": len(r.text)})
        except Exception as e:
            print(f"  {host}/{struct}: ERR {e}")
        time.sleep(0.3)

results["wb_basket"] = wb_results

# WB: also try the detail page HTML for embedded data
print("  --- WB detail HTML ---")
try:
    r = cffi_requests.get(f"https://www.wildberries.ru/catalog/{wb_nm}/detail.aspx",
                          headers=HEADERS, timeout=20, impersonate="chrome")
    print(f"  detail: {r.status_code} len={len(r.text)}")
    (BASE / f"diag_v63_wb_{wb_nm}.html").write_text(r.text[:80000], encoding="utf-8")
    # Look for price in HTML
    price_matches = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', r.text)
    print(f"  price patterns in HTML: {price_matches[:5]}")
    results["wb_detail"] = {"status": r.status_code, "len": len(r.text),
                            "prices_found": price_matches[:5]}
except Exception as e:
    print(f"  detail ERROR: {e}")
    results["wb_detail"] = {"error": str(e)}

time.sleep(2)

# ============================================================
# 4. AVITO: Playwright headless
# ============================================================
print("\n=== 4. AVITO: Playwright ===")
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()

        # First visit: main page (get cookies)
        print("  visiting avito.ru main...")
        page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        cookies = ctx.cookies()
        print(f"  cookies after main: {len(cookies)}")

        # Second visit: specific ad
        ad_url = "https://www.avito.ru/moskva/telefony/iphone_14_128_gb_8341658309"
        print(f"  visiting ad: {ad_url}")
        resp = page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        status = resp.status if resp else None
        content = page.content()
        print(f"  ad status={status} len={len(content)}")

        (BASE / "diag_v63_avito_pw.html").write_text(content[:80000], encoding="utf-8")

        # Check for data
        has_jsonld = 'application/ld+json' in content
        has_preloaded = '__PRELOADED_STATE__' in content or 'data-state' in content
        has_og = 'og:title' in content or 'og:description' in content
        has_price = re.search(r'\d[\d\s]{2,10}\s*(?:₽|руб)', content)
        title_m = re.search(r'<title>(.*?)</title>', content, re.DOTALL)
        print(f"  jsonld={has_jsonld} preloaded={has_preloaded} og={has_og} price={bool(has_price)}")
        if title_m:
            print(f"  title: {title_m.group(1)[:100]}")

        results["avito_pw"] = {
            "status": status, "len": len(content),
            "cookies_count": len(cookies),
            "jsonld": has_jsonld, "preloaded": has_preloaded,
            "og": has_og, "price": bool(has_price),
            "title": title_m.group(1)[:200] if title_m else None,
        }

        browser.close()
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
    results["avito_pw"] = {"error": f"{type(e).__name__}: {e}"}

# ============================================================
# Save
# ============================================================
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {OUT}")
print("DONE")
