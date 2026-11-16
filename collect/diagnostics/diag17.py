"""diag17 — полная диагностика SQLite: схема + данные по 4 площадкам."""
import sqlite3, json, sys, os

DB = r"C:\Users\aleks\AppData\Local\R52\data\r52.sqlite3"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag17_result.json")

result = {"schema": {}, "counts": {}, "samples": {}, "errors": []}

try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # --- 1. Схема всех таблиц ---
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    result["tables"] = tables

    for t in tables:
        cols = cur.execute(f"PRAGMA table_info({t})").fetchall()
        result["schema"][t] = [{"name": c["name"], "type": c["type"], "pk": c["pk"]} for c in cols]

    # --- 2. Счётчики по таблицам ---
    for t in tables:
        cnt = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        result["counts"][t] = cnt

    # --- 3. product: группировка по source / platform ---
    prod_cols = [c["name"] for c in result["schema"].get("product", [])]
    print(f"[schema] product columns: {prod_cols}")

    # Найти колонку-источник
    src_col = None
    for c in prod_cols:
        if c.lower() in ("source", "platform", "marketplace", "origin"):
            src_col = c
            break
    if src_col:
        rows = cur.execute(f"SELECT {src_col}, COUNT(*) as cnt FROM product GROUP BY {src_col}").fetchall()
        result["product_by_source"] = {r[0]: r[1] for r in rows}
    else:
        result["product_by_source"] = "NO_SOURCE_COL"
        result["errors"].append(f"product: no source/platform column found in {prod_cols}")

    # --- 4. observation: схема и данные ---
    obs_cols = [c["name"] for c in result["schema"].get("observation", [])]
    print(f"[schema] observation columns: {obs_cols}")

    if "product_id" in obs_cols:
        # JOIN с product чтобы получить source
        join_src = ""
        if src_col:
            join_src = f"p.{src_col} as src"
        q = f"""
            SELECT {join_src}, COUNT(*) as cnt
            FROM observation o
            LEFT JOIN product p ON o.product_id = p.id
            GROUP BY {src_col if src_col else 'o.product_id'}
        """
        if src_col:
            rows = cur.execute(q).fetchall()
            result["observation_by_source"] = {r[0]: r[1] for r in rows}
        else:
            rows = cur.execute("SELECT product_id, COUNT(*) as cnt FROM observation GROUP BY product_id LIMIT 20").fetchall()
            result["observation_by_product"] = {str(r[0]): r[1] for r in rows}

    # --- 5. price_point: схема и данные ---
    pp_cols = [c["name"] for c in result["schema"].get("price_point", [])]
    print(f"[schema] price_point columns: {pp_cols}")

    if pp_cols:
        # Найти связь с observation или product
        link_col = None
        for c in pp_cols:
            if c in ("observation_id", "product_id"):
                link_col = c
                break
        if link_col == "observation_id":
            q = f"""
                SELECT {src_col if src_col else 'p.id'} as src, COUNT(*) as cnt
                FROM price_point pp
                JOIN observation o ON pp.observation_id = o.id
                JOIN product p ON o.product_id = p.id
                GROUP BY {src_col if src_col else 'p.id'}
            """
        elif link_col == "product_id":
            q = f"""
                SELECT p.{src_col} as src, COUNT(*) as cnt
                FROM price_point pp
                JOIN product p ON pp.product_id = p.id
                GROUP BY p.{src_col}
            """ if src_col else """
                SELECT pp.product_id, COUNT(*) as cnt
                FROM price_point pp GROUP BY pp.product_id LIMIT 20
            """
        else:
            q = f"SELECT COUNT(*) FROM price_point"
            result["price_point_total"] = cur.execute(q).fetchone()[0]

        if src_col and link_col:
            rows = cur.execute(q).fetchall()
            result["price_point_by_source"] = {str(r[0]): r[1] for r in rows}

    # --- 6. Образцы: по 3 записи на площадку ---
    for platform in ("ozon", "farpost", "wildberries", "avito", "wb", "OZON", "FARPOST", "WILDBERRIES", "AVITO"):
        if not src_col:
            break
        rows = cur.execute(
            f"SELECT * FROM product WHERE {src_col}=? LIMIT 3", (platform,)
        ).fetchall()
        if rows:
            result["samples"][f"product_{platform}"] = [dict(r) for r in rows]

    # --- 7. Последние observation (10 штук) ---
    if obs_cols:
        order_col = "id" if "id" in obs_cols else obs_cols[0]
        rows = cur.execute(f"SELECT * FROM observation ORDER BY {order_col} DESC LIMIT 10").fetchall()
        result["recent_observations"] = [dict(r) for r in rows]

    # --- 8. Последние price_point (10 штук) ---
    if pp_cols:
        order_col = "id" if "id" in pp_cols else pp_cols[0]
        rows = cur.execute(f"SELECT * FROM price_point ORDER BY {order_col} DESC LIMIT 10").fetchall()
        result["recent_price_points"] = [dict(r) for r in rows]

    conn.close()

except Exception as e:
    result["errors"].append(f"DB_ERROR: {type(e).__name__}: {e}")
    import traceback
    result["errors"].append(traceback.format_exc())

# --- Печать в stdout ---
print(f"\n=== TABLES: {result.get('tables', [])}")
print(f"=== COUNTS: {json.dumps(result.get('counts', {}), ensure_ascii=False)}")
print(f"=== PRODUCT BY SOURCE: {json.dumps(result.get('product_by_source', {}), ensure_ascii=False)}")
print(f"=== OBS BY SOURCE: {json.dumps(result.get('observation_by_source', result.get('observation_by_product', {})), ensure_ascii=False)}")
print(f"=== PP BY SOURCE: {json.dumps(result.get('price_point_by_source', {}), ensure_ascii=False)}")
print(f"=== ERRORS: {result.get('errors', [])}")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\nResult → {OUT}")
