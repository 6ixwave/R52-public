"""Диагностика: тест каждого WB price-эндпоинта по отдельности.

Запуск: python debug/scripts/diag_wb_price_endpoints.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

NM_ID = 1097975275

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
    print("HTTP: curl_cffi (impersonate=chrome)")
except ImportError:
    import requests
    USE_CFFI = False
    print("HTTP: requests (fallback)")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

ENDPOINTS = [
    ("card_v1",   f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v2",   f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v4",   f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v5",   f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("catalog_v1",f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("catalog_v2",f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("web_api",   f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    ("search_wb", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    # Новые варианты
    ("search_v2", f"https://search.wb.ru/v1/search?query=test&resultset=catalog&nm={NM_ID}"),
    ("api_cards", f"https://api.wb.ru/cards/v1/detail?nm={NM_ID}"),
]

results = {}
for label, url in ENDPOINTS:
    print(f"\n{'='*60}")
    print(f"  {label}: {url[:80]}")
    t0 = time.monotonic()
    try:
        if USE_CFFI:
            resp = cffi_req.get(url, headers=HEADERS, impersonate="chrome", timeout=12)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=12)
        elapsed = round(time.monotonic() - t0, 1)
        body = resp.text[:500]
        print(f"  Status: {resp.status_code}  Time: {elapsed}s  Body[:200]: {body[:200]}")
        
        # Try JSON parse
        if resp.status_code == 200:
            try:
                data = resp.json()
                # Extract price info
                products = data.get("products") or data.get("data", {}).get("items", [])
                if products:
                    p = products[0]
                    sizes = p.get("sizes", [])
                    print(f"  Product: name={str(p.get('name',''))[:60]}")
                    print(f"  Sizes: {len(sizes)}")
                    for i, s in enumerate(sizes[:2]):
                        price = s.get("price", {})
                        qty = s.get("qty")
                        print(f"    size[{i}]: price={json.dumps(price, ensure_ascii=False)[:200]} qty={qty}")
                elif "data" in data and isinstance(data["data"], dict):
                    d = data["data"]
                    prices = d.get("prices") or d.get("price")
                    print(f"  data.prices: {json.dumps(prices, ensure_ascii=False)[:200] if prices else 'None'}")
                    print(f"  data keys: {list(d.keys())[:10]}")
                else:
                    print(f"  JSON keys: {list(data.keys())[:10]}")
                    print(f"  JSON[:300]: {json.dumps(data, ensure_ascii=False)[:300]}")
                results[label] = {"status": 200, "has_price": bool(products and products[0].get("sizes") and products[0]["sizes"][0].get("price"))}
            except Exception as e:
                print(f"  JSON parse error: {e}")
                results[label] = {"status": 200, "has_price": False, "error": str(e)}
        else:
            results[label] = {"status": resp.status_code, "has_price": False}
    except Exception as e:
        elapsed = round(time.monotonic() - t0, 1)
        print(f"  EXCEPTION: {type(e).__name__}: {str(e)[:150]}  Time: {elapsed}s")
        results[label] = {"status": "EXC", "has_price": False, "error": str(e)[:100]}
    
    time.sleep(1.5)

print(f"\n{'='*60}")
print("SUMMARY:")
for label, r in results.items():
    mark = "✓" if r.get("has_price") else "✗"
    print(f"  {mark} {label}: status={r['status']}")

# Save
out = ROOT / "debug" / "outputs" / "wb_price_endpoints_report.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
