"""E2E v4: in-process server, WB collect, FolderWatcher, API with token, accept, verify."""
import sys, os, json, time, threading, traceback, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

HOME = Path.home() / ".local" / "share" / "r52"
PORT = 8423
BASE = f"http://127.0.0.1:{PORT}"
INTAKE_DIR = HOME / "intake_test"

print("=" * 60)
print("E2E v4: R-52 Pipeline (WB + token API)")
print("=" * 60)

# --- 1. Config ---
print("\n[1] Config...")
cfg_file = HOME / "config.toml"
cfg_file.parent.mkdir(parents=True, exist_ok=True)
intake_fwd = str(INTAKE_DIR).replace("\\", "/")
toml_text = (
    f'[api]\nport = {PORT}\n\n[logging]\nlevel = "DEBUG"\n\n'
    f'[display]\ntimezone = "Asia/Vladivostok"\n\n'
    f'[intake]\nfolders = ["{intake_fwd}"]\n'
)
cfg_file.write_text(toml_text, encoding="utf-8")
INTAKE_DIR.mkdir(parents=True, exist_ok=True)
for f in INTAKE_DIR.glob("jarvis-eye*.json"):
    f.unlink()
lock_file = HOME / "run" / "core.lock"
if lock_file.exists():
    lock_file.unlink()
print(f"  port={PORT}, intake={intake_fwd}")

# --- 2. Start server ---
print("\n[2] Server...")
from r52.config import load_config
from r52.service import App
from r52.log import setup_logging

config = load_config(str(HOME))
setup_logging(config.paths.logs_dir, "DEBUG", console=True)
app = App(config)
app.start(port=PORT)
time.sleep(1)
health = app.health()
print(f"  port={app.port}, running={app._running}, health={health['ok']}")
TOKEN = app.token
print(f"  token={TOKEN[:16]}...")

# --- 3. API helper (with Bearer token) ---
def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read()) if e.headers.get("content-type","").startswith("application/json") else {}
        print(f"  API {method} {path} -> {e.code}: {err}")
        return None
    except Exception as e:
        print(f"  API {method} {path} -> ERR: {e}")
        return None

# --- 4. Collect WB ---
print("\n[3] Collecting WB...")
wb_doc = None
try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(use_proxies=False)
    wb_doc = wb.collect_product("199298871", "https://www.wildberries.ru/catalog/199298871/detail.aspx")
    wb.close()
    if wb_doc:
        obs = wb_doc.get("observation", {})
        print(f"  WB: status={obs.get('status')}, title={str(obs.get('title','?'))[:60]}")
    else:
        print("  WB: returned None")
except Exception as e:
    print(f"  WB ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()

# --- 5. Write to intake ---
print("\n[4] Writing to intake...")
if wb_doc:
    fname = "jarvis-eye-wb-e2e4.json"
    fpath = INTAKE_DIR / fname
    fpath.write_text(json.dumps(wb_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {fname} ({fpath.stat().st_size}B)")
else:
    print("  SKIP: no WB doc")

# --- 6. Wait for FolderWatcher (2 ticks x 2s + buffer) ---
print("\n[5] Waiting 7s for FolderWatcher...")
time.sleep(7)

# --- 7. Query API with token ---
print("\n[6] API queries...")
status = api("GET", "/api/status")
if status:
    db = status.get("database", {})
    print(f"  DB: products={db.get('products')}, obs={db.get('observations')}, prices={db.get('price_points')}")

inbox = api("GET", "/api/inbox")
items = []
if inbox is not None:
    items = inbox if isinstance(inbox, list) else inbox.get("items", [])
    print(f"  Inbox: {len(items)} items")
    for item in items[:5]:
        pv = item.get("preview") or {}
        print(f"    id={item.get('id')} [{item.get('status')}] {item.get('filename')} "
              f"title={str(pv.get('title','?'))[:40]} price={pv.get('price_text','?')}")

# --- 8. Accept PENDING items ---
print("\n[7] Accepting inbox items...")
for item in items:
    if item.get("status") == "PENDING":
        result = api("POST", f"/api/inbox/{item['id']}/accept", {})
        if result:
            print(f"  Accepted id={item['id']}: product_id={result.get('product_id')}, "
                  f"obs_created={result.get('observation_created')}")
        else:
            print(f"  Accept id={item['id']} failed")

# --- 9. Verify products ---
print("\n[8] Products after accept...")
products = api("GET", "/api/products")
plist = []
if products is not None:
    plist = products if isinstance(products, list) else products.get("items", [])
    print(f"  Products: {len(plist)} total")
    for p in plist[:10]:
        print(f"    id={p.get('id')} src={p.get('source')} listing={p.get('listing_id')} "
              f"title={str(p.get('title','?'))[:50]}")

# --- 10. Summary ---
print("\n" + "=" * 60)
print("E2E v4 SUMMARY")
print("=" * 60)
wb_ok = wb_doc is not None
wb_inbox = any("wb" in (i.get("filename") or "").lower() for i in items)
wb_product = any(p.get("source") == "WB" for p in plist)
print(f"  WB collected:      {'Y' if wb_ok else 'N'}")
print(f"  WB in inbox:       {'Y' if wb_inbox else 'N'}")
print(f"  WB in products:    {'Y' if wb_product else 'N'}")
print(f"  API with token:    {'Y' if status else 'N'}")
print("=" * 60)

# --- 11. Stop ---
print("\n[9] Stopping...")
app.stop()
print("DONE")
