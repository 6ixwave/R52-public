"""v102: Точечная диагностика WB — basket CDN + price + HTML.

Проверяет для nm_id=259671175:
1. Basket CDN card.json (какой хост, что в ответе)
2. card.wb.ru v1/v2/v4/v5
3. www.wildberries.ru/web/api
4. HTML detail.aspx → og:price:amount, data-price, JSON-LD

Выводит структурированный отчёт. Без bs4/lxml — только re + json.
"""
import json
import re
import sys
import time
from pathlib import Path

# --- HTTP ---
try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    print("[OK] curl_cffi доступен")
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req
    print("[WARN] curl_cffi нет, fallback requests")

import requests as std_req

NM_ID = 259671175
VOL = NM_ID // 100000  # 2596
PART = (NM_ID // 1000) % 1000  # 259
CARD_PATH = f"/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"
DETAIL_URL = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"

JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

report = {}

def http_get(url, headers=None, timeout=15, use_cffi=True):
    """GET, возвращает (status_code, body_text, body_bytes_len)."""
    if use_cffi and HAS_CFFI:
        kw = {"impersonate": "chrome", "timeout": timeout}
        if headers:
            kw["headers"] = headers
        try:
            r = cffi_req.get(url, **kw)
            return r.status_code, r.text, len(r.content)
        except Exception as e:
            return -1, str(e), 0
    else:
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        if headers:
            h.update(headers)
        try:
            r = std_req.get(url, headers=h, timeout=timeout)
            return r.status_code, r.text, len(r.content)
        except Exception as e:
            return -1, str(e), 0

# ============================================================
# 1. BASKET CDN
# ============================================================
print("\n" + "="*60)
print("1. BASKET CDN")
print("="*60)

basket_result = None
found_host = None
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}{CARD_PATH}"
    t0 = time.monotonic()
    code, body, blen = http_get(url, JSON_HEADERS, timeout=8)
    dt = time.monotonic() - t0
    if code == 200:
        found_host = host
        try:
            data = json.loads(body)
            if data.get("nm_id") == NM_ID:
                basket_result = data
                print(f"  [OK] {host} vol={VOL} part={PART} nm_id={NM_ID}")
                print(f"       keys={list(data.keys())[:15]}")
                print(f"       imt_name={data.get('imt_name','?')[:60]}")
                print(f"       brand={data.get('selling',{}).get('brand_name','?')}")
                print(f"       photo_count={data.get('media',{}).get('photo_count','?')}")
                print(f"       size={blen}B time={dt:.1f}s")
                # Проверяем есть ли цена внутри card.json
                price_keys = [k for k in data.keys() if 'price' in k.lower() or 'cost' in k.lower()]
                print(f"       price_keys_in_card={price_keys}")
                break
            else:
                print(f"  [WARN] {host}: 200 но nm_id={data.get('nm_id')} != {NM_ID}")
        except json.JSONDecodeError:
            print(f"  [WARN] {host}: 200 но не JSON, body[:80]={body[:80]}")
    elif code in (404, 403):
        pass  # тихо
    elif code == -1:
        print(f"  [ERR] {host}: exception {body[:60]}")
    else:
        print(f"  [INFO] {host}: HTTP {code}")
    time.sleep(0.3)

if not found_host:
    print("  [FAIL] basket host не найден (1-60)")
else:
    print(f"  [DONE] host={found_host} за ~{time.monotonic()-t0:.1f}s")

report["basket"] = {
    "host": found_host,
    "found": basket_result is not None,
    "keys": list(basket_result.keys()) if basket_result else [],
}

# ============================================================
# 2. PRICE ENDPOINTS
# ============================================================
print("\n" + "="*60)
print("2. PRICE ENDPOINTS")
print("="*60)

price_found = None
price_source = None

endpoints = []
for ver in ["v1", "v2", "v4", "v5"]:
    endpoints.append((
        f"card_{ver}",
        f"https://card.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}",
    ))
endpoints.append((
    "web_api_v1",
    f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}",
))
endpoints.append((
    "web_api_v2",
    f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM_ID}",
))

for label, url in endpoints:
    print(f"\n  --- {label} ---")
    t0 = time.monotonic()
    code, body, blen = http_get(url, JSON_HEADERS, timeout=12)
    dt = time.monotonic() - t0
    print(f"  HTTP {code} | {blen}B | {dt:.1f}s")
    
    if code == 200:
        try:
            data = json.loads(body)
            # card.wb.ru format
            products = data.get("products") or data.get("data", {}).get("items", [])
            if products:
                p = products[0]
                sizes = p.get("sizes", [])
                print(f"  name={p.get('name','?')[:50]}")
                print(f"  sizes_count={len(sizes)}")
                for sz in sizes[:3]:
                    pr = sz.get("price", {})
                    qty = sz.get("qty")
                    print(f"    size={sz.get('name','?')} qty={qty} price={json.dumps(pr, ensure_ascii=False)[:120]}")
                    if pr and not price_found:
                        price_found = pr
                        price_source = f"{label}.sizes[0].price"
            # web_api format
            if "data" in data and isinstance(data["data"], dict):
                d = data["data"]
                prices = d.get("prices") or d.get("price")
                if prices:
                    print(f"  web_api data.prices={json.dumps(prices, ensure_ascii=False)[:200]}")
                    if not price_found:
                        price_found = prices
                        price_source = f"{label}.data.prices"
            # Fallback: ищем любые числовые поля
            if not price_found:
                flat = json.dumps(data)
                price_matches = re.findall(r'"(?:price|basic|product|card|wallet|old|crossed|bankCard)"\s*:\s*(\d+)', flat)
                if price_matches:
                    print(f"  regex prices: {price_matches[:10]}")
                    price_found = {f"regex_{i}": int(m) for i, m in enumerate(price_matches[:5])}
                    price_source = f"{label}.regex"
        except json.JSONDecodeError:
            print(f"  не JSON: {body[:100]}")
    elif code in (403, 498):
        print(f"  BLOCKED (не обходим)")
    else:
        print(f"  body[:100]={body[:100]}")
    time.sleep(1)

print(f"\n  [RESULT] price_found={price_found is not None} source={price_source}")
if price_found:
    print(f"  [DATA] {json.dumps(price_found, ensure_ascii=False)[:300]}")

report["price"] = {
    "found": price_found is not None,
    "source": price_source,
    "data": price_found,
}

# ============================================================
# 3. HTML (og:price, data-price, JSON-LD)
# ============================================================
print("\n" + "="*60)
print("3. HTML DETAIL PAGE")
print("="*60)

code, html, blen = http_get(DETAIL_URL, HTML_HEADERS, timeout=20)
print(f"  HTTP {code} | {blen}B")

html_prices = []
html_title = None
html_og_price = None

if code == 200 and len(html) > 1000:
    # og:price:amount
    m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]*content=["\']([\d.]+)["\'][^>]*property=["\']og:price:amount["\']', html, re.I)
    if m:
        html_og_price = m.group(1)
        minor = int(round(float(m.group(1)) * 100))
        html_prices.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "og:price:amount"})
        print(f"  og:price:amount = {m.group(1)} ({minor} коп)")

    # og:title
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        html_title = m.group(1).strip()
        print(f"  og:title = {html_title[:80]}")

    # data-price attributes
    for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
        minor = int(m.group(1))
        if minor > 100:
            html_prices.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "data-price"})
    if html_prices and html_prices[-1].get("label") == "data-price":
        print(f"  data-price: {len([p for p in html_prices if p['label']=='data-price'])} found")

    # JSON-LD
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    print(f"  JSON-LD Product: name={item.get('name','?')[:50]}")
                    offers = item.get("offers", {})
                    if isinstance(offers, dict):
                        price_val = offers.get("price") or offers.get("lowPrice")
                        if price_val:
                            minor = int(round(float(price_val) * 100))
                            html_prices.append({"role": "regular", "amount_minor": minor,
                                               "currency": offers.get("priceCurrency", "RUB"),
                                               "label": "jsonld.offers.price"})
                            print(f"    offers.price={price_val} currency={offers.get('priceCurrency','?')}")
                    elif isinstance(offers, list):
                        for o in offers[:2]:
                            pv = o.get("price")
                            if pv:
                                minor = int(round(float(pv) * 100))
                                html_prices.append({"role": "regular", "amount_minor": minor,
                                                   "currency": o.get("priceCurrency", "RUB"),
                                                   "label": "jsonld.offers[].price"})
        except (json.JSONDecodeError, ValueError):
            pass

    # window.__STATE__ / __NEXT_DATA__
    m = re.search(r'window\.__(?:STATE|NEXT_DATA|PRELOADED_STATE)["\']?\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
    if m:
        print(f"  __STATE__ found, len={len(m.group(1))}")
        # Ищем price в состоянии
        state_prices = re.findall(r'"(?:price|basic|product)"\s*:\s*(\d+)', m.group(1))
        if state_prices:
            print(f"  state prices: {state_prices[:10]}")

    # h1
    if not html_title:
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
        if m:
            html_title = m.group(1).strip()
            print(f"  h1 = {html_title[:80]}")

    print(f"\n  [HTML PRICES] {len(html_prices)} найдено:")
    for p in html_prices[:5]:
        print(f"    {p}")
else:
    print(f"  [WARN] HTML не получен или слишком короткий")

report["html"] = {
    "status": code,
    "size": blen,
    "title": html_title,
    "og_price": html_og_price,
    "prices": html_prices,
}

# ============================================================
# 4. ИТОГ
# ============================================================
print("\n" + "="*60)
print("4. ИТОГ")
print("="*60)

all_prices = []
if price_found:
    all_prices.append(("API", price_source, price_found))
if html_prices:
    all_prices.append(("HTML", "og/data-price/jsonld", html_prices))

print(f"  Цена получена: {'ДА' if all_prices else 'НЕТ'}")
for src, method, data in all_prices:
    print(f"    [{src}] {method}: {json.dumps(data, ensure_ascii=False)[:200]}")

# Сохраняем отчёт
out_path = Path(__file__).parent / "probe_v102_report.json"
out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\n  Отчёт: {out_path}")

# Сохраняем card.json если получили
if basket_result:
    card_path = Path(__file__).parent / f"card_{NM_ID}_v102.json"
    card_path.write_text(json.dumps(basket_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Card: {card_path}")

print("\n[DONE] v102 завершён")
