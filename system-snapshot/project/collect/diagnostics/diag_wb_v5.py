"""WB v5: api.wb.ru + basket deep + 498 cookie challenge + nm-tiles."""
import json, sys, time, re

def main():
    from curl_cffi import requests as cffi_req
    print("[OK] curl_cffi")

    nm_id = 138278567
    vol, part = nm_id // 100000, nm_id // 1000
    basket_host = f"basket-{(nm_id % 100)}.wbbasket.ru"
    # Actually earlier we found basket-10 works for this ID
    basket_host = "basket-10.wbbasket.ru"

    print(f"\n{'='*60}\nWB V5 — nm_id={nm_id} basket={basket_host}\n{'='*60}")

    # ============================================================
    # A. api.wb.ru (DIFFERENT HOST from card.wb.ru)
    # ============================================================
    print("\n[A] API.WB.RU")
    api_urls = [
        f"https://api.wb.ru/cards/v2/detail?nm={nm_id}&curr=rub",
        f"https://api.wb.ru/cards/v1/detail?nm={nm_id}",
        f"https://api.wb.ru/cards/v2/detail?nm={nm_id}&curr=rub&dest=-1257786",
        f"https://api.wb.ru/cards/v1?nm={nm_id}",
        f"https://api.wb.ru/v1/cards/detail?nm={nm_id}",
        f"https://api.wb.ru/nm-tiles/v1?nm={nm_id}",
        f"https://api.wb.ru/nm-details/v1?nm={nm_id}",
    ]
    for url in api_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=10,
                             headers={"Accept": "application/json",
                                      "Referer": "https://www.wildberries.ru/"})
            tag = url.split("api.wb.ru")[1][:45]
            print(f"    {tag} -> {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200 and len(r.content) > 50:
                d = r.json() if r.content[:1] in (b'{', b'[') else r.text
                s = json.dumps(d, ensure_ascii=False) if not isinstance(d, str) else d
                has_p = any(k in s.lower() for k in ("price", "basic", "priceu", "amount"))
                print(f"      has_price={has_p} keys={list(d.keys())[:10] if isinstance(d, dict) else 'list'}")
                if has_p:
                    print(f"      *** {s[:400]}")
        except Exception as e:
            print(f"    {url.split('api.wb.ru')[1][:35]} ERR: {str(e)[:50]}")
        time.sleep(0.5)

    # ============================================================
    # B. Basket CDN — deep paths for price
    # ============================================================
    print("\n[B] BASKET CDN DEEP PATHS")
    base = f"https://{basket_host}/vol{vol}/part{part}/{nm_id}"
    deep_paths = [
        f"{base}/info/ru/card.json",          # known working
        f"{base}/info/ru/card.json",          # base
        f"{base}/price/ru/price.json",
        f"{base}/price/price.json",
        f"{base}/price.json",
        f"{base}/info/price.json",
        f"{base}/info/ru/price.json",
        f"{base}/selling/ru/selling.json",
        f"{base}/selling/selling.json",
        f"{base}/info/ru/nm.json",
        f"{base}/info/ru/product.json",
        f"{base}/info/ru/details.json",
        f"{base}/info/ru/card_full.json",
        f"{base}/card.json",
        f"{base}/info/ru/card.json?format=full",
    ]
    # Also try different basket numbers
    for bnum in [5, 10, 20, 30, 40, 50, 60, 70, 80, 90]:
        pass  # we'll test basket-10 first, then others if needed

    for p in deep_paths:
        try:
            r = cffi_req.get(p, impersonate="chrome", timeout=8,
                             headers={"Accept": "application/json"})
            tag = p.split(f"/{nm_id}")[1][:40]
            print(f"    {tag}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200 and len(r.content) > 100:
                d = r.json()
                s = json.dumps(d, ensure_ascii=False)
                has_p = any(k in s.lower() for k in ("price", "basic", "priceu", "amount", "rub"))
                if has_p and tag != "/info/ru/card.json":
                    print(f"      *** PRICE HINT *** {s[:300]}")
                elif tag == "/info/ru/card.json":
                    # Dump full card.json to see ALL fields
                    print(f"      FULL card.json keys: {list(d.keys())}")
                    for k, v in d.items():
                        if isinstance(v, (int, float, str)) and len(str(v)) < 80:
                            print(f"        {k} = {v}")
                        elif isinstance(v, list) and len(v) < 5:
                            print(f"        {k} = {json.dumps(v, ensure_ascii=False)[:100]}")
                        elif isinstance(v, dict):
                            print(f"        {k} = {{...}} ({len(v)} keys)")
        except Exception as e:
            print(f"    {p.split(f'/nm_id')[1][:30] if 'nm_id' in p else p[-30:]}: ERR {str(e)[:40]}")
        time.sleep(0.3)

    # ============================================================
    # C. 498 HTML — is it a cookie challenge?
    # ============================================================
    print("\n[C] 498 HTML CHALLENGE ANALYSIS")
    s = cffi_req.Session(impersonate="chrome")
    r = s.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
              timeout=20, headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    print(f"    first GET: {r.status_code} ({len(r.content)}B)")
    print(f"    cookies after: {s.cookies.get_dict()}")

    # Check for JS challenge markers
    html = r.text
    markers = {
        "wbaas": "wbaas" in html.lower(),
        "altcha": "altcha" in html.lower(),
        "challenge": "challenge" in html.lower(),
        "set-cookie-js": "document.cookie" in html,
        "meta-refresh": "http-equiv" in html.lower() and "refresh" in html.lower(),
        "data-req-uuid": "data-req-uuid" in html,
        "script-count": html.count("<script"),
    }
    print(f"    markers: {markers}")

    # If 498 has a meta-refresh or JS that sets cookie, try second request
    if r.status_code == 498:
        time.sleep(2)
        r2 = s.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                   timeout=20, headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9",
                                        "Referer": "https://www.wildberries.ru/"})
        print(f"    second GET: {r2.status_code} ({len(r2.content)}B)")
        if r2.status_code == 200:
            print(f"    *** COOKIE CHALLENGE PASSED ***")
            # Extract price
            for pat, lbl in [(r'"basic"\s*:\s*(\d+)', "basic"),
                             (r'"priceU"\s*:\s*(\d+)', "priceU"),
                             (r'og:price:amount[^>]*content=["\']([\d.]+)', "og"),
                             (r'"price"\s*:\s*(\d+)', "price")]:
                ms = re.findall(pat, r2.text, re.I)
                if ms:
                    print(f"    [{lbl}] {ms[:5]}")

    # ============================================================
    # D. WB static-basket for media (confirms CDN access pattern)
    # ============================================================
    print("\n[D] STATIC BASKET (media test)")
    # From card.json we might have image paths, but let's try standard pattern
    img_paths = [
        f"https://static-basket-01.wbbasket.ru/vol0/part{part}/{nm_id}/images/{nm_id}-1.jpg",
        f"https://static-basket-10.wbbasket.ru/vol0/part{part}/{nm_id}/images/{nm_id}-1.jpg",
        f"https://static-basket-02.wbbasket.ru/vol0/part{part}/{nm_id}/images/{nm_id}-1.jpg",
    ]
    for ip in img_paths:
        try:
            r = cffi_req.get(ip, impersonate="chrome", timeout=8)
            ct = r.headers.get("Content-Type", "?")
            print(f"    {ip.split('static-basket')[1][:40]}: {r.status_code} {ct} ({len(r.content)}B)")
        except Exception as e:
            print(f"    ERR: {str(e)[:50]}")
        time.sleep(0.3)

    # ============================================================
    # E. Try nm_id 1097975275 (iPhone) on basket CDN
    # ============================================================
    print("\n[E] SECOND NM_ID (1097975275)")
    nm2 = 1097975275
    vol2, part2 = nm2 // 100000, nm2 // 1000
    # Find which basket
    for bnum in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
        try:
            r = cffi_req.get(f"https://basket-{bnum}.wbbasket.ru/vol{vol2}/part{part2}/{nm2}/info/ru/card.json",
                             impersonate="chrome", timeout=6)
            if r.status_code == 200:
                print(f"    basket-{bnum}: 200 ({len(r.content)}B)")
                d = r.json()
                print(f"      name={d.get('imt_name','?')[:50]}")
                break
            elif r.status_code == 404:
                continue
        except Exception:
            continue
    else:
        print(f"    No basket found for {nm2}")

    print(f"\n{'='*60}\nDONE\n{'='*60}")

if __name__ == "__main__":
    main()
