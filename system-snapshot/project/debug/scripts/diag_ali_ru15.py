"""V15: AliExpress.ru — search API (aer-webapi) + product page.
Focus: parse the 1.2MB JSON from aer-webapi/v1/search, then product page.
Proper main(), file logging, robust JS."""
import json, logging, os, sys, time, io, random, re
from datetime import datetime, timezone
from pathlib import Path

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# ─── Logging: console + file ───
LOG_FILE = os.path.join(BASE, "diag_ali_ru15_run.log")
log = logging.getLogger("diag_ali_ru15")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
log.addHandler(_sh)
_fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
_fh.setFormatter(_fmt)
log.addHandler(_fh)

OUT_JSON = os.path.join(BASE, "diag_ali_ru15_result.json")
OUT_DIR = os.path.join(BASE, "diag_ali_ru15_out")
os.makedirs(OUT_DIR, exist_ok=True)

SEARCH_QUERY = "наушники беспроводные"
PID = "1005013496905897"

result = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "phase": "start",
    "search_api": {},
    "search_dom": [],
    "product": {},
    "product_captcha": {},
    "mtop": [],
    "log": []
}

def L(msg):
    log.info(msg)
    result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")

def main():
    from r52.collectors.aliexpress import AliExpressCollector

    L("V15 starting...")
    col = AliExpressCollector(domain="ru")
    try:
        ok = col._ensure_browser()
        if not ok:
            L("Browser FAILED")
            result["phase"] = "browser_failed"
            return
        L("Browser OK")
        col._warm_session()
        cookies = col._ctx.cookies()
        L(f"Warm: {len(cookies)} cookies")
        page = col._page

        # ─── MTOP / API interception ───
        api_data = []
        def on_response(response):
            try:
                url = response.url
                # Target: aer-webapi, aer-jsonapi, mtop, pdp
                if any(k in url.lower() for k in ["aer-webapi", "aer-jsonapi", "mtop", "pdp.pc"]):
                    if any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff"]):
                        return
                    body = response.body()
                    if body and len(body) > 100:
                        text = body.decode('utf-8', errors='replace')
                        api_data.append({"url": url[:300], "len": len(text), "body": text})
                        L(f"  API: {url[:100]} ({len(text)}b)")
            except Exception:
                pass
        page.on("response", on_response)

        # ─── PHASE 1: Search page ───
        L("=== PHASE 1: Search ===")
        search_url = f"https://aliexpress.ru/wholesale?SearchText={SEARCH_QUERY.replace(' ', '+')}"
        L(f"URL: {search_url}")
        api_data.clear()  # reset for search phase
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(10)  # wait for full JS render + API calls

        st = page.evaluate("""() => {
            const p = document.getElementById('baxia-punish');
            if (p) return {onCap: true, body: document.body.innerHTML.length};
            return {onCap: false, body: document.body.innerHTML.length, title: document.title.substring(0,200)};
        }""")
        L(f"Search state: {st}")

        # Parse search API response
        search_api_json = None
        for d in api_data:
            if "aer-webapi" in d["url"] and "search" in d["url"].lower():
                try:
                    search_api_json = json.loads(d["body"])
                    L(f"Search API parsed: {len(d['body'])} bytes")
                    break
                except Exception as e:
                    L(f"Search API parse error: {e}")
        if search_api_json:
            result["search_api"] = {"size": len(json.dumps(search_api_json)), "keys": list(search_api_json.keys())[:20]}
            # Find product arrays
            def find_items(obj, path="", depth=0):
                if depth > 6:
                    return []
                found = []
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, list) and len(v) > 2:
                            first = v[0] if v else {}
                            if isinstance(first, dict):
                                keys = set(first.keys())
                                if keys & {"itemId", "skuId", "title", "price", "subject", "productTitle", "minPrice", "maxPrice"}:
                                    found.append({"path": f"{path}.{k}", "count": len(v), "keys": sorted(keys)[:20], "first": first})
                        found.extend(find_items(v, f"{path}.{k}", depth+1))
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:5]):
                        found.extend(find_items(v, f"{path}[{i}]", depth+1))
                return found
            items_found = find_items(search_api_json)
            result["search_api"]["item_arrays"] = items_found[:10]
            for ia in items_found[:5]:
                L(f"  Array: {ia['path']} count={ia['count']} keys={ia['keys'][:10]}")
            # Save full API JSON for offline analysis
            with open(os.path.join(OUT_DIR, "search_api.json"), "w", encoding="utf-8") as f:
                json.dump(search_api_json, f, ensure_ascii=False, indent=2)
            L(f"Search API saved to search_api.json")

        # DOM fallback: extract cards
        L("DOM card extraction...")
        dom_cards = page.evaluate("""(maxItems) => {
            const items = [];
            const links = document.querySelectorAll('a[href*="/item/"]');
            const seen = new Set();
            for (const a of links) {
                const href = a.href;
                if (seen.has(href) || items.length >= maxItems) continue;
                seen.add(href);
                let card = a;
                for (let i = 0; i < 8 && card.parentElement; i++) {
                    card = card.parentElement;
                    if (card.querySelector('img[src*="alicdn"]') && card.textContent.includes('\\u20bd')) break;
                }
                const item = {href: href};
                const tEl = card.querySelector('h2, h3, [class*="title"], [class*="Title"]');
                item.title = tEl ? tEl.textContent.trim().substring(0, 300) : (a.getAttribute('title') || a.textContent.trim().substring(0, 200));
                const prices = [];
                card.querySelectorAll('span, div, em, strong').forEach(el => {
                    const t = el.textContent.trim();
                    if (t.includes('\\u20bd') && t.length < 30 && /\\d/.test(t)) prices.push(t);
                });
                item.prices = [...new Set(prices)].slice(0, 5);
                const img = card.querySelector('img[src*="alicdn"], img[src*="aliexpress-media"]');
                item.image = img ? (img.src || img.getAttribute('data-src') || '') : null;
                items.push(item);
            }
            return items;
        }""", 30)
        result["search_dom"] = dom_cards
        L(f"DOM cards: {len(dom_cards)}")
        if dom_cards:
            c0 = dom_cards[0]
            L(f"  First: title={str(c0.get('title','?'))[:60]} prices={c0.get('prices',[])}")

        # Save search HTML
        try:
            html = page.content()
            with open(os.path.join(OUT_DIR, "search_page.html"), "w", encoding="utf-8") as f:
                f.write(html)
            L(f"Search HTML: {len(html)}b")
        except Exception as e:
            L(f"Save HTML: {e}")

        # ─── PHASE 2: Product page ───
        L("=== PHASE 2: Product ===")
        prod_url = f"https://aliexpress.ru/item/{PID}.html"
        L(f"URL: {prod_url}")
        api_data.clear()
        page.goto(prod_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)

        pst = page.evaluate("""() => {
            const p = document.getElementById('baxia-punish');
            if (p) {
                const cls = p.className || '';
                return {onCap: true, isClick: cls.includes('captchaclick'), body: document.body.innerHTML.length};
            }
            const h1 = document.querySelector('h1');
            return {onCap: false, h1: h1 ? h1.textContent.trim().substring(0,200) : null, body: document.body.innerHTML.length};
        }""")
        L(f"Product state: {pst}")

        captcha_solved = True  # default: no captcha
        if pst.get('onCap'):
            L("CAPTCHA on product! Waiting 15s (might be intermittent)...")
            time.sleep(15)
            pst2 = page.evaluate("""() => {
                const p = document.getElementById('baxia-punish');
                if (p) return {onCap: true};
                return {onCap: false};
            }""")
            if pst2.get('onCap'):
                L("CAPTCHA still present, trying solve...")
                # Simple CAPTCHA attempt: click highest-score cells
                try:
                    from PIL import Image
                    import numpy as np
                    q_el = page.query_selector('#click-question-canvas')
                    grid_imgs = page.query_selector_all('#click-grid-0, #click-grid-1, #click-grid-2, #click-grid-3, #click-grid-4, #click-grid-5, #click-grid-6, #click-grid-7, #click-grid-8')
                    if q_el and len(grid_imgs) >= 9:
                        q_bytes = q_el.screenshot()
                        q_arr = np.array(Image.open(io.BytesIO(q_bytes)).convert('L').resize((64,64))).astype(float)
                        scores = []
                        for i in range(9):
                            g_bytes = grid_imgs[i].screenshot()
                            g_arr = np.array(Image.open(io.BytesIO(g_bytes)).convert('L').resize((64,64))).astype(float)
                            qn = (q_arr - q_arr.mean()) / (q_arr.std() + 1e-8)
                            gn = (g_arr - g_arr.mean()) / (g_arr.std() + 1e-8)
                            scores.append((i, float(np.mean(qn * gn))))
                        scores.sort(key=lambda x: x[1], reverse=True)
                        L(f"CAPTCHA scores: {[(i, round(s,3)) for i,s in scores]}")
                        top3 = [i for i, _ in scores[:3]]
                        L(f"Clicking top3: {top3}")
                        for ci in top3:
                            grid_imgs[ci].click()
                            time.sleep(0.5)
                        time.sleep(2)
                        sub = page.query_selector('#click-submit')
                        if sub:
                            sub.click()
                        time.sleep(5)
                        pst3 = page.evaluate("() => { const p = document.getElementById('baxia-punish'); return p ? {onCap:true} : {onCap:false}; }")
                        captcha_solved = not pst3.get('onCap')
                        L(f"CAPTCHA solved: {captcha_solved}")
                except Exception as e:
                    L(f"CAPTCHA solve error: {e}")
                    captcha_solved = False
            else:
                L("CAPTCHA disappeared on its own!")
        else:
            L("No CAPTCHA on product!")

        # Extract product data (simplified JS to avoid syntax errors)
        L("Extracting product data...")
        try:
            prod_data = page.evaluate("""() => {
                var r = {};
                var h1 = document.querySelector('h1');
                r.title = h1 ? h1.textContent.trim() : null;
                r.prices = [];
                var priceEls = document.querySelectorAll('[class*="price"], [class*="Price"]');
                for (var i = 0; i < priceEls.length && i < 50; i++) {
                    var t = (priceEls[i].textContent || '').trim();
                    if (t.length > 0 && t.length < 40 && t.indexOf('\\u20bd') >= 0) {
                        if (r.prices.indexOf(t) < 0) r.prices.push(t);
                    }
                }
                r.prices = r.prices.slice(0, 10);
                r.images = [];
                var imgs = document.querySelectorAll('img');
                for (var j = 0; j < imgs.length; j++) {
                    var src = imgs[j].src || imgs[j].getAttribute('data-src') || '';
                    if (src.indexOf('alicdn') >= 0 && src.indexOf('placeholder') < 0 && src.indexOf('logo') < 0 && src.indexOf('.svg') < 0 && src.length > 20) {
                        if (r.images.indexOf(src) < 0) r.images.push(src);
                    }
                    if (r.images.length >= 20) break;
                }
                r.meta = {};
                var metas = document.querySelectorAll('meta[property]');
                for (var k = 0; k < metas.length; k++) {
                    var p = metas[k].getAttribute('property') || '';
                    if (p.indexOf('og:') >= 0) r.meta[p] = (metas[k].getAttribute('content') || '').substring(0, 300);
                }
                r.jsonld = [];
                var scripts = document.querySelectorAll('script[type="application/ld+json"]');
                for (var m = 0; m < scripts.length; m++) {
                    try { r.jsonld.push(JSON.parse(scripts[m].textContent)); } catch(e) {}
                }
                r.seller = null;
                var sEl = document.querySelector('[class*="store-name"], [class*="seller-name"], [data-pl="store-name"]');
                if (sEl) r.seller = sEl.textContent.trim().substring(0, 100);
                r.bodyLen = document.body.innerHTML.length;
                return r;
            }""")
            result["product"] = prod_data
            L(f"Product: title={str(prod_data.get('title','?'))[:60]}")
            L(f"  prices={prod_data.get('prices',[])}")
            L(f"  images={len(prod_data.get('images',[]))} jsonld={len(prod_data.get('jsonld',[]))}")
            L(f"  seller={prod_data.get('seller')}")
        except Exception as e:
            L(f"Product extract error: {e}")
            result["product"] = {"error": str(e)}

        # Product API data
        for d in api_data:
            if "pdp" in d["url"].lower() or "item" in d["url"].lower():
                if d["len"] > 500:
                    result["product"]["api"] = {"url": d["url"][:200], "len": d["len"]}
                    try:
                        pjson = json.loads(d["body"])
                        result["product"]["api"]["keys"] = list(pjson.keys())[:15]
                    except Exception:
                        pass
                    break

        # Save product HTML
        try:
            phtml = page.content()
            with open(os.path.join(OUT_DIR, "product_page.html"), "w", encoding="utf-8") as f:
                f.write(phtml)
            L(f"Product HTML: {len(phtml)}b")
        except Exception as e:
            L(f"Save product HTML: {e}")

        # ─── Summary ───
        result["mtop"] = [{"url": d["url"][:200], "len": d["len"]} for d in api_data[:15]]
        result["phase"] = "done"
        result["search_ok"] = len(dom_cards) > 0 or bool(search_api_json)
        result["prod_ok"] = bool(prod_data.get('title')) if isinstance(prod_data, dict) else False
        result["api_ok"] = bool(search_api_json)

        L(f"PHASE=done search_ok={result['search_ok']} prod_ok={result['prod_ok']} api_ok={result['api_ok']}")

    except Exception as e:
        import traceback
        result["phase"] = f"error: {e}"
        result["traceback"] = traceback.format_exc()
        L(f"ERROR: {e}\n{traceback.format_exc()}")
    finally:
        try:
            col.close()
        except Exception:
            pass

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    L(f"Saved: {OUT_JSON}")


if __name__ == "__main__":
    main()
