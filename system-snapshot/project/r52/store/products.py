"""Товары, наблюдения и точки цен.

Исходные наблюдения не изменяются. Сводка в строке товара (last_*) всегда
пересчитывается из наблюдений и нужна только для быстрого списка.
"""

import hashlib
import json
import re
import uuid

from ..domain import pricing
from ..domain.compare import diff
from ..domain.normalize import identity_from_url, manual_normalized, plain_url
from ..errors import AppError

SORTS = {
    "recent": "p.last_seen_at IS NULL, p.last_seen_at DESC, p.id DESC",
    "price": "p.last_lowest_minor IS NULL, p.last_lowest_minor, p.id",
    "title": "p.title COLLATE NOCASE, p.id",
    "added": "p.id DESC",
}
_LATEST = "ORDER BY observed_at IS NULL, observed_at DESC, id DESC"


def search_text(*parts):
    return " ".join(str(part) for part in parts if part).casefold().replace("ё", "е")


def tokens(query):
    if not query:
        return []
    text = str(query).casefold().replace("ё", "е")
    return [token for token in re.split(r"[^\w./-]+", text) if token][:8]


def _like(token):
    return "%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def ensure_source(conn, key, name=None, kind="retail"):
    conn.execute("INSERT OR IGNORE INTO source(key, name, kind) VALUES(?, ?, ?)", (key, name or key, kind))


def upsert_product(conn, *, source, listing_id, title, url, brand, model, origin, now, overwrite=True):
    row = conn.execute("SELECT * FROM product WHERE source = ? AND listing_id = ?", (source, listing_id)).fetchone()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO product(source, listing_id, url, title, brand, model, origin, search_text, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source, listing_id, url, title, brand, model, origin, search_text(title, brand, model, listing_id), now, now),
        )
        return cursor.lastrowid, True
    if overwrite:
        title = title or row["title"]
        url = url or row["url"]
        brand = brand or row["brand"]
        model = model or row["model"]
        conn.execute(
            "UPDATE product SET url = ?, title = ?, brand = ?, model = ?, search_text = ?, updated_at = ? WHERE id = ?",
            (url, title, brand, model, search_text(title, brand, model, listing_id), now, row["id"]),
        )
    return row["id"], False


def add_observation(conn, *, product_id, normalized, raw, origin, evidence_level, fingerprint,
                    observed_at, availability, prices, now):
    """Возвращает (id, created). Повтор с тем же отпечатком не создаёт новой записи."""
    cursor = conn.execute(
        "INSERT INTO observation(product_id, observed_at, imported_at, origin, evidence_level, availability, "
        "fingerprint, normalized_json, raw_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(fingerprint) DO NOTHING",
        (
            product_id, observed_at, now, origin, evidence_level, availability, fingerprint,
            json.dumps(normalized, ensure_ascii=False),
            None if raw is None else json.dumps(raw, ensure_ascii=False),
        ),
    )
    if cursor.rowcount == 0:
        row = conn.execute("SELECT id FROM observation WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return row["id"], False
    observation_id = cursor.lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO price_point(observation_id, product_id, role, amount_minor, currency, label, observed_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        [(observation_id, product_id, item["role"], item["amount_minor"], item["currency"], item.get("label"), observed_at)
         for item in prices],
    )
    return observation_id, True


def observation_summary(conn, observation_id):
    points = [dict(row) for row in conn.execute(
        "SELECT role, amount_minor, currency FROM price_point WHERE observation_id = ?", (observation_id,))]
    return pricing.summarize(points)


def refresh_summary(conn, product_id):
    latest = conn.execute(
        f"SELECT id, observed_at, availability FROM observation WHERE product_id = ? {_LATEST} LIMIT 1", (product_id,)
    ).fetchone()
    count = conn.execute("SELECT count(*) FROM observation WHERE product_id = ?", (product_id,)).fetchone()[0]
    if latest is None:
        conn.execute(
            "UPDATE product SET last_observation_id = NULL, last_seen_at = NULL, last_currency = NULL, "
            "last_regular_minor = NULL, last_lowest_minor = NULL, last_lowest_role = NULL, "
            "last_availability = 'UNKNOWN', observation_count = 0 WHERE id = ?", (product_id,))
        return
    normalized = load_normalized(conn, latest["id"]) or {}
    media = normalized.get("media") or {}
    if media.get("items"):
        conn.execute("UPDATE product SET gallery_json=? WHERE id=?",(json.dumps(media['items'],ensure_ascii=False),product_id))
    if media.get("image_data"):
        conn.execute("UPDATE product SET image_data=?, image_source_url=?, image_observed_at=? WHERE id=?",
                     (media["image_data"],media.get("source_url"),latest["observed_at"],product_id))
    if normalized.get("category_path"):
        conn.execute("UPDATE product SET category_path=? WHERE id=?",(normalized["category_path"],product_id))
    summary = observation_summary(conn, latest["id"])
    conn.execute(
        "UPDATE product SET last_observation_id = ?, last_seen_at = ?, last_currency = ?, last_regular_minor = ?, "
        "last_lowest_minor = ?, last_lowest_role = ?, last_availability = ?, observation_count = ? WHERE id = ?",
        (latest["id"], latest["observed_at"], summary["currency"], summary["regular_minor"], summary["lowest_minor"],
         summary["lowest_role"], latest["availability"], count, product_id),
    )


def card(row):
    data = dict(row)
    summary = {
        "currency": data.get("last_currency"),
        "regular_minor": data.get("last_regular_minor"),
        "lowest_minor": data.get("last_lowest_minor"),
        "lowest_role": data.get("last_lowest_role"),
    }
    role = summary["lowest_role"]
    return {
        "media_url": f"/api/products/{data['id']}/media",
        "photo_count": sum(i.get('type')=='image' for i in json.loads(data.get('gallery_json') or '[]')) or int(bool(data.get('image_data'))),
        "video_count": sum(i.get('type')=='video' for i in json.loads(data.get('gallery_json') or '[]')),
        "image_url": f"/api/products/{data['id']}/image" if data.get("image_data") or data.get('gallery_json') else None,
        "image_observed_at": data.get("image_observed_at"),
        "category_path": data.get("category_path"),
        "id": data["id"],
        "source": data["source"],
        "listing_id": data["listing_id"],
        "title": data["title"],
        "url": data["url"],
        "brand": data["brand"],
        "model": data["model"],
        "origin": data["origin"],
        "last_seen_at": data["last_seen_at"],
        "availability": data["last_availability"],
        "observation_count": data["observation_count"],
        **summary,
        "conditional": bool(role) and role in pricing.CONDITIONAL,
        "price_text": pricing.price_line(summary),
        "favorite": bool(data.get("favorite")),
        "watched": bool(data.get("watched")),
    }


def get_product_row(conn, product_id):
    row = conn.execute("SELECT * FROM product WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise AppError("PRODUCT_NOT_FOUND", status=404)
    return row


def list_products(conn, query=None, source=None, only=None, sort="recent", limit=50, offset=0):
    where, params = [], []
    for token in tokens(query):
        where.append("p.search_text LIKE ? ESCAPE '\\'")
        params.append(_like(token))
    if source:
        where.append("p.source = ?")
        params.append(source)
    if only == "favorites":
        where.append("f.product_id IS NOT NULL")
    elif only == "watched":
        where.append("w.id IS NOT NULL AND w.active = 1")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    base = ("FROM product p LEFT JOIN favorite f ON f.product_id = p.id "
            f"LEFT JOIN watch w ON w.product_id = p.id {clause}")
    total = conn.execute(f"SELECT count(*) {base}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT p.*, f.product_id IS NOT NULL AS favorite, (w.id IS NOT NULL AND w.active = 1) AS watched "
        f"{base} ORDER BY {SORTS.get(sort, SORTS['recent'])} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"total": total, "items": [card(row) for row in rows]}


def load_normalized(conn, observation_id):
    row = conn.execute("SELECT normalized_json FROM observation WHERE id = ?", (observation_id,)).fetchone()
    return json.loads(row["normalized_json"]) if row else None


def product_detail(conn, product_id):
    row = conn.execute(
        "SELECT p.*, f.product_id IS NOT NULL AS favorite, (w.id IS NOT NULL AND w.active = 1) AS watched "
        "FROM product p LEFT JOIN favorite f ON f.product_id = p.id LEFT JOIN watch w ON w.product_id = p.id "
        "WHERE p.id = ?", (product_id,)).fetchone()
    if row is None:
        raise AppError("PRODUCT_NOT_FOUND", status=404)
    latest = load_normalized(conn, row["last_observation_id"]) if row["last_observation_id"] else None
    points = [dict(item) for item in conn.execute(
        "SELECT pp.observation_id, o.observed_at, pp.role, pp.amount_minor, pp.currency "
        "FROM price_point pp JOIN observation o ON o.id = pp.observation_id WHERE pp.product_id = ? "
        "ORDER BY o.observed_at IS NULL, o.observed_at, o.id LIMIT 5000", (product_id,))]
    series, undated = pricing.history(points)
    observations = [dict(item) for item in conn.execute(
        "SELECT id, observed_at, imported_at, origin, evidence_level, availability FROM observation "
        f"WHERE product_id = ? {_LATEST} LIMIT 30", (product_id,))]
    dated = [item for item in observations if item["observed_at"]]
    changes = None
    if len(dated) >= 2:
        changes = diff(load_normalized(conn, dated[1]["id"]), load_normalized(conn, dated[0]["id"]))
    watch = conn.execute("SELECT * FROM watch WHERE product_id = ?", (product_id,)).fetchone()
    groups = [dict(item) for item in conn.execute(
        "SELECT g.id, g.name FROM compare_item i JOIN compare_group g ON g.id = i.group_id WHERE i.product_id = ?",
        (product_id,))]
    return {
        "product": card(row),
        "latest": latest,
        "history": series[-500:],
        "history_undated": undated,
        "stats": {"lowest": pricing.stats(series, "lowest_minor"), "regular": pricing.stats(series, "regular_minor")},
        "hint": pricing.deal_hint(series),
        "observations": observations,
        "changes": changes,
        "watch": watch_dict(watch),
        "groups": groups,
    }


def watch_dict(row):
    if row is None:
        return None
    data = dict(row)
    data["active"] = bool(data["active"])
    data["notify_availability"] = bool(data["notify_availability"])
    return data


def create_manual_product(conn, title, url, now):
    identity = identity_from_url(url) if url else None
    if identity:
        source, listing_id, canonical = identity
    else:
        source, listing_id = "MANUAL", "manual-" + uuid.uuid4().hex[:12]
        canonical = plain_url(url) if url else None
    return upsert_product(conn, source=source, listing_id=listing_id, title=title, url=canonical, brand=None,
                          model=None, origin="MANUAL", now=now, overwrite=False)


def add_manual_observation(conn, product_id, *, amount_minor, currency, role, availability, note, observed_at, now):
    product = dict(get_product_row(conn, product_id))
    normalized = manual_normalized(product, amount_minor, currency, role, availability, note, observed_at)
    token = hashlib.sha256(f"MANUAL\n{product_id}\n{uuid.uuid4().hex}".encode()).hexdigest()
    observation_id, _ = add_observation(
        conn, product_id=product_id, normalized=normalized, raw=None, origin="MANUAL",
        evidence_level="USER_ENTERED", fingerprint=token, observed_at=observed_at,
        availability=availability, prices=normalized["prices"], now=now,
    )
    refresh_summary(conn, product_id)
    return observation_id


def sparklines(conn, product_ids, points=40):
    """Короткие ряды минимальной показанной цены для мини графиков в списках: {id: [[время, цена]]}."""
    result = {}
    for product_id in product_ids:
        rows = [dict(item) for item in conn.execute(
            "SELECT pp.observation_id, o.observed_at, pp.role, pp.amount_minor, pp.currency "
            "FROM price_point pp JOIN observation o ON o.id = pp.observation_id "
            "WHERE pp.product_id = ? AND o.observed_at IS NOT NULL ORDER BY o.observed_at, o.id LIMIT 5000",
            (product_id,))]
        series, _ = pricing.history(rows)
        result[str(product_id)] = [[item["observed_at"], item["lowest_minor"]] for item in series[-points:]
                                   if item.get("lowest_minor") is not None]
    return result
