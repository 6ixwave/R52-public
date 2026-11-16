"""Диагностика v32: сохранить ВСЕ ответы + найти живой ID Avito.

Ключевые отличия от v30/v31:
- Сохраняем HTML при ЛЮБОМ статусе (200, 403, 404, 429, 498)
- Avito: парсим 429 search-страницу на предмет живых ID
- WB: двухшаговый (main → cookie → card API)
- Ozon: composer API с сессией
- Финальный JSON-отчёт

Запуск:
    C:\Python315\python.exe diag_v32_all.py
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v32")

try:
    from curl_cffi import requests as cr
except ImportError:
    log.error("curl_cffi не установлен")
    sys.exit(1)

BASE = Path(__file__).parent
results = []


def fetch_and_save(key, url, impersonate="chrome", timeout=30, note="",
                   headers=None, verify_ssl=True, session=None):
    """GET + сохранение HTML при ЛЮБОМ статусе."""
    log.info("[%s] %s (imp=%s) %s", key, url, impersonate, note)
    start = time.monotonic()
    try:
        kwargs = {"impersonate": impersonate, "timeout": timeout, "allow_redirects": True}
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
            "has_state": any(k in body for k in
                ('__STATE__', '__INITIAL_STATE__', '__PRELOADED_STATE__',
                 'data-state', '__NEXT_DATA__', 'window.__')),
            "has_og_title": 'og:title' in body,
            "has_captcha": any(k in body.lower()[:5000] for k in
                ('captcha', 'challenge', 'pow_', 'turnstile', 'smartcaptcha', 'altcha')),
            "server": resp.headers.get("server", ""),
            "set_cookie": resp.headers.get("set-cookie", "")[:200],
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
            r["verdict"] = "404_PAGE"
        elif resp.status_code in (403, 498):
            r["verdict"] = f"BLOCKED_{resp.status_code}"
        elif resp.status_code == 429:
            r["verdict"] = "RATE_LIMITED"
        else:
            r["verdict"] = f"HTTP_{resp.status_code}"

        # Сохраняем ВСЕГДА (любой статус > 500 байт)
        if len(body) > 500:
            sp = BASE / f"v32_{key}.html"
            sp.write_text(body, encoding="utf-8")
            r["saved"] = sp.name
            log.info("  Saved %dB → %s", len(body), sp.name)

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


def extract_avito_ids(html: str) -> list[str]:
    """Извлекает ID объявлений из HTML Avito (search/404/429 страницы)."""
    ids = set()
    # Паттерн 1: /{city}/{cat}/{slug}-{ID}
    ids.update(re.findall(r'avito\.ru/(?:[a-z\-]+/)+[a-z0-9\-]+-(\d{8,})', html))
    # Паттерн 2: /ad/{ID}
    ids.update(re.findall(r'/ad/(\d{8,})', html))
    # Паттерн 3: data-атрибуты
    ids.update(re.findall(r'data-ad-id=["\'](\d{8,})["\']', html))
    ids.update(re.findall(r'data-id=["\'](\d{8,})["\']', html))
    # Паттерн 4: JSON-вложенность
    ids.update(re.findall(r'"(?:adId|ad_id|listingId|id)"\s*:\s*"?(\d{8,})"?', html))
    # Паттерн 5: href с query
    ids.update(re.findall(r'\?id=(\d{8,})', html))
    return sorted(ids)


def main():
    log.info("=== DIAG V32: All responses + valid IDs ===")

    # ============================================================
    # 1. AVITO — найти живой ID
    # ============================================================
    log.info("=== AVITO ===")
    avito_sess = cr.Session(impersonate="chrome")

    # 1a. Main page (get cookies)
    r = fetch_and_save("AVITO_main", "https://www.avito.ru/",
                       session=avito_sess, note="main + cookies")
    results.append(r)
    time.sleep(5)

    # 1b. Search page (429 but 34KB — might have data)
    r = fetch_and_save("AVITO_search",
                       "https://www.avito.ru/search?q=%D1%88%D0%B8%D0%BD%D0%B0+225%2F45%2FR18",
                       session=avito_sess, note="search tires")
    results.append(r)
    time.sleep(5)

    # 1c. Parse search page for IDs
    avito_ids = []
    search_file = BASE / "v32_AVITO_search.html"
    if search_file.exists():
        html = search_file.read_text(encoding="utf-8")
        avito_ids = extract_avito_ids(html)
        log.info("Avito search: found %d IDs: %s", len(avito_ids), avito_ids[:10])

    # Also parse the 404 page (might have "similar" links)
    for f in BASE.glob("v32_AVITO_*.html"):
        if f.name != "v32_AVITO_search.html":
            more = extract_avito_ids(f.read_text(encoding="utf-8"))
            new = [i for i in more if i not in avito_ids]
            if new:
                log.info("  %s: +%d IDs: %s", f.name, len(new), new[:5])
                avito_ids.extend(new)

    # 1d. Fetch a valid card
    if avito_ids:
        vid = avito_ids[0]
        r = fetch_and_save(f"AVITO_card_{vid[:8]}",
                           f"https://www.avito.ru/ad/{vid}",
                           session=avito_sess, note=f"valid ID {vid}")
        results.append(r)
    else:
        log.warning("No Avito IDs found in any page!")

    time.sleep(4)

    # ============================================================
    # 2. WILDBERRIES — two-step + API
    # ============================================================
    log.info("=== WILDBERRIES ===")
    wb_sess = cr.Session(impersonate="chrome")

    # 2a. Main page (498 + cookies)
    r = fetch_and_save("WB_main", "https://www.wildberries.ru/",
                       session=wb_sess, note="main 498")
    results.append(r)
    time.sleep(4)

    # 2b. Card API with session cookies
    r = fetch_and_save("WB_card_api",
                       "https://card.wb.ru/cards/v2/detail?nm=199298871",
                       session=wb_sess,
                       headers={"Accept": "application/json",
                                "Referer": "https://www.wildberries.ru/"},
                       note="card API + cookies")
    results.append(r)
    time.sleep(3)

    # 2c. Card API v1 (older endpoint, sometimes less protected)
    r = fetch_and_save("WB_card_v1",
                       "https://card.wb.ru/cards/detail?nm=199298871",
                       session=wb_sess,
                       headers={"Accept": "application/json"},
                       note="card API v1")
    results.append(r)
    time.sleep(3)

    # 2d. WB static (try static-basket with proper numbering)
    # Pattern: static-basket-{N}.wbbasket.ru where N is 0-9
    for n in [0, 5, 10]:
        r = fetch_and_save(f"WB_static{n}",
                           f"https://static-basket-{n}.wbbasket.ru/vol0/part199/199298871/info/ru/card.json",
                           session=wb_sess,
                           headers={"Accept": "application/json"},
                           note=f"static-basket-{n}")
        results.append(r)
        if r.get("http_status") == 200:
            log.info("WB static-basket-%d WORKS!", n)
            break
        time.sleep(2)

    # 2e. WB detail page (the 498 page itself — save for analysis)
    r = fetch_and_save("WB_detail",
                       "https://www.wildberries.ru/catalog/199298871/detail.aspx",
                       session=wb_sess, note="detail page 498")
    results.append(r)

    time.sleep(3)

    # ============================================================
    # 3. OZON — session + composer
    # ============================================================
    log.info("=== OZON ===")
    oz_sess = cr.Session(impersonate="chrome")

    # 3a. Main page (get cookies)
    r = fetch_and_save("OZON_main", "https://www.ozon.ru/",
                       session=oz_sess, note="main + cookies")
    results.append(r)
    time.sleep(4)

    # 3b. Product page with session
    r = fetch_and_save("OZON_product",
                       "https://www.ozon.ru/product/1600562267/",
                       session=oz_sess,
                       headers={"Referer": "https://www.ozon.ru/"},
                       note="product + cookies")
    results.append(r)
    time.sleep(4)

    # 3c. Composer API (JSON)
    r = fetch_and_save("OZON_composer",
                       "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/",
                       session=oz_sess,
                       headers={"Accept": "application/json",
                                "Referer": "https://www.ozon.ru/"},
                       note="composer API")
    results.append(r)
    time.sleep(3)

    # 3d. Ozon search (might be less protected than product)
    r = fetch_and_save("OZON_search",
                       "https://www.ozon.ru/search/?text=%D1%88%D0%B8%D0%BD%D0%B0+225%2F45%2FR18",
                       session=oz_sess, note="search page")
    results.append(r)

    # ============================================================
    # 4. Summary
    # ============================================================
    print("\n" + "=" * 70)
    print("DIAG V32 SUMMARY")
    print("=" * 70)
    for r in results:
        icon = "✓" if "OK" in r.get("verdict", "") else \
               "⚠" if any(x in r.get("verdict", "") for x in ("404", "THIN", "RATE")) else \
               "✗"
        print(f"\n{icon} {r['key']} [{r.get('impersonate','?')}] → {r.get('verdict','?')} "
              f"({r.get('elapsed_sec',0)}s, {r.get('body_bytes',0)}B)")
        if r.get("saved"):
            print(f"   Saved: {r['saved']}")
        if r.get("note"):
            print(f"   Note: {r['note']}")
    print(f"\nAvito IDs found: {avito_ids[:10]}")
    print("\n" + "=" * 70)

    out = BASE / "diag_v32_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
