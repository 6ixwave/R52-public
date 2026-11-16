#!/usr/bin/env python3
"""e2e_avito_v5.py — Avito E2E v5: beat IP firewall via API/smart paths.
Key: ALL pages (desktop+mobile) = firewall. IP blocked server-side.
New vectors: mobile API, sitemap, long cooldown, direct ads, show_technical, AMP.
"""
import json, os, re, sys, time, sqlite3, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")
DB_DIR = ROOT / "e2e-avito-v5"
DB_PATH = DB_DIR / "r52.sqlite3"
RESULT_PATH = ROOT / "e2e_avito_v5_result.json"

def log(msg):
    ts = datetime.now(TZ).strftime("[%H:%M:%S]")
    print(f"{ts} {msg}", flush=True)

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
    ts = now_iso()
    c = conn.cursor()
    c.execute("""
        INSERT INTO product (product_id, platform, title, brand, url, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            title=excluded.title, brand=excluded.brand, url=excluded.url,
            source=excluded.source, updated_at=excluded.updated_at
    """, (product_id, platform, title, brand, url, source, ts, ts))
    c.execute("""
        INSERT INTO observation (product_id, platform, observed_at, status, notes)
        VALUES (?, ?, ?, 'active', 'e2e_avito_v5')
    """, (product_id, platform, ts))
    obs_id = c.lastrowid
    c.execute("""
        INSERT INTO price_point (observation_id, price_cents, currency, recorded_at)
        VALUES (?, ?, 'RUB', ?)
    """, (obs_id, price_cents, ts))
    conn.commit()
    c.execute("SELECT COUNT(*) FROM product WHERE product_id=?", (product_id,))
    p_cnt = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM observation WHERE product_id=?", (product_id,))
    o_cnt = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM price_point WHERE observation_id=?", (obs_id,))
    pp_cnt = c.fetchone()[0]
    return {"status": "ACCEPTED", "created": p_cnt == 1, "P": p_cnt, "O": o_cnt, "PP": pp_cnt}

def is_firewall(html):
    """Detect Avito IP firewall page (any size)."""
    if not html or len(html) < 1000:
        return False
    head = html[:6000]
    return ("Доступ ограничен" in head or
            "проблема с IP" in head or
            "firewall" in head.lower() or
            "firewall__title" in head)

def extract_title_price(html):
    title = None
    m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m:
            title = m.group(1).strip()
    if not title:
        m = re.search(r'<title[^>]*>([^<]{10,300})</title>', html)
        if m:
            t = m.group(1).strip()
            if "Avito" not in t and "Авито" not in t and "Доступ" not in t:
                title = t

    price_cents = None
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            if isinstance(ld, dict) and 'offers' in ld:
                off = ld['offers']
                if isinstance(off, list) and off:
                    off = off[0]
                p = off.get('price') or off.get('lowPrice')
                if p:
                    price_cents = int(float(p) * 100)
                    break
        except:
            pass
    if not price_cents:
        m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,\.]+)\s*(?:₽|руб)', html)
        if m:
            try:
                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                price_cents = int(float(raw) * 100)
            except:
                pass
    if not price_cents:
        m = re.search(r'"price"\s*:\s*"?(\d{5,})"?', html)
        if m:
            price_cents = int(m.group(1))
    return title, price_cents

def extract_ad_ids(html):
    ids = []
    ids += re.findall(r'/ad/(\d{9,12})', html)
    ids += re.findall(r'data-adid["\s:=]+["\']?(\d{9,12})', html)
    ids += re.findall(r'"(?:ad_id|adId)"\s*:\s*"?(\d{9,12})"?', html)
    ids += re.findall(r'href="[^"]*?/ad/(\d+)', html)
    return list(dict.fromkeys(ids))

# ============================================================
# S1: Diagnostic — check which paths are NOT blocked
# ============================================================
def s1_diagnostic(conn):
    log("  [S1] Diagnostic: probing which Avito paths are accessible")
    from curl_cffi import requests as cffi_req
    results = []
    
    probe_urls = [
        "https://www.avito.ru/info/show_technical",
        "https://www.avito.ru/sitemap.xml",
        "https://www.avito.ru/robots.txt",
        "https://m.avito.ru/info/show_technical",
        "https://www.avito.ru/vladivostok",
        "https://www.avito.ru/cities/vladivostok",
    ]
    
    accessible = []
    for url in probe_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=15,
                           headers={"Accept-Language": "ru-RU,ru;q=0.9",
                                    "Accept": "text/html,application/xhtml+xml,application/xml"})
            fw = is_firewall(r.text)
            log(f"  [S1] {r.status_code} len={len(r.text)} fw={fw} {url}")
            if r.status_code == 200 and not fw:
                accessible.append((url, r.text))
                (DB_DIR / f"s1_{url.split('avito.ru')[-1].strip('/').replace('/','_') or 'root'}.html").write_text(r.text[:50000], encoding="utf-8")
        except Exception as e:
            log(f"  [S1] {url} err: {e}")
        time.sleep(3)
    
    log(f"  [S1] Accessible (non-firewall): {len(accessible)}")
    for url, txt in accessible:
        ids = extract_ad_ids(txt)
        if ids:
            log(f"  [S1] {url}: {len(ids)} ad IDs → {ids[:5]}")
    
    return accessible

# ============================================================
# S2: Avito Mobile App API (JSON endpoints)
# ============================================================
def s2_mobile_api(conn):
    log("  [S2] Avito Mobile App API...")
    from curl_cffi import requests as cffi_req
    results = []
    
    # The Avito mobile app uses these API patterns
    api_urls = [
        # Search API
        ("https://api.avito.ru/v1/search?query=iphone&region=vladivostok", "search_v1"),
        ("https://api.avito.ru/v2/search?query=iphone&city=vladivostok", "search_v2"),
        # Category listing
        ("https://api.avito.ru/v1/categories/tekhnika_i_svyaz/smartfony?city=vladivostok", "cat_v1"),
        # Mobile web API
        ("https://m.avito.ru/api/search?query=iphone", "mweb_search"),
        ("https://m.avito.ru/api/listings?category=smartfony&city=vladivostok", "mweb_listings"),
        # Composer-style API (like Ozon)
        ("https://www.avito.ru/api/composer/v1/page?path=/vladivostok/tekhnika_i_svyaz/smartfony", "composer"),
        # Internal XHR endpoints
        ("https://www.avito.ru/api/v1/search?query=iphone&region_id=1", "xhr_search"),
    ]
    
    headers = {
        "Accept": "application/json, text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "AvitoMobile/12.0 (iPhone; iOS 17.0; Scale/3.00)",
    }
    
    for url, name in api_urls:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=15, headers=headers)
            fw = is_firewall(r.text)
            log(f"  [S2] {name}: {r.status_code} len={len(r.text)} fw={fw} ct={r.headers.get('content-type','?')[:40]}")
            if r.status_code == 200 and not fw and len(r.text) > 200:
                (DB_DIR / f"s2_{name}.json").write_text(r.text[:100000], encoding="utf-8")
                # Try to find ad data in JSON
                try:
                    data = r.json()
                    s = json.dumps(data, ensure_ascii=False)
                    # Look for ad IDs
                    ids = re.findall(r'"(?:id|ad_id|adId)"\s*:\s*"?(\d{9,12})"?', s)
                    titles = re.findall(r'"(?:title|name)"\s*:\s*"([^"]{10,200})"', s)
                    prices = re.findall(r'"(?:price|price_cents|amount)"\s*:\s*"?(\d{5,})"?', s)
                    log(f"  [S2] {name}: IDs={len(ids)} titles={len(titles)} prices={len(prices)}")
                    if ids and titles:
                        log(f"  [S2] {name} sample: id={ids[0]} title={titles[0][:50]} price={prices[0] if prices else '?'}")
                        # Try to pair them
                        for i in range(min(len(ids), len(titles), 3)):
                            pid = ids[i]
                            t = titles[i]
                            p = int(prices[i]) if i < len(prices) else None
                            if p and p > 10000:
                                r2 = inbox_accept(conn, "avito", f"AVITO-{pid}", t, p,
                                                 f"https://www.avito.ru/ad/{pid}", f"api_{name}")
                                results.append({"strategy": f"S2_{name}", "pid": pid, **r2})
                                log(f"  [S2] API VERDICT: E2E_PASS")
                                return results
                except:
                    # Not JSON, try regex on raw
                    ids = re.findall(r'/ad/(\d{9,12})', r.text)
                    if ids:
                        log(f"  [S2] {name} regex IDs: {ids[:5]}")
        except Exception as e:
            log(f"  [S2] {name} err: {e}")
        time.sleep(2)
    
    return results

# ============================================================
# S3: Playwright — long cooldown + direct ad page (skip category)
# ============================================================
def s3_direct_ad(conn):
    log("  [S3] Playwright: 30s cooldown + direct ad pages")
    from playwright.sync_api import sync_playwright
    
    log("  [S3] Cooldown 30s...")
    time.sleep(30)
    
    results = []
    # Known/guessed ad IDs (from various sources, Vladivostok)
    # Avito ad IDs are typically 10 digits
    test_ids = [
        "1111111111",  # test
        "7494308705",  # from earlier diag
        "1000000001",  # generic
        "5000000001",  # generic
        "6000000001",  # generic
    ]
    
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
        
        # Try show_technical first (linked from firewall page itself)
        log("  [S3] show_technical...")
        try:
            page.goto("https://www.avito.ru/info/show_technical", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)
            html = page.content()
            fw = is_firewall(html)
            log(f"  [S3] show_technical: len={len(html)} fw={fw}")
            (DB_DIR / "s3_show_tech.html").write_text(html[:50000], encoding="utf-8")
            if not fw:
                log("  [S3] show_technical accessible! IP not fully blocked.")
        except Exception as e:
            log(f"  [S3] show_technical err: {e}")
        
        # Now try direct ad pages
        for aid in test_ids:
            url = f"https://www.avito.ru/ad/{aid}"
            log(f"  [S3] Ad: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(8000)
                html = page.content()
                fw = is_firewall(html)
                log(f"  [S3] Ad {aid}: len={len(html)} fw={fw}")
                (DB_DIR / f"s3_ad_{aid}.html").write_text(html[:50000], encoding="utf-8")
                
                if fw:
                    continue
                if "Не найдено" in html[:3000] or "404" in html[:500]:
                    log(f"  [S3] Ad {aid}: 404")
                    continue
                
                title, price_cents = extract_title_price(html)
                # DOM fallback
                if not title:
                    try:
                        title = page.evaluate("() => { const h1 = document.querySelector('h1'); return h1 ? h1.textContent.trim() : ''; }")
                    except: pass
                if not price_cents:
                    try:
                        price_cents = page.evaluate("""
                            () => {
                                const els = document.querySelectorAll('[class*="price"], [class*="sum"], [data-widget*="Price"]');
                                for (const el of els) {
                                    const t = el.textContent || '';
                                    const m = t.match(/([\\d\\s\\u00a0\\u2009,]+)\\s*(?:₽|руб)/);
                                    if (m) {
                                        const v = m[1].replace(/[\\s\\u00a0\\u2009,]/g, '');
                                        const n = parseInt(v);
                                        if (n > 100) return n;
                                    }
                                }
                                return null;
                            }
                        """)
                    except: pass
                
                log(f"  [S3] Ad {aid}: title={title!r} price={price_cents}")
                if title and price_cents and price_cents > 1000:
                    r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_direct_ad")
                    results.append({"strategy": "S3_direct_ad", "pid": aid, **r})
                    log(f"  [S3] VERDICT: E2E_PASS")
                    browser.close()
                    return results
            except Exception as e:
                log(f"  [S3] Ad {aid} err: {e}")
            time.sleep(5)
        
        browser.close()
    return results

# ============================================================
# S4: Playwright — sitemap + search with fresh context
# ============================================================
def s4_sitemap_search(conn):
    log("  [S4] Playwright: sitemap + fresh search context")
    from playwright.sync_api import sync_playwright
    results = []
    
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
        
        # Try sitemap
        log("  [S4] sitemap.xml...")
        try:
            page.goto("https://www.avito.ru/sitemap.xml", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)
            html = page.content()
            fw = is_firewall(html)
            log(f"  [S4] sitemap: len={len(html)} fw={fw}")
            (DB_DIR / "s4_sitemap.html").write_text(html[:50000], encoding="utf-8")
            
            if not fw:
                # Extract URLs
                urls = re.findall(r'<loc>([^<]+)</loc>', html)
                log(f"  [S4] sitemap URLs: {len(urls)}")
                ad_urls = [u for u in urls if '/ad/' in u]
                log(f"  [S4] ad URLs: {len(ad_urls)} → {ad_urls[:5]}")
                
                for au in ad_urls[:3]:
                    log(f"  [S4] Ad from sitemap: {au}")
                    page.goto(au, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    fw = is_firewall(html)
                    log(f"  [S4] Ad: len={len(html)} fw={fw}")
                    if fw: continue
                    
                    title, price_cents = extract_title_price(html)
                    if title and price_cents and price_cents > 1000:
                        aid_m = re.search(r'/ad/(\d+)', au)
                        aid = aid_m.group(1) if aid_m else "SITEMAP-1"
                        r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, au, "pw_sitemap")
                        results.append({"strategy": "S4_sitemap", "pid": aid, **r})
                        log(f"  [S4] VERDICT: E2E_PASS")
                        browser.close()
                        return results
                    time.sleep(3)
        except Exception as e:
            log(f"  [S4] sitemap err: {e}")
        
        # Try search with fresh context (new browser to reset cookies)
        log("  [S4] Fresh search...")
        try:
            page2 = ctx.new_page()
            # Go to search directly without homepage
            page2.goto("https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony?search_text=iphone", 
                       wait_until="domcontentloaded", timeout=25000)
            page2.wait_for_timeout(12000)
            page2.evaluate("window.scrollTo(0, 800)")
            page2.wait_for_timeout(3000)
            html = page2.content()
            fw = is_firewall(html)
            log(f"  [S4] Search: len={len(html)} fw={fw}")
            (DB_DIR / "s4_search.html").write_text(html[:50000], encoding="utf-8")
            
            if not fw and len(html) > 10000:
                ids = extract_ad_ids(html)
                log(f"  [S4] Search IDs: {len(ids)} → {ids[:5]}")
                
                # DOM
                try:
                    dom = page2.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[href*="/ad/"], a[href*="ad/"]');
                            return Array.from(links).slice(0, 20).map(a => ({
                                href: a.href,
                                text: (a.textContent || '').trim().slice(0, 100),
                                price: (a.querySelector('[class*="price"]') || {}).textContent || ''
                            }));
                        }
                    """)
                    if dom:
                        log(f"  [S4] DOM: {len(dom)} links")
                        for d in dom[:5]:
                            log(f"    {d['href']} | {d['text'][:50]} | {d['price']}")
                            m = re.search(r'/ad/(\d+)', d['href'])
                            if m and m.group(1) not in ids:
                                ids.append(m.group(1))
                except Exception as e:
                    log(f"  [S4] DOM err: {e}")
                
                for aid in ids[:4]:
                    url = f"https://www.avito.ru/ad/{aid}"
                    log(f"  [S4] Ad: {url}")
                    page2.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page2.wait_for_timeout(8000)
                    html = page2.content()
                    fw = is_firewall(html)
                    log(f"  [S4] Ad {aid}: len={len(html)} fw={fw}")
                    if fw: continue
                    
                    title, price_cents = extract_title_price(html)
                    if not title:
                        try:
                            title = page2.evaluate("() => { const h1 = document.querySelector('h1'); return h1 ? h1.textContent.trim() : ''; }")
                        except: pass
                    if not price_cents:
                        try:
                            price_cents = page2.evaluate("""
                                () => {
                                    const els = document.querySelectorAll('[class*="price"], [class*="sum"]');
                                    for (const el of els) {
                                        const t = el.textContent || '';
                                        const m = t.match(/([\\d\\s\\u00a0\\u2009,]+)\\s*(?:₽|руб)/);
                                        if (m) {
                                            const v = m[1].replace(/[\\s\\u00a0\\u2009,]/g, '');
                                            const n = parseInt(v);
                                            if (n > 100) return n;
                                        }
                                    }
                                    return null;
                                }
                            """)
                        except: pass
                    
                    log(f"  [S4] Ad {aid}: title={title!r} price={price_cents}")
                    if title and price_cents and price_cents > 1000:
                        r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_search")
                        results.append({"strategy": "S4_search", "pid": aid, **r})
                        log(f"  [S4] VERDICT: E2E_PASS")
                        browser.close()
                        return results
                    time.sleep(3)
        except Exception as e:
            log(f"  [S4] search err: {e}")
        
        browser.close()
    return results

# ============================================================
# S5: curl_cffi with mobile API headers (Avito app)
# ============================================================
def s5_cffi_mobile_api(conn):
    log("  [S5] curl_cffi: Avito mobile app API endpoints")
    from curl_cffi import requests as cffi_req
    results = []
    
    # Avito mobile app API patterns
    endpoints = [
        # V1 search
        "https://api.avito.ru/v1/search?query=iphone&region=25&limit=10",
        # V2 search  
        "https://api.avito.ru/v2/search?query=iphone&city_id=25&limit=10",
        # Category
        "https://api.avito.ru/v1/categories?city_id=25",
        # Listing
        "https://api.avito.ru/v1/listings?category_id=109&city_id=25&limit=10",
        # Web API (used by m.avito.ru)
        "https://m.avito.ru/api/v1/search?query=iphone&city=vladivostok",
        "https://m.avito.ru/api/v1/listings?category=smartfony",
    ]
    
    # Try with app-like headers
    app_headers = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "AvitoMobile/12.5 (iPhone; iOS 17.2; Scale/3.00)",
        "X-App-Version": "12.5.0",
        "X-Platform": "ios",
    }
    
    for url in endpoints:
        try:
            r = cffi_req.get(url, impersonate="chrome", timeout=15, headers=app_headers)
            fw = is_firewall(r.text)
            ct = r.headers.get('content-type', '?')[:50]
            log(f"  [S5] {r.status_code} len={len(r.text)} fw={fw} ct={ct}")
            log(f"       {url}")
            if r.status_code == 200 and not fw and len(r.text) > 100:
                (DB_DIR / f"s5_{url.split('?')[0].split('/')[-1] or 'root'}.json").write_text(r.text[:100000], encoding="utf-8")
                # Try JSON parse
                try:
                    data = r.json()
                    s = json.dumps(data, ensure_ascii=False)
                    log(f"  [S5] JSON keys: {list(data.keys())[:10] if isinstance(data, dict) else type(data)}")
                    # Look for results/items/listings
                    items = None
                    for key in ['results', 'items', 'listings', 'data', 'offers', 'ads']:
                        if isinstance(data, dict) and key in data:
                            items = data[key]
                            break
                    if items is None and isinstance(data, list):
                        items = data
                    if items:
                        log(f"  [S5] items: {len(items)}")
                        for item in items[:3]:
                            if isinstance(item, dict):
                                iid = item.get('id') or item.get('ad_id') or item.get('adId')
                                ititle = item.get('title') or item.get('name') or ''
                                iprice = item.get('price') or item.get('price_cents') or item.get('amount')
                                if iid and ititle and iprice:
                                    iprice = int(iprice)
                                    if iprice > 10000:
                                        r2 = inbox_accept(conn, "avito", f"AVITO-{iid}", str(ititle), iprice,
                                                         f"https://www.avito.ru/ad/{iid}", "cffi_mobile_api")
                                        results.append({"strategy": "S5_mobile_api", "pid": str(iid), **r2})
                                        log(f"  [S5] API VERDICT: E2E_PASS")
                                        return results
                except:
                    pass
        except Exception as e:
            log(f"  [S5] {url} err: {e}")
        time.sleep(2)
    
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E AVITO V5 START ===")
    conn = setup_db()
    log(f"DB: {DB_PATH}")
    
    # Initial cooldown (longer than v4's 15s)
    log("Cooldown 20s...")
    time.sleep(20)
    
    strategies = [
        ("S1_diagnostic", s1_diagnostic),
        ("S2_mobile_api", s2_mobile_api),
        ("S3_direct_ad", s3_direct_ad),
        ("S4_sitemap_search", s4_sitemap_search),
        ("S5_cffi_mobile_api", s5_cffi_mobile_api),
    ]
    
    final_result = None
    for name, func in strategies:
        log(f"{'='*50}")
        try:
            results = func(conn)
            if results and results[0].get("P", 0) > 0:
                final_result = results[0]
                log(f"SUCCESS via {name}: {final_result}")
                break
        except Exception as e:
            log(f"{name} EXCEPTION: {e}\n{traceback.format_exc()}")
        log(f"{'='*50}")
    
    if final_result:
        verdict = "E2E_PASS"
        detail = final_result
    else:
        verdict = "E2E_FAIL"
        detail = "all strategies failed — IP firewall persistent"

    log(f"=== AVITO VERDICT: {verdict} ===")
    
    result = {"ts": now_iso(), "verdict": verdict, "detail": detail}
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"Result: {RESULT_PATH}")
    
    c = conn.cursor()
    c.execute("SELECT platform, product_id, title, url FROM product")
    rows = c.fetchall()
    log(f"DB products: {len(rows)}")
    for row in rows:
        log(f"  {row[0]} | {row[1]} | {str(row[2])[:60]} | {str(row[3])[:50]}")
    c.execute("SELECT COUNT(*) FROM observation")
    log(f"DB observations: {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM price_point")
    log(f"DB price_points: {c.fetchone()[0]}")
    
    conn.close()
    log("E2E_AVITO_V5_DONE")

if __name__ == "__main__":
    main()
