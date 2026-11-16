"""Перезапуск ядра при сбоях для режима без окна (Планировщик заданий Windows).

Ядро запускается дочерним процессом с каналом stdin: если надзорщик завершится,
канал закроется и ядро остановится само, без процессов без хозяина.
"""

import logging
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("r52.supervisor")
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_ALREADY_RUNNING, EXIT_CONFIG = 0, 1, 2, 3, 4
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FINAL_CODES = (EXIT_USAGE, EXIT_ALREADY_RUNNING, EXIT_CONFIG)


class RestartPolicy:
    def __init__(self, delays=(1, 2, 5, 10, 30, 60), max_crashes=5, window_seconds=600):
        self.delays = tuple(delays)
        self.max_crashes = max_crashes
        self.window_seconds = window_seconds
        self.crashes = deque()

    def record_crash(self, now):
        """Записывает сбой. Возвращает False, если сбоев за окно слишком много."""
        self.crashes.append(now)
        while self.crashes and now - self.crashes[0] > self.window_seconds:
            self.crashes.popleft()
        return len(self.crashes) <= self.max_crashes

    def delay(self):
        index = min(max(len(self.crashes) - 1, 0), len(self.delays) - 1)
        return self.delays[index]


def core_command(home=None):
    command = [sys.executable, "-m", "r52", "serve", "--parent-stdin"]
    if home:
        command += ["--home", str(home)]
    return command


def _spawn(command):
    return subprocess.Popen(
        command, cwd=PROJECT_ROOT, stdin=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _finish(process):
    if process.stdin:
        process.stdin.close()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        log.warning("Ядро не остановилось за 20 секунд, завершаю принудительно")
        process.kill()
        process.wait()


def supervise(home=None, policy=None, spawn=None, sleep=time.sleep, monotonic=time.monotonic):
    policy = policy or RestartPolicy()
    command = core_command(home)
    spawn = spawn or (lambda: _spawn(command))
    while True:
        process = spawn()
        try:
            code = process.wait()
        except KeyboardInterrupt:
            log.info("Остановка по запросу пользователя")
            _finish(process)
            return EXIT_OK
        if code == EXIT_OK:
            log.info("Ядро завершилось штатно")
            return EXIT_OK
        if code in FINAL_CODES:
            log.error("Ядро завершилось с кодом %s, перезапуск не поможет", code)
            return code
        if not policy.record_crash(monotonic()):
            log.error("Слишком много сбоев за %s секунд, надзор остановлен", policy.window_seconds)
            return EXIT_ERROR
        delay = policy.delay()
        log.warning("Ядро упало с кодом %s, перезапуск через %s с", code, delay)
        sleep(delay)
