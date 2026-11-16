"""Очередь задач с явными состояниями, ключом повтора и восстановлением после перезапуска.

Задача, прерванная перезапуском, автоматически повторяется только если её обработчик
безопасен для повтора. Иначе она остаётся INTERRUPTED с понятной причиной.
"""

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from ..errors import MESSAGES, AppError
from ..timeutil import iso

log = logging.getLogger("r52.tasks")

TRANSITIONS = {
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "BLOCKED"},
    "INTERRUPTED": {"QUEUED", "CANCELLED"},
}
TERMINAL = ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "BLOCKED")
ORIGINS = ("USER", "SCHEDULE", "SYSTEM", "ASSISTANT")
MAX_QUEUED = 200
MAX_ATTEMPTS = 3
KEY = re.compile(r"^[A-Za-z0-9:._+-]{1,200}$")


class TaskCancelled(Exception):
    pass


class TaskInterrupted(Exception):
    pass


class TaskOutcome(Exception):
    """Завершение без успеха, но и не сбой: BLOCKED или PARTIAL с результатом."""

    def __init__(self, status, code, result=None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.result = result


def _same(params):
    return params


@dataclass
class Handler:
    kind: str
    run: Callable
    validate: Callable = _same
    retry_safe: bool = False
    timeout_seconds: int = 900
    title: str = ""


class TaskContext:
    def __init__(self, manager, task_id, deadline):
        self.manager = manager
        self.task_id = task_id
        self.deadline = deadline

    def progress(self, percent):
        self.manager.heartbeat = self.manager.clock.monotonic()
        with self.manager.db.write() as conn:
            conn.execute(
                "UPDATE task SET progress = ? WHERE id = ? AND status = 'RUNNING'",
                (max(0, min(99, int(percent))), self.task_id),
            )

    def check(self):
        self.manager.heartbeat = self.manager.clock.monotonic()
        if self.manager.stopping.is_set():
            raise TaskInterrupted()
        if self.manager.clock.now() > self.deadline:
            raise AppError("LOCAL_TASK_DEADLINE")
        with self.manager.db.read() as conn:
            row = conn.execute("SELECT cancel_requested FROM task WHERE id = ?", (self.task_id,)).fetchone()
        if row and row["cancel_requested"]:
            raise TaskCancelled()


def _dump(value):
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def task_dict(row, transitions=None):
    data = dict(row)
    data["params"] = json.loads(data.pop("params_json"))
    result = data.pop("result_json")
    data["result"] = json.loads(result) if result else None
    data["cancel_requested"] = bool(data["cancel_requested"])
    if transitions is not None:
        data["transitions"] = transitions
    return data


class TaskManager:
    def __init__(self, db, clock):
        self.db = db
        self.clock = clock
        self.handlers = {}
        self.stopping = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self.heartbeat = clock.monotonic()
        self.current = None

    def register(self, handler):
        self.handlers[handler.kind] = handler

    def kinds(self):
        return [{"kind": h.kind, "title": h.title, "retry_safe": h.retry_safe} for h in self.handlers.values()]

    def _transition(self, conn, task_id, before, after, note=None, **fields):
        if after not in TRANSITIONS.get(before, ()):
            raise AppError("INVALID_TASK_TRANSITION", status=409, detail={"from": before, "to": after})
        assignments = ["status = ?"] + [f"{key} = ?" for key in fields]
        cursor = conn.execute(
            f"UPDATE task SET {', '.join(assignments)} WHERE id = ? AND status = ?",
            [after, *fields.values(), task_id, before],
        )
        if cursor.rowcount != 1:
            raise AppError("INVALID_TASK_TRANSITION", status=409, detail={"from": before, "to": after})
        conn.execute(
            "INSERT INTO task_transition(task_id, before, after, at, note) VALUES(?, ?, ?, ?, ?)",
            (task_id, before, after, iso(self.clock.now()), note),
        )

    @staticmethod
    def _row(conn, task_id):
        row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise AppError("TASK_NOT_FOUND", status=404)
        return row

    def submit(self, kind, params=None, origin="USER", idempotency_key=None):
        """Возвращает (task, created). Тот же ключ с теми же параметрами возвращает прежнюю задачу."""
        handler = self.handlers.get(kind)
        if handler is None:
            raise AppError("UNSUPPORTED_TASK", detail=kind)
        if origin not in ORIGINS:
            raise AppError("INVALID_INPUT", detail={"field": "origin", "reason": "choice"})
        if params is not None and not isinstance(params, dict):
            raise AppError("INVALID_INPUT", detail={"field": "params", "reason": "object_required"})
        params = handler.validate(dict(params or {}))
        key = idempotency_key or f"auto:{uuid.uuid4().hex}"
        if not isinstance(key, str) or not KEY.match(key):
            raise AppError("INVALID_INPUT", detail={"field": "idempotency_key", "reason": "pattern"})
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = iso(self.clock.now())
        with self.db.write() as conn:
            row = conn.execute("SELECT * FROM task WHERE idempotency_key = ?", (key,)).fetchone()
            if row is not None:
                if row["kind"] != kind or row["params_json"] != encoded:
                    raise AppError("IDEMPOTENCY_CONFLICT", status=409)
                return task_dict(row), False
            if kind == "autocollect":
                active = conn.execute("SELECT * FROM task WHERE kind=? AND status IN ('QUEUED','RUNNING') LIMIT 1",
                                      (kind,)).fetchone()
                if active:
                    return task_dict(active), False
            queued = conn.execute("SELECT count(*) FROM task WHERE status = 'QUEUED'").fetchone()[0]
            if queued >= MAX_QUEUED:
                raise AppError("QUEUE_FULL", status=429)
            task_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO task(id, kind, origin, params_json, idempotency_key, status, created_at) "
                "VALUES(?, ?, ?, ?, ?, 'QUEUED', ?)", (task_id, kind, origin, encoded, key, now))
            conn.execute(
                "INSERT INTO task_transition(task_id, before, after, at, note) VALUES(?, NULL, 'QUEUED', ?, ?)",
                (task_id, now, origin))
            row = self._row(conn, task_id)
        self._wake.set()
        return task_dict(row), True

    def cancel(self, task_id):
        with self.db.write() as conn:
            row = self._row(conn, task_id)
            status = row["status"]
            if status in ("QUEUED", "INTERRUPTED"):
                self._transition(conn, task_id, status, "CANCELLED", "USER_CANCEL",
                                 finished_at=iso(self.clock.now()), error_code="TASK_CANCELLED")
            elif status == "RUNNING":
                conn.execute("UPDATE task SET cancel_requested = 1 WHERE id = ?", (task_id,))
            else:
                raise AppError("TASK_NOT_CANCELLABLE", status=409)
            return task_dict(self._row(conn, task_id))

    def retry(self, task_id):
        with self.db.write() as conn:
            row = self._row(conn, task_id)
            if row["status"] != "INTERRUPTED":
                raise AppError("INVALID_TASK_TRANSITION", status=409, detail={"from": row["status"], "to": "QUEUED"})
            self._transition(conn, task_id, "INTERRUPTED", "QUEUED", "USER_RETRY", attempt=row["attempt"] + 1,
                             started_at=None, cancel_requested=0, error_code=None, error_message=None)
            row = self._row(conn, task_id)
        self._wake.set()
        return task_dict(row)

    def recover(self):
        """После запуска: RUNNING становится INTERRUPTED, безопасные для повтора снова ставятся в очередь."""
        interrupted = requeued = 0
        with self.db.write() as conn:
            for row in conn.execute("SELECT id FROM task WHERE status = 'RUNNING'").fetchall():
                self._transition(conn, row["id"], "RUNNING", "INTERRUPTED", "PROCESS_RESTART", error_code="PROCESS_RESTART")
                interrupted += 1
            for row in conn.execute("SELECT * FROM task WHERE status = 'INTERRUPTED'").fetchall():
                handler = self.handlers.get(row["kind"])
                if handler and handler.retry_safe and row["attempt"] < MAX_ATTEMPTS:
                    self._transition(conn, row["id"], "INTERRUPTED", "QUEUED", "AUTO_RETRY_SAFE",
                                     attempt=row["attempt"] + 1, started_at=None, cancel_requested=0, error_code=None)
                    requeued += 1
                elif row["error_code"] != "OUTCOME_UNKNOWN_NO_AUTOMATIC_RETRY":
                    conn.execute(
                        "UPDATE task SET error_code = 'OUTCOME_UNKNOWN_NO_AUTOMATIC_RETRY' WHERE id = ?", (row["id"],))
        if requeued:
            self._wake.set()
        return {"interrupted": interrupted, "requeued": requeued}

    def get(self, task_id):
        with self.db.read() as conn:
            row = self._row(conn, task_id)
            transitions = [dict(item) for item in conn.execute(
                "SELECT before, after, at, note FROM task_transition WHERE task_id = ? ORDER BY id", (task_id,))]
        return task_dict(row, transitions)

    def list(self, status=None, limit=50):
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM task WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM task ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [task_dict(row) for row in rows]

    def counts(self):
        with self.db.read() as conn:
            rows = conn.execute("SELECT status, count(*) AS n FROM task GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    def run_one(self):
        """Берёт одну задачу из очереди и выполняет её. Возвращает False, если очередь пуста."""
        started = self.clock.now()
        with self.db.write() as conn:
            row = conn.execute("SELECT * FROM task WHERE status = 'QUEUED' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if row is None:
                return False
            handler = self.handlers.get(row["kind"])
            if handler is None:
                self._transition(conn, row["id"], "QUEUED", "CANCELLED", "HANDLER_MISSING",
                                 finished_at=iso(started), error_code="UNSUPPORTED_TASK")
                return True
            deadline = started + timedelta(seconds=handler.timeout_seconds)
            self._transition(conn, row["id"], "QUEUED", "RUNNING", None,
                             started_at=iso(started), deadline_at=iso(deadline), progress=0)
        task_id = row["id"]
        self.current = task_id
        context = TaskContext(self, task_id, deadline)
        try:
            result = handler.run(context, json.loads(row["params_json"]))
            outcome, fields = "COMPLETED", {"result_json": _dump(result), "progress": 100}
        except TaskOutcome as signal:
            outcome = signal.status
            fields = {"result_json": _dump(signal.result), "error_code": signal.code,
                      "error_message": MESSAGES.get(signal.code)}
        except TaskCancelled:
            outcome, fields = "CANCELLED", {"error_code": "TASK_CANCELLED"}
        except TaskInterrupted:
            outcome, fields = "INTERRUPTED", {"error_code": "LOCAL_STOP_REQUESTED"}
        except AppError as error:
            outcome, fields = "FAILED", {"error_code": error.code, "error_message": error.message[:500]}
        except Exception as error:  # граница задачи: сбой записывается в задачу, поток работает дальше
            log.exception("Задача %s (%s) завершилась сбоем", task_id, row["kind"])
            outcome, fields = "FAILED", {"error_code": "UNEXPECTED_ERROR", "error_message": type(error).__name__}
        finally:
            self.current = None
        if outcome != "INTERRUPTED":
            fields["finished_at"] = iso(self.clock.now())
        with self.db.write() as conn:
            self._transition(conn, task_id, "RUNNING", outcome, None, **fields)
        log.info("Задача %s (%s): %s", task_id, row["kind"], outcome)
        return True

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.stopping.clear()
        self._thread = threading.Thread(target=self._loop, name="r52-worker", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self.stopping.is_set():
            self.heartbeat = self.clock.monotonic()
            try:
                ran = self.run_one()
            except Exception:  # граница цикла: сбой базы не должен останавливать рабочий поток
                log.exception("Сбой цикла задач")
                ran = False
                self.stopping.wait(2)
            if not ran:
                self._wake.wait(2)
                self._wake.clear()

    def stop(self, timeout=15):
        self.stopping.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        return not self.alive()

    def alive(self):
        return bool(self._thread and self._thread.is_alive())
