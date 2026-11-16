"""v103: Расширенная диагностика WB.

1. Basket CDN: 3 формулы vol/part, хосты 1-100
2. Тело 498 (web_api + HTML) — что WB отдаёт
3. Альтернативные price: static-basket, wbxapi, mobile
4. Fallback: если basket нашёлся — проверяем media URL как индикатор
"""
import json
import re
import sys
import time
from pathlib import Path

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    print("[OK] curl_cffi")
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req
    print("[WARN] fallback requests")

import requests as std_req

NM_ID = 259671175
DETAIL_URL = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"

JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

report = {"basket": {}, "code_498": {}, "price_alt": {}, "summary": {}}

def http_get(url, headers=None, timeout=12, use_cffi=True):
    if use_cffi and HAS_CFFI:
        kw = {"impersonate": "chrome", "timeout": timeout}
        if headers:
            kw["headers"] = headers
        try:
            r = cffi_req.get(url, **kw)
            return r.status_code, r.text, len(r.content), dict(r.headers)
        except Exception as e:
            return -1, str(e), 0, {}
    else:
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
        if headers:
            h.update(headers)
        try:
            r = std_req.get(url, headers=h, timeout=timeout)
            return r.status_code, r.text, len(r.content), dict(r.headers)
        except Exception as e:
            return -1, str(e), 0, {}

# ============================================================
# 1. BASKET CDN — 3 формулы × 100 хостов
# ============================================================
print("\n" + "="*60)
print("1. BASKET CDN (3 формулы, 100 хостов)")
print("="*60)

formulas = {
    "F1_vol100k_part1k": (NM_ID // 100000, (NM_ID // 1000) % 1000),
    "F2_vol100k_part_mod": (NM_ID // 100000, (NM_ID % 100000) // 100),
    "F3_vol10k_part100": (NM_ID // 10000, (NM_ID // 100) % 100),
}

for fname, (vol, part) in formulas.items():
    path = f"/vol{vol}/part{part}/{NM_ID}/info/ru/card.json"
    print(f"\n  [{fname}] vol={vol} part={part} path={path}")
    
    found = False
    for n in range(1, 101):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        t0 = time.monotonic()
        code, body, blen, hdrs = http_get(url, JSON_HEADERS, timeout=6)
        dt = time.monotonic() - t0
        
        if code == 200 and blen > 100:
            try:
                data = json.loads(body)
                if data.get("nm_id") == NM_ID:
                    print(f"    [FOUND] {host} vol={vol} part={part} ({dt:.1f}s, {blen}B)")
                    print(f"    keys: {list(data.keys())[:10]}")
                    report["basket"] = {
                        "formula": fname, "host": host, "vol": vol,
                        "part": part, "path": path, "size": blen,
                        "keys": list(data.keys()),
                    }
                    found = True
                    break
            except json.JSONDecodeError:
                pass
        elif code not in (404, 403):
            print(f"    {host}: HTTP {code} {blen}B {dt:.1f}s")
        time.sleep(0.2)
        
        if n % 20 == 0:
            print(f"    ... scanned {n}/100")
    
    if found:
        break
    else:
        print(f"    [NOT FOUND] 100 hosts")

# ============================================================
# 2. ТЕЛО 498 — web_api
# ============================================================
print("\n" + "="*60)
print("2. CODE 498 ANALYSIS (web_api)")
print("="*60)

url_498 = f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"
code, body, blen, hdrs = http_get(url_498, JSON_HEADERS, timeout=12)
print(f"  URL: {url_498}")
print(f"  HTTP {code} | {blen}B")
print(f"  Headers: {json.dumps({k:v for k,v in hdrs.items() if k.lower() in ('content-type','x-request-id','server','set-cookie','www-authenticate')}, indent=4)}")
print(f"  Body[:500]: {body[:500]}")

report["code_498"]["web_api"] = {
    "status": code, "size": blen,
    "body_preview": body[:500],
    "headers": {k: v for k, v in hdrs.items() if k.lower() in ('content-type','server','www-authenticate','x-request-id')},
}

# ============================================================
# 3. ТЕЛО 498 — HTML
# ============================================================
print("\n" + "="*60)
print("3. CODE 498 ANALYSIS (HTML)")
print("="*60)

code, html, blen, hdrs = http_get(DETAIL_URL, HTML_HEADERS, timeout=15)
print(f"  HTTP {code} | {blen}B")
print(f"  Content-Type: {hdrs.get('content-type','?')}")
print(f"  Server: {hdrs.get('server','?')}")
print(f"  Body[:400]: {html[:400]}")

# Проверяем: JS-редирект? meta refresh?
if "refresh" in html.lower() or "location" in html.lower() or "script" in html.lower():
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.I)
    for s in scripts[:3]:
        if len(s.strip()) > 5:
            print(f"  Script: {s.strip()[:200]}")

report["code_498"]["html"] = {
    "status": code, "size": blen,
    "content_type": hdrs.get("content-type", ""),
    "server": hdrs.get("server", ""),
    "body_preview": html[:400],
}

# ============================================================
# 4. АЛЬТЕРНАТИВНЫЕ PRICE
# ============================================================
print("\n" + "="*60)
print("4. ALTERNATIVE PRICE ENDPOINTS")
print("="*60)

alt_endpoints = [
    # static-basket CDN (иногда отдаёт price)
    ("static_basket", f"https://static-basket-01.wbbasket.ru/vol{NM_ID//100000}/{NM_ID}/info/ru/card.json"),
    # wbxapi
    ("wbxapi_v1", f"https://wbxapi.wildberries.ru/api/v1/card/{NM_ID}"),
    ("wbxapi_v2", f"https://wbxapi.wildberries.ru/api/v2/card/{NM_ID}"),
    # mobile
    ("mobile_api", f"https://www.wildberries.ru/web/api/v1/catalog/{NM_ID}"),
    # search API (иногда содержит price)
    ("search_api", f"https://search.wbxd.ru/v5/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30"),
    # basket price (отдельный endpoint)
    ("basket_price", f"https://basket-01.wbbasket.ru/vol{NM_ID//100000}/part{(NM_ID//1000)%1000}/{NM_ID}/price.json"),
]

for label, url in alt_endpoints:
    print(f"\n  --- {label} ---")
    t0 = time.monotonic()
    code, body, blen, hdrs = http_get(url, JSON_HEADERS, timeout=10)
    dt = time.monotonic() - t0
    print(f"  {url[:80]}")
    print(f"  HTTP {code} | {blen}B | {dt:.1f}s")
    
    if code == 200 and blen > 50:
        try:
            data = json.loads(body)
            # Ищем price-поля
            flat = json.dumps(data)
            prices = re.findall(r'"(?:price|basic|product|card|wallet|old|crossed|bankCard|total)"\s*:\s*(\d+)', flat)
            print(f"  JSON keys: {list(data.keys())[:10] if isinstance(data, dict) else 'list'}")
            if prices:
                print(f"  PRICES: {prices[:10]}")
                report["price_alt"][label] = {"status": code, "prices": prices[:5], "body_preview": body[:300]}
            else:
                print(f"  No price fields. body[:200]={body[:200]}")
        except json.JSONDecodeError:
            print(f"  Not JSON: {body[:150]}")
    elif code in (403, 498, 404):
        print(f"  BLOCKED/NOT_FOUND. body[:150]={body[:150]}")
    else:
        print(f"  body[:150]={body[:150]}")
    time.sleep(1)

# ============================================================
# 5. ИТОГ
# ============================================================
print("\n" + "="*60)
print("5. ИТОГ v103")
print("="*60)

b = report["basket"]
p = report["price_alt"]
print(f"  Basket: {'FOUND ' + b.get('host','') if b.get('host') else 'NOT FOUND'}")
print(f"  498 web_api: {report['code_498'].get('web_api',{}).get('status','?')}")
print(f"  498 html: {report['code_498'].get('html',{}).get('status','?')}")
print(f"  Alt prices: {len(p)} endpoints with data")
for k, v in p.items():
    print(f"    {k}: {v.get('prices',[])}")

out = Path(__file__).parent / "probe_v103_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\n  Report: {out}")
print("[DONE] v103")
