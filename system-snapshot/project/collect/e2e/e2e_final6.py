#!/usr/bin/env python3
"""e2e_final6.py — Final E2E for all 4 platforms via full intake chain.
Ozon: Playwright (proven) → build_eye → stage_bytes → accept
Avito: 3 strategies (Playwright, RSS, curl_cffi mobile)
WB: Basket CDN (proven)
Farpost: HTTP warm-up + card (proven)
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-final6"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_final6_result.json"
LOG = ROOT / "e2e_final6_stdout.txt"

results = {}
t0 = time.monotonic()

def log(m):
    line = f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

# ============================================================
# APP + INTAKE (proven from fast5)
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
    fname = f"final6-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FINAL6_{source}")
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
# OZON — Playwright (proven in V3) → full intake
# ============================================================
def run_ozon(app):
    log("=== OZON: Playwright + full intake ===")
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
            # Warm-up
            log("  OZON: warm-up ozon.ru...")
            page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(6000)
            log(f"  OZON warm-up: len={len(page.content())}")

            # Proven product ID
            pid = "2984707012"
            url = f"https://www.ozon.ru/product/{pid}/"
            log(f"  OZON: goto {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(12000)
            html = page.content()
            log(f"  OZON prod {pid}: len={len(html)}")

            if len(html) < 5000:
                log("  OZON: STUB page, retry with longer wait...")
                page.wait_for_timeout(8000)
                html = page.content()
                log(f"  OZON prod {pid} retry: len={len(html)}")

            # Parse title
            title = None
            m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
            if m:
                title = m.group(1).strip()
            if not title:
                m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                if m:
                    title = m.group(1).strip()
            if not title:
                m = re.search(r'<meta[^>]*name="title"\s+content="([^"]+)"', html, re.I)
                if m:
                    title = m.group(1).strip()

            # Parse price
            price_cents = None
            # JSON-LD
            for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    if isinstance(ld, dict) and "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, list) and off:
                            off = off[0]
                        p = off.get("price") or off.get("lowPrice")
                        if p:
                            price_cents = int(float(p) * 100)
                            break
                except:
                    pass
            # DOM patterns
            if not price_cents:
                m = re.search(r'"price"\s*:\s*"?(\d{5,})"?', html)
                if m:
                    price_cents = int(m.group(1))
            if not price_cents:
                m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:₽|руб)', html)
                if m:
                    try:
                        raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                        price_cents = int(float(raw) * 100)
                    except:
                        pass
            # data-widget price
            if not price_cents:
                m = re.search(r'data-price="(\d+)"', html)
                if m:
                    price_cents = int(m.group(1))

            log(f"  OZON: title={title!r} price_cents={price_cents}")

            if title and price_cents and price_cents > 10000:
                eye = build_eye("OZON", pid, url, title, price_cents)
                acc = feed_inbox(app, eye, "OZON")
                sq = verify_sqlite("OZON")
                verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
                results["OZON"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None,
                                   "strategy": "playwright_desktop"}
                log(f"  OZON VERDICT: {verdict}")
            else:
                results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"), "inbox": None,
                                   "strategy": "playwright_desktop", "reason": f"title={bool(title)} price={price_cents}"}
                log(f"  OZON VERDICT: E2E_FAIL (title={bool(title)}, price={price_cents})")
            browser.close()
    except Exception as e:
        log(f"  OZON EXCEPTION: {e}\n{traceback.format_exc()}")
        results["OZON"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("OZON"), "inbox": None,
                           "strategy": "playwright_desktop", "error": str(e)}
    return results.get("OZON", {})

# ============================================================
# AVITO — 3 strategies
# ============================================================
def run_avito(app):
    log("=== AVITO: multi-strategy ===")
    # Known ad IDs to try
    ad_ids = ["7494308705", "1154321987", "1204567890"]
    eye = None

    # --- Strategy A: Playwright with warm-up ---
    log("  AVITO A: Playwright...")
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
            # Warm-up: main page
            page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(8000)
            home_html = page.content()
            log(f"  AVITO A home: len={len(home_html)}")
            if "Доступ ограничен" in home_html:
                log("  AVITO A: IP block on home, trying category...")
                page.goto("https://www.avito.ru/vladivostok", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)

            for aid in ad_ids:
                url = f"https://www.avito.ru/ad/{aid}"
                log(f"  AVITO A: goto {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"  AVITO A ad={aid}: len={len(html)}")
                    if "Доступ ограничен" in html or len(html) < 3000:
                        log(f"  AVITO A ad={aid}: blocked/stub")
                        continue
                    # Parse
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
                    if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                    # Price
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
                    log(f"  AVITO A ad={aid}: title={title!r} price={price_cents}")
                    if title and price_cents and price_cents > 500:
                        eye = build_eye("AVITO", aid, url, title, price_cents)
                        log(f"  AVITO A: SUCCESS ad={aid}")
                        break
                except Exception as e:
                    log(f"  AVITO A ad={aid} err: {e}")
            browser.close()
    except Exception as e:
        log(f"  AVITO A exception: {e}")

    # --- Strategy B: RSS / category pages via curl_cffi ---
    if not eye:
        log("  AVITO B: curl_cffi category/RSS...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            # Try category page first to find ad links
            cat_urls = [
                "https://www.avito.ru/vladivostok/smartfony-i-telefony",
                "https://www.avito.ru/vladivostok/tech/communication/cellphones",
            ]
            for cu in cat_urls:
                try:
                    r = s.get(cu, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    log(f"  AVITO B {cu.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 5000 and "Доступ ограничен" not in r.text:
                        # Find ad links
                        ad_links = re.findall(r'avito\.ru/(?:[^/]+/)?(?:ad|ads)/(\d{6,})', r.text)
                        if not ad_links:
                            ad_links = re.findall(r'href="[^"]*?/(\d{8,})"', r.text)
                        log(f"  AVITO B: found {len(ad_links)} ad links")
                        if ad_links:
                            aid = ad_links[0]
                            ad_url = f"https://www.avito.ru/ad/{aid}"
                            r2 = s.get(ad_url, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                            log(f"  AVITO B ad={aid}: HTTP {r2.status_code} len={len(r2.text)}")
                            if r2.status_code == 200 and "Доступ ограничен" not in r2.text:
                                html = r2.text
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
                                if title and price_cents:
                                    eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                                    log(f"  AVITO B: SUCCESS title={title!r} price={price_cents}")
                                    break
                except Exception as e:
                    log(f"  AVITO B cat err: {e}")
        except Exception as e:
            log(f"  AVITO B exception: {e}")

    # --- Strategy C: m.avito.ru mobile ---
    if not eye:
        log("  AVITO C: m.avito.ru mobile...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            for aid in ad_ids:
                mu = f"https://m.avito.ru/ad/{aid}"
                try:
                    r = s.get(mu, timeout=20, headers={
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                    log(f"  AVITO C m.ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 2000 and "Доступ ограничен" not in r.text:
                        html = r.text
                        title = None
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                        price_cents = None
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m: price_cents = int(m.group(1))
                        if title and price_cents:
                            eye = build_eye("AVITO", aid, mu, title, price_cents)
                            log(f"  AVITO C: SUCCESS m.ad={aid}")
                            break
                except Exception as e:
                    log(f"  AVITO C m.ad={aid} err: {e}")
        except Exception as e:
            log(f"  AVITO C exception: {e}")

    # Feed to intake
    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"), "inbox": None,
                            "reason": "all 3 strategies failed"}
        log("  AVITO VERDICT: E2E_FAIL (all strategies)")
    return results.get("AVITO", {})

# ============================================================
# WB — Basket CDN (proven)
# ============================================================
def run_wb(app):
    log("=== WB: Basket CDN ===")
    nm_id = 1097975275
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        vol = nm_id // 100000
        part = nm_id // 1000
        # Find basket host
        basket_host = None
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
        if not basket_host:
            log("  WB: basket host not found")
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"), "reason": "no basket host"}
            return

        log(f"  WB: basket={basket_host} card keys={len(card) if isinstance(card, dict) else 0}")
        title = card.get("name", "")
        brand = card.get("brand", "")
        desc = card.get("description", "")[:2000]
        # Price from card or price API
        price_cents = None
        if "sizes" in card and card["sizes"]:
            sz = card["sizes"][0]
            price_cents = sz.get("price", {}).get("basic", 0)
        if not price_cents:
            try:
                pr = s.get(f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
                           timeout=10, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                if pr.status_code == 200:
                    pd = pr.json()
                    if pd.get("data", {}).get("products"):
                        prod = pd["data"]["products"][0]
                        price_cents = prod.get("sizes", [{}])[0].get("price", {}).get("basic", 0)
            except:
                pass
        log(f"  WB: title={title!r} brand={brand!r} price={price_cents}")
        if title and price_cents:
            eye = build_eye("WB", str(nm_id), url, title, price_cents, brand=brand, desc=desc)
            acc = feed_inbox(app, eye, "WB")
            sq = verify_sqlite("WB")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["WB"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None,
                             "strategy": "basket_cdn"}
            log(f"  WB VERDICT: {verdict}")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"), "reason": f"title={bool(title)} price={price_cents}"}
            log(f"  WB VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"), "error": str(e)}
    return results.get("WB", {})

# ============================================================
# FARPOST — HTTP warm-up + card (proven)
# ============================================================
def run_farpost(app):
    log("=== FARPOST: HTTP warm-up + card ===")
    lid = "138975631"
    url = f"https://www.farpost.ru/vladivostok/tech/communication/cellphones/apple-iphone-17-256gb-green-sage-zelenyj-esim-bez-rustore-ucenka-ion-{lid}.html"
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        # Warm-up
        r0 = s.get("https://www.farpost.ru/", timeout=20)
        log(f"  FP warm-up: HTTP {r0.status_code}")
        time.sleep(2)
        # Card
        r = s.get(url, timeout=25, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
        log(f"  FP card: HTTP {r.status_code} len={len(r.text)}")
        html = r.text
        # Title
        title = None
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1).strip()
        if not title:
            m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
            if m: title = m.group(1).strip()
        # Price
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
        if title and price_cents:
            eye = build_eye("FARPOST", lid, url, title, price_cents)
            acc = feed_inbox(app, eye, "FARPOST")
            sq = verify_sqlite("FARPOST")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["FARPOST"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None,
                                  "strategy": "http_warmup"}
            log(f"  FP VERDICT: {verdict}")
        else:
            results["FARPOST"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("FARPOST"), "reason": f"title={bool(title)} price={price_cents}"}
            log(f"  FP VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  FP EXCEPTION: {e}\n{traceback.format_exc()}")
        results["FARPOST"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("FARPOST"), "error": str(e)}
    return results.get("FARPOST", {})

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E FINAL6 START ===")
    app = make_app()
    log("App ready, DB initialized")

    # Order: WB (fastest) → Farpost → Ozon (Playwright) → Avito (slowest)
    run_wb(app)
    run_farpost(app)
    run_ozon(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"=== E2E FINAL6 DONE in {elapsed:.1f}s ===")

    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
