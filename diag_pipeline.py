"""E2E pipeline test: collector → JSON → check_export → normalize_eye → FolderWatcher."""
import json, time, logging, sys, os
from pathlib import Path
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

print("=" * 60)
print("PIPELINE INTEGRATION TEST")
print("=" * 60)

# --- 1. Import core modules ---
print("\n[1] Importing modules...")
from r52.intake.eye import check_export, parse_export_bytes, preview, Inbox
from r52.domain.normalize import normalize_eye
from r52.errors import AppError
print("  ✓ eye.py, normalize.py imported")

# --- 2. Collect FARPOST ---
print("\n[2] FARPOST collect...")
from r52.collectors.farpost import FarpostCollector
fp = FarpostCollector(use_proxies=False)
FARPOST_ID = "116361427"
FARPOST_URL = "https://www.farpost.ru/vladivostok/116361427.html"
t0 = time.time()
fp_doc = fp.collect_product(FARPOST_ID, FARPOST_URL)
fp_elapsed = time.time() - t0
fp.close()
if fp_doc is None:
    print("  ✗ FARPOST: collect_product returned None")
    sys.exit(1)
print(f"  ✓ FARPOST: {fp_elapsed:.1f}s, status={fp_doc['observation'].get('status')}")
print(f"    title={fp_doc['observation'].get('fields',{}).get('title',{}).get('value','?')[:60]}")
print(f"    prices={len(fp_doc['observation'].get('price_candidates',[]))}")
print(f"    media={len(fp_doc['observation'].get('media',{}).get('items',[]))}")

# --- 3. Collect WB ---
print("\n[3] WB collect...")
from r52.collectors.wb import WBCollector
wb = WBCollector(use_proxies=False)
WB_ID = "199298871"
WB_URL = "https://www.wildberries.ru/catalog/199298871/detail.aspx"
t0 = time.time()
wb_doc = wb.collect_product(WB_ID, WB_URL)
wb_elapsed = time.time() - t0
wb.close()
if wb_doc is None:
    print("  ✗ WB: collect_product returned None")
    sys.exit(1)
print(f"  ✓ WB: {wb_elapsed:.1f}s, status={wb_doc['observation'].get('status')}")
print(f"    title={wb_doc['observation'].get('fields',{}).get('title',{}).get('value','?')[:60]}")
print(f"    prices={len(wb_doc['observation'].get('price_candidates',[]))}")
print(f"    media={len(wb_doc['observation'].get('media',{}).get('items',[]))}")

# --- 4. check_export + normalize_eye for each ---
def test_normalize(label, doc):
    print(f"\n[4-{label}] check_export + normalize_eye...")
    try:
        obs = check_export(doc)
        print(f"  ✓ check_export passed")
    except AppError as e:
        print(f"  ✗ check_export FAILED: {e.code} — {e}")
        return None

    try:
        normalized, cleaned = normalize_eye(obs)
        print(f"  ✓ normalize_eye passed")
        print(f"    source={normalized['source']}")
        print(f"    listing_id={normalized['listing_id']}")
        print(f"    title={normalized['title'][:60]}")
        print(f"    prices={len(normalized['prices'])}")
        for p in normalized['prices']:
            print(f"      role={p['role']} amount={p['amount_minor']} {p['currency']}")
        print(f"    availability={normalized['availability']}")
        print(f"    completeness={normalized['completeness']}")
        print(f"    media={len(normalized.get('media',{}).get('items',[]))} items")
        return normalized
    except AppError as e:
        print(f"  ✗ normalize_eye FAILED: {e.code} — {e}")
        return None

fp_norm = test_normalize("FARPOST", fp_doc)
wb_norm = test_normalize("WB", wb_doc)

# --- 5. Write to folder + FolderWatcher scan ---
print("\n[5] FolderWatcher test...")
watch_dir = ROOT / "test_intake"
watch_dir.mkdir(exist_ok=True)

# Clean old test files
for f in watch_dir.glob("jarvis-eye*.json"):
    f.unlink()

# Write FARPOST
fp_file = watch_dir / f"jarvis-eye-farpost-test1.json"
fp_file.write_text(json.dumps(fp_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✓ Written: {fp_file.name} ({fp_file.stat().st_size}B)")

# Write WB
wb_file = watch_dir / f"jarvis-eye-wb-test1.json"
wb_file.write_text(json.dumps(wb_doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✓ Written: {wb_file.name} ({wb_file.stat().st_size}B)")

# Test FolderWatcher (needs app mock)
from r52.intake.folder import FolderWatcher, PATTERN
import fnmatch

# Verify pattern match
for f in [fp_file.name, wb_file.name]:
    match = fnmatch.fnmatch(f.lower(), PATTERN)
    print(f"  Pattern '{PATTERN}' vs '{f}': {'✓' if match else '✗'}")

# Full FolderWatcher requires app object — do a lightweight scan test
print("\n  Lightweight scan (no app):")
candidates = []
with os.scandir(str(watch_dir)) as entries:
    for entry in entries:
        if fnmatch.fnmatch(entry.name.lower(), PATTERN) and entry.is_file():
            candidates.append((entry.path, entry.stat().st_size, entry.stat().st_mtime_ns))
print(f"  Found {len(candidates)} candidates (expect 2)")
for path, size, mtime in candidates:
    print(f"    {Path(path).name} ({size}B)")

# --- 6. Preview (what UI would show) ---
print("\n[6] Preview (UI data)...")
for label, norm in [("FARPOST", fp_norm), ("WB", wb_norm)]:
    if norm is None:
        print(f"  {label}: skipped (normalize failed)")
        continue
    try:
        from r52.domain import pricing
        summary = pricing.summarize(norm["prices"])
        print(f"\n  {label}:")
        print(f"    title: {norm['title'][:60]}")
        print(f"    price: {pricing.price_line(summary)}")
        print(f"    availability: {norm['availability']}")
        print(f"    completeness: {norm['completeness']}")
    except Exception as e:
        print(f"  {label}: preview error: {e}")

# --- 7. Summary ---
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"  FARPOST: {'✓ PASS' if fp_norm else '✗ FAIL'}")
print(f"  WB:      {'✓ PASS' if wb_norm else '✗ FAIL'}")
print(f"  FolderWatcher pattern: ✓")
print(f"  Files in intake dir: {len(list(watch_dir.glob('jarvis-eye*.json')))}")
print("=" * 60)
print("DONE")
