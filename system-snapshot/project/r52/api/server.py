"""Локальный HTTP сервер ядра.

Слушает только 127.0.0.1 и проверяет заголовок Host, это защищает от подмены адреса через DNS.
Программы (CLI, лаунчер) приходят с токеном, который ядро создаёт при каждом запуске. Такие
запросы не должны приходить со страниц браузера, поэтому заголовок Origin для них запрещён.
Окно интерфейса входит по одноразовому коду и работает с cookie сессии. Его запросы принимаются
только со своей страницы и только с заголовком X-R52-UI, который чужая страница поставить не может.
Файлы интерфейса (/ui/) секретов не содержат и отдаются со строгой политикой безопасности (CSP).
"""

import hmac
import json
import logging
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePath
from urllib.parse import parse_qs, urlsplit

from ..errors import AppError
from .uiauth import COOKIE, SESSION_TTL

log = logging.getLogger("r52.api")
MAX_BODY = 24_000_000
MAX_STATIC = 4_000_000
SOCKET_TIMEOUT = 30
UI_HEADER = "X-R52-UI"
JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
CSP = ("default-src 'self'; img-src 'self' data: https:; media-src 'self' https:;  style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
MIME = {".html": HTML_TYPE, ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
        ".woff2": "font/woff2"}
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./")
EXPIRED_PAGE = (
    '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>R-52</title>'
    '<meta name="color-scheme" content="dark"><meta name="theme-color" content="#07080f"><style>'
    'html{height:100%;background:#07080f;color:#eceeff;font:16px/1.55 "Segoe UI Variable Text","Segoe UI",'
    'system-ui,sans-serif}body{margin:0;min-height:100%;display:grid;place-items:center;background:'
    'radial-gradient(55% 45% at 25% 20%,rgba(124,58,237,.28),transparent 70%),'
    'radial-gradient(45% 45% at 80% 85%,rgba(34,211,238,.18),transparent 70%)}'
    'main{max-width:460px;margin:24px;padding:36px 40px;border-radius:24px;background:rgba(255,255,255,.05);'
    'border:1px solid rgba(255,255,255,.1);box-shadow:0 30px 80px -30px rgba(0,0,0,.8);text-align:center}'
    'h1{margin:0 0 10px;font-size:22px;font-weight:600}p{margin:0;color:rgba(236,238,255,.68)}</style></head>'
    '<body><main><h1>Ссылка для входа устарела</h1><p>Закройте это окно и снова запустите R-52 '
    'ярлыком или файлом R52.cmd. Ссылка срабатывает один раз в течение минуты.</p></main>'
    '</body></html>').encode("utf-8")


class Request:
    def __init__(self, query, body, params, via="token"):
        self.query = query
        self.body = body
        self.params = params
        self.via = via


def _encode(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value) if isinstance(value, (set, frozenset)) else list(value)
    raise TypeError(f"нельзя преобразовать в JSON: {type(value).__name__}")


def _reject_constant(name):
    raise ValueError(name)


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, router, token, ui=None, web_root=None):
        self.router = router
        self.token = token
        self.ui = ui
        self.web_root = web_root
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def port(self):
        return self.server_address[1]

    def origins(self):
        return (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")


class _Handler(BaseHTTPRequestHandler):
    server_version = "R52"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT

    def log_message(self, format, *args):  # noqa: A002 (имя задано http.server)
        log.debug("%s %s", self.address_string(), format % args)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_HEAD(self):  # noqa: N802
        self._handle("HEAD")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def do_PUT(self):  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self):  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self):  # noqa: N802
        self._handle("DELETE")

    def _handle(self, method):
        request_id = uuid.uuid4().hex[:12]
        path = "?"
        self._body_read = False
        content_type, headers = JSON_TYPE, []
        try:
            split = urlsplit(self.path)
            path = split.path
            self._check_host()
            if path == "/" or path == "/ui" or path.startswith("/ui/"):
                status, data, content_type, headers = self._ui(method, path, split.query)
            else:
                via = self._check_auth(path)
                body = self._read_body(method)
                query = {key: values[-1] for key, values in parse_qs(split.query, keep_blank_values=True).items()}
                status, payload = self.server.router.dispatch(method, path, query, body, via=via)
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_encode).encode("utf-8")
        except AppError as error:
            status, content_type, headers = error.status, JSON_TYPE, []
            data = json.dumps({"error": error.as_dict(), "request_id": request_id}, ensure_ascii=False,
                              default=_encode).encode("utf-8")
            if status >= 500:
                log.error("Ошибка %s в %s %s (запрос %s): %s", error.code, method, path, request_id, error.detail)
        except Exception:  # граница запроса: ошибка одного запроса не роняет ядро
            log.exception("Необработанная ошибка в %s %s (запрос %s)", method, path, request_id)
            status, content_type, headers = 500, JSON_TYPE, []
            data = json.dumps({"error": AppError("INTERNAL_ERROR", status=500).as_dict(), "request_id": request_id},
                              ensure_ascii=False).encode("utf-8")
        self._send(status, data, content_type, headers, head=method == "HEAD")

    def _ui(self, method, path, query):
        if method not in ("GET", "HEAD"):
            raise AppError("METHOD_NOT_ALLOWED", status=405)
        if path in ("/", "/ui"):
            return self._redirect("/ui/")
        if path == "/ui/login":
            if method != "GET":
                raise AppError("METHOD_NOT_ALLOWED", status=405)
            return self._login(query)
        file, suffix = self._static(path[len("/ui/"):] or "index.html")
        try:
            data = file.read_bytes()
        except OSError:
            raise AppError("NOT_FOUND", status=404) from None
        headers = [("Content-Security-Policy", CSP), ("X-Frame-Options", "DENY")] if suffix == ".html" else []
        return 200, data, MIME[suffix], headers

    def _static(self, relative):
        root = self.server.web_root
        parts = relative.split("/")
        if (root is None or len(relative) > 200 or not set(relative) <= _SAFE
                or any(part in ("", ".", "..") or part.startswith(".") for part in parts)):
            raise AppError("NOT_FOUND", status=404)
        file = root.joinpath(*parts)
        suffix = file.suffix.lower()
        if suffix not in MIME:
            raise AppError("NOT_FOUND", status=404)
        try:
            resolved = file.resolve(strict=True)
            inside = resolved.is_relative_to(root.resolve(strict=True))
            size = resolved.stat().st_size if inside and resolved.is_file() else None
        except (OSError, RuntimeError):
            raise AppError("NOT_FOUND", status=404) from None
        if size is None or size > MAX_STATIC:
            raise AppError("NOT_FOUND", status=404)
        return resolved, suffix

    def _login(self, query):
        values = parse_qs(query).get("code") or [""]
        ui = self.server.ui
        session = ui.redeem(values[-1]) if ui is not None else None
        if session is not None:
            cookie = f"{COOKIE}={session}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"
            return self._redirect("/ui/", [("Set-Cookie", cookie)])
        if self._session_ok():
            return self._redirect("/ui/")
        return 403, EXPIRED_PAGE, HTML_TYPE, [("Content-Security-Policy", PAGE_CSP)]

    @staticmethod
    def _redirect(location, headers=()):
        return 303, b"", "text/plain; charset=utf-8", [("Location", location), *headers]

    def _session_value(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == COOKIE:
                return value.strip().strip('"')
        return None

    def _session_ok(self):
        ui = self.server.ui
        return ui is not None and ui.check(self._session_value())

    def _check_host(self):
        port = self.server.port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise AppError("HOST_REFUSED", status=403)

    def _check_auth(self, path):
        """Возвращает способ доступа: token (программа), ui (окно R-52) или public (/healthz)."""
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins():
            raise AppError("ORIGIN_REFUSED", status=403)
        if path == "/healthz":
            return "public"
        header = self.headers.get("Authorization")
        if header is not None:
            if origin is not None:
                raise AppError("ORIGIN_REFUSED", status=403)
            given = header[7:] if header.startswith("Bearer ") else ""
            if not given or not hmac.compare_digest(given.encode("utf-8"), self.server.token.encode("utf-8")):
                raise AppError("AUTH_REQUIRED", status=401)
            return "token"
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            raise AppError("ORIGIN_REFUSED", status=403)
        if not self._session_ok():
            raise AppError("AUTH_REQUIRED", status=401)
        if self.headers.get(UI_HEADER) != "1":
            raise AppError("UI_HEADER_REQUIRED", status=403)
        return "ui"

    def _read_body(self, method):
        self._body_read = True
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise AppError("LENGTH_REQUIRED", status=411)
        header = self.headers.get("Content-Length")
        if header is None:
            if method in ("POST", "PUT", "PATCH"):
                self.close_connection = True
                raise AppError("LENGTH_REQUIRED", status=411)
            return {}
        if not header.isdigit():
            self.close_connection = True
            raise AppError("LENGTH_REQUIRED", status=411)
        length = int(header)
        if length > MAX_BODY:
            self.close_connection = True
            raise AppError("BODY_TOO_LARGE", status=413)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.close_connection = True
            raise AppError("INVALID_INPUT", detail={"field": "body", "reason": "incomplete"})
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise AppError("JSON_REQUIRED", status=415)
        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError):
            raise AppError("JSON_REQUIRED") from None
        if not isinstance(data, dict):
            raise AppError("INVALID_INPUT", detail={"field": "body", "reason": "object_required"})
        return data

    def _has_body(self):
        length = (self.headers.get("Content-Length") or "").strip()
        return bool(self.headers.get("Transfer-Encoding")) or length not in ("", "0")

    def _send(self, status, data, content_type=JSON_TYPE, headers=(), head=False):
        if not self._body_read and self._has_body():
            self.close_connection = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            for name, value in headers:
                self.send_header(name, value)
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if not head:
                self.wfile.write(data)
        except OSError as error:
            log.debug("Клиент закрыл соединение до ответа: %s", error)
            self.close_connection = True
