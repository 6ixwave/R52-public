"""probe_v97 — фокус-тест ОДНОЙ площадки.

Использование:
    python -u probe_v97.py WB
    python -u probe_v97.py OZON
    python -u probe_v97.py AVITO
    python -u probe_v97.py FARPOST

Вывод: stdout + probe_v97.log (дублирование, чтобы не потерять при таймауте).
Watchdog: убивает процесс через 90 с.
"""
import json, logging, os, signal, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "probe_v97.log"

# --- Logging: stdout + файл ---
_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _fh])
log = logging.getLogger("v97")

# --- Watchdog ---
WATCHDOG_SECS = 90
def _watchdog():
    time.sleep(WATCHDOG_SECS)
    log.error("WATCHDOG: %d s exceeded, forcing exit", WATCHDOG_SECS)
    os._exit(2)
threading.Thread(target=_watchdog, daemon=True).start()

# --- Args ---
SOURCE = sys.argv[1].upper() if len(sys.argv) > 1 else "WB"
TEST_IDS = {
    "WB": "259671175",
    "OZON": "3690613958",
    "AVITO": "1154321987",
    "FARPOST": "125270259",
}
LID = TEST_IDS.get(SOURCE, "0")

sys.path.insert(0, str(ROOT))
HOME = ROOT / "r52"
DB = HOME / "data" / "r52.sqlite3"
INTAKE = HOME / "data" / "intake"
INTAKE.mkdir(parents=True, exist_ok=True)

def main():
    log.info("=== probe_v97 START source=%s lid=%s ===", SOURCE, LID)
    log.info("DB: %s (exists=%s)", DB, DB.exists())

    # 1. App init
    t0 = time.monotonic()
    from r52.config import load_config
    from r52.service import App
    config = load_config(home=str(HOME))
    log.info("Config loaded in %.1fs. db_file=%s intake_folders=%s",
             time.monotonic()-t0, config.paths.db_file, config.intake_folders)

    t1 = time.monotonic()
    app = App(config, install_guard=False)
    app.open()
    log.info("App opened in %.1fs. auto_accept=%s",
             time.monotonic()-t1, app.settings.get("intake.auto_accept"))

    # 2. Collector
    from r52.collectors import COLLECTOR_REGISTRY
    if SOURCE not in COLLECTOR_REGISTRY:
        log.error("Unknown source %s", SOURCE)
        app.stop()
        return
    cls, display = COLLECTOR_REGISTRY[SOURCE]
    col = cls(data_dir=app.config.paths.data_dir, use_proxies=False)

    # Patch session timeouts to be strict
    col.session.adapters.clear()
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    adapter = HTTPAdapter(max_retries=Retry(total=1, backoff_factor=2,
                                            status_forcelist=[429, 500, 502, 503, 504]))
    col.session.mount("https://", adapter)
    col.session.mount("http://", adapter)
    # Override timeouts on the pool
    col.pool.connect_timeout = 8
    col.pool.read_timeout = 15
    log.info("Collector %s ready (strict timeouts 8s/15s)", display)

    url = col.build_url(LID)
    log.info("URL: %s", url)

    t2 = time.monotonic()
    try:
        obs = col.collect_product(LID, url)
    except Exception as e:
        log.exception("COLLECT EXCEPTION after %.1fs: %s", time.monotonic()-t2, e)
        app.stop()
        return
    elapsed = time.monotonic() - t2
    log.info("Collect done in %.1fs, result=%s", elapsed, "yes" if obs else "no")

    if not obs:
        log.warning("No observation returned")
        app.stop()
        return

    # 3. Write JSON
    written = col.write_observations([obs], INTAKE)
    log.info("Written: %s", [Path(w).name for w in written])

    # 4. Inbox stage + accept
    for wp in written:
        data = Path(wp).read_bytes()
        item, created = app.inbox.stage_bytes(data, filename=Path(wp).name, origin="COLLECTOR")
        log.info("Staged: id=%s created=%s status=%s err=%s",
                 item.get("id"), created, item.get("status"), item.get("error_code"))
        if item.get("status") == "PENDING":
            res = app.inbox.accept(item["id"])
            log.info("Accepted: pid=%s obs_created=%s",
                     res.get("product_id"), res.get("observation_created"))
        elif item.get("status") == "ACCEPTED":
            log.info("Auto-accepted")
        elif item.get("status") == "INVALID":
            log.warning("INVALID: %s", item.get("error_code"))

    # 5. DB verify
    import sqlite3
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    prods = [dict(r) for r in conn.execute(
        "SELECT id,source,listing_id,title,last_regular_minor,last_availability,observation_count "
        "FROM product WHERE source=? AND listing_id=? ORDER BY id DESC LIMIT 3", (SOURCE, LID))]
    pps = [dict(r) for r in conn.execute(
        "SELECT pp.id,pp.amount_minor,pp.currency,pp.label "
        "FROM price_point pp JOIN product p ON pp.product_id=p.id "
        "WHERE p.source=? AND p.listing_id=? ORDER BY pp.id DESC LIMIT 5", (SOURCE, LID))]
    conn.close()
    log.info("DB: %d products, %d price_points for %s/%s", len(prods), len(pps), SOURCE, LID)
    for p in prods:
        log.info("  P: id=%s title=%s reg=%s avail=%s obs=%s",
                 p["id"], str(p["title"])[:50], p["last_regular_minor"],
                 p["last_availability"], p["observation_count"])
    for pp in pps[:3]:
        log.info("  PP: amt=%s cur=%s label=%s", pp["amount_minor"], pp["currency"], pp["label"])

    # Verdict
    has_p = len(prods) > 0
    has_t = has_p and not str(prods[0]["title"]).startswith(f"{SOURCE}-")
    has_pp = len(pps) > 0 and pps[0]["amount_minor"] is not None
    verdict = f"{'PASS' if has_p else 'FAIL'}{'_TITLE' if has_t else '_FBT'}{'_PRICE' if has_pp else '_NOP'}"
    log.info("VERDICT: %s (total %.1fs)", verdict, time.monotonic()-t0)

    app.stop()
    log.info("=== probe_v97 DONE ===")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("FATAL")
        sys.exit(1)
