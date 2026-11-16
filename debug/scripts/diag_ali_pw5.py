"""AliExpress v5: TMD/Baxia recaptcha в iframe, исправленный JS, warm-up + MTOP."""
import sys, json, re, time, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

LID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{LID}.html"
HOME_URL = "https://www.aliexpress.com/"

result = {
    "listing_id": LID, "url": PRODUCT_URL,
    "title": None, "price": None, "currency": None,
    "availability": None, "images": [], "category": None,
    "method": None, "elapsed": 0,
    "tmd_detected": False, "tmd_solved": False,
    "mtop_responses": [], "errors": [],
    "frames_found": [],
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

    # MTOP interceptor
    def on_resp(resp):
        try:
            u = resp.url
            if "mtop" in u.lower():
                body = resp.text()
                mtop_bodies.append({"url": u[:200], "status": resp.status, "body": body})
                print(f"  [MTOP] {resp.status} len={len(body)} :: {u[:120]}")
                print(f"    head: {body[:300]}")
        except Exception:
            pass
    page.on("response", on_resp)

    # ── 1. WARM-UP ──
    print(f"\n[1] WARM-UP: {HOME_URL}")
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(6000)
        print(f"  OK title={page.title()[:60]}")
    except Exception as e:
        print(f"  WARM-UP ERR: {e}")
        result["errors"].append(f"warmup: {e}")

    # ── 2. PRODUCT PAGE ──
    print(f"\n[2] PRODUCT: {PRODUCT_URL}")
    try:
        resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=25000)
        print(f"  HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        print(f"  NAV ERR: {e}")
        result["errors"].append(f"nav: {e}")

    # ── 3. WAIT + DETECT ──
    print("\n[3] WAITING (25s max)...")
    page_state = {"rendered": False, "tmd": False, "baxia_iframe": None}

    for tick in range(25):
        page.wait_for_timeout(1000)
        try:
            st = page.evaluate("""
                () => {
                    const r = {};
                    // H1
                    const h1 = document.querySelector('h1');
                    r.h1 = h1 ? h1.textContent.trim().substring(0, 200) : null;

                    // Price elements (use Array.from to fix slice issue)
                    const priceEls = Array.from(document.querySelectorAll(
                        '[data-pl="price"], [class*="price--current"], [class*="Price--current"], ' +
                        '[class*="pdp-price"], span[class*="price"], div[class*="price"]'
                    ));
                    r.prices = [];
                    for (const el of priceEls.slice(0, 10)) {
                        const t = (el.textContent || '').trim();
                        if (t && t.length < 40 && /\\d/.test(t)) r.prices.push(t);
                    }

                    // Buy button
                    const bb = document.querySelector('[data-pl="buy"], [class*="buy--"], button[class*="Buy"]');
                    r.buyBtn = bb ? (bb.textContent || '').trim().substring(0, 50) : null;

                    // TMD / Baxia detection
                    const tmdEl = document.querySelector('[class*="tmd"], [class*="TMD"], [id*="tmd"], [class*="punish"], [class*="baxia"], [class*="Baxia"]');
                    r.tmd = tmdEl ? {cls: (tmdEl.className || '').substring(0, 100), tag: tmdEl.tagName} : null;

                    // Iframes
                    r.iframes = [];
                    Array.from(document.querySelectorAll('iframe')).forEach(f => {
                        r.iframes.push({
                            src: (f.src || '').substring(0, 200),
                            w: f.offsetWidth, h: f.offsetHeight,
                            visible: f.offsetParent !== null || f.offsetWidth > 0
                        });
                    });

                    // Body text sample (for TMD detection)
                    r.bodyHead = document.body ? document.body.textContent.trim().substring(0, 300) : '';
                    r.bodyLen = document.body ? document.body.innerHTML.length : 0;

                    // Images
                    r.imgCount = Array.from(document.querySelectorAll('img[src*="alicdn"]')).length;

                    return r;
                }
            """)
        except Exception as e:
            print(f"  tick {tick}: eval err: {e}")
            continue

        # Log state
        if tick % 5 == 0 or st.get("tmd") or st.get("prices"):
            print(f"  tick {tick}: h1={str(st.get('h1',''))[:50]} prices={st.get('prices',[])} tmd={st.get('tmd')} iframes={len(st.get('iframes',[]))}")

        # Detect TMD
        if st.get("tmd"):
            page_state["tmd"] = True
            result["tmd_detected"] = True
            print(f"  TMD DETECTED: {st['tmd']}")
            break

        # Check for recaptcha iframe
        for fr in st.get("iframes", []):
            if "recaptcha" in fr["src"].lower() or "captcha" in fr["src"].lower() or "tmd" in fr["src"].lower() or "punish" in fr["src"].lower():
                page_state["baxia_iframe"] = fr
                page_state["tmd"] = True
                result["tmd_detected"] = True
                print(f"  CAPTCHA IFRAME: {fr}")
                break

        # Rendered?
        if st.get("prices") and len(st["prices"]) > 0:
            page_state["rendered"] = True
            print(f"  RENDERED! prices={st['prices']}")
            break
        if st.get("h1") and st.get("buyBtn"):
            page_state["rendered"] = True
            print(f"  RENDERED (h1+buy)!")
            break

    # ── 4. SOLVE TMD/CAPTCHA ──
    if page_state["tmd"] and not page_state["rendered"]:
        print("\n[4] SOLVING TMD/CAPTCHA...")
        solved = False

        # 4a: Find all frames (including iframes)
        all_frames = page.frames
        print(f"  Total frames: {len(all_frames)}")
        for i, f in enumerate(all_frames):
            try:
                fu = f.url[:150] if f.url else "(no url)"
                print(f"    [{i}] {fu}")
                result["frames_found"].append(fu)
            except Exception:
                pass

        # 4b: Try to find and interact with recaptcha in iframe
        for frame in all_frames:
            if frame == page.main_frame:
                continue
            try:
                fu = frame.url or ""
                if not any(k in fu.lower() for k in ("captcha", "tmd", "punish", "nc_", "baxia", "verify")):
                    continue
                print(f"  Interacting with frame: {fu[:100]}")

                # Wait for iframe content
                frame.wait_for_load_state("domcontentloaded", timeout=8000)
                page.wait_for_timeout(2000)

                # Look for slider / button / checkbox in frame
                interacted = False

                # Try: slider button
                slider_sel = frame.query_selector(
                    '[class*="slider"], [class*="slide-btn"], [class*="drag"], '
                    '.nc_slider, .nc_iconfont, [class*="puzzle"], [class*="btn"]'
                )
                if slider_sel:
                    box = slider_sel.bounding_box()
                    if box and box["width"] > 10:
                        print(f"  Slider in iframe: {box}")
                        sx = box["x"] + box["width"] / 2
                        sy = box["y"] + box["height"] / 2
                        page.mouse.move(sx, sy)
                        page.wait_for_timeout(400)
                        page.mouse.down()
                        for i in range(1, 35):
                            x = sx + (260 * i / 34)
                            y = sy + (i % 3) * 1.5
                            page.mouse.move(x, y)
                            page.wait_for_timeout(15)
                        page.mouse.up()
                        print("  Slider dragged in iframe")
                        page.wait_for_timeout(5000)
                        interacted = True

                # Try: "click to verify" / checkbox
                if not interacted:
                    btn = frame.query_selector('button, [role="button"], input[type="checkbox"], [class*="verify"], [class*="confirm"]')
                    if btn:
                        box = btn.bounding_box()
                        if box:
                            page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                            print(f"  Clicked in iframe: {btn.evaluate('el => el.tagName + \'.\' + (el.className||\'\').substring(0,40)')[:60]}")
                            page.wait_for_timeout(5000)
                            interacted = True

                # Try: any visible clickable element
                if not interacted:
                    els = frame.query_selector_all('div, span, button, img, [class*="btn"]')
                    for el in els[:10]:
                        try:
                            b = el.bounding_box()
                            if b and b["width"] > 30 and b["height"] > 20:
                                page.mouse.click(b["x"] + b["width"]/2, b["y"] + b["height"]/2)
                                print(f"  Clicked element in iframe at ({b['x']:.0f},{b['y']:.0f})")
                                page.wait_for_timeout(4000)
                                interacted = True
                                break
                        except Exception:
                            continue

                if interacted:
                    solved = True
                    break
            except Exception as e:
                print(f"  Frame interact err: {e}")
                continue

        # 4c: Main page TMD interaction (if not in iframe)
        if not solved:
            print("  Trying main page TMD...")
            try:
                # Look for TMD-specific elements
                tmd_btn = page.query_selector(
                    '[class*="tmd"] button, [class*="tmd"] [class*="btn"], '
                    '[class*="punish"] button, .nc-container .nc_slider, '
                    '[class*="verify"] button, [id*="tmd"] button'
                )
                if tmd_btn:
                    box = tmd_btn.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                        print(f"  Clicked TMD btn on main page")
                        page.wait_for_timeout(5000)
                        solved = True
            except Exception as e:
                print(f"  TMD main err: {e}")

        # 4d: After solving, wait for re-render
        if solved:
            print("  Waiting for re-render after captcha...")
            for i in range(15):
                page.wait_for_timeout(1000)
                try:
                    check = page.evaluate("""
                        () => {
                            const p = Array.from(document.querySelectorAll(
                                '[data-pl="price"], [class*="price--current"], span[class*="price"]'
                            ));
                            for (const el of p) {
                                const t = (el.textContent||'').trim();
                                if (t && /\\d/.test(t) && t.length < 30) return t;
                            }
                            const h1 = document.querySelector('h1');
                            return h1 ? 'H1:' + h1.textContent.trim().substring(0,50) : null;
                        }
                    """)
                    if check:
                        print(f"  RE-RENDERED: {check}")
                        page_state["rendered"] = True
                        break
                except Exception:
                    continue

        result["tmd_solved"] = solved
        print(f"  TMD solved: {solved}")

    # ── 5. EXTRACT FROM DOM ──
    print("\n[5] EXTRACT...")
    try:
        dom = page.evaluate("""
            () => {
                const r = {};

                // Title
                const h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                if (!r.title) {
                    const t = document.querySelector('title');
                    if (t) {
                        r.title = t.textContent.replace(/\\s*\\|.*$/,'').trim();
                    }
                }

                // Prices
                r.prices = [];
                const pEls = Array.from(document.querySelectorAll(
                    '[data-pl="price"], [class*="price--current"], [class*="Price--current"], ' +
                    '[class*="pdp-price"], span[class*="price"], div[class*="price"]'
                ));
                for (const el of pEls.slice(0, 12)) {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 40 && /\\d/.test(t)) r.prices.push(t);
                }
                r.prices = [...new Set(r.prices)].slice(0, 8);

                // Availability
                r.availability = null;
                const aEls = Array.from(document.querySelectorAll('[class*="stock"], [class*="availability"], [data-pl="stock"]'));
                for (const el of aEls) {
                    const t = (el.textContent||'').trim().toLowerCase();
                    if (t.includes('in stock') || t.includes('available')) { r.availability='IN_STOCK'; break; }
                    if (t.includes('out of stock') || t.includes('unavailable')) { r.availability='OUT_OF_STOCK'; break; }
                    if (t.includes('pre-order') || t.includes('preorder')) { r.availability='PRE_ORDER'; break; }
                }
                const bb = document.querySelector('[data-pl="buy"], [class*="buy--"]');
                r.buyBtn = bb ? (bb.textContent||'').trim().substring(0,60) : null;

                // Images
                r.images = [];
                const imgs = Array.from(document.querySelectorAll('img'));
                for (const img of imgs) {
                    const src = img.src || img.getAttribute('data-src') || '';
                    if (src.includes('alicdn') && !src.includes('placeholder') && !src.includes('logo')
                        && !src.includes('icon') && !src.includes('blank')) {
                        if (src.includes('/kf/') || src.includes('s.alicdn') || src.includes('ae0')) {
                            r.images.push(src);
                        }
                    }
                }
                r.images = [...new Set(r.images)].slice(0, 15);

                // Breadcrumb
                const bc = document.querySelector('[class*="breadcrumb"], [class*="Breadcrumb"]');
                r.category = bc ? bc.textContent.trim().substring(0, 200) : null;

                // Body length for debug
                r.bodyLen = document.body ? document.body.innerHTML.length : 0;

                return r;
            }
        """)
        print(f"  title={str(dom.get('title',''))[:80]}")
        print(f"  prices={dom.get('prices',[])}")
        print(f"  avail={dom.get('availability')} buyBtn={dom.get('buyBtn')}")
        print(f"  images={len(dom.get('images',[]))} bodyLen={dom.get('bodyLen')}")

        result["title"] = dom.get("title")
        result["price"] = dom.get("prices", [None])[0] if dom.get("prices") else None
        result["availability"] = dom.get("availability")
        result["images"] = dom.get("images", [])
        result["category"] = dom.get("category")
        if dom.get("prices"):
            result["method"] = "DOM"

    except Exception as e:
        print(f"  DOM extract err: {e}")
        result["errors"].append(f"dom: {e}")

    # ── 6. JS STATE FALLBACK ──
    if not result["price"]:
        print("\n[6] JS STATE...")
        try:
            js = page.evaluate("""
                () => {
                    const r = {};
                    if (window.__INIT_DATA_STATE__) r.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0, 5000);
                    if (window.runParams) r.runParams = JSON.stringify(window.runParams).substring(0, 3000);
                    if (window.__NEXT_DATA__) r.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0, 5000);
                    if (window._dida) r.dida = JSON.stringify(window._dida).substring(0, 3000);
                    return r;
                }
            """)
            for k, v in js.items():
                print(f"  {k}: {str(v)[:200]}")
                pm = re.search(r'"(?:minPrice|salePrice|price|amount|formattedPrice|actvPrice|displayPrice)"\s*:\s*"?([\d.]+)"?', v or "")
                if pm:
                    result["price"] = pm.group(1)
                    result["currency"] = "USD"
                    result["method"] = f"JS_{k}"
                    print(f"  PRICE from {k}: {result['price']}")
                    break
        except Exception as e:
            print(f"  JS err: {e}")
            result["errors"].append(f"js: {e}")

    # ── 7. MTOP FALLBACK ──
    if not result["price"] and mtop_bodies:
        print(f"\n[7] MTOP FALLBACK ({len(mtop_bodies)} resp)...")
        for mr in mtop_bodies:
            body = mr["body"]
            # Strip jsonp
            m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
            if m:
                body = m.group(1)
            try:
                d = json.loads(body)
                ret = d.get("ret", [])
                data = d.get("data", {})
                data_str = json.dumps(data, ensure_ascii=False) if data else body
                print(f"  MTOP ret={ret[:3]} dataLen={len(data_str)}")
                if any("SUCCESS" in str(r) for r in ret):
                    pm = re.search(r'"(?:minPrice|salePrice|price|amount|actvPrice|displayPrice)"\s*:\s*"?([\d.]+)"?', data_str)
                    if pm:
                        result["price"] = pm.group(1)
                        result["currency"] = "USD"
                        result["method"] = "MTOP"
                        print(f"  MTOP PRICE: {result['price']}")
                        # Also extract title
                        tm = re.search(r'"(?:title|subject|name)"\s*:\s*"([^"]{10,300})"', data_str)
                        if tm and not result["title"]:
                            result["title"] = tm.group(1)
                        # Images
                        im = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(//[^"]*alicdn[^"]*)"', data_str)
                        im2 = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(https?://[^"]*alicdn[^"]*)"', data_str)
                        if im and not result["images"]:
                            result["images"] = [f"https:{u}" for u in im[:15]]
                        elif im2 and not result["images"]:
                            result["images"] = im2[:15]
                        break
            except Exception:
                continue

    # ── Screenshot ──
    try:
        page.screenshot(path="diag_ali_pw5.png", full_page=False)
        print(f"\nScreenshot: diag_ali_pw5.png")
    except Exception:
        pass

    browser.close()

result["elapsed"] = time.monotonic() - t_start
result["mtop_responses"] = [
    {"url": m["url"][:150], "status": m["status"], "len": len(m["body"]), "head": m["body"][:300]}
    for m in mtop_bodies[:6]
]

print(f"\n{'='*60}")
print(f"RESULT:")
print(f"  Title:      {result['title']}")
print(f"  Price:      {result['price']} {result.get('currency','')}")
print(f"  Avail:      {result['availability']}")
print(f"  Images:     {len(result['images'])}")
for img in result["images"][:5]:
    print(f"    {img[:120]}")
print(f"  Category:   {result['category']}")
print(f"  Method:     {result['method']}")
print(f"  TMD:        detected={result['tmd_detected']} solved={result['tmd_solved']}")
print(f"  Frames:     {result['frames_found']}")
print(f"  Elapsed:    {result['elapsed']:.1f}s")
print(f"  Errors:     {result['errors']}")
print(f"{'='*60}")

with open("diag_ali_pw5_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Saved: diag_ali_pw5_result.json")
