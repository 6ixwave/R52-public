"""Диагностика: дамп структуры card.json для WB-товара.

Запуск: python debug/scripts/diag_wb_cardjson.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

NM_ID = 1097975275  # iPhone 17 Pro Max

# Быстрый fetch через curl_cffi (как в wb.py)
try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
except ImportError:
    import requests
    USE_CFFI = False

import time
import random

vol = NM_ID // 100000
part = NM_ID // 1000
path = f"/vol{vol}/part{part}/{NM_ID}/info/ru/card.json"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

# Ищем basket host
found_host = None
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}{path}"
    try:
        if USE_CFFI:
            resp = cffi_req.get(url, headers=HEADERS, impersonate="chrome", timeout=8)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            found_host = host
            break
    except Exception:
        continue
    time.sleep(0.3)

if not found_host:
    print("ERROR: basket host not found")
    sys.exit(1)

print(f"Host: {found_host}")
print(f"URL: https://{found_host}{path}")

# Fetch
if USE_CFFI:
    resp = cffi_req.get(f"https://{found_host}{path}", headers=HEADERS, impersonate="chrome", timeout=15)
else:
    resp = requests.get(f"https://{found_host}{path}", headers=HEADERS, timeout=15)

print(f"Status: {resp.status_code}")
print(f"Size: {len(resp.content)} bytes")

data = resp.json()
print(f"\n=== TOP-LEVEL KEYS ({len(data)}) ===")
for k in sorted(data.keys()):
    v = data[k]
    t = type(v).__name__
    if isinstance(v, (int, float, str, bool)):
        preview = repr(v)[:80]
    elif isinstance(v, (list, dict)):
        preview = f"{t}[{len(v)}]"
    else:
        preview = t
    print(f"  {k}: {t} = {preview}")

print(f"\n=== SELLING ===")
selling = data.get("selling", {})
if isinstance(selling, dict):
    for k in sorted(selling.keys()):
        v = selling[k]
        print(f"  {k}: {repr(v)[:100]}")
else:
    print(f"  type={type(selling).__name__}, val={repr(selling)[:200]}")

print(f"\n=== SIZES (first 2) ===")
sizes = data.get("sizes", [])
if isinstance(sizes, list):
    for i, s in enumerate(sizes[:2]):
        print(f"  [{i}]:")
        if isinstance(s, dict):
            for k in sorted(s.keys()):
                v = s[k]
                print(f"      {k}: {repr(v)[:120]}")
else:
    print(f"  type={type(sizes).__name__}")

print(f"\n=== MEDIA ===")
media = data.get("media", {})
print(f"  {json.dumps(media, ensure_ascii=False)[:300]}")

print(f"\n=== PRICE-RELATED FIELDS (top-level) ===")
for pk in ("price", "priceU", "priceD", "oldPrice", "priceU", "priceD"):
    if pk in data:
        print(f"  {pk}: {repr(data[pk])[:100]}")

print(f"\n=== imt_name ===")
print(f"  {repr(data.get('imt_name', ''))[:120]}")

# Save full JSON for reference
out = ROOT / "debug" / "outputs" / "wb_cardjson_dump.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nFull JSON saved: {out}")
