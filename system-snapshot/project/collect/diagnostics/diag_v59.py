"""Диагностика v59: WB card.json FULL dump + price + Avito desktop active ads.
Запуск: C:/Python315/python.exe diag_v59.py
"""
import json, re, sys, time, traceback
from pathlib import Path

OUT = Path(__file__).parent / "diag_v59_out.txt"
lines = []

def log(msg):
    lines.append(msg)
    print(msg, flush=True)

def save():
    OUT.write_text("\n".join(lines), encoding="utf-8")

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

def fetch(url, timeout=15, headers=None):
    if HAS_CFFI:
        kw = {"impersonate": "chrome", "timeout": timeout}
        if headers:
            kw["headers"] = headers
        return cffi_req.get(url, **kw)
    else:
        import requests as std_req
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        if headers:
            h.update(headers)
        return std_req.get(url, timeout=timeout, headers=h)

# ============================================================
# PART 1: WB BASKET CDN — FULL JSON DUMP
# ============================================================
log("=" * 60)
log("PART 1: WB BASKET CDN — FULL JSON DUMP")
log("=" * 60)

WB_TEST_IDS = ["199298871", "1097975275"]
host_cache = {}

def find_basket_host(nm_id_str):
    nm = int(nm_id_str)
    vol = nm // 100000
    part = nm // 1000
    path = f"/vol{vol}/part{part}/{nm_id_str}/info/ru/card.json"
    if vol in host_cache:
        host = f"basket-{host_cache[vol]:02d}.wbbasket.ru"
        r = fetch(f"https://{host}{path}", timeout=10)
        if r.status_code == 200:
            return host, r
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        r = fetch(f"https://{host}{path}", timeout=10)
        if r.status_code == 200:
            host_cache[vol] = n
            return host, r
    return None, None

for nm in WB_TEST_IDS:
    log(f"\n--- BASKET nm={nm} ---")
    host, r = find_basket_host(nm)
    if not host:
        log("  NOT FOUND")
        continue
    log(f"  host={host}, HTTP {r.status_code}, {len(r.text)} bytes")
    try:
        card = r.json()
        # Save full JSON
        save_path = Path(__file__).parent / f"diag_v59_wb_{nm}.json"
        save_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  saved: {save_path.name}")
        
        # ALL top-level keys
        log(f"  KEYS ({len(card.keys())}): {sorted(card.keys())}")
        
        # Scalar fields
        for k in ["imt_id", "nm_id", "imt_name", "slug", "vendor_code",
                  "subj_name", "subj_root_name", "certificate", "colors"]:
            v = card.get(k)
            log(f"  {k} = {repr(v)[:120]}")
        
        # Description
        desc = card.get("description", "")
        log(f"  description: {len(desc)} chars")
        
        # Options
        opts = card.get("options", [])
        log(f"  options: {len(opts)} items")
        if opts:
            log(f"    option[0] keys: {list(opts[0].keys()) if isinstance(opts[0], dict) else type(opts[0])}")
            for o in opts[:5]:
                if isinstance(o, dict):
                    log(f"    {o.get('id','?')}: {o.get('name','?')} = {str(o.get('value',''))[:60]}")
        
        # Media — FULL structure
        media = card.get("media", [])
        log(f"  media: {len(media)} items")
        if media:
            log(f"    media[0] keys: {list(media[0].keys()) if isinstance(media[0], dict) else type(media[0])}")
            for i, m in enumerate(media[:6]):
                if isinstance(m, dict):
                    log(f"    [{i}] type={m.get('type','?')} key={m.get('key','?')[:80]}")
                    for mk, mv in m.items():
                        if mk not in ("type", "key"):
                            log(f"         {mk}={repr(mv)[:80]}")
                else:
                    log(f"    [{i}] raw: {repr(m)[:100]}")
        
        # Compositions
        comps = card.get("compositions", [])
        log(f"  compositions: {len(comps)} items")
        if comps and isinstance(comps[0], dict):
            log(f"    comp[0] keys: {list(comps[0].keys())}")
        
        # Any other interesting fields
        for k in ["imt_group_name", "imt_group_id", "imt_group_url", "imt_group_url_name",
                  "imt_group_url_name", "brand", "brand_name", "model", "article"]:
            if k in card:
                log(f"  {k} = {repr(card[k])[:100]}")
        
    except Exception as e:
        log(f"  PARSE ERROR: {e}")
        log(f"  first 300: {r.text[:300]}")
    time.sleep(1)

# ============================================================
# PART 2: WB PRICE — Card API v4 / v2 / Search
# ============================================================
log("\n" + "=" * 60)
log("PART 2: WB PRICE SOURCES")
log("=" * 60)

# Try Card API v4
for api_ver in ["v4", "v2"]:
    url = f"https://card.wb.ru/cards/{api_ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=199298871"
    log(f"\n--- Card API {api_ver} ---")
    log(f"  URL: {url[:100]}")
    try:
        r = fetch(url, timeout=15, headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.wildberries.ru/",
            "Origin": "https://www.wildberries.ru",
        })
        log(f"  HTTP {r.status_code}, len={len(r.text)}")
        if r.status_code == 200:
            data = r.json()
            prods = data.get("products", data.get("data", {}).get("items", []))
            log(f"  products: {len(prods)}")
            if prods:
                p = prods[0]
                sizes = p.get("sizes", [])
                if sizes:
                    pr = sizes[0].get("price", {})
                    log(f"  price: {json.dumps(pr)}")
                    log(f"  qty: {sizes[0].get('qty')}")
                log(f"  name: {p.get('name','?')[:60]}")
                log(f"  brand: {p.get('brand','?')}")
    except Exception as e:
        log(f"  ERROR: {e}")
    time.sleep(2)

# Try Search API
log(f"\n--- Search API ---")
search_url = "https://search.wb.ru/exactmatch/ru/common/v5/search?query=iphone&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&page=1"
try:
    r = fetch(search_url, timeout=15, headers={
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
    })
    log(f"  HTTP {r.status_code}, len={len(r.text)}")
    if r.status_code == 200:
        data = r.json()
        prods = data.get("data", {}).get("products", data.get("products", []))
        log(f"  products: {len(prods)}, total: {data.get('data',{}).get('total', data.get('total','?'))}")
        if prods:
            p = prods[0]
            log(f"  [{p.get('id')}] {p.get('name','?')[:50]} brand={p.get('brand','?')}")
            sizes = p.get("sizes", [])
            if sizes:
                pr = sizes[0].get("price", {})
                log(f"  price: {json.dumps(pr)}")
except Exception as e:
    log(f"  ERROR: {e}")

# ============================================================
# PART 3: AVITO DESKTOP — find active ads via Playwright
# ============================================================
log("\n" + "=" * 60)
log("PART 3: AVITO DESKTOP (Playwright)")
log("=" * 60)

try:
    from playwright.sync_api import sync_playwright
    
    # Strategy: go to a category page, find ad links, then open one
    AVITO_CATEGORIES = [
        "https://www.avito.ru/moskva/telefony",
        "https://www.avito.ru/moskva/kvartiry/prodam",
    ]
    
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="ru-RU",
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        
        ad_url_found = None
        
        for cat_url in AVITO_CATEGORIES:
            log(f"\n--- CATEGORY: {cat_url} ---")
            try:
                resp = page.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)
                status = resp.status if resp else "?"
                title = page.title()
                content = page.content()
                log(f"  HTTP {status}, len={len(content)}, title={title[:60]}")
                
                # Check for captcha/block
                if "проверка" in content[:5000].lower() or "captcha" in content[:5000].lower():
                    log("  VERDICT: CAPTCHA/BLOCK")
                    continue
                
                # Find ad links
                ad_links = re.findall(r'href="(/[^"]+/\d{8,})"', content)
                log(f"  ad links found: {len(ad_links)}")
                for al in ad_links[:5]:
                    log(f"    {al}")
                
                if ad_links and not ad_url_found:
                    ad_url_found = f"https://www.avito.ru{ad_links[0]}"
                    log(f"  SELECTED: {ad_url_found}")
                    
            except Exception as e:
                log(f"  ERROR: {e}")
                traceback.print_exc()
            time.sleep(2)
        
        # Open the found ad
        if ad_url_found:
            log(f"\n--- AD CARD: {ad_url_found} ---")
            try:
                resp = page.goto(ad_url_found, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                status = resp.status if resp else "?"
                title = page.title()
                content = page.content()
                log(f"  HTTP {status}, len={len(content)}, title={title[:80]}")
                
                # Check for not-found
                if "not-found" in content[:5000] or "не найдено" in content[:5000].lower():
                    log("  VERDICT: 404 / NOT FOUND")
                elif "captcha" in content[:5000].lower():
                    log("  VERDICT: CAPTCHA")
                else:
                    log("  VERDICT: PAGE OK")
                    
                    # Extract data
                    # JSON-LD
                    ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content, re.DOTALL)
                    log(f"  JSON-LD blocks: {len(ld_blocks)}")
                    for i, lb in enumerate(ld_blocks[:3]):
                        try:
                            ld = json.loads(lb)
                            log(f"    [{i}] type={ld.get('@type','?')} keys={list(ld.keys())[:10]}")
                            if ld.get("@type") == "Product":
                                log(f"        name={ld.get('name','?')[:60]}")
                                offers = ld.get("offers", {})
                                log(f"        offers={json.dumps(offers, ensure_ascii=False)[:200]}")
                        except:
                            log(f"    [{i}] parse err, first 100: {lb[:100]}")
                    
                    # Meta tags
                    for prop in ["og:title", "og:description", "og:image", "og:price:amount"]:
                        m = re.search(rf'<meta[^>]*property=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)', content, re.I)
                        if m:
                            log(f"  meta {prop} = {m.group(1)[:80]}")
                    
                    # Price in HTML
                    price_matches = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', content[:20000])
                    log(f"  price matches: {price_matches[:5]}")
                    
                    # Ad ID
                    ad_id_m = re.search(r'/(\d{8,12})["\s?]', ad_url_found)
                    log(f"  ad_id from URL: {ad_id_m.group(1) if ad_id_m else '?'}")
                    
                    # Save
                    save_name = f"diag_v59_avito_{ad_url_found.split('/')[-1][:20]}.html"
                    save_path = Path(__file__).parent / save_name
                    save_path.write_text(content, encoding="utf-8")
                    log(f"  saved: {save_name} ({len(content)} bytes)")
                    
            except Exception as e:
                log(f"  ERROR: {e}")
                traceback.print_exc()
        
        # If no ad found from category, try direct known URLs
        if not ad_url_found:
            log("\n--- DIRECT AVITO URLS (desktop) ---")
            direct_urls = [
                "https://www.avito.ru/moskva/telefony/iphone_14_128_gb_8341658309",
                "https://www.avito.ru/novosemeykino/zemelnye_uchastki/uchastok_494_ga_promnaznacheniya_7764972140",
            ]
            for du in direct_urls:
                log(f"\n  URL: {du}")
                try:
                    resp = page.goto(du, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(4000)
                    status = resp.status if resp else "?"
                    title = page.title()
                    content = page.content()
                    log(f"  HTTP {status}, len={len(content)}, title={title[:60]}")
                    if "not-found" in content[:5000]:
                        log("  VERDICT: 404")
                    elif len(content) > 10000:
                        log("  VERDICT: PAGE (check content)")
                        # Quick check
                        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content, re.DOTALL)
                        log(f"  JSON-LD: {len(ld_blocks)}")
                        price_m = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', content[:20000])
                        log(f"  prices: {price_m[:3]}")
                except Exception as e:
                    log(f"  ERROR: {e}")
                time.sleep(2)
        
        browser.close()
    log("  Playwright done")
except Exception as e:
    log(f"  PLAYWRIGHT ERROR: {e}")
    traceback.print_exc()

# ============================================================
# SUMMARY
# ============================================================
log("\n" + "=" * 60)
log("SUMMARY v59")
log("=" * 60)
log(f"  curl_cffi: {'YES' if HAS_CFFI else 'NO'}")
log(f"  WB basket hosts: {host_cache}")
log("DONE")

save()
print(f"\nSaved to {OUT}")
