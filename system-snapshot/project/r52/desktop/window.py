"""Окно R-52.

Находит или запускает ядро, получает одноразовый код входа и открывает интерфейс отдельным окном
Microsoft Edge или Google Chrome: режим приложения без вкладок и свой профиль в папке данных R-52.
Если ядро запустило само окно, лаунчер держит его, пока интерфейс открыт, и останавливает после
закрытия окна (не прерывая идущие задачи). Если подходящего браузера нет, ссылка открывается в
браузере по умолчанию.
"""

import argparse
import logging
import logging.handlers
import ntpath
import os
import shutil
import subprocess
import sys
import time
import webbrowser

from ..errors import AppError
from ..log import UtcFormatter
from .link import CoreLink

log = logging.getLogger("r52.window")

ENV_BROWSER = "R52_BROWSER"
WINDOW_SIZE = "1360,880"
POLL_SECONDS = 1.0
FIRST_VISIT_SECONDS = 120
GONE_SECONDS = 12
TASK_WAIT_SECONDS = 3600
MAX_RESTARTS = 3

_WINDOWS_PATHS = (
    ("PROGRAMFILES(X86)", "Microsoft\\Edge\\Application\\msedge.exe"),
    ("PROGRAMFILES", "Microsoft\\Edge\\Application\\msedge.exe"),
    ("LOCALAPPDATA", "Microsoft\\Edge\\Application\\msedge.exe"),
    ("PROGRAMFILES", "Google\\Chrome\\Application\\chrome.exe"),
    ("PROGRAMFILES(X86)", "Google\\Chrome\\Application\\chrome.exe"),
    ("LOCALAPPDATA", "Google\\Chrome\\Application\\chrome.exe"),
    ("PROGRAMFILES", "BraveSoftware\\Brave-Browser\\Application\\brave.exe"),
    ("LOCALAPPDATA", "BraveSoftware\\Brave-Browser\\Application\\brave.exe"),
    ("LOCALAPPDATA", "Yandex\\YandexBrowser\\Application\\browser.exe"),
)
_REGISTRY_NAMES = ("msedge.exe", "chrome.exe", "brave.exe")
_MAC_PATHS = (
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_UNIX_NAMES = ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
               "chromium", "chromium-browser", "brave-browser")


def _registry_paths():
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for name in _REGISTRY_NAMES:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                value = winreg.QueryValue(hive, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name)
            except OSError:
                continue
            if value:
                found.append(value.strip().strip('"'))
    return found


def browser_candidates(env=None, platform=None, registry=_registry_paths, which=shutil.which):
    """Кандидаты в порядке предпочтения: R52_BROWSER, Edge, Chrome, Brave, Яндекс Браузер."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    found = []
    custom = (env.get(ENV_BROWSER) or "").strip().strip('"')
    if custom:
        found.append(which(custom) or custom)
    if platform == "win32":
        for variable, tail in _WINDOWS_PATHS:
            base = env.get(variable)
            if base:
                found.append(ntpath.join(base, tail))
        found.extend(registry())
    elif platform == "darwin":
        found.extend(_MAC_PATHS)
    else:
        found.extend(path for path in map(which, _UNIX_NAMES) if path)
    return list(dict.fromkeys(found))


def find_browser(env=None, platform=None, registry=_registry_paths, which=shutil.which, is_file=os.path.isfile):
    for candidate in browser_candidates(env, platform, registry, which):
        if is_file(candidate):
            return candidate
    return None


def app_command(browser, url, profile_dir, first_run=False):
    """Команда окна приложения. Размер задаётся только в первый раз, дальше браузер его помнит."""
    command = [browser, "--app=" + url, "--user-data-dir=" + str(profile_dir), "--no-first-run",
               "--no-default-browser-check", "--disable-sync", "--disable-background-networking",
               "--disable-features=Translate"]
    if first_run:
        command.append("--window-size=" + WINDOW_SIZE)
    return command


def login_url(link):
    runtime = link.client.runtime()
    issued = link.client.request("POST", "/api/ui/session", {})
    return f"http://127.0.0.1:{runtime['port']}/ui/login?code=[REDACTED_AUTH_SECRET]'code']}"


def open_window(url, browser, profile_dir, popen=subprocess.Popen, fallback=webbrowser.open):
    """Открывает окно. Возвращает app, browser или None, если не получилось ничего."""
    if browser:
        try:
            command = app_command(browser, url, profile_dir, first_run=not os.path.isdir(profile_dir))
            popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True)
            log.info("Окно открыто через %s", browser)
            return "app"
        except OSError as error:
            log.warning("Браузер %s не запустился: %s", browser, error)
    try:
        if fallback(url):
            log.info("Интерфейс открыт в браузере по умолчанию")
            return "browser"
    except webbrowser.Error as error:
        log.warning("Браузер по умолчанию не открылся: %s", error)
    return None


def _port(link):
    return link.client.runtime()["port"]


def _presence(link):
    try:
        return link.client.request("GET", "/api/ui/presence", timeout=5)
    except AppError as error:
        log.debug("Присутствие окна не получено: %s", error.code)
        return None


def _busy(link):
    """Есть ли задачи в работе или в очереди. Если ядро не ответило, считаем, что нет."""
    try:
        tasks = link.client.request("GET", "/api/status", timeout=5).get("tasks") or {}
    except AppError:
        return False
    return (tasks.get("RUNNING") or 0) + (tasks.get("QUEUED") or 0) > 0


def hold(link, reopen, clock=time.monotonic, sleep=time.sleep):
    """Держит ядро, запущенное окном, пока интерфейс открыт. Возвращает код выхода лаунчера."""
    started, gone_since, restarts = clock(), None, 0
    port = _port(link)
    while True:
        code = link.exit_code()
        if code is not None:
            if code == 0:
                log.info("Ядро остановлено, лаунчер завершает работу")
                return 0
            if restarts >= MAX_RESTARTS:
                log.error("Ядро снова завершилось с кодом %s после %s перезапусков", code, restarts)
                return 1
            restarts += 1
            log.warning("Ядро завершилось с кодом %s, запускаю снова", code)
            if link.connect() != "started":
                return 0
            if _port(link) != port:
                port = _port(link)
                reopen()
            started, gone_since = clock(), None
            continue
        presence = _presence(link)
        now = clock()
        if presence is not None:
            if presence.get("pages"):
                gone_since = None
            elif presence.get("seen"):
                gone_since = now if gone_since is None else gone_since
                waited = now - gone_since
                if waited >= GONE_SECONDS and (waited >= GONE_SECONDS + TASK_WAIT_SECONDS or not _busy(link)):
                    log.info("Окно закрыто, останавливаю ядро")
                    break
            elif now - started >= FIRST_VISIT_SECONDS:
                log.info("Окно так и не открылось, останавливаю ядро")
                break
        sleep(POLL_SECONDS)
    link.stop()
    return 0


def _setup_logging(paths):
    root = logging.getLogger("r52")
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(paths.logs_dir / "window.log", maxBytes=500_000,
                                                       backupCount=2, encoding="utf-8", delay=True)
    except OSError:
        return
    handler.setFormatter(UtcFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _alert(text):
    """Сообщение человеку. У pythonw нет консоли, поэтому в Windows это системное окно."""
    log.warning("%s", " ".join(text.split()))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "R-52", 0x10 | 0x40000)
            return
        except (AttributeError, OSError):
            pass
    if sys.stderr is not None:
        sys.stderr.write(text + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m r52.desktop", description="Открывает окно R-52.")
    parser.add_argument("--home", help="папка данных R-52")
    parser.add_argument("--browser", help="путь к Edge, Chrome или другому браузеру на Chromium")
    parser.add_argument("--print-url", action="store_true", help="не открывать окно, только напечатать ссылку входа")
    args = parser.parse_args(argv)
    try:
        link = CoreLink(args.home)
    except AppError as error:
        _alert("Не удалось прочитать настройки R-52.\n\n" + error.message)
        return 1
    _setup_logging(link.paths)
    try:
        link.connect()
        browser = args.browser or find_browser()
        profile = link.paths.home / "ui" / "browser"

        def reopen():
            url = login_url(link)
            if args.print_url:
                print(url, flush=True)
            elif open_window(url, browser, profile) is None:
                _alert("Не получилось открыть окно R-52. Откройте эту ссылку в Edge или Chrome "
                       "в течение минуты:\n\n" + url)

        reopen()
        return hold(link, reopen) if link.owns_core else 0
    except AppError as error:
        log.error("Окно R-52 не открылось: %s %s", error.code, error.detail)
        link.stop()
        _alert("Не удалось открыть R-52.\n\n" + error.message + "\n\nЖурналы: " + str(link.paths.logs_dir))
        return 1
    except KeyboardInterrupt:
        link.stop()
        return 130
