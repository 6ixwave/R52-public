"""События и уведомления.

Событие идентифицируется ключом факта (fact_key), поэтому повторная обработка не
создаёт второе уведомление. Тихие часы откладывают показ, но не теряют его.
"""

import json

from ..errors import AppError
from ..timeutil import iso, parse_hhmm

TRANSITIONS = {
    "PENDING": {"SHOWN", "READ", "DISMISSED"},
    "DEFERRED_QUIET": {"PENDING", "READ", "DISMISSED"},
    "SHOWN": {"READ", "DISMISSED"},
    "READ": {"DISMISSED"},
    "DISMISSED": set(),
}
UNREAD = ("PENDING", "DEFERRED_QUIET", "SHOWN")


def in_quiet_hours(spec, local_dt):
    if not spec:
        return False
    start_h, start_m = parse_hhmm(spec[:5])
    end_h, end_m = parse_hhmm(spec[6:])
    start, end = start_h * 60 + start_m, end_h * 60 + end_m
    minute = local_dt.hour * 60 + local_dt.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


class Notifier:
    def __init__(self, settings, clock, tz):
        self.settings = settings
        self.clock = clock
        self.tz = tz

    def quiet_now(self):
        return in_quiet_hours(self.settings.get("notifications.quiet_hours"), self.clock.now().astimezone(self.tz))

    def add_event(self, conn, *, kind, fact_key, product_id=None, payload=None, title=None, body=None):
        """Создаёт событие один раз. Уведомление добавляется, если есть заголовок."""
        now = iso(self.clock.now())
        cursor = conn.execute(
            "INSERT INTO event(fact_key, kind, product_id, payload_json, created_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(fact_key) DO NOTHING",
            (fact_key, kind, product_id, json.dumps(payload or {}, ensure_ascii=False), now),
        )
        if cursor.rowcount == 0:
            return None
        event_id = cursor.lastrowid
        if title and self.settings.get("notifications.enabled"):
            status = "DEFERRED_QUIET" if self.quiet_now() else "PENDING"
            conn.execute(
                "INSERT INTO notification(event_id, product_id, title, body, status, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (event_id, product_id, title[:200], (body or "")[:1000], status, now),
            )
        return event_id

    def release_deferred(self, conn):
        if self.quiet_now():
            return 0
        return conn.execute("UPDATE notification SET status = 'PENDING' WHERE status = 'DEFERRED_QUIET'").rowcount

    def mark(self, conn, notification_id, status):
        row = conn.execute("SELECT status FROM notification WHERE id = ?", (notification_id,)).fetchone()
        if row is None:
            raise AppError("NOTIFICATION_NOT_FOUND", status=404)
        if status not in TRANSITIONS[row["status"]]:
            # Повторная или запоздавшая отметка не ошибка: состояние не меняется назад.
            return False
        now = iso(self.clock.now())
        if status == "SHOWN":
            conn.execute("UPDATE notification SET status = ?, shown_at = ? WHERE id = ?", (status, now, notification_id))
        else:
            conn.execute(
                "UPDATE notification SET status = ?, read_at = COALESCE(read_at, ?) WHERE id = ?",
                (status, now, notification_id),
            )
        return True

    def read_all(self, conn):
        return conn.execute(
            "UPDATE notification SET status = 'READ', read_at = COALESCE(read_at, ?) "
            "WHERE status IN ('PENDING', 'DEFERRED_QUIET', 'SHOWN')", (iso(self.clock.now()),)).rowcount


def list_notifications(conn, status=None, limit=50):
    if status == "unread":
        rows = conn.execute(
            "SELECT * FROM notification WHERE status IN ('PENDING', 'DEFERRED_QUIET', 'SHOWN') ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    elif status:
        rows = conn.execute(
            "SELECT * FROM notification WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM notification ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def counts(conn):
    rows = conn.execute("SELECT status, count(*) AS n FROM notification GROUP BY status").fetchall()
    by_status = {row["status"]: row["n"] for row in rows}
    return {"unread": sum(by_status.get(status, 0) for status in UNREAD), "pending": by_status.get("PENDING", 0)}


def list_events(conn, limit=50, since=None):
    if since:
        rows = conn.execute(
            "SELECT * FROM event WHERE created_at >= ? ORDER BY id DESC LIMIT ?", (since, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM event ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        out.append(item)
    return out
