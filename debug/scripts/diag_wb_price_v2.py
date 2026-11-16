"""Диагностика v2: поиск рабочего price-пути для WB.

Тестирует:
1. search.wildberries.ru/web/search (как в collect_by_search)
2. card.wb.ru с dest=-18986 и полными browser headers
3. card.wb.ru с dest=-1257786 но с sec-ch-ua
4. basket CDN: есть ли отдельный price.json?
5. Мобильный API: m.wildberries.ru

Запуск: python debug/scripts/diag_wb_price_v2.py
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

NM_ID = 1097975275
PRODUCT_NAME = "Смартфон iPhone 17 Pro Max 256 Серебряный"

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
    print("HTTP: curl_cffi (impersonate=chrome)")
except ImportError:
    import requests
    USE_CFFI = False
    print("HTTP: requests")

# Полные браузерные заголовки
FULL_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

# HTML headers
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

results = {}

def fetch(label, url, headers, timeout=12):
    t0 = time.monotonic()
    try:
        if USE_CFFI:
            resp = cffi_req.get(url, headers=headers, impersonate="chrome", timeout=timeout)
        else:
            resp = requests.get(url, headers=headers, timeout=timeout)
        elapsed = round(time.monotonic() - t0, 1)
        body_preview = resp.text[:300] if resp.status_code == 200 else resp.text[:200]
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"  URL: {url[:90]}")
        print(f"  Status: {resp.status_code}  Time: {elapsed}s  Size: {len(resp.content)}B")
        print(f"  Body[:200]: {body_preview[:200]}")
        return resp
    except Exception as e:
        elapsed = round(time.monotonic() - t0, 1)
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"  URL: {url[:90]}")
        print(f"  EXCEPTION: {type(e).__name__}: {str(e)[:120]}  Time: {elapsed}s")
        return None

def extract_price_info(resp, label):
    """Извлекает цену из JSON-ответа."""
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    
    info = {"label": label}
    
    # Pattern 1: {"products": [{"sizes": [{"price": {...}, "qty": N}]}]}
    products = data.get("products") or []
    if products and isinstance(products, list):
        p = products[0]
        sizes = p.get("sizes", [])
        info["name"] = p.get("name", "")[:60]
        info["sizes_count"] = len(sizes)
        if sizes:
            s = sizes[0]
            info["price"] = s.get("price", {})
            info["qty"] = s.get("qty")
        return info
    
    # Pattern 2: {"data": {"products": [...]}}
    d = data.get("data", {})
    if isinstance(d, dict):
        products = d.get("products") or d.get("items") or []
        if products:
            p = products[0]
            info["name"] = p.get("name", "")[:60]
            sizes = p.get("sizes", [])
            info["sizes_count"] = len(sizes)
            if sizes:
                info["price"] = sizes[0].get("price", {})
                info["qty"] = sizes[0].get("qty")
            else:
                # Maybe flat price
                info["price"] = p.get("price") or d.get("price")
            return info
    
    # Pattern 3: flat
    info["keys"] = list(data.keys())[:15]
    info["price"] = data.get("price") or data.get("prices")
    return info


# ============================================================
# 1. SEARCH API (same as collect_by_search)
# ============================================================
print("\n" + "#"*60)
print("# 1. SEARCH API: search.wildberries.ru/web/search")
print("#"*60)
from urllib.parse import quote
search_url = (f"https://search.wildberries.ru/web/search"
              f"?query={quote(PRODUCT_NAME)}&resultset=catalog&sort=popular&page=1&dest=-18986")
resp = fetch("search_web", search_url, FULL_HEADERS, timeout=15)
if resp and resp.status_code == 200:
    info = extract_price_info(resp, "search_web")
    if info:
        print(f"  EXTRACTED: {json.dumps(info, ensure_ascii=False)[:400]}")
        results["search_web"] = info
    # Also dump raw structure
    try:
        data = resp.json()
        d = data.get("data", data)
        products = d.get("products", []) if isinstance(d, dict) else []
        print(f"  Products found: {len(products)}")
        if products:
            p0 = products[0]
            print(f"  p0 keys: {list(p0.keys())[:20]}")
            print(f"  p0.id={p0.get('id')}, p0.nm_id={p0.get('nm_id')}")
            print(f"  p0.price={json.dumps(p0.get('price'), ensure_ascii=False)[:200]}")
            print(f"  p0.sizes={json.dumps(p0.get('sizes'), ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"  Parse: {e}")
time.sleep(1)

# ============================================================
# 2. CARD.WB.RU with dest=-18986 + full headers
# ============================================================
print("\n" + "#"*60)
print("# 2. CARD.WB.RU v2, dest=-18986, full headers")
print("#"*60)
url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-18986&spp=30&nm={NM_ID}"
resp = fetch("card_v2_18986", url, FULL_HEADERS, timeout=12)
if resp and resp.status_code == 200:
    info = extract_price_info(resp, "card_v2_18986")
    if info:
        print(f"  EXTRACTED: {json.dumps(info, ensure_ascii=False)[:400]}")
        results["card_v2_18986"] = info
time.sleep(1)

# ============================================================
# 3. CARD.WB.RU v1, dest=-18986
# ============================================================
print("\n" + "#"*60)
print("# 3. CARD.WB.RU v1, dest=-18986")
print("#"*60)
url = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-18986&spp=30&nm={NM_ID}"
resp = fetch("card_v1_18986", url, FULL_HEADERS, timeout=12)
if resp and resp.status_code == 200:
    info = extract_price_info(resp, "card_v1_18986")
    if info:
        print(f"  EXTRACTED: {json.dumps(info, ensure_ascii=False)[:400]}")
        results["card_v1_18986"] = info
time.sleep(1)

# ============================================================
# 4. BASKET CDN: check for price.json or similar
# ============================================================
print("\n" + "#"*60)
print("# 4. BASKET CDN: alternative JSON paths")
print("#"*60)
vol = NM_ID // 100000
part = NM_ID // 1000
host = f"basket-42.wbbasket.ru"  # known from earlier

basket_paths = [
    f"/vol{vol}/part{part}/{NM_ID}/info/ru/card.json",  # known good
    f"/vol{vol}/part{part}/{NM_ID}/info/ru/price.json",
    f"/vol{vol}/part{part}/{NM_ID}/info/ru/offer.json",
    f"/vol{vol}/part{part}/{NM_ID}/info/ru/size.json",
]
for bp in basket_paths:
    url = f"https://{host}{bp}"
    resp = fetch(f"basket_{bp.split('/')[-1]}", url, FULL_HEADERS, timeout=8)
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            keys = list(data.keys())[:15] if isinstance(data, dict) else f"list[{len(data)}]"
            print(f"  KEYS: {keys}")
            # Check for price
            price_keys = [k for k in (data.keys() if isinstance(data, dict) else []) if "price" in k.lower() or "cost" in k.lower()]
            if price_keys:
                print(f"  PRICE KEYS: {price_keys}")
                for pk in price_keys:
                    print(f"    {pk} = {repr(data[pk])[:150]}")
            results[f"basket_{bp.split('/')[-1]}"] = {"status": 200, "keys": keys}
        except Exception as e:
            print(f"  Parse: {e}")
    time.sleep(0.5)

# ============================================================
# 5. MOBILE / ALTERNATE: m.wildberries.ru or wb.ru
# ============================================================
print("\n" + "#"*60)
print("# 5. ALTERNATE HOSTS")
print("#"*60)

# 5a. wb.ru (redirect?)
url = f"https://wb.ru/catalog/{NM_ID}/detail.aspx"
resp = fetch("wb_ru_html", url, HTML_HEADERS, timeout=12)
if resp:
    print(f"  Final URL: {resp.url if hasattr(resp, 'url') else 'n/a'}")

# 5b. search.wb.ru with different path
url = f"https://search.wb.ru/v1/search?query={quote(PRODUCT_NAME)}&resultset=catalog&dest=-18986"
resp = fetch("search_wb_v1", url, FULL_HEADERS, timeout=12)
if resp and resp.status_code == 200:
    info = extract_price_info(resp, "search_wb_v1")
    if info:
        print(f"  EXTRACTED: {json.dumps(info, ensure_ascii=False)[:400]}")
        results["search_wb_v1"] = info

# 5c. catalog.wb.ru with full headers
url = f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-18986&spp=30&nm={NM_ID}"
resp = fetch("catalog_v2_full", url, FULL_HEADERS, timeout=12)
if resp and resp.status_code == 200:
    info = extract_price_info(resp, "catalog_v2_full")
    if info:
        print(f"  EXTRACTED: {json.dumps(info, ensure_ascii=False)[:400]}")
        results["catalog_v2_full"] = info

time.sleep(1)

# ============================================================
# 6. SEARCH with nm_id filter (most promising)
# ============================================================
print("\n" + "#"*60)
print("# 6. SEARCH with specific nm_id (filter)")
print("#"*60)
url = (f"https://search.wildberries.ru/web/search"
       f"?query={quote(str(NM_ID))}&resultset=catalog&sort=popular&page=1&dest=-18986")
resp = fetch("search_by_id", url, FULL_HEADERS, timeout=15)
if resp and resp.status_code == 200:
    try:
        data = resp.json()
        d = data.get("data", data)
        products = d.get("products", []) if isinstance(d, dict) else []
        print(f"  Products: {len(products)}")
        for i, p in enumerate(products[:3]):
            pid = p.get("id") or p.get("nm_id")
            price = p.get("price", {})
            print(f"  [{i}] id={pid} price={json.dumps(price, ensure_ascii=False)[:150]}")
            if str(pid) == str(NM_ID):
                results["search_by_id"] = {"found": True, "price": price}
                print(f"  >>> MATCH! Full: {json.dumps(p, ensure_ascii=False)[:500]}")
                break
    except Exception as e:
        print(f"  Parse: {e}")

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*60}")
print("SUMMARY:")
for label, r in results.items():
    has_price = "price" in str(r) and "None" not in str(r.get("price", "None"))
    mark = "✓" if has_price else "✗"
    print(f"  {mark} {label}: {json.dumps(r, ensure_ascii=False)[:120]}")

# Save
out = ROOT / "debug" / "outputs" / "wb_price_v2_report.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
