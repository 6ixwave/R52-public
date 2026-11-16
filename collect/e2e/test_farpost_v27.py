"""Изолированный тест FarpostCollector v27.

Запускает парсер на 3 реальных карточках и выводит JSON-сводку.
Прямое соединение (без прокси) для скорости теста.
"""
import json
import sys
import time
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Добавляем корень проекта
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from r52.collectors.farpost import FarpostCollector
import requests

# 3 тестовые карточки (из diag_v26)
TEST_URLS = [
    {
        "label": "LingLong (1 фото, нет Product JSON-LD)",
        "url": "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-139122082.html",
        "id": "139122082",
    },
    {
        "label": "Michelin (18 фото, Product JSON-LD)",
        "url": "https://www.farpost.ru/khabarovsk/auto/wheel/tire/shiny-michelin-x-ice-snow-suv-275-50r21-137723592.html",
        "id": "137723592",
    },
    {
        "label": "Long March (7 фото, Product JSON-LD)",
        "url": "https://www.farpost.ru/ni/khabarovsk/auto/wheel/tire/gruzovaja-shina-long-march-lm777-425-85r21-24pr-173c-137440009.html",
        "id": "137440009",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch(url: str) -> requests.Response:
    """Прямой GET без прокси (для теста)."""
    time.sleep(3)  # мягкая пауза
    return requests.get(url, headers=HEADERS, timeout=(10, 30))


def main():
    collector = FarpostCollector(data_dir=ROOT, use_proxies=False)
    results = {}

    for item in TEST_URLS:
        label = item["label"]
        url = item["url"]
        lid = item["id"]
        print(f"\n{'='*60}")
        print(f"TEST: {label}")
        print(f"URL:  {url}")
        print(f"{'='*60}")

        try:
            resp = fetch(url)
            print(f"HTTP {resp.status_code}, {len(resp.text)} байт")
        except Exception as e:
            print(f"FETCH ERROR: {e}")
            results[label] = {"error": str(e)}
            continue

        if resp.status_code != 200:
            results[label] = {"http_status": resp.status_code}
            continue

        # Сохраняем HTML для отладки
        html_path = ROOT / f"fp_v27_{lid}.html"
        html_path.write_text(resp.text, encoding="utf-8")
        print(f"HTML saved: {html_path.name}")

        try:
            obs = collector.parse_page(resp, lid, url, "")
        except Exception as e:
            import traceback
            print(f"PARSE ERROR: {e}")
            traceback.print_exc()
            results[label] = {"error": str(e)}
            continue

        if obs is None:
            results[label] = {"error": "parse_page returned None"}
            continue

        fields = obs.get("fields", {})
        prices = obs.get("price_candidates", [])
        media = obs.get("media", {}).get("items", [])
        photos = [m for m in media if m["type"] == "image"]
        videos = [m for m in media if m["type"] == "video"]
        specs = fields.get("specs", {}).get("value", {}) if isinstance(fields.get("specs"), dict) else {}

        summary = {
            "http_status": resp.status_code,
            "html_size": len(resp.text),
            "title": fields.get("title", {}).get("value", ""),
            "title_method": fields.get("title", {}).get("method", ""),
            "price": prices[0] if prices else None,
            "price_count": len(prices),
            "photos_count": len(photos),
            "photos_sample": [p["source_url"] for p in photos[:5]],
            "videos_count": len(videos),
            "videos_sample": [v["source_url"] for v in videos[:3]],
            "description": (fields.get("description", {}).get("value", "") or "")[:120],
            "category": fields.get("category", {}).get("value", ""),
            "brand": fields.get("brand", {}).get("value", ""),
            "model": fields.get("model", {}).get("value", ""),
            "specs_count": len(specs),
            "specs_sample": {k: v["value"] for k, v in list(specs.items())[:8]},
            "seller": fields.get("seller", {}).get("value", ""),
            "availability": obs.get("availability", "UNKNOWN"),
            "bulletin_id": fields.get("bulletin_id", {}).get("value", ""),
        }
        results[label] = summary

        # Печать сводки
        print(f"\n  Title:    {summary['title']}")
        print(f"  Price:    {summary['price']}")
        print(f"  Photos:   {summary['photos_count']}")
        for p in summary['photos_sample'][:3]:
            print(f"             {p[:90]}")
        print(f"  Videos:   {summary['videos_count']}")
        print(f"  Desc:     {summary['description'][:80]}...")
        print(f"  Category: {summary['category']}")
        print(f"  Brand:    {summary['brand']}")
        print(f"  Model:    {summary['model']}")
        print(f"  Specs:    {summary['specs_count']}")
        for k, v in list(summary['specs_sample'].items())[:5]:
            print(f"             {k}: {v}")
        print(f"  Seller:   {summary['seller']}")
        print(f"  Avail:    {summary['availability']}")
        print(f"  BullID:   {summary['bulletin_id']}")

    # Сохраняем итоговый JSON
    out_path = ROOT / "test_farpost_v27_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\n{'='*60}")
    print(f"RESULTS saved: {out_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
