"""Деньги: Decimal и целые копейки, валюта всегда явная."""

import math
import re
from decimal import ROUND_HALF_UP, Decimal

_SPACES = re.compile(r"[\s\u00a0\u202f\u2009]")
_CURRENCY_SUFFIX = re.compile(r"(?:₽|руб\.?|р\.|rub)$", re.IGNORECASE)
_AMOUNT = re.compile(r"^(\d{1,12})(?:[.,](\d{1,2}))?$")
MAX_MINOR = 10**14
CURRENCIES = frozenset({"RUB", "USD", "EUR", "CNY"})


def parse_amount(value):
    """Возвращает Decimal или None. Двусмысленные записи не угадываются."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() and value >= 0 else None
    if isinstance(value, int):
        return Decimal(value) if 0 <= value < 10**12 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or value >= 10**12:
            return None
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not isinstance(value, str) or len(value) > 40:
        return None
    text = _SPACES.sub("", value.strip())
    text = _CURRENCY_SUFFIX.sub("", text)
    match = _AMOUNT.match(text)
    if not match:
        return None
    whole, fraction = match.groups()
    return Decimal(whole + ("." + fraction if fraction else ""))


def to_minor(amount):
    minor = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if minor < 0 or minor >= MAX_MINOR:
        raise ValueError("amount out of range")
    return minor


def parse_minor(value):
    amount = parse_amount(value)
    return None if amount is None else to_minor(amount)


def format_minor(minor, currency="RUB"):
    if minor is None:
        return "цена неизвестна"
    whole, kop = divmod(int(minor), 100)
    text = f"{whole:,}".replace(",", "\u00a0")
    if kop:
        text = f"{text},{kop:02d}"
    sign = "₽" if currency == "RUB" else (currency or "")
    return f"{text}\u00a0{sign}".strip()
