r"""probe5.py — Быстрые стратегии: WB price, Ozon entrypoint+JSONLD, Avito listing.
Run: C:\Python315\python.exe probe5.py
"""
import sys, os, json, time, re, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("P5")

RESULTS = {}

def mark(src, label, ok, detail=""):
    key = f"{src}:{label}"
    RESULTS[key] = {"ok": ok, "detail": detail}
    log.info("[%s] %s → %s %s", src, label, "PASS" if ok else "FAIL", detail[:120])

# ============================================================
# WB: 4 новые стратегии цены
# ============================================================
def wb_price_strategies(nm_id=1097975275):
    log.info("=== WB PRICE STRATEGIES (nm_id=%d) ===", nm_id)
    from curl_cffi import requests as cr

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }

    # S1: search.wb.ru/v5/search
    try:
        u = f"https://search.wb.ru/v5/search?appType=1&curr=rub&nm={nm_id}&spp=30"
        r = cr.get(u, impersonate="chrome", timeout=12, headers=headers)
        if r.status_code == 200:
            d = r.json()
            prods = d.get("data", {}).get("products", [])
            if prods:
                p = prods[0]
                sizes = p.get("sizes", [])
                prices = []
                for s in sizes[:3]:
                    pr = s.get("price", {})
                    for k in ("basic", "product", "card", "wallet", "old", "crossed"):
                        v = pr.get(k)
                        if v and v > 0:
                            prices.append(f"{k}={v}")
                mark("WB", "search_v5", True, f"products={len(prods)} prices={prices[:6]}")
                return
            mark("WB", "search_v5", False, f"200 but no products, keys={list(d.keys())[:5]}")
        else:
            mark("WB", "search_v5", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "search_v5", False, str(e)[:80])

    # S2: web base card v1
    try:
        u = f"https://www.wildberries.ru/web/api/catalog/card/wb-base/v1/{nm_id}"
        r = cr.get(u, impersonate="chrome", timeout=12, headers=headers)
        if r.status_code == 200:
            d = r.json()
            keys = list(d.keys())[:10]
            # Look for price in various places
            price_found = None
            for path in [("data", "price"), ("data", "prices"), ("price"), ("prices")]:
                obj = d
                for k in path:
                    obj = obj.get(k) if isinstance(obj, dict) else None
                if obj:
                    price_found = obj
                    break
            mark("WB", "web_base_v1", True, f"keys={keys} price={str(price_found)[:60]}")
            return
        else:
            mark("WB", "web_base_v1", False, f"HTTP {r.status_code} body[:80]={r.text[:80]}")
    except Exception as e:
        mark("WB", "web_base_v1", False, str(e)[:80])

    # S3: card.wb.ru with dest=0 (no region)
    try:
        u = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=0&spp=30&nm={nm_id}"
        r = cr.get(u, impersonate="chrome", timeout=12, headers=headers)
        if r.status_code == 200:
            d = r.json()
            prods = d.get("data", {}).get("items", []) or d.get("products", [])
            if prods:
                p = prods[0]
                sizes = p.get("sizes", [])
                prices = []
                for s in sizes[:3]:
                    pr = s.get("price", {})
                    for k in ("basic", "product", "card", "wallet"):
                        v = pr.get(k)
                        if v and v > 0:
                            prices.append(f"{k}={v}")
                mark("WB", "card_dest0", True, f"items={len(prods)} prices={prices[:6]}")
                return
            mark("WB", "card_dest0", False, f"200 no items, keys={list(d.keys())[:5]}")
        else:
            mark("WB", "card_dest0", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "card_dest0", False, str(e)[:80])

    # S4: HTML og:price / meta
    try:
        u = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        r = cr.get(u, impersonate="chrome", timeout=15, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        if r.status_code == 200:
            html = r.text
            # og:price:amount
            m = re.search(r'property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
            if not m:
                m = re.search(r'content=["\']([\d.]+)["\'][^>]*property=["\']og:price:amount["\']', html, re.I)
            # data-price
            m2 = re.search(r'data-price=["\'](\d+)["\']', html, re.I)
            # __NEXT_DATA__ or state with price
            m3 = re.search(r'"price"\s*:\s*(\d{3,})', html)
            detail = f"len={len(html)}"
            if m: detail += f" og_price={m.group(1)}"
            if m2: detail += f" data_price={m2.group(1)}"
            if m3: detail += f" json_price={m3.group(1)}"
            mark("WB", "html_meta", bool(m or m2 or m3), detail)
        else:
            mark("WB", "html_meta", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "html_meta", False, str(e)[:80])

    time.sleep(1)

# ============================================================
# OZON: entrypoint API + JSON-LD
# ============================================================
def ozon_strategies(listing_id="3481320800"):
    log.info("=== OZON STRATEGIES (id=%s) ===", listing_id)
    from curl_cffi import requests as cr

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.ozon.ru/",
    }

    # S1: entrypoint-api.bx/page/json/v2
    try:
        u = (f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
             f"?url=%2Fproduct%2F{listing_id}%2F%3F__rr%3D1%26layout_container%3DpdpPage2column")
        r = cr.get(u, impersonate="chrome", timeout=15, headers=headers)
        if r.status_code == 200:
            d = r.json()
            # Navigate layout to find price widget
            layout = d.get("layout", [])
            price_found = None
            title_found = None
            def walk(widgets):
                nonlocal price_found, title_found
                if isinstance(widgets, list):
                    for w in widgets:
                        walk(w)
                elif isinstance(widgets, dict):
                    comp = widgets.get("component", "")
                    if "price" in comp.lower() or "Price" in comp:
                        params = widgets.get("params", "{}")
                        try:
                            pd = json.loads(params) if isinstance(params, str) else params
                            price_found = pd
                        except: pass
                    if "title" in comp.lower() or "header" in comp.lower():
                        params = widgets.get("params", "{}")
                        try:
                            pd = json.loads(params) if isinstance(params, str) else params
                            if "title" in str(pd).lower():
                                title_found = pd
                        except: pass
                    for ph in widgets.get("placeholders", []):
                        walk(ph.get("widgets", []))
            walk(layout)
            # Also check top-level keys
            top_keys = list(d.keys())[:8]
            detail = f"layout_items={len(layout)} top_keys={top_keys}"
            if price_found: detail += f" price_widget={str(price_found)[:80]}"
            if title_found: detail += f" title={str(title_found)[:60]}"
            mark("OZON", "entrypoint_api", True, detail)
            # Save full response for analysis
            with open("ozon_entrypoint_full.json", "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        else:
            mark("OZON", "entrypoint_api", False, f"HTTP {r.status_code} body[:100]={r.text[:100]}")
    except Exception as e:
        mark("OZON", "entrypoint_api", False, str(e)[:80])

    # S2: JSON-LD from HTML (via curl_cffi)
    try:
        u = f"https://www.ozon.ru/product/{listing_id}/"
        r = cr.get(u, impersonate="chrome", timeout=15, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        })
        if r.status_code == 200:
            html = r.text
            # Extract all JSON-LD
            ld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL, re.I)
            product_ld = None
            for block in ld_blocks:
                try:
                    d = json.loads(block.strip())
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        product_ld = d
                        break
                    if isinstance(d, list):
                        for item in d:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                product_ld = item
                                break
                except: pass
            if product_ld:
                offers = product_ld.get("offers", {})
                price = offers.get("price") if isinstance(offers, dict) else None
                mark("OZON", "jsonld_html", True,
                     f"name={str(product_ld.get('name',''))[:50]} price={price} sku={product_ld.get('sku')}")
            else:
                mark("OZON", "jsonld_html", False,
                     f"len={len(html)} ld_blocks={len(ld_blocks)} no Product type")
        else:
            mark("OZON", "jsonld_html", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("OZON", "jsonld_html", False, str(e)[:80])

    # S3: composer-api.bx/page/json/v2 (the old one that sometimes works)
    try:
        u = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{listing_id}/"
        r = cr.get(u, impersonate="chrome", timeout=12, headers=headers)
        if r.status_code == 200:
            d = r.json()
            top_keys = list(d.keys())[:8]
            mark("OZON", "composer_api", True, f"keys={top_keys} len={len(r.text)}")
        else:
            mark("OZON", "composer_api", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("OZON", "composer_api", False, str(e)[:80])

    time.sleep(1)

# ============================================================
# AVITO: find listing + parse
# ============================================================
def avito_strategies():
    log.info("=== AVITO STRATEGIES ===")
    from curl_cffi import requests as cr

    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    # S1: Find listing from category page
    listing_id = None
    try:
        u = "https://www.avito.ru/moskva/telefony"
        r = cr.get(u, impersonate="chrome", timeout=15, headers=headers)
        if r.status_code == 200:
            html = r.text
            # Find ad links: /moskva/.../ad-{id} or /ad/{id}
            ids = re.findall(r'avito\.ru/.*/(?:ad-|)(\d{9,12})', html)
            if not ids:
                ids = re.findall(r'href="[^"]*?/(\d{9,12})"', html)
            if ids:
                listing_id = ids[0]
                mark("AVITO", "find_listing", True, f"id={listing_id} from {len(ids)} candidates")
            else:
                mark("AVITO", "find_listing", False, f"len={len(html)} no IDs found")
        else:
            mark("AVITO", "find_listing", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("AVITO", "find_listing", False, str(e)[:80])

    if not listing_id:
        listing_id = "1045231001"  # fallback
        mark("AVITO", "find_listing", False, "using fallback id")

    # S2: Fetch the listing (www + m)
    for prefix in ("www", "m"):
        try:
            u = f"https://{prefix}.avito.ru/ad/{listing_id}"
            r = cr.get(u, impersonate="chrome", timeout=15, headers=headers)
            if r.status_code == 200 and len(r.text) > 1000:
                html = r.text
                # Check for captcha
                has_captcha = "hcaptcha" in html.lower() or "smartcaptcha" in html.lower()
                # JSON-LD
                ld_blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL, re.I)
                product_ld = None
                for block in ld_blocks:
                    try:
                        d = json.loads(block.strip())
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            product_ld = d
                            break
                    except: pass
                # og: meta
                og_title = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                og_price = re.search(r'property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
                data_price = re.search(r'data-price=["\'](\d+)["\']', html, re.I)

                detail = f"len={len(html)} captcha={has_captcha} ld={len(ld_blocks)}"
                if og_title: detail += f" title={og_title.group(1)[:40]}"
                if og_price: detail += f" og_price={og_price.group(1)}"
                if data_price: detail += f" data_price={data_price.group(1)}"
                if product_ld: detail += f" ld_price={product_ld.get('offers',{}).get('price','?')}"
                mark("AVITO", f"fetch_{prefix}", True, detail)
                # Save HTML for analysis
                with open(f"avito_p5_{prefix}.html", "w", encoding="utf-8") as f:
                    f.write(html[:50000])
                break
            else:
                mark("AVITO", f"fetch_{prefix}", False,
                     f"HTTP {r.status_code} len={len(r.text) if r.text else 0}")
        except Exception as e:
            mark("AVITO", f"fetch_{prefix}", False, str(e)[:80])
        time.sleep(1)

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    t0 = time.time()
    wb_price_strategies()
    ozon_strategies()
    avito_strategies()
    dt = time.time() - t0

    log.info("=== PROBE5 DONE in %.1fs ===", dt)
    with open("probe5_results.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    for k, v in RESULTS.items():
        log.info("  %s: %s %s", k, "PASS" if v["ok"] else "FAIL", v["detail"][:100])
