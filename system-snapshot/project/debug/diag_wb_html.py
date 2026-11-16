"""Диагностика WB: проверяем HTML fallback и card.wb.ru batch API."""
import re, sys, time, json, logging
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag.wb")

HOME = Path(__file__).resolve().parents[1] / "data" / "test_home"
from r52.collectors.wb import WBCollector

c = WBCollector(data_dir=HOME, use_proxies=False)
c._warmup()
time.sleep(1)

query = "тест"
headers_json = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
headers_html = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
proxies = c.pool.get_proxy()

def do_get(url, headers, timeout=10):
    if c._cffi:
        resp = c._cffi.get(url, impersonate="chrome", timeout=(8, timeout), headers=headers, proxies=proxies)
    else:
        resp = c.session.get(url, headers=headers, proxies=proxies, timeout=(8, timeout))
    return resp

# 1. Try JSON API endpoints
print("=" * 70)
print("1. JSON API endpoints")
print("=" * 70)
params = f"query={quote(query)}&resultset=catalog&dest=-1257786&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page=1"
json_urls = [
    f"https://catalog.wb.ru/exactmatch/ru/common/v4/search?{params}",
    f"https://search.wb.ru/exactmatch/ru/common/v4/search?{params}",
]
for i, u in enumerate(json_urls):
    try:
        r = do_get(u, headers_json, timeout=8)
        print(f"\n--- JSON {i+1}: {u.split('?')[0]} ---")
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            body = r.text[:500]
            print(f"  Body: {body}")
            try:
                d = r.json()
                prods = d.get("products", d.get("data", {}).get("products", []))
                print(f"  Products: {len(prods)}")
                if prods:
                    p0 = prods[0]
                    print(f"  First: id={p0.get('id')} name={p0.get('name','')[:50]} price={p0.get('sizes',[{}])[0].get('price',{}).get('basic') if p0.get('sizes') else 'N/A'}")
            except Exception as e:
                print(f"  JSON parse: {e}")
        elif r.status_code in (403, 429):
            print(f"  Headers: {dict(list(r.headers.items())[:8])}")
    except Exception as e:
        print(f"  Error: {e}")

# 2. Try card.wb.ru batch
print("\n" + "=" * 70)
print("2. card.wb.ru batch API")
print("=" * 70)
card_url = f"https://card.wb.ru/cards/v2/query?{params}"
try:
    r = do_get(card_url, headers_json, timeout=8)
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        body = r.text[:500]
        print(f"  Body: {body}")
        try:
            d = r.json()
            prods = d.get("data", {}).get("products", d.get("products", []))
            print(f"  Products: {len(prods)}")
            if prods:
                p0 = prods[0]
                print(f"  First: id={p0.get('id')} name={p0.get('name','')[:50]}")
        except Exception as e:
            print(f"  JSON parse: {e}")
except Exception as e:
    print(f"  Error: {e}")

# 3. HTML fallback: wildberries.ru search page
print("\n" + "=" * 70)
print("3. HTML fallback: wildberries.ru")
print("=" * 70)
html_urls = [
    f"https://www.wildberries.ru/catalog/0/ru/?search={quote(query)}&page=1",
    f"https://www.wildberries.ru/catalog/0/ru/search.aspx?search={quote(query)}",
]
for i, u in enumerate(html_urls):
    try:
        r = do_get(u, headers_html, timeout=12)
        print(f"\n--- HTML {i+1}: {u.split('?')[0]} ---")
        print(f"  Status: {r.status_code}")
        print(f"  Final URL: {r.url}")
        html = r.text if hasattr(r, 'text') else r.content.decode('utf-8', 'replace')
        print(f"  Length: {len(html)}")
        if r.status_code == 200 and len(html) > 3000:
            # Try current regex
            prod_pat = re.compile(
                r'<a[^>]*href=["\'](?:https?://www\.wildberries\.ru)?/catalog/(\d+)/detail\.aspx[^"\']*["\'][^>]*>',
                re.I
            )
            matches = list(prod_pat.finditer(html))
            print(f"  Current regex matches: {len(matches)}")
            
            # Try broader: any /catalog/{id}/ link
            prod_pat2 = re.compile(
                r'<a[^>]*href=["\'](?:https?://www\.wildberries\.ru)?/catalog/(\d+)/[^"\']*["\'][^>]*>',
                re.I
            )
            matches2 = list(prod_pat2.finditer(html))
            print(f"  Broader regex matches: {len(matches2)}")
            
            # Try data attributes
            prod_pat3 = re.compile(r'data-id=["\'](\d{5,})["\']')
            matches3 = list(prod_pat3.finditer(html))
            print(f"  data-id matches: {len(matches3)}")
            
            # Try JSON in page (window.__STATE__ etc)
            state_pat = re.compile(r'window\.__\w+__\s*=\s*')
            state_m = list(state_pat.finditer(html))
            print(f"  window.__STATE__ objects: {len(state_m)}")
            
            # Show first product-like block
            if matches2:
                m = matches2[0]
                bs = max(0, m.start() - 200)
                be = min(len(html), m.end() + 500)
                print(f"\n  First product block:")
                print(f"  {html[bs:be]}")
                
            # Save for inspection
            out = Path(__file__).parent / f"wb_html_{i+1}.html"
            out.write_text(html, encoding="utf-8")
            print(f"  Saved: {out}")
    except Exception as e:
        print(f"  Error: {e}")

c.close()
print("\nDone.")
