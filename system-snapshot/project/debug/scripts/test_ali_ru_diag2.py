"""Диагностика AliExpress RU v2: locale, images, search API, single item."""
import sys, os, json, time, logging, re
from pathlib import Path
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
sys.path.insert(0, '.')

OUT = Path('data/collector_output/test_ali_ru_diag2')
OUT.mkdir(parents=True, exist_ok=True)

from curl_cffi import requests as cffi_req

query = "наушники bluetooth"

# === 1. WHOLESALE HTML: locale + images ===
print('\n=== 1. WHOLESALE HTML ===')
url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page=1"

# Try multiple cookie variants
cookie_variants = [
    ("A: f_locale", {"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB"}),
    ("B: ais", {"aep_usuc_f": "ais=ru-RU&fre=0&c_b_locale=ru_RU"}),
    ("C: both", {"aep_usuc_f": "ais=ru-RU&fre=0&c_b_locale=ru_RU&f_locale=ru-RU&f_region=RU&f_currency=RUB"}),
]

for label, cookies in cookie_variants:
    try:
        resp = cffi_req.get(
            url, impersonate="chrome", timeout=(15, 30),
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": "https://ru.aliexpress.com/",
                "Upgrade-Insecure-Requests": "1",
            },
            cookies=cookies,
        )
        html = resp.text
        print(f'\n  [{label}] Status={resp.status_code}, Size={len(html)}')
        
        # Locale markers
        it_markers = html.lower().count('cuffie') + html.lower().count('auricolari') + html.lower().count('bluetooth')
        ru_markers = html.count('Наушник') + html.count('наушник') + html.count('Беспроводн') + html.count('беспроводн')
        print(f'    IT markers: {it_markers}, RU markers: {ru_markers}')
        
        # Check <html lang>
        lang_m = re.search(r'<html[^>]*lang=["\']([^"\']+)["\']', html)
        print(f'    <html lang>: {lang_m.group(1) if lang_m else "not found"}')
        
        # Check for locale in JS
        locale_js = re.search(r'locale["\s:]+["\']?([a-zA-Z\-]+)', html[:5000])
        print(f'    JS locale: {locale_js.group(1) if locale_js else "not found"}')
        
        # First 3 titles
        card_idx = html.find('id="card-list"')
        if card_idx > 0:
            window = html[card_idx:card_idx+100000]
            titles = re.findall(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,200})"', window)
            if not titles:
                titles = re.findall(r'<h3[^>]*>(.*?)</h3>', window, re.DOTALL)
                titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]
            print(f'    First titles:')
            for t in titles[:3]:
                print(f'      "{t[:70]}"')
        
        # Images in first card
        if card_idx > 0:
            window = html[card_idx:card_idx+50000]
            # All img srcs
            all_imgs = re.findall(r'<img[^>]*src="([^"]*(?:alicdn|aliexpress)[^"]*)"', window, re.I)
            # data-src
            data_imgs = re.findall(r'data-src="([^"]*(?:alicdn|aliexpress)[^"]*)"', window, re.I)
            # data-lazy-src
            lazy_imgs = re.findall(r'data-lazy-src="([^"]*(?:alicdn|aliexpress)[^"]*)"', window, re.I)
            # background-image
            bg_imgs = re.findall(r'background(?:-image)?\s*:\s*url\(([^)]*(?:alicdn|aliexpress)[^)]*)\)', window, re.I)
            print(f'    Images: src={len(all_imgs)}, data-src={len(data_imgs)}, lazy={len(lazy_imgs)}, bg={len(bg_imgs)}')
            for img in (all_imgs + data_imgs + lazy_imgs + bg_imgs)[:4]:
                print(f'      {img[:90]}')
        
        # Save first variant full HTML for analysis
        if label.startswith("C"):
            (OUT / "wholesale_full.html").write_text(html, encoding="utf-8")
            print(f'    Full HTML saved ({len(html)} bytes)')
        
        time.sleep(1)
    except Exception as e:
        print(f'  [{label}] Error: {e}')

# === 2. SEARCH API ===
print('\n=== 2. SEARCH API ===')
api_urls = [
    f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={quote(query)}&page=1&pageSize=20&sortType=default",
    f"https://ru.aliexpress.com/fn/search-pc/index?SearchText={quote(query)}&page=1",
    f"https://www.aliexpress.com/f/search?SearchText={quote(query)}&page=1",
]
for api_url in api_urls:
    try:
        r2 = cffi_req.get(
            api_url, impersonate="chrome", timeout=(15, 30),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}",
                "Origin": "https://ru.aliexpress.com",
            },
            cookies={"aep_usuc_f": "ais=ru-RU&fre=0&c_b_locale=ru_RU&f_locale=ru-RU&f_region=RU&f_currency=RUB"},
        )
        print(f'\n  URL: {api_url[:80]}')
        print(f'  Status: {r2.status_code}, Size: {len(r2.text)}, CT: {r2.headers.get("content-type","?")[:50]}')
        print(f'  First 400: {r2.text[:400]}')
        try:
            data = r2.json()
            print(f'  JSON OK! Keys: {list(data.keys())[:8]}')
        except Exception:
            print(f'  Not JSON')
    except Exception as e:
        print(f'\n  URL: {api_url[:80]}\n  Error: {e}')
    time.sleep(1)

# === 3. SINGLE ITEM ===
print('\n=== 3. SINGLE ITEM ===')
item_url = "https://ru.aliexpress.com/item/1005006791583450.html"
r3 = cffi_req.get(
    item_url, impersonate="chrome", timeout=(15, 30),
    headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://ru.aliexpress.com/",
    },
    cookies={"aep_usuc_f": "ais=ru-RU&fre=0&c_b_locale=ru_RU&f_locale=ru-RU&f_region=RU&f_currency=RUB"},
)
print(f'Status: {r3.status_code}, Size: {len(r3.text)}')
print(f'First 600: {r3.text[:600]}')

# Check structure
og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', r3.text, re.I)
og_price = re.search(r'<meta[^>]+property=["\']og:price:amount["\'][^>]+content=["\']([^"\']*)["\']', r3.text, re.I)
title_tag = re.search(r'<title[^>]*>(.*?)</title>', r3.text, re.DOTALL)
h1 = re.search(r'<h1[^>]*>(.*?)</h1>', r3.text, re.DOTALL)
jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r3.text, re.DOTALL)
init_state = re.search(r'window\.__INIT_DATA_STATE__\s*=\s*(\{.{100,500})', r3.text, re.DOTALL)

print(f'\n  og:title: {og_title.group(1)[:100] if og_title else "N/A"}')
print(f'  og:price: {og_price.group(1) if og_price else "N/A"}')
print(f'  <title>: {title_tag.group(1)[:100] if title_tag else "N/A"}')
print(f'  <h1>: {re.sub(r"<[^>]+>","",h1.group(1))[:100] if h1 else "N/A"}')
print(f'  JSON-LD: {len(jsonld)} blocks')
for j in jsonld[:2]:
    print(f'    {j[:200]}')
print(f'  __INIT_DATA_STATE__: {"found" if init_state else "not found"}')
if init_state:
    print(f'    {init_state.group(1)[:300]}')

# Check if it's a redirect/short page
if len(r3.text) < 5000:
    print('\n  SHORT PAGE - likely CAPTCHA or redirect stub')
    # Look for meta refresh
    refresh = re.search(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']*)["\']', r3.text, re.I)
    if refresh:
        print(f'  Meta refresh: {refresh.group(1)}')

print('\nDone.')
