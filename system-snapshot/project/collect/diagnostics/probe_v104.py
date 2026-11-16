"""v104: Быстрая диагностика WB.

Бюджет: ~120s максимум.
1. Basket CDN: F1 (vol=2596, part=671), хосты 1-60, timeout=5s
2. Тело 498 (web_api + HTML)
3. search.wbxd.ru (отдельный домен, может не блокировать)
4. Если basket найден — проверяем media URL
"""
import json, re, sys, time
from pathlib import Path

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req

NM_ID = 259671175
VOL = NM_ID // 100000          # 2596
PART = (NM_ID // 1000) % 1000  # 671
CARD_PATH = f"/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"
DETAIL_URL = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"

JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}
HTML_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

t_start = time.monotonic()
report = {}

def get(url, headers=None, timeout=5):
    kw = {"impersonate": "chrome", "timeout": timeout}
    if headers: kw["headers"] = headers
    try:
        r = cffi_req.get(url, **kw)
        return r.status_code, r.text, len(r.content), dict(r.headers)
    except Exception as e:
        return -1, str(e), 0, {}

# ============================================================
# 1. BASKET CDN (быстрый: 1-60, 5s timeout, 0.1s sleep)
# ============================================================
print("="*60)
print(f"1. BASKET CDN: vol={VOL} part={PART} path={CARD_PATH}")
print("="*60)

found_host = None
card_data = None
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}{CARD_PATH}"
    t0 = time.monotonic()
    code, body, blen, hdrs = get(url, JSON_H, timeout=5)
    dt = time.monotonic() - t0

    if code == 200 and blen > 100:
        try:
            d = json.loads(body)
            if d.get("nm_id") == NM_ID:
                found_host = host
                card_data = d
                print(f"  [FOUND] {host} ({dt:.1f}s, {blen}B)")
                print(f"  name={d.get('imt_name','?')[:60]}")
                print(f"  brand={d.get('selling',{}).get('brand_name','?')}")
                break
        except Exception:
            pass
    elif code not in (404,):
        print(f"  {host}: {code} {blen}B {dt:.1f}s")
    time.sleep(0.1)

    if n == 20 or n == 40:
        print(f"  ... {n}/60 scanned, elapsed={time.monotonic()-t_start:.0f}s")

if not found_host:
    print(f"  [NOT FOUND] 60 hosts, elapsed={time.monotonic()-t_start:.0f}s")
    # Пробуем static-basket
    for n in range(1, 11):
        host = f"static-basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{CARD_PATH}"
        code, body, blen, _ = get(url, JSON_H, timeout=4)
        if code == 200 and blen > 100:
            try:
                d = json.loads(body)
                if d.get("nm_id") == NM_ID:
                    found_host = host
                    card_data = d
                    print(f"  [FOUND static] {host}")
                    break
            except: pass
        time.sleep(0.1)

report["basket"] = {"host": found_host, "found": card_data is not None,
                    "vol": VOL, "part": PART, "path": CARD_PATH}

# ============================================================
# 2. ТЕЛО 498
# ============================================================
print(f"\n{'='*60}")
print("2. CODE 498 BODY")
print("="*60)

code, body, blen, hdrs = get(f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}", JSON_H, timeout=10)
print(f"  web_api: HTTP {code} | {blen}B")
print(f"  CT: {hdrs.get('content-type','?')}")
print(f"  Body[:400]: {body[:400]}")

code2, html, blen2, hdrs2 = get(DETAIL_URL, HTML_H, timeout=10)
print(f"\n  HTML: HTTP {code2} | {blen2}B")
print(f"  CT: {hdrs2.get('content-type','?')}")
print(f"  Body[:400]: {html[:400]}")

# Если 498 — может быть JS challenge или redirect
if code2 == 498 and blen2 > 100:
    # Ищем meta refresh, location, script
    m = re.search(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*', html, re.I)
    if m: print(f"  meta-refresh: {m.group(0)[:150]}")
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL|re.I)
    for s in scripts[:3]:
        s = s.strip()
        if len(s) > 10:
            print(f"  script: {s[:200]}")

report["498"] = {"web_api": {"code": code, "size": blen, "body": body[:300]},
                 "html": {"code": code2, "size": blen2, "body": html[:300]}}

# ============================================================
# 3. SEARCH.WBXD.RU (отдельный домен)
# ============================================================
print(f"\n{'='*60}")
print("3. SEARCH.WBXD.RU")
print("="*60)

search_urls = [
    f"https://search.wbxd.ru/v5/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30",
    f"https://search.wbxd.ru/v4/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30",
    f"https://search.wbxd.ru/v3/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30",
]

for i, url in enumerate(search_urls):
    t0 = time.monotonic()
    code, body, blen, hdrs = get(url, JSON_H, timeout=8)
    dt = time.monotonic() - t0
    print(f"\n  v{i+3}: HTTP {code} | {blen}B | {dt:.1f}s")
    if code == 200 and blen > 100:
        try:
            d = json.loads(body)
            products = d.get("data", {}).get("items") or d.get("products") or []
            if products:
                p = products[0]
                print(f"  name={p.get('name','?')[:50]}")
                sizes = p.get("sizes", [])
                for sz in sizes[:2]:
                    pr = sz.get("price", {})
                    qty = sz.get("qty")
                    print(f"  size qty={qty} price={json.dumps(pr, ensure_ascii=False)[:150]}")
                report["search"] = {"version": f"v{i+3}", "name": p.get("name"),
                                    "prices": sizes[0].get("price") if sizes else None,
                                    "qty": sizes[0].get("qty") if sizes else None}
                break
            else:
                print(f"  keys={list(d.keys())[:10]}")
                print(f"  body[:200]={body[:200]}")
        except json.JSONDecodeError:
            print(f"  not JSON: {body[:150]}")
    elif code in (403, 498):
        print(f"  BLOCKED. body[:150]={body[:150]}")
    time.sleep(1)

# ============================================================
# 4. ИТОГ
# ============================================================
print(f"\n{'='*60}")
print(f"4. ИТОГ (elapsed={time.monotonic()-t_start:.0f}s)")
print("="*60)
print(f"  Basket: {'YES ' + found_host if found_host else 'NO'}")
print(f"  498 web_api: {report['498']['web_api']['code']}")
print(f"  498 html: {report['498']['html']['code']}")
print(f"  Search: {report.get('search',{}).get('name','NO')}")

out = Path(__file__).parent / "probe_v104_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"  Report: {out}")

if card_data:
    cp = Path(__file__).parent / f"card_{NM_ID}_v104.json"
    cp.write_text(json.dumps(card_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Card: {cp}")

print("[DONE] v104")
