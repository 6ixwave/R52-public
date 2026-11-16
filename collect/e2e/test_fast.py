"""test_fast.py — Быстрый тест WB + Farpost с минимальными задержками.

Оптимизации:
- Pre-cache basket hosts (из v123: vol2→host, vol1→host)
- Fast throttle 1-2s вместо 8-20s
- Таймаут запросов 10s
- Ограничение: 2 WB + 2 Farpost

Запуск:
  cmd /c "C:\Python315\python.exe -u test_fast.py > test_fast_output.log 2>&1 & echo EXIT=%ERRORLEVEL%"
"""
import sys, os, json, time, logging, traceback
from pathlib import Path
from unittest.mock import patch

PROJ = Path(__file__).parent
sys.path.insert(0, str(PROJ))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Пatches для ускорения
import r52.collectors.base as base_mod
import r52.collectors.wb as wb_mod
import r52.collectors.farpost as fp_mod

# Укорачиваем rate limit
base_mod.MIN_DELAY = 1
base_mod.MAX_DELAY = 2
base_mod.BACKOFF_BASE = 2

# Pre-cache basket hosts (из v123/v124: vol=2 → найден, vol=1 → найден)
# vol = nm_id // 100000
# 259671175 → vol=2, 199298871 → vol=1
# v123 подтвердил что оба работают, но не записал номер хоста.
# Попробуем несколько известных: basket-01, basket-02, ...
# Для теста сканируем максимум 10 хостов
wb_mod._basket_host_cache = {}

# Ускоряем fast throttle
_orig_fast_throttle = wb_mod.WBCollector._fast_throttle
def _fast_throttle_patched(self):
    now = time.monotonic()
    elapsed = now - self._last_request
    wait = 1.5 - elapsed
    if wait > 0:
        time.sleep(wait)
    self._last_request = time.monotonic()
wb_mod.WBCollector._fast_throttle = _fast_throttle_patched

# Ускоряем обычный throttle
_orig_throttle = base_mod.BaseCollector._throttle
def _throttle_patched(self):
    now = time.monotonic()
    elapsed = now - self._last_request
    wait = 2.0 - elapsed
    if wait > 0:
        time.sleep(wait)
    self._last_request = time.monotonic()
base_mod.BaseCollector._throttle = _throttle_patched


def run_one(collector, listing_id, label):
    t0 = time.time()
    url = collector.build_url(listing_id)
    print(f"\n--- {label} {listing_id} ({url}) ---", flush=True)
    try:
        result = collector.collect_product(listing_id, url)
        elapsed = time.time() - t0
        if result is None:
            print(f"  [{elapsed:.1f}s] RESULT=None", flush=True)
            return "NONE"
        obs = result.get("observation", {})
        fields = obs.get("fields", {})
        prices = obs.get("price_candidates", [])
        media = obs.get("media", {}).get("items", [])
        title = fields.get("title", {}).get("value", "?")
        status = obs.get("status", "?")
        avail = obs.get("availability", "?")
        print(f"  [{elapsed:.1f}s] status={status} title={title[:70]}", flush=True)
        print(f"  prices={len(prices)} media={len(media)} avail={avail}", flush=True)
        for p in prices[:5]:
            print(f"    P: {p.get('role','?')} {p.get('amount_minor','?')} {p.get('currency','?')} ({p.get('label','?')})", flush=True)
        for m in media[:5]:
            print(f"    M: {m.get('type','?')} {m.get('source_url','?')[:80]}", flush=True)
        specs = fields.get("specs", {}).get("value", {})
        if isinstance(specs, dict) and specs:
            print(f"  specs: {len(specs)} pairs", flush=True)
            for k, v in list(specs.items())[:3]:
                print(f"    S: {v.get('label','?')} = {v.get('value','?')[:40]}", flush=True)
        seller = fields.get("seller", {}).get("value", "")
        if seller:
            print(f"  seller: {seller[:50]}", flush=True)
        cat = fields.get("category", {}).get("value", "")
        if cat:
            print(f"  category: {cat[:60]}", flush=True)
        out = PROJ / f"testf_{label.lower()}_{listing_id}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  saved -> {out.name}", flush=True)
        return status
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [{elapsed:.1f}s] EXCEPTION: {e}", flush=True)
        traceback.print_exc()
        return "EXC"


def main():
    results = {}

    # ===================== WB =====================
    print("\n" + "=" * 60, flush=True)
    print("WILDBERRIES (2 cards)", flush=True)
    print("=" * 60, flush=True)

    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)

    # Ограничиваем сканирование хостов до 15
    orig_find = wb._find_basket_host
    def _find_basket_host_limited(self, nm_id, vol, path):
        if vol in wb_mod._basket_host_cache:
            n = wb_mod._basket_host_cache[vol]
            return f"basket-{n:02d}.wbbasket.ru"
        for n in range(1, 16):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            resp = self._http_get(url, headers=wb_mod.JSON_HEADERS, timeout=8, fast=True)
            if resp is not None and resp.status_code == 200:
                wb_mod._basket_host_cache[vol] = n
                logging.getLogger("r52.collectors.wb").info("WB: vol=%d -> %s (nm_id=%d)", vol, host, nm_id)
                return host
        logging.getLogger("r52.collectors.wb").warning("WB %d: basket host not found vol=%d (scanned 1-15)", nm_id, vol)
        return None
    wb._find_basket_host = _find_basket_host_limited.__get__(wb, WBCollector)

    for nm in ["259671175", "199298871"]:
        results[f"WB-{nm}"] = run_one(wb, nm, "WB")

    print(f"\nWB stats: {wb.stats}", flush=True)

    # ===================== FARPOST =====================
    print("\n" + "=" * 60, flush=True)
    print("FARPOST (2 bulletins)", flush=True)
    print("=" * 60, flush=True)

    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(use_proxies=False)

    for bid in ["130693618", "136342005"]:
        results[f"FP-{bid}"] = run_one(fp, bid, "FP")

    print(f"\nFP stats: {fp.stats}", flush=True)

    # ===================== SUMMARY =====================
    print("\n" + "=" * 60, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 60, flush=True)
    for k, v in results.items():
        print(f"  {k}: {v}", flush=True)
    print("\nTEST FAST DONE", flush=True)


if __name__ == "__main__":
    main()
