"""
diag_ali_pw43.py — AliExpress: TMD/CAPTCHA bypass + product data extraction.
Key fix vs pw41: after getting CAPTCHA token, RELOAD the page instead of
retrying MTOP via page.evaluate. Also parse the ~280 KB resource for data.
"""
import json, time, sys, os, traceback
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\pw_ali_profile_pw43"
OUT = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw43_result.json"

diag = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "product_id": PRODUCT_ID,
    "product_url": PRODUCT_URL,
    "profile": PROFILE,
    "phase": "init",
    "cookies_warm": [],
    "cookies_category": [],
    "cookies_product": [],
    "final_url": "",
    "title_tag": "",
    "tmd": "none",
    "tmd_detail": {},
    "captcha_token": None,
    "captcha_await_ms": 0,
    "reload_after_tmd": False,
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
    "big_resource_size": 0,
    "big_resource_keys": [],
    "source": "none",
    "errors": [],
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def grab_cookies(context):
    try:
        return [c["name"] for c in context.cookies()]
    except Exception:
        return []

def find_tmd_frames(page):
    """Return list of frames whose URL contains _____tmd____ or punish."""
    hits = []
    for fr in page.frames:
        u = fr.url or ""
        if "_____tmd____" in u or "punish" in u or "recaptcha" in u.lower():
            hits.append({"name": fr.name, "url": u[:200]})
    return hits

def wait_for_captcha_token(page, timeout_s=60):
    """Look inside TMD/recaptcha iframe for a textarea or input that holds a token.
    Returns (token_str_or_None, elapsed_ms)."""
    t0 = time.time()
    deadline = t0 + timeout_s
    while time.time() < deadline:
        frames = page.frames
        for fr in frames:
            u = fr.url or ""
            if "_____tmd____" not in u and "punish" not in u and "recaptcha" not in u.lower():
                continue
            try:
                # Common selectors for the hidden token field
                for sel in ["textarea[name='token']", "textarea", "input[name='token']",
                            "#slidetoken", "#token", "textarea[aria-label]"]:
                    el = fr.query_selector(sel)
                    if el:
                        val = el.input_value()
                        if val and len(val) > 10:
                            return val, int((time.time() - t0) * 1000)
                        # sometimes the value is in an attribute
                        for attr in ("value", "data-token", "id"):
                            av = el.get_attribute(attr)
                            if av and len(av) > 10:
                                return av, int((time.time() - t0) * 1000)
            except Exception as e:
                diag["errors"].append(f"tmd_frame_sel: {e}")
        time.sleep(1.5)
    return None, int((time.time() - t0) * 1000)

def extract_product_data(page):
    """Pull product info from DOM, runParams, JSON-LD, and window globals."""
    try:
        data = page.evaluate("""() => {
            const out = {};
            // --- title ---
            const t = document.querySelector('title');
            out.title = t ? t.textContent.trim() : '';
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
                '[data-role="price"] .price, .ic-price-current, [class*="Price--current"]'
            );
            out.domPrices = Array.from(priceEls).map(e => e.textContent.trim()).filter(Boolean);
            // --- stock / availability ---
            const stockEl = document.querySelector('[class*="stock"], [class*="availability"], [data-role="stock"]');
            out.stock = stockEl ? stockEl.textContent.trim() : null;
            // --- category / breadcrumbs ---
            const bc = document.querySelectorAll('[class*="breadcrumb"] a, [class*="Breadcrumb"] a, nav[aria-label="breadcrumb"] a');
            out.breadcrumbs = Array.from(bc).map(a => a.textContent.trim()).filter(Boolean);
            // --- seller ---
            const sellerEl = document.querySelector('[class*="seller"], [class*="store-name"], [data-role="seller"]');
            out.seller = sellerEl ? sellerEl.textContent.trim() : '';
            // --- images ---
            const imgs = document.querySelectorAll(
                '[class*="gallery"] img, [class*="Gallery"] img, ' +
                '[class*="thumbnail"] img, .pdp-mod-gallery img, img[src*="ae01"]'
            );
            out.images = Array.from(imgs).map(i => i.src || i.getAttribute('data-src') || '').filter(u => u && !u.includes('150x150.gif'));
            // --- variants / SKU count ---
            const skuEls = document.querySelectorAll('[class*="sku"] [class*="item"], [class*="Sku"] [class*="item"], [data-role="sku"] .sku-item');
            out.variantsN = skuEls.length;
            // --- window globals that may hold data ---
            out._dida = window._dida ? Object.keys(window._dida) : null;
            out._pageConfig = window._pageConfig ? Object.keys(window._pageConfig) : null;
            out._initData = window._initData ? Object.keys(window._initData) : null;
            // --- meta tags ---
            const metas = {};
            document.querySelectorAll('meta[name], meta[property]').forEach(m => {
                const k = m.getAttribute('name') || m.getAttribute('property');
                if (k) metas[k] = m.getAttribute('content') || '';
            });
            out.metas = metas;
            return out;
        }""")
        return data
    except Exception as e:
        diag["errors"].append(f"extract_product_data: {e}")
        return {}

def intercept_mtop(page, bucket):
    """Register a response listener that logs MTOP responses into diag[bucket]."""
    def on_response(resp):
        try:
            url = resp.url
            if "mtop" in url.lower() or "acs.aliexpress" in url:
                body = ""
                try:
                    body = resp.text()[:2000]
                except Exception:
                    pass
                diag[bucket].append({
                    "url": url[:250],
                    "status": resp.status,
                    "body_preview": body[:500],
                    "has_fail": "FAIL_SYS" in body or "RGV5" in body,
                    "has_data": '"data"' in body and "FAIL_SYS" not in body,
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
        """)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ---------- Phase 1: warmup ----------
        diag["phase"] = "warmup_homepage"
        log("Phase 1: warmup homepage")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)
        diag["cookies_warm"] = grab_cookies(ctx)
        log(f"  homepage cookies: {len(diag['cookies_warm'])}")

        # ---------- Phase 2: category page ----------
        diag["phase"] = "warmup_category"
        log("Phase 2: category page")
        page.goto("https://www.aliexpress.com/category/100003109/smartphones.html",
                  wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        diag["cookies_category"] = grab_cookies(ctx)
        log(f"  category cookies: {len(diag['cookies_category'])}")

        # ---------- Phase 3: product page (first visit, expect TMD) ----------
        diag["phase"] = "product_first"
        log(f"Phase 3: product {PRODUCT_URL}")
        handler1 = intercept_mtop(page, "mtop_intercepted")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        diag["final_url"] = page.url
        diag["title_tag"] = page.title()
        log(f"  final_url: {diag['final_url']}")
        log(f"  title: {diag['title_tag'][:100]}")
        diag["cookies_product"] = grab_cookies(ctx)
        log(f"  product cookies: {len(diag['cookies_product'])}")

        # ---------- Phase 4: detect TMD ----------
        diag["phase"] = "tmd_detect"
        tmd_frames = find_tmd_frames(page)
        tmd_iframes = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish'], iframe[src*='recaptcha']")
        diag["tmd_detail"] = {
            "frames": tmd_frames,
            "iframe_count": len(tmd_iframes),
            "iframe_srcs": [i.get_attribute("src")[:200] for i in tmd_iframes],
        }
        if tmd_frames or tmd_iframes:
            diag["tmd"] = "present"
            log(f"  TMD detected: {len(tmd_frames)} frames, {len(tmd_iframes)} iframes")

            # Wait for captcha token
            diag["phase"] = "tmd_wait_token"
            log("  Waiting for CAPTCHA token (up to 90 s)…")
            token, elapsed = wait_for_captcha_token(page, timeout_s=90)
            diag["captcha_token"] = token[:50] + "…" if token and len(token) > 50 else token
            diag["captcha_await_ms"] = elapsed
            log(f"  token: {'YES' if token else 'NO'} ({elapsed} ms)")

            if token:
                # Key: RELOAD the page so the server sees the new session
                diag["phase"] = "tmd_reload"
                diag["reload_after_tmd"] = True
                log("  Reloading product page after token…")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(6)
                diag["final_url"] = page.url
                diag["title_tag"] = page.title()
                diag["cookies_product"] = grab_cookies(ctx)
                log(f"  after reload url: {diag['final_url']}")
                log(f"  after reload title: {diag['title_tag'][:100]}")
                log(f"  after reload cookies: {len(diag['cookies_product'])}")

                # Check if TMD is still there
                tmd2 = find_tmd_frames(page)
                diag["tmd_detail"]["after_reload_frames"] = tmd2
                if not tmd2:
                    diag["tmd"] = "passed"
                    log("  TMD appears to be PASSED")
                else:
                    diag["tmd"] = "still_present_after_reload"
                    log("  TMD still present after reload")
            else:
                diag["tmd"] = "timeout_no_token"
                log("  No token received, trying anyway")
        else:
            diag["tmd"] = "none"
            log("  No TMD detected — good")

        # ---------- Phase 5: extract data ----------
        diag["phase"] = "extract"
        log("Phase 5: extracting product data")
        pd = extract_product_data(page)
        diag["title_tag"] = pd.get("title", diag["title_tag"])
        diag["run_params_raw"] = (pd.get("runParams") or "")[:500]
        diag["run_params_len"] = len(diag["run_params_raw"])
        diag["prices"] = pd.get("domPrices", [])
        diag["dom_prices"] = pd.get("domPrices", [])
        diag["stock"] = pd.get("stock")
        diag["breadcrumbs"] = pd.get("breadcrumbs", [])
        diag["category"] = pd.get("breadcrumbs", [None])[-1] if pd.get("breadcrumbs") else None
        diag["seller"] = pd.get("seller", "")
        diag["images"] = pd.get("images", [])[:10]
        diag["variants_n"] = pd.get("variantsN", 0)
        diag["json_ld"] = pd.get("jsonLd")
        diag["_dida_keys"] = pd.get("_dida")
        diag["_pageConfig_keys"] = pd.get("_pageConfig")
        diag["_initData_keys"] = pd.get("_initData")
        diag["metas"] = pd.get("metas", {})

        # ---------- Phase 6: big resource (~280 KB) ----------
        diag["phase"] = "big_resource"
        log("Phase 6: looking for big inline resource")
        try:
            big = page.evaluate("""() => {
                // Look for large script tags or window.__INIT_DATA__ etc.
                const scripts = document.querySelectorAll('script:not([src])');
                let biggest = {len: 0, keys: []};
                scripts.forEach(s => {
                    const t = s.textContent || '';
                    if (t.length > biggest.len) {
                        biggest.len = t.length;
                        // try to find JSON keys
                        try {
                            const obj = JSON.parse(t);
                            biggest.keys = Object.keys(obj);
                        } catch(e) {
                            // maybe it's an assignment like window.X = {...}
                            const m = t.match(/window\\.(\\w+)\\s*=/);
                            if (m) biggest.keys = ['window.' + m[1]];
                        }
                    }
                });
                return biggest;
            }""")
            diag["big_resource_size"] = big.get("len", 0)
            diag["big_resource_keys"] = big.get("keys", [])[:30]
            log(f"  biggest inline script: {big.get('len', 0)} bytes, keys: {big.get('keys', [])[:10]}")
        except Exception as e:
            diag["errors"].append(f"big_resource: {e}")

        # ---------- Phase 7: MTOP after reload ----------
        diag["phase"] = "mtop_after"
        log("Phase 7: checking MTOP responses after reload")
        # The responses captured during reload are in mtop_intercepted (same handler)
        # Let's also do a manual fetch of the PDP query
        try:
            mtop_result = page.evaluate("""async () => {
                try {
                    const r = await fetch('/h5/mtop.aliexpress.pdp.pc.query/1.0/?jsv=2.7.2&appKey=24568761&t=' + Date.now()
                        + '&sign=[REDACTED_AUTH_SECRET]&api=mtop.aliexpress.pdp.pc.query&v=1.0&type=originaljson'
                        + '&dataType=json&data=' + encodeURIComponent(JSON.stringify({productId: '1005011813323763'})),
                        {credentials: 'include'});
                    const txt = await r.text();
                    return {status: r.status, body: txt.substring(0, 1500)};
                } catch(e) { return {error: String(e)}; }
            }""")
            diag["mtop_manual_fetch"] = mtop_result
            log(f"  manual MTOP: {mtop_result.get('status', 'err')}")
        except Exception as e:
            diag["errors"].append(f"mtop_manual: {e}")

        # ---------- Phase 8: determine source ----------
        if diag["prices"] and any(len(p) > 1 and not p.isdigit() for p in diag["prices"]):
            diag["source"] = "dom"
        elif diag["run_params_len"] > 100:
            diag["source"] = "runParams"
        elif diag["json_ld"]:
            diag["source"] = "json_ld"
        elif diag["big_resource_size"] > 10000:
            diag["source"] = "big_resource"
        else:
            diag["source"] = "none"

        diag["phase"] = "done"
        log(f"DONE. source={diag['source']}, prices={diag['prices'][:5]}, tmd={diag['tmd']}")

        ctx.close()

    # Save
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
