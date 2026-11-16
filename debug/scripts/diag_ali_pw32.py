"""
diag_ali_pw32.py — AliExpress: MTOP via curl_cffi + TMD bypass
Стратегии:
  A) Playwright warmup → cookies → curl_cffi MTOP call
  B) Если TMD → Playwright headful interaction with TMD iframe
  C) Fallback: meta tags + _page_config_
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright", "pw_ali_profile")
RESULT = os.path.join(WORK, "diag_ali_pw32_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw32",
    "strategies": {},
    "final_data": None,
    "errors": [],
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ─── STEP 1: Playwright warmup → get cookies ───
log("STEP 1: Playwright warmup for cookies")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Europe/London",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # Warmup: main page
        log("  Warming up main page...")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        cookies_warm = browser.cookies()
        log(f"  Warmup cookies: {len(cookies_warm)}")

        # Load product page (headful, let JS run)
        log("  Loading product page...")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)  # let MTOP fire
        cookies_prod = browser.cookies()
        log(f"  Product page cookies: {len(cookies_prod)}")

        # Extract key cookies
        cookie_dict = {c["name"]: c["value"] for c in cookies_prod}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        m_h5_tk_enc = cookie_dict.get("_m_h5_tk_enc", "")
        log(f"  _m_h5_tk: {m_h5_tk[:20]}..." if len(m_h5_tk) > 20 else f"  _m_h5_tk: '{m_h5_tk}'")
        log(f"  _m_h5_tk_enc: {m_h5_tk_enc[:20]}..." if len(m_h5_tk_enc) > 20 else f"  _m_h5_tk_enc: '{m_h5_tk_enc}'")

        # Check if TMD popup appeared
        tmd_visible = False
        tmd_iframe = None
        try:
            # Look for TMD dialog/iframe
            tmd_el = page.query_selector("iframe[src*='_____tmd_____']")
            if tmd_el:
                tmd_iframe = tmd_el.get_attribute("src")
                tmd_visible = True
                log(f"  TMD iframe found: {str(tmd_iframe)[:100]}...")
            # Also check for visible TMD overlay
            tmd_overlay = page.query_selector(".tmd-punish, [class*='tmd'], [id*='tmd']")
            if tmd_overlay:
                tmd_visible = True
                log("  TMD overlay element found")
        except Exception as e:
            log(f"  TMD detection error: {e}")

        out["strategies"]["warmup"] = {
            "cookies_count": len(cookies_prod),
            "m_h5_tk": m_h5_tk[:40],
            "m_h5_tk_enc": m_h5_tk_enc[:40],
            "tmd_visible": tmd_visible,
            "tmd_iframe_url": str(tmd_iframe)[:200] if tmd_iframe else None,
        }

        # ─── STEP 2: MTOP via curl_cffi ───
        log("STEP 2: MTOP via curl_cffi")
        try:
            from curl_cffi import requests as cffi_req

            token = m_h5_tk.split("_")[0] if m_h5_tk else ""
            t = str(int(time.time() * 1000))
            app_key = "12574478"

            mtop_data = {
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
            data_str = json.dumps(mtop_data, separators=(",", ":"))
            sign_src = f"{token}&{t}&{app_key}&{data_str}"
            sign = hashlib.md5(sign_src.encode()).hexdigest()
            log(f"  Sign computed: {sign}")

            mtop_url = (
                f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
                f"?jsv=2.5.1&appKey={app_key}&t={t}&sign={sign}"
                f"&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp"
                f"&v=1.0&timeout=15000&dataType=originaljsonp"
                f"&callback=mtopjsonp1&data={urllib.parse.quote(data_str)}"
            )

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": PRODUCT_URL,
                "Origin": "https://www.aliexpress.com",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }

            # Build cookie string
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies_prod)

            log("  Calling MTOP...")
            resp = cffi_req.get(
                mtop_url,
                headers=headers,
                cookies=cookie_str,
                impersonate="chrome",
                timeout=20,
            )
            log(f"  MTOP status: {resp.status_code}, len: {len(resp.text)}")

            # Parse JSONP
            body = resp.text.strip()
            mtop_json = None
            if body.startswith("mtopjsonp1("):
                body = body[len("mtopjsonp1("):-1]
                mtop_json = json.loads(body)
            elif body.startswith("{"):
                mtop_json = json.loads(body)

            if mtop_json:
                ret = mtop_json.get("ret", [])
                log(f"  MTOP ret: {ret}")
                if "SUCCESS" in str(ret[0]) if ret else False:
                    log("  *** MTOP SUCCESS! ***")
                    out["strategies"]["mtop_cffi"] = {
                        "success": True,
                        "ret": ret,
                        "data_keys": list(mtop_json.get("data", {}).keys())[:20] if mtop_json.get("data") else [],
                        "data_preview": json.dumps(mtop_json.get("data", {}))[:2000],
                    }
                    out["final_data"] = mtop_json
                else:
                    # Check if TMD again
                    data = mtop_json.get("data", {})
                    tmd_url = data.get("url", "")
                    out["strategies"]["mtop_cffi"] = {
                        "success": False,
                        "ret": ret,
                        "tmd_url": tmd_url[:200] if tmd_url else None,
                        "data_keys": list(data.keys()),
                        "body_preview": body[:500],
                    }
                    if "tmd" in tmd_url or "punish" in tmd_url:
                        log("  TMD again → will try Playwright interaction (Step 3)")
                        out["strategies"]["mtop_cffi"]["needs_tmd_bypass"] = True
            else:
                out["strategies"]["mtop_cffi"] = {
                    "success": False,
                    "error": "Could not parse MTOP response",
                    "body_preview": body[:500],
                }

        except Exception as e:
            log(f"  MTOP cffi error: {e}")
            out["strategies"]["mtop_cffi"] = {"success": False, "error": str(e)}
            out["errors"].append(f"mtop_cffi: {e}")

        # ─── STEP 3: TMD bypass via Playwright interaction ───
        if out["strategies"].get("mtop_cffi", {}).get("needs_tmd_bypass"):
            log("STEP 3: TMD bypass via Playwright")
            try:
                # Wait for TMD dialog to appear
                time.sleep(2)
                # Try to find and click the recaptcha button
                # TMD usually has a button or the iframe with recaptcha
                tmd_dialog = page.query_selector("div[class*='tmd'], div[class*='punish'], .dialog, [class*='modal']")
                if tmd_dialog:
                    log("  TMD dialog found, trying to interact...")

                # Try clicking on the iframe area or a "verify" button
                # Common TMD elements:
                click_targets = [
                    "button:has-text('Verify')",
                    "button:has-text('验证')",
                    "button:has-text('Confirm')",
                    ".tmd-btn",
                    "[class*='captcha'] button",
                    "#tmd-captcha-btn",
                    "a:has-text('I am not a robot')",
                    ".recaptcha-checkbox",
                ]
                clicked = False
                for sel in click_targets:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click()
                            log(f"  Clicked: {sel}")
                            clicked = True
                            time.sleep(3)
                            break
                    except:
                        pass

                if not clicked:
                    # Try the iframe directly
                    tmd_if = page.query_selector("iframe[src*='tmd'], iframe[src*='punish']")
                    if tmd_if:
                        log("  Found TMD iframe, trying frame interaction...")
                        frame = tmd_if.content_frame
                        if frame:
                            # Look for recaptcha inside
                            for sel in ["button", "input[type=checkbox]", ".recaptcha-checkbox", "[class*='captcha']"]:
                                try:
                                    el = frame.query_selector(sel)
                                    if el and el.is_visible():
                                        el.click()
                                        log(f"  Clicked in iframe: {sel}")
                                        clicked = True
                                        time.sleep(3)
                                        break
                                except:
                                    pass

                # Wait and check if TMD resolved
                time.sleep(5)
                # Reload page after TMD
                log("  Reloading product page after TMD attempt...")
                page.reload(wait_until="domcontentloaded", timeout=45000)
                time.sleep(6)

                # Check DOM for product data
                h1 = page.query_selector("h1")
                og_title = page.query_selector("meta[property='og:title']")
                price_el = page.query_selector("[class*='price'], .pdp-price, [class*='Price']")
                jsonld_s = page.query_selector_all("script[type='application/ld+json']")

                dom_data = {
                    "h1": h1.inner_text() if h1 else None,
                    "og_title": og_title.get_attribute("content") if og_title else None,
                    "price": price_el.inner_text() if price_el else None,
                    "jsonld_count": len(jsonld_s),
                    "jsonld": [],
                }
                for s in jsonld_s:
                    try:
                        dom_data["jsonld"].append(json.loads(s.inner_text()))
                    except:
                        pass

                # Check if MTOP fired successfully this time
                # Look for window state
                run_params = page.evaluate("window.runParams || '{}'")
                dom_data["runParams"] = run_params

                out["strategies"]["tmd_bypass"] = {
                    "clicked": clicked,
                    "dom": dom_data,
                    "tmd_resolved": bool(h1 or price_el or dom_data.get("jsonld")),
                }
                log(f"  TMD resolved: {out['strategies']['tmd_bypass']['tmd_resolved']}")
                log(f"  h1: {dom_data['h1']}")
                log(f"  price: {dom_data['price']}")
                log(f"  jsonld: {len(dom_data['jsonld'])} items")

                if dom_data.get("jsonld"):
                    out["final_data"] = {"source": "jsonld", "data": dom_data["jsonld"]}
                elif h1 and price_el:
                    out["final_data"] = {"source": "dom", "data": dom_data}

            except Exception as e:
                log(f"  TMD bypass error: {e}")
                out["strategies"]["tmd_bypass"] = {"error": str(e)}
                out["errors"].append(f"tmd_bypass: {e}")

        # ─── STEP 4: Fallback — extract from meta + page state ───
        if not out["final_data"]:
            log("STEP 4: Fallback extraction from meta + page state")
            try:
                # Get all meta tags
                metas = {}
                for m in page.query_selector_all("meta"):
                    prop = m.get_attribute("property") or m.get_attribute("name") or ""
                    content = m.get_attribute("content") or ""
                    if prop:
                        metas[prop] = content

                # Get images from DOM
                images = []
                for img in page.query_selector_all("img[src*='aliexpress-media'], img[src*='alicdn']"):
                    src = img.get_attribute("src")
                    if src and src not in images:
                        images.append(src)

                # Get _page_config_
                page_config = page.evaluate("window._page_config_ || null")

                # Get any price-like text
                all_text = page.inner_text("body")[:5000]
                price_matches = re.findall(r'[\$€£]\s?\d+[\.,]\d+', all_text)

                fallback = {
                    "source": "meta_fallback",
                    "og_title": metas.get("og:title", ""),
                    "og_image": metas.get("og:image", ""),
                    "og_description": metas.get("og:description", ""),
                    "title_tag": page.title(),
                    "images": images[:15],
                    "prices_found": price_matches[:10],
                    "page_config": page_config,
                    "body_snippet": all_text[:2000],
                }
                out["strategies"]["fallback"] = fallback
                out["final_data"] = fallback
                log(f"  Fallback: og_title={fallback['og_title'][:60]}...")
                log(f"  Fallback: prices={fallback['prices_found']}")
                log(f"  Fallback: images={len(fallback['images'])}")

            except Exception as e:
                log(f"  Fallback error: {e}")
                out["errors"].append(f"fallback: {e}")

        browser.close()

except Exception as e:
    log(f"MAIN ERROR: {e}")
    traceback.print_exc()
    out["errors"].append(f"main: {e}")

# ─── Save result ───
out["elapsed"] = None  # filled below
with open(RESULT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log(f"Result saved: {RESULT} ({os.path.getsize(RESULT)} bytes)")
log("DONE")
