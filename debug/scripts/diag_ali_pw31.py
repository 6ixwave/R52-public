"""Диагностика AliExpress pw31: headful + reCAPTCHA + MTOP fallback.

Ключевые изменения vs pw30:
- headless=False (убирает headless-детект Google reCAPTCHA)
- Ждём bframe (реальный виджет) перед кликом
- Клик мышью с движением (mouse.move → mouse.click)
- Fallback: прямой MTOP API через requests (без браузера)
- Бюджет 120s
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
OUT_PATH = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw31_result.json"
COOKIES_PATH = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw31_cookies.json"

BUDGET_START = time.monotonic()
BUDGET_TOTAL = 120

def elapsed():
    return time.monotonic() - BUDGET_START

def remaining():
    return max(0, BUDGET_TOTAL - elapsed())

def log(msg):
    print(f"[{elapsed():.1f}s] {msg}", flush=True)

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

# ─── JS: extract data ───
EXTRACT_JS = """
() => {
    const r = {};
    const h1 = document.querySelector('h1');
    r.h1 = h1 ? h1.textContent.trim().substring(0, 500) : null;
    const ogTitle = document.querySelector('meta[property="og:title"]');
    r.ogTitle = ogTitle ? (ogTitle.getAttribute('content') || '').trim() : null;
    const titleEl = document.querySelector('title');
    r.titleTag = titleEl ? titleEl.textContent.trim().substring(0, 500) : null;

    r.prices = [];
    const seen = new Set();
    document.querySelectorAll('[data-price],[data-activity-price],[data-original-price]').forEach(el => {
        const p = el.getAttribute('data-price') || '';
        const ap = el.getAttribute('data-activity-price') || '';
        const op = el.getAttribute('data-original-price') || '';
        if ((p || ap || op) && !seen.has(p + ap + op)) {
            seen.add(p + ap + op);
            r.prices.push({src: 'data-attr', price: p, act: ap, orig: op, text: (el.textContent||'').trim().substring(0,80)});
        }
    });
    const pSels = ['[class*="price"]','[class*="Price"]','[data-pl*="price"]','[class*="money"]','[class*="amount"]'];
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

    r.currencies = [];
    const curRe = /[€$£¥₴₽]|EUR|USD|GBP|CNY|INR/g;
    const bodyText = document.body ? document.body.textContent.substring(0, 30000) : '';
    const curMatches = bodyText.match(curRe);
    if (curMatches) r.currencies = [...new Set(curMatches)].slice(0, 10);

    r.metas = {};
    document.querySelectorAll('meta[property],meta[name]').forEach(m => {
        const p = m.getAttribute('property') || m.getAttribute('name') || '';
        const c = (m.getAttribute('content') || '').trim();
        if (p && c) r.metas[p] = c.substring(0, 400);
    });

    r.jsonld = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try { r.jsonld.push(JSON.parse(s.textContent)); }
        catch(e) { r.jsonld.push({err: e.message, raw: s.textContent.substring(0, 300)}); }
    });

    r.windowState = {};
    for (const k of ['__INIT_DATA_STATE__','runParams','_page_config_','__INIT_DATA_CALLBACK__','__NEXT_DATA__','pageData','productData']) {
        try {
            const v = window[k];
            if (v !== undefined && v !== null) {
                r.windowState[k] = (typeof v === 'string' ? v : JSON.stringify(v)).substring(0, 20000);
            }
        } catch(e) {}
    }

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

    const ogImg = document.querySelector('meta[property="og:image"]');
    r.ogImage = ogImg ? (ogImg.getAttribute('content') || '').trim() : null;

    r.availability = [];
    const aSels = ['[class*="stock"]','[class*="availability"]','[class*="buy-now"]','[class*="addToCart"]','button[class*="buy"]','[class*="sold-out"]'];
    for (const s of aSels) {
        try {
            document.querySelectorAll(s).forEach(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t && t.length < 150) r.availability.push(t);
            });
        } catch(e) {}
    }
    r.availability = [...new Set(r.availability)].slice(0, 10);

    const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"],nav[aria-label]');
    r.breadcrumb = bc ? bc.textContent.trim().substring(0, 300) : null;
    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
    return r;
}
"""

# ─── JS: check reCAPTCHA state in bframe ───
CHECK_RC_JS = """
() => {
    const r = { ready: false, details: {} };
    // Look for the actual reCAPTCHA widget
    const widget = document.querySelector('.g-recaptcha, .recaptcha-widget, #recaptcha, [class*="recaptcha"]');
    r.details.hasWidget = !!widget;
    if (widget) {
        r.details.widgetHTML = widget.innerHTML.substring(0, 300);
    }
    // Iframe count
    r.details.iframes = [];
    document.querySelectorAll('iframe').forEach(f => {
        r.details.iframes.push({src: (f.src||'').substring(0,150), w: f.width, h: f.height});
    });
    // Check for checkbox
    const cb = document.querySelector('#recaptcha-anchor, input[type="checkbox"], .rc-button, span[role="checkbox"]');
    r.details.hasCheckbox = !!cb;
    if (cb) {
        r.details.cbVisible = cb.offsetParent !== null;
        r.details.cbHTML = cb.outerHTML.substring(0, 200);
    }
    // Loading state
    const loading = document.querySelector('.loading, #loading, [class*="loading"]');
    r.details.isLoading = !!loading && loading.offsetParent !== null;
    // grecaptcha
    r.details.grecaptcha = typeof window.grecaptcha !== 'undefined';
    // sessionStorage token
    try {
        const tok = sessionStorage.getItem('bx-recaptcha-token');
        r.details.token = tok ? tok.substring(0, 100) : null;
    } catch(e) {}
    r.ready = r.details.hasCheckbox && r.details.cbVisible;
    return r;
}
"""

def solve_tmd_headful(ctx, tmd_url, budget_s=40):
    """Open TMD in new page (headful), solve reCAPTCHA with mouse movement."""
    log(f"  [TMD] Opening in new page (headful)...")
    tmd_page = ctx.new_page()
    tmd_page.set_default_timeout(15000)

    try:
        tmd_page.goto(tmd_url, wait_until="domcontentloaded", timeout=20000)
        log(f"  [TMD] Loaded: {tmd_page.url[:100]}")
    except Exception as e:
        log(f"  [TMD] Load error: {e}")
        tmd_page.close()
        return False, []

    # Wait for scripts (reCAPTCHA Enterprise + LWSC)
    log("  [TMD] Waiting for reCAPTCHA scripts...")
    tmd_page.wait_for_timeout(5000)

    # Find the reCAPTCHA bframe and click
    clicked = False
    for attempt in range(5):
        # Scan all frames for reCAPTCHA
        for frame in tmd_page.frames:
            fu = (frame.url or "").lower()
            if "google.com/recaptcha" in fu or "recaptcha" in fu:
                log(f"  [TMD] Frame: {fu[:100]}")
                try:
                    frame.wait_for_load_state("domcontentloaded", timeout=8000)
                except:
                    pass
                # Try to find and click checkbox
                for sel in ('#recaptcha-anchor', 'input[type="checkbox"]',
                            'span[role="checkbox"]', '.rc-button',
                            'div[style*="cursor: pointer"]'):
                    try:
                        el = frame.query_selector(sel)
                        if el:
                            box = el.bounding_box()
                            if box and box["width"] > 0:
                                # Mouse movement (human-like)
                                tmd_page.mouse.move(box["x"] - 50, box["y"] - 30, steps=5)
                                tmd_page.wait_for_timeout(200)
                                tmd_page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2, steps=8)
                                tmd_page.wait_for_timeout(300)
                                tmd_page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                                log(f"  [TMD] Mouse-clicked '{sel}' at ({box['x']:.0f},{box['y']:.0f})")
                                clicked = True
                                break
                            else:
                                el.click()
                                log(f"  [TMD] El-clicked '{sel}'")
                                clicked = True
                                break
                    except Exception as e:
                        continue
                if clicked:
                    break
        if clicked:
            break
        tmd_page.wait_for_timeout(2000)
        log(f"  [TMD] Attempt {attempt+1}: no checkbox yet, waiting...")

    if not clicked:
        log("  [TMD] No checkbox found in 5 attempts. Trying direct page click...")
        # Maybe it's v3 (invisible) — just wait for callback
        pass

    # Wait for resolution
    log("  [TMD] Waiting for resolution...")
    deadline = time.monotonic() + budget_s
    solved = False
    while time.monotonic() < deadline:
        tmd_page.wait_for_timeout(2000)
        try:
            cur_url = tmd_page.url
            # Redirect = solved
            if "third_validate_close" in cur_url or ("punish" not in cur_url.lower() and "acs.aliexpress" not in cur_url):
                log(f"  [TMD] REDIRECT! {cur_url[:100]}")
                solved = True
                break

            # Token in sessionStorage
            token = tmd_page.evaluate("""
                () => { try { return sessionStorage.getItem('bx-recaptcha-token'); } catch(e) { return null; } }
            """)
            if token:
                log(f"  [TMD] Token: {token[:80]}...")
                solved = True
                break

            # Check bframe state
            for frame in tmd_page.frames:
                fu = (frame.url or "").lower()
                if "bframe" in fu or "recaptcha" in fu:
                    try:
                        st = frame.evaluate(CHECK_RC_JS)
                        if st.get('details', {}).get('token'):
                            log(f"  [TMD] Frame token found!")
                            solved = True
                            break
                    except:
                        pass
        except Exception as e:
            if "closed" in str(e).lower() or "crashed" in str(e).lower():
                log(f"  [TMD] Page closed: {e}")
                break
            continue
        if solved:
            break

    # Save cookies
    cookies = []
    try:
        cookies = ctx.cookies()
        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        x5 = [c for c in cookies if 'x5' in c['name'].lower() or 'sec' in c['name'].lower()]
        for c in x5:
            log(f"    Cookie: {c['name']}={c['value'][:60]}...")
        log(f"  [TMD] {len(cookies)} cookies saved")
    except Exception as e:
        log(f"  [TMD] Cookie save error: {e}")

    # Save TMD page HTML for debug
    try:
        tmd_html = tmd_page.content()
        tmd_html_path = OUT_PATH.replace("_result.json", "_tmd_page.html")
        with open(tmd_html_path, "w", encoding="utf-8") as f:
            f.write(tmd_html)
        log(f"  [TMD] HTML saved: {len(tmd_html)}b")
    except:
        pass

    tmd_page.close()
    return solved, cookies

def mtop_api_fallback(cookies, product_id):
    """Direct MTOP API call via requests (no browser)."""
    log("[MTOP-API] Direct API call...")
    try:
        import requests
    except ImportError:
        log("[MTOP-API] requests not installed, trying pip install...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "requests", "-q"],
                      capture_output=True)
        import requests

    # Build cookie string
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": f"https://it.aliexpress.com/item/{product_id}.html",
        "Origin": "https://it.aliexpress.com",
        "Accept": "*/*",
        "Cookie": cookie_str,
    }

    # MTOP parameters
    t = str(int(time.time() * 1000))
    data = {
        "productId": product_id,
        "_lang": "en_US",
        "_currency": "USD",
        "country": "US",
        "province": "",
        "city": "",
        "channel": "",
        "pdp_ext_f": "",
        "pdpNPI": "",
        "sourceType": "",
        "clientType": "pc",
        "ext": json.dumps({"foreverRandomToken": "", "site": "glo", "crawler": False,
                           "x-m-biz-bx-region": "", "signedIn": False, "host": "www.aliexpress.com"})
    }

    # MTOP sign (simplified: for h5 it's often just a token)
    # The real sign requires appKey + token + data hash
    # Try with the appKey from the page
    app_key = "12574478"
    # Simple sign: md5(token + "&" + t + "&" + app_key + "&" + data_json)
    import hashlib
    data_json = json.dumps(data, separators=(',', ':'))
    token = ""  # We don't have the MTOP token without a valid session
    sign_str = f"{token}&{t}&{app_key}&{data_json}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()

    url = f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
    params = {
        "jsv": "2.5.1",
        "appKey": app_key,
        "t": t,
        "sign": sign,
        "api": "mtop.aliexpress.pdp.pc.query",
        "type": "originaljsonp",
        "v": "1.0",
        "timeout": "15000",
        "dataType": "json",
        "data": data_json,
    }

    log(f"[MTOP-API] GET {url[:60]}...")
    log(f"[MTOP-API] Params: t={t}, sign={sign[:20]}...")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        log(f"[MTOP-API] HTTP {resp.status_code}, {len(resp.text)}b")
        body = resp.text.strip()
        # Strip JSONP
        m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', body, re.DOTALL)
        if m:
            body = m.group(1)
        d = json.loads(body)
        ret = d.get("ret", [])
        log(f"[MTOP-API] ret={ret}")
        if any("SUCCESS" in str(r) for r in ret):
            log(f"[MTOP-API] SUCCESS! data_keys={list(d.get('data',{}).keys())[:10]}")
            return d
        else:
            log(f"[MTOP-API] data_keys={list(d.get('data',{}).keys())[:10] if isinstance(d.get('data'), dict) else 'N/A'}")
            return d
    except Exception as e:
        log(f"[MTOP-API] Error: {e}")
        return None

def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    url = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
    result = {
        "product_id": PRODUCT_ID,
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw31",
        "headful": True,
    }

    xhr_bodies = []
    tmd_url = None

    def on_response(resp):
        nonlocal tmd_url
        try:
            u = resp.url
            ct = resp.headers.get("content-type", "")
            if (("json" in ct and resp.status == 200) or
                ("mtop" in u.lower() and resp.status == 200) or
                ("pdp" in u.lower() and resp.status == 200)):
                body = ""
                try:
                    body = resp.text()
                except:
                    pass
                if len(body) > 50:
                    xhr_bodies.append({
                        "url": u[:300], "status": resp.status,
                        "len": len(body), "ct": ct[:100], "body": body[:25000],
                    })
                    tag = "MTOP" if "mtop" in u.lower() else "JSON"
                    log(f"  [{tag}] {u[:70]}... ({len(body)}b)")
                    if "FAIL_SYS_USER_VALIDATE" in body and "punish" in body:
                        m = re.search(r'"url"\s*:\s*"([^"]+)"', body)
                        if m:
                            tmd_url = m.group(1)
                            log(f"  [MTOP] TMD URL: {tmd_url[:80]}...")
        except:
            pass

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,  # ← KEY CHANGE
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-automation",
                "--window-size=1920,1080",
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

        # ─── P1: WARMUP ───
        log("[P1] Warmup (headful)...")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            try:
                page.evaluate(DISMISS_JS)
            except:
                pass
            page.wait_for_timeout(1000)
            cookies = ctx.cookies()
            log(f"[P1] Done. {len(cookies)} cookies. {elapsed():.1f}s")
        except Exception as e:
            log(f"[P1] Error: {e}")

        # ─── P2: LOAD PRODUCT ───
        page.on("response", on_response)
        log(f"[P2] Loading product...")
        http_status = 0
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            http_status = resp.status if resp else 0
            log(f"[P2] HTTP {http_status}, final={page.url[:100]}")
        except Exception as e:
            log(f"[P2] Error: {e}")

        # Wait for MTOP / TMD
        log("[P2] Waiting for MTOP/TMD...")
        for tick in range(12):  # 12 × 1.5s = 18s
            page.wait_for_timeout(1500)
            if tmd_url:
                log(f"[P2] TMD URL found at tick={tick}")
                break
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
                        return {h1: h1?h1.textContent.trim().substring(0,100):null,
                                prices:[...new Set(prices)].slice(0,3),
                                bodyLen: document.body?document.body.innerHTML.length:0};
                    }
                """)
                if check.get('h1') and len(check['h1']) > 15:
                    log(f"[P2] Rendered! h1={check['h1'][:60]}")
                    break
                if tick % 4 == 0:
                    log(f"[P2] tick={tick}: bodyLen={check.get('bodyLen',0)}")
            except:
                continue

        page.remove_listener("response", on_response)

        # ─── P3: SOLVE TMD (headful) ───
        tmd_solved = False
        all_cookies = []
        if tmd_url:
            log(f"[P3] TMD detected. Solving (headful)...")
            tmd_solved, all_cookies = solve_tmd_headful(ctx, tmd_url, budget_s=min(40, int(remaining())))
            log(f"[P3] TMD solved: {tmd_solved}")
        else:
            log("[P3] No TMD. Checking render...")
            all_cookies = ctx.cookies()

        # ─── P4: RELOAD (post-TMD) ───
        if tmd_solved and remaining() > 25:
            log("[P4] Reloading product (post-TMD)...")
            page.wait_for_timeout(2000)
            xhr_bodies.clear()
            page.on("response", on_response)
            try:
                resp2 = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                http_status = resp2.status if resp2 else http_status
                log(f"[P4] HTTP {http_status}")
            except Exception as e:
                log(f"[P4] Error: {e}")

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
            log("[P4] No TMD or unsolved. Extra wait...")
            page.wait_for_timeout(3000)

        # ─── P5: EXTRACT ───
        log("[P5] Extracting...")
        dom = {}
        try:
            dom = page.evaluate(EXTRACT_JS)
        except Exception as e:
            log(f"[P5] Error: {e}")
            dom = {"error": str(e)}

        # Parse MTOP
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
                    mtop_raw_list.append({"ret": ret, "data_keys": list(d.get("data",{}).keys())[:10] if isinstance(d.get("data"), dict) else str(d.get("data",""))[:80]})
                    if any("SUCCESS" in str(r) for r in ret):
                        mtop_data = d
                        log(f"[P5] MTOP SUCCESS! keys={list(d.get('data',{}).keys())[:8]}")
                        break
                    else:
                        log(f"[P5] MTOP ret={ret}")
                except (json.JSONDecodeError, ValueError) as e:
                    log(f"[P5] MTOP parse: {e}")
                    continue

        # ─── P6: MTOP API FALLBACK ───
        mtop_api_data = None
        if not mtop_data and remaining() > 15:
            log("[P6] MTOP API fallback...")
            all_cookies = ctx.cookies()
            mtop_api_data = mtop_api_fallback(all_cookies, PRODUCT_ID)

        # ─── BUILD RESULT ───
        result.update({
            "final_url": page.url,
            "http_status": http_status,
            "elapsed": round(elapsed(), 1),
            "tmd_url": tmd_url[:200] if tmd_url else None,
            "tmd_solved": tmd_solved,
            "xhr_count": len(xhr_bodies),
            "xhr_bodies": xhr_bodies[:10],
            "dom": dom,
            "mtop_success": mtop_data is not None,
            "mtop_data": mtop_data,
            "mtop_raw_list": mtop_raw_list,
            "mtop_api_fallback": mtop_api_data,
        })

        # ─── SUMMARY ───
        log(f"\n{'='*55}")
        log(f"SUMMARY pw31 for {PRODUCT_ID} (headful)")
        log(f"{'='*55}")
        log(f"  h1: {str(dom.get('h1','N/A'))[:100]}")
        log(f"  ogTitle: {str(dom.get('ogTitle','N/A'))[:100]}")
        prices = dom.get('prices', [])
        log(f"  prices ({len(prices)}):")
        for p in prices[:8]:
            log(f"    [{p.get('src','?')}] {p.get('text', p.get('price',''))[:50]}")
        log(f"  currencies: {dom.get('currencies', [])[:5]}")
        log(f"  ogImage: {str(dom.get('ogImage','N/A'))[:80]}")
        log(f"  images: {len(dom.get('images', []))}")
        log(f"  availability: {dom.get('availability', [])[:3]}")
        log(f"  mtop_success: {mtop_data is not None}")
        log(f"  mtop_api: {mtop_api_data is not None}")
        log(f"  tmd_solved: {tmd_solved}")
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
