"""
diag_ali_pw46.py — AliExpress: 3-track data extraction.
Track A: reCAPTCHA Enterprise via grecaptcha.execute() in TMD frame + human-like mouse
Track B: Parse full page HTML for inline JSON / __INIT_DATA__ / runParams
Track C: Mobile URL (m.aliexpress.com) as fallback
Goal: get price, title, image, stock, SKU, category — even if TMD isn't fully passed.
"""
import json, time, sys, os, traceback, re
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
MOBILE_URL = f"https://m.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\pw_ali_profile_pw46"
OUT = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw46_result.json"

diag = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "product_id": PRODUCT_ID,
    "phase": "init",
    "track_a": {"status": "pending", "detail": {}},
    "track_b": {"status": "pending", "detail": {}},
    "track_c": {"status": "pending", "detail": {}},
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
        return [c["name"] for c in ctx.cookies()]
    except Exception:
        return []

# ===================== TRACK A: reCAPTCHA =====================
def track_a_solve_recaptcha(page, timeout_s=100):
    """Solve reCAPTCHA Enterprise in TMD frame. Returns (token, detail)."""
    detail = {}
    t0 = time.time()

    # Step 1: Wait for TMD frame + google frame (up to 15s)
    tmd_frame = None
    google_frame = None
    for i in range(30):
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
    if not tmd_frame:
        detail["error"] = "no_tmd_frame"
        return None, detail

    log(f"  TMD frame: {tmd_frame.url[:120]}")
    if google_frame:
        log(f"  Google frame: {google_frame.url[:120]}")

    # Step 2: Wait for grecaptcha to be defined in TMD frame (up to 10s)
    grecaptcha_ready = False
    for i in range(20):
        try:
            res = tmd_frame.evaluate("""() => {
                try {
                    return typeof window.grecaptcha !== 'undefined' &&
                           typeof window.grecaptcha.execute === 'function';
                } catch(e) { return false; }
            }""")
            if res:
                grecaptcha_ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    detail["grecaptcha_ready"] = grecaptcha_ready
    log(f"  grecaptcha ready: {grecaptcha_ready}")

    # Step 3: Human-like mouse movement + click
    if google_frame:
        try:
            anchor = google_frame.query_selector("#recaptcha-anchor")
            if anchor:
                box = anchor.bounding_box()
                if box:
                    # Move mouse in a curve (3 steps)
                    sx, sy = box["x"] + 200, box["y"] + 150
                    page.mouse.move(sx, sy)
                    time.sleep(0.3)
                    page.mouse.move((sx + box["x"]) / 2, (sy + box["y"]) / 2)
                    time.sleep(0.2)
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    time.sleep(0.3)
                    # Click
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    detail["clicked"] = True
                    log("  Mouse click on checkbox")
        except Exception as e:
            detail["click_err"] = str(e)

    time.sleep(3)

    # Step 4: Try grecaptcha.execute() in TMD frame (programmatic token)
    token = None
    if grecaptcha_ready:
        for attempt in range(3):
            try:
                tok = tmd_frame.evaluate("""async () => {
                    try {
                        if (window.grecaptcha && typeof grecaptcha.execute === 'function') {
                            const t = await grecaptcha.execute();
                            if (t && t.length > 50) return t;
                        }
                        return null;
                    } catch(e) { return 'ERR:' + e.message; }
                }""")
                if tok and len(tok) > 50 and not str(tok).startswith("ERR"):
                    token = tok
                    detail["token_from"] = f"grecaptcha.execute() attempt {attempt+1}"
                    break
                elif str(tok).startswith("ERR"):
                    detail[f"exec_err_{attempt}"] = str(tok)
            except Exception as e:
                detail[f"exec_exc_{attempt}"] = str(e)
            time.sleep(3)

    # Step 5: Watch for token in textarea (any frame) via polling
    if not token:
        for i in range(30):  # 15s
            for fr in page.frames:
                try:
                    ta = fr.query_selector("textarea[name='[REDACTED_AUTH_SECRET]']")
                    if ta:
                        val = ta.input_value()
                        if val and len(val) > 50:
                            token = val
                            detail["token_from"] = f"textarea in frame {fr.url[:80]}"
                            break
                except Exception:
                    pass
            if token:
                break
            time.sleep(0.5)

    # Step 6: Also check main page
    if not token:
        try:
            tok = page.evaluate("""() => {
                try {
                    const ta = document.querySelector("textarea[name='[REDACTED_AUTH_SECRET]']");
                    if (ta && ta.value && ta.value.length > 50) return ta.value;
                    if (window.grecaptcha && grecaptcha.getResponse) {
                        const r = grecaptcha.getResponse();
                        if (r && r.length > 50) return r;
                    }
                    return null;
                } catch(e) { return null; }
            }""")
            if tok:
                token = tok
                detail["token_from"] = "main_page"
        except Exception:
            pass

    detail["token_found"] = bool(token)
    if token:
        detail["token_len"] = len(token)
        detail["token_preview"] = token[:60] + "…"
    detail["elapsed_ms"] = int((time.time() - t0) * 1000)
    return token, detail

# ===================== TRACK B: HTML parse =====================
def track_b_parse_html(page):
    """Extract data from full page HTML (inline scripts, meta, DOM)."""
    detail = {}
    try:
        html = page.content()
        detail["html_len"] = len(html)
    except Exception as e:
        detail["error"] = str(e)
        return detail

    # B1: Meta tags
    og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', html)
    og_image = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']*)["\']', html)
    detail["og_title"] = og_title.group(1) if og_title else ""
    detail["og_image"] = og_image.group(1) if og_image else ""

    # B2: Inline JSON in script tags — look for common patterns
    patterns = [
        (r'window\.__INIT_DATA__\s*=\s*(\{.*?\});?\s*</script>', "__INIT_DATA__"),
        (r'window\.runParams\s*=\s*(\{.*?\});?\s*</script>', "runParams"),
        (r'window\._dida\s*=\s*(\{.*?\});?\s*</script>', "_dida"),
        (r'window\._pageConfig\s*=\s*(\{.*?\});?\s*</script>', "_pageConfig"),
        (r'"priceInfo"\s*:\s*(\{[^}]+\})', "priceInfo"),
        (r'"skuPriceList"\s*:\s*(\[[^\]]*\])', "skuPriceList"),
        (r'"skuPathList"\s*:\s*(\[[^\]]*\])', "skuPathList"),
        (r'"productTitle"\s*:\s*"([^"]*)"', "productTitle"),
        (r'"minAmount"\s*:\s*([\d.]+)', "minAmount"),
        (r'"maxAmount"\s*:\s*([\d.]+)', "maxAmount"),
        (r'"currency"\s*:\s*"([^"]*)"', "currency"),
        (r'"stock"\s*:\s*(\d+)', "stock"),
        (r'"sellerName"\s*:\s*"([^"]*)"', "sellerName"),
        (r'"categoryPath"\s*:\s*(\[[^\]]*\])', "categoryPath"),
    ]
    found = {}
    for pat, name in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            found[name] = m.group(1)[:500]
    detail["inline_found"] = found

    # B3: JSON-LD
    ld_matches = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    json_ld = []
    for ld in ld_matches:
        try:
            json_ld.append(json.loads(ld.strip()))
        except Exception:
            pass
    detail["json_ld"] = json_ld

    # B4: Look for price patterns in HTML
    price_patterns = [
        r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,.]+)\s*$',
        r'data-price="([\d.]+)"',
        r'"price"\s*:\s*"([\d.]+)"',
        r'\$([\d]+\.[\d]{2})',
        r'€([\d]+\.[\d]{2})',
    ]
    prices_found = []
    for pp in price_patterns:
        ms = re.findall(pp, html, re.MULTILINE)
        prices_found.extend(ms[:10])
    detail["prices_regex"] = prices_found[:15]

    # B5: DOM extraction via evaluate
    try:
        dom_data = page.evaluate("""() => {
            const out = {};
            // All text nodes containing $ or € followed by digits
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const priceTexts = [];
            while (walker.nextNode()) {
                const t = walker.currentNode.textContent.trim();
                if (/^[\\$€£]\\?\\s?[\\d,]+\\.\\d{2}$/.test(t) || /^[\\d,]+\\.\\d{2}\\s*(USD|EUR|CNY|\\$|€)$/.test(t)) {
                    priceTexts.push(t);
                }
            }
            out.priceTexts = priceTexts.slice(0, 20);
            // Title from h1
            const h1 = document.querySelector('h1');
            out.h1 = h1 ? h1.textContent.trim() : '';
            // All images with ae-pic or ae01 in src
            const imgs = document.querySelectorAll('img[src*="ae-pic"], img[src*="ae01"], img[src*="aliexpress-media"]');
            out.images = Array.from(imgs).map(i => i.src || i.getAttribute('data-src') || '').filter(u => u && !u.includes('150x150.gif')).slice(0, 15);
            // Breadcrumb
            const bc = document.querySelectorAll('[class*="breadcrumb"] a, [class*="Breadcrumb"] a, nav a');
            out.breadcrumbs = Array.from(bc).map(a => a.textContent.trim()).filter(Boolean).slice(0, 10);
            return out;
        }""")
        detail["dom"] = dom_data
    except Exception as e:
        detail["dom_err"] = str(e)

    return detail

# ===================== TRACK C: Mobile URL =====================
def track_c_mobile(page):
    """Try mobile AliExpress URL for simpler anti-bot."""
    detail = {}
    try:
        page.goto(MOBILE_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)
        detail["url"] = page.url
        detail["title"] = page.title()
        detail["html_len"] = len(page.content())

        # Check for TMD on mobile
        tmd = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
        detail["tmd_iframes"] = len(tmd)

        # Extract data
        data = page.evaluate("""() => {
            const out = {};
            const t = document.querySelector('title');
            out.title = t ? t.textContent.trim() : '';
            const h1 = document.querySelector('h1');
            out.h1 = h1 ? h1.textContent.trim() : '';
            // Mobile price selectors
            const priceSels = [
                '[class*="price"] [class*="current"]', '[class*="Price"] [class*="current"]',
                '[class*="price-current"]', '[class*="PriceCurrent"]',
                '[data-role="price"]', '.pdp-price', '[class*="price"] .value'
            ];
            out.prices = [];
            priceSels.forEach(sel => {
                document.querySelectorAll(sel).forEach(e => {
                    const t = e.textContent.trim();
                    if (t && t.length < 30) out.prices.push(t);
                });
            });
            // Images
            const imgs = document.querySelectorAll('img[src*="ae-pic"], img[src*="aliexpress-media"]');
            out.images = Array.from(imgs).map(i => i.src || '').filter(u => u && !u.includes('150x150.gif')).slice(0, 10);
            // Stock
            const stockEl = document.querySelector('[class*="stock"], [class*="availability"]');
            out.stock = stockEl ? stockEl.textContent.trim() : '';
            // Inline data
            out.runParams = window.runParams ? JSON.stringify(window.runParams).substring(0, 500) : '';
            out._dida = window._dida ? Object.keys(window._dida) : null;
            return out;
        }""")
        detail["data"] = data
        detail["status"] = "ok"
    except Exception as e:
        detail["status"] = "error"
        detail["error"] = str(e)
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

        log("Warmup: category")
        page.goto("https://www.aliexpress.com/category/100003109/smartphones.html",
                  wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)

        # ===== Product page =====
        log(f"Product: {PRODUCT_URL}")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        log(f"  url: {page.url}")
        log(f"  title: {page.title()[:80]}")
        log(f"  cookies: {len(grab_cookies(ctx))}")

        # ===== TRACK B: Parse current page HTML (do this FIRST, before TMD interferes) =====
        log("Track B: parsing page HTML…")
        diag["phase"] = "track_b"
        tb = track_b_parse_html(page)
        diag["track_b"] = {"status": "done", "detail": tb}
        log(f"  html_len={tb.get('html_len', 0)}, og_title={tb.get('og_title', '')[:60]}")
        log(f"  inline_found: {list(tb.get('inline_found', {}).keys())}")
        log(f"  prices_regex: {tb.get('prices_regex', [])[:5]}")
        log(f"  dom prices: {tb.get('dom', {}).get('priceTexts', [])[:5]}")

        # ===== TRACK A: Solve reCAPTCHA =====
        log("Track A: solving reCAPTCHA…")
        diag["phase"] = "track_a"
        # Check if TMD is present
        tmd_iframes = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
        if tmd_iframes:
            log(f"  TMD present ({len(tmd_iframes)} iframes)")
            time.sleep(3)  # let reCAPTCHA initialize
            token, ta_detail = track_a_solve_recaptcha(page, timeout_s=100)
            diag["track_a"] = {"status": "done", "detail": ta_detail}
            log(f"  token: {'YES' if token else 'NO'}")
            log(f"  detail: {json.dumps(ta_detail, ensure_ascii=False)[:300]}")

            if token:
                # Wait for TMD to process
                log("  Waiting 8s for TMD POST…")
                time.sleep(8)
                # Reload
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(6)
                log(f"  after reload: {page.title()[:80]}")
                tmd2 = page.query_selector_all("iframe[src*='_____tmd____'], iframe[src*='punish']")
                if not tmd2:
                    log("  *** TMD PASSED ***")
                    diag["track_a"]["tmd_passed"] = True
                    # Re-extract data after TMD pass
                    tb2 = track_b_parse_html(page)
                    diag["track_b"]["after_tmd"] = tb2
                    log(f"  post-TMD prices: {tb2.get('prices_regex', [])[:5]}")
                    log(f"  post-TMD dom prices: {tb2.get('dom', {}).get('priceTexts', [])[:5]}")
                else:
                    log("  TMD still present after reload")
                    diag["track_a"]["tmd_passed"] = False
        else:
            diag["track_a"] = {"status": "no_tmd", "detail": {}}
            log("  No TMD — direct access!")

        # ===== TRACK C: Mobile URL =====
        log("Track C: mobile URL…")
        diag["phase"] = "track_c"
        tc = track_c_mobile(page)
        diag["track_c"] = {"status": "done", "detail": tc}
        log(f"  url: {tc.get('url', '')[:80]}")
        log(f"  title: {tc.get('title', '')[:60]}")
        log(f"  prices: {tc.get('data', {}).get('prices', [])[:5]}")
        log(f"  tmd_iframes: {tc.get('tmd_iframes', 0)}")

        # ===== Merge best data =====
        diag["phase"] = "merge"
        fd = diag["final_data"]

        # Title: prefer h1 > og_title > mobile title
        dom = tb.get("dom", {})
        if dom.get("h1"):
            fd["title"] = dom["h1"]
        elif tb.get("og_title"):
            fd["title"] = tb["og_title"]
        elif tc.get("data", {}).get("h1"):
            fd["title"] = tc["data"]["h1"]
        elif tc.get("title"):
            fd["title"] = tc["title"]

        # Price: prefer DOM > regex > mobile
        all_prices = (dom.get("priceTexts", []) + tb.get("prices_regex", []) +
                      tc.get("data", {}).get("prices", []))
        # Filter: keep only ones that look like real prices
        real_prices = [p for p in all_prices if re.search(r'\d+[\.,]\d{2}', p) or re.search(r'^[\$€£]\s?\d+', p)]
        if real_prices:
            fd["price"] = real_prices[0]
        elif tb.get("inline_found", {}).get("minAmount"):
            fd["price"] = tb["inline_found"]["minAmount"]

        # Currency
        if tb.get("inline_found", {}).get("currency"):
            fd["currency"] = tb["inline_found"]["currency"]
        elif "$" in fd["price"]:
            fd["currency"] = "USD"
        elif "€" in fd["price"]:
            fd["currency"] = "EUR"

        # Images
        all_imgs = (dom.get("images", []) + [tb.get("og_image", "")] +
                    tc.get("data", {}).get("images", []))
        seen = set()
        fd["images"] = []
        for img in all_imgs:
            if img and img not in seen and not img.endswith(".gif"):
                seen.add(img)
                fd["images"].append(img)
        fd["images"] = fd["images"][:10]

        # Stock
        fd["stock"] = dom.get("stock", "") or tc.get("data", {}).get("stock", "") or ""

        # Category
        bc = dom.get("breadcrumbs", [])
        if bc:
            fd["category"] = " > ".join(bc[-3:])
        elif tb.get("inline_found", {}).get("categoryPath"):
            fd["category"] = tb["inline_found"]["categoryPath"][:100]

        # Seller
        if tb.get("inline_found", {}).get("sellerName"):
            fd["seller"] = tb["inline_found"]["sellerName"]

        # SKU count
        if tb.get("inline_found", {}).get("skuPathList"):
            try:
                fd["sku_count"] = len(json.loads(tb["inline_found"]["skuPathList"]))
            except Exception:
                fd["sku_count"] = 0

        # Source priority
        if real_prices and dom.get("priceTexts"):
            fd["source"] = "dom"
        elif real_prices and tb.get("prices_regex"):
            fd["source"] = "html_regex"
        elif real_prices and tc.get("data", {}).get("prices"):
            fd["source"] = "mobile"
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
