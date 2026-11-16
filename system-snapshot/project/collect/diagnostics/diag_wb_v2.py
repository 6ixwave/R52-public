"""WB v2: curl_cffi Chrome impersonation + batch API + catalog search."""
import json, sys, time, re, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

WB_IDS = ["199298871", "314337853"]
results = []

# --- curl_cffi ---
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
    print("curl_cffi available")
except ImportError as e:
    HAS_CFFI = False
    print(f"curl_cffi NOT available: {e}")
    HAS_CFFI = False

def probe_cffi(label, url, impersonate="chrome", expect_json=False, params=None, extra_headers=None):
    """GET через curl_cffi с имитацией браузера."""
    print(f"\n{'='*60}")
    print(f"[{label}] {url}")
    if params:
        print(f"  params: {params}")
    t0 = time.monotonic()
    try:
        headers = {}
        if extra_headers:
            headers.update(extra_headers)
        r = cffi_requests.get(url, headers=headers, impersonate=impersonate, timeout=15, params=params)
        elapsed = time.monotonic() - t0
        ct = r.headers.get("Content-Type", "?")
        print(f"  Status: {r.status_code}, {len(r.content)} bytes, {elapsed:.1f}s, CT={ct}")

        if r.status_code == 200:
            if expect_json:
                try:
                    data = r.json()
                    keys = list(data.keys())[:15] if isinstance(data, dict) else "list"
                    print(f"  JSON keys: {keys}")
                    return data
                except Exception as e:
                    print(f"  JSON parse fail: {e}")
            text = r.text
            # JSON-LD
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"  JSON-LD: {len(ld)} blocks")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"    [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                        elif isinstance(obj, list):
                            for j, item in enumerate(obj[:3]):
                                print(f"    [{i}.{j}] @type={item.get('@type','?')} name={str(item.get('name',''))[:60]}")
                    except:
                        print(f"    [{i}] parse fail")
            # OG meta
            og = re.findall(r'<meta[^>]*property=["\']og:(?:title|price:amount|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"  OG: {og[:5]}")
            # window.__STATE__ / __NEXT_DATA__
            for var in ("__STATE__", "__NEXT_DATA__", "__APP_DATA__", "window.data"):
                m = re.search(rf'{var}\s*=\s*(\{{.*?\}})\s*[;)]', text, re.DOTALL)
                if m:
                    print(f"  {var}: len={len(m.group(1))}")
            return text
        else:
            body = r.text[:400]
            print(f"  Body: {body[:300]}")
            return None
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  ERROR ({elapsed:.1f}s): {type(e).__name__}: {e}")
        traceback.print_exc()
        return None

if HAS_CFFI:
    # --- 1. card.wb.ru v2 batch ---
    batch_nm = ",".join(WB_IDS)
    url = f"https://card.wb.ru/cards/v2/detail"
    data = probe_cffi("WB card v2 batch", url, impersonate="chrome", expect_json=True, params={"nm": batch_nm})
    if data:
        prods = data.get("data", {}).get("products", [])
        print(f"  Products in batch: {len(prods)}")
        for p in prods:
            nm = p.get("nm", "?")
            name = p.get("name", "?")[:60]
            brand = p.get("brand", "?")
            sizes = p.get("sizes", [])
            price_info = ""
            if sizes:
                s = sizes[0]
                pr = s.get("price", {})
                basic = pr.get("basic", {})
                discount = pr.get("discount", {})
                price_info = f"basic={basic.get('v')} disc={discount.get('v')} r={basic.get('r',0)}"
            imgs = p.get("im", [])
            print(f"  nm={nm} name={name} brand={brand} price=[{price_info}] imgs={len(imgs)}")
            results.append({"id": nm, "method": "card_v2_batch", "status": "OK", "name": name, "brand": brand,
                            "price": price_info, "imgs": len(imgs)})
    else:
        results.append({"id": batch_nm, "method": "card_v2_batch", "status": "FAIL"})

    # --- 2. card.wb.ru v2 single ---
    for wid in WB_IDS:
        url = f"https://card.wb.ru/cards/v2/detail"
        data = probe_cffi(f"WB card v2 single {wid}", url, impersonate="chrome", expect_json=True, params={"nm": wid})
        if data:
            prods = data.get("data", {}).get("products", [])
            if prods:
                p = prods[0]
                results.append({"id": wid, "method": "card_v2_single", "status": "OK",
                                "name": p.get("name","")[:60]})
            else:
                results.append({"id": wid, "method": "card_v2_single", "status": "NO_PRODUCTS"})
        else:
            results.append({"id": wid, "method": "card_v2_single", "status": "FAIL"})

    # --- 3. catalog.wb.ru search ---
    url = "https://catalog.wb.ru/v1/search"
    data = probe_cffi("WB catalog search", url, impersonate="chrome", expect_json=True,
                      params={"query": "lynxauto датчик", "resultset": "popular", "sort": "popular",
                              "spp": 30, "nm": WB_IDS[0]})
    if data:
        prods = data.get("data", {}).get("products", [])
        print(f"  Search results: {len(prods)}")
        for p in prods[:5]:
            print(f"    nm={p.get('nm')} name={p.get('name','')[:50]} brand={p.get('brand','?')}")
        results.append({"id": "search", "method": "catalog_v1", "status": "OK", "count": len(prods)})
    else:
        results.append({"id": "search", "method": "catalog_v1", "status": "FAIL"})

    # --- 4. HTML detail via curl_cffi ---
    for wid in WB_IDS:
        url = f"https://www.wildberries.ru/catalog/{wid}/detail.aspx"
        text = probe_cffi(f"WB HTML {wid}", url, impersonate="chrome")
        if text:
            results.append({"id": wid, "method": "html_cffi", "status": "OK", "len": len(text)})
        else:
            results.append({"id": wid, "method": "html_cffi", "status": "FAIL"})

    # --- 5. basket.wb.ru (mobile API) ---
    for wid in WB_IDS:
        url = f"https://basket.wb.ru/cards/v2/detail"
        data = probe_cffi(f"WB basket {wid}", url, impersonate="chrome", expect_json=True, params={"nm": wid})
        if data:
            prods = data.get("data", {}).get("products", [])
            if prods:
                results.append({"id": wid, "method": "basket_v2", "status": "OK",
                                "name": prods[0].get("name","")[:60]})
            else:
                results.append({"id": wid, "method": "basket_v2", "status": "NO_PRODUCTS"})
        else:
            results.append({"id": wid, "method": "basket_v2", "status": "FAIL"})

    # --- 6. static-basket (images) ---
    for wid in WB_IDS:
        url = f"https://static-basket-01.wbbox.ru/image/{wid}"
        r = probe_cffi(f"WB static-basket {wid}", url, impersonate="chrome")
        if r is not None:
            results.append({"id": wid, "method": "static_image", "status": "OK"})
        else:
            results.append({"id": wid, "method": "static_image", "status": "FAIL"})

# --- Summary ---
print(f"\n{'='*60}")
print("WB v2 PROBE SUMMARY:")
for r in results:
    print(f"  {r}")

out = Path(__file__).parent / "diag_wb_v2_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
