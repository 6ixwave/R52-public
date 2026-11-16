"""Сравнение: изменения одной карточки во времени и таблица нескольких товаров."""

import json

from ..money import format_minor
from .normalize import FIELDS
from .pricing import ROLE_LABELS

COMPARED = (
    "availability", "displayed_price", "price_conditions", "condition", "delivery",
    "seller_location", "variant", "bundle", "warranty", "returns",
)
INTERPRETATION = (
    "Сравнение отображённых сведений и заявлений продавца, "
    "не доказательство физического изменения товара."
)
AVAILABILITY_TEXT = {
    "IN_STOCK": "в наличии по данным источника",
    "OUT_OF_STOCK": "нет в наличии по данным источника",
    "UNKNOWN": "неизвестно",
}
CONDITION_TEXT = {"NEW": "новое, со слов продавца", "USED": "б/у, со слов продавца", "UNKNOWN": "неизвестно"}
ROWS = (
    ("price", "Минимальная показанная цена"),
    ("regular", "Обычная цена"),
    ("availability", "Наличие"),
    ("condition", "Состояние"),
    ("variant", "Модификация"),
    ("delivery", "Доставка"),
    ("warranty", "Гарантия"),
    ("seller_location", "Где продавец"),
    ("observed", "Последнее наблюдение"),
)


def short(value, limit=160):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def diff(previous, current):
    changes = []
    before_fields = previous.get("fields") or {}
    after_fields = current.get("fields") or {}
    for key in COMPARED:
        before = before_fields.get(key) or {}
        after = after_fields.get(key) or {}
        state_before = before.get("state", "UNKNOWN")
        state_after = after.get("state", "UNKNOWN")
        if state_before == "UNKNOWN" and state_after == "UNKNOWN":
            continue
        if state_before == "UNKNOWN":
            kind = "DATA_APPEARED"
        elif state_after == "UNKNOWN":
            kind = "DATA_DISAPPEARED"
        elif before.get("value") != after.get("value"):
            kind = "VALUE_CHANGED"
        else:
            continue
        changes.append({
            "field": key,
            "label": FIELDS.get(key, key),
            "kind": kind,
            "before": short(before.get("value")),
            "after": short(after.get("value")),
        })
    return {"changes": changes, "interpretation": INTERPRETATION}


def _cell(key, product, best):
    summary = product["summary"]
    latest = product.get("latest") or {}
    if key == "price":
        value = summary.get("lowest_minor")
        if value is None:
            return {"text": "неизвестно", "state": "UNKNOWN"}
        text = format_minor(value, summary.get("currency"))
        role = summary.get("lowest_role")
        if role and role != "regular":
            text += f" ({ROLE_LABELS[role]})"
        return {"text": text, "state": "OBSERVED", "best": value == best}
    if key == "regular":
        value = summary.get("regular_minor")
        if value is None:
            return {"text": "неизвестно", "state": "UNKNOWN"}
        return {"text": format_minor(value, summary.get("currency")), "state": "OBSERVED"}
    if key == "availability":
        code = product.get("availability") or "UNKNOWN"
        return {"text": AVAILABILITY_TEXT.get(code, "неизвестно"), "state": "UNKNOWN" if code == "UNKNOWN" else "SELLER_CLAIM"}
    if key == "condition":
        code = (latest.get("condition") or {}).get("code", "UNKNOWN")
        return {"text": CONDITION_TEXT.get(code, "неизвестно"), "state": "UNKNOWN" if code == "UNKNOWN" else "SELLER_CLAIM"}
    if key == "observed":
        seen = product.get("last_seen_at")
        return {"text": seen or "время неизвестно", "state": "OBSERVED" if seen else "UNKNOWN", "time": seen}
    field = (latest.get("fields") or {}).get(key) or {}
    state = field.get("state", "UNKNOWN")
    return {"text": short(field.get("value")) if state != "UNKNOWN" else "неизвестно", "state": state}


def table(products):
    """products: [{id, title, source, url, summary, latest, availability, last_seen_at}]."""
    known = [item["summary"]["lowest_minor"] for item in products if item["summary"].get("lowest_minor") is not None]
    best = min(known) if known else None
    roles = {item["summary"].get("lowest_role") for item in products if item["summary"].get("lowest_minor") is not None}
    rows = [{"key": key, "label": label, "cells": [_cell(key, item, best) for item in products]} for key, label in ROWS]
    notes = []
    if len(roles) > 1:
        notes.append("Минимальные цены показаны с разными условиями, напрямую они не равноценны.")
    notes.append("Наличие, доставка во Владивосток и итоговая стоимость не проверены.")
    columns = [{"product_id": item["id"], "title": item["title"], "source": item["source"], "url": item.get("url"), "image_url": item.get("image_url"), "media_url":item.get("media_url")} for item in products]
    return {"columns": columns, "rows": rows, "notes": notes}
