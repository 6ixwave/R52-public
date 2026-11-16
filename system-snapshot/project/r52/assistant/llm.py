"""Необязательная локальная модель через Ollama на 127.0.0.1.

Ответ модели принимается, только если каждое число в нём есть в фактах или в вопросе.
Иначе используется ответ по правилам: модель не может придумать цену.
"""

import http.client
import json
import re
from decimal import Decimal, InvalidOperation

from ..errors import AppError

SYSTEM_PROMPT = (
    "Ты помощник R-52 по покупкам. Отвечай по-русски, коротко и по делу. "
    "Используй только факты из блока ФАКТЫ. Не придумывай цены, даты, проценты и другие числа. "
    "Если фактов не хватает, так и скажи. Не обещай наличие и доставку: они не проверены."
)
NUMBER = re.compile(r"\d[\d\u00a0\u202f ]*(?:[.,]\d+)?")


class OllamaClient:
    def __init__(self, port, model, timeout=45):
        self.port = int(port)
        self.model = model
        self.timeout = timeout

    def chat(self, system, user):
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.2},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, ensure_ascii=False).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            conn.request("POST", "/api/chat", body=body, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            status = response.status
            data = response.read(4_000_000)
        except (OSError, http.client.HTTPException) as error:
            raise AppError("LLM_UNAVAILABLE", status=503, detail=type(error).__name__) from None
        finally:
            conn.close()
        if status != 200:
            raise AppError("LLM_UNAVAILABLE", status=503, detail=f"HTTP {status}")
        try:
            content = json.loads(data)["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise AppError("LLM_UNAVAILABLE", status=503, detail="bad_response") from None
        if not isinstance(content, str) or not content.strip():
            raise AppError("LLM_UNAVAILABLE", status=503, detail="empty")
        return content.strip()


def numbers_in(text):
    found = set()
    for match in NUMBER.finditer(text or ""):
        raw = re.sub(r"[\u00a0\u202f ]", "", match.group(0)).replace(",", ".").rstrip(".")
        try:
            found.add(format(Decimal(raw).normalize(), "f"))
        except InvalidOperation:
            continue
    return found


def numbers_supported(answer, facts):
    allowed = numbers_in(facts)
    bad = sorted(number for number in numbers_in(answer) if number not in allowed)
    return not bad, bad
