"""Базовый класс сборщика: HTTP-запросы через прокси, rate limiting, ретраи.

Сборщик работает в subprocess (см. runner.py). Результат — список dict
наблюдений в формате, совместимом с intake/eye.py.
"""

import json
import logging
import random
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .proxy_pool import ProxyPool, CONNECT_TIMEOUT, READ_TIMEOUT

log = logging.getLogger("r52.collectors")

# Реалистичные заголовки (обновлять при смене браузеров)
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Rate limiting: минимальная пауза между запросами к одному источнику (сек)
MIN_DELAY = 8
MAX_DELAY = 20
# Ретраи при 429/5xx
MAX_RETRIES = 3
BACKOFF_BASE = 10  # сек


class BaseCollector:
    """Базовый сборщик. Подклассы реализуют parse_page()."""

    source: str = ""
    title: str = ""

    def __init__(self, data_dir: Optional[Path] = None, use_proxies: bool = True):
        self.pool = ProxyPool(data_dir=data_dir, use_proxies=use_proxies)
        self.session = self._make_session()
        self._last_request = 0.0
        self.stats = {"requests": 0, "success": 0, "failed": 0, "blocked": 0}

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_BASE / 10,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update(BASE_HEADERS)
        s.headers["User-Agent"] = random.choice(UA_LIST)
        return s

    def get_locale_headers(self) -> dict:
        """Дополнительные заголовки для принудительной локали.

        Переопределяется в подклассах (напр. AliExpress — cookie aep_usuc_f).
        По умолчанию — пусто.
        """
        return {}

    def _throttle(self):
        """Пауза между запросами: MIN_DELAY..MAX_DELAY сек + Gaussian джиттер (σ=2с).

        Gaussian-шум имитирует человеческий темп: интервалы не идеально равномерные,
        что снижает детект machine-perfect timing (Layer 3 anti-bot).
        """
        now = time.monotonic()
        elapsed = now - self._last_request
        base_delay = random.uniform(MIN_DELAY, MAX_DELAY)
        noise = random.gauss(0, 2.0)
        wait = base_delay + noise - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def fetch(self, url: str) -> Optional[requests.Response]:
        """GET с прокси, rate limit, ретраем. Возвращает Response или None."""
        self._throttle()
        proxies = self.pool.get_proxy()
        try:
            start = time.monotonic()
            resp = self.session.get(url, proxies=proxies, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            latency = time.monotonic() - start
            self.stats["requests"] += 1

            if resp.status_code == 200:
                self.stats["success"] += 1
                self.pool.mark_success(latency)
                return resp
            elif resp.status_code in (403, 429):
                self.stats["blocked"] += 1
                self.pool.mark_failure()
                # Ретраи с backoff
                for attempt in range(MAX_RETRIES):
                    delay = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 5)
                    log.info("%s: HTTP %d, retry %d/%d через %.0fс", self.source, resp.status_code, attempt + 1, MAX_RETRIES, delay)
                    time.sleep(delay)
                    self._throttle()
                    proxies = self.pool.get_proxy()
                    resp = self.session.get(url, proxies=proxies, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
                    if resp.status_code == 200:
                        self.stats["success"] += 1
                        self.pool.mark_success(time.monotonic() - start)
                        return resp
                    if resp.status_code in (403, 429):
                        self.pool.mark_failure()
                self.stats["failed"] += 1
                return None
            elif resp.status_code == 404:
                self.stats["failed"] += 1
                return None
            else:
                log.warning("%s: HTTP %d для %s", self.source, resp.status_code, url)
                self.stats["failed"] += 1
                return None
        except (requests.RequestException, OSError) as e:
            self.stats["requests"] += 1
            self.stats["failed"] += 1
            self.pool.mark_failure()
            log.warning("%s: ошибка сети %s: %s", self.source, url, e)
            return None

    def collect_product(self, listing_id: str, url: str, title_hint: str = "") -> Optional[dict]:
        """Собирает одну карточку. Возвращает dict наблюдения или None."""
        resp = self.fetch(url)
        if resp is None:
            return None
        try:
            observation = self.parse_page(resp, listing_id, url, title_hint)
        except Exception as e:
            log.exception("%s: ошибка парсинга %s: %s", self.source, listing_id, e)
            return None
        if observation is None:
            return None

        # Пост-обработка: совместимость с intake/eye.py + normalize.py
        self._ensure_evidence(observation)
        self._ensure_price_amounts(observation)
        self._ensure_availability_in_fields(observation)

        # Обёртка в формат автономного сборщика (JARVIS_BR02_COLLECTOR_AUTONOMOUS)
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

    # ================================================================
    # Мостовой метод: Protocol Collector.collect → collect_product
    # ================================================================

    def collect(self, app, ctx, params) -> dict:
        """Собирает один товар. Вызывается из handlers._run_collect.

        params: {product_id: int|None, source: str|None}
        Возвращает сводку-dict для task result.
        """
        product_id = params.get("product_id")
        if product_id is None:
            return {"status": "NO_PRODUCT", "collected": 0}

        with app.db.read() as conn:
            row = conn.execute(
                "SELECT id, title, source, url FROM product WHERE id = ?",
                (product_id,),
            ).fetchone()

        if row is None:
            return {"status": "NOT_FOUND", "product_id": product_id, "collected": 0}

        url = row["url"] or ""
        title = row["title"] or ""
        src = row["source"] or self.source

        listing_id = self._extract_listing_id(url)
        if not listing_id:
            return {
                "status": "NO_LISTING_ID",
                "source": src,
                "url": url,
                "collected": 0,
            }

        log.info("%s: collect pid=%s listing_id=%s", self.source, product_id, listing_id)

        obs = self.collect_product(listing_id, url, title)

        # Записываем в папку приёма (FolderWatcher подхватит)
        written = []
        if obs:
            output_dir = Path(app.paths.home) / "inbox"
            written = self.write_observations([obs], output_dir)

        self.close()
        return {
            "status": "OK" if obs else "NO_DATA",
            "source": src,
            "product_id": product_id,
            "listing_id": listing_id,
            "collected": 1 if obs else 0,
            "files": [Path(w).name for w in written],
            "stats": self.stats,
        }

    def _extract_listing_id(self, url: str) -> Optional[str]:
        """Извлекает числовой listing_id из URL товара."""
        if not url:
            return None
        patterns = [
            r"/catalog/(\d+)/",
            r"/product/(\d+)",
            r"/ad/(\d+)",
            r"/item/(\d+)",
            r"/(\d{8,})",
            r"/(\d{6,})",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        # Fallback: последний числовой сегмент URL
        parts = url.rstrip("/").split("/")
        for part in reversed(parts):
            if part.isdigit() and len(part) >= 6:
                return part
        return None

    def _ensure_evidence(self, observation: dict):
        """Добавляет evidence в поля, если коллектор его не поставил.

        normalize.py _known() требует: status=='KNOWN' AND evidence (truthy) AND value.
        """
        fields = observation.get("fields")
        if not isinstance(fields, dict):
            return
        for key, item in fields.items():
            if isinstance(item, dict) and item.get("status") == "KNOWN" and not item.get("evidence"):
                item["evidence"] = {"method": item.get("method", "COLLECTOR"), "source": self.source}

    def _ensure_price_amounts(self, observation: dict):
        """Добавляет amount (полная цена) и visible_label в price_candidates.

        normalize.py _prices() читает candidate.get("amount") → parse_amount() → to_minor().
        Коллекторы пишут amount_minor (копейки); добавляем amount для совместимости.
        """
        candidates = observation.get("price_candidates")
        if not isinstance(candidates, list):
            return
        for c in candidates:
            if not isinstance(c, dict):
                continue
            if "amount" not in c and c.get("amount_minor") is not None:
                minor = int(c["amount_minor"])
                c["amount"] = f"{minor // 100}.{minor % 100:02d}"
            if "visible_label" not in c and c.get("label"):
                c["visible_label"] = c["label"]

    def _ensure_availability_in_fields(self, observation: dict):
        """Переносит top-level availability в fields, если коллектор не поставил.

        normalize_eye ищет availability в fields, а коллекторы кладут на верхний уровень.
        """
        avail = observation.get("availability")
        if not avail or avail == "UNKNOWN":
            return
        fields = observation.setdefault("fields", {})
        if "availability" not in fields:
            fields["availability"] = {
                "status": "KNOWN",
                "value": avail,
                "method": "COLLECTOR",
                "evidence": {"method": "COLLECTOR", "source": self.source},
            }

    def parse_page(self, resp: requests.Response, listing_id: str, url: str, title_hint: str) -> Optional[dict]:
        """Подклассы переопределяют. Возвращает dict с fields, price_candidates, media.

        Ожидаемая структура observation (после _ensure_evidence / _ensure_price_amounts):
        {
            "fields": {key: {"status": "KNOWN", "value": ..., "method": ..., "evidence": {...}}},
            "price_candidates": [{"role": ..., "amount": "123.45", "amount_minor": 12345,
                                  "currency": "RUB", "visible_label": ...}],
            "media": {"items": [{"type": "image|video", "source_url": ..., "image_data": None}]},
            "availability": "IN_STOCK|OUT_OF_STOCK|UNKNOWN",
            "category_path": str or None,
        }
        """
        raise NotImplementedError

    # --- Утилиты парсинга ---

    @staticmethod
    def extract_json_ld(html: str) -> list[dict]:
        """Достаёт все JSON-LD блоки."""
        blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
        results = []
        for block in blocks:
            try:
                data = json.loads(block.strip())
                if isinstance(data, list):
                    results.extend(data)
                elif isinstance(data, dict):
                    results.append(data)
            except (json.JSONDecodeError, ValueError):
                continue
        return results

    @staticmethod
    def find_product_jsonld(blocks: list[dict]) -> Optional[dict]:
        """Находит Product из JSON-LD."""
        def walk(v, depth=0):
            if not v or depth > 6:
                return None
            if isinstance(v, list):
                for item in v:
                    r = walk(item, depth + 1)
                    if r:
                        return r
                return None
            if isinstance(v, dict):
                types = v.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "Product" in types:
                    return v
                if "@graph" in v:
                    return walk(v["@graph"], depth + 1)
            return None
        for block in blocks:
            r = walk(block)
            if r:
                return r
        return None

    @staticmethod
    def parse_price_text(text: str) -> Optional[tuple[int, str]]:
        """Извлекает цену в копейках и валюту из строки."""
        if not text:
            return None
        m = re.search(r'(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(₽|руб|руб\.?|RUB|₴|грн)', text, re.IGNORECASE)
        if not m:
            return None
        amount_str = m[1].replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
        try:
            amount = float(amount_str)
            minor = int(round(amount * 100))
        except ValueError:
            return None
        currency = "RUB" if m[2] in ("₽", "руб", "руб.", "RUB") else ("UAH" if m[2] in ("₴", "грн") else "RUB")
        return (minor, currency)

    @staticmethod
    def safe_media_url(url: str, source: str) -> Optional[str]:
        """Проверяет URL медиа на допустимость (CDN источника)."""
        if not url or not url.startswith("https://"):
            return None
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            allowed = {
                "WB": ("wbbasket.ru", "wbstatic.net", "wildberries.ru"),
                "OZON": ("ozone.ru", "ozon.ru", "ozoncdn.net"),
                "AVITO": ("avito.st", "avito.ru"),
                "FARPOST": ("farpost.ru", "farpost-ads.ru"),
                "ALIEXPRESS": ("alicdn.com", "aliexpress-media.com", "aliexpress.com"),
                "ALIEXPRESS_RU": ("alicdn.com", "aliexpress-media.com", "aliexpress.com"),
            }
            suffixes = allowed.get(source, ())
            if not any(host == s or host.endswith("." + s) for s in suffixes):
                return None
            return url.split("#")[0]
        except ValueError:
            return None

    def close(self):
        """Закрывает HTTP-сессию. Вызывается runner по завершении."""
        try:
            self.session.close()
        except Exception:
            pass

    def write_observations(self, observations: list[dict], output_dir: Path):
        """Записывает наблюдения как jarvis-eye*.json в папку приёма."""
        output_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for obs in observations:
            filename = f"jarvis-eye-{self.source.lower()}-{uuid.uuid4().hex[:8]}.json"
            path = output_dir / filename
            path.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(str(path))
            log.info("%s: записан %s", self.source, filename)
        return written
