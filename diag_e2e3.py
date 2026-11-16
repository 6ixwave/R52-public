"""E2E v3: in-process server + collectors + FolderWatcher + API query."""
import sys, os, json, time, threading, traceback, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

HOME = Path.home() / ".local" / "share" / "r52"
PORT = 8422
BASE = f"http://127.0.0.1:{PORT}"
INTAKE_DIR = HOME / "intake_test"

print("=" * 60)
print("E2E v3: R-52 Full Pipeline (in-process)")
print("=" * 60)

# --- 1. Config ---
print("\n[1] Writing config...")
cfg_file = HOME / "config.toml"
cfg_file.parent.mkdir(parents=True, exist_ok=True)
intake_fwd = str(INTAKE_DIR).replace("\\", "/")
toml_text = (
    f'[api]\nport = {PORT}\n\n[logging]\nlevel = "DEBUG"\n\n'
    f'[display]\ntimezone = "Asia/Vladivostok"\n\n'
    f'[intake]\nfolders = ["{intake_fwd}"]\n'
)
cfg_file.write_text(toml_text, encoding="utf-8")
print(f"  config: port={PORT}, intake={intake_fwd}")

# Clean intake
INTAKE_DIR.mkdir(parents=True, exist_ok=True)
for f in INTAKE_DIR.glob("jarvis-eye*.json"):
    f.unlink()
print(f"  intake cleaned")

# Remove stale lock
lock_file = HOME / "run" / "core.lock"
if lock_file.exists():
    lock_file.unlink()
    print("  removed stale lock")

# --- 2. Start server ---
print("\n[2] Starting server...")
from r52.config import load_config
from r52.service import App
from r52.log import setup_logging

config = load_config(str(HOME))
setup_logging(config.paths.logs_dir, "DEBUG", console=True)
app = App(config)
app.start(port=PORT)
print(f"  Server started: port={app.port}, running={app._running}")

# Quick health check
time.sleep(1)
health = app.health()
print(f"  Health: {health}")
if not health["ok"]:
    print("  FATAL: server not healthy, aborting")
    app.stop()
    sys.exit(1)

# --- 3. Collect ---
print("\n[3] Collecting products...")
results = {}

# FARPOST
try:
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(use_proxies=False)
    fp_doc = fp.collect_product("116361427", "https://www.farpost.ru/vladivostok/116361427.html")
    fp.close()
    if fp_doc:
        obs = fp_doc.get("observation", {})
        results["farpost"] = fp_doc
        print(f"  FARPOST: status={obs.get('status')}, title={str(obs.get('title','?'))[:60]}")
    else:
        print("  FARPOST: returned None")
except Exception as e:
    print(f"  FARPOST ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()

# WB
try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)
    wb_doc = wb.collect_product("199298871", "https://www.wildberries.ru/catalog/199298871/detail.aspx")
    wb.close()
    if wb_doc:
        obs = wb_doc.get("observation", {})
        results["wb"] = wb_doc
        print(f"  WB: status={obs.get('status')}, title={str(obs.get('title','?'))[:60]}")
    else:
        print("  WB: returned None")
except Exception as e:
    print(f"  WB ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()

# --- 4. Write JSON to intake ---
print("\n[4] Writing JSON to intake...")
written = []
for name, doc in results.items():
    fname = f"jarvis-eye-{name}-e2e3.json"
    fpath = INTAKE_DIR / fname
    fpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(fname)
    print(f"  {fname} ({fpath.stat().st_size}B)")

if not written:
    print("  WARNING: no files to write, skipping watcher test")

# --- 5. Wait for FolderWatcher ---
print("\n[5] Waiting 8s for FolderWatcher...")
time.sleep(8)

# --- 6. Query API ---
print("\n[6] Querying API...")

def api_get(path):
    try:
        resp = urllib.request.urlopen(f"{BASE}{path}", timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        print(f"  GET {path} failed: {e}")
        return None

# Status
status = api_get("/api/status")
if status:
    db = status.get("database", {})
    print(f"  DB: products={db.get('products')}, observations={db.get('observations')}, prices={db.get('price_points')}")
    intake_info = status.get("intake", {})
    print(f"  Intake: enabled={intake_info.get('enabled')}, last_error={intake_info.get('last_error')}")

# Inbox
inbox = api_get("/api/inbox")
if inbox is not None:
    items = inbox if isinstance(inbox, list) else inbox.get("items", [])
    print(f"\n  Inbox: {len(items)} items")
    for item in items[:10]:
        fname = item.get("filename", "?")
        st = item.get("status", "?")
        pv = item.get("preview") or {}
        print(f"    [{st}] {fname} | title={str(pv.get('title','?'))[:50]} | price={pv.get('price_text','?')}")
else:
    items = []
    print("  Inbox: fetch failed")

# Products
products = api_get("/api/products")
if products is not None:
    plist = products if isinstance(products, list) else products.get("items", [])
    print(f"\n  Products: {len(plist)} total")
    for p in plist[:10]:
        print(f"    id={p.get('id')} src={p.get('source')} listing={p.get('listing_id')} "
              f"title={str(p.get('title','?'))[:50]}")
else:
    plist = []
    print("  Products: fetch failed")

# --- 7. Summary ---
print("\n" + "=" * 60)
print("E2E v3 SUMMARY")
print("=" * 60)

fp_collected = "farpost" in results
wb_collected = "wb" in results
fp_in_inbox = any("farpost" in (i.get("filename") or "").lower() for i in items)
wb_in_inbox = any("wb" in (i.get("filename") or "").lower() for i in items)
fp_in_products = any(p.get("source") == "FARPOST" for p in plist)
wb_in_products = any(p.get("source") == "WB" for p in plist)

print(f"  FARPOST collected:    {'Y' if fp_collected else 'N'}")
print(f"  WB collected:         {'Y' if wb_collected else 'N'}")
print(f"  FARPOST in inbox:     {'Y' if fp_in_inbox else 'N'}")
print(f"  WB in inbox:          {'Y' if wb_in_inbox else 'N'}")
print(f"  FARPOST in products:  {'Y' if fp_in_products else 'N'}")
print(f"  WB in products:       {'Y' if wb_in_products else 'N'}")
print("=" * 60)

# --- 8. Stop ---
print("\n[8] Stopping server...")
app.stop()
print("DONE")
