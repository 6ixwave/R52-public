"""Диагностика: Search API + locale для AliExpress RU."""
import json, re, time, sys
from urllib.parse import quote
from curl_cffi import requests as cffi_req

print("=" * 60)
print("DIAG: AliExpress RU Search API + Locale")
print("=" * 60)

QUERY = "наушники bluetooth"
ENQ = quote(QUERY, safe="")

# --- TEST 1: Search API with different header combos ---
API_URLS = [
    ("A: aer-webapi + XHR", "https://ru.aliexpress.com/aer-webapi/v1/search", {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={ENQ}",
        "Origin": "https://ru.aliexpress.com",
    }),
    ("B: aer-webapi no XHR", "https://ru.aliexpress.com/aer-webapi/v1/search", {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={ENQ}",
    }),
    ("C: mtop search", "https://ru.aliexpress.com/h5/mtop.relationrecommend.AliExpressRecommend.recommend/1.0/", {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }),
]

COOKIE = {"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"}

for label, url, hdrs in API_URLS:
    print(f"\n--- {label} ---")
    try:
        t0 = time.monotonic()
        r = cffi_req.get(
            f"{url}?SearchText={ENQ}&page=1&pageSize=20&sortType=default&_lang=ru_RU&_currency=RUB",
            impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=COOKIE,
        )
        lat = time.monotonic() - t0
        body = r.text[:500]
        print(f"  HTTP {r.status_code}, {len(r.text)} bytes, {lat:.1f}s")
        print(f"  Content-Type: {r.headers.get('content-type','?')}")
        print(f"  Body[:200]: {body[:200]}")
        # Try JSON parse
        try:
            d = r.json()
            print(f"  JSON OK! keys={list(d.keys())[:5]}")
        except Exception as je:
            print(f"  JSON parse fail: {je}")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(1)

# --- TEST 2: Wholesale with www.aliexpress.com + _lang ---
print(f"\n{'=' * 60}")
print("TEST: www.aliexpress.com wholesale with _lang=ru_RU")
print("=" * 60)
for domain, label in [("ru.aliexpress.com", "ru domain"), ("www.aliexpress.com", "www domain")]:
    print(f"\n--- {label} ---")
    url = f"https://{domain}/wholesale?SearchText={ENQ}&page=1&_lang=ru_RU&_currency=RUB"
    try:
        t0 = time.monotonic()
        r = cffi_req.get(url, impersonate="chrome", timeout=(15, 30),
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": f"https://{domain}/",
            },
            cookies=COOKIE,
        )
        lat = time.monotonic() - t0
        html = r.text
        lang_m = re.search(r'<html[^>]*lang=["\']([^"\']*)', html)
        print(f"  HTTP {r.status_code}, {len(html)} bytes, {lat:.1f}s")
        print(f"  <html lang>: {lang_m.group(1) if lang_m else 'NOT FOUND'}")
        # Extract titles
        titles = re.findall(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', html)
        if not titles:
            titles = re.findall(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', html, re.DOTALL)
            titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]
        print(f"  Titles: {len(titles)}")
        for t in titles[:4]:
            print(f"    {t[:120]}")
        # RU detection
        ru_chars = len(re.findall(r'[\u0410-\u044f]', html[:200000]))
        print(f"  RU chars in first 200KB: {ru_chars}")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(2)

# --- TEST 3: Product page by ID (locale check) ---
print(f"\n{'=' * 60}")
print("TEST: Product page by ID")
print("=" * 60)
for pid in ["1005010340652259", "1005012296880178"]:
    print(f"\n--- ID {pid} ---")
    for domain in ["ru.aliexpress.com", "www.aliexpress.com"]:
        url = f"https://{domain}/item/{pid}.html?_lang=ru_RU"
        try:
            t0 = time.monotonic()
            r = cffi_req.get(url, impersonate="chrome", timeout=(15, 30),
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                },
                cookies=COOKIE,
            )
            lat = time.monotonic() - t0
            html = r.text
            lang_m = re.search(r'<html[^>]*lang=["\']([^"\']*)', html)
            title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
            og_title = re.search(r'property="og:title"[^>]*content="([^"]*)"', html)
            price_m = re.findall(r'pdp_npi=([^&"]+)', html[:50000])
            print(f"  [{domain}] HTTP {r.status_code}, {len(html)} bytes, {lat:.1f}s")
            print(f"    lang: {lang_m.group(1) if lang_m else '?'}")
            print(f"    title: {(title_m.group(1)[:100] if title_m else '?')}")
            print(f"    og:title: {(og_title.group(1)[:100] if og_title else '?')}")
            if price_m:
                print(f"    pdp_npi: {price_m[0][:80]}")
        except Exception as e:
            print(f"  [{domain}] ERROR: {e}")
        time.sleep(1)

print("\nDONE")
