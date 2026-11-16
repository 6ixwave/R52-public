"""DIAG V123: WB IMAGE/PRICE FINAL + FARPOST FULL-SLUG (2026-11-12)

Фокус:
1. WB: новые паттерны изображений (ir-XX без бага, basket с _N.suffix), mobile API, search
2. Farpost: full-slug URLs из category page, ?u= с полным путём, прямой fetch
"""
import json, re, sys, time, os
from datetime import datetime

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v123_result.log")
lines = []

def log(msg=""):
    print(msg)
    lines.append(msg)

def header(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)

def main():
    log(f"DIAG V123: WB IMAGE/PRICE FINAL + FARPOST FULL-SLUG  ({datetime.now().strftime('%Y-%m-%d')})")
    
    from curl_cffi import requests as cr
    c = cr.Session(impersonate="chrome")
    
    # ============================================================
    # PART 1: WB IMAGES — NEW PATTERNS
    # ============================================================
    header("PART 1: WB IMAGE PATTERNS (FIXED)")
    
    # Test cards
    cards = [
        {"nm_id": 259671175, "imt_id": 236868886, "vol": 2596, "part": 259671},
        {"nm_id": 199298871, "imt_id": 158033827, "vol": 1992, "part": 199298},
    ]
    
    # First: confirm basket host for each vol
    basket_hosts = {}
    for card in cards:
        nm = card["nm_id"]
        vol = card["vol"]
        path = f"/vol{vol}/part{card['part']}/{nm}/info/ru/card.json"
        found = False
        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            try:
                r = c.get(url, timeout=8, headers={"Accept": "application/json"})
                if r.status_code == 200:
                    basket_hosts[vol] = n
                    log(f"  card {nm}: basket-{n:02d}.wbbasket.ru OK ({len(r.content)}B)")
                    found = True
                    break
            except Exception:
                continue
        if not found:
            log(f"  card {nm}: basket host NOT FOUND for vol={vol}")
    
    # Test image patterns on basket CDN (with _N suffix)
    log("\n--- 1A: Basket CDN image patterns (with _N suffix) ---")
    card = cards[0]
    nm, imt, vol, part = card["nm_id"], card["imt_id"], card["vol"], card["part"]
    bh = basket_hosts.get(vol, 16)
    base = f"https://basket-{bh:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/images"
    
    patterns = [
        f"/S800/{imt}.jpg",
        f"/S800/{imt}_1.jpg",
        f"/S800/{imt}_2.jpg",
        f"/S800/{nm}.jpg",
        f"/S800/{nm}_1.jpg",
        f"/S800/1.jpg",
        f"/S800/2.jpg",
        f"/S800/3.jpg",
        f"/S800/{imt}/1.jpg",
        f"/S800/{imt}/2.jpg",
        f"/S120/{imt}.jpg",
        f"/S120/{imt}_1.jpg",
        f"/S120/{nm}_1.jpg",
        f"/S400/{imt}.jpg",
        f"/S100/{imt}.jpg",
        f"/big/{imt}.jpg",
        f"/big/{imt}_1.jpg",
        f"/{imt}.jpg",
        f"/{imt}_1.jpg",
    ]
    
    ok_count = 0
    for pat in patterns:
        url = base + pat
        try:
            r = c.get(url, timeout=8)
            ct = r.headers.get("Content-Type", "")
            sz = len(r.content)
            status = "OK" if r.status_code == 200 and "image" in ct else "xx"
            if status == "OK":
                ok_count += 1
            log(f"  [{status}] {r.status_code} {sz:>6}B {ct[:30]:30} {pat}")
        except Exception as e:
            log(f"  [ER] {str(e)[:60]} {pat}")
    
    log(f"  Basket CDN: {ok_count}/{len(patterns)} OK")
    
    # Test ir-XX.wbxstatic.ru (FIXED: no space in hostname)
    log("\n--- 1B: ir-XX.wbxstatic.ru (FIXED hostname) ---")
    ok_ir = 0
    for n in [1, 2, 3, 5, 10, 15, 20, 30, 40, 50]:
        host = f"ir-{n:02d}.wbxstatic.ru"
        for pat in [f"/{imt}/S800/1.jpg", f"/{imt}/S800/{imt}.jpg", f"/S800/{imt}.jpg"]:
            url = f"https://{host}{pat}"
            try:
                r = c.get(url, timeout=6)
                ct = r.headers.get("Content-Type", "")
                sz = len(r.content)
                status = "OK" if r.status_code == 200 and "image" in ct else "xx"
                if status == "OK":
                    ok_ir += 1
                    log(f"  [{status}] {r.status_code} {sz:>6}B {ct[:30]:30} {host}{pat}")
                else:
                    log(f"  [{status}] {r.status_code} {sz:>6}B {ct[:30]:30} {host}{pat}")
            except Exception as e:
                log(f"  [ER] {str(e)[:50]} {host}{pat}")
            if r.status_code == 200:
                break  # found working host, skip other patterns
        if ok_ir > 0:
            break
    log(f"  ir-XX.wbxstatic.ru: {ok_ir} OK")
    
    # Test ir-XX.wbstatic.net
    log("\n--- 1C: ir-XX.wbstatic.net ---")
    ok_ir2 = 0
    for n in [1, 2, 3, 5, 10, 15, 20]:
        host = f"ir-{n:02d}.wbstatic.net"
        for pat in [f"/{imt}/S800/1.jpg", f"/S800/{imt}.jpg"]:
            url = f"https://{host}{pat}"
            try:
                r = c.get(url, timeout=6)
                ct = r.headers.get("Content-Type", "")
                sz = len(r.content)
                status = "OK" if r.status_code == 200 and "image" in ct else "xx"
                if status == "OK":
                    ok_ir2 += 1
                    log(f"  [{status}] {r.status_code} {sz:>6}B {ct[:30]:30} {host}{pat}")
                else:
                    log(f"  [{status}] {r.status_code} {sz:>6}B {ct[:30]:30} {host}{pat}")
            except Exception as e:
                log(f"  [ER] {str(e)[:50]} {host}{pat}")
        if ok_ir2 > 0:
            break
    log(f"  ir-XX.wbstatic.net: {ok_ir2} OK")
    
    # Test static.wbstatic.net (different path structure)
    log("\n--- 1D: static.wbstatic.net / wbxstatic.ru direct ---")
    for host in ["static.wbstatic.net", "wbxstatic.ru", "ir.wbxstatic.ru"]:
        for pat in [f"/{imt}/S800/1.jpg", f"/{imt}/S800/{imt}.jpg", f"/images/{imt}.jpg"]:
            url = f"https://{host}{pat}"
            try:
                r = c.get(url, timeout=6)
                ct = r.headers.get("Content-Type", "")
                sz = len(r.content)
                status = "OK" if r.status_code == 200 and "image" in ct else "xx"
                log(f"  [{status}] {r.status_code} {sz:>6}B {ct[:30]:30} {host}{pat}")
            except Exception as e:
                log(f"  [ER] {str(e)[:50]} {host}{pat}")
    
    # ============================================================
    # PART 2: WB PRICE — MOBILE/SEARCH/ALT
    # ============================================================
    header("PART 2: WB PRICE ALTERNATIVES")
    
    nm = 259671175
    endpoints = [
        ("search_v5", f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&spp=30&query={nm}"),
        ("search_v5_nm", f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("card_v2_app", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}&sup=1"),
        ("card_v2_mobile", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}&sup=1&lang=ru"),
        ("web_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}&curr=rub"),
        ("web_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm}&curr=rub"),
        ("basket_price", f"https://basket-{bh:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/price.json"),
        ("basket_seller", f"https://basket-{bh:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/seller.json"),
        ("basket_stock", f"https://basket-{bh:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/stock.json"),
    ]
    
    for label, url in endpoints:
        try:
            r = c.get(url, timeout=10, headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.wildberries.ru/",
            })
            sz = len(r.content)
            ct = r.headers.get("Content-Type", "")
            log(f"  [{label}] {r.status_code} {sz:>6}B {ct[:30]:30}")
            if r.status_code == 200 and sz > 10:
                try:
                    data = r.json()
                    # Look for price fields
                    s = json.dumps(data, ensure_ascii=False)
                    if "price" in s.lower() or "basic" in s.lower():
                        log(f"    → HAS PRICE DATA! (first 300: {s[:300]})")
                    else:
                        log(f"    → no price field. keys={list(data.keys())[:10] if isinstance(data, dict) else type(data)}")
                except Exception:
                    log(f"    → not JSON. body[:200]={r.text[:200]}")
        except Exception as e:
            log(f"  [{label}] ER: {str(e)[:60]}")
        time.sleep(0.5)
    
    # Try WB mobile API (different endpoint)
    log("\n--- 2B: WB mobile/app API ---")
    mobile_urls = [
        f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}",
        f"https://card.wb.ru/cards/v2/detail?curr=rub&nm={nm}",
        f"https://card.wb.ru/cards/detail?curr=rub&nm={nm}",
    ]
    for url in mobile_urls:
        try:
            r = c.get(url, timeout=10, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
            })
            sz = len(r.content)
            log(f"  [{r.status_code}] {sz:>6}B {url[:70]}")
            if r.status_code == 200 and sz > 10:
                log(f"    body[:200]={r.text[:200]}")
        except Exception as e:
            log(f"  [ER] {str(e)[:50]} {url[:60]}")
    
    # ============================================================
    # PART 3: FARPOST — FULL-SLUG URL DISCOVERY
    # ============================================================
    header("PART 3: FARPOST FULL-SLUG URLS")
    
    # 3A: Get category page, extract full listing URLs
    log("\n--- 3A: Category page → full URLs ---")
    try:
        r = c.get("https://www.farpost.ru/", timeout=15)
        log(f"  Warmup: {r.status_code}, {len(r.content)}B, cookies={list(r.cookies.keys())}")
        time.sleep(1)
        
        r = c.get("https://www.farpost.ru/krasnoyarsk/zapchasti/", timeout=20)
        log(f"  Category: {r.status_code}, {len(r.content)}B")
        
        html = r.text
        # Extract ALL href patterns that look like listing pages
        # Pattern 1: /{city}/{category}/.../{slug}-{id}.html
        full_urls = re.findall(
            r'href="(https://www\.farpost\.ru/(?:[a-z]+/)+[a-z0-9\-]+-\d{5,}\.html)"',
            html
        )
        # Pattern 2: /{city}/{category}/.../{slug}-{id} (no .html)
        full_urls2 = re.findall(
            r'href="(https://www\.farpost\.ru/(?:[a-z]+/)+[a-z0-9\-]+-\d{5,})"',
            html
        )
        # Pattern 3: relative paths
        rel_urls = re.findall(
            r'href="(/[a-z]+/[a-z]+/[a-z0-9\-/]+-\d{5,}(?:\.html)?)"',
            html
        )
        # Pattern 4: bulletin
        bull_urls = re.findall(
            r'href="(https://www\.farpost\.ru/bulletin/\d+)"',
            html
        )
        
        all_urls = list(set(full_urls + full_urls2 + [f"https://www.farpost.ru{u}" for u in rel_urls]))
        bull_set = set(bull_urls)
        
        log(f"  Full-slug URLs: {len(full_urls)} (unique: {len(set(full_urls))})")
        log(f"  Full-slug no-ext: {len(full_urls2)}")
        log(f"  Relative URLs: {len(rel_urls)}")
        log(f"  Bulletin URLs: {len(bull_set)}")
        
        # Show samples
        unique_full = list(set(full_urls + full_urls2))[:10]
        for u in unique_full:
            log(f"    {u}")
        
        # 3B: Try fetching full-slug URLs directly
        log("\n--- 3B: Fetch full-slug URLs directly ---")
        test_urls = unique_full[:5]
        for i, url in enumerate(test_urls, 1):
            try:
                r2 = c.get(url, timeout=15)
                sz = len(r2.content)
                ct = r2.headers.get("Content-Type", "")
                enc = r2.encoding or r2.apparent_encoding or "?"
                log(f"\n  [{i}] {url}")
                log(f"      HTTP {r2.status_code}, {sz}B, enc={enc}")
                
                if r2.status_code == 200 and sz > 5000:
                    h = r2.text
                    # Check for product data
                    has_price = 'data-bulletin-price' in h
                    has_ld = 'application/ld+json' in h
                    has_gallery = 'data-image-info' in h or 'lightbox' in h
                    has_lightbox = 'baza.lightbox.init' in h
                    title_m = re.search(r'<title[^>]*>([^<]+)</title>', h)
                    title = title_m.group(1)[:60] if title_m else "?"
                    
                    log(f"      title={title}")
                    log(f"      price_attr={has_price}, jsonld={has_ld}, gallery={has_gallery}, lightbox={has_lightbox}")
                    
                    # Extract price if present
                    pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', h)
                    if pm:
                        log(f"      PRICE: {pm.group(1)} RUB")
                    
                    # Extract JSON-LD
                    for lm in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', h, re.DOTALL):
                        try:
                            ld = json.loads(lm.group(1).strip())
                            t = ld.get("@type", "?")
                            if t == "Product":
                                log(f"      JSON-LD Product: name={str(ld.get('name','?'))[:50]}")
                                offers = ld.get("offers", {})
                                if isinstance(offers, dict):
                                    log(f"        price={offers.get('price')}, currency={offers.get('priceCurrency')}")
                                elif isinstance(offers, list) and offers:
                                    log(f"        offers[0]: price={offers[0].get('price')}")
                        except Exception:
                            pass
                    
                    # Save first successful page
                    if has_price or has_ld:
                        save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "farpost_v123_success.html")
                        with open(save_path, "w", encoding="utf-8") as f:
                            f.write(h)
                        log(f"      SAVED → {os.path.basename(save_path)} ({sz}B)")
                        break  # one success is enough
                elif r2.status_code == 404:
                    log(f"      404 page ({sz}B)")
                else:
                    log(f"      body[:100]={r2.text[:100]}")
            except Exception as e:
                log(f"  [{i}] ER: {str(e)[:60]} {url[:60]}")
            time.sleep(1)
        
        # 3C: ?u= redirect with full slug path
        log("\n--- 3C: ?u= redirect with full slug ---")
        if unique_full:
            slug_path = unique_full[0].replace("https://www.farpost.ru", "")
            u_url = f"https://www.farpost.ru?u={slug_path}&f=1"
            try:
                r3 = c.get(u_url, timeout=15, allow_redirects=False)
                log(f"  URL: {u_url[:80]}")
                log(f"  HTTP {r3.status_code}, {len(r3.content)}B")
                loc = r3.headers.get("Location", "")
                log(f"  Location: {loc[:80]}")
                
                if r3.status_code in (301, 302):
                    # Follow redirect
                    if loc.startswith("/"):
                        loc = "https://www.farpost.ru" + loc
                    r4 = c.get(loc, timeout=15)
                    log(f"  Followed → {r4.status_code}, {len(r4.content)}B")
                    h4 = r4.text
                    pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', h4)
                    log(f"  price={pm.group(1) if pm else 'not found'}")
                    log(f"  has_ld={'application/ld+json' in h4}")
                elif r3.status_code == 200:
                    h3 = r3.text
                    pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', h3)
                    log(f"  Direct 200: price={pm.group(1) if pm else 'not found'}, size={len(h3)}B")
            except Exception as e:
                log(f"  ER: {str(e)[:60]}")
    
    except Exception as e:
        log(f"  PART 3 ERROR: {e}")
    
    # ============================================================
    # PART 4: WB — CHECK IF card.json HAS HIDDEN FIELDS
    # ============================================================
    header("PART 4: WB card.json FIELD AUDIT")
    
    card = cards[0]
    nm = card["nm_id"]
    vol = card["vol"]
    path = f"/vol{vol}/part{card['part']}/{nm}/info/ru/card.json"
    bh = basket_hosts.get(vol, 16)
    url = f"https://basket-{bh:02d}.wbbasket.ru{path}"
    try:
        r = c.get(url, timeout=10)
        data = r.json()
        log(f"  card.json keys: {sorted(data.keys())}")
        log(f"  media: {json.dumps(data.get('media', {}))}")
        log(f"  selling: {json.dumps(data.get('selling', {}))}")
        log(f"  colors: {data.get('colors', [])}")
        log(f"  full_colors: {data.get('full_colors', [])}")
        # Check for any URL-like fields
        s = json.dumps(data)
        urls_found = re.findall(r'https?://[^\s"\\]+', s)
        log(f"  URLs in card.json: {urls_found[:10]}")
        # Check for price-like fields
        for k in data.keys():
            if "price" in k.lower() or "cost" in k.lower() or "amount" in k.lower():
                log(f"  PRICE FIELD: {k} = {data[k]}")
    except Exception as e:
        log(f"  ER: {e}")
    
    # ============================================================
    # SUMMARY
    # ============================================================
    header("SUMMARY")
    log(f"  WB basket hosts: {basket_hosts}")
    log(f"  WB images basket: {ok_count}/{len(patterns)}")
    log(f"  WB images ir-XX.wbxstatic: {ok_ir}")
    log(f"  WB images ir-XX.wbstatic: {ok_ir2}")
    log(f"  Farpost full-slug URLs found: {len(unique_full)}")
    log("")
    log("DIAG V123 DONE")
    
    # Save
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nSaved to {OUT}")

if __name__ == "__main__":
    main()
