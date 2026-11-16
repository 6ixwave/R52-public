"""Один процесс ядра на одну папку данных.

Блокировка снимается операционной системой при любом завершении процесса,
поэтому после сбоя не остаётся висячего замка.
"""

import sys
from pathlib import Path

from .errors import AppError


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self._handle = None

    @property
    def held(self):
        return self._handle is not None

    def acquire(self):
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AppError("CORE_ALREADY_RUNNING", status=409) from None
        self._handle = handle

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def is_core_running(lock_path):
    probe = ProcessLock(lock_path)
    try:
        probe.acquire()
    except AppError:
        return True
    probe.release()
    return False
