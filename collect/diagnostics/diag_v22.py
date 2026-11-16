"""Диагностика v22: сжатая, без DNS-прешека, CDN с 5s таймаутом.

Фокус:
- WB: CDN basket-199{0-9} с коротким таймаутом + card.wb.ru
- Ozon: session flow (home → cookies → product), api.ozon.ru
- Avito: /ad/{id}, /p/{id}, m.avito.ru, поиск активного ID
- Farpost: homepage → geo-категория → извлечь реальный item
- Прокси: monosans/proxy-list + proxyscrape, тест через farpost
"""

import json
import logging
import re
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diag_v22")

TIMEOUT = 8
CDN_TIMEOUT = 5
TOTAL_BUDGET = 200

t0 = time.monotonic()
results = {}

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

import requests as std_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {
    "User-Agent": UA,
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
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}

def rem():
    return max(0, TOTAL_BUDGET - (time.monotonic() - t0))

def get(sess, url, hdrs=None, t=TIMEOUT, name=""):
    try:
        r = sess.get(url, headers=hdrs or H, timeout=t, allow_redirects=True)
        return r.status_code, len(r.content), r
    except Exception as e:
        return -1, 0, str(e)

def cffi_get(url, hdrs=None, t=TIMEOUT, imp="chrome"):
    try:
        s = cffi_req.Session(impersonate=imp)
        r = s.get(url, headers=hdrs or H, timeout=t)
        return r.status_code, len(r.content), r
    except Exception as e:
        return -1, 0, str(e)

# ======================================================================
# 1. WILDBERRIES
# ======================================================================
print("=" * 70)
print(f"1. WILDBERRIES [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

wb_id = "199298871"
vol = int(wb_id) // 1000000       # 199
part = int(wb_id) // 1000         # 199298
wb_res = {}

sess = std_req.Session()
sess.headers.update(H_API)

# 1a. card.wb.ru (know it's 403, quick confirm)
st, sz, r = get(sess, f"https://card.wb.ru/cards/v2/detail?nm={wb_id}", H_API, 6)
print(f"  [card_v2] {st} | {sz}B")
wb_res["card_v2"] = {"status": st, "size": sz}

# 1b. CDN scan: basket-{vol}{0-9}.wbbasket.ru (5s timeout each)
print("  --- CDN scan (basket) ---")
cdn_hit = None
for sfx in range(10):
    if rem() < 30:
        break
    host = f"basket-{vol}{sfx}.wbbasket.ru"
    url_info = f"https://{host}/vol{vol}/part{part}/{wb_id}/info/{wb_id}-info.json"
    st, sz, r = get(sess, url_info, H_API, CDN_TIMEOUT)
    mark = "HIT" if st == 200 and sz > 50 else ""
    print(f"    {host:35s} -> {st} | {sz}B {mark}")
    wb_res[f"cdn_b_{sfx}"] = {"status": st, "size": sz, "host": host}
    if st == 200 and sz > 50:
        cdn_hit = host
        break

# 1c. CDN scan: static-basket-{vol}{0-9}
if not cdn_hit:
    print("  --- CDN scan (static-basket) ---")
    for sfx in range(10):
        if rem() < 25:
            break
        host = f"static-basket-{vol}{sfx}.wbbasket.ru"
        url_info = f"https://{host}/vol{vol}/part{part}/{wb_id}/info/{wb_id}-info.json"
        st, sz, r = get(sess, url_info, H_API, CDN_TIMEOUT)
        mark = "HIT" if st == 200 and sz > 50 else ""
        print(f"    {host:35s} -> {st} | {sz}B {mark}")
        wb_res[f"cdn_sb_{sfx}"] = {"status": st, "size": sz, "host": host}
        if st == 200 and sz > 50:
            cdn_hit = host
            break

# 1d. If CDN hit, try image
if cdn_hit:
    # Extract vol/sfx from host
    m = re.search(r'(?:static-)?basket-(\d+)(\d)\.wbbasket', cdn_hit)
    if m:
        v2, s2 = m.group(1), m.group(2)
        url_img = f"https://{cdn_hit}/vol{v2}/part{part}/{wb_id}/images1/{wb_id}-1.jpg"
        st, sz, r = get(sess, url_img, H_API, CDN_TIMEOUT)
        print(f"  [IMG] {st} | {sz}B")
        wb_res["img"] = {"status": st, "size": sz}

# 1e. Try a different (smaller) product for CDN
wb_id2 = "142770354"
vol2 = int(wb_id2) // 1000000  # 142
part2 = int(wb_id2) // 1000    # 142770
print(f"  --- CDN alt product {wb_id2} (vol={vol2}, part={part2}) ---")
for sfx in range(5):
    if rem() < 20:
        break
    host = f"basket-{vol2}{sfx}.wbbasket.ru"
    url_info = f"https://{host}/vol{vol2}/part{part2}/{wb_id2}/info/{wb_id2}-info.json"
    st, sz, r = get(sess, url_info, H_API, CDN_TIMEOUT)
    mark = "HIT" if st == 200 and sz > 50 else ""
    print(f"    {host:35s} -> {st} | {sz}B {mark}")
    wb_res[f"cdn_alt_{sfx}"] = {"status": st, "size": sz, "host": host}
    if st == 200 and sz > 50:
        break

results["WB"] = wb_res
print(f"  WB: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 2. OZON
# ======================================================================
print("=" * 70)
print(f"2. OZON [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

oz_id = "1600562267"
oz_res = {}

# 2a. Session flow: home → product
sess_oz = std_req.Session()
sess_oz.headers.update(H)

try:
    st, sz, r = get(sess_oz, "https://www.ozon.ru/", H, 8)
    cks = list(sess_oz.cookies.keys())
    print(f"  [home] {st} | {sz}B | ck={cks}")
    oz_res["home"] = {"status": st, "size": sz, "cookies": cks}

    time.sleep(1)

    st, sz, r = get(sess_oz, f"https://www.ozon.ru/product/{oz_id}/", H, 8)
    has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
    has_og = 'og:title' in r.text if hasattr(r, 'text') and sz > 0 else False
    print(f"  [product] {st} | {sz}B | JSONLD={has_ld} OG={has_og}")
    oz_res["product"] = {"status": st, "size": sz, "jsonld": has_ld, "og": has_og}

    time.sleep(1)

    st, sz, r = get(sess_oz,
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oz_id}/",
        {"Accept": "application/json"}, 8)
    print(f"  [composer] {st} | {sz}B")
    oz_res["composer"] = {"status": st, "size": sz}
except Exception as e:
    print(f"  [ozon] ERR: {e}")

# 2b. api.ozon.ru
try:
    st, sz, r = get(sess_oz,
        f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oz_id}/",
        {"Accept": "application/json"}, 8)
    print(f"  [api.ozon.ru] {st} | {sz}B")
    oz_res["api_sub"] = {"status": st, "size": sz}
except Exception as e:
    print(f"  [api_sub] ERR: {str(e)[:60]}")

# 2c. curl_cffi
if HAS_CFFI:
    try:
        st, sz, r = cffi_get(f"https://www.ozon.ru/product/{oz_id}/", H, 8)
        has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
        print(f"  [cffi_product] {st} | {sz}B | JSONLD={has_ld}")
        oz_res["cffi"] = {"status": st, "size": sz, "jsonld": has_ld}
    except Exception as e:
        print(f"  [cffi_oz] ERR: {str(e)[:60]}")

# 2d. Mobile UA
try:
    h_mob = dict(H)
    h_mob["User-Agent"] = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
    h_mob["sec-ch-ua-mobile"] = "?1"
    st, sz, r = get(sess_oz, f"https://www.ozon.ru/product/{oz_id}/", h_mob, 8)
    print(f"  [mobile] {st} | {sz}B")
    oz_res["mobile"] = {"status": st, "size": sz}
except Exception as e:
    print(f"  [mob] ERR: {str(e)[:60]}")

results["OZON"] = oz_res
print(f"  OZON: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 3. AVITO
# ======================================================================
print("=" * 70)
print(f"3. AVITO [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

av_id = "1096783029"
av_res = {}
sess_av = std_req.Session()
sess_av.headers.update(H)

# 3a. Home for PoW
try:
    st, sz, r = get(sess_av, "https://www.avito.ru/", H, 8)
    cks = list(sess_av.cookies.keys())
    print(f"  [home] {st} | {sz}B | ck={cks}")
    av_res["home"] = {"status": st, "size": sz, "cookies": cks}

    # Extract PoW
    html = r.text if hasattr(r, 'text') else ""
    pow_m = re.search(r'startPow\((\d+)\)', html)
    print(f"  PoW: {pow_m.group(1) if pow_m else 'N/A'}")
except Exception as e:
    print(f"  [av_home] ERR: {e}")

# 3b. URL patterns
av_urls = [
    ("ad", f"https://www.avito.ru/ad/{av_id}"),
    ("p", f"https://www.avito.ru/p/{av_id}"),
    ("m_ad", f"https://m.avito.ru/ad/{av_id}"),
    ("m_p", f"https://m.avito.ru/p/{av_id}"),
    ("moscow", f"https://www.avito.ru/moscow/all_avito/{av_id}"),
]
for name, url in av_urls:
    if rem() < 25:
        break
    st, sz, r = get(sess_av, url, H, 8)
    has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
    has_og = 'og:title' in r.text if hasattr(r, 'text') and sz > 0 else False
    print(f"  [{name}] {st} | {sz}B | JSONLD={has_ld} OG={has_og}")
    av_res[name] = {"status": st, "size": sz, "jsonld": has_ld, "og": has_og}

# 3c. Search for active ads
try:
    st, sz, r = get(sess_av, "https://www.avito.ru/moscow/search?query=iphone", H, 8)
    print(f"  [search] {st} | {sz}B")
    av_res["search"] = {"status": st, "size": sz}
    if st == 200 and sz > 5000:
        links = re.findall(r'href="([^"]*(?:/ad/|/p/)\d+)"', r.text)
        if not links:
            links = re.findall(r'href="(/[^"]*/\d{8,})"', r.text)
        print(f"  [search_links] {len(links)} found")
        if links:
            sample = links[0]
            if sample.startswith('/'):
                sample = f"https://www.avito.ru{sample}"
            print(f"  [sample] {sample}")
            st2, sz2, r2 = get(sess_av, sample, H, 8)
            has_ld = 'application/ld+json' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            print(f"  [sample_ad] {st2} | {sz2}B | JSONLD={has_ld}")
            av_res["sample_ad"] = {"status": st2, "size": sz2, "url": sample, "jsonld": has_ld}
except Exception as e:
    print(f"  [av_search] ERR: {str(e)[:60]}")

# 3d. curl_cffi
if HAS_CFFI:
    try:
        st, sz, r = cffi_get(f"https://www.avito.ru/ad/{av_id}", H, 8)
        print(f"  [cffi_ad] {st} | {sz}B")
        av_res["cffi"] = {"status": st, "size": sz}
    except Exception as e:
        print(f"  [cffi_av] ERR: {str(e)[:60]}")

results["AVITO"] = av_res
print(f"  AVITO: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 4. FARPOST
# ======================================================================
print("=" * 70)
print(f"4. FARPOST [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

fp_id = "18530297982"
fp_res = {}
sess_fp = std_req.Session()
sess_fp.headers.update(H)

# 4a. Homepage
try:
    st, sz, r = get(sess_fp, "https://www.farpost.ru/", H, 8)
    cks = list(sess_fp.cookies.keys())
    print(f"  [home] {st} | {sz}B | ck={cks}")
    fp_res["home"] = {"status": st, "size": sz, "cookies": cks}
except Exception as e:
    print(f"  [fp_home] ERR: {e}")

# 4b. Geo categories (from v17 that worked)
fp_cats = [
    ("geo_telefony", "https://www.farpost.ru/geo/telefony/"),
    ("geo_noutbuki", "https://www.farpost.ru/geo/noutbuki/"),
    ("geo_all", "https://www.farpost.ru/geo/"),
]
for name, url in fp_cats:
    if rem() < 25:
        break
    st, sz, r = get(sess_fp, url, H, 8)
    has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
    has_og = 'og:title' in r.text if hasattr(r, 'text') and sz > 0 else False
    has_altcha = 'altcha' in r.text.lower() if hasattr(r, 'text') else False
    print(f"  [{name}] {st} | {sz}B | JSONLD={has_ld} OG={has_og} ALTCHA={has_altcha}")
    fp_res[name] = {"status": st, "size": sz, "jsonld": has_ld, "og": has_og, "altcha": has_altcha}

# 4c. Extract real item from category
try:
    st, sz, r = get(sess_fp, "https://www.farpost.ru/geo/telefony/", H, 8)
    if st == 200 and sz > 5000:
        # Try multiple patterns
        patterns = [
            r'href="(/geo/[^"]+/\d{5,})"',
            r'href="(/item/\d+)"',
            r'href="(/ads/\d+)"',
            r'"href"\s*:\s*"(/[^"]*/\d+)"',
            r'data-url="([^"]*/\d+)"',
        ]
        found = []
        for pat in patterns:
            found = re.findall(pat, r.text)
            if found:
                print(f"  [cat_links] pattern='{pat[:30]}' -> {len(found)}")
                break
        if found:
            sample = found[0]
            if sample.startswith('/'):
                sample = f"https://www.farpost.ru{sample}"
            print(f"  [item] {sample}")
            st2, sz2, r2 = get(sess_fp, sample, H, 8)
            has_ld = 'application/ld+json' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            has_og = 'og:title' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            # Extract og:title
            og_title = ""
            if hasattr(r2, 'text'):
                m = re.search(r'og:title["\s]+content=["\']([^"\']+)', r2.text)
                if m:
                    og_title = m.group(1)[:80]
            print(f"  [item_resp] {st2} | {sz2}B | JSONLD={has_ld} OG={has_og} title='{og_title}'")
            fp_res["item"] = {"status": st2, "size": sz2, "url": sample, "jsonld": has_ld, "og": has_og, "title": og_title}
    else:
        print(f"  [cat_for_item] {st} | {sz}B (not 200 or small)")
except Exception as e:
    print(f"  [fp_item] ERR: {str(e)[:60]}")

# 4d. Direct item URLs
fp_items = [
    ("item_geo", f"https://www.farpost.ru/geo/item/{fp_id}"),
    ("item_direct", f"https://www.farpost.ru/item/{fp_id}"),
]
for name, url in fp_items:
    if rem() < 15:
        break
    st, sz, r = get(sess_fp, url, H, 8)
    print(f"  [{name}] {st} | {sz}B")
    fp_res[name] = {"status": st, "size": sz}

results["FARPOST"] = fp_res
print(f"  FARPOST: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 5. PROXY SOURCES
# ======================================================================
print("=" * 70)
print(f"5. PROXY [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

px_res = {}
all_px = set()

# Source 1: monosans/proxy-list (GitHub)
try:
    r = std_req.get("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
                    timeout=TIMEOUT, headers={"User-Agent": UA})
    lines = [l.strip() for l in r.text.splitlines() if l.strip() and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', l.strip())]
    print(f"  [monosans_http] {r.status_code} | {len(lines)} proxies")
    px_res["monosans"] = {"status": r.status_code, "count": len(lines)}
    all_px.update(lines[:50])
except Exception as e:
    print(f"  [monosans] ERR: {str(e)[:60]}")
    px_res["monosans"] = {"status": -1, "error": str(e)[:60]}

# Source 2: proxyscrape v4
try:
    r = std_req.get("https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=text&timeout=10000&country=ru",
                    timeout=TIMEOUT, headers={"User-Agent": UA})
    lines = [l.strip() for l in r.text.splitlines() if l.strip()]
    # Parse: "ip:port" or "http://ip:port"
    px = set()
    for l in lines:
        c = l.replace("http://", "").replace("https://", "").strip()
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', c):
            px.add(c)
    print(f"  [proxyscrape] {r.status_code} | {len(lines)} lines | {len(px)} proxies")
    px_res["proxyscrape"] = {"status": r.status_code, "count": len(px)}
    all_px.update(list(px)[:50])
except Exception as e:
    print(f"  [proxyscrape] ERR: {str(e)[:60]}")
    px_res["proxyscrape"] = {"status": -1, "error": str(e)[:60]}

print(f"  Total unique: {len(all_px)}")

# Test top 8 against farpost
working = []
for i, p in enumerate(list(all_px)[:8]):
    if rem() < 12:
        break
    try:
        pr = {"http": f"http://{p}", "https": f"http://{p}"}
        tr = std_req.get("https://www.farpost.ru/", proxies=pr, timeout=6, headers=H)
        ok = tr.status_code in (200, 301, 302)
        print(f"    [{i}] {p:25s} -> {tr.status_code} {'OK' if ok else 'FAIL'}")
        if ok:
            working.append(p)
    except Exception as e:
        print(f"    [{i}] {p:25s} -> ERR {str(e)[:40]}")

px_res["total"] = len(all_px)
px_res["working"] = working
results["PROXY"] = px_res
print(f"  PROXY: {time.monotonic()-sec:.1f}s")

# ======================================================================
# SUMMARY
# ======================================================================
elapsed = time.monotonic() - t0
print("\n" + "=" * 70)
print(f"SUMMARY [{elapsed:.1f}s]")
print("=" * 70)
for src in ["WB", "OZON", "AVITO", "FARPOST"]:
    data = results.get(src, {})
    ok = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("status") == 200}
    print(f"  {src}: {len(data)} tests, {len(ok)} x 200")
    for k, v in ok.items():
        print(f"    OK {k}: {v.get('size', 0)}B")
print(f"  PROXY working: {len(results.get('PROXY', {}).get('working', []))}")

with open("diag_v22_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\nDONE diag_v22 [{elapsed:.1f}s]")
