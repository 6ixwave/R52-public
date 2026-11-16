"""
diag_ali_pw37.py — AliExpress: TMD bypass + MTOP intercept + DOM extraction
Fixes from pw36:
- content_frame is PROPERTY in Playwright 1.64 (not callable)
- page.on("response") intercepts page's own MTOP calls
- reCAPTCHA Enterprise ANCHOR: 40s auto-resolve window
- 5 strategies: intercept > DOM > MTOP-PC > MTOP-Mobile > fallback
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback, shutil
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(WORK, "pw_ali_profile_pw37")
RESULT = os.path.join(WORK, "diag_ali_pw37_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw37",
    "strategies": {},
    "intercepted_mtop": [],
    "final_data": None,
    "errors": [],
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def parse_mtop(body):
    body = body.strip()
    for prefix in ["mtopjsonp1(", "mtopjsonp2(", "mtopjsonp("]:
        if body.startswith(prefix):
            body = body[len(prefix):]
            if body.endswith(")"):
                body = body[:-1]
            break
    return json.loads(body)

def extract_dom(page):
    data = {}
    for key, sel, method in [
        ("h1", "h1", "text"),
        ("og_title", "meta[property='og:title']", "attr"),
        ("og_image", "meta[property='og:image']", "attr"),
        ("og_description", "meta[property='og:description']", "attr"),
        ("title_tag", None, "title"),
    ]:
        try:
            if method == "title":
                data[key] = page.title()
            elif method == "text":
                el = page.query_selector(sel)
                data[key] = el.inner_text().strip() if el else None
            elif method == "attr":
                el = page.query_selector(sel)
                data[key] = el.get_attribute("content") if el else None
        except:
            data[key] = None
    try:
        price_els = page.query_selector_all(
            "[class*='price'], .pdp-price, [class*='Price'], "
            "[data-role='price'], .comet-price, [class*='sku-price'], "
            "[class*='current-price'], [class*='sale-price'], "
            "[class*='price-wrap'], [class*='price-box']"
        )
        prices = []
        for pe in price_els[:30]:
            try:
                txt = pe.inner_text().strip()
                if txt and len(txt) < 100 and re.search(r'[\d$€£]', txt):
                    prices.append(txt)
            except:
                pass
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
            try:
                src = img.get_attribute("src") or ""
                if src and src not in imgs and not src.endswith(('.gif', '.svg')):
                    imgs.append(src)
            except:
                pass
        data["images"] = imgs[:20]
    except:
        data["images"] = []
    try:
        sku_els = page.query_selector_all("[class*='sku'], [class*='SKU'], [class*='stock'], [class*='availability']")
        sku_texts = []
        for se in sku_els[:20]:
            try:
                txt = se.inner_text().strip()
                if txt and len(txt) < 200:
                    sku_texts.append(txt)
            except:
                pass
        data["sku_texts"] = sku_texts
    except:
        data["sku_texts"] = []
    return data

# ── FRESH PROFILE ──
if os.path.exists(PROFILE):
    log(f"Removing old profile: {PROFILE}")
    shutil.rmtree(PROFILE, ignore_errors=True)
    time.sleep(1)
log(f"Fresh profile: {PROFILE}")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 768},
            user_agent=UA,
            locale="en-US",
            timezone_id="America/New_York",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── RESPONSE INTERCEPTOR ──
        mtop_responses = []

        def on_response(response):
            try:
                url = response.url
                if "mtop.aliexpress" in url or "acs.aliexpress.com" in url:
                    body = ""
                    try:
                        body = response.text()
                    except:
                        pass
                    info = {
                        "url": url[:300],
                        "status": response.status,
                        "body_len": len(body),
                        "body": body[:5000],
                    }
                    mtop_responses.append(info)
                    log(f"  [INTERCEPT] {url[:100]}... status={response.status} len={len(body)}")
            except Exception as e:
                pass

        page.on("response", on_response)

        # ── WARMUP ──
        log("WARMUP 1: homepage")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)
        cookies = browser.cookies()
        log(f"  cookies: {len(cookies)}")

        log("WARMUP 2: category page")
        page.goto("https://www.aliexpress.com/category/50903.html", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        cookies = browser.cookies()
        log(f"  cookies: {len(cookies)}")

        # ── LOAD PRODUCT ──
        log("LOAD: product page")
        mtop_responses.clear()
        try:
            page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=40000)
        except:
            log("  domcontentloaded timeout, continuing...")
        time.sleep(10)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""
        log(f"  cookies: {len(cookies)}, token={token[:20]}...")
        log(f"  URL: {page.url}")
        log(f"  MTOP intercepted so far: {len(mtop_responses)}")

        # ── CHECK TMD ──
        tmd_if = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
        has_tmd = tmd_if is not None
        log(f"  TMD present: {has_tmd}")

        if has_tmd:
            log("=== TMD FLOW ===")
            src = tmd_if.get_attribute("src") or ""
            log(f"  TMD src: {src[:150]}...")
            time.sleep(5)

            # content_frame is PROPERTY in Playwright 1.64
            try:
                outer_frame = tmd_if.content_frame
                log(f"  outer_frame type: {type(outer_frame).__name__}")
            except Exception as e:
                log(f"  content_frame error: {e}")
                outer_frame = None

            if outer_frame:
                time.sleep(3)
                try:
                    rc_ifs = outer_frame.query_selector_all(
                        "iframe[src*='google.com/recaptcha'], iframe[src*='recaptcha'], iframe[title='reCAPTCHA']"
                    )
                except:
                    rc_ifs = []
                log(f"  recaptcha iframes: {len(rc_ifs)}")

                rc_textarea = None
                for i in range(5):
                    try:
                        rc_textarea = outer_frame.query_selector(
                            "#g-recaptcha-response, textarea[name='g-recaptcha-response'], textarea.g-recaptcha-response"
                        )
                    except:
                        pass
                    if rc_textarea:
                        log(f"  textarea found on attempt {i+1}")
                        break
                    time.sleep(2)

                if not rc_textarea:
                    log("  Searching all frames for textarea...")
                    for fr in browser.frames:
                        try:
                            rc_textarea = fr.query_selector("#g-recaptcha-response")
                            if rc_textarea:
                                log(f"  textarea in frame: {fr.url[:80]}")
                                break
                        except:
                            pass

                # Poll for reCAPTCHA Enterprise ANCHOR auto-token (40s)
                log("  Polling for reCAPTCHA anchor token (40s max)...")
                rc_token = ""
                for attempt in range(20):
                    time.sleep(2)
                    try:
                        if rc_textarea:
                            val = rc_textarea.input_value()
                            if val and len(val) > 10:
                                rc_token = val
                                log(f"  *** TOKEN! (attempt {attempt+1}) len={len(val)} ***")
                                break
                    except:
                        pass
                    try:
                        tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                        if not tmd_still:
                            rc_token = "AUTO_GONE"
                            log(f"  TMD gone (attempt {attempt+1})")
                            break
                    except:
                        pass
                    if attempt % 5 == 4:
                        log(f"  poll {attempt+1}: still waiting...")

                if not rc_token:
                    log("  No auto-token. Trying click...")
                    try:
                        if rc_ifs:
                            rc_box = rc_ifs[0].bounding_box()
                            if rc_box:
                                cx = rc_box["x"] + 30
                                cy = rc_box["y"] + rc_box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked: ({cx:.0f},{cy:.0f})")
                                time.sleep(5)
                        else:
                            tmd_box = tmd_if.bounding_box()
                            if tmd_box:
                                cx = tmd_box["x"] + tmd_box["width"] / 2
                                cy = tmd_box["y"] + tmd_box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked TMD center: ({cx:.0f},{cy:.0f})")
                                time.sleep(5)
                        for attempt in range(10):
                            time.sleep(2)
                            try:
                                if rc_textarea:
                                    val = rc_textarea.input_value()
                                    if val and len(val) > 10:
                                        rc_token = val
                                        log(f"  *** TOKEN after click! (attempt {attempt+1}) ***")
                                        break
                            except:
                                pass
                            try:
                                tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                                if not tmd_still:
                                    rc_token = "AUTO_GONE_AFTER_CLICK"
                                    break
                            except:
                                pass
                    except Exception as e:
                        log(f"  click error: {e}")

                out["strategies"]["recaptcha"] = {
                    "tmd_present": True,
                    "rc_iframes": len(rc_ifs) if rc_ifs else 0,
                    "textarea_found": rc_textarea is not None,
                    "token_received": bool(rc_token),
                    "token_preview": rc_token[:60] if rc_token else None,
                    "auto_resolved": "AUTO" in rc_token if rc_token else False,
                }

            log("  Waiting 5s then reloading...")
            time.sleep(5)
            mtop_responses.clear()
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
                time.sleep(12)
                log(f"  Reloaded. URL: {page.url}")
            except Exception as e:
                log(f"  Reload error: {e}")

        else:
            log("No TMD — direct extraction")
            out["strategies"]["recaptcha"] = {"tmd_present": False}

        # ── STRATEGY A: Intercepted MTOP ──
        log(f"A: Checking intercepted MTOP ({len(mtop_responses)} responses)")
        for i, mr in enumerate(mtop_responses):
            if mr["body_len"] > 200:
                try:
                    mj = parse_mtop(mr["body"])
                    ret = mj.get("ret", [])
                    log(f"  MTOP[{i}]: ret={ret}")
                    if ret and "SUCCESS" in str(ret[0]):
                        log(f"  *** MTOP[{i}] SUCCESS! ***")
                        out["strategies"]["mtop_intercept"] = {
                            "success": True, "index": i, "ret": ret,
                            "data_keys": list(mj.get("data", {}).keys())[:30],
                        }
                        out["final_data"] = {"source": "mtop_intercept", "data": mj}
                        break
                except Exception as e:
                    log(f"  MTOP[{i}] parse error: {e}")

        # ── STRATEGY B: DOM ──
        if not out["final_data"]:
            log("B: DOM extraction")
            dom_data = extract_dom(page)
            out["strategies"]["dom"] = dom_data
            log(f"  h1: {dom_data.get('h1')}")
            log(f"  prices: {dom_data.get('prices', [])[:5]}")
            log(f"  jsonld: {len(dom_data.get('jsonld', []))}")
            log(f"  images: {len(dom_data.get('images', []))}")
            has_good = (dom_data.get("h1") and dom_data.get("prices")) or dom_data.get("jsonld")
            if has_good:
                log("  *** GOOD DOM! ***")
                out["final_data"] = {"source": "dom", "data": dom_data}

        # ── STRATEGY C: MTOP PC via browser fetch ──
        if not out["final_data"]:
            log("C: MTOP PC via browser fetch")
            try:
                cookies2 = browser.cookies()
                cd2 = {c["name"]: c["value"] for c in cookies2}
                tok2 = cd2.get("_m_h5_tk", "").split("_")[0]
                data_str = json.dumps({
                    "productId": PRODUCT_ID,
                    "_lang": "en_US", "_currency": "USD", "country": "US",
                    "province": "", "city": "", "channel": "",
                    "pdp_ext_f": "", "pdpNPI": "", "sourceType": "",
                    "clientType": "pc",
                    "ext": json.dumps({
                        "foreverRandomToken": cd2.get("foreverRandomToken", ""),
                        "site": "glo", "crawler": False,
                        "x-m-biz-bx-region": "", "signedIn": False,
                        "host": "www.aliexpress.com"
                    })
                }, separators=(",", ":"))
                t = str(int(time.time() * 1000))
                app_key = "12574478"
                sign_src = f"{tok2}&{t}&{app_key}&{data_str}"
                sign = hashlib.md5(sign_src.encode()).hexdigest()
                murl = (
                    f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
                    f"?jsv=2.5.1&appKey={app_key}&t={t}&sign={sign}"
                    f"&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp"
                    f"&v=1.0&timeout=15000&dataType=originaljsonp"
                    f"&callback=mtopjsonp1&data={urllib.parse.quote(data_str)}"
                )
                mres = page.evaluate("""
                    async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            return {status: r.status, text: await r.text()};
                        } catch(e) { return {status: 0, error: e.message}; }
                    }
                """, murl)
                mtext = mres.get("text", "")
                log(f"  status={mres.get('status')}, len={len(mtext)}")
                if mtext:
                    mj = parse_mtop(mtext)
                    mr = mj.get("ret", [])
                    log(f"  ret={mr}")
                    if mr and "SUCCESS" in str(mr[0]):
                        log("  *** PC MTOP SUCCESS ***")
                        out["final_data"] = {"source": "mtop_pc", "data": mj}
                    else:
                        d = mj.get("data", {})
                        out["strategies"]["mtop_pc"] = {
                            "success": False, "ret": mr,
                            "data_keys": list(d.keys())[:20] if d else [],
                        }
            except Exception as e:
                log(f"  PC MTOP error: {e}")
                out["strategies"]["mtop_pc"] = {"error": str(e)}

        # ── STRATEGY D: MTOP Mobile via browser fetch ──
        if not out["final_data"]:
            log("D: MTOP Mobile via browser fetch")
            try:
                cookies3 = browser.cookies()
                cd3 = {c["name"]: c["value"] for c in cookies3}
                tok3 = cd3.get("_m_h5_tk", "").split("_")[0]
                data_str = json.dumps({
                    "productId": PRODUCT_ID,
                    "_lang": "en_US", "_currency": "USD", "country": "US",
                    "channel": "", "sourceType": "", "clientType": "mobile",
                    "ext": json.dumps({
                        "foreverRandomToken": cd3.get("foreverRandomToken", ""),
                        "site": "glo", "crawler": False,
                        "x-m-biz-bx-region": "", "signedIn": False,
                        "host": "m.aliexpress.com"
                    })
                }, separators=(",", ":"))
                t = str(int(time.time() * 1000))
                app_key = "12574478"
                sign_src = f"{tok3}&{t}&{app_key}&{data_str}"
                sign = hashlib.md5(sign_src.encode()).hexdigest()
                murl = (
                    f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.mobile.query/1.0/"
                    f"?jsv=2.5.1&appKey={app_key}&t={t}&sign={sign}"
                    f"&api=mtop.aliexpress.pdp.mobile.query&type=originaljsonp"
                    f"&v=1.0&timeout=15000&dataType=originaljsonp"
                    f"&callback=mtopjsonp1&data={urllib.parse.quote(data_str)}"
                )
                mres = page.evaluate("""
                    async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            return {status: r.status, text: await r.text()};
                        } catch(e) { return {status: 0, error: e.message}; }
                    }
                """, murl)
                mtext = mres.get("text", "")
                log(f"  status={mres.get('status')}, len={len(mtext)}")
                if mtext:
                    mj = parse_mtop(mtext)
                    mr = mj.get("ret", [])
                    log(f"  ret={mr}")
                    if mr and "SUCCESS" in str(mr[0]):
                        log("  *** MOBILE MTOP SUCCESS ***")
                        out["final_data"] = {"source": "mtop_mobile", "data": mj}
                    else:
                        d = mj.get("data", {})
                        out["strategies"]["mtop_mobile"] = {
                            "success": False, "ret": mr,
                            "data_keys": list(d.keys())[:20] if d else [],
                        }
            except Exception as e:
                log(f"  Mobile MTOP error: {e}")
                out["strategies"]["mtop_mobile"] = {"error": str(e)}

        # ── STRATEGY E: Fallback ──
        if not out["final_data"]:
            log("E: Fallback")
            try:
                metas = {}
                for m in page.query_selector_all("meta"):
                    prop = m.get_attribute("property") or m.get_attribute("name") or ""
                    content = m.get_attribute("content") or ""
                    if prop:
                        metas[prop] = content
                body_text = page.inner_text("body")[:12000]
                price_matches = re.findall(r'[\$€£]\s?\d+[\.,]?\d*', body_text)
                fb = {
                    "source": "meta_fallback",
                    "og_title": metas.get("og:title", ""),
                    "og_image": metas.get("og:image", ""),
                    "og_description": metas.get("og:description", ""),
                    "images": out["strategies"].get("dom", {}).get("images", []),
                    "prices_found": price_matches[:15],
                    "body_snippet": body_text[:5000],
                    "title_tag": page.title(),
                }
                out["strategies"]["fallback"] = fb
                out["final_data"] = fb
                log(f"  og_title={fb['og_title'][:70]}")
                log(f"  prices={fb['prices_found'][:5]}")
            except Exception as e:
                log(f"  Fallback error: {e}")

        # ── SAVE ──
        out["intercepted_mtop"] = mtop_responses
        try:
            html = page.content()
            with open(os.path.join(WORK, "diag_ali_pw37_page.html"), "w", encoding="utf-8") as f:
                f.write(html)
            log(f"  Page saved: {len(html)} bytes")
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
