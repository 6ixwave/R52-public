"""Сборка ядра: база, настройки, задачи, расписание, входящие, помощник и API."""

import json
import logging
import os
import secrets
import sqlite3
import threading
import uuid
from pathlib import Path

from . import SERVICE_ID, __version__, netguard
from .api.routes import build_router
from .api.server import ApiServer
from .api.uiauth import UiAuth
from .assistant.engine import Assistant
from .core.handlers import register_all
from .core.notify import Notifier
from .core.notify import counts as notification_counts
from .core.scheduler import Scheduler
from .core.settings import Settings
from .core.tasks import TaskManager
from .db import Database, migrate
from .db.backup import list_backups
from .db.migrate import schema_version
from .fsutil import atomic_write_json, atomic_write_text
from .intake.eye import Inbox
from .intake.folder import FolderWatcher
from .lease import ProcessLock
from .log import PROBLEMS
from .opener import open_path
from .sources import REGISTRY, sync_sources_to_db
from .timeutil import Clock, iso

log = logging.getLogger("r52.service")
WORKER_STALE_SECONDS = 60
SCHEDULER_STALE_SECONDS = 30
WEB_ROOT = Path(__file__).resolve().parent / "web"


class App:
    def __init__(self, config, clock=None, install_guard=True):
        self.config = config
        self.paths = config.paths
        self.ui = None
        self.opener = open_path
        self.clock = clock or Clock()
        self.tz = config.tz
        self.install_guard = install_guard
        self.instance = uuid.uuid4().hex
        self.lock = ProcessLock(self.paths.lock_file)
        self.db = Database(self.paths.db_file)
        self.settings = self.notifier = self.tasks = self.scheduler = None
        self.inbox = self.watcher = self.assistant = self.server = None
        self.token = self.port = self.started_at = None
        self.shutdown_requested = threading.Event()
        self._server_thread = None
        self._opened = False
        self._running = False

    def open(self):
        """Открывает базу и компоненты без фоновых потоков и без сервера (для команд CLI)."""
        if self._opened:
            return self
        self.paths.ensure()
        self.lock.acquire()
        self.shutdown_requested.clear()
        try:
            if self.install_guard:
                netguard.install()
            self.db.initialize()
            result = migrate(self.db, backup_dir=self.paths.backups_dir, now=self.clock.now())
            if result["applied"]:
                log.info("Применены миграции базы: %s", result["applied"])
            with self.db.write() as conn:
                sync_sources_to_db(conn)
            self.settings = Settings(self.db, self.clock)
            self.notifier = Notifier(self.settings, self.clock, self.tz)
            self.tasks = TaskManager(self.db, self.clock)
            register_all(self)
            self.scheduler = Scheduler(self.db, self.tasks, self.notifier, self.settings, self.clock, self.tz)
            self.scheduler.ensure_defaults()
            self.settings.on_change(self.scheduler.sync_settings)
            self.inbox = Inbox(self)
            from .core.autocollect import accept_deliveries
            folders = tuple(dict.fromkeys((*self.config.intake_folders, self.paths.home / "auto-inbox")))
            self.watcher = FolderWatcher(self, folders)
            self.scheduler.after_tick = lambda: accept_deliveries(self)
            self.assistant = Assistant(self)
        except BaseException:
            self.lock.release()
            raise
        self._opened = True
        return self

    def start(self, port=None):
        self.open()
        try:
            recovered = self.tasks.recover()
            if recovered["interrupted"] or recovered["requeued"]:
                log.warning("После перезапуска задачи восстановлены: %s", recovered)
            self.token = secrets.token_urlsafe(32)
            self.ui = UiAuth(self.paths.run_dir / "ui-sessions.json")
            self.server = self._bind(self.config.api_port if port is None else port)
            self.port = self.server.port
            self._server_thread = threading.Thread(
                target=self.server.serve_forever, kwargs={"poll_interval": 0.5}, name="r52-api", daemon=True)
            self._server_thread.start()
            self.tasks.start()
            self.scheduler.start()
            self.watcher.start()
            self.started_at = iso(self.clock.now())
            self._running = True
            atomic_write_json(self.paths.runtime_file, self.runtime_info())
            self._remember_port()
            log.info("Ядро R-52 %s запущено на 127.0.0.1:%s", __version__, self.port)
        except BaseException:
            self.stop()
            raise
        return self

    def _bind(self, port):
        """Порт 0 значит любой свободный. Сначала пробуем прошлый, чтобы открытое окно переподключилось."""
        router = build_router(self)
        if port == 0:
            last = self._last_port()
            if last:
                try:
                    return ApiServer(last, router, self.token, ui=self.ui, web_root=WEB_ROOT)
                except OSError:
                    log.info("Прошлый порт %s занят, беру свободный", last)
        return ApiServer(port, router, self.token, ui=self.ui, web_root=WEB_ROOT)

    def _last_port(self):
        try:
            value = int((self.paths.run_dir / "last-port").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return value if 1024 <= value <= 65535 else None

    def _remember_port(self):
        try:
            atomic_write_text(self.paths.run_dir / "last-port", str(self.port))
        except OSError as error:
            log.warning("Не удалось запомнить порт: %s", error)

    def runtime_info(self):
        return {"service": SERVICE_ID, "pid": os.getpid(), "port": self.port, "token": self.token,
                "started_at": self.started_at, "version": __version__, "instance": self.instance}

    def request_shutdown(self):
        self.shutdown_requested.set()
        return {"stopping": True}

    def stop(self):
        self.shutdown_requested.set()
        self._running = False
        for component in (self.watcher, self.scheduler):
            if component is not None:
                component.stop()
        if self.tasks is not None:
            self.tasks.stop()
        if self.server is not None:
            if self._server_thread is not None and self._server_thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._opened:
            try:
                self.db.checkpoint()
            except sqlite3.Error as error:
                log.warning("Не удалось сбросить журнал WAL при остановке: %s", error)
            self._remove_runtime()
            self.lock.release()
            self._opened = False
            log.info("Ядро R-52 остановлено")

    def _remove_runtime(self):
        try:
            data = json.loads(self.paths.runtime_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            log.warning("Файл runtime.json не прочитан при остановке: %s", error)
            return
        if data.get("instance") == self.instance:
            self.paths.runtime_file.unlink(missing_ok=True)

    def health(self):
        base = {"service": SERVICE_ID, "version": __version__, "instance": self.instance}
        if not self._running:
            return {**base, "ok": False, "components": {}}
        mono = self.clock.monotonic()
        worker = self.tasks.alive() and (
            mono - self.tasks.heartbeat < WORKER_STALE_SECONDS or self.tasks.current is not None)
        scheduler = self.scheduler.alive() and mono - self.scheduler.heartbeat < SCHEDULER_STALE_SECONDS
        intake = self.watcher.alive()
        return {**base, "ok": bool(worker and scheduler and intake),
                "components": {"worker": bool(worker), "scheduler": bool(scheduler), "intake": bool(intake)}}

    def sources(self):
        with self.db.read() as conn:
            rows = [dict(row) for row in conn.execute("SELECT key, name, kind FROM source ORDER BY key")]
        for row in rows:
            row["collector"] = "READY" if row["key"] in REGISTRY else "NOT_IMPLEMENTED"
        return rows

    def status(self):
        with self.db.read() as conn:
            products = conn.execute("SELECT count(*) FROM product").fetchone()[0]
            observations = conn.execute("SELECT count(*) FROM observation").fetchone()[0]
            price_points = conn.execute("SELECT count(*) FROM price_point").fetchone()[0]
            notifications = notification_counts(conn)
            imports = [dict(row) for row in conn.execute(
                "SELECT kind, source_path, finished_at, status FROM import_run ORDER BY id DESC LIMIT 5")]
        backups = list_backups(self.paths.backups_dir)
        return {
            "service": SERVICE_ID, "version": __version__, "instance": self.instance, "pid": os.getpid(),
            "started_at": self.started_at, "home": str(self.paths.home), "timezone": self.config.timezone,
            "health": self.health(),
            "database": {"path": str(self.paths.db_file), "size_bytes": self.db.size_bytes(),
                         "schema_version": schema_version(self.db), "products": products,
                         "observations": observations, "price_points": price_points},
            "tasks": self.tasks.counts(),
            "inbox_pending": self.inbox.pending_count(),
            "notifications": notifications,
            "backups": {"count": len(backups), "last": backups[0] if backups else None},
            "sources": self.sources(),
            "intake": {"folders": [str(folder) for folder in self.watcher.folders],
                       "enabled": self.settings.get("intake.enabled"), "last_error": self.watcher.last_error},
            "network_guard": netguard.installed(),
            "imports": imports,
            "problems": PROBLEMS.snapshot(20),
        }
