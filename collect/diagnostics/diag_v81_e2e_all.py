#!/usr/bin/env python3
"""V81 E2E: Farpost URL fix, WB safari17+content.json, Avito curl_cffi+mobile.

Ozon already works (safari17). Focus on the other three.
"""

import sys, os, json, time, hashlib, logging, traceback
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
                    format="%(asctime)s v81 %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("v81")

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
    """Extract Product from JSON-LD blocks."""
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
    import re
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


# ============================================================
# SECTION 0: DB INIT
# ============================================================
log.info("=== V81 E2E ALL ===")
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
# SECTION 1: FARPOST (ID 16879823067 from v48)
# ============================================================
log.info("=== 1. FARPOST ===")
try:
    import re
    from curl_cffi import requests as cr

    FP_ID = "16879823067"
    fp_urls = [
        f"https://www.farpost.ru/{FP_ID}",
        f"https://www.farpost.ru/krasnoyarsk/{FP_ID}",
        f"https://www.farpost.ru/krasnoyarsk/noutbuki/{FP_ID}",
        f"https://farpost.ru/{FP_ID}",
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

        # Fallback: HTML price
        if not price_minor:
            m = re.search(r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)', fp_html, re.DOTALL | re.IGNORECASE)
            if m:
                try:
                    price_minor = int(round(float(m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                except (ValueError, TypeError):
                    pass

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}",
                "amount_minor": price_minor, "currency": "RUB",
                "visible_label": f"{price_minor//100} ₽", "label": "page"
            })

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "safari17", "source": "FARPOST"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page", "source": "FARPOST"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": availability,
            "category_path": "krasnoyarsk",
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
# SECTION 2: WILDBERRIES (safari17 + content.json + basket)
# ============================================================
log.info("=== 2. WILDBERRIES ===")
try:
    import re
    from curl_cffi import requests as cr

    WB_NM = "199298871"
    WB_URL = f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx"

    wb_title = None
    wb_price_minor = None
    wb_availability = "UNKNOWN"
    wb_brand = None
    wb_model = None

    # Step 1: HTML via safari17 (worked for Ozon)
    log.info("WB 1: HTML safari17")
    try:
        resp = cr.get(WB_URL, impersonate="safari17_0", timeout=25,
                      headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("WB HTML safari17: HTTP %d, %dB", resp.status_code, len(resp.content))
        if resp.status_code == 200:
            html = resp.text
            # JSON-LD
            info = parse_jsonld(html)
            if info:
                wb_title = info.get("title")
                wb_price_minor = info.get("price_minor")
                wb_availability = info.get("availability", "UNKNOWN")
                wb_brand = info.get("brand")
                wb_model = info.get("model")
                log.info("WB JSON-LD: title=%s price=%s avail=%s", wb_title, wb_price_minor, wb_availability)
            # Inline state
            if not wb_title:
                m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
                if m:
                    try:
                        state = json.loads(m.group(1))
                        s = json.dumps(state)
                        tm = re.search(r'"name"\s*:\s*"([^"]{5,300})"', s)
                        if tm:
                            wb_title = tm.group(1)
                        pm = re.search(r'"salePriceU"\s*:\s*(\d+)', s)
                        if not wb_price_minor and pm:
                            wb_price_minor = int(pm.group(1))
                        pm2 = re.search(r'"priceU"\s*:\s*(\d+)', s)
                        if not wb_price_minor and pm2:
                            wb_price_minor = int(pm2.group(1))
                    except (json.JSONDecodeError, ValueError):
                        pass
            if not wb_title:
                wb_title = parse_og_title(html)
            # Price from HTML
            if not wb_price_minor:
                for pat in [r'"salePriceU"\s*:\s*(\d+)', r'"priceU"\s*:\s*(\d+)',
                            r'data-price="(\d+)"', r'"price"\s*:\s*(\d+)']:
                    m = re.search(pat, html)
                    if m:
                        wb_price_minor = int(m.group(1))
                        log.info("WB price pattern %s: %d", pat, wb_price_minor)
                        break
            # Availability
            if "в наличии" in html.lower() or "in stock" in html.lower():
                wb_availability = "IN_STOCK"
            elif "нет в наличии" in html.lower() or "закончил" in html.lower():
                wb_availability = "OUT_OF_STOCK"
    except Exception as e:
        log.warning("WB HTML err: %s", e)

    # Step 2: content.json (CDN)
    if not wb_price_minor:
        log.info("WB 2: content.json")
        try:
            content_url = f"https://wbx-content-v2.wbstatic.net/cards/{WB_NM}/content.json"
            resp = cr.get(content_url, impersonate="chrome124", timeout=15,
                          headers={"User-Agent": "Mozilla/5.0"})
            log.info("WB content.json: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                data = resp.json()
                wb_title = data.get("name") or wb_title
                wb_brand = data.get("brand") or wb_brand
                # Prices in content.json are under "cards" or top-level
                for key in ("salePriceU", "priceU", "price"):
                    if data.get(key):
                        wb_price_minor = int(data[key])
                        break
                if not wb_price_minor:
                    cards = data.get("cards", [])
                    if cards:
                        for key in ("salePriceU", "priceU"):
                            if cards[0].get(key):
                                wb_price_minor = int(cards[0][key])
                                break
                log.info("WB content: title=%s price=%s", wb_title, wb_price_minor)
        except Exception as e:
            log.warning("WB content err: %s", e)

    # Step 3: basket CDN (corrected vol/part)
    if not wb_title or not wb_price_minor:
        log.info("WB 3: basket CDN")
        try:
            nm_int = int(WB_NM)
            vol = nm_int // 100000
            part = (nm_int // 1000) % 100
            log.info("WB basket: vol=%d part=%d", vol, part)
            for h in range(10):
                card_url = f"https://basket-{h}.wbbasket.ru/vol{vol}/part{part}/{WB_NM}/cards.json"
                try:
                    resp = cr.get(card_url, impersonate="chrome124", timeout=8,
                                  headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200 and len(resp.content) > 50:
                        data = resp.json()
                        cards = data.get("data", {}).get("cards", [])
                        if cards:
                            c = cards[0]
                            wb_title = c.get("name") or wb_title
                            if isinstance(c.get("brand"), dict):
                                wb_brand = c["brand"].get("value")
                            wb_model = c.get("model") or wb_model
                            log.info("WB basket h=%d: title=%s", h, wb_title)
                            break
                except Exception:
                    continue
        except Exception as e:
            log.warning("WB basket err: %s", e)

    # Step 4: card.wb.ru API
    if not wb_price_minor:
        log.info("WB 4: card.wb.ru API")
        try:
            api_url = f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}"
            resp = cr.get(api_url, impersonate="safari17_0", timeout=15,
                          headers={"Accept": "application/json",
                                   "Origin": "https://www.wildberries.ru",
                                   "Referer": "https://www.wildberries.ru/"})
            log.info("WB card API: HTTP %d, %dB", resp.status_code, len(resp.content))
            if resp.status_code == 200:
                data = resp.json()
                products = data.get("data", {}).get("products", [])
                if products:
                    p = products[0]
                    wb_price_minor = p.get("salePriceU") or p.get("priceU")
                    if wb_price_minor:
                        wb_price_minor = int(wb_price_minor)
                    wb_title = p.get("name") or wb_title
                    wb_availability = "IN_STOCK" if p.get("stock", 0) > 0 else "OUT_OF_STOCK"
                    log.info("WB API: price=%s stock=%s", wb_price_minor, p.get("stock"))
        except Exception as e:
            log.warning("WB API err: %s", e)

    # Persist
    if wb_title:
        price_candidates = []
        if wb_price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{wb_price_minor//100}.{wb_price_minor%100:02d}",
                "amount_minor": wb_price_minor, "currency": "RUB",
                "visible_label": f"{wb_price_minor//100} ₽", "label": "wb"
            })
        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": wb_title, "method": "COLLECTOR",
                          "evidence": {"method": "safari17+cdn", "source": "WB"}},
                "availability": {"status": "KNOWN", "value": wb_availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page", "source": "WB"}},
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
        results["WB"] = {"status": "NO_DATA", "note": "no title from any strategy"}

except Exception as e:
    log.error("WB error: %s\n%s", e, traceback.format_exc())
    results["WB"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 3: AVITO (curl_cffi, mobile, delays)
# ============================================================
log.info("=== 3. AVITO ===")
try:
    import re
    from curl_cffi import requests as cr

    # Try mobile URLs first (less aggressive bot detection)
    avito_targets = [
        # Mobile listing pages
        ("https://m.avito.ru/krasnoyarsk/telefony", "cat_mobile"),
        ("https://www.avito.ru/krasnoyarsk/telefony", "cat_desktop"),
    ]

    live_url = None
    live_id = None
    live_html = None

    # Phase 1: find a live listing from category
    for cat_url, label in avito_targets:
        try:
            log.info("AVITO %s: %s", label, cat_url)
            time.sleep(3)
            resp = cr.get(cat_url, impersonate="safari17_0", timeout=25,
                          headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            log.info("AVITO %s: HTTP %d, %dB", label, resp.status_code, len(resp.content))
            if resp.status_code != 200:
                continue
            html = resp.text
            # Find listing links
            links = re.findall(r'href="([^"]*?/(\d{8,12})/)"', html)
            log.info("AVITO %s: %d links", label, len(links))
            for link_path, lid in links[:8]:
                if not link_path.startswith("/"):
                    full_url = f"https://www.avito.ru{link_path}"
                else:
                    full_url = f"https://www.avito.ru{link_path}"
                # Try mobile version
                mobile_url = full_url.replace("https://www.avito.ru", "https://m.avito.ru")
                for try_url in [mobile_url, full_url]:
                    try:
                        time.sleep(2)
                        r2 = cr.get(try_url, impersonate="safari17_0", timeout=20,
                                    headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                        log.info("AVITO listing %s: HTTP %d, %dB", try_url, r2.status_code, len(r2.content))
                        if r2.status_code == 200 and len(r2.content) > 5000:
                            live_url = try_url
                            live_id = lid
                            live_html = r2.text
                            break
                    except Exception:
                        continue
                if live_url:
                    break
            if live_url:
                break
        except Exception as e:
            log.warning("AVITO cat err: %s", e)

    # Phase 2: fallback known IDs via mobile
    if not live_url:
        log.info("AVITO: trying known IDs via mobile")
        for test_id in ["8341658309", "1154321987"]:
            for base in ["https://m.avito.ru", "https://www.avito.ru"]:
                test_url = f"{base}/krasnoyarsk/{test_id}"
                try:
                    time.sleep(3)
                    resp = cr.get(test_url, impersonate="safari17_0", timeout=20,
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
        availability = "IN_STOCK"  # page loaded = active
        brand = info.get("brand")
        model = info.get("model")

        # __PRELOADED_STATE__
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

        # HTML price
        if not price_minor:
            m = re.search(r'class="[^"]*(?:price|g-price)[^"]*"[^>]*>.*?(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)', live_html, re.DOTALL | re.IGNORECASE)
            if m:
                try:
                    price_minor = int(round(float(m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                except (ValueError, TypeError):
                    pass

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}",
                "amount_minor": price_minor, "currency": "RUB",
                "visible_label": f"{price_minor//100} ₽", "label": "page"
            })

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "safari17_mobile", "source": "AVITO"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page_load", "source": "AVITO"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": availability,
            "category_path": "krasnoyarsk",
        }
        pr = persist(db, "AVITO", live_id, live_url, title, obs_data, brand, model)
        log.info("AVITO PERSIST: %s", pr)
        results["AVITO"] = {"status": "OK", **pr, "price_minor": price_minor,
                            "availability": availability, "title": title, "url": live_url}
    else:
        results["AVITO"] = {"status": "NO_LIVE_LISTING", "note": "all attempts 429/439/timeout"}

except Exception as e:
    log.error("AVITO error: %s\n%s", e, traceback.format_exc())
    results["AVITO"] = {"status": "ERROR", "error": str(e)}

# ============================================================
# SECTION 4: OZON (keep working safari17)
# ============================================================
log.info("=== 4. OZON ===")
try:
    import re
    from curl_cffi import requests as cr

    OZON_ID = "479010389"
    OZON_URL = f"https://www.ozon.ru/product/{OZON_ID}/"

    resp = cr.get(OZON_URL, impersonate="safari17_0", timeout=25,
                  headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("OZON safari17: HTTP %d, %dB", resp.status_code, len(resp.content))

    if resp.status_code == 200:
        html = resp.text
        info = parse_jsonld(html) or {}
        title = info.get("title") or parse_og_title(html) or f"Ozon {OZON_ID}"
        price_minor = info.get("price_minor")
        availability = info.get("availability", "UNKNOWN")
        brand = info.get("brand")
        model = info.get("model")

        # data-widget price
        if not price_minor:
            m = re.search(r'data-widget="[^"]*price[^"]*"[^>]*>(.*?)</', html, re.DOTALL)
            if m:
                pm = re.search(r'(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)', m.group(1))
                if pm:
                    try:
                        price_minor = int(round(float(pm.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")) * 100))
                    except (ValueError, TypeError):
                        pass

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}",
                "amount_minor": price_minor, "currency": "RUB",
                "visible_label": f"{price_minor//100} ₽", "label": "page"
            })

        obs_data = {
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                          "evidence": {"method": "safari17", "source": "OZON"}},
                "availability": {"status": "KNOWN", "value": availability, "method": "COLLECTOR",
                                 "evidence": {"method": "page", "source": "OZON"}},
            },
            "price_candidates": price_candidates,
            "media": {"items": []},
            "availability": availability,
            "category_path": "pripravy",
        }
        pr = persist(db, "OZON", OZON_ID, OZON_URL, title, obs_data, brand, model)
        log.info("OZON PERSIST: %s", pr)
        results["OZON"] = {"status": "OK", **pr, "price_minor": price_minor,
                           "availability": availability, "title": title}
    else:
        results["OZON"] = {"status": "HTTP_ERROR", "code": resp.status_code}

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
    "version": "v81",
    "timestamp": NOW,
    "pre": PRE,
    "post": POST,
    "results": results,
    "overall": "OK" if all(r.get("status") == "OK" for r in results.values()) else "PARTIAL",
}

out_path = ROOT / "diag_v81_result.json"
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== V81 DONE: %s ===", summary["overall"])
log.info("Result: %s", out_path)
