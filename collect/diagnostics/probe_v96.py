"""probe_v96 — E2E тест: Collector → JSON → Inbox.stage_bytes → Inbox.accept → SQLite.

Исправления относительно v95:
  - App (не R52App), load_config, install_guard=False, app.open(), app.stop()
  - DB: r52/data/r52.sqlite3 (штатное имя Paths.db_file)
  - auto_accept=False → явный accept()
  - Проверка quality: title не fallback, цены > 0

Запуск:
    C:\\Python315\\python.exe probe_v96.py
"""

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v96")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Штатный home: r52/ → data/r52.sqlite3
HOME = ROOT / "r52"
DB_PATH = HOME / "data" / "r52.sqlite3"
INTAKE_DIR = HOME / "data" / "intake"

# По 1 товару на площадку (известные ID)
TEST_ITEMS = {
    "WB": "259671175",
    "OZON": "3690613958",
    "AVITO": "1154321987",
    "FARPOST": "125270259",
}


def db_snapshot(db_path):
    import sqlite3
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    counts = {}
    for tbl in ("product", "observation", "price_point", "inbox_item", "intake_file"):
        try:
            row = conn.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()
            counts[tbl] = row["c"]
        except sqlite3.OperationalError:
            counts[tbl] = -1
    conn.close()
    return counts


def check_records(db_path, source, listing_id):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    out = {"products": [], "price_points": [], "observations": []}
    try:
        out["products"] = [dict(r) for r in conn.execute(
            "SELECT id, source, listing_id, title, last_regular_minor, last_availability, observation_count "
            "FROM product WHERE source=? AND listing_id=? ORDER BY id DESC LIMIT 3",
            (source, listing_id)).fetchall()]
        out["price_points"] = [dict(r) for r in conn.execute(
            "SELECT pp.id, pp.amount_minor, pp.currency, pp.label, pp.observed_at "
            "FROM price_point pp JOIN product p ON pp.product_id = p.id "
            "WHERE p.source=? AND p.listing_id=? ORDER BY pp.id DESC LIMIT 5",
            (source, listing_id)).fetchall()]
        out["observations"] = [dict(r) for r in conn.execute(
            "SELECT o.id, o.observed_at, o.origin, o.evidence_level, o.availability "
            "FROM observation o JOIN product p ON o.product_id = p.id "
            "WHERE p.source=? AND p.listing_id=? ORDER BY o.id DESC LIMIT 3",
            (source, listing_id)).fetchall()]
    except Exception as e:
        out["error"] = str(e)
    conn.close()
    return out


def run_platform(app, source, listing_id):
    log.info("=" * 64)
    log.info("PLATFORM: %s | ID: %s", source, listing_id)
    log.info("=" * 64)

    before = db_snapshot(DB_PATH)
    log.info("BEFORE: %s", before)

    # 1. Collector
    from r52.collectors import COLLECTOR_REGISTRY
    cls, display_name = COLLECTOR_REGISTRY[source]
    collector = cls(data_dir=app.config.paths.data_dir, use_proxies=False)
    log.info("Collector: %s (proxies=OFF)", display_name)

    url = collector.build_url(listing_id)
    log.info("URL: %s", url)

    t0 = time.monotonic()
    try:
        observation = collector.collect_product(listing_id, url)
    except Exception as e:
        log.error("COLLECT EXCEPTION: %s", e)
        return {"source": source, "status": "COLLECT_ERROR", "error": str(e)[:400]}
    elapsed = time.monotonic() - t0
    log.info("Collect: %.1fs", elapsed)

    if not observation:
        return {"source": source, "status": "NO_OBSERVATION", "elapsed": round(elapsed, 1)}

    # 2. Quality check
    obs_data = observation.get("observation", observation)
    status = obs_data.get("status", "UNKNOWN")
    title = obs_data.get("title", "")
    # Для Farpost/WB title может быть в fields
    if not title and "fields" in obs_data:
        f = obs_data["fields"].get("title", {})
        title = f.get("value", "") if isinstance(f, dict) else str(f)
    price_candidates = obs_data.get("price_candidates", [])
    log.info("Status=%s | Title=%s | Prices=%d", status, title[:60], len(price_candidates))
    if price_candidates:
        for pc in price_candidates[:3]:
            log.info("  Price: %s %s (%s)", pc.get("amount"), pc.get("currency"), pc.get("label"))

    # 3. Write JSON to intake
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    written = collector.write_observations([observation], INTAKE_DIR)
    log.info("Written: %s", [Path(w).name for w in written])

    if not written:
        fname = f"jarvis-eye-{source.lower()}-v96-{listing_id}.json"
        fpath = INTAKE_DIR / fname
        fpath.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
        written = [str(fpath)]
        log.info("Manual write: %s", fpath.name)

    # 4. Inbox: stage + accept
    for fpath_str in written:
        fpath = Path(fpath_str)
        if not fpath.exists():
            log.warning("File missing: %s", fpath)
            continue
        data = fpath.read_bytes()
        log.info("Staging: %s (%d bytes)", fpath.name, len(data))

        item, created = app.inbox.stage_bytes(data, filename=fpath.name, origin="COLLECTOR")
        log.info("  Staged: id=%s created=%s status=%s err=%s",
                 item.get("id"), created, item.get("status"), item.get("error_code"))

        if item.get("status") == "PENDING":
            try:
                result = app.inbox.accept(item["id"])
                log.info("  Accepted: product_id=%s obs_created=%s events=%s",
                         result.get("product_id"), result.get("observation_created"),
                         len(result.get("events", [])))
            except Exception as e:
                log.error("  Accept error: %s", e)
        elif item.get("status") == "INVALID":
            log.warning("  INVALID: %s", item.get("error_code"))
        elif item.get("status") == "ACCEPTED":
            log.info("  Auto-accepted (auto_accept=True)")
        else:
            log.warning("  Status: %s (not staged/accepted)", item.get("status"))

    # 5. Verify DB
    time.sleep(1)
    after = db_snapshot(DB_PATH)
    log.info("AFTER: %s", after)
    deltas = {k: after.get(k, 0) - before.get(k, 0) for k in before}
    log.info("DELTA: %s", deltas)

    details = check_records(DB_PATH, source, listing_id)
    log.info("Records: products=%d pp=%d obs=%d",
             len(details["products"]), len(details["price_points"]), len(details["observations"]))
    for p in details["products"][:2]:
        log.info("  P: id=%s title=%s last_reg=%s avail=%s obs_cnt=%s",
                 p.get("id"), str(p.get("title", ""))[:50],
                 p.get("last_regular_minor"), p.get("last_availability"), p.get("observation_count"))
    for pp in details["price_points"][:3]:
        log.info("  PP: id=%s amt=%s cur=%s label=%s",
                 pp.get("id"), pp.get("amount_minor"), pp.get("currency"), pp.get("label"))

    # Verdict
    has_product = len(details["products"]) > 0
    has_real_title = has_product and not str(details["products"][0].get("title", "")).startswith(f"{source}-")
    has_price = len(details["price_points"]) > 0 and details["price_points"][0].get("amount_minor") is not None
    has_obs = len(details["observations"]) > 0

    verdict = "PASS" if (has_product and has_obs) else "PARTIAL"
    if has_real_title:
        verdict += "+TITLE"
    else:
        verdict += "-FALLBACK_TITLE"
    verdict += "+PRICE" if has_price else "-NO_PRICE"

    log.info("VERDICT: %s", verdict)
    return {
        "source": source, "status": status, "title": title[:80],
        "verdict": verdict, "deltas": deltas,
        "prices": details["price_points"][:3],
        "elapsed": round(elapsed, 1),
    }


def main():
    log.info("probe_v96 start %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("HOME: %s", HOME)
    log.info("DB:   %s (exists=%s)", DB_PATH, DB_PATH.exists())

    # Init App
    from r52.config import load_config
    from r52.service import App

    config = load_config(home=str(HOME))
    log.info("Config: intake_folders=%s", config.intake_folders)
    log.info("Config: db_file=%s", config.paths.db_file)

    app = App(config, install_guard=False)
    app.open()
    log.info("App opened. auto_accept=%s", app.settings.get("intake.auto_accept"))

    results = []
    for source, lid in TEST_ITEMS.items():
        try:
            r = run_platform(app, source, lid)
        except Exception as e:
            log.exception("Platform %s crashed: %s", source, e)
            r = {"source": source, "status": "CRASH", "error": str(e)[:400]}
        results.append(r)
        time.sleep(3)  # буфер между площадками

    # Summary
    log.info("=" * 64)
    log.info("SUMMARY")
    log.info("=" * 64)
    for r in results:
        log.info("  %-8s | %-12s | %-40s | %s | %.1fs",
                 r.get("source", "?"), r.get("status", "?"),
                 str(r.get("title", r.get("error", "")))[:40],
                 r.get("verdict", "?"), r.get("elapsed", 0))

    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
    out_file = ROOT / "probe_v96_result.json"
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Result: %s", out_file)

    app.stop()
    log.info("DONE")


if __name__ == "__main__":
    main()
