"""v135: WB — browser-check.js analysis + Playwright price/media + card.wb.ru mobile.

Strategy:
  A) Download & analyze browser-check.js (understand the challenge)
  B) Playwright headless: load product page, wait for challenge, extract price+images
  C) card.wb.ru with mobile UA / different headers

Budget: 120s (Playwright startup ~10s, page load ~15s)
"""
import json, logging, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("v135")

BUDGET = 120
START = time.monotonic()

def rem():
    return BUDGET - (time.monotonic() - START)

# ============================================================
# A) BROWSER-CHECK.JS ANALYSIS
# ============================================================
def analyze_browser_check():
    log.info("--- A) BROWSER-CHECK.JS ---")
    url = "https://www.wildberries.ru/__wbaas/challenges/antibot/__static/v2/browser-check.js"
    try:
        from curl_cffi import requests as cffi_req
        r = cffi_req.get(url, timeout=10, impersonate="chrome")
    except ImportError:
        import requests
        r = requests.get(url, timeout=10)
    
    if r and r.status_code == 200:
        body = r.text
        log.info("  browser-check.js: %dB", len(body))
        # Save for later analysis
        with open("browser_check_v135.js", "w", encoding="utf-8") as f:
            f.write(body)
        # Key patterns
        for pat in ["cookie", "document.cookie", "setCookie", "localStorage",
                    "fetch", "XMLHttpRequest", "navigator", "canvas",
                    "fingerprint", "token", "hash", "sha", "md5",
                    "site-key", "siteKey", "challenge", "verify",
                    "wb-sid", "antibot", "redirect", "location"]:
            hits = [m.start() for m in re.finditer(pat, body, re.I)]
            if hits:
                log.info("  pattern '%s': %d hits", pat, len(hits))
        # First 500 chars
        log.info("  JS[:500]: %s", body[:500])
        # Look for cookie name
        for m in re.finditer(r'["\']([^"\']*(?:cookie|sid|token|antibot|challenge)[^"\']*)["\']', body, re.I):
            log.info("  cookie/token name: %s", m.group(1))
        # Look for fetch/XHR endpoints
        for m in re.finditer(r'(?:fetch|open|url)\s*\(\s*["\']([^"\']+)["\']', body, re.I):
            log.info("  endpoint: %s", m.group(1))
        return body
    else:
        code = r.status_code if r else "ERR"
        log.info("  browser-check.js: HTTP %s", code)
        return None

def analyze_index_js():
    """Also grab the main challenge module."""
    url = "https://www.wildberries.ru/__wbaas/challenges/antibot/__static/v2/index-DL5hnmG4.js"
    try:
        from curl_cffi import requests as cffi_req
        r = cffi_req.get(url, timeout=10, impersonate="chrome")
    except ImportError:
        import requests
        r = requests.get(url, timeout=10)
    if r and r.status_code == 200:
        body = r.text
        log.info("  index.js: %dB", len(body))
        with open("index_v135.js", "w", encoding="utf-8") as f:
            f.write(body)
        for pat in ["cookie", "document.cookie", "setCookie", "fetch",
                    "navigator", "canvas", "token", "site-key",
                    "wb-sid", "antibot", "redirect", "challenge",
                    "verify", "solve", "complete"]:
            hits = [m.start() for m in re.finditer(pat, body, re.I)]
            if hits:
                log.info("  idx pattern '%s': %d hits", pat, len(hits))
        return body
    return None

# ============================================================
# B) PLAYWRIGHT: full page load
# ============================================================
def playwright_collect(nm_id):
    log.info("--- B) PLAYWRIGHT nm=%d ---")
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("  Playwright not installed")
        return None
    
    result = {"price": None, "images": [], "video": [], "title": None,
              "html_size": 0, "challenge_waited": 0}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()
        
        t0 = time.monotonic()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            log.info("  goto: %d ms, title=%s", int((time.monotonic()-t0)*1000),
                     page.title()[:60])
        except Exception as e:
            log.warning("  goto err: %s", str(e)[:100])
        
        # Wait for challenge to resolve (body changes from 1136B to full page)
        challenge_waited = 0
        for i in range(20):
            if rem() < 15:
                break
            try:
                body_len = page.evaluate("document.body ? document.body.innerHTML.length : 0")
                title = page.title()
                log.info("  wait[%d]: body=%dB title=%s", i, body_len, title[:50])
                if body_len > 5000:
                    log.info("  Challenge resolved after %d polls", i)
                    challenge_waited = i
                    break
            except Exception:
                pass
            time.sleep(1.5)
        
        # Extract data
        try:
            html = page.content()
            result["html_size"] = len(html)
            log.info("  Final HTML: %dB", len(html))
            
            # Title
            t = page.title()
            if t and "Wildberries" not in t:
                result["title"] = t
            
            # Price: look for common patterns
            price_selectors = [
                "[data-test='price-current']",
                ".price-current",
                "[class*='price']",
                "[data-qa='price']",
            ]
            for sel in price_selectors:
                try:
                    els = page.query_selector_all(sel)
                    for el in els[:5]:
                        txt = el.inner_text().strip()
                        if txt and any(c.isdigit() for c in txt):
                            log.info("  PRICE [%s]: %s", sel, txt[:60])
                            if not result["price"]:
                                result["price"] = txt
                except Exception:
                    pass
            
            # Also try og:price meta
            try:
                og_price = page.query_selector("meta[property='og:price:amount']")
                if og_price:
                    val = og_price.get_attribute("content")
                    log.info("  og:price:amount = %s", val)
                    if val:
                        result["price"] = val
            except Exception:
                pass
            
            # Images
            img_urls = set()
            # og:image
            try:
                og_img = page.query_selector("meta[property='og:image']")
                if og_img:
                    u = og_img.get_attribute("content")
                    if u:
                        img_urls.add(u)
            except Exception:
                pass
            
            # All img tags with wbbasket
            try:
                imgs = page.query_selector_all("img")
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    data_src = img.get_attribute("data-src") or ""
                    for u in [src, data_src]:
                        if "wbbasket" in u or "wbstatic" in u:
                            img_urls.add(u)
            except Exception:
                pass
            
            # Also from HTML source (JSON blobs)
            for m in re.finditer(r'(https://[^"\'\\]*wbbasket[^"\'\\]*\.(?:jpg|jpeg|png|webp))', html):
                img_urls.add(m.group(1))
            for m in re.finditer(r'(https://[^"\'\\]*wbbasket[^"\'\\]*\.(?:mp4|webm))', html):
                img_urls.add(m.group(1))
            
            result["images"] = [u for u in img_urls if not u.endswith((".mp4", ".webm"))]
            result["video"] = [u for u in img_urls if u.endswith((".mp4", ".webm"))]
            log.info("  Images: %d, Videos: %d", len(result["images"]), len(result["video"]))
            for i, u in enumerate(result["images"][:5]):
                log.info("    img[%d]: %s", i, u[:120])
            for i, u in enumerate(result["video"][:3]):
                log.info("    vid[%d]: %s", i, u[:120])
            
            # Save HTML for analysis
            with open(f"pw_wb_{nm_id}_v135.html", "w", encoding="utf-8") as f:
                f.write(html)
            
            # Save cookies
            cookies = ctx.cookies()
            log.info("  Cookies after challenge (%d):", len(cookies))
            for c in cookies[:10]:
                log.info("    %s = %s (domain=%s)", c["name"], str(c["value"])[:30], c.get("domain", ""))
            result["cookies"] = cookies
            
        except Exception as e:
            log.warning("  Extract err: %s", str(e)[:100])
        
        browser.close()
    
    log.info("  Playwright done: price=%s, imgs=%d, vids=%d, html=%dB",
             result.get("price", "?")[:30] if result.get("price") else None,
             len(result.get("images", [])), len(result.get("video", [])),
             result.get("html_size", 0))
    return result

# ============================================================
# C) card.wb.ru with mobile / varied headers
# ============================================================
def test_card_wb_mobile(nm_id):
    log.info("--- C) card.wb.ru MOBILE ---")
    try:
        from curl_cffi import requests as cffi_req
        have_cffi = True
    except ImportError:
        import requests as cffi_req
        have_cffi = False
    
    variants = [
        ("mobile_ua", {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Accept": "application/json",
        }),
        ("android_ua", {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json",
        }),
        ("no_ua", {
            "Accept": "application/json",
        }),
        ("curl_ua", {
            "User-Agent": "curl/8.5.0",
            "Accept": "application/json",
        }),
    ]
    
    for label, hdrs in variants:
        if rem() < 5:
            break
        url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
        try:
            if have_cffi:
                r = cffi_req.get(url, headers=hdrs, timeout=10, impersonate="chrome")
            else:
                r = cffi_req.get(url, headers=hdrs, timeout=10)
            code = r.status_code
            clen = len(r.content or b"")
            log.info("  %s -> %d %dB", label, code, clen)
            if code == 200 and clen > 30:
                d = r.json()
                items = d.get("data", {}).get("items") or d.get("products") or []
                if items:
                    p = items[0]
                    sz = p.get("sizes", [{}])[0]
                    pr = sz.get("price", {})
                    log.info("  *** HIT %s: basic=%s total=%s qty=%s ***",
                             label, pr.get("basic"), pr.get("total"), sz.get("qty"))
                    return {"source": label, "price": pr, "qty": sz.get("qty")}
        except Exception as e:
            log.info("  %s err: %s", label, str(e)[:60])
        time.sleep(0.5)
    return None

# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== v135 START budget=%ds ===", BUDGET)
    
    nm = 199298871
    
    # A) Browser-check analysis
    bc_js = analyze_browser_check()
    if rem() > 5:
        idx_js = analyze_index_js()
    
    # B) Playwright
    if rem() > 40:
        pw_result = playwright_collect(nm)
    else:
        pw_result = None
        log.warning("  Skipping Playwright (time low)")
    
    # C) card.wb.ru mobile
    if rem() > 15:
        mobile_price = test_card_wb_mobile(nm)
    else:
        mobile_price = None
    
    # Summary
    log.info("=== v135 SUMMARY ===")
    log.info("  browser-check.js: %s", "OK" if bc_js else "FAIL")
    log.info("  Playwright: price=%s imgs=%d html=%dB",
             pw_result.get("price", "?")[:30] if pw_result and pw_result.get("price") else None,
             len(pw_result.get("images", [])) if pw_result else 0,
             pw_result.get("html_size", 0) if pw_result else 0)
    log.info("  card.wb mobile: %s", "HIT" if mobile_price else "none")
    log.info("  Total: %.1fs", time.monotonic() - START)
    log.info("=== v135 DONE ===")

if __name__ == "__main__":
    main()
