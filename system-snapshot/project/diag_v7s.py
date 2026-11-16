#!/usr/bin/env python3
"""diag_v7s — Playwright TMD slider with network monitoring + proper API.
Improvements over v7r:
  - Overshoot + natural easing on slider drag
  - Intercept verify POST response to see if token was accepted
  - Post-drag: wait 10s, check iframes, re-navigate to search
  - API: try m.aliexpress.com JSON endpoint with RU cookies
"""
import json, re, time, logging, sys, os
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('diag_v7s')

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
SEARCH = "наушники"
BASE_RU = "https://ru.aliexpress.com/wholesale"


def natural_drag(page, log):
    """Drag slider with overshoot, ease-in-out, micro-jitter."""
    handle = page.query_selector('#nc_1_n1z')
    if not handle:
        handle = page.query_selector('.nc_iconfont.btn_slide')
    if not handle:
        handle = page.query_selector('[class*="slider"]')
    if not handle:
        log.info("  [drag] No slider handle found")
        return False

    box = handle.bounding_box()
    if not box:
        log.info("  [drag] Handle no bounding box")
        return False

    # Track: parent #nc_1_wrapper or the scale bar
    track = page.query_selector('#nc_1_wrapper')
    track_w = 300
    if track:
        tbox = track.bounding_box()
        if tbox:
            track_w = tbox['width'] - box['width'] - 4
            log.info("  [drag] Track width: %.0f", track_w)

    start_x = box['x'] + box['width'] / 2
    start_y = box['y'] + box['height'] / 2
    # Target: full track + 5px overshoot, then settle back
    target_x = start_x + track_w
    overshoot_x = target_x + 6

    log.info("  [drag] Start: (%.0f, %.0f) → Target: %.0f (overshoot: %.0f)",
             start_x, start_y, target_x, overshoot_x)

    # Move to handle with slight approach
    page.mouse.move(start_x - 5, start_y - 3)
    time.sleep(0.2)
    page.mouse.move(start_x, start_y)
    time.sleep(0.3)

    # Press
    page.mouse.down()
    time.sleep(0.1)

    # Phase 1: accelerate (60% distance in 40% time)
    steps_p1 = 12
    for i in range(1, steps_p1 + 1):
        t = i / steps_p1
        # Ease-in: t^2
        x = start_x + (target_x - start_x) * 0.6 * (t * t)
        y = start_y + (i % 4 - 1.5) * 1.2
        page.mouse.move(x, y)
        time.sleep(0.03)

    # Phase 2: decelerate + overshoot (40% distance in 40% time)
    steps_p2 = 10
    for i in range(1, steps_p2 + 1):
        t = i / steps_p2
        # Ease-out: 1-(1-t)^2
        progress = 0.6 + 0.4 * (1 - (1 - t) ** 2)
        x = start_x + (overshoot_x - start_x) * progress
        y = start_y + (i % 3 - 1) * 0.8
        page.mouse.move(x, y)
        time.sleep(0.04)

    # Phase 3: settle back from overshoot
    time.sleep(0.15)
    for i in range(1, 6):
        t = i / 5
        x = overshoot_x - (overshoot_x - target_x) * t
        y = start_y + (i % 2 - 0.5) * 0.5
        page.mouse.move(x, y)
        time.sleep(0.05)

    # Hold briefly then release
    time.sleep(0.3)
    page.mouse.up()
    log.info("  [drag] Released at x=%.0f", target_x)
    return True


def main():
    log.info("=== diag_v7s — %s ===", time.strftime('%Y-%m-%d %H:%M:%S'))
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
    verify_responses = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--window-size=1920,1080',
                '--disable-ipc-flooding-protection',
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
        # Stronger anti-detection
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [
                {name: 'Chrome PDF Plugin'},
                {name: 'Chrome PDF Viewer'},
                {name: 'Native Client'}
            ]});
            window.chrome = {runtime: {}, app: {isInstalled: false}};
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 16});
            // Patch isConnected for iframe detection
            const origQuery = document.querySelector.bind(document);
            document.querySelector = (sel) => {
                const el = origQuery(sel);
                if (el && el.shadowRoot) el.shadowRoot;
                return el;
            };
        """)

        page = ctx.new_page()

        # ── Network interceptor: capture verify responses ──
        def on_response(resp):
            url = resp.url
            if 'verify' in url or 'tmd' in url or 'punish' in url:
                try:
                    body = resp.text()
                    log.info("  [NET] %s → HTTP %d, %dB", url[:100], resp.status, len(body))
                    verify_responses.append({
                        'url': url,
                        'status': resp.status,
                        'body': body[:2000],
                        'headers': dict(resp.headers),
                    })
                except Exception:
                    verify_responses.append({'url': url, 'status': resp.status, 'body': 'binary'})

        page.on('response', on_response)

        # ── [1] Warm-up ──
        log.info("── [1] Warm-up ──")
        page.goto("https://ru.aliexpress.com/", wait_until='domcontentloaded', timeout=30000)
        time.sleep(5)
        cookies_warm = ctx.cookies()
        log.info("  Cookies: %d", len(cookies_warm))
        aep = [c for c in cookies_warm if c['name'] == 'aep_usuc_f']
        if aep:
            log.info("  aep_usuc_f: %s", aep[0]['value'][:120])

        # ── [2] Search → TMD ──
        search_url = f"{BASE_RU}?SearchText={quote(SEARCH)}&page=1"
        log.info("── [2] Search ──")
        page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)

        url = page.url
        log.info("  URL: %s", url[:150])

        if '_____tmd_____' in url or 'punish' in url:
            log.info("── [3] TMD detected. Waiting for widget... ──")
            # Wait for slider to be interactive
            try:
                page.wait_for_selector('#nc_1_n1z', timeout=10000)
                log.info("  Slider handle ready")
            except Exception:
                log.info("  Slider not found in 10s, trying alternative...")
                page.wait_for_timeout(3000)

            time.sleep(2)  # Extra settle time

            # Drag
            dragged = natural_drag(page, log)
            if not dragged:
                log.info("  Drag failed, trying bounding-box fallback")
                nc = page.query_selector('#nc_1_wrapper')
                if nc:
                    b = nc.bounding_box()
                    if b:
                        page.mouse.move(b['x'] + 15, b['y'] + b['height'] / 2)
                        time.sleep(0.3)
                        page.mouse.down()
                        for i in range(1, 30):
                            x = b['x'] + 15 + (b['width'] - 30) * i / 30
                            page.mouse.move(x, b['y'] + b['height'] / 2 + (i % 3 - 1))
                            time.sleep(0.04)
                        time.sleep(0.2)
                        page.mouse.up()
                        dragged = True

            # ── [4] Wait for verify callback ──
            log.info("── [4] Waiting 10s for verify response... ──")
            time.sleep(10)

            url_after = page.url
            html_after = page.content()
            log.info("  After wait: URL=%s", url_after[:120])
            log.info("  Content: %dB", len(html_after))
            log.info("  Verify responses captured: %d", len(verify_responses))
            for vr in verify_responses:
                log.info("    [%d] %s: %s", vr['status'], vr['url'][:80], vr['body'][:200])

            # Check if TMD passed
            tmd_passed = ('_____tmd_____' not in url_after
                          and 'punish' not in url_after)
            if tmd_passed:
                tmd_solved = True
                log.info("  ✓ Redirected! TMD solved!")
            else:
                # Check if content looks like results (maybe in-place reload)
                has_products = bool(re.findall(
                    r'data-product-card|"itemId"|"productId"|class="[^"]*card', html_after))
                has_prices = bool(re.findall(r'\d+[.,]\d{2}\s*(?:RUB|₽|EUR|€)', html_after))
                if has_products or has_prices:
                    tmd_solved = True
                    log.info("  ✓ Results content detected in TMD page!")

            # ── [5] If still TMD, re-navigate ──
            if not tmd_solved:
                log.info("── [5] Re-navigating to search (cookies should be set) ──")
                page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                time.sleep(5)
                url2 = page.url
                html2 = page.content()
                log.info("  2nd nav: URL=%s, %dB", url2[:120], len(html2))

                if '_____tmd_____' not in url2 and 'punish' not in url2:
                    tmd_solved = True
                    log.info("  ✓ 2nd nav passed TMD!")
                else:
                    # Check for iframes
                    frames = page.frames
                    log.info("  Frames: %d", len(frames))
                    for i, fr in enumerate(frames):
                        log.info("    Frame[%d]: %s (%dB)", i, fr.url[:80], len(fr.content()) if fr.content() else 0)

                    # Try one more drag on the new TMD
                    time.sleep(2)
                    if natural_drag(page, log):
                        time.sleep(8)
                        url3 = page.url
                        html3 = page.content()
                        log.info("  3rd attempt: URL=%s, %dB", url3[:100], len(html3))
                        if '_____tmd_____' not in url3:
                            tmd_solved = True

        else:
            # Direct hit
            html_direct = page.content()
            log.info("  Direct: %dB", len(html_direct))
            if len(html_direct) > 100000:
                tmd_solved = True

        # ── [6] Final page analysis ──
        if tmd_solved:
            log.info("── [6] Results analysis ──")
            results_html = page.content()
            log.info("  Size: %dB", len(results_html))

            t = re.search(r'<title>([^<]+)</title>', results_html)
            log.info("  Title: %s", t.group(1)[:120] if t else '?')

            prices = re.findall(r'(\d{1,5}[.,]\d{2})\s*(?:EUR|€|USD|\$|RUB|₽)', results_html)
            log.info("  Prices: %d, first 8: %s", len(prices), prices[:8])

            rub = len(re.findall(r'\d+[.,]\d{2}\s*(?:RUB|₽)', results_html))
            eur = len(re.findall(r'\d+[.,]\d{2}\s*(?:EUR|€)', results_html))
            log.info("  Currency: RUB=%d EUR=%d", rub, eur)

            ru_words = re.findall(r'[а-яА-ЯёЁ]{4,}', results_html)
            log.info("  RU words: %d (sample: %s)", len(ru_words), ru_words[:8])

            save_path = os.path.join(OUT_DIR, 'diag_v7s_results.html')
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(results_html)
            log.info("  Saved → %s (%dB)", save_path, len(results_html))

        # ── [7] Save cookies ──
        cookies_list = ctx.cookies()
        cookies_path = os.path.join(OUT_DIR, 'diag_v7s_cookies.json')
        with open(cookies_path, 'w', encoding='utf-8') as f:
            json.dump(cookies_list, f, indent=2, ensure_ascii=False)
        log.info("  Cookies: %d → %s", len(cookies_list), cookies_path)

        # Save verify responses
        vr_path = os.path.join(OUT_DIR, 'diag_v7s_verify.json')
        with open(vr_path, 'w', encoding='utf-8') as f:
            json.dump(verify_responses, f, indent=2, ensure_ascii=False)
        log.info("  Verify responses: %d → %s", len(verify_responses), vr_path)

        browser.close()

    # ── [8] API with cookies ──
    log.info("── [8] API fallback ──")
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
    for c in cookies_list:
        try:
            s.cookies.set(c['name'], c['value'], domain=c['domain'])
        except Exception:
            try:
                s.cookies.set(c['name'], c['value'])
            except Exception:
                pass

    time.sleep(3)

    # Proper AE search API endpoints
    api_tests = [
        # Mobile API (JSON)
        ("m-search",
         f"https://m.aliexpress.com/fn/search-pc/index?SearchText={quote(SEARCH)}&page=1&sortType=total_tranpro_desc&cookieFlag=1",
         "GET"),
        # Desktop fn API
        ("fn-search",
         f"https://ru.aliexpress.com/fn/search-pc/index?SearchText={quote(SEARCH)}&page=1&sortType=total_tranpro_desc&cookieFlag=1",
         "GET"),
        # Old-style API
        ("aer-api",
         f"https://aer-webapi.aliexpress.com/fn/search-pc/index?SearchText={quote(SEARCH)}&page=1&sortType=total_tranpro_desc",
         "GET"),
        # H5 API
        ("h5-search",
         f"https://m.aliexpress.com/fn/search-pc/index?SearchText={quote(SEARCH)}&page=1&sortType=total_tranpro_desc&xman_us_f=b_locale%3Dru_RU%26c_tp%3DRUB%26region%3DRU%26site%3Drus",
         "GET"),
    ]

    for name, url, method in api_tests:
        try:
            log.info("  [%s] %s", name, url[:100])
            r = s.get(url, timeout=15)
            ct = r.headers.get('Content-Type', '')
            log.info("  [%s] HTTP %d, %dB, CT=%s", name, r.status_code, len(r.content), ct[:50])

            if 'json' in ct or (r.content and r.content[:1] in (b'{', b'[')):
                log.info("  [%s] ✓ JSON response!", name)
                data = r.json()
                if isinstance(data, dict):
                    log.info("  [%s] Keys: %s", name, list(data.keys())[:10])
                    # Look for product list
                    for key in ('products', 'items', 'result', 'data', 'moduleList'):
                        if key in data:
                            val = data[key]
                            if isinstance(val, list):
                                log.info("  [%s] %s: %d items", name, key, len(val))
                                if val:
                                    log.info("  [%s] First: %s", name, json.dumps(val[0], ensure_ascii=False)[:200])
                            elif isinstance(val, dict):
                                log.info("  [%s] %s keys: %s", name, key, list(val.keys())[:8])
            else:
                # HTML response — check if it's the SSR page
                text = r.text[:300]
                log.info("  [%s] Body[:300]: %s", name, text.replace('\n', ' ')[:250])

            # Save full response
            save_api = os.path.join(OUT_DIR, f'diag_v7s_api_{name}.txt')
            with open(save_api, 'w', encoding='utf-8') as f:
                f.write(f"URL: {url}\nStatus: {r.status_code}\nCT: {ct}\n\n{r.text[:5000]}")
        except Exception as e:
            log.warning("  [%s] Error: %s", name, e)
        time.sleep(2)

    # ── [9] Summary ──
    log.info("── Summary ──")
    log.info("  TMD solved: %s", tmd_solved)
    log.info("  Results: %dB", len(results_html) if results_html else 0)
    log.info("  Cookies: %d", len(cookies_list))
    log.info("  Verify responses: %d", len(verify_responses))
    log.info("=== DONE ===")
    log.info("diag_v7s complete")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.exception("FATAL: %s", e)
        sys.exit(1)
