"""E2E Speed5: параллельный тест 4 площадок (фиксы WB/Ozon/Avito).

WB:      100000001 (Белизна) — card.json + search.wb.ru
OZON:    2795954097 (iPhone 15) — curl_cffi composer-api
AVITO:   482962662 (iPhone 15 Moscow) — Festima-подтверждённый ID
FARPOST: 139122082 (Шины LingLong) — уже работает
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e5")

IDS = {
    "WB": "100000001",
    "OZON": "2795954097",
    "AVITO": "482962662",
    "FARPOST": "139122082",
}


def collect_one(source: str, lid: str) -> dict:
    from r52.collectors.runner import _get_collector_class
    start = time.monotonic()
    try:
        cls = _get_collector_class(source)
        collector = cls(use_proxies=False)
        url = collector.build_url(lid)
        log.info("%s URL: %s", source, url)
        result = collector.collect_product(lid, url)
        elapsed = time.monotonic() - start

        if result:
            obs = result.get("observation", {})
            title = obs.get("fields", {}).get("title", {}).get("value", "")
            prices = len(obs.get("price_candidates", []))
            media = len(obs.get("media", {}).get("items", []))
            status = obs.get("status", "?")
            verdict = "DATA_OK" if (title and prices > 0) else ("DATA_WEAK" if title else "FAIL")
            if hasattr(collector, "close"):
                collector.close()
            return {"verdict": verdict, "title": title[:60], "prices": prices,
                    "media": media, "status": status, "elapsed": round(elapsed, 1)}
        else:
            return {"verdict": "FAIL", "title": "", "prices": 0, "media": 0,
                    "status": "NONE", "elapsed": round(elapsed, 1)}
    except Exception as e:
        elapsed = time.monotonic() - start
        log.exception("%s error", source)
        return {"verdict": "ERROR", "title": "", "prices": 0, "media": 0,
                "status": str(e)[:80], "elapsed": round(elapsed, 1)}


def main():
    t0 = time.monotonic()
    log.info("=== E2E v5 PARALLEL START %s ===", datetime.now(timezone(timedelta(hours=10))).isoformat())
    log.info("IDs: %s", IDS)

    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(collect_one, src, lid): src for src, lid in IDS.items()}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                results[src] = fut.result()
            except Exception as e:
                results[src] = {"verdict": "ERROR", "title": "", "prices": 0, "media": 0,
                                "status": str(e)[:80], "elapsed": 0}

    total = time.monotonic() - t0
    log.info("=== E2E v5 DONE in %.1fs ===", total)
    log.info("SUMMARY:")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    ok = sum(1 for r in results.values() if r["verdict"] == "DATA_OK")
    log.info("=== VERDICT: %d/4 ===", ok)
    print(f"E2E_V5_DONE ok={ok}/4 total={total:.0f}s")


if __name__ == "__main__":
    main()
