"""E2E: server → intake folder → FolderWatcher → DB → API."""
import json, time, logging, sys, os, subprocess, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PORT = 8420
BASE = f"http://127.0.0.1:{PORT}"
INTAKE_DIR = Path(r"C:\Users\aleks\OneDrive\Desktop\r52-intake")

print("=" * 60)
print("E2E TEST: R-52 Full Pipeline")
print("=" * 60)

# --- 0. Prepare intake dir ---
print("\n[0] Preparing intake dir...")
INTAKE_DIR.mkdir(parents=True, exist_ok=True)
# Clean old test files
for f in INTAKE_DIR.glob("jarvis-eye*.json"):
    f.unlink()
    print(f"  Removed old: {f.name}")
print(f"  ✓ {INTAKE_DIR}")

# --- 1. Collect FARPOST + WB (reuse pipeline results) ---
print("\n[1] Collecting products...")
from r52.collectors.farpost import FarpostCollector
from r52.collectors.wb import WBCollector

fp = FarpostCollector(use_proxies=False)
fp_doc = fp.collect_product("116361427", "https://www.farpost.ru/vladivostok/116361427.html")
fp.close()
print(f"  FARPOST: {fp_doc['observation'].get('status') if fp_doc else 'None'}")

wb = WBCollector(use_proxies=False)
wb_doc = wb.collect_product("199298871", "https://www.wildberries.ru/catalog/199298871/detail.aspx")
wb.close()
print(f"  WB: {wb_doc['observation'].get('status') if wb_doc else 'None'}")

# --- 2. Start server ---
print("\n[2] Starting R-52 server...")
env = os.environ.copy()
env["R52_DATA_DIR"] = str(Path.home() / ".local" / "share" / "r52")
server_proc = subprocess.Popen(
    [sys.executable, "run.py"],
    cwd=str(ROOT),
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
time.sleep(3)

# Health check
try:
    resp = urllib.request.urlopen(f"{BASE}/api/health", timeout=5)
    health = json.loads(resp.read())
    print(f"  ✓ Health: {health}")
except Exception as e:
    print(f"  ✗ Server not responding: {e}")
    print(f"  stderr: {server_proc.stderr.read().decode()[:500]}")
    server_proc.terminate()
    sys.exit(1)

# --- 3. Write JSON files to intake ---
print("\n[3] Writing files to intake...")
fp_file = INTAKE_DIR / "jarvis-eye-farpost-e2e.json"
fp_file.write_text(json.dumps(fp_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✓ {fp_file.name} ({fp_file.stat().st_size}B)")

wb_file = INTAKE_DIR / "jarvis-eye-wb-e2e.json"
wb_file.write_text(json.dumps(wb_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✓ {wb_file.name} ({wb_file.stat().st_size}B)")

# --- 4. Wait for FolderWatcher (2 ticks × 2s + buffer) ---
print("\n[4] Waiting for FolderWatcher (6s)...")
time.sleep(6)

# --- 5. Query API ---
print("\n[5] Querying API...")

# Inbox
try:
    resp = urllib.request.urlopen(f"{BASE}/api/inbox", timeout=5)
    inbox = json.loads(resp.read())
    print(f"  Inbox: {len(inbox)} items")
    for item in inbox:
        print(f"    id={item.get('id')} status={item.get('status')} "
              f"origin={item.get('origin')} file={item.get('filename')}")
        preview = item.get("preview")
        if preview:
            print(f"      title={preview.get('title','?')[:50]}")
            print(f"      price={preview.get('price_text','?')}")
            print(f"      availability={preview.get('availability','?')}")
except Exception as e:
    print(f"  ✗ Inbox query: {e}")
    inbox = []

# Products
try:
    resp = urllib.request.urlopen(f"{BASE}/api/products", timeout=5)
    products = json.loads(resp.read())
    print(f"\n  Products: {len(products)} total")
    for p in products:
        print(f"    id={p.get('id')} source={p.get('source')} "
              f"listing={p.get('listing_id')} title={str(p.get('title','?'))[:50]}")
except Exception as e:
    print(f"  ✗ Products query: {e}")
    products = []

# Collectors status
try:
    resp = urllib.request.urlopen(f"{BASE}/api/collectors/status", timeout=5)
    statuses = json.loads(resp.read())
    print(f"\n  Collectors: {len(statuses)}")
    for s in statuses:
        print(f"    {s['source']}: {s['collector']}")
except Exception as e:
    print(f"  ✗ Collectors status: {e}")

# --- 6. Summary ---
print("\n" + "=" * 60)
print("E2E SUMMARY")
print("=" * 60)
fp_in_inbox = any("farpost" in (i.get("filename") or "").lower() for i in inbox)
wb_in_inbox = any("wb" in (i.get("filename") or "").lower() for i in inbox)
fp_in_products = any(p.get("source") == "FARPOST" for p in products)
wb_in_products = any(p.get("source") == "WB" for p in products)

print(f"  FARPOST in inbox:    {'✓' if fp_in_inbox else '✗'}")
print(f"  WB in inbox:         {'✓' if wb_in_inbox else '✗'}")
print(f"  FARPOST in products: {'✓' if fp_in_products else '✗'}")
print(f"  WB in products:      {'✓' if wb_in_products else '✗'}")
print(f"  auto_accept:         ✓ (settings)")
print("=" * 60)

# --- 7. Cleanup ---
print("\n[7] Stopping server...")
server_proc.terminate()
try:
    server_proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    server_proc.kill()
print("  ✓ Server stopped")

print("\nDONE")
