"""V13: Multi-strategy AliExpress.ru — search page first, then product + MTOP.
Goal: get REAL product data (title, price, images, seller) via any working path."""
import json, logging, os, sys, time, io, random, re
from datetime import datetime, timezone
from PIL import Image
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru13")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from r52.collectors.aliexpress import AliExpressCollector

PID = "1005013496905897"
SEARCH_QUERY = "наушники беспроводные"
OUT_DIR = os.path.join(BASE, "diag_ali_ru13_out")
OUT_JSON = os.path.join(BASE, "diag_ali_ru13_result.json")
os.makedirs(OUT_DIR, exist_ok=True)


def check_page_state(page):
    return page.evaluate("""
        () => {
            const p = document.getElementById('baxia-punish');
            if (p) {
                const cls = p.className || '';
                const msg = document.querySelector('.error-msg, [class*="error"], [class*="result"]');
                return {onCap: true, isClick: cls.includes('captchaclick'),
                        msg: msg ? msg.textContent.trim().substring(0,120) : '',
                        body: document.body.innerHTML.length};
            }
            const h1 = document.querySelector('h1');
            return {onCap: false, h1: h1 ? h1.textContent.trim().substring(0,200) : null,
                    body: document.body.innerHTML.length};
        }
    """)


def extract_search_results(page, max_items=20):
    """Extract product cards from AliExpress search results page."""
    return page.evaluate("""
        (maxItems) => {
            const items = [];
            // Try multiple selectors for product cards
            const selectors = [
                '[data-pl="search-item-card"]',
                '.search-item-card',
                '[class*="Card--"]',
                '[class*="item-card"]',
                '.man-module-root',
                '[class*="search-item"]'
            ];
            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length > 2) break;
            }
            // Fallback: look for any repeated structure with price
            if (cards.length < 2) {
                const all = document.querySelectorAll('[class*="price"], [class*="Price"]');
                const parentSet = new Set();
                all.forEach(el => {
                    let p = el;
                    for (let i = 0; i < 6 && p; i++) {
                        if (p.querySelector && p.querySelector('img[src*="alicdn"]')) {
                            parentSet.add(p);
                            break;
                        }
                        p = p.parentElement;
                    }
                });
                cards = [...parentSet];
            }
            for (let i = 0; i < Math.min(cards.length, maxItems); i++) {
                const card = cards[i];
                const item = {};
                // Title
                const titleEl = card.querySelector('h2, [class*="title"], [class*="Title"], a[title]');
                item.title = titleEl ? titleEl.textContent.trim().substring(0, 300) : null;
                // Price
                const priceEls = card.querySelectorAll('[class*="price"], [class*="Price"]');
                const prices = [];
                priceEls.forEach(el => {
                    const t = el.textContent.trim();
                    if (t && /\\d/.test(t) && t.length < 40) prices.push(t);
                });
                item.prices = [...new Set(prices)].slice(0, 5);
                // Image
                const img = card.querySelector('img[src*="alicdn"], img[src*="aliexpress"]');
                item.image = img ? (img.src || img.getAttribute('data-src') || '') : null;
                // Link
                const link = card.querySelector('a[href*="item"], a[href*="product"]');
                item.url = link ? link.href : null;
                // Seller
                const sellerEl = card.querySelector('[class*="store"], [class*="seller"], [class*="shop"]');
                item.seller = sellerEl ? sellerEl.textContent.trim().substring(0, 100) : null;
                // Rating
                const ratingEl = card.querySelector('[class*="rating"], [class*="star"], [class*="score"]');
                item.rating = ratingEl ? ratingEl.textContent.trim().substring(0, 30) : null;
                // Sold
                const soldEl = card.querySelector('[class*="sold"], [class*="sales"]');
                item.sold = soldEl ? soldEl.textContent.trim().substring(0, 50) : null;
                if (item.title || item.prices.length > 0) {
                    items.push(item);
                }
            }
            // Also try JSON-LD
            const jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            // Meta
            const meta = {};
            document.querySelectorAll('meta[property]').forEach(m => {
                const p = m.getAttribute('property') || '';
                if (p.includes('og:')) meta[p] = (m.getAttribute('content') || '').substring(0, 200);
            });
            return {items, count: items.length, jsonld: jsonld.length, meta,
                    bodyLen: document.body.innerHTML.length,
                    title: document.title.substring(0, 200)};
        }
    """, max_items)


def extract_product_page(page):
    """Extract full product data from a product page."""
    return page.evaluate("""
        () => {
            const r = {};
            const h1 = document.querySelector('h1');
            r.title = h1 ? h1.textContent.trim() : null;
            // Prices
            r.prices = [];
            document.querySelectorAll('[data-pl="price"], [class*="price--current"], [class*="Price--current"], [class*="pdp-price"], span[class*="price"]').forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && /\\d/.test(t) && t.length < 60) r.prices.push(t);
            });
            r.prices = [...new Set(r.prices)].slice(0, 10);
            // Images
            r.images = [];
            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.getAttribute('data-src') || '';
                if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
                    && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                    && !src.includes('.svg') && !src.includes('.gif')) {
                    r.images.push(src);
                }
            });
            r.images = [...new Set(r.images)].slice(0, 20);
            // Meta OG
            r.meta = {};
            document.querySelectorAll('meta[property]').forEach(m => {
                const p = m.getAttribute('property') || '';
                if (p.includes('og:')) r.meta[p] = (m.getAttribute('content') || '').substring(0, 300);
            });
            // JSON-LD
            r.jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            // Seller
            const sellerEl = document.querySelector('[class*="store-name"], [class*="seller-name"], [data-pl="store-name"]');
            r.seller = sellerEl ? sellerEl.textContent.trim().substring(0, 100) : null;
            // SKU / specs
            r.specs = {};
            document.querySelectorAll('[class*="spec"], [class*="attribute"], [class*="param"]').forEach(el => {
                const t = el.textContent.trim();
                if (t && t.length < 200) r.specs[t.substring(0, 50)] = t.substring(0, 200);
            });
            // Delivery
            const delEl = document.querySelector('[class*="delivery"], [class*="shipping"], [class*="logistics"]');
            r.delivery = delEl ? delEl.textContent.trim().substring(0, 200) : null;
            r.bodyLen = document.body.innerHTML.length;
            return r;
        }
    """)


def intercept_mtop(page, api_name="mtop.aliexpress.pdp.pc.query"):
    """Set up MTOP interception and return collected responses."""
    mtop_data = []
    def on_response(response):
        try:
            url = response.url
            if api_name in url or "pdp" in url.lower():
                body = response.body()
                if body and len(body) > 100:
                    text = body.decode('utf-8', errors='replace')
                    mtop_data.append({"url": url[:200], "len": len(text), "body": text[:5000]})
        except Exception:
            pass
    page.on("response", on_response)
    return mtop_data, on_response


def try_solve_captcha_v2(page, out_dir, prefix=""):
    """Improved CAPTCHA solver: analyze question for icon, compare with grid.
    Returns (solved: bool, info: dict)."""
    info = {"attempts": [], "scores": {}}
    page.wait_for_selector('#click-question-canvas', timeout=10000)
    page.wait_for_timeout(2000)

    # Extract question
    q_el = page.query_selector('#click-question-canvas')
    q_bytes = q_el.screenshot() if q_el else b''
    if not q_bytes:
        return False, {"error": "no question image"}
    q_img = Image.open(io.BytesIO(q_bytes)).convert("RGB")
    qw, qh = q_img.size
    q_img.save(os.path.join(out_dir, f"{prefix}question.png"))
    log.info("Question: %dx%d, %d bytes", qw, qh, len(q_bytes))

    # Extract grid
    grid_bytes = []
    grid_imgs = []
    for i in range(9):
        g_el = page.query_selector(f'#click-grid-{i}')
        if g_el:
            gb = g_el.screenshot()
            grid_bytes.append(gb)
            grid_imgs.append(Image.open(io.BytesIO(gb)).convert("RGB"))
            grid_imgs[-1].save(os.path.join(out_dir, f"{prefix}grid_{i}.png"))
        else:
            grid_bytes.append(None)
            grid_imgs.append(None)
    log.info("Grid: %d/9 images", sum(1 for g in grid_bytes if g))

    # Strategy: The question (356x49) likely has text + maybe a small icon on the right
    # Split question into segments and look for a non-text region
    q_arr = np.array(q_img, dtype=np.float64)
    # Find columns with high variance (likely icon/object vs text)
    col_var = q_arr.std(axis=(0, 2))
    # The rightmost 60-80px might be an icon
    icon_region = q_img.crop((qw - 80, 0, qw, qh)) if qw > 100 else q_img
    icon_arr = np.array(icon_region, dtype=np.float64)

    # Compare each grid cell to the icon region AND full question
    scores = []
    for i in range(9):
        if grid_imgs[i] is None:
            scores.append(1.0)
            continue
        g_small = grid_imgs[i].resize((64, 64), Image.LANCZOS)
        g_gray = np.array(g_small.convert("L"), dtype=np.float64)

        # Compare to icon region
        ic_small = icon_region.resize((64, 64), Image.LANCZOS)
        ic_gray = np.array(ic_small.convert("L"), dtype=np.float64)
        ic_norm = (ic_gray - ic_gray.mean()) / (ic_gray.std() + 1e-8)
        g_norm = (g_gray - g_gray.mean()) / (g_gray.std() + 1e-8)
        ncc_icon = float(np.mean(ic_norm * g_norm))

        # Compare to full question (resized)
        q_small = q_img.resize((64, 64), Image.LANCZOS)
        q_gray = np.array(q_small.convert("L"), dtype=np.float64)
        q_norm = (q_gray - q_gray.mean()) / (q_gray.std() + 1e-8)
        ncc_full = float(np.mean(q_norm * g_norm))

        # Edge-based comparison
        from PIL import ImageFilter
        g_edge = np.array(g_small.filter(ImageFilter.FIND_EDGES).convert("L"), dtype=np.float64)
        ic_edge = np.array(ic_small.filter(ImageFilter.FIND_EDGES).convert("L"), dtype=np.float64)
        q_edge = np.array(q_small.filter(ImageFilter.FIND_EDGES).convert("L"), dtype=np.float64)
        g_e_norm = (g_edge - g_edge.mean()) / (g_edge.std() + 1e-8)
        ic_e_norm = (ic_edge - ic_edge.mean()) / (ic_edge.std() + 1e-8)
        q_e_norm = (q_edge - q_edge.mean()) / (q_edge.std() + 1e-8)
        edge_icon = float(np.mean(ic_e_norm * g_e_norm))
        edge_full = float(np.mean(q_e_norm * g_e_norm))

        # Combined: icon NCC weighted higher, plus edge
        score = 0.40 * (1.0 - max(0, min(1, ncc_icon))) + 0.20 * (1.0 - max(0, min(1, ncc_full))) + 0.20 * (1.0 - max(0, min(1, edge_icon))) + 0.20 * (1.0 - max(0, min(1, edge_full)))
        scores.append(score)

    info["scores"] = {str(i): round(s, 4) for i, s in enumerate(scores)}
    sorted_s = sorted(enumerate(scores), key=lambda x: x[1])
    log.info("Scores: %s", [(i, round(s, 3)) for i, s in sorted_s])

    # Try: top-1, top-2, top-3
    top = [x[0] for x in sorted_s]
    attempts = [
        [top[0]],
        [top[0], top[1]],
        [top[0], top[1], top[2]],
    ]

    for att_idx, selected in enumerate(attempts):
        log.info("Attempt %d: %s", att_idx + 1, selected)
        info["attempts"].append({"cells": selected, "status": "pending"})

        # Click
        for idx in selected:
            g_el = page.query_selector(f'#click-grid-{idx}')
            if g_el:
                box = g_el.bounding_box()
                if box:
                    cx = box['x'] + box['width'] / 2 + random.uniform(-3, 3)
                    cy = box['y'] + box['height'] / 2 + random.uniform(-3, 3)
                    page.mouse.click(cx, cy)
                else:
                    g_el.click()
                page.wait_for_timeout(random.randint(300, 500))

        # Submit
        page.wait_for_timeout(500)
        sub = page.query_selector('#click-submit')
        if sub:
            sub.click()
        else:
            page.keyboard.press("Enter")
        page.wait_for_timeout(1000)

        # Wait result
        success = False
        for tick in range(12):
            page.wait_for_timeout(1000)
            try:
                st = check_page_state(page)
            except Exception:
                continue
            if not st.get("onCap"):
                if st.get("body", 0) > 20000:
                    success = True
                    break
            elif st.get("msg") and "неправильн" in st.get("msg", "").lower():
                break

        info["attempts"][-1]["status"] = "success" if success else "timeout"
        if success:
            info["solved_attempt"] = att_idx + 1
            info["solved_cells"] = selected
            return True, info

        # Refresh for next attempt
        refresh = page.query_selector('#click-refresh')
        if refresh:
            refresh.click()
        page.wait_for_timeout(2500)
        # Re-extract
        page.wait_for_selector('#click-question-canvas', timeout=8000)
        page.wait_for_timeout(1500)

    return False, info


def main():
    result = {"pid": PID, "ts": datetime.now(timezone.utc).isoformat(),
              "phase": "start", "log": [], "strategy": "search_first"}

    def logline(msg):
        result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        log.info(msg)

    logline("V13 multi-strategy starting...")
    col = AliExpressCollector(domain="ru")

    try:
        ok = col._ensure_browser()
        if not ok:
            result["phase"] = "browser_failed"
            return
        logline("Browser OK")

        col._warm_session()
        logline(f"Warm: {len(col._ctx.cookies())} cookies")

        # ─── PHASE 1: Search page ───
        logline("=== PHASE 1: Search page ===")
        search_url = f"https://aliexpress.ru/wholesale?SearchText={SEARCH_QUERY.replace(' ', '+')}"
        logline(f"URL: {search_url}")

        mtop_data, mtop_handler = intercept_mtop(col._page)
        resp = col._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        logline(f"HTTP {resp.status if resp else 0}")

        # Wait for content
        search_ok = False
        for tick in range(20):
            col._page.wait_for_timeout(1000)
            try:
                st = check_page_state(col._page)
                logline(f"tick={tick}: {st}")
                if st.get("onCap"):
                    if st.get("isClick"):
                        logline("CAPTCHA on search! Trying to solve...")
                        solved, cap_info = try_solve_captcha_v2(col._page, OUT_DIR, "s_")
                        result["search_captcha"] = cap_info
                        if solved:
                            logline("Search CAPTCHA solved!")
                            col._page.wait_for_timeout(3000)
                            search_ok = True
                        else:
                            logline("Search CAPTCHA NOT solved")
                    break
                if not st.get("onCap") and st.get("body", 0) > 30000:
                    search_ok = True
                    break
            except Exception:
                continue

        if search_ok:
            col._page.wait_for_timeout(3000)
            # Scroll to load more
            for _ in range(3):
                col._page.evaluate("window.scrollBy(0, 800)")
                col._page.wait_for_timeout(1500)

            logline("Extracting search results...")
            search_data = extract_search_results(col._page, 20)
            result["search"] = search_data
            logline(f"Search: {search_data.get('count', 0)} items, body={search_data.get('bodyLen', 0)}b")

            if search_data.get("count", 0) > 0:
                logline(f"First item: {search_data['items'][0].get('title', '')[:80]}")
                logline(f"  prices: {search_data['items'][0].get('prices', [])}")
                logline(f"  image: {str(search_data['items'][0].get('image', ''))[:80]}")
                logline(f"  url: {str(search_data['items'][0].get('url', ''))[:80]}")

                # Save search HTML
                html = col._page.content()
                with open(os.path.join(OUT_DIR, "search_page.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                logline(f"Search HTML: {len(html)}b")

                result["search_success"] = True
            else:
                logline("Search loaded but 0 items extracted — checking DOM...")
                # Try broader extraction
                dom_items = col._page.evaluate("""
                    () => {
                        const items = [];
                        document.querySelectorAll('a[href*="/item/"]').forEach(a => {
                            const card = a.closest('[class*="card"], [class*="item"], [class*="module"]') || a.parentElement;
                            if (card) {
                                const t = card.textContent.trim().substring(0, 200);
                                const img = card.querySelector('img[src*="alicdn"]');
                                items.push({text: t, img: img ? img.src : null, href: a.href});
                            }
                        });
                        return items.slice(0, 20);
                    }
                """)
                result["search_dom_fallback"] = dom_items
                logline(f"DOM fallback: {len(dom_items)} links")

        # ─── PHASE 2: Product page ───
        logline("=== PHASE 2: Product page ===")
        prod_url = col.build_url(PID)
        logline(f"URL: {prod_url}")

        resp2 = col._page.goto(prod_url, wait_until="domcontentloaded", timeout=30000)
        logline(f"HTTP {resp2.status if resp2 else 0}")

        prod_ok = False
        for tick in range(20):
            col._page.wait_for_timeout(1000)
            try:
                st = check_page_state(col._page)
                logline(f"tick={tick}: {st}")
                if st.get("onCap"):
                    if st.get("isClick"):
                        logline("CAPTCHA on product! Trying to solve...")
                        solved, cap_info = try_solve_captcha_v2(col._page, OUT_DIR, "p_")
                        result["product_captcha"] = cap_info
                        if solved:
                            logline("Product CAPTCHA solved!")
                            col._page.wait_for_timeout(3000)
                            prod_ok = True
                        else:
                            logline("Product CAPTCHA NOT solved")
                    break
                if not st.get("onCap") and st.get("body", 0) > 30000:
                    prod_ok = True
                    break
            except Exception:
                continue

        if prod_ok:
            col._page.wait_for_timeout(3000)
            prod_data = extract_product_page(col._page)
            result["product"] = prod_data
            logline(f"Product: title={str(prod_data.get('title', ''))[:60]}")
            logline(f"  prices={prod_data.get('prices', [])}")
            logline(f"  images={len(prod_data.get('images', []))} jsonld={len(prod_data.get('jsonld', []))}")
            logline(f"  seller={prod_data.get('seller')}")

            html = col._page.content()
            with open(os.path.join(OUT_DIR, "product_page.html"), "w", encoding="utf-8") as f:
                f.write(html)
            logline(f"Product HTML: {len(html)}b")
            result["product_success"] = True

        # ─── PHASE 3: MTOP API ───
        logline("=== PHASE 3: MTOP data ===")
        result["mtop_count"] = len(mtop_data)
        if mtop_data:
            result["mtop"] = mtop_data[:5]
            logline(f"MTOP: {len(mtop_data)} responses captured")
            for i, m in enumerate(mtop_data[:3]):
                logline(f"  [{i}] {m['url'][:80]} ({m['len']}b)")
        else:
            logline("MTOP: no responses captured")
            # Try direct MTOP call via page.evaluate
            logline("Trying direct MTOP fetch...")
            mtop_direct = col._page.evaluate("""
                async () => {
                    try {
                        const params = {
                            api: 'mtop.aliexpress.pdp.pc.query',
                            v: '1.0',
                            data: JSON.stringify({productId: '${PID}', channel: 'pc', ext: '{}'}),
                            type: 'jsonp',
                            dataType: 'json'
                        };
                        const qs = new URLSearchParams(params).toString();
                        const url = `https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/?${qs}`;
                        const r = await fetch(url, {credentials: 'include'});
                        const t = await r.text();
                        return {status: r.status, len: t.length, body: t.substring(0, 5000)};
                    } catch(e) {
                        return {error: e.message};
                    }
                }
            """)
            result["mtop_direct"] = mtop_direct
            logline(f"MTOP direct: {mtop_direct.get('status', mtop_direct.get('error'))}")

        col._page.remove_listener("response", mtop_handler)
        result["phase"] = "done"

    except Exception as e:
        import traceback
        result["phase"] = f"error: {e}"
        result["traceback"] = traceback.format_exc()
        logline(f"ERROR: {e}")
    finally:
        col.close()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logline(f"Saved: {OUT_JSON}")
    logline(f"PHASE={result['phase']} search_ok={result.get('search_success')} prod_ok={result.get('product_success')}")


if __name__ == "__main__":
    main()
