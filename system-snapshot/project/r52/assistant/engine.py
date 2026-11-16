"""Помощник. Отвечает по данным локальной базы и ничего не меняет без подтверждения.

Предложение действия (следить, добавить в избранное, сохранить сравнение) сохраняется
с токеном и выполняется только после нажатия кнопки. Через сутки предложение истекает.
"""

import json
import threading
import hashlib
import re
import secrets
import sqlite3
import uuid
from datetime import timedelta

from ..core import watches
from ..core.notify import counts as notification_counts
from ..core.notify import list_events
from ..errors import AppError
from ..money import format_minor
from ..store import lists
from ..store.products import list_products, product_detail
from ..timeutil import iso, local_text, parse_aware
from . import intents
from .llm import SYSTEM_PROMPT, OllamaClient, numbers_supported

SESSION = re.compile(r"^[a-f0-9]{32}$")
KEY = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{10,60}$")
ACTION_TTL = timedelta(hours=24)
MAX_TEXT = 500
HELP_TEXT = (
    "Я работаю с вашей локальной базой. Примеры:\n"
    "• найди rtx 4060\n• история цен rtx 4060\n• сравни rtx 4060 и rx 7600\n"
    "• следи за rtx 4060 до 30000\n• добавь rtx 4060 в избранное\n• что нового\n• статус\n\n"
    "Сбор цен с маркетплейсов пока не подключён: данные приходят из Eye, ручного ввода и старых баз."
)
NOT_FOUND = (
    "По запросу «{q}» в локальной базе ничего нет. Сбор с маркетплейсов пока не подключён: "
    "добавьте карточку через Eye или вручную."
)
EVENT_TEXT = {
    "TARGET_REACHED": "цена достигла цели",
    "PRICE_DROPPED": "цена снизилась",
    "BECAME_AVAILABLE": "товар появился в наличии",
    "BECAME_UNAVAILABLE": "товар пропал из наличия",
    "BACKUP_FAILED": "резервная копия не создана",
    "DB_CHECK_FAILED": "проверка базы нашла ошибки",
}
AVAILABILITY = {"IN_STOCK": "в наличии", "OUT_OF_STOCK": "нет в наличии", "UNKNOWN": "неизвестно"}


class Assistant:
    def __init__(self, app):
        self.app = app
        self._send_lock = threading.RLock()

    def create_session(self, title=None):
        session_id = uuid.uuid4().hex
        now = iso(self.app.clock.now())
        with self.app.db.write() as conn:
            conn.execute(
                "INSERT INTO chat_session(id, title, created_at, updated_at) VALUES(?, ?, ?, ?)",
                (session_id, title, now, now),
            )
        return {"id": session_id, "title": title, "created_at": now, "updated_at": now}

    def list_sessions(self, limit=20):
        with self.app.db.read() as conn:
            rows = conn.execute("SELECT * FROM chat_session ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _session(conn, session_id):
        if not isinstance(session_id, str) or not SESSION.match(session_id):
            raise AppError("SESSION_NOT_FOUND", status=404)
        row = conn.execute("SELECT * FROM chat_session WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise AppError("SESSION_NOT_FOUND", status=404)
        return row

    @staticmethod
    def _message(conn, row):
        data = dict(row)
        stored = data.pop("data_json", None)
        data.pop("idempotency_key", None)
        data["data"] = json.loads(stored) if stored else None
        for action in (data["data"] or {}).get("actions") or []:
            state = conn.execute("SELECT status FROM assistant_action WHERE token = ?", (action["token"],)).fetchone()
            action["status"] = state["status"] if state else "EXPIRED"
        return data

    def get_session(self, session_id, limit=100):
        with self.app.db.read() as conn:
            session = dict(self._session(conn, session_id))
            rows = conn.execute(
                "SELECT * FROM (SELECT * FROM chat_message WHERE session_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (session_id, limit)).fetchall()
            session["messages"] = [self._message(conn, row) for row in rows]
        return session

    def _pair(self, conn, user_id, replayed):
        user = conn.execute("SELECT * FROM chat_message WHERE id = ?", (user_id,)).fetchone()
        reply = conn.execute(
            "SELECT * FROM chat_message WHERE session_id = ? AND id > ? AND role = 'assistant' ORDER BY id LIMIT 1",
            (user["session_id"], user_id)).fetchone()
        return {"user": self._message(conn, user), "reply": self._message(conn, reply) if reply else None,
                "replayed": replayed}

    def _replay(self, conn, session_id, text, key, context_ids=None):
        if key is None:
            return None
        row = conn.execute("SELECT * FROM chat_message WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            return None
        stored_context = json.loads(row["data_json"] or "{}").get("context_product_ids", [])
        if row["session_id"] != session_id or row["text"] != text or stored_context != (context_ids or []):
            raise AppError("IDEMPOTENCY_CONFLICT", status=409)
        return self._pair(conn, row["id"], replayed=True)

    def send(self, session_id, text, idempotency_key=None, context_product_ids=None):
        # Serialize locally; persisted claim prevents paid replay after a crash/restart.
        with self._send_lock:
            claim = None
            if self.app.settings.get("assistant.llm") == "openrouter":
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
                    raise AppError("TEXT_REQUIRED")
                if not isinstance(idempotency_key, str) or not KEY.fullmatch(idempotency_key):
                    raise AppError("INVALID_INPUT", message="Для OpenRouter нужен ключ повтора сообщения.")
                with self.app.db.read() as conn:
                    self._session(conn, session_id)
                    # _send performs full context validation before a paid request.
                    if context_product_ids is None:
                        previous = conn.execute("SELECT data_json FROM chat_message WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
                        context_product_ids = json.loads(previous[0] or "{}").get("context_product_ids", []) if previous else []
                    if (not isinstance(context_product_ids, list) or len(context_product_ids) > 12 or
                        any(type(pid) is not int or not 1 <= pid <= 9007199254740991 for pid in context_product_ids)):
                        raise AppError("INVALID_INPUT")
                    context_product_ids = list(dict.fromkeys(context_product_ids))
                    replay = self._replay(conn, session_id, text.strip(), idempotency_key, context_product_ids)
                    if replay:
                        return replay
                fingerprint = hashlib.sha256(json.dumps([session_id,text.strip(),context_product_ids], ensure_ascii=False).encode()).hexdigest()
                with self.app.db.write() as conn:
                    previous = conn.execute("SELECT fingerprint FROM assistant_request WHERE key=?", (idempotency_key,)).fetchone()
                    if previous:
                        if previous[0] != fingerprint:
                            raise AppError("IDEMPOTENCY_CONFLICT", status=409)
                        raise AppError("ASSISTANT_REQUEST_UNCERTAIN", status=409,
                                       message="Запрос уже начат или прерван. Платный повтор заблокирован; проверьте историю.")
                    conn.execute("INSERT INTO assistant_request VALUES(?,?,'STARTED',?)", (idempotency_key, fingerprint, iso(self.app.clock.now())))
                claim = idempotency_key
            try:
                result = self._send(session_id, text, idempotency_key, context_product_ids)
            except BaseException:
                if claim:
                    with self.app.db.write() as conn:
                        conn.execute("UPDATE assistant_request SET status='FAILED' WHERE key=?", (claim,))
                raise
            if claim:
                with self.app.db.write() as conn:
                    conn.execute("UPDATE assistant_request SET status='DONE' WHERE key=?", (claim,))
            return result

    def _send(self, session_id, text, idempotency_key=None, context_product_ids=None):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
            raise AppError("TEXT_REQUIRED")
        text = text.strip()
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not KEY.match(idempotency_key)):
            raise AppError("INVALID_INPUT", detail={"field": "idempotency_key", "reason": "pattern"})
        with self.app.db.read() as conn:
            self._session(conn, session_id)
            if context_product_ids is None:
                previous = conn.execute("SELECT data_json FROM chat_message WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
                context_product_ids = json.loads(previous["data_json"] or "{}").get("context_product_ids", []) if previous else []
            if (not isinstance(context_product_ids, list) or len(context_product_ids) > 12 or
                    any(type(pid) is not int or not 1 <= pid <= 9007199254740991 for pid in context_product_ids)):
                raise AppError("INVALID_INPUT", detail={"field": "context_product_ids"})
            context_product_ids = list(dict.fromkeys(context_product_ids))
            replay = self._replay(conn, session_id, text, idempotency_key, context_product_ids)
        if replay:
            return replay
        from .cloud import CloudAssistant
        cloud = CloudAssistant(self)
        history = cloud.history(session_id) if self.app.settings.get("assistant.llm") == "openrouter" else []
        parsed = intents.parse(text)
        if parsed.name in ("greeting", "identity"):
            reply = getattr(self, "_on_" + parsed.name)(parsed)
            reply.update({"intent": parsed.name, "cards": [], "actions": []})
        elif self.app.settings.get("assistant.llm") == "openrouter" and (context_product_ids or parsed.name == "free"):
            reply = cloud.respond(text, context_product_ids or [], history)
        else:
            reply = self.respond(text, context_product_ids)
        reply.setdefault("llm", {"used": False, "provider": "rules", "calls": 0})
        now = iso(self.app.clock.now())
        data = {
            "intent": reply["intent"], "cards": reply["cards"], "table": reply.get("table"),
            "note": reply.get("note"), "llm": reply.get("llm"), "context_product_ids": context_product_ids,
            "actions": [{key: value for key, value in action.items() if key != "params"} for action in reply["actions"]],
        }
        try:
            with self.app.db.write() as conn:
                user_id = conn.execute(
                    "INSERT INTO chat_message(session_id, role, text, data_json, idempotency_key, created_at) "
                    "VALUES(?, 'user', ?, ?, ?, ?)", (session_id, text, json.dumps({"context_product_ids": context_product_ids}), idempotency_key, now)).lastrowid
                for action in reply["actions"]:
                    conn.execute(
                        "INSERT INTO assistant_action(token, session_id, kind, params_json, status, created_at) "
                        "VALUES(?, ?, ?, ?, 'PROPOSED', ?)",
                        (action["token"], session_id, action["kind"], json.dumps(action["params"], ensure_ascii=False), now),
                    )
                conn.execute(
                    "INSERT INTO chat_message(session_id, role, text, data_json, idempotency_key, created_at) "
                    "VALUES(?, 'assistant', ?, ?, NULL, ?)",
                    (session_id, reply["text"], json.dumps(data, ensure_ascii=False), now),
                )
                conn.execute(
                    "UPDATE chat_session SET updated_at = ?, title = COALESCE(title, ?) WHERE id = ?",
                    (now, text[:60], session_id),
                )
        except sqlite3.IntegrityError:
            # Тот же ключ пришёл одновременно во втором запросе: возвращаем уже сохранённый ответ.
            with self.app.db.read() as conn:
                replay = self._replay(conn, session_id, text, idempotency_key, context_product_ids)
            if replay:
                return replay
            raise
        with self.app.db.read() as conn:
            return self._pair(conn, user_id, replayed=False)

    def respond(self, text, context_ids=None):
        if context_ids:
            result = self._context_reply(text, context_ids)
            result.setdefault("cards", [])
            result.setdefault("actions", [])
            result["intent"] = "map_context"
            return result
        intent = intents.parse(text)
        result = getattr(self, "_on_" + intent.name)(intent)
        result.setdefault("cards", [])
        result.setdefault("actions", [])
        result["intent"] = intent.name
        return result

    def _context_reply(self, question, context_ids):
        # Explicit read-only scope. Never replace missing selected IDs with a broad search.
        with self.app.db.read() as conn:
            existing = [pid for pid in context_ids if conn.execute("SELECT id FROM product WHERE id = ?", (pid,)).fetchone()]
            if not existing:
                return {"text": "Выбранные на карте товары больше не найдены в базе. Вернитесь на карту и выберите их заново."}
            result = lists.compare_products(conn, existing)
        items = result["products"]
        facts = self._facts(items)
        reply = {"text": f"Контекст карты: выбранных записей — {len(items)}.\n" + facts +
                 "\nЭто сохранённые наблюдения, не подтверждение текущей цены. Срок доставки, гарантию и состояние по этим данным не определяю.",
                 "cards": items, "note": "Отвечаю только по выбранным на карте товарам; внешний поиск не выполняется."}
        if len(items) > 1:
            reply["table"] = result["table"]
            reply["text"] += "\n" + " ".join(result["table"]["notes"])
        if len(existing) != len(context_ids):
            reply["text"] += "\nЧасть выбранных записей больше недоступна."
        if self.app.settings.get("assistant.llm") == "ollama":
            client = OllamaClient(self.app.settings.get("assistant.ollama_port"), self.app.settings.get("assistant.ollama_model"))
            try:
                answer = client.chat(SYSTEM_PROMPT, "ТОЛЬКО ВЫБРАННЫЕ НА КАРТЕ ЗАПИСИ:\n" + facts + "\n\nВОПРОС: " + question)
            except AppError as error:
                reply["note"] += " Локальная модель недоступна, показана сводка по базе."
                reply["llm"] = {"used": False, "error": error.code}
                return reply
            supported, bad = numbers_supported(answer, facts + " " + question)
            if supported:
                reply["text"] = answer[:2000]
                reply["llm"] = {"used": True, "model": client.model}
            else:
                reply["llm"] = {"used": False, "rejected_numbers": bad[:10]}
                reply["note"] += " Ответ модели отклонён: неподтверждённые числа."
        return reply

    @staticmethod
    def _action(kind, params, label):
        return {"token": secrets.token_urlsafe(18), "kind": kind, "label": label, "params": params}

    def _find(self, query, limit=5):
        with self.app.db.read() as conn:
            return list_products(conn, query=query, sort="recent", limit=limit)

    def _on_greeting(self, intent):
        return {"text": "Привет! Я помощник R-52. Помогу найти сохранённые товары, показать историю и сравнить варианты. Что хотите сделать?"}

    def _on_identity(self, intent):
        mode = self.app.settings.get("assistant.llm")
        if mode == "openrouter":
            text = "Я помощник R-52. Выбран GPT‑5 Mini через OpenRouter, но этот справочный ответ составлен локально, без платного запроса. Он не подтверждает, что соединение с моделью работает. Для проверки отправьте отдельный свободный вопрос о товарах и посмотрите подпись и возможную ошибку."
        elif mode == "ollama":
            text = "Я помощник R-52. Выбран режим локальной модели Ollama. Этот справочный ответ составлен по правилам и не проверяет доступность модели."
        else:
            text = "Я помощник R-52. Сейчас включены только правила, нейросеть не используется. Облачный и локальный режимы выбираются в настройках."
        return {"text": text}

    def _on_help(self, intent):
        return {"text": HELP_TEXT}

    def _on_status(self, intent):
        status = self.app.status()
        database = status["database"]
        parts = [f"Ядро работает, версия {status['version']}.",
                 f"Товаров: {database['products']}, наблюдений: {database['observations']}."]
        if status["inbox_pending"]:
            parts.append(f"Во входящих ждут проверки: {status['inbox_pending']}.")
        last = status["backups"]["last"]
        parts.append(f"Последняя резервная копия: {local_text(last['created_at'], self.app.tz)}."
                     if last else "Резервных копий ещё нет.")
        problems = len(status["problems"])
        parts.append(f"Предупреждений в журнале: {problems}." if problems else "Предупреждений нет.")
        return {"text": " ".join(parts)}

    def _on_news(self, intent):
        since = iso(self.app.clock.now() - timedelta(hours=24))
        with self.app.db.read() as conn:
            events = [event for event in list_events(conn, limit=30, since=since) if event["kind"] != "DIGEST"]
            unread = notification_counts(conn)["unread"]
            observations = conn.execute("SELECT count(*) FROM observation WHERE imported_at >= ?", (since,)).fetchone()[0]
            titles = {row["id"]: row["title"] for row in conn.execute(
                "SELECT id, title FROM product WHERE id IN (SELECT product_id FROM event WHERE created_at >= ?)", (since,))}
        lines = [f"За сутки новых наблюдений: {observations}."]
        if events:
            lines.append("События:")
            for event in events[:5]:
                title = titles.get(event["product_id"])
                lines.append("• " + EVENT_TEXT.get(event["kind"], event["kind"]) + (f": {title}" if title else ""))
        else:
            lines.append("Срабатываний слежения не было.")
        pending = self.app.inbox.pending_count()
        if pending:
            lines.append(f"Во входящих ждут проверки: {pending}.")
        if unread:
            lines.append(f"Непрочитанных уведомлений: {unread}.")
        return {"text": "\n".join(lines)}

    def _on_inbox(self, intent):
        items = self.app.inbox.list(status="PENDING", limit=10)
        if not items:
            return {"text": "Во входящих пусто. Сохраните карточку через Eye, и она появится здесь."}
        lines = [f"Ждут проверки: {len(items)}."]
        for item in items[:5]:
            shown = item["preview"] or {}
            lines.append(f"• {shown.get('title') or item['filename']}: {shown.get('price_text') or 'цена неизвестна'}")
        lines.append("Откройте раздел «Входящие», чтобы принять или отклонить.")
        return {"text": "\n".join(lines)}

    def _on_favorites(self, intent):
        with self.app.db.read() as conn:
            items = lists.list_favorites(conn, limit=10)
        if not items:
            return {"text": "В избранном пока пусто."}
        return {"text": f"В избранном товаров: {len(items)}.", "cards": items[:8]}

    def _on_watches(self, intent):
        with self.app.db.read() as conn:
            items = watches.list_watches(conn)
        if not items:
            return {"text": "Слежений пока нет. Пример: «следи за rtx 4060 до 30000»."}
        lines = [f"Слежений: {len(items)}."]
        for item in items[:8]:
            watch, product = item["watch"], item["product"]
            currency = product["currency"] or "RUB"
            target = format_minor(watch["target_minor"], currency) if watch["target_minor"] is not None else "без цели"
            mark = " (цель достигнута)" if item["target_reached"] else ""
            lines.append(f"• {product['title']}: сейчас {format_minor(item['current_minor'], currency)}, цель {target}{mark}")
        return {"text": "\n".join(lines), "cards": [item["product"] for item in items[:8]]}

    def _on_search(self, intent):
        found = self._find(intent.query)
        if not found["total"]:
            return {"text": NOT_FOUND.format(q=intent.query)}
        text = f"По запросу «{intent.query}» найдено товаров: {found['total']}."
        if found["total"] > len(found["items"]):
            text += f" Показываю последние {len(found['items'])}."
        if any(item["conditional"] for item in found["items"]):
            text += " Часть цен показана с условием: карта банка, кошелёк или купон."
        return {"text": text, "cards": found["items"]}

    def _on_history(self, intent):
        found = self._find(intent.query, limit=1)
        if not found["total"]:
            return {"text": NOT_FOUND.format(q=intent.query)}
        product = found["items"][0]
        with self.app.db.read() as conn:
            detail = product_detail(conn, product["id"])
        summary = detail["stats"]["lowest"]
        if not summary:
            return {"text": f"По «{product['title']}» нет наблюдений с ценой и известным временем.", "cards": [product]}
        currency = product["currency"] or "RUB"
        text = (
            f"«{product['title']}»: наблюдений с ценой {summary['count']}, "
            f"минимум {format_minor(summary['min'], currency)}, максимум {format_minor(summary['max'], currency)}, "
            f"последняя {format_minor(summary['last'], currency)}. {detail['hint']['text']}"
        )
        if found["total"] > 1:
            text += f" Похожих товаров ещё {found['total'] - 1}: уточните запрос, если это не тот."
        return {"text": text, "cards": [product]}

    def _on_compare(self, intent):
        first, second = self._find(intent.query, 1), self._find(intent.other, 1)
        missing = [query for query, found in ((intent.query, first), (intent.other, second)) if not found["total"]]
        if missing:
            return {"text": "Не нашёл в базе: " + ", ".join(f"«{query}»" for query in missing) + "."}
        a, b = first["items"][0], second["items"][0]
        if a["id"] == b["id"]:
            return {"text": "Оба запроса указывают на один товар. Уточните названия.", "cards": [a]}
        with self.app.db.read() as conn:
            result = lists.compare_products(conn, [a["id"], b["id"]])
        action = self._action("save_group", {"name": f"{a['title'][:40]} и {b['title'][:40]}",
                                             "product_ids": [a["id"], b["id"]]}, "Сохранить сравнение")
        return {"text": "Сравнение по последним наблюдениям. " + " ".join(result["table"]["notes"]),
                "cards": result["products"], "table": result["table"], "actions": [action]}

    def _on_compare_favorites(self, intent):
        with self.app.db.read() as conn:
            favorites = lists.list_favorites(conn, limit=8)
            if len(favorites) < 2:
                return {"text": "Для сравнения нужно хотя бы два товара в избранном."}
            result = lists.compare_products(conn, [item["id"] for item in favorites])
        return {"text": "Сравнение избранного по последним наблюдениям. " + " ".join(result["table"]["notes"]),
                "cards": result["products"], "table": result["table"]}

    def _on_watch(self, intent):
        if intent.percent is not None and not 1 <= intent.percent <= 90:
            return {"text": "Процент снижения должен быть от 1 до 90."}
        found = self._find(intent.query, 3)
        if not found["total"]:
            return {"text": NOT_FOUND.format(q=intent.query)}
        drop = intent.percent if intent.percent else (None if intent.amount_minor is not None else 5)
        label = (f"Следить до {format_minor(intent.amount_minor)}" if intent.amount_minor is not None
                 else f"Следить за снижением на {drop}%")
        items = found["items"][:3]
        actions = []
        for product in items:
            params = {"product_id": product["id"], "target_minor": intent.amount_minor, "drop_percent": drop,
                      "price_basis": "lowest_shown", "notify_availability": True}
            actions.append(self._action("create_watch", params, label + (f": {product['title'][:40]}" if len(items) > 1 else "")))
        text = ("Подтвердите слежение кнопкой. Считаю по минимальной показанной цене, включая цены с условием; "
                "это можно поменять в разделе «Слежение». Новые цены появятся, когда вы сохраните карточку через Eye.")
        if len(items) > 1:
            text = "Нашлось несколько товаров, выберите нужный. " + text
        return {"text": text, "cards": items, "actions": actions}

    def _on_favorite_add(self, intent):
        found = self._find(intent.query, 3)
        if not found["total"]:
            return {"text": NOT_FOUND.format(q=intent.query)}
        items = found["items"][:3]
        actions = [self._action("add_favorite", {"product_id": product["id"]},
                                "В избранное" + (f": {product['title'][:40]}" if len(items) > 1 else ""))
                   for product in items]
        return {"text": "Добавить в избранное? Подтвердите кнопкой.", "cards": items, "actions": actions}

    def _on_free(self, intent):
        if self.app.settings.get("assistant.llm") == "ollama":
            return self._ask_llm(intent.query)
        found = self._find(intent.query, 5)
        if found["total"]:
            result = self._on_search(intent)
            result["text"] = "Понял как поиск. " + result["text"]
            return result
        return {"text": "Не понял запрос. " + HELP_TEXT}

    def _facts(self, items):
        if not items:
            return "Подходящих товаров в базе нет."
        lines = []
        for product in items:
            seen = local_text(product["last_seen_at"], self.app.tz) if product["last_seen_at"] else "время неизвестно"
            lines.append(
                f"- {product['title']} ({product['source']}): {product['price_text']}, "
                f"наблюдений {product['observation_count']}, последнее {seen}, "
                f"наличие {AVAILABILITY.get(product['availability'], 'неизвестно')}"
            )
        return "\n".join(lines)

    def _ask_llm(self, question):
        found = self._find(question, 5)
        facts = self._facts(found["items"])
        if found["total"]:
            fallback = self._on_search(intents.Intent("search", query=question))
        else:
            fallback = {"text": NOT_FOUND.format(q=question)}
        client = OllamaClient(self.app.settings.get("assistant.ollama_port"), self.app.settings.get("assistant.ollama_model"))
        try:
            answer = client.chat(SYSTEM_PROMPT, "ФАКТЫ:\n" + facts + "\n\nВОПРОС: " + question)
        except AppError as error:
            fallback["note"] = "Локальная модель недоступна, ответ по правилам."
            fallback["llm"] = {"used": False, "error": error.code}
            return fallback
        supported, bad = numbers_supported(answer, facts + " " + question)
        if not supported:
            fallback["note"] = "Ответ модели отклонён: в нём есть числа, которых нет в данных."
            fallback["llm"] = {"used": False, "rejected_numbers": bad[:10]}
            return fallback
        return {"text": answer[:2000], "cards": found["items"], "llm": {"used": True, "model": client.model}}

    def confirm(self, token):
        if not isinstance(token, str) or not TOKEN.match(token):
            raise AppError("ACTION_NOT_FOUND", status=404)
        moment = self.app.clock.now()
        now = iso(moment)
        with self.app.db.write() as conn:
            row = conn.execute("SELECT * FROM assistant_action WHERE token = ?", (token,)).fetchone()
            if row is None:
                raise AppError("ACTION_NOT_FOUND", status=404)
            if row["status"] == "DONE":
                stored = json.loads(row["result_json"]) if row["result_json"] else None
                return {"token": token, "status": "DONE", "result": stored, "replayed": True}
            if row["status"] != "PROPOSED" or parse_aware(row["created_at"]) + ACTION_TTL < moment:
                raise AppError("ACTION_NOT_FOUND", status=410, detail="expired")
            result = self._execute(conn, row["kind"], json.loads(row["params_json"]), now)
            conn.execute(
                "UPDATE assistant_action SET status = 'DONE', done_at = ?, result_json = ? WHERE token = ?",
                (now, json.dumps(result, ensure_ascii=False), token),
            )
        return {"token": token, "status": "DONE", "result": result, "replayed": False}

    @staticmethod
    def _execute(conn, kind, params, now):
        if kind == "create_watch":
            watch = watches.put_watch(
                conn, params["product_id"], target_minor=params["target_minor"], price_basis=params["price_basis"],
                drop_percent=params["drop_percent"], notify_availability=params["notify_availability"], active=True,
                now=now)
            return {"text": "Слежение включено.", "watch": watch}
        if kind == "add_favorite":
            lists.set_favorite(conn, params["product_id"], True, None, now)
            return {"text": "Добавлено в избранное.", "product_id": params["product_id"]}
        if kind == "save_group":
            group_id = lists.create_group(conn, params["name"], params["product_ids"], now)
            return {"text": "Сравнение сохранено.", "group_id": group_id}
        raise AppError("ACTION_NOT_FOUND", status=404)
