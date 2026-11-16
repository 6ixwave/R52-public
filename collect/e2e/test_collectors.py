"""Тест всех 4 сборщиков на живых ID. Запуск: python test_collectors.py"""
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("test_collectors.log", encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("test")

OUT_DIR = Path(__file__).parent / "test_output"
OUT_DIR.mkdir(exist_ok=True)

# Живые ID из поиска 12.11.2026
TESTS = [
    ("WB", "211321120"),       # Толстовка ТЕЛОДВИЖЕНИЯ
    ("OZON", "1161549185"),    # ПДД 2026 с комментариями
    ("AVITO", "21964448"),     # ASUS TUF Gaming FX505DT (Владивосток)
    ("FARPOST", "1234567890"), # Farpost (ID из прошлых тестов)
]


def run_one(source: str, listing_id: str) -> dict:
    """Запускает один сборщик и возвращает результат."""
    log.info("=" * 60)
    log.info("TEST: %s / %s", source, listing_id)
    t0 = time.monotonic()
    collector = None

    try:
        from r52.collectors import COLLECTOR_REGISTRY
        cls, display = COLLECTOR_REGISTRY[source]
        collector = cls(data_dir=None, use_proxies=False)
        url = collector.build_url(listing_id)
        log.info("URL: %s", url)

        result = collector.collect_product(listing_id, url)
        elapsed = time.monotonic() - t0

        if result:
            obs = result.get("observation", {})
            fields = obs.get("fields", {})
            title = fields.get("title", {}).get("value", "")
            prices = obs.get("price_candidates", [])
            media = obs.get("media", {}).get("items", [])
            status = obs.get("status", "?")

            log.info("RESULT: %s | %.1fs | title=%s | prices=%d | media=%d | status=%s",
                     display, elapsed, str(title)[:50], len(prices), len(media), status)

            out_file = OUT_DIR / f"{source.lower()}_{listing_id}.json"
            out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Saved: %s", out_file)

            for p in prices[:5]:
                log.info("  price: %s = %s %s (%s)", p.get("role"), p.get("amount"), p.get("currency"), p.get("label"))
            for m in media[:5]:
                log.info("  media: %s %s", m.get("type"), str(m.get("source_url", ""))[:80])

            return {"source": source, "id": listing_id, "ok": True, "status": status,
                    "title": str(title)[:60], "prices": len(prices), "media": len(media),
                    "elapsed": round(elapsed, 1)}
        else:
            log.warning("RESULT: None (parse returned nothing) | %.1fs", elapsed)
            return {"source": source, "id": listing_id, "ok": False, "status": "NONE",
                    "title": "", "prices": 0, "media": 0, "elapsed": round(elapsed, 1)}

    except Exception as e:
        elapsed = time.monotonic() - t0
        log.exception("ERROR: %s", e)
        return {"source": source, "id": listing_id, "ok": False, "status": f"ERROR: {e}",
                "title": "", "prices": 0, "media": 0, "elapsed": round(elapsed, 1)}
    finally:
        try:
            if collector and hasattr(collector, "_pw") and collector._pw:
                collector._pw.stop()
        except Exception:
            pass
        try:
            if collector and hasattr(collector, "session"):
                collector.session.close()
        except Exception:
            pass


def main():
    log.info("START test_collectors at %s", time.strftime("%H:%M:%S"))
    results = []
    for source, lid in TESTS:
        r = run_one(source, lid)
        results.append(r)

    log.info("=" * 60)
    log.info("SUMMARY:")
    for r in results:
        mark = "OK" if r["ok"] else "FAIL"
        log.info("  [%s] %s/%s: %s | title=%s | P=%d M=%d | %.1fs",
                 mark, r["source"], r["id"], r["status"], r["title"], r["prices"], r["media"], r["elapsed"])

    summary_file = OUT_DIR / "summary.json"
    summary_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Summary: %s", summary_file)

    ok_count = sum(1 for r in results if r["ok"])
    log.info("DONE: %d/%d OK", ok_count, len(results))


if __name__ == "__main__":
    main()
