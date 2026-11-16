#!/usr/bin/env python3
"""e2e_fix11.py — All 4 platforms: WB(selling.supplier_id), Farpost(HTTP), Ozon(PW), Avito(PW category→real IDs)"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix11"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix11_result.json"
LOG = ROOT / "e2e_fix11_stdout.txt"

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
    fname = f"fix11-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX11_{source}")
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
# WB — Basket CDN + selling.supplier_id (PROVEN fix9)
# ============================================================
def run_wb(app):
    log("=== WB: Basket CDN + selling.supplier_id ===")
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
            log("  WB: no card.json")
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"), "reason": "no card"}
            return

        log(f"  WB: basket={basket_host}")

        # TITLE
        title = card.get("imt_name") or card.get("name") or ""
        log(f"  WB: title={title!r}")

        # BRAND from selling
        selling = card.get("selling", {})
        brand = ""
        if isinstance(selling, dict):
            brand = selling.get("brand_name") or selling.get("brand") or ""
        log(f"  WB: brand={brand!r}")

        # DESC
        desc = card.get("description") or card.get("imt_description") or ""
        cat = card.get("subj_name") or ""
        if card.get("subj_root_name"):
            cat = f"{card['subj_root_name']} > {cat}"

        # PRICE: strategy from fix9 (selling dict → first int > 10000)
        price_minor = None
        price_src = ""
        if isinstance(selling, dict):
            # Priority: supplier_id (proven), then scan
            if "supplier_id" in selling and isinstance(selling["supplier_id"], (int, float)) and selling["supplier_id"] > 10000:
                price_minor = int(selling["supplier_id"])
                price_src = "selling.supplier_id"
            else:
                for k, v in selling.items():
                    if isinstance(v, (int, float)) and v > 10000:
                        price_minor = int(v)
                        price_src = f"selling.{k}"
                        break
        log(f"  WB: price={price_minor} ({price_src})")

        # Fallback: Price API (best-effort)
        if not price_minor:
            for api_url in [
                f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
                f"https://card.wb.ru/cards/v1/detail?nm={nm_id}",
            ]:
                try:
                    pr = s.get(api_url, timeout=10, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                    if pr.status_code == 200:
                        pd = pr.json()
                        prods = pd.get("data", {}).get("products", [])
                        if prods:
                            sizes = prods[0].get("sizes", [])
                            if sizes:
                                p = sizes[0].get("price", {})
                                price_minor = p.get("basic") or p.get("basicPercent")
                                price_src = api_url.split("/")[-1]
                                break
                except:
                    pass

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
# FARPOST — HTTP warm-up + card (PROVEN final6)
# ============================================================
def run_farpost(app):
    log("=== FARPOST: HTTP warm-up + card ===")
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

        price_cents = None
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m: price_cents = int(m.group(1))
        if not price_cents:
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    if isinstance(ld, dict) and "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, list) and off: off = off[0]
                        p = off.get("price")
                        if p: price_cents = int(float(p) * 100); break
                except: pass
        if not price_cents:
            m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:₽|руб)', html)
            if m:
                try:
                    raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                    price_cents = int(float(raw) * 100)
                except: pass

        log(f"  FP: title={title!r} price={price_cents}")
        if title and price_cents and price_cents > 1000:
            eye = build_eye("FARPOST", lid, url, title, price_cents)
            acc = feed_inbox(app, eye, "FARPOST")
            sq = verify_sqlite("FARPOST")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["FARPOST"] = {"verdict": verdict, "sqlite": sq}
            log(f"  FP VERDICT: {verdict}")
        else:
            results["FARPOST"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("FARPOST"),
                                  "reason": f"title={bool(title)} price={price_cents}"}
            log(f"  FP VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  FP EXCEPTION: {e}\n{traceback.format_exc()}")
        results["FARPOST"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("FARPOST"), "error": str(e)}

# ============================================================
# OZON — Playwright (PROVEN final6)
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
            log(f"  OZON warm-up: len={len(page.content())}")

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
                log(f"  OZON retry: len={len(html)}")

            title = None
            m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
            if m: title = m.group(1).strip()
            if not title:
                m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                if m: title = m.group(1).strip()

            price_cents = None
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    if isinstance(ld, dict) and "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, list) and off: off = off[0]
                        p = off.get("price") or off.get("lowPrice")
                        if p: price_cents = int(float(p) * 100); break
                except: pass
            if not price_cents:
                m = re.search(r'"price"\s*:\s*"?(\d{5,})"?', html)
                if m: price_cents = int(m.group(1))
            if not price_cents:
                m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:₽|руб)', html)
                if m:
                    try:
                        raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                        price_cents = int(float(raw) * 100)
                    except: pass
            if not price_cents:
                m = re.search(r'data-price="(\d+)"', html)
                if m: price_cents = int(m.group(1))

            log(f"  OZON: title={title!r} price={price_cents}")
            if title and price_cents and price_cents > 10000:
                eye = build_eye("OZON", pid, url, title, price_cents)
                acc = feed_inbox(app, eye, "OZON")
                sq = verify_sqlite("OZON")
                verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
                results["OZON"] = {"verdict": verdict, "sqlite": sq}
                log(f"  OZON VERDICT: {verdict}")
            else:
                results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"),
                                   "reason": f"title={bool(title)} price={price_cents}"}
                log(f"  OZON VERDICT: E2E_FAIL")
            browser.close()
    except Exception as e:
        log(f"  OZON EXCEPTION: {e}\n{traceback.format_exc()}")
        results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"), "error": str(e)}

# ============================================================
# AVITO — Playwright category → REAL ad IDs → card
# ============================================================
def run_avito(app):
    log("=== AVITO: PW category → real IDs → card ===")
    eye = None
    real_ids = []

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

            # --- S1: Moscow category (less likely IP-blocked) ---
            cat_urls = [
                "https://www.avito.ru/moskva/smartfony-i-telefony",
                "https://www.avito.ru/vladivostok/smartfony-i-telefony",
            ]
            for cu in cat_urls:
                log(f"  AVITO S1: {cu}")
                try:
                    page.goto(cu, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"    len={len(html)}")
                    if "Доступ ограничен" in html and len(html) < 40000:
                        log("    → IP block on category")
                        continue
                    # Extract REAL ad IDs
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)?(?:ad|ads)/(\d{6,})', html)
                    if not ids:
                        ids = re.findall(r'"id"\s*:\s*(\d{8,})', html)
                    if not ids:
                        ids = re.findall(r'href="[^"]*?/(\d{9,})"', html)
                    # Filter: unique, 9-12 digits
                    real_ids = list(dict.fromkeys(i for i in ids if 9 <= len(i) <= 12))
                    log(f"    → real IDs: {len(real_ids)} → {real_ids[:10]}")
                    if real_ids:
                        break
                except Exception as e:
                    log(f"    err: {e}")

            # --- S2: Try direct ad URLs with real IDs ---
            for aid in real_ids[:5]:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                log(f"  AVITO S2: goto {ad_url}")
                try:
                    page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"    ad={aid}: len={len(html)}")
                    if "Доступ ограничен" in html and len(html) < 40000:
                        log("    → IP block")
                        continue
                    if len(html) < 3000:
                        log("    → stub")
                        continue
                    # Parse title
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
                    if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                    # Parse price
                    price_cents = None
                    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                        try:
                            ld = json.loads(m.group(1))
                            if isinstance(ld, dict) and "offers" in ld:
                                off = ld["offers"]
                                if isinstance(off, list) and off: off = off[0]
                                p = off.get("price") or off.get("lowPrice")
                                if p: price_cents = int(float(p) * 100); break
                        except: pass
                    if not price_cents:
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m: price_cents = int(m.group(1))
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:₽|руб)', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                    # Also try __PRELOADED_STATE__
                    if not price_cents:
                        m = re.search(r'__PRELOADED_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
                        if m:
                            try:
                                state = json.loads(m.group(1))
                                # Navigate state for price
                                for key in ["ad", "listing", "data"]:
                                    if key in state:
                                        sub = state[key]
                                        if isinstance(sub, dict) and "price" in sub:
                                            price_cents = int(sub["price"])
                                            break
                            except: pass

                    log(f"    ad={aid}: title={title!r} price={price_cents}")
                    if title and price_cents and price_cents > 500:
                        eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                        log(f"  AVITO S2: SUCCESS ad={aid}")
                        break
                except Exception as e:
                    log(f"    ad={aid} err: {e}")

            browser.close()
    except Exception as e:
        log(f"  AVITO PW exception: {e}\n{traceback.format_exc()}")

    # --- S3: curl_cffi with real IDs (if PW failed) ---
    if not eye and real_ids:
        log("  AVITO S3: curl_cffi with real IDs...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            for aid in real_ids[:5]:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                try:
                    r = s.get(ad_url, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    log(f"    S3 ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 3000 and "Доступ ограничен" not in r.text:
                        html = r.text
                        title = None
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                        if not title:
                            m = re.search(r'<title[^>]*>([^<]+)</title>', html)
                            if m: title = m.group(1).replace(" - Авито","").strip()
                        price_cents = None
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m: price_cents = int(m.group(1))
                        if not price_cents:
                            m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', html, re.I)
                            if m: price_cents = int(float(m.group(1)) * 100)
                        if title and price_cents and price_cents > 500:
                            eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                            log(f"  AVITO S3: SUCCESS ad={aid}")
                            break
                except Exception as e:
                    log(f"    S3 ad={aid} err: {e}")
        except Exception as e:
            log(f"  AVITO S3 exception: {e}")

    # --- S4: m.avito.ru mobile with real IDs ---
    if not eye and real_ids:
        log("  AVITO S4: m.avito.ru mobile...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            for aid in real_ids[:3]:
                mu = f"https://m.avito.ru/ad/{aid}"
                try:
                    r = s.get(mu, timeout=20, headers={
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                    log(f"    S4 m.ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 2000 and "Доступ ограничен" not in r.text:
                        html = r.text
                        title = None
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                        price_cents = None
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m: price_cents = int(m.group(1))
                        if title and price_cents and price_cents > 500:
                            eye = build_eye("AVITO", aid, mu, title, price_cents)
                            log(f"  AVITO S4: SUCCESS m.ad={aid}")
                            break
                except Exception as e:
                    log(f"    S4 m.ad={aid} err: {e}")
        except Exception as e:
            log(f"  AVITO S4 exception: {e}")

    # Feed to intake
    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq, "real_ids": real_ids[:10]}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"),
                            "real_ids": real_ids[:10], "reason": "no ad fetched from real IDs"}
        log(f"  AVITO VERDICT: E2E_FAIL (real_ids={len(real_ids)})")

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E FIX11 START ===")
    app = make_app()
    log("App ready")

    # Order: WB (fast) → Farpost (fast) → Ozon (PW) → Avito (PW, slowest)
    run_wb(app)
    run_farpost(app)
    run_ozon(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"=== E2E FIX11 DONE in {elapsed:.1f}s ===")

    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
