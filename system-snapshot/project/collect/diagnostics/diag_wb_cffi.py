"""Диагностика: curl_cffi vs requests для WB card.json и price API.
Запуск: C:/Python315/python.exe diag_wb_cffi.py
"""
import json, sys, time
from pathlib import Path

OUT = Path(__file__).parent / "diag_wb_cffi_out.txt"
lines = []

def log(msg):
    lines.append(msg)
    print(msg, flush=True)

def save():
    OUT.write_text("\n".join(lines), encoding="utf-8")

# --- Загрузка ---
try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    log("curl_cffi: OK")
except ImportError as e:
    HAS_CFFI = False
    log(f"curl_cffi: MISSING ({e})")

import requests as std_req
log("requests: OK")

# --- Тестовые ID ---
TEST_IDS = ["199298871"]

# --- Подбор basket host (один раз) ---
def find_host(nm_id_str):
    nm = int(nm_id_str)
    vol = nm // 100000
    part = nm // 1000
    path = f"/vol{vol}/part{part}/{nm_id_str}/info/ru/card.json"
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=8)
            if r.status_code == 200:
                return host, url
        except Exception:
            continue
    return None, None

log("\n" + "=" * 60)
log("FIND BASKET HOST (curl_cffi)")
log("=" * 60)

for nm in TEST_IDS:
    host, url = find_host(nm)
    if not host:
        log(f"  {nm}: host NOT FOUND")
        continue
    log(f"  {nm}: host={host}")
    log(f"  url={url}")

    # --- TEST 1: curl_cffi ---
    log(f"\n--- TEST 1: curl_cffi (impersonate=chrome) ---")
    t0 = time.monotonic()
    r1 = cffi_req.get(url, impersonate="chrome", timeout=15)
    dt1 = time.monotonic() - t0
    log(f"  status={r1.status_code}, {len(r1.content)} bytes, {dt1:.2f}s")
    log(f"  content-type: {r1.headers.get('content-type', '?')}")
    body1 = r1.text[:300]
    log(f"  body[:300]: {body1}")
    try:
        j1 = r1.json()
        log(f"  JSON OK: nm_id={j1.get('nm_id')}, imt_name={j1.get('imt_name','?')[:60]}")
    except Exception as e:
        log(f"  JSON FAIL: {e}")

    # --- TEST 2: standard requests ---
    log(f"\n--- TEST 2: standard requests ---")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    t0 = time.monotonic()
    r2 = std_req.get(url, headers=headers, timeout=15)
    dt2 = time.monotonic() - t0
    log(f"  status={r2.status_code}, {len(r2.content)} bytes, {dt2:.2f}s")
    log(f"  content-type: {r2.headers.get('content-type', '?')}")
    body2 = r2.text[:300]
    log(f"  body[:300]: {body2}")
    try:
        j2 = r2.json()
        log(f"  JSON OK: nm_id={j2.get('nm_id')}, imt_name={j2.get('imt_name','?')[:60]}")
    except Exception as e:
        log(f"  JSON FAIL: {e}")

    # --- TEST 3: price API curl_cffi ---
    log(f"\n--- TEST 3: price API (curl_cffi) ---")
    price_url = (
        f"https://card.wb.ru/cards/v5/detail"
        f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"
    )
    t0 = time.monotonic()
    r3 = cffi_req.get(price_url, impersonate="chrome", timeout=15)
    dt3 = time.monotonic() - t0
    log(f"  status={r3.status_code}, {len(r3.content)} bytes, {dt3:.2f}s")
    body3 = r3.text[:300]
    log(f"  body[:300]: {body3}")
    try:
        j3 = r3.json()
        prods = j3.get("products", [])
        log(f"  products: {len(prods)}")
        if prods:
            p = prods[0]
            log(f"  name={p.get('name','?')[:60]}")
            log(f"  brand={p.get('brand','?')}")
            sizes = p.get("sizes", [])
            if sizes:
                s0 = sizes[0]
                log(f"  size[0] price={s0.get('price')}")
                log(f"  size[0] qty={s0.get('qty')}")
    except Exception as e:
        log(f"  JSON FAIL: {e}")

    # --- TEST 4: price API standard requests ---
    log(f"\n--- TEST 4: price API (standard requests) ---")
    t0 = time.monotonic()
    r4 = std_req.get(price_url, headers=headers, timeout=15)
    dt4 = time.monotonic() - t0
    log(f"  status={r4.status_code}, {len(r4.content)} bytes, {dt4:.2f}s")
    body4 = r4.text[:300]
    log(f"  body[:300]: {body4}")

    # --- TEST 5: HTML page curl_cffi ---
    log(f"\n--- TEST 5: HTML page (curl_cffi) ---")
    html_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    t0 = time.monotonic()
    r5 = cffi_req.get(html_url, impersonate="chrome", timeout=20)
    dt5 = time.monotonic() - t0
    log(f"  status={r5.status_code}, {len(r5.content)} bytes, {dt5:.2f}s")
    # og:title
    import re
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r5.text, re.I)
    if m:
        log(f"  og:title: {m.group(1)[:80]}")
    m = re.search(r'<title[^>]*>([^<]+)</title>', r5.text, re.I)
    if m:
        log(f"  <title>: {m.group(1)[:80]}")
    # price in HTML
    prices = re.findall(r'>(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)\s*<', r5.text)
    log(f"  prices found: {prices[:5]}")

    time.sleep(3)

save()
log(f"\nSaved: {OUT}")
