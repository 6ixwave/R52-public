#!/usr/bin/env python3
"""DIAG V121: Farpost direct full-path + WB images/price deep test.

Цели:
1. Farpost: прямой full-path URL (без ?u= redirect) → реальная карточка?
2. Farpost: /bulletin/{id} с warmup cookie → работает?
3. WB: 30+ паттернов картинок на найденном basket-хосте + ir-*.wbxstatic.net
4. WB: 8 price-эндпоинтов (card.wb.ru v1-v5, basket_info, web API, search)
"""

import json
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = "diag_v121_result.log"
lines = []

def log(msg):
    lines.append(msg)
    print(msg)

def save():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
JSON_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.fildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

log("=" * 64)
log("DIAG V121: FARPOST DIRECT + WB IMAGES/PRICE  (2026-11-11)")
log("=" * 64)

# ================================================================
# PART 1: FARPOST — URL patterns
# ================================================================
log("\n" + "=" * 64)
log("PART 1: FARPOST URL PATTERNS")
log("=" * 64)

FP_ID = "129606872"
FP_FULL_PATH = "/ni/krasnoyarsk/zapchasti/kontraktnyj-dvigatel-1zz-fe-cena"

s = requests.Session()
s.headers.update(HEADERS)

# Warmup
log("\n[Warmup] GET https://www.farpost.ru/")
try:
    r = s.get("https://www.farpost.ru/", timeout=15)
    log(f"  HTTP {r.status_code}, {len(r.content)}B, cookies={list(s.cookies.keys())}")
except Exception as e:
    log(f"  error: {e}")
time.sleep(2)


def check_fp_page(r, label, save_to=None):
    """Анализирует ответ Farpost."""
    log(f"\n[{label}] HTTP {r.status_code}, {len(r.content)}B, enc={r.encoding or r.apparent_encoding}")
    if r.status_code != 200:
        return
    html = r.text
    if save_to:
        with open(save_to, "wb") as f:
            f.write(r.content)
        log(f"  saved → {save_to}")
    # Title
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    log(f"  <title>: {(m.group(1)[:100] if m else 'N/A')}")
    # OG
    for m in re.finditer(
        r'<meta[^>]*property=["\']og:(\w+)["\'][^>]*content=["\']([^"\']*)["\']',
        html, re.I,
    ):
        log(f"  og:{m.group(1)} = {m.group(2)[:100]}")
    # Price
    price_found = False
    for pat_name, pat in [
        ("data-bulletin-price", r'data-bulletin-price=["\'](\d+)["\']'),
        ("viewbull-price", r'viewbull-summary-price__value[^>]*>([\d\s\u00a0]+)'),
        ("og:price", r'og:price:amount["\'][^>]*content=["\']([\d.]+)'),
        ("price-text", r'>\s*([\d\s\u00a0]{3,12})\s*(?:₽|руб)\s*<'),
    ]:
        m = re.search(pat, html, re.I)
        if m:
            log(f"  PRICE [{pat_name}]: {m.group(1)[:60]}")
            price_found = True
    if not price_found:
        log("  PRICE: not found")
    # JSON-LD
    ld_blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.I,
    )
    log(f"  JSON-LD blocks: {len(ld_blocks)}")
    for i, blk in enumerate(ld_blocks[:5]):
        try:
            d = json.loads(blk.strip())
            t = d.get("@type", "?") if isinstance(d, dict) else "list"
            name = d.get("name", "")[:60] if isinstance(d, dict) else ""
            log(f"    [{i}] type={t} name={name}")
        except Exception:
            log(f"    [{i}] parse error, first 80: {blk.strip()[:80]}")
    # Gallery / images
    gallery_count = len(re.findall(r'data-image-info', html))
    lightbox_count = len(re.findall(r'lightbox', html, re.I))
    img_farpost = len(re.findall(r'static\.baza\.farpost\.ru', html))
    log(f"  Gallery: data-image-info={gallery_count}, lightbox={lightbox_count}, CDN imgs={img_farpost}")
    # Altcha
    altcha = bool(re.search(r'altcha|Altcha', html, re.I))
    log(f"  Altcha: {altcha}")
    # Specs
    spec_count = len(re.findall(r'viewbull-field__container', html))
    log(f"  Specs blocks: {spec_count}")
    # Seller
    m = re.search(r'userNick[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', html, re.DOTALL)
    if m:
        log(f"  Seller: {re.sub('<[^>]+>', '', m.group(1)).strip()[:50]}")
    # Bulletin ID
    m = re.search(r'bulletinId=["\'](\d+)["\']', html)
    if m:
        log(f"  bulletinId: {m.group(1)}")


# [A] Direct full path
url_a = f"https://www.farpost.ru{FP_FULL_PATH}"
log(f"\n[A] Direct: {url_a}")
try:
    r = s.get(url_a, timeout=25)
    check_fp_page(r, "A", "farpost_direct_v121.html")
except Exception as e:
    log(f"  error: {e}")
time.sleep(3)

# [B] /bulletin/{id}
url_b = f"https://www.farpost.ru/bulletin/{FP_ID}"
log(f"\n[B] Bulletin: {url_b}")
try:
    r = s.get(url_b, timeout=25)
    check_fp_page(r, "B", "farpost_bulletin_v121.html")
except Exception as e:
    log(f"  error: {e}")
time.sleep(3)

# [C] Full path + ?f=1
url_c = f"https://www.farpost.ru{FP_FULL_PATH}?f=1"
log(f"\n[C] Full+f=1: {url_c}")
try:
    r = s.get(url_c, timeout=25)
    check_fp_page(r, "C", "farpost_f1_direct_v121.html")
except Exception as e:
    log(f"  error: {e}")
time.sleep(3)

# [D] ?u= redirect (v120 pattern — для сравнения)
from urllib.parse import quote
url_d = f"https://www.farpost.ru?u={quote(FP_FULL_PATH, safe='')}&f=1"
log(f"\n[D] ?u= redirect: {url_d[:120]}")
try:
    r = s.get(url_d, timeout=25, allow_redirects=True)
    log(f"  final_url={r.url[:120]}")
    check_fp_page(r, "D")
except Exception as e:
    log(f"  error: {e}")

# [E] Full path, no warmup (fresh session)
log("\n[E] Fresh session, direct full path")
s2 = requests.Session()
s2.headers.update(HEADERS)
try:
    r = s2.get(url_a, timeout=25)
    check_fp_page(r, "E")
except Exception as e:
    log(f"  error: {e}")

# ================================================================
# PART 2: WB IMAGES
# ================================================================
log("\n" + "=" * 64)
log("PART 2: WB IMAGE URL PATTERNS")
log("=" * 64)

NM_ID = 259671175
IMT_ID = 236868886
VOL = NM_ID // 100000
PART = NM_ID // 1000
log(f"\nmn_id={NM_ID}, imt_id={IMT_ID}, vol={VOL}, part={PART}")

# Find basket host
card_path = f"/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"
basket_host = None
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    try:
        r = requests.get(f"https://{host}{card_path}", headers=JSON_HEADERS, timeout=8)
        if r.status_code == 200:
            basket_host = host
            log(f"  card.json OK → {host}")
            break
    except Exception:
        continue

if not basket_host:
    log("  WARNING: basket host not found, using basket-13")
    basket_host = "basket-13.wbbasket.ru"

# Build image URL patterns
sizes = ["S800", "S120", "S50", "1000x0", "big", "thumbnail", "120x120"]
patterns = []
for sz in sizes:
    base = f"/vol{VOL}/part{PART}/{NM_ID}/images"
    patterns.append(f"{base}/{sz}/{IMT_ID}.jpg")
    patterns.append(f"{base}/{sz}/{IMT_ID}_1.jpg")
    patterns.append(f"{base}/{sz}/{NM_ID}.jpg")
    patterns.append(f"{base}/{sz}/{NM_ID}_1.jpg")

# ir-*.wbxstatic.net
for n in range(1, 7):
    patterns.append(f"IR:{n}/{sizes[0]}/{NM_ID}.jpg")
    patterns.append(f"IR:{n}/{sizes[0]}/{IMT_ID}.jpg")

log(f"\nTesting {len(patterns)} image patterns...")
ok_count = 0
for i, pat in enumerate(patterns):
    if pat.startswith("IR:"):
        parts = pat.split("/", 1)
        url = f"https://ir-{parts[0][3:]}.wbxstatic.net/{parts[1]}"
        disp = f"ir-{parts[0][3:]}.wbxstatic.net/{parts[1]}"
    else:
        url = f"https://{basket_host}{pat}"
        disp = pat
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        ct = r.headers.get("Content-Type", "")
        sz = len(r.content)
        ok = r.status_code == 200 and "image" in ct
        mark = "OK" if ok else "xx"
        if ok:
            ok_count += 1
        log(f"  [{mark}] {r.status_code} {sz:>6}B {ct[:25]:25s} {disp}")
    except Exception as e:
        log(f"  [??]  -1  error: {str(e)[:35]} {disp}")
    time.sleep(0.3)

log(f"\n  Summary: {ok_count}/{len(patterns)} OK")

# ================================================================
# PART 3: WB PRICE
# ================================================================
log("\n" + "=" * 64)
log("PART 3: WB PRICE ENDPOINTS")
log("=" * 64)

price_eps = [
    ("card_v1",  f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v2",  f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v4",  f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v5",  f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("basket",   f"https://{basket_host}{card_path}"),
    ("web_v1",   f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    ("web_v1b",  f"https://www.wildberries.ru/web/api/catalog/cards/v1?nm={NM_ID}"),
    ("search5",  f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&nm={NM_ID}"),
]

for label, url in price_eps:
    log(f"\n[{label}] {url[:110]}")
    try:
        r = requests.get(url, headers=JSON_HEADERS, timeout=12)
        log(f"  HTTP {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 10:
            try:
                data = r.json()
                s_json = json.dumps(data, ensure_ascii=False)
                for key in ["price", "basic", "product", "card", "wallet", "old", "qty"]:
                    m = re.search(rf'"{key}"\s*:\s*(\d+)', s_json)
                    if m:
                        log(f"  {key} = {m.group(1)}")
                # Also check nested
                prods = data.get("data", {}).get("items") or data.get("products") or []
                if prods and isinstance(prods, list):
                    p = prods[0]
                    log(f"  product keys: {list(p.keys())[:15]}")
                    pr = p.get("prices", p.get("price", {}))
                    if pr:
                        log(f"  prices: {json.dumps(pr, ensure_ascii=False)[:200]}")
            except Exception as e:
                log(f"  parse: {e}, body[:150]={r.text[:150]}")
    except Exception as e:
        log(f"  error: {e}")
    time.sleep(1)

# ================================================================
# PART 4: WB HTML (detail.aspx) — curl_cffi if available
# ================================================================
log("\n" + "=" * 64)
log("PART 4: WB HTML (detail.aspx)")
log("=" * 64)

wb_html_url = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"
log(f"\n{wb_html_url}")

# Try curl_cffi
try:
    from curl_cffi import requests as cffi_req
    r = cffi_req.get(wb_html_url, impersonate="chrome", timeout=20)
    log(f"  [cffi] HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code == 200:
        html = r.text
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        log(f"  title: {(m.group(1)[:100] if m else 'N/A')}")
        # og:price
        m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            log(f"  og:price: {m.group(1)}")
        # data-price
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
            log(f"  data-price: {m.group(1)}")
            break
        # JSON-LD
        ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
        log(f"  JSON-LD blocks: {len(ld)}")
        with open("wb_html_v121.html", "wb") as f:
            f.write(r.content)
        log(f"  saved → wb_html_v121.html")
except ImportError:
    log("  curl_cffi not installed, trying requests...")
    r = requests.get(wb_html_url, headers=HEADERS, timeout=20)
    log(f"  [req]  HTTP {r.status_code}, {len(r.content)}B")
except Exception as e:
    log(f"  error: {e}")

log("\n" + "=" * 64)
log("DIAG V121 DONE")
log("=" * 64)
save()
print(f"\nLog saved → {LOG}")
