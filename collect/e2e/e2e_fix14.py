#!/usr/bin/env python3
"""e2e_fix14.py — Diagnostic + fix round.
WB: dump selling keys, try wbx API, fallback to HTML
Ozon: retry with longer timeout
Avito: save HTML, debug ID extraction
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix14"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix14_result.json"

results = {}
t0 = time.monotonic()

def log(m):
    line = f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

# ============================================================
# APP + INTAKE (same as fix13)
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
    fname = f"fix14-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX14_{source}")
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
# WB — DIAGNOSTIC: dump selling, try all price sources
# ============================================================
def run_wb(app):
    log("=== WB: Diagnostic + price hunt ===")
    nm_id = 1097975275
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        vol = nm_id // 100000
        part = nm_id // 1000
        basket_host = None
        card = None

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
            results["WB"] = {"verdict": "E2E_FAIL", "reason": "no card"}
            return
        log(f"  WB: basket={basket_host}")

        title = card.get("imt_name") or card.get("name") or ""
        selling = card.get("selling", {})
        brand = ""
        if isinstance(selling, dict):
            brand = selling.get("brand_name") or selling.get("brand") or ""
        desc = card.get("description") or card.get("imt_description") or ""
        cat = card.get("subj_name") or ""
        if card.get("subj_root_name"):
            cat = f"{card['subj_root_name']} > {cat}"

        # DUMP selling keys
        if isinstance(selling, dict):
            log(f"  WB selling keys: {list(selling.keys())}")
            for k, v in selling.items():
                if isinstance(v, (int, float)) and v > 100:
                    log(f"    selling.{k} = {v}")
        else:
            log(f"  WB selling type: {type(selling)}")

        # DUMP top-level numeric fields
        for k, v in card.items():
            if isinstance(v, (int, float)) and v > 1000 and k not in ("id", "nm_id", "timestamp"):
                log(f"  WB card.{k} = {v}")

        # DUMP sizes
        sizes = card.get("sizes", [])
        if sizes:
            log(f"  WB sizes[0] keys: {list(sizes[0].keys()) if isinstance(sizes[0], dict) else 'n/a'}")
            if isinstance(sizes[0], dict):
                sz_p = sizes[0].get("price", {})
                if isinstance(sz_p, dict):
                    log(f"  WB sizes[0].price: {sz_p}")

        # Try price APIs with different headers
        price_minor = None
        price_src = ""
        price_headers = {
            "Accept": "application/json",
            "Referer": "https://www.wildberries.ru/",
            "Origin": "https://www.wildberries.ru",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        price_endpoints = [
            ("wbx_v1", f"https://wbx-content.v2.wbcontent.ru/cards/v1/detail?nm={nm_id}"),
            ("card_wb_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("catalog_wb", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("search_wb_v1", f"https://search.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ("wbx_content", f"https://wbx-content.v2.wbcontent.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ]
        for label, api_url in price_endpoints:
            try:
                pr = s.get(api_url, timeout=10, headers=price_headers)
                log(f"  WB {label}: HTTP {pr.status_code} len={len(pr.text)}")
                if pr.status_code == 200:
                    pd = pr.json()
                    prods = pd.get("data", {}).get("products") or pd.get("products") or []
                    if prods:
                        p0 = prods[0]
                        sizes_p = p0.get("sizes", [])
                        if sizes_p:
                            sp = sizes_p[0].get("price", {})
                            for pk in ("basic", "product", "card", "wallet", "basicPercent"):
                                pv = sp.get(pk)
                                if isinstance(pv, (int, float)) and pv > 1000:
                                    price_minor = int(pv)
                                    price_src = f"{label}.sizes.price.{pk}"
                                    break
                        if not price_minor:
                            for pk in ("price", "priceU"):
                                pv = p0.get(pk)
                                if isinstance(pv, (int, float)) and pv > 1000:
                                    price_minor = int(pv)
                                    price_src = f"{label}.{pk}"
                    if price_minor:
                        log(f"  WB {label}: price={price_minor}")
                        break
            except Exception as e:
                log(f"  WB {label} err: {e}")
            time.sleep(0.3)

        # Fallback: selling dict (excluding supplier_id)
        if not price_minor and isinstance(selling, dict):
            skip_keys = {"supplier_id", "id", "nm_id", "timestamp"}
            for k, v in selling.items():
                if k in skip_keys:
                    continue
                if isinstance(v, (int, float)) and 5000 < v < 5000000:
                    price_minor = int(v)
                    price_src = f"selling.{k}"
                    break
            log(f"  WB selling fallback: price={price_minor} src={price_src}")

        # Fallback: sizes in card
        if not price_minor:
            for size in card.get("sizes", []):
                if not isinstance(size, dict):
                    continue
                sz_p = size.get("price", {})
                if isinstance(sz_p, dict):
                    for pk in ("basic", "product", "card", "wallet"):
                        pv = sz_p.get(pk)
                        if isinstance(pv, (int, float)) and 5000 < pv < 5000000:
                            price_minor = int(pv)
                            price_src = f"card.sizes.{pk}"
                            break
                if price_minor:
                    break

        # Fallback: HTML
        if not price_minor:
            try:
                hr = s.get(url, timeout=25, headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
                log(f"  WB HTML: HTTP {hr.status_code} len={len(hr.text)}")
                if hr.status_code == 200:
                    html = hr.text
                    (WORK / "wb_html.html").write_text(html[:200000], encoding="utf-8")
                    # JSON-LD
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
                    # data-price
                    if not price_minor:
                        m = re.search(r'data-price="(\d+)"', html)
                        if m and int(m.group(1)) > 5000:
                            price_minor = int(m.group(1))
                            price_src = "html.data-price"
                    # regex ₽
                    if not price_minor:
                        m = re.search(r'(\d[\d\u00a0\s]{3,})\s*\u20bd', html)
                        if m:
                            raw = m.group(1).replace('\u00a0','').replace(' ','')
                            price_minor = int(float(raw) * 100)
                            price_src = "html.rub_regex"
                    # __INITIAL_STATE__ or similar
                    if not price_minor:
                        m = re.search(r'"price"\s*:\s*(\d{5,})', html)
                        if m:
                            price_minor = int(m.group(1))
                            price_src = "html.price_field"
            except Exception as e:
                log(f"  WB HTML err: {e}")

        log(f"  WB: FINAL price={price_minor} src={price_src}")

        if title and price_minor and price_minor > 10000:
            eye = build_eye("WB", str(nm_id), url, title, price_minor, brand=brand, desc=desc, cat=cat)
            acc = feed_inbox(app, eye, "WB")
            sq = verify_sqlite("WB")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["WB"] = {"verdict": verdict, "sqlite": sq, "price_source": price_src,
                            "price": price_minor}
            log(f"  WB VERDICT: {verdict}")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"),
                             "reason": f"title={bool(title)} price={price_minor} src={price_src}"}
            log(f"  WB VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "error": str(e)}

# ============================================================
# OZON — Retry with longer timeout
# ============================================================
def run_ozon(app):
    log("=== OZON: Playwright (retry, 60s timeout) ===")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
                timezone_id="Asia/Vladivostok",
            )
            page = ctx.new_page()
            page.set_default_timeout(60000)

            # Warm-up with retry
            warmup_ok = False
            for attempt in range(3):
                try:
                    log(f"  OZON warm-up attempt {attempt+1}...")
                    page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)
                    log(f"    len={len(page.content())}")
                    warmup_ok = True
                    break
                except Exception as e:
                    log(f"    err: {e}")
                    page.wait_for_timeout(3000)
            if not warmup_ok:
                log("  OZON: warm-up failed after 3 attempts")
                results["OZON"] = {"verdict": "E2E_FAIL", "reason": "warmup timeout"}
                browser.close()
                return

            pid = "2984707012"
            url = f"https://www.ozon.ru/product/{pid}/"
            log(f"  OZON: goto {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(12000)
            html = page.content()
            log(f"  OZON prod: len={len(html)}")

            if len(html) < 5000:
                page.wait_for_timeout(10000)
                html = page.content()
                log(f"  OZON retry: len={len(html)}")

            (WORK / "ozon_html.html").write_text(html[:200000], encoding="utf-8")

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

            log(f"  OZON: title={title!r} price={price_minor}")
            if title and price_minor and price_minor > 10000:
                eye = build_eye("OZON", pid, url, title, price_minor)
                acc = feed_inbox(app, eye, "OZON")
                sq = verify_sqlite("OZON")
                verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
                results["OZON"] = {"verdict": verdict, "sqlite": sq, "price": price_minor}
                log(f"  OZON VERDICT: {verdict}")
            else:
                results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"),
                                   "reason": f"title={bool(title)} price={price_minor}"}
                log(f"  OZON VERDICT: E2E_FAIL")
            browser.close()
    except Exception as e:
        log(f"  OZON EXCEPTION: {e}\n{traceback.format_exc()}")
        results["OZON"] = {"verdict": "E2E_FAIL", "error": str(e)}

# ============================================================
# AVITO — Save HTML, debug extraction
# ============================================================
def run_avito(app):
    log("=== AVITO: Diagnostic + extraction ===")
    eye = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
                timezone_id="Asia/Vladivostok",
            )
            page = ctx.new_page()
            page.set_default_timeout(60000)

            # Try desktop first with longer wait
            cat_urls = [
                "https://www.avito.ru/moskva/smartfony-i-telefony",
                "https://www.avito.ru/spb/smartfony-i-telefony",
            ]
            real_ids = []
            cat_html = ""
            for cu in cat_urls:
                log(f"  AVITO cat: {cu}")
                try:
                    page.goto(cu, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(12000)
                    html = page.content()
                    log(f"    len={len(html)}")
                    (WORK / f"avito_cat_{cu.split('/')[3]}.html").write_text(html[:300000], encoding="utf-8")

                    if "Доступ ограничен" in html and len(html) < 50000:
                        log("    → IP block")
                        continue
                    cat_html = html

                    # Multiple ID extraction patterns
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)?ad/(\d{6,})', html)
                    if not ids:
                        ids = re.findall(r'avito\.ru/(?:[^/]+/)?ads/(\d{6,})', html)
                    if not ids:
                        ids = re.findall(r'"adId"\s*:\s*(\d{6,})', html)
                    if not ids:
                        ids = re.findall(r'"id"\s*:\s*(\d{9,12})', html)
                    if not ids:
                        ids = re.findall(r'href="[^"]*?/(\d{9,12})"', html)
                    if not ids:
                        # Try data attributes
                        ids = re.findall(r'data-ad-id="(\d+)"', html)
                    if not ids:
                        # Try __PRELOADED_STATE__
                        m = re.search(r'__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
                        if m:
                            try:
                                state = json.loads(m.group(1))
                                # Search for ad IDs in state
                                def find_ids(obj, depth=0):
                                    found = []
                                    if depth > 6:
                                        return found
                                    if isinstance(obj, dict):
                                        for k, v in obj.items():
                                            if k in ("id", "adId", "ad_id") and isinstance(v, int) and 100000000 < v < 999999999999:
                                                found.append(str(v))
                                            else:
                                                found.extend(find_ids(v, depth+1))
                                    elif isinstance(obj, list):
                                        for item in obj[:50]:
                                            found.extend(find_ids(item, depth+1))
                                    return found
                                ids = find_ids(state)
                            except:
                                pass

                    real_ids = list(dict.fromkeys(i for i in ids if 8 <= len(i) <= 12))
                    log(f"    → {len(real_ids)} IDs: {real_ids[:10]}")
                    if real_ids:
                        break
                except Exception as e:
                    log(f"    err: {e}")

            # Try direct ad pages
            for aid in real_ids[:5]:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                log(f"  AVITO ad: {ad_url}")
                try:
                    page.goto(ad_url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"    ad={aid}: len={len(html)}")
                    (WORK / f"avito_ad_{aid}.html").write_text(html[:300000], encoding="utf-8")

                    if "Доступ ограничен" in html and len(html) < 50000:
                        log("    → IP block")
                        continue
                    if len(html) < 3000:
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
                        log(f"  AVITO SUCCESS: ad={aid}")
                        break
                except Exception as e:
                    log(f"    ad={aid} err: {e}")

            browser.close()
    except Exception as e:
        log(f"  AVITO EXCEPTION: {e}\n{traceback.format_exc()}")

    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"),
                           "reason": "no ad data"}
        log(f"  AVITO VERDICT: E2E_FAIL")

# ============================================================
# FARPOST — already proven, quick re-run
# ============================================================
def run_farpost(app):
    log("=== FARPOST: re-run (proven) ===")
    lid = "138975631"
    url = f"https://www.farpost.ru/vladivostok/tech/communication/cellphones/apple-iphone-17-256gb-green-sage-zelenyj-esim-bez-rustore-ucenka-ion-{lid}.html"
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        s.get("https://www.farpost.ru/", timeout=20)
        time.sleep(2)
        r = s.get(url, timeout=25, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        html = r.text
        title = None
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1).strip()
        price_minor = None
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m:
            price_minor = int(m.group(1)) * 100
        log(f"  FP: title={title!r} price={price_minor}")
        if title and price_minor and price_minor > 10000:
            eye = build_eye("FARPOST", lid, url, title, price_minor)
            acc = feed_inbox(app, eye, "FARPOST")
            sq = verify_sqlite("FARPOST")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["FARPOST"] = {"verdict": verdict, "sqlite": sq, "price": price_minor}
            log(f"  FP VERDICT: {verdict}")
        else:
            results["FARPOST"] = {"verdict": "E2E_FAIL", "reason": f"title={bool(title)} price={price_minor}"}
    except Exception as e:
        log(f"  FP EXCEPTION: {e}")
        results["FARPOST"] = {"verdict": "E2E_FAIL", "error": str(e)}

# ============================================================
# MAIN
# ============================================================
def main():
    log(f"e2e_fix14 start")
    app = make_app()
    run_wb(app)
    run_farpost(app)
    run_ozon(app)
    run_avito(app)
    elapsed = time.monotonic() - t0
    log(f"e2e_fix14 done in {elapsed:.1f}s")
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
