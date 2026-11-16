"""Диагностика: SQLite + WB + Avito (curl_cffi)."""
import json, re, sys, time
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=10))
def ts():
    return datetime.now(TZ).strftime("%H:%M:%S")

print(f"[{ts()}] diag16 start")

# ============================================================
# 1. SQLITE
# ============================================================
print(f"\n[{ts()}] === SQLITE ===")
import sqlite3
DB = r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3"
con = sqlite3.connect(DB)
cur = con.cursor()

for src in ["WB", "OZON", "AVITO", "FARPOST"]:
    p = cur.execute(
        "SELECT id, listing_id, title, source FROM product WHERE source=?", (src,)
    ).fetchall()
    o = cur.execute("""
        SELECT o.id, o.product_id, o.observed_at, o.availability,
               substr(o.normalized_json, 1, 200)
        FROM observation o
        JOIN product p ON o.product_id = p.id
        WHERE p.source=?
        ORDER BY o.rowid DESC LIMIT 3
    """, (src,)).fetchall()
    pp = cur.execute("""
        SELECT pp.id, pp.product_id, pp.role, pp.amount_minor, pp.currency, pp.label, pp.observed_at
        FROM price_point pp
        JOIN product p ON pp.product_id = p.id
        WHERE p.source=?
        ORDER BY pp.rowid DESC LIMIT 5
    """, (src,)).fetchall()
    print(f"  --- {src} ---")
    print(f"  products={len(p)}")
    for r in p[:3]:
        print(f"    P: id={r[0]} listing={r[1]} title={str(r[2])[:50]}")
    print(f"  observations={len(o)}")
    for r in o[:2]:
        print(f"    O: id={r[0]} prod={r[1]} ts={r[2]} avail={r[3]}")
        print(f"       json[:100]={str(r[4])[:100]}")
    print(f"  price_points={len(pp)}")
    for r in pp[:3]:
        print(f"    PP: id={r[0]} prod={r[1]} role={r[2]} amt={r[3]} cur={r[4]} label={r[5]}")
con.close()

# ============================================================
# 2. WB — curl_cffi
# ============================================================
print(f"\n[{ts()}] === WB: curl_cffi ===")
from curl_cffi import requests as cr

NM = 1097975275
h_html = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}
h_json = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

# 2a. HTML page
try:
    r = cr.get(f"https://www.wildberries.ru/catalog/{NM}/detail.aspx",
               headers=h_html, impersonate="chrome", timeout=15)
    print(f"  HTML: status={r.status_code} len={len(r.text)}")
    html = r.text
    # Price patterns
    for pat_name, pat in [
        ("og:price", r'og:price:amount[^>]*content=["\']([\d.]+)'),
        ("data-price", r'data-price=["\'](\d+)["\']'),
        ("basic", r'"basic"\s*:\s*(\d+)'),
        ("priceU", r'"priceU"\s*:\s*(\d+)'),
        ("priceD", r'"priceD"\s*:\s*(\d+)'),
        ("price_obj", r'"price"\s*:\s*\{[^}]+\}'),
    ]:
        m = re.findall(pat, html)
        if m:
            print(f"  HTML MATCH {pat_name}: {m[:3]}")
    # Embedded state
    for state_name in ["__INITIAL_STATE__", "__NEXT_DATA__", "__APP_DATA__", "window.__STATE__"]:
        m = re.search(rf'{re.escape(state_name)}\s*=\s*(\{{.+?\}})\s*[;<]', html, re.DOTALL)
        if m:
            print(f"  HTML STATE {state_name}: len={len(m.group(1))}")
    # First 3000 chars
    print(f"  HTML[:3000]:")
    for line in html[:3000].split("\n")[:40]:
        print(f"    {line[:120]}")
except Exception as e:
    print(f"  HTML ERROR: {e}")

time.sleep(2)

# 2b. Price endpoints
print(f"\n  --- WB Price endpoints ---")
price_urls = [
    ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("card_v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM}"),
    ("search", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
]
for label, url in price_urls:
    try:
        r = cr.get(url, headers=h_json, impersonate="chrome", timeout=10)
        body = r.text[:300] if r.status_code == 200 else r.text[:200]
        print(f"  {label}: {r.status_code} len={len(r.text)} body[:150]={body[:150]}")
        if r.status_code == 200:
            try:
                data = r.json()
                prods = data.get("products") or data.get("data", {}).get("items", [])
                if prods:
                    p = prods[0]
                    sizes = p.get("sizes", [])
                    if sizes:
                        sz = sizes[0]
                        pr = sz.get("price", {})
                        print(f"    PRICE: basic={pr.get('basic')} product={pr.get('product')} card={pr.get('card')} wallet={pr.get('wallet')} old={pr.get('old')}")
                    print(f"    name={p.get('name','?')[:60]}")
            except Exception as e2:
                print(f"    json parse: {e2}")
    except Exception as e:
        print(f"  {label}: ERROR {e}")
    time.sleep(1.5)

# 2c. Basket card.json (confirm still works)
print(f"\n  --- WB Basket card.json ---")
vol = NM // 100000
part = NM // 1000
# Try known host basket-42
for host_num in [42, 1, 10, 20, 30]:
    host = f"basket-{host_num:02d}.wbbasket.ru"
    path = f"/vol{vol}/part{part}/{NM}/info/ru/card.json"
    url = f"https://{host}{path}"
    try:
        r = cr.get(url, headers=h_json, impersonate="chrome", timeout=8)
        if r.status_code == 200:
            data = r.json()
            print(f"  {host}: 200, keys={list(data.keys())[:15]}")
            selling = data.get("selling", {})
            print(f"  selling keys: {list(selling.keys()) if isinstance(selling, dict) else type(selling)}")
            if isinstance(selling, dict):
                for k in ("price", "priceU", "priceD", "oldPrice", "brand_name"):
                    if k in selling:
                        print(f"    selling.{k} = {selling[k]}")
            # Check sizes
            sizes = data.get("sizes", [])
            if sizes:
                print(f"  sizes[0]: {json.dumps(sizes[0], ensure_ascii=False)[:200]}")
            break
        else:
            print(f"  {host}: {r.status_code}")
    except Exception as e:
        print(f"  {host}: ERROR {e}")
    time.sleep(0.5)

# ============================================================
# 3. AVITO — curl_cffi
# ============================================================
print(f"\n[{ts()}] === AVITO: curl_cffi ===")
h_av = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

# 3a. Listing page (spb)
for city in ["spb", "ekb"]:
    url = f"https://www.avito.ru/{city}/smartfony-i-telefony"
    try:
        r = cr.get(url, headers=h_av, impersonate="chrome", timeout=15)
        print(f"\n  [{city}] status={r.status_code} len={len(r.text)}")
        html = r.text
        # Ad ID patterns (broader)
        patterns = {
            "ad_url": r'avito\.ru/(?:\w+/)?ad/(\d{6,})',
            "ad_slug": r'avito\.ru/\w+/\w+/(\d{6,})',
            "data_ad": r'data-ad[-_]?id=["\']?(\d+)',
            "json_id": r'"(?:adId|ad_id|id)"\s*:\s*"?(\d{8,})"?',
            "href_ad": r'href=["\'](?:/|\w+/)(?:ad|ads)/(\d+)',
        }
        found_any = False
        for pname, pat in patterns.items():
            m = re.findall(pat, html)
            if m:
                print(f"  [{city}] MATCH {pname}: {m[:5]} (total={len(m)})")
                found_any = True
        if not found_any:
            print(f"  [{city}] NO ad IDs found in any pattern")
        # Check for __PRELOADED_STATE__ / __NEXT_DATA__ / __NUXT__
        for sn in ["__PRELOADED_STATE__", "__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__", "window.__STATE__"]:
            m = re.search(rf'{re.escape(sn)}\s*=\s*(\{{.+?\}})\s*[;<]', html, re.DOTALL)
            if m:
                print(f"  [{city}] STATE {sn}: len={len(m.group(1))}")
                # Try to find ad IDs in state
                state_str = m.group(1)
                ids = re.findall(r'\d{10,}', state_str)
                if ids:
                    print(f"    numeric IDs in state: {ids[:10]}")
        # JSON-LD
        ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"  [{city}] JSON-LD blocks: {len(ld)}")
        # Structure: print key sections
        print(f"  [{city}] HTML[:4000] structure:")
        for line in html[:4000].split("\n")[:50]:
            stripped = line.strip()
            if stripped and len(stripped) > 5:
                print(f"    {stripped[:130]}")
        break  # only first city if it works
    except Exception as e:
        print(f"  [{city}] ERROR: {e}")
    time.sleep(2)

# 3b. Mobile Avito
print(f"\n  --- AVITO mobile ---")
try:
    r = cr.get("https://m.avito.ru/spb/smartfony-i-telefony",
               headers=h_av, impersonate="chrome", timeout=15)
    print(f"  mobile: status={r.status_code} len={len(r.text)}")
    html = r.text
    ids = re.findall(r'avito\.ru/(?:\w+/)?ad/(\d{6,})', html)
    ids2 = re.findall(r'"(?:adId|id)"\s*:\s*"?(\d{8,})"?', html)
    print(f"  mobile ad IDs (url): {ids[:5]}")
    print(f"  mobile ad IDs (json): {ids2[:5]}")
    if len(html) > 1000:
        print(f"  mobile HTML[:2000]:")
        for line in html[:2000].split("\n")[:30]:
            s = line.strip()
            if s and len(s) > 5:
                print(f"    {s[:130]}")
except Exception as e:
    print(f"  mobile ERROR: {e}")

print(f"\n[{ts()}] diag16 done")
