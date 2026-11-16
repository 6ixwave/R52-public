#!/usr/bin/env python3
"""V86 PROBE: Fix Farpost, WB session, Ozon session, Avito fresh IDs."""
import sys, os, json, time, re, logging, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s v86 %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("v86")

from curl_cffi import requests as cr

results = {}

# ============================================================
# 0. Check available impersonate versions
# ============================================================
log.info("=== 0. CURL_CFFI VERSIONS ===")
try:
    from curl_cffi.requests.impersonate import BrowserTypeLiteral
    import curl_cffi
    log.info("curl_cffi version: %s", curl_cffi.__version__)
    # Try listing available browsers
    from curl_cffi.requests import BrowserTypeLiteral
    avail = []
    for v in ["chrome", "chrome124", "chrome123", "chrome120", "chrome119",
              "chrome116", "chrome112", "chrome110", "chrome109",
              "safari", "safari17_0", "safari17_2", "safari15_3",
              "firefox", "firefox133", "firefox132", "firefox128",
              "edge", "edge124", "edge123"]:
        try:
            s = cr.Session(impersonate=v)
            avail.append(v)
            s.close()
        except Exception:
            pass
    log.info("Available impersonate: %s", avail)
except Exception as e:
    log.warning("Version check: %s", e)

# ============================================================
# 1. FARPOST — fix Path vs str
# ============================================================
log.info("=== 1. FARPOST (collector, fixed) ===")
farpost_results = {}
try:
    from r52.collectors.farpost import FarpostCollector
    data_dir = ROOT / "r52" / "data"
    # Ensure directory exists
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "collectors").mkdir(exist_ok=True)
    
    fc = FarpostCollector(data_dir=data_dir, use_proxies=False)
    fp_id = "127622540"
    fp_url = f"https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-{fp_id}.html"
    log.info("FP collector: %s", fp_url)
    obs = fc.collect_product(listing_id=fp_id, url=fp_url, title_hint="")
    if obs:
        o = obs.get("observation", {})
        fields = o.get("fields", {})
        title = fields.get("title", {}).get("value", "N/A")
        prices = o.get("price_candidates", [])
        avail = o.get("availability", "UNKNOWN")
        media = o.get("media", {}).get("items", [])
        log.info("FP collector: title=%s prices=%d avail=%s media=%d status=%s",
                 title[:60], len(prices), avail, len(media), o.get("status"))
        farpost_results = {
            "status": o.get("status"),
            "title": title,
            "price_minor": prices[0]["amount_minor"] if prices else None,
            "availability": avail,
            "media_count": len(media),
            "category": o.get("category_path"),
        }
    else:
        farpost_results = {"status": "NO_DATA"}
        log.warning("FP collector: NO_DATA")
except Exception as e:
    log.error("FP collector ERR: %s\n%s", e, traceback.format_exc())
    farpost_results = {"error": str(e)}

results["FARPOST"] = farpost_results
log.info("Farpost: %s", {k: v for k, v in farpost_results.items() if k != "prices"})

# ============================================================
# 2. WILDBERRIES — Session approach
# ============================================================
log.info("=== 2. WILDBERRIES (session) ===")
wb_results = {}
nm = 57408794
vol = nm // 100000
part = (nm // 1000) % 1000

# --- 2a. Session: home → cookies → product ---
try:
    sess = cr.Session(impersonate="chrome124")
    
    # Step 1: Hit homepage to get cookies
    log.info("WB sess: GET home")
    r_home = sess.get("https://www.wildberries.ru/", timeout=20,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("WB home: HTTP %d, %dB, cookies=%s", 
             r_home.status_code, len(r_home.content), list(sess.cookies.keys()))
    time.sleep(2)
    
    # Step 2: Hit product page with session cookies
    wb_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    log.info("WB sess: GET product")
    r_prod = sess.get(wb_url, timeout=25,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("WB product: HTTP %d, %dB", r_prod.status_code, len(r_prod.content))
    
    if r_prod.status_code == 200:
        txt = r_prod.text
        m_title = re.search(r"<title>(.*?)</title>", txt, re.DOTALL)
        m_og = re.search(r'og:title[^>]*content="([^"]+)"', txt)
        m_price = re.search(r'"price"\s*:\s*(\d+)', txt)
        m_brand = re.search(r'"brand"\s*:\s*"([^"]+)"', txt)
        log.info("WB product title: %s", (m_title.group(1)[:80] if m_title else None))
        log.info("WB product og:title: %s", (m_og.group(1)[:80] if m_og else None))
        log.info("WB product price: %s", (m_price.group(1) if m_price else None))
        log.info("WB product brand: %s", (m_brand.group(1) if m_brand else None))
        wb_results["session_html"] = {
            "status": 200,
            "title": m_title.group(1) if m_title else None,
            "price": m_price.group(1) if m_price else None,
        }
        with open(ROOT / "wb_v86_session.html", "w", encoding="utf-8") as f:
            f.write(txt)
    elif r_prod.status_code == 498:
        log.info("WB product 498 (still challenge), cookies=%s", list(sess.cookies.keys()))
        wb_results["session_html"] = {"status": 498, "cookies": list(sess.cookies.keys())}
    else:
        wb_results["session_html"] = {"status": r_prod.status_code}
    
    # Step 3: Try API with session cookies
    time.sleep(3)
    api_url = f"https://card.wb.ru/cards/v1/detail?nm={nm}&curr=rub&dest=-1257786"
    r_api = sess.get(api_url, timeout=15,
                     headers={"Accept": "application/json",
                              "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"})
    log.info("WB API (sess): HTTP %d, %dB", r_api.status_code, len(r_api.content))
    if r_api.status_code == 200:
        d = r_api.json()
        prods = d.get("data", {}).get("products", [])
        if prods:
            p = prods[0]
            log.info("WB API sess: name=%s brand=%s", p.get("name", "")[:60], p.get("brand"))
            wb_results["api_session"] = {"status": 200, "name": p.get("name")}
    wb_results.setdefault("api_session", {"status": r_api.status_code})
    
    sess.close()
except Exception as e:
    log.warning("WB session ERR: %s", e)
    wb_results["session"] = {"error": str(e)}

# --- 2b. Basket CDN: try static-basket prefix + more hosts ---
log.info("WB basket: trying static-basket + new path formats")
basket_paths = [
    # Old format on regular basket
    f"/vol{vol}/part{part}/{nm}/info/ru/card.json",
    # New: maybe no "info/ru"
    f"/vol{vol}/part{part}/{nm}/card.json",
    # Try with leading zero on part?
    f"/vol{vol}/part{part:03d}/{nm}/info/ru/card.json",
]

for host_prefix in ["basket", "static-basket"]:
    for n in [1, 5, 10]:
        host = f"{host_prefix}-{n:02d}.wbbasket.ru"
        for p in basket_paths[:2]:
            url = f"https://{host}{p}"
            try:
                r = cr.get(url, impersonate="chrome124", timeout=8,
                           headers={"Accept": "application/json",
                                    "Referer": "https://www.wildberries.ru/"})
                if r.status_code == 200:
                    log.info("WB BASKET FOUND: %s -> %dB", url, len(r.content))
                    wb_results["basket"] = {"url": url, "status": 200}
                    break
                elif n == 1 and p == basket_paths[0]:
                    log.info("WB basket %s%s -> %d", host, p[:40], r.status_code)
            except Exception as e:
                if "getaddrinfo" in str(e) or "resolve" in str(e).lower():
                    log.info("WB basket %s: DNS FAIL", host)
                    break
                log.debug("WB basket %s: %s", host, e)
            time.sleep(1)
        if "basket" in wb_results:
            break
    if "basket" in wb_results:
        break

# --- 2c. Try second WB ID (199298871) on basket ---
nm2 = 199298871
vol2 = nm2 // 100000  # 1992
part2 = (nm2 // 1000) % 1000  # 298
log.info("WB basket nm2: vol=%d part=%d", vol2, part2)
for n in [1, 3, 7, 15, 25]:
    host = f"basket-{n:02d}.wbbasket.ru"
    p = f"/vol{vol2}/part{part2}/{nm2}/info/ru/card.json"
    url = f"https://{host}{p}"
    try:
        r = cr.get(url, impersonate="chrome124", timeout=8,
                   headers={"Accept": "application/json"})
        log.info("WB basket2 %s: HTTP %d, %dB", host, r.status_code, len(r.content))
        if r.status_code == 200:
            wb_results["basket_nm2"] = {"url": url, "status": 200}
            break
    except Exception as e:
        log.info("WB basket2 %s: %s", host, str(e)[:60])
    time.sleep(1.5)

results["WB"] = wb_results
log.info("WB summary: %s", {k: v.get("status", "ok") for k, v in wb_results.items() if isinstance(v, dict)})

# ============================================================
# 3. OZON — Session + warm-up
# ============================================================
log.info("=== 3. OZON (session) ===")
ozon_results = {}
ozon_id = "721093013"

try:
    sess = cr.Session(impersonate="chrome124")
    
    # Warm-up: homepage
    log.info("Ozon sess: GET home")
    r_home = sess.get("https://www.ozon.ru/", timeout=20, verify=False,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon home: HTTP %d, %dB, cookies=%s",
             r_home.status_code, len(r_home.content), list(sess.cookies.keys()))
    time.sleep(3)
    
    # Product page
    ozon_url = f"https://www.ozon.ru/product/{ozon_id}/"
    log.info("Ozon sess: GET product")
    r_prod = sess.get(ozon_url, timeout=25, verify=False,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon product: HTTP %d, %dB", r_prod.status_code, len(r_prod.content))
    
    if r_prod.status_code == 200:
        txt = r_prod.text
        m_og = re.search(r'og:title[^>]*content="([^"]+)"', txt)
        m_price = re.search(r'"price"\s*:\s*"?(\d+)', txt)
        log.info("Ozon product og:title: %s", (m_og.group(1)[:80] if m_og else None))
        ozon_results["session"] = {"status": 200, "title": m_og.group(1) if m_og else None}
        with open(ROOT / "ozon_v86_session.html", "w", encoding="utf-8") as f:
            f.write(txt)
    else:
        # Analyze 403 body for WAF cookie hints
        body = r_prod.text
        m_cookie = re.findall(r'set-cookie[^;]*|_ozon[^;]*|waf[^;]*', body[:3000], re.I)
        log.info("Ozon 403 cookie hints: %s", m_cookie[:5])
        ozon_results["session"] = {"status": r_prod.status_code, "cookies": list(sess.cookies.keys())}
    
    # Try composer API with session
    time.sleep(3)
    log.info("Ozon sess: composer API")
    r_comp = sess.get(
        "https://api.ozon.ru/composer-api.bx/page/json/v2",
        timeout=15, verify=False,
        params={"url": ozon_url},
        headers={"Accept": "application/json",
                 "Referer": ozon_url}
    )
    log.info("Ozon composer: HTTP %d, %dB", r_comp.status_code, len(r_comp.content))
    if r_comp.status_code == 200:
        d = r_comp.json()
        log.info("Ozon composer keys: %s", list(d.keys())[:10])
        ozon_results["composer"] = {"status": 200, "keys": list(d.keys())[:10]}
    ozon_results.setdefault("composer", {"status": r_comp.status_code})
    
    sess.close()
except Exception as e:
    log.warning("Ozon session ERR: %s", e)
    ozon_results["session"] = {"error": str(e)}

# --- 3b. Try different product (kardamon) with full slug URL ---
time.sleep(2)
try:
    slug_url = "https://www.ozon.ru/product/kardamon-tselyy-zelenyy-krupnye-otbornye-zerna-100-gr-721093013/"
    r = cr.get(slug_url, impersonate="chrome124", timeout=20, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon slug: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r.text)
        ozon_results["slug"] = {"status": 200, "title": m.group(1) if m else None}
    ozon_results.setdefault("slug", {"status": r.status_code})
except Exception as e:
    log.warning("Ozon slug ERR: %s", e)

results["OZON"] = ozon_results
log.info("Ozon summary: %s", {k: v.get("status", "ok") for k, v in ozon_results.items() if isinstance(v, dict)})

# ============================================================
# 4. AVITO — longer delays + fresh approach
# ============================================================
log.info("=== 4. AVITO (fresh) ===")
avito_results = {}

# 4a: Try desktop with 15s initial delay
log.info("Avito: waiting 15s before first request...")
time.sleep(15)

cat_urls = [
    ("d_telefony", "https://www.avito.ru/krasnoyarsk/telefony"),
    ("m_telefony", "https://m.avito.ru/krasnoyarsk/telefony"),
]

ad_ids = []
for name, url in cat_urls:
    try:
        r = cr.get(url, impersonate="chrome124", timeout=30, verify=False,
                   headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("Avito %s: HTTP %d, %dB", name, r.status_code, len(r.content))
        if r.status_code == 200 and len(r.content) > 5000:
            # Multiple extraction strategies
            ids = re.findall(r'avito\.ru/(?:ad/|krasnoyarsk/[a-z\-]+/)(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'/ad/(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'"adId"\s*:\s*(\d+)', r.text)
            if not ids:
                ids = re.findall(r'data-ad-id="(\d+)"', r.text)
            if not ids:
                # Last resort: any 9-10 digit number in hrefs
                ids = re.findall(r'href="[^"]*?/(\d{9,10})/"', r.text)
            log.info("Avito %s IDs: %s", name, ids[:10])
            ad_ids.extend(ids[:5])
            with open(ROOT / f"avito_v86_{name}.html", "w", encoding="utf-8") as f:
                f.write(r.text)
            avito_results[name] = {"status": 200, "ids_count": len(ids)}
        else:
            avito_results[name] = {"status": r.status_code}
    except Exception as e:
        log.warning("Avito %s ERR: %s", name, e)
        avito_results[name] = {"error": str(e)}
    time.sleep(15)  # Long delay between category requests

log.info("Avito IDs found: %s", ad_ids[:10])

# 4b: Try direct ad with found IDs
if ad_ids:
    for aid in ad_ids[:2]:
        time.sleep(10)
        for prefix in ["https://www.avito.ru/ad/", "https://m.avito.ru/ad/"]:
            aurl = f"{prefix}{aid}"
            try:
                r = cr.get(aurl, impersonate="chrome124", timeout=20, verify=False,
                           headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("Avito AD %s: HTTP %d, %dB", aid, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.content) > 5000:
                    m = re.search(r'og:title[^>]*content="([^"]+)"', r.text, re.I)
                    t = m.group(1) if m else None
                    log.info("Avito AD title: %s", (t[:80] if t else None))
                    avito_results[f"ad_{aid}"] = {"status": 200, "title": t}
                    break
            except Exception as e:
                log.warning("Avito AD %s: %s", aid, e)

# 4c: If no IDs from categories, try known pattern
if not ad_ids:
    log.info("Avito: no IDs from categories, trying direct known IDs with long delay")
    # Try IDs that might still be live (from DB or common patterns)
    test_ids = ["8341658309", "1154321987"]
    for aid in test_ids:
        time.sleep(12)
        aurl = f"https://www.avito.ru/ad/{aid}"
        try:
            r = cr.get(aurl, impersonate="chrome124", timeout=20, verify=False,
                       headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
            log.info("Avito known %s: HTTP %d, %dB", aid, r.status_code, len(r.content))
            if r.status_code == 200:
                avito_results[f"known_{aid}"] = {"status": 200}
            elif r.status_code == 404:
                avito_results[f"known_{aid}"] = {"status": 404, "note": "dead"}
            else:
                avito_results[f"known_{aid}"] = {"status": r.status_code}
        except Exception as e:
            avito_results[f"known_{aid}"] = {"error": str(e)}

results["AVITO"] = avito_results
log.info("Avito summary: %s", {k: v.get("status", "ok") for k, v in avito_results.items() if isinstance(v, dict)})

# ============================================================
# FINAL
# ============================================================
log.info("=== V86 PROBE DONE ===")
for mp in ["FARPOST", "WB", "OZON", "AVITO"]:
    st = results.get(mp, {})
    ok = any(v.get("status") == 200 or v.get("status") in ("SUCCESS", "PARTIAL_SUCCESS") 
             for v in st.values() if isinstance(v, dict))
    log.info("  %s: %s", mp, "HAS_DATA" if ok else "NO_DATA")

with open(ROOT / "probe_v86_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
log.info("Saved probe_v86_result.json")
