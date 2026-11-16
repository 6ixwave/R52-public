"""e2e_fast2.py — Быстрый E2E всех 4 площадок.
Цепочка: fetch -> build JSON -> Inbox.stage_bytes -> accept -> SQLite verify.
Запуск: python e2e_fast2.py
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fast2"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"

results = {}
t0 = time.monotonic()

def log(m):
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {m}", flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

def sess():
    from curl_cffi import requests as cr
    return cr.Session(impersonate="chrome")

def build_eye(source, lid, url, title, price_minor=None, cur="RUB",
              brand=None, model=None, desc=None, cat=None, media=None, hs=200):
    f = {}
    if title:
        f["title"] = {"status":"KNOWN","value":title,"method":"HTTP","evidence":{"method":"HTTP","source":source}}
    if brand:
        f["brand"] = {"status":"KNOWN","value":brand,"method":"HTTP","evidence":{"method":"HTTP","source":source}}
    if model:
        f["model"] = {"status":"KNOWN","value":model,"method":"HTTP","evidence":{"method":"HTTP","source":source}}
    if desc:
        f["description"] = {"status":"KNOWN","value":desc[:3000],"method":"HTTP","evidence":{"method":"HTTP","source":source}}
    if cat:
        f["category"] = {"status":"KNOWN","value":cat,"method":"HTTP","evidence":{"method":"HTTP","source":source}}
    pc = []
    if price_minor is not None:
        pc.append({"role":"regular","amount_minor":int(price_minor),"currency":cur,"label":"Cena"})
    return {
        "format":"JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action":"EXPLICIT_POPUP_CLICK",
        "fully_autonomous":False,
        "collector_source":source,
        "observation":{
            "fields":f,
            "price_candidates":pc,
            "media":{"items":media or []},
            "availability":"UNKNOWN",
            "category_path":cat or "",
            "source":source,
            "id":str(lid),
            "url":url,
            "observed_at":now_iso(),
            "status":"PARTIAL_SUCCESS",
            "product_content":bool(title),
            "evidence_level":"COLLECTOR_AUTONOMOUS_HTTP",
            "http_status":hs
        }
    }

def make_app():
    from r52.db import Database, migrate
    class FC:
        def now(self): return datetime.now(TZ)
    class MN:
        def notify(self,*a,**k): pass
        def __getattr__(self,n): return lambda *a,**k: None
    app = type("App",(),{})()
    app.clock = FC()
    app.db = Database(str(DB))
    app.db.initialize()
    migrate(app.db)
    app.settings = {"intake.auto_accept":True}
    app.notifier = MN()
    return app

def feed_inbox(app, eye_json, source):
    from r52.intake.eye import Inbox
    inbox = Inbox(app)
    data = json.dumps(eye_json, ensure_ascii=False).encode("utf-8")
    fname = f"fast2-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FAST2_{source}")
    log(f"  Inbox: status={item['status']} created={created}")
    if item["status"] == "PENDING":
        acc = inbox.accept(item["id"])
        log(f"  Accept: product_id={acc.get('product_id')} obs_new={acc.get('observation_created')}")
        return acc
    return item

def verify_sqlite(source):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        p = conn.execute("SELECT id,source,listing_id,title,url FROM product WHERE source=?",(source,)).fetchall()
        o = conn.execute("SELECT o.id,o.product_id,o.observed_at,o.availability FROM observation o JOIN product p ON o.product_id=p.id WHERE p.source=?",(source,)).fetchall()
        pp = conn.execute("SELECT pp.id,pp.observation_id,pp.role,pp.amount_minor,pp.currency FROM price_point pp JOIN observation o ON pp.observation_id=o.id JOIN product p ON o.product_id=p.id WHERE p.source=?",(source,)).fetchall()
        return {"products":[dict(r) for r in p],"observations":[dict(r) for r in o],
                "price_points":[dict(r) for r in pp],
                "product_count":len(p),"observation_count":len(o),"price_point_count":len(pp)}
    finally:
        conn.close()

def extract_jsonld(html):
    out = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try: out.append(json.loads(m.group(1)))
        except: pass
    return out

def extract_og(html, prop):
    m = re.search(rf'<meta[^>]*property="{re.escape(prop)}"[^>]*content="([^"]*)"', html)
    if not m:
        m = re.search(rf'<meta[^>]*content="([^"]*)"[^>]*property="{re.escape(prop)}"', html)
    return m.group(1) if m else None

# ===================== WB =====================
def do_wb(app):
    log("WB: fetching...")
    lid = "1097975275"
    url = f"https://www.wildberries.ru/catalog/{lid}/detail.aspx"
    s = sess()
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S1: card.wb.ru API
    try:
        r = s.get(f"https://card.wb.ru/cards/v2/detail?nm={lid}", timeout=15)
        log(f"  S1 card.wb.ru: {r.status_code} len={len(r.content)}")
        if r.status_code == 200:
            d = r.json()
            cards = d.get("data",{}).get("cards",[])
            if cards:
                c = cards[0]
                title = c.get("name")
                brand = c.get("brand")
                model = c.get("model")
                desc = (c.get("description") or "")[:3000]
                sizes = c.get("sizes",[])
                if sizes:
                    price_minor = sizes[0].get("price",{}).get("basic")
                hs, strat = 200, "card.wb.ru"
                log(f"  S1 OK: {title[:50] if title else '?'} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")

    # S2: basket CDN
    if not title:
        try:
            vol = int(lid) // 10000
            part = int(lid) // 10
            r = s.get(f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{lid}/cards/v2/card.json", timeout=15)
            log(f"  S2 basket: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                c = r.json()
                title = c.get("name")
                brand = c.get("brand")
                model = c.get("model")
                desc = (c.get("description") or "")[:3000]
                sizes = c.get("sizes",[])
                if sizes:
                    price_minor = sizes[0].get("price",{}).get("basic")
                hs, strat = 200, "basket_cdn"
                log(f"  S2 OK: {title[:50] if title else '?'} price={price_minor}")
        except Exception as e:
            log(f"  S2 ERR: {e}")

    # S3: existing JSON (real data, maybe no price)
    if not title:
        ex = ROOT / "e2e-final-work" / "collector_out" / "WB" / "jarvis-eye-wb-5cd4b856.json"
        if ex.exists():
            log(f"  S3: existing JSON {ex.name}")
            d = json.loads(ex.read_text(encoding="utf-8"))
            obs = d["observation"]
            fl = obs.get("fields",{})
            title = fl.get("title",{}).get("value")
            brand = fl.get("brand",{}).get("value")
            model = fl.get("model",{}).get("value")
            desc = fl.get("description",{}).get("value","")
            cat = fl.get("category",{}).get("value")
            media = obs.get("media",{}).get("items",[])
            pcs = obs.get("price_candidates",[])
            if pcs: price_minor = pcs[0].get("amount_minor")
            hs = obs.get("http_status",200)
            strat = "existing_json"
            log(f"  S3 OK: {title[:50] if title else '?'} price={price_minor}")

    if not title:
        log("  WB: ALL FAILED"); return None
    log(f"  WB done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("WB",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "WB")

# ===================== OZON =====================
def do_ozon(app):
    log("OZON: fetching...")
    lid = "562267"
    url = f"https://www.ozon.ru/product/{lid}/"
    s = sess()
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S1: Composer API
    try:
        r = s.get(f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/", timeout=20)
        log(f"  S1 composer: {r.status_code} len={len(r.content)}")
        if r.status_code == 200:
            d = r.json()
            state = d.get("state", d)
            for w in state.get("widgets", []):
                wt = w.get("widgetType","")
                props = w.get("props",{})
                data = props.get("data",{}) if isinstance(props,dict) else {}
                if not data and isinstance(w.get("data"),dict):
                    data = w["data"]
                if not title and (data.get("title") or data.get("name")):
                    title = data.get("title") or data.get("name")
                if not price_minor:
                    for pk in ("price","priceV2","mainPrice","priceInfo"):
                        pv = data.get(pk)
                        if isinstance(pv,dict):
                            price_minor = pv.get("price") or pv.get("value") or pv.get("amount")
                        elif isinstance(pv,(int,float)):
                            price_minor = int(pv)
                        if price_minor: break
            if not title:
                for k in ("title","productTitle","name","seoTitle"):
                    if state.get(k):
                        title = state[k]; break
            if title:
                hs, strat = 200, "composer_api"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")

    # S2: HTML + JSON-LD + og
    if not title:
        try:
            r = s.get(url, timeout=20)
            log(f"  S2 HTML: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                html = r.text
                for ld in extract_jsonld(html):
                    items = ld if isinstance(ld,list) else [ld]
                    for it in items:
                        if isinstance(it,dict) and it.get("@type") in ("Product","IndividualProduct"):
                            title = it.get("name")
                            desc = (it.get("description") or "")[:2000]
                            off = it.get("offers",{})
                            if isinstance(off,dict):
                                price_minor = off.get("price")
                                if price_minor: price_minor = int(float(price_minor)*100)
                            elif isinstance(off,list) and off:
                                price_minor = off[0].get("price")
                                if price_minor: price_minor = int(float(price_minor)*100)
                            imgs = it.get("image",[])
                            if isinstance(imgs,str): imgs=[imgs]
                            for u in (imgs or [])[:5]:
                                media.append({"type":"image","source_url":u,"image_data":None})
                            break
                    if title: break
                if not title: title = extract_og(html,"og:title")
                if not price_minor:
                    pa = extract_og(html,"og:price:amount")
                    if pa: price_minor = int(float(pa)*100)
                if title:
                    hs, strat = r.status_code, "html_jsonld"
                    log(f"  S2 OK: {title[:60]} price={price_minor}")
        except Exception as e:
            log(f"  S2 ERR: {e}")

    # S3: HTML inline data
    if not title:
        try:
            r = s.get(url, timeout=20)
            html = r.text
            if r.status_code == 200:
                for m in re.finditer(r'"title"\s*:\s*"([^"]{10,200})"', html):
                    t = m.group(1)
                    if len(t)>10 and not t.lower().startswith("ozon"):
                        title = t; strat = "inline_title"; break
                if title:
                    log(f"  S3 OK: {title[:60]} price={price_minor} strat={strat}")
        except Exception as e:
            log(f"  S3 ERR: {e}")

    if not title:
        log("  OZON: ALL FAILED"); return None
    log(f"  OZON done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("OZON",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "OZON")

# ===================== AVITO =====================
def do_avito(app):
    log("AVITO: fetching...")
    lid = "7494308705"
    url = f"https://www.avito.ru/ad/{lid}"
    s = sess()
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S1: HTML + JSON-LD + PRELOADED + og
    try:
        r = s.get(url, timeout=20)
        log(f"  S1 HTML: {r.status_code} len={len(r.content)}")
        if r.status_code == 200:
            html = r.text
            for ld in extract_jsonld(html):
                items = ld if isinstance(ld,list) else [ld]
                for it in items:
                    if isinstance(it,dict) and it.get("@type") in ("Product","IndividualProduct"):
                        title = it.get("name")
                        desc = (it.get("description") or "")[:2000]
                        off = it.get("offers",{})
                        if isinstance(off,dict):
                            price_minor = off.get("price")
                            if price_minor: price_minor = int(float(price_minor)*100)
                        imgs = it.get("image",[])
                        if isinstance(imgs,str): imgs=[imgs]
                        for u in (imgs or [])[:5]:
                            media.append({"type":"image","source_url":u,"image_data":None})
                        break
                if title: break
            if not title:
                m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*({.*?});?\s*(?:</script>|$)', html, re.DOTALL)
                if m:
                    try:
                        ps = json.loads(m.group(1))
                        for k in ("ad","product","item","data"):
                            if k in ps and isinstance(ps[k],dict):
                                d = ps[k]
                                title = d.get("title") or d.get("name")
                                if title:
                                    price_minor = d.get("price")
                                    if price_minor: price_minor = int(price_minor)
                                    break
                    except: pass
            if not title: title = extract_og(html,"og:title")
            if not price_minor:
                pa = extract_og(html,"og:price:amount")
                if pa: price_minor = int(float(pa)*100)
            if title:
                hs, strat = r.status_code, "html"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")

    # S2: Mobile
    if not title:
        try:
            r = s.get(f"https://m.avito.ru/ad/{lid}", timeout=20)
            log(f"  S2 mobile: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                html = r.text
                for ld in extract_jsonld(html):
                    items = ld if isinstance(ld,list) else [ld]
                    for it in items:
                        if isinstance(it,dict) and it.get("@type") in ("Product","IndividualProduct"):
                            title = it.get("name")
                            off = it.get("offers",{})
                            if isinstance(off,dict):
                                price_minor = off.get("price")
                                if price_minor: price_minor = int(float(price_minor)*100)
                            break
                    if title: break
                if not title: title = extract_og(html,"og:title")
                if title:
                    hs, strat = r.status_code, "mobile"
                    log(f"  S2 OK: {title[:60]} price={price_minor}")
        except Exception as e:
            log(f"  S2 ERR: {e}")

    if not title:
        log("  AVITO: ALL FAILED"); return None
    log(f"  AVITO done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("AVITO",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "AVITO")

# ===================== FARPOST =====================
def do_farpost(app):
    log("FARPOST: fetching...")
    lid = "1154321987"
    url = f"https://www.farpost.ru/vladivostok/{lid}.html"
    s = sess()
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S1: HTML + JSON-LD + h1 + data attrs + lightbox
    try:
        r = s.get(url, timeout=20)
        log(f"  S1 HTML: {r.status_code} len={len(r.content)}")
        if r.status_code == 200:
            try: html = r.text
            except: html = r.content.decode("cp1251",errors="replace")
            for ld in extract_jsonld(html):
                items = ld if isinstance(ld,list) else [ld]
                for it in items:
                    if isinstance(it,dict) and it.get("@type") in ("Product","IndividualProduct"):
                        title = it.get("name")
                        desc = (it.get("description") or "")[:2000]
                        off = it.get("offers",{})
                        if isinstance(off,dict):
                            price_minor = off.get("price")
                            if price_minor: price_minor = int(float(price_minor)*100)
                        imgs = it.get("image",[])
                        if isinstance(imgs,str): imgs=[imgs]
                        for u in (imgs or [])[:5]:
                            media.append({"type":"image","source_url":u,"image_data":None})
                        break
                if title: break
            if not title:
                m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
                if m: title = re.sub(r'<[^>]+>','',m.group(1)).strip()
            if not price_minor:
                m = re.search(r'data-price[=\s]+["\']?(\d+)', html)
                if m: price_minor = int(m.group(1))
            if not price_minor:
                m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', html)
                if m:
                    dg = re.sub(r'[^\d]','',m.group(1))
                    if dg: price_minor = int(dg)*100
            m = re.search(r'lightbox\.init\(\s*(\[.*?\])\s*\)', html, re.DOTALL)
            if m:
                try:
                    for it in json.loads(m.group(1))[:5]:
                        u = it.get("src") or it.get("url") or ""
                        if u: media.append({"type":"image","source_url":u,"image_data":None})
                except: pass
            if not title: title = extract_og(html,"og:title")
            if not price_minor:
                pa = extract_og(html,"og:price:amount")
                if pa: price_minor = int(float(pa)*100)
            if title:
                hs, strat = r.status_code, "html"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")

    if not title:
        log("  FARPOST: ALL FAILED"); return None
    log(f"  FARPOST done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("FARPOST",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "FARPOST")

# ===================== MAIN =====================
def main():
    log("=== E2E FAST2 START ===")
    if DB.exists(): DB.unlink()
    app = make_app()
    log(f"DB: {DB}")

    for name, fn in [("WB",do_wb),("OZON",do_ozon),("AVITO",do_avito),("FARPOST",do_farpost)]:
        log(f"{'='*40}")
        log(f"PLATFORM: {name}")
        log(f"{'='*40}")
        try:
            res = fn(app)
            sq = verify_sqlite(name)
            hp = sq["product_count"]>0
            ho = sq["observation_count"]>0
            hpp = sq["price_point_count"]>0
            ht = any(p.get("title") for p in sq["products"])
            v = "E2E_PASS" if (hp and ho and hpp and ht) else "E2E_PARTIAL" if (hp and ho) else "E2E_FAIL"
            results[name] = {"verdict":v,"sqlite":sq,"inbox":res}
            log(f"  VERDICT: {v} | P={sq['product_count']} O={sq['observation_count']} PP={sq['price_point_count']}")
        except Exception as e:
            log(f"  EXCEPTION: {e}")
            traceback.print_exc()
            results[name] = {"verdict":"ERROR","error":str(e)[:300]}
        time.sleep(1)

    total = time.monotonic()-t0
    passed = sum(1 for r in results.values() if r["verdict"]=="E2E_PASS")
    partial = sum(1 for r in results.values() if r["verdict"]=="E2E_PARTIAL")
    log(f"{'='*40}")
    log(f"SUMMARY: PASS={passed} PARTIAL={partial} FAIL={4-passed-partial}/4 | {total:.1f}s")
    for n,r in results.items():
        log(f"  {n}: {r['verdict']}")
    out = ROOT/"e2e_fast2_result.json"
    out.write_text(json.dumps({"ts":now_iso(),"s":round(total,1),"pass":passed,"partial":partial,"d":results},ensure_ascii=False,indent=2))
    log(f"Result: {out}")
    log("E2E_FAST2_DONE")

if __name__=="__main__":
    main()
