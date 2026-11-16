#!/usr/bin/env python3
"""Диагностика v35: WB API, Avito Playwright, Ozon подтверждение.

Цель:
  1. WB — проверить JSON-API card.wb.ru (может обойти WBAAS).
  2. Avito — Playwright на валидных URL (поиск + карточка).
  3. Ozon — Playwright, подтвердить парсинг JSON-LD / state.
"""

import json
import time
import logging
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v35")

BASE = Path(__file__).parent
RESULTS = []

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def record(key, url, mode, elapsed, title, nbytes, has_jsonld, has_state,
           has_og, has_captcha, verdict, saved=None):
    entry = {
        "key": key, "url": url, "mode": mode,
        "elapsed_sec": round(elapsed, 2),
        "title": (title or "")[:200],
        "bytes": nbytes,
        "has_jsonld": has_jsonld, "has_state": has_state,
        "has_og": has_og, "has_captcha": has_captcha,
        "verdict": verdict,
    }
    if saved:
        entry["saved"] = saved
    RESULTS.append(entry)
    log.info(f"  {key:28s} {verdict:15s} {nbytes:>9d}b  {elapsed:>6.1f}s  {title[:60]}")


def check_content(html):
    if not html or len(html) < 200:
        return "EMPTY"
    head = html[:15000].lower()
    has_jsonld = "application/ld+json" in head
    has_state = any(s in head for s in
                    ("__initial_state__", "__state__", "__preloaded_state__",
                     "__next_data__", "data-state"))
    has_og = "og:title" in head or "og:image" in head
    has_captcha = any(s in head for s in
                      ("captcha", "challenge", "antibot", "browser-check",
                       "altcha", "wbaas", "firewall", "ограничен"))
    if has_captcha and len(html) < 15000:
        return "CHALLENGE"
    if (has_jsonld or has_state) and has_og and len(html) > 50000:
        return "CONTENT_OK"
    if has_state and len(html) > 50000:
        return "CONTENT_OK"
    if len(html) > 50000:
        return "CONTENT_THIN"
    return "CONTENT_SHORT"


# =====================================================================
# 1. WILDBERRIES — JSON API (card.wb.ru, web/api)
# =====================================================================
log.info("=" * 60)
log.info("WB: JSON API endpoints")
log.info("=" * 60)

WB_NM = "199298871"
WB_API_TESTS = [
    ("WB_API_v2", f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}"),
    ("WB_API_web", f"https://www.wildberries.ru/web/api/v1/cards/detail?nm={WB_NM}"),
    ("WB_API_v1", f"https://card.wb.ru/cards/detail?nm={WB_NM}"),
    ("WB_API_nm", f"https://card.wb.ru/cards/v1/nm/{WB_NM}"),
]

for key, url in WB_API_TESTS:
    try:
        t0 = time.time()
        hdrs = dict(HEADERS_BROWSER)
        hdrs["Accept"] = "application/json, text/plain, */*"
        hdrs["Referer"] = "https://www.wildberries.ru/"
        r = requests.get(url, headers=hdrs, timeout=20)
        elapsed = time.time() - t0
        body = r.text
        nbytes = len(body.encode("utf-8"))

        data = None
        try:
            data = json.loads(body)
        except Exception:
            pass

        if data is not None:
            # card.wb.ru: {"data":{"products":[...]}}
            products = []
            if isinstance(data, dict):
                products = data.get("data", {}).get("products", [])
                if not products:
                    products = data.get("products", [])
            elif isinstance(data, list):
                products = data

            title = products[0].get("name", "no_name") if products else "empty"
            verdict = "API_OK" if products else "API_EMPTY"
            has_jsonld = False
            has_state = bool(products)
            has_og = False
            has_captcha = False
        else:
            title = body[:120].replace("\n", " ")
            verdict = check_content(body)
            has_jsonld = "application/ld+json" in body[:10000]
            has_state = "__state__" in body[:10000]
            has_og = "og:" in body[:10000]
            has_captcha = any(s in body[:10000].lower()
                              for s in ("challenge", "antibot", "wbaas"))

        ext = ".json" if data is not None else ".html"
        saved = f"v35_{key}{ext}"
        (BASE / saved).write_text(body[:300000], encoding="utf-8")
        record(key, url, "REQ", elapsed, title, nbytes,
               has_jsonld, has_state, has_og, has_captcha, verdict, saved)

        # Сохраняем полный JSON для офлайн-парсинга
        if data is not None:
            (BASE / f"v35_{key}_full.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2)[:800000],
                encoding="utf-8")
    except Exception as e:
        record(key, url, "REQ", 0, str(e)[:120], 0, False, False, False, False, "ERROR")
    time.sleep(2)

# =====================================================================
# 2. AVITO — Playwright (поиск + валидная карточка)
# =====================================================================
log.info("=" * 60)
log.info("AVITO: Playwright")
log.info("=" * 60)

AVITO_TESTS = [
    ("AVITO_moskva",    "https://www.avito.ru/moskva/"),
    ("AVITO_search",    "https://www.avito.ru/moskva/transport/avto/"),
    ("AVITO_mobile",    "https://m.avito.ru/moskva/"),
    ("AVITO_ad_short",  "https://www.avito.ru/ad/1096783029"),
]

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=HEADERS_BROWSER["User-Agent"],
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()

        for key, url in AVITO_TESTS:
            try:
                t0 = time.time()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(6000)
                elapsed = time.time() - t0

                html = page.content()
                title = page.title()
                nbytes = len(html.encode("utf-8"))
                verdict = check_content(html)
                has_jsonld = "application/ld+json" in html[:20000]
                has_state = any(s in html[:20000] for s in
                                ("__PRELOADED_STATE__", "__INITIAL_STATE__",
                                 "data-state"))
                has_og = "og:title" in html[:20000]
                has_captcha = any(s in html[:12000].lower() for s in
                                  ("captcha", "challenge", "antibot",
                                   "firewall", "ограничен", "404"))

                saved = f"v35_{key}.html"
                (BASE / saved).write_text(html[:600000], encoding="utf-8")
                record(key, url, "PW", elapsed, title, nbytes,
                       has_jsonld, has_state, has_og, has_captcha, verdict, saved)
            except Exception as e:
                record(key, url, "PW", 0, str(e)[:120], 0,
                       False, False, False, False, "ERROR")
            time.sleep(3)

        browser.close()
except Exception as e:
    log.error(f"Avito Playwright fatal: {e}")
    record("AVITO_PW_FATAL", "-", "PW", 0, str(e)[:120], 0,
           False, False, False, False, "FATAL")

# =====================================================================
# 3. OZON — Playwright (подтверждение + второй товар)
# =====================================================================
log.info("=" * 60)
log.info("OZON: Playwright confirm")
log.info("=" * 60)

OZON_TESTS = [
    ("OZON_prod1", "https://www.ozon.ru/product/1600562267/"),
    ("OZON_prod2", "https://www.ozon.ru/product/1234567890/"),
]

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=HEADERS_BROWSER["User-Agent"],
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()

        for key, url in OZON_TESTS:
            try:
                t0 = time.time()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(6000)
                elapsed = time.time() - t0

                html = page.content()
                title = page.title()
                nbytes = len(html.encode("utf-8"))
                verdict = check_content(html)
                has_jsonld = "application/ld+json" in html[:20000]
                has_state = any(s in html[:20000] for s in
                                ("__INITIAL_STATE__", "__NEXT_DATA__",
                                 "data-state"))
                has_og = "og:title" in html[:20000]
                has_captcha = any(s in html[:12000].lower() for s in
                                  ("captcha", "challenge", "antibot", "altcha"))

                saved = f"v35_{key}.html"
                (BASE / saved).write_text(html[:600000], encoding="utf-8")
                record(key, url, "PW", elapsed, title, nbytes,
                       has_jsonld, has_state, has_og, has_captcha, verdict, saved)
            except Exception as e:
                record(key, url, "PW", 0, str(e)[:120], 0,
                       False, False, False, False, "ERROR")
            time.sleep(3)

        browser.close()
except Exception as e:
    log.error(f"Ozon Playwright fatal: {e}")
    record("OZON_PW_FATAL", "-", "PW", 0, str(e)[:120], 0,
           False, False, False, False, "FATAL")

# =====================================================================
# Итог
# =====================================================================
out = BASE / "diag_v35_results.json"
out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
log.info(f"\nResults → {out}")

print("\n" + "=" * 70)
print(f"{'KEY':<28s} {'VERDICT':<15s} {'BYTES':>9s}  {'SEC':>6s}  TITLE")
print("-" * 70)
for r in RESULTS:
    print(f"{r['key']:<28s} {r['verdict']:<15s} {r['bytes']:>9d}  "
          f"{r['elapsed_sec']:>6.1f}  {r['title'][:50]}")
print("=" * 70)
