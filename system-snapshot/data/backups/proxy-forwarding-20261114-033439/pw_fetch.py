"""Playwright-based fetcher для сборщиков маркетплейсов.

Используется когда стандартный HTTP (requests/curl_cffi) блокируется антиботом.
Работает в subprocess (как и сборщики), не затрагивает ядро R52.

Usage:
    from r52.collectors.pw_fetch import PwFetcher

    fetcher = PwFetcher()
    fetcher.start()
    result = fetcher.get("https://www.ozon.ru/product/123/")
    if result:
        html = result.html
        status = result.status
    fetcher.stop()
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("r52.collectors.pw_fetch")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1920, "height": 1080}
PAGE_TIMEOUT = 30_000
NAV_TIMEOUT = 25_000


@dataclass
class PwResult:
    html: str
    status: int
    final_url: str
    elapsed: float
    title: str = ""
    meta: dict = field(default_factory=dict)


class PwFetcher:
    """Общий Playwright-браузер для нескольких fetch-запросов."""

    def __init__(self, headless: bool = True, ua: str = DEFAULT_UA):
        self._pw = None
        self._browser = None
        self._context = None
        self._headless = headless
        self._ua = ua
        self._started = False
        self.stats = {"fetches": 0, "success": 0, "failed": 0}

    def start(self):
        if self._started:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=self._ua,
            viewport=VIEWPORT,
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en']});"
        )
        self._started = True
        log.info("PwFetcher: browser started (headless=%s)", self._headless)

    def stop(self):
        if not self._started:
            return
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            log.warning("PwFetcher stop error: %s", e)
        self._started = False

    def get(self, url: str, wait_selector: str = "", extra_wait_ms: int = 2000) -> Optional[PwResult]:
        """GET страницу. Возвращает PwResult или None."""
        if not self._started:
            self.start()

        self.stats["fetches"] += 1
        t0 = time.monotonic()
        page = self._context.new_page()

        try:
            page.set_default_timeout(PAGE_TIMEOUT)
            response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            if extra_wait_ms > 0:
                page.wait_for_timeout(extra_wait_ms)

            html = page.content()
            status = response.status if response else 0
            title = page.title() or ""
            elapsed = time.monotonic() - t0

            result = PwResult(
                html=html,
                status=status,
                final_url=page.url,
                elapsed=elapsed,
                title=title,
            )

            if status == 200 and len(html) > 500:
                self.stats["success"] += 1
                log.info("PwFetcher: %s -> 200 (%dB, %.1fs)", url[:80], len(html), elapsed)
            else:
                self.stats["failed"] += 1
                log.warning("PwFetcher: %s -> %d (%dB, %.1fs)", url[:80], status, len(html), elapsed)

            return result

        except Exception as e:
            self.stats["failed"] += 1
            elapsed = time.monotonic() - t0
            log.warning("PwFetcher: %s ERROR: %s (%.1fs)", url[:80], e, elapsed)
            return None
        finally:
            page.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
