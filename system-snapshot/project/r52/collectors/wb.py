"""Сборщик Wildberries."""
import json
import logging
import os
import random
import re
import time
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Optional

import requests as _requests
from .base import BaseCollector
from .discovery import SearchError
from .pw_fetch import PwFetcher

log = logging.getLogger("r52.collectors.wb")

WB_HOSTS = ("wildberries.ru", "www.wildberries.ru")
WB_CDN = ("wbbasket.ru", "wbstatic.net", "wildberries.ru")
CARD_URL_RE = re.compile(r"https://(?:www\.)?wildberries\.ru/catalog/(\d+)/detail\.aspx")

WB_DEST = os.environ.get("R52_WB_DEST", "-1257786")
WB_SPP = os.environ.get("R52_WB_SPP", "30")

_basket_host_cache: dict[int, int] = {}

JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}
_PROBE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

class WBCollector(BaseCollector):
    source = "WB"
    title = "Wildberries"

    def __init__(self, data_dir=None, use_proxies: bool = True):
        super().__init__(data_dir=data_dir, use_proxies=use_proxies)
        self._cffi = None
        try:
            from curl_cffi import requests as cffi_req
            self._cffi = cffi_req.Session(impersonate="chrome")
        except ImportError:
            pass
        self._warmed_up = False
        self._pw: Optional[PwFetcher] = None
        self._pw_direct: Optional[PwFetcher] = None

    def _warmup(self):
        if self._warmed_up: return
        try:
            self._throttle()
            proxies = self.pool.get_proxy()
            if proxies is None and self.pool.use_proxies:
                self._warmed_up = True
                return
            headers = {"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"}
            for wurl in ["https://www.wildberries.ru/", "https://catalog.wb.ru/"]:
                try:
                    if self._cffi: self._cffi.get(wurl, timeout=5, proxies=proxies, headers=headers)
                    else: self.session.get(wurl, proxies=proxies, timeout=(3, 5), headers=headers)
                except Exception: pass
        except Exception: pass
        self._warmed_up = True

    def build_url(self, listing_id: str) -> str:
        return f"https://www.wildberries.ru/catalog/{listing_id}/detail.aspx"

    def _get_pw_direct(self) -> Optional[PwFetcher]:
        if self._pw_direct is None:
            self._pw_direct = PwFetcher(proxies=None, require_proxy=False)
            if not self._pw_direct.start():
                self._pw_direct = None
        return self._pw_direct

    def close(self):
        if self._pw_direct: self._pw_direct.stop(); self._pw_direct = None

    def _fast_throttle(self):
        now = time.monotonic()
        elapsed = now - self._last_request
        wait = random.uniform(0.5, 1.5) - elapsed
        if wait > 0: time.sleep(wait)
        self._last_request = time.monotonic()

    def _http_get(self, url: str, headers: Optional[dict] = None, timeout: int = 15, fast: bool = False, direct: bool = False) -> Optional[object]:
        if fast: self._fast_throttle()
        else: self._throttle()

        proxies = None if direct else self.pool.get_proxy()
        if proxies is None and self.pool.use_proxies and not direct:
            return None

        if self._cffi:
            try: return self._cffi.get(url, timeout=timeout, proxies=proxies, headers=headers or {})
            except Exception: return None
        else:
            try: return self.session.get(url, headers=headers or {}, proxies=proxies, timeout=(5, timeout))
            except Exception: return None

    def _fetch_card_json(self, nm_id: int) -> Optional[dict]:
        vol = nm_id // 100000
        part = nm_id // 1000
        path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        host = self._find_basket_host(nm_id, vol, path)
        if host is None: return None
        resp = self._http_get(f"https://{host}{path}", headers=JSON_HEADERS, timeout=10, fast=True, direct=True)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get("nm_id"): return data
            except ValueError: pass
        return None

    def _find_basket_host(self, nm_id: int, vol: int, path: str) -> Optional[str]:
        if vol in _basket_host_cache:
            return f"basket-{_basket_host_cache[vol]:02d}.wbbasket.ru"

        candidates = list(range(1, 31))
        if vol >= 61 and vol not in candidates: candidates.append(vol)

        def probe(n: int) -> Optional[int]:
            try:
                r = _requests.get(f"https://basket-{n:02d}.wbbasket.ru{path}", headers=JSON_HEADERS, timeout=3)
                if r.status_code == 200 and r.json().get("nm_id"): return n
            except Exception: pass
            return None

        found = None
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(probe, n): n for n in candidates}
            for fut in as_completed(futures):
                if fut.result() is not None:
                    found = fut.result()
                    for f in futures:
                        if not f.done(): f.cancel()
                    break
        if found:
            _basket_host_cache[vol] = found
            return f"basket-{found:02d}.wbbasket.ru"
        return None

    def _collect_via_pw(self, nm_id: int, url: str):
        pw = self._get_pw_direct()
        if not pw: return None, None, None, None, []
        # Ускорили таймауты в 3 раза, чтобы не висеть 20 секунд на каждом товаре
        xhr = pw.get_xhr(url, patterns=["card.wb.ru", "basket", "info/ru/card.json", "search.wb.ru", "__internal/u-card"],
                         extra_wait_ms=2000, xhr_timeout_ms=5000, catch_json=True, min_json=300)
        card_data, price_data, media_from_pw, basket_host, html_resp = None, None, [], None, None

        if xhr and xhr.meta.get("xhr_bodies"):
            for u, txt in xhr.meta["xhr_bodies"]:
                if "wbbasket.ru" in u and "/info/ru/card.json" in u:
                    m = re.search(r"https://basket-(\d+)\.wbbasket\.ru", u)
                    if m: basket_host = int(m.group(1))
                try: d = json.loads(txt)
                except ValueError: continue
                
                if isinstance(d, dict):
                    if str(d.get("nm_id")) == str(nm_id):
                        card_data = d
                    elif "products" in d and d["products"]:
                        p = d["products"][0]
                        price_data = {"name": p.get("name"), "brand": p.get("brand"), "sizes": p.get("sizes", []), "source": "pw_xhr"}
                        for size in p.get("sizes", []):
                            if size.get("price"): price_data["prices"] = size["price"]; price_data["qty"] = size.get("qty"); break

        if not card_data or not price_data:
            pw_result = pw.get(url, extra_wait_ms=2000)
            if pw_result and pw_result.html:
                html_resp = SimpleNamespace(text=pw_result.html, status_code=200)
                m = re.search(r'basket-(\d+)\.wbbasket\.ru', pw_result.html)
                if m: basket_host = int(m.group(1))
        return card_data, price_data, basket_host, html_resp, media_from_pw

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        nm_id = int(listing_id)
        self._warmup()
        card_data = self._fetch_card_json(nm_id)
        price_data = self._fetch_price(nm_id)
        html_resp = None

        if not card_data or not price_data:
            pw_card, pw_price, pw_host, pw_html, _ = self._collect_via_pw(nm_id, url)
            if pw_card: card_data = pw_card
            if pw_price: price_data = pw_price
            if pw_host is not None: _basket_host_cache[nm_id // 100000] = pw_host
            if pw_html: html_resp = pw_html

        if not html_resp: html_resp = self._fetch_html(url)
        basket_host = _basket_host_cache.get(nm_id // 100000)
        observation = self._merge_observations(nm_id, card_data, price_data, html_resp, basket_host, title_hint, [])
        
        if observation is None: return None
        self._ensure_evidence(observation); self._ensure_price_amounts(observation); self._ensure_availability_in_fields(observation)

        has_title = bool(observation["fields"].get("title", {}).get("value", "")) and observation["fields"]["title"]["value"] != f"WB-{nm_id}"
        has_price = len(observation.get("price_candidates", [])) > 0
        has_media = len(observation.get("media", {}).get("items", [])) > 0
        status = "SUCCESS" if (has_title and has_price and has_media) else ("PARTIAL_SUCCESS" if (has_title or has_price) else "DEGRADED")

        from datetime import datetime, timezone, timedelta
        return {"format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS", "capture_action": "COLLECTOR_RUN", "fully_autonomous": True, "collector_source": self.source,
                "observation": {**observation, "source": self.source, "id": str(listing_id), "url": url, "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                                "status": status, "product_content": True, "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP", "http_status": 200}}

    def _fetch_price(self, nm_id: int) -> Optional[dict]:
        params = f"appType=1&curr=rub&dest={WB_DEST}&spp={WB_SPP}&nm={nm_id}"
        for url in [f"https://card.wb.ru/cards/v4/detail?{params}", f"https://catalog.wb.ru/cards/v4/detail?{params}"]:
            resp = self._http_get(url, headers=JSON_HEADERS, timeout=12, fast=True)
            if resp is not None and resp.status_code == 200:
                try:
                    data = resp.json(); products = data.get("products") or data.get("data", {}).get("items", [])
                    if products:
                        p = products[0]; result = {"name": p.get("name"), "brand": p.get("brand"), "sizes": p.get("sizes", []), "source": "card_v4"}
                        for size in p.get("sizes", []):
                            if size.get("price"): result["prices"] = size["price"]; result["qty"] = size.get("qty"); break
                        return result
                except ValueError: pass
        return None

    def _fetch_html(self, url: str):
        resp = self._http_get(url, headers=HTML_HEADERS, timeout=20)
        if resp is not None and resp.status_code == 200: return resp
        return None

    def _merge_observations(self, nm_id: int, card_data: Optional[dict], price_data: Optional[dict], html_resp, basket_host: Optional[int], title_hint: str, media_from_pw: list) -> Optional[dict]:
        fields, price_candidates, availability, category = {}, [], "UNKNOWN", None
        
        raw_title = card_data.get("imt_name") if card_data else (price_data.get("name") if price_data else (self._title_from_html(html_resp.text) if html_resp else None))
        title = html.unescape(raw_title).strip() if raw_title else title_hint or f"WB-{nm_id}"
        fields["title"] = {"status": "KNOWN", "value": title, "method": "CARD_JSON"}

        if card_data:
            seller = card_data.get("supplier") or card_data.get("selling", {}).get("supplier_name")
            if seller: fields["seller"] = {"status": "KNOWN", "value": seller, "method": "CARD_JSON"}
            seller_rating = card_data.get("supplierRating")
            if seller_rating: fields["seller_rating"] = {"status": "KNOWN", "value": str(seller_rating), "method": "CARD_JSON"}
            item_rating = card_data.get("reviewRating")
            if item_rating: fields["item_rating"] = {"status": "KNOWN", "value": str(item_rating), "method": "CARD_JSON"}
            item_reviews = card_data.get("feedbacks")
            if item_reviews: fields["item_reviews"] = {"status": "KNOWN", "value": str(item_reviews), "method": "CARD_JSON"}

        brand = card_data.get("selling", {}).get("brand_name") if card_data else (price_data.get("brand") if price_data else None)
        if brand: fields["brand"] = {"status": "KNOWN", "value": str(brand), "method": "CARD_JSON"}

        description = card_data.get("description") if card_data else (self._description_from_html(html_resp.text) if html_resp else None)
        if description: fields["description"] = {"status": "KNOWN", "value": html.unescape(description)[:3000], "method": "CARD_JSON"}

        if card_data:
            subj = card_data.get("subj_name", ""); subj_root = card_data.get("subj_root_name", "")
            if subj and subj_root: category = f"{subj_root} > {subj}"
            elif subj: category = subj
        if category: fields["category"] = {"status": "KNOWN", "value": category, "method": "CARD_JSON"}

        if price_data and price_data.get("prices"):
            role_map = {"basic": "regular", "product": "regular", "card": "card", "wallet": "wallet", "old": "old", "crossed": "old", "bankCard": "card"}
            for key, val in price_data["prices"].items():
                if isinstance(val, (int, float)) and val > 0:
                    price_candidates.append({"role": role_map.get(key, "regular"), "amount_minor": int(val), "currency": "RUB", "label": f"price_api.{key}"})

        def _wb_price_val(pv):
            if isinstance(pv, (int, float)) and pv > 0: return int(pv)
            if isinstance(pv, list) and pv and isinstance(pv[0], dict): return int(pv[0].get("p") or pv[0].get("price") or 0) or None
            return None

        if not price_candidates and card_data:
            selling = card_data.get("selling", {})
            if isinstance(selling, dict):
                for pk in ("price", "priceU", "priceD", "oldPrice"):
                    val = _wb_price_val(selling.get(pk))
                    if val: price_candidates.append({"role": "regular" if pk in ("price", "priceU") else "old", "amount_minor": val, "currency": "RUB", "label": f"card_json.selling.{pk}"})
            for pk in ("price", "priceU", "priceD"):
                val = _wb_price_val(card_data.get(pk))
                if val: price_candidates.append({"role": "regular", "amount_minor": val, "currency": "RUB", "label": f"card_json.{pk}"})

        if availability == "UNKNOWN" and card_data:
            for size in card_data.get("sizes", []):
                if isinstance(size, dict) and isinstance(size.get("qty"), (int, float)):
                    availability = "IN_STOCK" if size["qty"] > 0 else "OUT_OF_STOCK"; break

        if price_data and price_data.get("qty") is not None:
            availability = "IN_STOCK" if price_data["qty"] > 0 else "OUT_OF_STOCK"

        media_items = self._build_media(nm_id, card_data, basket_host, html_resp, media_from_pw)
        return {"fields": fields, "price_candidates": self._dedupe_prices(price_candidates), "media": {"items": media_items}, "availability": availability, "category_path": category}

    def _build_media(self, nm_id: int, card_data: Optional[dict], basket_host_num: Optional[int], html_resp, media_from_pw: list) -> list:
        items, seen_urls = [], set()
        for url in media_from_pw: 
            self._add_media(items, seen_urls, "image", url)
            
        if html_resp:
            for m in re.finditer(r'(?:src|data-src|srcset)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']', html_resp.text, re.I):
                img_url = m.group(1).split(",")[0].strip().split(" ")[0]
                self._add_media(items, seen_urls, "image", self._normalize_wb_image_url(img_url))
                
        if not items and basket_host_num and card_data:
            pics_count = card_data.get("pics", 0)
            if pics_count > 0:
                vol, part = nm_id // 100000, nm_id // 1000
                for i in range(1, min(pics_count + 1, 10)):
                    self._add_media(items, seen_urls, "image", f"https://basket-{basket_host_num:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{i}.webp")
                    self._add_media(items, seen_urls, "image", f"https://basket-{basket_host_num:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{i}.jpg")
        return items[:40]

    def _title_from_html(self, html: str) -> Optional[str]:
        m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        return m.group(1).strip() if m else None

    def _description_from_html(self, html: str) -> Optional[str]:
        m = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)', html, re.I)
        return m.group(1) if m else None

    def _add_media(self, items: list, seen: set, mtype: str, url: str):
        if not url: return
        clean_url = url.split("?")[0].split("#")[0]
        if "wbbasket.ru" in clean_url:
            base_key = re.sub(r'\.(jpg|webp)$', '', clean_url)
            if base_key not in seen:
                seen.add(base_key)
                items.append({"type": mtype, "source_url": clean_url, "image_data": None})

    @staticmethod
    def _normalize_wb_image_url(url: str) -> str:
        if url.startswith("//"): url = "https:" + url
        m = re.search(r'(https://basket-\d+\.wbbasket\.ru/.*/images/)(S\d+|big|c\d+x\d+)(\.\w+)', url)
        return f"{m.group(1)}big{m.group(3)}" if m else url

    @staticmethod
    def _dedupe_prices(candidates: list) -> list:
        seen, result = set(), []
        for c in candidates:
            key = (c["role"], c["amount_minor"], c["currency"])
            if key not in seen and c["amount_minor"] > 0: seen.add(key); result.append(c)
        return result

    def collect_by_search(self, query: str, page: int = 1, page_size: int = 20) -> list[dict]:
        from urllib.parse import quote
        from datetime import datetime, timezone, timedelta
        self._warmup()

        search_urls = [f"https://{h}/exactmatch/ru/common/v4/search?query={quote(query)}&resultset=catalog&dest={WB_DEST}&curr=rub&spp={WB_SPP}&appType=1&lang=ru&sort=popular&page={page}" for h in ["catalog.wb.ru", "search.wb.ru"]]
        data, last_status = None, None

        for s_url in search_urls:
            resp = self._http_get(s_url, headers=JSON_HEADERS, timeout=5, fast=True)
            if resp is not None and getattr(resp, 'status_code', None) == 200:
                try: data = resp.json(); break
                except ValueError: pass
            last_status = getattr(resp, 'status_code', None) if resp else None

        if data is None:
            pw = self._get_pw_direct()
            if pw:
                try:
                    xhr = pw.get_xhr(f"https://www.wildberries.ru/catalog/0/ru/?search={quote(query)}&page={page}", patterns=["__internal/", "search"], extra_wait_ms=5000, xhr_timeout_ms=20000, product_key="products")
                    if xhr and xhr.html:
                        xj = json.loads(xhr.html)
                        xprods = xj.get("products") or (xj.get("data", {}).get("products") if isinstance(xj.get("data"), dict) else None)
                        if xprods: data = {"products": xprods}
                except Exception: pass
            if data is None: raise SearchError(f"SEARCH_HTTP_{last_status}" if last_status else "SEARCH_HTTP_UNAVAILABLE")

        products = data.get("products") or (data.get("data", {}).get("products") if isinstance(data.get("data"), dict) else []) or []
        results = []
        now = datetime.now(timezone(timedelta(hours=10))).isoformat()
        
        valid_products = []
        for prod in products[:page_size]:
            if not isinstance(prod, dict): continue
            nm_id = str(prod.get("id") or prod.get("nm_id") or "")
            if not nm_id: continue
            raw_title = prod.get("name") or prod.get("title") or ""
            title = html.unescape(raw_title).strip()
            if not title or title.startswith("WB-") or "не найден" in title.lower():
                continue
            valid_products.append((nm_id, title, prod))

        cards_data = {}
        def fetch_card(item):
            nm = int(item[0])
            return nm, self._fetch_card_json(nm)
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(fetch_card, item): item for item in valid_products}
            for future in as_completed(futures):
                nm, card = future.result()
                if card:
                    cards_data[nm] = card

        for nm_id, title, prod in valid_products:
            prices = []
            for vals in [prod.get("price")] + [s.get("price") for s in (prod.get("sizes") or []) if isinstance(s, dict)]:
                if not vals: continue
                vals = vals if isinstance(vals, dict) else {"value": vals}
                for key in ("product", "basic", "sale", "value"):
                    val = vals.get(key)
                    if val is None or isinstance(val, bool): continue
                    try:
                        minor = Decimal(str(val))
                        if minor.is_finite() and minor > 0 and minor == minor.to_integral_value():
                            minor = int(minor)
                            prices.append({"role": "unknown", "amount_minor": minor, "amount": f"{minor // 100}.{minor % 100:02d}", "currency": "RUB", "variant_association": "UNKNOWN", "evidence": {"method": "SEARCH_API"}})
                    except Exception: pass

            obs = {"fields": {"title": {"status": "KNOWN", "value": title[:300], "method": "SEARCH_API", "evidence": {"method": "SEARCH_API"}}}, 
                   "price_candidates": prices, "media": {"items": []}, "availability": "UNKNOWN"}
            if prod.get("brand"): obs["fields"]["brand"] = {"status": "KNOWN", "value": html.unescape(prod["brand"]), "method": "SEARCH_API", "evidence": {"method": "SEARCH_API"}}
            
            card_data = cards_data.get(int(nm_id))
            if card_data:
                seller = card_data.get("supplier")
                if seller: obs["fields"]["seller"] = {"status": "KNOWN", "value": seller, "method": "CARD_JSON", "evidence": {"method": "CARD_JSON"}}
                sr = card_data.get("supplierRating")
                if sr: obs["fields"]["seller_rating"] = {"status": "KNOWN", "value": str(sr), "method": "CARD_JSON", "evidence": {"method": "CARD_JSON"}}
                ir = card_data.get("reviewRating")
                if ir: obs["fields"]["item_rating"] = {"status": "KNOWN", "value": str(ir), "method": "CARD_JSON", "evidence": {"method": "CARD_JSON"}}
                ifv = card_data.get("feedbacks")
                if ifv: obs["fields"]["item_reviews"] = {"status": "KNOWN", "value": str(ifv), "method": "CARD_JSON", "evidence": {"method": "CARD_JSON"}}

                vol, part = int(nm_id) // 100000, int(nm_id) // 1000
                basket_host = _basket_host_cache.get(vol)
                pics_count = card_data.get("pics", 0)
                
                if basket_host and pics_count > 0:
                    seen_urls = set()
                    for i in range(1, min(pics_count + 1, 10)):
                        u = f"https://basket-{basket_host:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{i}.webp"
                        if u not in seen_urls:
                            seen_urls.add(u)
                            obs["media"]["items"].append({"type": "image", "source_url": u})

            if not obs["media"]["items"]:
                seen_urls = set()
                for img in (prod.get("images") or prod.get("photos") or [])[:10]:
                    u = img if isinstance(img, str) else img.get("url") if isinstance(img, dict) else None
                    if u:
                        u = self._normalize_wb_image_url(u)
                        if u not in seen_urls:
                            seen_urls.add(u)
                            obs["media"]["items"].append({"type": "image", "source_url": u})

            self._ensure_evidence(obs); self._ensure_price_amounts(obs); self._ensure_availability_in_fields(obs)
            results.append({"format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS", "capture_action": "COLLECTOR_RUN", "fully_autonomous": True, "collector_source": self.source,
                            "observation": {**obs, "source": self.source, "id": nm_id, "listing_id": nm_id, "title": title, "url": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx", "observed_at": now, "status": "PARTIAL_SUCCESS", "product_content": True, "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP", "http_status": 200}})

        return results

    def parse_page(self, resp, listing_id, url, title_hint):
        return self._merge_observations(int(listing_id), None, None, resp, None, title_hint, [])