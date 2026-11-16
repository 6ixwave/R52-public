#!/usr/bin/env python3
"""v79: Focused E2E — fix Ozon (SSL/403), WB (price), Avito (rate-limit), verify Farpost.

Strategy:
- OZON: try requests lib (bypass curl_cffi SSL), chrome120 impersonation, composer API via requests
- WB: plain requests to card.wb.ru (TLS fingerprint), static-basket price.json, card.json price fields
- AVITO: curl_cffi chrome, 20s+ delays, city page → extract ad IDs → fetch ad
- FARPOST: quick re-verify (was OK in v78)

Output: diag_v79_result.json + diag_v79_out.txt
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("v79")

DB_PATH = BASE / "r52" / "data" / "r52.db"
RESULT_PATH = BASE / "diag_v79_result.json"

# ============================================================
# DB HELPERS
# ============================================================

def db_init():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # Apply migrations
    try:
        from r52.db.migrate import apply_migrations
        applied = apply_migrations(str(DB_PATH))
        ver = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or 0
        log.info("DB: %s, applied=%s, version=%s", DB_PATH.name, applied, ver)
    except Exception as e:
        log.warning("DB migrate: %s", e)
        ver = 0
    return conn

def db_counts(conn):
    r = {}
    for t in ("product", "observation", "price_point", "source"):
        r[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return r

def db_products(conn):
    rows = conn.execute(
        "SELECT id, source, listing_id, url, title, last_lowest_minor, last_availability, observation_count, last_seen_at "
        "FROM product ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]

def db_price_points(conn):
    rows = conn.execute(
        "SELECT id, product_id, role, amount_minor, currency, label, created_at FROM price_point ORDER BY id DESC LIMIT 10"
    ).fetchall()
    return [dict(r) for r in rows]

def db_observations_recent(conn, limit=8):
    rows = conn.execute(
        "SELECT id, product_id, source, observed_at, status FROM observation ORDER BY id DESC LIMIT ?"
    ).fetchall() if True else []
    return [dict(r) for r in rows]

# ============================================================
# OZON
# ============================================================

def try_ozon():
    """Multi-strategy Ozon fetch. Returns dict with status, data, errors."""
    import requests as req
    from curl_cffi import requests as cffi_req

    OZON_IDS = ["479010389", "1600562267"]
    results = {}
    errors = []

    # --- Strategy 1: plain requests with full browser headers ---
    log.info("OZON S1: plain requests, full headers")
    headers_full = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    for oid in OZON_IDS:
        url = f"https://www.ozon.ru/product/{oid}/"
        try:
            time.sleep(3)
            r = req.get(url, headers=headers_full, timeout=20, allow_redirects=True)
            log.info("OZON S1 %s: HTTP %d, %dB", oid, r.status_code, len(r.content))
            if r.status_code == 200 and len(r.text) > 5000:
                results[oid] = {"strategy": "requests_full", "html": r.text, "status": 200, "bytes": len(r.content)}
                break
            elif r.status_code == 403:
                errors.append(f"S1 {oid}: 403 ({len(r.content)}B)")
                # Save first 403 for debug
                dbg = BASE / f"diag_v79_ozon_403_{oid}.html"
                dbg.write_text(r.text[:5000], encoding="utf-8")
                log.info("OZON S1 %s: saved 403 body to %s", oid, dbg.name)
            else:
                errors.append(f"S1 {oid}: HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"S1 {oid}: {e}")

    # --- Strategy 2: curl_cffi chrome120 ---
    if not results:
        log.info("OZON S2: curl_cffi chrome120")
        for oid in OZON_IDS:
            url = f"https://www.ozon.ru/product/{oid}/"
            try:
                time.sleep(3)
                r = cffi_req.get(url, impersonate="chrome120", timeout=20,
                                 headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("OZON S2 %s: HTTP %d, %dB", oid, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.text) > 5000:
                    results[oid] = {"strategy": "cffi_c120", "html": r.text, "status": 200, "bytes": len(r.content)}
                    break
                else:
                    errors.append(f"S2 {oid}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"S2 {oid}: {e}")

    # --- Strategy 3: curl_cffi firefox ---
    if not results:
        log.info("OZON S3: curl_cffi firefox")
        for oid in OZON_IDS:
            url = f"https://www.ozon.ru/product/{oid}/"
            try:
                time.sleep(3)
                r = cffi_req.get(url, impersonate="firefox", timeout=20,
                                 headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("OZON S3 %s: HTTP %d, %dB", oid, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.text) > 5000:
                    results[oid] = {"strategy": "cffi_firefox", "html": r.text, "status": 200, "bytes": len(r.content)}
                    break
                else:
                    errors.append(f"S3 {oid}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"S3 {oid}: {e}")

    # --- Strategy 4: Composer API via requests (bypass SSL issue) ---
    if not results:
        log.info("OZON S4: composer API via requests")
        for oid in OZON_IDS:
            page_url = f"https://www.ozon.ru/product/{oid}/"
            api_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url={page_url}"
            try:
                time.sleep(3)
                r = req.get(api_url, headers={
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Referer": page_url,
                }, timeout=20)
                log.info("OZON S4 %s: HTTP %d, %dB", oid, r.status_code, len(r.content))
                if r.status_code == 200:
                    data = r.json()
                    results[oid] = {"strategy": "composer_api", "json": data, "status": 200, "bytes": len(r.content)}
                    break
                else:
                    errors.append(f"S4 {oid}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"S4 {oid}: {e}")

    # --- Strategy 5: Mobile URL via requests ---
    if not results:
        log.info("OZON S5: mobile URL")
        for oid in OZON_IDS:
            url = f"https://m.ozon.ru/product/{oid}/"
            try:
                time.sleep(3)
                r = req.get(url, headers={
                    **headers_full,
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    "sec-ch-ua-mobile": "?1",
                }, timeout=20)
                log.info("OZON S5 %s: HTTP %d, %dB", oid, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.text) > 3000:
                    results[oid] = {"strategy": "mobile", "html": r.text, "status": 200, "bytes": len(r.content)}
                    break
                else:
                    errors.append(f"S5 {oid}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"S5 {oid}: {e}")

    # --- Parse whatever we got ---
    parsed = None
    for oid, data in results.items():
        if "html" in data:
            parsed = parse_ozon_html(data["html"], oid)
        elif "json" in data:
            parsed = parse_ozon_composer(data["json"], oid)
        if parsed:
            break

    if parsed:
        return {"status": "OK", "parsed": parsed, "strategy": list(results.keys())[0] if results else "?",
                "errors": errors}
    return {"status": "ERROR", "error": "all strategies failed", "errors": errors}


def parse_ozon_html(html, oid):
    """Extract fields from Ozon SSR HTML."""
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector.__new__(OzonCollector)
    # Minimal init without full session
    col.source = "OZON"
    col.title = "Ozon"

    class FakeResp:
        def __init__(self, text):
            self.text = text
            self.content = text.encode("utf-8")
            self.status_code = 200

    try:
        obs = col.parse_page(FakeResp(html), oid, f"https://www.ozon.ru/product/{oid}/", f"OZON-{oid}")
        if obs:
            col._ensure_evidence(obs)
            col._ensure_price_amounts(obs)
            col._ensure_availability_in_fields(obs)
            return obs
    except Exception as e:
        log.warning("OZON parse error: %s", e)
    return None


def parse_ozon_composer(data, oid):
    """Extract from composer API JSON."""
    fields = {}
    price_candidates = []
    media_items = []
    availability = "UNKNOWN"

    # Walk the JSON to find product data
    def find_product(obj, depth=0):
        if depth > 8 or not isinstance(obj, (dict, list)):
            return None
        if isinstance(obj, list):
            for item in obj:
                r = find_product(item, depth + 1)
                if r:
                    return r
            return None
        if isinstance(obj, dict):
            if "title" in obj and ("price" in obj or "prices" in obj):
                return obj
            for v in obj.values():
                r = find_product(v, depth + 1)
                if r:
                    return r
        return None

    prod = find_product(data)
    if prod:
        title = prod.get("title") or prod.get("name") or f"OZON-{oid}"
        fields["title"] = {"status": "KNOWN", "value": title, "method": "COMPOSER"}
        price = prod.get("price") or prod.get("currentPrice")
        if price:
            try:
                minor = int(round(float(price) * 100)) if float(price) < 100000 else int(price)
                price_candidates.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "composer"})
            except (ValueError, TypeError):
                pass
        imgs = prod.get("images") or prod.get("photos") or []
        if isinstance(imgs, list):
            for img in imgs[:20]:
                if isinstance(img, str):
                    media_items.append({"type": "image", "source_url": img, "image_data": None})
                elif isinstance(img, dict):
                    u = img.get("url") or img.get("src")
                    if u:
                        media_items.append({"type": "image", "source_url": u, "image_data": None})

    return {
        "fields": fields,
        "price_candidates": price_candidates,
        "media": {"items": media_items},
        "availability": availability,
        "category_path": None,
    }


# ============================================================
# WILDBERRIES (price focus)
# ============================================================

def try_wb():
    """WB: card.json already works. Focus on getting price."""
    import requests as req
    from curl_cffi import requests as cffi_req

    NM_ID = 199298871
    vol = NM_ID // 100000
    part = NM_ID // 1000
    results = {"basket": False, "price_api": False, "price_source": None, "prices": [], "html": False}
    errors = []

    # --- 1. Basket CDN card.json (verify still works) ---
    log.info("WB 1: basket CDN card.json")
    card_data = None
    basket_host = None
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}/vol{vol}/part{part}/{NM_ID}/info/ru/card.json"
        try:
            r = req.get(url, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}, timeout=10)
            if r.status_code == 200:
                card_data = r.json()
                basket_host = n
                log.info("WB basket: %s OK (%d keys, %dB)", host, len(card_data), len(r.content))
                break
        except Exception:
            continue
    results["basket"] = card_data is not None

    # Check if card.json has price info
    if card_data:
        for key in ("price", "prices", "sale_price", "basic_price", "old_price"):
            if key in card_data:
                log.info("WB card.json has key '%s': %s", key, str(card_data[key])[:100])
                results["card_price_key"] = key

    # --- 2. Price via plain requests (bypass curl_cffi TLS) ---
    log.info("WB 2: price via plain requests")
    price_endpoints = [
        ("card_v1_req", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("card_v2_req", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
        ("web_api_req", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
        ("web_api_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM_ID}"),
    ]

    price_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    for label, url in price_endpoints:
        try:
            time.sleep(2)
            r = req.get(url, headers=price_headers, timeout=12)
            log.info("WB price %s: HTTP %d, %dB", label, r.status_code, len(r.content))
            if r.status_code == 200:
                data = r.json()
                products = data.get("products") or data.get("data", {}).get("items", [])
                if products:
                    p = products[0]
                    sizes = p.get("sizes", [])
                    for s in sizes:
                        pr = s.get("price", {})
                        if pr:
                            results["price_api"] = True
                            results["price_source"] = label
                            results["prices"] = pr
                            results["qty"] = s.get("qty")
                            results["name"] = p.get("name")
                            log.info("WB price via %s: %s", label, str(pr)[:200])
                            break
                    if results["price_api"]:
                        break
                elif "data" in data and isinstance(data["data"], dict):
                    d = data["data"]
                    prices = d.get("prices") or d.get("price")
                    if prices:
                        results["price_api"] = True
                        results["price_source"] = label
                        results["prices"] = prices
                        results["qty"] = d.get("qty")
                        results["name"] = d.get("name")
                        log.info("WB price via %s (data): %s", label, str(prices)[:200])
                        break
        except Exception as e:
            errors.append(f"{label}: {e}")

    # --- 3. Price via curl_cffi chrome120 ---
    if not results["price_api"]:
        log.info("WB 3: price via curl_cffi chrome120")
        for label, url in price_endpoints[:2]:
            try:
                time.sleep(2)
                r = cffi_req.get(url, impersonate="chrome120", timeout=12,
                                 headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                log.info("WB price c120 %s: HTTP %d, %dB", label, r.status_code, len(r.content))
                if r.status_code == 200:
                    data = r.json()
                    products = data.get("products", [])
                    if products:
                        p = products[0]
                        for s in p.get("sizes", []):
                            pr = s.get("price", {})
                            if pr:
                                results["price_api"] = True
                                results["price_source"] = f"c120_{label}"
                                results["prices"] = pr
                                results["qty"] = s.get("qty")
                                results["name"] = p.get("name")
                                break
                        if results["price_api"]:
                            break
            except Exception as e:
                errors.append(f"c120_{label}: {e}")

    # --- 4. Static basket price.json ---
    if not results["price_api"] and basket_host:
        log.info("WB 4: static-basket price.json")
        for n in range(1, 61):
            host = f"static-basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}/vol{vol}/part{part}/{NM_ID}/info/ru/price.json"
            try:
                r = req.get(url, headers={"Accept": "application/json"}, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    log.info("WB static-basket %s: %s", host, str(data)[:200])
                    if data:
                        results["price_api"] = True
                        results["price_source"] = f"static-basket-{n:02d}"
                        results["prices"] = data
                        break
            except Exception:
                continue

    # --- 5. HTML page (best-effort, for price in SSR) ---
    log.info("WB 5: HTML page")
    try:
        url = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"
        r = req.get(url, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }, timeout=20)
        log.info("WB HTML: HTTP %d, %dB", r.status_code, len(r.content))
        results["html"] = r.status_code == 200
        if r.status_code == 200 and len(r.text) > 5000:
            # Try to extract price from HTML
            html = r.text
            # JSON-LD
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(m.group(1).strip())
                    if isinstance(ld, dict):
                        offers = ld.get("offers", {})
                        if isinstance(offers, dict):
                            price = offers.get("price")
                            if price:
                                results["price_api"] = True
                                results["price_source"] = "html_jsonld"
                                results["prices"] = {"basic": price}
                                log.info("WB HTML JSON-LD price: %s", price)
                except (json.JSONDecodeError, ValueError):
                    pass
            # data-attributes
            for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
                val = int(m.group(1))
                if val > 100:
                    results["price_api"] = True
                    results["price_source"] = "html_data_price"
                    results["prices"] = {"basic": val / 100.0}
                    log.info("WB HTML data-price: %d", val)
                    break
    except Exception as e:
        errors.append(f"HTML: {e}")

    # --- Build observation ---
    observation = None
    if card_data or results["price_api"]:
        fields = {}
        price_candidates = []
        media_items = []
        availability = "UNKNOWN"

        title = (card_data or {}).get("imt_name") or results.get("name") or f"WB-{NM_ID}"
        fields["title"] = {"status": "KNOWN", "value": title, "method": "CARD_JSON" if card_data else "PRICE_API"}

        if card_data:
            selling = card_data.get("selling", {})
            brand = selling.get("brand_name") if isinstance(selling, dict) else None
            if brand:
                fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": "CARD_JSON"}
            subj = card_data.get("subj_name", "")
            subj_root = card_data.get("subj_root_name", "")
            if subj:
                fields["category"] = {"status": "KNOWN", "value": f"{subj_root} > {subj}" if subj_root else subj, "method": "CARD_JSON"}
            desc = card_data.get("description")
            if desc:
                fields["description"] = {"status": "KNOWN", "value": desc[:3000], "method": "CARD_JSON"}
            # Photos from card.json
            for key in ("photo_count", "photos", "images"):
                if key in card_data:
                    log.info("WB card.json media key: %s = %s", key, str(card_data[key])[:100])

        if results["prices"]:
            pr = results["prices"]
            role_map = {"basic": "regular", "product": "regular", "card": "card",
                        "wallet": "wallet", "old": "old", "crossed": "old", "bankCard": "card"}
            for key, val in pr.items():
                if isinstance(val, (int, float)) and val > 0:
                    minor = int(round(val * 100)) if val < 100000 else int(val)
                    role = role_map.get(key, "regular")
                    price_candidates.append({"role": role, "amount_minor": minor, "currency": "RUB",
                                             "label": f"WB {key}"})
            if results.get("qty") is not None:
                availability = "IN_STOCK" if results["qty"] > 0 else "OUT_OF_STOCK"

        observation = {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": availability,
            "category_path": fields.get("category", {}).get("value"),
        }

    return {
        "status": "OK" if (card_data and results["price_api"]) else ("PARTIAL" if card_data or results["price_api"] else "ERROR"),
        "observation": observation,
        "basket": results["basket"],
        "price_api": results["price_api"],
        "price_source": results["price_source"],
        "prices": results["prices"],
        "availability": observation.get("availability") if observation else "UNKNOWN",
        "html": results["html"],
        "errors": errors,
        "title": (card_data or {}).get("imt_name") or results.get("name") or f"WB-{NM_ID}",
    }


# ============================================================
# AVITO
# ============================================================

def try_avito():
    """Avito: curl_cffi, long delays, city page → ad IDs → fetch ad."""
    import requests as req
    from curl_cffi import requests as cffi_req

    CITY = "krasnoyarsk"
    errors = []
    results = {}

    # --- Strategy A: curl_cffi city page → extract ad IDs ---
    log.info("AVITO A: curl_cffi city page")
    ad_ids = []
    try:
        url = f"https://www.avito.ru/{CITY}"
        r = cffi_req.get(url, impersonate="chrome", timeout=25,
                         headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        log.info("AVITO city: HTTP %d, %dB", r.status_code, len(r.content))
        if r.status_code == 200 and len(r.text) > 5000:
            # Extract ad IDs from links: /krasnoyarsk/.../1234567890
            id_re = re.compile(rf'/avito\.ru/{CITY}/[a-z0-9\-]*/(\d{{9,}})')
            ad_ids = list(set(id_re.findall(r.text)))[:10]
            log.info("AVITO city: found %d ad IDs: %s", len(ad_ids), ad_ids[:5])
        elif r.status_code == 439:
            errors.append(f"city: 439 rate limit")
            log.info("AVITO city: 439, waiting 20s...")
            time.sleep(20)
            r = cffi_req.get(url, impersonate="chrome", timeout=25,
                             headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            log.info("AVITO city retry: HTTP %d, %dB", r.status_code, len(r.content))
            if r.status_code == 200:
                id_re = re.compile(rf'/avito\.ru/{CITY}/[a-z0-9\-]*/(\d{{9,}})')
                ad_ids = list(set(id_re.findall(r.text)))[:10]
        else:
            errors.append(f"city: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"city: {e}")

    # --- Strategy B: category page for specific items ---
    if not ad_ids:
        log.info("AVITO B: category pages")
        categories = ["telefony", "noutbuki-i-ultrabuki", "legkovye_avto"]
        for cat in categories:
            try:
                time.sleep(15)
                url = f"https://www.avito.ru/{CITY}/{cat}"
                r = cffi_req.get(url, impersonate="chrome", timeout=25,
                                 headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("AVITO cat %s: HTTP %d, %dB", cat, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.text) > 5000:
                    id_re = re.compile(rf'/avito\.ru/{CITY}/[a-z0-9\-]*/(\d{{9,}})')
                    new_ids = list(set(id_re.findall(r.text)))[:5]
                    ad_ids.extend(new_ids)
                    log.info("AVITO cat %s: %d IDs", cat, len(new_ids))
                    if ad_ids:
                        break
                elif r.status_code == 439:
                    log.info("AVITO cat %s: 439, waiting 20s...", cat)
                    time.sleep(20)
                    r = cffi_req.get(url, impersonate="chrome", timeout=25,
                                     headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    if r.status_code == 200:
                        id_re = re.compile(rf'/avito\.ru/{CITY}/[a-z0-9\-]*/(\d{{9,}})')
                        ad_ids.extend(list(set(id_re.findall(r.text)))[:5])
                        if ad_ids:
                            break
            except Exception as e:
                errors.append(f"cat {cat}: {e}")

    # --- Strategy C: known ad IDs (from previous diagnostics + fresh) ---
    if not ad_ids:
        log.info("AVITO C: known/fresh IDs")
        known_ids = ["8341658309", "1045236789", "1154321987"]
        # Also try some fresh-looking IDs
        import random
        for _ in range(5):
            known_ids.append(str(random.randint(8_000_000_000, 12_000_000_000)))
        ad_ids = known_ids

    # --- Fetch individual ads ---
    log.info("AVITO: fetching up to 5 ads: %s", ad_ids[:5])
    ad_html = None
    ad_id_used = None
    for aid in ad_ids[:5]:
        try:
            time.sleep(12)
            # Try both URL patterns
            for url_pat in [
                f"https://www.avito.ru/ad/{aid}",
                f"https://www.avito.ru/{CITY}/ad/{aid}",
            ]:
                r = cffi_req.get(url_pat, impersonate="chrome", timeout=25,
                                 headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log.info("AVITO ad %s (%s): HTTP %d, %dB", aid, url_pat[:50], r.status_code, len(r.content))
                if r.status_code == 200 and len(r.text) > 5000:
                    # Check it's not a 404 page
                    if "Такого объявления" not in r.text and "не существует" not in r.text.lower():
                        ad_html = r.text
                        ad_id_used = aid
                        break
                    else:
                        log.info("AVITO ad %s: 200 but 404 page", aid)
                elif r.status_code == 439:
                    log.info("AVITO ad %s: 439, waiting 25s...", aid)
                    time.sleep(25)
                    r = cffi_req.get(url_pat, impersonate="chrome", timeout=25,
                                     headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    if r.status_code == 200 and len(r.text) > 5000:
                        ad_html = r.text
                        ad_id_used = aid
                        break
                elif r.status_code == 404:
                    continue
            if ad_html:
                break
        except Exception as e:
            errors.append(f"ad {aid}: {e}")

    if not ad_html:
        return {"status": "ERROR", "error": f"no live ad found ({len(ad_ids)} tried)", "errors": errors}

    # --- Parse ---
    log.info("AVITO: parsing ad %s (%dB)", ad_id_used, len(ad_html))
    from r52.collectors.avito import AvitoCollector
    col = AvitoCollector.__new__(AvitoCollector)
    col.source = "AVITO"
    col.title = "Avito"

    class FakeResp:
        def __init__(self, text):
            self.text = text
            self.content = text.encode("utf-8")
            self.status_code = 200

    try:
        obs = col.parse_page(FakeResp(ad_html), ad_id_used, f"https://www.avito.ru/ad/{ad_id_used}", f"AVITO-{ad_id_used}")
        if obs:
            col._ensure_evidence(obs)
            col._ensure_price_amounts(obs)
            col._ensure_availability_in_fields(obs)
            return {
                "status": "OK",
                "ad_id": ad_id_used,
                "observation": obs,
                "title": obs.get("fields", {}).get("title", {}).get("value", "?"),
                "prices": len(obs.get("price_candidates", [])),
                "availability": obs.get("availability", "UNKNOWN"),
                "media": len(obs.get("media", {}).get("items", [])),
                "errors": errors,
            }
    except Exception as e:
        errors.append(f"parse: {e}")
        log.exception("AVITO parse error")

    return {"status": "ERROR", "error": "parse failed", "ad_id": ad_id_used, "errors": errors}


# ============================================================
# FARPOST (quick re-verify)
# ============================================================

def try_farpost():
    """Farpost: was OK in v78, quick re-verify."""
    import requests as req

    BP_ID = "130693618"
    url = f"https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/triangle-icelynx-ti501-205-55-r16-94t-xl-{BP_ID}.html"
    errors = []

    try:
        r = req.get(url, headers={
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }, timeout=20)
        log.info("FARPOST: HTTP %d, %dB", r.status_code, len(r.content))
        if r.status_code != 200 or len(r.text) < 5000:
            return {"status": "ERROR", "error": f"HTTP {r.status_code}, {len(r.content)}B", "errors": errors}

        from r52.collectors.farpost import FarpostCollector
        col = FarpostCollector.__new__(FarpostCollector)
        col.source = "FARPOST"
        col.title = "Farpost"

        class FakeResp:
            def __init__(self, text):
                self.text = text
                self.content = text.encode("utf-8")
                self.status_code = 200

        obs = col.parse_page(FakeResp(r.text), BP_ID, url, f"FARPOST-{BP_ID}")
        if obs:
            col._ensure_evidence(obs)
            col._ensure_price_amounts(obs)
            col._ensure_availability_in_fields(obs)
            return {
                "status": "OK",
                "observation": obs,
                "bulletin_id": BP_ID,
                "title": obs.get("fields", {}).get("title", {}).get("value", "?"),
                "prices": len(obs.get("price_candidates", [])),
                "availability": obs.get("availability", "UNKNOWN"),
                "media": len(obs.get("media", {}).get("items", [])),
                "errors": errors,
            }
    except Exception as e:
        errors.append(str(e))
        log.exception("FARPOST error")

    return {"status": "ERROR", "error": "parse failed", "errors": errors}


# ============================================================
# PERSIST TO DB
# ============================================================

def persist_observation(conn, source, obs, listing_id, url):
    """Write observation through the standard R52 store pipeline."""
    try:
        from r52.store.products import ProductStore
        store = ProductStore(str(DB_PATH))

        # Ensure source exists
        src_id = store.ensure_source(source)

        # Upsert product
        fields = obs.get("fields", {})
        title = fields.get("title", {}).get("value", f"{source}-{listing_id}")
        brand = fields.get("brand", {}).get("value")
        model = fields.get("model", {}).get("value")
        category = obs.get("category_path") or fields.get("category", {}).get("value")

        prod_id = store.upsert_product(
            source=source,
            listing_id=str(listing_id),
            url=url,
            title=title,
            brand=brand,
            model=model,
            category_path=category,
        )

        # Observation
        obs_id = store.record_observation(
            product_id=prod_id,
            source=source,
            fields=fields,
            availability=obs.get("availability", "UNKNOWN"),
        )

        # Price points
        pp_count = 0
        for pc in obs.get("price_candidates", []):
            if pc.get("amount_minor") and pc["amount_minor"] > 0:
                store.record_price_point(
                    product_id=prod_id,
                    role=pc.get("role", "regular"),
                    amount_minor=int(pc["amount_minor"]),
                    currency=pc.get("currency", "RUB"),
                    label=pc.get("label", ""),
                )
                pp_count += 1

        # Media
        media_items = obs.get("media", {}).get("items", [])
        if media_items:
            store.record_gallery(prod_id, media_items)

        log.info("PERSIST %s: prod=%d obs=%d pp=%d media=%d", source, prod_id, obs_id, pp_count, len(media_items))
        return {"product_id": prod_id, "obs_id": obs_id, "pp": pp_count, "media": len(media_items)}
    except Exception as e:
        log.exception("PERSIST %s error: %s", source, e)
        return {"error": str(e)}


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=== V79 E2E ALL ===")
    t0 = time.monotonic()

    conn = db_init()
    pre = db_counts(conn)
    log.info("PRE: %s", pre)

    platforms = {}

    # 1. FARPOST
    log.info("=== 1. FARPOST ===")
    try:
        fp = try_farpost()
        platforms["FARPOST"] = fp
        if fp["status"] == "OK":
            p = persist_observation(conn, "FARPOST", fp["observation"], fp["bulletin_id"],
                                    f"https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/triangle-icelynx-ti501-205-55-r16-94t-xl-{fp['bulletin_id']}.html")
            fp["persist"] = p
    except Exception as e:
        platforms["FARPOST"] = {"status": "ERROR", "error": str(e)}
        log.exception("FARPOST exception")

    # 2. OZON
    log.info("=== 2. OZON ===")
    try:
        oz = try_ozon()
        platforms["OZON"] = oz
        if oz["status"] == "OK" and oz.get("parsed"):
            oid = oz.get("strategy", "479010389")
            # Extract actual ID from parsed
            p = persist_observation(conn, "OZON", oz["parsed"], oid,
                                    f"https://www.ozon.ru/product/{oid}/")
            oz["persist"] = p
    except Exception as e:
        platforms["OZON"] = {"status": "ERROR", "error": str(e)}
        log.exception("OZON exception")

    # 3. WILDBERRIES
    log.info("=== 3. WILDBERRIES ===")
    try:
        wb = try_wb()
        platforms["WB"] = wb
        if wb.get("observation"):
            p = persist_observation(conn, "WB", wb["observation"], "199298871",
                                    "https://www.wildberries.ru/catalog/199298871/detail.aspx")
            wb["persist"] = p
    except Exception as e:
        platforms["WB"] = {"status": "ERROR", "error": str(e)}
        log.exception("WB exception")

    # 4. AVITO
    log.info("=== 4. AVITO ===")
    try:
        av = try_avito()
        platforms["AVITO"] = av
        if av["status"] == "OK" and av.get("observation"):
            p = persist_observation(conn, "AVITO", av["observation"], av["ad_id"],
                                    f"https://www.avito.ru/ad/{av['ad_id']}")
            av["persist"] = p
    except Exception as e:
        platforms["AVITO"] = {"status": "ERROR", "error": str(e)}
        log.exception("AVITO exception")

    # 5. VERIFICATION
    log.info("=== 5. VERIFICATION ===")
    conn.commit()
    post = db_counts(conn)
    log.info("POST: %s", post)

    products = db_products(conn)
    log.info("Products (%d):", len(products))
    for p in products:
        log.info("  [%s] id=%s lid=%s | %s | %s %s | obs=%s | %s",
                 p["source"], p["id"], p["listing_id"],
                 (p["title"] or "?")[:40],
                 p["last_lowest_minor"], "RUB" if p["last_lowest_minor"] else "?",
                 p["last_availability"], p["observation_count"],
                 (p["last_seen_at"] or "?")[:19])

    pps = db_price_points(conn)
    log.info("Price points (last %d):", len(pps))
    for pp in pps:
        log.info("  PP id=%s prod=%s role=%s amt=%s %s label=%s",
                 pp["id"], pp["product_id"], pp["role"],
                 pp["amount_minor"], pp["currency"], (pp["label"] or "?")[:30])

    obs_recent = db_observations_recent(conn)
    log.info("Recent observations (%d):", len(obs_recent))
    for o in obs_recent:
        log.info("  OBS id=%s prod=%s src=%s at=%s status=%s",
                 o["id"], o["product_id"], o["source"],
                 (o["observed_at"] or "?")[:19], o["status"])

    # Summary
    elapsed = time.monotonic() - t0
    summary = {
        "db": str(DB_PATH),
        "pre": pre,
        "post": post,
        "platforms": {},
        "elapsed_sec": round(elapsed, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    for name, data in platforms.items():
        s = {"status": data.get("status", "?")}
        if data.get("observation"):
            obs = data["observation"]
            s["title"] = obs.get("fields", {}).get("title", {}).get("value", "?")[:60]
            s["prices"] = len(obs.get("price_candidates", []))
            s["availability"] = obs.get("availability", "UNKNOWN")
            s["media"] = len(obs.get("media", {}).get("items", []))
        if data.get("persist"):
            s["persist"] = data["persist"]
        if data.get("error"):
            s["error"] = data["error"]
        if data.get("price_source"):
            s["price_source"] = data["price_source"]
        if data.get("strategy"):
            s["strategy"] = data["strategy"]
        if data.get("ad_id"):
            s["ad_id"] = data["ad_id"]
        if data.get("bulletin_id"):
            s["bulletin_id"] = data["bulletin_id"]
        if data.get("basket") is not None:
            s["basket"] = data["basket"]
        if data.get("price_api") is not None:
            s["price_api"] = data["price_api"]
        if data.get("html") is not None:
            s["html"] = data["html"]
        if data.get("errors"):
            s["errors"] = data["errors"][:5]
        summary["platforms"][name] = s

    # Write result
    RESULT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("Result: %s", RESULT_PATH.name)

    # Console summary
    log.info("")
    log.info("=" * 60)
    log.info("V79 E2E ALL: %s (%.1fs)", "OK" if all(p.get("status") == "OK" for p in platforms.values()) else "PARTIAL", elapsed)
    for name, s in summary["platforms"].items():
        icon = "OK" if s["status"] == "OK" else ("WARN" if s["status"] == "PARTIAL" else "ERR")
        log.info("  [%s] %s: prices=%s avail=%s | %s", icon, name, s.get("prices", 0), s.get("availability", "?"), s.get("title", s.get("error", "?"))[:50])
    log.info("  DB: %s -> %s", pre, post)
    log.info("=" * 60)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
