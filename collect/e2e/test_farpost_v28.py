"""Тест FarpostCollector v28: дедупликация + метаданные галереи.

Проверки:
1. Long March: фото == 7 (не 14) — дедуп по каноническому ключу
2. Michelin: фото == 18
3. LingLong: фото == 1
4. Метаданные (alt, label, is_main, width, height) присутствуют у фото из галереи
5. Видео: 0 (на этих карточках нет видео — подтверждено в HTML)
6. Цена, specs, title, category, seller извлекаются

Exit code: 0 = все PASS, 1 = есть FAIL.
"""
import json
import sys
import time
import logging
from pathlib import Path

# Гасим stderr-шум в PowerShell
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from r52.collectors.farpost import FarpostCollector
import requests

TEST_URLS = [
    {
        "label": "LingLong",
        "url": "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-139122082.html",
        "id": "139122082",
        "expected_photos": 1,
    },
    {
        "label": "Michelin",
        "url": "https://www.farpost.ru/khabarovsk/auto/wheel/tire/shiny-michelin-x-ice-snow-suv-275-50r21-137723592.html",
        "id": "137723592",
        "expected_photos": 18,
    },
    {
        "label": "LongMarch",
        "url": "https://www.farpost.ru/ni/khabarovsk/auto/wheel/tire/gruzovaja-shina-long-march-lm777-425-85r21-24pr-173c-137440009.html",
        "id": "137440009",
        "expected_photos": 7,
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch(url: str) -> requests.Response:
    time.sleep(3)
    return requests.get(url, headers=HEADERS, timeout=(10, 30))


def main():
    collector = FarpostCollector(data_dir=ROOT, use_proxies=False)
    results = {}
    all_pass = True

    for item in TEST_URLS:
        label = item["label"]
        url = item["url"]
        lid = item["id"]
        expected = item["expected_photos"]

        print(f"\n{'='*60}")
        print(f"TEST: {label} (ID={lid})")
        print(f"{'='*60}")

        try:
            resp = fetch(url)
        except Exception as e:
            print(f"  FAIL: fetch error: {e}")
            results[label] = {"status": "FAIL", "reason": f"fetch: {e}"}
            all_pass = False
            continue

        if resp.status_code != 200:
            print(f"  FAIL: HTTP {resp.status_code}")
            results[label] = {"status": "FAIL", "reason": f"HTTP {resp.status_code}"}
            all_pass = False
            continue

        # Сохраняем HTML
        html_path = ROOT / f"fp_v28_{lid}.html"
        html_path.write_text(resp.text, encoding="utf-8")

        try:
            obs = collector.parse_page(resp, lid, url, "")
        except Exception as e:
            import traceback
            print(f"  FAIL: parse error: {e}")
            traceback.print_exc()
            results[label] = {"status": "FAIL", "reason": f"parse: {e}"}
            all_pass = False
            continue

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

        # --- ASSERTIONS ---
        checks = {}

        # 1. Фото count
        checks["photos_count"] = {
            "expected": expected,
            "actual": len(photos),
            "pass": len(photos) == expected,
        }

        # 2. Метаданные: для галерейных фото (/v/ или photos>1) — обязательно.
        #    Для одиночного каталожного фото (farpost_catalog/) — опционально.
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

        # 3. is_main: ровно одно фото с is_main=True (если есть галерея)
        main_count = sum(1 for p in photos if p.get("is_main"))
        checks["is_main_count"] = {
            "expected": 1 if len(photos) > 1 else 0,
            "actual": main_count,
            "pass": main_count == (1 if len(photos) > 1 else 0),
        }

        # 4. Цена
        checks["price"] = {
            "expected": "present",
            "actual": "present" if prices else "missing",
            "pass": len(prices) > 0,
        }

        # 5. Title
        title_val = fields.get("title", {}).get("value", "")
        checks["title"] = {
            "expected": "non-empty",
            "actual": title_val[:50] if title_val else "empty",
            "pass": len(title_val) > 5,
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
            "actual": cat_val[:50] if cat_val else "empty",
            "pass": len(cat_val) > 3,
        }

        # 8. Videos = 0 (на этих карточках нет видео)
        checks["videos"] = {
            "expected": 0,
            "actual": len(videos),
            "pass": len(videos) == 0,
        }

        # --- OUTPUT ---
        label_pass = all(c["pass"] for c in checks.values())
        status = "PASS" if label_pass else "FAIL"
        if not label_pass:
            all_pass = False

        print(f"  Status: {status}")
        print(f"  Photos: {len(photos)} (expected {expected})")
        for p in photos[:3]:
            meta_str = ""
            if p.get("alt"):
                meta_str += f" alt='{p['alt'][:30]}'"
            if p.get("is_main"):
                meta_str += " [MAIN]"
            if p.get("width"):
                meta_str += f" {p['width']}x{p.get('height',0)}"
            print(f"    {p['source_url'][:70]}{meta_str}")
        if len(photos) > 3:
            print(f"    ... and {len(photos)-3} more")
        print(f"  Videos: {len(videos)}")
        print(f"  Price:  {prices[0]['amount_minor']//100 if prices else 'N/A'} RUB")
        print(f"  Title:  {title_val[:60]}")
        print(f"  Specs:  {len(specs)}")
        print(f"  Cat:    {cat_val[:60]}")

        # Детальный вывод failed checks
        for cname, cdata in checks.items():
            mark = "OK " if cdata["pass"] else "FAIL"
            print(f"    [{mark}] {cname}: expected={cdata['expected']}, actual={cdata['actual']}")

        results[label] = {
            "status": status,
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

    # --- SUMMARY ---
    print(f"\n{'='*60}")
    print(f"SUMMARY: {'ALL PASS' if all_pass else 'HAS FAILURES'}")
    print(f"{'='*60}")
    for label, res in results.items():
        print(f"  {label}: {res['status']} (photos={res.get('photos','?')})")

    # Save JSON
    out_path = ROOT / "test_farpost_v28_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults: {out_path}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
