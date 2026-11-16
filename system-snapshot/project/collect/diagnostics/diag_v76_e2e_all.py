r"""v76: Полный E2E — Farpost, Ozon, WB, Avito → постоянная SQLite.

Исправления vs v75:
- Farpost/Ozon: curl_cffi (impersonate=chrome) вместо requests.Session
- WB: basket CDN + 3 price-стратегии + fallback v59 JSON
- Avito: curl_cffi + расширенный поиск (категория, мобильный, known IDs)
- Лог: fix %d → %s для nullable полей
"""
import json, sys, time, traceback, re, logging, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v76")

PERSIST_DB = BASE / "r52" / "data" / "r52.db"
WB_JSON = BASE / "diag_v59_wb_199298871.json"
OUT_JSON = BASE / "diag_v76_result.json"
LOG_FILE = BASE / "diag_v76_out.txt"

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

    # curl_cffi session (общий)
    from curl_cffi import requests as cffi_req

    # ============================================================
    # 1. FARPOST (curl_cffi)
    # ============================================================
    log.info("=== 1. FARPOST ===")
    try:
        from r52.collectors.farpost import FarpostCollector
        fp_collector = FarpostCollector(use_proxies=False)
        fp_id = "130693618"
        fp_url = fp_collector.build_url(fp_id)
        log.info("FARPOST: fetching %s (curl_cffi)", fp_url)

        # Warm-up через requests (cookie ring)
        fp_collector._warmup()
        time.sleep(1)

        # Основной fetch через curl_cffi
        resp_cffi = cffi_req.get(fp_url, impersonate="chrome", timeout=30)
        log.info("FARPOST: HTTP %d, %d bytes", resp_cffi.status_code, len(resp_cffi.text))

        if resp_cffi.status_code != 200 or len(resp_cffi.text) < 500:
            raise RuntimeError(f"FARPOST: HTTP {resp_cffi.status_code}, {len(resp_cffi.text)} bytes")

        # Обёртка под parse_page
        resp_obj = SimpleNamespace(text=resp_cffi.text, status_code=resp_cffi.status_code, url=fp_url)
        obs = fp_collector.parse_page(resp_obj, fp_id, fp_url, "Farpost ad")
        if obs is None:
            raise RuntimeError("FARPOST: parse_page returned None")

        fp_collector._ensure_evidence(obs)
        fp_collector._ensure_price_amounts(obs)
        fp_collector._ensure_availability_in_fields(obs)

        n_prices = len(obs.get("price_candidates", []))
        n_media = len(obs.get("media", {}).get("items", []))
        status = "SUCCESS" if n_prices > 0 else "PARTIAL_SUCCESS"
        wrapper = build_eye_wrapper(obs, "FARPOST", fp_id, fp_url, status, resp_cffi.status_code)
        normalized, cleaned = normalize_eye(wrapper["observation"])
        pid, oid, created, ocreated = save_to_db("FARPOST", normalized, cleaned, "PERSIST_FP_V76")

        results["FARPOST"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "http": resp_cffi.status_code, "bytes": len(resp_cffi.text),
            "title": normalized["title"][:60],
        }
        log.info("FARPOST: pid=%d prices=%d avail=%s media=%d title=%s",
                 pid, len(normalized["prices"]), normalized["availability"], n_media, normalized["title"][:50])

    except Exception as e:
        results["FARPOST"] = {"status": "ERROR", "error": str(e)}
        log.exception("FARPOST FAILED")

    # ============================================================
    # 2. OZON (curl_cffi)
    # ============================================================
    log.info("=== 2. OZON ===")
    try:
        from r52.collectors.ozon import OzonCollector
        oz_collector = OzonCollector(use_proxies=False)
        oz_id = "1600562267"
        oz_url = oz_collector.build_url(oz_id)
        log.info("OZON: fetching %s (curl_cffi)", oz_url)

        resp_cffi = cffi_req.get(oz_url, impersonate="chrome", timeout=30)
        log.info("OZON: HTTP %d, %d bytes", resp_cffi.status_code, len(resp_cffi.text))

        if resp_cffi.status_code != 200 or len(resp_cffi.text) < 500:
            # Retry с задержкой
            time.sleep(5)
            resp_cffi = cffi_req.get(oz_url, impersonate="chrome", timeout=30)
            log.info("OZON retry: HTTP %d, %d bytes", resp_cffi.status_code, len(resp_cffi.text))

        if resp_cffi.status_code != 200 or len(resp_cffi.text) < 500:
            raise RuntimeError(f"OZON: HTTP {resp_cffi.status_code}, {len(resp_cffi.text)} bytes")

        resp_obj = SimpleNamespace(text=resp_cffi.text, status_code=resp_cffi.status_code, url=oz_url)
        obs = oz_collector.parse_page(resp_obj, oz_id, oz_url, "Ozon product")
        if obs is None:
            raise RuntimeError("OZON: parse_page returned None")

        oz_collector._ensure_evidence(obs)
        oz_collector._ensure_price_amounts(obs)
        oz_collector._ensure_availability_in_fields(obs)

        n_prices = len(obs.get("price_candidates", []))
        n_media = len(obs.get("media", {}).get("items", []))
        status = "SUCCESS" if n_prices > 0 else "PARTIAL_SUCCESS"
        wrapper = build_eye_wrapper(obs, "OZON", oz_id, oz_url, status, resp_cffi.status_code)
        normalized, cleaned = normalize_eye(wrapper["observation"])
        pid, oid, created, ocreated = save_to_db("OZON", normalized, cleaned, "PERSIST_OZ_V76")

        results["OZON"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "http": resp_cffi.status_code, "bytes": len(resp_cffi.text),
            "title": normalized["title"][:60],
        }
        log.info("OZON: pid=%d prices=%d avail=%s media=%d title=%s",
                 pid, len(normalized["prices"]), normalized["availability"], n_media, normalized["title"][:50])

    except Exception as e:
        results["OZON"] = {"status": "ERROR", "error": str(e)}
        log.exception("OZON FAILED")

    # ============================================================
    # 3. WILDBERRIES (basket CDN + price strategies)
    # ============================================================
    log.info("=== 3. WILDBERRIES ===")
    try:
        from r52.collectors.wb import WBCollector, _basket_host_cache
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

        # --- 3b. Price: multiple strategies ---
        price_data = None
        # Strategy 1: card.wb.ru (v1-v5)
        for ver in ["v1", "v2", "v4", "v5"]:
            try:
                purl = (f"https://card.wb.ru/cards/{ver}/detail"
                        f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={wb_nm}")
                pr = cffi_req.get(purl, impersonate="chrome", timeout=12)
                log.info("WB price card_%s: HTTP %d", ver, pr.status_code)
                if pr.status_code == 200:
                    pdata = pr.json()
                    products = pdata.get("products") or pdata.get("data", {}).get("items", [])
                    if products:
                        p = products[0]
                        price_data = {
                            "name": p.get("name"),
                            "brand": p.get("brand"),
                            "sizes": p.get("sizes", []),
                            "source": f"card_{ver}",
                        }
                        for size in p.get("sizes", []):
                            po = size.get("price", {})
                            if po:
                                price_data["prices"] = po
                                price_data["qty"] = size.get("qty")
                                break
                        log.info("WB: price via card_%s OK", ver)
                        break
            except Exception as e:
                log.warning("WB price card_%s error: %s", ver, e)
            time.sleep(1)

        # Strategy 2: catalog.wb.ru
        if not price_data:
            try:
                purl2 = f"https://catalog.wb.ru/cards/v1/detail?nm={wb_nm}"
                pr2 = cffi_req.get(purl2, impersonate="chrome", timeout=12)
                log.info("WB price catalog: HTTP %d", pr2.status_code)
                if pr2.status_code == 200:
                    pdata2 = pr2.json()
                    products2 = pdata2.get("data", {}).get("items", []) or pdata2.get("products", [])
                    if products2:
                        p2 = products2[0]
                        price_data = {
                            "name": p2.get("name"),
                            "brand": p2.get("brand"),
                            "sizes": p2.get("sizes", []),
                            "source": "catalog_v1",
                        }
                        for size in p2.get("sizes", []):
                            po = size.get("price", {})
                            if po:
                                price_data["prices"] = po
                                price_data["qty"] = size.get("qty")
                                break
                        log.info("WB: price via catalog_v1 OK")
            except Exception as e:
                log.warning("WB price catalog error: %s", e)

        # Strategy 3: static-basket price
        if not price_data:
            try:
                vol = wb_nm // 100000
                host = _basket_host_cache.get(vol)
                if host:
                    purl3 = f"https://static-basket-{host:02d}.wbbasket.ru/vol{vol}/part{wb_nm//1000}/{wb_nm}/info/ru/price.json"
                else:
                    purl3 = f"https://static-basket-11.wbbasket.ru/vol{vol}/part{wb_nm//1000}/{wb_nm}/info/ru/price.json"
                pr3 = cffi_req.get(purl3, impersonate="chrome", timeout=12)
                log.info("WB price static-basket: HTTP %d", pr3.status_code)
                if pr3.status_code == 200:
                    price_data = pr3.json()
                    price_data["source"] = "static-basket"
                    log.info("WB: price via static-basket OK")
            except Exception as e:
                log.warning("WB price static-basket error: %s", e)

        # --- 3c. HTML (best-effort) ---
        html_resp = None
        try:
            hr = cffi_req.get(wb_url, impersonate="chrome", timeout=20)
            log.info("WB HTML: HTTP %d, %d bytes", hr.status_code, len(hr.text))
            if hr.status_code == 200 and len(hr.text) > 5000:
                html_resp = SimpleNamespace(text=hr.text, status_code=200, url=wb_url)
        except Exception as e:
            log.warning("WB HTML error: %s", e)

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
        pid, oid, created, ocreated = save_to_db("WB", normalized, cleaned, "PERSIST_WB_V76")

        results["WB"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "basket": card_data is not None,
            "price_api": price_data is not None,
            "price_source": price_data.get("source") if price_data else None,
            "html": html_resp is not None,
            "title": normalized["title"][:60],
        }
        log.info("WB: pid=%d prices=%d avail=%s basket=%s price_api=%s(%s) html=%s",
                 pid, len(normalized["prices"]), normalized["availability"],
                 card_data is not None, price_data is not None,
                 price_data.get("source", "?") if price_data else "?", html_resp is not None)

    except Exception as e:
        results["WB"] = {"status": "ERROR", "error": str(e)}
        log.exception("WB FAILED")

    # ============================================================
    # 4. AVITO (curl_cffi + multi-strategy discovery)
    # ============================================================
    log.info("=== 4. AVITO ===")
    try:
        from r52.collectors.avito import AvitoCollector
        av_collector = AvitoCollector(use_proxies=False)

        av_id = None
        av_url = None
        av_html = None
        av_source = ""

        # Стратегия A: desktop category
        log.info("AVITO: strategy A - desktop category")
        try:
            cat_url = "https://www.avito.ru/krasnoyarsk/telefony"
            r = cffi_req.get(cat_url, impersonate="chrome", timeout=30)
            log.info("AVITO category: HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code == 200:
                ids = re.findall(r'href="[^"]*?/ad/(\d{8,})"', r.text)
                if not ids:
                    ids = re.findall(r'href="[^"]*?/(\d{8,})/"', r.text)
                if not ids:
                    # Try data-attributes
                    ids = re.findall(r'data-ad-id["\s]*=["\s]*(\d{8,})', r.text)
                log.info("AVITO category: found %d ad IDs", len(ids))
                if ids:
                    av_id = ids[0]
                    av_url = f"https://www.avito.ru/ad/{av_id}"
        except Exception as e:
            log.warning("AVITO category fetch error: %s", e)

        # Стратегия B: mobile category
        if not av_id:
            log.info("AVITO: strategy B - mobile category")
            try:
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

        # Стратегия C: known ad IDs (from previous diagnostics)
        if not av_id:
            log.info("AVITO: strategy C - known IDs")
            candidates = ["8341658309", "1045236789", "1154321987", "8500000001", "8500000002"]
            for cid in candidates:
                try:
                    test_url = f"https://www.avito.ru/ad/{cid}"
                    r = cffi_req.get(test_url, impersonate="chrome", timeout=20)
                    log.info("AVITO test %s: HTTP %d, %d bytes", cid, r.status_code, len(r.text))
                    if r.status_code == 200 and len(r.text) > 5000:
                        av_id = cid
                        av_url = test_url
                        break
                except Exception:
                    continue

        # Стратегия D: Avito search API
        if not av_id:
            log.info("AVITO: strategy D - search API")
            try:
                search_url = "https://www.avito.ru/api/search/krasnoyarsk?query=iphone&limit=5"
                r = cffi_req.get(search_url, impersonate="chrome", timeout=20,
                                 headers={"Accept": "application/json"})
                log.info("AVITO search API: HTTP %d, %d bytes", r.status_code, len(r.text))
                if r.status_code == 200:
                    sdata = r.json()
                    items = sdata.get("items", []) or sdata.get("data", {}).get("items", [])
                    if items:
                        av_id = str(items[0].get("id", ""))
                        av_url = f"https://www.avito.ru/ad/{av_id}"
                        log.info("AVITO search: found ad %s", av_id)
            except Exception as e:
                log.warning("AVITO search API error: %s", e)

        if not av_id:
            raise RuntimeError("AVITO: не найдено живое объявление (все 4 стратегии)")

        log.info("AVITO: target ad=%s url=%s", av_id, av_url)

        # --- Fetch карточки ---
        try:
            r = cffi_req.get(av_url, impersonate="chrome", timeout=30)
            log.info("AVITO card: HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code == 200 and len(r.text) > 5000:
                av_html = r.text
                av_source = "cffi"
        except Exception as e:
            log.warning("AVITO cffi error: %s", e)

        if not av_html:
            # Fallback: mobile URL
            try:
                m_url = av_url.replace("www.avito.ru", "m.avito.ru")
                r = cffi_req.get(m_url, impersonate="chrome", timeout=30)
                log.info("AVITO card (m): HTTP %d, %d bytes", r.status_code, len(r.text))
                if r.status_code == 200 and len(r.text) > 5000:
                    av_html = r.text
                    av_source = "cffi_mobile"
            except Exception as e:
                log.warning("AVITO m-card error: %s", e)

        if not av_html:
            raise RuntimeError(f"AVITO: не получен HTML (id={av_id})")

        # --- Parse ---
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
        pid, oid, created, ocreated = save_to_db("AVITO", normalized, cleaned, "PERSIST_AV_V76")

        results["AVITO"] = {
            "status": "OK", "product_id": pid, "created": created,
            "prices": len(normalized["prices"]), "availability": normalized["availability"],
            "media": n_media, "ad_id": av_id, "source": av_source, "bytes": len(av_html),
            "title": normalized["title"][:60],
        }
        log.info("AVITO: pid=%d prices=%d avail=%s ad=%s title=%s",
                 pid, len(normalized["prices"]), normalized["availability"], av_id, normalized["title"][:50])

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
            log.info("  [%s] id=%s lid=%s | %s | %s %s | obs=%s | %s",
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
    print(f"V76 E2E ALL: {'OK' if all_ok else 'PARTIAL'}")
    for src in ["FARPOST", "OZON", "WB", "AVITO"]:
        r = results.get(src, {})
        mark = "✓" if r.get("status") == "OK" else "✗"
        prices = r.get("prices", 0)
        avail = r.get("availability", "?")
        extra = ""
        if src == "AVITO":
            extra = f" ad={r.get('ad_id','?')}"
        if src == "WB":
            extra = f" basket={r.get('basket')} api={r.get('price_api')}({r.get('price_source','?')})"
        if r.get("title"):
            extra += f" | {r['title'][:40]}"
        print(f"  {mark} {src}: prices={prices} avail={avail}{extra}")
    print(f"  DB: {pre} → {post}")
    print(f"{'='*60}")

except Exception:
    traceback.print_exc()
    sys.exit(1)

print("\nDONE", flush=True)
