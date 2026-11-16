"""Диагностика WB: пробуем публичные endpoints для карточки товара."""
import json, sys, time, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

WB_IDS = ["199298871", "314337853"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

try:
    import requests
    session = requests.Session()
    session.headers.update(HEADERS)
    ENGINE = "requests"
except ImportError:
    ENGINE = "none"
    session = None

results = []

def probe(label, url, expect_json=False):
    print(f"\n{'='*60}")
    print(f"[{label}] {url}")
    t0 = time.monotonic()
    try:
        r = session.get(url, timeout=15, allow_redirects=True)
        elapsed = time.monotonic() - t0
        print(f"  Status: {r.status_code}, {len(r.content)} bytes, {elapsed:.1f}s")
        print(f"  Headers: Content-Type={r.headers.get('Content-Type','?')}")
        if r.status_code == 200:
            text = r.text
            if expect_json:
                try:
                    data = r.json()
                    print(f"  JSON keys: {list(data.keys())[:10]}")
                    return data
                except Exception as e:
                    print(f"  JSON parse fail: {e}")
            # Проверка JSON-LD
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"  JSON-LD blocks: {len(ld)}")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"    [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                        elif isinstance(obj, list):
                            for j, item in enumerate(obj[:3]):
                                print(f"    [{i}.{j}] @type={item.get('@type','?')} name={str(item.get('name',''))[:60]}")
                    except:
                        print(f"    [{i}] parse fail, len={len(block)}")
            # Проверка og: meta
            og = re.findall(r'<meta[^>]*property=["\']og:(?:title|price:amount|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"  OG meta: {og[:5]}")
            # Проверка __STATE__ или window.__
            state = re.search(r'window\.__STATE__\s*=\s*({.*?});', text, re.DOTALL)
            if state:
                print(f"  __STATE__ found, len={len(state.group(1))}")
            return text
        else:
            body = r.text[:500]
            print(f"  Body preview: {body[:300]}")
            return None
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")
        return None

# --- 1. card.wb.ru API ---
for wid in WB_IDS:
    url = f"https://card.wb.ru/cards/v2/detail?nm={wid}"
    data = probe(f"WB card API v2 id={wid}", url, expect_json=True)
    if data:
        products = data.get("data", {}).get("products", [])
        if products:
            p = products[0]
            print(f"  Product: name={p.get('name','?')[:60]} brand={p.get('brand','?')}")
            sizes = p.get("sizes", [])
            if sizes:
                s = sizes[0]
                price = s.get("price", {})
                print(f"  Price: basic={price.get('basic',{}).get('v')} discount={price.get('discount',{}).get('v')} currency={price.get('basic',{}).get('r',0)}")
                print(f"  Supply: {s.get('supply')}")
            imgs = p.get("im", [])
            print(f"  Images: {len(imgs)}")
            results.append({"id": wid, "method": "card_api_v2", "status": "OK", "name": p.get("name","")[:60]})
        else:
            results.append({"id": wid, "method": "card_api_v2", "status": "NO_PRODUCTS"})
    else:
        results.append({"id": wid, "method": "card_api_v2", "status": "FAIL"})

# --- 2. HTML detail page ---
for wid in WB_IDS:
    url = f"https://www.wildberries.ru/catalog/{wid}/detail.aspx"
    text = probe(f"WB HTML id={wid}", url)
    if text:
        results.append({"id": wid, "method": "html", "status": "OK"})
    else:
        results.append({"id": wid, "method": "html", "status": "FAIL"})

# --- 3. card.wb.ru v1 (legacy) ---
for wid in WB_IDS:
    url = f"https://card.wb.ru/cards/v1/detail?nm={wid}"
    data = probe(f"WB card API v1 id={wid}", url, expect_json=True)
    if data:
        results.append({"id": wid, "method": "card_api_v1", "status": "OK"})
    else:
        results.append({"id": wid, "method": "card_api_v1", "status": "FAIL"})

# --- Summary ---
print(f"\n{'='*60}")
print("WB PROBE SUMMARY:")
for r in results:
    print(f"  {r}")

# Save raw results for further processing
out = Path(__file__).parent / "diag_wb_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
