"""AliExpress Playwright v2: warm-up + MTOP intercept + page-context retry."""
import sys
import json
import logging
import re
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

LID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{LID}.html"
HOME_URL = "https://www.aliexpress.com/"

mtop_responses = []  # collected MTOP responses

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
        extra_http_headers={
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        },
    )
    # Remove navigator.webdriver
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['it-IT', 'it', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    """)
    page = ctx.new_page()

    # ─── Network interceptor ───
    def on_response(resp):
        try:
            url = resp.url
            if "mtop.aliexpress.pdp" in url or "mtop.aliexpress.product" in url:
                body = resp.text()
                mtop_responses.append({"url": url, "status": resp.status, "body": body})
                print(f"  [MTOP] {resp.status} len={len(body)} {url[:100]}")
                print(f"         body[:300]: {body[:300]}")
        except Exception as e:
            pass

    page.on("response", on_response)

    # ─── Phase 1: Warm-up on homepage ───
    print(f"\n{'='*60}")
    print(f"PHASE 1: Warm-up {HOME_URL}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)
    print(f"Homepage loaded: {time.monotonic()-t0:.1f}s")

    # Check cookies
    cookies = ctx.cookies()
    cookie_names = [c["name"] for c in cookies]
    print(f"Cookies ({len(cookie_names)}): {cookie_names[:15]}")
    has_xman = any("xman" in n for n in cookie_names)
    has_mh5 = any("_m_h5_tk" in n for n in cookie_names)
    print(f"  xman: {has_xman}, _m_h5_tk: {has_mh5}")

    # ─── Phase 2: Navigate to product page ───
    print(f"\n{'='*60}")
    print(f"PHASE 2: Product page {PRODUCT_URL}")
    print(f"{'='*60}")
    t1 = time.monotonic()
    page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
    print(f"DOMContentLoaded: {time.monotonic()-t1:.1f}s")

    # Wait for network idle (MTOP calls happen here)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
        print(f"networkidle: {time.monotonic()-t1:.1f}s")
    except Exception:
        print(f"networkidle timeout at {time.monotonic()-t1:.1f}s")

    # Extra wait for SPA rendering
    page.wait_for_timeout(5000)
    print(f"Total product load: {time.monotonic()-t1:.1f}s")

    # ─── Phase 3: Check what we got ───
    print(f"\n{'='*60}")
    print(f"PHASE 3: Analyze MTOP responses ({len(mtop_responses)} total)")
    print(f"{'='*60}")

    product_data = None
    for i, mr in enumerate(mtop_responses):
        body = mr["body"]
        # Strip jsonp wrapper
        clean = body
        m = re.search(r'mtopjsonp\d*\((.*)\)', clean, re.DOTALL)
        if m:
            clean = m.group(1)
        try:
            data = json.loads(clean)
            ret = data.get("ret", [])
            print(f"\n  [{i}] ret: {ret}")
            if any("SUCCESS" in r for r in ret):
                product_data = data.get("data", {})
                print(f"      *** SUCCESS - product data found! ***")
                break
            elif any("TOKEN_EMPTY" in r for r in ret):
                print(f"      Token empty (expected on first call)")
            elif any("USER_VALIDATE" in r or "RGV587" in r for r in ret):
                print(f"      Baxia USER_VALIDATE")
        except (json.JSONDecodeError, ValueError):
            print(f"  [{i}] parse error: {body[:100]}")

    # ─── Phase 4: If no data, try page-context MTOP call ───
    if not product_data:
        print(f"\n{'='*60}")
        print(f"PHASE 4: Page-context MTOP retry")
        print(f"{'='*60}")

        # Check if page has lib.mtop or window.__mtop
        has_mtop_lib = page.evaluate("typeof window.lib !== 'undefined' && typeof window.lib.mtop !== 'undefined'")
        print(f"  window.lib.mtop: {has_mtop_lib}")

        if has_mtop_lib:
            # Call MTOP from page context (has all cookies + Baxia tokens)
            result = page.evaluate("""
            (productId) => {
                return new Promise((resolve) => {
                    window.lib.mtop.request({
                        api: 'mtop.aliexpress.pdp.pc.query',
                        v: '1.0',
                        data: {
                            productId: productId,
                            extParams: '{}',
                            _currency: 'EUR',
                            _lang: 'it_IT',
                            site: 'ita'
                        },
                        type: 'GET',
                        dataType: 'json'
                    }, (res) => {
                        resolve({success: true, data: res});
                    }, (err) => {
                        resolve({success: false, error: err});
                    });
                    // Timeout fallback
                    setTimeout(() => resolve({success: false, error: 'timeout'}), 15000);
                });
            }
            """, LID)
            print(f"  MTOP result: {json.dumps(result, ensure_ascii=False)[:500]}")
            if result and result.get("success"):
                data = result.get("data", {})
                ret = data.get("ret", [])
                if any("SUCCESS" in r for r in ret):
                    product_data = data.get("data", {})

        # If still no data, try direct fetch from page
        if not product_data:
            print(f"  Trying direct fetch from page context...")
            result2 = page.evaluate("""
            async (productId) => {
                try {
                    const t = Date.now();
                    const data = JSON.stringify({
                        productId: productId,
                        extParams: '{}',
                        _currency: 'EUR',
                        _lang: 'it_IT',
                        site: 'ita'
                    });
                    // Get token from cookie
                    const cookies = document.cookie.split(';').map(c => c.trim());
                    const tkCookie = cookies.find(c => c.startsWith('_m_h5_tk='));
                    const token = tkCookie ? tkCookie.split('=')[1].split('_')[0] : '';
                    
                    const signStr = `${token}&${t}&12574478&${data}`;
                    // Simple MD5 (AliExpress uses this)
                    const resp = await fetch(`https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/?jsv=2.5.1&appKey=12574478&t=${t}&sign=[REDACTED_AUTH_SECRET]&api=mtop.aliexpress.pdp.pc.query&v=1.0&type=originaljson&dataType=json&data=${encodeURIComponent(data)}`, {
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                    const text = await resp.text();
                    return {status: resp.status, body: text.substring(0, 2000), token: token.substring(0, 30)};
                } catch(e) {
                    return {error: e.message};
                }
            }
            """, LID)
            print(f"  Fetch result: {json.dumps(result2, ensure_ascii=False)[:600]}")
            if result2 and result2.get("body"):
                try:
                    d = json.loads(result2["body"])
                    if any("SUCCESS" in r for r in d.get("ret", [])):
                        product_data = d.get("data", {})
                except:
                    pass

    # ─── Phase 5: If still nothing, try mobile page ───
    if not product_data:
        print(f"\n{'='*60}")
        print(f"PHASE 5: Try mobile page")
        print(f"{'='*60}")
        mobile_url = f"https://m.aliexpress.com/item/{LID}.html"
        page2 = ctx.new_page()
        t2 = time.monotonic()
        page2.goto(mobile_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page2.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass
        page2.wait_for_timeout(3000)
        print(f"Mobile page: {time.monotonic()-t2:.1f}s")
        
        mobile_html = page2.content()
        print(f"Mobile HTML: {len(mobile_html)} bytes")
        
        # Check for product data in mobile page
        m_h1 = page2.query_selector("h1")
        print(f"Mobile h1: {m_h1.inner_text()[:100] if m_h1 else 'N/A'}")
        
        # Check mobile MTOP
        mobile_mtop = page2.evaluate("""
        () => {
            const el = document.querySelector('[data-role="price"], .price, [class*="price"]');
            return el ? el.textContent.trim() : null;
        }
        """)
        print(f"Mobile price element: {mobile_mtop}")
        
        # Check for __INIT_DATA_STATE__ or similar in mobile
        mobile_state = page2.evaluate("window.__INIT_DATA_STATE__ || window._d_c_ || null")
        if mobile_state:
            s = json.dumps(mobile_state, ensure_ascii=False)
            print(f"Mobile state: {s[:400]}")
        
        page2.close()

    # ─── Phase 6: Final DOM extraction (if product_data found via MTOP) ───
    print(f"\n{'='*60}")
    print(f"PHASE 6: Final data extraction")
    print(f"{'='*60}")

    if product_data:
        print(f"  Product data keys: {list(product_data.keys())[:20]}")
        pdata_str = json.dumps(product_data, ensure_ascii=False)
        
        # Title
        title = None
        for k in ("title", "subject", "name", "productTitle"):
            if k in product_data and isinstance(product_data[k], str):
                title = product_data[k]
                break
        if not title:
            m = re.search(r'"(?:title|subject)"\s*:\s*"([^"]{10,300})"', pdata_str)
            if m:
                title = m.group(1)
        print(f"  Title: {title[:120] if title else 'NOT FOUND'}")
        
        # Price
        prices = re.findall(r'"(?:minPrice|salePrice|price|amount|formattedPrice|displayPrice)"\s*:\s*"?([\d.]+)"?', pdata_str)
        currencies = re.findall(r'"(?:currency|priceCurrency)"\s*:\s*"([^"]+)"', pdata_str)
        print(f"  Prices: {prices[:10]}")
        print(f"  Currencies: {currencies[:5]}")
        
        # Images
        imgs = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(//[^"]*alicdn[^"]*)"', pdata_str)
        imgs2 = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(https?://[^"]*alicdn[^"]*)"', pdata_str)
        print(f"  Images (protocol-rel): {imgs[:5]}")
        print(f"  Images (full): {imgs2[:5]}")
        
        # Availability
        avail = re.findall(r'"(?:stock|inStock|stockStatus|available|buyNow)"\s*:\s*("?[^,}\]]+)"?', pdata_str)
        print(f"  Availability: {avail[:5]}")
        
        # Save full response
        out = "diag_ali_pw2_response.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(product_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved to: {out} ({len(pdata_str)} bytes)")
    else:
        print(f"  No product data obtained from MTOP")
        # Last resort: DOM extraction from rendered page
        html = page.content()
        print(f"  Rendered HTML: {len(html)} bytes")
        
        # Try to find price in rendered DOM
        price_el = page.query_selector("[class*='price-current'], [class*='Price-current'], [data-price]")
        if price_el:
            print(f"  DOM price: {price_el.inner_text()[:80]}")
        
        # Try h1
        h1 = page.query_selector("h1, [class*='title'], [class*='Title']")
        if h1:
            print(f"  DOM title: {h1.inner_text()[:100]}")
        
        # Try images in rendered DOM
        imgs = page.query_selector_all("img[src*='alicdn']")
        product_imgs = []
        for img in imgs:
            src = img.get_attribute("src") or ""
            if "assets" not in src and "s.alicdn" not in src and "img.alicdn" not in src:
                product_imgs.append(src)
        print(f"  DOM product images: {len(product_imgs)}")
        for u in product_imgs[:5]:
            print(f"    {u[:120]}")

    browser.close()

print(f"\nDone.")
