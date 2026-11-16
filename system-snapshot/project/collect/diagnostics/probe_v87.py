#!/usr/bin/env python3
"""V87 PROBE: edge124, body analysis, cookie pass, Farpost E2E."""
import sys, os, json, time, re, logging, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s v87 %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("v87")

from curl_cffi import requests as cr

results = {}

# ============================================================
# 1. WILDBERRIES — edge124 + basket body + card.wb.ru
# ============================================================
log.info("=== 1. WILDBERRIES ===")
wb = {}
nm = 57408794
vol = nm // 100000
part = (nm // 1000) % 1000

# 1a: Basket 404 body analysis (what's in those 196B?)
log.info("WB basket: analyzing 404 body")
host = "basket-01.wbbasket.ru"
p = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
try:
    r = cr.get(f"https://{host}{p}", impersonate="edge124", timeout=10,
               headers={"Accept": "application/json"})
    log.info("WB basket edge124: HTTP %d, %dB", r.status_code, len(r.content))
    log.info("WB basket body: %s", r.text[:200])
    wb["basket_body"] = r.text[:200]
except Exception as e:
    log.info("WB basket: %s", e)

# 1b: Try different vol/part calculations
log.info("WB basket: alternative vol/part")
alt_paths = [
    # Standard
    f"/vol{vol}/part{part}/{nm}/info/ru/card.json",
    # Without 'part' keyword
    f"/vol{vol}/{part}/{nm}/info/ru/card.json",
    # With leading zeros
    f"/vol{vol:03d}/part{part:03d}/{nm}/info/ru/card.json",
    # Maybe vol is nm//1000000 for newer items?
    f"/vol{nm // 1000000}/part{(nm // 1000) % 1000}/{nm}/info/ru/card.json",
    # Try image path (images are on static-basket)
    f"/vol{vol}/part{part}/{nm}/images/big1.png",
]
for i, ap in enumerate(alt_paths):
    for hn in [1, 5, 10, 20, 30, 40, 50]:
        h = f"basket-{hn:02d}.wbbasket.ru"
        try:
            r = cr.get(f"https://{h}{ap}", impersonate="edge124", timeout=8,
                       headers={"Accept": "*/*"})
            if r.status_code == 200:
                log.info("WB BASKET HIT: %s%s -> %dB", h, ap, len(r.content))
                wb[f"basket_hit_{i}"] = {"host": h, "path": ap, "size": len(r.content)}
                break
        except Exception:
            pass
        time.sleep(0.5)
    if f"basket_hit_{i}" in wb:
        break
    time.sleep(1)

# 1c: card.wb.ru with edge124
log.info("WB card.wb.ru edge124")
try:
    api = f"https://card.wb.ru/cards/v1/detail?nm={nm}&curr=rub&dest=-1257786"
    r = cr.get(api, impersonate="edge124", timeout=15,
               headers={"Accept": "application/json, text/plain, */*",
                        "Origin": "https://www.wildberries.ru",
                        "Referer": "https://www.wildberries.ru/"})
    log.info("WB card.wb edge124: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        d = r.json()
        prods = d.get("data", {}).get("products", [])
        if prods:
            log.info("WB card name=%s brand=%s", prods[0].get("name", "")[:60], prods[0].get("brand"))
            wb["api_edge"] = {"status": 200, "name": prods[0].get("name")}
    wb.setdefault("api_edge", {"status": r.status_code})
except Exception as e:
    log.info("WB card.wb: %s", e)
    wb["api_edge"] = {"error": str(e)}

# 1d: WB HTML with edge124 (maybe 498 is chrome-specific?)
time.sleep(3)
log.info("WB HTML edge124")
try:
    r = cr.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
               impersonate="edge124", timeout=25,
               headers={"Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("WB HTML edge124: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r"<title>(.*?)</title>", r.text, re.DOTALL)
        log.info("WB HTML title: %s", (m.group(1)[:80] if m else None))
        wb["html_edge"] = {"status": 200, "title": m.group(1) if m else None}
        with open(ROOT / "wb_v87_edge.html", "w", encoding="utf-8") as f:
            f.write(r.text)
    elif r.status_code == 498:
        log.info("WB HTML still 498, body[:200]: %s", r.text[:200])
        wb["html_edge"] = {"status": 498}
    else:
        wb["html_edge"] = {"status": r.status_code}
except Exception as e:
    log.info("WB HTML edge: %s", e)

# 1e: WB session with edge124 (home → product)
time.sleep(2)
log.info("WB session edge124")
try:
    sess = cr.Session(impersonate="edge124")
    r1 = sess.get("https://www.wildberries.ru/", timeout=20,
                  headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("WB home edge: HTTP %d, cookies=%s", r1.status_code, list(sess.cookies.keys()))
    time.sleep(3)
    r2 = sess.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx", timeout=25,
                  headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("WB prod edge sess: HTTP %d, %dB", r2.status_code, len(r2.content))
    if r2.status_code == 200:
        m = re.search(r"<title>(.*?)</title>", r2.text, re.DOTALL)
        wb["sess_edge"] = {"status": 200, "title": m.group(1) if m else None}
    else:
        wb["sess_edge"] = {"status": r2.status_code, "cookies": list(sess.cookies.keys())}
    sess.close()
except Exception as e:
    log.info("WB sess edge: %s", e)

results["WB"] = wb
log.info("WB summary: %s", {k: v.get("status", "ok") for k, v in wb.items() if isinstance(v, dict)})

# ============================================================
# 2. OZON — edge124 + explicit cookies
# ============================================================
log.info("=== 2. OZON ===")
oz = {}
ozon_id = "721093013"
ozon_url = f"https://www.ozon.ru/product/{ozon_id}/"

# 2a: edge124 direct
log.info("Ozon edge124 direct")
try:
    r = cr.get(ozon_url, impersonate="edge124", timeout=20, verify=False,
               headers={"Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon edge124: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r.text)
        oz["edge"] = {"status": 200, "title": m.group(1) if m else None}
    else:
        oz["edge"] = {"status": r.status_code}
except Exception as e:
    log.info("Ozon edge: %s", e)
    oz["edge"] = {"error": str(e)}

# 2b: Session edge124: home → extract cookies → product with explicit Cookie header
time.sleep(3)
log.info("Ozon session edge124")
try:
    sess = cr.Session(impersonate="edge124")
    r_home = sess.get("https://www.ozon.ru/", timeout=20, verify=False,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    cookies = dict(sess.cookies)
    log.info("Ozon home edge: HTTP %d, cookies=%s", r_home.status_code, list(cookies.keys()))
    time.sleep(3)
    
    # Build explicit cookie string
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    log.info("Ozon cookie_str: %s", cookie_str[:200])
    
    r_prod = sess.get(ozon_url, timeout=25, verify=False,
                      headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9",
                               "Cookie": cookie_str})
    log.info("Ozon prod edge sess: HTTP %d, %dB", r_prod.status_code, len(r_prod.content))
    if r_prod.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r_prod.text)
        oz["sess_edge"] = {"status": 200, "title": m.group(1) if m else None}
        with open(ROOT / "ozon_v87_edge.html", "w", encoding="utf-8") as f:
            f.write(r_prod.text)
    else:
        # Check if 403 body has useful info
        body = r_prod.text
        m_waf = re.search(r'waf[^<]{0,100}', body, re.I)
        oz["sess_edge"] = {"status": r_prod.status_code, "waf_hint": m_waf.group(0) if m_waf else None}
    sess.close()
except Exception as e:
    log.info("Ozon sess edge: %s", e)

# 2c: Try api.ozon.ru with edge124 + specific headers
time.sleep(3)
log.info("Ozon API edge124")
try:
    r = cr.get("https://api.ozon.ru/composer-api.bx/page/json/v2",
               impersonate="edge124", timeout=15, verify=False,
               params={"url": ozon_url},
               headers={"Accept": "application/json",
                        "Referer": ozon_url,
                        "Origin": "https://www.ozon.ru",
                        "x-requested-with": "XMLHttpRequest"})
    log.info("Ozon API edge: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        d = r.json()
        oz["api_edge"] = {"status": 200, "keys": list(d.keys())[:5]}
    else:
        oz["api_edge"] = {"status": r.status_code, "body": r.text[:200]}
except Exception as e:
    log.info("Ozon API edge: %s", e)

# 2d: Try a DIFFERENT ozon product (maybe 721093013 is special)
time.sleep(3)
log.info("Ozon alt product")
try:
    alt_url = "https://www.ozon.ru/product/1600562267/"
    r = cr.get(alt_url, impersonate="edge124", timeout=20, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon alt 1600562267: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r.text)
        oz["alt"] = {"status": 200, "title": m.group(1) if m else None}
    else:
        oz["alt"] = {"status": r.status_code}
except Exception as e:
    log.info("Ozon alt: %s", e)

results["OZON"] = oz
log.info("Ozon summary: %s", {k: v.get("status", "ok") for k, v in oz.items() if isinstance(v, dict)})

# ============================================================
# 3. AVITO — 429 body analysis + edge124 + rare category
# ============================================================
log.info("=== 3. AVITO ===")
av = {}

# 3a: Analyze the 429 body (35KB is a full page!)
log.info("Avito: 429 body analysis")
try:
    r = cr.get("https://www.avito.ru/krasnoyarsk/telefony",
               impersonate="edge124", timeout=25, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Avito edge124 cat: HTTP %d, %dB", r.status_code, len(r.content))
    body = r.text
    # Save full body
    with open(ROOT / "avito_v87_429.html", "w", encoding="utf-8") as f:
        f.write(body)
    # Extract ad IDs from 429 body
    ids = re.findall(r'avito\.ru/(?:ad/|krasnoyarsk/[a-z\-]+/)(\d{8,})', body)
    if not ids:
        ids = re.findall(r'/ad/(\d{8,})', body)
    if not ids:
        ids = re.findall(r'"adId"\s*:\s*(\d+)', body)
    if not ids:
        ids = re.findall(r'data-ad-id="(\d+)"', body)
    log.info("Avito 429 IDs: %s", ids[:10])
    # Check for embedded JSON state
    m_state = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.{0,200})', body)
    log.info("Avito 429 state: %s", (m_state.group(1)[:100] if m_state else "none"))
    # Check for og:title
    m_og = re.search(r'og:title[^>]*content="([^"]+)"', body)
    log.info("Avito 429 og:title: %s", (m_og.group(1)[:80] if m_og else None))
    av["cat_edge"] = {"status": r.status_code, "ids": ids[:10], "size": len(body)}
except Exception as e:
    log.info("Avito cat: %s", e)
    av["cat_edge"] = {"error": str(e)}

# 3b: Try a rarer category (less rate-limited)
time.sleep(12)
log.info("Avito: rare category (akvariumy)")
try:
    r = cr.get("https://www.avito.ru/krasnoyarsk/akvariumy-i-akvaristyka",
               impersonate="edge124", timeout=25, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Avito akvariumy: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        ids = re.findall(r'avito\.ru/(?:ad/|krasnoyarsk/[a-z\-]+/)(\d{8,})', r.text)
        if not ids:
            ids = re.findall(r'/ad/(\d{8,})', r.text)
        log.info("Avito akvariumy IDs: %s", ids[:10])
        av["rare_cat"] = {"status": 200, "ids": ids[:10]}
        with open(ROOT / "avito_v87_rare.html", "w", encoding="utf-8") as f:
            f.write(r.text)
    else:
        av["rare_cat"] = {"status": r.status_code}
except Exception as e:
    log.info("Avito rare: %s", e)

# 3c: Try direct ad with edge124 (fresh delay)
time.sleep(10)
log.info("Avito: direct ad edge124")
# Use IDs from 429 body or try common patterns
test_ids = av["cat_edge"].get("ids", [])[:3] if "cat_edge" in av else []
if not test_ids:
    test_ids = ["8341658309"]  # fallback
for aid in test_ids:
    time.sleep(8)
    try:
        r = cr.get(f"https://www.avito.ru/ad/{aid}",
                   impersonate="edge124", timeout=20, verify=False,
                   headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("Avito AD %s edge: HTTP %d, %dB", aid, r.status_code, len(r.content))
        if r.status_code == 200:
            m = re.search(r'og:title[^>]*content="([^"]+)"', r.text, re.I)
            av[f"ad_{aid}"] = {"status": 200, "title": m.group(1) if m else None}
        else:
            av[f"ad_{aid}"] = {"status": r.status_code}
    except Exception as e:
        av[f"ad_{aid}"] = {"error": str(e)}

results["AVITO"] = av
log.info("Avito summary: %s", {k: v.get("status", "ok") for k, v in av.items() if isinstance(v, dict)})

# ============================================================
# 4. FARPOST — E2E with Database persist
# ============================================================
log.info("=== 4. FARPOST E2E ===")
fp = {}
try:
    from r52.db.database import Database
    from r52.db.migrate import migrate
    from r52.store.products import ensure_source, upsert_product, add_observation
    from r52.collectors.farpost import FarpostCollector
    
    DB_PATH = str(ROOT / "r52" / "data" / "r52.db")
    db = Database(DB_PATH)
    db.initialize()
    try:
        migrate(db)
    except Exception as e:
        log.warning("FP migrate: %s", e)
    
    data_dir = ROOT / "r52" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "collectors").mkdir(exist_ok=True)
    
    fc = FarpostCollector(data_dir=data_dir, use_proxies=False)
    fp_id = "127622540"
    fp_url = f"https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-{fp_id}.html"
    
    obs = fc.collect_product(listing_id=fp_id, url=fp_url, title_hint="")
    if obs:
        o = obs.get("observation", {})
        fields = o.get("fields", {})
        title = fields.get("title", {}).get("value", "N/A")
        prices = o.get("price_candidates", [])
        avail = o.get("availability", "UNKNOWN")
        media = o.get("media", {}).get("items", [])
        status = o.get("status", "UNKNOWN")
        
        log.info("FP E2E: title=%s prices=%d avail=%s media=%d status=%s",
                 title[:60], len(prices), avail, len(media), status)
        
        # Persist
        src = ensure_source(db, "FARPOST", "Farpost", "marketplace", "FarpostCollector")
        prod = upsert_product(db, src, fp_id, fp_url, title, None, None, "COLLECTOR")
        obs_id = add_observation(db, prod["id"], o)
        
        log.info("FP E2E PERSIST: src=%s prod_id=%s obs_id=%s", src, prod["id"], obs_id)
        fp = {"status": status, "title": title, "price_minor": prices[0]["amount_minor"] if prices else None,
              "availability": avail, "media_count": len(media),
              "prod_id": prod["id"], "obs_id": obs_id}
    else:
        fp = {"status": "NO_DATA"}
except Exception as e:
    log.error("FP E2E: %s\n%s", e, traceback.format_exc())
    fp = {"error": str(e)}

results["FARPOST"] = fp
log.info("Farpost E2E: %s", fp)

# ============================================================
# FINAL
# ============================================================
log.info("=== V87 DONE ===")
for mp in ["FARPOST", "WB", "OZON", "AVITO"]:
    st = results.get(mp, {})
    ok = any(v.get("status") in (200, "SUCCESS", "PARTIAL_SUCCESS") 
             for v in st.values() if isinstance(v, dict))
    log.info("  %s: %s", mp, "OK" if ok else "PENDING")

with open(ROOT / "probe_v87_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
log.info("Saved probe_v87_result.json")
