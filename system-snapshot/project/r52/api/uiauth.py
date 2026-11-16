"""Вход в окно интерфейса и учёт открытых окон.

Лаунчер получает одноразовый код по токену ядра и открывает окно по ссылке с этим кодом.
Ядро меняет код на cookie сессии (HttpOnly, SameSite=Strict). В файле сессий лежат только
SHA-256 от идентификаторов, поэтому копия файла не даёт входа. Код живёт 60 секунд и
срабатывает один раз.

Открытая страница раз в несколько секунд сообщает о себе. По этим отметкам лаунчер понимает,
что окно закрыто, и останавливает ядро, которое запускал сам.
"""

import hashlib
import json
import logging
import secrets
import threading
import time

from ..fsutil import atomic_write_json

log = logging.getLogger("r52.api")

COOKIE = "r52_session"
CODE_TTL = 60
SESSION_TTL = 30 * 24 * 3600
TOUCH_EVERY = 3600
PRESENCE_TTL = 150
MAX_CODES = 20
MAX_SESSIONS = 50
MAX_PAGES = 50


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plausible(value):
    return isinstance(value, str) and 16 <= len(value) <= 128


class UiAuth:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()
        self._codes = {}
        self._pages = {}
        self._seen = False
        self._sessions = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            log.warning("Файл сессий окна не прочитан, вход понадобится заново: %s", error)
            return {}
        sessions = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(sessions, dict):
            return {}
        now = self.clock()
        return {key: float(value) for key, value in sessions.items()
                if isinstance(key, str) and len(key) == 64 and isinstance(value, (int, float))
                and not isinstance(value, bool) and 0 <= now - value < SESSION_TTL}

    def _save(self):
        try:
            atomic_write_json(self.path, {"sessions": self._sessions})
        except OSError as error:
            log.warning("Файл сессий окна не записан: %s", error)

    def issue_code(self):
        """Одноразовый код для ссылки входа. Выдаётся только по токену ядра."""
        code = secrets.token_urlsafe(24)
        with self._lock:
            now = self.clock()
            self._codes = {key: expiry for key, expiry in self._codes.items() if expiry > now}
            while len(self._codes) >= MAX_CODES:
                self._codes.pop(next(iter(self._codes)))
            self._codes[_digest(code)] = now + CODE_TTL
        return {"code": code, "expires_in": CODE_TTL}

    def redeem(self, code):
        """Меняет код на новую сессию. Повторный или просроченный код даёт None."""
        if not _plausible(code):
            return None
        with self._lock:
            expiry = self._codes.pop(_digest(code), None)
            now = self.clock()
            if expiry is None or expiry <= now:
                return None
            session = secrets.token_urlsafe(32)
            self._sessions[_digest(session)] = now
            if len(self._sessions) > MAX_SESSIONS:
                extra = len(self._sessions) - MAX_SESSIONS
                for key in sorted(self._sessions, key=self._sessions.get)[:extra]:
                    del self._sessions[key]
            self._save()
        return session

    def check(self, session):
        if not _plausible(session):
            return False
        key = _digest(session)
        with self._lock:
            last = self._sessions.get(key)
            if last is None:
                return False
            now = self.clock()
            if now - last >= SESSION_TTL:
                del self._sessions[key]
                self._save()
                return False
            if now - last >= TOUCH_EVERY:
                self._sessions[key] = now
                self._save()
        return True

    def heartbeat(self, page):
        with self._lock:
            now = self.clock()
            self._prune(now)
            if page not in self._pages and len(self._pages) >= MAX_PAGES:
                return False
            self._pages[page] = now
            self._seen = True
        return True

    def bye(self, page):
        with self._lock:
            self._pages.pop(page, None)

    def presence(self):
        with self._lock:
            self._prune(self.clock())
            return {"pages": len(self._pages), "seen": self._seen}

    def _prune(self, now):
        self._pages = {key: moment for key, moment in self._pages.items() if now - moment < PRESENCE_TTL}
