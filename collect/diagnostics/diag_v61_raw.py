"""Сырая HTTP-диагностика: что реально приходит по URL каждого маркетплейса.

Запуск: C:\Python315\python.exe diag_v61_raw.py
Результат: diag_v61_out.json
"""
import json
import time
import sys
import traceback
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

OUT = Path(__file__).parent / "diag_v61_out.json"

# --- Цели ---
TARGETS = {
    "WB": {
        "url": "https://www.wildberries.ru/catalog/199298871/detail.aspx",
        "api": "https://card.wb.ru/cards/v2/detail?nm=199298871",
        "price_api": "https://basket-21.wbbasket.ru/vol0/part21/199298871/info/ru/card.json",
    },
    "OZON": {
        "url": "https://www.ozon.ru/product/1600562267/",
        # Альтернативный — ищем свежий
        "url_alt": "https://www.ozon.ru/product/smartfon-iphone-17-pro-max-256gb-4605111975/",
    },
    "FARPOST": {
        "url": "https://www.farpost.ru/bulletin/18530297982",
        "url_alt": "https://www.farpost.ru/item/18530297982",
    },
    "AVITO": {
        "url": "https://www.avito.ru/moskva/telefony/iphone_14_128_gb_8341658309",
        "url_mobile": "https://m.avito.ru/moskva/telefony/iphone_14_128_gb_8341658309",
    },
}

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

HEADERS_JSON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def fetch(label: str, url: str, headers: dict, use_cffi: bool = True) -> dict:
    """Один HTTP-запрос, возвращает status, headers, размер, первые 800 символов."""
    result = {
        "label": label,
        "url": url,
        "status": None,
        "content_type": None,
        "content_length": None,
        "elapsed_ms": 0,
        "first_800": "",
        "error": None,
        "final_url": None,
        "lib": None,
    }
    start = time.monotonic()
    try:
        if use_cffi and cffi_requests:
            result["lib"] = "curl_cffi"
            resp = cffi_requests.get(url, headers=headers, timeout=20, impersonate="chrome")
        elif requests:
            result["lib"] = "requests"
            resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        else:
            result["error"] = "no HTTP lib"
            return result

        result["status"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["content_length"] = resp.headers.get("Content-Length", "")
        result["final_url"] = str(resp.url)
        body = resp.text[:2000] if hasattr(resp, "text") else ""
        result["first_800"] = body[:800]
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    return result


def main():
    results = []
    print("=" * 70)
    print("RAW HTTP DIAGNOSTIC v61")
    print("=" * 70)

    for source, targets in TARGETS.items():
        print(f"\n--- {source} ---")
        for key, url in targets.items():
            hdrs = HEADERS_JSON if "api" in key or "json" in key else HEADERS_BROWSER
            r = fetch(f"{source}/{key}", url, hdrs)
            results.append(r)
            print(f"  {key:12s} → {r['status']} {r['content_type'][:40]} "
                  f"len={r['content_length'] or '?'} {r['elapsed_ms']}ms lib={r['lib']}")
            if r["error"]:
                print(f"    ERROR: {r['error'][:120]}")
            if r["final_url"] and r["final_url"] != url:
                print(f"    REDIRECT → {r['final_url'][:100]}")
            # Первые 200 символов
            snippet = r["first_800"][:200].replace("\n", " ")
            print(f"    BODY: {snippet}")
            time.sleep(1)

    # Сохранить
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUT}")
    print("DONE")


if __name__ == "__main__":
    main()
