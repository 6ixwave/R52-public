"""Диагностика v42: все 4 маркетплейса.

WB:      новый endpoint /cards/v4/detail + HTML fallback
Ozon:    подтверждение на 3 ID
Avito:   мобильная версия + API /web/1/js/items
Farpost: живые URL из поиска (Kemerovo, Vladivostok)

Запуск: C:\\Python315\\python.exe diag_v42_all.py
Результат: diag_v42_results.json
Лог:     diag_v42_run.log
"""

import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Путь к проекту ---
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

# --- Логирование ---
LOG_FILE = PROJECT / "diag_v42_run.log"
RESULT_FILE = PROJECT / "diag_v42_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v42")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v42",
    "timestamp": NOW,
    "platforms": {},
}

# ============================================================
# 1. WILDBERRIES — новый endpoint v4 + HTML
# ============================================================
def test_wb():
    log.info("=== WB: тестирование ===")
    wb_results = {"api_v4": [], "html": []}

    # Известные WB nmId (артикулы)
    wb_ids = ["199298871", "314337853", "245678901"]

    # --- A. API v4 ---
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.wildberries.ru/",
            "Origin": "https://www.wildberries.ru",
        }
        for nm in wb_ids:
            url = (
                f"https://card.wb.ru/cards/v4/detail"
                f"?appType=1&curr=rub&dest=-1257786&spp=30&ab_testing=false&nm={nm}"
            )
            entry = {"nm": nm, "url": url}
            t0 = time.monotonic()
            try:
                resp = requests.get(url, headers=headers, timeout=(10, 20))
                entry["http"] = resp.status_code
                entry["bytes"] = len(resp.content)
                entry["time_s"] = round(time.monotonic() - t0, 2)
                if resp.status_code == 200:
                    data = resp.json()
                    # Структура: {"data": {"products": [...]}} или {"products": [...]}
                    products = None
                    if isinstance(data, dict):
                        if "data" in data and isinstance(data["data"], dict):
                            products = data["data"].get("products")
                        elif "products" in data:
                            products = data["products"]
                    entry["has_products"] = products is not None and len(products) > 0
                    if products:
                        p = products[0]
                        entry["name"] = p.get("name", "")[:120]
                        entry["brand"] = p.get("brand", "")
                        entry["priceU"] = p.get("priceU")
                        entry["salePriceU"] = p.get("salePriceU")
                        entry["sale_pct"] = p.get("sale")
                        entry["pics_count"] = p.get("pics")
                        entry["totalQuantity"] = p.get("totalQuantity")
                        entry["status"] = "PARSED"
                    else:
                        entry["status"] = "NO_PRODUCTS"
                        entry["raw_keys"] = list(data.keys())[:10] if isinstance(data, dict) else str(data)[:200]
                elif resp.status_code == 404:
                    entry["status"] = "NOT_FOUND"
                    entry["body"] = resp.text[:300]
                else:
                    entry["status"] = f"HTTP_{resp.status_code}"
                    entry["body"] = resp.text[:500]
            except requests.exceptions.JSONDecodeError:
                entry["status"] = "JSON_DECODE_ERR"
                entry["body"] = resp.text[:300]
            except Exception as e:
                entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
            wb_results["api_v4"].append(entry)
            log.info("WB API v4 nm=%s: %s", nm, entry.get("status"))
            time.sleep(3)

    except Exception as e:
        log.exception("WB API v4 block error: %s", e)
        wb_results["api_v4_error"] = str(e)

    # --- B. HTML карточки (fallback) ---
    try:
        from r52.collectors.wb import WBCollector
        wb_collector = WBCollector(use_proxies=False)
        for nm in wb_ids[:2]:  # только 2 HTML, чтобы не грузить
            url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
            entry = {"nm": nm, "url": url}
            t0 = time.monotonic()
            try:
                resp = wb_collector.fetch(url)
                entry["time_s"] = round(time.monotonic() - t0, 2)
                if resp is None:
                    entry["status"] = "FETCH_NONE"
                    # Попробуем напрямую
                    import requests
                    r2 = requests.get(url, headers=headers, timeout=(10, 20))
                    entry["direct_http"] = r2.status_code
                    entry["direct_bytes"] = len(r2.content)
                    entry["has_wbaas"] = "wbaas" in r2.text.lower() or "antibot" in r2.text.lower()
                    entry["title_match"] = re.search(r"<title[^>]*>([^<]+)", r2.text, re.I)
                    if entry["title_match"]:
                        entry["title"] = entry["title_match"].group(1)[:120]
                    entry["status"] = f"DIRECT_{r2.status_code}"
                else:
                    entry["http"] = resp.status_code
                    entry["bytes"] = len(resp.text)
                    parsed = wb_collector.parse_page(resp, nm, url, "")
                    if parsed:
                        entry["status"] = "PARSED"
                        entry["title"] = parsed["fields"].get("title", {}).get("value", "")[:120]
                        entry["prices"] = len(parsed.get("price_candidates", []))
                        entry["media"] = len(parsed.get("media", {}).get("items", []))
                    else:
                        entry["status"] = "PARSE_NONE"
            except Exception as e:
                entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
            wb_results["html"].append(entry)
            log.info("WB HTML nm=%s: %s", nm, entry.get("status"))
    except Exception as e:
        log.exception("WB HTML block error: %s", e)
        wb_results["html_error"] = str(e)

    results["platforms"]["WB"] = wb_results


# ============================================================
# 2. OZON — подтверждение на 3 ID
# ============================================================
def test_ozon():
    log.info("=== OZON: тестирование ===")
    ozon_results = []

    ozon_ids = ["1600562267", "1317678381", "229225961"]

    try:
        from r52.collectors.ozon import OzonCollector
        oz_collector = OzonCollector(use_proxies=False)

        for oid in ozon_ids:
            url = f"https://www.ozon.ru/product/{oid}/"
            entry = {"id": oid, "url": url}
            t0 = time.monotonic()
            try:
                resp = oz_collector.fetch(url)
                entry["time_s"] = round(time.monotonic() - t0, 2)
                if resp is None:
                    # Direct
                    import requests
                    r2 = requests.get(url, timeout=(10, 30), headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                    entry["direct_http"] = r2.status_code
                    entry["direct_bytes"] = len(r2.content)
                    entry["status"] = f"DIRECT_{r2.status_code}"
                    if r2.status_code == 200:
                        parsed = oz_collector.parse_page(r2, oid, url, "")
                        if parsed:
                            entry["status"] = "PARSED"
                            entry["title"] = parsed["fields"].get("title", {}).get("value", "")[:120]
                            entry["prices"] = len(parsed.get("price_candidates", []))
                            entry["media"] = len(parsed.get("media", {}).get("items", []))
                            entry["has_jsonld"] = bool(parsed.get("fields", {}).get("description"))
                        else:
                            entry["status"] = "PARSE_NONE"
                else:
                    entry["http"] = resp.status_code
                    entry["bytes"] = len(resp.text)
                    parsed = oz_collector.parse_page(resp, oid, url, "")
                    if parsed:
                        entry["status"] = "PARSED"
                        entry["title"] = parsed["fields"].get("title", {}).get("value", "")[:120]
                        entry["prices"] = len(parsed.get("price_candidates", []))
                        entry["media"] = len(parsed.get("media", {}).get("items", []))
                        entry["availability"] = parsed.get("availability")
                        entry["category"] = parsed.get("category_path", "")[:80]
                    else:
                        entry["status"] = "PARSE_NONE"
            except Exception as e:
                entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
            ozon_results.append(entry)
            log.info("OZON %s: %s", oid, entry.get("status"))
    except Exception as e:
        log.exception("OZON block error: %s", e)
        ozon_results.append({"error": str(e)})

    results["platforms"]["OZON"] = ozon_results


# ============================================================
# 3. AVITO — мобильная версия + API
# ============================================================
def test_avito():
    log.info("=== AVITO: тестирование ===")
    avito_results = {"mobile": [], "api": [], "desktop": []}

    # Известные Avito ID (ищем актуальные)
    avito_ids = ["1004567890", "1056789012", "1123456789"]

    try:
        import requests

        # --- A. Мобильная версия m.avito.ru ---
        mobile_headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        for aid in avito_ids:
            url = f"https://m.avito.ru/ad/{aid}"
            entry = {"id": aid, "url": url}
            t0 = time.monotonic()
            try:
                resp = requests.get(url, headers=mobile_headers, timeout=(10, 20), allow_redirects=True)
                entry["http"] = resp.status_code
                entry["final_url"] = str(resp.url)
                entry["bytes"] = len(resp.content)
                entry["time_s"] = round(time.monotonic() - t0, 2)
                text = resp.text[:5000]
                entry["has_captcha"] = any(x in text.lower() for x in ["captcha", "recaptcha", "smartcaptcha"])
                entry["has_ip_limit"] = "доступ ограничен" in text.lower() or "проблема с ip" in text.lower()
                title_m = re.search(r"<title[^>]*>([^<]+)", resp.text, re.I)
                entry["title"] = title_m.group(1)[:120] if title_m else ""
                if resp.status_code == 200 and not entry["has_captcha"]:
                    entry["status"] = "OK"
                elif resp.status_code == 439:
                    entry["status"] = "HTTP_439"
                elif resp.status_code == 404:
                    entry["status"] = "NOT_FOUND"
                else:
                    entry["status"] = f"HTTP_{resp.status_code}"
            except Exception as e:
                entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
            avito_results["mobile"].append(entry)
            log.info("AVITO mobile %s: %s", aid, entry.get("status"))
            time.sleep(5)

        # --- B. API /web/1/js/items ---
        api_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.avito.ru/",
        }
        # Пробуем поиск по категории
        api_url = "https://www.avito.ru/web/1/js/items?path=transport&f=0&q=телефон&maxPrice=100000"
        entry = {"url": api_url}
        t0 = time.monotonic()
        try:
            resp = requests.get(api_url, headers=api_headers, timeout=(10, 20))
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                entry["items_count"] = len(items)
                if items:
                    first = items[0]
                    entry["first_id"] = first.get("id")
                    entry["first_title"] = first.get("title", "")[:100]
                    entry["first_price"] = first.get("price", {}).get("value") if isinstance(first.get("price"), dict) else first.get("price")
                    entry["first_url"] = first.get("url", "")[:120]
                entry["status"] = "PARSED"
            else:
                entry["status"] = f"HTTP_{resp.status_code}"
                entry["body"] = resp.text[:300]
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        avito_results["api"].append(entry)
        log.info("AVITO API: %s", entry.get("status"))

        # --- C. Desktop (короткий тест) ---
        desktop_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        # Главная страница — проверяем, не заблокирован ли IP вообще
        entry = {"url": "https://www.avito.ru/"}
        t0 = time.monotonic()
        try:
            resp = requests.get(entry["url"], headers=desktop_headers, timeout=(10, 20))
            entry["http"] = resp.status_code
            entry["bytes"] = len(resp.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            entry["has_ip_limit"] = "доступ ограничен" in resp.text[:5000].lower()
            entry["status"] = "OK" if resp.status_code == 200 else f"HTTP_{resp.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        avito_results["desktop"].append(entry)
        log.info("AVITO desktop home: %s", entry.get("status"))

    except Exception as e:
        log.exception("AVITO block error: %s", e)
        avito_results["error"] = str(e)

    results["platforms"]["AVITO"] = avito_results


# ============================================================
# 4. FARPOST — живые URL из поиска
# ============================================================
def test_farpost():
    log.info("=== FARPOST: тестирование ===")
    farpost_results = []

    # Живые URL из web_search (ноябрь 2026)
    fp_urls = [
        {
            "id": "125103161",
            "url": "https://www.farpost.ru/kemerovo/zapchasti/akpp-nissan-mr20de-ustanovka-garantija-reof10a-gb57-125103161.html",
            "hint": "АКПП Nissan MR20DE",
        },
        {
            "id": "120069731",
            "url": "https://www.farpost.ru/kemerovo/zapchasti/akpp-citroen-ep6cdt-ustanovka-garantija-dostavka-120069731.html",
            "hint": "АКПП Citroen EP6CDT",
        },
        {
            "id": "16879823067",
            "url": "https://www.farpost.ru/vladivostok/zapchasti/datchik-kisloroda-lynxauto-art-dm-2350-g16879823067.html",
            "hint": "Датчик кислорода LYNXauto",
        },
        {
            "id": "17260583280",
            "url": "https://www.farpost.ru/vladivostok/zapchasti/filtr-toplivnyj-lynxauto-art-lf-1073m-g17260583280.html",
            "hint": "Фильтр топливный LYNXauto",
        },
    ]

    try:
        from r52.collectors.farpost import FarpostCollector
        fp_collector = FarpostCollector(use_proxies=False)

        for item in fp_urls:
            entry = {"id": item["id"], "url": item["url"], "hint": item["hint"]}
            t0 = time.monotonic()
            try:
                resp = fp_collector.fetch(item["url"])
                entry["time_s"] = round(time.monotonic() - t0, 2)
                if resp is None:
                    # Direct
                    import requests
                    r2 = requests.get(item["url"], timeout=(10, 20), headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                    entry["direct_http"] = r2.status_code
                    entry["direct_bytes"] = len(r2.content)
                    entry["status"] = f"DIRECT_{r2.status_code}"
                    if r2.status_code == 200:
                        parsed = fp_collector.parse_page(r2, item["id"], item["url"], item["hint"])
                        if parsed:
                            entry["status"] = "PARSED"
                            entry["title"] = parsed["fields"].get("title", {}).get("value", "")[:120]
                            entry["prices"] = parsed.get("price_candidates", [])
                            entry["media_count"] = len(parsed.get("media", {}).get("items", []))
                            entry["category"] = parsed.get("category_path", "")[:80]
                        else:
                            entry["status"] = "PARSE_NONE"
                    elif r2.status_code == 404:
                        entry["status"] = "NOT_FOUND"
                        entry["title"] = (re.search(r"<title[^>]*>([^<]+)", r2.text, re.I) or [None, ""])[1][:100]
                else:
                    entry["http"] = resp.status_code
                    entry["bytes"] = len(resp.text)
                    parsed = fp_collector.parse_page(resp, item["id"], item["url"], item["hint"])
                    if parsed:
                        entry["status"] = "PARSED"
                        entry["title"] = parsed["fields"].get("title", {}).get("value", "")[:120]
                        entry["prices"] = parsed.get("price_candidates", [])
                        entry["media_count"] = len(parsed.get("media", {}).get("items", []))
                        entry["category"] = parsed.get("category_path", "")[:80]
                        entry["description_len"] = len(parsed["fields"].get("description", {}).get("value", ""))
                    else:
                        entry["status"] = "PARSE_NONE"
                        entry["html_len"] = len(resp.text)
            except Exception as e:
                entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
            farpost_results.append(entry)
            log.info("FARPOST %s: %s", item["id"], entry.get("status"))
    except Exception as e:
        log.exception("FARPOST block error: %s", e)
        farpost_results.append({"error": str(e)})

    results["platforms"]["FARPOST"] = farpost_results


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== diag_v42_all.py START ===")
    log.info("Timestamp: %s", NOW)
    log.info("Python: %s", sys.version)

    t_start = time.monotonic()

    for name, func in [("WB", test_wb), ("OZON", test_ozon), ("AVITO", test_avito), ("FARPOST", test_farpost)]:
        try:
            func()
        except Exception as e:
            log.exception("Platform %s crashed: %s", name, e)
            results["platforms"][name] = {"fatal_error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}

    elapsed = round(time.monotonic() - t_start, 1)
    results["total_time_s"] = elapsed
    log.info("=== diag_v42_all.py DONE in %.1fs ===", elapsed)

    # Сохраняем
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log.info("Results saved: %s (%d bytes)", RESULT_FILE, RESULT_FILE.stat().st_size)


if __name__ == "__main__":
    main()
