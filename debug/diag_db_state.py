"""Проверка состояния тестовой БД R-52."""
import sqlite3
import sys
from pathlib import Path

db_path = Path(__file__).parent.parent / "data" / "test_home" / "data" / "r52.sqlite3"
if not db_path.exists():
    print(f"DB NOT FOUND: {db_path}")
    sys.exit(1)

c = sqlite3.connect(str(db_path))
c.row_factory = sqlite3.Row

print(f"=== DB: {db_path} ===")
print(f"user_version: {c.execute('PRAGMA user_version').fetchone()[0]}")

print("\n--- Tables ---")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(f"  {r[0]}")

print("\n--- schema_migrations ---")
try:
    for r in c.execute("SELECT * FROM schema_migrations ORDER BY version"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- auto_source ---")
try:
    for r in c.execute("SELECT * FROM auto_source"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- auto_target (count + sample) ---")
try:
    cnt = c.execute("SELECT count(*) FROM auto_target").fetchone()[0]
    print(f"  count: {cnt}")
    for r in c.execute("SELECT * FROM auto_target LIMIT 10"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- inbox_item (count + last 5) ---")
try:
    cnt = c.execute("SELECT count(*) FROM inbox_item").fetchone()[0]
    print(f"  count: {cnt}")
    for r in c.execute("SELECT id, filename, origin, status, error_code, received_at FROM inbox_item ORDER BY id DESC LIMIT 5"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- product (count + last 5) ---")
try:
    cnt = c.execute("SELECT count(*) FROM product").fetchone()[0]
    print(f"  count: {cnt}")
    for r in c.execute("SELECT id, source, listing_id, title, url, created_at FROM product ORDER BY id DESC LIMIT 5"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- auto_delivery ---")
try:
    for r in c.execute("SELECT * FROM auto_delivery LIMIT 10"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- intake_file ---")
try:
    for r in c.execute("SELECT * FROM intake_file LIMIT 10"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- observation (count + last 3) ---")
try:
    cnt = c.execute("SELECT count(*) FROM observation").fetchone()[0]
    print(f"  count: {cnt}")
    for r in c.execute("SELECT id, product_id, observed_at, availability, imported_at FROM observation ORDER BY id DESC LIMIT 3"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n--- task (last 5) ---")
try:
    for r in c.execute("SELECT id, type, status, created_at, finished_at FROM task ORDER BY id DESC LIMIT 5"):
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  ERROR: {e}")

c.close()
print("\n=== DONE ===")
