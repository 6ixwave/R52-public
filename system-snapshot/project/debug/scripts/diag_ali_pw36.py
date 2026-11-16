"""
diag_ali_pw36.py — AliExpress: fresh profile + anchor recaptcha + mobile MTOP
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback, shutil
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(WORK, "pw_ali_profile_pw36")
RESULT = os.path.join(WORK, "diag_ali_pw36_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw36",
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

def build_mtop_url(token, data_str, api="mtop.aliexpress.pdp.pc.query", app_key="12574478"):
    t = str(int(time.time() * 1000))
    sign_src = f"{token}&{t}&{app_key}&{data_str}"
    sign = hashlib.md5(sign_src.encode()).hexdigest()
    return (
        f"https://acs.aliexpress.com/h5/{api}/1.0/"
        f"?jsv=2.5.1&appKey={app_key}&t={t}&sign=[REDACTED_AUTH_SECRET]"
        f"&api={api}&type=originaljsonp"
        f"&v=1.0&timeout=15000&dataType=originaljsonp"
        f"&callback=mtopjsonp1&data={urllib.parse.quote(data_str)}"
    )

def mtop_payload_pc(cookie_dict):
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

def mtop_payload_mobile(cookie_dict):
    return {
        "productId": PRODUCT_ID,
        "_lang": "en_US",
        "_currency": "USD",
        "country": "US",
        "channel": "",
        "sourceType": "",
        "clientType": "mobile",
        "ext": json.dumps({
            "foreverRandomToken": cookie_dict.get("foreverRandomToken", ""),
            "site": "glo",
            "crawler": False,
            "signedIn": False,
            "host": "m.aliexpress.com"
        })
    }

def extract_dom(page):
    data = {}
    for key, sel, method in [
        ("h1", "h1", "text"),
        ("og_title", "meta[property='og:title']", "attr"),
        ("og_image", "meta[property='og:image']", "attr"),
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
            "[class*='current-price'], [class*='sale-price']"
        )
        prices = []
        for pe in price_els[:20]:
            txt = pe.inner_text().strip()
            if txt and len(txt) < 100 and re.search(r'[\d$€£]', txt):
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

        # ── WARMUP: 2 pages ──
        log("WARMUP 1: homepage")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)
        cookies = browser.cookies()
        log(f"  cookies: {len(cookies)}")

        log("WARMUP 2: category page (build session)")
        page.goto("https://www.aliexpress.com/category/50903.html", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        cookies = browser.cookies()
        log(f"  cookies: {len(cookies)}")

        # ── LOAD PRODUCT ──
        log("LOAD: product page (networkidle)")
        try:
            page.goto(PRODUCT_URL, wait_until="networkidle", timeout=50000)
        except:
            log("  networkidle timeout, continuing...")
        time.sleep(8)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""
        log(f"  cookies: {len(cookies)}, token={token[:20]}...")
        log(f"  URL: {page.url}")

        # ── CHECK TMD ──
        tmd_if = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
        has_tmd = tmd_if is not None
        log(f"  TMD present: {has_tmd}")

        if has_tmd:
            log("=== TMD FLOW ===")
            src = tmd_if.get_attribute("src") or ""
            log(f"  TMD src: {src[:150]}...")

            # Wait for TMD frame to fully render
            log("  Waiting 8s for TMD frame to render...")
            time.sleep(8)

            # Get outer frame
            outer_frame = tmd_if.content_frame
            if callable(outer_frame):
                outer_frame = outer_frame()
            log(f"  outer_frame: {type(outer_frame).__name__}")

            if outer_frame:
                # Find recaptcha iframe AND textarea
                time.sleep(3)
                rc_ifs = outer_frame.query_selector_all(
                    "iframe[src*='google.com/recaptcha'], iframe[src*='recaptcha'], iframe[title='reCAPTCHA']"
                )
                log(f"  recaptcha iframes: {len(rc_ifs)}")

                # Poll for textarea (might appear late)
                rc_textarea = None
                for i in range(5):
                    rc_textarea = outer_frame.query_selector(
                        "#[REDACTED_AUTH_SECRET], textarea[name='[REDACTED_AUTH_SECRET]'], textarea.[REDACTED_AUTH_SECRET]"
                    )
                    if rc_textarea:
                        log(f"  textarea found on attempt {i+1}")
                        break
                    time.sleep(2)
                if not rc_textarea:
                    log("  WARNING: textarea not found in TMD frame")
                    # Try in all frames
                    for fr in browser.frames:
                        try:
                            rc_textarea = fr.query_selector("#[REDACTED_AUTH_SECRET]")
                            if rc_textarea:
                                log(f"  textarea found in frame: {fr.url[:80]}")
                                break
                        except:
                            pass

                # ── POLL for anchor auto-token (up to 35s) ──
                log("  Polling for anchor recaptcha token (35s max)...")
                rc_token = "[REDACTED_AUTH_SECRET]"
                for attempt in range(18):  # 18 * 2s = 36s
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
                    # Check if TMD disappeared
                    try:
                        tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                        if not tmd_still:
                            rc_token = "[REDACTED_AUTH_SECRET]"
                            log(f"  TMD gone (attempt {attempt+1})")
                            break
                    except:
                        pass
                    if attempt % 5 == 4:
                        log(f"  poll {attempt+1}: still waiting...")

                # ── If no token, click visible recaptcha ──
                if not rc_token:
                    log("  No auto-token. Clicking recaptcha area...")
                    try:
                        # Click the recaptcha iframe
                        if rc_ifs:
                            rc_box = rc_ifs[0].bounding_box()
                            if rc_box:
                                cx = rc_box["x"] + 30  # left side = checkbox area
                                cy = rc_box["y"] + rc_box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked checkbox area: ({cx:.0f},{cy:.0f})")
                                time.sleep(5)
                        else:
                            # Click TMD iframe center
                            tmd_box = tmd_if.bounding_box()
                            if tmd_box:
                                cx = tmd_box["x"] + tmd_box["width"] / 2
                                cy = tmd_box["y"] + tmd_box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked TMD center: ({cx:.0f},{cy:.0f})")
                                time.sleep(5)

                        # Poll again after click
                        for attempt in range(8):
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
                                    rc_token = "[REDACTED_AUTH_SECRET]"
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
                log(f"  reCAPTCHA: token={bool(rc_token)}, auto={('AUTO' in rc_token) if rc_token else False}")

            # ── AFTER TMD: reload page ──
            log("  Waiting 5s then reloading page...")
            time.sleep(5)
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
                time.sleep(12)
                log(f"  Reloaded. URL: {page.url}")
            except Exception as e:
                log(f"  Reload error: {e}")

        else:
            log("No TMD — direct extraction")
            out["strategies"]["recaptcha"] = {"tmd_present": False}

        # ── STRATEGY A: DOM ──
        log("A: DOM extraction")
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

        # ── STRATEGY B: MTOP PC via browser fetch ──
        if not out["final_data"]:
            log("B: MTOP PC via browser fetch")
            try:
                cookies2 = browser.cookies()
                cd2 = {c["name"]: c["value"] for c in cookies2}
                tok2 = cd2.get("_m_h5_tk", "").split("_")[0]
                ds2 = json.dumps(mtop_payload_pc(cd2), separators=(",", ":"))
                murl2 = build_mtop_url(tok2, ds2, "mtop.aliexpress.pdp.pc.query")

                mres = page.evaluate("""
                    async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            return {status: r.status, text: await r.text()};
                        } catch(e) { return {status: 0, error: e.message}; }
                    }
                """, murl2)
                mtext = mres.get("text", "")
                log(f"  status={mres.get('status')}, len={len(mtext)}")
                if mtext:
                    mj = parse_mtop(mtext)
                    mr = mj.get("ret", [])
                    log(f"  ret={mr}")
                    if mr and "SUCCESS" in str(mr[0]):
                        log("  *** PC MTOP SUCCESS ***")
                        out["strategies"]["mtop_pc"] = {"success": True, "ret": mr}
                        out["final_data"] = {"source": "mtop_pc", "data": mj}
                    else:
                        d = mj.get("data", {})
                        out["strategies"]["mtop_pc"] = {
                            "success": False, "ret": mr,
                            "data_keys": list(d.keys())[:20] if d else [],
                            "is_tmd": "tmd" in str(d.get("url","")) or "punish" in str(d.get("url","")),
                        }
            except Exception as e:
                log(f"  PC MTOP error: {e}")
                out["strategies"]["mtop_pc"] = {"error": str(e)}

        # ── STRATEGY C: MTOP MOBILE via browser fetch ──
        if not out["final_data"]:
            log("C: MTOP MOBILE via browser fetch")
            try:
                cookies3 = browser.cookies()
                cd3 = {c["name"]: c["value"] for c in cookies3}
                tok3 = cd3.get("_m_h5_tk", "").split("_")[0]
                ds3 = json.dumps(mtop_payload_mobile(cd3), separators=(",", ":"))
                murl3 = build_mtop_url(tok3, ds3, "mtop.aliexpress.pdp.mobile.query")

                mres3 = page.evaluate("""
                    async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            return {status: r.status, text: await r.text()};
                        } catch(e) { return {status: 0, error: e.message}; }
                    }
                """, murl3)
                mtext3 = mres3.get("text", "")
                log(f"  status={mres3.get('status')}, len={len(mtext3)}")
                if mtext3:
                    mj3 = parse_mtop(mtext3)
                    mr3 = mj3.get("ret", [])
                    log(f"  ret={mr3}")
                    if mr3 and "SUCCESS" in str(mr3[0]):
                        log("  *** MOBILE MTOP SUCCESS ***")
                        out["strategies"]["mtop_mobile"] = {"success": True, "ret": mr3}
                        out["final_data"] = {"source": "mtop_mobile", "data": mj3}
                    else:
                        d3 = mj3.get("data", {})
                        out["strategies"]["mtop_mobile"] = {
                            "success": False, "ret": mr3,
                            "data_keys": list(d3.keys())[:20] if d3 else [],
                        }
            except Exception as e:
                log(f"  Mobile MTOP error: {e}")
                out["strategies"]["mtop_mobile"] = {"error": str(e)}

        # ── STRATEGY D: Fallback ──
        if not out["final_data"]:
            log("D: Fallback")
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
                    "images": dom_data.get("images", []),
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
        try:
            html = page.content()
            with open(os.path.join(WORK, "diag_ali_pw36_page.html"), "w", encoding="utf-8") as f:
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
