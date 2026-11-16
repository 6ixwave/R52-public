"""Быстрая проверка состояния постоянной БД R52."""
import sqlite3
import os
import sys

DB = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12\r52\data\r52.db"

if not os.path.exists(DB):
    print(f"DB NOT FOUND: {DB}")
    sys.exit(1)

conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(f"DB: {DB}")
print(f"Tables ({len(tables)}): {', '.join(tables)}")
print()

# Ключевые таблицы
key_tables = ["source", "product", "observation", "price_point",
              "product_media", "product_gallery", "product_field",
              "observation_field", "intake_log", "migration"]
for t in key_tables:
    if t in tables:
        cur.execute(f"SELECT COUNT(*) FROM [{t}]")
        cnt = cur.fetchone()[0]
        print(f"  {t:25s} {cnt:6d} rows")
        if cnt > 0 and cnt <= 5:
            cur.execute(f"SELECT * FROM [{t}] LIMIT 3")
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                print(f"    {dict(zip(cols, row))}")
    else:
        print(f"  {t:25s} (not found)")

# Последние записи в product
if "product" in tables:
    cur.execute("SELECT COUNT(*) FROM product")
    pc = cur.fetchone()[0]
    if pc > 0:
        print(f"\nLast 3 products:")
        cur.execute("SELECT id, source, listing_id, title, observed_at FROM product ORDER BY id DESC LIMIT 3")
        for row in cur.fetchall():
            print(f"  {row}")

conn.close()
print("\nDONE")
