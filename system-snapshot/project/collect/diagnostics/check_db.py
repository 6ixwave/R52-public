"""Быстрая проверка содержимого r52.db: товары, наблюдения, входящие."""
import json
import os
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "r52", "data", "r52.db")

results = {}
log_lines = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line)

log(f"DB: {DB}")
if not os.path.exists(DB):
    log("ОШИБКА: база не найдена")
    results["error"] = "db_not_found"
    with open(os.path.join(os.path.dirname(__file__), "check_db.json"), "w", encoding="utf-8") as f:
        json.dump({"log": log_lines, **results}, f, ensure_ascii=False, indent=2)
    raise SystemExit(1)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. Таблицы
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
log(f"Таблицы ({len(tables)}): {tables}")
results["tables"] = tables

# 2. Товары
products = [dict(r) for r in conn.execute(
    "SELECT id, source, listing_id, title, brand, model, origin, "
    "last_regular_minor, last_lowest_minor, last_availability, observation_count, last_seen_at "
    "FROM product ORDER BY id"
).fetchall()]
log(f"Товаров: {len(products)}")
for p in products:
    log(f"  #{p['id']} [{p['source']}] {p['listing_id']} | {str(p['title'])[:60]} | "
        f"price={p['last_lowest_minor']} | obs={p['observation_count']}")
results["products"] = products

# 3. Наблюдения
obs = [dict(r) for r in conn.execute(
    "SELECT id, product_id, observed_at, origin, evidence_level, availability, fingerprint "
    "FROM observation ORDER BY id"
).fetchall()]
log(f"Наблюдений: {len(obs)}")
for o in obs:
    log(f"  obs#{o['id']} prod={o['product_id']} {o['observed_at']} {o['origin']} {o['evidence_level']} {o['availability']} fp={o['fingerprint'][:12]}...")
results["observations"] = obs

# 4. Точки цен
pp = [dict(r) for r in conn.execute(
    "SELECT id, observation_id, product_id, role, amount_minor, currency, label "
    "FROM price_point ORDER BY id"
).fetchall()]
log(f"Точек цен: {len(pp)}")
for p in pp:
    log(f"  pp#{p['id']} obs={p['observation_id']} {p['role']} {p['amount_minor']} {p['currency']} {str(p['label'])[:40]}")
results["price_points"] = pp

# 5. Входящие (inbox_item)
if "inbox_item" in tables:
    inbox = [dict(r) for r in conn.execute(
        "SELECT id, filename, origin, received_at, status, error_code "
        "FROM inbox_item ORDER BY id"
    ).fetchall()]
    log(f"Входящих: {len(inbox)}")
    for i in inbox:
        log(f"  inbox#{i['id']} {i['filename']} {i['origin']} {i['status']} {i['error_code'] or ''}")
    results["inbox"] = inbox
else:
    log("Таблица inbox_item отсутствует")
    results["inbox"] = []

# 6. Источники
if "source" in tables:
    sources = [dict(r) for r in conn.execute("SELECT * FROM source ORDER BY key").fetchall()]
    log(f"Источники: {sources}")
    results["sources"] = sources

# 7. Duplicates check
dup_products = conn.execute(
    "SELECT source, listing_id, COUNT(*) as cnt FROM product GROUP BY source, listing_id HAVING cnt > 1"
).fetchall()
log(f"Дубликаты товаров: {len(dup_products)}")
results["dup_products"] = [dict(r) for r in dup_products]

dup_obs = conn.execute(
    "SELECT fingerprint, COUNT(*) as cnt FROM observation GROUP BY fingerprint HAVING cnt > 1"
).fetchall()
log(f"Дубликаты наблюдений: {len(dup_obs)}")
results["dup_obs"] = [dict(r) for r in dup_obs]

conn.close()

# Итог
results["summary"] = {
    "products": len(products),
    "observations": len(obs),
    "price_points": len(pp),
    "inbox_items": len(results.get("inbox", [])),
    "dup_products": len(dup_products),
    "dup_obs": len(dup_obs),
}
log(f"ИТОГ: {json.dumps(results['summary'], ensure_ascii=False)}")

out = os.path.join(os.path.dirname(__file__), "check_db.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump({"log": log_lines, **results}, f, ensure_ascii=False, indent=2, default=str)
log(f"Результаты: {out}")
print("\nDONE")
