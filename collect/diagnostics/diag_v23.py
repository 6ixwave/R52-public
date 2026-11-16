"""Диагностика v23: финальная, с фиксами по v22.

Ключевые изменения:
- WB: DNS-прешек 3 хоста, CDN 10s, HTML 498 → extract state
- Ozon: прокси-тест 5 шт → product через прокси
- Avito: search 200 → save HTML, 8 regex patterns, follow ad
- Farpost: home 200 → extract links, find item URL
- Proxies: 30s budget, 15 тестов, save working
"""

import json
import logging
import re
import socket
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diag_v23")

TIMEOUT = 10
CDN_TIMEOUT = 10
TOTAL_BUDGET = 240

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

def dns_test(host, t=6):
    try:
        ip = socket.gethostbyname(host)
        return ip
    except Exception as e:
        return None

# ======================================================================
# 0. PROXY POOL (do first — needed for Ozon)
# ======================================================================
print("=" * 70)
print(f"0. PROXY POOL [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

px_res = {}
all_px = set()

# Source 1: monosans GitHub
try:
    r = std_req.get(
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        timeout=12, headers={"User-Agent": UA})
    lines = [l.strip() for l in r.text.splitlines()
             if l.strip() and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', l.strip())]
    print(f"  [monosans] {r.status_code} | {len(lines)} proxies")
    px_res["monosans"] = len(lines)
    all_px.update(lines[:80])
except Exception as e:
    print(f"  [monosans] ERR: {str(e)[:60]}")

# Source 2: proxyscrape
try:
    r = std_req.get(
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&proxy_format=protocolipport&format=text&timeout=10000&country=ru",
        timeout=12, headers={"User-Agent": UA})
    px = set()
    for l in r.text.splitlines():
        c = l.strip().replace("http://", "").replace("https://", "")
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', c):
            px.add(c)
    print(f"  [proxyscrape] {r.status_code} | {len(px)} proxies")
    px_res["proxyscrape"] = len(px)
    all_px.update(px)
except Exception as e:
    print(f"  [proxyscrape] ERR: {str(e)[:60]}")

print(f"  Total unique: {len(all_px)}")

# Test 15 proxies against farpost home (lighter than google)
working_px = []
test_list = list(all_px)[:15]
for i, p in enumerate(test_list):
    if rem() < 60:
        break
    try:
        pr = {"http": f"http://{p}", "https": f"http://{p}"}
        tr = std_req.get("https://www.farpost.ru/", proxies=pr, timeout=6, headers=H)
        ok = tr.status_code in (200, 301, 302)
        print(f"    [{i:2d}] {p:25s} -> {tr.status_code} {'OK' if ok else 'FAIL'}")
        if ok:
            working_px.append(p)
    except Exception as e:
        print(f"    [{i:2d}] {p:25s} -> ERR {str(e)[:40]}")

px_res["total"] = len(all_px)
px_res["tested"] = len(test_list)
px_res["working"] = working_px
results["PROXY"] = px_res
print(f"  Working: {len(working_px)}/{len(test_list)}")
print(f"  PROXY: {time.monotonic()-sec:.1f}s")

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

# 1a. card.wb.ru quick check
st, sz, r = get(sess, f"https://card.wb.ru/cards/v2/detail?nm={wb_id}", H_API, 8)
print(f"  [card_v2] {st} | {sz}B")
wb_res["card_v2"] = {"status": st, "size": sz}

# 1b. DNS pre-check on 3 CDN hosts
print("  --- CDN DNS ---")
cdn_hosts = []
for sfx in [0, 3, 7]:
    host = f"basket-{vol}{sfx}.wbbasket.ru"
    ip = dns_test(host)
    print(f"    {host:35s} -> {ip}")
    if ip:
        cdn_hosts.append((host, sfx))
    if rem() < 50:
        break

# 1c. CDN HTTP (only DNS-resolved hosts, 10s timeout)
print("  --- CDN HTTP ---")
cdn_hit = None
for host, sfx in cdn_hosts:
    if rem() < 40:
        break
    url_info = f"https://{host}/vol{vol}/part{part}/{wb_id}/info/{wb_id}-info.json"
    st, sz, r = get(sess, url_info, H_API, CDN_TIMEOUT)
    mark = "HIT" if st == 200 and sz > 50 else ""
    print(f"    {host:35s} -> {st} | {sz}B {mark}")
    wb_res[f"cdn_{sfx}"] = {"status": st, "size": sz, "host": host}
    if st == 200 and sz > 50:
        cdn_hit = host
        # Save info JSON
        try:
            with open("wb_cdn_info.json", "w", encoding="utf-8") as f:
                f.write(r.text[:5000])
            print(f"    Saved wb_cdn_info.json ({min(sz,5000)}B)")
        except Exception:
            pass
        break

# 1d. If no CDN hit, try static-basket
if not cdn_hit:
    for sfx in [0, 5]:
        if rem() < 35:
            break
        host = f"static-basket-{vol}{sfx}.wbbasket.ru"
        ip = dns_test(host)
        if not ip:
            print(f"    static-basket-{vol}{sfx}: DNS fail")
            continue
        url_info = f"https://{host}/vol{vol}/part{part}/{wb_id}/info/{wb_id}-info.json"
        st, sz, r = get(sess, url_info, H_API, CDN_TIMEOUT)
        mark = "HIT" if st == 200 and sz > 50 else ""
        print(f"    {host:35s} -> {st} | {sz}B {mark}")
        wb_res[f"cdn_sb_{sfx}"] = {"status": st, "size": sz, "host": host}
        if st == 200 and sz > 50:
            cdn_hit = host
            break

# 1e. HTML page (498) — extract embedded state
print("  --- HTML 498 extract ---")
st, sz, r = get(sess, f"https://www.wildberries.ru/catalog/{wb_id}/detail.aspx", H, 10)
print(f"  [html] {st} | {sz}B")
wb_res["html"] = {"status": st, "size": sz}
if hasattr(r, 'text') and sz > 500:
    html = r.text
    # Save for inspection
    with open("wb_html_sample.html", "w", encoding="utf-8") as f:
        f.write(html[:10000])
    # Look for state patterns
    patterns = {
        "__STATE__": r'window\.__STATE__\s*=',
        "__WB_STATE__": r'window\.__WB_STATE__\s*=',
        "data-state": r'data-state=["\']',
        "script#state": r'id=["\']state["\']',
        "__NEXT_DATA__": r'__NEXT_DATA__',
        "application/ld+json": r'application/ld\+json',
        "og:title": r'og:title',
        "wbbasket": r'wbbasket\.ru',
        "images": r'images\d+/',
    }
    found = {}
    for name, pat in patterns.items():
        m = re.search(pat, html, re.I)
        found[name] = bool(m)
    print(f"  Patterns: {found}")
    wb_res["html_patterns"] = found

    # Extract all wbbasket URLs
    wb_imgs = re.findall(r'https://[a-z\-]*basket-\d+\d?\.wbbasket\.ru/[^\s"\'<>]+', html)
    if wb_imgs:
        print(f"  wbbasket URLs in HTML: {len(wb_imgs)}")
        for u in wb_imgs[:5]:
            print(f"    {u[:100]}")
        wb_res["html_wb_urls"] = wb_imgs[:10]

results["WB"] = wb_res
print(f"  WB: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 2. OZON (use proxy if available)
# ======================================================================
print("=" * 70)
print(f"2. OZON [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

oz_id = "1600562267"
oz_res = {}
sess_oz = std_req.Session()
sess_oz.headers.update(H)

# 2a. Direct (expect 403)
st, sz, r = get(sess_oz, "https://www.ozon.ru/", H, 8)
cks = list(sess_oz.cookies.keys())
print(f"  [direct_home] {st} | {sz}B | ck={cks}")
oz_res["direct_home"] = {"status": st, "size": sz, "cookies": cks}

# 2b. Through proxy (if we have one)
if working_px:
    px = working_px[0]
    pr = {"http": f"http://{px}", "https": f"http://{px}"}
    sess_oz2 = std_req.Session()
    sess_oz2.headers.update(H)
    try:
        st, sz, r = get(sess_oz2, "https://www.ozon.ru/", H, 12)
        cks2 = list(sess_oz2.cookies.keys())
        print(f"  [proxy_home px={px}] {st} | {sz}B | ck={cks2}")
        oz_res["proxy_home"] = {"status": st, "size": sz, "proxy": px, "cookies": cks2}

        if st == 200:
            time.sleep(1)
            st, sz, r = get(sess_oz2, f"https://www.ozon.ru/product/{oz_id}/", H, 12)
            has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
            has_og = 'og:title' in r.text if hasattr(r, 'text') and sz > 0 else False
            has_state = '__INITIAL_STATE__' in r.text or '__NEXT_DATA__' in r.text if hasattr(r, 'text') else False
            print(f"  [proxy_product] {st} | {sz}B | JSONLD={has_ld} OG={has_og} STATE={has_state}")
            oz_res["proxy_product"] = {"status": st, "size": sz, "jsonld": has_ld, "og": has_og, "state": has_state}
            # Save sample
            if hasattr(r, 'text') and sz > 1000:
                with open("ozon_proxy_sample.html", "w", encoding="utf-8") as f:
                    f.write(r.text[:8000])
    except Exception as e:
        print(f"  [proxy_ozon] ERR: {str(e)[:60]}")
        oz_res["proxy_err"] = str(e)[:80]
else:
    print("  [proxy] NO WORKING PROXY — trying direct with extra headers")
    # Try with X-Request-Id and proper session
    try:
        sess_oz3 = std_req.Session()
        sess_oz3.headers.update(H)
        sess_oz3.headers["X-Request-Id"] = "0"
        r1 = sess_oz3.get("https://www.ozon.ru/", timeout=8)
        time.sleep(1.5)
        r2 = sess_oz3.get(f"https://www.ozon.ru/product/{oz_id}/", timeout=8)
        print(f"  [session_product] {r2.status_code} | {len(r2.content)}B")
        oz_res["session_product"] = {"status": r2.status_code, "size": len(r2.content)}
    except Exception as e:
        print(f"  [sess_ozon] ERR: {str(e)[:60]}")

# 2c. curl_cffi (different TLS fingerprint)
if HAS_CFFI:
    try:
        st, sz, r = cffi_get(f"https://www.ozon.ru/product/{oz_id}/", H, 10)
        has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
        print(f"  [cffi_product] {st} | {sz}B | JSONLD={has_ld}")
        oz_res["cffi"] = {"status": st, "size": sz, "jsonld": has_ld}
    except Exception as e:
        print(f"  [cffi_oz] ERR: {str(e)[:60]}")

results["OZON"] = oz_res
print(f"  OZON: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 3. AVITO (search works! extract links)
# ======================================================================
print("=" * 70)
print(f"3. AVITO [rem={rem():.0f}s]")
print("=" * 70)
sec = time.monotonic()

av_id = "1096783029"
av_res = {}
sess_av = std_req.Session()
sess_av.headers.update(H)

# 3a. Home for cookies/PoW
try:
    st, sz, r = get(sess_av, "https://www.avito.ru/", H, 8)
    cks = list(sess_av.cookies.keys())
    print(f"  [home] {st} | {sz}B | ck={cks}")
    av_res["home"] = {"status": st, "size": sz, "cookies": cks}
except Exception as e:
    print(f"  [av_home] ERR: {e}")

# 3b. Search page (the 200 one!)
print("  --- Search ---")
try:
    st, sz, r = get(sess_av, "https://www.avito.ru/moscow/search?query=iphone", H, 12)
    print(f"  [search] {st} | {sz}B")
    av_res["search"] = {"status": st, "size": sz}

    if st == 200 and sz > 5000:
        html = r.text
        # Save sample
        with open("avito_search_sample.html", "w", encoding="utf-8") as f:
            f.write(html[:15000])
        print(f"  Saved avito_search_sample.html")

        # Try many patterns to find ad URLs
        patterns = [
            ("href_ad", r'href="([^"]*avito\.ru[^"]*/\d{8,})"'),
            ("href_p", r'href="(/[^"]*/p/\d+)"'),
            ("href_ad2", r'href="(/[^"]*/ad/\d+)"'),
            ("data_href", r'data-href="([^"]*/\d{8,})"'),
            ("json_url", r'"url"\s*:\s*"([^"]*avito[^"]*/\d+)"'),
            ("json_ad", r'"adUrl"\s*:\s*"([^"]+)"'),
            ("og_url", r'og:url["\s]+content=["\']([^"\']+)"'),
            ("any_num", r'href="([^"]*?/\d{9,12})"'),
        ]
        found_links = []
        for name, pat in patterns:
            links = re.findall(pat, html)
            if links:
                found_links = links
                print(f"  [pattern {name}] {len(links)} found")
                for l in links[:5]:
                    print(f"    {l[:90]}")
                break
        if not found_links:
            # Last resort: find any /NNNNNNNNN/ pattern
            found_links = re.findall(r'/(?:p|ad)/(\d{8,})', html)
            if found_links:
                print(f"  [fallback ids] {len(found_links)} found: {found_links[:5]}")
                found_links = [f"/p/{x}" for x in found_links[:10]]
            else:
                # Look for data attributes
                data_attrs = re.findall(r'data-(?:ad|item|listing)[^=]*=["\']([^"\']+)', html, re.I)
                print(f"  [data_attrs] {len(data_attrs)} found")
                found_links = data_attrs[:10]

        av_res["search_links"] = found_links[:10]

        # Follow first ad link
        if found_links:
            sample = found_links[0]
            if sample.startswith('/'):
                sample = f"https://www.avito.ru{sample}"
            if not sample.startswith('http'):
                sample = f"https://www.avito.ru/{sample}"
            print(f"  [follow] {sample}")
            st2, sz2, r2 = get(sess_av, sample, H, 10)
            has_ld = 'application/ld+json' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            has_og = 'og:title' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            # Extract og:title
            og_t = ""
            if hasattr(r2, 'text'):
                m = re.search(r'og:title["\s]+content=["\']([^"\']+)', r2.text)
                if m:
                    og_t = m.group(1)[:80]
            print(f"  [ad_page] {st2} | {sz2}B | JSONLD={has_ld} OG={has_og} title='{og_t}'")
            av_res["ad_page"] = {"status": st2, "size": sz2, "url": sample, "jsonld": has_ld, "og": has_og, "title": og_t}
            if hasattr(r2, 'text') and sz2 > 1000:
                with open("avito_ad_sample.html", "w", encoding="utf-8") as f:
                    f.write(r2.text[:10000])
except Exception as e:
    print(f"  [av_search] ERR: {str(e)[:80]}")
    traceback.print_exc()

# 3c. Direct /ad/{id}
try:
    st, sz, r = get(sess_av, f"https://www.avito.ru/ad/{av_id}", H, 8)
    has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
    print(f"  [direct_ad] {st} | {sz}B | JSONLD={has_ld}")
    av_res["direct_ad"] = {"status": st, "size": sz, "jsonld": has_ld}
except Exception as e:
    print(f"  [direct_ad] ERR: {str(e)[:60]}")

results["AVITO"] = av_res
print(f"  AVITO: {time.monotonic()-sec:.1f}s")

# ======================================================================
# 4. FARPOST (home works! find items)
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
    st, sz, r = get(sess_fp, "https://www.farpost.ru/", H, 10)
    cks = list(sess_fp.cookies.keys())
    print(f"  [home] {st} | {sz}B | ck={cks}")
    fp_res["home"] = {"status": st, "size": sz, "cookies": cks}

    if st == 200 and sz > 5000:
        html = r.text
        with open("farpost_home_sample.html", "w", encoding="utf-8") as f:
            f.write(html[:15000])

        # Extract ALL hrefs
        all_hrefs = re.findall(r'href="([^"]+)"', html)
        # Filter for item-like URLs
        item_like = [h for h in all_hrefs if re.search(r'/\d{5,}', h) and not h.startswith('#')]
        cat_like = [h for h in all_hrefs if re.search(r'/(geo|catalog|category|ads|item)/', h, re.I)]
        print(f"  All hrefs: {len(all_hrefs)}, item-like: {len(item_like)}, cat-like: {len(cat_like)}")
        for h in item_like[:8]:
            print(f"    item: {h[:80]}")
        for h in cat_like[:8]:
            print(f"    cat:  {h[:80]}")
        fp_res["home_items"] = item_like[:10]
        fp_res["home_cats"] = cat_like[:10]

        # Follow first item-like link
        if item_like:
            sample = item_like[0]
            if sample.startswith('/'):
                sample = f"https://www.farpost.ru{sample}"
            print(f"  [follow_item] {sample}")
            st2, sz2, r2 = get(sess_fp, sample, H, 10)
            has_ld = 'application/ld+json' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            has_og = 'og:title' in r2.text if hasattr(r2, 'text') and sz2 > 0 else False
            og_t = ""
            if hasattr(r2, 'text'):
                m = re.search(r'og:title["\s]+content=["\']([^"\']+)', r2.text)
                if m:
                    og_t = m.group(1)[:80]
            print(f"  [item] {st2} | {sz2}B | JSONLD={has_ld} OG={has_og} title='{og_t}'")
            fp_res["item"] = {"status": st2, "size": sz2, "url": sample, "jsonld": has_ld, "og": has_og, "title": og_t}
            if hasattr(r2, 'text') and sz2 > 1000:
                with open("farpost_item_sample.html", "w", encoding="utf-8") as f:
                    f.write(r2.text[:10000])
except Exception as e:
    print(f"  [fp_home] ERR: {str(e)[:80]}")

# 4b. Try category patterns (different from v22)
fp_cats = [
    ("geo_root", "https://www.farpost.ru/geo/"),
    ("geo_msk", "https://www.farpost.ru/geo/moskva/"),
    ("cat_root", "https://www.farpost.ru/catalog/"),
    ("ads_root", "https://www.farpost.ru/ads/"),
    ("items_root", "https://www.farpost.ru/items/"),
]
for name, url in fp_cats:
    if rem() < 25:
        break
    st, sz, r = get(sess_fp, url, H, 8)
    has_ld = 'application/ld+json' in r.text if hasattr(r, 'text') and sz > 0 else False
    preview = ""
    if hasattr(r, 'text') and sz > 0:
        m = re.search(r'<title[^>]*>([^<]+)', r.text, re.I)
        preview = m.group(1)[:60] if m else ""
    print(f"  [{name}] {st} | {sz}B | JSONLD={has_ld} | {preview}")
    fp_res[name] = {"status": st, "size": sz, "jsonld": has_ld, "title": preview}

# 4c. Direct item
st, sz, r = get(sess_fp, f"https://www.farpost.ru/item/{fp_id}", H, 8)
print(f"  [direct_item] {st} | {sz}B")
fp_res["direct_item"] = {"status": st, "size": sz}

results["FARPOST"] = fp_res
print(f"  FARPOST: {time.monotonic()-sec:.1f}s")

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

with open("diag_v23_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\nDONE diag_v23 [{elapsed:.1f}s]")
