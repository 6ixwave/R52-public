"""Слежение за ценой и наличием.

Событие создаётся только для самого свежего наблюдения: старая выгрузка,
импортированная позже, не будит уведомлением. Ключ факта не даёт повторить событие.
"""

from ..domain import pricing
from ..errors import AppError
from ..money import format_minor
from ..store.products import card, get_product_row, observation_summary, watch_dict

CAVEAT = "Условия цены, наличие и доставка во Владивосток не проверены."
BASES = ("regular", "lowest_shown")


def put_watch(conn, product_id, *, target_minor, price_basis, drop_percent, notify_availability, active, now):
    get_product_row(conn, product_id)
    if target_minor is None and drop_percent is None and not notify_availability:
        raise AppError("INVALID_INPUT", detail={"field": "watch", "reason": "condition_required"})
    conn.execute(
        "INSERT INTO watch(product_id, target_minor, price_basis, drop_percent, notify_availability, active, "
        "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(product_id) DO UPDATE SET "
        "target_minor = excluded.target_minor, price_basis = excluded.price_basis, "
        "drop_percent = excluded.drop_percent, notify_availability = excluded.notify_availability, "
        "active = excluded.active, updated_at = excluded.updated_at",
        (product_id, target_minor, price_basis, drop_percent, int(notify_availability), int(active), now, now),
    )
    return watch_dict(conn.execute("SELECT * FROM watch WHERE product_id = ?", (product_id,)).fetchone())


def remove_watch(conn, product_id):
    if conn.execute("DELETE FROM watch WHERE product_id = ?", (product_id,)).rowcount == 0:
        raise AppError("WATCH_NOT_FOUND", status=404)


def list_watches(conn):
    rows = conn.execute(
        "SELECT w.id AS watch_id, w.target_minor, w.price_basis, w.drop_percent, w.notify_availability, "
        "w.active AS watch_active, w.updated_at AS watch_updated_at, p.*, w.active AS watched, "
        "f.product_id IS NOT NULL AS favorite FROM watch w JOIN product p ON p.id = w.product_id "
        "LEFT JOIN favorite f ON f.product_id = p.id ORDER BY w.updated_at DESC").fetchall()
    out = []
    for row in rows:
        product = card(row)
        current = pricing.watched_value(product, row["price_basis"])
        target = row["target_minor"]
        out.append({
            "watch": {
                "id": row["watch_id"],
                "product_id": product["id"],
                "target_minor": target,
                "price_basis": row["price_basis"],
                "drop_percent": row["drop_percent"],
                "notify_availability": bool(row["notify_availability"]),
                "active": bool(row["watch_active"]),
                "updated_at": row["watch_updated_at"],
            },
            "product": product,
            "current_minor": current,
            "target_reached": target is not None and current is not None and current <= target,
        })
    return out


def _role_note(summary, basis):
    if basis == "lowest_shown" and summary["conditional"]:
        return f" Условие цены: {pricing.ROLE_LABELS[summary['lowest_role']]}."
    return ""


def evaluate(conn, notifier, product_id, observation_id):
    """Проверяет условия слежения для нового наблюдения. Возвращает id созданных событий."""
    watch = conn.execute("SELECT * FROM watch WHERE product_id = ? AND active = 1", (product_id,)).fetchone()
    if watch is None:
        return []
    current = conn.execute(
        "SELECT id, observed_at, availability FROM observation WHERE id = ?", (observation_id,)).fetchone()
    if current is None or current["observed_at"] is None:
        return []
    newer = conn.execute(
        "SELECT 1 FROM observation WHERE product_id = ? AND id <> ? AND observed_at > ? LIMIT 1",
        (product_id, observation_id, current["observed_at"])).fetchone()
    if newer:
        return []
    previous = conn.execute(
        "SELECT id, availability FROM observation WHERE product_id = ? AND id <> ? AND observed_at IS NOT NULL "
        "AND observed_at <= ? ORDER BY observed_at DESC, id DESC LIMIT 1",
        (product_id, observation_id, current["observed_at"])).fetchone()
    basis = watch["price_basis"]
    summary = observation_summary(conn, observation_id)
    value = pricing.watched_value(summary, basis)
    before = pricing.watched_value(observation_summary(conn, previous["id"]), basis) if previous else None
    title = get_product_row(conn, product_id)["title"]
    currency = summary["currency"] or watch["currency"]
    note = _role_note(summary, basis)
    created = []
    target = watch["target_minor"]
    if target is not None and value is not None and value <= target and (before is None or before > target):
        created.append(notifier.add_event(
            conn, kind="TARGET_REACHED", fact_key=f"target:{watch['id']}:{observation_id}", product_id=product_id,
            payload={"value_minor": value, "target_minor": target, "currency": currency, "basis": basis},
            title="Цена достигла цели",
            body=f"{title}: {format_minor(value, currency)} при цели {format_minor(target, currency)}.{note} {CAVEAT}",
        ))
    drop = watch["drop_percent"]
    if drop and value is not None and before and value * 100 <= before * (100 - drop):
        percent = round((before - value) * 100 / before)
        created.append(notifier.add_event(
            conn, kind="PRICE_DROPPED", fact_key=f"drop:{watch['id']}:{observation_id}", product_id=product_id,
            payload={"before_minor": before, "value_minor": value, "percent": percent, "currency": currency},
            title=f"Цена снизилась на {percent}%",
            body=f"{title}: было {format_minor(before, currency)}, стало {format_minor(value, currency)}.{note} {CAVEAT}",
        ))
    if watch["notify_availability"] and previous:
        was, now_state = previous["availability"], current["availability"]
        if was != now_state and "UNKNOWN" not in (was, now_state):
            appeared = now_state == "IN_STOCK"
            created.append(notifier.add_event(
                conn, kind="BECAME_AVAILABLE" if appeared else "BECAME_UNAVAILABLE",
                fact_key=f"avail:{watch['id']}:{observation_id}", product_id=product_id,
                payload={"before": was, "after": now_state},
                title="Товар снова в наличии" if appeared else "Товар пропал из наличия",
                body=f"{title}. Наличие указано источником и не проверено.",
            ))
    conn.execute("UPDATE watch SET last_observation_id = ? WHERE id = ?", (observation_id, watch["id"]))
    return [event_id for event_id in created if event_id]
