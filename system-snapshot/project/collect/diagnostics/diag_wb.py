"""Быстрая диагностика: сырой GET к WB без прокси, с подробным логом."""
import requests
import time
import traceback

URLS = [
    ("WB-199298871", "https://www.wildberries.ru/catalog/199298871/detail.aspx"),
    ("WB-1", "https://www.wildberries.ru/"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

for label, url in URLS:
    print(f"\n{'='*60}")
    print(f"[{label}] {url}")
    start = time.monotonic()
    try:
        r = requests.get(url, headers=HEADERS, timeout=(8, 15))
        elapsed = time.monotonic() - start
        print(f"  Status: {r.status_code}  Time: {elapsed:.2f}s")
        print(f"  Content-Length: {len(r.content)} bytes")
        print(f"  Content-Type: {r.headers.get('Content-Type', '?')}")
        print(f"  Server: {r.headers.get('Server', '?')}")
        print(f"  First 300 chars: {r.text[:300]!r}")
    except requests.exceptions.SSLError as e:
        print(f"  SSL ERROR ({time.monotonic()-start:.2f}s): {e}")
    except requests.exceptions.ConnectionError as e:
        print(f"  CONNECTION ERROR ({time.monotonic()-start:.2f}s): {e}")
    except requests.exceptions.Timeout as e:
        print(f"  TIMEOUT ({time.monotonic()-start:.2f}s): {e}")
    except Exception as e:
        print(f"  ERROR ({time.monotonic()-start:.2f}s): {type(e).__name__}: {e}")
        traceback.print_exc()
    time.sleep(2)

print("\nDone.")
