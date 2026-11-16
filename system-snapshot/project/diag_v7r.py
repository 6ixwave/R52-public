#!/usr/bin/env python3
"""diag_v7r — Playwright noCaptcha slider drag + API fallback for AE RU.
Goal: solve TMD via slider interaction, or bypass via search API with RU cookies.
"""
import json, re, time, logging, sys, os
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('diag_v7r')

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
SEARCH = "наушники"
BASE_RU = "https://ru.aliexpress.com/wholesale"


def is_results_page(html, url=""):
    """Distinguish real search results from CAPTCHA page."""
    if '_____tmd_____' in url or 'punish' in url:
        return False
    title_m = re.search(r'<title>([^<]+)</title>', html)
    title = title_m.group(1) if title_m else ''
    if 'CAPTCHA' in title.upper() or 'проверка' in title.lower():
        return False
    # Real results have product cards, prices, etc.
    has_cards = bool(re.search(r'data-product-card|class="[^"]*(?:card|item)[^"]*"', html))
    has_prices = bool(re.findall(r'\d+[.,]\d{2}\s*(?:EUR|€|USD|\$|RUB|₽)', html))
    return has_cards or has_prices or len(html) > 200000


def drag_slider(page, log):
    """Drag the noCaptcha slider from left to right with smooth motion."""
    # Try multiple selectors for the slider handle
    selectors = [
        '#nc_1_n1z',
        '.nc_iconfont.btn_slide',
        '.nc-container .nc_slider',
        '#nc_1_wrapper .nc_slider',
        '[class*="slider"]',
        '.nc-lang-cnt .nc_iconfont',
    ]
    handle = None
    for sel in selectors:
        try:
            handle = page.query_selector(sel)
            if handle:
                log.info("  [slider] Found handle: %s", sel)
                break
        except Exception:
            continue

    if not handle:
        # Try broader approach: find any draggable element in the captcha area
        log.info("  [slider] No standard selector, trying bounding-box approach...")
        captcha_area = page.query_selector('#nc_1_wrapper, .nc-container, [id*="nc_"], [class*="captcha"]')
        if captcha_area:
            box = captcha_area.bounding_box()
            if box:
                log.info("  [slider] Captcha area box: %s", box)
                # The slider handle is usually at the left edge of the track
                start_x = box['x'] + 10
                start_y = box['y'] + box['height'] / 2
                end_x = box['x'] + box['width'] - 10
                end_y = start_y
                log.info("  [slider] Drag: (%.0f,%.0f) → (%.0f,%.0f)", start_x, start_y, end_x, end_y)
                page.mouse.move(start_x, start_y)
                time.sleep(0.3)
                page.mouse.down()
                time.sleep(0.2)
                # Smooth drag: 20 steps over ~1 second
                steps = 20
                for i in range(1, steps + 1):
                    x = start_x + (end_x - start_x) * i / steps
                    y = start_y + (i % 3 - 1) * 1.5  # slight vertical jitter
                    page.mouse.move(x, y)
                    time.sleep(0.05)
                time.sleep(0.3)
                page.mouse.up()
                return True
        return False

    box = handle.bounding_box()
    if not box:
        log.warning("  [slider] Handle found but no bounding box")
        return False

    log.info("  [slider] Handle box: x=%.0f y=%.0f w=%.0f h=%.0f",
             box['x'], box['y'], box['width'], box['height'])

    # Get the track width (parent element)
    track = page.query_selector('#nc_1__scale_text, .nc_scale, [class*="scale"]')
    track_width = 300  # default
    if track:
        tbox = track.bounding_box()
        if tbox:
            track_width = tbox['width']
            log.info("  [slider] Track width: %.0f", track_width)

    start_x = box['x'] + box['width'] / 2
    start_y = box['y'] + box['height'] / 2
    end_x = start_x + track_width - box['width']
    end_y = start_y

    log.info("  [slider] Drag: (%.0f,%.0f) → (%.0f,%.0f) [track=%.0f]",
             start_x, start_y, end_x, end_y, track_width)

    # Smooth drag with human-like motion
    page.mouse.move(start_x, start_y)
    time.sleep(0.3)
    page.mouse.down()
    time.sleep(0.15)

    steps = 25
    for i in range(1, steps + 1):
        t = i / steps
        # Ease-in-out
        t = t * t * (3 - 2 * t)
        x = start_x + (end_x - start_x) * t
        # Slight vertical wobble for realism
        y = start_y + (i % 5 - 2) * 0.8
        page.mouse.move(x, y)
        time.sleep(0.04 + 0.02 * (1 - t))  # faster at start, slower at end

    time.sleep(0.4)
    page.mouse.up()
    log.info("  [slider] Drag complete, waiting for result...")
    return True


def main():
    log.info("=== diag_v7r — %s ===", time.strftime('%Y-%m-%d %H:%M:%S'))

    log.info("Cooldown 15s...")
    time.sleep(15)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("  playwright not installed")
        sys.exit(1)

    tmd_solved = False
    results_html = None
    cookies_list = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--window-size=1920,1080',
            ]
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale='ru-RU',
            timezone_id='Europe/Moscow',
            viewport={'width': 1920, 'height': 1080},
            extra_http_headers={
                'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
            },
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = {runtime: {}};
        """)

        page = ctx.new_page()

        # ── [1] Warm-up: home page ──
        log.info("── [1] Warm-up ──")
        page.goto("https://ru.aliexpress.com/", wait_until='domcontentloaded', timeout=30000)
        time.sleep(4)
        cookies_warm = ctx.cookies()
        log.info("  Cookies: %d", len(cookies_warm))
        aep = [c for c in cookies_warm if c['name'] == 'aep_usuc_f']
        if aep:
            log.info("  aep_usuc_f: %s", aep[0]['value'][:100])

        # ── [2] Search → TMD ──
        search_url = f"{BASE_RU}?SearchText={quote(SEARCH)}&page=1"
        log.info("── [2] Search ──")
        page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(2)

        url = page.url
        log.info("  URL: %s", url[:120])

        if '_____tmd_____' in url or 'punish' in url:
            log.info("── [3] TMD: solving via slider ──")
            # Wait for CAPTCHA widget to render
            time.sleep(3)

            # Check what's on the page
            page_html = page.content()
            log.info("  TMD page: %dB", len(page_html))

            # Look for the noCaptcha container
            nc_el = page.query_selector('#nc_1_wrapper, .nc-container, [id*="nc_1"]')
            if nc_el:
                log.info("  noCaptcha container found")
            else:
                log.info("  noCaptcha container NOT found — checking for other captcha types...")
                # Maybe it's a different challenge (QR code, etc.)
                qr = page.query_selector('[class*="qrcode"], [id*="qrcode"], canvas')
                log.info("  QR/canvas: %s", 'yes' if qr else 'no')

            # Attempt slider drag
            dragged = drag_slider(page, log)
            if dragged:
                # Wait for result
                time.sleep(5)
                url_after = page.url
                log.info("  After drag: URL=%s", url_after[:120])

                # Check if redirected
                if '_____tmd_____' not in url_after and 'punish' not in url_after:
                    log.info("  ✓ Redirected! Loading results...")
                    page.wait_for_load_state('domcontentloaded', timeout=15000)
                    time.sleep(2)
                    tmd_solved = True
                else:
                    # Maybe the page reloaded in place
                    page_html2 = page.content()
                    log.info("  After drag: content=%dB", len(page_html2))
                    if is_results_page(page_html2, url_after):
                        tmd_solved = True
                        log.info("  ✓ Results page detected after drag!")
                    else:
                        # Try reload
                        log.info("  Still on TMD. Trying reload...")
                        page.reload(wait_until='domcontentloaded', timeout=30000)
                        time.sleep(4)
                        url_reload = page.url
                        html_reload = page.content()
                        log.info("  Reload: URL=%s, %dB", url_reload[:100], len(html_reload))
                        if is_results_page(html_reload, url_reload):
                            tmd_solved = True
                            log.info("  ✓ Results after reload!")
                        else:
                            log.info("  ✗ Still TMD after reload")

            # Second drag attempt if still stuck
            if not tmd_solved:
                log.info("── [3b] Second slider attempt ──")
                time.sleep(2)
                # Sometimes the widget resets
                page_html = page.content()
                nc2 = page.query_selector('#nc_1_n1z, .nc_slider, [class*="slider"]')
                if nc2:
                    log.info("  Slider element re-found, dragging again...")
                    drag_slider(page, log)
                    time.sleep(6)
                    url2 = page.url
                    html2 = page.content()
                    log.info("  After 2nd drag: URL=%s, %dB", url2[:100], len(html2))
                    if is_results_page(html2, url2):
                        tmd_solved = True
                        log.info("  ✓ Results after 2nd drag!")
                else:
                    log.info("  No slider element for 2nd attempt")
        else:
            # Direct hit (no TMD)
            html_direct = page.content()
            log.info("  Direct hit? %dB", len(html_direct))
            if is_results_page(html_direct, url):
                tmd_solved = True
                log.info("  ✓ Direct results, no TMD!")

        # ── [4] Get results ──
        if tmd_solved:
            log.info("── [4] Results analysis ──")
            results_html = page.content()
            log.info("  Size: %dB", len(results_html))

            lang_m = re.search(r'<html[^>]*lang=["\']?(\w+)', results_html)
            log.info("  lang: %s", lang_m.group(1) if lang_m else '?')
            t = re.search(r'<title>([^<]+)</title>', results_html)
            if t:
                log.info("  title: %s", t.group(1)[:120])

            prices = re.findall(r'(\d{1,5}[.,]\d{2})\s*(?:EUR|€|USD|\$|RUB|₽)', results_html)
            log.info("  Prices: %d, first 8: %s", len(prices), prices[:8])

            # Currency breakdown
            eur = len(re.findall(r'\d+[.,]\d{2}\s*(?:EUR|€)', results_html))
            rub = len(re.findall(r'\d+[.,]\d{2}\s*(?:RUB|₽)', results_html))
            usd = len(re.findall(r'\d+[.,]\d{2}\s*(?:USD|\$)', results_html))
            log.info("  Currency: EUR=%d RUB=%d USD=%d", eur, rub, usd)

            # Product names (look for title attributes or h3/h4)
            names = re.findall(r'title="([^"]{10,100})"', results_html)
            log.info("  Product titles: %d, first 5: %s", len(names), names[:5])

            # Russian words check
            ru_words = re.findall(r'[а-яА-ЯёЁ]{4,}', results_html)
            log.info("  RU words found: %d (sample: %s)", len(ru_words), ru_words[:10])

            # Save
            save_path = os.path.join(OUT_DIR, 'diag_v7r_results.html')
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(results_html)
            log.info("  Saved → %s (%dB)", save_path, len(results_html))

        # ── [5] Save cookies ──
        cookies_list = ctx.cookies()
        cookies_path = os.path.join(OUT_DIR, 'diag_v7r_cookies.json')
        with open(cookies_path, 'w', encoding='utf-8') as f:
            json.dump(cookies_list, f, indent=2, ensure_ascii=False)
        log.info("  Cookies saved: %d → %s", len(cookies_list), cookies_path)

        browser.close()

    # ── [6] API fallback with cookies ──
    log.info("── [6] API fallback (curl_cffi + RU cookies) ──")
    try:
        from curl_cffi import requests as cr
    except ImportError:
        import requests as cr

    s = cr.Session(impersonate="chrome")
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://ru.aliexpress.com/wholesale",
        "Origin": "https://ru.aliexpress.com",
    })

    # Set cookies from Playwright
    for c in cookies_list:
        try:
            s.cookies.set(c['name'], c['value'], domain=c['domain'])
        except Exception:
            try:
                s.cookies.set(c['name'], c['value'])
            except Exception:
                pass
    log.info("  Cookies set: %d", len(cookies_list))

    time.sleep(3)

    # Try multiple API endpoints
    api_endpoints = [
        (
            "search-pc",
            f"https://ru.aliexpress.com/fn/search-pc/index?SearchText={quote(SEARCH)}&page=1&sortType=total_tranpro_desc",
            "GET", None
        ),
        (
            "aer-webapi",
            "https://aer-webapi.aliexpress.com/fn/search-pc/index",
            "POST",
            {"SearchText": SEARCH, "page": 1, "sortType": "total_tranpro_desc"}
        ),
        (
            "search-api-v1",
            f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={quote(SEARCH)}&page=1",
            "GET", None
        ),
    ]

    for name, url, method, body in api_endpoints:
        try:
            log.info("  [%s] %s %s", name, method, url[:100])
            if method == "POST":
                r = s.post(url, json=body, timeout=15)
            else:
                r = s.get(url, timeout=15)
            log.info("  [%s] HTTP %d, %dB", name, r.status_code, len(r.content))

            if r.status_code == 200 and len(r.content) > 500:
                text = r.text[:500]
                log.info("  [%s] Body[:500]: %s", name, text)

                # Check for product data
                if '"products"' in r.text or '"items"' in r.text or '"result"' in r.text:
                    log.info("  [%s] ✓ Product data found!", name)
                    # Try to parse
                    try:
                        data = r.json()
                        log.info("  [%s] JSON keys: %s", name, list(data.keys())[:10])
                    except Exception:
                        pass
            elif r.status_code == 200:
                log.info("  [%s] Small response: %s", name, r.text[:200])
            else:
                log.info("  [%s] Non-200: %s", name, r.text[:200])
        except Exception as e:
            log.warning("  [%s] Error: %s", name, e)

        time.sleep(2)

    # ── [7] Summary ──
    log.info("── Summary ──")
    log.info("  TMD solved (slider): %s", tmd_solved)
    log.info("  Results HTML: %dB", len(results_html) if results_html else 0)
    log.info("  Cookies: %d", len(cookies_list))
    log.info("=== DONE ===")
    log.info("diag_v7r complete")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.exception("FATAL: %s", e)
        sys.exit(1)
