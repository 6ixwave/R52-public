"""Диагностика AliExpress pw28: TMD-обход + post-TMD MTOP + DOM.

Улучшения vs pw27:
- Фикс NoneType crash
- После TMD: reload страницы (cookies x5sec уже стоят)
- Перехват ВСЕХ JSON-ответов (не только pdp.pc.query)
- Двойной цикл: 1st pass (TMD) → 2nd pass (data)
- Сохранение TMD-frame HTML для анализа
"""
import json
import re
import time
import os
import sys
from datetime import datetime, timezone

PRODUCTS = [
    "1005011813323763",
    "1005007379458743",
]

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile")

EXTRACT_JS = """
() => {
    const r = {};
    const h1 = document.querySelector('h1');
    r.h1 = h1 ? h1.textContent.trim().substring(0, 300) : null;
    const titleEl = document.querySelector('title');
    r.titleTag = titleEl ? titleEl.textContent.trim().substring(0, 300) : null;

    // Prices - aggressive
    r.priceElements = [];
    const pSels = ['[class*="price"]','[class*="Price"]','[data-pl*="price"]','[data-pl*="Price"]','[class*="sku"] [class*="price"]'];
    const seen = new Set();
    for (const s of pSels) {
        try {
            document.querySelectorAll(s).forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && t.length < 100 && /\\d/.test(t) && !seen.has(t)) {
                    seen.add(t);
                    r.priceElements.push({
                        tag: el.tagName,
                        cls: (el.className || '').substring(0, 100),
                        text: t.substring(0, 80),
                        dataPrice: el.getAttribute('data-price') || '',
                        ariaLabel: (el.getAttribute('aria-label') || '').substring(0, 80)
                    });
                }
            });
        } catch(e) {}
    }
    r.priceElements = r.priceElements.slice(0, 25);

    // data-price attrs
    r.dataPriceAttrs = [];
    document.querySelectorAll('[data-price],[data-activity-price],[data-original-price]').forEach(el => {
        r.dataPriceAttrs.push({
            price: el.getAttribute('data-price') || '',
            actPrice: el.getAttribute('data-activity-price') || '',
            origPrice: el.getAttribute('data-original-price') || ''
        });
    });
    r.dataPriceAttrs = r.dataPriceAttrs.slice(0, 15);

    // Images
    r.images = [];
    document.querySelectorAll('img').forEach(img => {
        const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
        if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
            && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
            && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif')
            && !src.includes('30x30') && !src.includes('48x48') && !src.includes('64x64')) {
            r.images.push(src.substring(0, 200));
        }
    });
    r.images = [...new Set(r.images)].slice(0, 20);

    // Availability
    r.availabilityTexts = [];
    const aSels = ['[class*="stock"]','[class*="availability"]','[data-pl="stock"]','[class*="buy-now"]','[class*="addToCart"]','button[class*="buy"]','[class*="purchase"]'];
    for (const s of aSels) {
        try {
            document.querySelectorAll(s).forEach(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t && t.length < 150) r.availabilityTexts.push(t);
            });
        } catch(e) {}
    }
    r.availabilityTexts = [...new Set(r.availabilityTexts)].slice(0, 10);

    // Breadcrumb
    const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"],nav[aria-label]');
    r.breadcrumb = bc ? bc.textContent.trim().substring(0, 300) : null;

    // Meta
    r.metas = {};
    document.querySelectorAll('meta[property],meta[name]').forEach(m => {
        const p = m.getAttribute('property') || m.getAttribute('name') || '';
        const c = m.getAttribute('content') || '';
        if (p && c) r.metas[p] = c.substring(0, 300);
    });

    // JSON-LD
    r.jsonld = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
    });

    // Window state vars
    r.windowState = {};
    for (const k of ['__INIT_DATA_STATE__','runParams','_page_config_','__INIT_DATA_CALLBACK__']) {
        try {
            const v = window[k];
            if (v && typeof v === 'object') {
                r.windowState[k] = JSON.stringify(v).substring(0, 12000);
            }
        } catch(e) {}
    }

    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
    return r;
}
"""

def safe_str(val, default="N/A", maxlen=80):
    if val is None:
        return default
    s = str(val)
    return s[:maxlen] if len(s) > maxlen else s

def wait_for_render(page, max_ticks=20, interval_ms=2000):
    """Wait until h1 or prices appear. Returns (rendered, last_check)."""
    last_check = {}
    for tick in range(max_ticks):
        page.wait_for_timeout(interval_ms)
        try:
            last_check = page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    const pEls = document.querySelectorAll('[class*="price"],[data-pl*="price"],[class*="Price"]');
                    const prices = [];
                    pEls.forEach(el => {
                        const t = (el.textContent||'').trim();
                        if (t && /\\d/.test(t) && t.length < 60) prices.push(t);
                    });
                    return {
                        h1: h1 ? h1.textContent.trim().substring(0, 200) : null,
                        prices: [...new Set(prices)].slice(0, 5),
                        bodyLen: document.body ? document.body.innerHTML.length : 0
                    };
                }
            """)
            if last_check.get("prices") or (last_check.get("h1") and len(last_check["h1"]) > 15):
                return True, last_check
            if tick % 4 == 0:
                print(f"    tick={tick*2}s: bodyLen={last_check.get('bodyLen',0)}, h1={safe_str(last_check.get('h1'),40)}")
        except:
            continue
    return False, last_check

def solve_tmd(page):
    """Try to solve TMD captcha. Returns True if solved."""
    print("  [TMD] Attempting to solve...")
    page.wait_for_timeout(3000)
    
    # Find TMD frame
    tmd_frame = None
    for f in page.frames:
        fu = (f.url or "").lower()
        if "tmd" in fu or "punish" in fu or "recaptcha" in fu:
            tmd_frame = f
            print(f"  [TMD] Frame: {fu[:100]}")
            break
    
    if not tmd_frame:
        print("  [TMD] No TMD frame found")
        return False
    
    # Save TMD frame HTML for analysis
    try:
        tmd_html = tmd_frame.content()
        tmd_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw28_tmd.html"
        with open(tmd_path, "w", encoding="utf-8") as f:
            f.write(tmd_html)
        print(f"  [TMD] Saved frame HTML: {len(tmd_html)} bytes")
    except Exception as e:
        print(f"  [TMD] Frame content error: {e}")
    
    # Try multiple click strategies
    clicked = False
    for attempt in range(3):
        try:
            tmd_frame.wait_for_load_state("domcontentloaded", timeout=5000)
            page.wait_for_timeout(2000)
            
            # Strategy 1: checkbox
            for sel in ('input[type="checkbox"]', '#recaptcha-anchor', '[role="checkbox"]', 
                        'span[role="checkbox"]', '.rc-button', '#sliders', '.nc_iconfont'):
                cb = tmd_frame.query_selector(sel)
                if cb:
                    box = cb.bounding_box()
                    if box and box["width"] > 0:
                        page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    else:
                        cb.click()
                    print(f"  [TMD] Clicked '{sel}' (attempt {attempt+1})")
                    clicked = True
                    break
            
            # Strategy 2: any clickable element in frame
            if not clicked:
                for sel in ('button', 'a', '[class*="btn"]', '[class*="verify"]', '[class*="confirm"]', 'div[onclick]'):
                    el = tmd_frame.query_selector(sel)
                    if el:
                        box = el.bounding_box()
                        if box and box["width"] > 0:
                            page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                            print(f"  [TMD] Clicked generic '{sel}' (attempt {attempt+1})")
                            clicked = True
                            break
            
            # Strategy 3: click center of frame
            if not clicked:
                # Get frame element position
                frame_el = page.query_selector('iframe[src*="tmd"],iframe[src*="punish"]')
                if frame_el:
                    box = frame_el.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                        print(f"  [TMD] Clicked frame center (attempt {attempt+1})")
                        clicked = True
            
            if clicked:
                # Wait for resolution
                for i in range(15):
                    page.wait_for_timeout(2000)
                    try:
                        check = page.evaluate("""
                            () => {
                                const h1 = document.querySelector('h1');
                                const pEls = document.querySelectorAll('[class*="price"],[class*="Price"]');
                                const prices = [];
                                pEls.forEach(el => {
                                    const t = (el.textContent||'').trim();
                                    if (t && /\\d/.test(t) && t.length < 50) prices.push(t);
                                });
                                // Check if TMD frame is gone
                                let tmdGone = true;
                                document.querySelectorAll('iframe').forEach(f => {
                                    if ((f.src||'').includes('tmd') || (f.src||'').includes('punish')) tmdGone = false;
                                });
                                return {h1: h1?h1.textContent.trim().substring(0,200):null, 
                                        prices:[...new Set(prices)].slice(0,5), 
                                        tmdGone, bodyLen: document.body?document.body.innerHTML.length:0};
                            }
                        """)
                        if check.get("tmdGone") and (check.get("prices") or (check.get("h1") and len(check["h1"]) > 15)):
                            print(f"  [TMD] Solved! Re-rendered at +{(i+1)*2}s")
                            return True
                        if check.get("prices"):
                            print(f"  [TMD] Prices appeared at +{(i+1)*2}s")
                            return True
                    except:
                        continue
                print(f"  [TMD] Attempt {attempt+1}: no re-render after 30s")
        except Exception as e:
            print(f"  [TMD] Attempt {attempt+1} error: {e}")
        page.wait_for_timeout(2000)
    
    return False

def main():
    from playwright.sync_api import sync_playwright
    
    os.makedirs(PROFILE_DIR, exist_ok=True)
    results = []
    
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-automation",
            ],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
            timezone_id="Europe/London",
            ignore_https_errors=True,
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            window.chrome = {runtime: {}};
            // Hide automation further
            const origQuery = window.navigator.userAgentData;
            if (origQuery) {
                Object.defineProperty(navigator, 'userAgentData', {get: () => ({...origQuery, brands: [{brand:'Chromium',version:'131'},{brand:'Google Chrome',version:'131'}], mobile: false, platform: 'Windows'})});
            }
        """)
        
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        
        # ─── WARM UP ───
        print("[WARM] Loading homepage...")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(6000)
            for sel in ('button:has-text("Accept")', 'button:has-text("Accept all")',
                        'button:has-text("Accetta")', '#onetrust-accept-btn-handler',
                        '[class*="cookie"] button', '[class*="consent"] button'):
                try:
                    btn = page.query_selector(sel)
                    if btn:
                        btn.click()
                        page.wait_for_timeout(2000)
                        break
                except:
                    pass
            page.evaluate("window.scrollTo(0, 300)")
            page.wait_for_timeout(4000)
            cookies = ctx.cookies()
            print(f"[WARM] Done. {len(cookies)} cookies.")
        except Exception as e:
            print(f"[WARM] Error: {e}")
        
        # ─── PRODUCTS ───
        for pid in PRODUCTS:
            print(f"\n{'='*60}")
            print(f"PRODUCT: {pid}")
            print(f"{'='*60}")
            
            xhr_bodies = []
            
            def on_response(resp):
                try:
                    u = resp.url
                    ct = resp.headers.get("content-type", "")
                    is_interesting = (
                        ("json" in ct and resp.status == 200) or
                        ("mtop" in u.lower() and resp.status == 200) or
                        ("pdp" in u.lower() and resp.status == 200)
                    )
                    if is_interesting:
                        body = ""
                        try:
                            body = resp.text()
                        except:
                            pass
                        if len(body) > 50:
                            xhr_bodies.append({
                                "url": u[:300],
                                "status": resp.status,
                                "len": len(body),
                                "ct": ct[:100],
                                "body": body[:20000],
                            })
                            tag = "MTOP" if "mtop" in u.lower() else "JSON"
                            print(f"  [{tag}] {u[:80]}... ({len(body)}b)")
                except:
                    pass
            
            page.on("response", on_response)
            url = f"https://www.aliexpress.com/item/{pid}.html"
            
            # ─── PASS 1: Initial load (may trigger TMD) ───
            t0 = time.monotonic()
            print("  [PASS 1] Initial load...")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                status = resp.status if resp else 0
                print(f"  [PASS 1] HTTP {status}, final={page.url[:100]}")
            except Exception as e:
                print(f"  [PASS 1] Error: {e}")
                page.remove_listener("response", on_response)
                results.append({"product_id": pid, "error": str(e)})
                continue
            
            rendered, last_check = wait_for_render(page, max_ticks=15, interval_ms=2000)
            
            if not rendered:
                # Check for TMD
                has_tmd = False
                for f in page.frames:
                    fu = (f.url or "").lower()
                    if "tmd" in fu or "punish" in fu:
                        has_tmd = True
                        break
                
                if has_tmd:
                    tmd_solved = solve_tmd(page)
                    if tmd_solved:
                        rendered = True
                    else:
                        print("  [PASS 1] TMD not solved, trying reload...")
                else:
                    print(f"  [PASS 1] No TMD but not rendered. bodyLen={last_check.get('bodyLen',0)}")
            
            # ─── PASS 2: Reload if needed (cookies from TMD should help) ───
            if not rendered:
                print("  [PASS 2] Reloading page (post-TMD cookies)...")
                page.wait_for_timeout(2000)
                try:
                    resp2 = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                    status2 = resp2.status if resp2 else 0
                    print(f"  [PASS 2] HTTP {status2}")
                except Exception as e:
                    print(f"  [PASS 2] Error: {e}")
                
                rendered, last_check = wait_for_render(page, max_ticks=18, interval_ms=2000)
                if rendered:
                    print(f"  [PASS 2] Rendered! h1={safe_str(last_check.get('h1'),60)}, prices={last_check.get('prices',[])}")
            
            elapsed = time.monotonic() - t0
            print(f"  Elapsed: {elapsed:.1f}s, rendered={rendered}")
            
            # ─── EXTRACT DOM ───
            print("  Extracting DOM...")
            try:
                dom = page.evaluate(EXTRACT_JS)
            except Exception as e:
                print(f"  DOM extract error: {e}")
                dom = {"error": str(e)}
            
            page.remove_listener("response", on_response)
            
            # ─── SUMMARY ───
            print(f"\n  --- SUMMARY ---")
            print(f"  h1: {safe_str(dom.get('h1'))}")
            print(f"  titleTag: {safe_str(dom.get('titleTag'))}")
            pe = dom.get('priceElements', [])
            print(f"  priceElements: {len(pe)}")
            for p in pe[:5]:
                print(f"    [{safe_str(p.get('cls'),40)}] {safe_str(p.get('text'),50)}")
            dp = dom.get('dataPriceAttrs', [])
            print(f"  dataPriceAttrs: {len(dp)}")
            for d in dp[:5]:
                print(f"    price={d.get('price','')} act={d.get('actPrice','')} orig={d.get('origPrice','')}")
            print(f"  images: {len(dom.get('images', []))}")
            print(f"  availability: {dom.get('availabilityTexts', [])[:3]}")
            print(f"  breadcrumb: {safe_str(dom.get('breadcrumb'),80)}")
            print(f"  bodyLen: {dom.get('bodyLen', 0)}")
            ws = dom.get('windowState', {})
            print(f"  windowState keys: {list(ws.keys())}")
            print(f"  XHR captured: {len(xhr_bodies)}")
            for x in xhr_bodies[:5]:
                print(f"    {x['url'][:70]} ({x['len']}b) ct={x.get('ct','')[:40]}")
            
            # ─── PARSE MTOP from XHR ───
            mtop_data = None
            for x in xhr_bodies:
                if "pdp.pc.query" in x["url"] and x["len"] > 500:
                    try:
                        clean = x["body"].strip()
                        m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', clean, re.DOTALL)
                        if m:
                            clean = m.group(1)
                        d = json.loads(clean)
                        ret = d.get("ret", [])
                        if any("SUCCESS" in str(r) for r in ret):
                            mtop_data = d
                            print(f"  [MTOP] SUCCESS! data_keys={list(d.get('data',{}).keys())[:10]}")
                            break
                    except (json.JSONDecodeError, ValueError):
                        continue
            
            r = {
                "product_id": pid,
                "url": url,
                "final_url": page.url,
                "http_status": status,
                "elapsed": round(elapsed, 1),
                "rendered": rendered,
                "xhr_count": len(xhr_bodies),
                "xhr_bodies": xhr_bodies[:15],
                "dom": dom,
                "mtop_success": mtop_data is not None,
                "mtop_data": mtop_data,
            }
            results.append(r)
            time.sleep(3)
        
        ctx.close()
    
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw28",
        "results": results,
    }
    out_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw28_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n\nSaved: {out_path} ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    main()
