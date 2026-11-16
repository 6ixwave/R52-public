"""AliExpress: Playwright с warm-up, ожиданием рендера, извлечением из DOM.

Стратегия:
1. Открыть aliexpress.com (warm-up, cookies, Baxia-токены)
2. Перейти на товар
3. Ждать появления price-элемента (до 20с)
4. Если Baxia — попытка решить (slider / click)
5. Извлечь: title, price, images, availability из DOM
6. Fallback: window.__INIT_DATA_STATE__ / window.runParams
"""
import sys, json, re, time, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

LID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{LID}.html"
HOME_URL = "https://www.aliexpress.com/"

result = {
    "listing_id": LID,
    "url": PRODUCT_URL,
    "title": None,
    "price": None,
    "currency": None,
    "availability": None,
    "images": [],
    "category": None,
    "method": None,
    "elapsed": 0,
    "baxia_detected": False,
    "baxia_solved": False,
    "mtop_responses": [],
    "errors": [],
}

t_start = time.monotonic()
mtop_bodies = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-automation",
        ],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-US",
        viewport={"width": 1920, "height": 1080},
        timezone_id="America/Los_Angeles",
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = {runtime: {}};
    """)
    page = ctx.new_page()

    # ── MTOP interceptor ──
    def on_response(resp):
        try:
            u = resp.url
            if "mtop" in u.lower() and ("pdp" in u.lower() or "product" in u.lower() or "detail" in u.lower()):
                body = resp.text()
                mtop_bodies.append({"url": u[:150], "status": resp.status, "body_len": len(body), "body_head": body[:500]})
                print(f"  [MTOP] {resp.status} len={len(body)} url={u[:100]}")
        except Exception:
            pass
    page.on("response", on_response)

    # ── STEP 1: Warm-up on main page ──
    print(f"\n[1] WARM-UP: {HOME_URL}")
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)
        print(f"  OK, title={page.title()[:60]}")
    except Exception as e:
        print(f"  WARM-UP ERROR: {e}")
        result["errors"].append(f"warmup: {e}")

    # ── STEP 2: Navigate to product ──
    print(f"\n[2] PRODUCT: {PRODUCT_URL}")
    try:
        resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=25000)
        print(f"  HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        print(f"  NAV ERROR: {e}")
        result["errors"].append(f"nav: {e}")

    # ── STEP 3: Wait for render (price or Baxia) ──
    print("\n[3] WAITING FOR RENDER (up to 20s)...")
    rendered = False
    baxia_found = False

    for attempt in range(20):
        page.wait_for_timeout(1000)
        try:
            state = page.evaluate("""
                () => {
                    const r = {};
                    // Price
                    const priceEls = document.querySelectorAll(
                        '[class*="price"] [class*="current"], ' +
                        '[class*="Price--current"], ' +
                        '[data-pl="price"], ' +
                        '.pdp-price--price, ' +
                        '[class*="price--current"], ' +
                        'span[class*="price"]'
                    );
                    r.prices = [];
                    for (const el of priceEls.slice(0, 8)) {
                        const t = (el.textContent || '').trim();
                        if (t && t.length < 40) r.prices.push(t);
                    }
                    // Title
                    const h1 = document.querySelector('h1');
                    r.h1 = h1 ? h1.textContent.trim().substring(0, 200) : null;
                    // Baxia / captcha
                    const baxia = document.querySelector(
                        '[class*="baxia"], [class*="Baxia"], .nc-container, ' +
                        '#nc_1_wrapper, [class*="punish"], [class*="verify-dialog"], ' +
                        '.container--baxia, [id*="baxia"]'
                    );
                    r.baxia = baxia ? {cls: (baxia.className||'').substring(0,80), visible: baxia.offsetParent !== null} : null;
                    // Iframes (Baxia often in iframe)
                    r.iframes = [];
                    document.querySelectorAll('iframe').forEach(f => {
                        r.iframes.push({src: (f.src||'').substring(0,120), w: f.offsetWidth, h: f.offsetHeight});
                    });
                    // Buy button (indicates rendered product)
                    const buyBtn = document.querySelector('[data-pl="buy"], [class*="buy--"], button[class*="Buy"]');
                    r.buyBtn = buyBtn ? (buyBtn.textContent||'').trim().substring(0,50) : null;
                    // Images
                    r.imgCount = document.querySelectorAll('img[src*="alicdn"]').length;
                    return r;
                }
            """)
        except Exception as e:
            print(f"  evaluate error (attempt {attempt}): {e}")
            continue

        if state.get("baxia"):
            baxia_found = True
            result["baxia_detected"] = True
            print(f"  BAXIA detected: {state['baxia']}")
            print(f"  Iframes: {state.get('iframes', [])}")
            break

        if state.get("prices") and len(state["prices"]) > 0:
            rendered = True
            print(f"  RENDERED! prices={state['prices']}")
            print(f"  h1={state.get('h1', '')[:80]}")
            print(f"  buyBtn={state.get('buyBtn')}")
            break

        if state.get("h1") and state.get("buyBtn"):
            rendered = True
            print(f"  RENDERED (h1+buy)! h1={state['h1'][:80]}")
            break

    if not rendered and not baxia_found:
        print("  Timeout: no price/baxia after 20s")
        # Last check
        try:
            final_state = page.evaluate("""
                () => {
                    const r = {};
                    r.h1 = document.querySelector('h1') ? document.querySelector('h1').textContent.trim().substring(0,200) : null;
                    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                    r.bodyText = document.body ? document.body.textContent.trim().substring(0,500) : '';
                    return r;
                }
            """)
            print(f"  Final: h1={final_state.get('h1')}, bodyLen={final_state.get('bodyLen')}")
            print(f"  bodyText: {final_state.get('bodyText', '')[:200]}")
        except Exception as e:
            result["errors"].append(f"final_eval: {e}")

    # ── STEP 4: Solve Baxia if needed ──
    if baxia_found:
        print("\n[4] SOLVING BAXIA...")
        # Try: look for slider in iframe or main page
        solved = False

        # 4a: Main page slider
        try:
            slider = page.evaluate("""
                () => {
                    const sels = [
                        '.nc-container .nc_slider', '.nc_iconfont',
                        '[class*="slider-btn"]', '[class*="slide-btn"]',
                        '[class*="drag-btn"]', '[class*="puzzle-btn"]',
                        '.baxia-slider-btn', '[class*="baxia"] button',
                    ];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) {
                            const r = el.getBoundingClientRect();
                            return {sel: sel, x: r.x, y: r.y, w: r.width, h: r.height};
                        }
                    }
                    return null;
                }
            """)
            if slider:
                print(f"  Slider: {slider}")
                sx = slider["x"] + slider["w"] / 2
                sy = slider["y"] + slider["h"] / 2
                page.mouse.move(sx, sy)
                page.wait_for_timeout(300)
                page.mouse.down()
                for i in range(1, 40):
                    x = sx + (280 * i / 39)
                    y = sy + (i % 3) * 1.5
                    page.mouse.move(x, y)
                    page.wait_for_timeout(12)
                page.mouse.up()
                print("  Slider dragged")
                page.wait_for_timeout(4000)
                solved = True
        except Exception as e:
            print(f"  Slider error: {e}")

        # 4b: Iframe slider
        if not solved:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    btn = frame.query_selector('[class*="slider"], [class*="btn"], [class*="drag"], button')
                    if btn:
                        box = btn.bounding_box()
                        if box and box["w"] > 10:
                            page.mouse.move(box["x"] + box["w"]/2, box["y"] + box["h"]/2)
                            page.wait_for_timeout(200)
                            page.mouse.down()
                            for i in range(1, 30):
                                x = box["x"] + box["w"]/2 + (250 * i / 29)
                                page.mouse.move(x, box["y"] + box["h"]/2)
                                page.wait_for_timeout(15)
                            page.mouse.up()
                            print(f"  Iframe slider dragged: {frame.url[:60]}")
                            page.wait_for_timeout(4000)
                            solved = True
                            break
                except Exception:
                    continue

        # 4c: Click "verify" / "confirm" button
        if not solved:
            try:
                btns = page.evaluate("""
                    () => {
                        const els = document.querySelectorAll('button, [role="button"], [class*="btn"], a[class*="confirm"]');
                        const r = [];
                        for (const el of els) {
                            if (el.offsetParent !== null && el.offsetWidth > 20) {
                                const t = (el.textContent || '').trim();
                                if (t.length > 2 && t.length < 60) {
                                    const b = el.getBoundingClientRect();
                                    r.push({text: t, x: b.x, y: b.y, w: b.width, h: b.height});
                                }
                            }
                        }
                        return r;
                    }
                """)
                for b in btns:
                    if any(k in b["text"].lower() for k in ("verify", "confirm", "ok", "proceed", "start", "drag", "slide")):
                        page.mouse.click(b["x"] + b["w"]/2, b["y"] + b["h"]/2)
                        print(f"  Clicked: '{b['text']}'")
                        page.wait_for_timeout(4000)
                        solved = True
                        break
            except Exception as e:
                print(f"  Button error: {e}")

        result["baxia_solved"] = solved
        print(f"  Baxia solved: {solved}")

        # Wait for re-render after captcha
        if solved:
            for i in range(10):
                page.wait_for_timeout(1000)
                try:
                    check = page.evaluate("""
                        () => {
                            const p = document.querySelectorAll('[class*="price"]');
                            for (const el of p) {
                                const t = (el.textContent||'').trim();
                                if (t && /\\d/.test(t) && t.length < 30) return t;
                            }
                            return null;
                        }
                    """)
                    if check:
                        print(f"  Post-captcha price: {check}")
                        rendered = True
                        break
                except Exception:
                    continue

    # ── STEP 5: Extract data from DOM ──
    print("\n[5] EXTRACTING DATA FROM DOM...")
    try:
        dom_data = page.evaluate("""
            () => {
                const r = {};

                // Title
                const h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                if (!r.title) {
                    const t = document.querySelector('title');
                    r.title = t ? t.textContent.replace(/\\s*\\|.*$/, '').trim() : null;
                }

                // Price — multiple strategies
                r.prices = [];
                // Strategy A: data-pl="price"
                document.querySelectorAll('[data-pl="price"]').forEach(el => {
                    const t = (el.textContent||'').trim();
                    if (t) r.prices.push(t);
                });
                // Strategy B: class patterns
                document.querySelectorAll('[class*="price--current"], [class*="Price--current"], [class*="pdp-price"]').forEach(el => {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 40) r.prices.push(t);
                });
                // Strategy C: span with $ or number
                document.querySelectorAll('span[class*="price"], div[class*="price"]').forEach(el => {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 30 && /[$€£\\d]/.test(t)) r.prices.push(t);
                });
                // Deduplicate
                r.prices = [...new Set(r.prices)].slice(0, 10);

                // Availability
                r.availability = null;
                const availEls = document.querySelectorAll('[class*="stock"], [class*="availability"], [data-pl="stock"]');
                for (const el of availEls) {
                    const t = (el.textContent||'').trim().toLowerCase();
                    if (t.includes('in stock') || t.includes('available')) { r.availability = 'IN_STOCK'; break; }
                    if (t.includes('out of stock') || t.includes('unavailable')) { r.availability = 'OUT_OF_STOCK'; break; }
                    if (t.includes('pre-order') || t.includes('preorder')) { r.availability = 'PRE_ORDER'; break; }
                }
                // Buy button text as hint
                const buyBtn = document.querySelector('[data-pl="buy"], [class*="buy--"]');
                if (buyBtn) r.buyBtnText = (buyBtn.textContent||'').trim();

                // Images
                r.images = [];
                document.querySelectorAll('img').forEach(img => {
                    const src = img.src || img.getAttribute('data-src') || '';
                    if (src.includes('alicdn') && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')) {
                        // Filter: product images usually have /kf/ or specific pattern
                        if (src.includes('/kf/') || src.includes('s.alicdn') || src.includes('ae0')) {
                            r.images.push(src);
                        }
                    }
                });
                r.images = [...new Set(r.images)].slice(0, 15);

                // Category / breadcrumb
                const bc = document.querySelector('[class*="breadcrumb"], [class*="Breadcrumb"]');
                r.category = bc ? bc.textContent.trim().substring(0, 200) : null;

                return r;
            }
        """)
        print(f"  DOM: title={dom_data.get('title', '')[:80]}")
        print(f"  DOM: prices={dom_data.get('prices', [])}")
        print(f"  DOM: avail={dom_data.get('availability')}, buyBtn={dom_data.get('buyBtnText')}")
        print(f"  DOM: images={len(dom_data.get('images', []))}")
        print(f"  DOM: category={dom_data.get('category', '')[:80]}")

        # Fill result
        result["title"] = dom_data.get("title")
        result["price"] = dom_data.get("prices", [None])[0] if dom_data.get("prices") else None
        result["availability"] = dom_data.get("availability")
        result["images"] = dom_data.get("images", [])
        result["category"] = dom_data.get("category")
        result["method"] = "DOM"

    except Exception as e:
        print(f"  DOM extract error: {e}")
        result["errors"].append(f"dom_extract: {e}")

    # ── STEP 6: Fallback — JS state from page context ──
    if not result["price"]:
        print("\n[6] FALLBACK: JS STATE...")
        try:
            js_state = page.evaluate("""
                () => {
                    const r = {};
                    // __INIT_DATA_STATE__
                    if (window.__INIT_DATA_STATE__) {
                        r.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0, 3000);
                    }
                    // runParams
                    if (window.runParams) {
                        r.runParams = JSON.stringify(window.runParams).substring(0, 2000);
                    }
                    // __NEXT_DATA__
                    if (window.__NEXT_DATA__) {
                        r.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0, 3000);
                    }
                    // window._dida / window._pageData
                    if (window._dida) r.dida = JSON.stringify(window._dida).substring(0, 2000);
                    if (window._pageData) r.pageData = JSON.stringify(window._pageData).substring(0, 2000);
                    return r;
                }
            """)
            print(f"  JS keys: {list(js_state.keys())}")
            for k, v in js_state.items():
                print(f"  {k}: {str(v)[:200]}")

            # Try to extract price from state
            for k, v in js_state.items():
                if v:
                    m = re.search(r'"(?:minPrice|salePrice|price|amount|formattedPrice|displayPrice|actvPrice)"\s*:\s*"?([\d.]+)"?', v)
                    if m:
                        result["price"] = m.group(1)
                        result["currency"] = "USD"
                        result["method"] = f"JS_STATE_{k}"
                        print(f"  PRICE from {k}: {result['price']}")
                        break
        except Exception as e:
            print(f"  JS state error: {e}")
            result["errors"].append(f"js_state: {e}")

    # ── STEP 7: MTOP fallback ──
    if not result["price"] and mtop_bodies:
        print(f"\n[7] MTOP FALLBACK ({len(mtop_bodies)} responses)...")
        for mr in mtop_bodies:
            body = mr["body_head"]
            # Strip jsonp wrapper
            m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
            if m:
                body = m.group(1)
            try:
                d = json.loads(body)
                ret = d.get("ret", [])
                if any("SUCCESS" in r for r in ret):
                    data_str = json.dumps(d.get("data", {}), ensure_ascii=False)
                    pm = re.search(r'"(?:minPrice|salePrice|price|amount|actvPrice)"\s*:\s*"?([\d.]+)"?', data_str)
                    if pm:
                        result["price"] = pm.group(1)
                        result["currency"] = "USD"
                        result["method"] = "MTOP_API"
                        print(f"  MTOP PRICE: {result['price']}")
                        break
            except Exception:
                continue

    # ── Screenshot ──
    try:
        page.screenshot(path="diag_ali_pw4.png", full_page=False)
        print(f"\nScreenshot: diag_ali_pw4.png")
    except Exception:
        pass

    browser.close()

result["elapsed"] = time.monotonic() - t_start
result["mtop_responses"] = mtop_bodies[:5]

# ── Final report ──
print(f"\n{'='*60}")
print(f"RESULT:")
print(f"  Title:      {result['title']}")
print(f"  Price:      {result['price']} {result.get('currency', '')}")
print(f"  Avail:      {result['availability']}")
print(f"  Images:     {len(result['images'])}")
print(f"  Method:     {result['method']}")
print(f"  Baxia:      detected={result['baxia_detected']}, solved={result['baxia_solved']}")
print(f"  Elapsed:    {result['elapsed']:.1f}s")
print(f"  Errors:     {result['errors']}")
print(f"{'='*60}")

# Save
with open("diag_ali_pw4_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Saved: diag_ali_pw4_result.json")
