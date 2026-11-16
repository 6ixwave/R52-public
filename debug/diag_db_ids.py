"""Запрос живых ID из auto_target + проверка БД."""
import sqlite3, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Найти БД
db_candidates = list(Path('data').rglob('*.db')) + list(Path('data').rglob('*.sqlite*'))
if not db_candidates:
    # Может быть в корне проекта
    db_candidates = list(ROOT.rglob('*.db')) + list(ROOT.rglob('*.sqlite*'))
    db_candidates = [p for p in db_candidates if 'node_modules' not in str(p)]

print(f"DB files found: {[str(p) for p in db_candidates[:10]]}")

if not db_candidates:
    print("NO DB FOUND")
    sys.exit(1)

db = db_candidates[0]
print(f"\nUsing: {db}")
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Версия схемы
try:
    cur.execute("SELECT * FROM pragma_user_version")
    ver = cur.fetchone()
    print(f"Schema version: {dict(ver) if ver else '?'}")
except:
    pass

# Таблицы
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(f"Tables ({len(tables)}): {tables}")

# auto_target
if 'auto_target' in tables:
    cur.execute("SELECT source, COUNT(*) as cnt FROM auto_target GROUP BY source ORDER BY source")
    print("\n=== auto_target by source ===")
    for r in cur.fetchall():
        print(f"  {r['source']}: {r['cnt']}")
    
    # Первые 5 ID для каждого источника
    for src in ['WB', 'OZON', 'AVITO', 'FARPOST', 'ALIEXPRESS', 'ALIEXPRESS_RU']:
        cur.execute("SELECT listing_id, url, title_hint, created_at FROM auto_target WHERE source=? ORDER BY created_at DESC LIMIT 5", (src,))
        rows = cur.fetchall()
        if rows:
            print(f"\n--- {src} (last 5) ---")
            for r in rows:
                url_short = (r['url'] or '?')[:90]
                hint = (r['title_hint'] or '')[:50]
                print(f"  id={r['listing_id']}  url={url_short}  hint={hint}")

# auto_source
if 'auto_source' in tables:
    cur.execute("SELECT * FROM auto_source LIMIT 20")
    rows = cur.fetchall()
    if rows:
        print(f"\n=== auto_source ({len(rows)} rows) ===")
        for r in rows:
            print(f"  {dict(r)}")

# settings
if 'settings' in tables:
    cur.execute("SELECT * FROM settings")
    rows = cur.fetchall()
    print(f"\n=== settings ({len(rows)} rows) ===")
    for r in rows:
        print(f"  {dict(r)}")

# import_runs
if 'import_runs' in tables:
    cur.execute("SELECT * FROM import_runs ORDER BY rowid DESC LIMIT 10")
    rows = cur.fetchall()
    print(f"\n=== import_runs (last 10) ===")
    for r in rows:
        d = dict(r)
        print(f"  {d}")

conn.close()
print("\nDone.")
