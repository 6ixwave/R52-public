"""
diag_ali_pw33.py — AliExpress MTOP + TMD bypass (fixed cookies)
"""
import json, re, time, hashlib, urllib.parse, os, sys, traceback
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright", "pw_ali_profile")
RESULT = os.path.join(WORK, "diag_ali_pw33_result.json")
PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

out = {
    "product_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "script": "diag_ali_pw33",
    "strategies": {},
    "final_data": None,
    "errors": [],
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

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

def parse_mtop(body):
    body = body.strip()
    if body.startswith("mtopjsonp1("):
        body = body[len("mtopjsonp1("):-1].rstrip(")")
    return json.loads(body)

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
        log("WARMUP: main page")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        log(f"  cookies: {len(cookies)}")

        # ── LOAD PRODUCT ──
        log("LOAD: product page")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(6)
        cookies = browser.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        m_h5_tk = cookie_dict.get("_m_h5_tk", "")
        m_h5_tk_enc = cookie_dict.get("_m_h5_tk_enc", "")
        token = m_h5_tk.split("_")[0] if m_h5_tk else ""
        log(f"  cookies: {len(cookies)}, _m_h5_tk=[REDACTED_AUTH_SECRET]")

        # ── STRATEGY A: curl_cffi MTOP ──
        log("A: MTOP via curl_cffi (dict cookies)")
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
            resp = cffi_req.get(
                mtop_url,
                headers=headers,
                cookies=cookie_dict,  # DICT, not string!
                impersonate="chrome",
                timeout=20,
            )
            body = resp.text
            log(f"  status={resp.status_code}, len={len(body)}")
            log(f"  body[:300]={body[:300]}")

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
                out["strategies"]["mtop_cffi"] = {
                    "success": False, "ret": ret,
                    "tmd_url": tmd_url[:200] if tmd_url else None,
                    "is_tmd": is_tmd,
                }
                log(f"  is_tmd={is_tmd}")
        except Exception as e:
            log(f"  cffi error: {e}")
            out["strategies"]["mtop_cffi"] = {"success": False, "error": str(e)}
            out["errors"].append(f"mtop_cffi: {e}")

        # ── STRATEGY B: TMD bypass in browser ──
        needs_tmd = out["strategies"].get("mtop_cffi", {}).get("is_tmd", True)
        if needs_tmd and not out["final_data"]:
            log("B: TMD bypass in browser")
            try:
                # Wait for TMD iframe
                time.sleep(2)
                tmd_if = page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
                if tmd_if:
                    src = tmd_if.get_attribute("src") or ""
                    log(f"  TMD iframe: {src[:120]}...")
                    frame = tmd_if.content_frame
                    if frame:
                        # Wait for frame to load
                        time.sleep(3)
                        # Try inner iframe (recaptcha)
                        inner_if = frame.query_selector("iframe[src*='recaptcha'], iframe[src*='captcha']")
                        target_frame = frame
                        if inner_if:
                            log("  Inner recaptcha iframe found")
                            inner_frame = inner_if.content_frame
                            if inner_frame:
                                target_frame = inner_frame
                                time.sleep(2)

                        # Click strategy: try multiple selectors
                        click_sels = [
                            "button",
                            "input[type='checkbox']",
                            "span[role='button']",
                            "div[role='button']",
                            ".rc-button",
                            ".btn-verify",
                            "[class*='verify']",
                            "[class*='confirm']",
                            "a[href='#']",
                            "div.clickable",
                            "canvas",
                        ]
                        clicked = False
                        for sel in click_sels:
                            try:
                                els = target_frame.query_selector_all(sel)
                                for el in els:
                                    if el.is_visible():
                                        el.click()
                                        log(f"  Clicked in frame: {sel}")
                                        clicked = True
                                        time.sleep(4)
                                        break
                                if clicked:
                                    break
                            except Exception:
                                continue

                        if not clicked:
                            # Try clicking the iframe itself at center
                            box = tmd_if.bounding_box()
                            if box:
                                cx = box["x"] + box["width"] / 2
                                cy = box["y"] + box["height"] / 2
                                page.mouse.click(cx, cy)
                                log(f"  Clicked iframe center: ({cx:.0f},{cy:.0f})")
                                clicked = True
                                time.sleep(4)

                else:
                    # No iframe — maybe TMD is a full-page overlay
                    log("  No TMD iframe, trying page-level selectors")
                    for sel in ["button:has-text('Verify')", "button:has-text('OK')",
                                 "[class*='tmd'] button", ".dialog-btn", "a:has-text('Continue')"]:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                el.click()
                                log(f"  Clicked: {sel}")
                                time.sleep(4)
                                break
                        except:
                            pass

                # After TMD interaction, wait and retry MTOP in-page
                log("  Waiting 5s then retrying MTOP in browser...")
                time.sleep(5)

                # Retry MTOP via page.evaluate (uses browser's own cookies/session)
                retry_data = mtop_payload(cookie_dict)
                data_str2 = json.dumps(retry_data, separators=(",", ":"))
                # Refresh token from cookies
                cookies2 = browser.cookies()
                cookie_dict2 = {c["name"]: c["value"] for c in cookies2}
                token2 = cookie_dict2.get("_m_h5_tk", "").split("_")[0]
                mtop_url2 = build_mtop_url(token2, data_str2)

                # Use fetch in page context
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

                log(f"  Retry MTOP: status={mtop_result.get('status')}")
                mtext = mtop_result.get("text", "")
                log(f"  body[:300]={mtext[:300]}")

                if mtext:
                    try:
                        mjson = parse_mtop(mtext)
                        mret = mjson.get("ret", [])
                        log(f"  retry ret={mret}")
                        if mret and "SUCCESS" in str(mret[0]):
                            log("  *** RETRY MTOP SUCCESS ***")
                            out["strategies"]["tmd_retry"] = {"success": True, "ret": mret}
                            out["final_data"] = mjson
                        else:
                            out["strategies"]["tmd_retry"] = {
                                "success": False, "ret": mret,
                                "data_keys": list(mjson.get("data", {}).keys())[:15]
                            }
                    except Exception as e2:
                        out["strategies"]["tmd_retry"] = {"error": str(e2), "body": mtext[:500]}

                # Also check DOM after TMD
                time.sleep(3)
                h1 = page.query_selector("h1")
                price_el = page.query_selector("[class*='price'], .pdp-price, [class*='Price']")
                jsonld_s = page.query_selector_all("script[type='application/ld+json']")
                dom = {
                    "h1": h1.inner_text() if h1 else None,
                    "price": price_el.inner_text() if price_el else None,
                    "jsonld_count": len(jsonld_s),
                    "jsonld": [],
                }
                for s in jsonld_s:
                    try:
                        dom["jsonld"].append(json.loads(s.inner_text()))
                    except:
                        pass
                out["strategies"]["dom_after_tmd"] = dom
                log(f"  DOM: h1={dom['h1']}, price={dom['price']}, jsonld={dom['jsonld_count']}")

                if not out["final_data"] and dom.get("jsonld"):
                    out["final_data"] = {"source": "jsonld", "data": dom["jsonld"]}

            except Exception as e:
                log(f"  TMD bypass error: {e}")
                out["strategies"]["tmd_bypass"] = {"error": str(e)}
                out["errors"].append(f"tmd: {e}")

        # ── STRATEGY C: Fallback ──
        if not out["final_data"]:
            log("C: Fallback — meta + images + page_config")
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
            body_text = page.inner_text("body")[:8000]
            price_matches = re.findall(r'[\$€£]\s?\d+[\.,]\d+', body_text)

            fb = {
                "source": "meta_fallback",
                "og_title": metas.get("og:title", ""),
                "og_image": metas.get("og:image", ""),
                "images": images[:20],
                "prices_found": price_matches[:10],
                "page_config": page_config,
                "body_snippet": body_text[:3000],
                "title_tag": page.title(),
            }
            out["strategies"]["fallback"] = fb
            out["final_data"] = fb
            log(f"  og_title={fb['og_title'][:70]}")
            log(f"  prices={fb['prices_found']}")
            log(f"  images={len(fb['images'])}")

        browser.close()

except Exception as e:
    log(f"MAIN ERROR: {e}")
    traceback.print_exc()
    out["errors"].append(f"main: {e}")

with open(RESULT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log(f"Saved: {RESULT} ({os.path.getsize(RESULT)} bytes)")
log("DONE")
