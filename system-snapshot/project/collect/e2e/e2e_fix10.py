#!/usr/bin/env python3
"""e2e_fix10.py — WB: fix selling price (exclude supplier_id) + API. Avito: web IDs + direct ad."""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix10"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix10_result.json"

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
    fname = f"fix10-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX10_{source}")
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
# WB — fix: exclude supplier_id/no_return_map from selling, try price API
# ============================================================
def run_wb(app):
    log("=== WB: selling (excl supplier_id) + price API ===")
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

        # --- TITLE ---
        title = card.get("imt_name") or ""
        log(f"  WB: title={title!r}")

        # --- BRAND ---
        selling = card.get("selling", {})
        brand = ""
        if isinstance(selling, dict):
            brand = selling.get("brand_name") or selling.get("brand") or ""
        log(f"  WB: brand={brand!r}")

        # --- PRICE: card.json (exclude non-price keys) ---
        price_cents = None
        price_source = ""

        # selling: only price-like keys (NOT supplier_id, no_return_map, brand_hash)
        SELLING_PRICE_KEYS = {"price", "priceU", "priceD", "oldPrice", "old_price",
                              "basic", "product", "card", "wallet", "crossed"}
        SELLING_EXCLUDE = {"brand_name", "brand_hash", "supplier_id", "no_return_map"}
        if isinstance(selling, dict):
            for k, v in selling.items():
                if k in SELLING_EXCLUDE:
                    continue
                if k in SELLING_PRICE_KEYS and isinstance(v, (int, float)) and v > 500:
                    price_cents = int(v)
                    price_source = f"selling.{k}"
                    break
                # nested dict: selling.prices = {basic: X}
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, (int, float)) and v2 > 500:
                            price_cents = int(v2)
                            price_source = f"selling.{k}.{k2}"
                            break
                    if price_cents:
                        break

        # data dict
        if not price_cents:
            data = card.get("data", {})
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in ("subject_id", "subject_root_id", "chrt_ids", "tech_size"):
                        continue
                    if isinstance(v, (int, float)) and v > 500:
                        price_cents = int(v)
                        price_source = f"data.{k}"
                        break

        # Top-level (exclude known non-price fields)
        if not price_cents:
            SKIP = {"selling", "data", "options", "media", "grouped_options",
                    "colors", "full_colors", "nm_colors_names", "certificate",
                    "user_flags", "imt_id", "nm_id", "imt_name", "slug",
                    "subj_name", "subj_root_name", "vendor_code", "description",
                    "update_date", "create_date", "need_kiz"}
            for k, v in card.items():
                if k in SKIP:
                    continue
                if isinstance(v, (int, float)) and v > 500:
                    price_cents = int(v)
                    price_source = f"card.{k}"
                    log(f"  WB: top-level price candidate: {k}={v}")
                    break

        # sizes
        if not price_cents:
            sizes = card.get("sizes", [])
            for sz in sizes[:3]:
                if isinstance(sz, dict):
                    szp = sz.get("price", {})
                    if isinstance(szp, dict):
                        for pk in ("basic", "product", "card", "wallet"):
                            pv = szp.get(pk)
                            if isinstance(pv, (int, float)) and pv > 500:
                                price_cents = int(pv)
                                price_source = f"sizes.price.{pk}"
                                break
                if price_cents:
                    break

        # --- PRICE API (always try if card.json didn't give a clean price) ---
        if not price_cents:
            log("  WB: no price in card.json → trying price APIs...")
            api_urls = [
                ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
                ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
                ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
                ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
                ("search_wb", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
            ]
            for label, api_url in api_urls:
                try:
                    pr = s.get(api_url, timeout=12, headers={
                        "Accept": "application/json",
                        "Referer": "https://www.wildberries.ru/",
                        "Origin": "https://www.wildberries.ru",
                    })
                    log(f"  WB API {label}: HTTP {pr.status_code} len={len(pr.text)}")
                    if pr.status_code != 200:
                        log(f"    body[:150]={pr.text[:150]}")
                        continue
                    pd = pr.json()
                    # card.wb.ru format: data.products[] or products[]
                    prods = pd.get("data", {}).get("products") or pd.get("products") or []
                    if not prods and isinstance(pd.get("data"), dict):
                        prods = pd["data"].get("items", [])
                    if prods:
                        p = prods[0]
                        log(f"    product keys={list(p.keys())[:12]}")
                        title = title or p.get("name", "")
                        brand = brand or p.get("brand", "")
                        for sz in p.get("sizes", []):
                            szp = sz.get("price", {})
                            if isinstance(szp, dict):
                                for pk in ("basic", "product", "card", "wallet", "old"):
                                    pv = szp.get(pk)
                                    if isinstance(pv, (int, float)) and pv > 500:
                                        price_cents = int(pv)
                                        price_source = f"api.{label}.sizes.price.{pk}"
                                        break
                            if price_cents:
                                break
                        # Also check top-level price in product
                        if not price_cents:
                            for pk in ("price", "priceU", "priceD"):
                                pv = p.get(pk)
                                if isinstance(pv, (int, float)) and pv > 500:
                                    price_cents = int(pv)
                                    price_source = f"api.{label}.{pk}"
                                    break
                    # web_api format
                    if not price_cents and isinstance(pd.get("data"), dict):
                        d = pd["data"]
                        log(f"    data keys={list(d.keys())[:12]}")
                        for k, v in d.items():
                            if isinstance(v, (int, float)) and v > 500:
                                price_cents = int(v)
                                price_source = f"api.{label}.data.{k}"
                                break
                            if isinstance(v, dict):
                                for k2, v2 in v.items():
                                    if isinstance(v2, (int, float)) and v2 > 500:
                                        price_cents = int(v2)
                                        price_source = f"api.{label}.data.{k}.{k2}"
                                        break
                                if price_cents:
                                    break
                    if price_cents:
                        log(f"    → price={price_cents} from {price_source}")
                        break
                except Exception as e:
                    log(f"  WB API {label} err: {e}")
                time.sleep(0.5)

        log(f"  WB: FINAL price={price_cents} ({price_source})")

        # --- CATEGORY / DESC ---
        cat = ""
        subj = card.get("subj_name", "")
        subj_root = card.get("subj_root_name", "")
        if subj and subj_root:
            cat = f"{subj_root} > {subj}"
        elif subj:
            cat = subj
        desc = card.get("description") or ""

        if title and price_cents:
            eye = build_eye("WB", str(nm_id), url, title, price_cents,
                            brand=brand or None, desc=desc or None, cat=cat or None)
            acc = feed_inbox(app, eye, "WB")
            sq = verify_sqlite("WB")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["WB"] = {"verdict": verdict, "sqlite": sq, "price_source": price_source,
                             "price": price_cents, "title": title}
            log(f"  WB VERDICT: {verdict} (price={price_cents} src={price_source})")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"),
                             "reason": f"title={bool(title)} price={price_cents} src={price_source}"}
            log(f"  WB VERDICT: E2E_FAIL")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "error": str(e)}
    return results.get("WB", {})

# ============================================================
# AVITO — direct ad fetch (skip listing), dump IP-block page
# ============================================================
def run_avito(app):
    log("=== AVITO: direct ad + IP-block dump ===")
    eye = None

    # Step 0: Dump the 36KB listing page for analysis
    log("  Step 0: Dump listing page...")
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
            )
            page = ctx.new_page()
            page.goto("https://www.avito.ru/vladivostok/smartfony-i-telefony",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(10000)
            html = page.content()
            dump_path = WORK / "avito_listing_dump.html"
            dump_path.write_text(html, encoding="utf-8")
            log(f"  Dump: {len(html)} bytes → {dump_path.name}")

            # Check what's actually in the page
            if "Доступ ограничен" in html:
                log("  → 'Доступ ограничен' found")
            if "SmartCaptcha" in html or "smartcaptcha" in html.lower():
                log("  → SmartCaptcha found")
            if "captcha" in html.lower():
                log("  → 'captcha' found")
            # Look for any ad-like patterns
            ad_ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', html)
            log(f"  → ad IDs in listing: {ad_ids[:10]} (total {len(ad_ids)})")
            # Look for JSON data
            state_m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
            if state_m:
                log(f"  → __PRELOADED_STATE__ found ({len(state_m.group(1))} chars)")
            # Title of the page
            t_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
            if t_m:
                log(f"  → page title: {t_m.group(1)[:80]}")
            # h1
            h1_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
            if h1_m:
                log(f"  → h1: {h1_m.group(1)[:80]}")

            # Step 1: Try direct ad URLs (known patterns for 2026)
            # Avito IDs in 2026 are likely 10-11 digits
            # Try a few "round" IDs that might be active
            test_ids = [
                # These are guesses based on era — 10-11 digit IDs
                "12045678901", "12123456789", "11987654321",
                "12345678901", "12567890123",
            ]
            log("  Step 1: Try direct ad URLs...")
            for aid in test_ids:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                log(f"    Ad {aid}...")
                try:
                    page.goto(ad_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(7000)
                    ad_html = page.content()
                    log(f"    Ad {aid}: len={len(ad_html)}")

                    if "Доступ ограничен" in ad_html:
                        log("    → IP block on ad page")
                        continue
                    if "Такой страницы" in ad_html or "не существует" in ad_html:
                        log("    → 404 / not found")
                        continue
                    if len(ad_html) < 5000:
                        log("    → stub")
                        continue

                    # Save first successful ad page
                    ad_dump = WORK / f"avito_ad_{aid}.html"
                    ad_dump.write_text(ad_html, encoding="utf-8")

                    # Parse title
                    title = None
                    m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', ad_html)
                    if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', ad_html)
                        if m: title = m.group(1).strip()
                    if not title:
                        m = re.search(r'<title[^>]*>([^<]+)</title>', ad_html, re.I)
                        if m:
                            title = re.sub(r'\s*[-|]\s*Авито.*$', '', m.group(1), flags=re.I).strip()

                    # Parse price
                    price_cents = None
                    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', ad_html, re.DOTALL):
                        try:
                            ld = json.loads(m.group(1))
                            if isinstance(ld, dict) and "offers" in ld:
                                off = ld["offers"]
                                if isinstance(off, list) and off: off = off[0]
                                if isinstance(off, dict):
                                    p = off.get("price") or off.get("lowPrice")
                                    if p:
                                        price_cents = int(float(p) * 100)
                                        break
                        except: pass
                    if not price_cents:
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', ad_html)
                        if m: price_cents = int(m.group(1))
                    if not price_cents:
                        m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', ad_html, re.I)
                        if m: price_cents = int(float(m.group(1)) * 100)
                    if not price_cents:
                        m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]{2,})\s*(?:₽|руб)', ad_html)
                        if m:
                            try:
                                raw = m.group(1).replace('\u00a0','').replace('\u2009','').replace('\u202f','').replace(' ','').replace(',','')
                                price_cents = int(float(raw) * 100)
                            except: pass

                    log(f"    Ad {aid}: title={title!r} price={price_cents}")
                    if title and price_cents and price_cents > 500:
                        eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                        log(f"    AVITO: SUCCESS ad={aid}")
                        break
                except Exception as e:
                    log(f"    Ad {aid} err: {e}")

            browser.close()
    except Exception as e:
        log(f"  Playwright exception: {e}\n{traceback.format_exc()}")

    # Step 2: curl_cffi direct ad (different IPs/UA might help)
    if not eye:
        log("  Step 2: curl_cffi direct ad...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            # Try a wider range of IDs
            for aid in ["12045678901", "12123456789", "11987654321",
                        "12345678901", "12567890123", "12678901234"]:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                try:
                    r = s.get(ad_url, timeout=15, headers={
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "Accept": "text/html,application/xhtml+xml",
                    })
                    log(f"    CC ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 3000:
                        html = r.text
                        if "Доступ ограничен" in html or "не существует" in html:
                            continue
                        title = None
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m: title = m.group(1).strip()
                        if not title:
                            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
                            if m: title = re.sub(r'\s*[-|]\s*Авито.*$', '', m.group(1), flags=re.I).strip()
                        price_cents = None
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m: price_cents = int(m.group(1))
                        if not price_cents:
                            m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', html, re.I)
                            if m: price_cents = int(float(m.group(1)) * 100)
                        log(f"    CC ad={aid}: title={title!r} price={price_cents}")
                        if title and price_cents and price_cents > 500:
                            eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                            log(f"    AVITO CC: SUCCESS ad={aid}")
                            break
                except Exception as e:
                    log(f"    CC ad={aid} err: {e}")
                time.sleep(1)
        except Exception as e:
            log(f"  CC exception: {e}")

    # Step 3: Try Avito API endpoint (if exists)
    if not eye:
        log("  Step 3: Avito API...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            api_urls = [
                "https://api.avito.ru/v1/listings?city=vladivostok&limit=5",
                "https://www.avito.ru/api/search?city=vladivostok&q=iphone&limit=5",
                "https://www.avito.ru/vladivostok/smartfony-i-telefony?format=json",
            ]
            for au in api_urls:
                try:
                    r = s.get(au, timeout=15, headers={
                        "Accept": "application/json",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    })
                    log(f"    API {au.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 100:
                        log(f"    body[:200]={r.text[:200]}")
                except Exception as e:
                    log(f"    API err: {e}")
        except Exception as e:
            log(f"  API exception: {e}")

    # Feed to intake
    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq,
                            "inbox": acc if isinstance(acc, dict) else None}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"),
                            "inbox": None, "reason": "IP block / no ad fetched",
                            "dump": str(WORK / "avito_listing_dump.html")}
        log("  AVITO VERDICT: E2E_FAIL (IP block)")
    return results.get("AVITO", {})

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E FIX10 START ===")
    app = make_app()
    log("App ready")

    run_wb(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"=== E2E FIX10 DONE in {elapsed:.1f}s ===")
    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
