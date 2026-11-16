r"""probe9.py — мульти-стратегийный прогон 4 площадок.

Фазы:
  1. FARPOST  — requests (длинный wait) + Playwright (JS /verify)
  2. OZON     — Playwright JSON-LD + SSR data-widget
  3. WB       — Basket CDN (meta) + Playwright detail.aspx (цена)
  4. AVITO    — Playwright категория → живое объявление → карточка

Запуск:
  C:\Python315\python.exe probe9.py 2>&1 | Tee-Object -FilePath probe9_log.txt
"""

import json
import logging
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S,%f",
)
log = logging.getLogger("probe9")

TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

RESULTS = {}

def mark(platform, strategy, ok, detail=""):
    key = f"{platform}:{strategy}"
    RESULTS[key] = {"ok": ok, "detail": detail, "ts": NOW()}
    log.info("[%s] %s → %s %s", platform, strategy, "PASS" if ok else "FAIL", detail[:120])

# ================================================================
# PHASE 1: FARPOST
# ================================================================

def phase_farpost():
    log.info("=== PHASE 1: FARPOST ===")
    t0 = time.monotonic()

    listing_ids = ["138859452", "138952176", "139122082"]

    # --- Strategy A: requests с длинным wait после /verify ---
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        # Warmup
        r0 = s.get("https://www.farpost.ru/", timeout=15)
        log.info("FARPOST warmup: HTTP %d, cookies=%s", r0.status_code, list(s.cookies.keys()))
        time.sleep(2)

        for lid in listing_ids[:2]:
            url = f"https://www.farpost.ru/vladivostok/{lid}.html"
            r = s.get(url, timeout=20, allow_redirects=True)
            final_url = r.url
            is_verify = "/verify" in final_url
            log.info("FARPOST A %s: HTTP %d, url=%s, verify=%s, len=%d",
                     lid, r.status_code, final_url[:80], is_verify, len(r.text))

            if is_verify:
                # Длинный wait — JS на /verify может ставить cookie
                time.sleep(12)
                r2 = s.get(url, timeout=20, allow_redirects=True)
                is_verify2 = "/verify" in r2.url
                log.info("FARPOST A %s retry: HTTP %d, verify=%s, len=%d",
                         lid, r2.status_code, is_verify2, len(r2.text))
                if not is_verify2 and r2.status_code == 200:
                    html = r2.text
                    price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                    title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
                    price = price_m.group(1) if price_m else None
                    title = title_m.group(1)[:60] if title_m else "?"
                    mark("FARPOST", f"A_req_{lid}", bool(price), f"price={price} title={title}")
                    if price:
                        return
                else:
                    mark("FARPOST", f"A_req_{lid}", False, f"still verify or short")
            elif r.status_code == 200 and len(r.text) > 500:
                html = r.text
                price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                mark("FARPOST", f"A_req_{lid}", bool(price_m), f"price={price_m.group(1) if price_m else None}")
                if price_m:
                    return
            time.sleep(3)
    except Exception as e:
        log.warning("FARPOST A error: %s", e)

    # --- Strategy B: Playwright (JS rendering handles /verify) ---
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

        # Warmup через Playwright
        page = ctx.new_page()
        page.goto("https://www.farpost.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        log.info("FARPOST B: warmup via PW OK")
        page.close()

        for lid in listing_ids:
            url = f"https://www.farpost.ru/vladivostok/{lid}.html"
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(4000)  # дать JS выполнить /verify redirect

                # Если ушли на /verify, подождать и дать JS отработать
                if "/verify" in page.url:
                    log.info("FARPOST B %s: on /verify, waiting 8s...", lid)
                    page.wait_for_timeout(8000)
                    # Иногда /verify делает meta-refresh или JS redirect
                    if "/verify" in page.url:
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(5000)

                html = page.content()
                status = resp.status if resp else 0
                log.info("FARPOST B %s: HTTP %d, url=%s, len=%d", lid, status, page.url[:80], len(html))

                if status == 200 and len(html) > 1000 and "/verify" not in page.url:
                    price_m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                    jsonld_m = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
                    title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
                    title = title_m.group(1)[:60] if title_m else "?"

                    price = price_m.group(1) if price_m else None
                    if not price and jsonld_m:
                        for block in jsonld_m:
                            try:
                                ld = json.loads(block)
                                if ld.get("@type") == "Product":
                                    offers = ld.get("offers", {})
                                    if isinstance(offers, list):
                                        offers = offers[0] if offers else {}
                                    price = str(offers.get("price", ""))
                            except:
                                pass

                    mark("FARPOST", f"B_pw_{lid}", bool(price), f"price={price} title={title} jsonld={len(jsonld_m)}")
                    if price:
                        page.close()
                        break
                else:
                    mark("FARPOST", f"B_pw_{lid}", False, f"status={status} verify={'/verify' in page.url} len={len(html)}")
            except Exception as e:
                mark("FARPOST", f"B_pw_{lid}", False, str(e)[:80])
            finally:
                page.close()

        browser.close()
        pw.stop()
    except Exception as e:
        log.warning("FARPOST B (PW) error: %s", e)
        try:
            browser.close()
            pw.stop()
        except:
            pass

    log.info("FARPOST phase done in %.1fs", time.monotonic() - t0)


# ================================================================
# PHASE 2: OZON
# ================================================================

def phase_ozon():
    log.info("=== PHASE 2: OZON ===")
    t0 = time.monotonic()

    # Тестовый товар: 3481320800 (Маленький принц открытки, price=284)
    listing_id = "3481320800"
    url = f"https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-{listing_id}/"

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en']});"
        )

        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)  # дать SSR + JS отработать

        html = page.content()
        status = resp.status if resp else 0
        log.info("OZON PW: HTTP %d, len=%d, url=%s", status, len(html), page.url[:80])

        if status == 200 and len(html) > 2000:
            # --- Extract JSON-LD ---
            jsonld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            product_ld = None
            for block in jsonld_blocks:
                try:
                    ld = json.loads(block.strip())
                    if isinstance(ld, dict) and ld.get("@type") == "Product":
                        product_ld = ld
                        break
                    if isinstance(ld, list):
                        for item in ld:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                product_ld = item
                                break
                except:
                    pass

            if product_ld:
                name = product_ld.get("name", "")
                offers = product_ld.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = offers.get("price")
                currency = offers.get("priceCurrency", "RUB")
                avail = offers.get("availability", "")
                brand = product_ld.get("brand", "")
                desc = product_ld.get("description", "")[:200]
                img = product_ld.get("image", "")
                sku = product_ld.get("sku", "")

                log.info("OZON JSON-LD: name=%s price=%s %s brand=%s sku=%s",
                         name[:50], price, currency, brand, sku)
                mark("OZON", "PW_jsonld", bool(price), f"price={price}{currency} name={name[:40]} brand={brand}")

                # Сохранить полный JSON-LD
                with open("ozon_pw9_jsonld.json", "w", encoding="utf-8") as f:
                    json.dump(product_ld, f, ensure_ascii=False, indent=2)
            else:
                mark("OZON", "PW_jsonld", False, f"no Product JSON-LD in {len(jsonld_blocks)} blocks")

            # --- Extract SSR data-widget / state ---
            # Ozon SSR: <div data-widget="..."> или window.__STATE__
            state_m = re.search(r'window\.__STATE__\s*=\s*(\{.*?\})\s*;</script>', html, re.DOTALL)
            if state_m:
                try:
                    state = json.loads(state_m.group(1))
                    log.info("OZON __STATE__ found, keys=%s", list(state.keys())[:10])
                    mark("OZON", "PW_state", True, f"keys={list(state.keys())[:5]}")
                except:
                    mark("OZON", "PW_state", False, "parse error")

            # --- Extract images ---
            imgs = re.findall(r'(https://ir\.ozone\.ru/s3/[^"\'<>\s]+)', html)
            log.info("OZON images found: %d", len(imgs))

            # --- Extract price from DOM (fallback) ---
            price_dom = re.search(r'class="[^"]*price[^"]*"[^>]*>([\d\s\u00a0]+)₽', html)
            if price_dom:
                log.info("OZON DOM price: %s", price_dom.group(1).strip())

            # --- XHR: composer-api (intercept) ---
            # Уже загружен при SSR, попробуем получить через performance API
            try:
                perf = page.evaluate("""
                    () => {
                        return performance.getEntriesByType('resource')
                            .filter(e => e.name.includes('composer') || e.name.includes('summary'))
                            .map(e => e.name)
                            .slice(0, 10);
                    }
                """)
                log.info("OZON XHR resources: %s", perf[:5])
            except Exception as e:
                log.info("OZON perf error: %s", e)

            # Сохранить HTML для анализа
            with open("ozon_pw9_html.html", "w", encoding="utf-8") as f:
                f.write(html[:200000])

        else:
            mark("OZON", "PW_page", False, f"status={status} len={len(html)}")

        page.close()
        browser.close()
        pw.stop()
    except Exception as e:
        log.warning("OZON PW error: %s\n%s", e, traceback.format_exc()[:300])

    log.info("OZON phase done in %.1fs", time.monotonic() - t0)


# ================================================================
# PHASE 3: WILDBERRIES
# ================================================================

def phase_wb():
    log.info("=== PHASE 3: WILDBERRIES ===")
    t0 = time.monotonic()

    nm_id = 1097975275
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

    # --- Strategy A: Basket CDN (metadata, уже работает) ---
    try:
        from curl_cffi import requests as cffi
        vol = nm_id // 100000
        part = nm_id // 1000
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

        # Найденный хост: basket-42
        host = "basket-42.wbbasket.ru"
        card_url = f"https://{host}{path}"
        resp = cffi.get(card_url, impersonate="chrome", timeout=15,
                        headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if resp.status_code == 200:
            card = resp.json()
            log.info("WB A: card.json OK, nm_id=%s, name=%s",
                     card.get("nm_id"), card.get("imt_name", "")[:50])
            mark("WB", "A_cdn_card", True, f"keys={len(card)} name={card.get('imt_name','')[:40]}")

            # Фото из CDN
            photos = []
            for i in range(1, 6):
                photo_url = f"https://{host}/vol{vol}/part{part}/{nm_id}/images/{nm_id}-{i}.webp"
                photos.append(photo_url)
            log.info("WB A: photos constructed: %d", len(photos))
        else:
            mark("WB", "A_cdn_card", False, f"HTTP {resp.status_code}")
    except Exception as e:
        mark("WB", "A_cdn_card", False, str(e)[:80])

    # --- Strategy B: Playwright на detail.aspx (цена из DOM) ---
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)  # WB грузит цену асинхронно

        html = page.content()
        status = resp.status if resp else 0
        log.info("WB B: HTTP %d, len=%d, url=%s", status, len(html), page.url[:80])

        if status == 200 and len(html) > 3000:
            # Цена из DOM: span с классом price / data-testid
            price_patterns = [
                r'data-testid="price"[^>]*>([\d\s\u00a0]+)',
                r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)\s*(?:₽|руб)',
                r'"price"\s*:\s*(\d+)',
                r'"priceU"\s*:\s*(\d+)',
                r'"basic"\s*:\s*(\d+)',
                r'"card"\s*:\s*(\d+)',
                r'"wallet"\s*:\s*(\d+)',
                r'data-qa="price"[^>]*>([\d\s\u00a0]+)',
                r'class="c-price__value[^"]*"[^>]*>([\d\s\u00a0]+)',
            ]
            price_found = None
            for pat in price_patterns:
                m = re.search(pat, html)
                if m:
                    val = m.group(1).replace("\u00a0", "").replace(" ", "").strip()
                    if val.isdigit() and int(val) > 100:
                        price_found = int(val)
                        log.info("WB B: price from pattern '%s' = %d", pat[:40], price_found)
                        break

            # Также попробуем через JS: window.__INITIAL_STATE__ или data-атрибуты
            if not price_found:
                try:
                    state = page.evaluate("""
                        () => {
                            if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__).slice(0, 500);
                            const el = document.querySelector('[data-testid="price"], .c-price, [class*="price"]');
                            return el ? el.textContent : 'NO_PRICE_EL';
                        }
                    """)
                    log.info("WB B: JS state/price = %s", str(state)[:200])
                    if state and state != "NO_PRICE_EL":
                        m2 = re.search(r'(\d{4,})', str(state))
                        if m2:
                            price_found = int(m2.group(1))
                except Exception as e:
                    log.info("WB B: JS eval error: %s", e)

            # Title
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
            title = title_m.group(1)[:60] if title_m else "?"

            mark("WB", "B_pw_price", bool(price_found), f"price={price_found} title={title}")

            # Сохранить HTML
            with open("wb_pw9_html.html", "w", encoding="utf-8") as f:
                f.write(html[:200000])

            # --- Strategy C: intercept XHR для price API ---
            try:
                api_calls = page.evaluate("""
                    () => {
                        return performance.getEntriesByType('resource')
                            .filter(e => e.name.includes('card.wb.ru') || e.name.includes('catalog.wb.ru') || e.name.includes('web/api'))
                            .map(e => e.name)
                            .slice(0, 10);
                    }
                """)
                log.info("WB B: API XHRs: %s", api_calls[:5])
            except:
                pass

        else:
            mark("WB", "B_pw_price", False, f"status={status} len={len(html)}")

        page.close()
        browser.close()
        pw.stop()
    except Exception as e:
        log.warning("WB B (PW) error: %s\n%s", e, traceback.format_exc()[:300])
        try:
            browser.close()
            pw.stop()
        except:
            pass

    log.info("WB phase done in %.1fs", time.monotonic() - t0)


# ================================================================
# PHASE 4: AVITO
# ================================================================

def phase_avito():
    log.info("=== PHASE 4: AVITO ===")
    t0 = time.monotonic()

    # Стратегия: Playwright на категорию → найти живое объявление → открыть
    # Категория: смартфоны (короткий URL)
    cat_urls = [
        "https://www.avito.ru/vladivostok/telefony_i_smartfony",
        "https://www.avito.ru/vladivostok/noutbuki",
        "https://www.avito.ru/vladivostok/telefony_i_smartfony?q=iphone",
    ]

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en']});"
        )

        # Warmup: главная
        page = ctx.new_page()
        resp = page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)
        log.info("AVITO warmup: HTTP %d, len=%d", resp.status if resp else 0, len(page.content()))
        page.close()

        listing_url_found = None
        for cat_url in cat_urls:
            page = ctx.new_page()
            try:
                resp = page.goto(cat_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)
                html = page.content()
                status = resp.status if resp else 0
                log.info("AVITO cat %s: HTTP %d, len=%d", cat_url.split("/")[-1], status, len(html))

                if status == 200 and len(html) > 5000:
                    # Найти ссылки на объявления: /vladivostok/.../NNNNNNNNNN
                    links = re.findall(r'href="(https://www\.avito\.ru/vladivostok/[^"]*?/(\d{9,12}))"', html)
                    if not links:
                        links = re.findall(r'href="(/vladivostok/[^"]*?/(\d{9,12}))"', html)

                    log.info("AVITO cat: found %d listing links", len(links))
                    if links:
                        # Берём первое
                        full_url = links[0][0]
                        if full_url.startswith("/"):
                            full_url = "https://www.avito.ru" + full_url
                        listing_id = links[0][1]
                        listing_url_found = (full_url, listing_id)
                        log.info("AVITO: target listing %s (%s)", listing_id, full_url[:80])
                        break
                    else:
                        # Может быть hCaptcha
                        captcha = "captcha" in html.lower() or "hcaptcha" in html.lower()
                        log.info("AVITO cat: no links, captcha=%s", captcha)
                else:
                    log.info("AVITO cat: bad response status=%d len=%d", status, len(html))
            except Exception as e:
                log.info("AVITO cat error: %s", e)
            finally:
                page.close()

        # --- Открыть найденное объявление ---
        if listing_url_found:
            full_url, listing_id = listing_url_found
            page = ctx.new_page()
            try:
                resp = page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                html = page.content()
                status = resp.status if resp else 0
                log.info("AVITO listing %s: HTTP %d, len=%d", listing_id, status, len(html))

                if status == 200 and len(html) > 3000:
                    # JSON-LD
                    jsonld_blocks = re.findall(
                        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html, re.DOTALL
                    )
                    product_ld = None
                    for block in jsonld_blocks:
                        try:
                            ld = json.loads(block.strip())
                            if isinstance(ld, dict) and ld.get("@type") in ("Product", "Offer"):
                                product_ld = ld
                                break
                            if isinstance(ld, list):
                                for item in ld:
                                    if isinstance(item, dict) and item.get("@type") in ("Product", "Offer"):
                                        product_ld = item
                                        break
                        except:
                            pass

                    price = None
                    title = None
                    if product_ld:
                        title = product_ld.get("name", "")
                        offers = product_ld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price = offers.get("price") or product_ld.get("price")
                        log.info("AVITO JSON-LD: title=%s price=%s", title[:50], price)

                    # DOM price
                    if not price:
                        price_m = re.search(r'class="[^"]*price[^"]*"[^>]*>([\d\s\u00a0]+)', html)
                        if price_m:
                            val = price_m.group(1).replace("\u00a0", "").replace(" ", "")
                            if val.isdigit():
                                price = int(val)

                    # Title fallback
                    if not title:
                        title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
                        if title_m:
                            title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()[:60]

                    mark("AVITO", f"PW_listing_{listing_id}", bool(price),
                         f"price={price} title={title or '?'} jsonld={'Y' if product_ld else 'N'}")

                    with open("avito_pw9_html.html", "w", encoding="utf-8") as f:
                        f.write(html[:150000])
                else:
                    mark("AVITO", f"PW_listing_{listing_id}", False, f"status={status} len={len(html)}")
            except Exception as e:
                mark("AVITO", f"PW_listing_{listing_id}", False, str(e)[:80])
            finally:
                page.close()
        else:
            mark("AVITO", "find_listing", False, "no listing found in categories")

        browser.close()
        pw.stop()
    except Exception as e:
        log.warning("AVITO PW error: %s\n%s", e, traceback.format_exc()[:300])
        try:
            browser.close()
            pw.stop()
        except:
            pass

    log.info("AVITO phase done in %.1fs", time.monotonic() - t0)


# ================================================================
# MAIN
# ================================================================

def main():
    t_start = time.monotonic()
    log.info("=== PROBE9 START ===")

    # Поочерёдно (RAM-ограничение: один Playwright за раз)
    phase_farpost()
    phase_ozon()
    phase_wb()
    phase_avito()

    elapsed = time.monotonic() - t_start
    log.info("=== PROBE9 DONE in %.1fs ===", elapsed)

    # Сводка
    log.info("--- SUMMARY ---")
    for key, val in sorted(RESULTS.items()):
        log.info("  %s: %s %s", key, "PASS" if val["ok"] else "FAIL", val["detail"][:80])

    # JSON
    with open("probe9_results.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)

    # Exit code: 0 если хоть что-то прошло
    any_pass = any(v["ok"] for v in RESULTS.values())
    log.info("Any PASS: %s", any_pass)
    sys.exit(0)


if __name__ == "__main__":
    main()
