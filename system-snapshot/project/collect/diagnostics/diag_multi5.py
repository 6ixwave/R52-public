"""Мультитест v5: глубокий анализ.
FP: разбирать saved 199KB HTML по структуре (числа, классы, data-field)
WB: полный card.json + новые basket price paths
OZON: requests (не cffi) для SSL fix + composer
AVITO: m.avito.ru + city path
"""
import json, re, time, sys, os, logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag5")

try:
    from curl_cffi import requests as cffi
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

import requests

OUT = {}
def rec(name, ok, detail, s):
    OUT[name] = {"ok": ok, "detail": detail, "s": round(s, 1)}
    log.info(f"{'OK ' if ok else 'FAIL'} {name}: {detail[:150]}")

BASE = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12"

# ============================================================
# FARPOST: deep analysis of saved 199KB HTML
# ============================================================
log.info("=== FARPOST deep ===")
FP_ID = "138952176"
saved = os.path.join(BASE, "dfv3_fp_full.html")

t0 = time.monotonic()
try:
    with open(saved, "rb") as f:
        raw = f.read()
    html = raw.decode("windows-1251", errors="replace")
    log.info("FP: raw=%d bytes, decoded=%d chars", len(raw), len(html))
    
    # 1. Find ALL numbers >= 100 (potential prices)
    nums = re.findall(r'(\d{3,10})', html)
    big_nums = [n for n in nums if int(n) >= 100][:30]
    
    # 2. Find all class names containing "price", "sum", "cost", "rub"
    price_classes = re.findall(r'class="([^"]*(?:price|sum|cost|rub|value)[^"]*)"', html, re.I)
    
    # 3. Find all data-field / data-* attributes
    data_fields = re.findall(r'data-(?:field|bulletin|price|value)[^=]*=["\']([^"\']+)["\']', html, re.I)
    
    # 4. Find "руб" or "₽" context
    rub_ctx = re.findall(r'.{0,40}(?:руб|₽|RUB).{0,40}', html, re.I)[:10]
    
    # 5. Find viewbull / bulletin / subject markers
    markers = {
        "viewbull": "viewbull" in html,
        "bulletinText": "bulletinText" in html,
        "subject": "data-field=\"subject\"" in html,
        "bulletinId": "bulletinId" in html,
        "lightbox": "lightbox" in html,
        "jsonld": "ld+json" in html,
        "og:": 'og:' in html,
        "company": "company" in html.lower(),
        "field_container": "viewbull-field" in html,
    }
    
    # 6. Extract <title>
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL|re.I)
    title_tag = m.group(1).strip()[:100] if m else "?"
    
    # 7. H1
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL|re.I)
    h1 = re.sub(r'<[^>]+>', ' ', m.group(1)).strip()[:100] if m else "?"
    
    # 8. Look for specific price div patterns
    price_divs = re.findall(r'<(?:div|span)[^>]*class="[^"]*(?:price|sum)[^"]*"[^>]*>(.*?)</(?:div|span)>', html, re.DOTALL|re.I)[:10]
    price_divs_clean = [re.sub(r'<[^>]+>', '', p).strip()[:50] for p in price_divs]
    
    # 9. JSON-LD blocks
    ld_blocks = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
    ld_info = []
    for lb in ld_blocks[:3]:
        try:
            d = json.loads(lb.strip())
            ld_info.append(list(d.keys()) if isinstance(d, dict) else "list")
        except:
            ld_info.append(lb.strip()[:60])
    
    # 10. First 2000 chars (head area)
    head_sample = html[:2000]
    
    fp_ok = bool(big_nums) or bool(price_classes) or bool(rub_ctx)
    rec("FP:deep_saved", fp_ok,
        f"title={title_tag} h1={h1[:40]} big_nums={big_nums[:5]} classes={price_classes[:5]} "
        f"data_f={data_fields[:5]} rub={rub_ctx[:2]} markers={markers} ld={ld_info[:2]} divs={price_divs_clean[:3]}",
        time.monotonic()-t0)
except Exception as e:
    rec("FP:deep_saved", False, f"err={e}", time.monotonic()-t0)

# --- FP-2: Try different bulletin (newer, less likely blocked) ---
t0 = time.monotonic()
try:
    # Try a search page to find a valid bulletin
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    # Warm-up with longer wait
    s.get("https://www.farpost.ru/", timeout=10)
    time.sleep(3)
    # Try the bulletin with full path (city/category)
    # First try /vladivostok/138952176.html with cookies from warmup
    r = s.get(f"https://www.farpost.ru/vladivostok/{FP_ID}.html", timeout=15, allow_redirects=True)
    ct = r.headers.get("Content-Type", "")
    html2 = r.content.decode("windows-1251", errors="replace") if "1251" in ct else r.text
    is_verify = "/verify" in r.url
    # Search for ANY numbers > 1000
    nums2 = [n for n in re.findall(r'(\d{4,10})', html2) if int(n) > 1000][:10]
    # data-field attributes
    df2 = re.findall(r'data-field=["\']([^"\']+)["\']', html2)[:15]
    
    fp2_ok = r.status_code == 200 and not is_verify and len(nums2) > 3
    rec("FP:live_v2", fp2_ok,
        f"HTTP={r.status_code} verify={is_verify} len={len(html2)} url={r.url[:60]} nums={nums2[:5]} data_fields={df2[:8]}",
        time.monotonic()-t0)
except Exception as e:
    rec("FP:live_v2", False, f"err={e}", time.monotonic()-t0)

# --- FP-3: Try farpost with curl_cffi (different TLS fingerprint) ---
t0 = time.monotonic()
try:
    if HAS_CFFI:
        r = cffi.get(f"https://www.farpost.ru/vladivostok/{FP_ID}.html",
                    impersonate="chrome", timeout=15,
                    headers={"Accept-Language": "ru-RU,ru;q=0.9"},
                    allow_redirects=True)
        html3 = r.content.decode("windows-1251", errors="replace") if "1251" in r.headers.get("Content-Type","") else r.text
        is_verify3 = "/verify" in str(r.url)
        nums3 = [n for n in re.findall(r'(\d{4,10})', html3) if int(n) > 1000][:10]
        fp3_ok = r.status_code == 200 and not is_verify3 and len(nums3) > 3
        rec("FP:cffi", fp3_ok,
            f"HTTP={r.status_code} verify={is_verify3} len={len(html3)} nums={nums3[:5]}",
            time.monotonic()-t0)
    else:
        rec("FP:cffi", False, "no cffi", 0)
except Exception as e:
    rec("FP:cffi", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# WILDBERRIES: deep card.json + new price strategies
# ============================================================
log.info("=== WB deep ===")
WB_ID = 1097975275
WB_VOL = WB_ID // 100000
WB_PART = WB_ID // 1000

# --- WB-1: Full card.json analysis ---
t0 = time.monotonic()
try:
    card_url = f"https://basket-42.wbbasket.ru/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/card.json"
    r = cffi.get(card_url, impersonate="chrome", timeout=15,
               headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"}) if HAS_CFFI \
        else requests.get(card_url, timeout=15, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
    if r.status_code == 200:
        d = r.json()
        # Dump ALL keys at top level
        top_keys = list(d.keys())
        # Check selling deeply
        selling = d.get("selling", {})
        selling_full = json.dumps(selling, ensure_ascii=False)[:300] if selling else "none"
        # Check sizes deeply
        sizes = d.get("sizes", [])
        size0 = json.dumps(sizes[0], ensure_ascii=False)[:200] if sizes else "none"
        # Check for any "price" string anywhere in JSON
        json_str = json.dumps(d, ensure_ascii=False)
        price_occurrences = [m.start() for m in re.finditer(r'price', json_str, re.I)][:10]
        price_ctx = [json_str[max(0,m-20):m+40] for m in price_occurrences[:5]]
        # Check "supp_price", "priceU", "priceD"
        for pk in ("supp_price","priceU","priceD","price","oldPrice","product_price"):
            if pk in json_str:
                price_ctx.append(f"FOUND:{pk}")
        
        wb1_ok = bool(price_occurrences)
        rec("WB:card_deep", wb1_ok,
            f"keys={top_keys} selling={selling_full[:100]} size0={size0[:80]} price_occ={len(price_occurrences)} ctx={price_ctx[:3]}",
            time.monotonic()-t0)
    else:
        rec("WB:card_deep", False, f"HTTP={r.status_code}", time.monotonic()-t0)
except Exception as e:
    rec("WB:card_deep", False, f"err={e}", time.monotonic()-t0)

# --- WB-2: New basket price paths ---
t0 = time.monotonic()
try:
    # Try different path patterns on basket-42
    paths = [
        f"/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/suppliers.json",
        f"/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/price.json",
        f"/vol{WB_VOL}/part{WB_PART}/{WB_ID}/price/ru.json",
        f"/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/options.json",
        f"/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/feedbacks.json",
        f"/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/sizes.json",
    ]
    wb2_results = []
    for p in paths:
        try:
            u = f"https://basket-42.wbbasket.ru{p}"
            r = cffi.get(u, impersonate="chrome", timeout=8,
                       headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"}) if HAS_CFFI \
                else requests.get(u, timeout=8, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                try:
                    d2 = r.json()
                    wb2_results.append(f"{p.split('/')[-1]}=200({list(d2.keys())[:4] if isinstance(d2,dict) else 'list'})")
                except:
                    wb2_results.append(f"{p.split('/')[-1]}=200(text)")
            else:
                wb2_results.append(f"{p.split('/')[-1]}={r.status_code}")
        except Exception:
            wb2_results.append(f"{p.split('/')[-1]}=err")
        time.sleep(0.3)
    rec("WB:basket_paths", any("=200" in x for x in wb2_results), " ".join(wb2_results), time.monotonic()-t0)
except Exception as e:
    rec("WB:basket_paths", False, f"err={e}", time.monotonic()-t0)

# --- WB-3: card.wb.ru with different dest values ---
t0 = time.monotonic()
try:
    # dest=-1257786 is Vladivostok. Try dest=0 (all), dest=-1 (default)
    dests = [0, -1, -1257786, -1257787]
    wb3_results = []
    for dest in dests:
        u = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest={dest}&spp=30&nm={WB_ID}"
        try:
            r = cffi.get(u, impersonate="chrome", timeout=10,
                       headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/","Origin":"https://www.wildberries.ru"}) if HAS_CFFI \
                else requests.get(u, timeout=10, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0","Referer":"https://www.wildberries.ru/"})
            if r.status_code == 200:
                d3 = r.json()
                prods = d3.get("products", [])
                if prods:
                    sz = prods[0].get("sizes", [])
                    pr = sz[0].get("price", {}) if sz else {}
                    wb3_results.append(f"dest={dest}:basic={pr.get('basic')}")
                else:
                    wb3_results.append(f"dest={dest}:empty")
            else:
                wb3_results.append(f"dest={dest}:{r.status_code}")
        except Exception as e:
            wb3_results.append(f"dest={dest}:err")
        time.sleep(0.5)
    wb3_ok = any("basic=" in x for x in wb3_results)
    rec("WB:card_dest", wb3_ok, " ".join(wb3_results), time.monotonic()-t0)
except Exception as e:
    rec("WB:card_dest", False, f"err={e}", time.monotonic()-t0)

# --- WB-4: basket-42 with /info/ru/ directory (list all files) ---
t0 = time.monotonic()
try:
    # Try common WB basket file names
    files = ["card.json","photos.json","feedbacks.json","options.json","sizes.json",
             "suppliers.json","price.json","info.json","meta.json","seo.json"]
    wb4_results = []
    for fn in files:
        u = f"https://basket-42.wbbasket.ru/vol{WB_VOL}/part{WB_PART}/{WB_ID}/info/ru/{fn}"
        try:
            r = cffi.get(u, impersonate="chrome", timeout=6,
                       headers={"Accept":"*/*","Referer":"https://www.wildberries.ru/"}) if HAS_CFFI \
                else requests.get(u, timeout=6, headers={"Accept":"*/*","User-Agent":"Mozilla/5.0"})
            wb4_results.append(f"{fn}={r.status_code}")
        except:
            wb4_results.append(f"{fn}=err")
        time.sleep(0.2)
    rec("WB:file_scan", any("=200" in x for x in wb4_results), " ".join(wb4_results), time.monotonic()-t0)
except Exception as e:
    rec("WB:file_scan", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# OZON: fix SSL with requests, try composer
# ============================================================
log.info("=== OZON (requests, no SSL issue) ===")
OZ_ID = "259671175"
OZ_URL = f"https://www.ozon.ru/product/{OZ_ID}/"

# --- OZ-1: Composer API via requests (fixes SSL) ---
t0 = time.monotonic()
try:
    comp_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZ_ID}/"
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    r = requests.get(comp_url, headers=h, timeout=15)
    oz1_ok = False
    if r.status_code == 200:
        d = r.json()
        blocks = d.get("page", {}).get("blocks", [])
        title = d.get("page", {}).get("title") or d.get("page", {}).get("seoTitle")
        # Find price block
        price_info = ""
        for b in blocks:
            if b.get("type") in ("webPrice","price","pdp-price","priceV2","PDPPrice"):
                props = b.get("props", {}) or b.get("data", {})
                price_info = f"type={b['type']} keys={list(props.keys())[:8]}"
                break
        oz1_ok = bool(title) or bool(price_info)
        oz1_detail = f"blocks={len(blocks)} title={str(title)[:50]} {price_info}"
    else:
        oz1_detail = f"HTTP={r.status_code} body[:150]={r.text[:150]}"
    rec("OZ:composer_req", oz1_ok, oz1_detail, time.monotonic()-t0)
except Exception as e:
    rec("OZ:composer_req", False, f"err={e}", time.monotonic()-t0)

# --- OZ-2: HTML via requests (not cffi) ---
t0 = time.monotonic()
try:
    h2 = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    r = requests.get(OZ_URL, headers=h2, timeout=15)
    prices = re.findall(r'(\d[\d\s]{2,9})\s*(?:₽|руб)', r.text) if r.status_code == 200 else []
    og_p = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', r.text) if r.status_code == 200 else None
    # Check for data-widget
    widgets = re.findall(r'data-widget="([^"]+)"', r.text)[:10] if r.status_code == 200 else []
    oz2_ok = r.status_code == 200 and (prices or og_p)
    rec("OZ:html_req", oz2_ok,
        f"HTTP={r.status_code} len={len(r.text)} prices={prices[:3]} og={og_p.group(1) if og_p else '?'} widgets={widgets[:5]}",
        time.monotonic()-t0)
except Exception as e:
    rec("OZ:html_req", False, f"err={e}", time.monotonic()-t0)

# --- OZ-3: api.ozon.ru/api/entrypoint/pdp via requests ---
t0 = time.monotonic()
try:
    pdp_url = f"https://api.ozon.ru/api/entrypoint/pdp/{OZ_ID}"
    h3 = {
        "Accept": "application/json",
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    r = requests.get(pdp_url, headers=h3, timeout=12)
    oz3_ok = False
    if r.status_code == 200:
        d = r.json()
        oz3_ok = True
        oz3_detail = f"keys={list(d.keys())[:10]}"
        # Deep search for price
        js = json.dumps(d, ensure_ascii=False)
        pm = re.findall(r'"price[^"]*"\s*:\s*([\d.]+)', js)[:5]
        oz3_detail += f" prices={pm}"
    else:
        oz3_detail = f"HTTP={r.status_code} body[:100]={r.text[:100]}"
    rec("OZ:pdp_req", oz3_ok, oz3_detail, time.monotonic()-t0)
except Exception as e:
    rec("OZ:pdp_req", False, f"err={e}", time.monotonic()-t0)

# --- OZ-4: cffi with verify=False (workaround SSL) ---
t0 = time.monotonic()
try:
    if HAS_CFFI:
        comp_url2 = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZ_ID}/"
        r = cffi.get(comp_url2, impersonate="chrome", timeout=15, verify=False,
                   headers={"Accept":"application/json","Referer":"https://www.ozon.ru/","Origin":"https://www.ozon.ru"})
        oz4_ok = r.status_code == 200
        if oz4_ok:
            d = r.json()
            blocks = d.get("page",{}).get("blocks",[])
            oz4_detail = f"blocks={len(blocks)} title={d.get('page',{}).get('title','?')[:40]}"
        else:
            oz4_detail = f"HTTP={r.status_code}"
        rec("OZ:cffi_noverify", oz4_ok, oz4_detail, time.monotonic()-t0)
    else:
        rec("OZ:cffi_noverify", False, "no cffi", 0)
except Exception as e:
    rec("OZ:cffi_noverify", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# AVITO: m.avito.ru + different approaches
# ============================================================
log.info("=== AVITO ===")
AV_ID = "1154321987"

# --- AV-1: m.avito.ru ---
t0 = time.monotonic()
try:
    m_h = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    r = cffi.get(f"https://m.avito.ru/{AV_ID}", impersonate="chrome", timeout=15, headers=m_h) if HAS_CFFI \
        else requests.get(f"https://m.avito.ru/{AV_ID}", timeout=15, headers=m_h)
    prices = re.findall(r'(\d[\d\s]{2,9})\s*(?:₽|руб)', r.text) if r.status_code == 200 else []
    ld = re.search(r'application/ld\+json[^>]*>(.*?)</script>', r.text, re.DOTALL) if r.status_code == 200 else None
    av1_ok = r.status_code == 200 and (prices or ld)
    rec("AV:m_avito", av1_ok, f"HTTP={r.status_code} len={len(r.text)} prices={prices[:3]} ld={bool(ld)}", time.monotonic()-t0)
except Exception as e:
    rec("AV:m_avito", False, f"err={e}", time.monotonic()-t0)

# --- AV-2: www.avito.ru with city in path ---
t0 = time.monotonic()
try:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    # Warm-up
    s.get("https://www.avito.ru/", timeout=10)
    time.sleep(5)  # longer wait
    # Try with city
    r = s.get(f"https://www.avito.ru/vladivostok/{AV_ID}", timeout=15)
    prices2 = re.findall(r'(\d[\d\s]{2,9})\s*(?:₽|руб)', r.text) if r.status_code == 200 else []
    av2_ok = r.status_code == 200 and len(prices2) > 0
    rec("AV:city_path", av2_ok, f"HTTP={r.status_code} len={len(r.text)} prices={prices2[:3]} url={r.url[:60]}", time.monotonic()-t0)
except Exception as e:
    rec("AV:city_path", False, f"err={e}", time.monotonic()-t0)

# --- AV-3: avito.ru/api (newer API patterns) ---
t0 = time.monotonic()
try:
    api_urls = [
        f"https://www.avito.ru/api/v2/offers/{AV_ID}",
        f"https://www.avito.ru/api/offers/{AV_ID}/full",
        f"https://www.avito.ru/api/v1/offers/{AV_ID}?full=true",
        f"https://m.avito.ru/api/offers/{AV_ID}",
        f"https://www.avito.ru/{AV_ID}/?api=1",
    ]
    av3_results = []
    for u in api_urls:
        try:
            r = cffi.get(u, impersonate="chrome", timeout=8,
                       headers={"Accept":"application/json, text/html","User-Agent":"Mozilla/5.0"}) if HAS_CFFI \
                else requests.get(u, timeout=8, headers={"Accept":"application/json, text/html","User-Agent":"Mozilla/5.0"})
            av3_results.append(f"{u.split('/')[-2][:20]}={r.status_code}")
        except Exception:
            av3_results.append("err")
        time.sleep(0.5)
    rec("AV:api_v2", any("=200" in x for x in av3_results), " ".join(av3_results), time.monotonic()-t0)
except Exception as e:
    rec("AV:api_v2", False, f"err={e}", time.monotonic()-t0)

# --- AV-4: cffi mobile with longer session ---
t0 = time.monotonic()
try:
    if HAS_CFFI:
        # Use cffi session for cookie persistence
        sess = cffi.Session(impersonate="chrome")
        sess.get("https://m.avito.ru/", timeout=10,
                headers={"User-Agent":"Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/131.0.0.0 Mobile Safari/537.36"})
        time.sleep(4)
        r = sess.get(f"https://m.avito.ru/{AV_ID}", timeout=15,
                    headers={"User-Agent":"Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/131.0.0.0 Mobile Safari/537.36"})
        prices4 = re.findall(r'(\d[\d\s]{2,9})\s*(?:₽|руб)', r.text) if r.status_code == 200 else []
        av4_ok = r.status_code == 200 and len(prices4) > 0
        rec("AV:cffi_session", av4_ok, f"HTTP={r.status_code} len={len(r.text)} prices={prices4[:3]}", time.monotonic()-t0)
    else:
        rec("AV:cffi_session", False, "no cffi", 0)
except Exception as e:
    rec("AV:cffi_session", False, f"err={e}", time.monotonic()-t0)

# ============================================================
# SAVE
# ============================================================
result = {
    "time": datetime.now(timezone(timedelta(hours=10))).isoformat(),
    "total_s": round(sum(v["s"] for v in OUT.values()), 1),
    "results": OUT,
}
out_path = os.path.join(BASE, "diag5_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
log.info("Saved to %s", out_path)
print(json.dumps(result, ensure_ascii=False, indent=2))
