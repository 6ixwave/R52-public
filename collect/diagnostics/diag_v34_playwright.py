"""Диагностика v34: Playwright headless Chromium для WB, Ozon, Avito.

Цель: проверить, проходит ли JS-антибот в реальном браузере.
Режимы: DIRECT (без прокси) и PROXY_B (198.51.100.20:80).
5 URL × 2 режима = 10 страниц. Сохраняем HTML + JSON-отчёт.

Запуск:
    C:\\Python315\\python.exe diag_v34_playwright.py
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v34")

BASE = Path(__file__).parent
results: list[dict] = []

PROXY_B = "http://198.51.100.20:80"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

TARGETS = [
    ("WB_main",    "https://www.wildberries.ru/"),
    ("WB_card",    "https://www.wildberries.ru/catalog/199298871/detail.aspx"),
    ("OZON_prod",  "https://www.ozon.ru/product/1600562267/"),
    ("AVITO_main", "https://www.avito.ru/"),
    ("AVITO_ad",   "https://www.avito.ru/ad/1096783029"),
]


def analyze_html(html: str) -> dict:
    """Быстрый анализ: что за страница."""
    title_m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.I)
    title = title_m.group(1).strip() if title_m else ""

    has_jsonld = "application/ld+json" in html
    has_state = any(k in html for k in (
        "__STATE__", "__INITIAL_STATE__", "__PRELOADED_STATE__",
        "__NEXT_DATA__", "window.__"))
    has_og = 'og:title' in html or 'og:description' in html
    has_captcha = any(k in html.lower()[:10000] for k in (
        "captcha", "challenge", "antibot", "fab_chlg", "wbaas",
        "qrator", "проверка безопасности", "доступ ограничен",
        "browser-check", "wait_msg"))

    if has_captcha and len(html) < 25000:
        verdict = "CHALLENGE"
    elif has_jsonld or has_state or has_og:
        verdict = "CONTENT_OK"
    elif len(html) > 5000:
        verdict = "CONTENT_THIN"
    else:
        verdict = "EMPTY"

    return {
        "title": title[:140],
        "bytes": len(html),
        "has_jsonld": has_jsonld,
        "has_state": has_state,
        "has_og": has_og,
        "has_captcha": has_captcha,
        "verdict": verdict,
    }


def main():
    log.info("=== DIAG V34: Playwright Chromium ===")
    log.info("Time: %s", time.strftime("%Y-%m-%d %H:%M:%S"))

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        for mode, proxy in [("DIRECT", None), ("PROXY_B", PROXY_B)]:
            log.info("=== MODE: %s %s ===", mode, proxy or "no-proxy")

            launch_kw: dict = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            }
            if proxy:
                launch_kw["proxy"] = {"server": proxy}

            try:
                browser = pw.chromium.launch(**launch_kw)
            except Exception as e:
                log.error("Browser launch failed (%s): %s", mode, e)
                results.append({"mode": mode, "error": f"LAUNCH: {str(e)[:300]}"})
                return

            context = browser.new_context(
                user_agent=UA,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                timezone_id="Asia/Vladivostok",
            )

            for tname, turl in TARGETS:
                key = f"{mode}_{tname}"
                log.info("[%s] %s", key, turl)
                page = context.new_page()
                start = time.monotonic()
                try:
                    page.goto(turl, wait_until="domcontentloaded", timeout=30000)
                    # JS-challenge: даём 12 с на прохождение
                    page.wait_for_timeout(12000)
                    # Дождёмся networkidle (скрипты антибота догрузятся)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    # Рендер
                    page.wait_for_timeout(3000)

                    html = page.content()
                    elapsed = round(time.monotonic() - start, 2)
                    info = analyze_html(html)

                    r = {
                        "key": key,
                        "url": turl,
                        "mode": mode,
                        "proxy": proxy or "DIRECT",
                        "elapsed_sec": elapsed,
                        **info,
                    }

                    safe = re.sub(r"[^a-zA-Z0-9_]", "_", key)
                    sp = BASE / f"v34_{safe}.html"
                    sp.write_text(html, encoding="utf-8")
                    r["saved"] = sp.name

                    log.info("  -> %s | %dB | %.1fs | %s",
                             r["verdict"], r["bytes"], elapsed, r["title"][:70])
                    results.append(r)

                except Exception as e:
                    elapsed = round(time.monotonic() - start, 2)
                    r = {
                        "key": key, "url": turl, "mode": mode,
                        "proxy": proxy or "DIRECT",
                        "elapsed_sec": elapsed,
                        "verdict": f"EXC: {type(e).__name__}: {str(e)[:250]}",
                    }
                    log.warning("  Exception: %s", e)
                    results.append(r)
                finally:
                    page.close()

                time.sleep(3)

            context.close()
            browser.close()

    # --- Report ---
    report = BASE / "diag_v34_results.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== DONE: %d entries -> %s ===", len(results), report.name)

    log.info("--- SUMMARY ---")
    for r in results:
        v = r.get("verdict", "?")
        ok = "OK " if "CONTENT_OK" in v else ("THN" if "CONTENT" in v else "CHL" if "CHALLENGE" in v else "ERR")
        log.info("  [%s] %-28s %-14s %6dB  %s",
                 ok, r.get("key", "?"), v, r.get("bytes", 0), r.get("mode", ""))


if __name__ == "__main__":
    main()
