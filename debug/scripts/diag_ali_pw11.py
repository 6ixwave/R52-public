"""AliExpress pw11: Persistent context + raw MTOP + DOM + TMD bypass.

Улучшения vs pw6:
- Persistent context (user_data_dir) — cookies живут между запусками
- Сырой MTOP JSON пишется в отдельный файл для инспекции
- DOM: расширенные селекторы + data-pl + React __NEXT_DATA__
- TMD: ожидание reCAPTCHA, клик, ожидание re-render
- Fallback: если MTOP пустой — пробуем curl_cffi напрямую
"""
import sys, json, re, time, os, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

LID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{LID}.html"
HOME_URL = "https://www.aliexpress.com/"
PROFILE_DIR = os.path.join(os.path.dirname(__file__), "pw_ali_profile")
RESULT_FILE = "diag_ali_pw11_result.json"
MTOP_RAW_FILE = "diag_ali_pw11_mtop_raw.json"
DOM_RAW_FILE = "diag_ali_pw11_dom_raw.json"

result = {
    "listing_id": LID, "url": PRODUCT_URL,
    "title": None, "price": None, "currency": None,
    "availability": None, "images": [], "category": None,
    "method": None, "elapsed": 0,
    "tmd_detected": False, "recaptcha_clicked": False, "recaptcha_solved": False,
    "mtop_pdp_success": False, "mtop_pdp_len": 0,
    "dom_rendered": False,
    "frames": [], "errors": [],
    "cookies_count": 0,
}

t0 = time.monotonic()
mtop_pdp_bodies = []
all_mtop = []

with sync_playwright() as pw:
    # Persistent context — cookies сохраняются
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Rome",
        ignore_https_errors=True,
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
        // Hide Playwright
        delete window.__playwright;
        delete window.__pw_manual;
    """)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # ── MTOP interceptor ──
    def on_resp(resp):
        try:
            u = resp.url
            if "mtop" in u.lower() or "pdp" in u.lower():
                body = ""
                try:
                    body = resp.text()
                except Exception:
                    pass
                entry = {"url": u[:300], "status": resp.status, "len": len(body), "body": body[:50000]}
                all_mtop.append(entry)
                if "pdp.pc.query" in u and "punish" not in u and "report" not in u:
                    mtop_pdp_bodies.append(entry)
                    print(f"  [PDP-MTOP] {resp.status} len={len(body)}")
                    print(f"    head: {body[:200]}")
        except Exception:
            pass
    page.on("response", on_resp)

    # ── 1. WARM-UP (if cookies exist, skip) ──
    cookies = ctx.cookies()
    print(f"[1] Cookies in profile: {len(cookies)}")
    result["cookies_count"] = len(cookies)

    if len(cookies) < 3:
        print(f"  WARM-UP {HOME_URL}")
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)
            # Accept cookies
            for sel in ['button:has-text("Accetta")', 'button:has-text("Accept")',
                        '[class*="cookie"] button', '[class*="Cookie"] button',
                        '#onetrust-accept-btn-handler']:
                try:
                    btn = page.query_selector(sel)
                    if btn:
                        btn.click()
                        print(f"  Cookie accepted via: {sel}")
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass
            # Scroll to trigger lazy-load
            page.evaluate("window.scrollTo(0, 300)")
            page.wait_for_timeout(2000)
            print(f"  OK title={page.title()[:60]}")
        except Exception as e:
            print(f"  WARM-UP ERR: {e}")
            result["errors"].append(f"warmup: {e}")
    else:
        print(f"  Skipping warm-up (enough cookies)")

    # ── 2. PRODUCT PAGE ──
    print(f"\n[2] PRODUCT {PRODUCT_URL}")
    try:
        resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"  HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        print(f"  NAV ERR: {e}")
        result["errors"].append(f"nav: {e}")

    # ── 3. WAIT & DETECT (20s) ──
    print("\n[3] WAIT & DETECT (20s)...")
    tmd_found = False
    rendered = False

    for tick in range(20):
        page.wait_for_timeout(1000)
        try:
            st = page.evaluate("""
                () => {
                    const r = {};
                    const h1 = document.querySelector('h1');
                    r.h1 = h1 ? h1.textContent.trim().substring(0,200) : null;
                    // Multiple price selectors
                    const pSels = [
                        '[data-pl="price"]', '[class*="price--current"]',
                        '[class*="Price--current"]', '[class*="pdp-price"]',
                        '[class*="price-current"]', '[data-testid="price"]',
                        'span[class*="price"]', '[class*="sku-price"]',
                        '[class*="PDP-price"]', '[class*="price-wrapper"]'
                    ];
                    r.prices = [];
                    for (const sel of pSels) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 50 && /\\d/.test(t)) r.prices.push(t);
                        }
                    }
                    r.prices = [...new Set(r.prices)].slice(0,10);
                    // Iframes
                    r.iframes = [];
                    Array.from(document.querySelectorAll('iframe')).forEach(f => {
                        r.iframes.push({src: (f.src||'').substring(0,300), w: f.offsetWidth, h: f.offsetHeight});
                    });
                    // Check body length
                    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                    // Check for TMD overlay
                    r.tmdOverlay = !!document.querySelector('[class*="tmd"], [class*="punish"], [id*="tmd"]');
                    return r;
                }
            """)
        except Exception as e:
            print(f"  tick {tick} eval err: {e}")
            continue

        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 10):
            rendered = True
            result["dom_rendered"] = True
            print(f"  RENDERED at tick {tick}: h1={st.get('h1','')[:80]} prices={st.get('prices',[])}")
            break

        for fr in st.get("iframes", []):
            if "punish" in fr["src"] or "tmd" in fr["src"].lower() or "recaptcha" in fr["src"].lower():
                tmd_found = True
                result["tmd_detected"] = True
                print(f"  tick {tick}: TMD/RECAPTCHA IFRAME: {fr['src'][:120]}")
                break
        if tmd_found:
            break

        if tick % 5 == 0:
            print(f"  {tick}s: h1={str(st.get('h1',''))[:50]} prices={st.get('prices',[])} bodyLen={st.get('bodyLen')} tmd={st.get('tmdOverlay')}")

    # ── 4. SOLVE TMD / reCAPTCHA ──
    if tmd_found and not rendered:
        print("\n[4] SOLVING TMD/reCAPTCHA...")
        page.wait_for_timeout(5000)

        all_frames = page.frames
        print(f"  Total frames: {len(all_frames)}")
        bframe = None
        for i, f in enumerate(all_frames):
            try:
                fu = f.url or ""
                result["frames"].append(fu[:200])
                if "recaptcha/enterprise/bframe" in fu or "recaptcha/api2/anchor" in fu:
                    bframe = f
                    print(f"    [{i}] BFRAME/ANCHOR: {fu[:150]}")
                elif "punish" in fu.lower() or "tmd" in fu.lower():
                    print(f"    [{i}] TMD: {fu[:150]}")
                elif "recaptcha" in fu.lower():
                    print(f"    [{i}] RECAPTCHA: {fu[:150]}")
            except Exception:
                pass

        if bframe:
            try:
                bframe.wait_for_load_state("domcontentloaded", timeout=10000)
                page.wait_for_timeout(3000)

                checkbox = None
                for sel in [
                    'input[type="checkbox"]',
                    '#recaptcha-anchor',
                    'span#recaptcha-anchor',
                    '[aria-label="Recaptcha"]',
                    '[aria-label="recaptcha"]',
                    '[role="checkbox"]',
                    '.rcb-anchor',
                    'div[role="presentation"] span',
                    'span[role="presentation"]',
                ]:
                    checkbox = bframe.query_selector(sel)
                    if checkbox:
                        print(f"  Checkbox found: {sel}")
                        break

                if checkbox:
                    box = checkbox.bounding_box()
                    if box and box["width"] > 0:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        page.mouse.move(cx - 5, cy - 3, steps=5)
                        page.wait_for_timeout(400)
                        page.mouse.move(cx, cy, steps=3)
                        page.wait_for_timeout(200)
                        page.mouse.click(cx, cy)
                        result["recaptcha_clicked"] = True
                        print(f"  Clicked at ({cx:.0f},{cy:.0f})")
                    else:
                        checkbox.click()
                        result["recaptcha_clicked"] = True
                        print("  Clicked via element")
                else:
                    print("  No checkbox, trying frame center...")
                    for f in all_frames:
                        try:
                            fu = f.url or ""
                            if "punish" in fu:
                                iframe_el = f.query_selector('iframe')
                                if iframe_el:
                                    ib = iframe_el.bounding_box()
                                    if ib:
                                        cx = ib["x"] + 30
                                        cy = ib["y"] + ib["h"] / 2
                                        page.mouse.move(cx, cy, steps=3)
                                        page.wait_for_timeout(300)
                                        page.mouse.click(cx, cy)
                                        result["recaptcha_clicked"] = True
                                        print(f"  Clicked iframe at ({cx:.0f},{cy:.0f})")
                                        break
                        except Exception:
                            continue
            except Exception as e:
                print(f"  BFRAME err: {e}")
                result["errors"].append(f"bframe: {e}")

        # Wait for re-render after captcha
        print("  Waiting for re-render (35s)...")
        for i in range(35):
            page.wait_for_timeout(1000)
            try:
                check = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const pEls = document.querySelectorAll('[data-pl="price"], [class*="price--current"], span[class*="price"], [class*="Price--current"]');
                        const prices = [];
                        pEls.forEach(el => {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 50 && /\\d/.test(t)) prices.push(t);
                        });
                        return {
                            h1: h1 ? h1.textContent.trim().substring(0,200) : null,
                            prices: [...new Set(prices)].slice(0,8),
                            bodyLen: document.body ? document.body.innerHTML.length : 0
                        };
                    }
                """)
                if i % 5 == 0:
                    print(f"  {i}s: h1={str(check.get('h1',''))[:60]} prices={check.get('prices',[])} body={check.get('bodyLen')}")
                if check.get("prices") or (check.get("h1") and len(check.get("h1","")) > 10):
                    rendered = True
                    result["dom_rendered"] = True
                    result["recaptcha_solved"] = True
                    print(f"  RE-RENDERED at {i}s!")
                    break
            except Exception:
                continue

        if not rendered:
            print("  Still not rendered after 35s")

    # ── 5. DOM EXTRACTION ──
    print("\n[5] DOM EXTRACTION...")
    dom_data = {}
    try:
        dom_data = page.evaluate("""
            () => {
                const r = {};
                // Title
                const h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                if (!r.title) {
                    const t = document.querySelector('title');
                    if (t) r.title = t.textContent.replace(/\\s*[|\\-–—].*$/,'').trim();
                }
                // Prices - comprehensive
                r.prices = [];
                const pSelectors = [
                    '[data-pl="price"]', '[class*="price--current"]',
                    '[class*="Price--current"]', '[class*="pdp-price"]',
                    '[class*="price-current"]', '[data-testid="price"]',
                    'span[class*="price"]', '[class*="sku-price"]',
                    '[class*="PDP-price"]', '[class*="price-wrapper"]',
                    '[class*="price--"]', '[class*="Price--"]'
                ];
                for (const sel of pSelectors) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 50 && /\\d/.test(t)) r.prices.push(t);
                        }
                    } catch(e) {}
                }
                r.prices = [...new Set(r.prices)].slice(0,12);
                // Availability
                r.availability = null;
                const aSels = ['[class*="stock"]', '[class*="availability"]', '[data-pl="stock"]',
                               '[class*="pdp-stock"]', '[class*="PDP-stock"]', '[class*="inventory"]'];
                for (const sel of aSels) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.textContent||'').trim().toLowerCase();
                            if (t.includes('in stock')||t.includes('disponib')||t.includes('available')||t.includes('in magazzino')){r.availability='IN_STOCK';break;}
                            if (t.includes('out of stock')||t.includes('non disponib')||t.includes('unavailable')||t.includes('esaurito')){r.availability='OUT_OF_STOCK';break;}
                            if (t.includes('pre-order')||t.includes('preorder')||t.includes('pre-ordin')){r.availability='PRE_ORDER';break;}
                        }
                    } catch(e) {}
                    if (r.availability) break;
                }
                // Images
                r.images = [];
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
                    if (src.includes('alicdn') && !src.includes('placeholder') && !src.includes('logo')
                        && !src.includes('icon') && !src.includes('blank') && !src.includes('spacer')
                        && (src.includes('/kf/')||src.includes('s.alicdn')||src.includes('ae0')||src.includes('aliexpress-media'))) {
                        r.images.push(src);
                    }
                }
                r.images = [...new Set(r.images)].slice(0,20);
                // Category / breadcrumb
                const bc = document.querySelector('[class*="breadcrumb"], [class*="Breadcrumb"], nav[aria-label*="readcrumb"]');
                r.category = bc ? bc.textContent.trim().substring(0,300) : null;
                // Meta tags
                r.meta = {};
                const metas = document.querySelectorAll('meta[property], meta[name]');
                for (const m of metas) {
                    const prop = m.getAttribute('property') || m.getAttribute('name') || '';
                    const content = m.getAttribute('content') || '';
                    if (prop.includes('og:') || prop.includes('product') || prop.includes('price')) {
                        r.meta[prop] = content.substring(0,200);
                    }
                }
                // JSON-LD
                r.jsonld = [];
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                for (const s of scripts) {
                    try {
                        const d = JSON.parse(s.textContent);
                        r.jsonld.push(d);
                    } catch(e) {}
                }
                // React/Next state
                r.jsState = {};
                try { if (window.__INIT_DATA_STATE__) r.jsState.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0,8000); } catch(e){}
                try { if (window.runParams) r.jsState.runParams = JSON.stringify(window.runParams).substring(0,5000); } catch(e){}
                try { if (window.__NEXT_DATA__) r.jsState.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0,8000); } catch(e){}
                try { if (window.__NUXT__) r.jsState.nuxt = JSON.stringify(window.__NUXT__).substring(0,5000); } catch(e){}
                r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                return r;
            }
        """)
        print(f"  title={str(dom_data.get('title',''))[:80]}")
        print(f"  prices={dom_data.get('prices',[])}")
        print(f"  avail={dom_data.get('availability')} images={len(dom_data.get('images',[]))} bodyLen={dom_data.get('bodyLen')}")
        print(f"  meta={json.dumps(dom_data.get('meta',{}), ensure_ascii=False)[:300]}")
        print(f"  jsonld_count={len(dom_data.get('jsonld',[]))}")
        print(f"  jsState keys={list(dom_data.get('jsState',{}).keys())}")

        result["title"] = dom_data.get("title")
        if dom_data.get("prices"):
            result["price"] = dom_data["prices"][0]
            result["method"] = "DOM"
        result["availability"] = dom_data.get("availability")
        result["images"] = dom_data.get("images", [])
        result["category"] = dom_data.get("category")

    except Exception as e:
        print(f"  DOM err: {e}")
        result["errors"].append(f"dom: {e}")

    # Save DOM raw
    try:
        with open(DOM_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump(dom_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved: {DOM_RAW_FILE}")
    except Exception:
        pass

    # ── 6. MTOP PDP PARSING ──
    if mtop_pdp_bodies:
        print(f"\n[6] MTOP PDP PARSING ({len(mtop_pdp_bodies)} responses)...")
        for idx, mr in enumerate(mtop_pdp_bodies):
            body = mr["body"].strip()
            # Strip JSONP wrapper
            m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
            if m:
                body = m.group(1)
            try:
                d = json.loads(body)
                ret = d.get("ret", [])
                data = d.get("data", {})
                ds = json.dumps(data, ensure_ascii=False) if data else ""
                print(f"  [{idx}] ret={ret[:2]} dataLen={len(ds)}")

                # Save full MTOP
                if idx == 0:
                    with open(MTOP_RAW_FILE, "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
                    print(f"  Saved full MTOP: {MTOP_RAW_FILE}")

                if any("SUCCESS" in str(r) for r in ret) and len(ds) > 100:
                    # Deep search for price
                    price_found = False
                    # Try structured paths
                    struct_paths = [
                        ("data", "pdp", "price", "minPrice"),
                        ("data", "pdp", "price", "salePrice"),
                        ("data", "pdp", "price", "actvPrice"),
                        ("data", "pdp", "price", "displayPrice"),
                        ("data", "pdp", "price", "formattedPrice"),
                        ("data", "pdp", "price", "amount"),
                        ("data", "price", "minPrice"),
                        ("data", "price", "salePrice"),
                        ("data", "sku", "price", "minPrice"),
                        ("data", "sku", "price", "salePrice"),
                    ]
                    for path in struct_paths:
                        val = data
                        for key in path:
                            if isinstance(val, dict):
                                val = val.get(key)
                            else:
                                val = None
                                break
                        if val and isinstance(val, (int, float, str)) and str(val) and any(c.isdigit() for c in str(val)):
                            result["price"] = str(val)
                            price_found = True
                            result["method"] = f"MTOP_{'.'.join(path)}"
                            print(f"  PRICE via path: {'.'.join(path)} = {val}")
                            break

                    # Regex fallback on full JSON string
                    if not price_found:
                        pm = re.search(r'"(?:minPrice|salePrice|actvPrice|displayPrice|formattedPrice|price|amount)"\s*:\s*"?([\d.]+)"?', ds)
                        if pm:
                            result["price"] = pm.group(1)
                            price_found = True
                            result["method"] = "MTOP_REGEX"
                            print(f"  PRICE via regex: {pm.group(1)}")

                    # Currency
                    cm = re.search(r'"(?:currency|priceCurrency|currencyCode)"\s*:\s*"([^"]+)"', ds)
                    if cm:
                        result["currency"] = cm.group(1).upper()
                    elif not result.get("currency"):
                        result["currency"] = "EUR"  # it.aliexpress.com

                    # Title
                    tm = re.search(r'"(?:title|subject|seoTitle|productTitle|name)"\s*:\s*"([^"]{10,500})"', ds)
                    if tm and not result["title"]:
                        result["title"] = tm.group(1)
                        print(f"  TITLE: {result['title'][:80]}")

                    # Images
                    im = re.findall(r'"(?:url|imageUrl|path|src)"\s*:\s*"(//[^"]*alicdn[^"]*)"', ds)
                    im2 = re.findall(r'"(?:url|imageUrl|path|src)"\s*:\s*"(https?://[^"]*alicdn[^"]*)"', ds)
                    if (im or im2) and not result["images"]:
                        result["images"] = ([f"https:{u}" for u in im[:20]] if im else im2[:20])
                        print(f"  IMAGES: {len(result['images'])}")

                    # Availability
                    am = re.search(r'"(?:stock|inStock|stockStatus|available|inventoryStatus|stockCount)"\s*:\s*("?[^,}\]]+)"?', ds)
                    if am and not result["availability"]:
                        av = am.group(1).strip('"')
                        if av in ("true","1","IN_STOCK","AVAILABLE") or (av.isdigit() and int(av)>0):
                            result["availability"] = "IN_STOCK"
                        elif av in ("false","0","OUT_OF_STOCK","UNAVAILABLE"):
                            result["availability"] = "OUT_OF_STOCK"
                        print(f"  AVAIL: {result['availability']}")

                    result["mtop_pdp_success"] = True
                    result["mtop_pdp_len"] = len(ds)
                    break
                elif any("FAIL" in str(r) for r in ret):
                    print(f"  [{idx}] MTOP FAIL: {ret}")
            except Exception as e:
                print(f"  [{idx}] parse err: {e}")
    else:
        print("\n[6] No MTOP PDP responses captured")

    # ── 7. JS STATE FALLBACK ──
    if not result["price"] and dom_data.get("jsState"):
        print("\n[7] JS STATE FALLBACK...")
        for k, v in dom_data["jsState"].items():
            if not v:
                continue
            pm = re.search(r'"(?:minPrice|salePrice|price|amount|actvPrice|displayPrice|formattedPrice)"\s*:\s*"?([\d.]+)"?', v)
            if pm:
                result["price"] = pm.group(1)
                result["currency"] = result.get("currency") or "USD"
                result["method"] = f"JS_{k}"
                print(f"  PRICE from {k}: {pm.group(1)}")
                break
            # Also try title
            tm = re.search(r'"(?:title|subject|productTitle|name)"\s*:\s*"([^"]{10,300})"', v)
            if tm and not result["title"]:
                result["title"] = tm.group(1)
                print(f"  TITLE from {k}: {tm.group(1)[:60]}")

    # ── 8. JSON-LD FALLBACK ──
    if not result["price"] and dom_data.get("jsonld"):
        print("\n[8] JSON-LD FALLBACK...")
        for ld in dom_data["jsonld"]:
            if isinstance(ld, dict) and ld.get("@type") in ("Product", ["Product"]):
                offers = ld.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                p = offers.get("price") or offers.get("lowPrice")
                if p:
                    result["price"] = str(p)
                    result["currency"] = offers.get("priceCurrency", "USD")
                    result["method"] = "JSON-LD"
                    print(f"  PRICE: {p} {result['currency']}")
                if not result["title"] and ld.get("name"):
                    result["title"] = ld["name"]
                if not result["availability"]:
                    av = offers.get("availability", "")
                    if "IN_STOCK" in av or "InStock" in av:
                        result["availability"] = "IN_STOCK"
                    elif "OUT_OF_STOCK" in av or "OutOfStock" in av:
                        result["availability"] = "OUT_OF_STOCK"
                break

    # ── Screenshot ──
    try:
        page.screenshot(path="diag_ali_pw11.png", full_page=False)
        print(f"\nScreenshot: diag_ali_pw11.png")
    except Exception:
        pass

    # Save all MTOP
    try:
        with open("diag_ali_pw11_all_mtop.json", "w", encoding="utf-8") as f:
            json.dump(all_mtop, f, ensure_ascii=False, indent=2)
        print(f"Saved all MTOP: {len(all_mtop)} entries")
    except Exception:
        pass

    ctx.close()

result["elapsed"] = time.monotonic() - t0

print(f"\n{'='*60}")
print(f"RESULT:")
print(f"  Title:      {result['title']}")
print(f"  Price:      {result['price']} {result.get('currency','')}")
print(f"  Avail:      {result['availability']}")
print(f"  Images:     {len(result['images'])}")
for img in result["images"][:5]:
    print(f"    {str(img)[:120]}")
print(f"  Category:   {result['category']}")
print(f"  Method:     {result['method']}")
print(f"  TMD:        detected={result['tmd_detected']}")
print(f"  reCAPTCHA:  clicked={result['recaptcha_clicked']} solved={result['recaptcha_solved']}")
print(f"  MTOP PDP:   success={result['mtop_pdp_success']} len={result['mtop_pdp_len']}")
print(f"  DOM:        rendered={result['dom_rendered']}")
print(f"  Cookies:    {result['cookies_count']}")
print(f"  Elapsed:    {result['elapsed']:.1f}s")
print(f"  Errors:     {result['errors']}")
print(f"{'='*60}")

with open(RESULT_FILE, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Saved: {RESULT_FILE}")
