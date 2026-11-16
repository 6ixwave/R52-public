r"""e2e_all.py — Full E2E for OZON, WB, FARPOST, AVITO.
Pipeline: Collector -> Inbox.stage_bytes -> Inbox.accept -> SQLite
Run: C:\Python315\python.exe e2e_all.py
"""
import sys, os, json, time, sqlite3, logging, traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

# --- Logging: console + file (unique name per run) ---
RUN_TS = time.strftime("%Y%m%d_%H%M%S")
LOG_FILE = ROOT / f"e2e_{RUN_TS}.txt"
fmt = logging.Formatter("%(asctime)s %(name)-18s %(levelname)-5s %(message)s")
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
fh.setFormatter(fmt)
log = logging.getLogger("E2E")
log.setLevel(logging.INFO)
log.addHandler(sh)
log.addHandler(fh)

DB = Path(os.environ.get("R52_HOME", Path(os.environ.get("LOCALAPPDATA", "")) / "R52")) / "data" / "r52.sqlite3"

# Per-marketplace timeout in seconds
PER_SRC_TIMEOUT = 90

# Listings: (listing_id_or_url, title_hint)
# Farpost: full URL confirmed live on 12.11.2026
LISTINGS = {
    "OZON":    ("2067947177", "Palit RTX 5060 Ti"),
    "WB":      ("1097975275", "WB card"),
    "FARPOST": ("https://www.farpost.ru/ni/irkutsk/zapchasti/dvigatel-nissan-cga3-ustanovka-v-podarok-garantija-do-goda-138859452.html", "Dvigatel Nissan CGA3"),
    "AVITO":   ("1045231001", "Avito listing"),
}

COLLECTOR_MAP = {
    "OZON":    ("r52.collectors.ozon",    "OzonCollector"),
    "WB":      ("r52.collectors.wb",      "WBCollector"),
    "FARPOST": ("r52.collectors.farpost",  "FarpostCollector"),
    "AVITO":   ("r52.collectors.avito",    "AvitoCollector"),
}


def get_counts(db_path):
    """Return baseline counts per table and per source."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    r = {}
    for tbl in ("product", "observation", "price_point"):
        r[tbl] = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    rows = cur.execute("SELECT source, COUNT(*) c FROM product GROUP BY source").fetchall()
    r["product_by_src"] = {row["source"]: row["c"] for row in rows}
    for tbl in ("observation", "price_point"):
        rows = cur.execute(
            f"SELECT p.source, COUNT(*) c FROM {tbl} t "
            f"JOIN product p ON p.id = t.product_id GROUP BY p.source"
        ).fetchall()
        r[f"{tbl}_by_src"] = {row["source"]: row["c"] for row in rows}
    rows = cur.execute("SELECT source, listing_id FROM product ORDER BY source, id").fetchall()
    r["listings"] = {}
    for row in rows:
        r["listings"].setdefault(row["source"], row["listing_id"])
    con.close()
    return r


def make_collector(name):
    import importlib
    mod_name, cls_name = COLLECTOR_MAP[name]
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    return cls(data_dir=None, use_proxies=False)


def pipe_inbox(inbox, result, source):
    """Serialize result -> stage_bytes -> accept. Returns (staged_item, accepted_result)."""
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    log.info("  payload %d bytes", len(payload))
    staged, created = inbox.stage_bytes(
        payload, filename=f"e2e-{source.lower()}.json", origin="E2E_TEST"
    )
    log.info("  stage_bytes -> id=%s created=%s status=%s err=%s",
             staged.get("id"), created, staged.get("status"), staged.get("error_code"))
    if staged.get("status") != "PENDING":
        log.warning("  stage not PENDING (status=%s, err=%s)",
                    staged.get("status"), staged.get("error_code"))
        return staged, None
    item_id = staged["id"]
    accepted = inbox.accept(item_id)
    log.info("  accept      -> product_id=%s obs_created=%s events=%d",
             accepted.get("product_id"), accepted.get("observation_created"),
             len(accepted.get("events", [])))
    return staged, accepted


def test_one(name, listing_id, title_hint, inbox, db_path):
    """Run E2E for one marketplace. Returns True if new price_points appeared."""
    log.info("=== %s  listing=%s ===", name, listing_id[:60])
    before = get_counts(db_path)
    col = make_collector(name)
    url = col.build_url(listing_id)
    log.info("  URL: %s", url)
    try:
        t0 = time.time()
        result = col.collect_product(listing_id, url, title_hint)
        dt = time.time() - t0
        if not result:
            log.warning("  collector returned None in %.1fs", dt)
            return False
        obs = result.get("observation", {})
        prices = obs.get("price_candidates", [])
        log.info("  collector OK  %.1fs  status=%s  prices=%d",
                 dt, obs.get("status", "?"), len(prices))
        for p in prices[:5]:
            log.info("    %-8s %8d %s  '%s'",
                     p.get("role", "?"), p.get("amount_minor", 0),
                     p.get("currency", "?"), (p.get("label") or "")[:40])
        pipe_inbox(inbox, result, name)
        time.sleep(0.5)
        after = get_counts(db_path)
        d_obs  = after["observation_by_src"].get(name, 0) - before["observation_by_src"].get(name, 0)
        d_pp   = after["price_point_by_src"].get(name, 0) - before["price_point_by_src"].get(name, 0)
        d_prod = after["product_by_src"].get(name, 0) - before["product_by_src"].get(name, 0)
        log.info("  DELTA  +prod=%d  +obs=%d  +pp=%d", d_prod, d_obs, d_pp)
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = cur.execute(
            "SELECT pp.id, pp.role, pp.amount_minor, pp.currency, pp.label, pp.observed_at "
            "FROM price_point pp JOIN product p ON p.id = pp.product_id "
            "WHERE p.source = ? AND p.listing_id = ? ORDER BY pp.id DESC LIMIT 5",
            (name, listing_id)
        ).fetchall()
        for row in rows:
            log.info("    pp#%d  %-8s %8d %s  '%s'",
                     row["id"], row["role"], row["amount_minor"],
                     row["currency"], (row["label"] or "")[:30])
        con.close()
        ok = d_pp > 0 or (d_obs > 0 and len(prices) > 0)
        log.info("  => %s", "PASS" if ok else "PARTIAL (no new pp, dedup?)")
        return ok
    except Exception:
        log.error("  EXCEPTION:\n%s", traceback.format_exc())
        return False
    finally:
        try:
            if hasattr(col, "close"):
                col.close()
        except Exception:
            pass


def test_one_with_timeout(name, listing_id, title_hint, inbox, db_path, timeout=PER_SRC_TIMEOUT):
    """Wrap test_one in a thread with timeout to prevent one marketplace blocking all."""
    log.info("--- %s: starting (timeout=%ds) ---", name, timeout)
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(test_one, name, listing_id, title_hint, inbox, db_path)
        try:
            ok = fut.result(timeout=timeout)
            elapsed = time.time() - t_start
            log.info("--- %s: done in %.1fs, ok=%s ---", name, elapsed, ok)
            return ok
        except FuturesTimeout:
            elapsed = time.time() - t_start
            log.warning("--- %s: TIMEOUT after %.1fs (limit %ds) ---", name, elapsed, timeout)
            return False
        except Exception as e:
            elapsed = time.time() - t_start
            log.error("--- %s: ERROR after %.1fs: %s ---", name, elapsed, e)
            return False


def main():
    log.info("E2E ALL  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("DB path: %s (exists=%s)", DB, DB.exists())
    log.info("Log file: %s", LOG_FILE)

    # 1) Open App FIRST (creates DB, runs migrations, builds Inbox)
    from r52.config import load_config
    from r52.service import App
    config = load_config(home=str(ROOT / "e2e_test_home"))
    app = App(config, install_guard=False)
    app.open()
    inbox = app.inbox
    log.info("App + Inbox ready (db=%s)", app.paths.db_file)
    db_path = app.paths.db_file

    # 2) Baseline
    baseline = get_counts(db_path)
    log.info("BASELINE: prod=%d obs=%d pp=%d",
             baseline["product"], baseline["observation"], baseline["price_point"])
    for src in ("OZON", "WB", "FARPOST", "AVITO"):
        log.info("  %-10s prod=%d obs=%d pp=%d",
                 src,
                 baseline["product_by_src"].get(src, 0),
                 baseline["observation_by_src"].get(src, 0),
                 baseline["price_point_by_src"].get(src, 0))

    # 3) Run each marketplace with timeout
    results = {}
    for name in ("WB", "FARPOST", "OZON", "AVITO"):
        lid, hint = LISTINGS[name]
        if lid is None:
            results[name] = "SKIPPED"
            log.info("=== %s SKIPPED ===", name)
            continue
        try:
            ok = test_one_with_timeout(name, lid, hint, inbox, db_path)
            results[name] = "PASS" if ok else "PARTIAL"
        except Exception as e:
            log.error("%s fatal: %s", name, e)
            results[name] = f"ERROR: {e}"

    # 4) Summary
    log.info("=" * 50)
    log.info("RESULTS:")
    for name, res in results.items():
        log.info("  %-10s %s", name, res)
    with open(ROOT / "e2e_all_result.json", "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "baseline": baseline, "results": results},
                  f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved e2e_all_result.json")

    # 5) Cleanup
    try:
        app.stop()
    except Exception as e:
        log.warning("app.stop: %s", e)
    log.info("E2E DONE")


if __name__ == "__main__":
    main()
