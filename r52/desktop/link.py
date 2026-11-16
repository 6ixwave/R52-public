"""Связь окна с ядром.

Окно не открывает базу само. Если ядро уже работает (например, запущено в фоне через supervise),
окно подключается к нему по runtime.json. Иначе окно запускает своё ядро дочерним процессом
(`serve --parent-stdin --ready-stdout`). Такое ядро завершается, когда окно закрывает канал stdin, в том
числе если окно закрылось аварийно.

Модуль не зависит от графической среды, поэтому проверяется тестами без экрана.
"""

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..client import CoreClient
from ..config import load_config
from ..errors import AppError
from ..lease import is_core_running

log = logging.getLogger("r52.desktop.link")
READY_PREFIX = "R52_READY "
START_TIMEOUT = 40
STOP_TIMEOUT = 20
EXIT_ALREADY_RUNNING = 3
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def start_failed(detail):
    return AppError("CORE_START_FAILED", "Не удалось запустить ядро R-52", status=503, detail=detail)


class CoreLink:
    def __init__(self, home=None, python=None, start_timeout=START_TIMEOUT):
        self.config = load_config(home)
        self.paths = self.config.paths
        self.paths.ensure()
        self.client = CoreClient(self.paths)
        self.python = python or sys.executable
        self.start_timeout = start_timeout
        self.process = None
        self.mode = None

    @property
    def owns_core(self):
        return self.process is not None

    def exit_code(self):
        """Код выхода ядра, запущенного окном, если оно уже завершилось; иначе None."""
        process = self.process
        return None if process is None else process.poll()

    def connect(self):
        """Возвращает "started" (ядро запущено окном) или "attached" (окно подключилось к работающему)."""
        if self.process is not None and self.process.poll() is not None:
            self.process = None
        if self.process is not None:
            return self.mode
        if is_core_running(self.paths.lock_file):
            self._wait_ready()
            self.mode = "attached"
        else:
            self._start()
        return self.mode

    def _wait_ready(self):
        """Блокировка берётся раньше, чем появляется runtime.json, поэтому ждём ответа API."""
        deadline = time.monotonic() + self.start_timeout
        while True:
            try:
                self.client.request("GET", "/api/status", timeout=10)
                return
            except AppError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

    def _start(self):
        command = [self.python, "-m", "r52", "serve", "--parent-stdin", "--ready-stdout",
                   "--home", str(self.paths.home)]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(item for item in (str(PROJECT_ROOT), env.get("PYTHONPATH")) if item)
        env["PYTHONIOENCODING"] = "utf-8"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with open(self.paths.logs_dir / "core-console.log", "ab") as console:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=console,
                                       cwd=str(PROJECT_ROOT), env=env, creationflags=flags)
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), name="r52-ready", daemon=True).start()
        try:
            raw = lines.get(timeout=self.start_timeout)
        except queue.Empty:
            process.kill()
            raise start_failed(f"ядро не ответило за {self.start_timeout} с") from None
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith(READY_PREFIX):
            info = json.loads(line[len(READY_PREFIX):])
            self.process = process
            self.mode = "started"
            log.info("Ядро запущено окном: PID %s, порт %s", info.get("pid"), info.get("port"))
            return
        try:
            code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            code = None
        if code == EXIT_ALREADY_RUNNING:
            # кто-то запустил ядро одновременно с нами, подключаемся к нему
            self._wait_ready()
            self.mode = "attached"
            return
        raise start_failed(f"код выхода {code}; подробности в папке {self.paths.logs_dir}")

    def stop(self):
        """Останавливает ядро, если его запустило окно. Ядро, работавшее в фоне, продолжает работу."""
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.warning("Ядро не остановилось за %s с, процесс завершается принудительно", STOP_TIMEOUT)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        finally:
            if process.stdout is not None:
                process.stdout.close()
