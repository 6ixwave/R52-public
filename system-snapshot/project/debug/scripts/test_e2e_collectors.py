"""E2E-тест конвейера сборщиков: subprocess → JSON → Eye → SQLite.

Запуск:
    python debug/scripts/test_e2e_collectors.py

Проверяет:
1. Импорт и реестр сборщиков.
2. Запуск каждого сборщика через subprocess (1 товар).
3. Полноту JSON: title, price, availability, photos.
4. Обработку через Inbox (stage_bytes → accept).
5. Запись в SQLite (product, observation).
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Корень проекта
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("test_e2e")

# Тестовые ID товаров (известные рабочие)
TEST_IDS = {
    "WB": ["1097975275"],           # iPhone 17 Pro Max (подтверждён)
    "OZON": ["286156832"],          # iPhone 17 Pro Max
    "FARPOST": ["287654321"],       # iPhone 18 Pro Max
    "ALIEXPRESS_RU": [],            # search mode
}
TEST_QUERY = {"ALIEXPRESS_RU": "iphone 17 pro max"}

# Папки
OUTPUT_BASE = ROOT / "collect" / "output"
INBOX_DIR = ROOT / "collect" / "inbox"
RESULTS = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    RESULTS.append({"test": name, "status": status, "detail": detail})
    log.info("[%s] %s %s", status, name, detail)
    return ok


def test_registry():
    """1. Реестр и импорты."""
    from r52.sources import collectors_status, SOURCE_KEYS
    statuses = collectors_status()
    all_ready = all(s["collector"] == "READY" for s in statuses)
    check("registry_all_ready", all_ready,
          f"{len(statuses)} sources: " + ", ".join(f"{s['source']}={s['collector']}" for s in statuses))

    from r52.collectors import (
        WBCollector, OzonCollector, AvitoCollector,
        FarpostCollector, AliExpressCollector, AliExpressRUCollector,
    )
    check("import_all_collectors", True, "6 classes imported")


def test_subprocess(source, listing_ids, output_dir, use_proxies=False, query=None):
    """2. Запуск сборщика через subprocess."""
    from r52.collectors.runner import run_collector_subprocess

    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        result = run_collector_subprocess(
            source, listing_ids, output_dir,
            data_dir=None, use_proxies=use_proxies, query=query
        )
    except Exception as e:
        log.exception("Subprocess error for %s", source)
        check(f"subprocess_{source}", False, str(e)[:200])
        return None

    elapsed = round(time.monotonic() - t0, 1)
    result["elapsed_total"] = elapsed
    log.info("Subprocess %s: %s", source, json.dumps(result, ensure_ascii=False))

    if result.get("status") == "ERROR":
        check(f"subprocess_{source}", False, f"exit_code={result.get('exit_code')}, stderr={result.get('stderr','')[:200]}")
    elif result.get("status") == "TIMEOUT":
        check(f"subprocess_{source}", False, f"timeout={result.get('timeout_sec')}s")
    else:
        check(f"subprocess_{source}", True,
              f"items={result.get('total_items')}, collected={result.get('collected')}, "
              f"failed={result.get('failed')}, files={result.get('files_written')}, {elapsed}s")
    return result


def test_json_completeness(source, output_dir):
    """3. Полнота JSON: title, price, availability, photos."""
    json_files = list(output_dir.glob("jarvis-eye-*.json"))
    if not json_files:
        check(f"json_{source}_exists", False, "no jarvis-eye-*.json found")
        return

    json_file = sorted(json_files, key=lambda p: p.stat().st_mtime)[-1]
    data = json.loads(json_file.read_text(encoding="utf-8"))
    obs = data.get("observation", {})
    fields = obs.get("fields", {})

    # Title
    title = fields.get("title", {}).get("value", "")
    check(f"json_{source}_title", len(title) > 5, f"title={title[:60]!r}")

    # Price: ищем в price_candidates (массив) или fields.price
    price_candidates = obs.get("price_candidates", [])
    price_val = ""
    if price_candidates:
        # Берём первый кандидат с amount
        for pc in price_candidates:
            if isinstance(pc, dict) and pc.get("amount_minor"):
                price_val = f"{pc.get('amount_minor', 0)/100:.0f} {pc.get('currency', 'RUB')}"
                break
            elif isinstance(pc, dict) and pc.get("text"):
                price_val = pc["text"]
                break
    if not price_val:
        price_field = fields.get("price", {})
        price_val = price_field.get("value", "")
    check(f"json_{source}_price", bool(price_val),
          f"price={str(price_val)[:40]!r}, candidates={len(price_candidates)}")

    # Availability: top-level observation.availability
    avail = obs.get("availability", "")
    if not avail:
        avail = fields.get("availability", {}).get("value", "")
    check(f"json_{source}_availability", bool(avail) and avail != "UNKNOWN",
          f"avail={str(avail)[:40]!r}")

    # Photos (media)
    media = obs.get("media", {})
    items = media.get("items", [])
    photos = [i for i in items if i.get("type") == "image"]
    check(f"json_{source}_photos", len(photos) > 0,
          f"photos={len(photos)}, total_media={len(items)}")

    # Format check
    fmt = data.get("format", "")
    check(f"json_{source}_format", fmt in (
        "JARVIS_BR02_EYE_USER_CAPTURE", "JARVIS_BR02_COLLECTOR_AUTONOMOUS"),
        f"format={fmt}")

    # Status
    status = obs.get("status", "")
    check(f"json_{source}_status", status in ("SUCCESS", "PARTIAL_SUCCESS"),
          f"status={status}")


def test_eye_pipeline(source, output_dir):
    """4. Обработка через Inbox: stage_bytes → accept → SQLite."""
    from r52.config import load_config
    from r52.service import App
    from r52.intake.eye import Inbox

    config = load_config(home=str(ROOT / "data" / "test_home"))
    app = App(config, install_guard=False)
    app.open()

    try:
        inbox = Inbox(app)

        json_files = list(output_dir.glob("jarvis-eye-*.json"))
        if not json_files:
            check(f"eye_{source}_file", False, "no JSON to process")
            return

        json_file = sorted(json_files, key=lambda p: p.stat().st_mtime)[-1]
        data_bytes = json_file.read_bytes()

        # Stage
        try:
            item, created = inbox.stage_bytes(data_bytes, filename=json_file.name, origin="COLLECTOR")
            check(f"eye_{source}_stage", True,
                  f"status={item['status']}, created={created}, id={item.get('id')}")
        except Exception as e:
            check(f"eye_{source}_stage", False, str(e)[:200])
            return

        if item["status"] == "PENDING":
            try:
                accepted = inbox.accept(item["id"])
                check(f"eye_{source}_accept", True,
                      f"product_id={accepted.get('product_id')}, "
                      f"obs_created={accepted.get('observation_created')}")
            except Exception as e:
                check(f"eye_{source}_accept", False, str(e)[:200])
        elif item["status"] == "ACCEPTED":
            check(f"eye_{source}_accept", True, "auto-accepted")
        else:
            check(f"eye_{source}_accept", False, f"status={item['status']}, err={item.get('error_code')}")

        # SQLite check — таблицы: product, observation (ед.ч.)
        try:
            with app.db.read() as conn:
                products = conn.execute(
                    "SELECT id, source, listing_id, title FROM product WHERE source = ? ORDER BY id DESC LIMIT 3",
                    (source,)
                ).fetchall()
                obs_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM observation WHERE product_id IN (SELECT id FROM product WHERE source = ?)",
                    (source,)
                ).fetchone()["cnt"]

            check(f"eye_{source}_sqlite", len(products) > 0,
                  f"products={len(products)}, observations={obs_count}")
        except Exception as e:
            check(f"eye_{source}_sqlite", False, f"DB error: {str(e)[:150]}")

    finally:
        app.db.close()
        app.lock.release()
        app._opened = False


def main():
    log.info("=" * 60)
    log.info("E2E COLLECTOR TEST — %s", datetime.now().isoformat())
    log.info("=" * 60)

    # 1. Registry
    test_registry()

    # 2-4. Per-source tests
    for source, ids in TEST_IDS.items():
        log.info("-" * 40)
        log.info("SOURCE: %s", source)
        output_dir = OUTPUT_BASE / source
        query = TEST_QUERY.get(source)

        result = test_subprocess(source, ids, output_dir, use_proxies=False, query=query)
        test_json_completeness(source, output_dir)
        test_eye_pipeline(source, output_dir)

    # Summary
    log.info("=" * 60)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    log.info("SUMMARY: %d PASS, %d FAIL (total %d)", passed, failed, len(RESULTS))

    for r in RESULTS:
        log.info("  [%s] %s — %s", r["status"], r["test"], r["detail"][:80])

    report = {
        "timestamp": datetime.now(timezone(timedelta(hours=10))).isoformat(),
        "total": len(RESULTS),
        "passed": passed,
        "failed": failed,
        "results": RESULTS,
    }
    report_path = ROOT / "debug" / "outputs" / "e2e_collectors_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Report: %s", report_path)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
