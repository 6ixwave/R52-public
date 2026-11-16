"""probe_v98 — WB E2E: optimized basket scan, detailed timing, 180s watchdog.

Использование:
    python -u probe_v98.py

Вывод: stdout + probe_v98.log
"""
import json, logging, os, random, sys, time, threading
from pathlib import Path

ROOT = Path(__file__).parent
LOG_FILE = ROOT / "probe_v98.log"

_fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_sh, _fh])
log = logging.getLogger("v98")

WATCHDOG_SECS = 180
def _watchdog():
    time.sleep(WATCHDOG_SECS)
    log.error("WATCHDOG: %d s exceeded, forcing exit", WATCHDOG_SECS)
    os._exit(2)
threading.Thread(target=_watchdog, daemon=True).start()

SOURCE = "WB"
LID = "259671175"

sys.path.insert(0, str(ROOT))
HOME = ROOT / "r52"
INTAKE = HOME / "data" / "intake"
INTAKE.mkdir(parents=True, exist_ok=True)


def main():
    t_start = time.monotonic()
    log.info("=== probe_v98 START source=%s lid=%s ===", SOURCE, LID)

    # ---- Phase 0: Quick connectivity (bypasses collector entirely) ----
    t0 = time.monotonic()
    log.info("P0: Testing connectivity to card.wb.ru ...")
    try:
        from curl_cffi import requests as cffi_req
        r = cffi_req.get(
            f"https://card.wb.ru/cards/v2/detail?nm={LID}",
            headers={"Accept": "application/json"},
            impersonate="chrome", timeout=15)
        log.info("P0: card.wb.ru HTTP %d in %.1fs, body[:300]=%s",
                 r.status_code, time.monotonic() - t0, r.text[:300])
    except Exception as e:
        log.error("P0: connectivity FAIL: %s (%.1fs)", e, time.monotonic() - t0)

    # ---- Phase 1: Config ----
    t0 = time.monotonic()
    from r52.config import load_config
    config = load_config(home=str(HOME))
    db_path = Path(config.paths.db_file)
    log.info("P1 Config: %.1fs db=%s exists=%s intake=%s",
             time.monotonic() - t0, db_path, db_path.exists(), config.intake_folders)
    alt_db = HOME / "data" / "r52.sqlite3"
    log.info("P1 Alt DB: %s exists=%s", alt_db, alt_db.exists())

    # ---- Phase 2: App ----
    t0 = time.monotonic()
    from r52.service import App
    app = App(config, install_guard=False)
    app.open()
    log.info("P2 App: %.1fs", time.monotonic() - t0)

    # ---- Phase 3: Collector with patches ----
    t0 = time.monotonic()
    from r52.collectors import COLLECTOR_REGISTRY
    from r52.collectors.wb import JSON_HEADERS as WB_JSON
    cls, display = COLLECTOR_REGISTRY[SOURCE]
    col = cls(data_dir=app.config.paths.data_dir, use_proxies=False)
    log.info("P3 Collector: %.1fs cffi=%s", time.monotonic() - t0,
             "yes" if col._cffi else "no")

    # Patch: fast throttle 1-2 s (was 2-4)
    def _fast():
        now = time.monotonic()
        wait = random.uniform(1, 2) - (now - col._last_request)
        if wait > 0:
            time.sleep(wait)
        col._last_request = time.monotonic()
    col._fast_throttle = _fast

    # Patch: normal throttle 3-5 s (was 8-20)
    def _normal():
        now = time.monotonic()
        wait = random.uniform(3, 5) - (now - col._last_request)
        if wait > 0:
            time.sleep(wait)
        col._last_request = time.monotonic()
    col._throttle = _normal

    # Patch: basket scan — 10 hosts, 6 s timeout (was 60 hosts, 8 s)
    _local_cache = {}
    def _fast_basket(nm_id, vol, path):
        if vol in _local_cache:
            n = _local_cache[vol]
            return f"basket-{n:02d}.wbbasket.ru"
        for n in range(1, 11):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            t_b = time.monotonic()
            resp = col._http_get(url, headers=WB_JSON, timeout=6, fast=True)
            code = resp.status_code if resp is not None else "None"
            log.info("P3 basket: %s -> %s (%.1fs)", host, code,
                     time.monotonic() - t_b)
            if resp is not None and resp.status_code == 200:
                _local_cache[vol] = n
                log.info("P3 basket FOUND: vol=%d -> %s", vol, host)
                return host
        log.warning("P3 basket: no host in 1-10 for vol=%d", vol)
        return None
    col._find_basket_host = _fast_basket

    # ---- Phase 4: Collect ----
    url = col.build_url(LID)
    log.info("P4 URL: %s", url)
    t0 = time.monotonic()
    try:
        obs = col.collect_product(LID, url)
    except Exception as e:
        log.exception("P4 COLLECT EXCEPTION after %.1fs: %s",
                      time.monotonic() - t0, e)
        app.stop()
        return
    elapsed = time.monotonic() - t0
    log.info("P4 Collect: %.1fs result=%s stats=%s",
             elapsed, "yes" if obs else "no", col.stats)

    if not obs:
        log.warning("P4: No observation returned")
        app.stop()
        return

    # ---- Phase 5: Write JSON ----
    t0 = time.monotonic()
    written = col.write_observations([obs], INTAKE)
    log.info("P5 Written: %s (%.1fs)",
             [Path(w).name for w in written], time.monotonic() - t0)

    # ---- Phase 6: Inbox ----
    t0 = time.monotonic()
    for wp in written:
        data = Path(wp).read_bytes()
        fname = Path(wp).name
        item, created = app.inbox.stage_bytes(
            data, filename=fname, origin="COLLECTOR")
        log.info("P6 Staged: id=%s status=%s err=%s",
                 item.get("id"), item.get("status"), item.get("error_code"))
        if item.get("status") == "PENDING":
            res = app.inbox.accept(item["id"])
            log.info("P6 Accepted: pid=%s obs=%s",
                     res.get("product_id"), res.get("observation_created"))
        elif item.get("status") == "ACCEPTED":
            log.info("P6 Auto-accepted")
        elif item.get("status") == "INVALID":
            log.warning("P6 INVALID: %s", item.get("error_code"))
    log.info("P6 Inbox: %.1fs", time.monotonic() - t0)

    # ---- Phase 7: DB verify ----
    t0 = time.monotonic()
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    prods = [dict(r) for r in conn.execute(
        "SELECT id,source,listing_id,title,last_regular_minor,"
        "last_availability,observation_count "
        "FROM product WHERE source=? AND listing_id=? "
        "ORDER BY id DESC LIMIT 3", (SOURCE, LID))]
    pps = [dict(r) for r in conn.execute(
        "SELECT pp.id,pp.amount_minor,pp.currency,pp.label "
        "FROM price_point pp JOIN product p ON pp.product_id=p.id "
        "WHERE p.source=? AND p.listing_id=? "
        "ORDER BY pp.id DESC LIMIT 5", (SOURCE, LID))]
    conn.close()
    log.info("P7 DB: %d products, %d pps (%.1fs)",
             len(prods), len(pps), time.monotonic() - t0)
    for p in prods:
        log.info("P7  P: id=%s title=%s reg=%s avail=%s obs=%s",
                 p["id"], str(p["title"])[:60], p["last_regular_minor"],
                 p["last_availability"], p["observation_count"])
    for pp in pps[:3]:
        log.info("P7  PP: amt=%s cur=%s label=%s",
                 pp["amount_minor"], pp["currency"], pp["label"])

    # ---- Verdict ----
    has_p = len(prods) > 0
    has_t = has_p and not str(prods[0]["title"]).startswith(f"{SOURCE}-")
    has_pp = len(pps) > 0 and pps[0]["amount_minor"] is not None
    verdict = (f"{'PASS' if has_p else 'FAIL'}"
               f"{'_TITLE' if has_t else '_FBT'}"
               f"{'_PRICE' if has_pp else '_NOP'}")
    log.info("VERDICT: %s (total %.1fs)", verdict, time.monotonic() - t_start)

    app.stop()
    log.info("=== probe_v98 DONE ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("FATAL")
        sys.exit(1)
