"""Полная проверка MTOP-парсера + normalize_eye: все поля, правильные ключи."""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r52.collectors.aliexpress import AliExpressCollector

col = AliExpressCollector.__new__(AliExpressCollector)
col.source = "aliexpress"
col.stats = {"requests": 0, "success": 0, "failed": 0, "blocked": 0}
col.pool = type("P", (), {
    "mark_success": lambda s, l: None,
    "mark_failure": lambda s: None,
    "throttle": lambda s: None,
})()
col._throttle = lambda: None

obs = {
    "fields": {},
    "price_candidates": [],
    "media": {"items": []},
    "availability": "UNKNOWN",
    "category_path": None,
}
obs["source"] = "ALIEXPRESS"
obs["id"] = "1005013496905897"
obs["listing_id"] = "1005013496905897"
obs["url"] = "https://www.aliexpress.com/item/1005013496905897.html"
obs["observed_at"] = "2026-11-13T03:30:00+10:00"

body = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "mtop_pw62_pdp_905897.json"), "r", encoding="utf-8").read()
ok = col._parse_mtop(body, obs)
print(f"parse_mtop ok={ok}")
print()

# ── Raw obs fields ──
print("=== OBS FIELDS ===")
for k in sorted(obs["fields"].keys()):
    v = obs["fields"][k]
    val = v.get("value")
    if isinstance(val, str):
        print(f"  {k}: {val[:100]}")
    elif isinstance(val, (int, float)):
        print(f"  {k}: {val}")
    elif isinstance(val, list):
        print(f"  {k}: list[{len(val)}]")
    elif isinstance(val, dict):
        print(f"  {k}: dict keys={list(val.keys())[:8]}")
    else:
        print(f"  {k}: {val}")

print(f"\nPrices: {len(obs['price_candidates'])}")
for p in obs["price_candidates"]:
    print(f"  {p.get('role','?')}: {p.get('amount','?')} {p.get('currency','?')}")
print(f"Images: {len(obs['media']['items'])}")
for i, img in enumerate(obs['media']['items'][:3]):
    print(f"  [{i}] {img.get('source_url','')[:80]}")
print(f"Availability: {obs['availability']}")
print(f"Category: {obs['category_path']}")

# ── normalize_eye ──
print("\n=== NORMALIZE_EYE ===")
errors = []
try:
    from r52.domain.normalize import normalize_eye
    eye, cleaned = normalize_eye(obs)

    # Core identity
    lid = eye.get('listing_id', '')
    title = eye.get('title', '')
    print(f"  listing_id: {lid}")
    print(f"  title: {title[:80]}")
    if not lid: errors.append("listing_id empty")
    if not title: errors.append("title empty")

    # Prices
    prices = eye.get('prices', [])
    print(f"  prices: {len(prices)}")
    for p in prices:
        print(f"    {p.get('role','?')}: {p.get('amount_minor','?')} {p.get('currency','?')}")
    if not prices: errors.append("no prices in normalized")

    # Media
    media_items = (eye.get('media') or {}).get('items', [])
    print(f"  media items: {len(media_items)}")
    if not media_items: errors.append("no media in normalized")

    # Availability
    avail = eye.get('availability', 'UNKNOWN')
    print(f"  availability: {avail}")
    if avail == 'UNKNOWN': errors.append("availability UNKNOWN")

    # Category
    cat = eye.get('category_path', '')
    print(f"  category_path: {cat}")

    # Characteristics
    chars = eye.get('characteristics', [])
    print(f"  characteristics: {len(chars)}")
    for c in chars[:5]:
        print(f"    {c.get('label','?')}: {str(c.get('value',''))[:60]}")
    if not chars: errors.append("no characteristics in normalized")

    # Fields (availability, delivery, etc.)
    fields = eye.get('fields', {})
    for fk in ('availability', 'delivery', 'condition', 'displayed_price'):
        fi = fields.get(fk, {})
        fv = fi.get('value')
        fs = fi.get('state', 'UNKNOWN')
        if fs != 'UNKNOWN':
            print(f"  field[{fk}]: {str(fv)[:80]}")

    # Delivery
    delivery = eye.get('delivery', {})
    print(f"  delivery.terms: {str(delivery.get('terms',''))[:100]}")
    print(f"  delivery.cost: {delivery.get('cost_minor')} {delivery.get('cost_state')}")

    # From cleaned obs (extra fields not in normalized directly)
    cfields = cleaned.get('fields', {})

    sku_var = cfields.get('sku_variants', {}).get('value', [])
    print(f"  sku_variants: {len(sku_var)}")
    for sv in sku_var[:3]:
        print(f"    {sv.get('sku_id','?')}: {sv.get('attr','?')[:50]} salable={sv.get('salable')}")

    shop = cfields.get('shop', {}).get('value', {})
    print(f"  shop: {shop.get('name','N/A')} (pos_rate={shop.get('positive_rate','?')})")

    rating = cfields.get('rating', {}).get('value')
    print(f"  rating: {rating}")

    pp = cfields.get('product_props', {}).get('value', [])
    print(f"  product_props: {len(pp)}")

    shipping = cfields.get('shipping', {}).get('value', {})
    print(f"  shipping: from={shipping.get('ship_from','?')} fee={shipping.get('fee','?')} eta={shipping.get('eta_min_days')}-{shipping.get('eta_max_days')}d")

    # Completeness
    comp = eye.get('completeness', {})
    print(f"\n  completeness: {comp.get('known',0)}/{comp.get('total',0)} known")

    if errors:
        print(f"\n  WARNINGS: {errors}")
    else:
        print(f"\n  RESULT: ALL FIELDS PASS")

except ImportError as e:
    print(f"  ImportError: {e}")
    print(f"\n  RESULT: normalize_eye NOT FOUND")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"\n  RESULT: ERROR: {e}")

print("\n=== DONE ===")
