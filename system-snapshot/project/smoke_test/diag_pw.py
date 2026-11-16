"""Диагностика Playwright + точечный тест проблемных коллекторов."""
import sys, os, re, time, json, traceback, logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# Приглушить слишком шумные
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

print("=" * 70)
print("STEP 1: PLAYWRIGHT CHECK")
print("=" * 70)

pw_ok = False
try:
    from playwright.sync_api import sync_playwright
    print("  playwright module: OK")
    pw = sync_playwright().start()
    # Check if chromium is available
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        print(f"  chromium launch: OK (version: {browser.version})")
        browser.close()
        pw_ok = True
    except Exception as e:
        print(f"  chromium launch FAILED: {e}")
    pw.stop()
except ImportError as e:
    print(f"  playwright module MISSING: {e}")
except Exception as e:
    print(f"  playwright error: {e}")
    traceback.print_exc()

print(f"\n  Playwright ready: {pw_ok}")

# Check curl_cffi
try:
    from curl_cffi import requests as cffi_requests
    print("  curl_cffi: OK")
except ImportError:
    print("  curl_cffi: NOT INSTALLED")

# Check requests
try:
    import requests
    print(f"  requests: OK ({requests.__version__})")
except ImportError:
    print("  requests: NOT INSTALLED")

print()
print("=" * 70)
print("STEP 2: ALIEXPRESS COLLECTOR (verbose)")
print("=" * 70)

try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    print(f"  domain={c._domain}, ru_mode={c._ru_mode}")
    
    # Test _ensure_browser directly
    print("  Calling _ensure_browser()...")
    t0 = time.monotonic()
    ok = c._ensure_browser()
    dt = time.monotonic() - t0
    print(f"  _ensure_browser → {ok} in {dt:.1f}s")
    
    if ok:
        print("  Warming session...")
        t0 = time.monotonic()
        c._warm_session()
        dt = time.monotonic() - t0
        print(f"  _warm_session done in {dt:.1f}s")
        
        # Now try collect
        lid = "1005006564853571"
        url = c.build_url(lid)
        print(f"  Collecting {lid} from {url}...")
        t0 = time.monotonic()
        r = c.collect_product(lid, url)
        dt = time.monotonic() - t0
        if r:
            obs = r.get("observation", {})
            title = (obs.get("fields") or {}).get("title", {}).get("value", "")
            prices = obs.get("price_candidates", [])
            print(f"  RESULT: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
        else:
            print(f"  RESULT: None in {dt:.1f}s")
    else:
        print("  Browser not available, trying HTTP fallback...")
        lid = "1005006564853571"
        url = c.build_url(lid)
        t0 = time.monotonic()
        r = c.collect_product(lid, url)
        dt = time.monotonic() - t0
        print(f"  HTTP fallback: {type(r).__name__} in {dt:.1f}s")
    
    c.cleanup()
except Exception as e:
    print(f"  ALIEXPRESS ERROR: {e}")
    traceback.print_exc()

print()
print("=" * 70)
print("STEP 3: OZON COLLECTOR (verbose)")
print("=" * 70)

try:
    from r52.collectors.ozon import OzonCollector
    c = OzonCollector(use_proxies=False)
    print(f"  cffi available: {c._cffi is not None}")
    
    lid = "1600562267"
    url = c.build_url(lid)
    print(f"  URL: {url}")
    
    # Test Composer API directly with verbose
    print("  --- Composer API ---")
    t0 = time.monotonic()
    cr = c._try_composer_api(lid, url, "")
    dt = time.monotonic() - t0
    if cr:
        obs = cr.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        print(f"  Composer: OK in {dt:.1f}s | {title[:50]}")
    else:
        print(f"  Composer: None in {dt:.1f}s")
    
    # Test Playwright path
    print("  --- Playwright ---")
    try:
        pw = c._get_pw()
        print(f"  _get_pw: OK")
        t0 = time.monotonic()
        result = pw.get(url, extra_wait_ms=3000)
        dt = time.monotonic() - t0
        if result:
            print(f"  PW.get: status={result.status}, len={len(result.html)} in {dt:.1f}s")
            if result.status == 200 and len(result.html) > 1000:
                # Try parse
                class _Resp:
                    def __init__(self, html, status):
                        self.text = html
                        self.status_code = status
                obs = c.parse_page(_Resp(result.html, result.status), lid, url, "")
                print(f"  Parse: {type(obs).__name__}")
                if obs:
                    title = (obs.get("fields") or {}).get("title", {}).get("value", "")
                    prices = obs.get("price_candidates", [])
                    print(f"  Parsed: title={title[:50]}, prices={len(prices)}")
        else:
            print(f"  PW.get: None in {dt:.1f}s")
    except Exception as e:
        print(f"  Playwright error: {e}")
        traceback.print_exc()
    
    c.cleanup()
except Exception as e:
    print(f"  OZON ERROR: {e}")
    traceback.print_exc()

print()
print("=" * 70)
print("STEP 4: AVITO — find live ID via search")
print("=" * 70)

import requests
sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})

avito_id = None
# Try mobile search API or specific category
avito_urls = [
    "https://m.avito.ru/moskva_i_mo/tech/phone/?f=AQUBBApYcGhvbmU%3D",
    "https://m.avito.ru/spb/tech/phone/",
    "https://m.avito.ru/ekaterinburg/tech/phone/",
    "https://m.avito.ru/novosibirsk/tech/phone/",
]
for u in avito_urls:
    try:
        t0 = time.monotonic()
        r = sess.get(u, timeout=15)
        dt = time.monotonic() - t0
        print(f"  {u.split('/')[2]}: {r.status_code} ({len(r.content)}B, {dt:.1f}s)")
        if r.status_code == 200:
            html = r.text
            # Avito mobile: /ad/123456789 or data-id
            ids = re.findall(r'/ad/(\d{8,})', html)
            if not ids:
                ids = re.findall(r'"id":(\d{9,})', html)
            if not ids:
                ids = re.findall(r'href="[^"]*?/(\d{9,})[/?]', html)
            print(f"    Found {len(ids)} IDs: {ids[:5]}")
            if ids:
                # Verify first one
                vid = ids[0]
                vr = sess.get(f"https://m.avito.ru/ad/{vid}", timeout=15)
                print(f"    Verify /ad/{vid}: {vr.status_code}")
                if vr.status_code == 200:
                    tm = re.search(r"<title[^>]*>(.*?)</title>", vr.text, re.I|re.S)
                    print(f"    Title: {(tm.group(1).strip()[:60] if tm else '?')}")
                    avito_id = vid
                    break
        time.sleep(1)
    except Exception as e:
        print(f"    Error: {e}")

print(f"\n  AVITO live ID: {avito_id}")

# If found, test collector
if avito_id:
    print(f"\n  Testing AvitoCollector with ID {avito_id}...")
    try:
        from r52.collectors.avito import AvitoCollector
        c = AvitoCollector(use_proxies=False)
        url = c.build_url(avito_id)
        t0 = time.monotonic()
        r = c.collect_product(avito_id, url)
        dt = time.monotonic() - t0
        if r:
            obs = r.get("observation", {})
            title = (obs.get("fields") or {}).get("title", {}).get("value", "")
            prices = obs.get("price_candidates", [])
            print(f"  AVITO: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
        else:
            print(f"  AVITO: None in {dt:.1f}s")
        c.cleanup()
    except Exception as e:
        print(f"  AVITO collector error: {e}")
        traceback.print_exc()

print()
print("=" * 70)
print("DONE")
print("=" * 70)
