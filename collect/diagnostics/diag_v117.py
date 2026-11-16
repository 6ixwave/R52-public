"""Диагностика v117: WB media CDN, WB price, Farpost ID→URL."""
import sys, json, re, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SEP = "=" * 60
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ru-RU,ru;q=0.9"}

nm = 199298871
imt = 158033827
vol = nm // 100000
part = nm // 1000


# ============================================================
# 1. WB MEDIA: новые CDN хосты и паттерны
# ============================================================
def test_wb_media_cdn():
    print(f"\n{SEP}\nWB MEDIA: CDN HOST DISCOVERY (nm={nm}, imt={imt})\n{SEP}")
    import requests

    # Candidate hosts
    hosts = [
        f"basket-13.wbbasket.ru",
        f"static-basket-13.wbbasket.ru",
        f"static-basket-01.wbbasket.ru",
        f"static-basket-02.wbbasket.ru",
        f"static-basket-05.wbbasket.ru",
        f"static-basket-10.wbbasket.ru",
        f"static-basket-20.wbbasket.ru",
        f"static-basket-30.wbbasket.ru",
        f"static-basket-40.wbbasket.ru",
        f"static-basket-50.wbbasket.ru",
        f"static-basket-55.wbbasket.ru",
        f"static-basket-60.wbbasket.ru",
        "wbx1.wbstatic.net",
        "wbx2.wbstatic.net",
        "wbx3.wbstatic.net",
        "static.wbstatic.net",
        "img.wbstatic.net",
    ]

    # Path patterns to try on each host
    paths = [
        f"/vol{vol}/part{part}/{nm}/images/S800/S800.jpg",
        f"/vol{vol}/part{part}/{imt}/images/S800/S800.jpg",
        f"/vol{vol}/part{part}/{nm}/images/S120/S120.jpg",
        f"/vol{vol}/part{part}/{imt}/images/S120/S120.jpg",
        f"/vol{vol}/part{part}/{nm}/images/S800/S800-2.jpg",
    ]

    found = []
    for host in hosts:
        for path in paths[:2]:  # First 2 paths per host to save time
            url = f"https://{host}{path}"
            try:
                r = requests.get(url, timeout=(4, 8), headers=H, allow_redirects=False)
                ct = r.headers.get("Content-Type", "?")
                ok = r.status_code == 200 and len(r.content) > 1000
                if ok or r.status_code not in (404,):
                    print(f"  [{r.status_code}] {len(r.content):>8}B {ct:20s} {host}{path[-30:]}")
                    if ok:
                        found.append(url)
            except Exception as e:
                if "resolve" not in str(e).lower():
                    print(f"  [ERR] {host}: {str(e)[:60]}")
            time.sleep(0.2)

    if found:
        print(f"\n  FOUND {len(found)} working URLs!")
        for u in found[:5]:
            print(f"    {u}")
    else:
        print("\n  No working image URLs found on tried hosts.")

    # Try the old basket host with different vol/part calculation
    # Maybe vol/part changed
    print(f"\n  --- Alt vol/part calculations ---")
    alt_calcs = [
        ("vol//100000, part//1000", vol, part),
        ("nm//100000, nm//1000", nm // 100000, nm // 1000),
        ("imt//100000, imt//1000", imt // 100000, imt // 1000),
    ]
    for label, v, p in alt_calcs:
        for host in ["basket-13.wbbasket.ru", "static-basket-13.wbbasket.ru"]:
            url = f"https://{host}/vol{v}/part{p}/{nm}/images/S800/S800.jpg"
            try:
                r = requests.get(url, timeout=(4, 8), headers=H)
                ok = r.status_code == 200 and len(r.content) > 1000
                if ok:
                    print(f"  OK [{label}] {host} vol={v} part={p}")
                    found.append(url)
            except:
                pass
            time.sleep(0.2)

    # Try wbstatic with different path
    print(f"\n  --- wbstatic patterns ---")
    wbstatic_paths = [
        f"https://wbx1.wbstatic.net/big/{vol}/{part}/{nm}/S800/S800.jpg",
        f"https://wbx1.wbstatic.net/big/{vol}/{part}/{imt}/S800/S800.jpg",
        f"https://wbx1.wbstatic.net/extra/{vol}/{part}/{nm}/S800/S800.jpg",
        f"https://wbx2.wbstatic.net/big/{vol}/{part}/{nm}/S800/S800.jpg",
        f"https://static.wbstatic.net/big/{vol}/{part}/{nm}/S800/S800.jpg",
        f"https://static.wbstatic.net/images/{vol}/{part}/{nm}/S800/S800.jpg",
    ]
    for url in wbstatic_paths:
        try:
            r = requests.get(url, timeout=(4, 8), headers=H)
            ok = r.status_code == 200 and len(r.content) > 1000
            if ok or r.status_code != 404:
                print(f"  [{r.status_code}] {len(r.content):>8}B {url}")
                if ok:
                    found.append(url)
        except Exception as e:
            if "resolve" not in str(e).lower():
                print(f"  [ERR] {url[-50:]}: {str(e)[:50]}")
        time.sleep(0.2)

    return found


# ============================================================
# 2. WB PRICE: wbstatic + SPA + batch
# ============================================================
def test_wb_price():
    print(f"\n{SEP}\nWB PRICE: NEW ENDPOINTS\n{SEP}")
    import requests

    headers = {
        **H,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }

    endpoints = [
        # wbstatic API
        ("wbx1_detail", f"https://wbx1.wbstatic.net/api/catalog/v1/detail?nm={nm}"),
        ("wbx2_detail", f"https://wbx2.wbstatic.net/api/catalog/v1/detail?nm={nm}"),
        ("wbx1_batch", f"https://wbx1.wbstatic.net/api/catalog/v1/batch?nm={nm}"),
        ("wbx2_batch", f"https://wbx2.wbstatic.net/api/catalog/v1/batch?nm={nm}"),
        # SPA data
        ("spa_data", f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"),
        # batch API (old)
        ("batch_v1", f"https://card.wb.ru/cards/v1/batch?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        # price-only
        ("price_api", f"https://price.wb.ru/price/{nm}"),
        ("price_api2", f"https://price.wb.ru/v1/price?nm={nm}"),
        # common
        ("common_v1", f"https://common-api.wildberries.ru/api/v1/card/{nm}"),
        ("common_v2", f"https://common-api.wildberries.ru/api/v2/card/{nm}"),
        # basket static (different from basket CDN)
        ("static_basket", f"https://static-basket-13.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json"),
    ]

    for label, url in endpoints:
        try:
            r = requests.get(url, timeout=(8, 15), headers=headers)
            body = r.text[:300] if r.status_code == 200 else r.text[:150]
            print(f"  [{label:15s}] HTTP {r.status_code} ({len(r.content):>6}B) {url.split('?')[0][-40:]}")
            if r.status_code == 200:
                print(f"    {body[:250]}")
                # Look for price
                pm = re.findall(r'"(?:price|basic|product|total|old|card|wallet)"\s*:\s*"?(\d+)"?', r.text)
                if pm:
                    print(f"    PRICES: {pm[:10]}")
        except Exception as e:
            err = str(e)[:80]
            if "resolve" not in err.lower():
                print(f"  [{label:15s}] ERR: {err}")
        time.sleep(0.8)

    # Try POST to card.wb.ru
    print(f"\n  --- POST card.wb.ru ---")
    try:
        r = requests.post(
            "https://card.wb.ru/cards/v1/detail",
            json={"nm": [nm], "appType": 1, "curr": "rub", "dest": -1257786, "spp": 30},
            headers={**headers, "Content-Type": "application/json"},
            timeout=(8, 15),
        )
        print(f"  [POST v1] HTTP {r.status_code} ({len(r.content)}B)")
        if r.status_code == 200:
            print(f"    {r.text[:300]}")
    except Exception as e:
        print(f"  [POST v1] ERR: {str(e)[:80]}")
    time.sleep(1)

    # Try with curl_cffi for the SPA page
    print(f"\n  --- curl_cffi SPA ---")
    try:
        from curl_cffi import requests as cffi_req
        r = cffi_req.get(
            f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
            impersonate="chrome",
            timeout=20,
            headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        print(f"  [SPA cffi] HTTP {r.status_code} ({len(r.content)}B)")
        if r.status_code == 200:
            # Look for __NEXT_DATA__ or similar
            nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
            if nd:
                print(f"    __NEXT_DATA__ found ({len(nd.group(1))}B)")
                try:
                    data = json.loads(nd.group(1))
                    # Search for price
                    def find_price(obj, path=""):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if "price" in k.lower() and isinstance(v, (int, float)) and v > 100:
                                    print(f"    PRICE: {path}.{k} = {v}")
                                find_price(v, f"{path}.{k}")
                        elif isinstance(obj, list):
                            for i, item in enumerate(obj[:5]):
                                find_price(item, f"{path}[{i}]")
                    find_price(data)
                except:
                    print(f"    JSON parse failed")
            else:
                # Try other patterns
                pm = re.findall(r'"price"\s*:\s*(\d+)', r.text)
                if pm:
                    print(f"    price fields: {pm[:10]}")
                # Look for image URLs
                imgs = re.findall(r'(https://[^\s"\'<>]+wbbasket\.ru[^\s"\'<>]*\.(?:jpg|png|webp))', r.text)
                print(f"    Image URLs in HTML: {len(imgs)}")
                for u in imgs[:8]:
                    print(f"      {u[:100]}")
                # Look for video
                vids = re.findall(r'(https://[^\s"\'<>]*\.(?:mp4|webm))', r.text)
                print(f"    Video URLs: {len(vids)}")
                for u in vids[:3]:
                    print(f"      {u[:100]}")
    except ImportError:
        print("  curl_cffi not available")
    except Exception as e:
        print(f"  [SPA cffi] ERR: {str(e)[:80]}")


# ============================================================
# 3. FARPOST: ID → full URL resolution
# ============================================================
def test_farpost_resolve():
    print(f"\n{SEP}\nFARPOST: ID → URL RESOLUTION\n{SEP}")
    import requests

    s = requests.Session()
    s.headers.update({
        **H,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    # Warmup
    s.get("https://www.farpost.ru/", timeout=(10, 20))
    time.sleep(1)

    test_ids = [129606872, 130693618]

    # Method 1: Try /bulletin/{id}/ (trailing slash)
    print("  --- Method 1: /bulletin/{id}/ (trailing slash) ---")
    for tid in test_ids:
        url = f"https://www.farpost.ru/bulletin/{tid}/"
        try:
            r = s.get(url, timeout=(10, 20), allow_redirects=False)
            loc = r.headers.get("Location", "")
            print(f"  {tid}: HTTP {r.status_code}, Location={loc[:80]}")
            if r.status_code == 200 and len(r.content) > 5000:
                pm = re.search(r'data-bulletin-price=["\'](\d+)', r.text)
                print(f"    Price: {pm.group(1) if pm else '?'}")
        except Exception as e:
            print(f"  {tid}: ERR {str(e)[:60]}")
        time.sleep(1)

    # Method 2: Try API endpoints
    print("\n  --- Method 2: API endpoints ---")
    api_urls = [
        f"https://www.farpost.ru/api/bulletin/{test_ids[0]}",
        f"https://www.farpost.ru/api/v1/bulletin/{test_ids[0]}",
        f"https://www.farpost.ru/bulletin/{test_ids[0]}.json",
        f"https://www.farpost.ru/bulletin/{test_ids[0]}/",
        f"https://www.farpost.ru/bulletin/{test_ids[0]}/index.html",
    ]
    for url in api_urls:
        try:
            r = s.get(url, timeout=(8, 15), allow_redirects=False)
            loc = r.headers.get("Location", "")
            ct = r.headers.get("Content-Type", "?")
            print(f"  [{r.status_code}] {ct:30s} {url[-50:]} Loc={loc[:60]}")
        except Exception as e:
            print(f"  [ERR] {url[-50:]}: {str(e)[:50]}")
        time.sleep(0.5)

    # Method 3: Search by ID
    print("\n  --- Method 3: Search ---")
    search_urls = [
        f"https://www.farpost.ru/search/?q={test_ids[0]}",
        f"https://www.farpost.ru/api/search/?q={test_ids[0]}",
    ]
    for url in search_urls:
        try:
            r = s.get(url, timeout=(10, 20))
            print(f"  [{r.status_code}] {len(r.content)}B {url[-50:]}")
            if r.status_code == 200:
                # Look for bulletin links
                links = re.findall(r'href="(/[^"]*%d[^"]*\.html)"' % test_ids[0], r.text)
                if links:
                    print(f"    Found: {links[:3]}")
                else:
                    # Broader search
                    links2 = re.findall(r'href="([^"]*bulletin[^"]*)"', r.text)
                    print(f"    Bulletin links: {links2[:5]}")
        except Exception as e:
            print(f"  [ERR] {url[-50:]}: {str(e)[:50]}")
        time.sleep(1)

    # Method 4: Try /ni/ with just ID (no city/category)
    print("\n  --- Method 4: /ni/ patterns ---")
    ni_urls = [
        f"https://www.farpost.ru/ni/bulletin/{test_ids[0]}",
        f"https://www.farpost.ru/ni/{test_ids[0]}",
        f"https://www.farpost.ru/bulletin/{test_ids[0]}/",
    ]
    for url in ni_urls:
        try:
            r = s.get(url, timeout=(8, 15), allow_redirects=False)
            loc = r.headers.get("Location", "")
            print(f"  [{r.status_code}] {len(r.content):>6}B Loc={loc[:70]} {url[-50:]}")
        except Exception as e:
            print(f"  [ERR] {url[-50:]}: {str(e)[:50]}")
        time.sleep(0.5)

    # Method 5: Check if there's a canonical URL in the working page
    print("\n  --- Method 5: Canonical/og:url from working page ---")
    working_url = "https://www.farpost.ru/ni/krasnoyarsk/zapchasti/kontraktnyj-dvigatel-1zz-fe-cena-s-navesnym-129606872.html"
    try:
        r = s.get(working_url, timeout=(10, 20))
        if r.status_code == 200:
            canon = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', r.text)
            og_url = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)', r.text)
            print(f"  canonical: {canon.group(1) if canon else '?'}")
            print(f"  og:url:    {og_url.group(1) if og_url else '?'}")
            # Look for any /bulletin/ references
            bull_refs = re.findall(r'(/bulletin/\d+[^"\']*)', r.text)
            print(f"  /bulletin/ refs: {bull_refs[:5]}")
            # Look for data-bulletin-id or similar
            bid = re.findall(r'data-bulletin-id=["\'](\d+)["\']', r.text)
            print(f"  data-bulletin-id: {bid[:5]}")
    except Exception as e:
        print(f"  ERR: {str(e)[:60]}")


# ============================================================
# 4. WB: Try getting HTML with cookies from card.json request
# ============================================================
def test_wb_html_cookies():
    print(f"\n{SEP}\nWB: HTML WITH SESSION COOKIES\n{SEP}")
    import requests

    s = requests.Session()
    s.headers.update({
        **H,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.wildberries.ru/",
    })

    # Step 1: Get main page for cookies
    r0 = s.get("https://www.wildberries.ru/", timeout=(10, 20))
    print(f"  Main page: HTTP {r0.status_code}, cookies={list(s.cookies.keys())[:10]}")
    time.sleep(2)

    # Step 2: Try catalog page
    url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    r1 = s.get(url, timeout=(15, 30))
    print(f"  Catalog: HTTP {r1.status_code} ({len(r1.content)}B)")

    if r1.status_code == 200 and len(r1.content) > 10000:
        html = r1.text
        # Images
        imgs = re.findall(r'(https://[^\s"\'<>]+wbbasket\.ru[^\s"\'<>]*\.(?:jpg|png|webp))', html)
        print(f"  Images in HTML: {len(imgs)}")
        for u in imgs[:10]:
            print(f"    {u[:100]}")
        # Videos
        vids = re.findall(r'(https://[^\s"\'<>]*\.(?:mp4|webm)[^\s"\'<>]*)', html)
        print(f"  Videos: {len(vids)}")
        for u in vids[:3]:
            print(f"    {u[:100]}")
        # Price
        prices = re.findall(r'data-price=["\'](\d+)["\']', html)
        print(f"  data-price: {prices[:5]}")
        og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html)
        print(f"  og:price: {og_price.group(1) if og_price else '?'}")
        # JSON-LD
        ld = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
        for block in ld[:3]:
            try:
                d = json.loads(block)
                if isinstance(d, dict) and d.get("@type") == "Product":
                    print(f"  JSON-LD Product: price={d.get('offers',{}).get('price','?')}")
            except:
                pass
    elif r1.status_code == 498:
        print(f"  Still 498. Body[:200]: {r1.text[:200]}")
        # Try with X-Requested-With
        r2 = s.get(url, timeout=(15, 30), headers={"X-Requested-With": "XMLHttpRequest"})
        print(f"  With XHR header: HTTP {r2.status_code} ({len(r2.content)}B)")


def main():
    test_wb_media_cdn()
    test_wb_price()
    test_farpost_resolve()
    test_wb_html_cookies()

    print(f"\n{SEP}\nALL DONE\n{SEP}")


if __name__ == "__main__":
    main()
