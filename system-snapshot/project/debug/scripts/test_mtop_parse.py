"""Быстрый тест: прогоняем mtop_pw62_pdp_905897.json через _parse_mtop."""
import sys, os, json, logging

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r52.collectors.aliexpress import AliExpressCollector

# Загружаем сохранённый MTOP body
mtop_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtop_pw62_pdp_905897.json")
with open(mtop_path, "r", encoding="utf-8") as f:
    body = f.read()

# Создаём коллектор без сети
col = AliExpressCollector.__new__(AliExpressCollector)
col.source = "aliexpress"
col.stats = {"requests": 0, "success": 0, "failed": 0, "blocked": 0}
col.pool = type("Pool", (), {"mark_success": lambda s, l: None, "mark_failure": lambda s: None, "throttle": lambda s: None})()
col._throttle = lambda: None

obs = {
    "fields": {},
    "price_candidates": [],
    "media": {"items": []},
    "availability": "UNKNOWN",
    "category_path": None,
}

ok = col._parse_mtop(body, obs)

print("\n" + "=" * 60)
print(f"MTOP parsed: {ok}")
print(f"Title: {obs['fields'].get('title', {}).get('value', 'N/A')[:100]}")
print(f"Prices ({len(obs['price_candidates'])}):")
for p in obs["price_candidates"]:
    print(f"  role={p['role']}  {p['amount']} {p['currency']}  label={p.get('visible_label','')}")
print(f"Availability: {obs['availability']}")
print(f"Images: {len(obs['media']['items'])}")
for im in obs["media"]["items"][:3]:
    print(f"  {im['source_url'][:80]}")
print(f"Category: {obs['category_path']}")
print(f"SKU variants: {len(obs['fields'].get('sku_variants', {}).get('value', []))}")
for sv in obs["fields"].get("sku_variants", {}).get("value", []):
    print(f"  id={sv['sku_id']}  attr={sv['attr'][:40]}  stock={sv['stock']}  salable={sv['salable']}")
print(f"SKU properties: {obs['fields'].get('sku_properties', {}).get('value', 'N/A')}")
print(f"Shipping: {json.dumps(obs['fields'].get('shipping', {}).get('value', {}), ensure_ascii=False)[:200]}")
print(f"Total inventory: {obs['fields'].get('total_inventory', {}).get('value', 'N/A')}")
print(f"Shop: {json.dumps(obs['fields'].get('shop', {}).get('value', {}), ensure_ascii=False)[:200]}")
print(f"Rating: {obs['fields'].get('rating', {}).get('value', 'N/A')}")
print(f"Description: {(obs['fields'].get('description', {}).get('value', '') or 'N/A')[:100]}")
print("=" * 60)
