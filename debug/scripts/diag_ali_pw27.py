"""Диагностика AliExpress pw27: Playwright полный перехват.

Цель: получить ВСЕ товарные данные через Playwright:
- Перехват всех XHR/fetch (не только MTOP pdp.pc.query)
- Ожидание полного рендера (до 35s)
- Извлечение __INIT_DATA_CALLBACK__, _page_config_, runParams
- DOM: все price-элементы, data-атрибуты, aria-label
- 2 товара: доступный + region-locked
"""
import json
import re
import time
import sys
import os
from datetime import datetime, timezone

PRODUCTS = [
    "1005011813323763",  # BT headphones (region-locked IT)
    "1005007379458743",  # другой товар для сравнения
]

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile")

EXTRACT_JS = """
() => {
    const r = {};
    
    // 1. __INIT_DATA_CALLBACK__
    if (window.__INIT_DATA_CALLBACK__) {
        try { r.initDataCallback = JSON.stringify(window.__INIT_DATA_CALLBACK__).substring(0, 15000); } catch(e) { r.initDataCallback = String(window.__INIT_DATA_CALLBACK__).substring(0, 2000); }
    }
    
    // 2. _page_config_
    if (window._page_config_) {
        try { r.pageConfig = JSON.stringify(window._page_config_).substring(0, 8000); } catch(e) {}
    }
    
    // 3. runParams
    if (window.runParams && Object.keys(window.runParams).length > 0) {
        try { r.runParams = JSON.stringify(window.runParams).substring(0, 8000); } catch(e) {}
    }
    
    // 4. h1 / title
    const h1 = document.querySelector('h1');
    r.h1 = h1 ? h1.textContent.trim().substring(0, 300) : null;
    const titleEl = document.querySelector('title');
    r.titleTag = titleEl ? titleEl.textContent.trim().substring(0, 300) : null;
    
    // 5. ALL price-related elements (aggressive)
    r.priceElements = [];
    const allEls = document.querySelectorAll('[class*="price"],[data-pl*="price"],[class*="Price"],[class*="Price"]');
    allEls.forEach(el => {
        const t = (el.textContent || '').trim();
        if (t && t.length < 100 && /\\d/.test(t)) {
            r.priceElements.push({
                tag: el.tagName,
                cls: (el.className || '').substring(0, 100),
                text: t.substring(0, 80),
                dataPrice: el.getAttribute('data-price') || '',
                dataActPrice: el.getAttribute('data-activity-price') || '',
                dataOrigPrice: el.getAttribute('data-original-price') || '',
                ariaLabel: el.getAttribute('aria-label') || ''
            });
        }
    });
    r.priceElements = r.priceElements.slice(0, 25);
    
    // 6. data-price / data-activity-price / data-original-price attrs
    r.dataPriceAttrs = [];
    document.querySelectorAll('[data-price],[data-activity-price],[data-original-price]').forEach(el => {
        r.dataPriceAttrs.push({
            tag: el.tagName,
            cls: (el.className || '').substring(0, 80),
            price: el.getAttribute('data-price') || '',
            actPrice: el.getAttribute('data-activity-price') || '',
            origPrice: el.getAttribute('data-original-price') || ''
        });
    });
    r.dataPriceAttrs = r.dataPriceAttrs.slice(0, 15);
    
    // 7. aria-label with price
    r.ariaPrices = [];
    document.querySelectorAll('[aria-label]').forEach(el => {
        const al = el.getAttribute('aria-label') || '';
        if (/\\d/.test(al) && (al.includes('€') || al.includes('$') || al.includes('price') || al.includes('Price'))) {
            r.ariaPrices.push(al.substring(0, 100));
        }
    });
    r.ariaPrices = r.ariaPrices.slice(0, 10);
    
    // 8. Images (alicdn / aliexpress-media)
    r.images = [];
    document.querySelectorAll('img').forEach(img => {
        const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
        if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
            && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
            && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif')
            && !src.includes('30x30') && !src.includes('48x48') && !src.includes('64x64')
            && !src.includes('110x42') && !src.includes('42x60')) {
            r.images.push(src.substring(0, 200));
        }
    });
    r.images = [...new Set(r.images)].slice(0, 20);
    
    // 9. Availability / stock
    r.availabilityTexts = [];
    const aSels = ['[class*="stock"]','[class*="availability"]','[data-pl="stock"]','[class*="pdp-stock"]','[class*="buy-now"]','[class*="addToCart"]','[class*="add-to-cart"]','button[class*="buy"]'];
    for (const s of aSels) {
        document.querySelectorAll(s).forEach(el => {
            const t = (el.textContent || '').trim().toLowerCase();
            if (t && t.length < 150) r.availabilityTexts.push(t);
        });
    }
    r.availabilityTexts = [...new Set(r.availabilityTexts)].slice(0, 10);
    
    // 10. Breadcrumb / category
    const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"],nav[aria-label]');
    r.breadcrumb = bc ? bc.textContent.trim().substring(0, 300) : null;
    
    // 11. All meta tags
    r.metas = {};
    document.querySelectorAll('meta[property],meta[name]').forEach(m => {
        const p = m.getAttribute('property') || m.getAttribute('name') || '';
        const c = m.getAttribute('content') || '';
        if (p && c) r.metas[p] = c.substring(0, 300);
    });
    
    // 12. JSON-LD
    r.jsonld = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
    });
    
    // 13. Body length (sanity)
    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
    
    // 14. Look for __NEXT_DATA__ or similar hydration state
    if (window.__NEXT_DATA__) {
        try { r.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0, 5000); } catch(e) {}
    }
    
    // 15. Any window var with "price" in key
    r.windowPriceVars = {};
    for (const k of Object.keys(window)) {
        if (k.toLowerCase().includes('price') || k.toLowerCase().includes('sku') || k.toLowerCase().includes('product')) {
            try {
                const v = window[k];
                if (typeof v === 'object' && v !== null) {
                    r.windowPriceVars[k] = JSON.stringify(v).substring(0, 2000);
                } else {
                    r.windowPriceVars[k] = String(v).substring(0, 200);
                }
            } catch(e) {}
        }
    }
    
    return r;
}
"""

def main():
    from playwright.sync_api import sync_playwright
    
    os.makedirs(PROFILE_DIR, exist_ok=True)
    results = []
    
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
            timezone_id="Europe/London",
            ignore_https_errors=True,
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            window.chrome = {runtime: {}};
        """)
        
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        
        # Warm up: homepage
        print("[WARM] Loading homepage...")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            # Accept cookies
            for sel in ('button:has-text("Accept")', 'button:has-text("Accept all")',
                        '#onetrust-accept-btn-handler', '[class*="cookie"] button'):
                try:
                    btn = page.query_selector(sel)
                    if btn:
                        btn.click()
                        page.wait_for_timeout(2000)
                        break
                except:
                    pass
            page.evaluate("window.scrollTo(0, 300)")
            page.wait_for_timeout(3000)
            cookies = ctx.cookies()
            print(f"[WARM] Done. {len(cookies)} cookies.")
        except Exception as e:
            print(f"[WARM] Error: {e}")
        
        for pid in PRODUCTS:
            print(f"\n{'='*60}")
            print(f"PRODUCT: {pid}")
            print(f"{'='*60}")
            
            # Intercept ALL XHR/fetch
            xhr_bodies = []
            def on_response(resp):
                try:
                    u = resp.url
                    ct = resp.headers.get("content-type", "")
                    if ("json" in ct or "mtop" in u.lower() or "api" in u.lower() or "pdp" in u.lower()) and resp.status == 200:
                        body = ""
                        try:
                            body = resp.text()
                        except:
                            pass
                        if len(body) > 50:
                            xhr_bodies.append({
                                "url": u[:300],
                                "status": resp.status,
                                "len": len(body),
                                "body": body[:15000],
                            })
                            print(f"  [XHR] {u[:80]}... ({len(body)} bytes)")
                except:
                    pass
            
            page.on("response", on_response)
            
            url = f"https://www.aliexpress.com/item/{pid}.html"
            t0 = time.monotonic()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                status = resp.status if resp else 0
                print(f"  Navigate: HTTP {status}, final={page.url[:100]}")
            except Exception as e:
                print(f"  Navigate error: {e}")
                page.remove_listener("response", on_response)
                results.append({"product_id": pid, "error": str(e)})
                continue
            
            # Wait for render: check every 2s for up to 35s
            rendered = False
            for tick in range(18):
                page.wait_for_timeout(2000)
                try:
                    check = page.evaluate("""
                        () => {
                            const h1 = document.querySelector('h1');
                            const priceEls = document.querySelectorAll('[class*="price"],[data-pl*="price"]');
                            const prices = [];
                            priceEls.forEach(el => {
                                const t = (el.textContent||'').trim();
                                if (t && /\\d/.test(t) && t.length < 60) prices.push(t);
                            });
                            return {
                                h1: h1 ? h1.textContent.trim().substring(0, 200) : null,
                                prices: [...new Set(prices)].slice(0, 5),
                                bodyLen: document.body ? document.body.innerHTML.length : 0
                            };
                        }
                    """)
                    if check.get("prices") or (check.get("h1") and len(check["h1"]) > 15):
                        rendered = True
                        print(f"  Rendered at tick={tick*2}s: h1={check.get('h1','')[:60]}, prices={check.get('prices',[])}")
                        break
                    if tick % 3 == 0:
                        print(f"  tick={tick*2}s: bodyLen={check.get('bodyLen',0)}, h1={check.get('h1','N/A')[:40]}")
                except:
                    continue
            
            # If not rendered, try TMD/captcha
            if not rendered:
                print("  Not rendered, checking for TMD/captcha...")
                try:
                    for f in page.frames:
                        fu = (f.url or "").lower()
                        if "recaptcha" in fu or "punish" in fu or "tmd" in fu:
                            print(f"  Found captcha frame: {fu[:100]}")
                            f.wait_for_load_state("domcontentloaded", timeout=8000)
                            page.wait_for_timeout(2000)
                            for sel in ('input[type="checkbox"]', '#recaptcha-anchor', '[role="checkbox"]'):
                                cb = f.query_selector(sel)
                                if cb:
                                    box = cb.bounding_box()
                                    if box and box["width"] > 0:
                                        page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                                    else:
                                        cb.click()
                                    print(f"  Clicked: {sel}")
                                    break
                            break
                    page.wait_for_timeout(10000)
                    # Re-check
                    check = page.evaluate("""
                        () => {
                            const h1 = document.querySelector('h1');
                            return {h1: h1?h1.textContent.trim().substring(0,200):null, bodyLen: document.body?document.body.innerHTML.length:0};
                        }
                    """)
                    if check.get("h1") and len(check["h1"]) > 15:
                        rendered = True
                        print(f"  After TMD: rendered, h1={check['h1'][:60]}")
                except Exception as e:
                    print(f"  TMD check error: {e}")
            
            # Extract full DOM state
            elapsed = time.monotonic() - t0
            print(f"  Extracting DOM state (elapsed={elapsed:.1f}s)...")
            try:
                dom = page.evaluate(EXTRACT_JS)
            except Exception as e:
                print(f"  DOM extract error: {e}")
                dom = {"error": str(e)}
            
            page.remove_listener("response", on_response)
            
            # Compile result
            r = {
                "product_id": pid,
                "url": url,
                "final_url": page.url,
                "http_status": status,
                "elapsed": round(elapsed, 1),
                "rendered": rendered,
                "xhr_count": len(xhr_bodies),
                "xhr_bodies": xhr_bodies[:10],
                "dom": dom,
            }
            
            # Summary
            print(f"\n  --- SUMMARY ---")
            print(f"  h1: {dom.get('h1', 'N/A')[:80]}")
            print(f"  titleTag: {dom.get('titleTag', 'N/A')[:80]}")
            print(f"  priceElements: {len(dom.get('priceElements', []))}")
            for pe in dom.get('priceElements', [])[:5]:
                print(f"    [{pe.get('cls','')[:40]}] {pe.get('text','')[:50]}")
            print(f"  dataPriceAttrs: {len(dom.get('dataPriceAttrs', []))}")
            for dp in dom.get('dataPriceAttrs', [])[:5]:
                print(f"    price={dp.get('price','')} act={dp.get('actPrice','')} orig={dp.get('origPrice','')}")
            print(f"  ariaPrices: {dom.get('ariaPrices', [])[:3]}")
            print(f"  images: {len(dom.get('images', []))}")
            print(f"  availability: {dom.get('availabilityTexts', [])[:3]}")
            print(f"  breadcrumb: {dom.get('breadcrumb', 'N/A')[:80]}")
            print(f"  bodyLen: {dom.get('bodyLen', 0)}")
            print(f"  initDataCallback: {'YES' if dom.get('initDataCallback') else 'NO'}")
            print(f"  pageConfig: {'YES' if dom.get('pageConfig') else 'NO'}")
            print(f"  runParams: {'YES' if dom.get('runParams') else 'NO'}")
            print(f"  windowPriceVars: {list(dom.get('windowPriceVars', {}).keys())[:5]}")
            print(f"  XHR captured: {len(xhr_bodies)}")
            for x in xhr_bodies[:3]:
                print(f"    {x['url'][:70]} ({x['len']}b)")
            
            results.append(r)
            time.sleep(3)
        
        ctx.close()
    
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw27",
        "results": results,
    }
    out_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw27_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n\nSaved: {out_path} ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    main()
