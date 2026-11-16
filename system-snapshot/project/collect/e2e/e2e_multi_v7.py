"""E2E Multi-Strategy V7: Fixed intake, expanded OZON/WB/FP strategies.
Fixes: db.read() context, config.tz, FP verify retry, WB host range, OZON Composer API.
"""
import json, os, sys, time, logging, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ["R52_HOME"] = str(BASE / "e2e_test_home")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("E2E-V7")

TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

# ============================================================
# 1. DB SETUP (fixed: context manager)
# ============================================================
def setup_db():
    from r52.config import Config, Paths
    from r52.db import Database, migrate

    home = BASE / "e2e_test_home"
    paths = Paths(home=home)
    paths.ensure()
    cfg = Config(paths=paths)
    db = Database(paths.db_file)
    db.initialize()
    migrate(db, backup_dir=paths.backups_dir, now=datetime.now(TZ))
    return db, paths, cfg

def db_counts(db):
    r = {}
    with db.read() as conn:
        for tbl in ("product", "observation", "price_point"):
            try:
                r[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except Exception as e:
                r[tbl] = f"ERR:{e}"
    return r

# ============================================================
# 2. INTAKE (fixed: config.tz, proper MiniApp)
# ============================================================
def intake_file(db, paths, cfg, filepath, origin="E2E-V7"):
    from r52.core.settings import Settings
    from r52.core.notify import Notifier
    from r52.intake.eye import Inbox
    from r52.timeutil import Clock

    clock = Clock()
    settings = Settings(db, clock)
    notifier = Notifier(settings, clock, cfg.tz)

    class MiniApp:
        pass
    app = MiniApp()
    app.db = db
    app.clock = clock
    app.settings = settings
    app.notifier = notifier
    app.tz = cfg.tz

    inbox = Inbox(app)
    data = filepath.read_bytes()
    name = filepath.name
    log.info("INTAKE: %s (%d bytes)", name, len(data))

    item, created = inbox.stage_bytes(data, filename=name, origin=origin)
    log.info("  stage: id=%s status=%s created=%s", item.get("id"), item.get("status"), created)

    if item["status"] == "PENDING":
        result = inbox.accept(item["id"])
        log.info("  accept: product_id=%s obs_created=%s", result.get("product_id"), result.get("observation_created"))
        return result
    elif item["status"] == "ACCEPTED":
        return item
    else:
        log.warning("  status=%s error=%s", item["status"], item.get("error_code"))
        return item

# ============================================================
# 3. OZON: Multi-strategy (Composer API → SSR → mobile SSR)
# ============================================================
def ozon_test():
    log.info("=== OZON MULTI ===")
    results = {}
    listing_id = "3481320800"
    url = f"https://www.ozon.ru/product/{listing_id}/"

    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return {"ok": False, "detail": "no_curl_cffi"}

    # --- Strategy A: Composer API v2 ---
    t0 = time.monotonic()
    try:
        composer_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{listing_id}/"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.ozon.ru/",
        }
        resp = cffi.get(composer_url, impersonate="chrome", timeout=15, headers=headers)
        elapsed = time.monotonic() - t0
        log.info("OZON A(composer): HTTP=%d len=%d t=%.1fs", resp.status_code, len(resp.text), elapsed)
        if resp.status_code == 200:
            data = resp.json()
            # Navigate to find title/price
            title = None
            price_val = None
            images = []
            # Try common paths
            for path in [("page","title"),("product","title"),("data","title")]:
                obj = data
                for k in path:
                    if isinstance(obj, dict):
                        obj = obj.get(k)
                    else:
                        obj = None
                        break
                if obj and isinstance(obj, str) and len(obj) > 5:
                    title = obj
                    break
            # Price
            for path in [("product","price"),("page","price"),("data","price")]:
                obj = data
                for k in path:
                    if isinstance(obj, dict):
                        obj = obj.get(k)
                    else:
                        obj = None
                        break
                if obj:
                    price_val = obj
                    break
            # Images
            for path in [("product","images"),("page","images")]:
                obj = data
                for k in path:
                    if isinstance(obj, dict):
                        obj = obj.get(k)
                    else:
                        obj = None
                        break
                if isinstance(obj, list):
                    for img in obj[:10]:
                        if isinstance(img, str):
                            images.append(img)
                        elif isinstance(img, dict):
                            u = img.get("url") or img.get("src")
                            if u: images.append(u)
                    break

            results["composer"] = {
                "ok": bool(title), "http": resp.status_code, "t": round(elapsed,1),
                "title": (title or "")[:80], "price": str(price_val)[:100] if price_val else None,
                "n_images": len(images), "n_keys": len(data) if isinstance(data, dict) else 0,
            }
            log.info("OZON A: title=%s price=%s imgs=%d", (title or "?")[:40], str(price_val)[:30], len(images))
        else:
            results["composer"] = {"ok": False, "http": resp.status_code, "t": round(elapsed,1)}
    except Exception as e:
        results["composer"] = {"ok": False, "error": str(e)[:150], "t": round(time.monotonic()-t0,1)}
        log.warning("OZON A error: %s", e)

    # --- Strategy B: SSR with full browser headers ---
    t0 = time.monotonic()
    try:
        ssr_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.ozon.ru/",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        resp = cffi.get(url, impersonate="chrome", timeout=20, headers=ssr_headers)
        elapsed = time.monotonic() - t0
        html = resp.text
        log.info("OZON B(SSR): HTTP=%d len=%d t=%.1fs", resp.status_code, len(html), elapsed)

        if resp.status_code == 200 and len(html) > 5000:
            # JSON-LD
            ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            product_ld = None
            for block in ld_blocks:
                try:
                    d = json.loads(block)
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        product_ld = d; break
                    if isinstance(d, list):
                        for item in d:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                product_ld = item; break
                except: pass

            # data-widget price
            price_w = re.search(r'data-widget="price"[^>]*>.*?(\d[\d\s\u00a0]*)\s*(?:₽|RUB)', html, re.DOTALL)
            # og tags
            og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
            # title
            t_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)

            results["ssr"] = {
                "ok": True, "http": 200, "len": len(html), "t": round(elapsed,1),
                "jsonld": bool(product_ld),
                "ld_price": product_ld.get("offers",{}).get("price") if product_ld else None,
                "widget_price": price_w.group(1).replace("\u00a0","").replace(" ","") if price_w else None,
                "og_price": og_p.group(1) if og_p else None,
                "title": (t_m.group(1).strip() if t_m else "")[:80],
            }
            log.info("OZON B: ld=%s wp=%s og=%s title=%s", results["ssr"]["jsonld"],
                     results["ssr"]["widget_price"], results["ssr"]["og_price"], results["ssr"]["title"][:40])
        else:
            results["ssr"] = {"ok": False, "http": resp.status_code, "len": len(html), "t": round(elapsed,1)}
    except Exception as e:
        results["ssr"] = {"ok": False, "error": str(e)[:150], "t": round(time.monotonic()-t0,1)}

    # --- Strategy C: mobile endpoint ---
    t0 = time.monotonic()
    try:
        m_url = f"https://m.ozon.ru/product/{listing_id}/"
        resp = cffi.get(m_url, impersonate="chrome", timeout=15)
        elapsed = time.monotonic() - t0
        results["mobile"] = {"http": resp.status_code, "len": len(resp.text), "t": round(elapsed,1)}
        log.info("OZON C(mobile): HTTP=%d len=%d t=%.1fs", resp.status_code, len(resp.text), elapsed)
    except Exception as e:
        results["mobile"] = {"error": str(e)[:100]}

    # --- Strategy D: entrypoint API ---
    t0 = time.monotonic()
    try:
        ep_url = f"https://www.ozon.ru/api/entrypoint/pdp/{listing_id}"
        resp = cffi.get(ep_url, impersonate="chrome", timeout=15,
                       headers={"Accept":"application/json","Referer":"https://www.ozon.ru/"})
        elapsed = time.monotonic() - t0
        if resp.status_code == 200:
            d = resp.json()
            results["entrypoint"] = {"ok": True, "http": 200, "n_keys": len(d) if isinstance(d,dict) else 0, "t": round(elapsed,1)}
        else:
            results["entrypoint"] = {"ok": False, "http": resp.status_code, "t": round(elapsed,1)}
        log.info("OZON D(entrypoint): HTTP=%d t=%.1fs", resp.status_code, elapsed)
    except Exception as e:
        results["entrypoint"] = {"ok": False, "error": str(e)[:100], "t": round(time.monotonic()-t0,1)}

    results["ok"] = any(r.get("ok") for r in results.values() if isinstance(r, dict))
    return results

# ============================================================
# 4. WB: Expanded basket hosts + price API
# ============================================================
def wb_test():
    log.info("=== WB MULTI ===")
    results = {}
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return {"ok": False, "detail": "no_curl_cffi"}

    headers = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}

    # --- Strategy A: basket CDN (expand to 60 hosts) ---
    t0 = time.monotonic()
    found_host = None
    card_data = None
    for i in range(1, 61):
        host = f"basket-{i}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            resp = cffi.get(url, impersonate="chrome", timeout=8, headers=headers)
            if resp.status_code == 200:
                card_data = resp.json()
                found_host = host
                break
        except:
            pass
    elapsed = time.monotonic() - t0
    if card_data:
        title = card_data.get("name","")
        brand = card_data.get("brand","")
        selling = card_data.get("selling",{})
        prices = [f"{k}={v}" for k,v in selling.items() if isinstance(v,(int,float)) and v>0][:5]
        results["basket"] = {"ok":True,"host":found_host,"title":title[:80],"brand":brand,"prices":prices,"nkeys":len(card_data),"t":round(elapsed,1)}
        log.info("WB A(basket): host=%s title=%s t=%.1fs", found_host, title[:40], elapsed)
    else:
        results["basket"] = {"ok":False,"t":round(elapsed,1),"tried":60}
        log.info("WB A(basket): all 60 hosts failed t=%.1fs", elapsed)

    # --- Strategy B: card.wb.ru price API ---
    t0 = time.monotonic()
    try:
        price_url = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
        resp = cffi.get(price_url, impersonate="chrome", timeout=12, headers=headers)
        elapsed = time.monotonic() - t0
        if resp.status_code == 200:
            d = resp.json()
            items = d.get("data",{}).get("items",[])
            if items:
                item = items[0]
                price_info = item.get("price",{})
                results["price_api"] = {
                    "ok": True, "t": round(elapsed,1),
                    "basic": price_info.get("basic"),
                    "product": price_info.get("product"),
                    "sale": price_info.get("sale"),
                    "total": price_info.get("total"),
                    "sizes": len(item.get("sizes",[])),
                }
                log.info("WB B(price): basic=%s product=%s sale=%s t=%.1fs",
                         price_info.get("basic"), price_info.get("product"), price_info.get("sale"), elapsed)
            else:
                results["price_api"] = {"ok":False,"detail":"no_items","t":round(elapsed,1)}
        else:
            results["price_api"] = {"ok":False,"http":resp.status_code,"t":round(elapsed,1)}
    except Exception as e:
        results["price_api"] = {"ok":False,"error":str(e)[:100],"t":round(time.monotonic()-t0,1)}

    # --- Strategy C: static-basket (alternative CDN) ---
    t0 = time.monotonic()
    try:
        static_url = f"https://static-basket-{nm_id % 10}.wbbasket.ru{path}"
        resp = cffi.get(static_url, impersonate="chrome", timeout=10, headers=headers)
        elapsed = time.monotonic() - t0
        if resp.status_code == 200:
            d = resp.json()
            results["static"] = {"ok":True,"host":static_url.split("/")[2],"title":d.get("name","")[:60],"t":round(elapsed,1)}
        else:
            results["static"] = {"ok":False,"http":resp.status_code,"t":round(elapsed,1)}
        log.info("WB C(static): HTTP=%d t=%.1fs", resp.status_code, elapsed)
    except Exception as e:
        results["static"] = {"ok":False,"error":str(e)[:100]}

    results["ok"] = any(r.get("ok") for r in results.values() if isinstance(r, dict))
    return results

# ============================================================
# 5. FARPOST: Session with verify retry
# ============================================================
def farpost_test():
    log.info("=== FARPOST MULTI ===")
    results = {}
    card_id = "134693091"
    url = f"https://www.farpost.ru/vladivostok/{card_id}.html"

    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return {"ok": False, "detail": "no_curl_cffi"}

    sess = cffi.Session(impersonate="chrome")

    # Warm-up
    t0 = time.monotonic()
    try:
        w = sess.get("https://www.farpost.ru/", timeout=15)
        log.info("FP warm: HTTP=%d cookies=%s t=%.1fs", w.status_code, list(sess.cookies.keys()), time.monotonic()-t0)
    except Exception as e:
        log.warning("FP warm error: %s", e)

    time.sleep(2)

    # Card request with retry
    for attempt in range(3):
        t0 = time.monotonic()
        try:
            resp = sess.get(url, timeout=20)
            elapsed = time.monotonic() - t0
            html = resp.text
            final_url = str(resp.url)
            is_verify = "/verify" in final_url

            log.info("FP attempt=%d: HTTP=%d len=%d verify=%s url=%s t=%.1fs",
                     attempt+1, resp.status_code, len(html), is_verify, final_url[:80], elapsed)

            if not is_verify and resp.status_code == 200 and len(html) > 5000:
                # Parse
                ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
                product_ld = None
                for block in ld_blocks:
                    try:
                        d = json.loads(block)
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            product_ld = d; break
                    except: pass

                price_attr = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                t_m = re.search(r'<h1[^>]*class="[^"]*subject[^"]*"[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
                title = re.sub(r'<[^>]+>','',t_m.group(1)).strip() if t_m else None

                # Lightbox photos
                lb = re.findall(r'"(https?://static\.baza\.farpost\.ru/[^"]+)"', html)
                photos = [p for p in lb if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))][:10]

                results["card"] = {
                    "ok": True, "http": 200, "len": len(html), "t": round(elapsed,1),
                    "attempt": attempt+1,
                    "jsonld": bool(product_ld),
                    "ld_price": product_ld.get("offers",{}).get("price") if product_ld else None,
                    "attr_price": price_attr.group(1) if price_attr else None,
                    "title": (title or "")[:80],
                    "n_photos": len(photos),
                }
                log.info("FP CARD: ld=%s price=%s/%s title=%s photos=%d",
                         results["card"]["jsonld"], results["card"]["ld_price"],
                         results["card"]["attr_price"], (title or "?")[:40], len(photos))
                break
            else:
                results[f"attempt{attempt+1}"] = {
                    "http": resp.status_code, "len": len(html), "verify": is_verify, "t": round(elapsed,1)
                }
                if attempt < 2:
                    time.sleep(5)
        except Exception as e:
            results[f"attempt{attempt+1}"] = {"error": str(e)[:150], "t": round(time.monotonic()-t0,1)}
            if attempt < 2:
                time.sleep(3)

    results["ok"] = results.get("card",{}).get("ok", False)
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    t_start = time.monotonic()
    log.info("=== E2E MULTI V7 START ===")

    db, paths, cfg = setup_db()
    before = db_counts(db)
    log.info("DB BEFORE: %s", before)

    report = {"before": before, "ozon": {}, "farpost": {}, "wb": {}, "intake": {}}

    # --- OZON ---
    report["ozon"] = ozon_test()

    # --- FARPOST ---
    report["farpost"] = farpost_test()

    # --- WB ---
    report["wb"] = wb_test()

    # --- INTAKE: WB dumps ---
    wb_dir = BASE / "e2e-wb-v1"
    for f in sorted(wb_dir.glob("jarvis-eye-wb-*.json")):
        try:
            r = intake_file(db, paths, cfg, f, origin="E2E-V7-WB")
            report["intake"][f.name] = {
                "product_id": r.get("product_id"),
                "obs_created": r.get("observation_created"),
                "status": r.get("status"),
            }
        except Exception as e:
            log.warning("WB intake %s: %s", f.name, e)
            report["intake"][f.name] = {"error": str(e)[:200]}

    # --- INTAKE: Farpost dumps ---
    fp_dir = BASE / "e2e-farpost-v2"
    for f in sorted(fp_dir.glob("jarvis-eye-farpost-*.json")):
        try:
            r = intake_file(db, paths, cfg, f, origin="E2E-V7-FP")
            report["intake"][f.name] = {
                "product_id": r.get("product_id"),
                "obs_created": r.get("observation_created"),
                "status": r.get("status"),
            }
        except Exception as e:
            log.warning("FP intake %s: %s", f.name, e)
            report["intake"][f.name] = {"error": str(e)[:200]}

    # --- DB AFTER ---
    after = db_counts(db)
    log.info("DB AFTER: %s", after)
    report["after"] = after
    report["delta"] = {k: (after[k] - before[k]) if isinstance(before[k],int) and isinstance(after[k],int) else "?" for k in before}

    # Per-source
    try:
        with db.read() as conn:
            src_rows = conn.execute("""
                SELECT p.source, COUNT(DISTINCT p.id) as products,
                       COUNT(DISTINCT o.id) as obs,
                       COUNT(DISTINCT pp.id) as pp
                FROM product p
                LEFT JOIN observation o ON o.product_id = p.id
                LEFT JOIN price_point pp ON pp.observation_id = o.id
                GROUP BY p.source
            """).fetchall()
            report["by_source"] = [dict(r) for r in src_rows]
    except Exception as e:
        report["by_source"] = str(e)[:200]

    elapsed = time.monotonic() - t_start
    report["total_s"] = round(elapsed, 1)
    report["time"] = NOW()

    out = BASE / "e2e_v7_results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("=== E2E V7 DONE %.1fs ===", elapsed)

    # Summary
    log.info("--- SUMMARY ---")
    log.info("OZON: ok=%s strategies=%s", report["ozon"].get("ok"),
             [k for k,v in report["ozon"].items() if isinstance(v,dict) and v.get("ok")])
    log.info("FP:   ok=%s", report["farpost"].get("ok"))
    log.info("WB:   ok=%s strategies=%s", report["wb"].get("ok"),
             [k for k,v in report["wb"].items() if isinstance(v,dict) and v.get("ok")])
    for name, r in report["intake"].items():
        log.info("INTAKE %s: pid=%s obs=%s status=%s", name, r.get("product_id"), r.get("obs_created"), r.get("status"))
    log.info("DELTA: %s", report["delta"])
    log.info("BY_SRC: %s", report.get("by_source"))

if __name__ == "__main__":
    main()
