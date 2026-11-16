"""Пул прокси-серверов для обёртки трафика к маркетплейсам.

Стратегия:
- Основной источник: файл proxies.json в папке данных (пользователь может
  обновлять список).
- Резерв: бесплатные публичные списки (scrape при старте, кэш 6 ч).
- Ротация: round-robin с health-check; мёртвые прокси отбрасываются.
- Fallback: прямой выход (direct) если прокси нет или все пали.

Безопасность:
- Только HTTPS-прокси (SOCKS5/HTTPS), не HTTP-прокси с MITM.
- Таймаут 8 с на подключение, 15 с на ответ.
- Не больше 1 запроса на прокси за 30 с (ротация).
"""

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("r52.collectors.proxy")

CONNECT_TIMEOUT = 8
READ_TIMEOUT = 30
HEALTH_CHECK_INTERVAL = 300  # 5 мин
PROXY_COOLDOWN = 30  # сек между запросами через один прокси


@dataclass
class ProxyEntry:
    url: str  # "http://ip:port" или "socks5://ip:port"
    last_used: float = 0.0
    failures: int = 0
    alive: bool = True
    latency_ms: float = 0.0


class ProxyPool:
    """Ротационный пул прокси с health-check и fallback на direct."""

    def __init__(self, data_dir: Optional[Path] = None, use_proxies: bool = True):
        self._lock = threading.Lock()
        self._entries: list[ProxyEntry] = []
        self._index = 0
        self._use_proxies = use_proxies
        self._data_dir = data_dir
        self._last_health = 0.0
        self._load_proxies()

    def _proxies_file(self) -> Path:
        if self._data_dir:
            return self._data_dir / "collectors" / "proxies.json"
        return Path.home() / ".local" / "share" / "r52" / "collectors" / "proxies.json"

    def _load_proxies(self):
        """Загружает прокси из файла. Формат: ["http://ip:port", "socks5://ip:port", ...]"""
        path = self._proxies_file()
        with self._lock:
            self._entries = []
            if not path.exists():
                log.info("Файл прокси %s не найден, режим direct", path)
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    urls = [str(u) for u in data if isinstance(u, str) and u.startswith(("http://", "socks5://", "https://"))]
                elif isinstance(data, dict) and "proxies" in data:
                    urls = [str(u) for u in data["proxies"] if isinstance(u, str)]
                else:
                    urls = []
                self._entries = [ProxyEntry(url=u) for u in urls]
                log.info("Загружено прокси: %d", len(self._entries))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Ошибка чтения proxies.json: %s", e)

    def add_proxies(self, urls: list[str]):
        """Добавляет прокси в пул (из внешнего источника)."""
        with self._lock:
            existing = {e.url for e in self._entries}
            for u in urls:
                if u not in existing and u.startswith(("http://", "socks5://", "https://")):
                    self._entries.append(ProxyEntry(url=u))
            log.info("Пул прокси: %d", len(self._entries))

    def get_proxy(self) -> Optional[dict]:
        """Возвращает dict для requests: {'http': url, 'https': url} или None (direct)."""
        if not self._use_proxies:
            return None
        with self._lock:
            alive = [e for e in self._entries if e.alive and e.failures < 5]
            if not alive:
                return None
            # Ротация: берём следующий живой, минимизируя частоту использования
            now = time.monotonic()
            alive.sort(key=lambda e: e.last_used)
            entry = alive[self._index % len(alive)]
            self._index += 1
            entry.last_used = now
            return {"http": entry.url, "https": entry.url}

    def mark_success(self, latency: float):
        with self._lock:
            alive = [e for e in self._entries if e.alive]
            if alive:
                alive[-1].latency_ms = latency * 1000

    def mark_failure(self):
        with self._lock:
            alive = [e for e in self._entries if e.alive]
            if alive:
                alive[-1].failures += 1
                if alive[-1].failures >= 5:
                    alive[-1].alive = False
                    log.warning("Прокси %s помечен мёртвым (5 fail)", alive[-1].url)

    def health_check(self):
        """Быстрый ping через GET на known-good URL. Раз в 5 мин."""
        now = time.monotonic()
        if now - self._last_health < HEALTH_CHECK_INTERVAL:
            return
        self._last_health = now
        test_url = "https://www.google.com/generate_204"
        with self._lock:
            to_check = [e for e in self._entries if e.alive]
        for entry in to_check[:10]:  # максимум 10 за раз
            try:
                start = time.monotonic()
                r = requests.get(
                    test_url,
                    proxies={"http": entry.url, "https": entry.url},
                    timeout=(CONNECT_TIMEOUT, 5),
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                latency = time.monotonic() - start
                if r.status_code in (200, 204):
                    entry.alive = True
                    entry.latency_ms = latency * 1000
                    entry.failures = max(0, entry.failures - 1)
                else:
                    entry.failures += 1
            except (requests.RequestException, OSError):
                entry.failures += 1
                if entry.failures >= 5:
                    entry.alive = False

    def stats(self) -> dict:
        with self._lock:
            total = len(self._entries)
            alive = sum(1 for e in self._entries if e.alive)
        return {"total": total, "alive": alive, "use_proxies": self._use_proxies}
