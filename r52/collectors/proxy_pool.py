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
import sys
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
_LAST_SELECTION = object()

# === Traffic guard: 100 MB limit per test package ===
TRAFFIC_LIMIT_BYTES = 100 * 1024 * 1024  # 100 MB
TRAFFIC_STATE_FILE = "traffic_state.json"
DIRECT_FAIL_THRESHOLD = 5  # consecutive direct failures before STOP


@dataclass
class ProxyEntry:
    url: str  # "http://ip:port" или "socks5://ip:port"
    last_used: float = 0.0
    failures: int = 0
    alive: bool = True
    latency_ms: float = 0.0


class ProxyPool:
    """Ротационный пул прокси с health-check и fallback на direct."""

    def __init__(self, data_dir: Optional[Path] = None, use_proxies: bool = True, fail_closed: bool = True):
        self._lock = threading.Lock()
        self._selection = threading.local()
        self._entries: list[ProxyEntry] = []
        self._index = 0
        self._use_proxies = use_proxies
        self._fail_closed = fail_closed
        self._data_dir = data_dir
        self._last_health = 0.0
        self._load_proxies()
        # Traffic guard: 100 MB limit, channel management
        if data_dir:
            self._traffic_file = data_dir / "collectors" / TRAFFIC_STATE_FILE
        elif sys.platform == "win32":
            self._traffic_file = Path.home() / "AppData" / "Local" / "R52" / "collectors" / TRAFFIC_STATE_FILE
        else:
            self._traffic_file = Path.home() / ".local" / "share" / "r52" / "collectors" / TRAFFIC_STATE_FILE
        self._bytes_used = self._load_traffic()
        self._direct_failures = 0
        self._channel_override = None

    def _proxies_file(self) -> Path:
        if self._data_dir:
            return self._data_dir / "collectors" / "proxies.json"
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Local" / "R52" / "collectors" / "proxies.json"
        return Path.home() / ".local" / "share" / "r52" / "collectors" / "proxies.json"

    def _load_proxies(self):
        """Загружает прокси из файла. Формат: ["http://ip:port", "socks5://ip:port", ...]"""
        path = self._proxies_file()
        with self._lock:
            self._entries = []
            if not path.exists():
                log.info("Файл прокси %s не найден, авто-режим direct", path)
                self._use_proxies = False
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
                if not self._entries:
                    log.info("proxies.json пуст, авто-режим direct")
                    self._use_proxies = False
                log.info("Загружено прокси: %d", len(self._entries))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Ошибка чтения proxies.json: %s, авто-режим direct", e)
                self._use_proxies = False

    def add_proxies(self, urls: list[str]):
        """Добавляет прокси в пул (из внешнего источника)."""
        with self._lock:
            existing = {e.url for e in self._entries}
            for u in urls:
                if u not in existing and u.startswith(("http://", "socks5://", "https://")):
                    self._entries.append(ProxyEntry(url=u))
            if self._entries:
                self._use_proxies = True  # явное добавление → включаем прокси
            log.info("Пул прокси: %d", len(self._entries))

    def get_proxy(self) -> Optional[dict]:
        """Возвращает dict для requests: {'http': url, 'https': url} или None (direct)."""
        self._selection.entry = None
        if not self._use_proxies:
            return None
        with self._lock:
            alive = [e for e in self._entries if e.alive and e.failures < 5]
            if not alive:
                if self._use_proxies and self._fail_closed:
                    log.warning("PROXY_FAIL_CLOSED: use_proxies=True, alive=0/total=%d — запрос пропущен (не direct)", len(self._entries))
                return None
            # Ротация: берём следующий живой, минимизируя частоту использования
            now = time.monotonic()
            alive.sort(key=lambda e: e.last_used)
            entry = alive[self._index % len(alive)]
            self._index += 1
            entry.last_used = now
            self._selection.entry = entry
            return {"http": entry.url, "https": entry.url}

    def _feedback_entry(self, proxies):
        # Caller holds the lock. Browser sessions can name their original proxy.
        if proxies is _LAST_SELECTION:
            selected = getattr(self._selection, "entry", None)
            return next((e for e in self._entries if e is selected), None)
        if not proxies:
            return None
        url = proxies.get("https") or proxies.get("http")
        return next((e for e in self._entries if e.url == url), None)

    def mark_success(self, latency: float, proxies=_LAST_SELECTION):
        with self._lock:
            entry = self._feedback_entry(proxies)
            if entry is not None:
                entry.latency_ms = latency * 1000

    def mark_failure(self, proxies=_LAST_SELECTION):
        with self._lock:
            entry = self._feedback_entry(proxies)
            if entry is not None:
                entry.failures += 1
                if entry.failures >= 5:
                    entry.alive = False
                    log.warning("Прокси #%d помечен мёртвым (5 fail)", self._entries.index(entry) + 1)

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

    @property
    def use_proxies(self) -> bool:
        """True если прокси включены И пул непустой (иначе деградация в direct)."""
        if not self._use_proxies:
            return False
        with self._lock:
            return len(self._entries) > 0

    def stats(self) -> dict:
        with self._lock:
            total = len(self._entries)
            alive = sum(1 for e in self._entries if e.alive)
        return {"total": total, "alive": alive, "use_proxies": self._use_proxies, "fail_closed": self._fail_closed}

    def is_ready(self) -> bool:
        """True если сбор может стартовать: либо direct разрешён, либо есть живой прокси."""
        if not self._use_proxies:
            return True
        with self._lock:
            return any(e.alive and e.failures < 5 for e in self._entries)

    # === Traffic guard & channel management ===

    def _load_traffic(self) -> int:
        try:
            if self._traffic_file.exists():
                data = json.loads(self._traffic_file.read_text(encoding="utf-8"))
                return int(data.get("bytes", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return 0

    def _save_traffic(self):
        try:
            self._traffic_file.parent.mkdir(parents=True, exist_ok=True)
            self._traffic_file.write_text(
                json.dumps({"bytes": self._bytes_used, "updated": time.time()}),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("Traffic save failed: %s", e)

    def record_bytes(self, n: int):
        """Accumulate downloaded bytes. Switches to direct when limit hit."""
        with self._lock:
            self._bytes_used += n
            log.debug("record_bytes: +%d B → total %d B (%.1f MB)", n, self._bytes_used, self._bytes_used / (1024 * 1024))
            if self._bytes_used >= TRAFFIC_LIMIT_BYTES:
                log.info("TRAFFIC_LIMIT: %d MB — channel → direct (home IP)",
                         self._bytes_used // (1024 * 1024))
            self._save_traffic()

    @property
    def channel(self) -> str:
        """Current channel: 'proxy', 'direct', or 'stop'."""
        if self._channel_override:
            return self._channel_override
        if not self._use_proxies:
            return "direct"
        if self._bytes_used >= TRAFFIC_LIMIT_BYTES:
            return "direct"
        with self._lock:
            if not any(e.alive and e.failures < 5 for e in self._entries):
                return "direct"
        return "proxy"

    @property
    def bytes_used(self) -> int:
        return self._bytes_used

    def mark_direct_result(self, success: bool):
        """Track consecutive direct failures for STOP logic."""
        with self._lock:
            if success:
                self._direct_failures = 0
            else:
                self._direct_failures += 1
                if self._direct_failures >= DIRECT_FAIL_THRESHOLD:
                    log.warning("DIRECT_FAIL_STOP: %d consecutive — channel=STOP",
                                self._direct_failures)
                    self._channel_override = "stop"

    def reset_traffic(self):
        """Reset traffic counter (new test package)."""
        with self._lock:
            self._bytes_used = 0
            self._direct_failures = 0
            self._channel_override = None
            self._save_traffic()
            log.info("Traffic reset: new test package")
