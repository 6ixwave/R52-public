"""Диагностика AliExpress RU: локаль, cookie, ID-сбор."""
import json, re, time, sys
from urllib.parse import quote

from curl_cffi import requests as cffi_req

print("=" * 60)
print("DIAGNOSTIC: AliExpress RU locale & ID collection")
print("=" * 60)

# --- TEST 1: Wholesale HTML with different cookie formats ---
QUERIES = [
    ("A: f_locale=ru-RU (hyphen)", "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"),
    ("B: f_locale=ru_RU (underscore)", "f_locale=ru_RU&f_region=RU&f_currency=RUB&ais=ru_RU&fre=0&c_b_locale=ru_RU"),
    ("C: minimal ru", "f_locale=ru&f_region=RU&f_currency=RUB"),
    ("D: full with intl", "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU&intl_locale=ru_RU"),
]

for label, cookie_val in QUERIES:
    print(f"\n--- {label} ---")
    url = "https://ru.aliexpress.com/wholesale?SearchText=" + quote("наушники bluetooth") + "&page=1"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://ru.aliexpress.com/",
        "Upgrade-Insecure-Requests": "1",
    }
    cookies = {"aep_usuc_f": cookie_val}
    try:
        t0 = time.monotonic()
        resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30), headers=headers, cookies=cookies)
        lat = time.monotonic() - t0
        print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
        html = resp.text

        # html lang attr
        lang_m = re.search(r'<html[^>]*lang=["\']([^"\']*)', html)
        print(f"  <html lang>: {lang_m.group(1) if lang_m else 'NOT FOUND'}")

        # meta locale
        meta_locale = re.findall(r'<meta[^>]*content="([^"]*)"[^>]*(?:locale|language)', html[:8000])
        meta_locale2 = re.findall(r'<meta[^>]*(?:locale|language)[^>]*content="([^"]*)"', html[:8000])
        print(f"  meta locale: {meta_locale[:2] + meta_locale2[:2]}")

        # Titles
        titles = re.findall(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', html)
        if not titles:
            titles = re.findall(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', html, re.DOTALL)
            titles = [re.sub(r'<[^>]+>', '', t).strip() for t in titles]
        print(f"  Titles: {len(titles)}")
        for t in titles[:3]:
            print(f"    {t[:120]}")

        # RU vs IT detection
        ru_chars = len(re.findall(r'[\u0410-\u044f]', html[:200000]))
        it_words = len(re.findall(r'\b(?:cuffie|cuffia|blu|wireless|ore|autonomia|disponib|magazzino|prezzo|nuove|stile|lunga|potenti|spaziale|cancellazione|attiva|rumore|risoluzione|alta|con)\b', html[:200000], re.I))
        ru_words = len(re.findall(r'\b(?:наушники|блютуз|беспроводн|автономност|отличн|новинк|качеств|звук|басы|удобн|комплект)\b', html[:200000], re.I))
        print(f"  RU chars: {ru_chars}, IT words: {it_words}, RU words: {ru_words}")

        # Check for locale switcher in HTML
        locale_switch = re.findall(r'(?:locale|language|lang)[^>]{0,50}(?:ru|it|en)', html[:50000], re.I)
        if locale_switch:
            print(f"  Locale hints: {locale_switch[:3]}")

    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(2)

# --- TEST 2: Product page by ID ---
print(f"\n{'=' * 60}")
print("TEST: Product page by ID (1005013037146888)")
print("=" * 60)
pid = "1005013037146888"
purl = f"https://ru.aliexpress.com/item/{pid}.html"
headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://ru.aliexpress.com/",
}
cookies = {"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"}
try:
    t0 = time.monotonic()
    resp = cffi_req.get(purl, impersonate="chrome", timeout=(15, 30), headers=headers, cookies=cookies)
    lat = time.monotonic() - t0
    print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
    html = resp.text

    # H1
    h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    h1_text = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip() if h1_m else "NOT FOUND"
    print(f"  H1: {h1_text[:200]}")

    # og:title
    og_t = re.search(r'og:title["\s]*content=["\']([^"\']+)', html)
    if not og_t:
        og_t = re.search(r'content=["\']([^"\']+)["\s]*property=["\']og:title', html)
    print(f"  og:title: {og_t.group(1)[:200] if og_t else 'NOT FOUND'}")

    # JSON-LD
    ld_matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
    for ld in ld_matches[:2]:
        try:
            ld_json = json.loads(ld.strip())
            if isinstance(ld_json, dict) and ld_json.get("@type") == "Product":
                print(f"  JSON-LD name: {ld_json.get('name', 'N/A')[:200]}")
                offers = ld_json.get("offers", {})
                if isinstance(offers, dict):
                    print(f"  JSON-LD price: {offers.get('price')} {offers.get('priceCurrency')}")
                elif isinstance(offers, list) and offers:
                    print(f"  JSON-LD price: {offers[0].get('price')} {offers[0].get('priceCurrency')}")
        except json.JSONDecodeError:
            pass

    # __INIT_DATA_STATE__
    init_m = re.search(r'window\.__INIT_DATA_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
    if init_m:
        try:
            init_data = json.loads(init_m.group(1))
            # Look for title in various paths
            def find_key(obj, key, depth=0):
                if depth > 10:
                    return None
                if isinstance(obj, dict):
                    if key in obj:
                        return obj[key]
                    for v in obj.values():
                        r = find_key(v, key, depth + 1)
                        if r:
                            return r
                elif isinstance(obj, list):
                    for it in obj:
                        r = find_key(it, key, depth + 1)
                        if r:
                            return r
                return None
            title = find_key(init_data, "title")
            if title:
                print(f"  INIT_DATA title: {str(title)[:200]}")
        except json.JSONDecodeError as e:
            print(f"  INIT_DATA parse error: {e}")
    else:
        print("  __INIT_DATA_STATE__: NOT FOUND")

    # Check for CAPTCHA
    if "captcha" in html[:5000].lower() or "tmd" in html[:5000].lower():
        print("  WARNING: CAPTCHA/TMD detected!")

except Exception as e:
    print(f"  ERROR: {e}")

# --- TEST 3: Search API titles ---
print(f"\n{'=' * 60}")
print("TEST: Search API titles")
print("=" * 60)
api_url = ("https://ru.aliexpress.com/aer-webapi/v1/search"
           f"?SearchText={quote('наушники bluetooth')}&page=1&pageSize=20&sortType=default")
api_headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={quote('наушники bluetooth')}",
    "Origin": "https://ru.aliexpress.com",
}
try:
    t0 = time.monotonic()
    resp = cffi_req.get(api_url, impersonate="chrome", timeout=(15, 30), headers=api_headers, cookies=cookies)
    lat = time.monotonic() - t0
    print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
    if resp.status_code == 200:
        data = resp.json()

        def find_snippets(obj, depth=0):
            results = []
            if depth > 15:
                return results
            if isinstance(obj, dict):
                if "snippetContainer" in obj and isinstance(obj["snippetContainer"], dict):
                    results.append(obj["snippetContainer"])
                for k, v in obj.items():
                    if k != "snippetContainer":
                        results.extend(find_snippets(v, depth + 1))
            elif isinstance(obj, list):
                for it in obj:
                    results.extend(find_snippets(it, depth + 1))
            return results

        snippets = find_snippets(data.get("data", data))
        print(f"  Snippets: {len(snippets)}")

        def find_texts(o):
            ts = []
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "text":
                        if isinstance(v, dict) and "text" in v:
                            t = str(v["text"]).strip()
                            if t:
                                ts.append(t)
                        elif isinstance(v, str) and v.strip():
                            ts.append(v.strip())
                    else:
                        ts.extend(find_texts(v))
            elif isinstance(o, list):
                for it in o:
                    ts.extend(find_texts(it))
            return ts

        for i, sn in enumerate(snippets[:5]):
            texts = find_texts(sn)
            good = [t for t in texts if len(t) > 15]
            print(f"  [{i}] {good[0][:120] if good else 'NO TITLE'}")

except Exception as e:
    print(f"  ERROR: {e}")

print(f"\n{'=' * 60}")
print("DIAGNOSTIC COMPLETE")
print("=" * 60)
