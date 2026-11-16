"""Расширенная диагностика: тестируем несколько URL-паттернов на каждый источник.

Цель: найти, какие эндпоинты реально возвращают данные с текущего IP без прокси.
"""
import json
import time
import traceback

import requests

HEADERS_WB = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}

HEADERS_OZON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

HEADERS_AVITO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

HEADERS_FARPOST = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def test(label, url, headers, timeout=(8, 20)):
    print(f"\n--- {label} ---")
    print(f"    URL: {url}")
    start = time.monotonic()
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        elapsed = time.monotonic() - start
        size = len(r.content)
        ct = r.headers.get("Content-Type", "?")
        print(f"    Status: {r.status_code} | Time: {elapsed:.1f}s | Size: {size} bytes | CT: {ct}")
        # Показываем начало тела
        preview = r.text[:500] if r.text else "(binary)"
        print(f"    Preview: {preview[:400]!r}")
        # Проверяем, похож ли ответ на JSON
        if ct and "json" in ct.lower():
            try:
                data = r.json()
                keys = list(data.keys())[:10] if isinstance(data, dict) else f"list[{len(data)}]"
                print(f"    JSON keys: {keys}")
            except Exception:
                pass
        return r.status_code, size
    except requests.exceptions.Timeout:
        print(f"    TIMEOUT ({time.monotonic()-start:.1f}s)")
    except requests.exceptions.ConnectionError as e:
        print(f"    CONN_ERR ({time.monotonic()-start:.1f}s): {str(e)[:200]}")
    except Exception as e:
        print(f"    ERR ({time.monotonic()-start:.1f}s): {type(e).__name__}: {str(e)[:200]}")
    return None, 0


# ============================================================
print("=" * 70)
print("WILDBERRIES — 3 паттерна")
print("=" * 70)

# 1. Классическая HTML-страница
test("WB-HTML", "https://www.wildberries.ru/catalog/199298871/detail.aspx", HEADERS_WB)
time.sleep(3)

# 2. card.wb.ru JSON API (публичный, часто без анти-бота)
test("WB-CARD-API", "https://card.wb.ru/cards/v2/detail?nm=199298871", HEADERS_WB)
time.sleep(3)

# 3. wbcontent JSON API
test("WB-CONTENT-API", "https://wbx-content-v2.wbcontent.ru/cards/v2/detail?nm=199298871", HEADERS_WB)
time.sleep(3)

# 4. basket API (старый, но иногда работает)
test("WB-BASKET", "https://basket-5.wbbasket.ru/vol1992/part199298/199298871/info/199298871-info.json", HEADERS_WB)
time.sleep(3)

# ============================================================
print("\n" + "=" * 70)
print("OZON — 3 паттерна")
print("=" * 70)

# 1. HTML-страница
test("OZON-HTML", "https://www.ozon.ru/product/1600562267/", HEADERS_OZON, timeout=(8, 30))
time.sleep(3)

# 2. Composer API (JSON, внутренний)
test("OZON-COMPOSER", "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/", HEADERS_OZON, timeout=(8, 30))
time.sleep(3)

# 3. Mobile
test("OZON-MOBILE", "https://m.ozon.ru/product/1600562267/", HEADERS_OZON, timeout=(8, 30))
time.sleep(3)

# ============================================================
print("\n" + "=" * 70)
print("AVITO — 2 паттерна")
print("=" * 70)

# 1. HTML
test("AVITO-HTML", "https://www.avito.ru/ad/1096783029", HEADERS_AVITO, timeout=(8, 20))
time.sleep(3)

# 2. Mobile / different path
test("AVITO-MOBILE", "https://m.avito.ru/ad/1096783029", HEADERS_AVITO, timeout=(8, 20))
time.sleep(3)

# ============================================================
print("\n" + "=" * 70)
print("FARPOST — 2 паттерна")
print("=" * 70)

# 1. HTML
test("FARPOST-HTML", "https://www.farpost.ru/item/18530297982", HEADERS_FARPOST, timeout=(8, 20))
time.sleep(3)

# 2. API (если есть)
test("FARPOST-API", "https://api.farpost.ru/item/18530297982", HEADERS_FARPOST, timeout=(8, 20))
time.sleep(3)

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
