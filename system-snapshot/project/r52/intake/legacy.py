"""Однократный импорт старых баз R-52: D0 (jarvis.sqlite3) и DATA-01 (observations.sqlite3).

Исходный файл открывается только на чтение и сначала копируется во временный снимок.
Время наблюдения не придумывается: если в старой базе его нет, оно остаётся пустым.
Повторный импорт ничего не дублирует: у каждого наблюдения есть отпечаток.
"""

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ..domain.normalize import (
    CHECKS, FIELDS, FULL_COST_BLOCKERS, VERSION, _completeness, availability_from, blank_field,
    canonical_source, condition_from, resolve_role,
)
from ..domain.pricing import ROLE_LABELS, ROLES
from ..errors import AppError
from ..money import CURRENCIES, format_minor, parse_amount, to_minor
from ..store.products import add_observation, ensure_source, refresh_summary, upsert_product
from ..timeutil import iso, parse_optional

log = logging.getLogger("r52.legacy")
CHUNK = 2000
D0_TABLES = {"source", "product", "offer", "capture", "price_observation"}
OFFER_NOTE = "Состояние предложения на момент последнего обновления D0, не привязано к конкретному снимку."
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@contextmanager
def snapshot(path, work_dir):
    """Копия старой базы во временной папке. Оригинал не изменяется."""
    path = Path(path)
    if not path.is_file():
        raise AppError("LEGACY_DB_NOT_FOUND", status=404, detail=str(path))
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="legacy-", dir=work_dir))
    copy = temp / "snapshot.sqlite3"
    try:
        try:
            source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                target = sqlite3.connect(copy)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        except sqlite3.Error as error:
            log.warning("Снимок через backup API не удался (%s), копирую файлы", error)
            copy.unlink(missing_ok=True)
            shutil.copy2(path, copy)
            wal = Path(str(path) + "-wal")
            if wal.exists():
                shutil.copy2(wal, Path(str(copy) + "-wal"))
        conn = sqlite3.connect(copy)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        try:
            shutil.rmtree(temp)
        except OSError as error:
            log.warning("Временный снимок не удалён: %s", error)


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _time(value):
    parsed = parse_optional(value)
    return iso(parsed) if parsed else None


def _json(text):
    if text in (None, ""):
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _price_field(points):
    labels = []
    for item in points:
        text = format_minor(item["amount_minor"], item["currency"])
        if item["role"] != "regular":
            text += f" ({ROLE_LABELS[item['role']]})"
        labels.append(text)
    return {"state": "OBSERVED", "value": labels, "method": "LEGACY_IMPORT", "evidence": None,
            "independently_verified": False}


def _base(scope, source, listing_id, url, observed_at, title, fields, points, legacy):
    return {
        "version": VERSION, "scope": scope, "source": source, "listing_id": listing_id, "url": url,
        "observed_at": observed_at, "title": title, "brand": None, "model": None, "fields": fields,
        "prices": points, "characteristics": [], "description_hints": [],
        "condition": condition_from(fields["condition"]["value"]), "availability": "UNKNOWN",
        "delivery": {"requested_destination": "Владивосток", "destination_mentioned": False,
                     "destination_confirmed": False, "terms": None, "terms_state": "UNKNOWN",
                     "cost_minor": None, "cost_state": "UNKNOWN"},
        "page_region": None, "conflicts": [],
        "checks": [{"code": code, "text": CHECKS[code]} for code in ("VERIFY_TOTAL", "VERIFY_VARIANT")],
        "full_cost_blockers": list(FULL_COST_BLOCKERS), "completeness": _completeness(fields), "legacy": legacy,
    }


def _record_run(app, kind, path, stats, started_at):
    with app.db.write() as conn:
        conn.execute(
            "INSERT INTO import_run(kind, source_path, started_at, finished_at, status, stats_json) "
            "VALUES(?, ?, ?, ?, 'COMPLETED', ?)",
            (kind, str(path), started_at, iso(app.clock.now()), json.dumps(stats, ensure_ascii=False)),
        )


def _offer_snapshot(offer):
    return {
        "note": OFFER_NOTE,
        "seller": _json(offer["seller_json"]),
        "variant": _json(offer["variant_json"]),
        "attributes": _json(offer["attributes_json"]),
        "availability": offer["availability"],
        "delivery": _json(offer["delivery_json"]),
    }


def _d0_points(items, stats):
    points, seen = [], set()
    for item in items:
        amount = item["amount_minor"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0 or item["currency"] not in CURRENCIES:
            stats["skipped_amounts"] += 1
            continue
        role = item["role"] if item["role"] in ROLES else "unknown"
        key = (role, amount, item["currency"])
        if key in seen:
            stats["duplicate_points"] += 1
            continue
        seen.add(key)
        points.append({
            "role": role, "role_origin": "LEGACY_D0_ROLE", "source_role": item["role"], "amount_minor": amount,
            "currency": item["currency"], "label": None, "variant_association": "UNKNOWN", "evidence": None,
            "applicability_verified": False,
        })
    return points


def import_d0(app, path, ctx=None, dry_run=False):
    started, started_at = time.monotonic(), iso(app.clock.now())
    with snapshot(path, app.paths.run_dir) as src:
        missing = D0_TABLES - _tables(src)
        if missing:
            raise AppError("LEGACY_DB_UNSUPPORTED", detail=sorted(missing))
        sources = {row["id"]: (canonical_source(row["key"]), row["name"])
                   for row in src.execute("SELECT id, key, name FROM source")}
        captures = {row["id"]: dict(row) for row in src.execute(
            "SELECT id, sha256, kind, evidence_level, captured_at, viewing_region, context_key, identity_confirmed, "
            "region_confirmed, observation_basis FROM capture")}
        offers = {row["offer_id"]: dict(row) for row in src.execute(
            "SELECT o.id AS offer_id, o.offer_key, o.seller_json, o.variant_json, o.attributes_json, o.availability, "
            "o.delivery_json, p.source_id, p.external_id, p.title, p.brand, p.model, p.source_url "
            "FROM offer o JOIN product p ON p.id = o.product_id")}
        groups = {}
        for row in src.execute(
                "SELECT offer_id, capture_id, role, currency, amount_minor FROM price_observation "
                "ORDER BY capture_id, offer_id, id"):
            groups.setdefault((row["offer_id"], row["capture_id"]), []).append(dict(row))
    latest_capture = {}
    for offer_id, capture_id in groups:
        mark = (captures.get(capture_id, {}).get("captured_at") or "", capture_id)
        if offer_id not in latest_capture or mark > latest_capture[offer_id]:
            latest_capture[offer_id] = mark
    stats = {
        "database": "D0", "dry_run": dry_run,
        "products_in_source": len({(offer["source_id"], offer["external_id"]) for offer in offers.values()}),
        "observations_in_source": len(groups), "products_new": 0, "observations_new": 0,
        "observations_existing": 0, "observations_without_time": 0, "price_points": 0,
        "duplicate_points": 0, "skipped_without_offer": 0, "skipped_amounts": 0, "roles": {},
    }
    if dry_run:
        with app.db.read() as conn:
            existing = {row[0] for row in conn.execute("SELECT fingerprint FROM observation WHERE origin = 'LEGACY_D0'")}
            known_products = {(row[0], row[1]) for row in conn.execute("SELECT source, listing_id FROM product")}
        new_products = set()
    now = iso(app.clock.now())
    keys = list(groups)
    for start in range(0, len(keys), CHUNK):
        batch = []
        for offer_id, capture_id in keys[start:start + CHUNK]:
            offer = offers.get(offer_id)
            if offer is None:
                stats["skipped_without_offer"] += 1
                continue
            capture = captures.get(capture_id, {})
            source, source_name = sources.get(offer["source_id"], ("UNKNOWN", None))
            listing_id = str(offer["external_id"])
            points = _d0_points(groups[(offer_id, capture_id)], stats)
            marker = capture.get("sha256") or f"capture-{capture_id}"
            fingerprint = hashlib.sha256(
                "\n".join(("LEGACY_D0", source, listing_id, str(offer["offer_key"]), str(marker))).encode("utf-8")
            ).hexdigest()
            batch.append((offer, capture, capture_id, source, source_name, listing_id, points, fingerprint))
        if dry_run:
            for offer, capture, _, source, _, listing_id, points, fingerprint in batch:
                if fingerprint in existing:
                    stats["observations_existing"] += 1
                    continue
                stats["observations_new"] += 1
                stats["price_points"] += len(points)
                if _time(capture.get("captured_at")) is None:
                    stats["observations_without_time"] += 1
                if (source, listing_id) not in known_products:
                    new_products.add((source, listing_id))
            continue
        with app.db.write() as conn:
            touched = set()
            for offer, capture, capture_id, source, source_name, listing_id, points, fingerprint in batch:
                ensure_source(conn, source, source_name, "marketplace")
                title = (offer["title"] or "").strip()[:500] or f"{source} {listing_id}"
                product_id, created = upsert_product(
                    conn, source=source, listing_id=listing_id, title=title, url=offer["source_url"] or None,
                    brand=offer["brand"] or None, model=offer["model"] or None, origin="LEGACY_D0", now=now,
                    overwrite=False,
                )
                stats["products_new"] += int(created)
                observed_at = _time(capture.get("captured_at"))
                attach = latest_capture.get(offer["offer_id"], (None, None))[1] == capture_id
                fields = {key: blank_field() for key in FIELDS}
                fields["title"] = {"state": "OBSERVED", "value": title, "method": "LEGACY_IMPORT", "evidence": None,
                                   "independently_verified": False}
                if points:
                    fields["price_conditions"] = _price_field(points)
                legacy = {
                    "database": "D0", "capture_id": capture_id, "capture_kind": capture.get("kind"),
                    "evidence_level": capture.get("evidence_level"),
                    "observation_basis": capture.get("observation_basis"),
                    "viewing_region": capture.get("viewing_region"), "context_key": capture.get("context_key"),
                    "identity_confirmed": bool(capture.get("identity_confirmed")),
                    "region_confirmed": bool(capture.get("region_confirmed")),
                    "offer_key": offer["offer_key"],
                    "offer_snapshot": _offer_snapshot(offer) if attach else None,
                }
                normalized = _base("LEGACY_D0", source, listing_id, offer["source_url"], observed_at, title,
                                   fields, points, legacy)
                _, new = add_observation(
                    conn, product_id=product_id, normalized=normalized, raw=None, origin="LEGACY_D0",
                    evidence_level="LEGACY:" + str(capture.get("evidence_level") or "UNKNOWN"),
                    fingerprint=fingerprint, observed_at=observed_at, availability="UNKNOWN", prices=points, now=now,
                )
                if new:
                    stats["observations_new"] += 1
                    stats["price_points"] += len(points)
                    stats["observations_without_time"] += int(observed_at is None)
                    touched.add(product_id)
                else:
                    stats["observations_existing"] += 1
            for product_id in touched:
                refresh_summary(conn, product_id)
        if ctx is not None:
            ctx.progress(min(len(keys), start + CHUNK) * 95 // max(1, len(keys)))
            ctx.check()
    if dry_run:
        stats["products_new"] = len(new_products)
    stats["seconds"] = round(time.monotonic() - started, 1)
    if not dry_run:
        _record_run(app, "LEGACY_D0", path, stats, started_at)
    log.info("Импорт D0: %s", stats)
    return stats


def _old_field(item):
    if (isinstance(item, dict) and item.get("state") in ("OBSERVED", "SELLER_CLAIM", "USER_ENTERED")
            and item.get("value") not in (None, "", [], {})):
        evidence = item.get("evidence")
        return {"state": item["state"], "value": item["value"], "method": item.get("method") or "LEGACY_DATA01",
                "evidence": evidence if isinstance(evidence, (dict, list, str)) else None,
                "independently_verified": False}
    return blank_field()


def _data01_prices(field):
    value = field.get("value") if isinstance(field, dict) else None
    points, seen = [], set()
    for candidate in value if isinstance(value, list) else []:
        if not isinstance(candidate, dict):
            continue
        amount = parse_amount(candidate.get("amount"))
        currency = candidate.get("currency")
        if amount is None or currency not in CURRENCIES:
            continue
        source_role = str(candidate.get("source_role") or "unknown").strip().lower()[:40]
        role, origin = resolve_role(source_role, str(candidate.get("conditions") or ""))
        key = (role, to_minor(amount), currency)
        if key in seen:
            continue
        seen.add(key)
        points.append({
            "role": role, "role_origin": origin, "source_role": source_role, "amount_minor": key[1],
            "currency": currency, "label": str(candidate.get("conditions") or "")[:300] or None,
            "variant_association": str(candidate.get("variant_association") or "UNKNOWN")[:40],
            "evidence": candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else None,
            "applicability_verified": False,
        })
    return points


def import_data01(app, path, ctx=None, dry_run=False):
    started, started_at = time.monotonic(), iso(app.clock.now())
    with snapshot(path, app.paths.run_dir) as src:
        if "observation" not in _tables(src):
            raise AppError("LEGACY_DB_UNSUPPORTED", detail=["observation"])
        rows = [dict(row) for row in src.execute("SELECT * FROM observation ORDER BY id")]
    stats = {
        "database": "DATA-01", "dry_run": dry_run, "observations_in_source": len(rows), "products_new": 0,
        "observations_new": 0, "observations_existing": 0, "observations_without_time": 0, "price_points": 0,
        "skipped_invalid": 0, "sources_created": [],
    }
    prepared = []
    for row in rows:
        old = _json(row.get("normalized_json"))
        source = canonical_source(row.get("source"))
        listing_id = str(row.get("listing_id") or "").strip()
        if not isinstance(old, dict) or not source or not listing_id:
            stats["skipped_invalid"] += 1
            continue
        old_fields = old.get("fields") if isinstance(old.get("fields"), dict) else {}
        fields = {key: _old_field(old_fields.get(key)) for key in FIELDS}
        title_value = fields["title"]["value"]
        title = title_value.strip()[:500] if isinstance(title_value, str) and title_value.strip() else f"{source} {listing_id}"
        points = _data01_prices(old_fields.get("price_conditions"))
        if points:
            fields["price_conditions"] = _price_field(points)
        observed_at = _time(row.get("observed_at"))
        availability = "UNKNOWN"
        if fields["availability"]["state"] != "UNKNOWN":
            availability = availability_from(fields["availability"]["value"])
        legacy = {
            "database": "DATA-01", "scope": row.get("scope"), "provenance": _json(row.get("provenance")),
            "old_version": old.get("version"), "observation_status": old.get("observation_status"),
            "purchase_total_state": old.get("purchase_total_state"),
        }
        url = old.get("url") if isinstance(old.get("url"), str) else None
        normalized = _base("LEGACY_DATA01", source, listing_id, url, observed_at, title, fields, points, legacy)
        normalized["availability"] = availability
        if isinstance(old.get("characteristics"), list):
            normalized["characteristics"] = old["characteristics"][:100]
        if isinstance(old.get("conflicts"), list):
            normalized["conflicts"] = [item for item in old["conflicts"] if isinstance(item, dict)][:20]
        sha = row.get("sha256")
        fingerprint = sha if isinstance(sha, str) and SHA256.match(sha) else hashlib.sha256(
            ("LEGACY_DATA01\n" + str(row.get("id")) + "\n" + str(row.get("normalized_json"))).encode("utf-8")).hexdigest()
        raw = _json(row.get("raw_json"))
        prepared.append({
            "source": source, "listing_id": listing_id, "title": title, "url": url, "normalized": normalized,
            "raw": raw if isinstance(raw, (dict, list)) else None, "fingerprint": fingerprint,
            "observed_at": observed_at, "availability": availability, "points": points,
            "evidence": "LEGACY:" + str(row.get("scope") or "UNKNOWN"),
        })
    if dry_run:
        with app.db.read() as conn:
            existing = {row[0] for row in conn.execute("SELECT fingerprint FROM observation")}
        for item in prepared:
            if item["fingerprint"] in existing:
                stats["observations_existing"] += 1
            else:
                stats["observations_new"] += 1
                stats["price_points"] += len(item["points"])
                stats["observations_without_time"] += int(item["observed_at"] is None)
        stats["seconds"] = round(time.monotonic() - started, 1)
        return stats
    now = iso(app.clock.now())
    with app.db.write() as conn:
        known_sources = {row[0] for row in conn.execute("SELECT key FROM source")}
        touched = set()
        for item in prepared:
            if item["source"] not in known_sources:
                ensure_source(conn, item["source"], item["source"], "retail")
                known_sources.add(item["source"])
                stats["sources_created"].append(item["source"])
            product_id, created = upsert_product(
                conn, source=item["source"], listing_id=item["listing_id"], title=item["title"], url=item["url"],
                brand=None, model=None, origin="LEGACY_DATA01", now=now, overwrite=False,
            )
            stats["products_new"] += int(created)
            _, new = add_observation(
                conn, product_id=product_id, normalized=item["normalized"], raw=item["raw"], origin="LEGACY_DATA01",
                evidence_level=item["evidence"], fingerprint=item["fingerprint"], observed_at=item["observed_at"],
                availability=item["availability"], prices=item["points"], now=now,
            )
            if new:
                stats["observations_new"] += 1
                stats["price_points"] += len(item["points"])
                stats["observations_without_time"] += int(item["observed_at"] is None)
                touched.add(product_id)
            else:
                stats["observations_existing"] += 1
        for product_id in touched:
            refresh_summary(conn, product_id)
    if ctx is not None:
        ctx.progress(95)
    stats["seconds"] = round(time.monotonic() - started, 1)
    _record_run(app, "LEGACY_DATA01", path, stats, started_at)
    log.info("Импорт DATA-01: %s", stats)
    return stats
