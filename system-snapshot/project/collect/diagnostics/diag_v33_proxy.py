"""Диагностика v33: прокси-тест для WB, Ozon, Avito.

Цель:
- Проверить два известных прокси на связность и latency.
- Через каждый прокси — основные URL трёх маркетплейсов.
- Без прокси, но с chrome120 / chrome131 (TLS-фингерпринт).
- Сохранить HTML при любом статусе.
- JSON-отчёт diag_v33_results.json.

Запуск:
    C:\\Python315\\python.exe diag_v33_proxy.py
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v33")

try:
    from curl_cffi import requests as cr
except ImportError:
    log.error("curl_cffi не установлен")
    sys.exit(1)

BASE = Path(__file__).parent
results: list[dict] = []

# --- Прокси для теста ---
PROXIES = [
    {"name": "proxy_A", "url": "[REDACTED_AUTH_SECRET]"},
    {"name": "proxy_B", "url": "[REDACTED_AUTH_SECRET]"},
]

# --- URL-цели ---
TARGETS = {
    "WB": {
        "main": "https://www.wildberries.ru/",
        "card_api": "https://card.wb.ru/cards/v2/detail?nm=199298871",
        "search_api": "https://search.wb.ru/exactmatch/ru/common/v9/search?query=%D1%88%D0%B8%D0%BD%D0%B0&resultset=catalog&page=1",
    },
    "OZON": {
        "product": "https://www.ozon.ru/product/1600562267/",
        "composer": "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/1600562267/",
        "search": "https://www.ozon.ru/search/?text=%D1%88%D0%B8%D0%BD%D0%B0+225%2F45%2FR18",
    },
    "AVITO": {
        "main": "https://www.avito.ru/",
        "search": "https://www.avito.ru/search?q=%D1%88%D0%B8%D0%BD%D0%B0+225%2F45%2FR18",
        "ad": "https://www.avito.ru/ad/1096783029",
    },
}


def fetch(key: str, url: str, impersonate: str = "chrome",
          proxy_url: str | None = None, timeout: int = 25,
          note: str = "", session=None, verify_ssl: bool = True) -> dict:
    """GET с опциональным прокси, сохранение HTML при любом статусе."""
    label = f"[{key}] {url[:60]} imp={impersonate} proxy={proxy_url or 'DIRECT'}"
    log.info("%s | %s", label, note)
    start = time.monotonic()
    try:
        kwargs: dict = {
            "impersonate": impersonate,
            "timeout": timeout,
            "allow_redirects": True,
        }
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
        if not verify_ssl:
            kwargs["verify"] = False

        if session:
            resp = session.get(url, **kwargs)
        else:
            resp = cr.get(url, **kwargs)

        elapsed = round(time.monotonic() - start, 2)
        body = resp.text
        status = resp.status_code

        r: dict = {
            "key": key,
            "url": url,
            "impersonate": impersonate,
            "proxy": proxy_url or "DIRECT",
            "http_status": status,
            "elapsed_sec": elapsed,
            "body_bytes": len(body),
            "note": note,
            "has_jsonld": "application/ld+json" in body,
            "has_state": any(k in body for k in (
                "__STATE__", "__INITIAL_STATE__", "__PRELOADED_STATE__",
                "data-state", "__NEXT_DATA__", "window.__")),
            "has_og_title": "og:title" in body,
            "has_captcha": any(k in body.lower()[:6000] for k in (
                "captcha", "challenge", "pow_", "turnstile", "smartcaptcha",
                "altcha", "antibot", "fab_chlg", "wbaas")),
            "server": resp.headers.get("server", ""),
            "set_cookie": resp.headers.get("set-cookie", "")[:300],
        }

        # Verdict
        if status == 200 and len(body) > 1000:
            if r["has_captcha"]:
                r["verdict"] = "200_CAPTCHA"
            elif r["has_jsonld"] or r["has_state"] or r["has_og_title"]:
                r["verdict"] = "200_OK"
            else:
                r["verdict"] = "200_THIN"
        elif status == 404:
            r["verdict"] = "404_PAGE"
        elif status in (403, 498):
            r["verdict"] = f"BLOCKED_{status}"
        elif status == 429:
            r["verdict"] = "RATE_LIMITED"
        else:
            r["verdict"] = f"HTTP_{status}"

        # Сохраняем HTML если > 500 байт
        if len(body) > 500:
            safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", key)
            sp = BASE / f"v33_{safe_key}.html"
            sp.write_text(body, encoding="utf-8")
            r["saved"] = sp.name
            log.info("  Saved %dB -> %s", len(body), sp.name)

        log.info("  => %s | %dB | %.2fs | JSONLD=%s State=%s CAP=%s",
                 r["verdict"], len(body), elapsed,
                 r["has_jsonld"], r["has_state"], r["has_captcha"])
        return r

    except Exception as e:
        elapsed = round(time.monotonic() - start, 2)
        r = {
            "key": key, "url": url, "impersonate": impersonate,
            "proxy": proxy_url or "DIRECT",
            "http_status": None, "elapsed_sec": elapsed, "body_bytes": 0,
            "verdict": f"EXC: {type(e).__name__}: {str(e)[:250]}",
            "note": note,
        }
        log.warning("  Exception: %s", e)
        return r


def test_proxy_connectivity(proxy_url: str) -> dict:
    """Быстрый ping через прокси на known-good URL."""
    log.info("Connectivity test: %s", proxy_url)
    start = time.monotonic()
    try:
        resp = cr.get(
            "https://www.google.com/generate_204",
            proxies={"http": proxy_url, "https": proxy_url},
            impersonate="chrome",
            timeout=12,
        )
        elapsed = round(time.monotonic() - start, 2)
        r = {
            "proxy": proxy_url,
            "status": resp.status_code,
            "elapsed_sec": elapsed,
            "ok": resp.status_code in (200, 204),
        }
        log.info("  %s -> %s (%.2fs)", "OK" if r["ok"] else "FAIL", resp.status_code, elapsed)
        return r
    except Exception as e:
        elapsed = round(time.monotonic() - start, 2)
        r = {"proxy": proxy_url, "status": None, "elapsed_sec": elapsed,
             "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        log.warning("  FAIL: %s", r["error"])
        return r


def main():
    log.info("=== DIAG V33: Proxy test for WB / Ozon / Avito ===")
    log.info("Time: %s", time.strftime("%Y-%m-%d %H:%M:%S"))

    # ============================================================
    # 1. CONNECTIVITY: пинг каждого прокси
    # ============================================================
    log.info("=== 1. PROXY CONNECTIVITY ===")
    proxy_status = {}
    for p in PROXIES:
        cs = test_proxy_connectivity(p["url"])
        proxy_status[p["name"]] = cs
        results.append({"section": "CONNECTIVITY", **cs})
        time.sleep(2)

    # ============================================================
    # 2. ЧЕРЕЗ КАЖДЫЙ ЖИВОЙ ПРОКСИ — все URL
    # ============================================================
    for p in PROXIES:
        if not proxy_status.get(p["name"], {}).get("ok"):
            log.info("=== 2. %s: SKIP (dead) ===", p["name"])
            continue

        log.info("=== 2. VIA %s (%s) ===", p["name"], p["url"])
        purl = p["url"]

        # --- WB ---
        for tkey, turl in TARGETS["WB"].items():
            key = f"{p['name']}_WB_{tkey}"
            r = fetch(key, turl, proxy_url=purl, note=f"WB {tkey} via {p['name']}")
            results.append(r)
            time.sleep(6)

        # --- OZON ---
        for tkey, turl in TARGETS["OZON"].items():
            key = f"{p['name']}_OZON_{tkey}"
            r = fetch(key, turl, proxy_url=purl, note=f"OZON {tkey} via {p['name']}",
                      verify_ssl=(tkey != "composer"))
            results.append(r)
            time.sleep(6)

        # --- AVITO ---
        avito_sess = cr.Session(impersonate="chrome")
        for tkey, turl in TARGETS["AVITO"].items():
            key = f"{p['name']}_AVITO_{tkey}"
            r = fetch(key, turl, proxy_url=purl, note=f"AVITO {tkey} via {p['name']}",
                      session=avito_sess)
            results.append(r)
            time.sleep(6)

    # ============================================================
    # 3. DIRECT, но с chrome120 / chrome131 (TLS fingerprint)
    # ============================================================
    for imp in ("chrome120", "chrome131"):
        log.info("=== 3. DIRECT %s ===", imp)
        # WB main
        r = fetch(f"DIR_{imp}_WB_main", TARGETS["WB"]["main"],
                  impersonate=imp, note=f"WB main {imp}")
        results.append(r)
        time.sleep(5)

        # Ozon product
        r = fetch(f"DIR_{imp}_OZON_product", TARGETS["OZON"]["product"],
                  impersonate=imp, note=f"OZON product {imp}")
        results.append(r)
        time.sleep(5)

        # Avito main
        r = fetch(f"DIR_{imp}_AVITO_main", TARGETS["AVITO"]["main"],
                  impersonate=imp, note=f"AVITO main {imp}")
        results.append(r)
        time.sleep(5)

    # ============================================================
    # 4. DIRECT chrome, WB cookie-flow: main -> card API
    # ============================================================
    log.info("=== 4. WB cookie-flow (direct chrome) ===")
    wb_sess = cr.Session(impersonate="chrome")
    r = fetch("WB_cf_main", TARGETS["WB"]["main"], session=wb_sess,
              note="WB main for cookies")
    results.append(r)
    time.sleep(4)
    r = fetch("WB_cf_card", TARGETS["WB"]["card_api"], session=wb_sess,
              note="WB card API with session cookies")
    results.append(r)
    time.sleep(4)

    # ============================================================
    # 5. DIRECT chrome, Ozon cookie-flow: product -> composer
    # ============================================================
    log.info("=== 5. Ozon cookie-flow (direct chrome) ===")
    oz_sess = cr.Session(impersonate="chrome")
    r = fetch("OZON_cf_product", TARGETS["OZON"]["product"], session=oz_sess,
              note="Ozon product for cookies")
    results.append(r)
    time.sleep(4)
    r = fetch("OZON_cf_composer", TARGETS["OZON"]["composer"], session=oz_sess,
              note="Ozon composer with cookies", verify_ssl=False)
    results.append(r)

    # ============================================================
    # SAVE REPORT
    # ============================================================
    report = BASE / "diag_v33_results.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== DONE: %d entries -> %s ===", len(results), report.name)

    # Краткая сводка
    log.info("--- SUMMARY ---")
    for r in results:
        if r.get("section") == "CONNECTIVITY":
            continue
        v = r.get("verdict", "?")
        ok = "✅" if "200_OK" in v else ("⚠️" if "200" in v else "❌")
        log.info("  %s %-35s %-18s %5dB  %s", ok, r.get("key", "?"), v,
                 r.get("body_bytes", 0), r.get("proxy", "DIRECT")[:20])


if __name__ == "__main__":
    main()
