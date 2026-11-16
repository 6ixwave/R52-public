#!/usr/bin/env python3
"""V84 E2E: All 4 marketplaces — cookie sessions, edge, composer API.

Changes from v83:
- WB: v1 API, cookie-jar HTML (session), web API, chrome124
- Ozon: cookie warm-up (homepage), edge impersonation, composer API
- Avito: 15s delays, edge, broader categories, mobile API
- Farpost: same (confirmed OK)
"""

import sys, os, json, time, hashlib, logging, traceback, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from r52.db.database import Database
from r52.db.migrate import migrate, schema_version
from r52.store.products import (
    ensure_source, upsert_product, add_observation, refresh_summary
)
from r52.timeutil import iso, utc_now

DB_PATH = ROOT / "r52" / "data" / "r52.db"
NOW = iso(utc_now())

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s v84 %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("v84")

results = {}


def db_counts(db):
    with db.read() as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("product", "observation", "price_point", "source")}


def persist(db, source, listing_id, url, title, obs_data, brand=None, model=None):
    with db.write() as conn:
        ensure_source(conn, source, kind="marketplace")
        product_id, created = upsert_product(
            conn, source=source, listing_id=str(listing_id), title=title,
            url=url, brand=brand, model=model, origin=f"PERSIST_{source}", now=NOW
        )
        normalized = {
            "version": "R52_NORMALIZED_3",
            "scope": "COLLECTOR",
            "source": source,
            "listing_id": str(listing_id),
            "url": url,
            "title": title,
            "fields": obs_data.get("fields", {}),
            "prices": obs_data.get("price_candidates", []),
            "media": obs_data.get("media", {}),
            "availability": obs_data.get("availability", "UNKNOWN"),
            "category_path": obs_data.get("category_path"),
            "observed_at": NOW,
        }
        fp_input = f"COLLECTOR\n{source}\n{listing_id}\n{NOW}\n" + \
                   hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        fp = hashlib.sha256(fp_input.encode()).hexdigest()

        prices = []
        for pc in obs_data.get("price_candidates", []):
            if pc.get("amount_minor") is not None:
                prices.append({
                    "role": pc.get("role", "regular"),
                    "amount_minor": int(pc["amount_minor"]),
                    "currency": pc.get("currency", "RUB"),
                    "label": pc.get("label"),
                })

        obs_id, obs_created = add_observation(
            conn, product_id=product_id, normalized=normalized, raw=obs_data,
            origin=f"COLLECTOR_{source}", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
            fingerprint=fp, observed_at=NOW,
            availability=obs_data.get("availability", "UNKNOWN"),
            prices=prices, now=NOW,
        )
        refresh_summary(conn, product_id)
    return {"product_id": product_id, "product_created": created,
            "observation_id": obs_id, "obs_created": obs_created,
            "prices_count": len(prices)}


def parse_jsonld(html):
    for block in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        try:
            data = json.loads(block.strip())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type", "")
                if isinstance(t, list):
                    t = " ".join(t)
                if "Product" in t:
                    result = {"title": item.get("name"), "brand": None, "model": None,
                              "price_minor": None, "availability": "UNKNOWN"}
                    if isinstance(item.get("brand"), dict):
                        result["brand"] = item["brand"].get("name")
                    elif isinstance(item.get("brand"), str):
                        result["brand"] = item["brand"]
                    result["model"] = item.get("mpn") or item.get("sku")
                    offers = item.get("offers")
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    if isinstance(offers, dict):
                        pv = offers.get("price") or offers.get("lowPrice")
                        if pv:
                            try:
                                result["price_minor"] = int(round(float(str(pv).replace(",", ".")) * 100))
                            except (ValueError, TypeError):
                                pass
                        avail = str(offers.get("availability", "")).upper()
                        if "IN_STOCK" in avail or "AVAIL" in avail:
                            result["availability"] = "IN_STOCK"
                        elif "OUT" in avail or "UNAVAIL" in avail:
                            result["availability"] = "OUT_OF_STOCK"
                    return result
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def parse_og_title(html):
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def make_price_list(price_minor, label="page"):
    if not price_minor:
        return []
    return [{
        "role": "regular",
        "amount": f"{price_minor // 100}.{price_minor % 100:02d}",
        "amount_minor": price_minor,
        "currency": "RUB",
        "visible_label": f"{price_minor // 100} \u20bd",
        "label": label,
    }]


# ============================================================
# SECTION 0: DB INIT
# ============================================================
log.info("=== V84 E2E ALL ===")
db = Database(DB_PATH)
db.initialize()
try:
    mig = migrate(db)
    log.info("DB migrate: %s", mig)
except Exception as e:
    log.warning("DB migrate: %s", e)
PRE = db_counts(db)
log.info("PRE: %s", PRE)

# ============================================================
# SECTION 1: FARPOST (confirmed working)
# ============================================================
log.info("=== 1. FARPOST ===")
try:
    from curl_cffi import requests as cr

    FP_ID = "127622540"
    fp_urls = [
        f"https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-{FP_ID}.html",
        f"https://www.farpost.ru/{FP_ID}",
    ]

    fp_html = None
    fp_url_used = None
    for fp_url in fp_urls:
        try:
            log.info("FP try: %s", fp_url)
            resp = cr.get(fp_url, impersonate="chrome124", timeout=20,
                          headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            log.info("FP: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200 and len(resp.content) > 5000:
                fp_html = resp.text
                fp_url_used = fp_url
                break
        except Exception as e:
            log.warning("FP err: %s", e)

    if fp_html:
        info = parse_jsonld(fp_html) or {}
        title = info.get("title") or parse_og_title(fp_html) or f"Farpost {FP_ID}"
        price_minor = info.get("price_minor")
        availability = info.get("availability", "UNKNOWN")
        brand = info.get("brand")
        model = info.get("model")

        if not price_minor:
            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', fp_html)
            if m:
                price_minor = int(m.group(1)) * 100

        if not price_minor:
            m = re.search(
                r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:\u20bd|\u0440\u0443\u0431)',
                fp_html, re.DOTALL | re.IGNORECASE
            )
            if m:
                try:
                    price_minor = int(round(float(m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                except (ValueError, TypeError):
                    pass

        media_items = []
        for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*farpost[^"\']*\.(?:jpg|png|webp)[^"\']*)["\']', fp_html, re.I):
            media_items.append({"type": "image", "url": m.group(1)})
        media_items = media_items[:30]

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "chrome124", "source": "FARPOST"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page", "source": "FARPOST"}},
            },
            "price_candidates": make_price_list(price_minor),
            "media": {"items": media_items},
            "availability": availability,
            "category_path": "auto > wheel > tire",
        }
        pr = persist(db, "FARPOST", FP_ID, fp_url_used, title, obs_data, brand, model)
        log.info("FP PERSIST: %s", pr)
        results["FARPOST"] = {"status": "OK", **pr, "price_minor": price_minor,
                              "availability": availability, "title": title, "url": fp_url_used}
    else:
        results["FARPOST"] = {"status": "ALL_FAILED", "note": "all URL patterns failed"}

except Exception as e:
    log.error("FARPOST error: %s\n%s", e, traceback.format_exc())
    results["FARPOST"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 2: WILDBERRIES (cookie session + v1 + web API)
# ============================================================
log.info("=== 2. WILDBERRIES ===")
try:
    from curl_cffi import requests as cr

    WB_IDS = ["57408794", "199298871"]
    wb_title = None
    wb_price_minor = None
    wb_availability = "UNKNOWN"
    wb_brand = None
    wb_model = None
    wb_media = []
    wb_used_id = None
    wb_used_url = None

    for WB_NM in WB_IDS:
        if wb_title and wb_price_minor:
            break
        wb_used_id = WB_NM
        WB_URL = f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx"
        nm_int = int(WB_NM)
        vol = nm_int // 100000
        part = (nm_int // 1000) % 1000
        log.info("WB nm=%s vol=%d part=%d", WB_NM, vol, part)

        # --- Strategy A: card.wb.ru v1 + v2 + v4 with chrome124 ---
        for ver in ["v1", "v2", "v4"]:
            if wb_price_minor:
                break
            try:
                api_url = f"https://card.wb.ru/cards/{ver}/detail?nm={WB_NM}&curr=rub&dest=-1257786"
                log.info("WB API %s: %s", ver, api_url)
                resp = cr.get(api_url, impersonate="chrome124", timeout=15,
                              headers={"Accept": "application/json, text/plain, */*",
                                       "Origin": "https://www.wildberries.ru",
                                       "Referer": "https://www.wildberries.ru/"})
                log.info("WB API %s: HTTP %d, %dB", ver, resp.status_code, len(resp.content))
                if resp.status_code == 200:
                    data = resp.json()
                    products = (data.get("data", {}).get("products")
                                or data.get("products")
                                or (data.get("data", {}).get("items") if isinstance(data.get("data"), dict) else None)
                                or [])
                    if products:
                        p = products[0]
                        wb_title = p.get("name") or wb_title
                        wb_brand = p.get("brand") or wb_brand
                        # Price from sizes
                        for sz in p.get("sizes", []):
                            pr_obj = sz.get("price", {})
                            if pr_obj:
                                for k in ("basic", "product", "card", "wallet", "sale"):
                                    if k in pr_obj and pr_obj[k] and pr_obj[k] > 0:
                                        wb_price_minor = int(pr_obj[k])
                                        break
                            if wb_price_minor:
                                break
                        # Fallback: top-level price fields
                        if not wb_price_minor:
                            for k in ("salePriceU", "priceU", "price"):
                                if p.get(k):
                                    wb_price_minor = int(p[k])
                                    break
                        stock = p.get("stock", 0)
                        if stock:
                            wb_availability = "IN_STOCK" if stock > 0 else "OUT_OF_STOCK"
                        for sz in p.get("sizes", []):
                            for img in sz.get("img", []):
                                if isinstance(img, str) and img.startswith("http"):
                                    wb_media.append({"type": "image", "url": img})
                        log.info("WB API %s: title=%s price=%d stock=%d", ver, wb_title, wb_price_minor or 0, stock)
                        break
            except Exception as e:
                log.warning("WB API %s err: %s", ver, e)
            time.sleep(2)

        # --- Strategy B: Web API (www.wildberries.ru) ---
        if not wb_price_minor:
            try:
                web_api = f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={WB_NM}&curr=rub&dest=-1257786"
                log.info("WB web-api: %s", web_api)
                resp = cr.get(web_api, impersonate="chrome124", timeout=15,
                              headers={"Accept": "application/json, text/plain, */*",
                                       "Origin": "https://www.wildberries.ru",
                                       "Referer": "https://www.wildberries.ru/"})
                log.info("WB web-api: HTTP %d, %dB", resp.status_code, len(resp.content))
                if resp.status_code == 200:
                    data = resp.json()
                    d = data.get("data", data)
                    if isinstance(d, dict):
                        wb_title = d.get("name") or wb_title
                        wb_brand = d.get("brand") or wb_brand
                        prices = d.get("prices") or d.get("price")
                        if isinstance(prices, dict):
                            for k in ("basic", "product", "card", "wallet", "sale"):
                                if k in prices and prices[k] and prices[k] > 0:
                                    wb_price_minor = int(prices[k])
                                    break
                        if not wb_price_minor:
                            for k in ("salePriceU", "priceU"):
                                if d.get(k):
                                    wb_price_minor = int(d[k])
                                    break
                        qty = d.get("qty")
                        if qty is not None:
                            wb_availability = "IN_STOCK" if qty > 0 else "OUT_OF_STOCK"
                        log.info("WB web-api: title=%s price=%s", wb_title, wb_price_minor)
            except Exception as e:
                log.warning("WB web-api err: %s", e)
            time.sleep(2)

        # --- Strategy C: Cookie-session HTML (homepage → product) ---
        if not wb_title:
            log.info("WB C: cookie session HTML")
            try:
                s = cr.Session(impersonate="chrome124")
                # Warm-up: homepage
                h1 = s.get("https://www.wildberries.ru/", timeout=20,
                           headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("WB session home: HTTP %d, %dB, cookies=%d",
                         h1.status_code, len(h1.content), len(s.cookies))
                time.sleep(3)
                # Product page
                h2 = s.get(WB_URL, timeout=25,
                           headers={"Accept-Language": "ru-RU,ru;q=0.9",
                                    "Referer": "https://www.wildberries.ru/"})
                log.info("WB session product: HTTP %d, %dB", h2.status_code, len(h2.content))
                if h2.status_code == 200 and len(h2.content) > 5000:
                    html = h2.text
                    info = parse_jsonld(html)
                    if info:
                        wb_title = info.get("title") or wb_title
                        wb_price_minor = info.get("price_minor") or wb_price_minor
                        wb_availability = info.get("availability", wb_availability)
                        wb_brand = info.get("brand") or wb_brand
                        wb_model = info.get("model") or wb_model
                    if not wb_title:
                        wb_title = parse_og_title(html)
                    if not wb_price_minor:
                        for pat in [r'"salePriceU"\s*:\s*(\d+)', r'"priceU"\s*:\s*(\d+)',
                                    r'data-price="(\d+)"',
                                    r'og:price:amount["\'][^>]*content=["\']([\d.]+)']:
                            m = re.search(pat, html, re.I)
                            if m:
                                val = m.group(1)
                                wb_price_minor = int(round(float(val) * 100)) if "." in val else int(val)
                                break
                    if wb_availability == "UNKNOWN":
                        if re.search(r"\u0432 \u043d\u0430\u043b\u0438\u0447\u0438\u0438|\u043a\u0443\u043f\u0438\u0442\u044c", html, re.I):
                            wb_availability = "IN_STOCK"
                        elif re.search(r"\u043d\u0435\u0442 \u0432 \u043d\u0430\u043b\u0438\u0447\u0438\u0438|\u0437\u0430\u043a\u043e\u043d\u0447\u0438\u043b", html, re.I):
                            wb_availability = "OUT_OF_STOCK"
                    for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']', html, re.I):
                        if not re.search(r'\.(mp4|webm)', m.group(1), re.I):
                            wb_media.append({"type": "image", "url": m.group(1)})
            except Exception as e:
                log.warning("WB session err: %s", e)
            time.sleep(3)

        # --- Strategy D: Basket CDN (numbered, quick sample) ---
        if not wb_title or not wb_price_minor:
            log.info("WB D: basket CDN")
            basket_path = f"/vol{vol}/part{part}/{WB_NM}/info/ru/card.json"
            found = False
            for h in [1, 2, 3, 10, 20, 30, 40, 50, 55, 60]:
                if found:
                    break
                try:
                    card_url = f"https://basket-{h:02d}.wbbasket.ru{basket_path}"
                    resp = cr.get(card_url, impersonate="chrome124", timeout=6,
                                  headers={"Accept": "application/json"})
                    if resp.status_code == 200 and len(resp.content) > 100:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("nm_id"):
                            wb_title = data.get("imt_name") or wb_title
                            selling = data.get("selling", {})
                            if isinstance(selling, dict):
                                wb_brand = selling.get("brand_name") or wb_brand
                            for key in ("salePriceU", "priceU"):
                                if data.get(key):
                                    wb_price_minor = int(data[key])
                                    break
                            log.info("WB basket h=%02d: title=%s price=%s", h, wb_title, wb_price_minor)
                            found = True
                            break
                    else:
                        log.info("WB basket h=%02d: HTTP %d", h, resp.status_code)
                except Exception as e:
                    log.info("WB basket h=%02d: %s", h, str(e)[:60])
            if not found:
                log.info("WB D: basket CDN all failed")
            time.sleep(2)

        time.sleep(3)

    wb_media = wb_media[:30]

    if wb_title:
        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": wb_title, "method": "COLLECTOR",
                          "evidence": {"method": "multi", "source": "WB"}},
                "availability": {"status": "KNOWN", "value": wb_availability, "method": "COLLECTOR",
                                 "evidence": {"method": "api+html", "source": "WB"}},
            },
            "price_candidates": make_price_list(wb_price_minor, "wb_api"),
            "media": {"items": wb_media},
            "availability": wb_availability,
            "category_path": "catalog",
        }
        pr = persist(db, "WB", wb_used_id, wb_used_url, wb_title, obs_data, wb_brand, wb_model)
        log.info("WB PERSIST: %s", pr)
        results["WB"] = {"status": "OK", **pr, "price_minor": wb_price_minor,
                         "availability": wb_availability, "title": wb_title, "nm_id": wb_used_id}
    else:
        results["WB"] = {"status": "NO_DATA", "note": "no title from any strategy"}

except Exception as e:
    log.error("WB error: %s\n%s", e, traceback.format_exc())
    results["WB"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 3: AVITO (15s delays, edge, broader cats)
# ============================================================
log.info("=== 3. AVITO ===")
try:
    from curl_cffi import requests as cr

    AVITO_DELAY = 15
    live_url = None
    live_id = None
    live_html = None

    cat_urls = [
        ("https://m.avito.ru/krasnoyarsk/telefony", "m_telefony"),
        ("https://m.avito.ru/krasnoyarsk/noutbuki", "m_noutbuki"),
        ("https://m.avito.ru/krasnoyarsk/elektro/telefony", "m_elektro_telefony"),
        ("https://www.avito.ru/krasnoyarsk/noutbuki", "d_noutbuki"),
    ]

    for cat_url, label in cat_urls:
        if live_url:
            break
        try:
            log.info("AVITO cat %s: %s", label, cat_url)
            time.sleep(AVITO_DELAY)
            resp = cr.get(cat_url, impersonate="edge", timeout=25,
                          headers={"Accept-Language": "ru-RU,ru;q=0.9",
                                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
            log.info("AVITO cat %s: HTTP %d, %dB", label, resp.status_code, len(resp.content))
            if resp.status_code != 200:
                continue
            html = resp.text
            links = re.findall(r'href="([^"]*?/(\d{8,12})/)"', html)
            links += re.findall(r'href="(/ad/(\d{8,12}))"', html)
            log.info("AVITO cat %s: %d links", label, len(links))

            for link_path, lid in links[:4]:
                if live_url:
                    break
                ad_url = f"https://m.avito.ru/ad/{lid}"
                full_url = f"https://www.avito.ru{link_path}" if link_path.startswith("/") else link_path
                for try_url in [ad_url, full_url]:
                    try:
                        time.sleep(AVITO_DELAY)
                        r2 = cr.get(try_url, impersonate="edge", timeout=20,
                                    headers={"Accept-Language": "ru-RU,ru;q=0.9",
                                             "Referer": cat_url})
                        log.info("AVITO listing %s: HTTP %d, %dB", try_url, r2.status_code, len(r2.content))
                        if r2.status_code == 200 and len(r2.content) > 5000:
                            live_url = try_url
                            live_id = lid
                            live_html = r2.text
                            break
                    except Exception:
                        continue
        except Exception as e:
            log.warning("AVITO cat err: %s", e)

    # Phase 2: known IDs with edge
    if not live_url:
        log.info("AVITO: trying known IDs (edge)")
        for test_id in ["8341658309", "1154321987"]:
            for base in ["https://m.avito.ru", "https://www.avito.ru"]:
                test_url = f"{base}/ad/{test_id}"
                try:
                    time.sleep(AVITO_DELAY)
                    resp = cr.get(test_url, impersonate="edge", timeout=20,
                                  headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    log.info("AVITO known %s: HTTP %d, %dB", test_url, resp.status_code, len(resp.content))
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        live_url = test_url
                        live_id = test_id
                        live_html = resp.text
                        break
                except Exception:
                    continue
            if live_url:
                break

    if live_url and live_html:
        info = parse_jsonld(live_html) or {}
        title = info.get("title") or parse_og_title(live_html) or f"Avito {live_id}"
        price_minor = info.get("price_minor")
        availability = "IN_STOCK"
        brand = info.get("brand")
        model = info.get("model")

        if not price_minor:
            m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', live_html, re.DOTALL)
            if m:
                try:
                    state = json.loads(m.group(1))
                    s = json.dumps(state)
                    pm = re.search(r'"price"\s*:\s*(\d+)', s)
                    if pm:
                        price_minor = int(pm.group(1))
                    if not title or title.startswith("Avito "):
                        tm = re.search(r'"title"\s*:\s*"([^"]{5,200})"', s)
                        if tm:
                            title = tm.group(1)
                except (json.JSONDecodeError, ValueError):
                    pass

        if not price_minor:
            m = re.search(
                r'class="[^"]*(?:price|g-price)[^"]*"[^>]*>.*?(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:\u20bd|\u0440\u0443\u0431)',
                live_html, re.DOTALL | re.IGNORECASE
            )
            if m:
                try:
                    price_minor = int(round(float(m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                except (ValueError, TypeError):
                    pass

        media_items = []
        for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*avito[^"\']*\.(?:jpg|png|webp)[^"\']*)["\']', live_html, re.I):
            media_items.append({"type": "image", "url": m.group(1)})
        media_items = media_items[:30]

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "edge", "source": "AVITO"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page_load", "source": "AVITO"}},
            },
            "price_candidates": make_price_list(price_minor),
            "media": {"items": media_items},
            "availability": availability,
            "category_path": "krasnoyarsk",
        }
        pr = persist(db, "AVITO", live_id, live_url, title, obs_data, brand, model)
        log.info("AVITO PERSIST: %s", pr)
        results["AVITO"] = {"status": "OK", **pr, "price_minor": price_minor,
                            "availability": availability, "title": title, "url": live_url}
    else:
        results["AVITO"] = {"status": "NO_LIVE_LISTING", "note": "all attempts 429/439/404/timeout"}

except Exception as e:
    log.error("AVITO error: %s\n%s", e, traceback.format_exc())
    results["AVITO"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 4: OZON (cookie warm-up + edge + composer API)
# ============================================================
log.info("=== 4. OZON ===")
try:
    from curl_cffi import requests as cr

    OZON_ID = "721093013"
    ozon_urls = [
        f"https://www.ozon.ru/product/kardamon-tselyy-zelenyy-krupnye-otbornye-zerna-100-gr-{OZON_ID}/",
        f"https://www.ozon.ru/product/{OZON_ID}/",
        f"https://m.ozon.ru/product/{OZON_ID}/",
    ]

    ozon_html = None
    ozon_url_used = None
    ozon_impersonate = None

    # --- Strategy A: Cookie warm-up (homepage → product) ---
    log.info("OZON A: cookie warm-up")
    try:
        s = cr.Session(impersonate="chrome124")
        h1 = s.get("https://www.ozon.ru/", timeout=20,
                   headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("OZON home: HTTP %d, %dB, cookies=%d", h1.status_code, len(h1.content), len(s.cookies))
        time.sleep(4)
        for oz_url in ozon_urls:
            if ozon_html:
                break
            try:
                log.info("OZON session: %s", oz_url)
                resp = s.get(oz_url, timeout=25,
                             headers={"Accept-Language": "ru-RU,ru;q=0.9",
                                      "Referer": "https://www.ozon.ru/"})
                log.info("OZON session: HTTP %d, %dB", resp.status_code, len(resp.content))
                if resp.status_code == 200 and len(resp.content) > 5000:
                    if "captcha" not in resp.text[:3000].lower():
                        ozon_html = resp.text
                        ozon_url_used = oz_url
                        ozon_impersonate = "chrome124_session"
                        break
            except Exception as e:
                log.warning("OZON session err: %s", e)
            time.sleep(3)
    except Exception as e:
        log.warning("OZON session init err: %s", e)

    # --- Strategy B: edge impersonation ---
    if not ozon_html:
        log.info("OZON B: edge")
        for oz_url in ozon_urls:
            if ozon_html:
                break
            try:
                log.info("OZON edge: %s", oz_url)
                resp = cr.get(oz_url, impersonate="edge", timeout=25,
                              headers={"Accept-Language": "ru-RU,ru;q=0.9",
                                       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                       "Referer": "https://www.ozon.ru/"})
                log.info("OZON edge: HTTP %d, %dB", resp.status_code, len(resp.content))
                if resp.status_code == 200 and len(resp.content) > 5000:
                    if "captcha" not in resp.text[:3000].lower():
                        ozon_html = resp.text
                        ozon_url_used = oz_url
                        ozon_impersonate = "edge"
                        break
            except Exception as e:
                log.warning("OZON edge err: %s", e)
            time.sleep(3)

    # --- Strategy C: Composer API ---
    if not ozon_html:
        log.info("OZON C: composer API")
        try:
            composer_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"
            resp = cr.get(composer_url, impersonate="chrome124", timeout=20,
                          headers={"Accept": "application/json",
                                   "Origin": "https://www.ozon.ru",
                                   "Referer": f"https://www.ozon.ru/product/{OZON_ID}/"})
            log.info("OZON composer: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                data = resp.json()
                # Extract from composer JSON
                s = json.dumps(data)
                tm = re.search(r'"title"\s*:\s*"([^"]{5,200})"', s)
                pm = re.search(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', s)
                if tm or pm:
                    ozon_html = json.dumps(data)  # mark as JSON
                    ozon_url_used = composer_url
                    ozon_impersonate = "composer_api"
                    # Store parsed values for later use
                    ozon_composer_data = data
        except Exception as e:
            log.warning("OZON composer err: %s", e)

    # --- Strategy D: Mobile API ---
    if not ozon_html:
        log.info("OZON D: mobile API")
        try:
            mob_api = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"
            resp = cr.get(mob_api, impersonate="safari17_0", timeout=20,
                          headers={"Accept": "application/json",
                                   "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
            log.info("OZON mob-api: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                data = resp.json()
                s = json.dumps(data)
                tm = re.search(r'"title"\s*:\s*"([^"]{5,200})"', s)
                pm = re.search(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', s)
                if tm or pm:
                    ozon_html = json.dumps(data)
                    ozon_url_used = mob_api
                    ozon_impersonate = "mobile_api"
        except Exception as e:
            log.warning("OZON mob-api err: %s", e)

    # --- Parse results ---
    if ozon_html:
        if ozon_impersonate in ("composer_api", "mobile_api"):
            # Parse from JSON
            try:
                data = json.loads(ozon_html) if ozon_html.startswith("{") else {}
                s = json.dumps(data)
                title = None
                price_minor = None
                availability = "UNKNOWN"
                brand = None
                media_items = []

                tm = re.search(r'"title"\s*:\s*"([^"]{5,200})"', s)
                if tm:
                    title = tm.group(1)

                pm = re.search(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', s)
                if pm:
                    price_minor = int(pm.group(1))

                if not price_minor:
                    pm = re.search(r'"price"\s*:\s*(\d{4,})', s)
                    if pm:
                        price_minor = int(pm.group(1))

                am = re.search(r'"availability"\s*:\s*"([^"]+)"', s)
                if am:
                    a = am.group(1).upper()
                    if "IN" in a or "AVAIL" in a:
                        availability = "IN_STOCK"
                    elif "OUT" in a or "UNAVAIL" in a:
                        availability = "OUT_OF_STOCK"

                bm = re.search(r'"brand"\s*:\s*"([^"]{2,50})"', s)
                if bm:
                    brand = bm.group(1)

                for m in re.finditer(r'"(https://[^"]*ir\.ozone\.ru[^"]*)"', s):
                    media_items.append({"type": "image", "url": m.group(1)})
                for m in re.finditer(r'"(https://[^"]*ozoncdn\.net[^"]*)"', s):
                    media_items.append({"type": "image", "url": m.group(1)})
                media_items = media_items[:30]

                title = title or f"Ozon {OZON_ID}"
            except Exception as e:
                log.warning("OZON JSON parse err: %s", e)
                title = f"Ozon {OZON_ID}"
                price_minor = None
                availability = "UNKNOWN"
                media_items = []
        else:
            # Parse from HTML
            info = parse_jsonld(ozon_html) or {}
            title = info.get("title") or parse_og_title(ozon_html) or f"Ozon {OZON_ID}"
            price_minor = info.get("price_minor")
            availability = info.get("availability", "UNKNOWN")
            brand = info.get("brand")
            model = info.get("model")

            if not price_minor:
                m = re.search(r'data-widget="[^"]*price[^"]*"[^>]*>(.*?)</', ozon_html, re.DOTALL)
                if m:
                    pm = re.search(r'(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)', m.group(1))
                    if pm:
                        try:
                            price_minor = int(round(float(pm.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                        except (ValueError, TypeError):
                            pass

            if not price_minor:
                m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', ozon_html, re.I)
                if m:
                    try:
                        price_minor = int(round(float(m.group(1)) * 100))
                    except ValueError:
                        pass

            if not price_minor:
                m = re.search(r'"price"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', ozon_html)
                if m:
                    price_minor = int(m.group(1))

            media_items = []
            for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru[^"\']*)["\']', ozon_html, re.I):
                media_items.append({"type": "image", "url": m.group(1)})
            for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*ozoncdn\.net[^"\']*)["\']', ozon_html, re.I):
                media_items.append({"type": "image", "url": m.group(1)})
            media_items = media_items[:30]

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": ozon_impersonate, "source": "OZON"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page", "source": "OZON"}},
            },
            "price_candidates": make_price_list(price_minor),
            "media": {"items": media_items},
            "availability": availability,
            "category_path": "spices",
        }
        pr = persist(db, "OZON", OZON_ID, ozon_url_used, title, obs_data, brand, model)
        log.info("OZON PERSIST: %s", pr)
        results["OZON"] = {"status": "OK", **pr, "price_minor": price_minor,
                           "availability": availability, "title": title, "impersonate": ozon_impersonate}
    else:
        results["OZON"] = {"status": "ALL_FAILED", "note": "all strategies failed (session/edge/composer/mobile)"}

except Exception as e:
    log.error("OZON error: %s\n%s", e, traceback.format_exc())
    results["OZON"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 5: SUMMARY
# ============================================================
POST = db_counts(db)
log.info("POST: %s", POST)
log.info("DELTA: product=%d obs=%d pp=%d src=%d",
         POST["product"] - PRE["product"],
         POST["observation"] - PRE["observation"],
         POST["price_point"] - PRE["price_point"],
         POST["source"] - PRE["source"])

summary = {
    "version": "v84",
    "timestamp": NOW,
    "pre": PRE,
    "post": POST,
    "results": results,
    "overall": "OK" if all(r.get("status") == "OK" for r in results.values()) else "PARTIAL",
}

out_path = ROOT / "diag_v84_result.json"
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== V84 DONE: %s ===", summary["overall"])
for name, r in results.items():
    log.info("  %s: %s", name, r.get("status"))
