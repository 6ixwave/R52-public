"""Сборщик Avito.

Стратегия парсинга:
1. JSON-LD (schema.org/Product / Offer) — Avito отдаёт на карточках.
2. Встроенный JSON: window.__PRELOADED_STATE__ / data-state.
3. HTML fallback: og: meta, data-атрибуты, видимый текст.

Медиа:
- Фото: avatars.mmedia.avito.st / l16.avito.st / l26.avito.st
- Видео: mp4 в HTML (редко, но бывает)

Особенности Avito:
- URL: https://www.avito.ru/{city}/{category}/{id} — ID последний числовой сегмент.
- Частый CAPTCHA (SmartCaptcha) — при 403/429 честно останавливаемся.
- Мобильная версия (m.avito.ru) иногда стабильнее — fallback.
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import urlsplit

from .base import BaseCollector
from .discovery import SearchError

log = logging.getLogger("r52.collectors.avito")

AVITO_HOSTS = ("avito.ru", "www.avito.ru", "m.avito.ru")
AVITO_CDN = ("avito.st", "avito.ru", "mmedia.avito.st")

# Паттерн URL: последний числовой сегмент — ID объявления
CARD_URL_RE = re.compile(r"https://(?:www\.)?avito\.ru/.*/(\d{6,})/?$")

# Встроенные JSON-контейнеры Avito
STATE_PATTERNS = [
    re.compile(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', re.DOTALL),
    re.compile(r'<script[^>]*id=["\']preloaded-state["\'][^>]*>(\{.*?\})</script>', re.DOTALL),
    re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', re.DOTALL),
    re.compile(r'data-state=["\'](\{.*?\})["\']', re.DOTALL),
]


class AvitoCollector(BaseCollector):
    source = "AVITO"
    title = "Avito"

    def __init__(self, data_dir=None, use_proxies: bool = True):
        super().__init__(data_dir=data_dir, use_proxies=use_proxies)
        self._cffi = None
        try:
            from curl_cffi import requests as cffi_req
            self._cffi = cffi_req
            log.info("AVITO: curl_cffi доступен (impersonate=chrome)")
        except ImportError:
            log.info("AVITO: curl_cffi недоступен, fallback на requests")
        self._warmed_up = False

    def _warmup(self):
        """Session warmup: заход на главную для cookie + session depth (anti-bot Layer 3)."""
        if self._warmed_up:
            return
        try:
            self._throttle()
            proxies = self.pool.get_proxy()
            if self._cffi:
                resp = self._cffi.get(
                    "https://www.avito.ru/",
                    impersonate="chrome", timeout=15, proxies=proxies,
                    headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
                )
            else:
                resp = self.session.get(
                    "https://www.avito.ru/", proxies=proxies, timeout=(5, 15),
                    headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
                )
            self.stats["requests"] += 1
            log.info("AVITO warmup: HTTP %d", getattr(resp, 'status_code', 0))
        except Exception as e:
            log.info("AVITO warmup failed: %s", e)
        self._warmed_up = True

    def build_url(self, listing_id: str) -> str:
        # Avito: ID без города — используем короткий URL
        return f"https://www.avito.ru/ad/{listing_id}"

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        """Multi-URL: www.avito.ru/ad → m.avito.ru/ad → www.avito.ru/{id} → super()."""
        urls_to_try = [
            f"https://www.avito.ru/ad/{listing_id}",
            f"https://m.avito.ru/ad/{listing_id}",
            f"https://www.avito.ru/{listing_id}",
            f"https://m.avito.ru/{listing_id}",
        ]
        for u in urls_to_try:
            try:
                self._throttle()
                proxies = self.pool.get_proxy()
                headers = {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                }
                resp = self.session.get(u, headers=headers, proxies=proxies, timeout=(5, 20))
                self.stats["requests"] += 1
                if resp.status_code == 200 and len(resp.text) > 500:
                    self.stats["success"] += 1
                    observation = self.parse_page(resp, listing_id, u, title_hint)
                    if observation:
                        self._ensure_evidence(observation)
                        self._ensure_price_amounts(observation)
                        self._ensure_availability_in_fields(observation)
                        from datetime import datetime, timezone, timedelta
                        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
                        has_title = bool(observation.get("fields", {}).get("title", {}).get("value", ""))
                        has_price = len(observation.get("price_candidates", [])) > 0
                        has_media = len(observation.get("media", {}).get("items", [])) > 0
                        if has_title and has_price and has_media:
                            status = "SUCCESS"
                        elif has_title and (has_price or has_media):
                            status = "PARTIAL_SUCCESS"
                        else:
                            status = "DEGRADED"
                        log.info("AVITO %s: %s OK (title=%s, prices=%d, media=%d)",
                                 listing_id, u.split("/")[2],
                                 str(observation["fields"]["title"]["value"])[:40],
                                 len(observation["price_candidates"]),
                                 len(observation["media"]["items"]))
                        return {
                            "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
                            "capture_action": "COLLECTOR_RUN",
                            "fully_autonomous": True,
                            "collector_source": self.source,
                            "observation": {
                                **observation,
                                "source": self.source,
                                "id": str(listing_id),
                                "url": u,
                                "observed_at": now,
                                "status": status,
                                "product_content": True,
                                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                                "http_status": 200,
                            },
                        }
                    else:
                        log.info("AVITO %s: %s 200 но parse=None", listing_id, u.split("/")[2])
                elif resp.status_code in (403, 429):
                    self.stats["blocked"] += 1
                    log.info("AVITO %s: %s -> %d (captcha?)", listing_id, u.split("/")[2], resp.status_code)
                    break
                else:
                    self.stats["failed"] += 1
                    log.info("AVITO %s: %s -> HTTP %d", listing_id, u.split("/")[2], resp.status_code)
            except Exception as e:
                self.stats["failed"] += 1
                log.info("AVITO %s: %s error: %s", listing_id, u.split("/")[2], e)

        return super().collect_product(listing_id, url, title_hint)

    def parse_page(self, resp, listing_id, url, title_hint):
        html = resp.text
        if not html or len(html) < 500:
            log.warning("AVITO %s: короткий ответ (%d байт)", listing_id, len(html or ""))
            return None

        # Проверка на CAPTCHA
        if self._is_captcha_page(html):
            log.warning("AVITO %s: CAPTCHA страница, остановка", listing_id)
            return None

        jsonld = self.extract_json_ld(html)
        product_ld = self.find_product_jsonld(jsonld)
        state = self._extract_state(html)

        fields = {}
        price_candidates = []
        media_items = []
        availability = "UNKNOWN"

        # --- Title ---
        title = None
        if product_ld:
            title = product_ld.get("name") or product_ld.get("title")
        if not title:
            m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                title = m.group(1)
        if not title:
            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            if m:
                title = m.group(1).replace(" - Авито", "").replace("Авито - ", "").strip()
        if not title:
            title = title_hint or f"AVITO-{listing_id}"
        fields["title"] = {"status": "KNOWN", "value": title, "method": "JSONLD" if product_ld else "META"}

        # --- Prices ---
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
                                "role": "regular", "amount_minor": minor,
                                "currency": currency, "label": "JSON-LD price",
                            })
                        except (ValueError, TypeError):
                            pass
                    avail = offer.get("availability", "")
                    if "IN_STOCK" in avail.upper():
                        availability = "IN_STOCK"
                    elif "OUT_OF_STOCK" in avail.upper():
                        availability = "OUT_OF_STOCK"

        if state:
            self._extract_prices_from_state(state, price_candidates)

        if not price_candidates:
            self._extract_prices_from_html(html, price_candidates)

        price_candidates = self._dedupe_prices(price_candidates)

        # --- Media ---
        media_items = self._extract_media(html, state, product_ld)

        # --- Description ---
        description = None
        if product_ld:
            description = product_ld.get("description")
        if not description and state:
            description = self._extract_description_from_state(state)
        if not description:
            m = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                description = m.group(1)
        if not description:
            # Avito: описание в div с data-attribute
            m = re.search(r'data-description=["\']([^"\']+)', html, re.I)
            if m:
                description = m.group(1)
        if description:
            fields["description"] = {"status": "KNOWN", "value": description[:3000], "method": "JSONLD" if product_ld else "HTML"}

        # --- Category ---
        category = None
        if state:
            category = self._extract_category(state)
        if not category:
            m = re.search(r'<meta[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                path = urlsplit(m.group(1)).path
                parts = [p for p in path.split("/") if p and not p.isdigit() and p not in ("ad", "ads")]
                if len(parts) >= 2:
                    category = " > ".join(parts[:3])
        if category:
            fields["category"] = {"status": "KNOWN", "value": category, "method": "STATE"}

        # --- Brand / Model ---
        brand = None
        model = None
        if product_ld:
            b = product_ld.get("brand")
            if isinstance(b, dict):
                brand = b.get("name")
            elif isinstance(b, str):
                brand = b
            model = product_ld.get("sku") or product_ld.get("mpn")
        if state:
            brand = brand or self._deep_get(state, "ad", "brand") or self._deep_get(state, "product", "brand")
            model = model or self._deep_get(state, "ad", "model") or self._deep_get(state, "product", "sku")
        if brand:
            fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": "JSONLD" if product_ld else "STATE"}
        if model:
            fields["model"] = {"status": "KNOWN", "value": str(model), "method": "JSONLD" if product_ld else "STATE"}

        # --- Availability ---
        if availability == "UNKNOWN":
            if re.search(r"в наличии|доступен|активно|продаётся", html, re.I):
                availability = "IN_STOCK"
            elif re.search(r"продано|недоступно|архив|завершено", html, re.I):
                availability = "OUT_OF_STOCK"

        return {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": availability,
            "category_path": category,
        }

    def _is_captcha_page(self, html: str) -> bool:
        """Определяет SmartCaptcha / капчу Avito."""
        captcha_markers = [
            "smartcaptcha",
            "captcha",
            "recaptcha",
            "cf-challenge",
            "turnstile",
            "data-sitekey",
        ]
        lower = html[:5000].lower()
        return any(m in lower for m in captcha_markers)

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
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        for script in scripts:
            stripped = script.strip()
            if stripped.startswith("{") and len(stripped) > 500:
                try:
                    data = json.loads(stripped)
                    if isinstance(data, dict) and ("ad" in data or "product" in data or "state" in data or "data" in data):
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

    def _extract_prices_from_state(self, state: dict, candidates: list):
        for path in [("ad", "price"), ("product", "price"), ("ad", "priceInfo"),
                     ("data", "price"), ("ad", "prices"), ("product", "prices")]:
            obj = self._deep_get(state, *path)
            if obj is None:
                continue
            if isinstance(obj, dict):
                for role_key, role in [("value", "regular"), ("price", "regular"),
                                       ("old", "old"), ("crossed", "old")]:
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

    def _extract_prices_from_html(self, html: str, candidates: list):
        m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            try:
                minor = int(round(float(m.group(1)) * 100))
                candidates.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "og:price"})
            except ValueError:
                pass

        for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
            try:
                minor = int(m.group(1))
                candidates.append({"role": "regular", "amount_minor": minor, "currency": "RUB", "label": "data-price"})
                break
            except ValueError:
                pass

        # Avito: цена в span с class "price" или data-attribute
        for m in re.finditer(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>(\d[\d\s\u00a0]*)\s*(?:₽|руб)', html, re.I):
            try:
                amount = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                candidates.append({"role": "regular", "amount_minor": int(amount * 100), "currency": "RUB", "label": "HTML price"})
            except ValueError:
                continue
            break

    def _extract_media(self, html: str, state: Optional[dict], product_ld: Optional[dict]) -> list:
        items = []
        seen_urls = set()

        # 1. Из state
        if state:
            self._media_from_state(state, items, seen_urls)

        # 2. Из JSON-LD
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

        # 3. Из HTML
        m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            self._add_media(items, seen_urls, "image", m.group(1))

        # Все img с avito.st
        for m in re.finditer(r'(?:src|data-src|data-original)=["\']([^"\']*avito\.st[^"\']*)["\']', html, re.I):
            self._add_media(items, seen_urls, "image", m.group(1))

        # Видео
        for m in re.finditer(r'(?:src|data-src|data-url)=["\']([^"\']*\.(?:mp4|webm)(?:\?[^"\']*)?)["\']', html, re.I):
            self._add_media(items, seen_urls, "video", m.group(1))
        if state:
            self._videos_from_state(state, items, seen_urls)

        return items[:40]

    def _media_from_state(self, state: dict, items: list, seen: set):
        for path in [("ad", "images"), ("ad", "photos"), ("product", "images"),
                     ("data", "images"), ("ad", "gallery"), ("product", "photos")]:
            obj = self._deep_get(state, *path)
            if not obj:
                continue
            if isinstance(obj, list):
                for entry in obj:
                    if isinstance(entry, str):
                        self._add_media(items, seen, "image", entry)
                    elif isinstance(entry, dict):
                        url = entry.get("url") or entry.get("src") or entry.get("link") or entry.get("path")
                        if url:
                            self._add_media(items, seen, "image", url)
            elif isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, str) and ("avito.st" in v or "avito.ru" in v):
                        self._add_media(items, seen, "image", v)

    def _videos_from_state(self, state: dict, items: list, seen: set):
        for path in [("ad", "videos"), ("product", "videos"), ("data", "videos"),
                     ("ad", "video"), ("product", "video")]:
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
            elif isinstance(obj, str) and (".mp4" in obj or ".webm" in obj):
                self._add_media(items, seen, "video", obj)

    def _add_media(self, items: list, seen: set, mtype: str, url: str):
        if not url or url in seen:
            return
        safe = self.safe_media_url(url, "AVITO")
        if safe is None:
            if "avito.st" in url or "avito.ru" in url:
                safe = url
            else:
                return
        seen.add(safe)
        items.append({"type": mtype, "source_url": safe, "image_data": None})

    def _extract_description_from_state(self, state: dict) -> Optional[str]:
        for path in [("ad", "description"), ("product", "description"),
                     ("data", "description"), ("ad", "descriptionHtml")]:
            obj = self._deep_get(state, *path)
            if obj and isinstance(obj, str) and len(obj) > 20:
                text = re.sub(r'<[^>]+>', ' ', obj)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:3000]
        return None

    def _extract_category(self, state: dict) -> Optional[str]:
        for path in [("ad", "category"), ("product", "category"),
                     ("ad", "categoryPath"), ("data", "category")]:
            obj = self._deep_get(state, *path)
            if obj:
                if isinstance(obj, str):
                    return obj
                if isinstance(obj, list):
                    return " > ".join(str(x) for x in obj)
                if isinstance(obj, dict):
                    return obj.get("name") or obj.get("path")
        return None

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
        """Search Avito: HTML search page → parse ad cards. CAPTCHA-aware."""
        from urllib.parse import quote
        from datetime import datetime, timezone, timedelta

        url = f"https://www.avito.ru/search?query={quote(query)}&page={page}"
        results = []

        try:
            self._warmup()
            self._throttle()
            proxies = self.pool.get_proxy()
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.avito.ru/",
            }
            if self._cffi:
                resp = self._cffi.get(url, impersonate="chrome", timeout=(15, 30),
                                      headers=headers, proxies=proxies)
            else:
                resp = self.session.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
            self.stats["requests"] += 1

            if resp.status_code in (403, 429):
                log.warning("AVITO search: CAPTCHA/blocked (%d) for %r", resp.status_code, query[:40])
                self.stats["blocked"] += 1
                raise SearchError(f"SEARCH_HTTP_{resp.status_code}")
            if resp.status_code != 200:
                log.warning("AVITO search: HTTP %d for %r", resp.status_code, query[:40])
                self.stats["failed"] += 1
                raise SearchError(f"SEARCH_HTTP_{resp.status_code}")

            html = resp.text
            if len(html) < 3000:
                log.warning("AVITO search: short response (%d bytes)", len(html))
                self.stats["failed"] += 1
                raise SearchError("SEARCH_RESPONSE_TOO_SHORT")

            # CAPTCHA check
            if self._is_captcha_page(html):
                log.warning("AVITO search: CAPTCHA page detected")
                self.stats["blocked"] += 1
                raise SearchError("SEARCH_ACCESS_CHALLENGE")

            self.stats["success"] += 1

        except SearchError:
            raise
        except Exception as e:
            self.stats["failed"] += 1
            log.warning("AVITO search: error: %s", type(e).__name__)
            raise SearchError("SEARCH_REQUEST_FAILED") from e

        # Parse ad cards: <a ... href="/.../{id}" ...> with title/price
        # Avito search cards have data-attributes or structured HTML
        card_pat = re.compile(
            r'<a[^>]*href=["\'](?:https?://(?:www\.|m\.)?avito\.ru/)?[^"]*?(\d{6,})[^"]*["\'][^>]*>',
            re.I
        )
        matches = list(card_pat.finditer(html))
        log.info("AVITO search: %d ad links found", len(matches))

        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        seen_ids = set()

        for i, m in enumerate(matches[:page_size * 3]):
            aid = m.group(1)
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            if len(results) >= page_size:
                break

            block_start = m.start()
            block_end = matches[i + 1].start() if i + 1 < len(matches) else min(block_start + 4000, len(html))
            block = html[block_start:block_end]

            # Title
            title = None
            t_match = re.search(r'aria-label=["\']([^"\']{10,200})["\']', block)
            if t_match:
                title = t_match.group(1).strip()
            if not title:
                t_match = re.search(r'<(?:h2|h3|span|div)[^>]*class=["\'][^"\']*(?:title|name|header)[^"\']*["\'][^>]*>([^<]{10,200})<', block, re.I)
                if t_match:
                    title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()
            if not title:
                t_match = re.search(r'>([^<>]{15,200})</(?:h2|h3|span|a)>', block)
                if t_match:
                    candidate = t_match.group(1).strip()
                    if len(candidate) > 10 and not candidate.startswith(('http', '{')):
                        title = candidate
            if not title:
                title = f"AVITO-{aid}"

            # Price
            price_minor = 0
            p_match = re.search(r'(\d{2,9}(?:[\s\u00a0]\d{3})*)(?:\s*[.,]\d{1,2})?\s*(?:₽|руб)', block)
            if p_match:
                price_str = p_match.group(1).replace(' ', '').replace('\u00a0', '')
                try:
                    price_minor = int(float(price_str) * 100)
                except ValueError:
                    pass
            if not price_minor:
                p_match = re.search(r'data-price=["\']?(\d{3,9})', block)
                if p_match:
                    price_minor = int(p_match.group(1))

            # Image
            img_url = None
            img_match = re.search(r'(?:src|data-src)=["\']([^"\']*(?:avito\.st|avito\.ru)/[^"\']+)["\']', block)
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
                    "id": aid,
                    "url": f"https://www.avito.ru/ad/{aid}",
                    "observed_at": now,
                    "status": "PARTIAL_SUCCESS",
                    "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTML",
                    "http_status": 200,
                },
            })

        log.info("AVITO: collect_by_search %r → %d products", query[:40], len(results))
        return results
        return result
