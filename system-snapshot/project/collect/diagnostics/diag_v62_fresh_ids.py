"""v62: Найти свежие ID товаров + диагностика WB price + Avito RSS.

Запуск: C:\Python315\python.exe diag_v62_fresh_ids.py
"""
import json
import re
import time
import sys
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
    LIB = "curl_cffi"
except ImportError:
    import requests as cffi_requests
    LIB = "requests"

OUT = Path(__file__).parent / "diag_v62_out.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

results = {}

# === 1. OZON: category page → extract product IDs ===
print("=== OZON: category scan ===")
ozon_cat = "https://www.ozon.ru/category/smartfony-15502/?sorting=discount"
try:
    r = cffi_requests.get(ozon_cat, headers=HEADERS, timeout=25, impersonate="chrome")
    print(f"  status={r.status_code} len={len(r.text)}")
    # Ozon embeds product tiles with data-widget or links /product/{id}/
    ids = re.findall(r'/product/(\d{6,})/', r.text)
    # Deduplicate, keep order
    seen = set()
    fresh_ids = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            fresh_ids.append(i)
    print(f"  Found {len(fresh_ids)} product IDs: {fresh_ids[:10]}")
    results["ozon_category"] = {
        "url": ozon_cat, "status": r.status_code,
        "ids_found": fresh_ids[:20], "len": len(r.text)
    }
    # Save first 5000 chars for inspection
    Path(__file__).parent / "diag_v62_ozon_cat.html" \
        .write_text(r.text[:20000], encoding="utf-8")
except Exception as e:
    print(f"  ERROR: {e}")
    results["ozon_category"] = {"error": str(e)}
time.sleep(2)

# === 2. FARPOST: category page → extract bulletin IDs ===
print("\n=== FARPOST: category scan ===")
fp_cat = "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/"
try:
    r = cffi_requests.get(fp_cat, headers=HEADERS, timeout=25, impersonate="chrome")
    print(f"  status={r.status_code} len={len(r.text)}")
    # Farpost bulletin links: /krasnoyarsk/.../slug-12345678.html or /bulletin/12345678
    fp_ids = re.findall(r'(\d{8,12})', r.text)
    # Filter: look for links with .html or /bulletin/
    fp_links = re.findall(r'href="[^"]*?/(\d{8,12})[^"]*?\.html"', r.text)
    fp_bulletin = re.findall(r'/bulletin/(\d+)', r.text)
    print(f"  .html links: {fp_links[:10]}")
    print(f"  /bulletin/: {fp_bulletin[:10]}")
    results["farpost_category"] = {
        "url": fp_cat, "status": r.status_code,
        "html_ids": fp_links[:20], "bulletin_ids": fp_bulletin[:20],
        "len": len(r.text)
    }
    Path(__file__).parent / "diag_v62_fp_cat.html" \
        .write_text(r.text[:20000], encoding="utf-8")
except Exception as e:
    print(f"  ERROR: {e}")
    results["farpost_category"] = {"error": str(e)}
time.sleep(2)

# === 3. WB: basket CDN price — перебор хостов ===
print("\n=== WB: basket CDN price scan ===")
wb_nm = "199298871"
# Determine vol/part from nm
vol = int(wb_nm) // 10000000
part = (int(wb_nm) // 100000) % 100
print(f"  nm={wb_nm} vol={vol} part={part}")

wb_price_results = []
for host_num in [21, 22, 23, 24, 25, 26, 27, 28]:
    url = f"https://basket-{host_num}.wbbasket.ru/vol{vol}/part{part:02d}/{wb_nm}/info/ru/card.json"
    try:
        r = cffi_requests.get(url, headers={"Accept": "application/json"},
                              timeout=10, impersonate="chrome")
        snippet = r.text[:200] if r.text else ""
        print(f"  basket-{host_num}: {r.status_code} len={len(r.text)} → {snippet[:80]}")
        wb_price_results.append({
            "host": f"basket-{host_num}", "url": url,
            "status": r.status_code, "len": len(r.text),
            "snippet": snippet[:200]
        })
        if r.status_code == 200 and len(r.text) > 50:
            # Found working host!
            Path(__file__).parent / f"diag_v62_wb_card_{host_num}.json" \
                .write_text(r.text, encoding="utf-8")
            break
    except Exception as e:
        print(f"  basket-{host_num}: ERROR {e}")
        wb_price_results.append({"host": f"basket-{host_num}", "error": str(e)})
    time.sleep(0.5)

# Also try the new card.wb.ru API
print("  --- card.wb.ru API ---")
api_url = f"https://card.wb.ru/cards/v2/detail?nm={wb_nm}"
try:
    r = cffi_requests.get(api_url, headers={"Accept": "application/json"},
                          timeout=10, impersonate="chrome")
    print(f"  card.wb.ru: {r.status_code} len={len(r.text)}")
    results["wb_card_api"] = {"url": api_url, "status": r.status_code,
                               "len": len(r.text), "snippet": r.text[:300]}
    Path(__file__).parent / "diag_v62_wb_api.json" \
        .write_text(r.text[:5000], encoding="utf-8")
except Exception as e:
    print(f"  card.wb.ru: ERROR {e}")

results["wb_basket"] = wb_price_results
time.sleep(2)

# === 4. AVITO: try RSS / API / different patterns ===
print("\n=== AVITO: multi-strategy ===")
avito_results = {}

# 4a. RSS feed (public, no auth)
rss_url = "https://www.avito.ru/rss/moskva/telefony"
try:
    r = cffi_requests.get(rss_url, headers=HEADERS, timeout=15, impersonate="chrome")
    print(f"  RSS: {r.status_code} len={len(r.text)} ct={r.headers.get('Content-Type','')}")
    avito_results["rss"] = {"url": rss_url, "status": r.status_code,
                             "len": len(r.text), "snippet": r.text[:500]}
    if r.status_code == 200:
        Path(__file__).parent / "diag_v62_avito_rss.xml" \
            .write_text(r.text[:10000], encoding="utf-8")
except Exception as e:
    print(f"  RSS: ERROR {e}")
    avito_results["rss"] = {"error": str(e)}
time.sleep(1)

# 4b. Try mobile API endpoint
api_mob = "https://m.avito.ru/api/v1/ads?city=moskva&category=telefony&limit=5"
try:
    r = cffi_requests.get(api_mob, headers=HEADERS, timeout=15, impersonate="chrome")
    print(f"  m.avito API: {r.status_code} len={len(r.text)}")
    avito_results["m_api"] = {"url": api_mob, "status": r.status_code,
                               "len": len(r.text), "snippet": r.text[:300]}
except Exception as e:
    print(f"  m.avito API: ERROR {e}")
    avito_results["m_api"] = {"error": str(e)}
time.sleep(1)

# 4c. Try the known ad URL with different Accept
ad_url = "https://www.avito.ru/moskva/telefony/iphone_14_128_gb_8341658309"
for accept in ["text/html", "application/json", "*/*"]:
    try:
        r = cffi_requests.get(ad_url, headers={**HEADERS, "Accept": accept},
                              timeout=15, impersonate="chrome")
        print(f"  ad (Accept={accept}): {r.status_code} len={len(r.text)}")
        avito_results[f"ad_accept_{accept}"] = {
            "status": r.status_code, "len": len(r.text),
            "snippet": r.text[:300]}
    except Exception as e:
        print(f"  ad (Accept={accept}): ERROR {e}")
    time.sleep(1)

# 4d. Try with Sec-Fetch headers (more browser-like)
browser_headers = {
    **HEADERS,
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}
try:
    r = cffi_requests.get(ad_url, headers=browser_headers, timeout=15, impersonate="chrome")
    print(f"  ad (full browser): {r.status_code} len={len(r.text)}")
    avito_results["ad_full_browser"] = {
        "status": r.status_code, "len": len(r.text),
        "snippet": r.text[:500]}
    if r.status_code == 200:
        Path(__file__).parent / "diag_v62_avito_ad.html" \
            .write_text(r.text[:30000], encoding="utf-8")
except Exception as e:
    print(f"  ad (full browser): ERROR {e}")
    avito_results["ad_full_browser"] = {"error": str(e)}

results["avito"] = avito_results

# === Save ===
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {OUT}")
print("DONE")
