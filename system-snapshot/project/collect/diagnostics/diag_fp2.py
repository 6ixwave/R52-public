r"""diag_fp2.py — Farpost deep diagnostic: dump raw HTTP before parse_page.
Run: C:\Python315\python.exe diag_fp2.py
"""
import sys, os, json, time, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

fmt = logging.Formatter("%(asctime)s %(name)-18s %(levelname)-5s %(message)s")
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
log = logging.getLogger("FP2")
log.setLevel(logging.DEBUG)
log.addHandler(sh)
for n in ("r52.collectors", "r52.collectors.farpost", "r52.collectors.base"):
    lg = logging.getLogger(n)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(sh)

LID = "16879823067"
print(f"=== FARPOST deep: {LID} ===")
t0 = time.time()

from r52.collectors.farpost import FarpostCollector
col = FarpostCollector(data_dir=None, use_proxies=False)
url = col.build_url(LID)
print(f"URL: {url}")

# Step 1: warm-up + raw fetch
t1 = time.time()
col._warmup()
print(f"warmup: {time.time()-t1:.1f}s")

t2 = time.time()
resp = col.fetch(url)
dt_fetch = time.time() - t2
print(f"fetch: {dt_fetch:.1f}s")

if resp is None:
    print("FETCH: None (network error or timeout)")
    if hasattr(col, "close"): col.close()
    sys.exit(1)

print(f"  status: {resp.status_code}")
print(f"  final_url: {resp.url}")
print(f"  content-length: {len(resp.content)}")
print(f"  headers CT: {resp.headers.get('Content-Type','?')}")

# Decode like parse_page does
ct = (resp.headers.get("Content-Type") or "").lower()
if "windows-1251" in ct or "cp1251" in ct or "1251" in ct:
    html = resp.content.decode("windows-1251", errors="replace")
    print("  decoded as windows-1251")
else:
    html = resp.text
    print("  decoded as utf-8/resp.text")

print(f"  html len: {len(html)}")
print(f"  html[:300]: {html[:300]}")
print(f"  html[-200:]: {html[-200:]}")

# Check for common failure patterns
checks = {
    "captcha": "captcha" in html.lower() or "recaptcha" in html.lower(),
    "verify": "/verify" in html,
    "404_page": "не существует" in html or "not found" in html.lower(),
    "redirect_page": "redirect" in html[:500].lower(),
    "jsonld": "application/ld+json" in html,
    "price_in_html": "6 099" in html or "6099" in html or "6&#8381;099" in html,
    "title_tag": "<title>" in html,
}
print(f"  checks: {json.dumps(checks, ensure_ascii=False)}")

# Save raw HTML snippet for inspection
with open(ROOT / "fp2_raw_snippet.txt", "w", encoding="utf-8") as f:
    f.write(f"URL: {url}\n")
    f.write(f"Status: {resp.status_code}\n")
    f.write(f"Final URL: {resp.url}\n")
    f.write(f"CT: {resp.headers.get('Content-Type','?')}\n")
    f.write(f"Len: {len(html)}\n")
    f.write(f"Checks: {json.dumps(checks, ensure_ascii=False)}\n\n")
    f.write("=== FIRST 2000 ===\n")
    f.write(html[:2000])
    f.write("\n\n=== LAST 500 ===\n")
    f.write(html[-500:])
print("Saved fp2_raw_snippet.txt")

# Step 2: try parse_page manually
t3 = time.time()
observation = col.parse_page(resp, LID, url, "Datchik kisloroda LYNXauto")
dt_parse = time.time() - t3
print(f"parse_page: {dt_parse:.1f}s -> {'OK' if observation else 'None'}")

if observation:
    prices = observation.get("price_candidates", [])
    title = observation.get("fields", {}).get("title", {}).get("value", "")
    print(f"  title: {title[:60]}")
    print(f"  prices: {len(prices)}")
    for p in prices[:5]:
        print(f"    {p.get('role')} {p.get('amount_minor',0)} {p.get('currency')} '{p.get('label','')}'")
    
    # Full collect for comparison
    t4 = time.time()
    result = col.collect_product(LID, url, "Datchik kisloroda LYNXauto")
    dt_full = time.time() - t4
    print(f"collect_product: {dt_full:.1f}s -> {'OK' if result else 'None'}")
    if result:
        with open(ROOT / "fp2_result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print("Saved fp2_result.json")
else:
    print("parse_page returned None — need to check parse logic")

if hasattr(col, "close"):
    col.close()
print(f"TOTAL: {time.time()-t0:.1f}s")
