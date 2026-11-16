"""Диагностика цен WB: card.json + price endpoints."""
import sys, json, time
sys.path.insert(0, '.')

from curl_cffi import requests as cffi_req

LID = 199298871
H_JSON = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Referer': 'https://www.wildberries.ru/',
    'Origin': 'https://www.wildberries.ru',
}

print("=" * 60)
print("1. CARD.JSON — поиск цен внутри")
print("=" * 60)

vol = LID // 100000
part = LID // 1000
path = f"/vol{vol}/part{part}/{LID}/info/ru/card.json"

# Найти basket host
host = None
for n in range(1, 61):
    h = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{h}{path}"
    t0 = time.monotonic()
    try:
        r = cffi_req.get(url, headers=H_JSON, impersonate='chrome', timeout=8)
        dt = time.monotonic() - t0
        if r.status_code == 200:
            host = h
            print(f"  Found: {h} ({dt:.1f}s)")
            break
        else:
            print(f"  {h}: {r.status_code} ({dt:.1f}s)")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"  {h}: ERR ({dt:.1f}s) {str(e)[:60]}")
    if n >= 10:
        print("  ... (проверяю до 60)")

if host:
    card = r.json()
    print(f"\n  card.json keys: {list(card.keys())[:20]}")
    
    # selling
    selling = card.get('selling', {})
    if selling:
        print(f"\n  selling: {json.dumps(selling, ensure_ascii=False)[:500]}")
    
    # sizes with prices
    sizes = card.get('sizes', [])
    print(f"\n  sizes count: {len(sizes)}")
    for i, sz in enumerate(sizes[:3]):
        if isinstance(sz, dict):
            print(f"  size[{i}]: price={sz.get('price')}, qty={sz.get('qty')}")
    
    # top-level price fields
    for pk in ('price', 'priceU', 'priceD', 'oldPrice', 'salePriceU'):
        pv = card.get(pk)
        if pv is not None:
            print(f"  card.{pk} = {pv}")
    
    # media
    media = card.get('media', {})
    print(f"\n  media: photo_count={media.get('photo_count')}, has_video={media.get('has_video')}")
else:
    print("  BASKET HOST NOT FOUND")

print("\n" + "=" * 60)
print("2. PRICE ENDPOINTS")
print("=" * 60)

endpoints = [
    ('card_v1', f'https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
    ('card_v2', f'https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
    ('card_v4', f'https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
    ('card_v5', f'https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
    ('catalog_v1', f'https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
    ('catalog_v2', f'https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
    ('web_api', f'https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={LID}'),
    ('search_wb', f'https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={LID}'),
]

for label, url in endpoints:
    t0 = time.monotonic()
    try:
        r = cffi_req.get(url, headers=H_JSON, impersonate='chrome', timeout=10)
        dt = time.monotonic() - t0
        if r.status_code == 200:
            d = r.json()
            prods = d.get('products') or d.get('data', {}).get('items', [])
            if prods:
                p = prods[0]
                price_info = p.get('price', {})
                sale = p.get('salePriceU', p.get('salePrice', '?'))
                stock = p.get('stock', p.get('sizes', []))
                stock_short = str(stock)[:80] if stock else '?'
                print(f"  {label}: 200 ({dt:.1f}s) name={str(p.get('name','?'))[:35]} price={price_info} sale={sale} stock={stock_short}")
            else:
                keys = list(d.keys())[:10]
                print(f"  {label}: 200 ({dt:.1f}s) no products, keys={keys}")
        else:
            body = r.text[:80]
            print(f"  {label}: {r.status_code} ({dt:.1f}s) body={body}")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"  {label}: ERR ({dt:.1f}s) {str(e)[:80]}")
    time.sleep(1.5)

print("\n" + "=" * 60)
print("3. HTML PAGE (og:price, embedded JSON)")
print("=" * 60)

html_url = f"https://www.wildberries.ru/catalog/{LID}/detail.aspx"
H_HTML = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Referer': 'https://www.wildberries.ru/',
}
t0 = time.monotonic()
try:
    r = cffi_req.get(html_url, headers=H_HTML, impersonate='chrome', timeout=20)
    dt = time.monotonic() - t0
    print(f"  HTML: {r.status_code} ({dt:.1f}s), {len(r.text)} bytes")
    if r.status_code == 200:
        import re
        html = r.text
        # og:price
        m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if not m:
            m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            print(f"  og:price:amount = {m.group(1)}")
        # data-price
        m = re.search(r'data-price=["\'](\d+)["\']', html, re.I)
        if m:
            print(f"  data-price = {m.group(1)}")
        # embedded price basic
        m = re.search(r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)', html)
        if m:
            print(f"  embedded price.basic = {m.group(1)}")
        # priceU
        m = re.search(r'"priceU"\s*:\s*(\d+)', html)
        if m:
            print(f"  priceU = {m.group(1)}")
        # priceD
        m = re.search(r'"priceD"\s*:\s*(\d+)', html)
        if m:
            print(f"  priceD = {m.group(1)}")
        # text price
        m = re.search(r'>\s*([\d\s\u00a0]{2,10})\s*(?:₽|руб)\s*<', html)
        if m:
            print(f"  text price = {m.group(1).strip()}")
        # availability
        if re.search(r'в наличии|есть в наличии|купить', html, re.I):
            print("  availability: IN_STOCK (text match)")
        elif re.search(r'нет в наличии|закончился|ожидание', html, re.I):
            print("  availability: OUT_OF_STOCK (text match)")
        else:
            print("  availability: UNKNOWN")
except Exception as e:
    dt = time.monotonic() - t0
    print(f"  HTML: ERR ({dt:.1f}s) {str(e)[:100]}")

print("\nDONE")
