r"""Быстрая проверка schema_migrations и _migration в r52.db."""
import sqlite3, sys, json
from pathlib import Path

BASE = Path(__file__).parent
DB = BASE / "r52" / "data" / "r52.db"

print(f"DB: {DB}", flush=True)
print(f"Exists: {DB.exists()}, size: {DB.stat().st_size if DB.exists() else 0}", flush=True)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# schema_migrations
try:
    rows = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    print(f"\nschema_migrations ({len(rows)} rows):", flush=True)
    for r in rows:
        d = dict(r)
        print(f"  v{d.get('version')} | {d.get('name','?')} | ck={str(d.get('checksum',''))[:20]}... | {d.get('applied_at','?')}", flush=True)
except Exception as e:
    print(f"schema_migrations ERROR: {e}", flush=True)

# _migration
try:
    rows2 = conn.execute("SELECT * FROM _migration ORDER BY rowid").fetchall()
    print(f"\n_migration ({len(rows2)} rows):", flush=True)
    for r in rows2:
        print(f"  {dict(r)}", flush=True)
except Exception as e:
    print(f"_migration ERROR: {e}", flush=True)

# product / observation / price_point
print("\n--- Product ---", flush=True)
for r in conn.execute("SELECT id, source, listing_id, title, brand, last_lowest_minor, last_currency, last_availability, observation_count, last_seen_at FROM product ORDER BY id").fetchall():
    d = dict(r)
    print(f"  P{d['id']} [{d['source']}] id={d['listing_id']} title={(d['title'] or '')[:50]} brand={d['brand']} price={d['last_lowest_minor']} {d['last_currency']} avail={d['last_availability']} obs={d['observation_count']} seen={d['last_seen_at']}", flush=True)

print("\n--- Observation ---", flush=True)
for r in conn.execute("SELECT id, product_id, origin, availability, fingerprint, observed_at FROM observation ORDER BY id").fetchall():
    d = dict(r)
    print(f"  O{d['id']} prod={d['product_id']} origin={d['origin']} avail={d['availability']} fp={str(d['fingerprint'] or '')[:16]}... at={d['observed_at']}", flush=True)

print("\n--- Price Point ---", flush=True)
for r in conn.execute("SELECT id, observation_id, role, amount_minor, currency, label FROM price_point ORDER BY id").fetchall():
    d = dict(r)
    print(f"  PP{d['id']} obs={d['observation_id']} role={d['role']} amt={d['amount_minor']} {d['currency']} label={d['label']}", flush=True)

# quick_check
qc = conn.execute("PRAGMA quick_check").fetchone()[0]
print(f"\nquick_check: {qc}", flush=True)

conn.close()
print("\nDONE", flush=True)
