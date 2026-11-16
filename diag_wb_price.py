"""Быстрый тест альтернативных WB price-эндпоинтов."""
import json, time, logging, sys
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("wb_price")

NM_ID = 199298871
VOL = NM_ID // 100000  # 1992

try:
    from curl_cffi import requests as creq
    HAVE_CFFI = True
except ImportError:
    HAVE_CFFI = False
    import requests as creq

def get(url, label, use_cffi=True, timeout=12):
    log.info("TRY %s: %s", label, url[:100])
    try:
        if use_cffi and HAVE_CFFI:
            r = creq.get(url, impersonate="chrome", timeout=timeout)
        else:
            r = creq.get(url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
        log.info("  -> %d, %dB", r.status_code, len(r.content))
        if r.status_code == 200:
            try:
                data = r.json()
                log.info("  JSON keys: %s", list(data.keys())[:10])
                return data
            except Exception:
                log.info("  Not JSON: %s", r.text[:200])
                return None
        else:
            log.info("  body[:150]=%s", r.text[:150])
            return None
    except Exception as e:
        log.warning("  ERROR: %s", e)
        return None
    time.sleep(0.5)

print(f"=== WB PRICE ALT TEST (nm={NM_ID}, vol={VOL}) ===")
print(f"curl_cffi: {HAVE_CFFI}")

# 1. Basket CDN: info.json / price.json
basket = f"https://basket-{VOL}.wbbasket.ru"
endpoints = [
    (f"basket_info", f"{basket}/vol{VOL}/part{NM_ID}/{NM_ID}-info.json"),
    (f"basket_price", f"{basket}/vol{VOL}/part{NM_ID}/{NM_ID}-price.json"),
    (f"basket_cards", f"{basket}/cards/{NM_ID}.json"),
    # 2. WB v2 API
    ("wb_v2_api", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM_ID}&curr=rub"),
    ("wb_v1_api_curr", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}&curr=rub&dest=-1257786"),
    # 3. Newer /api/ path
    ("wb_new_api", f"https://www.wildberries.ru/api/cards/v1/detail?nm={NM_ID}"),
    # 4. card.wb.ru v5 with mobile params
    ("card_v5_mobile", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}&nmId={NM_ID}"),
    # 5. search.wb.ru with different format
    ("search_v2", f"https://search.wb.ru/v1/search?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    # 6. basket CDN vol/part structure
    ("basket_volpart", f"https://basket-{VOL}.wbbasket.ru/vol{VOL}/part{NM_ID}/{NM_ID}.json"),
]

for label, url in endpoints:
    data = get(url, label)
    if data:
        # Look for price fields
        s = json.dumps(data, ensure_ascii=False)
        for key in ["price", "priceU", "priceD", "salePriceU", "salePriceD", "sizes", "totalQty"]:
            if key in s:
                log.info("  FOUND '%s' in response!", key)
        # Try to extract
        products = data.get("products") or data.get("data", {}).get("items") or data.get("items")
        if products:
            p = products[0] if isinstance(products, list) else products
            log.info("  PRODUCT: %s", json.dumps(p, ensure_ascii=False)[:300])
            break
        else:
            # Maybe flat structure
            if "priceU" in data or "sizes" in data:
                log.info("  FLAT: %s", json.dumps(data, ensure_ascii=False)[:300])
                break

print("=== DONE ===")
