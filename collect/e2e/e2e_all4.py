"""E2E-тест: 4 маркетплейса → collect_product → normalize_eye → JSON.

Быстрый: без Playwright, без прокси (direct), 1 товар на площадку.
Запуск:  python e2e_all4.py [wb_id] [ozon_id] [avito_id] [farpost_id]
Результат: e2e_all4_result.json + stdout (редирект в .txt)
"""
import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Путь к проекту ---
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# --- Лог в файл + stdout ---
LOG_FILE = ROOT / "e2e_all4.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("e2e_all4")

# --- Тестовые ID (можно переопределить через argv) ---
DEFAULT_IDS = {
    "WB": "100000001",
    "OZON": "1234567890",
    "AVITO": "1234567890",
    "FARPOST": "139122082",
}

def get_ids():
    ids = dict(DEFAULT_IDS)
    args = sys.argv[1:]
    for i, key in enumerate(["WB", "OZON", "AVITO", "FARPOST"]):
        if i < len(args):
            ids[key] = args[i]
    return ids

# --- Минимальный mock для Inbox (если понадобится) ---
class MockSettings:
    def get(self, key, default=None):
        return default

class MockApp:
    def __init__(self, db_path: Path):
        from r52.db.database import Database
        from r52.timeutil import Clock
        self.db = Database(db_path)
        self.clock = Clock()
        self.settings = MockSettings()
        self.notifier = None
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db.initialize()

def test_one(source: str, lid: str) -> dict:
    """Один прогон: collect_product → normalize_eye. Возвращает summary dict."""
    t0 = time.monotonic()
    result = {
        "source": source,
        "listing_id": lid,
        "ok": False,
        "status": "ERROR",
        "title": None,
        "prices": [],
        "media_count": 0,
        "elapsed_sec": 0,
        "error": None,
    }

    # Импорт коллектора
    mapping = {
        "WB": ("r52.collectors.wb", "WBCollector"),
        "OZON": ("r52.collectors.ozon", "OzonCollector"),
        "AVITO": ("r52.collectors.avito", "AvitoCollector"),
        "FARPOST": ("r52.collectors.farpost", "FarpostCollector"),
    }
    mod_path, cls_name = mapping[source]
    import importlib
    mod = importlib.import_module(mod_path)
    cls = getattr(mod, cls_name)

    # use_proxies=False → direct, быстрее для теста
    collector = cls(data_dir=None, use_proxies=False)
    url = collector.build_url(lid)
    log.info("[%s] URL: %s", source, url)

    try:
        obs_full = collector.collect_product(lid, url)
        if obs_full is None:
            result["status"] = "COLLECT_NONE"
            result["error"] = "collect_product returned None"
            result["elapsed_sec"] = round(time.monotonic() - t0, 1)
            return result

        obs = obs_full.get("observation", {})
        result["status"] = obs.get("status", "UNKNOWN")
        result["title"] = obs.get("fields", {}).get("title", {}).get("value")

        # Цены
        prices = obs.get("price_candidates", [])
        result["prices"] = [
            {"role": p.get("role"), "amount_minor": p.get("amount_minor"),
             "currency": p.get("currency"), "label": p.get("label")}
            for p in prices[:5]
        ]

        # Медиа
        media = obs.get("media", {}).get("items", [])
        result["media_count"] = len(media)

        # --- normalize_eye: валидация формата ---
        from r52.domain.normalize import normalize_eye
        normalized, cleaned = normalize_eye(obs)
        result["normalized_source"] = normalized.get("source")
        result["normalized_listing_id"] = normalized.get("listing_id")
        result["completeness"] = normalized.get("completeness")
        result["ok"] = True

        # Статистика коллектора
        result["collector_stats"] = collector.stats
        log.info("[%s] OK: title=%r prices=%d media=%d stats=%s",
                 source, result["title"], len(prices), len(media), collector.stats)

    except Exception as e:
        result["status"] = "EXCEPTION"
        result["error"] = f"{type(e).__name__}: {e}"
        log.exception("[%s] EXCEPTION: %s", source, e)
    finally:
        result["elapsed_sec"] = round(time.monotonic() - t0, 1)
        try:
            collector.close()
        except Exception:
            pass

    return result

def main():
    ids = get_ids()
    log.info("=== E2E ALL4 START ===")
    log.info("IDs: %s", ids)

    results = []
    for source in ["WB", "OZON", "AVITO", "FARPOST"]:
        lid = ids[source]
        log.info("--- Testing %s (id=%s) ---", source, lid)
        r = test_one(source, lid)
        results.append(r)
        # Короткая пауза между площадками (не rate-limit, а буфер)
        time.sleep(2)

    # --- Итог ---
    summary = {
        "timestamp": datetime.now(timezone(timedelta(hours=10))).isoformat(),
        "ids": ids,
        "results": results,
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "total_sec": round(sum(r["elapsed_sec"] for r in results), 1),
    }

    out_file = ROOT / "e2e_all4_result.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== E2E ALL4 DONE: %d/%d passed, %ds total ===",
             summary["passed"], 4, summary["total_sec"])
    log.info("Result: %s", out_file)

    # Краткий вывод в stdout (для .txt)
    print(f"\n{'='*60}")
    print(f"E2E ALL4: {summary['passed']}/4 passed in {summary['total_sec']}s")
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['source']:8s} id={r['listing_id']:15s} "
              f"status={r['status']:20s} title={str(r['title'] or '')[:40]:40s} "
              f"prices={len(r['prices'])} media={r['media_count']} {r['elapsed_sec']}s")
        if r["error"]:
            print(f"    ERR: {r['error'][:100]}")
    print(f"{'='*60}")
    print(f"JSON: {out_file}")

if __name__ == "__main__":
    main()
