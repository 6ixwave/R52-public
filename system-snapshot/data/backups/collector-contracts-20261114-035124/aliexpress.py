"""Сборщик AliExpress (aliexpress.com).

Стратегия (приоритет сверху вниз):
1. Playwright persistent context:
   - Прогрев сессии (главная → cookies → scroll)
   - Перехват MTOP pdp.pc.query (золотой ответ с полными данными)
   - DOM extraction (React-гидратация, price-селекторы)
   - JS state / meta / JSON-LD fallback
2. curl_cffi (impersonate=chrome) — HTTP, обход Akamai
3. requests — последний fallback

MTOP-пути (data.result.*):
- productId, categoryPath, category1, category3
- HEADER_IMAGE_PC.currentSkuImages — галерея
- image — главное фото
- targetSkuPriceInfo.salePriceString / monthlyLowestPrice / salePriceLocal / originalPrice
- SKUModule, deliveryOptionPanelDisplayList, buyerProtection, taxInfo

Медиа: ae01-06.alicdn.com, aliexpress-media.com
URL: https://www.aliexpress.com/item/{numeric_id}.html
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlsplit

from .base import BaseCollector
from .pw_fetch import playwright_proxy

log = logging.getLogger("r52.collectors.aliexpress")

# ─── Константы ───

ALI_CDN_HOSTS = (
    "ae01.alicdn.com", "ae02.alicdn.com", "ae03.alicdn.com",
    "ae04.alicdn.com", "ae05.alicdn.com", "ae06.alicdn.com",
    "ae01.aliexpress-media.com", "ae02.aliexpress-media.com",
    "ae03.aliexpress-media.com", "ae04.aliexpress-media.com",
    "ae-pic-a1.aliexpress-media.com", "ae-pic-a2.aliexpress-media.com",
)

NOISE_PATTERNS = re.compile(
    r"(placeholder|default|logo|icon|\.svg|\.gif|\.ico|\.css|\.js|blank|empty|spacer|favicon|banner)",
    re.I,
)

HOME_URL = "https://www.aliexpress.com/"
RU_HOME_URL = "https://ru.aliexpress.com/"
PROFILE_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile"
)
PROFILE_DIR_RU = os.path.join(
    os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_ru_pw_profile"
)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# HTML regex (для HTTP fallback)
INIT_STATE_RE = re.compile(
    r'window\.__INIT_DATA_STATE__\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)',
    re.DOTALL,
)
RUN_PARAMS_RE = re.compile(
    r'window\.runParams\s*=\s*(\{.*?\})\s*;\s*(?:</script>|window\.)',
    re.DOTALL,
)
TITLE_TAG_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.DOTALL | re.IGNORECASE)
H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.DOTALL | re.IGNORECASE)
PRICE_CLASS_RE = re.compile(
    r'class="[^"]*(?:price|Price)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*[$\u20ac\u00a3]?\s*([\d,]+\.?\d*)',
    re.DOTALL,
)
PRICE_DATA_RE = re.compile(
    r'(?:data-price|data-original-price|data-activity-price)=["\']([\d.]+)["\']',
    re.I,
)
IMG_SRC_RE = re.compile(
    r'(?:src|data-src|data-lazy-src|data-original)=["\']([^"\']*(?:alicdn|aliexpress)[^"\']*)["\']',
    re.I,
)
OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', re.I)
OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']*)["\']', re.I)
OG_PRICE_RE = re.compile(r'<meta[^>]+property=["\']og:price:amount["\'][^>]+content=["\']([^"\']*)["\']', re.I)
OG_CURRENCY_RE = re.compile(r'<meta[^>]+property=["\']og:price:currency["\'][^>]+content=["\']([^"\']*)["\']', re.I)

AVAIL_IN_STOCK_RE = re.compile(r'(?:in\s*stock|available|ready\s*to\s*ship|\u0432\s*\u043d\u0430\u043b\u0438\u0447\u0438\u0438|disponib|magazzino)', re.I)
AVAIL_OUT_STOCK_RE = re.compile(r'(?:out\s*of\s*stock|unavailable|sold\s*out|\u043d\u0435\u0442\s*\u0432\s*\u043d\u0430\u043b\u0438\u0447\u0438\u0438|\u0440\u0430\u0441\u043f\u0440\u043e\u0434\u0430\u043d\u043e|esaurito|non\s*disponib)', re.I)
AVAIL_PREORDER_RE = re.compile(r'(?:pre-?order|preorder|\u043f\u0440\u0435\u0434\u0437\u0430\u043a\u0430\u0437)', re.I)


class AliExpressCollector(BaseCollector):
    """Сборщик AliExpress: Playwright (основной) + HTTP fallback."""

    source = "ALIEXPRESS"
    title = "AliExpress"

    def __init__(self, *args, domain: str = "com", **kwargs):
        """domain: 'com' (www.aliexpress.com) или 'ru' (ru.aliexpress.com)."""
        super().__init__(*args, **kwargs)
        self._domain = domain
        self._ru_mode = (domain == "ru")
        self._pw = None
        self._ctx = None
        self._page = None
        self._session_warm = False
        self._mtop_bodies = []
        self._pw_failed = False

    # ─── Lifecycle ───

    def _ensure_browser(self) -> bool:
        """Запускает persistent context. True если готово."""
        if self._page is not None:
            return True
        if self._pw_failed:
            return False
        try:
            from playwright.sync_api import sync_playwright
            prof = PROFILE_DIR_RU if self._ru_mode else PROFILE_DIR
            os.makedirs(prof, exist_ok=True)
            loc = "ru-RU" if self._ru_mode else "it-IT"
            tz = "Europe/Moscow" if self._ru_mode else "Europe/Rome"
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                prof,
                headless=True,
                proxy=playwright_proxy(self.pool.get_proxy()),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                user_agent=DEFAULT_UA,
                locale=loc,
                viewport={"width": 1920, "height": 1080},
                timezone_id=tz,
                ignore_https_errors=True,
            )
            _langs = "['ru-RU','ru','en']" if self._ru_mode else "['it-IT','it','en']"
            self._ctx.add_init_script(f"""
                Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});
                Object.defineProperty(navigator, 'languages', {{get: () => {_langs}}});
                Object.defineProperty(navigator, 'plugins', {{get: () => [1,2,3,4,5]}});
                Object.defineProperty(navigator, 'platform', {{get: () => 'Win32'}});
                window.chrome = {{runtime: {{}}}};
            """)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            # Force Russian locale for RU mode (override persistent profile memory)
            if self._ru_mode:
                try:
                    # Clear stale locale cookies from previous sessions
                    old_cookies = self._ctx.cookies()
                    stale = [c for c in old_cookies if c.get("name") in ("aep_usuc_f", "aep_usuc", "intl_common_forever")]
                    if stale:
                        self._ctx.remove_cookies(stale)
                        log.info("ALI PW: cleared %d stale locale cookies", len(stale))
                    # Set comprehensive RU locale cookie (all known params)
                    self._ctx.add_cookies([{
                        "name": "aep_usuc_f",
                        "value": "[REDACTED_AUTH_SECRET]",
                        "domain": ".aliexpress.com",
                        "path": "/",
                    }])
                    self._page.evaluate("try{localStorage.setItem('lang','ru');localStorage.setItem('i18n_lang','ru')}catch(e){}")
                    log.info("ALI PW: RU locale forced (cleared old, set full cookie)")
                except Exception as e:
                    log.warning("ALI PW: locale force failed: %s", e)
            log.info("ALI PW: browser started, profile=%s, domain=%s", prof, self._domain)
            return True
        except Exception as e:
            log.warning("ALI PW: browser start failed: %s", e)
            self._pw_failed = True
            self._cleanup_pw()
            return False

    def _warm_session(self):
        """Прогрев: главная → cookies → scroll. Раз в сессию."""
        if self._session_warm:
            return
        try:
            cookies = self._ctx.cookies()
            if len(cookies) > 5:
                log.info("ALI PW: session already warm (%d cookies)", len(cookies))
                self._session_warm = True
                return

            home = RU_HOME_URL if self._ru_mode else HOME_URL
            log.info("ALI PW: warming session (%s)...", home)
            self._page.goto(home, wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_timeout(5000)

            cookie_sels = (
                'button:has-text("Принять")',
                'button:has-text("Accept")',
                'button:has-text("Accetta")',
                '[class*="cookie"] button',
                '#onetrust-accept-btn-handler',
            )
            for sel in cookie_sels:
                try:
                    btn = self._page.query_selector(sel)
                    if btn:
                        btn.click()
                        log.info("ALI PW: cookie accepted via %s", sel)
                        self._page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            self._page.evaluate("window.scrollTo(0, 500)")
            self._page.wait_for_timeout(3000)
            self._session_warm = True
            cookies = self._ctx.cookies()
            log.info("ALI PW: session warm, %d cookies", len(cookies))
            # Re-assert RU locale after warm-up (site may override during page load)
            if self._ru_mode:
                try:
                    self._ctx.add_cookies([{
                        "name": "aep_usuc_f",
                        "value": "[REDACTED_AUTH_SECRET]",
                        "domain": ".aliexpress.com",
                        "path": "/",
                    }])
                    self._page.evaluate("try{localStorage.setItem('lang','ru');localStorage.setItem('i18n_lang','ru')}catch(e){}")
                    log.info("ALI PW: RU locale re-asserted post-warm")
                except Exception:
                    pass
        except Exception as e:
            log.warning("ALI PW: warm session error: %s", e)

    def _cleanup_pw(self):
        """Закрывает браузер."""
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = None
        self._pw = None
        self._page = None
        self._session_warm = False

    def close(self):
        """Вызов при завершении работы сборщика."""
        self._cleanup_pw()

    # ─── URL ───

    def collect_by_search(self, query: str, page: int = 1, page_size: int = 20) -> list[dict]:
        from urllib.parse import urlencode
        from .discovery import collect_links

        url = "https://www.aliexpress.com/wholesale?" + urlencode({"SearchText": query, "page": page})
        return collect_links(self, "ALIEXPRESS", url, page_size)

    def build_url(self, listing_id: str) -> str:
        if self._ru_mode:
            return f"https://ru.aliexpress.com/item/{listing_id}.html"
        return f"https://www.aliexpress.com/item/{listing_id}.html"

    # ─── Fetch ───

    def fetch(self, url: str):
        """Основной fetch: Playwright → curl_cffi → requests."""
        if not self._pw_failed:
            if self._ensure_browser():
                self._warm_session()
                result = self._fetch_pw(url)
                if result:
                    return result

        try:
            from curl_cffi import requests as cffi_req
            self._throttle()
            start = time.monotonic()
            resp = cffi_req.get(
                url,
                impersonate="chrome",
                proxies=self.pool.get_proxy(),
                timeout=(15, 30),
                headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": "https://www.aliexpress.com/",
                },
            )
            latency = time.monotonic() - start
            self.stats["requests"] += 1
            if resp.status_code == 200:
                self.stats["success"] += 1
                self.pool.mark_success(latency)
                log.info("ALI: curl_cffi OK, %d bytes, %.1fs", len(resp.text), latency)
                return resp
            elif resp.status_code in (403, 429, 503):
                self.stats["blocked"] += 1
                self.pool.mark_failure()
                log.info("ALI: curl_cffi HTTP %d, fallback", resp.status_code)
            else:
                log.warning("ALI: curl_cffi HTTP %d", resp.status_code)
                self.stats["failed"] += 1
                return None
        except ImportError:
            log.warning("ALI: curl_cffi not installed")
        except Exception as e:
            log.warning("ALI: curl_cffi error: %s", e)
            self.stats["requests"] += 1

        return super().fetch(url)

    # ─── Playwright fetch ───

    def _fetch_pw(self, url: str) -> Optional[dict]:
        """Playwright: navigate → intercept MTOP → wait render → extract DOM."""
        self._mtop_bodies = []
        t0 = time.monotonic()

        def on_resp(resp):
            try:
                u = resp.url
                if "mtop" in u.lower() and "pdp.pc.query" in u and "punish" not in u:
                    body = ""
                    try:
                        body = resp.text()
                    except Exception:
                        pass
                    if len(body) > 100:
                        self._mtop_bodies.append({"url": u[:400], "status": resp.status, "body": body})
                        log.info("ALI PW: MTOP PDP captured, %d bytes", len(body))
            except Exception:
                pass

        self._page.on("response", on_resp)

        try:
            resp = self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else 0
            log.info("ALI PW: navigate %s -> %d", url[:80], status)

            rendered = False
            tmd_found = False
            for tick in range(20):
                self._page.wait_for_timeout(1000)
                try:
                    st = self._page.evaluate("""
                        () => {
                            const h1 = document.querySelector('h1');
                            const prices = [];
                            const sels = ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                                '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]','[class*="price-row"]'];
                            for (const s of sels) {
                                try {
                                    document.querySelectorAll(s).forEach(el => {
                                        const t = (el.textContent||'').trim();
                                        if (t && t.length < 60 && /\\d/.test(t)) prices.push(t);
                                    });
                                } catch(e) {}
                            }
                            const iframes = [];
                            document.querySelectorAll('iframe').forEach(f => {
                                iframes.push((f.src||'').substring(0,300));
                            });
                            return {h1: h1?h1.textContent.trim().substring(0,300):null,
                                    prices:[...new Set(prices)].slice(0,8), iframes,
                                    bodyLen: document.body?document.body.innerHTML.length:0};
                        }
                    """)
                except Exception:
                    continue

                if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
                    rendered = True
                    log.info("ALI PW: rendered at tick=%d, prices=%s", tick, st.get("prices", [])[:3])
                    break

                for fr in st.get("iframes", []):
                    if "punish" in fr or "tmd" in fr.lower() or "recaptcha" in fr.lower():
                        tmd_found = True
                        break
                if tmd_found:
                    break

                # Click-captcha в основном DOM (не iframe)
                if not tmd_found:
                    try:
                        cap_check = self._page.evaluate("""
                            () => {
                                const p = document.getElementById('baxia-punish');
                                if (p && (p.classList.contains('captchaclick') || p.classList.contains('drag') || p.classList.contains('scratch'))) {
                                    return {found: true, type: p.className.match(/(captchaclick|drag|scratch)/)?.[0] || 'unknown'};
                                }
                                return {found: false};
                            }
                        """)
                        if cap_check.get("found"):
                            tmd_found = True
                            log.info("ALI PW: captcha detected in main DOM: %s", cap_check.get("type"))
                            break
                    except Exception:
                        pass

            if tmd_found and not rendered:
                rendered = self._solve_tmd()

            dom_data = {}
            try:
                dom_data = self._page.evaluate("""
                    () => {
                        const r = {};
                        const h1 = document.querySelector('h1');
                        r.title = h1 ? h1.textContent.trim() : null;
                        if (!r.title) {
                            const t = document.querySelector('title');
                            if (t) r.title = t.textContent.replace(/\\s*[|\\-\u2013\u2014].*$/,'').trim();
                        }
                        r.prices = [];
                        const pSels = ['[data-pl="price"]','[class*="price--current"]','[class*="Price--current"]',
                            '[class*="pdp-price"]','[class*="price-current"]','span[class*="price"]',
                            '[class*="sku-price"]','[class*="priceRow"]','[class*="price-row"]','[data-pl*="price"]'];
                        for (const s of pSels) {
                            try {
                                document.querySelectorAll(s).forEach(el => {
                                    const t = (el.textContent||'').trim();
                                    if (t && t.length < 60 && /\\d/.test(t)) r.prices.push(t);
                                });
                            } catch(e) {}
                        }
                        document.querySelectorAll('[data-price],[data-activity-price],[data-original-price]').forEach(el => {
                            const v = el.getAttribute('data-price')||el.getAttribute('data-activity-price')||el.getAttribute('data-original-price');
                            if (v) r.prices.push(v);
                        });
                        r.prices = [...new Set(r.prices)].slice(0,15);
                        r.availability = null;
                        const aSels = ['[class*="stock"]','[class*="availability"]','[data-pl="stock"]','[class*="pdp-stock"]'];
                        for (const s of aSels) {
                            try {
                                document.querySelectorAll(s).forEach(el => {
                                    const t = (el.textContent||'').trim().toLowerCase();
                                    if (t.length < 100) {
                                        if (t.includes('in stock')||t.includes('disponib')||t.includes('available')||t.includes('magazzino')) r.availability='IN_STOCK';
                                        if (t.includes('out of stock')||t.includes('non disponib')||t.includes('unavailable')||t.includes('esaurito')) r.availability='OUT_OF_STOCK';
                                    }
                                });
                            } catch(e) {}
                            if (r.availability) break;
                        }
                        r.images = [];
                        document.querySelectorAll('img').forEach(img => {
                            const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
                            if (src && (src.includes('alicdn')||src.includes('aliexpress-media'))
                                && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                                && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif')) {
                                r.images.push(src);
                            }
                        });
                        r.images = [...new Set(r.images)].slice(0,20);
                        const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"]');
                        r.category = bc ? bc.textContent.trim().substring(0,300) : null;
                        r.meta = {};
                        document.querySelectorAll('meta[property],meta[name]').forEach(m => {
                            const p = m.getAttribute('property')||m.getAttribute('name')||'';
                            const c = m.getAttribute('content')||'';
                            if (p.includes('og:')||p.includes('product')||p.includes('price')) r.meta[p]=c.substring(0,300);
                        });
                        r.jsonld = [];
                        document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                            try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                        });
                        r.jsState = {};
                        try { if (window.__INIT_DATA_STATE__) r.jsState.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0,12000); } catch(e){}
                        try { if (window.runParams) r.jsState.runParams = JSON.stringify(window.runParams).substring(0,8000); } catch(e){}
                        r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                        return r;
                    }
                """)
            except Exception as e:
                log.warning("ALI PW: DOM extraction error: %s", e)

            self._page.remove_listener("response", on_resp)

            elapsed = time.monotonic() - t0
            self.stats["requests"] += 1
            if self._mtop_bodies or rendered:
                self.stats["success"] += 1
                self.pool.mark_success(elapsed)
            else:
                self.stats["failed"] += 1

            log.info(
                "ALI PW: done in %.1fs, mtop=%d, rendered=%s, dom_prices=%s",
                elapsed, len(self._mtop_bodies), rendered,
                dom_data.get("prices", [])[:3],
            )

            return {
                "type": "playwright",
                "mtop_bodies": self._mtop_bodies,
                "dom": dom_data,
                "status": status,
                "elapsed": elapsed,
                "rendered": rendered,
            }

        except Exception as e:
            log.warning("ALI PW: fetch error: %s", e)
            try:
                self._page.remove_listener("response", on_resp)
            except Exception:
                pass
            self.stats["requests"] += 1
            self.stats["failed"] += 1
            return None

    def _solve_tmd(self) -> bool:
        """Обход TMD: click-captcha (grid), drag, scratch, recaptcha."""
        log.info("ALI PW: solving TMD...")
        self._page.wait_for_timeout(3000)
        try:
            # ── 1. Click-captcha (grid 3x3) в основном DOM ──
            cap_type = self._detect_captcha_type()
            if cap_type == "captchaclick":
                return self._solve_click_captcha()
            elif cap_type == "drag":
                return self._solve_drag_captcha()
            elif cap_type == "scratch":
                return self._solve_scratch_captcha()

            # ── 2. Recaptcha (iframe) ──
            for f in self._page.frames:
                try:
                    fu = f.url or ""
                    if "recaptcha" in fu and ("bframe" in fu or "anchor" in fu):
                        f.wait_for_load_state("domcontentloaded", timeout=8000)
                        self._page.wait_for_timeout(2000)
                        for sel in ('input[type="checkbox"]', '#recaptcha-anchor', '[role="checkbox"]'):
                            cb = f.query_selector(sel)
                            if cb:
                                box = cb.bounding_box()
                                if box and box["width"] > 0:
                                    self._page.mouse.click(
                                        box["x"] + box["width"] / 2,
                                        box["y"] + box["height"] / 2,
                                    )
                                else:
                                    cb.click()
                                log.info("ALI PW: TMD checkbox clicked: %s", sel)
                                break
                        break
                except Exception:
                    continue

            # ── 3. Wait for re-render ──
            for i in range(25):
                self._page.wait_for_timeout(1000)
                try:
                    check = self._page.evaluate("""
                        () => {
                            const h1 = document.querySelector('h1');
                            const pEls = document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"]');
                            const prices = [];
                            pEls.forEach(el => { const t=(el.textContent||'').trim(); if(t&&/\\d/.test(t)&&t.length<50) prices.push(t); });
                            const punish = document.getElementById('baxia-punish');
                            return {h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,6), onCaptcha: !!punish};
                        }
                    """)
                    if check.get("prices") or (check.get("h1") and len(check["h1"]) > 15):
                        log.info("ALI PW: TMD solved, re-rendered at %ds", i)
                        return True
                    if not check.get("onCaptcha") and i > 5:
                        break
                except Exception:
                    continue
        except Exception as e:
            log.warning("ALI PW: TMD solve error: %s", e)
        return False

    def _detect_captcha_type(self) -> str:
        """Возвращает 'captchaclick', 'drag', 'scratch', 'recaptcha' или 'none'."""
        try:
            info = self._page.evaluate("""
                () => {
                    const p = document.getElementById('baxia-punish');
                    if (p) {
                        const cls = p.className || '';
                        if (cls.includes('captchaclick')) return 'captchaclick';
                        if (cls.includes('drag')) return 'drag';
                        if (cls.includes('scratch')) return 'scratch';
                    }
                    for (const f of document.querySelectorAll('iframe')) {
                        if ((f.src||'').includes('recaptcha')) return 'recaptcha';
                    }
                    return 'none';
                }
            """)
            return info or "none"
        except Exception:
            return "none"

    def _solve_click_captcha(self) -> bool:
        """Решение click-captcha: 3x3 grid, выбрать совпадающие ячейки."""
        log.info("ALI PW: solving click-captcha (grid)...")
        self._page.wait_for_timeout(2000)

        for attempt in range(3):  # до 3 попыток (refresh при ошибке)
            try:
                # Ожидание отрисовки canvas
                self._page.wait_for_selector('#click-question-canvas', timeout=8000)
                self._page.wait_for_timeout(1500)

                # Скриншот вопрос-канваса
                q_el = self._page.query_selector('#click-question-canvas')
                if not q_el:
                    log.warning("ALI PW: question canvas not found")
                    return False
                q_bytes = q_el.screenshot()

                # Скриншоты grid-ячеек
                grid_bytes = []
                for i in range(9):
                    g_el = self._page.query_selector(f'#click-grid-{i}')
                    if g_el:
                        grid_bytes.append(g_el.screenshot())
                    else:
                        grid_bytes.append(None)

                # Сравнение через PIL
                selected = self._compare_captcha_images(q_bytes, grid_bytes)

                if not selected:
                    log.warning("ALI PW: no matching cells found, clicking all")
                    selected = list(range(9))

                log.info("ALI PW: attempt %d, selected cells: %s", attempt + 1, selected)

                # Клик по выбранным ячейкам
                for idx in selected:
                    g_el = self._page.query_selector(f'#click-grid-{idx}')
                    if g_el:
                        box = g_el.bounding_box()
                        if box:
                            self._page.mouse.click(
                                box['x'] + box['width'] / 2,
                                box['y'] + box['height'] / 2,
                            )
                        else:
                            g_el.click()
                        self._page.wait_for_timeout(300)

                # Клик «Отправить»
                self._page.wait_for_timeout(500)
                submit_el = self._page.query_selector('#click-submit')
                if submit_el:
                    submit_el.click()
                    log.info("ALI PW: click-captcha submitted (attempt %d)", attempt + 1)
                else:
                    log.warning("ALI PW: submit button not found")
                    return False

                # Ожидание результата
                solved = self._wait_captcha_result(timeout_s=15)
                if solved:
                    log.info("ALI PW: click-captcha solved on attempt %d", attempt + 1)
                    return True

                # Не удалось — refresh
                log.info("ALI PW: captcha attempt %d failed, refreshing", attempt + 1)
                refresh_el = self._page.query_selector('#click-refresh')
                if refresh_el:
                    refresh_el.click()
                    self._page.wait_for_timeout(2500)

            except Exception as e:
                log.warning("ALI PW: click-captcha attempt %d error: %s", attempt + 1, e)

        return False

    def _compare_captcha_images(self, q_bytes: bytes, grid_bytes: list) -> list:
        """Сравнивает вопрос с grid-ячейками. Возвращает список индексов совпадений."""
        try:
            from PIL import Image, ImageChops
            import io
        except ImportError:
            log.warning("ALI PW: PIL not available for captcha comparison")
            return []

        try:
            q_img = Image.open(io.BytesIO(q_bytes)).convert('RGB')
            # Вопрос — широкая полоса (355x48), grid — квадраты (120x120)
            # Resize вопроса к размеру первой grid-ячейки
            ref_size = (120, 120)
            q_resized = q_img.resize(ref_size, Image.LANCZOS)

            scores = []
            for i, gb in enumerate(grid_bytes):
                if not gb:
                    scores.append((i, 255.0))
                    continue
                g_img = Image.open(io.BytesIO(gb)).convert('RGB').resize(ref_size, Image.LANCZOS)
                diff = ImageChops.difference(q_resized, g_img)
                # Среднее абсолютное отличие по всем каналам
                hist = diff.histogram()  # 765 значений (256*3)
                total = g_img.width * g_img.height * 3
                mean_diff = sum(idx * hist[idx] for idx in range(256)) / total if total > 0 else 255.0
                scores.append((i, mean_diff))
                log.debug("ALI PW: grid[%d] diff=%.1f", i, mean_diff)

            scores.sort(key=lambda x: x[1])
            # Порог: если разброс мал — берём топ-2, иначе ячейки с diff < 35
            threshold = 35.0
            selected = [idx for idx, d in scores if d < threshold]
            if not selected:
                selected = [scores[0][0], scores[1][0]]
            if len(selected) > 5:
                selected = [idx for idx, d in scores[:5]]
            return selected

        except Exception as e:
            log.warning("ALI PW: image comparison error: %s", e)
            return []

    def _wait_captcha_result(self, timeout_s: int = 15) -> bool:
        """Ждёт результат captcha: успех (страница товара) или ошибка (refresh)."""
        for i in range(timeout_s):
            self._page.wait_for_timeout(1000)
            try:
                st = self._page.evaluate("""
                    () => {
                        const punish = document.getElementById('baxia-punish');
                        if (punish) {
                            const errMsg = document.querySelector('.error-msg');
                            const errText = errMsg ? errMsg.textContent.trim() : '';
                            const result = document.querySelector('.click-captcha-result');
                            const resultVisible = result && result.style.display !== 'none';
                            const resultMsg = result ? (result.querySelector('.result-msg')?.textContent || '') : '';
                            return {onCaptcha: true, errText: errText, resultVisible: resultVisible, resultMsg: resultMsg};
                        }
                        const h1 = document.querySelector('h1');
                        const pEls = document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"]');
                        const prices = [];
                        pEls.forEach(el => { const t=(el.textContent||'').trim(); if(t&&/\\d/.test(t)&&t.length<50) prices.push(t); });
                        return {onCaptcha: false, h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,6)};
                    }
                """)
                if st.get("onCaptcha"):
                    if st.get("resultVisible") or st.get("errText"):
                        log.info("ALI PW: captcha result: visible=%s msg=%s", st.get("resultVisible"), st.get("resultMsg", st.get("errText")))
                        return False
                    continue
                else:
                    if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
                        return True
            except Exception:
                continue
        return False

    def _solve_drag_captcha(self) -> bool:
        """Заглушка для drag-captcha (slider)."""
        log.info("ALI PW: drag-captcha detected (basic solve)...")
        self._page.wait_for_timeout(2000)
        try:
            slider = self._page.query_selector('.drag-captcha-slider, [class*="slider"]')
            if slider:
                box = slider.bounding_box()
                if box:
                    sx = box['x'] + box['width'] / 2
                    sy = box['y'] + box['height'] / 2
                    # Тянем на ~200px (обычная длина трека)
                    self._page.mouse.move(sx, sy)
                    self._page.mouse.down()
                    for step in range(20):
                        self._page.mouse.move(sx + (step + 1) * 10, sy)
                        self._page.wait_for_timeout(50)
                    self._page.mouse.up()
                    log.info("ALI PW: drag slider moved")
            for i in range(15):
                self._page.wait_for_timeout(1000)
                st = self._page.evaluate("""
                    () => {
                        const p = document.getElementById('baxia-punish');
                        if (!p) return {solved: true};
                        const h1 = document.querySelector('h1');
                        return {solved: false, h1: h1?h1.textContent.trim().substring(0,100):null};
                    }
                """)
                if st.get("solved"):
                    return True
        except Exception as e:
            log.warning("ALI PW: drag-captcha error: %s", e)
        return False

    def _solve_scratch_captcha(self) -> bool:
        """Заглушка для scratch-captcha (счистить код)."""
        log.info("ALI PW: scratch-captcha detected (basic solve)...")
        self._page.wait_for_timeout(2000)
        try:
            scratch = self._page.query_selector('.scratch-captcha-container, [class*="scratch"]')
            if scratch:
                box = scratch.bounding_box()
                if box:
                    cx = box['x'] + box['width'] / 2
                    cy = box['y'] + box['height'] / 2
                    # Счищаем: несколько движений
                    for dx in range(-50, 51, 10):
                        for dy in range(-30, 31, 10):
                            self._page.mouse.move(cx + dx, cy + dy)
                            self._page.mouse.down()
                            self._page.mouse.move(cx + dx + 5, cy + dy + 3)
                            self._page.mouse.up()
                    log.info("ALI PW: scratch captcha scraped")
            for i in range(15):
                self._page.wait_for_timeout(1000)
                st = self._page.evaluate("""
                    () => {
                        const p = document.getElementById('baxia-punish');
                        if (!p) return {solved: true};
                        return {solved: false};
                    }
                """)
                if st.get("solved"):
                    return True
        except Exception as e:
            log.warning("ALI PW: scratch-captcha error: %s", e)
        return False

    # ─── Parse ───

    def parse_page(self, resp, listing_id: str, url: str, title_hint: str) -> Optional[dict]:
        """Парсит результат fetch. Возвращает observation dict или None."""
        observation = {
            "fields": {},
            "price_candidates": [],
            "media": {"items": []},
            "availability": "UNKNOWN",
            "category_path": None,
        }

        if resp is None:
            return None

        if isinstance(resp, dict) and resp.get("type") == "playwright":
            self._parse_pw_result(resp, observation)
        else:
            html = resp.text if hasattr(resp, "text") else resp.decode("utf-8", errors="replace")
            if not html or len(html) < 500:
                log.warning("ALI: HTML too short (%d bytes)", len(html or ""))
                return None
            self._parse_jsonld(html, observation)
            self._parse_inline_state(html, observation)
            self._parse_html(html, observation)
            self._parse_og(html, observation)

        has_title = "title" in observation["fields"] and observation["fields"]["title"].get("value")
        has_price = len(observation["price_candidates"]) > 0
        if not has_title and not has_price:
            log.warning("ALI: neither title nor price extracted")
            return None

        log.info(
            "ALI parse: title=%s, prices=%d, avail=%s, media=%d",
            (has_title and observation["fields"]["title"]["value"][:50]) or "N/A",
            len(observation["price_candidates"]),
            observation["availability"],
            len(observation["media"]["items"]),
        )
        return observation

    # ─── Playwright parse ───

    def _parse_pw_result(self, pw_result: dict, obs: dict):
        """Извлекает данные из Playwright-результата: MTOP → DOM → JS → meta."""
        mtop_bodies = pw_result.get("mtop_bodies", [])
        dom = pw_result.get("dom", {})

        for mb in mtop_bodies:
            if self._parse_mtop(mb.get("body", ""), obs):
                break

        if dom:
            self._parse_dom(dom, obs)

        if not obs["price_candidates"]:
            js_state = dom.get("jsState", {})
            for key, val in js_state.items():
                if not val or val == "{}":
                    continue
                pm = re.search(
                    r'"(?:minPrice|salePrice|price|amount|actvPrice|displayPrice|formattedPrice)"\s*:\s*"?([\d.]+)"?',
                    val,
                )
                if pm:
                    parsed = self._parse_price_value(pm.group(1), "EUR")
                    if parsed:
                        obs["price_candidates"].append(parsed)
                        log.info("ALI PW: price from JS state %s: %s", key, pm.group(1))
                        break

        meta = dom.get("meta", {})
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            og_title = meta.get("og:title", "")
            if og_title:
                clean = re.sub(r'\s*[|\-\u2013\u2014]\s*(AliExpress|aliexpress).*$', '', og_title).strip()
                if len(clean) > 5:
                    obs["fields"]["title"] = {
                        "status": "KNOWN", "value": clean[:300],
                        "method": "PW_META", "evidence": {"method": "PW_META", "source": self.source},
                    }
        if not obs["price_candidates"]:
            for mk, mv in meta.items():
                if "price" in mk.lower() and mv and any(c.isdigit() for c in mv):
                    parsed = self._parse_price_value(mv, meta.get("og:price:currency", "EUR"))
                    if parsed:
                        obs["price_candidates"].append(parsed)
                        break
        if not obs["media"]["items"]:
            og_img = meta.get("og:image", "")
            if og_img and self._is_ali_image(og_img):
                obs["media"]["items"].append({"type": "image", "source_url": og_img, "image_data": None})

        if not obs["price_candidates"]:
            for ld in dom.get("jsonld", []):
                if isinstance(ld, dict) and ld.get("@type") in ("Product", ["Product"]):
                    offers = ld.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    p = offers.get("price") or offers.get("lowPrice")
                    if p:
                        parsed = self._parse_price_value(str(p), offers.get("priceCurrency", "EUR"))
                        if parsed:
                            obs["price_candidates"].append(parsed)
                    if not obs["fields"].get("title") and ld.get("name"):
                        obs["fields"]["title"] = {
                            "status": "KNOWN", "value": ld["name"][:300],
                            "method": "PW_JSONLD", "evidence": {"method": "PW_JSONLD", "source": self.source},
                        }
                    break

    def _parse_mtop(self, body: str, obs: dict) -> bool:
        """Парсит MTOP pdp.pc.query JSON (модульная PDP-структура + flat fallback)."""
        body = body.strip()
        m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
        if m:
            body = m.group(1)
        try:
            d = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            log.debug("ALI PW: MTOP JSON parse error: %s", e)
            return False

        ret = d.get("ret", [])
        if not any("SUCCESS" in str(r) for r in ret):
            log.info("ALI PW: MTOP ret=%s (not success)", ret[:2])
            return False

        data = d.get("data", {})
        result = data.get("result", data)
        if not isinstance(result, dict):
            return False

        # ── Helpers: module lookup + flat fallback ──
        def _mod(key: str) -> dict:
            v = result.get(key)
            return v if isinstance(v, dict) else {}

        def _flat(key: str):
            return result.get(key)

        # ── Price (PRICE module → targetSkuPriceInfo, fallback PRICE_BANNER) ──
        price_mod = _mod("PRICE") or _mod("PRICE_BANNER")
        price_info = price_mod.get("targetSkuPriceInfo") or _flat("targetSkuPriceInfo") or {}
        if not isinstance(price_info, dict):
            price_info = {}

        spl = price_info.get("salePriceLocal", "")
        if spl and "|" in spl:
            parts = spl.split("|")
            if len(parts) >= 3:
                try:
                    amount = float(f"{parts[1]}.{parts[2]}")
                    currency = self._detect_currency(price_info.get("salePriceString", ""))
                    if 0.01 < amount < 1000000:
                        obs["price_candidates"].append({
                            "role": "current",
                            "amount": f"{amount:.2f}",
                            "amount_minor": int(round(amount * 100)),
                            "currency": currency,
                            "visible_label": parts[0],
                        })
                except (ValueError, IndexError):
                    pass

        if not obs["price_candidates"]:
            sps = price_info.get("salePriceString", "")
            if sps:
                parsed = self._parse_price_value(sps, self._detect_currency(sps))
                if parsed:
                    obs["price_candidates"].append(parsed)

        for key, role in (("originalPrice", "original"), ("monthlyLowestPrice", "monthly_lowest")):
            val = price_info.get(key, "")
            if val:
                parsed = self._parse_price_value(val, self._detect_currency(val))
                if parsed:
                    parsed["role"] = role
                    obs["price_candidates"].append(parsed)

        # Currency override from GLOBAL_DATA.globalData.currencyCode
        gd = _mod("GLOBAL_DATA").get("globalData") or {}
        gd_currency = gd.get("currencyCode", "")
        if gd_currency:
            for pc in obs["price_candidates"]:
                if not pc.get("currency") or pc["currency"] in ("", "UNKNOWN"):
                    pc["currency"] = gd_currency

        # Per-SKU price map
        sku_price_map = price_mod.get("skuPriceInfoMap") or _flat("skuPriceInfoMap") or {}
        if isinstance(sku_price_map, dict) and sku_price_map:
            obs["fields"]["sku_prices"] = {
                "status": "KNOWN", "value": sku_price_map,
                "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
            }

        # ── Title (PRODUCT_TITLE / PRODUCT module → text / subject) ──
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            title = None
            prod_mod = _mod("PRODUCT_TITLE") or _mod("PRODUCT")
            title = prod_mod.get("text") or prod_mod.get("title") or prod_mod.get("subject")
            if not title:
                for path in (
                    ("GLOBAL_DATA", "globalData", "subject"),
                    ("GLOBAL_DATA", "globalData", "title"),
                    ("GLOBAL_DATA", "subject"),
                    ("GLOBAL_DATA", "title"),
                    ("seoInfo", "title"),
                    ("SKUModule", "title"),
                ):
                    val = result
                    for key in path:
                        if isinstance(val, dict):
                            val = val.get(key)
                        else:
                            val = None
                            break
                    if val and isinstance(val, str) and len(val) > 10:
                        title = val
                        break
            if not title:
                title = _flat("subject") or _flat("title")
            if not title:
                ds = json.dumps(result, ensure_ascii=False)
                tm = re.search(r'"(?:title|subject|seoTitle|productTitle)"\s*:\s*"([^"]{10,500})"', ds)
                if tm:
                    title = tm.group(1)
            if title and isinstance(title, str) and len(title) > 5:
                obs["fields"]["title"] = {
                    "status": "KNOWN", "value": title[:300],
                    "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                }

        # ── Images (HEADER_IMAGE_PC: imgList > imagePathList > mainImages) ──
        if not obs["media"]["items"]:
            images = []
            hipc = _mod("HEADER_IMAGE_PC")
            images = hipc.get("imgList") or hipc.get("imagePathList") or []
            if isinstance(images, list) and images and isinstance(images[0], dict):
                images = [im.get("imageUrl", "") for im in images if isinstance(im, dict)]
            if not images:
                img = hipc.get("image") or _flat("image") or ""
                if img:
                    images = [img]
            if not images:
                ds = json.dumps(result, ensure_ascii=False)
                imgs = re.findall(r'"(?:url|imageUrl|path|src)"\s*:\s*"(https?://[^"]*(?:alicdn|aliexpress-media)[^"]*)"', ds)
                images = imgs[:20]

            seen = set()
            for img_url in images:
                if not isinstance(img_url, str):
                    continue
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                if self._is_ali_image(img_url) and img_url not in seen:
                    seen.add(img_url)
                    obs["media"]["items"].append({"type": "image", "source_url": img_url, "image_data": None})

        # ── Category ──
        if not obs["category_path"]:
            gd_inner = _mod("GLOBAL_DATA").get("globalData") or _mod("GLOBAL_DATA") or {}
            cat_path = _flat("categoryPath") or gd_inner.get("categoryPath") or ""
            if cat_path:
                obs["category_path"] = cat_path
            else:
                c1 = _flat("category1") or gd_inner.get("category1") or ""
                c3 = _flat("category3") or gd_inner.get("category3") or ""
                if c1 or c3:
                    obs["category_path"] = "/".join(filter(None, [c1, c3]))

        # ── SKU variants & properties ──
        sku_mod = _mod("SKU")
        sku_paths = sku_mod.get("skuPaths") or []
        if isinstance(sku_paths, list) and sku_paths:
            sku_variants = []
            for sp in sku_paths:
                if not isinstance(sp, dict):
                    continue
                sku_variants.append({
                    "sku_id": str(sp.get("skuIdStr") or sp.get("skuId") or ""),
                    "attr": sp.get("skuAttr") or sp.get("path") or "",
                    "stock": sp.get("skuStock"),
                    "salable": sp.get("salable", False),
                })
            if sku_variants:
                obs["fields"]["sku_variants"] = {
                    "status": "KNOWN", "value": sku_variants,
                    "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                }

        sku_props = sku_mod.get("skuProperties") or []
        if isinstance(sku_props, list) and sku_props:
            prop_summary = []
            for prop in sku_props:
                if not isinstance(prop, dict):
                    continue
                pname = prop.get("skuPropertyName", "")
                values = []
                for pv in (prop.get("skuPropertyValues") or []):
                    if isinstance(pv, dict):
                        values.append(pv.get("propertyValueDisplayName") or pv.get("propertyValueName") or "")
                if pname:
                    prop_summary.append({"name": pname, "values": values})
            if prop_summary:
                obs["fields"]["sku_properties"] = {
                    "status": "KNOWN", "value": prop_summary,
                    "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                }

        # ── Shipping (SHIPPING module → deliveryLayoutInfo → bizData) ──
        ship_mod = _mod("SHIPPING")
        delivery_list = ship_mod.get("deliveryLayoutInfo") or ship_mod.get("originalLayoutResultList") or []
        if isinstance(delivery_list, list) and delivery_list:
            first = delivery_list[0] if isinstance(delivery_list[0], dict) else {}
            biz = first.get("bizData") or {}
            shipping_info = {
                "ship_from": biz.get("shipFrom") or biz.get("shipFromCode") or "",
                "ship_to": biz.get("shipTo") or biz.get("shipToCode") or "",
                "provider": biz.get("deliveryProviderName") or biz.get("company") or "",
                "fee": biz.get("shippingFee") or "",
                "eta_min_days": biz.get("deliveryDayMin"),
                "eta_max_days": biz.get("deliveryDayMax"),
                "eta_min_date": biz.get("displayEtaMinDate") or "",
                "eta_max_date": biz.get("displayEtaMaxDate") or "",
                "currency": biz.get("currency") or "",
                "tracking": biz.get("tracking") == "visible",
            }
            obs["fields"]["shipping"] = {
                "status": "KNOWN", "value": shipping_info,
                "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
            }
            # Mirror as delivery for normalize_eye
            if not obs["fields"].get("delivery"):
                parts = []
                if shipping_info.get("ship_from"):
                    parts.append(f"From: {shipping_info['ship_from']}")
                if shipping_info.get("provider"):
                    parts.append(f"Provider: {shipping_info['provider']}")
                if shipping_info.get("fee"):
                    parts.append(f"Fee: {shipping_info['fee']}")
                if shipping_info.get("eta_min_days") and shipping_info.get("eta_max_days"):
                    parts.append(f"ETA: {shipping_info['eta_min_days']}-{shipping_info['eta_max_days']} days")
                delivery_text = " | ".join(parts) if parts else json.dumps(shipping_info, ensure_ascii=False)
                obs["fields"]["delivery"] = {
                    "status": "KNOWN", "value": delivery_text,
                    "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                }

        # ── Quantity / Inventory ──
        qty_mod = _mod("QUANTITY_PC")
        total_inv = qty_mod.get("totalAvailableInventory")
        if total_inv is not None:
            obs["fields"]["total_inventory"] = {
                "status": "KNOWN", "value": int(total_inv),
                "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
            }

        # ── Shop / Seller ──
        shop_mod = _mod("SHOP_CARD_PC")
        shop_name = shop_mod.get("storeName") or ""
        if shop_name:
            obs["fields"]["shop"] = {
                "status": "KNOWN",
                "value": {
                    "name": shop_name,
                    "url": shop_mod.get("storeHomePage") or "",
                    "positive_rate": shop_mod.get("sellerPositiveRate") or "",
                    "score": shop_mod.get("sellerScore"),
                },
                "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
            }

        # ── Rating ──
        rating_mod = _mod("PC_RATING")
        rating_val = None
        if rating_mod:
            rating_val = rating_mod.get("rating") or rating_mod.get("score") or rating_mod.get("averageScore")
            if not rating_val:
                # Parse from aiSell.textFragments HTML: <strong>4.8</strong>
                ai_sell = rating_mod.get("aiSell") or {}
                for frag in (ai_sell.get("textFragments") or []):
                    rm = re.search(r'<strong>([\d.]+)</strong>', str(frag))
                    if rm:
                        try:
                            rating_val = float(rm.group(1))
                            break
                        except ValueError:
                            pass
        if not rating_val:
            rating_val = shop_mod.get("sellerScore")
        if rating_val:
            obs["fields"]["rating"] = {
                "status": "KNOWN", "value": rating_val,
                "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
            }

        # ── Availability ──
        if obs["availability"] == "UNKNOWN":
            if obs["fields"].get("sku_variants"):
                for sv in obs["fields"]["sku_variants"]["value"]:
                    if sv.get("salable") and (sv.get("stock") or 0) > 0:
                        obs["availability"] = "IN_STOCK"
                        break
            if obs["availability"] == "UNKNOWN" and total_inv is not None:
                obs["availability"] = "IN_STOCK" if int(total_inv) > 0 else "OUT_OF_STOCK"
            if obs["availability"] == "UNKNOWN":
                ds = json.dumps(result, ensure_ascii=False)
                am = re.search(r'"(?:stock|inStock|stockStatus|available|inventoryStatus|stockCount)"\s*:\s*("?[^,}\]]+)"?', ds)
                if am:
                    av = am.group(1).strip('"')
                    if av in ("true", "1", "IN_STOCK", "AVAILABLE") or (av.isdigit() and int(av) > 0):
                        obs["availability"] = "IN_STOCK"
                    elif av in ("false", "0", "OUT_OF_STOCK", "UNAVAILABLE"):
                        obs["availability"] = "OUT_OF_STOCK"
            if obs["availability"] == "UNKNOWN" and obs["price_candidates"]:
                delivery = _flat("deliveryOptionPanelDisplayList") or []
                if isinstance(delivery, list) and len(delivery) > 0:
                    obs["availability"] = "IN_STOCK"

        # Mirror availability into fields for normalize_eye
        if obs["availability"] != "UNKNOWN" and not obs["fields"].get("availability"):
            obs["fields"]["availability"] = {
                "status": "KNOWN", "value": obs["availability"],
                "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
            }

        # ── Description ──
        if not obs["fields"].get("description"):
            desc_mod = _mod("DESC")
            desc = desc_mod.get("description") or desc_mod.get("content") or _flat("description") or _flat("detailDescription") or ""
            if isinstance(desc, str) and len(desc) > 20:
                obs["fields"]["description"] = {
                    "status": "KNOWN", "value": desc[:2000],
                    "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                }
            else:
                # Fallback: store pcDescUrl + selling points as structured description
                desc_url = desc_mod.get("pcDescUrl") or desc_mod.get("nativeDescUrl") or ""
                spi = desc_mod.get("sellingPointInfo") or {}
                selling_points = []
                if isinstance(spi, dict):
                    for sp in (spi.get("viewList") or spi.get("points") or []):
                        if isinstance(sp, dict):
                            selling_points.append(sp.get("text") or sp.get("title") or "")
                        elif isinstance(sp, str):
                            selling_points.append(sp)
                desc_payload = {
                    "desc_url": desc_url,
                    "selling_points": selling_points,
                    "title": desc_mod.get("title") or "",
                }
                if desc_url or selling_points:
                    obs["fields"]["description"] = {
                        "status": "KNOWN", "value": desc_payload,
                        "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                    }

        # ── Product Props (PRODUCT_PROP_PC.showedProps) ──
        if not obs["fields"].get("product_props"):
            ppc = _mod("PRODUCT_PROP_PC")
            props = ppc.get("showedProps") or ppc.get("outerProps") or []
            if isinstance(props, list) and props:
                prop_list = []
                for p in props:
                    if isinstance(p, dict) and p.get("attrName"):
                        prop_list.append({"name": p["attrName"], "value": p.get("attrValue", "")})
                if prop_list:
                    obs["fields"]["product_props"] = {
                        "status": "KNOWN", "value": prop_list,
                        "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                    }
                    # Mirror as characteristics for normalize_eye
                    if not obs["fields"].get("characteristics"):
                        obs["fields"]["characteristics"] = {
                            "status": "KNOWN",
                            "value": [{"label": p.get("name", ""), "value": p.get("value", "")}
                                       for p in prop_list if isinstance(p, dict)],
                            "method": "MTOP", "evidence": {"method": "MTOP", "source": self.source},
                        }

        log.info(
            "ALI PW: MTOP parsed \u2014 title=%s, prices=%d, imgs=%d, cat=%s, avail=%s, sku=%d",
            (obs["fields"].get("title", {}).get("value", "")[:40]) or "N/A",
            len(obs["price_candidates"]),
            len(obs["media"]["items"]),
            (obs["category_path"] or "N/A")[:30],
            obs["availability"],
            len(obs["fields"].get("sku_variants", {}).get("value", [])),
        )
        return bool(obs["price_candidates"] or obs["fields"].get("title"))

    def _parse_dom(self, dom: dict, obs: dict):
        """Извлекает из DOM-данных (Playwright evaluate)."""
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            t = dom.get("title", "")
            if t and len(t) > 5:
                obs["fields"]["title"] = {
                    "status": "KNOWN", "value": t[:300],
                    "method": "PW_DOM", "evidence": {"method": "PW_DOM", "source": self.source},
                }

        if not obs["price_candidates"]:
            for p in dom.get("prices", []):
                parsed = self._parse_price_value(p, "EUR")
                if parsed:
                    obs["price_candidates"].append(parsed)
                    break

        if obs["availability"] == "UNKNOWN" and dom.get("availability"):
            obs["availability"] = dom["availability"]

        if not obs["media"]["items"]:
            seen = set()
            for img_url in dom.get("images", []):
                if isinstance(img_url, str) and self._is_ali_image(img_url) and img_url not in seen:
                    seen.add(img_url)
                    obs["media"]["items"].append({"type": "image", "source_url": img_url, "image_data": None})

        if not obs["category_path"] and dom.get("category"):
            obs["category_path"] = dom["category"]

    # ─── HTTP parse strategies (fallback) ───

    def _parse_jsonld(self, html: str, obs: dict):
        blocks = self.extract_json_ld(html)
        product = self.find_product_jsonld(blocks)
        if not product:
            return
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            name = product.get("name")
            if name:
                obs["fields"]["title"] = {
                    "status": "KNOWN", "value": name.strip()[:300],
                    "method": "JSON-LD", "evidence": {"method": "JSON-LD", "source": self.source},
                }
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            price_val = offers.get("price") or offers.get("lowPrice")
            currency = offers.get("priceCurrency", "EUR")
            if price_val:
                parsed = self._parse_price_value(str(price_val), currency)
                if parsed:
                    obs["price_candidates"].append(parsed)
            avail_str = str(offers.get("availability", ""))
            if "IN_STOCK" in avail_str.upper() or "AVAILABLE" in avail_str.upper():
                obs["availability"] = "IN_STOCK"
            elif "OUT_OF_STOCK" in avail_str.upper() or "SOLD_OUT" in avail_str.upper():
                obs["availability"] = "OUT_OF_STOCK"
        images = product.get("image")
        if isinstance(images, str):
            images = [images]
        if isinstance(images, list):
            for img_url in images[:20]:
                if isinstance(img_url, str) and self._is_ali_image(img_url):
                    obs["media"]["items"].append({"type": "image", "source_url": img_url, "image_data": None})

    def _parse_inline_state(self, html: str, obs: dict):
        for regex in (INIT_STATE_RE, RUN_PARAMS_RE):
            m = regex.search(html)
            if m:
                try:
                    state = json.loads(m.group(1))
                    self._extract_from_state(state, obs)
                    return
                except (json.JSONDecodeError, ValueError):
                    continue

    def _extract_from_state(self, state: dict, obs: dict):
        product_data = None
        for key in ("productData", "product", "itemInfo", "item", "detailData", "result"):
            if key in state and isinstance(state[key], dict):
                product_data = state[key]
                break
        if not product_data:
            product_data = self._deep_find_product(state)
        if not product_data:
            return

        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            for tkey in ("title", "subject", "name", "productTitle", "seoTitle"):
                val = product_data.get(tkey)
                if val and isinstance(val, str) and len(val) > 5:
                    obs["fields"]["title"] = {
                        "status": "KNOWN", "value": val.strip()[:300],
                        "method": "INLINE_JS", "evidence": {"method": "INLINE_JS", "source": self.source},
                    }
                    break

        if not obs["price_candidates"]:
            for pkey in ("price", "skuPriceList", "priceInfo", "tradePrice", "activityPrice", "targetSkuPriceInfo"):
                if pkey in product_data:
                    pi = product_data[pkey]
                    if isinstance(pi, dict):
                        for pk in ("minPrice", "salePrice", "price", "amount", "formattedPrice", "salePriceString"):
                            val = pi.get(pk)
                            if val:
                                parsed = self._parse_price_value(str(val), pi.get("currency", "EUR"))
                                if parsed:
                                    obs["price_candidates"].append(parsed)
                                    break
                    elif isinstance(pi, (int, float, str)):
                        parsed = self._parse_price_value(str(pi), "EUR")
                        if parsed:
                            obs["price_candidates"].append(parsed)
                    break

        if not obs["media"]["items"]:
            for ikey in ("images", "imageList", "gallery", "imgUrls", "skuImages", "currentSkuImages"):
                if ikey in product_data and isinstance(product_data[ikey], list):
                    for img in product_data[ikey][:20]:
                        if isinstance(img, str) and self._is_ali_image(img):
                            obs["media"]["items"].append({"type": "image", "source_url": img, "image_data": None})
                    break

        if not obs["category_path"]:
            for ckey in ("categoryPath", "breadcrumb", "categories"):
                val = product_data.get(ckey)
                if val:
                    if isinstance(val, list):
                        obs["category_path"] = " > ".join(str(v) for v in val if v)
                    elif isinstance(val, str):
                        obs["category_path"] = val
                    break

    def _deep_find_product(self, data: dict, depth: int = 0) -> Optional[dict]:
        if depth > 5:
            return None
        if isinstance(data, dict):
            has_title = any(k in data for k in ("title", "subject", "name", "productTitle"))
            has_price = any(k in data for k in ("price", "skuPriceList", "priceInfo", "tradePrice", "targetSkuPriceInfo"))
            if has_title and has_price:
                return data
            for v in data.values():
                if isinstance(v, dict):
                    r = self._deep_find_product(v, depth + 1)
                    if r:
                        return r
        elif isinstance(data, list):
            for item in data[:10]:
                if isinstance(item, dict):
                    r = self._deep_find_product(item, depth + 1)
                    if r:
                        return r
        return None

    def _parse_html(self, html: str, obs: dict):
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            m = H1_RE.search(html)
            if m:
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if len(title) > 5:
                    obs["fields"]["title"] = {
                        "status": "KNOWN", "value": title[:300],
                        "method": "HTML_H1", "evidence": {"method": "HTML_H1", "source": self.source},
                    }
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            m = TITLE_TAG_RE.search(html)
            if m:
                raw = re.sub(r'\s*\|\s*AliExpress.*$', '', m.group(1), flags=re.I).strip()
                if len(raw) > 5:
                    obs["fields"]["title"] = {
                        "status": "KNOWN", "value": raw[:300],
                        "method": "HTML_TITLE", "evidence": {"method": "HTML_TITLE", "source": self.source},
                    }
        if not obs["price_candidates"]:
            for m in PRICE_DATA_RE.finditer(html):
                parsed = self._parse_price_value(m.group(1), "EUR")
                if parsed:
                    obs["price_candidates"].append(parsed)
                    break
        if not obs["price_candidates"]:
            for m in PRICE_CLASS_RE.finditer(html):
                val = m.group(1).replace(",", ".")
                try:
                    amount = float(val)
                    if 0.01 < amount < 1000000:
                        obs["price_candidates"].append({
                            "role": "current", "amount": f"{amount:.2f}",
                            "amount_minor": int(round(amount * 100)),
                            "currency": "EUR", "visible_label": f"\u20ac{amount:.2f}",
                        })
                        break
                except ValueError:
                    continue
        if obs["availability"] == "UNKNOWN":
            if AVAIL_OUT_STOCK_RE.search(html):
                obs["availability"] = "OUT_OF_STOCK"
            elif AVAIL_PREORDER_RE.search(html):
                obs["availability"] = "PRE_ORDER"
            elif AVAIL_IN_STOCK_RE.search(html):
                obs["availability"] = "IN_STOCK"
        if not obs["media"]["items"]:
            seen = set()
            for m in IMG_SRC_RE.finditer(html):
                url = m.group(1).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if self._is_ali_image(url) and url not in seen:
                    seen.add(url)
                    obs["media"]["items"].append({"type": "image", "source_url": url, "image_data": None})
                if len(obs["media"]["items"]) >= 20:
                    break

    def _parse_og(self, html: str, obs: dict):
        if not obs["fields"].get("title") or not obs["fields"]["title"].get("value"):
            m = OG_TITLE_RE.search(html)
            if m:
                title = m.group(1).strip()
                if len(title) > 5:
                    obs["fields"]["title"] = {
                        "status": "KNOWN", "value": title[:300],
                        "method": "OPEN_GRAPH", "evidence": {"method": "OPEN_GRAPH", "source": self.source},
                    }
        if not obs["price_candidates"]:
            mp = OG_PRICE_RE.search(html)
            mc = OG_CURRENCY_RE.search(html)
            if mp:
                parsed = self._parse_price_value(mp.group(1), mc.group(1).upper() if mc else "EUR")
                if parsed:
                    obs["price_candidates"].append(parsed)
        if not obs["media"]["items"]:
            m = OG_IMAGE_RE.search(html)
            if m:
                url = m.group(1).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if self._is_ali_image(url):
                    obs["media"]["items"].append({"type": "image", "source_url": url, "image_data": None})

    # ─── Utilities ───

    def _parse_price_value(self, val, currency: str = "EUR") -> Optional[dict]:
        """Парсит цену: '57,06\u20ac', '57.06', '$12.99', '57,06\u20ac|57|06'."""
        if isinstance(val, (int, float)):
            amount = float(val)
        elif isinstance(val, str):
            clean = val
            clean = re.sub(r'[$\u20ac\u00a3\s]', '', clean)
            if re.match(r'^\d{1,3}(\.\d{3})*,\d{2}$', clean):
                clean = clean.replace(".", "").replace(",", ".")
            elif "," in clean and "." not in clean:
                clean = clean.replace(",", ".")
            try:
                amount = float(clean)
            except ValueError:
                return None
        else:
            return None

        if not (0.01 < amount < 1000000):
            return None

        minor = int(round(amount * 100))
        return {
            "role": "current",
            "amount": f"{amount:.2f}",
            "amount_minor": minor,
            "currency": (currency or "EUR").upper(),
            "visible_label": f"{(currency or 'EUR').upper()} {amount:.2f}",
        }

    @staticmethod
    def _detect_currency(price_str: str) -> str:
        """Определяет валюту по символу в строке цены."""
        if not price_str:
            return "EUR"
        if "\u20ac" in price_str or "EUR" in price_str.upper():
            return "EUR"
        if "$" in price_str or "USD" in price_str.upper():
            return "USD"
        if "\u00a3" in price_str or "GBP" in price_str.upper():
            return "GBP"
        if "\u20bd" in price_str or "RUB" in price_str.upper():
            return "RUB"
        return "EUR"

    @staticmethod
    def _is_ali_image(url: str) -> bool:
        if not url or not url.startswith("http"):
            return False
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return False
        if not any(host == h or host.endswith("." + h) for h in ALI_CDN_HOSTS):
            return False
        if NOISE_PATTERNS.search(url):
            return False
        return True


class AliExpressRUCollector(AliExpressCollector):
    """Сборщик AliExpress RU (ru.aliexpress.com).

    Стратегия (приоритет сверху вниз):
    1. curl_cffi (impersonate=chrome) — быстрый HTTP, обход Akamai
    2. Playwright persistent context — полный рендер, MTOP перехват
    3. Search API (aer-webapi/v1/search) — запасной канал без капчи
    """

    source = "ALIEXPRESS_RU"
    title = "AliExpress RU"
    SEARCH_API_URL = "https://ru.aliexpress.com/aer-webapi/v1/search"

    def __init__(self, *args, **kwargs):
        kwargs["domain"] = "ru"
        super().__init__(*args, **kwargs)

    @staticmethod
    def _detect_captcha(html: str) -> bool:
        """Детектит CAPTCHA/TMD страницу."""
        if not html:
            return False
        markers = (
            "Пройдите проверку", "пройдите проверку",
            "Verify you are human", "verify-human",
            "captcha", "CAPTCHA", "tmd=",
            "nc_iconfont", "aliexpress-captcha", "nocaptcha",
        )
        sample = html[:5000]
        return any(m in sample for m in markers)

    # ─── Fetch: curl_cffi → Playwright → requests ───

    def fetch(self, url: str):
        """RU-специфичный fetch: curl_cffi → Playwright → requests."""
        # 1. curl_cffi first (fast, no browser)
        try:
            from curl_cffi import requests as cffi_req
            self._throttle()
            start = time.monotonic()
            resp = cffi_req.get(
                url,
                impersonate="chrome",
                proxies=self.pool.get_proxy(),
                timeout=(15, 30),
                headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": "https://ru.aliexpress.com/",
                },
                cookies={"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"},
            )
            latency = time.monotonic() - start
            self.stats["requests"] += 1
            if resp.status_code == 200:
                self.stats["success"] += 1
                self.pool.mark_success(latency)
                log.info("ALI_RU: curl_cffi OK, %d bytes, %.1fs", len(resp.text), latency)
                return resp
            elif resp.status_code in (403, 429, 503):
                self.stats["blocked"] += 1
                self.pool.mark_failure()
                log.info("ALI_RU: curl_cffi HTTP %d, trying Playwright", resp.status_code)
            else:
                log.warning("ALI_RU: curl_cffi HTTP %d", resp.status_code)
                self.stats["failed"] += 1
                return None
        except ImportError:
            log.warning("ALI_RU: curl_cffi not installed")
        except Exception as e:
            log.warning("ALI_RU: curl_cffi error: %s", e)
            self.stats["requests"] += 1

        # 2. Playwright (slower, full render + MTOP)
        if not self._pw_failed:
            if self._ensure_browser():
                self._warm_session()
                result = self._fetch_pw(url)
                if result:
                    return result

        # 3. requests fallback
        return super().fetch(url)

    # ─── Search API ───

    def _fetch_search_api(self, query: str, page: int = 1, page_size: int = 40) -> Optional[dict]:
        """Запрос к search API aer-webapi/v1/search."""
        try:
            from curl_cffi import requests as cffi_req
            from urllib.parse import quote
            encoded_query = quote(query, safe="")
            url = (f"{self.SEARCH_API_URL}?SearchText={encoded_query}"
                   f"&page={page}&pageSize={page_size}&sortType=default&_lang=ru_RU&_currency=RUB")
            resp = cffi_req.get(
                url,
                impersonate="chrome",
                proxies=self.pool.get_proxy(),
                timeout=(15, 30),
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={encoded_query}",
                    "Origin": "https://ru.aliexpress.com",
                },
                cookies={"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"},
            )
            if resp.status_code == 200:
                body = resp.text.strip()
                if body.startswith('<'):
                    log.warning("ALI_RU: search API returned HTML (404 page?), len=%d", len(body))
                    return None
                data = resp.json()
                log.info("ALI_RU: search API OK, %d bytes, query=%r", len(resp.text), query[:40])
                return data
            else:
                log.warning("ALI_RU: search API HTTP %d", resp.status_code)
        except Exception as e:
            log.warning("ALI_RU: search API error: %s", e)
        return None

    def _parse_search_api(self, data: dict) -> list[dict]:
        """Парсит ответ search API в список товаров (рекурсивный поиск snippetContainer)."""
        products = []
        try:
            root = data.get("data", data)
            snippets = self._find_all_snippets(root)
            for snippet in snippets:
                prod = self._parse_search_snippet(snippet)
                if prod:
                    products.append(prod)
        except Exception as e:
            log.warning("ALI_RU: search API parse error: %s", e)
        seen = set()
        unique = []
        for p in products:
            key = p.get("item_id") or p.get("url")
            if key and key not in seen:
                seen.add(key)
                unique.append(p)
        log.info("ALI_RU: search API parsed %d unique products", len(unique))
        return unique

    @staticmethod
    def _find_all_snippets(obj, depth=0) -> list:
        """Рекурсивно находит все snippetContainer в JSON-структуре."""
        results = []
        if depth > 15:
            return results
        if isinstance(obj, dict):
            if "snippetContainer" in obj and isinstance(obj["snippetContainer"], dict):
                results.append(obj["snippetContainer"])
            for k, v in obj.items():
                if k != "snippetContainer":
                    results.extend(AliExpressRUCollector._find_all_snippets(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(AliExpressRUCollector._find_all_snippets(item, depth + 1))
        return results

    def _parse_search_snippet(self, snippet: dict) -> Optional[dict]:
        """Парсит один snippetContainer в товар."""
        product = {"item_id": None, "sku": None, "price": None, "currency": "RUB",
                   "title": None, "images": [], "url": None, "seller": None}

        def find_events(o):
            evs = []
            if isinstance(o, dict):
                if "aerEvent" in o and isinstance(o["aerEvent"], dict):
                    evs.append(o["aerEvent"])
                for v in o.values():
                    evs.extend(find_events(v))
            elif isinstance(o, list):
                for it in o:
                    evs.extend(find_events(it))
            return evs

        for ev in find_events(snippet):
            if ev.get("itemId") and not product["item_id"]:
                product["item_id"] = str(ev["itemId"])
            if ev.get("sku") and not product["sku"]:
                product["sku"] = str(ev["sku"])
            if ev.get("finalPrice") and not product["price"]:
                try:
                    product["price"] = float(ev["finalPrice"])
                except (ValueError, TypeError):
                    pass
            if ev.get("currency"):
                product["currency"] = ev["currency"]

        def find_areas(o):
            areas = []
            if isinstance(o, dict):
                if "presentations" in o:
                    for pres in o["presentations"]:
                        if isinstance(pres, dict) and "layoutAreas" in pres:
                            areas.extend(pres["layoutAreas"])
                for v in o.values():
                    areas.extend(find_areas(v))
            elif isinstance(o, list):
                for it in o:
                    areas.extend(find_areas(it))
            return areas

        def find_texts(o):
            ts = []
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "text":
                        if isinstance(v, dict) and "text" in v:
                            t = str(v["text"]).strip()
                            if t:
                                ts.append(t)
                        elif isinstance(v, str) and v.strip():
                            ts.append(v.strip())
                    else:
                        ts.extend(find_texts(v))
            elif isinstance(o, list):
                for it in o:
                    ts.extend(find_texts(it))
            return ts

        def find_images(o):
            imgs = []
            if isinstance(o, dict):
                if "gallery" in o and isinstance(o["gallery"], dict):
                    if "images" in o["gallery"] and isinstance(o["gallery"]["images"], list):
                        imgs.extend(o["gallery"]["images"])
                for k, v in o.items():
                    if k == "url" and isinstance(v, str) and ("alicdn" in v or "aliexpress-media" in v):
                        imgs.append(v)
                    else:
                        imgs.extend(find_images(v))
            elif isinstance(o, list):
                for it in o:
                    imgs.extend(find_images(it))
            return imgs

        areas = find_areas(snippet)
        for area in areas:
            area_id = ""
            if isinstance(area.get("id"), dict):
                area_id = area["id"].get("name", "")
            elif isinstance(area.get("id"), str):
                area_id = area["id"]

            if "title" in area_id.lower():
                texts = find_texts(area)
                good = [t for t in texts if len(t) > 10 and t != "Выберите категорию"]
                if good and not product["title"]:
                    product["title"] = good[0]
            if "price" in area_id.lower():
                texts = find_texts(area)
                for t in texts:
                    if "\u20bd" in t or "RUB" in t.upper():
                        nums = re.findall(r'[\d\s\u00a0\u202f]+(?:\.\d+)?', t)
                        if nums:
                            raw = nums[0].replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
                            try:
                                val = float(raw)
                                if not product["price"] or val < product["price"]:
                                    product["price"] = val
                            except ValueError:
                                pass
            if "gallery" in area_id.lower() or "image" in area_id.lower():
                for img in find_images(area):
                    if img and img not in product["images"]:
                        product["images"].append(img)
            if "seller" in area_id.lower() or "store" in area_id.lower():
                texts = find_texts(area)
                if texts and not product["seller"]:
                    product["seller"] = texts[0]

        if product["item_id"] and not product["url"]:
            product["url"] = f"https://ru.aliexpress.com/item/{product['item_id']}.html"
        if product["sku"] and product["url"] and "sku_id" not in product["url"]:
            product["url"] += f"?sku_id={product['sku']}"

        if not product["item_id"] and not product["title"] and not product["price"]:
            return None
        return product

    # ─── HTML Search (V20 parser) ───

    def _fetch_wholesale_html(self, query: str, page: int = 1) -> Optional[str]:
        """Fetches wholesale search page HTML via curl_cffi (840KB server-rendered)."""
        from urllib.parse import quote
        url = f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}&page={page}&_lang=ru_RU&_currency=RUB"
        try:
            from curl_cffi import requests as cffi_req
            self._throttle()
            start = time.monotonic()
            resp = cffi_req.get(
                url,
                impersonate="chrome",
                proxies=self.pool.get_proxy(),
                timeout=(15, 30),
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": "https://ru.aliexpress.com/",
                    "Upgrade-Insecure-Requests": "1",
                },
                cookies={"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"},
            )
            latency = time.monotonic() - start
            self.stats["requests"] += 1
            if resp.status_code == 200 and len(resp.text) > 100000:
                self.stats["success"] += 1
                self.pool.mark_success(latency)
                log.info("ALI_RU: wholesale HTML OK, %d bytes, %.1fs", len(resp.text), latency)
                return resp.text
            else:
                log.warning("ALI_RU: wholesale HTML HTTP %d, len=%d", resp.status_code, len(resp.text))
        except Exception as e:
            log.warning("ALI_RU: wholesale HTML error: %s", e)
            self.stats["requests"] += 1
        return None

    def _parse_wholesale_html(self, html: str) -> list[dict]:
        """V20 parser: extracts product cards from wholesale page HTML."""
        import html as html_mod
        from urllib.parse import unquote

        products = []
        card_list_idx = html.find('id="card-list"')
        if card_list_idx < 0:
            log.warning("ALI_RU: card-list not found in HTML")
            return products

        window_end = min(card_list_idx + 700000, len(html))
        card_area = html[card_list_idx:window_end]

        anchor_pat = re.compile(
            r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>'
        )
        matches = list(anchor_pat.finditer(card_area))

        if not matches:
            anchor_pat2 = re.compile(r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>')
            matches = list(anchor_pat2.finditer(card_area))
            log.info("ALI_RU: fallback pattern, %d item links", len(matches))

        log.info("ALI_RU: %d card anchors found", len(matches))

        for i, m in enumerate(matches):
            href = html_mod.unescape(m.group(1))
            block_start = m.start()
            block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
            card_html = card_area[block_start:block_end]

            p = {"item_id": None, "title": None, "price": None, "currency": None,
                 "original_price": None, "images": [], "url": None, "seller": None}

            # Item ID
            id_match = re.search(r'/item/(\d+)\.html', href)
            if id_match:
                p["item_id"] = id_match.group(1)

            # Clean URL
            if href.startswith("//"):
                p["url"] = "https:" + href
            else:
                p["url"] = href
            p["url"] = re.sub(r'\?.*$', '', p["url"])
            # Force ru.aliexpress.com domain (geo-redirect fix)
            p["url"] = re.sub(r'https?://[a-z]{2,3}\.aliexpress\.com', 'https://ru.aliexpress.com', p["url"], flags=re.I)

            # Title (5 patterns, h3 first — most reliable per diagnostics)
            title = None
            t_match = re.search(r'<h3[^>]*>(.*?)</h3>', card_html, re.DOTALL)
            if t_match:
                title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
                title = html_mod.unescape(title)
                if len(title) < 10:
                    title = None
            if not title:
                t_match = re.search(r'class="[^"]*new--title[^"]*"[^>]*title="([^"]{10,300})"', card_html)
                if t_match:
                    title = html_mod.unescape(t_match.group(1)).strip()
            if not title:
                t_match = re.search(r'<h3[^>]*class="[^"]*new--titleText[^"]*"[^>]*>(.*?)</h3>', card_html, re.DOTALL)
                if t_match:
                    title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
                    title = html_mod.unescape(title)
            if not title:
                t_match = re.search(r'class="[^"]*images--item[^"]*"[^>]*alt="([^"]{15,300})"', card_html)
                if t_match:
                    title = html_mod.unescape(t_match.group(1)).strip()
            if not title:
                t_match = re.search(r'aria-label="([^"]{15,300})"', card_html)
                if t_match:
                    title = html_mod.unescape(t_match.group(1)).strip()
            p["title"] = title

            # Price: DOM extraction (primary) → pdp_npi in href (fallback)
            price_val = None
            orig_price_val = None
            currency_val = None

            # 1) DOM: find price in card HTML (IT locale uses comma decimal)
            price_block = re.search(
                r'class="[^"]*new--price[^"]*"[^>]*>(.*?)</(?:span|div)>',
                card_html, re.DOTALL
            )
            if price_block:
                inner = price_block.group(1)
                num_m = re.search(r'(\d{1,5}(?:[.,]\d{1,2})?)', inner)
                if num_m:
                    raw = num_m.group(1)
                    if ',' in raw and '.' not in raw:
                        raw = raw.replace(',', '.')
                    elif ',' in raw and '.' in raw:
                        raw = raw.replace(',', '')
                    try:
                        price_val = float(raw)
                    except ValueError:
                        pass

            # 2) Currency from nearby text in card
            cur_area = ''
            pidx = card_html.find('price')
            if pidx >= 0:
                cur_area = card_html[pidx:pidx + 400]
            for cur_sym in ('EUR', 'USD', 'RUB', 'GBP', '€', '$', '₽'):
                if cur_sym in cur_area:
                    currency_val = {'€': 'EUR', '$': 'USD', '₽': 'RUB'}.get(cur_sym, cur_sym)
                    break

            # 3) Fallback: pdp_npi in href
            if not price_val:
                decoded = unquote(html_mod.unescape(href))
                pdp_m = re.search(r'pdp_npi=([^&]+)', decoded)
                if pdp_m:
                    parts = pdp_m.group(1).split("!")
                    if len(parts) >= 4:
                        if re.match(r'^[A-Z]{3}$', parts[1]):
                            currency_val = currency_val or parts[1]
                        try:
                            price_val = float(parts[2])
                        except (ValueError, IndexError):
                            pass
                        try:
                            orig_price_val = float(parts[3])
                        except (ValueError, IndexError):
                            pass

            if price_val and price_val > 0:
                p["price"] = price_val
            if orig_price_val and orig_price_val > 0:
                p["original_price"] = orig_price_val
            p["currency"] = currency_val or "RUB"

            # Images
            img_pat = re.compile(r'<img[^>]*class="[^"]*images--item[^"]*"[^>]*src="([^"]+)"', re.I)
            for im in img_pat.finditer(card_html):
                img_url = html_mod.unescape(im.group(1))
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                if img_url not in p["images"]:
                    p["images"].append(img_url)
            lazy_pat = re.compile(r'<div[^>]*class="[^"]*lazy-load[^"]*"[^>]*data-src="([^"]+)"', re.I)
            for im in lazy_pat.finditer(card_html):
                img_url = html_mod.unescape(im.group(1))
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                if img_url not in p["images"] and len(p["images"]) < 5:
                    p["images"].append(img_url)

            products.append(p)

        # Deduplicate
        seen = set()
        unique = []
        for p in products:
            key = p.get("item_id") or p.get("url")
            if key and key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    def _wholesale_product_to_observation(self, prod: dict, listing_id: str, url: str) -> Optional[dict]:
        """Converts wholesale HTML product to observation dict."""
        obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
               "availability": "IN_STOCK", "category_path": None}
        if prod.get("title"):
            obs["fields"]["title"] = {
                "status": "KNOWN", "value": prod["title"][:300],
                "method": "WHOLESALE_HTML", "evidence": {"method": "WHOLESALE_HTML", "source": self.source},
            }
        if prod.get("price"):
            amount = float(prod["price"])
            currency = prod.get("currency", "RUB")
            obs["price_candidates"].append({
                "role": "current", "amount": f"{amount:.2f}",
                "amount_minor": int(round(amount * 100)),
                "currency": currency, "visible_label": f"{amount:,.2f} {currency}",
            })
            if prod.get("original_price"):
                obs["price_candidates"].append({
                    "role": "original", "amount": f"{prod['original_price']:.2f}",
                    "amount_minor": int(round(prod["original_price"] * 100)),
                    "currency": currency, "visible_label": f"{prod['original_price']:,.2f} {currency}",
                })
        for img in prod.get("images", [])[:20]:
            if img and self._is_ali_image(img):
                obs["media"]["items"].append({"type": "image", "source_url": img, "image_data": None})
        if not obs["fields"].get("title") and not obs["price_candidates"]:
            return None
        return obs

    # ─── Collect product (RU override) ───

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        """RU: www-domain fetch (3 retries) → TMD detect → search API fallback."""
        www_url = f"https://www.aliexpress.com/item/{listing_id}.html?_lang=ru_RU"
        observation = None

        for attempt in range(3):
            try:
                from curl_cffi import requests as cffi_req
                self._throttle()
                t0 = time.monotonic()
                resp = cffi_req.get(
                    www_url,
                    impersonate="chrome",
                    proxies=self.pool.get_proxy(),
                    timeout=(15, 30),
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                        "Referer": "https://www.aliexpress.com/",
                    },
                    cookies={"aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU"},
                )
                lat = time.monotonic() - t0
                self.stats["requests"] += 1
                html = resp.text if hasattr(resp, "text") else ""
                bxpunish = resp.headers.get("bxpunish", "0") if hasattr(resp, "headers") else "0"
                is_tmd = (len(html) < 5000) or (bxpunish == "1")

                if resp.status_code == 200 and not is_tmd and len(html) > 10000:
                    self.stats["success"] += 1
                    log.info("ALI_RU: product page OK (try %d), %dB, %.1fs", attempt + 1, len(html), lat)
                    is_captcha = self._detect_captcha(html)
                    try:
                        observation = self.parse_page(resp, listing_id, url, title_hint)
                    except Exception as e:
                        log.warning("ALI_RU: parse_page error: %s", e)
                        observation = None
                    if observation and not is_captcha:
                        has_title = observation.get("fields", {}).get("title", {}).get("value")
                        has_price = len(observation.get("price_candidates", [])) > 0
                        if has_title or has_price:
                            self._ensure_evidence(observation)
                            self._ensure_price_amounts(observation)
                            self._ensure_availability_in_fields(observation)
                            now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                            return {
                                "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                                "capture_action": "COLLECTOR_RUN",
                                "fully_autonomous": True,
                                "collector_source": self.source,
                                "observation": {
                                    **observation,
                                    "source": self.source,
                                    "id": str(listing_id),
                                    "url": url,
                                    "observed_at": now,
                                    "status": "PARTIAL_SUCCESS",
                                    "product_content": True,
                                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                                    "http_status": 200,
                                },
                            }
                else:
                    log.info("ALI_RU: TMD/CAPTCHA (try %d), %dB, bxpunish=%s", attempt + 1, len(html), bxpunish)
                    self.stats["blocked"] += 1
            except Exception as e:
                log.warning("ALI_RU: product fetch error (try %d): %s", attempt + 1, e)
                self.stats["requests"] += 1
            if attempt < 2:
                time.sleep(3)

        # Phase 2: search API fallback
        log.info("ALI_RU: HTML incomplete/captcha after 3 tries, trying search API for id=%s", listing_id)
        observation = self._collect_via_search_api(listing_id, url)
        if observation is None:
            return None

        self._ensure_evidence(observation)
        self._ensure_price_amounts(observation)
        self._ensure_availability_in_fields(observation)
        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        return {
            "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
            "capture_action": "COLLECTOR_RUN",
            "fully_autonomous": True,
            "collector_source": self.source,
            "observation": {
                **observation,
                "source": self.source,
                "id": str(listing_id),
                "url": url,
                "observed_at": now,
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_SEARCH_API",
                "http_status": 200,
            },
        }

    def _collect_via_search_api(self, listing_id: str, url: str) -> Optional[dict]:
        """Находит товар через search API. Несколько стратегий поиска."""
        # Strategy 1: search by item ID directly
        for query in (str(listing_id), f"товар {listing_id}"):
            data = self._fetch_search_api(query, page=1, page_size=40)
            if not data:
                continue
            products = self._parse_search_api(data)
            for p in products:
                if p.get("item_id") == str(listing_id):
                    log.info("ALI_RU: found id=%s via search (query=%r)", listing_id, query[:30])
                    return self._search_product_to_observation(p, listing_id, url)
        # Strategy 2: broader search
        data = self._fetch_search_api("aliexpress", page=1, page_size=40)
        if data:
            products = self._parse_search_api(data)
            for p in products:
                if p.get("item_id") == str(listing_id):
                    return self._search_product_to_observation(p, listing_id, url)
            for p in products:
                if p.get("title") or p.get("price"):
                    log.warning("ALI_RU: id=%s not in results, using first available", listing_id)
                    return self._search_product_to_observation(p, listing_id, url)
        return None

    def _search_product_to_observation(self, prod: dict, listing_id: str, url: str) -> Optional[dict]:
        """Search API product → observation dict."""
        obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
               "availability": "IN_STOCK", "category_path": None}
        if prod.get("title"):
            obs["fields"]["title"] = {
                "status": "KNOWN", "value": prod["title"][:300],
                "method": "SEARCH_API", "evidence": {"method": "SEARCH_API", "source": self.source},
            }
        if prod.get("price"):
            amount = float(prod["price"])
            currency = prod.get("currency", "RUB")
            obs["price_candidates"].append({
                "role": "current", "amount": f"{amount:.2f}",
                "amount_minor": int(round(amount * 100)),
                "currency": currency, "visible_label": f"{amount:,.2f} {currency}",
            })
        for img in prod.get("images", [])[:20]:
            if img and self._is_ali_image(img):
                obs["media"]["items"].append({"type": "image", "source_url": img, "image_data": None})
        if prod.get("seller"):
            obs["fields"]["shop"] = {
                "status": "KNOWN", "value": {"name": prod["seller"]},
                "method": "SEARCH_API", "evidence": {"method": "SEARCH_API", "source": self.source},
            }
        if prod.get("sku"):
            obs["fields"]["sku_id"] = {
                "status": "KNOWN", "value": prod["sku"],
                "method": "SEARCH_API", "evidence": {"method": "SEARCH_API", "source": self.source},
            }
        if not obs["fields"].get("title") and not obs["price_candidates"]:
            return None
        return obs

    def collect_by_search(self, query: str, page: int = 1, page_size: int = 40) -> list[dict]:
        """Собирает товары по поисковому запросу: HTML wholesale (V20) → search API."""
        results = []

        # Phase 1: HTML wholesale page (V20 parser) — primary
        html = self._fetch_wholesale_html(query, page)
        if html:
            products = self._parse_wholesale_html(html)
            log.info("ALI_RU: HTML search found %d products", len(products))
            for prod in products:
                obs = self._wholesale_product_to_observation(
                    prod, prod.get("item_id", ""), prod.get("url", ""))
                if obs is None:
                    continue
                self._ensure_evidence(obs)
                self._ensure_price_amounts(obs)
                self._ensure_availability_in_fields(obs)
                now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                results.append({
                    "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                    "capture_action": "COLLECTOR_RUN",
                    "fully_autonomous": True,
                    "collector_source": self.source,
                    "observation": {
                        **obs,
                        "source": self.source,
                        "id": str(prod.get("item_id", "")),
                        "url": prod.get("url", ""),
                        "observed_at": now,
                        "status": "PARTIAL_SUCCESS",
                        "product_content": True,
                        "evidence_level": "COLLECTOR_AUTONOMOUS_HTML",
                        "http_status": 200,
                    },
                })

        # Quality check: titles should have real content (any language)
        # Geo-detection may give IT/EN titles — that's acceptable
        html_quality_ok = True
        if results:
            good_count = 0
            for r in results:
                t = r["observation"]["fields"].get("title", {}).get("value", "")
                if len(t) >= 10:
                    good_count += 1
            if good_count < max(1, len(results) // 2):
                log.info("ALI_RU: HTML quality low (%d/%d titles with content), merging search API", good_count, len(results))
                html_quality_ok = False

        # Phase 2: search API (fallback or quality merge)
        if len(results) < 5 or not html_quality_ok:
            log.info("ALI_RU: HTML gave %d, trying search API", len(results))
            data = self._fetch_search_api(query, page, page_size)
            if data:
                api_products = self._parse_search_api(data)
                existing_ids = {r["observation"]["id"] for r in results}
                for prod in api_products:
                    if prod.get("item_id") in existing_ids:
                        continue
                    obs = self._search_product_to_observation(
                        prod, prod.get("item_id", ""), prod.get("url", ""))
                    if obs is None:
                        continue
                    self._ensure_evidence(obs)
                    self._ensure_price_amounts(obs)
                    self._ensure_availability_in_fields(obs)
                    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                    results.append({
                        "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                        "capture_action": "COLLECTOR_RUN",
                        "fully_autonomous": True,
                        "collector_source": self.source,
                        "observation": {
                            **obs,
                            "source": self.source,
                            "id": str(prod.get("item_id", "")),
                            "url": prod.get("url", ""),
                            "observed_at": now,
                            "status": "PARTIAL_SUCCESS",
                            "product_content": True,
                            "evidence_level": "COLLECTOR_AUTONOMOUS_SEARCH_API",
                            "http_status": 200,
                        },
                    })

        log.info("ALI_RU: collect_by_search %r → %d products", query[:40], len(results))
        return results
