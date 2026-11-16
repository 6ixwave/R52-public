#!/usr/bin/env python3
"""
diag_ali_pw41.py — AliExpress: aggressive HTML parse + TMD/reCAPTCHA interaction + MTOP retry
"""
import json, time, re, os, sys, hashlib, urllib.parse
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
PROFILE = "pw_ali_profile_pw41"
RESULT = "diag_ali_pw41_result.json"

def main():
    from playwright.sync_api import sync_playwright
    R = {
        "product_id": PRODUCT_ID, "url": PRODUCT_URL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw41",
        "strategies": {}, "intercepted_mtop": [],
        "final_data": None, "errors": [],
        "html_analysis": {}, "tmd": {}
    }

    with sync_playwright() as p:
        if os.path.exists(PROFILE):
            import shutil; shutil.rmtree(PROFILE)

        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False,
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US", timezone_id="Europe/Moscow",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ── 1. Warm-up ──
        print("[41] Warm-up homepage…")
        page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        n_warm = len(ctx.cookies())
        print(f"[41] Cookies after warm-up: {n_warm}")

        # ── 2. Product page ──
        print("[41] Loading product…")
        mtop_log = []
        def _on_resp(resp):
            u = resp.url
            if "acs.aliexpress.com" in u or "mtop" in u:
                try:
                    b = resp.text()
                    mtop_log.append({"url": u[:300], "status": resp.status,
                                     "body_len": len(b), "body": b[:2000]})
                except: pass
        page.on("response", _on_resp)

        page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(6)
        cur = page.url
        print(f"[41] URL: {cur}")
        R["final_url"] = cur

        # ── 3. Aggressive HTML parse ──
        print("[41] Parsing HTML…")
        html = page.content()
        R["html_length"] = len(html)

        ha = {}

        # 3a. Embedded JSON blobs
        for name, pat in [
            ("__INIT_DATA", r'window\.__INIT_DATA\s*=\s*(\{.+?\})\s*;?\s*(?:</script>|$)'),
            ("runParams",   r'window\.runParams\s*=\s*(\{.+?\})\s*;'),
            ("__NEXT_DATA", r'window\.__NEXT_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>'),
            ("initialState",r'window\.initialState\s*=\s*(\{.+?\})\s*;'),
        ]:
            m = re.search(pat, html, re.DOTALL)
            if m:
                raw = m.group(1)
                ha[name] = raw[:3000]
                try: ha[name + "_parsed"] = json.loads(raw)
                except: pass
                print(f"[41] Found {name}: {len(raw)} chars")

        # 3b. Price patterns (broad)
        price_pats = [
            r'"(?:salePrice|price|minPrice|maxPrice|formatPrice|displayPrice|originalPrice|promotionPrice)"\s*:\s*"?([\d,.]+)"?',
            r'data-price["\s]*[:=]["\s]*([\d,.]+)',
            r'class=["\'][^"\']*price[^"\']*["\'][^>]*>\s*\$?€?£?([\d,.]+)',
            r'(?:USD|EUR|€|\$)\s*([\d,]+\.?\d*)',
            r'"(?:priceText|priceDisplay)"\s*:\s*"([^"]+)"',
            r'"(?:minAmount|maxAmount)"\s*:\s*"?([\d.]+)"?',
        ]
        prices = []
        for pat in price_pats:
            ms = re.findall(pat, html)
            prices.extend(ms[:8])
        ha["prices"] = prices[:25]
        print(f"[41] Prices found: {prices[:10]}")

        # 3c. Titles
        titles = []
        for pat in [r'<h1[^>]*>([^<]{10,300})</h1>',
                    r'"title"\s*:\s*"([^"]{10,300})"',
                    r'og:title["\s]*content=["\']([^"\']+)',
                    r'name=["\']title["\']\s+content=["\']([^"\']+)']:
            titles.extend(re.findall(pat, html, re.I)[:5])
        ha["titles"] = titles[:10]

        # 3d. JSON-LD
        jld = []
        for blk in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try: jld.append(json.loads(blk.strip()))
            except: jld.append({"raw": blk.strip()[:500]})
        ha["jsonld"] = jld[:3]

        # 3e. SKU / variants / stock
        ha["sku_ids"] = re.findall(r'"skuId"\s*:\s*"?(\d+)', html)[:10]
        ha["variants"] = len(re.findall(r'"(?:variants|attributes|skuList)"\s*:\s*\[', html))
        ha["stock"] = re.findall(r'"(?:stock|inStock|quantity|availability)"\s*:\s*"?([\w]+)"?', html)[:10]

        # 3f. Category / breadcrumb
        ha["breadcrumb"] = re.findall(r'class=["\'][^"\']*breadcrumb[^"\']*["\'][^>]*>(.*?)</', html, re.DOTALL)[:3]

        # 3g. Full text snippet around "price"
        price_ctx = []
        for m in re.finditer(r'(?i)(price|€|\$|USD|EUR)', html):
            s = max(0, m.start()-80); e = min(len(html), m.end()+80)
            snippet = html[s:e].replace("\n"," ").strip()
            if any(c.isdigit() for c in snippet):
                price_ctx.append(snippet)
            if len(price_ctx) >= 15: break
        ha["price_context"] = price_ctx

        R["html_analysis"] = ha

        # ── 4. TMD / reCAPTCHA interaction ──
        print("[41] Checking TMD…")
        time.sleep(2)
        tmd = {"present": False, "iframes": 0, "steps": [], "token": None, "result": "none"}

        # Collect all frames
        all_frames = page.frames
        tmd["total_frames"] = len(all_frames)
        for fr in all_frames:
            fu = fr.url
            if any(k in fu for k in ("tmd","punish","recaptcha","x5sec")):
                tmd["present"] = True
                tmd["iframes"] += 1
                tmd["steps"].append(f"frame: {fu[:200]}")

        # DOM iframes
        for ifr in page.query_selector_all("iframe"):
            src = ifr.get_attribute("src") or ""
            if any(k in src for k in ("tmd","punish","recaptcha")):
                tmd["present"] = True
                tmd["steps"].append(f"iframe src: {src[:200]}")

        # TMD overlay in DOM
        for sel in ["[class*='tmd']","[id*='tmd']","[class*='punish']","[class*='captcha-overlay']",".sufei-punish"]:
            el = page.query_selector(sel)
            if el:
                tmd["present"] = True
                tmd["steps"].append(f"DOM: {sel}")
                break

        if tmd["present"]:
            tmd["result"] = "attempted"
            print("[41] TMD found, interacting…")
            try:
                # Find the reCAPTCHA / punish frame
                rc_frame = None
                for fr in all_frames:
                    if "recaptcha" in fr.url or ("punish" in fr.url and "iframe=1" in fr.url):
                        rc_frame = fr
                        break
                if not rc_frame:
                    for fr in all_frames:
                        if "punish" in fr.url:
                            rc_frame = fr
                            break

                if rc_frame:
                    rc_frame.wait_for_load_state("domcontentloaded", timeout=15000)
                    time.sleep(2)
                    tmd["steps"].append(f"rc_frame loaded: {rc_frame.url[:150]}")

                    # Try checkbox (reCAPTCHA v2)
                    for sel in ["#recaptcha-anchor","input[type='checkbox']",".recaptcha-checkbox",
                                "[aria-label='I am not a robot']",".rc-checkbox"]:
                        cb = rc_frame.query_selector(sel)
                        if cb:
                            tmd["steps"].append(f"checkbox via {sel}")
                            cb.click()
                            time.sleep(4)
                            break

                    # Try canvas slider
                    cv = rc_frame.query_selector("canvas")
                    if cv:
                        box = cv.bounding_box()
                        if box:
                            tmd["steps"].append(f"canvas at {box}")
                            page.mouse.move(box["x"]+15, box["y"]+box["height"]/2)
                            page.mouse.down()
                            time.sleep(0.15)
                            page.mouse.move(box["x"]+box["width"]-15, box["y"]+box["height"]/2, steps=30)
                            time.sleep(0.15)
                            page.mouse.up()
                            tmd["steps"].append("slider drag done")
                            time.sleep(3)

                    # Try any button / "verify" / "confirm"
                    for sel in ["button","[role='button']",".btn","input[type='submit']",
                                "[class*='verify']","[class*='confirm']","[class*='submit']"]:
                        btn = rc_frame.query_selector(sel)
                        if btn:
                            txt = (btn.inner_text() or "").strip()[:50]
                            tmd["steps"].append(f"button '{txt}' via {sel}")
                            btn.click()
                            time.sleep(3)
                            break

                    # Check textarea for token
                    ta = rc_frame.query_selector("textarea")
                    if ta:
                        val = ta.input_value()
                        tmd["token"] = (val or "")[:200]
                        tmd["steps"].append(f"textarea: {val[:80] if val else 'empty'}")

                    # Success indicator
                    for sel in ["[class*='success']","[class*='verified']","[class*='passed']",
                                "[class*='complete']",".tmd-success"]:
                        if rc_frame.query_selector(sel):
                            tmd["result"] = "success"
                            tmd["steps"].append(f"SUCCESS via {sel}")
                            break
                else:
                    tmd["steps"].append("no rc_frame found, trying main page")
                    # Try in main page
                    for sel in [".g-recaptcha","[class*='recaptcha']","iframe[src*='recaptcha']"]:
                        w = page.query_selector(sel)
                        if w:
                            tmd["steps"].append(f"main page: {sel}")
                            break

            except Exception as e:
                tmd["steps"].append(f"ERR: {e}")
                tmd["result"] = f"error: {e}"

        # Post-TMD wait
        time.sleep(3)
        R["tmd"] = tmd

        # ── 5. MTOP retry via page.evaluate (has cookies) ──
        print("[41] MTOP retry via page.evaluate…")
        try:
            mtop_eval = page.evaluate("""
                async () => {
                    const pid = '""" + PRODUCT_ID + """';
                    const t = Date.now();
                    const data = JSON.stringify({productId: pid});
                    const cb = 'mtopcb41';
                    window[cb] = (r) => { window.__mtop41 = r; };
                    const u = `https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/`
                        + `?jsv=2.5.1&appKey=12574478&t=${t}`
                        + `&api=mtop.aliexpress.pdp.pc.query&type=originaljsonp&v=1.0`
                        + `&timeout=15000&dataType=originaljsonp&callback=${cb}`
                        + `&data=${encodeURIComponent(data)}`;
                    const s = document.createElement('script');
                    s.src = u;
                    document.head.appendChild(s);
                    await new Promise(r => setTimeout(r, 8000));
                    const res = window.__mtop41 || null;
                    return res ? {ok:true, data:res} : {ok:false, msg:'no response'};
                }
            """)
            R["strategies"]["mtop_eval"] = mtop_eval
            print(f"[41] MTOP eval: ok={mtop_eval.get('ok') if isinstance(mtop_eval,dict) else '?'}")
        except Exception as e:
            R["strategies"]["mtop_eval"] = {"error": str(e)}
            R["errors"].append(f"mtop_eval: {e}")

        # ── 6. MTOP via curl_cffi with proper sign ──
        print("[41] MTOP via curl_cffi…")
        try:
            cookies = ctx.cookies()
            ck = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            h5tk = ""
            for c in cookies:
                if c["name"] == "_m_h5_tk":
                    h5tk = c["value"].split("_")[0]; break

            t_ms = str(int(time.time()*1000))
            app_key = "12574478"
            data_str = json.dumps({"productId": PRODUCT_ID}, separators=(",",":"))
            sign = hashlib.md5(f"{h5tk}&{t_ms}&{app_key}&{data_str}".encode()).hexdigest()
            url = (f"https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/"
                   f"?jsv=2.5.1&appKey={app_key}&t={t_ms}&sign=[REDACTED_AUTH_SECRET]"
                   f"&api=mtop.aliexpress.pdp.pc.query&type=originaljson&v=1.0"
                   f"&timeout=15000&dataType=json"
                   f"&data={urllib.parse.quote(data_str)}")

            from curl_cffi import requests as cr
            r2 = cr.get(url, cookies=ck, headers={
                "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": PRODUCT_URL,
                "Accept":"application/json, text/plain, */*"
            }, impersonate="chrome", timeout=15)

            md = {"status": r2.status_code, "body_len": len(r2.text), "body": r2.text[:3000]}
            try: md["parsed"] = r2.json()
            except: pass
            R["strategies"]["mtop_cffi"] = md
            print(f"[41] cffi MTOP: {r2.status_code}, {len(r2.text)}B")

            # Try alternative endpoints
            for api_name in ["mtop.aliexpress.pdp.query","mtop.aliexpress.detail.get","mtop.aliexpress.pdp.mobile.query"]:
                try:
                    t2 = str(int(time.time()*1000))
                    sign2 = hashlib.md5(f"{h5tk}&{t2}&{app_key}&{data_str}".encode()).hexdigest()
                    u2 = (f"https://acs.aliexpress.com/h5/{api_name}/1.0/"
                          f"?jsv=2.5.1&appKey={app_key}&t={t2}&sign=[REDACTED_AUTH_SECRET]"
                          f"&api={api_name}&type=originaljson&v=1.0"
                          f"&timeout=10000&dataType=json"
                          f"&data={urllib.parse.quote(data_str)}")
                    r3 = cr.get(u2, cookies=ck, headers={
                        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Referer": PRODUCT_URL
                    }, impersonate="chrome", timeout=10)
                    alt = {"api": api_name, "status": r3.status_code, "body": r3.text[:1500]}
                    try: alt["parsed"] = r3.json()
                    except: pass
                    R["strategies"][f"mtop_alt_{api_name.split('.')[-1]}"] = alt
                    print(f"[41] alt {api_name}: {r3.status_code}")
                except Exception as ae:
                    R["strategies"][f"mtop_alt_{api_name.split('.')[-1]}"] = {"error": str(ae)}
        except Exception as e:
            R["strategies"]["mtop_cffi"] = {"error": str(e)}
            R["errors"].append(f"mtop_cffi: {e}")

        # ── 7. DOM parse (post-TMD) ──
        print("[41] DOM parse…")
        dom = {}
        try:
            h1 = page.query_selector("h1")
            dom["h1"] = h1.inner_text().strip() if h1 else None

            og_t = page.query_selector('meta[property="og:title"]')
            dom["og_title"] = og_t.get_attribute("content") if og_t else None
            og_i = page.query_selector('meta[property="og:image"]')
            dom["og_image"] = og_i.get_attribute("content") if og_i else None

            # Price elements
            pels = []
            for sel in ["[class*='price']","[class*='Price']",".i-price",".sale-price",
                        "[data-testid*='price']",".price-wrap","[class*='amount']"]:
                for el in page.query_selector_all(sel)[:5]:
                    txt = el.inner_text().strip()
                    if txt and any(c.isdigit() for c in txt):
                        pels.append({"sel": sel, "txt": txt[:100]})
            dom["prices"] = pels[:20]

            # Images
            dom["images"] = [i.get_attribute("src") for i in
                page.query_selector_all("img[src*='aliexpress-media'],img[src*='ae-pic']")[:20]
                if i.get_attribute("src")]

            # Stock
            st = page.query_selector("[class*='stock'],[class*='availability']")
            dom["stock"] = st.inner_text().strip()[:100] if st else None

            # Category
            cat = page.query_selector("[class*='breadcrumb'] a, [class*='category'] a")
            dom["category"] = cat.inner_text().strip()[:100] if cat else None

            # Variants
            dom["variants_n"] = len(page.query_selector_all(
                "[class*='sku'] [class*='item'],[class*='variant'] [class*='item']"))

        except Exception as e:
            dom["error"] = str(e)
            R["errors"].append(f"dom: {e}")
        R["strategies"]["dom"] = dom

        # ── 8. Final data assembly ──
        print("[41] Assembling final data…")
        fd = {}

        # Priority 1: MTOP
        for key in ["mtop_cffi","mtop_eval"]:
            ms = R["strategies"].get(key, {})
            pj = ms.get("parsed") or (ms.get("data") if isinstance(ms.get("data"),dict) else None)
            if pj:
                rets = pj.get("ret",[])
                if rets and "SUCCESS" in str(rets[0]):
                    fd = {"source": key, "raw": pj.get("data",{})}
                    break

        # Priority 2: DOM
        if not fd:
            if dom.get("prices") or dom.get("h1"):
                fd = {
                    "source":"dom",
                    "title": dom.get("h1") or dom.get("og_title"),
                    "price": dom["prices"][0]["txt"] if dom.get("prices") else None,
                    "image": dom.get("og_image"),
                    "images": dom.get("images",[]),
                    "stock": dom.get("stock"),
                    "category": dom.get("category"),
                }

        # Priority 3: HTML analysis
        if not fd:
            if ha.get("prices") or ha.get("titles"):
                fd = {
                    "source":"html_analysis",
                    "title": ha.get("titles",[None])[0] if ha.get("titles") else None,
                    "price": ha.get("prices",[None])[0] if ha.get("prices") else None,
                    "price_context": ha.get("price_context",[])[:5],
                    "images": dom.get("images",[]),
                }

        # Priority 4: meta fallback
        if not fd:
            fd = {"source":"meta_fallback",
                  "title": ha.get("titles",[None])[0] if ha.get("titles") else None,
                  "image": dom.get("og_image"),
                  "images": dom.get("images",[])}

        R["final_data"] = fd

        # ── 9. Save ──
        R["intercepted_mtop"] = mtop_log[:20]
        R["cookies"] = {"count": len(ctx.cookies()),
                        "names": [c["name"] for c in ctx.cookies()]}
        ctx.close()

    with open(RESULT,"w",encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)

    sz = os.path.getsize(RESULT)
    print(f"[41] DONE → {RESULT} ({sz} B)")
    print(f"[41] source={R['final_data'].get('source')}")
    print(f"[41] tmd={R['tmd'].get('result')}")
    print(f"[41] prices={R['html_analysis'].get('prices',[])[:8]}")
    print(f"[41] errors={R['errors']}")

if __name__ == "__main__":
    main()
