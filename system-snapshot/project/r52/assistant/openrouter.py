"""OpenRouter transport. Fixed HTTPS destination, no retries, no secret persistence."""
import http.client
import json
import math
import os
import re
import socket
import ssl
from ..errors import AppError
from ..netguard import openrouter_access, NetworkBlocked

MODEL = "openai/gpt-5-mini"
MAX_RESPONSE = 1_000_000
ERRORS = {
    400: "OpenRouter отклонил параметры запроса.",
    401: "Ключ OpenRouter недействителен.",
    402: "Баланс OpenRouter исчерпан.",
    403: "Доступ или лимит ключа OpenRouter ограничен.",
    404: "Модель OpenRouter недоступна.",
    429: "Лимит частоты OpenRouter. Повторите позже новым сообщением.",
}


def key_present():
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def safe_error(raw, key):
    """Only error.message, bounded and redacted. No raw body, request, headers or metadata."""
    if len(raw) > MAX_RESPONSE:
        return "Тело ошибки слишком большое; подробности скрыты."
    try:
        data = json.loads(raw)
        error = data.get("error", {}) if isinstance(data, dict) else {}
        text = error.get("message", "") if isinstance(error, dict) else ""
    except (ValueError, TypeError):
        return "Сервер вернул ошибку не в формате JSON."
    if not isinstance(text, str):
        return ""
    # Replace the exact secret before truncation, then common credential formats.
    if key:
        text = text.replace(key, "[КЛЮЧ СКРЫТ]")
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[КЛЮЧ СКРЫТ]", text)
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [СКРЫТО]", text)
    text = re.sub(r"(?i)(api[_ -]?key|authorization|token)\s*[:=]\s*[^\s,;]+", r"\1=[СКРЫТО]", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return " ".join(text.split())[:500]


class OpenRouterClient:
    def __init__(self, max_tokens=1400, timeout=35, model=MODEL):
        self.max_tokens = int(max_tokens)
        self.timeout = timeout
        if model not in (MODEL, "anthropic/claude-sonnet-4.5"):
            raise AppError("INVALID_INPUT", message="Модель не входит в разрешённый список.")
        self.model = model

    def chat(self, messages, tool):
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise AppError("OPENROUTER_KEY_MISSING", message="Задайте OPENROUTER_API_KEY и перезапустите R-52.")
        if len(key) > 1024 or "\r" in key or "\n" in key:
            raise AppError("OPENROUTER_KEY_INVALID", message="Неверный формат ключа OpenRouter.")
        payload = {
            "model": self.model, "messages": messages, "stream": False,
            "max_tokens": self.max_tokens,
            "reasoning": {"effort": "low"},
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": "assistant_request"}},
            "provider": {"require_parameters": True, "allow_fallbacks": False},
        }
        if self.model != MODEL:
            payload.pop("reasoning", None)
        # Do not send unsupported temperature. Limit applies to reasoning + visible output.
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > 60_000:
            raise AppError("OPENROUTER_INPUT_LIMIT", message="Слишком большой контекст для платного запроса.")
        conn = http.client.HTTPSConnection("openrouter.ai", 443, timeout=self.timeout,
                                            context=ssl.create_default_context())
        try:
            with openrouter_access():
                conn.request("POST", "/api/v1/chat/completions", body=body, headers={
                    "Authorization": "Bearer " + key, "Content-Type": "application/json",
                    "Accept": "application/json",
                })
                response = conn.getresponse()
                status = response.status
                raw = response.read(MAX_RESPONSE + 1)
        except NetworkBlocked:
            raise AppError("OPENROUTER_NETWORK_BLOCKED", message="Сетевой ограничитель R-52 отклонил соединение с OpenRouter. Автоматического повтора нет.") from None
        except ssl.SSLError:
            raise AppError("OPENROUTER_TLS", message="Не удалось проверить защищённое TLS-соединение с OpenRouter. Проверка сертификатов не отключалась.") from None
        except socket.gaierror:
            raise AppError("OPENROUTER_DNS", message="Не удалось определить адрес openrouter.ai через DNS.") from None
        except TimeoutError:
            raise AppError("OPENROUTER_TIMEOUT", message="Истёк тайм-аут соединения с OpenRouter. Автоматического повтора нет.") from None
        except (OSError, http.client.HTTPException, ValueError):
            raise AppError("OPENROUTER_UNAVAILABLE", message="Сетевая ошибка соединения с OpenRouter. Автоматического повтора нет.") from None
        finally:
            conn.close()
        if status != 200:
            explanation = safe_error(raw, key)
            message = f"OpenRouter HTTP {status}: " + ERRORS.get(status, "Запрос отклонён.")
            if explanation:
                message += " Причина сервера: " + explanation
            raise AppError("OPENROUTER_HTTP_" + str(status), message=message)
        if len(raw) > MAX_RESPONSE:
            raise AppError("OPENROUTER_BAD_RESPONSE", message="Ответ OpenRouter слишком большой.")
        try:
            data = json.loads(raw)
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            # Keep only billing fields; never retain raw upstream objects.
            safe_usage = {}
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(field)
                if type(value) is int and 0 <= value <= 10**9:
                    safe_usage[field] = value
            value = usage.get("cost")
            cost = float(value) if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None
            choice = data["choices"][0]
            calls = choice["message"].get("tool_calls") or []
            # Malformed outputs still carry usage/cost and are charged in the ledger.
            valid = choice.get("finish_reason") == "tool_calls" and len(calls) == 1
            plan = None
            if valid and calls[0].get("type") == "function":
                fn = calls[0].get("function") or {}
                if fn.get("name") == "assistant_request":
                    arguments = fn.get("arguments")
                    if isinstance(arguments, str) and len(arguments) <= 24_000:
                        try:
                            plan = json.loads(arguments)
                        except (ValueError, TypeError):
                            pass
            return {"plan": plan, "usage": safe_usage, "cost_usd": cost, "model": self.model}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AppError("OPENROUTER_BAD_RESPONSE", message="Некорректный ответ OpenRouter. Повтор отключён.") from None
