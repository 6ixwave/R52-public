"""Check isolated SQLite DBs for Ozon V3 and Farpost (fast5)."""
import sqlite3, json, os, sys

ROOT = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"

def check_db(label, db_path):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  DB: {db_path}")
    print(f"  Exists: {os.path.exists(db_path)}")
    if not os.path.exists(db_path):
        print("  SKIP: no file")
        return
    sz = os.path.getsize(db_path)
    print(f"  Size: {sz} bytes")
    if sz < 100:
        print("  SKIP: too small")
        return
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    # list tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"  Tables: {tables}")
    for t in ("product", "observation", "price_point"):
        if t not in tables:
            print(f"  [{t}] NOT FOUND")
            continue
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = cur.fetchone()[0]
        print(f"  [{t}] rows={cnt}")
        if cnt > 0:
            cur.execute(f"SELECT * FROM {t} LIMIT 5")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            for i, row in enumerate(rows):
                print(f"    row{i}: " + ", ".join(f"{c}={row[c]}" for c in cols if row[c] is not None and len(str(row[c])) < 120))
    con.close()

# --- Ozon V3 ---
check_db("OZON V3 (isolated)", os.path.join(ROOT, "e2e-ozon-avito-v3", "r52.sqlite3"))

# --- Farpost / fast5 ---
check_db("FAST5 (WB+Ozon+Farpost+Avito)", os.path.join(ROOT, "e2e-fast5", "r52.sqlite3"))

# --- Sample JSON from fast5 ---
print(f"\n{'='*60}")
print("  SAMPLE JSON FILES (fast5)")
print(f"{'='*60}")
fast5_dir = os.path.join(ROOT, "e2e-fast5")
for fname in sorted(os.listdir(fast5_dir)):
    if fname.startswith("fast5-FARPOST") and fname.endswith(".json"):
        fpath = os.path.join(fast5_dir, fname)
        sz = os.path.getsize(fpath)
        print(f"\n  {fname} ({sz} bytes)")
        if sz < 20000:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            # pretty print first 1500 chars
            try:
                obj = json.loads(data)
                print("  " + json.dumps(obj, ensure_ascii=False, indent=2)[:1500])
            except:
                print("  " + data[:1500])
        break  # just first one

# WB sample
for fname in sorted(os.listdir(fast5_dir)):
    if fname.startswith("fast5-WB") and fname.endswith(".json"):
        fpath = os.path.join(fast5_dir, fname)
        sz = os.path.getsize(fpath)
        print(f"\n  {fname} ({sz} bytes)")
        if sz < 20000:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            try:
                obj = json.loads(data)
                print("  " + json.dumps(obj, ensure_ascii=False, indent=2)[:1500])
            except:
                print("  " + data[:1500])
        break

# Ozon sample
for fname in sorted(os.listdir(fast5_dir)):
    if fname.startswith("fast5-OZON") and fname.endswith(".json"):
        fpath = os.path.join(fast5_dir, fname)
        sz = os.path.getsize(fpath)
        print(f"\n  {fname} ({sz} bytes)")
        if sz < 20000:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            try:
                obj = json.loads(data)
                print("  " + json.dumps(obj, ensure_ascii=False, indent=2)[:1500])
            except:
                print("  " + data[:1500])
        break

print("\nDONE")
