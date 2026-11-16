r"""diag_fp4.py — Farpost: try multiple URLs/IDs to find a live listing.
Run: C:\Python315\python.exe diag_fp4.py
"""
import sys, os, time, json
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
print(f"warmup: {r0.status_code}  cookies={list(s.cookies.keys())}")
time.sleep(1)

# Candidate URLs to try
URLS = [
    ("17260583280 /vladivostok/", "https://www.farpost.ru/vladivostok/17260583280.html"),
    ("16879823067 /vladivostok/", "https://www.farpost.ru/vladivostok/16879823067.html"),
    ("17260583280 no-city",       "https://www.farpost.ru/17260583280.html"),
    ("16879823067 no-city",       "https://www.farpost.ru/16879823067.html"),
    ("17260583280 /bulletin/",    "https://www.farpost.ru/bulletin/17260583280.html"),
    ("16879823067 /bulletin/",    "https://www.farpost.ru/bulletin/16879823067.html"),
    ("125103161 /vladivostok/",   "https://www.farpost.ru/vladivostok/125103161.html"),
]

results = []
for label, url in URLS:
    t0 = time.time()
    try:
        r = s.get(url, timeout=(8, 20), allow_redirects=True)
        dt = time.time() - t0
        ct = (r.headers.get("Content-Type") or "").lower()
        if "1251" in ct:
            html = r.content.decode("windows-1251", errors="replace")
        else:
            html = r.text
        has_ld = "application/ld+json" in html
        has_price = "data-bulletin-price" in html
        title_m = ""
        import re
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if tm:
            title_m = tm.group(1)[:60]
        ok = r.status_code == 200 and len(html) > 2000 and has_price
        print(f"  [{label}] {r.status_code} {dt:.1f}s len={len(html)} ld={has_ld} price={has_price} title='{title_m}'")
        results.append({"label": label, "url": url, "status": r.status_code, "ok": ok, "html_len": len(html)})
        if ok:
            # Save a snippet
            with open(ROOT / f"fp4_live_{label.split()[0]}.html", "w", encoding="utf-8") as f:
                f.write(html[:5000])
            print(f"    => SAVED fp4_live_{label.split()[0]}.html")
    except Exception as e:
        print(f"  [{label}] EXC {type(e).__name__}: {e}")
        results.append({"label": label, "url": url, "status": "EXC", "ok": False})
    time.sleep(2)

print(f"\n=== SUMMARY ===")
for r in results:
    mark = "✓" if r["ok"] else "✗"
    print(f"  {mark} {r['label']:30s} {r['status']}  len={r.get('html_len','?')}")
