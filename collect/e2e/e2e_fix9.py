#!/usr/bin/env python3
"""e2e_fix9.py — WB: dump selling/data + price API verbose. Avito: m.avito, Moscow, web IDs."""
import json, os, re, sys, time, sqlite3, traceback, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=10))
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WORK = ROOT / "e2e-fix9"
WORK.mkdir(exist_ok=True)
DB = WORK / "r52.sqlite3"
RESULT = ROOT / "e2e_fix9_result.json"

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
    fname = f"fix9-{source}-{hashlib.sha256(data).hexdigest()[:8]}.json"
    (WORK / fname).write_bytes(data)
    item, created = inbox.stage_bytes(data, filename=fname, origin=f"FIX9_{source}")
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
# WB — dump selling, data, price API verbose
# ============================================================
def run_wb(app):
    log("=== WB: dump selling/data + price API ===")
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

        # --- DUMP selling ---
        selling = card.get("selling", {})
        log(f"  WB: selling type={type(selling).__name__}")
        if isinstance(selling, dict):
            log(f"  WB: selling keys = {list(selling.keys())}")
            for k, v in selling.items():
                log(f"  WB:   selling.{k} = {repr(v)[:120]}")
        elif isinstance(selling, list):
            log(f"  WB: selling is list len={len(selling)}")
            for i, item in enumerate(selling[:3]):
                log(f"  WB:   selling[{i}] = {repr(item)[:200]}")

        # --- DUMP data ---
        data = card.get("data", {})
        log(f"  WB: data type={type(data).__name__}")
        if isinstance(data, dict):
            log(f"  WB: data keys = {list(data.keys())[:20]}")
            for k in list(data.keys())[:10]:
                v = data[k]
                log(f"  WB:   data.{k} = {repr(v)[:120]}")

        # --- TITLE ---
        title = card.get("imt_name") or ""
        log(f"  WB: title={title!r}")

        # --- BRAND ---
        brand = ""
        if isinstance(selling, dict):
            brand = selling.get("brand_name") or selling.get("brand") or ""
        log(f"  WB: brand={brand!r}")

        # --- PRICE: exhaustive search in card.json ---
        price_cents = None
        price_source = ""

        # 1. selling dict — try ALL keys
        if isinstance(selling, dict):
            for k, v in selling.items():
                if isinstance(v, (int, float)) and v > 100:
                    price_cents = int(v)
                    price_source = f"selling.{k}"
                    log(f"  WB: price from selling.{k}={v}")
                    break
                # nested: selling.price = {basic: X, ...}
                if isinstance(v, dict) and any(isinstance(x, (int, float)) and x > 100 for x in v.values()):
                    for k2, v2 in v.items():
                        if isinstance(v2, (int, float)) and v2 > 100:
                            price_cents = int(v2)
                            price_source = f"selling.{k}.{k2}"
                            log(f"  WB: price from selling.{k}.{k2}={v2}")
                            break
                    if price_cents:
                        break

        # 2. data dict
        if not price_cents and isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, (int, float)) and v > 100:
                    price_cents = int(v)
                    price_source = f"data.{k}"
                    break
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, (int, float)) and v2 > 100:
                            price_cents = int(v2)
                            price_source = f"data.{k}.{k2}"
                            break
                    if price_cents:
                        break

        # 3. Top-level numeric fields
        if not price_cents:
            for k, v in card.items():
                if k in ("selling", "data", "options", "media", "grouped_options", "colors", "full_colors", "nm_colors_names", "certificate", "user_flags"):
                    continue
                if isinstance(v, (int, float)) and v > 100:
                    price_cents = int(v)
                    price_source = f"card.{k}"
                    log(f"  WB: price from card.{k}={v}")
                    break

        # 4. sizes (top-level or in data)
        if not price_cents:
            sizes = card.get("sizes", [])
            if not sizes and isinstance(data, dict):
                sizes = data.get("sizes", [])
            for sz in sizes[:3]:
                if isinstance(sz, dict):
                    szp = sz.get("price", {})
                    if isinstance(szp, dict):
                        for pk in ("basic", "product", "card", "wallet", "old"):
                            pv = szp.get(pk)
                            if isinstance(pv, (int, float)) and pv > 100:
                                price_cents = int(pv)
                                price_source = f"sizes.price.{pk}"
                                break
            if price_cents:
                log(f"  WB: price from sizes: {price_source}={price_cents}")

        # 5. Price API — verbose
        if not price_cents:
            log("  WB: no price in card.json, trying price APIs...")
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
                    body = pr.text[:500]
                    log(f"  WB API {label}: HTTP {pr.status_code} len={len(pr.text)} body[:200]={body[:200]}")
                    if pr.status_code == 200:
                        pd = pr.json()
                        # Try multiple response formats
                        prods = pd.get("data", {}).get("products") or pd.get("products") or pd.get("data", {}).get("items") or []
                        if prods:
                            p = prods[0]
                            log(f"  WB API {label}: product keys={list(p.keys())[:15]}")
                            title = title or p.get("name", "")
                            brand = brand or p.get("brand", "")
                            for sz in p.get("sizes", []):
                                szp = sz.get("price", {})
                                if isinstance(szp, dict):
                                    for pk in ("basic", "product", "card", "wallet"):
                                        pv = szp.get(pk)
                                        if isinstance(pv, (int, float)) and pv > 100:
                                            price_cents = int(pv)
                                            price_source = f"api.{label}.{pk}"
                                            break
                                if price_cents:
                                    break
                        # web_api format
                        if not price_cents and "data" in pd and isinstance(pd["data"], dict):
                            d = pd["data"]
                            log(f"  WB API {label}: data keys={list(d.keys())[:15]}")
                            for k, v in d.items():
                                if isinstance(v, (int, float)) and v > 100:
                                    price_cents = int(v)
                                    price_source = f"api.{label}.data.{k}"
                                    break
                                if isinstance(v, dict):
                                    for k2, v2 in v.items():
                                        if isinstance(v2, (int, float)) and v2 > 100:
                                            price_cents = int(v2)
                                            price_source = f"api.{label}.data.{k}.{k2}"
                                            break
                                    if price_cents:
                                        break
                        if price_cents:
                            log(f"  WB API {label}: price={price_cents} ({price_source})")
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
            results["WB"] = {"verdict": verdict, "sqlite": sq, "price_source": price_source}
            log(f"  WB VERDICT: {verdict}")
        else:
            results["WB"] = {"verdict": "E2E_FAIL", "sqlite": verify_sqlite("WB"),
                             "reason": f"title={bool(title)} price={price_cents} src={price_source}"}
            log(f"  WB VERDICT: E2E_FAIL (title={bool(title)}, price={price_cents})")
    except Exception as e:
        log(f"  WB EXCEPTION: {e}\n{traceback.format_exc()}")
        results["WB"] = {"verdict": "E2E_FAIL", "error": str(e)}
    return results.get("WB", {})

# ============================================================
# AVITO — multi-strategy: m.avito, Moscow, direct ad URLs
# ============================================================
def run_avito(app):
    log("=== AVITO: multi-strategy ===")
    eye = None
    real_ids = []

    # Strategy 1: m.avito.ru (mobile, often less protected)
    log("  S1: m.avito.ru listing...")
    try:
        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        mobile_urls = [
            "https://m.avito.ru/vladivostok/smartfony-i-telefony",
            "https://m.avito.ru/moscow/smartfony-i-telefony",
            "https://m.avito.ru/vladivostok/tech/communication/cellphones",
        ]
        for mu in mobile_urls:
            try:
                r = s.get(mu, timeout=20, headers={
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                })
                log(f"  m.avito {mu.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                if r.status_code == 200 and len(r.text) > 5000:
                    if "Доступ ограничен" in r.text or "problem with IP" in r.text.lower():
                        log("  → IP block (mobile)")
                        continue
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', r.text)
                    ids += re.findall(r'"(?:adId|ad_id|id)"\s*:\s*"?(\d{9,})"?', r.text)
                    seen = set()
                    for i in ids:
                        if i not in seen and len(i) >= 9:
                            seen.add(i)
                            real_ids.append(i)
                    log(f"  → {len(real_ids)} IDs from m.avito")
                    if real_ids:
                        break
            except Exception as e:
                log(f"  m.avito err: {e}")
    except Exception as e:
        log(f"  S1 exception: {e}")

    # Strategy 2: Moscow city page (different geo, maybe less blocked)
    if not real_ids:
        log("  S2: Moscow www.avito.ru...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            moscow_urls = [
                "https://www.avito.ru/moscow/smartfony-i-telefony",
                "https://www.avito.ru/moscow/tech/communication/cellphones",
            ]
            for mu in moscow_urls:
                try:
                    r = s.get(mu, timeout=20, headers={
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "Accept": "text/html,application/xhtml+xml",
                    })
                    log(f"  Moscow {mu.split('/')[-1]}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 5000:
                        if "Доступ ограничен" in r.text:
                            log("  → IP block (Moscow)")
                            continue
                        ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', r.text)
                        ids += re.findall(r'"(?:adId|ad_id|id)"\s*:\s*"?(\d{9,})"?', r.text)
                        seen = set()
                        for i in ids:
                            if i not in seen and len(i) >= 9:
                                seen.add(i)
                                real_ids.append(i)
                        log(f"  → {len(real_ids)} IDs from Moscow")
                        if real_ids:
                            break
                except Exception as e:
                    log(f"  Moscow err: {e}")
        except Exception as e:
            log(f"  S2 exception: {e}")

    # Strategy 3: Playwright on m.avito.ru (mobile UA)
    if not real_ids:
        log("  S3: Playwright m.avito.ru...")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
                )
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    viewport={"width": 390, "height": 844},
                    locale="ru-RU",
                    timezone_id="Asia/Vladivostok",
                    is_mobile=True,
                    has_touch=True,
                )
                page = ctx.new_page()
                for murl in [
                    "https://m.avito.ru/vladivostok/smartfony-i-telefony",
                    "https://m.avito.ru/moscow/smartfony-i-telefony",
                ]:
                    try:
                        page.goto(murl, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(8000)
                        html = page.content()
                        log(f"  PW m.avito {murl.split('/')[-1]}: len={len(html)}")
                        if "Доступ ограничен" in html:
                            log("  → IP block (PW mobile)")
                            continue
                        ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', html)
                        ids += re.findall(r'"(?:adId|ad_id|id)"\s*:\s*"?(\d{9,})"?', html)
                        seen = set()
                        for i in ids:
                            if i not in seen and len(i) >= 9:
                                seen.add(i)
                                real_ids.append(i)
                        log(f"  → {len(real_ids)} IDs from PW mobile")
                        if real_ids:
                            break
                    except Exception as e:
                        log(f"  PW mobile err: {e}")
                browser.close()
        except Exception as e:
            log(f"  S3 exception: {e}")

    # Strategy 4: Playwright on www.avito.ru Moscow (desktop)
    if not real_ids:
        log("  S4: Playwright Moscow desktop...")
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
                    timezone_id="Europe/Moscow",
                )
                page = ctx.new_page()
                # Moscow city page first (less specific = less likely blocked)
                page.goto("https://www.avito.ru/moscow", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(10000)
                html = page.content()
                log(f"  PW Moscow city: len={len(html)}")
                if "Доступ ограничен" not in html and len(html) > 5000:
                    ids = re.findall(r'avito\.ru/(?:[^/]+/)*ad/(\d{7,})', html)
                    ids += re.findall(r'"(?:adId|ad_id|id)"\s*:\s*"?(\d{9,})"?', html)
                    seen = set()
                    for i in ids:
                        if i not in seen and len(i) >= 9:
                            seen.add(i)
                            real_ids.append(i)
                    log(f"  → {len(real_ids)} IDs from Moscow city")
                browser.close()
        except Exception as e:
            log(f"  S4 exception: {e}")

    log(f"  Total real IDs: {len(real_ids)} → {real_ids[:10]}")

    # --- Fetch an ad (Playwright preferred) ---
    if real_ids:
        log("  Fetching ad via Playwright...")
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
                for aid in real_ids[:8]:
                    ad_url = f"https://www.avito.ru/ad/{aid}"
                    log(f"  Ad {aid}...")
                    try:
                        page.goto(ad_url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(8000)
                        ad_html = page.content()
                        log(f"  Ad {aid}: len={len(ad_html)}")
                        if "Доступ ограничен" in ad_html or len(ad_html) < 5000:
                            continue
                        if "Такой страницы" in ad_html or "не существует" in ad_html:
                            continue

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

                        log(f"  Ad {aid}: title={title!r} price={price_cents}")
                        if title and price_cents and price_cents > 500:
                            eye = build_eye("AVITO", aid, ad_url, title, price_cents)
                            log(f"  AVITO PW: SUCCESS ad={aid}")
                            break
                    except Exception as e:
                        log(f"  Ad {aid} err: {e}")
                browser.close()
        except Exception as e:
            log(f"  PW fetch exception: {e}")

    # Fallback: curl_cffi on ad
    if not eye and real_ids:
        log("  CC fallback on ad...")
        try:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate="chrome")
            for aid in real_ids[:5]:
                ad_url = f"https://www.avito.ru/ad/{aid}"
                try:
                    r = s.get(ad_url, timeout=20, headers={
                        "Accept-Language": "ru-RU,ru;q=0.9",
                        "Accept": "text/html",
                    })
                    log(f"  CC ad={aid}: HTTP {r.status_code} len={len(r.text)}")
                    if r.status_code == 200 and len(r.text) > 3000 and "Доступ ограничен" not in r.text:
                        html = r.text
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
    log("=== E2E FIX9 START ===")
    app = make_app()
    log("App ready")

    run_wb(app)
    run_avito(app)

    elapsed = time.monotonic() - t0
    log(f"=== E2E FIX9 DONE in {elapsed:.1f}s ===")
    out = {"ts": now_iso(), "s": round(elapsed, 1),
           "pass": sum(1 for v in results.values() if v.get("verdict") == "E2E_PASS"),
           "d": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Result → {RESULT}")
    for k, v in results.items():
        log(f"  {k}: {v.get('verdict')}")

if __name__ == "__main__":
    main()
