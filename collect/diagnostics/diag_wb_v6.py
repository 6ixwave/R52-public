"""Диагностика WB: поиск рабочих источников цены и фото.

Тесты:
1. Price API: несколько вариантов URL/параметров/заголовков.
2. Photo CDN: несколько паттернов URL для дополнительных фото.
3. Basket CDN: дополнительные JSON-файлы (prices, info).
"""
import json
import logging
import time
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_wb_v6")

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    log.info("curl_cffi доступен")
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req
    log.info("fallback: requests")

NM_ID = 199298871
VOL = NM_ID // 100000   # 1992
PART = NM_ID // 1000    # 199298
BASKET_HOST = "basket-13.wbbasket.ru"  # из run4

results = {}

def http_get(url, headers=None, timeout=12, label=""):
    """Быстрый GET, возвращает (status, body_snippet, content_type)."""
    kw = {"timeout": timeout}
    if HAS_CFFI:
        kw["impersonate"] = "chrome"
    if headers:
        kw["headers"] = headers
    try:
        resp = cffi_req.get(url, **kw)
        body = resp.text[:300] if resp.status_code == 200 else resp.text[:200]
        ct = resp.headers.get("Content-Type", "")
        log.info("[%s] %d %s | %s", label, resp.status_code, ct[:40], body[:120].replace("\n", " "))
        return resp.status_code, body, ct
    except Exception as e:
        log.warning("[%s] ERROR: %s", label, e)
        return 0, str(e), ""

# ============================================================
# 1. PRICE: несколько вариантов
# ============================================================
log.info("=" * 60)
log.info("PRICE ENDPOINTS")
log.info("=" * 60)

price_headers = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

price_urls = [
    # card.wb.ru — разные версии и параметры
    ("card_v2_dest", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v1_dest", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card_v2_nodest", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&spp=30&nm={NM_ID}"),
    ("card_v2_dest2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=1&spp=30&nm={NM_ID}"),
    ("card_no_ver", f"https://card.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    # web api
    ("web_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    ("web_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM_ID}"),
    # basket CDN — может быть prices.json
    ("basket_prices", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/info/ru/prices.json"),
    ("basket_price", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/info/ru/price.json"),
    ("basket_info", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/info/ru/info.json"),
]

for label, url in price_urls:
    s, body, ct = http_get(url, headers=price_headers, label=label)
    if s == 200 and "json" in ct:
        results[f"price_{label}"] = {"status": "OK", "body": body[:500]}
    time.sleep(0.5)

# ============================================================
# 2. PHOTO: несколько паттернов
# ============================================================
log.info("=" * 60)
log.info("PHOTO PATTERNS")
log.info("=" * 60)

base_photo = f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/images"
photo_patterns = [
    ("S800_primary", f"{base_photo}/S800/S800.jpg"),
    ("S800_2", f"{base_photo}/S800/S800_2.jpg"),
    ("S800_3", f"{base_photo}/S800/S800_3.jpg"),
    ("S800_4", f"{base_photo}/S800/S800_4.jpg"),
    ("S120_primary", f"{base_photo}/S120/S120.jpg"),
    ("S120_2", f"{base_photo}/S120/S120_2.jpg"),
    ("S120_3", f"{base_photo}/S120/S120_3.jpg"),
    ("S120_4", f"{base_photo}/S120/S120_4.jpg"),
    ("S120_5", f"{base_photo}/S120/S120_5.jpg"),
    ("S120_6", f"{base_photo}/S120/S120_6.jpg"),
    # альтернативные имена
    ("S800_img1", f"{base_photo}/S800/img1.jpg"),
    ("S800_img2", f"{base_photo}/S800/img2.jpg"),
    # без подпапки размера
    ("no_sub_S800", f"{base_photo}/S800.jpg"),
    ("no_sub_S800_2", f"{base_photo}/S800_2.jpg"),
]

photo_headers = {
    "Accept": "image/*,*/*;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

for label, url in photo_patterns:
    s, body, ct = http_get(url, headers=photo_headers, timeout=8, label=label)
    if s == 200:
        results[f"photo_{label}"] = {"status": "OK", "url": url, "ct": ct}
    elif s == 404:
        pass  # ожидаемо для несуществующих
    else:
        results[f"photo_{label}"] = {"status": f"HTTP_{s}", "url": url}
    time.sleep(0.3)

# ============================================================
# 3. VIDEO
# ============================================================
log.info("=" * 60)
log.info("VIDEO PATTERNS")
log.info("=" * 60)

video_patterns = [
    ("video_mp4", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/video/S800.mp4"),
    ("video_webm", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/video/S800.webm"),
    ("video_root", f"https://{BASKET_HOST}/vol{VOL}/part{PART}/{NM_ID}/video.mp4"),
]

for label, url in video_patterns:
    s, body, ct = http_get(url, headers=photo_headers, timeout=8, label=label)
    if s == 200:
        results[f"video_{label}"] = {"status": "OK", "url": url, "ct": ct}
    time.sleep(0.3)

# ============================================================
# SUMMARY
# ============================================================
log.info("=" * 60)
log.info("RESULTS SUMMARY")
log.info("=" * 60)
for k, v in results.items():
    log.info("  %s: %s", k, json.dumps(v, ensure_ascii=False)[:150])

# Сохранить
with open("diag_wb_v6_out.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
log.info("Saved: diag_wb_v6_out.json")
print("DONE")
