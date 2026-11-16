"""AVITO FAST: find live listing + full parse."""
import json, sys, os, time, re, logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from curl_cffi import requests as cffi_req

    print("=" * 60)
    print("AVITO FAST DIAG")
    print("=" * 60)

    # Step 1: Find a LIVE listing via Avito search
    print("\n[1] Finding live listing...")
    search_url = "https://www.avito.ru/search?q=ноутбук&f=0"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.avito.ru/",
    }
    live_id = None
    try:
        r = cffi_req.get(search_url, impersonate="chrome", headers=headers, timeout=20)
        print(f"    search: {r.status_code} ({len(r.content)}B)")
        if r.status_code == 200:
            # Find ad IDs in HTML
            ids = re.findall(r'avito\.ru/[a-z0-9-]+/(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'/ad/(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'"id"\s*:\s*"?(\d{8,})"?', r.text)
            print(f"    found IDs: {ids[:5]}")
            if ids:
                live_id = ids[0]
    except Exception as e:
        print(f"    search ERR: {e}")

    # Fallback: try a few known patterns
    if not live_id:
        print("    [WARN] No ID from search, trying direct...")
        # Try popular categories
        for test_url in [
            "https://www.avito.ru/moskva/noutbuki",
            "https://www.avito.ru/search?q=iphone",
        ]:
            try:
                r = cffi_req.get(test_url, impersonate="chrome", headers=headers, timeout=15)
                if r.status_code == 200:
                    ids = re.findall(r'avito\.ru/[a-z0-9-]+/(\d{8,})', r.text)
                    ids += re.findall(r'/ad/(\d{8,})', r.text)
                    if ids:
                        live_id = ids[0]
                        print(f"    found from {test_url}: {live_id}")
                        break
            except Exception:
                pass

    if not live_id:
        print("    [FAIL] No live listing found. Using 7494308705 (may 404)")
        live_id = "7494308705"

    print(f"\n[2] Testing listing: {live_id}")
    url = f"https://www.avito.ru/ad/{live_id}"

    # Step 2: Fetch the listing
    resp = cffi_req.get(url, impersonate="chrome", headers=headers, timeout=20)
    print(f"    HTTP {resp.status_code} ({len(resp.content)}B)")

    if resp.status_code == 404:
        print("    [WARN] 404 — listing might be gone")
        # Try with city prefix
        for city in ["moskva", "spb", "ekaterinburg"]:
            alt_url = f"https://www.avito.ru/{city}/ad/{live_id}"
            r2 = cffi_req.get(alt_url, impersonate="chrome", headers=headers, timeout=15)
            print(f"    {city}: {r2.status_code}")
            if r2.status_code == 200:
                resp = r2
                url = alt_url
                break

    if resp.status_code != 200:
        print(f"    [FAIL] Still {resp.status_code}, trying more IDs...")
        # If search gave multiple, try next
        try:
            r = cffi_req.get("https://www.avito.ru/search?q=телефон", impersonate="chrome",
                             headers=headers, timeout=15)
            ids = re.findall(r'avito\.ru/[a-z0-9-]+/(\d{8,})', r.text)
            for tid in ids[:5]:
                r2 = cffi_req.get(f"https://www.avito.ru/ad/{tid}", impersonate="chrome",
                                  headers=headers, timeout=15)
                print(f"    try {tid}: {r2.status_code}")
                if r2.status_code == 200:
                    resp = r2
                    live_id = tid
                    url = f"https://www.avito.ru/ad/{tid}"
                    break
        except Exception as e:
            print(f"    ERR: {e}")

    if resp.status_code != 200:
        print("    [ABORT] No live listing available")
        return

    html = resp.text
    print(f"\n[3] PARSING ({len(html)} bytes)")

    # JSON-LD
    jsonld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                               html, re.DOTALL | re.I)
    print(f"    JSON-LD blocks: {len(jsonld_blocks)}")
    product_ld = None
    for block in jsonld_blocks:
        try:
            d = json.loads(block.strip())
            if isinstance(d, dict) and d.get("@type") in ("Product", "Product", "Offer"):
                product_ld = d
            elif isinstance(d, list):
                for item in d:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        product_ld = item
            if product_ld:
                break
        except (json.JSONDecodeError, ValueError):
            pass

    if product_ld:
        print(f"    Product: {str(product_ld.get('name','?'))[:50]}")
        offers = product_ld.get("offers", {})
        if isinstance(offers, dict):
            offers = [offers]
        for o in (offers if isinstance(offers, list) else []):
            if isinstance(o, dict):
                print(f"    Offer: price={o.get('price')} cur={o.get('priceCurrency')} "
                      f"avail={o.get('availability')}")
    else:
        print("    Product JSON-LD: None")

    # State
    state = None
    for pat in [r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>',
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>',
                r'<script[^>]*id=["\']preloaded-state["\'][^>]*>(\{.*?\})</script>']:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                state = json.loads(m.group(1))
                print(f"    State: {list(state.keys())[:10]}")
                break
            except (json.JSONDecodeError, ValueError):
                continue
    if not state:
        print("    State: None")

    # Prices
    price_candidates = []
    if product_ld:
        offers = product_ld.get("offers", {})
        if isinstance(offers, dict):
            offers = [offers]
        for o in (offers if isinstance(offers, list) else []):
            if isinstance(o, dict):
                pv = o.get("price") or o.get("lowPrice")
                if pv:
                    try:
                        price_candidates.append({
                            "role": "regular",
                            "amount_minor": int(round(float(pv) * 100)),
                            "currency": o.get("priceCurrency", "RUB"),
                            "label": "JSON-LD",
                        })
                    except (ValueError, TypeError):
                        pass

    # HTML price patterns
    for pat, lbl in [
        (r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', "og:price"),
        (r'data-price=["\'](\d+)["\']', "data-price"),
        (r'class=["\'][^"\']*price[^"\']*["\'][^>]*>(\d[\d\s\u00a0]*)\s*(?:₽|руб)', "html-price"),
        (r'"price"\s*:\s*"?(\d+)"?', "json-price"),
    ]:
        ms = re.findall(pat, html, re.I)
        if ms:
            print(f"    [{lbl}] {ms[:3]}")
            try:
                val = float(ms[0].replace(" ", "").replace("\u00a0", ""))
                if val > 100:
                    price_candidates.append({"role": "regular", "amount_minor": int(val*100),
                                             "currency": "RUB", "label": lbl})
            except (ValueError, TypeError):
                pass

    print(f"\n[4] RESULTS:")
    print(f"    listing_id={live_id}")
    print(f"    url={url}")
    print(f"    price_candidates={len(price_candidates)}")
    for pc in price_candidates[:5]:
        print(f"      {pc}")
    print(f"    title={str(product_ld.get('name','?') if product_ld else 'N/A')[:50]}")

    # Save for later E2E
    out = {
        "live_id": live_id,
        "url": url,
        "http_status": resp.status_code,
        "has_jsonld": bool(product_ld),
        "has_state": bool(state),
        "price_candidates": price_candidates,
        "html_size": len(html),
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "avito_live_test.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n    Saved -> avito_live_test.json")

    print(f"\n{'='*60}\nDONE\n{'='*60}")

if __name__ == "__main__":
    main()
