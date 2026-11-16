"""
diag_ali_pw35.py — AliExpress: reCAPTCHA Enterprise (anchor) auto-resolve
Key insight: TMD frame contains a NESTED google.com recaptcha iframe.
The "anchor" type auto-executes. We poll g-recaptcha-response for a token.
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright", "pw_ali_profile")
RESULT = os.path.join(WORK, "diag_ali_pw35_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw35",
    "strategies": {},
    "final_data": None,
    "errors": [],
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def parse_mtop(body):
    body = body.strip()
    if body.startswith("mtopjsonp1("):
        body = body[len("mtopjsonp1("):-1].rstrip(")")
    return json.loads(body)

def build_mtop_url(token, data_str, app_key="12574478"):
    t = str(int(time.time() * 1000))
    sign_src = f"{token}&{t}&{app_key}&{data_str}"
    sign = hashlib.md5(sign_src.encode()).hexdigest()
    return (
        f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
        f"?jsv=2.5.1&appKey={app_key}&t={t}&sign={sign}"
        f"&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp"
        f"&v=1.0&timeout=15000&dataType=originaljsonp"
        f"&callback=mtopjsonp1&data={urllib.parse.quote(data_str)}"
    )

def mtop_payload(cookie_dict):
    return {
        "productId": PRODUCT_ID,
        "_lang": "en_US",
        "_currency": "USD",
        "country": "US",
        "province": "",
        "city": "",
        "channel": "",
        "pdp_ext_f": "",
        "pdpNPI": "",
        "sourceType": "",
        "clientType": "pc",
        "ext": json.dumps({
            "foreverRandomToken": cookie_dict.get("foreverRandomToken", ""),
            "site": "glo",
            "crawler": False,
            "x-m-biz-bx-region": "",
            "signedIn": False,
            "host": "www.aliexpress.com"
        })
    }

def extract_dom(page):
    """Extract product data from rendered DOM"""
    data = {}
    try:
        h1 = page.query_selector("h1")
        data["h1"] = h1.inner_text().strip() if h1 else None
    except:
        data["h1"] = None
    try:
        og_t = page.query_selector("meta[property='og:title']")
        data["og_title"] = og_t.get_attribute("content") if og_t else None
    except:
        data["og_title"] = None
    try:
        price_els = page.query_selector_all("[class*='price'], .pdp-price, [class*='Price'], [data-role='price'], .comet-price")
        prices = []
        for pe in price_els[:15]:
            txt = pe.inner_text().strip()
            if txt and len(txt) < 100:
                prices.append(txt)
        data["prices"] = prices
    except:
        data["prices"] = []
    try:
        jsonld_s = page.query_selector_all("script[type='application/ld+json']")
        jsonld_items = []
        for s in jsonld_s:
            try:
                txt = s.inner_text().strip()
                if txt:
                    jsonld_items.append(json.loads(txt))
            except:
                pass
        data["jsonld"] = jsonld_items
    except:
        data["jsonld"] = []
    try:
        imgs = []
        for img in page.query_selector_all("img[src*='aliexpress-media'], img[src*='alicdn']"):
            src = img.get_attribute("src") or ""
            if src and src not in imgs:
                imgs.append(src)
        data["images"] = imgs[:20]
    except:
        data["images"] = []
    try:
        data["title_tag"] = page.title()
    except:
        data["title_tag"] = ""
    return data

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 768},
            user_agent=UA,
            locale="en-US",
            timezone_id="Europe/London",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── WARMUP ──
        log("WARMUP: main page")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        cookies = browser.cookies()
        log(f"  cookies: {len(cookies)}")

        # ── LOAD PRODUCT ──
        log("LOAD: product page")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(10)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""
        log(f"  cookies: {len(cookies)}, token={token[:20]}...")

        # ── CHECK IF TMD APPEARED ──
        tmd_if = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
        has_tmd = tmd_if is not None
        log(f"  TMD iframe present: {has_tmd}")

        if has_tmd:
            log("TMD DETECTED — entering reCAPTCHA Enterprise flow")
            src = tmd_if.get_attribute("src") or ""
            log(f"  TMD src: {src[:150]}...")

            # ── STEP 1: Get outer TMD frame ──
            time.sleep(3)
            outer_frame = tmd_if.content_frame
            if callable(outer_frame):
                outer_frame = outer_frame()
            log(f"  outer_frame type: {type(outer_frame).__name__}")

            if outer_frame:
                # ── STEP 2: Find google recaptcha iframe INSIDE TMD frame ──
                time.sleep(3)
                rc_ifs = outer_frame.query_selector_all("iframe[src*='google.com/recaptcha'], iframe[src*='recaptcha']")
                log(f"  recaptcha iframes in TMD: {len(rc_ifs)}")

                # Also check for the g-recaptcha-response textarea in TMD frame
                rc_textarea = outer_frame.query_selector("#g-recaptcha-response, textarea[name='g-recaptcha-response']")
                log(f"  g-recaptcha-response textarea: {rc_textarea is not None}")

                # ── STEP 3: Wait for anchor recaptcha to auto-execute ──
                # The "anchor" type auto-runs. Poll the textarea for a token.
                log("  Waiting for reCAPTCHA Enterprise (anchor) auto-execute...")
                rc_token = ""
                for attempt in range(15):  # up to 45s (3s intervals)
                    time.sleep(3)
                    try:
                        if rc_textarea:
                            val = rc_textarea.input_value()
                            if val and len(val) > 10:
                                rc_token = val
                                log(f"  *** reCAPTCHA token received (attempt {attempt+1})! len={len(val)} ***")
                                break
                    except Exception as e:
                        log(f"  poll error: {e}")

                    # Also check if TMD iframe disappeared (auto-resolved)
                    try:
                        tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                        if not tmd_still:
                            log(f"  TMD iframe gone (attempt {attempt+1}) — auto-resolved!")
                            rc_token = "AUTO_RESOLVED"
                            break
                    except:
                        pass

                    log(f"  poll {attempt+1}: no token yet...")

                # ── STEP 4: If no auto-token, try clicking recaptcha area ──
                if not rc_token:
                    log("  No auto-token. Trying manual click on recaptcha...")
                    try:
                        # Click the recaptcha iframe area
                        if rc_ifs:
                            rc_box = rc_ifs[0].bounding_box()
                            if rc_box:
                                cx = rc_box["x"] + rc_box["width"] / 2
                                cy = rc_box["y"] + rc_box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked recaptcha center: ({cx:.0f},{cy:.0f})")
                                time.sleep(8)

                        # Check textarea again
                        for attempt in range(5):
                            time.sleep(3)
                            try:
                                if rc_textarea:
                                    val = rc_textarea.input_value()
                                    if val and len(val) > 10:
                                        rc_token = val
                                        log(f"  *** Token after click (attempt {attempt+1})! ***")
                                        break
                            except:
                                pass

                        # Check if TMD gone
                        tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                        if not tmd_still:
                            rc_token = "AUTO_RESOLVED_AFTER_CLICK"
                            log("  TMD resolved after click!")
                    except Exception as e:
                        log(f"  click error: {e}")

                out["strategies"]["recaptcha"] = {
                    "tmd_present": True,
                    "rc_iframes": len(rc_ifs),
                    "token_received": bool(rc_token),
                    "token_preview": rc_token[:50] if rc_token else None,
                    "auto_resolved": "AUTO" in rc_token,
                }
                log(f"  reCAPTCHA strategy: token={bool(rc_token)}, auto={('AUTO' in rc_token)}")

            # ── STEP 5: After TMD, wait for page re-render ──
            log("  Waiting 8s for page re-render after TMD...")
            time.sleep(8)

            # Check if page reloaded (URL might change)
            try:
                log(f"  Current URL: {page.url}")
            except:
                pass

        else:
            log("No TMD — direct extraction")
            out["strategies"]["recaptcha"] = {"tmd_present": False}

        # ── STRATEGY A: DOM extraction ──
        log("A: DOM extraction")
        dom_data = extract_dom(page)
        out["strategies"]["dom"] = dom_data
        log(f"  h1: {dom_data.get('h1')}")
        log(f"  prices: {dom_data.get('prices', [])[:5]}")
        log(f"  jsonld: {len(dom_data.get('jsonld', []))} items")
        log(f"  images: {len(dom_data.get('images', []))}")

        has_good_dom = (dom_data.get("h1") and dom_data.get("prices")) or dom_data.get("jsonld")
        if has_good_dom:
            log("  *** GOOD DOM DATA! ***")
            out["final_data"] = {"source": "dom", "data": dom_data}

        # ── STRATEGY B: MTOP retry via browser fetch ──
        if not out["final_data"]:
            log("B: MTOP retry via page.evaluate (browser fetch)")
            try:
                cookies2 = browser.cookies()
                cookie_dict2 = {c["name"]: c["value"] for c in cookies2}
                token2 = cookie_dict2.get("_m_h5_tk", "").split("_")[0]
                data_str2 = json.dumps(mtop_payload(cookie_dict2), separators=(",", ":"))
                mtop_url2 = build_mtop_url(token2, data_str2)

                mtop_result = page.evaluate("""
                    async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include', headers: {'Accept': '*/*'}});
                            return {status: r.status, text: await r.text()};
                        } catch(e) {
                            return {status: 0, error: e.message};
                        }
                    }
                """, mtop_url2)

                mstatus = mtop_result.get("status", 0)
                mtext = mtop_result.get("text", "")
                merr = mtop_result.get("error", "")
                log(f"  fetch: status={mstatus}, len={len(mtext)}, err={merr}")

                if mtext:
                    mjson = parse_mtop(mtext)
                    mret = mjson.get("ret", [])
                    log(f"  ret={mret}")

                    if mret and "SUCCESS" in str(mret[0]):
                        log("  *** BROWSER MTOP SUCCESS! ***")
                        out["strategies"]["mtop_browser"] = {"success": True, "ret": mret}
                        out["final_data"] = {"source": "mtop", "data": mjson}
                    else:
                        data = mjson.get("data", {})
                        out["strategies"]["mtop_browser"] = {
                            "success": False, "ret": mret,
                            "data_keys": list(data.keys())[:20] if data else [],
                            "is_tmd": "tmd" in str(data.get("url", "")) or "punish" in str(data.get("url", "")),
                        }
                        # If MTOP succeeded with data, extract product fields
                        if data and not out["strategies"]["mtop_browser"]["is_tmd"]:
                            # Try to find product info in MTOP data
                            product_info = data.get("productInfo", data.get("product", data.get("itemInfo", {})))
                            out["strategies"]["mtop_browser"]["product_info_keys"] = list(product_info.keys())[:30] if product_info else []
                            # Save full data for analysis
                            out["strategies"]["mtop_browser"]["data_preview"] = json.dumps(data)[:3000]
                elif merr:
                    out["strategies"]["mtop_browser"] = {"success": False, "error": merr}
            except Exception as e:
                log(f"  browser MTOP error: {e}")
                out["strategies"]["mtop_browser"] = {"error": str(e)}
                out["errors"].append(f"mtop_browser: {e}")

        # ── STRATEGY C: MTOP via curl_cffi (fresh cookies after TMD) ──
        if not out["final_data"]:
            log("C: MTOP via curl_cffi (post-TMD cookies)")
            try:
                from curl_cffi import requests as cffi_req
                cookies3 = browser.cookies()
                cookie_dict3 = {c["name"]: c["value"] for c in cookies3}
                token3 = cookie_dict3.get("_m_h5_tk", "").split("_")[0]
                data_str3 = json.dumps(mtop_payload(cookie_dict3), separators=(",", ":"))
                mtop_url3 = build_mtop_url(token3, data_str3)

                headers = {
                    "User-Agent": UA,
                    "Referer": PRODUCT_URL,
                    "Origin": "https://www.aliexpress.com",
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                }
                resp = cffi_req.get(mtop_url3, headers=headers, cookies=cookie_dict3, impersonate="chrome", timeout=20)
                body = resp.text
                log(f"  status={resp.status_code}, len={len(body)}")

                mjson = parse_mtop(body)
                mret = mjson.get("ret", [])
                log(f"  ret={mret}")

                if mret and "SUCCESS" in str(mret[0]):
                    log("  *** CFFI MTOP SUCCESS! ***")
                    out["strategies"]["mtop_cffi"] = {"success": True, "ret": mret}
                    out["final_data"] = {"source": "mtop_cffi", "data": mjson}
                else:
                    data = mjson.get("data", {})
                    out["strategies"]["mtop_cffi"] = {
                        "success": False, "ret": mret,
                        "data_keys": list(data.keys())[:20] if data else [],
                    }
            except Exception as e:
                log(f"  cffi error: {e}")
                out["strategies"]["mtop_cffi"] = {"error": str(e)}
                out["errors"].append(f"mtop_cffi: {e}")

        # ── STRATEGY D: Final fallback (meta + page state) ──
        if not out["final_data"]:
            log("D: Fallback — meta + images + page_config + body text")
            try:
                metas = {}
                for m in page.query_selector_all("meta"):
                    prop = m.get_attribute("property") or m.get_attribute("name") or ""
                    content = m.get_attribute("content") or ""
                    if prop:
                        metas[prop] = content

                page_config = page.evaluate("window._page_config_ || null")
                body_text = page.inner_text("body")[:12000]
                price_matches = re.findall(r'[\$€£]\s?\d+[\.,]?\d*', body_text)

                fb = {
                    "source": "meta_fallback",
                    "og_title": metas.get("og:title", ""),
                    "og_image": metas.get("og:image", ""),
                    "og_description": metas.get("og:description", ""),
                    "images": dom_data.get("images", []),
                    "prices_found": price_matches[:15],
                    "page_config": page_config,
                    "body_snippet": body_text[:5000],
                    "title_tag": page.title(),
                    "runParams": page.evaluate("window.runParams || '{}'"),
                }
                out["strategies"]["fallback"] = fb
                out["final_data"] = fb
                log(f"  og_title={fb['og_title'][:70]}")
                log(f"  prices={fb['prices_found'][:5]}")
                log(f"  images={len(fb['images'])}")
            except Exception as e:
                log(f"  Fallback error: {e}")
                out["errors"].append(f"fallback: {e}")

        # ── SAVE PAGE STATE FOR DEBUG ──
        try:
            page_html = page.content()
            with open(os.path.join(WORK, "diag_ali_pw35_page.html"), "w", encoding="utf-8") as f:
                f.write(page_html)
            log(f"  Page HTML saved: {len(page_html)} bytes")
        except:
            pass

        browser.close()

except Exception as e:
    log(f"MAIN ERROR: {e}")
    traceback.print_exc()
    out["errors"].append(f"main: {e}")

with open(RESULT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log(f"Saved: {RESULT} ({os.path.getsize(RESULT)} bytes)")
log("DONE")
