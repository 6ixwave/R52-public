"""Диагностика v44: фиксы после v43.

WB:      API v4 + HTML с полными sec-ch-ua заголовками
Ozon:    cooldown 30с + 2 ID + полные браузерные заголовки
Avito:   chrome impersonation (не iphone), home + API + 1 ad
Farpost: 3-й ID для подтверждения

Запуск: C:\Python315\python.exe diag_v44_all.py
Результат: diag_v44_results.json
Лог:     diag_v44_run.log
"""

import json
import logging
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

LOG_FILE = PROJECT / "diag_v44_run.log"
RESULT_FILE = PROJECT / "diag_v44_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v44")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v44",
    "timestamp": NOW,
    "platforms": {},
}

try:
    from curl_cffi import requests as cffi_req
    CFFI_OK = True
except ImportError:
    CFFI_OK = False
    log.warning("curl_cffi не найден")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Полные браузерные заголовки (max realism)
FULL_BROWSER_HEADERS = {
    "User-Agent": UA_CHROME,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# JSON API заголовки
JSON_HEADERS = {
    "User-Agent": UA_CHROME,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def cffi_get(url, headers=None, timeout=25, impersonate="chrome"):
    h = dict(FULL_BROWSER_HEADERS)
    if headers:
        h.update(headers)
    r = cffi_req.get(url, headers=h, timeout=timeout, impersonate=impersonate, allow_redirects=True)
    return r


def safe_text(r, n=5000):
    if hasattr(r, 'text'):
        return r.text[:n]
    return r.content[:n].decode('utf-8', errors='replace')


# ============================================================
# 1. WILDBERRIES
# ============================================================
def test_wb():
    log.info("=== WB ===")
    wb = {"api_v4": [], "html": []}

    wb_ids = ["199298871", "314337853"]

    # --- A. API v4 (full JSON headers) ---
    for nm in wb_ids:
        # dest=-1257786 = Москва, попробуем и без dest
        url = f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&ab_testing=false&nm={nm}"
        entry = {"nm": nm, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers=JSON_HEADERS, timeout=15)
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            if r.status_code == 200:
                data = r.json()
                products = None
                if isinstance(data, dict):
                    if "data" in data and isinstance(data["data"], dict):
                        products = data["data"].get("products")
                    elif "products" in data:
                        products = data["products"]
                entry["has_products"] = bool(products)
                if products:
                    p = products[0]
                    entry["name"] = p.get("name", "")[:120]
                    entry["brand"] = p.get("brand", "")
                    entry["priceU"] = p.get("priceU")
                    entry["salePriceU"] = p.get("salePriceU")
                    entry["sale_pct"] = p.get("sale")
                    entry["pics"] = p.get("pics")
                    entry["totalQuantity"] = p.get("totalQuantity")
                    entry["status"] = "PARSED"
                else:
                    entry["status"] = "NO_PRODUCTS"
                    entry["keys"] = list(data.keys())[:10] if isinstance(data, dict) else str(data)[:200]
            else:
                body = safe_text(r, 500)
                entry["status"] = f"HTTP_{r.status_code}"
                entry["body_snippet"] = body
                entry["has_wbaas"] = any(x in body.lower() for x in ["wbaas", "antibot", "challenge", "browser-check"])
                entry["has_json"] = body.strip().startswith("{")
            # Заголовки ответа
            entry["resp_headers"] = dict(r.headers) if hasattr(r, 'headers') else {}
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        wb["api_v4"].append(entry)
        log.info("WB v4 nm=%s: %s (%.1fs)", nm, entry.get("status"), entry.get("time_s", 0))
        time.sleep(3)

    # --- B. HTML (полные заголовки + sec-ch-ua) ---
    for nm in wb_ids[:1]:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        entry = {"nm": nm, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers=None, timeout=20)
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = safe_text(r, 8000)
            entry["has_wbaas"] = any(x in text.lower() for x in ["wbaas", "antibot", "browser-check"])
            entry["has_challenge"] = "challenge" in text.lower()
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            # Проверяем meta-теги
            entry["has_og_title"] = "og:title" in text
            entry["has_jsonld"] = "application/ld+json" in text
            entry["has_state"] = "__STATE__" in text or "data-state" in text
            if r.status_code == 200:
                entry["status"] = "OK"
                from r52.collectors.wb import WBCollector
                col = WBCollector(use_proxies=False)
                parsed = col.parse_page(r, nm, url, "")
                if parsed:
                    entry["status"] = "PARSED"
                    entry["parsed_title"] = parsed["fields"].get("title", {}).get("value", "")[:100]
                    entry["prices"] = len(parsed.get("price_candidates", []))
                    entry["media"] = len(parsed.get("media", {}).get("items", []))
            else:
                entry["status"] = f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        wb["html"].append(entry)
        log.info("WB HTML nm=%s: %s (%.1fs)", nm, entry.get("status"), entry.get("time_s", 0))

    results["platforms"]["WB"] = wb


# ============================================================
# 2. OZON (cooldown 20с в начале)
# ============================================================
def test_ozon():
    log.info("=== OZON (cooldown 20s) ===")
    time.sleep(20)

    oz = []
    ozon_ids = ["1600562267", "1317678381"]

    for oid in ozon_ids:
        url = f"https://www.ozon.ru/product/{oid}/"
        entry = {"id": oid, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers=None, timeout=30)
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = safe_text(r, 10000)
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            entry["has_data_widget"] = "data-widget" in text
            entry["has_error_widget"] = 'data-widget="error"' in text
            entry["has_og"] = "og:title" in text
            if r.status_code == 200:
                entry["status"] = "OK"
                from r52.collectors.ozon import OzonCollector
                col = OzonCollector(use_proxies=False)
                parsed = col.parse_page(r, oid, url, "")
                if parsed:
                    entry["status"] = "PARSED"
                    entry["parsed_title"] = parsed["fields"].get("title", {}).get("value", "")[:100]
                    entry["prices"] = len(parsed.get("price_candidates", []))
                    entry["media"] = len(parsed.get("media", {}).get("items", []))
                    entry["availability"] = parsed.get("availability")
                    entry["category"] = (parsed.get("category_path") or "")[:80]
                else:
                    entry["status"] = "PARSE_NONE"
            else:
                entry["status"] = f"HTTP_{r.status_code}"
                entry["body_snippet"] = safe_text(r, 300)
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        oz.append(entry)
        log.info("OZON %s: %s (%.1fs)", oid, entry.get("status"), entry.get("time_s", 0))
        time.sleep(5)

    results["platforms"]["OZON"] = oz


# ============================================================
# 3. AVITO (chrome impersonation)
# ============================================================
def test_avito():
    log.info("=== AVITO ===")
    av = {"home": {}, "api": [], "ad": []}

    # --- A. Главная ---
    t0 = time.monotonic()
    try:
        r = cffi_get("https://www.avito.ru/", headers=None, timeout=20)
        av["home"] = {
            "http": r.status_code,
            "bytes": len(r.content),
            "time_s": round(time.monotonic() - t0, 2),
            "has_ip_limit": "доступ ограничен" in safe_text(r, 5000).lower(),
            "has_captcha": any(x in safe_text(r, 5000).lower() for x in ["captcha", "smartcaptcha"]),
        }
        log.info("AVITO home: HTTP %d", r.status_code)
    except Exception as e:
        av["home"] = {"error": str(e)[:200]}

    time.sleep(5)

    # --- B. API /web/1/js/items ---
    api_url = "https://www.avito.ru/web/1/js/items?path=transport&f=0&q=%D1%82%D0%B5%D0%BB%D0%B5%D1%84%D0%BE%D0%BD&maxPrice=100000"
    entry = {"url": api_url}
    t0 = time.monotonic()
    try:
        r = cffi_get(api_url, headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.avito.ru/transport",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }, timeout=20)
        entry["http"] = r.status_code
        entry["bytes"] = len(r.content)
        entry["time_s"] = round(time.monotonic() - t0, 2)
        if r.status_code == 200:
            data = r.json()
            items = data.get("items", [])
            entry["items_count"] = len(items)
            if items:
                first = items[0]
                entry["first_id"] = first.get("id")
                entry["first_title"] = first.get("title", "")[:100]
                price = first.get("price")
                entry["first_price"] = price.get("value") if isinstance(price, dict) else price
                entry["first_url"] = (first.get("url") or "")[:120]
            entry["status"] = "PARSED"
        else:
            entry["status"] = f"HTTP_{r.status_code}"
            entry["body"] = safe_text(r, 300)
    except Exception as e:
        entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
    av["api"].append(entry)
    log.info("AVITO API: %s", entry.get("status"))

    time.sleep(5)

    # --- C. Карточка (desktop, chrome) ---
    # Пробуем ID из API или известный
    ad_ids = ["1004567890"]
    for aid in ad_ids:
        url = f"https://www.avito.ru/ad/{aid}"
        entry = {"id": aid, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers=None, timeout=20)
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["final_url"] = str(r.url) if hasattr(r, 'url') else ""
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = safe_text(r, 8000)
            entry["has_captcha"] = any(x in text.lower() for x in ["captcha", "recaptcha", "smartcaptcha"])
            entry["has_ip_limit"] = "доступ ограничен" in text.lower() or "проблема с ip" in text.lower()
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            entry["has_jsonld"] = "application/ld+json" in text
            entry["has_state"] = "__PRELOADED_STATE__" in text or "data-state" in text
            entry["status"] = "OK" if r.status_code == 200 else f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        av["ad"].append(entry)
        log.info("AVITO ad %s: %s", aid, entry.get("status"))

    results["platforms"]["AVITO"] = av


# ============================================================
# 4. FARPOST (3-й ID + проверка данных)
# ============================================================
def test_farpost():
    log.info("=== FARPOST ===")
    fp = []

    fp_urls = [
        {
            "id": "125103161",
            "url": "https://www.farpost.ru/kemerovo/zapchasti/akpp-nissan-mr20de-ustanovka-garantija-reof10a-gb57-125103161.html",
            "hint": "АКПП Nissan MR20DE",
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

    for item in fp_urls:
        entry = {"id": item["id"], "url": item["url"], "hint": item["hint"]}
        t0 = time.monotonic()
        try:
            r = cffi_get(item["url"], headers=None, timeout=20)
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = safe_text(r, 10000)
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            if r.status_code == 200:
                entry["status"] = "OK"
                from r52.collectors.farpost import FarpostCollector
                col = FarpostCollector(use_proxies=False)
                parsed = col.parse_page(r, item["id"], item["url"], item["hint"])
                if parsed:
                    entry["status"] = "PARSED"
                    entry["parsed_title"] = parsed["fields"].get("title", {}).get("value", "")[:100]
                    entry["prices"] = parsed.get("price_candidates", [])
                    entry["media_count"] = len(parsed.get("media", {}).get("items", []))
                    entry["category"] = (parsed.get("category_path") or "")[:80]
                    entry["desc_len"] = len(parsed["fields"].get("description", {}).get("value", ""))
                    entry["availability"] = parsed.get("availability")
                    # Поля
                    entry["fields_keys"] = list(parsed.get("fields", {}).keys())
                else:
                    entry["status"] = "PARSE_NONE"
                    entry["html_len"] = len(text)
            else:
                entry["status"] = f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        fp.append(entry)
        log.info("FARPOST %s: %s (%.1fs)", item["id"], entry.get("status"), entry.get("time_s", 0))
        time.sleep(4)

    results["platforms"]["FARPOST"] = fp


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== diag_v44 START | cffi=%s ===", CFFI_OK)
    t0 = time.monotonic()

    for name, fn in [("WB", test_wb), ("OZON", test_ozon), ("AVITO", test_avito), ("FARPOST", test_farpost)]:
        try:
            fn()
        except Exception as e:
            log.exception("Platform %s fatal: %s", name, e)
            results["platforms"][name] = {"fatal": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-500:]}

    elapsed = round(time.monotonic() - t0, 1)
    results["total_time_s"] = elapsed
    log.info("=== diag_v44 DONE in %.1fs ===", elapsed)

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved: %s (%d B)", RESULT_FILE, RESULT_FILE.stat().st_size)


if __name__ == "__main__":
    main()
