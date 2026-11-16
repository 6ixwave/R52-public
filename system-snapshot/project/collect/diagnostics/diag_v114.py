"""Диагностика v114: живой Farpost + WB CARD_JSON dump + WB HTML retry."""
import sys, json, re, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SEP = "=" * 60
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ============================================================
# 1. FARPOST: найти живое объявление через поиск
# ============================================================
def test_farpost_find():
    print(f"\n{SEP}\nFARPOST: FIND LIVE LISTING\n{SEP}")
    import requests
    
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    
    # Warmup
    r = s.get("https://www.farpost.ru/", timeout=(10, 30))
    print(f"Warmup: {r.status_code}, cookies={list(s.cookies.keys())}")
    time.sleep(2)
    
    # Search for a product (e.g., "нерка" or "iphone" or "велосипед")
    # Farpost search URL: https://www.farpost.ru/search/?query=...
    search_url = "https://www.farpost.ru/search/?query=%D0%BD%D0%B5%D1%80%D0%BA%D0%B0"
    print(f"\nSearch: {search_url}")
    try:
        r = s.get(search_url, timeout=(10, 30))
        print(f"  Status: {r.status_code}, Bytes: {len(r.content)}")
        
        # Extract bulletin links
        # Pattern: /{city}/{category}/...-{id}.html or /bulletin/{id}
        links = re.findall(r'href="(/[^"]*?-\d{6,}\.html)"', r.text)
        if not links:
            links = re.findall(r'href="(/bulletin/\d+)"', r.text)
        print(f"  Found {len(links)} bulletin links")
        for l in links[:10]:
            print(f"    {l}")
        
        # Try first link
        if links:
            target = f"https://www.farpost.ru{links[0]}"
            print(f"\n  Testing: {target}")
            time.sleep(3)
            r2 = s.get(target, timeout=(10, 30))
            print(f"  Status: {r2.status_code}, Bytes: {len(r2.content)}")
            if r2.status_code == 200 and len(r2.content) > 5000:
                # Extract key data
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r2.text)
                print(f"  Price (data-bulletin-price): {m.group(1) if m else 'NOT FOUND'}")
                
                ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r2.text, re.DOTALL)
                for i, block in enumerate(ld_blocks[:5]):
                    try:
                        data = json.loads(block.strip())
                        t = data.get("@type", "?") if isinstance(data, dict) else "list"
                        name = data.get("name", "") if isinstance(data, dict) else ""
                        offers = data.get("offers", {}) if isinstance(data, dict) else {}
                        price = offers.get("price") if isinstance(offers, dict) else (offers[0].get("price") if offers else None)
                        print(f"  LD[{i}]: type={t}, name={str(name)[:50]}, price={price}")
                    except:
                        pass
                
                # Gallery
                imgs = re.findall(r'data-image-info="(\{.*?\})"', r2.text)
                print(f"  Gallery (data-image-info): {len(imgs)} items")
                
                # Bulletin ID
                m = re.search(r'bulletinId=["\'](\d+)["\']', r2.text)
                if not m:
                    m = re.search(r'/(\d{6,})\.html', r2.url)
                print(f"  Bulletin ID: {m.group(1) if m else 'NOT FOUND'}")
                
                # Title
                m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', r2.text, re.DOTALL)
                if m:
                    print(f"  Title: {re.sub(r'<[^>]+>','',m.group(1)).strip()[:80]}")
                
                return links[0]
            else:
                print(f"  Short or non-200 response")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    # Fallback: try known working path pattern
    print("\n  Fallback: trying /krasnoyarsk/... path")
    time.sleep(3)
    # Try a generic category page
    for cat_url in [
        "https://www.farpost.ru/krasnoyarsk/auto/",
        "https://www.farpost.ru/krasnoyarsk/electronics/",
    ]:
        try:
            r = s.get(cat_url, timeout=(10, 30))
            print(f"  {cat_url}: {r.status_code}, {len(r.content)}B")
            links = re.findall(r'href="(/[^"]*?-\d{6,}\.html)"', r.text)
            if links:
                print(f"    Found {len(links)} links, first: {links[0]}")
                target = f"https://www.farpost.ru{links[0]}"
                time.sleep(3)
                r2 = s.get(target, timeout=(10, 30))
                if r2.status_code == 200 and len(r2.content) > 5000:
                    m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r2.text)
                    print(f"    -> {target}: {r2.status_code}, price={m.group(1) if m else '?'}")
                    return links[0]
        except Exception as e:
            print(f"    ERROR: {e}")
        time.sleep(2)
    
    return None


# ============================================================
# 2. WB: Full CARD_JSON dump
# ============================================================
def test_wb_card_json():
    print(f"\n{SEP}\nWB: CARD_JSON FULL DUMP\n{SEP}")
    import requests
    
    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000
    
    # Find basket host
    host = None
    for n in range(1, 61):
        h = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{h}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        try:
            r = requests.get(url, timeout=(8, 15), headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Referer": "https://www.wildberries.ru/",
            })
            if r.status_code == 200:
                host = h
                break
        except:
            continue
        time.sleep(1)
    
    if not host:
        print("  Basket host not found!")
        return
    
    print(f"  Host: {host}")
    url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    r = requests.get(url, timeout=(10, 20), headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
    })
    print(f"  Status: {r.status_code}, Bytes: {len(r.content)}")
    
    if r.status_code == 200:
        data = r.json()
        print(f"  Keys: {sorted(data.keys())}")
        print(f"  nm_id: {data.get('nm_id')}")
        print(f"  imt_name: {str(data.get('imt_name',''))[:80]}")
        print(f"  selling: {json.dumps(data.get('selling', {}), ensure_ascii=False)[:200]}")
        print(f"  media: {json.dumps(data.get('media', {}), ensure_ascii=False)[:200]}")
        print(f"  options count: {len(data.get('options', []))}")
        
        # Check for price-related keys
        price_keys = [k for k in data.keys() if 'price' in k.lower() or 'cost' in k.lower() or 'amount' in k.lower()]
        print(f"  Price-related keys: {price_keys}")
        
        # Check selling dict for price
        selling = data.get("selling", {})
        if isinstance(selling, dict):
            print(f"  selling keys: {sorted(selling.keys())}")
            for k, v in selling.items():
                if 'price' in k.lower() or 'cost' in k.lower():
                    print(f"    selling.{k} = {v}")
        
        # Full dump to file
        dump_path = Path(__file__).parent / "wb_card_json_dump.json"
        dump_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Full dump: {dump_path} ({dump_path.stat().st_size} bytes)")
        
        # Check for embedded price in options or other fields
        for opt in data.get("options", []):
            if isinstance(opt, dict):
                name = opt.get("name", "").lower()
                if any(x in name for x in ("цен", "price", "стоимост")):
                    print(f"  Option price: {opt}")
        
        # Check for "price" in the raw JSON text
        raw = r.text
        for pattern in [r'"price[^"]*"\s*:\s*\{[^}]*\}', r'"priceU"\s*:\s*\d+', r'"priceD"\s*:\s*\d+']:
            m = re.search(pattern, raw)
            if m:
                print(f"  Pattern {pattern[:30]}: {m.group(0)[:100]}")
    
    return data if r.status_code == 200 else None


# ============================================================
# 3. WB: HTML with long delay (avoid 498)
# ============================================================
def test_wb_html_retry():
    print(f"\n{SEP}\nWB: HTML RETRY (long delay)\n{SEP}")
    import requests
    
    nm_id = 199298871
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    
    print("  Waiting 15s before HTML request...")
    time.sleep(15)
    
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
    })
    
    for attempt in range(3):
        try:
            r = s.get(url, timeout=(10, 30))
            print(f"  Attempt {attempt+1}: HTTP {r.status_code}, {len(r.content)}B")
            
            if r.status_code == 200 and len(r.content) > 10000:
                html = r.text
                # Price patterns
                m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
                print(f"    og:price:amount: {m.group(1) if m else 'NOT FOUND'}")
                
                m = re.search(r'data-price=["\'](\d+)["\']', html, re.I)
                print(f"    data-price: {m.group(1) if m else 'NOT FOUND'}")
                
                # JSON-LD
                for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                    try:
                        data = json.loads(m.group(1).strip())
                        if isinstance(data, dict) and data.get("@type") == "Product":
                            offers = data.get("offers", {})
                            if isinstance(offers, dict):
                                offers = [offers]
                            for o in offers:
                                if isinstance(o, dict) and o.get("price"):
                                    print(f"    JSON-LD: {o['price']} {o.get('priceCurrency','?')}")
                    except:
                        pass
                
                # Embedded state
                m = re.search(r'window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
                if m:
                    print(f"    __NEXT_DATA__: found ({len(m.group(1))} chars)")
                
                # Generic price text
                prices = re.findall(r'(\d{2,7})\s*(?:₽|руб)', html)
                print(f"    Price text matches: {prices[:10]}")
                
                # Media
                imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']', html, re.I)
                print(f"    Media URLs: {len(imgs)}")
                for i in imgs[:5]:
                    print(f"      {i[:90]}")
                
                return True
            elif r.status_code == 498:
                print(f"    498 soft-block, body: {r.text[:100]}")
            elif r.status_code == 200:
                print(f"    200 but short ({len(r.content)}B)")
        except Exception as e:
            print(f"    ERROR: {e}")
        
        if attempt < 2:
            wait = 10 * (attempt + 1)
            print(f"  Waiting {wait}s...")
            time.sleep(wait)
    
    return False


def main():
    fp_path = test_farpost_find()
    wb_data = test_wb_card_json()
    wb_html_ok = test_wb_html_retry()
    
    print(f"\n{SEP}\nSUMMARY\n{SEP}")
    print(f"  Farpost live path: {fp_path or 'NOT FOUND'}")
    print(f"  WB CARD_JSON: {'OK' if wb_data else 'FAILED'}")
    print(f"  WB HTML: {'OK' if wb_html_ok else 'FAILED'}")
    print(f"  DONE")


if __name__ == "__main__":
    main()
