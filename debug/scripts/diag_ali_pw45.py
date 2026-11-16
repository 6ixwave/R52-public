"""
diag_ali_pw45.py — AliExpress TMD/reCAPTCHA Enterprise: improved solve.
Key improvements vs pw44:
  - Wait for reCAPTCHA widget to FULLY load before clicking (spinner visible)
  - After click: wait for spinner to STOP (verification complete)
  - Token search: ALL frames + grecaptcha.getResponse() + network XHR watch
  - If auto-verify fails: try programmatic grecaptcha.execute()
  - Watch for TMD POST (x5secdata cookie change) as success signal
  - Tighter per-phase timeouts to stay under total limit
"""
import json, time, sys, os, traceback, re
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\pw_ali_profile_pw45"
OUT = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw45_result.json"

diag = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "product_id": PRODUCT_ID,
    "profile": PROFILE,
    "phase": "init",
    "cookies_warm": [],
    "cookies_product": [],
    "final_url": "",
    "title_tag": "",
    "tmd": "none",
    "tmd_detail": {},
    "recaptcha": {},
    "captcha_token_preview": None,
    "captcha_await_ms": 0,
    "tmd_post_detected": False,
    "reload_count": 0,
    "mtop_intercepted": [],
    "run_params_raw": "",
    "run_params_len": 0,
    "prices": [],
    "dom_prices": [],
    "stock": None,
    "category": None,
    "variants_n": 0,
    "seller": "",
    "images": [],
    "json_ld": None,
    "meta_og_title": "",
    "meta_og_image": "",
    "meta_description": "",
    "big_resource_size": 0,
    "big_resource_keys": [],
    "inline_scripts_summary": [],
    "source": "none",
    "errors": [],
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def grab_cookies(ctx):
    try:
        return [c["name"] for c in ctx.cookies()]
    except Exception:
        return []

def grab_cookie_values(ctx):
    try:
        return {c["name"]: c["value"][:80] for c in ctx.cookies()}
    except Exception:
        return {}

def solve_recaptcha_v2(page, timeout_s=90):
    """
    Improved reCAPTCHA Enterprise solve:
    1. Find google.com frame
    2. Wait for widget to be ready (spinner or anchor visible)
    3. Click checkbox
    4. Wait for verification to complete (spinner stops / checkmark)
    5. Extract token from: textarea in any frame, grecaptcha.getResponse(),
       or intercept the callback
    6. Fallback: programmatic grecaptcha.execute()
    """
    detail = {}
    t0 = time.time()
    deadline = t0 + timeout_s

    # --- Step 1: Find google.com reCAPTCHA frame ---
    google_frame = None
    while time.time() < deadline:
        for fr in page.frames:
            u = (fr.url or "").lower()
            if "google.com/recaptcha" in u or "recaptcha/enterprise" in u:
                google_frame = fr
                break
        if google_frame:
            break
        time.sleep(0.5)

    if not google_frame:
        detail["error"] = "google_frame_not_found"
        return None, int((time.time() - t0) * 1000), detail

    detail["google_url"] = (google_frame.url or "")[:250]
    log("  Found google reCAPTCHA frame")

    # --- Step 2: Wait for widget to be ready ---
    # The anchor/checkbox should be visible. Wait up to 10s.
    widget_ready = False
    for _ in range(20):
        try:
            anchor = google_frame.query_selector("#recaptcha-anchor")
            if anchor:
                box = anchor.bounding_box()
                if box and box["width"] > 0:
                    widget_ready = True
                    break
            # Also check for the spinner (means it's loading)
            spinner = google_frame.query_selector(".recaptcha-checkbox-spinner")
            if spinner:
                # Widget is loading, wait a bit more
                time.sleep(1)
                continue
        except Exception:
            pass
        time.sleep(0.5)

    detail["widget_ready"] = widget_ready
    if not widget_ready:
        log("  WARNING: widget may not be fully ready, trying anyway")

    time.sleep(1)  # extra settle

    # --- Step 3: Click the checkbox ---
    clicked = False
    try:
        # Try multiple selectors
        for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                    "div[role='checkbox']", "#recaptcha-anchor > div"]:
            el = google_frame.query_selector(sel)
            if el:
                el.click()
                clicked = True
                detail["click_sel"] = sel
                log(f"  Clicked: {sel}")
                break
    except Exception as e:
        detail["click_error"] = str(e)

    if not clicked:
        # Fallback: click at the center of the widget area
        try:
            widget = google_frame.query_selector(".recaptcha-widget, .g-recaptcha, #recaptcha")
            if widget:
                box = widget.bounding_box()
                if box:
                    # Click at center-left (where checkbox usually is)
                    page.mouse.click(box["x"] + 30, box["y"] + box["height"] / 2)
                    clicked = True
                    detail["click_sel"] = "mouse_fallback"
                    log("  Clicked via mouse fallback")
        except Exception as e:
            detail["mouse_error"] = str(e)

    time.sleep(2)  # let verification start

    # --- Step 4: Wait for verification to complete ---
    # Signs of completion:
    #   a) Spinner disappears and checkmark appears
    #   b) grecaptcha.getResponse() returns a token
    #   c) A new challenge appears (images) - means auto-verify failed
    verified = False
    challenge_appeared = False
    for i in range(30):  # up to 15s
        try:
            # Check for success (checkmark)
            checkmark = google_frame.query_selector(
                ".recaptcha-checkbox-success, .rc-checkbox-success, "
                "[class*='success'], .recaptcha-checkbox-checked"
            )
            if checkmark:
                verified = True
                detail["verify_signal"] = "checkmark"
                break

            # Check if spinner is still spinning (verification in progress)
            spinner = google_frame.query_selector(
                ".recaptcha-checkbox-spinner, .rc-spinner, [class*='spinner']"
            )
            if spinner:
                time.sleep(0.5)
                continue

            # Check for new challenge (images, slider)
            challenge = google_frame.query_selector(
                ".rc-image-selection, .rc-slider-captcha, "
                "[class*='challenge'], .recaptcha-challenge, "
                "img[class*='tile'], .rc-imageselect"
            )
            if challenge:
                challenge_appeared = True
                detail["challenge"] = "visual"
                log("  Visual challenge appeared")
                break

            # Check for error text
            err = google_frame.query_selector(
                "[class*='error'], .rc-error, [class*='Error']"
            )
            if err:
                detail["verify_error"] = (err.text_content() or "")[:100]
                break

        except Exception:
            pass
        time.sleep(0.5)

    detail["verified"] = verified
    detail["challenge_appeared"] = challenge_appeared
    log(f"  Verification: verified={verified}, challenge={challenge_appeared}")

    # --- Step 5: Extract token ---
    token = None

    # 5a: Wait for token in textarea (any frame)
    if verified or not challenge_appeared:
        for i in range(20):  # up to 10s
            for fr in page.frames:
                try:
                    ta = fr.query_selector("textarea[name='g-recaptcha-response']")
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
            time.sleep(0.5)

    # 5b: Try grecaptcha.getResponse() in TMD frame and main page
    if not token:
        for fr in page.frames:
            try:
                u = (fr.url or "").lower()
                if "tmd" in u or "punish" in u or fr == page.main_frame:
                    resp = fr.evaluate("""() => {
                        try {
                            if (window.grecaptcha && grecaptcha.getResponse) {
                                return grecaptcha.getResponse();
                            }
                            return null;
                        } catch(e) { return null; }
                    }""")
                    if resp and len(resp) > 50:
                        token = resp
                        detail["token_from"] = f"grecaptcha.getResponse() in {u[:80]}"
                        break
            except Exception:
                pass

    # 5c: Try in google frame itself
    if not token:
        try:
            resp = google_frame.evaluate("""() => {
                try {
                    if (window.grecaptcha && grecaptcha.getResponse) {
                        return grecaptcha.getResponse();
                    }
                    return null;
                } catch(e) { return null; }
            }""")
            if resp and len(resp) > 50:
                token = resp
                detail["token_from"] = "grecaptcha.getResponse() in google frame"
        except Exception:
            pass

    # 5d: Fallback - programmatic execute (for invisible/auto mode)
    if not token and not challenge_appeared:
        log("  Trying programmatic grecaptcha.execute()…")
        for fr in page.frames:
            try:
                u = (fr.url or "").lower()
                if "tmd" in u or "punish" in u or fr == page.main_frame:
                    resp = fr.evaluate("""async () => {
                        try {
                            if (window.grecaptcha && grecaptcha.execute) {
                                const token = await grecaptcha.execute();
                                return token;
                            }
                            return null;
                        } catch(e) { return 'ERR:' + e.message; }
                    }""")
                    if resp and len(resp) > 50 and not str(resp).startswith("ERR"):
                        token = resp
                        detail["token_from"] = "grecaptcha.execute() programmatic"
                        break
                    elif str(resp).startswith("ERR"):
                        detail["execute_error"] = str(resp)
            except Exception:
                pass

    # 5e: Last resort - look for token in any input/textarea with long value
    if not token:
        for fr in page.frames:
            try:
                els = fr.query_selector_all("textarea, input[type='hidden']")
                for el in els:
                    try:
                        val = el.input_value()
                        if val and len(val) > 50 and "recaptcha" in (el.get_attribute("name") or "").lower():
                            token = val
                            detail["token_from"] = f"hidden_input in {fr.url[:80]}"
                            break
                    except Exception:
                        pass
                if token:
                    break
            except Exception:
                pass

    detail["token_found"] = bool(token)
    if token:
        detail["token_len"] = len(token)
        detail["token_preview"] = token[:60] + "…"

    return token, int((time.time() - t0) * 1000), detail


def extract_product_data(page):
    try:
        data = page.evaluate("""() => {
            const out = {};
            const t = document.querySelector('title');
            out.title = t ? t.textContent.trim() : '';
            const ogTitle = document.querySelector('meta[property="og:title"]');
            out.ogTitle = ogTitle ? ogTitle.content : '';
            const ogImage = document.querySelector('meta[property="og:image"]');
            out.ogImage = ogImage ? ogImage.content : '';
            const ogDesc = document.querySelector('meta[property="og:description"]');
            out.ogDesc = ogDesc ? ogDesc.content : '';
            let rp = '';
            try { rp = JSON.stringify(window.runParams || ''); } catch(e) { rp = String(window.runParams || ''); }
            out.runParams = rp;
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            const ld = [];
            scripts.forEach(s => { try { ld.push(JSON.parse(s.textContent)); } catch(e){} });
            out.jsonLd = ld;
            const priceEls = document.querySelectorAll(
                '[class*="price"] [class*="current"], .pdp-price-current, ' +
                '[data-role="price"] .price, .ic-price-current, [class*="Price--current"], ' +
                '[class*="price-current"], [class*="PriceCurrent"], [class*="price"] .value'
            );
            out.domPrices = Array.from(priceEls).map(e => e.textContent.trim()).filter(Boolean);
            const stockEl = document.querySelector('[class*="stock"], [class*="availability"], [data-role="stock"]');
            out.stock = stockEl ? stockEl.textContent.trim() : null;
            const bc = document.querySelectorAll('[class*="breadcrumb"] a, [class*="Breadcrumb"] a, nav[aria-label="breadcrumb"] a');
            out.breadcrumbs = Array.from(bc).map(a => a.textContent.trim()).filter(Boolean);
            const sellerEl = document.querySelector('[class*="seller"], [class*="store-name"], [data-role="seller"]');
            out.seller = sellerEl ? sellerEl.textContent.trim() : '';
            const imgs = document.querySelectorAll(
                '[class*="gallery"] img, [class*="Gallery"] img, ' +
                '[class*="thumbnail"] img, .pdp-mod-gallery img, img[src*="ae01"], img[src*="ae-pic"]'
            );
            out.images = Array.from(imgs).map(i => i.src || i.getAttribute('data-src') || '').filter(u => u && !u.includes('150x150.gif'));
            const skuEls = document.querySelectorAll('[class*="sku"] [class*="item"], [class*="Sku"] [class*="item"], [data-role="sku"] .sku-item');
            out.variantsN = skuEls.length;
            const inlineScripts = document.querySelectorAll('script:not([src])');
            const summaries = [];
            inlineScripts.forEach(s => {
                const txt = s.textContent || '';
                if (txt.length > 1000) {
                    summaries.push({len: txt.length, head: txt.substring(0, 300)});
                }
            });
            summaries.sort((a,b) => b.len - a.len);
            out.inlineScripts = summaries.slice(0, 8);
            out._dida = window._dida ? Object.keys(window._dida) : null;
            out._pageConfig = window._pageConfig ? Object.keys(window._pageConfig) : null;
            out._initData = window._initData ? Object.keys(window._initData) : null;
            out._globalData = window._globalData ? Object.keys(window._globalData) : null;
            // Also check for window.__INIT_DATA__, window.__NEXT_DATA__, etc.
            out._INIT_DATA = window.__INIT_DATA__ ? Object.keys(window.__INIT_DATA__).slice(0,20) : null;
            out._NEXT_DATA = window.__NEXT_DATA__ ? Object.keys(window.__NEXT_DATA__).slice(0,20) : null;
            return out;
        }""")
        return data
    except Exception as e:
        diag["errors"].append(f"extract_product_data: {e}")
        return {}


def intercept_mtop(page, bucket):
    def on_response(resp):
        try:
            url = resp.url
            if "mtop" in url.lower() or "acs.aliexpress" in url:
                body = ""
                try:
                    body = resp.text()[:3000]
                except Exception:
                    pass
                diag[bucket].append({
                    "url": url[:250],
                    "status": resp.status,
                    "body_preview": body[:1000],
                    "has_fail": "FAIL_SYS" in body or "RGV5" in body,
                    "has_data": '"data"' in body and "FAIL_SYS" not in body,
                    "body_len": len(body),
                })
        except Exception:
            pass
    page.on("response", on_response)
    return on_response


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

        # ===== Phase 1: warmup =====
        diag["phase"] = "warmup"
        log("Phase 1: warmup")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)
        diag["cookies_warm"] = grab_cookies(ctx)
        log(f"  homepage cookies: {len(diag['cookies_warm'])}")

        page.goto("https://www.aliexpress.com/category/100003109/smartphones.html",
                  wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        log(f"  category cookies: {len(grab_cookies(ctx))}")

        # ===== Phase 2: product page =====
        diag["phase"] = "product"
        log(f"Phase 2: product {PRODUCT_URL}")
        handler = intercept_mtop(page, "mtop_intercepted")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        diag["final_url"] = page.url
        diag["title_tag"] = page.title()
        diag["cookies_product"] = grab_cookies(ctx)
        log(f"  url: {diag['final_url']}")
        log(f"  title: {diag['title_tag'][:100]}")

        # ===== Phase 3: TMD + reCAPTCHA =====
        diag["phase"] = "tmd"
        tmd_iframes = page.query_selector_all(
            "iframe[src*='_____tmd____'], iframe[src*='punish']"
        )
        tmd_frames = [fr for fr in page.frames if "_____tmd_____" in (fr.url or "") or "punish" in (fr.url or "")]
        diag["tmd_detail"] = {
            "iframe_count": len(tmd_iframes),
            "frame_count": len(tmd_frames),
            "frame_urls": [fr.url[:200] for fr in tmd_frames],
        }

        if tmd_frames or tmd_iframes:
            diag["tmd"] = "present"
            log(f"  TMD: {len(tmd_frames)} frames, {len(tmd_iframes)} iframes")

            # Wait for reCAPTCHA to fully initialize
            log("  Waiting 5s for reCAPTCHA to initialize…")
            time.sleep(5)

            # Solve
            diag["phase"] = "recaptcha"
            log("  Solving reCAPTCHA (up to 90s)…")
            token, elapsed, rc_detail = solve_recaptcha_v2(page, timeout_s=90)
            diag["recaptcha"] = rc_detail
            diag["captcha_token_preview"] = token[:60] + "…" if token and len(token) > 60 else token
            diag["captcha_await_ms"] = elapsed
            log(f"  Token: {'YES' if token else 'NO'} ({elapsed}ms)")
            log(f"  Detail: {json.dumps(rc_detail, ensure_ascii=False)[:400]}")

            if token:
                # Wait for TMD JS to POST token and get new cookies
                log("  Waiting 8s for TMD POST + cookie update…")
                time.sleep(8)
                cookies_after = grab_cookie_values(ctx)
                diag["cookies_after_token"] = cookies_after
                # Check if x5secdata changed (success signal)
                x5 = [v for k, v in cookies_after.items() if k in ("x5secdata", "x5sectag")]
                diag["tmd_post_detected"] = any("recaptcha" not in v.lower() for v in x5) if x5 else False
                log(f"  x5secdata/x5sectag: {x5}")

            # ===== Phase 4: Reload attempts =====
            for attempt in range(1, 4):
                diag["phase"] = f"reload_{attempt}"
                diag["reload_count"] = attempt
                log(f"  Reload {attempt}…")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(6)
                diag["final_url"] = page.url
                diag["title_tag"] = page.title()
                diag["cookies_product"] = grab_cookies(ctx)
                log(f"  url: {diag['final_url']}")
                log(f"  title: {diag['title_tag'][:100]}")

                tmd2 = page.query_selector_all(
                    "iframe[src*='_____tmd____'], iframe[src*='punish']"
                )
                if not tmd2:
                    diag["tmd"] = "passed"
                    log(f"  *** TMD PASSED (attempt {attempt}) ***")
                    break
                else:
                    diag["tmd"] = f"still_{attempt}"
                    log(f"  TMD still present (attempt {attempt})")
                    if attempt < 3 and not token:
                        time.sleep(2)
                        token, el2, det2 = solve_recaptcha_v2(page, timeout_s=60)
                        if token:
                            diag["recaptcha"][f"retry_{attempt}"] = det2
                            time.sleep(8)
        else:
            diag["tmd"] = "none"
            log("  No TMD — direct access!")

        # ===== Phase 5: Extract data =====
        diag["phase"] = "extract"
        log("Phase 5: extracting data")
        pd = extract_product_data(page)
        diag["title_tag"] = pd.get("title", diag["title_tag"])
        diag["meta_og_title"] = pd.get("ogTitle", "")
        diag["meta_og_image"] = pd.get("ogImage", "")
        diag["meta_description"] = pd.get("ogDesc", "")
        diag["run_params_raw"] = (pd.get("runParams") or "")[:500]
        diag["run_params_len"] = len(diag["run_params_raw"])
        diag["prices"] = pd.get("domPrices", [])
        diag["dom_prices"] = pd.get("domPrices", [])
        diag["stock"] = pd.get("stock")
        diag["category"] = pd.get("breadcrumbs", [None])[-1] if pd.get("breadcrumbs") else None
        diag["seller"] = pd.get("seller", "")
        diag["images"] = (pd.get("images", []) or [])[:10]
        diag["variants_n"] = pd.get("variantsN", 0)
        diag["json_ld"] = pd.get("jsonLd")
        diag["inline_scripts_summary"] = pd.get("inlineScripts", [])
        diag["_dida_keys"] = pd.get("_dida")
        diag["_pageConfig_keys"] = pd.get("_pageConfig")
        diag["_initData_keys"] = pd.get("_initData")
        diag["_globalData_keys"] = pd.get("_globalData")
        diag["_INIT_DATA_keys"] = pd.get("_INIT_DATA")
        diag["_NEXT_DATA_keys"] = pd.get("_NEXT_DATA")

        # ===== Phase 6: Determine source =====
        if diag["prices"] and any(len(p) > 1 and not p.isdigit() for p in diag["prices"]):
            diag["source"] = "dom"
        elif diag["run_params_len"] > 100:
            diag["source"] = "runParams"
        elif diag["json_ld"]:
            diag["source"] = "json_ld"
        elif diag["meta_og_title"]:
            diag["source"] = "meta_og"
        else:
            diag["source"] = "none"

        diag["phase"] = "done"
        log(f"DONE. source={diag['source']}, tmd={diag['tmd']}, "
            f"prices={diag['prices'][:5]}, og_title={diag['meta_og_title'][:80]}")
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
