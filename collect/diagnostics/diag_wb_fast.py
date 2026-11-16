"""WB FAST: 5 strategies in sequence. Goal: get price."""
import json, sys, time, re

def main():
    from curl_cffi import requests as cffi_req
    print("[OK] curl_cffi")

    nm_id = 138278567
    vol, part = nm_id // 100000, nm_id // 1000
    card_path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

    print(f"\n{'='*60}\nWB FAST DIAG — nm_id={nm_id}\n{'='*60}")

    # ============================================================
    # STRATEGY 1: Cookie session (GET main → cookies → card.wb.ru)
    # ============================================================
    print("\n[1] COOKIE SESSION")
    s = cffi_req.Session(impersonate="chrome")
    try:
        r0 = s.get("https://www.wildberries.ru/", timeout=15,
                   headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        print(f"    main page: {r0.status_code} ({len(r0.content)}B)")
        cookies = s.cookies.get_dict()
        print(f"    cookies: {list(cookies.keys())[:10]}")

        # Now try card.wb.ru with session cookies
        for ver in ["v2", "v1"]:
            url = f"https://card.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
            r1 = s.get(url, timeout=12,
                       headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            print(f"    card_{ver}: {r1.status_code} ({len(r1.content)}B)")
            if r1.status_code == 200:
                d = r1.json()
                prods = d.get("products", [])
                if prods:
                    p = prods[0]
                    sz = p.get("sizes", [])
                    print(f"    *** PRICE FOUND *** name={p.get('name','?')[:40]}")
                    for s2 in sz[:3]:
                        print(f"        price={s2.get('price')} qty={s2.get('qty')}")
                    break
                else:
                    print(f"    200 but empty: {r1.text[:150]}")
            elif r1.status_code == 403:
                print(f"    still 403 (cookie didn't help)")
            time.sleep(1)

        # Try HTML with session
        r2 = s.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx", timeout=20,
                   headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        print(f"    HTML(session): {r2.status_code} ({len(r2.content)}B)")
        if r2.status_code == 200:
            html = r2.text
            for pat, lbl in [(r'"basic"\s*:\s*(\d+)', "basic"),
                             (r'"priceU"\s*:\s*(\d+)', "priceU"),
                             (r'og:price:amount[^>]*content=["\']([\d.]+)', "og"),
                             (r'data-price=["\'](\d+)["\']', "data-price")]:
                ms = re.findall(pat, html, re.I)
                if ms:
                    print(f"    [{lbl}] {ms[:3]}")
    except Exception as e:
        print(f"    ERR: {e}")

    # ============================================================
    # STRATEGY 2: Safari impersonate (different TLS fingerprint)
    # ============================================================
    print("\n[2] SAFARI IMPERSONATE")
    for ver in ["v2", "v1"]:
        url = f"https://card.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
        try:
            r = cffi_req.get(url, impersonate="safari", timeout=12,
                             headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            print(f"    card_{ver}(safari): {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                d = r.json()
                prods = d.get("products", [])
                if prods:
                    p = prods[0]
                    sz = p.get("sizes", [])
                    print(f"    *** PRICE (safari) *** {p.get('name','?')[:30]}")
                    for s2 in sz[:3]:
                        print(f"        price={s2.get('price')} qty={s2.get('qty')}")
        except Exception as e:
            print(f"    card_{ver}(safari) ERR: {str(e)[:60]}")
        time.sleep(1)

    # ============================================================
    # STRATEGY 3: search.wb.ru (different API surface)
    # ============================================================
    print("\n[3] SEARCH.WB.RU")
    search_urls = [
        f"https://search.wb.ru/suggest/v1?query=test&nm={nm_id}",
        f"https://search.wb.ru/api/search/v1?query=утюг&nm={nm_id}",
        f"https://search.wb.ru/catalog/v1?nm={nm_id}",
        f"https://search.wb.ru/cards/v1/detail?nm={nm_id}",
    ]
    for url in search_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=10,
                             headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            print(f"    {url.split('/')[2]}{url.split('/')[3][:20]}... -> {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200 and len(r.content) > 50:
                print(f"      {r.text[:200]}")
        except Exception as e:
            print(f"    {url.split('/')[2]} ERR: {str(e)[:50]}")
        time.sleep(0.5)

    # ============================================================
    # STRATEGY 4: card.wb.ru minimal (no dest, no spp, just nm)
    # ============================================================
    print("\n[4] CARD.WB.RU MINIMAL")
    minimal_urls = [
        f"https://card.wb.ru/cards/v1/detail?nm={nm_id}",
        f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
        f"https://card.wb.ru/cards/v1/detail?curr=rub&nm={nm_id}",
        f"https://card.wb.ru/cards/v1?nm={nm_id}",
    ]
    for url in minimal_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=10,
                             headers={"Accept": "application/json"})
            print(f"    {url.split('?')[1][:40]}... -> {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                print(f"      {r.text[:200]}")
        except Exception as e:
            print(f"    ERR: {str(e)[:50]}")
        time.sleep(0.5)

    # ============================================================
    # STRATEGY 5: More CDN paths on basket-10 (which WORKS)
    # ============================================================
    print("\n[5] BASKET CDN EXTRA PATHS")
    host = "basket-10.wbbasket.ru"
    extra_paths = [
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card_full.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/full.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/price_info.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/selling.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json?full=1",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json?withPrice=1",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json?price=1",
    ]
    for p in extra_paths:
        try:
            r = cffi_req.get(f"https://{host}{p}", impersonate="chrome", timeout=8,
                             headers={"Accept": "application/json"})
            tag = p.split("/")[-1]
            print(f"    {tag}: {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200 and len(r.content) > 100:
                # Check if it has price fields
                d = r.json()
                has_price = any(k.lower() in str(d).lower() for k in ("price","basic","priceu"))
                print(f"      has_price_hint={has_price}, keys={list(d.keys())[:8]}")
                if has_price:
                    print(f"      DATA: {json.dumps(d, ensure_ascii=False)[:300]}")
        except Exception as e:
            print(f"    {p.split('/')[-1]}: ERR {str(e)[:40]}")
        time.sleep(0.3)

    # ============================================================
    # BONUS: Try card.wb.ru with Accept-Encoding and specific WB headers
    # ============================================================
    print("\n[BONUS] CARD.WB.RU WB-SPECIFIC HEADERS")
    wb_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
        "Origin": "https://www.wildberries.ru",
        "X-Wb-App": "web",
        "X-Wb-Platform": "desktop",
    }
    try:
        r = cffi_req.get(
            f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
            impersonate="chrome", headers=wb_headers, timeout=12)
        print(f"    {r.status_code} ({len(r.content)}B)")
        if r.status_code == 200:
            print(f"    *** SUCCESS *** {r.text[:300]}")
    except Exception as e:
        print(f"    ERR: {str(e)[:60]}")

    print(f"\n{'='*60}\nDONE\n{'='*60}")

if __name__ == "__main__":
    main()
