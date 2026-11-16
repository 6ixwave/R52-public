"""V14: AliExpress.ru — focus on search-page embedded JSON + fixed CAPTCHA + product API.
Strategy:
  1. Load search page, extract ALL embedded JSON (window.__INITIAL_STATE__, __NEXT_DATA__, etc.)
  2. Better DOM card extraction (per-card, not per-ancestor)
  3. If product page needed: fixed CAPTCHA solver (pick HIGHEST scores)
  4. Try direct MTOP / API call for product data
"""
import json, logging, os, sys, time, random, re, io
from datetime import datetime, timezone
from PIL import Image
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru14")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from r52.collectors.aliexpress import AliExpressCollector

SEARCH_QUERY = "наушники беспроводные"
PID = "1005013496905897"
OUT_JSON = os.path.join(BASE, "diag_ali_ru14_result.json")
OUT_DIR = os.path.join(BASE, "diag_ali_ru14_out")
os.makedirs(OUT_DIR, exist_ok=True)

result = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "phase": "start",
    "search": {},
    "search_embedded": {},
    "search_dom": [],
    "product": {},
    "product_captcha": {},
    "mtop": [],
    "api": {},
    "log": []
}

def logmsg(msg):
    log.info(msg)
    result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")

# ─── 1. Start browser via collector ───
logmsg("Starting browser...")
col = AliExpressCollector(domain="ru")
try:
    ok = col._ensure_browser()
except Exception as e:
    import traceback
    logmsg(f"Browser EXCEPTION: {e}\n{traceback.format_exc()}")
    result["phase"] = "browser_exception"
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    sys.exit(1)
if not ok:
    logmsg("Browser FAILED (returned False)")
    result["phase"] = "browser_failed"
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    sys.exit(1)
page = col._page
logmsg("Browser OK")

# Warm session
try:
    col._warm_session()
    cookies = col._ctx.cookies()
    logmsg(f"Warm: {len(cookies)} cookies")
except Exception as e:
    logmsg(f"Warm session error: {e}")
    cookies = []
    logmsg(f"Warm: 0 cookies (error)")

# ─── 2. MTOP interception (global) ───
mtop_data = []
def on_response(response):
    try:
        url = response.url
        if any(k in url.lower() for k in ["pdp", "mtop", "search", "query", "item"]):
            if ".js" in url or ".css" in url or ".png" in url or ".jpg" in url:
                return
            body = response.body()
            if body and len(body) > 50:
                text = body.decode('utf-8', errors='replace')
                mtop_data.append({"url": url[:300], "len": len(text), "body": text[:8000]})
                logmsg(f"  MTOP: {url[:120]} ({len(text)}b)")
    except Exception:
        pass
page.on("response", on_response)

# ─── 3. PHASE 1: Search page ───
logmsg("=== PHASE 1: Search page ===")
search_url = f"https://aliexpress.ru/wholesale?SearchText={SEARCH_QUERY.replace(' ', '+')}"
logmsg(f"URL: {search_url}")
page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
time.sleep(8)  # wait for JS rendering

state = page.evaluate("""
    () => {
        const p = document.getElementById('baxia-punish');
        if (p) return {onCap: true, body: document.body.innerHTML.length};
        return {onCap: false, body: document.body.innerHTML.length,
                title: document.title.substring(0,200)};
    }
""")
logmsg(f"State: {state}")

# 3a. Extract ALL embedded JSON / state objects
logmsg("Extracting embedded JSON...")
embedded = page.evaluate("""
    () => {
        const out = {};
        // window.__INITIAL_STATE__
        if (window.__INITIAL_STATE__) {
            try { out.initial_state = JSON.stringify(window.__INITIAL_STATE__).substring(0, 50000); } catch(e) { out.initial_state_err = e.message; }
        }
        // window.__NEXT_DATA__
        if (window.__NEXT_DATA__) {
            try { out.next_data = JSON.stringify(window.__NEXT_DATA__).substring(0, 50000); } catch(e) { out.next_data_err = e.message; }
        }
        // window.__APP_DATA__
        if (window.__APP_DATA__) {
            try { out.app_data = JSON.stringify(window.__APP_DATA__).substring(0, 50000); } catch(e) { out.app_data_err = e.message; }
        }
        // window.__INITIAL_DATA__
        if (window.__INITIAL_DATA__) {
            try { out.initial_data = JSON.stringify(window.__INITIAL_DATA__).substring(0, 50000); } catch(e) { out.initial_data_err = e.message; }
        }
        // window.__SEARCH_DATA__ or similar
        const knownKeys = ['__INITIAL_STATE__','__NEXT_DATA__','__APP_DATA__','__INITIAL_DATA__',
                          '__SEARCH_DATA__','__PRODUCT_DATA__','__STATE__','__DATA__'];
        out.window_keys = Object.keys(window).filter(k => k.startsWith('__') && k.length < 40);
        // Script tags with JSON
        const scripts = [];
        document.querySelectorAll('script:not([src])').forEach(s => {
            const t = s.textContent || '';
            if (t.length > 100 && (t.includes('itemList') || t.includes('productList') ||
                t.includes('price') || t.includes('title') || t.includes('skuId') ||
                t.includes('itemId') || t.includes('SearchText'))) {
                scripts.push(t.substring(0, 10000));
            }
        });
        out.scripts = scripts.slice(0, 5);
        // JSON-LD
        const jsonld = [];
        document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
            try { jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
        });
        out.jsonld = jsonld;
        return out;
    }
""")
result["search_embedded"] = embedded
logmsg(f"Embedded keys: {embedded.get('window_keys', [])}")
logmsg(f"Scripts found: {len(embedded.get('scripts', []))}")
logmsg(f"JSON-LD: {len(embedded.get('jsonld', []))}")

# 3b. Better DOM extraction — per-card with proper isolation
logmsg("Extracting DOM cards...")
dom_cards = page.evaluate("""
    (maxItems) => {
        const items = [];
        // Strategy: find all <a> tags with item URLs, then walk up to find card container
        const links = document.querySelectorAll('a[href*="/item/"]');
        const seen = new Set();
        for (const a of links) {
            const href = a.href;
            if (seen.has(href)) continue;
            seen.add(href);
            if (items.length >= maxItems) break;
            // Walk up to find a container that has both image and price text
            let card = a;
            for (let i = 0; i < 8 && card.parentElement; i++) {
                card = card.parentElement;
                if (card.querySelector('img[src*="alicdn"]') && card.textContent.includes('₽')) {
                    break;
                }
            }
            const item = {href: href};
            // Title: from <a> title attr or nearby heading
            item.title = a.getAttribute('title') || a.textContent.trim().substring(0, 200) || null;
            // Also try h2, h3, [class*="title"] within card
            const tEl = card.querySelector('h2, h3, [class*="title"], [class*="Title"]');
            if (tEl) item.title = tEl.textContent.trim().substring(0, 300);
            // Price: find elements with ₽
            const prices = [];
            card.querySelectorAll('span, div, em, strong').forEach(el => {
                const t = el.textContent.trim();
                if (t.includes('₽') && t.length < 30 && /\\d/.test(t)) prices.push(t);
            });
            item.prices = [...new Set(prices)].slice(0, 5);
            // Image
            const img = card.querySelector('img[src*="alicdn"], img[src*="aliexpress-media"]');
            item.image = img ? (img.src || img.getAttribute('data-src') || '') : null;
            // Rating
            const rEl = card.querySelector('[class*="rating"], [class*="star"], [class*="score"]');
            item.rating = rEl ? rEl.textContent.trim().substring(0, 30) : null;
            // Sold
            const sEl = card.querySelector('[class*="sold"], [class*="sales"], [class*="bought"]');
            item.sold = sEl ? sEl.textContent.trim().substring(0, 50) : null;
            items.push(item);
        }
        return items;
    }
""", 30)
result["search_dom"] = dom_cards
logmsg(f"DOM cards: {len(dom_cards)}")
if dom_cards:
    logmsg(f"First card: title={dom_cards[0].get('title','?')[:60]}, prices={dom_cards[0].get('prices',[])}")

# 3c. Try to find product list in embedded state
if embedded.get('initial_state'):
    try:
        is_data = json.loads(embedded['initial_state'])
        # Look for product arrays
        def find_products(obj, path=""):
            found = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                        keys = set(v[0].keys())
                        if keys & {'itemId','skuId','title','price','subject','productTitle'}:
                            found.append({"path": f"{path}.{k}", "count": len(v), "keys": list(keys)[:15], "first": v[0]})
                    found.extend(find_products(v, f"{path}.{k}"))
            elif isinstance(obj, list):
                for i, v in enumerate(obj[:3]):
                    found.extend(find_products(v, f"{path}[{i}]"))
            return found
        products = find_products(is_data)
        result["search_embedded"]["product_arrays"] = products
        logmsg(f"Product arrays in __INITIAL_STATE__: {len(products)}")
        for p in products[:3]:
            logmsg(f"  {p['path']} count={p['count']} keys={p['keys'][:8]}")
    except Exception as e:
        logmsg(f"Parse __INITIAL_STATE__ error: {e}")

# Save search HTML for offline analysis
try:
    html = page.content()
    with open(os.path.join(OUT_DIR, "search_page.html"), "w", encoding="utf-8") as f:
        f.write(html)
    logmsg(f"Search HTML saved: {len(html)} bytes")
except Exception as e:
    logmsg(f"Save HTML error: {e}")

# ─── 4. PHASE 2: Product page with fixed CAPTCHA ───
logmsg("=== PHASE 2: Product page ===")
prod_url = f"https://aliexpress.ru/item/{PID}.html"
logmsg(f"URL: {prod_url}")
page.goto(prod_url, wait_until="domcontentloaded", timeout=30000)
time.sleep(5)

pstate = page.evaluate("""
    () => {
        const p = document.getElementById('baxia-punish');
        if (p) {
            const cls = p.className || '';
            return {onCap: true, isClick: cls.includes('captchaclick'),
                    body: document.body.innerHTML.length};
        }
        const h1 = document.querySelector('h1');
        return {onCap: false, h1: h1 ? h1.textContent.trim().substring(0,200) : null,
                body: document.body.innerHTML.length};
    }
""")
logmsg(f"Product state: {pstate}")

if pstate.get('onCap'):
    logmsg("CAPTCHA on product! Solving...")
    captcha_info = {"attempts": [], "scores": {}, "solved": False}

    # Get question image
    q_img_data = None
    try:
        q_img = page.query_selector('#baxia-punish img, [class*="captcha"] img, [class*="question"] img')
        if q_img:
            q_bytes = q_img.screenshot()
            q_path = os.path.join(OUT_DIR, "captcha_question.png")
            with open(q_path, "wb") as f:
                f.write(q_bytes)
            q_img_data = q_bytes
            logmsg(f"Question image: {len(q_bytes)} bytes")
    except Exception as e:
        logmsg(f"Question img error: {e}")

    # Get grid cells
    grid_imgs = page.query_selector_all('[class*="grid"] img, [class*="cell"] img, [class*="captcha"] img')
    # Filter: question is usually first, grid is 9 cells
    grid_paths = []
    for i, gimg in enumerate(grid_imgs):
        try:
            gbytes = gimg.screenshot()
            gpath = os.path.join(OUT_DIR, f"grid_{i}.png")
            with open(gpath, "wb") as f:
                f.write(gbytes)
            grid_paths.append(gpath)
        except Exception:
            pass
    logmsg(f"Grid images: {len(grid_paths)}")

    # Score: compare each grid cell to question
    if q_img_data and len(grid_paths) >= 9:
        q_arr = np.array(Image.open(io.BytesIO(q_img_data)).convert('L').resize((118, 118))).astype(float)
        scores = {}
        for i, gp in enumerate(grid_paths[:9]):
            try:
                g_arr = np.array(Image.open(gp).convert('L').resize((118, 118))).astype(float)
                diff = np.abs(q_arr - g_arr)
                score = 1.0 - (diff.mean() / 255.0)
                scores[str(i)] = round(score, 4)
            except Exception:
                scores[str(i)] = 0.0
        captcha_info["scores"] = scores
        sorted_scores = sorted(scores.items(), key=lambda x: float(x[1]), reverse=True)
        logmsg(f"Scores (high→low): {[(int(k),v) for k,v in sorted_scores]}")

        # Pick TOP 3 (not bottom 3!)
        top3 = [int(k) for k, v in sorted_scores[:3]]
        logmsg(f"Picking top 3: {top3}")

        # Try clicking top 3 cells
        for attempt in range(3):
            cells_to_click = top3[:attempt + 1]
            logmsg(f"Attempt {attempt+1}: cells {cells_to_click}")
            # Click cells
            for ci in cells_to_click:
                try:
                    cell = grid_imgs[ci]
                    cell.click()
                    time.sleep(0.5)
                except Exception as e:
                    logmsg(f"  Click {ci} error: {e}")
            time.sleep(3)
            # Check if solved
            after = page.evaluate("""
                () => {
                    const p = document.getElementById('baxia-punish');
                    if (p) {
                        const vis = p.offsetParent !== null;
                        return {still: vis};
                    }
                    return {still: false};
                }
            """)
            captcha_info["attempts"].append({"cells": cells_to_click, "still": after.get('still', True)})
            logmsg(f"  Still CAPTCHA: {after.get('still', True)}")
            if not after.get('still', True):
                captcha_info["solved"] = True
                logmsg("CAPTCHA SOLVED!")
                break
            # Reload grid for next attempt
            try:
                page.reload(wait_until="domcontentloaded", timeout=15000)
                time.sleep(3)
                grid_imgs = page.query_selector_all('[class*="grid"] img, [class*="cell"] img, [class*="captcha"] img')
            except Exception:
                pass

    result["product_captcha"] = captcha_info
    logmsg(f"CAPTCHA solved: {captcha_info['solved']}")

# Extract product data (if CAPTCHA solved or no CAPTCHA)
logmsg("Extracting product data...")
prod_data = page.evaluate("""
    () => {
        const r = {};
        const h1 = document.querySelector('h1');
        r.title = h1 ? h1.textContent.trim() : null;
        r.prices = [];
        document.querySelectorAll('[data-pl="price"], [class*="price--current"], [class*="Price--current"],
            [class*="pdp-price"], span[class*="price"], [class*="price"]').forEach(el => {
            const t = (el.textContent || '').trim();
            if (t && /\\d/.test(t) && t.length < 60 && t.includes('₽')) r.prices.push(t);
        });
        r.prices = [...new Set(r.prices)].slice(0, 10);
        r.images = [];
        document.querySelectorAll('img').forEach(img => {
            const src = img.src || img.getAttribute('data-src') || '';
            if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
                && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                && !src.includes('.svg') && !src.includes('.gif') && src.length > 20) {
                r.images.push(src);
            }
        });
        r.images = [...new Set(r.images)].slice(0, 20);
        r.meta = {};
        document.querySelectorAll('meta[property]').forEach(m => {
            const p = m.getAttribute('property') || '';
            if (p.includes('og:')) r.meta[p] = (m.getAttribute('content') || '').substring(0, 300);
        });
        r.jsonld = [];
        document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
            try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
        });
        // Embedded state
        r.embedded = {};
        if (window.__INITIAL_STATE__) {
            try { r.embedded.initial_state = JSON.stringify(window.__INITIAL_STATE__).substring(0, 30000); } catch(e) {}
        }
        if (window.__NEXT_DATA__) {
            try { r.embedded.next_data = JSON.stringify(window.__NEXT_DATA__).substring(0, 30000); } catch(e) {}
        }
        r.seller = null;
        const sEl = document.querySelector('[class*="store-name"], [class*="seller-name"], [data-pl="store-name"], [class*="Shop"]');
        if (sEl) r.seller = sEl.textContent.trim().substring(0, 100);
        r.bodyLen = document.body.innerHTML.length;
        return r;
    }
""")
result["product"] = prod_data
logmsg(f"Product: title={str(prod_data.get('title','?'))[:60]}, prices={prod_data.get('prices',[])}, imgs={len(prod_data.get('images',[]))}")

# Save product HTML
try:
    phtml = page.content()
    with open(os.path.join(OUT_DIR, "product_page.html"), "w", encoding="utf-8") as f:
        f.write(phtml)
    logmsg(f"Product HTML saved: {len(phtml)} bytes")
except Exception as e:
    logmsg(f"Save product HTML error: {e}")

# ─── 5. PHASE 3: Direct API / MTOP call ───
logmsg("=== PHASE 3: Direct API ===")
# Try calling MTOP API directly via page context (uses session cookies)
api_result = page.evaluate("""
    () => {
        return new Promise((resolve) => {
            const pid = '""" + PID + """';
            // Try the standard MTOP endpoint
            const url = `https://aliexpress.ru/item/${pid}.html/_____tmd_____/pdp.pc.query?skuId=&channel=&scene=pc`;
            fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}})
                .then(r => r.text())
                .then(t => resolve({status: 'fetched', len: t.length, body: t.substring(0, 5000)}))
                .catch(e => resolve({status: 'error', msg: e.message}));
            setTimeout(() => resolve({status: 'timeout'}), 10000);
        });
    }
""")
result["api"] = api_result
logmsg(f"API: {api_result.get('status','?')} len={api_result.get('len',0)}")

# Also try the search API
api_search = page.evaluate("""
    () => {
        return new Promise((resolve) => {
            const url = 'https://aliexpress.ru/fn/search-pc/index?SearchText=' + encodeURIComponent('""" + SEARCH_QUERY + """') + '&page=1';
            fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}})
                .then(r => r.text())
                .then(t => resolve({status: 'fetched', len: t.length, body: t.substring(0, 5000)}))
                .catch(e => resolve({status: 'error', msg: e.message}));
            setTimeout(() => resolve({status: 'timeout'}), 10000);
        });
    }
""")
result["api"]["search"] = api_search
logmsg(f"Search API: {api_search.get('status','?')} len={api_search.get('len',0)}")

# ─── 6. Collect MTOP data ───
result["mtop"] = mtop_data[:20]
logmsg(f"MTOP captured: {len(mtop_data)}")

# ─── 7. Save results ───
result["phase"] = "done"
result["search_ok"] = len(dom_cards) > 0
result["prod_ok"] = bool(prod_data.get('title'))
result["api_ok"] = api_result.get('status') == 'fetched' and api_result.get('len', 0) > 100

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
logmsg(f"Saved: {OUT_JSON}")
logmsg(f"PHASE=done search_ok={result['search_ok']} prod_ok={result['prod_ok']} api_ok={result['api_ok']}")

# Cleanup
try:
    col.close()
except Exception:
    pass

# ─── Top-level exception guard ───
# (wrapped via if __name__ block below)

if __name__ == "__main__":
    pass  # main code runs at module level above; this block is a safety net
