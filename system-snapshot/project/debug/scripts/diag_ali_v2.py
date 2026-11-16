"""Диагностика v2: крошечные страницы, mobile API, cookie-комбо."""
import json, re, time
from urllib.parse import quote
from curl_cffi import requests as cffi_req

COOKIE_RU = {"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"}
HDRS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

print("=" * 60)
print("TEST 1: Крошечная страница ru.aliexpress.com/item/ — что внутри?")
print("=" * 60)
pid = "1005010340652259"
url = f"https://ru.aliexpress.com/item/{pid}.html"
r = cffi_req.get(url, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
print(f"HTTP {r.status_code}, {len(r.text)} bytes")
print(f"Headers: {dict(list(r.headers.items())[:10])}")
print(f"Body full:\n{r.text[:1500]}")

print(f"\n{'=' * 60}")
print("TEST 2: www.aliexpress.com/item/ (большая страница)")
print("=" * 60)
url2 = f"https://www.aliexpress.com/item/{pid}.html?_lang=ru_RU"
r2 = cffi_req.get(url2, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
print(f"HTTP {r2.status_code}, {len(r2.text)} bytes")
# Extract key data
title_m = re.search(r'<title[^>]*>(.*?)</title>', r2.text, re.DOTALL)
og_t = re.search(r'property="og:title"[^>]*content="([^"]*)"', r2.text)
og_p = re.search(r'property="og:price:amount"[^>]*content="([^"]*)"', r2.text)
og_c = re.search(r'property="og:price:currency"[^>]*content="([^"]*)"', r2.text)
pdp = re.findall(r'pdp_npi=([^&"]+)', r2.text[:100000])
print(f"title: {title_m.group(1)[:100] if title_m else '?'}")
print(f"og:title: {og_t.group(1)[:100] if og_t else '?'}")
print(f"og:price: {og_p.group(1) if og_p else '?'} {og_c.group(1) if og_c else '?'}")
print(f"pdp_npi: {pdp[0][:80] if pdp else '?'}")
# Check for __INIT_DATA_STATE__
init_m = re.search(r'window\.__INIT_DATA_STATE__\s*=\s*', r2.text)
print(f"__INIT_DATA_STATE__: {'FOUND at pos ' + str(init_m.start()) if init_m else 'NOT FOUND'}")
# JSON-LD
jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', r2.text, re.DOTALL)
print(f"JSON-LD blocks: {len(jsonld)}")
if jsonld:
    try:
        ld = json.loads(jsonld[0])
        print(f"  JSON-LD type: {ld.get('@type','?')}")
        print(f"  JSON-LD name: {str(ld.get('name','?'))[:80]}")
        print(f"  JSON-LD offers: {json.dumps(ld.get('offers',{}), ensure_ascii=False)[:200]}")
    except: pass

print(f"\n{'=' * 60}")
print("TEST 3: Mobile API (m.aliexpress.com)")
print("=" * 60)
for murl in [
    f"https://m.aliexpress.com/item/{pid}.html",
    f"https://m.aliexpress.com/fallback/item/{pid}.html",
]:
    try:
        rm = cffi_req.get(murl, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
        print(f"\n  {murl}")
        print(f"  HTTP {rm.status_code}, {len(rm.text)} bytes")
        mt = re.search(r'<title[^>]*>(.*?)</title>', rm.text, re.DOTALL)
        print(f"  title: {mt.group(1)[:80] if mt else '?'}")
    except Exception as e:
        print(f"  {murl}: ERROR {e}")
    time.sleep(1)

print(f"\n{'=' * 60}")
print("TEST 4: Wholesale — aggressive cookie combo")
print("=" * 60)
ENQ = quote("наушники bluetooth", safe="")
# Try with multiple cookies
cookie_aggressive = {
    "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
    "intl_common_forever": "ru",
    "xman_us": "t_id=123456&last_visit_time=1731480000000",
    "intl_locale": "ru_RU",
    "aep_usuc": "site=ru&c_tp=RUB&b_locale=ru_RU&region=RU",
}
url_w = f"https://ru.aliexpress.com/wholesale?SearchText={ENQ}&page=1&_lang=ru_RU&_currency=RUB"
rw = cffi_req.get(url_w, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=cookie_aggressive)
html = rw.text
lang_m = re.search(r'<html[^>]*lang=["\']([^"\']*)', html)
print(f"HTTP {rw.status_code}, {len(html)} bytes")
print(f"<html lang>: {lang_m.group(1) if lang_m else '?'}")
# Check for locale in meta
meta_lang = re.findall(r'<meta[^>]*name="(?:locale|language|content-language)"[^>]*content="([^"]*)"', html[:10000], re.I)
print(f"meta locale: {meta_lang}")
# Check for "ru" hints in first 50KB
ru_hints = re.findall(r'(?:locale|lang|language)[^>]{0,30}ru', html[:50000], re.I)
print(f"RU hints in head: {ru_hints[:5]}")
# Extract titles
titles = re.findall(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', html)
if not titles:
    titles = re.findall(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', html, re.DOTALL)
    titles = [re.sub(r'<[^>]+>','',t).strip() for t in titles]
print(f"Titles ({len(titles)}):")
for t in titles[:4]:
    print(f"  {t[:120]}")
# Check for Russian chars in titles area
title_area = html[html.find('card-list'):html.find('card-list')+100000] if 'card-list' in html else ''
ru_in_cards = len(re.findall(r'[\u0410-\u044f]', title_area))
it_in_cards = len(re.findall(r'\b(?:cuffie|cuffia|blu|wireless|ore|autonomia|disponib|magazzino|prezzo|nuove|stile|lunga|potenti|spaziale|cancellazione|attiva|rumore|risoluzione|alta|con|ideali|musica|rock|design|bassi)\b', title_area, re.I))
print(f"RU chars in cards: {ru_in_cards}, IT words: {it_in_cards}")

print(f"\n{'=' * 60}")
print("TEST 5: Wholesale with 's' param (old AE locale switch)")
print("=" * 60)
url_s = f"https://ru.aliexpress.com/wholesale?SearchText={ENQ}&page=1&s=ru_RU"
rs = cffi_req.get(url_s, impersonate="chrome", timeout=(15,30), headers=HDRS, cookies=COOKIE_RU)
hs = rs.text
lang_s = re.search(r'<html[^>]*lang=["\']([^"\']*)', hs)
print(f"HTTP {rs.status_code}, {len(hs)} bytes, lang={lang_s.group(1) if lang_s else '?'}")
titles_s = re.findall(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', hs)
for t in titles_s[:3]:
    print(f"  {t[:100]}")

print("\nDONE")
