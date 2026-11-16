"""Диагностика всех 6 сборщиков: по одному, с полным логом."""
import logging, sys, time, traceback

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("diag_collectors.log", mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("diag")

sys.path.insert(0, ".")

def run_one(name, collector, lid, url_override=None):
    url = url_override or collector.build_url(lid)
    log.info("=" * 60)
    log.info("TEST: %s | ID=%s | URL=%s", name, lid, url)
    log.info("=" * 60)
    t0 = time.monotonic()
    try:
        result = collector.collect_product(lid, url)
        dt = time.monotonic() - t0
        if result:
            obs = result.get("observation", {})
            fields = obs.get("fields", {})
            title = fields.get("title", {}).get("value", "N/A")
            prices = obs.get("price_candidates", [])
            media = obs.get("media", {}).get("items", [])
            log.info("RESULT: %s | %.1fs", obs.get("status", "?"), dt)
            log.info("  Title: %s", str(title)[:100])
            log.info("  Prices: %d -> %s", len(prices), prices[:2])
            log.info("  Media: %d items", len(media))
            log.info("  Avail: %s", obs.get("availability"))
            log.info("  Evidence: %s", obs.get("evidence_level"))
        else:
            log.info("RESULT: None | %.1fs", dt)
    except Exception as e:
        dt = time.monotonic() - t0
        log.error("EXCEPTION (%.1fs): %s", dt, e)
        traceback.print_exc()
    finally:
        try:
            collector.close()
        except Exception:
            pass
    log.info("")


# ─── 1. WB (рабочий, контрольный) ───
from r52.collectors.wb import WBCollector
run_one("WB", WBCollector(use_proxies=False), "199298871")

# ─── 2. OZON ───
from r52.collectors.ozon import OzonCollector
run_one("OZON", OzonCollector(use_proxies=False), "1600562267")

# ─── 3. FARPOST (полный URL из реального листинга) ───
from r52.collectors.farpost import FarpostCollector
run_one(
    "FARPOST",
    FarpostCollector(use_proxies=False),
    "116361427",
    url_override="https://www.farpost.ru/vladivostok/tech/communication/cellphones/iphone-17-256gb-trade-in-kredit-garantija-1-god-116361427.html",
)

# ─── 4. AVITO ───
from r52.collectors.avito import AvitoCollector
run_one("AVITO", AvitoCollector(use_proxies=False), "1096783029")

# ─── 5. ALIEXPRESS (com) ───
from r52.collectors.aliexpress import AliExpressCollector
run_one("ALIEXPRESS", AliExpressCollector(use_proxies=False, domain="com"), "1005006564853571")

# ─── 6. ALIEXPRESS_RU ───
run_one("ALIEXPRESS_RU", AliExpressCollector(use_proxies=False, domain="ru"), "1005006564853571")

log.info("ALL DONE. Log: diag_collectors.log")
