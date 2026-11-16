"""Диагностика v3: WB basket с правильным part, поиск живых ID, Ozon entrypoint."""
import json
import re
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
        preview = r.text[:800] if r.text else "(binary)"
        print(f"    Preview: {preview[:600]!r}")
        data = None
        if "json" in ct.lower() or (r.text and r.text.strip().startswith(("{", "["))):
            try:
                data = r.json()
                if isinstance(data, dict):
                    print(f"    JSON keys: {list(data.keys())[:20]}")
                elif isinstance(data, list):
                    print(f"    JSON list[{len(data)}]")
                    if data and isinstance(data[0], dict):
                        print(f"    First item keys: {list(data[0].keys())[:20]}")
            except Exception as e:
                print(f"    JSON parse err: {e}")
        return r.status_code, size, r, data
    except requests.exceptions.SSLError as e:
        print(f"    SSL_ERR ({time.monotonic()-start:.1f}s): {str(e)[:200]}")
    except requests.exceptions.ConnectionError as e:
        print(f"    CONN_ERR ({time.monotonic()-start:.1f}s): {str(e)[:200]}")
    except requests.exceptions.Timeout:
        print(f"    TIMEOUT ({time.monotonic()-start:.1f}s)")
    except Exception as e:
        print(f"    ERR ({time.monotonic()-start:.1f}s): {type(e).__name__}: {str(e)[:200]}")
        traceback.print_exc()
    return None, 0, None, None


# ============================================================
print("=" * 70)
print("WB BASKET — правильный расчёт vol/part")
print("=" * 70)

nm = 199298871
vol = nm // 100000  # 1992
part = nm // 100    # 1992988
print(f"nm={nm} -> vol={vol}, part={part}")

h_wb = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# info.json — основные данные карточки
for n in [10, 20, 50, 1, 5]:
    url = f"https://basket-{n}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/{nm}-info.json"
    code, size, r, data = test(f"WB-INFO-{n}", url, h_wb, timeout=(5, 10))
    if code == 200 and data:
        path = f"wb_info_{n}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"    >>> BASKET {n} WORKS! Saved {path}")
        break
    time.sleep(1)

# card.json — расширенная карточка
for n in [10, 20, 50]:
    url = f"https://basket-{n}.wbbasket.ru/vol{vol}/part{part}/{nm}/card/{nm}-card.json"
    code, size, r, data = test(f"WB-CARD-{n}", url, h_wb, timeout=(5, 10))
    if code == 200 and data:
        path = f"wb_card_{n}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"    >>> CARD {n} WORKS! Saved {path}")
        break
    time.sleep(1)

# Фото: пробуем первый (images/big)
for n in [10, 20, 50]:
    url = f"https://basket-{n}.wbbasket.ru/vol{vol}/part{part}/{nm}/images/big/{nm}-1.jpg"
    code, size, r, _ = test(f"WB-IMG-{n}", url, h_wb, timeout=(5, 10))
    if code == 200:
        print(f"    >>> IMAGE WORKS on basket-{n}, size={size}")
        break
    time.sleep(1)

# ============================================================
print("\n" + "=" * 70)
print("WB CARD.WB.RU — разные заголовки и версии")
print("=" * 70)

# Вариант 1: минимальные
h1 = {"User-Agent": UA, "Accept": "application/json"}
test("WB-API-min", f"https://card.wb.ru/cards/v2/detail?nm={nm}", h1, timeout=(5, 10))
time.sleep(2)

# Вариант 2: с Origin/Referer + sec-ch + параметры
h2 = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/catalog/199298871/detail.aspx",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}
test("WB-API-full", f"https://card.wb.ru/cards/v2/detail?nm={nm}&appType=1&curr=rub&dest=-1257786&spp=30&ab_testing=false&lang=ru", h2, timeout=(5, 10))
time.sleep(2)

# Вариант 3: v4 (по Hashnode 2026)
test("WB-API-v4", f"https://card.wb.ru/cards/v4/detail?nm={nm}", h2, timeout=(5, 10))

# ============================================================
print("\n" + "=" * 70)
print("OZON — entrypoint-api + www.ozon.ru/api")
print("=" * 70)

h_ozon = {
    "User-Agent": UA,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.ozon.ru/",
}

# entrypoint-api (по GitHub JTJag)
test("OZON-ENTRYPOINT", "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=/product/1600562267/", h_ozon, timeout=(8, 20))
time.sleep(2)

# composer через www
test("OZON-COMPOSER-WWW", "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/1600562267/", h_ozon, timeout=(8, 20))

# ============================================================
print("\n" + "=" * 70)
print("FARPOST — поиск живых ID через главную и категории")
print("=" * 70)

h_fp = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Главная → ищем ссылки /item/
code, size, r, _ = test("FP-HOME", "https://www.farpost.ru/", h_fp, timeout=(8, 20))
if r and r.status_code == 200:
    item_ids = re.findall(r'/(?:item|l)/(\d{8,15})', r.text)
    print(f"    Found item IDs on home: {item_ids[:10]}")
    data_ids = re.findall(r'data-item-id=["\'](\d+)["\']', r.text)
    print(f"    data-item-id: {data_ids[:10]}")
    ad_links = re.findall(r'href="(/[^"]*?/(\d{8,15}))"', r.text)
    print(f"    Ad links: {ad_links[:10]}")

time.sleep(2)

# Категория "Телефоны"
test("FP-PHONES", "https://www.farpost.ru/telefony/", h_fp, timeout=(8, 20))
time.sleep(2)

# Прямой поиск (разный URL pattern)
test("FP-SEARCH2", "https://www.farpost.ru/search/?q=iphone&cityId=1", h_fp, timeout=(8, 20))

# ============================================================
print("\n" + "=" * 70)
print("AVITO — поиск живого ID")
print("=" * 70)

h_av = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Главная
code, size, r, _ = test("AV-HOME", "https://www.avito.ru/", h_av, timeout=(8, 20))
if r and r.status_code == 200:
    ad_ids = re.findall(r'/ad/(\d{9,15})', r.text)
    print(f"    Ad IDs on home: {ad_ids[:10]}")

time.sleep(2)

# Категория телефоны
code, size, r, _ = test("AV-PHONES", "https://www.avito.ru/moscow/telefony", h_av, timeout=(8, 20))
if r and r.status_code == 200:
    ad_ids = re.findall(r'/ad/(\d{9,15})', r.text)
    print(f"    Ad IDs on phones: {ad_ids[:10]}")

# ============================================================
print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
