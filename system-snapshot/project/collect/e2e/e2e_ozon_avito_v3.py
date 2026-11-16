#!/usr/bin/env python3
"""e2e_ozon_avito_v3.py — Fix conn scope + Avito DOM extraction."""
import json, os, re, sys, time, sqlite3, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")
DB_DIR = ROOT / "e2e-ozon-avito-v3"
DB_PATH = DB_DIR / "r52.sqlite3"
RESULT_PATH = ROOT / "e2e_ozon_avito_v3_result.json"
LOG_PATH = ROOT / "e2e_ozon_avito_v3_stdout.txt"

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
        VALUES (?, ?, ?, 'active', 'e2e_ozon_avito_v3')
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

# ============================================================
# OZON — Playwright desktop (proven: gets 1.1MB page with title+price)
# ============================================================
def fetch_ozon(conn):
    log("OZON: starting (Playwright desktop)...")
    results = []
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

            # Warm-up
            log("  O1: warm-up...")
            page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            log(f"  O1 warm-up: len={len(page.content())}")

            # Product IDs (2984707012 proven to work in v2)
            product_ids = ["2984707012", "1000000001"]

            for pid in product_ids:
                url = f"https://www.ozon.ru/product/{pid}/"
                log(f"  O1: goto {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"  O1 prod {pid}: len={len(html)}")

                    if len(html) < 5000:
                        log(f"  O1 prod {pid}: STUB, skip")
                        continue

                    # Title
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{10,200})</h1>', html)
                    if m:
                        title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m:
                            title = m.group(1).strip()

                    # Price
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
                    # DOM price patterns
                    if not price_cents:
                        m = re.search(r'"price"\s*:\s*(\d{5,})', html)
                        if m:
                            price_cents = int(m.group(1))
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,\.]+)\s*(?:₽|руб)', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except:
                                pass

                    log(f"  O1 prod {pid}: title={title!r} price={price_cents}")

                    if title and price_cents and price_cents > 10000:
                        r = inbox_accept(conn, "ozon", f"OZON-{pid}", title, price_cents, url, "pw_desktop_dom", brand=None)
                        results.append({"strategy": "O1_pw_desktop", "pid": pid, **r})
                        log(f"  O1 VERDICT: E2E_PASS | P={r['P']} O={r['O']} PP={r['PP']}")
                        browser.close()
                        return results

                except Exception as e:
                    log(f"  O1 prod {pid} err: {e}")

            browser.close()
    except Exception as e:
        log(f"  O1 EXCEPTION: {e}\n{traceback.format_exc()}")

    log("OZON: ALL STRATEGIES FAILED")
    return results

# ============================================================
# AVITO — Playwright + DOM evaluate + HTML save for diagnosis
# ============================================================
def fetch_avito(conn):
    log("AVITO: starting...")
    results = []
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

            # Warm-up
            log("  A1: warm-up avito.ru...")
            page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            home_html = page.content()
            cookies = ctx.cookies()
            log(f"  A1 warm-up: len={len(home_html)} cookies={len(cookies)}")

            # Category: try multiple
            categories = [
                "https://www.avito.ru/vladivostok/tekhnika_i_svyaz/smartfony",
                "https://www.avito.ru/vladivostok/nedvizhimost/kvartiry/sdam",
                "https://www.avito.ru/vladivostok/avtomobili/legkovye",
            ]

            real_ad_ids = []
            saved_html = False

            for ci, cat_url in enumerate(categories):
                log(f"  A1: category[{ci}] {cat_url}")
                try:
                    page.goto(cat_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(12000)  # longer wait for JS
                    # Scroll to trigger lazy loading
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(3000)
                    html = page.content()
                    log(f"  A1 cat[{ci}]: len={len(html)}")

                    # Save first category HTML for diagnosis
                    if not saved_html and len(html) > 5000:
                        html_path = ROOT / "e2e_avito_v3_cat.html"
                        html_path.write_text(html, encoding="utf-8")
                        log(f"  A1: saved HTML → {html_path.name}")
                        saved_html = True

                    if len(html) < 5000:
                        continue

                    # --- Multiple patterns for ad IDs ---
                    # Pattern 1: /ad/1234567890
                    ids1 = re.findall(r'/ad/(\d{9,12})', html)
                    # Pattern 2: data-adid="123"
                    ids2 = re.findall(r'data-adid["\s:=]+["\']?(\d{9,12})', html)
                    # Pattern 3: data-id="123"
                    ids3 = re.findall(r'data-id["\s:=]+["\']?(\d{9,12})', html)
                    # Pattern 4: any href with /ad/
                    ids4 = re.findall(r'href="[^"]*/ad/(\d+)', html)
                    # Pattern 5: JSON embedded
                    ids5 = re.findall(r'"(?:ad_id|adId|id)"\s*:\s*"?(\d{9,12})"?', html)

                    all_ids = list(dict.fromkeys(ids1 + ids2 + ids3 + ids4 + ids5))
                    log(f"  A1 cat[{ci}] ads: p1={len(ids1)} p2={len(ids2)} p3={len(ids3)} p4={len(ids4)} p5={len(ids5)} total={len(all_ids)}")
                    if all_ids:
                        log(f"  A1 first 5: {all_ids[:5]}")
                    real_ad_ids.extend(all_ids[:5])

                    # --- Try DOM evaluate for links ---
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
                            log(f"  A1 DOM links: {len(dom_links)}")
                            for dl in dom_links[:5]:
                                log(f"    {dl['href']} | {dl['text'][:50]}")
                            for dl in dom_links:
                                m = re.search(r'/ad/(\d+)', dl['href'])
                                if m:
                                    aid = m.group(1)
                                    if aid not in real_ad_ids:
                                        real_ad_ids.append(aid)
                    except Exception as e:
                        log(f"  A1 DOM eval err: {e}")

                    # --- Try embedded JSON state ---
                    try:
                        state = page.evaluate("""
                            () => {
                                if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__).slice(0, 5000);
                                if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__).slice(0, 5000);
                                if (window.__APP_DATA__) return JSON.stringify(window.__APP_DATA__).slice(0, 5000);
                                // Try script tags with JSON
                                const scripts = document.querySelectorAll('script:not([src])');
                                for (const s of scripts) {
                                    const t = s.textContent || '';
                                    if (t.includes('"ads"') || t.includes('"items"') || t.includes('"offers"')) {
                                        return t.slice(0, 5000);
                                    }
                                }
                                return '';
                            }
                        """)
                        if state and len(state) > 100:
                            log(f"  A1 state: len={len(state)} preview={state[:200]}")
                            state_ids = re.findall(r'(\d{9,12})', state)
                            if state_ids:
                                log(f"  A1 state IDs: {state_ids[:5]}")
                                for sid in state_ids[:5]:
                                    if sid not in real_ad_ids:
                                        real_ad_ids.append(sid)
                    except Exception as e:
                        log(f"  A1 state eval err: {e}")

                except Exception as e:
                    log(f"  A1 cat[{ci}] err: {e}")
                time.sleep(3)

            log(f"  A1 total real_ad_ids: {len(real_ad_ids)} → {real_ad_ids[:8]}")

            # Visit individual ads
            for aid in real_ad_ids[:5]:
                url = f"https://www.avito.ru/ad/{aid}"
                log(f"  A1: ad {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    log(f"  A1 ad {aid}: len={len(html)}")

                    if len(html) < 5000:
                        continue

                    # Check 404
                    if "Не найдено" in html[:3000] or "404" in html[:500]:
                        log(f"  A1 ad {aid}: 404, skip")
                        continue

                    # Title
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
                    if m:
                        title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m:
                            title = m.group(1).strip()
                    if not title:
                        # Try DOM
                        try:
                            title = page.evaluate("() => { const h1 = document.querySelector('h1'); return h1 ? h1.textContent.trim() : ''; }")
                        except:
                            pass

                    # Price
                    price_cents = None
                    # JSON-LD
                    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                        try:
                            ld = json.loads(m.group(1))
                            if isinstance(ld, dict) and 'offers' in ld:
                                off = ld['offers']
                                if isinstance(off, list) and off:
                                    off = off[0]
                                p = off.get('price')
                                if p:
                                    price_cents = int(float(p) * 100)
                                    break
                        except:
                            pass
                    # DOM price
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\u2009\s,\.]+)\s*(?:₽|руб)', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except:
                                pass
                    if not price_cents:
                        m = re.search(r'"price"\s*:\s*"?(\d{5,})"?', html)
                        if m:
                            price_cents = int(m.group(1))
                    if not price_cents:
                        # DOM evaluate
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
                        except:
                            pass

                    log(f"  A1 ad {aid}: title={title!r} price={price_cents}")
                    if title and price_cents and price_cents > 1000:
                        r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_ad_page")
                        results.append({"strategy": "A1_ad_page", "pid": aid, **r})
                        log(f"  A1 VERDICT: E2E_PASS | P={r['P']} O={r['O']} PP={r['PP']}")
                        browser.close()
                        return results
                except Exception as e:
                    log(f"  A1 ad {aid} err: {e}")
                time.sleep(3)

            # If no ads found from categories, try direct known ad IDs
            # (from previous diag runs: 7494308705 was 404, try broader range)
            if not real_ad_ids:
                log("  A1: no ads from categories, trying search page...")
                try:
                    page.goto("https://www.avito.ru/vladivostok/search?query=iphone", wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(10000)
                    html = page.content()
                    log(f"  A1 search: len={len(html)}")
                    ids = re.findall(r'/ad/(\d{9,12})', html)
                    ids = list(dict.fromkeys(ids))
                    log(f"  A1 search ads: {ids[:5]}")
                    for aid in ids[:3]:
                        url = f"https://www.avito.ru/ad/{aid}"
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(8000)
                        html = page.content()
                        title = None
                        m = re.search(r'<h1[^>]*>([^<]{10,300})</h1>', html)
                        if m: title = m.group(1).strip()
                        if not title:
                            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                            if m: title = m.group(1).strip()
                        price_cents = None
                        m = re.search(r'(\d[\d\u00a0\u2009\s,\.]+)\s*(?:₽|руб)', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass
                        if title and price_cents:
                            r = inbox_accept(conn, "avito", f"AVITO-{aid}", title, price_cents, url, "pw_search_ad")
                            results.append({"strategy": "A1_search", "pid": aid, **r})
                            log(f"  A1 SEARCH VERDICT: E2E_PASS")
                            browser.close()
                            return results
                        time.sleep(2)
                except Exception as e:
                    log(f"  A1 search err: {e}")

            browser.close()
    except Exception as e:
        log(f"  A1 EXCEPTION: {e}\n{traceback.format_exc()}")

    log("AVITO: ALL STRATEGIES FAILED")
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E OZON+AVITO V3 START ===")
    conn = setup_db()
    log(f"DB: {DB_PATH}")

    final = {}

    # OZON
    ozon_results = fetch_ozon(conn)
    if ozon_results and ozon_results[0].get("P", 0) > 0:
        final["ozon"] = {"verdict": "E2E_PASS", "detail": ozon_results[0]}
    else:
        final["ozon"] = {"verdict": "E2E_FAIL", "detail": ozon_results}

    # AVITO
    avito_results = fetch_avito(conn)
    if avito_results and avito_results[0].get("P", 0) > 0:
        final["avito"] = {"verdict": "E2E_PASS", "detail": avito_results[0]}
    else:
        final["avito"] = {"verdict": "E2E_FAIL", "detail": avito_results}

    passes = sum(1 for v in final.values() if v["verdict"] == "E2E_PASS")
    log(f"=== SUMMARY: PASS={passes}/2 ===")
    for k, v in final.items():
        log(f"  {k.upper()}: {v['verdict']}")

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
    log("E2E_OZON_AVITO_V3_DONE")

if __name__ == "__main__":
    main()
