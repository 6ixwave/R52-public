"""Диагностика v57: WB card API v4 + basket CDN + Avito live listings.
Запуск: C:/Python315/python.exe diag_v57_wb_avito.py
"""
import json
import sys
import time
import traceback
from pathlib import Path

OUT = Path(__file__).parent / "diag_v57_out.txt"
lines = []

def log(msg):
    lines.append(msg)
    print(msg)

def save():
    OUT.write_text("\n".join(lines), encoding="utf-8")

# === WB: card.wb.ru/cards/v4/detail ===
log("=" * 60)
log("WB CARD API v4 (curl_cffi browser TLS)")
log("=" * 60)

WB_TEST_IDS = ["199298871", "110835207", "1097975275"]

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    log("curl_cffi NOT available, falling back to requests")

for nm in WB_TEST_IDS:
    url = (
        f"https://card.wb.ru/cards/v4/detail"
        f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"
    )
    log(f"\n--- WB card v4: nm={nm} ---")
    log(f"  URL: {url}")
    try:
        if HAS_CFFI:
            r = cffi_req.get(url, impersonate="chrome", timeout=20)
        else:
            import requests as std_req
            r = std_req.get(url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
        log(f"  HTTP {r.status_code}, len={len(r.text)}")
        if r.status_code == 200:
            data = r.json()
            prods = data.get("products", [])
            log(f"  products: {len(prods)}")
            for p in prods:
                pid = p.get("id", "?")
                pname = p.get("name", "?")
                pbrand = p.get("brand", "?")
                sizes = p.get("sizes", [])
                price_info = "no price"
                avail = "unknown"
                if sizes:
                    s = sizes[0]
                    pr = s.get("price", {})
                    if pr.get("product") is not None:
                        price_info = f"product={pr['product']/100:.0f} RUB, basic={pr.get('basic',0)/100:.0f} RUB"
                    if s.get("qty", 0) > 0:
                        avail = "IN_STOCK"
                    else:
                        avail = "OUT_OF_STOCK"
                log(f"  [{pid}] name={pname[:50]} brand={pbrand}")
                log(f"    price: {price_info}")
                log(f"    avail: {avail}")
                log(f"    root(imtId)={p.get('root','?')}")
                log(f"    pics: {len(p.get('pics',[]))} items")
        else:
            log(f"  BODY (first 500): {r.text[:500]}")
    except Exception as e:
        log(f"  ERROR: {e}")
        traceback.print_exc()
    time.sleep(1)

# === WB: basket CDN (static card with media) ===
log("\n" + "=" * 60)
log("WB BASKET CDN (static card.json)")
log("=" * 60)

# Для nmId 199298871: vol=1992, part=19929
# host lookup: try basket-01..basket-60
test_nm = "199298871"
nm_int = int(test_nm)
vol = nm_int // 100000
part = nm_int // 1000
log(f"\nnmId={test_nm}, vol={vol}, part={part}")

found_host = None
for n in range(1, 61):
    host = f"basket-{n:02d}.wbbasket.ru"
    url = f"https://{host}/vol{vol}/part{part}/{test_nm}/info/ru/card.json"
    try:
        if HAS_CFFI:
            r = cffi_req.get(url, impersonate="chrome", timeout=10)
        else:
            import requests as std_req
            r = std_req.get(url, timeout=10)
        if r.status_code == 200:
            found_host = host
            log(f"  FOUND: {host} (HTTP 200, {len(r.text)} bytes)")
            card = r.json()
            log(f"  imt_id={card.get('imt_id')}, nm_id={card.get('nm_id')}")
            log(f"  imt_name={str(card.get('imt_name','?'))[:60]}")
            log(f"  vendor_code={card.get('vendor_code')}")
            media = card.get("media", [])
            log(f"  media: {len(media)} items")
            for m in media[:5]:
                log(f"    type={m.get('type')} key={str(m.get('key','?'))[:50]}")
            options = card.get("options", [])
            log(f"  options: {len(options)} attrs")
            for o in options[:8]:
                log(f"    {o.get('name')}: {str(o.get('value',''))[:40]}")
            break
        elif r.status_code != 404:
            log(f"  {host}: HTTP {r.status_code}")
    except Exception as e:
        log(f"  {host}: {e}")
    time.sleep(0.2)

if not found_host:
    log("  BASKET: no host found in 1-60")

# === AVITO: live listings ===
log("\n" + "=" * 60)
log("AVITO LIVE LISTINGS")
log("=" * 60)

# Из поиска: попробуем несколько форматов
# 1) Короткий URL /ad/{id}
# 2) Полный URL с городом
# Живые ID — берём из поиска и популярных категорий
AVITO_TESTS = [
    # (id, label, url_pattern)
    ("7764972140", "zemelnye novosemeykino (from search)", "https://www.avito.ru/novosemeykino/zemelnye_uchastki/uchastok_494_ga_promnaznacheniya_7764972140"),
    ("8341658309", "moskva telefony (from search)", "https://www.avito.ru/moskva/telefony/iphone_18_pro_max_256_gb_8341658309"),
    ("200026938", "prev test id", "https://www.avito.ru/ad/200026938"),
    ("1154321987", "prev test id 2", "https://www.avito.ru/ad/1154321987"),
]

# Также попробуем поиск на Avito для живых объявлений
AVITO_SEARCH_URL = "https://www.avito.ru/moskva/telefony?category_id=2509"

for ad_id, label, url in AVITO_TESTS:
    log(f"\n--- AVITO {ad_id} ({label}) ---")
    log(f"  URL: {url}")
    try:
        if HAS_CFFI:
            r = cffi_req.get(url, impersonate="chrome", timeout=20)
        else:
            import requests as std_req
            r = std_req.get(url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
        log(f"  HTTP {r.status_code}, len={len(r.text)}")
        # Проверка на 404 / CAPTCHA / реальную карточку
        html = r.text
        if "404" in html[:2000] or "не найдено" in html.lower()[:3000] or "объявление удалено" in html.lower()[:3000]:
            log("  VERDICT: 404 / not found")
        elif "captcha" in html.lower()[:3000] or "smartcaptcha" in html.lower()[:3000]:
            log("  VERDICT: CAPTCHA")
        elif "og:title" in html and "og:price" in html:
            log("  VERDICT: REAL CARD (has og:title + og:price)")
            import re
            mt = re.search(r'property="og:title"[^>]*content="([^"]+)"', html)
            mp = re.search(r'property="og:price"[^>]*content="([^"]+)"', html)
            if mt: log(f"  title: {mt.group(1)[:60]}")
            if mp: log(f"  price: {mp.group(1)}")
        elif len(html) > 5000:
            # Может быть реальная страница без og:price
            import re
            mt = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            log(f"  VERDICT: PAGE ({len(html)} bytes), title={mt.group(1)[:60] if mt else '?'}")
            # Проверка JSON-LD
            if 'application/ld+json' in html:
                log("  HAS JSON-LD")
            if '__PRELOADED_STATE__' in html:
                log("  HAS __PRELOADED_STATE__")
        else:
            log(f"  VERDICT: SHORT PAGE ({len(html)} bytes)")
            log(f"  first 300: {html[:300]}")
    except Exception as e:
        log(f"  ERROR: {e}")
    time.sleep(2)

# === AVITO: search page для живых объявлений ===
log(f"\n--- AVITO search: {AVITO_SEARCH_URL} ---")
try:
    if HAS_CFFI:
        r = cffi_req.get(AVITO_SEARCH_URL, impersonate="chrome", timeout=20)
    else:
        import requests as std_req
        r = std_req.get(AVITO_SEARCH_URL, timeout=20)
    log(f"  HTTP {r.status_code}, len={len(r.text)}")
    if r.status_code == 200 and len(r.text) > 5000:
        import re
        # Ищем ссылки на объявления в HTML
        ad_links = re.findall(r'href="(https://www\.avito\.ru/[^"]+_\d{6,})"', r.text)
        log(f"  Found {len(ad_links)} ad links")
        for link in ad_links[:10]:
            log(f"    {link}")
        # JSON-LD
        if 'application/ld+json' in r.text:
            log("  HAS JSON-LD on search page")
        # __PRELOADED_STATE__
        if '__PRELOADED_STATE__' in r.text:
            log("  HAS __PRELOADED_STATE__")
            # Извлекаем несколько ID
            state_m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>', r.text, re.DOTALL)
            if state_m:
                log(f"  STATE size: {len(state_m.group(1))} chars")
    elif r.status_code == 439:
        log("  VERDICT: HTTP 439 (rate limit / IP restriction)")
    else:
        log(f"  BODY first 500: {r.text[:500]}")
except Exception as e:
    log(f"  ERROR: {e}")

# === Summary ===
log("\n" + "=" * 60)
log("SUMMARY v57")
log("=" * 60)
log(f"  curl_cffi: {'YES' if HAS_CFFI else 'NO'}")
log(f"  WB card v4: tested {len(WB_TEST_IDS)} IDs")
log(f"  WB basket: host={found_host}")
log(f"  Avito: tested {len(AVITO_TESTS)} listings + search")
log("DONE")

save()
print(f"\nSaved to {OUT}")
