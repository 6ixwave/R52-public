"""Диагностика v58: WB basket CDN (full JSON) + search.wb.ru + Avito Playwright mobile.
Запуск: C:/Python315/python.exe diag_v58_wb_avito.py
"""
import json
import re
import sys
import time
import traceback
from pathlib import Path

OUT = Path(__file__).parent / "diag_v58_out.txt"
lines = []

def log(msg):
    lines.append(msg)
    print(msg)

def save():
    OUT.write_text("\n".join(lines), encoding="utf-8")

# === WB: BASKET CDN — полный JSON ===
log("=" * 60)
log("WB BASKET CDN — full card.json")
log("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    log("curl_cffi NOT available")

def fetch(url, timeout=15):
    if HAS_CFFI:
        return cffi_req.get(url, impersonate="chrome", timeout=timeout)
    else:
        import requests as std_req
        return std_req.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        })

WB_IDS = ["199298871", "110835207", "1097975275"]
host_cache = {}  # vol -> host_number

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
        del host_cache[vol]
    
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        r = fetch(f"https://{host}{path}", timeout=10)
        if r.status_code == 200:
            host_cache[vol] = n
            return host, r
    return None, None

for nm in WB_IDS:
    log(f"\n--- BASKET: nm={nm} ---")
    host, r = find_basket_host(nm)
    if not host:
        log(f"  NOT FOUND in basket-01..60")
        continue
    log(f"  host={host}, HTTP {r.status_code}, {len(r.text)} bytes")
    try:
        card = r.json()
        log(f"  imt_id={card.get('imt_id')}")
        log(f"  nm_id={card.get('nm_id')}")
        log(f"  imt_name={str(card.get('imt_name','?'))[:80]}")
        log(f"  vendor_code={card.get('vendor_code')}")
        log(f"  subj_name={card.get('subj_name','?')}")
        
        # Description
        desc = card.get("description", "")
        log(f"  description: {len(desc)} chars, first 200: {desc[:200]}")
        
        # Options (attributes)
        opts = card.get("options", [])
        log(f"  options: {len(opts)} attrs")
        for o in opts[:12]:
            log(f"    {o.get('name','?')}: {str(o.get('value',''))[:50]}")
        
        # Media
        media = card.get("media", [])
        log(f"  media: {len(media)} items")
        for m in media[:8]:
            mtype = m.get("type", "?")
            key = m.get("key", "?")
            log(f"    type={mtype} key={key[:60]}")
        
        # Compositions, certificate
        comps = card.get("compositions", [])
        log(f"  compositions: {len(comps)}")
        cert = card.get("certificate", "")
        if cert:
            log(f"  certificate: {str(cert)[:100]}")
        
        # Colors
        colors = card.get("colors", [])
        log(f"  colors: {len(colors)}")
        
        # Full keys
        log(f"  ALL KEYS: {list(card.keys())}")
        
        # Save full JSON for reference
        save_path = Path(__file__).parent / f"diag_v58_wb_card_{nm}.json"
        save_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  saved: {save_path.name}")
        
    except Exception as e:
        log(f"  PARSE ERROR: {e}")
        log(f"  first 500: {r.text[:500]}")
    time.sleep(1)

# === WB: SEARCH API (для цен и nmId discovery) ===
log("\n" + "=" * 60)
log("WB SEARCH API (search.wb.ru)")
log("=" * 60)

SEARCH_TESTS = [
    "https://search.wb.ru/exactmatch/ru/common/v4/search?query=iphone&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&page=1",
    "https://search.wb.ru/exactmatch/ru/common/v4/search?query=овсяный+коктейль&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&page=1",
]

for i, url in enumerate(SEARCH_TESTS):
    log(f"\n--- SEARCH {i+1} ---")
    log(f"  URL: {url[:100]}...")
    try:
        r = fetch(url, timeout=20)
        log(f"  HTTP {r.status_code}, len={len(r.text)}")
        if r.status_code == 200:
            data = r.json()
            # Check shape
            if "products" in data:
                prods = data.get("products", [])
                total = data.get("total", 0)
                log(f"  products: {len(prods)}, total: {total}")
                for p in prods[:5]:
                    pid = p.get("id")
                    pname = p.get("name", "?")[:50]
                    pbrand = p.get("brand", "?")
                    sizes = p.get("sizes", [])
                    price_str = "no price"
                    qty = 0
                    if sizes:
                        s = sizes[0]
                        pr = s.get("price", {})
                        qty = s.get("qty", 0)
                        if pr.get("product") is not None:
                            price_str = f"product={pr['product']/100:.0f}RUB basic={pr.get('basic',0)/100:.0f}RUB qty={qty}"
                    log(f"  [{pid}] {pname} | {pbrand} | {price_str}")
            elif "state" in data:
                log(f"  UNEXPECTED SHAPE: keys={list(data.keys())}")
                log(f"  first 300: {r.text[:300]}")
            else:
                log(f"  UNEXPECTED: keys={list(data.keys())}")
        else:
            log(f"  BODY: {r.text[:300]}")
    except Exception as e:
        log(f"  ERROR: {e}")
    time.sleep(2)

# === WB: CARD API v4 retry (maybe need more headers) ===
log("\n" + "=" * 60)
log("WB CARD API v4 (retry with full headers)")
log("=" * 60)

card_url = "https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=199298871"
log(f"  URL: {card_url}")
try:
    if HAS_CFFI:
        r = cffi_req.get(card_url, impersonate="chrome", timeout=20,
                         headers={
                             "Accept": "application/json, text/plain, */*",
                             "Accept-Language": "ru-RU,ru;q=0.9",
                             "Referer": "https://www.wildberries.ru/",
                         })
    else:
        r = fetch(card_url, timeout=20)
    log(f"  HTTP {r.status_code}, len={len(r.text)}")
    if r.status_code == 200:
        data = r.json()
        prods = data.get("products", [])
        log(f"  products: {len(prods)}")
        for p in prods:
            log(f"  [{p.get('id')}] {p.get('name','?')[:50]}")
            sizes = p.get("sizes", [])
            if sizes:
                pr = sizes[0].get("price", {})
                log(f"    price: {pr}")
    else:
        log(f"  headers: {dict(r.headers)}")
        log(f"  BODY: {r.text[:300]}")
except Exception as e:
    log(f"  ERROR: {e}")

# === AVITO: Playwright mobile ===
log("\n" + "=" * 60)
log("AVITO PLAYWRIGHT MOBILE (m.avito.ru)")
log("=" * 60)

try:
    from playwright.sync_api import sync_playwright
    
    AVITO_MOBILE_TESTS = [
        "https://m.avito.ru/moskva/telefony?category_id=2509",
        "https://m.avito.ru/moskva/telefony/iphone_14_128_gb_8341658309",
        "https://m.avito.ru/novosemeykino/zemelnye_uchastki/uchastok_494_ga_promnaznacheniya_7764972140",
    ]
    
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            viewport={"width": 390, "height": 844},
            locale="ru-RU",
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9",
            }
        )
        page = ctx.new_page()
        
        for url in AVITO_MOBILE_TESTS:
            log(f"\n--- AVITO MOBILE: {url} ---")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                status = resp.status if resp else "?"
                content = page.content()
                title = page.title()
                log(f"  HTTP {status}, len={len(content)}, title={title[:60]}")
                
                # Check for real content
                if "404" in title.lower() or "не найдено" in content[:3000].lower():
                    log("  VERDICT: 404")
                elif "проверка безопасности" in content[:3000] or "captcha" in content[:3000].lower():
                    log("  VERDICT: SECURITY CHECK / CAPTCHA")
                elif len(content) > 5000:
                    # Look for ad data
                    ad_ids = re.findall(r'(\d{8,})', content[:5000])
                    log(f"  VERDICT: PAGE, potential IDs: {ad_ids[:5]}")
                    # Check for JSON state
                    if "__PRELOADED_STATE__" in content:
                        log("  HAS __PRELOADED_STATE__")
                    if "application/ld+json" in content:
                        log("  HAS JSON-LD")
                    # Save
                    save_path = Path(__file__).parent / f"diag_v58_avito_mob_{url.split('/')[-1][:30]}.html"
                    save_path.write_text(content, encoding="utf-8")
                    log(f"  saved: {save_path.name} ({len(content)} bytes)")
                else:
                    log(f"  VERDICT: SHORT ({len(content)} bytes)")
                    log(f"  first 300: {content[:300]}")
            except Exception as e:
                log(f"  ERROR: {e}")
                traceback.print_exc()
            time.sleep(2)
        
        browser.close()
    log("  Playwright done")
except Exception as e:
    log(f"  PLAYWRIGHT ERROR: {e}")
    traceback.print_exc()

# === Summary ===
log("\n" + "=" * 60)
log("SUMMARY v58")
log("=" * 60)
log(f"  curl_cffi: {'YES' if HAS_CFFI else 'NO'}")
log(f"  WB basket: hosts found={list(host_cache.values())}")
log(f"  WB search: tested 2 queries")
log(f"  WB card v4: tested with extra headers")
log(f"  Avito mobile: tested 3 URLs via Playwright")
log("DONE")

save()
print(f"\nSaved to {OUT}")
