#!/usr/bin/env python3
"""diag_v7q — Playwright TMD solver for AliExpress RU.
Strategy: open punish page in real browser, let 79KB JS execute,
capture new x5secdata cookie or redirect, then use for search.
"""
import json, re, time, logging, sys, os
from urllib.parse import quote

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('diag_v7q')

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
SEARCH = "наушники"
BASE_RU = "https://ru.aliexpress.com/wholesale"


def main():
    log.info("=== diag_v7q — %s ===", time.strftime('%Y-%m-%d %H:%M:%S'))

    # Cooldown
    log.info("Cooldown 20s...")
    time.sleep(20)

    # ── [1] Playwright: solve TMD ──
    log.info("── [1] Playwright TMD solve ──")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("  playwright not installed")
        sys.exit(1)

    new_x5 = None
    tmd_solved = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
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
        # Anti-detection
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = {runtime: {}};
        """)

        page = ctx.new_page()

        # [1a] Warm-up: visit home
        log.info("  [warmup] Home page...")
        page.goto("https://ru.aliexpress.com/", wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)
        cookies_warm = ctx.cookies()
        log.info("  [warmup] Cookies after home: %d", len(cookies_warm))
        for c in cookies_warm[:5]:
            log.info("    %s = %s... (domain=%s)", c['name'], c['value'][:40], c['domain'])

        # [1b] Navigate to search → expect TMD redirect
        search_url = f"{BASE_RU}?SearchText={quote(SEARCH)}&page=1"
        log.info("  [search] → %s", search_url)
        page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(2)

        # Check if we're on TMD page
        current_url = page.url
        title = page.title()
        log.info("  [search] URL: %s", current_url)
        log.info("  [search] Title: %s", title[:100])

        is_tmd = ('_____tmd_____' in current_url or 'punish' in current_url
                  or 'nocaptcha' in page.content()[:2000]
                  or 'x5secdata' in page.content()[:5000])

        if not is_tmd:
            # Maybe direct hit!
            content_len = len(page.content())
            log.info("  [search] Direct hit? content=%dB", content_len)
            if content_len > 50000:
                tmd_solved = True
                log.info("  ✓ Direct search page, no TMD!")
        else:
            log.info("  [search] TMD page detected. Waiting for JS to solve...")

            # [1c] Wait for TMD JS to execute and redirect
            # The 79KB JS should auto-solve (noCaptcha) and redirect
            # Give it up to 20 seconds
            log.info("  [solve] Waiting up to 20s for auto-redirect...")
            try:
                page.wait_for_url(
                    lambda u: '_____tmd_____' not in u and 'punish' not in u,
                    timeout=20000
                )
                log.info("  [solve] ✓ Redirected to: %s", page.url)
                tmd_solved = True
            except Exception as e:
                log.info("  [solve] No auto-redirect in 20s: %s", str(e)[:100])

            # [1d] If still on TMD, check if JS set a cookie
            time.sleep(3)
            cookies_tmd = ctx.cookies()
            x5_cookies = [c for c in cookies_tmd if c['name'] == 'x5secdata']
            log.info("  [solve] x5secdata cookies: %d", len(x5_cookies))
            for c in x5_cookies:
                log.info("    domain=%s value=%s... (len=%d)", c['domain'], c['value'][:50], len(c['value']))

            # [1e] Try clicking any button (some TMD pages need interaction)
            try:
                btn = page.query_selector('button, [role="button"], .nc-container, #nocaptcha')
                if btn:
                    log.info("  [solve] Found button/captcha element, clicking...")
                    btn.click()
                    time.sleep(5)
                    cookies_after = ctx.cookies()
                    x5_after = [c for c in cookies_after if c['name'] == 'x5secdata']
                    log.info("  [solve] After click: %d x5secdata cookies", len(x5_after))
                    for c in x5_after:
                        log.info("    domain=%s value=%s...", c['domain'], c['value'][:50])
            except Exception as e:
                log.info("  [solve] Click attempt: %s", str(e)[:100])

            # [1f] Check if page auto-reloaded with content
            content_len = len(page.content())
            log.info("  [solve] Page content: %dB", content_len)
            if content_len > 50000:
                tmd_solved = True
                log.info("  ✓ Full page loaded after TMD!")

            # [1g] Last resort: manual reload after JS execution
            if not tmd_solved:
                log.info("  [solve] Trying reload...")
                page.reload(wait_until='domcontentloaded', timeout=30000)
                time.sleep(5)
                content_len = len(page.content())
                log.info("  [solve] After reload: %dB, URL=%s", content_len, page.url)
                if content_len > 50000 and '_____tmd_____' not in page.url:
                    tmd_solved = True
                    log.info("  ✓ TMD solved via reload!")

        # [1h] Extract final cookies
        cookies_final = ctx.cookies()
        log.info("  [final] Total cookies: %d", len(cookies_final))
        x5_final = [c for c in cookies_final if c['name'] == 'x5secdata']
        for c in x5_final:
            log.info("  [final] x5secdata domain=%s len=%d", c['domain'], len(c['value']))
            new_x5 = c['value']

        # [1i] If solved, parse the page
        if tmd_solved:
            log.info("── [2] Page analysis ──")
            html = page.content()
            lang_m = re.search(r'<html[^>]*lang=["\']?(\w+)', html)
            log.info("  lang='%s'", lang_m.group(1) if lang_m else '?')
            t = re.search(r'<title>([^<]+)</title>', html)
            if t:
                log.info("  title: %s", t.group(1)[:120])
            prices = re.findall(r'(\d{1,4}[.,]\d{2})\s*(?:EUR|€|USD|\$|RUB|₽)', html)
            log.info("  Prices: %d, first 5: %s", len(prices), prices[:5])
            log.info("  Domains: it=%d, ru=%d", html.count('it.aliexpress'), html.count('ru.aliexpress'))

            # Save HTML for later
            save_path = os.path.join(OUT_DIR, 'diag_v7q_search.html')
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(html)
            log.info("  Saved → %s (%dB)", save_path, len(html))

            # Count product cards
            cards = re.findall(r'data-product-card|class="[^"]*card[^"]*"', html)
            log.info("  Card-like elements: %d", len(cards))

        # [1j] Save all cookies as JSON for reuse
        cookies_path = os.path.join(OUT_DIR, 'diag_v7q_cookies.json')
        with open(cookies_path, 'w', encoding='utf-8') as f:
            json.dump(cookies_final, f, indent=2)
        log.info("  Cookies saved → %s", cookies_path)

        browser.close()

    # ── [3] If we got x5secdata, test with curl_cffi ──
    if new_x5 and not tmd_solved:
        log.info("── [3] curl_cffi with new x5secdata ──")
        try:
            from curl_cffi import requests as cr
        except ImportError:
            import requests as cr

        s = cr.Session(impersonate="chrome")
        s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        # Set cookies from Playwright
        for c in cookies_final:
            try:
                s.cookies.set(c['name'], c['value'], domain=c['domain'])
            except Exception:
                pass

        time.sleep(3)
        r = s.get(search_url, timeout=20)
        log.info("  Search: HTTP %d, %dB", r.status_code, len(r.content))
        if len(r.content) > 50000:
            log.info("  ✓ Full page via curl_cffi!")
            tmd_solved = True
            prices = re.findall(r'(\d{1,4}[.,]\d{2})\s*(?:EUR|€|USD|\$|RUB|₽)', r.text)
            log.info("  Prices: %d, first 5: %s", len(prices), prices[:5])

    # ── [4] Summary ──
    log.info("── Summary ──")
    log.info("  TMD solved: %s", tmd_solved)
    log.info("  New x5secdata: %s", new_x5[:60] if new_x5 else 'None')
    log.info("=== DONE ===")
    log.info("diag_v7q complete")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.exception("FATAL: %s", e)
        sys.exit(1)
