r"""diag_fp3.py — Farpost: catch exact network error after warm-up.
Run: C:\Python315\python.exe diag_fp3.py
"""
import sys, os, json, time, logging, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

fmt = logging.Formatter("%(asctime)s %(name)-18s %(levelname)-5s %(message)s")
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
log = logging.getLogger("FP3")
log.setLevel(logging.DEBUG)
log.addHandler(sh)
for n in ("r52.collectors", "r52.collectors.farpost", "r52.collectors.base", "urllib3"):
    lg = logging.getLogger(n)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(sh)

LID = "16879823067"
URL = f"https://www.farpost.ru/vladivostok/{LID}.html"
print(f"=== FP3: {LID}  URL={URL} ===")

import requests
from r52.collectors.base import BASE_HEADERS, UA_LIST, CONNECT_TIMEOUT, READ_TIMEOUT
import random

# --- Phase 1: manual session, no BaseCollector ---
print("\n--- Phase 1: manual requests.Session ---")
s = requests.Session()
s.headers.update(BASE_HEADERS)
s.headers["User-Agent"] = random.choice(UA_LIST)

# 1a: warm-up
t0 = time.time()
try:
    r0 = s.get("https://www.farpost.ru/", timeout=(10, 30), allow_redirects=True)
    print(f"  warmup: {time.time()-t0:.1f}s  status={r0.status_code}  url={r0.url}")
    print(f"  cookies: {dict(s.cookies)}")
except Exception as e:
    print(f"  warmup EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

time.sleep(1)

# 1b: product page
t1 = time.time()
try:
    r1 = s.get(URL, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
    dt = time.time() - t1
    print(f"  product: {dt:.1f}s  status={r1.status_code}  final_url={r1.url}")
    print(f"  headers CT: {r1.headers.get('Content-Type','?')}")
    print(f"  content-length: {len(r1.content)}")
    print(f"  redirect chain: {[x.status_code for x in r1.history]}")
    for h in r1.history:
        print(f"    {h.status_code} {h.url}")
    ct = (r1.headers.get("Content-Type") or "").lower()
    if "1251" in ct or "cp1251" in ct:
        html = r1.content.decode("windows-1251", errors="replace")
    else:
        html = r1.text
    print(f"  html len: {len(html)}")
    print(f"  html[:300]: {html[:300]}")
    has_ld = "application/ld+json" in html
    has_price = "data-bulletin-price" in html
    print(f"  jsonld={has_ld}  price_attr={has_price}")
    if r1.status_code == 200 and len(html) > 500:
        print("  => PHASE1 OK")
    else:
        print("  => PHASE1 ISSUE")
except Exception as e:
    dt = time.time() - t1
    print(f"  product EXCEPTION ({dt:.1f}s): {type(e).__name__}: {e}")
    traceback.print_exc()
    print("  => PHASE1 FAIL")

# --- Phase 2: curl_cffi (bypass TLS fingerprint) ---
print("\n--- Phase 2: curl_cffi ---")
try:
    from curl_cffi import requests as cr
    t2 = time.time()
    # warm-up with curl_cffi
    cs = cr.Session(impersonate="chrome131")
    cr0 = cs.get("https://www.farpost.ru/", timeout=(10, 30))
    print(f"  warmup: {time.time()-t2:.1f}s  status={cr0.status_code}")
    time.sleep(1)
    t3 = time.time()
    cr1 = cs.get(URL, timeout=(10, 30))
    print(f"  product: {time.time()-t3:.1f}s  status={cr1.status_code}  url={cr1.url}")
    print(f"  len: {len(cr1.content)}")
    ct2 = (cr1.headers.get("Content-Type") or "").lower()
    if "1251" in ct2 or "cp1251" in ct2:
        html2 = cr1.content.decode("windows-1251", errors="replace")
    else:
        html2 = cr1.text
    print(f"  html len: {len(html2)}")
    print(f"  html[:200]: {html2[:200]}")
    has_ld2 = "application/ld+json" in html2
    has_price2 = "data-bulletin-price" in html2
    print(f"  jsonld={has_ld2}  price_attr={has_price2}")
    if cr1.status_code == 200 and len(html2) > 500:
        print("  => PHASE2 OK")
    else:
        print("  => PHASE2 ISSUE")
except ImportError:
    print("  curl_cffi not installed, skipping")
except Exception as e:
    print(f"  curl_cffi EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()

# --- Phase 3: BaseCollector with verbose ---
print("\n--- Phase 3: BaseCollector.fetch() ---")
from r52.collectors.farpost import FarpostCollector
col = FarpostCollector(data_dir=None, use_proxies=False)
print(f"  session UA: {col.session.headers.get('User-Agent','?')[:50]}")
print(f"  pool.use_proxies: {col.pool._use_proxies}")
print(f"  pool.entries: {len(col.pool._entries)}")

t4 = time.time()
# Manual: call _warmup then super().fetch
col._warmup()
print(f"  warmup done: {time.time()-t4:.1f}s")

t5 = time.time()
resp = col.fetch(URL)
dt5 = time.time() - t5
print(f"  fetch: {dt5:.1f}s  resp={'None' if resp is None else f'status={resp.status_code} url={resp.url}'}")
if resp is None:
    print(f"  stats: {col.stats}")
    # Try direct session.get to see what's happening
    t6 = time.time()
    try:
        r6 = col.session.get(URL, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
        print(f"  direct session.get: {time.time()-t6:.1f}s  status={r6.status_code}  url={r6.url}")
        print(f"  history: {[(h.status_code, h.url) for h in r6.history]}")
        print(f"  len: {len(r6.content)}")
    except Exception as e:
        print(f"  direct session.get EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
else:
    print(f"  content-len: {len(resp.content)}")
    ct3 = (resp.headers.get("Content-Type") or "").lower()
    if "1251" in ct3:
        html3 = resp.content.decode("windows-1251", errors="replace")
    else:
        html3 = resp.text
    print(f"  html len: {len(html3)}")
    # Try parse
    obs = col.parse_page(resp, LID, URL, "test")
    print(f"  parse_page: {'OK' if obs else 'None'}")
    if obs:
        prices = obs.get("price_candidates", [])
        title = obs.get("fields", {}).get("title", {}).get("value", "")
        print(f"  title: {title[:60]}")
        print(f"  prices: {len(prices)}")
        for p in prices[:5]:
            print(f"    {p.get('role')} {p.get('amount_minor',0)} {p.get('currency')}")

if hasattr(col, "close"):
    col.close()
print(f"\nTOTAL: {time.time()-t0:.1f}s")
