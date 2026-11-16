"""Exact E2E replica for AliExpress RU: pick latest file, run full stage_bytes pipeline."""
import json, sys, traceback, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from r52.domain.normalize import normalize_eye, card_identity, canonical_source, HOSTS, ID_PATTERNS, _split, clean
from r52.domain.media import normalize_media
from r52.domain import pricing
from r52.intake.eye import Inbox, parse_export_bytes, check_export, preview
from r52.errors import AppError
from r52.config import load_config
from r52.service import App

# --- Step 1: pick the same file E2E would pick ---
output_dir = ROOT / "collect" / "output" / "ALIEXPRESS_RU"
json_files = list(output_dir.glob("jarvis-eye-*.json"))
print(f"Found {len(json_files)} JSON files in {output_dir}")

# Sort by mtime, pick latest (same as E2E)
json_files_sorted = sorted(json_files, key=lambda p: p.stat().st_mtime)
for f in json_files_sorted:
    mt = time.strftime("%H:%M:%S", time.localtime(f.stat().st_mtime))
    print(f"  {mt}  {f.name}")

json_file = json_files_sorted[-1]
print(f"\n>>> E2E picks: {json_file.name}")
print(f">>> mtime: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(json_file.stat().st_mtime))}")

data_bytes = json_file.read_bytes()
print(f">>> size: {len(data_bytes)} bytes")

# --- Step 2: parse and check ---
print("\n=== parse_export_bytes ===")
document = parse_export_bytes(data_bytes)
print(f"  format: {document.get('format')!r}")
print(f"  capture_action: {document.get('capture_action')!r}")
print(f"  fully_autonomous: {document.get('fully_autonomous')!r}")

print("\n=== check_export ===")
observation = check_export(document)
print(f"  observation keys: {list(observation.keys())}")
print(f"  source: {observation.get('source')!r}")
print(f"  id: {observation.get('id')!r}")
print(f"  url: {observation.get('url')!r}")

# --- Step 3: normalize_eye (the critical step) ---
print("\n=== normalize_eye ===")
try:
    normalized, cleaned = normalize_eye(observation)
    print(f"  SUCCESS")
    print(f"  source={normalized['source']}, listing_id={normalized['listing_id']}")
    print(f"  url={normalized['url']}")
    print(f"  title={normalized['title'][:80]!r}")
    print(f"  prices={len(normalized['prices'])}")
    print(f"  availability={normalized['availability']}")
except AppError as e:
    print(f"  AppError: code={e.code}, status={e.status}, detail={e.detail}")
    traceback.print_exc()
    print("\n=== normalize_eye FAILED — checking card_identity manually ===")
    obs = clean({k: v for k, v in observation.items() if k != "media"})
    source = canonical_source(obs.get("source"))
    listing_id = str(obs.get("id") or obs.get("listing_id") or "").strip()
    url = obs.get("url")
    print(f"  source={source!r}, in HOSTS={source in HOSTS}")
    print(f"  listing_id={listing_id!r}")
    print(f"  url={url!r}")
    split = _split(url)
    if split:
        parts, port = split
        host = (parts.hostname or "").lower()
        print(f"  scheme={parts.scheme!r}, host={host!r}, port={port!r}")
        print(f"  host in HOSTS: {host in HOSTS.get(source, set())}")
        if source in HOSTS:
            print(f"  HOSTS = {HOSTS[source]}")
        pat = ID_PATTERNS.get(source)
        if pat:
            match = pat.search(parts.path)
            print(f"  pattern={pat.pattern!r}, match={match}")
            if match:
                found = next((g for g in match.groups() if g), None)
                print(f"  found={found!r}, listing_id={listing_id!r}, equal={str(listing_id)==found}")
    else:
        print("  _split returned None")
    sys.exit(1)

# --- Step 4: preview ---
print("\n=== preview ===")
try:
    shown = preview(normalized)
    print(f"  SUCCESS: source={shown['source']}, title={shown['title'][:60]!r}")
except Exception as e:
    print(f"  {type(e).__name__}: {e}")
    traceback.print_exc()

# --- Step 5: full App + Inbox.stage_bytes (exact E2E replica) ---
print("\n=== Full App + Inbox.stage_bytes ===")
config = load_config(home=str(ROOT / "data" / "test_home"))
app = App(config, install_guard=False)
app.open()

try:
    inbox = Inbox(app)
    item, created = inbox.stage_bytes(data_bytes, filename=json_file.name, origin="COLLECTOR")
    print(f"  status={item['status']}, created={created}, id={item.get('id')}")
    print(f"  error_code={item.get('error_code')}")
    if item["status"] == "PENDING":
        print("  → would proceed to accept")
        try:
            accepted = inbox.accept(item["id"])
            print(f"  accept: product_id={accepted.get('product_id')}, obs_created={accepted.get('observation_created')}")
        except Exception as e:
            print(f"  accept FAIL: {type(e).__name__}: {e}")
            traceback.print_exc()
    elif item["status"] == "INVALID":
        print(f"  → INVALID at stage: {item.get('error_code')}")
        print(f"  (E2E would report accept FAIL with this error)")
    else:
        print(f"  → status={item['status']}")
finally:
    app.db.close()
    app.lock.release()
    app._opened = False

print("\n=== Done ===")
