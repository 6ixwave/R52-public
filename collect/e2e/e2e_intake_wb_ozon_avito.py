"""E2E: WB + Ozon + Avito JSON -> Inbox.stage_bytes -> Inbox.accept -> SQLite.

Использует свежий R52_HOME (e2e-fresh-home-2), чтобы не трогать Farpost-базу.
Запуск: python e2e_intake_wb_ozon_avito.py
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FRESH_HOME = ROOT / "e2e-fresh-home-2"
import shutil
if FRESH_HOME.exists():
    shutil.rmtree(FRESH_HOME)
FRESH_HOME.mkdir(parents=True)
os.environ["R52_HOME"] = str(FRESH_HOME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e-intake-wb-oz-av")

# --- 1. Конфигурация и открытие App ---
from r52.config import load_config
from r52.service import App

log.info("=== E2E WB + Ozon + Avito Intake (fresh home) ===")
config = load_config()
log.info("Home: %s", config.paths.home)
log.info("DB: %s", config.paths.db_file)
log.info("Intake folders: %s", config.intake_folders)

app = App(config, install_guard=False)
app.open()
log.info("App открыт. Миграции применены.")

# --- 2. Определение тестовых файлов ---
test_files = []

# WB: 2 файла из e2e-wb-v1
wb_dir = ROOT / "e2e-wb-v1"
for f in sorted(wb_dir.glob("jarvis-eye-wb-*.json")):
    test_files.append(("WB", f))
log.info("WB: %d файлов из %s", len([x for x in test_files if x[0]=="WB"]), wb_dir.name)

# Ozon: лучший файл из test-intake-e2e-ozon
ozon_dir = ROOT / "test-intake-e2e-ozon"
for f in sorted(ozon_dir.glob("jarvis-eye-ozon-*.json")):
    test_files.append(("OZON", f))
log.info("OZON: %d файлов из %s", len([x for x in test_files if x[0]=="OZON"]), ozon_dir.name)

# Avito: валидный тестовый файл
avito_dir = ROOT / "e2e-avito-v1"
for f in sorted(avito_dir.glob("jarvis-eye-avito-*.json")):
    test_files.append(("AVITO", f))
log.info("AVITO: %d файлов из %s", len([x for x in test_files if x[0]=="AVITO"]), avito_dir.name)

log.info("Всего файлов для приёма: %d", len(test_files))
for src, f in test_files:
    log.info("  [%s] %s (%d байт)", src, f.name, f.stat().st_size)

if not test_files:
    log.error("Нет тестовых JSON!")
    sys.exit(1)

# --- 3. Stage + Accept ---
results = []
for src, jf in test_files:
    data = jf.read_bytes()
    log.info("\n--- [%s] Stage: %s ---", src, jf.name)

    # Pre-check: если observation.status == FAILED, это деградация сборщика
    obs_status = ""
    try:
        raw_doc = json.loads(data.decode("utf-8"))
        obs_status = raw_doc.get("observation", {}).get("status", "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    t0 = time.monotonic()
    item, created = app.inbox.stage_bytes(data, filename=jf.name, origin="FOLDER")
    elapsed = time.monotonic() - t0
    log.info("  status=%s created=%s elapsed=%.2fs", item["status"], created, elapsed)
    log.info("  error_code=%s", item.get("error_code"))

    # FAILED-наблюдение: валидный вывод сборщика, но без данных
    if item["status"] == "INVALID" and obs_status == "FAILED":
        log.info("  -> EXPECTED_DEGRADED (collector status=FAILED, err=%s)", item.get("error_code"))
        results.append({
            "source": src, "file": jf.name,
            "stage": "EXPECTED_DEGRADED",
            "error_code": item.get("error_code"),
            "collector_status": "FAILED",
        })
        continue

    if item["status"] == "PENDING":
        log.info("  -> Accept item_id=%s", item["id"])
        t1 = time.monotonic()
        accepted = app.inbox.accept(item["id"])
        elapsed2 = time.monotonic() - t1
        log.info("  accepted: product_id=%s obs_created=%s events=%s elapsed=%.2fs",
                 accepted.get("product_id"), accepted.get("observation_created"),
                 accepted.get("events"), elapsed2)
        results.append({
            "source": src,
            "file": jf.name,
            "stage": "PENDING->ACCEPTED",
            "product_id": accepted.get("product_id"),
            "observation_created": accepted.get("observation_created"),
            "events": accepted.get("events"),
        })
    elif item["status"] == "ACCEPTED":
        log.info("  -> auto-accepted")
        results.append({
            "source": src,
            "file": jf.name,
            "stage": "AUTO_ACCEPTED",
            "product_id": item.get("product_id"),
            "observation_created": item.get("observation_created"),
        })
    elif item["status"] == "INVALID":
        log.warning("  -> INVALID: %s", item.get("error_code"))
        results.append({
            "source": src,
            "file": jf.name,
            "stage": "INVALID",
            "error_code": item.get("error_code"),
        })
    else:
        log.warning("  -> unexpected status: %s", item["status"])
        results.append({"source": src, "file": jf.name, "stage": item["status"]})

# --- 4. Проверка таблиц SQLite ---
log.info("\n=== Проверка SQLite ===")
with app.db.read() as conn:
    products = [dict(r) for r in conn.execute(
        "SELECT id, source, listing_id, title, brand, model, url, origin, created_at, updated_at "
        "FROM product ORDER BY id")]
    observations = [dict(r) for r in conn.execute(
        "SELECT id, product_id, observed_at, origin, evidence_level, availability, fingerprint "
        "FROM observation ORDER BY id")]
    price_points = [dict(r) for r in conn.execute(
        "SELECT id, observation_id, product_id, role, amount_minor, currency, label, observed_at "
        "FROM price_point ORDER BY id")]
    inbox_items = [dict(r) for r in conn.execute(
        "SELECT id, filename, origin, status, error_code, received_at, decided_at, observation_id "
        "FROM inbox_item ORDER BY id")]
    product_summary = [dict(r) for r in conn.execute(
        "SELECT id, source, listing_id, title, last_regular_minor, last_lowest_minor, "
        "last_availability, observation_count FROM product ORDER BY id")]

log.info("Products: %d", len(products))
for p in products:
    log.info("  #%d %s/%s | %s | brand=%s | origin=%s",
             p["id"], p["source"], p["listing_id"],
             (p["title"] or "")[:60], p["brand"], p["origin"])

log.info("Observations: %d", len(observations))
for o in observations:
    log.info("  #%d product=%d | %s | %s | avail=%s | fp=%s...",
             o["id"], o["product_id"], o["origin"], o["evidence_level"],
             o["availability"], (o["fingerprint"] or "")[:16])

log.info("Price points: %d", len(price_points))
for pp in price_points:
    log.info("  #%d obs=%d product=%d | %s %d %s | label=%s | %s",
             pp["id"], pp["observation_id"], pp["product_id"],
             pp["role"], pp["amount_minor"], pp["currency"], pp["label"], pp["observed_at"])

log.info("Product summary:")
for ps in product_summary:
    log.info("  #%d %s/%s | reg=%s low=%s | avail=%s | obs_count=%d",
             ps["id"], ps["source"], ps["listing_id"],
             ps["last_regular_minor"], ps["last_lowest_minor"],
             ps["last_availability"], ps["observation_count"])

log.info("Inbox items: %d", len(inbox_items))
for ii in inbox_items:
    log.info("  #%d %s | %s | %s | obs_id=%s",
             ii["id"], ii["filename"], ii["status"], ii["origin"], ii["observation_id"])

# --- 5. Итог по площадкам ---
log.info("\n=== ИТОГ E2E по площадкам ===")
for src in ("WB", "OZON", "AVITO"):
    src_results = [r for r in results if r["source"] == src]
    accepted = sum(1 for r in src_results if r["stage"] in ("PENDING->ACCEPTED", "AUTO_ACCEPTED"))
    invalid = sum(1 for r in src_results if r["stage"] == "INVALID")
    log.info("  %s: всего=%d принято=%d invalid=%d", src, len(src_results), accepted, invalid)

ok = all(r["stage"] in ("PENDING->ACCEPTED", "AUTO_ACCEPTED", "EXPECTED_DEGRADED") for r in results)
log.info("\nФайлов обработано: %d", len(results))
log.info("Успешно принято: %d", sum(1 for r in results if r["stage"] in ("PENDING->ACCEPTED", "AUTO_ACCEPTED")))
log.info("Expected degraded: %d", sum(1 for r in results if r["stage"] == "EXPECTED_DEGRADED"))
log.info("Invalid: %d", sum(1 for r in results if r["stage"] == "INVALID"))
log.info("Products в БД: %d", len(products))
log.info("Observations в БД: %d", len(observations))
log.info("Price points в БД: %d", len(price_points))
log.info("E2E STATUS: %s", "PASS" if ok and len(observations) > 0 else "PARTIAL/FAIL")

out = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "fresh_home": str(FRESH_HOME),
    "files_processed": len(results),
    "results": results,
    "products": products,
    "product_summary": product_summary,
    "observations": observations,
    "price_points": price_points,
    "inbox_items": inbox_items,
    "status": "PASS" if ok and len(observations) > 0 else "PARTIAL/FAIL",
    "summary": {
        "accepted": sum(1 for r in results if r["stage"] in ("PENDING->ACCEPTED", "AUTO_ACCEPTED")),
        "expected_degraded": sum(1 for r in results if r["stage"] == "EXPECTED_DEGRADED"),
        "invalid": sum(1 for r in results if r["stage"] == "INVALID"),
        "products_in_db": len(products),
        "observations_in_db": len(observations),
        "price_points_in_db": len(price_points),
    },
}
out_file = ROOT / "e2e-intake-wb-oz-av-result.json"
out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Результат: %s", out_file)

app.stop()
log.info("=== Done ===")
