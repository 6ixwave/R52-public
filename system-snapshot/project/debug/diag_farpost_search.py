"""
diag_farpost_search.py — Test FARPOST collect_by_search price extraction.
Runs live search and checks if prices are extracted correctly.
"""
import sys, os, json, time, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

from r52.collectors.farpost import FarpostCollector

QUERY = "тест"
PAGE = 1
PAGE_SIZE = 5

print(f"=== FARPOST SEARCH DIAGNOSTIC ===")
print(f"Query: {QUERY!r}, page={PAGE}, size={PAGE_SIZE}")
print()

collector = FarpostCollector(data_dir=None, use_proxies=False)
print(f"Collector: {collector.source}, proxies={collector.pool.use_proxies}")
print()

t0 = time.time()
try:
    rows = collector.collect_by_search(QUERY, page=PAGE, page_size=PAGE_SIZE)
    elapsed = time.time() - t0
    print(f"Rows returned: {len(rows)} ({elapsed:.1f}s)")
    print()

    has_price = 0
    has_title = 0
    has_img = 0
    for i, row in enumerate(rows):
        obs = row.get("observation", {})
        aid = obs.get("id", "?")
        title = obs.get("title", "?")[:50]
        prices = obs.get("price_candidates", [])
        media = obs.get("media", {}).get("items", [])
        url = obs.get("url", "?")

        p_str = "NO_PRICE"
        if prices:
            p_str = f"{prices[0].get('amount', '?')} {prices[0].get('currency', '?')}"
            has_price += 1
        if title and not title.startswith("FARPOST-"):
            has_title += 1
        if media:
            has_img += 1

        print(f"  [{i+1}] ID={aid} | {p_str:>15} | {title}")
        print(f"      url={url[:80]}")
        if prices:
            for p in prices:
                print(f"      price: role={p.get('role')} minor={p.get('amount_minor')} cur={p.get('currency')}")
        if media:
            for m in media[:2]:
                print(f"      media: {m.get('type')} {m.get('source_url', '')[:60]}")
        print()

    print(f"=== SUMMARY ===")
    print(f"  Total: {len(rows)}")
    print(f"  With price: {has_price}/{len(rows)}")
    print(f"  With title: {has_title}/{len(rows)}")
    print(f"  With image: {has_img}/{len(rows)}")
    print(f"  Status: {'PASS' if has_price == len(rows) and len(rows) > 0 else 'CHECK'}")
    print(f"=== DONE ===")

except Exception as e:
    elapsed = time.time() - t0
    import traceback
    print(f"ERROR after {elapsed:.1f}s: {type(e).__name__}: {e}")
    traceback.print_exc()
    print("=== DONE (ERROR) ===")
