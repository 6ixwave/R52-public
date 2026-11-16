"""Диагностика AliExpress pw30: обход reCAPTCHA Enterprise TMD.

Ключевое отличие от pw29:
- TMD = Google reCAPTCHA Enterprise (не слайдер)
- Открываем TMD iframe URL в отдельной вкладке
- Кликаем чекбокс reCAPTCHA / ждём v3 callback
- После TMD: x5sec cookie → MTOP проходит
- Извлечение: MTOP + DOM + meta og:* + _page_config_
"""
import json
import re
import time
import os
import sys
import traceback
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile")
OUT_PATH = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw30_result.json"
COOKIES_PATH = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw30_cookies.json"

BUDGET_START = time.monotonic()
BUDGET_TOTAL = 100

def elapsed():
    return time.monotonic() - BUDGET_START

def remaining():
    return max(0, BUDGET_TOTAL - elapsed())

def log(msg):
    print(f"[{elapsed():.1f}s] {msg}", flush=True)

# ─── JS: извлечение данных (агрессивный) ───
EXTRACT_JS = """
() => {
    const r = {};

    // Title: h1 → og:title → title tag
    const h1 = document.querySelector('h1');
    r.h1 = h1 ? h1.textContent.trim().substring(0, 500) : null;
    const ogTitle = document.querySelector('meta[property="og:title"]');
    r.ogTitle = ogTitle ? (ogTitle.getAttribute('content') || '').trim() : null;
    const titleEl = document.querySelector('title');
    r.titleTag = titleEl ? titleEl.textContent.trim().substring(0, 500) : null;

    // Prices: multi-source
    r.prices = [];
    const seen = new Set();

    // data-attrs
    document.querySelectorAll('[data-price],[data-activity-price],[data-original-price]').forEach(el => {
        const p = el.getAttribute('data-price') || '';
        const ap = el.getAttribute('data-activity-price') || '';
        const op = el.getAttribute('data-original-price') || '';
        if ((p || ap || op) && !seen.has(p + ap + op)) {
            seen.add(p + ap + op);
            r.prices.push({src: 'data-attr', price: p, act: ap, orig: op, text: (el.textContent||'').trim().substring(0,80)});
        }
    });

    // class-based
    const pSels = ['[class*="price"]','[class*="Price"]','[data-pl*="price"]','[class*="money"]','[class*="Money"]','[class*="amount"]'];
    for (const sel of pSels) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && t.length < 80 && /\\d+[.,]?\\d{0,2}/.test(t) && !seen.has(t)) {
                    seen.add(t);
                    r.prices.push({src: 'dom', sel: sel.substring(0,40), cls: (el.className||'').substring(0,60), text: t.substring(0,60)});
                }
            });
        } catch(e) {}
    }
    r.prices = r.prices.slice(0, 30);

    // Currency
    r.currencies = [];
    const curRe = /[€$£¥₴₽]|EUR|USD|GBP|CNY|INR/g;
    const bodyText = document.body ? document.body.textContent.substring(0, 30000) : '';
    const curMatches = bodyText.match(curRe);
    if (curMatches) r.currencies = [...new Set(curMatches)].slice(0, 10);

    // Meta tags
    r.metas = {};
    document.querySelectorAll('meta[property],meta[name]').forEach(m => {
        const p = m.getAttribute('property') || m.getAttribute('name') || '';
        const c = (m.getAttribute('content') || '').trim();
        if (p && c) r.metas[p] = c.substring(0, 400);
    });

    // JSON-LD
    r.jsonld = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try { r.jsonld.push(JSON.parse(s.textContent)); }
        catch(e) { r.jsonld.push({err: e.message, raw: s.textContent.substring(0, 300)}); }
    });

    // Window state
    r.windowState = {};
    for (const k of ['__INIT_DATA_STATE__','runParams','_page_config_','__INIT_DATA_CALLBACK__','__NEXT_DATA__','pageData','productData']) {
        try {
            const v = window[k];
            if (v !== undefined && v !== null) {
                r.windowState[k] = (typeof v === 'string' ? v : JSON.stringify(v)).substring(0, 20000);
            }
        } catch(e) {}
    }

    // Images (product, filter small)
    r.images = [];
    document.querySelectorAll('img').forEach(img => {
        const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
        if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
            && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
            && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif')
            && !/\\/(30x30|48x48|64x64|110x42|42x60)\\./.test(src)) {
            r.images.push(src.substring(0, 250));
        }
    });
    r.images = [...new Set(r.images)].slice(0, 25);

    // og:image specifically
    const ogImg = document.querySelector('meta[property="og:image"]');
    r.ogImage = ogImg ? (ogImg.getAttribute('content') || '').trim() : null;

    // Availability
    r.availability = [];
    const aSels = ['[class*="stock"]','[class*="availability"]','[class*="buy-now"]','[class*="addToCart"]','button[class*="buy"]','[class*="sold-out"]','[class*="out-of-stock"]'];
    for (const s of aSels) {
        try {
            document.querySelectorAll(s).forEach(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t && t.length < 150) r.availability.push(t);
            });
        } catch(e) {}
    }
    r.availability = [...new Set(r.availability)].slice(0, 10);

    // Breadcrumb
    const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"],nav[aria-label]');
    r.breadcrumb = bc ? bc.textContent.trim().substring(0, 300) : null;

    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
    return r;
}
"""

# ─── JS: dismiss popups ───
DISMISS_JS = """
() => {
    let closed = [];
    const sels = [
        '#onetrust-accept-btn-handler',
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("Accetta")',
        'button:has-text("OK")',
        '[class*="cookie"] button[class*="accept"]',
        '[class*="consent"] button[class*="accept"]',
        'button:has-text("Continue")',
        '[class*="modal"] [class*="close"]',
        '[aria-label="Close"]',
    ];
    for (const sel of sels) {
        try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) { el.click(); closed.push(sel); }
        } catch(e) {}
    }
    return { closed };
}
"""

# ─── JS: solve reCAPTCHA in TMD page ───
SOLVE_RECAPTCHA_JS = """
() => {
    const r = { step: 'init', details: {} };

    // 1. Check for reCAPTCHA widget container
    const rcWrap = document.querySelector('.g-recaptcha, #recaptcha, .recaptcha-widget, [class*="recaptcha"]');
    r.details.hasWidget = !!rcWrap;
    if (rcWrap) {
        r.details.widgetClass = (rcWrap.className || '').substring(0, 100);
        r.details.widgetHTML = rcWrap.innerHTML.substring(0, 500);
    }

    // 2. Check for iframe (reCAPTCHA v2 loads in iframe)
    const iframes = document.querySelectorAll('iframe');
    r.details.iframes = [];
    iframes.forEach(f => {
        r.details.iframes.push({
            src: (f.src || '').substring(0, 200),
            title: f.title || '',
            w: f.width, h: f.height
        });
    });

    // 3. Try to find and click checkbox
    let clicked = false;
    // v2 checkbox: usually inside iframe, but try direct first
    const checkboxSels = [
        '#recaptcha-anchor',
        'input[type="checkbox"]',
        'span[role="checkbox"]',
        '[class*="recaptcha-checkbox"]',
        '.rc-button',
        '#sliders',
        '.nc_iconfont',
        'div[style*="cursor: pointer"]',
    ];
    for (const sel of checkboxSels) {
        try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                el.click();
                clicked = true;
                r.details.clicked = sel;
                break;
            }
        } catch(e) {}
    }

    // 4. Check for v3 (invisible) — look for grecaptcha
    r.details.hasGrecaptcha = typeof window.grecaptcha !== 'undefined';
    if (typeof window.grecaptcha !== 'undefined') {
        r.details.grecaptchaKeys = Object.keys(window.grecaptcha);
    }

    // 5. Check for LWSC / epssw
    r.details.hasEpssw = typeof window.epssw !== 'undefined';
    r.details.hasLWSC = typeof window.LWSC !== 'undefined';

    // 6. Check loading state
    const loading = document.querySelector('.loading, #loading, [class*="loading"]');
    r.details.isLoading = !!loading && loading.offsetParent !== null;

    // 7. Check sessionStorage for token (means callback already fired)
    try {
        const token = sessionStorage.getItem('bx-recaptcha-token');
        r.details.sessionToken = token ? token.substring(0, 200) : null;
    } catch(e) {}

    // 8. Check for redirect
    r.details.location = location.href.substring(0, 200);

    r.step = clicked ? 'clicked' : (r.details.hasGrecaptcha ? 'v3-waiting' : 'waiting');
    return r;
}
"""

def solve_tmd_recaptcha(ctx, tmd_iframe_url, budget_s=35):
    """Open TMD iframe URL in new page, solve reCAPTCHA, wait for redirect."""
    log(f"  [TMD] Opening iframe URL in new page ({len(tmd_iframe_url)} chars)...")
    tmd_page = ctx.new_page()
    tmd_page.set_default_timeout(15000)

    try:
        tmd_page.goto(tmd_iframe_url, wait_until="domcontentloaded", timeout=20000)
        log(f"  [TMD] Loaded. URL: {tmd_page.url[:100]}")
    except Exception as e:
        log(f"  [TMD] Load error: {e}")
        # Try opening the base punish URL
        try:
            tmd_page.goto(tmd_iframe_url, wait_until="load", timeout=20000)
        except Exception as e2:
            log(f"  [TMD] 2nd load error: {e2}")
            tmd_page.close()
            return False

    # Wait for reCAPTCHA scripts to load
    tmd_page.wait_for_timeout(4000)

    # Check page state
    try:
        state = tmd_page.evaluate(SOLVE_RECAPTCHA_JS)
        log(f"  [TMD] State: step={state.get('step')}, widget={state.get('details',{}).get('hasWidget')}, "
            f"iframes={len(state.get('details',{}).get('iframes',[]))}, "
            f"grecaptcha={state.get('details',{}).get('hasGrecaptcha')}, "
            f"loading={state.get('details',{}).get('isLoading')}")
        for k, v in state.get('details', {}).items():
            if k not in ('iframes', 'widgetHTML'):
                log(f"    {k}: {str(v)[:100]}")
        for i, f in enumerate(state.get('details', {}).get('iframes', [])):
            log(f"    iframe[{i}]: {f.get('src','')[:80]}")
    except Exception as e:
        log(f"  [TMD] State eval error: {e}")

    # Try clicking checkbox (might be in nested iframe)
    try:
        # Check if there's a reCAPTCHA iframe inside the page
        for frame in tmd_page.frames:
            fu = (frame.url or "").lower()
            if "recaptcha" in fu or "google.com" in fu:
                log(f"  [TMD] Found reCAPTCHA frame: {fu[:100]}")
                try:
                    frame.wait_for_load_state("domcontentloaded", timeout=8000)
                    # Try clicking in this frame
                    for sel in ('#recaptcha-anchor', 'input[type="checkbox"]', 'span[role="checkbox"]', '.rc-button'):
                        el = frame.query_selector(sel)
                        if el:
                            box = el.bounding_box()
                            if box and box["width"] > 0:
                                tmd_page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                            else:
                                el.click()
                            log(f"  [TMD] Clicked '{sel}' in reCAPTCHA frame")
                            break
                except Exception as e:
                    log(f"  [TMD] Frame click error: {e}")
    except Exception as e:
        log(f"  [TMD] Frame scan error: {e}")

    # Wait for resolution: redirect or token in sessionStorage
    deadline = time.monotonic() + budget_s
    solved = False
    while time.monotonic() < deadline:
        tmd_page.wait_for_timeout(2000)
        try:
            # Check if page redirected (TMD solved)
            cur_url = tmd_page.url
            if "third_validate_close" in cur_url or "punish" not in cur_url.lower():
                log(f"  [TMD] Redirected! URL: {cur_url[:100]}")
                solved = True
                break

            # Check sessionStorage for token
            token = tmd_page.evaluate("""
                () => {
                    try { return sessionStorage.getItem('bx-recaptcha-token'); }
                    catch(e) { return null; }
                }
            """)
            if token:
                log(f"  [TMD] Token in sessionStorage: {token[:80]}...")
                solved = True
                break

            # Check if loading spinner is gone and page changed
            page_state = tmd_page.evaluate("""
                () => {
                    const loading = document.querySelector('.loading, #loading');
                    const bodyText = document.body ? document.body.textContent.trim().substring(0, 200) : '';
                    return { loading: loading ? loading.offsetParent !== null : false, bodyText };
                }
            """)
            if not page_state.get('loading') and page_state.get('bodyText'):
                log(f"  [TMD] Loading gone. Body: {page_state['bodyText'][:80]}")
                # Might be solved or might be a different state
                if "success" in page_state['bodyText'].lower() or "redirect" in page_state['bodyText'].lower():
                    solved = True
                    break
        except Exception as e:
            if "closed" in str(e).lower() or "crashed" in str(e).lower():
                log(f"  [TMD] Page closed/crashed: {e}")
                break
            continue

    # Save TMD page state for debugging
    try:
        tmd_html = tmd_page.content()
        tmd_html_path = OUT_PATH.replace("_result.json", "_tmd_page.html")
        with open(tmd_html_path, "w", encoding="utf-8") as f:
            f.write(tmd_html)
        log(f"  [TMD] Saved page HTML: {len(tmd_html)}b")
    except:
        pass

    # Save cookies from TMD page
    try:
        tmd_cookies = ctx.cookies()
        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(tmd_cookies, f, ensure_ascii=False, indent=2)
        log(f"  [TMD] Saved {len(tmd_cookies)} cookies")
        # Check for x5sec
        x5 = [c for c in tmd_cookies if 'x5' in c['name'].lower() or 'sec' in c['name'].lower()]
        for c in x5:
            log(f"    Cookie: {c['name']}={c['value'][:50]}...")
    except:
        pass

    tmd_page.close()
    return solved

def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    url = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
    result = {
        "product_id": PRODUCT_ID,
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw30",
    }

    xhr_bodies = []
    tmd_iframe_url = None

    def on_response(resp):
        nonlocal tmd_iframe_url
        try:
            u = resp.url
            ct = resp.headers.get("content-type", "")
            interesting = (
                ("json" in ct and resp.status == 200) or
                ("mtop" in u.lower() and resp.status == 200) or
                ("pdp" in u.lower() and resp.status == 200)
            )
            if interesting:
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
                        "body": body[:25000],
                    })
                    tag = "MTOP" if "mtop" in u.lower() else "JSON"
                    log(f"  [{tag}] {u[:70]}... ({len(body)}b)")

                    # Extract TMD iframe URL from MTOP response
                    if "FAIL_SYS_USER_VALIDATE" in body and "punish" in body:
                        m = re.search(r'"url"\s*:\s*"([^"]+)"', body)
                        if m:
                            tmd_iframe_url = m.group(1)
                            log(f"  [MTOP] TMD URL extracted: {tmd_iframe_url[:80]}...")
        except:
            pass

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
        """)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ─── PHASE 1: WARMUP (5s) ───
        log("[P1] Warmup...")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(2000)
            try:
                page.evaluate(DISMISS_JS)
            except:
                pass
            page.wait_for_timeout(1000)
            cookies = ctx.cookies()
            log(f"[P1] Done. {len(cookies)} cookies. {elapsed():.1f}s")
        except Exception as e:
            log(f"[P1] Error: {e}")

        # ─── PHASE 2: LOAD PRODUCT (catch TMD) ───
        page.on("response", on_response)
        log(f"[P2] Loading product...")
        http_status = 0
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            http_status = resp.status if resp else 0
            log(f"[P2] HTTP {http_status}, final={page.url[:100]}")
        except Exception as e:
            log(f"[P2] Error: {e}")
            result["load_error"] = str(e)

        # Wait for MTOP to fire and potentially trigger TMD
        log("[P2] Waiting for MTOP/TMD...")
        for tick in range(10):  # 10 × 1.5s = 15s
            page.wait_for_timeout(1500)
            if tmd_iframe_url:
                log(f"[P2] TMD URL found at tick={tick}")
                break
            # Also check if page rendered (no TMD needed)
            try:
                check = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        return { h1: h1 ? h1.textContent.trim().substring(0,100) : null,
                                 bodyLen: document.body ? document.body.innerHTML.length : 0 };
                    }
                """)
                if check.get('h1') and len(check['h1']) > 15:
                    log(f"[P2] Rendered without TMD! h1={check['h1'][:60]}")
                    break
            except:
                continue

        page.remove_listener("response", on_response)

        # ─── PHASE 3: SOLVE TMD ───
        tmd_solved = False
        if tmd_iframe_url:
            log(f"[P3] TMD detected. Solving...")
            # Dismiss any popups first
            try:
                page.evaluate(DISMISS_JS)
            except:
                pass
            tmd_solved = solve_tmd_recaptcha(ctx, tmd_iframe_url, budget_s=min(35, int(remaining())))
            log(f"[P3] TMD solved: {tmd_solved}")
        else:
            log("[P3] No TMD URL found. Checking if rendered...")
            try:
                check = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const pEls = document.querySelectorAll('[class*="price"],[class*="Price"]');
                        const prices = [];
                        pEls.forEach(el => {
                            const t = (el.textContent||'').trim();
                            if (t && /\\d/.test(t) && t.length < 60) prices.push(t);
                        });
                        return {h1: h1?h1.textContent.trim().substring(0,200):null,
                                prices:[...new Set(prices)].slice(0,5),
                                bodyLen: document.body?document.body.innerHTML.length:0};
                    }
                """)
                log(f"[P3] State: h1={str(check.get('h1',''))[:60]}, prices={check.get('prices',[])}, bodyLen={check.get('bodyLen',0)}")
            except:
                pass

        # ─── PHASE 4: RELOAD (post-TMD) ───
        if tmd_solved and remaining() > 20:
            log("[P4] Reloading product page (post-TMD)...")
            page.wait_for_timeout(2000)
            xhr_bodies.clear()
            page.on("response", on_response)
            try:
                resp2 = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                http_status = resp2.status if resp2 else http_status
                log(f"[P4] HTTP {http_status}")
            except Exception as e:
                log(f"[P4] Error: {e}")

            # Wait for render
            rendered = False
            for tick in range(10):
                page.wait_for_timeout(1500)
                try:
                    check = page.evaluate("""
                        () => {
                            const h1 = document.querySelector('h1');
                            const pEls = document.querySelectorAll('[class*="price"],[class*="Price"]');
                            const prices = [];
                            pEls.forEach(el => {
                                const t = (el.textContent||'').trim();
                                if (t && /\\d/.test(t) && t.length < 60) prices.push(t);
                            });
                            return {h1: h1?h1.textContent.trim().substring(0,200):null,
                                    prices:[...new Set(prices)].slice(0,5),
                                    bodyLen: document.body?document.body.innerHTML.length:0};
                        }
                    """)
                    if check.get("prices") or (check.get("h1") and len(check["h1"]) > 15):
                        rendered = True
                        log(f"[P4] Rendered! h1={str(check.get('h1',''))[:60]}, prices={check.get('prices',[])}")
                        break
                    if tick % 3 == 0:
                        log(f"[P4] tick={tick}: bodyLen={check.get('bodyLen',0)}")
                except:
                    continue
        elif remaining() > 15:
            log("[P4] No TMD or not solved. Waiting a bit more...")
            page.wait_for_timeout(3000)

        # ─── PHASE 5: EXTRACT ───
        log("[P5] Extracting data...")
        dom = {}
        try:
            dom = page.evaluate(EXTRACT_JS)
        except Exception as e:
            log(f"[P5] Error: {e}")
            dom = {"error": str(e)}

        page.remove_listener("response", on_response)

        # Parse MTOP from XHR
        log("[P5] Parsing MTOP...")
        mtop_data = None
        mtop_raw_list = []
        for x in xhr_bodies:
            if ("pdp.pc.query" in x["url"] or "mtop" in x["url"].lower()) and x["len"] > 200:
                try:
                    clean = x["body"].strip()
                    m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', clean, re.DOTALL)
                    if m:
                        clean = m.group(1)
                    d = json.loads(clean)
                    ret = d.get("ret", [])
                    mtop_raw_list.append({"ret": ret, "data_keys": list(d.get("data", {}).keys())[:10] if isinstance(d.get("data"), dict) else str(d.get("data",""))[:100]})
                    if any("SUCCESS" in str(r) for r in ret):
                        mtop_data = d
                        log(f"[P5] MTOP SUCCESS! keys={list(d.get('data',{}).keys())[:8]}")
                        break
                    else:
                        log(f"[P5] MTOP ret={ret}")
                except (json.JSONDecodeError, ValueError) as e:
                    log(f"[P5] MTOP parse: {e}")
                    continue

        # ─── BUILD RESULT ───
        result.update({
            "final_url": page.url,
            "http_status": http_status,
            "elapsed": round(elapsed(), 1),
            "tmd_iframe_url": tmd_iframe_url[:200] if tmd_iframe_url else None,
            "tmd_solved": tmd_solved,
            "xhr_count": len(xhr_bodies),
            "xhr_bodies": xhr_bodies[:10],
            "dom": dom,
            "mtop_success": mtop_data is not None,
            "mtop_data": mtop_data,
            "mtop_raw_list": mtop_raw_list,
        })

        # ─── SUMMARY ───
        log(f"\n{'='*50}")
        log(f"SUMMARY for {PRODUCT_ID}")
        log(f"{'='*50}")
        log(f"  h1: {str(dom.get('h1','N/A'))[:100]}")
        log(f"  ogTitle: {str(dom.get('ogTitle','N/A'))[:100]}")
        log(f"  titleTag: {str(dom.get('titleTag','N/A'))[:100]}")
        prices = dom.get('prices', [])
        log(f"  prices ({len(prices)}):")
        for p in prices[:8]:
            log(f"    [{p.get('src','?')}] {p.get('text', p.get('price',''))[:50]}")
        log(f"  currencies: {dom.get('currencies', [])[:5]}")
        log(f"  ogImage: {str(dom.get('ogImage','N/A'))[:80]}")
        log(f"  jsonld: {len(dom.get('jsonld', []))} blocks")
        log(f"  images: {len(dom.get('images', []))}")
        log(f"  availability: {dom.get('availability', [])[:3]}")
        log(f"  breadcrumb: {str(dom.get('breadcrumb','N/A'))[:80]}")
        ws = dom.get('windowState', {})
        log(f"  windowState keys: {list(ws.keys())}")
        if '_page_config_' in ws:
            log(f"  _page_config_: {ws['_page_config_'][:200]}")
        log(f"  mtop_success: {mtop_data is not None}")
        log(f"  mtop_raw: {mtop_raw_list[:3]}")
        log(f"  bodyLen: {dom.get('bodyLen', 0)}")
        log(f"  Total: {elapsed():.1f}s")

        ctx.close()

    # ─── SAVE ───
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"\nSaved: {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        traceback.print_exc()
        partial = {
            "product_id": PRODUCT_ID,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "elapsed": round(elapsed(), 1),
        }
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(partial, f, ensure_ascii=False, indent=2)
        log(f"Partial saved: {OUT_PATH}")
    sys.exit(0)
