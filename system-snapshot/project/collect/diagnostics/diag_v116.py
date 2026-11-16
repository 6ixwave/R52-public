"""Диагностика v116: WB media URL, WB price, Farpost /bulletin/ redirect."""
import sys, json, re, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SEP = "=" * 60
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ============================================================
# 1. WB MEDIA URL PATTERNS
# ============================================================
def test_wb_media():
    print(f"\n{SEP}\nWB: MEDIA URL PATTERNS (nm_id=199298871, imt_id=158033827)\n{SEP}")
    import requests

    nm = 199298871
    imt = 158033827
    vol = nm // 100000
    part = nm // 1000
    host = "basket-13.wbbasket.ru"

    base_nm = f"https://{host}/vol{vol}/part{part}/{nm}/images"
    base_imt = f"https://{host}/vol{vol}/part{part}/{imt}/images"

    sizes = ["S800", "S120", "S500", "S700", "S1000", "S2000", "S100"]
    suffixes = ["", "-1", "-2"]

    print(f"\n  --- nm_id base: {base_nm}/ ---")
    for sz in sizes:
        for suf in suffixes:
            url = f"{base_nm}/{sz}/{sz}{suf}.jpg"
            try:
                r = requests.get(url, timeout=(5, 10), headers={"User-Agent": UA})
                ct = r.headers.get("Content-Type", "?")
                mark = "OK" if r.status_code == 200 and len(r.content) > 1000 else ""
                print(f"  [{r.status_code}] {len(r.content):>8}B {ct:20s} {sz}{suf}.jpg {mark}")
            except Exception as e:
                print(f"  [ERR] {sz}{suf}.jpg: {e}")
            time.sleep(0.3)

    print(f"\n  --- imt_id base: {base_imt}/ ---")
    for sz in ["S800", "S120"]:
        for suf in ["", "-1"]:
            url = f"{base_imt}/{sz}/{sz}{suf}.jpg"
            try:
                r = requests.get(url, timeout=(5, 10), headers={"User-Agent": UA})
                ct = r.headers.get("Content-Type", "?")
                mark = "OK" if r.status_code == 200 and len(r.content) > 1000 else ""
                print(f"  [{r.status_code}] {len(r.content):>8}B {ct:20s} {sz}{suf}.jpg {mark}")
            except Exception as e:
                print(f"  [ERR] {sz}{suf}.jpg: {e}")
            time.sleep(0.3)

    # Try colors (other nm_ids)
    print(f"\n  --- Other colors (nm_ids from card.json) ---")
    colors = [173121919, 173036660, 199298870, 308060721, 308060716, 201304460]
    for cid in colors[:3]:
        cvol = cid // 100000
        cpart = cid // 1000
        # Need to find which basket host
        # Try same host first
        for chost_n in [13, 1, 5, 10, 20, 30, 40, 50]:
            chost = f"basket-{chost_n:02d}.wbbasket.ru"
            url = f"https://{chost}/vol{cvol}/part{cpart}/{cid}/images/S800/S800.jpg"
            try:
                r = requests.get(url, timeout=(3, 8), headers={"User-Agent": UA})
                if r.status_code == 200 and len(r.content) > 1000:
                    print(f"  color {cid}: OK on {chost} ({len(r.content)}B)")
                    break
            except:
                pass
            time.sleep(0.2)
        else:
            print(f"  color {cid}: not found on tried hosts")

    # Try no-size pattern
    print(f"\n  --- No-size / direct patterns ---")
    alt_patterns = [
        f"https://{host}/vol{vol}/part{part}/{nm}/images/S800.jpg",
        f"https://{host}/vol{vol}/part{part}/{nm}/images/S800-1.jpg",
        f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800.webp",
        f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800-1.webp",
        f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800.png",
        f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800-2.jpg",
    ]
    for url in alt_patterns:
        try:
            r = requests.get(url, timeout=(5, 10), headers={"User-Agent": UA})
            ct = r.headers.get("Content-Type", "?")
            mark = "OK" if r.status_code == 200 and len(r.content) > 1000 else ""
            print(f"  [{r.status_code}] {len(r.content):>8}B {ct:20s} {url.split('/images/')[1]} {mark}")
        except Exception as e:
            print(f"  [ERR] {url.split('/images/')[1]}: {e}")
        time.sleep(0.3)


# ============================================================
# 2. WB PRICE ALTERNATIVES
# ============================================================
def test_wb_price():
    print(f"\n{SEP}\nWB: PRICE ALTERNATIVES\n{SEP}")
    import requests

    nm = 199298871
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }

    # Try basket CDN info
    vol = nm // 100000
    part = nm // 1000
    host = "basket-13.wbbasket.ru"

    endpoints = [
        ("basket_info", f"https://{host}/vol{vol}/part{part}/{nm}/info/ru/card.json"),
        ("card_v1_nopost", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("card_v2_nopost", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("web_api_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm}"),
        ("web_api_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}"),
        ("common_api", f"https://common-api.wildberries.ru/api/v1/card/{nm}"),
        ("common_api2", f"https://common-api.wildberries.ru/api/v2/card/{nm}"),
    ]

    for label, url in endpoints:
        try:
            r = requests.get(url, timeout=(8, 15), headers=headers)
            body_preview = r.text[:200] if r.status_code == 200 else r.text[:100]
            print(f"  [{label}] HTTP {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                print(f"    {body_preview}")
                # Look for price fields
                if "price" in r.text.lower():
                    # Find price-like patterns
                    pm = re.findall(r'"(?:price|basic|product|card|wallet|old)"\s*:\s*"?(\d+)"?', r.text)
                    if pm:
                        print(f"    Price fields found: {pm[:10]}")
        except Exception as e:
            print(f"  [{label}] ERROR: {e}")
        time.sleep(1)

    # Try with curl_cffi if available
    print(f"\n  --- curl_cffi (impersonate=chrome) ---")
    try:
        from curl_cffi import requests as cffi_req
        for label, url in endpoints[:4]:
            try:
                r = cffi_req.get(url, impersonate="chrome", timeout=12, headers=headers)
                print(f"  [{label}] cffi HTTP {r.status_code} ({len(r.content)}B)")
                if r.status_code == 200:
                    print(f"    {r.text[:200]}")
            except Exception as e:
                print(f"  [{label}] cffi ERROR: {e}")
            time.sleep(1)
    except ImportError:
        print("  curl_cffi not available")

    # Try mobile API
    print(f"\n  --- Mobile API ---")
    mobile_endpoints = [
        ("m_api", f"https://m.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}"),
        ("m_api2", f"https://m.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm}"),
    ]
    for label, url in mobile_endpoints:
        try:
            r = requests.get(url, timeout=(8, 15), headers={**headers, "Referer": "https://m.wildberries.ru/"})
            print(f"  [{label}] HTTP {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                print(f"    {r.text[:200]}")
        except Exception as e:
            print(f"  [{label}] ERROR: {e}")
        time.sleep(1)


# ============================================================
# 3. FARPOST /bulletin/ REDIRECT TRACE
# ============================================================
def test_farpost_bulletin():
    print(f"\n{SEP}\nFARPOST: /bulletin/ REDIRECT TRACE\n{SEP}")
    import requests

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    # Warmup
    r0 = s.get("https://www.farpost.ru/", timeout=(10, 20), allow_redirects=False)
    print(f"  Warmup: HTTP {r0.status_code}, cookies={list(s.cookies.keys())}")
    time.sleep(1)

    # /bulletin/ with allow_redirects=False
    r1 = s.get("https://www.farpost.ru/bulletin/129606872", timeout=(10, 20), allow_redirects=False)
    loc = r1.headers.get("Location", "?")
    print(f"  /bulletin/ (no-redirect): HTTP {r1.status_code}, Location={loc}")

    if r1.status_code in (301, 302, 303, 307, 308):
        # Follow manually
        if loc.startswith("/"):
            loc = "https://www.farpost.ru" + loc
        r2 = s.get(loc, timeout=(10, 20), allow_redirects=False)
        loc2 = r2.headers.get("Location", "")
        print(f"  Follow 1: HTTP {r2.status_code}, len={len(r2.content)}, Location2={loc2}")

        if r2.status_code in (301, 302, 303, 307, 308) and loc2:
            if loc2.startswith("/"):
                loc2 = "https://www.farpost.ru" + loc2
            r3 = s.get(loc2, timeout=(10, 20), allow_redirects=False)
            print(f"  Follow 2: HTTP {r3.status_code}, len={len(r3.content)}")

            if r3.status_code == 200:
                pm = re.search(r'data-bulletin-price=["\'](\d+)', r3.text)
                print(f"  Price in final: {pm.group(1) if pm else 'NOT FOUND'}")
                print(f"  Title: {re.search(r'<h1[^>]*>(.*?)</h1>', r3.text, re.DOTALL).group(1)[:80] if re.search(r'<h1[^>]*>(.*?)</h1>', r3.text, re.DOTALL) else '?'}")
        elif r2.status_code == 200:
            pm = re.search(r'data-bulletin-price=["\'](\d+)', r2.text)
            print(f"  Price in follow1: {pm.group(1) if pm else 'NOT FOUND'}")
    elif r1.status_code == 200:
        print(f"  /bulletin/ direct 200! len={len(r1.content)}")
        pm = re.search(r'data-bulletin-price=["\'](\d+)', r1.text)
        print(f"  Price: {pm.group(1) if pm else 'NOT FOUND'}")
    else:
        print(f"  /bulletin/ unexpected: HTTP {r1.status_code}")
        print(f"  Body[:200]: {r1.text[:200]}")

    # Now with allow_redirects=True
    s2 = requests.Session()
    s2.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    s2.get("https://www.farpost.ru/", timeout=(10, 20))
    time.sleep(1)
    r4 = s2.get("https://www.farpost.ru/bulletin/129606872", timeout=(10, 30), allow_redirects=True)
    print(f"\n  allow_redirects=True: HTTP {r4.status_code}, len={len(r4.content)}, final_url={r4.url}")
    pm = re.search(r'data-bulletin-price=["\'](\d+)', r4.text)
    print(f"  Price: {pm.group(1) if pm else 'NOT FOUND'}")
    # Check if it's the same page
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', r4.text, re.DOTALL)
    if h1:
        print(f"  H1: {h1.group(1)[:80]}")

    # Test with different ID (130693618 from e2e)
    print(f"\n  --- ID 130693618 ---")
    s3 = requests.Session()
    s3.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    s3.get("https://www.farpost.ru/", timeout=(10, 20))
    time.sleep(1)
    r5 = s3.get("https://www.farpost.ru/bulletin/130693618", timeout=(10, 30), allow_redirects=True)
    print(f"  HTTP {r5.status_code}, len={len(r5.content)}, final_url={r5.url}")
    pm = re.search(r'data-bulletin-price=["\'](\d+)', r5.text)
    print(f"  Price: {pm.group(1) if pm else 'NOT FOUND'}")
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', r5.text, re.DOTALL)
    if h1:
        print(f"  H1: {h1.group(1)[:80]}")
    # Check for "not found" or "deleted"
    if "не найдено" in r5.text.lower() or "удалено" in r5.text.lower() or "архив" in r5.text.lower():
        print("  WARNING: page says not found / deleted / archive")


# ============================================================
# 4. WB: Check if card.json has hidden price data
# ============================================================
def test_wb_card_price():
    print(f"\n{SEP}\nWB: CARD.JSON HIDDEN PRICE FIELDS\n{SEP}")
    dump = Path(__file__).parent / "wb_card_json_dump.json"
    if not dump.exists():
        print("  Dump not found")
        return
    data = json.loads(dump.read_text(encoding="utf-8"))

    # Search for any price-like keys recursively
    def find_prices(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if "price" in k.lower() or "cost" in k.lower() or "amount" in k.lower():
                    print(f"  {path}.{k} = {json.dumps(v, ensure_ascii=False)[:100]}")
                find_prices(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                find_prices(item, f"{path}[{i}]")

    print("  All price/cost/amount keys:")
    find_prices(data)

    # Check 'data' field more carefully
    d = data.get("data", {})
    print(f"\n  data field: {json.dumps(d, ensure_ascii=False)}")

    # Check if there's a 'selling' with more info
    selling = data.get("selling", {})
    print(f"  selling: {json.dumps(selling, ensure_ascii=False)}")

    # Look for any numeric fields that could be price
    print("\n  Numeric fields > 100 (potential prices):")
    def find_numbers(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (int, float)) and v > 100 and not isinstance(v, bool):
                    print(f"  {path}.{k} = {v}")
                find_numbers(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                find_numbers(item, f"{path}[{i}]")
    find_numbers(data)


def main():
    test_wb_media()
    test_wb_price()
    test_farpost_bulletin()
    test_wb_card_price()

    print(f"\n{SEP}\nALL DONE\n{SEP}")


if __name__ == "__main__":
    main()
