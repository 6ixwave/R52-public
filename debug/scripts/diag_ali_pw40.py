"""
diag_ali_pw40.py — AliExpress: TMD reCAPTCHA ANCHOR bypass v3
Fixes over pw39:
- Stealth patches via add_init_script (navigator.webdriver, chrome, plugins, etc.)
- Manual grecaptcha.execute() in TMD frame if auto-token doesn't appear
- After token: trigger TMD report endpoint to finalize
- Retry MTOP PC with fresh cookies after TMD
- Mobile site (m.aliexpress.com) as alternative PDP source
- 75s total TMD window
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback, shutil
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(WORK, "pw_ali_profile_pw40")
RESULT = os.path.join(WORK, "diag_ali_pw40_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
MOBILE_URL = f"https://m.aliexpress.com/item/{PRODUCT_ID}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw40",
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
            "[class*='price-wrap'], [class*='price-box'], "
            "[class*='salePrice'], [class*='originalPrice'], "
            "span[class*='price'], div[class*='price']"
        )
        prices = []
        for pe in price_els[:40]:
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
        for img in page.query_selector_all("img[src*='aliexpress-media'], img[src*='alicdn'], img[src*='ae01.alicdn']"):
            try:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src and src not in imgs and not src.endswith(('.gif', '.svg')):
                    imgs.append(src)
            except:
                pass
        data["images"] = imgs[:20]
    except:
        data["images"] = []
    return data

# ── STEALTH SCRIPT ──
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 16});
window.chrome = {runtime: {onMessage: {addListener: () => {}}}};
if (!window.Notification) { window.Notification = {permission: 'default', requestPermission: () => Promise.resolve('default')}; }
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (params) => (params.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : origQuery(params));
"""

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
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        )
        browser.add_init_script(STEALTH_JS)
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
                        "body": body[:8000],
                    }
                    mtop_responses.append(info)
                    log(f"  [INT] {url[:80]}... st={response.status} len={len(body)}")
            except:
                pass

        page.on("response", on_response)

        # ── WARMUP ──
        log("WARMUP 1: homepage")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(10)
        log(f"  cookies: {len(browser.cookies())}")

        log("WARMUP 2: category")
        page.goto("https://www.aliexpress.com/category/50903.html", wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        log(f"  cookies: {len(browser.cookies())}")

        # ── LOAD PRODUCT ──
        log("LOAD: product page")
        mtop_responses.clear()
        try:
            page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=40000)
        except:
            log("  domcontentloaded timeout, continuing...")
        time.sleep(12)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""
        log(f"  cookies: {len(cookies)}, token={token[:20]}...")
        log(f"  URL: {page.url}")
        log(f"  MTOP intercepted: {len(mtop_responses)}")

        # ── TMD CHECK ──
        tmd_if = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
        has_tmd = tmd_if is not None
        log(f"  TMD present: {has_tmd}")

        if has_tmd:
            log("=== TMD FLOW ===")
            src = tmd_if.get_attribute("src") or ""
            log(f"  TMD src: {src[:150]}")
            time.sleep(8)

            outer_frame = None
            try:
                cf = tmd_if.content_frame
                if callable(cf):
                    outer_frame = cf()
                else:
                    outer_frame = cf
                log(f"  outer_frame: {type(outer_frame).__name__ if outer_frame else 'None'}")
            except Exception as e:
                log(f"  content_frame error: {e}")

            if outer_frame:
                time.sleep(3)
                rc_ifs = []
                try:
                    rc_ifs = outer_frame.query_selector_all(
                        "iframe[src*='google.com/recaptcha'], iframe[src*='recaptcha'], iframe[title='reCAPTCHA']"
                    )
                except Exception as e:
                    log(f"  rc_ifs error: {e}")
                log(f"  recaptcha iframes: {len(rc_ifs)}")

                rc_textarea = None
                for i in range(6):
                    try:
                        rc_textarea = outer_frame.query_selector(
                            "#g-recaptcha-response, textarea[name='g-recaptcha-response'], textarea.g-recaptcha-response"
                        )
                    except:
                        pass
                    if rc_textarea:
                        log(f"  textarea found (att {i+1})")
                        break
                    time.sleep(2)

                if not rc_textarea:
                    for fr in page.frames:
                        try:
                            rc_textarea = fr.query_selector("#g-recaptcha-response")
                            if rc_textarea:
                                log(f"  textarea in frame: {fr.url[:80]}")
                                break
                        except:
                            pass

                # ── POLL for auto-token (40s) ──
                log("  Polling for reCAPTCHA anchor auto-token (40s)...")
                rc_token = ""
                for attempt in range(20):
                    time.sleep(2)
                    try:
                        if rc_textarea:
                            val = rc_textarea.input_value()
                            if val and len(val) > 10:
                                rc_token = val
                                log(f"  *** AUTO TOKEN! (att {attempt+1}) len={len(val)} ***")
                                break
                    except:
                        pass
                    try:
                        tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                        if not tmd_still:
                            rc_token = "AUTO_GONE"
                            log(f"  TMD gone (att {attempt+1})")
                            break
                    except:
                        pass
                    if attempt % 5 == 4:
                        log(f"  poll {attempt+1}: waiting...")

                # ── MANUAL grecaptcha.execute() ──
                if not rc_token:
                    log("  No auto-token. Trying manual grecaptcha.execute()...")
                    try:
                        # Find the frame that has grecaptcha
                        target_frame = outer_frame
                        for fr in page.frames:
                            try:
                                has_grec = fr.evaluate("typeof grecaptcha !== 'undefined'")
                                if has_grec:
                                    target_frame = fr
                                    log(f"  grecaptcha found in frame: {fr.url[:80]}")
                                    break
                            except:
                                pass

                        # Check if grecaptcha is ready
                        grec_ready = target_frame.evaluate("""
                            () => {
                                try {
                                    if (typeof grecaptcha === 'undefined') return 'no_grec';
                                    if (grecaptcha.ready) return 'ready';
                                    return 'no_ready';
                                } catch(e) { return 'err:' + e.message; }
                            }
                        """)
                        log(f"  grecaptcha state: {grec_ready}")

                        if grec_ready in ('ready',):
                            # Try to find sitekey from the page
                            sitekey = target_frame.evaluate("""
                                () => {
                                    try {
                                        const el = document.querySelector('[data-sitekey]');
                                        if (el) return el.getAttribute('data-sitekey');
                                        const scripts = document.querySelectorAll('script');
                                        for (const s of scripts) {
                                            const m = s.src.match(/sitekey=([^&]+)/);
                                            if (m) return m[1];
                                        }
                                        return '';
                                    } catch(e) { return ''; }
                                }
                            """)
                            log(f"  sitekey: {sitekey[:40]}...")

                            if sitekey:
                                exec_result = target_frame.evaluate("""
                                    (sitekey) => {
                                        return new Promise((resolve) => {
                                            let resolved = false;
                                            grecaptcha.ready(function() {
                                                grecaptcha.execute(sitekey, {action: 'tmd'}).then(function(token) {
                                                    resolved = true;
                                                    resolve({ok: true, token: token.substring(0, 80)});
                                                }).catch(function(e) {
                                                    resolve({ok: false, error: e.message});
                                                });
                                            });
                                            setTimeout(() => {
                                                if (!resolved) resolve({ok: false, error: 'timeout_10s'});
                                            }, 10000);
                                        });
                                    }
                                """, sitekey)
                                log(f"  execute result: {exec_result}")
                                if exec_result and exec_result.get("ok"):
                                    time.sleep(3)
                                    try:
                                        val = rc_textarea.input_value() if rc_textarea else ""
                                        if val and len(val) > 10:
                                            rc_token = val
                                            log(f"  *** TOKEN after execute! len={len(val)} ***")
                                    except:
                                        pass
                    except Exception as e:
                        log(f"  manual execute error: {e}")

                # ── CLICK reCAPTCHA (last resort) ──
                if not rc_token:
                    log("  Still no token. Clicking reCAPTCHA area...")
                    try:
                        clicked = False
                        if rc_ifs:
                            rc_box = rc_ifs[0].bounding_box()
                            if rc_box:
                                cx = rc_box["x"] + 28
                                cy = rc_box["y"] + rc_box["height"] - 40
                                page.mouse.click(cx, cy)
                                log(f"  Clicked reCAPTCHA bottom-left: ({cx:.0f},{cy:.0f}) box={rc_box}")
                                clicked = True
                                time.sleep(6)
                        if not clicked:
                            tmd_box = tmd_if.bounding_box()
                            if tmd_box:
                                cx = tmd_box["x"] + tmd_box["width"] / 2
                                cy = tmd_box["y"] + tmd_box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked TMD center: ({cx:.0f},{cy:.0f})")
                                time.sleep(6)

                        for attempt in range(15):
                            time.sleep(2)
                            try:
                                if rc_textarea:
                                    val = rc_textarea.input_value()
                                    if val and len(val) > 10:
                                        rc_token = val
                                        log(f"  *** TOKEN after click! (att {attempt+1}) ***")
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
                    "rc_iframes": len(rc_ifs),
                    "textarea_found": rc_textarea is not None,
                    "token_received": bool(rc_token),
                    "token_preview": rc_token[:80] if rc_token else None,
                    "auto_resolved": "AUTO" in rc_token if rc_token else False,
                }
                log(f"  reCAPTCHA: token={bool(rc_token)}, auto={'AUTO' in rc_token if rc_token else False}")

            # ── AFTER TMD: wait + trigger report ──
            log("  Waiting 10s after TMD...")
            time.sleep(10)
            tmd_still = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
            if tmd_still:
                log("  TMD still present. Trying report trigger...")
                try:
                    outer2 = tmd_still.content_frame
                    if callable(outer2):
                        outer2 = outer2()
                    if outer2:
                        report_url = outer2.evaluate("""
                            () => {
                                try {
                                    const scripts = document.querySelectorAll('script');
                                    for (const s of scripts) {
                                        const txt = s.textContent || '';
                                        const m = txt.match(/report[^']*([^'"]+)/);
                                        if (m) return m[1];
                                    }
                                    return '';
                                } catch(e) { return ''; }
                            }
                        """)
                        log(f"  report url: {report_url[:100]}")
                        # Try to trigger the TMD report
                        outer2.evaluate("""
                            () => {
                                try {
                                    if (typeof window.__tmdReport === 'function') window.__tmdReport();
                                    if (typeof window.reportResult === 'function') window.reportResult();
                                    const btn = document.querySelector('[class*="report"], [id*="report"], button');
                                    if (btn) btn.click();
                                } catch(e) {}
                            }
                        """)
                except Exception as e:
                    log(f"  report trigger error: {e}")

                log("  Reloading page...")
                mtop_responses.clear()
                try:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                    time.sleep(15)
                    log(f"  Reloaded. URL: {page.url}")
                except Exception as e:
                    log(f"  Reload error: {e}")
            else:
                log("  TMD gone! Waiting 15s...")
                mtop_responses.clear()
                time.sleep(15)

        else:
            log("No TMD — direct extraction")
            out["strategies"]["recaptcha"] = {"tmd_present": False}

        # ── STRATEGY A: Intercepted MTOP ──
        log(f"A: Intercepted MTOP ({len(mtop_responses)})")
        for i, mr in enumerate(mtop_responses):
            if mr["body_len"] > 200:
                try:
                    mj = parse_mtop(mr["body"])
                    ret = mj.get("ret", [])
                    log(f"  MTOP[{i}]: ret={ret}")
                    if ret and "SUCCESS" in str(ret[0]):
                        log(f"  *** MTOP[{i}] SUCCESS ***")
                        out["strategies"]["mtop_intercept"] = {
                            "success": True, "index": i, "ret": ret,
                            "data_keys": list(mj.get("data", {}).keys())[:30],
                        }
                        out["final_data"] = {"source": "mtop_intercept", "data": mj}
                        break
                except Exception as e:
                    log(f"  MTOP[{i}] parse: {e}")

        # ── STRATEGY B: DOM ──
        if not out["final_data"]:
            log("B: DOM")
            dom_data = extract_dom(page)
            out["strategies"]["dom"] = dom_data
            log(f"  h1={dom_data.get('h1')}")
            log(f"  prices={dom_data.get('prices', [])[:5]}")
            log(f"  jsonld={len(dom_data.get('jsonld', []))}")
            log(f"  images={len(dom_data.get('images', []))}")
            if (dom_data.get("h1") and dom_data.get("prices")) or dom_data.get("jsonld"):
                log("  *** GOOD DOM ***")
                out["final_data"] = {"source": "dom", "data": dom_data}

        # ── STRATEGY C: MTOP PC retry ──
        if not out["final_data"]:
            log("C: MTOP PC browser fetch (retry)")
            try:
                cd = {c["name"]: c["value"] for c in browser.cookies()}
                tok = cd.get("_m_h5_tk", "").split("_")[0]
                ds = json.dumps({
                    "productId": PRODUCT_ID,
                    "_lang": "en_US", "_currency": "USD", "country": "US",
                    "province": "", "city": "", "channel": "",
                    "pdp_ext_f": "", "pdpNPI": "", "sourceType": "",
                    "clientType": "pc",
                    "ext": json.dumps({
                        "foreverRandomToken": cd.get("foreverRandomToken", ""),
                        "site": "glo", "crawler": False,
                        "x-m-biz-bx-region": "", "signedIn": False,
                        "host": "www.aliexpress.com"
                    })
                }, separators=(",", ":"))
                t = str(int(time.time() * 1000))
                ak = "12574478"
                sign = hashlib.md5(f"{tok}&{t}&{ak}&{ds}".encode()).hexdigest()
                murl = (
                    f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
                    f"?jsv=2.5.1&appKey={ak}&t={t}&sign={sign}"
                    f"&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp"
                    f"&v=1.0&timeout=15000&dataType=originaljsonp"
                    f"&callback=mtopjsonp1&data={urllib.parse.quote(ds)}"
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
                log(f"  st={mres.get('status')} len={len(mtext)}")
                if mtext:
                    mj = parse_mtop(mtext)
                    mr = mj.get("ret", [])
                    log(f"  ret={mr}")
                    if mr and "SUCCESS" in str(mr[0]):
                        log("  *** PC MTOP OK ***")
                        out["final_data"] = {"source": "mtop_pc", "data": mj}
                    else:
                        d = mj.get("data", {})
                        out["strategies"]["mtop_pc"] = {
                            "success": False, "ret": mr,
                            "data_keys": list(d.keys())[:20] if d else [],
                        }
            except Exception as e:
                log(f"  PC MTOP err: {e}")
                out["strategies"]["mtop_pc"] = {"error": str(e)}

        # ── STRATEGY D: Mobile site ──
        if not out["final_data"]:
            log("D: Mobile site (m.aliexpress.com)")
            try:
                mtop_responses.clear()
                page.goto(MOBILE_URL, wait_until="domcontentloaded", timeout=35000)
                time.sleep(12)
                log(f"  Mobile URL: {page.url}")
                log(f"  Mobile MTOP intercepted: {len(mtop_responses)}")

                # Check for TMD on mobile
                tmd_m = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                if tmd_m:
                    log("  Mobile TMD present, waiting 20s...")
                    time.sleep(20)

                # DOM on mobile
                m_dom = extract_dom(page)
                out["strategies"]["mobile_dom"] = m_dom
                log(f"  m-h1={m_dom.get('h1')}")
                log(f"  m-prices={m_dom.get('prices', [])[:5]}")
                log(f"  m-jsonld={len(m_dom.get('jsonld', []))}")
                log(f"  m-images={len(m_dom.get('images', []))}")

                if (m_dom.get("h1") and m_dom.get("prices")) or m_dom.get("jsonld"):
                    log("  *** GOOD MOBILE DOM ***")
                    out["final_data"] = {"source": "mobile_dom", "data": m_dom}
                else:
                    # Try mobile MTOP intercept
                    for i, mr in enumerate(mtop_responses):
                        if mr["body_len"] > 200:
                            try:
                                mj = parse_mtop(mr["body"])
                                ret = mj.get("ret", [])
                                if ret and "SUCCESS" in str(ret[0]):
                                    log(f"  *** MOBILE MTOP[{i}] SUCCESS ***")
                                    out["final_data"] = {"source": "mobile_mtop", "data": mj}
                                    break
                            except:
                                pass
            except Exception as e:
                log(f"  Mobile err: {e}")
                out["strategies"]["mobile"] = {"error": str(e)}

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
                body_text = page.inner_text("body")[:15000]
                price_matches = re.findall(r'[\$€£]\s?\d+[\.,]?\d*', body_text)
                fb = {
                    "source": "meta_fallback",
                    "og_title": metas.get("og:title", ""),
                    "og_image": metas.get("og:image", ""),
                    "og_description": metas.get("og:description", ""),
                    "images": out["strategies"].get("dom", {}).get("images", []),
                    "prices_found": price_matches[:15],
                    "body_snippet": body_text[:6000],
                    "title_tag": page.title(),
                }
                out["strategies"]["fallback"] = fb
                out["final_data"] = fb
                log(f"  og={fb['og_title'][:70]}")
            except Exception as e:
                log(f"  Fallback err: {e}")

        # ── SAVE ──
        out["intercepted_mtop"] = mtop_responses
        try:
            html = page.content()
            with open(os.path.join(WORK, "diag_ali_pw40_page.html"), "w", encoding="utf-8") as f:
                f.write(html)
            log(f"  Page: {len(html)} bytes")
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
