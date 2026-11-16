"""E2E v2: server (cli serve) → intake folder → FolderWatcher → DB → API."""
import json, time, logging, sys, os, subprocess, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e2")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PORT = 8420
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path.home() / ".local" / "share" / "r52"
INTAKE_DIR = HOME / "intake_test"

print("=" * 60)
print("E2E v2: R-52 Full Pipeline")
print("=" * 60)

# --- 0. Prepare ---
print("\n[0] Preparing...")
INTAKE_DIR.mkdir(parents=True, exist_ok=True)
for f in INTAKE_DIR.glob("jarvis-eye*.json"):
    f.unlink()

# Write config.toml with our intake folder
config_file = HOME / "config.toml"
config_content = f'''[api]
port = {PORT}

[logging]
level = "INFO"

[display]
timezone = "Asia/Vladivostok"

[intake]
folders = ["{INTAKE_DIR}"]
'''
config_file.write_text(config_content, encoding="utf-8")
print(f"  ✓ config.toml: port={PORT}, intake={INTAKE_DIR}")

# --- 1. Collect (reuse fast path) ---
print("\n[1] Collecting...")
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
print("\n[2] Starting server...")
env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT)
server_proc = subprocess.Popen(
    [sys.executable, "-m", "r52.cli", "serve", "--port", str(PORT), "--home", str(HOME)],
    cwd=str(ROOT),
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

# Wait for health (up to 15s)
healthy = False
for i in range(30):
    time.sleep(0.5)
    try:
        resp = urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
        health = json.loads(resp.read())
        healthy = True
        print(f"  ✓ Health after {(i+1)*0.5:.1f}s: {health}")
        break
    except Exception:
        pass

if not healthy:
    # Check if process died
    if server_proc.poll() is not None:
        stderr = server_proc.stderr.read().decode()[:800]
        print(f"  ✗ Server died (code={server_proc.returncode})")
        print(f"  stderr: {stderr}")
    else:
        print(f"  ✗ Server not responding after 15s")
    server_proc.terminate()
    sys.exit(1)

# --- 3. Write JSON to intake ---
print("\n[3] Writing files to intake...")
fp_file = INTAKE_DIR / "jarvis-eye-farpost-e2e.json"
fp_file.write_text(json.dumps(fp_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✓ {fp_file.name} ({fp_file.stat().st_size}B)")

wb_file = INTAKE_DIR / "jarvis-eye-wb-e2e.json"
wb_file.write_text(json.dumps(wb_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✓ {wb_file.name} ({wb_file.stat().st_size}B)")

# --- 4. Wait for FolderWatcher (2 ticks × 2s + 2s buffer = 6s) ---
print("\n[4] Waiting for FolderWatcher (7s)...")
time.sleep(7)

# --- 5. Query API ---
print("\n[5] Querying API...")

def api_get(path):
    try:
        resp = urllib.request.urlopen(f"{BASE}{path}", timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        print(f"  ✗ GET {path}: {e}")
        return None

# Inbox
inbox = api_get("/api/inbox")
if inbox is not None:
    items = inbox if isinstance(inbox, list) else inbox.get("items", [])
    print(f"  Inbox: {len(items)} items")
    for item in items[:10]:
        print(f"    id={item.get('id')} status={item.get('status')} "
              f"origin={item.get('origin')} file={item.get('filename')}")
        pv = item.get("preview")
        if pv:
            print(f"      title={str(pv.get('title','?'))[:50]}")
            print(f"      price={pv.get('price_text','?')}")
else:
    items = []

# Products
products = api_get("/api/products")
if products is not None:
    plist = products if isinstance(products, list) else products.get("items", [])
    print(f"\n  Products: {len(plist)} total")
    for p in plist[:10]:
        print(f"    id={p.get('id')} source={p.get('source')} "
              f"listing={p.get('listing_id')} title={str(p.get('title','?'))[:50]}")
else:
    plist = []

# Status
status = api_get("/api/status")
if status:
    intake_info = status.get("intake", {})
    print(f"\n  Intake: enabled={intake_info.get('enabled')} folders={intake_info.get('folders')}")
    sources = status.get("sources", [])
    for s in sources:
        print(f"    {s.get('source')}: {s.get('collector')}")

# --- 6. Summary ---
print("\n" + "=" * 60)
print("E2E SUMMARY")
print("=" * 60)
fp_in_inbox = any("farpost" in (i.get("filename") or "").lower() for i in items)
wb_in_inbox = any("wb" in (i.get("filename") or "").lower() for i in items)
fp_in_products = any(p.get("source") == "FARPOST" for p in plist)
wb_in_products = any(p.get("source") == "WB" for p in plist)

print(f"  FARPOST in inbox:    {'✓' if fp_in_inbox else '✗'}")
print(f"  WB in inbox:         {'✓' if wb_in_inbox else '✗'}")
print(f"  FARPOST in products: {'✓' if fp_in_products else '✗'}")
print(f"  WB in products:      {'✓' if wb_in_products else '✗'}")
print("=" * 60)

# --- 7. Cleanup ---
print("\n[7] Stopping server...")
server_proc.terminate()
try:
    server_proc.wait(timeout=8)
except subprocess.TimeoutExpired:
    server_proc.kill()
print("  ✓ Server stopped")
print("\nDONE")
