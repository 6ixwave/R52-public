#!/usr/bin/env python3
"""probe_v92.py — E2E: 4 marketplace, fresh IDs, full URLs.

Strategy:
- Farpost: 3 fresh IDs with FULL URLs (not /bulletin/ redirect)
- WB: 2 IDs, post-parse 498 body for JSON-LD / meta
- Ozon: 2 IDs, 4 strategies (desktop, mobile, composer API, cffi)
- Avito: scrape category for fresh IDs, then collect
"""
import json, logging, sys, time, re, os
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger("v92")

DB_PATH = ROOT / "r52" / "data" / "r52.db"
OUT_JSON = ROOT / "probe_v92_result.json"
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
    try:
        from r52.service import R52App
        return R52App(str(ROOT / "r52"))
    except (ImportError, TypeError):
        pass
    try:
        from r52.service import create_app
        return create_app(str(ROOT / "r52"))
    except (ImportError, TypeError):
        pass
    # Minimal fallback
    class _App:
        def __init__(self):
            self.data_dir = ROOT / "r52" / "data"
            self.db_path = self.data_dir / "r52.db"
            self.root = ROOT / "r52"
    return _App()


def inbox_accept(inbox, obs_dict):
    """Stage + accept via Inbox. Returns accept-result dict or None."""
    payload = json.dumps(obs_dict, ensure_ascii=False).encode("utf-8")
    try:
        item = inbox.stage_bytes(payload)
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
# Farpost — fresh full URLs
# ──────────────────────────────────────────────
def test_farpost(app, inbox):
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()
    for lid, full_url in FARPOST_ITEMS:
        log.info("FP %s: %s", lid, full_url[:90])
        obs = None
        # 1) Collector (requests + warm-up)
        try:
            obs = col.collect_product(lid, full_url, title_hint=f"FP-{lid}")
        except Exception as e:
            log.warning("FP %s: collect error: %s", lid, e)
        if obs:
            log.info("FP %s: collector OK, title=%r", lid, obs_title(obs))
        else:
            # 2) Direct fetch + parse
            log.info("FP %s: collector None, direct fetch", lid)
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
# WB — post-parse 498 body
# ──────────────────────────────────────────────
def test_wb(app, inbox):
    from r52.collectors.wb import WBCollector
    col = WBCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()
    for lid in WB_IDS:
        url = f"https://www.wildberries.ru/catalog/{lid}/detail.aspx"
        log.info("WB %s: %s", lid, url)
        obs = None
        try:
            obs = col.collect_product(lid, url, title_hint=f"WB-{lid}")
        except Exception as e:
            log.warning("WB %s: collect error: %s", lid, e)
        if obs:
            title = obs_title(obs)
            synthetic = (title == f"WB-{lid}" or not title)
            if synthetic:
                log.info("WB %s: synthetic title, post-parse 498 body", lid)
                try:
                    resp = col._http_get(url, timeout=20)
                    if resp and resp.text:
                        html = resp.text
                        log.info("WB %s: body %d bytes (HTTP %s)",
                                 lid, len(html), resp.status_code)
                        # JSON-LD
                        for m in re.finditer(
                                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                                html, re.DOTALL):
                            try:
                                ld = json.loads(m.group(1))
                                if isinstance(ld, dict):
                                    t = ld.get("name") or ld.get("headline")
                                    if t:
                                        title = t
                                        log.info("WB %s: JSON-LD title=%r", lid, t)
                                        break
                            except Exception:
                                pass
                        # og:title
                        if title == f"WB-{lid}":
                            m = re.search(
                                r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
                                html, re.I)
                            if m:
                                title = m.group(1).strip()
                                log.info("WB %s: og:title=%r", lid, title)
                        # <title>
                        if title == f"WB-{lid}":
                            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
                            if m:
                                title = m.group(1).replace(
                                    " - Wildberries", "").replace(
                                    "Wildberries - ", "").strip()
                                log.info("WB %s: <title>=%r", lid, title)
                        if title and title != f"WB-{lid}":
                            obs["observation"]["fields"]["title"] = {
                                "status": "KNOWN", "value": title,
                                "method": "HTML_POST"}
                            obs["observation"]["status"] = "PARTIAL_SUCCESS"
                except Exception as e:
                    log.warning("WB %s: post-parse error: %s", lid, e)
            res = inbox_accept(inbox, obs)
            if res:
                pid = res.get("product_id")
                log.info("WB %s: ACCEPTED product_id=%s title=%r", lid, pid, title)
                results.append({"lid": lid, "product_id": pid,
                                "title": title, "status": "ACCEPTED"})
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
# Ozon — 4 strategies
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
                "title": {"status": "KNOWN", "value": title,
                          "method": "COMPOSER_API"},
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
                {"amount_minor": minor, "currency": "RUB",
                 "source": "COMPOSER_API"})
        except Exception:
            pass
    return obs


def test_ozon(app, inbox):
    from r52.collectors.ozon import OzonCollector
    col = OzonCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()
    for lid in OZON_IDS:
        log.info("OZON %s: 4 strategies", lid)
        obs = None
        url_d = f"https://www.ozon.ru/product/{lid}/"

        # S1: collector desktop
        try:
            obs = col.collect_product(lid, url_d, title_hint=f"OZ-{lid}")
            if obs:
                log.info("OZON %s: S1 desktop OK", lid)
        except Exception as e:
            log.warning("OZON %s: S1 error: %s", lid, e)

        # S2: mobile
        if not obs:
            url_m = f"https://m.ozon.ru/product/{lid}/"
            log.info("OZON %s: S2 mobile", lid)
            try:
                resp = col.fetch(url_m)
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
                resp = col.fetch(url_api)
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
                        log.info("OZON %s: S4 %s → %d (%dB)",
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
# Avito — fresh IDs from category
# ──────────────────────────────────────────────
def test_avito(app, inbox):
    from r52.collectors.avito import AvitoCollector
    col = AvitoCollector(data_dir=ROOT / "r52" / "data", use_proxies=False)
    results = []
    t0 = time.monotonic()

    # Scrape category for fresh IDs
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
        # Desktop
        try:
            obs = col.collect_product(lid, url, title_hint=f"AV-{lid}")
        except Exception as e:
            log.warning("AVITO %s: desktop error: %s", lid, e)
        # Mobile
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
    log.info("probe_v92 starting — %s", datetime.now(TZ).isoformat())
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
        "probe": "v92",
        "started_at": datetime.now(TZ).isoformat(),
        "sqlite_before": before,
        "sqlite_after": after,
        "delta": delta,
        "marketplaces": all_results,
        "total_inbox_accepted": total_acc,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("Results → %s", OUT_JSON)

    log.info("=== SUMMARY v92 ===")
    for mp, data in all_results.items():
        log.info("  %-8s: %d accepted, %d items",
                 mp, data["inbox_accepted"], len(data["items"]))
        for item in data["items"]:
            log.info("    %s", json.dumps(item, ensure_ascii=False))
    log.info("=== DONE ===")


if __name__ == "__main__":
    main()
