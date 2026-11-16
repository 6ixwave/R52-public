#!/usr/bin/env python
"""
pw55: AliExpress PDP — reuse pw53 profile + US region + deep reCAPTCHA
Key fixes:
  - Reuse pw_ali_profile_pw53 (warm cookies, no TMD on first run)
  - US via aep_usuc_f cookie + gatewayAdapt param (no .us redirect)
  - Deep frame traversal for reCAPTCHA (TMD → iframe → checkbox)
  - 30s TMD resolution wait + retry
  - JSONP strip + PDP deep-walk
"""

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime

from playwright.sync_api import sync_playwright

# ── Config ──────────────────────────────────────────────────────────────
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html?gatewayAdapt=glo2usa"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "pw_ali_profile_pw53")  # REUSE pw53 profile
OUT_JSON = os.path.join(BASE_DIR, "diag_ali_pw55_result.json")

NAV_TIMEOUT = 50_000
WAIT_AFTER_LOAD = 15_000
TMD_WAIT = 35_000
RELOAD_WAIT = 15_000

result = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "region": "US (via cookie + gatewayAdapt)",
    "profile": "pw53 (reused)",
    "timestamp": datetime.now().isoformat(),
    "phase": "init",
    "mtop_responses": [],
    "pdp_raw_len": 0,
    "pdp_parsed": {},
    "dom_data": {},
    "js_state": {},
    "tmd_handled": False,
    "recaptcha_solved": False,
    "big_boss_ban": False,
    "errors": [],
}

_mtop_idx = 0


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def safe_body(response):
    try:
        return response.body()
    except Exception:
        return None


def is_mtop(url):
    return ("acs.aliexpress.com" in url or "recom-acs.aliexpress.com" in url) and "mtop" in url.lower()


def is_pdp_mtop(url):
    return "pdp.pc.query" in url or "pdp.msite" in url


def strip_jsonp(raw):
    raw = raw.strip()
    m = re.match(r'^\s*(\w+)\((\{.*\})\)\s*;?\s*$', raw, re.DOTALL)
    if m:
        return m.group(2)
    if raw.startswith("{"):
        return raw
    return raw


def on_response(response):
    global _mtop_idx
    url = response.url
    status = response.status
    if not is_mtop(url):
        return
    body = safe_body(response)
    body_len = 0
    body_head = ""
    if body:
        body_len = len(body)
        body_head = body[:2000].decode("utf-8", errors="replace")
    rec = {"url": url[:300], "status": status, "body_len": body_len, "saved_to": "", "body_head": body_head}
    # Save PDP
    if is_pdp_mtop(url) and body and body_len > 200:
        tag = "pdp_pc" if "pdp.pc.query" in url else "pdp_msite"
        fname = f"mtop_pw55_{tag}_{PRODUCT_ID[-6:]}.json"
        fpath = os.path.join(BASE_DIR, fname)
        try:
            with open(fpath, "wb") as f:
                f.write(body)
            rec["saved_to"] = fpath
            log(f"  PDP saved: {fname} ({body_len} B)")
        except Exception as e:
            rec["body_head"] += f" [save err: {e}]"
    result["mtop_responses"].append(rec)
    _mtop_idx += 1
    log(f"  MTOP[{_mtop_idx}] {status} {url[:110]}")
    log(f"    body: {body_len} B")


def deep_find_recaptcha(frame, depth=0, max_depth=5):
    """Recursively find reCAPTCHA checkbox in nested frames."""
    if depth > max_depth:
        return None, None, None
    prefix = "  " * depth
    try:
        # Check current frame for checkbox
        for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                     "input[type='checkbox'][aria-label]",
                     "span[role='checkbox']",
                     "div[role='checkbox']"]:
            el = frame.query_selector(sel)
            if el:
                try:
                    if el.is_visible():
                        log(f"{prefix}[d{depth}] Found checkbox: {sel}")
                        return frame, el, sel
                except Exception:
                    pass
        # Check for reCAPTCHA iframe (go deeper)
        if depth < max_depth:
            iframes = frame.query_selector_all("iframe")
            for iframe_el in iframes:
                try:
                    src = iframe_el.get_attribute("src") or ""
                    if "recaptcha" in src.lower() or "google.com" in src.lower():
                        log(f"{prefix}[d{depth}] reCAPTCHA iframe: {src[:100]}")
                        # Get the frame object
                        child_frames = frame.child_frames
                        for cf in child_frames:
                            if "recaptcha" in cf.url.lower() or "google.com" in cf.url.lower():
                                log(f"{prefix}[d{depth}] → child frame: {cf.url[:100]}")
                                return deep_find_recaptcha(cf, depth + 1, max_depth)
                        # Try waiting for the iframe to load
                        frame.wait_for_timeout(2000)
                        for cf in frame.child_frames:
                            if "recaptcha" in cf.url.lower() or "google.com" in cf.url.lower():
                                return deep_find_recaptcha(cf, depth + 1, max_depth)
                except Exception:
                    pass
            # Also check all child frames generically
            for cf in frame.child_frames:
                if depth < max_depth:
                    r = deep_find_recaptcha(cf, depth + 1, max_depth)
                    if r[0]:
                        return r
    except Exception as e:
        log(f"{prefix}[d{depth}] scan error: {e}")
    return None, None, None


def extract_dom(page):
    dom = {}
    try:
        dom["og:title"] = page.evaluate("document.querySelector('meta[property=\"og:title\"]')?.content || ''")
        dom["og:image"] = page.evaluate("document.querySelector('meta[property=\"og:image\"]')?.content || ''")
        dom["title_tag"] = page.title()

        # Price
        price_selectors = [
            ".price-current .price-text", ".price-current .current-price",
            "[class*='price'] [class*='current']", "[class*='Price'] [class*='Current']",
            ".product-price .price", ".price-wrap .price",
            ".sku-price .price", ".sku-price .current",
            ".detail-price .price", "[data-role='price']",
            ".price-box .price", "[class*='price'] span", "[class*='Price'] span",
            ".h5-price", ".price-int", ".price-dec",
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

        # Currency scan
        try:
            price_texts = page.evaluate("""
                () => {
                    const results = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const t = walker.currentNode.textContent.trim();
                        if (t && (t.includes('$') || t.includes('\\u20ac') || t.includes('\\u00a5') || t.includes('R$') || t.includes('\\u20bd')) && t.length < 50) {
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

        # Availability
        for sel in ["[class*='stock']", "[class*='Stock']", "[class*='availability']", "[data-role='stock']"]:
            try:
                el = page.query_selector(sel)
                if el:
                    txt = el.inner_text().strip()
                    if txt:
                        dom["availability"] = txt
                        break
            except Exception:
                pass

        # Images
        try:
            imgs = page.evaluate("""
                () => {
                    const urls = new Set();
                    const og = document.querySelector('meta[property="og:image"]');
                    if (og) urls.add(og.content);
                    document.querySelectorAll('.gallery img, [class*="gallery"] img, .img-wrap img, .product-img img').forEach(img => {
                        const src = img.src || img.dataset.src || '';
                        if (src && src.includes('aliexpress-media')) urls.add(src);
                    });
                    document.querySelectorAll('img[src*="aliexpress-media"]').forEach(img => {
                        const src = img.src || '';
                        if (src && src.match(/\\d{2,}x\\d{2,}/)) urls.add(src);
                    });
                    return [...urls].slice(0, 30);
                }
            """)
            dom["img_count"] = len(imgs)
            dom["images"] = imgs
        except Exception as e:
            dom["img_error"] = str(e)

        # Breadcrumb
        try:
            bc = page.query_selector_all(".breadcrumb a, [class*='breadcrumb'] a, nav[aria-label] a")
            cats = [a.inner_text().strip() for a in bc if a.inner_text().strip()]
            if cats:
                dom["breadcrumb"] = cats
        except Exception:
            pass

    except Exception as e:
        dom["error"] = str(e)
    return dom


def extract_js_state(page):
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


def parse_pdp(raw_text):
    data = {}
    try:
        json_str = strip_jsonp(raw_text)
        parsed = json.loads(json_str)
        data["api"] = parsed.get("api", "")
        data["ret"] = parsed.get("ret", [])
        data["v"] = parsed.get("v", "")

        d = parsed.get("data", {})
        res = d.get("result", d) if isinstance(d, dict) else d

        # GLOBAL_DATA
        gd = res.get("GLOBAL_DATA", {}) if isinstance(res, dict) else {}
        gdata = gd.get("globalData", {}) if isinstance(gd, dict) else {}
        data["bigBossBan"] = gdata.get("bigBossBan", False)
        data["errorCode"] = gdata.get("errorCode", "")
        data["bigBossBanTip"] = gdata.get("bigBossBanTip", "")

        # Walk for prices
        price_found = []
        def find_prices(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("price", "minprice", "maxprice", "saleprice", "originalprice",
                                     "formatprice", "priceinfo", "skuprice", "promotionprice"):
                        price_found.append({"path": f"{path}.{k}", "val": str(v)[:300]})
                    find_prices(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:10]):
                    find_prices(item, f"{path}[{i}]")
            elif isinstance(obj, str) and re.search(r"[$\u20ac\u00a5]|\\d+\\.\\d{2}", obj) and len(obj) < 50:
                price_found.append({"path": path, "val": obj})
        find_prices(d)
        data["prices_found"] = price_found[:20]

        # Walk for titles
        title_found = []
        def find_titles(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("title", "subject", "name", "productname") and isinstance(v, str) and len(v) > 10:
                        title_found.append({"path": f"{path}.{k}", "val": v[:300]})
                    find_titles(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:5]):
                    find_titles(item, f"{path}[{i}]")
        find_titles(d)
        data["titles_found"] = title_found[:10]

        # SKU
        sku_info = None
        for key in ["skuInfo", "SKU_INFO", "sku", "SKU"]:
            sku_info = d.get(key) or (res.get(key) if isinstance(res, dict) else None)
            if sku_info:
                break
        if sku_info:
            data["sku_info"] = json.dumps(sku_info, ensure_ascii=False)[:3000]

        # Images
        img_list = None
        for key in ["imageList", "IMAGE_LIST", "images", "galleryImages"]:
            img_list = d.get(key) or (res.get(key) if isinstance(res, dict) else None)
            if img_list:
                break
        if img_list:
            data["image_list"] = img_list[:20]

        data["data_keys"] = list(d.keys())[:25] if isinstance(d, dict) else []
        data["result_keys"] = list(res.keys())[:25] if isinstance(res, dict) else []

    except json.JSONDecodeError as e:
        data["parse_error"] = f"JSON: {e}"
    except Exception as e:
        data["parse_error"] = str(e)
    return data


def try_load_product(page, url, label=""):
    """Load product page and return (success, tmd_count)."""
    log(f"  [{label}] Loading: {url[:100]}")
    try:
        page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(WAIT_AFTER_LOAD)
        page.evaluate("window.scrollBy(0, 300)")
        page.wait_for_timeout(3_000)
        tmd_count = 0
        for frame in page.frames:
            if "_____tmd_____" in frame.url or "punish" in frame.url:
                tmd_count += 1
        log(f"  [{label}] Title: {page.title()[:80]}")
        log(f"  [{label}] URL: {page.url[:120]}")
        log(f"  [{label}] TMD frames: {tmd_count}")
        return tmd_count == 0, tmd_count
    except Exception as e:
        log(f"  [{label}] Error: {e}")
        return False, 0


def handle_tmd(page):
    """Attempt to solve TMD/reCAPTCHA. Returns True if resolved."""
    log("  Handling TMD...")
    result["tmd_handled"] = True

    # Find all TMD frames
    tmd_frames = [f for f in page.frames if "_____tmd_____" in f.url or "punish" in f.url]
    log(f"  TMD frames: {len(tmd_frames)}")

    for tmd_frame in tmd_frames:
        log(f"  TMD: {tmd_frame.url[:120]}")

        # Deep search for reCAPTCHA checkbox
        found_frame, checkbox_el, sel = deep_find_recaptcha(tmd_frame)
        if found_frame and checkbox_el:
            log(f"  Clicking checkbox ({sel}) in frame {found_frame.url[:80]}")
            try:
                checkbox_el.click(timeout=5000)
                log("  Clicked!")
                result["recaptcha_solved"] = True
            except Exception as e:
                log(f"  Click failed: {e}, trying JS click")
                try:
                    found_frame.evaluate(f"document.querySelector('{sel}')?.click()")
                    log("  JS clicked!")
                    result["recaptcha_solved"] = True
                except Exception as e2:
                    log(f"  JS click failed: {e2}")
        else:
            log("  No reCAPTCHA checkbox found in TMD frames")
            # Try: some TMD versions resolve after just waiting
            log("  Waiting 15s for auto-resolve...")
            page.wait_for_timeout(15_000)

    # Wait for TMD to clear
    log(f"  Waiting {TMD_WAIT // 1000}s for TMD resolution...")
    deadline = time.time() + TMD_WAIT / 1000
    while time.time() < deadline:
        remaining = [f for f in page.frames if "_____tmd_____" in f.url or "punish" in f.url]
        if len(remaining) == 0:
            log("  TMD cleared!")
            return True
        page.wait_for_timeout(3_000)

    remaining = [f for f in page.frames if "_____tmd_____" in f.url or "punish" in f.url]
    log(f"  TMD still present: {len(remaining)}")
    return len(remaining) == 0


def main():
    log("=== pw55: AliExpress PDP — pw53 profile + US + deep reCAPTCHA ===")
    log(f"Product: {PRODUCT_ID}")
    log(f"URL: {PRODUCT_URL}")
    log(f"Profile: {os.path.basename(PROFILE_DIR)} (reused)")

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.on("response", on_response)

        # ── Phase 1: Force US region cookie ──────────────────────────
        log("=== Phase 1: US region cookie ===")
        try:
            browser.add_cookies([{
                "name": "aep_usuc_f",
                "value": "site=glo&c_tp=USD&region=US&b_locale=en_US&ae_u_p_s=2",
                "domain": ".aliexpress.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }])
            browser.add_cookies([{
                "name": "_lang",
                "value": "en_US",
                "domain": ".aliexpress.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }])
            log("  Cookies set: aep_usuc_f=US, _lang=en_US")
        except Exception as e:
            log(f"  Cookie error: {e}")

        # ── Phase 2: Quick warm-up (homepage, 5s) ────────────────────
        log("=== Phase 2: Quick warm-up ===")
        try:
            page.goto("https://www.aliexpress.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(5_000)
            cookies = browser.cookies()
            log(f"  Cookies: {len(cookies)}")
            log(f"  URL: {page.url[:100]}")
        except Exception as e:
            log(f"  Warmup error: {e}")
            result["errors"].append(f"warmup: {e}")

        # ── Phase 3: Load product page ────────────────────────────────
        log("=== Phase 3: Load product page ===")
        mtop_before = len(result["mtop_responses"])
        ok, tmd_count = try_load_product(page, PRODUCT_URL, "try1")
        log(f"  MTOP so far: {len(result['mtop_responses']) - mtop_before}")
        page.screenshot(path=os.path.join(BASE_DIR, "diag_ali_pw55_after_load.png"))

        # ── Phase 4: TMD handling ─────────────────────────────────────
        if tmd_count > 0:
            log(f"=== Phase 4: TMD handling ({tmd_count} frames) ===")
            resolved = handle_tmd(page)
            if not resolved:
                log("  TMD not resolved — trying reload...")
                try:
                    page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                    page.wait_for_timeout(WAIT_AFTER_LOAD)
                    tmd_count2 = sum(1 for f in page.frames if "_____tmd_____" in f.url or "punish" in f.url)
                    log(f"  After reload: TMD={tmd_count2}")
                    if tmd_count2 == 0:
                        resolved = True
                        log("  TMD cleared after reload!")
                except Exception as e:
                    log(f"  Reload error: {e}")
            page.screenshot(path=os.path.join(BASE_DIR, "diag_ali_pw55_after_tmd.png"))
        else:
            log("=== Phase 4: No TMD — proceeding ===")

        # ── Phase 5: DOM extraction ───────────────────────────────────
        log("=== Phase 5: DOM extraction ===")
        result["dom_data"] = extract_dom(page)
        d = result["dom_data"]
        log(f"  Title: {(d.get('og:title') or d.get('title_tag') or '')[:80]}")
        log(f"  Price candidates: {d.get('price_candidates', [])[:5]}")
        log(f"  Currency texts: {d.get('price_texts_currency', [])[:5]}")
        log(f"  Availability: {d.get('availability', 'none')}")
        log(f"  Images: {d.get('img_count', 0)}")

        # ── Phase 6: JS state ─────────────────────────────────────────
        log("=== Phase 6: JS state ===")
        result["js_state"] = extract_js_state(page)
        js = result["js_state"]
        for key in ["initial_data", "runParams", "dida"]:
            if key in js and js[key]:
                log(f"  {key}: {len(js[key])} chars")
        if js.get("jsonld"):
            log(f"  JSON-LD: {len(js['jsonld'])} blocks")
            for i, ld in enumerate(js["jsonld"][:3]):
                log(f"    [{i}] type={ld.get('@type','?')} name={str(ld.get('name',''))[:60]}")

        # ── Phase 7: Parse PDP MTOP ───────────────────────────────────
        log("=== Phase 7: Parse PDP MTOP ===")
        pdp_raw = ""
        for rec in result["mtop_responses"]:
            if is_pdp_mtop(rec["url"]):
                if rec.get("saved_to") and os.path.exists(rec["saved_to"]):
                    with open(rec["saved_to"], "r", encoding="utf-8", errors="replace") as f:
                        pdp_raw = f.read()
                    log(f"  PDP file: {os.path.basename(rec['saved_to'])} ({len(pdp_raw)} B)")
                elif rec.get("body_head"):
                    pdp_raw = rec["body_head"]
                    log(f"  PDP from body_head ({len(pdp_raw)} B) — partial!")
                break

        result["pdp_raw_len"] = len(pdp_raw)
        if pdp_raw:
            result["pdp_parsed"] = parse_pdp(pdp_raw)
            pp = result["pdp_parsed"]
            log(f"  API: {pp.get('api')}")
            log(f"  ret: {pp.get('ret')}")
            log(f"  bigBossBan: {pp.get('bigBossBan')}")
            log(f"  errorCode: {pp.get('errorCode')}")
            log(f"  data_keys: {pp.get('data_keys', [])[:15]}")
            log(f"  result_keys: {pp.get('result_keys', [])[:15]}")
            log(f"  prices_found: {pp.get('prices_found', [])[:5]}")
            log(f"  titles_found: {pp.get('titles_found', [])[:3]}")
            if pp.get("sku_info"):
                log(f"  sku_info: {pp['sku_info'][:200]}")
            if pp.get("image_list"):
                log(f"  image_list: {len(pp['image_list'])} images")
            if pp.get("parse_error"):
                log(f"  PARSE ERROR: {pp['parse_error']}")
                result["errors"].append(f"pdp_parse: {pp['parse_error']}")
        else:
            log("  No PDP data captured!")
            result["errors"].append("pdp: no data captured")

        # ── Phase 8: If bigBossBan, try US-specific URL ───────────────
        if result["pdp_parsed"].get("bigBossBan"):
            log("=== Phase 8: bigBossBan — trying alternate approach ===")
            alt_url = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html?gatewayAdapt=glo2usa&spm=a2g0o.productlist"
            ok2, tmd2 = try_load_product(page, alt_url, "alt")
            if tmd2 == 0:
                result["dom_data_alt"] = extract_dom(page)
                da = result["dom_data_alt"]
                log(f"  Alt title: {(da.get('og:title') or da.get('title_tag') or '')[:80]}")
                log(f"  Alt price: {da.get('price_candidates', [])[:3]}")
                log(f"  Alt images: {da.get('img_count', 0)}")
            page.screenshot(path=os.path.join(BASE_DIR, "diag_ali_pw55_alt.png"))

        # ── Save ──────────────────────────────────────────────────────
        result["phase"] = "done"
        browser.close()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    size = os.path.getsize(OUT_JSON)
    log("=== DONE ===")
    log(f"  MTOP total: {len(result['mtop_responses'])}")
    log(f"  TMD: {result['tmd_handled']}, reCAPTCHA: {result['recaptcha_solved']}")
    log(f"  bigBossBan: {result['pdp_parsed'].get('bigBossBan')}")
    log(f"  PDP keys: {result['pdp_parsed'].get('data_keys', [])[:10]}")
    log(f"  Prices: {result['pdp_parsed'].get('prices_found', [])[:3]}")
    log(f"  Titles: {result['pdp_parsed'].get('titles_found', [])[:2]}")
    log(f"  DOM title: {(result['dom_data'].get('og:title') or '')[:80]}")
    log(f"  DOM price: {result['dom_data'].get('price_candidates', [])[:3]}")
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
