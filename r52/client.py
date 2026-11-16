"""Клиент локального API ядра для командной строки.

Адрес и токен берутся из runtime.json, который ядро пишет при запуске и удаляет при остановке.
"""

import http.client
import json

from . import SERVICE_ID
from .errors import AppError


class CoreClient:
    def __init__(self, paths):
        self.paths = paths

    def runtime(self):
        try:
            data = json.loads(self.paths.runtime_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AppError("CORE_NOT_RUNNING", status=503) from None
        except (OSError, ValueError):
            raise AppError("CORE_NOT_RUNNING", status=503, detail="runtime.json повреждён") from None
        if (not isinstance(data, dict) or data.get("service") != SERVICE_ID
                or not isinstance(data.get("port"), int) or not isinstance(data.get("token"), str)):
            raise AppError("CORE_NOT_RUNNING", status=503, detail="runtime.json повреждён")
        return data

    def request(self, method, path, body=None, timeout=60):
        runtime = self.runtime()
        headers = {"Authorization": "Bearer " + runtime["token"]}
        data = None
        if body is not None or method in ("POST", "PUT", "PATCH"):
            data = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", runtime["port"], timeout=timeout)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except (OSError, http.client.HTTPException) as error:
            raise AppError("CORE_NOT_RUNNING", status=503, detail=type(error).__name__) from None
        finally:
            connection.close()
        try:
            result = json.loads(payload.decode("utf-8")) if payload else {}
        except (UnicodeDecodeError, ValueError):
            raise AppError("WRONG_SERVICE", status=502) from None
        if response.status >= 400:
            error = result.get("error") if isinstance(result, dict) else None
            error = error if isinstance(error, dict) else {}
            raise AppError(error.get("code") or "INTERNAL_ERROR", error.get("message"), response.status,
                           error.get("detail"))
        return result
