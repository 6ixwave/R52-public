"""E2E V8: Fast multi-marketplace test.
Fixes: r52.db package import, cfg.tz, db.read() context, WB price API, FP verify.
Target: <180s total.
"""
import json, os, sys, time, logging, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ["R52_HOME"] = str(BASE / "e2e_v8_home")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("V8")
TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

# ─── DB ───
def setup_db():
    from r52.config import Config, Paths
    from r52.db import Database, migrate
    home = BASE / "e2e_v8_home"
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

# ─── INTAKE ───
def intake_file(db, cfg, filepath, origin="V8"):
    from r52.core.settings import Settings
    from r52.core.notify import Notifier
    from r52.intake.eye import Inbox
    from r52.timeutil import Clock
    clock = Clock()
    settings = Settings(db, clock)
    notifier = Notifier(settings, clock, cfg.tz)
    class App: pass
    app = App(); app.db = db; app.clock = clock; app.settings = settings; app.notifier = notifier; app.tz = cfg.tz
    inbox = Inbox(app)
    data = filepath.read_bytes()
    item, created = inbox.stage_bytes(data, filename=filepath.name, origin=origin)
    if item["status"] == "PENDING":
        return inbox.accept(item["id"])
    return item

# ─── WB ───
def wb_test():
    log.info("=== WB ===")
    R = {}
    nm = 1097975275
    vol, part = nm // 100000, nm // 1000
    path = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
    from curl_cffi import requests as cffi
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}

    # A: basket CDN (10 hosts, 5s each)
    t0 = time.monotonic()
    card, host = None, None
    for i in range(1, 11):
        h = f"basket-{i}.wbbasket.ru"
        try:
            r = cffi.get(f"https://{h}{path}", impersonate="chrome", timeout=5, headers=H)
            if r.status_code == 200:
                card, host = r.json(), h
                break
        except: pass
    el = time.monotonic() - t0
    if card:
        R["basket"] = {"ok": True, "host": host, "title": card.get("imt_name","")[:60],
                       "brand": card.get("selling",{}).get("brand_name",""),
                       "photo_count": card.get("media",{}).get("photo_count",0),
                       "n_options": len(card.get("options",[])), "t": round(el,1)}
    else:
        R["basket"] = {"ok": False, "t": round(el,1), "tried": 10}
    log.info("WB basket: ok=%s host=%s t=%.1fs", R["basket"].get("ok"), host, el)

    # B: Price API v2 (GET)
    t0 = time.monotonic()
    try:
        r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", impersonate="chrome", timeout=12, headers=H)
        el = time.monotonic() - t0
        if r.status_code == 200:
            d = r.json()
            items = d.get("data",{}).get("items",[])
            if items:
                p = items[0].get("price",{})
                R["price_v2"] = {"ok": True, "basic": p.get("basic"), "product": p.get("product"),
                                 "sale": p.get("sale"), "total": p.get("total"),
                                 "sizes": len(items[0].get("sizes",[])), "t": round(el,1)}
            else:
                R["price_v2"] = {"ok": False, "detail": "no_items", "t": round(el,1)}
        else:
            R["price_v2"] = {"ok": False, "http": r.status_code, "t": round(el,1)}
    except Exception as e:
        R["price_v2"] = {"ok": False, "error": str(e)[:100], "t": round(time.monotonic()-t0,1)}
    log.info("WB price_v2: %s", R["price_v2"])

    # C: Price API v1 (POST)
    t0 = time.monotonic()
    try:
        r = cffi.post("https://card.wb.ru/cards/v1/detail",
                      json={"nm":[nm],"curr":"rub","ext":"info,im,marketplace"},
                      impersonate="chrome", timeout=12, headers=H)
        el = time.monotonic() - t0
        if r.status_code == 200:
            d = r.json()
            items = d.get("data",{}).get("items",[])
            if items:
                p = items[0].get("price",{})
                R["price_v1"] = {"ok": True, "basic": p.get("basic"), "product": p.get("product"),
                                 "sale": p.get("sale"), "total": p.get("total"), "t": round(el,1)}
            else:
                R["price_v1"] = {"ok": False, "detail": "no_items", "t": round(el,1)}
        else:
            R["price_v1"] = {"ok": False, "http": r.status_code, "t": round(el,1)}
    except Exception as e:
        R["price_v1"] = {"ok": False, "error": str(e)[:100], "t": round(time.monotonic()-t0,1)}
    log.info("WB price_v1: %s", R["price_v1"])

    # D: static-basket
    t0 = time.monotonic()
    try:
        sh = f"static-basket-{nm % 10}.wbbasket.ru"
        r = cffi.get(f"https://{sh}{path}", impersonate="chrome", timeout=8, headers=H)
        el = time.monotonic() - t0
        if r.status_code == 200:
            d = r.json()
            R["static"] = {"ok": True, "host": sh, "title": d.get("imt_name","")[:50], "t": round(el,1)}
        else:
            R["static"] = {"ok": False, "http": r.status_code, "t": round(el,1)}
    except Exception as e:
        R["static"] = {"ok": False, "error": str(e)[:80], "t": round(time.monotonic()-t0,1)}
    log.info("WB static: %s", R["static"])

    R["ok"] = any(v.get("ok") for v in R.values() if isinstance(v,dict))
    return R

# ─── OZON ───
def ozon_test():
    log.info("=== OZON ===")
    R = {}
    lid = "3481320800"
    from curl_cffi import requests as cffi

    # A: Composer API
    t0 = time.monotonic()
    try:
        cu = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/"
        r = cffi.get(cu, impersonate="chrome", timeout=15,
                     headers={"Accept":"application/json","Referer":"https://www.ozon.ru/","Accept-Language":"ru-RU,ru;q=0.9"})
        el = time.monotonic() - t0
        if r.status_code == 200:
            d = r.json()
            title, price, imgs = None, None, []
            def dig(obj, *keys):
                for k in keys:
                    if isinstance(obj, dict): obj = obj.get(k)
                    else: return None
                return obj
            for paths in [("page","title"),("product","title"),("data","title")]:
                t = dig(d, *paths)
                if t and isinstance(t,str) and len(t)>5: title = t; break
            for paths in [("product","price"),("page","price"),("data","price")]:
                p = dig(d, *paths)
                if p: price = p; break
            R["composer"] = {"ok": bool(title), "http": 200, "title": (title or "")[:60],
                             "price": str(price)[:80] if price else None,
                             "n_keys": len(d) if isinstance(d,dict) else 0, "t": round(el,1)}
        else:
            R["composer"] = {"ok": False, "http": r.status_code, "t": round(el,1)}
    except Exception as e:
        R["composer"] = {"ok": False, "error": str(e)[:100], "t": round(time.monotonic()-t0,1)}
    log.info("OZON composer: %s", R["composer"])

    # B: SSR desktop
    t0 = time.monotonic()
    try:
        url = f"https://www.ozon.ru/product/{lid}/"
        hdrs = {"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8","Referer":"https://www.ozon.ru/",
                "sec-ch-ua":'"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                "sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"',
                "Sec-Fetch-Dest":"document","Sec-Fetch-Mode":"navigate","Sec-Fetch-Site":"same-origin",
                "Sec-Fetch-User":"?1","Upgrade-Insecure-Requests":"1"}
        r = cffi.get(url, impersonate="chrome", timeout=20, headers=hdrs)
        el = time.monotonic() - t0
        html = r.text
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        pld = None
        for b in ld_blocks:
            try:
                d = json.loads(b)
                if isinstance(d,dict) and d.get("@type")=="Product": pld=d; break
                if isinstance(d,list):
                    for i in d:
                        if isinstance(i,dict) and i.get("@type")=="Product": pld=i; break
                    if pld: break
            except: pass
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        t_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        R["ssr"] = {"ok": r.status_code==200 and len(html)>5000, "http": r.status_code,
                    "len": len(html), "jsonld": bool(pld),
                    "ld_price": pld.get("offers",{}).get("price") if pld else None,
                    "og_price": og_p.group(1) if og_p else None,
                    "title": (t_m.group(1).strip() if t_m else "")[:60], "t": round(el,1)}
    except Exception as e:
        R["ssr"] = {"ok": False, "error": str(e)[:100], "t": round(time.monotonic()-t0,1)}
    log.info("OZON ssr: %s", R["ssr"])

    # C: Mobile
    t0 = time.monotonic()
    try:
        r = cffi.get(f"https://m.ozon.ru/product/{lid}/", impersonate="chrome", timeout=15,
                     headers={"Accept":"text/html","Accept-Language":"ru-RU"})
        el = time.monotonic() - t0
        R["mobile"] = {"http": r.status_code, "len": len(r.text), "t": round(el,1)}
    except Exception as e:
        R["mobile"] = {"error": str(e)[:80], "t": round(time.monotonic()-t0,1)}
    log.info("OZON mobile: %s", R["mobile"])

    # D: entrypoint API
    t0 = time.monotonic()
    try:
        r = cffi.get(f"https://www.ozon.ru/api/entrypoint/pdp/{lid}", impersonate="chrome", timeout=15,
                     headers={"Accept":"application/json","Referer":"https://www.ozon.ru/"})
        el = time.monotonic() - t0
        if r.status_code == 200:
            d = r.json()
            R["entrypoint"] = {"ok": True, "n_keys": len(d) if isinstance(d,dict) else 0, "t": round(el,1)}
        else:
            R["entrypoint"] = {"ok": False, "http": r.status_code, "t": round(el,1)}
    except Exception as e:
        R["entrypoint"] = {"ok": False, "error": str(e)[:80], "t": round(time.monotonic()-t0,1)}
    log.info("OZON entrypoint: %s", R["entrypoint"])

    R["ok"] = any(v.get("ok") for v in R.values() if isinstance(v,dict))
    return R

# ─── FARPOST ───
def farpost_test():
    log.info("=== FARPOST ===")
    R = {}
    cid = "134693091"
    from curl_cffi import requests as cffi
    sess = cffi.Session(impersonate="chrome")

    # Warmup
    t0 = time.monotonic()
    try:
        w = sess.get("https://www.farpost.ru/", timeout=15)
        log.info("FP warm: HTTP=%d cookies=%s t=%.1fs", w.status_code, list(sess.cookies.keys()), time.monotonic()-t0)
    except Exception as e:
        log.warning("FP warm: %s", e)
    time.sleep(2)

    # A: Card with verify retry
    for city in ("vladivostok", "habarovsk"):
        url = f"https://www.farpost.ru/{city}/{cid}.html"
        for attempt in range(2):
            t0 = time.monotonic()
            try:
                r = sess.get(url, timeout=20)
                el = time.monotonic() - t0
                html = r.text
                fu = str(r.url)
                is_v = "/verify" in fu
                log.info("FP %s a%d: HTTP=%d len=%d verify=%s t=%.1fs", city, attempt+1, r.status_code, len(html), is_v, el)

                if not is_v and r.status_code == 200 and len(html) > 5000:
                    ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
                    pld = None
                    for b in ld_blocks:
                        try:
                            d = json.loads(b)
                            if isinstance(d,dict) and d.get("@type")=="Product": pld=d; break
                            if isinstance(d,list):
                                for i in d:
                                    if isinstance(i,dict) and i.get("@type")=="Product": pld=i; break
                                if pld: break
                        except: pass
                    pa = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                    t_m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
                    title = re.sub(r'<[^>]+>','',t_m.group(1)).strip() if t_m else None
                    og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
                    lb = re.findall(r'"(https?://static\.baza\.farpost\.ru/[^"]+)"', html)
                    photos = [p for p in lb if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))][:10]

                    key = f"card_{city}"
                    R[key] = {"ok": True, "http": 200, "len": len(html), "t": round(el,1),
                              "jsonld": bool(pld),
                              "ld_price": pld.get("offers",{}).get("price") if pld else None,
                              "attr_price": pa.group(1) if pa else None,
                              "og_price": og_p.group(1) if og_p else None,
                              "title": (title or "")[:60], "n_photos": len(photos)}
                    log.info("FP %s: ld=%s price=%s/%s/%s title=%s photos=%d",
                             city, R[key]["jsonld"], R[key]["ld_price"], R[key]["attr_price"],
                             R[key]["og_price"], (title or "?")[:40], len(photos))
                    break
                else:
                    R[f"verify_{city}_a{attempt+1}"] = {"http": r.status_code, "len": len(html),
                                                        "verify": is_v, "url": fu[:80], "t": round(el,1)}
                    if attempt == 0: time.sleep(5)
            except Exception as e:
                R[f"err_{city}_a{attempt+1}"] = {"error": str(e)[:120], "t": round(time.monotonic()-t0,1)}
                if attempt == 0: time.sleep(3)
        if any(k.startswith("card_") and R.get(k,{}).get("ok") for k in R):
            break

    # B: Mobile farpost
    t0 = time.monotonic()
    try:
        r = sess.get(f"https://m.farpost.ru/vladivostok/{cid}.html", timeout=15)
        el = time.monotonic() - t0
        R["mobile"] = {"http": r.status_code, "len": len(r.text), "t": round(el,1)}
    except Exception as e:
        R["mobile"] = {"error": str(e)[:80], "t": round(time.monotonic()-t0,1)}
    log.info("FP mobile: %s", R["mobile"])

    R["ok"] = any(v.get("ok") for v in R.values() if isinstance(v,dict))
    return R

# ─── AVITO ───
def avito_test():
    log.info("=== AVITO ===")
    R = {}
    from curl_cffi import requests as cffi
    url = "https://www.avito.ru/vladivostok/smartfony"
    hdrs = {"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8",
            "sec-ch-ua":'"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
            "sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"',
            "Sec-Fetch-Dest":"document","Sec-Fetch-Mode":"navigate","Sec-Fetch-Site":"none",
            "Sec-Fetch-User":"?1","Upgrade-Insecure-Requests":"1"}

    # A: Desktop search
    t0 = time.monotonic()
    try:
        r = cffi.get(url, impersonate="chrome", timeout=15, headers=hdrs)
        el = time.monotonic() - t0
        html = r.text
        captcha = "captcha" in html.lower() or "antibot" in html.lower()
        items = re.findall(r'data-qa="index-item"[^>]*>.*?data-price="(\d+)"', html, re.DOTALL)
        titles = re.findall(r'data-qa="index-item-title"[^>]*>([^<]+)<', html)
        R["search"] = {"http": r.status_code, "len": len(html), "captcha": captcha,
                       "n_prices": len(items), "n_titles": len(titles),
                       "first_prices": items[:3], "first_titles": [t.strip()[:40] for t in titles[:3]],
                       "t": round(el,1)}
    except Exception as e:
        R["search"] = {"error": str(e)[:100], "t": round(time.monotonic()-t0,1)}
    log.info("AV search: %s", R["search"])

    # B: Mobile
    t0 = time.monotonic()
    try:
        r = cffi.get(f"https://m.avito.ru/vladivostok/smartfony", impersonate="chrome", timeout=15,
                     headers={"Accept":"text/html","Accept-Language":"ru-RU"})
        el = time.monotonic() - t0
        R["mobile"] = {"http": r.status_code, "len": len(r.text), "t": round(el,1)}
    except Exception as e:
        R["mobile"] = {"error": str(e)[:80], "t": round(time.monotonic()-t0,1)}
    log.info("AV mobile: %s", R["mobile"])

    R["ok"] = R.get("search",{}).get("n_prices",0) > 0
    return R

# ─── MAIN ───
def main():
    t0 = time.monotonic()
    log.info("=== E2E V8 START ===")

    db, paths, cfg = setup_db()
    before = db_counts(db)
    log.info("DB BEFORE: %s", before)
    rep = {"before": before}

    rep["wb"] = wb_test()
    rep["ozon"] = ozon_test()
    rep["farpost"] = farpost_test()
    rep["avito"] = avito_test()

    # Intake: process existing dumps
    rep["intake"] = {}
    for d in ("e2e-wb-v1", "e2e-farpost-v2"):
        dd = BASE / d
        if not dd.exists(): continue
        for f in sorted(dd.glob("jarvis-eye-*.json")):
            try:
                r = intake_file(db, cfg, f, origin=f"V8-{d}")
                rep["intake"][f.name] = {"product_id": r.get("product_id"),
                                         "obs_created": r.get("observation_created"),
                                         "status": r.get("status")}
            except Exception as e:
                rep["intake"][f.name] = {"error": str(e)[:200]}

    after = db_counts(db)
    rep["after"] = after
    rep["delta"] = {k: (after[k]-before[k]) if isinstance(before[k],int) and isinstance(after[k],int) else "?" for k in before}

    # by_source
    try:
        with db.read() as conn:
            rows = conn.execute("""
                SELECT p.source, COUNT(DISTINCT p.id) as products,
                       COUNT(DISTINCT o.id) as obs, COUNT(DISTINCT pp.id) as pp
                FROM product p
                LEFT JOIN observation o ON o.product_id = p.id
                LEFT JOIN price_point pp ON pp.observation_id = o.id
                GROUP BY p.source""").fetchall()
            rep["by_source"] = [dict(r) for r in rows]
    except Exception as e:
        rep["by_source"] = str(e)[:200]

    el = time.monotonic() - t0
    rep["total_s"] = round(el,1)
    rep["time"] = NOW()

    out = BASE / "e2e_v8_results.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("=== E2E V8 DONE %.1fs ===", el)
    log.info("WB: ok=%s %s", rep["wb"].get("ok"), {k:v for k,v in rep["wb"].items() if isinstance(v,dict) and v.get("ok")})
    log.info("OZON: ok=%s %s", rep["ozon"].get("ok"), {k:v for k,v in rep["ozon"].items() if isinstance(v,dict) and v.get("ok")})
    log.info("FP: ok=%s %s", rep["farpost"].get("ok"), {k:v for k,v in rep["farpost"].items() if isinstance(v,dict) and v.get("ok")})
    log.info("AV: ok=%s %s", rep["avito"].get("ok"), {k:v for k,v in rep["avito"].items() if isinstance(v,dict) and v.get("ok")})
    log.info("DELTA: %s", rep["delta"])
    log.info("BY_SRC: %s", rep.get("by_source"))

if __name__ == "__main__":
    main()
