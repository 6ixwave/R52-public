"""AliExpress Baxia captcha: найти, скриншот, решить, получить MTOP."""
import sys, json, re, time, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

LID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{LID}.html"

mtop_data = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    """)
    page = ctx.new_page()

    def on_resp(resp):
        try:
            u = resp.url
            if "mtop.aliexpress.pdp" in u:
                body = resp.text()
                mtop_data.append({"url": u, "status": resp.status, "body": body})
                print(f"  [MTOP] {resp.status} len={len(body)}")
                print(f"    {body[:200]}")
        except:
            pass
    page.on("response", on_resp)

    # ── Open product page ──
    print("OPENING PRODUCT...")
    t0 = time.monotonic()
    page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(8000)
    print(f"  loaded {time.monotonic()-t0:.1f}s")

    # ── Find the Baxia captcha dialog ──
    print("\nSEARCHING FOR CAPTCHA...")
    
    # Strategy: look for iframes, dialogs, overlays
    # Baxia often uses an iframe or a div overlay
    
    # 1. Check for iframes
    frames = page.frames
    print(f"  Frames: {len(frames)}")
    for f in frames:
        print(f"    frame: {f.url[:100]} name={f.name}")
    
    # 2. Check for common Baxia containers in main page
    captcha_info = page.evaluate("""
    () => {
        const results = {};
        
        // Look for baxia/punish/captcha elements
        const selectors = [
            '.baxia-dialog', '.baxia-captcha', '[class*="baxia"]',
            '.nc-container', '#nc_1_wrapper', '.J_MIDDLEware_LAYER',
            '[class*="captcha"]', '[class*="Captcha"]',
            '.punish-dialog', '[class*="punish"]',
            '.container', '.dialog', '[role="dialog"]',
            '.nc_wrapper', '.nc-lang-cnt',
            '[class*="verify"]', '[class*="slide"]',
        ];
        
        for (const sel of selectors) {
            const els = document.querySelectorAll(sel);
            if (els.length > 0) {
                results[sel] = [];
                for (const el of els.slice(0, 3)) {
                    results[sel].push({
                        tag: el.tagName,
                        id: el.id,
                        cls: (el.className || '').substring(0, 100),
                        visible: el.offsetParent !== null,
                        w: el.offsetWidth,
                        h: el.offsetHeight,
                        html: el.innerHTML.substring(0, 300)
                    });
                }
            }
        }
        
        // Check for iframe
        const iframes = document.querySelectorAll('iframe');
        results['iframes'] = [];
        for (const f of iframes) {
            results['iframes'].push({
                src: (f.src || '').substring(0, 200),
                w: f.offsetWidth,
                h: f.offsetHeight,
                visible: f.offsetParent !== null
            });
        }
        
        // Check for slider elements specifically
        const sliders = document.querySelectorAll('[class*="slider"], [class*="slide"], [class*="drag"], [class*="puzzle"]');
        results['sliders'] = [];
        for (const s of sliders.slice(0, 5)) {
            results['sliders'].push({
                tag: s.tagName,
                cls: (s.className || '').substring(0, 100),
                w: s.offsetWidth,
                h: s.offsetHeight
            });
        }
        
        // Check for buttons
        const btns = document.querySelectorAll('button, [class*="btn"], [class*="confirm"], [class*="verify"]');
        results['buttons'] = [];
        for (const b of btns.slice(0, 10)) {
            results['buttons'].push({
                tag: b.tagName,
                text: (b.textContent || '').trim().substring(0, 50),
                cls: (b.className || '').substring(0, 80),
                visible: b.offsetParent !== null
            });
        }
        
        return results;
    }
    """)
    
    print(f"  Captcha info: {json.dumps(captcha_info, ensure_ascii=False, indent=2)[:2000]}")
    
    # Screenshot
    page.screenshot(path="diag_ali_cap_full.png")
    print("  Screenshot saved: diag_ali_cap_full.png")
    
    # ── Try to solve ──
    print("\nSOLVING CAPTCHA...")
    
    solved = False
    
    # Attempt 1: If there's a slider (Baxia slide puzzle)
    # Baxia slider: usually a track + a handle you drag
    slider_info = page.evaluate("""
    () => {
        // Common Baxia slider patterns
        const patterns = [
            // NC (NoCaptcha) slider
            '.nc-container .nc_slider', '.nc-container .nc_iconfont',
            // Generic slider
            '[class*="slider"] [class*="handle"]', '[class*="slider"] [class*="btn"]',
            // Baxia specific
            '.baxia-slider-btn', '[class*="baxia"] [class*="slider"]',
            // Track
            '.nc-container .nc_scale', '[class*="slider-track"]',
        ];
        const results = {};
        for (const p of patterns) {
            const el = document.querySelector(p);
            if (el) {
                const r = el.getBoundingClientRect();
                results[p] = {x: r.x, y: r.y, w: r.width, h: r.height, visible: el.offsetParent !== null};
            }
        }
        return results;
    }
    """)
    print(f"  Slider elements: {json.dumps(slider_info, ensure_ascii=False)}")
    
    if slider_info:
        # Drag the slider
        # Find the handle (first element) and track
        handle = None
        track_w = 300  # default
        
        for sel, info in slider_info.items():
            if 'handle' in sel or 'btn' in sel or 'slider' in sel:
                handle = page.query_selector(sel)
                break
        
        if not handle:
            # Try broader search
            handle = page.query_selector('[class*="slider"] [class*="handle"], [class*="nc_slider"], [class*="slide-btn"]')
        
        if handle:
            box = handle.bounding_box()
            if box:
                # Drag from handle center to the right (track width - handle width)
                start_x = box["x"] + box["width"] / 2
                start_y = box["y"] + box["height"] / 2
                # Drag distance: usually track width minus some offset
                drag_dist = 280  # typical Baxia track ~300px
                end_x = start_x + drag_dist
                end_y = start_y
                
                print(f"  Dragging slider: ({start_x:.0f},{start_y:.0f}) -> ({end_x:.0f},{end_y:.0f})")
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                # Smooth drag with steps
                steps = 30
                for i in range(1, steps + 1):
                    x = start_x + (end_x - start_x) * i / steps
                    y = start_y + (i % 3) * 2  # slight Y jitter
                    page.mouse.move(x, y)
                    page.wait_for_timeout(15)
                page.mouse.up()
                print("  Slider dragged")
                page.wait_for_timeout(3000)
                solved = True
    
    # Attempt 2: If there's a "click to verify" button
    if not solved:
        btn_info = page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button, [class*="verify"], [class*="confirm"], [class*="btn"]');
            const results = [];
            for (const b of btns) {
                if (b.offsetParent !== null && b.offsetWidth > 20) {
                    const r = b.getBoundingClientRect();
                    results.push({
                        tag: b.tagName,
                        text: (b.textContent || '').trim().substring(0, 50),
                        cls: (b.className || '').substring(0, 80),
                        x: r.x, y: r.y, w: r.width, h: r.height
                    });
                }
            }
            return results;
        }
        """)
        print(f"  Visible buttons: {json.dumps(btn_info, ensure_ascii=False)[:500]}")
        
        for btn in btn_info:
            if any(k in btn["text"].lower() for k in ("verify", "confirm", "ok", "yes", "proceed", "slider", "drag")):
                page.mouse.click(btn["x"] + btn["w"]/2, btn["y"] + btn["h"]/2)
                print(f"  Clicked button: {btn['text']}")
                page.wait_for_timeout(3000)
                solved = True
                break
    
    # Attempt 3: If it's a puzzle (drag piece to gap)
    if not solved:
        puzzle = page.evaluate("""
        () => {
            // Baxia puzzle: background image + draggable piece
            const bg = document.querySelector('[class*="puzzle-bg"], [class*="captcha-bg"], img[class*="captcha"]');
            const piece = document.querySelector('[class*="puzzle-piece"], [class*="captcha-piece"], [class*="drag"]');
            const result = {};
            if (bg) {
                const r = bg.getBoundingClientRect();
                result.bg = {x: r.x, y: r.y, w: r.width, h: r.height, src: (bg.src || bg.style.backgroundImage || '').substring(0, 100)};
            }
            if (piece) {
                const r = piece.getBoundingClientRect();
                result.piece = {x: r.x, y: r.y, w: r.width, h: r.height};
            }
            return result;
        }
        """)
        print(f"  Puzzle: {json.dumps(puzzle, ensure_ascii=False)}")
        
        if puzzle.get("piece"):
            px = puzzle["piece"]["x"] + puzzle["piece"]["width"] / 2
            py = puzzle["piece"]["y"] + puzzle["piece"]["height"] / 2
            # Drag to ~60% of background width (approximate gap position)
            target_x = puzzle.get("bg", {}).get("x", px) + puzzle.get("bg", {}).get("w", 300) * 0.6
            page.mouse.move(px, py)
            page.mouse.down()
            for i in range(1, 25):
                x = px + (target_x - px) * i / 24
                page.mouse.move(x, py + (i % 2))
                page.wait_for_timeout(20)
            page.mouse.up()
            print(f"  Puzzle dragged to {target_x:.0f}")
            page.wait_for_timeout(3000)
            solved = True
    
    # Attempt 4: Iframe-based captcha
    if not solved:
        for f in page.frames:
            if f == page.main_frame:
                continue
            print(f"  Trying iframe: {f.url[:80]}")
            try:
                # Look for slider/button inside iframe
                iframe_btn = f.query_selector('[class*="slider"], [class*="btn"], button, [class*="drag"]')
                if iframe_btn:
                    box = iframe_btn.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                        print(f"    Clicked in iframe")
                        page.wait_for_timeout(3000)
                        solved = True
                        break
            except Exception as e:
                print(f"    iframe error: {e}")
    
    # ── Wait for post-captcha MTOP ──
    print(f"\nWAITING FOR MTOP (solved={solved})...")
    before = len(mtop_data)
    page.wait_for_timeout(10000)
    new_mt = mtop_data[before:]
    print(f"  New MTOP responses: {len(new_mt)}")
    
    product_data = None
    for mr in mtop_data:
        clean = mr["body"].strip()
        m = re.match(r'mtopjsonp\d*\((.*)\)', clean, re.DOTALL)
        if m:
            clean = m.group(1)
        try:
            d = json.loads(clean)
            ret = d.get("ret", [])
            if any("SUCCESS" in r for r in ret):
                product_data = d.get("data", {})
                print(f"  *** MTOP SUCCESS ***")
                break
        except:
            pass
    
    # ── Final screenshot ──
    page.screenshot(path="diag_ali_cap_after.png")
    print("  After screenshot saved: diag_ali_cap_after.png")
    
    # ── Extract data ──
    if product_data:
        s = json.dumps(product_data, ensure_ascii=False)
        print(f"\nPRODUCT DATA: {len(s)} bytes")
        print(f"  Keys: {list(product_data.keys())[:20]}")
        
        title = None
        for k in ("title","subject","name","productTitle","seoTitle"):
            if k in product_data and isinstance(product_data[k], str):
                title = product_data[k]; break
        if not title:
            m = re.search(r'"(?:title|subject)"\s*:\s*"([^"]{10,300})"', s)
            title = m.group(1) if m else None
        print(f"  Title: {title[:120] if title else 'N/A'}")
        
        prices = re.findall(r'"(?:minPrice|salePrice|price|amount|formattedPrice|displayPrice)"\s*:\s*"?([\d.]+)"?', s)
        currs = re.findall(r'"(?:currency|priceCurrency)"\s*:\s*"([^"]+)"', s)
        print(f"  Prices: {prices[:8]}")
        print(f"  Currency: {currs[:5]}")
        
        imgs = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(//[^"]*alicdn[^"]*)"', s)
        imgs2 = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(https?://[^"]*alicdn[^"]*)"', s)
        print(f"  Images: {len(imgs)+len(imgs2)}")
        for u in (imgs+imgs2)[:5]:
            print(f"    {u[:120]}")
        
        avail = re.findall(r'"(?:stock|inStock|stockStatus|available|buyNow|stockCount)"\s*:\s*("?[^,}\]]+)"?', s)
        print(f"  Avail: {avail[:5]}")
        
        with open("diag_ali_cap_data.json", "w", encoding="utf-8") as f:
            json.dump(product_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved: diag_ali_cap_data.json")
    else:
        # Last resort: DOM
        print("\nDOM FALLBACK...")
        h1 = page.query_selector("h1")
        print(f"  h1: {h1.inner_text()[:100] if h1 else 'N/A'}")
        
        # Look for price in rendered DOM
        price_txt = page.evaluate("""
        () => {
            const els = document.querySelectorAll('[class*="price"], [data-price], [class*="Price"]');
            const results = [];
            for (const el of els.slice(0, 10)) {
                const t = el.textContent.trim();
                if (t && t.length < 30) results.push(t);
            }
            return results;
        }
        """)
        print(f"  Price elements: {price_txt[:8]}")
        
        imgs = page.query_selector_all("img[src*='alicdn']")
        prod_imgs = []
        for img in imgs:
            src = img.get_attribute("src") or ""
            if any(x in src for x in ("assets", "s.alicdn", "img.alicdn", "tfs/", "g.alicdn")):
                continue
            prod_imgs.append(src)
        print(f"  Product images: {len(prod_imgs)}")
        for u in prod_imgs[:5]:
            print(f"    {u[:120]}")
    
    browser.close()

print(f"\nDone. Total: {time.monotonic()-t0:.1f}s")
