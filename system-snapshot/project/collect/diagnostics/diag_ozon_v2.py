"""OZON v2: SSR card + data-widget + og:meta + JSON-LD. Find working endpoint."""
import json, sys, time, re

def main():
    from curl_cffi import requests as cffi_req
    print("[OK] curl_cffi")
    print(f"\n{'='*60}\nOZON V2\n{'='*60}")

    # Test product IDs (from earlier PARTIAL_SUCCESS: 1600562267)
    test_ids = [1600562267, 1234567890, 2500000001]
    # Also try to find a real one via search
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.ozon.ru/",
    }

    # ============================================================
    # A. Search for live product
    # ============================================================
    print("\n[A] SEARCH FOR LIVE PRODUCT")
    search_urls = [
        "https://www.ozon.ru/search/?text=ноутбук",
        "https://www.ozon.ru/search/?text=iphone&from_global=true",
        "https://www.ozon.ru/category/noutbuki-16531/",
    ]
    ozon_ids = []
    for url in search_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", headers=headers, timeout=15)
            print(f"    {url[:50]}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                html = r.text
                # Ozon product URLs: /product/...-{id}/
                pats = [
                    r'/product/[a-z0-9-]+-(\d+)/',
                    r'"sku"\s*:\s*(\d{6,})',
                    r'data-widget[^>]*sku=["\']?(\d+)',
                    r'"id"\s*:\s*(\d{8,})',
                ]
                for p in pats:
                    ids = re.findall(p, html)
                    if ids:
                        ozon_ids.extend(ids[:10])
                        print(f"      {p[:30]}: {ids[:5]}")
                        break
        except Exception as e:
            print(f"    ERR: {str(e)[:50]}")
        time.sleep(1)

    if not ozon_ids:
        ozon_ids = [str(t) for t in test_ids]
    print(f"    Test IDs: {ozon_ids[:5]}")

    # ============================================================
    # B. Fetch product card (multiple URL patterns)
    # ============================================================
    print("\n[B] PRODUCT CARD FETCH")
    for pid in ozon_ids[:5]:
        url_patterns = [
            f"https://www.ozon.ru/product/{pid}/",
            f"https://www.ozon.ru/product/-{pid}/",
            f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{pid}/",
        ]
        for url in url_patterns:
            try:
                r = cffi_req.get(url, impersonate="chrome", headers=headers, timeout=15)
                tag = url.split("ozon.ru")[1][:40]
                print(f"    {tag}: {r.status_code} ({len(r.content)}B)")

                if r.status_code == 200:
                    content = r.text
                    # Try JSON (composer API)
                    if url.startswith("https://www.ozon.ru/api/"):
                        try:
                            d = r.json()
                            print(f"      JSON keys: {list(d.keys())[:10]}")
                            # Look for price in state
                            state_str = json.dumps(d, ensure_ascii=False)
                            price_ms = re.findall(r'"price"\s*:\s*\{[^}]*"v"\s*:\s*(\d+)', state_str)
                            if price_ms:
                                print(f"      *** PRICES: {price_ms[:5]} ***")
                        except:
                            pass
                    else:
                        # HTML: extract data
                        jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                                           content, re.DOTALL | re.I)
                        print(f"      JSON-LD: {len(jsonld)} blocks")
                        for block in jsonld[:2]:
                            try:
                                d = json.loads(block.strip())
                                if isinstance(d, dict):
                                    print(f"      @type={d.get('@type')} name={str(d.get('name','?'))[:40]}")
                                    offers = d.get("offers", {})
                                    if isinstance(offers, dict):
                                        offers = [offers]
                                    for o in (offers if isinstance(offers, list) else []):
                                        if isinstance(o, dict):
                                            print(f"      price={o.get('price')} cur={o.get('priceCurrency')}")
                            except:
                                pass

                        # data-widget
                        widgets = re.findall(r'data-widget=["\'](\w+)["\']', content)
                        if widgets:
                            print(f"      widgets: {list(set(widgets))[:10]}")

                        # og:meta
                        for pat, lbl in [
                            (r'og:price:amount[^>]*content=["\']([\d.]+)', "og-price"),
                            (r'og:title[^>]*content=["\']([^"\']{5,60})', "og-title"),
                            (r'"price"\s*:\s*"?(\d+)"?', "json-price"),
                        ]:
                            ms = re.findall(pat, content, re.I)
                            if ms:
                                print(f"      [{lbl}] {ms[:3]}")

                        # state JSON
                        state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', content, re.DOTALL)
                        if state_m:
                            try:
                                state = json.loads(state_m.group(1))
                                print(f"      STATE keys: {list(state.keys())[:10]}")
                            except:
                                print(f"      STATE: parse fail")
                    break  # Got 200, move to next ID
                elif r.status_code == 403:
                    print(f"      403 (anti-bot)")
                    break
            except Exception as e:
                print(f"    {url[:40]} ERR: {str(e)[:40]}")
            time.sleep(1)

    # ============================================================
    # C. Composer API (JSON, often less protected)
    # ============================================================
    print("\n[C] COMPOSER API DIRECT")
    composer_urls = [
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{ozon_ids[0]}/",
        f"https://www.ozon.ru/api/composer-api.bx/page/v2?url=/product/{ozon_ids[0]}/",
        f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=/product/{ozon_ids[0]}/",
    ]
    for url in composer_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=12,
                             headers={"Accept": "application/json", **headers})
            tag = url.split("ozon.ru")[1][:45]
            print(f"    {tag}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                d = r.json()
                s = json.dumps(d, ensure_ascii=False)
                # Find prices
                prices = re.findall(r'"(?:price|v)"\s*:\s*(\d{4,})', s)
                names = re.findall(r'"(?:title|name)"\s*:\s*"([^"]{5,60})"', s)
                print(f"      prices: {prices[:5]}")
                print(f"      names: {names[:3]}")
                print(f"      top keys: {list(d.keys())[:8]}")
        except Exception as e:
            print(f"    ERR: {str(e)[:50]}")
        time.sleep(0.5)

    print(f"\n{'='*60}\nDONE\n{'='*60}")

if __name__ == "__main__":
    main()
