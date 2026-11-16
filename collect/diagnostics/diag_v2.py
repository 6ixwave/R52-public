"""Диагностика v2: точечные тесты эндпоинтов с правильными заголовками.

Проверяем:
- WB: card.wb.ru с Origin/Referer, basket с разными номерами, wbx-content
- Ozon: HTML с verify=False, composer API
- Avito: несколько живых ID (ищем через поиск на сайте)
- Farpost: несколько живых ID
"""
import json
import time
import traceback

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def test(label, url, headers=None, timeout=(8, 20), verify=True):
    print(f"\n--- {label} ---")
    print(f"    URL: {url}")
    if headers is None:
        headers = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}
    start = time.monotonic()
    try:
        r = requests.get(url, headers=headers, timeout=timeout, verify=verify, allow_redirects=True)
        elapsed = time.monotonic() - start
        size = len(r.content)
        ct = r.headers.get("Content-Type", "?")
        print(f"    Status: {r.status_code} | Time: {elapsed:.1f}s | Size: {size}B | CT: {ct}")
        preview = r.text[:600] if r.text else "(binary)"
        print(f"    Preview: {preview[:500]!r}")
        if "json" in ct.lower() or r.text.strip().startswith(("{", "[")):
            try:
                data = r.json()
                if isinstance(data, dict):
                    print(f"    JSON keys: {list(data.keys())[:15]}")
                elif isinstance(data, list):
                    print(f"    JSON list[{len(data)}], first keys: {list(data[0].keys())[:15] if data else '[]'}")
            except Exception as e:
                print(f"    JSON parse err: {e}")
        return r.status_code, size
    except requests.exceptions.SSLError as e:
        print(f"    SSL_ERR ({time.monotonic()-start:.1f}s): {str(e)[:200]}")
    except requests.exceptions.ConnectionError as e:
        print(f"    CONN_ERR ({time.monotonic()-start:.1f}s): {str(e)[:200]}")
    except requests.exceptions.Timeout:
        print(f"    TIMEOUT ({time.monotonic()-start:.1f}s)")
    except Exception as e:
        print(f"    ERR ({time.monotonic()-start:.1f}s): {type(e).__name__}: {str(e)[:200]}")
    return None, 0


# ============================================================
print("=" * 70)
print("WILDBERRIES")
print("=" * 70)

# card.wb.ru — JSON API, нужен Origin + Referer
h_wb_api = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}
test("WB-CARD-v2", "https://card.wb.ru/cards/v2/detail?nm=199298871", h_wb_api)
time.sleep(2)

# card.wb.ru — старый v1
test("WB-CARD-v1", "https://card.wb.ru/cards/detail?nm=199298871", h_wb_api)
time.sleep(2)

# basket — пробуем несколько номеров (vol/part зависят от nm)
# nm=199298871 → vol=1992, part=199298
for n in [1, 5, 10, 20, 50]:
    url = f"https://basket-{n}.wbbasket.ru/vol1992/part199298/199298871/info/199298871-info.json"
    code, size = test(f"WB-BASKET-{n}", url, h_wb_api, timeout=(5, 10))
    if code == 200:
        print(f"    >>> FOUND WORKING BASKET: {n}")
        break
    time.sleep(1)

# wbx-content-v2
test("WB-WBX-CONTENT", "https://wbx-content-v2.wbcontent.ru/cards/v2/detail?nm=199298871", h_wb_api, timeout=(5, 10))
time.sleep(2)

# WB HTML с cookie-обходом (заголовок X-WB-*)
h_wb_html = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.google.com/",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
test("WB-HTML-2", "https://www.wildberries.ru/catalog/199298871/detail.aspx", h_wb_html)

# ============================================================
print("\n" + "=" * 70)
print("OZON")
print("=" * 70)

# HTML с verify=False (SSL cert issue на api.ozon.ru)
h_ozon = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
test("OZON-HTML-2", "https://www.ozon.ru/product/1600562267/", h_ozon, timeout=(8, 30))
time.sleep(2)

# Composer API с verify=False
test("OZON-COMPOSER-2", "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/", h_ozon, timeout=(8, 20), verify=False)
time.sleep(2)

# Ozon API v1 (старый)
test("OZON-API-V1", "https://api.ozon.ru/composer-api.bx/page/json?url=/product/1600562267/", h_ozon, timeout=(8, 20), verify=False)
time.sleep(2)

# Ozon: пробуем через www.ozon.ru/api
test("OZON-WWW-API", "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/1600562267/", h_ozon, timeout=(8, 20))

# ============================================================
print("\n" + "=" * 70)
print("AVITO — поиск живого ID через поиск на сайте")
print("=" * 70)

# Сначала найдём живое объявление через поиск
h_avito = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
# Поиск по популярному запросу
test("AVITO-SEARCH", "https://www.avito.ru/search?q=iphone&f=ASgBAgICAkKKAQw", h_avito, timeout=(8, 20))
time.sleep(2)

# Прямые ID — пробуем несколько форматов
for aid in ["1096783029", "1080000000", "1075000000"]:
    test(f"AVITO-{aid}", f"https://www.avito.ru/moscow/ad/{aid}", h_avito, timeout=(8, 15))
    time.sleep(2)

# ============================================================
print("\n" + "=" * 70)
print("FARPOST — поиск живого ID")
print("=" * 70)

h_fp = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
# Поиск
test("FP-SEARCH", "https://www.farpost.ru/search/?q=iphone", h_fp, timeout=(8, 20))
time.sleep(2)

# Прямые ID
for fid in ["18530297982", "18500000000", "18400000000"]:
    test(f"FP-{fid}", f"https://www.farpost.ru/item/{fid}", h_fp, timeout=(8, 15))
    time.sleep(2)

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
