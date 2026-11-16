"""Диагностика v20: точечная проверка API-эндпоинтов каждой площадки.

Фокус:
- WB: card.wb.ru API (не HTML), static-basket, basket-XX
- Ozon: api.ozon.ru, mobile, composer с правильными заголовками
- Avito: PoW flow с cookie, m.avito.ru
- Farpost: разные URL-паттерны (item/ads/category/id)
- Прокси: дополнительные источники, проверка через саму площадку
"""

import json
import logging
import re
import sys
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diag_v20")

TIMEOUT = 15
TOTAL_BUDGET = 180  # секунд

t0 = time.monotonic()
results = {}

# --- curl_cffi availability ---
try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    log.info("curl_cffi available")
except ImportError:
    HAS_CFFI = False
    log.warning("curl_cffi NOT available")

import requests as std_req
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
HEADERS_API = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}

def rem():
    return max(0, TOTAL_BUDGET - (time.monotonic() - t0))

def safe_req(session, url, headers=None, name=""):
    """GET с таймаутом, возвращает (status, bytes, elapsed)."""
    try:
        h = headers or HEADERS
        r = session.get(url, headers=h, timeout=TIMEOUT, allow_redirects=True)
        return r.status_code, len(r.content), r
    except Exception as e:
        return -1, 0, str(e)

def cffi_get(url, headers=None, name=""):
    """GET через curl_cffi с impersonate=chrome."""
    try:
        s = cffi_req.Session(impersonate="chrome")
        h = headers or HEADERS
        r = s.get(url, headers=h, timeout=TIMEOUT)
        return r.status_code, len(r.content), r
    except Exception as e:
        return -1, 0, str(e)

# ======================================================================
# 1. WILDBERRIES — API endpoints
# ======================================================================
print("\n" + "=" * 70)
print(f"1. WILDBERRIES [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

wb_id = "199298871"
vol = str(int(wb_id) // 1000000)  # 199
part = str(int(wb_id) // 1000)    # 1992988
# Actually: vol = nm // 1000000, part = (nm // 1000) % 1000000
vol_num = int(wb_id) // 1000000
part_num = (int(wb_id) // 1000) % 1000000

wb_results = {}

# 1a. card.wb.ru — основной API карточки
urls_wb = [
    (f"card_v2", f"https://card.wb.ru/cards/v2/detail?nm={wb_id}"),
    (f"card_v1", f"https://card.wb.ru/cards/v1/detail?nm={wb_id}"),
    (f"card_v2_json", f"https://card.wb.ru/cards/v2/detail?nm={wb_id}&supp=1"),
    (f"static_info", f"https://static-basket-{vol_num}.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v2", f"https://static-basket-{vol_num}1.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v3", f"https://static-basket-{vol_num}2.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v4", f"https://static-basket-{vol_num}3.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v5", f"https://static-basket-{vol_num}4.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v6", f"https://static-basket-{vol_num}5.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v7", f"https://static-basket-{vol_num}6.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v8", f"https://static-basket-{vol_num}7.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v9", f"https://static-basket-{vol_num}8.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
    (f"static_info_v10", f"https://static-basket-{vol_num}9.wbbasket.ru/vol{vol_num}/part{part_num}/{wb_id}/info/{wb_id}-info.json"),
]

# Standard requests
sess = std_req.Session()
sess.headers.update(HEADERS_API)

for name, url in urls_wb[:6]:  # first 6 with standard
    status, size, resp = safe_req(sess, url, HEADERS_API, name)
    body_preview = ""
    if hasattr(resp, 'text') and size > 0:
        body_preview = resp.text[:200].replace('\n', ' ')
    print(f"  [{name}] {status} | {size}B | {body_preview[:120]}")
    wb_results[name] = {"status": status, "size": size}
    if rem() < 20:
        break

# curl_cffi for card API
if HAS_CFFI:
    for name, url in [("cffi_card_v2", urls_wb[0][1]), ("cffi_card_v1", urls_wb[1][1])]:
        status, size, resp = cffi_get(url, HEADERS_API, name)
        body_preview = ""
        if hasattr(resp, 'text') and size > 0:
            body_preview = resp.text[:200].replace('\n', ' ')
        print(f"  [{name}] {status} | {size}B | {body_preview[:120]}")
        wb_results[name] = {"status": status, "size": size}
        if rem() < 15:
            break

# Try a known-good WB product (small ID)
wb_id2 = "142770354"
vol2 = int(wb_id2) // 1000000
part2 = (int(wb_id2) // 1000) % 1000000
url2 = f"https://card.wb.ru/cards/v2/detail?nm={wb_id2}"
status, size, resp = safe_req(sess, url2, HEADERS_API, "card_alt")
body_preview = ""
if hasattr(resp, 'text') and size > 0:
    body_preview = resp.text[:300].replace('\n', ' ')
print(f"  [card_alt {wb_id2}] {status} | {size}B | {body_preview[:200]}")
wb_results["card_alt"] = {"status": status, "size": size}

# Try basket CDN with alt ID
for suffix in ["", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
    url_cdn = f"https://static-basket-{vol2}{suffix}.wbbasket.ru/vol{vol2}/part{part2}/{wb_id2}/info/{wb_id2}-info.json"
    status, size, resp = safe_req(sess, url_cdn, HEADERS_API, f"cdn_{suffix}")
    if status == 200 and size > 100:
        print(f"  [CDN hit suffix={suffix}] {status} | {size}B")
        wb_results[f"cdn_{suffix}"] = {"status": status, "size": size}
        break
    if rem() < 10:
        break

results["WB"] = wb_results
print(f"  WB section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 2. OZON — API + composer + mobile
# ======================================================================
print("\n" + "=" * 70)
print(f"2. OZON [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

ozon_id = "1600562267"
ozon_results = {}

# Ozon API endpoints
urls_ozon = [
    ("api_composer", f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{ozon_id}/"),
    ("api_product", f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=/product/{ozon_id}/"),
    ("api_mobile", f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{ozon_id}/&device=mobile"),
    ("page_direct", f"https://www.ozon.ru/product/{ozon_id}/"),
    ("api_search", f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=/search/?text=test&from_global=true"),
]

sess_ozon = std_req.Session()
sess_ozon.headers.update(HEADERS)
# Add specific Ozon headers
sess_ozon.headers["Accept"] = "application/json, text/plain, */*"
sess_ozon.headers["X-Request-Id"] = "0"

for name, url in urls_ozon:
    status, size, resp = safe_req(sess_ozon, url, None, name)
    body_preview = ""
    if hasattr(resp, 'text') and size > 0:
        body_preview = resp.text[:250].replace('\n', ' ')
    print(f"  [{name}] {status} | {size}B | {body_preview[:180]}")
    ozon_results[name] = {"status": status, "size": size}
    if rem() < 20:
        break

# curl_cffi for Ozon
if HAS_CFFI:
    for name, url in [("cffi_page", f"https://www.ozon.ru/product/{ozon_id}/"),
                      ("cffi_composer", urls_ozon[0][1])]:
        status, size, resp = cffi_get(url, HEADERS, name)
        body_preview = ""
        if hasattr(resp, 'text') and size > 0:
            body_preview = resp.text[:250].replace('\n', ' ')
        print(f"  [{name}] {status} | {size}B | {body_preview[:180]}")
        ozon_results[name] = {"status": status, "size": size}
        if rem() < 15:
            break

# Try with cookies from initial visit
try:
    s2 = std_req.Session()
    s2.headers.update(HEADERS)
    r1 = s2.get(f"https://www.ozon.ru/", timeout=TIMEOUT)
    cookies = dict(s2.cookies)
    print(f"  [ozon_home] {r1.status_code} | cookies: {list(cookies.keys())}")
    # Now try product with cookies
    r2 = s2.get(f"https://www.ozon.ru/product/{ozon_id}/", timeout=TIMEOUT)
    print(f"  [ozon_product_2nd] {r2.status_code} | {len(r2.content)}B")
    ozon_results["product_2nd"] = {"status": r2.status_code, "size": len(r2.content)}
except Exception as e:
    print(f"  [ozon_session] ERR: {e}")

results["OZON"] = ozon_results
print(f"  OZON section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 3. AVITO — PoW flow + mobile
# ======================================================================
print("\n" + "=" * 70)
print(f"3. AVITO [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

avito_id = "1096783029"
avito_results = {}

# 3a. Direct with proper PoW
sess_av = std_req.Session()
sess_av.headers.update(HEADERS)

try:
    # Step 1: Get main page to get PoW challenge
    r1 = sess_av.get(f"https://www.avito.ru/", timeout=TIMEOUT)
    print(f"  [av_home] {r1.status_code} | {len(r1.content)}B")
    
    # Extract PoW params
    html1 = r1.text
    pow_match = re.search(r'startPow\((\d+)\)', html1)
    complexity = int(pow_match.group(1)) if pow_match else 10
    print(f"  PoW complexity: {complexity}")
    
    # Look for nonce/challenge in HTML
    nonce_match = re.search(r'nonce["\s:=]+["\']?(\d+)', html1)
    challenge_match = re.search(r'challenge["\s:=]+["\']?([\w]+)', html1)
    print(f"  nonce: {nonce_match.group(1) if nonce_match else 'N/A'}")
    print(f"  challenge: {challenge_match.group(1) if challenge_match else 'N/A'}")
    
    # Step 2: Try mobile version
    r2 = sess_av.get(f"https://m.avito.ru/ad/{avito_id}", timeout=TIMEOUT)
    print(f"  [av_mobile] {r2.status_code} | {len(r2.content)}B")
    avito_results["mobile"] = {"status": r2.status_code, "size": len(r2.content)}
    
    # Step 3: Try desktop with /ad/ path
    r3 = sess_av.get(f"https://www.avito.ru/ad/{avito_id}", timeout=TIMEOUT)
    print(f"  [av_desktop_ad] {r3.status_code} | {len(r3.content)}B")
    avito_results["desktop_ad"] = {"status": r3.status_code, "size": len(r3.content)}
    
    # Step 4: Try with city prefix
    r4 = sess_av.get(f"https://www.avito.ru/moscow/all_avito/{avito_id}", timeout=TIMEOUT)
    print(f"  [av_city] {r4.status_code} | {len(r4.content)}B")
    avito_results["city"] = {"status": r4.status_code, "size": len(r4.content)}
    
except Exception as e:
    print(f"  [avito] ERR: {e}")
    traceback.print_exc()

# curl_cffi for Avito
if HAS_CFFI:
    try:
        status, size, resp = cffi_get(f"https://www.avito.ru/ad/{avito_id}", HEADERS, "cffi_avito")
        body_preview = ""
        if hasattr(resp, 'text') and size > 0:
            body_preview = resp.text[:200].replace('\n', ' ')
        print(f"  [cffi_avito] {status} | {size}B | {body_preview[:150]}")
        avito_results["cffi"] = {"status": status, "size": size}
    except Exception as e:
        print(f"  [cffi_avito] ERR: {e}")

results["AVITO"] = avito_results
print(f"  AVITO section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 4. FARPOST — URL patterns
# ======================================================================
print("\n" + "=" * 70)
print(f"4. FARPOST [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

fp_id = "18530297982"
fp_results = {}

# Different URL patterns for Farpost items
urls_fp = [
    ("item", f"https://www.farpost.ru/item/{fp_id}"),
    ("ads", f"https://www.farpost.ru/ads/{fp_id}"),
    ("ad", f"https://www.farpost.ru/ad/{fp_id}"),
    ("listing", f"https://www.farpost.ru/listing/{fp_id}"),
    ("offer", f"https://www.farpost.ru/offer/{fp_id}"),
    ("category_telefony", f"https://www.farpost.ru/geo/telefony/"),
    ("category_noutbuki", f"https://www.farpost.ru/geo/noutbuki/"),
    ("search", f"https://www.farpost.ru/search/?q={fp_id}"),
]

sess_fp = std_req.Session()
sess_fp.headers.update(HEADERS)

for name, url in urls_fp:
    status, size, resp = safe_req(sess_fp, url, None, name)
    body_preview = ""
    has_jsonld = False
    has_og = False
    if hasattr(resp, 'text') and size > 0:
        body_preview = resp.text[:150].replace('\n', ' ')
        has_jsonld = 'application/ld+json' in resp.text
        has_og = 'og:title' in resp.text
    print(f"  [{name}] {status} | {size}B | JSONLD={has_jsonld} OG={has_og} | {body_preview[:100]}")
    fp_results[name] = {"status": status, "size": size, "jsonld": has_jsonld, "og": has_og}
    if rem() < 15:
        break

# Try a known Farpost item from category page
try:
    r_cat = sess_fp.get("https://www.farpost.ru/geo/telefony/", timeout=TIMEOUT)
    if r_cat.status_code == 200:
        # Find item links
        links = re.findall(r'href="(/geo/[^"]+/\d+)"', r_cat.text)
        if not links:
            links = re.findall(r'href="([^"]*farpost\.ru[^"]*/\d+)"', r_cat.text)
        if not links:
            links = re.findall(r'href="([^"]+)"[^>]*class="[^"]*item[^"]*"', r_cat.text)
        print(f"  [cat_links] found {len(links)} links")
        if links:
            sample = links[0]
            if sample.startswith('/'):
                sample = f"https://www.farpost.ru{sample}"
            print(f"  [cat_sample] {sample}")
            r_item = sess_fp.get(sample, timeout=TIMEOUT)
            has_jsonld = 'application/ld+json' in r_item.text
            has_og = 'og:title' in r_item.text
            print(f"  [cat_item] {r_item.status_code} | {len(r_item.content)}B | JSONLD={has_jsonld} OG={has_og}")
            fp_results["cat_item"] = {"status": r_item.status_code, "size": len(r_item.content), "url": sample}
except Exception as e:
    print(f"  [fp_cat] ERR: {e}")

results["FARPOST"] = fp_results
print(f"  FARPOST section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 5. PROXY SOURCES — additional
# ======================================================================
print("\n" + "=" * 70)
print(f"5. PROXY SOURCES [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

proxy_results = {}
proxy_sources = [
    ("proxyscrape_all", "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=text&timeout=10000&country=ru"),
    ("proxyscrape_v2", "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=text&country=ru"),
    ("spyscraper", "https://api.spyscraper.com/v3/?country=ru&protocol=http&format=text&minPing=0"),
    ("geonode", "https://proxylist.geonode.com/api/proxy-list?limit=50&page=1&sort_by=lastChecked&protocol=http&type=http&country=RU"),
]

for name, url in proxy_sources:
    try:
        r = std_req.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        lines = [l.strip() for l in r.text.splitlines() if l.strip() and not l.startswith('#')]
        # Filter for ip:port format
        proxies = [l for l in lines if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', l)]
        print(f"  [{name}] {r.status_code} | {len(lines)} lines | {len(proxies)} valid proxies")
        proxy_results[name] = {"status": r.status_code, "count": len(proxies), "sample": proxies[:3]}
        if proxies:
            # Quick test first 5 against farpost (not google)
            for i, p in enumerate(proxies[:5]):
                try:
                    pr = {"http": f"http://{p}", "https": f"http://{p}"}
                    tr = std_req.get("https://www.farpost.ru/", proxies=pr, timeout=8, headers=HEADERS)
                    ok = tr.status_code in (200, 301, 302)
                    print(f"    proxy[{i}] {p} -> {tr.status_code} {'OK' if ok else 'FAIL'}")
                    if ok:
                        proxy_results["working"] = proxy_results.get("working", [])
                        proxy_results["working"].append(p)
                except Exception as e:
                    print(f"    proxy[{i}] {p} -> ERR {str(e)[:60]}")
                if rem() < 15:
                    break
    except Exception as e:
        print(f"  [{name}] ERR: {str(e)[:80]}")
        proxy_results[name] = {"status": -1, "error": str(e)[:80]}
    if rem() < 10:
        break

results["PROXY"] = proxy_results
print(f"  PROXY section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# SUMMARY
# ======================================================================
elapsed = time.monotonic() - t0
print("\n" + "=" * 70)
print(f"SUMMARY [{elapsed:.1f}s]")
print("=" * 70)

for src in ["WB", "OZON", "AVITO", "FARPOST"]:
    data = results.get(src, {})
    successes = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("status") == 200}
    print(f"  {src}: {len(data)} tests, {len(successes)} HTTP-200")
    for k, v in successes.items():
        print(f"    ✓ {k}: {v.get('size', 0)}B")

working_proxies = results.get("PROXY", {}).get("working", [])
print(f"  PROXY working: {len(working_proxies)}")

# Save full results
out_path = "diag_v20_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\nResults saved to {out_path}")
print(f"DONE diag_v20 [{elapsed:.1f}s]")
