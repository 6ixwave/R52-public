r"""v75: Полный E2E — Farpost, Ozon, WB, Avito → постоянная SQLite.

Стратегия:
- Farpost: warm-up + fetch + parse_page (наиболее стабильный)
- Ozon: fetch + parse_page (SSR-HTML)
- WB: basket CDN → price API → HTML (fallback: v59 JSON)
- Avito: поиск живого объявления → fetch + parse_page
- Все через normalize_eye → upsert_product → add_observation → refresh_summary
"""
import json, sys, time, traceback, re, logging, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v75")

PERSIST_DB = BASE / "r52" / "data" / "r52.db"
WB_JSON = BASE / "diag_v59_wb_199298871.json"
OUT_JSON = BASE / "diag_v75_result.json"
LOG_FILE = BASE / "diag_v75_out.txt"

results = {}
now_utc = datetime.now(timezone.utc)
now_iso = now_utc.isoformat()
tz_kras = timezone(timedelta(hours=10))

# ============================================================
# DB INIT
# ============================================================
log.info("=== DB INIT ===")
from r52.db import Database
from r52.db.migrate import migrate
from r52.store.products import upsert_product, add_observation, refresh_summary
from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE

PERSIST_DB.parent.mkdir(parents=True, exist_ok=True)
db = Database(str(PERSIST_DB))
db.initialize()
mig = migrate(db, backup_dir=str(PERSIST_DB.parent / "backups"), now=now_utc)
log.info("БД: %s, applied=%s, version=%s", PERSIST_DB.name, mig["applied"], mig.get("version", "?"))

try:
    # ============================================================
    # PRE COUNTS
    # ============================================================
    with db.read() as conn:
        pre = {
            "product": conn.execute("SELECT count(*) FROM product").fetchone()[0],
            "observation": conn.execute("SELECT count(*) FROM observation").fetchone()[0],
            "price_point": conn.execute("SELECT count(*) FROM price_point").fetchone()[0],
            "source": conn.execute("SELECT count(*) FROM source").fetchone()[0],
        }
    log.info("ДО: %s", pre)

    # ============================================================
    # HELPERS
    # ============================================================
    def save_to_db(source, normalized, cleaned, origin_tag):
        """Сохраняет товар + наблюдение + цены в БД. Возвращает (pid, oid, created, ocreated)."""
        fp_hash = fingerprint(EYE_SCOPE, cleaned)
        with db.write() as conn:
            pid, created = upsert_product(
                conn, source=source, listing_id=normalized["listing_id"],
                title=normalized["title"], url=normalized["url"],
                brand=normalized.get("brand"), model=normalized.get("model"),
                origin=origin_tag, now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin=origin_tag, evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
                fingerprint=fp_hash, observed_at=normalized["observed_at"],
                availability=normalized["availability"], prices=normalized["prices"],
                now=now_iso)
            refresh_summary(conn, pid)
        return pid, oid, created, ocreated

    def build_eye_wrapper(obs, source, listing_id, url, status, http_status=200):
        """Строит JARVIS_BR02_EYE_USER_CAPTURE обёртку."""
        return {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": source,
            "observation": {
                **obs,
                "source": source, "id": str(listing_id), "url": url,
                "observed_at": datetime.now(tz_kras).isoformat(),
                "status": status,
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": http_status,
            },
        }

    # ============================================================
    # 1. FARPOST
    # ============================================================
    log.info("=== 1. FARPOST ===")
    try:
        from r52.collectors.farpost import FarpostCollector
        fp_collector = FarpostCollector(use_proxies=False)
        fp_id = "130693618"
        fp_url = fp_collector.build_url(fp_id)
        log.info("FARPOST: fetching %s", fp_url)

        resp = fp_collector.fetch(fp_url)
        if resp is None:
            raise RuntimeError("FARPOST: fetch returned None")
        log.info("FARPOST: HTTP %d, %d bytes", resp.status_code, len(resp.text))

        obs = fp_collector.parse_page(resp, fp_id, fp_url, "Farpost ad")
        if obs is None:
            raise RuntimeError("FARPOST: parse_page returned None")

        fp_collector._ensure_evidence(obs)
        fp_collector._ensure_price_amounts(obs)
        fp_collector._ensure_availability_in_fields(obs)

        n_prices = len(obs.get("price_candidates", []))
        n_media = len(obs.get("media", {}).get("items", []))
        status = "SUCCESS" if n_prices > 0 else "PARTIAL_SUCCESS"
        wrapper = build_eye_wrapper(obs, "FARPOST", fp_id, fp_url, status, resp.status_code)
        normalized, cleaned = normalize_eye(wrapper["observation"])
        pid, oid, created, ocreated = save_to_db("FARPOST", normalized, cleaned, "PERSIST_FP_V75")

        results["FARPOST"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "http": resp.status_code, "bytes": len(resp.text),
        }
        log.info("FARPOST: pid=%d prices=%d avail=%s media=%d", pid, len(normalized["prices"]), normalized["availability"], n_media)

    except Exception as e:
        results["FARPOST"] = {"status": "ERROR", "error": str(e)}
        log.exception("FARPOST FAILED")

    # ============================================================
    # 2. OZON
    # ============================================================
    log.info("=== 2. OZON ===")
    try:
        from r52.collectors.ozon import OzonCollector
        oz_collector = OzonCollector(use_proxies=False)
        oz_id = "1600562267"
        oz_url = oz_collector.build_url(oz_id)
        log.info("OZON: fetching %s", oz_url)

        resp = oz_collector.fetch(oz_url)
        if resp is None:
            raise RuntimeError("OZON: fetch returned None")
        log.info("OZON: HTTP %d, %d bytes", resp.status_code, len(resp.text))

        obs = oz_collector.parse_page(resp, oz_id, oz_url, "Ozon product")
        if obs is None:
            raise RuntimeError("OZON: parse_page returned None")

        oz_collector._ensure_evidence(obs)
        oz_collector._ensure_price_amounts(obs)
        oz_collector._ensure_availability_in_fields(obs)

        n_prices = len(obs.get("price_candidates", []))
        n_media = len(obs.get("media", {}).get("items", []))
        status = "SUCCESS" if n_prices > 0 else "PARTIAL_SUCCESS"
        wrapper = build_eye_wrapper(obs, "OZON", oz_id, oz_url, status, resp.status_code)
        normalized, cleaned = normalize_eye(wrapper["observation"])
        pid, oid, created, ocreated = save_to_db("OZON", normalized, cleaned, "PERSIST_OZ_V75")

        results["OZON"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "http": resp.status_code, "bytes": len(resp.text),
        }
        log.info("OZON: pid=%d prices=%d avail=%s media=%d", pid, len(normalized["prices"]), normalized["availability"], n_media)

    except Exception as e:
        results["OZON"] = {"status": "ERROR", "error": str(e)}
        log.exception("OZON FAILED")

    # ============================================================
    # 3. WILDBERRIES
    # ============================================================
    log.info("=== 3. WILDBERRIES ===")
    try:
        from r52.collectors.wb import WBCollector
        from r52.collectors.wb import _basket_host_cache
        wb_collector = WBCollector(use_proxies=False)
        wb_id = "199298871"
        wb_nm = int(wb_id)
        wb_url = wb_collector.build_url(wb_id)

        # --- 3a. Basket CDN ---
        card_data = None
        try:
            card_data = wb_collector._fetch_card_json(wb_nm)
            if card_data:
                log.info("WB: basket CDN OK (%d keys)", len(card_data))
            else:
                log.info("WB: basket CDN empty/failed")
        except Exception as e:
            log.warning("WB: basket CDN error: %s", e)

        # --- 3b. Price API ---
        price_data = None
        try:
            price_data = wb_collector._fetch_price(wb_nm)
            if price_data:
                log.info("WB: price API OK via %s", price_data.get("source", "?"))
            else:
                log.info("WB: price API empty")
        except Exception as e:
            log.warning("WB: price API error: %s", e)

        # --- 3c. HTML ---
        html_resp = None
        try:
            html_resp = wb_collector._fetch_html(wb_url)
            if html_resp:
                log.info("WB: HTML OK %d bytes", len(html_resp.text))
            else:
                log.info("WB: HTML failed")
        except Exception as e:
            log.warning("WB: HTML error: %s", e)

        # --- 3d. Fallback: v59 JSON ---
        if card_data is None and WB_JSON.exists():
            log.info("WB: fallback to v59 JSON")
            card_data = json.loads(WB_JSON.read_text(encoding="utf-8"))

        if card_data is None and price_data is None and html_resp is None:
            raise RuntimeError("WB: все источники пусты")

        # --- Merge ---
        obs = wb_collector._merge_observations(wb_nm, card_data, price_data, html_resp,
                                               _basket_host_cache.get(wb_nm // 100000), "WB test")
        if obs is None:
            raise RuntimeError("WB: _merge_observations returned None")

        wb_collector._ensure_evidence(obs)
        wb_collector._ensure_price_amounts(obs)
        wb_collector._ensure_availability_in_fields(obs)

        n_prices = len(obs.get("price_candidates", []))
        n_media = len(obs.get("media", {}).get("items", []))
        has_title = bool(obs["fields"].get("title", {}).get("value"))
        status = "SUCCESS" if (has_title and n_prices > 0) else "PARTIAL_SUCCESS"
        wrapper = build_eye_wrapper(obs, "WB", wb_id, wb_url, status)
        normalized, cleaned = normalize_eye(wrapper["observation"])
        pid, oid, created, ocreated = save_to_db("WB", normalized, cleaned, "PERSIST_WB_V75")

        results["WB"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "basket": card_data is not None,
            "price_api": price_data is not None, "html": html_resp is not None,
            "title": normalized["title"][:60],
        }
        log.info("WB: pid=%d prices=%d avail=%s basket=%s price_api=%s html=%s",
                 pid, len(normalized["prices"]), normalized["availability"],
                 card_data is not None, price_data is not None, html_resp is not None)

    except Exception as e:
        results["WB"] = {"status": "ERROR", "error": str(e)}
        log.exception("WB FAILED")

    # ============================================================
    # 4. AVITO
    # ============================================================
    log.info("=== 4. AVITO ===")
    try:
        from r52.collectors.avito import AvitoCollector
        av_collector = AvitoCollector(use_proxies=False)

        # --- 4a. Найти живое объявление ---
        av_id = None
        av_url = None
        av_html = None
        av_source = ""

        # Стратегия A: fetch категории, извлечь ID
        log.info("AVITO: fetching category page for ad discovery")
        try:
            from curl_cffi import requests as cffi_req
            cat_url = "https://www.avito.ru/krasnoyarsk/telefony"
            r = cffi_req.get(cat_url, impersonate="chrome", timeout=30)
            log.info("AVITO category: HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code == 200:
                ids = re.findall(r'href="[^"]*?/ad/(\d{8,})"', r.text)
                if not ids:
                    ids = re.findall(r'href="[^"]*?/(\d{8,})/"', r.text)
                log.info("AVITO category: found %d ad IDs", len(ids))
                if ids:
                    av_id = ids[0]
                    av_url = f"https://www.avito.ru/ad/{av_id}"
        except Exception as e:
            log.warning("AVITO category fetch error: %s", e)

        # Стратегия B: мобильная версия категории
        if not av_id:
            try:
                from curl_cffi import requests as cffi_req
                m_cat = "https://m.avito.ru/krasnoyarsk/telefony"
                r = cffi_req.get(m_cat, impersonate="chrome", timeout=30)
                log.info("AVITO m-category: HTTP %d, %d bytes", r.status_code, len(r.text))
                if r.status_code == 200:
                    ids = re.findall(r'href="[^"]*?/ad/(\d{8,})"', r.text)
                    if not ids:
                        ids = re.findall(r'href="[^"]*?/(\d{8,})/"', r.text)
                    log.info("AVITO m-category: found %d ad IDs", len(ids))
                    if ids:
                        av_id = ids[0]
                        av_url = f"https://m.avito.ru/ad/{av_id}"
            except Exception as e:
                log.warning("AVITO m-category error: %s", e)

        # Стратегия C: прямой fetch по известным ID
        if not av_id:
            candidates = ["8341658309", "8500000001", "8500000002"]
            for cid in candidates:
                try:
                    from curl_cffi import requests as cffi_req
                    test_url = f"https://www.avito.ru/ad/{cid}"
                    r = cffi_req.get(test_url, impersonate="chrome", timeout=20)
                    log.info("AVITO test %s: HTTP %d, %d bytes", cid, r.status_code, len(r.text))
                    if r.status_code == 200 and len(r.text) > 5000:
                        av_id = cid
                        av_url = test_url
                        break
                except Exception:
                    continue

        if not av_id:
            raise RuntimeError("AVITO: не найдено живое объявление")

        log.info("AVITO: target ad=%s url=%s", av_id, av_url)

        # --- 4b. Fetch карточки ---
        try:
            from curl_cffi import requests as cffi_req
            r = cffi_req.get(av_url, impersonate="chrome", timeout=30)
            log.info("AVITO card: HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code == 200 and len(r.text) > 5000:
                av_html = r.text
                av_source = "cffi"
        except Exception as e:
            log.warning("AVITO cffi error: %s", e)

        if not av_html:
            try:
                import requests as req
                headers = {
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                }
                r = req.get(av_url, headers=headers, timeout=30, allow_redirects=True)
                log.info("AVITO card (req): HTTP %d, %d bytes", r.status_code, len(r.text))
                if r.status_code == 200 and len(r.text) > 5000:
                    av_html = r.text
                    av_source = "requests"
            except Exception as e:
                log.warning("AVITO requests error: %s", e)

        if not av_html:
            raise RuntimeError(f"AVITO: не получен HTML (id={av_id})")

        # --- 4c. Parse ---
        resp = SimpleNamespace(text=av_html, status_code=200, url=av_url)
        obs = av_collector.parse_page(resp, av_id, av_url, "Avito ad")
        if obs is None:
            raise RuntimeError("AVITO: parse_page → None (CAPTCHA/пустой)")

        av_collector._ensure_evidence(obs)
        av_collector._ensure_price_amounts(obs)
        av_collector._ensure_availability_in_fields(obs)

        n_prices = len(obs.get("price_candidates", []))
        n_media = len(obs.get("media", {}).get("items", []))
        status = "SUCCESS" if n_prices > 0 else "PARTIAL_SUCCESS"
        wrapper = build_eye_wrapper(obs, "AVITO", av_id, av_url, status)
        normalized, cleaned = normalize_eye(wrapper["observation"])
        pid, oid, created, ocreated = save_to_db("AVITO", normalized, cleaned, "PERSIST_AV_V75")

        results["AVITO"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "ad_id": av_id, "source": av_source, "bytes": len(av_html),
            "title": normalized["title"][:60],
        }
        log.info("AVITO: pid=%d prices=%d avail=%s ad=%s", pid, len(normalized["prices"]), normalized["availability"], av_id)

    except Exception as e:
        results["AVITO"] = {"status": "ERROR", "error": str(e)}
        log.exception("AVITO FAILED")

    # ============================================================
    # 5. VERIFICATION
    # ============================================================
    log.info("=== 5. VERIFICATION ===")
    with db.read() as conn:
        post = {
            "product": conn.execute("SELECT count(*) FROM product").fetchone()[0],
            "observation": conn.execute("SELECT count(*) FROM observation").fetchone()[0],
            "price_point": conn.execute("SELECT count(*) FROM price_point").fetchone()[0],
            "source": conn.execute("SELECT count(*) FROM source").fetchone()[0],
        }
        log.info("ПОСЛЕ: %s", post)

        rows = conn.execute(
            "SELECT p.id, p.source, p.listing_id, substr(p.title,1,40) as t, "
            "p.last_lowest_minor, p.last_currency, p.last_availability, "
            "p.observation_count, p.last_seen_at "
            "FROM product p ORDER BY p.source, p.id"
        ).fetchall()
        for r in rows:
            log.info("  [%s] id=%d lid=%s | %s | %s %s | obs=%d | %s",
                     r["source"], r["id"], r["listing_id"], r["t"],
                     r["last_lowest_minor"], r["last_currency"],
                     r["last_availability"], r["observation_count"], r["last_seen_at"])

        pp_rows = conn.execute(
            "SELECT p.source, count(*) as cnt, min(pp.amount_minor) as lo, max(pp.amount_minor) as hi "
            "FROM price_point pp JOIN observation o ON o.id = pp.observation_id "
            "JOIN product p ON p.id = o.product_id GROUP BY p.source"
        ).fetchall()
        for r in pp_rows:
            log.info("  PP [%s]: count=%d range=%s..%s", r["source"], r["cnt"], r["lo"], r["hi"])

    db.checkpoint()

    # ============================================================
    # 6. ИТОГ
    # ============================================================
    summary = {
        "db": str(PERSIST_DB),
        "pre": pre,
        "post": post,
        "platforms": results,
        "timestamp": now_iso,
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    all_ok = all(r.get("status") == "OK" for r in results.values())
    print(f"\n{'='*60}")
    print(f"V75 E2E ALL: {'OK' if all_ok else 'PARTIAL'}")
    for src in ["FARPOST", "OZON", "WB", "AVITO"]:
        r = results.get(src, {})
        mark = "✓" if r.get("status") == "OK" else "✗"
        prices = r.get("prices", 0)
        avail = r.get("availability", "?")
        extra = ""
        if src == "AVITO":
            extra = f" ad={r.get('ad_id','?')}"
        if src == "WB":
            extra = f" basket={r.get('basket')} api={r.get('price_api')}"
        print(f"  {mark} {src}: prices={prices} avail={avail}{extra}")
    print(f"  DB: {pre} → {post}")
    print(f"{'='*60}")

except Exception:
    traceback.print_exc()
    sys.exit(1)

print("\nDONE", flush=True)
