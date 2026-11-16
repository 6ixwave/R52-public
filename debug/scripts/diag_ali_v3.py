"""Diag v3: Embedded JSON in wholesale, product page retries, pdp_npi format."""
import json, re, time
from urllib.parse import quote
from curl_cffi import requests as cffi_req

COOKIE_RU = {"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"}
HDRS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

print("=" * 60)
print("TEST 1: Wholesale page - embedded JSON & pdp_npi format")
print("=" * 60)
ENQ = quote("наушники bluetooth", safe="")
url = f"https://ru.aliexpress.com/wholesale?SearchText={ENQ}&page=1&_lang=ru_RU&_currency=RUB"
r = cffi_req.get(url, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
html = r.text
print(f"HTTP {r.status_code}, {len(html)} bytes")

for var_name in ["__INIT_DATA_STATE__", "runParams", "__NEXT_DATA__", "__APP_DATA__", "g_config", "window.__data"]:
    pos = html.find(var_name)
    if pos >= 0:
        snippet = html[pos:pos+200]
        print(f"  {var_name}: FOUND at {pos}")
        print(f"    {snippet[:150]}")
    else:
        print(f"  {var_name}: not found")

pdp_matches = re.findall(r'pdp_npi=([^&"]+)', html[:200000])
print(f"\npdp_npi instances: {len(pdp_matches)}")
for i, m in enumerate(pdp_matches[:5]):
    parts = m.split("!")
    print(f"  [{i}] raw: {m[:120]}")
    print(f"       parts({len(parts)}): {parts}")
    if len(parts) >= 4:
        print(f"       currency={parts[1]}, price={parts[2]}, orig={parts[3]}")

json_titles = re.findall(r'"title"\s*:\s*"([^"]{10,200})"', html[:300000])
print(f"\nJSON title fields: {len(json_titles)}")
for t in json_titles[:5]:
    has_ru = any('\u0410' <= c <= '\u044f' for c in t)
    print(f"  {'RU' if has_ru else '??'}: {t[:100]}")

for key in ["subject", "productTitle", "itemTitle", "seoTitle", "name"]:
    found = re.findall(rf'"{key}"\s*:\s*"([^"]{{10,200}})"', html[:300000])
    if found:
        print(f"\n{key}: {len(found)} found")
        for t in found[:3]:
            has_ru = any('\u0410' <= c <= '\u044f' for c in t)
            print(f"  {'RU' if has_ru else '??'}: {t[:100]}")

cl_idx = html.find('card-list')
if cl_idx >= 0:
    card_area = html[cl_idx:cl_idx+50000]
    data_attrs = re.findall(r'data-(?:title|name|subject|text)="([^"]{10,200})"', card_area)
    print(f"\ncard-list data attrs: {len(data_attrs)}")
    for t in data_attrs[:5]:
        print(f"  {t[:100]}")
    aria = re.findall(r'aria-label="([^"]{10,200})"', card_area)
    print(f"card-list aria-labels: {len(aria)}")
    for t in aria[:5]:
        has_ru = any('\u0410' <= c <= '\u044f' for c in t)
        print(f"  {'RU' if has_ru else '??'}: {t[:100]}")
    h3s = re.findall(r'<h3[^>]*>(.*?)</h3>', card_area, re.DOTALL)
    print(f"card-list h3: {len(h3s)}")
    for t in h3s[:5]:
        clean = re.sub(r'<[^>]+>', '', t).strip()
        has_ru = any('\u0410' <= c <= '\u044f' for c in clean)
        print(f"  {'RU' if has_ru else '??'}: {clean[:100]}")
    spans = re.findall(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</span>', card_area, re.DOTALL)
    print(f"card-list title spans: {len(spans)}")
    for t in spans[:5]:
        clean = re.sub(r'<[^>]+>', '', t).strip()
        has_ru = any('\u0410' <= c <= '\u044f' for c in clean)
        print(f"  {'RU' if has_ru else '??'}: {clean[:100]}")

print(f"\n{'=' * 60}")
print("TEST 2: Product page by ID - 3 retries, www first")
print("=" * 60)
for pid in ["1005010340652259", "1005012296880178"]:
    got_full = False
    for attempt in range(3):
        url_p = f"https://www.aliexpress.com/item/{pid}.html?_lang=ru_RU"
        try:
            t0 = time.monotonic()
            rp = cffi_req.get(url_p, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
            lat = time.monotonic() - t0
            size = len(rp.text)
            bxp = rp.headers.get('bxpunish', '0')
            title_m = re.search(r'<title[^>]*>(.*?)</title>', rp.text, re.DOTALL)
            og_t = re.search(r'property="og:title"[^>]*content="([^"]*)"', rp.text)
            print(f"  [{pid}] try{attempt+1} www: HTTP {rp.status_code}, {size}B, {lat:.1f}s, bxpunish={bxp}")
            print(f"    title: {(title_m.group(1)[:80] if title_m else '?')}")
            print(f"    og:title: {(og_t.group(1)[:80] if og_t else '?')}")
            if size > 10000:
                print(f"    >>> FULL PAGE! <<<")
                got_full = True
                break
        except Exception as e:
            print(f"  [{pid}] try{attempt+1} www: ERROR {e}")
        time.sleep(2)
    if not got_full:
        print(f"  [{pid}] www: all 3 tries CAPTCHA/short")

print(f"\n{'=' * 60}")
print("TEST 3: Product page - ru domain URL patterns")
print("=" * 60)
pid = "1005010340652259"
patterns = [
    f"https://ru.aliexpress.com/item/{pid}.html",
    f"https://ru.aliexpress.com/i/{pid}.html",
    f"https://ru.aliexpress.com/item/{pid}.html?spm=a2g0o.productlist",
]
for purl in patterns:
    try:
        t0 = time.monotonic()
        rp = cffi_req.get(purl, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
        lat = time.monotonic() - t0
        size = len(rp.text)
        bxp = rp.headers.get('bxpunish', '0')
        print(f"  {purl[:65]}: HTTP {rp.status_code}, {size}B, {lat:.1f}s, bxpunish={bxp}")
    except Exception as e:
        print(f"  {purl[:65]}: ERROR {e}")
    time.sleep(1)

print(f"\n{'=' * 60}")
print("TEST 4: Wholesale - check if 'it' locale can be overridden via cookie 'site' param")
print("=" * 60)
cookie_site = {
    "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU&site=ru",
    "aep_usuc": "site=ru&c_tp=RUB&b_locale=ru_RU&region=RU",
}
url_w = f"https://ru.aliexpress.com/wholesale?SearchText={ENQ}&page=1"
rw = cffi_req.get(url_w, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=cookie_site)
hw = rw.text
lang_m = re.search(r'<html[^>]*lang=["\']([^"\']*)', hw)
print(f"site=ru cookie: HTTP {rw.status_code}, {len(hw)}B, lang={lang_m.group(1) if lang_m else '?'}")
titles_w = re.findall(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', hw)
if not titles_w:
    titles_w = re.findall(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', hw, re.DOTALL)
    titles_w = [re.sub(r'<[^>]+>', '', t).strip() for t in titles_w]
print(f"Titles: {len(titles_w)}")
for t in titles_w[:4]:
    has_ru = any('\u0410' <= c <= '\u044f' for c in t)
    print(f"  {'RU' if has_ru else '??'}: {t[:100]}")

print("\nDONE")
