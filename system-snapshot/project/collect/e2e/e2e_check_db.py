"""Проверка версии схемы в БД и списка таблиц."""
import sqlite3, sys
from pathlib import Path

DB = Path(r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3")
print(f"DB path: {DB}")
print(f"Exists: {DB.exists()}")
if not DB.exists():
    sys.exit(1)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# 1. Версии миграций
print("\n=== schema_migrations ===")
rows = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
for r in rows:
    print(f"  v{r['version']} | {r['name']} | checksum={r['checksum'][:16]}... | {r['applied_at']}")

# 2. Таблицы
print("\n=== Tables ===")
tabs = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
for t in tabs:
    print(f"  {t}")

# 3. Индексы
print("\n=== Indexes (non-auto) ===")
idx = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
for i in idx:
    print(f"  {i}")

# 4. Счётчики
print("\n=== Counts ===")
for t in ("product", "observation", "price_point", "inbox_item", "intake_file", "source"):
    try:
        n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")
    except Exception as e:
        print(f"  {t}: ERROR {e}")

# 5. Продукты (если есть)
print("\n=== Products ===")
try:
    prods = conn.execute("SELECT id, source, listing_id, title, brand, model, origin FROM product ORDER BY id").fetchall()
    for p in prods:
        print(f"  #{p['id']} {p['source']}/{p['listing_id']} | {(p['title'] or '')[:50]} | {p['brand']} | {p['origin']}")
except Exception as e:
    print(f"  ERROR: {e}")

# 6. Inbox items
print("\n=== Inbox items ===")
try:
    items = conn.execute("SELECT id, filename, origin, status, error_code, observation_id FROM inbox_item ORDER BY id").fetchall()
    for i in items:
        print(f"  #{i['id']} {i['filename']} | {i['status']} | {i['origin']} | obs={i['observation_id']} | err={i['error_code']}")
except Exception as e:
    print(f"  ERROR: {e}")

conn.close()
print("\nDone.")
