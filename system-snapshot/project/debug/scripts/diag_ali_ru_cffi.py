"""Точечная диагностика: прямой curl_cffi запрос к AliExpress RU search API."""
import sys, os, time, json, traceback
from urllib.parse import quote

print("=" * 60)
print("DIAG: Direct curl_cffi → aer-webapi/v1/search")
print("=" * 60)

# 1. Проверка curl_cffi
print("\n[1] Checking curl_cffi...")
try:
    from curl_cffi import requests as cffi_req
    print(f"    OK: curl_cffi version: {cffi_req.__version__ if hasattr(cffi_req, '__version__') else 'unknown'}")
except ImportError as e:
    print(f"    FAIL: {e}")
    sys.exit(1)

# 2. Прямой запрос
QUERY = "наушники беспроводные"
ENCODED = quote(QUERY, safe="")
URL = f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={ENCODED}&page=1&pageSize=40&sortType=default"

print(f"\n[2] GET {URL[:100]}...")
t0 = time.monotonic()
try:
    resp = cffi_req.get(
        URL,
        impersonate="chrome",
        timeout=(15, 30),
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={ENCODED}",
            "Origin": "https://ru.aliexpress.com",
        },
    )
    elapsed = time.monotonic() - t0
    print(f"    Status: {resp.status_code}")
    print(f"    Headers: Content-Type={resp.headers.get('content-type','?')}")
    print(f"    Body size: {len(resp.text)} bytes")
    print(f"    Elapsed: {elapsed:.1f}s")
    print(f"    First 500 chars: {resp.text[:500]}")
    
    if resp.status_code == 200:
        try:
            data = resp.json()
            print(f"\n[3] JSON parsed OK, top keys: {list(data.keys())[:10]}")
            # Save
            out_dir = os.path.join(os.path.dirname(__file__), "data", "collector_output")
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, "diag_cffi_search.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            print(f"    Saved: {out}")
        except Exception as e:
            print(f"\n[3] JSON parse FAIL: {e}")
    else:
        print(f"\n[3] Non-200 response, checking for redirect/captcha...")
        # Check for TMD/captcha markers
        text = resp.text[:3000]
        for marker in ["Пройдите проверку", "captcha", "tmd", "punish", "redirect", "location"]:
            if marker.lower() in text.lower():
                print(f"    Found marker: {marker}")
except Exception as e:
    elapsed = time.monotonic() - t0
    print(f"\n    EXCEPTION after {elapsed:.1f}s: {type(e).__name__}: {e}")
    traceback.print_exc()

# 3. Второй запрос — без Referer/Origin (проще)
print(f"\n[4] Second attempt (minimal headers)...")
t0 = time.monotonic()
try:
    resp2 = cffi_req.get(
        URL,
        impersonate="chrome",
        timeout=(15, 30),
    )
    elapsed = time.monotonic() - t0
    print(f"    Status: {resp2.status_code}, size: {len(resp2.text)} bytes, {elapsed:.1f}s")
    print(f"    First 300: {resp2.text[:300]}")
except Exception as e:
    elapsed = time.monotonic() - t0
    print(f"    EXCEPTION after {elapsed:.1f}s: {type(e).__name__}: {e}")
    traceback.print_exc()

# 4. Третий — через requests (fallback)
print(f"\n[5] Third attempt (plain requests)...")
t0 = time.monotonic()
try:
    import requests
    resp3 = requests.get(
        URL,
        timeout=(15, 30),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    elapsed = time.monotonic() - t0
    print(f"    Status: {resp3.status_code}, size: {len(resp3.text)} bytes, {elapsed:.1f}s")
    print(f"    First 300: {resp3.text[:300]}")
except Exception as e:
    elapsed = time.monotonic() - t0
    print(f"    EXCEPTION after {elapsed:.1f}s: {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n" + "=" * 60)
print("DIAG DONE")
print("=" * 60)
