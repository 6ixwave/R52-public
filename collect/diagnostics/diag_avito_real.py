"""Avito real-collector diagnostic — step-by-step trace."""
import json, sys, os, time, re, logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag-avito")

def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from r52.collectors.avito import AvitoCollector
    from r52.collectors.base import BaseCollector

    listing_id = "7494308705"
    url = f"https://www.avito.ru/ad/{listing_id}"

    print("=" * 70)
    print(f"AVITO REAL COLLECTOR DIAG  listing={listing_id}")
    print("=" * 70)

    col = AvitoCollector(data_dir=None, use_proxies=False)
    print(f"\n[1] Collector created: {col.source}")

    # Step 2: raw HTTP
    print("\n[2] Raw HTTP fetch...")
    try:
        from curl_cffi import requests as cffi_req
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.avito.ru/",
        }
        resp = cffi_req.get(url, impersonate="chrome", headers=headers, timeout=20)
        print(f"    HTTP {resp.status_code}, {len(resp.content)} bytes")
        html = resp.text
    except Exception as e:
        print(f"    curl_cffi error: {e}")
        import requests
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }, timeout=20)
        print(f"    requests HTTP {resp.status_code}, {len(resp.content)} bytes")
        html = resp.text

    if not html or len(html) < 500:
        print("    [FAIL] Too short, aborting")
        return

    # Step 3: CAPTCHA check
    print("\n[3] CAPTCHA check...")
    is_captcha = col._is_captcha_page(html)
    print(f"    captcha={is_captcha}")
    if is_captcha:
        print("    [WARN] CAPTCHA page — collector would return None here")

    # Step 4: JSON-LD
    print("\n[4] JSON-LD extraction...")
    jsonld = col.extract_json_ld(html)
    print(f"    jsonld blocks: {len(jsonld) if jsonld else 0}")
    product_ld = col.find_product_jsonld(jsonld)
    if product_ld:
        print(f"    product_ld: name={product_ld.get('name','?')[:50]}")
        offers = product_ld.get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        if isinstance(offers, list):
            for i, o in enumerate(offers[:5]):
                if isinstance(o, dict):
                    print(f"    offer[{i}]: price={o.get('price')} "
                          f"currency={o.get('priceCurrency')} "
                          f"avail={o.get('availability')}")
    else:
        print("    product_ld: None")

    # Step 5: State
    print("\n[5] State extraction...")
    state = col._extract_state(html)
    if state:
        print(f"    state keys: {list(state.keys())[:15]}")
        for pk in ("ad","product","data","state"):
            sub = state.get(pk)
            if sub:
                print(f"    state.{pk} keys: {list(sub.keys())[:15] if isinstance(sub,dict) else type(sub)}")
    else:
        print("    state: None")

    # Step 6: Prices from state
    print("\n[6] Prices from state...")
    price_cands = []
    if state:
        col._extract_prices_from_state(state, price_cands)
    print(f"    from state: {len(price_cands)} candidates")
    for c in price_cands[:5]:
        print(f"      {c}")

    # Step 7: Prices from HTML
    print("\n[7] Prices from HTML...")
    if not price_cands:
        col._extract_prices_from_html(html, price_cands)
    print(f"    total: {len(price_cands)} candidates")
    for c in price_cands[:5]:
        print(f"      {c}")

    # Step 8: Media
    print("\n[8] Media...")
    media = col._extract_media(html, state, product_ld)
    print(f"    {len(media)} items")
    for m in media[:5]:
        print(f"      {m['type']}: {m.get('source_url','?')[:80]}")

    # Step 9: Full collect_product
    print("\n[9] Full collect_product()...")
    t0 = time.time()
    result = col.collect_product(listing_id, url, title_hint="")
    elapsed = time.time() - t0
    if result:
        obs = result.get("observation", {})
        print(f"    OK in {elapsed:.1f}s")
        print(f"    status={obs.get('status')}")
        print(f"    fields: {list(obs.get('fields',{}).keys())}")
        pc = obs.get("price_candidates", [])
        print(f"    price_candidates: {len(pc)}")
        for p in pc[:5]:
            print(f"      {p}")
        print(f"    availability={obs.get('availability')}")
        media_items = obs.get("media",{}).get("items",[])
        print(f"    media: {len(media_items)} items")
    else:
        print(f"    [FAIL] collect_product returned None in {elapsed:.1f}s")

    # Step 10: Check __PRELOADED_STATE__ raw
    print("\n[10] Raw state patterns in HTML...")
    for pat_name, pat in [
        ("__PRELOADED_STATE__", r'window\.__PRELOADED_STATE__'),
        ("__INITIAL_STATE__", r'window\.__INITIAL_STATE__'),
        ("data-state", r'data-state=\['),
        ("__NUXT__", r'window\.__NUXT__'),
        ("__APP_DATA__", r'window\.__APP_DATA__'),
    ]:
        found = re.search(pat, html)
        print(f"    {pat_name}: {'FOUND' if found else 'not found'}")

    # Step 11: Check for price in raw HTML text
    print("\n[11] Raw price patterns in HTML...")
    raw_pats = [
        (r'"price"\s*:\s*"?([\d.]+)"?', '"price"'),
        (r'"priceValue"\s*:\s*"?([\d.]+)"?', '"priceValue"'),
        (r'"amount"\s*:\s*"?([\d.]+)"?', '"amount"'),
        (r'data-price=["\']?([\d]+)', 'data-price'),
        (r'class=["\'][^"\']*price[^"\']*["\'][^>]*>([^<]{1,30})', 'class=price'),
    ]
    for pat, lbl in raw_pats:
        ms = re.findall(pat, html, re.I)
        if ms:
            print(f"    [{lbl}] {ms[:5]}")

    print(f"\n{'='*70}\nDONE\n{'='*70}")

if __name__ == "__main__":
    main()
