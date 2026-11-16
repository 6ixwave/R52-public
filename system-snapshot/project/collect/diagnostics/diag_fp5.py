r"""diag_fp5.py — Find live Farpost product from category page.
Run: C:\Python315\python.exe diag_fp5.py
"""
import sys, os, time, json, re
from pathlib import Path
import requests, random

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from r52.collectors.base import BASE_HEADERS, UA_LIST

s = requests.Session()
s.headers.update(BASE_HEADERS)
s.headers["User-Agent"] = random.choice(UA_LIST)

# Warm-up
r0 = s.get("https://www.farpost.ru/", timeout=(10, 30))
print(f"warmup: {r0.status_code}")
time.sleep(1)

# Fetch category page
cat_url = "https://www.farpost.ru/vladivostok/125103161.html"
r1 = s.get(cat_url, timeout=(8, 30))
ct = (r1.headers.get("Content-Type") or "").lower()
if "1251" in ct:
    html = r1.content.decode("windows-1251", errors="replace")
else:
    html = r1.text
print(f"category: {r1.status_code} len={len(html)}")

# Extract product links: /vladivostok/{id}.html or /{city}/{cat}/{slug}-{id}.html
# Farpost product URLs typically: /vladivostok/123456789.html or with slug
link_re = re.compile(r'href="(/(?:vladivostok|khabarovsk|blagoveshchensk|khabarovsk|chita|irkutsk|uzhgod|komsomolsk-na-amure|artem|nakhodka)/[^"]*?/[^"]*?-(\d+)\.html)"')
links = link_re.findall(html)
print(f"found {len(links)} product links")

# Also try simpler pattern: any /{city}/...-{id}.html
link_re2 = re.compile(r'href="(/[^"]*?/[^"]*?-(\d+)\.html)"')
links2 = link_re2.findall(html)
print(f"found {len(links2)} product links (broad)")

# Deduplicate by ID, keep first
seen = set()
candidates = []
for path, lid in links2:
    if lid not in seen and len(lid) >= 6:
        seen.add(lid)
        candidates.append((lid, f"https://www.farpost.ru{path}"))
    if len(candidates) >= 10:
        break

print(f"candidates: {len(candidates)}")
for lid, url in candidates[:5]:
    print(f"  {lid}  {url}")

# Try top 3 candidates
results = []
for lid, url in candidates[:3]:
    time.sleep(2)
    t0 = time.time()
    try:
        r = s.get(url, timeout=(8, 25), allow_redirects=True)
        dt = time.time() - t0
        ct2 = (r.headers.get("Content-Type") or "").lower()
        if "1251" in ct2:
            h = r.content.decode("windows-1251", errors="replace")
        else:
            h = r.text
        has_ld = "application/ld+json" in h
        has_price = "data-bulletin-price" in h
        tm = re.search(r'<title[^>]*>([^<]+)</title>', h, re.I)
        title = tm.group(1)[:70] if tm else "?"
        ok = r.status_code == 200 and len(h) > 3000 and (has_ld or has_price)
        print(f"\n  [{lid}] {r.status_code} {dt:.1f}s len={len(h)} ld={has_ld} price={has_price}")
        print(f"    title: {title}")
        results.append({"id": lid, "url": url, "status": r.status_code, "ok": ok, "title": title})
        if ok:
            # Save snippet for later parsing
            with open(ROOT / f"fp5_live_{lid}.html", "w", encoding="utf-8") as f:
                f.write(h[:8000])
            print(f"    SAVED fp5_live_{lid}.html")
            # Extract JSON-LD
            ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', h, re.DOTALL)
            for i, lb in enumerate(ld_blocks[:3]):
                try:
                    data = json.loads(lb.strip())
                    print(f"    LD[{i}]: {json.dumps(data, ensure_ascii=False)[:200]}")
                except:
                    pass
            # Extract price
            price_matches = re.findall(r'data-bulletin-price=["\'](\d+)["\']', h)
            if price_matches:
                print(f"    prices: {price_matches[:5]}")
    except Exception as e:
        print(f"  [{lid}] EXC: {type(e).__name__}: {e}")
        results.append({"id": lid, "url": url, "status": "EXC", "ok": False})

print(f"\n=== SUMMARY ===")
for r in results:
    mark = "LIVE" if r["ok"] else "DEAD"
    print(f"  {mark} {r['id']}  {r['status']}  {r.get('title','')[:50]}")

# Save results
with open(ROOT / "fp5_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("Saved fp5_results.json")
