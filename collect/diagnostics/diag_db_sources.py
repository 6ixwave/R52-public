"""diag_db_sources.py — точная проверка записей по каждому источнику."""
import sqlite3, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
db = ROOT / "e2e_test_home" / "data" / "r52.sqlite3"

if not db.exists():
    found = list((ROOT / "e2e_test_home").rglob("*.sqlite3"))
    if found:
        db = found[0]
    else:
        print("DB NOT FOUND"); sys.exit(1)

con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row
cur = con.cursor()

print(f"DB: {db}")
print("=" * 70)

# 1. Products by source
print("\n## PRODUCTS BY SOURCE")
rows = cur.execute("SELECT source, COUNT(*) as cnt FROM product GROUP BY source").fetchall()
for r in rows:
    print(f"  {r['source']:10s} → {r['cnt']}")

# 2. Observations by product source
print("\n## OBSERVATIONS BY SOURCE")
rows = cur.execute("""
    SELECT p.source, COUNT(*) as cnt, MAX(o.observed_at) as last_obs
    FROM observation o JOIN product p ON p.id = o.product_id
    GROUP BY p.source
""").fetchall()
for r in rows:
    print(f"  {r['source']:10s} → {r['cnt']} obs, last={r['last_obs']}")

# 3. Price points by product source
print("\n## PRICE POINTS BY SOURCE")
rows = cur.execute("""
    SELECT p.source, COUNT(*) as cnt, MAX(pp.observed_at) as last_pp
    FROM price_point pp JOIN product p ON p.id = pp.product_id
    GROUP BY p.source
""").fetchall()
for r in rows:
    print(f"  {r['source']:10s} → {r['cnt']} pp, last={r['last_pp']}")

# 4. Detailed: for each product, show last obs and pp
print("\n## PER-PRODUCT DETAIL")
prods = cur.execute("SELECT * FROM product ORDER BY id").fetchall()
for p in prods:
    print(f"\n  Product id={p['id']} [{p['source']}] listing={p['listing_id']}")
    print(f"    title: {p['title'][:60]}")
    print(f"    obs_count={p['observation_count']} last_seen={p['last_seen_at']}")
    print(f"    last_regular={p['last_regular_minor']} last_avail={p['last_availability']}")
    
    # observations
    obs = cur.execute(
        "SELECT id, observed_at, availability FROM observation WHERE product_id=? ORDER BY id DESC LIMIT 3",
        (p['id'],)
    ).fetchall()
    print(f"    last obs: {[dict(o) for o in obs]}")
    
    # price points
    pp = cur.execute(
        "SELECT id, role, amount_minor, currency, observed_at FROM price_point WHERE product_id=? ORDER BY id DESC LIMIT 3",
        (p['id'],)
    ).fetchall()
    print(f"    last pp:  {[dict(o) for o in pp]}")

# 5. Check: do OZON/AVITO products exist?
print("\n## OZON / AVITO PRODUCT CHECK")
for src in ("OZON", "AVITO"):
    rows = cur.execute("SELECT id, listing_id, title FROM product WHERE source=?", (src,)).fetchall()
    if rows:
        for r in rows:
            print(f"  {src}: id={r['id']} listing={r['listing_id']} title={r['title'][:50]}")
    else:
        print(f"  {src}: NO PRODUCT RECORD")

# 6. Check observations for OZON/AVITO (might be linked to wrong product)
print("\n## OBS CHECK: source in normalized_json")
obs_all = cur.execute("SELECT id, product_id, observed_at, normalized_json FROM observation ORDER BY id").fetchall()
for o in obs_all:
    nj = o['normalized_json']
    if nj:
        try:
            data = json.loads(nj)
            src = data.get('source', '?')
        except:
            src = '?'
    else:
        src = '?'
    print(f"  obs {o['id']:2d} prod={o['product_id']} src={src:10s} at={o['observed_at']}")

# 7. Fingerprint dedup check
print("\n## FINGERPRINT DUPLICATES")
rows = cur.execute("""
    SELECT fingerprint, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
    FROM observation GROUP BY fingerprint HAVING cnt > 1
""").fetchall()
if rows:
    for r in rows:
        print(f"  fp={r['fingerprint'][:20]}... count={r['cnt']} ids={r['ids']}")
else:
    print("  No duplicates")

con.close()
print("\nDIAG_DB_SOURCES_DONE")
