"""Применение миграций по номерам, каждая в своей транзакции."""

import hashlib
import logging
import sqlite3

from ..errors import AppError
from ..timeutil import iso, utc_now
from .schema import MIGRATIONS

log = logging.getLogger("r52.db")


def checksum(sql):
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def latest_version():
    return max(version for version, _, _ in MIGRATIONS)


def applied_versions(conn):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return {}
    return {row["version"]: row for row in conn.execute("SELECT * FROM schema_migrations")}


def migrate(db, backup_dir=None, now=None):
    """Применяет недостающие миграции. Перед изменением существующей базы делает копию."""
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
            "applied_at TEXT NOT NULL) STRICT"
        )
        applied = applied_versions(conn)
        known = {version: (name, sql) for version, name, sql in MIGRATIONS}
        unknown = sorted(version for version in applied if version not in known)
        if unknown:
            raise AppError(
                "SCHEMA_TOO_NEW",
                status=500,
                detail={"database": sorted(applied), "code_latest": latest_version()},
            )
        for version, row in applied.items():
            if row["checksum"] != checksum(known[version][1]):
                raise AppError("MIGRATION_CHECKSUM_MISMATCH", status=500, detail={"version": version})
        pending = [(version, name, sql) for version, name, sql in MIGRATIONS if version not in applied]
        if not pending:
            return {"applied": [], "version": max(applied) if applied else 0}
        if applied and backup_dir is not None:
            from .backup import create_backup

            create_backup(db, backup_dir, label=f"pre-migrate-v{max(applied)}")
        done = []
        for version, name, sql in pending:
            stamp = iso(now or utc_now())
            script = (
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
                f"VALUES({int(version)}, '{name}', '{checksum(sql)}', '{stamp}');\n"
                "COMMIT;"
            )
            try:
                conn.executescript(script)
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            log.info("Миграция %s (%s) применена", version, name)
            done.append(version)
        return {"applied": done, "version": done[-1]}
    finally:
        conn.close()


def schema_version(db):
    with db.read() as conn:
        applied = applied_versions(conn)
    return max(applied) if applied else 0
