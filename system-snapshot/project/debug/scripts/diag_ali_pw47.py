"""
diag_ali_pw47.py — AliExpress: 4-track product data extraction.
Track A: reCAPTCHA with 20s init + all-frame token search
Track B: Deep HTML parse (all inline scripts, JSON structures)
Track C: Search-page navigation (natural entry)
Track D: MTOP API via Python requests with cookie signing
"""
import json, time, sys, os, traceback, re, hashlib
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
SEARCH_URL = f"https://www.aliexpress.com/wholesale?SearchText=wireless+headphones+bluetooth+5.4+LED&display={PRODUCT_ID}"
PROFILE = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\pw_ali_profile_pw47"
OUT = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw47_result.json"

diag = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "product_id": PRODUCT_ID,
    "phase": "init",
    "track_a": {"status": "pending", "detail": {}},
    "track_b": {"status": "pending", "detail": {}},
    "track_c": {"status": "pending", "detail": {}},
    "track_d": {"status": "pending", "detail": {}},
    "final_data": {
        "title": "", "price": "", "currency": "", "stock": "",
        "category": "", "seller": "", "images": [], "sku_count": 0,
        "source": "none",
    },
    "errors": [],
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def grab_cookies(ctx):
    try:
        return {c["name"]: c["value"] for c in ctx.cookies()}
    except Exception:
        return {}

# ===================== TRACK A: reCAPTCHA =====================
def track_a(page, timeout_s=100):
    detail = {}
    t0 = time.time()

    # Wait for TMD frame + google frame (up to 20s)
    tmd_frame = None
    google_frame = None
    for i in range(40):
        for fr in page.frames:
            u = (fr.url or "").lower()
            if "_____tmd_____" in u and "punish" in u and not tmd_frame:
                tmd_frame = fr
            if "google.com/recaptcha" in u and not google_frame:
                google_frame = fr
        if tmd_frame and google_frame:
            break
        time.sleep(0.5)

    detail["tmd_frame"] = bool(tmd_frame)
    detail["google_frame"] = bool(google_frame)
    detail["frames_total"] = len(page.frames)
    detail["frame_urls"] = [fr.url[:150] for fr in page.frames]

    if not tmd_frame:
        detail["error"] = "no_tmd_frame"
        return None, detail

    # Wait for grecaptcha in TMD frame (up to 20s)
    grecaptcha_ready = False
    grecaptcha_frame = None
    for i in range(40):
        for fr in page.frames:
            u = (fr.url or "").lower()
            if "tmd" in u or "punish" in u or fr == page.main_frame:
                try:
                    res = fr.evaluate("""() => {
                        try {
                            if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') return 'exec';
                            if (window.grecaptcha && typeof window.grecaptcha.getResponse === 'function') return 'resp';
                            if (window.grecaptcha) return 'obj';
                            return null;
                        } catch(e) { return null; }
                    }""")
                    if res:
                        grecaptcha_ready = True
                        grecaptcha_frame = fr
                        detail["grecaptcha_state"] = res
                        detail["grecaptcha_frame"] = u[:100]
                        break
                except Exception:
                    pass
        if grecaptcha_ready:
            break
        time.sleep(0.5)

    detail["grecaptcha_ready"] = grecaptcha_ready
    log(f"  grecaptcha ready: {grecaptcha_ready} (frame: {detail.get('grecaptcha_frame', 'N/A')})")

    # Click checkbox with human-like mouse
    if google_frame:
        try:
            anchor = google_frame.query_selector("#recaptcha-anchor")
            if anchor:
                box = anchor.bounding_box()
                if box:
                    page.mouse.move(box["x"] + 150, box["y"] + 100)
                    time.sleep(0.3)
                    page.mouse.move(box["x"] + 80, box["y"] + 50)
                    time.sleep(0.2)
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    time.sleep(0.3)
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    detail["clicked"] = True
                    log("  Clicked checkbox")
        except Exception as e:
            detail["click_err"] = str(e)

    time.sleep(3)

    # Wait for token (30s, check all frames every 1s)
    token = None
    for i in range(30):
        # Check textarea in all frames
        for fr in page.frames:
            try:
                ta = fr.query_selector("textarea[name='[REDACTED_AUTH_SECRET]']")
                if ta:
                    val = ta.input_value()
                    if val and len(val) > 50:
                        token = val
                        detail["token_from"] = f"textarea in {fr.url[:80]}"
                        break
            except Exception:
                pass
        if token:
            break

        # Check grecaptcha.getResponse() in all frames
        for fr in page.frames:
            try:
                resp = fr.evaluate("""() => {
                    try {
                        if (window.grecaptcha && grecaptcha.getResponse) {
                            const r = grecaptcha.getResponse();
                            if (r && r.length > 50) return r;
                        }
                        return null;
                    } catch(e) { return null; }
                }""")
                if resp and len(resp) > 50:
                    token = resp
                    detail["token_from"] = f"getResponse() in {fr.url[:80]}"
                    break
            except Exception:
                pass
        if token:
            break

        # Check for visual challenge (means auto-verify failed)
        if i == 10:
            try:
                challenge = google_frame.query_selector(
                    ".rc-image-selection, .rc-slider-captcha, .recaptcha-challenge, img[class*='tile']"
                )
                if challenge:
                    detail["challenge_appeared"] = True
                    log("  Visual challenge appeared at 10s")
            except Exception:
                pass

        time.sleep(1)

    # Fallback: programmatic execute
    if not token and grecaptcha_ready and grecaptcha_frame:
        log("  Trying grecaptcha.execute()…")
        for attempt in range(3):
            try:
                tok = grecaptcha_frame.evaluate("""async () => {
                    try {
                        if (window.grecaptcha && typeof grecaptcha.execute === 'function') {
                            const t = await grecaptcha.execute();
                            if (t && t.length > 50) return t;
                        }
                        return null;
                    } catch(e) { return 'ERR:' + e.message; }
                }""")
                if tok and len(str(tok)) > 50 and not str(tok).startswith("ERR"):
                    token = tok
                    detail["token_from"] = f"execute() attempt {attempt+1}"
                    break
                elif str(tok).startswith("ERR"):
                    detail[f"exec_err_{attempt}"] = str(tok)[:100]
            except Exception as e:
                detail[f"exec_exc_{attempt}"] = str(e)[:100]
            time.sleep(3)

    detail["token_found"] = bool(token)
    if token:
        detail["token_len"] = len(token)
        detail["token_preview"] = token[:60] + "…"
    detail["elapsed_ms"] = int((time.time() - t0) * 1000)
    return token, detail

# ===================== TRACK B: Deep HTML parse =====================
def track_b(page):
    detail = {}
    try:
        html = page.content()
        detail["html_len"] = len(html)
    except Exception as e:
        detail["error"] = str(e)
        return detail

    # B1: Meta tags
    for prop in ["og:title", "og:image", "og:description", "og:video"]:
        m = re.search(rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']', html)
        if not m:
            m = re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(prop)}["\']', html)
        detail[f"meta_{prop}"] = m.group(1) if m else ""

    # B2: ALL inline scripts > 500 chars — summarize and search for product data
    scripts = re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', html, re.DOTALL)
    big_scripts = [(i, s) for i, s in enumerate(scripts) if len(s) > 500]
    detail["inline_script_count"] = len(scripts)
    detail["big_scripts"] = len(big_scripts)

    # Search for product-related JSON in all scripts
    product_patterns = {
        "priceInfo": r'"priceInfo"\s*:\s*\{[^}]+\}',
        "skuPriceList": r'"skuPriceList"\s*:\s*\[[^\]]+\]',
        "skuPathList": r'"skuPathList"\s*:\s*\[[^\]]+\]',
        "productTitle": r'"productTitle"\s*:\s*"[^"]*"',
        "minAmount": r'"minAmount"\s*:\s*[\d.]+',
        "maxAmount": r'"maxAmount"\s*:\s*[\d.]+',
        "currency": r'"currency"\s*:\s*"[A-Z]{3}"',
        "stock": r'"stock"\s*:\s*\d+',
        "sellerName": r'"sellerName"\s*:\s*"[^"]*"',
        "categoryPath": r'"categoryPath"\s*:\s*\[[^\]]*\]',
        "categoryId": r'"categoryId"\s*:\s*\d+',
        "productId": r'"productId"\s*:\s*"\d+"',
        "originalPrice": r'"originalPrice"\s*:\s*[\d.]+',
        "formatPrice": r'"formatPrice"\s*:\s*"[^"]*"',
        "promotionPrice": r'"promotionPrice"\s*:\s*[\d.]+',
        "skuAmount": r'"skuAmount"\s*:\s*\d+',
        "imageList": r'"imageList"\s*:\s*\[[^\]]+\]',
        "mainImage": r'"mainImage"\s*:\s*"[^"]*"',
        "__INIT_DATA__": r'window\.__INIT_DATA__\s*=\s*',
        "__dida_i18n__": r'window\.__dida_i18n__\s*=\s*',
        "_pageData": r'window\._pageData\s*=\s*',
        "runParams": r'window\.runParams\s*=\s*',
        "_globalData": r'window\._globalData\s*=\s*',
    }
    found = {}
    for name, pat in product_patterns.items():
        m = re.search(pat, html, re.DOTALL)
        if m:
            found[name] = m.group(0)[:300]
    detail["product_patterns_found"] = list(found.keys())
    detail["product_patterns_data"] = found

    # B3: Look for price-like strings in the full HTML
    price_matches = re.findall(r'["\']?((?:US\s?\$|€|£|\$|¥)\s?[\d,]+\.?\d*)["\']?', html)
    detail["price_strings"] = price_matches[:20]

    # B4: JSON-LD
    ld_matches = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL
    )
    json_ld = []
    for ld in ld_matches:
        try:
            json_ld.append(json.loads(ld.strip()))
        except Exception:
            pass
    detail["json_ld"] = json_ld

    # B5: DOM extraction
    try:
        dom_data = page.evaluate("""() => {
            const out = {};
            const h1 = document.querySelector('h1');
            out.h1 = h1 ? h1.textContent.trim() : '';
            // Price elements (broader selectors)
            const priceSels = [
                '[class*="price"] [class*="current"]', '[class*="price"] [class*="Price"]',
                '[class*="Price"] [class*="current"]', '[class*="price-current"]',
                '[class*="PriceCurrent"]', '[data-role="price"]', '.pdp-price',
                '[class*="price"] .value', '[class*="price"] span',
                '[class*="Price--current"]', '[class*="price"] [class*="value"]'
            ];
            out.prices = [];
            priceSels.forEach(sel => {
                document.querySelectorAll(sel).forEach(e => {
                    const t = e.textContent.trim();
                    if (t && t.length < 40 && /\\d/.test(t)) out.prices.push(t);
                });
            });
            // Images
            const imgs = document.querySelectorAll('img[src*="ae-pic"], img[src*="aliexpress-media"], img[src*="ae01"]');
            out.images = Array.from(imgs).map(i => i.src || i.getAttribute('data-src') || '').filter(u => u && !u.includes('150x150.gif')).slice(0, 15);
            // Breadcrumbs
            const bc = document.querySelectorAll('[class*="breadcrumb"] a, [class*="Breadcrumb"] a, nav[aria-label] a');
            out.breadcrumbs = Array.from(bc).map(a => a.textContent.trim()).filter(Boolean).slice(0, 10);
            // All window globals
            out.globals = {};
            ['__INIT_DATA__', '__dida_i18n__', '_pageData', 'runParams', '_globalData',
             '_dida', '_pageConfig', '_initData', '__NEXT_DATA__'].forEach(k => {
                if (window[k]) {
                    try { out.globals[k] = Object.keys(window[k]).slice(0, 15); }
                    catch(e) { out.globals[k] = 'exists'; }
                }
            });
            return out;
        }""")
        detail["dom"] = dom_data
    except Exception as e:
        detail["dom_err"] = str(e)

    return detail

# ===================== TRACK C: Search navigation =====================
def track_c(page):
    detail = {}
    try:
        # Go to search page first
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)

        # Search for the product
        search_q = "wireless headphones bluetooth 5.4 led display"
        page.goto(f"https://www.aliexpress.com/wholesale?SearchText={search_q.replace(' ', '+')}",
                  wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)
        detail["search_url"] = page.url
        detail["search_title"] = page.title()
        detail["search_html_len"] = len(page.content())

        # Check for TMD on search
        tmd_search = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
        detail["search_tmd"] = len(tmd_search)
        log(f"  Search page: TMD={len(tmd_search)}")

        # Find the product in search results and click it
        clicked = False
        try:
            # Look for the product ID in links
            links = page.query_selector_all(f'a[href*="{PRODUCT_ID}"]')
            if links:
                links[0].click()
                clicked = True
                detail["clicked_link"] = True
                time.sleep(6)
            else:
                # Try to find by title text
                detail["clicked_link"] = False
                detail["link_count"] = len(links)
        except Exception as e:
            detail["click_err"] = str(e)

        if clicked:
            detail["product_url"] = page.url
            detail["product_title"] = page.title()
            detail["product_html_len"] = len(page.content())
            tmd_prod = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
            detail["product_tmd"] = len(tmd_prod)
            log(f"  Product via search: TMD={len(tmd_prod)}, title={page.title()[:60]}")

            # Quick extract
            data = page.evaluate("""() => {
                const out = {};
                const t = document.querySelector('title');
                out.title = t ? t.textContent.trim() : '';
                const ogT = document.querySelector('meta[property="og:title"]');
                out.ogTitle = ogT ? ogT.content : '';
                const priceEls = document.querySelectorAll(
                    '[class*="price"] [class*="current"], [class*="Price"] [class*="current"], ' +
                    '[class*="price-current"], [class*="PriceCurrent"], [data-role="price"]'
                );
                out.prices = Array.from(priceEls).map(e => e.textContent.trim()).filter(Boolean);
                const imgs = document.querySelectorAll('img[src*="ae-pic"], img[src*="aliexpress-media"]');
                out.images = Array.from(imgs).map(i => i.src || '').filter(u => u).slice(0, 10);
                return out;
            }""")
            detail["data"] = data
            detail["status"] = "ok"
        else:
            detail["status"] = "no_product_in_search"
    except Exception as e:
        detail["status"] = "error"
        detail["error"] = str(e)
    return detail

# ===================== TRACK D: MTOP via Python =====================
def track_d(cookie_dict):
    """Call MTOP API directly with proper MD5 signing."""
    detail = {}
    try:
        import requests
    except ImportError:
        detail["error"] = "requests not installed"
        return detail

    # Get _m_h5_tk from cookies
    token = cookie_dict.get("_m_h5_tk", "")
    if not token:
        detail["error"] = "no _m_h5_tk cookie"
        return detail
    # Token is "xxx_timestamp" — take the part before underscore
    token_part = token.split("_")[0] if "_" in token else token
    detail["token_preview"] = token[:40]

    app_key = "24568761"
    api = "mtop.aliexpress.pdp.pc.query"
    v = "1.0"
    data = json.dumps({"productId": PRODUCT_ID}, separators=(',', ':'))

    def mtop_sign(tk, ts, ak, d):
        raw = f"{tk}&{ts}&{ak}&{d}"
        return hashlib.md5(raw.encode()).hexdigest()

    cookies = {k: v for k, v in cookie_dict.items() if k in (
        "_m_h5_tk", "_m_h5_tk_enc", "xman_us_f", "xman_t", "xman_f",
        "acs_usuc_t", "cna", "aep_usuc_f", "x5sectag", "cbc"
    )}

    for attempt in range(3):
        ts = str(int(time.time() * 1000))
        sign = mtop_sign(token_part, ts, app_key, data)
        url = (f"https://acs.aliexpress.com/h5/{api}/{v}/"
               f"?jsv=2.7.2&appKey={app_key}&t={ts}&sign=[REDACTED_AUTH_SECRET]"
               f"&api={api}&v={v}&type=originaljson&dataType=json"
               f"&timeout=15000&data={requests.utils.quote(data)}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.aliexpress.com/",
            "Accept": "application/json",
            "Origin": "https://www.aliexpress.com",
        }

        try:
            r = requests.post(url, headers=headers, cookies=cookies, timeout=15)
            body = r.text[:5000]
            detail[f"attempt_{attempt}"] = {
                "status": r.status_code,
                "body_len": len(body),
                "body_preview": body[:1000],
            }
            log(f"  MTOP attempt {attempt}: status={r.status_code}, len={len(body)}")

            # Check if we need to refresh token
            if "FAIL_SYS_TOKEN_EMPTY" in body or "FAIL_SYS_TOKEN_EXOIRED" in body:
                # Update token from response cookies
                new_tok = r.cookies.get("_m_h5_tk", "")
                if new_tok:
                    token = new_tok
                    token_part = token.split("_")[0]
                    detail["token_refreshed"] = True
                    cookies["_m_h5_tk"] = new_tok
                time.sleep(1)
                continue

            # Check for TMD
            if "FAIL_SYS_USER_VALIDATE" in body or "RGV587" in body:
                detail[f"attempt_{attempt}"]["tmd_blocked"] = True
                log(f"  MTOP attempt {attempt}: TMD blocked")
                time.sleep(2)
                continue

            # Success?
            if '"data"' in body and "FAIL_SYS" not in body:
                detail[f"attempt_{attempt}"]["success"] = True
                try:
                    j = json.loads(body)
                    detail[f"attempt_{attempt}"]["data_keys"] = list(j.get("data", {}).keys())[:20]
                except Exception:
                    pass
                break

        except Exception as e:
            detail[f"attempt_{attempt}"] = {"error": str(e)}
        time.sleep(1)

    detail["status"] = "done"
    return detail

# ===================== MAIN =====================
def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            window.chrome = {runtime: {}};
        """)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ===== Warmup =====
        log("Warmup: homepage")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)
        log(f"  cookies: {len(grab_cookies(ctx))}")

        # ===== Product page =====
        log(f"Product: {PRODUCT_URL}")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        log(f"  url: {page.url}")
        log(f"  title: {page.title()[:80]}")
        cookies = grab_cookies(ctx)
        log(f"  cookies: {len(cookies)}")

        # ===== TRACK B: Deep HTML parse (do first, before TMD overlay changes DOM) =====
        log("Track B: deep HTML parse…")
        diag["phase"] = "track_b"
        tb = track_b(page)
        diag["track_b"] = {"status": "done", "detail": tb}
        log(f"  html_len={tb.get('html_len', 0)}, patterns={tb.get('product_patterns_found', [])}")
        log(f"  prices: {tb.get('price_strings', [])[:5]}")
        log(f"  dom prices: {tb.get('dom', {}).get('prices', [])[:5]}")
        log(f"  globals: {tb.get('dom', {}).get('globals', {})}")

        # ===== TRACK A: reCAPTCHA (longer wait) =====
        log("Track A: reCAPTCHA (20s init + 30s token wait)…")
        diag["phase"] = "track_a"
        tmd_iframes = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
        if tmd_iframes:
            log(f"  TMD present ({len(tmd_iframes)} iframes)")
            time.sleep(5)  # let baxia initialize
            token, ta = track_a(page, timeout_s=100)
            diag["track_a"] = {"status": "done", "detail": ta}
            log(f"  token: {'YES' if token else 'NO'} ({ta.get('elapsed_ms', 0)}ms)")
            log(f"  detail: {json.dumps(ta, ensure_ascii=False)[:400]}")

            if token:
                log("  Waiting 8s for TMD POST…")
                time.sleep(8)
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(6)
                tmd2 = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
                diag["track_a"]["tmd_passed"] = len(tmd2) == 0
                log(f"  TMD passed: {diag['track_a']['tmd_passed']}")
                if diag["track_a"]["tmd_passed"]:
                    tb2 = track_b(page)
                    diag["track_b"]["after_tmd"] = tb2
                    log(f"  post-TMD prices: {tb2.get('price_strings', [])[:5]}")
        else:
            diag["track_a"] = {"status": "no_tmd", "detail": {}}
            log("  No TMD!")

        # ===== TRACK C: Search navigation =====
        log("Track C: search navigation…")
        diag["phase"] = "track_c"
        tc = track_c(page)
        diag["track_c"] = {"status": "done", "detail": tc}
        log(f"  status: {tc.get('status')}, tmd={tc.get('search_tmd', 0)}")
        log(f"  prices: {tc.get('data', {}).get('prices', [])[:5]}")

        # ===== TRACK D: MTOP via Python =====
        log("Track D: MTOP API via Python…")
        diag["phase"] = "track_d"
        # Refresh cookies after all page interactions
        cookies_final = grab_cookies(ctx)
        td = track_d(cookies_final)
        diag["track_d"] = {"status": "done", "detail": td}
        for k, v in td.items():
            if k.startswith("attempt_") and isinstance(v, dict):
                log(f"  {k}: status={v.get('status')}, tmd={v.get('tmd_blocked', False)}, success={v.get('success', False)}")

        # ===== Merge best data =====
        diag["phase"] = "merge"
        fd = diag["final_data"]

        # Title
        dom = tb.get("dom", {})
        if dom.get("h1"):
            fd["title"] = dom["h1"]
        elif tb.get("meta_og:title"):
            fd["title"] = tb["meta_og:title"]
        elif tc.get("data", {}).get("title"):
            fd["title"] = tc["data"]["title"]

        # Price
        all_prices = (dom.get("prices", []) + tb.get("price_strings", []) +
                      tc.get("data", {}).get("prices", []))
        # Also check post-TMD
        if "after_tmd" in tb:
            tb2 = tb["after_tmd"]
            all_prices.extend(tb2.get("price_strings", []))
            all_prices.extend(tb2.get("dom", {}).get("prices", []))
        real_prices = [p for p in all_prices if re.search(r'\d+[\.,]\d{2}', str(p))]
        if real_prices:
            fd["price"] = real_prices[0]
        # Check inline patterns
        pp = tb.get("product_patterns_data", {})
        if not fd["price"] and pp.get("minAmount"):
            m = re.search(r'[\d.]+', pp["minAmount"])
            if m:
                fd["price"] = m.group(0)
        if not fd["price"] and pp.get("formatPrice"):
            m = re.search(r'"formatPrice"\s*:\s*"([^"]*)"', pp["formatPrice"])
            if m:
                fd["price"] = m.group(1)

        # Currency
        if pp.get("currency"):
            m = re.search(r'"([A-Z]{3})"', pp["currency"])
            if m:
                fd["currency"] = m.group(1)
        elif "$" in str(fd["price"]):
            fd["currency"] = "USD"
        elif "€" in str(fd["price"]):
            fd["currency"] = "EUR"

        # Images
        all_imgs = (dom.get("images", []) + [tb.get("meta_og:image", "")] +
                    tc.get("data", {}).get("images", []))
        seen = set()
        fd["images"] = []
        for img in all_imgs:
            if img and img not in seen and not img.endswith(".gif"):
                seen.add(img)
                fd["images"].append(img)
        fd["images"] = fd["images"][:10]

        # Category
        bc = dom.get("breadcrumbs", [])
        if bc:
            fd["category"] = " > ".join(bc[-3:])
        elif pp.get("categoryPath"):
            fd["category"] = pp["categoryPath"][:100]

        # Seller
        if pp.get("sellerName"):
            m = re.search(r'"([^"]*)"', pp["sellerName"])
            if m:
                fd["seller"] = m.group(1)

        # SKU
        if pp.get("skuPathList"):
            items = re.findall(r'\{[^}]+\}', pp["skuPathList"])
            fd["sku_count"] = len(items)

        # Source
        if real_prices and dom.get("prices"):
            fd["source"] = "dom"
        elif real_prices:
            fd["source"] = "html"
        elif fd["title"] and fd["images"]:
            fd["source"] = "meta_og"
        else:
            fd["source"] = "none"

        log(f"=== FINAL ===")
        log(f"  title: {fd['title'][:80]}")
        log(f"  price: {fd['price']} {fd['currency']}")
        log(f"  stock: {fd['stock']}")
        log(f"  images: {len(fd['images'])}")
        log(f"  category: {fd['category'][:60]}")
        log(f"  seller: {fd['seller'][:40]}")
        log(f"  sku_count: {fd['sku_count']}")
        log(f"  source: {fd['source']}")

        ctx.close()

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)
    log(f"Saved → {OUT} ({os.path.getsize(OUT)} bytes)")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        diag["phase"] = "CRASH"
        diag["errors"].append(traceback.format_exc())
        try:
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(diag, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        raise
