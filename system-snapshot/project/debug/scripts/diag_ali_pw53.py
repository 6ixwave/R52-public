#!/usr/bin/env python
"""
pw53: AliExpress TMD bypass + MTOP PDP + DOM (v3)
Fixes:
  - Response.body() without timeout kwarg
  - Nested iframe search for reCAPTCHA
  - Extended price selectors
  - JS state extraction (window.__INITIAL_DATA__, runParams)
  - Longer waits, better TMD resolution detection
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
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pw_ali_profile_pw53")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_ali_pw53_result.json")
SCREENSHOT_DIR = os.path.dirname(os.path.abspath(__file__))

NAV_TIMEOUT = 45_000
WAIT_AFTER_LOAD = 8_000
TMD_WAIT = 40_000       # max wait for reCAPTCHA to appear
RELOAD_WAIT = 15_000    # wait after reload for MTOP

result = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now().isoformat(),
    "phase": "init",
    "mtop_responses": [],
    "pdp_data": {},
    "dom_data": {},
    "js_state": {},
    "tmd_handled": False,
    "recaptcha_solved": False,
    "errors": [],
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def safe_body(response):
    """Read response body safely — no timeout kwarg in Playwright 1.64."""
    try:
        body = response.body()
        return body
    except Exception as e:
        return None


def is_mtop(url):
    return "acs.aliexpress.com" in url and "mtop" in url.lower()


def is_pdp_mtop(url):
    return "pdp.pc.query" in url or "pdp.msite" in url


def is_tmd(url):
    return "_____tmd_____" in url or "punish" in url


def on_response(response):
    url = response.url
    status = response.status
    if not is_mtop(url):
        return
    body = safe_body(response)
    body_text = ""
    body_len = 0
    if body:
        body_len = len(body)
        body_text = body[:3000].decode("utf-8", errors="replace")
        # Save full body for PDP
        if is_pdp_mtop(url) and body_len > 200:
            tag = "pdp" if "pdp.pc.query" in url else "pdp_msite"
            fname = f"mtop_pw53_{tag}_{PRODUCT_ID[-6:]}.json"
            fpath = os.path.join(SCREENSHOT_DIR, fname)
            try:
                with open(fpath, "wb") as f:
                    f.write(body)
            except Exception:
                pass
            result["mtop_responses"][-1]["saved_to"] = fpath
    rec = {
        "url": url[:300],
        "status": status,
        "body_len": body_len,
        "saved_to": "",
        "body_head": body_text[:500],
        "body_error": "",
    }
    result["mtop_responses"].append(rec)
    log(f"  MTOP {status} {url[:120]}")
    if body_len:
        log(f"    body: {body_len} bytes")
    else:
        log(f"    body: EMPTY (error: {rec['body_error']})")


def find_recaptcha_frame(frame, depth=0, max_depth=4):
    """Recursively search for reCAPTCHA in nested frames."""
    prefix = "  " * depth
    try:
        # Check current frame
        for sel in ["#recaptcha-anchor", ".recaptcha-checkbox", "input[type='checkbox']",
                     "iframe[src*='recaptcha']", "iframe[src*='google.com/recaptcha']"]:
            el = frame.query_selector(sel)
            if el and el.is_visible():
                log(f"{prefix}Found reCAPTCHA element at depth {depth}: {sel}")
                return frame, sel
    except Exception:
        pass
    # Recurse into child frames
    if depth < max_depth:
        try:
            children = frame.child_frames
            for child in children:
                log(f"{prefix}child frame: {child.url[:100]}")
                found_frame, found_sel = find_recaptcha_frame(child, depth + 1, max_depth)
                if found_frame:
                    return found_frame, found_sel
        except Exception:
            pass
    return None, None


def extract_dom(page):
    """Extract product data from DOM with multiple selector strategies."""
    dom = {}
    try:
        # Title
        dom["og:title"] = page.evaluate(
            "document.querySelector('meta[property=\"og:title\"]')?.content || ''"
        )
        dom["og:image"] = page.evaluate(
            "document.querySelector('meta[property=\"og:image\"]')?.content || ''"
        )
        dom["title_tag"] = page.title()

        # ── Price: try many selectors ──
        price_selectors = [
            # New PDP layout
            ".price-current .price-text",
            ".price-current .current-price",
            "[class*='price'] [class*='current']",
            "[class*='Price'] [class*='Current']",
            ".product-price .price",
            ".price-wrap .price",
            # Legacy
            ".sku-price .price",
            ".sku-price .current",
            ".detail-price .price",
            "[data-role='price']",
            ".price-box .price",
            # Generic: look for currency symbols
            "[class*='price'] span",
            "[class*='Price'] span",
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

        # Also grab all text nodes with currency symbols
        try:
            price_texts = page.evaluate("""
                () => {
                    const results = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        if (t && (t.includes('$') || t.includes('€') || t.includes('¥') || t.includes('R$') || t.includes('₽')) && t.length < 50) {
                            results.push(t);
                        }
                    }
                    return results.slice(0, 20);
                }
            """)
            if price_texts:
                dom["price_texts_currency"] = price_texts
        except Exception:
            pass

        # ── Availability / stock ──
        avail_selectors = [
            "[class*='stock']", "[class*='Stock']",
            "[class*='availability']", "[class*='Availability']",
            "[class*='in-stock']", "[class*='out-of-stock']",
            "[data-role='stock']",
        ]
        for sel in avail_selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    txt = el.inner_text().strip()
                    if txt:
                        dom["availability"] = txt
                        break
            except Exception:
                pass

        # ── Category / breadcrumb ──
        try:
            bc = page.query_selector_all(".breadcrumb a, [class*='breadcrumb'] a, nav a")
            cats = [a.inner_text().strip() for a in bc if a.inner_text().strip()]
            if cats:
                dom["breadcrumb"] = cats
        except Exception:
            pass

        # ── Images ──
        try:
            imgs = page.evaluate("""
                () => {
                    const urls = new Set();
                    // og:image
                    const og = document.querySelector('meta[property="og:image"]');
                    if (og) urls.add(og.content);
                    // product gallery
                    document.querySelectorAll('.gallery img, [class*="gallery"] img, .img-wrap img, .product-img img').forEach(img => {
                        const src = img.src || img.dataset.src || '';
                        if (src && src.includes('aliexpress-media')) urls.add(src);
                    });
                    // all product images
                    document.querySelectorAll('img[src*="aliexpress-media"]').forEach(img => {
                        const src = img.src || '';
                        if (src && !src.includes('/kf/') === false && src.match(/\\d{2,}x\\d{2,}/)) urls.add(src);
                    });
                    return [...urls].slice(0, 30);
                }
            """)
            dom["img_count"] = len(imgs)
            dom["images"] = imgs
        except Exception as e:
            dom["img_error"] = str(e)

        # ── SKU / variants ──
        try:
            sku_text = page.evaluate("""
                () => {
                    const els = document.querySelectorAll('[class*="sku"] , [class*="Sku"], [class*="variant"], [class*="attribute"]');
                    const texts = [];
                    els.forEach(el => {
                        const t = el.innerText?.trim();
                        if (t && t.length < 200) texts.push(t);
                    });
                    return texts.slice(0, 15);
                }
            """)
            if sku_text:
                dom["sku_variants"] = sku_text
        except Exception:
            pass

    except Exception as e:
        dom["error"] = str(e)
    return dom


def extract_js_state(page):
    """Try to grab structured data from JS globals."""
    state = {}
    try:
        raw = page.evaluate("""
            () => {
                const out = {};
                // window.__INITIAL_DATA__
                if (window.__INITIAL_DATA__) {
                    out.initial_data = JSON.stringify(window.__INITIAL_DATA__).slice(0, 5000);
                }
                // window.runParams
                if (window.runParams) {
                    out.runParams = JSON.stringify(window.runParams).slice(0, 5000);
                }
                // window._dida_config_
                if (window._dida_config_) {
                    out.dida = JSON.stringify(window._dida_config_).slice(0, 3000);
                }
                // window.__NEXT_DATA__ (unlikely but check)
                if (window.__NEXT_DATA__) {
                    out.next = JSON.stringify(window.__NEXT_DATA__).slice(0, 5000);
                }
                // script[type="application/ld+json"]
                const lds = document.querySelectorAll('script[type="application/ld+json"]');
                out.jsonld = [];
                lds.forEach(s => {
                    try { out.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                });
                return out;
            }
        """)
        state = raw
    except Exception as e:
        state["error"] = str(e)
    return state


def main():
    log(f"=== pw53: AliExpress TMD bypass + MTOP PDP + DOM (v3) ===")
    log(f"Product: {PRODUCT_ID}")
    log(f"URL: {PRODUCT_URL}")

    with sync_playwright() as pw:
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

        # ── Phase 1: Warm-up ──────────────────────────────────────────
        log("=== Phase 1: Warm-up ===")
        try:
            page.goto("https://www.aliexpress.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(5_000)
            # Scroll a bit to trigger lazy loads
            page.evaluate("window.scrollBy(0, 500)")
            page.wait_for_timeout(2_000)
            cookies = browser.cookies()
            log(f"  Cookies: {len(cookies)}")
        except Exception as e:
            result["errors"].append(f"warmup: {e}")
            log(f"  Warmup error: {e}")

        # ── Phase 2: Load product page ────────────────────────────────
        log("=== Phase 2: Load product page ===")
        mtop_before = len(result["mtop_responses"])
        try:
            page.goto(PRODUCT_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(WAIT_AFTER_LOAD)
            page.evaluate("window.scrollBy(0, 300)")
            page.wait_for_timeout(2_000)
            log(f"  Title: {page.title()[:80]}")
            log(f"  MTOP so far: {len(result['mtop_responses']) - mtop_before}")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw53_after_load.png"))
        except Exception as e:
            result["errors"].append(f"load: {e}")
            log(f"  Load error: {e}")

        # ── Phase 3: TMD detection ────────────────────────────────────
        log("=== Phase 3: TMD detection ===")
        tmd_frames = []
        try:
            # Look for TMD iframes in main frame
            for frame in page.frames:
                if is_tmd(frame.url):
                    tmd_frames.append(frame)
            log(f"  TMD iframes: {len(tmd_frames)}")
            for i, f in enumerate(tmd_frames):
                log(f"    [{i}] {f.url[:150]}")
        except Exception as e:
            log(f"  TMD scan error: {e}")

        # ── Phase 4: reCAPTCHA bypass ─────────────────────────────────
        log("=== Phase 4: reCAPTCHA bypass ===")
        if tmd_frames:
            result["tmd_handled"] = True
            tmd_frame = tmd_frames[0]
            log(f"  TMD frame: {tmd_frame.url[:150]}")

            # Wait for reCAPTCHA to appear — search nested frames
            log("  Waiting for reCAPTCHA widget (40s, nested search)...")
            recaptcha_found = False
            deadline = time.time() + TMD_WAIT / 1000
            while time.time() < deadline and not recaptcha_found:
                try:
                    # Search in TMD frame and its children
                    found_frame, found_sel = find_recaptcha_frame(tmd_frame)
                    if found_frame:
                        log(f"  reCAPTCHA found! selector={found_sel}")
                        # Click the checkbox
                        el = found_frame.query_selector(found_sel)
                        if el:
                            el.click()
                            log("  Clicked reCAPTCHA checkbox")
                            recaptcha_found = True
                            result["recaptcha_solved"] = True
                        else:
                            # Try JS click
                            found_frame.evaluate(f"document.querySelector('{found_sel}')?.click()")
                            log("  JS-clicked reCAPTCHA checkbox")
                            recaptcha_found = True
                            result["recaptcha_solved"] = True
                except Exception as e:
                    log(f"  reCAPTCHA search: {e}")
                page.wait_for_timeout(2_000)

            if not recaptcha_found:
                # Try alternative: look for reCAPTCHA in ALL frames
                log("  Trying all frames for reCAPTCHA...")
                for frame in page.frames:
                    try:
                        for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                                     "input[type='checkbox']",
                                     "iframe[src*='recaptcha']",
                                     "iframe[src*='google.com']"]:
                            el = frame.query_selector(sel)
                            if el and el.is_visible():
                                log(f"  Found in frame {frame.url[:80]}: {sel}")
                                el.click()
                                recaptcha_found = True
                                result["recaptcha_solved"] = True
                                break
                    except Exception:
                        pass
                    if recaptcha_found:
                        break

            if not recaptcha_found:
                # Last resort: try clicking the TMD frame itself (some TMD versions
                # just need a click to pass)
                log("  Trying direct TMD frame click...")
                try:
                    tmd_el = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                    if tmd_el:
                        tmd_el.click()
                        page.wait_for_timeout(5_000)
                        # Check if TMD iframe is gone
                        still_tmd = [f for f in page.frames if is_tmd(f.url)]
                        if len(still_tmd) < len(tmd_frames):
                            log("  TMD frame reduced — possible pass")
                            result["recaptcha_solved"] = True
                except Exception as e:
                    log(f"  TMD click: {e}")

            # Wait for TMD to resolve
            log("  Waiting 10s for TMD resolution...")
            page.wait_for_timeout(10_000)
            remaining_tmd = [f for f in page.frames if is_tmd(f.url)]
            log(f"  TMD iframes remaining: {len(remaining_tmd)}")
            if len(remaining_tmd) == 0:
                log("  TMD resolved!")
                result["tmd_handled"] = True
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw53_after_tmd.png"))
        else:
            log("  No TMD detected — proceeding")

        # ── Phase 5: Reload & capture PDP MTOP ───────────────────────
        log("=== Phase 5: Reload & capture PDP ===")
        mtop_before_reload = len(result["mtop_responses"])
        try:
            page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(RELOAD_WAIT)
            page.evaluate("window.scrollBy(0, 300)")
            page.wait_for_timeout(3_000)
            new_mtop = len(result["mtop_responses"]) - mtop_before_reload
            log(f"  After reload: {new_mtop} new MTOP")
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw53_after_reload.png"))
        except Exception as e:
            result["errors"].append(f"reload: {e}")
            log(f"  Reload error: {e}")

        # ── Phase 6: DOM extraction ───────────────────────────────────
        log("=== Phase 6: DOM extraction ===")
        result["dom_data"] = extract_dom(page)
        d = result["dom_data"]
        log(f"  Title: {(d.get('og:title') or d.get('title_tag') or '')[:80]}")
        price_cands = d.get("price_candidates", [])
        price_texts = d.get("price_texts_currency", [])
        log(f"  Price candidates: {len(price_cands)}")
        for pc in price_cands[:5]:
            log(f"    [{pc['sel']}] {pc['text']}")
        log(f"  Currency texts: {price_texts[:5]}")
        log(f"  Availability: {d.get('availability', 'none')}")
        log(f"  Images: {d.get('img_count', 0)}")
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "diag_ali_pw53_final.png"))

        # ── Phase 7: JS state ─────────────────────────────────────────
        log("=== Phase 7: JS state ===")
        result["js_state"] = extract_js_state(page)
        js = result["js_state"]
        for key in ["initial_data", "runParams", "dida", "next"]:
            if key in js and js[key]:
                log(f"  {key}: {len(js[key])} chars")
        if "jsonld" in js and js["jsonld"]:
            log(f"  JSON-LD: {len(js['jsonld'])} blocks")
            for i, ld in enumerate(js["jsonld"][:3]):
                log(f"    [{i}] type={ld.get('@type','?')} name={str(ld.get('name',''))[:60]}")

        # ── Parse PDP MTOP data ───────────────────────────────────────
        log("=== Phase 8: Parse PDP MTOP ===")
        for rec in result["mtop_responses"]:
            if is_pdp_mtop(rec["url"]) and rec.get("saved_to") and os.path.exists(rec["saved_to"]):
                try:
                    with open(rec["saved_to"], "r", encoding="utf-8", errors="replace") as f:
                        raw = f.read()
                    # Strip JSONP wrapper
                    m = re.search(r"^\(?\s*(\{.*\})\s*\)?$", raw, re.DOTALL)
                    if m:
                        data = json.loads(m.group(1))
                        result["pdp_data"] = data
                        log(f"  PDP parsed: {len(raw)} bytes, keys={list(data.keys())[:10]}")
                        # Dig for product info
                        if "data" in data:
                            d2 = data["data"]
                            if isinstance(d2, dict):
                                log(f"  data keys: {list(d2.keys())[:15]}")
                                # Price
                                for pk in ["price", "Price", "skuPrice", "priceInfo"]:
                                    if pk in d2:
                                        log(f"  {pk}: {json.dumps(d2[pk])[:200]}")
                                # Title
                                for tk in ["title", "Title", "subject"]:
                                    if tk in d2:
                                        log(f"  {tk}: {str(d2[tk])[:100]}")
                except Exception as e:
                    log(f"  PDP parse error: {e}")
                    result["errors"].append(f"pdp_parse: {e}")
            elif is_pdp_mtop(rec["url"]) and rec.get("body_head"):
                log(f"  PDP body_head: {rec['body_head'][:200]}")

        result["phase"] = "done"
        browser.close()

    # ── Save ──────────────────────────────────────────────────────────
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    size = os.path.getsize(OUT_JSON)
    log(f"=== DONE ===")
    log(f"  MTOP total: {len(result['mtop_responses'])}")
    log(f"  TMD: {result['tmd_handled']}, reCAPTCHA: {result['recaptcha_solved']}")
    log(f"  PDP keys: {list(result['pdp_data'].keys())[:10] if result['pdp_data'] else 'empty'}")
    log(f"  DOM title: {(result['dom_data'].get('og:title') or '')[:80]}")
    log(f"  DOM price: {result['dom_data'].get('price_candidates', ['none'])[:3]}")
    log(f"  Errors: {result['errors']}")
    log(f"  Result: {OUT_JSON} ({size} B)")


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
