"""Полный E2E Ozon: SSR-HTML → OzonCollector.parse_page → Eye JSON → stage → accept → SQLite.

Проверяет:
1. Офлайн-парсинг реального SSR-HTML (diag_v48_ozon_1600562267.html).
2. Обёртка в формат JARVIS_BR02_EYE_USER_CAPTURE.
3. stage_bytes → accept → SQLite.
4. SQLite содержит title, price, brand, media, availability.
"""
import json
import logging
import os
import sys
import tempfile
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, str(Path(__file__).parent))

# --- Временный home ---
TMP_HOME = Path(tempfile.mkdtemp(prefix="r52-e2e-ozon-"))
os.environ["R52_HOME"] = str(TMP_HOME)
print(f"TMP_HOME: {TMP_HOME}")

from dataclasses import replace
from r52.config import load_config
from r52.service import App

# --- Интэйк-папка для Ozon ---
INTAKE_DIR = Path(__file__).parent / "test-intake-e2e-ozon"
INTAKE_DIR.mkdir(exist_ok=True)
# Очистка предыдущих файлов
for f in INTAKE_DIR.glob("jarvis-eye-ozon-*.json"):
    f.unlink()

config = load_config(home=str(TMP_HOME))
config = replace(config, intake_folders=(INTAKE_DIR,))
print(f"Config: db={config.paths.db_file}, intake={config.intake_folders}")

app = App(config, install_guard=False)
app.open()
print("App opened OK")

# --- 1. Загружаем SSR-HTML ---
HTML_FILE = Path(__file__).parent / "diag_v48_ozon_1600562267.html"
if not HTML_FILE.exists():
    print(f"FATAL: {HTML_FILE} not found")
    sys.exit(1)

html = HTML_FILE.read_text(encoding="utf-8")
print(f"\nHTML loaded: {len(html)} bytes")

LISTING_ID = "1600562267"
URL = "https://www.ozon.ru/product/tkan-dlya-obivki-mebeli-velyur-s-abstraktnym-printom-otrez-3-metra-1600562267/"

# --- 2. Парсинг через OzonCollector ---
from r52.collectors.ozon import OzonCollector

collector = OzonCollector(data_dir=None, use_proxies=False)

# Mock Response
class MockResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.url = URL

resp = MockResp(html)

print("\n--- Parsing via OzonCollector.parse_page ---")
observation = collector.parse_page(resp, LISTING_ID, URL, "")

if observation is None:
    print("FATAL: parse_page returned None")
    sys.exit(1)

# --- 3. Пост-обработка (как в base.py collect_product) ---
collector._ensure_evidence(observation)
collector._ensure_price_amounts(observation)
collector._ensure_availability_in_fields(observation)

# --- 4. Обёртка в Eye-формат ---
now = datetime.now(timezone(timedelta(hours=10))).isoformat()
eye_doc = {
    "format": "JARVIS_BR02_EYE_USER_CAPTURE",
    "capture_action": "EXPLICIT_POPUP_CLICK",
    "fully_autonomous": False,
    "collector_source": "OZON",
    "observation": {
        **observation,
        "source": "OZON",
        "id": LISTING_ID,
        "url": URL,
        "observed_at": now,
        "status": "PARTIAL_SUCCESS",
        "product_content": True,
        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
        "http_status": 200,
    },
}

# --- 5. Запись JSON в интэйк ---
json_path = INTAKE_DIR / f"jarvis-eye-ozon-{LISTING_ID}.json"
json_path.write_text(json.dumps(eye_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nEye JSON written: {json_path} ({json_path.stat().st_size} bytes)")

# --- 6. Pipeline: stage → accept ---
print("\n--- Pipeline: stage_bytes → accept ---")
data = json_path.read_bytes()
item, created = app.inbox.stage_bytes(data, filename=json_path.name, origin="E2E_OZON")
print(f"Stage: status={item['status']}, created={created}, id={item['id']}")

if item["status"] == "INVALID":
    print(f"INVALID: error_code={item['error_code']}")
    print(f"Preview: {item.get('preview')}")
    sys.exit(1)

accepted = app.inbox.accept(item["id"])
print(f"Accept: product_id={accepted.get('product_id')}, "
      f"observation_created={accepted.get('observation_created')}")

# --- 7. Verify SQLite ---
print(f"\n{'='*60}")
print("SQLITE VERIFICATION (OZON)")
print(f"{'='*60}")

with app.db.read() as conn:
    products = conn.execute(
        "SELECT id, source, listing_id, title, brand, url, last_availability, "
        "last_regular_minor, last_lowest_minor, observation_count, category_path "
        "FROM product WHERE source = 'OZON' ORDER BY id"
    ).fetchall()

    print(f"\nProducts (OZON): {len(products)}")
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
        "WHERE p.source = 'OZON' ORDER BY o.id"
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
        "WHERE p.source = 'OZON' ORDER BY pp.id"
    ).fetchall()
    print(f"\nPrice points: {len(pp_rows)}")
    for pp in pp_rows:
        d = dict(pp)
        print(f"  pp_id={d['id']} role={d['role']} amount={d['amount_minor']} ({d['amount_minor']//100 if d['amount_minor'] else '?'} {d['currency']}) label={d['label']}")

    # Gallery
    gallery_rows = conn.execute(
        "SELECT p.id, p.gallery_json FROM product p WHERE p.source = 'OZON'"
    ).fetchall()
    print(f"\nGallery data:")
    for g in gallery_rows:
        d = dict(g)
        items = json.loads(d["gallery_json"] or "[]")
        print(f"  product={d['id']}: {len(items)} media items")
        for item in items[:5]:
            print(f"    type={item.get('type')} url={str(item.get('source_url',''))[:80]}")
        if len(items) > 5:
            print(f"    ... and {len(items)-5} more")

# --- 8. Summary ---
print(f"\n{'='*60}")
ok = len(products) > 0 and len(pp_rows) > 0
print(f"SUMMARY: {'PASS' if ok else 'FAIL'} — {len(products)} product(s), {len(pp_rows)} price point(s), {len(obs_rows)} observation(s)")

app.stop()
print(f"\nApp stopped. TMP_HOME={TMP_HOME}")
sys.exit(0 if ok else 1)
