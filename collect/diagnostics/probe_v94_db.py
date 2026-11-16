"""Быстрая проверка SQLite: счётчики, схемы, последние записи."""
import sqlite3, json, sys

DB = r"r52\data\r52.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

tables = ["product", "observation", "price_point", "source", "inbox_item", "intake_file"]

print("=== COUNTS ===")
for t in tables:
    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n}")

print("\n=== SCHEMAS ===")
for t in tables:
    cols = c.execute(f"PRAGMA table_info({t})").fetchall()
    print(f"  {t}: {[dict(r) for r in cols]}")

print("\n=== PRODUCTS (all) ===")
for r in c.execute("SELECT * FROM product ORDER BY id"):
    d = dict(r)
    # shorten long fields
    for k in ("title", "description", "url"):
        if k in d and d[k] and len(str(d[k])) > 80:
            d[k] = str(d[k])[:80] + "..."
    print(f"  {d}")

print("\n=== OBSERVATIONS (last 10) ===")
for r in c.execute("SELECT * FROM observation ORDER BY id DESC LIMIT 10"):
    d = dict(r)
    for k in ("raw_json",):
        if k in d and d[k] and len(str(d[k])) > 100:
            d[k] = str(d[k])[:100] + "..."
    print(f"  {d}")

print("\n=== PRICE_POINTS (last 10) ===")
for r in c.execute("SELECT * FROM price_point ORDER BY id DESC LIMIT 10"):
    print(f"  {dict(r)}")

print("\n=== SOURCES ===")
for r in c.execute("SELECT * FROM source ORDER BY id"):
    print(f"  {dict(r)}")

print("\n=== INBOX_ITEMS (last 5) ===")
for r in c.execute("SELECT * FROM inbox_item ORDER BY id DESC LIMIT 5"):
    d = dict(r)
    for k in ("payload", "raw"):
        if k in d and d[k] and len(str(d[k])) > 100:
            d[k] = str(d[k])[:100] + "..."
    print(f"  {d}")

c.close()
print("\nDONE")
