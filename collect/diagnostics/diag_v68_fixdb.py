r"""Диагностика и фикс постоянной БД: schema_migrations vs существующие таблицы.

Запуск: C:\Python315\python.exe diag_v68_fixdb.py
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

DB = BASE / "r52" / "data" / "r52.db"
OUT = BASE / "diag_v68_fixdb_result.json"

result = {"db": str(DB), "exists": DB.exists()}

if not DB.exists():
    result["action"] = "NO_DB"
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DB not found — nothing to fix")
    sys.exit(0)

result["size"] = DB.stat().st_size
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# 1. Все таблицы
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
result["tables"] = tables
print("TABLES:", tables)

# 2. Счётчики
for t in tables:
    try:
        n = conn.execute(f"SELECT count(*) FROM [{t}]").fetchone()[0]
        result[f"count_{t}"] = n
        print(f"  {t}: {n}")
    except Exception as e:
        result[f"count_{t}"] = f"ERR: {e}"

# 3. schema_migrations
sm_exists = "schema_migrations" in tables
result["schema_migrations_exists"] = sm_exists
if sm_exists:
    sm_rows = conn.execute("SELECT version, name, applied_at FROM schema_migrations ORDER BY version").fetchall()
    result["migrations"] = [(r["version"], r["name"], r["applied_at"]) for r in sm_rows]
    print("MIGRATIONS:", result["migrations"])

# 4. Максимальная версия из кода
from r52.db.migrate import latest_version, MIGRATIONS
max_ver = latest_version()
result["code_max_version"] = max_ver
result["code_migrations"] = [(v, n) for v, n, _ in MIGRATIONS]
print(f"CODE max version: {max_ver}")
print("CODE migrations:", result["code_migrations"])

# 5. Фикс: если таблицы есть, но schema_migrations пуст → вписать max version
applied_max = 0
if sm_exists:
    row = conn.execute("SELECT MAX(version) as mv FROM schema_migrations").fetchone()
    applied_max = row["mv"] or 0
    result["applied_max"] = applied_max
    print(f"Applied max: {applied_max}")

# Проверка: есть ли ключевые таблицы
key_tables = {"source", "product", "observation", "price_point"}
has_key = key_tables.issubset(set(tables))
result["has_key_tables"] = has_key

if has_key and applied_max < max_ver:
    # Вписать все миграции как применённые (таблицы уже существуют)
    from r52.timeutil import iso
    from datetime import datetime, timezone
    stamp = iso(datetime.now(timezone.utc))
    conn.execute("BEGIN IMMEDIATE")
    for ver, name, sql in MIGRATIONS:
        if ver > applied_max:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, name, checksum, applied_at) VALUES(?, ?, ?, ?)",
                (ver, name, __import__("hashlib").sha256(sql.encode()).hexdigest(), stamp),
            )
    conn.execute("COMMIT")
    result["action"] = f"FIXED: inserted versions {applied_max+1}..{max_ver}"
    print(f"FIXED: inserted versions {applied_max+1}..{max_ver}")
elif has_key and applied_max >= max_ver:
    result["action"] = "OK: already up to date"
    print("OK: schema already up to date")
else:
    result["action"] = "NO_KEY_TABLES: may need full migration"
    print("No key tables — full migration needed")

conn.close()
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nResult: {OUT}")
print("DONE")
