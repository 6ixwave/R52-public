"""diag_ozon_avito.py — точечная диагностика OZON/AVITO в e2e_test_home."""
import json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "e2e_test_home" / "data" / "r52.sqlite3"

if not DB.exists():
    print(f"DB not found: {DB}")
    sys.exit(1)

con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=" * 70)
print(f"DB: {DB}")
print("=" * 70)

# 1. All products
print("\n## ALL PRODUCTS")
rows = cur.execute("SELECT id, source, listing_id, title, observation_count FROM product ORDER BY id").fetchall()
for r in rows:
    print(f"  id={r['id']} [{r['source']}] listing={r['listing_id']} obs={r['observation_count']} title={r['title'][:50]}")
print(f"  Total: {len(rows)}")

# 2. All inbox items
print("\n## INBOX ITEMS")
rows = cur.execute("SELECT id, filename, origin, status, error_code, decided_at, observation_id FROM inbox_item ORDER BY id").fetchall()
for r in rows:
    print(f"  id={r['id']} file={r['filename']} origin={r['origin']} status={r['status']} err={r['error_code']} obs_id={r['observation_id']}")
print(f"  Total: {len(rows)}")

# 3. All observations with source from normalized_json
print("\n## OBSERVATIONS (source from normalized_json)")
rows = cur.execute("SELECT id, product_id, observed_at, origin, normalized_json FROM observation ORDER BY id").fetchall()
for r in rows:
    src = "?"
    title = "?"
    try:
        nj = json.loads(r["normalized_json"]) if r["normalized_json"] else {}
        src = nj.get("source", "?")
        title = (nj.get("title") or "?")[:40]
    except:
        pass
    print(f"  obs={r['id']} prod={r['product_id']} src={src} at={r['observed_at'][:19]} title={title}")
print(f"  Total: {len(rows)}")

# 4. Price points
print("\n## PRICE POINTS (last 10)")
rows = cur.execute(
    "SELECT pp.id, pp.product_id, pp.role, pp.amount_minor, pp.currency, pp.label, o.observed_at "
    "FROM price_point pp JOIN observation o ON o.id=pp.observation_id ORDER BY pp.id DESC LIMIT 10"
).fetchall()
for r in rows:
    print(f"  pp={r['id']} prod={r['product_id']} role={r['role']} amt={r['amount_minor']} {r['currency']} at={r['observed_at'][:19]}")

# 5. Source summary
print("\n## SOURCE SUMMARY (from observations)")
src_counts = {}
for r in cur.execute("SELECT normalized_json FROM observation"):
    try:
        nj = json.loads(r[0]) if r[0] else {}
        s = nj.get("source", "UNKNOWN")
        src_counts[s] = src_counts.get(s, 0) + 1
    except:
        src_counts["PARSE_ERR"] = src_counts.get("PARSE_ERR", 0) + 1
for s, c in sorted(src_counts.items()):
    print(f"  {s:12s} → {c} obs")

# 6. Check: are there OZON/AVITO products?
print("\n## OZON/AVITO PRODUCT CHECK")
for src in ("OZON", "AVITO"):
    rows = cur.execute("SELECT * FROM product WHERE source=?", (src,)).fetchall()
    print(f"  {src}: {len(rows)} products")
    for r in rows:
        print(f"    id={r['id']} listing={r['listing_id']} title={r['title'][:50]}")

# 7. Check: OZON/AVITO observations linked to which products?
print("\n## OZON/AVITO OBSERVATIONS → PRODUCT MAPPING")
for src in ("OZON", "AVITO"):
    rows = cur.execute(
        "SELECT o.id, o.product_id, p.source as p_source, p.listing_id as p_listing, p.title as p_title "
        "FROM observation o LEFT JOIN product p ON p.id=o.product_id "
        "WHERE o.normalized_json LIKE ?", (f'%"{src}"%',)
    ).fetchall()
    print(f"  {src}: {len(rows)} obs")
    for r in rows:
        print(f"    obs={r['id']} → prod={r['product_id']} [{r['p_source']}] listing={r['p_listing']} title={str(r['p_title'])[:40]}")

# 8. Inbox detail for OZON/AVITO files
print("\n## INBOX DETAIL (OZON/AVITO files)")
for pattern in ("%ozon%", "%avito%"):
    rows = cur.execute(
        "SELECT id, filename, status, error_code, payload_json, preview_json, decided_at "
        "FROM inbox_item WHERE filename LIKE ? ORDER BY id", (pattern,)
    ).fetchall()
    for r in rows:
        print(f"  id={r['id']} file={r['filename']} status={r['status']} err={r['error_code']}")
        if r["preview_json"]:
            try:
                pv = json.loads(r["preview_json"])
                print(f"    preview: src={pv.get('source')} listing={pv.get('listing_id')} title={str(pv.get('title',''))[:40]}")
            except:
                pass
        if r["payload_json"] and r["status"] == "PENDING":
            try:
                pl = json.loads(r["payload_json"])
                obs = pl.get("observation", {})
                print(f"    payload obs: src={obs.get('source')} id={obs.get('id')} url={str(obs.get('url',''))[:60]}")
                prices = obs.get("price_candidates", [])
                print(f"    payload prices: {len(prices)}")
            except:
                pass

con.close()
print("\nDIAG_OZON_AVITO_DONE")
