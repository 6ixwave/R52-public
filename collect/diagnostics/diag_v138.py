"""Диагностика v138: Ozon 562267 + Avito 7494308705.
Проверяет: status, длина, маркеры (data-widget, JSON-LD, captcha, state).
Два HTTP-клиента: requests и curl_cffi.
"""
import json
import logging
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v138")

OUT = Path(__file__).parent
results = {}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def analyze_html(html: str, source: str) -> dict:
    """Извлекает ключевые маркеры из HTML."""
    info = {
        "length": len(html or ""),
        "has_og_title": bool(re.search(r'<meta[^>]*property=["\']og:title["\']', html, re.I)),
        "has_og_price": bool(re.search(r'<meta[^>]*property=["\']og:price', html, re.I)),
        "has_jsonld": bool(re.search(r'application/ld\+json', html, re.I)),
        "has_data_widget": bool(re.search(r'data-widget=', html, re.I)),
        "has_preloaded_state": bool(re.search(r'__PRELOADED_STATE__', html, re.I)),
        "has_initial_state": bool(re.search(r'__INITIAL_STATE__', html, re.I)),
        "has_captcha": bool(re.search(r'captcha|recaptcha|smartcaptcha|cf-challenge|turnstile', html[:5000], re.I)),
        "has_error_widget": bool(re.search(r'data-widget=["\']error["\']', html, re.I)),
        "title_tag": None,
        "og_title": None,
        "first_300": (html or "")[:300],
    }
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    if m:
        info["title_tag"] = m.group(1).strip()[:200]
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        info["og_title"] = m.group(1).strip()[:200]
    # data-widget names
    widgets = re.findall(r'data-widget=["\']([^"\']+)["\']', html)
    info["widgets"] = list(set(widgets))[:20]
    return info


def fetch_requests(url: str, label: str) -> dict:
    """GET через requests."""
    import requests
    t0 = time.monotonic()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=(10, 30), allow_redirects=True)
        elapsed = time.monotonic() - t0
        html = resp.text
        info = analyze_html(html, label)
        info.update({
            "client": "requests",
            "status": resp.status_code,
            "final_url": str(resp.url),
            "elapsed_sec": round(elapsed, 2),
            "content_type": resp.headers.get("Content-Type", ""),
            "content_length_header": resp.headers.get("Content-Length", ""),
        })
        # Save HTML
        safe = label.replace("/", "_")
        html_path = OUT / f"diag_v138_{safe}_requests.html"
        html_path.write_text(html, encoding="utf-8")
        info["html_saved"] = str(html_path)
        log.info("[%s requests] status=%d len=%d elapsed=%.1fs final_url=%s",
                 label, resp.status_code, len(html), elapsed, resp.url)
        return info
    except Exception as e:
        elapsed = time.monotonic() - t0
        log.error("[%s requests] ERROR: %s", label, e)
        return {"client": "requests", "error": str(e), "elapsed_sec": round(elapsed, 2), "status": None}


def fetch_curl_cffi(url: str, label: str) -> dict:
    """GET через curl_cffi (impersonate=chrome)."""
    try:
        from curl_cffi import requests as cr
        t0 = time.monotonic()
        resp = cr.get(url, impersonate="chrome", timeout=30, allow_redirects=True)
        elapsed = time.monotonic() - t0
        html = resp.text
        info = analyze_html(html, label)
        info.update({
            "client": "curl_cffi",
            "status": resp.status_code,
            "final_url": str(resp.url),
            "elapsed_sec": round(elapsed, 2),
            "content_type": resp.headers.get("Content-Type", ""),
        })
        safe = label.replace("/", "_")
        html_path = OUT / f"diag_v138_{safe}_cffi.html"
        html_path.write_text(html, encoding="utf-8")
        info["html_saved"] = str(html_path)
        log.info("[%s cffi] status=%d len=%d elapsed=%.1fs final_url=%s",
                 label, resp.status_code, len(html), elapsed, resp.url)
        return info
    except Exception as e:
        elapsed = time.monotonic() - t0
        log.error("[%s cffi] ERROR: %s", label, e)
        return {"client": "curl_cffi", "error": str(e), "elapsed_sec": round(elapsed, 2), "status": None}


def main():
    targets = [
        ("OZON-562267", "https://www.ozon.ru/product/562267/"),
        ("AVITO-7494308705", "https://www.avito.ru/ad/7494308705"),
    ]

    for label, url in targets:
        log.info("=== %s: %s ===", label, url)
        r1 = fetch_requests(url, label)
        results[f"{label}_requests"] = r1
        time.sleep(3)
        r2 = fetch_curl_cffi(url, label)
        results[f"{label}_cffi"] = r2
        time.sleep(5)

    # Summary
    log.info("=== SUMMARY ===")
    for k, v in results.items():
        log.info("%s: status=%s len=%s widgets=%s", k, v.get("status"), v.get("length"), v.get("widgets", [])[:5])

    out_path = OUT / "diag_v138_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved: %s", out_path)


if __name__ == "__main__":
    main()
