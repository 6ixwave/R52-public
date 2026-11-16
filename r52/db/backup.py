"""Резервные копии: снимок через backup API SQLite, проверка, ротация, восстановление.

Копия считается готовой только после quick_check и проверки таблицы миграций.
До этого она лежит под именем .partial и не попадает в список.
"""

import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..errors import AppError
from ..timeutil import utc_now

log = logging.getLogger("r52.backup")
NAME = re.compile(r"^r52-(\d{8}T\d{6}Z)(?:-([a-z0-9-]{1,40}))?\.sqlite3$")
KEEP_OTHER = 10


def _stamp(now):
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def verify_backup(path):
    path = Path(path)
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            versions = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as error:
        raise AppError("BACKUP_VERIFY_FAILED", status=500, detail=str(error)) from None
    if result != "ok" or versions < 1:
        raise AppError("BACKUP_VERIFY_FAILED", status=500, detail=str(result))
    return True


def create_backup(db, backup_dir, label=None, now=None):
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    if label is not None and not re.fullmatch(r"[a-z0-9-]{1,40}", label):
        raise AppError("INVALID_INPUT", detail="label")
    moment = now or utc_now()
    name = f"r52-{_stamp(moment)}" + (f"-{label}" if label else "") + ".sqlite3"
    target = backup_dir / name
    if target.exists():
        raise AppError("BACKUP_EXISTS", status=409, detail=name)
    partial = backup_dir / (name + ".partial")
    source = db.connect()
    try:
        dest = sqlite3.connect(partial)
        try:
            # Порциями, чтобы не держать читающую транзакцию слишком долго.
            source.backup(dest, pages=2048)
            # Копия должна быть одним файлом без -wal и -shm.
            dest.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest.close()
    finally:
        source.close()
    try:
        verify_backup(partial)
    except AppError:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, target)
    size = target.stat().st_size
    log.info("Резервная копия %s создана, %s байт", name, size)
    return {"name": name, "size": size, "label": label}


def list_backups(backup_dir):
    backup_dir = Path(backup_dir)
    items = []
    if not backup_dir.is_dir():
        return items
    for path in backup_dir.iterdir():
        match = NAME.match(path.name)
        if not match or not path.is_file():
            continue
        created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        items.append({
            "name": path.name,
            "created_at": created.isoformat(timespec="seconds"),
            "label": match.group(2),
            "size": path.stat().st_size,
        })
    items.sort(key=lambda item: item["name"], reverse=True)
    return items


def rotate_backups(backup_dir, keep_daily=7, keep_weekly=4):
    """Автоматические копии: последняя за каждый из keep_daily дней и за каждую из
    keep_weekly недель. Остальные метки (manual, pre-restore, pre-migrate): последние 10."""
    items = list_backups(backup_dir)
    keep = set()
    days, weeks = [], []
    for item in (entry for entry in items if entry["label"] == "auto"):
        created = datetime.fromisoformat(item["created_at"])
        day = created.date()
        week = created.isocalendar()[:2]
        if day not in days and len(days) < keep_daily:
            days.append(day)
            keep.add(item["name"])
        if week not in weeks and len(weeks) < keep_weekly:
            weeks.append(week)
            keep.add(item["name"])
    others = [entry for entry in items if entry["label"] != "auto"]
    keep.update(entry["name"] for entry in others[:KEEP_OTHER])
    removed = []
    for item in items:
        if item["name"] not in keep:
            (Path(backup_dir) / item["name"]).unlink(missing_ok=True)
            removed.append(item["name"])
    return removed


def restore_backup(db_file, backup_dir, name, now=None):
    """Восстанавливает базу из копии. Вызывать только при остановленном ядре."""
    from .database import Database

    if not isinstance(name, str) or not NAME.match(name):
        raise AppError("BACKUP_NOT_FOUND", status=404)
    source = Path(backup_dir) / name
    if not source.is_file():
        raise AppError("BACKUP_NOT_FOUND", status=404)
    verify_backup(source)
    db_file = Path(db_file)
    safety = None
    if db_file.exists():
        try:
            safety = create_backup(Database(db_file), backup_dir, label="pre-restore", now=now)["name"]
        except (AppError, sqlite3.Error) as error:
            # Текущая база может быть повреждена: тогда сохраняем файлы как есть.
            log.warning("Копия текущей базы не прошла проверку: %s", error)
            safety = f"r52-{_stamp(now or utc_now())}-pre-restore-raw.sqlite3"
            shutil.copy2(db_file, Path(backup_dir) / safety)
    staging = db_file.with_name(db_file.name + ".restore")
    shutil.copyfile(source, staging)
    # Старый WAL от другой базы нельзя оставлять: SQLite попытается его применить.
    for suffix in ("-wal", "-shm"):
        Path(str(db_file) + suffix).unlink(missing_ok=True)
    os.replace(staging, db_file)
    log.warning("База восстановлена из %s, прежняя сохранена как %s", name, safety)
    return {"restored": name, "safety_copy": safety}
