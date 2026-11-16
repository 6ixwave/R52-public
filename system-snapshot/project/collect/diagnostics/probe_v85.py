#!/usr/bin/env python3
"""V85 PROBE: targeted tests for each marketplace.
Goal: find ONE working strategy per marketplace before building E2E.
"""
import sys, os, json, time, re, logging, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s v85 %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("v85")

from curl_cffi import requests as cr

results = {}

# ============================================================
# 1. WILDBERRIES
# ============================================================
log.info("=== 1. WILDBERRIES ===")
wb_results = {}

# --- 1a. Basket CDN: try multiple path formats on basket-01 ---
nm = 57408794
vol = nm // 100000          # 574
part = (nm // 1000) % 1000  # 408

path_variants = [
    f"/vol{vol}/part{part}/{nm}/info/ru/card.json",
    f"/vol{vol}/part{part}/{nm}/info/card.json",
    f"/vol{vol}/part{part}/{nm}/card.json",
    f"/vol{vol}/{part}/{nm}/info/ru/card.json",
    f"/vol{vol}/{part}/{nm}/card.json",
    f"/{vol}/{part}/{nm}/info/ru/card.json",
]

log.info("WB basket: nm=%d vol=%d part=%d", nm, vol, part)
for i, p in enumerate(path_variants):
    host = f"basket-{(i % 5) + 1:02d}.wbbasket.ru"
    url = f"https://{host}{p}"
    try:
        r = cr.get(url, impersonate="chrome124", timeout=10,
                   headers={"Accept": "application/json",
                            "Referer": "https://www.wildberries.ru/"})
        log.info("WB basket[%d] %s%s -> HTTP %d, %dB", i, host, p[:50], r.status_code, len(r.content))
        if r.status_code == 200:
            wb_results["basket"] = {"url": url, "data": r.json()}
            log.info("WB BASKET FOUND! keys=%s", list(r.json().keys())[:10])
            break
    except Exception as e:
        log.warning("WB basket[%d] ERR: %s", i, e)
    time.sleep(1.5)

# --- 1b. HTML detail.aspx with chrome124 ---
wb_html_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
try:
    r = cr.get(wb_html_url, impersonate="chrome124", timeout=20,
               headers={"Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"})
    log.info("WB HTML: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        txt = r.text
        m_title = re.search(r"<title>(.*?)</title>", txt, re.DOTALL)
        m_og = re.search(r'og:title[^>]*content="([^"]+)"', txt)
        m_price = re.search(r'"price"\s*:\s*(\d+)', txt)
        m_next = re.search(r'window\.__NEXT_DATA__\s*=\s*(\{)', txt)
        log.info("WB HTML title: %s", (m_title.group(1)[:80] if m_title else None))
        log.info("WB HTML og:title: %s", (m_og.group(1)[:80] if m_og else None))
        log.info("WB HTML price: %s", (m_price.group(1) if m_price else None))
        log.info("WB HTML NEXT_DATA: %s", ("yes" if m_next else "no"))
        wb_results["html"] = {"status": 200, "title": m_title.group(1) if m_title else None}
        with open(ROOT / "wb_probe_html.html", "w", encoding="utf-8") as f:
            f.write(txt)
    elif r.status_code == 498:
        log.info("WB HTML 498 body[:300]: %s", r.text[:300])
        wb_results["html"] = {"status": 498, "body_head": r.text[:300]}
    else:
        wb_results["html"] = {"status": r.status_code}
except Exception as e:
    log.warning("WB HTML ERR: %s", e)
    wb_results["html"] = {"error": str(e)}

# --- 1c. card.wb.ru v1 with chrome124 (retry, maybe WAF cooled down) ---
time.sleep(3)
try:
    api_url = f"https://card.wb.ru/cards/v1/detail?nm={nm}&curr=rub&dest=-1257786"
    r = cr.get(api_url, impersonate="chrome124", timeout=15,
               headers={"Accept": "application/json, text/plain, */*",
                        "Origin": "https://www.wildberries.ru",
                        "Referer": "https://www.wildberries.ru/"})
    log.info("WB API v1: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        d = r.json()
        prods = d.get("data", {}).get("products", [])
        if prods:
            p = prods[0]
            log.info("WB API v1 product: name=%s brand=%s", p.get("name", "")[:60], p.get("brand"))
            sizes = p.get("sizes", [])
            if sizes:
                log.info("WB API v1 sizes[0]: price=%s", sizes[0].get("price"))
            wb_results["api_v1"] = {"status": 200, "product": p}
    wb_results.setdefault("api_v1", {"status": r.status_code})
except Exception as e:
    log.warning("WB API v1 ERR: %s", e)
    wb_results["api_v1"] = {"error": str(e)}

results["WB"] = wb_results
log.info("WB summary: %s", {k: v.get("status", "ok" if "data" in v else "partial") for k, v in wb_results.items()})

# ============================================================
# 2. OZON
# ============================================================
log.info("=== 2. OZON ===")
ozon_results = {}
ozon_id = "721093013"
ozon_url = f"https://www.ozon.ru/product/{ozon_id}/"

# --- 2a. Analyze 403 body ---
try:
    r = cr.get(ozon_url, impersonate="chrome124", timeout=20, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon chrome124: HTTP %d, %dB", r.status_code, len(r.content))
    body = r.text
    # Check for WAF markers
    markers = ["challenge", "captcha", "data-widget", "__INITIAL_STATE__",
               "og:title", "ir.ozone.ru", "price", "item-card",
               "application/ld+json", "waf", "bot", "verify"]
    found = [m for m in markers if m.lower() in body.lower()]
    log.info("Ozon 403 markers: %s", found)
    log.info("Ozon 403 head: %s", body[:400])
    with open(ROOT / "ozon_probe_403.html", "w", encoding="utf-8") as f:
        f.write(body)
    ozon_results["chrome124"] = {"status": r.status_code, "markers": found}
except Exception as e:
    log.warning("Ozon chrome124 ERR: %s", e)
    ozon_results["chrome124"] = {"error": str(e)}

# --- 2b. Safari ---
time.sleep(3)
try:
    r = cr.get(ozon_url, impersonate="safari17_5", timeout=20, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon safari17_5: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r.text)
        log.info("Ozon safari og:title: %s", (m.group(1)[:80] if m else None))
        ozon_results["safari"] = {"status": 200, "title": m.group(1) if m else None}
    else:
        ozon_results["safari"] = {"status": r.status_code}
except Exception as e:
    log.warning("Ozon safari ERR: %s", e)
    ozon_results["safari"] = {"error": str(e)}

# --- 2c. Firefox ---
time.sleep(3)
try:
    r = cr.get(ozon_url, impersonate="firefox133", timeout=20, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon firefox133: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r.text)
        log.info("Ozon firefox og:title: %s", (m.group(1)[:80] if m else None))
        ozon_results["firefox"] = {"status": 200, "title": m.group(1) if m else None}
    else:
        ozon_results["firefox"] = {"status": r.status_code}
except Exception as e:
    log.warning("Ozon firefox ERR: %s", e)
    ozon_results["firefox"] = {"error": str(e)}

# --- 2d. Mobile ---
time.sleep(3)
try:
    murl = f"https://m.ozon.ru/product/{ozon_id}/"
    r = cr.get(murl, impersonate="chrome124", timeout=20, verify=False,
               headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("Ozon mobile: HTTP %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        m = re.search(r'og:title[^>]*content="([^"]+)"', r.text)
        log.info("Ozon mobile og:title: %s", (m.group(1)[:80] if m else None))
        ozon_results["mobile"] = {"status": 200, "title": m.group(1) if m else None}
    else:
        ozon_results["mobile"] = {"status": r.status_code}
except Exception as e:
    log.warning("Ozon mobile ERR: %s", e)
    ozon_results["mobile"] = {"error": str(e)}

results["OZON"] = ozon_results
log.info("Ozon summary: %s", {k: v.get("status", "ok") for k, v in ozon_results.items()})

# ============================================================
# 3. AVITO
# ============================================================
log.info("=== 3. AVITO ===")
avito_results = {}

# --- 3a. Category page (mobile, less protected) ---
cat_urls = [
    ("m_telefony", "https://m.avito.ru/krasnoyarsk/telefony"),
    ("m_noutbuki", "https://m.avito.ru/krasnoyarsk/noutbuki"),
    ("m_tv", "https://m.avito.ru/krasnoyarsk/telefony-i-smartfony"),
]

ad_ids_found = []
for name, url in cat_urls:
    try:
        r = cr.get(url, impersonate="chrome124", timeout=25, verify=False,
                   headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("Avito %s: HTTP %d, %dB", name, r.status_code, len(r.content))
        if r.status_code == 200 and len(r.content) > 5000:
            # Extract ad IDs
            ids = re.findall(r'avito\.ru/(?:ad/|krasnoyarsk/[a-z\-]+/)(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'/ad/(\d{8,})', r.text)
            if not ids:
                ids = re.findall(r'data-ad-id[=:]\s*"?(\d+)', r.text)
            log.info("Avito %s IDs: %s", name, ids[:10])
            ad_ids_found.extend(ids[:5])
            with open(ROOT / f"avito_probe_{name}.html", "w", encoding="utf-8") as f:
                f.write(r.text)
            avito_results[name] = {"status": 200, "ids": ids[:10]}
        else:
            avito_results[name] = {"status": r.status_code}
    except Exception as e:
        log.warning("Avito %s ERR: %s", name, e)
        avito_results[name] = {"error": str(e)}
    time.sleep(8)

log.info("Avito total IDs: %s", ad_ids_found[:15])

# --- 3b. Try direct ad URLs ---
if ad_ids_found:
    for aid in ad_ids_found[:3]:
        time.sleep(6)
        for prefix in ["https://www.avito.ru/ad/", "https://m.avito.ru/ad/"]:
            aurl = f"{prefix}{aid}"
            try:
                r = cr.get(aurl, impersonate="chrome124", timeout=20, verify=False,
                           headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("Avito AD %s (%s): HTTP %d, %dB", aid, prefix, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.content) > 5000:
                    m = re.search(r'og:title[^>]*content="([^"]+)"', r.text, re.I)
                    t = m.group(1) if m else None
                    log.info("Avito AD title: %s", (t[:80] if t else None))
                    avito_results[f"ad_{aid}"] = {"status": 200, "title": t, "url": aurl}
                    break
            except Exception as e:
                log.warning("Avito AD %s ERR: %s", aid, e)

results["AVITO"] = avito_results
log.info("Avito summary: %s", {k: v.get("status", "ok") for k, v in avito_results.items()})

# ============================================================
# 4. FARPOST (through actual collector)
# ============================================================
log.info("=== 4. FARPOST (collector) ===")
farpost_results = {}
try:
    from r52.collectors.farpost import FarpostCollector
    fc = FarpostCollector(data_dir=str(ROOT / "r52" / "data"), use_proxies=False)
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
            "prices": prices,
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
log.info("Farpost summary: %s", {k: v for k, v in farpost_results.items() if k != "prices"})

# ============================================================
# FINAL
# ============================================================
log.info("=== V85 PROBE DONE ===")
for mp in ["WB", "OZON", "AVITO", "FARPOST"]:
    st = results.get(mp, {})
    ok = any(v.get("status") == 200 or v.get("status") == "SUCCESS" for v in st.values() if isinstance(v, dict))
    log.info("  %s: %s", mp, "HAS_DATA" if ok else "NO_DATA")

# Save results
with open(ROOT / "probe_v85_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
log.info("Saved probe_v85_result.json")
