"""Избранное и группы сравнения."""

import json

from ..domain import compare
from ..errors import AppError
from .products import card, get_product_row

MAX_GROUP = 12
PRODUCT_SELECT = (
    "SELECT p.*, f.product_id IS NOT NULL AS favorite, (w.id IS NOT NULL AND w.active = 1) AS watched "
    "FROM product p LEFT JOIN favorite f ON f.product_id = p.id LEFT JOIN watch w ON w.product_id = p.id"
)


def set_favorite(conn, product_id, on, note, now):
    get_product_row(conn, product_id)
    if on:
        conn.execute(
            "INSERT INTO favorite(product_id, created_at, note) VALUES(?, ?, ?) "
            "ON CONFLICT(product_id) DO UPDATE SET note = excluded.note",
            (product_id, now, note),
        )
    else:
        conn.execute("DELETE FROM favorite WHERE product_id = ?", (product_id,))
    return on


def list_favorites(conn, limit=200):
    rows = conn.execute(
        "SELECT p.*, 1 AS favorite, (w.id IS NOT NULL AND w.active = 1) AS watched, f.note AS favorite_note "
        "FROM favorite f JOIN product p ON p.id = f.product_id LEFT JOIN watch w ON w.product_id = p.id "
        "ORDER BY f.created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        item = card(row)
        item["note"] = row["favorite_note"]
        out.append(item)
    return out


def _group(conn, group_id):
    row = conn.execute("SELECT * FROM compare_group WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise AppError("GROUP_NOT_FOUND", status=404)
    return row


def create_group(conn, name, product_ids, now):
    if len(product_ids) > MAX_GROUP:
        raise AppError("INVALID_INPUT", detail={"field": "product_ids", "reason": f"max_{MAX_GROUP}"})
    for product_id in product_ids:
        get_product_row(conn, product_id)
    group_id = conn.execute("INSERT INTO compare_group(name, created_at) VALUES(?, ?)", (name, now)).lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO compare_item(group_id, product_id, added_at) VALUES(?, ?, ?)",
        [(group_id, product_id, now) for product_id in product_ids],
    )
    return group_id


def list_groups(conn):
    rows = conn.execute(
        "SELECT g.id, g.name, g.created_at, count(i.product_id) AS size FROM compare_group g "
        "LEFT JOIN compare_item i ON i.group_id = g.id GROUP BY g.id ORDER BY g.id DESC").fetchall()
    return [dict(row) for row in rows]


def add_item(conn, group_id, product_id, now):
    _group(conn, group_id)
    get_product_row(conn, product_id)
    exists = conn.execute(
        "SELECT 1 FROM compare_item WHERE group_id = ? AND product_id = ?", (group_id, product_id)).fetchone()
    size = conn.execute("SELECT count(*) FROM compare_item WHERE group_id = ?", (group_id,)).fetchone()[0]
    if not exists and size >= MAX_GROUP:
        raise AppError("INVALID_INPUT", detail={"field": "product_id", "reason": f"max_{MAX_GROUP}"})
    conn.execute(
        "INSERT OR IGNORE INTO compare_item(group_id, product_id, added_at) VALUES(?, ?, ?)",
        (group_id, product_id, now),
    )


def remove_item(conn, group_id, product_id):
    _group(conn, group_id)
    conn.execute("DELETE FROM compare_item WHERE group_id = ? AND product_id = ?", (group_id, product_id))


def delete_group(conn, group_id):
    _group(conn, group_id)
    conn.execute("DELETE FROM compare_group WHERE id = ?", (group_id,))


def _comparison(conn, rows):
    products, cards = [], []
    for row in rows:
        item = card(row)
        latest = None
        if row["last_observation_id"]:
            stored = conn.execute(
                "SELECT normalized_json FROM observation WHERE id = ?", (row["last_observation_id"],)).fetchone()
            latest = json.loads(stored["normalized_json"]) if stored else None
        products.append({
            "id": item["id"], "title": item["title"], "source": item["source"], "url": item["url"],
            "image_url":item.get("image_url"), "media_url":item.get("media_url"), "summary": item, "latest": latest, "availability": item["availability"],
            "last_seen_at": item["last_seen_at"],
        })
        cards.append(item)
    return {"table": compare.table(products), "products": cards}


def group_detail(conn, group_id):
    group = _group(conn, group_id)
    rows = conn.execute(
        PRODUCT_SELECT + " JOIN compare_item i ON i.product_id = p.id WHERE i.group_id = ? ORDER BY i.added_at, p.id",
        (group_id,)).fetchall()
    return {"group": dict(group), **_comparison(conn, rows)}


def compare_products(conn, product_ids):
    rows = []
    for product_id in product_ids:
        row = conn.execute(PRODUCT_SELECT + " WHERE p.id = ?", (product_id,)).fetchone()
        if row is None:
            raise AppError("PRODUCT_NOT_FOUND", status=404)
        rows.append(row)
    return _comparison(conn, rows)
