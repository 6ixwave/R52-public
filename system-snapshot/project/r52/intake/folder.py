"""Наблюдение за папками: файлы jarvis-eye*.json попадают во входящие.

Файл берётся, только когда его размер и время изменения не менялись два такта
подряд (браузер закончил запись). Символические ссылки и вложенные папки не
просматриваются. Файлы не удаляются и не перемещаются.
"""

import fnmatch
import logging
import os
import threading
from pathlib import Path

from ..timeutil import iso
from .eye import MAX_BYTES

log = logging.getLogger("r52.intake")
PATTERN = "jarvis-eye*.json"
TICK_SECONDS = 2
MAX_FILES_PER_TICK = 20


class FolderWatcher:
    def __init__(self, app, folders):
        self.app = app
        self.folders = [Path(folder) for folder in folders]
        self._pending = {}
        self._warned = set()
        self._stop = threading.Event()
        self._thread = None
        self.heartbeat = app.clock.monotonic()
        self.last_error = None

    def _candidates(self):
        found = []
        for folder in self.folders:
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if not fnmatch.fnmatch(entry.name.lower(), PATTERN):
                            continue
                        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                        found.append((entry.path, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                continue
            except OSError as error:
                self.last_error = f"{folder}: {error.strerror or error}"
                if str(folder) not in self._warned:
                    self._warned.add(str(folder))
                    log.warning("Папка недоступна: %s", self.last_error)
        return found

    def scan(self):
        if not self.app.settings.get("intake.enabled"):
            return []
        candidates = self._candidates()
        if not candidates:
            self._pending.clear()
            return []
        with self.app.db.read() as conn:
            known = {row["path"]: (row["size"], row["mtime_ns"])
                     for row in conn.execute("SELECT path, size, mtime_ns FROM intake_file")}
        staged, present = [], set()
        for path, size, mtime in candidates:
            present.add(path)
            if known.get(path) == (size, mtime):
                continue
            if self._pending.get(path) != (size, mtime):
                self._pending[path] = (size, mtime)
                continue
            if len(staged) >= MAX_FILES_PER_TICK:
                break
            result = self._ingest(path, size, mtime)
            if result:
                staged.append(result)
        for path in list(self._pending):
            if path not in present:
                del self._pending[path]
        return staged

    def _ingest(self, path, size, mtime):
        digest = None
        if size > MAX_BYTES:
            result = "TOO_LARGE"
        else:
            try:
                data = Path(path).read_bytes()
            except OSError as error:
                # Файл может быть ещё занят браузером: попробуем на следующем такте.
                if path not in self._warned:
                    self._warned.add(path)
                    log.warning("Не удалось прочитать %s: %s", path, error.strerror or error)
                return None
            item, created = self.app.inbox.stage_bytes(data, filename=Path(path).name, origin="FOLDER")
            digest = item["content_sha256"]
            result = item["status"] if created else "DUPLICATE"
        with self.app.db.write() as conn:
            conn.execute(
                "INSERT INTO intake_file(path, size, mtime_ns, content_sha256, result, seen_at) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET size = excluded.size, mtime_ns = excluded.mtime_ns, "
                "content_sha256 = excluded.content_sha256, result = excluded.result, seen_at = excluded.seen_at",
                (path, size, mtime, digest, result, iso(self.app.clock.now())),
            )
        self._pending.pop(path, None)
        return {"path": path, "result": result}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="r52-intake", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(TICK_SECONDS):
            self.heartbeat = self.app.clock.monotonic()
            try:
                self.scan()
            except Exception:  # граница цикла: плохой файл или занятая база не останавливают наблюдение
                log.exception("Сбой просмотра папки")

    def stop(self, timeout=10):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())
