"""Сборщик Wildberries.

Стратегия (от стабильного к хрупкому):
1. Basket CDN card.json — основной: title, brand, desc, options, category, media.
2. Price API (card.wb.ru / web API) — цена, наличие.
3. HTML (detail.aspx) — fallback для медиа, цен.

HTTP: curl_cffi (impersonate=chrome) как основной, requests как fallback.

Безопасность:
- Никаких cookie, токенов, авторизации.
- Только GET на публичные endpoints.
- Прокси + rate limit из base.py.
"""

import json
import logging
import random
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlsplit

from .base import BaseCollector
from .discovery import SearchError

log = logging.getLogger("r52.collectors.wb")

WB_HOSTS = ("wildberries.ru", "www.wildberries.ru")
WB_CDN = ("wbbasket.ru", "wbstatic.net", "wildberries.ru")

CARD_URL_RE = re.compile(r"https://(?:www\.)?wildberries\.ru/catalog/(\d+)/detail\.aspx")

WB_IMG_SIZE = "S800"

# Кэш basket-хостов (class-level)
_basket_host_cache: dict[int, int] = {}

# Заголовки для JSON API
JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}

# Заголовки для HTML
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}


class WBCollector(BaseCollector):
    source = "WB"
    title = "Wildberries"

    def __init__(self, data_dir=None, use_proxies: bool = True):
        super().__init__(data_dir=data_dir, use_proxies=use_proxies)
        # curl_cffi как основной HTTP-клиент
        self._cffi = None
        try:
            from curl_cffi import requests as cffi_req
            self._cffi = cffi_req
            log.info("WB: curl_cffi доступен (impersonate=chrome)")
        except ImportError:
            log.info("WB: curl_cffi недоступен, fallback на requests")
        self._warmed_up = False

    def _warmup(self):
        """Session warmup: GET главной для cookie + TLS fingerprint (anti-bot Layer 1.5)."""
        if self._warmed_up:
            return
        try:
            self._throttle()
            proxies = self.pool.get_proxy()
            if proxies is None and self.pool.use_proxies:
                log.info("WB: warmup FAIL_CLOSED — прокси нет, skip (не direct)")
                self._warmed_up = True
                return
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            }
            if self._cffi:
                resp = self._cffi.get(
                    "https://www.wildberries.ru/",
                    impersonate="chrome", timeout=6, proxies=proxies,
                    headers=headers,
                )
            else:
                resp = self.session.get(
                    "https://www.wildberries.ru/", proxies=proxies, timeout=(3, 6),
                    headers=headers,
                )
            self.stats["requests"] += 1
            log.info("WB warmup: HTTP %d", getattr(resp, 'status_code', 0))
        except Exception as e:
            log.info("WB warmup failed: %s", e)
        self._warmed_up = True

    def build_url(self, listing_id: str) -> str:
        return f"https://www.wildberries.ru/catalog/{listing_id}/detail.aspx"

    # ================================================================
    # HTTP HELPER: curl_cffi primary, requests fallback
    # ================================================================

    def _fast_throttle(self):
        """Короткая пауза для внутренних API-вызовов (2-4 сек)."""
        now = time.monotonic()
        elapsed = now - self._last_request
        wait = random.uniform(2, 4) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _http_get(self, url: str, headers: Optional[dict] = None,
                  timeout: int = 15, fast: bool = False) -> Optional[object]:
        """GET запрос. Возвращает response object (cffi или requests) или None."""
        if fast:
            self._fast_throttle()
        else:
            self._throttle()
        proxies = self.pool.get_proxy()

        if proxies is None and self.pool.use_proxies:
            log.info("WB: FAIL_CLOSED _http_get — прокси нет, %s отложен (не direct)", url[:60])
            return None

        if self._cffi:
            kw = {"impersonate": "chrome", "timeout": timeout, "proxies": proxies}
            if headers:
                kw["headers"] = headers
            try:
                resp = self._cffi.get(url, **kw)
                self.stats["requests"] += 1
                if resp.status_code == 200:
                    self.stats["success"] += 1
                    return resp
                elif resp.status_code in (403, 429, 498):
                    self.stats["blocked"] += 1
                else:
                    self.stats["failed"] += 1
                return resp  # возвращаем даже не-200 (для лога)
            except Exception as e:
                self.stats["requests"] += 1
                self.stats["failed"] += 1
                log.warning("WB cffi error %s: %s", url[:80], e)
                return None
        else:
            # Fallback: standard requests
            h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
            if headers:
                h.update(headers)
            try:
                resp = self.session.get(url, headers=h, proxies=proxies,
                                        timeout=(5, timeout))
                self.stats["requests"] += 1
                if resp.status_code == 200:
                    self.stats["success"] += 1
                    return resp
                elif resp.status_code in (403, 429, 498):
                    self.stats["blocked"] += 1
                else:
                    self.stats["failed"] += 1
                return resp
            except Exception as e:
                self.stats["requests"] += 1
                self.stats["failed"] += 1
                log.warning("WB req error %s: %s", url[:80], e)
                return None

    # ================================================================
    # ПЕРЕОПРЕДЕЛЁННЫЙ collect_product
    # ================================================================

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        nm_id = int(listing_id)
        self._warmup()

        # 1. Basket CDN
        card_data = self._fetch_card_json(nm_id)
        basket_host = _basket_host_cache.get(nm_id // 100000)

        # 2. Price
        price_data = self._fetch_price(nm_id)

        # 3. HTML (best-effort)
        html_resp = self._fetch_html(url)

        # Merge
        observation = self._merge_observations(
            nm_id, card_data, price_data, html_resp, basket_host, title_hint
        )
        if observation is None:
            return None

        # Пост-обработка
        self._ensure_evidence(observation)
        self._ensure_price_amounts(observation)
        self._ensure_availability_in_fields(observation)

        # Динамический статус
        title_val = observation["fields"].get("title", {}).get("value", "")
        has_title = bool(title_val) and title_val != f"WB-{nm_id}"
        has_price = len(observation.get("price_candidates", [])) > 0
        has_media = len(observation.get("media", {}).get("items", [])) > 0
        has_desc = bool(observation["fields"].get("description", {}).get("value", ""))

        if has_title and has_price and has_media:
            status = "SUCCESS"
        elif has_title and has_media:
            status = "PARTIAL_SUCCESS"
        elif has_title or has_price:
            status = "PARTIAL_SUCCESS"
        else:
            status = "DEGRADED"

        from datetime import datetime, timezone, timedelta
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
                "status": status,
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
            },
        }

    # ================================================================
    # BASKET CDN
    # ================================================================

    def _fetch_card_json(self, nm_id: int) -> Optional[dict]:
        vol = nm_id // 100000
        part = nm_id // 1000
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

        host = self._find_basket_host(nm_id, vol, path)
        if host is None:
            log.warning("WB %d: basket host не найден", nm_id)
            return None

        url = f"https://{host}{path}"
        resp = self._http_get(url, headers=JSON_HEADERS, timeout=15, fast=True)
        if resp is None:
            return None
        if resp.status_code != 200:
            log.warning("WB %d: card.json HTTP %d", nm_id, resp.status_code)
            return None
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("nm_id"):
                log.info("WB %d: card.json OK (%d keys, %dB)",
                         nm_id, len(data), len(resp.content))
                return data
            else:
                log.warning("WB %d: card.json 200 но нет nm_id", nm_id)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("WB %d: card.json parse error: %s (body[:100]=%s)",
                        nm_id, e, resp.text[:100])
        return None

    def _find_basket_host(self, nm_id: int, vol: int, path: str) -> Optional[str]:
        if vol in _basket_host_cache:
            n = _basket_host_cache[vol]
            return f"basket-{n:02d}.wbbasket.ru"

        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            resp = self._http_get(url, headers=JSON_HEADERS, timeout=8, fast=True)
            if resp is not None and resp.status_code == 200:
                _basket_host_cache[vol] = n
                log.info("WB: vol=%d -> %s (nm_id=%d)", vol, host, nm_id)
                return host
        log.warning("WB %d: basket host не найден для vol=%d", nm_id, vol)
        return None

    # ================================================================
    # PRICE
    # ================================================================

    def _fetch_price(self, nm_id: int) -> Optional[dict]:
        """Пробует price-эндпоинты. v4 — основной (подтверждён 2026-07)."""
        endpoints = [
            # card.wb.ru v4 — основной (единственный рабочий по данным dev.to 2026-07)
            ("card_v4",
             f"https://card.wb.ru/cards/v4/detail"
             f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            # catalog.wb.ru v4 — mirror
            ("catalog_v4",
             f"https://catalog.wb.ru/cards/v4/detail"
             f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            # Web API (fallback)
            ("web_api",
             f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
            # search.wb.ru (последний шанс)
            ("search_wb",
             f"https://search.wb.ru/cards/v4/detail"
             f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ]

        for label, url in endpoints:
            try:
                resp = self._http_get(url, headers=JSON_HEADERS, timeout=12, fast=True)
                if resp is None:
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    # card.wb.ru format
                    products = data.get("products") or data.get("data", {}).get("items", [])
                    if products:
                        p = products[0]
                        result = {
                            "name": p.get("name"),
                            "brand": p.get("brand"),
                            "sizes": p.get("sizes", []),
                            "source": label,
                        }
                        for size in p.get("sizes", []):
                            price_obj = size.get("price", {})
                            if price_obj:
                                result["prices"] = price_obj
                                result["qty"] = size.get("qty")
                                break
                        log.info("WB %d: price via %s OK (name=%s)",
                                 nm_id, label, str(result.get("name", "?"))[:40])
                        return result
                    # web_api format
                    if "data" in data and isinstance(data["data"], dict):
                        d = data["data"]
                        prices = d.get("prices") or d.get("price")
                        if prices:
                            result = {"prices": prices, "qty": d.get("qty"),
                                      "name": d.get("name"), "source": label}
                            log.info("WB %d: price via %s OK", nm_id, label)
                            return result
                elif resp.status_code in (403, 498):
                    log.info("WB %d: price %s -> %d", nm_id, label, resp.status_code)
                    continue
                else:
                    log.info("WB %d: price %s -> %d", nm_id, label, resp.status_code)
            except (json.JSONDecodeError, ValueError) as e:
                log.debug("WB %d: price %s parse: %s", nm_id, label, e)
            except Exception as e:
                log.debug("WB %d: price %s error: %s", nm_id, label, e)
            time.sleep(1)

        log.info("WB %d: price — все эндпоинты не дали данных", nm_id)
        return None

    # ================================================================
    # HTML (best-effort)
    # ================================================================

    def _fetch_html(self, url: str):
        resp = self._http_get(url, headers=HTML_HEADERS, timeout=20)
        if resp is not None and resp.status_code == 200:
            log.info("WB HTML: 200, %dB", len(resp.content))
            return resp
        if resp is not None:
            log.info("WB HTML: HTTP %d", resp.status_code)
        return None

    # ================================================================
    # MERGE
    # ================================================================

    def _merge_observations(self, nm_id: int, card_data: Optional[dict],
                            price_data: Optional[dict], html_resp,
                            basket_host: Optional[int], title_hint: str) -> Optional[dict]:
        fields = {}
        price_candidates = []
        media_items = []
        availability = "UNKNOWN"
        category = None

        # --- TITLE ---
        title = None
        title_method = "FALLBACK"
        if card_data:
            title = card_data.get("imt_name")
            if title:
                title_method = "CARD_JSON"
        if not title and price_data:
            title = price_data.get("name")
            if title:
                title_method = "PRICE_API"
        if not title and html_resp:
            title = self._title_from_html(html_resp.text)
            if title:
                title_method = "HTML"
        if not title:
            title = title_hint or f"WB-{nm_id}"
        fields["title"] = {"status": "KNOWN", "value": title, "method": title_method}

        # --- BRAND ---
        brand = None
        brand_method = "CARD_JSON"
        if card_data:
            selling = card_data.get("selling", {})
            if isinstance(selling, dict):
                brand = selling.get("brand_name")
            if not brand:
                for opt in card_data.get("options", []):
                    if isinstance(opt, dict):
                        opt_name = (opt.get("name") or "").lower()
                        if opt_name in ("бренд", "производитель", "brand"):
                            brand = opt.get("value")
                            break
        if not brand and price_data:
            brand = price_data.get("brand")
            brand_method = "PRICE_API"
        if brand:
            fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": brand_method}

        # --- MODEL ---
        model = None
        if card_data:
            for opt in card_data.get("options", []):
                if isinstance(opt, dict) and opt.get("is_unifying"):
                    model = opt.get("value")
                    break
        if not model and card_data:
            model = card_data.get("vendor_code")
        if model:
            fields["model"] = {"status": "KNOWN", "value": str(model), "method": "CARD_JSON"}

        # --- DESCRIPTION ---
        description = None
        desc_method = "CARD_JSON"
        if card_data:
            description = card_data.get("description")
        if not description and html_resp:
            description = self._description_from_html(html_resp.text)
            desc_method = "HTML"
        if description:
            fields["description"] = {"status": "KNOWN", "value": description[:3000],
                                     "method": desc_method}

        # --- CATEGORY ---
        if card_data:
            subj = card_data.get("subj_name", "")
            subj_root = card_data.get("subj_root_name", "")
            if subj and subj_root:
                category = f"{subj_root} > {subj}"
            elif subj:
                category = subj
        if not category and html_resp:
            category = self._category_from_html(html_resp.text)
        if category:
            fields["category"] = {"status": "KNOWN", "value": category, "method": "CARD_JSON"}

        # --- OPTIONS ---
        if card_data:
            options = card_data.get("options", [])
            if isinstance(options, list) and options:
                opt_lines = []
                for opt in options[:25]:
                    if isinstance(opt, dict):
                        name = opt.get("name", "")
                        value = opt.get("value", "")
                        if name and value:
                            opt_lines.append(f"{name}: {value}")
                if opt_lines:
                    fields["options"] = {"status": "KNOWN", "value": "\n".join(opt_lines),
                                         "method": "CARD_JSON"}

        # --- PRICES ---
        if price_data and price_data.get("prices"):
            pr = price_data["prices"]
            role_map = {
                "basic": "regular", "product": "regular",
                "card": "card", "wallet": "wallet",
                "old": "old", "crossed": "old",
                "bankCard": "card",
            }
            for key, val in pr.items():
                if isinstance(val, (int, float)) and val > 0:
                    role = role_map.get(key, "regular")
                    price_candidates.append({
                        "role": role,
                        "amount_minor": int(val),
                        "currency": "RUB",
                        "label": f"price_api.{key}",
                    })

        # Цена из card.json (selling / price / priceU / priceD)
        # WB формат: число ИЛИ список [{"p": 99900, "d": 0}]
        def _wb_price_val(pv):
            """Извлекает цену из WB-формата: int/float или list[{p,d}]."""
            if isinstance(pv, (int, float)) and pv > 0:
                return int(pv)
            if isinstance(pv, list) and pv:
                first = pv[0]
                if isinstance(first, dict):
                    p_val = first.get("p") or first.get("price")
                    if isinstance(p_val, (int, float)) and p_val > 0:
                        return int(p_val)
            return None

        if not price_candidates and card_data:
            selling = card_data.get("selling", {})
            if isinstance(selling, dict):
                for pk in ("price", "priceU", "priceD", "oldPrice"):
                    pv = selling.get(pk)
                    val = _wb_price_val(pv)
                    if val:
                        price_candidates.append({
                            "role": "regular" if pk in ("price", "priceU") else "old",
                            "amount_minor": val,
                            "currency": "RUB",
                            "label": f"card_json.selling.{pk}",
                        })
            # Топ-уровневые поля card.json
            for pk in ("price", "priceU", "priceD"):
                pv = card_data.get(pk)
                val = _wb_price_val(pv)
                if val:
                    price_candidates.append({
                        "role": "regular",
                        "amount_minor": val,
                        "currency": "RUB",
                        "label": f"card_json.{pk}",
                    })

            # Sizes prices (sizes[].price.basic, etc.)
            if not price_candidates:
                for size in card_data.get("sizes", []):
                    if not isinstance(size, dict):
                        continue
                    sz_price = size.get("price", {})
                    if not isinstance(sz_price, dict):
                        continue
                    for pk, role in [("basic", "regular"), ("product", "regular"),
                                     ("card", "card"), ("wallet", "wallet"),
                                     ("old", "old"), ("crossed", "old")]:
                        pv = sz_price.get(pk)
                        val = _wb_price_val(pv)
                        if val:
                            price_candidates.append({
                                "role": role,
                                "amount_minor": val,
                                "currency": "RUB",
                                "label": f"card_json.sizes.price.{pk}",
                            })
                    if price_candidates:
                        break

        # Availability из card.json sizes[].qty (если price_data не дал)
        if availability == "UNKNOWN" and card_data:
            for size in card_data.get("sizes", []):
                if isinstance(size, dict):
                    qty = size.get("qty")
                    if isinstance(qty, (int, float)):
                        availability = "IN_STOCK" if qty > 0 else "OUT_OF_STOCK"
                        break

        if not price_candidates and html_resp:
            self._extract_prices_from_html(html_resp.text, price_candidates)

        price_candidates = self._dedupe_prices(price_candidates)

        # --- AVAILABILITY ---
        if price_data and price_data.get("qty") is not None:
            availability = "IN_STOCK" if price_data["qty"] > 0 else "OUT_OF_STOCK"
        if availability == "UNKNOWN" and html_resp:
            html_text = html_resp.text
            if re.search(r"в наличии|есть в наличии|доступен|купить", html_text, re.I):
                availability = "IN_STOCK"
            elif re.search(r"нет в наличии|закончился|распродан|ожидание", html_text, re.I):
                availability = "OUT_OF_STOCK"

        # Availability из JSON-LD
        if availability == "UNKNOWN" and html_resp:
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_resp.text, re.I | re.DOTALL):
                try:
                    data = json.loads(m.group(1))
                except (json.JSONDecodeError, ValueError):
                    continue
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict) or item.get("@type") != "Product":
                        continue
                    offers = item.get("offers")
                    if isinstance(offers, dict):
                        offers = [offers]
                    if isinstance(offers, list):
                        for offer in offers:
                            if not isinstance(offer, dict):
                                continue
                            avail = offer.get("availability", "")
                            if "InStock" in avail:
                                availability = "IN_STOCK"
                                break
                            elif "OutOfStock" in avail:
                                availability = "OUT_OF_STOCK"
                                break
                    if availability != "UNKNOWN":
                        break
                if availability != "UNKNOWN":
                    break

        # --- MEDIA ---
        media_items = self._build_media(nm_id, card_data, basket_host, html_resp)

        return {
            "fields": fields,
            "price_candidates": price_candidates,
            "media": {"items": media_items},
            "availability": availability,
            "category_path": category,
        }

    # ================================================================
    # MEDIA
    # ================================================================

    def _build_media(self, nm_id: int, card_data: Optional[dict],
                     basket_host_num: Optional[int], html_resp) -> list:
        items = []
        seen_urls = set()
        vol = nm_id // 100000
        part = nm_id // 1000

        # 1. Primary image
        if basket_host_num:
            host = f"basket-{basket_host_num:02d}.wbbasket.ru"
            primary_url = (f"https://{host}/vol{vol}/part{part}/{nm_id}"
                           f"/images/{WB_IMG_SIZE}/{WB_IMG_SIZE}.jpg")
            self._add_media(items, seen_urls, "image", primary_url)

        # 2. Из HTML
        if html_resp:
            html = html_resp.text
            m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                self._add_media(items, seen_urls, "image", m.group(1))

            for m in re.finditer(
                r'(?:src|data-src|data-original)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']',
                html, re.I
            ):
                img_url = m.group(1)
                if re.search(r'\.(mp4|webm)(\?[^"\']*)?$', img_url, re.I):
                    continue
                img_url = self._normalize_wb_image_url(img_url)
                self._add_media(items, seen_urls, "image", img_url)

            for m in re.finditer(
                r'(?:src|data-src|data-url)=["\']([^"\']*\.(?:mp4|webm)(?:\?[^"\']*)?)["\']',
                html, re.I
            ):
                self._add_media(items, seen_urls, "video", m.group(1))

        # 3. Log mismatch
        if card_data:
            media_info = card_data.get("media", {})
            if isinstance(media_info, dict):
                photo_count = media_info.get("photo_count", 0)
                has_video = media_info.get("has_video", False)
                found_photos = sum(1 for i in items if i["type"] == "image")
                if photo_count > found_photos:
                    log.info("WB %d: photo_count=%d, found=%d", nm_id, photo_count, found_photos)
                if has_video and not any(i["type"] == "video" for i in items):
                    log.info("WB %d: has_video=true, видео не найдено", nm_id)

        return items[:40]

    # ================================================================
    # HTML PARSING
    # ================================================================

    def _title_from_html(self, html: str) -> Optional[str]:
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        if m:
            t = m.group(1).replace(" - Wildberries", "").replace(" — Wildberries", "").strip()
            if t and len(t) > 3:
                return t
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    t = data.get("name") or data.get("title")
                    if t:
                        return str(t).strip()
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("name"):
                            return str(item["name"]).strip()
            except (json.JSONDecodeError, ValueError):
                continue
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
        if m:
            return m.group(1).strip()
        return None

    def _description_from_html(self, html: str) -> Optional[str]:
        m = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            return m.group(1)
        return None

    def _category_from_html(self, html: str) -> Optional[str]:
        m = re.search(r'<nav[^>]*class=["\'][^"\']*breadcrumb[^"\']*["\'][^>]*>(.*?)</nav>', html, re.I | re.DOTALL)
        if m:
            links = re.findall(r'>([^<]+)<', m.group(1))
            links = [l.strip() for l in links if l.strip() and l.strip() != "/"]
            if len(links) >= 2:
                return " > ".join(links[:4])
        return None

    def _extract_prices_from_html(self, html: str, candidates: list):
        m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            try:
                minor = int(round(float(m.group(1)) * 100))
                candidates.append({"role": "regular", "amount_minor": minor,
                                   "currency": "RUB", "label": "og:price"})
            except ValueError:
                pass
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
            try:
                minor = int(m.group(1))
                if minor > 100:
                    candidates.append({"role": "regular", "amount_minor": minor,
                                       "currency": "RUB", "label": "data-price"})
                    break
            except ValueError:
                pass
        # WB: embedded JSON state — "price": {"basic": 12345, ...}
        m = re.search(r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)', html)
        if m:
            try:
                minor = int(m.group(1))
                if minor > 100:
                    candidates.append({"role": "regular", "amount_minor": minor,
                                       "currency": "RUB", "label": "embedded_json.basic"})
            except ValueError:
                pass
        # WB: "priceU" / "priceD" в embedded state
        for pk in ("priceU", "priceD"):
            m = re.search(rf'"{pk}"\s*:\s*(\d+)', html)
            if m:
                try:
                    minor = int(m.group(1))
                    if minor > 100:
                        candidates.append({"role": "regular", "amount_minor": minor,
                                           "currency": "RUB", "label": f"embedded_json.{pk}"})
                        break
                except ValueError:
                    pass
        for m in re.finditer(r'>\s*([\d\s\u00a0]{2,10})\s*(?:₽|руб)\s*<', html):
            try:
                amount = float(m.group(1).replace(" ", "").replace("\u00a0", ""))
                if amount > 1:
                    candidates.append({"role": "regular",
                                       "amount_minor": int(amount * 100),
                                       "currency": "RUB", "label": "HTML text"})
                    break
            except ValueError:
                continue

        # JSON-LD offers (Product schema)
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
            try:
                data = json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("@type") != "Product":
                    continue
                offers = item.get("offers")
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
                                if minor > 0:
                                    candidates.append({
                                        "role": "regular",
                                        "amount_minor": minor,
                                        "currency": currency,
                                        "label": "JSON-LD offer",
                                    })
                            except (ValueError, TypeError):
                                pass

    # ================================================================
    # UTILITIES
    # ================================================================

    def _add_media(self, items: list, seen: set, mtype: str, url: str):
        if not url or url in seen:
            return
        safe = self.safe_media_url(url, "WB")
        if safe is None:
            if "wbbasket.ru" in url or "wbstatic.net" in url:
                safe = url
            else:
                return
        seen.add(safe)
        items.append({"type": mtype, "source_url": safe, "image_data": None})

    @staticmethod
    def _normalize_wb_image_url(url: str) -> str:
        m = re.search(r'(https://basket-\d+\.wbbasket\.ru/.*/images/)(S\d+)(\.\w+)', url)
        if m:
            return f"{m.group(1)}{WB_IMG_SIZE}{m.group(3)}"
        return url

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
        """Search WB via JSON API: search.wb.ru/exactmatch/ru/common/v4/search."""
        from urllib.parse import quote
        from datetime import datetime, timezone, timedelta

        self._warmup()

        # 2026-11-14: catalog.wb.ru → 200; search.wb.ru → 403; search.wildberries.ru → dead DNS
        # 2026-11-14: добавлены v5/v18 (подтверждены проектами 2025-26), u-search.wb.ru (Habr)
        # Приоритет: catalog.wb.ru (200) → search.wb.ru (403 без прокси) → u-search → wildberries
        _hosts = ["catalog.wb.ru", "search.wb.ru", "u-search.wb.ru"]
        _versions = ["v4", "v5", "v18"]
        _params = (f"query={quote(query)}&resultset=catalog&dest=-1257786"
                   f"&curr=rub&spp=30&appType=1&lang=ru&sort=popular&page={page}")
        search_urls = []
        for h in _hosts:
            for v in _versions:
                search_urls.append(f"https://{h}/exactmatch/ru/common/{v}/search?{_params}")
        # Перестановка: v4-catalog первым (проверен 200), затем v5-catalog, v18-catalog,
        # далее остальные хосты по версиям.
        # URL: https://{host}/exactmatch/ru/common/{ver}/search → split('/') idx: 2=host, 6=ver
        search_urls.sort(key=lambda u: (_hosts.index(u.split('/')[2]), _versions.index(u.split('/')[6])))
        # 2026-11-14 v5: limit 4 URLs + 25s global deadline (diag v4: warmup+6×(3+10)>40s)
        search_urls = search_urls[:4]
        _search_deadline = time.monotonic() + 25

        data = None
        last_status = None
        json_error = False
        for s_url in search_urls:
            if time.monotonic() > _search_deadline:
                log.info("WB search: global 30s deadline reached, stopping")
                break
            resp = self._http_get(s_url, headers=JSON_HEADERS, timeout=8, fast=True)
            if resp is None:
                continue
            status = getattr(resp, 'status_code', None)
            if status == 200:
                try:
                    data = resp.json()
                    break
                except (json.JSONDecodeError, ValueError):
                    json_error = True
                    continue
            if status in (403, 429):
                # WAF challenge: short delay + retry same URL once
                import time as _t
                log.info("WB search: HTTP %d, retrying in 2s", status)
                _t.sleep(2)
                resp2 = self._http_get(s_url, headers=JSON_HEADERS, timeout=10)
                if resp2 is not None:
                    status2 = getattr(resp2, 'status_code', None)
                    if status2 == 200:
                        try:
                            data = resp2.json()
                            break
                        except (json.JSONDecodeError, ValueError):
                            json_error = True
                            continue
                    last_status = status2
                else:
                    last_status = status
                continue
            last_status = status
            log.warning("WB search: HTTP %s for %s", status, s_url.split("?")[0])

        if data is None:
            if json_error:
                raise SearchError("SEARCH_INVALID_JSON")
            elif last_status is not None:
                raise SearchError(f"SEARCH_HTTP_{last_status}")
            else:
                raise SearchError("SEARCH_HTTP_UNAVAILABLE")

        # Shape validation: normal = {metadata, products, total};
        # "quiet" 200 = {state, version, params, data:{products:[...]}} — retryable.
        products = []
        if isinstance(data, dict):
            if "metadata" in data and "products" in data:
                products = data.get("products") or []
            elif "data" in data and isinstance(data["data"], dict):
                products = data["data"].get("products") or []
                # If shape is the "quiet" variant, log it
                if "state" in data or "version" in data:
                    log.info("WB search: quiet-200 shape detected, got %d products", len(products))
            else:
                # Fallback: maybe top-level list or items
                products = data.get("products") or data.get("items") or []
        if not products and isinstance(data, list):
            products = data

        log.info("WB search: %d products in response for %r", len(products), query[:40])

        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        results = []

        for prod in products[:page_size]:
            if not isinstance(prod, dict):
                continue
            nm_id = str(prod.get("id") or prod.get("nm_id") or "")
            if not nm_id:
                continue

            title = prod.get("name") or prod.get("title") or f"WB-{nm_id}"
            brand = prod.get("brand") or ""

            # Preserve each API price and its size, without choosing a cheapest variant.
            price_inputs = [("price", prod.get("price"), None)]
            sizes = prod.get("sizes")
            for index, size in enumerate(sizes if isinstance(sizes, list) else []):
                if isinstance(size, dict):
                    price_inputs.append((f"sizes[{index}].price", size.get("price"),
                                         size.get("chrtId") or size.get("name")))
            prices = []
            for path, values, variant in price_inputs:
                values = values if isinstance(values, dict) else {"value": values}
                for key in ("product", "basic", "sale", "value"):
                    value = values.get(key)
                    if value is None or isinstance(value, bool):
                        continue
                    try:
                        minor = Decimal(str(value))
                        if (not minor.is_finite() or minor <= 0 or minor.adjusted() > 18
                                or minor != minor.to_integral_value()):
                            continue
                        minor = int(minor)
                    except (InvalidOperation, ValueError, OverflowError):
                        continue
                    prices.append({
                        "role": "unknown", "amount_minor": minor,
                        "amount": f"{minor // 100}.{minor % 100:02d}", "currency": "RUB",
                        "visible_label": f"{minor // 100}.{minor % 100:02d} руб.",
                        "variant_id": str(variant) if variant is not None else None,
                        "variant_association": "UNKNOWN", "usable_purchase_price": False,
                        "evidence": {"method": "SEARCH_API", "source": self.source,
                                     "path": f"{path}.{key}", "value_minor": minor,
                                     "listing_id": nm_id, "variant_id": variant},
                    })

            # Images
            images = []
            img_list = prod.get("images") or prod.get("photos") or []
            if isinstance(img_list, list):
                for img in img_list[:10]:
                    if isinstance(img, str) and img.startswith("http"):
                        images.append(img)
                    elif isinstance(img, dict):
                        u = img.get("url") or img.get("src") or ""
                        if u and u.startswith("http"):
                            images.append(u)

            obs = {"fields": {}, "price_candidates": prices, "media": {"items": []},
                   "availability": "UNKNOWN",
                   "category_path": None}

            obs["fields"]["title"] = {
                "status": "KNOWN", "value": title[:300],
                "method": "SEARCH_API", "evidence": {"method": "SEARCH_API", "source": self.source},
            }
            if brand:
                obs["fields"]["brand"] = {
                    "status": "KNOWN", "value": brand,
                    "method": "SEARCH_API", "evidence": {"method": "SEARCH_API", "source": self.source},
                }
            for img in images[:10]:
                obs["media"]["items"].append({"type": "image", "source_url": img, "image_data": None})

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
                    "id": nm_id,
                    "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                    "observed_at": now,
                    "status": "PARTIAL_SUCCESS",
                    "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                    "http_status": 200,
                },
            })

        log.info("WB: collect_by_search %r → %d products", query[:40], len(results))
        return results

    # ================================================================
    # parse_page (fallback)
    # ================================================================

    def parse_page(self, resp, listing_id, url, title_hint):
        html = resp.text
        if not html or len(html) < 200:
            log.warning("WB %s: короткий ответ (%dB)", listing_id, len(html or ""))
            return None
        nm_id = int(listing_id)
        return self._merge_observations(nm_id, None, None, resp, None, title_hint)
