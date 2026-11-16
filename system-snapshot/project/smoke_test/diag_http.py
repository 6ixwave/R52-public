"""Быстрый HTTP-пинг всех 5 проблемных URL + диагностика коллекторов."""
import sys, time, traceback, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

URLS = {
    "FARPOST": "https://www.farpost.ru/vladivostok/18530297982.html",
    "ALIEXPRESS": "https://www.aliexpress.com/item/1005006564853571.html",
    "ALIEXPRESS_RU": "https://ru.aliexpress.com/item/1005006564853571.html",
    "AVITO": "https://www.avito.ru/ad/1096783029",
    "OZON": "https://www.ozon.ru/product/1600562267/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

print("=" * 70)
print("STEP 1: RAW HTTP PING (requests, 15s timeout each)")
print("=" * 70)

try:
    import requests
except ImportError:
    print("requests not available, trying urllib")
    import urllib.request

for src, url in URLS.items():
    t0 = time.monotonic()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        dt = time.monotonic() - t0
        body_len = len(resp.content)
        title_hint = ""
        # quick title extract
        import re
        m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.I | re.S)
        if m:
            title_hint = m.group(1).strip()[:80]
        print(f"\n[{src}] {resp.status_code} in {dt:.1f}s, {body_len} bytes")
        print(f"  URL(final): {resp.url}")
        print(f"  Title: {title_hint}")
        print(f"  Content-Type: {resp.headers.get('Content-Type','?')}")
        # Check for common block patterns
        text_lower = resp.text[:5000].lower()
        if "captcha" in text_lower or "robot" in text_lower or "403" in resp.text[:200]:
            print(f"  ⚠ POSSIBLE BLOCK/CAPTCHA")
        if "not found" in text_lower or "404" in resp.text[:200]:
            print(f"  ⚠ POSSIBLE 404/NOT FOUND")
        if "geo" in text_lower and ("block" in text_lower or "restrict" in text_lower):
            print(f"  ⚠ POSSIBLE GEO-BLOCK")
    except requests.exceptions.ConnectionError as e:
        print(f"\n[{src}] CONNECTION ERROR in {time.monotonic()-t0:.1f}s: {e}")
    except requests.exceptions.Timeout:
        print(f"\n[{src}] TIMEOUT (15s)")
    except Exception as e:
        print(f"\n[{src}] ERROR {type(e).__name__}: {e}")

print("\n" + "=" * 70)
print("STEP 2: COLLECTOR-LEVEL DIAGNOSTIC (fast sources first)")
print("=" * 70)

# FARPOST
print("\n--- FARPOST collector ---")
try:
    from r52.collectors.farpost import FarpostCollector
    c = FarpostCollector(use_proxies=False)
    url = c.build_url("18530297982")
    print(f"  build_url → {url}")
    # Monkey-patch to trace where None comes from
    import r52.collectors.farpost as fp_mod
    orig_fetch = c._fetch_page if hasattr(c, '_fetch_page') else None
    t0 = time.monotonic()
    result = c.collect_product("18530297982", url)
    print(f"  collect_product → {type(result).__name__} in {time.monotonic()-t0:.1f}s")
    if result:
        print(f"  Keys: {list(result.keys())}")
        obs = result.get("observation", {})
        print(f"  status: {obs.get('status')}")
        print(f"  title: {(obs.get('fields') or {}).get('title',{}).get('value','')[:60]}")
    else:
        print("  → None (need to trace internal method)")
        # Try calling internal fetch
        if hasattr(c, '_fetch_page'):
            print("  Trying _fetch_page...")
            page = c._fetch_page(url)
            print(f"  _fetch_page → {type(page).__name__}, len={len(page) if page else 0}")
        elif hasattr(c, 'fetch'):
            print("  Trying fetch...")
            page = c.fetch(url)
            print(f"  fetch → {type(page).__name__}, len={len(page) if page else 0}")
        else:
            print(f"  Available methods: {[m for m in dir(c) if not m.startswith('_')]}")
except Exception:
    traceback.print_exc()

# ALIEXPRESS
print("\n--- ALIEXPRESS collector ---")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    url = c.build_url("1005006564853571")
    print(f"  build_url → {url}")
    t0 = time.monotonic()
    result = c.collect_product("1005006564853571", url)
    print(f"  collect_product → {type(result).__name__} in {time.monotonic()-t0:.1f}s")
    if result:
        print(f"  Keys: {list(result.keys())}")
    else:
        print("  → None")
        print(f"  Available methods: {[m for m in dir(c) if not m.startswith('_')]}")
except Exception:
    traceback.print_exc()

# ALIEXPRESS_RU
print("\n--- ALIEXPRESS_RU collector ---")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    url = c.build_url("1005006564853571")
    print(f"  build_url → {url}")
    t0 = time.monotonic()
    result = c.collect_product("1005006564853571", url)
    print(f"  collect_product → {type(result).__name__} in {time.monotonic()-t0:.1f}s")
    if result:
        print(f"  Keys: {list(result.keys())}")
    else:
        print("  → None")
except Exception:
    traceback.print_exc()

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
