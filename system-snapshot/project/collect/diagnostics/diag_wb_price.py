"""WB price diagnostic v2: card.json selling/data + HTML for nm_id=138278567."""
import json, sys, time, re
sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")

from curl_cffi import requests as cffi_req

JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

NM_ID = 138278567
vol = NM_ID // 100000  # 1382
part = NM_ID // 1000   # 13827
path = f"/vol{vol}/part{part}/{NM_ID}/info/ru/card.json"

print(f"=== WB Price Diagnostic v2: nm_id={NM_ID} ===")
print(f"vol={vol}, part={part}")

# 1. Find basket host (start from likely range)
print("\n--- 1. Basket host ---")
host = None
for n in range(1, 61):
    h = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{h}{path}"
    try:
        resp = cffi_req.get(url, impersonate="chrome", timeout=8, headers=JSON_HEADERS)
        if resp.status_code == 200:
            host = h
            print(f"  Found: {h} (HTTP 200, {len(resp.content)}B)")
            break
    except Exception as e:
        pass
    time.sleep(0.2)

if not host:
    print("  BASKET HOST NOT FOUND — trying static-basket")
    for n in range(1, 61):
        h = f"static-basket-{vol}.wbbasket.ru"
        url = f"https://{h}{path}"
        try:
            resp = cffi_req.get(url, impersonate="chrome", timeout=8, headers=JSON_HEADERS)
            if resp.status_code == 200:
                host = h
                print(f"  Found static: {h}")
                break
        except Exception:
            pass
        time.sleep(0.2)
        break  # static-basket uses vol in hostname, try once

if not host:
    print("  ABORT: no basket host")
    sys.exit(1)

# 2. card.json full dump of price-related fields
print(f"\n--- 2. card.json price fields (from {host}) ---")
url = f"https://{host}{path}"
resp = cffi_req.get(url, impersonate="chrome", timeout=15, headers=JSON_HEADERS)
print(f"  HTTP {resp.status_code}, {len(resp.content)} bytes")
if resp.status_code != 200:
    print("  ABORT")
    sys.exit(1)

data = resp.json()
print(f"  Top-level keys ({len(data)}): {sorted(data.keys())}")

# selling
selling = data.get("selling")
print(f"\n  [selling] type={type(selling).__name__}")
if isinstance(selling, dict):
    print(f"  selling keys ({len(selling)}): {sorted(selling.keys())}")
    for k in sorted(selling.keys()):
        v = selling[k]
        if isinstance(v, (int, float)):
            print(f"    selling.{k} = {v}")
        elif isinstance(v, str) and len(v) < 120:
            print(f"    selling.{k} = {v!r}")
        elif isinstance(v, (list, dict)):
            print(f"    selling.{k} = {json.dumps(v, ensure_ascii=False)[:200]}")
        else:
            print(f"    selling.{k} = {type(v).__name__}: {str(v)[:80]}")
elif selling is None:
    print("  selling = None")

# data field
d = data.get("data")
print(f"\n  [data] type={type(d).__name__}")
if isinstance(d, dict):
    print(f"  data keys ({len(d)}): {sorted(d.keys())[:30]}")
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, (int, float)) and v > 0:
            print(f"    data.{k} = {v}")
        elif isinstance(v, str) and len(v) < 100:
            print(f"    data.{k} = {v!r}")
        elif isinstance(v, (list, dict)):
            sv = json.dumps(v, ensure_ascii=False)
            print(f"    data.{k} = {sv[:200]}")

# price-like keys at top level
print("\n  [Top-level price-like keys]")
for k in sorted(data.keys()):
    if "price" in k.lower() or "cost" in k.lower() or "amount" in k.lower():
        v = data[k]
        print(f"    {k} = {json.dumps(v, ensure_ascii=False)[:150]}")

# 3. HTML price extraction
print(f"\n--- 3. HTML price extraction ---")
html_url = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"
try:
    time.sleep(1.5)
    resp3 = cffi_req.get(html_url, impersonate="chrome", timeout=20, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    print(f"  HTTP {resp3.status_code}, {len(resp3.content)} bytes")
    if resp3.status_code == 200:
        html = resp3.text

        # og:price
        for prop in ("og:price:amount", "product:price:amount", "og:price:currency"):
            m = re.search(rf'<meta[^>]*property=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                print(f"  {prop} = {m.group(1)}")

        # data-price attributes
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
            print(f"  data-price = {m.group(1)}")
            break

        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if isinstance(item, dict) and "price" in item:
                        print(f"  JSON-LD price = {json.dumps(item['price'], ensure_ascii=False)}")
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        print(f"  JSON-LD Product: name={str(item.get('name',''))[:60]}, price={json.dumps(item.get('price'), ensure_ascii=False)[:150]}")
            except (json.JSONDecodeError, ValueError):
                pass

        # data-bulletin-price (found earlier)
        for m in re.finditer(r'data-bulletin-price=["\']([^"\']+)', html, re.I):
            print(f"  data-bulletin-price = {m.group(1)}")

        # General ₽ pattern
        prices_found = []
        for m in re.finditer(r'>\s*([\d\s\u00a0]{2,12})\s*(?:₽|руб)\s*<', html):
            try:
                amt = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                if amt > 1:
                    prices_found.append(amt)
            except ValueError:
                pass
        if prices_found:
            print(f"  HTML ₽ values: {prices_found[:10]}")

        # state JSON (window.__STATE__ or similar)
        m = re.search(r'window\.__(?:STATE|INITIAL_STATE|APP_STATE)\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
        if m:
            print(f"  window state found: {m.group(1)[:200]}")

except Exception as e:
    print(f"  HTML error: {type(e).__name__}: {e}")

# 4. Quick AVITO regex test
print(f"\n--- 4. AVITO regex verification ---")
from r52.domain.normalize import ID_PATTERNS, card_identity
avito_pat = ID_PATTERNS["AVITO"]
test_urls = [
    "https://www.avito.ru/ad/7494308705",
    "https://www.avito.ru/moskva/noutbuk-asus/7494308705",
    "https://avito.ru/ad/12345678",
    "https://www.avito.ru/moskva/phone-12345678",
]
for u in test_urls:
    m = avito_pat.search(u.split("://", 1)[1].split("/", 1)[1] if "://" in u else u)
    # Better: use urlsplit
    from urllib.parse import urlsplit
    parts = urlsplit(u)
    m = avito_pat.search(parts.path)
    found = next((g for g in m.groups() if g), None) if m else None
    status = "OK" if found == "7494308705" or found == "12345678" else "CHECK"
    print(f"  {u}")
    print(f"    path={parts.path}, match={m.group(0) if m else None}, id={found} [{status}]")

print("\n=== Done ===")
