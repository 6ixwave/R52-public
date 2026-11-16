"""Диагностика v21: точечная проверка после анализа v20.

Исправления:
- WB: basket-XX (не static-basket), card.wb.ru с x-wb-заголовками, DNS-тест
- Ozon: полный session-flow, api.ozon.ru, mobile UA
- Avito: проверка актуальности ID, /p/ pattern, PoW с cookie
- Farpost: точные URL из v17, www vs bare, geo-категории
- Прокси: фикс формата, больше источников, тест через farpost
"""

import json
import logging
import re
import socket
import sys
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diag_v21")

TIMEOUT = 12
TOTAL_BUDGET = 240  # секунд

t0 = time.monotonic()
results = {}

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    log.info("curl_cffi available")
except ImportError:
    HAS_CFFI = False
    log.warning("curl_cffi NOT available")

import requests as std_req

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
UA_MOBILE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
UA_FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"

H_BASE = {
    "User-Agent": UA_CHROME,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A_Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

H_API = {
    "User-Agent": UA_CHROME,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}

def rem():
    return max(0, TOTAL_BUDGET - (time.monotonic() - t0))

def safe_get(session, url, headers=None, name=""):
    try:
        h = headers or H_BASE
        r = session.get(url, headers=h, timeout=TIMEOUT, allow_redirects=True)
        return r.status_code, len(r.content), r
    except Exception as e:
        return -1, 0, str(e)

def cffi_get(url, headers=None, impersonate="chrome"):
    try:
        s = cffi_req.Session(impersonate=impersonate)
        h = headers or H_BASE
        r = s.get(url, headers=h, timeout=TIMEOUT)
        return r.status_code, len(r.content), r
    except Exception as e:
        return -1, 0, str(e)

def dns_test(host):
    """Быстрая проверка DNS."""
    try:
        ip = socket.gethostbyname(host)
        return ip
    except Exception as e:
        return f"ERR:{str(e)[:40]}"

# ======================================================================
# 0. DNS / CDN connectivity pre-check
# ======================================================================
print("\n" + "=" * 70)
print(f"0. DNS PRE-CHECK [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

dns_targets = [
    "card.wb.ru",
    "static-basket-199.wbbasket.ru",
    "basket-199.wbbasket.ru",
    "static-basket-142.wbbasket.ru",
    "basket-142.wbbasket.ru",
    "www.ozon.ru",
    "api.ozon.ru",
    "www.avito.ru",
    "m.avito.ru",
    "www.farpost.ru",
    "farpost.ru",
]

dns_results = {}
for host in dns_targets:
    ip = dns_test(host)
    print(f"  {host:45s} -> {ip}")
    dns_results[host] = ip

results["DNS"] = dns_results
print(f"  DNS section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 1. WILDBERRIES
# ======================================================================
print("\n" + "=" * 70)
print(f"1. WILDBERRIES [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

wb_id = "199298871"
vol = int(wb_id) // 1000000  # 199
part = (int(wb_id) // 1000) % 1000000  # 1992988
wb_results = {}

sess_wb = std_req.Session()
sess_wb.headers.update(H_API)

# 1a. card.wb.ru with extra headers
wb_urls = [
    ("card_v2_basic", f"https://card.wb.ru/cards/v2/detail?nm={wb_id}"),
    ("card_v2_full", f"https://card.wb.ru/cards/v2/detail?nm={wb_id}&appType=1&curr=rub&dest=-1257786&hideGrouping=false"),
    ("card_v2_spu", f"https://card.wb.ru/cards/v2/detail?nm={wb_id}&supp=1"),
    ("card_main_dom", f"https://www.wildberries.ru/cards/v2/detail?nm={wb_id}"),
]

# Extra WB headers
H_WB = dict(H_API)
H_WB["x-cdn-version"] = "1"
H_WB["x-wb-version"] = "1"
H_WB["x-requested-with"] = "XMLHttpRequest"

for name, url in wb_urls:
    status, size, resp = safe_get(sess_wb, url, H_WB, name)
    preview = ""
    if hasattr(resp, 'text') and size > 0:
        preview = resp.text[:200].replace('\n', ' ')
    print(f"  [{name}] {status} | {size}B | {preview[:150]}")
    wb_results[name] = {"status": status, "size": size}
    if rem() < 25:
        break

# 1b. curl_cffi with full chrome
if HAS_CFFI:
    for name, url in [("cffi_card", wb_urls[0][1]), ("cffi_card_full", wb_urls[1][1])]:
        status, size, resp = cffi_get(url, H_WB)
        preview = ""
        if hasattr(resp, 'text') and size > 0:
            preview = resp.text[:200].replace('\n', ' ')
        print(f"  [{name}] {status} | {size}B | {preview[:150]}")
        wb_results[name] = {"status": status, "size": size}
        if rem() < 20:
            break

# 1c. CDN: basket-XX and static-basket-XX (both prefixes)
print("  --- CDN scan ---")
cdn_hits = []
for prefix in ["static-basket", "basket"]:
    for suffix in range(10):
        host = f"{prefix}-{vol}{suffix}.wbbasket.ru"
        ip = dns_test(host)
        if ip.startswith("ERR"):
            continue
        url_cdn = f"https://{host}/vol{vol}/part{part}/{wb_id}/info/{wb_id}-info.json"
        status, size, resp = safe_get(sess_wb, url_cdn, H_API, f"cdn_{prefix[-3:]}_{suffix}")
        if status == 200 and size > 50:
            print(f"  [CDN HIT] {host} suffix={suffix} -> {status} {size}B")
            wb_results[f"cdn_{prefix}_{suffix}"] = {"status": status, "size": size, "host": host}
            cdn_hits.append(host)
            break
        if rem() < 20:
            break
    if cdn_hits:
        break
    if rem() < 15:
        break

# 1d. Try images CDN (basket-XX) for a known pattern
# Image URL: https://basket-{vol}{s}.wbbasket.ru/vol{vol}/part{part}/{nm}/images1/{nm}-1.jpg
for suffix in range(10):
    host = f"basket-{vol}{suffix}.wbbasket.ru"
    ip = dns_test(host)
    if ip.startswith("ERR"):
        continue
    url_img = f"https://{host}/vol{vol}/part{part}/{wb_id}/images1/{wb_id}-1.jpg"
    status, size, resp = safe_get(sess_wb, url_img, H_API, f"img_{suffix}")
    if status == 200 and size > 1000:
        print(f"  [IMG HIT] {host} -> {status} {size}B")
        wb_results[f"img_{suffix}"] = {"status": status, "size": size, "host": host}
        break
    if rem() < 15:
        break

# 1e. Try a definitely-active small product
wb_id2 = "1"
vol2 = int(wb_id2) // 1000000 or 1
part2 = (int(wb_id2) // 1000) % 1000000 or 1
url2 = f"https://card.wb.ru/cards/v2/detail?nm={wb_id2}"
status, size, resp = safe_get(sess_wb, url2, H_WB, "card_nm1")
preview = ""
if hasattr(resp, 'text') and size > 0:
    preview = resp.text[:200].replace('\n', ' ')
print(f"  [card_nm1] {status} | {size}B | {preview[:150]}")
wb_results["card_nm1"] = {"status": status, "size": size}

results["WB"] = wb_results
print(f"  WB section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 2. OZON
# ======================================================================
print("\n" + "=" * 70)
print(f"2. OZON [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

ozon_id = "1600562267"
ozon_results = {}

# 2a. Full session: home -> cookies -> product
sess_oz = std_req.Session()
sess_oz.headers.update(H_BASE)

try:
    r_home = sess_oz.get("https://www.ozon.ru/", timeout=TIMEOUT)
    cookies = dict(sess_oz.cookies)
    print(f"  [home] {r_home.status_code} | {len(r_home.content)}B | cookies: {list(cookies.keys())}")
    ozon_results["home"] = {"status": r_home.status_code, "size": len(r_home.content), "cookies": list(cookies.keys())}

    # Wait a bit
    time.sleep(1)

    r_prod = sess_oz.get(f"https://www.ozon.ru/product/{ozon_id}/", timeout=TIMEOUT)
    print(f"  [product] {r_prod.status_code} | {len(r_prod.content)}B")
    ozon_results["product"] = {"status": r_prod.status_code, "size": len(r_prod.content)}

    # Try composer API with session cookies
    r_comp = sess_oz.get(
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{ozon_id}/",
        headers={"Accept": "application/json, text/plain, */*"},
        timeout=TIMEOUT
    )
    print(f"  [composer] {r_comp.status_code} | {len(r_comp.content)}B")
    ozon_results["composer"] = {"status": r_comp.status_code, "size": len(r_comp.content)}

except Exception as e:
    print(f"  [ozon_session] ERR: {e}")

# 2b. curl_cffi with full chrome impersonation
if HAS_CFFI:
    try:
        s_cffi = cffi_req.Session(impersonate="chrome")
        # Visit home first
        r_h = s_cffi.get("https://www.ozon.ru/", timeout=TIMEOUT)
        print(f"  [cffi_home] {r_h.status_code} | {len(r_h.content)}B")
        ozon_results["cffi_home"] = {"status": r_h.status_code, "size": len(r_h.content)}

        time.sleep(1)

        r_p = s_cffi.get(f"https://www.ozon.ru/product/{ozon_id}/", timeout=TIMEOUT)
        print(f"  [cffi_product] {r_p.status_code} | {len(r_p.content)}B")
        ozon_results["cffi_product"] = {"status": r_p.status_code, "size": len(r_p.content)}

        # Check if HTML has useful data
        if r_p.status_code == 200 and len(r_p.content) > 5000:
            html = r_p.text
            has_jsonld = 'application/ld+json' in html
            has_og = 'og:title' in html
            has_state = '__INITIAL_STATE__' in html or '__NEXT_DATA__' in html
            print(f"    JSONLD={has_jsonld} OG={has_og} STATE={has_state}")
            ozon_results["cffi_product"]["jsonld"] = has_jsonld
            ozon_results["cffi_product"]["og"] = has_og
            ozon_results["cffi_product"]["state"] = has_state
    except Exception as e:
        print(f"  [cffi_ozon] ERR: {e}")

# 2c. Try api.ozon.ru directly
try:
    s_api = std_req.Session()
    s_api.headers.update(H_API)
    r_api = s_api.get(
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{ozon_id}/",
        timeout=TIMEOUT
    )
    print(f"  [api.ozon.ru] {r_api.status_code} | {len(r_api.content)}B")
    ozon_results["api_subdomain"] = {"status": r_api.status_code, "size": len(r_api.content)}
except Exception as e:
    print(f"  [api_ozon] ERR: {str(e)[:80]}")

# 2d. Mobile UA
try:
    s_mob = std_req.Session()
    h_mob = dict(H_BASE)
    h_mob["User-Agent"] = UA_MOBILE
    h_mob["sec-ch-ua-mobile"] = "?1"
    r_mob = s_mob.get(f"https://www.ozon.ru/product/{ozon_id}/", headers=h_mob, timeout=TIMEOUT)
    print(f"  [mobile] {r_mob.status_code} | {len(r_mob.content)}B")
    ozon_results["mobile"] = {"status": r_mob.status_code, "size": len(r_mob.content)}
except Exception as e:
    print(f"  [ozon_mob] ERR: {str(e)[:80]}")

results["OZON"] = ozon_results
print(f"  OZON section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 3. AVITO
# ======================================================================
print("\n" + "=" * 70)
print(f"3. AVITO [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

avito_id = "1096783029"
avito_results = {}

sess_av = std_req.Session()
sess_av.headers.update(H_BASE)

# 3a. Home page for PoW
try:
    r_home = sess_av.get("https://www.avito.ru/", timeout=TIMEOUT)
    print(f"  [home] {r_home.status_code} | {len(r_home.content)}B")
    avito_results["home"] = {"status": r_home.status_code, "size": len(r_home.content)}

    html_h = r_home.text
    # Extract PoW params
    pow_m = re.search(r'startPow\((\d+)\)', html_h)
    complexity = int(pow_m.group(1)) if pow_m else 10
    print(f"  PoW complexity: {complexity}")

    # Look for challenge/nonce in scripts
    challenge_m = re.search(r'"challenge"\s*:\s*"([^"]+)"', html_h)
    nonce_m = re.search(r'"nonce"\s*:\s*"([^"]+)"', html_h)
    print(f"  challenge: {challenge_m.group(1) if challenge_m else 'N/A'}")
    print(f"  nonce: {nonce_m.group(1) if nonce_m else 'N/A'}")

    # Check cookies set
    cookies_av = dict(sess_av.cookies)
    print(f"  cookies: {list(cookies_av.keys())}")
    avito_results["cookies"] = list(cookies_av.keys())

except Exception as e:
    print(f"  [av_home] ERR: {e}")

# 3b. Try different URL patterns for the ad
avito_urls = [
    ("ad_short", f"https://www.avito.ru/ad/{avito_id}"),
    ("p_short", f"https://www.avito.ru/p/{avito_id}"),
    ("moscow_all", f"https://www.avito.ru/moscow/all_avito/{avito_id}"),
    ("moscow_auto", f"https://www.avito.ru/moscow/avito/{avito_id}"),
    ("m_ad", f"https://m.avito.ru/ad/{avito_id}"),
    ("m_p", f"https://m.avito.ru/p/{avito_id}"),
]

for name, url in avito_urls:
    status, size, resp = safe_get(sess_av, url, None, name)
    preview = ""
    if hasattr(resp, 'text') and size > 0:
        preview = resp.text[:150].replace('\n', ' ')
    print(f"  [{name}] {status} | {size}B | {preview[:120]}")
    avito_results[name] = {"status": status, "size": size}
    if rem() < 20:
        break

# 3c. curl_cffi for Avito
if HAS_CFFI:
    try:
        status, size, resp = cffi_get(f"https://www.avito.ru/ad/{avito_id}", H_BASE)
        preview = ""
        if hasattr(resp, 'text') and size > 0:
            preview = resp.text[:200].replace('\n', ' ')
        print(f"  [cffi_ad] {status} | {size}B | {preview[:150]}")
        avito_results["cffi_ad"] = {"status": status, "size": size}
    except Exception as e:
        print(f"  [cffi_av] ERR: {e}")

# 3d. Try a known active Avito ad (search for one)
try:
    s_search = std_req.Session()
    s_search.headers.update(H_BASE)
    r_s = s_search.get(f"https://www.avito.ru/moscow/search?query=iphone", timeout=TIMEOUT)
    print(f"  [search] {r_s.status_code} | {len(r_s.content)}B")
    avito_results["search"] = {"status": r_s.status_code, "size": len(r_s.content)}

    # Find ad links
    ad_links = re.findall(r'href="([^"]*avito\.ru[^"]*/\d{8,})"', r_s.text)
    if ad_links:
        sample = ad_links[0]
        if sample.startswith('/'):
            sample = f"https://www.avito.ru{sample}"
        print(f"  [search_sample] {sample}")
        r_ad = s_search.get(sample, timeout=TIMEOUT)
        print(f"  [search_ad] {r_ad.status_code} | {len(r_ad.content)}B")
        avito_results["search_ad"] = {"status": r_ad.status_code, "size": len(r_ad.content), "url": sample}
except Exception as e:
    print(f"  [av_search] ERR: {str(e)[:80]}")

results["AVITO"] = avito_results
print(f"  AVITO section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 4. FARPOST
# ======================================================================
print("\n" + "=" * 70)
print(f"4. FARPOST [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

fp_id = "18530297982"
fp_results = {}

sess_fp = std_req.Session()
sess_fp.headers.update(H_BASE)

# 4a. Homepage first
try:
    r_hp = sess_fp.get("https://www.farpost.ru/", timeout=TIMEOUT)
    print(f"  [homepage] {r_hp.status_code} | {len(r_hp.content)}B")
    fp_results["homepage"] = {"status": r_hp.status_code, "size": len(r_hp.content)}
    cookies_fp = dict(sess_fp.cookies)
    print(f"  cookies: {list(cookies_fp.keys())}")
    fp_results["cookies"] = list(cookies_fp.keys())
except Exception as e:
    print(f"  [fp_home] ERR: {e}")

# 4b. Category pages (from v17 that worked)
fp_cat_urls = [
    ("cat_telefony", "https://www.farpost.ru/geo/telefony/"),
    ("cat_noutbuki", "https://www.farpost.ru/geo/noutbuki/"),
    ("cat_smartfony", "https://www.farpost.ru/geo/smartfony/"),
    ("cat_all", "https://www.farpost.ru/geo/"),
    ("cat_root", "https://farpost.ru/geo/telefony/"),
]

for name, url in fp_cat_urls:
    status, size, resp = safe_get(sess_fp, url, None, name)
    has_jsonld = False
    has_og = False
    preview = ""
    if hasattr(resp, 'text') and size > 0:
        has_jsonld = 'application/ld+json' in resp.text
        has_og = 'og:title' in resp.text
        preview = resp.text[:150].replace('\n', ' ')
    print(f"  [{name}] {status} | {size}B | JSONLD={has_jsonld} OG={has_og} | {preview[:100]}")
    fp_results[name] = {"status": status, "size": size, "jsonld": has_jsonld, "og": has_og}
    if rem() < 20:
        break

# 4c. Try item with different URL patterns
fp_item_urls = [
    ("item_geo", f"https://www.farpost.ru/geo/item/{fp_id}"),
    ("item_ads", f"https://www.farpost.ru/ads/{fp_id}"),
    ("item_direct", f"https://www.farpost.ru/item/{fp_id}"),
    ("item_v2", f"https://www.farpost.ru/v2/item/{fp_id}"),
    ("item_api", f"https://www.farpost.ru/api/item/{fp_id}"),
]

for name, url in fp_item_urls:
    status, size, resp = safe_get(sess_fp, url, None, name)
    preview = ""
    if hasattr(resp, 'text') and size > 0:
        preview = resp.text[:150].replace('\n', ' ')
    print(f"  [{name}] {status} | {size}B | {preview[:100]}")
    fp_results[name] = {"status": status, "size": size}
    if rem() < 15:
        break

# 4d. If category works, extract a real item link
try:
    r_cat = sess_fp.get("https://www.farpost.ru/geo/telefony/", timeout=TIMEOUT)
    if r_cat.status_code == 200 and len(r_cat.content) > 5000:
        # Find item links in various patterns
        patterns = [
            r'href="(/geo/[^"]+/\d{5,})"',
            r'href="(/item/\d+)"',
            r'href="(/ads/\d+)"',
            r'data-href="([^"]*farpost[^"]*/\d+)"',
            r'"url"\s*:\s*"([^"]*farpost[^"]*/\d+)"',
        ]
        found_links = []
        for pat in patterns:
            links = re.findall(pat, r_cat.text)
            if links:
                found_links = links
                print(f"  [cat_links pattern={pat[:30]}] {len(links)} found")
                break

        if found_links:
            sample = found_links[0]
            if sample.startswith('/'):
                sample = f"https://www.farpost.ru{sample}"
            print(f"  [cat_item] {sample}")
            r_item = sess_fp.get(sample, timeout=TIMEOUT)
            has_jsonld = 'application/ld+json' in r_item.text
            has_og = 'og:title' in r_item.text
            print(f"  [cat_item_resp] {r_item.status_code} | {len(r_item.content)}B | JSONLD={has_jsonld} OG={has_og}")
            fp_results["cat_item"] = {"status": r_item.status_code, "size": len(r_item.content), "url": sample}
    else:
        print(f"  [cat_for_item] {r_cat.status_code} (not 200 or too small)")
except Exception as e:
    print(f"  [fp_cat_item] ERR: {str(e)[:80]}")

# 4e. curl_cffi for Farpost
if HAS_CFFI:
    try:
        status, size, resp = cffi_get("https://www.farpost.ru/geo/telefony/")
        preview = ""
        if hasattr(resp, 'text') and size > 0:
            preview = resp.text[:150].replace('\n', ' ')
        print(f"  [cffi_cat] {status} | {size}B | {preview[:100]}")
        fp_results["cffi_cat"] = {"status": status, "size": size}
    except Exception as e:
        print(f"  [cffi_fp] ERR: {e}")

results["FARPOST"] = fp_results
print(f"  FARPOST section: {time.monotonic()-sec_start:.1f}s")

# ======================================================================
# 5. PROXY SOURCES (fixed format)
# ======================================================================
print("\n" + "=" * 70)
print(f"5. PROXY SOURCES [rem={rem():.0f}s]")
print("=" * 70)
sec_start = time.monotonic()

proxy_results = {}
proxy_sources = [
    ("ps_v4", "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=text&timeout=10000&country=ru"),
    ("ps_v3", "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=text&country=ru"),
    ("ps_v4_csv", "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=csv&country=ru"),
    ("geonode", "https://proxylist.geonode.com/api/proxy-list?limit=100&page=1&sort_by=lastChecked&protocol=http&type=http&country=RU"),
]

all_proxies = set()

for name, url in proxy_sources:
    try:
        r = std_req.get(url, timeout=TIMEOUT, headers={"User-Agent": UA_CHROME})
        lines = [l.strip() for l in r.text.splitlines() if l.strip()]
        # Parse: could be "ip:port", "http://ip:port", "ip,port,protocol", CSV
        proxies = set()
        for line in lines:
            # Remove protocol prefix
            clean = line.replace("http://", "").replace("https://", "").replace("socks5://", "")
            # CSV format: ip,port,protocol,country
            if "," in clean:
                parts = clean.split(",")
                if len(parts) >= 2:
                    candidate = f"{parts[0]}:{parts[1]}"
                    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', candidate):
                        proxies.add(candidate)
            elif re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', clean):
                proxies.add(clean)
            # JSON format (geonode)
            elif line.startswith('{') or line.startswith('['):
                try:
                    data = json.loads(line if line.startswith('{') else r.text)
                    if isinstance(data, list):
                        for item in data:
                            ip = item.get("ip", "")
                            port = item.get("port", "")
                            if ip and port:
                                proxies.add(f"{ip}:{port}")
                    elif isinstance(data, dict):
                        items = data.get("items", data.get("data", []))
                        for item in items:
                            ip = item.get("ip", "")
                            port = item.get("port", "")
                            if ip and port:
                                proxies.add(f"{ip}:{port}")
                except json.JSONDecodeError:
                    pass

        print(f"  [{name}] {r.status_code} | {len(lines)} lines | {len(proxies)} proxies")
        proxy_results[name] = {"status": r.status_code, "count": len(proxies), "sample": list(proxies)[:5]}
        all_proxies.update(proxies)
    except Exception as e:
        print(f"  [{name}] ERR: {str(e)[:80]}")
        proxy_results[name] = {"status": -1, "error": str(e)[:80]}
    if rem() < 25:
        break

print(f"  Total unique proxies: {len(all_proxies)}")

# Test top 10 against farpost (lighter than google)
working = []
test_proxies = list(all_proxies)[:10]
for i, p in enumerate(test_proxies):
    if rem() < 10:
        break
    try:
        pr = {"http": f"http://{p}", "https": f"http://{p}"}
        tr = std_req.get("https://www.farpost.ru/", proxies=pr, timeout=8, headers=H_BASE)
        ok = tr.status_code in (200, 301, 302)
        print(f"    [{i}] {p} -> {tr.status_code} {'OK' if ok else 'FAIL'}")
        if ok:
            working.append(p)
    except Exception as e:
        print(f"    [{i}] {p} -> ERR {str(e)[:50]}")

proxy_results["total_unique"] = len(all_proxies)
proxy_results["working"] = working
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
        print(f"    OK {k}: {v.get('size', 0)}B")

print(f"  PROXY working: {len(results.get('PROXY', {}).get('working', []))}")

out_path = "diag_v21_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\nResults saved to {out_path}")
print(f"DONE diag_v21 [{elapsed:.1f}s]")
