"""Диагностика WB v7: расширенный поиск фото и цены.

Тесты:
1. Photo: ir-XX.wbbasket.ru, static-basket, разные размеры и пути.
2. Price: sec-fetch headers, x-render-version, мобильный API.
3. Кэш basket host: проверить что S800 реально отдаётся или нет.
"""
import json
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_wb_v7")

from curl_cffi import requests as cffi_req

NM_ID = 199298871
VOL = NM_ID // 100000   # 1992
PART = NM_ID // 1000    # 199298
BASKET_HOST = "basket-13.wbbasket.ru"

results = {}

def http_get(url, headers=None, timeout=10, label=""):
    kw = {"timeout": timeout, "impersonate": "chrome"}
    if headers:
        kw["headers"] = headers
    try:
        resp = cffi_req.get(url, **kw)
        body = resp.text[:200] if resp.status_code == 200 else resp.text[:150]
        ct = resp.headers.get("Content-Type", "")
        clen = len(resp.content) if resp.status_code == 200 else 0
        log.info("[%s] %d %s %dB | %s", label, resp.status_code, ct[:30], clen, body[:100].replace("\n", " "))
        return resp.status_code, body, ct, clen
    except Exception as e:
        log.warning("[%s] ERROR: %s", label, e)
        return 0, str(e), "", 0

# ============================================================
# 1. PHOTO: расширенный поиск
# ============================================================
log.info("=" * 60)
log.info("PHOTO: расширенный поиск")
log.info("=" * 60)

photo_headers = {
    "Accept": "image/*,*/*;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

photo_urls = []

# A) ir-XX.wbbasket.ru (изображения)
for n in [13, 1, 5, 10]:
    photo_urls.append((f"ir{n:02d}_S800", f"https://ir-{n:02d}.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800.jpg"))

# B) static-basket
for n in [13, 1]:
    photo_urls.append((f"static{n:02d}_S800", f"https://static-basket{n:02d}.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800.jpg"))

# C) basket-13 разные размеры
for size in ["S100", "S200", "S220", "S300", "S400", "S500", "S600", "S700", "S800", "S120"]:
    photo_urls.append((f"b13_{size}", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images/{size}/{size}.jpg"))

# D) basket-13 без подпапки
photo_urls.append(("b13_flat", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images/S800.jpg"))
photo_urls.append(("b13_flat2", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images/S800_2.jpg"))

# E) basket-13 /images/1/
photo_urls.append(("b13_num1", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images/1/S800.jpg"))
photo_urls.append(("b13_num2", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images/2/S800.jpg"))

# F) wbstatic.net
photo_urls.append(("wbstatic_S800", f"https://wbstatic.net/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800.jpg"))

# G) basket-13 /images/ (root)
photo_urls.append(("b13_root", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images/"))

for label, url in photo_urls:
    s, body, ct, clen = http_get(url, headers=photo_headers, timeout=8, label=label)
    if s == 200 and clen > 100:
        results[f"photo_{label}"] = {"status": "OK", "url": url, "ct": ct, "bytes": clen}
    elif s == 404:
        pass
    else:
        results[f"photo_{label}"] = {"status": f"HTTP_{s}", "url": url, "ct": ct}
    time.sleep(0.3)

# ============================================================
# 2. PRICE: расширенный поиск
# ============================================================
log.info("=" * 60)
log.info("PRICE: расширенный поиск")
log.info("=" * 60)

# A) card.wb.ru с sec-fetch
price_headers_full = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/catalog/199298871/detail.aspx",
    "Origin": "https://www.wildberries.ru",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

price_urls = [
    ("card_v2_secfetch", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}", price_headers_full),
    ("card_v1_secfetch", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}", price_headers_full),
    # B) x-render-version
    ("card_v2_xrender", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}",
     {**price_headers_full, "x-render-version": "1"}),
    # C) Мобильный API
    ("mobile_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}",
     {**price_headers_full, "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}),
    # D) basket CDN — другие JSON
    ("basket_nmjson", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/nm.json", price_headers_full),
    ("basket_card_alt", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json", price_headers_full),
    # E) basket-13 /prices
    ("basket_prices_v2", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/prices.json", price_headers_full),
]

for label, url, hdrs in price_urls:
    s, body, ct, clen = http_get(url, headers=hdrs, timeout=10, label=label)
    if s == 200:
        results[f"price_{label}"] = {"status": "OK", "url": url, "ct": ct, "bytes": clen, "body": body[:500]}
    elif s in (403, 498):
        results[f"price_{label}"] = {"status": f"HTTP_{s}", "url": url}
    else:
        results[f"price_{label}"] = {"status": f"HTTP_{s}", "url": url, "body": body[:200]}
    time.sleep(0.5)

# ============================================================
# 3. HTML detail.aspx с полными заголовками (может дать 200?)
# ============================================================
log.info("=" * 60)
log.info("HTML detail.aspx")
log.info("=" * 60)

html_headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

s, body, ct, clen = http_get(
    f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx",
    headers=html_headers, timeout=15, label="html_full"
)
results["html_full"] = {"status": f"HTTP_{s}", "bytes": clen, "ct": ct, "body": body[:300]}

# ============================================================
# SUMMARY
# ============================================================
log.info("=" * 60)
log.info("RESULTS")
log.info("=" * 60)
for k, v in results.items():
    log.info("  %s: %s", k, json.dumps(v, ensure_ascii=False)[:200])

with open("diag_wb_v7_out.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
log.info("Saved: diag_wb_v7_out.json")
print("DONE")
