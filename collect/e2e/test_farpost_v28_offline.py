"""Тест FarpostCollector v28 (offline): парсинг кэшированных HTML.

Использует fp_v27_*.html, сохранённые при успешном первом прогоне.
Проверяет: дедупликацию, метаданные, цену, specs, title.
Exit: 0 = PASS, 1 = FAIL.
"""
import json
import sys
import logging
from pathlib import Path
from types import SimpleNamespace

logging.basicConfig(level=logging.WARNING)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from r52.collectors.farpost import FarpostCollector

# Кэшированные HTML из успешного прогона
CARDS = [
    {
        "label": "LingLong",
        "html_file": "fp_v27_139122082.html",
        "id": "139122082",
        "expected_photos": 1,
    },
    {
        "label": "Michelin",
        "html_file": "fp_v27_137723592.html",
        "id": "137723592",
        "expected_photos": 18,
    },
    {
        "label": "LongMarch",
        "html_file": "fp_v27_137440009.html",
        "id": "137440009",
        "expected_photos": 7,
    },
]


def make_resp(html: str) -> SimpleNamespace:
    """Имитация requests.Response для parse_page."""
    return SimpleNamespace(text=html, status_code=200)


def main():
    collector = FarpostCollector(data_dir=ROOT, use_proxies=False)
    all_pass = True
    results = {}

    for card in CARDS:
        label = card["label"]
        lid = card["id"]
        expected = card["expected_photos"]
        html_path = ROOT / card["html_file"]

        print(f"\n{'='*60}")
        print(f"TEST: {label} (ID={lid})")
        print(f"{'='*60}")

        if not html_path.exists():
            print(f"  FAIL: {html_path.name} not found")
            results[label] = {"status": "FAIL", "reason": "html missing"}
            all_pass = False
            continue

        html = html_path.read_text(encoding="utf-8")
        if len(html) < 5000:
            print(f"  FAIL: HTML too small ({len(html)} bytes) — likely landing page")
            results[label] = {"status": "FAIL", "reason": "landing page"}
            all_pass = False
            continue

        resp = make_resp(html)
        obs = collector.parse_page(resp, lid, f"https://farpost.ru/{lid}", "")

        if obs is None:
            print("  FAIL: parse_page returned None")
            results[label] = {"status": "FAIL", "reason": "None"}
            all_pass = False
            continue

        fields = obs.get("fields", {})
        prices = obs.get("price_candidates", [])
        media = obs.get("media", {}).get("items", [])
        photos = [m for m in media if m["type"] == "image"]
        videos = [m for m in media if m["type"] == "video"]

        checks = {}

        # 1. Фото count
        checks["photos_count"] = {
            "expected": expected,
            "actual": len(photos),
            "pass": len(photos) == expected,
        }

        # 2. Метаданные (для галерейных: photos>1 или /v/ в URL)
        has_meta = any(
            p.get("alt") or p.get("label") or p.get("width")
            for p in photos
        )
        gallery_card = len(photos) > 1 or any("/v/" in p["source_url"] for p in photos)
        checks["metadata_present"] = {
            "expected": "required" if gallery_card else "optional",
            "actual": has_meta,
            "pass": has_meta if gallery_card else True,
        }

        # 3. is_main
        main_count = sum(1 for p in photos if p.get("is_main"))
        checks["is_main_count"] = {
            "expected": 1 if len(photos) > 1 else 0,
            "actual": main_count,
            "pass": main_count == (1 if len(photos) > 1 else 0),
        }

        # 4. Цена
        checks["price"] = {
            "expected": "present",
            "actual": f"{prices[0]['amount_minor']//100} RUB" if prices else "missing",
            "pass": len(prices) > 0,
        }

        # 5. Title
        title_val = fields.get("title", {}).get("value", "")
        checks["title"] = {
            "expected": "non-empty, not landing",
            "actual": title_val[:50],
            "pass": len(title_val) > 10 and "доска объявлений" not in title_val.lower(),
        }

        # 6. Specs
        specs = fields.get("specs", {}).get("value", {})
        checks["specs"] = {
            "expected": ">=5",
            "actual": len(specs),
            "pass": len(specs) >= 5,
        }

        # 7. Category
        cat_val = fields.get("category", {}).get("value", "")
        checks["category"] = {
            "expected": "non-empty",
            "actual": cat_val[:50],
            "pass": len(cat_val) > 3,
        }

        # 8. Videos
        checks["videos"] = {
            "expected": 0,
            "actual": len(videos),
            "pass": len(videos) == 0,
        }

        # Output
        label_pass = all(c["pass"] for c in checks.values())
        if not label_pass:
            all_pass = False

        print(f"  Status: {'PASS' if label_pass else 'FAIL'}")
        print(f"  Photos: {len(photos)} (expected {expected})")
        for p in photos[:4]:
            meta = ""
            if p.get("alt"):
                meta += f" alt='{p['alt'][:35]}'"
            if p.get("is_main"):
                meta += " [MAIN]"
            if p.get("width"):
                meta += f" {p['width']}x{p.get('height',0)}"
            print(f"    {p['source_url'][:72]}{meta}")
        if len(photos) > 4:
            print(f"    ... +{len(photos)-4} more")
        print(f"  Price:  {prices[0]['amount_minor']//100 if prices else 'N/A'} RUB")
        print(f"  Title:  {title_val[:60]}")
        print(f"  Specs:  {len(specs)}")
        print(f"  Cat:    {cat_val[:60]}")

        for cname, cdata in checks.items():
            mark = "OK  " if cdata["pass"] else "FAIL"
            print(f"    [{mark}] {cname}: {cdata['expected']} / {cdata['actual']}")

        results[label] = {
            "status": "PASS" if label_pass else "FAIL",
            "photos": len(photos),
            "videos": len(videos),
            "title": title_val,
            "price": prices[0] if prices else None,
            "specs_count": len(specs),
            "category": cat_val,
            "checks": checks,
            "photos_detail": [
                {
                    "url": p["source_url"],
                    "alt": p.get("alt", ""),
                    "label": p.get("label", ""),
                    "is_main": p.get("is_main", False),
                    "width": p.get("width", 0),
                    "height": p.get("height", 0),
                }
                for p in photos
            ],
        }

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {'ALL PASS' if all_pass else 'HAS FAILURES'}")
    print(f"{'='*60}")
    for label, res in results.items():
        print(f"  {label}: {res['status']} (photos={res.get('photos','?')})")

    out_path = ROOT / "test_farpost_v28_offline_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults: {out_path}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
