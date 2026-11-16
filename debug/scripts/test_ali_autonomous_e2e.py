"""Сквозной автономный тест: AliExpress → JSON → Inbox → SQLite → read-back.

Проверяет полный конвейер:
  1. Сбор (Playwright или MTOP-файл)
  2. Обёртка в JARVIS_BR02_COLLECTOR_AUTONOMOUS
  3. Inbox.stage_bytes (как FolderWatcher)
  4. Inbox.accept → upsert_product + add_observation
  5. Read-back: product, prices, gallery, characteristics
  6. Дедупликация (fingerprint)
"""
import sys, os, json, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from r52.collectors.aliexpress import AliExpressCollector
from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE
from r52.intake.eye import Inbox, check_export, parse_export_bytes
from r52.db import Database, migrate
from r52.store.products import ensure_source, load_normalized

PASS = 0
FAIL = 0
ERRORS = []

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        ERRORS.append(f"{label}: {detail}")
        print(f"  [FAIL] {label} — {detail}")

print("=" * 70)
print("ALIEXPRESS AUTONOMOUS E2E TEST")
print(f"Time: {datetime.now().isoformat()}")
print("=" * 70)

# ── 0. Fresh DB ──
db_path = os.path.join(ROOT, "test_ali_auto.db")
for suffix in ("", "-wal", "-shm"):
    p = db_path + suffix
    if os.path.exists(p):
        os.remove(p)

db = Database(db_path)
db.initialize()
migrate(db)
print("\n[0] Database created and migrated")

# ── Mock app ──
class MockClock:
    def now(self):
        return datetime.now(timezone(timedelta(hours=10)))
    def monotonic(self):
        return time.monotonic()

class MockSettings:
    def get(self, key, default=None):
        if key == "intake.auto_accept":
            return False  # manual accept to test the flow
        if key == "intake.enabled":
            return True
        return default

class MockNotifier:
    def __init__(self):
        self.events = []
    def notify(self, *a, **kw):
        self.events.append((a, kw))

class MockApp:
    def __init__(self):
        self.db = db
        self.clock = MockClock()
        self.settings = MockSettings()
        self.notifier = MockNotifier()
        self.inbox = None

app = MockApp()
app.inbox = Inbox(app)

with db.write() as conn:
    ensure_source(conn, "ALIEXPRESS", "AliExpress")
print("    Inbox ready, source ALIEXPRESS ensured")

# ── 1. Collect ──
print("\n[1] Collecting product 1005013496905897...")
LISTING_ID = "1005013496905897"
URL = f"https://www.aliexpress.com/item/{LISTING_ID}.html"

observation = None
collect_method = ""

# Try real Playwright first (with timeout)
try:
    print("    Attempting Playwright collection...")
    col = AliExpressCollector(use_proxies=False)
    t0 = time.monotonic()
    obs_raw = col.collect_product(LISTING_ID, URL)
    elapsed = time.monotonic() - t0
    col.close()
    if obs_raw and obs_raw.get("observation", {}).get("status") == "PARTIAL_SUCCESS":
        observation = obs_raw
        collect_method = f"Playwright ({elapsed:.1f}s)"
        print(f"    Playwright OK in {elapsed:.1f}s")
    else:
        print(f"    Playwright returned: {obs_raw.get('observation',{}).get('status') if obs_raw else 'None'}")
except Exception as e:
    print(f"    Playwright failed: {e}")

# Fallback: static MTOP file
if observation is None:
    print("    Falling back to static MTOP file...")
    mtop_file = os.path.join(ROOT, "mtop_pw62_pdp_905897.json")
    if not os.path.exists(mtop_file):
        print("    FATAL: no MTOP file found")
        sys.exit(1)
    
    col = AliExpressCollector.__new__(AliExpressCollector)
    col.source = "ALIEXPRESS"
    col.stats = {"requests": 0, "success": 0, "failed": 0, "blocked": 0}
    col.pool = type("P", (), {
        "mark_success": lambda s, l: None,
        "mark_failure": lambda s: None,
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
    body = open(mtop_file, "r", encoding="utf-8").read()
    ok = col._parse_mtop(body, obs)
    print(f"    MTOP parse: ok={ok}")
    
    col._ensure_evidence(obs)
    col._ensure_price_amounts(obs)
    col._ensure_availability_in_fields(obs)
    
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
    observation = {
        "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
        "capture_action": "COLLECTOR_RUN",
        "fully_autonomous": True,
        "collector_source": "ALIEXPRESS",
        "observation": {
            **obs,
            "source": "ALIEXPRESS",
            "id": LISTING_ID,
            "url": URL,
            "observed_at": now,
            "status": "PARTIAL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }
    collect_method = "static MTOP file"

print(f"    Method: {collect_method}")
print(f"    Format: {observation['format']}")
print(f"    capture_action: {observation['capture_action']}")
print(f"    fully_autonomous: {observation['fully_autonomous']}")

# ── 2. Write JSON (simulating collector output) ──
print("\n[2] Writing JSON to intake_out...")
intake_dir = Path(os.path.join(ROOT, "intake_out"))
intake_dir.mkdir(exist_ok=True)
json_file = intake_dir / f"jarvis-eye-aliexpress-autotest-{LISTING_ID[:8]}.json"
json_file.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"    File: {json_file.name} ({json_file.stat().st_size} bytes)")

# ── 3. Inbox.stage_bytes (simulating FolderWatcher) ──
print("\n[3] Inbox.stage_bytes (as FolderWatcher would)...")
data = json_file.read_bytes()
item, created = app.inbox.stage_bytes(data, filename=json_file.name, origin="FOLDER")
print(f"    created={created}")
print(f"    status={item['status']}")
print(f"    error_code={item['error_code']}")
print(f"    preview title: {(item.get('preview') or {}).get('title', '')[:60]}")

check("stage_bytes created", created, "not created")
check("stage_bytes PENDING", item["status"] == "PENDING", f"status={item['status']}")
check("no error_code", item["error_code"] is None, f"error={item['error_code']}")

if item["status"] != "PENDING":
    print(f"\n  ABORT: item not PENDING, cannot continue")
    print(f"  Error: {item['error_code']}")
    sys.exit(1)

item_id = item["id"]

# ── 4. Inbox.accept ──
print("\n[4] Inbox.accept...")
result = app.inbox.accept(item_id)
print(f"    status={result['status']}")
print(f"    product_id={result.get('product_id')}")
print(f"    observation_created={result.get('observation_created')}")
print(f"    events={result.get('events')}")

check("accept → ACCEPTED", result["status"] == "ACCEPTED", f"status={result['status']}")
check("product_id set", result.get("product_id") is not None, "no product_id")
check("observation created", result.get("observation_created") is True, "not created")

product_id = result["product_id"]

# ── 5. Read back ──
print("\n[5] Read-back from SQLite...")
with db.read() as conn:
    row = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()
    print(f"    title: {row['title'][:80]}")
    print(f"    url: {row['url']}")
    print(f"    brand: {row['brand']}")
    print(f"    model: {row['model']}")
    print(f"    origin: {row['origin']}")
    print(f"    last_currency: {row['last_currency']}")
    print(f"    last_regular_minor: {row['last_regular_minor']}")
    print(f"    last_availability: {row['last_availability']}")
    print(f"    observation_count: {row['observation_count']}")
    
    check("title non-empty", bool(row["title"] and len(row["title"]) > 10), f"title={row['title']}")
    check("origin=COLLECTOR", row["origin"] == "COLLECTOR", f"origin={row['origin']}")
    check("last_currency set", row["last_currency"] is not None, "no currency")
    check("last_regular_minor > 0", (row["last_regular_minor"] or 0) > 0, f"price={row['last_regular_minor']}")
    check("availability known", row["last_availability"] not in (None, "UNKNOWN"), f"avail={row['last_availability']}")
    
    # Gallery
    gallery = json.loads(row["gallery_json"] or "[]")
    print(f"    gallery images: {len(gallery)}")
    for g in gallery[:3]:
        print(f"      {g.get('source_url','')[:70]}")
    check("gallery has images", len(gallery) >= 3, f"only {len(gallery)}")
    
    # Price points
    pp = conn.execute("SELECT * FROM price_point WHERE product_id=?", (product_id,)).fetchall()
    print(f"    price_points: {len(pp)}")
    for p in pp:
        print(f"      {p['role']}: {p['amount_minor']} {p['currency']}")
    check("price_points >= 1", len(pp) >= 1, "no prices")
    
    # Normalized observation
    obs_rows = conn.execute("SELECT * FROM observation WHERE product_id=?", (product_id,)).fetchall()
    obs_id = obs_rows[0]["id"] if obs_rows else None
    if obs_id:
        norm = load_normalized(conn, obs_id)
        comp = norm["completeness"]
        print(f"    normalized completeness: {comp['known']}/{comp['total']}")
        print(f"    normalized prices: {len(norm['prices'])}")
        print(f"    normalized characteristics: {len(norm['characteristics'])}")
        print(f"    normalized media: {len((norm.get('media') or {}).get('items', []))}")
        check("completeness >= 5", comp["known"] >= 5, f"only {comp['known']}")
        check("characteristics >= 5", len(norm["characteristics"]) >= 5, f"only {len(norm['characteristics'])}")
        check("media items >= 3", len((norm.get('media') or {}).get('items', [])) >= 3, "too few media")
        
        # SKU / variants
        sku_data = norm.get("fields", {}).get("sku_variants")
        if sku_data:
            sv = sku_data.get("value") if isinstance(sku_data, dict) else sku_data
            print(f"    sku_variants: {type(sv).__name__} len={len(sv) if isinstance(sv, (list,dict)) else '?'}")
    
    # Duplicate check
    dupes = conn.execute(
        "SELECT count(*) as c FROM product WHERE source='ALIEXPRESS' AND listing_id=?",
        (LISTING_ID,)
    ).fetchone()["c"]
    print(f"    duplicate check: {dupes} product rows")
    check("no duplicates", dupes == 1, f"{dupes} rows")

# ── 6. Re-insert (fingerprint dedup) ──
print("\n[6] Re-insert same observation (fingerprint dedup)...")
with db.write() as conn:
    obs_rows = conn.execute("SELECT * FROM observation WHERE product_id=?", (product_id,)).fetchall()
    if obs_rows:
        obs_id = obs_rows[0]["id"]
        norm = load_normalized(conn, obs_id)
        raw = json.loads(obs_rows[0]["raw_json"]) if obs_rows[0]["raw_json"] else {}
        
        # Get fingerprint from the original
        fp = fingerprint(EYE_SCOPE, raw)
        obs_id2, created2 = __import__("r52.store.products", fromlist=["add_observation"]).add_observation(
            conn, product_id=product_id, normalized=norm, raw=raw,
            origin="COLLECTOR", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
            fingerprint=fp, observed_at=norm["observed_at"],
            availability=norm["availability"], prices=norm["prices"],
            now=datetime.now(timezone(timedelta(hours=10))).isoformat()
        )
        print(f"    re-insert: id={obs_id2} created={created2}")
        check("fingerprint dedup", created2 is False, f"created={created2}")

# ── 7. Summary ──
print(f"\n{'=' * 70}")
print(f"COLLECT METHOD: {collect_method}")
print(f"PASS: {PASS}  FAIL: {FAIL}")
if ERRORS:
    print(f"ERRORS:")
    for e in ERRORS:
        print(f"  - {e}")
print(f"RESULT: {'ALL PASS' if FAIL == 0 else 'WARNINGS'}")
print(f"{'=' * 70}")
sys.exit(0 if FAIL == 0 else 1)
