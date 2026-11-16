"""Фикс: создать недостающие таблицы assistant_* по финальной схеме (post-V8).
Не трогает schema_migrations — только добавляет отсутствующие таблицы."""
import os, sys, sqlite3
from pathlib import Path

# Находим БД
home = os.environ.get("R52_HOME")
if home:
    db_path = Path(home).expanduser().resolve() / "data" / "r52.sqlite3"
else:
    local = os.environ.get("LOCALAPPDATA", "")
    db_path = Path(local) / "R52" / "data" / "r52.sqlite3"

print(f"DB: {db_path}")
if not db_path.exists():
    print(f"[FAIL] БД не найдена: {db_path}")
    sys.exit(1)

conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

# Все таблицы, которые должны быть после V1-V8
EXPECTED_TABLES = {
    "source", "product", "observation", "price_point", "inbox_item",
    "intake_file", "favorite", "compare_group", "compare_item", "watch",
    "event", "notification", "task", "task_transition", "schedule",
    "setting", "chat_session", "chat_message", "assistant_action",
    "import_run", "assistant_analysis_request", "assistant_llm_call",
    "assistant_request", "schema_migrations"
}

existing = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
)}

missing = EXPECTED_TABLES - existing
print(f"Таблиц в БД: {len(existing)}")
print(f"Ожидаемо: {len(EXPECTED_TABLES)}")
print(f"Не хватает: {sorted(missing) if missing else 'ничего'}")

if not missing:
    print("[OK] Все таблицы на месте")
    conn.close()
    sys.exit(0)

# Создаём недостающие по финальной схеме
FIXES = {
    "assistant_analysis_request": """
        CREATE TABLE IF NOT EXISTS assistant_analysis_request(
          key TEXT PRIMARY KEY,
          fingerprint TEXT NOT NULL,
          created_at TEXT NOT NULL,
          result_json TEXT,
          request_json TEXT CHECK(request_json IS NULL OR json_valid(request_json)),
          scope_json TEXT CHECK(scope_json IS NULL OR json_valid(scope_json))
        ) STRICT;
        CREATE INDEX IF NOT EXISTS analysis_request_created ON assistant_analysis_request(created_at);
    """,
    "assistant_llm_call": """
        CREATE TABLE IF NOT EXISTS assistant_llm_call(
          id INTEGER PRIMARY KEY,
          model TEXT NOT NULL,
          created_at TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN('PENDING','DONE','FAILED','UNKNOWN')),
          reserve_usd REAL,
          usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
          cost_usd REAL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS llm_call_created ON assistant_llm_call(created_at);
    """,
    "assistant_request": """
        CREATE TABLE IF NOT EXISTS assistant_request(
          key TEXT PRIMARY KEY,
          fingerprint TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN('STARTED','DONE','FAILED')),
          created_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS assistant_request_created ON assistant_request(created_at);
    """,
}

created = []
for tbl in sorted(missing):
    sql = FIXES.get(tbl)
    if sql:
        conn.executescript(sql)
        created.append(tbl)
        print(f"  [CREATE] {tbl}")
    else:
        print(f"  [WARN] {tbl} — нет шаблона, пропускаем")

conn.commit()

# Финальная проверка
existing2 = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
)}
still_missing = EXPECTED_TABLES - existing2
if still_missing:
    print(f"[WARN] Всё ещё не хватает: {sorted(still_missing)}")
else:
    print(f"[PASS] Все {len(EXPECTED_TABLES)} таблиц на месте")

# Проверка schema_migrations
rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
print(f"\nМиграции в БД: {[(r['version'], r['name']) for r in rows]}")

conn.close()
print("\nГотово.")
