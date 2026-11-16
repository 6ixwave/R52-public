"""Тест всех сборщиков: ALIEXPRESS_RU, ALIEXPRESS, WB, OZON, AVITO, FARPOST."""
import sys, os, json, logging, time, traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = {}

def test_collector(name, collector_cls, test_fn):
    print(f"\n{'='*60}")
    print(f"  TESTING: {name}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    try:
        c = collector_cls(use_proxies=False)
        result = test_fn(c)
        elapsed = time.monotonic() - t0
        c.close()
        RESULTS[name] = {"ok": True, "elapsed": round(elapsed, 1), "detail": result}
        print(f"  OK in {elapsed:.1f}s: {result}")
    except Exception as e:
        elapsed = time.monotonic() - t0
        RESULTS[name] = {"ok": False, "elapsed": round(elapsed, 1), "error": str(e)}
        print(f"  FAIL in {elapsed:.1f}s: {e}")
        traceback.print_exc()

# ─── 1. ALIEXPRESS_RU: search API ───
def test_ali_ru(c):
    from r52.collectors.aliexpress import AliExpressRUCollector
    results = c.collect_by_search("наушники беспроводные", page=1, page_size=10)
    if not results:
        return "0 products (API empty)"
    first = results[0]["observation"]
    title = first.get("fields", {}).get("title", {}).get("value", "?")[:60]
    prices = first.get("price_candidates", [])
    price_str = prices[0]["amount"] + " " + prices[0].get("currency", "?") if prices else "no price"
    media = len(first.get("media", {}).get("items", []))
    return f"{len(results)} products, first: id={first['id']}, price={price_str}, media={media}, title={title}"

# ─── 2. ALIEXPRESS (global) ───
def test_ali_com(c):
    from r52.collectors.aliexpress import AliExpressCollector
    url = c.build_url("32568056283")
    print(f"  URL: {url}")
    result = c.collect_product("32568056283", url)
    if result:
        obs = result["observation"]
        title = obs.get("fields", {}).get("title", {}).get("value", "?")[:60]
        prices = obs.get("price_candidates", [])
        price_str = prices[0]["amount"] + " " + prices[0].get("currency", "?") if prices else "no price"
        media = len(obs.get("media", {}).get("items", []))
        return f"id={obs['id']}, price={price_str}, media={media}, title={title}"
    return "None (page not parsed)"

# ─── 3. WB ───
def test_wb(c):
    url = "https://www.wildberries.ru/catalog/167854494/detail.aspx"
    result = c.collect_product("167854494", url, "Тест WB")
    if result:
        obs = result["observation"]
        title = obs.get("fields", {}).get("title", {}).get("value", "?")[:60]
        prices = obs.get("price_candidates", [])
        price_str = prices[0]["amount"] + " " + prices[0].get("currency", "?") if prices else "no price"
        return f"id={obs['id']}, price={price_str}, title={title}"
    return "None"

# ─── 4. OZON ───
def test_ozon(c):
    url = "https://www.ozon.ru/product/167854494"
    result = c.collect_product("167854494", url, "Тест OZON")
    if result:
        obs = result["observation"]
        title = obs.get("fields", {}).get("title", {}).get("value", "?")[:60]
        prices = obs.get("price_candidates", [])
        price_str = prices[0]["amount"] + " " + prices[0].get("currency", "?") if prices else "no price"
        return f"id={obs['id']}, price={price_str}, title={title}"
    return "None"

# ─── 5. AVITO ───
def test_avito(c):
    url = "https://www.avito.ru/moskva/avto/167854494"
    result = c.collect_product("167854494", url, "Тест AVITO")
    if result:
        obs = result["observation"]
        title = obs.get("fields", {}).get("title", {}).get("value", "?")[:60]
        prices = obs.get("price_candidates", [])
        price_str = prices[0]["amount"] + " " + prices[0].get("currency", "?") if prices else "no price"
        return f"id={obs['id']}, price={price_str}, title={title}"
    return "None"

# ─── 6. FARPOST ───
def test_farpost(c):
    url = "https://www.farpost.ru/items/167854494"
    result = c.collect_product("167854494", url, "Тест Farpost")
    if result:
        obs = result["observation"]
        title = obs.get("fields", {}).get("title", {}).get("value", "?")[:60]
        prices = obs.get("price_candidates", [])
        price_str = prices[0]["amount"] + " " + prices[0].get("currency", "?") if prices else "no price"
        return f"id={obs['id']}, price={price_str}, title={title}"
    return "None"


def main():
    from r52.collectors.aliexpress import AliExpressRUCollector, AliExpressCollector
    from r52.collectors.wb import WBCollector
    from r52.collectors.ozon import OzonCollector
    from r52.collectors.avito import AvitoCollector
    from r52.collectors.farpost import FarpostCollector

    test_collector("ALIEXPRESS_RU (search)", AliExpressRUCollector, test_ali_ru)
    test_collector("ALIEXPRESS (global)", AliExpressCollector, test_ali_com)
    test_collector("WB", WBCollector, test_wb)
    test_collector("OZON", OzonCollector, test_ozon)
    test_collector("AVITO", AvitoCollector, test_avito)
    test_collector("FARPOST", FarpostCollector, test_farpost)

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for name, r in RESULTS.items():
        status = "OK" if r["ok"] else "FAIL"
        detail = r.get("detail", r.get("error", ""))
        print(f"  [{status}] {name} ({r['elapsed']}s): {detail[:100]}")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print("\nSaved → test_results.json")

if __name__ == "__main__":
    main()
