"""Полный E2E Farpost: JSON → Inbox.stage_bytes → accept → SQLite.

Проверяет:
1. stage_bytes принимает JSON (PENDING).
2. accept() создаёт product + observation + price_point.
3. SQLite содержит реальные данные (title, price, availability).
"""
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, str(Path(__file__).parent))

# --- Временный home, чтобы не трогать основной R52 ---
TMP_HOME = Path(tempfile.mkdtemp(prefix="r52-e2e-farpost-"))
os.environ["R52_HOME"] = str(TMP_HOME)
print(f"TMP_HOME: {TMP_HOME}")

from dataclasses import replace
from r52.config import load_config
from r52.service import App

config = load_config(home=str(TMP_HOME))
config = replace(config, intake_folders=(Path(__file__).parent / "test-intake-e2e",))
print(f"Config: db={config.paths.db_file}, intake={config.intake_folders}")

app = App(config, install_guard=False)
app.open()
print("App opened OK")

# --- Загружаем JSON-файлы из test-intake-e2e/ ---
json_dir = Path(__file__).parent / "test-intake-e2e"
json_files = sorted(json_dir.glob("jarvis-eye-farpost-*.json"))
if not json_files:
    print("FATAL: no JSON files in test-intake-e2e/")
    sys.exit(1)

print(f"\nFound {len(json_files)} JSON files:")
for f in json_files:
    print(f"  {f.name} ({f.stat().st_size} bytes)")

# --- Pipeline: stage → accept → verify ---
results = []
for jf in json_files:
    data = jf.read_bytes()
    print(f"\n--- {jf.name} ---")

    # 1. Stage
    item, created = app.inbox.stage_bytes(data, filename=jf.name, origin="E2E_TEST")
    print(f"  Stage: status={item['status']}, created={created}, id={item['id']}")
    if item["status"] == "INVALID":
        print(f"  INVALID: error_code={item['error_code']}")
        results.append({"file": jf.name, "stage": "INVALID", "error": item["error_code"]})
        continue
    if not created:
        print(f"  DUPLICATE (already staged)")
        results.append({"file": jf.name, "stage": "DUPLICATE"})
        continue

    # 2. Accept
    try:
        accepted = app.inbox.accept(item["id"])
        print(f"  Accept: product_id={accepted.get('product_id')}, "
              f"observation_created={accepted.get('observation_created')}")
        results.append({"file": jf.name, "stage": "ACCEPTED",
                        "product_id": accepted.get("product_id"),
                        "observation_created": accepted.get("observation_created")})
    except Exception as e:
        print(f"  ACCEPT FAILED: {type(e).__name__}: {e}")
        results.append({"file": jf.name, "stage": "ACCEPT_FAIL", "error": str(e)[:200]})
        continue

# --- 3. Verify in SQLite ---
print(f"\n{'='*60}")
print("SQLITE VERIFICATION")
print(f"{'='*60}")

with app.db.read() as conn:
    products = conn.execute(
        "SELECT id, source, listing_id, title, brand, url, last_availability, "
        "last_regular_minor, last_lowest_minor, observation_count, category_path "
        "FROM product WHERE source = 'FARPOST' ORDER BY id"
    ).fetchall()

    print(f"\nProducts (FARPOST): {len(products)}")
    for p in products:
        d = dict(p)
        print(f"\n  ID={d['id']} listing={d['listing_id']}")
        print(f"  Title: {d['title']}")
        print(f"  Brand: {d['brand']}")
        print(f"  URL: {d['url']}")
        print(f"  Availability: {d['last_availability']}")
        print(f"  Price regular: {d['last_regular_minor']} minor ({d['last_regular_minor']//100 if d['last_regular_minor'] else '?'})")
        print(f"  Price lowest: {d['last_lowest_minor']} minor")
        print(f"  Observations: {d['observation_count']}")
        print(f"  Category: {d['category_path']}")

    # Observations
    obs_rows = conn.execute(
        "SELECT o.id, o.product_id, o.observed_at, o.origin, o.evidence_level, o.availability, "
        "length(o.normalized_json) as norm_len, length(o.raw_json) as raw_len "
        "FROM observation o JOIN product p ON p.id = o.product_id "
        "WHERE p.source = 'FARPOST' ORDER BY o.id"
    ).fetchall()
    print(f"\nObservations: {len(obs_rows)}")
    for o in obs_rows:
        d = dict(o)
        print(f"  obs_id={d['id']} product={d['product_id']} at={d['observed_at']} "
              f"origin={d['origin']} evidence={d['evidence_level']} avail={d['availability']} "
              f"norm={d['norm_len']}B raw={d['raw_len']}B")

    # Price points
    pp_rows = conn.execute(
        "SELECT pp.id, pp.observation_id, pp.product_id, pp.role, pp.amount_minor, pp.currency, pp.label "
        "FROM price_point pp JOIN product p ON p.id = pp.product_id "
        "WHERE p.source = 'FARPOST' ORDER BY pp.id"
    ).fetchall()
    print(f"\nPrice points: {len(pp_rows)}")
    for pp in pp_rows:
        d = dict(pp)
        print(f"  pp_id={d['id']} role={d['role']} amount={d['amount_minor']} ({d['amount_minor']//100 if d['amount_minor'] else '?'} {d['currency']}) label={d['label']}")

    # Gallery
    gallery_rows = conn.execute(
        "SELECT p.id, p.gallery_json FROM product p WHERE p.source = 'FARPOST'"
    ).fetchall()
    print(f"\nGallery data:")
    for g in gallery_rows:
        d = dict(g)
        items = json.loads(d["gallery_json"] or "[]")
        print(f"  product={d['id']}: {len(items)} media items")
        for item in items:
            print(f"    type={item.get('type')} url={str(item.get('source_url',''))[:80]}")

# --- Summary ---
print(f"\n{'='*60}")
ok_count = sum(1 for r in results if r["stage"] == "ACCEPTED")
print(f"SUMMARY: {ok_count}/{len(results)} files fully accepted")
for r in results:
    print(f"  {r['file']}: {r['stage']} {r.get('error','')}")

# --- Cleanup ---
app.stop()
print(f"\nApp stopped. TMP_HOME={TMP_HOME} (can be deleted)")

# Exit code: 0 if at least one product in SQLite, 1 otherwise
sys.exit(0 if len(products) > 0 else 1)
