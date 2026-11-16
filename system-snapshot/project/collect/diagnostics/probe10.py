r"""probe10.py — фиксированный прогон 4 площадок.

Фиксы vs probe9:
  - logging без %f (Windows strftime)
  - Farpost: /verify → click button/form, longer wait, manual redirect
  - Ozon: mobile URL + homepage warmup + networkidle
  - WB: 498 body analysis + price API cffi + homepage→product
  - Avito: shorter per-step, category→listing→card
"""

import json
import logging
import re
import sys
import time
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("probe10")

RESULTS = {}

def mark(platform, strategy, ok, detail=""):
    key = f"{platform}:{strategy}"
    RESULTS[key] = {"ok": ok, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    log.info("[%s] %s -> %s | %s", platform, strategy, "PASS" if ok else "FAIL", detail[:140])

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PW_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

PW_INIT = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en']});"
    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
    "window.chrome={runtime:{}};"
)


def launch_pw():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=PW_ARGS)
    ctx = browser.new_context(
        user_agent=UA,
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        timezone_id="Asia/Vladivostok",
    )
    ctx.add_init_script(PW_INIT)
    return pw, browser, ctx


def safe_close(pw, browser):
    try:
        browser.close()
        pw.stop()
    except Exception:
        pass


# ================================================================
# PHASE 1: FARPOST — /verify deep-dive
# ================================================================

def phase_farpost():
    log.info("=== PHASE 1: FARPOST ===")
    t0 = time.monotonic()
    listing_id = "139122082"
    url = f"https://www.farpost.ru/vladivostok/{listing_id}.html"

    pw, browser, ctx = launch_pw()
    try:
        # Warmup: главная
        page = ctx.new_page()
        page.goto("https://www.farpost.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        cookies = ctx.cookies()
        log.info("FARPOST warmup cookies: %s", [c["name"] for c in cookies])
        page.close()

        # Открыть карточку
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)

        if "/verify" in page.url:
            log.info("FARPOST: on /verify, url=%s", page.url)

            # Анализ /verify страницы
            verify_html = page.content()
            log.info("FARPOST /verify: len=%d", len(verify_html))

            # Сохранить для анализа
            with open("farpost_verify.html", "w", encoding="utf-8") as f:
                f.write(verify_html[:100000])

            # Стратегия 1: найти и кликнуть кнопку/ссылку
            clicked = False
            for selector in [
                "button", "input[type=submit]", "a.btn",
                "[class*=verify]", "[class*=check]", "[class*=confirm]",
                "#verify-btn", ".verify-button", "form button",
            ]:
                try:
                    els = page.query_selector_all(selector)
                    if els:
                        log.info("FARPOST /verify: found %d elements for '%s'", len(els), selector)
                        for el in els[:3]:
                            txt = el.inner_text().strip()[:50] if el.inner_text() else ""
                            tag = el.evaluate("el => el.tagName")
                            log.info("  %s: '%s'", tag, txt)
                        els[0].click()
                        clicked = True
                        log.info("FARPOST /verify: clicked '%s'", selector)
                        break
                except Exception as e:
                    log.info("FARPOST /verify: selector '%s' err: %s", selector, str(e)[:50])

            if clicked:
                page.wait_for_timeout(8000)
                log.info("FARPOST /verify after click: url=%s, len=%d", page.url, len(page.content()))

            # Стратегия 2: если ещё на /verify — подождать meta-refresh / JS timer
            if "/verify" in page.url:
                log.info("FARPOST: still /verify, waiting 15s for JS timer...")
                page.wait_for_timeout(15000)
                log.info("FARPOST: after 15s wait url=%s", page.url)

            # Стратегия 3: ручной redirect по ?u= параметру
            if "/verify" in page.url:
                log.info("FARPOST: manual redirect to target url")
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)
                log.info("FARPOST: after manual redirect url=%s", page.url)

            # Стратегия 4: JS-вызов fetch к целевому URL с текущими cookie
            if "/verify" in page.url:
                try:
                    data = page.evaluate("""
                        async () => {
                            const r = await fetch(window.location.href.replace('/verify?u=', '/'), {
                                credentials: 'include',
                                headers: {'Accept': 'text/html'}
                            });
                            const text = await r.text();
                            return {status: r.status, len: text.length, snippet: text.substring(0, 500)};
                        }
                    """)
                    log.info("FARPOST JS fetch: %s", data)
                except Exception as e:
                    log.info("FARPOST JS fetch err: %s", e)

        # Финальная проверка
        html = page.content()
        status = resp.status if resp else 0
        final_url = page.url
        log.info("FARPOST final: HTTP %d, url=%s, len=%d", status, final_url[:80], len(html))

        # Извлечение данных
        price = None
        title = None

        # data-bulletin-price
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m:
            price = m.group(1)

        # JSON-LD
        if not price:
            blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            for b in blocks:
                try:
                    ld = json.loads(b.strip())
                    if isinstance(ld, dict) and ld.get("@type") == "Product":
                        title = ld.get("name", "")
                        offers = ld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price = str(offers.get("price", ""))
                        break
                except Exception:
                    pass

        # Title fallback
        if not title:
            tm = re.search(r'<title[^>]*>([^<]+)</title>', html)
            if tm:
                title = tm.group(1)[:80]

        ok = bool(price) and "/verify" not in final_url
        mark("FARPOST", "pw_verify_deep", ok,
             f"price={price} title={str(title)[:40]} url_verify={'/verify' in final_url} len={len(html)}")

        # Сохранить итоговый HTML
        with open("farpost_pw10.html", "w", encoding="utf-8") as f:
            f.write(html[:200000])

        page.close()
    except Exception as e:
        log.warning("FARPOST error: %s\n%s", e, traceback.format_exc()[:400])
    finally:
        safe_close(pw, browser)

    log.info("FARPOST done in %.1fs", time.monotonic() - t0)


# ================================================================
# PHASE 2: OZON — mobile + homepage + networkidle
# ================================================================

def phase_ozon():
    log.info("=== PHASE 2: OZON ===")
    t0 = time.monotonic()
    listing_id = "3481320800"

    pw, browser, ctx = launch_pw()
    try:
        # --- Strategy A: Homepage → product (desktop) ---
        page = ctx.new_page()
        resp = page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)
        log.info("OZON A homepage: HTTP %d, len=%d", resp.status if resp else 0, len(page.content()))

        # Переход к товару
        prod_url = f"https://www.ozon.ru/product/{listing_id}/"
        resp2 = page.goto(prod_url, wait_until="networkidle", timeout=40000)
        page.wait_for_timeout(3000)
        html = page.content()
        status = resp2.status if resp2 else 0
        log.info("OZON A product: HTTP %d, len=%d, url=%s", status, len(html), page.url[:80])

        if status == 200 and len(html) > 5000:
            # JSON-LD
            blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            price = None
            title = None
            for b in blocks:
                try:
                    ld = json.loads(b.strip())
                    if isinstance(ld, dict) and ld.get("@type") == "Product":
                        title = ld.get("name", "")
                        offers = ld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price = str(offers.get("price", ""))
                        break
                except Exception:
                    pass

            # DOM price
            if not price:
                pm = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', html)
                if pm:
                    val = pm.group(1).replace("\u00a0", "").replace(" ", "").strip()
                    if val.isdigit() and int(val) > 10:
                        price = val

            mark("OZON", "A_hp_product", bool(price),
                 f"price={price} title={str(title)[:40]} len={len(html)}")
            with open("ozon_pw10_a.html", "w", encoding="utf-8") as f:
                f.write(html[:200000])
        else:
            mark("OZON", "A_hp_product", False, f"status={status} len={len(html)}")
            with open("ozon_pw10_a.html", "w", encoding="utf-8") as f:
                f.write(html[:100000])
        page.close()

        # --- Strategy B: Mobile URL ---
        page = ctx.new_page()
        m_url = f"https://m.ozon.ru/product/{listing_id}/"
        resp3 = page.goto(m_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)
        m_html = page.content()
        m_status = resp3.status if resp3 else 0
        log.info("OZON B mobile: HTTP %d, len=%d, url=%s", m_status, len(m_html), page.url[:80])

        if m_status == 200 and len(m_html) > 3000:
            price_b = None
            tm = re.search(r'<title[^>]*>([^<]+)</title>', m_html)
            title_b = tm.group(1)[:60] if tm else ""
            pm = re.search(r'([\d\s\u00a0]+)\s*₽', m_html)
            if pm:
                val = pm.group(1).replace("\u00a0", "").replace(" ", "").strip()
                if val.isdigit() and int(val) > 10:
                    price_b = val
            mark("OZON", "B_mobile", bool(price_b), f"price={price_b} title={title_b} len={len(m_html)}")
            with open("ozon_pw10_b.html", "w", encoding="utf-8") as f:
                f.write(m_html[:150000])
        else:
            mark("OZON", "B_mobile", False, f"status={m_status} len={len(m_html)}")
        page.close()

        # --- Strategy C: Composer API через Playwright XHR ---
        page = ctx.new_page()
        page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        try:
            api_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{listing_id}/"
            data = page.evaluate("""
                async (url) => {
                    try {
                        const r = await fetch(url, {
                            headers: {'Accept': 'application/json'},
                            credentials: 'include'
                        });
                        const text = await r.text();
                        return {status: r.status, len: text.length, snippet: text.substring(0, 800)};
                    } catch(e) {
                        return {error: e.message};
                    }
                }
            """, api_url)
            log.info("OZON C composer XHR: %s", str(data)[:200])
            if data.get("status") == 200:
                mark("OZON", "C_composer_xhr", True, f"len={data.get('len')}")
                with open("ozon_pw10_c.json", "w", encoding="utf-8") as f:
                    f.write(data.get("snippet", ""))
            else:
                mark("OZON", "C_composer_xhr", False, f"status={data.get('status')} err={data.get('error','')}")
        except Exception as e:
            mark("OZON", "C_composer_xhr", False, str(e)[:80])
        page.close()

    except Exception as e:
        log.warning("OZON error: %s\n%s", e, traceback.format_exc()[:400])
    finally:
        safe_close(pw, browser)

    log.info("OZON done in %.1fs", time.monotonic() - t0)


# ================================================================
# PHASE 3: WILDBERRIES — CDN + 498 body + price API
# ================================================================

def phase_wb():
    log.info("=== PHASE 3: WILDBERRIES ===")
    t0 = time.monotonic()
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000

    # --- Strategy A: Basket CDN (уже работает) ---
    try:
        from curl_cffi import requests as cffi
        host = "basket-42.wbbasket.ru"
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        resp = cffi.get(f"https://{host}{path}", impersonate="chrome", timeout=15,
                        headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if resp.status_code == 200:
            card = resp.json()
            mark("WB", "A_cdn", True, f"keys={len(card)} name={card.get('imt_name','')[:40]}")
        else:
            mark("WB", "A_cdn", False, f"HTTP {resp.status_code}")
    except Exception as e:
        mark("WB", "A_cdn", False, str(e)[:80])

    # --- Strategy B: Price API через cffi (card.wb.ru) ---
    try:
        from curl_cffi import requests as cffi
        price_found = None
        for ver in ["v1", "v2", "v4", "v5"]:
            ep = f"https://card.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
            try:
                r = cffi.get(ep, impersonate="chrome", timeout=12,
                             headers={
                                 "Accept": "application/json",
                                 "Referer": "https://www.wildberries.ru/",
                                 "Origin": "https://www.wildberries.ru",
                             })
                log.info("WB B card.%s: HTTP %d, len=%d", ver, r.status_code, len(r.content))
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and data:
                        item = data[0]
                        price_found = item.get("priceU") or item.get("price") or item.get("basic")
                        size = item.get("size", {})
                        log.info("WB B card.%s: priceU=%s basic=%s size_keys=%s",
                                 ver, item.get("priceU"), item.get("basic"), list(size.keys())[:5] if isinstance(size, dict) else "?")
                        with open(f"wb_price_{ver}.json", "w", encoding="utf-8") as f:
                            json.dump(item, f, ensure_ascii=False, indent=2)
                        break
            except Exception as e:
                log.info("WB B card.%s err: %s", ver, str(e)[:60])
            time.sleep(2)

        mark("WB", "B_price_api", bool(price_found), f"price={price_found}")
    except Exception as e:
        mark("WB", "B_price_api", False, str(e)[:80])

    # --- Strategy C: Playwright detail.aspx — анализ 498 + homepage warmup ---
    pw, browser, ctx = launch_pw()
    try:
        # Homepage first (cookies)
        page = ctx.new_page()
        page.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)
        wb_cookies = ctx.cookies()
        log.info("WB C homepage cookies: %s", [c["name"] for c in wb_cookies][:10])
        page.close()

        # Product page
        page = ctx.new_page()
        prod_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        resp = page.goto(prod_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        html = page.content()
        status = resp.status if resp else 0
        log.info("WB C product: HTTP %d, len=%d, url=%s", status, len(html), page.url[:80])

        # Анализ тела (даже 498)
        with open("wb_pw10_detail.html", "w", encoding="utf-8") as f:
            f.write(html[:200000])

        price_c = None
        title_c = None

        # Title
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if tm:
            title_c = tm.group(1)[:80]

        # Price patterns (широкий поиск)
        price_pats = [
            r'"priceU"\s*:\s*(\d+)',
            r'"price"\s*:\s*(\d+)',
            r'"basic"\s*:\s*(\d+)',
            r'"card"\s*:\s*(\d+)',
            r'"wallet"\s*:\s*(\d+)',
            r'data-testid="price"[^>]*>([\d\s\u00a0]+)',
            r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)\s*(?:₽|руб)',
            r'([\d\s\u00a0]{3,})\s*₽',
        ]
        for pat in price_pats:
            m = re.search(pat, html)
            if m:
                val = m.group(1).replace("\u00a0", "").replace(" ", "").strip()
                if val.isdigit() and int(val) > 100:
                    price_c = int(val)
                    log.info("WB C price from '%s': %d", pat[:40], price_c)
                    break

        # JS: window.__INITIAL_STATE__ или data-атрибуты
        if not price_c:
            try:
                state = page.evaluate("""
                    () => {
                        if (window.__INITIAL_STATE__) {
                            const s = JSON.stringify(window.__INITIAL_STATE__);
                            return s.substring(0, 1000);
                        }
                        const el = document.querySelector('[data-testid="price"], .c-price, [class*="price"]');
                        return el ? el.textContent : 'NO_EL';
                    }
                """)
                log.info("WB C JS: %s", str(state)[:200])
                m2 = re.search(r'(\d{4,})', str(state))
                if m2:
                    price_c = int(m2.group(1))
            except Exception as e:
                log.info("WB C JS err: %s", e)

        # XHR performance entries
        try:
            xhrs = page.evaluate("""
                () => performance.getEntriesByType('resource')
                    .filter(e => e.name.includes('card.wb.ru') || e.name.includes('catalog.wb.ru') || e.name.includes('web/api'))
                    .map(e => e.name).slice(0, 10);
            """)
            log.info("WB C XHRs: %s", xhrs[:5])
        except Exception:
            pass

        mark("WB", "C_pw_detail", bool(price_c),
             f"price={price_c} title={str(title_c)[:40]} status={status} len={len(html)}")
        page.close()

    except Exception as e:
        log.warning("WB C error: %s\n%s", e, traceback.format_exc()[:300])
    finally:
        safe_close(pw, browser)

    log.info("WB done in %.1fs", time.monotonic() - t0)


# ================================================================
# PHASE 4: AVITO — category → listing → card
# ================================================================

def phase_avito():
    log.info("=== PHASE 4: AVITO ===")
    t0 = time.monotonic()

    pw, browser, ctx = launch_pw()
    try:
        # Warmup
        page = ctx.new_page()
        page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)
        log.info("AVITO warmup: len=%d", len(page.content()))
        page.close()

        # Категория
        cat_url = "https://www.avito.ru/vladivostok/telefony_i_smartfony"
        page = ctx.new_page()
        resp = page.goto(cat_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)
        html = page.content()
        status = resp.status if resp else 0
        log.info("AVITO cat: HTTP %d, len=%d", status, len(html))

        listing_url = None
        listing_id = None

        if status == 200 and len(html) > 5000:
            # Ссылки на объявления
            links = re.findall(r'href="(?:https://www\.avito\.ru)?(/vladivostok/[^"]*?/(\d{9,12}))"', html)
            if not links:
                links = re.findall(r'href="(?:https://www\.avito\.ru)?(/[^"]*?/(\d{9,12}))"', html)
            log.info("AVITO cat: %d links", len(links))

            if links:
                listing_id = links[0][1]
                listing_url = "https://www.avito.ru" + links[0][0]
                log.info("AVITO target: %s (%s)", listing_id, listing_url[:70])
            else:
                # captcha?
                cap = "captcha" in html.lower() or "hcaptcha" in html.lower()
                log.info("AVITO cat: no links, captcha=%s", cap)
                with open("avito_pw10_cat.html", "w", encoding="utf-8") as f:
                    f.write(html[:100000])
        else:
            log.info("AVITO cat: bad status=%d len=%d", status, len(html))
            with open("avito_pw10_cat.html", "w", encoding="utf-8") as f:
                f.write(html[:100000])
        page.close()

        # Карточка
        if listing_url:
            page = ctx.new_page()
            resp2 = page.goto(listing_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            l_html = page.content()
            l_status = resp2.status if resp2 else 0
            log.info("AVITO listing %s: HTTP %d, len=%d", listing_id, l_status, len(l_html))

            price = None
            title = None

            # JSON-LD
            blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                l_html, re.DOTALL
            )
            for b in blocks:
                try:
                    ld = json.loads(b.strip())
                    if isinstance(ld, dict) and ld.get("@type") in ("Product", "Offer"):
                        title = ld.get("name", "")
                        offers = ld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price = offers.get("price") or ld.get("price")
                        break
                except Exception:
                    pass

            # DOM
            if not price:
                pm = re.search(r'class="[^"]*price[^"]*"[^>]*>([\d\s\u00a0]+)', l_html)
                if pm:
                    val = pm.group(1).replace("\u00a0", "").replace(" ", "").strip()
                    if val.isdigit():
                        price = int(val)

            if not title:
                tm = re.search(r'<h1[^>]*>(.*?)</h1>', l_html, re.DOTALL)
                if tm:
                    title = re.sub(r'<[^>]+>', '', tm.group(1)).strip()[:80]

            mark("AVITO", f"listing_{listing_id}", bool(price),
                 f"price={price} title={str(title)[:40]} len={len(l_html)}")
            with open("avito_pw10_card.html", "w", encoding="utf-8") as f:
                f.write(l_html[:150000])
            page.close()
        else:
            mark("AVITO", "find", False, "no listing in category")

    except Exception as e:
        log.warning("AVITO error: %s\n%s", e, traceback.format_exc()[:400])
    finally:
        safe_close(pw, browser)

    log.info("AVITO done in %.1fs", time.monotonic() - t0)


# ================================================================
# MAIN
# ================================================================

def main():
    t_start = time.monotonic()
    log.info("=== PROBE10 START ===")

    phase_farpost()
    phase_ozon()
    phase_wb()
    phase_avito()

    elapsed = time.monotonic() - t_start
    log.info("=== PROBE10 DONE in %.1fs ===", elapsed)

    log.info("--- SUMMARY ---")
    for key, val in sorted(RESULTS.items()):
        log.info("  %-25s %s | %s", key, "PASS" if val["ok"] else "FAIL", val["detail"][:90])

    with open("probe10_results.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)

    any_pass = any(v["ok"] for v in RESULTS.values())
    log.info("Any PASS: %s", any_pass)
    sys.exit(0)


if __name__ == "__main__":
    main()
