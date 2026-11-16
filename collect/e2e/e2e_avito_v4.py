#!/usr/bin/env python3
"""e2e_avito_v4.py — Avito E2E: beat the IP firewall.
Key finding: 36K page = 'Доступ ограничен: проблема с IP' (firewall + CAPTCHA).
Strategy: long warm-up, human-like delays, mobile, RSS, search, DOM extraction.
"""
import json, os, re, sys, time, sqlite3, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")
DB_DIR = ROOT / "e2e-avito-v4"
DB_PATH = DB_DIR / "r52.sqlite3"
RESULT_PATH = ROOT / "e2e_avito_v4_result.json"

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
        VALUES (?, ?, ?, 'active', 'e2e_avito_v4')
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
    """Detect Avito IP firewall page."""
    if len(html) < 20000:
        return False
    return ("Доступ ограничен" in html[:5000] or
            "проблема с IP" in html[:5000] or
            "firewall" in html[:3000].lower())

def extract_ad_ids(html):
    """Multiple patterns for Avito ad IDs."""
    ids = []
    ids += re.findall(r'/ad/(\d{9,12})', html)
    ids += re.findall(r'data-adid["\s:=]+["\']?(\d{9,12})', html)
    ids += re.findall(r'"(?:ad_id|adId)"\s*:\s*"?(\d{9,12})"?', html)
    ids += re.findall(r'href="[^"]*?/ad/(\d+)', html)
    return list(dict.fromkeys(ids))

def extract_title_price(html):
    """Extract title and price from Avito ad page HTML."""
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
            if "Avito" not in t and "Авито" not in t:
                title = t

    price_cents = None
    # JSON-LD
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
    # DOM patterns
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

# ============================================================
# STRATEGY 1: Playwright desktop — long warm-up + human behavior
# ============================================================
def strat_pw_desktop(conn):
    log("  [S1] Playwright desktop: long warm-up + category + ad")
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

        # Phase 1: Homepage warm-up (long)
        log("  [S1] Homepage warm-up...")
        page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)
        # Scroll to trigger lazy content
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(3000)
        page.evaluate("window.scrollTo(0, 1500)")
        page.wait_for_timeout(3000)
        home_html = page.content()
        fw = is_firewall(home_html)
        log(f"  [S1] Homepage: len={len(home_html)} firewall={fw}")
        (DB_DIR / "s1_home.html").write_text(home_html, encoding="utf-8")

        if fw:
            log("  [S1] Homepage is firewall — trying anyway (might be soft block)")

        # Phase 2: Category with long wait
        cat_urls = [
            "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
            "https://www.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
        ]
        real_ids = []
        for ci, cat_url in enumerate(cat_urls):
            log(f"  [S1] Category[{ci}]: {cat_url}")
            try:
                page.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(12000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(4000)
                html = page.content()
                fw = is_firewall(html)
                log(f"  [S1] Cat[{ci}]: len={len(html)} firewall={fw}")
                (DB_DIR / f"s1_cat{ci}.html").write_text(html, encoding="utf-8")

                if fw and len(html) < 40000:
                    log(f"  [S1] Cat[{ci}]: likely firewall, waiting 5s and retrying...")
                    page.wait_for_timeout(5000)
                    html = page.content()
                    fw = is_firewall(html)
                    log(f"  [S1] Cat[{ci}] retry: len={len(html)} firewall={fw}")

                ids = extract_ad_ids(html)
                log(f"  [S1] Cat[{ci}] IDs: {len(ids)} → {ids[:5]}")
                real_ids.extend(ids[:5])

                # DOM evaluate for links
                try:
                    dom_links = page.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[href*="/ad/"]');
                            return Array.from(links).slice(0, 20).map(a => ({
                                href: a.href,
                                text: (a.textContent || '').trim().slice(0, 100)
                            }));
                        }
                    """)
                    if dom_links:
                        log(f"  [S1] DOM links: {len(dom_links)}")
                        for dl in dom_links[:5]:
                            log(f"    {dl['href']} | {dl['text'][:60]}")
                        for dl in dom_links:
                            m = re.search(r'/ad/(\d+)', dl['href'])
                            if m and m.group(1) not in real_ids:
                                real_ids.append(m.group(1))
                except Exception as e:
                    log(f"  [S1] DOM eval err: {e}")
            except Exception as e:
                log(f"  [S1] Cat[{ci}] err: {e}")
            time.sleep(4)

        log(f"  [S1] Total real IDs: {len(real_ids)} → {real_ids[:8]}")

        # Phase 3: Visit ads
        for aid in real_ids[:5]:
            url = f"https://www.avito.ru/ad/{aid}"
            log(f"  [S1] Ad: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                html = page.content()
                fw = is_firewall(html)
                log(f"  [S1] Ad {aid}: len={len(html)} firewall={fw}")
                (DB_DIR / f"s1_ad_{aid}.html").write_text(html, encoding="utf-8")

                if fw:
                    continue
                if "Не найдено" in html[:3000]:
                    log(f"  [S1] Ad {aid}: 404")
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

                log(f"  [S1] Ad {aid}: title={title!r} price={price_cents}")
                if title and price_cents and price_cents > 1000:
                    r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_desktop")
                    results.append({"strategy": "S1_pw_desktop", "pid": aid, **r})
                    log(f"  [S1] VERDICT: E2E_PASS | P={r['P']} O={r['O']} PP={r['PP']}")
                    browser.close()
                    return results
            except Exception as e:
                log(f"  [S1] Ad {aid} err: {e}")
            time.sleep(4)

        browser.close()
    return results

# ============================================================
# STRATEGY 2: Playwright mobile
# ============================================================
def strat_pw_mobile(conn):
    log("  [S2] Playwright mobile...")
    from playwright.sync_api import sync_playwright
    results = []
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

        log("  [S2] Mobile homepage...")
        page.goto("https://m.avito.ru/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)
        home_html = page.content()
        fw = is_firewall(home_html)
        log(f"  [S2] Home: len={len(home_html)} firewall={fw}")
        (DB_DIR / "s2_home.html").write_text(home_html, encoding="utf-8")

        # Mobile categories
        cat_urls = [
            "https://m.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
            "https://m.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
        ]
        real_ids = []
        for ci, cat_url in enumerate(cat_urls):
            log(f"  [S2] Cat[{ci}]: {cat_url}")
            try:
                page.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(10000)
                html = page.content()
                fw = is_firewall(html)
                log(f"  [S2] Cat[{ci}]: len={len(html)} firewall={fw}")
                (DB_DIR / f"s2_cat{ci}.html").write_text(html, encoding="utf-8")

                ids = extract_ad_ids(html)
                log(f"  [S2] Cat[{ci}] IDs: {len(ids)} → {ids[:5]}")
                real_ids.extend(ids[:5])

                # DOM
                try:
                    dom_links = page.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[href*="/ad/"]');
                            return Array.from(links).slice(0, 15).map(a => a.href);
                        }
                    """)
                    if dom_links:
                        log(f"  [S2] DOM: {len(dom_links)} links")
                        for dl in dom_links[:5]:
                            m = re.search(r'/ad/(\d+)', dl)
                            if m and m.group(1) not in real_ids:
                                real_ids.append(m.group(1))
                except: pass
            except Exception as e:
                log(f"  [S2] Cat[{ci}] err: {e}")
            time.sleep(4)

        log(f"  [S2] Total IDs: {len(real_ids)} → {real_ids[:8]}")

        for aid in real_ids[:4]:
            url = f"https://m.avito.ru/ad/{aid}"
            log(f"  [S2] Ad: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                html = page.content()
                fw = is_firewall(html)
                log(f"  [S2] Ad {aid}: len={len(html)} firewall={fw}")
                (DB_DIR / f"s2_ad_{aid}.html").write_text(html, encoding="utf-8")
                if fw: continue

                title, price_cents = extract_title_price(html)
                if not title:
                    try:
                        title = page.evaluate("() => { const h1 = document.querySelector('h1'); return h1 ? h1.textContent.trim() : ''; }")
                    except: pass
                if not price_cents:
                    try:
                        price_cents = page.evaluate("""
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

                log(f"  [S2] Ad {aid}: title={title!r} price={price_cents}")
                if title and price_cents and price_cents > 1000:
                    r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_mobile")
                    results.append({"strategy": "S2_pw_mobile", "pid": aid, **r})
                    log(f"  [S2] VERDICT: E2E_PASS")
                    browser.close()
                    return results
            except Exception as e:
                log(f"  [S2] Ad {aid} err: {e}")
            time.sleep(3)

        browser.close()
    return results

# ============================================================
# STRATEGY 3: curl_cffi with different impersonation + RSS
# ============================================================
def strat_cffi_rss(conn):
    log("  [S3] curl_cffi + RSS feeds...")
    from curl_cffi import requests as cffi_req
    results = []

    # Try RSS feeds (various URL patterns)
    rss_urls = [
        "https://www.avito.ru/rss/vladivostok/tekhnika_i_svyaz/smartfony",
        "https://www.avito.ru/rss/vladivostok/nedvizhimost/kvartiry/sdam",
        "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony/rss",
        "https://www.avito.ru/rss?cat=smartfony&city=vladivostok",
        "https://m.avito.ru/rss/vladivostok/tekhnika_i_svyaz/smartfony",
    ]
    for ru in rss_urls:
        try:
            r = cffi_req.get(ru, impersonate="chrome", timeout=15,
                            headers={"Accept": "application/rss+xml, application/xml, text/html",
                                     "Accept-Language": "ru-RU,ru;q=0.9"})
            log(f"  [S3] RSS {r.status_code} len={len(r.text)} {ru}")
            if r.status_code == 200 and len(r.text) > 500:
                (DB_DIR / f"s3_rss_{ru.split('/')[-1]}.xml").write_text(r.text, encoding="utf-8")
                # Parse RSS items
                items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
                log(f"  [S3] RSS items: {len(items)}")
                for item in items[:3]:
                    tm = re.search(r'<title>([^<]+)</title>', item)
                    lm = re.search(r'<link>([^<]+)</link>', item)
                    pm = re.search(r'<price>(\d+)</price>', item)
                    if not pm:
                        pm = re.search(r'(\d[\d\s,]+)\s*(?:₽|руб)', item)
                    if tm and lm:
                        title = tm.group(1).strip()
                        url = lm.group(1).strip()
                        price_cents = None
                        if pm:
                            try:
                                raw = pm.group(1).replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                        if title and price_cents and price_cents > 1000:
                            aid_m = re.search(r'/ad/(\d+)', url)
                            aid = aid_m.group(1) if aid_m else "RSS-1"
                            r2 = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "rss_feed")
                            results.append({"strategy": "S3_rss", "pid": aid, **r2})
                            log(f"  [S3] RSS VERDICT: E2E_PASS {title[:50]}")
                            return results
        except Exception as e:
            log(f"  [S3] RSS err: {e}")
        time.sleep(2)

    # Try curl_cffi on category with different impersonation
    for imp in ["chrome", "firefox", "safari"]:
        try:
            r = cffi_req.get(
                "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
                impersonate=imp, timeout=20,
                headers={"Accept-Language": "ru-RU,ru;q=0.9",
                         "Accept": "text/html,application/xhtml+xml"}
            )
            fw = is_firewall(r.text)
            log(f"  [S3] cffi {imp}: {r.status_code} len={len(r.text)} fw={fw}")
            if r.status_code == 200 and not fw and len(r.text) > 10000:
                (DB_DIR / f"s3_cffi_{imp}.html").write_text(r.text, encoding="utf-8")
                ids = extract_ad_ids(r.text)
                log(f"  [S3] cffi {imp} IDs: {ids[:5]}")
                for aid in ids[:3]:
                    r2 = cffi_req.get(f"https://www.avito.ru/ad/{aid}", impersonate=imp,
                                     timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                    log(f"  [S3] Ad {aid}: {r2.status_code} len={len(r2.text)}")
                    if r2.status_code == 200 and len(r2.text) > 5000:
                        title, price_cents = extract_title_price(r2.text)
                        if title and price_cents and price_cents > 1000:
                            r3 = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents,
                                             f"https://www.avito.ru/ad/{aid}", f"cffi_{imp}")
                            results.append({"strategy": f"S3_cffi_{imp}", "pid": aid, **r3})
                            log(f"  [S3] CFI VERDICT: E2E_PASS")
                            return results
                    time.sleep(3)
        except Exception as e:
            log(f"  [S3] cffi {imp} err: {e}")
        time.sleep(3)

    return results

# ============================================================
# STRATEGY 4: Avito search page via Playwright (different entry)
# ============================================================
def strat_pw_search(conn):
    log("  [S4] Playwright search page...")
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

        # Go directly to search (skip homepage to save time)
        search_url = "https://www.avito.ru/vladivostok/search?query=iphone"
        log(f"  [S4] {search_url}")
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(12000)
            page.evaluate("window.scrollTo(0, 1000)")
            page.wait_for_timeout(3000)
            html = page.content()
            fw = is_firewall(html)
            log(f"  [S4] Search: len={len(html)} fw={fw}")
            (DB_DIR / "s4_search.html").write_text(html, encoding="utf-8")

            if not fw:
                ids = extract_ad_ids(html)
                log(f"  [S4] Search IDs: {len(ids)} → {ids[:5]}")

                # Also try DOM
                try:
                    dom = page.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[href*="/ad/"]');
                            return Array.from(links).slice(0, 15).map(a => ({
                                href: a.href,
                                text: (a.textContent || '').trim().slice(0, 80)
                            }));
                        }
                    """)
                    if dom:
                        log(f"  [S4] DOM: {len(dom)}")
                        for d in dom[:5]:
                            log(f"    {d['href']} | {d['text']}")
                            m = re.search(r'/ad/(\d+)', d['href'])
                            if m and m.group(1) not in ids:
                                ids.append(m.group(1))
                except: pass

                for aid in ids[:4]:
                    url = f"https://www.avito.ru/ad/{aid}"
                    log(f"  [S4] Ad: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    fw = is_firewall(html)
                    log(f"  [S4] Ad {aid}: len={len(html)} fw={fw}")
                    (DB_DIR / f"s4_ad_{aid}.html").write_text(html, encoding="utf-8")
                    if fw: continue

                    title, price_cents = extract_title_price(html)
                    if not title:
                        try:
                            title = page.evaluate("() => { const h1 = document.querySelector('h1'); return h1 ? h1.textContent.trim() : ''; }")
                        except: pass
                    if not price_cents:
                        try:
                            price_cents = page.evaluate("""
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
                        results.append({"strategy": "S4_pw_search", "pid": aid, **r})
                        log(f"  [S4] VERDICT: E2E_PASS")
                        browser.close()
                        return results
                    time.sleep(3)
        except Exception as e:
            log(f"  [S4] err: {e}")

        browser.close()
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E AVITO V4 START ===")
    conn = setup_db()
    log(f"DB: {DB_PATH}")

    # Wait 15s before starting (let rate limit cool down)
    log("Waiting 15s for rate-limit cooldown...")
    time.sleep(15)

    strategies = [
        ("S1_pw_desktop", strat_pw_desktop),
        ("S2_pw_mobile", strat_pw_mobile),
        ("S3_cffi_rss", strat_cffi_rss),
        ("S4_pw_search", strat_pw_search),
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

    # Summary
    if final_result:
        verdict = "E2E_PASS"
        detail = final_result
    else:
        verdict = "E2E_FAIL"
        detail = "all strategies failed"

    log(f"=== AVITO VERDICT: {verdict} ===")

    result = {"ts": now_iso(), "verdict": verdict, "detail": detail}
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"Result: {RESULT_PATH}")

    # DB verify
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
    log("E2E_AVITO_V4_DONE")

if __name__ == "__main__":
    main()
