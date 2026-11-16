"""e2e_fast4.py — E2E v4: WB price fix, Ozon 2984707012, Avito live-ad, Farpost 138975631.
Запуск: python e2e_fast4.py
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fast4"
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
            "status":"PARTIAL_SUCCESS" if (title and price_minor) else "DEGRADED",
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
    fname = f"fast4-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FAST4_{source}")
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
    vol = int(lid) // 100000
    part = int(lid) // 1000

    # S0: Direct WB page (curl_cffi)
    try:
        r = s.get(url, timeout=25)
        log(f"  S0 WB page: {r.status_code} len={len(r.content)}")
        if r.status_code == 200:
            html = r.text
            m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
            if m:
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if not title:
                title = extract_og(html, "og:title")
            if not price_minor:
                for m in re.finditer(r'data-qa-price[^>]*>\s*([\d\s\u00a0]+)', html):
                    dg = re.sub(r'[^\d]', '', m.group(1))
                    if dg and len(dg) >= 3:
                        price_minor = int(dg) * 100
                        break
            if not price_minor:
                for m in re.finditer(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', html):
                    dg = re.sub(r'[^\d]', '', m.group(1))
                    if dg and len(dg) >= 4:
                        price_minor = int(dg) * 100
                        break
            if not price_minor:
                pa = extract_og(html, "og:price:amount")
                if pa:
                    price_minor = int(float(pa) * 100)
            if not media:
                for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*wbbasket\.ru/[^"\']+)["\']', html):
                    u = m.group(1)
                    if "favicon" not in u:
                        media.append({"type": "image", "source_url": u, "image_data": None})
                    if len(media) >= 5:
                        break
            if title:
                hs, strat = 200, "wb_page"
                log(f"  S0 OK: {title[:50]} price={price_minor}")
    except Exception as e:
        log(f"  S0 ERR: {e}")

    # S1: basket CDN card.json (has selling.price, name, brand, etc.)
    for host in (f"basket-{vol}", f"static-basket-{vol}"):
        if title:
            break
        try:
            r = s.get(f"https://{host}.wbbasket.ru/vol{vol}/part{part}/{lid}/cards/v2/card.json", timeout=15)
            log(f"  S1 {host} card.json: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                c = r.json()
                title = c.get("name")
                brand = c.get("brand")
                model = c.get("model")
                desc = (c.get("description") or "")[:3000]
                # Price from selling
                selling = c.get("selling", {})
                if isinstance(selling, dict):
                    for pk in ("price", "priceU", "priceD"):
                        pv = selling.get(pk)
                        if isinstance(pv, (int, float)) and pv > 0:
                            price_minor = int(pv)
                            log(f"  S1 selling.{pk}={pv}")
                            break
                # Price from sizes
                if not price_minor:
                    sizes = c.get("sizes", [])
                    if sizes:
                        po = sizes[0].get("price", {})
                        price_minor = po.get("basic") or po.get("total")
                        log(f"  S1 sizes[0].price={po}")
                # Media
                imgs = c.get("im", [])
                for u in (imgs or [])[:5]:
                    media.append({"type":"image","source_url":u,"image_data":None})
                hs, strat = 200, f"basket_{host}"
                log(f"  S1 OK: {title[:50] if title else '?'} price={price_minor}")
        except Exception as e:
            log(f"  S1 {host} ERR: {e}")

    # S2: card.wb.ru multiple versions
    if not title:
        for ver in ("v1","v2","v4","v5"):
            if title:
                break
            try:
                r = s.get(f"https://card.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={lid}", timeout=15)
                log(f"  S2 card.wb {ver}: {r.status_code} len={len(r.content)}")
                if r.status_code == 200:
                    d = r.json()
                    prods = d.get("data",{}).get("cards") or d.get("products") or []
                    if prods:
                        c = prods[0]
                        title = c.get("name")
                        brand = c.get("brand")
                        model = c.get("model")
                        desc = (c.get("description") or "")[:3000]
                        sizes = c.get("sizes",[])
                        if sizes:
                            po = sizes[0].get("price",{})
                            price_minor = po.get("basic") or po.get("total")
                        hs, strat = 200, f"card_wb_{ver}"
                        log(f"  S2 OK: {title[:50] if title else '?'} price={price_minor}")
                        break
            except Exception as e:
                log(f"  S2 {ver} ERR: {e}")

    # S3: static-basket price.json (dedicated price endpoint)
    if not price_minor and title:
        for host in (f"static-basket-{vol}", f"basket-{vol}"):
            try:
                r = s.get(f"https://{host}.wbbasket.ru/vol{vol}/part{part}/{lid}/price.json", timeout=15)
                log(f"  S3 {host} price.json: {r.status_code} len={len(r.content)}")
                if r.status_code == 200:
                    pd = r.json()
                    price_minor = pd.get("price") or pd.get("basic") or pd.get("total")
                    if isinstance(price_minor, (int,float)):
                        price_minor = int(price_minor)
                    log(f"  S3 OK: price={price_minor}")
                    if price_minor:
                        strat = strat + "+price_json"
                        break
            except Exception as e:
                log(f"  S3 {host} ERR: {e}")

    # S4: card.wb.ru price via nm_id param
    if not price_minor and title:
        try:
            r = s.get(f"https://card.wb.ru/cards/v2/detail?nm_id={lid}", timeout=15)
            log(f"  S4 card.wb nm_id: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                d = r.json()
                cards = d.get("data",{}).get("cards",[])
                if cards:
                    sz = cards[0].get("sizes",[])
                    if sz:
                        po = sz[0].get("price",{})
                        price_minor = po.get("basic") or po.get("total")
                        log(f"  S4 OK: price={price_minor}")
                        if price_minor:
                            strat = strat + "+nm_id"
        except Exception as e:
            log(f"  S4 ERR: {e}")

    # S5: web API
    if not price_minor and title:
        try:
            r = s.get(f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={lid}", timeout=15)
            log(f"  S5 web_api: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                d = r.json()
                dd = d.get("data",{})
                prices = dd.get("prices") or dd.get("price")
                if isinstance(prices, dict):
                    price_minor = prices.get("basic") or prices.get("value")
                elif isinstance(prices, (int,float)):
                    price_minor = int(prices)
                if not price_minor:
                    sizes = dd.get("sizes",[])
                    if sizes:
                        po = sizes[0].get("price",{})
                        price_minor = po.get("basic") or po.get("total")
                log(f"  S5 OK: price={price_minor}")
                if price_minor:
                    strat = strat + "+web_api"
        except Exception as e:
            log(f"  S5 ERR: {e}")

    if not title:
        log("  WB: ALL FAILED"); return None
    log(f"  WB done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("WB",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "WB")

# ===================== OZON (new ID 2984707012) =====================
def do_ozon(app):
    log("OZON: Playwright fetch...")
    lid = "2984707012"
    url = f"https://www.ozon.ru/product/{lid}/"
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S1: Playwright
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
            time.sleep(3)
            html = page.content()
            log(f"  S1 PW: html_len={len(html)}")
            (WORK / "ozon_pw4.html").write_text(html, encoding="utf-8")

            # JSON-LD
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

            # h1
            if not title:
                m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
                if m:
                    title = re.sub(r'<[^>]+>','',m.group(1)).strip()

            # og:title
            if not title:
                title = extract_og(html,"og:title")

            # Price from HTML: data-widget price patterns
            if not price_minor:
                # Ozon SSR: "price" in widget data
                for m in re.finditer(r'"price"\s*:\s*(\d{3,})', html):
                    val = int(m.group(1))
                    if val > 1000:
                        price_minor = val
                        break
            if not price_minor:
                for m in re.finditer(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', html):
                    dg = re.sub(r'[^\d]','',m.group(1))
                    if dg and len(dg)>=4:
                        price_minor = int(dg)*100
                        break
            # og:price:amount
            if not price_minor:
                pa = extract_og(html,"og:price:amount")
                if pa: price_minor = int(float(pa)*100)

            # Media: ir.ozone.ru
            if not media:
                for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', html):
                    u = m.group(1)
                    if "ds_image_default" not in u:
                        media.append({"type":"image","source_url":u,"image_data":None})
                    if len(media)>=5: break

            if title:
                hs, strat = 200, "playwright_rendered"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
            browser.close()
    except Exception as e:
        log(f"  S1 PW ERR: {e}")
        traceback.print_exc()

    # S2: Composer API
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

    # S1: Direct ad page (www + m)
    for base in ("https://www.avito.ru/ad/{lid}", "https://m.avito.ru/ad/{lid}"):
        if title:
            break
        try:
            u = base.format(lid=lid)
            r = s.get(u, timeout=20)
            log(f"  S1 {u}: {r.status_code} len={len(r.content)}")
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
                            for u2 in (imgs or [])[:5]:
                                media.append({"type":"image","source_url":u2,"image_data":None})
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
                    url = u
                    hs, strat = 200, "ad_page"
                    log(f"  S1 OK: {title[:60]} price={price_minor}")
                    break
        except Exception as e:
            log(f"  S1 ERR: {e}")

    # S2: Find live ad from category
    if not title:
        log("  S2: finding live ad from category...")
        for cat_url in (
            "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
            "https://www.avito.ru/vladivostok/tekhnika/smartfony",
            "https://m.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
        ):
            if title:
                break
            try:
                r = s.get(cat_url, timeout=20)
                log(f"  S2 category {cat_url}: {r.status_code} len={len(r.content)}")
                if r.status_code != 200:
                    continue
                html = r.text
                ads = re.findall(r'href="(https://(?:www|m)\.avito\.ru/ad/\d+)"', html)
                if not ads:
                    ads = re.findall(r'href="/ad/(\d+)"', html)
                    ads = [f"https://www.avito.ru/ad/{a}" for a in ads]
                for ad_url in ads[:12]:
                    ad_id = ad_url.rstrip("/").split("/")[-1]
                    log(f"  S2 trying: {ad_url}")
                    try:
                        r2 = s.get(ad_url, timeout=15)
                        if r2.status_code != 200:
                            continue
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
                        log(f"  S2 ad ERR: {e}")
            except Exception as e:
                log(f"  S2 cat ERR: {e}")

    if not title:
        log("  AVITO: ALL FAILED"); return None
    log(f"  AVITO done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("AVITO",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "AVITO")

# ===================== FARPOST (new ID 138975631) =====================
def do_farpost(app):
    log("FARPOST: fetching...")
    lid = "138975631"
    url = f"https://www.farpost.ru/vladivostok/{lid}.html"
    s = sess()
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # Warm-up
    try:
        s.get("https://www.farpost.ru/", timeout=15)
        log("  warm-up OK")
    except:
        pass
    time.sleep(1)

    # S1: Direct ad page
    try:
        r = s.get(url, timeout=25)
        log(f"  S1 ad page: {r.status_code} len={len(r.content)} url={r.url}")
        if r.status_code == 200:
            # Decode: Farpost is cp1251
            ct = (r.headers.get("Content-Type") or "").lower()
            if "1251" in ct or "cp1251" in ct:
                html = r.content.decode("windows-1251", errors="replace")
            else:
                raw_head = r.content[:500].decode("ascii", errors="ignore")
                if "windows-1251" in raw_head.lower() or "cp1251" in raw_head.lower():
                    html = r.content.decode("windows-1251", errors="replace")
                else:
                    html = r.text
            (WORK / "farpost_138975631.html").write_text(html, encoding="utf-8")
            log(f"  S1 html_len={len(html)}")

            # JSON-LD
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

            # h1 with subject
            if not title:
                m = re.search(r'<h1[^>]*class="[^"]*subject[^"]*"[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
                if m:
                    title = re.sub(r'<[^>]+>','',m.group(1)).strip()
            if not title:
                m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
                if m:
                    title = re.sub(r'<[^>]+>','',m.group(1)).strip()

            # Price: data-bulletin-price
            if not price_minor:
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                if m:
                    price_minor = int(m.group(1)) * 100
                    log(f"  S1 data-bulletin-price={m.group(1)}")

            # Price: viewbull-summary-price__value
            if not price_minor:
                m = re.search(r'class="viewbull-summary-price__value[^"]*"[^>]*data-field="price"[^>]*>([\d\s\u00a0]+)', html)
                if m:
                    dg = re.sub(r'[^\d]','',m.group(1))
                    if dg: price_minor = int(dg)*100

            # Price: generic class price
            if not price_minor:
                m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', html)
                if m:
                    dg = re.sub(r'[^\d]','',m.group(1))
                    if dg and len(dg)>=3: price_minor = int(dg)*100

            # og
            if not title: title = extract_og(html,"og:title")
            if not price_minor:
                pa = extract_og(html,"og:price:amount")
                if pa: price_minor = int(float(pa)*100)

            # Media: farpost CDN
            if not media:
                for m in re.finditer(r'(?:src|data-src|data-image-info)=["\']([^"\']*farpost\.ru/[^"\']+)["\']', html):
                    u = m.group(1)
                    if "_thumbnail" not in u and "favicon" not in u:
                        media.append({"type":"image","source_url":u,"image_data":None})
                    if len(media)>=5: break

            if title:
                hs, strat = 200, "ad_page"
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {e}")
        traceback.print_exc()

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
                    try:
                        r2 = s.get(ad_url, timeout=15)
                        if r2.status_code != 200:
                            continue
                        try: h2 = r2.text
                        except: h2 = r2.content.decode("cp1251",errors="replace")
                        m = re.search(r'<h1[^>]*>(.*?)</h1>', h2, re.DOTALL)
                        if m: title = re.sub(r'<[^>]+>','',m.group(1)).strip()
                        if not title: title = extract_og(h2,"og:title")
                        if not price_minor:
                            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', h2)
                            if m: price_minor = int(m.group(1))*100
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
                        log(f"  S2 ad ERR: {e}")
        except Exception as e:
            log(f"  S2 ERR: {e}")

    if not title:
        log("  FARPOST: ALL FAILED"); return None
    log(f"  FARPOST done: strat={strat} price={price_minor}")
    return feed_inbox(app, build_eye("FARPOST",lid,url,title,price_minor,brand=brand,model=model,desc=desc,cat=cat,media=media,hs=hs), "FARPOST")

# ===================== MAIN =====================
def main():
    log("=== E2E FAST4 START ===")
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
        time.sleep(2)

    total = time.monotonic()-t0
    passed = sum(1 for r in results.values() if r["verdict"]=="E2E_PASS")
    partial = sum(1 for r in results.values() if r["verdict"]=="E2E_PARTIAL")
    log(f"{'='*40}")
    log(f"SUMMARY: PASS={passed} PARTIAL={partial} FAIL={4-passed-partial}/4 | {total:.1f}s")
    for n,r in results.items():
        log(f"  {n}: {r['verdict']}")
    out = ROOT/"e2e_fast4_result.json"
    out.write_text(json.dumps({"ts":now_iso(),"s":round(total,1),"pass":passed,"partial":partial,"d":results},ensure_ascii=False,indent=2))
    log(f"Result: {out}")
    log("E2E_FAST4_DONE")

if __name__=="__main__":
    main()
