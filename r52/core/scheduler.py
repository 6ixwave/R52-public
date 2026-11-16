"""Расписание: резервная копия, обслуживание базы и сводка.

Пропущенные во время сна запуски не копятся: выполняется один, следующий
считается от текущего времени. Ключ повтора задачи привязан к сроку запуска,
поэтому перезапуск в момент такта не создаёт вторую задачу.
"""

import json
import logging
import threading
from datetime import timedelta, timezone

from ..errors import AppError
from ..timeutil import iso, parse_hhmm

log = logging.getLogger("r52.scheduler")
TICK_SECONDS = 5
DEFAULTS = (
    # ключ, вид задачи, настройка времени, фиксированное время, настройка включения
    ("backup", "backup", "backup.time", None, None),
    ("maintenance", "maintenance", None, "05:00", None),
    ("digest", "digest", "digest.time", None, "digest.enabled"),
)


class Scheduler:
    def __init__(self, db, tasks, notifier, settings, clock, tz):
        self.db = db
        self.tasks = tasks
        self.notifier = notifier
        self.settings = settings
        self.clock = clock
        self.tz = tz
        self._stop = threading.Event()
        self._thread = None
        self.heartbeat = clock.monotonic()
        self.after_tick = None

    def next_due(self, rule, interval_seconds, daily_time, after):
        if rule == "interval":
            return after + timedelta(seconds=interval_seconds)
        hour, minute = parse_hhmm(daily_time)
        local = after.astimezone(self.tz)
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    def _plan(values):
        plan = []
        for key, kind, time_setting, fixed, enabled_setting in DEFAULTS:
            daily = values[time_setting] if time_setting else fixed
            enabled = values[enabled_setting] if enabled_setting else True
            plan.append((key, kind, daily, bool(enabled)))
        return plan

    def ensure_defaults(self):
        now = self.clock.now()
        with self.db.write() as conn:
            for key, kind, daily, enabled in self._plan(self.settings.all()):
                conn.execute(
                    "INSERT OR IGNORE INTO schedule(key, kind, params_json, rule, daily_time, enabled, next_due_at, "
                    "created_at) VALUES(?, ?, '{}', 'daily', ?, ?, ?, ?)",
                    (key, kind, daily, int(enabled), iso(self.next_due("daily", None, daily, now)), iso(now)),
                )

    def sync_settings(self, changed, values):
        if changed & {"autocollect.enabled", "autocollect.refresh_seconds", "autocollect.discovery_seconds"}:
            self.configure_auto(values)
        if not changed & {"backup.time", "digest.time", "digest.enabled"}:
            return
        now = self.clock.now()
        with self.db.write() as conn:
            for key, _kind, daily, enabled in self._plan(values):
                row = conn.execute("SELECT daily_time, enabled FROM schedule WHERE key = ?", (key,)).fetchone()
                if row is None or (row["daily_time"] == daily and bool(row["enabled"]) == enabled):
                    continue
                conn.execute(
                    "UPDATE schedule SET daily_time = ?, enabled = ?, next_due_at = ? WHERE key = ?",
                    (daily, int(enabled), iso(self.next_due("daily", None, daily, now)), key),
                )

    def list(self):
        with self.db.read() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM schedule ORDER BY key")]

    def tick(self):
        now = self.clock.now()
        stamp = iso(now)
        submitted = []
        with self.db.read() as conn:
            due = [dict(row) for row in conn.execute(
                "SELECT * FROM schedule WHERE enabled = 1 AND next_due_at <= ? ORDER BY next_due_at", (stamp,))]
        for item in due:
            task_id = None
            try:
                task, _ = self.tasks.submit(
                    item["kind"], json.loads(item["params_json"]), origin="SCHEDULE",
                    idempotency_key=f"schedule:{item['key']}:{item['next_due_at']}",
                )
                task_id = task["id"]
                submitted.append(task_id)
            except AppError as error:
                log.warning("Расписание %s не поставило задачу: %s", item["key"], error.code)
            following = iso(self.next_due(item["rule"], item["interval_seconds"], item["daily_time"], now))
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE schedule SET next_due_at = ?, last_task_id = COALESCE(?, last_task_id), last_run_at = ? "
                    "WHERE key = ? AND next_due_at = ?",
                    (following, task_id, stamp, item["key"], item["next_due_at"]),
                )
        released = 0
        if not self.notifier.quiet_now():
            with self.db.read() as conn:
                deferred = conn.execute("SELECT count(*) FROM notification WHERE status = 'DEFERRED_QUIET'").fetchone()[0]
            if deferred:
                with self.db.write() as conn:
                    released = self.notifier.release_deferred(conn)
        self.heartbeat = self.clock.monotonic()
        return {"submitted": submitted, "released": released}

    def configure_auto(self, values):
        now = self.clock.now()
        # Per-target intervals may be shorter than either global interval.
        with self.db.write() as conn:
            conn.execute("""
                INSERT INTO schedule(key,kind,rule,interval_seconds,enabled,next_due_at,created_at)
                VALUES('autocollect','autocollect','interval',60,?,?,?)
                ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,next_due_at=excluded.next_due_at
            """, (int(values["autocollect.enabled"]), iso(now+timedelta(seconds=15)), iso(now)))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.configure_auto(self.settings.all())
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="r52-scheduler", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
                if self.after_tick:
                    self.after_tick()
            except Exception:  # граница цикла: сбой одного такта не останавливает расписание
                log.exception("Сбой такта расписания")

    def stop(self, timeout=10):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())
