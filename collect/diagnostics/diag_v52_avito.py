"""Диагностика v52: Avito — новые подходы.
Запуск: C:/Python315/python.exe diag_v52_avito.py
"""
import json, re, time, os
from datetime import datetime

OUT = []
def log(msg):
    OUT.append(msg)
    print(msg)

def main():
    log("=" * 60)
    log(f"DIAG v52: AVITO  |  {datetime.now().isoformat()}")
    log("=" * 60)

    avito_ok = False
    avito_ids = []

    # --- A1: curl_cffi safari impersonation ---
    log("\n--- A1: curl_cffi safari ---")
    try:
        from curl_cffi import requests as cffi_req
        for aid in ["1154321987", "1045236789"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                resp = cffi_req.get(url, impersonate="safari", timeout=20)
                html = resp.text
                log(f"  safari id={aid}: HTTP {resp.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_safari_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  safari id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A1 ERROR: {e}")

    # --- A2: curl_cffi chrome120 (older) ---
    log("\n--- A2: curl_cffi chrome120 ---")
    try:
        from curl_cffi import requests as cffi_req
        for aid in ["1154321987"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                resp = cffi_req.get(url, impersonate="chrome120", timeout=20)
                html = resp.text
                log(f"  chrome120 id={aid}: HTTP {resp.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_c120_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  chrome120 id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A2 ERROR: {e}")

    # --- A3: requests with minimal headers ---
    log("\n--- A3: requests minimal headers ---")
    try:
        import requests
        for aid in ["1154321987"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                # Минимальные заголовки — как у "чистого" браузера
                r = requests.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                }, timeout=15, allow_redirects=True)
                html = r.text
                log(f"  minimal id={aid}: HTTP {r.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_min_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  minimal id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A3 ERROR: {e}")

    # --- A4: Avito via "www.avito.ru" with cookie jar ---
    log("\n--- A4: requests with session (cookie jar) ---")
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        # First: hit main page to get cookies
        try:
            r0 = s.get("https://www.avito.ru/", timeout=15)
            log(f"  Main page: HTTP {r0.status_code}, cookies={len(s.cookies)}")
            for c in s.cookies:
                log(f"    cookie: {c.name}={c.value[:30]}")
        except Exception as e:
            log(f"  Main page ERR: {e}")
        time.sleep(3)
        # Then: hit ad page
        for aid in ["1154321987"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                r = s.get(url, timeout=15)
                html = r.text
                log(f"  session id={aid}: HTTP {r.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_sess_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  session id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A4 ERROR: {e}")

    # --- A5: Avito via "m.avito.ru" with curl_cffi ---
    log("\n--- A5: m.avito.ru curl_cffi ---")
    try:
        from curl_cffi import requests as cffi_req
        for aid in ["1154321987", "1045236789"]:
            url = f"https://m.avito.ru/ad/{aid}"
            try:
                resp = cffi_req.get(url, impersonate="chrome", timeout=20)
                html = resp.text
                log(f"  m.avito id={aid}: HTTP {resp.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_m_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  m.avito id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A5 ERROR: {e}")

    # --- A6: Avito via "www.avito.ru" with HTTP/1.1 forced ---
    log("\n--- A6: HTTP/1.1 forced ---")
    try:
        import requests
        from requests.adapters import HTTPAdapter
        # Force HTTP/1.1
        s = requests.Session()
        adapter = HTTPAdapter()
        s.mount("https://", adapter)
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Connection": "keep-alive",
        })
        for aid in ["1154321987"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                r = s.get(url, timeout=15)
                html = r.text
                log(f"  h11 id={aid}: HTTP {r.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_h11_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  h11 id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A6 ERROR: {e}")

    # --- A7: Avito via "www.avito.ru" with "X-Real-IP" (local) ---
    log("\n--- A7: X-Real-IP local ---")
    try:
        import requests
        for aid in ["1154321987"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                r = requests.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
                    "Accept": "text/html",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "X-Real-IP": "127.0.0.1",
                    "X-Forwarded-For": "127.0.0.1",
                }, timeout=15)
                html = r.text
                log(f"  xreal id={aid}: HTTP {r.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                log(f"    CAPTCHA={has_captcha}")
            except Exception as e:
                log(f"  xreal id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A7 ERROR: {e}")

    # --- A8: Avito via "api.avito.ru" (separate host) ---
    log("\n--- A8: api.avito.ru ---")
    try:
        import requests
        for aid in ["1154321987"]:
            urls = [
                f"https://api.avito.ru/ads/{aid}",
                f"https://api.avito.ru/v1/ads/{aid}",
                f"https://api.avito.ru/ads/{aid}/full",
            ]
            for u in urls:
                try:
                    r = requests.get(u, headers={
                        "User-Agent": "AvitoMobile/1.0",
                        "Accept": "application/json",
                    }, timeout=10)
                    log(f"  api id={aid} {u.split('avito.ru')[1][:30]}: HTTP {r.status_code}, len={len(r.content)}")
                    if r.status_code == 200 and len(r.content) > 50:
                        log(f"    Body[:200]: {r.text[:200]}")
                        avito_ok = True
                except Exception as e:
                    log(f"  api id={aid}: ERR {e}")
                time.sleep(2)
    except Exception as e:
        log(f"  A8 ERROR: {e}")

    # --- A9: Avito via "www.avito.ru" with "Sec-Fetch" headers ---
    log("\n--- A9: Sec-Fetch headers ---")
    try:
        from curl_cffi import requests as cffi_req
        for aid in ["1154321987"]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                resp = cffi_req.get(url, impersonate="chrome", timeout=20, headers={
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                })
                html = resp.text
                log(f"  secfetch id={aid}: HTTP {resp.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:80] if title_m else '?'}")
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}")
                if not has_captcha and len(html) > 5000:
                    avito_ok = True
                    with open(f"diag_v52_avito_secfetch_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
            except Exception as e:
                log(f"  secfetch id={aid}: ERR {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A9 ERROR: {e}")

    log(f"\nAVITO RESULT: ok={avito_ok}, ids={avito_ids[:5]}")

    # Summary
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    log(f"  AVITO: ok={avito_ok}")
    log(f"  DONE: {datetime.now().isoformat()}")

    if not os.environ.get("AHUB_REDIRECT"):
        with open("diag_v52_out.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\nFull log saved: diag_v52_out.txt ({len(OUT)} lines)")
    else:
        log(f"\nFull log: {len(OUT)} lines (via shell redirect)")

if __name__ == "__main__":
    main()
