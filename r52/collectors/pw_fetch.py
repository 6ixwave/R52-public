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
from urllib.parse import unquote, urlsplit

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


def playwright_proxy(proxies: Optional[dict]) -> Optional[dict]:
    """Translate the selected HTTP proxy without passing credentials in server."""
    if not proxies:
        return None
    try:
        selected = proxies.get("https") or proxies.get("http")
        parsed = urlsplit(selected)
        port = parsed.port
        if (parsed.scheme not in ("http", "https", "socks5") or not parsed.hostname
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ValueError("INVALID_BROWSER_PROXY") from None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    result = {"server": f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")}
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


class PwFetcher:
    """Общий Playwright-браузер для нескольких fetch-запросов."""

    def __init__(self, headless: bool = False, ua: str = DEFAULT_UA,
                 proxies: Optional[dict] = None, require_proxy: bool = False,
                 fullscreen: bool = True):
        self._pw = None
        self._browser = None
        self._context = None
        self._headless = headless
        self._ua = ua
        self._proxy = playwright_proxy(proxies)
        self._require_proxy = require_proxy
        self._fullscreen = fullscreen
        self._started = False
        self.stats = {"fetches": 0, "success": 0, "failed": 0}

    def start(self):
        if self._started:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--window-size=1920,1080",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--force-device-scale-factor=1",
        ]
        if self._fullscreen and not self._headless:
            launch_args.append("--kiosk")
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            proxy=self._proxy,
            args=launch_args,
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
        if self._require_proxy and self._proxy is None:
            log.warning("PwFetcher: require_proxy=True, прокси не задан — браузер НЕ стартовал (fail-closed)")
            self._pw.stop()
            self._pw = None
            self._browser = None
            self._context = None
            self._started = False
            return False
        self._started = True
        log.info("PwFetcher: browser started (headless=%s, proxy=%s)", self._headless, "yes" if self._proxy else "no")
        return True

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
            ok = self.start()
            if not ok:
                log.info("PwFetcher: get() — start() вернул False (fail-closed), url=%s", url[:60])
                return None

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

    def get_xhr(self, url: str, patterns, extra_wait_ms: int = 8000,
                xhr_timeout_ms: int = 15000,
                product_key: Optional[str] = None,
                catch_json: bool = False, min_json: int = 1500) -> Optional[PwResult]:
        """Перехватить XHR/fetch-ответы, чей URL содержит любой подстроку из patterns.

        Возвращает PwResult, где html = лучший JSON-тело (dict со списком 'products',
        иначе самое большое тело), meta['xhr_bodies'] = [(url, text), ...].
        """
        if not self._started:
            if not self.start():
                log.info("PwFetcher.get_xhr: start() False (fail-closed), url=%s", url[:60])
                return None
        self.stats["fetches"] += 1
        t0 = time.monotonic()
        page = self._context.new_page()
        bodies = []  # (url, text)

        def _on_response(resp):
            try:
                u = resp.url or ""
                if resp.status != 200:
                    return
                ct = ""
                try:
                    ct = (resp.headers or {}).get("content-type", "") or ""
                except Exception:
                    ct = ""
                url_hit = any(p in u for p in patterns)
                json_hit = catch_json and "json" in ct.lower()
                if url_hit or json_hit:
                    txt = resp.text()
                    if url_hit or len(txt) >= min_json:
                        bodies.append((u, txt))
            except Exception:
                pass

        try:
            page.on("response", _on_response)
            page.set_default_timeout(PAGE_TIMEOUT)
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            if extra_wait_ms > 0:
                page.wait_for_timeout(extra_wait_ms)
            deadline = time.monotonic() + xhr_timeout_ms / 1000.0
            import json as _json0
            while time.monotonic() < deadline:
                _done = False
                if product_key:
                    for _u, _t in bodies:
                        try:
                            _d = _json0.loads(_t)
                        except (ValueError, TypeError):
                            _d = None
                        if isinstance(_d, dict):
                            _pl = _d.get(product_key)
                            if not _pl and isinstance(_d.get("data"), dict):
                                _pl = _d["data"].get(product_key)
                            if isinstance(_pl, list) and _pl:
                                _done = True
                                break
                elif catch_json:
                    _done = any(len(t) >= min_json for _, t in bodies)
                else:
                    _done = bool(bodies)
                if _done:
                    page.wait_for_timeout(400)  # grace для 2-го ответа
                    break
                page.wait_for_timeout(300)
            import json as _json
            best = ""
            best_url = url
            for u, txt in bodies:
                try:
                    d = _json.loads(txt)
                except (ValueError, TypeError):
                    d = None
                if isinstance(d, dict) and isinstance(d.get("products"), list) and d["products"]:
                    best, best_url = txt, u
                    break
                if len(txt) > len(best):
                    best, best_url = txt, u
            status = 200 if bodies else 0
            result = PwResult(
                html=best, status=status, final_url=best_url,
                elapsed=time.monotonic() - t0, meta={"xhr_bodies": bodies},
            )
            if best:
                self.stats["success"] += 1
                log.info("PwFetcher.get_xhr: %d bodies, best %dB from %s",
                         len(bodies), len(best), best_url[:80])
            else:
                self.stats["failed"] += 1
                log.warning("PwFetcher.get_xhr: no matching XHR captured")
            return result
        except Exception as e:
            self.stats["failed"] += 1
            log.warning("PwFetcher.get_xhr ERROR: %s (%.1fs)", e, time.monotonic() - t0)
            return None
        finally:
            page.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
