"""E2E: AliExpress MTOP → post-process → normalize → SQLite → read-back."""
import sys, os, json
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from r52.collectors.aliexpress import AliExpressCollector
from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE
from r52.store.products import (
    ensure_source, upsert_product, add_observation, refresh_summary, load_normalized
)
from r52.db import Database, migrate

print("=" * 60)
print("ALIEXPRESS E2E TEST")
print("=" * 60)

# ── 1. Parse MTOP ──
col = AliExpressCollector.__new__(AliExpressCollector)
col.source = "ALIEXPRESS"
col.stats = {"requests": 0, "success": 0, "failed": 0, "blocked": 0}
col.pool = type("P", (), {
    "mark_success": lambda s, l: None,
    "mark_failure": lambda s: None,
    "throttle": lambda s: None,
    "stats": lambda s: {},
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
obs["observed_at"] = "2026-11-13T04:00:00+10:00"

mtop_file = os.path.join(ROOT, "mtop_pw62_pdp_905897.json")
body = open(mtop_file, "r", encoding="utf-8").read()
ok = col._parse_mtop(body, obs)
print(f"\n[1] parse_mtop: ok={ok}")
print(f"    fields keys: {sorted(obs['fields'].keys())}")
print(f"    price_candidates: {len(obs['price_candidates'])}")
for p in obs["price_candidates"][:4]:
    print(f"      {p.get('role','?')}: {p.get('amount','?')} {p.get('currency','?')}")
print(f"    media items: {len(obs['media']['items'])}")
print(f"    availability: {obs['availability']}")
print(f"    category: {obs['category_path']}")

# ── 2. Base post-processing ──
col._ensure_evidence(obs)
col._ensure_price_amounts(obs)
col._ensure_availability_in_fields(obs)
print(f"\n[2] post-processing done")
known_fields = [k for k, v in obs["fields"].items()
                if isinstance(v, dict) and v.get("status") == "KNOWN" and v.get("evidence")]
print(f"    KNOWN fields with evidence: {len(known_fields)}")
for k in sorted(known_fields):
    v = obs["fields"][k].get("value")
    if isinstance(v, str):
        print(f"      {k}: {v[:60]}")
    elif isinstance(v, (int, float)):
        print(f"      {k}: {v}")
    elif isinstance(v, list):
        print(f"      {k}: list[{len(v)}]")
    elif isinstance(v, dict):
        print(f"      {k}: dict({list(v.keys())[:5]})")
    else:
        print(f"      {k}: {v}")

# ── 3. Wrap in intake document ──
now = datetime.now(timezone(timedelta(hours=10))).isoformat()
document = {
    "format": "JARVIS_BR02_EYE_USER_CAPTURE",
    "capture_action": "EXPLICIT_POPUP_CLICK",
    "fully_autonomous": False,
    "collector_source": "ALIEXPRESS",
    "observation": {
        **obs,
        "source": "ALIEXPRESS",
        "id": "1005013496905897",
        "url": "https://www.aliexpress.com/item/1005013496905897.html",
        "observed_at": now,
        "status": "PARTIAL_SUCCESS",
        "product_content": True,
        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
        "http_status": 200,
    },
}
print(f"\n[3] intake document ready (format={document['format']})")

# ── 4. Normalize ──
normalized, cleaned = normalize_eye(document["observation"])
comp = normalized["completeness"]
print(f"\n[4] normalize_eye:")
print(f"    completeness: {comp['known']}/{comp['total']} known")
print(f"    unknown fields: {comp['unknown']}")
print(f"    title: {normalized['title'][:80]}")
print(f"    brand: {normalized.get('brand')}")
print(f"    model: {normalized.get('model')}")
print(f"    prices: {len(normalized['prices'])}")
for p in normalized["prices"]:
    print(f"      {p['role']}: {p['amount_minor']} {p['currency']} ({p.get('label','')[:40]})")
print(f"    availability: {normalized['availability']}")
print(f"    media items: {len((normalized.get('media') or {}).get('items', []))}")
print(f"    characteristics: {len(normalized['characteristics'])}")
for c in normalized["characteristics"][:5]:
    print(f"      {c['label']}: {str(c['value'])[:50]}")
print(f"    delivery.terms: {str(normalized['delivery'].get('terms',''))[:100]}")
print(f"    delivery.cost: {normalized['delivery'].get('cost_minor')} ({normalized['delivery'].get('cost_state')})")
print(f"    category_path: {normalized.get('category_path')}")

# ── 5. SQLite via Database ──
db_path = os.path.join(ROOT, "test_ali_e2e.db")
for suffix in ("", "-wal", "-shm"):
    p = db_path + suffix
    if os.path.exists(p):
        os.remove(p)

db = Database(db_path)
db.initialize()
migrate(db)

with db.write() as conn:
    ensure_source(conn, "ALIEXPRESS", "AliExpress")
    product_id, created = upsert_product(
        conn, source="ALIEXPRESS", listing_id="1005013496905897",
        title=normalized["title"], url=normalized["url"],
        brand=normalized.get("brand"), model=normalized.get("model"),
        origin="COLLECTOR", now=now
    )
    print(f"\n[5] SQLite insert:")
    print(f"    product_id={product_id} created={created}")

    fp = fingerprint(EYE_SCOPE, cleaned)
    obs_id, obs_created = add_observation(
        conn, product_id=product_id, normalized=normalized, raw=cleaned,
        origin="COLLECTOR", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
        fingerprint=fp, observed_at=normalized["observed_at"],
        availability=normalized["availability"], prices=normalized["prices"], now=now
    )
    print(f"    observation_id={obs_id} created={obs_created}")
    refresh_summary(conn, product_id)

# ── 6. Read back ──
with db.read() as conn:
    row = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()
    print(f"\n[6] Read back from SQLite:")
    print(f"    title: {row['title'][:80]}")
    print(f"    url: {row['url']}")
    print(f"    brand: {row['brand']}")
    print(f"    model: {row['model']}")
    print(f"    last_currency: {row['last_currency']}")
    print(f"    last_regular_minor: {row['last_regular_minor']}")
    print(f"    last_lowest_minor: {row['last_lowest_minor']}")
    print(f"    last_lowest_role: {row['last_lowest_role']}")
    print(f"    last_availability: {row['last_availability']}")
    print(f"    observation_count: {row['observation_count']}")
    gallery = json.loads(row["gallery_json"] or "[]")
    print(f"    gallery images: {len(gallery)}")
    for g in gallery[:3]:
        print(f"      {g.get('source_url','')[:70]}")
    print(f"    category_path: {row['category_path']}")

    pp = conn.execute("SELECT * FROM price_point WHERE product_id=?", (product_id,)).fetchall()
    print(f"    price_points: {len(pp)}")
    for p in pp:
        print(f"      {p['role']}: {p['amount_minor']} {p['currency']}")

    norm_stored = load_normalized(conn, obs_id)
    print(f"    stored normalized completeness: {norm_stored['completeness']}")
    print(f"    stored normalized prices: {len(norm_stored['prices'])}")
    print(f"    stored normalized chars: {len(norm_stored['characteristics'])}")

    rows = conn.execute(
        "SELECT * FROM product WHERE source='ALIEXPRESS' AND listing_id='1005013496905897'"
    ).fetchall()
    print(f"    duplicate check: {len(rows)} product rows (expect 1)")

# ── 7. Re-insert (same fingerprint → no new obs) ──
with db.write() as conn:
    obs_id2, obs_created2 = add_observation(
        conn, product_id=product_id, normalized=normalized, raw=cleaned,
        origin="COLLECTOR", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
        fingerprint=fp, observed_at=normalized["observed_at"],
        availability=normalized["availability"], prices=normalized["prices"], now=now
    )
    print(f"\n[7] Re-insert obs: id={obs_id2} created={obs_created2} (expect False)")

# ── Summary ──
errors = []
if comp["known"] < 4:
    errors.append(f"completeness too low: {comp['known']}/14")
if not normalized["prices"]:
    errors.append("no prices")
if not (normalized.get("media") or {}).get("items"):
    errors.append("no media")
if normalized["availability"] == "UNKNOWN":
    errors.append("availability UNKNOWN")
if len(normalized["characteristics"]) < 5:
    errors.append(f"few characteristics: {len(normalized['characteristics'])}")
if row["last_availability"] != normalized["availability"]:
    errors.append("availability mismatch in product row")
if len(rows) != 1:
    errors.append("duplicate products")
if obs_created2:
    errors.append("fingerprint dedup failed")

print(f"\n{'=' * 60}")
if errors:
    print(f"RESULT: WARNINGS {errors}")
else:
    print(f"RESULT: ALL PASS")
print(f"{'=' * 60}")
