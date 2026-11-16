"""Быстрая проверка нового ozon.py на сохранённом HTML (офлайн)."""
import sys
import os
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO)

from r52.collectors.ozon import OzonCollector

class FakeResp:
    def __init__(self, text):
        self.text = text

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base, "v35_OZON_prod1.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    print(f"HTML size: {len(html)} bytes")

    collector = OzonCollector(use_proxies=False)
    resp = FakeResp(html)
    url = "https://www.ozon.ru/product/tkan-dlya-obivki-mebeli-velyur-s-abstraktnym-printom-otrez-3-metra-1600562267/"
    result = collector.parse_page(resp, "1600562267", url, "Ткань для обивки")

    if result is None:
        print("FAIL: parse_page returned None")
        return

    print("\n=== FIELDS ===")
    for k, v in result["fields"].items():
        val = v.get("value", "")
        if isinstance(val, str) and len(val) > 120:
            val = val[:120] + "..."
        print(f"  {k}: {val}  [{v.get('method','')}]")

    print(f"\n=== PRICES ({len(result['price_candidates'])}) ===")
    for p in result["price_candidates"][:10]:
        print(f"  {p['role']:10s} {p['amount_minor']/100:>10.2f} ₽  [{p['label']}]")

    print(f"\n=== MEDIA ({len(result['media']['items'])}) ===")
    for m in result["media"]["items"][:8]:
        print(f"  {m['type']:6s} {m['source_url'][:80]}")

    print(f"\n=== AVAILABILITY: {result['availability']} ===")
    print(f"=== CATEGORY: {result['category_path']} ===")

    # Сохраняем полный результат
    out_path = os.path.join(base, "test_ozon_v2_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nSaved → {out_path}")

if __name__ == "__main__":
    main()
