"""Диагностика v2: свежий Playwright + API headers + embedded JSON."""
import re, json, time, os, shutil, sys

print("=" * 60)
print("DIAG v2: AliExpress RU locale — fresh approaches")
print("=" * 60)

# ─── 1. Playwright FRESH profile with explicit locale ───
print("\n[1] Playwright FRESH profile, locale='ru-RU'")
PW_PROFILE = os.path.expanduser("~/.local/share/r52/aliexpress_pw_profile_RU_FRESH")
if os.path.exists(PW_PROFILE):
    shutil.rmtree(PW_PROFILE)
    print("  Deleted old fresh profile")
os.makedirs(PW_PROFILE, exist_ok=True)

from playwright.sync_api import sync_playwright
from urllib.parse import quote

query = "наушники bluetooth"
search_url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page=1&_lang=ru_RU&_currency=RUB"

try:
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=PW_PROFILE,
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ignore_https_errors=True,
    )
    # Anti-detect
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
    """)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Step 1: Visit home page first (warm up)
    print("  Warming: ru.aliexpress.com ...")
    page.goto("https://ru.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    
    # Check home page locale
    home_html = page.content()
    home_lang = re.search(r'<html[^>]*lang="([^"]*)"', home_html)
    print(f"  Home lang: {home_lang.group(1) if home_lang else 'NOT FOUND'}")
    
    # Set cookies explicitly
    page.evaluate("""
        try {
            document.cookie = 'aep_usuc_f=f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU; domain=.aliexpress.com; path=/; max-age=31536000';
            document.cookie = 'intl_common_forever=ru; domain=.aliexpress.com; path=/; max-age=31536000';
            localStorage.setItem('lang', 'ru');
            localStorage.setItem('i18n_lang', 'ru');
        } catch(e) { console.log(e); }
    """)
    page.wait_for_timeout(1000)

    # Step 2: Navigate to search
    print(f"  Searching: {search_url[:80]}...")
    t0 = time.monotonic()
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(6000)
    lat = time.monotonic() - t0
    
    html = page.content()
    print(f"  Page loaded: {len(html)} bytes, {lat:.1f}s")
    
    lang_m = re.search(r'<html[^>]*lang="([^"]*)"', html)
    print(f"  <html lang=\"{lang_m.group(1) if lang_m else 'NOT FOUND'}\">")
    
    title_m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    print(f"  <title>: {title_m.group(1).strip()[:80] if title_m else 'NOT FOUND'}")
    
    # h3 titles
    h3s = re.findall(r'<h3[^>]*>(.*?)</h3>', html[:300000], re.DOTALL)
    h3s_clean = [re.sub(r'<[^>]+>', '', h).strip()[:70] for h in h3s[:6]]
    for i, t in enumerate(h3s_clean):
        print(f"  h3[{i}]: {t}")
    
    # Check embedded JSON
    print("\n  Embedded JSON check:")
    for marker in ['__NEXT_DATA__', '__INIT_DATA_STATE__', 'runParams', 'window.__data', '__APP_DATA__']:
        idx = html.find(marker)
        print(f"    {marker}: {'FOUND at ' + str(idx) if idx >= 0 else 'not found'}")
    
    # Try to find product JSON in page
    # Look for "subject" or "title" fields in JSON-like structures
    json_blocks = re.findall(r'"(?:subject|title|pcTitle)"\s*:\s*"([^"]{10,200})"', html[:400000])
    print(f"  JSON title/subject fields found: {len(json_blocks)}")
    for i, t in enumerate(json_blocks[:5]):
        print(f"    [{i}]: {t[:70]}")
    
    # Check if there's a "locale" or "lang" in embedded data
    locale_m = re.search(r'"(?:locale|lang|language)"\s*:\s*"([^"]+)"', html[:200000])
    if locale_m:
        print(f"  Embedded locale: {locale_m.group(1)}")
    
    # Save HTML for inspection
    out_html = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\data\collector_output\diag_pw_fresh.html"
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Saved HTML to {out_html}")
    
    ctx.close()
    pw.stop()
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
    try:
        pw.stop()
    except:
        pass

# ─── 2. Search API with XHR headers ───
print("\n[2] Search API with XHR headers")
from curl_cffi import requests as cffi_req

api_variants = [
    {
        "label": "v1: XHR header",
        "url": f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={quote(query)}&page=1&pageSize=10&sortType=default&_lang=ru_RU&_currency=RUB",
        "headers": {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}",
            "Origin": "https://ru.aliexpress.com",
            "X-Requested-With": "XMLHttpRequest",
        },
    },
    {
        "label": "v2: POST",
        "url": "https://ru.aliexpress.com/aer-webapi/v1/search",
        "post": True,
        "data": {"SearchText": query, "page": 1, "pageSize": 10, "sortType": "default", "_lang": "ru_RU", "_currency": "RUB"},
        "headers": {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}",
            "Origin": "https://ru.aliexpress.com",
        },
    },
    {
        "label": "v3: mtop API",
        "url": f"https://acs.m.aliexpress.com/h5/mtop.ae.search.pc.query/1.0/?jsv=2.7.2&appKey=24568759&api=mtop.ae.search.pc.query&v=1.0&type=jsonp&dataType=json&SearchText={quote(query)}&page=1&pageSize=20&sortType=default&_lang=ru_RU&_currency=RUB",
        "headers": {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://ru.aliexpress.com/",
            "Origin": "https://ru.aliexpress.com",
        },
    },
]

cookies_api = {
    "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
    "site": "ru",
}

for v in api_variants:
    print(f"\n  --- {v['label']} ---")
    try:
        t0 = time.monotonic()
        if v.get("post"):
            resp = cffi_req.post(v["url"], impersonate="chrome", timeout=(15, 30),
                                  headers=v["headers"], cookies=cookies_api, json=v["data"])
        else:
            resp = cffi_req.get(v["url"], impersonate="chrome", timeout=(15, 30),
                                 headers=v["headers"], cookies=cookies_api)
        lat = time.monotonic() - t0
        print(f"  HTTP {resp.status_code}, {len(resp.text)} bytes, {lat:.1f}s")
        
        # Check if JSON
        is_json = resp.text.strip().startswith('{') or resp.text.strip().startswith('[')
        has_jsonp = 'jsonp' in resp.text[:100].lower() or 'mtopjsonp' in resp.text[:200]
        print(f"  JSON: {is_json}, JSONP: {has_jsonp}, starts with: {resp.text[:80]!r}")
        
        if is_json:
            try:
                data = resp.json()
                print(f"  Top keys: {list(data.keys())[:10] if isinstance(data, dict) else 'list'}")
                # Try to find items
                items_found = False
                for key in ["data", "result"]:
                    if isinstance(data, dict) and key in data:
                        inner = data[key]
                        if isinstance(inner, dict):
                            print(f"  data.{key} keys: {list(inner.keys())[:10]}")
                            for k2 in ["items", "products", "list", "result", "moduleList"]:
                                if k2 in inner:
                                    items = inner[k2]
                                    if isinstance(items, list):
                                        print(f"  data.{key}.{k2}: {len(items)} items")
                                        for i, it in enumerate(items[:3]):
                                            if isinstance(it, dict):
                                                t = it.get("title") or it.get("subject") or it.get("pcTitle") or ""
                                                p = it.get("price") or it.get("minPrice") or ""
                                                print(f"    [{i}] {str(t)[:50]} | {str(p)[:30]}")
                                        items_found = True
                                        break
            except json.JSONDecodeError:
                print(f"  JSON parse error. First 200: {resp.text[:200]}")
        elif has_jsonp:
            # Extract JSONP
            jm = re.search(r'[a-zA-Z_]\w*\((.*)\)\s*;?\s*$', resp.text, re.DOTALL)
            if jm:
                try:
                    data = json.loads(jm.group(1))
                    print(f"  JSONP parsed! Top keys: {list(data.keys())[:10] if isinstance(data, dict) else 'list'}")
                except:
                    print(f"  JSONP extract: {jm.group(1)[:200]}")
            else:
                print(f"  JSONP but no match. Text: {resp.text[:200]}")
        else:
            print(f"  HTML response (not JSON). Title: {re.search(r'<title>(.*?)</title>', resp.text, re.DOTALL)}")
    except Exception as e:
        print(f"  ERROR: {e}")

# ─── 3. Check wholesale HTML for embedded product data ───
print("\n[3] Wholesale HTML embedded data deep check")
try:
    url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page=1&_lang=ru_RU&_currency=RUB"
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30),
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                            "Referer": "https://ru.aliexpress.com/",
                        },
                        cookies=cookies_api)
    html = resp.text
    print(f"  HTML: {len(html)} bytes")
    
    # Find ALL script blocks and look for product data
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"  Script blocks: {len(scripts)}")
    
    # Look for large scripts with product data
    for i, s in enumerate(scripts):
        if len(s) > 5000 and ('"title"' in s or '"subject"' in s or '"pcTitle"' in s):
            print(f"\n  Script[{i}] ({len(s)} bytes) has title/subject:")
            # Find product titles
            titles = re.findall(r'"(?:pcTitle|title|subject)"\s*:\s*"([^"]{10,200})"', s)
            for j, t in enumerate(titles[:8]):
                print(f"    [{j}]: {t[:70]}")
            # Check locale in script
            loc = re.search(r'"(?:locale|lang|language|site)"\s*:\s*"([^"]+)"', s)
            if loc:
                print(f"    locale in script: {loc.group(1)}")
            # Check for "ru" vs "it"
            ru_count = s.count('"ru')
            it_count = s.count('"it')
            print(f"    'ru' occurrences: {ru_count}, 'it' occurrences: {it_count}")
            break
    
    # Look for window.__INIT_DATA_STATE__ or similar
    for marker in ['__INIT_DATA_STATE__', '__APP_DATA__', 'window.__data', 'g_pageConfig', 'runParams']:
        idx = html.find(marker)
        if idx >= 0:
            snippet = html[idx:idx+500]
            print(f"\n  {marker} at {idx}: {snippet[:200]}")
    
    # Check meta tags for locale
    metas = re.findall(r'<meta[^>]*(?:locale|language|lang)[^>]*>', html[:5000], re.IGNORECASE)
    print(f"\n  Meta locale tags: {metas[:5]}")
    
    # Look for "i18n" or "locale" in first 50KB
    i18n_matches = re.findall(r'"(?:i18n|locale|lang|site)"\s*[:=]\s*["\']?([a-z]{2}(?:-[A-Z]{2})?)', html[:50000])
    print(f"  i18n/locale values in first 50KB: {set(i18n_matches[:20])}")
    
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("DIAG v2 complete")
print("=" * 60)
