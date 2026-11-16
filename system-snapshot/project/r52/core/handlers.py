"""Обработчики задач. Все работают только локально, без сети."""

import logging
import sqlite3
from datetime import timedelta
from pathlib import Path

from ..db.backup import create_backup, rotate_backups
from ..errors import AppError
from ..sources import collector_for
from ..timeutil import iso
from ..validate import boolean, choice, fail, integer, only_keys, text
from .tasks import Handler, TaskOutcome

log = logging.getLogger("r52.handlers")


def _local_day(app):
    return app.clock.now().astimezone(app.tz).strftime("%Y-%m-%d")


def _empty(params):
    only_keys(params, set())
    return {}


def _backup_params(params):
    only_keys(params, {"label"})
    return {"label": choice(params, "label", {"auto", "manual"}, "auto")}


def _run_backup(app, ctx, params):
    try:
        info = create_backup(app.db, app.paths.backups_dir, label=params["label"], now=app.clock.now())
    except (AppError, OSError, sqlite3.Error) as error:
        with app.db.write() as conn:
            app.notifier.add_event(
                conn, kind="BACKUP_FAILED", fact_key=f"backup-failed:{_local_day(app)}",
                payload={"error": getattr(error, "code", type(error).__name__)},
                title="Резервная копия не создана",
                body="Проверьте свободное место на диске. Подробности в журнале R-52.",
            )
        raise
    removed = rotate_backups(
        app.paths.backups_dir, app.settings.get("backup.keep_daily"), app.settings.get("backup.keep_weekly"))
    return {**info, "removed": removed}


def _run_maintenance(app, ctx, params):
    now = app.clock.now()
    cutoff = iso(now - timedelta(days=app.settings.get("retention.days")))
    expired = iso(now - timedelta(days=1))
    with app.db.write() as conn:
        result = {
            "tasks": conn.execute(
                "DELETE FROM task WHERE status IN ('COMPLETED', 'CANCELLED', 'FAILED', 'PARTIAL', 'BLOCKED') "
                "AND finished_at < ?", (cutoff,)).rowcount,
            "notifications": conn.execute(
                "DELETE FROM notification WHERE status IN ('READ', 'DISMISSED') AND created_at < ?", (cutoff,)).rowcount,
            "events": conn.execute(
                "DELETE FROM event WHERE created_at < ? AND id NOT IN "
                "(SELECT event_id FROM notification WHERE event_id IS NOT NULL)", (cutoff,)).rowcount,
            "intake_files": conn.execute("DELETE FROM intake_file WHERE seen_at < ?", (cutoff,)).rowcount,
            "actions_expired": conn.execute(
                "UPDATE assistant_action SET status = 'EXPIRED' WHERE status = 'PROPOSED' AND created_at < ?",
                (expired,)).rowcount,
        }
    ctx.check()
    check = app.db.quick_check()
    result["quick_check"] = check
    with app.db.read() as conn:
        conn.execute("PRAGMA optimize")
    result["checkpoint"] = list(app.db.checkpoint())
    if check != "ok":
        with app.db.write() as conn:
            app.notifier.add_event(
                conn, kind="DB_CHECK_FAILED", fact_key=f"db-check:{_local_day(app)}", payload={"result": check[:500]},
                title="Проверка базы нашла ошибки",
                body="Сделайте резервную копию и восстановите базу из последней проверенной копии.",
            )
        raise TaskOutcome("PARTIAL", "DB_QUICK_CHECK_FAILED", result)
    return result


def _run_digest(app, ctx, params):
    now = app.clock.now()
    since = iso(now - timedelta(hours=24))
    day = _local_day(app)
    with app.db.read() as conn:
        observations = conn.execute("SELECT count(*) FROM observation WHERE imported_at >= ?", (since,)).fetchone()[0]
        kinds = {row["kind"]: row["n"] for row in conn.execute(
            "SELECT kind, count(*) AS n FROM event WHERE created_at >= ? AND kind <> 'DIGEST' GROUP BY kind", (since,))}
        inbox = conn.execute("SELECT count(*) FROM inbox_item WHERE status = 'PENDING'").fetchone()[0]
        watches = conn.execute("SELECT count(*) FROM watch WHERE active = 1").fetchone()[0]
    parts = []
    if observations:
        parts.append(f"новых наблюдений: {observations}")
    price_events = kinds.get("TARGET_REACHED", 0) + kinds.get("PRICE_DROPPED", 0)
    if price_events:
        parts.append(f"срабатываний слежения: {price_events}")
    availability = kinds.get("BECAME_AVAILABLE", 0) + kinds.get("BECAME_UNAVAILABLE", 0)
    if availability:
        parts.append(f"изменений наличия: {availability}")
    if inbox:
        parts.append(f"ждут проверки во входящих: {inbox}")
    payload = {"day": day, "observations": observations, "events": kinds, "inbox_pending": inbox, "watches": watches}
    body = ("За сутки " + ", ".join(parts) + ".") if parts else None
    with app.db.write() as conn:
        event_id = app.notifier.add_event(
            conn, kind="DIGEST", fact_key=f"digest:{day}", payload=payload,
            title="Сводка R-52" if body else None, body=body,
        )
    return {**payload, "event_id": event_id, "notified": bool(body and event_id)}


def _legacy_params(params):
    only_keys(params, {"d0", "data01", "dry_run"})
    result = {
        "d0": text(params, "d0", 1000),
        "data01": text(params, "data01", 1000),
        "dry_run": boolean(params, "dry_run", False),
    }
    if not result["d0"] and not result["data01"]:
        raise fail("d0", "path_required")
    return result


def _run_legacy(app, ctx, params):
    from ..intake.legacy import import_d0, import_data01

    result = {}
    if params["d0"]:
        result["d0"] = import_d0(app, Path(params["d0"]).expanduser(), ctx=ctx, dry_run=params["dry_run"])
    if params["data01"]:
        result["data01"] = import_data01(app, Path(params["data01"]).expanduser(), ctx=ctx, dry_run=params["dry_run"])
    return result


def _collect_params(params):
    only_keys(params, {"product_id", "source"})
    result = {"product_id": integer(params, "product_id", 1, 2**62), "source": text(params, "source", 40)}
    if result["product_id"] is None and result["source"] is None:
        raise fail("product_id", "required")
    return result


def _run_collect(app, ctx, params):
    from .autocollect import BatchFailure, SourceDisabled, batch, run

    source = params["source"]
    if params["product_id"] is not None:
        with app.db.read() as conn:
            row = conn.execute("SELECT source,listing_id FROM product WHERE id = ?", (params["product_id"],)).fetchone()
        if row is None:
            raise AppError("PRODUCT_NOT_FOUND", status=404)
        source = row["source"]
    collector_cls = collector_for(source)
    if collector_cls is None:
        raise TaskOutcome("BLOCKED", "SOURCE_NOT_IMPLEMENTED", {"source": source})
    source = source.upper()
    if params["product_id"] is None:
        return run(app, ctx, {"force": True, "source": source})
    try:
        published, rejected, _ = batch(app, ctx, dict(source=source, mode="refresh",
                                ids=[row["listing_id"]], limit=1))
    except SourceDisabled:
        raise TaskOutcome("BLOCKED", "SOURCE_DISABLED", {"source": source}) from None
    except BatchFailure as error:
        raise TaskOutcome("PARTIAL", str(error), {"source": source,
                          "published": error.published[0], "diagnostic": error.diagnostic}) from error
    result = {"source": source, "product_id": params["product_id"],
              "published": published, "rejected": rejected, "import_pending": bool(published)}
    if rejected or not published:
        raise TaskOutcome("PARTIAL", "EMPTY_OR_INVALID_EXPORT", result)
    return result


def register_all(app):
    from .autocollect import run
    def auto_params(params):
        only_keys(params, {"force"})
        return {"force": boolean(params, "force", False)}
    tasks = app.tasks
    tasks.register(Handler("autocollect", lambda ctx, p: run(app, ctx, p), auto_params,
                           retry_safe=False, timeout_seconds=3600, title="Автосбор"))
    tasks.register(Handler("backup", lambda ctx, p: _run_backup(app, ctx, p), _backup_params,
                           retry_safe=True, timeout_seconds=1800, title="Резервная копия"))
    tasks.register(Handler("maintenance", lambda ctx, p: _run_maintenance(app, ctx, p), _empty,
                           retry_safe=True, timeout_seconds=1800, title="Обслуживание базы"))
    tasks.register(Handler("digest", lambda ctx, p: _run_digest(app, ctx, p), _empty,
                           retry_safe=True, timeout_seconds=120, title="Сводка за сутки"))
    tasks.register(Handler("import_legacy", lambda ctx, p: _run_legacy(app, ctx, p), _legacy_params,
                           retry_safe=True, timeout_seconds=3600, title="Импорт старых баз R-52"))
    tasks.register(Handler("collect", lambda ctx, p: _run_collect(app, ctx, p), _collect_params,
                           retry_safe=False, timeout_seconds=600, title="Сбор с источника"))
