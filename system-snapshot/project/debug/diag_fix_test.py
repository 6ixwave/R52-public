"""Быстрая проверка: FARPOST цена + WB/AVITO статус после фикса."""
import sys, time, re, logging
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.fix")

HOME = ROOT / "data" / "test_home"
results = {}

# === 1. FARPOST: search + price check ===
log.info("=== FARPOST test ===")
t0 = time.monotonic()
try:
    from r52.collectors.runner import _get_collector_class
    fp = _get_collector_class("FARPOST")(data_dir=HOME, use_proxies=False)
    rows = fp.collect_by_search("тест", page=1, page_size=5)
    prices_found = 0
    for r in rows:
        obs = r.get("observation", {})
        pc = obs.get("price_candidates", [])
        if pc and pc[0].get("amount_minor", 0) > 0:
            prices_found += 1
            log.info("  FARPOST item %s: price=%s %s (%d kop)",
                     obs.get("listing_id"), pc[0].get("amount"), pc[0].get("currency"), pc[0].get("amount_minor"))
    results["FARPOST"] = {
        "status": "PASS" if prices_found > 0 else "FAIL",
        "rows": len(rows),
        "prices_found": prices_found,
        "elapsed": round(time.monotonic() - t0, 1),
    }
    fp.close()
except Exception as e:
    results["FARPOST"] = {"status": "FAIL", "error": str(e)[:200], "elapsed": round(time.monotonic() - t0, 1)}
    log.error("FARPOST FAIL: %s", e)

# === 2. WB: quick status ===
log.info("=== WB test ===")
t0 = time.monotonic()
try:
    wb = _get_collector_class("WB")(data_dir=HOME, use_proxies=False)
    rows = wb.collect_by_search("тест", page=1, page_size=3)
    prices_found = sum(1 for r in rows if r.get("observation", {}).get("price_candidates"))
    results["WB"] = {"status": "PASS", "rows": len(rows), "prices": prices_found,
                     "elapsed": round(time.monotonic() - t0, 1)}
    wb.close()
except Exception as e:
    results["WB"] = {"status": "FAIL", "error": str(e)[:200], "elapsed": round(time.monotonic() - t0, 1)}
    log.error("WB FAIL: %s", e)

# === 3. AVITO: quick status ===
log.info("=== AVITO test ===")
t0 = time.monotonic()
try:
    av = _get_collector_class("AVITO")(data_dir=HOME, use_proxies=False)
    rows = av.collect_by_search("тест", page=1, page_size=3)
    prices_found = sum(1 for r in rows if r.get("observation", {}).get("price_candidates"))
    results["AVITO"] = {"status": "PASS", "rows": len(rows), "prices": prices_found,
                        "elapsed": round(time.monotonic() - t0, 1)}
    av.close()
except Exception as e:
    results["AVITO"] = {"status": "FAIL", "error": str(e)[:200], "elapsed": round(time.monotonic() - t0, 1)}
    log.error("AVITO FAIL: %s", e)

# === Summary ===
log.info("=== SUMMARY ===")
for src, res in results.items():
    status = res.get("status")
    extra = f" rows={res.get('rows','?')} prices={res.get('prices_found', res.get('prices','?'))}" if status == "PASS" else f" err={res.get('error','?')[:80]}"
    log.info("  %-15s %s%s (%.1fs)", src, status, extra, res.get("elapsed", 0))

import json
report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
out = ROOT / "debug" / "diag_fix_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Report: %s", out)
sys.exit(0 if all(r.get("status") == "PASS" for r in results.values()) else 1)
