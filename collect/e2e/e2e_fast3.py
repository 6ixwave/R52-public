"""e2e_fast3.py — E2E v3: исправленный WB basket, Playwright Ozon, живые Avito/Farpost.
Запуск: python e2e_fast3.py
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fast3"
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
    fname = f"fast3-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FAST3_{source}")
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

    # S1: card.wb.ru (nm_id param)
    try:
        r = s.get(f"https://card.wb.ru/cards/v2/detail?nm_id={lid}", timeout=15)
        log(f"  S1a card.wb.ru nm_id: {r.status_code} len={len(r.content)}")
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
                hs, strat = 200, "card_wb_nm_id"
                log(f"  S1a OK: {title[:50] if title else '?'} price={price_minor}")
    except Exception as e:
        log(f"  S1a ERR: {e}")

    # S1b: card.wb.ru (nm param)
    if not title:
        try:
            r = s.get(f"https://card.wb.ru/cards/v2/detail?nm={lid}", timeout=15)
            log(f"  S1b card.wb.ru nm: {r.status_code} len={len(r.content)}")
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
                    hs, strat = 200, "card_wb_nm"
                    log(f"  S1b OK: {title[:50] if title else '?'} price={price_minor}")
        except Exception as e:
            log(f"  S1b ERR: {e}")

    # S2: basket CDN — corrected vol/part
    if not title:
        try:
            vol = int(lid) // 100000
            part = int(lid) // 1000
            r = s.get(f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{lid}/cards/v2/card.json", timeout=15)
            log(f"  S2 basket vol={vol} part={part}: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                c = r.json()
                title = c.get("name")
                brand = c.get("brand")
                model = c.get("model")
                desc = (c.get("description") or "")[:3000]
                sizes = c.get("sizes",[])
                if sizes:
                    price_minor = sizes[0].get("price",{}).get("basic")
                hs, strat = 200, "basket_cdn_fixed"
                log(f"  S2 OK: {title[:50] if title else '?'} price={price_minor}")
        except Exception as e:
            log(f"  S2 ERR: {e}")

    # S3: basket static (different path pattern)
    if not title:
        try:
            vol = int(lid) // 100000
            part = int(lid) // 1000
            r = s.get(f"https://static-basket-42.wbbasket.ru/vol{vol}/part{part}/{lid}/cards/v2/card.json", timeout=15)
            log(f"  S3 static-basket: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                c = r.json()
                title = c.get("name")
                brand = c.get("brand")
                model = c.get("model")
                desc = (c.get("description") or "")[:3000]
                sizes = c.get("sizes",[])
                if sizes:
                    price_minor = sizes[0].get("price",{}).get("basic")
                hs, strat = 200, "static_basket"
                log(f"  S3 OK: {title[:50] if title else '?'} price={price_minor}")
        except Exception as e:
            log(f"  S3 ERR: {e}")

    # S4: existing JSON (real data, maybe no price)
    if not title:
        ex = ROOT / "e2e-final-work" / "collector_out" / "WB" / "jarvis-eye-wb-5cd4b856.json"
        if ex.exists():
            log(f"  S4: existing JSON")
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
            log(f"  S4 OK: {title[:50] if title else '?'} price={price_minor}")

    # S5: WB price via static-basket price endpoint
    if not price_minor and title:
        try:
            vol = int(lid) // 100000
            part = int(lid) // 1000
            r = s.get(f"https://static-basket-42.wbbasket.ru/vol{vol}/part{part}/{lid}/price.json", timeout=15)
            log(f"  S5 price.json: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                pd = r.json()
                price_minor = pd.get("price") or pd.get("basic") or pd.get("total")
                if isinstance(price_minor, (int,float)):
                    price_minor = int(price_minor)
                log(f"  S5 OK: price={price_minor}")
        except Exception as e:
            log(f"  S5 ERR: {e}")

    # S6: WB price via card.wb.ru price
    if not price_minor and title:
        try:
            r = s.get(f"https://card.wb.ru/cards/v1/detail?nm={lid}", timeout=15)
            log(f"  S6 card.wb v1: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                d = r.json()
                cards = d.get("data",{}).get("cards",[])
                if cards:
                    sz = cards[0].get("sizes",[])
                    if sz:
                        price_minor = sz[0].get("price",{}).get("basic")
                        log(f"  S6 OK: price={price_minor}")
        except Exception as e:
            log(f"  S6 ERR: {e}")

    if not title:
        log("  WB: ALL FAILED"); return None
    log(f"  WB done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("WB",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "WB")

# ===================== OZON (Playwright) =====================
def do_ozon(app):
    log("OZON: Playwright fetch...")
    lid = "562267"
    url = f"https://www.ozon.ru/product/{lid}/"
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S1: Playwright with wait_for_selector
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width":1920,"height":1080},
                locale="ru-RU",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("h1, [data-widget*='product'], .tile-root__title", timeout=20000)
            except:
                pass
            time.sleep(2)
            html = page.content()
            log(f"  S1 PW: html_len={len(html)}")
            (WORK / "ozon_pw3.html").write_text(html, encoding="utf-8")

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

            if not title:
                m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
                if m:
                    title = re.sub(r'<[^>]+>','',m.group(1)).strip()

            if not title:
                title = extract_og(html,"og:title")

            if not price_minor:
                for m in re.finditer(r'"price"\s*:\s*(\d+)', html):
                    price_minor = int(m.group(1))
                    break
                if not price_minor:
                    for m in re.finditer(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', html):
                        dg = re.sub(r'[^\d]','',m.group(1))
                        if dg and len(dg)>=3:
                            price_minor = int(dg)*100
                            break

            if title:
                hs, strat = 200, "playwright_rendered"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
            browser.close()
    except Exception as e:
        log(f"  S1 PW ERR: {e}")
        traceback.print_exc()

    # S2: Composer API with headers
    if not title:
        try:
            s = sess()
            r = s.get(
                f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/",
                headers={"Accept":"application/json","X-Ozon-Client":"web"},
                timeout=20
            )
            log(f"  S2 composer: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                d = r.json()
                state = d.get("state", d)
                for w in state.get("widgets", []):
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
                if title:
                    hs, strat = 200, "composer_api"
                    log(f"  S2 OK: {title[:60]} price={price_minor}")
        except Exception as e:
            log(f"  S2 ERR: {e}")

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

    # S1: Direct ad page
    try:
        r = s.get(url, timeout=20)
        log(f"  S1 ad page: {r.status_code} len={len(r.content)}")
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
                hs, strat = 200, "ad_page"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")

    # S2: Find live ad from category
    if not title:
        log("  S2: finding live ad from category...")
        try:
            r = s.get("https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony", timeout=20)
            log(f"  S2 category: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                html = r.text
                ads = re.findall(r'href="(https://www\.avito\.ru/ad/\d+)"', html)
                for ad_url in ads[:10]:
                    ad_id = ad_url.split("/")[-1]
                    log(f"  S2 trying: {ad_url}")
                    r2 = s.get(ad_url, timeout=15)
                    if r2.status_code == 200:
                        h2 = r2.text
                        for ld in extract_jsonld(h2):
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
                        if not title:
                            title = extract_og(h2,"og:title")
                        if not price_minor:
                            pa = extract_og(h2,"og:price:amount")
                            if pa: price_minor = int(float(pa)*100)
                        if title:
                            lid, url = ad_id, ad_url
                            hs, strat = 200, "category_live_ad"
                            log(f"  S2 OK: {title[:60]} price={price_minor} id={lid}")
                            break
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

    # S1: Direct ad page
    try:
        r = s.get(url, timeout=20)
        log(f"  S1 ad page: {r.status_code} len={len(r.content)}")
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
            if not title: title = extract_og(html,"og:title")
            if not price_minor:
                pa = extract_og(html,"og:price:amount")
                if pa: price_minor = int(float(pa)*100)
            if title:
                hs, strat = 200, "ad_page"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")

    # S2: Find live ad from Farpost category
    if not title:
        log("  S2: finding live ad from category...")
        try:
            r = s.get("https://www.farpost.ru/vladivostok/tekhnika/smartfony/", timeout=20)
            log(f"  S2 category: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                try: html = r.text
                except: html = r.content.decode("cp1251",errors="replace")
                ads = re.findall(r'href="(/vladivostok/\d+\.html)"', html)
                for ad_path in ads[:10]:
                    ad_url = f"https://www.farpost.ru{ad_path}"
                    ad_id = ad_path.split("/")[-1].replace(".html","")
                    log(f"  S2 trying: {ad_url}")
                    r2 = s.get(ad_url, timeout=15)
                    if r2.status_code == 200:
                        try: h2 = r2.text
                        except: h2 = r2.content.decode("cp1251",errors="replace")
                        m = re.search(r'<h1[^>]*>(.*?)</h1>', h2, re.DOTALL)
                        if m: title = re.sub(r'<[^>]+>','',m.group(1)).strip()
                        if not title: title = extract_og(h2,"og:title")
                        if not price_minor:
                            m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', h2)
                            if m:
                                dg = re.sub(r'[^\d]','',m.group(1))
                                if dg: price_minor = int(dg)*100
                        if title:
                            lid, url = ad_id, ad_url
                            hs, strat = 200, "category_live_ad"
                            log(f"  S2 OK: {title[:60]} price={price_minor} id={lid}")
                            break
        except Exception as e:
            log(f"  S2 ERR: {e}")

    if not title:
        log("  FARPOST: ALL FAILED"); return None
    log(f"  FARPOST done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("FARPOST",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "FARPOST")

# ===================== MAIN =====================
def main():
    log("=== E2E FAST3 START ===")
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
    out = ROOT/"e2e_fast3_result.json"
    out.write_text(json.dumps({"ts":now_iso(),"s":round(total,1),"pass":passed,"partial":partial,"d":results},ensure_ascii=False,indent=2))
    log(f"Result: {out}")
    log("E2E_FAST3_DONE")

if __name__=="__main__":
    main()
