#!/usr/bin/env python
"""
pw60: AliExpress PDP via IT-region warm profile.
Strategy:
  1. Reuse pw53 profile (IT session, warm cookies)
  2. Homepage → grab productIds from recommend MTOP
  3. Navigate to product → capture PDP MTOP (mtop.aliexpress.pdp.pc.query)
  4. Full parse: title, price, images, SKU, delivery, category
  5. DOM extraction as backup
  6. TMD handling (non-blocking: PDP usually fires before TMD resolves)
"""

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── Config ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "pw_ali_profile_pw53")  # warm IT profile
OUT_JSON = os.path.join(BASE_DIR, "diag_ali_pw60_result.json")
SCREENSHOT_DIR = BASE_DIR

NAV_TIMEOUT = 45_000
WAIT_AFTER_LOAD = 10_000
TMD_WAIT = 25_000
RELOAD_WAIT = 12_000

result = {
    "timestamp": datetime.now().isoformat(),
    "phase": "init",
    "product_id": None,
    "product_url": None,
    "mtop_responses": [],
    "pdp_raw": None,
    "pdp_parsed": {},
    "dom_data": {},
    "js_state": {},
    "tmd_handled": False,
    "recaptcha_solved": False,
    "errors": [],
    "timings": {},
}

t_start = time.time()


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    elapsed = time.time() - t_start
    print(f"[{ts}] (+{elapsed:.1f}s) {msg}", flush=True)


def safe_body(response):
    try:
        return response.body()
    except Exception:
        return None


def is_mtop(url):
    return "acs.aliexpress.com" in url and "mtop" in url.lower()


def is_pdp_mtop(url):
    return "pdp.pc.query" in url or "pdp.msite" in url


def is_recom_mtop(url):
    return "recommend" in url.lower() and "aliexpress" in url.lower()


def is_tmd(url):
    return "_____tmd_____" in url or "punish" in url


def on_response(response):
    url = response.url
    status = response.status
    if not is_mtop(url):
        return
    body = safe_body(response)
    body_len = len(body) if body else 0
    body_text = ""
    if body:
        body_text = body[:5000].decode("utf-8", errors="replace")
        # Save PDP
        if is_pdp_mtop(url) and body_len > 200:
            fname = f"mtop_pw60_pdp_{(result['product_id'] or 'unknown')[-6:]}.json"
            fpath = os.path.join(SCREENSHOT_DIR, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(body)
                result["pdp_raw"] = fpath
            except Exception:
                pass
        # Save recommend (for product IDs)
        if is_recom_mtop(url) and body_len > 5000 and body_len < 500000:
            tag = "recom"
            fname = f"mtop_pw60_{tag}_{len(result['mtop_responses'])}.json"
            fpath = os.path.join(SCREENSHOT_DIR, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(body)
            except Exception:
                pass
    rec = {
        "url": url[:300],
        "status": status,
        "body_len": body_len,
        "is_pdp": is_pdp_mtop(url),
        "is_recom": is_recom_mtop(url),
        "body_head": body_text[:500],
    }
    result["mtop_responses"].append(rec)
    log(f"  MTOP[{status}] {'PDP' if is_pdp_mtop(url) else 'RECOM' if is_recom_mtop(url) else 'other'} {url[:100]}")
    if body_len:
        log(f"    body: {body_len} bytes")


def extract_product_ids_from_recom(page):
    """Try to grab product IDs from recommend MTOP responses or DOM."""
    ids = []
    # From captured MTOP
    for rec in result["mtop_responses"]:
        if rec["is_recom"] and rec["body_len"] > 5000:
            # Look for productId in body_head or saved file
            body = rec["body_head"]
            # Also check saved files
            for f in Path(SCREENSHOT_DIR).glob("mtop_pw60_recom_*.json"):
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                    found = re.findall(r'"productId"\s*:\s*"?(\d{10,20})"?', content)
                    ids.extend(found)
                    found2 = re.findall(r'"redirectedId"\s*:\s*"?(\d{10,20})"?', content)
                    ids.extend(found2)
                except Exception:
                    pass
    # From DOM
    try:
        dom_ids = page.evaluate("""
            () => {
                const ids = [];
                document.querySelectorAll('[data-product-id], [data-item-id]').forEach(el => {
                    const id = el.getAttribute('data-product-id') || el.getAttribute('data-item-id');
                    if (id && /^\\d{10,}$/.test(id)) ids.push(id);
                });
                // Also from links
                document.querySelectorAll('a[href*="/item/"]').forEach(a => {
                    const m = a.href.match(/item\\/(\\d+)/);
                    if (m) ids.push(m[1]);
                });
                return [...new Set(ids)];
            }
        """)
        ids.extend(dom_ids)
    except Exception:
        pass
    return list(dict.fromkeys(ids))  # dedupe, preserve order


def parse_pdp_jsonp(raw_text):
    """Parse MTOP JSONP response into structured dict."""
    # Strip JSONP wrapper: mtopjsonp1({...}) or ({...})
    m = re.search(r'^\s*\(?mtopjsonp\d+\(\s*(\{.*\})\s*\)?\s*$', raw_text, re.DOTALL)
    if not m:
        m = re.search(r'^\s*\(\s*(\{.*\})\s*\)\s*$', raw_text, re.DOTALL)
    if not m:
        # Maybe raw JSON
        m = re.search(r'^\s*(\{.*\})\s*$', raw_text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def extract_pdp_fields(data):
    """Extract all product fields from parsed PDP MTOP data."""
    out = {}
    if not data or "data" not in data:
        return out

    d = data["data"]
    if not isinstance(d, dict):
        return out

    result_keys = list(d.keys())
    out["_data_keys"] = result_keys[:30]

    # Look for result wrapper
    res = d.get("result", d)
    if isinstance(res, dict):
        res_keys = list(res.keys())
        out["_result_keys"] = res_keys[:30]

        # GLOBAL_DATA
        gd = res.get("GLOBAL_DATA", {})
        if isinstance(gd, dict):
            out["_globalData_keys"] = list(gd.keys())[:20]
            gdata = gd.get("globalData", {})
            if isinstance(gdata, dict):
                out["bigBossBan"] = gdata.get("bigBossBan", False)
                out["errorCode"] = gdata.get("errorCode")
                out["bigBossBanTip"] = gdata.get("bigBossBanTip")

        # Title / subject
        for key in ["title", "subject", "TITLE", "Subject", "productTitle"]:
            if key in res:
                out["title"] = str(res[key])[:300]
                break
        if "title" not in out:
            # Try nested
            for k, v in res.items():
                if isinstance(v, dict):
                    for k2 in ["title", "subject", "name"]:
                        if k2 in v:
                            out["title"] = str(v[k2])[:300]
                            out["title_path"] = f"{k}.{k2}"
                            break
                if "title" in out:
                    break

        # Price
        price_paths = [
            ("targetSkuPriceInfo", "salePriceString"),
            ("targetSkuPriceInfo", "originalPrice"),
            ("targetSkuPriceInfo", "monthlyLowestPrice"),
            ("targetSkuPriceInfo", "salePriceLocal"),
            ("targetSkuPriceInfo", "currency"),
            ("price", "salePriceString"),
            ("price", "originalPrice"),
            ("priceInfo", "salePriceString"),
            ("skuPrice", "salePriceString"),
        ]
        price = {}
        for path in price_paths:
            obj = res
            for p in path[:-1]:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    obj = None
                    break
            if isinstance(obj, dict) and path[-1] in obj:
                price[path[-1]] = obj[path[-1]]
        # Also check for price in various locations
        if not price:
            for k, v in res.items():
                if isinstance(v, dict) and any(pk in v for pk in ["salePriceString", "price", "amount"]):
                    price[f"from_{k}"] = {pk: v[pk] for pk in v if pk in ["salePriceString", "originalPrice", "price", "amount", "currency", "salePriceLocal", "monthlyLowestPrice"]}
        out["price"] = price if price else "not found"

        # Images
        images = []
        img_sources = [
            ("HEADER_IMAGE_PC", "currentSkuImages"),
            ("HEADER_IMAGE_PC", "imageList"),
            ("image", None),
            ("images", None),
            ("gallery", None),
        ]
        for path in img_sources:
            obj = res
            for p in path[:-1]:
                if isinstance(obj, dict):
                    obj = obj.get(p)
                else:
                    obj = None
                    break
            if isinstance(obj, dict):
                if path[-1]:
                    imgs = obj.get(path[-1], [])
                    if isinstance(imgs, list):
                        for img in imgs[:20]:
                            if isinstance(img, dict):
                                url = img.get("url") or img.get("imgUrl") or img.get("image") or ""
                                if url:
                                    images.append(url)
                            elif isinstance(img, str):
                                images.append(img)
                elif isinstance(obj, list):
                    for img in obj[:20]:
                        if isinstance(img, str):
                            images.append(img)
                        elif isinstance(img, dict):
                            url = img.get("url") or img.get("imgUrl") or ""
                            if url:
                                images.append(url)
        out["images"] = images[:30]
        out["image_count"] = len(images)

        # SKU
        sku = res.get("SKUModule", res.get("skuModule", res.get("sku", {})))
        if isinstance(sku, dict):
            sku_keys = list(sku.keys())[:20]
            out["sku_keys"] = sku_keys
            # Try to get variants
            for k in ["skuPaths", "skuPathList", "variants", "attributes", "propertyList"]:
                if k in sku:
                    out[f"sku_{k}"] = json.dumps(sku[k], ensure_ascii=False)[:2000]
        elif isinstance(sku, list):
            out["sku_list_len"] = len(sku)
            out["sku_sample"] = json.dumps(sku[:3], ensure_ascii=False)[:1000]

        # Category
        for k in ["categoryPath", "category1", "category3", "categoryId", "breadcrumb"]:
            if k in res:
                out[f"cat_{k}"] = str(res[k])[:200]
        # Nested category
        for k, v in res.items():
            if isinstance(v, dict) and "categoryPath" in v:
                out["categoryPath"] = str(v["categoryPath"])[:200]
                break

        # Delivery
        for k in ["deliveryOptionPanelDisplayList", "delivery", "shipping", "logistics"]:
            if k in res and isinstance(res[k], (list, dict)):
                out[f"delivery_{k}"] = json.dumps(res[k], ensure_ascii=False)[:1500]
                break

        # Buyer protection
        for k in ["buyerProtection", "buyerProtectionList", "protection"]:
            if k in res:
                out[f"protection_{k}"] = json.dumps(res[k], ensure_ascii=False)[:1000]
                break

        # Product ID
        for k in ["productId", "itemId", "item_id"]:
            if k in res:
                out["productId"] = str(res[k])
                break
        if "productId" not in out:
            out["productId"] = result.get("product_id")

        # Monthly sales / reviews
        for k in ["monthlySold", "soldCount", "reviewCount", "rating", "score"]:
            if k in res:
                out[k] = res[k]

        # Full dump (truncated) for debugging
        out["_full_dump"] = json.dumps(res, ensure_ascii=False)[:8000]

    return out


def extract_dom(page):
    """DOM extraction as backup."""
    dom = {}
    try:
        dom["title_tag"] = page.title()
        dom["og_title"] = page.evaluate(
            "document.querySelector('meta[property=\"og:title\"]')?.content || ''"
        )
        dom["og_image"] = page.evaluate(
            "document.querySelector('meta[property=\"og:image\"]')?.content || ''"
        )
        dom["og_price"] = page.evaluate(
            "document.querySelector('meta[property=\"og:price:amount\"]')?.content || ''"
        )
        dom["og_currency"] = page.evaluate(
            "document.querySelector('meta[property=\"og:price:currency\"]')?.content || ''"
        )

        # Price from DOM
        price_selectors = [
            ".price-current .price-text",
            "[class*='price'] [class*='current']",
            "[class*='Price'] [class*='Current']",
            ".product-price .price",
            "[data-role='price']",
            ".sku-price .price",
            ".detail-price .price",
        ]
        for sel in price_selectors:
            try:
                els = page.query_selector_all(sel)
                for el in els[:3]:
                    txt = el.inner_text().strip()
                    if txt and re.search(r"[\d.,]+", txt):
                        dom.setdefault("price_candidates", []).append({"sel": sel, "text": txt})
            except Exception:
                pass

        # Currency text nodes
        try:
            price_texts = page.evaluate("""
                () => {
                    const results = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        if (t && (t.includes('$') || t.includes('\\u20ac') || t.includes('\\u00a3') || t.includes('R$') || t.includes('\\u20bd')) && t.length < 50) {
                            results.push(t);
                        }
                    }
                    return results.slice(0, 15);
                }
            """)
            if price_texts:
                dom["price_texts"] = price_texts
        except Exception:
            pass

        # Images
        try:
            imgs = page.evaluate("""
                () => {
                    const urls = new Set();
                    const og = document.querySelector('meta[property="og:image"]');
                    if (og) urls.add(og.content);
                    document.querySelectorAll('img[src*="aliexpress-media"], img[src*="alicdn"]').forEach(img => {
                        const src = img.src || img.dataset.src || '';
                        if (src && (src.includes('/kf/') || src.match(/\\d{2,}x\\d{2,}/))) urls.add(src);
                    });
                    return [...urls].slice(0, 30);
                }
            """)
            dom["images"] = imgs
            dom["img_count"] = len(imgs)
        except Exception as e:
            dom["img_error"] = str(e)

        # H1
        try:
            h1 = page.query_selector("h1")
            if h1:
                dom["h1"] = h1.inner_text().strip()[:200]
        except Exception:
            pass

    except Exception as e:
        dom["error"] = str(e)
    return dom


def extract_js_state(page):
    """JS globals + JSON-LD."""
    state = {}
    try:
        raw = page.evaluate("""
            () => {
                const out = {};
                if (window.__INITIAL_DATA__) out.initial_data = JSON.stringify(window.__INITIAL_DATA__).slice(0, 8000);
                if (window.runParams) out.runParams = JSON.stringify(window.runParams).slice(0, 8000);
                if (window._dida_config_) out.dida = JSON.stringify(window._dida_config_).slice(0, 3000);
                const lds = document.querySelectorAll('script[type="application/ld+json"]');
                out.jsonld = [];
                lds.forEach(s => { try { out.jsonld.push(JSON.parse(s.textContent)); } catch(e) {} });
                return out;
            }
        """)
        state = raw
    except Exception as e:
        state["error"] = str(e)
    return state


def handle_tmd(page):
    """Handle TMD/reCAPTCHA if present. Non-blocking for PDP."""
    tmd_frames = [f for f in page.frames if is_tmd(f.url)]
    if not tmd_frames:
        log("  No TMD frames")
        return False

    log(f"  TMD frames: {len(tmd_frames)}")
    for i, f in enumerate(tmd_frames[:3]):
        log(f"    [{i}] {f.url[:120]}")

    tmd_frame = tmd_frames[0]
    solved = False

    # Search for reCAPTCHA in TMD frame and children
    deadline = time.time() + TMD_WAIT / 1000
    while time.time() < deadline and not solved:
        try:
            for frame in [tmd_frame] + tmd_frame.child_frames:
                for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                             "input[type='checkbox']",
                             "iframe[src*='recaptcha']"]:
                    el = frame.query_selector(sel)
                    if el and el.is_visible():
                        log(f"  reCAPTCHA found: {sel}")
                        el.click()
                        solved = True
                        break
                if solved:
                    break
        except Exception:
            pass
        if not solved:
            page.wait_for_timeout(2000)

    # Wait for resolution
    page.wait_for_timeout(8000)
    remaining = [f for f in page.frames if is_tmd(f.url)]
    log(f"  TMD remaining after wait: {len(remaining)}")
    if len(remaining) < len(tmd_frames):
        solved = True
        log("  TMD resolved!")

    result["tmd_handled"] = True
    result["recaptcha_solved"] = solved
    return solved


def main():
    log("=== pw60: AliExpress PDP via IT warm profile ===")
    log(f"Profile: {PROFILE_DIR}")

    with sync_playwright() as pw:
        t0 = time.time()
        browser = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="it-IT",
            timezone_id="Europe/Rome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.on("response", on_response)
        result["timings"]["launch"] = time.time() - t0

        # ── Phase 1: Homepage warm-up + grab product IDs ──────────────
        log("=== Phase 1: Homepage ===")
        t0 = time.time()
        try:
            page.goto("https://www.aliexpress.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(3000)
            page.evaluate("window.scrollBy(0, 500)")
            page.wait_for_timeout(2000)
            cookies = browser.cookies()
            log(f"  Cookies: {len(cookies)}")
            log(f"  MTOP so far: {len(result['mtop_responses'])}")
        except Exception as e:
            result["errors"].append(f"homepage: {e}")
            log(f"  Homepage error: {e}")
        result["timings"]["homepage"] = time.time() - t0

        # Grab product IDs
        product_ids = extract_product_ids_from_recom(page)
        log(f"  Product IDs found: {len(product_ids)}")
        for pid in product_ids[:10]:
            log(f"    {pid}")

        # Pick a product (first valid one, or fallback)
        target_id = None
        for pid in product_ids:
            if len(pid) >= 12:  # valid AE product ID
                target_id = pid
                break
        if not target_id:
            target_id = "1005011813323763"  # fallback (known to work in IT, even if banned)
            log(f"  Fallback to known ID: {target_id}")

        result["product_id"] = target_id
        result["product_url"] = f"https://www.aliexpress.com/item/{target_id}.html"
        log(f"  Target product: {target_id}")

        # ── Phase 2: Load product page ─────────────────────────────────
        log("=== Phase 2: Product page ===")
        t0 = time.time()
        mtop_before = len(result["mtop_responses"])
        try:
            page.goto(result["product_url"], timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(WAIT_AFTER_LOAD)
            page.evaluate("window.scrollBy(0, 400)")
            page.wait_for_timeout(3000)
            log(f"  Title: {page.title()[:80]}")
            new_mtop = len(result["mtop_responses"]) - mtop_before
            log(f"  New MTOP: {new_mtop}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw60_product.png"))
        except Exception as e:
            result["errors"].append(f"product_load: {e}")
            log(f"  Load error: {e}")
        result["timings"]["product_load"] = time.time() - t0

        # ── Phase 3: TMD handling ──────────────────────────────────────
        log("=== Phase 3: TMD check ===")
        t0 = time.time()
        handle_tmd(page)
        result["timings"]["tmd"] = time.time() - t0

        # ── Phase 4: If no PDP yet, reload ─────────────────────────────
        pdp_captured = any(r["is_pdp"] and r["body_len"] > 200 for r in result["mtop_responses"])
        if not pdp_captured:
            log("=== Phase 4: Reload for PDP ===")
            t0 = time.time()
            mtop_before = len(result["mtop_responses"])
            try:
                page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(RELOAD_WAIT)
                page.evaluate("window.scrollBy(0, 300)")
                page.wait_for_timeout(3000)
                new_mtop = len(result["mtop_responses"]) - mtop_before
                log(f"  After reload: {new_mtop} new MTOP")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw60_reload.png"))
            except Exception as e:
                result["errors"].append(f"reload: {e}")
                log(f"  Reload error: {e}")
            result["timings"]["reload"] = time.time() - t0
        else:
            log("=== Phase 4: PDP already captured ===")

        # ── Phase 5: Parse PDP ─────────────────────────────────────────
        log("=== Phase 5: Parse PDP ===")
        pdp_rec = next((r for r in result["mtop_responses"] if r["is_pdp"] and r["body_len"] > 200), None)
        if pdp_rec:
            # Read from saved file or body_head
            raw_text = ""
            if result.get("pdp_raw") and os.path.exists(result["pdp_raw"]):
                with open(result["pdp_raw"], "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()
            elif pdp_rec["body_head"]:
                raw_text = pdp_rec["body_head"]

            if raw_text:
                parsed = parse_pdp_jsonp(raw_text)
                if parsed:
                    result["pdp_parsed"] = extract_pdp_fields(parsed)
                    log(f"  PDP parsed OK. Keys: {list(result['pdp_parsed'].keys())[:15]}")
                    log(f"  Title: {str(result['pdp_parsed'].get('title', ''))[:80]}")
                    log(f"  Price: {json.dumps(result['pdp_parsed'].get('price', {}), ensure_ascii=False)[:200]}")
                    log(f"  Images: {result['pdp_parsed'].get('image_count', 0)}")
                    log(f"  bigBossBan: {result['pdp_parsed'].get('bigBossBan', False)}")
                    log(f"  errorCode: {result['pdp_parsed'].get('errorCode')}")
                else:
                    log(f"  PDP JSONP parse failed. Raw head: {raw_text[:200]}")
                    result["errors"].append("pdp_jsonp_parse_fail")
            else:
                log("  No PDP raw text available")
        else:
            log("  No PDP MTOP captured!")
            result["errors"].append("no_pdp_mtop")

        # ── Phase 6: DOM extraction ────────────────────────────────────
        log("=== Phase 6: DOM ===")
        result["dom_data"] = extract_dom(page)
        d = result["dom_data"]
        log(f"  Title: {d.get('og_title') or d.get('title_tag') or d.get('h1', '')[:80]}")
        log(f"  Price candidates: {d.get('price_candidates', [])[:3]}")
        log(f"  Price texts: {d.get('price_texts', [])[:5]}")
        log(f"  Images: {d.get('img_count', 0)}")

        # ── Phase 7: JS state ──────────────────────────────────────────
        log("=== Phase 7: JS state ===")
        result["js_state"] = extract_js_state(page)
        js = result["js_state"]
        for key in ["initial_data", "runParams", "dida"]:
            if key in js and js[key]:
                log(f"  {key}: {len(js[key])} chars")
        if "jsonld" in js and js["jsonld"]:
            log(f"  JSON-LD: {len(js['jsonld'])} blocks")
            for i, ld in enumerate(js["jsonld"][:3]):
                log(f"    [{i}] type={ld.get('@type','?')} name={str(ld.get('name',''))[:60]}")

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw60_final.png"))
        result["phase"] = "done"
        browser.close()

    # ── Save ──────────────────────────────────────────────────────────
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    size = os.path.getsize(OUT_JSON)
    log("=== DONE ===")
    log(f"  Product: {result['product_id']}")
    log(f"  MTOP total: {len(result['mtop_responses'])}")
    log(f"  PDP captured: {any(r['is_pdp'] and r['body_len'] > 200 for r in result['mtop_responses'])}")
    log(f"  PDP parsed: {bool(result['pdp_parsed'])}")
    log(f"  Title: {str(result['pdp_parsed'].get('title', result['dom_data'].get('og_title', '')))[:80]}")
    log(f"  Price: {json.dumps(result['pdp_parsed'].get('price', {}), ensure_ascii=False)[:150]}")
    log(f"  Images: {result['pdp_parsed'].get('image_count', result['dom_data'].get('img_count', 0))}")
    log(f"  TMD: {result['tmd_handled']}, reCAPTCHA: {result['recaptcha_solved']}")
    log(f"  Errors: {result['errors']}")
    log(f"  Result: {OUT_JSON} ({size} B)")
    log(f"  Total time: {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        result["phase"] = "error"
        result["errors"].append(traceback.format_exc())
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        sys.exit(1)
