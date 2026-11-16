"""v105: WB basket CDN — мультиформула + search.wbxd + mobile API.

Бюджет: ~120s.
- 7 part-значений × 60 хостов (timeout=4s, sleep=0.05s)
- search.wbxd.ru v3/v4/v5 с подробным traceback
- mobile API wildberries.ru
"""
import json, re, sys, time, traceback
from pathlib import Path

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req

NM_ID = 259671175
t0_global = time.monotonic()
report = {"basket": {}, "search": {}, "mobile": {}, "summary": {}}

def get(url, headers=None, timeout=4):
    kw = {"impersonate": "chrome", "timeout": timeout}
    if headers:
        kw["headers"] = headers
    try:
        r = cffi_req.get(url, **kw)
        return r.status_code, r.text, len(r.content), dict(r.headers)
    except Exception as e:
        return -1, str(e), 0, {}

JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

# ============================================================
# 1. BASKET CDN — мультиформула
# ============================================================
print("="*60)
print("1. BASKET CDN (мультиформула)")
print("="*60)

# Формулы part для nm_id=259671175
vol = NM_ID // 100000  # 2596
part_candidates = {
    "A_671": (NM_ID // 1000) % 1000,       # 671
    "B_259": (NM_ID // 1000) % 100,        # 71? no... let me compute
    "C_175": NM_ID % 1000,                 # 175
    "D_71":  (NM_ID // 1000) % 100,        # 71
    "E_67":  (NM_ID // 10000) % 100,       # 67
    "F_711": (NM_ID // 100) % 1000,        # 711
    "G_259": (NM_ID // 100000) % 100,      # 96? 
}
# Реальные значения:
# A: 259671 % 1000 = 671
# B: 259671 % 100 = 71
# C: 259671175 % 1000 = 175
# D: 259671 % 100 = 71 (дублирует B)
# E: 25967 % 100 = 67
# F: 2596711 % 1000 = 711
# G: 2596 % 100 = 96

# Убираем дубликаты
seen_parts = set()
formulas = []
for label, part in part_candidates.items():
    if part not in seen_parts:
        seen_parts.add(part)
        formulas.append((label, vol, part))

# Добавим "259" явно (из контекста)
if 259 not in seen_parts:
    formulas.append(("H_259", vol, 259))

print(f"  vol={vol}, part candidates: {[f[2] for f in formulas]}")

found = False
for label, vol_v, part_v in formulas:
    path = f"/vol{vol_v}/part{part_v}/{NM_ID}/info/ru/card.json"
    print(f"\n  [{label}] vol={vol_v} part={part_v} -> {path}")
    
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        t0 = time.monotonic()
        code, body, blen, hdrs = get(url, JSON_H, timeout=4)
        dt = time.monotonic() - t0
        
        if code == 200 and blen > 100:
            try:
                d = json.loads(body)
                if d.get("nm_id") == NM_ID:
                    print(f"    [FOUND] {host} ({dt:.1f}s, {blen}B)")
                    print(f"    name={d.get('imt_name','?')[:60]}")
                    report["basket"] = {"host": host, "formula": label,
                                         "vol": vol_v, "part": part_v,
                                         "path": path, "size": blen,
                                         "keys": list(d.keys())[:15]}
                    found = True
                    break
            except Exception:
                pass
        elif code not in (404,):
            print(f"    {host}: {code} {blen}B")
        time.sleep(0.05)
        
        if time.monotonic() - t0_global > 100:
            print(f"    [TIME BUDGET] elapsed={time.monotonic()-t0_global:.0f}s")
            break
    
    if found:
        break
    if time.monotonic() - t0_global > 100:
        break

if not found:
    print(f"\n  [NOT FOUND] all formulas, elapsed={time.monotonic()-t0_global:.0f}s")

# ============================================================
# 2. SEARCH.WBXD.RU (с подробным traceback)
# ============================================================
print(f"\n{'='*60}")
print("2. SEARCH.WBXD.RU")
print("="*60)

search_urls = [
    ("v5", f"https://search.wbxd.ru/v5/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30"),
    ("v4", f"https://search.wbxd.ru/v4/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30"),
    ("v3", f"https://search.wbxd.ru/v3/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}&spp=30"),
]

for ver, url in search_urls:
    print(f"\n  [{ver}] {url[:80]}")
    t0 = time.monotonic()
    try:
        code, body, blen, hdrs = get(url, JSON_H, timeout=8)
        dt = time.monotonic() - t0
        print(f"  HTTP {code} | {blen}B | {dt:.1f}s")
        if code == -1:
            print(f"  EXCEPTION: {body[:200]}")
        elif code == 200:
            d = json.loads(body)
            items = d.get("data",{}).get("items") or d.get("products") or []
            if items:
                p = items[0]
                sizes = p.get("sizes",[])
                print(f"  name={p.get('name','?')[:50]}")
                for sz in sizes[:2]:
                    print(f"  qty={sz.get('qty')} price={json.dumps(sz.get('price',{}),ensure_ascii=False)[:120]}")
                report["search"] = {"ver": ver, "name": p.get("name"),
                                    "price": sizes[0].get("price") if sizes else None}
            else:
                print(f"  keys={list(d.keys())[:8]} body[:200]={body[:200]}")
        else:
            print(f"  body[:200]={body[:200]}")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"  EXC {dt:.1f}s: {type(e).__name__}: {e}")
        traceback.print_exc()
    time.sleep(1)

# ============================================================
# 3. MOBILE / ALTERNATIVE API
# ============================================================
print(f"\n{'='*60}")
print("3. ALTERNATIVE WB ENDPOINTS")
print("="*60)

alt_urls = [
    ("mobile_v1", f"https://www.wildberries.ru/web/api/v1/catalog/{NM_ID}"),
    ("mobile_v2", f"https://www.wildberries.ru/web/api/v2/catalog/{NM_ID}"),
    ("wbxapi", f"https://wbxapi.wildberries.ru/api/v1/card/{NM_ID}"),
    ("static_01", f"https://static-basket-01.wbbasket.ru/vol{vol}/part{671}/{NM_ID}/info/ru/card.json"),
    # Новый формат 2026: basket CDN без part
    ("no_part", f"https://basket-01.wbbasket.ru/vol{vol}/{NM_ID}/info/ru/card.json"),
    ("no_part2", f"https://basket-01.wbbasket.ru/{NM_ID}/info/ru/card.json"),
]

for label, url in alt_urls:
    t0 = time.monotonic()
    code, body, blen, hdrs = get(url, JSON_H, timeout=6)
    dt = time.monotonic() - t0
    print(f"\n  [{label}] {url[:80]}")
    print(f"  HTTP {code} | {blen}B | {dt:.1f}s")
    if code == 200 and blen > 50:
        try:
            d = json.loads(body)
            print(f"  keys={list(d.keys())[:10] if isinstance(d,dict) else 'list'}")
            # Ищем price
            flat = json.dumps(d)
            prices = re.findall(r'"(?:price|basic|product|card|wallet|old|crossed|bankCard)"\s*:\s*(\d+)', flat)
            if prices:
                print(f"  PRICES: {prices[:8]}")
            report["mobile"][label] = {"code": code, "keys": list(d.keys())[:8] if isinstance(d,dict) else [], "prices": prices[:5]}
        except Exception as e:
            print(f"  parse: {e}, body[:150]={body[:150]}")
    elif code == -1:
        print(f"  EXC: {body[:150]}")
    else:
        print(f"  body[:150]={body[:150]}")
    time.sleep(0.5)

# ============================================================
# 4. ИТОГ
# ============================================================
print(f"\n{'='*60}")
print(f"4. ИТОГ v105 (elapsed={time.monotonic()-t0_global:.0f}s)")
print("="*60)
print(f"  Basket: {'YES -> ' + report['basket'].get('host','') if report.get('basket',{}).get('host') else 'NO'}")
print(f"  Search: {report.get('search',{}).get('name','NO')}")
print(f"  Alt: {list(report.get('mobile',{}).keys())}")

out = Path(__file__).parent / "probe_v105_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"  Report: {out}")
print("[DONE] v105")
