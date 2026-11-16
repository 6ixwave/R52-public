"""Быстрая диагностика: чистые HTTP-запросы без rate-limit и коллекторной обвязки."""
import sys, time, json
from curl_cffi import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

def hit(label, url, headers=None, timeout=15):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {url}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    try:
        h = {**H, **(headers or {})}
        r = cffi_req.get(url, impersonate="chrome", headers=h, timeout=timeout)
        dt = time.monotonic() - t0
        print(f"  Status: {r.status_code} | Time: {dt:.1f}s | Len: {len(r.text)}")
        print(f"  Headers: {dict(list(r.headers.items())[:6])}")
        print(f"  Body[:600]:\n{r.text[:600]}")
        return r
    except Exception as e:
        print(f"  EXCEPTION ({time.monotonic()-t0:.1f}s): {type(e).__name__}: {e}")
        return None

# --- 1. WB: basket CDN (основной источник) ---
# Для ID 199298871: vol=19929, part=1992
lid = "199298871"
vol = lid[:5]
part = lid[:7]
basket_url = f"https://basket-{int(vol)//100}.wbbasket.ru/vol{vol}/part{part}/{lid}/info/card.json"
hit("WB BASKET CDN", basket_url, headers={"Accept": "application/json"})

# WB price API
price_url = f"https://card.wb.ru/cards/v2/detail?nm={lid}"
hit("WB PRICE API", price_url, headers={"Accept": "application/json"})

# --- 2. OZON: Composer API ---
ozon_id = "1600562267"
hit("OZON COMPOSER API", f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{ozon_id}/",
    headers={"Accept": "application/json"}, timeout=20)

# OZON SSR HTML
hit("OZON SSR HTML", f"https://www.ozon.ru/product/{ozon_id}/", timeout=25)

# --- 3. AVITO ---
avito_id = "1096783029"
hit("AVITO WWW", f"https://www.avito.ru/ad/{avito_id}", timeout=20)
hit("AVITO MOBILE", f"https://m.avito.ru/ad/{avito_id}", timeout=20)

# --- 4. FARPOST ---
fp_id = "18530297982"
hit("FARPOST SHORT", f"https://www.farpost.ru/vladivostok/{fp_id}.html", timeout=20)
hit("FARPOST BULLETIN", f"https://www.farpost.ru/bulletin/{fp_id}", timeout=20)

# --- 5. ALI (проверка доступности) ---
hit("ALI EXPRESS", "https://www.aliexpress.com/item/1005001234567890.html", timeout=20)

print("\n\nDONE.")
