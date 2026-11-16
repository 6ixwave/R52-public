"""Диагностика WB / Ozon / Avito через curl_cffi (TLS-имперсонация).

Проверяет:
1. Проходит ли TLS-отпечаток Chrome 131?
2. Какой HTTP-статус возвращает сервер?
3. Есть ли JSON-LD / state в ответе?
4. Сохраняет HTML для офлайн-парсинга.

Запуск:
    C:\Python315\python.exe diag_v29_curlcffi.py
"""

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v29")

try:
    from curl_cffi import requests as cr
except ImportError:
    log.error("curl_cffi не установлен. pip install curl_cffi")
    sys.exit(1)

# Тестовые ID (из integration_test.py)
TARGETS = {
    "WB": {
        "url": "https://www.wildberries.ru/catalog/199298871/detail.aspx",
        "label": "Wildberries",
        "save": "diag_v29_wb.html",
    },
    "OZON": {
        "url": "https://www.ozon.ru/product/1600562267/",
        "label": "Ozon",
        "save": "diag_v29_ozon.html",
    },
    "AVITO": {
        "url": "https://www.avito.ru/ad/1096783029",
        "label": "Avito",
        "save": "diag_v29_avito.html",
    },
}

# Мобильные / альтернативные эндпоинты (fallback)
MOBILE_TARGETS = {
    "AVITO_MOBILE": {
        "url": "https://m.avito.ru/ad/1096783029",
        "label": "Avito Mobile",
        "save": "diag_v29_avito_m.html",
    },
    "WB_MOBILE": {
        "url": "https://www.wildberries.ru/catalog/199298871/detail.aspx",
        "label": "WB (mobile UA)",
        "save": "diag_v29_wb_m.html",
    },
}

results = []

def try_fetch(source_key, info, impersonate="chrome", timeout=30):
    """Один GET через curl_cffi."""
    url = info["url"]
    log.info("=== %s → %s (impersonate=%s) ===", source_key, url, impersonate)
    start = time.monotonic()

    try:
        resp = cr.get(
            url,
            impersonate=impersonate,
            timeout=timeout,
            allow_redirects=True,
        )
        elapsed = round(time.monotonic() - start, 2)
        status = resp.status_code
        body = resp.text
        body_len = len(body)

        # Быстрая проверка содержимого
        has_jsonld = 'application/ld+json' in body
        has_state = any(k in body for k in ('__STATE__', '__INITIAL_STATE__', '__PRELOADED_STATE__', 'data-state'))
        has_og_title = 'og:title' in body
        has_captcha = any(k in body.lower() for k in ('captcha', 'challenge', 'pow_', 'turnstile', 'smartcaptcha'))
        has_403_page = 'access denied' in body.lower() or 'forbidden' in body.lower()

        result = {
            "source": source_key,
            "label": info["label"],
            "url": url,
            "impersonate": impersonate,
            "http_status": status,
            "elapsed_sec": elapsed,
            "body_bytes": body_len,
            "has_jsonld": has_jsonld,
            "has_state": has_state,
            "has_og_title": has_og_title,
            "has_captcha": has_captcha,
            "has_403_page": has_403_page,
            "headers_server": resp.headers.get("server", ""),
            "headers_cf": resp.headers.get("cf-ray", ""),
            "headers_x Powered": resp.headers.get("x-powered-by", ""),
            "verdict": "",
        }

        if status == 200 and body_len > 1000:
            if has_captcha:
                result["verdict"] = "200_BUT_CAPTCHA"
            elif has_jsonld or has_state or has_og_title:
                result["verdict"] = "200_CONTENT_OK"
            else:
                result["verdict"] = "200_THIN"
        elif status in (403, 498):
            result["verdict"] = f"BLOCKED_{status}"
        elif status == 439:
            result["verdict"] = "BLOCKED_439_POW"
        elif status == 429:
            result["verdict"] = "RATE_LIMITED"
        else:
            result["verdict"] = f"HTTP_{status}"

        # Сохраняем HTML для офлайн-парсинга
        if status == 200 and body_len > 500:
            save_path = Path(__file__).parent / info["save"]
            save_path.write_text(body, encoding="utf-8")
            result["saved_to"] = str(save_path)
            log.info("  Saved %d bytes → %s", body_len, save_path.name)

        log.info("  %s | %d bytes | %.2fs | JSON-LD=%s State=%s OG=%s CAPTCHA=%s",
                 result["verdict"], body_len, elapsed,
                 has_jsonld, has_state, has_og_title, has_captcha)

        return result

    except Exception as e:
        elapsed = round(time.monotonic() - start, 2)
        result = {
            "source": source_key,
            "label": info["label"],
            "url": url,
            "impersonate": impersonate,
            "http_status": None,
            "elapsed_sec": elapsed,
            "body_bytes": 0,
            "verdict": f"EXCEPTION: {type(e).__name__}: {str(e)[:200]}",
        }
        log.warning("  Exception: %s", e)
        return result


def main():
    log.info("=== DIAG V29: curl_cffi TLS-имперсонация ===")
    log.info("curl_cffi version: %s", cr.__version__ if hasattr(cr, '__version__') else 'unknown')

    # --- Основной прогон: desktop Chrome 131 ---
    for key, info in TARGETS.items():
        results.append(try_fetch(key, info, impersonate="chrome"))
        time.sleep(5)  # пауза между источниками

    # --- Мобильные / альтернативные ---
    for key, info in MOBILE_TARGETS.items():
        results.append(try_fetch(key, info, impersonate="chrome_mobile"))
        time.sleep(5)

    # --- Попробовать Firefox для Ozon (иногда помогает) ---
    log.info("=== Ozon через Firefox impersonation ===")
    results.append(try_fetch("OZON_FF", TARGETS["OZON"], impersonate="firefox"))

    # --- Сводка ---
    print("\n" + "=" * 70)
    print("DIAG V29 SUMMARY (curl_cffi)")
    print("=" * 70)
    for r in results:
        icon = "✓" if "OK" in r["verdict"] else "⚠" if "THIN" in r["verdict"] or "CAPTCHA" in r["verdict"] else "✗"
        print(f"\n{icon} {r['source']} [{r.get('impersonate','?')}] → {r['verdict']} ({r['elapsed_sec']}s, {r['body_bytes']}B)")
        if r.get("saved_to"):
            print(f"   Saved: {Path(r['saved_to']).name}")
    print("\n" + "=" * 70)

    # JSON-отчёт
    out = Path(__file__).parent / "diag_v29_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {out}")

    sys.exit(0)


if __name__ == "__main__":
    main()
