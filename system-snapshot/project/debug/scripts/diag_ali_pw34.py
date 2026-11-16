"""
diag_ali_pw34.py — AliExpress TMD bypass (fixed content_frame + longer waits)
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright", "pw_ali_profile")
RESULT = os.path.join(WORK, "diag_ali_pw34_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw34",
    "strategies": {},
    "final_data": None,
    "errors": [],
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def get_content_frame(el):
    """Get content_frame from ElementHandle — handles both property and method"""
    try:
        cf = el.content_frame
        if callable(cf):
            return cf()
        return cf
    except Exception:
        try:
            return el.content_frame()
        except Exception:
            return None

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
        f"?jsv=2.5.1&appKey={app_key}&t={t}&sign=[REDACTED_AUTH_SECRET]"
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

try:
    from playwright.sync_api import sync_playwright
    from curl_cffi import requests as cffi_req

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 768},
            user_agent=UA,
            locale="en-US",
            timezone_id="Europe/London",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── WARMUP ──
        log("WARMUP: main page (8s)")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(8)
        cookies = browser.cookies()
        log(f"  cookies: {len(cookies)}")

        # ── LOAD PRODUCT ──
        log("LOAD: product page (wait 12s for MTOP + TMD)")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(12)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""
        log(f"  cookies: {len(cookies)}, token={token[:20]}...")

        # ── STRATEGY A: curl_cffi MTOP ──
        log("A: MTOP via curl_cffi")
        data_str = json.dumps(mtop_payload(cookie_dict), separators=(",", ":"))
        mtop_url = build_mtop_url(token, data_str)
        headers = {
            "User-Agent": UA,
            "Referer": PRODUCT_URL,
            "Origin": "https://www.aliexpress.com",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            resp = cffi_req.get(mtop_url, headers=headers, cookies=cookie_dict, impersonate="chrome", timeout=20)
            body = resp.text
            log(f"  status={resp.status_code}, len={len(body)}")
            mtop_json = parse_mtop(body)
            ret = mtop_json.get("ret", [])
            log(f"  ret={ret}")
            if ret and "SUCCESS" in str(ret[0]):
                log("  *** MTOP SUCCESS ***")
                out["strategies"]["mtop_cffi"] = {"success": True, "ret": ret}
                out["final_data"] = mtop_json
            else:
                data = mtop_json.get("data", {})
                tmd_url = data.get("url", "")
                is_tmd = "tmd" in tmd_url or "punish" in tmd_url
                out["strategies"]["mtop_cffi"] = {"success": False, "ret": ret, "is_tmd": is_tmd, "tmd_url": tmd_url[:150]}
                log(f"  is_tmd={is_tmd}")
        except Exception as e:
            log(f"  cffi error: {e}")
            out["strategies"]["mtop_cffi"] = {"success": False, "error": str(e)}
            out["errors"].append(f"mtop_cffi: {e}")

        # ── STRATEGY B: TMD bypass in browser ──
        if not out["final_data"]:
            log("B: TMD bypass in browser")
            try:
                tmd_if = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                if tmd_if:
                    src = tmd_if.get_attribute("src") or ""
                    log(f"  TMD iframe: {src[:120]}...")

                    # Wait for outer frame to load
                    time.sleep(5)
                    outer_frame = get_content_frame(tmd_if)
                    log(f"  outer_frame: {type(outer_frame).__name__}")

                    if outer_frame:
                        # Look for inner iframe (recaptcha)
                        time.sleep(3)
                        inner_ifs = outer_frame.query_selector_all("iframe")
                        log(f"  inner iframes in outer: {len(inner_ifs)}")
                        target_frame = outer_frame

                        for iif in inner_ifs:
                            iif_src = iif.get_attribute("src") or ""
                            if "recaptcha" in iif_src or "x5step=3" in iif_src:
                                log(f"  Found recaptcha iframe: {iif_src[:100]}...")
                                time.sleep(3)
                                inner_frame = get_content_frame(iif)
                                if inner_frame:
                                    target_frame = inner_frame
                                    log(f"  inner_frame: {type(inner_frame).__name__}")
                                    break

                        # Try to find and click captcha element
                        log("  Searching for clickable elements in TMD frame...")
                        # Dump frame HTML for debugging
                        try:
                            frame_html = target_frame.evaluate("document.body ? document.body.innerHTML : ''")
                            log(f"  frame HTML length: {len(frame_html)}")
                            # Save for analysis
                            with open(os.path.join(WORK, "diag_ali_pw34_tmd_frame.html"), "w", encoding="utf-8") as f:
                                f.write(frame_html)
                        except Exception as e:
                            log(f"  frame HTML error: {e}")

                        # Click strategies (in order of likelihood)
                        click_sels = [
                            # Standard recaptcha checkbox
                            "input[type='checkbox']",
                            "span[role='checkbox']",
                            "div[role='button']",
                            # AliExpress TMD specific
                            "#bx-pu-btn",
                            ".bx-pu-btn",
                            "[class*='punish'] button",
                            "[class*='captcha'] button",
                            "[class*='verify']",
                            "[class*='confirm']",
                            # Generic
                            "button",
                            "a",
                            "canvas",
                            "div[class*='click']",
                            "div[class*='slider']",
                        ]
                        clicked = False
                        for sel in click_sels:
                            try:
                                els = target_frame.query_selector_all(sel)
                                log(f"  sel '{sel}': {len(els)} elements")
                                for el in els:
                                    try:
                                        if el.is_visible():
                                            el.click()
                                            log(f"  *** CLICKED: {sel} ***")
                                            clicked = True
                                            time.sleep(5)
                                            break
                                    except Exception:
                                        continue
                                if clicked:
                                    break
                            except Exception:
                                continue

                        if not clicked:
                            # Click iframe center as last resort
                            box = tmd_if.bounding_box()
                            if box:
                                cx = box["x"] + box["width"] / 2
                                cy = box["y"] + box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked iframe center: ({cx:.0f},{cy:.0f})")
                                clicked = True
                                time.sleep(5)

                else:
                    log("  No TMD iframe found, checking page-level...")
                    # Maybe TMD is a dialog on the page itself
                    for sel in ["button:has-text('Verify')", "button:has-text('OK')",
                                 "[class*='tmd'] button", ".dialog-btn", "[class*='modal'] button"]:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                el.click()
                                log(f"  Clicked page-level: {sel}")
                                time.sleep(5)
                                break
                        except:
                            pass

                # ── Wait for TMD to resolve and page to re-render ──
                log("  Waiting 10s for TMD resolution + page re-render...")
                time.sleep(10)

                # ── STRATEGY B2: Retry MTOP via page.evaluate (browser context) ──
                log("B2: MTOP retry via page.evaluate (browser fetch)")
                try:
                    cookies2 = browser.cookies()
                    cookie_dict2 = {c["name"]: c["value"] for c in cookies2}
                    token2 = cookie_dict2.get("_m_h5_tk", "").split("_")[0]
                    data_str2 = json.dumps(mtop_payload(cookie_dict2), separators=(",", ":"))
                    mtop_url2 = build_mtop_url(token2, data_str2)

                    mtop_result = page.evaluate("""
                        async (url) => {
                            try {
                                const r = await fetch(url, {credentials: 'include'});
                                return {status: r.status, text: await r.text()};
                            } catch(e) {
                                return {status: 0, error: e.message};
                            }
                        }
                    """, mtop_url2)

                    mstatus = mtop_result.get("status", 0)
                    mtext = mtop_result.get("text", "")
                    merr = mtop_result.get("error", "")
                    log(f"  fetch status={mstatus}, len={len(mtext)}, err={merr}")

                    if mtext:
                        mjson = parse_mtop(mtext)
                        mret = mjson.get("ret", [])
                        log(f"  retry ret={mret}")
                        if mret and "SUCCESS" in str(mret[0]):
                            log("  *** BROWSER MTOP SUCCESS ***")
                            out["strategies"]["mtop_browser"] = {"success": True, "ret": mret}
                            out["final_data"] = mjson
                        else:
                            out["strategies"]["mtop_browser"] = {
                                "success": False, "ret": mret,
                                "data_keys": list(mjson.get("data", {}).keys())[:15]
                            }
                    elif merr:
                        out["strategies"]["mtop_browser"] = {"success": False, "error": merr}
                except Exception as e:
                    log(f"  browser MTOP error: {e}")
                    out["strategies"]["mtop_browser"] = {"error": str(e)}
                    out["errors"].append(f"mtop_browser: {e}")

                # ── STRATEGY B3: Extract from rendered DOM ──
                log("B3: DOM extraction after TMD")
                try:
                    h1 = page.query_selector("h1")
                    og_title = page.query_selector("meta[property='og:title']")
                    price_els = page.query_selector_all("[class*='price'], .pdp-price, [class*='Price'], [data-role='price']")
                    jsonld_s = page.query_selector_all("script[type='application/ld+json']")

                    prices = []
                    for pe in price_els[:10]:
                        txt = pe.inner_text().strip()
                        if txt:
                            prices.append(txt)

                    jsonld_items = []
                    for s in jsonld_s:
                        try:
                            jsonld_items.append(json.loads(s.inner_text()))
                        except:
                            pass

                    run_params = page.evaluate("window.runParams || '{}'")
                    if isinstance(run_params, str):
                        try:
                            run_params = json.loads(run_params)
                        except:
                            pass

                    dom_data = {
                        "h1": h1.inner_text() if h1 else None,
                        "og_title": og_title.get_attribute("content") if og_title else None,
                        "prices": prices,
                        "jsonld_count": len(jsonld_items),
                        "jsonld": jsonld_items,
                        "runParams_keys": list(run_params.keys())[:20] if isinstance(run_params, dict) else str(run_params)[:200],
                        "title_tag": page.title(),
                    }
                    out["strategies"]["dom"] = dom_data
                    log(f"  h1={dom_data['h1']}")
                    log(f"  prices={prices[:5]}")
                    log(f"  jsonld={dom_data['jsonld_count']}")
                    log(f"  runParams={dom_data['runParams_keys']}")

                    if not out["final_data"]:
                        if dom_data.get("jsonld"):
                            out["final_data"] = {"source": "jsonld", "data": dom_data["jsonld"]}
                        elif dom_data.get("h1") and prices:
                            out["final_data"] = {"source": "dom", "data": dom_data}
                except Exception as e:
                    log(f"  DOM error: {e}")
                    out["errors"].append(f"dom: {e}")

            except Exception as e:
                log(f"  TMD bypass error: {e}")
                traceback.print_exc()
                out["strategies"]["tmd_bypass"] = {"error": str(e)}
                out["errors"].append(f"tmd: {e}")

        # ── STRATEGY C: Final fallback ──
        if not out["final_data"]:
            log("C: Fallback — meta + images + page_config")
            try:
                metas = {}
                for m in page.query_selector_all("meta"):
                    prop = m.get_attribute("property") or m.get_attribute("name") or ""
                    content = m.get_attribute("content") or ""
                    if prop:
                        metas[prop] = content

                images = []
                for img in page.query_selector_all("img[src*='aliexpress-media'], img[src*='alicdn']"):
                    src = img.get_attribute("src")
                    if src and src not in images:
                        images.append(src)

                page_config = page.evaluate("window._page_config_ || null")
                body_text = page.inner_text("body")[:10000]
                price_matches = re.findall(r'[\$€£]\s?\d+[\.,]?\d*', body_text)

                fb = {
                    "source": "meta_fallback",
                    "og_title": metas.get("og:title", ""),
                    "og_image": metas.get("og:image", ""),
                    "images": images[:20],
                    "prices_found": price_matches[:15],
                    "page_config": page_config,
                    "body_snippet": body_text[:4000],
                    "title_tag": page.title(),
                }
                out["strategies"]["fallback"] = fb
                out["final_data"] = fb
                log(f"  og_title={fb['og_title'][:70]}")
                log(f"  prices={fb['prices_found'][:5]}")
                log(f"  images={len(fb['images'])}")
            except Exception as e:
                log(f"  Fallback error: {e}")
                out["errors"].append(f"fallback: {e}")

        browser.close()

except Exception as e:
    log(f"MAIN ERROR: {e}")
    traceback.print_exc()
    out["errors"].append(f"main: {e}")

with open(RESULT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log(f"Saved: {RESULT} ({os.path.getsize(RESULT)} bytes)")
log("DONE")
