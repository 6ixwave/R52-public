#!/usr/bin/env python3
"""e2e_fix7.py — Fix WB (card.json keys) + Avito (find real ad IDs)."""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix7"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix7_result.json"

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
    fname = f"fix7-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX7_{source}")
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
# WB — fix card.json key extraction
# ============================================================
def run_wb(app):
    log("=== WB: Basket CDN (fixed keys) ===")
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
            results["WB"] = {"verdict": "E2E_FAIL", "reason": "no card"}
            return

        log(f"  WB: basket={basket_host}")
        log(f"  WB: card keys = {list(card.keys())[:25]}")

        # WB card.json structure: imts=name, brand, description, options, sizes[].price.basic
        title = card.get("imts") or card.get("name") or card.get("title") or ""
        brand = card.get("brand") or ""
        desc = card.get("description") or card.get("desc") or ""
        # Category from options
        cat = ""
        if "options" in card:
            for opt in card["options"]:
                if opt.get("name") == "Категория":
                    cat = opt.get("value", "")
                    break
        # Price from sizes
        price_cents = None
        if "sizes" in card and card["sizes"]:
            sz = card["sizes"][0]
            price_obj = sz.get("price", {})
            price_cents = price_obj.get("basic") or price_obj.get("product") or 0
        # Fallback: price API
        if not price_cents:
            try:
                pr = s.get(f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
                           timeout=10, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                if pr.status_code == 200:
                    pd = pr.json()
                    prods = pd.get("data", {}).get("products", [])
                    if prods:
                        p = prods[0]
                        title = title or p.get("name", "")
                        brand = brand or p.get("brand", "")
                        if p.get("sizes"):
                            price_cents = p["sizes"][0].get("price", {}).get("basic", 0)
            except Exception as e:
                log(f"  WB price API err: {e}")

        log(f"  WB: title={title!r}")
        log(f"  WB: brand={brand!r} price={price_cents} cat={cat!r}")

        if title and price_cents:
            eye = build_eye("WB", str(nm_id), url, title, price_cents,
                            brand=brand or None, desc=desc or None, cat=cat or None)
            acc = feed_inbox(app, eye, "WB")
            sq = verify_sqlite("WB")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["WB"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None,
                             "strategy": "basket_cdn_fixed"}
            log(f"  WB VERDICT: {verdict}")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"),
                             "reason": f"title={bool(title)} price={price_cents}"}
            log(f"  WB VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "error": str(e)}
    return results.get("WB", {})

# ============================================================
# AVITO — find real ad IDs, then fetch
# ============================================================
def run_avito(app):
    log("=== AVITO: find real ads + fetch ===")
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    real_ad_ids = []

    # --- Step 1: Find real ad IDs ---
    log("  Step 1: Finding real Avito ad IDs...")

    # 1a: Try search page (less protected than home)
    search_urls = [
        "https://www.avito.ru/vladivostok/smartfony-i-telefony?q=iphone+17",
        "https://www.avito.ru/vladivostok/tech/communication/cellphones?q=iphone",
        "https://www.avito.ru/vladivostok?q=iphone+17",
    ]
    for su in search_urls:
        try:
            r = s.get(su, timeout=20, headers={
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            })
            log(f"  Search {su.split('?')[0].split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
            if r.status_code == 200 and len(r.text) > 5000:
                if "Доступ ограничен" in r.text:
                    log("  → IP block")
                    continue
                # Extract ad IDs: /ad/1234567890 or /ads/1234567890
                ids = re.findall(r'avito\.ru/(?:[^/]+/)*(?:ad|ads)/(\d{7,})', r.text)
                # Also try data attributes
                ids += re.findall(r'data-ad-id=["\']?(\d{7,})', r.text)
                # Deduplicate, keep order
                seen = set()
                unique_ids = []
                for i in ids:
                    if i not in seen:
                        seen.add(i)
                        unique_ids.append(i)
                log(f"  → found {len(unique_ids)} ad IDs: {unique_ids[:10]}")
                real_ad_ids.extend(unique_ids[:15])
                if real_ad_ids:
                    break
        except Exception as e:
            log(f"  Search err: {e}")

    # 1b: Try category listing (no search query)
    if not real_ad_ids:
        cat_urls = [
            "https://www.avito.ru/vladivostok/smartfony-i-telefony",
            "https://www.avito.ru/vladivostok/telefony-i-smartfony",
        ]
        for cu in cat_urls:
            try:
                r = s.get(cu, timeout=20, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                log(f"  Cat {cu.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 5000 and "Доступ ограничен" not in r.text:
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)*(?:ad|ads)/(\d{7,})', r.text)
                    seen = set()
                    for i in ids:
                        if i not in seen:
                            seen.add(i)
                            real_ad_ids.append(i)
                    log(f"  → {len(real_ad_ids)} IDs from category")
                    if real_ad_ids:
                        break
            except Exception as e:
                log(f"  Cat err: {e}")

    # 1c: Try RSS (if available)
    if not real_ad_ids:
        try:
            r = s.get("https://www.avito.ru/rss/vladivostok/smartfony-i-telefony",
                      timeout=15, headers={"Accept": "application/rss+xml"})
            log(f"  RSS: HTTP {r.status_code} len={len(r.text)}")
            if r.status_code == 200:
                ids = re.findall(r'avito\.ru/(?:[^/]+/)*(?:ad|ads)/(\d{7,})', r.text)
                real_ad_ids = ids[:10]
                log(f"  → {len(real_ad_ids)} IDs from RSS")
        except Exception as e:
            log(f"  RSS err: {e}")

    # 1d: Hardcoded fallback IDs (known active patterns — 10-digit, recent)
    if not real_ad_ids:
        log("  Fallback: trying known ID patterns...")
        # Avito IDs are 10-digit, incrementing. Try a range around current era.
        # Based on the era (2026), IDs likely in 11-13 billion range
        real_ad_ids = [
            "11234567890", "11345678901", "11456789012",
            "11567890123", "11678901234",
        ]
        log(f"  Using fallback IDs: {real_ad_ids}")

    log(f"  Total candidate IDs: {len(real_ad_ids)}")

    # --- Step 2: Fetch a real ad ---
    eye = None
    # Try Playwright first (better for Avito)
    log("  Step 2: Playwright fetch...")
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

            # Warm-up on a less-protected page
            try:
                page.goto("https://www.avito.ru/vladivostok", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(6000)
                log(f"  PW warm-up: len={len(page.content())}")
            except:
                pass

            for aid in real_ad_ids[:8]:
                url = f"https://www.avito.ru/ad/{aid}"
                log(f"  PW ad={aid}...")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(8000)
                    html = page.content()
                    log(f"  PW ad={aid}: len={len(html)}")

                    if "Доступ ограничен" in html:
                        log(f"  → IP block")
                        continue
                    if "Такой страницы" in html or "не существует" in html:
                        log(f"  → 404 / not found")
                        continue
                    if len(html) < 5000:
                        log(f"  → stub")
                        continue

                    # Parse title
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', html)
                    if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
                        if m: title = m.group(1).replace(" - Авито", "").replace("Авито - ", "").strip()

                    # Parse price
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
                    # DOM
                    if not price_cents:
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m: price_cents = int(m.group(1))
                    if not price_cents:
                        m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', html, re.I)
                        if m: price_cents = int(float(m.group(1)) * 100)
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]+)\s*(?:₽|руб)', html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass

                    log(f"  PW ad={aid}: title={title!r} price={price_cents}")
                    if title and price_cents and price_cents > 500:
                        eye = build_eye("AVITO", aid, url, title, price_cents)
                        log(f"  AVITO PW: SUCCESS ad={aid}")
                        break
                except Exception as e:
                    log(f"  PW ad={aid} err: {e}")
            browser.close()
    except Exception as e:
        log(f"  PW exception: {e}")

    # Fallback: curl_cffi on real ad
    if not eye and real_ad_ids:
        log("  Step 2b: curl_cffi on real ad...")
        for aid in real_ad_ids[:5]:
            url = f"https://www.avito.ru/ad/{aid}"
            try:
                r = s.get(url, timeout=20, headers={
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Accept": "text/html",
                })
                log(f"  CC ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 3000 and "Доступ ограничен" not in r.text and "не существует" not in r.text:
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
                        eye = build_eye("AVITO", aid, url, title, price_cents)
                        log(f"  AVITO CC: SUCCESS ad={aid}")
                        break
            except Exception as e:
                log(f"  CC ad={aid} err: {e}")

    # Feed to intake
    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq, "inbox": acc if isinstance(acc, dict) else None,
                            "ad_ids_tried": real_ad_ids[:10]}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"), "inbox": None,
                            "ad_ids_tried": real_ad_ids[:10], "reason": "no ad fetched"}
        log("  AVITO VERDICT: E2E_FAIL")
    return results.get("AVITO", {})

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E FIX7 START ===")
    app = make_app()
    log("App ready")

    run_wb(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"=== E2E FIX7 DONE in {elapsed:.1f}s ===")
    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
