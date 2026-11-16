"""Диагностика AliExpress pw29: быстрый автономный сбор + цены.

Улучшения vs pw28:
- 1 товар (не 2) — бюджет ~100s
- Авто-закрывание popup/cookie/region (убирает EXPLICIT_POPUP_CLICK)
- Агрессивный поиск цен: DOM, data-attrs, meta, JSON-LD, MTOP, window state
- MTOP JSONP парсинг
- Жёсткие таймауты на каждом этапе
- Результат пишется даже при частичном сбое
"""
import json
import re
import time
import os
import sys
import traceback
from datetime import datetime, timezone

PRODUCT_ID = "1005011813323763"
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile")
OUT_PATH = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw29_result.json"

BUDGET_START = time.monotonic()
BUDGET_TOTAL = 110  # секунд на всё

def elapsed():
    return time.monotonic() - BUDGET_START

def remaining():
    return max(0, BUDGET_TOTAL - elapsed())

def log(msg):
    print(f"[{elapsed():.1f}s] {msg}", flush=True)

# ─── JS: авто-закрывание popup ───
DISMISS_POPUP_JS = """
() => {
    let closed = [];
    // Cookie / consent
    const cookieSels = [
        '#onetrust-accept-btn-handler',
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("Accetta")',
        'button:has-text("OK")',
        '[class*="cookie"] button[class*="accept"]',
        '[class*="consent"] button[class*="accept"]',
        '[class*="cookie"] [class*="btn"]',
        'button[class*="cookie"]',
        '[aria-label*="cookie"]',
        '[aria-label*="Cookie"]',
    ];
    for (const sel of cookieSels) {
        try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                el.click();
                closed.push(sel);
            }
        } catch(e) {}
    }
    // Region / location popup
    const regionSels = [
        'button:has-text("Continue")',
        'button:has-text("Continue with this location")',
        'button:has-text("Continuar")',
        '[class*="location"] button[class*="confirm"]',
        '[class*="region"] button[class*="confirm"]',
        '[class*="shipping"] button[class*="confirm"]',
        '[class*="geo"] button',
    ];
    for (const sel of regionSels) {
        try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                el.click();
                closed.push(sel);
            }
        } catch(e) {}
    }
    // Generic modal close
    const closeSels = [
        '[class*="modal"] [class*="close"]',
        '[class*="dialog"] [class*="close"]',
        '[aria-label="Close"]',
        '[aria-label="close"]',
        '.next-dialog-close',
        '[class*="popup"] [class*="close"]',
    ];
    for (const sel of closeSels) {
        try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                el.click();
                closed.push(sel);
            }
        } catch(e) {}
    }
    // TMD / punish iframe detection
    let tmdFrames = [];
    document.querySelectorAll('iframe').forEach(f => {
        const src = (f.src || '').toLowerCase();
        if (src.includes('tmd') || src.includes('punish') || src.includes('recaptcha')) {
            tmdFrames.push(f.src.substring(0, 200));
        }
    });
    return { closed, tmdFrames };
}
"""

# ─── JS: извлечение данных ───
EXTRACT_JS = """
() => {
    const r = {};

    // Title
    const h1 = document.querySelector('h1');
    r.h1 = h1 ? h1.textContent.trim().substring(0, 500) : null;
    const titleEl = document.querySelector('title');
    r.titleTag = titleEl ? titleEl.textContent.trim().substring(0, 500) : null;

    // ── PRICES: aggressive multi-source ──
    r.prices = [];
    const seenPrices = new Set();

    // 1. data-price attributes
    document.querySelectorAll('[data-price],[data-activity-price],[data-original-price],[data-sku-price]').forEach(el => {
        const p = el.getAttribute('data-price') || '';
        const ap = el.getAttribute('data-activity-price') || '';
        const op = el.getAttribute('data-original-price') || '';
        const sp = el.getAttribute('data-sku-price') || '';
        const txt = (el.textContent || '').trim().substring(0, 100);
        if ((p || ap || op || sp) && !seenPrices.has(p + ap + op)) {
            seenPrices.add(p + ap + op);
            r.prices.push({src: 'data-attr', price: p, act: ap, orig: op, sku: sp, text: txt});
        }
    });

    // 2. class-based price elements
    const priceSels = [
        '[class*="price"]', '[class*="Price"]',
        '[data-pl*="price"]', '[data-pl*="Price"]',
        '[class*="sku"] [class*="price"]',
        '[class*="sku"] [class*="Price"]',
        '[class*="money"]', '[class*="Money"]',
        'span[class*="num"]', '[class*="amount"]',
    ];
    for (const sel of priceSels) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && t.length < 80 && /\\d+[.,]?\\d{0,2}/.test(t)) {
                    const key = t;
                    if (!seenPrices.has(key)) {
                        seenPrices.add(key);
                        r.prices.push({
                            src: 'dom',
                            sel: sel.substring(0, 50),
                            cls: (el.className || '').substring(0, 80),
                            text: t.substring(0, 60),
                            tag: el.tagName
                        });
                    }
                }
            });
        } catch(e) {}
    }
    r.prices = r.prices.slice(0, 30);

    // 3. Currency detection
    r.currencies = [];
    const curSels = '[class*="currency"],[class*="Currency"],[class*="symbol"]';
    document.querySelectorAll(curSels).forEach(el => {
        const t = (el.textContent || '').trim();
        if (t && t.length < 10) r.currencies.push(t);
    });
    // Also from price text patterns
    const curRe = /(€|\\$|£|¥|₴|₽|INR|EUR|USD|GBP|CNY)/g;
    document.body.textContent.substring(0, 50000).match(curRe);
    r.currencies = [...new Set(r.currencies)].slice(0, 10);

    // 4. Meta tags (og:, product:)
    r.metas = {};
    document.querySelectorAll('meta[property],meta[name]').forEach(m => {
        const p = m.getAttribute('property') || m.getAttribute('name') || '';
        const c = (m.getAttribute('content') || '').trim();
        if (p && c) r.metas[p] = c.substring(0, 300);
    });

    // 5. JSON-LD
    r.jsonld = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try {
            const d = JSON.parse(s.textContent);
            r.jsonld.push(d);
        } catch(e) {
            r.jsonld.push({parse_error: e.message, raw: s.textContent.substring(0, 500)});
        }
    });

    // 6. Window state
    r.windowState = {};
    for (const k of ['__INIT_DATA_STATE__','runParams','_page_config_','__INIT_DATA_CALLBACK__',
                     '__NEXT_DATA__','__NUXT__','pageData','productData']) {
        try {
            const v = window[k];
            if (v !== undefined && v !== null) {
                const sv = typeof v === 'string' ? v : JSON.stringify(v);
                r.windowState[k] = sv.substring(0, 15000);
            }
        } catch(e) {}
    }

    // 7. Images (product only)
    r.images = [];
    document.querySelectorAll('img').forEach(img => {
        const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
        if (src && (src.includes('alicdn') || src.includes('aliexpress-media'))
            && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
            && !src.includes('blank') && !src.includes('.svg') && !src.includes('.gif')
            && !src.includes('30x30') && !src.includes('48x48') && !src.includes('64x64')
            && !src.includes('110x42') && !src.includes('42x60')) {
            r.images.push(src.substring(0, 250));
        }
    });
    r.images = [...new Set(r.images)].slice(0, 25);

    // 8. Availability / stock
    r.availability = [];
    const aSels = ['[class*="stock"]','[class*="availability"]','[data-pl="stock"]',
                   '[class*="buy-now"]','[class*="addToCart"]','button[class*="buy"]',
                   '[class*="purchase"]','[class*="sold-out"]','[class*="out-of-stock"]'];
    for (const s of aSels) {
        try {
            document.querySelectorAll(s).forEach(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t && t.length < 150) r.availability.push(t);
            });
        } catch(e) {}
    }
    r.availability = [...new Set(r.availability)].slice(0, 10);

    // 9. Breadcrumb / category
    const bc = document.querySelector('[class*="breadcrumb"],[class*="Breadcrumb"],nav[aria-label]');
    r.breadcrumb = bc ? bc.textContent.trim().substring(0, 300) : null;

    // 10. SKU variants
    r.skuVariants = [];
    document.querySelectorAll('[class*="sku"],[class*="Sku"]').forEach(el => {
        const t = (el.textContent || '').trim();
        if (t && t.length < 200 && t.length > 2) r.skuVariants.push(t.substring(0, 150));
    });
    r.skuVariants = [...new Set(r.skuVariants)].slice(0, 15);

    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
    return r;
}
"""

def solve_tmd_quick(page, budget_s=25):
    """Быстрый обход TMD. Бюджет в секундах."""
    log("  [TMD] Quick solve...")
    page.wait_for_timeout(2000)

    tmd_frame = None
    for f in page.frames:
        fu = (f.url or "").lower()
        if "tmd" in fu or "punish" in fu or "recaptcha" in fu:
            tmd_frame = f
            log(f"  [TMD] Frame: {fu[:100]}")
            break

    if not tmd_frame:
        log("  [TMD] No frame found")
        return False

    # Save TMD HTML
    try:
        tmd_html = tmd_frame.content()
        tmd_path = OUT_PATH.replace("_result.json", "_tmd.html")
        with open(tmd_path, "w", encoding="utf-8") as fh:
            fh.write(tmd_html)
        log(f"  [TMD] Saved HTML: {len(tmd_html)}b")
    except Exception as e:
        log(f"  [TMD] Save error: {e}")

    # Click strategies (1 pass, fast)
    for sel in ('input[type="checkbox"]', '#recaptcha-anchor', '[role="checkbox"]',
                'span[role="checkbox"]', '.rc-button', '#sliders', '.nc_iconfont',
                'button', 'a', '[class*="btn"]', '[class*="verify"]'):
        try:
            cb = tmd_frame.query_selector(sel)
            if cb:
                box = cb.bounding_box()
                if box and box["width"] > 0:
                    page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                else:
                    cb.click()
                log(f"  [TMD] Clicked '{sel}'")
                break
        except:
            continue

    # Wait for resolution (up to budget)
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)
        try:
            check = page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    const pEls = document.querySelectorAll('[class*="price"],[class*="Price"]');
                    const prices = [];
                    pEls.forEach(el => {
                        const t = (el.textContent||'').trim();
                        if (t && /\\d/.test(t) && t.length < 50) prices.push(t);
                    });
                    let tmdGone = true;
                    document.querySelectorAll('iframe').forEach(f => {
                        const s = (f.src||'').toLowerCase();
                        if (s.includes('tmd') || s.includes('punish')) tmdGone = false;
                    });
                    return {h1: h1?h1.textContent.trim().substring(0,200):null,
                            prices:[...new Set(prices)].slice(0,5),
                            tmdGone, bodyLen: document.body?document.body.innerHTML.length:0};
                }
            """)
            if check.get("tmdGone") and (check.get("prices") or (check.get("h1") and len(check.get("h1","")) > 15)):
                log(f"  [TMD] Solved! h1={str(check.get('h1',''))[:50]}")
                return True
            if check.get("prices"):
                log(f"  [TMD] Prices appeared: {check['prices'][:3]}")
                return True
        except:
            continue

    log("  [TMD] Not solved within budget")
    return False

def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    url = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
    result = {
        "product_id": PRODUCT_ID,
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw29",
    }

    xhr_bodies = []

    def on_response(resp):
        try:
            u = resp.url
            ct = resp.headers.get("content-type", "")
            interesting = (
                ("json" in ct and resp.status == 200) or
                ("mtop" in u.lower() and resp.status == 200) or
                ("pdp" in u.lower() and resp.status == 200)
            )
            if interesting:
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
                        "ct": ct[:100],
                        "body": body[:25000],
                    })
                    tag = "MTOP" if "mtop" in u.lower() else "JSON"
                    log(f"  [{tag}] {u[:70]}... ({len(body)}b)")
        except:
            pass

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-automation",
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

        # ─── WARMUP (быстрый, 8s max) ───
        log("[WARM] Homepage...")
        try:
            page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            # Закрыть popup
            try:
                page.evaluate(DISMISS_POPUP_JS)
            except:
                pass
            page.evaluate("window.scrollTo(0, 200)")
            page.wait_for_timeout(2000)
            cookies = ctx.cookies()
            log(f"[WARM] Done. {len(cookies)} cookies. {elapsed():.1f}s")
        except Exception as e:
            log(f"[WARM] Error: {e}")

        # ─── PRODUCT PAGE ───
        page.on("response", on_response)
        log(f"[LOAD] {url}")
        http_status = 0
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
            http_status = resp.status if resp else 0
            log(f"[LOAD] HTTP {http_status}, final={page.url[:100]}")
        except Exception as e:
            log(f"[LOAD] Error: {e}")
            result["load_error"] = str(e)

        # ─── WAIT FOR RENDER (max 12s) ───
        log("[RENDER] Waiting...")
        rendered = False
        last_check = {}
        for tick in range(8):  # 8 × 1.5s = 12s
            page.wait_for_timeout(1500)
            try:
                last_check = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const pEls = document.querySelectorAll('[class*="price"],[data-pl*="price"],[class*="Price"]');
                        const prices = [];
                        pEls.forEach(el => {
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
                if last_check.get("prices") or (last_check.get("h1") and len(last_check["h1"]) > 15):
                    rendered = True
                    log(f"[RENDER] OK at tick={tick}. h1={str(last_check.get('h1',''))[:60]}")
                    break
                if tick % 3 == 0:
                    log(f"[RENDER] tick={tick}: bodyLen={last_check.get('bodyLen',0)}")
            except:
                continue

        # ─── DISMISS POPUPS ───
        log("[POPUP] Dismissing...")
        popup_result = {}
        try:
            popup_result = page.evaluate(DISMISS_POPUP_JS)
            log(f"[POPUP] Closed: {popup_result.get('closed', [])}, TMD frames: {popup_result.get('tmdFrames', [])}")
        except Exception as e:
            log(f"[POPUP] Error: {e}")

        # ─── TMD IF NEEDED ───
        if not rendered and popup_result.get("tmdFrames"):
            tmd_ok = solve_tmd_quick(page, budget_s=min(20, int(remaining())))
            if tmd_ok:
                rendered = True
                # После TMD — дождись рендера
                page.wait_for_timeout(2000)

        # ─── RELOAD IF NOT RENDERED ───
        if not rendered and remaining() > 20:
            log("[RELOAD] 2nd pass...")
            try:
                resp2 = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                http_status = resp2.status if resp2 else http_status
                log(f"[RELOAD] HTTP {http_status}")
                page.wait_for_timeout(3000)
                # Закрыть popup снова
                try:
                    page.evaluate(DISMISS_POPUP_JS)
                except:
                    pass
                for tick in range(6):
                    page.wait_for_timeout(1500)
                    try:
                        last_check = page.evaluate("""
                            () => {
                                const h1 = document.querySelector('h1');
                                const pEls = document.querySelectorAll('[class*="price"],[class*="Price"]');
                                const prices = [];
                                pEls.forEach(el => {
                                    const t = (el.textContent||'').trim();
                                    if (t && /\\d/.test(t) && t.length < 60) prices.push(t);
                                });
                                return {h1: h1?h1.textContent.trim().substring(0,200):null,
                                        prices:[...new Set(prices)].slice(0,5),
                                        bodyLen: document.body?document.body.innerHTML.length:0};
                            }
                        """)
                        if last_check.get("prices") or (last_check.get("h1") and len(last_check["h1"]) > 15):
                            rendered = True
                            log(f"[RELOAD] Rendered! h1={str(last_check.get('h1',''))[:60]}")
                            break
                    except:
                        continue
            except Exception as e:
                log(f"[RELOAD] Error: {e}")

        # ─── EXTRACT ───
        log("[EXTRACT] DOM + state...")
        dom = {}
        try:
            dom = page.evaluate(EXTRACT_JS)
        except Exception as e:
            log(f"[EXTRACT] Error: {e}")
            dom = {"error": str(e)}

        page.remove_listener("response", on_response)

        # ─── PARSE MTOP FROM XHR ───
        log("[MTOP] Parsing captured XHR...")
        mtop_data = None
        mtop_raw = None
        for x in xhr_bodies:
            if ("pdp.pc.query" in x["url"] or "mtop" in x["url"].lower()) and x["len"] > 200:
                try:
                    clean = x["body"].strip()
                    # JSONP: mtopjsonp1({...});
                    m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', clean, re.DOTALL)
                    if m:
                        clean = m.group(1)
                    d = json.loads(clean)
                    ret = d.get("ret", [])
                    mtop_raw = d
                    if any("SUCCESS" in str(r) for r in ret):
                        mtop_data = d
                        log(f"[MTOP] SUCCESS! keys={list(d.get('data',{}).keys())[:8]}")
                        break
                    else:
                        log(f"[MTOP] ret={ret}")
                except (json.JSONDecodeError, ValueError) as e:
                    log(f"[MTOP] Parse error: {e}")
                    continue

        # ─── BUILD RESULT ───
        result.update({
            "final_url": page.url,
            "http_status": http_status,
            "elapsed": round(elapsed(), 1),
            "rendered": rendered,
            "popup_dismissed": popup_result,
            "xhr_count": len(xhr_bodies),
            "xhr_bodies": xhr_bodies[:10],
            "dom": dom,
            "mtop_success": mtop_data is not None,
            "mtop_data": mtop_data,
            "mtop_raw": mtop_raw,
            "last_check": last_check,
        })

        # ─── SUMMARY ───
        log(f"\n{'='*50}")
        log(f"SUMMARY for {PRODUCT_ID}")
        log(f"{'='*50}")
        log(f"  h1: {str(dom.get('h1','N/A'))[:100]}")
        log(f"  titleTag: {str(dom.get('titleTag','N/A'))[:100]}")
        prices = dom.get('prices', [])
        log(f"  prices ({len(prices)}):")
        for p in prices[:8]:
            log(f"    [{p.get('src','?')}] {p.get('text', p.get('price',''))[:50]}")
        log(f"  currencies: {dom.get('currencies', [])[:5]}")
        log(f"  jsonld: {len(dom.get('jsonld', []))} blocks")
        log(f"  images: {len(dom.get('images', []))}")
        log(f"  availability: {dom.get('availability', [])[:3]}")
        log(f"  breadcrumb: {str(dom.get('breadcrumb','N/A'))[:80]}")
        log(f"  windowState keys: {list(dom.get('windowState', {}).keys())}")
        log(f"  mtop_success: {mtop_data is not None}")
        log(f"  bodyLen: {dom.get('bodyLen', 0)}")
        log(f"  Total: {elapsed():.1f}s")

        ctx.close()

    # ─── SAVE ───
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"\nSaved: {OUT_PATH} ({os.path.getsize(OUT_PATH)} bytes)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        traceback.print_exc()
        # Сохраним что есть
        partial = {
            "product_id": PRODUCT_ID,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "elapsed": round(elapsed(), 1),
        }
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(partial, f, ensure_ascii=False, indent=2)
        log(f"Partial saved: {OUT_PATH}")
    sys.exit(0)
