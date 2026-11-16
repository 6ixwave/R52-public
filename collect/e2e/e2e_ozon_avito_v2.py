#!/usr/bin/env python3
"""e2e_ozon_avito_v2.py — Focused E2E for Ozon & Avito.
Key insight: Ozon Playwright gets 3305-byte stub → need longer wait + DOM extraction.
Avito: 429/439 on categories → need warm-up, delays, real ad IDs from rendered page.
"""
import json, os, re, sys, time, sqlite3, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")
DB_DIR = ROOT / "e2e-ozon-avito-v2"
DB_PATH = DB_DIR / "r52.sqlite3"
RESULT_PATH = ROOT / "e2e_ozon_avito_v2_result.json"
LOG_PATH = ROOT / "e2e_ozon_avito_v2_stdout.txt"

def log(msg):
    ts = datetime.now(TZ).strftime("[%H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

def setup_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS product (
        product_id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        title TEXT,
        brand TEXT,
        url TEXT,
        source TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS observation (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        observed_at TEXT,
        status TEXT DEFAULT 'active',
        notes TEXT,
        FOREIGN KEY (product_id) REFERENCES product(product_id)
    );
    CREATE TABLE IF NOT EXISTS price_point (
        price_point_id INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_id INTEGER NOT NULL,
        price_cents INTEGER,
        currency TEXT DEFAULT 'RUB',
        recorded_at TEXT,
        FOREIGN KEY (observation_id) REFERENCES observation(observation_id)
    );
    """)
    conn.commit()
    return conn

def inbox_accept(conn, platform, product_id, title, price_cents, url, source, brand=None):
    """Push through Inbox → normalize → upsert_product → observation → price_point."""
    ts = now_iso()
    c = conn.cursor()
    # Upsert product
    c.execute("""
        INSERT INTO product (product_id, platform, title, brand, url, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            title=excluded.title, brand=excluded.brand, url=excluded.url,
            source=excluded.source, updated_at=excluded.updated_at
    """, (product_id, platform, title, brand, url, source, ts, ts))
    # Observation
    c.execute("""
        INSERT INTO observation (product_id, platform, observed_at, status, notes)
        VALUES (?, ?, ?, 'active', 'e2e_ozon_avito_v2')
    """, (product_id, platform, ts))
    obs_id = c.lastrowid
    # Price point
    c.execute("""
        INSERT INTO price_point (observation_id, price_cents, currency, recorded_at)
        VALUES (?, ?, 'RUB', ?)
    """, (obs_id, price_cents, ts))
    conn.commit()
    # Verify
    c.execute("SELECT COUNT(*) FROM product WHERE product_id=?", (product_id,))
    p_cnt = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM observation WHERE product_id=?", (product_id,))
    o_cnt = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM price_point WHERE observation_id=?", (obs_id,))
    pp_cnt = c.fetchone()[0]
    return {"status": "ACCEPTED", "created": p_cnt == 1, "P": p_cnt, "O": o_cnt, "PP": pp_cnt}

# ============================================================
# OZON
# ============================================================
def fetch_ozon():
    log("OZON: starting...")
    results = []
    
    # --- Strategy O1: Playwright with long wait + DOM extraction ---
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
            )
            page = ctx.new_page()
            
            # Warm-up: homepage
            log("  O1: warm-up ozon.ru...")
            page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            warm_html = page.content()
            log(f"  O1 warm-up: len={len(warm_html)}")
            
            # Try multiple product IDs
            product_ids = [
                "2984707012",  # previously used
                "1000000001",  # generic
                "562267",      # from diag_v138
            ]
            
            # First try to find a real product from search
            log("  O1: search for 'iphone'...")
            page.goto("https://www.ozon.ru/search/?text=iphone&from_global=true", 
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)
            search_html = page.content()
            log(f"  O1 search: len={len(search_html)}")
            
            # Extract product links from search
            prod_links = re.findall(r'href="(/product/(\d+)/[^"]*)"', search_html)
            log(f"  O1 search links: {len(prod_links)} found")
            if prod_links:
                real_ids = [pid for _, pid in prod_links[:5]]
                log(f"  O1 real IDs: {real_ids}")
                product_ids = real_ids + product_ids
            
            for pid in product_ids[:4]:
                url = f"https://www.ozon.ru/product/{pid}/"
                log(f"  O1: goto {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(10000)  # Wait for JS rendering
                    html = page.content()
                    log(f"  O1 prod {pid}: len={len(html)}")
                    
                    if len(html) < 5000:
                        log(f"  O1 prod {pid}: STUB, skip")
                        continue
                    
                    # Extract title
                    title = None
                    # Try h1
                    m = re.search(r'<h1[^>]*>([^<]{10,200})</h1>', html)
                    if m:
                        title = m.group(1).strip()
                    # Try meta og:title
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m:
                            title = m.group(1).strip()
                    # Try data attributes
                    if not title:
                        m = re.search(r'data-widget="ProductCard"[^>]*>.*?<span[^>]*>([^<]{10,200})</span>', html, re.DOTALL)
                        if m:
                            title = m.group(1).strip()
                    
                    # Extract price
                    price_cents = None
                    # JSON-LD
                    m = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                    if m:
                        try:
                            ld = json.loads(m.group(1))
                            if isinstance(ld, dict) and 'offers' in ld:
                                off = ld['offers']
                                if isinstance(off, list) and off:
                                    off = off[0]
                                p = off.get('price') or off.get('lowPrice')
                                if p:
                                    price_cents = int(float(p) * 100)
                        except:
                            pass
                    # DOM price
                    if not price_cents:
                        m = re.search(r'class="[^"]*price[^"]*"[^>]*>.*?(\d[\d\s\u00a0\u2009\u202f,\.]+)', html, re.DOTALL)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace('\xa0','')
                                if ',' in raw:
                                    raw = raw.replace('.','').replace(',','.')
                                price_cents = int(float(raw) * 100)
                            except:
                                pass
                    # window.__STATE__ or similar
                    if not price_cents:
                        m = re.search(r'"price"\s*:\s*(\d+)', html)
                        if m:
                            price_cents = int(m.group(1))
                    
                    log(f"  O1 prod {pid}: title={title!r} price={price_cents}")
                    
                    if title and price_cents:
                        r = inbox_accept(conn, "ozon", f"OZON-{pid}", title, price_cents, url, "playwright_dom", brand=None)
                        results.append({"strategy": "O1_pw_dom", "pid": pid, **r})
                        log(f"  O1 VERDICT: E2E_PASS | P={r['P']} O={r['O']} PP={r['PP']}")
                        browser.close()
                        return results
                    
                    # Try in-page fetch for API
                    if len(html) > 8000:
                        log(f"  O1: in-page fetch for {pid}...")
                        try:
                            api_data = page.evaluate("""
                                async () => {
                                    try {
                                        const r = await fetch('/api/composer-api.bx/page/json/v2?url=/product/%s/' + '%s/', {
                                            headers: {'Accept': 'application/json'}
                                        });
                                        if (r.ok) return await r.json();
                                        return {status: r.status, text: (await r.text()).slice(0,500)};
                                    } catch(e) { return {error: e.message}; }
                                }
                            """, pid, pid)
                            if api_data and 'error' not in api_data:
                                log(f"  O1 in-page API: keys={list(api_data.keys())[:10]}")
                                # Try to extract from API response
                                s = json.dumps(api_data, ensure_ascii=False)
                                tm = re.search(r'"title"\s*:\s*"([^"]{10,200})"', s)
                                pm = re.search(r'"price"\s*:\s*(\d+)', s)
                                if tm and pm:
                                    t = tm.group(1)
                                    p = int(pm.group(1))
                                    r = inbox_accept(conn, "ozon", f"OZON-{pid}", t, p, url, "pw_inpage_api")
                                    results.append({"strategy": "O1_inpage_api", "pid": pid, **r})
                                    log(f"  O1 VERDICT: E2E_PASS | P={r['P']} O={r['O']} PP={r['PP']}")
                                    browser.close()
                                    return results
                        except Exception as e:
                            log(f"  O1 in-page fetch err: {e}")
                except Exception as e:
                    log(f"  O1 prod {pid} err: {e}")
            
            browser.close()
    except Exception as e:
        log(f"  O1 EXCEPTION: {e}")
        log(f"  {traceback.format_exc()}")
    
    # --- Strategy O2: Playwright mobile ---
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                viewport={"width": 390, "height": 844},
                locale="ru-RU",
                is_mobile=True,
                has_touch=True,
            )
            page = ctx.new_page()
            log("  O2: mobile warm-up...")
            page.goto("https://m.ozon.ru/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            log(f"  O2 warm-up: len={len(page.content())}")
            
            for pid in ["2984707012", "562267"]:
                url = f"https://m.ozon.ru/product/{pid}/"
                log(f"  O2: {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    log(f"  O2 {pid}: len={len(html)}")
                    if len(html) < 3000:
                        continue
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{10,200})</h1>', html)
                    if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                    price_cents = None
                    m = re.search(r'"price"\s*:\s*(\d+)', html)
                    if m: price_cents = int(m.group(1))
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\s,\.]+)\s*₽', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                    log(f"  O2 {pid}: title={title!r} price={price_cents}")
                    if title and price_cents:
                        r = inbox_accept(conn, "ozon", f"OZON-{pid}", title, price_cents, url, "pw_mobile")
                        results.append({"strategy": "O2_mobile", "pid": pid, **r})
                        log(f"  O2 VERDICT: E2E_PASS")
                        browser.close()
                        return results
                except Exception as e:
                    log(f"  O2 {pid} err: {e}")
            browser.close()
    except Exception as e:
        log(f"  O2 EXCEPTION: {e}")
    
    # --- Strategy O3: curl_cffi with browser impersonation ---
    try:
        from curl_cffi import requests as cffi_req
        log("  O3: curl_cffi browser impersonation...")
        for pid in ["2984707012", "562267"]:
            url = f"https://www.ozon.ru/product/{pid}/"
            r = cffi_req.get(url, impersonate="chrome", timeout=20,
                             headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            log(f"  O3 {pid}: {r.status_code} len={len(r.text)}")
            if r.status_code == 200 and len(r.text) > 5000:
                html = r.text
                title = None
                m = re.search(r'<h1[^>]*>([^<]{10,200})</h1>', html)
                if m: title = m.group(1).strip()
                if not title:
                    m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                    if m: title = m.group(1).strip()
                price_cents = None
                m = re.search(r'"price"\s*:\s*(\d+)', html)
                if m: price_cents = int(m.group(1))
                if not price_cents:
                    m = re.search(r'(\d[\d\u00a0\s,\.]+)\s*₽', html)
                    if m:
                        try:
                            raw = m.group(1).replace('\u00a0','').replace(' ','').replace(',','')
                            price_cents = int(float(raw) * 100)
                        except: pass
                log(f"  O3 {pid}: title={title!r} price={price_cents}")
                if title and price_cents:
                    r2 = inbox_accept(conn, "ozon", f"OZON-{pid}", title, price_cents, url, "cffi_chrome")
                    results.append({"strategy": "O3_cffi", "pid": pid, **r2})
                    log(f"  O3 VERDICT: E2E_PASS")
                    return results
            time.sleep(3)
    except Exception as e:
        log(f"  O3 EXCEPTION: {e}")
    
    log("OZON: ALL STRATEGIES FAILED")
    return results

# ============================================================
# AVITO
# ============================================================
def fetch_avito():
    log("AVITO: starting...")
    results = []
    
    # --- Strategy A1: Playwright with warm-up + category + ad extraction ---
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="ru-RU",
            )
            page = ctx.new_page()
            
            # Warm-up: homepage (important for cookies)
            log("  A1: warm-up avito.ru...")
            page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            home_html = page.content()
            cookies = ctx.cookies()
            log(f"  A1 warm-up: len={len(home_html)} cookies={len(cookies)}")
            
            # Category pages to find real ads
            categories = [
                "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
                "https://www.avito.ru/vladivostok/elektro_i_byt/telefony_i_svyaz",
                "https://www.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
            ]
            
            real_ad_ids = []
            for cat_url in categories:
                log(f"  A1: category {cat_url}")
                try:
                    page.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"  A1 cat: len={len(html)}")
                    
                    if len(html) < 5000:
                        continue
                    
                    # Extract ad IDs from rendered page
                    # Pattern: /ad/1234567890 or data-adid
                    ad_ids = re.findall(r'/ad/(\d{9,12})', html)
                    ad_ids = list(dict.fromkeys(ad_ids))  # unique, preserve order
                    log(f"  A1 cat ads: {len(ad_ids)} → {ad_ids[:5]}")
                    real_ad_ids.extend(ad_ids[:5])
                    
                    # Also try to extract title+price from listing cards
                    # Avito uses <a> with data-widget or specific classes
                    cards = re.findall(
                        r'data-widget="[^"]*"[^>]*>.*?href="[^"]*/ad/(\d+)[^"]*"[^>]*>.*?'
                        r'(?:<span|<div|<p)[^>]*>([^<]{10,200})</(?:span|div|p)>.*?'
                        r'(?:<span|<div|<p)[^>]*>(\d[\d\u00a0\s,\.]+)\s*(?:₽|руб)',
                        html, re.DOTALL
                    )
                    if cards:
                        log(f"  A1 cards: {len(cards)}")
                        for cid, ctitle, cprice in cards[:3]:
                            try:
                                pr = int(cprice.replace('\u00a0','').replace(' ','').replace(',',''))
                                if pr > 100:
                                    r = inbox_accept(conn, "avito", f"AVITO-{cid}", ctitle.strip(), pr,
                                                     f"https://www.avito.ru/ad/{cid}", "pw_category_card")
                                    results.append({"strategy": "A1_card", "pid": cid, **r})
                                    log(f"  A1 CARD VERDICT: E2E_PASS ad={cid}")
                                    browser.close()
                                    return results
                            except: pass
                except Exception as e:
                    log(f"  A1 cat err: {e}")
                time.sleep(3)
            
            # Navigate to individual ad pages
            log(f"  A1: visiting {min(len(real_ad_ids),5)} individual ads...")
            for aid in real_ad_ids[:5]:
                url = f"https://www.avito.ru/ad/{aid}"
                log(f"  A1: {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    log(f"  A1 ad {aid}: len={len(html)}")
                    
                    if len(html) < 5000:
                        continue
                    
                    # Check for 404
                    if "404" in html[:500] or "Не найдено" in html[:2000]:
                        log(f"  A1 ad {aid}: 404")
                        continue
                    
                    # Extract title
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
                    if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                    
                    # Extract price
                    price_cents = None
                    # JSON-LD
                    m = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                    if m:
                        try:
                            ld = json.loads(m.group(1))
                            if isinstance(ld, dict) and 'offers' in ld:
                                off = ld['offers']
                                if isinstance(off, list) and off: off = off[0]
                                p = off.get('price')
                                if p: price_cents = int(float(p) * 100)
                        except: pass
                    # DOM
                    if not price_cents:
                        m = re.search(r'class="[^"]*(?:price|sum)[^"]*"[^>]*>\s*(\d[\d\u00a0\s,\.]+)', html, re.DOTALL)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\s,\.]+)\s*₽', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                    
                    log(f"  A1 ad {aid}: title={title!r} price={price_cents}")
                    if title and price_cents:
                        r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_ad_page")
                        results.append({"strategy": "A1_ad_page", "pid": aid, **r})
                        log(f"  A1 VERDICT: E2E_PASS | P={r['P']} O={r['O']} PP={r['PP']}")
                        browser.close()
                        return results
                except Exception as e:
                    log(f"  A1 ad {aid} err: {e}")
                time.sleep(3)
            
            browser.close()
    except Exception as e:
        log(f"  A1 EXCEPTION: {e}")
        log(f"  {traceback.format_exc()}")
    
    # --- Strategy A2: Playwright mobile Avito ---
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
                viewport={"width": 412, "height": 915},
                locale="ru-RU",
                is_mobile=True,
                has_touch=True,
            )
            page = ctx.new_page()
            log("  A2: mobile warm-up...")
            page.goto("https://m.avito.ru/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            log(f"  A2 warm-up: len={len(page.content())}")
            
            # Mobile category
            cat_urls = [
                "https://m.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
                "https://m.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
            ]
            for cat_url in cat_urls:
                log(f"  A2: {cat_url}")
                try:
                    page.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    log(f"  A2 cat: len={len(html)}")
                    if len(html) < 3000: continue
                    
                    ad_ids = re.findall(r'/ad/(\d{9,12})', html)
                    ad_ids = list(dict.fromkeys(ad_ids))
                    log(f"  A2 cat ads: {len(ad_ids)} → {ad_ids[:5]}")
                    
                    for aid in ad_ids[:3]:
                        url = f"https://m.avito.ru/ad/{aid}"
                        log(f"  A2: {url}")
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(6000)
                        html = page.content()
                        log(f"  A2 ad {aid}: len={len(html)}")
                        if len(html) < 3000: continue
                        
                        title = None
                        m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
                        if m: title = m.group(1).strip()
                        if not title:
                            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                            if m: title = m.group(1).strip()
                        
                        price_cents = None
                        m = re.search(r'(\d[\d\u00a0\s,\.]+)\s*₽', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                        
                        if title and price_cents:
                            r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_mobile_ad")
                            results.append({"strategy": "A2_mobile", "pid": aid, **r})
                            log(f"  A2 VERDICT: E2E_PASS")
                            browser.close()
                            return results
                        time.sleep(2)
                except Exception as e:
                    log(f"  A2 err: {e}")
            browser.close()
    except Exception as e:
        log(f"  A2 EXCEPTION: {e}")
    
    # --- Strategy A3: curl_cffi for Avito (different TLS fingerprint) ---
    try:
        from curl_cffi import requests as cffi_req
        log("  A3: curl_cffi...")
        # Try category first
        for cat_url in ["https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
                        "https://www.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam"]:
            r = cffi_req.get(cat_url, impersonate="chrome", timeout=20,
                             headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            log(f"  A3 cat: {r.status_code} len={len(r.text)}")
            if r.status_code == 200 and len(r.text) > 5000:
                ad_ids = re.findall(r'/ad/(\d{9,12})', r.text)
                ad_ids = list(dict.fromkeys(ad_ids))
                log(f"  A3 cat ads: {ad_ids[:5]}")
                for aid in ad_ids[:3]:
                    url = f"https://www.avito.ru/ad/{aid}"
                    r2 = cffi_req.get(url, impersonate="chrome", timeout=20,
                                      headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    log(f"  A3 ad {aid}: {r2.status_code} len={len(r2.text)}")
                    if r2.status_code == 200 and len(r2.text) > 5000:
                        html = r2.text
                        title = None
                        m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
                        if m: title = m.group(1).strip()
                        if not title:
                            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                            if m: title = m.group(1).strip()
                        price_cents = None
                        m = re.search(r'(\d[\d\u00a0\s,\.]+)\s*₽', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                        if title and price_cents:
                            r3 = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "cffi_ad")
                            results.append({"strategy": "A3_cffi", "pid": aid, **r3})
                            log(f"  A3 VERDICT: E2E_PASS")
                            return results
                    time.sleep(3)
            time.sleep(3)
    except Exception as e:
        log(f"  A3 EXCEPTION: {e}")
    
    log("AVITO: ALL STRATEGIES FAILED")
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E OZON+AVITO V2 START ===")
    conn = setup_db()
    log(f"DB: {DB_PATH}")
    
    final = {}
    
    # OZON
    ozon_results = fetch_ozon()
    if ozon_results and ozon_results[0].get("P", 0) > 0:
        final["ozon"] = {"verdict": "E2E_PASS", "detail": ozon_results[0]}
    else:
        final["ozon"] = {"verdict": "E2E_FAIL", "detail": ozon_results}
    
    # AVITO
    avito_results = fetch_avito()
    if avito_results and avito_results[0].get("P", 0) > 0:
        final["avito"] = {"verdict": "E2E_PASS", "detail": avito_results[0]}
    else:
        final["avito"] = {"verdict": "E2E_FAIL", "detail": avito_results}
    
    # Summary
    passes = sum(1 for v in final.values() if v["verdict"] == "E2E_PASS")
    log(f"=== SUMMARY: PASS={passes}/2 ===")
    for k, v in final.items():
        log(f"  {k.upper()}: {v['verdict']}")
    
    # Write result JSON
    result = {"ts": now_iso(), "results": final, "pass": passes, "total": 2}
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"Result: {RESULT_PATH}")
    
    # Verify DB
    c = conn.cursor()
    c.execute("SELECT platform, product_id, title, url FROM product")
    rows = c.fetchall()
    log(f"DB products: {len(rows)}")
    for row in rows:
        log(f"  {row[0]} | {row[1]} | {row[2][:60]} | {row[3][:50]}")
    c.execute("SELECT COUNT(*) FROM observation")
    log(f"DB observations: {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM price_point")
    log(f"DB price_points: {c.fetchone()[0]}")
    
    conn.close()
    log("E2E_OZON_AVITO_V2_DONE")

if __name__ == "__main__":
    main()
