"""Диагностика РЕАЛЬНОЙ БД + полный E2E-тест intake."""
import sqlite3, os, sys, json, hashlib
from pathlib import Path

HOME = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\e2e_test_home")
DB = HOME / "data" / "r52.sqlite3"

print(f"=== REAL DB: {DB} ===")
print(f"exists={DB.exists()} size={DB.stat().st_size if DB.exists() else 0}")

c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row

# Schema migrations
sm = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
if sm:
    rows = c.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    print(f"\nschema_migrations: {len(rows)} applied")
    for r in rows:
        print(f"  v{r['version']} {r['name']} @ {r['applied_at']}")
else:
    print("\nschema_migrations: NOT FOUND")

# All tables
tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print(f"\nTables ({len(tabs)}): {tabs}")

# Data in key tables
for t in ["source", "product", "observation", "price_point", "inbox_item", "intake_file"]:
    if t in tabs:
        cnt = c.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        print(f"\n--- {t} [{cnt} rows] ---")
        if cnt > 0:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info([{t}])")]
            print(f"  cols: {cols}")
            for r in c.execute(f"SELECT * FROM [{t}] LIMIT 15").fetchall():
                d = dict(r)
                for k in list(d.keys()):
                    if isinstance(d[k], str) and len(d[k]) > 80:
                        d[k] = d[k][:80] + "..."
                print(f"  {d}")

# Settings
if "setting" in tabs:
    rows = c.execute("SELECT * FROM setting").fetchall()
    print(f"\n--- setting [{len(rows)}] ---")
    for r in rows:
        print(f"  {dict(r)}")

c.close()
print("\n=== DB DONE ===")
