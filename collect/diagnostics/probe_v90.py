#!/usr/bin/env python3
"""probe_v90.py — Full E2E: 4 marketplaces → Inbox(app) → SQLite.

Fixes v89 Inbox init error (Inbox(app) not Inbox(app, db)).
Runs collectors directly (no subprocess), feeds JSON through real Inbox,
verifies SQLite row counts.
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
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v90")

ROOT = Path(__file__).parent
DB_PATH = ROOT / "r52" / "data" / "r52.db"
INBOX_DIR = ROOT / "test-intake-v90"

# ============================================================
# Minimal App mock (real Database + Clock, stub settings/notifier)
# ============================================================
from r52.db.database import Database
from r52.timeutil import Clock
from r52.intake.eye import Inbox
from r52.core.watches import evaluate

class MockSettings:
    def get(self, key, default=None):
        if key == "intake.auto_accept":
            return False  # we call accept() manually
        return default

class MockNotifier:
    def send(self, *a, **kw):
        pass
    def __getattr__(self, name):
        return lambda *a, **kw: None

class MockApp:
    def __init__(self, db_path: Path):
        self.db = Database(db_path)
        self.clock = Clock()
        self.settings = MockSettings()
        self.notifier = MockNotifier()

def make_app() -> MockApp:
    app = MockApp(DB_PATH)
    app.db.initialize()
    # Run migrations
    from r52.db.migrate import migrate
    result = migrate(app.db, backup_dir=ROOT / "test-backups-v90", now=app.clock.now())
    if result.get("applied"):
        log.info("Migrations applied: %s", result["applied"])
    return app

# ============================================================
# SQLite counter helper
# ============================================================
def db_counts(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    tables = ["product", "observation", "price_point", "source", "inbox_item"]
    counts = {}
    for t in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()
            counts[t] = row["c"]
        except Exception:
            counts[t] = -1
    conn.close()
    return counts

# ============================================================
# 1. WILDBERRIES
# ============================================================
def probe_wb(app: MockApp) -> dict:
    log.info("=" * 60)
    log.info("WILDBERRIES probe starting")
    results = {"source": "WB", "items": [], "inbox_accepted": 0}

    from r52.collectors.wb import WBCollector
    col = WBCollector(data_dir=None, use_proxies=False)

    nm_ids = [199298871, 1285331379]
    for nm_id in nm_ids:
        url = col.build_url(str(nm_id))
        log.info("WB %d: %s", nm_id, url)
        try:
            obs = col.collect_product(str(nm_id), url, title_hint="")
        except Exception as e:
            log.warning("WB %d: collect_product error: %s", nm_id, e)
            obs = None

        if obs:
            data_bytes = json.dumps(obs, ensure_ascii=False).encode("utf-8")
            fname = f"jarvis-eye-wb-{nm_id}.json"
            item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
            log.info("WB %d: inbox stage → %s (created=%s)", nm_id, item.get("status"), created)
            if item["status"] == "PENDING":
                try:
                    result = app.inbox.accept(item["id"])
                    log.info("WB %d: ACCEPTED product_id=%s obs_created=%s",
                             nm_id, result.get("product_id"), result.get("observation_created"))
                    results["inbox_accepted"] += 1
                    results["items"].append({
                        "nm_id": nm_id,
                        "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:80],
                        "product_id": result.get("product_id"),
                    })
                except Exception as e:
                    log.warning("WB %d: accept error: %s", nm_id, e)
                    results["items"].append({"nm_id": nm_id, "error": str(e)})
            elif item["status"] == "INVALID":
                results["items"].append({"nm_id": nm_id, "error": f"INVALID: {item.get('error_code')}"})
            else:
                results["items"].append({"nm_id": nm_id, "status": item["status"]})
        else:
            results["items"].append({"nm_id": nm_id, "status": "NO_DATA"})

    # Also try direct basket CDN (faster, no 8-20s throttle)
    if not any(i.get("status") != "NO_DATA" for i in results["items"]):
        log.info("WB: trying direct basket CDN (fast path)")
        from curl_cffi import requests as cffi_req
        IMP = "chrome124"
        for nm_id in nm_ids:
            vol = nm_id // 100000
            part = (nm_id // 1000) % 1000
            path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
            found = False
            for n in range(1, 61):
                host = f"basket-{n:02d}.wbbasket.ru"
                try:
                    resp = cffi_req.get(f"https://{host}{path}",
                                        headers={"Accept": "application/json"},
                                        impersonate=IMP, timeout=6)
                    if resp.status_code == 200:
                        card = resp.json()
                        name = card.get("name", "")
                        log.info("WB %d: basket %s → '%s' (%d keys)", nm_id, host, name[:50], len(card))
                        # Build observation from card.json
                        obs = _wb_card_to_observation(card, nm_id)
                        if obs:
                            data_bytes = json.dumps(obs, ensure_ascii=False).encode("utf-8")
                            fname = f"jarvis-eye-wb-b-{nm_id}.json"
                            item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
                            log.info("WB %d basket: inbox → %s", nm_id, item.get("status"))
                            if item["status"] == "PENDING":
                                result = app.inbox.accept(item["id"])
                                results["inbox_accepted"] += 1
                                results["items"].append({
                                    "nm_id": nm_id, "via": "basket_cdn",
                                    "title": name[:80], "product_id": result.get("product_id"),
                                })
                        found = True
                        break
                except Exception:
                    pass
                time.sleep(0.15)
            if not found:
                results["items"].append({"nm_id": nm_id, "status": "NO_BASKET"})
            time.sleep(1)

    results["collector_stats"] = col.stats
    return results


def _wb_card_to_observation(card: dict, nm_id: int) -> Optional[dict]:
    """Build JARVIS-format observation from basket card.json."""
    name = card.get("name", "")
    brand = card.get("brand", "")
    desc = card.get("description", "")
    if not name:
        return None

    # Price from card.json (may be in "sizes" or top-level)
    price_minor = None
    sizes = card.get("sizes", [])
    if sizes:
        s0 = sizes[0]
        price = s0.get("price", {})
        # price structure: {"basic": {"r": 12345, "v": 123.45}, ...}
        basic = price.get("basic", {})
        if isinstance(basic, dict) and "r" in basic:
            price_minor = int(basic["r"])
        elif isinstance(basic, (int, float)):
            price_minor = int(basic)
    if price_minor is None:
        p = card.get("price", {})
        if isinstance(p, dict):
            basic = p.get("basic", {})
            if isinstance(basic, dict) and "r" in basic:
                price_minor = int(basic["r"])

    # Media
    media_items = []
    for img_key in ("im", "photo", "images"):
        imgs = card.get(img_key, [])
        if isinstance(imgs, list):
            for img in imgs[:10]:
                if isinstance(img, str) and img.startswith("http"):
                    media_items.append({"type": "image", "source_url": img, "image_data": None})
                elif isinstance(img, dict):
                    u = img.get("url") or img.get("src")
                    if u:
                        media_items.append({"type": "image", "source_url": u, "image_data": None})
        if media_items:
            break

    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

    fields = {
        "title": {"status": "KNOWN", "value": name, "method": "COLLECTOR",
                  "evidence": {"method": "COLLECTOR", "source": "WB"}},
    }
    if brand:
        fields["model"] = {"status": "KNOWN", "value": brand, "method": "COLLECTOR",
                           "evidence": {"method": "COLLECTOR", "source": "WB"}}

    price_candidates = []
    if price_minor:
        price_candidates.append({
            "role": "regular",
            "amount": f"{price_minor // 100}.{price_minor % 100:02d}",
            "amount_minor": price_minor,
            "currency": "RUB",
            "visible_label": f"{price_minor // 100} ₽",
        })

    return {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "WB",
        "observation": {
            "source": "WB",
            "id": str(nm_id),
            "url": url,
            "observed_at": now,
            "status": "PARTIAL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": "IN_STOCK" if price_minor else "UNKNOWN",
            "category_path": card.get("category", ""),
        },
    }


# ============================================================
# 2. OZON
# ============================================================
def probe_ozon(app: MockApp) -> dict:
    log.info("=" * 60)
    log.info("OZON probe starting")
    results = {"source": "OZON", "items": [], "inbox_accepted": 0}

    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=None, use_proxies=False)

    listing_ids = ["479010389", "721093013"]
    for lid in listing_ids:
        url = col.build_url(lid)
        log.info("OZON %s: %s", lid, url)
        try:
            obs = col.collect_product(lid, url, title_hint="")
        except Exception as e:
            log.warning("OZON %s: collect_product error: %s", lid, e)
            obs = None

        if obs:
            data_bytes = json.dumps(obs, ensure_ascii=False).encode("utf-8")
            fname = f"jarvis-eye-ozon-{lid}.json"
            item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
            log.info("OZON %s: inbox → %s", lid, item.get("status"))
            if item["status"] == "PENDING":
                try:
                    result = app.inbox.accept(item["id"])
                    results["inbox_accepted"] += 1
                    results["items"].append({
                        "listing_id": lid,
                        "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:80],
                        "product_id": result.get("product_id"),
                    })
                except Exception as e:
                    log.warning("OZON %s: accept error: %s", lid, e)
                    results["items"].append({"listing_id": lid, "error": str(e)})
            else:
                results["items"].append({"listing_id": lid, "status": item["status"],
                                         "error_code": item.get("error_code")})
        else:
            # Fallback: direct curl_cffi SSR
            log.info("OZON %s: collector returned None, trying direct SSR", lid)
            obs2 = _ozon_direct_ssr(lid)
            if obs2:
                data_bytes = json.dumps(obs2, ensure_ascii=False).encode("utf-8")
                fname = f"jarvis-eye-ozon-s-{lid}.json"
                item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
                if item["status"] == "PENDING":
                    result = app.inbox.accept(item["id"])
                    results["inbox_accepted"] += 1
                    results["items"].append({
                        "listing_id": lid, "via": "direct_ssr",
                        "title": obs2["observation"].get("fields", {}).get("title", {}).get("value", "")[:80],
                        "product_id": result.get("product_id"),
                    })
                else:
                    results["items"].append({"listing_id": lid, "status": item["status"]})
            else:
                results["items"].append({"listing_id": lid, "status": "NO_DATA"})

    results["collector_stats"] = col.stats
    return results


def _ozon_direct_ssr(lid: str) -> Optional[dict]:
    """Direct curl_cffi SSR fetch for Ozon, parse data-widget / og / price."""
    from curl_cffi import requests as cffi_req
    url = f"https://www.ozon.ru/product/{lid}/"
    try:
        resp = cffi_req.get(url, impersonate="chrome124", timeout=15, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
    except Exception as e:
        log.warning("OZON SSR %s: %s", lid, e)
        return None

    if resp.status_code != 200:
        log.info("OZON SSR %s: HTTP %d", lid, resp.status_code)
        return None

    html = resp.text
    if len(html) < 1000:
        log.info("OZON SSR %s: short HTML (%d)", lid, len(html))
        return None

    # Title
    title = None
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        title = m.group(1)
    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            title = m.group(1).replace(" - Ozon", "").strip()

    # Price: look for data-widget="price" or common patterns
    price_minor = None
    price_patterns = [
        r'data-widget="price"[^>]*>.*?(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)',
        r'"price"\s*:\s*"?(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)"?',
        r'class="[^"]*price[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)',
    ]
    for pat in price_patterns:
        m = re.search(pat, html, re.I | re.DOTALL)
        if m:
            try:
                amt = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
                price_minor = int(round(float(amt) * 100))
                break
            except (ValueError, IndexError):
                continue

    # Media
    media_items = []
    for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', html, re.I):
        media_items.append({"type": "image", "source_url": m.group(1), "image_data": None})
        if len(media_items) >= 10:
            break

    if not title and not price_minor:
        log.info("OZON SSR %s: no title/price found in %d bytes", lid, len(html))
        return None

    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
    fields = {}
    if title:
        fields["title"] = {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                           "evidence": {"method": "COLLECTOR", "source": "OZON"}}

    price_candidates = []
    if price_minor:
        price_candidates.append({
            "role": "regular",
            "amount": f"{price_minor // 100}.{price_minor % 100:02d}",
            "amount_minor": price_minor,
            "currency": "RUB",
            "visible_label": f"{price_minor // 100} ₽",
        })

    return {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "OZON",
        "observation": {
            "source": "OZON",
            "id": lid,
            "url": url,
            "observed_at": now,
            "status": "PARTIAL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": "IN_STOCK" if price_minor else "UNKNOWN",
            "category_path": None,
        },
    }


# ============================================================
# 3. AVITO
# ============================================================
def probe_avito(app: MockApp) -> dict:
    log.info("=" * 60)
    log.info("AVITO probe starting")
    results = {"source": "AVITO", "items": [], "inbox_accepted": 0}

    from r52.collectors.avito import AvitoCollector
    col = AvitoCollector(data_dir=None, use_proxies=False)

    # Known/likely-valid Avito ad IDs (short numeric)
    listing_ids = ["1154238761", "1148902345"]
    for lid in listing_ids:
        url = col.build_url(lid)
        log.info("AVITO %s: %s", lid, url)
        try:
            obs = col.collect_product(lid, url, title_hint="")
        except Exception as e:
            log.warning("AVITO %s: collect_product error: %s", lid, e)
            obs = None

        if obs:
            data_bytes = json.dumps(obs, ensure_ascii=False).encode("utf-8")
            fname = f"jarvis-eye-avito-{lid}.json"
            item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
            log.info("AVITO %s: inbox → %s", lid, item.get("status"))
            if item["status"] == "PENDING":
                try:
                    result = app.inbox.accept(item["id"])
                    results["inbox_accepted"] += 1
                    results["items"].append({
                        "listing_id": lid,
                        "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:80],
                        "product_id": result.get("product_id"),
                    })
                except Exception as e:
                    log.warning("AVITO %s: accept error: %s", lid, e)
                    results["items"].append({"listing_id": lid, "error": str(e)})
            else:
                results["items"].append({"listing_id": lid, "status": item["status"],
                                         "error_code": item.get("error_code")})
        else:
            # Direct SSR fallback
            log.info("AVITO %s: trying direct SSR", lid)
            obs2 = _avito_direct(lid)
            if obs2:
                data_bytes = json.dumps(obs2, ensure_ascii=False).encode("utf-8")
                fname = f"jarvis-eye-avito-s-{lid}.json"
                item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
                if item["status"] == "PENDING":
                    result = app.inbox.accept(item["id"])
                    results["inbox_accepted"] += 1
                    results["items"].append({
                        "listing_id": lid, "via": "direct_ssr",
                        "title": obs2["observation"].get("fields", {}).get("title", {}).get("value", "")[:80],
                        "product_id": result.get("product_id"),
                    })
                else:
                    results["items"].append({"listing_id": lid, "status": item["status"]})
            else:
                results["items"].append({"listing_id": lid, "status": "NO_DATA"})

    results["collector_stats"] = col.stats
    return results


def _avito_direct(lid: str) -> Optional[dict]:
    """Direct curl_cffi for Avito (www + m)."""
    from curl_cffi import requests as cffi_req
    urls = [
        f"https://www.avito.ru/ad/{lid}",
        f"https://m.avito.ru/ad/{lid}",
    ]
    for url in urls:
        try:
            resp = cffi_req.get(url, impersonate="chrome124", timeout=12, headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
        except Exception as e:
            log.info("AVITO %s: %s err: %s", lid, url, e)
            continue

        if resp.status_code not in (200, 301, 302):
            log.info("AVITO %s: %s → HTTP %d", lid, url, resp.status_code)
            continue

        html = resp.text
        if len(html) < 500:
            continue

        # CAPTCHA check
        if "bot-challenge" in html.lower() or "captcha" in html.lower()[:3000]:
            log.info("AVITO %s: CAPTCHA/bot-challenge detected", lid)
            continue

        # JSON-LD
        title = None
        price_minor = None
        for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(block.strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    t = item.get("@type", "")
                    if isinstance(t, str) and "Product" in t:
                        title = item.get("name")
                        offer = item.get("offers", {})
                        if isinstance(offer, dict):
                            p = offer.get("price")
                            if p:
                                price_minor = int(round(float(p) * 100))
                        break
            except (json.JSONDecodeError, ValueError):
                continue

        # og:title fallback
        if not title:
            m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                title = m.group(1)

        # Price from HTML
        if not price_minor:
            m = re.search(r'class="[^"]*(?:price|offer)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)', html, re.I | re.DOTALL)
            if m:
                try:
                    amt = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
                    price_minor = int(round(float(amt) * 100))
                except ValueError:
                    pass

        if not title and not price_minor:
            continue

        # Media
        media_items = []
        for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*(?:avatars\.mmedia\.avito\.st|l16\.avito\.st|l26\.avito\.st)/[^"\']+)["\']', html, re.I):
            media_items.append({"type": "image", "source_url": m.group(1), "image_data": None})
            if len(media_items) >= 10:
                break

        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        fields = {}
        if title:
            fields["title"] = {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                               "evidence": {"method": "COLLECTOR", "source": "AVITO"}}

        price_candidates = []
        if price_minor:
            price_candidates.append({
                "role": "regular",
                "amount": f"{price_minor // 100}.{price_minor % 100:02d}",
                "amount_minor": price_minor,
                "currency": "RUB",
                "visible_label": f"{price_minor // 100} ₽",
            })

        return {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "AVITO",
            "observation": {
                "source": "AVITO",
                "id": lid,
                "url": url,
                "observed_at": now,
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": resp.status_code,
                "fields": fields,
                "price_candidates": price_candidates,
                "media": {"items": media_items},
                "availability": "IN_STOCK" if price_minor else "UNKNOWN",
                "category_path": None,
            },
        }

    return None


# ============================================================
# 4. FARPOST
# ============================================================
def probe_farpost(app: MockApp) -> dict:
    log.info("=" * 60)
    log.info("FARPOST probe starting")
    results = {"source": "FARPOST", "items": [], "inbox_accepted": 0}

    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=None, use_proxies=False)

    listing_ids = ["127622540", "130693618"]
    for lid in listing_ids:
        url = col.build_url(lid)
        log.info("FARPOST %s: %s", lid, url)
        try:
            obs = col.collect_product(lid, url, title_hint="")
        except Exception as e:
            log.warning("FARPOST %s: collect_product error: %s", lid, e)
            obs = None

        if obs:
            data_bytes = json.dumps(obs, ensure_ascii=False).encode("utf-8")
            fname = f"jarvis-eye-farpost-{lid}.json"
            item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
            log.info("FARPOST %s: inbox → %s", lid, item.get("status"))
            if item["status"] == "PENDING":
                try:
                    result = app.inbox.accept(item["id"])
                    results["inbox_accepted"] += 1
                    results["items"].append({
                        "listing_id": lid,
                        "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:80],
                        "product_id": result.get("product_id"),
                    })
                except Exception as e:
                    log.warning("FARPOST %s: accept error: %s", lid, e)
                    results["items"].append({"listing_id": lid, "error": str(e)})
            else:
                results["items"].append({"listing_id": lid, "status": item["status"],
                                         "error_code": item.get("error_code")})
        else:
            results["items"].append({"listing_id": lid, "status": "NO_DATA"})

    results["collector_stats"] = col.stats
    return results


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("probe_v90 starting — %s", datetime.now().isoformat())
    t0 = time.monotonic()

    counts_before = db_counts(DB_PATH)
    log.info("SQLite BEFORE: %s", counts_before)

    app = make_app()
    app.inbox = Inbox(app)

    all_results = {}

    # Run all 4 probes
    for name, fn in [("WB", probe_wb), ("OZON", probe_ozon),
                     ("AVITO", probe_avito), ("FARPOST", probe_farpost)]:
        try:
            all_results[name] = fn(app)
        except Exception as e:
            log.exception("Probe %s crashed: %s", name, e)
            all_results[name] = {"source": name, "error": str(e), "items": [], "inbox_accepted": 0}
        time.sleep(2)

    counts_after = db_counts(DB_PATH)
    log.info("SQLite AFTER:  %s", counts_after)

    elapsed = time.monotonic() - t0
    summary = {
        "probe": "v90",
        "started_at": datetime.now().isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "sqlite_before": counts_before,
        "sqlite_after": counts_after,
        "delta": {k: counts_after.get(k, 0) - counts_before.get(k, 0) for k in counts_before},
        "marketplaces": all_results,
        "total_inbox_accepted": sum(r.get("inbox_accepted", 0) for r in all_results.values()),
    }

    # Write results
    out_json = ROOT / "probe_v90_result.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Results → %s", out_json)

    # Print summary
    log.info("=" * 60)
    log.info("SUMMARY v90:")
    for name, r in all_results.items():
        accepted = r.get("inbox_accepted", 0)
        items = r.get("items", [])
        log.info("  %-8s: %d accepted, %d items", name, accepted, len(items))
        for it in items:
            log.info("    %s", json.dumps(it, ensure_ascii=False)[:120])
    log.info("  Delta: %s", summary["delta"])
    log.info("  Total elapsed: %.1fs", elapsed)

    return 0


if __name__ == "__main__":
    sys.exit(main())
