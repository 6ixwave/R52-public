"""Диагностика WB mobile: m.wildberries.ru / api mobile endpoints."""
import re, sys, time, json, logging
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.wb_mobile")

HOME = Path(__file__).resolve().parents[1] / "data" / "test_home"
from r52.collectors.wb import WBCollector

c = WBCollector(data_dir=HOME, use_proxies=False)
c._warmup()
time.sleep(1)

query = "тест"
proxies = c.pool.get_proxy()

def do_get(url, headers, timeout=10):
    if c._cffi:
        resp = c._cffi.get(url, impersonate="chrome", timeout=(8, timeout), headers=headers, proxies=proxies)
    else:
        resp = c.session.get(url, headers=headers, proxies=proxies, timeout=(8, timeout))
    return resp

UA_MOBILE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# 1. m.wildberries.ru
print("=" * 70)
print("1. m.wildberries.ru search")
print("=" * 70)
m_url = f"https://m.wildberries.ru/catalog/0/ru/?search={quote(query)}&page=1"
r = do_get(m_url, {"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9", "User-Agent": UA_MOBILE}, timeout=12)
print(f"  Status: {r.status_code}, len={len(r.text)}")
if r.status_code == 200 and len(r.text) > 3000:
    html = r.text
    # Look for product links
    for pat_name, pat in [
        ("catalog detail", re.compile(r'/catalog/(\d+)/detail\.aspx', re.I)),
        ("data-id", re.compile(r'data-id=["\'](\d{5,})["\']')),
        ("nm-t", re.compile(r'"nm":(\d{5,})')),
        ("price basic", re.compile(r'"basic":(\d+)')),
    ]:
        ms = list(pat.finditer(html))
        print(f"  {pat_name}: {len(ms)} matches")
    # Save
    out = Path(__file__).parent / "wb_mobile.html"
    out.write_text(html, encoding="utf-8")
    print(f"  Saved: {out}")

# 2. catalog.wb.ru with mobile UA
print("\n" + "=" * 70)
print("2. catalog.wb.ru JSON with mobile UA")
print("=" * 70)
params = f"query={quote(query)}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
j_url = f"https://catalog.wb.ru/exactmatch/ru/common/v4/search?{params}"
r = do_get(j_url, {"Accept": "application/json", "User-Agent": UA_MOBILE}, timeout=8)
print(f"  Status: {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        prods = d.get("products", [])
        print(f"  Products: {len(prods)}")
        if prods:
            p0 = prods[0]
            sizes = p0.get("sizes", [{}])
            price = sizes[0].get("price", {}).get("basic") if sizes else None
            print(f"  First: id={p0.get('id')} name={p0.get('name','')[:40]} price_basic={price}")
    except Exception as e:
        print(f"  Parse: {e}")

# 3. card.wb.ru with mobile UA
print("\n" + "=" * 70)
print("3. card.wb.ru batch with mobile UA")
print("=" * 70)
card_url = f"https://card.wb.ru/cards/v2/query?{params}"
r = do_get(card_url, {"Accept": "application/json", "User-Agent": UA_MOBILE}, timeout=8)
print(f"  Status: {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        prods = d.get("data", {}).get("products", d.get("products", []))
        print(f"  Products: {len(prods)}")
    except Exception as e:
        print(f"  Parse: {e}")

# 4. Wildberries API: search with different path
print("\n" + "=" * 70)
print("4. WB API v5 / v3")
print("=" * 70)
for ver in ["v5", "v3"]:
    u = f"https://catalog.wb.ru/exactmatch/ru/common/{ver}/search?{params}"
    try:
        r = do_get(u, {"Accept": "application/json", "User-Agent": UA_DESKTOP}, timeout=8)
        print(f"  {ver}: Status={r.status_code}")
        if r.status_code == 200:
            d = r.json()
            print(f"    Products: {len(d.get('products', []))}")
    except Exception as e:
        print(f"  {ver}: {e}")

# 5. Try with x-request-type header (WB WAF bypass)
print("\n" + "=" * 70)
print("5. catalog.wb.ru with X-Request-Type header")
print("=" * 70)
r = do_get(j_url, {
    "Accept": "application/json",
    "User-Agent": UA_DESKTOP,
    "X-Request-Type": "fetch",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}, timeout=8)
print(f"  Status: {r.status_code}")
if r.status_code == 200:
    d = r.json()
    print(f"  Products: {len(d.get('products', []))}")

# 6. Try basket-*.wbbasket.ru direct (image CDN often less protected)
print("\n" + "=" * 70)
print("6. www.wildberries.ru with full desktop headers + cookies")
print("=" * 70)
# First hit homepage to get cookies
r_home = do_get("https://www.wildberries.ru/", {"User-Agent": UA_DESKTOP}, timeout=10)
print(f"  Homepage: {r_home.status_code}")
cookies = {}
if c._cffi:
    cookies = c._cffi.cookies.get_dict()
else:
    cookies = dict(c.session.cookies)
print(f"  Cookies: {list(cookies.keys())[:10]}")

# Now search with cookies
r = do_get(f"https://www.wildberries.ru/catalog/0/ru/?search={quote(query)}&page=1", {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "User-Agent": UA_DESKTOP,
    "Referer": "https://www.wildberries.ru/",
}, timeout=12)
print(f"  Search: {r.status_code}, len={len(r.text)}")
if r.status_code == 200 and len(r.text) > 5000:
    html = r.text
    ms = list(re.finditer(r'/catalog/(\d+)/detail\.aspx', html, re.I))
    print(f"  Product links: {len(ms)}")
    out = Path(__file__).parent / "wb_desktop_cookies.html"
    out.write_text(html, encoding="utf-8")

c.close()
print("\nDone.")
