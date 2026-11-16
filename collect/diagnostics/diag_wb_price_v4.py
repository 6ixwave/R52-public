"""WB Price Diagnostic v4 — browser-like headers, mobile API, common.wb.ru."""
import json, sys, time, re

def main():
    try:
        from curl_cffi import requests as cffi_req
        print("[OK] curl_cffi")
    except ImportError:
        print("[ERR] no curl_cffi"); sys.exit(1)

    print("=" * 70)
    print("WB PRICE DIAGNOSTIC v4")
    print("=" * 70)

    nm_ids = [138278567, 145567890]

    # Full browser-like headers (Chrome 131 desktop)
    BROWSER = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # XHR / API headers
    XHR = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
        "X-Requested-With": "XMLHttpRequest",
    }

    for nm_id in nm_ids:
        vol = nm_id // 100000
        part = nm_id // 1000
        print(f"\n{'='*60}\nNM_ID = {nm_id}\n{'='*60}")

        # --- A. card.wb.ru with full XHR headers ---
        print("\n--- A. card.wb.ru (XHR headers) ---")
        for ver in ["v1", "v2", "v4"]:
            for dest in ["-1257786", "0", "1"]:
                url = (f"https://card.wb.ru/cards/{ver}/detail"
                       f"?appType=1&curr=rub&dest={dest}&spp=30&nm={nm_id}")
                try:
                    r = cffi_req.get(url, impersonate="chrome", headers=XHR, timeout=10)
                    tag = f"v{ver[1:]}_d{dest}"
                    if r.status_code == 200:
                        d = r.json()
                        prods = d.get("products", [])
                        if prods:
                            p = prods[0]
                            sz = p.get("sizes", [])
                            print(f"  [{tag}] 200! name={str(p.get('name','?'))[:30]} sizes={len(sz)}")
                            if sz:
                                print(f"    price={sz[0].get('price')} qty={sz[0].get('qty')}")
                        else:
                            print(f"  [{tag}] 200 but empty: {r.text[:150]}")
                    else:
                        print(f"  [{tag}] {r.status_code}")
                except Exception as e:
                    print(f"  [v{ver[1:]}_d{dest}] ERR {str(e)[:60]}")
                time.sleep(0.5)

        # --- B. catalog.wb.ru ---
        print("\n--- B. catalog.wb.ru ---")
        for ver in ["v1", "v2"]:
            url = f"https://catalog.wb.ru/cards/{ver}/detail?curr=rub&spp=30&nm={nm_id}"
            try:
                r = cffi_req.get(url, impersonate="chrome", headers=XHR, timeout=10)
                if r.status_code == 200:
                    d = r.json()
                    prods = d.get("products", [])
                    print(f"  [cat_{ver}] 200! products={len(prods)}")
                    if prods:
                        print(f"    price={prods[0].get('sizes',[{}])[0].get('price')}")
                else:
                    print(f"  [cat_{ver}] {r.status_code}")
            except Exception as e:
                print(f"  [cat_{ver}] ERR {str(e)[:60]}")
            time.sleep(0.5)

        # --- C. web API (www.wildberries.ru/web/api/...) ---
        print("\n--- C. web API ---")
        web_apis = [
            ("web_v1_detail", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
            ("web_v2_detail", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm_id}"),
            ("web_v1_list",   f"https://www.wildberries.ru/web/api/catalog/cards/v1?nm={nm_id}"),
            ("web_common",    f"https://www.wildberries.ru/web/api/common/v1/cards?nm={nm_id}"),
        ]
        for label, url in web_apis:
            try:
                r = cffi_req.get(url, impersonate="chrome", headers=XHR, timeout=10)
                if r.status_code == 200:
                    print(f"  [{label}] 200! {r.text[:200]}")
                else:
                    print(f"  [{label}] {r.status_code} ({len(r.content)}B)")
            except Exception as e:
                print(f"  [{label}] ERR {str(e)[:60]}")
            time.sleep(0.5)

        # --- D. common.wb.ru (gave 401 before) ---
        print("\n--- D. common.wb.ru ---")
        common_apis = [
            ("common_cards", f"https://common.wb.ru/cards/v1/detail?nm={nm_id}"),
            ("common_price", f"https://common.wb.ru/price/v1?nm={nm_id}"),
            ("common_info",  f"https://common.wb.ru/info/v1?nm={nm_id}"),
        ]
        for label, url in common_apis:
            try:
                r = cffi_req.get(url, impersonate="chrome", headers=XHR, timeout=10)
                print(f"  [{label}] {r.status_code} ({len(r.content)}B)")
                if r.status_code == 200:
                    print(f"    {r.text[:200]}")
            except Exception as e:
                print(f"  [{label}] ERR {str(e)[:60]}")
            time.sleep(0.5)

        # --- E. Mobile HTML (m.wildberries.ru) ---
        print("\n--- E. Mobile HTML ---")
        MOBILE = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Sec-Ch-Ua-Mobile": "?1",
            "Sec-Ch-Ua-Platform": '"Android"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        for murl in [
            f"https://m.wildberries.ru/catalog/{nm_id}/detail.aspx",
            f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx?mobile=1",
        ]:
            try:
                r = cffi_req.get(murl, impersonate="chrome", headers=MOBILE, timeout=15)
                print(f"  {murl[:60]}... -> {r.status_code} ({len(r.content)}B)")
                if r.status_code == 200:
                    html = r.text
                    # Search for price patterns
                    pats = [
                        (r'"price"\s*:\s*\{([^}]+)\}', "price_obj"),
                        (r'"basic"\s*:\s*(\d+)', "basic"),
                        (r'"priceU"\s*:\s*(\d+)', "priceU"),
                        (r'"priceD"\s*:\s*(\d+)', "priceD"),
                        (r'og:price:amount[^>]*content=["\']([\d.]+)', "og_price"),
                        (r'data-price=["\'](\d+)["\']', "data_price"),
                        (r'"priceValue"\s*:\s*"?([\d.]+)"?', "priceValue"),
                        (r'__NEXT_DATA__\s*=\s*', "next_data"),
                        (r'window\.__STATE__\s*=\s*', "state"),
                    ]
                    for pat, lbl in pats:
                        ms = re.findall(pat, html, re.I)
                        if ms:
                            print(f"    [{lbl}] {ms[:3]}")
                    # Try to find embedded JSON with prices
                    m = re.search(r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*</script>', html, re.DOTALL)
                    if m:
                        try:
                            nd = json.loads(m.group(1))
                            # Deep search for price
                            def find_price(obj, path=""):
                                if isinstance(obj, dict):
                                    for k, v in obj.items():
                                        if k.lower() in ("price","basic","priceu","priced","pricevalue") and isinstance(v,(int,float)) and v > 0:
                                            print(f"    [NEXT_DATA] {path}.{k} = {v}")
                                        find_price(v, f"{path}.{k}")
                                elif isinstance(obj, list):
                                    for i, item in enumerate(obj[:20]):
                                        find_price(item, f"{path}[{i}]")
                            find_price(nd)
                        except json.JSONDecodeError:
                            print(f"    [NEXT_DATA] parse fail ({len(m.group(1))}ch)")
            except Exception as e:
                print(f"  ERR {str(e)[:60]}")
            time.sleep(1)

        # --- F. Desktop HTML with full browser headers ---
        print("\n--- F. Desktop HTML (full browser) ---")
        try:
            r = cffi_req.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                             impersonate="chrome", headers=BROWSER, timeout=15)
            print(f"  {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                html = r.text
                pats = [
                    (r'"price"\s*:\s*\{([^}]+)\}', "price_obj"),
                    (r'"basic"\s*:\s*(\d+)', "basic"),
                    (r'"priceU"\s*:\s*(\d+)', "priceU"),
                    (r'og:price:amount[^>]*content=["\']([\d.]+)', "og_price"),
                    (r'data-price=["\'](\d+)["\']', "data_price"),
                    (r'__NEXT_DATA__\s*=\s*', "next_data"),
                ]
                for pat, lbl in pats:
                    ms = re.findall(pat, html, re.I)
                    if ms:
                        print(f"    [{lbl}] {ms[:3]}")
            elif r.status_code == 498:
                print(f"  498 body[:300]: {r.text[:300]}")
        except Exception as e:
            print(f"  ERR {str(e)[:60]}")

        # --- G. wbx / static content CDN ---
        print("\n--- G. Static CDN paths ---")
        static_paths = [
            f"https://wbx-content-v1.wbstatic.net/vol{vol}/part{part}/{nm_id}/info/ru/card.json",
            f"https://static-basket-{vol%100:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json",
            f"https://static-basket-{vol%100:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/price.json",
        ]
        for url in static_paths:
            try:
                r = cffi_req.get(url, impersonate="chrome", headers=XHR, timeout=8)
                print(f"  {url.split('/')[2]}{url.split('/')[3][:30]}... -> {r.status_code}")
                if r.status_code == 200:
                    print(f"    {r.text[:200]}")
            except Exception as e:
                print(f"  {url.split('/')[2][:30]}... ERR {str(e)[:50]}")
            time.sleep(0.5)

    print(f"\n{'='*70}\nDONE\n{'='*70}")

if __name__ == "__main__":
    main()
