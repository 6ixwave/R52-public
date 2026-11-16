"""Пользовательские настройки в базе. Значение проверяется до записи."""

import json
import logging
import re
import threading

from ..errors import AppError
from ..timeutil import iso, parse_hhmm

log = logging.getLogger("r52.settings")


def _bool(value):
    if not isinstance(value, bool):
        raise ValueError("bool")
    return value


def _hhmm(value):
    if not isinstance(value, str):
        raise ValueError("time")
    parse_hhmm(value)
    return value


def _quiet(value):
    if value == "":
        return value
    if not isinstance(value, str) or len(value) != 11 or value[5] != "-":
        raise ValueError("range")
    parse_hhmm(value[:5])
    parse_hhmm(value[6:])
    if value[:5] == value[6:]:
        raise ValueError("empty range")
    return value


def _int_range(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError("int")
        return value
    return check


def _choice(*options):
    def check(value):
        if value not in options:
            raise ValueError("choice")
        return value
    return check


def _model(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,80}", value):
        raise ValueError("model")
    return value


SPEC = {
    "autocollect.enabled": (True, _bool),
    "autocollect.refresh_seconds": (21600, _int_range(60, 2592000)),
    "autocollect.discovery_seconds": (3600, _int_range(60, 2592000)),
    "autocollect.new_limit": (20, _int_range(1, 200)),
    "notifications.enabled": (True, _bool),
    "notifications.quiet_hours": ("23:00-08:00", _quiet),
    "digest.enabled": (True, _bool),
    "digest.time": ("09:00", _hhmm),
    "intake.enabled": (True, _bool),
    "intake.auto_accept": (False, _bool),
    "assistant.llm": ("off", _choice("off", "ollama", "openrouter")),
    "assistant.ollama_model": ("qwen2.5:7b-instruct", _model),
    "assistant.ollama_port": (11434, _int_range(1024, 65535)),
    "assistant.openrouter_budget_cents": (100, _int_range(1, 1000000)),
    "assistant.openrouter_max_tokens": (1400, _int_range(100, 16000)),
    "backup.time": ("04:00", _hhmm),
    "backup.keep_daily": (7, _int_range(1, 60)),
    "backup.keep_weekly": (4, _int_range(0, 52)),
    "retention.days": (180, _int_range(7, 3650)),
    "ui.motion": (True, _bool),
    "ui.sound": (False, _bool),
}


class Settings:
    def __init__(self, db, clock):
        self.db = db
        self.clock = clock
        self._lock = threading.Lock()
        self._cache = None
        self._listeners = []

    def on_change(self, callback):
        self._listeners.append(callback)

    def all(self):
        with self._lock:
            if self._cache is None:
                values = {key: default for key, (default, _) in SPEC.items()}
                with self.db.read() as conn:
                    rows = conn.execute("SELECT key, value_json FROM setting").fetchall()
                for row in rows:
                    if row["key"] not in SPEC:
                        continue
                    try:
                        values[row["key"]] = SPEC[row["key"]][1](json.loads(row["value_json"]))
                    except (ValueError, TypeError):
                        log.warning("Настройка %s повреждена, используется значение по умолчанию", row["key"])
                self._cache = values
            return dict(self._cache)

    def get(self, key):
        return self.all()[key]

    def update(self, changes):
        if not isinstance(changes, dict) or not changes:
            raise AppError("INVALID_INPUT", detail={"field": "settings", "reason": "object_required"})
        checked = {}
        for key, value in changes.items():
            if key not in SPEC:
                raise AppError("SETTING_UNKNOWN", detail=key)
            try:
                checked[key] = SPEC[key][1](value)
            except (ValueError, TypeError):
                raise AppError("SETTING_INVALID", detail=key) from None
        now = iso(self.clock.now())
        with self.db.write() as conn:
            for key, value in checked.items():
                conn.execute(
                    "INSERT INTO setting(key, value_json, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                    (key, json.dumps(value), now),
                )
        with self._lock:
            self._cache = None
        values = self.all()
        for callback in self._listeners:
            callback(set(checked), values)
        return values
