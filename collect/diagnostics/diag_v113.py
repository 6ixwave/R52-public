"""Диагностика v113: Farpost redirect + WB price endpoints + WB HTML prices."""
import sys, json, re, time, logging
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SEP = "=" * 60

# ============================================================
# 1. FARPOST: проверить redirect /bulletin/{id}
# ============================================================
def test_farpost_redirect():
    print(f"\n{SEP}\nFARPOST REDIRECT TEST\n{SEP}")
    import requests
    
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    
    # Warmup
    print("1. Warmup: GET https://www.farpost.ru/")
    try:
        r = s.get("https://www.farpost.ru/", timeout=(10, 30))
        print(f"   Status: {r.status_code}, Bytes: {len(r.content)}")
        print(f"   Cookies: {list(s.cookies.keys())}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return
    
    time.sleep(2)
    
    # Redirect URL
    url = "https://www.farpost.ru/bulletin/130693618"
    print(f"\n2. GET {url} (follow redirects)")
    try:
        r = s.get(url, timeout=(10, 30), allow_redirects=True)
        print(f"   Final URL: {r.url}")
        print(f"   Status: {r.status_code}, Bytes: {len(r.content)}")
        print(f"   History: {[h.status_code for h in r.history]}")
        if r.status_code == 200 and len(r.content) > 500:
            # Check for JSON-LD
            ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r.text, re.DOTALL)
            print(f"   JSON-LD blocks: {len(ld_blocks)}")
            for i, block in enumerate(ld_blocks[:3]):
                try:
                    data = json.loads(block.strip())
                    t = data.get("@type", "?") if isinstance(data, dict) else "list"
                    name = data.get("name", "") if isinstance(data, dict) else ""
                    print(f"   LD[{i}]: type={t}, name={str(name)[:60]}")
                except:
                    print(f"   LD[{i}]: parse error, len={len(block)}")
            # Price
            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r.text)
            print(f"   data-bulletin-price: {m.group(1) if m else 'NOT FOUND'}")
            # Title
            m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', r.text, re.DOTALL)
            if m:
                print(f"   H1 title: {re.sub(r'<[^>]+>','',m.group(1)).strip()[:80]}")
        elif r.status_code == 200:
            print(f"   SHORT response ({len(r.content)} bytes): {r.text[:200]}")
        else:
            print(f"   Body[:300]: {r.text[:300]}")
    except Exception as e:
        print(f"   ERROR: {e}")
    
    # No-redirect to see the 301/302
    print(f"\n3. GET {url} (NO follow redirects)")
    try:
        r = s.get(url, timeout=(10, 30), allow_redirects=False)
        print(f"   Status: {r.status_code}")
        loc = r.headers.get("Location", "none")
        print(f"   Location: {loc}")
    except Exception as e:
        print(f"   ERROR: {e}")
    
    time.sleep(2)
    
    # Try canonical path if we got one from Location
    if loc and loc.startswith("/"):
        canonical = f"https://www.farpost.ru{loc}"
        print(f"\n4. GET canonical: {canonical}")
        try:
            r = s.get(canonical, timeout=(10, 30))
            print(f"   Status: {r.status_code}, Bytes: {len(r.content)}")
            if r.status_code == 200:
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r.text)
                print(f"   Price: {m.group(1) if m else 'NOT FOUND'}")
        except Exception as e:
            print(f"   ERROR: {e}")


# ============================================================
# 2. WB: Price endpoints individually
# ============================================================
def test_wb_prices():
    print(f"\n{SEP}\nWB PRICE ENDPOINTS\n{SEP}")
    import requests
    
    nm_id = 199298871
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    })
    
    endpoints = [
        ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v5", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
    ]
    
    for label, url in endpoints:
        try:
            r = s.get(url, timeout=(8, 15))
            snippet = r.text[:150] if r.status_code != 200 else ""
            print(f"  {label:10s}: HTTP {r.status_code} ({len(r.content)}B) {snippet[:80]}")
            if r.status_code == 200:
                data = r.json()
                products = data.get("products") or data.get("data", {}).get("items", [])
                if products:
                    p = products[0]
                    sizes = p.get("sizes", [])
                    if sizes:
                        pr = sizes[0].get("price", {})
                        print(f"           name={p.get('name','?')[:40]}, prices={json.dumps(pr, ensure_ascii=False)[:100]}")
        except Exception as e:
            print(f"  {label:10s}: ERROR {e}")
        time.sleep(3)
    
    # Try curl_cffi if available
    print("\n  --- curl_cffi (impersonate=chrome) ---")
    try:
        from curl_cffi import requests as cffi_req
        for label, url in endpoints[:3]:
            r = cffi_req.get(url, impersonate="chrome", timeout=12,
                            headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            print(f"  {label:10s}: HTTP {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                data = r.json()
                products = data.get("products", [])
                if products:
                    pr = products[0].get("sizes", [{}])[0].get("price", {})
                    print(f"           prices={json.dumps(pr, ensure_ascii=False)[:100]}")
            time.sleep(3)
    except ImportError:
        print("  curl_cffi not available")
    except Exception as e:
        print(f"  cffi error: {e}")


# ============================================================
# 3. WB: HTML price extraction
# ============================================================
def test_wb_html_prices():
    print(f"\n{SEP}\nWB HTML PRICE PATTERNS\n{SEP}")
    import requests
    
    nm_id = 199298871
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    
    try:
        r = s.get(url, timeout=(10, 25))
        print(f"  Status: {r.status_code}, Bytes: {len(r.content)}")
        html = r.text
        
        # og:price
        m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        print(f"  og:price:amount: {m.group(1) if m else 'NOT FOUND'}")
        
        # data-price
        m = re.search(r'data-price=["\'](\d+)["\']', html, re.I)
        print(f"  data-price: {m.group(1) if m else 'NOT FOUND'}")
        
        # JSON-LD price
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(m.group(1).strip())
                if isinstance(data, dict) and data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    if isinstance(offers, dict):
                        offers = [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price"):
                            print(f"  JSON-LD price: {o['price']} {o.get('priceCurrency','?')}")
            except:
                pass
        
        # Embedded state / __NEXT_DATA__ / window.__
        for pattern in [r'window\.__NEXT_DATA__\s*=\s*(\{.*?\});?\s*</script>',
                       r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>',
                       r'"price"\s*:\s*(\d+)']:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                val = m.group(1)
                print(f"  Pattern '{pattern[:40]}': found, len={len(val)}, preview={val[:100]}")
        
        # Generic: find all price-like patterns
        prices_found = re.findall(r'(\d{2,7})\s*(?:₽|руб)', html)
        if prices_found:
            print(f"  Price-like text matches: {prices_found[:10]}")
        
        # Check for embedded JSON with "sizes" array (WB specific)
        m = re.search(r'"sizes"\s*:\s*\[(.*?)\]', html, re.DOTALL)
        if m:
            print(f"  'sizes' array found, len={len(m.group(1))}, preview={m.group(1)[:150]}")
        
        # Check for "priceU" or "priceD" (WB internal)
        for key in ["priceU", "priceD", "priceBasic", "productPrice"]:
            m = re.search(rf'"{key}"\s*:\s*(\d+)', html)
            if m:
                print(f"  {key}: {m.group(1)}")
                
    except Exception as e:
        print(f"  ERROR: {e}")


def main():
    test_farpost_redirect()
    test_wb_prices()
    test_wb_html_prices()
    print(f"\n{SEP}\nDONE\n{SEP}")


if __name__ == "__main__":
    main()
