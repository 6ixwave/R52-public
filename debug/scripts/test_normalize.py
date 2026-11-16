"""Проверка: MTOP-парсер → normalize_eye → полный дамп + валидация."""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r52.collectors.aliexpress import AliExpressCollector
from r52.domain.normalize import normalize_eye, FIELDS

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
    "source": "ALIEXPRESS",
    "id": "1005013496905897",
    "listing_id": "1005013496905897",
    "url": "https://www.aliexpress.com/item/1005013496905897.html",
    "observed_at": "2026-11-13T03:30:00+10:00",
}

body = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "mtop_pw62_pdp_905897.json"), "r", encoding="utf-8").read()
ok = col._parse_mtop(body, obs)
print(f"parse_mtop ok={ok}")

# ── Мост: availability → fields (как делает base.py) ──
avail = obs.get("availability", "UNKNOWN")
if avail and avail != "UNKNOWN":
    obs["fields"]["availability"] = {
        "status": "KNOWN",
        "value": avail,
        "method": "MTOP",
        "evidence": {"method": "MTOP", "source": "aliexpress"},
    }

# ── Мост: shipping → delivery (normalize ищет "delivery") ──
ship = obs["fields"].get("shipping")
if ship:
    obs["fields"]["delivery"] = {
        "status": "KNOWN",
        "value": ship["value"],
        "method": ship.get("method", "MTOP"),
        "evidence": ship.get("evidence"),
    }

# ── Мост: shop → seller_location ──
shop = obs["fields"].get("shop")
if shop:
    obs["fields"]["seller_location"] = {
        "status": "KNOWN",
        "value": shop["value"].get("name", ""),
        "method": shop.get("method", "MTOP"),
        "evidence": shop.get("evidence"),
    }

# ── Мост: rating → reviews ──
rating = obs["fields"].get("rating")
if rating:
    obs["fields"]["reviews"] = {
        "status": "KNOWN",
        "value": f"Rating {rating['value']}/5",
        "method": rating.get("method", "MTOP"),
        "evidence": rating.get("evidence"),
    }

# ── Мост: product_props → characteristics (через fields) ──
# normalize._characteristics() ищет в raw_fields["characteristics"] или в fields
pp = obs["fields"].get("product_props")
if pp:
    obs["fields"]["characteristics"] = pp["value"]

print(f"\nCollector fields: {sorted(obs['fields'].keys())}")
print(f"Price candidates: {len(obs['price_candidates'])}")
print(f"Media items: {len(obs['media']['items'])}")

# ── Normalize ──
normalized, cleaned = normalize_eye(obs)

print("\n" + "=" * 60)
print("NORMALIZED EYE (full JSON)")
print("=" * 60)
print(json.dumps(normalized, ensure_ascii=False, indent=2))

# ── Validation ──
print("\n" + "=" * 60)
print("VALIDATION")
print("=" * 60)
errors = []

# Core identity
if not normalized.get("source"):
    errors.append("source empty")
if not normalized.get("listing_id"):
    errors.append("listing_id empty")
if not normalized.get("url"):
    errors.append("url empty")
if not normalized.get("title"):
    errors.append("title empty")
if not normalized.get("observed_at"):
    errors.append("observed_at empty")

# Prices
prices = normalized.get("prices", [])
if not prices:
    errors.append("no prices")
else:
    for p in prices:
        if not p.get("amount_minor"):
            errors.append(f"price {p.get('role')} has no amount_minor")
        if not p.get("currency"):
            errors.append(f"price {p.get('role')} has no currency")

# Availability
avail = normalized.get("availability", "UNKNOWN")
if avail == "UNKNOWN":
    errors.append("availability still UNKNOWN")

# Fields (normalized)
nf = normalized.get("fields", {})
for fkey in FIELDS:
    fval = nf.get(fkey, {})
    state = fval.get("state", "UNKNOWN")
    val = fval.get("value")
    print(f"  field {fkey:20s} state={state:15s} value={str(val)[:60]}")

# Media
media = normalized.get("media") or {}
img_count = len(media.get("items", []))
if img_count == 0:
    errors.append("no images in normalized media")

# Category
cat = normalized.get("category_path", "")
if not cat:
    errors.append("no category_path")

# Completeness
comp = normalized.get("completeness", {})
print(f"\n  completeness: {json.dumps(comp, ensure_ascii=False)}")

# Extra fields from collector (not in FIELDS)
extra = set(obs["fields"].keys()) - set(FIELDS.keys()) - set(
    ("characteristics", "delivery", "seller_location", "reviews")
)
if extra:
    print(f"  extra collector fields (not in normalize FIELDS): {sorted(extra)}")

print()
if errors:
    print(f"  ERRORS ({len(errors)}):")
    for e in errors:
        print(f"    - {e}")
else:
    print("  ALL CHECKS PASS")

print("\n=== DONE ===")
