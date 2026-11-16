"""Быстрая проверка SQLite: таблицы, записи, ключевые поля.
Использование: python check_sql.py <path_to_db>
"""
import sqlite3, os, sys

if len(sys.argv) < 2:
    print("Usage: python check_sql.py <db_path>")
    sys.exit(1)

DB = sys.argv[1]
if not os.path.exists(DB):
    print(f"DB NOT FOUND: {DB}")
    sys.exit(1)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
)]
print(f"TABLES ({len(tables)}): {tables}")

for t in tables:
    cur.execute(f"SELECT * FROM [{t}]")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print(f"\n=== {t} ({len(rows)} rows) ===")
    print(f"  COLS: {cols}")
    for i, row in enumerate(rows[:20]):
        d = dict(row)
        for k in list(d):
            if isinstance(d[k], str) and len(d[k]) > 120:
                d[k] = d[k][:117] + "..."
        print(f"  [{i}] {d}")

conn.close()
print("\nDONE")
