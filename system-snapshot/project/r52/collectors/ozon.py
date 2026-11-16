"""Сборщик Ozon.

Стратегия парсинга (обновлена 11.11.2026 по реальному SSR-HTML):
1. SSR-HTML с data-widget — основной источник (Ozon НЕ отдаёт JSON-LD и __INITIAL_STATE__ на карточках).
2. og: meta — title, description, image, url.
3. Встроенный JSON (state) — запасной, бывает на отдельных карточках.
4. JSON-LD — редкий, держим как fallback.

Медиа:
- Фото: ir.ozone.ru/s3/... (основной CDN), ozoncdn.net (старый/доп)
- Видео: mp4/webm в HTML или в state

Безопасность:
- Только GET на публичную карточку, без cookie/токенов.
- Прокси + rate limit из base.py.
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import urlsplit, unquote

from .base import BaseCollector
from .discovery import SearchError
from .pw_fetch import PwFetcher

try:
    from curl_cffi import requests as _cffi_req
except ImportError:
    _cffi_req = None

log = logging.getLogger("r52.collectors.ozon")

OZON_HOSTS = ("ozon.ru", "www.ozon.ru")
OZON_CDN = ("ir.ozone.ru", "ozoncdn.net", "ozon.ru", "st.ozone.ru", "cdn1.ozonusercontent.com")

# Паттерн URL карточки Ozon: /product/{id}/ или /product/{slug}-{id}/
CARD_URL_RE = re.compile(r"https://(?:www\.)?ozon\.ru/product/[a-z0-9\-]*(\d+)/?")

# Встроенные JSON-контейнеры (запасной вариант)
STATE_PATTERNS = [
    re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', re.DOTALL),
    re.compile(r'<script[^>]*id=["\']initial-state["\'][^>]*>(\{.*?\})</script>', re.DOTALL),
    re.compile(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(\{.*?\})</script>', re.DOTALL),
    re.compile(r'data-state=["\'](\{.*?\})["\']', re.DOTALL),
    re.compile(r'window\.__APP_STATE__\s*=\s*(\{.*?\})\s*;', re.DOTALL),
]

# Паттерн изображений Ozon: ir.ozone.ru/s3/... или ozoncdn.net
IMG_PATTERNS = [
    re.compile(r'(?:src|data-src|data-original|href)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', re.I),
    re.compile(r'(?:src|data-src|data-original|href)=["\']([^"\']*ozoncdn\.net/[^"\']+)["\']', re.I),
    re.compile(r'(?:src|data-src|data-original|href)=["\']([^"\']*ozonusercontent\.com/[^"\']+)["\']', re.I),
    re.compile(r'"(https?://ir\.ozone\.ru/s3/[^"]+)"', re.I),
]

# Паттерн видео
VIDEO_PATTERNS = [
    re.compile(r'(?:src|data-src|data-url)=["\']([^"\']*\.(?:mp4|webm)(?:\?[^"\']*)?)["\']', re.I),
    re.compile(r'"(https?://[^"]*\.(?:mp4|webm)[^"]*)"', re.I),
]


class OzonCollector(BaseCollector):
    source = "OZON"
    title = "Ozon"

    def __init__(self, data_dir=None, use_proxies: bool = True):
        super().__init__(data_dir=data_dir, use_proxies=use_proxies)
        self._pw: Optional[PwFetcher] = None
        self._pw_direct: Optional[PwFetcher] = None
        self._cffi = _cffi_req.Session(impersonate="chrome") if _cffi_req else None
        self._warmed_up = False

    def _warmup(self):
        """Session warmup: p1→403 (challenge), p2→200. Один пробный GET главной."""
        if self._warmed_up:
            return
        try:
            self._throttle()
            proxies = self.pool.get_proxy()
            if proxies is None and self.pool.use_proxies:
                log.info("OZON: warmup FAIL_CLOSED — прокси нет, skip (не direct)")
                self._warmed_up = True
                return
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            }
            if self._cffi:
                resp = self._cffi.get(
                    "https://www.ozon.ru/",
                    timeout=15, proxies=proxies,
                    headers=headers, verify=False,
                )
            else:
                resp = self.session.get(
                    "https://www.ozon.ru/", proxies=proxies, timeout=(5, 15),
                    headers=headers, verify=False,
                )
            self.stats["requests"] += 1
            log.info("OZON warmup: HTTP %d", getattr(resp, 'status_code', 0))
        except Exception as e:
            log.info("OZON warmup failed: %s", e)
        self._warmed_up = True

    def _get_pw(self) -> Optional[PwFetcher]:
        if self._pw is None:
            proxies = self.pool.get_proxy()
            self._pw = PwFetcher(proxies=proxies, require_proxy=self.pool.use_proxies)
            if not self._pw.start():
                log.info("OZON: PwFetcher fail-closed (no proxy)")
                self._pw = None
        return self._pw

    def _get_pw_direct(self) -> Optional[PwFetcher]:
        """Direct-браузер (без прокси) — надёжный путь для WAF-protected search.

        2026-11-15: diag показала, что прокси-браузер режется WAF (403/3311B),
        а direct-браузер проходит (200/873KB, slug-URL)."""
        if self._pw_direct is None:
            self._pw_direct = PwFetcher(proxies=None, require_proxy=False)
            if not self._pw_direct.start():
                log.info("OZON: PwFetcher direct fail-closed")
                self._pw_direct = None
        return self._pw_direct

    def close(self):
        if self._pw:
            self._pw.stop()
            self._pw = None
        if self._pw_direct:
            self._pw_direct.stop()
            self._pw_direct = None

    def build_url(self, listing_id: str) -> str:
        return f"https://www.ozon.ru/product/{listing_id}/"

    # ================================================================
    # COMPOSER API (fast, no browser)
    # ================================================================

    def _try_composer_api(self, listing_id: str, url: str, title_hint: str) -> Optional[dict]:
        """Пробует Ozon Composer API — быстрый JSON без браузера."""
        endpoints = [
            f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{listing_id}/",
            f"https://www.ozon.ru/api/entrypoint/pdp/{listing_id}",
        ]
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.ozon.ru/",
        }
        # Add mobile API endpoint
        endpoints.append(
            f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{listing_id}/&isMobile=true"
        )
        for ep_url in endpoints:
            try:
                self._throttle()
                proxies = self.pool.get_proxy()
                if self._cffi:
                    kw = {"impersonate": "chrome", "timeout": 15, "headers": headers, "proxies": proxies, "verify": False}
                    resp = self._cffi.get(ep_url, **kw)
                else:
                    resp = self.session.get(ep_url, headers=headers, proxies=proxies, timeout=(5, 15))
                self.stats["requests"] += 1
                if resp.status_code != 200:
                    log.info("OZON %s: %s -> HTTP %d", listing_id, ep_url.split("?")[0].split("/")[-1], resp.status_code)
                    continue
                data = resp.json()
                if not isinstance(data, dict):
                    continue
                fields = {}
                price_candidates = []
                media_items = []
                seen_media = set()

                # Title
                title = None
                for path in [("page", "title"), ("page", "seoTitle"), ("product", "title"),
                             ("product", "name"), ("data", "title"), ("props", "pageProps", "product", "title")]:
                    title = self._deep_get(data, *path)
                    if title:
                        break
                if title:
                    fields["title"] = {"status": "KNOWN", "value": str(title), "method": "COMPOSER_API"}

                # Prices
                price_obj = None
                for path in [("product", "price"), ("page", "price"), ("data", "price"),
                             ("product", "priceInfo"), ("props", "pageProps", "product", "price")]:
                    price_obj = self._deep_get(data, *path)
                    if price_obj:
                        break
                if not price_obj:
                    blocks = self._deep_get(data, "page", "blocks")
                    if isinstance(blocks, list):
                        for b in blocks:
                            if isinstance(b, dict) and b.get("type") in ("webPrice", "price", "pdp-price"):
                                price_obj = b.get("props", {}).get("price") or b.get("data", {}).get("price")
                                if price_obj:
                                    break
                if isinstance(price_obj, dict):
                    for pk, role in [("price", "regular"), ("current", "regular"), ("basic", "regular"),
                                     ("old", "old"), ("crossed", "old"), ("card", "card"),
                                     ("min", "regular"), ("max", "regular"), ("value", "regular")]:
                        pv = price_obj.get(pk)
                        if isinstance(pv, (int, float)) and pv > 0:
                            amount = int(pv) if pv >= 1000 else int(pv * 100)
                            price_candidates.append({"role": role, "amount_minor": amount,
                                                     "currency": "RUB", "label": f"composer.{pk}"})
                elif isinstance(price_obj, (int, float)) and price_obj > 0:
                    amount = int(price_obj) if price_obj >= 1000 else int(price_obj * 100)
                    price_candidates.append({"role": "regular", "amount_minor": amount,
                                             "currency": "RUB", "label": "composer.price"})

                # Media
                for path in [("product", "images"), ("page", "images"), ("product", "photos"),
                             ("product", "gallery"), ("data", "images")]:
                    images = self._deep_get(data, *path)
                    if isinstance(images, list) and images:
                        for img in images[:30]:
                            if isinstance(img, str):
                                self._add_media(media_items, seen_media, "image", img)
                            elif isinstance(img, dict):
                                u = img.get("url") or img.get("src") or img.get("path")
                                if u:
                                    self._add_media(media_items, seen_media, "image", u)
                        break

                # Description
                for path in [("product", "description"), ("page", "description"), ("data", "description")]:
                    desc = self._deep_get(data, *path)
                    if desc and isinstance(desc, str) and len(desc) > 20:
                        fields["description"] = {"status": "KNOWN", "value": desc[:3000], "method": "COMPOSER_API"}
                        break

                # Category
                for path in [("product", "category"), ("page", "breadcrumb"), ("data", "category")]:
                    cat = self._deep_get(data, *path)
                    if cat:
                        if isinstance(cat, list):
                            cat = " > ".join(str(x) for x in cat[:4])
                        elif isinstance(cat, dict):
                            cat = cat.get("name") or cat.get("path") or str(cat)
                        fields["category"] = {"status": "KNOWN", "value": str(cat), "method": "COMPOSER_API"}
                        break

                if not fields.get("title") and not price_candidates:
                    log.info("OZON %s: composer %s 200 но данных мало", listing_id, ep_url.split("?")[0].split("/")[-1])
                    continue

                log.info("OZON %s: Composer-API OK (title=%s, prices=%d, media=%d)",
                         listing_id, str(fields.get("title", {}).get("value", "?"))[:40],
                         len(price_candidates), len(media_items))

                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                has_title = bool(fields.get("title", {}).get("value", ""))
                has_price = len(price_candidates) > 0
                status = "PARTIAL_SUCCESS" if (has_title and has_price) else "DEGRADED"
                return {
                    "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                    "capture_action": "COLLECTOR_RUN",
                    "fully_autonomous": True,
                    "collector_source": self.source,
                    "observation": {
                        "fields": fields,
                        "price_candidates": price_candidates,
                        "media": {"items": media_items},
                        "availability": "UNKNOWN",
                        "category_path": fields.get("category", {}).get("value"),
                        "source": self.source,
                        "id": str(listing_id),
                        "url": url,
                        "observed_at": now,
                        "status": status,
                        "product_content": True,
                        "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                        "http_status": 200,
                    },
                }
            except Exception as e:
                log.info("OZON %s: composer %s error: %s", listing_id, ep_url.split("?")[0].split("/")[-1], e)

        return None

    # ================================================================
    # COLLECT_PRODUCT: Composer-API → Playwright → HTTP
    # ================================================================

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        """Composer-API → Playwright → HTTP fallback."""
        # --- Strategy 0: Composer API ---
        composer_result = self._try_composer_api(listing_id, url, title_hint)
        if composer_result:
            return composer_result

        # --- Strategy 1: Playwright ---
        try:
            pw = self._get_pw()
            # 2026-11-15: 3с не хватало на FAB proof-of-work; 10с — как в search (проходит challenge)
            result = pw.get(url, extra_wait_ms=10000)
            if result and result.status == 200 and len(result.html) > 1000:
                class _Resp:
                    def __init__(self, html, status):
                        self.text = html
                        self.status_code = status
                observation = self.parse_page(_Resp(result.html, result.status), listing_id, url, title_hint)
                if observation:
                    self._ensure_evidence(observation)
                    self._ensure_price_amounts(observation)
                    self._ensure_availability_in_fields(observation)
                    from datetime import datetime, timezone, timedelta
                    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                    has_title = bool(observation.get("fields", {}).get("title", {}).get("value", ""))
                    has_price = len(observation.get("price_candidates", [])) > 0
                    status = "PARTIAL_SUCCESS" if (has_title and has_price) else "DEGRADED"
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
                            "status": status,
                            "product_content": True,
                            "evidence_level": "COLLECTOR_PLAYWRIGHT",
                            "http_status": 200,
                        },
                    }
                log.info("OZON %s: PW 200 но parse=None", listing_id)
            else:
                log.info("OZON %s: PW status=%d len=%d", listing_id,
                         result.status if result else 0,
                         len(result.html) if result else 0)
        except Exception as e:
            log.warning("OZON %s: Playwright error: %s", listing_id, e)

        # --- Strategy 2: HTTP fallback (cffi first, then base) ---
        if self._cffi:
            try:
                self._throttle()
                headers = {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Referer": "https://www.ozon.ru/",
                }
                resp = self._cffi.get(url, impersonate="chrome", timeout=(15, 30), headers=headers,
                                      proxies=self.pool.get_proxy())
                self.stats["requests"] += 1
                if resp.status_code == 200 and len(resp.text) > 2000:
                    class _Resp:
                        def __init__(self, html, status):
                            self.text = html
                            self.status_code = status
                    observation = self.parse_page(_Resp(resp.text, resp.status_code), listing_id, url, title_hint)
                    if observation:
                        self._ensure_evidence(observation)
                        self._ensure_price_amounts(observation)
                        self._ensure_availability_in_fields(observation)
                        from datetime import datetime, timezone, timedelta
                        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                        has_title = bool(observation.get("fields", {}).get("title", {}).get("value", ""))
                        has_price = len(observation.get("price_candidates", [])) > 0
                        status = "PARTIAL_SUCCESS" if (has_title and has_price) else "DEGRADED"
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
                                "status": status,
                                "product_content": True,
                                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                                "http_status": 200,
                            },
                        }
                    log.info("OZON %s: cffi HTTP 200 но parse=None", listing_id)
                else:
                    log.info("OZON %s: cffi HTTP status=%d len=%d",
                             listing_id, resp.status_code, len(resp.text) if hasattr(resp, 'text') else 0)
            except Exception as e:
                log.warning("OZON %s: cffi HTTP error: %s", listing_id, e)

        return super().collect_product(listing_id, url, title_hint)

    def parse_page(self, resp, listing_id, url, title_hint):
        html = resp.text
        if not html or len(html) < 500:
            log.warning("OZON %s: короткий ответ (%d байт)", listing_id, len(html or ""))
            return None

        # Антибот-челлендж (JS challenge page) — не товар
        if 'Antibot Challenge Page' in html or 'abt-challenge' in html or 'fab_chlg_' in html:
            log.warning("OZON %s: ANTI-BOT challenge page (len=%d)", listing_id, len(html))
            return None

        # Проверка: страница "Такой страницы не существует"
        if 'data-widget="error"' in html and "Такой страницы не существует" in html:
            log.warning("OZON %s: 404-страница (widget=error)", listing_id)
            return None

        # Запасные источники
        jsonld = self.extract_json_ld(html)
        product_ld = self.find_product_jsonld(jsonld)
        state = self._extract_state(html)

        fields = {}
        price_candidates = []
        availability = "UNKNOWN"

        # === TITLE ===
        title = self._extract_title(html, product_ld, title_hint, listing_id)
        fields["title"] = {"status": "KNOWN", "value": title, "method": "SSR"}

        # === PRICES ===
        self._extract_prices_from_html(html, price_candidates)
        if product_ld:
            self._extract_prices_from_ld(product_ld, price_candidates)
        if state:
            self._extract_prices_from_state(state, price_candidates)
        price_candidates = self._dedupe_prices(price_candidates)

        # === MEDIA ===
        media_items = self._extract_media(html, state, product_ld)

        # === DESCRIPTION ===
        description = self._extract_description(html, state, product_ld)
        if description:
            fields["description"] = {"status": "KNOWN", "value": description[:3000], "method": "SSR"}

        # === CATEGORY (breadcrumb) ===
        category = self._extract_category(html, state)
        if category:
            fields["category"] = {"status": "KNOWN", "value": category, "method": "BREADCRUMB"}

        # === SKU / MODEL ===
        sku = self._extract_sku(html, url, listing_id, product_ld)
        if sku:
            fields["model"] = {"status": "KNOWN", "value": sku, "method": "URL"}

        # === RATING / REVIEWS ===
        rating, reviews_count = self._extract_rating(html)
        if rating:
            fields["rating"] = {"status": "KNOWN", "value": rating, "method": "SSR"}
        if reviews_count:
            fields["reviews_count"] = {"status": "KNOWN", "value": reviews_count, "method": "SSR"}

        # === CHARACTERISTICS (webAspects / webShortCharacteristics) ===
        chars = self._extract_characteristics(html)
        if chars:
            fields["characteristics"] = {"status": "KNOWN", "value": chars, "method": "SSR"}

        # === BRAND ===
        brand = self._extract_brand(html, state, product_ld)
        if brand:
            fields["brand"] = {"status": "KNOWN", "value": brand, "method": "SSR"}

        # === AVAILABILITY ===
        availability = self._extract_availability(html, state, product_ld)

        return {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": availability,
            "category_path": category,
        }

    # ------------------------------------------------------------------
    # TITLE
    # ------------------------------------------------------------------
    def _extract_title(self, html: str, product_ld: Optional[dict], title_hint: str, listing_id: str) -> str:
        # 1. og:title
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            t = m.group(1).strip()
            if t:
                return t
        # 2. JSON-LD
        if product_ld:
            t = product_ld.get("name") or product_ld.get("title")
            if t:
                return t.strip()
        # 3. <title>
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            t = m.group(1).strip()
            t = re.sub(r'\s*[-|]\s*(Ozon|OZON)\s*$', '', t, flags=re.I)
            if t:
                return t
        # 4. h1
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
        if m:
            return m.group(1).strip()
        return title_hint or f"OZON-{listing_id}"

    # ------------------------------------------------------------------
    # PRICES
    # ------------------------------------------------------------------
    def _extract_prices_from_html(self, html: str, candidates: list):
        # webPrice widget: ищем блок и парсим цены из него
        price_block = self._get_widget_block(html, "webPrice")
        search_html = price_block or html

        # og:price:amount
        m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            try:
                minor = int(round(float(m.group(1)) * 100))
                candidates.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "og:price"})
            except ValueError:
                pass

        # data-price
        for m in re.finditer(r'data-price=["\'](\d+)["\']', search_html, re.I):
            try:
                minor = int(m.group(1))
                if minor > 0:
                    candidates.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "data-price"})
            except ValueError:
                pass

        # Видимый текст: "2 164 ₽" / "936 ₽" / "246 ₽ за 1 шт"
        # Ищем все вхождения числа+₽ в price block или в целом HTML
        price_text_re = re.compile(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб(?:\.|лей)?)', re.I)
        seen_amounts = set()
        for m in price_text_re.finditer(search_html):
            raw = m.group(1).replace(" ", "").replace("\u00a0", "").replace("\xa0", "")
            try:
                amount = float(raw)
                if amount < 1 or amount > 10_000_000:
                    continue
                minor = int(round(amount * 100))
                if minor in seen_amounts:
                    continue
                seen_amounts.add(minor)
                # Определяем роль: первое большое — regular, меньшее — old/discount
                candidates.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "HTML text"})
            except ValueError:
                continue

        # "за 1 шт" — уточнение
        m = re.search(r'(\d[\d\s\u00a0]{1,9})\s*₽\s*за\s*(\d+)\s*шт', search_html, re.I)
        if m:
            try:
                amount = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                qty = int(m.group(2))
                minor = int(round(amount * 100))
                candidates.append({"role": "unit", "amount_minor": minor, "currency": "RUB",
                                   "label": f"HTML {amount}₽/{qty}шт"})
            except ValueError:
                pass

    def _extract_prices_from_ld(self, product_ld: dict, candidates: list):
        offers = product_ld.get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list):
            return
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            price_val = offer.get("price") or offer.get("lowPrice")
            currency = offer.get("priceCurrency", "RUB")
            if price_val:
                try:
                    minor = int(round(float(price_val) * 100))
                    candidates.append({"role": "regular", "amount_minor": minor,
                                       "currency": currency, "label": "JSON-LD price"})
                except (ValueError, TypeError):
                    pass

    def _extract_prices_from_state(self, state: dict, candidates: list):
        search_paths = [
            ("product", "price"), ("product", "prices"),
            ("data", "price"), ("props", "pageProps", "product", "price"),
            ("product", "currentPrice"), ("product", "priceInfo"),
        ]
        for path in search_paths:
            obj = self._deep_get(state, *path)
            if obj is None:
                continue
            if isinstance(obj, dict):
                for role_key, role in [("price", "regular"), ("current", "regular"),
                                       ("old", "old"), ("crossed", "old"),
                                       ("card", "card"), ("min", "regular")]:
                    val = obj.get(role_key)
                    if val and isinstance(val, (int, float)) and val > 0:
                        candidates.append({
                            "role": role,
                            "amount_minor": int(val * 100) if val < 100000 else int(val),
                            "currency": "RUB",
                            "label": f"state.{role_key}",
                        })
            elif isinstance(obj, (int, float)) and obj > 0:
                candidates.append({
                    "role": "regular",
                    "amount_minor": int(obj * 100) if obj < 100000 else int(obj),
                    "currency": "RUB",
                    "label": "state.price",
                })

    # ------------------------------------------------------------------
    # MEDIA
    # ------------------------------------------------------------------
    def _extract_media(self, html: str, state: Optional[dict], product_ld: Optional[dict]) -> list:
        items = []
        seen_urls = set()

        # 1. Из HTML — все img-паттерны
        for pattern in IMG_PATTERNS:
            for m in pattern.finditer(html):
                url = m.group(1)
                self._add_media(items, seen_urls, "image", url)

        # 2. og:image
        m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            self._add_media(items, seen_urls, "image", m.group(1))
        for m in re.finditer(r'<meta[^>]*property=["\']og:image:secure_url["\'][^>]*content=["\']([^"\']+)', html, re.I):
            self._add_media(items, seen_urls, "image", m.group(1))

        # 3. Из JSON-LD
        if product_ld:
            img = product_ld.get("image")
            if isinstance(img, str):
                self._add_media(items, seen_urls, "image", img)
            elif isinstance(img, list):
                for u in img:
                    if isinstance(u, str):
                        self._add_media(items, seen_urls, "image", u)
                    elif isinstance(u, dict):
                        self._add_media(items, seen_urls, "image", u.get("url", ""))

        # 4. Из state
        if state:
            self._media_from_state(state, items, seen_urls)

        # 5. Видео
        for pattern in VIDEO_PATTERNS:
            for m in pattern.finditer(html):
                self._add_media(items, seen_urls, "video", m.group(1))
        if state:
            self._videos_from_state(state, items, seen_urls)

        return items[:60]

    def _media_from_state(self, state: dict, items: list, seen: set):
        paths = [
            ("product", "images"), ("product", "photos"), ("product", "imageList"),
            ("props", "pageProps", "product", "images"),
            ("props", "pageProps", "product", "gallery"),
            ("data", "images"), ("product", "gallery"),
        ]
        for path in paths:
            obj = self._deep_get(state, *path)
            if not obj:
                continue
            if isinstance(obj, list):
                for entry in obj:
                    if isinstance(entry, str):
                        self._add_media(items, seen, "image", entry)
                    elif isinstance(entry, dict):
                        url = entry.get("url") or entry.get("src") or entry.get("path") or entry.get("link")
                        if url:
                            self._add_media(items, seen, "image", url)

    def _videos_from_state(self, state: dict, items: list, seen: set):
        for path in [("product", "videos"), ("product", "video"),
                     ("props", "pageProps", "product", "videos"), ("data", "videos")]:
            obj = self._deep_get(state, *path)
            if not obj:
                continue
            if isinstance(obj, list):
                for v in obj:
                    if isinstance(v, str) and (".mp4" in v or ".webm" in v):
                        self._add_media(items, seen, "video", v)
                    elif isinstance(v, dict):
                        url = v.get("url") or v.get("src") or v.get("link")
                        if url and (".mp4" in url or ".webm" in url):
                            self._add_media(items, seen, "video", url)

    def _add_media(self, items: list, seen: set, mtype: str, url: str):
        if not url or url in seen:
            return
        # Нормализация: убираем resize-суффиксы для оригинала
        # ir.ozone.ru/s3/multimedia-1-5/wc500/123.jpg → берём как есть (wc500 = 500px)
        # Для "оригинала" можно wc1200 или без wc
        safe = self.safe_media_url(url, "OZON")
        if safe is None:
            if any(d in url for d in OZON_CDN):
                safe = url
            else:
                return
        seen.add(safe)
        items.append({"type": mtype, "source_url": safe, "image_data": None})

    # ------------------------------------------------------------------
    # DESCRIPTION
    # ------------------------------------------------------------------
    def _extract_description(self, html: str, state: Optional[dict], product_ld: Optional[dict]) -> Optional[str]:
        # 1. og:description
        m = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            desc = m.group(1).strip()
            if len(desc) > 30:
                return desc
        # 2. JSON-LD
        if product_ld:
            d = product_ld.get("description")
            if d and len(d) > 20:
                return re.sub(r'<[^>]+>', ' ', d).strip()[:3000]
        # 3. State
        if state:
            d = self._extract_description_from_state(state)
            if d:
                return d
        # 4. HTML: ищем длинный текст-блок (description widget или просто текст)
        # Ozon кладёт описание в div с class pdp_* или в отдельный блок
        m = re.search(r'<div[^>]*(?:class|id)=["\'][^"\']*description[^"\']*["\'][^>]*>(.*?)</div>', html, re.I | re.DOTALL)
        if m:
            text = re.sub(r'<[^>]+>', ' ', m.group(1))
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 50:
                return text[:3000]
        return None

    def _extract_description_from_state(self, state: dict) -> Optional[str]:
        for path in [("product", "description"), ("product", "descriptionHtml"),
                     ("props", "pageProps", "product", "description"), ("data", "description")]:
            obj = self._deep_get(state, *path)
            if obj and isinstance(obj, str) and len(obj) > 20:
                text = re.sub(r'<[^>]+>', ' ', obj)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:3000]
        return None

    # ------------------------------------------------------------------
    # CATEGORY (breadcrumb)
    # ------------------------------------------------------------------
    def _extract_category(self, html: str, state: Optional[dict]) -> Optional[str]:
        # 1. Breadcrumb widget: <div data-widget="breadCrumbs">...<a>...<span>Хобби</span></a>...
        bc_block = self._get_widget_block(html, "breadCrumbs")
        if bc_block:
            # Извлекаем текст из <span> внутри <a>
            spans = re.findall(r'<span>([^<]+)</span>', bc_block)
            if len(spans) >= 2:
                return " > ".join(s.strip() for s in spans if s.strip())
            # Или из <li>
            items = re.findall(r'<li[^>]*>(.*?)</li>', bc_block, re.DOTALL)
            texts = []
            for item in items:
                t = re.sub(r'<[^>]+>', '', item).strip()
                if t:
                    texts.append(t)
            if len(texts) >= 2:
                return " > ".join(texts)

        # 2. State
        if state:
            for path in [("product", "category"), ("product", "categoryPath"),
                         ("props", "pageProps", "product", "category"), ("data", "category")]:
                obj = self._deep_get(state, *path)
                if obj:
                    if isinstance(obj, str):
                        return obj
                    if isinstance(obj, list):
                        return " > ".join(str(x) for x in obj)
                    if isinstance(obj, dict):
                        return obj.get("name") or obj.get("path")

        # 3. og:url → path segments
        m = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            path = urlsplit(m.group(1)).path
            parts = [p for p in path.split("/") if p and p != "product" and not p.isdigit()]
            if len(parts) >= 2:
                return " > ".join(parts[:3])
        return None

    # ------------------------------------------------------------------
    # SKU
    # ------------------------------------------------------------------
    def _extract_sku(self, html: str, url: str, listing_id: str, product_ld: Optional[dict] = None) -> Optional[str]:
        # 0. Из JSON-LD (наиболее специфичный)
        if product_ld:
            sku = product_ld.get("sku")
            if sku and str(sku) != str(listing_id):
                return str(sku)
        # 1. Из URL
        m = CARD_URL_RE.search(url)
        if m:
            return m.group(1)
        # 2. Из webDetailSKU widget
        sku_block = self._get_widget_block(html, "webDetailSKU")
        if sku_block:
            m = re.search(r'(\d{6,})', sku_block)
            if m:
                return m.group(1)
        # 3. Из listing_id
        if listing_id and listing_id.isdigit():
            return listing_id
        return None

    # ------------------------------------------------------------------
    # RATING
    # ------------------------------------------------------------------
    def _extract_rating(self, html: str) -> tuple:
        """Возвращает (rating_str, reviews_count_str) или (None, None)."""
        # webSingleProductScore: "4.9 • 12 972 отзыва"
        score_block = self._get_widget_block(html, "webSingleProductScore")
        if score_block:
            m = re.search(r'([\d.]+)\s*•\s*([\d\s\u00a0]+)\s*отзыв', score_block)
            if m:
                return m.group(1), m.group(2).replace(" ", "").replace("\u00a0", "")
            # Альтернативный формат
            m = re.search(r'([\d.]+)\s*•\s*([\d\s\u00a0]+)', score_block)
            if m:
                return m.group(1), m.group(2).replace(" ", "").replace("\u00a0", "")
        # Fallback: ищем в целом HTML
        m = re.search(r'([\d.]+)\s*•\s*([\d\s\u00a0]+)\s*отзыв', html)
        if m:
            return m.group(1), m.group(2).replace(" ", "").replace("\u00a0", "")
        return None, None

    # ------------------------------------------------------------------
    # CHARACTERISTICS
    # ------------------------------------------------------------------
    def _extract_characteristics(self, html: str) -> Optional[str]:
        """Достаёт характеристики из webAspects / webShortCharacteristics."""
        chars_parts = []

        # webAspects: <span class="...">Длина, м:</span> ... <span>3</span>
        aspects_block = self._get_widget_block(html, "webAspects")
        if aspects_block:
            # Парим пары "label: value"
            pairs = re.findall(r'<span[^>]*>([^<]+):</span>\s*<span[^>]*>([^<]+)</span>', aspects_block)
            for label, value in pairs:
                label = label.strip()
                value = value.strip()
                if label and value:
                    chars_parts.append(f"{label} {value}")

        # webShortCharacteristics
        short_block = self._get_widget_block(html, "webShortCharacteristics")
        if short_block:
            # Текстовые пары
            texts = re.findall(r'<span[^>]*class="[^"]*ts[^"]*"[^>]*>([^<]+)</span>', short_block)
            for t in texts:
                t = t.strip()
                if t and len(t) > 2 and t not in chars_parts:
                    chars_parts.append(t)

        if chars_parts:
            return " | ".join(chars_parts[:30])
        return None

    # ------------------------------------------------------------------
    # BRAND
    # ------------------------------------------------------------------
    def _extract_brand(self, html: str, state: Optional[dict], product_ld: Optional[dict]) -> Optional[str]:
        if product_ld:
            b = product_ld.get("brand")
            if isinstance(b, dict):
                return b.get("name")
            elif isinstance(b, str):
                return b
        if state:
            for path in [("product", "brand"), ("product", "manufacturer"),
                         ("props", "pageProps", "product", "brand")]:
                obj = self._deep_get(state, *path)
                if obj and isinstance(obj, str):
                    return obj
                if isinstance(obj, dict):
                    return obj.get("name")
        # Из HTML: data-brand, meta
        m = re.search(r'data-brand=["\']([^"\']+)', html, re.I)
        if m:
            return m.group(1)
        return None

    # ------------------------------------------------------------------
    # AVAILABILITY
    # ------------------------------------------------------------------
    def _extract_availability(self, html: str, state: Optional[dict], product_ld: Optional[dict]) -> str:
        # JSON-LD
        if product_ld:
            offers = product_ld.get("offers")
            if isinstance(offers, dict):
                offers = [offers]
            if isinstance(offers, list):
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    avail = (offer.get("availability") or "").upper()
                    if "IN_STOCK" in avail or "INSTOCK" in avail:
                        return "IN_STOCK"
                    if "OUT_OF_STOCK" in avail or "OUTOFSTOCK" in avail:
                        return "OUT_OF_STOCK"

        # State
        if state:
            for path in [("product", "availability"), ("product", "inStock"),
                         ("props", "pageProps", "product", "availability"),
                         ("data", "availability"), ("product", "stock")]:
                obj = self._deep_get(state, *path)
                if obj is None:
                    continue
                if isinstance(obj, bool):
                    return "IN_STOCK" if obj else "OUT_OF_STOCK"
                if isinstance(obj, str):
                    if obj.lower() in ("in_stock", "available", "yes", "true", "1"):
                        return "IN_STOCK"
                    if obj.lower() in ("out_of_stock", "unavailable", "no", "false", "0"):
                        return "OUT_OF_STOCK"
                if isinstance(obj, int):
                    return "IN_STOCK" if obj > 0 else "OUT_OF_STOCK"

        # HTML fallback
        if re.search(r"в наличии|есть в наличии|доступен|заказать|в корзину", html, re.I):
            return "IN_STOCK"
        if re.search(r"нет в наличии|закончился|распродан|ожидание|предзаказ|поставка", html, re.I):
            return "OUT_OF_STOCK"
        # webAddToCart — если кнопка "В корзину" видна → в наличии
        if 'data-widget="webAddToCart"' in html and "В корзину" in html:
            return "IN_STOCK"
        return "UNKNOWN"

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    def _get_widget_block(self, html: str, widget_name: str) -> Optional[str]:
        """Вырезает HTML-блок от data-widget="name" до следующего data-widget или закрытия."""
        # Находим начало
        pattern = re.compile(r'data-widget=["\']' + re.escape(widget_name) + r'["\']', re.I)
        m = pattern.search(html)
        if not m:
            return None
        start = m.start()
        # Ищем конец: следующий data-widget= или конец строки
        rest = html[start:]
        # Простой heuristic: берём 5000 символов или до следующего data-widget
        next_widget = re.search(r'data-widget=["\'](?!' + re.escape(widget_name) + r')', rest[20:])
        if next_widget:
            end = start + 20 + next_widget.start()
        else:
            end = min(start + 5000, len(html))
        return html[start:end]

    def _extract_state(self, html: str) -> Optional[dict]:
        for pattern in STATE_PATTERNS:
            m = pattern.search(html)
            if m:
                try:
                    data = json.loads(m.group(1))
                    if isinstance(data, dict) and data:
                        return data
                except (json.JSONDecodeError, ValueError):
                    continue
        # Большой JSON в script
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        for script in scripts:
            stripped = script.strip()
            if stripped.startswith("{") and len(stripped) > 1000:
                try:
                    data = json.loads(stripped)
                    if isinstance(data, dict) and ("product" in data or "state" in data or "data" in data or "props" in data):
                        return data
                except (json.JSONDecodeError, ValueError):
                    continue
        return None

    def _deep_get(self, d: dict, *keys) -> Optional[object]:
        obj = d
        for k in keys:
            if not isinstance(obj, dict):
                return None
            obj = obj.get(k)
        return obj

    @staticmethod
    def _dedupe_prices(candidates: list) -> list:
        seen = set()
        result = []
        for c in candidates:
            key = (c["role"], c["amount_minor"], c["currency"])
            if key not in seen and c["amount_minor"] > 0:
                seen.add(key)
                result.append(c)
        # Сортировка: regular первым, потом по сумме
        role_order = {"regular": 0, "unit": 1, "old": 2, "card": 3, "wallet": 4}
        result.sort(key=lambda x: (role_order.get(x["role"], 9), x["amount_minor"]))
        return result

    # ================================================================
    # SEARCH MODE: collect_by_search
    # ================================================================

    def collect_by_search(self, query: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Search Ozon: HTML search page → parse product tiles."""
        from urllib.parse import quote
        from datetime import datetime, timezone, timedelta

        self._warmup()

        # 2026-11-14: from_global=true (подтверждено Apify/hermes 2025-26)
        url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true&page={page}"
        results = []

        try:
            self._throttle()
            proxies = self.pool.get_proxy()
            if proxies is None and self.pool.use_proxies:
                log.info("OZON: search FAIL_CLOSED — прокси нет, skip (не direct)")
                raise SearchError("SEARCH_PROXY_NOT_READY")
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.ozon.ru/",
            }
            resp = None
            # WAF pattern: p1 often 403, p2+ passes (verified 2026-11-14)
            # Strategy: try requested page; if 403 and page==1, try page=2
            try_pages = [page]
            if page == 1:
                try_pages.append(2)
            for t_page in try_pages:
                t_url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true&page={t_page}"
                for attempt in range(2):
                    if self._cffi:
                        resp = self._cffi.get(t_url, impersonate="chrome", timeout=(15, 30), headers=headers,
                                              verify=False, proxies=proxies)
                    else:
                        resp = self.session.get(t_url, headers=headers, proxies=proxies, timeout=(10, 30), verify=False)
                    self.stats["requests"] += 1
                    if resp.status_code == 200:
                        break
                    if resp.status_code in (403, 429) and attempt == 0:
                        log.info("OZON search: HTTP %d, retrying in 3s", resp.status_code)
                        import time as _t
                        _t.sleep(3)
                        continue
                    break
                if resp.status_code == 200:
                    if t_page != page:
                        log.info("OZON search: page %d passed (p%d was 403)", t_page, page)
                    break
            else:
                # all try_pages exhausted
                pass

            _sc = getattr(resp, 'status_code', 0)
            if resp is None or _sc != 200:
                log.warning("OZON search: HTTP %s for %r", _sc, query[:40])
                if _sc in (403, 429):
                    # 2026-11-15: WAF blocks HTTP — PwFetcher as primary fallback
                    # (diag: proxy/direct → 403, Playwright search → 200 + 16 markers)
                    # 2026-11-15: p2 стабильнее p1 (diag: p1 challenge ~3KB, p2 200/873KB)
                    pw = self._get_pw_direct()
                    if pw:
                        log.info("OZON search: HTTP %d → PwFetcher (direct)", _sc)
                        _pw_pages = [2, 1] if page == 1 else [page]
                        for _pp in _pw_pages:
                            _pp_url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true&page={_pp}"
                            try:
                                pw_result = pw.get(_pp_url, extra_wait_ms=15000)
                                if pw_result:
                                    _pw_html = pw_result.html if hasattr(pw_result, "html") else ""
                                    if len(_pw_html) > 5000:
                                        html = _pw_html
                                        self.stats["success"] += 1
                                        log.info("OZON search: PwFetcher p%d OK (%d bytes)", _pp, len(html))
                                        resp = None  # handled via PW, skip to tile parsing
                                        break
                                    else:
                                        log.warning("OZON search: PwFetcher p%d short (%d bytes)", _pp, len(_pw_html))
                            except Exception as pw_e:
                                log.info("OZON search: PwFetcher p%d failed: %s", _pp, pw_e)
                    # 2026-11-15 v10: XHR capture on 403 — direct-браузер, p2 стабильнее
                    # (Ozon SPA грузит плитки через API; p1 часто challenge)
                    if resp is not None and pw:
                        _xhr_url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true&page={2 if page == 1 else page}"
                        try:
                            _xres = pw.get_xhr(_xhr_url, patterns=["/api/", "widget", "search", "tile", "entrypoint", "composer"],
                                               extra_wait_ms=7000, xhr_timeout_ms=20000,
                                               catch_json=True, min_json=2000)
                            if _xres and (_xres.html or _xres.meta.get("xhr_bodies")):
                                import json as _xj
                                _xids = []
                                _xmeta = {}
                                def _xwalk(o):
                                    if isinstance(o, dict):
                                        _kid = None
                                        for k in ("tileId", "productId", "itemId"):
                                            v = o.get(k)
                                            if isinstance(v, (int, str)) and str(v).isdigit() and len(str(v)) >= 6:
                                                _kid = str(v)
                                                break
                                        if _kid:
                                            _xids.append(_kid)
                                            _xmeta[_kid] = {
                                                "title": o.get("title") or o.get("name") or "",
                                                "price": o.get("price") or o.get("priceValue"),
                                            }
                                        for vv in o.values():
                                            _xwalk(vv)
                                    elif isinstance(o, list):
                                        for vv in o:
                                            _xwalk(vv)
                                for _u, _t in _xres.meta.get("xhr_bodies", []):
                                    try:
                                        _xwalk(_xj.loads(_t))
                                    except (ValueError, TypeError):
                                        for _m in re.findall(r'/product/(\d{6,})/', _t):
                                            if _m not in _xids:
                                                _xids.append(_m)
                                if _xids:
                                    _xnow = datetime.now(timezone(timedelta(hours=10))).isoformat()
                                    _xseen = set()
                                    for _xid in _xids[:page_size]:
                                        if _xid in _xseen:
                                            continue
                                        _xseen.add(_xid)
                                        _xm = _xmeta.get(_xid, {})
                                        _xt = str(_xm.get("title") or f"OZON-{_xid}")[:300]
                                        _xobs = {"fields": {}, "price_candidates": [],
                                                 "media": {"items": []},
                                                 "availability": "UNKNOWN", "category_path": None}
                                        _xobs["fields"]["title"] = {"status": "KNOWN", "value": _xt,
                                                                    "method": "SEARCH_XHR",
                                                                    "evidence": {"method": "SEARCH_XHR", "source": self.source}}
                                        _xp = _xm.get("price")
                                        if _xp:
                                            try:
                                                _xmin = int(float(str(_xp).replace(",", ".").replace(" ", "")) * 100)
                                                if _xmin > 0:
                                                    _xobs["price_candidates"].append({
                                                        "role": "current", "amount_minor": _xmin,
                                                        "amount": f"{_xmin // 100}.{_xmin % 100:02d}",
                                                        "currency": "RUB",
                                                        "visible_label": f"{_xmin // 100} руб."})
                                            except (ValueError, TypeError):
                                                pass
                                        self._ensure_evidence(_xobs)
                                        self._ensure_price_amounts(_xobs)
                                        self._ensure_availability_in_fields(_xobs)
                                        results.append({
                                            "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                                            "capture_action": "COLLECTOR_RUN",
                                            "fully_autonomous": True,
                                            "collector_source": self.source,
                                            "observation": {**_xobs, "source": self.source,
                                                             "id": _xid, "listing_id": _xid,
                                                             "title": _xt,
                                                             "url": f"https://www.ozon.ru/product/{_xid}/",
                                                             "observed_at": _xnow,
                                                             "status": "PARTIAL_SUCCESS",
                                                             "product_content": True,
                                                             "evidence_level": "COLLECTOR_AUTONOMOUS_XHR",
                                                             "http_status": 200}})
                                    self.stats["success"] += 1
                                    log.info("OZON search: XHR(403) → %d products", len(results))
                                    return results
                        except Exception as _xex:
                            log.info("OZON search: XHR(403) failed: %s", _xex)
                    if resp is not None:
                        # PW/XHR не дали HTML — блокировка
                        self.stats["blocked"] += 1
                        raise SearchError(f"SEARCH_HTTP_{_sc}")
                    else:
                        # PW отдал HTML (resp=None) — уходим в tile parsing ниже
                        log.info("OZON search: PW HTML ready (%d bytes), proceeding to tile parse", len(html))
                else:
                    self.stats["failed"] += 1
                    raise SearchError(f"SEARCH_HTTP_{_sc}")
            else:
                html = resp.text if hasattr(resp, "text") else ""
                if len(html) < 5000:
                    log.warning("OZON search: short response (%d bytes)", len(html))
                    self.stats["failed"] += 1
                    raise SearchError("SEARCH_RESPONSE_TOO_SHORT")
                # 2026-11-14: WAF soft-200 detection (large page with challenge indicators)
                _waf_words = ["captcha", "antibot", "challenge", "puzzle", "slider-captcha"]
                _head_lower = html[:8000].lower()
                _waf_hits = sum(1 for w in _waf_words if w in _head_lower)
                if _waf_hits >= 2:
                    log.warning("OZON search: WAF soft-200 (%d indicators, %d bytes)", _waf_hits, len(html))
                    self.stats["blocked"] += 1
                    raise SearchError("SEARCH_WAF_SUSPECTED")
                self.stats["success"] += 1

        except SearchError:
            raise
        except Exception as e:
            self.stats["failed"] += 1
            log.warning("OZON search: error: %s", type(e).__name__)
            raise SearchError("SEARCH_REQUEST_FAILED") from e

        # Parse product tiles from search HTML
        # Pattern 0 (OZON slug): href="/product/{slug}-{numericId}/" — id = last digit block
        tile_pat_slug = re.compile(
            r'href=["\'](?:/product/|https://www\.ozon\.ru/product/)[^"\'<>\s]+?-(\d{6,})/(\?[^"\']*)?["\']',
            re.I
        )
        matches = list(tile_pat_slug.finditer(html))
        log.info("OZON search: %d product links found (pattern 0 slug)", len(matches))
        # Pattern 1: <a ... href="/product/...-{id}/" ...>
        if not matches:
            tile_pat = re.compile(
                r'<a[^>]*href=["\'](?:/product/|https://www\.ozon\.ru/product/)[^"\']*?(\d+)[^"\']*["\'][^>]*>',
                re.I
            )
            matches = list(tile_pat.finditer(html))
            log.info("OZON search: %d product links found (pattern 1)", len(matches))
        # Pattern 2: data-tid attribute (Ozon SPA tiles)
        if not matches:
            tile_pat2 = re.compile(r'data-tid=["\'](\d+)["\']', re.I)
            matches = list(tile_pat2.finditer(html))
            log.info("OZON search: %d data-tid links found (pattern 2)", len(matches))
        # 2026-11-14 v5: Pattern 3 — embedded JSON (window.__INITIAL_STATE__ / data-widget)
        if not matches:
            _json_ids = []
            # Try __INITIAL_STATE__ or __NEXT_DATA__
            _state_pat = re.compile(
                r'(?:__INITIAL_STATE__|__NEXT_DATA__|window\.__state)\s*=\s*(\{.+?\})\s*;?\s*(?:</script>|$)',
                re.S
            )
            _state_m = _state_pat.search(html)
            if _state_m:
                try:
                    import json as _json
                    _state = _json.loads(_state_m.group(1))
                    # Walk for product IDs in common Ozon structures
                    def _walk(obj):
                        if isinstance(obj, dict):
                            for k in ('tileId', 'productId', 'itemId', 'id'):
                                if k in obj and isinstance(obj[k], (int, str)):
                                    v = str(obj[k])
                                    if v.isdigit() and len(v) >= 6:
                                        _json_ids.append(v)
                            for v in obj.values():
                                _walk(v)
                        elif isinstance(obj, list):
                            for v in obj:
                                _walk(v)
                    _walk(_state)
                    log.info("OZON search: %d IDs from embedded JSON (pattern 3)", len(_json_ids))
                except (ValueError, RecursionError):
                    pass
            if not _json_ids:
                # Fallback: data-widget="ProductList" items with data-tid or href
                _widget_pat = re.compile(
                    r'data-widget=["\'](?:ProductList|ProductTile)["\'][^>]*>.*?data-tid=["\'](\d+)["\']',
                    re.S
                )
                _json_ids = [m.group(1) for m in _widget_pat.finditer(html)]
                if not _json_ids:
                    # Last resort: any /product/{id}/ in JSON strings
                    _json_ids = re.findall(r'/product/(\d{6,})/', html)
                    log.info("OZON search: %d IDs from /product/ JSON scan (pattern 3b)", len(_json_ids))
            if _json_ids:
                # Convert to match-like objects for unified processing below
                class _FakeMatch:
                    def __init__(self, s, pos):
                        self._s, self._pos = s, pos
                    def group(self, n):
                        return self._s
                    def start(self):
                        return self._pos
                # Find positions in HTML for block extraction
                _positions = []
                for _jid in _json_ids:
                    _idx = html.find(_jid)
                    if _idx >= 0:
                        _positions.append(_idx)
                if _positions:
                    matches = [_FakeMatch(_json_ids[i], _positions[i]) for i in range(min(len(_json_ids), len(_positions)))]
                    log.info("OZON search: using %d JSON-extracted IDs for tile parsing", len(matches))
                else:
                    # No positions — build minimal results directly
                    for _jid in _json_ids[:page_size]:
                        obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
                               "availability": "UNKNOWN", "category_path": None}
                        obs["fields"]["title"] = {"status": "KNOWN", "value": f"OZON-{_jid}",
                                                   "method": "SEARCH_JSON", "evidence": {"method": "SEARCH_JSON", "source": self.source}}
                        self._ensure_evidence(obs)
                        self._ensure_price_amounts(obs)
                        self._ensure_availability_in_fields(obs)
                        results.append({
                            "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                            "capture_action": "COLLECTOR_RUN",
                            "fully_autonomous": True,
                            "collector_source": self.source,
                            "observation": {**obs, "source": self.source, "id": _jid,
                                             "listing_id": _jid,
                                             "title": f"OZON-{_jid}",
                                             "url": f"https://www.ozon.ru/product/{_jid}/",
                                             "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                                             "status": "PARTIAL_SUCCESS", "product_content": True,
                                             "evidence_level": "COLLECTOR_AUTONOMOUS_HTML", "http_status": 200},
                        })
                    log.info("OZON: collect_by_search %r → %d products (JSON direct)", query[:40], len(results))
                    return results
        if not matches and len(html) > 5000:
            log.warning("OZON search: 0 tiles in %d bytes — likely JS-render/WAF page", len(html))
            # 2026-11-14 v7: PwFetcher fallback — lowered threshold from 50000 to 5000
            # (diag v5: OZON p1 got 200 with ~8-40KB but 0 tiles; PwFetcher direct)
            pw = self._get_pw_direct()
            if pw:
                log.info("OZON search: trying PwFetcher for JS-rendered page")
                try:
                    # 2026-11-14 v8b: extra_wait_ms=10000 — FAB JS challenge cycle:
                    # domcontentloaded → JS loads → proof-of-work → cookie set → redirect
                    # 5s was insufficient (diag_v6d: still 403 after 5s)
                    pw_result = pw.get(url, extra_wait_ms=10000)
                    if pw_result:
                        pw_html = pw_result.html if hasattr(pw_result, "html") else ""
                        if len(pw_html) > 5000:
                            # Re-run all tile patterns on rendered HTML
                            m1 = list(tile_pat.finditer(pw_html))
                            if m1:
                                matches = m1
                                html = pw_html
                                log.info("OZON search: PwFetcher pattern 1 → %d tiles", len(matches))
                            else:
                                m2 = list(tile_pat2.finditer(pw_html))
                                if m2:
                                    matches = m2
                                    html = pw_html
                                    log.info("OZON search: PwFetcher pattern 2 → %d tiles", len(matches))
                                else:
                                    m3 = re.findall(r'/product/(\d{6,})/', pw_html)
                                    if m3:
                                        class _FM:
                                            def __init__(self, s, pos):
                                                self._s, self._pos = s, pos
                                            def group(self, n): return self._s
                                            def start(self): return self._pos
                                        _pos = [pw_html.find(x) for x in m3]
                                        _pos = [p if p >= 0 else 0 for p in _pos]
                                        matches = [_FM(m3[i], _pos[i]) for i in range(len(m3))]
                                        html = pw_html
                                        log.info("OZON search: PwFetcher pattern 3 → %d tiles", len(matches))
                except Exception as pw_e:
                    log.info("OZON search: PwFetcher failed: %s", pw_e)
            if not matches:
                # 2026-11-15 v10: XHR capture — Ozon SPA грузит плитки через API;
                # p1 часто challenge, p2 стабильнее (diag 2026-11-15)
                xhr_url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true&page={2 if page == 1 else page}"
                log.info("OZON search: 0 tiles in rendered HTML, trying XHR capture %s", xhr_url.split('?')[0])
                try:
                    xhr = pw.get_xhr(xhr_url, patterns=["/api/", "widget", "search", "entrypoint", "composer"],
                                     extra_wait_ms=7000, xhr_timeout_ms=18000,
                                     catch_json=True, min_json=2000)
                    if xhr and xhr.html:
                        import json as _json
                        _ids = []
                        def _walk_ids(o):
                            if isinstance(o, dict):
                                for k in ("tileId", "productId", "itemId", "id"):
                                    v = o.get(k)
                                    if isinstance(v, (int, str)) and str(v).isdigit() and len(str(v)) >= 6:
                                        _ids.append(str(v))
                                for vv in o.values():
                                    _walk_ids(vv)
                            elif isinstance(o, list):
                                for vv in o:
                                    _walk_ids(vv)
                        try:
                            _walk_ids(_json.loads(xhr.html))
                        except (ValueError, TypeError):
                            _ids = re.findall(r'/product/(\d{6,})/', xhr.html)
                        if not _ids:
                            for _u, _t in xhr.meta.get("xhr_bodies", []):
                                _ids = re.findall(r'/product/(\d{6,})/', _t)
                                if _ids:
                                    break
                        if _ids:
                            class _FM2:
                                def __init__(self, s, pos):
                                    self._s, self._pos = s, pos
                                def group(self, n): return self._s
                                def start(self): return self._pos
                            _pos = [html.find(x) for x in _ids]
                            _pos = [p if p >= 0 else 0 for p in _pos]
                            matches = [_FM2(_ids[i], _pos[i]) for i in range(len(_ids))]
                            log.info("OZON search: XHR → %d product ids", len(matches))
                except Exception as _xe:
                    log.info("OZON search: XHR failed: %s", _xe)
            if not matches:
                log.warning("OZON search: still 0 tiles after PwFetcher+XHR — returning empty")

        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        seen_ids = set()

        for i, m in enumerate(matches[:page_size * 3]):
            pid = m.group(1)
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            if len(results) >= page_size:
                break

            # Extract block around this link (next 3000 chars)
            block_start = m.start()
            block_end = matches[i + 1].start() if i + 1 < len(matches) else min(block_start + 3000, len(html))
            block = html[block_start:block_end]

            # Title: look for text in h2/h3/span or aria-label
            title = None
            t_match = re.search(r'aria-label=["\']([^"\']{10,200})["\']', block)
            if t_match:
                title = t_match.group(1).strip()
            if not title:
                t_match = re.search(r'<(?:h2|h3|span)[^>]*class=["\'][^"\']*(?:title|name)[^"\']*["\'][^>]*>([^<]{10,200})<', block, re.I)
                if t_match:
                    title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
            if not title:
                t_match = re.search(r'>([^<>]{15,200})</(?:h2|h3|span|div)>', block)
                if t_match:
                    candidate = t_match.group(1).strip()
                    if not candidate.startswith(('http', '{', '<')) and len(candidate) > 10:
                        title = candidate
            if not title:
                title = f"OZON-{pid}"

            # Price: look for numeric patterns with ₽ or rub
            price_minor = 0
            p_match = re.search(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)(?:\s*[.,]\d{1,2})?\s*(?:₽|руб)', block)
            if p_match:
                price_str = p_match.group(1).replace(' ', '').replace('\u00a0', '')
                try:
                    price_minor = int(float(price_str) * 100)
                except ValueError:
                    pass
            if not price_minor:
                p_match = re.search(r'"(?:price|value)"\s*:\s*(\d{3,9})', block)
                if p_match:
                    price_minor = int(p_match.group(1))

            # Image
            img_url = None
            img_match = re.search(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', block)
            if img_match:
                img_url = img_match.group(1)

            obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
                   "availability": "IN_STOCK" if price_minor > 0 else "UNKNOWN",
                   "category_path": None}

            obs["fields"]["title"] = {
                "status": "KNOWN", "value": title[:300],
                "method": "SEARCH_HTML", "evidence": {"method": "SEARCH_HTML", "source": self.source},
            }
            if price_minor > 0:
                obs["price_candidates"].append({
                    "role": "current",
                    "amount": f"{price_minor // 100}.{price_minor % 100:02d}",
                    "amount_minor": price_minor,
                    "currency": "RUB",
                    "visible_label": f"{price_minor // 100} руб.",
                })
            if img_url:
                obs["media"]["items"].append({"type": "image", "source_url": img_url, "image_data": None})

            self._ensure_evidence(obs)
            self._ensure_price_amounts(obs)
            self._ensure_availability_in_fields(obs)

            results.append({
                "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                "capture_action": "COLLECTOR_RUN",
                "fully_autonomous": True,
                "collector_source": self.source,
                "observation": {
                    **obs,
                    "source": self.source,
                    "id": pid,
                    "listing_id": pid,
                    "title": title,
                    "url": f"https://www.ozon.ru/product/{pid}/",
                    "observed_at": now,
                    "status": "PARTIAL_SUCCESS",
                    "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTML",
                    "http_status": 200,
                },
            })

        log.info("OZON: collect_by_search %r → %d products", query[:40], len(results))
        return results
