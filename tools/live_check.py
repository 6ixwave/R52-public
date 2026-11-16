"""Адресная live-диагностика источников R-52.

Запуск: python tools/live_check.py
Проверяет: DNS, HTTP-статус, размер, время, CAPTCHA-признаки, пагинацию.
"""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("live_check")

# Попробуем curl_cffi как основной
try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
    log.info("curl_cffi доступен")
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req
    log.info("curl_cffi НЕдоступен, используем requests")

import requests

RESULTS = {}

def check(name, url, method="GET", params=None, headers=None, timeout=20, label=""):
    """Один HTTP-запрос, возвращает dict-сводку."""
    t0 = time.monotonic()
    entry = {
        "label": label or name,
        "url": url,
        "method": method,
        "params": params,
        "status": None,
        "size": 0,
        "elapsed": 0.0,
        "error": None,
        "captcha_hints": [],
        "content_type": "",
        "final_url": "",
    }
    try:
        h = {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}
        if headers:
            h.update(headers)

        if HAS_CFFI:
            resp = cffi_req.get(url, params=params, headers=h, timeout=timeout, impersonate="chrome")
        else:
            resp = cffi_req.get(url, params=params, headers=h, timeout=(5, timeout))

        entry["status"] = resp.status_code
        entry["size"] = len(resp.content)
        entry["elapsed"] = round(time.monotonic() - t0, 2)
        entry["content_type"] = resp.headers.get("Content-Type", "")[:80]
        entry["final_url"] = getattr(resp, "url", url)

        # CAPTCHA / антибот признаки
        body_snippet = resp.text[:3000].lower() if entry["size"] < 50000 else resp.text[:5000].lower()
        captcha_markers = [
            "captcha", "smartcaptcha", "recaptcha", "hcaptcha", "turnstile",
            "verify you are human", "are you a robot", "подтвердите",
            "проверка", "antibot", "bot detection", "challenge",
            'data-widget="captcha', "cf-challenge", "akamai",
            "waf", "rate limit", "too many requests",
        ]
        for m in captcha_markers:
            if m in body_snippet:
                entry["captcha_hints"].append(m)

        # 403/429 — отдельная пометка
        if resp.status_code in (403, 429):
            entry["captcha_hints"].append(f"HTTP_{resp.status_code}")

    except Exception as e:
        entry["elapsed"] = round(time.monotonic() - t0, 2)
        entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    RESULTS[name] = entry
    status_str = f"HTTP {entry['status']}" if entry["status"] else "ERR"
    log.info("%-20s %-6s %5dB  %5.1fs  %s", entry["label"], status_str, entry["size"], entry["elapsed"], entry["error"] or "")
    if entry["captcha_hints"]:
        log.info("  CAPTCHA hints: %s", ", ".join(entry["captcha_hints"][:5]))
    return entry


def main():
    log.info("=== LIVE CHECK R-52 ===")
    log.info("curl_cffi: %s", "YES" if HAS_CFFI else "NO (requests)")
    log.info("")

    # --- 1. Wildberries ---
    log.info("--- WILDBERRIES ---")
    check("wb_main", "https://www.wildberries.ru/", label="WB main")
    check("wb_search", "https://www.wildberries.ru/search", params={"query": "наушники"}, label="WB search")
    check("wb_search_old", "https://search.wildberries.ru/", params={"query": "наушники"}, label="WB search old")
    check("wb_basket", "https://basket-01.wbbasket.ru/vol0/part0/0.json", label="WB basket CDN")
    check("wb_price_api", "https://card.wb.ru/cards/v2/detail.json", params={"nm": "123456789"}, label="WB price API")
    check("wb_card", "https://www.wildberries.ru/catalog/123456789/detail.aspx", label="WB card")

    # --- 2. Ozon ---
    log.info("--- OZON ---")
    check("ozon_main", "https://www.ozon.ru/", label="Ozon main")
    check("ozon_search", "https://www.ozon.ru/search/", params={"text": "наушники", "from": "global"}, label="Ozon search")
    check("ozon_search_p2", "https://www.ozon.ru/search/", params={"text": "наушники", "from": "global", "page": "2"}, label="Ozon search p2")
    check("ozon_card", "https://www.ozon.ru/product/1234567890/", label="Ozon card")

    # --- 3. Avito ---
    log.info("--- AVITO ---")
    check("avito_main", "https://www.avito.ru/", label="Avito main")
    check("avito_search_lis", "https://www.avito.ru/lis?query=наушники", label="Avito /lis?query")
    check("avito_search_new", "https://www.avito.ru/search?query=наушники", label="Avito /search")
    check("avito_m_main", "https://m.avito.ru/", label="Avito m.main")
    check("avito_card", "https://www.avito.ru/moskva/elektronika/1234567890", label="Avito card")

    # --- 4. Farpost ---
    log.info("--- FARPOST ---")
    check("farpost_main", "https://www.farpost.ru/", label="Farpost main")
    check("farpost_dir", "https://www.farpost.ru/dir", params={"query": "наушники"}, label="Farpost /dir")
    check("farpost_search", "https://www.farpost.ru/search", params={"q": "наушники"}, label="Farpost /search")
    check("farpost_card", "https://www.farpost.ru/moskva/elektronika/test-1234567890.html", label="Farpost card")

    # --- 5. AliExpress ---
    log.info("--- ALIEXPRESS ---")
    check("ali_main", "https://www.aliexpress.com/", label="Ali main")
    check("ali_search", "https://www.aliexpress.com/wholesale?SearchText=earbuds", label="Ali search")
    check("ali_search_p2", "https://www.aliexpress.com/wholesale?SearchText=earbuds&page=2", label="Ali search p2")
    check("ali_card", "https://www.aliexpress.com/item/1005001234567890.html", label="Ali card")
    check("ali_ru_main", "https://ru.aliexpress.com/", label="Ali RU main")
    check("ali_ru_search", "https://ru.aliexpress.com/wholesale?SearchText=наушники", label="Ali RU search")

    # --- Сводка ---
    log.info("")
    log.info("=== СВОДКА ===")
    for name, e in RESULTS.items():
        status = e["status"] or "ERR"
        cap = "CAPTCHA!" if e["captcha_hints"] else ""
        err = e["error"][:60] if e["error"] else ""
        log.info("%-20s %-6s %6dB %5.1fs  %-10s %s", e["label"], status, e["size"], e["elapsed"], cap, err)

    # Сохранить JSON
    out = Path(__file__).parent / "live_check_results.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Результаты: %s", out)


if __name__ == "__main__":
    main()
