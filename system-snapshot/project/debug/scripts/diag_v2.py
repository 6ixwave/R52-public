"""Диагностика v2: исправленные URL, свежие ID, глубокая проверка."""
import sys, time, json, re
from curl_cffi import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

def hit(label, url, headers=None, timeout=15, verify=True):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {url}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    try:
        h = {**H, **(headers or {})}
        r = cffi_req.get(url, impersonate="chrome", headers=h, timeout=timeout, verify=verify)
        dt = time.monotonic() - t0
        print(f"  Status: {r.status_code} | Time: {dt:.1f}s | Len: {len(r.text)}")
        # Show key headers
        for hk in ('server', 'content-type', 'ozon-antibot', 'set-cookie'):
            if hk in r.headers:
                v = r.headers[hk]
                print(f"  {hk}: {v[:120]}")
        print(f"  Body[:800]:\n{r.text[:800]}")
        return r
    except Exception as e:
        print(f"  EXCEPTION ({time.monotonic()-t0:.1f}s): {type(e).__name__}: {e}")
        return None

# ============================================================
# 1. WB — basket CDN (правильная формула) + price
# ============================================================
# WB basket: для ID 199298871
# vol = 19929, part = 1992988, host = basket-{vol//100}
# Но если DNS не резолвится, пробуем basket-{vol//1000} и www
lid = "199298871"
vol = lid[:5]
part = lid[:7]

# Варианты basket host
for host_fmt in [
    f"basket-{int(vol)//100}.wbbasket.ru",
    f"basket-{int(vol)//1000}.wbbasket.ru",
    f"www.basket-{int(vol)//100}.wbbasket.ru",
]:
    url = f"https://{host_fmt}/vol{vol}/part{part}/{lid}/info/card.json"
    r = hit(f"WB BASKET ({host_fmt})", url, headers={"Accept": "application/json"}, timeout=10)
    if r and r.status_code == 200:
        print(f"  >>> FOUND WORKING BASKET HOST: {host_fmt}")
        break

# WB price: пробуем v2 и v1, разные заголовки
for api in [
    f"https://card.wb.ru/cards/v2/detail?nm={lid}",
    f"https://card.wb.ru/cards/v1/detail?nm={lid}",
    f"https://static-basket-{int(vol)//100}.wbbasket.ru/vol{vol}/part{part}/{lid}/info/variants.json",
]:
    hit(f"WB PRICE/ALT", api, headers={
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }, timeout=10)

# WB HTML (detail.aspx) — цена в HTML
hit("WB HTML detail.aspx", f"https://www.wildberries.ru/catalog/{lid}/detail.aspx", timeout=15)

# ============================================================
# 2. OZON — Composer API (verify=False) + SSR
# ============================================================
ozon_id = "1600562267"

# Composer API с verify=False (SSL issue)
hit("OZON COMPOSER (no-verify)", 
    f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{ozon_id}/",
    headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"},
    timeout=20, verify=False)

# Новый API Ozon
hit("OZON API v2", 
    f"https://www.ozon.ru/api/entrypoint/pdp/{ozon_id}",
    headers={"Accept": "application/json"}, timeout=15)

# SSR с cookie от antibot (имитация)
hit("OZON SSR", f"https://www.ozon.ru/product/{ozon_id}/", timeout=20)

# ============================================================
# 3. AVITO — пробуем разные URL-форматы и ID
# ============================================================
# Свежий ID: ищем популярное объявление
avito_ids = ["1096783029", "1120000000", "1150000000"]
for aid in avito_ids:
    hit(f"AVITO www/{aid}", f"https://www.avito.ru/ad/{aid}", timeout=15)
    time.sleep(1)

# ============================================================
# 4. FARPOST — разные URL-форматы
# ============================================================
fp_id = "18530297982"
for fmt in [
    f"https://www.farpost.ru/vladivostok/{fp_id}.html",
    f"https://www.farpost.ru/bulletin/{fp_id}",
    f"https://www.farpost.ru/ad/{fp_id}",
    f"https://www.farpost.ru/vladivostok/electronics/{fp_id}.html",
]:
    hit(f"FARPOST {fmt.split('.ru')[1]}", fmt, timeout=12)
    time.sleep(1)

# ============================================================
# 5. ALI EXPRESS — глубокий парсинг
# ============================================================
ali_url = "https://www.aliexpress.com/item/1005001234567890.html"
r = hit("ALI EXPRESS", ali_url, timeout=20)
if r and r.status_code == 200:
    html = r.text
    # JSON-LD
    jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"\n  --- JSON-LD blocks: {len(jsonld)} ---")
    for i, block in enumerate(jsonld[:3]):
        print(f"  [{i}]: {block[:300]}")
    
    # __INITIAL_STATE__ / window state
    state_patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;',
        r'window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;',
        r'"__state__"\s*:\s*(\{.*?\})',
        r'window\._dida_config\s*=\s*(\{.*?\})',
        r'window\.__AE_GLOBAL__',
    ]
    for pat in state_patterns:
        m = re.search(pat, html)
        if m:
            print(f"\n  STATE MATCH [{pat[:40]}]: {m.group(0)[:200]}")
    
    # og: meta
    og = re.findall(r'<meta\s+property=["\']og:([^"\']+)["\']\s+content=["\']([^"\']*)["\']', html)
    print(f"\n  OG META: {og[:8]}")
    
    # Title
    tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if tm:
        print(f"  TITLE: {tm.group(1)[:100]}")
    
    # Images
    imgs = re.findall(r'(https?://[^"\']*(?:alicdn|aliexpress)[^"\']*\.(?:jpg|png|webp)[^"\']*)', html)
    print(f"  IMAGES found: {len(imgs)}, first 3: {imgs[:3]}")

print("\n\nDONE v2.")
