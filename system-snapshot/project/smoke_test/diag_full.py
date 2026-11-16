"""Полная диагностика: поиск живых ID + тест всех 6 коллекторов."""
import sys, os, re, time, json, traceback, logging
from pathlib import Path

# Критично: Python кладёт папку скрипта в sys.path[0], а не cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

results = {}

# ============================================================
# STEP 1: Найти живые ID для FARPOST и AVITO
# ============================================================
print("=" * 70)
print("STEP 1: FIND LIVE IDS")
print("=" * 70)

import requests
sess = requests.Session()
sess.headers.update(HEADERS)

# --- FARPOST ---
print("\n--- FARPOST: scraping category page ---")
farpost_id = None
try:
    url = "https://www.farpost.ru/vladivostok/tech/communication/cellphones/"
    r = sess.get(url, timeout=15)
    print(f"  Category: {r.status_code}, {len(r.content)} bytes")
    # Farpost listing links: /vladivostok/.../{slug}-{ID}.html
    # Pattern: data-bulletin-id="12345" or href with numeric ID
    ct = r.headers.get("Content-Type", "")
    if "1251" in ct or "cp1251" in ct:
        html = r.content.decode("windows-1251", errors="replace")
    else:
        html = r.text
    
    # Try data-bulletin-id first
    ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html)
    if not ids:
        # Try href pattern: /vladivostok/.../something-123456789.html
        ids = re.findall(r'/vladivostok/[^"]*?-(\d{5,})\.html', html)
    if not ids:
        # Broader: any numeric ID in bulletin links
        ids = re.findall(r'href="[^"]*?/(\d{8,})\.html', html)
    
    print(f"  Found {len(ids)} candidate IDs: {ids[:5]}")
    if ids:
        farpost_id = ids[0]
        # Verify: fetch the listing page
        verify_url = f"https://www.farpost.ru/vladivostok/{farpost_id}.html"
        vr = sess.get(verify_url, timeout=15)
        print(f"  Verify {verify_url}: {vr.status_code}, {len(vr.content)} bytes")
        if vr.status_code != 200:
            # Try with category path
            verify_url2 = f"https://www.farpost.ru/vladivostok/tech/communication/cellphones/{farpost_id}.html"
            vr = sess.get(verify_url2, timeout=15)
            print(f"  Verify2 {verify_url2}: {vr.status_code}")
            if vr.status_code == 200:
                farpost_id = farpost_id  # keep
            else:
                farpost_id = None
        else:
            # Extract title for confirmation
            tm = re.search(r"<title[^>]*>(.*?)</title>", vr.text, re.I | re.S)
            print(f"  Title: {(tm.group(1).strip()[:60] if tm else '?')}")
except Exception as e:
    print(f"  FARPOST find error: {e}")
    traceback.print_exc()

print(f"\n  FARPOST live ID: {farpost_id}")

# --- AVITO ---
print("\n--- AVITO: scraping category page ---")
avito_id = None
try:
    # Avito category: /moskva_i_mo/tech/phone (note: moskva with Latin 'a')
    urls_to_try = [
        "https://m.avito.ru/moskva_i_mo/tech/phone",
        "https://m.avito.ru/spb/tech/phone",
        "https://www.avito.ru/moskva_i_mo/tech/phone",
        "https://www.avito.ru/spb/tech",
    ]
    for cat_url in urls_to_try:
        try:
            r = sess.get(cat_url, timeout=15)
            print(f"  {cat_url}: {r.status_code}, {len(r.content)} bytes")
            if r.status_code != 200:
                continue
            html = r.text
            # Avito listing links: /ad/123456789 or /moskva_i_mo/.../123456789
            ids = re.findall(r'/ad/(\d{8,})', html)
            if not ids:
                ids = re.findall(r'href="[^"]*?/(\d{9,})[/?]', html)
            if not ids:
                # JSON-LD or embedded data
                ids = re.findall(r'"id":(\d{9,})', html)
            print(f"  Found {len(ids)} candidate IDs: {ids[:5]}")
            if ids:
                avito_id = ids[0]
                # Verify
                verify_url = f"https://www.avito.ru/ad/{avito_id}"
                vr = sess.get(verify_url, timeout=15)
                print(f"  Verify {verify_url}: {vr.status_code}, {len(vr.content)} bytes")
                if vr.status_code == 200:
                    tm = re.search(r"<title[^>]*>(.*?)</title>", vr.text, re.I | re.S)
                    print(f"  Title: {(tm.group(1).strip()[:60] if tm else '?')}")
                    break
                else:
                    avito_id = None
        except Exception as e:
            print(f"  {cat_url} error: {e}")
        time.sleep(2)
except Exception as e:
    print(f"  AVITO find error: {e}")
    traceback.print_exc()

print(f"\n  AVITO live ID: {avito_id}")

# ============================================================
# STEP 2: Test each collector with (hopefully) live IDs
# ============================================================
print("\n" + "=" * 70)
print("STEP 2: COLLECTOR TESTS")
print("=" * 70)

TEST_IDS = {
    "WB": "199298871",
    "OZON": "1600562267",
    "AVITO": avito_id or "1096783029",
    "FARPOST": farpost_id or "18530297982",
    "ALIEXPRESS": "1005006564853571",
    "ALIEXPRESS_RU": "1005006564853571",
}

# --- WB (known working, quick re-check) ---
print("\n--- WB ---")
try:
    from r52.collectors.wb import WBCollector
    c = WBCollector(use_proxies=False)
    url = c.build_url(TEST_IDS["WB"])
    t0 = time.monotonic()
    r = c.collect_product(TEST_IDS["WB"], url)
    dt = time.monotonic() - t0
    if r:
        obs = r.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        results["WB"] = {"status": obs.get("status"), "title": title[:60], "prices": len(prices), "time": round(dt,1)}
        print(f"  OK: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
    else:
        results["WB"] = {"status": "NO_DATA", "time": round(dt,1)}
        print(f"  NO_DATA in {dt:.1f}s")
except Exception as e:
    results["WB"] = {"status": f"ERROR: {e}", "time": 0}
    print(f"  ERROR: {e}")

# --- FARPOST ---
print(f"\n--- FARPOST (ID={TEST_IDS['FARPOST']}) ---")
try:
    from r52.collectors.farpost import FarpostCollector
    c = FarpostCollector(use_proxies=False)
    url = c.build_url(TEST_IDS["FARPOST"])
    print(f"  URL: {url}")
    t0 = time.monotonic()
    r = c.collect_product(TEST_IDS["FARPOST"], url)
    dt = time.monotonic() - t0
    if r:
        obs = r.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        results["FARPOST"] = {"status": obs.get("status"), "title": title[:60], "prices": len(prices), "time": round(dt,1)}
        print(f"  OK: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
    else:
        results["FARPOST"] = {"status": "NO_DATA", "time": round(dt,1)}
        print(f"  NO_DATA in {dt:.1f}s")
except Exception as e:
    results["FARPOST"] = {"status": f"ERROR: {e}", "time": 0}
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- AVITO ---
print(f"\n--- AVITO (ID={TEST_IDS['AVITO']}) ---")
try:
    from r52.collectors.avito import AvitoCollector
    c = AvitoCollector(use_proxies=False)
    url = c.build_url(TEST_IDS["AVITO"])
    print(f"  URL: {url}")
    t0 = time.monotonic()
    r = c.collect_product(TEST_IDS["AVITO"], url)
    dt = time.monotonic() - t0
    if r:
        obs = r.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        results["AVITO"] = {"status": obs.get("status"), "title": title[:60], "prices": len(prices), "time": round(dt,1)}
        print(f"  OK: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
    else:
        results["AVITO"] = {"status": "NO_DATA", "time": round(dt,1)}
        print(f"  NO_DATA in {dt:.1f}s")
except Exception as e:
    results["AVITO"] = {"status": f"ERROR: {e}", "time": 0}
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- OZON (Composer API first) ---
print(f"\n--- OZON (ID={TEST_IDS['OZON']}) ---")
try:
    from r52.collectors.ozon import OzonCollector
    c = OzonCollector(use_proxies=False)
    url = c.build_url(TEST_IDS["OZON"])
    print(f"  URL: {url}")
    t0 = time.monotonic()
    r = c.collect_product(TEST_IDS["OZON"], url)
    dt = time.monotonic() - t0
    if r:
        obs = r.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        results["OZON"] = {"status": obs.get("status"), "title": title[:60], "prices": len(prices), "time": round(dt,1)}
        print(f"  OK: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
    else:
        results["OZON"] = {"status": "NO_DATA", "time": round(dt,1)}
        print(f"  NO_DATA in {dt:.1f}s")
        # Extra: try Composer API directly
        print("  Trying Composer API directly...")
        composer_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{TEST_IDS['OZON']}/"
        cr = sess.get(composer_url, headers=HEADERS, timeout=15, verify=False)
        print(f"  Composer direct: {cr.status_code}, {len(cr.content)} bytes")
        if cr.status_code == 200:
            try:
                data = cr.json()
                print(f"  Keys: {list(data.keys())[:10]}")
            except:
                print(f"  Not JSON: {cr.text[:200]}")
except Exception as e:
    results["OZON"] = {"status": f"ERROR: {e}", "time": 0}
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- ALIEXPRESS (Playwright) ---
print(f"\n--- ALIEXPRESS (ID={TEST_IDS['ALIEXPRESS']}) ---")
try:
    from r52.collectors.aliexpress import AliExpressCollector
    c = AliExpressCollector(use_proxies=False)
    url = c.build_url(TEST_IDS["ALIEXPRESS"])
    print(f"  URL: {url}")
    t0 = time.monotonic()
    r = c.collect_product(TEST_IDS["ALIEXPRESS"], url)
    dt = time.monotonic() - t0
    if r:
        obs = r.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        results["ALIEXPRESS"] = {"status": obs.get("status"), "title": title[:60], "prices": len(prices), "time": round(dt,1)}
        print(f"  OK: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
    else:
        results["ALIEXPRESS"] = {"status": "NO_DATA", "time": round(dt,1)}
        print(f"  NO_DATA in {dt:.1f}s")
except Exception as e:
    results["ALIEXPRESS"] = {"status": f"ERROR: {e}", "time": 0}
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- ALIEXPRESS_RU (Playwright) ---
print(f"\n--- ALIEXPRESS_RU (ID={TEST_IDS['ALIEXPRESS_RU']}) ---")
try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    c = AliExpressRUCollector(use_proxies=False)
    url = c.build_url(TEST_IDS["ALIEXPRESS_RU"])
    print(f"  URL: {url}")
    t0 = time.monotonic()
    r = c.collect_product(TEST_IDS["ALIEXPRESS_RU"], url)
    dt = time.monotonic() - t0
    if r:
        obs = r.get("observation", {})
        title = (obs.get("fields") or {}).get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        results["ALIEXPRESS_RU"] = {"status": obs.get("status"), "title": title[:60], "prices": len(prices), "time": round(dt,1)}
        print(f"  OK: {obs.get('status')} | {title[:50]} | {len(prices)} prices | {dt:.1f}s")
    else:
        results["ALIEXPRESS_RU"] = {"status": "NO_DATA", "time": round(dt,1)}
        print(f"  NO_DATA in {dt:.1f}s")
except Exception as e:
    results["ALIEXPRESS_RU"] = {"status": f"ERROR: {e}", "time": 0}
    print(f"  ERROR: {e}")
    traceback.print_exc()

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 70)
print("FINAL SUMMARY")
print("=" * 70)
for src, info in results.items():
    icon = "✓" if info.get("status") in ("OK", "PARTIAL_SUCCESS", "SUCCESS") else "⚠" if "WARN" in str(info.get("status","")) else "✗"
    print(f"{icon} {src:15s} | {str(info.get('status','?')):20s} | {info.get('title','—')[:40]:40s} | {info.get('prices',0)} prices | {info.get('time',0)}s")

# Save JSON
out = Path(__file__).parent / "diag_full_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
