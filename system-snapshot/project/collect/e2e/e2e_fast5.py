#!/usr/bin/env python3
"""e2e_fast5.py — E2E v5: all 4 platforms, proven FAST4 patterns + WB Playwright + Avito search."""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fast5"
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

# === Proven patterns from FAST4 ===
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
    fname = f"fast5-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FAST5_{source}")
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

# ===================== WB (Playwright + curl fallbacks) =====================
def do_wb(app):
    log("WB: fetching...")
    lid = "1097975275"
    url = f"https://www.wildberries.ru/catalog/{lid}/detail.aspx"
    vol = int(lid) // 100000
    part = int(lid) // 1000
    title = brand = model = desc = cat = None
    price_minor = None
    media = []
    hs = 0
    strat = ""

    # S0: Playwright (renders WBAAS JS challenge)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            page.goto(url, timeout=35000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            html = page.content()
            browser.close()
        log(f"  S0 PW: len={len(html)}")
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if not title:
            title = extract_og(html, "og:title")
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
        for ld in extract_jsonld(html):
            if isinstance(ld, dict):
                if not title and ld.get("name"):
                    title = ld["name"]
                if not price_minor:
                    pv = ld.get("price") or (ld.get("offers") or {}).get("price")
                    if pv:
                        price_minor = int(float(pv) * 100)
        for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*wbbasket\.ru/[^"\']+)["\']', html):
            u = m.group(1)
            if "favicon" not in u:
                media.append({"type":"image","source_url":u,"image_data":None})
            if len(media) >= 5:
                break
        if title:
            # Validate: reject stub/error pages
            _stub_pats = ("что-то не так", "ошибка", "error", "page not found",
                          "загрузка", "loading", "404", "503")
            _is_stub = (len(title) < 15 or
                        any(p in title.lower() for p in _stub_pats) or
                        (price_minor is None and len(title) < 25))
            if _is_stub:
                log(f"  S0 STUB: '{title[:40]}' price={price_minor} -> fallback")
                title = None
                price_minor = None
                media = []
            else:
                hs, strat = 200, "wb_playwright"
                log(f"  S0 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S0 PW ERR: {str(e)[:120]}")

    # S1: basket CDN card.json (proper host enumeration)
    if not title:
        s = sess()
        for n in range(1, 61):
            if title:
                break
            host = f"basket-{n:02d}.wbbasket.ru"
            for path in (f"/vol{vol}/part{part}/{lid}/info/ru/card.json",
                         f"/vol{vol}/part{part}/{lid}/cards/v2/card.json"):
                if title:
                    break
                try:
                    r = s.get(f"https://{host}{path}",
                              headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"},
                              timeout=10)
                    if r.status_code == 200:
                        c = r.json()
                        all_keys = list(c.keys())
                        log(f"  S1 {host} keys({len(all_keys)})={all_keys[:16]}")
                        # Title: imt_name (new format) or name (old) or nested
                        title = (c.get("imt_name") or c.get("name") or c.get("title")
                                 or (c.get("data",{}).get("name") if isinstance(c.get("data"),dict) else None))
                        if not title and isinstance(c.get("data"), list) and c["data"]:
                            title = c["data"][0].get("name")
                        if not title:
                            log(f"  S1 {host}: 200 but no title. keys={all_keys[:10]}")
                            continue
                        # Brand/Model from options list or direct fields
                        brand = c.get("brand")
                        model = c.get("model")
                        opts = c.get("options")
                        if isinstance(opts, list):
                            for o in opts:
                                if not isinstance(o, dict):
                                    continue
                                on = (o.get("name") or "").lower()
                                ov = o.get("value")
                                if on in ("бренд","brand","производитель") and not brand:
                                    brand = ov
                                elif on in ("модель","model","артикул") and not model:
                                    model = ov
                                elif on in ("цена","price") and not price_minor:
                                    try:
                                        pv = int(str(ov).replace("\u20bd","").replace(" ","").replace(",","."))
                                        if pv > 100:
                                            price_minor = pv * 100
                                    except: pass
                        desc = (c.get("description") or "")[:3000]
                        cat = c.get("subj_name") or c.get("category")
                        # Price from selling dict
                        selling = c.get("selling")
                        if isinstance(selling, dict):
                            log(f"  S1 selling keys={list(selling.keys())[:10]}")
                            for pk in ("priceU","priceD","price","oldPrice"):
                                pv = selling.get(pk)
                                if isinstance(pv,(int,float)) and pv > 100:
                                    price_minor = int(pv)
                                    break
                            if not price_minor:
                                for k,v in selling.items():
                                    if isinstance(v,(int,float)) and v > 1000:
                                        price_minor = int(v)
                                        break
                        # Price from data dict
                        if not price_minor:
                            data = c.get("data")
                            if isinstance(data, dict):
                                for pk in ("price","priceU","priceD"):
                                    pv = data.get(pk)
                                    if isinstance(pv,(int,float)) and pv > 100:
                                        price_minor = int(pv)
                                        break
                        # Media: media key (list of str or dict)
                        med = c.get("media")
                        if isinstance(med, list):
                            for u in med[:5]:
                                if isinstance(u, str) and u.startswith("http"):
                                    media.append({"type":"image","source_url":u,"image_data":None})
                                elif isinstance(u, dict):
                                    uurl = u.get("url") or u.get("src") or u.get("link")
                                    if uurl:
                                        media.append({"type":"image","source_url":uurl,"image_data":None})
                        # Fallback: im key
                        if not media:
                            for u in (c.get("im") or [])[:5]:
                                if isinstance(u, str):
                                    media.append({"type":"image","source_url":u,"image_data":None})
                        hs, strat = 200, f"basket_{n:02d}"
                        log(f"  S1 OK [{host}]: '{title[:50]}' brand={brand} price={price_minor} media={len(media)}")
                        break
                    elif r.status_code not in (404,):
                        log(f"  S1 {host} {path[-25:]}: HTTP {r.status_code}")
                except Exception as e:
                    log(f"  S1 {host} ERR: {str(e)[:80]}")

    # S1.5: catalog.wb.ru (mirror)
    if not title:
        for ver in ("v1","v2","v4","v5"):
            if title:
                break
            try:
                s = sess()
                r = s.get(f"https://catalog.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={lid}", timeout=12)
                log(f"  S1.5 catalog {ver}: {r.status_code} len={len(r.content)}")
                if r.status_code == 200:
                    d = r.json()
                    prods = d.get("products") or d.get("data",{}).get("items") or []
                    if prods:
                        c = prods[0]
                        title = c.get("name")
                        brand = c.get("brand")
                        model = c.get("model")
                        sizes = c.get("sizes",[])
                        if sizes:
                            po = sizes[0].get("price",{})
                            price_minor = po.get("basic") or po.get("total")
                        if not price_minor:
                            selling = c.get("selling",{})
                            if isinstance(selling, dict):
                                for pk in ("price","priceU","priceD"):
                                    pv = selling.get(pk)
                                    if isinstance(pv,(int,float)) and pv > 0:
                                        price_minor = int(pv)
                                        break
                        hs, strat = 200, f"catalog_{ver}"
                        log(f"  S1.5 OK: {title[:50] if title else '?'} price={price_minor}")
            except Exception as e:
                log(f"  S1.5 {ver} ERR: {str(e)[:80]}")

    # S2: card.wb.ru API
    if not title:
        for ver in ("v2","v1","v4","v5"): 
            if title:
                break
            try:
                s = sess()
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
                        sizes = c.get("sizes",[])
                        if sizes:
                            po = sizes[0].get("price",{})
                            price_minor = po.get("basic") or po.get("total")
                        hs, strat = 200, f"card_wb_{ver}"
                        log(f"  S2 OK: {title[:50] if title else '?'} price={price_minor}")
            except Exception as e:
                log(f"  S2 {ver} ERR: {str(e)[:100]}")

    # S3: WB web API
    if not title:
        try:
            s = sess()
            r = s.get(f"https://www.wildberries.ru/web/api/catalog/v1/card?nm={lid}", timeout=15)
            log(f"  S3 web-api: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                d = r.json()
                title = d.get("name") or d.get("title")
                brand = d.get("brand")
                if not price_minor:
                    pv = d.get("priceU") or d.get("price")
                    if isinstance(pv,(int,float)) and pv > 0:
                        price_minor = int(pv) if pv > 1000 else int(pv*100)
                if title:
                    hs, strat = 200, "wb_web_api"
                    log(f"  S3 OK: {title[:50]} price={price_minor}")
        except Exception as e:
            log(f"  S3 ERR: {str(e)[:100]}")

    if not title:
        log("  WB: ALL FAILED")
        return None
    # Final validation: reject stubs even from API sources
    _stub_pat = ("что-то не так", "ошибка", "page not found", "404", "503")
    if len(title) < 10 or any(p in title.lower() for p in _stub_pat):
        log(f"  WB REJECT: stub title '{title[:40]}'")
        return None
    log(f"  WB done: strat={strat} price={price_minor} title='{title[:50]}'")
    return build_eye("WB", lid, url, title, price_minor, "RUB", brand, model, desc, cat, media, hs)

# ===================== OZON (multi-strategy v2) =====================
def do_ozon(app):
    log("OZON: fetching...")
    lid = "2984707012"
    url = f"https://www.ozon.ru/product/{lid}/"
    title = price_minor = None
    media = []

    # S1: Playwright with response interception + in-page fetch
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-features=IsolateOrigins,site-per-process",
                      "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="ru-RU",
                viewport={"width":1366,"height":900},
                extra_http_headers={"Accept-Language":"ru-RU,ru;q=0.9"},
            )
            ctx.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});
                window.chrome={runtime:{}};
            """)
            page = ctx.new_page()
            _api_bodies = []
            def _on_resp(resp):
                try:
                    u = resp.url.lower()
                    if ('composer-api' in u or '/api/' in u) and resp.status == 200:
                        b = resp.text()
                        if len(b) > 200: _api_bodies.append(b)
                except: pass
            page.on('response', _on_resp)
            page.goto("https://www.ozon.ru/", timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(12000)
            try:
                for sel in ["button:has-text('Принять')","button:has-text('ОК')",
                            "[data-widget='cookie-banner'] button",".cookie-accept"]:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click(); page.wait_for_timeout(1500); break
            except: pass
            log(f"  S1 warm-up: len={len(page.content())} url={page.url[:60]}")
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except: pass
            page.wait_for_timeout(5000)
            log(f"  S1 prod: len={len(page.content())} url={page.url[:60]} title={page.title()[:50]}")
            if not _api_bodies:
                try:
                    _fetched = page.evaluate(f"""async () => {{
                        try {{
                            const r = await fetch('/api/composer-api.bx/page/json/v2?url=/product/{lid}/',
                                {{headers:{{'Accept':'application/json'}},credentials:'include'}});
                            return await r.text();
                        }} catch(e) {{ return null; }}
                    }}""")
                    if _fetched and len(_fetched) > 200:
                        _api_bodies.append(_fetched)
                        log(f"  S1 FETCH: len={len(_fetched)}")
                except Exception as e:
                    log(f"  S1 FETCH ERR: {str(e)[:80]}")
            try:
                _ws = page.evaluate("() => { try { const s=window.__STATE__||window.__INITIAL_STATE__; return s?JSON.stringify(s):null } catch(e){return null} }")
                if _ws and len(_ws) > 200:
                    _api_bodies.append(_ws)
                    log(f"  S1 STATE: len={len(_ws)}")
            except: pass
            html = page.content()
            browser.close()
        for _ab in _api_bodies:
            try:
                _ad = json.loads(_ab)
                # Ozon composer-api: widgetStates structure
                ws = _ad.get("widgetStates") or {}
                if isinstance(ws, dict) and ws:
                    log(f"  S1 WS keys: {list(ws.keys())[:15]}")
                    for wname in ("ProductTitle","ProductInfo","ProductCard","Title"):
                        w = ws.get(wname)
                        if isinstance(w, dict):
                            t = w.get("title") or w.get("name") or w.get("text")
                            if t and len(str(t)) > 10:
                                title = str(t)
                                log(f"  S1 WS {wname}: '{title[:60]}'")
                                break
                    for wname in ("ProductPrice","Price","ProductCard","PriceInfo"):
                        w = ws.get(wname)
                        if isinstance(w, dict):
                            pv = (w.get("price") or w.get("value") or
                                  (w.get("priceInfo") or {}).get("price") or
                                  (w.get("data") or {}).get("price"))
                            if pv:
                                pv_s = str(pv).replace('.','').replace(',','')
                                if pv_s.isdigit() and int(pv_s) > 100:
                                    price_minor = int(pv_s)*100
                                    log(f"  S1 WS {wname}: price={price_minor}")
                                    break
                # Generic deep-search fallback
                if not title or not price_minor:
                    def _fo(obj, depth=0):
                        nonlocal title, price_minor
                        if depth > 10: return
                        if isinstance(obj, dict):
                            if not title:
                                for k in ("title","name","productTitle","productName"):
                                    v = obj.get(k)
                                    if v and isinstance(v,str) and len(v) > 12:
                                        title = v; break
                            if not price_minor:
                                for k in ("price","priceInfo","priceData","oldPrice"):
                                    pv = obj.get(k)
                                    if isinstance(pv, dict):
                                        pv = pv.get("price") or pv.get("value") or pv.get("text")
                                    if pv and str(pv).replace('.','').replace(',','').isdigit():
                                        v = int(str(pv).replace(',',''))
                                        if v > 1000:
                                            price_minor = v*100; break
                            for v in obj.values():
                                if isinstance(v,(dict,list)): _fo(v,depth+1)
                        elif isinstance(obj,list):
                            for v in obj[:30]: _fo(v,depth+1)
                    _fo(_ad)
                if title:
                    log(f"  S1 API OK: {title[:60]} price={price_minor}")
                    break
            except Exception as e:
                log(f"  S1 API parse ERR: {str(e)[:60]}")
        log(f"  S1 PW: html_len={len(html)}")
        if len(html) > 8000:
            # __NEXT_DATA__
            for m in re.finditer(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    nd = json.loads(m.group(1))
                    props = nd.get("props",{}).get("pageProps",{})
                    for key in ("product","item","data","initialData"):
                        pd = props.get(key)
                        if isinstance(pd, dict):
                            title = title or pd.get("name") or pd.get("title")
                            if not price_minor:
                                pv = pd.get("price") or (pd.get("priceInfo") or {}).get("price")
                                if pv:
                                    price_minor = int(float(pv)*100)
                except: pass
            # window.__STATE__ / window.__INITIAL_STATE__
            for m in re.finditer(r'window\.__\w+\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL):
                try:
                    st = json.loads(m.group(1))
                    def deep_find(obj, depth=0):
                        nonlocal title, price_minor
                        if depth > 6: return
                        if isinstance(obj, dict):
                            if not title and obj.get("name") and len(str(obj.get("name"))) > 10:
                                title = str(obj["name"])
                            if not price_minor:
                                for pk in ("price","priceInfo","priceData"):
                                    pv = obj.get(pk)
                                    if isinstance(pv, dict):
                                        pv = pv.get("price") or pv.get("value")
                                    if pv and str(pv).replace('.','').replace(',','').isdigit():
                                        price_minor = int(float(str(pv).replace(',','')))*100
                            for v in obj.values():
                                deep_find(v, depth+1)
                        elif isinstance(obj, list):
                            for v in obj[:20]:
                                deep_find(v, depth+1)
                    deep_find(st)
                except: pass
            # Title / OG
            if not title:
                m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
                if m:
                    title = m.group(1).strip()
            if not title:
                title = extract_og(html, "og:title")
            # Price patterns
            if not price_minor:
                for m in re.finditer(r'"price"\s*:\s*"?(\d+)"?', html):
                    v = int(m.group(1))
                    if v > 1000:
                        price_minor = v
                        break
            if not price_minor:
                for m in re.finditer(r'(\d[\d\s\u00a0]{3,})\s*\u20bd', html):
                    dg = re.sub(r'[^\d]','',m.group(1))
                    if dg and int(dg) > 1000:
                        price_minor = int(dg)*100
                        break
            # JSON-LD
            for ld in extract_jsonld(html):
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    if not title and ld.get("name"):
                        title = ld["name"]
                    if not price_minor:
                        pv = ld.get("price") or (ld.get("offers") or {}).get("price")
                        if pv:
                            price_minor = int(float(pv)*100)
            # Images
            for m in re.finditer(r'(https://ir\.ozon\.ru/[^"\s]+)', html):
                media.append({"type":"image","source_url":m.group(1),"image_data":None})
                if len(media) >= 5:
                    break
            if title:
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 PW ERR: {str(e)[:120]}")

    # S2: curl_cffi main URL (chrome) with session warm-up
    if not title:
        try:
            from curl_cffi import requests as cr
            s2 = cr.Session(impersonate="chrome")
            s2.headers.update({"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9"})
            try:
                s2.get("https://www.ozon.ru/", timeout=10)
            except: pass
            time.sleep(1)
            r = s2.get(url, timeout=20)
            log(f"  S2 main: {r.status_code} len={len(r.content)}")
            if r.status_code == 200 and len(r.content) > 5000:
                html = r.text
                m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
                if m: title = m.group(1).strip()
                if not title: title = extract_og(html, "og:title")
                for ld in extract_jsonld(html):
                    if isinstance(ld, dict) and ld.get("@type") in ("Product","Offer"):
                        if not title and ld.get("name"): title = ld["name"]
                        if not price_minor:
                            pv = ld.get("price") or (ld.get("offers") or {}).get("price")
                            if pv: price_minor = int(float(pv)*100)
                if not price_minor:
                    for m in re.finditer(r'"price"\s*:\s*"?(\d+)"?', html):
                        v = int(m.group(1))
                        if v > 1000: price_minor = v; break
                for m in re.finditer(r'(https://ir\.ozon\.ru/[^"\s]+)', html):
                    media.append({"type":"image","source_url":m.group(1),"image_data":None})
                    if len(media) >= 5: break
                if title: log(f"  S2 main OK: {title[:60]} price={price_minor}")
        except Exception as e:
            log(f"  S2 main ERR: {str(e)[:100]}")
    # S2b: curl_cffi mobile (safari)
    if not title:
        try:
            from curl_cffi import requests as cr
            s2b = cr.Session(impersonate="safari")
            s2b.headers.update({"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9"})
            murl = f"https://m.ozon.ru/product/{lid}/"
            r = s2b.get(murl, timeout=15)
            log(f"  S2b mobile: {r.status_code} len={len(r.content)}")
            if r.status_code == 200 and len(r.content) > 5000:
                html = r.text
                m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
                if m: title = m.group(1).strip()
                if not title: title = extract_og(html, "og:title")
                for ld in extract_jsonld(html):
                    if isinstance(ld, dict) and ld.get("@type") in ("Product","Offer"):
                        if not title and ld.get("name"): title = ld["name"]
                        if not price_minor:
                            pv = ld.get("price") or (ld.get("offers") or {}).get("price")
                            if pv: price_minor = int(float(pv)*100)
                if not price_minor:
                    for m in re.finditer(r'"price"\s*:\s*"?(\d+)"?', html):
                        v = int(m.group(1))
                        if v > 1000: price_minor = v; break
                for m in re.finditer(r'(https://ir\.ozon\.ru/[^"\s]+)', html):
                    media.append({"type":"image","source_url":m.group(1),"image_data":None})
                    if len(media) >= 5: break
                if title: log(f"  S2b mobile OK: {title[:60]} price={price_minor}")
        except Exception as e:
            log(f"  S2b ERR: {str(e)[:100]}")

    # S3: Composer API (fix SSL with verify=False)
    if not title:
        try:
            from curl_cffi import requests as cr
            s3 = cr.Session(impersonate="chrome")
            api_urls = [
                f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/",
                f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/",
            ]
            for au in api_urls:
                if title:
                    break
                try:
                    r = s3.get(au, timeout=15, headers={
                        "Accept":"application/json",
                        "OZON-API-VERSION":"v1",
                        "Referer":url,
                    }, verify=False)
                    log(f"  S3 API {au[:50]}: {r.status_code} len={len(r.content)}")
                    if r.status_code == 200:
                        d = r.json()
                        def find_ozon_data(obj, depth=0):
                            nonlocal title, price_minor
                            if depth > 8: return
                            if isinstance(obj, dict):
                                if not title:
                                    for k in ("title","name","productTitle"):
                                        if obj.get(k) and len(str(obj.get(k))) > 10:
                                            title = str(obj[k])
                                            break
                                if not price_minor:
                                    for k in ("price","priceInfo","priceData"):
                                        pv = obj.get(k)
                                        if isinstance(pv, dict):
                                            pv = pv.get("price") or pv.get("value") or pv.get("text")
                                        if pv and str(pv).replace('.','').replace(',','').isdigit():
                                            price_minor = int(float(str(pv).replace(',','')))*100
                                            break
                                for v in obj.values():
                                    if isinstance(v, (dict, list)):
                                        find_ozon_data(v, depth+1)
                            elif isinstance(obj, list):
                                for v in obj[:30]:
                                    find_ozon_data(v, depth+1)
                        find_ozon_data(d)
                        if title:
                            log(f"  S3 OK: {title[:60]} price={price_minor}")
                except Exception as e:
                    log(f"  S3 API ERR: {str(e)[:80]}")
                time.sleep(0.5)
        except Exception as e:
            log(f"  S3 ERR: {str(e)[:100]}")

    # S4: Search API (find any product)
    if not title:
        try:
            from curl_cffi import requests as cr
            s4 = cr.Session(impersonate="chrome")
            r = s4.get(f"https://www.ozon.ru/api/entrypoint/pwa/shop/mobile/v1/search?text=iphone&limit=5",
                      timeout=15, headers={"Accept":"application/json"})
            log(f"  S4 search: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                d = r.json()
                items = d.get("items") or d.get("products") or []
                if items:
                    item = items[0]
                    lid = str(item.get("id") or item.get("sku") or lid)
                    title = item.get("title") or item.get("name")
                    pv = item.get("price") or (item.get("priceInfo") or {}).get("price")
                    if pv:
                        price_minor = int(float(pv)*100)
                    url = f"https://www.ozon.ru/product/{lid}/"
                    if title:
                        log(f"  S4 OK: {title[:60]} price={price_minor} lid={lid}")
        except Exception as e:
            log(f"  S4 ERR: {str(e)[:100]}")

    if not title:
        log("  OZON: ALL FAILED")
        return None
    # Stub validation
    _oz_stub = ("похоже", "нет соединения", "ошибка", "error", "загрузите",
                "page not found", "404", "503", "обновите страницу")
    if len(title) < 15 or any(p in title.lower() for p in _oz_stub):
        log(f"  OZON STUB: '{title[:50]}' price={price_minor} -> REJECT")
        return None
    log(f"  OZON done: '{title[:50]}' price={price_minor}")
    return build_eye("OZON", lid, url, title, price_minor, "RUB", media=media, hs=200)

# ===================== AVITO (multi-strategy) =====================
def do_avito(app):
    log("AVITO: fetching...")
    s = sess()
    s.headers.update({
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8",
        "Referer":"https://www.avito.ru/",
        "Upgrade-Insecure-Requests":"1",
    })
    title = price_minor = None
    media = []
    ad_id = None
    ad_url = ""

    # Warm-up: main page (let cookies set, longer delay for rate-limit)
    try:
        r = s.get("https://www.avito.ru/", timeout=15)
        log(f"  warm-up: {r.status_code} cookies={len(s.cookies)}")
    except Exception as e:
        log(f"  warm-up ERR: {str(e)[:80]}")
    time.sleep(5)

    # S0: RSS feed + sitemap (lighter, less bot-protected)
    rss_urls = [
        "https://www.avito.ru/rss/vladivostok/tekhnika_i_svyaz/smartfony",
        "https://www.avito.ru/rss/vladivostok/tekhnika_i_svyaz",
        "https://www.avito.ru/rss/vladivostok",
        "https://www.avito.ru/sitemap.xml",
    ]
    ad_ids = []
    for i, ru in enumerate(rss_urls, 1):
        try:
            r = s.get(ru, timeout=15)
            log(f"  S0 RSS {i}: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                links = re.findall(r'avito\.ru/[^"<>]*?/(\d{7,})[^"<>]*', r.text)
                if not links:
                    links = re.findall(r'/ad/(\d{7,})', r.text)
                ad_ids = list(dict.fromkeys(links))[:10]
                if ad_ids:
                    log(f"  S0 RSS {i} found {len(ad_ids)}: {ad_ids[:5]}")
                    break
        except Exception as e:
            log(f"  S0 RSS {i} ERR: {str(e)[:80]}")
        time.sleep(2)

    # S1: Category/search pages (curl_cffi + requests, multiple patterns)
    search_urls = [
        "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
        "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony?search_saver=true",
        "https://www.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
        "https://www.avito.ru/vladivostok/avtomobili/legkovye",
    ]
    ad_ids = []
    # S1a: curl_cffi chrome (better TLS fingerprint)
    try:
        from curl_cffi import requests as cr
        s1c = cr.Session(impersonate="chrome")
        s1c.headers.update({"Accept":"text/html","Accept-Language":"ru-RU,ru;q=0.9"})
        for i, su in enumerate(search_urls, 1):
            if ad_ids: break
            try:
                r = s1c.get(su, timeout=20, allow_redirects=True)
                log(f"  S1c {su[:55]}: {r.status_code} len={len(r.content)}")
                if r.status_code in (200, 301, 302) and len(r.content) > 15000:
                    pats = [
                        r'avito\.ru/vladivostok/[^"<>]+?/(\d{8,})["<>]',
                        r'avito\.ru/vladivostok/[^"<>]+?-(\d{8,})["<>]',
                        r'data-ad-id="(\d{7,})"',
                        r'/ad/(\d{7,})',
                        r'"adId"\s*:\s*"?(\d{7,})"?',
                        r'"id"\s*:\s*"?(\d{9,})"?',
                    ]
                    for pat in pats:
                        links = re.findall(pat, r.text)
                        if links:
                            ad_ids = list(dict.fromkeys(links))[:10]
                            log(f"  S1c found {len(ad_ids)} ads: {ad_ids[:5]}")
                            break
            except Exception as e:
                log(f"  S1c ERR: {str(e)[:80]}")
            time.sleep(3)
    except Exception as e:
        log(f"  S1c init ERR: {str(e)[:80]}")
    # S1b: requests session fallback
    if not ad_ids:
        for i, su in enumerate(search_urls, 1):
            if ad_ids: break
            try:
                r = s.get(su, timeout=15, allow_redirects=True)
                log(f"  S1 {su[:55]}: {r.status_code} len={len(r.content)}")
                if r.status_code in (200, 301, 302) and len(r.content) > 15000:
                    pats = [
                        r'avito\.ru/vladivostok/[^"\s>]+?/(\d{8,})["\s>?]',
                        r'avito\.ru/vladivostok/[^"\s>]+?-(\d{8,})["\s>?]',
                        r'data-ad-id="(\d{7,})"',
                        r'/ad/(\d{7,})',
                        r'"adId"\s*:\s*"?(\d{7,})"?',
                        r'"id"\s*:\s*"?(\d{9,})"?',
                    ]
                    for pat in pats:
                        links = re.findall(pat, r.text)
                        if links:
                            ad_ids = list(dict.fromkeys(links))[:10]
                            log(f"  S1 found {len(ad_ids)} ads: {ad_ids[:5]}")
                            break
            except Exception as e:
                log(f"  S1 ERR: {str(e)[:100]}")
            time.sleep(3)

    # S2: Direct ad URLs (broader set of known/recent IDs)
    if not ad_ids:
        candidates = ["7494308705", "7501234567", "7510000001",
                       "7480000001", "7470000001"]
        for aid in candidates:
            for base in (f"https://www.avito.ru/ad/{aid}",
                         f"https://m.avito.ru/ad/{aid}"):
                try:
                    r = s.get(base, timeout=12)
                    log(f"  S2 {base}: {r.status_code} len={len(r.content)}")
                    if r.status_code == 200 and len(r.content) > 5000:
                        ad_ids = [aid]
                        break
                except Exception as e:
                    log(f"  S2 ERR: {str(e)[:80]}")
                time.sleep(0.5)
            if ad_ids:
                break

    # S3: Mobile / lite endpoint
    if not ad_ids:
        try:
            r = s.get("https://m.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony", timeout=12)
            log(f"  S3 mobile: {r.status_code} len={len(r.content)}")
            if r.status_code == 200:
                links = re.findall(r'/ad/(\d{7,})', r.text)
                ad_ids = list(dict.fromkeys(links))[:5]
                if ad_ids:
                    log(f"  S3 found: {ad_ids[:5]}")
        except Exception as e:
            log(f"  S3 ERR: {str(e)[:80]}")

    # S4: Playwright (kvartiry-first + DOM query + state extraction)
    if not ad_ids:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale="ru-RU",
                    viewport={"width":1366,"height":900},
                    extra_http_headers={"Accept-Language":"ru-RU,ru;q=0.9"},
                )
                ctx.add_init_script("""
                    Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                    window.chrome={runtime:{}};
                    Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});
                """)
                page = ctx.new_page()
                # Warm-up on main page
                page.goto("https://www.avito.ru/", timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(10000)
                try:
                    page.mouse.wheel(0, 300)
                    page.wait_for_timeout(2000)
                except: pass
                # Try kvartiry first (less bot-protected per S1 results), then smartfony
                cat_urls = [
                    "https://www.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
                    "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
                ]
                html = ""
                for cu in cat_urls:
                    try:
                        page.goto(cu, timeout=45000, wait_until="domcontentloaded")
                        try:
                            page.wait_for_load_state("networkidle", timeout=15000)
                        except: pass
                        page.wait_for_timeout(5000)
                        _ch = page.content()
                        log(f"  S4 {cu[30:65]}: len={len(_ch)}")
                        if len(_ch) > 20000 and 'error_no_entry' not in _ch:
                            html = _ch
                            break
                    except Exception as e:
                        log(f"  S4 nav ERR: {str(e)[:60]}")
                log(f"  S4 best: len={len(html)}")
                # DOM query for ad links
                if html and len(html) > 15000:
                    try:
                        _dom_ads = page.evaluate("""
                            () => {
                                const links = document.querySelectorAll('a[href*="/ad/"], a[data-ad-id]');
                                const ids = [];
                                links.forEach(a => {
                                    const h = a.href || '';
                                    const m = h.match(/(\\d{8,})/);
                                    if (m) ids.push(m[1]);
                                    const did = a.getAttribute('data-ad-id');
                                    if (did) ids.push(did);
                                });
                                return [...new Set(ids)];
                            }
                        """)
                        if _dom_ads:
                            ad_ids = _dom_ads[:10]
                            log(f"  S4 DOM found {len(ad_ids)}: {ad_ids[:5]}")
                    except Exception as e:
                        log(f"  S4 DOM ERR: {str(e)[:60]}")
                # Regex fallback on HTML
                if not ad_ids and html:
                    pats = [
                        r'avito\.ru/[^"<>\s]+?/(\d{8,})["<>\s]',
                        r'avito\.ru/[^"<>\s]+?-(\d{8,})["<>\s]',
                        r'data-ad-id="(\d{7,})"',
                        r'data-id="(\d{8,})"',
                        r'/ad/(\d{7,})',
                        r'"adId"\s*:\s*"?(\d{7,})"?',
                    ]
                    for pat in pats:
                        links = re.findall(pat, html)
                        if links:
                            ad_ids = list(dict.fromkeys(links))[:10]
                            log(f"  S4 REGEX found {len(ad_ids)}: {ad_ids[:5]}")
                            break
                # __PRELOADED_STATE__
                if not ad_ids:
                    try:
                        _ps = page.evaluate("() => { try { const s=window.__PRELOADED_STATE__; return s?JSON.stringify(s):null } catch(e){return null} }")
                        if _ps and len(_ps) > 100:
                            log(f"  S4 STATE: len={len(_ps)}")
                            _psd = json.loads(_ps)
                            def _fa(obj, depth=0):
                                nonlocal ad_ids
                                if depth > 6 or ad_ids: return
                                if isinstance(obj, dict):
                                    for k in ("adId","id","itemId"):
                                        v = obj.get(k)
                                        if v and str(v).isdigit() and len(str(v)) >= 8:
                                            ad_ids = [str(v)]; break
                                    for v in obj.values():
                                        if isinstance(v,(dict,list)): _fa(v,depth+1)
                                elif isinstance(obj,list):
                                    for v in obj[:20]: _fa(v,depth+1)
                            _fa(_psd)
                    except Exception as e:
                        log(f"  S4 STATE ERR: {str(e)[:60]}")
                browser.close()
        except Exception as e:
            log(f"  S4 PW ERR: {str(e)[:100]}")

    # Fetch best ad
    for aid in (ad_ids or ["7494308705"])[:5]:
        for base in (f"https://www.avito.ru/ad/{aid}", f"https://m.avito.ru/ad/{aid}"):
            try:
                r = s.get(base, timeout=12)
                log(f"  AD {aid}: {r.status_code} len={len(r.content)}")
                if r.status_code == 200 and len(r.content) > 5000:
                    html = r.text
                    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
                    title = m.group(1).strip() if m else extract_og(html, "og:title")
                    if not title:
                        continue
                    # Stub check
                    _av_stub = ("авито", "страница не найдена", "ошибка", "404",
                                "загрузите", "обновите")
                    if len(title) < 12 or any(p in title.lower() for p in _av_stub):
                        log(f"  AD {aid} STUB: '{title[:40]}'")
                        title = None
                        continue
                    for ld in extract_jsonld(html):
                        if isinstance(ld, dict):
                            pv = ld.get("price") or (ld.get("offers") or {}).get("price")
                            if pv:
                                price_minor = int(float(pv)*100)
                                break
                    if not price_minor:
                        pm = re.search(r'"price"\s*:\s*"?(\d+)"?', html)
                        if pm and int(pm.group(1)) > 100:
                            price_minor = int(pm.group(1))
                    if not price_minor:
                        pm = re.search(r'([\d\s\u00a0]{3,})\s*\u20bd', html)
                        if pm:
                            dg = re.sub(r'[^\d]','',pm.group(1))
                            if dg and int(dg) > 100:
                                price_minor = int(dg)*100
                    for m in re.finditer(r'(https://[^"\s]*mmedia\.avito\.st/[^"\s]+)', html):
                        media.append({"type":"image","source_url":m.group(1),"image_data":None})
                        if len(media) >= 5:
                            break
                    ad_id, ad_url = aid, base
                    log(f"  AVITO OK: {title[:60]} price={price_minor}")
                    break
                time.sleep(0.5)
            except Exception as e:
                log(f"  AD {aid} ERR: {str(e)[:80]}")
        if title:
            break

    if not title:
        log("  AVITO: ALL FAILED")
        return None
    return build_eye("AVITO", ad_id, ad_url, title, price_minor, "RUB", media=media, hs=200)

# ===================== FARPOST (proven) =====================
def do_farpost(app):
    log("FARPOST: fetching...")
    lid = "138975631"
    url = f"https://www.farpost.ru/vladivostok/tech/communication/cellphones/apple-iphone-17-256gb-green-sage-zelenyj-esim-bez-rustore-ucenka-ion-{lid}.html"
    s = sess()
    try:
        s.get("https://www.farpost.ru/", timeout=10)
        log("  warm-up OK")
    except: pass
    time.sleep(0.5)
    title = price_minor = None
    media = []
    try:
        r = s.get(url, timeout=15)
        log(f"  S1: {r.status_code} len={len(r.content)}")
        if r.status_code == 200:
            html = r.content.decode("windows-1251", errors="replace")
            m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
            if m:
                title = m.group(1).strip()
            if not title:
                title = extract_og(html, "og:title")
            pm = re.search(r'itemprop="price"[^>]*content="(\d+)"', html)
            if pm:
                price_minor = int(pm.group(1)) * 100
            if not price_minor:
                for ld in extract_jsonld(html):
                    if isinstance(ld, dict):
                        pv = ld.get("price") or (ld.get("offers") or {}).get("price")
                        if pv:
                            price_minor = int(float(pv)*100)
                            break
            if not price_minor:
                pm = re.search(r'(\d[\d\s\u00a0]{2,})\s*(?:\u20bd|\u0440\u0443\u0431)', html)
                if pm:
                    dg = re.sub(r'[^\d]','',pm.group(1))
                    if dg:
                        price_minor = int(dg)*100
            for m in re.finditer(r'(https://[^"\s]*farpost\.ru[^"\s]*\.(?:jpg|jpeg|png))', html):
                media.append({"type":"image","source_url":m.group(1),"image_data":None})
                if len(media) >= 5:
                    break
            if title:
                log(f"  S1 OK: {title[:60]} price={price_minor}")
    except Exception as e:
        log(f"  S1 ERR: {str(e)[:100]}")

    if not title:
        log("  FARPOST: ALL FAILED")
        return None
    return build_eye("FARPOST", lid, url, title, price_minor, "RUB", media=media, hs=200)

# ===================== MAIN =====================
log("=== E2E FAST5 START ===")
log(f"DB: {DB}")

app = make_app()
log("App ready")

for name, fn in [("WB", do_wb), ("OZON", do_ozon), ("AVITO", do_avito), ("FARPOST", do_farpost)]:
    try:
        eye = fn(app)
        if eye:
            dec = feed_inbox(app, eye, name)
            sql = verify_sqlite(name)
            verdict = "E2E_PASS" if (dec.get("observation_created") or sql["product_count"] > 0) else "E2E_FAIL"
            results[name] = {"verdict": verdict, "sqlite": sql, "inbox": dec}
        else:
            results[name] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite(name), "inbox": None}
    except Exception as e:
        log(f"  {name} EXCEPTION: {e}")
        traceback.print_exc()
        results[name] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite(name), "inbox": None, "error": str(e)}
    log(f"  VERDICT: {results[name]['verdict']} | P={results[name]['sqlite']['product_count']} O={results[name]['sqlite']['observation_count']} PP={results[name]['sqlite']['price_point_count']}")
    log("=" * 50)

elapsed = time.monotonic() - t0
pass_count = sum(1 for v in results.values() if v["verdict"] == "E2E_PASS")
log(f"=== SUMMARY: PASS={pass_count} FAIL={4-pass_count}/4 | {elapsed:.1f}s ===")
for k, v in results.items():
    log(f"  {k}: {v['verdict']}")

out = {"ts": datetime.now(TZ).isoformat(), "s": round(elapsed,1), "pass": pass_count, "d": results}
out_path = ROOT / "e2e_fast5_result.json"
out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
log(f"Result: {out_path}")
log("E2E_FAST5_DONE")
