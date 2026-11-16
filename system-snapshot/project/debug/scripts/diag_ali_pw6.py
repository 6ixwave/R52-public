"""AliExpress v6: Google reCAPTCHA Enterprise в TMD bframe.

Ключевое отличие от pw5:
- Целевой клик — в frame bframe (google.com/recaptcha/enterprise/bframe)
- Ожидание ре-рендера до 30с
- Fallback: MTOP PDP после cookies warm-up
"""
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
    "tmd_detected": False, "recaptcha_clicked": False, "recaptcha_solved": False,
    "mtop_pdp_success": False,
    "frames": [], "errors": [],
}

t0 = time.monotonic()
mtop_pdp_bodies = []

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
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Rome",
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = {runtime: {}};
    """)
    page = ctx.new_page()

    # ── MTOP PDP interceptor ──
    def on_resp(resp):
        try:
            u = resp.url
            if "pdp.pc.query" in u and "punish" not in u and "report" not in u:
                body = resp.text()
                mtop_pdp_bodies.append({"url": u[:200], "status": resp.status, "body": body})
                print(f"  [PDP-MTOP] {resp.status} len={len(body)}")
                print(f"    {body[:300]}")
        except Exception:
            pass
    page.on("response", on_resp)

    # ── 1. WARM-UP ──
    print(f"\n[1] WARM-UP {HOME_URL}")
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(7000)
        # Accept cookies if dialog appears
        try:
            cookie_btn = page.query_selector('[class*="cookie"] button, [class*="Cookie"] button, button:has-text("Accetta"), button:has-text("Accept")')
            if cookie_btn:
                cookie_btn.click()
                print("  Cookie accepted")
                page.wait_for_timeout(2000)
        except Exception:
            pass
        print(f"  OK title={page.title()[:60]}")
    except Exception as e:
        print(f"  WARM-UP ERR: {e}")
        result["errors"].append(f"warmup: {e}")

    # ── 2. PRODUCT ──
    print(f"\n[2] PRODUCT {PRODUCT_URL}")
    try:
        resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=25000)
        print(f"  HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        print(f"  NAV ERR: {e}")
        result["errors"].append(f"nav: {e}")

    # ── 3. WAIT FOR TMD / RENDER (15s) ──
    print("\n[3] WAIT (15s)...")
    tmd_frame = None
    rendered = False

    for tick in range(15):
        page.wait_for_timeout(1000)
        try:
            st = page.evaluate("""
                () => {
                    const r = {};
                    const h1 = document.querySelector('h1');
                    r.h1 = h1 ? h1.textContent.trim().substring(0,200) : null;
                    const pEls = Array.from(document.querySelectorAll('[data-pl="price"], [class*="price--current"], span[class*="price"]'));
                    r.prices = [];
                    for (const el of pEls.slice(0,8)) {
                        const t = (el.textContent||'').trim();
                        if (t && t.length < 40 && /\\d/.test(t)) r.prices.push(t);
                    }
                    r.iframes = [];
                    Array.from(document.querySelectorAll('iframe')).forEach(f => {
                        r.iframes.push({src: (f.src||'').substring(0,250), w: f.offsetWidth, h: f.offsetHeight});
                    });
                    return r;
                }
            """)
        except Exception as e:
            print(f"  tick {tick} eval err: {e}")
            continue

        # Check if already rendered (no TMD)
        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 10):
            rendered = True
            print(f"  RENDERED at tick {tick}: h1={st.get('h1','')[:60]} prices={st.get('prices',[])}")
            break

        # Find TMD / reCAPTCHA iframe
        for fr in st.get("iframes", []):
            if "punish" in fr["src"] or "tmd" in fr["src"].lower():
                tmd_frame = fr
                result["tmd_detected"] = True
                print(f"  tick {tick}: TMD IFRAME found: {fr['src'][:100]}")
                break

        if tmd_frame:
            break

    if rendered:
        print("  No TMD needed, page rendered directly!")
    elif tmd_frame:
        print(f"  TMD detected, waiting for reCAPTCHA bframe...")
    else:
        print("  No TMD, no render after 15s")

    # ── 4. SOLVE reCAPTCHA ──
    if tmd_frame and not rendered:
        print("\n[4] SOLVING reCAPTCHA...")

        # Wait a bit for bframe to load
        page.wait_for_timeout(4000)

        # List all frames
        all_frames = page.frames
        print(f"  Frames: {len(all_frames)}")
        bframe = None
        anchor_frame = None
        for i, f in enumerate(all_frames):
            try:
                fu = f.url or ""
                result["frames"].append(fu[:150])
                if "recaptcha/enterprise/bframe" in fu:
                    bframe = f
                    print(f"    [{i}] BFRAME: {fu[:120]}")
                elif "recaptcha/enterprise/anchor" in fu:
                    anchor_frame = f
                    print(f"    [{i}] ANCHOR: {fu[:120]}")
                elif "punish" in fu.lower() or "tmd" in fu.lower():
                    print(f"    [{i}] TMD: {fu[:120]}")
            except Exception:
                pass

        # Try to click reCAPTCHA checkbox in bframe
        if bframe:
            try:
                bframe.wait_for_load_state("domcontentloaded", timeout=8000)
                page.wait_for_timeout(2000)

                # Find the checkbox: usually input[type=checkbox] or a div with role
                checkbox = None
                for sel in [
                    'input[type="checkbox"]',
                    '[aria-label="Recaptcha"]',
                    '[aria-label="recaptcha"]',
                    'span#recaptcha-anchor',
                    '#recaptcha-anchor',
                    '[class*="anchor"]',
                    'div[role="checkbox"]',
                    '.rcb-anchor',
                    'span[role="presentation"]',
                ]:
                    checkbox = bframe.query_selector(sel)
                    if checkbox:
                        print(f"  Found checkbox: {sel}")
                        break

                if checkbox:
                    box = checkbox.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        print(f"  Clicking checkbox at ({cx:.0f}, {cy:.0f}) size={box['width']:.0f}x{box['height']:.0f}")
                        page.mouse.move(cx, cy)
                        page.wait_for_timeout(500)
                        page.mouse.click(cx, cy)
                        result["recaptcha_clicked"] = True
                        print("  Clicked!")
                    else:
                        # Try evaluate click
                        checkbox.click()
                        result["recaptcha_clicked"] = True
                        print("  Clicked via element.click()")
                else:
                    # Fallback: click center of bframe
                    print("  No checkbox found, clicking bframe center...")
                    # Get bframe position from parent
                    try:
                        bframe_box = bframe.evaluate("""
                            () => {
                                // Get the iframe element in parent
                                const iframes = document.querySelectorAll('iframe');
                                for (const f of iframes) {
                                    if (f.src && f.src.includes('recaptcha/enterprise/bframe')) {
                                        const r = f.getBoundingClientRect();
                                        return {x: r.x, y: r.y, w: r.width, h: r.height};
                                    }
                                }
                                return null;
                            }
                        """)
                    except Exception:
                        bframe_box = None

                    if bframe_box:
                        cx = bframe_box["x"] + 30  # checkbox usually on left
                        cy = bframe_box["y"] + bframe_box["h"] / 2
                        page.mouse.move(cx, cy)
                        page.wait_for_timeout(300)
                        page.mouse.click(cx, cy)
                        result["recaptcha_clicked"] = True
                        print(f"  Clicked bframe at ({cx:.0f},{cy:.0f})")
                    else:
                        # Last resort: find iframe element in TMD frame
                        for f in all_frames:
                            if f == page.main_frame:
                                continue
                            try:
                                fu = f.url or ""
                                if "punish" in fu:
                                    iframe_el = f.query_selector('iframe[src*="bframe"]')
                                    if iframe_el:
                                        ib = iframe_el.bounding_box()
                                        if ib:
                                            cx = ib["x"] + 30
                                            cy = ib["y"] + ib["h"] / 2
                                            page.mouse.move(cx, cy)
                                            page.wait_for_timeout(300)
                                            page.mouse.click(cx, cy)
                                            result["recaptcha_clicked"] = True
                                            print(f"  Clicked via TMD frame iframe at ({cx:.0f},{cy:.0f})")
                                            break
                            except Exception:
                                continue
            except Exception as e:
                print(f"  BFRAME interact err: {e}")
                result["errors"].append(f"bframe: {e}")

        # Also try: if no bframe found, look in TMD frame for nested iframe
        if not bframe:
            print("  No bframe in frame list, searching TMD frame...")
            for f in all_frames:
                try:
                    fu = f.url or ""
                    if "punish" in fu and "recaptcha" not in fu:
                        nested = f.query_selector('iframe[src*="bframe"], iframe[src*="recaptcha"]')
                        if nested:
                            nb = nested.bounding_box()
                            if nb:
                                cx = nb["x"] + 30
                                cy = nb["y"] + nb["h"] / 2
                                page.mouse.move(cx, cy)
                                page.wait_for_timeout(300)
                                page.mouse.click(cx, cy)
                                result["recaptcha_clicked"] = True
                                print(f"  Clicked nested iframe at ({cx:.0f},{cy:.0f})")
                                bframe = f
                                break
                except Exception:
                    continue

        # ── 5. WAIT FOR RE-RENDER (30s) ──
        print("\n[5] WAITING FOR RE-RENDER (30s)...")
        for i in range(30):
            page.wait_for_timeout(1000)
            try:
                check = page.evaluate("""
                    () => {
                        const r = {};
                        const h1 = document.querySelector('h1');
                        r.h1 = h1 ? h1.textContent.trim().substring(0,200) : null;
                        const pEls = Array.from(document.querySelectorAll('[data-pl="price"], [class*="price--current"], span[class*="price"]'));
                        r.prices = [];
                        for (const el of pEls.slice(0,8)) {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 40 && /\\d/.test(t)) r.prices.push(t);
                        }
                        // Check if TMD is gone
                        const tmd = document.querySelector('[class*="tmd"], [class*="punish"]');
                        r.tmdGone = !tmd;
                        // Check for new iframes (TMD removed = solved)
                        r.iframeCount = document.querySelectorAll('iframe').length;
                        return r;
                    }
                """)
                if i % 5 == 0:
                    print(f"  {i}s: h1={str(check.get('h1',''))[:50]} prices={check.get('prices',[])} tmdGone={check.get('tmdGone')} iframes={check.get('iframeCount')}")
                if check.get("prices") or (check.get("h1") and len(check.get("h1","")) > 10):
                    rendered = True
                    result["recaptcha_solved"] = True
                    print(f"  RE-RENDERED at {i}s!")
                    break
            except Exception:
                continue

        if not rendered:
            print("  Still not rendered after 30s")

    # ── 6. EXTRACT DATA ──
    print("\n[6] EXTRACT...")
    try:
        dom = page.evaluate("""
            () => {
                const r = {};
                const h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                if (!r.title) {
                    const t = document.querySelector('title');
                    if (t) r.title = t.textContent.replace(/\\s*\\|.*$/,'').trim();
                }
                r.prices = [];
                const pEls = Array.from(document.querySelectorAll('[data-pl="price"], [class*="price--current"], [class*="Price--current"], [class*="pdp-price"], span[class*="price"], div[class*="price"]'));
                for (const el of pEls.slice(0,12)) {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 40 && /\\d/.test(t)) r.prices.push(t);
                }
                r.prices = [...new Set(r.prices)].slice(0,8);
                r.availability = null;
                const aEls = Array.from(document.querySelectorAll('[class*="stock"], [class*="availability"], [data-pl="stock"]'));
                for (const el of aEls) {
                    const t = (el.textContent||'').trim().toLowerCase();
                    if (t.includes('in stock')||t.includes('available')){r.availability='IN_STOCK';break;}
                    if (t.includes('out of stock')||t.includes('unavailable')){r.availability='OUT_OF_STOCK';break;}
                    if (t.includes('pre-order')||t.includes('preorder')){r.availability='PRE_ORDER';break;}
                }
                r.images = [];
                Array.from(document.querySelectorAll('img')).forEach(img => {
                    const src = img.src || img.getAttribute('data-src') || '';
                    if (src.includes('alicdn') && !src.includes('placeholder') && !src.includes('logo')
                        && !src.includes('icon') && !src.includes('blank') && (src.includes('/kf/')||src.includes('s.alicdn')||src.includes('ae0'))) {
                        r.images.push(src);
                    }
                });
                r.images = [...new Set(r.images)].slice(0,15);
                const bc = document.querySelector('[class*="breadcrumb"], [class*="Breadcrumb"]');
                r.category = bc ? bc.textContent.trim().substring(0,200) : null;
                r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                return r;
            }
        """)
        print(f"  title={str(dom.get('title',''))[:80]}")
        print(f"  prices={dom.get('prices',[])}")
        print(f"  avail={dom.get('availability')} images={len(dom.get('images',[]))} bodyLen={dom.get('bodyLen')}")

        result["title"] = dom.get("title")
        result["price"] = dom.get("prices",[None])[0] if dom.get("prices") else None
        result["availability"] = dom.get("availability")
        result["images"] = dom.get("images",[])
        result["category"] = dom.get("category")
        if dom.get("prices"):
            result["method"] = "DOM"

    except Exception as e:
        print(f"  DOM err: {e}")
        result["errors"].append(f"dom: {e}")

    # ── 7. MTOP PDP FALLBACK ──
    if not result["price"] and mtop_pdp_bodies:
        print(f"\n[7] MTOP PDP FALLBACK ({len(mtop_pdp_bodies)} resp)...")
        for mr in mtop_pdp_bodies:
            body = mr["body"].strip()
            m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
            if m:
                body = m.group(1)
            try:
                d = json.loads(body)
                ret = d.get("ret",[])
                data = d.get("data",{})
                ds = json.dumps(data, ensure_ascii=False) if data else ""
                print(f"  ret={ret[:2]} dataLen={len(ds)}")
                if any("SUCCESS" in str(r) for r in ret) and len(ds) > 100:
                    # Extract price
                    pm = re.search(r'"(?:minPrice|salePrice|price|amount|actvPrice|displayPrice|formattedPrice)"\s*:\s*"?([\d.]+)"?', ds)
                    cm = re.search(r'"(?:currency|priceCurrency)"\s*:\s*"([^"]+)"', ds)
                    tm = re.search(r'"(?:title|subject|name|seoTitle)"\s*:\s*"([^"]{10,300})"', ds)
                    im = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(//[^"]*alicdn[^"]*)"', ds)
                    im2 = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(https?://[^"]*alicdn[^"]*)"', ds)
                    am = re.search(r'"(?:stock|inStock|stockStatus|available)"\s*:\s*("?[^,}\]]+)"?', ds)

                    if pm:
                        result["price"] = pm.group(1)
                        result["currency"] = cm.group(1).upper() if cm else "USD"
                        result["method"] = "MTOP_PDP"
                        print(f"  PRICE: {result['price']} {result['currency']}")
                    if tm and not result["title"]:
                        result["title"] = tm.group(1)
                        print(f"  TITLE: {result['title'][:80]}")
                    if (im or im2) and not result["images"]:
                        result["images"] = ([f"https:{u}" for u in im[:15]] if im else im2[:15])
                        print(f"  IMAGES: {len(result['images'])}")
                    if am and not result["availability"]:
                        av = am.group(1).strip('"')
                        if av in ("true","1","IN_STOCK") or (av.isdigit() and int(av)>0):
                            result["availability"] = "IN_STOCK"
                        elif av in ("false","0","OUT_OF_STOCK"):
                            result["availability"] = "OUT_OF_STOCK"
                    result["mtop_pdp_success"] = True
                    break
            except Exception:
                continue

    # ── 8. JS STATE FALLBACK ──
    if not result["price"]:
        print("\n[8] JS STATE...")
        try:
            js = page.evaluate("""
                () => {
                    const r = {};
                    if (window.__INIT_DATA_STATE__) r.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0,5000);
                    if (window.runParams) r.runParams = JSON.stringify(window.runParams).substring(0,3000);
                    if (window.__NEXT_DATA__) r.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0,5000);
                    return r;
                }
            """)
            for k,v in js.items():
                pm = re.search(r'"(?:minPrice|salePrice|price|amount|actvPrice|displayPrice)"\s*:\s*"?([\d.]+)"?', v or "")
                if pm:
                    result["price"] = pm.group(1)
                    result["currency"] = "USD"
                    result["method"] = f"JS_{k}"
                    print(f"  PRICE from {k}: {result['price']}")
                    break
        except Exception as e:
            print(f"  JS err: {e}")

    # ── Screenshot ──
    try:
        page.screenshot(path="diag_ali_pw6.png", full_page=False)
        print(f"\nScreenshot: diag_ali_pw6.png")
    except Exception:
        pass

    browser.close()

result["elapsed"] = time.monotonic() - t0

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
print(f"  TMD:        detected={result['tmd_detected']}")
print(f"  reCAPTCHA:  clicked={result['recaptcha_clicked']} solved={result['recaptcha_solved']}")
print(f"  MTOP PDP:   success={result['mtop_pdp_success']}")
print(f"  Elapsed:    {result['elapsed']:.1f}s")
print(f"  Errors:     {result['errors']}")
print(f"{'='*60}")

with open("diag_ali_pw6_result.json","w",encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Saved: diag_ali_pw6_result.json")
