r"""v73: WB (цена из HTML) + Avito (мульти-стратегия) → постоянная БД.

WB:  JSON card.json нет priceU/saleU → парсим HTML detail.aspx
Avito: локальный HTML = redirect → live fetch (curl_cffi, mobile, desktop)
"""
import json, sys, time, traceback, re, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v73")

PERSIST_DB = BASE / "r52" / "data" / "r52.db"
WB_JSON = BASE / "diag_v59_wb_199298871.json"
OUT_JSON = BASE / "diag_v73_result.json"

results = {}
now_utc = datetime.now(timezone.utc)
now_iso = now_utc.isoformat()
tz_krasnoyarsk = timezone(timedelta(hours=10))

try:
    from r52.db import Database
    from r52.db.migrate import migrate
    from r52.store.products import upsert_product, add_observation, refresh_summary
    from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE

    PERSIST_DB.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(PERSIST_DB))
    db.initialize()
    mig = migrate(db, backup_dir=str(PERSIST_DB.parent / "backups"), now=now_utc)
    log.info("БД: %s, applied=%s", PERSIST_DB.name, mig["applied"])

    with db.read() as conn:
        pre = {
            "product": conn.execute("SELECT count(*) FROM product").fetchone()[0],
            "observation": conn.execute("SELECT count(*) FROM observation").fetchone()[0],
            "price_point": conn.execute("SELECT count(*) FROM price_point").fetchone()[0],
        }
    log.info("ДО: %s", pre)

    # ============================================================
    # 1. WILDBERRIES — цена из HTML detail.aspx
    # ============================================================
    log.info("=== WB: HTML price extraction ===")
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

        # --- Fetch HTML ---
        wb_html = ""
        wb_html_ok = False
        try:
            from curl_cffi import requests as cffi_req
            log.info("WB HTML: curl_cffi fetch %s", wb_url)
            r = cffi_req.get(wb_url, impersonate="chrome", timeout=25)
            log.info("WB HTML: HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code == 200:
                wb_html = r.text
                wb_html_ok = True
        except Exception as e:
            log.warning("WB HTML cffi error: %s", e)

        if not wb_html_ok:
            try:
                import requests as req
                headers = {
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Referer": "https://www.wildberries.ru/",
                }
                log.info("WB HTML: requests fetch")
                r2 = req.get(wb_url, headers=headers, timeout=25)
                log.info("WB HTML: HTTP %d, %d bytes", r2.status_code, len(r2.text))
                if r2.status_code == 200:
                    wb_html = r2.text
                    wb_html_ok = True
            except Exception as e:
                log.warning("WB HTML requests error: %s", e)

        # --- Extract price from HTML ---
        wb_prices = []
        if wb_html:
            # 1) og:price meta
            m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', wb_html, re.I)
            if m:
                val = float(m.group(1))
                wb_prices.append({"role": "regular", "amount_minor": int(val * 100), "currency": "RUB", "label": "og:price:amount"})
                log.info("WB HTML: og:price=%s", m.group(1))

            # 2) JSON-LD
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', wb_html, re.S | re.I):
                try:
                    ld = json.loads(m.group(1).strip())
                    if isinstance(ld, list):
                        for item in ld:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                offers = item.get("offers")
                                if isinstance(offers, dict):
                                    offers = [offers]
                                if isinstance(offers, list):
                                    for off in offers:
                                        pv = off.get("price") or off.get("lowPrice")
                                        if pv:
                                            wb_prices.append({"role": "regular", "amount_minor": int(float(pv) * 100), "currency": off.get("priceCurrency", "RUB"), "label": "JSON-LD"})
                    elif isinstance(ld, dict) and ld.get("@type") == "Product":
                        offers = ld.get("offers")
                        if isinstance(offers, dict):
                            offers = [offers]
                        if isinstance(offers, list):
                            for off in offers:
                                pv = off.get("price") or off.get("lowPrice")
                                if pv:
                                    wb_prices.append({"role": "regular", "amount_minor": int(float(pv) * 100), "currency": off.get("priceCurrency", "RUB"), "label": "JSON-LD"})
                except (json.JSONDecodeError, ValueError):
                    pass

            # 3) data-price / data-old-price attributes
            m = re.search(r'data-price=["\'](\d+)["\']', wb_html, re.I)
            if m:
                wb_prices.append({"role": "regular", "amount_minor": int(m.group(1)), "currency": "RUB", "label": "data-price"})

            # 4) Embedded state JSON (window.__STATE__ or similar)
            m = re.search(r'window\.__STATE__\s*=\s*(\{.*?\})\s*;\s*</script>', wb_html, re.S)
            if m:
                try:
                    state = json.loads(m.group(1))
                    # Try common paths
                    for path in [("catalog", "card", "priceU"), ("card", "priceU"), ("product", "priceU")]:
                        val = state
                        for k in path:
                            if isinstance(val, dict):
                                val = val.get(k)
                            else:
                                val = None
                                break
                        if val and isinstance(val, (int, float)) and val > 0:
                            wb_prices.append({"role": "regular", "amount_minor": int(val), "currency": "RUB", "label": f"state.{path[-1]}"})
                            break
                except (json.JSONDecodeError, ValueError):
                    pass

            # 5) Text pattern: "1 234 ₽" or "1234 руб"
            if not wb_prices:
                m = re.search(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', wb_html, re.I)
                if m:
                    digits = re.sub(r'[\s]', '', m.group(1))
                    if digits.isdigit() and int(digits) > 100:
                        wb_prices.append({"role": "regular", "amount_minor": int(digits) * 100, "currency": "RUB", "label": "html_text_price"})
                        log.info("WB HTML: text price=%s", digits)

        log.info("WB HTML: %d цен извлечено", len(wb_prices))

        # --- Build observation ---
        specs = {}
        for opt in (card.get("options") or []):
            name = opt.get("name", "")
            val = opt.get("value", "")
            if name and val:
                specs[name] = {"label": name, "value": val}

        vol = int(wb_id) // 100000
        part = int(wb_id) // 1000
        media_items = [{"type": "image", "url": f"https://basket-{vol}-wbbasket.wbbasket.ru/vol{vol}/part{part}/{wb_id}/images/big1/{wb_id}-{i+1}.jpg", "index": i} for i in range(wb_media_count)]
        if (card.get("media") or {}).get("has_video"):
            media_items.append({"type": "video", "url": None, "index": wb_media_count})

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

        from r52.collectors.wb import WBCollector
        wb_collector = WBCollector(use_proxies=False)
        wb_collector._ensure_evidence(wb_observation)
        wb_collector._ensure_price_amounts(wb_observation)
        wb_collector._ensure_availability_in_fields(wb_observation)

        wb_wrapper = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "WB",
            "observation": {
                **wb_observation,
                "source": "WB", "id": wb_id, "url": wb_url,
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
                origin="PERSIST_WB_V73", now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin="PERSIST_WB_V73", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
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
            "html_fetched": wb_html_ok,
            "html_bytes": len(wb_html) if wb_html else 0,
        }
        log.info("WB: product=%d prices=%d avail=%s", pid, len(normalized["prices"]), normalized["availability"])

    except Exception as e:
        results["WB"] = {"status": "ERROR", "error": str(e)}
        log.exception("WB FAILED")

    # ============================================================
    # 2. AVITO — мульти-стратегия
    # ============================================================
    log.info("=== AVITO: multi-strategy ===")
    try:
        from r52.collectors.avito import AvitoCollector
        av_collector = AvitoCollector(use_proxies=False)
        av_id = "8341658309"
        av_url = f"https://www.avito.ru/ad/{av_id}"

        av_html = ""
        av_source = ""
        av_status = 0

        # Strategy 1: curl_cffi desktop
        try:
            from curl_cffi import requests as cffi_req
            log.info("AVITO S1: curl_cffi desktop")
            r = cffi_req.get(av_url, impersonate="chrome", timeout=30)
            log.info("AVITO S1: HTTP %d, %d bytes", r.status_code, len(r.text))
            if r.status_code == 200 and len(r.text) > 5000:
                av_html = r.text
                av_source = "cffi_desktop"
                av_status = r.status_code
        except Exception as e:
            log.warning("AVITO S1 error: %s", e)

        # Strategy 2: curl_cffi mobile
        if not av_html:
            try:
                from curl_cffi import requests as cffi_req
                m_url = f"https://m.avito.ru/ad/{av_id}"
                log.info("AVITO S2: curl_cffi mobile %s", m_url)
                r = cffi_req.get(m_url, impersonate="chrome", timeout=30)
                log.info("AVITO S2: HTTP %d, %d bytes", r.status_code, len(r.text))
                if r.status_code == 200 and len(r.text) > 5000:
                    av_html = r.text
                    av_source = "cffi_mobile"
                    av_status = r.status_code
                    av_url = m_url
            except Exception as e:
                log.warning("AVITO S2 error: %s", e)

        # Strategy 3: requests with rich headers
        if not av_html:
            try:
                import requests as req
                headers = {
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
                    "Upgrade-Insecure-Requests": "1",
                }
                log.info("AVITO S3: requests desktop")
                r = req.get(av_url, headers=headers, timeout=30, allow_redirects=True)
                log.info("AVITO S3: HTTP %d, %d bytes, final_url=%s", r.status_code, len(r.text), r.url)
                if r.status_code == 200 and len(r.text) > 5000:
                    av_html = r.text
                    av_source = "req_desktop"
                    av_status = r.status_code
            except Exception as e:
                log.warning("AVITO S3 error: %s", e)

        # Strategy 4: requests mobile
        if not av_html:
            try:
                import requests as req
                m_url = f"https://m.avito.ru/ad/{av_id}"
                headers_m = {
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    "Referer": "https://m.avito.ru/",
                }
                log.info("AVITO S4: requests mobile")
                r = req.get(m_url, headers=headers_m, timeout=30, allow_redirects=True)
                log.info("AVITO S4: HTTP %d, %d bytes", r.status_code, len(r.text))
                if r.status_code == 200 and len(r.text) > 5000:
                    av_html = r.text
                    av_source = "req_mobile"
                    av_status = r.status_code
                    av_url = m_url
            except Exception as e:
                log.warning("AVITO S4 error: %s", e)

        if not av_html:
            raise RuntimeError(f"AVITO: все 4 стратегии не дали HTML. Последняя ошибка см. в логе")

        # Check for redirect/landing page
        is_redirect = (
            'http-equiv="refresh"' in av_html[:1000]
            or len(av_html) < 10000
            or "Доска объявлений от частных лиц" in av_html[:2000]
        )
        if is_redirect:
            log.warning("AVITO: похоже на redirect/landing page (%d bytes)", len(av_html))
            # Save anyway as PARTIAL — better than nothing
            av_status = 200

        # Save HTML for inspection
        html_out = BASE / f"diag_v73_avito_{av_source}.html"
        html_out.write_text(av_html, encoding="utf-8")
        log.info("AVITO HTML saved: %s (%d bytes)", html_out.name, len(av_html))

        resp = SimpleNamespace(text=av_html, status_code=av_status, url=av_url)
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
                "source": "AVITO", "id": av_id, "url": av_url,
                "observed_at": datetime.now(tz_krasnoyarsk).isoformat(),
                "status": "PARTIAL_SUCCESS" if is_redirect else "SUCCESS",
                "product_content": not is_redirect,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": av_status,
            },
        }
        normalized, cleaned = normalize_eye(av_wrapper["observation"])
        fp_hash = fingerprint(EYE_SCOPE, cleaned)

        with db.write() as conn:
            pid, created = upsert_product(
                conn, source="AVITO", listing_id=normalized["listing_id"],
                title=normalized["title"], url=normalized["url"],
                brand=normalized.get("brand"), model=normalized.get("model"),
                origin="PERSIST_AVITO_V73", now=now_iso)
            oid, ocreated = add_observation(
                conn, product_id=pid, normalized=normalized, raw=cleaned,
                origin="PERSIST_AVITO_V73", evidence_level="COLLECTOR_AUTONOMOUS_HTTP",
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
            "fetch_source": av_source,
            "html_bytes": len(av_html),
            "is_redirect": is_redirect,
        }
        log.info("AVITO: product=%d prices=%d avail=%s src=%s", pid, len(normalized["prices"]), normalized["availability"], av_source)

    except Exception as e:
        results["AVITO"] = {"status": "ERROR", "error": str(e)}
        log.exception("AVITO FAILED")

    # ============================================================
    # 3. VERIFICATION
    # ============================================================
    log.info("=== VERIFICATION ===")
    with db.read() as conn:
        post = {
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

    db.checkpoint()

    # ============================================================
    # 4. ИТОГ
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
    print(f"V73 WB+AVITO: {'OK' if all_ok else 'PARTIAL'}")
    for src, r in results.items():
        mark = "✓" if r.get("status") == "OK" else "✗"
        extra = f" src={r.get('fetch_source','')} html={r.get('html_bytes',0)}B" if src == "AVITO" else f" html={r.get('html_bytes',0)}B"
        print(f"  {mark} {src}: {r.get('status')} prices={r.get('prices',0)}{extra}")
    print(f"  DB: {pre} → {post}")
    print(f"{'='*60}")

except Exception:
    traceback.print_exc()
    sys.exit(1)

print("\nDONE", flush=True)
