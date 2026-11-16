"""
diag_ali_pw44.py — AliExpress TMD/reCAPTCHA Enterprise bypass + product data.
Fixes vs pw43:
  - Proper nested-frame handling (TMD frame → google.com reCAPTCHA frame)
  - Click the reCAPTCHA checkbox, wait for REAL token (>50 chars)
  - Wait for TMD JS to process token before reload
  - Meta tags as fallback data source
  - Multi-reload if TMD persists
"""
import json, time, sys, os, traceback, re
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\pw_ali_profile_pw44"
OUT = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw44_result.json"

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
    "reload_count": 0,
    "mtop_intercepted": [],
    "mtop_after_reload": [],
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

def find_frames_by_kw(page, keywords):
    """Find frames whose URL matches any keyword."""
    hits = []
    for fr in page.frames:
        u = (fr.url or "").lower()
        for kw in keywords:
            if kw in u:
                hits.append({"name": fr.name, "url": (fr.url or "")[:250]})
                break
    return hits

def solve_recaptcha_enterprise(page, timeout_s=120):
    """
    Navigate into the reCAPTCHA Enterprise iframe (google.com), click the
    checkbox, and wait for the parent TMD frame's g-recaptcha-response
    textarea to receive a real token (>50 chars).
    Returns (token_str_or_None, elapsed_ms, detail_dict).
    """
    detail = {"found_google_frame": False, "clicked_checkbox": False,
              "challenge_type": "unknown", "auto_solved": False}
    t0 = time.time()
    deadline = t0 + timeout_s

    # Phase A: find the google.com reCAPTCHA frame
    while time.time() < deadline:
        frames = page.frames
        google_frame = None
        tmd_frame = None
        for fr in frames:
            u = (fr.url or "").lower()
            if "google.com/recaptcha" in u or "recaptcha/enterprise" in u:
                google_frame = fr
            if "_____tmd_____" in u or ("punish" in u and "acs.aliexpress" in u):
                tmd_frame = fr

        if google_frame:
            detail["found_google_frame"] = True
            detail["google_frame_url"] = (google_frame.url or "")[:250]
            break
        time.sleep(1)

    if not google_frame:
        return None, int((time.time() - t0) * 1000), detail

    # Phase B: click the checkbox inside google frame
    try:
        # reCAPTCHA Enterprise "normal" size: checkbox is #recaptcha-anchor
        # or .recaptcha-checkbox, or a div with role="checkbox"
        for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                    "div[role='checkbox']", ".recaptcha-checkbox-spinner",
                    "#recaptcha-anchor div", ".rc-checkbox"]:
            el = google_frame.query_selector(sel)
            if el:
                el.click()
                detail["clicked_checkbox"] = True
                detail["checkbox_selector"] = sel
                log(f"  Clicked checkbox via '{sel}'")
                break
    except Exception as e:
        detail["click_error"] = str(e)
        diag["errors"].append(f"recaptcha_click: {e}")

    time.sleep(2)  # let the challenge render

    # Phase C: check if a visual challenge appeared (image grid)
    try:
        challenge = google_frame.query_selector(
            ".rc-image-selection, .rc-slider-captcha, .recaptcha-challenge"
        )
        if challenge:
            detail["challenge_type"] = "visual"
            log("  Visual challenge detected (may need manual solve)")
        else:
            detail["challenge_type"] = "auto_or_checkbox"
    except Exception:
        pass

    # Phase D: wait for token in the TMD frame's textarea
    # The token appears in textarea[name="g-recaptcha-response"] in the TMD frame
    # (the parent of the google frame, or in the main page)
    while time.time() < deadline:
        # Search ALL frames for the response textarea
        for fr in page.frames:
            u = (fr.url or "").lower()
            # Look in TMD frame, main page, or any frame
            try:
                ta = fr.query_selector("textarea[name='g-recaptcha-response']")
                if ta:
                    val = ta.input_value()
                    if val and len(val) > 50:
                        detail["token_frame_url"] = u[:200]
                        detail["token_len"] = len(val)
                        return val, int((time.time() - t0) * 1000), detail
            except Exception:
                pass
        time.sleep(1.5)

    return None, int((time.time() - t0) * 1000), detail

def extract_product_data(page):
    """Pull product info from DOM, meta tags, runParams, JSON-LD, inline scripts."""
    try:
        data = page.evaluate("""() => {
            const out = {};
            // --- title ---
            const t = document.querySelector('title');
            out.title = t ? t.textContent.trim() : '';
            // --- meta og: tags (server-rendered, available even with TMD) ---
            const ogTitle = document.querySelector('meta[property="og:title"]');
            out.ogTitle = ogTitle ? ogTitle.content : '';
            const ogImage = document.querySelector('meta[property="og:image"]');
            out.ogImage = ogImage ? ogImage.content : '';
            const ogDesc = document.querySelector('meta[property="og:description"]');
            out.ogDesc = ogDesc ? ogDesc.content : '';
            const descMeta = document.querySelector('meta[name="description"]');
            out.descMeta = descMeta ? descMeta.content : '';
            // --- runParams ---
            let rp = '';
            try { rp = JSON.stringify(window.runParams || ''); } catch(e) { rp = String(window.runParams || ''); }
            out.runParams = rp;
            // --- JSON-LD ---
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            const ld = [];
            scripts.forEach(s => { try { ld.push(JSON.parse(s.textContent)); } catch(e){} });
            out.jsonLd = ld;
            // --- DOM prices ---
            const priceEls = document.querySelectorAll(
                '[class*="price"] [class*="current"], .pdp-price-current, ' +
                '[data-role="price"] .price, .ic-price-current, [class*="Price--current"], ' +
                '[class*="price-current"], [class*="PriceCurrent"]'
            );
            out.domPrices = Array.from(priceEls).map(e => e.textContent.trim()).filter(Boolean);
            // --- stock ---
            const stockEl = document.querySelector('[class*="stock"], [class*="availability"], [data-role="stock"]');
            out.stock = stockEl ? stockEl.textContent.trim() : null;
            // --- breadcrumbs ---
            const bc = document.querySelectorAll('[class*="breadcrumb"] a, [class*="Breadcrumb"] a, nav[aria-label="breadcrumb"] a');
            out.breadcrumbs = Array.from(bc).map(a => a.textContent.trim()).filter(Boolean);
            // --- seller ---
            const sellerEl = document.querySelector('[class*="seller"], [class*="store-name"], [data-role="seller"]');
            out.seller = sellerEl ? sellerEl.textContent.trim() : '';
            // --- images ---
            const imgs = document.querySelectorAll(
                '[class*="gallery"] img, [class*="Gallery"] img, ' +
                '[class*="thumbnail"] img, .pdp-mod-gallery img, img[src*="ae01"], img[src*="ae-pic"]'
            );
            out.images = Array.from(imgs).map(i => i.src || i.getAttribute('data-src') || '').filter(u => u && !u.includes('150x150.gif'));
            // --- variants ---
            const skuEls = document.querySelectorAll('[class*="sku"] [class*="item"], [class*="Sku"] [class*="item"], [data-role="sku"] .sku-item');
            out.variantsN = skuEls.length;
            // --- inline scripts summary (top 10 by size, first 200 chars) ---
            const inlineScripts = document.querySelectorAll('script:not([src])');
            const summaries = [];
            inlineScripts.forEach(s => {
                const txt = s.textContent || '';
                if (txt.length > 500) {
                    summaries.push({len: txt.length, head: txt.substring(0, 200)});
                }
            });
            summaries.sort((a,b) => b.len - a.len);
            out.inlineScripts = summaries.slice(0, 10);
            // --- window globals ---
            out._dida = window._dida ? Object.keys(window._dida) : null;
            out._pageConfig = window._pageConfig ? Object.keys(window._pageConfig) : null;
            out._initData = window._initData ? Object.keys(window._initData) : null;
            out._globalData = window._globalData ? Object.keys(window._globalData) : null;
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
                    "body_preview": body[:800],
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
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        """)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ===== Phase 1: warmup =====
        diag["phase"] = "warmup_homepage"
        log("Phase 1: warmup homepage")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)
        diag["cookies_warm"] = grab_cookies(ctx)
        log(f"  homepage cookies: {len(diag['cookies_warm'])}")

        # ===== Phase 2: category =====
        diag["phase"] = "warmup_category"
        log("Phase 2: category page")
        page.goto("https://www.aliexpress.com/category/100003109/smartphones.html",
                  wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        log(f"  category cookies: {len(grab_cookies(ctx))}")

        # ===== Phase 3: product page =====
        diag["phase"] = "product_visit"
        log(f"Phase 3: product {PRODUCT_URL}")
        handler1 = intercept_mtop(page, "mtop_intercepted")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        diag["final_url"] = page.url
        diag["title_tag"] = page.title()
        diag["cookies_product"] = grab_cookies(ctx)
        log(f"  url: {diag['final_url']}")
        log(f"  title: {diag['title_tag'][:100]}")
        log(f"  cookies: {len(diag['cookies_product'])}")

        # ===== Phase 4: TMD detection + reCAPTCHA solve =====
        diag["phase"] = "tmd_solve"
        tmd_frames = find_frames_by_kw(page, ["_____tmd_____", "punish"])
        tmd_iframes = page.query_selector_all(
            "iframe[src*='_____tmd____'], iframe[src*='punish']"
        )
        diag["tmd_detail"] = {
            "frames": tmd_frames,
            "iframe_count": len(tmd_iframes),
        }

        if tmd_frames or tmd_iframes:
            diag["tmd"] = "present"
            log(f"  TMD detected: {len(tmd_frames)} frames, {len(tmd_iframes)} iframes")

            # Wait for reCAPTCHA Enterprise to fully load (google.com frame)
            log("  Waiting for reCAPTCHA Enterprise frame…")
            time.sleep(3)

            # Solve reCAPTCHA
            diag["phase"] = "recaptcha_solve"
            log("  Solving reCAPTCHA Enterprise (up to 120 s)…")
            token, elapsed, rc_detail = solve_recaptcha_enterprise(page, timeout_s=120)
            diag["recaptcha"] = rc_detail
            diag["captcha_token_preview"] = token[:60] + "…" if token and len(token) > 60 else token
            diag["captcha_await_ms"] = elapsed
            log(f"  token: {'YES' if token else 'NO'} ({elapsed} ms)")
            log(f"  detail: {json.dumps(rc_detail, ensure_ascii=False)[:300]}")

            # After token, wait for TMD JS to process it (POST to server, set cookies)
            if token:
                log("  Waiting 5 s for TMD JS to process token…")
                time.sleep(5)
                diag["cookies_product"] = grab_cookies(ctx)
                log(f"  cookies after token: {len(diag['cookies_product'])}")

            # ===== Phase 5: reload (up to 3 attempts) =====
            for attempt in range(1, 4):
                diag["phase"] = f"reload_{attempt}"
                diag["reload_count"] = attempt
                log(f"  Reload attempt {attempt}…")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(6)
                diag["final_url"] = page.url
                diag["title_tag"] = page.title()
                diag["cookies_product"] = grab_cookies(ctx)
                log(f"  url: {diag['final_url']}")
                log(f"  title: {diag['title_tag'][:100]}")

                # Check TMD
                tmd2 = find_frames_by_kw(page, ["_____tmd_____", "punish"])
                if not tmd2:
                    diag["tmd"] = "passed"
                    log(f"  TMD PASSED on attempt {attempt}!")
                    break
                else:
                    diag["tmd"] = f"still_present_attempt_{attempt}"
                    log(f"  TMD still present (attempt {attempt})")
                    if attempt < 3:
                        log("  Re-solving reCAPTCHA…")
                        time.sleep(2)
                        token2, el2, det2 = solve_recaptcha_enterprise(page, timeout_s=90)
                        if token2:
                            diag["recaptcha"][f"retry_{attempt}"] = det2
                            time.sleep(5)
        else:
            diag["tmd"] = "none"
            log("  No TMD — direct access!")

        # ===== Phase 6: extract data =====
        diag["phase"] = "extract"
        log("Phase 6: extracting product data")
        pd = extract_product_data(page)
        diag["title_tag"] = pd.get("title", diag["title_tag"])
        diag["meta_og_title"] = pd.get("ogTitle", "")
        diag["meta_og_image"] = pd.get("ogImage", "")
        diag["meta_description"] = pd.get("ogDesc", "") or pd.get("descMeta", "")
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

        # ===== Phase 7: MTOP manual fetch (after TMD pass) =====
        diag["phase"] = "mtop_manual"
        log("Phase 7: manual MTOP fetch")
        try:
            mtop_result = page.evaluate("""async () => {
                try {
                    const data = JSON.stringify({productId: '1005011813323763'});
                    const url = '/h5/mtop.aliexpress.pdp.pc.query/1.0/?jsv=2.7.2&appKey=24568761'
                        + '&t=' + Date.now()
                        + '&sign=placeholder&api=mtop.aliexpress.pdp.pc.query&v=1.0'
                        + '&type=originaljson&dataType=json&data=' + encodeURIComponent(data);
                    const r = await fetch(url, {credentials: 'include',
                        headers: {'Accept': 'application/json'}});
                    const txt = await r.text();
                    return {status: r.status, body: txt.substring(0, 3000)};
                } catch(e) { return {error: String(e)}; }
            }""")
            diag["mtop_manual_fetch"] = mtop_result
            log(f"  MTOP: status={mtop_result.get('status', 'err')}, "
                f"body_len={len(mtop_result.get('body',''))}")
        except Exception as e:
            diag["errors"].append(f"mtop_manual: {e}")

        # ===== Phase 8: determine source =====
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
