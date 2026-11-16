"""Диагностика v31: поиск живых ID + API-эндпоинты.

Стратегия:
1. AVITO: TLS работает (404 = ID мёртвый). Ищем живой ID через search-страницу
   или пробуем известные активные объявления.
2. WB: card.wb.ru API (JSON, не HTML) — проще для парсинга. Пробуем с сессией.
3. OZON: composer API с verify=False + альтернативные эндпоинты.

Запуск:
    C:\Python315\python.exe diag_v31_valid.py
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v31")

try:
    from curl_cffi import requests as cr
except ImportError:
    log.error("curl_cffi не установлен")
    sys.exit(1)

results = []


def try_fetch(key, url, impersonate="chrome", timeout=30, save=None, note="",
              headers=None, verify_ssl=True, session=None):
    log.info("[%s] %s (imp=%s) %s", key, url, impersonate, note)
    start = time.monotonic()
    try:
        kwargs = {
            "impersonate": impersonate,
            "timeout": timeout,
            "allow_redirects": True,
        }
        if headers:
            kwargs["headers"] = headers
        if not verify_ssl:
            kwargs["verify"] = False

        if session:
            resp = session.get(url, **kwargs)
        else:
            resp = cr.get(url, **kwargs)

        elapsed = round(time.monotonic() - start, 2)
        body = resp.text
        r = {
            "key": key, "url": url, "impersonate": impersonate,
            "http_status": resp.status_code, "elapsed_sec": elapsed,
            "body_bytes": len(body), "note": note,
            "has_jsonld": 'application/ld+json' in body,
            "has_state": any(k in body for k in ('__STATE__', '__INITIAL_STATE__',
                                                  '__PRELOADED_STATE__', 'data-state',
                                                  '__NEXT_DATA__')),
            "has_og_title": 'og:title' in body,
            "has_captcha": any(k in body.lower()[:5000] for k in
                               ('captcha', 'challenge', 'pow_', 'turnstile',
                                'smartcaptcha', 'altcha')),
            "server": resp.headers.get("server", ""),
        }

        # Verdict
        if resp.status_code == 200 and len(body) > 1000:
            if r["has_captcha"]:
                r["verdict"] = "200_CAPTCHA"
            elif r["has_jsonld"] or r["has_state"] or r["has_og_title"]:
                r["verdict"] = "200_OK"
            else:
                r["verdict"] = "200_THIN"
        elif resp.status_code == 404:
            r["verdict"] = "404_PAGE" if len(body) > 5000 else "404_SMALL"
        elif resp.status_code in (403, 498):
            r["verdict"] = f"BLOCKED_{resp.status_code}"
        elif resp.status_code == 429:
            r["verdict"] = "RATE_LIMITED"
        else:
            r["verdict"] = f"HTTP_{resp.status_code}"

        # Save
        if save and resp.status_code in (200, 404) and len(body) > 500:
            sp = Path(__file__).parent / save
            sp.write_text(body, encoding="utf-8")
            r["saved"] = sp.name
            log.info("  Saved %d bytes → %s", len(body), sp.name)

        log.info("  → %s | %dB | %.2fs | JSONLD=%s State=%s OG=%s CAP=%s",
                 r["verdict"], len(body), elapsed,
                 r["has_jsonld"], r["has_state"], r["has_og_title"], r["has_captcha"])
        return r

    except Exception as e:
        elapsed = round(time.monotonic() - start, 2)
        r = {"key": key, "url": url, "impersonate": impersonate,
             "http_status": None, "elapsed_sec": elapsed, "body_bytes": 0,
             "verdict": f"EXC: {type(e).__name__}: {str(e)[:200]}", "note": note}
        log.warning("  Exception: %s", e)
        return r


def main():
    log.info("=== DIAG V31: Valid IDs + API endpoints ===")

    # ============================================================
    # 1. AVITO — найти живой ID
    # ============================================================
    log.info("=== AVITO: finding valid listing ===")

    # 1a. Search page (got 429 last time, but 34KB body — might have results)
    #     Try with session + longer delay
    avito_session = cr.Session(impersonate="chrome")
    # First hit: main page to get cookies
    try:
        r0 = avito_session.get("https://www.avito.ru/", timeout=15, allow_redirects=True)
        log.info("Avito main: %d, %dB, cookies=%d",
                 r0.status_code, len(r0.text), len(r0.cookies))
    except Exception as e:
        log.warning("Avito main failed: %s", e)
    time.sleep(6)

    # 1b. Search for tires (should have many active listings)
    r = try_fetch("AVITO_search2", "https://www.avito.ru/search?q=%D1%88%D0%B8%D0%BD%D0%B0+225%2F45%2FR18",
                  "chrome", save="diag_v31_avito_search.html", note="search tires",
                  session=avito_session)
    results.append(r)

    # Parse search results to find a valid ID
    valid_avito_id = None
    if r.get("saved"):
        search_html = (Path(__file__).parent / r["saved"]).read_text(encoding="utf-8")
        # Avito listing links: /{city}/{category}/{slug}-{ID} or /ad/{ID}
        ids = re.findall(r'avito\.ru/(?:[a-z\-]+/)+[a-z0-9\-]+-(\d{8,})', search_html)
        if not ids:
            ids = re.findall(r'/ad/(\d{8,})', search_html)
        if not ids:
            # Try data attributes
            ids = re.findall(r'data-id=["\'](\d{8,})["\']', search_html)
        if ids:
            valid_avito_id = ids[0]
            log.info("Found Avito IDs: %s (using %s)", ids[:5], valid_avito_id)
        else:
            log.warning("No Avito IDs found in search HTML (%d bytes)", len(search_html))
            # Check if it's a rate-limit page
            if "429" in str(r.get("http_status")):
                log.info("Search was rate-limited, trying direct known IDs...")

    time.sleep(6)

    # 1c. If we found an ID, fetch the card
    if valid_avito_id:
        r = try_fetch("AVITO_card", f"https://www.avito.ru/ad/{valid_avito_id}",
                      "chrome", save="diag_v31_avito_card.html",
                      note=f"valid ID {valid_avito_id}", session=avito_session)
        results.append(r)
    else:
        # Try some "likely active" IDs (recent format: 10-12 digits)
        # These are guesses based on Avito's ID range in 2025-2026
        test_ids = ["1234567890", "1198765432", "1154321987"]
        for tid in test_ids:
            r = try_fetch(f"AVITO_try_{tid}", f"https://www.avito.ru/ad/{tid}",
                          "chrome", save=f"diag_v31_avito_{tid}.html",
                          note=f"guess ID {tid}", session=avito_session)
            results.append(r)
            if r.get("http_status") == 200 and r.get("body_bytes", 0) > 5000:
                valid_avito_id = tid
                log.info("Found valid Avito ID: %s", tid)
                break
            time.sleep(5)

    # ============================================================
    # 2. WILDBERRIES — API endpoints
    # ============================================================
    log.info("=== WILDBERRIES: API approaches ===")

    # 2a. card.wb.ru with proper session
    wb_session = cr.Session(impersonate="chrome")
    # Pre-warm: hit main page
    try:
        r0 = wb_session.get("https://www.wildberries.ru/", timeout=15, allow_redirects=True)
        log.info("WB main: %d, %dB", r0.status_code, len(r0.text))
    except Exception as e:
        log.warning("WB main failed: %s", e)
    time.sleep(4)

    # 2b. Card API v2 (JSON endpoint)
    r = try_fetch("WB_card_api", "https://card.wb.ru/cards/v2/detail?nm=199298871",
                  "chrome", save="diag_v31_wb_card.json",
                  note="card API v2 with session", session=wb_session,
                  headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
    results.append(r)
    time.sleep(4)

    # 2c. Card API with different param (spp = supplier price)
    r = try_fetch("WB_card_spp", "https://card.wb.ru/cards/v2/detail?nm=199298871&sup=1",
                  "chrome", save="diag_v31_wb_card_spp.json",
                  note="card API with sup param", session=wb_session,
                  headers={"Accept": "application/json"})
    results.append(r)
    time.sleep(4)

    # 2d. WB static basket (need to guess vol/part or use known pattern)
    # Pattern: static-basket-{N}.wbbasket.ru/vol{X}/part{Y}/{ID}/images/l{SIZE}/{ID}-1.jpg
    # For info: static-basket-{N}.wbbasket.ru/vol{X}/part{Y}/{ID}/info/ru/card.json
    # Try a few common static-basket numbers
    for sb_num in [0, 1, 2, 3, 4]:
        url = f"https://static-basket-{sb_num}.wbbasket.ru/vol0/part199/199298871/info/ru/card.json"
        r = try_fetch(f"WB_sb{sb_num}", url, "chrome",
                      save=f"diag_v31_wb_sb{sb_num}.json",
                      note=f"static-basket-{sb_num}", timeout=10,
                      headers={"Accept": "application/json"})
        results.append(r)
        if r.get("http_status") == 200 and r.get("body_bytes", 0) > 100:
            log.info("WB static-basket-%d WORKS!", sb_num)
            break
        time.sleep(2)

    # 2e. Try WB search API (might be less protected)
    r = try_fetch("WB_search_api", "https://search.wb.ru/exactmatch/ru/common/v9/search?query=%D1%88%D0%B8%D0%BD%D0%B0&resultset=catalog&page=1",
                  "chrome", save="diag_v31_wb_search.json",
                  note="WB search API v9", session=wb_session,
                  headers={"Accept": "application/json"})
    results.append(r)

    # ============================================================
    # 3. OZON — fix SSL + alternative endpoints
    # ============================================================
    log.info("=== OZON: API with fixed SSL ===")

    # 3a. Composer API with verify=False
    r = try_fetch("OZON_composer", "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/",
                  "chrome", save="diag_v31_ozon_composer.json",
                  note="composer API no-SSL-verify", verify_ssl=False,
                  headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"})
    results.append(r)
    time.sleep(4)

    # 3b. Ozon with different product (try a very popular one)
    #     Product ID 123456789 might not exist, let's try a known format
    r = try_fetch("OZON_page", "https://www.ozon.ru/product/1600562267/",
                  "chrome", save="diag_v31_ozon_page.html",
                  note="main page retry", verify_ssl=False)
    results.append(r)
    time.sleep(4)

    # 3c. Ozon mobile / lite
    r = try_fetch("OZON_lite", "https://lite.ozon.ru/product/1600562267/",
                  "chrome", save="diag_v31_ozon_lite.html",
                  note="lite.ozon.ru", verify_ssl=False)
    results.append(r)

    # 3d. Ozon with specific headers that might bypass WAF
    ozon_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.ozon.ru/",
        "Upgrade-Insecure-Requests": "1",
    }
    r = try_fetch("OZON_headers", "https://www.ozon.ru/product/1600562267/",
                  "chrome", save="diag_v31_ozon_headers.html",
                  note="with referer+headers", verify_ssl=False, headers=ozon_headers)
    results.append(r)

    # ============================================================
    # 4. Summary
    # ============================================================
    print("\n" + "=" * 70)
    print("DIAG V31 SUMMARY")
    print("=" * 70)
    for r in results:
        icon = "✓" if "OK" in r.get("verdict", "") else \
               "⚠" if "404" in r.get("verdict", "") or "THIN" in r.get("verdict", "") or "RATE" in r.get("verdict", "") else \
               "✗"
        print(f"\n{icon} {r['key']} [{r.get('impersonate','?')}] → {r.get('verdict','?')} "
              f"({r.get('elapsed_sec',0)}s, {r.get('body_bytes',0)}B)")
        if r.get("saved"):
            print(f"   Saved: {r['saved']}")
        if r.get("note"):
            print(f"   Note: {r['note']}")
        # For JSON responses, show first 200 chars
        if r.get("saved") and r["saved"].endswith(".json"):
            try:
                content = (Path(__file__).parent / r["saved"]).read_text(encoding="utf-8")[:200]
                print(f"   JSON preview: {content}")
            except Exception:
                pass
    print("\n" + "=" * 70)

    out = Path(__file__).parent / "diag_v31_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
