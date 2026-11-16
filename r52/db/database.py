"""Подключение к SQLite.

Правила: WAL, внешние ключи включены, один писатель в процессе, короткие
транзакции. Внутри записи нельзя открывать вторую запись: это ловится сразу,
а не превращается в подвисание на блокировке.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..errors import AppError

MIN_SQLITE = (3, 37, 0)  # STRICT таблицы


def sqlite_ok():
    return sqlite3.sqlite_version_info >= MIN_SQLITE


class Database:
    def __init__(self, path, busy_timeout_ms=10_000):
        self.path = Path(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._writer = threading.Lock()
        self._local = threading.local()

    def connect(self):
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def initialize(self):
        if not sqlite_ok():
            raise AppError("SQLITE_TOO_OLD", status=500, detail=sqlite3.sqlite_version)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        finally:
            conn.close()
        if str(mode).lower() != "wal":
            raise AppError("DB_WAL_UNAVAILABLE", status=500, detail=str(mode))

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self):
        if getattr(self._local, "writing", False):
            raise AppError("NESTED_WRITE", status=500)
        with self._writer:
            self._local.writing = True
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                conn.close()
                self._local.writing = False

    def quick_check(self):
        with self.read() as conn:
            return conn.execute("PRAGMA quick_check").fetchone()[0]

    def checkpoint(self):
        with self.read() as conn:
            return tuple(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())

    def size_bytes(self):
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
        return total

    def close(self):
        """WAL checkpoint + освобождение. Для совместимости с тестами/сервисами."""
        try:
            self.checkpoint()
        except Exception:
            pass
