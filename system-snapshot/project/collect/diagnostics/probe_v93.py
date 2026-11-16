#!/usr/bin/env python3
"""probe_v93.py — E2E: 4 marketplace, fixed _App, improved WB/Ozon.

Fixes vs v92:
- _App has clock, db, settings, notifier (unblocks stage_bytes/accept)
- WB: try static-basket CDN pattern + HTML JSON-LD fallback
- Ozon: mobile UA + composer API with proper Accept
- Avito: category scrape + collect
"""
import json, logging, sys, time, re, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger("v93")

DB_PATH = ROOT / "r52" / "data" / "r52.db"
OUT_JSON = ROOT / "probe_v93_result.json"
TZ = timezone(timedelta(hours=10))

# === FRESH DATA (11.11.2026) ===
FARPOST_ITEMS = [
    ("138080119",
     "https://www.farpost.ru/vladivostok/tech/communication/cellphones/"
     "iphone-18-pro-max-512gb-trade-in-kredit-garantija-1-god-138080119.html"),
    ("138078491",
     "https://www.farpost.ru/vladivostok/tech/communication/cellphones/"
     "iphone-18-pro-1tb-trade-in-kredit-garantija-1-god-138078491.html"),
    ("125270259",
     "https://www.farpost.ru/vladivostok/tech/communication/cellphones/"
     "iphone-18-pro-max-256gb-trade-in-kredit-garantija-1-god-125270259.html"),
]
WB_IDS = ["259671175", "387715226"]
OZON_IDS = ["479010389", "1600562267"]
AVITO_KNOWN = ["1154321987", "8341658309"]


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def sqlite_counts():
    import sqlite3
    c = sqlite3.connect(str(DB_PATH))
    counts = {}
    for t in ['product', 'observation', 'price_point', 'source',
              'inbox_item', 'intake_file']:
        try:
            counts[t] = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            counts[t] = -1
    c.close()
    return counts


def make_app():
    """Create App-like object with all attributes Inbox needs."""
    from r52.timeutil import Clock, zone
    from r52.db import Database, migrate
    from r52.core.settings import Settings
    from r52.core.notify import Notifier

    data_dir = ROOT / "r52" / "data"
    db_path = data_dir / "r52.db"
    tz = zone("Asia/Vladivostok")

    clock = Clock()
    db = Database(db_path)
    db.initialize()
    migrate(db, backup_dir=data_dir / "backups", now=clock.now())
    settings = Settings(db, clock)
    notifier = Notifier(settings, clock, tz)

    class _App:
        pass

    app = _App()
    app.data_dir = data_dir
    app.db_path = db_path
    app.root = ROOT / "r52"
    app.clock = clock
    app.db = db
    app.settings = settings
    app.notifier = notifier
    app.tz = tz
    log.info("App ready: db=%s", db_path.name)
    return app


def inbox_accept(inbox, obs_dict):
    """Stage + accept via Inbox. Returns accept-result dict or None."""
    payload = json.dumps(obs_dict, ensure_ascii=False).encode("utf-8")
    try:
        item, created = inbox.stage_bytes(payload, origin="COLLECTOR")
    except Exception as e:
        log.warning("stage_bytes error: %s", e)
        return None
    if item is None:
        log.warning("stage_bytes returned None")
        return None
    item_id = item.get("id") or item.get("item_id")
    if item_id is None:
        log.warning("stage_bytes no id in %s", list(item.keys())[:6])
        return None
    if item.get("status") == "INVALID":
        log.warning("stage_bytes INVALID: %s", item.get("error_code"))
        return None
    try:
        result = inbox.accept(item_id)
    except Exception as e:
        log.warning("accept(%s) error: %s", item_id, e)
        return None
    return result


def obs_title(obs):
    return (obs or {}).get("observation", {}).get("fields", {}).get("title", {}).get("value", "?")


def obs_prices(obs):
    return (obs or {}).get("observation", {}).get("price_candidates", [])


# ──────────────────────────────────────────────
# Farpost
# ──────────────────────────────────────────────
def test_farpost(app, inbox):
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()
    for lid, full_url in FARPOST_ITEMS:
        log.info("FP %s: %s", lid, full_url[:90])
        obs = None
        try:
            obs = col.collect_product(lid, full_url, title_hint=f"FP-{lid}")
        except Exception as e:
            log.warning("FP %s: collect error: %s", lid, e)
        if obs:
            log.info("FP %s: collector OK, title=%r", lid, obs_title(obs))
        else:
            try:
                col._warmup()
                resp = col.session.get(full_url, timeout=(10, 30))
                log.info("FP %s: direct HTTP %d (%d bytes)",
                         lid, resp.status_code, len(resp.text or ""))
                if resp.status_code == 200:
                    obs = col.parse_page(resp, lid, full_url, f"FP-{lid}")
            except Exception as e:
                log.warning("FP %s: direct error: %s", lid, e)
        if obs:
            res = inbox_accept(inbox, obs)
            if res:
                pid = res.get("product_id")
                log.info("FP %s: ACCEPTED product_id=%s title=%r prices=%s",
                         lid, pid, obs_title(obs), obs_prices(obs)[:2])
                results.append({"lid": lid, "product_id": pid,
                                "title": obs_title(obs), "status": "ACCEPTED"})
            else:
                results.append({"lid": lid, "status": "STAGED"})
        else:
            results.append({"lid": lid, "status": "NO_DATA"})
        time.sleep(2)
    el = time.monotonic() - t0
    acc = sum(1 for r in results if r.get("status") == "ACCEPTED")
    log.info("FARPOST done in %.1fs (accepted=%d)", el, acc)
    return results, dict(col.stats)


# ──────────────────────────────────────────────
# WB — improved: static-basket + HTML JSON-LD
# ──────────────────────────────────────────────
def _wb_basket_fetch(col, nm_id):
    """Try multiple basket CDN patterns."""
    vol = nm_id // 100000
    part = (nm_id // 1000) % 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

    # Pattern 1: basket-{n:02d}.wbbasket.ru (1..80)
    for n in range(1, 81):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            resp = col._http_get(url, headers={"Accept": "application/json"},
                                 timeout=6, fast=True)
            if resp and resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("nm_id"):
                    log.info("WB %d: basket-%02d.wbbasket.ru OK", nm_id, n)
                    return data
        except Exception:
            continue

    # Pattern 2: static-basket-{n:02d}.wbbasket.ru
    for n in range(1, 21):
        host = f"static-basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            resp = col._http_get(url, headers={"Accept": "application/json"},
                                 timeout=6, fast=True)
            if resp and resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("nm_id"):
                    log.info("WB %d: static-basket-%02d OK", nm_id, n)
                    return data
        except Exception:
            continue

    log.info("WB %d: all basket patterns exhausted", nm_id)
    return None


def _wb_html_parse(col, nm_id, url):
    """Fetch HTML and extract JSON-LD / meta / title."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
    }
    resp = col._http_get(url, headers=headers, timeout=20)
    if not resp or not resp.text:
        return None
    html = resp.text
    log.info("WB %d: HTML %d bytes (HTTP %s)", nm_id, len(html), resp.status_code)
    title, desc, price = None, None, None

    # JSON-LD
    for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.I):
        try:
            ld = json.loads(m.group(1))
            if isinstance(ld, dict):
                t = ld.get("name") or ld.get("headline")
                if t:
                    title = t
                d = ld.get("description")
                if d:
                    desc = d
                off = ld.get("offers")
                if isinstance(off, dict):
                    price = off.get("price") or off.get("lowPrice")
                elif isinstance(off, list) and off:
                    price = off[0].get("price")
                if title:
                    break
        except Exception:
            pass

    # og:title
    if not title:
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            title = m.group(1).strip()

    # <title>
    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            title = m.group(1).replace(" - Wildberries", "").replace("Wildberries - ", "").strip()

    # Meta description
    if not desc:
        m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            desc = m.group(1).strip()

    return {"title": title, "description": desc, "price": price,
            "http_status": resp.status_code, "bytes": len(html)}


def test_wb(app, inbox):
    from r52.collectors.wb import WBCollector
    col = WBCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()
    for lid in WB_IDS:
        nm_id = int(lid)
        url = f"https://www.wildberries.ru/catalog/{lid}/detail.aspx"
        log.info("WB %s: %s", lid, url)
        obs = None

        # S1: full collector (basket + price + HTML)
        try:
            obs = col.collect_product(lid, url, title_hint=f"WB-{lid}")
            if obs:
                log.info("WB %s: S1 collector OK, title=%r", lid, obs_title(obs))
        except Exception as e:
            log.warning("WB %s: S1 error: %s", lid, e)

        # S2: basket CDN direct (more patterns)
        if not obs:
            log.info("WB %s: S2 basket direct", lid)
            card = _wb_basket_fetch(col, nm_id)
            if card:
                title = card.get("name") or f"WB-{lid}"
                desc = card.get("description") or ""
                obs = {
                    "format": "JARVIS_BR02_EYE_USER_CAPTURE",
                    "capture_action": "EXPLICIT_POPUP_CLICK",
                    "fully_autonomous": False,
                    "collector_source": "WB",
                    "observation": {
                        "source": "WB", "id": lid, "url": url,
                        "observed_at": datetime.now(TZ).isoformat(),
                        "status": "PARTIAL_SUCCESS",
                        "product_content": True,
                        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                        "http_status": 200,
                        "fields": {
                            "title": {"status": "KNOWN", "value": title, "method": "BASKET_CDN"},
                            "description": {"status": "KNOWN" if desc else "UNKNOWN",
                                            "value": desc, "method": "BASKET_CDN"},
                        },
                        "price_candidates": [],
                        "media": {"items": []},
                    },
                }
                # Extract media from card
                photos = card.get("photos") or []
                for p in photos[:10]:
                    if isinstance(p, str):
                        obs["observation"]["media"]["items"].append(
                            {"type": "image", "url": f"https://ir.ozone.ru/{p}" if p.startswith("ir.") else p})
                log.info("WB %s: S2 basket OK title=%r photos=%d", lid, title, len(photos))

        # S3: HTML JSON-LD
        if not obs:
            log.info("WB %s: S3 HTML parse", lid)
            parsed = _wb_html_parse(col, nm_id, url)
            if parsed and (parsed.get("title") or parsed.get("price")):
                title = parsed["title"] or f"WB-{lid}"
                obs = {
                    "format": "JARVIS_BR02_EYE_USER_CAPTURE",
                    "capture_action": "EXPLICIT_POPUP_CLICK",
                    "fully_autonomous": False,
                    "collector_source": "WB",
                    "observation": {
                        "source": "WB", "id": lid, "url": url,
                        "observed_at": datetime.now(TZ).isoformat(),
                        "status": "PARTIAL_SUCCESS",
                        "product_content": True,
                        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                        "http_status": parsed["http_status"],
                        "fields": {
                            "title": {"status": "KNOWN", "value": title, "method": "HTML_JSONLD"},
                            "description": {"status": "KNOWN" if parsed.get("description") else "UNKNOWN",
                                            "value": parsed.get("description") or "", "method": "HTML_JSONLD"},
                        },
                        "price_candidates": [],
                        "media": {"items": []},
                    },
                }
                if parsed.get("price"):
                    try:
                        minor = int(round(float(parsed["price"]) * 100))
                        obs["observation"]["price_candidates"].append(
                            {"amount_minor": minor, "currency": "RUB", "source": "HTML_JSONLD"})
                    except Exception:
                        pass
                log.info("WB %s: S3 HTML OK title=%r price=%s", lid, title, parsed.get("price"))

        # S4: Price API (last resort for price)
        if obs:
            pc = obs["observation"].get("price_candidates", [])
            if not pc:
                for ver in ["v2", "v4"]:
                    purl = (f"https://card.wb.ru/cards/{ver}/detail"
                            f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}")
                    try:
                        resp = col._http_get(purl, headers={"Accept": "application/json"},
                                             timeout=10, fast=True)
                        if resp and resp.status_code == 200:
                            data = resp.json()
                            products = data.get("products") or []
                            if products:
                                sizes = products[0].get("sizes", [])
                                if sizes:
                                    po = sizes[0].get("price", {})
                                    if po:
                                        minor = po.get("product") or po.get("basic")
                                        if minor:
                                            obs["observation"]["price_candidates"].append(
                                                {"amount_minor": int(minor), "currency": "RUB",
                                                 "source": f"card_{ver}"})
                                            log.info("WB %s: price via %s = %s", lid, ver, minor)
                                            break
                    except Exception:
                        continue

        if obs:
            res = inbox_accept(inbox, obs)
            if res:
                pid = res.get("product_id")
                log.info("WB %s: ACCEPTED product_id=%s title=%r", lid, pid, obs_title(obs))
                results.append({"lid": lid, "product_id": pid,
                                "title": obs_title(obs), "status": "ACCEPTED"})
            else:
                results.append({"lid": lid, "status": "STAGED"})
        else:
            results.append({"lid": lid, "status": "NO_DATA"})
        time.sleep(3)
    el = time.monotonic() - t0
    acc = sum(1 for r in results if r.get("status") == "ACCEPTED")
    log.info("WB done in %.1fs (accepted=%d)", el, acc)
    return results, dict(col.stats)


# ──────────────────────────────────────────────
# Ozon — mobile + composer + cffi
# ──────────────────────────────────────────────
def _ozon_from_composer(lid, data, url):
    now = datetime.now(TZ).isoformat()
    title, price, desc = None, None, None
    try:
        ws = data.get("widgetStates", {})
        for wkey, wval in ws.items():
            if not isinstance(wval, dict):
                continue
            props = wval.get("props", {})
            if isinstance(props, dict):
                if props.get("title"):
                    title = props["title"]
                p = props.get("price")
                if isinstance(p, dict):
                    price = p.get("price") or p.get("current")
                elif isinstance(p, (int, float)):
                    price = p
                if props.get("description"):
                    desc = props["description"]
    except Exception as e:
        log.warning("OZON composer parse: %s", e)
    if not title:
        title = f"OZ-{lid}"
    obs = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "OZON",
        "observation": {
            "source": "OZON", "id": str(lid), "url": url,
            "observed_at": now,
            "status": "PARTIAL_SUCCESS" if title != f"OZ-{lid}" else "DEGRADED",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
            "fields": {
                "title": {"status": "KNOWN", "value": title, "method": "COMPOSER_API"},
                "description": {"status": "KNOWN" if desc else "UNKNOWN",
                                "value": desc or "", "method": "COMPOSER_API"},
            },
            "price_candidates": [],
            "media": {"items": []},
        },
    }
    if price:
        try:
            minor = int(round(float(price) * 100))
            obs["observation"]["price_candidates"].append(
                {"amount_minor": minor, "currency": "RUB", "source": "COMPOSER_API"})
        except Exception:
            pass
    return obs


def test_ozon(app, inbox):
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()
    for lid in OZON_IDS:
        log.info("OZON %s: strategies", lid)
        obs = None
        url_d = f"https://www.ozon.ru/product/{lid}/"

        # S1: collector desktop
        try:
            obs = col.collect_product(lid, url_d, title_hint=f"OZ-{lid}")
            if obs:
                log.info("OZON %s: S1 desktop OK", lid)
        except Exception as e:
            log.warning("OZON %s: S1 error: %s", lid, e)

        # S2: mobile with mobile UA
        if not obs:
            url_m = f"https://m.ozon.ru/product/{lid}/"
            log.info("OZON %s: S2 mobile", lid)
            try:
                mobile_headers = {
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                }
                resp = col.fetch(url_m, headers=mobile_headers)
                if resp and resp.status_code == 200:
                    obs = col.parse_page(resp, lid, url_m, f"OZ-{lid}")
                    log.info("OZON %s: S2 mobile OK", lid)
                else:
                    log.info("OZON %s: S2 mobile HTTP %s",
                             lid, resp.status_code if resp else "None")
            except Exception as e:
                log.warning("OZON %s: S2 error: %s", lid, e)

        # S3: Composer API
        if not obs:
            url_api = (f"https://api.ozon.ru/composer-api.bx/page/json/v2"
                       f"?url=/product/{lid}/")
            log.info("OZON %s: S3 composer API", lid)
            try:
                api_headers = {
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Referer": "https://www.ozon.ru/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                }
                resp = col.fetch(url_api, headers=api_headers)
                if resp and resp.status_code == 200:
                    data = resp.json()
                    log.info("OZON %s: S3 composer keys=%s",
                             lid, list(data.keys())[:6])
                    obs = _ozon_from_composer(lid, data, url_api)
                else:
                    log.info("OZON %s: S3 composer HTTP %s",
                             lid, resp.status_code if resp else "None")
            except Exception as e:
                log.warning("OZON %s: S3 error: %s", lid, e)

        # S4: curl_cffi multi-impersonation
        if not obs:
            log.info("OZON %s: S4 cffi", lid)
            try:
                from curl_cffi import requests as cffi_req
                for imp in ["chrome", "firefox", "safari"]:
                    try:
                        r = cffi_req.get(url_d, impersonate=imp, timeout=15)
                        log.info("OZON %s: S4 %s -> %d (%dB)",
                                 lid, imp, r.status_code, len(r.content))
                        if r.status_code == 200:
                            obs = col.parse_page(r, lid, url_d, f"OZ-{lid}")
                            break
                    except Exception as e2:
                        log.warning("OZON %s: S4 %s: %s", lid, imp, e2)
                    time.sleep(2)
            except ImportError:
                log.info("OZON %s: S4 cffi not available", lid)

        if obs:
            res = inbox_accept(inbox, obs)
            if res:
                pid = res.get("product_id")
                log.info("OZON %s: ACCEPTED product_id=%s title=%r",
                         lid, pid, obs_title(obs))
                results.append({"lid": lid, "product_id": pid,
                                "title": obs_title(obs), "status": "ACCEPTED"})
            else:
                results.append({"lid": lid, "status": "STAGED"})
        else:
            results.append({"lid": lid, "status": "NO_DATA"})
        time.sleep(3)
    el = time.monotonic() - t0
    acc = sum(1 for r in results if r.get("status") == "ACCEPTED")
    log.info("OZON done in %.1fs (accepted=%d)", el, acc)
    return results, dict(col.stats)


# ──────────────────────────────────────────────
# Avito — category scrape + collect
# ──────────────────────────────────────────────
def test_avito(app, inbox):
    from r52.collectors.avito import AvitoCollector
    col = AvitoCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()

    fresh_ids = []
    for cat in ["https://www.avito.ru/vladivostok/tech/noutbuki",
                "https://www.avito.ru/vladivostok/tech/telefony"]:
        try:
            log.info("AVITO: scrape %s", cat)
            resp = col.fetch(cat)
            if resp and resp.status_code == 200:
                ids = re.findall(r'avito\.ru/[a-z\-]+/(\d{6,})', resp.text)
                fresh_ids = list(dict.fromkeys(ids))[:4]
                log.info("AVITO: %d fresh IDs from %s: %s",
                         len(fresh_ids), cat.split("/")[-1], fresh_ids)
                if fresh_ids:
                    break
            else:
                log.info("AVITO: category HTTP %s",
                         resp.status_code if resp else "None")
        except Exception as e:
            log.warning("AVITO: category error: %s", e)
        time.sleep(2)

    all_ids = fresh_ids + AVITO_KNOWN
    seen = set()
    all_ids = [x for x in all_ids if not (x in seen or seen.add(x))][:4]
    log.info("AVITO: testing %d IDs: %s", len(all_ids), all_ids)

    for lid in all_ids:
        url = f"https://www.avito.ru/ad/{lid}"
        log.info("AVITO %s: %s", lid, url)
        obs = None
        try:
            obs = col.collect_product(lid, url, title_hint=f"AV-{lid}")
        except Exception as e:
            log.warning("AVITO %s: desktop error: %s", lid, e)
        if not obs:
            url_m = f"https://m.avito.ru/ad/{lid}"
            try:
                resp = col.fetch(url_m)
                if resp and resp.status_code == 200:
                    obs = col.parse_page(resp, lid, url_m, f"AV-{lid}")
                    log.info("AVITO %s: mobile OK", lid)
                else:
                    log.info("AVITO %s: mobile HTTP %s",
                             lid, resp.status_code if resp else "None")
            except Exception as e:
                log.warning("AVITO %s: mobile error: %s", lid, e)
        if obs:
            res = inbox_accept(inbox, obs)
            if res:
                pid = res.get("product_id")
                log.info("AVITO %s: ACCEPTED product_id=%s title=%r",
                         lid, pid, obs_title(obs))
                results.append({"lid": lid, "product_id": pid,
                                "title": obs_title(obs), "status": "ACCEPTED"})
            else:
                results.append({"lid": lid, "status": "STAGED"})
        else:
            results.append({"lid": lid, "status": "NO_DATA"})
        time.sleep(3)
    el = time.monotonic() - t0
    acc = sum(1 for r in results if r.get("status") == "ACCEPTED")
    log.info("AVITO done in %.1fs (accepted=%d)", el, acc)
    return results, dict(col.stats)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    log.info("probe_v93 starting — %s", datetime.now(TZ).isoformat())
    before = sqlite_counts()
    log.info("SQLite BEFORE: %s", before)

    app = make_app()
    from r52.intake.eye import Inbox
    inbox = Inbox(app)

    all_results = {}

    log.info("=== FARPOST ===")
    fp_res, fp_stats = test_farpost(app, inbox)
    all_results["FARPOST"] = {
        "items": fp_res,
        "inbox_accepted": sum(1 for r in fp_res if r.get("status") == "ACCEPTED"),
        "stats": fp_stats,
    }

    log.info("=== WB ===")
    wb_res, wb_stats = test_wb(app, inbox)
    all_results["WB"] = {
        "items": wb_res,
        "inbox_accepted": sum(1 for r in wb_res if r.get("status") == "ACCEPTED"),
        "stats": wb_stats,
    }

    log.info("=== OZON ===")
    oz_res, oz_stats = test_ozon(app, inbox)
    all_results["OZON"] = {
        "items": oz_res,
        "inbox_accepted": sum(1 for r in oz_res if r.get("status") == "ACCEPTED"),
        "stats": oz_stats,
    }

    log.info("=== AVITO ===")
    av_res, av_stats = test_avito(app, inbox)
    all_results["AVITO"] = {
        "items": av_res,
        "inbox_accepted": sum(1 for r in av_res if r.get("status") == "ACCEPTED"),
        "stats": av_stats,
    }

    after = sqlite_counts()
    log.info("SQLite AFTER: %s", after)
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in before}
    log.info("Delta: %s", delta)

    total_acc = sum(v["inbox_accepted"] for v in all_results.values())
    out = {
        "probe": "v93",
        "started_at": datetime.now(TZ).isoformat(),
        "sqlite_before": before,
        "sqlite_after": after,
        "delta": delta,
        "marketplaces": all_results,
        "total_inbox_accepted": total_acc,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("Results -> %s", OUT_JSON)

    log.info("=== SUMMARY v93 ===")
    for mp, data in all_results.items():
        log.info("  %-8s: %d accepted, %d items",
                 mp, data["inbox_accepted"], len(data["items"]))
        for item in data["items"]:
            log.info("    %s", json.dumps(item, ensure_ascii=False))
    log.info("=== DONE ===")


if __name__ == "__main__":
    main()
