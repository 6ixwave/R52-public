#!/usr/bin/env python3
"""V80 E2E: corrected imports, per-marketplace isolation, new strategies.

Fixes from v79:
- migrate(db) instead of apply_migrations
- store functions instead of ProductStore class
- Ozon: chrome124, short URL, composer API v2
- WB: basket options/1.json, HTML inline price
- Avito: category page → live listing → parse
"""

import sys, os, json, time, hashlib, logging, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from r52.db.database import Database
from r52.db.migrate import migrate, schema_version
from r52.store.products import (
    ensure_source, upsert_product, add_observation, refresh_summary
)
from r52.domain.normalize import fingerprint as norm_fingerprint, identity_from_url
from r52.timeutil import iso, utc_now

DB_PATH = ROOT / "r52" / "data" / "r52.db"
NOW = iso(utc_now())
TZ = timezone(timedelta(hours=10))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s v80 %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("v80")

results = {}


def db_counts(db):
    with db.read() as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("product", "observation", "price_point", "source")}


def persist(db, source, listing_id, url, title, obs_data, brand=None, model=None):
    """Persist one observation through store functions. Returns dict."""
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


# ============================================================
# SECTION 0: DB INIT
# ============================================================
log.info("=== V80 E2E ALL ===")
db = Database(DB_PATH)
db.initialize()

try:
    mig = migrate(db)
    log.info("DB migrate: %s", mig)
except Exception as e:
    log.warning("DB migrate: %s", e)

log.info("schema_version: %s", schema_version(db))
PRE = db_counts(db)
log.info("PRE: %s", PRE)

# ============================================================
# SECTION 1: FARPOST
# ============================================================
log.info("=== 1. FARPOST ===")
try:
    import requests as req

    FP_URL = "https://www.farpost.ru/krasnoyarsk/tovary/130693618"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    resp = req.get(FP_URL, headers=headers, timeout=20)
    log.info("FARPOST: HTTP %d, %dB", resp.status_code, len(resp.content))

    if resp.status_code == 200:
        html = resp.text
        title = "Farpost 130693618"
        price_minor = None
        availability = "UNKNOWN"
        brand = None
        model = None

        # JSON-LD
        import re
        for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
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
                        title = item.get("name", title)
                        brand = item.get("brand", {}).get("name") if isinstance(item.get("brand"), dict) else item.get("brand")
                        model = item.get("mpn") or item.get("sku")
                        offers = item.get("offers")
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            price_val = offers.get("price") or offers.get("lowPrice")
                            if price_val:
                                try:
                                    price_minor = int(round(float(str(price_val).replace(",", ".")) * 100))
                                except (ValueError, TypeError):
                                    pass
                            avail = str(offers.get("availability", "")).upper()
                            if "IN_STOCK" in avail or "AVAIL" in avail:
                                availability = "IN_STOCK"
                            elif "OUT" in avail or "UNAVAIL" in avail:
                                availability = "OUT_OF_STOCK"
                        break
            except (json.JSONDecodeError, ValueError):
                continue

        # Inline JS: baza.lightbox.init
        if price_minor is None:
            m = re.search(r'baza\.lightbox\.init\s*\((\[.*?\])\)', html, re.DOTALL)
            if m:
                try:
                    items = json.loads(m.group(1))
                    if items:
                        first = items[0]
                        price_minor = first.get("price")
                        if price_minor:
                            price_minor = int(round(float(price_minor) * 100))
                except (json.JSONDecodeError, ValueError):
                    pass

        # HTML price
        if price_minor is None:
            m = re.search(r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)', html, re.DOTALL | re.IGNORECASE)
            if m:
                try:
                    price_minor = int(round(float(m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                except (ValueError, TypeError):
                    pass

        # OG meta
        if not title or title.startswith("Farpost"):
            m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.IGNORECASE)
            if m:
                title = m.group(1)

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}",
                "amount_minor": price_minor, "currency": "RUB",
                "visible_label": f"{price_minor//100} ₽", "label": "page_price"
            })

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "JSON-LD+HTML", "source": "FARPOST"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "JSON-LD", "source": "FARPOST"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": availability,
            "category_path": "krasnoyarsk/tovary",
        }

        pr = persist(db, "FARPOST", "130693618", FP_URL, title, obs_data, brand, model)
        log.info("FARPOST PERSIST: %s", pr)
        results["FARPOST"] = {"status": "OK", **pr, "price_minor": price_minor, "availability": availability}
    else:
        results["FARPOST"] = {"status": "HTTP_ERROR", "code": resp.status_code}

except Exception as e:
    log.error("FARPOST error: %s\n%s", e, traceback.format_exc())
    results["FARPOST"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 2: OZON
# ============================================================
log.info("=== 2. OZON ===")
try:
    OZON_ID = "479010389"
    OZON_URL = f"https://www.ozon.ru/product/kardamon-zelenyy-molotyy-priprava-100-g-universalnye-pripravy-i-spetsii-{OZON_ID}/"
    OZON_SHORT = f"https://www.ozon.ru/product/{OZON_ID}/"

    ozon_html = None
    ozon_strategy = None

    # Strategy A: curl_cffi chrome124
    try:
        from curl_cffi import requests as cr
        log.info("OZON A: curl_cffi chrome124, short URL")
        resp = cr.get(OZON_SHORT, impersonate="chrome124",
                      headers={"Accept-Language": "ru-RU,ru;q=0.9"}, timeout=20)
        log.info("OZON A short: HTTP %d, %dB", resp.status_code, len(resp.content))
        if resp.status_code == 200:
            ozon_html = resp.text
            ozon_strategy = "curl_cffi_chrome124_short"
        else:
            # Try full URL
            resp2 = cr.get(OZON_URL, impersonate="chrome124",
                           headers={"Accept-Language": "ru-RU,ru;q=0.9"}, timeout=20)
            log.info("OZON A full: HTTP %d, %dB", resp2.status_code, len(resp2.content))
            if resp2.status_code == 200:
                ozon_html = resp2.text
                ozon_strategy = "curl_cffi_chrome124_full"
    except Exception as e:
        log.warning("OZON A error: %s", e)

    # Strategy B: curl_cffi safari17
    if not ozon_html:
        try:
            from curl_cffi import requests as cr
            log.info("OZON B: curl_cffi safari17_0")
            resp = cr.get(OZON_URL, impersonate="safari17_0",
                          headers={"Accept-Language": "ru-RU,ru;q=0.9"}, timeout=20)
            log.info("OZON B: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                ozon_html = resp.text
                ozon_strategy = "curl_cffi_safari17"
        except Exception as e:
            log.warning("OZON B error: %s", e)

    # Strategy C: composer API v2
    if not ozon_html:
        try:
            import requests as req
            log.info("OZON C: composer API v2")
            api_url = "https://api.ozon.ru/composer-api.bx/page/json/v2"
            params = {
                "url": f"/product/{OZON_ID}/",
                "widgetStates": json.dumps([
                    {"name": "product-info", "state": {}},
                    {"name": "price", "state": {}},
                ]),
            }
            hdrs = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.ozon.ru/",
            }
            resp = req.get(api_url, params=params, headers=hdrs, timeout=20)
            log.info("OZON C: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                data = resp.json()
                ozon_html = json.dumps(data, ensure_ascii=False)
                ozon_strategy = "composer_api_v2"
        except Exception as e:
            log.warning("OZON C error: %s", e)

    # Strategy D: plain requests with minimal headers
    if not ozon_html:
        try:
            import requests as req
            log.info("OZON D: plain requests minimal headers")
            resp = req.get(OZON_URL, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
                "Accept-Language": "ru",
            }, timeout=20, allow_redirects=True)
            log.info("OZON D: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                ozon_html = resp.text
                ozon_strategy = "plain_requests_minimal"
        except Exception as e:
            log.warning("OZON D error: %s", e)

    # Parse if we got HTML
    if ozon_html:
        import re
        title = f"Ozon {OZON_ID}"
        price_minor = None
        availability = "UNKNOWN"
        brand = None
        model = None

        # JSON-LD
        for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', ozon_html, re.DOTALL):
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
                        title = item.get("name", title)
                        if isinstance(item.get("brand"), dict):
                            brand = item["brand"].get("name")
                        model = item.get("mpn") or item.get("sku")
                        offers = item.get("offers")
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            pv = offers.get("price") or offers.get("lowPrice")
                            if pv:
                                try:
                                    price_minor = int(round(float(str(pv).replace(",", ".")) * 100))
                                except (ValueError, TypeError):
                                    pass
                            avail = str(offers.get("availability", "")).upper()
                            if "IN_STOCK" in avail or "AVAIL" in avail:
                                availability = "IN_STOCK"
                        break
            except (json.JSONDecodeError, ValueError):
                continue

        # data-widget SSR
        if price_minor is None:
            m = re.search(r'data-widget="[^"]*price[^"]*"[^>]*>(.*?)</', ozon_html, re.DOTALL)
            if m:
                pm = re.search(r'(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)', m.group(1))
                if pm:
                    try:
                        price_minor = int(round(float(pm.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                    except (ValueError, TypeError):
                        pass

        # og:meta
        if not title or title.startswith("Ozon "):
            m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', ozon_html, re.IGNORECASE)
            if m:
                title = m.group(1)

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}",
                "amount_minor": price_minor, "currency": "RUB",
                "visible_label": f"{price_minor//100} ₽", "label": "page_price"
            })

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": ozon_strategy, "source": "OZON"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": ozon_strategy, "source": "OZON"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": availability,
            "category_path": "pripravy",
        }
        pr = persist(db, "OZON", OZON_ID, OZON_URL, title, obs_data, brand, model)
        log.info("OZON PERSIST: %s (strategy=%s)", pr, ozon_strategy)
        results["OZON"] = {"status": "OK", **pr, "price_minor": price_minor,
                           "availability": availability, "strategy": ozon_strategy}
    else:
        results["OZON"] = {"status": "ALL_403", "note": "all strategies returned 403"}

except Exception as e:
    log.error("OZON error: %s\n%s", e, traceback.format_exc())
    results["OZON"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 3: WILDBERRIES
# ============================================================
log.info("=== 3. WILDBERRIES ===")
try:
    WB_NM = "199298871"
    WB_URL = f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx"

    wb_title = None
    wb_price_minor = None
    wb_availability = "UNKNOWN"
    wb_brand = None
    wb_model = None
    wb_data = {}

    # Step 1: basket CDN card.json
    log.info("WB 1: basket CDN card.json")
    try:
        import requests as req
        vol = int(WB_NM) // 100000
        part = (int(WB_NM) // 1000) % 100
        for h in range(10):
            card_url = f"https://basket-{h}.wbbasket.ru/vol{vol}/part{part}/{WB_NM}/cards.json"
            try:
                resp = req.get(card_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200 and len(resp.content) > 100:
                    data = resp.json()
                    cards = data.get("data", {}).get("cards", [])
                    if cards:
                        c = cards[0]
                        wb_title = c.get("name")
                        wb_brand = c.get("brand", {}).get("value") if isinstance(c.get("brand"), dict) else c.get("brand")
                        wb_model = c.get("model")
                        wb_data = c
                        log.info("WB basket h=%d: OK, title=%s", h, wb_title)
                        break
            except Exception:
                continue
        else:
            log.info("WB basket: all h tried, no card")
    except Exception as e:
        log.warning("WB basket error: %s", e)

    # Step 2: price via basket options/1.json
    log.info("WB 2: basket options/1.json")
    if not wb_price_minor:
        try:
            import requests as req
            vol = int(WB_NM) // 100000
            part = (int(WB_NM) // 1000) % 100
            for h in range(10):
                opt_url = f"https://basket-{h}.wbbasket.ru/vol{vol}/part{part}/{WB_NM}/options/1.json"
                try:
                    resp = req.get(opt_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        data = resp.json()
                        prices = data.get("data", {}).get("prices", [])
                        if prices:
                            p = prices[0]
                            wb_price_minor = p.get("basic") or p.get("price")
                            if wb_price_minor:
                                wb_price_minor = int(wb_price_minor)
                            log.info("WB options h=%d: price=%s", h, wb_price_minor)
                            break
                except Exception:
                    continue
        except Exception as e:
            log.warning("WB options error: %s", e)

    # Step 3: HTML page for price/availability
    log.info("WB 3: HTML page")
    try:
        from curl_cffi import requests as cr
        resp = cr.get(WB_URL, impersonate="chrome124", timeout=25,
                      headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("WB HTML: HTTP %d, %dB", resp.status_code, len(resp.content))
        if resp.status_code == 200:
            html = resp.text
            import re
            if not wb_title:
                m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                if m:
                    wb_title = m.group(1).strip().replace(" - Wildberries", "").replace(" - \u0412\u0430\u0439\u043b\u0430\u0431\u0435\u0440\u0440\u0438\u0435", "")
            # Inline price
            if not wb_price_minor:
                m = re.search(r'"price"\s*:\s*(\d+)', html)
                if m:
                    wb_price_minor = int(m.group(1))
                    log.info("WB HTML inline price: %d", wb_price_minor)
            if not wb_price_minor:
                m = re.search(r'data-price="(\d+)"', html)
                if m:
                    wb_price_minor = int(m.group(1))
            # Availability
            if "в наличии" in html.lower() or "in stock" in html.lower():
                wb_availability = "IN_STOCK"
            elif "нет в наличии" in html.lower() or "закончил" in html.lower():
                wb_availability = "OUT_OF_STOCK"
    except Exception as e:
        log.warning("WB HTML error: %s", e)

    # Step 4: card.wb.ru API
    if not wb_price_minor:
        log.info("WB 4: card.wb.ru API")
        try:
            from curl_cffi import requests as cr
            for api_ver in ["v2", "v1"]:
                api_url = f"https://card.wb.ru/cards/{api_ver}/detail?nm={WB_NM}"
                resp = cr.get(api_url, impersonate="chrome124", timeout=15,
                              headers={"Accept": "application/json", "Origin": "https://www.wildberries.ru",
                                       "Referer": "https://www.wildberries.ru/"})
                log.info("WB card %s: HTTP %d, %dB", api_ver, resp.status_code, len(resp.content))
                if resp.status_code == 200:
                    data = resp.json()
                    products = data.get("data", {}).get("products", [])
                    if products:
                        p = products[0]
                        wb_price_minor = p.get("salePriceU") or p.get("priceU")
                        if wb_price_minor:
                            wb_price_minor = int(wb_price_minor)
                        if not wb_title:
                            wb_title = p.get("name")
                        wb_availability = "IN_STOCK" if p.get("stock", 0) > 0 else "OUT_OF_STOCK"
                        break
        except Exception as e:
            log.warning("WB card API error: %s", e)

    # Persist
    if wb_title:
        price_candidates = []
        if wb_price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{wb_price_minor//100}.{wb_price_minor%100:02d}",
                "amount_minor": wb_price_minor, "currency": "RUB",
                "visible_label": f"{wb_price_minor//100} ₽", "label": "basket+html"
            })
        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": wb_title, "method": "COLLECTOR",
                          "evidence": {"method": "basket_cdn+html", "source": "WB"}},
                "availability": {"status": "KNOWN", "value": wb_availability, "method": "COLLECTOR",
                                 "evidence": {"method": "basket_cdn", "source": "WB"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": wb_availability,
            "category_path": "catalog",
        }
        pr = persist(db, "WB", WB_NM, WB_URL, wb_title, obs_data, wb_brand, wb_model)
        log.info("WB PERSIST: %s", pr)
        results["WB"] = {"status": "OK", **pr, "price_minor": wb_price_minor,
                         "availability": wb_availability, "title": wb_title}
    else:
        results["WB"] = {"status": "NO_DATA", "note": "no title obtained"}

except Exception as e:
    log.error("WB error: %s\n%s", e, traceback.format_exc())
    results["WB"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 4: AVITO
# ============================================================
log.info("=== 4. AVITO ===")
try:
    # Strategy: find live listing from category page, then parse it
    import requests as req
    import re

    avito_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    # Try category pages to find live listings
    cat_urls = [
        "https://www.avito.ru/krasnoyarsk/telefony",
        "https://www.avito.ru/krasnoyarsk/noutbuki",
        "https://www.avito.ru/krasnoyarsk/avtozapchasti",
    ]
    live_listing_url = None
    live_listing_id = None

    for cat_url in cat_urls:
        try:
            log.info("AVITO cat: %s", cat_url)
            resp = req.get(cat_url, headers=avito_headers, timeout=20)
            log.info("AVITO cat: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code != 200:
                continue
            html = resp.text
            # Find listing links: /krasnoyarsk/...-ID/ or /krasnoyarsk/..._ID/
            links = re.findall(r'href="(/krasnoyarsk/[^"]*?_(\d{8,})/)"', html)
            if not links:
                links = re.findall(r'href="(/krasnoyarsk/[^"]*?/(\d{8,})/)"', html)
            log.info("AVITO cat: found %d links", len(links))
            for link_path, lid in links[:5]:
                full_url = f"https://www.avito.ru{link_path}"
                log.info("AVITO trying: %s (id=%s)", full_url, lid)
                try:
                    r2 = req.get(full_url, headers=avito_headers, timeout=20)
                    log.info("AVITO listing: HTTP %d, %dB", r2.status_code, len(r2.content))
                    if r2.status_code == 200 and len(r2.content) > 5000:
                        # Check it's not a redirect page
                        if "redirect" not in r2.text[:2000].lower() or "og:title" in r2.text:
                            live_listing_url = full_url
                            live_listing_id = lid
                            break
                except Exception:
                    continue
            if live_listing_url:
                break
        except Exception as e:
            log.warning("AVITO cat error: %s", e)

    # Fallback: try known ID from previous diagnostics
    if not live_listing_url:
        log.info("AVITO: trying known IDs")
        for test_id in ["8341658309", "1154321987", "1045236789"]:
            test_url = f"https://www.avito.ru/krasnoyarsk/{test_id}"
            try:
                resp = req.get(test_url, headers=avito_headers, timeout=20)
                log.info("AVITO known %s: HTTP %d, %dB", test_id, resp.status_code, len(resp.content))
                if resp.status_code == 200 and len(resp.content) > 5000:
                    live_listing_url = test_url
                    live_listing_id = test_id
                    break
            except Exception:
                continue

    if live_listing_url:
        # Parse the listing
        log.info("AVITO parsing: %s", live_listing_url)
        resp = req.get(live_listing_url, headers=avito_headers, timeout=25)
        html = resp.text

        title = f"Avito {live_listing_id}"
        price_minor = None
        availability = "IN_STOCK"  # if page loads, likely active
        brand = None
        model = None

        # JSON-LD
        for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(block.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("@type", "")
                    if isinstance(t, list):
                        t = " ".join(t)
                    if "Product" in t or "Offer" in t:
                        title = item.get("name", title)
                        offers = item.get("offers")
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            pv = offers.get("price")
                            if pv:
                                try:
                                    price_minor = int(round(float(str(pv).replace(",", ".")) * 100))
                                except (ValueError, TypeError):
                                    pass
                        break
            except (json.JSONDecodeError, ValueError):
                continue

        # __PRELOADED_STATE__
        if not price_minor:
            m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
            if m:
                try:
                    state = json.loads(m.group(1))
                    # Navigate to find price
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

        # HTML price
        if not price_minor:
            m = re.search(r'class="[^"]*(?:price|g-price)[^"]*"[^>]*>.*?(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)', html, re.DOTALL | re.IGNORECASE)
            if m:
                try:
                    price_minor = int(round(float(m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                except (ValueError, TypeError):
                    pass

        # OG
        if not title or title.startswith("Avito "):
            m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.IGNORECASE)
            if m:
                title = m.group(1)

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}",
                "amount_minor": price_minor, "currency": "RUB",
                "visible_label": f"{price_minor//100} ₽", "label": "page_price"
            })

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "category+listing", "source": "AVITO"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page_load", "source": "AVITO"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": availability,
            "category_path": "krasnoyarsk",
        }
        pr = persist(db, "AVITO", live_listing_id, live_listing_url, title, obs_data, brand, model)
        log.info("AVITO PERSIST: %s", pr)
        results["AVITO"] = {"status": "OK", **pr, "price_minor": price_minor,
                            "availability": availability, "title": title, "url": live_listing_url}
    else:
        results["AVITO"] = {"status": "NO_LIVE_LISTING", "note": "no live listing found"}

except Exception as e:
    log.error("AVITO error: %s\n%s", e, traceback.format_exc())
    results["AVITO"] = {"status": "ERROR", "error": str(e)}

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
    "version": "v80",
    "timestamp": NOW,
    "pre": PRE,
    "post": POST,
    "results": results,
    "overall": "OK" if all(r.get("status") == "OK" for r in results.values()) else "PARTIAL",
}

out_path = ROOT / "diag_v80_result.json"
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== V80 DONE: %s ===", summary["overall"])
log.info("Result: %s", out_path)
