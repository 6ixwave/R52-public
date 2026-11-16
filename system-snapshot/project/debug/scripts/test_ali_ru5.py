"""Диагностика AliExpress RU v5: ru.aliexpress.com + TMD punish redirect + SSR + MTOP direct."""
import json, os, re, sys, time, traceback
from datetime import datetime, timezone

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_ali_ru5_result.json")
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pw_ali_profile_ru5")
PRODUCT_ID = "1005013496905897"
# Пробуем ru.aliexpress.com
PRODUCT_URL = f"https://ru.aliexpress.com/item/{PRODUCT_ID}.html"
HOME_URL = "https://ru.aliexpress.com/"

result = {
    "pid": PRODUCT_ID,
    "url": PRODUCT_URL,
    "ts": datetime.now(timezone.utc).isoformat(),
    "phase": "init",
    "log": [],
    "mtop_all": [],
    "mtop_direct": None,
    "ssr_state": None,
    "dom": {},
    "rendered": False,
    "tmd_found": False,
    "tmd_solved": False,
    "tmd_punish_url": None,
}

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    result["log"].append(line)
    print(line, flush=True)

def save():
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE, exist_ok=True)

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        PROFILE,
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="ru-RU",
        viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Moscow",
        ignore_https_errors=True,
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
    """)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    log("Browser OK (ru locale)")

    # ─── Прогрев ───
    log("Warming session...")
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)
    # Cookie banner
    for sel in ('button:has-text("Принять")','button:has-text("Accept")','[class*="cookie"] button','#onetrust-accept-btn-handler','button:has-text("OK")'):
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                log(f"Cookie: {sel}")
                page.wait_for_timeout(2000)
                break
        except: pass
    page.evaluate("window.scrollTo(0, 300)")
    page.wait_for_timeout(3000)
    cookies = ctx.cookies()
    log(f"Warm: {len(cookies)} cookies")

    # ─── MTOP intercept ───
    mtop_bodies = []
    def on_resp(resp):
        try:
            u = resp.url
            if "mtop" in u.lower() and ("pdp" in u.lower() or "query" in u.lower()):
                body = ""
                try:
                    body = resp.text()
                except: pass
                mtop_bodies.append({"url": u[:500], "status": resp.status, "len": len(body), "body": body[:5000]})
                log(f"MTOP intercept: {len(body)}b status={resp.status}")
        except: pass
    page.on("response", on_resp)

    # ─── Navigate to product ───
    log(f"Navigating: {PRODUCT_URL}")
    resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
    log(f"HTTP {resp.status if resp else 0}")
    page.wait_for_timeout(5000)

    # ─── Wait for render / detect TMD ───
    rendered = False
    tmd_found = False
    for tick in range(15):
        page.wait_for_timeout(1000)
        try:
            st = page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    const prices = [];
                    const sels = ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                        '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]','[class*="price-row"]',
                        '[class*="sku-price"]','[class*="Price"]'];
                    for (const s of sels) {
                        try {
                            document.querySelectorAll(s).forEach(el => {
                                const t = (el.textContent||'').trim();
                                if (t && t.length < 60 && /\\d/.test(t)) prices.push(t);
                            });
                        } catch(e) {}
                    }
                    const iframes = [];
                    document.querySelectorAll('iframe').forEach(f => {
                        iframes.push((f.src||'').substring(0,400));
                    });
                    return {
                        h1: h1?h1.textContent.trim().substring(0,300):null,
                        prices:[...new Set(prices)].slice(0,10),
                        iframes,
                        bodyLen: document.body?document.body.innerHTML.length:0,
                        title: document.title
                    };
                }
            """)
        except: continue

        log(f"tick={tick}: h1={str(st.get('h1'))[:60]} prices={st.get('prices',[])[:3]} ifr={len(st.get('iframes',[]))} body={st.get('bodyLen')}")

        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
            rendered = True
            log(f"RENDERED at tick={tick}")
            break

        for fr in st.get("iframes", []):
            if "punish" in fr or "tmd" in fr.lower() or "recaptcha" in fr.lower():
                tmd_found = True
                result["tmd_punish_url"] = fr
                log(f"TMD iframe: {fr[:200]}")
                break
        if tmd_found:
            break

    # ─── TMD solve (improved) ───
    if tmd_found and not rendered:
        log("Solving TMD...")
        page.wait_for_timeout(3000)
        # Try clicking in all frames
        clicked = False
        for f in page.frames:
            try:
                fu = f.url or ""
                if "recaptcha" in fu or "punish" in fu or "bframe" in fu:
                    f.wait_for_load_state("domcontentloaded", timeout=8000)
                    page.wait_for_timeout(2000)
                    for sel in ('input[type="checkbox"]', '#recaptcha-anchor', '[role="checkbox"]', '.recaptcha-checkbox'):
                        cb = f.query_selector(sel)
                        if cb:
                            box = cb.bounding_box()
                            if box and box["width"] > 0:
                                page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                            else:
                                cb.click()
                            log(f"TMD click: {sel} in frame {fu[:100]}")
                            clicked = True
                            break
                    if clicked: break
            except: continue

        # Also try main page
        if not clicked:
            for sel in ('#recaptcha-anchor', 'input[type="checkbox"]', '[role="checkbox"]'):
                try:
                    cb = page.query_selector(sel)
                    if cb:
                        cb.click()
                        log(f"TMD click main: {sel}")
                        clicked = True
                        break
                except: pass

        # Wait for resolution
        log("Waiting for TMD resolution (30s)...")
        for i in range(30):
            page.wait_for_timeout(1000)
            try:
                check = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const pEls = document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"],[class*="Price"]');
                        const prices = [];
                        pEls.forEach(el => { const t=(el.textContent||'').trim(); if(t&&/\\d/.test(t)&&t.length<50) prices.push(t); });
                        const iframes = document.querySelectorAll('iframe');
                        let tmdGone = true;
                        iframes.forEach(f => { if((f.src||'').includes('punish')||(f.src||'').includes('recaptcha')) tmdGone=false; });
                        return {h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,6), tmdGone, bodyLen: document.body?document.body.innerHTML.length:0};
                    }
                """)
                if check.get("prices") or (check.get("h1") and len(check["h1"]) > 15):
                    log(f"TMD solved, rendered at {i+1}s")
                    rendered = True
                    result["tmd_solved"] = True
                    break
                if check.get("tmdGone") and i > 5:
                    log(f"TMD iframe gone at {i+1}s, checking...")
                    page.wait_for_timeout(2000)
            except: continue

        if not rendered:
            log("TMD: trying reload after solve")
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)
            try:
                st2 = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const prices = [];
                        document.querySelectorAll('[data-pl="price"],[class*="pdp-price"],span[class*="price"]').forEach(el => {
                            const t=(el.textContent||'').trim(); if(t&&/\\d/.test(t)&&t.length<50) prices.push(t);
                        });
                        return {h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,6), bodyLen: document.body?document.body.innerHTML.length:0};
                    }
                """)
                log(f"Post-reload: h1={str(st2.get('h1'))[:60]} prices={st2.get('prices',[])[:3]} body={st2.get('bodyLen')}")
                if st2.get("prices") or (st2.get("h1") and len(st2["h1"]) > 15):
                    rendered = True
                    result["tmd_solved"] = True
            except: pass

    # ─── Direct MTOP API call via page.evaluate ───
    log("Direct MTOP API call...")
    try:
        mtop_result = page.evaluate("""
            async () => {
                const productId = window.__productId || document.querySelector('meta[property="og:url"]')?.content?.match(/(\\d{10,})/)?.[1] || '';
                const t = Date.now();
                const url = `https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/?jsv=2.5.1&appKey=12574478&t=${t}&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp&v=1.0&dataType=json&data=${encodeURIComponent(JSON.stringify({productId: window.location.href.match(/(\\d{10,})/)?.[1] || ''}))}`;
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    const text = await r.text();
                    return {status: r.status, len: text.length, body: text.substring(0, 10000)};
                } catch(e) {
                    return {error: e.message};
                }
            }
        """)
        if mtop_result:
            result["mtop_direct"] = mtop_result
            log(f"MTOP direct: status={mtop_result.get('status')} len={mtop_result.get('len')}")
            if mtop_result.get("body"):
                log(f"MTOP direct body[:200]={mtop_result['body'][:200]}")
    except Exception as e:
        log(f"MTOP direct error: {e}")

    # ─── SSR state extraction ───
    log("Extracting SSR state...")
    try:
        ssr = page.evaluate("""
            () => {
                const r = {};
                try {
                    if (window.__INIT_DATA_STATE__) r.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0, 20000);
                } catch(e) { r.initStateErr = e.message; }
                try {
                    if (window.runParams) r.runParams = JSON.stringify(window.runParams).substring(0, 15000);
                } catch(e) { r.runParamsErr = e.message; }
                try {
                    if (window.__NEXT_DATA__) r.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0, 10000);
                } catch(e) {}
                // Look for any large JSON in scripts
                r.scripts = [];
                document.querySelectorAll('script').forEach(s => {
                    const t = s.textContent || '';
                    if (t.length > 500 && (t.includes('price') || t.includes('product') || t.includes('sku'))) {
                        r.scripts.push(t.substring(0, 3000));
                    }
                });
                r.scripts = r.scripts.slice(0, 5);
                return r;
            }
        """)
        result["ssr_state"] = ssr
        log(f"SSR: initState={'Y' if ssr.get('initState') else 'N'} runParams={'Y' if ssr.get('runParams') else 'N'} scripts={len(ssr.get('scripts',[]))}")
    except Exception as e:
        log(f"SSR error: {e}")

    # ─── Full DOM extraction ───
    log("Full DOM extraction...")
    try:
        dom_data = page.evaluate("""
            () => {
                const r = {};
                const h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                if (!r.title) {
                    const t = document.querySelector('title');
                    if (t) r.title = t.textContent.replace(/\\s*[|\\-\\u2013\\u2014].*$/,'').trim();
                }
                // OG
                r.meta = {};
                document.querySelectorAll('meta[property],meta[name]').forEach(m => {
                    const p = m.getAttribute('property')||m.getAttribute('name')||'';
                    const c = m.getAttribute('content')||'';
                    if (p.startsWith('og:')||p.includes('product')||p.includes('price')||p.includes('description')) r.meta[p]=c.substring(0,500);
                });
                // Prices
                r.prices = [];
                const pSels = ['[data-pl="price"]','[class*="price--current"]','[class*="Price--current"]',
                    '[class*="pdp-price"]','[class*="price-current"]','span[class*="price"]',
                    '[class*="sku-price"]','[class*="priceRow"]','[class*="price-row"]','[data-pl*="price"]',
                    '[class*="Price"]','[class*="price"]'];
                for (const s of pSels) {
                    try {
                        document.querySelectorAll(s).forEach(el => {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 80 && /\\d/.test(t)) r.prices.push(t);
                        });
                    } catch(e) {}
                }
                document.querySelectorAll('[data-price],[data-activity-price],[data-original-price]').forEach(el => {
                    const v = el.getAttribute('data-price')||el.getAttribute('data-activity-price')||el.getAttribute('data-original-price');
                    if (v) r.prices.push(v);
                });
                r.prices = [...new Set(r.prices)].slice(0,20);
                // Images
                r.images = [];
                document.querySelectorAll('img').forEach(img => {
                    const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
                    if (src && (src.includes('alicdn')||src.includes('aliexpress-media'))
                        && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                        && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif')
                        && !src.includes('48x48') && !src.includes('220x220')) {
                        r.images.push(src);
                    }
                });
                r.images = [...new Set(r.images)].slice(0,20);
                // JSON-LD
                r.jsonld = [];
                document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                    try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                });
                // Availability
                r.availability = null;
                const aSels = ['[class*="stock"]','[class*="availability"]','[data-pl="stock"]','[class*="pdp-stock"]'];
                for (const s of aSels) {
                    try {
                        document.querySelectorAll(s).forEach(el => {
                            const t = (el.textContent||'').trim().toLowerCase();
                            if (t.length < 100) {
                                if (t.includes('в наличии')||t.includes('in stock')||t.includes('disponib')||t.includes('available')) r.availability='IN_STOCK';
                                if (t.includes('нет в наличии')||t.includes('out of stock')||t.includes('non disponib')||t.includes('esaurito')) r.availability='OUT_OF_STOCK';
                            }
                        });
                    } catch(e) {}
                    if (r.availability) break;
                }
                // Breadcrumb
                const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"]');
                r.category = bc ? bc.textContent.trim().substring(0,300) : null;
                r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                return r;
            }
        """)
        result["dom"] = dom_data
        log(f"DOM: title={str(dom_data.get('title'))[:60]} prices={dom_data.get('prices',[])[:3]} imgs={len(dom_data.get('images',[]))} jsonld={len(dom_data.get('jsonld',[]))}")
    except Exception as e:
        log(f"DOM error: {e}")

    # ─── Save full HTML for analysis ───
    try:
        html = page.content()
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_ali_ru5_page.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"HTML saved: {len(html)} bytes")
    except Exception as e:
        log(f"HTML save error: {e}")

    # ─── Screenshot ───
    try:
        page.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_ali_ru5.png"), full_page=False)
        log("Screenshot saved")
    except: pass

    # ─── Summary ───
    result["rendered"] = rendered
    result["tmd_found"] = tmd_found
    result["mtop_all"] = mtop_bodies
    result["phase"] = "done"
    log(f"DONE: rendered={rendered} tmd={tmd_found} tmd_solved={result['tmd_solved']} mtop={len(mtop_bodies)}")

    page.remove_listener("response", on_resp)
    ctx.close()
    pw.stop()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        result["phase"] = "error"
        result["log"].append(f"ERROR: {e}\n{traceback.format_exc()}")
        print(f"ERROR: {e}", flush=True)
        traceback.print_exc()
    finally:
        save()
        print(f"Saved: {OUT}", flush=True)
