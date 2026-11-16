"""Цены: роли, сводка по наблюдению, история и прозрачная подсказка о выгоде.

Цена с картой, кошельком или купоном не выдаётся за обычную. Старая (зачёркнутая)
цена никогда не считается ценой покупки.
"""

from ..money import format_minor

ROLE_LABELS = {
    "regular": "обычная",
    "card": "с картой банка",
    "wallet": "с кошельком",
    "coupon": "по купону",
    "old": "старая, зачёркнутая",
    "unknown": "условия не распознаны",
}
ROLES = tuple(ROLE_LABELS)
CONDITIONAL = frozenset({"card", "wallet", "coupon", "unknown"})


def summarize(points):
    """Сводка одного наблюдения. Валюты не смешиваются: берётся самая частая."""
    points = [point for point in points if point.get("amount_minor") is not None and point.get("currency")]
    empty = {"currency": None, "regular_minor": None, "lowest_minor": None, "lowest_role": None,
             "old_minor": None, "conditional": False}
    if not points:
        return empty
    counts = {}
    for point in points:
        counts[point["currency"]] = counts.get(point["currency"], 0) + 1
    currency = max(sorted(counts), key=lambda code: counts[code])
    same = [point for point in points if point["currency"] == currency]
    regular = min((point["amount_minor"] for point in same if point["role"] == "regular"), default=None)
    shown = [point for point in same if point["role"] != "old"]
    lowest = min(shown, key=lambda point: (point["amount_minor"], point["role"] != "regular")) if shown else None
    old = max((point["amount_minor"] for point in same if point["role"] == "old"), default=None)
    return {
        "currency": currency,
        "regular_minor": regular,
        "lowest_minor": lowest["amount_minor"] if lowest else None,
        "lowest_role": lowest["role"] if lowest else None,
        "old_minor": old,
        "conditional": bool(lowest) and lowest["role"] in CONDITIONAL,
    }


def watched_value(summary, basis):
    if basis == "regular":
        return summary.get("regular_minor")
    return summary.get("lowest_minor")


def price_line(summary):
    if summary.get("lowest_minor") is None:
        return "Цена неизвестна"
    text = format_minor(summary["lowest_minor"], summary.get("currency"))
    role = summary.get("lowest_role")
    if role and role != "regular":
        text += f" ({ROLE_LABELS[role]})"
    return text


def history(rows):
    """rows: точки цен с observation_id и observed_at по времени.

    Наблюдения без времени не попадают в график: их место на оси неизвестно.
    """
    grouped, order = {}, []
    for row in rows:
        key = row["observation_id"]
        if key not in grouped:
            grouped[key] = {"observed_at": row["observed_at"], "points": []}
            order.append(key)
        grouped[key]["points"].append(row)
    series, undated = [], 0
    for key in order:
        item = grouped[key]
        if item["observed_at"] is None:
            undated += 1
            continue
        series.append({"observation_id": key, "observed_at": item["observed_at"], **summarize(item["points"])})
    currencies = [item["currency"] for item in series if item["currency"]]
    if currencies:
        main = max(sorted(set(currencies)), key=currencies.count)
        series = [item for item in series if item["currency"] == main]
    return series, undated


def stats(series, field="lowest_minor"):
    values = [item[field] for item in series if item.get(field) is not None]
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    middle = ordered[count // 2] if count % 2 else (ordered[count // 2 - 1] + ordered[count // 2]) // 2
    result = {"count": count, "min": ordered[0], "max": ordered[-1], "median": middle,
              "last": values[-1], "previous": values[-2] if count > 1 else None}
    if result["previous"]:
        result["change_minor"] = result["last"] - result["previous"]
        result["change_percent"] = round(result["change_minor"] * 100 / result["previous"], 1)
    return result


def deal_hint(series):
    """Сравнение последней цены с медианой собственных наблюдений. Все числа видны."""
    summary = stats(series)
    if not summary or summary["count"] < 3 or not summary["median"]:
        return {"code": "NOT_ENOUGH_HISTORY", "text": "Мало наблюдений, чтобы сравнить с обычной ценой."}
    last, middle = summary["last"], summary["median"]
    delta = round((middle - last) * 100 / middle)
    if delta >= 5:
        code, text = "BELOW_USUAL", f"Последняя цена ниже медианы наблюдений на {delta}%."
    elif delta <= -5:
        code, text = "ABOVE_USUAL", f"Последняя цена выше медианы наблюдений на {-delta}%."
    else:
        code, text = "USUAL", "Последняя цена близка к медиане наблюдений."
    return {
        "code": code,
        "text": text + " Условия цены и доставка не проверены.",
        "median_minor": middle,
        "last_minor": last,
        "observations": summary["count"],
    }
