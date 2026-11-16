"""Окно R-52: лаунчер и вход в интерфейс.

Настоящий браузер не запускается. Проверяются:
- поиск браузера, команда окна и запасной путь через браузер по умолчанию;
- правило, по которому лаунчер держит своё ядро, пока окно открыто, и перезапускает его после сбоя;
- вход по одноразовой ссылке, cookie сессии и доступ окна к API настоящего ядра;
- раздача файлов интерфейса, заголовки безопасности и целостность модулей интерфейса.

Внешний вид экранов эти тесты не проверяют.
"""

import contextlib
import http.client
import io
import json
import logging
import re
import shutil
import time
import unittest
import webbrowser
from pathlib import Path
from unittest import mock

import helpers
from r52.desktop import window
from r52.desktop.link import CoreLink
from r52.errors import AppError
from r52.lease import is_core_running

WEB = helpers.ROOT / "r52" / "web"
VIEWS = ("autocollect", "map", "overview", "products", "product", "inbox", "watch", "assistant", "tasks", "settings", "service")
LOGIN = re.compile(r"^http://127\.0\.0\.1:(\d+)(/ui/login\?code=[\w-]+)$")
OPEN = {"pages": 1, "seen": True}
CLOSED = {"pages": 0, "seen": True}
NEVER = {"pages": 0, "seen": False}
IMPORT = re.compile(r"""import\s*(?:\{([^}]*)\}\s*from\s*)?["'](\.{1,2}/[^"']+)["']""")
DYNAMIC = re.compile(r"""import\(\s*["'](\.{1,2}/[^"']+)["']\s*\)""")
DECLARED = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
LISTED = re.compile(r"^export\s*\{([^}]*)\}", re.M)


def fetch(port, path, method="GET", headers=None, body=None):
    """Запрос к ядру без перехода по редиректам. Возвращает (статус, заголовки, тело)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        sent, data = dict(headers or {}), None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def log_in(test, link):
    """Вход как у лаунчера: код по ключу ядра, затем cookie сессии по ссылке. Возвращает (путь входа, cookie)."""
    url = window.login_url(link)
    found = LOGIN.match(url)
    test.assertIsNotNone(found, url)
    port = int(found.group(1))
    test.assertEqual(port, link.client.runtime()["port"])
    status, headers, _ = fetch(port, found.group(2))
    test.assertEqual((status, headers["Location"]), (303, "/ui/"))
    cookie = headers["Set-Cookie"]
    for part in ("r52_session=", "HttpOnly", "SameSite=Strict", "Path=/"):
        test.assertIn(part, cookie)
    return found.group(2), cookie.split(";")[0]


def keep_logging(test):
    """main() добавляет журнал окна в файл. После теста обработчик убирается, уровень возвращается."""
    saved = [(logger, list(logger.handlers), logger.level) for logger in (logging.getLogger(), logging.getLogger("r52"))]

    def restore():
        for logger, handlers, level in saved:
            for handler in list(logger.handlers):
                if handler not in handlers:
                    logger.removeHandler(handler)
                    handler.close()
            logger.setLevel(level)

    test.addCleanup(restore)


# --- поиск браузера и открытие окна ------------------------------------------------

class BrowserTest(unittest.TestCase):
    ENV = {"PROGRAMFILES": "C:\\Program Files", "PROGRAMFILES(X86)": "C:\\Program Files (x86)",
           "LOCALAPPDATA": "C:\\Users\\kenn\\AppData\\Local"}
    EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
    CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

    def test_candidates_on_windows(self):
        found = window.browser_candidates(self.ENV, "win32", registry=lambda: [self.EDGE, "D:\\Chrome\\chrome.exe"],
                                          which=lambda name: None)
        self.assertEqual(found[0], self.EDGE)  # Edge есть в каждой Windows 10 и 11
        self.assertIn(self.CHROME, found)
        self.assertEqual(found[-1], "D:\\Chrome\\chrome.exe")  # пути из реестра идут последними
        self.assertEqual(len(found), len(set(found)))
        custom = window.browser_candidates({**self.ENV, window.ENV_BROWSER: ' "E:\\Portable\\chrome.exe" '}, "win32",
                                           registry=lambda: [], which=lambda name: None)
        self.assertEqual(custom[:2], ["E:\\Portable\\chrome.exe", self.EDGE])  # своя настройка важнее

    def test_candidates_elsewhere(self):
        paths = {"chromium": "/usr/bin/chromium", "google-chrome": "/opt/google/chrome/google-chrome"}
        self.assertEqual(window.browser_candidates({}, "linux", which=paths.get),
                         ["/opt/google/chrome/google-chrome", "/usr/bin/chromium"])
        self.assertEqual(window.browser_candidates({}, "darwin", which=paths.get), list(window._MAC_PATHS))
        self.assertEqual(window.browser_candidates({window.ENV_BROWSER: "chromium"}, "linux", which=paths.get),
                         ["/usr/bin/chromium", "/opt/google/chrome/google-chrome"])

    def test_find_browser(self):
        options = dict(env=self.ENV, platform="win32", registry=lambda: [], which=lambda name: None)
        self.assertEqual(window.find_browser(**options, is_file={self.CHROME}.__contains__), self.CHROME)
        self.assertIsNone(window.find_browser(**options, is_file=lambda path: False))

    def test_app_command(self):
        url = "http://127.0.0.1:8752/ui/login?code=abc"
        first = window.app_command("msedge.exe", url, Path("profile"), first_run=True)
        self.assertEqual(first[:2], ["msedge.exe", "--app=" + url])
        self.assertIn("--user-data-dir=" + str(Path("profile")), first)
        self.assertIn("--window-size=" + window.WINDOW_SIZE, first)
        later = window.app_command("msedge.exe", url, Path("profile"))
        self.assertEqual([part for part in later if part.startswith("--window-size")], [])  # размер браузер помнит сам

    def test_open_window(self):
        profile = helpers.temp_home(self) / "browser"
        started, opened = [], []

        def popen(command, **kwargs):
            started.append(command)

        def fallback(url):
            opened.append(url)
            return True

        self.assertEqual(window.open_window("URL", "msedge.exe", profile, popen=popen, fallback=fallback), "app")
        self.assertIn("--app=URL", started[-1])
        self.assertIn("--window-size=" + window.WINDOW_SIZE, started[-1])  # профиля ещё нет: первый запуск
        profile.mkdir(exist_ok=True)
        self.assertEqual(window.open_window("URL", "msedge.exe", profile, popen=popen, fallback=fallback), "app")
        self.assertEqual([part for part in started[-1] if part.startswith("--window-size")], [])
        self.assertEqual(opened, [])

        def broken(command, **kwargs):
            raise FileNotFoundError(command[0])

        with self.assertLogs("r52.window", "WARNING"):
            self.assertEqual(window.open_window("URL", "нет.exe", profile, popen=broken, fallback=fallback), "browser")
        self.assertEqual(opened, ["URL"])
        self.assertIsNone(window.open_window("URL", None, profile, popen=broken, fallback=lambda url: False))

        def refused(url):
            raise webbrowser.Error("браузер не найден")

        with self.assertLogs("r52.window", "WARNING"):
            self.assertIsNone(window.open_window("URL", None, profile, popen=broken, fallback=refused))


# --- лаунчер держит своё ядро, пока окно открыто -----------------------------------

class FakeClient:
    def __init__(self, link):
        self.link = link

    def runtime(self):
        return {"port": self.link.port}

    def request(self, method, path, body=None, timeout=None):
        if path == "/api/ui/presence":
            states = self.link.presence
            answer = states.pop(0) if len(states) > 1 else states[0]
        elif path == "/api/status":
            answer = self.link.status
        else:
            raise AssertionError(path)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeLink:
    """Ядро, запущенное окном: ответы о присутствии страниц по очереди, коды выхода, перезапуски."""

    def __init__(self, presence, tasks=None, exits=(), ports=(), mode="started"):
        self.presence = list(presence)
        self.status = {"tasks": tasks or {}}
        self.exits = list(exits)
        self.ports = list(ports)
        self.port = 8752
        self.mode = mode
        self.client = FakeClient(self)
        self.connects = self.stops = 0

    def exit_code(self):
        return self.exits.pop(0) if self.exits else None

    def connect(self):
        self.connects += 1
        if self.ports:
            self.port = self.ports.pop(0)
        return self.mode

    def stop(self):
        self.stops += 1


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class HoldTest(unittest.TestCase):
    def hold(self, link):
        clock, reopened = Clock(), []
        code = window.hold(link, lambda: reopened.append(link.port), clock=clock, sleep=clock.sleep)
        return code, clock.now, reopened

    def test_stops_after_window_closed(self):
        link = FakeLink([OPEN] * 30 + [CLOSED])
        code, now, reopened = self.hold(link)
        self.assertEqual((code, link.stops, reopened), (0, 1, []))
        self.assertEqual(now, 30 + window.GONE_SECONDS)

    def test_short_gap_keeps_core(self):
        """Перезагрузка страницы ненадолго убирает её из списка открытых, но ядро не останавливается."""
        link = FakeLink([OPEN] * 5 + [CLOSED] * 3 + [OPEN] * 5 + [CLOSED])
        code, now, _ = self.hold(link)
        self.assertEqual((code, link.stops, now), (0, 1, 13 + window.GONE_SECONDS))

    def test_window_never_opened(self):
        link = FakeLink([NEVER])
        code, now, _ = self.hold(link)
        self.assertEqual((code, link.stops, now), (0, 1, window.FIRST_VISIT_SECONDS))

    def test_waits_for_running_tasks(self):
        link = FakeLink([OPEN, CLOSED], tasks={"RUNNING": 1})
        code, now, _ = self.hold(link)
        self.assertEqual((code, link.stops), (0, 1))
        self.assertEqual(now, 1 + window.GONE_SECONDS + window.TASK_WAIT_SECONDS)  # но не дольше предела

    def test_status_error_counts_as_idle(self):
        link = FakeLink([OPEN, CLOSED])
        link.status = AppError("CORE_UNAVAILABLE", status=503)
        code, now, _ = self.hold(link)
        self.assertEqual((code, link.stops, now), (0, 1, 1 + window.GONE_SECONDS))

    def test_presence_errors_are_ignored(self):
        error = AppError("CORE_UNAVAILABLE", status=503)
        link = FakeLink([error] * 5 + [OPEN, CLOSED])
        code, now, _ = self.hold(link)
        self.assertEqual((code, link.stops, now), (0, 1, 6 + window.GONE_SECONDS))

    def test_restarts_crashed_core_and_reopens_window(self):
        link = FakeLink([CLOSED], exits=[None, 1], ports=[8800])
        with self.assertLogs("r52.window", "WARNING"):
            code, now, reopened = self.hold(link)
        self.assertEqual((code, link.connects, link.stops), (0, 1, 1))
        self.assertEqual(reopened, [8800])  # у нового ядра другой порт: окно открыто заново
        self.assertEqual(now, 1 + window.GONE_SECONDS)

    def test_gives_up_after_repeated_crashes(self):
        link = FakeLink([OPEN], exits=[1] * (window.MAX_RESTARTS + 1))
        with self.assertLogs("r52.window", "WARNING"):
            code, _, reopened = self.hold(link)
        self.assertEqual((code, link.connects, reopened), (1, window.MAX_RESTARTS, []))

    def test_core_stopped_or_taken_over(self):
        stopped = FakeLink([OPEN], exits=[0])  # например, кнопкой «Завершить работу ядра»
        self.assertEqual(self.hold(stopped)[0], 0)
        self.assertEqual((stopped.connects, stopped.stops), (0, 0))
        taken = FakeLink([OPEN], exits=[1], mode="attached")  # после сбоя ядро уже запустил кто-то другой
        with self.assertLogs("r52.window", "WARNING"):
            self.assertEqual(self.hold(taken)[0], 0)
        self.assertEqual((taken.connects, taken.stops), (1, 0))


# --- вход в интерфейс и файлы интерфейса против настоящего ядра --------------------

class InterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = helpers.temp_home()
        cls.link = CoreLink(cls.home)
        cls.link.connect()
        cls.port = cls.link.client.runtime()["port"]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.link.stop()
        finally:
            shutil.rmtree(cls.home, ignore_errors=True)

    def test_login_code_works_once(self):
        path, cookie = log_in(self, self.link)
        status, headers, body = fetch(self.port, path)  # тот же код второй раз
        self.assertEqual(status, 403)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("R-52".encode(), body)
        status, headers, _ = fetch(self.port, path, headers={"Cookie": cookie})  # у окна уже есть сессия
        self.assertEqual((status, headers["Location"]), (303, "/ui/"))
        self.assertEqual(fetch(self.port, "/ui/login?code=")[0], 403)
        self.assertEqual(fetch(self.port, "/ui/login?code=" + "x" * 32)[0], 403)

    def test_interface_files(self):
        for path in ("/", "/ui"):
            status, headers, _ = fetch(self.port, path)
            self.assertEqual((status, headers["Location"]), (303, "/ui/"))
        status, headers, body = fetch(self.port, "/ui/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn(b'src="/ui/js/main.js"', body)
        for name in ("js/main.js", *(f"js/views/{view}.js" for view in VIEWS)):
            status, headers, _ = fetch(self.port, "/ui/" + name)
            self.assertEqual((status, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"), name)
        self.assertEqual(fetch(self.port, "/ui/css/views.css")[1]["Content-Type"], "text/css; charset=utf-8")
        for bad in ("/ui/../r52/config.py", "/ui/js/../../../r52/cli.py", "/ui/.hidden.js", "/ui/js/%2e%2e/main.js",
                    "/ui/index.php", "/ui/nope.js"):
            self.assertEqual(fetch(self.port, bad)[0], 404, bad)
        self.assertEqual(fetch(self.port, "/ui/", "POST", body={})[0], 405)
        self.assertEqual(fetch(self.port, "/ui/", headers={"Host": "evil.example"})[0], 403)

    def test_api_with_window_session(self):
        _, cookie = log_in(self, self.link)
        ui = {"Cookie": cookie, "X-R52-UI": "1"}
        status, _, body = fetch(self.port, "/api/status", headers=ui)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["service"], "r52-core")
        cases = [
            ({**ui, "Origin": f"http://127.0.0.1:{self.port}"}, 200),
            ({"X-R52-UI": "1"}, 401),  # без сессии
            ({"Cookie": "r52_session=made-up-session", "X-R52-UI": "1"}, 401),
            ({"Cookie": cookie}, 403),  # без заголовка окна
            ({**ui, "Origin": "http://evil.example"}, 403),  # запрос с чужого сайта
            ({**ui, "Sec-Fetch-Site": "cross-site"}, 403),
            ({"Authorization": "Bearer wrong-token"}, 401),
        ]
        for headers, expected in cases:
            self.assertEqual(fetch(self.port, "/api/status", headers=headers)[0], expected, headers)
        self.assertEqual(fetch(self.port, "/api/ui/session", "POST", ui, {})[0], 403)  # код выдаётся только по ключу
        self.assertEqual(fetch(self.port, "/api/ui/presence", headers=ui)[0], 403)

    def test_presence_follows_pages(self):
        _, cookie = log_in(self, self.link)
        ui, page = {"Cookie": cookie, "X-R52-UI": "1"}, {"page": "ui-test-presence"}
        status, _, body = fetch(self.port, "/api/ui/heartbeat", "POST", ui, page)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        before = self.link.client.request("GET", "/api/ui/presence")
        self.assertTrue(before["seen"])
        self.assertGreaterEqual(before["pages"], 1)
        self.assertEqual(fetch(self.port, "/api/ui/bye", "POST", ui, page)[0], 200)
        after = self.link.client.request("GET", "/api/ui/presence")
        self.assertEqual((after["pages"], after["seen"]), (before["pages"] - 1, True))
        self.assertEqual(fetch(self.port, "/api/ui/heartbeat", "POST", ui, {})[0], 400)

    def test_launcher_attaches_and_prints_link(self):
        keep_logging(self)
        alerts, out = [], io.StringIO()
        with mock.patch.object(window, "_alert", alerts.append), contextlib.redirect_stdout(out):
            code = window.main(["--home", str(self.home), "--print-url"])
        self.assertEqual((code, alerts), (0, []))  # ядро запущено не этим лаунчером: он сразу выходит
        found = LOGIN.match(out.getvalue().strip())
        self.assertIsNotNone(found, out.getvalue())
        status, headers, _ = fetch(self.port, found.group(2))
        self.assertEqual(status, 303)
        self.assertIn("r52_session=", headers["Set-Cookie"])
        self.assertIsNone(self.link.exit_code())  # ядро продолжает работать


class LauncherTest(unittest.TestCase):
    def test_own_core_stops_after_window_closes(self):
        home = helpers.temp_home(self)
        link = CoreLink(home)
        self.assertEqual(link.connect(), "started")
        process = link.process
        self.addCleanup(link.stop)
        _, cookie = log_in(self, link)
        port = link.client.runtime()["port"]
        ui, page = {"Cookie": cookie, "X-R52-UI": "1"}, {"page": "ui-test-window"}
        self.assertEqual(fetch(port, "/api/ui/heartbeat", "POST", ui, page)[0], 200)
        self.assertEqual(fetch(port, "/api/ui/bye", "POST", ui, page)[0], 200)  # окно закрыли
        begin = time.monotonic()
        with mock.patch.multiple(window, POLL_SECONDS=0.05, GONE_SECONDS=0.5, TASK_WAIT_SECONDS=5):
            self.assertEqual(window.hold(link, lambda: self.fail("окно не нужно открывать заново")), 0)
        self.assertLess(time.monotonic() - begin, 30)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(link.paths.runtime_file.exists())
        self.assertFalse(is_core_running(link.paths.lock_file))

    def test_broken_settings_are_reported(self):
        home = helpers.temp_home(self)
        (home / "config.toml").write_text('[api]\nport = "много"\n', encoding="utf-8")
        alerts = []
        with mock.patch.object(window, "_alert", alerts.append):
            self.assertEqual(window.main(["--home", str(home)]), 1)
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].startswith("Не удалось прочитать настройки R-52."), alerts[0])

    def test_alert_without_windows_goes_to_stderr(self):
        err = io.StringIO()
        with mock.patch.object(window.sys, "platform", "linux"), contextlib.redirect_stderr(err), \
                self.assertLogs("r52.window", "WARNING"):
            window._alert("Окно R-52\nне открылось")
        self.assertEqual(err.getvalue(), "Окно R-52\nне открылось\n")


# --- файлы интерфейса --------------------------------------------------------------

class WebFilesTest(unittest.TestCase):
    """Модули интерфейса загружает браузер, и битый импорт видно только по пустому экрану. Проверяем заранее."""

    @staticmethod
    def sources():
        return {path.resolve(): path.read_text(encoding="utf-8") for path in (WEB / "js").rglob("*.js")}

    def test_imports_resolve(self):
        sources = self.sources()
        self.assertGreater(len(sources), 15)
        exported = {}
        for path, text in sources.items():
            names = set(DECLARED.findall(text))
            for listing in LISTED.findall(text):
                names.update(part.split(" as ")[-1].strip() for part in listing.split(",") if part.strip())
            exported[path] = names
        problems = []
        for path, text in sources.items():
            where = path.relative_to(WEB.resolve()).as_posix()
            for listing, target in IMPORT.findall(text):
                module = (path.parent / target).resolve()
                if module not in exported:
                    problems.append(f"{where}: нет модуля {target}")
                    continue
                for part in listing.split(","):
                    name = part.split(" as ")[0].strip()
                    if name and name not in exported[module]:
                        problems.append(f"{where}: {target} не экспортирует {name}")
            for target in DYNAMIC.findall(text):
                if (path.parent / target).resolve() not in exported:
                    problems.append(f"{where}: нет модуля {target}")
        self.assertEqual(problems, [])
        routes = DYNAMIC.findall(sources[(WEB / "js" / "main.js").resolve()])
        self.assertEqual(sorted(Path(target).stem for target in routes), sorted(VIEWS))
        for view in VIEWS:
            self.assertIn("render", exported[(WEB / "js" / "views" / f"{view}.js").resolve()], view)

    def test_page_follows_security_policy(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        targets = re.findall(r'(?:src|href)="/ui/([^"]+)"', html)
        self.assertIn("js/main.js", targets)
        for target in targets:
            self.assertTrue((WEB / target).is_file(), target)
        self.assertNotRegex(html, r"<script(?![^>]*\ssrc=)")  # встроенный скрипт политика CSP не пустит
        self.assertNotRegex(html, r"\sstyle=")  # как и встроенные стили
        self.assertNotRegex(html, r"\son[a-z]+=")
        for path, text in self.sources().items():
            self.assertNotRegex(text, r"""setAttribute\(\s*["']style|\beval\(|new Function\(|\sstyle=["']""", path.name)
