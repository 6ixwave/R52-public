"""Диагностика WB Price API v2: проверка всех эндпоинтов и card.json полей."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

NM_IDS = [138278567, 145567890]

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
    print(f"[OK] curl_cffi доступен")
except ImportError:
    import requests
    cffi_req = None
    USE_CFFI = False
    print(f"[WARN] curl_cffi недоступен, используем requests")

def http_get(url, headers=None, timeout=12):
    if USE_CFFI:
        kw = {"impersonate": "chrome", "timeout": timeout}
        if headers:
            kw["headers"] = headers
        try:
            return cffi_req.get(url, **kw)
        except Exception as e:
            print(f"  [ERR] cffi: {e}")
            return None
    else:
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
        if headers:
            h.update(headers)
        try:
            return requests.get(url, headers=h, timeout=timeout)
        except Exception as e:
            print(f"  [ERR] req: {e}")
            return None

JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

print("=" * 70)
print("WB PRICE DIAGNOSTIC v2")
print("=" * 70)

for nm_id in NM_IDS:
    print(f"\n{'='*60}")
    print(f"NM_ID = {nm_id}")
    print(f"{'='*60}")
    
    vol = nm_id // 100000
    part = nm_id // 1000
    
    # --- 1. Card.json: ищем все price-подобные поля ---
    print(f"\n--- 1. Card.json (vol={vol}, part={part}) ---")
    found_host = None
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        resp = http_get(url, headers=JSON_H, timeout=8)
        if resp and resp.status_code == 200:
            found_host = host
            break
        time.sleep(0.3)
    
    if found_host:
        print(f"  Host: {found_host}")
        try:
            card = resp.json()
            print(f"  Keys: {sorted(card.keys())}")
            # Ищем все числовые поля с "price" в имени
            for k, v in card.items():
                if "price" in k.lower() or "cost" in k.lower():
                    print(f"  [PRICE] {k} = {v}")
            # selling
            selling = card.get("selling", {})
            if selling:
                print(f"  selling keys: {sorted(selling.keys())}")
                for k, v in selling.items():
                    if isinstance(v, (int, float)) and v > 0:
                        print(f"  [SELLING] {k} = {v}")
                    elif "price" in k.lower():
                        print(f"  [SELLING] {k} = {v}")
            # data
            data = card.get("data", {})
            if data:
                print(f"  data keys: {sorted(data.keys())[:20]}")
                for k, v in data.items():
                    if "price" in k.lower() or "cost" in k.lower():
                        print(f"  [DATA] {k} = {v}")
            # Топ-уровень: все int/float > 0
            print(f"  Top-level numeric fields:")
            for k, v in card.items():
                if isinstance(v, (int, float)) and v > 0 and not isinstance(v, bool):
                    print(f"    {k} = {v}")
        except Exception as e:
            print(f"  Parse error: {e}")
    else:
        print(f"  [FAIL] Host не найден")
    
    # --- 2. Price API endpoints ---
    print(f"\n--- 2. Price API ---")
    price_endpoints = [
        ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v5", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("web_api_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
        ("web_api_v2", f"https://www.wildberries.ru/web/api/v1/cards/{nm_id}"),
        ("common_v1", f"https://common-api.wildberries.ru/api/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ]
    
    for label, url in price_endpoints:
        resp = http_get(url, headers=JSON_H, timeout=10)
        if resp is None:
            print(f"  [{label}] -> None (exception)")
            continue
        sc = resp.status_code
        if sc == 200:
            try:
                d = resp.json()
                # Разные форматы
                products = d.get("products") or d.get("data", {}).get("items") or d.get("data")
                if isinstance(products, list) and products:
                    p = products[0]
                    prices = p.get("prices") or p.get("price")
                    qty = p.get("qty")
                    name = p.get("name", "")[:40]
                    print(f"  [{label}] 200 OK | name={name} | qty={qty} | prices={json.dumps(prices, ensure_ascii=False) if prices else 'None'}")
                    # sizes
                    sizes = p.get("sizes", [])
                    if sizes:
                        s0 = sizes[0]
                        sp = s0.get("price", {})
                        print(f"    sizes[0].price = {json.dumps(sp, ensure_ascii=False)}")
                elif isinstance(products, dict):
                    prices = products.get("prices") or products.get("price")
                    print(f"  [{label}] 200 OK (dict) | keys={list(products.keys())[:10]} | prices={json.dumps(prices, ensure_ascii=False) if prices else 'None'}")
                else:
                    print(f"  [{label}] 200 | top keys={list(d.keys())[:10]} | body[:200]={resp.text[:200]}")
            except (json.JSONDecodeError, ValueError) as e:
                print(f"  [{label}] 200 but parse err: {e} | body[:100]={resp.text[:100]}")
        else:
            print(f"  [{label}] -> HTTP {sc}")
        time.sleep(1)
    
    # --- 3. HTML: og:price, data-bulletin-price ---
    print(f"\n--- 3. HTML (og:price, data-price) ---")
    html_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    resp = http_get(html_url, headers={"Accept": "text/html", "Accept-Language": "ru-RU"}, timeout=15)
    if resp and resp.status_code == 200:
        html = resp.text
        print(f"  HTML size: {len(html)} bytes")
        import re
        # og:price
        for m in re.finditer(r'<meta[^>]*(?:property|name)=["\']og:price[^"\']*["\'][^>]*content=["\']([^"\']+)', html, re.I):
            print(f"  og:price = {m.group(1)}")
        # data-bulletin-price
        for m in re.finditer(r'data-bulletin-price=["\']([^"\']+)', html, re.I):
            print(f"  data-bulletin-price = {m.group(1)}")
        # data-price
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
            print(f"  data-price = {m.group(1)}")
        # embedded "price": {"basic": ...}
        for m in re.finditer(r'"price"\s*:\s*\{([^}]{0,200})\}', html):
            print(f"  embedded price obj = {{{m.group(1)}}}")
            break
        # priceU / priceD
        for pk in ("priceU", "priceD", "oldPrice"):
            for m in re.finditer(rf'"{pk}"\s*:\s*([\d.]+)', html):
                print(f"  {pk} = {m.group(1)}")
                break
        # visible text price
        for m in re.finditer(r'>\s*([\d\s\u00a0]{2,12})\s*(?:₽|руб)\s*<', html):
            print(f"  visible text: {m.group(1).strip()} ₽")
            break
    elif resp:
        print(f"  HTML: HTTP {resp.status_code}")
    else:
        print(f"  HTML: None")

print(f"\n{'='*70}")
print("DONE")
print(f"{'='*70}")
