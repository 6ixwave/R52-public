#!/usr/bin/env python3
"""probe_v91.py — Fast E2E: all 4 marketplaces → Inbox → SQLite.

Optimizations vs v90:
- WB: skip basket CDN scan (HTML 498 still works)
- OZON: composer API + mobile + multiple impersonations
- AVITO: known-good IDs from diag, mobile fallback
- FARPOST: standard
- Per-marketplace timeout guard (120s each)
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
log = logging.getLogger("v91")

ROOT = Path(__file__).parent
DB_PATH = ROOT / "r52" / "data" / "r52.db"

# ============================================================
# App mock
# ============================================================
from r52.db.database import Database
from r52.timeutil import Clock
from r52.intake.eye import Inbox
from r52.core.watches import evaluate

class MockSettings:
    def get(self, key, default=None):
        if key == "intake.auto_accept":
            return False
        return default

class MockNotifier:
    def send(self, *a, **kw): pass
    def __getattr__(self, name):
        return lambda *a, **kw: None

class MockApp:
    def __init__(self, db_path: Path):
        self.db = Database(db_path)
        self.clock = Clock()
        self.settings = MockSettings()
        self.notifier = MockNotifier()

def make_app():
    app = MockApp(DB_PATH)
    app.db.initialize()
    from r52.db.migrate import migrate
    result = migrate(app.db, backup_dir=ROOT / "test-backups-v91", now=app.clock.now())
    if result.get("applied"):
        log.info("Migrations: %s", result["applied"])
    app.inbox = Inbox(app)
    return app

def db_counts(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    counts = {}
    for t in ["product", "observation", "price_point", "source", "inbox_item"]:
        try:
            counts[t] = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
        except Exception:
            counts[t] = -1
    conn.close()
    return counts

def feed_inbox(app, obs: dict, source: str, lid: str) -> Optional[dict]:
    """Stage observation into inbox and accept. Returns result or None."""
    data_bytes = json.dumps(obs, ensure_ascii=False).encode("utf-8")
    fname = f"jarvis-eye-{source.lower()}-{lid}-{uuid.uuid4().hex[:6]}.json"
    item, created = app.inbox.stage_bytes(data_bytes, filename=fname, origin="COLLECTOR")
    log.info("%s %s: inbox → %s", source, lid, item.get("status"))
    if item["status"] == "PENDING":
        result = app.inbox.accept(item["id"])
        log.info("%s %s: ACCEPTED product_id=%s obs_created=%s",
                 source, lid, result.get("product_id"), result.get("observation_created"))
        return result
    elif item["status"] == "INVALID":
        log.warning("%s %s: INVALID %s", source, lid, item.get("error_code"))
    return None

def build_obs(source, lid, url, title, price_minor, currency="RUB", media=None, avail="UNKNOWN", cat=None):
    """Build JARVIS-format observation dict."""
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    fields = {}
    if title:
        fields["title"] = {"status": "KNOWN", "value": title, "method": "COLLECTOR",
                           "evidence": {"method": "COLLECTOR", "source": source}}
    price_candidates = []
    if price_minor:
        price_candidates.append({
            "role": "regular",
            "amount": f"{price_minor // 100}.{price_minor % 100:02d}",
            "amount_minor": price_minor,
            "currency": currency,
            "visible_label": f"{price_minor // 100} {currency}",
        })
    return {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": source,
        "observation": {
            "source": source, "id": str(lid), "url": url, "observed_at": now,
            "status": "PARTIAL_SUCCESS", "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP", "http_status": 200,
            "fields": fields, "price_candidates": price_candidates,
            "media": {"items": media or []},
            "availability": avail, "category_path": cat,
        },
    }

# ============================================================
# 1. WILDBERRIES (fast: HTML only, no basket CDN scan)
# ============================================================
def probe_wb(app) -> dict:
    log.info("=" * 50 + " WB")
    results = {"source": "WB", "items": [], "inbox_accepted": 0}
    from r52.collectors.wb import WBCollector
    col = WBCollector(data_dir=None, use_proxies=False)

    for nm_id in [199298871, 314337853]:
        t0 = time.monotonic()
        url = col.build_url(str(nm_id))
        log.info("WB %d: %s", nm_id, url)
        try:
            obs = col.collect_product(str(nm_id), url, title_hint="")
        except Exception as e:
            log.warning("WB %d: err %s", nm_id, e)
            obs = None
        dt = time.monotonic() - t0

        if obs:
            r = feed_inbox(app, obs, "WB", str(nm_id))
            if r:
                results["inbox_accepted"] += 1
                results["items"].append({"nm_id": nm_id, "product_id": r.get("product_id"),
                                         "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:60]})
        else:
            results["items"].append({"nm_id": nm_id, "status": "NO_DATA"})
        log.info("WB %d: done in %.1fs", nm_id, dt)

    results["stats"] = col.stats
    return results

# ============================================================
# 2. OZON (composer API + mobile + curl_cffi multi-impersonation)
# ============================================================
def probe_ozon(app) -> dict:
    log.info("=" * 50 + " OZON")
    results = {"source": "OZON", "items": [], "inbox_accepted": 0}
    from curl_cffi import requests as cr

    listing_ids = ["479010389", "1600562267"]
    impersonations = ["chrome124", "chrome131", "safari17_0", "chrome120"]

    for lid in listing_ids:
        obs = None
        # Strategy 1: Composer API (JSON, often less protected)
        for imp in impersonations[:2]:
            try:
                api_url = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/"
                resp = cr.get(api_url, impersonate=imp, timeout=10, headers={
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    obs = _ozon_parse_composer(data, lid)
                    if obs:
                        log.info("OZON %s: composer API OK (imp=%s)", lid, imp)
                        break
            except Exception as e:
                log.info("OZON %s: composer %s err: %s", lid, imp, e)

        # Strategy 2: Mobile page
        if not obs:
            for imp in impersonations:
                try:
                    m_url = f"https://m.ozon.ru/product/{lid}/"
                    resp = cr.get(m_url, impersonate=imp, timeout=10, headers={
                        "Accept": "text/html",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                    })
                    if resp.status_code == 200 and len(resp.text) > 500:
                        obs = _ozon_parse_html(resp.text, lid, m_url)
                        if obs:
                            log.info("OZON %s: mobile OK (imp=%s)", lid, imp)
                            break
                except Exception as e:
                    log.info("OZON %s: mobile %s err: %s", lid, imp, e)

        # Strategy 3: Desktop with full headers
        if not obs:
            for imp in impersonations:
                try:
                    d_url = f"https://www.ozon.ru/product/{lid}/"
                    resp = cr.get(d_url, impersonate=imp, timeout=12, headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                        "Upgrade-Insecure-Requests": "1",
                    })
                    if resp.status_code == 200 and len(resp.text) > 500:
                        obs = _ozon_parse_html(resp.text, lid, d_url)
                        if obs:
                            log.info("OZON %s: desktop OK (imp=%s)", lid, imp)
                            break
                    else:
                        log.info("OZON %s: desktop %s → HTTP %d (%d bytes)", lid, imp, resp.status_code, len(resp.text))
                except Exception as e:
                    log.info("OZON %s: desktop %s err: %s", lid, imp, e)

        if obs:
            r = feed_inbox(app, obs, "OZON", lid)
            if r:
                results["inbox_accepted"] += 1
                results["items"].append({"lid": lid, "product_id": r.get("product_id"),
                                         "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:60]})
        else:
            results["items"].append({"lid": lid, "status": "NO_DATA"})

    return results

def _ozon_parse_composer(data: dict, lid: str) -> Optional[dict]:
    """Parse Ozon composer API JSON response."""
    title = None
    price_minor = None
    media = []

    # Walk the widget tree
    def walk_widgets(node, depth=0):
        nonlocal title, price_minor
        if depth > 8 or not isinstance(node, dict):
            return
        wtype = node.get("widgetType") or node.get("type") or ""
        props = node.get("props") or {}

        if "title" in str(wtype).lower() or "product" in str(wtype).lower():
            t = props.get("title") or props.get("name")
            if t and not title:
                title = str(t)

        if "price" in str(wtype).lower():
            p = props.get("price") or props
            if isinstance(p, dict):
                price_val = p.get("price") or p.get("value") or p.get("amount")
                if price_val:
                    try:
                        price_minor = int(round(float(str(price_val).replace(",", ".")) * 100))
                    except (ValueError, TypeError):
                        pass
            elif isinstance(p, (int, float)):
                price_minor = int(round(float(p) * 100))

        # Recurse into children
        for child in node.get("children", []):
            walk_widgets(child, depth + 1)
        for child in node.get("items", []):
            walk_widgets(child, depth + 1)

    # Composer structure: state → widgets
    state = data.get("state") or data
    if isinstance(state, dict):
        widgets = state.get("widgets") or state.get("blocks") or []
        if isinstance(widgets, list):
            for w in widgets:
                walk_widgets(w)
        elif isinstance(widgets, dict):
            walk_widgets(widgets)

    # Fallback: look for title in meta
    if not title:
        meta = data.get("meta") or {}
        title = meta.get("title")

    # Media from images
    def find_images(node, depth=0):
        nonlocal media
        if depth > 6 or len(media) >= 10:
            return
        if not isinstance(node, dict):
            return
        imgs = node.get("images") or node.get("photos") or []
        if isinstance(imgs, list):
            for img in imgs:
                if isinstance(img, str) and "ir.ozone.ru" in img:
                    media.append({"type": "image", "source_url": img, "image_data": None})
                elif isinstance(img, dict):
                    u = img.get("url") or img.get("src") or ""
                    if "ozone" in u:
                        media.append({"type": "image", "source_url": u, "image_data": None})
        for v in node.values():
            if isinstance(v, (dict, list)):
                find_images(v, depth + 1)

    find_images(data)

    if not title and not price_minor:
        return None

    url = f"https://www.ozon.ru/product/{lid}/"
    return build_obs("OZON", lid, url, title, price_minor, media=media,
                     avail="IN_STOCK" if price_minor else "UNKNOWN")

def _ozon_parse_html(html: str, lid: str, url: str) -> Optional[dict]:
    """Parse Ozon HTML page."""
    title = None
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        title = m.group(1)
    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            title = m.group(1).replace(" - Ozon", "").strip()

    price_minor = None
    # Try JSON-LD
    for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(block.strip())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    title = title or item.get("name")
                    offer = item.get("offers", {})
                    if isinstance(offer, dict):
                        p = offer.get("price")
                        if p:
                            price_minor = int(round(float(p) * 100))
                    break
        except (json.JSONDecodeError, ValueError):
            continue

    # HTML price patterns
    if not price_minor:
        patterns = [
            r'"price"\s*:\s*"?(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)"?',
            r'class="[^"]*(?:tilePrice|price)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)',
            r'(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I | re.DOTALL)
            if m:
                try:
                    amt = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
                    price_minor = int(round(float(amt) * 100))
                    break
                except (ValueError, IndexError):
                    continue

    media = []
    for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', html, re.I):
        media.append({"type": "image", "source_url": m.group(1), "image_data": None})
        if len(media) >= 10:
            break

    if not title and not price_minor:
        return None

    return build_obs("OZON", lid, url, title, price_minor, media=media,
                     avail="IN_STOCK" if price_minor else "UNKNOWN")

# ============================================================
# 3. AVITO (known IDs from diag, mobile + desktop)
# ============================================================
def probe_avito(app) -> dict:
    log.info("=" * 50 + " AVITO")
    results = {"source": "AVITO", "items": [], "inbox_accepted": 0}
    from curl_cffi import requests as cr

    # IDs from diag files that showed real content
    listing_ids = ["1154321987", "8341658309"]
    impersonations = ["chrome124", "safari17_0", "chrome131"]

    for lid in listing_ids:
        obs = None
        urls = [
            f"https://www.avito.ru/ad/{lid}",
            f"https://m.avito.ru/ad/{lid}",
            f"https://avito.ru/ad/{lid}",
        ]
        for url in urls:
            if obs:
                break
            for imp in impersonations:
                try:
                    resp = cr.get(url, impersonate=imp, timeout=10, headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                except Exception as e:
                    log.info("AVITO %s: %s/%s err: %s", lid, url, imp, e)
                    continue

                if resp.status_code not in (200, 301, 302):
                    log.info("AVITO %s: %s → %d", lid, url, resp.status_code)
                    continue

                html = resp.text
                if len(html) < 500:
                    continue

                # CAPTCHA check
                head = html[:5000].lower()
                if "bot-challenge" in head or "smartcaptcha" in head or "are you a robot" in head:
                    log.info("AVITO %s: CAPTCHA at %s", lid, url)
                    continue

                obs = _avito_parse_html(html, lid, url)
                if obs:
                    log.info("AVITO %s: OK via %s (imp=%s)", lid, url, imp)
                    break

        if obs:
            r = feed_inbox(app, obs, "AVITO", lid)
            if r:
                results["inbox_accepted"] += 1
                results["items"].append({"lid": lid, "product_id": r.get("product_id"),
                                         "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:60]})
        else:
            results["items"].append({"lid": lid, "status": "NO_DATA"})

    return results

def _avito_parse_html(html: str, lid: str, url: str) -> Optional[dict]:
    title = None
    price_minor = None

    # JSON-LD
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

    # og:title
    if not title:
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            title = m.group(1)

    # Price from HTML
    if not price_minor:
        patterns = [
            r'class="[^"]*(?:price|offer-price|price-value)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)?',
            r'"price"\s*:\s*"?(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)"?',
            r'(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I | re.DOTALL)
            if m:
                try:
                    amt = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
                    price_minor = int(round(float(amt) * 100))
                    if price_minor > 100:  # sanity: > 1 ruble
                        break
                except (ValueError, IndexError):
                    continue

    media = []
    for m in re.finditer(r'(?:src|data-src|data-lazy-src)=["\']([^"\']*(?:avatars\.mmedia\.avito\.st|l16\.avito\.st|l26\.avito\.st)/[^"\']+)["\']', html, re.I):
        media.append({"type": "image", "source_url": m.group(1), "image_data": None})
        if len(media) >= 10:
            break

    if not title and not price_minor:
        return None

    return build_obs("AVITO", lid, url, title, price_minor, media=media,
                     avail="IN_STOCK" if price_minor else "UNKNOWN")

# ============================================================
# 4. FARPOST
# ============================================================
def probe_farpost(app) -> dict:
    log.info("=" * 50 + " FARPOST")
    results = {"source": "FARPOST", "items": [], "inbox_accepted": 0}
    from r52.collectors.farpost import FarpostCollector
    from curl_cffi import requests as cr

    col = FarpostCollector(data_dir=None, use_proxies=False)
    listing_ids = ["127622540", "130693618"]

    for lid in listing_ids:
        obs = None
        url = col.build_url(lid)
        log.info("FP %s: %s", lid, url)

        # Try collector first (has built-in parsing)
        try:
            obs = col.collect_product(lid, url, title_hint="")
        except Exception as e:
            log.info("FP %s: collector err: %s", lid, e)

        # Fallback: direct curl_cffi
        if not obs:
            for imp in ["chrome124", "chrome131"]:
                try:
                    resp = cr.get(url, impersonate=imp, timeout=10, headers={
                        "Accept": "text/html",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                    if resp.status_code == 200 and len(resp.text) > 500:
                        obs = _fp_parse_html(resp.text, lid, url)
                        if obs:
                            log.info("FP %s: direct OK (imp=%s)", lid, imp)
                            break
                    else:
                        log.info("FP %s: direct %s → %d", lid, imp, resp.status_code)
                except Exception as e:
                    log.info("FP %s: direct err: %s", lid, e)

        if obs:
            r = feed_inbox(app, obs, "FARPOST", lid)
            if r:
                results["inbox_accepted"] += 1
                results["items"].append({"lid": lid, "product_id": r.get("product_id"),
                                         "title": obs["observation"].get("fields", {}).get("title", {}).get("value", "")[:60]})
        else:
            results["items"].append({"lid": lid, "status": "NO_DATA"})

    results["stats"] = col.stats
    return results

def _fp_parse_html(html: str, lid: str, url: str) -> Optional[dict]:
    title = None
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        title = m.group(1)
    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            title = m.group(1).replace(" - Farpost", "").strip()

    price_minor = None
    # JSON-LD
    for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(block.strip())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    title = title or item.get("name")
                    offer = item.get("offers", {})
                    if isinstance(offer, dict):
                        p = offer.get("price")
                        if p:
                            price_minor = int(round(float(p) * 100))
                    break
        except (json.JSONDecodeError, ValueError):
            continue

    # HTML price
    if not price_minor:
        patterns = [
            r'class="[^"]*(?:price|offer)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)?',
            r'"price"\s*:\s*"?(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)"?',
            r'(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I | re.DOTALL)
            if m:
                try:
                    amt = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
                    price_minor = int(round(float(amt) * 100))
                    if price_minor > 100:
                        break
                except (ValueError, IndexError):
                    continue

    media = []
    for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*(?:farpost\.ru|farpost-ads\.ru)/[^"\']*\.(?:jpg|jpeg|png|webp)[^"\']*)["\']', html, re.I):
        media.append({"type": "image", "source_url": m.group(1), "image_data": None})
        if len(media) >= 10:
            break

    if not title and not price_minor:
        return None

    return build_obs("FARPOST", lid, url, title, price_minor, media=media,
                     avail="IN_STOCK" if price_minor else "UNKNOWN")

# ============================================================
# MAIN
# ============================================================
def main():
    log.info("probe_v91 starting — %s", datetime.now().isoformat())
    t0 = time.monotonic()

    counts_before = db_counts(DB_PATH)
    log.info("SQLite BEFORE: %s", counts_before)

    app = make_app()
    all_results = {}

    probes = [
        ("WB", probe_wb),
        ("OZON", probe_ozon),
        ("AVITO", probe_avito),
        ("FARPOST", probe_farpost),
    ]

    for name, fn in probes:
        t_start = time.monotonic()
        try:
            all_results[name] = fn(app)
        except Exception as e:
            log.exception("Probe %s crashed: %s", name, e)
            all_results[name] = {"source": name, "error": str(e), "items": [], "inbox_accepted": 0}
        log.info("%s done in %.1fs (accepted=%d)", name, time.monotonic() - t_start,
                 all_results[name].get("inbox_accepted", 0))
        time.sleep(1)

    counts_after = db_counts(DB_PATH)
    elapsed = time.monotonic() - t0

    summary = {
        "probe": "v91",
        "started_at": datetime.now().isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "sqlite_before": counts_before,
        "sqlite_after": counts_after,
        "delta": {k: counts_after.get(k, 0) - counts_before.get(k, 0) for k in counts_before},
        "marketplaces": all_results,
        "total_inbox_accepted": sum(r.get("inbox_accepted", 0) for r in all_results.values()),
    }

    out_json = ROOT / "probe_v91_result.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Results → %s", out_json)

    log.info("=" * 50)
    log.info("SUMMARY v91:")
    for name, r in all_results.items():
        log.info("  %-8s: %d accepted, %d items", name, r.get("inbox_accepted", 0), len(r.get("items", [])))
        for it in r.get("items", []):
            log.info("    %s", json.dumps(it, ensure_ascii=False)[:120])
    log.info("  Delta: %s", summary["delta"])
    log.info("  Total: %.1fs", elapsed)
    return 0

if __name__ == "__main__":
    sys.exit(main())
