"""test_fp_diag.py — Диагностика Farpost: почему fetch() возвращает None.

Проверяет:
1. Warm-up + /bulletin/{id} (редирект)
2. Warm-up + полный путь /{city}/{cat}/{slug}-{id}.html
3. curl_cffi vs requests
4. Redirect chain, status, cookies

Запуск:
  cmd /c "C:\Python315\python.exe -u test_fp_diag.py > test_fp_diag_out.log 2>&1 & echo EXIT=%ERRORLEVEL%"
"""
import sys, os, json, time, logging, traceback
from pathlib import Path
from urllib.parse import urlsplit

PROJ = Path(__file__).parent
sys.path.insert(0, str(PROJ))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fp_diag")

BULLETINS = [
    ("130693618", "Triangle IcelynX TI501 205/55 R16"),
    ("136342005", "Комплект дисков Rays G25 R17"),
    ("98836529", "Поршни 4D35 Mitsubishi Canter"),
]

def test_requests_session():
    """Тест 1: requests.Session (как в FarpostCollector)."""
    import requests
    print("\n" + "=" * 60, flush=True)
    print("TEST 1: requests.Session", flush=True)
    print("=" * 60, flush=True)

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })

    # Warm-up
    log.info("Warm-up: GET https://www.farpost.ru/")
    try:
        r = s.get("https://www.farpost.ru/", timeout=(10, 30))
        log.info("Warm-up: %d, %dB, cookies=%s", r.status_code, len(r.content), list(s.cookies.keys()))
    except Exception as e:
        log.error("Warm-up error: %s", e)
        return

    time.sleep(1)

    for bid, title in BULLETINS:
        url = f"https://www.farpost.ru/bulletin/{bid}"
        log.info("\n--- Bulletin %s (%s) ---", bid, title)
        log.info("URL: %s", url)
        try:
            r = s.get(url, timeout=(10, 30), allow_redirects=True)
            log.info("Status: %d, %dB", r.status_code, len(r.content))
            log.info("Final URL: %s", r.url)
            log.info("History: %s", [(h.status_code, h.headers.get('Location', '')[:80]) for h in r.history])
            log.info("Cookies after: %s", list(s.cookies.keys()))

            if r.status_code == 200:
                # Quick parse check
                import re
                m = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.I)
                log.info("Title: %s", m.group(1)[:80] if m else "?")
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r.text)
                log.info("Price: %s", m.group(1) if m else "?")
                m = re.search(r'"@type"\s*:\s*"Product"', r.text)
                log.info("JSON-LD Product: %s", "YES" if m else "NO")
            elif r.status_code in (301, 302, 307, 308):
                log.warning("Redirect not followed? Location: %s", r.headers.get('Location', '?'))
            else:
                log.warning("Non-200: %d", r.status_code)
                log.warning("Body[:200]: %s", r.text[:200])
        except Exception as e:
            log.error("Fetch error: %s", e)
            traceback.print_exc()
        time.sleep(2)

def test_curl_cffi():
    """Тест 2: curl_cffi (impersonate=chrome)."""
    print("\n" + "=" * 60, flush=True)
    print("TEST 2: curl_cffi", flush=True)
    print("=" * 60, flush=True)

    try:
        from curl_cffi import requests as cffi_req
    except ImportError:
        log.warning("curl_cffi not available")
        return

    # Warm-up
    log.info("Warm-up: GET https://www.farpost.ru/ (cffi)")
    try:
        r = cffi_req.get("https://www.farpost.ru/", impersonate="chrome", timeout=30)
        log.info("Warm-up: %d, %dB, cookies=%s", r.status_code, len(r.content), list(r.cookies.keys()) if hasattr(r, 'cookies') else '?')
    except Exception as e:
        log.error("Warm-up error: %s", e)
        return

    time.sleep(1)

    for bid, title in BULLETINS[:2]:  # только 2 для скорости
        url = f"https://www.farpost.ru/bulletin/{bid}"
        log.info("\n--- Bulletin %s (%s) ---", bid, title)
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=30)
            log.info("Status: %d, %dB", r.status_code, len(r.content))
            log.info("Final URL: %s", r.url)

            if r.status_code == 200:
                import re
                m = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.I)
                log.info("Title: %s", m.group(1)[:80] if m else "?")
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r.text)
                log.info("Price: %s", m.group(1) if m else "?")
            else:
                log.warning("Body[:200]: %s", r.text[:200])
        except Exception as e:
            log.error("Fetch error: %s", e)
            traceback.print_exc()
        time.sleep(2)

def test_collector_direct():
    """Тест 3: FarpostCollector.collect_product с логированием."""
    print("\n" + "=" * 60, flush=True)
    print("TEST 3: FarpostCollector.collect_product", flush=True)
    print("=" * 60, flush=True)

    # Ускоряем throttle
    import r52.collectors.base as base_mod
    import r52.collectors.farpost as fp_mod

    orig_throttle = base_mod.BaseCollector._throttle
    def fast_throttle(self):
        now = time.monotonic()
        elapsed = now - self._last_request
        wait = 2.0 - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
    base_mod.BaseCollector._throttle = fast_throttle

    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(use_proxies=False)

    for bid, title in BULLETINS[:2]:
        url = fp.build_url(bid)
        log.info("\n--- Collector: %s (%s) ---", bid, title)
        log.info("URL: %s", url)

        # Step 1: warm-up
        fp._warmup()
        log.info("After warmup: warmed=%s, cookies=%s", fp._warmed_up, list(fp.session.cookies.keys()))

        # Step 2: fetch
        t0 = time.time()
        resp = fp.fetch(url)
        elapsed = time.time() - t0
        if resp is None:
            log.warning("fetch() returned None in %.1fs", elapsed)
            log.warning("Stats: %s", fp.stats)
        else:
            log.info("fetch() OK: %d, %dB in %.1fs", resp.status_code, len(resp.content), elapsed)
            # Step 3: parse
            obs = fp.parse_page(resp, bid, url, title)
            if obs:
                fields = obs.get("fields", {})
                prices = obs.get("price_candidates", [])
                media = obs.get("media", {}).get("items", [])
                log.info("Parse OK: title=%s, prices=%d, media=%d",
                         fields.get("title", {}).get("value", "?")[:50],
                         len(prices), len(media))
            else:
                log.warning("parse_page returned None")
        time.sleep(1)

    log.info("\nCollector stats: %s", fp.stats)


if __name__ == "__main__":
    t0 = time.time()
    test_requests_session()
    test_curl_cffi()
    test_collector_direct()
    print(f"\nFP DIAG DONE ({time.time()-t0:.0f}s)", flush=True)
