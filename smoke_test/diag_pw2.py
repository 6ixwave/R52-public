"""Точечная диагностика: persistent context, OZON PW, AVITO live ID."""
import sys, os, re, time, json, traceback, logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

print("=" * 70)
print("STEP 1: PERSISTENT CONTEXT DIRECT TEST")
print("=" * 70)

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile")
print(f"  Profile: {PROFILE_DIR}")
print(f"  Exists: {os.path.exists(PROFILE_DIR)}")
if os.path.exists(PROFILE_DIR):
    entries = os.listdir(PROFILE_DIR)
    print(f"  Entries: {entries[:15]}")
    # Check for lock files
    locks = [e for e in entries if "lock" in e.lower() or "singleton" in e.lower()]
    print(f"  Locks: {locks}")

# Test 1: Fresh profile
print("\n  --- Test A: Fresh temp profile ---")
try:
    pw = sync_playwright().start()
    tmp_prof = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_test")
    os.makedirs(tmp_prof, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        tmp_prof,
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        user_agent=UA,
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Rome",
        ignore_https_errors=True,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print(f"  Fresh profile: OK (pages={len(ctx.pages)})")
    ctx.close()
    pw.stop()
except Exception as e:
    print(f"  Fresh profile FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()
    try:
        pw.stop()
    except:
        pass

# Test 2: Existing profile
print("\n  --- Test B: Existing profile ---")
try:
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        user_agent=UA,
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Rome",
        ignore_https_errors=True,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print(f"  Existing profile: OK (pages={len(ctx.pages)})")
    ctx.close()
    pw.stop()
except Exception as e:
    print(f"  Existing profile FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()
    try:
        pw.stop()
    except:
        pass

# Test 3: Collector's _ensure_browser with traceback
print("\n  --- Test C: Collector _ensure_browser (with full traceback) ---")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    # Monkey-patch to catch the exact error
    orig_cleanup = c._cleanup_pw if hasattr(c, '_cleanup_pw') else None
    try:
        from playwright.sync_api import sync_playwright as sp2
        prof = PROFILE_DIR
        os.makedirs(prof, exist_ok=True)
        pw2 = sp2().start()
        ctx2 = pw2.chromium.launch_persistent_context(
            prof,
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
            user_agent=UA,
            locale="it-IT",
            viewport={"width": 1920, "height": 1080},
            timezone_id="Europe/Rome",
            ignore_https_errors=True,
        )
        print(f"  Direct PW in collector scope: OK")
        ctx2.close()
        pw2.stop()
    except Exception as e2:
        print(f"  Direct PW in collector scope FAILED: {type(e2).__name__}: {e2}")
        traceback.print_exc()
        try:
            pw2.stop()
        except:
            pass
except Exception as e:
    print(f"  Collector test error: {e}")
    traceback.print_exc()

print()
print("=" * 70)
print("STEP 2: OZON _get_pw INSPECTION")
print("=" * 70)

try:
    from r52.collectors.ozon import OzonCollector
    c = OzonCollector(use_proxies=False)
    print(f"  _cffi: {c._cffi is not None}")
    print(f"  Has _get_pw: {hasattr(c, '_get_pw')}")
    print(f"  Has _pw: {hasattr(c, '_pw')}")
    
    # What is _get_pw?
    import inspect
    if hasattr(c, '_get_pw'):
        src = inspect.getsource(c._get_pw)
        print(f"  _get_pw source (first 500):")
        print(src[:500])
    
    pw = c._get_pw()
    print(f"\n  _get_pw() returned: {type(pw).__name__}")
    print(f"  pw attributes: {[a for a in dir(pw) if not a.startswith('_')]}")
    
    # Try navigating to a simple page first
    url = "https://www.ozon.ru/product/1600562267/"
    print(f"\n  Navigating to {url}...")
    t0 = time.monotonic()
    result = pw.get(url, extra_wait_ms=5000)
    dt = time.monotonic() - t0
    if result:
        print(f"  Result: status={result.status}, len={len(result.html)} in {dt:.1f}s")
        # Check for anti-bot markers
        html_snippet = result.html[:2000]
        if "captcha" in html_snippet.lower() or "antibot" in html_snippet.lower():
            print("  ⚠ ANTI-BOT/CAPTCHA detected in HTML")
        if "cookie" in html_snippet.lower():
            print("  ⚠ Cookie wall detected")
        # Title
        tm = re.search(r"<title[^>]*>(.*?)</title>", result.html, re.I|re.S)
        print(f"  Title: {(tm.group(1).strip()[:80] if tm else '?')}")
    else:
        print(f"  Result: None in {dt:.1f}s")
except Exception as e:
    print(f"  OZON PW error: {e}")
    traceback.print_exc()

print()
print("=" * 70)
print("STEP 3: AVITO — find live ID via Google/known patterns")
print("=" * 70)

import requests
sess = requests.Session()
sess.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})

# Strategy: try known Avito listing patterns or use a different city
# Avito 439 = rate limited. Try with delay and different approach.
avito_id = None

# Try: specific popular categories with different cities
avito_search_urls = [
    "https://m.avito.ru/samara/tech/phone/",
    "https://m.avito.ru/ekaterinburg/tech/phone/",
    "https://m.avito.ru/kazan/tech/phone/",
    "https://m.avito.ru/krasnodar/tech/phone/",
    "https://m.avito.ru/rostov-na-donu/tech/phone/",
]

for u in avito_search_urls:
    try:
        time.sleep(3)  # Longer delay to avoid 439
        t0 = time.monotonic()
        r = sess.get(u, timeout=20)
        dt = time.monotonic() - t0
        print(f"  {u.split('/')[2]}: {r.status_code} ({len(r.content)}B, {dt:.1f}s)")
        if r.status_code == 200:
            html = r.text
            ids = re.findall(r'/ad/(\d{8,})', html)
            if not ids:
                ids = re.findall(r'data-id="(\d{8,})"', html)
            if not ids:
                ids = re.findall(r'"listingId":(\d{8,})', html)
            print(f"    IDs found: {len(ids)} {ids[:5]}")
            if ids:
                vid = ids[0]
                time.sleep(2)
                vr = sess.get(f"https://m.avito.ru/ad/{vid}", timeout=15)
                print(f"    Verify /ad/{vid}: {vr.status_code} ({len(vr.content)}B)")
                if vr.status_code == 200:
                    tm = re.search(r"<title[^>]*>(.*?)</title>", vr.text, re.I|re.S)
                    print(f"    Title: {(tm.group(1).strip()[:60] if tm else '?')}")
                    avito_id = vid
                    break
    except Exception as e:
        print(f"    Error: {e}")

print(f"\n  AVITO live ID: {avito_id}")

# If still no ID, try a known pattern: Avito IDs are 9-11 digits
# Try a range of recent-looking IDs
if not avito_id:
    print("\n  Trying known Avito ID patterns...")
    # Avito IDs from 2024-2026 are typically 9-11 digits starting with 1
    test_ids = [
        "1156783029",  # modified from dead one
        "1096783030",
        "1123456789",
        "1187654321",
    ]
    for tid in test_ids:
        try:
            time.sleep(2)
            r = sess.get(f"https://m.avito.ru/ad/{tid}", timeout=15)
            print(f"  /ad/{tid}: {r.status_code}")
            if r.status_code == 200:
                tm = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I|re.S)
                print(f"    Title: {(tm.group(1).strip()[:60] if tm else '?')}")
                avito_id = tid
                break
        except Exception as e:
            print(f"    Error: {e}")

print(f"\n  FINAL AVITO ID: {avito_id}")

print()
print("=" * 70)
print("DONE")
print("=" * 70)
