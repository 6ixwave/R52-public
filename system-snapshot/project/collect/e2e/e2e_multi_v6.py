"""E2E Multi-Strategy V6: Intake WB+FP dumps, live Ozon SSR, WB basket.
Priority: Ozon -> Farpost -> WB -> Avito.
Target: product + observation + price_point in test DB.
"""
import json, os, sys, time, sqlite3, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ["R52_HOME"] = str(BASE / "e2e_test_home")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("E2E-V6")

TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

# ============================================================
# 1. DB SETUP
# ============================================================
def db_counts(conn):
    r = {}
    for tbl in ("product", "observation", "price_point"):
        try:
            r[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        except Exception:
            r[tbl] = -1
    return r

def setup_db():
    from r52.config import Config, Paths
    from r52.db import Database, migrate
    from r52.timeutil import Clock

    home = BASE / "e2e_test_home"
    paths = Paths(home=home)
    paths.ensure()
    cfg = Config(paths=paths)
    db = Database(paths.db_file)
    db.initialize()
    migrate(db, backup_dir=paths.backups_dir, now=datetime.now(TZ))
    return db, paths

# ============================================================
# 2. INTAKE via Inbox (standard pipeline)
# ============================================================
def intake_file(db, paths, filepath, origin="E2E-V6"):
    """Feed one jarvis-eye JSON through Inbox.stage_bytes + accept."""
    from r52.core.settings import Settings
    from r52.core.notify import Notifier
    from r52.intake.eye import Inbox
    from r52.timeutil import Clock

    clock = Clock()
    settings = Settings(db, clock)
    notifier = Notifier(settings, clock, paths.tz)

    class MiniApp:
        pass
    app = MiniApp()
    app.db = db
    app.clock = clock
    app.settings = settings
    app.notifier = notifier
    app.tz = paths.tz

    inbox = Inbox(app)
    data = filepath.read_bytes()
    name = filepath.name
    log.info("INTAKE: %s (%d bytes)", name, len(data))

    item, created = inbox.stage_bytes(data, filename=name, origin=origin)
    log.info("  stage: id=%s status=%s created=%s", item.get("id"), item.get("status"), created)

    if item["status"] == "PENDING":
        result = inbox.accept(item["id"])
        log.info("  accept: product_id=%s obs_created=%s events=%s",
                 result.get("product_id"), result.get("observation_created"), result.get("events"))
        return result
    elif item["status"] == "ACCEPTED":
        log.info("  auto-accepted (already)")
        return item
    else:
        log.warning("  status=%s error=%s", item["status"], item.get("error_code"))
        return item

# ============================================================
# 3. OZON: SSR HTML via curl_cffi (direct page, NOT API)
# ============================================================
def ozon_ssr_test():
    """Fetch Ozon product page SSR HTML directly."""
    log.info("=== OZON SSR TEST ===")
    results = {}
    listing_id = "3481320800"
    url = f"https://www.ozon.ru/product/{listing_id}/"

    try:
        from curl_cffi import requests as cffi
    except ImportError:
        log.warning("curl_cffi not available")
        return {"ok": False, "detail": "no_curl_cffi"}

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.ozon.ru/",
    }

    # Strategy A: impersonate=chrome
    t0 = time.monotonic()
    try:
        resp = cffi.get(url, impersonate="chrome", timeout=20, headers=headers)
        elapsed = time.monotonic() - t0
        html = resp.text
        log.info("OZON A: HTTP=%d len=%d t=%.1fs", resp.status_code, len(html), elapsed)

        if resp.status_code == 200 and len(html) > 5000:
            # Extract JSON-LD
            import re
            ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            product_ld = None
            for block in ld_blocks:
                try:
                    d = json.loads(block)
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        product_ld = d
                        break
                    if isinstance(d, list):
                        for item in d:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                product_ld = item
                                break
                except:
                    pass

            # Extract price from HTML
            price_match = re.search(r'data-widget="price"[^>]*>.*?(\d[\d\s]*)\s*(?:₽|RUB)', html, re.DOTALL)
            price_html = None
            if price_match:
                price_html = price_match.group(1).replace(" ", "").replace("\u00a0", "")

            # Also try data-widget state
            state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
            has_state = bool(state_match)

            # Title
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            title = title_match.group(1).strip() if title_match else None

            # OG tags
            og_title = re.search(r'property="og:title"\s+content="([^"]*)"', html)
            og_price = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)

            results = {
                "ok": resp.status_code == 200 and len(html) > 5000,
                "http": resp.status_code,
                "len": len(html),
                "t": round(elapsed, 1),
                "jsonld_product": bool(product_ld),
                "jsonld_price": product_ld.get("offers", {}).get("price") if product_ld else None,
                "html_price": price_html,
                "has_state": has_state,
                "title": (title or "")[:80],
                "og_title": og_title.group(1)[:80] if og_title else None,
                "og_price": og_price.group(1) if og_price else None,
            }
            log.info("OZON A: jsonld=%s price_ld=%s price_html=%s title=%s",
                     results["jsonld_product"], results["jsonld_price"],
                     results["html_price"], results["title"][:40])

            if product_ld:
                log.info("OZON A: LD=%s", json.dumps(product_ld, ensure_ascii=False)[:300])
        else:
            results = {"ok": False, "http": resp.status_code, "len": len(html), "t": round(elapsed, 1)}
    except Exception as e:
        elapsed = time.monotonic() - t0
        results = {"ok": False, "error": str(e)[:200], "t": round(elapsed, 1)}
        log.warning("OZON A error: %s", e)

    # Strategy B: mobile UA
    t0 = time.monotonic()
    try:
        m_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        resp = cffi.get(url, impersonate="chrome", timeout=20, headers=m_headers,
                        params={"from": "mobile"})
        elapsed = time.monotonic() - t0
        html = resp.text
        log.info("OZON B(mobile): HTTP=%d len=%d t=%.1fs", resp.status_code, len(html), elapsed)
        results["mobile"] = {"http": resp.status_code, "len": len(html), "t": round(elapsed, 1)}
    except Exception as e:
        results["mobile"] = {"error": str(e)[:100]}

    return results

# ============================================================
# 4. WB: Basket CDN (confirmed working in d10)
# ============================================================
def wb_basket_test():
    """Fetch WB basket CDN card.json for target item."""
    log.info("=== WB BASKET TEST ===")
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return {"ok": False, "detail": "no_curl_cffi"}

    headers = {
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
    }

    # Try multiple basket hosts
    results = {"ok": False, "host": None, "title": None, "prices": []}
    for i in range(1, 30):
        host = f"basket-{i}.wbbasket.ru"
        url = f"https://{host}{path}"
        t0 = time.monotonic()
        try:
            resp = cffi.get(url, impersonate="chrome", timeout=10, headers=headers)
            elapsed = time.monotonic() - t0
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("name", "")
                brand = data.get("brand", "")
                # Extract prices from selling
                selling = data.get("selling", {})
                prices = []
                for k, v in selling.items():
                    if isinstance(v, (int, float)) and v > 0:
                        prices.append(f"{k}={v}")
                results = {
                    "ok": True, "host": host, "title": title[:80],
                    "brand": brand, "prices": prices[:5],
                    "nkeys": len(data), "t": round(elapsed, 1),
                }
                log.info("WB BASKET: host=%s title=%s prices=%s t=%.1fs",
                         host, title[:50], prices[:3], elapsed)
                break
            elif resp.status_code == 404:
                continue
            else:
                log.debug("WB BASKET: %s -> %d", host, resp.status_code)
        except Exception as e:
            log.debug("WB BASKET: %s error: %s", host, str(e)[:50])

    return results

# ============================================================
# 5. FARPOST: Live card fetch (warm-up + card)
# ============================================================
def farpost_live_test():
    """Fetch a Farpost card with warm-up."""
    log.info("=== FARPOST LIVE TEST ===")
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return {"ok": False, "detail": "no_curl_cffi"}

    # Use known working card ID
    card_id = "134693091"
    url = f"https://www.farpost.ru/vladivostok/{card_id}.html"

    # Warm-up
    t0 = time.monotonic()
    try:
        sess = cffi.Session(impersonate="chrome")
        w = sess.get("https://www.farpost.ru/", timeout=15)
        log.info("FP warm-up: HTTP=%d t=%.1fs", w.status_code, time.monotonic() - t0)
        time.sleep(1)

        t0 = time.monotonic()
        resp = sess.get(url, timeout=20)
        elapsed = time.monotonic() - t0
        html = resp.text

        # Check encoding
        import re
        if "windows-1251" in html[:500].lower():
            # re-decode from raw
            pass

        # Extract JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        product_ld = None
        for block in ld_blocks:
            try:
                d = json.loads(block)
                if isinstance(d, dict) and d.get("@type") == "Product":
                    product_ld = d
                    break
            except:
                pass

        price_attr = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        title_match = re.search(r'<h1[^>]*class="[^"]*subject[^"]*"[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else None

        results = {
            "ok": resp.status_code == 200 and len(html) > 3000,
            "http": resp.status_code, "len": len(html), "t": round(elapsed, 1),
            "verify": "/verify" in (resp.url or ""),
            "jsonld": bool(product_ld),
            "ld_price": product_ld.get("offers", {}).get("price") if product_ld else None,
            "attr_price": price_attr.group(1) if price_attr else None,
            "title": (title or "")[:80],
        }
        log.info("FP LIVE: HTTP=%d len=%d verify=%s ld=%s price=%s title=%s t=%.1fs",
                 resp.status_code, len(html), results["verify"],
                 results["jsonld"], results["ld_price"] or results["attr_price"],
                 (title or "?")[:40], elapsed)
        return results
    except Exception as e:
        log.warning("FP LIVE error: %s", e)
        return {"ok": False, "error": str(e)[:200]}

# ============================================================
# MAIN
# ============================================================
def main():
    t_start = time.monotonic()
    log.info("=== E2E MULTI V6 START ===")

    # Setup DB
    db, paths = setup_db()
    conn = db.read()
    before = db_counts(conn)
    log.info("DB BEFORE: %s", before)

    report = {"before": before, "ozon": {}, "farpost_live": {}, "wb_basket": {}, "intake": {}}

    # --- OZON SSR ---
    report["ozon"] = ozon_ssr_test()

    # --- FARPOST LIVE ---
    report["farpost_live"] = farpost_live_test()

    # --- WB BASKET ---
    report["wb_basket"] = wb_basket_test()

    # --- INTAKE: existing WB dumps ---
    wb_dir = BASE / "e2e-wb-v1"
    for f in sorted(wb_dir.glob("jarvis-eye-wb-*.json")):
        try:
            r = intake_file(db, paths, f, origin="E2E-V6-WB")
            report["intake"][f.name] = {
                "product_id": r.get("product_id"),
                "obs_created": r.get("observation_created"),
                "status": r.get("status"),
            }
        except Exception as e:
            log.warning("WB intake %s error: %s", f.name, e)
            report["intake"][f.name] = {"error": str(e)[:200]}

    # --- INTAKE: existing Farpost dumps ---
    fp_dir = BASE / "e2e-farpost-v2"
    for f in sorted(fp_dir.glob("jarvis-eye-farpost-*.json")):
        try:
            r = intake_file(db, paths, f, origin="E2E-V6-FP")
            report["intake"][f.name] = {
                "product_id": r.get("product_id"),
                "obs_created": r.get("observation_created"),
                "status": r.get("status"),
            }
        except Exception as e:
            log.warning("FP intake %s error: %s", f.name, e)
            report["intake"][f.name] = {"error": str(e)[:200]}

    # --- DB AFTER ---
    conn = db.read()
    after = db_counts(conn)
    log.info("DB AFTER: %s", after)
    report["after"] = after
    report["delta"] = {k: after[k] - before[k] for k in before if before[k] >= 0 and after[k] >= 0}

    # Per-source breakdown
    try:
        src_rows = conn.execute("""
            SELECT p.source, COUNT(DISTINCT p.id) as products,
                   COUNT(DISTINCT o.id) as obs,
                   COUNT(DISTINCT pp.id) as pp
            FROM product p
            LEFT JOIN observation o ON o.product_id = p.id
            LEFT JOIN price_point pp ON pp.observation_id = o.id
            GROUP BY p.source
        """).fetchall()
        report["by_source"] = [dict(r) for r in src_rows]
    except Exception as e:
        report["by_source"] = str(e)[:200]

    elapsed = time.monotonic() - t_start
    report["total_s"] = round(elapsed, 1)
    report["time"] = NOW()

    # Save report
    out = BASE / "e2e_v6_results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== E2E MULTI V6 DONE in %.1fs ===", elapsed)
    log.info("Report: %s", out)

    # Print summary
    log.info("--- SUMMARY ---")
    log.info("OZON SSR: ok=%s", report["ozon"].get("ok"))
    log.info("FP LIVE:  ok=%s", report["farpost_live"].get("ok"))
    log.info("WB BASKET: ok=%s host=%s", report["wb_basket"].get("ok"), report["wb_basket"].get("host"))
    for name, r in report["intake"].items():
        log.info("INTAKE %s: pid=%s obs=%s", name, r.get("product_id"), r.get("obs_created"))
    log.info("DELTA: %s", report["delta"])
    log.info("BY_SRC: %s", report.get("by_source"))

    return report

if __name__ == "__main__":
    main()
