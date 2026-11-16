#!/usr/bin/env python3
"""
diag_ali_pw42.py — AliExpress: fix cookies dict, force EN, reCAPTCHA Enterprise click, TMD solve, MTOP retry
Key fixes vs pw41:
  1. curl_cffi cookies as DICT not string
  2. Force English via cookie aep_usuc_f
  3. Deeper reCAPTCHA Enterprise interaction (wait, click checkbox, poll textarea)
  4. TMD punish API direct call with x5secdata
  5. Multiple MTOP endpoints
  6. Longer warm-up with category page
"""
import json, time, re, os, sys, hashlib, urllib.parse, shutil
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE = "pw_ali_profile_pw42"
RESULT = "diag_ali_pw42_result.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def main():
    from playwright.sync_api import sync_playwright
    R = {
        "product_id": PRODUCT_ID, "url": PRODUCT_URL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw42",
        "strategies": {}, "intercepted_mtop": [],
        "final_data": None, "errors": [],
        "html_analysis": {}, "tmd": {}, "cookies_info": {}
    }

    with sync_playwright() as p:
        if os.path.exists(PROFILE):
            shutil.rmtree(PROFILE)

        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False,
            viewport={"width": 1366, "height": 768},
            user_agent=UA,
            locale="en-US", timezone_id="Europe/Moscow",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-features=IsolateOrigins,site-per-process"]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ── 1. Force English: set cookie before any navigation ──
        print("[42] Setting English cookie…")
        ctx.add_cookies([{
            "name": "aep_usuc_f",
            "value": "aimode=PC&site=glo&c_tp=USD&region=US&b_locale=en_US",
            "domain": ".aliexpress.com",
            "path": "/",
            "secure": True,
            "httpOnly": False
        }])

        # ── 2. Warm-up: homepage → category → product ──
        print("[42] Warm-up homepage…")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        # Accept cookies if dialog
        for sel in ["#cookie-btn", "button:has-text('Accept')", "button:has-text('OK')",
                    "[class*='cookie'] button", ".accept-btn"]:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    time.sleep(1)
                    break
            except: pass
        n1 = len(ctx.cookies())
        print(f"[42] Cookies after homepage: {n1}")

        print("[42] Warm-up category…")
        page.goto("https://www.aliexpress.com/category/509.html", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        n2 = len(ctx.cookies())
        print(f"[42] Cookies after category: {n2}")

        # ── 3. Product page ──
        print("[42] Loading product…")
        mtop_log = []
        def _on_resp(resp):
            u = resp.url
            if "acs.aliexpress.com" in u or "mtop" in u or "umdc" in u:
                try:
                    b = resp.text()
                    mtop_log.append({"url": u[:300], "status": resp.status,
                                     "body_len": len(b), "body": b[:2500]})
                except: pass
        page.on("response", _on_resp)

        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(8)
        cur = page.url
        print(f"[42] Final URL: {cur}")
        R["final_url"] = cur
        n3 = len(ctx.cookies())
        print(f"[42] Cookies after product: {n3}")

        # ── 4. HTML deep parse ──
        print("[42] Parsing HTML…")
        html = page.content()
        R["html_length"] = len(html)
        ha = {}

        # Embedded JSON
        for name, pat in [
            ("__INIT_DATA", r'window\.__INIT_DATA\s*=\s*(\{.+?\})\s*;?\s*(?:</script>|$)'),
            ("runParams",   r'window\.runParams\s*=\s*(\{.+?\})\s*;'),
            ("__NEXT_DATA", r'window\.__NEXT_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>'),
            ("initialState",r'window\.initialState\s*=\s*(\{.+?\})\s*;'),
            ("__pdp_data",  r'window\.__pdp_data\s*=\s*(\{.+?\})\s*;'),
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                raw = m.group(1)
                ha[name] = raw[:5000]
                try: ha[name + "_parsed"] = json.loads(raw)
                except: pass
                print(f"[42] Found {name}: {len(raw)} chars")

        # Prices - broader patterns
        price_pats = [
            r'"(?:salePrice|price|minPrice|maxPrice|formatPrice|displayPrice|originalPrice|promotionPrice|priceText|priceDisplay)"\s*:\s*"?([\d,.]+)"?',
            r'data-price["\s]*[:=]["\s]*([\d,.]+)',
            r'class=["\'][^"\']*price[^"\']*["\'][^>]*>\s*\$?€?£?\s*([\d,]+\.?\d*)',
            r'(?:USD|EUR|€|\$|£)\s*([\d,]+\.?\d{2})',
            r'"(?:minAmount|maxAmount|cent|minPriceCent|maxPriceCent)"\s*:\s*"?(\d+)"?',
            r'"price"\s*:\s*\{[^}]*"cent"\s*:\s*(\d+)',
        ]
        prices = []
        for pat in price_pats:
            ms = re.findall(pat, html)
            prices.extend(ms[:10])
        ha["prices"] = prices[:30]
        print(f"[42] Prices: {prices[:12]}")

        # Titles
        titles = []
        for pat in [r'<h1[^>]*>([^<]{10,300})</h1>',
                    r'"title"\s*:\s*"([^"]{10,300})"',
                    r'og:title["\s]*content=["\']([^"\']+)',
                    r'name=["\']title["\']\s+content=["\']([^"\']+)']:
            titles.extend(re.findall(pat, html, re.I)[:5])
        ha["titles"] = list(dict.fromkeys(titles))[:10]

        # JSON-LD
        jld = []
        for blk in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try: jld.append(json.loads(blk.strip()))
            except: jld.append({"raw": blk.strip()[:500]})
        ha["jsonld"] = jld[:5]

        # SKU / stock / variants
        ha["sku_ids"] = re.findall(r'"skuId"\s*:\s*"?(\d+)', html)[:15]
        ha["variants"] = len(re.findall(r'"(?:variants|attributes|skuList|skuPropList)"\s*:\s*\[', html))
        ha["stock"] = re.findall(r'"(?:stock|inStock|quantity|availability|itemStatus)"\s*:\s*"?([\w]+)"?', html)[:15]

        # Category
        ha["breadcrumb"] = re.findall(r'class=["\'][^"\']*breadcrumb[^"\']*["\'][^>]*>(.*?)</', html, re.DOTALL)[:5]
        ha["category_json"] = re.findall(r'"(?:categoryName|categoryPath|breadcrumb)"\s*:\s*"?([^",}\]]+)"?', html)[:10]

        # All script blocks > 1KB (might contain product data)
        big_scripts = []
        for i, blk in enumerate(re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)):
            if len(blk) > 1000 and not blk.strip().startswith(("//","/*")):
                # Check if it has product-like keys
                if any(k in blk for k in ("productId","price","sku","stock","title","seller")):
                    big_scripts.append({"idx": i, "len": len(blk), "snippet": blk[:500]})
        ha["big_scripts"] = big_scripts[:5]

        R["html_analysis"] = ha

        # ── 5. TMD / reCAPTCHA Enterprise interaction ──
        print("[42] Checking TMD…")
        time.sleep(3)
        tmd = {"present": False, "iframes": 0, "steps": [], "token": None,
               "result": "none", "frames_detail": []}

        all_frames = page.frames
        tmd["total_frames"] = len(all_frames)

        # Detailed frame list
        for fr in all_frames:
            fu = fr.url
            fd = {"url": fu[:300], "name": fr.name}
            if any(k in fu for k in ("tmd","punish","recaptcha","x5sec","google.com/recaptcha")):
                tmd["present"] = True
                tmd["iframes"] += 1
                tmd["steps"].append(f"frame: {fu[:200]}")
            tmd["frames_detail"].append(fd)

        # DOM iframes
        for ifr in page.query_selector_all("iframe"):
            src = ifr.get_attribute("src") or ""
            if any(k in src for k in ("tmd","punish","recaptcha","google")):
                tmd["present"] = True
                tmd["steps"].append(f"iframe src: {src[:200]}")

        # TMD overlay
        for sel in ["[class*='tmd']","[id*='tmd']","[class*='punish']","[class*='captcha']",
                    ".sufei-punish","[class*='overlay']","[class*='modal']"]:
            el = page.query_selector(sel)
            if el:
                tmd["present"] = True
                tmd["steps"].append(f"DOM overlay: {sel}")
                break

        if tmd["present"]:
            tmd["result"] = "attempting"
            print("[42] TMD found, interacting with reCAPTCHA…")
            try:
                # Find the Google reCAPTCHA frame specifically
                rc_frame = None
                punish_frame = None
                for fr in all_frames:
                    fu = fr.url
                    if "google.com/recaptcha" in fu:
                        rc_frame = fr
                        tmd["steps"].append(f"GOOGLE RC frame: {fu[:200]}")
                    elif "punish" in fu and "iframe=1" in fu:
                        punish_frame = fr
                    elif "punish" in fu and not rc_frame:
                        if not punish_frame:
                            punish_frame = fr

                # Work in the punish frame (parent of reCAPTCHA)
                work_frame = punish_frame or rc_frame
                if work_frame:
                    work_frame.wait_for_load_state("domcontentloaded", timeout=20000)
                    time.sleep(3)
                    tmd["steps"].append(f"work_frame loaded: {work_frame.url[:150]}")

                    # Try to find and click reCAPTCHA checkbox
                    clicked = False
                    # In the reCAPTCHA frame
                    if rc_frame:
                        try:
                            rc_frame.wait_for_load_state("domcontentloaded", timeout=15000)
                            time.sleep(2)
                            # reCAPTCHA v2 checkbox
                            for sel in ["#recaptcha-anchor", "input[type='checkbox']",
                                        ".recaptcha-checkbox", "[aria-label='I am not a robot']",
                                        ".rc-checkbox", "span[role='checkbox']",
                                        "#slider", ".recaptcha-slide-button"]:
                                cb = rc_frame.query_selector(sel)
                                if cb:
                                    tmd["steps"].append(f"RC checkbox via {sel}")
                                    cb.scroll_into_view_if_needed()
                                    time.sleep(0.5)
                                    cb.click()
                                    clicked = True
                                    time.sleep(5)
                                    break
                        except Exception as e2:
                            tmd["steps"].append(f"RC frame err: {e2}")

                    # If not found in RC frame, try in punish frame
                    if not clicked and work_frame:
                        for sel in ["#recaptcha-anchor", "input[type='checkbox']",
                                    ".recaptcha-checkbox", "iframe[src*='recaptcha']",
                                    "canvas", ".rc-slider-button", "[class*='slide']"]:
                            cb = work_frame.query_selector(sel)
                            if cb:
                                tmd["steps"].append(f"PF element via {sel}")
                                try:
                                    cb.click()
                                    clicked = True
                                    time.sleep(5)
                                except: pass
                                break

                    # If still not clicked, try main page
                    if not clicked:
                        for sel in ["#recaptcha-anchor", ".g-recaptcha",
                                    "iframe[src*='recaptcha']", "iframe[src*='google.com/recaptcha']"]:
                            w = page.query_selector(sel)
                            if w:
                                tmd["steps"].append(f"Main page: {sel}")
                                try:
                                    w.click()
                                    clicked = True
                                    time.sleep(5)
                                except: pass
                                break

                    tmd["steps"].append(f"clicked={clicked}")

                    # Poll for token in textarea (in punish frame or main page)
                    token_found = False
                    for attempt in range(6):
                        time.sleep(3)
                        # Check textarea in punish frame
                        if work_frame:
                            try:
                                ta = work_frame.query_selector("textarea")
                                if ta:
                                    val = ta.input_value()
                                    if val and len(val) > 20:
                                        tmd["token"] = val[:300]
                                        token_found = True
                                        tmd["steps"].append(f"token in PF textarea (attempt {attempt+1})")
                                        break
                            except: pass
                        # Check textarea in main page
                        try:
                            ta2 = page.query_selector("textarea")
                            if ta2:
                                val2 = ta2.input_value()
                                if val2 and len(val2) > 20:
                                    tmd["token"] = val2[:300]
                                    token_found = True
                                    tmd["steps"].append(f"token in main textarea (attempt {attempt+1})")
                                    break
                        except: pass
                        # Check for success indicator
                        for sel in ["[class*='success']","[class*='verified']","[class*='passed']",
                                    "[class*='complete']",".tmd-success","[class*='done']"]:
                            try:
                                if work_frame and work_frame.query_selector(sel):
                                    tmd["result"] = "success"
                                    tmd["steps"].append(f"SUCCESS indicator {sel} (attempt {attempt+1})")
                                    token_found = True
                                    break
                                if page.query_selector(sel):
                                    tmd["result"] = "success"
                                    tmd["steps"].append(f"SUCCESS main {sel} (attempt {attempt+1})")
                                    token_found = True
                                    break
                            except: pass
                        if token_found: break

                    if token_found:
                        tmd["result"] = "token_received"
                    else:
                        tmd["result"] = "timeout_no_token"

                    # After token, wait and check if TMD overlay disappears
                    time.sleep(4)
                    tmd["overlay_gone"] = not page.query_selector("[class*='punish'],[class*='tmd-overlay']")

            except Exception as e:
                tmd["steps"].append(f"ERR: {e}")
                tmd["result"] = f"error: {e}"
                R["errors"].append(f"tmd: {e}")

        R["tmd"] = tmd
        print(f"[42] TMD result: {tmd['result']}")

        # ── 6. MTOP retry via page.evaluate (with sign) ──
        print("[42] MTOP retry via page.evaluate…")
        try:
            mtop_eval = page.evaluate("""
                async () => {
                    const pid = '""" + PRODUCT_ID + """';
                    const t = Date.now();
                    const data = JSON.stringify({productId: pid});
                    const cb = 'mtopcb42';
                    window[cb] = (r) => { window.__mtop42 = r; };
                    const u = `https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/`
                        + `?jsv=2.5.1&appKey=12574478&t=${t}`
                        + `&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp&v=1.0`
                        + `&timeout=15000&dataType=originaljsonp&callback=${cb}`
                        + `&data=${encodeURIComponent(data)}`;
                    const s = document.createElement('script');
                    s.src = u;
                    s.onerror = () => { window.__mtop42 = {err: 'script error'}; };
                    document.head.appendChild(s);
                    await new Promise(r => setTimeout(r, 10000));
                    const res = window.__mtop42 || null;
                    return res ? {ok:true, data:res} : {ok:false, msg:'no response'};
                }
            """)
            R["strategies"]["mtop_eval"] = mtop_eval
            ret = mtop_eval.get("data",{}).get("ret",[]) if isinstance(mtop_eval,dict) else []
            print(f"[42] MTOP eval ret: {ret}")
        except Exception as e:
            R["strategies"]["mtop_eval"] = {"error": str(e)}
            R["errors"].append(f"mtop_eval: {e}")

        # ── 7. MTOP via curl_cffi — FIXED: cookies as DICT ──
        print("[42] MTOP via curl_cffi (fixed)…")
        try:
            cookies_list = ctx.cookies()
            # Build dict: {name: value}
            ck_dict = {}
            h5tk = ""
            for c in cookies_list:
                ck_dict[c["name"]] = c["value"]
                if c["name"] == "_m_h5_tk":
                    h5tk = c["value"].split("_")[0]

            R["cookies_info"] = {"count": len(ck_dict), "names": list(ck_dict.keys()),
                                 "has_h5tk": bool(h5tk)}

            t_ms = str(int(time.time()*1000))
            app_key = "12574478"
            data_str = json.dumps({"productId": PRODUCT_ID}, separators=(",",":"))
            sign = hashlib.md5(f"{h5tk}&{t_ms}&{app_key}&{data_str}".encode()).hexdigest()
            url = (f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
                   f"?jsv=2.5.1&appKey={app_key}&t={t_ms}&sign={sign}"
                   f"&api=mtop.aliexpress.pdp.pc.query&type=originaljson&v=1.0"
                   f"&timeout=15000&dataType=json"
                   f"&data={urllib.parse.quote(data_str)}")

            from curl_cffi import requests as cr
            r2 = cr.get(url, cookies=ck_dict, headers={
                "User-Agent": UA,
                "Referer": PRODUCT_URL,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.aliexpress.com",
            }, impersonate="chrome", timeout=15)

            md = {"status": r2.status_code, "body_len": len(r2.text), "body": r2.text[:3000]}
            try: md["parsed"] = r2.json()
            except: pass
            R["strategies"]["mtop_cffi"] = md
            ret_cffi = md.get("parsed",{}).get("ret",[]) if isinstance(md.get("parsed"),dict) else []
            print(f"[42] cffi MTOP: {r2.status_code}, ret={ret_cffi}")

            # Alternative endpoints
            for api_name in ["mtop.aliexpress.pdp.query",
                             "mtop.aliexpress.detail.get",
                             "mtop.aliexpress.pdp.mobile.query",
                             "mtop.relationrecommend.AliExpressRecommend.recommend"]:
                try:
                    t2 = str(int(time.time()*1000))
                    sign2 = hashlib.md5(f"{h5tk}&{t2}&{app_key}&{data_str}".encode()).hexdigest()
                    u2 = (f"https://acs.aliexpress.com/h5/{api_name}/1.0/"
                          f"?jsv=2.5.1&appKey={app_key}&t={t2}&sign={sign2}"
                          f"&api={api_name}&type=originaljson&v=1.0"
                          f"&timeout=10000&dataType=json"
                          f"&data={urllib.parse.quote(data_str)}")
                    r3 = cr.get(u2, cookies=ck_dict, headers={
                        "User-Agent": UA,
                        "Referer": PRODUCT_URL,
                    }, impersonate="chrome", timeout=10)
                    alt = {"api": api_name, "status": r3.status_code, "body": r3.text[:2000]}
                    try: alt["parsed"] = r3.json()
                    except: pass
                    R["strategies"][f"mtop_alt_{api_name.split('.')[-1]}"] = alt
                    ret_alt = alt.get("parsed",{}).get("ret",[]) if isinstance(alt.get("parsed"),dict) else []
                    print(f"[42] alt {api_name}: {r3.status_code} ret={ret_alt}")
                except Exception as ae:
                    R["strategies"][f"mtop_alt_{api_name.split('.')[-1]}"] = {"error": str(ae)}
                    print(f"[42] alt {api_name}: ERR {ae}")
        except Exception as e:
            R["strategies"]["mtop_cffi"] = {"error": str(e)}
            R["errors"].append(f"mtop_cffi: {e}")
            print(f"[42] cffi ERR: {e}")

        # ── 8. TMD punish API direct (x5secdata) ──
        print("[42] TMD punish API direct…")
        try:
            # Extract x5secdata from intercepted MTOP responses
            x5secdata = None
            for mt in mtop_log:
                if "punish" in mt.get("url","") and "x5secdata=" in mt.get("url",""):
                    m = re.search(r'x5secdata=([^&]+)', mt["url"])
                    if m:
                        x5secdata = m.group(1)
                        break
            if not x5secdata:
                # Try from MTOP body
                for mt in mtop_log:
                    body = mt.get("body","")
                    m = re.search(r'x5secdata=([^"&]+)', body)
                    if m:
                        x5secdata = m.group(1)
                        break

            R["strategies"]["tmd_direct"] = {"x5secdata_found": bool(x5secdata),
                                             "x5secdata": (x5secdata or "")[:200]}
            if x5secdata:
                # POST to TMD verify endpoint
                from curl_cffi import requests as cr
                tmd_url = f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/_____tmd_____/verify"
                r4 = cr.post(tmd_url,
                    data={"x5secdata": x5secdata, "token": tmd.get("token","")},
                    cookies=ck_dict,
                    headers={"User-Agent": UA, "Referer": PRODUCT_URL},
                    impersonate="chrome", timeout=10)
                R["strategies"]["tmd_direct"]["verify"] = {
                    "status": r4.status_code, "body": r4.text[:1000]}
                print(f"[42] TMD verify: {r4.status_code}")
        except Exception as e:
            R["strategies"]["tmd_direct"] = {"error": str(e)}
            R["errors"].append(f"tmd_direct: {e}")

        # ── 9. DOM parse (post-TMD) ──
        print("[42] DOM parse…")
        dom = {}
        try:
            h1 = page.query_selector("h1")
            dom["h1"] = h1.inner_text().strip() if h1 else None

            og_t = page.query_selector('meta[property="og:title"]')
            dom["og_title"] = og_t.get_attribute("content") if og_t else None
            og_i = page.query_selector('meta[property="og:image"]')
            dom["og_image"] = og_i.get_attribute("content") if og_i else None

            # Price elements - broader
            pels = []
            for sel in ["[class*='price']","[class*='Price']",".i-price",".sale-price",
                        "[data-testid*='price']",".price-wrap","[class*='amount']",
                        "[class*='money']","[class*='cost']","[class*='value']"]:
                for el in page.query_selector_all(sel)[:8]:
                    txt = el.inner_text().strip()
                    if txt and any(c.isdigit() for c in txt) and len(txt) < 100:
                        pels.append({"sel": sel, "txt": txt[:100]})
            dom["prices"] = pels[:25]

            # Images
            dom["images"] = [i.get_attribute("src") for i in
                page.query_selector_all("img[src*='aliexpress-media'],img[src*='ae-pic']")[:25]
                if i.get_attribute("src")]

            # Stock / availability
            st = page.query_selector("[class*='stock'],[class*='availability'],[class*='out-of-stock']")
            dom["stock"] = st.inner_text().strip()[:100] if st else None

            # Category / breadcrumb
            cat = page.query_selector("[class*='breadcrumb'] a, [class*='category'] a, nav a")
            dom["category"] = cat.inner_text().strip()[:100] if cat else None

            # Variants / SKU
            dom["variants_n"] = len(page.query_selector_all(
                "[class*='sku'] [class*='item'],[class*='variant'] [class*='item'],[class*='attr'] [class*='value']"))

            # Seller
            seller = page.query_selector("[class*='seller'],[class*='store'],[class*='shop']")
            dom["seller"] = seller.inner_text().strip()[:100] if seller else None

        except Exception as e:
            dom["error"] = str(e)
            R["errors"].append(f"dom: {e}")
        R["strategies"]["dom"] = dom

        # ── 10. Final data assembly ──
        print("[42] Assembling final data…")
        fd = {}

        # Priority 1: MTOP (check all variants)
        for key in ["mtop_cffi","mtop_eval"]:
            ms = R["strategies"].get(key, {})
            pj = ms.get("parsed") or (ms.get("data") if isinstance(ms.get("data"),dict) else None)
            if pj and isinstance(pj, dict):
                rets = pj.get("ret",[])
                if rets and "SUCCESS" in str(rets[0]):
                    fd = {"source": key, "raw": pj.get("data",{})}
                    break

        # Priority 2: DOM with real prices
        if not fd:
            real_prices = [p for p in dom.get("prices",[]) if re.search(r'\d+[\.,]\d{2}', p["txt"])]
            if real_prices or dom.get("h1"):
                fd = {
                    "source":"dom",
                    "title": dom.get("h1") or dom.get("og_title"),
                    "price": real_prices[0]["txt"] if real_prices else
                             (dom["prices"][0]["txt"] if dom.get("prices") else None),
                    "image": dom.get("og_image"),
                    "images": dom.get("images",[]),
                    "stock": dom.get("stock"),
                    "category": dom.get("category"),
                    "seller": dom.get("seller"),
                }

        # Priority 3: HTML analysis
        if not fd:
            if ha.get("prices") or ha.get("titles"):
                fd = {
                    "source":"html_analysis",
                    "title": ha.get("titles",[None])[0] if ha.get("titles") else None,
                    "price": ha.get("prices",[None])[0] if ha.get("prices") else None,
                    "images": dom.get("images",[]),
                }

        # Priority 4: meta fallback
        if not fd:
            fd = {"source":"meta_fallback",
                  "title": ha.get("titles",[None])[0] if ha.get("titles") else None,
                  "image": dom.get("og_image"),
                  "images": dom.get("images",[])}

        R["final_data"] = fd

        # ── 11. Save ──
        R["intercepted_mtop"] = mtop_log[:25]
        ctx.close()

    with open(RESULT,"w",encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)

    sz = os.path.getsize(RESULT)
    print(f"[42] DONE → {RESULT} ({sz} B)")
    print(f"[42] source={R['final_data'].get('source')}")
    print(f"[42] tmd={R['tmd'].get('result')}")
    print(f"[42] prices={R['html_analysis'].get('prices',[])[:10]}")
    print(f"[42] dom_prices={R['strategies'].get('dom',{}).get('prices',[])[:5]}")
    print(f"[42] errors={R['errors']}")

if __name__ == "__main__":
    main()
