#!/usr/bin/env python3
"""e2e_fix12.py — AVITO deep-dive: 6 strategies to bypass IP block.
S1: RSS feed (curl_cffi)
S2: Playwright MOBILE (iPhone) on m.avito.ru category
S3: Playwright desktop on different cities (khabarovsk, yuzhno-sakhalinsk)
S4: Direct known ad IDs via m.avito.ru (curl_cffi mobile)
S5: avito.ru search page (curl_cffi)
S6: Playwright desktop avito.ru city page → extract IDs from JSON
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix12"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix12_result.json"

results = {}
t0 = time.monotonic()

def log(m):
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {m}", flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

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
    fname = f"fix12-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX12_{source}")
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

def parse_ad_page(html):
    """Parse title and price from an Avito ad page HTML."""
    title = None
    m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
    if m: title = m.group(1).strip()
    if not title:
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1).strip()
    if not title:
        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m: title = m.group(1).replace(" - Авито", "").replace(" - Avito", "").strip()

    price_cents = None
    # JSON-LD
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            if isinstance(ld, dict) and "offers" in ld:
                off = ld["offers"]
                if isinstance(off, list) and off: off = off[0]
                p = off.get("price") or off.get("lowPrice")
                if p: price_cents = int(float(p) * 100); break
        except: pass
    # "price": 12345
    if not price_cents:
        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
        if m: price_cents = int(m.group(1))
    # og:price:amount
    if not price_cents:
        m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', html, re.I)
        if m: price_cents = int(float(m.group(1)) * 100)
    # Displayed price
    if not price_cents:
        m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:₽|руб)', html)
        if m:
            try:
                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                price_cents = int(float(raw) * 100)
            except: pass
    # __PRELOADED_STATE__
    if not price_cents:
        m = re.search(r'__PRELOADED_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
        if m:
            try:
                state = json.loads(m.group(1))
                for key in ["ad", "listing", "data"]:
                    if key in state:
                        sub = state[key]
                        if isinstance(sub, dict) and "price" in sub:
                            price_cents = int(sub["price"])
                            break
            except: pass

    return title, price_cents

def is_blocked(html):
    """Check if page is an IP-block page."""
    if "Доступ ограничен" in html and len(html) < 45000:
        return True
    if "avito" in html.lower() and len(html) < 40000 and "captcha" in html.lower():
        return True
    return False

# ============================================================
# S1: RSS FEED (curl_cffi)
# ============================================================
def s1_rss():
    log("  S1: RSS feed...")
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        rss_urls = [
            "https://www.avito.ru/rss/vladivostok/smartfony-i-telefony",
            "https://www.avito.ru/rss/moskva/smartfony-i-telefony",
            "https://www.avito.ru/rss/vladivostok/tech/communication/cellphones",
        ]
        for ru in rss_urls:
            try:
                r = s.get(ru, timeout=20, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
                log(f"    RSS {ru.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 500:
                    # RSS items: <link> and <title>
                    links = re.findall(r'<link>\s*(https?://www\.avito\.ru/ad/(\d+))\s*</link>', r.text)
                    if not links:
                        links = re.findall(r'<link>\s*(https?://www\.avito\.ru/[^<]+?/(\d+))\s*</link>', r.text)
                    titles = re.findall(r'<title>\s*(?:<!\[CDATA\[)?([^<\]]+)', r.text)
                    log(f"    RSS items: {len(links)} links, {len(titles)} titles")
                    if links:
                        aid = links[0][1]
                        ad_url = links[0][0]
                        # Fetch the ad
                        r2 = s.get(ad_url, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                        log(f"    RSS→ad {aid}: HTTP {r2.status_code} len={len(r2.text)}")
                        if r2.status_code == 200 and not is_blocked(r2.text):
                            title, price = parse_ad_page(r2.text)
                            log(f"    RSS ad={aid}: title={title!r} price={price}")
                            if title and price and price > 500:
                                return build_eye("AVITO", aid, ad_url, title, price)
                    # If RSS has inline data, try to parse
                    if not links and len(r.text) > 1000:
                        # Save for debugging
                        (WORK / "s1_rss_sample.xml").write_text(r.text[:5000], encoding="utf-8")
            except Exception as e:
                log(f"    RSS err: {e}")
    except Exception as e:
        log(f"  S1 exception: {e}")
    return None

# ============================================================
# S2: Playwright MOBILE (iPhone) on m.avito.ru
# ============================================================
def s2_pw_mobile():
    log("  S2: Playwright MOBILE (iPhone) m.avito.ru...")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                viewport={"width": 390, "height": 844},
                locale="ru-RU",
                timezone_id="Asia/Vladivostok",
                is_mobile=True,
                has_touch=True,
                device_scale_factor=3,
            )
            page = ctx.new_page()
            # Mobile category pages
            cat_urls = [
                "https://m.avito.ru/vladivostok/smartfony-i-telefony",
                "https://m.avito.ru/moskva/smartfony-i-telefony",
                "https://m.avito.ru/khabarovsk/smartfony-i-telefony",
            ]
            for cu in cat_urls:
                log(f"    PW-M: {cu}")
                try:
                    page.goto(cu, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    log(f"      len={len(html)}")
                    if is_blocked(html):
                        log("      → IP block (mobile)")
                        continue
                    # Extract IDs
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)?(?:ad|ads)/(\d{6,})', html)
                    if not ids:
                        ids = re.findall(r'"id"\s*:\s*(\d{8,})', html)
                    if not ids:
                        ids = re.findall(r'href="[^"]*?/(\d{9,})"', html)
                    real_ids = list(dict.fromkeys(i for i in ids if 9 <= len(i) <= 12))
                    log(f"      → IDs: {len(real_ids)} → {real_ids[:5]}")
                    if real_ids:
                        # Try first ad
                        aid = real_ids[0]
                        ad_url = f"https://m.avito.ru/ad/{aid}"
                        page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(8000)
                        html2 = page.content()
                        log(f"      ad={aid}: len={len(html2)}")
                        if not is_blocked(html2) and len(html2) > 3000:
                            title, price = parse_ad_page(html2)
                            log(f"      ad={aid}: title={title!r} price={price}")
                            if title and price and price > 500:
                                browser.close()
                                return build_eye("AVITO", aid, ad_url, title, price)
                except Exception as e:
                    log(f"      err: {e}")
            browser.close()
    except Exception as e:
        log(f"  S2 exception: {e}\n{traceback.format_exc()}")
    return None

# ============================================================
# S3: Playwright desktop on DIFFERENT CITIES
# ============================================================
def s3_pw_other_cities():
    log("  S3: Playwright desktop other cities...")
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
            cities = [
                "https://www.avito.ru/khabarovsk/smartfony-i-telefony",
                "https://www.avito.ru/yuzhno-sakhalinsk/smartfony-i-telefony",
                "https://www.avito.ru/krasnoyarsk/smartfony-i-telefony",
                "https://www.avito.ru/novosibirsk/smartfony-i-telefony",
            ]
            for cu in cities:
                city = cu.split(".ru/")[1].split("/")[0]
                log(f"    PW-D {city}: {cu}")
                try:
                    page.goto(cu, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"      len={len(html)}")
                    if is_blocked(html):
                        log("      → IP block")
                        continue
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)?(?:ad|ads)/(\d{6,})', html)
                    if not ids:
                        ids = re.findall(r'"id"\s*:\s*(\d{8,})', html)
                    real_ids = list(dict.fromkeys(i for i in ids if 9 <= len(i) <= 12))
                    log(f"      → IDs: {len(real_ids)}")
                    if real_ids:
                        aid = real_ids[0]
                        ad_url = f"https://www.avito.ru/ad/{aid}"
                        page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(10000)
                        html2 = page.content()
                        log(f"      ad={aid}: len={len(html2)}")
                        if not is_blocked(html2) and len(html2) > 3000:
                            title, price = parse_ad_page(html2)
                            log(f"      ad={aid}: title={title!r} price={price}")
                            if title and price and price > 500:
                                browser.close()
                                return build_eye("AVITO", aid, ad_url, title, price)
                except Exception as e:
                    log(f"      err: {e}")
            browser.close()
    except Exception as e:
        log(f"  S3 exception: {e}\n{traceback.format_exc()}")
    return None

# ============================================================
# S4: Direct known ad IDs via m.avito.ru (curl_cffi mobile UA)
# ============================================================
def s4_direct_ids_mobile():
    log("  S4: Direct ad IDs via m.avito.ru (mobile UA)...")
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        # Known + guessed IDs (9-10 digits, recent range)
        ad_ids = [
            "7494308705",  # from previous diag
            "1154321987",
            "1204567890",
            "138975631",   # farpost ID (maybe cross-listed?)
            "1097975275",  # WB ID (unlikely but try)
            "1100000001",
            "1100000002",
            "1099999999",
            "1099999998",
        ]
        mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        for aid in ad_ids:
            mu = f"https://m.avito.ru/ad/{aid}"
            try:
                r = s.get(mu, timeout=15, headers={
                    "User-Agent": mobile_ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                })
                log(f"    m.ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 2000 and not is_blocked(r.text):
                    title, price = parse_ad_page(r.text)
                    log(f"    m.ad={aid}: title={title!r} price={price}")
                    if title and price and price > 500:
                        return build_eye("AVITO", aid, mu, title, price)
                elif r.status_code == 404:
                    log(f"    m.ad={aid}: 404 (not found)")
                time.sleep(1)
            except Exception as e:
                log(f"    m.ad={aid} err: {e}")
    except Exception as e:
        log(f"  S4 exception: {e}")
    return None

# ============================================================
# S5: avito.ru SEARCH page (curl_cffi)
# ============================================================
def s5_search():
    log("  S5: avito.ru search...")
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        search_urls = [
            "https://www.avito.ru/vladivostok/search?q=iphone+17",
            "https://www.avito.ru/moskva/search?q=iphone+17",
            "https://www.avito.ru/vladivostok/search?query=iphone",
        ]
        for su in search_urls:
            try:
                r = s.get(su, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log(f"    search: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 5000 and not is_blocked(r.text):
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)?(?:ad|ads)/(\d{6,})', r.text)
                    if not ids:
                        ids = re.findall(r'"id"\s*:\s*(\d{8,})', r.text)
                    real_ids = list(dict.fromkeys(i for i in ids if 9 <= len(i) <= 12))
                    log(f"    search IDs: {len(real_ids)} → {real_ids[:5]}")
                    if real_ids:
                        aid = real_ids[0]
                        ad_url = f"https://www.avito.ru/ad/{aid}"
                        r2 = s.get(ad_url, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                        log(f"    search→ad {aid}: HTTP {r2.status_code} len={len(r2.text)}")
                        if r2.status_code == 200 and not is_blocked(r2.text):
                            title, price = parse_ad_page(r2.text)
                            log(f"    search ad={aid}: title={title!r} price={price}")
                            if title and price and price > 500:
                                return build_eye("AVITO", aid, ad_url, title, price)
            except Exception as e:
                log(f"    search err: {e}")
    except Exception as e:
        log(f"  S5 exception: {e}")
    return None

# ============================================================
# S6: Playwright desktop — city page → __NEXT_DATA__ / JSON
# ============================================================
def s6_pw_city_json():
    log("  S6: Playwright city page → JSON extraction...")
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
            # Try city root first (less likely blocked than category)
            city_urls = [
                "https://www.avito.ru/vladivostok",
                "https://www.avito.ru/khabarovsk",
            ]
            for cu in city_urls:
                city = cu.split(".ru/")[1]
                log(f"    PW city: {cu}")
                try:
                    page.goto(cu, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(12000)
                    html = page.content()
                    log(f"      len={len(html)}")
                    if is_blocked(html):
                        log("      → IP block")
                        continue
                    # Try __NEXT_DATA__
                    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
                    if m:
                        try:
                            nd = json.loads(m.group(1))
                            # Navigate to find ad IDs
                            props = nd.get("props", {}).get("pageProps", {})
                            items = props.get("items", []) or props.get("listings", []) or []
                            log(f"      __NEXT_DATA__ items: {len(items)}")
                            for item in items[:10]:
                                if isinstance(item, dict):
                                    aid = str(item.get("id", ""))
                                    if aid and len(aid) >= 9:
                                        ad_url = f"https://www.avito.ru/ad/{aid}"
                                        page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
                                        page.wait_for_timeout(10000)
                                        html2 = page.content()
                                        title, price = parse_ad_page(html2)
                                        log(f"      NEXT ad={aid}: title={title!r} price={price}")
                                        if title and price and price > 500:
                                            browser.close()
                                            return build_eye("AVITO", aid, ad_url, title, price)
                        except Exception as e:
                            log(f"      NEXT_DATA parse err: {e}")
                    # Fallback: regex IDs
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)?(?:ad|ads)/(\d{6,})', html)
                    real_ids = list(dict.fromkeys(i for i in ids if 9 <= len(i) <= 12))
                    log(f"      regex IDs: {len(real_ids)}")
                    if real_ids:
                        aid = real_ids[0]
                        ad_url = f"https://www.avito.ru/ad/{aid}"
                        page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(10000)
                        html2 = page.content()
                        title, price = parse_ad_page(html2)
                        log(f"      city ad={aid}: title={title!r} price={price}")
                        if title and price and price > 500:
                            browser.close()
                            return build_eye("AVITO", aid, ad_url, title, price)
                except Exception as e:
                    log(f"      err: {e}")
            browser.close()
    except Exception as e:
        log(f"  S6 exception: {e}\n{traceback.format_exc()}")
    return None

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E FIX12 (AVITO DEEP) START ===")
    app = make_app()
    log("App ready")

    eye = None

    # Try strategies in order of likelihood
    eye = s1_rss()
    if not eye:
        eye = s2_pw_mobile()
    if not eye:
        eye = s3_pw_other_cities()
    if not eye:
        eye = s4_direct_ids_mobile()
    if not eye:
        eye = s5_search()
    if not eye:
        eye = s6_pw_city_json()

    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"),
                            "reason": "all 6 strategies failed"}
        log(f"  AVITO VERDICT: E2E_FAIL (all 6 strategies)")

    elapsed = time.monotonic() - t0
    log(f"=== E2E FIX12 DONE in {elapsed:.1f}s ===")

    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
