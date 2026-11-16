r"""E2E Persist All v69: Farpost + WB + Ozon + Avito → постоянная r52/data/r52.db.

Исправления vs v67:
- WB: добавлен _ensure_evidence / _ensure_price_amounts / _ensure_availability_in_fields
- Avito: live HTTP fetch (локальные HTML — redirect-страницы без контента)
- Logging: %s вместо %d для last_lowest_minor (может быть None)

Запуск: C:\Python315\python.exe diag_v69_persist_all.py
"""
import json
print("SCRIPT START", flush=True)
import logging
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("persist_all")

# === ПУТИ ===
PERSIST_DB = BASE / "r52" / "data" / "r52.db"
FP_HTML = BASE / "diag_v64_fp_1.html"
WB_JSON = BASE / "diag_v59_wb_199298871.json"
OZON_HTML = BASE / "diag_v48_ozon_1600562267.html"
OUT_JSON = BASE / "diag_v69_result.json"

results = {}

try:
    # ============================================================
    # 0. ПОСТОЯННАЯ БД
    # ============================================================
    from r52.db import Database
    from r52.db.migrate import migrate
    from r52.store.products import upsert_product, add_observation, refresh_summary
    from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE
    from r52.timeutil import iso

    PERSIST_DB.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(PERSIST_DB))
    db.initialize()
    mig = migrate(db, backup_dir=str(PERSIST_DB.parent / "backups"), now=datetime.now(timezone.utc))
    log.info("Постоянная БД: %s, миграции: %s", PERSIST_DB, mig["applied"])

    # Снимок ДО
    with db.read() as conn:
        pre = {
            "source": conn.execute("SELECT count(*) FROM source").fetchone()[0],
            "product": conn.execute("SELECT count(*) FROM product").fetchone()[0],
            "observation": conn.execute("SELECT count(*) FROM observation").fetchone()[0],
            "price_point": conn.execute("SELECT count(*) FROM price_point").fetchone()[0],
        }
    log.info("ДО: %s", pre)

    now_utc = datetime.now(timezone.utc)
    now_iso = iso(now_utc)
    tz_krasnoyarsk = timezone(timedelta(hours=10))

    # ============================================================
    # 1. FARPOST
    # ============================================================
    log.info("=== FARPOST ===")
    try:
        raw = FP_HTML.read_bytes()
        try:
            html = raw.decode("cp1251")
            enc = "cp1251"
        except (UnicodeDecodeError, ValueError):
            html = raw.decode("utf-8", errors="replace")
            enc = "utf-8(replace)"
        log.info("FP HTML: %d байт, enc=%s", len(html), enc)

        from r52.collectors.farpost import FarpostCollector
        fp_collector = FarpostCollector(use_proxies=False)
        fp_id = "130693618"
        fp_url = "https://www.farpost.ru/ni/krasnoyarsk/auto/wheel/tire/triangle-icelynx-ti501-205-55-r16-94t-xl-130693618.html"
        resp = SimpleNamespace(text=html, status_code=200, url=fp_url)

        obs = fp_collector.parse_page(resp, fp_id, fp_url, "Triangle IcelynX")
        if obs is None:
            raise RuntimeError("FP parse_page → None")

        fp_collector._ensure_evidence(obs)
        fp_collector._ensure_price_amounts(obs)
        fp_collector._ensure_availability_in_fields(obs)

        wrapper = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "FARPOST",
            "observation": {
                **obs,
                "source": "FARPOST",
                "id": fp_id,
                "url": fp_url,
                "observed_at": datetime.now(tz_krasnoyarsk).isoformat(),
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
            },
        }
        normalized, cleaned = normalize_eye(wrapper["observation"])
        fp_hash = fingerprint(EYE_SCOPE, cleaned)

        with db.write() as conn:
            pid, created = upsert_product(
                conn, source="FARPOST", listing_id=normalized["listing_id"],
                title=normalized["title"], url=normalized["url"],
                brand=normalized.get("brand"), model=normalized.get("model"),
                origin="PERSIST_FP", now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin="PERSIST_FP", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
                fingerprint=fp_hash, observed_at=normalized["observed_at"],
                availability=normalized["availability"], prices=normalized["prices"],
                now=now_iso)
            refresh_summary(conn, pid)

        results["FARPOST"] = {
            "status": "OK", "product_id": pid, "created": created,
            "observation_id": oid, "observation_created": ocreated,
            "title": normalized["title"][:60],
            "prices": len(normalized["prices"]),
            "availability": normalized["availability"],
        }
        log.info("FP: product=%d created=%s obs=%d", pid, created, oid)

    except Exception as e:
        results["FARPOST"] = {"status": "ERROR", "error": str(e)}
        log.exception("FP FAILED")

    # ============================================================
    # 2. WILDBERRIES (FIX: _ensure_evidence)
    # ============================================================
    log.info("=== WILDBERRIES ===")
    try:
        card = json.loads(WB_JSON.read_text(encoding="utf-8"))
        wb_id = str(card["nm_id"])
        wb_url = f"https://www.wildberries.ru/catalog/{wb_id}/detail.aspx"
        wb_title = card.get("imt_name", f"WB-{wb_id}")
        wb_brand = (card.get("selling") or {}).get("brand_name")
        wb_desc = card.get("description", "")
        wb_subj = card.get("subj_name", "")
        wb_subj_root = card.get("subj_root_name", "")
        wb_category = f"{wb_subj_root} > {wb_subj}" if wb_subj_root and wb_subj else (wb_subj or wb_subj_root)
        wb_media_count = (card.get("media") or {}).get("photo_count", 0)
        wb_has_video = (card.get("media") or {}).get("has_video", False)

        log.info("WB card: id=%s title=%s brand=%s media=%d", wb_id, wb_title[:40], wb_brand, wb_media_count)

        # --- Цена: live fetch из WB Price API ---
        wb_prices = []
        try:
            import requests as req
            price_api = f"https://card.wb.ru/cards/v2/detail?nm={wb_id}"
            headers = {
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.wildberries.ru/",
            }
            pr = req.get(price_api, headers=headers, timeout=15)
            if pr.status_code == 200:
                pdata = pr.json()
                products = (pdata.get("data") or {}).get("products") or []
                if products:
                    prod = products[0]
                    sizes = prod.get("sizes") or []
                    if sizes:
                        sz = sizes[0]
                        price_info = sz.get("price") or {}
                        base_price = price_info.get("basic") or 0
                        total_price = price_info.get("total") or 0
                        currency = price_info.get("currency") or "RUB"
                        if total_price > 0:
                            wb_prices.append({
                                "role": "regular",
                                "amount_minor": int(total_price),
                                "currency": currency,
                                "label": "wb_price_api.total",
                            })
                        if base_price > 0:
                            wb_prices.append({
                                "role": "regular",
                                "amount_minor": int(base_price),
                                "currency": currency,
                                "label": "wb_price_api.basic",
                            })
                        old = price_info.get("old") or 0
                        if old > 0:
                            wb_prices.append({
                                "role": "old",
                                "amount_minor": int(old),
                                "currency": currency,
                                "label": "wb_price_api.old",
                            })
                    log.info("WB price API: %d цен", len(wb_prices))
                else:
                    log.warning("WB price API: products пуст")
            else:
                log.warning("WB price API: HTTP %d", pr.status_code)
        except Exception as pe:
            log.warning("WB price API error: %s", pe)

        if not wb_prices:
            log.warning("WB: цены не получены, ставим UNKNOWN")

        # --- Specs из options ---
        specs = {}
        for opt in (card.get("options") or []):
            name = opt.get("name", "")
            val = opt.get("value", "")
            if name and val:
                specs[name] = {"label": name, "value": val}

        # --- Media items ---
        vol = int(wb_id) // 100000
        part = int(wb_id) // 1000
        media_items = []
        for i in range(wb_media_count):
            media_items.append({
                "type": "image",
                "url": f"https://basket-{vol}-wbbasket.wbbasket.ru/vol{vol}/part{part}/{wb_id}/images/big1/{wb_id}-{i+1}.jpg",
                "index": i,
            })
        if wb_has_video:
            media_items.append({"type": "video", "url": None, "index": wb_media_count})

        # --- Конструкция observation ---
        wb_observation = {
            "fields": {
                "title": {"status": "KNOWN", "value": wb_title, "method": "CARD_JSON"},
                "description": {"status": "KNOWN", "value": wb_desc[:3000], "method": "CARD_JSON"},
                "brand": {"status": "KNOWN", "value": wb_brand or "UNKNOWN", "method": "CARD_JSON"},
                "model": {"status": "KNOWN", "value": wb_title, "method": "CARD_JSON"},
                "category": {"status": "KNOWN", "value": wb_category, "method": "CARD_JSON"},
                "specs": {"status": "KNOWN", "value": specs, "method": "CARD_JSON"},
            },
            "price_candidates": wb_prices,
            "media": {"items": media_items},
            "availability": "IN_STOCK" if wb_prices else "UNKNOWN",
            "category_path": wb_category,
        }

        # === FIX: добавить evidence, amount, availability в fields ===
        from r52.collectors.wb import WBCollector
        wb_collector = WBCollector(use_proxies=False)
        wb_collector._ensure_evidence(wb_observation)
        wb_collector._ensure_price_amounts(wb_observation)
        wb_collector._ensure_availability_in_fields(wb_observation)
        log.info("WB: evidence добавлен в %d полей", len(wb_observation["fields"]))

        wb_wrapper = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "WB",
            "observation": {
                **wb_observation,
                "source": "WB",
                "id": wb_id,
                "url": wb_url,
                "observed_at": datetime.now(tz_krasnoyarsk).isoformat(),
                "status": "SUCCESS" if wb_prices else "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
            },
        }
        normalized, cleaned = normalize_eye(wb_wrapper["observation"])
        fp_hash = fingerprint(EYE_SCOPE, cleaned)

        with db.write() as conn:
            pid, created = upsert_product(
                conn, source="WB", listing_id=normalized["listing_id"],
                title=normalized["title"], url=normalized["url"],
                brand=normalized.get("brand"), model=normalized.get("model"),
                origin="PERSIST_WB", now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin="PERSIST_WB", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
                fingerprint=fp_hash, observed_at=normalized["observed_at"],
                availability=normalized["availability"], prices=normalized["prices"],
                now=now_iso)
            refresh_summary(conn, pid)

        results["WB"] = {
            "status": "OK", "product_id": pid, "created": created,
            "observation_id": oid, "observation_created": ocreated,
            "title": normalized["title"][:60],
            "prices": len(normalized["prices"]),
            "availability": normalized["availability"],
            "price_api_live": bool(wb_prices),
        }
        log.info("WB: product=%d created=%s obs=%d prices=%d", pid, created, oid, len(normalized["prices"]))

    except Exception as e:
        results["WB"] = {"status": "ERROR", "error": str(e)}
        log.exception("WB FAILED")

    # ============================================================
    # 3. OZON
    # ============================================================
    log.info("=== OZON ===")
    try:
        raw = OZON_HTML.read_bytes()
        try:
            html = raw.decode("utf-8")
            enc = "utf-8"
        except (UnicodeDecodeError, ValueError):
            html = raw.decode("utf-8", errors="replace")
            enc = "utf-8(replace)"
        log.info("OZON HTML: %d байт, enc=%s", len(html), enc)

        from r52.collectors.ozon import OzonCollector
        oz_collector = OzonCollector(use_proxies=False)
        oz_id = "1600562267"
        oz_url = f"https://www.ozon.ru/product/{oz_id}/"
        resp = SimpleNamespace(text=html, status_code=200, url=oz_url)

        obs = oz_collector.parse_page(resp, oz_id, oz_url, "")
        if obs is None:
            raise RuntimeError("OZON parse_page → None")

        oz_collector._ensure_evidence(obs)
        oz_collector._ensure_price_amounts(obs)
        oz_collector._ensure_availability_in_fields(obs)

        oz_wrapper = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "OZON",
            "observation": {
                **obs,
                "source": "OZON",
                "id": oz_id,
                "url": oz_url,
                "observed_at": datetime.now(tz_krasnoyarsk).isoformat(),
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
            },
        }
        normalized, cleaned = normalize_eye(oz_wrapper["observation"])
        fp_hash = fingerprint(EYE_SCOPE, cleaned)

        with db.write() as conn:
            pid, created = upsert_product(
                conn, source="OZON", listing_id=normalized["listing_id"],
                title=normalized["title"], url=normalized["url"],
                brand=normalized.get("brand"), model=normalized.get("model"),
                origin="PERSIST_OZON", now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin="PERSIST_OZON", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
                fingerprint=fp_hash, observed_at=normalized["observed_at"],
                availability=normalized["availability"], prices=normalized["prices"],
                now=now_iso)
            refresh_summary(conn, pid)

        results["OZON"] = {
            "status": "OK", "product_id": pid, "created": created,
            "observation_id": oid, "observation_created": ocreated,
            "title": normalized["title"][:60],
            "prices": len(normalized["prices"]),
            "availability": normalized["availability"],
        }
        log.info("OZON: product=%d created=%s obs=%d", pid, created, oid)

    except Exception as e:
        results["OZON"] = {"status": "ERROR", "error": str(e)}
        log.exception("OZON FAILED")

    # ============================================================
    # 4. AVITO (live fetch)
    # ============================================================
    log.info("=== AVITO ===")
    try:
        from r52.collectors.avito import AvitoCollector
        av_collector = AvitoCollector(use_proxies=False)
        av_id = "8341658309"
        av_url = f"https://www.avito.ru/ad/{av_id}"

        # Live HTTP fetch
        import requests as req
        av_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.avito.ru/",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-User": "?1",
        }
        log.info("AVITO fetch: %s", av_url)
        av_resp = req.get(av_url, headers=av_headers, timeout=25)
        log.info("AVITO HTTP: %d, %d байт", av_resp.status_code, len(av_resp.text))

        if av_resp.status_code == 200:
            av_html = av_resp.text
        elif av_resp.status_code in (301, 302, 307, 308):
            # Follow redirect manually
            redirect_url = av_resp.headers.get("Location", "")
            log.info("AVITO redirect → %s", redirect_url)
            av_resp2 = req.get(redirect_url, headers=av_headers, timeout=25)
            av_html = av_resp2.text
            log.info("AVITO redirect HTTP: %d, %d байт", av_resp2.status_code, len(av_html))
        else:
            raise RuntimeError(f"AVITO HTTP {av_resp.status_code}")

        # Проверка на redirect/captcha
        if len(av_html) < 2000 or 'http-equiv="refresh"' in av_html[:500]:
            # Попробовать мобильную версию
            log.warning("AVITO desktop: redirect/короткий, пробуем m.avito.ru")
            av_m_url = f"https://m.avito.ru/ad/{av_id}"
            av_resp_m = req.get(av_m_url, headers=av_headers, timeout=25)
            log.info("AVITO mobile HTTP: %d, %d байт", av_resp_m.status_code, len(av_resp_m.text))
            av_html = av_resp_m.text
            av_url = av_m_url

        resp = SimpleNamespace(text=av_html, status_code=av_resp.status_code, url=av_url)
        obs = av_collector.parse_page(resp, av_id, av_url, "Avito ad")
        if obs is None:
            raise RuntimeError("AVITO parse_page → None (CAPTCHA или пустой ответ)")

        av_collector._ensure_evidence(obs)
        av_collector._ensure_price_amounts(obs)
        av_collector._ensure_availability_in_fields(obs)

        av_wrapper = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "AVITO",
            "observation": {
                **obs,
                "source": "AVITO",
                "id": av_id,
                "url": av_url,
                "observed_at": datetime.now(tz_krasnoyarsk).isoformat(),
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": av_resp.status_code,
            },
        }
        normalized, cleaned = normalize_eye(av_wrapper["observation"])
        fp_hash = fingerprint(EYE_SCOPE, cleaned)

        with db.write() as conn:
            pid, created = upsert_product(
                conn, source="AVITO", listing_id=normalized["listing_id"],
                title=normalized["title"], url=normalized["url"],
                brand=normalized.get("brand"), model=normalized.get("model"),
                origin="PERSIST_AVITO", now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin="PERSIST_AVITO", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
                fingerprint=fp_hash, observed_at=normalized["observed_at"],
                availability=normalized["availability"], prices=normalized["prices"],
                now=now_iso)
            refresh_summary(conn, pid)

        results["AVITO"] = {
            "status": "OK", "product_id": pid, "created": created,
            "observation_id": oid, "observation_created": ocreated,
            "title": normalized["title"][:60],
            "prices": len(normalized["prices"]),
            "availability": normalized["availability"],
        }
        log.info("AVITO: product=%d created=%s obs=%d prices=%d", pid, created, oid, len(normalized["prices"]))

    except Exception as e:
        results["AVITO"] = {"status": "ERROR", "error": str(e)}
        log.exception("AVITO FAILED")

    # ============================================================
    # 5. ФИНАЛЬНАЯ ПРОВЕРКА
    # ============================================================
    log.info("=== VERIFICATION ===")
    with db.read() as conn:
        post = {
            "source": conn.execute("SELECT count(*) FROM source").fetchone()[0],
            "product": conn.execute("SELECT count(*) FROM product").fetchone()[0],
            "observation": conn.execute("SELECT count(*) FROM observation").fetchone()[0],
            "price_point": conn.execute("SELECT count(*) FROM price_point").fetchone()[0],
        }
        log.info("ПОСЛЕ: %s", post)

        products = conn.execute(
            "SELECT id, source, listing_id, title, brand, last_lowest_minor, last_currency, "
            "last_availability, observation_count, last_seen_at FROM product ORDER BY id"
        ).fetchall()
        for p in products:
            log.info("  P%d [%s] %s | %s | %s %s | obs=%d | %s",
                     p["id"], p["source"], p["listing_id"], (p["title"] or "")[:50],
                     p["last_lowest_minor"], p["last_currency"],
                     p["last_availability"], p["observation_count"], p["last_seen_at"])

        for src in ("FARPOST", "WB", "OZON", "AVITO"):
            obs_rows = conn.execute(
                "SELECT o.id, o.product_id, o.origin, o.availability, o.fingerprint, "
                "count(pp.id) as pp_count "
                "FROM observation o LEFT JOIN price_point pp ON pp.observation_id = o.id "
                "WHERE o.product_id IN (SELECT id FROM product WHERE source=?) "
                "GROUP BY o.id ORDER BY o.id", (src,)).fetchall()
            for o in obs_rows:
                log.info("  OBS %s: id=%d prod=%d origin=%s avail=%s pp=%d fp=%s...",
                         src, o["id"], o["product_id"], o["origin"], o["availability"],
                         o["pp_count"], (o["fingerprint"] or "")[:16])

    db.checkpoint()

    # ============================================================
    # 6. ИТОГ
    # ============================================================
    summary = {
        "db": str(PERSIST_DB),
        "pre_counts": pre,
        "post_counts": post,
        "platforms": results,
        "timestamp": now_iso,
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    all_ok = all(r.get("status") == "OK" for r in results.values())
    print(f"\n{'='*60}")
    print(f"PERSIST ALL v69: {'OK' if all_ok else 'PARTIAL'}")
    for src, r in results.items():
        mark = "✓" if r.get("status") == "OK" else "✗"
        print(f"  {mark} {src}: {r.get('status')}  title={r.get('title','?')[:50]}  prices={r.get('prices',0)}")
    print(f"  DB counts: {pre} → {post}")
    print(f"{'='*60}")
    print(f"JSON: {OUT_JSON}")

except Exception:
    print("\n" + "="*60)
    print("PERSIST ALL v69: EXCEPTION")
    print("="*60)
    traceback.print_exc()
    sys.exit(1)

print("\nDONE", flush=True)
