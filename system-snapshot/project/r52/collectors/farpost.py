"""Сборщик Farpost (farpost.ru).

Стратегия парсинга (приоритет从高到低):
1. JSON-LD (schema.org/Product / Offer / BreadcrumbList) — структурированные данные.
2. Inline JS: baza.lightbox.init(items) — массив фото карточки.
3. HTML: data-атрибуты (data-bulletin-price, data-field), CSS-классы.
4. Open Graph / meta-теги.
5. <img> с CDN static.baza.farpost.ru.

Медиа:
- Фото: CDN static.baza.farpost.ru, суффиксы _bulletin (полн.) / _default (сред.)
- Видео: mp4/webm (редко), video-теги, data-src в скриптах

Особенности Farpost:
- URL: https://www.farpost.ru/{city}/{category}/{slug}-{numeric_id}.html
- Характеристики: div.field.viewbull-field__container > div.label + div.value
- Цена: span[data-bulletin-price] или JSON-LD offers.price
- Описание: div.bulletinText > p[data-field="text"]
- Продавец: div.company-profile, a[href*="/user/"]
"""

import json
import logging
import random
import re
import time
from typing import Optional
from urllib.parse import urlsplit, quote

from .base import BaseCollector
from .discovery import SearchError

log = logging.getLogger("r52.collectors.farpost")

FARPOST_HOSTS = ("farpost.ru", "www.farpost.ru")
FARPOST_CDN_HOSTS = ("static.baza.farpost.ru", "img.farpost.ru", "farpost-ads.ru")
FARPOST_CDN_RE = re.compile(r"https?://(?:static\.baza\.)?farpost\.ru/", re.I)

# Суффиксы размеров фото на CDN
CDN_SUFFIXES = ("_bulletin", "_default", "_thumbnail120", "_thumbnail50", "_large")

# Шумовые URL (не фото товара)
NOISE_PATTERNS = re.compile(
    r"(personal/bookmark|get-application-link|farpost_company/.*_thumbnail|farpost_catalog/.*_default"
    r"|#|\.svg|\.gif|\.ico|\.css|\.js|counter\.|resources/|favicon)",
    re.I,
)

# Паттерн для lightbox.init(items)
LIGHTBOX_RE = re.compile(
    r"baza\.lightbox\.init\(\s*(\[[\s\S]*?\])\s*\)",
)

# Паттерн для data-bulletin-price
PRICE_ATTR_RE = re.compile(r'data-bulletin-price=["\'](\d+)["\']')

# Паттерн для характеристик: div.field.viewbull-field__container
SPEC_BLOCK_RE = re.compile(
    r'<div\s+class="field\s+viewbull-field__container[^"]*"[^>]*>\s*'
    r'<div\s+class="label">([^<]+)</div>\s*'
    r'<div\s+class="value">\s*'
    r'<span\s+class="inplace"(?:\s+data-field="([^"]*)")?[^>]*>\s*'
    r'(.*?)\s*</span>',
    re.DOTALL,
)

# Паттерн для описания
DESC_RE = re.compile(
    r'<div\s+class="bulletinText[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)

# Паттерн для модели/названия
MODEL_RE = re.compile(
    r'data-field="model~[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)

# Паттерн для JSON-LD
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

# Паттерн для видео
VIDEO_RE = re.compile(
    r'(?:src|data-src|data-url|data-video)=["\']([^"\']*\.(?:mp4|webm|ogg)(?:\?[^"\']*)?)["\']',
    re.I,
)
VIDEO_TAG_RE = re.compile(
    r'<video[^>]*>(.*?)</video>',
    re.DOTALL,
)

# Паттерн для seller
SELLER_RE = re.compile(
    r'<span\s+class="userNick[^"]*"[^>]*>\s*<a\s+href="(/user/[^/]+/?)">(.*?)</a>',
    re.DOTALL,
)


class FarpostCollector(BaseCollector):
    source = "FARPOST"
    title = "Farpost"

    def __init__(self, data_dir=None, use_proxies: bool = True):
        super().__init__(data_dir=data_dir, use_proxies=use_proxies)
        self._cffi = None
        try:
            from curl_cffi import requests as cffi_req
            self._cffi = cffi_req
            log.info("FARPOST: curl_cffi доступен (impersonate=chrome)")
        except ImportError:
            log.info("FARPOST: curl_cffi недоступен, fallback на requests")
        self._warmed_up = False

    def _warmup(self):
        """Одноразовый warm-up: GET главной для cookie ring/ring_session."""
        if self._warmed_up:
            return
        try:
            proxies = self.pool.get_proxy()
            if proxies is None and self.pool.use_proxies:
                log.info("FARPOST: warmup FAIL_CLOSED — прокси нет, skip (не direct)")
                self._warmed_up = True
                return
            resp = self.session.get("https://www.farpost.ru/", timeout=(10, 30),
                                    proxies=proxies)
            if resp.status_code == 200:
                self._warmed_up = True
                log.info("FARPOST: warm-up OK, cookies=%s", list(self.session.cookies.keys()))
            else:
                log.warning("FARPOST: warm-up HTTP %d", resp.status_code)
        except Exception as e:
            log.warning("FARPOST: warm-up error: %s", e)
        time.sleep(1)

    def fetch(self, url: str):
        """GET с warm-up перед первым запросом + retry при /verify soft-block."""
        self._warmup()
        resp = super().fetch(url)
        # Farpost soft-block: 302 → /verify?u=... (не CAPTCHA, а cookie-проверка)
        if resp is not None and "/verify" in (resp.url or ""):
            log.info("FARPOST: /verify page (resp.url=%s), retry in 5s", resp.url)
            time.sleep(5)
            self._last_request = time.monotonic()  # bypass throttle for retry
            resp2 = super().fetch(url)
            if resp2 is not None and "/verify" not in (resp2.url or ""):
                log.info("FARPOST: retry OK, url=%s", resp2.url)
                return resp2
            log.warning("FARPOST: retry still /verify or None")
            return resp2 or resp
        return resp

    def build_url(self, listing_id: str) -> str:
        """Строит URL карточки Farpost.

        Поддерживаемые форматы listing_id:
        - Числовой: 139122082 → /vladivostok/139122082.html (универсальный редирект)
        - Полный путь: /krasnoyarsk/auto/wheel/tire/shiny-139122082.html
        - Slug: shiny-139122082 → /vladivostok/139122082.html
        - Полный URL: https://www.farpost.ru/...

        Примечание: /vladivostok/{id}.html работает как универсальный вход
        и редиректит на городскую страницу. /bulletin/{id} даёт 404.
        """
        lid = listing_id.strip()

        # Уже полный URL
        if lid.startswith("http"):
            return lid

        # Полный путь (уже содержит город)
        if lid.startswith("/"):
            return f"https://www.farpost.ru{lid}"

        # Числовой ID или slug с числом на конце
        if lid.isdigit():
            return f"https://www.farpost.ru/vladivostok/{lid}.html"

        # Slug: shiny-139122082 → извлекаем число
        m = re.search(r'(\d+)$', lid)
        if m:
            return f"https://www.farpost.ru/vladivostok/{m.group(1)}.html"

        # Fallback
        return f"https://www.farpost.ru/vladivostok/{lid}.html"

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        """Farpost: GET с заголовками → parse_page → обёртка."""
        from datetime import datetime, timezone, timedelta
        self._throttle()
        proxies = self.pool.get_proxy()
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.farpost.ru/",
        }
        try:
            self._warmup()
            resp = self.session.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
            self.stats["requests"] += 1
            if resp.status_code != 200:
                log.info("FARPOST %s: HTTP %d", listing_id, resp.status_code)
                return None
            self.stats["success"] += 1
        except Exception as e:
            log.warning("FARPOST %s: fetch error: %s", listing_id, e)
            return None

        try:
            observation = self.parse_page(resp, listing_id, url, title_hint)
        except Exception as e:
            log.exception("FARPOST %s: parse error: %s", listing_id, e)
            return None
        if observation is None:
            return None

        self._ensure_evidence(observation)
        self._ensure_price_amounts(observation)
        self._ensure_availability_in_fields(observation)

        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        has_title = bool(observation.get("fields", {}).get("title", {}).get("value", ""))
        has_price = len(observation.get("price_candidates", [])) > 0
        has_media = len(observation.get("media", {}).get("items", [])) > 0
        if has_title and has_price and has_media:
            status = "FULL_SUCCESS"
        elif has_title and has_price:
            status = "PARTIAL_SUCCESS"
        else:
            status = "DEGRADED"
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

    def parse_page(self, resp, listing_id, url, title_hint):
        # Farpost использует windows-1251; resp.text может декодировать как ISO-8859-1
        ct = (resp.headers.get("Content-Type") or "").lower()
        if "windows-1251" in ct or "cp1251" in ct or "1251" in ct:
            html = resp.content.decode("windows-1251", errors="replace")
        else:
            # Пробуем определить: если есть meta charset=windows-1251 в первых 500 байтах
            raw_head = resp.content[:500].decode("ascii", errors="ignore")
            if "windows-1251" in raw_head.lower() or "cp1251" in raw_head.lower():
                html = resp.content.decode("windows-1251", errors="replace")
            else:
                html = resp.text
        if not html or len(html) < 500:
            log.warning("FARPOST %s: короткий ответ (%d байт)", listing_id, len(html or ""))
            return None

        # 404 / "Страница не найдена" — не товар
        if 'Страница не найдена' in html[:500] or '<title>Страница не найдена</title>' in html:
            log.warning("FARPOST %s: 404-страница (Страница не найдена, len=%d)", listing_id, len(html))
            return None

        # --- Извлечение всех источников данных ---
        jsonld_blocks = self._extract_json_ld_blocks(html)
        product_ld = self._find_product_jsonld(jsonld_blocks)
        breadcrumb_ld = self._find_breadcrumb_jsonld(jsonld_blocks)
        lightbox_items = self._extract_lightbox_items(html)
        gallery_items = self._extract_gallery_info(html)
        og_data = self._extract_og(html)

        fields = {}
        price_candidates = []
        availability = "UNKNOWN"

        # === TITLE ===
        title = None
        if product_ld:
            title = product_ld.get("name") or product_ld.get("title")
        if not title:
            m = re.search(
                r'<h1[^>]*class="[^"]*subject[^"]*"[^>]*>.*?'
                r'<span[^>]*data-field="subject"[^>]*>(.*?)</span>',
                html, re.DOTALL,
            )
            if m:
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if not title and og_data:
            title = og_data.get("title")
        if not title:
            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            if m:
                title = m.group(1).replace(" - Купить", "").replace(" на Фарпост", "").replace(" - Farpost", "").replace(" - Фарпост", "").strip()
        if not title:
            title = title_hint or f"FARPOST-{listing_id}"

        fields["title"] = {
            "status": "KNOWN",
            "value": title,
            "method": "JSONLD" if product_ld else "HTML",
        }

        # === PRICE ===
        # 1. data-bulletin-price (самый надёжный)
        m = PRICE_ATTR_RE.search(html)
        if m:
            try:
                amount = int(m.group(1))
                if amount > 0:
                    price_candidates.append({
                        "role": "regular",
                        "amount_minor": amount * 100,
                        "currency": "RUB",
                        "label": "data-bulletin-price",
                    })
            except ValueError:
                pass

        # 2. JSON-LD offers
        if product_ld:
            offers = product_ld.get("offers")
            if isinstance(offers, dict):
                offers = [offers]
            if isinstance(offers, list):
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    price_val = offer.get("price") or offer.get("lowPrice")
                    currency = offer.get("priceCurrency", "RUB")
                    if price_val:
                        try:
                            minor = int(round(float(price_val) * 100))
                            price_candidates.append({
                                "role": "regular",
                                "amount_minor": minor,
                                "currency": currency,
                                "label": "JSON-LD price",
                            })
                        except (ValueError, TypeError):
                            pass
                    avail = offer.get("availability", "")
                    if "IN_STOCK" in avail.upper():
                        availability = "IN_STOCK"
                    elif "OUT_OF_STOCK" in avail.upper():
                        availability = "OUT_OF_STOCK"

        # 3. HTML fallback: viewbull-summary-price__value
        if not price_candidates:
            m = re.search(
                r'class="viewbull-summary-price__value[^"]*"[^>]*data-field="price"[^>]*>([\d\s\u00a0]+)',
                html,
            )
            if m:
                try:
                    amount = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                    price_candidates.append({
                        "role": "regular",
                        "amount_minor": int(amount * 100),
                        "currency": "RUB",
                        "label": "HTML price",
                    })
                except ValueError:
                    pass

        # 4. og:price:amount
        if not price_candidates:
            m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                try:
                    minor = int(round(float(m.group(1)) * 100))
                    if minor > 0:
                        price_candidates.append({
                            "role": "regular",
                            "amount_minor": minor,
                            "currency": "RUB",
                            "label": "og:price",
                        })
                except (ValueError, TypeError):
                    pass

        # 5. Generic class="price" text (1 500 ₽)
        if not price_candidates:
            m = re.search(r'class="price[^"]*"[^>]*>([\d\s\u00a0]+)\s*(?:₽|руб)', html, re.I)
            if m:
                try:
                    amount = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                    if amount > 0:
                        price_candidates.append({
                            "role": "regular",
                            "amount_minor": int(amount * 100),
                            "currency": "RUB",
                            "label": "HTML price div",
                        })
                except (ValueError, TypeError):
                    pass

        price_candidates = self._dedupe_prices(price_candidates)

        # === MEDIA (фото + видео) ===
        media_items = self._extract_media_full(html, gallery_items, lightbox_items, product_ld, og_data)

        # === DESCRIPTION ===
        description = None
        if product_ld:
            description = product_ld.get("description")
        if not description:
            m = DESC_RE.search(html)
            if m:
                text = re.sub(r'<[^>]+>', ' ', m.group(1))
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 10:
                    description = text
        if not description and og_data:
            description = og_data.get("description")
        if description:
            fields["description"] = {
                "status": "KNOWN",
                "value": description[:3000],
                "method": "JSONLD" if product_ld else "HTML",
            }

        # === CATEGORY ===
        category = None
        if breadcrumb_ld:
            items = breadcrumb_ld.get("itemListElement", [])
            names = [it.get("name", "") for it in items if isinstance(it, dict) and it.get("name")]
            if len(names) >= 2:
                category = " > ".join(names[1:-1])  # без города и товара
        if not category:
            m = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                path = urlsplit(m.group(1)).path
                parts = [p for p in path.split("/") if p and not p.isdigit()]
                # Убираем slug (последний, содержит дефис и число)
                parts = [p for p in parts if not re.search(r'\d{6,}$', p)]
                if len(parts) >= 2:
                    category = " > ".join(parts[:4])
        if category:
            fields["category"] = {
                "status": "KNOWN",
                "value": category,
                "method": "BREADCRUMB" if breadcrumb_ld else "URL",
            }

        # === BRAND / MODEL ===
        brand = None
        model = None
        if product_ld:
            b = product_ld.get("brand")
            if isinstance(b, dict):
                brand = b.get("name")
            elif isinstance(b, str):
                brand = b
            model = product_ld.get("sku") or product_ld.get("mpn")
        if not model:
            m = MODEL_RE.search(html)
            if m:
                model = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if brand:
            fields["brand"] = {
                "status": "KNOWN",
                "value": str(brand),
                "method": "JSONLD" if product_ld else "HTML",
            }
        if model:
            fields["model"] = {
                "status": "KNOWN",
                "value": str(model),
                "method": "JSONLD" if product_ld else "HTML",
            }

        # === SPECS (характеристики) ===
        specs = self._extract_specs(html)
        if specs:
            fields["specs"] = {
                "status": "KNOWN",
                "value": specs,
                "method": "HTML",
            }

        # === SELLER ===
        seller_name = None
        seller_url = None
        m = SELLER_RE.search(html)
        if m:
            seller_url = f"https://www.farpost.ru{m.group(1)}"
            seller_name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if seller_name:
            fields["seller"] = {
                "status": "KNOWN",
                "value": seller_name,
                "method": "HTML",
            }
            if seller_url:
                fields["seller_url"] = {
                    "status": "KNOWN",
                    "value": seller_url,
                    "method": "HTML",
                }

        # === AVAILABILITY ===
        if availability == "UNKNOWN":
            # data-field="goodPresentState"
            m = re.search(r'data-field="goodPresentState"[^>]*>\s*([^<]+)', html)
            if m:
                val = m.group(1).strip()
                if "в наличии" in val.lower() or "активно" in val.lower():
                    availability = "IN_STOCK"
                elif "нет" in val.lower() or "архив" in val.lower():
                    availability = "OUT_OF_STOCK"
            if availability == "UNKNOWN":
                if re.search(r"в наличии|доступен|активно|продаётся|купить", html, re.I):
                    availability = "IN_STOCK"
                elif re.search(r"нет в наличии|закончился|распродан|архив|завершено", html, re.I):
                    availability = "OUT_OF_STOCK"

        # === BULLETIN ID ===
        m = re.search(r'bulletinId=["\'](\d+)["\']', html)
        if m:
            fields["bulletin_id"] = {
                "status": "KNOWN",
                "value": m.group(1),
                "method": "HTML",
            }

        # === SOFT-BLOCK DETECTION ===
        # /verify page: generic title, no price, no media, no bulletin_id
        title_val = (fields.get("title", {}).get("value") or "").strip().lower()
        is_verify = (
            title_val in ("фарпост - доска объявлений", "фарпост", "farpost")
            and not price_candidates
            and not media_items
            and "bulletin_id" not in fields
        )
        if is_verify:
            log.warning("FARPOST %s: /verify soft-block detected (generic title, no data)", listing_id)
            return None

        return {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": availability,
            "category_path": category,
        }

    # ==================== МЕТОДЫ ИЗВЛЕЧЕНИЯ ====================

    def _extract_json_ld_blocks(self, html: str) -> list:
        """Извлекает все JSON-LD блоки."""
        blocks = []
        for m in JSONLD_RE.finditer(html):
            raw = m.group(1).strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    blocks.append(data)
                elif isinstance(data, list):
                    blocks.extend(b for b in data if isinstance(b, dict))
            except (json.JSONDecodeError, ValueError):
                continue
        return blocks

    def _find_product_jsonld(self, blocks: list) -> Optional[dict]:
        for b in blocks:
            t = b.get("@type", "")
            if t == "Product":
                return b
        return None

    def _find_breadcrumb_jsonld(self, blocks: list) -> Optional[dict]:
        for b in blocks:
            t = b.get("@type", "")
            if t == "BreadcrumbList":
                return b
        return None

    def _extract_lightbox_items(self, html: str) -> list:
        """Извлекает массив фото из baza.lightbox.init(items)."""
        items = []
        for m in LIGHTBOX_RE.finditer(html):
            raw = m.group(1)
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            src = entry.get("src") or entry.get("url") or ""
                            alt = entry.get("alt", "")
                            if src:
                                items.append({"src": src, "alt": alt})
            except (json.JSONDecodeError, ValueError):
                continue
        return items

    @staticmethod
    def _canonical_photo_key(url: str) -> str:
        """Канонический ключ для дедупликации фото Farpost CDN.

        Убирает query-параметры и суффикс размера, оставляя базовый путь /v/{id}.
        """
        url = url.split("?")[0].strip()
        for suffix in ("_default", "_thumbnail120", "_thumbnail50", "_large", "_bulletin"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
                break
        return url

    def _extract_gallery_info(self, html: str) -> list:
        """Извлекает структурированные данные фото из data-image-info атрибутов.

        Каждый <img> в галерее несёт JSON с msrc (полн. размер), src (сред.),
        alt, label, isMain, width, height — самый полный источник.
        """
        items = []
        for m in re.finditer(
            r'data-image-info="(\{.*?\})"',
            html,
        ):
            raw = m.group(1)
            raw = (
                raw.replace("&quot;", '"')
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
            )
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    continue
                msrc = data.get("msrc", "")
                src = data.get("src", "")
                url = msrc or src
                if not url:
                    continue
                items.append({
                    "url": url,
                    "alt": data.get("alt", ""),
                    "label": data.get("label", ""),
                    "is_main": data.get("isMain", False),
                    "width": data.get("width", 0),
                    "height": data.get("height", 0),
                })
            except (json.JSONDecodeError, ValueError):
                continue
        return items

    def _extract_og(self, html: str) -> dict:
        """Извлекает Open Graph метаданные."""
        og = {}
        for m in re.finditer(
            r'<meta[^>]*property=["\']og:(\w+)(?::\w+)?["\'][^>]*content=["\']([^"\']*)["\']',
            html, re.I,
        ):
            key = m.group(1)
            val = m.group(2)
            if key not in og:
                og[key] = val
        return og

    def _extract_media_full(
        self,
        html: str,
        gallery_items: list,
        lightbox_items: list,
        product_ld: Optional[dict],
        og_data: dict,
    ) -> list:
        """Полное извлечение медиа: фото и видео.

        Приоритет источников:
        1. data-image-info (структурированный JSON в галерее)
        2. JSON-LD Product.image
        3. lightbox.init (inline JS)
        4. og:image
        5. <img> с CDN
        6. data-src / data-original
        7. Видео
        """
        items = []
        seen_keys = set()

        # 1. Галерея data-image-info — самый полный источник
        for gi in gallery_items:
            self._add_photo(items, seen_keys, gi["url"], meta=gi)

        # 2. JSON-LD Product.image
        if product_ld:
            img = product_ld.get("image")
            if isinstance(img, str):
                self._add_photo(items, seen_keys, img)
            elif isinstance(img, list):
                for u in img:
                    if isinstance(u, str):
                        self._add_photo(items, seen_keys, u)
                    elif isinstance(u, dict):
                        self._add_photo(items, seen_keys, u.get("url", ""))

        # 3. lightbox.init (inline JS)
        for entry in lightbox_items:
            src = entry.get("src", "").replace("\\/", "/")
            if src:
                self._add_photo(items, seen_keys, src)

        # 4. og:image
        if og_data:
            for key in ("image", "image:secure_url"):
                url = og_data.get(key, "")
                if url:
                    self._add_photo(items, seen_keys, url)

        # 5. <img> с CDN
        for m in re.finditer(
            r'<img[^>]*(?:src|data-src|data-original)=["\']([^"\']+)["\']',
            html, re.I,
        ):
            src = m.group(1)
            if self._is_product_photo(src):
                self._add_photo(items, seen_keys, src)

        # 6. data-src / data-original (lazy loading)
        for m in re.finditer(
            r'(?:data-src|data-original|data-lazy-src)=["\']([^"\']*baza\.farpost\.ru[^"\']*)["\']',
            html, re.I,
        ):
            self._add_photo(items, seen_keys, m.group(1))

        # 7. Видео
        for m in VIDEO_RE.finditer(html):
            self._add_video(items, seen_keys, m.group(1))
        for m in VIDEO_TAG_RE.finditer(html):
            block = m.group(1)
            for vm in re.finditer(r'<source[^>]*src=["\']([^"\']+)["\']', block, re.I):
                self._add_video(items, seen_keys, vm.group(1))
            for vm in re.finditer(r'<video[^>]*src=["\']([^"\']+)["\']', block, re.I):
                self._add_video(items, seen_keys, vm.group(1))

        return items[:50]

    def _is_product_photo(self, url: str) -> bool:
        """Проверяет, является ли URL фото товара (не аватар, не иконка)."""
        if not url or url.startswith("data:"):
            return False
        # Должен содержать CDN farpost
        if "baza.farpost.ru" not in url and "farpost.ru" not in url:
            return False
        # Фильтруем шум
        if NOISE_PATTERNS.search(url):
            return False
        # Должен выглядеть как фото (суффикс, путь /v/ или /upload/)
        if "/v/" in url or "/upload/" in url or any(s in url for s in CDN_SUFFIXES):
            return True
        return False

    def _add_photo(self, items: list, seen: set, url: str, meta: dict = None):
        if not url:
            return
        # Нормализуем
        url = url.replace("\\/", "/").strip()
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            url = f"https://www.farpost.ru{url}"
        # Канонический ключ для дедупа
        key = self._canonical_photo_key(url)
        if key in seen:
            return
        # Фильтруем шум
        if NOISE_PATTERNS.search(url):
            return
        # Проверяем CDN
        if not self._is_product_photo(url):
            return
        seen.add(key)
        entry = {
            "type": "image",
            "source_url": url,
            "image_data": None,
        }
        if meta:
            entry["alt"] = meta.get("alt", "")
            entry["label"] = meta.get("label", "")
            entry["is_main"] = meta.get("is_main", False)
            entry["width"] = meta.get("width", 0)
            entry["height"] = meta.get("height", 0)
        items.append(entry)

    def _add_video(self, items: list, seen: set, url: str):
        if not url or url in seen:
            return
        url = url.replace("\\/", "/").strip()
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            url = f"https://www.farpost.ru{url}"
        seen.add(url)
        items.append({
            "type": "video",
            "source_url": url,
            "image_data": None,
        })

    def _extract_specs(self, html: str) -> dict:
        """Извлекает характеристики из div.field.viewbull-field__container."""
        specs = {}
        for m in SPEC_BLOCK_RE.finditer(html):
            label = m.group(1).strip()
            data_field = m.group(2) or ""
            value_raw = m.group(3)
            # Чистим HTML из значения
            value = re.sub(r'<[^>]+>', '', value_raw)
            value = re.sub(r'\s+', ' ', value).strip()
            # Убираем &nbsp;
            value = value.replace("\xa0", " ").strip()
            if label and value:
                key = data_field if data_field else label.lower().replace(" ", "_")
                specs[key] = {"label": label, "value": value}
        return specs

    @staticmethod
    def _dedupe_prices(candidates: list) -> list:
        seen = set()
        result = []
        for c in candidates:
            key = (c["role"], c["amount_minor"], c["currency"])
            if key not in seen and c["amount_minor"] > 0:
                seen.add(key)
                result.append(c)
        return result

    # ================================================================
    # SEARCH MODE: collect_by_search
    # ================================================================

    def collect_by_search(self, query: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Search Farpost: HTML search page → parse bulletin cards."""
        from urllib.parse import quote
        from datetime import datetime, timezone, timedelta

        # Public homepage search form: GET /dir, field query (verified 2026-11-14).
        class _FarpostRetryOK(Exception):
            """Internal: retry after /verify succeeded, html ready for parse."""
            pass

        url = f"https://www.farpost.ru/dir?query={quote(query)}&page={page}"
        results = []

        try:
            self._warmup()
            if page > 1:
                # 2026-11-14 v6: extra delay for pagination (diag v4: p2 got /verify after p1 in 9s)
                # 18s gap + jitter to avoid rate-limit trigger
                time.sleep(random.uniform(16, 20))
            self._throttle()
            proxies = self.pool.get_proxy()
            if proxies is None and self.pool.use_proxies:
                log.info("FARPOST: search FAIL_CLOSED — прокси нет, skip (не direct)")
                raise SearchError("SEARCH_PROXY_NOT_READY")
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.farpost.ru/",
            }
            if self._cffi:
                resp = self._cffi.get(url, impersonate="chrome", timeout=(15, 30),
                                      headers=headers, proxies=proxies)
            else:
                resp = self.session.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
            self.stats["requests"] += 1

            if resp.status_code != 200:
                # 2026-11-14 v7: P2 403 retry (diag v5: P1 OK 3.3s → P2 REQ_FAILED 2.4s;
                # Farpost rate-limits same-IP rapid requests; 10s gap before retry)
                if resp.status_code in (403, 429) and page > 1:
                    log.info("FARPOST search: HTTP %d P%d, retrying in 10s", resp.status_code, page)
                    time.sleep(random.uniform(10, 12))
                    if self._cffi:
                        resp = self._cffi.get(url, impersonate="chrome", timeout=(10, 20),
                                              headers=headers, proxies=proxies)
                    else:
                        resp = self.session.get(url, headers=headers, proxies=proxies, timeout=(8, 20))
                    self.stats["requests"] += 1
                    if resp.status_code == 200:
                        log.info("FARPOST search: P%d retry OK", page)
                    else:
                        log.warning("FARPOST search: P%d retry HTTP %d", page, resp.status_code)
                        self.stats["blocked"] += 1
                        raise SearchError(f"SEARCH_HTTP_{resp.status_code}")
                else:
                    log.warning("FARPOST search: HTTP %d for %r", resp.status_code, query[:40])
                    self.stats["blocked" if resp.status_code in (403, 429) else "failed"] += 1
                    raise SearchError(f"SEARCH_HTTP_{resp.status_code}")

            # Farpost uses windows-1251
            ct = (resp.headers.get("Content-Type") or "").lower()
            if "1251" in ct or "cp1251" in ct:
                html = resp.content.decode("windows-1251", errors="replace")
            else:
                html = resp.text

            # 2026-11-14 v8: Farpost /dir?query=X&page=N redirects to category URL
            # losing the page param (confirmed diag_v6c: p1==p2, 45/45 overlap).
            # Fix: if page>1 and final URL lacks page=N, re-request with it appended.
            # v8b: skip if final URL is /verify (retry loop already handled above).
            if page > 1 and "/verify" not in (resp.url or ""):
                _final = resp.url or ""
                if f"page={page}" not in _final:
                    _sep = "&" if "?" in _final else "?"
                    _pg_url = f"{_final}{_sep}page={page}"
                    log.info("FARPOST search: page param lost in redirect, retrying %s", _pg_url[:90])
                    if self._cffi:
                        resp = self._cffi.get(_pg_url, impersonate="chrome", timeout=(15, 30),
                                              headers=headers, proxies=proxies)
                    else:
                        resp = self.session.get(_pg_url, headers=headers, proxies=proxies, timeout=(10, 30))
                    self.stats["requests"] += 1
                    ct = (resp.headers.get("Content-Type") or "").lower()
                    if "1251" in ct or "cp1251" in ct:
                        html = resp.content.decode("windows-1251", errors="replace")
                    else:
                        html = resp.text

            # Check for /verify redirect (v6: retry once with 8s delay)
            if "/verify" in (resp.url or ""):
                log.warning("FARPOST search: /verify page (soft-block), retrying in 8s")
                self.stats["blocked"] += 1
                if page > 1:
                    time.sleep(random.uniform(7, 9))
                    if self._cffi:
                        resp = self._cffi.get(url, impersonate="chrome", timeout=(15, 30),
                                              headers=headers, proxies=proxies)
                    else:
                        resp = self.session.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
                    self.stats["requests"] += 1
                    if resp.status_code == 200 and "/verify" not in (resp.url or ""):
                        log.info("FARPOST search: retry after /verify succeeded")
                        ct2 = (resp.headers.get("Content-Type") or "").lower()
                        if "1251" in ct2 or "cp1251" in ct2:
                            html = resp.content.decode("windows-1251", errors="replace")
                        else:
                            html = resp.text
                        self.stats["success"] += 1
                        # skip to parse (html already set)
                        raise _FarpostRetryOK(html)
                    else:
                        raise SearchError("SEARCH_ACCESS_CHALLENGE")
                else:
                    raise SearchError("SEARCH_ACCESS_CHALLENGE")

            if len(html) < 3000:
                log.warning("FARPOST search: short response (%d bytes)", len(html))
                self.stats["failed"] += 1
                raise SearchError("SEARCH_RESPONSE_TOO_SHORT")

            self.stats["success"] += 1

        except SearchError:
            raise
        except _FarpostRetryOK as retry_ok:
            html = retry_ok.args[0] if retry_ok.args else ""
        except Exception as e:
            self.stats["failed"] += 1
            log.warning("FARPOST search: error: %s", type(e).__name__)
            raise SearchError("SEARCH_REQUEST_FAILED") from e

        # Parse bulletin cards: links to /{city}/{category}/{slug}-{id}.html
        card_pat = re.compile(
            r'<a[^>]*href=["\'](?:https?://www\.farpost\.ru)?/[^"\']*/([a-z0-9\-]*?(\d{5,}))[a-z0-9\-]*\.html["\'][^>]*>',
            re.I
        )
        matches = list(card_pat.finditer(html))
        log.info("FARPOST search: %d bulletin links found", len(matches))

        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        seen_ids = set()

        for i, m in enumerate(matches[:page_size * 3]):
            fid = m.group(2)  # numeric ID
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            if len(results) >= page_size:
                break

            # Price is in descriptionCell BEFORE the bulletin link; expand backwards
            block_start = max(0, m.start() - 2500)
            block_end = matches[i + 1].start() if i + 1 < len(matches) else min(m.end() + 2000, len(html))
            block = html[block_start:block_end]

            # Title: 2026-11-15 — anchor text FIRST (most reliable), then h2/h3 after link
            title = None
            # 1) Anchor text: <a ...>TITLE</a>
            a_close = html.find('</a>', m.end())
            if a_close > 0:
                a_text = html[m.end():a_close]
                a_clean = re.sub(r'<[^>]+>', '', a_text).strip()
                a_clean = a_clean.replace('&larr;', '←').replace('&nbsp;', ' ').replace('&amp;', '&').replace('&laquo;', '«').replace('&raquo;', '»')
                a_clean = re.sub(r'\s+', ' ', a_clean).strip()
                if len(a_clean) >= 5 and not a_clean.startswith(('←', 'Назад', '>>')):
                    title = a_clean
            # 2) h2/h3 in window AFTER the link (avoids previous card's headings)
            if not title:
                after_start = m.end()
                after_end = min(after_start + 1500, block_end)
                after_block = html[after_start:after_end]
                t_match = re.search(r'<h[23][^>]*>(.*?)</h[23]>', after_block, re.DOTALL)
                if t_match:
                    title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
                    title = re.sub(r'\s+', ' ', title).strip()
            # 3) aria-label in full block
            if not title:
                t_match = re.search(r'aria-label=["\']([^"\']{10,200})["\']', block)
                if t_match:
                    title = t_match.group(1).strip()
            # 4) Generic text in block
            if not title:
                t_match = re.search(r'>([^<>]{15,200})</(?:a|span|div)>', block)
                if t_match:
                    candidate = t_match.group(1).strip()
                    if len(candidate) > 10 and not candidate.startswith(('http', '{', '←')):
                        title = candidate
            if not title:
                title = f"FARPOST-{fid}"

            # Price: data-price, data-role="price", data-bulletin-price, text pattern
            price_minor = 0
            # 1) data-price="14 000<i class='currency...'>"
            p_match = re.search(r'data-price=["\']([\d][\d\s\u00a0]*)', block)
            if p_match:
                price_str = p_match.group(1).replace(' ', '').replace('\u00a0', '')
                try:
                    price_minor = int(float(price_str) * 100)
                except ValueError:
                    pass
            # 2) data-role="price">14 000<i...>
            if not price_minor:
                p_match = re.search(r'data-role=["\']price["\']>([\d][\d\s\u00a0]*)', block)
                if p_match:
                    price_str = p_match.group(1).replace(' ', '').replace('\u00a0', '')
                    try:
                        price_minor = int(float(price_str) * 100)
                    except ValueError:
                        pass
            # 3) data-bulletin-price (legacy)
            if not price_minor:
                p_match = re.search(r'data-bulletin-price=["\'](\d+)["\']', block)
                if p_match:
                    price_minor = int(p_match.group(1))
            # 4) text: 14 000 ₽ / руб / &#8381;
            if not price_minor:
                p_match = re.search(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)\s*(?:₽|руб|&#8381;)', block)
                if p_match:
                    price_str = p_match.group(1).replace(' ', '').replace('\u00a0', '')
                    try:
                        price_minor = int(float(price_str) * 100)
                    except ValueError:
                        pass

            # Image
            img_url = None
            img_match = re.search(r'(?:src|data-src)=["\']([^"\']*static\.baza\.farpost\.ru/[^"\']+)["\']', block)
            if img_match:
                img_url = img_match.group(1)
            if not img_match:
                img_match = re.search(r'(?:src|data-src)=["\']([^"\']*farpost\.ru/[^"\']*\.(?:jpg|jpeg|png|webp)[^"\']*)["\']', block, re.I)
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
                    "id": fid,
                    "listing_id": fid,
                    "title": title,
                    "url": f"https://www.farpost.ru/vladivostok/{fid}.html",
                    "observed_at": now,
                    "status": "PARTIAL_SUCCESS",
                    "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTML",
                    "http_status": 200,
                },
            })

        log.info("FARPOST: collect_by_search %r → %d products", query[:40], len(results))
        return results
