"""
diag_schema_intake.py
=====================
Диагностика:
  1. Реальная схема SQLite (PRAGMA table_info) для ключевых таблиц.
  2. Таблица миграций и PRAGMA user_version.
  3. Трассировка auto_inbox -> auto_delivery (почему WB JSON не доставлен).
  4. Поиск validate_export в исходном коде проекта.
  5. Проверка таблицы товаров (products / observation).

Запуск:  py -3 debug\diag_schema_intake.py
Вывод:   debug\diag_schema_intake_output\report.json  +  stdout
"""

import json
import os
import re
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path

# --- Пути -------------------------------------------------------------------
PROJECT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT / "data" / "test_home" / "data" / "r52.sqlite3"
OUT_DIR = PROJECT / "debug" / "diag_schema_intake_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT = OUT_DIR / "report.json"

report: dict = {
    "ts": datetime.now().isoformat(),
    "db_path": str(DB_PATH),
    "db_exists": DB_PATH.exists(),
    "sections": {},
    "errors": [],
}

# --- 1. Схема таблиц ---------------------------------------------------------
def get_table_info(cur: sqlite3.Cursor, table: str) -> list[dict]:
    """PRAGMA table_info(table) -> список столбцов."""
    cur.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": bool(r[3]),
            "default": r[4],
            "pk": bool(r[5]),
        }
        for r in rows
    ]

def get_all_tables(cur: sqlite3.Cursor) -> list[str]:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]

KEY_TABLES = [
    "observation",
    "auto_inbox",
    "auto_delivery",
    "auto_target",
    "auto_source",
    "auto_task",
    "auto_tasks",
    "task",
    "tasks",
    "product",
    "products",
    "price",
    "prices",
    "migration",
    "migrations",
    "schema_migrations",
    "app_log",
    "log",
    "logs",
    "error_log",
    "observation_price",
    "observation_image",
    "observation_attr",
]

def section_schema(db: sqlite3.Connection):
    cur = db.cursor()
    all_tables = get_all_tables(cur)
    report["sections"]["all_tables"] = all_tables

    schema: dict = {}
    for t in KEY_TABLES:
        if t in all_tables:
            schema[t] = get_table_info(cur, t)
        else:
            # попробуем найти похожее
            matches = [x for x in all_tables if t.replace("s", "") in x.lower()]
            schema[t] = f"NOT FOUND (similar: {matches})" if matches else "NOT FOUND"
    report["sections"]["key_tables_schema"] = schema

    # user_version
    cur.execute("PRAGMA user_version")
    report["sections"]["user_version"] = cur.fetchone()[0]

    # foreign_keys
    cur.execute("PRAGMA foreign_key_list(auto_inbox)")
    report["sections"]["auto_inbox_fks"] = [list(r) for r in cur.fetchall()]
    cur.execute("PRAGMA foreign_key_list(auto_delivery)")
    report["sections"]["auto_delivery_fks"] = [list(r) for r in cur.fetchall()]

# --- 2. Миграции -------------------------------------------------------------
def section_migrations(db: sqlite3.Connection):
    cur = db.cursor()
    all_tables = get_all_tables(cur)
    mig_tables = [t for t in all_tables if "migrat" in t.lower()]
    report["sections"]["migration_tables"] = mig_tables

    for mt in mig_tables:
        try:
            cur.execute(f"SELECT * FROM {mt} ORDER BY rowid DESC LIMIT 50")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            report["sections"][f"migration_data_{mt}"] = rows
        except Exception as e:
            report["sections"][f"migration_data_{mt}"] = f"ERROR: {e}"

# --- 3. auto_inbox -> auto_delivery ------------------------------------------
def section_inbox_delivery(db: sqlite3.Connection):
    cur = db.cursor()
    all_tables = get_all_tables(cur)

    # auto_inbox
    if "auto_inbox" in all_tables:
        cur.execute("SELECT * FROM auto_inbox ORDER BY rowid DESC LIMIT 20")
        cols = [d[0] for d in cur.description]
        report["sections"]["auto_inbox_rows"] = [dict(zip(cols, r)) for r in cur.fetchall()]

    # auto_delivery
    if "auto_delivery" in all_tables:
        cur.execute("SELECT * FROM auto_delivery ORDER BY rowid DESC LIMIT 20")
        cols = [d[0] for d in cur.description]
        report["sections"]["auto_delivery_rows"] = [dict(zip(cols, r)) for r in cur.fetchall()]

    # auto_target
    if "auto_target" in all_tables:
        cur.execute("SELECT * FROM auto_target ORDER BY rowid DESC LIMIT 20")
        cols = [d[0] for d in cur.description]
        report["sections"]["auto_target_rows"] = [dict(zip(cols, r)) for r in cur.fetchall()]

    # auto_source
    if "auto_source" in all_tables:
        cur.execute("SELECT * FROM auto_source ORDER BY rowid DESC LIMIT 20")
        cols = [d[0] for d in cur.description]
        report["sections"]["auto_source_rows"] = [dict(zip(cols, r)) for r in cur.fetchall()]

    # observation (или products)
    obs_table = None
    for candidate in ("observation", "products", "product"):
        if candidate in all_tables:
            obs_table = candidate
            break
    if obs_table:
        cur.execute(f"SELECT * FROM {obs_table} ORDER BY rowid DESC LIMIT 20")
        cols = [d[0] for d in cur.description]
        report["sections"][f"{obs_table}_rows"] = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) FROM {obs_table}")
        report["sections"][f"{obs_table}_count"] = cur.fetchone()[0]

# --- 4. Поиск validate_export ------------------------------------------------
def section_validate_export():
    """Поиск импортов и определений validate_export в .py файлах проекта."""
    pattern = re.compile(r"validate_export", re.IGNORECASE)
    hits: list[dict] = []
    for py_file in PROJECT.rglob("*.py"):
        if "site-packages" in str(py_file) or ".venv" in str(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append({
                    "file": str(py_file.relative_to(PROJECT)),
                    "line": i,
                    "text": line.strip()[:200],
                })
    report["sections"]["validate_export_hits"] = hits
    report["sections"]["validate_export_count"] = len(hits)

# --- 5. Проверка intake pipeline (eye.py, folder.py) --------------------------
def section_intake_code():
    """Ключевые строки из eye.py и folder.py для трассировки маршрута."""
    snippets: dict = {}
    for rel in ("r52/intake/eye.py", "r52/intake/folder.py", "r52/intake/__init__.py"):
        p = PROJECT / rel
        if not p.exists():
            snippets[rel] = "FILE NOT FOUND"
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        # ищем: class, def, COLLECTOR_AUTONOMOUS, EXPLICIT_POPUP, fully_autonomous,
        #       validate, ACCEPTED, REJECTED, delivery, inbox
        keywords = re.compile(
            r"(class\s+\w+|def\s+\w+|COLLECTOR_AUTONOMOUS|EXPLICIT_POPUP|fully_autonomous|"
            r"validate|ACCEPTED|REJECTED|delivery|inbox|status|error_code|capture_action|"
            r"JARVIS_BR02|import\s+.*eye|from\s+.*eye)",
            re.IGNORECASE,
        )
        matched = [
            {"line": i, "text": l.strip()[:200]}
            for i, l in enumerate(lines, 1)
            if keywords.search(l)
        ]
        snippets[rel] = {"total_lines": len(lines), "matched": matched[:80]}
    report["sections"]["intake_code"] = snippets

# --- 6. Проверка runner / autocollect ----------------------------------------
def section_runner():
    snippets: dict = {}
    for rel in ("r52/collectors/runner.py", "r52/core/autocollect.py", "r52/collectors/auto_worker.py"):
        p = PROJECT / rel
        if not p.exists():
            snippets[rel] = "FILE NOT FOUND"
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        keywords = re.compile(
            r"(class\s+\w+|def\s+\w+|COLLECTOR_AUTONOMOUS|JARVIS_BR02|WORKER_MAX|WORKER_ITEM|"
            r"subprocess|Popen|timeout|json\.dump|inbox|output|result|status|error)",
            re.IGNORECASE,
        )
        matched = [
            {"line": i, "text": l.strip()[:200]}
            for i, l in enumerate(lines, 1)
            if keywords.search(l)
        ]
        snippets[rel] = {"total_lines": len(lines), "matched": matched[:80]}
    report["sections"]["runner_code"] = snippets

# --- main --------------------------------------------------------------------
def main():
    print(f"[diag_schema_intake] DB: {DB_PATH}  exists={DB_PATH.exists()}")

    if DB_PATH.exists():
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = None
        try:
            section_schema(db)
            section_migrations(db)
            section_inbox_delivery(db)
        except Exception as e:
            report["errors"].append(f"DB section: {e}\n{traceback.format_exc()}")
        finally:
            db.close()
    else:
        report["errors"].append("DB file not found")

    section_validate_export()
    section_intake_code()
    section_runner()

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[diag_schema_intake] Report: {REPORT}")
    print(f"[diag_schema_intake] Sections: {list(report['sections'].keys())}")
    print(f"[diag_schema_intake] Errors: {len(report['errors'])}")
    for e in report["errors"]:
        print(f"  ERR: {e[:200]}")
    print("[diag_schema_intake] DONE")

if __name__ == "__main__":
    main()
