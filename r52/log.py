"""Журнал: файл с ротацией, скрытие секретов и буфер последних проблем для экрана состояния."""

import logging
import logging.handlers
import re
import sys
import threading
from collections import deque
from datetime import datetime, timezone

from .fsutil import read_tail

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [СКРЫТО]"),
    (re.compile(r"(?:sk-or-|sk-proj-|sk-|ghp_|gho_|xox[abp]-)[A-Za-z0-9_-]{8,}"), "[СЕКРЕТ СКРЫТ]"),
    (re.compile(r"(?i)\b(token|api_key|apikey|password|secret)([\"']?\s*[=:]\s*[\"']?)([^&\s\"',}]+)"), r"\1\2[СКРЫТО]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL СКРЫТ]"),
    (re.compile(r"(?:\+7|\b8)[\s()-]*(?:\d[\s()-]*){10}\b"), "[ТЕЛЕФОН СКРЫТ]"),
]


def sanitize(text):
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class UtcFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802 (имя из logging)
        return datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")

    def format(self, record):
        return sanitize(super().format(record))


class ProblemBuffer(logging.Handler):
    """Последние предупреждения и ошибки, чтобы показать их в окне без чтения файла."""

    def __init__(self, capacity=200):
        super().__init__(logging.WARNING)
        self.items = deque(maxlen=capacity)
        self._items_lock = threading.Lock()

    def emit(self, record):
        try:
            entry = {
                "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": sanitize(record.getMessage())[:500],
            }
        except Exception:  # журнал не должен ронять приложение, стандартный путь logging
            self.handleError(record)
            return
        with self._items_lock:
            self.items.append(entry)

    def snapshot(self, limit=50):
        with self._items_lock:
            return list(self.items)[-limit:][::-1]

    def clear(self):
        with self._items_lock:
            self.items.clear()


PROBLEMS = ProblemBuffer()
_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"


def setup_logging(logs_dir, level="INFO", console=False):
    root = logging.getLogger("r52")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
    root.propagate = False
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "core.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(UtcFormatter(_FORMAT))
    root.addHandler(file_handler)
    root.addHandler(PROBLEMS)
    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(UtcFormatter(_FORMAT))
        root.addHandler(stream)
    install_excepthooks()
    return root


def install_excepthooks():
    crash = logging.getLogger("r52.crash")

    def process_hook(exc_type, exc, tb):
        crash.critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        crash.critical("Необработанная ошибка в потоке %s", name,
                       exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = process_hook
    threading.excepthook = thread_hook


def tail(logs_dir, lines=200):
    text = read_tail(logs_dir / "core.log")
    rows = text.splitlines()[-max(1, min(lines, 1000)):]
    return [sanitize(row) for row in rows]
