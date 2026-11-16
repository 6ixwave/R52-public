r"""e2e_diag.py — Быстрая диагностика: что реально возвращает каждый коллектор.
Run: C:\Python315\python.exe e2e_diag.py
"""
import sys, os, time, json, logging, traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("DIAG")

TIMEOUT_PER = 60  # сек на один коллектор

# Живые объявления (проверены 12.11.2026)
TESTS = [
    ("OZON",    "5443610976", "RTX 5060 Ti системник"),
    ("WB",      "1097975275", "iPhone 17 Pro Max"),
    ("FARPOST", "138859452",  "Dvigatel Nissan CGA3"),
    ("AVITO",   "1045231001", "Avito test"),
]

COLLECTOR_MAP = {
    "OZON":    ("r52.collectors.ozon",    "OzonCollector"),
    "WB":      ("r52.collectors.wb",      "WBCollector"),
    "FARPOST": ("r52.collectors.farpost",  "FarpostCollector"),
    "AVITO":   ("r52.collectors.avito",    "AvitoCollector"),
}


def run_one(name, lid, hint):
    """Запускает один коллектор, возвращает краткий отчёт."""
    import importlib
    mod_name, cls_name = COLLECTOR_MAP[name]
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    col = cls(data_dir=None, use_proxies=False)
    url = col.build_url(lid)
    t0 = time.time()
    try:
        result = col.collect_product(lid, url, hint)
        dt = time.time() - t0
        if result is None:
            return {"name": name, "lid": lid, "url": url, "dt": dt,
                    "ok": False, "reason": "collector returned None",
                    "stats": dict(col.stats)}
        obs = result.get("observation", {})
        fields = obs.get("fields", {})
        prices = obs.get("price_candidates", [])
        media = obs.get("media", {}).get("items", [])
        title = fields.get("title", {}).get("value", "?")
        report = {
            "name": name, "lid": lid, "url": url, "dt": round(dt, 1),
            "ok": True,
            "status": obs.get("status", "?"),
            "evidence": obs.get("evidence_level", "?"),
            "title": title[:60],
            "n_fields": len(fields),
            "field_keys": list(fields.keys()),
            "n_prices": len(prices),
            "prices": [{"role": p.get("role"), "minor": p.get("amount_minor"),
                        "cur": p.get("currency"), "label": p.get("label", "")[:30]}
                       for p in prices[:5]],
            "n_media": len(media),
            "availability": obs.get("availability", "?"),
            "stats": dict(col.stats),
        }
        return report
    except Exception as e:
        dt = time.time() - t0
        return {"name": name, "lid": lid, "url": url, "dt": round(dt, 1),
                "ok": False, "reason": f"EXCEPTION: {e}",
                "trace": traceback.format_exc()[-500:],
                "stats": dict(col.stats)}
    finally:
        try:
            col.close()
        except Exception:
            pass


def main():
    log.info("=" * 60)
    log.info("DIAGNOSTIC  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    results = []
    for name, lid, hint in TESTS:
        log.info("--- %s (%s) ---", name, lid)
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(run_one, name, lid, hint)
            try:
                r = fut.result(timeout=TIMEOUT_PER)
            except FuturesTimeout:
                r = {"name": name, "lid": lid, "dt": TIMEOUT_PER,
                     "ok": False, "reason": "TIMEOUT"}
            except Exception as e:
                r = {"name": name, "lid": lid, "ok": False, "reason": str(e)}
        results.append(r)

        log.info("  dt=%.1fs  ok=%s  status=%s  title='%s'",
                 r.get("dt", 0), r.get("ok"), r.get("status", "?"), r.get("title", "?")[:40])
        if r.get("ok"):
            log.info("  fields=%d  prices=%d  media=%d  avail=%s",
                     r.get("n_fields", 0), r.get("n_prices", 0),
                     r.get("n_media", 0), r.get("availability", "?"))
            for p in r.get("prices", []):
                log.info("    %-8s %10d %s  '%s'",
                         p.get("role", "?"), p.get("minor", 0),
                         p.get("cur", "?"), p.get("label", ""))
        else:
            log.info("  reason: %s", r.get("reason", "?")[:120])
        log.info("  stats: %s", r.get("stats", {}))

    # Summary
    log.info("=" * 60)
    log.info("SUMMARY:")
    for r in results:
        mark = "✓" if r.get("ok") and r.get("n_prices", 0) > 0 else "△" if r.get("ok") else "✗"
        log.info("  %s %-10s dt=%5.1fs  prices=%d  status=%s",
                 mark, r["name"], r.get("dt", 0),
                 r.get("n_prices", 0), r.get("status", r.get("reason", "?")[:20]))

    with open(ROOT / "e2e_diag_result.json", "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results},
                  f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved e2e_diag_result.json")


if __name__ == "__main__":
    main()
