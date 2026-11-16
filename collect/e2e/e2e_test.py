"""E2E-тест Farpost и WB: прямой вызов коллекторов, вывод полей."""
import sys, json, logging, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

SEP = "=" * 60

def run_test(collector, lid, label):
    print(f"\n{SEP}")
    print(f"TEST: {label}  ID={lid}")
    print(SEP)
    url = collector.build_url(lid)
    print(f"URL: {url}")
    t0 = time.monotonic()
    try:
        result = collector.collect_product(lid, url)
    except Exception:
        traceback.print_exc()
        result = None
    elapsed = time.monotonic() - t0

    if result is None:
        print(f"RESULT: None ({elapsed:.1f}s)")
        print(f"Stats: {collector.stats}")
        return None

    obs = result["observation"]
    print(f"Status:       {obs.get('status')}")
    print(f"Title:        {obs['fields'].get('title', {}).get('value', 'N/A')}")
    print(f"Title method: {obs['fields'].get('title', {}).get('method', '?')}")

    prices = obs.get("price_candidates", [])
    print(f"Prices ({len(prices)}):")
    for p in prices:
        amt = p.get("amount", "?")
        print(f"  [{p.get('role')}] {amt} {p.get('currency')}  ({p.get('label')})")

    media = obs.get("media", {}).get("items", [])
    photos = sum(1 for m in media if m["type"] == "image")
    videos = sum(1 for m in media if m["type"] == "video")
    print(f"Media: {len(media)} items ({photos} photos, {videos} videos)")
    for m in media[:6]:
        print(f"  {m['type']:5s}: {m['source_url'][:90]}")
    if len(media) > 6:
        print(f"  ... +{len(media)-6} more")

    print(f"Availability: {obs.get('availability')}")
    print(f"Category:     {obs.get('category_path', 'N/A')}")
    print(f"Fields:       {list(obs['fields'].keys())}")

    # Check for brand / model / description / seller
    for key in ("brand", "model", "description", "seller", "specs", "options"):
        v = obs["fields"].get(key)
        if v and v.get("value"):
            val = str(v["value"])[:80]
            print(f"  {key:12s}: {val}")

    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Stats:   {collector.stats}")
    return result


def main():
    # --- FARPOST ---
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(use_proxies=False)
    run_test(fp, "130693618", "FARPOST")

    # --- WB ---
    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)
    run_test(wb, "199298871", "WB")

    print(f"\n{SEP}")
    print("DONE")
    print(SEP)


if __name__ == "__main__":
    main()
