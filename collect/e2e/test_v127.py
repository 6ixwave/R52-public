"""V127: Фокусированный тест WB (card.json + price) и Farpost (исправленный URL).

Запуск:
  cmd /c "C:\Python315\python.exe -u test_v127.py > test_v127_out.log 2>&1 & echo EXIT=%ERRORLEVEL%"
"""
import json
import logging
import sys
import time
from pathlib import Path

# Raw string для пути Python
PY = r"C:\Python315\python.exe"
PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("v127")

def main():
    t0 = time.monotonic()
    results = {}

    # ===================== WB =====================
    log.info("=" * 60)
    log.info("WILDBERRIES (card.json + price extraction)")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=PROJ, use_proxies=False)

    wb_ids = ["199298871", "259671175"]
    for nm in wb_ids:
        log.info("\n--- WB %s ---", nm)
        t1 = time.monotonic()
        url = wb.build_url(nm)
        obs = wb.collect_product(nm, url, title_hint="")
        dt = time.monotonic() - t1

        if obs is None:
            log.warning("  RESULT=None (%.1fs)", dt)
            results[f"WB-{nm}"] = "NONE"
            continue

        o = obs["observation"]
        status = o.get("status", "?")
        title = o.get("fields", {}).get("title", {}).get("value", "?")
        prices = o.get("price_candidates", [])
        media = o.get("media", {}).get("items", [])
        avail = o.get("availability", "?")

        log.info("  [%.1fs] status=%s title=%s", dt, status, title[:60])
        log.info("  prices=%d media=%d avail=%s", len(prices), len(media), avail)
        for p in prices[:3]:
            log.info("    P: %s %d RUB (%s)", p.get("role"), p.get("amount_minor", 0), p.get("label"))
        for m in media[:3]:
            log.info("    M: %s %s", m.get("type"), m.get("source_url", "")[:80])

        # Сохранить JSON
        out = PROJ / f"v127_wb_{nm}.json"
        out.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("  saved -> %s", out.name)
        results[f"WB-{nm}"] = status

    log.info("\nWB stats: %s", wb.stats)

    # ===================== FARPOST =====================
    log.info("\n" + "=" * 60)
    log.info("FARPOST (fixed URL: /vladivostok/{id}.html)")
    log.info("=" * 60)

    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(data_dir=PROJ, use_proxies=False)

    fp_ids = ["130693618", "136342005", "98836529"]
    for bid in fp_ids:
        log.info("\n--- FP %s ---", bid)
        t1 = time.monotonic()
        url = fp.build_url(bid)
        log.info("  URL: %s", url)
        obs = fp.collect_product(bid, url, title_hint="")
        dt = time.monotonic() - t1

        if obs is None:
            log.warning("  RESULT=None (%.1fs)", dt)
            results[f"FP-{bid}"] = "NONE"
            continue

        o = obs["observation"]
        status = o.get("status", "?")
        title = o.get("fields", {}).get("title", {}).get("value", "?")
        prices = o.get("price_candidates", [])
        media = o.get("media", {}).get("items", [])
        avail = o.get("availability", "?")
        specs = o.get("fields", {}).get("specs", {}).get("value", {})
        seller = o.get("fields", {}).get("seller", {}).get("value", "?")

        log.info("  [%.1fs] status=%s title=%s", dt, status, title[:60])
        log.info("  prices=%d media=%d avail=%s seller=%s", len(prices), len(media), avail, seller)
        for p in prices[:3]:
            log.info("    P: %s %d RUB (%s)", p.get("role"), p.get("amount_minor", 0), p.get("label"))
        for m in media[:3]:
            log.info("    M: %s %s", m.get("type"), m.get("source_url", "")[:80])
        if isinstance(specs, dict):
            log.info("  specs: %d pairs", len(specs))
            for k, v in list(specs.items())[:4]:
                log.info("    S: %s = %s", v.get("label", k), v.get("value", "?")[:40])

        out = PROJ / f"v127_fp_{bid}.json"
        out.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("  saved -> %s", out.name)
        results[f"FP-{bid}"] = status

    log.info("\nFP stats: %s", fp.stats)

    # ===================== SUMMARY =====================
    log.info("\n" + "=" * 60)
    log.info("SUMMARY (V127)")
    log.info("=" * 60)
    for k, v in results.items():
        log.info("  %s: %s", k, v)
    log.info("\nV127 DONE (%.0fs)", time.monotonic() - t0)

if __name__ == "__main__":
    main()
