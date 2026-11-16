#!/usr/bin/env python3
"""e2e_fix13.py — All 4 platforms with fixed price logic.
WB: Price API PRIMARY (not supplier_id)
Farpost: data-bulletin-price × 100 fix
Ozon: Playwright (proven)
Avito: 3 new strategies (API, curl_cffi direct, PW mobile)
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix13"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix13_result.json"
LOG = ROOT / "e2e_fix13_stdout.txt"

results = {}
t0 = time.monotonic()

def log(m):
    line = f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

# ============================================================
# APP + INTAKE
# ============================================================
def make_app():
    from r52.db import Database, migrate
    class FC:
        def now(self): return datetime.now(TZ)
    class MN:
        def notify(self, *a, **k): pass
        def __getattr__(self, n): return lambda *a, **k: None
    app = type("App", (), {})()
    app.clock = FC()
    app.db = Database(str(DB))
    app.db.initialize()
    migrate(app.db)
    app.settings = {"intake.auto_accept": True}
    app.notifier = MN()
    return app

def feed_inbox(app, eye_json, source):
    from r52.intake.eye import Inbox
    inbox = Inbox(app)
    data = json.dumps(eye_json, ensure_ascii=False).encode("utf-8")
    fname = f"fix13-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX13_{source}")
    log(f"  Inbox[{source}]: status={item['status']} created={created}")
    if item["status"] == "PENDING":
        acc = inbox.accept(item["id"])
        log(f"  Accept[{source}]: product_id={acc.get('product_id')} obs_new={acc.get('observation_created')}")
        return acc
    return item

def verify_sqlite(source):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM product WHERE source=?", (source,))
        products = [dict(r) for r in cur.fetchall()]
        prod_ids = [p["id"] for p in products]
        obs, pp = [], []
        if prod_ids:
            ph = ",".join("?" * len(prod_ids))
            cur.execute(f"SELECT * FROM observation WHERE product_id IN ({ph})", prod_ids)
            obs = [dict(r) for r in cur.fetchall()]
            obs_ids = [o["id"] for o in obs]
            if obs_ids:
                ph2 = ",".join("?" * len(obs_ids))
                cur.execute(f"SELECT * FROM price_point WHERE observation_id IN ({ph2})", obs_ids)
                pp = [dict(r) for r in cur.fetchall()]
        return {"products": products, "observations": obs, "price_points": pp,
                "product_count": len(products), "observation_count": len(obs), "price_point_count": len(pp)}
    finally:
        conn.close()

def build_eye(source, lid, url, title, price_minor=None, cur="RUB",
              brand=None, model=None, desc=None, cat=None, media=None, hs=200):
    f = {}
    if title:
        f["title"] = {"status": "KNOWN", "value": title, "method": "HTTP",
                      "evidence": {"method": "HTTP", "source": source}}
    if brand:
        f["brand"] = {"status": "KNOWN", "value": brand, "method": "HTTP",
                      "evidence": {"method": "HTTP", "source": source}}
    if model:
        f["model"] = {"status": "KNOWN", "value": model, "method": "HTTP",
                      "evidence": {"method": "HTTP", "source": source}}
    if desc:
        f["description"] = {"status": "KNOWN", "value": desc[:3000], "method": "HTTP",
                            "evidence": {"method": "HTTP", "source": source}}
    if cat:
        f["category"] = {"status": "KNOWN", "value": cat, "method": "HTTP",
                         "evidence": {"method": "HTTP", "source": source}}
    pc = []
    if price_minor is not None:
        pc.append({"role": "regular", "amount_minor": int(price_minor), "currency": cur, "label": "Cena"})
    return {
        "format": "JARVIS_BR02_EYE_USER_CAPTURE",
        "capture_action": "EXPLICIT_POPUP_CLICK",
        "fully_autonomous": False,
        "collector_source": source,
        "observation": {
            "fields": f,
            "price_candidates": pc,
            "media": {"items": media or []},
            "availability": "UNKNOWN",
            "category_path": cat or "",
            "source": source,
            "id": str(lid),
            "url": url,
            "observed_at": now_iso(),
            "status": "PARTIAL_SUCCESS" if (title and price_minor) else "DEGRADED",
            "product_content": bool(title),
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTTP",
            "http_status": hs,
        }
    }

# ============================================================
# WB — Price API PRIMARY, card.json selling SECONDARY
# ============================================================
def run_wb(app):
    log("=== WB: Price API + card.json (FIXED: no supplier_id) ===")
    nm_id = 1097975275
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        vol = nm_id // 100000
        part = nm_id // 1000
        basket_host = None
        card = None

        # 1. Fetch card.json
        for i in range(42, 55):
            h = f"basket-{i}.wbbasket.ru"
            try:
                r = s.get(f"https://{h}/vol{vol}/part{part}/{nm_id}/info/ru/card.json",
                          timeout=8, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                if r.status_code == 200:
                    basket_host = h
                    card = r.json()
                    break
            except:
                continue
        if not card:
            log("  WB: no card.json")
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"), "reason": "no card"}
            return
        log(f"  WB: basket={basket_host}")

        # TITLE, BRAND, DESC, CAT from card
        title = card.get("imt_name") or card.get("name") or ""
        selling = card.get("selling", {})
        brand = ""
        if isinstance(selling, dict):
            brand = selling.get("brand_name") or selling.get("brand") or ""
        desc = card.get("description") or card.get("imt_description") or ""
        cat = card.get("subj_name") or ""
        if card.get("subj_root_name"):
            cat = f"{card['subj_root_name']} > {cat}"
        log(f"  WB: title={title!r} brand={brand!r}")

        # 2. PRICE: Try Price API endpoints FIRST
        price_minor = None
        price_src = ""
        price_endpoints = [
            ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("card_v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("card_v5", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("search_wb", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ]

        for label, api_url in price_endpoints:
            try:
                pr = s.get(api_url, timeout=10, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                if pr.status_code != 200:
                    log(f"  WB price {label}: HTTP {pr.status_code}")
                    continue
                pd = pr.json()
                # Format 1: {"data": {"products": [...]}} or {"products": [...]}
                prods = pd.get("data", {}).get("products") or pd.get("products") or []
                if prods and isinstance(prods, list):
                    p0 = prods[0]
                    sizes = p0.get("sizes", [])
                    if sizes:
                        sz_price = sizes[0].get("price", {})
                        for pk in ("basic", "product", "card", "wallet", "old", "crossed", "basicPercent"):
                            pv = sz_price.get(pk)
                            if isinstance(pv, (int, float)) and pv > 1000:
                                price_minor = int(pv)
                                price_src = f"price_api.{label}.{pk}"
                                break
                        if price_minor:
                            log(f"  WB price {label}: {price_minor} ({price_src})")
                            break
                    # Top-level price in product
                    if not price_minor:
                        for pk in ("price", "priceU"):
                            pv = p0.get(pk)
                            if isinstance(pv, (int, float)) and pv > 1000:
                                price_minor = int(pv)
                                price_src = f"price_api.{label}.{pk}"
                                break
                # Format 2: {"data": {"prices": {...}}}
                if not price_minor:
                    d = pd.get("data", {})
                    if isinstance(d, dict):
                        prices = d.get("prices") or d.get("price")
                        if isinstance(prices, dict):
                            for pk, pv in prices.items():
                                if isinstance(pv, (int, float)) and pv > 1000:
                                    price_minor = int(pv)
                                    price_src = f"price_api.{label}.{pk}"
                                    break
                if price_minor:
                    break
            except Exception as e:
                log(f"  WB price {label} err: {e}")
            time.sleep(0.5)

        # 3. Fallback: card.json selling (NOT supplier_id!)
        if not price_minor and isinstance(selling, dict):
            for pk in ("price", "priceU", "priceD", "oldPrice", "priceOld"):
                pv = selling.get(pk)
                if isinstance(pv, (int, float)) and pv > 1000:
                    price_minor = int(pv)
                    price_src = f"card_selling.{pk}"
                    break
            if not price_minor:
                for pk in ("price", "priceU", "priceD"):
                    pv = card.get(pk)
                    if isinstance(pv, (int, float)) and pv > 1000:
                        price_minor = int(pv)
                        price_src = f"card.{pk}"
                        break
            if not price_minor:
                for size in card.get("sizes", []):
                    if not isinstance(size, dict):
                        continue
                    sz_p = size.get("price", {})
                    if isinstance(sz_p, dict):
                        for pk in ("basic", "product", "card"):
                            pv = sz_p.get(pk)
                            if isinstance(pv, (int, float)) and pv > 1000:
                                price_minor = int(pv)
                                price_src = f"card.sizes.{pk}"
                                break
                    if price_minor:
                        break

        # 4. Last resort: HTML
        if not price_minor:
            try:
                hr = s.get(url, timeout=20, headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
                if hr.status_code == 200:
                    html = hr.text
                    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                        try:
                            ld = json.loads(m.group(1))
                            if isinstance(ld, dict) and "offers" in ld:
                                off = ld["offers"]
                                if isinstance(off, list) and off: off = off[0]
                                p = off.get("price") or off.get("lowPrice")
                                if p:
                                    price_minor = int(float(p) * 100)
                                    price_src = "html.jsonld"
                                    break
                        except: pass
                    if not price_minor:
                        m = re.search(r'data-price="(\d+)"', html)
                        if m and int(m.group(1)) > 1000:
                            price_minor = int(m.group(1))
                            price_src = "html.data-price"
                    if not price_minor:
                        m = re.search(r'(\d[\d\u00a0\s]{3,})\s*\u20bd', html)
                        if m:
                            raw = m.group(1).replace('\u00a0','').replace(' ','')
                            price_minor = int(float(raw) * 100)
                            price_src = "html.regex"
            except Exception as e:
                log(f"  WB HTML err: {e}")

        log(f"  WB: FINAL price={price_minor} src={price_src}")

        if title and price_minor and price_minor > 10000:
            eye = build_eye("WB", str(nm_id), url, title, price_minor, brand=brand, desc=desc, cat=cat)
            acc = feed_inbox(app, eye, "WB")
            sq = verify_sqlite("WB")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["WB"] = {"verdict": verdict, "sqlite": sq, "price_source": price_src}
            log(f"  WB VERDICT: {verdict}")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"),
                             "reason": f"title={bool(title)} price={price_minor} src={price_src}"}
            log(f"  WB VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"), "error": str(e)}

# ============================================================
# FARPOST — FIXED: data-bulletin-price × 100
# ============================================================
def run_farpost(app):
    log("=== FARPOST: HTTP warm-up + card (FIXED ×100) ===")
    lid = "138975631"
    url = f"https://www.farpost.ru/vladivostok/tech/communication/cellphones/apple-iphone-17-256gb-green-sage-zelenyj-esim-bez-rustore-ucenka-ion-{lid}.html"
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        r0 = s.get("https://www.farpost.ru/", timeout=20)
        log(f"  FP warm-up: HTTP {r0.status_code}")
        time.sleep(2)
        r = s.get(url, timeout=25, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        log(f"  FP card: HTTP {r.status_code} len={len(r.text)}")
        html = r.text

        title = None
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1).strip()
        if not title:
            m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
            if m: title = m.group(1).strip()

        price_minor = None
        price_src = ""

        # 1. data-bulletin-price (value in RUBLES → ×100 = kopecks)
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m:
            val = int(m.group(1))
            if val > 0:
                price_minor = val * 100
                price_src = "data-bulletin-price"

        # 2. JSON-LD
        if not price_minor:
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    if isinstance(ld, dict) and "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, list) and off: off = off[0]
                        p = off.get("price") or off.get("lowPrice")
                        if p:
                            price_minor = int(float(p) * 100)
                            price_src = "jsonld"
                            break
                except: pass

        # 3. HTML regex
        if not price_minor:
            m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:\u20bd|\u0440\u0443\u0431)', html)
            if m:
                try:
                    raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                    price_minor = int(float(raw) * 100)
                    price_src = "html_regex"
                except: pass

        log(f"  FP: title={title!r} price={price_minor} src={price_src}")
        if title and price_minor and price_minor > 10000:
            eye = build_eye("FARPOST", lid, url, title, price_minor)
            acc = feed_inbox(app, eye, "FARPOST")
            sq = verify_sqlite("FARPOST")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["FARPOST"] = {"verdict": verdict, "sqlite": sq, "price_source": price_src}
            log(f"  FP VERDICT: {verdict}")
        else:
            results["FARPOST"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("FARPOST"),
                                  "reason": f"title={bool(title)} price={price_minor} src={price_src}"}
            log(f"  FP VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  FP EXCEPTION: {e}\n{traceback.format_exc()}")
        results["FARPOST"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("FARPOST"), "error": str(e)}

# ============================================================
# OZON — Playwright (PROVEN)
# ============================================================
def run_ozon(app):
    log("=== OZON: Playwright ===")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
                timezone_id="Asia/Vladivostok",
            )
            page = ctx.new_page()
            log("  OZON: warm-up...")
            page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(6000)

            pid = "2984707012"
            url = f"https://www.ozon.ru/product/{pid}/"
            log(f"  OZON: goto {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(12000)
            html = page.content()
            log(f"  OZON prod {pid}: len={len(html)}")

            if len(html) < 5000:
                page.wait_for_timeout(8000)
                html = page.content()

            title = None
            m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
            if m: title = m.group(1).strip()
            if not title:
                m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                if m: title = m.group(1).strip()

            price_minor = None
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    if isinstance(ld, dict) and "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, list) and off: off = off[0]
                        p = off.get("price") or off.get("lowPrice")
                        if p: price_minor = int(float(p) * 100); break
                except: pass
            if not price_minor:
                m = re.search(r'"price"\s*:\s*"?(\d{5,})"?', html)
                if m: price_minor = int(m.group(1))
            if not price_minor:
                m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:\u20bd|\u0440\u0443\u0431)', html)
                if m:
                    try:
                        raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                        price_minor = int(float(raw) * 100)
                    except: pass
            if not price_minor:
                m = re.search(r'data-price="(\d+)"', html)
                if m: price_minor = int(m.group(1))

            log(f"  OZON: title={title!r} price={price_minor}")
            if title and price_minor and price_minor > 10000:
                eye = build_eye("OZON", pid, url, title, price_minor)
                acc = feed_inbox(app, eye, "OZON")
                sq = verify_sqlite("OZON")
                verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
                results["OZON"] = {"verdict": verdict, "sqlite": sq}
                log(f"  OZON VERDICT: {verdict}")
            else:
                results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"),
                                   "reason": f"title={bool(title)} price={price_minor}"}
                log(f"  OZON VERDICT: E2E_FAIL")
            browser.close()
    except Exception as e:
        log(f"  OZON EXCEPTION: {e}\n{traceback.format_exc()}")
        results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"), "error": str(e)}

# ============================================================
# AVITO — 3 NEW strategies
# ============================================================
def run_avito(app):
    log("=== AVITO: 3 new strategies ===")
    eye = None
    strat_results = {}

    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")

        # --- S1: Avito API/RSS endpoints ---
        log("  AVITO S1: API/RSS endpoints...")
        api_urls = [
            "https://www.avito.ru/api/search/list?ab=1&ad_count=20&category=smartfony-i-telefony&city=1&f=&from=search&order=DESC&query=iphone",
            "https://www.avito.ru/api/ads?category=smartfony-i-telefony&city=1&page=1",
            "https://www.avito.ru/moskva/smartfony-i-telefony/rss",
            "https://www.avito.ru/rss/smartfony-i-telefony",
            "https://www.avito.ru/vladivostok/smartfony-i-telefony/rss",
        ]
        s1_ids = []
        for au in api_urls:
            try:
                r = s.get(au, timeout=15, headers={"Accept": "*/*", "Accept-Language": "ru-RU,ru;q=0.9"})
                log(f"    {au[:65]}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 500:
                    try:
                        data = r.json()
                        items = data.get("items") or data.get("ads") or data.get("data", {}).get("items") or []
                        for item in items[:10]:
                            aid = str(item.get("id") or item.get("ad_id") or "")
                            if aid and len(aid) >= 8:
                                s1_ids.append(aid)
                    except:
                        ids = re.findall(r'avito\.ru/ad/(\d+)', r.text)
                        s1_ids.extend(ids[:10])
                    if s1_ids:
                        break
            except Exception as e:
                log(f"    err: {e}")
        s1_ids = list(dict.fromkeys(s1_ids))
        log(f"  AVITO S1: {len(s1_ids)} IDs → {s1_ids[:5]}")
        strat_results["S1_api"] = {"ids": s1_ids[:5], "count": len(s1_ids)}

        # --- S2: Direct ad page with curl_cffi ---
        test_ids = s1_ids[:5]
        if not test_ids:
            test_ids = []
        log(f"  AVITO S2: direct curl_cffi on {len(test_ids)} IDs")
        for aid in test_ids:
            ad_url = f"https://www.avito.ru/ad/{aid}"
            try:
                r = s.get(ad_url, timeout=20, headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
                log(f"    ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code != 200 or len(r.text) < 3000:
                    continue
                html = r.text
                if "Доступ ограничен" in html and len(html) < 40000:
                    log("    → IP block")
                    continue
                title = None
                m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
                if m: title = m.group(1).strip()
                if not title:
                    m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                    if m: title = m.group(1).strip()
                price_minor = None
                for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                    try:
                        ld = json.loads(m.group(1))
                        if isinstance(ld, dict) and "offers" in ld:
                            off = ld["offers"]
                            if isinstance(off, list) and off: off = off[0]
                            p = off.get("price") or off.get("lowPrice")
                            if p: price_minor = int(float(p) * 100); break
                    except: pass
                if not price_minor:
                    m = re.search(r'__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
                    if m:
                        try:
                            state = json.loads(m.group(1))
                            for path in [("ad","price"),("listing","price"),("data","price"),("ad","priceValue"),("listing","priceValue")]:
                                obj = state
                                for k in path:
                                    if isinstance(obj, dict) and k in obj:
                                        obj = obj[k]
                                    else:
                                        obj = None
                                        break
                                if isinstance(obj, (int, float)) and obj > 100:
                                    price_minor = int(obj * 100)
                                    break
                        except: pass
                if not price_minor:
                    m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                    if m: price_minor = int(m.group(1))
                if not price_minor:
                    m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:\u20bd|\u0440\u0443\u0431)', html)
                    if m:
                        try:
                            raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                            price_minor = int(float(raw) * 100)
                        except: pass

                log(f"    ad={aid}: title={title!r} price={price_minor}")
                if title and price_minor and price_minor > 500:
                    eye = build_eye("AVITO", aid, ad_url, title, price_minor)
                    strat_results["S2_direct"] = {"ad": aid, "title": title, "price": price_minor}
                    break
            except Exception as e:
                log(f"    ad={aid} err: {e}")

        # --- S3: Playwright mobile viewport ---
        if not eye:
            log("  AVITO S3: Playwright mobile...")
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        headless=True,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
                    )
                    ctx = browser.new_context(
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                        viewport={"width": 390, "height": 844},
                        locale="ru-RU",
                        is_mobile=True,
                        has_touch=True,
                    )
                    page = ctx.new_page()
                    cat_urls = [
                        "https://www.avito.ru/moskva/smartfony-i-telefony",
                        "https://www.avito.ru/smartfony-i-telefony",
                    ]
                    for cu in cat_urls:
                        log(f"    S3: {cu}")
                        try:
                            page.goto(cu, wait_until="domcontentloaded", timeout=35000)
                            page.wait_for_timeout(8000)
                            html = page.content()
                            log(f"      len={len(html)}")
                            if "Доступ ограничен" in html and len(html) < 40000:
                                continue
                            ids = re.findall(r'avito\.ru/(?:[^/]+/)?ad/(\d{8,})', html)
                            if not ids:
                                ids = re.findall(r'"id"\s*:\s*(\d{9,12})', html)
                            ids = list(dict.fromkeys(i for i in ids if 8 <= len(i) <= 12))
                            log(f"      → {len(ids)} IDs: {ids[:5]}")
                            if not ids:
                                continue
                            for aid in ids[:3]:
                                ad_url = f"https://www.avito.ru/ad/{aid}"
                                page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_timeout(8000)
                                html2 = page.content()
                                if "Доступ ограничен" in html2 and len(html2) < 40000:
                                    continue
                                title = None
                                m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html2)
                                if m: title = m.group(1).strip()
                                if not title:
                                    m = re.search(r'property="og:title"\s+content="([^"]+)"', html2)
                                    if m: title = m.group(1).strip()
                                price_minor = None
                                for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html2, re.DOTALL):
                                    try:
                                        ld = json.loads(m.group(1))
                                        if isinstance(ld, dict) and "offers" in ld:
                                            off = ld["offers"]
                                            if isinstance(off, list) and off: off = off[0]
                                            p = off.get("price") or off.get("lowPrice")
                                            if p: price_minor = int(float(p) * 100); break
                                    except: pass
                                if not price_minor:
                                    m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:\u20bd|\u0440\u0443\u0431)', html2)
                                    if m:
                                        try:
                                            raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                                            price_minor = int(float(raw) * 100)
                                        except: pass
                                log(f"      ad={aid}: title={title!r} price={price_minor}")
                                if title and price_minor and price_minor > 500:
                                    eye = build_eye("AVITO", aid, ad_url, title, price_minor)
                                    strat_results["S3_mobile"] = {"ad": aid, "title": title, "price": price_minor}
                                    break
                            if eye:
                                break
                        except Exception as e:
                            log(f"      err: {e}")
                    browser.close()
            except Exception as e:
                log(f"  AVITO S3 exception: {e}")

    except Exception as e:
        log(f"  AVITO EXCEPTION: {e}\n{traceback.format_exc()}")

    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq, "strategies": strat_results}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"),
                           "strategies": strat_results, "reason": "no ad data from any strategy"}
        log(f"  AVITO VERDICT: E2E_FAIL")

# ============================================================
# MAIN
# ============================================================
def main():
    log(f"e2e_fix13 start, TZ=UTC+10")
    app = make_app()

    run_wb(app)
    run_farpost(app)
    run_ozon(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"e2e_fix13 done in {elapsed:.1f}s")

    out = {
        "ts": now_iso(),
        "s": round(elapsed, 1),
        "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
        "d": results,
    }
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")

if __name__ == "__main__":
    main()
