"""
Тест полного конвейера R-52: коллекторы → publish → watcher → inbox → accept → product/observation.
Запуск: python debug/test_full_pipeline.py
"""
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

HOME = PROJECT_ROOT / "data" / "test_home"
LOGS = HOME / "logs"
LOGS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS / "test_pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("test.pipeline")

SOURCES = ["WB", "OZON", "AVITO", "FARPOST", "ALIEXPRESS", "ALIEXPRESS_RU"]
RESULTS = {}


def test_collector(source: str):
    """Тестирует один коллектор: search → collect_product → проверка формата и цены."""
    log.info("=== TEST START: %s ===", source)
    t0 = time.monotonic()
    collector = None
    try:
        from r52.collectors.runner import _get_collector_class
        collector = _get_collector_class(source)(data_dir=HOME, use_proxies=False)

        # 1. Поиск
        query = "тест"
        rows = collector.collect_by_search(query, page=1, page_size=3)
        valid = [r for r in rows if isinstance(r, dict)
                 and r.get("observation", {}).get("status") not in ("FAILED", "BLOCKED")]

        if not valid:
            statuses = [r.get("observation", {}).get("status") for r in rows if isinstance(r, dict)]
            raise ValueError(f"No valid rows from search '{query}', got {len(rows)} rows: {statuses}")

        # 2. Проверка обязательных полей
        first = valid[0]["observation"]
        required = ["source", "listing_id", "title", "url", "observed_at", "status"]
        missing = [f for f in required if f not in first]
        if missing:
            raise ValueError(f"Missing fields: {missing}")

        # 3. Проверка цены
        prices = first.get("price_candidates", [])
        has_price = any(p.get("amount") and p.get("currency") for p in prices)

        # 4. collect_product для первого найденного
        listing_id = first["listing_id"]
        url = collector.build_url(listing_id)
        product_row = collector.collect_product(listing_id, url)
        product_status = product_row.get("observation", {}).get("status") if product_row else "NO_RESULT"

        elapsed = time.monotonic() - t0
        RESULTS[source] = {
            "status": "PASS",
            "search_rows": len(rows),
            "valid_rows": len(valid),
            "has_price": has_price,
            "price_candidates": len(prices),
            "first_price": prices[0] if prices else None,
            "product_status": product_status,
            "elapsed_sec": round(elapsed, 1),
            "errors": [],
        }
        log.info("=== TEST PASS: %s (%.1fs) search=%d valid=%d price=%s product=%s ===",
                 source, elapsed, len(rows), len(valid), has_price, product_status)
    except Exception as e:
        elapsed = time.monotonic() - t0
        RESULTS[source] = {
            "status": "FAIL",
            "error": str(e)[:500],
            "traceback": traceback.format_exc()[-1000:],
            "elapsed_sec": round(elapsed, 1),
        }
        log.error("=== TEST FAIL: %s (%.1fs) %s ===", source, elapsed, str(e)[:200])
    finally:
        if collector:
            try:
                collector.close()
            except Exception:
                pass


def test_intake_pipeline():
    """Тестирует конвейер intake: publish → watcher → inbox → accept → product/observation."""
    log.info("=== TEST START: INTAKE PIPELINE ===")
    t0 = time.monotonic()
    app = None
    try:
        from r52.config import Config, Paths
        from r52.service import App

        config = Config(paths=Paths(home=HOME))
        app = App(config, install_guard=False)
        app.open()

        # 1. Проверка watcher
        assert app.watcher is not None, "Watcher not initialized"
        app.watcher.start()

        # 2. Папка auto-inbox
        auto_inbox = HOME / "auto-inbox"
        auto_inbox.mkdir(exist_ok=True)

        # 3. Тестовый файл JARVIS_BR02_COLLECTOR_AUTONOMOUS
        test_obs = {
            "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
            "capture_action": "COLLECTOR_RUN",
            "fully_autonomous": True,
            "collector_source": "WB",
            "observation": {
                "source": "WB",
                "id": "999999999",
                "listing_id": "999999999",
                "url": "https://www.wildberries.ru/catalog/999999999/detail.aspx",
                "title": "Test Pipeline Product",
                "observed_at": "2026-11-14T16:00:00+00:00",
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
                "availability": "IN_STOCK",
                "price_candidates": [
                    {"role": "current", "amount": "999", "amount_minor": 99900,
                     "currency": "RUB", "visible_label": "999 \u20bd"}
                ],
                "fields": {
                    "title": {"status": "KNOWN", "value": "Test Pipeline Product",
                              "method": "CARD_JSON", "evidence": {"method": "CARD_JSON", "source": "WB"}},
                    "availability": {"status": "KNOWN", "value": "IN_STOCK",
                                     "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "WB"}}
                },
                "media": {"items": [], "image_data": None, "source_url": None, "method": None}
            }
        }

        import hashlib
        raw = json.dumps(test_obs, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        target = auto_inbox / f"jarvis-eye-auto-{digest}.json"
        target.write_bytes(raw)

        # 4. Ждём watcher (минимум 2 тика × 2 сек + запас)
        time.sleep(6)

        # 5. Проверка intake_file
        with app.db.read() as conn:
            intake_rows = conn.execute(
                "SELECT * FROM intake_file WHERE path LIKE ?", (str(auto_inbox) + "%",)).fetchall()

        # 6. Проверка inbox_item
        with app.db.read() as conn:
            inbox_rows = conn.execute(
                "SELECT * FROM inbox_item WHERE content_sha256 = ?", (digest,)).fetchall()

        # 7. Если PENDING — accept
        accepted = False
        if inbox_rows and inbox_rows[0]["status"] == "PENDING":
            result = app.inbox.accept(inbox_rows[0]["id"])
            accepted = True

        # 8. Проверка product + observation
        with app.db.read() as conn:
            product = conn.execute(
                "SELECT * FROM product WHERE listing_id = '999999999'").fetchone()
            obs = None
            if product:
                obs = conn.execute(
                    "SELECT * FROM observation WHERE product_id = ? ORDER BY id DESC LIMIT 1",
                    (product["id"],)).fetchone()

        # 9. Cleanup
        target.unlink(missing_ok=True)

        elapsed = time.monotonic() - t0
        ok = product is not None and obs is not None
        RESULTS["INTAKE"] = {
            "status": "PASS" if ok else "PARTIAL",
            "intake_files": len(intake_rows),
            "inbox_items": len(inbox_rows),
            "inbox_status": inbox_rows[0]["status"] if inbox_rows else "NOT_FOUND",
            "accepted": accepted,
            "product_id": product["id"] if product else None,
            "observation_id": obs["id"] if obs else None,
            "elapsed_sec": round(elapsed, 1),
        }
        log.info("=== TEST %s: INTAKE (%.1fs) intake=%d inbox=%d product=%s obs=%s ===",
                 "PASS" if ok else "PARTIAL", elapsed, len(intake_rows), len(inbox_rows),
                 product["id"] if product else None, obs["id"] if obs else None)
    except Exception as e:
        elapsed = time.monotonic() - t0
        RESULTS["INTAKE"] = {
            "status": "FAIL",
            "error": str(e)[:500],
            "traceback": traceback.format_exc()[-1000:],
            "elapsed_sec": round(elapsed, 1),
        }
        log.error("=== TEST FAIL: INTAKE (%.1fs) %s ===", elapsed, str(e)[:200])
    finally:
        if app:
            try:
                app.stop()
            except Exception:
                pass


def main():
    log.info("R-52 Full Pipeline Test — %d sources + intake", len(SOURCES))

    for source in SOURCES:
        test_collector(source)
        time.sleep(1)

    test_intake_pipeline()

    log.info("=" * 60)
    log.info("SUMMARY:")
    for name, r in RESULTS.items():
        log.info("  %-16s %s  %s", name, r["status"], r.get("error", f"{r.get('elapsed_sec', 0)}s"))

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": RESULTS,
        "pass_count": sum(1 for r in RESULTS.values() if r["status"] == "PASS"),
        "fail_count": sum(1 for r in RESULTS.values() if r["status"] == "FAIL"),
        "total": len(RESULTS),
    }
    report_path = PROJECT_ROOT / "debug" / "test_pipeline_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Report: %s", report_path)

    return 0 if report["fail_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
