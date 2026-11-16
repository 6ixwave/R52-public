r"""Диагностика WB Price API: проверяем card.wb.ru и basket CDN."""
import json, time, sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

print("=== WB PRICE API DIAG ===", flush=True)

# 1. Читаем локальный JSON
WB_JSON = BASE / "diag_v59_wb_199298871.json"
if not WB_JSON.exists():
    print(f"ERROR: {WB_JSON} not found", flush=True)
    sys.exit(1)

card = json.loads(WB_JSON.read_text(encoding="utf-8"))
nm_id = str(card["nm_id"])
print(f"nm_id: {nm_id}", flush=True)
print(f"title: {card.get('imt_name','?')[:60]}", flush=True)

# 2. Пробуем card.wb.ru/cards/v2/detail
print("\n--- card.wb.ru/cards/v2/detail ---", flush=True)
try:
    import requests
    url = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
    headers = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    r = requests.get(url, headers=headers, timeout=20)
    print(f"HTTP {r.status_code}, {len(r.text)} bytes", flush=True)
    if r.status_code == 200:
        data = r.json()
        products = (data.get("data") or {}).get("products") or []
        print(f"products count: {len(products)}", flush=True)
        if products:
            p = products[0]
            sizes = p.get("sizes") or []
            print(f"sizes: {len(sizes)}", flush=True)
            if sizes:
                sz = sizes[0]
                price = sz.get("price") or {}
                print(f"price: basic={price.get('basic')}, total={price.get('total')}, old={price.get('old')}, currency={price.get('currency')}", flush=True)
            # Также смотрим priceU, sizeU
            print(f"priceU: {p.get('priceU')}", flush=True)
            print(f"sizeU: {p.get('sizeU')}", flush=True)
            print(f"saleU: {p.get('saleU')}", flush=True)
        else:
            print(f"FULL JSON (first 500): {r.text[:500]}", flush=True)
    else:
        print(f"BODY (first 300): {r.text[:300]}", flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", flush=True)

# 3. Пробуем basket CDN для цены
print("\n--- basket CDN (basket-XX-wbbasket) ---", flush=True)
try:
    vol = int(nm_id) // 100000
    part = int(nm_id) // 1000
    basket_url = f"https://basket-{vol}-wbbasket.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    print(f"URL: {basket_url}", flush=True)
    r2 = requests.get(basket_url, headers=headers, timeout=20)
    print(f"HTTP {r2.status_code}, {len(r2.text)} bytes", flush=True)
    if r2.status_code == 200:
        bdata = r2.json()
        print(f"keys: {list(bdata.keys())[:15]}", flush=True)
        print(f"priceU: {bdata.get('priceU')}", flush=True)
        print(f"saleU: {bdata.get('saleU')}", flush=True)
        print(f"sizeU: {bdata.get('sizeU')}", flush=True)
        print(f"basic: {bdata.get('basic')}", flush=True)
        print(f"total: {bdata.get('total')}", flush=True)
        # price в рублях (копейки)
        pu = bdata.get("priceU") or 0
        su = bdata.get("saleU") or 0
        print(f"price_rub: {pu/100:.2f}, sale_rub: {su/100:.2f}", flush=True)
    else:
        print(f"BODY: {r2.text[:300]}", flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", flush=True)

# 4. Пробуем curl_cffi (имитация Chrome TLS)
print("\n--- curl_cffi (chrome TLS) ---", flush=True)
try:
    from curl_cffi import requests as cffi_req
    url3 = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
    r3 = cffi_req.get(url3, impersonate="chrome", timeout=20)
    print(f"HTTP {r3.status_code}, {len(r3.text)} bytes", flush=True)
    if r3.status_code == 200:
        d3 = r3.json()
        prods3 = (d3.get("data") or {}).get("products") or []
        print(f"products: {len(prods3)}", flush=True)
        if prods3:
            p3 = prods3[0]
            sz3 = (p3.get("sizes") or [{}])[0]
            pr3 = sz3.get("price") or {}
            print(f"price: basic={pr3.get('basic')}, total={pr3.get('total')}, old={pr3.get('old')}", flush=True)
            print(f"priceU={p3.get('priceU')}, saleU={p3.get('saleU')}, sizeU={p3.get('sizeU')}", flush=True)
    else:
        print(f"BODY: {r3.text[:300]}", flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", flush=True)

print("\nDONE", flush=True)
