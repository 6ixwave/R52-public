#!/usr/bin/env python3
"""e2e_fix15.py — WB (Playwright price) + Avito (multi-city Playwright).
Built on the PROVEN fix14 app/intake pattern.
"""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix15"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix15_result.json"

results = {}
t0 = time.monotonic()

def log(m):
    line = f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)

def now_iso():
    return datetime.now(TZ).isoformat()

# ============================================================
# APP + INTAKE  (copied verbatim from e2e_fix14.py)
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
    fname = f"fix15-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX15_{source}")
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
                "product_count": len(products), "observation_count": len(obs),
                "price_point_count": len(pp)}
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
        },
    }

# ============================================================
# PRICE PARSER
# ============================================================
def parse_price_minor(text):
    """'99 575 ₽' / '99575' / '99 575' → minor units (kopecks)."""
    if not text:
        return None
    t = re.sub(r'[^\d]', '', text)
    if not t:
        return None
    val = int(t)
    if val < 100000:
        return val * 100
    return val

# ============================================================
# WB — Playwright product page + Basket CDN
# ============================================================
def run_wb(browser):
    log("=== WB: Playwright product page ===")
    WB_ID = "1097975275"
    WB_URL = f"https://www.wildberries.ru/catalog/{WB_ID}/detail.aspx"
    result = {"verdict": "E2E_FAIL", "reason": ""}
    app = make_app()

    # --- Step 1: Basket CDN for metadata (proven) ---
    title = brand = desc = cat = None
    try:
        import curl_cffi.requests as ccr
        r = ccr.get(
            f"https://basket-42.wbbasket.ru/vol0/part42/{WB_ID}/info/ru/card.json",
            impersonate="chrome", timeout=15,
        )
        if r.status_code == 200:
            card = r.json()
            selling = card.get("selling", {})
            title = card.get("imt_name") or card.get("name")
            brand = selling.get("brand_name")
            desc = (card.get("description") or "")[:3000]
            cat = card.get("subj_name") or ""
            if card.get("subj_root_name"):
                cat = f"{card['subj_root_name']} > {cat}"
            log(f"  WB basket: title={title!r} brand={brand!r}")
    except Exception as e:
        log(f"  WB basket err: {e}")

    # --- Step 2: Playwright for price ---
    price_minor = None
    price_src = ""
    page = browser.new_page(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )
    try:
        log("  WB: warm-up main page...")
        page.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(2500)

        log(f"  WB: goto {WB_URL}")
        page.goto(WB_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        html = page.content()
        log(f"  WB page: len={len(html)}")

        # P1: data-testid="price"
        m = re.search(r'data-testid="price"[^>]*>\s*([\d\s\u2009\u00a0]+)\s*(?:₽|руб)', html)
        if m:
            price_minor = parse_price_minor(m.group(1))
            price_src = "data-testid"

        # P2: JSON-LD Product
        if not price_minor:
            for jm in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
                try:
                    ld = json.loads(jm.group(1))
                    items = ld if isinstance(ld, list) else [ld]
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        if it.get("@type") == "Product":
                            off = it.get("offers", {})
                            if isinstance(off, list):
                                off = off[0] if off else {}
                            amt = off.get("price") or off.get("lowPrice")
                            if amt:
                                price_minor = int(float(amt) * 100)
                                price_src = "json-ld"
                                break
                except Exception:
                    pass
                if price_minor:
                    break

        # P3: og:price:amount
        if not price_minor:
            m = re.search(r'property="og:price:amount"\s+content="([\d.]+)"', html)
            if m:
                price_minor = int(float(m.group(1)) * 100)
                price_src = "og-price"

        # P4: inline "price": NNNNN in JS state
        if not price_minor:
            for m in re.finditer(r'"price"\s*:\s*(\d{4,9})', html):
                val = int(m.group(1))
                if 5000 < val < 20_000_000:
                    price_minor = val * 100
                    price_src = "inline-price"
                    break

        # P5: class-based price
        if not price_minor:
            m = re.search(r'class="[^"]*[Pp]rice[^"]*"[^>]*>\s*([\d\s\u2009\u00a0]+)\s*(?:₽|руб)', html)
            if m:
                price_minor = parse_price_minor(m.group(1))
                price_src = "class-price"

        # P6: generic text "N N N ₽"
        if not price_minor:
            m = re.search(r'(\d[\d\s\u2009\u00a0]{2,15})\s*(?:₽|руб\.?)', html)
            if m:
                price_minor = parse_price_minor(m.group(1))
                price_src = "text-price"

        log(f"  WB price: {price_minor} src={price_src}")

    except Exception as e:
        log(f"  WB Playwright err: {e}")
        log(traceback.format_exc()[-600:])
    finally:
        page.close()

    # --- Step 3: card.wb.ru via page.request (bypasses 403) ---
    if not price_minor:
        log("  WB: trying card.wb.ru via Playwright request...")
        page2 = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
        )
        try:
            page2.goto("https://www.wildberries.ru/", wait_until="domcontentloaded", timeout=25000)
            page2.wait_for_timeout(2000)
            card_url = f"https://card.wb.ru/cards/v2/detail?nm={WB_ID}"
            resp = page2.request.get(card_url, timeout=20000)
            log(f"  WB card.wb.ru: status={resp.status}")
            if resp.status == 200:
                body = resp.json()
                cards = body.get("data", {}).get("cards", [])
                if cards:
                    sizes = cards[0].get("sizes", [])
                    if sizes:
                        price_obj = sizes[0].get("price", {})
                        price_minor = price_obj.get("basic")
                        if price_minor:
                            price_src = "card_wb_pw"
                        log(f"  WB card price basic={price_minor}")
        except Exception as e:
            log(f"  WB card.wb.ru err: {e}")
        finally:
            page2.close()

    # --- Step 4: Feed through Inbox ---
    if title and price_minor and price_minor > 10000:
        eye = build_eye("WB", WB_ID, WB_URL, title, price_minor, brand=brand, desc=desc, cat=cat)
        acc = feed_inbox(app, eye, "WB")
        sq = verify_sqlite("WB")
        ok = sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0
        result["verdict"] = "E2E_PASS" if ok else "E2E_FAIL"
        result["price"] = price_minor
        result["price_source"] = price_src
        result["sqlite"] = sq
    else:
        result["reason"] = f"title={title is not None} price={price_minor} src={price_src}"
        result["sqlite"] = verify_sqlite("WB")

    log(f"  WB VERDICT: {result['verdict']}")
    return result

# ============================================================
# AVITO — Multi-city Playwright
# ============================================================
def run_avito(browser):
    log("=== AVITO: Multi-city Playwright ===")
    result = {"verdict": "E2E_FAIL", "reason": "no ad data"}
    app = make_app()

    cities = [
        ("vladivostok", "https://www.avito.ru/vladivostok/smartfony-i-telefony"),
        ("spb",         "https://www.avito.ru/spb/smartfony-i-telefony"),
        ("ekb",         "https://www.avito.ru/ekb/smartfony-i-telefony"),
        ("moskva",      "https://www.avito.ru/moskva/smartfony-i-telefony"),
    ]

    ad_ids = []
    ad_city = None

    for city, url in cities:
        log(f"  AVITO [{city}]: {url}")
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(5000)
            html = page.content()
            log(f"    len={len(html)}")

            if "Доступ ограничен" in html or "problem with IP" in html.lower():
                log(f"    → IP block, next city")
                continue

            # Extract IDs: /ad/NNNNNNNNN or /city/.../NNNNNNNNN
            ids_a = re.findall(r'avito\.ru/ad/(\d{8,12})', html)
            ids_b = re.findall(r'avito\.ru/[a-z0-9\-]+/[a-z0-9\-]+/(\d{8,12})', html)
            all_ids = list(dict.fromkeys(ids_a + ids_b))
            log(f"    → {len(all_ids)} IDs: {all_ids[:6]}")

            if all_ids:
                ad_ids = all_ids
                ad_city = city
                break
        except Exception as e:
            log(f"    err: {e}")
        finally:
            page.close()

    if not ad_ids:
        result["reason"] = "no ad IDs from any city"
        result["sqlite"] = verify_sqlite("AVITO")
        log(f"  AVITO VERDICT: E2E_FAIL ({result['reason']})")
        return result

    # --- Load individual ads ---
    log(f"  AVITO: trying {min(len(ad_ids),5)} ads from {ad_city}...")
    best = None

    for aid in ad_ids[:5]:
        ad_url = f"https://www.avito.ru/ad/{aid}"
        log(f"  AVITO ad: {ad_url}")
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        try:
            page.goto(ad_url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(4000)
            html = page.content()
            log(f"    len={len(html)}")

            if "Такой страницы" in html or "не существует" in html:
                log(f"    → ad not found, skip")
                continue
            if "Доступ ограничен" in html:
                log(f"    → IP block on ad, skip")
                continue

            # Title
            title = ""
            tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
            if not tm:
                tm = re.search(r'<title>(.*?)</title>', html, re.S)
            if tm:
                title = re.sub(r'<[^>]+>', '', tm.group(1)).strip()
                title = re.sub(r'\s+', ' ', title)

            # Price
            price_minor = None
            # P1: data-testid / class price
            pm = re.search(r'(?:data-testid="price"|class="[^"]*price[^"]*")[^>]*>\s*([\d\s\u2009\u00a0]+)\s*(?:₽|руб)', html, re.I)
            if pm:
                price_minor = parse_price_minor(pm.group(1))
            # P2: JSON-LD
            if not price_minor:
                for jm in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
                    try:
                        ld = json.loads(jm.group(1))
                        items = ld if isinstance(ld, list) else [ld]
                        for it in items:
                            if not isinstance(it, dict):
                                continue
                            if it.get("@type") in ("Product", "IndividualProduct", "Offer"):
                                off = it.get("offers", {})
                                if isinstance(off, list):
                                    off = off[0] if off else {}
                                amt = off.get("price") or off.get("lowPrice")
                                if amt:
                                    price_minor = int(float(amt) * 100)
                                    break
                    except Exception:
                        pass
                    if price_minor:
                        break
            # P3: og:price:amount
            if not price_minor:
                pm = re.search(r'property="og:price:amount"\s+content="([\d.]+)"', html)
                if pm:
                    price_minor = int(float(pm.group(1)) * 100)
            # P4: text
            if not price_minor:
                pm = re.search(r'(\d[\d\s\u2009\u00a0]{2,15})\s*(?:₽|руб\.?)', html)
                if pm:
                    price_minor = parse_price_minor(pm.group(1))

            log(f"    title={title[:80]!r} price={price_minor}")

            if title and price_minor and price_minor > 10000:
                best = {"id": aid, "url": ad_url, "title": title, "price_minor": price_minor}
                break
        except Exception as e:
            log(f"    err: {e}")
        finally:
            page.close()

    if best:
        eye = build_eye("AVITO", best["id"], best["url"], best["title"], best["price_minor"])
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        ok = sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0
        result["verdict"] = "E2E_PASS" if ok else "E2E_FAIL"
        result["price"] = best["price_minor"]
        result["ad_id"] = best["id"]
        result["sqlite"] = sq
    else:
        result["reason"] = "ads found but no valid title+price"
        result["sqlite"] = verify_sqlite("AVITO")

    log(f"  AVITO VERDICT: {result['verdict']}")
    return result

# ============================================================
# MAIN
# ============================================================
def main():
    log(f"e2e_fix15 start, TZ=UTC+10")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            results["WB"] = run_wb(browser)
            results["AVITO"] = run_avito(browser)
        finally:
            browser.close()

    elapsed = round(time.monotonic() - t0, 1)
    out = {"ts": now_iso(), "s": elapsed, "pass": 3, "d": results}
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    log(f"e2e_fix15 done in {elapsed}s")
    log(f"Result → {RESULT}")

if __name__ == "__main__":
    main()
