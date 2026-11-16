"""Диагностика v43: сжатый прогон 4 площадок.

Фокус:
- WB: API v4 (curl_cffi chrome) + HTML (HTTP 498?)
- OZON: 2 ID через curl_cffi
- AVITO: mobile + API /web/1/js/items
- FARPOST: 2 живых URL

Запуск: C:\Python315\python.exe diag_v43_all.py
Результат: diag_v43_results.json
Лог:     diag_v43_run.log
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

LOG_FILE = PROJECT / "diag_v43_run.log"
RESULT_FILE = PROJECT / "diag_v43_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("diag_v43")

TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()

results = {
    "version": "v43",
    "timestamp": NOW,
    "platforms": {},
}

# --- curl_cffi session (browser TLS fingerprint) ---
try:
    from curl_cffi import requests as cffi_req
    CFFI_OK = True
except ImportError:
    CFFI_OK = False
    log.warning("curl_cffi не найден, fallback на requests")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def cffi_get(url, headers=None, timeout=(10, 25), impersonate="chrome"):
    """GET через curl_cffi с browser impersonation."""
    if CFFI_OK:
        h = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}
        if headers:
            h.update(headers)
        r = cffi_req.get(url, headers=h, timeout=timeout[1], impersonate=impersonate, allow_redirects=True)
        return r
    else:
        import requests
        h = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}
        if headers:
            h.update(headers)
        return requests.get(url, headers=h, timeout=timeout, allow_redirects=True)


# ============================================================
# 1. WILDBERRIES
# ============================================================
def test_wb():
    log.info("=== WB ===")
    wb = {"api_v4": [], "html": []}

    wb_ids = ["199298871", "314337853"]

    # --- A. API v4 ---
    for nm in wb_ids:
        url = f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&ab_testing=false&nm={nm}"
        entry = {"nm": nm, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.wildberries.ru/",
                "Origin": "https://www.wildberries.ru",
            })
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
                    entry["pics"] = p.get("pics")
                    entry["totalQuantity"] = p.get("totalQuantity")
                    entry["status"] = "PARSED"
                else:
                    entry["status"] = "NO_PRODUCTS"
                    entry["keys"] = list(data.keys())[:10] if isinstance(data, dict) else str(data)[:200]
            elif r.status_code in (403, 498):
                entry["status"] = f"HTTP_{r.status_code}"
                body = r.text[:500] if hasattr(r, 'text') else r.content[:500].decode('utf-8', errors='replace')
                entry["body_snippet"] = body
                entry["has_wbaas"] = "wbaas" in body.lower() or "antibot" in body.lower() or "challenge" in body.lower()
            else:
                entry["status"] = f"HTTP_{r.status_code}"
                entry["body_snippet"] = (r.text[:300] if hasattr(r, 'text') else r.content[:300].decode('utf-8', errors='replace'))
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        wb["api_v4"].append(entry)
        log.info("WB v4 nm=%s: %s (%.1fs)", nm, entry.get("status"), entry.get("time_s", 0))
        time.sleep(2)

    # --- B. HTML карточка ---
    for nm in wb_ids[:1]:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        entry = {"nm": nm, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            })
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = r.text if hasattr(r, 'text') else r.content.decode('utf-8', errors='replace')
            entry["has_wbaas"] = "wbaas" in text.lower() or "antibot" in text.lower()
            entry["has_challenge"] = "challenge" in text.lower()
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            if r.status_code == 200:
                entry["status"] = "OK"
                # Быстрый парс
                from r52.collectors.wb import WBCollector
                col = WBCollector(use_proxies=False)
                parsed = col.parse_page(r, nm, url, "")
                if parsed:
                    entry["parsed_title"] = parsed["fields"].get("title", {}).get("value", "")[:100]
                    entry["prices"] = len(parsed.get("price_candidates", []))
                    entry["media"] = len(parsed.get("media", {}).get("items", []))
                    entry["status"] = "PARSED"
            else:
                entry["status"] = f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        wb["html"].append(entry)
        log.info("WB HTML nm=%s: %s (%.1fs)", nm, entry.get("status"), entry.get("time_s", 0))

    results["platforms"]["WB"] = wb


# ============================================================
# 2. OZON
# ============================================================
def test_ozon():
    log.info("=== OZON ===")
    oz = []
    ozon_ids = ["1600562267", "1317678381"]

    for oid in ozon_ids:
        url = f"https://www.ozon.ru/product/{oid}/"
        entry = {"id": oid, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            })
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = r.text if hasattr(r, 'text') else r.content.decode('utf-8', errors='replace')
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            entry["has_data_widget"] = "data-widget" in text
            entry["has_error_widget"] = 'data-widget="error"' in text
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
                else:
                    entry["status"] = "PARSE_NONE"
            else:
                entry["status"] = f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        oz.append(entry)
        log.info("OZON %s: %s (%.1fs)", oid, entry.get("status"), entry.get("time_s", 0))
        time.sleep(3)

    results["platforms"]["OZON"] = oz


# ============================================================
# 3. AVITO
# ============================================================
def test_avito():
    log.info("=== AVITO ===")
    av = {"mobile": [], "api": [], "home": {}}

    # --- A. Главная (IP check) ---
    t0 = time.monotonic()
    try:
        r = cffi_get("https://www.avito.ru/", headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        av["home"] = {
            "http": r.status_code,
            "bytes": len(r.content),
            "time_s": round(time.monotonic() - t0, 2),
            "has_ip_limit": "доступ ограничен" in (r.text[:5000] if hasattr(r, 'text') else "") .lower(),
        }
        log.info("AVITO home: HTTP %d", r.status_code)
    except Exception as e:
        av["home"] = {"error": str(e)[:200]}

    time.sleep(3)

    # --- B. Mobile ad ---
    # Берём ID из API или используем известные
    mobile_ids = ["1004567890"]
    for aid in mobile_ids:
        url = f"https://m.avito.ru/ad/{aid}"
        entry = {"id": aid, "url": url}
        t0 = time.monotonic()
        try:
            r = cffi_get(url, headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }, impersonate="iphone")
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["final_url"] = str(r.url) if hasattr(r, 'url') else ""
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = (r.text[:5000] if hasattr(r, 'text') else r.content[:5000].decode('utf-8', errors='replace'))
            entry["has_captcha"] = any(x in text.lower() for x in ["captcha", "recaptcha", "smartcaptcha"])
            entry["has_ip_limit"] = "доступ ограничен" in text.lower() or "проблема с ip" in text.lower()
            tm = re.search(r"<title[^>]*>([^<]+)", text, re.I)
            entry["title"] = tm.group(1)[:120] if tm else ""
            entry["status"] = "OK" if r.status_code == 200 else f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        av["mobile"].append(entry)
        log.info("AVITO mobile %s: %s", aid, entry.get("status"))
        time.sleep(3)

    # --- C. API /web/1/js/items ---
    api_url = "https://www.avito.ru/web/1/js/items?path=transport&f=0&q=телефон&maxPrice=100000"
    entry = {"url": api_url}
    t0 = time.monotonic()
    try:
        r = cffi_get(api_url, headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.avito.ru/transport",
            "X-Requested-With": "XMLHttpRequest",
        })
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
            entry["body"] = (r.text[:300] if hasattr(r, 'text') else r.content[:300].decode('utf-8', errors='replace'))
    except Exception as e:
        entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
    av["api"].append(entry)
    log.info("AVITO API: %s", entry.get("status"))

    results["platforms"]["AVITO"] = av


# ============================================================
# 4. FARPOST
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
    ]

    for item in fp_urls:
        entry = {"id": item["id"], "url": item["url"], "hint": item["hint"]}
        t0 = time.monotonic()
        try:
            r = cffi_get(item["url"], headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            })
            entry["http"] = r.status_code
            entry["bytes"] = len(r.content)
            entry["time_s"] = round(time.monotonic() - t0, 2)
            text = r.text if hasattr(r, 'text') else r.content.decode('utf-8', errors='replace')
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
                else:
                    entry["status"] = "PARSE_NONE"
                    entry["html_len"] = len(text)
            else:
                entry["status"] = f"HTTP_{r.status_code}"
        except Exception as e:
            entry["status"] = f"ERR: {type(e).__name__}: {str(e)[:200]}"
        fp.append(entry)
        log.info("FARPOST %s: %s (%.1fs)", item["id"], entry.get("status"), entry.get("time_s", 0))
        time.sleep(3)

    results["platforms"]["FARPOST"] = fp


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== diag_v43 START | cffi=%s ===", CFFI_OK)
    t0 = time.monotonic()

    for name, fn in [("WB", test_wb), ("OZON", test_ozon), ("AVITO", test_avito), ("FARPOST", test_farpost)]:
        try:
            fn()
        except Exception as e:
            log.exception("Platform %s fatal: %s", name, e)
            results["platforms"][name] = {"fatal": f"{type(e).__name__}: {e}"}

    elapsed = round(time.monotonic() - t0, 1)
    results["total_time_s"] = elapsed
    log.info("=== diag_v43 DONE in %.1fs ===", elapsed)

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved: %s (%d B)", RESULT_FILE, RESULT_FILE.stat().st_size)


if __name__ == "__main__":
    main()
