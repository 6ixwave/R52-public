r"""e2e_farpost.py — Focused E2E: Farpost 138859452 → Inbox → SQLite.
Run: C:\Python315\python.exe e2e_farpost.py
"""
import sys, os, json, time, sqlite3, logging, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("FP_E2E")

# --- Config ---
LISTING_ID = "138859452"
TITLE_HINT = "Dvigatel Nissan CGA3"
DB_HOME = ROOT / "e2e_test_home"


def main():
    log.info("=" * 60)
    log.info("FARPOST FOCUSED E2E  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("Listing: %s", LISTING_ID)
    log.info("DB home: %s", DB_HOME)

    # 1) App + Inbox
    from r52.config import load_config
    from r52.service import App
    config = load_config(home=str(DB_HOME))
    app = App(config, install_guard=False)
    app.open()
    inbox = app.inbox
    db_path = app.paths.db_file
    log.info("App open. DB: %s", db_path)

    # 2) Baseline
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    base_prod = con.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    base_obs = con.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    base_pp = con.execute("SELECT COUNT(*) FROM price_point").fetchone()[0]
    con.close()
    log.info("BASELINE: prod=%d obs=%d pp=%d", base_prod, base_obs, base_pp)

    # 3) Collector
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=None, use_proxies=False)
    url = col.build_url(LISTING_ID)
    log.info("URL: %s", url)

    t0 = time.time()
    result = col.collect_product(LISTING_ID, url, TITLE_HINT)
    dt = time.time() - t0

    if result is None:
        log.error("COLLECTOR returned None in %.1fs", dt)
        log.error("stats: %s", col.stats)
        col.close()
        app.stop()
        return

    log.info("COLLECTOR OK in %.1fs", dt)
    log.info("  format=%s", result.get("format"))
    log.info("  capture_action=%s", result.get("capture_action"))
    log.info("  fully_autonomous=%s", result.get("fully_autonomous"))

    obs = result.get("observation", {})
    log.info("  obs.source=%s", obs.get("source"))
    log.info("  obs.id=%s", obs.get("id"))
    log.info("  obs.url=%s", obs.get("url"))
    log.info("  obs.observed_at=%s", obs.get("observed_at"))
    log.info("  obs.status=%s", obs.get("status"))
    log.info("  obs.evidence_level=%s", obs.get("evidence_level"))
    log.info("  obs.http_status=%s", obs.get("http_status"))

    fields = obs.get("fields", {})
    log.info("  fields keys: %s", list(fields.keys()))
    for k, v in fields.items():
        if isinstance(v, dict):
            val = v.get("value")
            if isinstance(val, str) and len(val) > 80:
                val = val[:80] + "..."
            log.info("    field[%s] status=%s value=%s method=%s", k, v.get("status"), val, v.get("method"))

    prices = obs.get("price_candidates", [])
    log.info("  price_candidates: %d", len(prices))
    for p in prices:
        log.info("    role=%s amount=%s amount_minor=%s currency=%s label=%s",
                 p.get("role"), p.get("amount"), p.get("amount_minor"), p.get("currency"), p.get("label"))

    media = obs.get("media", {})
    items = media.get("items", [])
    log.info("  media items: %d", len(items))
    for i, mi in enumerate(items[:5]):
        log.info("    [%d] type=%s url=%s", i, mi.get("type"), (mi.get("source_url") or "")[:80])

    log.info("  availability=%s", obs.get("availability"))
    log.info("  category_path=%s", obs.get("category_path"))

    # 4) Pre-check: normalize_eye dry run
    log.info("--- DRY RUN: normalize_eye ---")
    try:
        from r52.domain.normalize import normalize_eye
        normalized, cleaned = normalize_eye(obs)
        log.info("  normalized.source=%s", normalized.get("source"))
        log.info("  normalized.listing_id=%s", normalized.get("listing_id"))
        log.info("  normalized.url=%s", normalized.get("url"))
        log.info("  normalized.title=%s", (normalized.get("title") or "")[:60])
        log.info("  normalized.prices: %d", len(normalized.get("prices", [])))
        for p in normalized.get("prices", []):
            log.info("    role=%s minor=%s cur=%s label=%s", p.get("role"), p.get("amount_minor"), p.get("currency"), p.get("label"))
        log.info("  normalized.availability=%s", normalized.get("availability"))
        log.info("  normalized.completeness=%s", normalized.get("completeness"))
        log.info("  DRY RUN OK")
    except Exception as e:
        log.error("  DRY RUN FAILED: %s", e)
        log.error("  %s", traceback.format_exc())
        log.error("  => normalize_eye rejects observation. Fix needed.")
        col.close()
        app.stop()
        return

    # 5) Inbox pipeline
    log.info("--- INBOX PIPELINE ---")
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    log.info("  payload: %d bytes", len(payload))

    staged, created = inbox.stage_bytes(payload, filename="e2e-farpost.json", origin="E2E_FP")
    log.info("  stage_bytes: id=%s created=%s status=%s err=%s",
             staged.get("id"), created, staged.get("status"), staged.get("error_code"))

    if staged.get("status") != "PENDING":
        log.error("  STAGE not PENDING. Aborting accept.")
        col.close()
        app.stop()
        return

    accepted = inbox.accept(staged["id"])
    log.info("  accept: product_id=%s obs_created=%s events=%d",
             accepted.get("product_id"), accepted.get("observation_created"),
             len(accepted.get("events", [])))

    # 6) DB verification
    log.info("--- DB VERIFICATION ---")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    prod_count = cur.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    obs_count = cur.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    pp_count = cur.execute("SELECT COUNT(*) FROM price_point").fetchone()[0]
    log.info("  AFTER: prod=%d obs=%d pp=%d", prod_count, obs_count, pp_count)
    log.info("  DELTA: +prod=%d +obs=%d +pp=%d",
             prod_count - base_prod, obs_count - base_obs, pp_count - base_pp)

    # Product row
    rows = cur.execute(
        "SELECT id, source, listing_id, title, url, brand, model, last_regular_minor, last_lowest_minor, last_availability, observation_count FROM product WHERE source='FARPOST' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        log.info("  PRODUCT #%d src=%s lid=%s title='%s' reg=%s low=%s avail=%s obs_n=%d",
                 r["id"], r["source"], r["listing_id"], (r["title"] or "")[:50],
                 r["last_regular_minor"], r["last_lowest_minor"], r["last_availability"], r["observation_count"])

    # Price points
    rows = cur.execute(
        "SELECT pp.id, pp.role, pp.amount_minor, pp.currency, pp.label, pp.observed_at, p.listing_id "
        "FROM price_point pp JOIN product p ON p.id=pp.product_id "
        "WHERE p.source='FARPOST' ORDER BY pp.id DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        log.info("  PP #%d role=%s minor=%d cur=%s label='%s' lid=%s",
                 r["id"], r["role"], r["amount_minor"], r["currency"], (r["label"] or "")[:30], r["listing_id"])

    # Observation
    rows = cur.execute(
        "SELECT o.id, o.observed_at, o.origin, o.evidence_level, o.availability, o.fingerprint "
        "FROM observation o JOIN product p ON p.id=o.product_id "
        "WHERE p.source='FARPOST' ORDER BY o.id DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        log.info("  OBS #%d at=%s origin=%s level=%s avail=%s fp=%s",
                 r["id"], r["observed_at"], r["origin"], r["evidence_level"], r["availability"], (r["fingerprint"] or "")[:16])

    con.close()

    # 7) Verdict
    d_pp = pp_count - base_pp
    d_obs = obs_count - base_obs
    d_prod = prod_count - base_prod
    if d_pp > 0:
        log.info("VERDICT: PASS — %d new price_point(s)", d_pp)
    elif d_obs > 0 and len(prices) > 0:
        log.info("VERDICT: PARTIAL — observation created, %d prices in collector, 0 new pp (dedup?)", len(prices))
    elif d_prod > 0:
        log.info("VERDICT: PARTIAL — product created, no observation")
    else:
        log.info("VERDICT: FAIL — no new records")

    col.close()
    app.stop()
    log.info("DONE")


if __name__ == "__main__":
    main()
