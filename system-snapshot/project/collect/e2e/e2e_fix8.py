#!/usr/bin/env python3
"""e2e_fix8.py — WB (imt_name + selling price) + Avito (real IDs from listing page)."""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix8"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix8_result.json"

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
    fname = f"fix8-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX8_{source}")
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
# WB — correct keys: imt_name, selling.price
# ============================================================
def run_wb(app):
    log("=== WB: Basket CDN (imt_name + selling) ===")
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

        # --- TITLE: imt_name (confirmed key) ---
        title = card.get("imt_name") or card.get("imt_id") or ""
        if not title:
            log(f"  WB: imt_name empty, trying imts/name/title...")
            title = card.get("imts") or card.get("name") or card.get("title") or ""
        log(f"  WB: title={title!r}")

        # --- BRAND: selling.brand_name or options ---
        brand = ""
        selling = card.get("selling", {})
        if isinstance(selling, dict):
            brand = selling.get("brand_name") or selling.get("brand") or ""
        if not brand:
            for opt in card.get("options", []):
                if isinstance(opt, dict):
                    opt_name = (opt.get("name") or "").lower()
                    if opt_name in ("бренд", "производитель", "brand"):
                        brand = opt.get("value", "")
                        break
        log(f"  WB: brand={brand!r}")

        # --- PRICE: selling dict first, then sizes, then price API ---
        price_cents = None
        price_source = ""
        if isinstance(selling, dict):
            for pk in ("price", "priceU", "priceD", "oldPrice"):
                pv = selling.get(pk)
                if isinstance(pv, (int, float)) and pv > 0:
                    price_cents = int(pv)
                    price_source = f"selling.{pk}"
                    break
        if not price_cents:
            # sizes array (might be in card or in data)
            sizes = card.get("sizes", [])
            if not sizes and isinstance(card.get("data"), dict):
                sizes = card["data"].get("sizes", [])
            for sz in sizes:
                if not isinstance(sz, dict):
                    continue
                sz_price = sz.get("price", {})
                if not isinstance(sz_price, dict):
                    continue
                for pk in ("basic", "product", "card", "wallet"):
                    pv = sz_price.get(pk)
                    if isinstance(pv, (int, float)) and pv > 0:
                        price_cents = int(pv)
                        price_source = f"sizes.price.{pk}"
                        break
                if price_cents:
                    break

        # --- Fallback: Price API ---
        if not price_cents:
            log("  WB: no price in card.json, trying price API...")
            for api_url in [
                f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
                f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
                f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
            ]:
                try:
                    pr = s.get(api_url, timeout=10, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
                    if pr.status_code == 200:
                        pd = pr.json()
                        prods = pd.get("data", {}).get("products") or pd.get("products") or []
                        if prods:
                            p = prods[0]
                            title = title or p.get("name", "")
                            brand = brand or p.get("brand", "")
                            for sz in p.get("sizes", []):
                                szp = sz.get("price", {})
                                if isinstance(szp, dict):
                                    for pk in ("basic", "product", "card"):
                                        pv = szp.get(pk)
                                        if isinstance(pv, (int, float)) and pv > 0:
                                            price_cents = int(pv)
                                            price_source = f"api.{api_url.split('/')[2]}.{pk}"
                                            break
                                if price_cents:
                                    break
                            if price_cents:
                                break
                except Exception as e:
                    log(f"  WB price API err: {e}")

        log(f"  WB: price={price_cents} ({price_source})")

        # --- CATEGORY ---
        cat = ""
        subj = card.get("subj_name", "")
        subj_root = card.get("subj_root_name", "")
        if subj and subj_root:
            cat = f"{subj_root} > {subj}"
        elif subj:
            cat = subj

        # --- DESCRIPTION ---
        desc = card.get("description") or ""

        if title and price_cents:
            eye = build_eye("WB", str(nm_id), url, title, price_cents,
                            brand=brand or None, desc=desc or None, cat=cat or None)
            acc = feed_inbox(app, eye, "WB")
            sq = verify_sqlite("WB")
            verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
            results["WB"] = {"verdict": verdict, "sqlite": sq,
                             "strategy": "basket_cdn_imt_name", "price_source": price_source}
            log(f"  WB VERDICT: {verdict}")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"),
                             "reason": f"title={bool(title)} price={price_cents}"}
            log(f"  WB VERDICT: E2E_FAIL (title={bool(title)}, price={price_cents})")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "error": str(e)}
    return results.get("WB", {})

# ============================================================
# AVITO — extract real ad IDs from listing page via Playwright
# ============================================================
def run_avito(app):
    log("=== AVITO: real IDs from listing + Playwright fetch ===")
    eye = None
    real_ids = []

    # Step 1: Playwright — load listing, extract ad IDs
    log("  Step 1: Playwright listing page...")
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

            # Load category listing (more ads than search)
            listing_url = "https://www.avito.ru/vladivostok/smartfony-i-telefony"
            log(f"  Loading: {listing_url}")
            try:
                page.goto(listing_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                html = page.content()
                log(f"  Listing: len={len(html)}")
            except Exception as e:
                log(f"  Listing goto err: {e}, trying m.avito...")
                html = ""
                try:
                    page.goto("https://m.avito.ru/vladivostok/smartfony-i-telefony",
                              wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(6000)
                    html = page.content()
                    log(f"  Mobile listing: len={len(html)}")
                except Exception as e2:
                    log(f"  Mobile listing err: {e2}")

            if len(html) > 3000:
                # Check for IP block
                if "Доступ ограничен" in html or "problem with IP" in html.lower():
                    log("  → IP block on listing")
                else:
                    # Extract ad IDs from HTML
                    # Pattern 1: /ad/1234567890
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', html)
                    # Pattern 2: data attributes
                    ids += re.findall(r'data-ad-id=["\']?(\d{7,})', html)
                    # Pattern 3: href with ad number
                    ids += re.findall(r'href="[^"]*?/(\d{9,12})/"', html)
                    # Pattern 4: JSON in page
                    ids += re.findall(r'"(?:id|adId|ad_id)"\s*:\s*"?(\d{7,})"?', html)

                    seen = set()
                    unique_ids = []
                    for i in ids:
                        if i not in seen and len(i) >= 9:
                            seen.add(i)
                            unique_ids.append(i)
                    log(f"  → {len(unique_ids)} unique ad IDs: {unique_ids[:10]}")
                    real_ids = unique_ids[:15]

            # Step 2: Navigate to a real ad
            if real_ids:
                log("  Step 2: Fetching real ad...")
                for aid in real_ids[:6]:
                    ad_url = f"https://www.avito.ru/ad/{aid}"
                    log(f"  Ad {aid}: {ad_url}")
                    try:
                        page.goto(ad_url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(8000)
                        ad_html = page.content()
                        log(f"  Ad {aid}: len={len(ad_html)}")

                        if "Доступ ограничен" in ad_html:
                            log("  → IP block")
                            continue
                        if "Такой страницы" in ad_html or "не существует" in ad_html or "404" in ad_html[:200]:
                            log("  → 404")
                            continue
                        if len(ad_html) < 5000:
                            log("  → stub")
                            continue

                        # Parse title
                        title = None
                        m = re.search(r'<h1[^>]*>([^<]{5,300})</h1>', ad_html)
                        if m:
                            title = m.group(1).strip()
                        if not title:
                            m = re.search(r'property="og:title"\s+content="([^"]+)"', ad_html)
                            if m:
                                title = m.group(1).strip()
                        if not title:
                            m = re.search(r'<meta[^>]*name="og:title"[^>]*content="([^"]+)"', ad_html, re.I)
                            if m:
                                title = m.group(1).strip()
                        if not title:
                            m = re.search(r'<title[^>]*>([^<]+)</title>', ad_html, re.I)
                            if m:
                                t = m.group(1).strip()
                                t = re.sub(r'\s*[-|]\s*Авито.*$', '', t, flags=re.I)
                                t = re.sub(r'^Авито\s*[-|]\s*', '', t, flags=re.I)
                                title = t.strip()

                        # Parse price
                        price_cents = None
                        # JSON-LD
                        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', ad_html, re.DOTALL):
                            try:
                                ld = json.loads(m.group(1))
                                if isinstance(ld, dict) and "offers" in ld:
                                    off = ld["offers"]
                                    if isinstance(off, list) and off:
                                        off = off[0]
                                    if isinstance(off, dict):
                                        p = off.get("price") or off.get("lowPrice")
                                        if p:
                                            price_cents = int(float(p) * 100)
                                            break
                            except:
                                pass
                        # __PRELOADED_STATE__
                        if not price_cents:
                            m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', ad_html, re.DOTALL)
                            if m:
                                try:
                                    state = json.loads(m.group(1))
                                    # Deep search for price
                                    state_str = json.dumps(state)
                                    pm = re.search(r'"(?:price|amount)"\s*:\s*(\d{5,})', state_str)
                                    if pm:
                                        price_cents = int(pm.group(1))
                                except:
                                    pass
                        # og:price
                        if not price_cents:
                            m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', ad_html, re.I)
                            if m:
                                price_cents = int(float(m.group(1)) * 100)
                        # DOM price
                        if not price_cents:
                            m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', ad_html)
                            if m:
                                price_cents = int(m.group(1))
                        # Visible text
                        if not price_cents:
                            m = re.search(r'(\d[\d\u00a0\u2009\u202f\s,.]{2,})\s*(?:₽|руб)', ad_html)
                            if m:
                                try:
                                    raw = m.group(1)
                                    raw = raw.replace('\u00a0', '').replace('\u2009', '').replace('\u202f', '')
                                    raw = raw.replace(' ', '').replace(',', '')
                                    price_cents = int(float(raw) * 100)
                                except:
                                    pass

                        log(f"  Ad {aid}: title={title!r} price={price_cents}")
                        if title and price_cents and price_cents > 500:
                            eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                            log(f"  AVITO PW: SUCCESS ad={aid}")
                            break
                    except Exception as e:
                        log(f"  Ad {aid} err: {e}")

            browser.close()
    except Exception as e:
        log(f"  Playwright exception: {e}\n{traceback.format_exc()}")

    # Fallback: curl_cffi on listing → extract IDs → fetch ad
    if not eye:
        log("  Step 3: curl_cffi fallback...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            # Try listing
            for lu in [
                "https://www.avito.ru/vladivostok/smartfony-i-telefony",
                "https://m.avito.ru/vladivostok/smartfony-i-telefony",
                "https://www.avito.ru/vladivostok/tech/communication/cellphones",
            ]:
                try:
                    r = s.get(lu, timeout=20, headers={
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "Accept": "text/html,application/xhtml+xml",
                    })
                    log(f"  CC listing {lu.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 5000 and "Доступ ограничен" not in r.text:
                        ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', r.text)
                        ids += re.findall(r'data-ad-id=["\']?(\d{7,})', r.text)
                        seen = set()
                        for i in ids:
                            if i not in seen and len(i) >= 9:
                                seen.add(i)
                                real_ids.append(i)
                        log(f"  → {len(real_ids)} IDs from CC listing")
                        if real_ids:
                            break
                except Exception as e:
                    log(f"  CC listing err: {e}")

            # Fetch first real ad
            for aid in real_ids[:5]:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                try:
                    r = s.get(ad_url, timeout=20, headers={
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "Accept": "text/html",
                    })
                    log(f"  CC ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 3000:
                        html = r.text
                        if "Доступ ограничен" in html or "не существует" in html:
                            continue
                        title = None
                        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
                        if m:
                            title = m.group(1).strip()
                        if not title:
                            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
                            if m:
                                title = re.sub(r'\s*[-|]\s*Авито.*$', '', m.group(1), flags=re.I).strip()
                        price_cents = None
                        m = re.search(r'"price"\s*:\s*"?(\d{4,})"?', html)
                        if m:
                            price_cents = int(m.group(1))
                        if not price_cents:
                            m = re.search(r'og:price:amount[^>]*content="([\d.]+)"', html, re.I)
                            if m:
                                price_cents = int(float(m.group(1)) * 100)
                        if title and price_cents and price_cents > 500:
                            eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                            log(f"  AVITO CC: SUCCESS ad={aid}")
                            break
                except Exception as e:
                    log(f"  CC ad={aid} err: {e}")
        except Exception as e:
            log(f"  CC fallback err: {e}")

    # Feed to intake
    if eye:
        acc = feed_inbox(app, eye, "AVITO")
        sq = verify_sqlite("AVITO")
        verdict = "E2E_PASS" if sq["product_count"] > 0 and sq["observation_count"] > 0 and sq["price_point_count"] > 0 else "E2E_FAIL"
        results["AVITO"] = {"verdict": verdict, "sqlite": sq,
                            "inbox": acc if isinstance(acc, dict) else None,
                            "ad_ids_tried": real_ids[:10]}
        log(f"  AVITO VERDICT: {verdict}")
    else:
        results["AVITO"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("AVITO"),
                            "inbox": None, "ad_ids_tried": real_ids[:10],
                            "reason": "no ad fetched"}
        log("  AVITO VERDICT: E2E_FAIL")
    return results.get("AVITO", {})

# ============================================================
# MAIN
# ============================================================
def main():
    log("=== E2E FIX8 START ===")
    app = make_app()
    log("App ready")

    run_wb(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"=== E2E FIX8 DONE in {elapsed:.1f}s ===")
    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
