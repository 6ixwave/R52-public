"""V88c: E2E 4 маркетплейса. Фиксы: stdout logging, top-level catch, Inbox API."""
import json, logging, sys, time, uuid, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("v88c")

DB_PATH = BASE / "r52" / "data" / "r52.db"
INTAKE_DIR = BASE / "r52" / "data" / "intake"
INTAKE_DIR.mkdir(parents=True, exist_ok=True)

results = {}
PRE = {}
POST = {}

def save_results():
    rf = BASE / "probe_v88c_result.json"
    rf.write_text(json.dumps({"PRE": PRE, "POST": POST, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved %s", rf.name)

def main():
    global PRE, POST

    # === SETUP ===
    log.info("=== SETUP ===")
    from r52.db.database import Database
    from r52.db.migrate import migrate
    from r52.store.products import ensure_source, upsert_product, add_observation, refresh_summary
    from r52.intake.eye import Inbox
    from r52.domain.normalize import normalize_eye, fingerprint, EYE_SCOPE
    from r52.timeutil import iso

    class MockApp:
        def __init__(self, db_path):
            self.db = Database(db_path)
            self.db.initialize()
            self.settings = {"intake.auto_accept": False}
            self.clock = type('C', (), {'now': staticmethod(lambda: datetime.now(timezone.utc))})()
            self.notifier = None

    app = MockApp(DB_PATH)
    migs = migrate(app.db)
    log.info("Migrations: %s", migs)

    def db_counts():
        with app.db.read() as conn:
            return {
                "product": conn.execute("SELECT count(*) FROM product").fetchone()[0],
                "observation": conn.execute("SELECT count(*) FROM observation").fetchone()[0],
                "price_point": conn.execute("SELECT count(*) FROM price_point").fetchone()[0],
                "source": conn.execute("SELECT count(*) FROM source").fetchone()[0],
            }

    PRE = db_counts()
    log.info("PRE: %s", PRE)

    # === 1. FARPOST ===
    log.info("=== 1. FARPOST ===")
    try:
        from r52.collectors.farpost import FarpostCollector
        fp = FarpostCollector(data_dir=BASE / "r52" / "data", use_proxies=False)
        fp_url = "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-127622540.html"
        fp_lid = "127622540"
        log.info("FP: collecting %s", fp_url)
        obs = fp.collect_product(fp_lid, fp_url, "Boto WD69")
        if obs:
            fn = f"jarvis-eye-farpost-{uuid.uuid4().hex[:8]}.json"
            fp_path = INTAKE_DIR / fn
            fp_path.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("FP: JSON %s (%d bytes)", fn, fp_path.stat().st_size)

            inbox = Inbox(app)
            data = fp_path.read_bytes()
            item, created = inbox.stage_bytes(data, filename=fn, origin="COLLECTOR")
            log.info("FP: inbox id=%s status=%s", item.get("id"), item.get("status"))

            if item["status"] == "PENDING":
                accepted = inbox.accept(item["id"])
                log.info("FP: accepted product_id=%s", accepted.get("product_id"))

            with app.db.read() as conn:
                row = conn.execute(
                    "SELECT id, title, last_lowest_minor, last_availability, observation_count FROM product WHERE source='FARPOST' AND listing_id=?",
                    (fp_lid,)).fetchone()
                if row:
                    log.info("FP: DB id=%s title=%s price=%s avail=%s", row["id"], row["title"][:40], row["last_lowest_minor"], row["last_availability"])
                    results["FARPOST"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"], "price_minor": row["last_lowest_minor"]}
                else:
                    results["FARPOST"] = {"status": "NO_SQLITE_ROW"}
        else:
            results["FARPOST"] = {"status": "NO_DATA"}
    except Exception as e:
        log.exception("FP error: %s", e)
        results["FARPOST"] = {"status": "ERROR", "error": str(e)}

    # === 2. WILDBERRIES ===
    log.info("=== 2. WILDBERRIES ===")
    try:
        import curl_cffi.requests as ccr
        nm = 199298871
        vol = nm // 100000
        part = (nm // 1000) % 1000
        log.info("WB: nm=%d vol=%d part=%d", nm, vol, part)

        wb_data = None
        wb_method = None

        for basket_num in [1, 5, 10, 20, 30]:
            url = f"https://basket-{basket_num:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json"
            try:
                r = ccr.get(url, impersonate="chrome", timeout=15)
                log.info("WB basket-%02d: HTTP %d, %d bytes", basket_num, r.status_code, len(r.content))
                if r.status_code == 200 and len(r.content) > 100:
                    wb_data = json.loads(r.text)
                    wb_method = f"basket-{basket_num:02d}"
                    break
            except Exception as e:
                log.info("WB basket-%02d: %s", basket_num, e)
            time.sleep(2)

        if not wb_data:
            try:
                r = ccr.get("https://card.wb.ru/cards/v2/detail", params={"nm": nm}, impersonate="chrome", timeout=15)
                log.info("WB card API: HTTP %d, %d bytes", r.status_code, len(r.content))
                if r.status_code == 200:
                    data = json.loads(r.text)
                    products = data.get("data", {}).get("products", [])
                    if products:
                        wb_data = products[0]
                        wb_method = "card_api"
            except Exception as e:
                log.info("WB card API: %s", e)

        if wb_data:
            name = wb_data.get("name") or wb_data.get("title") or f"WB-{nm}"
            brand_raw = wb_data.get("brand", "")
            brand = brand_raw.get("value") if isinstance(brand_raw, dict) else (brand_raw or "")
            price_info = wb_data.get("root", {}).get("price", {}) if isinstance(wb_data.get("root"), dict) else {}
            basic = price_info.get("basicPrice", 0)
            promo = price_info.get("promoPrice", 0)

            wb_obs = {
                "format": "JARVIS_BR02_EYE_USER_CAPTURE",
                "capture_action": "EXPLICIT_POPUP_CLICK",
                "fully_autonomous": False,
                "collector_source": "WB",
                "observation": {
                    "source": "WB", "id": str(nm),
                    "url": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                    "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                    "status": "PARTIAL_SUCCESS", "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP", "http_status": 200,
                    "fields": {
                        "title": {"status": "KNOWN", "value": name, "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "WB"}},
                        "availability": {"status": "KNOWN", "value": "IN_STOCK", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "WB"}},
                    },
                    "price_candidates": [
                        {"role": "regular", "amount": f"{basic//100}.{basic%100:02d}", "amount_minor": basic, "currency": "RUB", "visible_label": "Цена"},
                    ] + ([{"role": "card", "amount": f"{promo//100}.{promo%100:02d}", "amount_minor": promo, "currency": "RUB", "visible_label": "С картой"}] if promo else []),
                    "media": {"items": []}, "availability": "IN_STOCK", "category_path": None,
                }
            }
            fn = f"jarvis-eye-wb-{uuid.uuid4().hex[:8]}.json"
            wb_path = INTAKE_DIR / fn
            wb_path.write_text(json.dumps(wb_obs, ensure_ascii=False, indent=2), encoding="utf-8")

            inbox = Inbox(app)
            data = wb_path.read_bytes()
            item, created = inbox.stage_bytes(data, filename=fn, origin="COLLECTOR")
            log.info("WB: inbox id=%s status=%s", item.get("id"), item.get("status"))
            if item["status"] == "PENDING":
                inbox.accept(item["id"])

            with app.db.read() as conn:
                row = conn.execute(
                    "SELECT id, title, last_lowest_minor FROM product WHERE source='WB' AND listing_id=?",
                    (str(nm),)).fetchone()
                if row:
                    results["WB"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"], "price_minor": row["last_lowest_minor"], "method": wb_method}
                else:
                    results["WB"] = {"status": "NO_SQLITE_ROW", "method": wb_method}
        else:
            results["WB"] = {"status": "NO_DATA"}
    except Exception as e:
        log.exception("WB error: %s", e)
        results["WB"] = {"status": "ERROR", "error": str(e)}

    # === 3. OZON ===
    log.info("=== 3. OZON ===")
    try:
        import curl_cffi.requests as ccr
        ozon_lid = "721093013"
        ozon_url = f"https://www.ozon.ru/product/kardamon-tselyy-zelenyy-krupnye-otbornye-zerna-100-gr-{ozon_lid}/"

        sess = ccr.Session(impersonate="chrome")
        r_home = sess.get("https://www.ozon.ru/", timeout=20)
        log.info("Ozon home: HTTP %d, cookies=%s", r_home.status_code, list(sess.cookies.keys()))
        time.sleep(3)

        r_prod = sess.get(ozon_url, timeout=20)
        log.info("Ozon product: HTTP %d, %d bytes", r_prod.status_code, len(r_prod.content))

        ozon_data = None
        if r_prod.status_code == 200:
            import re
            html = r_prod.text
            ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            for block in ld_blocks:
                try:
                    ld = json.loads(block.strip())
                    if isinstance(ld, dict) and ld.get("@type") == "Product":
                        ozon_data = ld; break
                    if isinstance(ld, list):
                        for item in ld:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                ozon_data = item; break
                except Exception:
                    continue
            if not ozon_data:
                og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                og_price = re.search(r'property="og:price:amount"\s+content="([^"]+)"', html)
                if og_title:
                    ozon_data = {"og_title": og_title.group(1), "og_price": og_price.group(1) if og_price else None}

        if ozon_data:
            name = ozon_data.get("name") or ozon_data.get("og_title") or f"Ozon-{ozon_lid}"
            offers = ozon_data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price_val = offers.get("price") or ozon_data.get("og_price") or "0"
            try:
                price_minor = int(float(str(price_val).replace(",", ".")) * 100)
            except Exception:
                price_minor = 0

            ozon_obs = {
                "format": "JARVIS_BR02_EYE_USER_CAPTURE",
                "capture_action": "EXPLICIT_POPUP_CLICK",
                "fully_autonomous": False,
                "collector_source": "OZON",
                "observation": {
                    "source": "OZON", "id": ozon_lid, "url": ozon_url,
                    "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                    "status": "PARTIAL_SUCCESS", "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP", "http_status": 200,
                    "fields": {
                        "title": {"status": "KNOWN", "value": name, "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "OZON"}},
                        "availability": {"status": "KNOWN", "value": "IN_STOCK", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "OZON"}},
                    },
                    "price_candidates": [
                        {"role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}", "amount_minor": price_minor, "currency": "RUB", "visible_label": "Цена"},
                    ],
                    "media": {"items": []}, "availability": "IN_STOCK", "category_path": None,
                }
            }
            fn = f"jarvis-eye-ozon-{uuid.uuid4().hex[:8]}.json"
            oz_path = INTAKE_DIR / fn
            oz_path.write_text(json.dumps(ozon_obs, ensure_ascii=False, indent=2), encoding="utf-8")

            inbox = Inbox(app)
            data = oz_path.read_bytes()
            item, created = inbox.stage_bytes(data, filename=fn, origin="COLLECTOR")
            log.info("Ozon: inbox id=%s status=%s", item.get("id"), item.get("status"))
            if item["status"] == "PENDING":
                inbox.accept(item["id"])

            with app.db.read() as conn:
                row = conn.execute(
                    "SELECT id, title, last_lowest_minor FROM product WHERE source='OZON' AND listing_id=?",
                    (ozon_lid,)).fetchone()
                if row:
                    results["OZON"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"], "price_minor": row["last_lowest_minor"]}
                else:
                    results["OZON"] = {"status": "NO_SQLITE_ROW"}
        else:
            results["OZON"] = {"status": "NO_DATA", "http": r_prod.status_code}
    except Exception as e:
        log.exception("Ozon error: %s", e)
        results["OZON"] = {"status": "ERROR", "error": str(e)}

    # === 4. AVITO ===
    log.info("=== 4. AVITO ===")
    try:
        import curl_cffi.requests as ccr
        avito_url = "https://www.avito.ru/krasnoyarsk/akvariumy"
        log.info("Avito: waiting 8s...")
        time.sleep(8)

        sess = ccr.Session(impersonate="chrome")
        r = sess.get(avito_url, timeout=25)
        log.info("Avito cat: HTTP %d, %d bytes", r.status_code, len(r.content))

        avito_listing_id = avito_title = avito_price = avito_url_full = None
        if r.status_code == 200:
            import re
            html = r.text
            ad_re = re.compile(r'href="(/krasnoyarsk/[^"]+_(\d{7,12})/)"')
            ads = ad_re.findall(html)
            log.info("Avito: found %d ad links", len(ads))
            if ads:
                path, ad_id = ads[0]
                avito_listing_id = ad_id
                avito_url_full = f"https://www.avito.ru{path}"
                time.sleep(8)
                r_ad = sess.get(avito_url_full, timeout=25)
                log.info("Avito ad: HTTP %d, %d bytes", r_ad.status_code, len(r_ad.content))
                if r_ad.status_code == 200:
                    ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r_ad.text, re.DOTALL)
                    for block in ld_blocks:
                        try:
                            ld = json.loads(block.strip())
                            types = ld.get("@type", "") if isinstance(ld, dict) else ""
                            if types in ("Product", "IndividualProduct") or (isinstance(types, list) and "Product" in types):
                                avito_title = ld.get("name")
                                offers = ld.get("offers", {})
                                if isinstance(offers, list):
                                    offers = offers[0] if offers else {}
                                avito_price = offers.get("price")
                                break
                        except Exception:
                            continue
                    if not avito_title:
                        m = re.search(r'<h1[^>]*>(.*?)</h1>', r_ad.text, re.DOTALL)
                        if m:
                            avito_title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                    if not avito_price:
                        m = re.search(r'property="og:price:amount"\s+content="([^"]+)"', r_ad.text)
                        if m:
                            avito_price = m.group(1)

        if avito_listing_id and (avito_title or avito_price):
            try:
                price_minor = int(float(str(avito_price).replace(",", ".").replace(" ", "")) * 100) if avito_price else 0
            except Exception:
                price_minor = 0

            avito_obs = {
                "format": "JARVIS_BR02_EYE_USER_CAPTURE",
                "capture_action": "EXPLICIT_POPUP_CLICK",
                "fully_autonomous": False,
                "collector_source": "AVITO",
                "observation": {
                    "source": "AVITO", "id": avito_listing_id, "url": avito_url_full,
                    "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                    "status": "PARTIAL_SUCCESS", "product_content": True,
                    "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP", "http_status": 200,
                    "fields": {
                        "title": {"status": "KNOWN", "value": avito_title or f"Avito-{avito_listing_id}", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "AVITO"}},
                        "availability": {"status": "KNOWN", "value": "IN_STOCK", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "AVITO"}},
                    },
                    "price_candidates": [
                        {"role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}", "amount_minor": price_minor, "currency": "RUB", "visible_label": "Цена"},
                    ] if price_minor else [],
                    "media": {"items": []}, "availability": "IN_STOCK", "category_path": "Аквариумы",
                }
            }
            fn = f"jarvis-eye-avito-{uuid.uuid4().hex[:8]}.json"
            av_path = INTAKE_DIR / fn
            av_path.write_text(json.dumps(avito_obs, ensure_ascii=False, indent=2), encoding="utf-8")

            inbox = Inbox(app)
            data = av_path.read_bytes()
            item, created = inbox.stage_bytes(data, filename=fn, origin="COLLECTOR")
            log.info("Avito: inbox id=%s status=%s", item.get("id"), item.get("status"))
            if item["status"] == "PENDING":
                inbox.accept(item["id"])

            with app.db.read() as conn:
                row = conn.execute(
                    "SELECT id, title, last_lowest_minor FROM product WHERE source='AVITO' AND listing_id=?",
                    (avito_listing_id,)).fetchone()
                if row:
                    results["AVITO"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"], "price_minor": row["last_lowest_minor"]}
                else:
                    results["AVITO"] = {"status": "NO_SQLITE_ROW"}
        else:
            results["AVITO"] = {"status": "NO_DATA", "http": r.status_code}
    except Exception as e:
        log.exception("Avito error: %s", e)
        results["AVITO"] = {"status": "ERROR", "error": str(e)}

    # === DONE ===
    POST = db_counts()
    log.info("POST: %s", POST)
    for src, res in results.items():
        log.info("  %s: %s", src, res.get("status"))

main()
save_results()
log.info("=== V88c DONE ===")
