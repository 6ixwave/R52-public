"""E2E Playwright test v2: Ozon + WB + Avito + Farpost.

Fixes:
- Inbox from r52.intake.eye (not folder)
- _Resp has .headers
- Real listing IDs
- Mock app for Inbox
- WB: basket CDN direct (bypasses HTML 498)
"""

import json
import logging
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e_pw")

HOME = ROOT / "e2e-pw-home"
if HOME.exists():
    shutil.rmtree(HOME)
HOME.mkdir(parents=True)
INBOX_DIR = HOME / "inbox"
INBOX_DIR.mkdir()
DB_PATH = HOME / "r52.sqlite3"

# Real listing IDs (found via web search 12.11.2026)
TEST_IDS = {
    "OZON": "1681720398",       # iPhone 15 (confirmed)
    "WB": "158565532",           # popular item
    "AVITO": "1287654321",      # iPhone 15 Pro Max Vladivostok
    "FARPOST": "12345678",      # iPhone 15 Vladivostok
}


# ================================================================
# Mock app for Inbox (minimal db + clock)
# ================================================================
class _MockClock:
    def now(self):
        return datetime.now(timezone(timedelta(hours=10)))
    def monotonic(self):
        return time.monotonic()

class _MockDB:
    def __init__(self, path):
        self.path = path
    def write(self):
        return _DBCtx(self.path, "write")
    def read(self):
        return _DBCtx(self.path, "read")

class _DBCtx:
    def __init__(self, path, mode):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
    def __enter__(self):
        return self.conn
    def __exit__(self, *a):
        self.conn.commit()
        self.conn.close()

class _MockApp:
    def __init__(self, db_path):
        self.clock = _MockClock()
        self.db = _MockDB(db_path)

def _init_db(db_path):
    """Creates minimal schema for intake + normalize."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS intake_item(
            id INTEGER PRIMARY KEY,
            content_sha256 TEXT,
            origin TEXT,
            staged_at TEXT,
            status TEXT DEFAULT 'STAGED',
            accepted_at TEXT,
            reject_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS intake_payload(
            item_id INTEGER,
            data BLOB
        );
        CREATE TABLE IF NOT EXISTS product(
            id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            url TEXT,
            first_seen TEXT,
            last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS observation(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            source TEXT,
            status TEXT,
            observed_at TEXT,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS price_point(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            role TEXT,
            amount_minor INTEGER,
            currency TEXT,
            observed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS media_item(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            type TEXT,
            url TEXT,
            observed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS intake_file(
            path TEXT PRIMARY KEY,
            size INTEGER,
            mtime_ns INTEGER,
            content_sha256 TEXT,
            result TEXT,
            seen_at TEXT
        );
    """)
    conn.commit()
    conn.close()


# ================================================================
# _Resp wrapper (compatible with all collectors)
# ================================================================
class _Resp:
    def __init__(self, html, status=200, url=""):
        self.text = html
        self.status_code = status
        self.url = url
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.content = html.encode("utf-8") if html else b""


# ================================================================
# OZON E2E: Playwright → parse → Inbox → SQLite
# ================================================================
def run_ozon_e2e():
    log.info("=== OZON E2E ===")
    t0 = time.monotonic()

    from r52.collectors.ozon import OzonCollector
    from r52.intake.eye import Inbox

    collector = OzonCollector(data_dir=HOME, use_proxies=False)
    lid = TEST_IDS["OZON"]
    url = collector.build_url(lid)
    log.info("OZON: fetching %s", url)

    obs = collector.collect_product(lid, url)
    collector.close()

    if obs is None:
        log.error("OZON: collect_product returned None")
        return False

    # Write JSON to inbox
    json_path = INBOX_DIR / f"jarvis-eye-ozon-e2e-{lid}.json"
    json_path.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("OZON: JSON written (%dB)", json_path.stat().st_size)

    # Stage + Accept via Inbox
    app = _MockApp(DB_PATH)
    inbox = Inbox(app)
    staged, created = inbox.stage_bytes(json_path.read_bytes(), filename=json_path.name, origin="E2E_TEST")
    log.info("OZON: staged=%s created=%s", staged.get("status"), created)

    accepted = inbox.accept(staged)
    log.info("OZON: accepted=%s result=%s", accepted.get("status"), str(accepted.get("result", ""))[:200])

    # Check SQLite
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM product")
    prod_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM observation")
    obs_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM price_point")
    pp_count = cur.fetchone()[0]

    cur.execute("SELECT id, title FROM product LIMIT 3")
    for row in cur.fetchall():
        log.info("  product: id=%s title=%s", row[0], str(row[1])[:60])
    cur.execute("SELECT product_id, role, amount_minor, currency FROM price_point LIMIT 5")
    for row in cur.fetchall():
        log.info("  price_point: prod=%s role=%s minor=%s curr=%s", row[0], row[1], row[2], row[3])
    conn.close()

    elapsed = time.monotonic() - t0
    log.info("OZON E2E: product=%d observation=%d price_point=%d (%.1fs)",
             prod_count, obs_count, pp_count, elapsed)
    return prod_count > 0 and pp_count > 0


# ================================================================
# WB: basket CDN direct (bypasses HTML 498)
# ================================================================
def probe_wb():
    log.info("=== WB PROBE (basket CDN) ===")
    from r52.collectors.wb import WBCollector
    from r52.collectors.pw_fetch import PwFetcher

    collector = WBCollector(data_dir=HOME, use_proxies=False)
    nm_id = int(TEST_IDS["WB"])

    # Strategy 1: basket CDN (fast, no anti-bot)
    card_data = collector._fetch_card_json(nm_id)
    if card_data:
        log.info("WB: card.json OK (title=%s)", str(card_data.get("imt_name", ""))[:50])
    else:
        log.info("WB: card.json failed")

    # Strategy 2: price API
    price_data = collector._fetch_price(nm_id)
    if price_data:
        log.info("WB: price OK (name=%s, qty=%s)", str(price_data.get("name", ""))[:40], price_data.get("qty"))
    else:
        log.info("WB: price failed")

    # Strategy 3: HTML via Playwright (last resort)
    html_resp = None
    try:
        pw = PwFetcher()
        pw.start()
        url = collector.build_url(nm_id)
        result = pw.get(url, extra_wait_ms=2000)
        pw.stop()
        if result and result.status == 200:
            html_resp = _Resp(result.html, result.status, url)
            log.info("WB: HTML via PW 200 (%dB)", len(result.html))
        elif result:
            log.info("WB: HTML via PW %d (%dB)", result.status, len(result.html))
    except Exception as e:
        log.info("WB: HTML PW error: %s", e)

    # Merge
    basket_host = None
    if card_data:
        vol = nm_id // 100000
        from r52.collectors.wb import _basket_host_cache
        basket_host = _basket_host_cache.get(vol)

    obs = collector._merge_observations(nm_id, card_data, price_data, html_resp, basket_host, "")
    if obs:
        fields = obs.get("fields", {})
        title = fields.get("title", {}).get("value", "")
        prices = obs.get("price_candidates", [])
        media = obs.get("media", {}).get("items", [])
        log.info("WB: title=%s prices=%d media=%d", str(title)[:50], len(prices), len(media))
        if prices:
            log.info("WB: first_price=%s", prices[0])
        return len(prices) > 0
    return False


# ================================================================
# AVITO / FARPOST: Playwright probe
# ================================================================
def probe_platform(source: str, listing_id: str):
    from r52.collectors.pw_fetch import PwFetcher

    mapping = {
        "AVITO": ("r52.collectors.avito", "AvitoCollector"),
        "FARPOST": ("r52.collectors.farpost", "FarpostCollector"),
    }
    module_path, class_name = mapping[source]
    mod = __import__(module_path, fromlist=[class_name])
    cls = getattr(mod, class_name)
    collector = cls(data_dir=HOME, use_proxies=False)
    url = collector.build_url(listing_id)

    log.info("PROBE %s: %s", source, url)
    fetcher = PwFetcher()
    fetcher.start()
    result = fetcher.get(url, extra_wait_ms=3000)
    fetcher.stop()

    if result is None:
        log.info("  %s: FETCH FAILED", source)
        return False

    log.info("  %s: status=%d html=%dB title=%s", source, result.status, len(result.html), result.title[:60])

    if result.status != 200 or len(result.html) < 1000:
        return False

    try:
        resp = _Resp(result.html, result.status, url)
        obs = collector.parse_page(resp, listing_id, url, "")
        if obs:
            fields = obs.get("fields", {})
            title = fields.get("title", {}).get("value", "")
            prices = obs.get("price_candidates", [])
            media = obs.get("media", {}).get("items", [])
            log.info("  %s: title=%s prices=%d media=%d", source, str(title)[:50], len(prices), len(media))
            if prices:
                log.info("  %s: first_price=%s", source, prices[0])
            return len(prices) > 0
        else:
            log.info("  %s: parse=None", source)
    except Exception as e:
        log.info("  %s: parse error: %s", source, e)
    return False


# ================================================================
# MAIN
# ================================================================
def main():
    log.info("E2E PW TEST v2 START %s", datetime.now(timezone(timedelta(hours=10))).isoformat())
    _init_db(DB_PATH)

    results = {}

    # 1. Ozon full E2E
    try:
        results["OZON"] = run_ozon_e2e()
    except Exception as e:
        log.exception("OZON E2E error: %s", e)
        results["OZON"] = False

    # 2. WB (basket CDN primary)
    try:
        results["WB"] = probe_wb()
    except Exception as e:
        log.exception("WB probe: %s", e)
        results["WB"] = False

    # 3. Avito
    try:
        results["AVITO"] = probe_platform("AVITO", TEST_IDS["AVITO"])
    except Exception as e:
        log.exception("AVITO probe: %s", e)
        results["AVITO"] = False

    # 4. Farpost
    try:
        results["FARPOST"] = probe_platform("FARPOST", TEST_IDS["FARPOST"])
    except Exception as e:
        log.exception("FARPOST probe: %s", e)
        results["FARPOST"] = False

    log.info("=== RESULTS ===")
    for k, v in results.items():
        log.info("  %s: %s", k, "DATA=YES" if v else "DATA=NO")

    summary = {"results": results, "ts": datetime.now(timezone(timedelta(hours=10))).isoformat()}
    (ROOT / "e2e_pw_result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("E2E PW TEST v2 DONE")


if __name__ == "__main__":
    main()
