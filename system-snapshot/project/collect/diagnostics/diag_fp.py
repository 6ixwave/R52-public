r"""diag_fp.py — Farpost single-listing diagnostic.
Run: C:\Python315\python.exe diag_fp.py
"""
import sys, os, json, time, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

fmt = logging.Formatter("%(asctime)s %(name)-18s %(levelname)-5s %(message)s")
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
log = logging.getLogger("FP")
log.setLevel(logging.DEBUG)
log.addHandler(sh)
for n in ("r52.collectors", "r52.collectors.farpost", "r52.collectors.base"):
    lg = logging.getLogger(n)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(sh)

print("=== FARPOST: 16879823067 ===")
t0 = time.time()

from r52.collectors.farpost import FarpostCollector
col = FarpostCollector(data_dir=None, use_proxies=False)
lid = "16879823067"
url = col.build_url(lid)
print(f"URL: {url}")

t1 = time.time()
result = col.collect_product(lid, url, "Datchik kisloroda LYNXauto")
dt = time.time() - t1
print(f"collect: {dt:.1f}s result={'OK' if result else 'None'}")

if result:
    obs = result.get("observation", {})
    prices = obs.get("price_candidates", [])
    print(f"prices={len(prices)} status={obs.get('status')}")
    for p in prices[:5]:
        print(f"  {p.get('role')} {p.get('amount_minor',0)} {p.get('currency')} '{p.get('label','')}'")
    title = obs.get("fields", {}).get("title", {}).get("value", "")
    print(f"title: {title[:60]}")
    with open(ROOT / "fp_diag_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print("Saved fp_diag_result.json")
else:
    print("RESULT: None — collector failed")

if hasattr(col, "close"):
    col.close()
print(f"TOTAL: {time.time()-t0:.1f}s")
