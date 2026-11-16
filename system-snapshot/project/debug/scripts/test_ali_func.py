"""Функциональный тест AliExpressCollector: полный цикл collect_product()."""
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Корень проекта в sys.path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from r52.collectors.aliexpress import AliExpressCollector

PRODUCT_ID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

print(f"\n{'='*60}")
print(f"ALI EXPRESS FUNCTIONAL TEST")
print(f"Product: {PRODUCT_ID}")
print(f"URL: {URL}")
print(f"{'='*60}\n")

t0 = time.monotonic()
collector = AliExpressCollector(data_dir=None, use_proxies=False)

try:
    print("[1] build_url...")
    url = collector.build_url(PRODUCT_ID)
    print(f"    → {url}")

    print("[2] collect_product()...")
    result = collector.collect_product(PRODUCT_ID, url)

    elapsed = time.monotonic() - t0

    if result is None:
        print(f"\n[RESULT] FAILED — collect_product returned None ({elapsed:.1f}s)")
        print(f"  stats: {collector.stats}")
        sys.exit(1)

    print(f"\n[RESULT] SUCCESS in {elapsed:.1f}s")
    print(f"  stats: {collector.stats}")

    # Распаковка
    obs = result.get("observation", {})
    fields = obs.get("fields", {})
    prices = obs.get("price_candidates", [])
    media = obs.get("media", {}).get("items", [])
    avail = obs.get("availability", "UNKNOWN")
    cat = obs.get("category_path", None)

    print(f"\n  status: {obs.get('status')}")
    print(f"  title: {fields.get('title', {}).get('value', 'N/A')[:100]}")
    print(f"  prices ({len(prices)}):")
    for p in prices[:5]:
        print(f"    {p.get('role','?')}: {p.get('amount')} {p.get('currency')} (label={p.get('visible_label','')})")
    print(f"  availability: {avail}")
    print(f"  category: {(cat or 'N/A')[:80]}")
    print(f"  images: {len(media)}")
    for img in media[:3]:
        print(f"    {img.get('source_url','')[:80]}")
    print(f"  fields keys: {list(fields.keys())}")

    # Сохраняем полный результат
    out = ROOT / "test_ali_func_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Full result saved → {out.name}")

except Exception as e:
    elapsed = time.monotonic() - t0
    import traceback
    print(f"\n[RESULT] EXCEPTION after {elapsed:.1f}s: {e}")
    traceback.print_exc()
    sys.exit(1)
finally:
    collector.close()
    print("\n[CLEANUP] collector closed")
