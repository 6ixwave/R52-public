"""Время: в базе только UTC, человеку показывается местное время."""

import time
from datetime import datetime, timedelta, timezone

from .errors import AppError

# В Windows нет системной базы часовых поясов, а пакет tzdata может
# отсутствовать. Для зон без перехода на летнее время есть точная замена.
_FIXED_ZONES = {
    "UTC": 0,
    "Europe/Kaliningrad": 2,
    "Europe/Moscow": 3,
    "Europe/Samara": 4,
    "Asia/Yekaterinburg": 5,
    "Asia/Omsk": 6,
    "Asia/Novosibirsk": 7,
    "Asia/Krasnoyarsk": 7,
    "Asia/Irkutsk": 8,
    "Asia/Yakutsk": 9,
    "Asia/Vladivostok": 10,
    "Asia/Magadan": 11,
    "Asia/Kamchatka": 12,
}


def zone(name):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except (ImportError, KeyError, ValueError, OSError):
        if name in _FIXED_ZONES:
            return timezone(timedelta(hours=_FIXED_ZONES[name]), name)
        raise AppError("CONFIG_INVALID", f"Неизвестный часовой пояс: {name}") from None


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise AppError("AWARE_TIME_REQUIRED")
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse_aware(value, code="AWARE_TIME_REQUIRED"):
    """Разбирает ISO время с часовым поясом. Время без пояса не принимается."""
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        raise AppError(code)
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise AppError(code) from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise AppError(code)
    return dt.astimezone(timezone.utc)


def parse_optional(value):
    """Как parse_aware, но неизвестное или битое время становится None, а не выдуманным значением."""
    try:
        return parse_aware(value)
    except AppError:
        return None


def parse_hhmm(text):
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        raise ValueError("HH:MM")
    hour, minute = int(text[:2]), int(text[3:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("HH:MM")
    return hour, minute


def local_text(value, tz):
    dt = parse_aware(value) if isinstance(value, str) else value
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")


class Clock:
    """Источник времени. В тестах заменяется на управляемые часы."""

    def now(self):
        return utc_now()

    def monotonic(self):
        return time.monotonic()


class FixedClock(Clock):
    def __init__(self, start):
        self.current = start
        self._mono = 1000.0

    def now(self):
        return self.current

    def monotonic(self):
        return self._mono

    def advance(self, seconds):
        self.current = self.current + timedelta(seconds=seconds)
        self._mono += seconds
