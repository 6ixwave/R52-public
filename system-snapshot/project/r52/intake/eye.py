"""Выгрузки Eye: проверка формата, входящие на проверку, принятие в базу.

Принимается только выгрузка, сделанная явным нажатием в расширении
(capture_action EXPLICIT_POPUP_CLICK и fully_autonomous false). Неподходящий файл не
теряется молча: он попадает во входящие со статусом INVALID и кодом причины.
"""

import hashlib
import json
import logging

from ..core.watches import evaluate
from ..domain import pricing
from ..domain.normalize import EYE_SCOPE, fingerprint, normalize_eye
from ..errors import MESSAGES, AppError
from ..store.products import add_observation, ensure_source, refresh_summary, upsert_product
from ..timeutil import iso

log = logging.getLogger("r52.intake")
FORMATS = ("JARVIS_BR02_EYE_USER_CAPTURE", "JARVIS_EYE_REVIEW_1", "JARVIS_BR02_COLLECTOR_AUTONOMOUS")
MAX_BYTES = 16_000_000


def parse_export_bytes(data):
    if len(data) > MAX_BYTES:
        raise AppError("EXPORT_TOO_LARGE", status=413)
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise AppError("EXPORT_NOT_JSON") from None


def check_export(document):
    if not isinstance(document, dict) or document.get("format") not in FORMATS:
        raise AppError("EXPORT_FORMAT_UNSUPPORTED")
    fmt = document["format"]
    if fmt == "JARVIS_BR02_COLLECTOR_AUTONOMOUS":
        if document.get("capture_action") != "COLLECTOR_RUN" or document.get("fully_autonomous") is not True:
            raise AppError("MANUAL_CAPTURE_REQUIRED")
    else:
        if document.get("capture_action") != "EXPLICIT_POPUP_CLICK" or document.get("fully_autonomous") is not False:
            raise AppError("MANUAL_CAPTURE_REQUIRED")
    observation = document.get("observation")
    if not isinstance(observation, dict):
        raise AppError("EXPORT_FORMAT_UNSUPPORTED")
    return observation


def preview(normalized):
    summary = pricing.summarize(normalized["prices"])
    return {
        "source": normalized["source"],
        "listing_id": normalized["listing_id"],
        "url": normalized["url"],
        "title": normalized["title"],
        "observed_at": normalized["observed_at"],
        "price_text": pricing.price_line(summary),
        **summary,
        "prices": [
            {"role": item["role"], "role_label": pricing.ROLE_LABELS[item["role"]], "amount_minor": item["amount_minor"],
             "currency": item["currency"], "label": item["label"]}
            for item in normalized["prices"]
        ],
        "availability": normalized["availability"],
        "completeness": normalized["completeness"],
        "conflicts": normalized["conflicts"],
        "checks": normalized["checks"],
        "photo_count": sum(i.get('type')=='image' for i in (normalized.get('media') or {}).get('items',[])),
        "video_count": sum(i.get('type')=='video' for i in (normalized.get('media') or {}).get('items',[])),
    }


def item_dict(row):
    data = dict(row)
    data.pop("payload_json", None)
    stored = data.pop("preview_json", None)
    data["preview"] = json.loads(stored) if stored else None
    data["error_message"] = MESSAGES.get(data["error_code"]) if data["error_code"] else None
    return data


def _dump(value):
    return None if value is None else json.dumps(value, ensure_ascii=False)


class Inbox:
    def __init__(self, app):
        self.app = app

    def stage_bytes(self, data, filename=None, origin="API"):
        """Добавляет файл во входящие. Возвращает (item, created). Тот же файл дважды не добавляется."""
        digest = hashlib.sha256(data).hexdigest()
        status, error_code, payload, shown = "PENDING", None, None, None
        try:
            document = parse_export_bytes(data)
            observation = check_export(document)
            normalized, cleaned = normalize_eye(observation)
            payload = {"format": document["format"], "observation": cleaned}
            shown = preview(normalized)
        except AppError as error:
            status, error_code = "INVALID", error.code
        name = (filename or "")[:255] or None
        now = iso(self.app.clock.now())
        with self.app.db.write() as conn:
            existing = conn.execute("SELECT * FROM inbox_item WHERE content_sha256 = ?", (digest,)).fetchone()
            if existing is not None:
                if existing["status"] == "INVALID" and status == "PENDING":
                    conn.execute(
                        "UPDATE inbox_item SET status = ?, error_code = NULL, payload_json = ?, preview_json = ?, received_at = ? WHERE id = ?",
                        (status, _dump(payload), _dump(shown), now, existing["id"]),
                    )
                    row = conn.execute("SELECT * FROM inbox_item WHERE id = ?", (existing["id"],)).fetchone()
                    log.info("Входящий %s обновлён: INVALID → PENDING", name)
                    if self.app.settings.get("intake.auto_accept"):
                        return self.accept(row["id"]), True
                    return item_dict(row), True
                return item_dict(existing), False
            item_id = conn.execute(
                "INSERT INTO inbox_item(content_sha256, filename, origin, received_at, status, error_code, payload_json, "
                "preview_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (digest, name, origin, now, status, error_code, _dump(payload), _dump(shown)),
            ).lastrowid
            row = conn.execute("SELECT * FROM inbox_item WHERE id = ?", (item_id,)).fetchone()
        log.info("Во входящие добавлен файл %s: %s %s", name, status, error_code or "")
        if status == "PENDING" and self.app.settings.get("intake.auto_accept"):
            return self.accept(item_id), True
        return item_dict(row), True

    def accept(self, item_id):
        now = iso(self.app.clock.now())
        with self.app.db.write() as conn:
            row = conn.execute("SELECT * FROM inbox_item WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise AppError("INBOX_ITEM_NOT_FOUND", status=404)
            if row["status"] != "PENDING":
                raise AppError("INBOX_ITEM_DECIDED", status=409)
            payload_doc = json.loads(row["payload_json"])
            fmt = payload_doc.get("format", "JARVIS_BR02_EYE_USER_CAPTURE")
            origin = "COLLECTOR" if fmt == "JARVIS_BR02_COLLECTOR_AUTONOMOUS" else "EYE"
            normalized, cleaned = normalize_eye(payload_doc["observation"])
            ensure_source(conn, normalized["source"], normalized["source"], "marketplace")
            product_id, _ = upsert_product(
                conn, source=normalized["source"], listing_id=normalized["listing_id"], title=normalized["title"],
                url=normalized["url"], brand=normalized["brand"], model=normalized["model"], origin=origin, now=now,
            )
            observation_id, created = add_observation(
                conn, product_id=product_id, normalized=normalized, raw=cleaned, origin=origin,
                evidence_level=str(normalized["eye"]["evidence_level"] or ("COLLECTOR_AUTONOMOUS_HTTP" if origin == "COLLECTOR" else "EYE_USER_CAPTURE")),
                fingerprint=fingerprint(EYE_SCOPE, cleaned), observed_at=normalized["observed_at"],
                availability=normalized["availability"], prices=normalized["prices"], now=now,
            )
            refresh_summary(conn, product_id)
            events = evaluate(conn, self.app.notifier, product_id, observation_id) if created else []
            conn.execute(
                "UPDATE inbox_item SET status = 'ACCEPTED', decided_at = ?, observation_id = ? WHERE id = ?",
                (now, observation_id, item_id),
            )
            row = conn.execute("SELECT * FROM inbox_item WHERE id = ?", (item_id,)).fetchone()
        result = item_dict(row)
        result.update({"product_id": product_id, "observation_created": created, "events": events})
        return result

    def reject(self, item_id):
        now = iso(self.app.clock.now())
        with self.app.db.write() as conn:
            row = conn.execute("SELECT status FROM inbox_item WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise AppError("INBOX_ITEM_NOT_FOUND", status=404)
            if row["status"] not in ("PENDING", "INVALID"):
                raise AppError("INBOX_ITEM_DECIDED", status=409)
            conn.execute("UPDATE inbox_item SET status = 'REJECTED', decided_at = ? WHERE id = ?", (now, item_id))
            return item_dict(conn.execute("SELECT * FROM inbox_item WHERE id = ?", (item_id,)).fetchone())

    def list(self, status=None, limit=100):
        """Без статуса - только записи, требующие внимания (PENDING и INVALID); ALL - все записи."""
        with self.app.db.read() as conn:
            if status == "ALL":
                rows = conn.execute("SELECT * FROM inbox_item ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            elif status:
                rows = conn.execute(
                    "SELECT * FROM inbox_item WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM inbox_item WHERE status IN ('PENDING', 'INVALID') ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
        return [item_dict(row) for row in rows]

    def pending_count(self):
        with self.app.db.read() as conn:
            return conn.execute("SELECT count(*) FROM inbox_item WHERE status = 'PENDING'").fetchone()[0]
