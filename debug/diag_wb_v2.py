"""WB v2: deep diagnostics — 498 body, cffi variants, mobile, cookies."""
import re, sys, time, json, logging, traceback
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.WARNING)

HOME = Path(__file__).resolve().parents[1] / "data" / "test_home"
from r52.collectors.wb import WBCollector

c = WBCollector(data_dir=HOME, use_proxies=False)
c._warmup()
time.sleep(1)

query = "тест"
proxies = c.pool.get_proxy()
UA_D = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
UA_M = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

def do_get(url, headers, timeout=10, impersonate="chrome"):
    if c._cffi:
        return c._cffi.get(url, impersonate=impersonate, timeout=(8, timeout), headers=headers, proxies=proxies)
    else:
        return c.session.get(url, headers=headers, proxies=proxies, timeout=(8, timeout))

results = []

# --- 1. JSON API with cffi chrome ---
print("=" * 60)
print("1. catalog.wb.ru v4 (cffi chrome)")
params = f"query={quote(query)}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
try:
    r = do_get(f"https://catalog.wb.ru/exactmatch/ru/common/v4/search?{params}",
               {"Accept": "application/json", "User-Agent": UA_D}, timeout=8)
    print(f"  Status={r.status_code} len={len(r.text)}")
    if r.status_code == 200:
        d = r.json()
        print(f"  Products: {len(d.get('products', []))}")
    else:
        print(f"  Body[:300]: {r.text[:300]}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- 2. HTML 498 body inspection ---
print("\n" + "=" * 60)
print("2. www.wildberries.ru HTML (cffi chrome) — inspect 498 body")
try:
    r = do_get(f"https://www.wildberries.ru/catalog/0/ru/?search={quote(query)}&page=1",
               {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ru-RU,ru;q=0.9",
                "User-Agent": UA_D, "Referer": "https://www.wildberries.ru/"}, timeout=12)
    print(f"  Status={r.status_code} len={len(r.text)}")
    print(f"  Body[:800]:")
    print(r.text[:800])
    if r.status_code == 200 and len(r.text) > 5000:
        ms = list(re.finditer(r'/catalog/(\d+)/detail\.aspx', r.text, re.I))
        print(f"  Product links: {len(ms)}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- 3. m.wildberries.ru ---
print("\n" + "=" * 60)
print("3. m.wildberries.ru (cffi chrome)")
try:
    r = do_get(f"https://m.wildberries.ru/catalog/0/ru/?search={quote(query)}&page=1",
               {"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9", "User-Agent": UA_M}, timeout=12)
    print(f"  Status={r.status_code} len={len(r.text)}")
    if r.status_code == 200 and len(r.text) > 3000:
        ms = list(re.finditer(r'/catalog/(\d+)/detail\.aspx', r.text, re.I))
        print(f"  Product links: {len(ms)}")
        out = Path(__file__).parent / "wb_mobile_v2.html"
        out.write_text(r.text, encoding="utf-8")
        print(f"  Saved: {out}")
    else:
        print(f"  Body[:500]: {r.text[:500]}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- 4. card.wb.ru with Referer ---
print("\n" + "=" * 60)
print("4. card.wb.ru v2 (cffi + Referer)")
try:
    r = do_get(f"https://card.wb.ru/cards/v2/query?{params}",
               {"Accept": "application/json", "User-Agent": UA_D,
                "Referer": "https://www.wildberries.ru/", "Origin": "https://www.wildberries.ru"}, timeout=8)
    print(f"  Status={r.status_code} len={len(r.text)}")
    if r.status_code == 200:
        d = r.json()
        prods = d.get("data", {}).get("products", d.get("products", []))
        print(f"  Products: {len(prods)}")
        if prods:
            p0 = prods[0]
            print(f"  First: id={p0.get('id')} name={p0.get('name','')[:40]}")
    else:
        print(f"  Body[:300]: {r.text[:300]}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- 5. Homepage first (cookies) then search ---
print("\n" + "=" * 60)
print("5. Homepage → cookies → search (cffi)")
try:
    r_home = do_get("https://www.wildberries.ru/", {"User-Agent": UA_D}, timeout=10)
    print(f"  Homepage: {r_home.status_code} len={len(r_home.text)}")
    # Check cookies
    try:
        cookies = c._cffi.cookies.get_dict() if c._cffi else dict(c.session.cookies)
        print(f"  Cookies: {list(cookies.keys())[:15]}")
    except Exception:
        print("  (no cookie access)")
    time.sleep(2)
    r = do_get(f"https://www.wildberries.ru/catalog/0/ru/?search={quote(query)}&page=1",
               {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ru-RU,ru;q=0.9",
                "User-Agent": UA_D, "Referer": "https://www.wildberries.ru/"}, timeout=12)
    print(f"  Search: {r.status_code} len={len(r.text)}")
    if r.status_code == 200 and len(r.text) > 5000:
        ms = list(re.finditer(r'/catalog/(\d+)/detail\.aspx', r.text, re.I))
        print(f"  Product links: {len(ms)}")
    else:
        print(f"  Body[:500]: {r.text[:500]}")
except Exception as e:
    print(f"  ERROR: {e}")
    traceback.print_exc()

# --- 6. WB API: common/v5 or v3 ---
print("\n" + "=" * 60)
print("6. catalog.wb.ru v5/v3")
for ver in ["v5", "v3"]:
    try:
        r = do_get(f"https://catalog.wb.ru/exactmatch/ru/common/{ver}/search?{params}",
                   {"Accept": "application/json", "User-Agent": UA_D}, timeout=8)
        print(f"  {ver}: Status={r.status_code}")
        if r.status_code == 200:
            d = r.json()
            print(f"    Products: {len(d.get('products', []))}")
    except Exception as e:
        print(f"  {ver}: ERROR {e}")

# --- 7. WB: different dest / spp ---
print("\n" + "=" * 60)
print("7. catalog.wb.ru v4 with dest=-1 (all regions)")
try:
    p2 = f"query={quote(query)}&resultset=catalog&dest=-1&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
    r = do_get(f"https://catalog.wb.ru/exactmatch/ru/common/v4/search?{p2}",
               {"Accept": "application/json", "User-Agent": UA_D}, timeout=8)
    print(f"  Status={r.status_code}")
    if r.status_code == 200:
        d = r.json()
        print(f"  Products: {len(d.get('products', []))}")
except Exception as e:
    print(f"  ERROR: {e}")

c.close()
print("\nDone.")
