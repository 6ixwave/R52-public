"""Мультитест v4: Farpost + WB + Ozon + Avito.
Стратегии:
  FARPOST: (1) cp1251 search в saved HTML, (2) live fetch + cp1251 decode, (3) API farpost
  WB: (1) card.json selling/sizes, (2) basket price endpoint, (3) m.wb.ru, (4) web-api v2
  OZON: (1) composer v2 + chrome impersonate, (2) mobile UA, (3) api.ozon.ru/search, (4) pw
  AVITO: (1) mobile API, (2) desktop + long wait, (3) category page
"""
import json, re, time, sys, os, logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag4")

try:
    from curl_cffi import requests as cffi
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    import requests as cffi

import requests

OUT = {}
def rec(name, ok, detail, s):
    OUT[name] = {"ok": ok, "detail": detail, "s": round(s, 1)}
    log.info(f"{'OK ' if ok else 'FAIL'} {name}: {detail[:120]}")

# ============================================================
# FARPOST
# ============================================================
log.info("=== FARPOST ===")
FP_ID = "138952176"
FP_URL = f"https://www.farpost.ru/vladivostok/{FP_ID}.html"

# --- FP-1: saved HTML cp1251 analysis ---
t0 = time.monotonic()
saved = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\dfv3_fp_full.html"
fp1_prices = []
fp1_title = None
fp1_bulletin = None
try:
    with open(saved, "rb") as f:
        raw = f.read()
    # Try cp1251
    html_cp = raw.decode("windows-1251", errors="replace")
    html_utf = raw.decode("utf-8", errors="replace")
    # Which encoding has more Cyrillic?
    cyr_cp = len(re.findall(r'[\u0400-\u04FF]', html_cp))
    cyr_utf = len(re.findall(r'[\u0400-\u04FF]', html_utf))
    html = html_cp if cyr_cp > cyr_utf else html_utf
    log.info("FP-1: encoding=%s cyr_cp=%d cyr_utf=%d len=%d", "cp1251" if cyr_cp>cyr_utf else "utf8", cyr_cp, cyr_utf, len(html))
    
    # Search for price patterns
    for pat_name, pat in [
        ("data-bulletin-price", r'data-bulletin-price=["\'](\d+)["\']'),
        ("viewbull-price", r'viewbull-summary-price__value[^>]*>([\d\s\u00a0]+)'),
        ("price-rub", r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)'),
        ("jsonld-price", r'"price"\s*:\s*"?([\d.]+)"?'),
        ("jsonld-offer", r'"offers"\s*:\s*\{[^}]*"price"\s*:\s*"?([\d.]+)"?'),
        ("span-price", r'<span[^>]*class="[^"]*price[^"]*"[^>]*>([\d\s\u00a0]+)'),
        ("data-price", r'data-price=["\'](\d+)["\']'),
    ]:
        ms = re.findall(pat, html, re.I)
        if ms:
            fp1_prices.append(f"{pat_name}={ms[:3]}")
            log.info("FP-1: %s -> %s", pat_name, ms[:3])
    
    # Bulletin ID
    m = re.search(r'bulletinId=["\'](\d+)["\']', html)
    if m: fp1_bulletin = m.group(1)
    m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
    if m: fp1_title = re.sub(r'<[^>]+>','',m.group(1)).strip()[:80]
    
    # Check if it's a verify/soft-block page
    is_verify = "verify" in html.lower()[:2000] and "bulletin" not in html.lower()[:5000]
    has_content = "bulletinText" in html or "viewbull" in html
    
    fp1_ok = bool(fp1_prices)
    rec("FP:cp1251_saved", fp1_ok,
        f"enc={'cp1251' if cyr_cp>cyr_utf else 'utf8'} prices={fp1_prices[:2]} title={fp1_title} bid={fp1_bulletin} verify={is_verify} content={has_content}",
        time.monotonic()-t0)
except Exception as e:
    rec("FP:cp1251_saved", False, f"err={e}", time.monotonic()-t0)

# --- FP-2: Live fetch with proper session ---
t0 = time.monotonic()
try:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    # Warm-up
    s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(1)
    r = s.get(FP_URL, timeout=15, allow_redirects=True)
    ct = r.headers.get("Content-Type","")
    if "1251" in ct:
        html2 = r.content.decode("windows-1251", errors="replace")
    else:
        html2 = r.content.decode("utf-8", errors="replace")
    prices2 = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html2)
    prices2 += re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html2)
    bid2 = re.search(r'bulletinId=["\'](\d+)["\']', html2)
    fp2_ok = r.status_code == 200 and (prices2 or bid2)
    rec("FP:live_cp1251", fp2_ok,
        f"HTTP={r.status_code} len={len(html2)} prices={prices2[:3]} bid={bid2.group(1) if bid2 else '?'} url={r.url[:60]}",
        time.monotonic()-t0)
except Exception as e:
    rec("FP:live_cp1251", False, f"err={e}", time.monotonic()-t0)

# --- FP-3: Farpost JSON API (baza.farpost.ru) ---
t0 = time.monotonic()
try:
    api_urls = [
        f"https://baza.farpost.ru/api/bulletin/{FP_ID}",
        f"https://www.farpost.ru/api/bulletin/{FP_ID}",
        f"https://baza.farpost.ru/bulletin/{FP_ID}/json",
    ]
    fp3_ok = False
    fp3_detail = ""
    for u in api_urls:
        try:
            if HAS_CFFI:
                r = cffi.get(u, impersonate="chrome", timeout=10, headers={"Accept":"application/json"})
            else:
                r = requests.get(u, timeout=10, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                d = r.json()
                fp3_ok = True
                fp3_detail = f"{u.split('/')[-1]} keys={list(d.keys())[:8]}"
                break
            else:
                fp3_detail = f"{u.split('?')[0].split('/')[-2]}={r.status_code}"
        except Exception:
            fp3_detail += f" {u.split('?')[0].split('/')[-2]}=err; "
    rec("FP:json_api", fp3_ok, fp3_detail, time.monotonic()-t0)
except Exception as e:
    rec("FP:json_api", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# WILDBERRIES
# ============================================================
log.info("=== WILDBERRIES ===")
WB_ID = 1097975275
WB_VOL = WB_ID // 100000  # 10979
WB_PART = WB_ID // 1000   # 109797

# --- WB-1: card.json → check selling/sizes for price ---
t0 = time.monotonic()
try:
    # Use cached host from dfv3: basket-42
    card_url = f"https://basket-42.wbbasket.ru/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/card.json"
    if HAS_CFFI:
        r = cffi.get(card_url, impersonate="chrome", timeout=15, headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
    else:
        r = requests.get(card_url, timeout=15, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
    if r.status_code == 200:
        d = r.json()
        selling = d.get("selling", {})
        sizes = d.get("sizes", [])
        price_keys = [k for k in d.keys() if "price" in k.lower()]
        selling_keys = list(selling.keys()) if isinstance(selling, dict) else []
        size_prices = []
        for sz in sizes[:3]:
            if isinstance(sz, dict) and "price" in sz:
                size_prices.append(sz["price"])
        wb1_prices = []
        if isinstance(selling, dict):
            for pk in ("price","priceU","priceD","oldPrice","product","basic"):
                if selling.get(pk):
                    wb1_prices.append(f"selling.{pk}={selling[pk]}")
        for sp in size_prices[:2]:
            if isinstance(sp, dict):
                for pk in ("basic","product","card","old","crossed"):
                    if sp.get(pk):
                        wb1_prices.append(f"size.{pk}={sp[pk]}")
        wb1_ok = len(wb1_prices) > 0
        rec("WB:card_price", wb1_ok,
            f"keys={len(d)} selling={selling_keys[:6]} price_keys={price_keys} size_prices={wb1_prices[:4]} nm={d.get('nm_id')}",
            time.monotonic()-t0)
    else:
        rec("WB:card_price", False, f"HTTP={r.status_code}", time.monotonic()-t0)
except Exception as e:
    rec("WB:card_price", False, f"err={e}", time.monotonic()-t0)

# --- WB-2: basket price endpoint (alternative to card.wb.ru) ---
t0 = time.monotonic()
try:
    # Try multiple basket hosts for price
    price_urls = [
        f"https://basket-42.wbbasket.ru/vol{WB_VOL}/part{WB_PART}/{WB_ID}/price/ru.json",
        f"https://basket-42.wbbasket.ru/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/price.json",
        f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_ID}",
        f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_ID}",
        f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_ID}",
        f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={WB_ID}",
        f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={WB_ID}",
    ]
    wb2_ok = False
    wb2_detail = ""
    for u in price_urls:
        try:
            if HAS_CFFI:
                r = cffi.get(u, impersonate="chrome", timeout=10,
                           headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/","Origin":"https://www.wildberries.ru"})
            else:
                r = requests.get(u, timeout=10, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                try:
                    d = r.json()
                    prods = d.get("products") or d.get("data",{}).get("items") or [d]
                    if prods and isinstance(prods[0], dict):
                        p = prods[0]
                        sz = p.get("sizes",[])
                        pr = sz[0].get("price",{}) if sz else p.get("price",{})
                        wb2_ok = True
                        wb2_detail = f"{u.split('?')[0].split('/')[-1]} basic={pr.get('basic')} product={pr.get('product')}"
                        break
                except:
                    pass
            wb2_detail += f" {u.split('?')[0].split('/')[-1]}={r.status_code}"
        except Exception:
            wb2_detail += f" err"
        time.sleep(0.5)
    rec("WB:price_multi", wb2_ok, wb2_detail[:150], time.monotonic()-t0)
except Exception as e:
    rec("WB:price_multi", False, f"err={e}", time.monotonic()-t0)

# --- WB-3: m.wildberries.ru (mobile) ---
t0 = time.monotonic()
try:
    mob_url = f"https://m.wildberries.ru/catalog/{WB_ID}/detail.aspx"
    if HAS_CFFI:
        r = cffi.get(mob_url, impersonate="chrome", timeout=15,
                   headers={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15","Accept":"text/html"})
    else:
        r = requests.get(mob_url, timeout=15, headers={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
    prices_m = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', r.text) if r.status_code==200 else []
    wb3_ok = r.status_code == 200 and len(prices_m) > 0
    rec("WB:mobile", wb3_ok, f"HTTP={r.status_code} len={len(r.text)} prices={prices_m[:3]}", time.monotonic()-t0)
except Exception as e:
    rec("WB:mobile", False, f"err={e}", time.monotonic()-t0)

# --- WB-4: search.wb.ru (alternative) ---
t0 = time.monotonic()
try:
    sw_url = f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_ID}"
    if HAS_CFFI:
        r = cffi.get(sw_url, impersonate="chrome", timeout=12,
                   headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
    else:
        r = requests.get(sw_url, timeout=12, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
    if r.status_code == 200:
        d = r.json()
        prods = d.get("products",[])
        if prods:
            p = prods[0]
            sz = p.get("sizes",[])
            pr = sz[0].get("price",{}) if sz else {}
            wb4_ok = True
            wb4_detail = f"basic={pr.get('basic')} product={pr.get('product')} name={str(p.get('name','?'))[:40]}"
        else:
            wb4_ok = False
            wb4_detail = f"200 but no products, keys={list(d.keys())[:5]}"
    else:
        wb4_ok = False
        wb4_detail = f"HTTP={r.status_code}"
    rec("WB:search_api", wb4_ok, wb4_detail, time.monotonic()-t0)
except Exception as e:
    rec("WB:search_api", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# OZON
# ============================================================
log.info("=== OZON ===")
OZ_ID = "259671175"
OZ_URL = f"https://www.ozon.ru/product/{OZ_ID}/"

# --- OZ-1: Composer API with full chrome headers ---
t0 = time.monotonic()
try:
    comp_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZ_ID}/"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    if HAS_CFFI:
        r = cffi.get(comp_url, impersonate="chrome", timeout=15, headers=headers)
    else:
        r = requests.get(comp_url, timeout=15, headers={**headers, "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"})
    oz1_ok = False
    if r.status_code == 200:
        d = r.json()
        # Navigate blocks for price
        blocks = d.get("page",{}).get("blocks",[])
        price_block = None
        for b in blocks:
            if b.get("type") in ("webPrice","price","pdp-price","priceV2"):
                price_block = b
                break
        title = d.get("page",{}).get("title") or d.get("page",{}).get("seoTitle")
        oz1_ok = bool(title) or bool(price_block)
        oz1_detail = f"blocks={len(blocks)} title={str(title)[:40]} price_block={'yes' if price_block else 'no'}"
        if price_block:
            props = price_block.get("props",{}) or price_block.get("data",{})
            oz1_detail += f" price_keys={list(props.keys())[:6]}"
    else:
        oz1_detail = f"HTTP={r.status_code} body[:100]={r.text[:100]}"
    rec("OZ:composer_v2", oz1_ok, oz1_detail, time.monotonic()-t0)
except Exception as e:
    rec("OZ:composer_v2", False, f"err={e}", time.monotonic()-t0)

# --- OZ-2: Mobile UA + different endpoint ---
t0 = time.monotonic()
try:
    mob_headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    # Try mobile product page
    r = cffi.get(OZ_URL, impersonate="chrome", timeout=15, headers=mob_headers) if HAS_CFFI else requests.get(OZ_URL, timeout=15, headers=mob_headers)
    prices_oz = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', r.text) if r.status_code==200 else []
    og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', r.text) if r.status_code==200 else None
    oz2_ok = r.status_code == 200 and (prices_oz or og_price)
    rec("OZ:mobile_ua", oz2_ok,
        f"HTTP={r.status_code} len={len(r.text)} prices={prices_oz[:3]} og={og_price.group(1) if og_price else '?'}",
        time.monotonic()-t0)
except Exception as e:
    rec("OZ:mobile_ua", False, f"err={e}", time.monotonic()-t0)

# --- OZ-3: api.ozon.ru/api/entrypoint/pdp ---
t0 = time.monotonic()
try:
    pdp_url = f"https://api.ozon.ru/api/entrypoint/pdp/{OZ_ID}"
    h3 = {"Accept":"application/json","Referer":"https://www.ozon.ru/","Origin":"https://www.ozon.ru"}
    r = cffi.get(pdp_url, impersonate="chrome", timeout=12, headers=h3) if HAS_CFFI else requests.get(pdp_url, timeout=12, headers={**h3,"User-Agent":"Mozilla/5.0"})
    oz3_ok = False
    if r.status_code == 200:
        d = r.json()
        oz3_ok = True
        oz3_detail = f"keys={list(d.keys())[:8]}"
        # Look for price
        for path in [("data","price"),("product","price"),("price","basic"),("data","product","price")]:
            obj = d
            for k in path:
                if isinstance(obj, dict):
                    obj = obj.get(k)
                else:
                    obj = None
                    break
            if obj:
                oz3_detail += f" {path}={str(obj)[:60]}"
                break
    else:
        oz3_detail = f"HTTP={r.status_code}"
    rec("OZ:pdp_api", oz3_ok, oz3_detail, time.monotonic()-t0)
except Exception as e:
    rec("OZ:pdp_api", False, f"err={e}", time.monotonic()-t0)

# --- OZ-4: Search API (avoids PDP block) ---
t0 = time.monotonic()
try:
    # Use search to find the product and get price from search results
    search_url = f"https://api.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/?text={OZ_ID}&from_global=true"
    h4 = {"Accept":"application/json","Referer":"https://www.ozon.ru/"}
    r = cffi.get(search_url, impersonate="chrome", timeout=15, headers=h4) if HAS_CFFI else requests.get(search_url, timeout=15, headers={**h4,"User-Agent":"Mozilla/5.0"})
    oz4_ok = False
    if r.status_code == 200:
        d = r.json()
        blocks = d.get("page",{}).get("blocks",[])
        # Find product tiles with prices
        found = []
        for b in blocks:
            if b.get("type") in ("searchResultsV2","searchResults","tilesList"):
                tiles = b.get("props",{}).get("tiles") or b.get("data",{}).get("tiles") or []
                for t in tiles[:5]:
                    if isinstance(t, dict):
                        price = t.get("price") or t.get("priceV2")
                        if price:
                            found.append(str(price)[:50])
        oz4_ok = len(found) > 0
        oz4_detail = f"blocks={len(blocks)} found_prices={found[:2]}"
    else:
        oz4_detail = f"HTTP={r.status_code}"
    rec("OZ:search_api", oz4_ok, oz4_detail, time.monotonic()-t0)
except Exception as e:
    rec("OZ:search_api", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# AVITO
# ============================================================
log.info("=== AVITO ===")
AV_ID = "1154321987"
AV_URL = f"https://www.avito.ru/{AV_ID}"

# --- AV-1: Mobile page ---
t0 = time.monotonic()
try:
    mob_h = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    r = cffi.get(AV_URL, impersonate="chrome", timeout=15, headers=mob_h) if HAS_CFFI else requests.get(AV_URL, timeout=15, headers=mob_h)
    prices_av = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', r.text) if r.status_code==200 else []
    ld = re.search(r'application/ld\+json[^>]*>(.*?)</script>', r.text, re.DOTALL) if r.status_code==200 else None
    av1_ok = r.status_code == 200 and (prices_av or ld)
    rec("AV:mobile", av1_ok, f"HTTP={r.status_code} len={len(r.text)} prices={prices_av[:3]} ld={bool(ld)}", time.monotonic()-t0)
except Exception as e:
    rec("AV:mobile", False, f"err={e}", time.monotonic()-t0)

# --- AV-2: Desktop with longer wait + session ---
t0 = time.monotonic()
try:
    s2 = requests.Session()
    s2.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    # Warm-up: homepage first
    s2.get("https://www.avito.ru/", timeout=10)
    time.sleep(3)
    r = s2.get(AV_URL, timeout=15)
    prices_av2 = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', r.text) if r.status_code==200 else []
    av2_ok = r.status_code == 200 and len(prices_av2) > 0
    rec("AV:session_desktop", av2_ok, f"HTTP={r.status_code} len={len(r.text)} prices={prices_av2[:3]}", time.monotonic()-t0)
except Exception as e:
    rec("AV:session_desktop", False, f"err={e}", time.monotonic()-t0)

# --- AV-3: API endpoint ---
t0 = time.monotonic()
try:
    api_urls = [
        f"https://www.avito.ru/api/v1/offers/{AV_ID}",
        f"https://api.avito.ru/v1/offers/{AV_ID}",
        f"https://m.avito.ru/api/offers/{AV_ID}",
    ]
    av3_ok = False
    av3_detail = ""
    for u in api_urls:
        try:
            r = cffi.get(u, impersonate="chrome", timeout=10, headers={"Accept":"application/json"}) if HAS_CFFI else requests.get(u, timeout=10, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                d = r.json()
                av3_ok = True
                av3_detail = f"{u.split('/')[-2]}={list(d.keys())[:6]}"
                break
            av3_detail += f" {u.split('?')[0].split('/')[-2]}={r.status_code}"
        except Exception:
            av3_detail += " err"
    rec("AV:api", av3_ok, av3_detail, time.monotonic()-t0)
except Exception as e:
    rec("AV:api", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# SAVE
# ============================================================
result = {
    "time": datetime.now(timezone(timedelta(hours=10))).isoformat(),
    "total_s": round(sum(v["s"] for v in OUT.values()), 1),
    "results": OUT,
}
out_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag4_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
log.info("Saved to %s", out_path)
print(json.dumps(result, ensure_ascii=False, indent=2))
