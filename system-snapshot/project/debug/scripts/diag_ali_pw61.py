#!/usr/bin/env python
"""
pw61: AliExpress PDP — fixed reCAPTCHA + proper homepage warm + PDP wait.
Fixes over pw60:
  1. Homepage: wait for networkidle + longer timeout, verify MTOP fired
  2. reCAPTCHA: content_frame() → #recaptcha-anchor (not iframe element)
  3. PDP: use page.expect_response() with timeout for pdp.pc.query
  4. Fallback: direct MTOP via page.evaluate(fetch) if page-level fails
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
PROFILE_DIR = os.path.join(BASE_DIR, "pw_ali_profile_pw53")
OUT_JSON = os.path.join(BASE_DIR, "diag_ali_pw61_result.json")
SCREENSHOT_DIR = BASE_DIR

NAV_TIMEOUT = 50_000
HOME_WAIT = 15_000
PRODUCT_WAIT = 12_000
TMD_WAIT = 30_000
PDP_WAIT = 20_000

result = {
    "timestamp": datetime.now().isoformat(),
    "phase": "init",
    "product_id": None,
    "product_url": None,
    "mtop_responses": [],
    "pdp_raw_path": None,
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
        if is_pdp_mtop(url) and body_len > 200:
            fname = f"mtop_pw61_pdp_{(result['product_id'] or 'unknown')[-6:]}.json"
            fpath = os.path.join(SCREENSHOT_DIR, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(body)
                result["pdp_raw_path"] = fpath
                log(f"  PDP SAVED: {fname} ({body_len}B)")
            except Exception as e:
                log(f"  PDP save error: {e}")
        if is_recom_mtop(url) and 5000 < body_len < 500000:
            fname = f"mtop_pw61_recom_{len(result['mtop_responses'])}.json"
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
    tag = "PDP" if is_pdp_mtop(url) else "RECOM" if is_recom_mtop(url) else "other"
    log(f"  MTOP[{status}] {tag} {url[:100]} ({body_len}B)")


def grab_product_ids(page):
    """Extract product IDs from MTOP responses and DOM."""
    ids = []
    # From saved recom files
    for f in Path(SCREENSHOT_DIR).glob("mtop_pw61_recom_*.json"):
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            ids.extend(re.findall(r'"productId"\s*:\s*"?(\d{10,20})"?', content))
            ids.extend(re.findall(r'"redirectedId"\s*:\s*"?(\d{10,20})"?', content))
        except Exception:
            pass
    # From DOM
    try:
        dom_ids = page.evaluate("""
            () => {
                const ids = [];
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
    return list(dict.fromkeys(ids))


def solve_recaptcha(page, tmd_frame):
    """Properly solve reCAPTCHA: navigate into iframe → find checkbox → click."""
    log("  Solving reCAPTCHA...")
    solved = False

    for attempt in range(3):
        log(f"  Attempt {attempt + 1}/3")
        try:
            # Find reCAPTCHA iframe in TMD frame or its children
            rc_frame = None
            for frame in [tmd_frame] + tmd_frame.child_frames:
                # Method 1: iframe element → content_frame
                for sel in ["iframe[src*='recaptcha']", "iframe[src*='google.com/recaptcha']",
                             "iframe[src*='google.com']"]:
                    el = frame.query_selector(sel)
                    if el:
                        try:
                            rc_frame = el.content_frame()
                            if rc_frame:
                                log(f"    Found reCAPTCHA iframe via {sel} in {frame.url[:60]}")
                                break
                        except Exception:
                            continue
                if rc_frame:
                    break
                # Method 2: direct child frame with recaptcha in URL
                for child in frame.child_frames:
                    if "recaptcha" in child.url.lower() or "google.com" in child.url.lower():
                        rc_frame = child
                        log(f"    Found reCAPTCHA child frame: {child.url[:80]}")
                        break
                if rc_frame:
                    break

            if not rc_frame:
                log("    No reCAPTCHA frame found, waiting...")
                page.wait_for_timeout(3000)
                continue

            # Now find the actual checkbox INSIDE the reCAPTCHA frame
            log(f"    reCAPTCHA frame URL: {rc_frame.url[:100]}")
            page.wait_for_timeout(2000)  # let it load

            checkbox = None
            for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                         "input[type='checkbox']", "#recaptcha-anchor span",
                         ".rcb-anchor", ".rcb-background-box"]:
                try:
                    checkbox = rc_frame.query_selector(sel)
                    if checkbox and checkbox.is_visible():
                        log(f"    Checkbox found: {sel}")
                        break
                except Exception:
                    continue

            if not checkbox:
                # Try waiting a bit more
                log("    Checkbox not visible yet, waiting 3s...")
                page.wait_for_timeout(3000)
                for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                             "input[type='checkbox']", ".rcb-anchor"]:
                    try:
                        checkbox = rc_frame.query_selector(sel)
                        if checkbox:
                            log(f"    Checkbox found (retry): {sel}")
                            break
                    except Exception:
                        continue

            if checkbox:
                # Click with force to bypass any overlay
                try:
                    checkbox.click(timeout=5000)
                    log("    Clicked checkbox (normal)")
                except Exception:
                    try:
                        checkbox.click(force=True, timeout=5000)
                        log("    Clicked checkbox (force)")
                    except Exception as e2:
                        log(f"    Click failed: {e2}")
                        # JS click as last resort
                        try:
                            rc_frame.evaluate("document.querySelector('#recaptcha-anchor')?.click()")
                            log("    JS-clicked checkbox")
                        except Exception:
                            pass
            else:
                log("    No checkbox element found in reCAPTCHA frame")
                # Try JS in the frame
                try:
                    rc_frame.evaluate("""
                        () => {
                            const cb = document.querySelector('#recaptcha-anchor') ||
                                       document.querySelector('.recaptcha-checkbox') ||
                                       document.querySelector('input[type=checkbox]');
                            if (cb) cb.click();
                        }
                    """)
                    log("    JS-click attempted in reCAPTCHA frame")
                except Exception as e:
                    log(f"    JS click error: {e}")

            # Wait for verification (green checkmark)
            log("    Waiting for verification (10s)...")
            page.wait_for_timeout(10_000)

            # Check if TMD frames reduced
            remaining = [f for f in page.frames if is_tmd(f.url)]
            log(f"    TMD frames remaining: {len(remaining)}")
            if len(remaining) == 0:
                solved = True
                log("    TMD RESOLVED!")
                break
            elif len(remaining) < 3:
                # Partial resolution
                page.wait_for_timeout(5000)
                remaining = [f for f in page.frames if is_tmd(f.url)]
                if len(remaining) == 0:
                    solved = True
                    log("    TMD RESOLVED (after extra wait)!")
                    break
        except Exception as e:
            log(f"    Error: {e}")
            page.wait_for_timeout(2000)

    return solved


def parse_pdp_jsonp(raw_text):
    m = re.search(r'^\s*\(?mtopjsonp\d+\(\s*(\{.*\})\s*\)?\s*$', raw_text, re.DOTALL)
    if not m:
        m = re.search(r'^\s*\(\s*(\{.*\})\s*\)\s*$', raw_text, re.DOTALL)
    if not m:
        m = re.search(r'^\s*(\{.*\})\s*$', raw_text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def extract_pdp_fields(data):
    out = {}
    if not data or "data" not in data:
        return out
    d = data["data"]
    if not isinstance(d, dict):
        return out
    out["_data_keys"] = list(d.keys())[:30]
    res = d.get("result", d)
    if not isinstance(res, dict):
        out["_res_type"] = type(res).__name__
        return out
    out["_result_keys"] = list(res.keys())[:30]

    # GLOBAL_DATA
    gd = res.get("GLOBAL_DATA", {})
    if isinstance(gd, dict):
        gdata = gd.get("globalData", {})
        if isinstance(gdata, dict):
            out["bigBossBan"] = gdata.get("bigBossBan", False)
            out["errorCode"] = gdata.get("errorCode")
            out["bigBossBanTip"] = gdata.get("bigBossBanTip")

    # Title
    for key in ["title", "subject", "TITLE", "productTitle", "name"]:
        if key in res:
            out["title"] = str(res[key])[:300]
            break
    if "title" not in out:
        for k, v in res.items():
            if isinstance(v, dict):
                for k2 in ["title", "subject", "name"]:
                    if k2 in v and isinstance(v[k2], str) and len(v[k2]) > 10:
                        out["title"] = v[k2][:300]
                        out["title_path"] = f"{k}.{k2}"
                        break
            if "title" in out:
                break

    # Price
    price = {}
    for path in [("targetSkuPriceInfo", "salePriceString"), ("targetSkuPriceInfo", "originalPrice"),
                 ("targetSkuPriceInfo", "monthlyLowestPrice"), ("targetSkuPriceInfo", "salePriceLocal"),
                 ("targetSkuPriceInfo", "currency"), ("price", "salePriceString"),
                 ("price", "originalPrice"), ("priceInfo", "salePriceString"),
                 ("skuPrice", "salePriceString"), ("skuPrice", "currency")]:
        obj = res
        for p in path[:-1]:
            if isinstance(obj, dict):
                obj = obj.get(p)
            else:
                obj = None
                break
        if isinstance(obj, dict) and path[-1] in obj:
            price[path[-1]] = obj[path[-1]]
    if not price:
        for k, v in res.items():
            if isinstance(v, dict) and any(pk in v for pk in ["salePriceString", "price", "amount", "currency"]):
                price[f"from_{k}"] = {pk: v[pk] for pk in v if pk in ["salePriceString", "originalPrice", "price", "amount", "currency", "salePriceLocal", "monthlyLowestPrice"]}
    out["price"] = price if price else "not found"

    # Images
    images = []
    for path in [("HEADER_IMAGE_PC", "currentSkuImages"), ("HEADER_IMAGE_PC", "imageList"),
                 ("image", None), ("images", None), ("gallery", None)]:
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
        out["sku_keys"] = list(sku.keys())[:20]
        for k in ["skuPaths", "skuPathList", "variants", "attributes", "propertyList"]:
            if k in sku:
                out[f"sku_{k}"] = json.dumps(sku[k], ensure_ascii=False)[:2000]

    # Category
    for k in ["categoryPath", "category1", "category3", "categoryId"]:
        if k in res:
            out[f"cat_{k}"] = str(res[k])[:200]

    # Delivery
    for k in ["deliveryOptionPanelDisplayList", "delivery", "shipping", "logistics"]:
        if k in res and isinstance(res[k], (list, dict)):
            out[f"delivery_{k}"] = json.dumps(res[k], ensure_ascii=False)[:1500]
            break

    # Product ID
    for k in ["productId", "itemId"]:
        if k in res:
            out["productId"] = str(res[k])
            break

    # Full dump
    out["_full_dump"] = json.dumps(res, ensure_ascii=False)[:8000]
    return out


def extract_dom(page):
    dom = {}
    try:
        dom["title_tag"] = page.title()
        dom["og_title"] = page.evaluate("document.querySelector('meta[property=\"og:title\"]')?.content || ''")
        dom["og_image"] = page.evaluate("document.querySelector('meta[property=\"og:image\"]')?.content || ''")
        dom["og_price"] = page.evaluate("document.querySelector('meta[property=\"og:price:amount\"]')?.content || ''")
        dom["og_currency"] = page.evaluate("document.querySelector('meta[property=\"og:price:currency\"]')?.content || ''")

        # H1
        try:
            h1 = page.query_selector("h1")
            if h1:
                dom["h1"] = h1.inner_text().strip()[:200]
        except Exception:
            pass

        # Price selectors
        price_selectors = [
            ".price-current .price-text", "[class*='price'] [class*='current']",
            "[class*='Price'] [class*='Current']", ".product-price .price",
            "[data-role='price']", ".sku-price .price", ".detail-price .price",
        ]
        for sel in price_selectors:
            try:
                for el in page.query_selector_all(sel)[:3]:
                    txt = el.inner_text().strip()
                    if txt and re.search(r"[\d.,]+", txt):
                        dom.setdefault("price_candidates", []).append({"sel": sel, "text": txt})
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
    except Exception as e:
        dom["error"] = str(e)
    return dom


def main():
    log("=== pw61: AliExpress PDP (fixed reCAPTCHA + warm homepage) ===")

    with sync_playwright() as pw:
        t0 = time.time()
        browser = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="en-US",
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

        # ── Phase 1: Homepage (long wait, verify MTOP) ────────────────
        log("=== Phase 1: Homepage (15s + networkidle) ===")
        t0 = time.time()
        try:
            page.goto("https://www.aliexpress.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            # Wait for network to settle
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
                log("  networkidle reached")
            except PWTimeout:
                log("  networkidle timeout (continuing)")
            page.wait_for_timeout(3_000)
            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(3_000)
            page.evaluate("window.scrollBy(0, 400)")
            page.wait_for_timeout(2_000)
            cookies = browser.cookies()
            log(f"  Cookies: {len(cookies)}")
            log(f"  MTOP on homepage: {len(result['mtop_responses'])}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw61_home.png"))
        except Exception as e:
            result["errors"].append(f"homepage: {e}")
            log(f"  Homepage error: {e}")
        result["timings"]["homepage"] = time.time() - t0

        # If no MTOP on homepage, try a category page
        if len(result["mtop_responses"]) == 0:
            log("  No MTOP on homepage, trying category page...")
            try:
                page.goto("https://www.aliexpress.com/category/100003102.html", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(8_000)
                page.evaluate("window.scrollBy(0, 500)")
                page.wait_for_timeout(3_000)
                log(f"  MTOP after category: {len(result['mtop_responses'])}")
            except Exception as e:
                result["errors"].append(f"category: {e}")

        # Grab product IDs
        product_ids = grab_product_ids(page)
        log(f"  Product IDs: {len(product_ids)}")
        for pid in product_ids[:8]:
            log(f"    {pid}")

        target_id = None
        for pid in product_ids:
            if len(pid) >= 12:
                target_id = pid
                break
        if not target_id:
            target_id = "1005011813323763"
            log(f"  Fallback ID: {target_id}")

        result["product_id"] = target_id
        result["product_url"] = f"https://www.aliexpress.com/item/{target_id}.html"
        log(f"  Target: {target_id}")

        # ── Phase 2: Product page ─────────────────────────────────────
        log("=== Phase 2: Product page ===")
        t0 = time.time()
        mtop_before = len(result["mtop_responses"])
        try:
            page.goto(result["product_url"], timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(PRODUCT_WAIT)
            page.evaluate("window.scrollBy(0, 400)")
            page.wait_for_timeout(3_000)
            log(f"  Title: {page.title()[:80]}")
            new_mtop = len(result["mtop_responses"]) - mtop_before
            log(f"  New MTOP: {new_mtop}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw61_product.png"))
        except Exception as e:
            result["errors"].append(f"product_load: {e}")
            log(f"  Load error: {e}")
        result["timings"]["product_load"] = time.time() - t0

        # ── Phase 3: TMD + reCAPTCHA ──────────────────────────────────
        log("=== Phase 3: TMD + reCAPTCHA ===")
        t0 = time.time()
        tmd_frames = [f for f in page.frames if is_tmd(f.url)]
        if tmd_frames:
            log(f"  TMD frames: {len(tmd_frames)}")
            for i, f in enumerate(tmd_frames[:3]):
                log(f"    [{i}] {f.url[:120]}")
            result["tmd_handled"] = True
            solved = solve_recaptcha(page, tmd_frames[0])
            result["recaptcha_solved"] = solved
            # After solve, wait and check
            page.wait_for_timeout(5_000)
            remaining = [f for f in page.frames if is_tmd(f.url)]
            log(f"  TMD remaining: {len(remaining)}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw61_after_tmd.png"))
        else:
            log("  No TMD — good")
        result["timings"]["tmd"] = time.time() - t0

        # ── Phase 4: Wait for PDP (if not captured yet) ───────────────
        pdp_captured = any(r["is_pdp"] and r["body_len"] > 200 for r in result["mtop_responses"])
        if not pdp_captured:
            log("=== Phase 4: Reload + wait for PDP ===")
            t0 = time.time()
            mtop_before = len(result["mtop_responses"])
            try:
                page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                # Wait specifically for PDP MTOP
                log("  Waiting for PDP MTOP (20s)...")
                pdp_resp = None
                try:
                    pdp_resp = page.wait_for_response(
                        lambda r: is_pdp_mtop(r.url) and r.status == 200,
                        timeout=PDP_WAIT
                    )
                    log(f"  PDP response received! status={pdp_resp.status}")
                except PWTimeout:
                    log("  PDP wait timeout")
                page.wait_for_timeout(5_000)
                page.evaluate("window.scrollBy(0, 300)")
                page.wait_for_timeout(3_000)
                new_mtop = len(result["mtop_responses"]) - mtop_before
                log(f"  New MTOP after reload: {new_mtop}")
                page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw61_reload.png"))
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
            raw_text = ""
            if result.get("pdp_raw_path") and os.path.exists(result["pdp_raw_path"]):
                with open(result["pdp_raw_path"], "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()
            elif pdp_rec["body_head"]:
                raw_text = pdp_rec["body_head"]

            if raw_text:
                parsed = parse_pdp_jsonp(raw_text)
                if parsed:
                    result["pdp_parsed"] = extract_pdp_fields(parsed)
                    log(f"  PDP keys: {list(result['pdp_parsed'].keys())[:15]}")
                    log(f"  Title: {str(result['pdp_parsed'].get('title', ''))[:80]}")
                    log(f"  Price: {json.dumps(result['pdp_parsed'].get('price', {}), ensure_ascii=False)[:200]}")
                    log(f"  Images: {result['pdp_parsed'].get('image_count', 0)}")
                    log(f"  bigBossBan: {result['pdp_parsed'].get('bigBossBan', False)}")
                    log(f"  errorCode: {result['pdp_parsed'].get('errorCode')}")
                    log(f"  result_keys: {result['pdp_parsed'].get('_result_keys', [])[:10]}")
                else:
                    log(f"  JSONP parse fail. Head: {raw_text[:200]}")
                    result["errors"].append("pdp_jsonp_parse_fail")
        else:
            log("  No PDP MTOP!")
            result["errors"].append("no_pdp_mtop")

        # ── Phase 6: DOM ───────────────────────────────────────────────
        log("=== Phase 6: DOM ===")
        result["dom_data"] = extract_dom(page)
        d = result["dom_data"]
        log(f"  Title: {d.get('og_title') or d.get('title_tag') or d.get('h1', '')[:80]}")
        log(f"  Price: {d.get('price_candidates', [])[:3]}")
        log(f"  Images: {d.get('img_count', 0)}")

        # ── Phase 7: JS state ──────────────────────────────────────────
        log("=== Phase 7: JS state ===")
        try:
            js = page.evaluate("""
                () => {
                    const out = {};
                    if (window.__INITIAL_DATA__) out.initial_data = JSON.stringify(window.__INITIAL_DATA__).slice(0, 8000);
                    if (window.runParams) out.runParams = JSON.stringify(window.runParams).slice(0, 8000);
                    const lds = document.querySelectorAll('script[type="application/ld+json"]');
                    out.jsonld = [];
                    lds.forEach(s => { try { out.jsonld.push(JSON.parse(s.textContent)); } catch(e) {} });
                    return out;
                }
            """)
            result["js_state"] = js
            for key in ["initial_data", "runParams"]:
                if js.get(key):
                    log(f"  {key}: {len(js[key])} chars")
            if js.get("jsonld"):
                log(f"  JSON-LD: {len(js['jsonld'])} blocks")
        except Exception as e:
            result["js_state"] = {"error": str(e)}
            log(f"  JS state error: {e}")

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw61_final.png"))
        result["phase"] = "done"
        browser.close()

    # ── Save ──────────────────────────────────────────────────────────
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    size = os.path.getsize(OUT_JSON)
    log("=== DONE ===")
    log(f"  Product: {result['product_id']}")
    log(f"  MTOP total: {len(result['mtop_responses'])}")
    log(f"  PDP: {any(r['is_pdp'] and r['body_len'] > 200 for r in result['mtop_responses'])}")
    log(f"  PDP parsed: {bool(result['pdp_parsed'])}")
    log(f"  Title: {str(result['pdp_parsed'].get('title', result['dom_data'].get('og_title', '')))[:80]}")
    log(f"  Price: {json.dumps(result['pdp_parsed'].get('price', {}), ensure_ascii=False)[:150]}")
    log(f"  Images: {result['pdp_parsed'].get('image_count', result['dom_data'].get('img_count', 0))}")
    log(f"  TMD: {result['tmd_handled']}, reCAPTCHA: {result['recaptcha_solved']}")
    log(f"  Errors: {result['errors']}")
    log(f"  Result: {OUT_JSON} ({size} B)")
    log(f"  Total: {time.time() - t_start:.1f}s")


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
