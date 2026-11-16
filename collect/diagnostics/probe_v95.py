"""probe_v95 — E2E тест полного цикла: сбор → JSON → Inbox → SQLite.

Для каждой площадки берём 1 известный товар, прогоняем через:
  1. Collector.collect_product() → observation dict
  2. write_observations() → jarvis-eye-*.json в intake
  3. Inbox.stage_bytes() → inbox_item
  4. Inbox.accept() → normalize → product/observation/price_point
  5. Проверка SQLite: новые записи?

Запуск:
    C:\\Python315\\python.exe probe_v95.py 2>&1 | Tee-Object probe_v95_out.txt
"""

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v95")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "r52" / "data" / "r52.db"
INTAKE_DIR = ROOT / "r52" / "data" / "intake"
DATA_DIR = ROOT / "r52" / "data"

# Товары для теста (по 1 на площадку)
TEST_ITEMS = {
    "WB": "199298871",
    "OZON": "479010389",
    "AVITO": "1154321987",
    "FARPOST": "127622540",
}


def db_snapshot():
    """Снимок счётчиков БД."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    counts = {}
    for tbl in ("product", "observation", "price_point", "inbox_item", "intake_file"):
        row = conn.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()
        counts[tbl] = row["c"]
    conn.close()
    return counts


def check_new_records(before, after, source):
    """Проверить, появились ли новые записи для source."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    results = {}
    # Новые product
    prods = conn.execute(
        "SELECT id, listing_id, title, last_regular_minor, last_availability FROM product WHERE source=? ORDER BY id DESC LIMIT 3",
        (source,)
    ).fetchall()
    results["products"] = [dict(r) for r in prods]
    # Новые price_point (через product)
    pp = conn.execute(
        "SELECT pp.id, pp.amount_minor, pp.currency, pp.label, pp.observed_at "
        "FROM price_point pp JOIN product p ON pp.product_id = p.id "
        "WHERE p.source=? ORDER BY pp.id DESC LIMIT 5",
        (source,)
    ).fetchall()
    results["price_points"] = [dict(r) for r in pp]
    # Новые observation
    obs = conn.execute(
        "SELECT o.id, o.observed_at, o.origin, o.evidence_level, o.availability "
        "FROM observation o JOIN product p ON o.product_id = p.id "
        "WHERE p.source=? ORDER BY o.id DESC LIMIT 3",
        (source,)
    ).fetchall()
    results["observations"] = [dict(r) for r in obs]
    conn.close()
    return results


def run_platform(source, listing_id):
    """Прогнать одну площадку через полный цикл."""
    log.info("=" * 60)
    log.info("PLATFORM: %s | ID: %s", source, listing_id)
    log.info("=" * 60)

    before = db_snapshot()
    log.info("BEFORE: %s", before)

    # 1. Инициализация collector
    from r52.collectors import COLLECTOR_REGISTRY
    cls, display_name = COLLECTOR_REGISTRY[source]
    collector = cls(data_dir=DATA_DIR, use_proxies=False)
    log.info("Collector: %s (proxies=OFF for test)", display_name)

    # 2. Сбор
    url = collector.build_url(listing_id)
    log.info("URL: %s", url)

    t0 = time.monotonic()
    try:
        observation = collector.collect_product(listing_id, url)
    except Exception as e:
        log.error("COLLECT ERROR: %s", e)
        return {"source": source, "status": "COLLECT_ERROR", "error": str(e)[:300]}

    elapsed = time.monotonic() - t0
    log.info("Collect done in %.1fs", elapsed)

    if not observation:
        log.warning("Observation is None/empty")
        return {"source": source, "status": "NO_OBSERVATION"}

    # Проверка качества
    obs_data = observation.get("observation", observation)
    status = obs_data.get("status", "UNKNOWN")
    title = obs_data.get("title", "")
    price_candidates = obs_data.get("price_candidates", [])
    log.info("Status: %s | Title: %s | Prices: %d", status, title[:60], len(price_candidates))

    if price_candidates:
        for pc in price_candidates[:3]:
            log.info("  Price: %s %s (%s)", pc.get("amount"), pc.get("currency"), pc.get("label"))

    # 3. Запись JSON в intake
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    written = collector.write_observations([observation], INTAKE_DIR)
    log.info("Files written: %s", written)

    if not written:
        # Ручная запись если write_observations вернул пусто
        fname = f"jarvis-eye-{source.lower()}-v95-{listing_id}.json"
        fpath = INTAKE_DIR / fname
        fpath.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Manual write: %s", fpath)
        written = [str(fpath)]

    # 4. Inbox: stage + accept
    from r52.service import R52App
    app = R52App(data_dir=DATA_DIR)

    for fpath_str in written:
        fpath = Path(fpath_str)
        if not fpath.exists():
            log.warning("File not found: %s", fpath)
            continue
        data = fpath.read_bytes()
        log.info("Staging: %s (%d bytes)", fpath.name, len(data))

        item, created = app.inbox.stage_bytes(data, filename=fpath.name, origin="FOLDER")
        log.info("Staged: id=%s, created=%s, status=%s", item.get("id"), created, item.get("status"))

        if item.get("status") == "STAGED" or created:
            try:
                result = app.inbox.accept(item["id"])
                log.info("Accept result: %s", json.dumps(result, ensure_ascii=False)[:300] if result else "None")
            except Exception as e:
                log.error("Accept error: %s", e)
        else:
            log.warning("Not staged (duplicate or error): %s", item.get("status"))

    # 5. Проверка БД
    time.sleep(1)  # дать WAL flush
    after = db_snapshot()
    log.info("AFTER: %s", after)

    deltas = {k: after[k] - before[k] for k in before}
    log.info("DELTA: %s", deltas)

    details = check_new_records(before, after, source)
    log.info("DETAILS: %s", json.dumps(details, ensure_ascii=False, default=str)[:500])

    # Вердикт
    has_product = any(d["listing_id"] == listing_id for d in details["products"])
    has_price = len(details["price_points"]) > 0 and details["price_points"][0]["amount_minor"] is not None
    has_obs = len(details["observations"]) > 0

    verdict = "PASS" if (has_product and has_obs) else "PARTIAL"
    if has_price:
        verdict += "+PRICE"
    else:
        verdict += "-NO_PRICE"

    log.info("VERDICT: %s", verdict)

    app.close()
    return {
        "source": source,
        "status": status,
        "title": title[:80],
        "verdict": verdict,
        "deltas": deltas,
        "prices": details["price_points"][:3],
        "elapsed": round(elapsed, 1),
    }


def main():
    log.info("probe_v95 starting at %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("DB: %s", DB_PATH)
    log.info("Intake: %s", INTAKE_DIR)

    results = []
    for source, lid in TEST_ITEMS.items():
        try:
            r = run_platform(source, lid)
        except Exception as e:
            log.exception("Platform %s crashed: %s", source, e)
            r = {"source": source, "status": "CRASH", "error": str(e)[:300]}
        results.append(r)
        time.sleep(2)

    # Итог
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    for r in results:
        log.info("  %-8s | %-10s | %-40s | %s", r.get("source"), r.get("status"), r.get("title", "")[:40], r.get("verdict", r.get("error", "")))

    out = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
    out_file = ROOT / "probe_v95_result.json"
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Result: %s", out_file)
    log.info("DONE")


if __name__ == "__main__":
    main()
