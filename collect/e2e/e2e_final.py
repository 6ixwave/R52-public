"""E2E Final v2: все 4 маркетплейса через штатную цепочку.
Запуск: python e2e_final.py
Результат: e2e_final_result.json + stdout
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TEST_IDS = {
    "WB": ["1097975275"],
    "OZON": ["562267"],
    "AVITO": ["7494308705"],
    "FARPOST": ["1154321987"],
}

WORK_DIR = ROOT / "e2e-final-work"
OUTPUT_DIR = WORK_DIR / "collector_out"
DB_PATH = WORK_DIR / "r52.sqlite3"

results = {}
t_start = time.monotonic()


def log(msg):
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def setup():
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    log(f"Рабочая папка: {WORK_DIR}")


def run_collector(source, ids):
    """Запускает сборщик в subprocess, возвращает список jarvis-eye-*.json."""
    out_dir = OUTPUT_DIR / source
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "r52.collectors.runner",
        "--source", source,
        "--ids", ",".join(ids),
        "--output-dir", str(out_dir),
        "--no-proxies",
    ]
    log(f"Запуск: {' '.join(cmd)}")
    t0 = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        elapsed = time.monotonic() - t0
        log(f"  exit={proc.returncode} за {elapsed:.1f}s")
        if proc.stdout:
            for line in proc.stdout.strip().splitlines()[-5:]:
                log(f"  stdout: {line}")
        if proc.stderr:
            for line in proc.stderr.strip().splitlines()[-5:]:
                log(f"  stderr: {line}")
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT 180s")
        return []
    except Exception as e:
        log(f"  ERROR: {e}")
        return []

    # Только jarvis-eye-*.json (не runner-*-result.json)
    files = sorted(out_dir.glob("jarvis-eye-*.json"))
    log(f"  JSON-файлов (jarvis-eye): {len(files)}")
    for f in files:
        log(f"    {f.name} ({f.stat().st_size} bytes)")
    return files


def make_app():
    """Изолированный App для Inbox (без сервера, без фоновых потоков)."""
    from r52.db import Database, migrate

    class FakeClock:
        def now(self):
            return datetime.now(TZ)

    class MockNotifier:
        def notify(self, *a, **k):
            return None
        def __getattr__(self, name):
            return lambda *a, **k: None

    app = type("App", (), {})()
    app.clock = FakeClock()
    app.db = Database(str(DB_PATH))
    app.db.initialize()
    migrate(app.db)
    app.settings = {"intake.auto_accept": True}
    app.notifier = MockNotifier()
    return app


def feed_inbox(source, json_files):
    """Прогоняет JSON через Inbox.stage_bytes + accept."""
    from r52.intake.eye import Inbox

    app = make_app()
    inbox = Inbox(app)
    fed = []

    for jf in json_files:
        data = jf.read_bytes()
        try:
            item, created = inbox.stage_bytes(data, filename=jf.name, origin=f"COLLECTOR_{source}")
            log(f"  stage: {jf.name} -> status={item['status']} created={created}")

            if item["status"] == "PENDING":
                accepted = inbox.accept(item["id"])
                log(f"  accept: product_id={accepted.get('product_id')} obs_created={accepted.get('observation_created')}")
                fed.append({
                    "file": jf.name,
                    "status": "ACCEPTED",
                    "product_id": accepted.get("product_id"),
                    "observation_created": accepted.get("observation_created"),
                })
            elif item["status"] == "INVALID":
                fed.append({"file": jf.name, "status": "INVALID", "error": item.get("error_code")})
            else:
                fed.append({"file": jf.name, "status": item["status"]})
        except Exception as e:
            log(f"  ERROR feeding {jf.name}: {e}")
            traceback.print_exc()
            fed.append({"file": jf.name, "status": "ERROR", "error": str(e)[:300]})

    return fed


def verify_sqlite(source):
    """Проверяет SQLite: product, observation, price_point."""
    import sqlite3
    if not DB_PATH.exists():
        return {"db_exists": False}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        products = conn.execute(
            "SELECT id, source, listing_id, title, url FROM product WHERE source=?", (source,)
        ).fetchall()
        observations = conn.execute(
            "SELECT o.id, o.product_id, o.observed_at, o.availability "
            "FROM observation o JOIN product p ON o.product_id=p.id WHERE p.source=?", (source,)
        ).fetchall()
        price_points = conn.execute(
            "SELECT pp.id, pp.observation_id, pp.role, pp.amount_minor, pp.currency "
            "FROM price_point pp JOIN observation o ON pp.observation_id=o.id "
            "JOIN product p ON o.product_id=p.id WHERE p.source=?", (source,)
        ).fetchall()
        return {
            "db_exists": True,
            "products": [dict(r) for r in products],
            "observations": [dict(r) for r in observations],
            "price_points": [dict(r) for r in price_points],
            "product_count": len(products),
            "observation_count": len(observations),
            "price_point_count": len(price_points),
        }
    finally:
        conn.close()


def main():
    setup()

    for source, ids in TEST_IDS.items():
        log(f"{'=' * 50}")
        log(f"ПЛОЩАДКА: {source} (ID: {ids})")
        log(f"{'=' * 50}")

        src_result = {"source": source, "ids": ids, "steps": {}}
        t_src = time.monotonic()

        # Шаг 1: Collector
        json_files = run_collector(source, ids)
        src_result["steps"]["collector"] = {
            "files": [f.name for f in json_files],
            "file_count": len(json_files),
        }

        if not json_files:
            src_result["verdict"] = "COLLECTOR_FAILED"
            src_result["elapsed"] = round(time.monotonic() - t_src, 1)
            results[source] = src_result
            continue

        # Шаг 2: Inbox
        try:
            fed = feed_inbox(source, json_files)
            src_result["steps"]["inbox"] = fed
        except Exception as e:
            log(f"  INBOX ERROR: {e}")
            traceback.print_exc()
            src_result["steps"]["inbox"] = {"error": str(e)[:500]}
            fed = []

        # Шаг 3: SQLite
        try:
            sq = verify_sqlite(source)
            src_result["steps"]["sqlite"] = sq
        except Exception as e:
            src_result["steps"]["sqlite"] = {"error": str(e)[:300]}
            sq = {}

        # Вердикт
        has_product = sq.get("product_count", 0) > 0
        has_obs = sq.get("observation_count", 0) > 0
        has_price = sq.get("price_point_count", 0) > 0
        has_title = any(p.get("title") for p in sq.get("products", []))

        if has_product and has_obs and has_price and has_title:
            src_result["verdict"] = "E2E_PASS"
        elif has_product and has_obs:
            src_result["verdict"] = "E2E_PARTIAL"
        else:
            src_result["verdict"] = "E2E_FAIL"

        src_result["elapsed"] = round(time.monotonic() - t_src, 1)
        results[source] = src_result
        log(f"  VERDICT: {src_result['verdict']} ({src_result['elapsed']}s)")
        log(f"  products={sq.get('product_count', 0)} obs={sq.get('observation_count', 0)} prices={sq.get('price_point_count', 0)}")

        time.sleep(2)

    # Итог
    total_elapsed = round(time.monotonic() - t_start, 1)
    passed = sum(1 for r in results.values() if r["verdict"] == "E2E_PASS")
    partial = sum(1 for r in results.values() if r["verdict"] == "E2E_PARTIAL")
    summary = {
        "timestamp": datetime.now(TZ).isoformat(),
        "total_seconds": total_elapsed,
        "passed": passed,
        "partial": partial,
        "total": len(TEST_IDS),
        "verdict": f"PASS={passed} PARTIAL={partial}/{len(TEST_IDS)}",
        "details": results,
    }

    out_file = ROOT / "e2e_final_result.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    log(f"{'=' * 50}")
    log(f"ИТОГ: PASS={passed} PARTIAL={partial}/{len(TEST_IDS)} за {total_elapsed}s")
    for src, r in results.items():
        log(f"  {src}: {r['verdict']}")
    log(f"Файл: {out_file}")
    log("E2E_FINAL_DONE")


if __name__ == "__main__":
    main()
