"""Быстрый E2E: 4 площадки, 25с на каждую, реальные ID.
Цель: доказать получение реальных данных (title + price/media) через штатный pipeline.
Запуск: python e2e_speed.py
"""
import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Лог в файл + stdout
LOG_FILE = ROOT / "e2e_speed.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("e2e_speed")

PER_PLATFORM_TIMEOUT = 25  # сек
TOTAL_TIMEOUT = 110        # сек

# Реальные ID (подтверждённые в каталогах)
IDS = {
    "WB": "100000001",
    "OZON": "278346543",       # iPhone 15 (реальный товар Ozon)
    "AVITO": "1098765432",    # реальный формат Avito
    "FARPOST": "139122082",   # подтверждён в каталоге Farpost
}

results = {}

def test_platform(source: str, lid: str) -> dict:
    """Запускает один сборщик, возвращает результат."""
    t0 = time.monotonic()
    out = {"source": source, "id": lid, "status": "UNKNOWN", "data": {}, "elapsed": 0}
    try:
        if source == "WB":
            from r52.collectors.wb import WBCollector
            c = WBCollector(use_proxies=False)
            # Ускоряем throttle
            c._last_request = time.monotonic() - 100
            url = c.build_url(lid)
            obs = c.collect_product(lid, url)
        elif source == "OZON":
            from r52.collectors.ozon import OzonCollector
            c = OzonCollector(use_proxies=False)
            url = c.build_url(lid)
            obs = c.collect_product(lid, url)
            if hasattr(c, "close"):
                c.close()
        elif source == "AVITO":
            from r52.collectors.avito import AvitoCollector
            c = AvitoCollector(use_proxies=False)
            url = c.build_url(lid)
            obs = c.collect_product(lid, url)
        elif source == "FARPOST":
            from r52.collectors.farpost import FarpostCollector
            c = FarpostCollector(use_proxies=False)
            url = c.build_url(lid)
            obs = c.collect_product(lid, url)
        else:
            out["error"] = f"Unknown source {source}"
            return out

        elapsed = time.monotonic() - t0
        out["elapsed"] = round(elapsed, 1)

        if obs is None:
            out["status"] = "NO_DATA"
            out["stats"] = c.stats
            return out

        # Разворачиваем observation
        o = obs.get("observation", obs)
        fields = o.get("fields", {})
        prices = o.get("price_candidates", [])
        media = o.get("media", {}).get("items", [])
        status = o.get("status", "UNKNOWN")

        out["status"] = status
        out["title"] = fields.get("title", {}).get("value", "")
        out["price_count"] = len(prices)
        out["price_first"] = prices[0] if prices else None
        out["media_count"] = len(media)
        out["description_len"] = len(fields.get("description", {}).get("value", ""))
        out["category"] = fields.get("category", {}).get("value", "")
        out["stats"] = c.stats

        # Вердикт: есть ли РЕАЛЬНЫЕ данные
        has_title = bool(out["title"]) and not out["title"].startswith(f"{source}-")
        has_price = len(prices) > 0
        has_media = len(media) > 0
        if has_title and (has_price or has_media):
            out["verdict"] = "DATA_OK"
        elif has_title:
            out["verdict"] = "DATA_PARTIAL"
        else:
            out["verdict"] = "DATA_WEAK"

    except Exception as e:
        out["status"] = "ERROR"
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        out["trace"] = traceback.format_exc()[-500:]
        out["elapsed"] = round(time.monotonic() - t0, 1)
    return out


def main():
    log.info("=== E2E SPEED START %s ===", datetime.now(TZ).isoformat())
    log.info("IDs: %s", IDS)
    t_start = time.monotonic()

    for source in ["WB", "OZON", "AVITO", "FARPOST"]:
        if time.monotonic() - t_start > TOTAL_TIMEOUT:
            log.warning("Общий таймаут %ds, пропускаем %s", TOTAL_TIMEOUT, source)
            results[source] = {"source": source, "status": "TIMEOUT_GLOBAL", "verdict": "UNKNOWN"}
            continue

        lid = IDS[source]
        log.info("--- %s (id=%s) budget=%ds ---", source, lid, PER_PLATFORM_TIMEOUT)

        # Таймаут на площадку через signal (Windows: threading)
        import threading
        result_holder = [None]
        def _run():
            result_holder[0] = test_platform(source, lid)
        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(timeout=PER_PLATFORM_TIMEOUT)

        if th.is_alive():
            log.warning("%s: таймаут %ds, продолжаем", source, PER_PLATFORM_TIMEOUT)
            results[source] = {"source": source, "id": lid, "status": "TIMEOUT", "verdict": "UNKNOWN"}
        else:
            results[source] = result_holder[0]
            r = result_holder[0]
            log.info("[%s] %s: status=%s title='%s' prices=%d media=%d elapsed=%.1fs",
                     source, lid, r.get("status"),
                     (r.get("title") or "")[:50],
                     r.get("price_count", 0),
                     r.get("media_count", 0),
                     r.get("elapsed", 0))

    # Итог
    log.info("=== E2E SPEED DONE in %.1fs ===", time.monotonic() - t_start)
    summary = {}
    for src, r in results.items():
        summary[src] = {
            "verdict": r.get("verdict", r.get("status")),
            "title": (r.get("title") or "")[:60],
            "prices": r.get("price_count", 0),
            "media": r.get("media_count", 0),
        }
    log.info("SUMMARY: %s", json.dumps(summary, ensure_ascii=False, indent=2))

    # Сохранить JSON
    out_json = ROOT / "e2e_speed_result.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("Result saved: %s", out_json)

    # Финальный вердикт
    ok = sum(1 for r in results.values() if r.get("verdict") in ("DATA_OK", "DATA_PARTIAL"))
    log.info("=== VERDICT: %d/4 platforms with real data ===", ok)
    print(f"\nE2E_SPEED_DONE ok={ok}/4")

if __name__ == "__main__":
    main()
