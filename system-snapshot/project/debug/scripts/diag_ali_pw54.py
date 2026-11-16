#!/usr/bin/env python
"""
pw54: AliExpress PDP — US region + JSONP parse + full extraction
Fixes vs pw53:
  - Force US region via aep_usuc_f cookie (avoids bigBossBan for IT)
  - Proper JSONP stripping (mtopjsonp1({...}))
  - saved_to tracked by index, not [-1]
  - Longer DOM wait + bigBossBan detection
  - Full PDP data walk for price/title/sku/images
"""

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Config ──────────────────────────────────────────────────────────────
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "pw_ali_profile_pw54")
OUT_JSON = os.path.join(BASE_DIR, "diag_ali_pw54_result.json")

NAV_TIMEOUT = 50_000
WAIT_AFTER_LOAD = 12_000
WAIT_DOM = 15_000

result = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "region": "US",
    "timestamp": datetime.now().isoformat(),
    "phase": "init",
    "mtop_responses": [],
    "pdp_data": {},
    "pdp_parsed": {},
    "dom_data": {},
    "js_state": {},
    "tmd_handled": False,
    "recaptcha_solved": False,
    "big_boss_ban": False,
    "errors": [],
}

# Counter for saved files
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
    return "acs.aliexpress.com" in url and "mtop" in url.lower()


def is_pdp_mtop(url):
    return "pdp.pc.query" in url or "pdp.msite" in url


def strip_jsonp(raw):
    """Strip JSONP wrapper: mtopjsonp1({...}) → {...}"""
    raw = raw.strip()
    # Pattern: callbackName({json})
    m = re.match(r'^\s*(\w+)\((\{.*\})\)\s*$', raw, re.DOTALL)
    if m:
        return m.group(2)
    # Sometimes: callbackName({json});
    m = re.match(r'^\s*(\w+)\((\{.*\})\)\s*;\s*$', raw, re.DOTALL)
    if m:
        return m.group(2)
    # Already JSON
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
    body_text = ""
    body_len = 0
    if body:
        body_len = len(body)
        body_text = body[:3000].decode("utf-8", errors="replace")
    rec = {
        "url": url[:300],
        "status": status,
        "body_len": body_len,
        "saved_to": "",
        "body_head": body_text[:500],
        "body_error": "",
    }
    # Save PDP body to file
    if is_pdp_mtop(url) and body and body_len > 200:
        tag = "pdp_pc" if "pdp.pc.query" in url else "pdp_msite"
        fname = f"mtop_pw54_{tag}_{PRODUCT_ID[-6:]}.json"
        fpath = os.path.join(BASE_DIR, fname)
        try:
            with open(fpath, "wb") as f:
                f.write(body)
            rec["saved_to"] = fpath
            log(f"  PDP saved: {fname} ({body_len} B)")
        except Exception as e:
            rec["body_error"] = f"save: {e}"
    result["mtop_responses"].append(rec)
    _mtop_idx += 1
    log(f"  MTOP[{_mtop_idx}] {status} {url[:110]}")
    if body_len:
        log(f"    body: {body_len} B")
    else:
        log(f"    body: EMPTY")


def extract_dom(page):
    dom = {}
    try:
        dom["og:title"] = page.evaluate(
            "document.querySelector('meta[property=\"og:title\"]')?.content || ''"
        )
        dom["og:image"] = page.evaluate(
            "document.querySelector('meta[property=\"og:image\"]')?.content || ''"
        )
        dom["title_tag"] = page.title()

        # Price — multiple strategies
        price_selectors = [
            ".price-current .price-text",
            ".price-current .current-price",
            "[class*='price'] [class*='current']",
            "[class*='Price'] [class*='Current']",
            ".product-price .price",
            ".price-wrap .price",
            ".sku-price .price",
            ".sku-price .current",
            ".detail-price .price",
            "[data-role='price']",
            ".price-box .price",
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

        # Currency text scan
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

        # Breadcrumb / category
        try:
            bc = page.query_selector_all(".breadcrumb a, [class*='breadcrumb'] a, nav[aria-label] a")
            cats = [a.inner_text().strip() for a in bc if a.inner_text().strip()]
            if cats:
                dom["breadcrumb"] = cats
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

        # SKU / variants
        try:
            sku_text = page.evaluate("""
                () => {
                    const els = document.querySelectorAll('[class*="sku"], [class*="Sku"], [class*="variant"], [class*="attribute"]');
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

        # BigBossBan check in DOM
        try:
            ban = page.evaluate("""
                () => {
                    const el = document.querySelector('[class*="ban"], [class*="Ban"], [class*="unavailable"], [class*="Unavailable"]');
                    return el ? el.innerText?.trim().slice(0, 200) : '';
                }
            """)
            if ban:
                dom["ban_text"] = ban
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
                if (window.__INITIAL_DATA__) {
                    out.initial_data = JSON.stringify(window.__INITIAL_DATA__).slice(0, 8000);
                }
                if (window.runParams) {
                    out.runParams = JSON.stringify(window.runParams).slice(0, 8000);
                }
                if (window._dida_config_) {
                    out.dida = JSON.stringify(window._dida_config_).slice(0, 3000);
                }
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


def parse_pdp(raw_text):
    """Parse PDP JSONP → structured product data."""
    data = {}
    try:
        json_str = strip_jsonp(raw_text)
        parsed = json.loads(json_str)
        data["api"] = parsed.get("api", "")
        data["ret"] = parsed.get("ret", [])
        data["v"] = parsed.get("v", "")

        d = parsed.get("data", {})
        res = d.get("result", d)

        # GLOBAL_DATA
        gd = res.get("GLOBAL_DATA", {})
        gdata = gd.get("globalData", {})
        data["bigBossBan"] = gdata.get("bigBossBan", False)
        data["errorCode"] = gdata.get("errorCode", "")
        data["bigBossBanTip"] = gdata.get("bigBossBanTip", "")

        # Look for product info in various locations
        # Common PDP structure: data.result.{MODULE_DATA, GLOBAL_DATA}
        # or data.result.{productInfo, skuInfo, priceInfo, ...}
        for key in res:
            if key in ("GLOBAL_DATA", "i18n"):
                continue
            val = res[key]
            if isinstance(val, dict):
                data.setdefault("modules", {})[key] = "dict"
            elif isinstance(val, str) and len(val) > 10:
                data.setdefault("fields", {})[key] = val[:200]
            else:
                data.setdefault("fields", {})[key] = str(val)[:200]

        # Deep search for price
        price_found = []
        def find_prices(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("price", "minPrice", "maxPrice", "salePrice", "originalPrice", "formatPrice"):
                        price_found.append({"path": f"{path}.{k}", "val": str(v)[:200]})
                    find_prices(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:10]):
                    find_prices(item, f"{path}[{i}]")
            elif isinstance(obj, str) and re.search(r"\\$|\\u20ac|\\u00a5|\\d+\\.\\d{2}", obj) and len(obj) < 50:
                price_found.append({"path": path, "val": obj})

        find_prices(d)
        data["prices_found"] = price_found[:15]

        # Deep search for title
        title_found = []
        def find_titles(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("title", "subject", "name") and isinstance(v, str) and len(v) > 10:
                        title_found.append({"path": f"{path}.{k}", "val": v[:200]})
                    find_titles(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:5]):
                    find_titles(item, f"{path}[{i}]")

        find_titles(d)
        data["titles_found"] = title_found[:10]

        # SKU info
        sku_info = d.get("skuInfo") or d.get("SKU_INFO") or res.get("skuInfo") or res.get("SKU_INFO")
        if sku_info:
            data["sku_info"] = json.dumps(sku_info, ensure_ascii=False)[:2000]

        # Image list
        img_list = d.get("imageList") or d.get("IMAGE_LIST") or res.get("imageList")
        if img_list:
            data["image_list"] = img_list[:15]

        # Full data size
        data["data_keys"] = list(d.keys())[:20]
        data["result_keys"] = list(res.keys())[:20]

    except json.JSONDecodeError as e:
        data["parse_error"] = f"JSON: {e}"
    except Exception as e:
        data["parse_error"] = str(e)

    return data


def main():
    log("=== pw54: AliExpress PDP — US region + JSONP + full extraction ===")
    log(f"Product: {PRODUCT_ID}")
    log(f"URL: {PRODUCT_URL}")
    log(f"Region: US (forced via cookie)")

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

        # ── Phase 1: Set US region cookie + warm-up ──────────────────
        log("=== Phase 1: US region cookie + Warm-up ===")
        try:
            # Set aep_usuc_f cookie to force US region
            context = browser
            context.add_cookies([{
                "name": "aep_usuc_f",
                "value": "[REDACTED_AUTH_SECRET]",
                "domain": ".aliexpress.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }])
            log("  Cookie aep_usuc_f set (US/USD/en_US)")

            # Also set _lang
            context.add_cookies([{
                "name": "_lang",
                "value": "[REDACTED_AUTH_SECRET]",
                "domain": ".aliexpress.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }])

            # Warm-up: homepage
            page.goto("https://www.aliexpress.com/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(6_000)
            page.evaluate("window.scrollBy(0, 500)")
            page.wait_for_timeout(2_000)
            cookies = browser.cookies()
            log(f"  Cookies after warmup: {len(cookies)}")
            log(f"  URL after redirect: {page.url[:100]}")
        except Exception as e:
            result["errors"].append(f"warmup: {e}")
            log(f"  Warmup error: {e}")

        # ── Phase 2: Load product page ────────────────────────────────
        log("=== Phase 2: Load product page ===")
        mtop_before = len(result["mtop_responses"])
        try:
            # Use gatewayAdapt to force US
            product_url_us = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html?gatewayAdapt=glo2usa"
            page.goto(product_url_us, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(WAIT_AFTER_LOAD)
            page.evaluate("window.scrollBy(0, 300)")
            page.wait_for_timeout(3_000)
            log(f"  Title: {page.title()[:100]}")
            log(f"  URL: {page.url[:120]}")
            log(f"  MTOP so far: {len(result['mtop_responses']) - mtop_before}")
            page.screenshot(path=os.path.join(BASE_DIR, "diag_ali_pw54_after_load.png"))
        except Exception as e:
            result["errors"].append(f"load: {e}")
            log(f"  Load error: {e}")

        # ── Phase 3: TMD detection ────────────────────────────────────
        log("=== Phase 3: TMD detection ===")
        tmd_frames = []
        try:
            for frame in page.frames:
                if "_____tmd_____" in frame.url or "punish" in frame.url:
                    tmd_frames.append(frame)
            log(f"  TMD iframes: {len(tmd_frames)}")
            for i, f in enumerate(tmd_frames):
                log(f"    [{i}] {f.url[:150]}")
        except Exception as e:
            log(f"  TMD scan error: {e}")

        # ── Phase 4: reCAPTCHA bypass (if TMD) ────────────────────────
        log("=== Phase 4: reCAPTCHA bypass ===")
        if tmd_frames:
            result["tmd_handled"] = True
            tmd_frame = tmd_frames[0]
            log(f"  TMD frame: {tmd_frame.url[:150]}")

            # Search nested frames for reCAPTCHA
            recaptcha_found = False
            deadline = time.time() + 30
            while time.time() < deadline and not recaptcha_found:
                for frame in [tmd_frame] + tmd_frame.child_frames:
                    try:
                        for sel in ["#recaptcha-anchor", ".recaptcha-checkbox",
                                     "input[type='checkbox']",
                                     "iframe[src*='recaptcha']",
                                     "iframe[src*='google.com/recaptcha']"]:
                            el = frame.query_selector(sel)
                            if el and el.is_visible():
                                log(f"  reCAPTCHA found: {sel} in {frame.url[:60]}")
                                el.click()
                                recaptcha_found = True
                                result["recaptcha_solved"] = True
                                break
                    except Exception:
                        pass
                    if recaptcha_found:
                        break
                if not recaptcha_found:
                    page.wait_for_timeout(2_000)

            # Wait for TMD to resolve
            page.wait_for_timeout(10_000)
            remaining = [f for f in page.frames if "_____tmd_____" in f.url or "punish" in f.url]
            log(f"  TMD remaining: {len(remaining)}")
            page.screenshot(path=os.path.join(BASE_DIR, "diag_ali_pw54_after_tmd.png"))
        else:
            log("  No TMD — proceeding")

        # ── Phase 5: DOM extraction (before reload) ───────────────────
        log("=== Phase 5: DOM extraction ===")
        result["dom_data"] = extract_dom(page)
        d = result["dom_data"]
        log(f"  Title: {(d.get('og:title') or d.get('title_tag') or '')[:80]}")
        log(f"  Price candidates: {d.get('price_candidates', [])[:3]}")
        log(f"  Currency texts: {d.get('price_texts_currency', [])[:5]}")
        log(f"  Availability: {d.get('availability', 'none')}")
        log(f"  Images: {d.get('img_count', 0)}")
        log(f"  Ban text: {d.get('ban_text', 'none')[:80]}")

        # ── Phase 6: JS state ─────────────────────────────────────────
        log("=== Phase 6: JS state ===")
        result["js_state"] = extract_js_state(page)
        js = result["js_state"]
        for key in ["initial_data", "runParams", "dida"]:
            if key in js and js[key]:
                log(f"  {key}: {len(js[key])} chars")
        if "jsonld" in js and js["jsonld"]:
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
                    log(f"  PDP file: {rec['saved_to']} ({len(pdp_raw)} B)")
                elif rec.get("body_head"):
                    pdp_raw = rec["body_head"]
                    log(f"  PDP from body_head ({len(pdp_raw)} B) — partial!")
                break

        if pdp_raw:
            result["pdp_parsed"] = parse_pdp(pdp_raw)
            pp = result["pdp_parsed"]
            log(f"  API: {pp.get('api')}")
            log(f"  ret: {pp.get('ret')}")
            log(f"  bigBossBan: {pp.get('bigBossBan')}")
            log(f"  errorCode: {pp.get('errorCode')}")
            log(f"  data_keys: {pp.get('data_keys')}")
            log(f"  result_keys: {pp.get('result_keys')}")
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

        # ── Phase 8: Reload & second capture (if first was ban) ───────
        if result["pdp_parsed"].get("bigBossBan"):
            log("=== Phase 8: bigBossBan detected — trying reload ===")
            try:
                page.reload(timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(WAIT_DOM)
                page.evaluate("window.scrollBy(0, 300)")
                page.wait_for_timeout(3_000)
                # Re-extract
                result["dom_data_reload"] = extract_dom(page)
                dr = result["dom_data_reload"]
                log(f"  Reload title: {(dr.get('og:title') or dr.get('title_tag') or '')[:80]}")
                log(f"  Reload price: {dr.get('price_candidates', [])[:3]}")
                log(f"  Reload images: {dr.get('img_count', 0)}")
                page.screenshot(path=os.path.join(BASE_DIR, "diag_ali_pw54_reload.png"))
            except Exception as e:
                result["errors"].append(f"reload: {e}")
                log(f"  Reload error: {e}")

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
    log(f"  PDP data_keys: {result['pdp_parsed'].get('data_keys', [])[:10]}")
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
