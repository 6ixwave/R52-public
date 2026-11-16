r"""E2E Farpost: сохранённый HTML → parse_page → normalize → SQLite → проверка.

Запуск: C:\Python315\python.exe diag_v66_e2e_fp.py
"""
import json
print("SCRIPT START", flush=True)
import logging
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("e2e_fp")

try:
    # === 1. Чтение сохранённого HTML ===
    html_file = BASE / "diag_v64_fp_1.html"
    log.info("Читаю %s (%d байт)", html_file.name, html_file.stat().st_size)
    # Farpost отдаёт windows-1251
    raw = html_file.read_bytes()
    # Пробуем cp1251, потом utf-8
    try:
        html = raw.decode("cp1251")
        enc_used = "cp1251"
    except (UnicodeDecodeError, ValueError):
        html = raw.decode("utf-8", errors="replace")
        enc_used = "utf-8(replace)"
    print(f"HTML: {len(html)} символов, {html.count(chr(10))} строк, enc={enc_used}")

    # === 2. Парсинг через FarpostCollector ===
    from r52.collectors.farpost import FarpostCollector
    print("FarpostCollector imported OK")

    collector = FarpostCollector(use_proxies=False)
    print("FarpostCollector instantiated OK")

    listing_id = "130693618"
    url = "https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/triangle-icelynx-ti501-205-55-r16-94t-xl-130693618.html"
    resp = SimpleNamespace(text=html, status_code=200, url=url)

    t0 = time.monotonic()
    observation = collector.parse_page(resp, listing_id, url, "Triangle IcelynX")
    elapsed = time.monotonic() - t0
    print(f"\n=== PARSE_PAGE: {elapsed:.2f}s ===")

    if observation is None:
        print("FATAL: parse_page вернул None")
        sys.exit(1)

    fields = observation.get("fields", {})
    prices = observation.get("price_candidates", [])
    media = observation.get("media", {}).get("items", [])
    availability = observation.get("availability", "UNKNOWN")

    print(f"Fields: {len(fields)} → {list(fields.keys())}")
    print(f"Title: {fields.get('title', {}).get('value', '?')[:80]}")
    print(f"Prices: {len(prices)}")
    for p in prices[:5]:
        print(f"  {p.get('role')}: {p.get('amount_minor')} {p.get('currency')} [{p.get('label')}]")
    print(f"Media: {len(media)} items")
    print(f"Availability: {availability}")
    print(f"Category: {observation.get('category_path')}")

    # === 3. Пост-обработка (как в base.py collect_product) ===
    collector._ensure_evidence(observation)
    collector._ensure_price_amounts(observation)
    collector._ensure_availability_in_fields(observation)

    # Обёртка в eye-формат
    now_iso = datetime.now(timezone(timedelta(hours=10))).isoformat()
    wrapper = {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": "FARPOST",
        "observation": {
            **observation,
            "source": "FARPOST",
            "id": listing_id,
            "url": url,
            "observed_at": now_iso,
            "status": "PARTIAL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": 200,
        },
    }
    print(f"\n=== EYE WRAPPER ===")
    print(f"format: {wrapper['format']}")
    print(f"capture_action: {wrapper['capture_action']}")
    print(f"fully_autonomous: {wrapper['fully_autonomous']}")

    # === 4. Нормализация ===
    from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE

    t0 = time.monotonic()
    normalized, cleaned = normalize_eye(wrapper["observation"])
    elapsed = time.monotonic() - t0
    print(f"\n=== NORMALIZE: {elapsed:.2f}s ===")
    print(f"source: {normalized['source']}")
    print(f"listing_id: {normalized['listing_id']}")
    print(f"title: {normalized['title']}")
    print(f"prices: {json.dumps(normalized['prices'], ensure_ascii=False)[:200]}")
    print(f"availability: {normalized['availability']}")
    print(f"completeness: {normalized['completeness']}")
    print(f"brand: {normalized.get('brand')}")
    print(f"model: {normalized.get('model')}")
    print(f"media items: {len((normalized.get('media') or {}).get('items', []))}")

    # === 5. SQLite ===
    from r52.db import Database
    from r52.db.migrate import migrate

    db_path = BASE / "test-intake-e2e-fp" / "r52_e2e_fp.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    # Убираем WAL/SHM
    for suffix in ("-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()

    db = Database(str(db_path))
    db.initialize()
    result = migrate(db, backup_dir=str(db_path.parent / "backups"), now=datetime.now(timezone.utc))
    print(f"\n=== DB: {db_path.name} ===")
    print(f"Migrations applied: {result['applied']}")

    from r52.store.products import upsert_product, add_observation, refresh_summary
    from r52.timeutil import iso

    now = iso(datetime.now(timezone.utc))
    fp = fingerprint(EYE_SCOPE, cleaned)
    print(f"Fingerprint: {fp[:32]}...")

    with db.write() as conn:
        product_id, created = upsert_product(
            conn,
            source=normalized["source"],
            listing_id=normalized["listing_id"],
            title=normalized["title"],
            url=normalized["url"],
            brand=normalized.get("brand"),
            model=normalized.get("model"),
            origin="E2E_FP",
            now=now,
        )
        print(f"Product: id={product_id} created={created}")

        obs_id, obs_created = add_observation(
            conn,
            product_id=product_id,
            normalized=normalized,
            raw=cleaned,
            origin="E2E_FP",
            evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
            fingerprint=fp,
            observed_at=normalized["observed_at"],
            availability=normalized["availability"],
            prices=normalized["prices"],
            now=now,
        )
        print(f"Observation: id={obs_id} created={obs_created}")

        refresh_summary(conn, product_id)

    # === 6. Проверка строк ===
    print(f"\n=== DB VERIFICATION ===")
    with db.read() as conn:
        prod = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()
        if prod:
            print(f"PRODUCT: id={prod['id']} source={prod['source']} listing_id={prod['listing_id']}")
            print(f"  title={prod['title'][:60]}")
            print(f"  url={prod['url'][:80]}")
            print(f"  last_lowest_minor={prod['last_lowest_minor']}")
            print(f"  last_seen_at={prod['last_seen_at']}")

        obs = conn.execute(
            "SELECT id, product_id, observed_at, origin, availability, fingerprint FROM observation WHERE product_id=?",
            (product_id,)).fetchall()
        print(f"OBSERVATIONS: {len(obs)}")
        for o in obs:
            print(f"  id={o['id']} origin={o['origin']} avail={o['availability']} fp={o['fingerprint'][:16]}...")

        pp = conn.execute("SELECT * FROM price_point WHERE product_id=?", (product_id,)).fetchall()
        print(f"PRICE_POINTS: {len(pp)}")
        for p in pp[:5]:
            print(f"  id={p['id']} role={p['role']} minor={p['amount_minor']} cur={p['currency']} at={p['observed_at']}")

    db.checkpoint()
    # Database не имеет close(): соединения закрываются в read()/write()

    # === 7. Итог ===
    print(f"\n{'='*60}")
    print(f"E2E FARPOST: OK")
    print(f"  HTML: {html_file.name} ({len(html)} bytes, {enc_used})")
    print(f"  Title: {normalized['title'][:60]}")
    print(f"  Prices: {len(normalized['prices'])} (first={normalized['prices'][0]['amount_minor'] if normalized['prices'] else 0} {normalized['prices'][0]['currency'] if normalized['prices'] else ''})")
    print(f"  Media: {len((normalized.get('media') or {}).get('items', []))} items")
    print(f"  Availability: {normalized['availability']}")
    print(f"  DB: {db_path}")
    print(f"{'='*60}")

    # JSON для отладки
    out_json = BASE / "diag_v66_fp_result.json"
    out_json.write_text(json.dumps({
        "parse": {
            "fields": {k: v.get("value") for k, v in fields.items()},
            "prices": prices,
            "media_count": len(media),
            "availability": availability,
        },
        "normalized": {
            "source": normalized["source"],
            "listing_id": normalized["listing_id"],
            "title": normalized["title"],
            "prices": normalized["prices"],
            "availability": normalized["availability"],
            "completeness": normalized["completeness"],
            "brand": normalized.get("brand"),
            "model": normalized.get("model"),
            "media_items": len((normalized.get("media") or {}).get("items", [])),
        },
        "db": {"product_id": product_id, "observation_id": obs_id, "price_points": len(pp)},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {out_json}")

except Exception:
    print("\n" + "="*60)
    print("E2E FARPOST: EXCEPTION")
    print("="*60)
    traceback.print_exc()
    sys.exit(1)

print("\nDONE")
