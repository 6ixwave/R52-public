"""v136: WB — Playwright full render + XHR intercept + token-based API.

Strategy:
  1) Playwright: load product page, wait for networkidle + specific selectors
  2) Intercept all XHR/fetch to discover data endpoints
  3) Extract price, images, title, specs from rendered DOM
  4) Use x_wbaas_token cookie for catalog/price API calls
  5) Fallback: parse __NEXT_DATA__ or window.__STATE__ from HTML

Budget: 150s
"""
import json, logging, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("v136")

BUDGET = 150
START = time.monotonic()
NM = 199298871  # Овсяный смарт коктейль

def rem():
    return BUDGET - (time.monotonic() - START)

def playwright_full(nm_id):
    log.info("=== PLAYWRIGHT FULL nm=%d ===", nm_id)
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    
    from playwright.sync_api import sync_playwright
    
    captured_requests = []
    captured_responses = []
    result = {
        "price": None, "price_raw": None, "images": [], "videos": [],
        "title": None, "html_size": 0, "xhr_endpoints": [],
        "cookies": [], "state_json": None, "render_wait": 0
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
            ]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
        )
        
        # Intercept XHR/fetch
        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type in ("xhr", "fetch"):
                    info = {
                        "url": resp.url,
                        "status": resp.status,
                        "type": req.resource_type,
                        "method": req.method,
                    }
                    # Try to read JSON body for small responses
                    if resp.status == 200 and len(resp.url) < 500:
                        try:
                            body = resp.text()
                            if len(body) < 50000:
                                info["body_len"] = len(body)
                                if "price" in body or "image" in body or "card" in body.lower():
                                    info["body_preview"] = body[:500]
                        except Exception:
                            pass
                    captured_responses.append(info)
            except Exception:
                pass
        
        ctx.on("response", on_response)
        
        page = ctx.new_page()
        
        # Navigate
        t0 = time.monotonic()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            log.info("  goto: %d ms", int((time.monotonic()-t0)*1000))
        except Exception as e:
            log.warning("  goto err: %s", str(e)[:120])
        
        # Phase 1: Wait for challenge to resolve (body > 5000)
        for i in range(15):
            if rem() < 30:
                break
            try:
                blen = page.evaluate("document.body ? document.body.innerHTML.length : 0")
                if blen > 5000:
                    log.info("  Challenge resolved at poll %d, body=%dB", i, blen)
                    break
            except Exception:
                pass
            time.sleep(1.5)
        
        # Phase 2: Wait for network idle (product data loads via XHR)
        log.info("  Waiting for networkidle...")
        t1 = time.monotonic()
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
            log.info("  networkidle: %d ms", int((time.monotonic()-t1)*1000))
        except Exception:
            log.info("  networkidle timeout (continuing)")
        
        # Phase 3: Wait for product-specific elements
        selectors_to_wait = [
            "[data-test='price-current']",
            ".price-current",
            "[class*='Price']",
            "[data-qa='price']",
            "img[src*='wbbasket']",
            "[class*='photo']",
        ]
        found_selector = None
        for sel in selectors_to_wait:
            if rem() < 10:
                break
            try:
                page.wait_for_selector(sel, timeout=8000)
                found_selector = sel
                log.info("  Found selector: %s", sel)
                break
            except Exception:
                continue
        
        if not found_selector:
            log.info("  No product selector found, waiting extra 5s")
            time.sleep(5)
        
        # Extract data
        try:
            html = page.content()
            result["html_size"] = len(html)
            log.info("  Final HTML: %dB", len(html))
            
            # Save HTML
            with open(f"pw_wb_{nm_id}_v136.html", "w", encoding="utf-8") as f:
                f.write(html)
            
            # --- TITLE ---
            try:
                t = page.title()
                if t and len(t) > 5:
                    result["title"] = t
                    log.info("  Title: %s", t[:100])
            except Exception:
                pass
            
            # H1
            try:
                h1 = page.query_selector("h1")
                if h1:
                    txt = h1.inner_text().strip()
                    if txt:
                        result["title"] = txt
                        log.info("  H1: %s", txt[:100])
            except Exception:
                pass
            
            # --- PRICE ---
            # Try multiple selectors
            price_selectors = [
                "[data-test='price-current']",
                ".price-current",
                "[class*='price-current']",
                "[data-qa='price']",
                "[class*='Price__Current']",
                "[class*='price'] [class*='current']",
            ]
            for sel in price_selectors:
                try:
                    els = page.query_selector_all(sel)
                    for el in els[:3]:
                        txt = el.inner_text().strip()
                        if txt and any(c.isdigit() for c in txt):
                            log.info("  PRICE [%s]: '%s'", sel, txt[:80])
                            if not result["price"]:
                                result["price"] = txt
                                result["price_raw"] = txt
                except Exception:
                    pass
            
            # og:price meta
            try:
                for prop in ["og:price:amount", "product:price:amount", "og:price"]:
                    meta = page.query_selector(f"meta[property='{prop}']")
                    if meta:
                        val = meta.get_attribute("content")
                        if val:
                            log.info("  meta[%s]: %s", prop, val)
                            if not result["price"]:
                                result["price"] = val
            except Exception:
                pass
            
            # JSON-LD
            try:
                scripts = page.query_selector_all("script[type='application/ld+json']")
                for s in scripts:
                    txt = s.inner_text()
                    if "price" in txt.lower():
                        log.info("  JSON-LD: %s", txt[:300])
                        try:
                            ld = json.loads(txt)
                            if isinstance(ld, dict) and "price" in ld:
                                result["price"] = str(ld["price"])
                                if not result["title"]:
                                    result["title"] = ld.get("name", "")
                        except Exception:
                            pass
            except Exception:
                pass
            
            # --- IMAGES ---
            img_urls = set()
            
            # og:image
            try:
                og = page.query_selector("meta[property='og:image']")
                if og:
                    u = og.get_attribute("content")
                    if u:
                        img_urls.add(u)
                        log.info("  og:image: %s", u[:120])
            except Exception:
                pass
            
            # All img tags
            try:
                imgs = page.query_selector_all("img")
                log.info("  Total <img> tags: %d", len(imgs))
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    data_src = img.get_attribute("data-src") or ""
                    srcset = img.get_attribute("srcset") or ""
                    for u in [src, data_src]:
                        if u and ("wbbasket" in u or "wbstatic" in u or "img.wb" in u):
                            img_urls.add(u)
                    # Parse srcset
                    if srcset:
                        for part in srcset.split(","):
                            u = part.strip().split(" ")[0]
                            if u and ("wbbasket" in u or "wbstatic" in u):
                                img_urls.add(u)
            except Exception:
                pass
            
            # From HTML source
            for m in re.finditer(r'https://[^\s"\'\\]+wbbasket[^\s"\'\\]*\.(?:jpg|jpeg|png|webp)', html):
                img_urls.add(m.group(0))
            for m in re.finditer(r'https://[^\s"\'\\]+wbstatic[^\s"\'\\]*\.(?:jpg|jpeg|png|webp)', html):
                img_urls.add(m.group(0))
            
            # Video
            vid_urls = set()
            for m in re.finditer(r'https://[^\s"\'\\]*wbbasket[^\s"\'\\]*\.(?:mp4|webm)', html):
                vid_urls.add(m.group(0))
            try:
                vids = page.query_selector_all("video")
                for v in vids:
                    src = v.get_attribute("src") or ""
                    if src:
                        vid_urls.add(src)
            except Exception:
                pass
            
            result["images"] = sorted(img_urls)
            result["videos"] = sorted(vid_urls)
            log.info("  Images: %d, Videos: %d", len(result["images"]), len(result["videos"]))
            for i, u in enumerate(result["images"][:8]):
                log.info("    img[%d]: %s", i, u[:130])
            
            # --- STATE / NEXT_DATA ---
            try:
                # window.__STATE__ or __NEXT_DATA__ or similar
                state = page.evaluate("""
                    () => {
                        if (window.__STATE__) return JSON.stringify(window.__STATE__);
                        if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__);
                        if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__);
                        return null;
                    }
                """)
                if state:
                    result["state_json"] = state[:5000]
                    log.info("  State JSON found: %dB", len(state))
                    log.info("  State[:300]: %s", state[:300])
            except Exception as e:
                log.info("  State eval err: %s", str(e)[:80])
            
            # --- COOKIES ---
            cookies = ctx.cookies()
            result["cookies"] = cookies
            log.info("  Cookies (%d):", len(cookies))
            for c in cookies[:15]:
                log.info("    %s = %s (dom=%s)", c["name"], str(c["value"])[:40], c.get("domain",""))
                
        except Exception as e:
            log.warning("  Extract err: %s", str(e)[:120])
        
        # Log captured XHR
        log.info("  === XHR/Fetch captured: %d ===", len(captured_responses))
        for r in captured_responses[:20]:
            log.info("    [%d] %s %s -> %dB", r["status"], r["method"], r["url"][:100], r.get("body_len", 0))
        
        browser.close()
    
    result["xhr_count"] = len(captured_responses)
    return result, captured_responses

def api_with_token(nm_id, cookies):
    """Use x_wbaas_token from Playwright to call APIs."""
    log.info("=== API WITH TOKEN ===")
    token = None
    for c in cookies:
        if c["name"] == "x_wbaas_token":
            token = c["value"]
            break
    
    if not token:
        log.info("  No x_wbaas_token found")
        return {}
    
    log.info("  Token: %s", token[:50])
    
    try:
        from curl_cffi import requests as cffi_req
    except ImportError:
        import requests as cffi_req
    
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
        "Cookie": f"x_wbaas_token={token}",
    }
    
    results = {}
    
    # 1) Catalog API v2
    urls = {
        "catalog_v2": f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
        "catalog_v1": f"https://catalog.wb.ru/cards/v1/detail?curr=rub&nm={nm_id}",
        "price_v2": f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
        "basket_card": None,  # filled below
    }
    
    # Find basket host from card.json
    try:
        # Try common basket hosts
        for host_num in [13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]:
            test_url = f"https://basket-{host_num:02d}.wbbasket.ru/vol{nm_id // 1000000000}/part{nm_id % 1000000000}/{nm_id}/images/big1.png"
            break  # just use first
        # Actually, get imt_id from card.json first
        card_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
        r = cffi_req.get(card_url, headers=hdrs, timeout=10, impersonate="chrome")
        log.info("  card.wb.ru v2 (with token): %d %dB", r.status_code, len(r.content or b""))
        if r.status_code == 200:
            d = r.json()
            items = d.get("data", {}).get("items") or d.get("products") or []
            if items:
                p = items[0]
                sz = (p.get("sizes") or [{}])[0]
                pr = sz.get("price", {})
                log.info("  *** CARD.WB HIT: basic=%s total=%s discount=%s qty=%s ***",
                         pr.get("basic"), pr.get("total"), pr.get("discount"), sz.get("qty"))
                results["card_wb"] = {
                    "price": pr, "qty": sz.get("qty"), "nm": p.get("nm"),
                    "brand": p.get("brand"), "name": p.get("name")
                }
    except Exception as e:
        log.info("  card.wb err: %s", str(e)[:80])
    
    # 2) Other APIs
    for label, u in urls.items():
        if not u or label == "card_v2":
            continue
        if rem() < 8:
            break
        try:
            r = cffi_req.get(u, headers=hdrs, timeout=10, impersonate="chrome")
            clen = len(r.content or b"")
            log.info("  %s: %d %dB", label, r.status_code, clen)
            if r.status_code == 200 and clen > 20:
                d = r.json()
                items = d.get("data", {}).get("items") or d.get("products") or []
                if items:
                    p = items[0]
                    sz = (p.get("sizes") or [{}])[0]
                    pr = sz.get("price", {})
                    log.info("  *** %s HIT: basic=%s total=%s ***", label, pr.get("basic"), pr.get("total"))
                    results[label] = {"price": pr, "nm": p.get("nm")}
        except Exception as e:
            log.info("  %s err: %s", label, str(e)[:60])
        time.sleep(0.5)
    
    # 3) Try basket CDN card.json with token
    try:
        # We know from v132 that basket-13 works for nm=199298871
        # Try to get imt_id
        for host_num in [13, 12, 11, 10]:
            card_url = f"https://basket-{host_num:02d}.wbbasket.ru/cards/{nm_id}/card.json"
            r = cffi_req.get(card_url, headers=hdrs, timeout=8, impersonate="chrome")
            log.info("  basket-%02d card.json: %d %dB", host_num, r.status_code, len(r.content or b""))
            if r.status_code == 200 and len(r.content or b"") > 100:
                d = r.json()
                log.info("  basket card keys: %s", list(d.keys())[:15])
                # Look for price fields
                for k in ["price", "prices", "basicPrice", "totalPrice", "salePrice"]:
                    if k in d:
                        log.info("  basket card[%s]: %s", k, str(d[k])[:100])
                # Look for image fields
                for k in ["image", "images", "photo", "photos", "media", "gallery"]:
                    if k in d:
                        log.info("  basket card[%s]: %s", k, str(d[k])[:200])
                results[f"basket_{host_num:02d}"] = d
                break
    except Exception as e:
        log.info("  basket err: %s", str(e)[:80])
    
    return results

def main():
    log.info("=== v136 START budget=%ds nm=%d ===", BUDGET, NM)
    
    # 1) Playwright full
    pw_result, xhrs = playwright_full(NM)
    
    # 2) API with token
    if rem() > 20:
        api_results = api_with_token(NM, pw_result.get("cookies", []))
    else:
        api_results = {}
    
    # 3) Summary
    log.info("=== v136 SUMMARY ===")
    log.info("  PW: price=%s imgs=%d vids=%d html=%dB xhr=%d",
             pw_result.get("price"), len(pw_result.get("images", [])),
             len(pw_result.get("videos", [])), pw_result.get("html_size", 0),
             pw_result.get("xhr_count", 0))
    log.info("  API: %s", json.dumps({k: "HIT" if v else "MISS" for k, v in api_results.items()}, ensure_ascii=False))
    
    # Save full result
    out = {
        "nm": NM,
        "playwright": {
            "price": pw_result.get("price"),
            "title": pw_result.get("title"),
            "images": pw_result.get("images", []),
            "videos": pw_result.get("videos", []),
            "html_size": pw_result.get("html_size"),
            "xhr_count": pw_result.get("xhr_count"),
            "state_json": pw_result.get("state_json"),
        },
        "api": api_results,
        "xhr_endpoints": [r["url"] for r in xhrs[:30]],
        "total_time": round(time.monotonic() - START, 1),
    }
    with open("v136_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    
    log.info("  Total: %.1fs", time.monotonic() - START)
    log.info("=== v136 DONE ===")

if __name__ == "__main__":
    main()
