"""Тест WB-коллектора: полный цикл на 2 товарах.

Запуск: C:/Python315/python.exe test_wb_collector.py
"""
import json
import logging
import sys
import time
from pathlib import Path

# Корень проекта
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from r52.collectors.wb import WBCollector

TEST_IDS = ["199298871", "1097975275"]
OUT_DIR = ROOT / "test_wb_out"
OUT_DIR.mkdir(exist_ok=True)

def main():
    collector = WBCollector(data_dir=ROOT, use_proxies=False)
    print(f"Stats before: {collector.stats}")
    print(f"Proxy stats: {collector.pool.stats()}")

    results = []
    for lid in TEST_IDS:
        print(f"\n{'='*60}")
        print(f"COLLECTING WB {lid}")
        print(f"{'='*60}")
        start = time.monotonic()
        url = collector.build_url(lid)
        obs = collector.collect_product(lid, url)
        elapsed = time.monotonic() - start

        if obs is None:
            print(f"  RESULT: NONE ({elapsed:.1f}s)")
            results.append({"id": lid, "status": "NONE"})
            continue

        item = obs["observation"]
        print(f"  status: {item.get('status')}")
        print(f"  title: {item['fields'].get('title', {}).get('value', '?')[:80]}")
        print(f"  brand: {item['fields'].get('brand', {}).get('value', '?')}")
        print(f"  model: {item['fields'].get('model', {}).get('value', '?')}")
        print(f"  category: {item['fields'].get('category', {}).get('value', '?')}")
        print(f"  availability: {item.get('availability')}")

        prices = item.get("price_candidates", [])
        print(f"  prices ({len(prices)}):")
        for p in prices:
            amt = p.get("amount_minor", 0) / 100
            print(f"    {p['role']}: {amt:.2f} {p['currency']} [{p.get('label','?')}]")

        media = item.get("media", {}).get("items", [])
        photos = [m for m in media if m["type"] == "image"]
        videos = [m for m in media if m["type"] == "video"]
        print(f"  media: {len(photos)} photos, {len(videos)} videos")
        for m in photos[:3]:
            print(f"    IMG: {m['source_url'][:100]}")
        for m in videos[:2]:
            print(f"    VID: {m['source_url'][:100]}")

        desc = item["fields"].get("description", {}).get("value", "")
        print(f"  description: {len(desc)} chars")

        print(f"  elapsed: {elapsed:.1f}s")
        results.append({"id": lid, "status": item.get("status"), "elapsed": round(elapsed, 1)})

        # Сохраняем
        out_file = OUT_DIR / f"test_wb_{lid}.json"
        out_file.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  saved: {out_file.name}")

        time.sleep(2)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        print(f"  {r}")
    print(f"  Collector stats: {collector.stats}")

    # Финальный JSON
    summary_file = OUT_DIR / "test_wb_summary.json"
    summary_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone. Output: {OUT_DIR}")

if __name__ == "__main__":
    main()
