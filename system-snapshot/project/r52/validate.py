"""Проверка входных данных: тип, диапазон, набор полей. Лишнее поле тоже ошибка."""

from .errors import AppError
from .money import parse_minor


def fail(field, reason="invalid"):
    return AppError("INVALID_INPUT", detail={"field": field, "reason": reason})


def only_keys(data, allowed, name="body"):
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise fail(name, "object_required")
    extra = sorted(set(data) - set(allowed))
    if extra:
        raise fail(name, "unknown_keys:" + ",".join(extra[:5]))
    return data


def text(data, key, max_len, required=False, min_len=1):
    value = data.get(key)
    if value is None:
        if required:
            raise fail(key, "required")
        return None
    if not isinstance(value, str):
        raise fail(key, "string_required")
    value = value.strip()
    if not min_len <= len(value) <= max_len:
        raise fail(key, f"length_{min_len}_{max_len}")
    return value


def integer(data, key, lo, hi, required=False, default=None):
    value = data.get(key, default)
    if value is None:
        if required:
            raise fail(key, "required")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(key, "integer_required")
    if not lo <= value <= hi:
        raise fail(key, f"range_{lo}_{hi}")
    return value


def boolean(data, key, default=None):
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise fail(key, "boolean_required")
    return value


def choice(data, key, options, default=None):
    value = data.get(key, default)
    if value is None:
        return default
    if value not in options:
        raise fail(key, "choice:" + ",".join(sorted(options)))
    return value


def amount_minor(data, key, required=False):
    """Сумма строкой ("49 990", "49990,50") или числом рублей. Возвращает копейки."""
    value = data.get(key)
    if value is None:
        if required:
            raise fail(key, "required")
        return None
    minor = parse_minor(value)
    if minor is None:
        raise fail(key, "amount_required")
    return minor


def id_list(data, key, max_items, required=False):
    value = data.get(key)
    if value is None:
        if required:
            raise fail(key, "required")
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise fail(key, f"list_up_to_{max_items}")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise fail(key, "positive_integers_required")
        if item not in out:
            out.append(item)
    return out


def query_int(query, key, default, lo, hi):
    value = query.get(key)
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except ValueError:
        raise fail(key, "integer_required") from None
    if not lo <= number <= hi:
        raise fail(key, f"range_{lo}_{hi}")
    return number


def path_int(value, name="id"):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise fail(name, "integer_required") from None
    if number < 1 or number > 2**62:
        raise fail(name, "range")
    return number
