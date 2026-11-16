"""Диагностика AliExpress RU: фокус на MTOP + TMD + DOM (v3, 300s timeout)."""
import json, os, sys, time, traceback
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=10))
OUT = "diag_ali_ru3_result.json"
PRODUCT_ID = "1005013496905897"

result = {"product_id": PRODUCT_ID, "timestamp": datetime.now(TZ).isoformat(),
          "phase": "start", "region": "RU", "errors": [], "log": [],
          "mtop_bodies": [], "dom": {}, "rendered": False, "tmd_found": False}

def log(msg):
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    result["log"].append(f"[{ts}] {msg}")
    print(f"[{ts}] {msg}", flush=True)

def save():
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

def safe_eval(page, script, timeout_ms=15000):
    """Evaluate с защитой от зависания."""
    try:
        return page.evaluate(script, timeout=timeout_ms)
    except Exception as e:
        log(f"safe_eval error: {e}")
        return None

try:
    from playwright.sync_api import sync_playwright
    PROFILE = os.path.expanduser("~/.local/share/r52/aliexpress_pw_profile_ru")
    os.makedirs(PROFILE, exist_ok=True)

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        PROFILE, headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="ru-RU", viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Moscow", ignore_https_errors=True,
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
    """)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    log("Browser started")

    # RU cookies
    ctx.add_cookies([
        {"name": "aep_usuc_f", "value": "RU|ru_RU|RUB|10001", "domain": ".aliexpress.com", "path": "/", "secure": True},
        {"name": "_lang", "value": "ru_RU", "domain": ".aliexpress.com", "path": "/", "secure": True},
        {"name": "intl_locale", "value": "ru_RU", "domain": ".aliexpress.com", "path": "/", "secure": True},
        {"name": "xman_us_f", "value": "us_f=RU&us_g=10001&us_l=ru_RU", "domain": ".aliexpress.com", "path": "/", "secure": True},
    ])
    log("RU cookies set")

    # Warm on aliexpress.ru
    log("Warming on aliexpress.ru...")
    page.goto("https://www.aliexpress.ru/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    for sel in ('button:has-text("Принять")', 'button:has-text("Accept")',
               '[class*="cookie"] button', '#onetrust-accept-btn-handler'):
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                log(f"Cookie: {sel}")
                page.wait_for_timeout(2000)
                break
        except Exception:
            pass
    page.evaluate("window.scrollTo(0, 300)")
    page.wait_for_timeout(3000)
    log(f"Warm done, {len(ctx.cookies())} cookies")

    # Navigate to product
    url = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
    log(f"Product: {url}")

    mtop_bodies = []
    def on_resp(resp):
        try:
            u = resp.url
            if "mtop" in u.lower() and "pdp" in u.lower() and "punish" not in u and "report" not in u:
                body = ""
                try:
                    body = resp.text()
                except Exception:
                    pass
                if len(body) > 50:
                    mtop_bodies.append({"url": u[:400], "status": resp.status,
                                       "body_len": len(body), "body": body[:20000]})
                    log(f"MTOP: {u[:100]}... ({len(body)}b)")
        except Exception:
            pass
    page.on("response", on_resp)

    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    log(f"Loaded HTTP {resp.status if resp else '?'}")

    # Wait for render / TMD
    rendered = False
    tmd_found = False
    for tick in range(20):
        page.wait_for_timeout(1000)
        st = safe_eval(page, """
            () => {
                const h1 = document.querySelector('h1');
                const prices = [];
                ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                 '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]',
                 '[class*="price-row"]','[class*="Price"]'].forEach(s => {
                    try { document.querySelectorAll(s).forEach(el => {
                        const t = (el.textContent||'').trim();
                        if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
                    }); } catch(e) {}
                });
                const iframes = [];
                document.querySelectorAll('iframe').forEach(f => iframes.push((f.src||'').substring(0,300)));
                return {h1: h1?h1.textContent.trim().substring(0,300):null,
                        prices:[...new Set(prices)].slice(0,10), iframes};
            }
        """)
        if not st:
            continue
        log(f"tick={tick}: h1={str(st.get('h1',''))[:40]}, prices={st.get('prices',[])[:3]}, ifr={len(st.get('iframes',[]))}")
        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
            rendered = True
            log(f"RENDERED tick={tick}")
            break
        for fr in st.get("iframes", []):
            if "punish" in fr or "tmd" in fr.lower() or "recaptcha" in fr.lower():
                tmd_found = True
                break
        if tmd_found:
            break

    # Solve TMD
    if tmd_found and not rendered:
        log("Solving TMD...")
        page.wait_for_timeout(4000)
        solved = False
        for attempt in range(3):
            log(f"TMD attempt {attempt+1}")
            page.wait_for_timeout(2000)
            for f in page.frames:
                try:
                    fu = f.url or ""
                    if "tmd" not in fu.lower() and "punish" not in fu.lower() and "recaptcha" not in fu.lower():
                        continue
                    f.wait_for_load_state("domcontentloaded", timeout=8000)
                    page.wait_for_timeout(1500)
                    for sel in ('#recaptcha-anchor', 'input[type="checkbox"]', '[role="checkbox"]',
                               '.rc-button', 'span.recaptcha-checkbox', '.rcbxd'):
                        try:
                            cb = f.query_selector(sel)
                            if cb:
                                box = cb.bounding_box()
                                if box and box["width"] > 0:
                                    page.mouse.click(box["x"]+box["width"]/2, box["y"]+box["height"]/2)
                                else:
                                    cb.click()
                                log(f"  Clicked: {sel}")
                                solved = True
                                break
                        except Exception as se:
                            log(f"  {sel}: {se}")
                    if solved:
                        break
                except Exception as fe:
                    log(f"  frame: {fe}")
            if solved:
                log("Waiting re-render...")
                for w in range(15):
                    page.wait_for_timeout(1000)
                    check = safe_eval(page, """
                        () => {
                            const h1 = document.querySelector('h1');
                            const pEls = document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"]');
                            const prices = [];
                            pEls.forEach(el => { const t=(el.textContent||'').trim(); if(t&&/\\d/.test(t)&&t.length<50) prices.push(t); });
                            return {h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,6)};
                        }
                    """)
                    if check and (check.get("prices") or (check.get("h1") and len(check["h1"]) > 15)):
                        rendered = True
                        log(f"TMD solved, rendered at {w}s")
                        break
                break
        result["tmd_solved"] = solved

    # Direct MTOP call (reliable)
    log("Direct MTOP call...")
    try:
        mtop_direct = page.evaluate("""
            async (pid) => {
                try {
                    const data = JSON.stringify({productId: pid, _currency: 'RUB', _lang: 'ru_RU'});
                    const t = Date.now();
                    const url = `https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/?jsv=2.7.2&appKey=24568851&t=${t}&sign=x&api=mtop.aliexpress.pdp.pc.query&v=1.0&type=originaljson&dataType=json&timeout=20000&data=${encodeURIComponent(data)}`;
                    const r = await fetch(url, {credentials: 'include', headers: {'Accept':'application/json'}});
                    const txt = await r.text();
                    return {status: r.status, len: txt.length, body: txt.substring(0, 30000)};
                } catch(e) { return {error: e.toString()}; }
            }
        """, PRODUCT_ID)
        result["mtop_direct"] = mtop_direct
        log(f"MTOP direct: {mtop_direct.get('status','?')} {mtop_direct.get('len','?')}b")
    except Exception as e:
        result["mtop_direct"] = {"error": str(e)}
        log(f"MTOP direct err: {e}")

    # DOM extraction (with timeout)
    log("DOM extraction...")
    dom = safe_eval(page, """
        () => {
            const r = {};
            const h1 = document.querySelector('h1');
            r.title = h1 ? h1.textContent.trim() : null;
            if (!r.title) { const t = document.querySelector('title'); if (t) r.title = t.textContent.replace(/\\s*[|\\-].*$/,'').trim(); }
            r.prices = [];
            ['[data-pl="price"]','[class*="price--current"]','[class*="Price--current"]',
             '[class*="pdp-price"]','[class*="price-current"]','span[class*="price"]',
             '[class*="sku-price"]','[class*="priceRow"]','[class*="price-row"]',
             '[data-pl*="price"]','[class*="Price"]','[class*="price"]'].forEach(s => {
                try { document.querySelectorAll(s).forEach(el => {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 80 && /\\d/.test(t)) r.prices.push(t);
                }); } catch(e) {}
            });
            r.prices = [...new Set(r.prices)].slice(0,15);
            r.images = [];
            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.getAttribute('data-src') || '';
                if (src && (src.includes('alicdn')||src.includes('aliexpress-media'))
                    && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                    && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif'))
                    r.images.push(src);
            });
            r.images = [...new Set(r.images)].slice(0,20);
            r.jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            r.meta = {};
            document.querySelectorAll('meta[property],meta[name]').forEach(m => {
                const p = m.getAttribute('property')||m.getAttribute('name')||'';
                const c = m.getAttribute('content')||'';
                if (p.includes('og:')||p.includes('product')||p.includes('price')) r.meta[p]=c.substring(0,300);
            });
            r.availability = null;
            ['[class*="stock"]','[class*="availability"]','[data-pl="stock"]').forEach(s => {
                try { document.querySelectorAll(s).forEach(el => {
                    const t = (el.textContent||'').trim().toLowerCase();
                    if (t.length < 100) {
                        if (t.includes('в наличии')||t.includes('in stock')||t.includes('available')) r.availability='IN_STOCK';
                        if (t.includes('нет в наличии')||t.includes('out of stock')) r.availability='OUT_OF_STOCK';
                    }
                }); } catch(e) {}
            });
            r.bodyLen = document.body ? document.body.innerHTML.length : 0;
            return r;
        }
    """, timeout_ms=20000)
    if dom:
        result["dom"] = dom
        log(f"DOM: title={str(dom.get('title',''))[:50]}, prices={dom.get('prices',[])[:4]}, imgs={len(dom.get('images',[]))}")
    else:
        log("DOM extraction returned None (timeout?)")

    # Screenshot
    try:
        page.screenshot(path="diag_ali_ru3_screenshot.png", full_page=False)
        log("Screenshot saved")
    except Exception as e:
        log(f"Screenshot: {e}")

    page.remove_listener("response", on_resp)
    result["mtop_intercepted"] = mtop_bodies
    result["rendered"] = rendered
    result["tmd_found"] = tmd_found
    result["phase"] = "done"
    ctx.close()
    pw.stop()

except Exception as e:
    result["phase"] = "error"
    result["errors"].append(f"{type(e).__name__}: {e}")
    result["log"].append(f"ERROR: {traceback.format_exc()}")
    log(f"ERROR: {e}")

save()
print(f"\n=== DONE: {result['phase']} ===")
print(f"rendered={result.get('rendered')}, tmd={result.get('tmd_found')}")
print(f"MTOP intercepted: {len(result.get('mtop_intercepted',[]))}")
md = result.get('mtop_direct',{})
print(f"MTOP direct: status={md.get('status','?')} len={md.get('len','?')}")
dom = result.get('dom',{})
print(f"DOM: title={str(dom.get('title',''))[:50]}, prices={dom.get('prices',[])[:4]}")
