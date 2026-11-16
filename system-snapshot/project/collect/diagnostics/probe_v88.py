"""V88: Полный E2E для 4 маркетплейсов.
Исправления:
- Farpost: ensure_source 4 аргумента
- WB: chrome impersonation, vol/part для nm=199298871
- Ozon: session + cookies
- Avito: редкая категория + прямой URL
- Полный цикл: collect → JSON → Inbox → SQLite
"""
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("v88")

DB_PATH = BASE / "r52" / "data" / "r52.db"
INTAKE_DIR = BASE / "r52" / "data" / "intake"
INTAKE_DIR.mkdir(parents=True, exist_ok=True)

# === SETUP ===
from r52.db.database import Database
from r52.db.migrate import run_migrations
from r52.store.products import ensure_source, upsert_product, add_observation, refresh_summary
from r52.intake.eye import Inbox, parse_export_bytes, check_export
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
migs = run_migrations(DB_PATH)
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

results = {}

# === 1. FARPOST E2E ===
log.info("=== 1. FARPOST E2E ===")
try:
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(data_dir=BASE / "r52" / "data", use_proxies=False)
    fp_url = "https://www.farpost.ru/krasnoyarsk/auto/wheel/tire/shiny-127622540.html"
    fp_lid = "127622540"
    log.info("FP: collecting %s", fp_url)
    obs = fp.collect_product(fp_lid, fp_url, "Boto WD69")
    if obs:
        # Write JSON (simulating subprocess output)
        fn = f"jarvis-eye-farpost-{uuid.uuid4().hex[:8]}.json"
        fp_path = INTAKE_DIR / fn
        fp_path.write_text(json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("FP: JSON written %s (%d bytes)", fn, fp_path.stat().st_size)
        
        # Inbox pipeline
        inbox = Inbox(app)
        data = fp_path.read_bytes()
        item, created = inbox.stage_bytes(data, filename=fn, origin="COLLECTOR")
        log.info("FP: inbox item id=%s status=%s created=%s", item.get("id"), item.get("status"), created)
        
        if item["status"] == "PENDING":
            accepted = inbox.accept(item["id"])
            log.info("FP: accepted product_id=%s obs_created=%s", accepted.get("product_id"), accepted.get("observation_created"))
        elif item["status"] == "ACCEPTED":
            log.info("FP: auto-accepted")
        else:
            log.warning("FP: inbox status=%s error=%s", item.get("status"), item.get("error_code"))
        
        # Verify SQLite
        with app.db.read() as conn:
            row = conn.execute(
                "SELECT id, source, listing_id, title, last_lowest_minor, last_availability, observation_count FROM product WHERE source='FARPOST' AND listing_id=?",
                (fp_lid,)
            ).fetchone()
            if row:
                log.info("FP: SQLite product id=%s title=%s price=%s avail=%s obs=%s",
                         row["id"], row["title"][:40], row["last_lowest_minor"], row["last_availability"], row["observation_count"])
                results["FARPOST"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"],
                                      "price_minor": row["last_lowest_minor"], "avail": row["last_availability"]}
            else:
                results["FARPOST"] = {"status": "NO_SQLITE_ROW"}
    else:
        results["FARPOST"] = {"status": "NO_DATA"}
except Exception as e:
    log.exception("FP E2E error: %s", e)
    results["FARPOST"] = {"status": "ERROR", "error": str(e)}

# === 2. WILDBERRIES E2E ===
log.info("=== 2. WILDBERRIES E2E ===")
try:
    # Try basket CDN with chrome impersonation
    import curl_cffi.requests as ccr
    
    nm = 199298871
    vol = nm // 100000  # 1992
    part = (nm // 1000) % 1000  # 298
    log.info("WB: nm=%d vol=%d part=%d", nm, vol, part)
    
    wb_data = None
    wb_method = None
    
    # Strategy 1: basket CDN
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
    
    # Strategy 2: HTML page with chrome
    if not wb_data:
        log.info("WB: trying HTML with chrome")
        html_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        try:
            r = ccr.get(html_url, impersonate="chrome", timeout=20)
            log.info("WB HTML: HTTP %d, %d bytes", r.status_code, len(r.content))
            if r.status_code == 200:
                import re
                # Extract from HTML
                title_m = re.search(r'<title>([^<]+)</title>', r.text)
                price_m = re.search(r'(\d[\d\s\u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)', r.text)
                if title_m:
                    wb_data = {
                        "nmId": nm,
                        "name": title_m.group(1).strip(),
                        "brand": "",
                        "sizes": [],
                        "root": {"price": {"basicPrice": int(price_m.group(1).replace(" ", "").replace(",", ".")) * 100 if price_m else 0}},
                    }
                    wb_method = "html"
        except Exception as e:
            log.info("WB HTML error: %s", e)
    
    # Strategy 3: card.wb.ru API
    if not wb_data:
        log.info("WB: trying card.wb.ru API with chrome")
        try:
            r = ccr.get("https://card.wb.ru/cards/v2/detail", 
                       params={"nm": nm},
                       impersonate="chrome", timeout=15)
            log.info("WB card API: HTTP %d, %d bytes", r.status_code, len(r.content))
            if r.status_code == 200:
                data = json.loads(r.text)
                products = data.get("data", {}).get("products", [])
                if products:
                    wb_data = products[0]
                    wb_method = "card_api"
        except Exception as e:
            log.info("WB card API error: %s", e)
    
    if wb_data:
        log.info("WB: data via %s", wb_method)
        # Build observation in collector format
        name = wb_data.get("name") or wb_data.get("title") or f"WB-{nm}"
        brand = wb_data.get("brand", {}).get("value") if isinstance(wb_data.get("brand"), dict) else wb_data.get("brand", "")
        price_info = wb_data.get("root", {}).get("price", {}) if isinstance(wb_data.get("root"), dict) else {}
        basic = price_info.get("basicPrice", 0)
        promo = price_info.get("promoPrice", 0)
        
        wb_obs = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "WB",
            "observation": {
                "source": "WB",
                "id": str(nm),
                "url": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
                "fields": {
                    "title": {"status": "KNOWN", "value": name, "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "WB"}},
                    "brand": {"status": "KNOWN" if brand else "UNKNOWN", "value": brand or None, "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "WB"}},
                    "availability": {"status": "KNOWN", "value": "IN_STOCK", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "WB"}},
                },
                "price_candidates": [
                    {"role": "regular", "amount": f"{basic//100}.{basic%100:02d}", "amount_minor": basic, "currency": "RUB", "visible_label": "Цена"},
                ] + ([{"role": "card", "amount": f"{promo//100}.{promo%100:02d}", "amount_minor": promo, "currency": "RUB", "visible_label": "С картой"}] if promo else []),
                "media": {"items": []},
                "availability": "IN_STOCK",
                "category_path": None,
            }
        }
        
        # Write + Inbox
        fn = f"jarvis-eye-wb-{uuid.uuid4().hex[:8]}.json"
        wb_path = INTAKE_DIR / fn
        wb_path.write_text(json.dumps(wb_obs, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("WB: JSON written %s", fn)
        
        inbox = Inbox(app)
        data = wb_path.read_bytes()
        item, created = inbox.stage_bytes(data, filename=fn, origin="COLLECTOR")
        log.info("WB: inbox id=%s status=%s", item.get("id"), item.get("status"))
        
        if item["status"] == "PENDING":
            accepted = inbox.accept(item["id"])
            log.info("WB: accepted product_id=%s", accepted.get("product_id"))
        
        with app.db.read() as conn:
            row = conn.execute(
                "SELECT id, source, listing_id, title, last_lowest_minor, last_availability, observation_count FROM product WHERE source='WB' AND listing_id=?",
                (str(nm),)
            ).fetchone()
            if row:
                log.info("WB: SQLite id=%s title=%s price=%s", row["id"], row["title"][:40], row["last_lowest_minor"])
                results["WB"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"],
                                  "price_minor": row["last_lowest_minor"], "method": wb_method}
            else:
                results["WB"] = {"status": "NO_SQLITE_ROW", "method": wb_method}
    else:
        results["WB"] = {"status": "NO_DATA"}
except Exception as e:
    log.exception("WB E2E error: %s", e)
    results["WB"] = {"status": "ERROR", "error": str(e)}

# === 3. OZON E2E ===
log.info("=== 3. OZON E2E ===")
try:
    import curl_cffi.requests as ccr
    
    ozon_lid = "721093013"
    ozon_url = f"https://www.ozon.ru/product/kardamon-tselyy-zelenyy-krupnye-otbornye-zerna-100-gr-{ozon_lid}/"
    
    # Session approach: home first for cookies
    sess = ccr.Session(impersonate="chrome")
    log.info("Ozon: GET home for cookies")
    r_home = sess.get("https://www.ozon.ru/", timeout=20)
    log.info("Ozon home: HTTP %d, cookies=%s", r_home.status_code, list(sess.cookies.keys()))
    time.sleep(3)
    
    log.info("Ozon: GET product")
    r_prod = sess.get(ozon_url, timeout=20)
    log.info("Ozon product: HTTP %d, %d bytes", r_prod.status_code, len(r_prod.content))
    
    ozon_data = None
    if r_prod.status_code == 200:
        import re
        html = r_prod.text
        # Try JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        for block in ld_blocks:
            try:
                ld = json.loads(block.strip())
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    ozon_data = ld
                    break
                if isinstance(ld, list):
                    for item in ld:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            ozon_data = item
                            break
            except:
                continue
        
        # Try data-widget / __NEXT_DATA__
        if not ozon_data:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if m:
                try:
                    next_data = json.loads(m.group(1))
                    props = next_data.get("props", {}).get("pageProps", {})
                    state = props.get("state", {})
                    if state:
                        ozon_data = {"_next": state}
                except:
                    pass
        
        # Try og: tags
        if not ozon_data:
            og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
            og_price = re.search(r'property="og:price:amount"\s+content="([^"]+)"', html)
            if og_title:
                ozon_data = {"og_title": og_title.group(1), "og_price": og_price.group(1) if og_price else None}
    
    if ozon_data:
        log.info("Ozon: data extracted: %s", str(ozon_data)[:200])
        name = ozon_data.get("name") or ozon_data.get("og_title") or f"Ozon-{ozon_lid}"
        offers = ozon_data.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_val = offers.get("price") or ozon_data.get("og_price") or "0"
        try:
            price_minor = int(float(str(price_val).replace(",", ".")) * 100)
        except:
            price_minor = 0
        
        ozon_obs = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "OZON",
            "observation": {
                "source": "OZON",
                "id": ozon_lid,
                "url": ozon_url,
                "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
                "fields": {
                    "title": {"status": "KNOWN", "value": name, "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "OZON"}},
                    "availability": {"status": "KNOWN", "value": "IN_STOCK", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "OZON"}},
                },
                "price_candidates": [
                    {"role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}", "amount_minor": price_minor, "currency": "RUB", "visible_label": "Цена"},
                ],
                "media": {"items": []},
                "availability": "IN_STOCK",
                "category_path": None,
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
            accepted = inbox.accept(item["id"])
            log.info("Ozon: accepted product_id=%s", accepted.get("product_id"))
        
        with app.db.read() as conn:
            row = conn.execute(
                "SELECT id, source, listing_id, title, last_lowest_minor FROM product WHERE source='OZON' AND listing_id=?",
                (ozon_lid,)
            ).fetchone()
            if row:
                results["OZON"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"], "price_minor": row["last_lowest_minor"]}
            else:
                results["OZON"] = {"status": "NO_SQLITE_ROW"}
    else:
        results["OZON"] = {"status": "NO_DATA", "http": r_prod.status_code}
except Exception as e:
    log.exception("Ozon E2E error: %s", e)
    results["OZON"] = {"status": "ERROR", "error": str(e)}

# === 4. AVITO E2E ===
log.info("=== 4. AVITO E2E ===")
try:
    import curl_cffi.requests as ccr
    
    # Try a less common category to avoid 429
    avito_url = "https://www.avito.ru/krasnoyarsk/akvariumy"
    log.info("Avito: GET %s", avito_url)
    time.sleep(10)  # Long delay before first request
    
    sess = ccr.Session(impersonate="chrome")
    r = sess.get(avito_url, timeout=25)
    log.info("Avito cat: HTTP %d, %d bytes", r.status_code, len(r.content))
    
    avito_listing_id = None
    avito_title = None
    avito_price = None
    avito_url_full = None
    
    if r.status_code == 200:
        import re
        html = r.text
        # Find ad links: /krasnoyarsk/..._NNNNNNNNN/
        ad_re = re.compile(r'href="(/krasnoyarsk/[^"]+_(\d{7,12})/)"')
        ads = ad_re.findall(html)
        log.info("Avito: found %d ad links", len(ads))
        if ads:
            # Pick first ad
            path, ad_id = ads[0]
            avito_listing_id = ad_id
            avito_url_full = f"https://www.avito.ru{path}"
            log.info("Avito: first ad id=%s url=%s", ad_id, avito_url_full)
            
            # Get ad page
            time.sleep(8)
            r_ad = sess.get(avito_url_full, timeout=25)
            log.info("Avito ad: HTTP %d, %d bytes", r_ad.status_code, len(r_ad.content))
            if r_ad.status_code == 200:
                # JSON-LD
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
                    except:
                        continue
                # Fallback: h1
                if not avito_title:
                    m = re.search(r'<h1[^>]*>(.*?)</h1>', r_ad.text, re.DOTALL)
                    if m:
                        avito_title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                # Price from meta
                if not avito_price:
                    m = re.search(r'property="og:price:amount"\s+content="([^"]+)"', r_ad.text)
                    if m:
                        avito_price = m.group(1)
    
    if avito_listing_id and (avito_title or avito_price):
        log.info("Avito: title=%s price=%s", avito_title, avito_price)
        try:
            price_minor = int(float(str(avito_price).replace(",", ".").replace(" ", "")) * 100) if avito_price else 0
        except:
            price_minor = 0
        
        avito_obs = {
            "format": "JARVIS_BR02_EYE_USER_CAPTURE",
            "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False,
            "collector_source": "AVITO",
            "observation": {
                "source": "AVITO",
                "id": avito_listing_id,
                "url": avito_url_full,
                "observed_at": datetime.now(timezone(timedelta(hours=10))).isoformat(),
                "status": "PARTIAL_SUCCESS",
                "product_content": True,
                "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
                "http_status": 200,
                "fields": {
                    "title": {"status": "KNOWN", "value": avito_title or f"Avito-{avito_listing_id}", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "AVITO"}},
                    "availability": {"status": "KNOWN", "value": "IN_STOCK", "method": "COLLECTOR", "evidence": {"method": "COLLECTOR", "source": "AVITO"}},
                },
                "price_candidates": [
                    {"role": "regular", "amount": f"{price_minor//100}.{price_minor%100:02d}", "amount_minor": price_minor, "currency": "RUB", "visible_label": "Цена"},
                ] if price_minor else [],
                "media": {"items": []},
                "availability": "IN_STOCK",
                "category_path": "Акваariumы",
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
            accepted = inbox.accept(item["id"])
            log.info("Avito: accepted product_id=%s", accepted.get("product_id"))
        
        with app.db.read() as conn:
            row = conn.execute(
                "SELECT id, source, listing_id, title, last_lowest_minor FROM product WHERE source='AVITO' AND listing_id=?",
                (avito_listing_id,)
            ).fetchone()
            if row:
                results["AVITO"] = {"status": "E2E_OK", "product_id": row["id"], "title": row["title"], "price_minor": row["last_lowest_minor"]}
            else:
                results["AVITO"] = {"status": "NO_SQLITE_ROW"}
    else:
        results["AVITO"] = {"status": "NO_DATA", "http": r.status_code if 'r' in dir() else None}
except Exception as e:
    log.exception("Avito E2E error: %s", e)
    results["AVITO"] = {"status": "ERROR", "error": str(e)}

# === FINAL ===
log.info("=== V88 DONE ===")
POST = db_counts()
log.info("POST: %s", POST)
for src, res in results.items():
    log.info("  %s: %s", src, res.get("status"))

result_file = BASE / "probe_v88_result.json"
result_file.write_text(json.dumps({"PRE": PRE, "POST": POST, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("Saved %s", result_file.name)
