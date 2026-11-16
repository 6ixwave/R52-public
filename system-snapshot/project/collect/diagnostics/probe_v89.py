#!/usr/bin/env python3
"""probe_v89.py — Comprehensive E2E probe: WB, Ozon, Avito, Farpost.

Strategy:
- curl_cffi chrome131 for all HTTP (matches UA Chrome/131 in base.py)
- WB: all 60 basket hosts + card API + newer endpoints
- Ozon: SSR-HTML via curl_cffi, parse og/meta/price
- Avito: www + m.avito.ru, known ad IDs
- Farpost: re-verify via collector class
- Full E2E: JSON → Inbox → accept → SQLite
"""

import json
import logging
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v89")

ROOT = Path(__file__).parent
DB_PATH = ROOT / "r52" / "data" / "r52.db"
INBOX_DIR = ROOT / "test-intake-v89"

# curl_cffi
from curl_cffi import requests as cffi_req
IMP = "chrome131"

def cget(url, headers=None, timeout=15):
    kw = {"impersonate": IMP, "timeout": timeout}
    if headers:
        kw["headers"] = headers
    return cffi_req.get(url, **kw)

# ============================================================
# 1. WILDBERRIES
# ============================================================
def probe_wb():
    results = {}
    nm_ids = [199298871, 1285331379]
    for nm_id in nm_ids:
        vol = nm_id // 100000
        part = (nm_id // 1000) % 1000
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        log.info("WB %d: vol=%d part=%d", nm_id, vol, part)

        card_data = None
        found_host = None
        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            try:
                resp = cget(url, headers={"Accept": "application/json"}, timeout=8)
                if resp.status_code == 200:
                    found_host = host
                    card_data = resp.json()
                    log.info("WB %d: %s OK (%dB, %d keys)",
                             nm_id, host, len(resp.content), len(card_data))
                    break
                elif n <= 3 or n % 10 == 0:
                    log.info("WB %d: %s HTTP %d", nm_id, host, resp.status_code)
            except Exception as e:
                if n <= 2:
                    log.warning("WB %d: %s err: %s", nm_id, host, e)
            time.sleep(0.2)

        entry = {"vol": vol, "part": part}
        if card_data:
            entry["host"] = found_host
            entry["nm_id"] = card_data.get("nm_id")
            entry["name"] = card_data.get("name", "")
            entry["brand"] = card_data.get("brand", "")
            entry["keys"] = list(card_data.keys())[:15]
        else:
            entry["host"] = None
            entry["status"] = "NO_BASKET"

        # Card API v2
        for api_url in [
            f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
            f"https://card.wb.ru/cards/detail?nm={nm_id}",
        ]:
            try:
                resp = cget(api_url, headers={
                    "Accept": "application/json",
                    "Referer": "https://www.wildberries.ru/",
                }, timeout=10)
                log.info("WB %d API %s: HTTP %d, %dB",
                         nm_id, api_url.split("?")[0].split("/")[-1],
                         resp.status_code, len(resp.content))
                if resp.status_code == 200:
                    data = resp.json()
                    products = data.get("data", {}).get("products", [])
                    if products:
                        p = products[0]
                        entry["api_price"] = p.get("price", {}).get("basic")
                        entry["api_name"] = p.get("name", "")
                        entry["api_brand"] = p.get("brand", "")
                        entry["api_size"] = len(resp.content)
                        break
            except Exception as e:
                log.warning("WB %d API err: %s", nm_id, e)
            time.sleep(1)

        results[str(nm_id)] = entry

    return results


# ============================================================
# 2. OZON
# ============================================================
def probe_ozon():
    results = {}
    pids = ["479010389", "721093013"]
    for pid in pids:
        url = f"https://www.ozon.ru/product/{pid}/"
        log.info("OZON %s: GET %s", pid, url)
        try:
            resp = cget(url, timeout=15)
            log.info("OZON %s: HTTP %d, %dB", pid, resp.status_code, len(resp.content))
            if resp.status_code == 200:
                html = resp.text
                out_html = ROOT / f"probe_v89_ozon_{pid}.html"
                out_html.write_text(html, encoding="utf-8")
                # Parse
                title_m = re.search(
                    r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                    html, re.I)
                price_m = re.search(
                    r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)',
                    html, re.I)
                img_m = re.search(
                    r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
                    html, re.I)
                # data-widget price
                dw_price = re.findall(r'data-price=["\'](\d+)["\']', html[:20000])
                # Visible price text
                vis_prices = re.findall(
                    r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', html[:30000])

                entry = {
                    "http": 200,
                    "title": title_m.group(1) if title_m else "UNKNOWN",
                    "og_price": price_m.group(1) if price_m else None,
                    "og_image": (img_m.group(1)[:80] if img_m else None),
                    "data_prices": dw_price[:5],
                    "visible_prices": vis_prices[:5],
                    "html_size": len(html),
                }
                results[pid] = entry
                log.info("OZON %s: title=%r price=%r vis=%s",
                         pid, entry["title"][:40], entry["og_price"], entry["visible_prices"][:3])
            else:
                # Save body for debugging
                body = resp.content[:5000]
                (ROOT / f"probe_v89_ozon_{pid}_body.bin").write_bytes(body)
                results[pid] = {"http": resp.status_code, "body_size": len(resp.content)}
        except Exception as e:
            results[pid] = {"error": str(e)}
            log.exception("OZON %s error", pid)
        time.sleep(3)
    return results


# ============================================================
# 3. AVITO
# ============================================================
def probe_avito():
    results = {}
    # Known ads + category page
    targets = [
        ("8341658309", "https://www.avito.ru/krasnoyarsk/iphone-14-128-gb-8341658309"),
        ("1154321987", "https://www.avito.ru/krasnoyarsk/iphone-11-1154321987"),
    ]
    # Category page to find live ads
    cat_url = "https://www.avito.ru/krasnoyarsk/telefony"

    # First: category page to find live ad IDs
    log.info("AVITO: category page %s", cat_url)
    live_ids = []
    try:
        resp = cget(cat_url, timeout=15)
        log.info("AVITO cat: HTTP %d, %dB", resp.status_code, len(resp.content))
        if resp.status_code == 200:
            html = resp.text
            (ROOT / "probe_v89_avito_cat.html").write_text(html, encoding="utf-8")
            # Find ad links: /krasnoyarsk/...-NNNNNNN
            ad_ids = re.findall(r'avito\.ru/krasnoyarsk/[a-z0-9\-]+-(\d{6,})', html)
            live_ids = list(dict.fromkeys(ad_ids))[:5]  # unique, first 5
            log.info("AVITO cat: found %d live ad IDs: %s", len(live_ids), live_ids)
    except Exception as e:
        log.warning("AVITO cat error: %s", e)

    # If no live IDs from category, use known ones
    if not live_ids:
        live_ids = [t[0] for t in targets]

    for ad_id in live_ids[:4]:
        # Build URL
        if ad_id in dict(targets):
            url = dict(targets)[ad_id]
        else:
            url = f"https://www.avito.ru/krasnoyarsk/ad/{ad_id}"

        log.info("AVITO %s: GET %s", ad_id, url)
        got_data = False
        for prefix in ["https://www.avito.ru", "https://m.avito.ru"]:
            test_url = url.replace("https://www.avito.ru", prefix).replace(
                "https://m.avito.ru", prefix)
            if "ad/" in test_url and prefix == "https://m.avito.ru":
                test_url = test_url  # m.avito.ru/ad/ID
            try:
                resp = cget(test_url, timeout=15)
                log.info("AVITO %s (%s): HTTP %d, %dB",
                         ad_id, prefix.split("//")[1].split(".")[0],
                         resp.status_code, len(resp.content))
                if resp.status_code == 200:
                    html = resp.text
                    (ROOT / f"probe_v89_avito_{ad_id}.html").write_text(html, encoding="utf-8")
                    title_m = re.search(
                        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                        html, re.I)
                    price_m = re.search(
                        r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)',
                        html, re.I)
                    # JSON-LD
                    jsonld_blocks = re.findall(
                        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html, re.DOTALL)
                    has_ld = len(jsonld_blocks) > 0
                    # State
                    has_state = "PRELOADED_STATE" in html or "data-state" in html

                    results[ad_id] = {
                        "http": 200,
                        "url": test_url,
                        "title": title_m.group(1) if title_m else "UNKNOWN",
                        "og_price": price_m.group(1) if price_m else None,
                        "jsonld": has_ld,
                        "state": has_state,
                        "html_size": len(html),
                    }
                    log.info("AVITO %s: title=%r price=%r ld=%s state=%s",
                             ad_id, results[ad_id]["title"][:40],
                             results[ad_id]["og_price"], has_ld, has_state)
                    got_data = True
                    break
                elif resp.status_code == 404:
                    results[ad_id] = {"http": 404, "url": test_url}
                    break
                elif resp.status_code == 439:
                    results[ad_id] = {"http": 439, "url": test_url,
                                      "note": "bot-challenge"}
                    # Try m. version
                else:
                    if ad_id not in results:
                        results[ad_id] = {"http": resp.status_code, "url": test_url}
            except Exception as e:
                if ad_id not in results:
                    results[ad_id] = {"error": str(e)}
            time.sleep(5)

    return results


# ============================================================
# 4. FARPOST (re-verify + E2E)
# ============================================================
def probe_farpost_e2e():
    """Full E2E: collector → JSON → Inbox → SQLite."""
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=ROOT / "r52" / "data")
    url = "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-127622540.html"
    log.info("FP: collecting %s", url)
    obs = col.collect_product("127622540", url, "Шины Boto WD69 245/50 R20")
    if not obs:
        return {"status": "NO_DATA"}

    # Write to inbox
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"jarvis-eye-farpost-{uuid.uuid4().hex[:8]}.json"
    fpath = INBOX_DIR / fname
    fpath.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("FP: wrote %s (%dB)", fname, fpath.stat().st_size)

    # Inbox accept
    from r52.intake.eye import Inbox
    inbox = Inbox(INBOX_DIR, DB_PATH)
    time.sleep(0.3)
    entry = inbox.stage_bytes(fpath.read_bytes(), fname)
    log.info("FP: staged id=%s status=%s", entry.get("id"), entry.get("status"))

    accepted = inbox.accept(entry["id"])
    log.info("FP: accepted=%s", accepted)

    # Verify SQLite
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM product WHERE source='FARPOST' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM observation WHERE source='FARPOST'")
    obs_cnt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM price_point WHERE source='FARPOST'")
    pp_cnt = cur.fetchone()[0]
    conn.close()

    title = obs["observation"]["fields"].get("title", {}).get("value", "?")
    prices = obs["observation"].get("price_candidates", [])
    price_minor = prices[0]["amount_minor"] if prices else None

    return {
        "status": "E2E_OK" if row else "E2E_PARTIAL",
        "product_id": row[0] if row else None,
        "title": row[1] if row else title,
        "price_minor": price_minor,
        "obs_count": obs_cnt,
        "pp_count": pp_cnt,
    }


# ============================================================
# 5. WB E2E (if card data obtained)
# ============================================================
def wb_e2e(card_data, nm_id, host):
    """Build observation from card.json + price, write to inbox, accept."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()

    name = card_data.get("name", f"WB-{nm_id}")
    brand = card_data.get("brand", "")
    desc = card_data.get("description", "")
    options = card_data.get("options", [])
    # Build options string
    opt_str = ""
    if isinstance(options, list):
        for o in options:
            if isinstance(o, dict):
                opt_str += f"{o.get('name','')}={o.get('value','')} "
    elif isinstance(options, dict):
        for k, v in options.items():
            opt_str += f"{k}={v} "

    # Media
    media_items = []
    imgs = card_data.get("im", [])
    if isinstance(imgs, list):
        for img in imgs[:10]:
            if isinstance(img, dict):
                ph = img.get("ph", "")
                if ph:
                    media_items.append({
                        "type": "image",
                        "source_url": f"https://basket-{host.split('-')[1]}.wbbasket.ru/{ph}",
                    })

    fields = {
        "title": {"status": "KNOWN", "value": name, "method": "BASKET_CDN",
                  "evidence": {"method": "BASKET_CDN", "source": "WB"}},
    }
    if brand:
        fields["brand"] = {"status": "KNOWN", "value": brand, "method": "BASKET_CDN",
                           "evidence": {"method": "BASKET_CDN", "source": "WB"}}
    if desc:
        fields["description"] = {"status": "KNOWN", "value": desc[:3000],
                                 "method": "BASKET_CDN",
                                 "evidence": {"method": "BASKET_CDN", "source": "WB"}}
    if opt_str:
        fields["characteristics"] = {"status": "KNOWN", "value": opt_str.strip(),
                                     "method": "BASKET_CDN",
                                     "evidence": {"method": "BASKET_CDN", "source": "WB"}}

    price_candidates = []
    # From card.json: price is often in "sizes" or separate API
    # For now, if we have API price
    # (caller can add)

    observation = {
        "fields": fields,
        "price_candidates": price_candidates,
        "media": {"items": media_items},
        "availability": "IN_STOCK" if media_items else "UNKNOWN",
        "category_path": card_data.get("subject", ""),
    }

    # Ensure price amounts
    for c in price_candidates:
        if "amount" not in c and c.get("amount_minor") is not None:
            minor = int(c["amount_minor"])
            c["amount"] = f"{minor // 100}.{minor % 100:02d}"

    obs_wrapper = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "WB",
        "observation": {
            **observation,
            "source": "WB",
            "id": str(nm_id),
            "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
            "observed_at": now,
            "status": "PARTIAL_SUCCESS" if price_candidates else "DEGRADED",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }

    fname = f"jarvis-eye-wb-{uuid.uuid4().hex[:8]}.json"
    fpath = INBOX_DIR / fname
    fpath.write_text(json.dumps(obs_wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("WB E2E: wrote %s (%dB)", fname, fpath.stat().st_size)

    from r52.intake.eye import Inbox
    inbox = Inbox(INBOX_DIR, DB_PATH)
    time.sleep(0.3)
    entry = inbox.stage_bytes(fpath.read_bytes(), fname)
    log.info("WB E2E: staged id=%s status=%s", entry.get("id"), entry.get("status"))
    accepted = inbox.accept(entry["id"])
    log.info("WB E2E: accepted=%s", accepted)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM product WHERE source='WB' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()

    return {
        "product_id": row[0] if row else None,
        "title": row[1] if row else name,
        "accepted": accepted,
    }


# ============================================================
# 6. OZON E2E
# ============================================================
def ozon_e2e(pid, html):
    """Build observation from Ozon HTML, E2E to SQLite."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()

    title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    title = title_m.group(1) if title_m else f"OZON-{pid}"

    price_candidates = []
    # og:price
    pm = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    if pm:
        try:
            minor = int(round(float(pm.group(1)) * 100))
            price_candidates.append({"role": "regular", "amount_minor": minor,
                                     "currency": "RUB", "amount": pm.group(1),
                                     "visible_label": "og:price"})
        except ValueError:
            pass
    # data-price
    for dm in re.finditer(r'data-price=["\'](\d+)["\']', html[:30000], re.I):
        try:
            minor = int(dm.group(1))
            if minor > 0 and minor < 100_000_000:
                price_candidates.append({"role": "regular", "amount_minor": minor,
                                         "currency": "RUB",
                                         "amount": f"{minor//100}.{minor%100:02d}",
                                         "visible_label": "data-price"})
        except ValueError:
            pass
    # Visible text prices
    seen = {c["amount_minor"] for c in price_candidates}
    for vm in re.finditer(r'(\d[\d\s\u00a0]{1,9})\s*₽', html[:40000]):
        raw = vm.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            amt = float(raw)
            if 1 < amt < 10_000_000:
                minor = int(round(amt * 100))
                if minor not in seen:
                    seen.add(minor)
                    price_candidates.append({"role": "regular", "amount_minor": minor,
                                             "currency": "RUB",
                                             "amount": f"{minor//100}.{minor%100:02d}",
                                             "visible_label": "HTML"})
        except ValueError:
            pass

    # Media
    media_items = []
    for im in re.finditer(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)', html, re.I):
        url = im.group(1)
        media_items.append({"type": "image", "source_url": url})
        if len(media_items) >= 10:
            break

    fields = {
        "title": {"status": "KNOWN", "value": title, "method": "SSR",
                  "evidence": {"method": "SSR", "source": "OZON"}},
    }
    if media_items:
        fields["description"] = {"status": "KNOWN", "value": f"Ozon product {pid}",
                                 "method": "SSR",
                                 "evidence": {"method": "SSR", "source": "OZON"}}

    obs_wrapper = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "OZON",
        "observation": {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": "IN_STOCK" if price_candidates else "UNKNOWN",
            "category_path": None,
            "source": "OZON",
            "id": str(pid),
            "url": f"https://www.ozon.ru/product/{pid}/",
            "observed_at": now,
            "status": "PARTIAL_SUCCESS" if price_candidates else "DEGRADED",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }

    fname = f"jarvis-eye-ozon-{uuid.uuid4().hex[:8]}.json"
    fpath = INBOX_DIR / fname
    fpath.write_text(json.dumps(obs_wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("OZON E2E: wrote %s (%dB)", fname, fpath.stat().st_size)

    from r52.intake.eye import Inbox
    inbox = Inbox(INBOX_DIR, DB_PATH)
    time.sleep(0.3)
    entry = inbox.stage_bytes(fpath.read_bytes(), fname)
    log.info("OZON E2E: staged id=%s status=%s", entry.get("id"), entry.get("status"))
    accepted = inbox.accept(entry["id"])
    log.info("OZON E2E: accepted=%s", accepted)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM product WHERE source='OZON' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()

    return {
        "product_id": row[0] if row else None,
        "title": row[1] if row else title,
        "prices": len(price_candidates),
        "media": len(media_items),
        "accepted": accepted,
    }


# ============================================================
# 7. AVITO E2E
# ============================================================
def avito_e2e(ad_id, html, url):
    """Build observation from Avito HTML, E2E to SQLite."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()

    title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    title = title_m.group(1) if title_m else f"AVITO-{ad_id}"

    price_candidates = []
    # JSON-LD
    ld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL)
    for block in ld_blocks:
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
                    offers = item.get("offers")
                    if isinstance(offers, dict):
                        offers = [offers]
                    if isinstance(offers, list):
                        for off in offers:
                            if isinstance(off, dict):
                                pv = off.get("price") or off.get("lowPrice")
                                if pv:
                                    minor = int(round(float(pv) * 100))
                                    price_candidates.append({
                                        "role": "regular", "amount_minor": minor,
                                        "currency": off.get("priceCurrency", "RUB"),
                                        "amount": str(pv), "visible_label": "JSON-LD"})
        except (json.JSONDecodeError, ValueError):
            continue

    # og:price
    if not price_candidates:
        pm = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if pm:
            try:
                minor = int(round(float(pm.group(1)) * 100))
                price_candidates.append({"role": "regular", "amount_minor": minor,
                                         "currency": "RUB", "amount": pm.group(1),
                                         "visible_label": "og:price"})
            except ValueError:
                pass

    # Media
    media_items = []
    for im in re.finditer(r'(?:src|data-src)=["\']([^"\']*(?:mmedia\.avito\.st|l\d+\.avito\.st)/[^"\']+)', html, re.I):
        media_items.append({"type": "image", "source_url": im.group(1)})
        if len(media_items) >= 10:
            break

    fields = {
        "title": {"status": "KNOWN", "value": title, "method": "SSR",
                  "evidence": {"method": "SSR", "source": "AVITO"}},
    }

    obs_wrapper = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "AVITO",
        "observation": {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": "IN_STOCK" if price_candidates else "UNKNOWN",
            "category_path": None,
            "source": "AVITO",
            "id": str(ad_id),
            "url": url,
            "observed_at": now,
            "status": "PARTIAL_SUCCESS" if price_candidates else "DEGRADED",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }

    fname = f"jarvis-eye-avito-{uuid.uuid4().hex[:8]}.json"
    fpath = INBOX_DIR / fname
    fpath.write_text(json.dumps(obs_wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("AVITO E2E: wrote %s (%dB)", fname, fpath.stat().st_size)

    from r52.intake.eye import Inbox
    inbox = Inbox(INBOX_DIR, DB_PATH)
    time.sleep(0.3)
    entry = inbox.stage_bytes(fpath.read_bytes(), fname)
    log.info("AVITO E2E: staged id=%s status=%s", entry.get("id"), entry.get("status"))
    accepted = inbox.accept(entry["id"])
    log.info("AVITO E2E: accepted=%s", accepted)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM product WHERE source='AVITO' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()

    return {
        "product_id": row[0] if row else None,
        "title": row[1] if row else title,
        "prices": len(price_candidates),
        "media": len(media_items),
        "accepted": accepted,
    }


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== V89 SETUP ===")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    log.info("Tables: %s", tables)
    pre = {}
    for t in ["product", "observation", "price_point", "source"]:
        if t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            pre[t] = cur.fetchone()[0]
    log.info("PRE: %s", pre)
    conn.close()

    results = {}

    # 1. FARPOST
    log.info("=== 1. FARPOST ===")
    try:
        results["FARPOST"] = probe_farpost_e2e()
    except Exception as e:
        log.exception("FARPOST error")
        results["FARPOST"] = {"error": str(e)}

    # 2. WB
    log.info("=== 2. WILDBERRIES ===")
    try:
        wb_data = probe_wb()
        results["WB"] = wb_data
        # E2E for first successful card
        for nm_id_str, entry in wb_data.items():
            if entry.get("nm_id"):
                log.info("WB E2E: nm_id=%s host=%s", nm_id_str, entry.get("host"))
                # Rebuild card data (simplified: use what we have)
                card = {"name": entry.get("name", f"WB-{nm_id_str}"),
                        "brand": entry.get("brand", ""),
                        "description": "",
                        "options": [],
                        "im": [],
                        "subject": ""}
                # Add API price if available
                if entry.get("api_price"):
                    card["api_price"] = entry["api_price"]
                e2e_res = wb_e2e(card, int(nm_id_str), entry.get("host", "basket-01"))
                results["WB"]["e2e"] = e2e_res
                break
    except Exception as e:
        log.exception("WB error")
        results["WB"] = {"error": str(e)}

    # 3. OZON
    log.info("=== 3. OZON ===")
    try:
        oz_data = probe_ozon()
        results["OZON"] = oz_data
        # E2E for first successful
        for pid, entry in oz_data.items():
            if entry.get("http") == 200:
                log.info("OZON E2E: pid=%s", pid)
                html = (ROOT / f"probe_v89_ozon_{pid}.html").read_text(encoding="utf-8")
                e2e_res = ozon_e2e(pid, html)
                results["OZON"]["e2e"] = e2e_res
                break
    except Exception as e:
        log.exception("OZON error")
        results["OZON"] = {"error": str(e)}

    # 4. AVITO
    log.info("=== 4. AVITO ===")
    try:
        av_data = probe_avito()
        results["AVITO"] = av_data
        # E2E for first successful
        for ad_id, entry in av_data.items():
            if entry.get("http") == 200:
                log.info("AVITO E2E: ad_id=%s", ad_id)
                html = (ROOT / f"probe_v89_avito_{ad_id}.html").read_text(encoding="utf-8")
                e2e_res = avito_e2e(ad_id, html, entry.get("url", ""))
                results["AVITO"]["e2e"] = e2e_res
                break
    except Exception as e:
        log.exception("AVITO error")
        results["AVITO"] = {"error": str(e)}

    # POST
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    post = {}
    for t in ["product", "observation", "price_point", "source"]:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        post[t] = cur.fetchone()[0]
    conn.close()
    log.info("POST: %s", post)

    # Summary
    for src in ["FARPOST", "WB", "OZON", "AVITO"]:
        r = results.get(src, {})
        status = r.get("status", r.get("e2e", {}).get("product_id", "UNKNOWN"))
        log.info("  %s: %s", src, status)

    out = {"PRE": pre, "POST": post, "results": results}
    (ROOT / "probe_v89_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved probe_v89_result.json")
    log.info("=== V89 DONE ===")


if __name__ == "__main__":
    main()
