#!/usr/bin/env python3
"""diag_ali_pw48.py — AliExpress deep extraction.
Improvements over pw46/pw47:
  1. Network response interception (capture MTOP from browser JS)
  2. 20s CSR render wait before DOM extraction
  3. Better reCAPTCHA Enterprise handling (poll token, retry)
  4. Post-reCAPTCHA DOM re-extraction
  5. Mobile URL fallback
  6. JS state dump (DCData, runParams, globalData, etc.)
"""

import json, re, time, traceback, sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
PRODUCT_ID   = "1005011813323763"
PRODUCT_URL  = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
MOBILE_URL   = f"https://m.aliexpress.com/item/{PRODUCT_ID}.html"
HOME_URL     = "https://www.aliexpress.com"
CAT_URL      = "https://www.aliexpress.com/category/phones-accessories.html"
PROFILE      = "pw_ali_profile_pw48"
OUT_DIR      = Path(r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12")
OUT_JSON     = OUT_DIR / "diag_ali_pw48_result.json"
OUT_LOG      = OUT_DIR / "diag_ali_pw48_log.txt"

# ── Logging ─────────────────────────────────────────────────────────
log_lines = []
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line, flush=True)
    try:
        OUT_LOG.write_text("\n".join(log_lines), encoding="utf-8")
    except Exception:
        pass

# ── Helpers ─────────────────────────────────────────────────────────
def extract_dom(page, label=""):
    """Extract product data from current DOM."""
    d = {}
    try:
        html = page.content()
        d["html_len"] = len(html)

        # meta og
        for prop in ["og:title", "og:image", "og:description"]:
            try:
                el = page.locator(f'meta[property="{prop}"]')
                d[f"og_{prop.split(':')[1]}"] = (el.get_attribute("content") or "") if el.count() else ""
            except Exception:
                d[f"og_{prop.split(':')[1]}"] = ""

        # h1
        try:
            d["h1"] = page.locator("h1").first.inner_text(timeout=2000).strip() if page.locator("h1").count() else ""
        except Exception:
            d["h1"] = ""

        # Prices — broad selectors
        prices = set()
        for sel in ["[class*='price']", "[class*='Price']", "[data-role='price']",
                     ".pdp-price", "[class*='sku-price']", "[class*='sale-price']"]:
            try:
                for el in page.locator(sel).all()[:15]:
                    txt = el.inner_text(timeout=800).strip()
                    if txt and re.search(r'[\d.,]+', txt) and len(txt) < 120:
                        prices.add(txt)
            except Exception:
                pass
        d["prices"] = list(prices)

        # Images
        imgs = []
        try:
            for el in page.locator("img").all()[:40]:
                src = el.get_attribute("src") or el.get_attribute("data-src") or ""
                if "ae-pic" in src or "aliexpress-media" in src:
                    imgs.append(src)
        except Exception:
            pass
        d["images"] = list(dict.fromkeys(imgs))

        # Stock / availability
        stock = set()
        for sel in ["[class*='stock']", "[class*='Stock']", "[class*='availability']",
                     "[class*='in-stock']", "[class*='out-stock']", "[class*='quantity']"]:
            try:
                for el in page.locator(sel).all()[:8]:
                    txt = el.inner_text(timeout=800).strip()
                    if txt and len(txt) < 200:
                        stock.add(txt)
            except Exception:
                pass
        d["stock_texts"] = list(stock)

        # Breadcrumbs
        bc = []
        try:
            for el in page.locator("[class*='breadcrumb'] a, nav[aria-label='breadcrumb'] a, [class*='Breadcrumb'] a").all()[:12]:
                txt = el.inner_text(timeout=800).strip()
                if txt:
                    bc.append(txt)
        except Exception:
            pass
        d["breadcrumbs"] = bc

        # Seller
        try:
            d["seller"] = ""
            for sel in ["[class*='store-name']", "[class*='seller-name']", "[class*='shop-name']",
                         "[class*='StoreName']", "[class*='SellerName']"]:
                els = page.locator(sel).all()
                if els:
                    d["seller"] = els[0].inner_text(timeout=800).strip()
                    break
        except Exception:
            d["seller"] = ""

        # TMD / iframe count
        tmd = 0
        total_iframes = 0
        try:
            for fr in page.locator("iframe").all():
                total_iframes += 1
                src = fr.get_attribute("src") or ""
                if "tmd" in src or "punish" in src:
                    tmd += 1
        except Exception:
            pass
        d["tmd_iframes"] = tmd
        d["iframe_count"] = total_iframes

    except Exception as e:
        d["error"] = str(e)

    if label:
        log(f"  DOM({label}): html={d.get('html_len',0)}, h1={d.get('h1','')[:60]!r}, "
            f"prices={len(d.get('prices',[]))}, imgs={len(d.get('images',[]))}, "
            f"tmd={d.get('tmd_iframes',0)}")
    return d

def extract_js_state(page):
    """Dump JS globals that may hold product data."""
    try:
        return page.evaluate("""() => {
            const out = {};
            const safe = (fn) => { try { fn(); } catch(e) { out._err = (out._err||[]) + [e.message]; } };

            safe(() => {
                if (window._d_c_ && window._d_c_.DCData)
                    out.DCData = JSON.parse(JSON.stringify(window._d_c_.DCData));
            });
            safe(() => {
                if (window.runParams)
                    out.runParams = JSON.parse(JSON.stringify(window.runParams));
            });
            safe(() => {
                if (window.globalData)
                    out.globalData = JSON.parse(JSON.stringify(window.globalData));
            });
            safe(() => {
                if (window.__INITIAL_STATE__)
                    out.__INITIAL_STATE__ = JSON.parse(JSON.stringify(window.__INITIAL_STATE__));
            });
            safe(() => {
                if (window.productData)
                    out.productData = JSON.parse(JSON.stringify(window.productData));
            });
            safe(() => {
                if (window._dida)
                    out._dida = JSON.parse(JSON.stringify(window._dida));
            });
            safe(() => {
                if (window.__NUXT__)
                    out.__NUXT__ = JSON.parse(JSON.stringify(window.__NUXT__));
            });
            safe(() => {
                if (window._d_c_) {
                    out._d_c_keys = Object.keys(window._d_c_);
                    out._d_c_viewName = window._d_c_.viewName || '';
                    out._d_c_isCSR = window._d_c_.isCSR;
                }
            });
            safe(() => {
                const keys = Object.keys(window).filter(k => {
                    try {
                        const v = window[k];
                        return v && typeof v === 'object' && !Array.isArray(v)
                            && k !== 'document' && k !== 'navigator' && k !== 'location'
                            && k !== 'chrome' && k !== 'caches' && k !== 'performance';
                    } catch(e) { return false; }
                });
                out.window_keys = keys.slice(0, 60);
            });
            return out;
        }""")
    except Exception as e:
        return {"error": str(e)}

# ── Main ────────────────────────────────────────────────────────────
def main():
    from playwright.sync_api import sync_playwright

    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "product_id": PRODUCT_ID,
        "phase": "init",
        "tracks": {},
        "network": [],
        "network_count": 0,
        "final_data": {},
        "errors": [],
    }

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(OUT_DIR / PROFILE),
            headless=False,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Europe/Rome",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ── Network interception ──
        captured = []
        def on_response(resp):
            try:
                url = resp.url
                interesting = any(k in url.lower() for k in
                    ["mtop", "pdp", "product", "detail", "query", "sku", "price"])
                if not interesting:
                    return
                body = ""
                try:
                    body = resp.text()
                except Exception:
                    body = f"(binary status={resp.status})"
                entry = {
                    "url": url[:400],
                    "status": resp.status,
                    "body_len": len(body),
                    "body_preview": body[:3000],
                }
                captured.append(entry)
                log(f"  NET: {resp.status} len={len(body)} {url[:100]}")
            except Exception:
                pass
        page.on("response", on_response)

        # ── Warmup ──
        log("Warmup: homepage")
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        log(f"  cookies: {len(ctx.cookies())}")

        log("Warmup: category")
        page.goto(CAT_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        log(f"  cookies: {len(ctx.cookies())}")

        # ── Product page ──
        log(f"Product: {PRODUCT_URL}")
        captured.clear()
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)

        # Wait for CSR render
        log("Waiting 20s for CSR render…")
        page.wait_for_timeout(20000)

        final_url = page.url
        page_title = page.title()
        log(f"  url: {final_url}")
        log(f"  title: {page_title!r}")
        log(f"  cookies: {len(ctx.cookies())}")

        # ── Track 1: DOM (post-render) ──
        log("Track 1: DOM extraction (post-render)")
        dom = extract_dom(page, "post-render")
        result["tracks"]["dom"] = {"status": "done", "detail": dom}

        # ── Track 2: JS state ──
        log("Track 2: JS state")
        js = extract_js_state(page)
        # Trim large fields for readability
        for k in list(js.keys()):
            if isinstance(js[k], dict) and len(json.dumps(js[k])) > 5000:
                js[k] = {kk: vv for kk, vv in list(js[k].items())[:20]}
                js[k]["_truncated"] = True
        result["tracks"]["js_state"] = {"status": "done", "detail": js}

        # ── Track 3: reCAPTCHA ──
        log("Track 3: reCAPTCHA Enterprise")
        rec = {}
        try:
            tmd_frame = None
            google_frame = None
            for fr in page.frames:
                u = (fr.url or "").lower()
                if "tmd" in u or "punish" in u:
                    tmd_frame = fr
                    log(f"  TMD frame: {fr.url[:120]}")
                if "google.com/recaptcha" in u:
                    google_frame = fr
                    log(f"  Google frame: {fr.url[:120]}")

            rec["tmd_frame"] = tmd_frame is not None
            rec["google_frame"] = google_frame is not None
            rec["frames_total"] = len(page.frames)

            if tmd_frame or google_frame:
                log("  Waiting 8s for reCAPTCHA to fully load…")
                page.wait_for_timeout(8000)

                # Find and click checkbox
                clicked = False
                for target in [google_frame, tmd_frame]:
                    if not target:
                        continue
                    for sel in ['input[type="checkbox"]',
                                'span.recaptcha-checkbox',
                                'div.recaptcha-checkbox',
                                '[class*="checkbox"]',
                                '.rc-checkbox']:
                        try:
                            el = target.locator(sel).first
                            if el.is_visible(timeout=2000):
                                el.click()
                                clicked = True
                                log(f"  Clicked '{sel}' in frame")
                                break
                        except Exception:
                            pass
                    if clicked:
                        break

                # Also try main page
                if not clicked:
                    try:
                        el = page.locator('iframe[src*="recaptcha"] input[type="checkbox"]').first
                        if el.is_visible(timeout=2000):
                            el.click()
                            clicked = True
                            log("  Clicked checkbox (main page → iframe)")
                    except Exception:
                        pass

                rec["clicked"] = clicked

                # Poll for token (30s)
                log("  Polling for token (30s)…")
                token = ""
                for i in range(15):
                    page.wait_for_timeout(2000)
                    # Check main page
                    try:
                        token = page.evaluate("""() => {
                            try {
                                if (typeof grecaptcha !== 'undefined') {
                                    const t = grecaptcha.getResponse();
                                    if (t && t.length > 10) return t;
                                }
                            } catch(e) {}
                            return '';
                        }""")
                    except Exception:
                        pass
                    # Check TMD frame
                    if not token and tmd_frame:
                        try:
                            token = tmd_frame.evaluate("""() => {
                                try {
                                    if (typeof grecaptcha !== 'undefined') {
                                        const t = grecaptcha.getResponse();
                                        if (t && t.length > 10) return t;
                                    }
                                } catch(e) {}
                                return '';
                            }""")
                        except Exception:
                            pass
                    # Check Google frame
                    if not token and google_frame:
                        try:
                            token = google_frame.evaluate("""() => {
                                try {
                                    if (typeof parent !== 'undefined' && parent.grecaptcha) {
                                        const t = parent.grecaptcha.getResponse();
                                        if (t && t.length > 10) return t;
                                    }
                                } catch(e) {}
                                return '';
                            }""")
                        except Exception:
                            pass

                    if token:
                        log(f"  Token found (poll {i+1}): {token[:30]}…")
                        break

                rec["token_found"] = bool(token)
                rec["token_preview"] = token[:50] if token else ""
                rec["polls"] = i + 1

                # After token → wait for MTOP retry
                if token:
                    log("  Token OK, waiting 12s for MTOP retry…")
                    page.wait_for_timeout(12000)

                    # Try clicking retry / reload buttons
                    for sel in ["[class*='retry']", "[class*='Retry']",
                                 "button:has-text('Retry')", "button:has-text('reload')",
                                 "[class*='reload']"]:
                        try:
                            btn = page.locator(sel).first
                            if btn.is_visible(timeout=1500):
                                btn.click()
                                log(f"  Clicked retry: {sel}")
                                page.wait_for_timeout(8000)
                                break
                        except Exception:
                            pass
            else:
                log("  No TMD/Google frames found")

        except Exception as e:
            rec["error"] = str(e)
            result["errors"].append(f"recaptcha: {e}")

        result["tracks"]["recaptcha"] = {"status": "done", "detail": rec}

        # ── Track 4: Post-reCAPTCHA DOM ──
        log("Track 4: Post-reCAPTCHA DOM")
        post_dom = extract_dom(page, "post-recaptcha")
        result["tracks"]["post_dom"] = {"status": "done", "detail": post_dom}

        # ── Track 5: Mobile URL ──
        log("Track 5: Mobile URL")
        mob = {}
        try:
            captured.clear()
            page.goto(MOBILE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(10000)
            mob = extract_dom(page, "mobile")
            mob["final_url"] = page.url
            mob["page_title"] = page.title()
        except Exception as e:
            mob["error"] = str(e)
            result["errors"].append(f"mobile: {e}")
        result["tracks"]["mobile"] = {"status": "done", "detail": mob}

        # ── Network dump ──
        result["network"] = captured[-60:]
        result["network_count"] = len(captured)

        # ── Merge ──
        log("Merging final data…")
        d1 = result["tracks"]["dom"]["detail"]
        d2 = result["tracks"]["post_dom"]["detail"]
        dm = result["tracks"]["mobile"]["detail"]
        net = result.get("network", [])

        # Title
        title = ""
        for candidate in [d2.get("h1", ""), d2.get("og_title", ""),
                          d1.get("h1", ""), d1.get("og_title", ""),
                          dm.get("h1", ""), dm.get("og_title", "")]:
            if candidate and len(candidate) > 10:
                title = candidate
                break

        # Price
        price, currency = "", ""
        all_prices = d2.get("prices", []) + d1.get("prices", []) + dm.get("prices", [])
        for p in all_prices:
            m = re.search(r'([\d.,]+)\s*([A-Z]{2,4}|€|\$|£)?', p)
            if m and float(m.group(1).replace(",", "")) > 0.5:
                price = m.group(1)
                currency = m.group(2) or ""
                break

        # Also scan network for MTOP JSON with price
        mtop_found = False
        for nr in net:
            body = nr.get("body_preview", "")
            if "mtop" in nr.get("url", "").lower() and "price" in body.lower():
                try:
                    parsed = json.loads(body) if body.startswith("{") else {}
                    if parsed:
                        result["mtop_network"] = parsed
                        mtop_found = True
                        log(f"  MTOP network hit: {nr['url'][:80]}")
                except Exception:
                    pass

        # Stock
        stock = ""
        for src in [d2.get("stock_texts", []), d1.get("stock_texts", []), dm.get("stock_texts", [])]:
            if src:
                stock = src[0]
                break

        # Category
        category = ""
        for src in [d2.get("breadcrumbs", []), d1.get("breadcrumbs", []), dm.get("breadcrumbs", [])]:
            if src:
                category = " > ".join(src)
                break

        # Seller
        seller = d2.get("seller", "") or d1.get("seller", "") or dm.get("seller", "")

        # Images
        all_imgs = list(dict.fromkeys(
            d2.get("images", []) + d1.get("images", []) + dm.get("images", [])
        ))
        product_imgs = [i for i in all_imgs
                        if not re.search(r'/(30x30|48x48|16x16|24x24|120x120)\.', i)]

        final = {
            "title": title,
            "price": price,
            "currency": currency,
            "stock": stock,
            "category": category,
            "seller": seller,
            "images": product_imgs[:20],
            "sku_count": 0,
            "source": "merged" if (price and title) else ("dom" if title else "meta_og"),
            "html_len": d1.get("html_len", 0),
            "tmd_iframes": d1.get("tmd_iframes", 0),
            "network_hits": len(net),
            "mtop_in_network": mtop_found,
        }
        result["final_data"] = final
        result["phase"] = "done"

        # ── Save ──
        OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        sz = OUT_JSON.stat().st_size
        log(f"Saved → {OUT_JSON} ({sz} bytes)")

        # ── Summary ──
        log("=== FINAL ===")
        log(f"  title:    {title[:90]}")
        log(f"  price:    {price} {currency}")
        log(f"  stock:    {stock[:60]}")
        log(f"  images:   {len(product_imgs)}")
        log(f"  category: {category[:60]}")
        log(f"  seller:   {seller[:60]}")
        log(f"  source:   {final['source']}")
        log(f"  net_hits: {len(net)}, mtop: {mtop_found}")

        ctx.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(f"FATAL:\n{traceback.format_exc()}")
        sys.exit(1)
