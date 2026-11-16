"""Диагностика v30: глубокая проверка WB/Ozon/Avito.

1. Список доступных impersonation-типов curl_cffi
2. Avito: разные URL-форматы + другой ID (404 = карточка снята?)
3. WB: API-эндпоинты + разные URL
4. Ozon: API + разные URL
5. Сохранение всех HTML для анализа

Запуск:
    C:\Python315\python.exe diag_v30_deep.py
"""

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v30")

try:
    from curl_cffi import requests as cr
    from curl_cffi.requests import BrowserType
except ImportError:
    log.error("curl_cffi не установлен")
    sys.exit(1)

results = []

def try_fetch(key, url, impersonate="chrome", timeout=30, save=None, note=""):
    log.info("[%s] %s (imp=%s) %s", key, url, impersonate, note)
    start = time.monotonic()
    try:
        resp = cr.get(url, impersonate=impersonate, timeout=timeout, allow_redirects=True)
        elapsed = round(time.monotonic() - start, 2)
        body = resp.text
        r = {
            "key": key, "url": url, "impersonate": impersonate,
            "http_status": resp.status_code, "elapsed_sec": elapsed,
            "body_bytes": len(body), "note": note,
            "has_jsonld": 'application/ld+json' in body,
            "has_state": any(k in body for k in ('__STATE__', '__INITIAL_STATE__', '__PRELOADED_STATE__', 'data-state', '__NEXT_DATA__')),
            "has_og_title": 'og:title' in body,
            "has_captcha": any(k in body.lower()[:5000] for k in ('captcha', 'challenge', 'pow_', 'turnstile', 'smartcaptcha', 'altcha')),
            "server": resp.headers.get("server", ""),
            "cf_ray": resp.headers.get("cf-ray", ""),
            "set_cookie_count": len(resp.headers.get("set-cookie", "")),
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

        # Save HTML
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
             "verdict": f"EXC: {type(e).__name__}: {str(e)[:150]}", "note": note}
        log.warning("  Exception: %s", e)
        return r


def main():
    log.info("=== DIAG V30: Deep check ===")

    # 0. Available impersonation types
    try:
        types = list(BrowserType)
        log.info("BrowserType options: %s", [t.value for t in types])
        results.append({"key": "BROWSERTYPES", "options": [t.value for t in types]})
    except Exception as e:
        log.warning("BrowserType list failed: %s", e)
        # Try alternative
        try:
            from curl_cffi import BrowserType as BT2
            opts = [x for x in dir(BT2) if not x.startswith('_')]
            log.info("BT2 options: %s", opts)
            results.append({"key": "BROWSERTYPES", "options": opts})
        except Exception as e2:
            log.warning("BT2 failed: %s", e2)

    # Also try string-based impersonation
    test_imps = ["chrome", "chrome120", "chrome124", "chrome131", "chrome132",
                 "firefox", "firefox133", "safari", "safari17", "edge", "edge131"]
    log.info("Testing impersonation strings...")
    imp_results = []
    for imp in test_imps:
        try:
            resp = cr.get("https://httpbin.org/get", impersonate=imp, timeout=10)
            imp_results.append({"imp": imp, "ok": resp.status_code == 200})
            log.info("  %s → 200 ✓", imp)
        except Exception as e:
            imp_results.append({"imp": imp, "ok": False, "err": str(e)[:80]})
            log.info("  %s → FAIL (%s)", imp, str(e)[:60])
    results.append({"key": "IMPERSONATION_TEST", "results": imp_results})
    time.sleep(2)

    # 1. AVITO — different URL formats and IDs
    log.info("=== AVITO ===")
    # Original ID (might be expired)
    results.append(try_fetch("AVITO_orig", "https://www.avito.ru/ad/1096783029",
                             "chrome", save="diag_v30_avito_orig.html", note="original ID"))
    time.sleep(4)
    # Try a well-known active listing (search for one)
    # Avito URL patterns: /{city}/{category}/{slug}-{id} or /ad/{id}
    results.append(try_fetch("AVITO_search", "https://www.avito.ru/search?q=шина+225+45+18",
                             "chrome", save="diag_v30_avito_search.html", note="search page"))
    time.sleep(4)
    # Try m.avito.ru
    results.append(try_fetch("AVITO_m", "https://m.avito.ru/ad/1096783029",
                             "chrome", save="diag_v30_avito_m.html", note="mobile"))
    time.sleep(4)

    # 2. WILDBERRIES — different approaches
    log.info("=== WILDBERRIES ===")
    # Standard
    results.append(try_fetch("WB_std", "https://www.wildberries.ru/catalog/199298871/detail.aspx",
                             "chrome", save="diag_v30_wb_std.html", note="standard"))
    time.sleep(4)
    # Try without www
    results.append(try_fetch("WB_nowww", "https://wildberries.ru/catalog/199298871/detail.aspx",
                             "chrome", save="diag_v30_wb_nowww.html", note="no www"))
    time.sleep(4)
    # WB API (public card API)
    results.append(try_fetch("WB_api_card", "https://card.wb.ru/cards/v2/detail?nm=199298871",
                             "chrome", save="diag_v30_wb_api.json", note="card API v2"))
    time.sleep(4)
    # WB basket API
    results.append(try_fetch("WB_api_basket", "https://basket-XX.wbbasket.ru/vol0/part199/199298871/info/ru/card.json",
                             "chrome", save="diag_v30_wb_basket.json", note="basket API"))
    time.sleep(4)

    # 3. OZON — different approaches
    log.info("=== OZON ===")
    results.append(try_fetch("OZON_std", "https://www.ozon.ru/product/1600562267/",
                             "chrome", save="diag_v30_ozon_std.html", note="standard"))
    time.sleep(4)
    # Ozon without trailing slash
    results.append(try_fetch("OZON_noslash", "https://www.ozon.ru/product/1600562267",
                             "chrome", save="diag_v30_ozon_noslash.html", note="no slash"))
    time.sleep(4)
    # Ozon API (public)
    results.append(try_fetch("OZON_api", "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/",
                             "chrome", save="diag_v30_ozon_api.json", note="composer API"))
    time.sleep(4)
    # Ozon with different product ID (try a popular one)
    results.append(try_fetch("OZON_alt", "https://www.ozon.ru/product/1600562267/",
                             "firefox", save="diag_v30_ozon_ff.html", note="firefox"))
    time.sleep(4)

    # 4. Summary
    print("\n" + "=" * 70)
    print("DIAG V30 SUMMARY")
    print("=" * 70)
    for r in results:
        if r.get("key") in ("BROWSERTYPES", "IMPERSONATION_TEST"):
            print(f"\n--- {r['key']} ---")
            print(f"  {json.dumps(r.get('options', r.get('results', [])), ensure_ascii=False)[:200]}")
            continue
        icon = "✓" if "OK" in r.get("verdict", "") else "⚠" if "404" in r.get("verdict", "") or "THIN" in r.get("verdict", "") else "✗"
        print(f"\n{icon} {r['key']} [{r.get('impersonate','?')}] → {r.get('verdict','?')} "
              f"({r.get('elapsed_sec',0)}s, {r.get('body_bytes',0)}B)")
        if r.get("saved"):
            print(f"   Saved: {r['saved']}")
        if r.get("note"):
            print(f"   Note: {r['note']}")
    print("\n" + "=" * 70)

    out = Path(__file__).parent / "diag_v30_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
