"""DIAG v4: полная структура WB + multi-URL для OZON/AVITO/FARPOST."""
import json, time, sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
    print("[diag] Using curl_cffi")
except ImportError:
    import requests
    USE_CFFI = False
    print("[diag] Using requests")

def get(url, **kw):
    if USE_CFFI:
        kw.setdefault("impersonate", "chrome")
        kw.setdefault("timeout", 15)
        return cffi_req.get(url, **kw)
    kw.setdefault("timeout", 15)
    kw.setdefault("headers", {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"})
    return requests.get(url, **kw)

t0 = time.monotonic()
print("DIAG v4 START")
WORK = ROOT / "e2e-final-work"
WORK.mkdir(exist_ok=True)

# ============ WB: полная структура card.json ============
print("\n" + "=" * 60)
print("[WB] nm_id=1097975275 — полная структура")
nm_id = 1097975275
vol = nm_id // 1000
part = (nm_id // 100) % 10000

wb_done = False
for host in (42, 1, 2, 3, 5):
    for suffix in ("cards.json", "card.json"):
        url = f"https://basket-{host}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/{suffix}"
        try:
            r = get(url)
            if r.status_code != 200:
                print(f"  basket-{host}/{suffix}: {r.status_code}")
                continue
            data = r.json()
            print(f"\n  basket-{host}/{suffix}: 200 len={len(r.text)}")
            print(f"  top keys ({len(data.keys())}): {list(data.keys())}")

            selling = data.get("selling", {})
            print(f"  selling keys: {list(selling.keys())}")
            print(f"  selling: {json.dumps(selling, ensure_ascii=False)[:400]}")

            sizes = data.get("sizes", [])
            print(f"  sizes count: {len(sizes)}")
            for i, s in enumerate(sizes[:5]):
                if not isinstance(s, dict):
                    continue
                sp = s.get("price", {})
                print(f"  size[{i}] keys={list(s.keys())[:10]}")
                print(f"  size[{i}].price: {json.dumps(sp, ensure_ascii=False)[:300]}")

            # Рекурсивный поиск цен
            def find_prices(obj, path=""):
                found = []
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in ("price", "priceU", "priceD", "oldPrice", "basic", "product", "card", "wallet", "old", "crossed", "bankCard", "priceU", "priceD") and isinstance(v, (int, float)) and v > 100:
                            found.append(f"{path}.{k}={v}")
                        elif isinstance(v, (dict, list)):
                            found.extend(find_prices(v, f"{path}.{k}"))
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:10]):
                        found.extend(find_prices(v, f"{path}[{i}]"))
                return found

            prices = find_prices(data)
            print(f"\n  ALL price-like fields ({len(prices)}):")
            for p in prices[:20]:
                print(f"    {p}")

            # Сохраняем полный JSON
            out = WORK / "wb_card_full.json"
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            print(f"  saved: {out.name} ({out.stat().st_size} bytes)")

            # Web API для цен
            api_url = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
            try:
                ra = get(api_url)
                print(f"\n  card.wb.ru/cards/v2/detail: {ra.status_code} len={len(ra.text)}")
                if ra.status_code == 200:
                    ad = ra.json()
                    products = ad.get("data", {}).get("products", [])
                    if products:
                        p0 = products[0]
                        print(f"  product keys: {list(p0.keys())[:20]}")
                        print(f"  price: {p0.get('price')}")
                        sz = p0.get("sizes", [])
                        if sz:
                            print(f"  sizes[0].price: {json.dumps(sz[0].get('price', {}), ensure_ascii=False)[:300]}")
                    else:
                        print(f"  no products in data: {json.dumps(ad, ensure_ascii=False)[:300]}")
                else:
                    print(f"  body: {ra.text[:200]}")
            except Exception as e:
                print(f"  card.wb.ru API error: {e}")

            # Ещё один API: catalog.wb.ru
            api2 = f"https://catalog.wb.ru/cards/v2/detail?nm={nm_id}"
            try:
                r2 = get(api2)
                print(f"\n  catalog.wb.ru/cards/v2/detail: {r2.status_code} len={len(r2.text)}")
                if r2.status_code == 200:
                    a2 = r2.json()
                    prods = a2.get("data", {}).get("products", [])
                    if prods:
                        p0 = prods[0]
                        print(f"  price: {p0.get('price')}")
                        sz = p0.get("sizes", [])
                        if sz:
                            print(f"  sizes[0].price: {json.dumps(sz[0].get('price', {}), ensure_ascii=False)[:300]}")
            except Exception as e:
                print(f"  catalog.wb.ru error: {e}")

            wb_done = True
            break
        except Exception as e:
            print(f"  basket-{host}/{suffix}: ERR {e}")
    if wb_done:
        break

# ============ OZON: несколько URL ============
print("\n" + "=" * 60)
print("[OZON] Multi-URL test")

ozon_tests = [
    ("https://www.ozon.ru/product/562267/", "ozon_562267"),
    ("https://www.ozon.ru/product/1705353494/", "ozon_1705353494"),
    ("https://m.ozon.ru/product/562267/", "ozon_m_562267"),
    ("https://www.ozon.ru/category/telefony-smartfony/", "ozon_cat"),
]
for url, tag in ozon_tests:
    try:
        r = get(url)
        ct = r.headers.get("content-type", "")
        text = r.text
        print(f"\n  [{tag}] {url}")
        print(f"  status={r.status_code} ct={ct} len={len(text)}")
        markers = {
            "json_ld": 'application/ld+json' in text,
            "og": 'og:title' in text or 'og:image' in text,
            "widget": 'data-widget' in text,
            "next_data": '__NEXT_DATA__' in text,
            "h1": '<h1' in text.lower(),
            "price_rub": bool(re.search(r'[\d\s]{3,}\s*[₽руб]', text)),
            "captcha": 'captcha' in text.lower()[:5000] or 'antibot' in text.lower()[:5000],
            "404": 'Такой страницы' in text or 'не существует' in text.lower()[:3000],
        }
        print(f"  markers: {markers}")
        if markers["json_ld"]:
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', text, re.DOTALL):
                try:
                    jld = json.loads(m.group(1).strip())
                    print(f"  JSON-LD: {json.dumps(jld, ensure_ascii=False)[:400]}")
                except Exception:
                    print(f"  JSON-LD parse err: {m.group(1)[:100]}")
        if markers["og"]:
            for m in re.finditer(r'property=["\']og:(\w+)["\'][^>]*content=["\']([^"\']*)["\']', text):
                print(f"  og:{m.group(1)} = {m.group(2)[:120]}")
        tm = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
        if tm:
            print(f"  <title>: {tm.group(1).strip()[:200]}")
        hm = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.DOTALL)
        if hm:
            print(f"  <h1>: {re.sub(r'<[^>]+>', '', hm.group(1)).strip()[:200]}")
        # Ищем product links для категории
        if 'cat' in tag:
            links = re.findall(r'/product/(\d+)', text)
            print(f"  product links: {len(links)}, first 5: {links[:5]}")
        out = WORK / f"{tag}.html"
        out.write_text(text[:80000], encoding="utf-8")
        print(f"  saved: {tag}.html ({out.stat().st_size}b)")
    except Exception as e:
        print(f"  [{tag}] ERROR: {e}")

# ============ AVITO: несколько URL ============
print("\n" + "=" * 60)
print("[AVITO] Multi-URL test")

avito_tests = [
    ("https://www.avito.ru/ad/7494308705", "avito_7494308705"),
    ("https://m.avito.ru/ad/7494308705", "avito_m_7494308705"),
    ("https://www.avito.ru/vladivostok/telefony", "avito_cat_telefony"),
    ("https://www.avito.ru/vladivostok/kompyuternaya-tehnika", "avito_cat_komp"),
]
for url, tag in avito_tests:
    try:
        r = get(url)
        ct = r.headers.get("content-type", "")
        text = r.text
        print(f"\n  [{tag}] {url}")
        print(f"  status={r.status_code} ct={ct} len={len(text)}")
        markers = {
            "json_ld": 'application/ld+json' in text,
            "preloaded": '__PRELOADED_STATE__' in text,
            "data_state": 'data-state' in text,
            "og": 'og:title' in text,
            "h1": '<h1' in text.lower(),
            "404": '404' in text[:2000] or 'не найдена' in text[:3000].lower(),
            "price": bool(re.search(r'[\d\s]{3,}\s*[₽руб]', text)),
        }
        print(f"  markers: {markers}")
        if markers["json_ld"]:
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', text, re.DOTALL):
                try:
                    jld = json.loads(m.group(1).strip())
                    print(f"  JSON-LD: {json.dumps(jld, ensure_ascii=False)[:400]}")
                except Exception:
                    pass
        if markers["preloaded"]:
            pm = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;\s*(?:</script>|$)', text, re.DOTALL)
            if pm:
                print(f"  PRELOADED_STATE len={len(pm.group(1))}")
                try:
                    ps = json.loads(pm.group(1))
                    print(f"  PS top keys: {list(ps.keys())[:10]}")
                except Exception:
                    pass
        tm = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
        if tm:
            print(f"  <title>: {tm.group(1).strip()[:200]}")
        if 'cat' in tag:
            links = re.findall(r'/(\d{7,})["\?]', text)
            print(f"  ad IDs: {len(links)}, first 5: {links[:5]}")
        out = WORK / f"{tag}.html"
        out.write_text(text[:80000], encoding="utf-8")
        print(f"  saved: {tag}.html ({out.stat().st_size}b)")
    except Exception as e:
        print(f"  [{tag}] ERROR: {e}")

# ============ FARPOST: несколько URL ============
print("\n" + "=" * 60)
print("[FARPOST] Multi-URL test")

farpost_tests = [
    ("https://www.farpost.ru/vladivostok/1154321987.html", "farpost_1154321987"),
    ("https://www.farpost.ru/vladivostok/telefony/", "farpost_cat_telefony"),
    ("https://www.farpost.ru/vladivostok/kompyuternaya-tehnika/", "farpost_cat_komp"),
]
for url, tag in farpost_tests:
    try:
        r = get(url)
        ct = r.headers.get("content-type", "")
        if 'windows-1251' in ct.lower():
            text = r.content.decode('windows-1251', errors='replace')
        else:
            text = r.text
        print(f"\n  [{tag}] {url}")
        print(f"  status={r.status_code} ct={ct} len={len(text)}")
        markers = {
            "json_ld": 'application/ld+json' in text,
            "og": 'og:title' in text or 'og:image' in text,
            "price": 'bulletin-price' in text or 'itemprop="price"' in text,
            "lightbox": 'lightbox' in text,
            "h1": '<h1' in text.lower(),
            "404": 'не найдена' in text[:3000].lower(),
        }
        print(f"  markers: {markers}")
        if markers["og"]:
            for m in re.finditer(r'property=["\']og:(\w+)["\'][^>]*content=["\']([^"\']*)["\']', text):
                print(f"  og:{m.group(1)} = {m.group(2)[:120]}")
        if markers["json_ld"]:
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', text, re.DOTALL):
                try:
                    jld = json.loads(m.group(1).strip())
                    print(f"  JSON-LD: {json.dumps(jld, ensure_ascii=False)[:400]}")
                except Exception:
                    pass
        tm = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
        if tm:
            print(f"  <title>: {tm.group(1).strip()[:200]}")
        if 'cat' in tag:
            links = re.findall(r'href="(/vladivostok/\d+\.html)"', text)
            print(f"  listing links: {len(links)}, first 5: {links[:5]}")
        out = WORK / f"{tag}.html"
        out.write_text(text[:80000], encoding="utf-8")
        print(f"  saved: {tag}.html ({out.stat().st_size}b)")
    except Exception as e:
        print(f"  [{tag}] ERROR: {e}")

print(f"\nDIAG v4 DONE {time.monotonic()-t0:.1f}s")
