"""AliExpress pw12: Take product from homepage (guaranteed available), full extraction.

Key fixes vs pw11:
- Product picked from homepage feed (not a hardcoded banned ID)
- ALL MTOP responses saved (not just first)
- Better DOM: wait for React hydration, multiple price selectors
- og: meta as reliable fallback
- runParams / __INIT_DATA_STATE__ deep parse
"""
import sys, json, re, time, os, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

HOME_URL = "https://www.aliexpress.com/"
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pw_ali_profile")
RESULT_FILE = "diag_ali_pw12_result.json"
MTOP_RAW_FILE = "diag_ali_pw12_mtop_raw.json"
DOM_RAW_FILE = "diag_ali_pw12_dom_raw.json"

result = {
    "listing_id": None, "url": None,
    "title": None, "price": None, "currency": None,
    "availability": None, "images": [], "category": None,
    "method": None, "elapsed": 0,
    "tmd_detected": False, "recaptcha_clicked": False, "recaptcha_solved": False,
    "mtop_pdp_success": False, "mtop_pdp_len": 0,
    "dom_rendered": False,
    "frames": [], "errors": [],
    "cookies_count": 0,
    "product_source": "homepage",
}

t0 = time.monotonic()
mtop_pdp_bodies = []
all_mtop = []

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
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
                entry = {"url": u[:400], "status": resp.status, "len": len(body), "body": body[:80000]}
                all_mtop.append(entry)
                if "pdp.pc.query" in u and "punish" not in u and "report" not in u:
                    mtop_pdp_bodies.append(entry)
                    print(f"  [PDP-MTOP] {resp.status} len={len(body)}")
                    print(f"    head: {body[:250]}")
        except Exception:
            pass
    page.on("response", on_resp)

    # ── 1. HOME PAGE ──
    cookies = ctx.cookies()
    print(f"[1] HOME | cookies={len(cookies)}")
    result["cookies_count"] = len(cookies)

    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        # Accept cookies
        for sel in ['button:has-text("Accetta")', 'button:has-text("Accept")',
                    '[class*="cookie"] button', '#onetrust-accept-btn-handler']:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    print(f"  Cookie accepted: {sel}")
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass
        # Scroll to load products
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(4000)
        print(f"  OK title={page.title()[:60]}")
    except Exception as e:
        print(f"  HOME ERR: {e}")
        result["errors"].append(f"home: {e}")

    # ── 2. PICK PRODUCT FROM HOMEPAGE ──
    print("\n[2] PICKING PRODUCT FROM HOMEPAGE...")
    product_url = None
    product_id = None

    # Strategy: find product links in the main feed
    try:
        links = page.evaluate("""
            () => {
                const results = [];
                // Common AliExpress product link patterns
                const selectors = [
                    'a[href*="/item/"]',
                    'a[href*="/i/"]',
                    '[class*="product"] a[href]',
                    '[class*="card"] a[href]',
                    '[data-pl*="item"] a',
                    'a[data-tracker*="item"]',
                ];
                for (const sel of selectors) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const href = el.href || el.getAttribute('href') || '';
                            const m = href.match(/(?:item|i)\\/(\\d{10,})/);
                            if (m) {
                                results.push({id: m[1], url: href, text: (el.textContent||'').trim().substring(0,100)});
                            }
                        }
                    } catch(e) {}
                    if (results.length > 5) break;
                }
                return results;
            }
        """)
        print(f"  Found {len(links)} product links")
        for i, l in enumerate(links[:8]):
            print(f"    [{i}] id={l['id']} text={l['text'][:60]}")

        if links:
            product_id = links[0]["id"]
            product_url = links[0]["url"]
            if not product_url.startswith("http"):
                product_url = "https:" + product_url if product_url.startswith("//") else "https://www.aliexpress.com" + product_url
            print(f"  PICKED: id={product_id} url={product_url[:100]}")
    except Exception as e:
        print(f"  Pick err: {e}")

    # Fallback: hardcoded known-good product (if homepage pick fails)
    if not product_id:
        print("  Fallback to known product...")
        product_id = "1005006798847852"
        product_url = f"https://www.aliexpress.com/item/{product_id}.html"

    result["listing_id"] = product_id
    result["url"] = product_url

    # ── 3. NAVIGATE TO PRODUCT ──
    print(f"\n[3] NAVIGATE {product_url}")
    try:
        resp = page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
        print(f"  HTTP {resp.status if resp else 'N/A'}")
    except Exception as e:
        print(f"  NAV ERR: {e}")
        result["errors"].append(f"nav: {e}")

    # ── 4. WAIT FOR RENDER (25s) ──
    print("\n[4] WAIT FOR RENDER (25s)...")
    rendered = False
    tmd_found = False

    for tick in range(25):
        page.wait_for_timeout(1000)
        try:
            st = page.evaluate("""
                () => {
                    const r = {};
                    const h1 = document.querySelector('h1');
                    r.h1 = h1 ? h1.textContent.trim().substring(0,300) : null;
                    r.prices = [];
                    const pSels = [
                        '[data-pl="price"]', '[class*="price--current"]',
                        '[class*="Price--current"]', '[class*="pdp-price"]',
                        '[class*="price-current"]', '[data-testid="price"]',
                        'span[class*="price"]', '[class*="sku-price"]',
                        '[class*="PDP-price"]', '[class*="price-wrapper"]',
                        '[class*="price--"]', '[class*="Price--"]',
                        '[class*="pdp-price-"]', '[class*="priceRow"]'
                    ];
                    for (const sel of pSels) {
                        try {
                            const els = document.querySelectorAll(sel);
                            for (const el of els) {
                                const t = (el.textContent||'').trim();
                                if (t && t.length < 60 && /\\d/.test(t)) r.prices.push(t);
                            }
                        } catch(e) {}
                    }
                    r.prices = [...new Set(r.prices)].slice(0,10);
                    r.iframes = [];
                    Array.from(document.querySelectorAll('iframe')).forEach(f => {
                        r.iframes.push({src: (f.src||'').substring(0,300), w: f.offsetWidth, h: f.offsetHeight});
                    });
                    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                    return r;
                }
            """)
        except Exception:
            continue

        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
            rendered = True
            result["dom_rendered"] = True
            print(f"  RENDERED tick={tick}: h1={st.get('h1','')[:80]} prices={st.get('prices',[])}")
            break

        for fr in st.get("iframes", []):
            if "punish" in fr["src"] or "tmd" in fr["src"].lower() or "recaptcha" in fr["src"].lower():
                tmd_found = True
                result["tmd_detected"] = True
                print(f"  tick={tick} TMD: {fr['src'][:100]}")
                break
        if tmd_found:
            break

        if tick % 5 == 0:
            print(f"  {tick}s: h1={str(st.get('h1',''))[:50]} prices={st.get('prices',[])} body={st.get('bodyLen')}")

    # ── 5. SOLVE TMD if needed ──
    if tmd_found and not rendered:
        print("\n[5] SOLVING TMD...")
        page.wait_for_timeout(5000)
        all_frames = page.frames
        bframe = None
        for f in all_frames:
            try:
                fu = f.url or ""
                if "recaptcha" in fu and ("bframe" in fu or "anchor" in fu):
                    bframe = f
                    break
            except Exception:
                pass

        if bframe:
            try:
                bframe.wait_for_load_state("domcontentloaded", timeout=8000)
                page.wait_for_timeout(2000)
                for sel in ['input[type="checkbox"]', '#recaptcha-anchor', '[role="checkbox"]']:
                    cb = bframe.query_selector(sel)
                    if cb:
                        box = cb.bounding_box()
                        if box and box["width"] > 0:
                            page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                        else:
                            cb.click()
                        result["recaptcha_clicked"] = True
                        print(f"  Clicked: {sel}")
                        break
            except Exception as e:
                print(f"  TMD err: {e}")

        # Wait re-render
        for i in range(30):
            page.wait_for_timeout(1000)
            try:
                check = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const pEls = document.querySelectorAll('[data-pl="price"], [class*="price--current"], span[class*="price"]');
                        const prices = [];
                        pEls.forEach(el => { const t=(el.textContent||'').trim(); if(t && /\\d/.test(t) && t.length<50) prices.push(t); });
                        return {h1: h1?h1.textContent.trim().substring(0,200):null, prices:[...new Set(prices)].slice(0,6)};
                    }
                """)
                if check.get("prices") or (check.get("h1") and len(check["h1"]) > 15):
                    rendered = True
                    result["recaptcha_solved"] = True
                    print(f"  RE-RENDERED at {i}s")
                    break
            except Exception:
                continue

    # ── 6. FULL DOM EXTRACTION ──
    print("\n[6] DOM EXTRACTION...")
    dom_data = {}
    try:
        dom_data = page.evaluate("""
            () => {
                const r = {};
                // Title: h1 > title > og:title
                const h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                if (!r.title) {
                    const t = document.querySelector('title');
                    if (t) r.title = t.textContent.replace(/\\s*[|\\-–—].*$/,'').trim();
                }
                // Prices
                r.prices = [];
                const pSels = [
                    '[data-pl="price"]', '[class*="price--current"]',
                    '[class*="Price--current"]', '[class*="pdp-price"]',
                    '[class*="price-current"]', '[data-testid="price"]',
                    'span[class*="price"]', '[class*="sku-price"]',
                    '[class*="PDP-price"]', '[class*="price-wrapper"]',
                    '[class*="price--"]', '[class*="Price--"]',
                    '[class*="priceRow"]', '[class*="price-row"]',
                    '[data-pl*="price"]', '[class*="PriceRow"]'
                ];
                for (const sel of pSels) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 60 && /\\d/.test(t)) r.prices.push(t);
                        }
                    } catch(e) {}
                }
                r.prices = [...new Set(r.prices)].slice(0,15);
                // Also grab from data attributes
                const dataPrices = document.querySelectorAll('[data-price], [data-activity-price], [data-original-price]');
                for (const el of dataPrices) {
                    const v = el.getAttribute('data-price') || el.getAttribute('data-activity-price') || el.getAttribute('data-original-price');
                    if (v) r.prices.push(v);
                }
                r.prices = [...new Set(r.prices)].slice(0,15);
                // Availability
                r.availability = null;
                const aSels = ['[class*="stock"]', '[class*="availability"]', '[data-pl="stock"]',
                               '[class*="pdp-stock"]', '[class*="inventory"]', '[class*="Stock"]'];
                for (const sel of aSels) {
                    try {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.textContent||'').trim().toLowerCase();
                            if (t.length < 100) {
                                if (t.includes('in stock')||t.includes('disponib')||t.includes('available')||t.includes('magazzino')||t.includes('in stock')){r.availability='IN_STOCK';break;}
                                if (t.includes('out of stock')||t.includes('non disponib')||t.includes('unavailable')||t.includes('esaurito')){r.availability='OUT_OF_STOCK';break;}
                            }
                        }
                    } catch(e) {}
                    if (r.availability) break;
                }
                // Images
                r.images = [];
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
                    if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
                        && !src.includes('placeholder') && !src.includes('logo')
                        && !src.includes('icon') && !src.includes('blank') && !src.includes('spacer')
                        && !src.includes('.svg') && !src.includes('.gif')
                        && (src.includes('/kf/')||src.includes('s.alicdn')||src.includes('ae0')||src.includes('aliexpress-media'))) {
                        r.images.push(src);
                    }
                }
                r.images = [...new Set(r.images)].slice(0,20);
                // Category
                const bc = document.querySelector('[class*="breadcrumb"], [class*="Breadcrumb"], nav[aria-label*="readcrumb"], [class*="breadcrumb"]');
                r.category = bc ? bc.textContent.trim().substring(0,300) : null;
                // Meta tags
                r.meta = {};
                const metas = document.querySelectorAll('meta[property], meta[name]');
                for (const m of metas) {
                    const prop = m.getAttribute('property') || m.getAttribute('name') || '';
                    const content = m.getAttribute('content') || '';
                    if (prop.includes('og:') || prop.includes('product') || prop.includes('price') || prop.includes('twitter:')) {
                        r.meta[prop] = content.substring(0,300);
                    }
                }
                // JSON-LD
                r.jsonld = [];
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                for (const s of scripts) {
                    try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                }
                // JS State (deep)
                r.jsState = {};
                try { if (window.__INIT_DATA_STATE__) r.jsState.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0,12000); } catch(e){}
                try { if (window.runParams) r.jsState.runParams = JSON.stringify(window.runParams).substring(0,8000); } catch(e){}
                try { if (window.__NEXT_DATA__) r.jsState.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0,12000); } catch(e){}
                // Also: window._dida_config, window.__dida_config
                try { if (window._dida_config) r.jsState.didaConfig = JSON.stringify(window._dida_config).substring(0,5000); } catch(e){}
                try { if (window.__dida_config) r.jsState.didaConfig2 = JSON.stringify(window.__dida_config).substring(0,5000); } catch(e){}
                r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                return r;
            }
        """)
        print(f"  title={str(dom_data.get('title',''))[:80]}")
        print(f"  prices={dom_data.get('prices',[])}")
        print(f"  avail={dom_data.get('availability')} images={len(dom_data.get('images',[]))} body={dom_data.get('bodyLen')}")
        print(f"  meta keys={list(dom_data.get('meta',{}).keys())}")
        print(f"  jsonld={len(dom_data.get('jsonld',[]))} jsState={list(dom_data.get('jsState',{}).keys())}")

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

    # ── 7. MTOP PDP PARSING (ALL responses) ──
    print(f"\n[7] MTOP PDP ({len(mtop_pdp_bodies)} responses)...")
    mtop_parsed = []
    for idx, mr in enumerate(mtop_pdp_bodies):
        body = mr["body"].strip()
        m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
        if m:
            body = m.group(1)
        try:
            d = json.loads(body)
            ret = d.get("ret", [])
            data = d.get("data", {})
            ds = json.dumps(data, ensure_ascii=False) if data else ""
            print(f"  [{idx}] ret={ret[:2]} dataLen={len(ds)}")
            mtop_parsed.append({"idx": idx, "ret": ret, "dataLen": len(ds), "data_keys": list(data.keys()) if isinstance(data, dict) else []})

            # Save each SUCCESS response
            if any("SUCCESS" in str(r) for r in ret) and len(ds) > 200:
                fname = MTOP_RAW_FILE.replace(".json", f"_{idx}.json")
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                print(f"  Saved SUCCESS MTOP: {fname}")

                # Deep parse
                price_found = False
                # Structured paths
                struct_paths = [
                    ("result", "GLOBAL_DATA", "globalData", "price", "minPrice"),
                    ("result", "GLOBAL_DATA", "globalData", "price", "salePrice"),
                    ("result", "GLOBAL_DATA", "globalData", "price", "actvPrice"),
                    ("result", "GLOBAL_DATA", "globalData", "price", "displayPrice"),
                    ("result", "GLOBAL_DATA", "globalData", "price", "formattedPrice"),
                    ("result", "GLOBAL_DATA", "globalData", "price", "amount"),
                    ("result", "GLOBAL_DATA", "globalData", "priceInfo", "minPrice"),
                    ("result", "GLOBAL_DATA", "globalData", "priceInfo", "salePrice"),
                    ("result", "GLOBAL_DATA", "globalData", "skuPrice", "minPrice"),
                    ("result", "GLOBAL_DATA", "globalData", "skuPrice", "salePrice"),
                    ("result", "price", "minPrice"),
                    ("result", "price", "salePrice"),
                    ("price", "minPrice"),
                    ("price", "salePrice"),
                    ("price", "actvPrice"),
                    ("price", "displayPrice"),
                ]
                for path in struct_paths:
                    val = data
                    for key in path:
                        if isinstance(val, dict):
                            val = val.get(key)
                        else:
                            val = None
                            break
                    if val is not None and isinstance(val, (int, float, str)) and str(val) and any(c.isdigit() for c in str(val)):
                        result["price"] = str(val)
                        price_found = True
                        result["method"] = f"MTOP_{'.'.join(path)}"
                        print(f"  PRICE: {val} via {'.'.join(path)}")
                        break

                # Regex on full JSON
                if not price_found:
                    pm = re.search(r'"(?:minPrice|salePrice|actvPrice|displayPrice|formattedPrice|price|amount)"\s*:\s*"?([\d.]+)"?', ds)
                    if pm:
                        result["price"] = pm.group(1)
                        price_found = True
                        result["method"] = "MTOP_REGEX"
                        print(f"  PRICE regex: {pm.group(1)}")

                # Currency
                cm = re.search(r'"(?:currency|priceCurrency|currencyCode)"\s*:\s*"([^"]+)"', ds)
                if cm:
                    result["currency"] = cm.group(1).upper()
                elif not result.get("currency"):
                    result["currency"] = "EUR"

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
        except Exception as e:
            print(f"  [{idx}] parse err: {e}")

    # ── 8. JS STATE FALLBACK ──
    if not result["price"] and dom_data.get("jsState"):
        print("\n[8] JS STATE...")
        for k, v in dom_data["jsState"].items():
            if not v or v == "{}":
                continue
            pm = re.search(r'"(?:minPrice|salePrice|price|amount|actvPrice|displayPrice|formattedPrice)"\s*:\s*"?([\d.]+)"?', v)
            if pm:
                result["price"] = pm.group(1)
                result["currency"] = result.get("currency") or "EUR"
                result["method"] = f"JS_{k}"
                print(f"  PRICE from {k}: {pm.group(1)}")
                break

    # ── 9. META FALLBACK ──
    if dom_data.get("meta"):
        meta = dom_data["meta"]
        if not result["title"] and meta.get("og:title"):
            result["title"] = meta["og:title"].replace(" - AliExpress", "").replace(" | AliExpress", "").strip()
            result["method"] = result.get("method") or "META"
            print(f"\n[9] META title: {result['title'][:80]}")
        if not result["images"] and meta.get("og:image"):
            result["images"] = [meta["og:image"]]
            print(f"  META image: {meta['og:image'][:100]}")
        # og:price
        if not result["price"]:
            for mk, mv in meta.items():
                if "price" in mk.lower() and mv and any(c.isdigit() for c in mv):
                    result["price"] = mv.strip()
                    result["method"] = result.get("method") or "META_PRICE"
                    print(f"  META price: {mv}")
                    break
        if not result["currency"]:
            for mk, mv in meta.items():
                if "currency" in mk.lower() and mv:
                    result["currency"] = mv.strip().upper()
                    break

    # ── 10. JSON-LD ──
    if not result["price"] and dom_data.get("jsonld"):
        print("\n[10] JSON-LD...")
        for ld in dom_data["jsonld"]:
            if isinstance(ld, dict) and ld.get("@type") in ("Product", ["Product"]):
                offers = ld.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                p = offers.get("price") or offers.get("lowPrice")
                if p:
                    result["price"] = str(p)
                    result["currency"] = offers.get("priceCurrency", "EUR")
                    result["method"] = "JSON-LD"
                    print(f"  PRICE: {p} {result['currency']}")
                if not result["title"] and ld.get("name"):
                    result["title"] = ld["name"]
                break

    # ── Screenshot ──
    try:
        page.screenshot(path="diag_ali_pw12.png", full_page=False)
        print(f"\nScreenshot saved")
    except Exception:
        pass

    # Save all MTOP
    try:
        with open("diag_ali_pw12_all_mtop.json", "w", encoding="utf-8") as f:
            json.dump(all_mtop, f, ensure_ascii=False, indent=2)
        print(f"All MTOP: {len(all_mtop)} entries")
    except Exception:
        pass

    ctx.close()

result["elapsed"] = time.monotonic() - t0

print(f"\n{'='*60}")
print(f"RESULT:")
print(f"  ID:         {result['listing_id']}")
print(f"  URL:        {result['url']}")
print(f"  Title:      {str(result['title'])[:80]}")
print(f"  Price:      {result['price']} {result.get('currency','')}")
print(f"  Avail:      {result['availability']}")
print(f"  Images:     {len(result['images'])}")
for img in result["images"][:5]:
    print(f"    {str(img)[:120]}")
print(f"  Category:   {str(result['category'])[:80]}")
print(f"  Method:     {result['method']}")
print(f"  TMD:        {result['tmd_detected']}")
print(f"  MTOP:       success={result['mtop_pdp_success']} len={result['mtop_pdp_len']}")
print(f"  DOM:        rendered={result['dom_rendered']}")
print(f"  Elapsed:    {result['elapsed']:.1f}s")
print(f"  Errors:     {result['errors']}")
print(f"{'='*60}")

with open(RESULT_FILE, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Saved: {RESULT_FILE}")
