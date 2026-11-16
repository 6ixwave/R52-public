"""Диагностика AliExpress.ru: click-captcha + extraction (v6)."""
import json, logging, os, sys, time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag_ali_ru6")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from r52.collectors.aliexpress import AliExpressCollector

PID = "1005013496905897"
OUT_JSON = os.path.join(BASE, "diag_ali_ru6_result.json")
OUT_HTML = os.path.join(BASE, "diag_ali_ru6_page.html")

def main():
    result = {
        "pid": PID,
        "url": f"https://ru.aliexpress.com/item/{PID}.html",
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": "start",
        "log": [],
    }

    def logline(msg):
        result["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        log.info(msg)

    logline("Starting AliExpress.ru collector (domain=ru)...")

    col = AliExpressCollector(domain="ru")
    try:
        # 1. Browser
        ok = col._ensure_browser()
        if not ok:
            result["phase"] = "browser_failed"
            logline("Browser start FAILED")
            return
        logline("Browser OK (ru locale)")

        # 2. Warm
        col._warm_session()
        cookies = col._ctx.cookies()
        logline(f"Warm: {len(cookies)} cookies")

        # 3. Navigate
        url = col.build_url(PID)
        logline(f"Navigating: {url}")

        # Use _fetch_pw directly for full control
        col._mtop_bodies = []

        def on_resp(resp):
            try:
                u = resp.url
                if "mtop" in u.lower() and "pdp.pc.query" in u and "punish" not in u:
                    body = resp.text()
                    if len(body) > 100:
                        col._mtop_bodies.append({"url": u[:400], "status": resp.status, "body": body})
                        logline(f"MTOP captured: {len(body)}b")
            except Exception:
                pass

        col._page.on("response", on_resp)
        resp = col._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        logline(f"HTTP {resp.status if resp else 0}")

        # 4. Tick loop (20s)
        rendered = False
        tmd_found = False
        captcha_type = "none"
        for tick in range(20):
            col._page.wait_for_timeout(1000)
            try:
                st = col._page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const prices = [];
                        const sels = ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                            '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]','[class*="price-row"]'];
                        for (const s of sels) {
                            try {
                                document.querySelectorAll(s).forEach(el => {
                                    const t = (el.textContent||'').trim();
                                    if (t && t.length < 60 && /\\d/.test(t)) prices.push(t);
                                });
                            } catch(e) {}
                        }
                        const punish = document.getElementById('baxia-punish');
                        let capType = 'none';
                        if (punish) {
                            const cls = punish.className || '';
                            if (cls.includes('captchaclick')) capType = 'captchaclick';
                            else if (cls.includes('drag')) capType = 'drag';
                            else if (cls.includes('scratch')) capType = 'scratch';
                        }
                        return {h1: h1?h1.textContent.trim().substring(0,300):null,
                                prices:[...new Set(prices)].slice(0,8),
                                capType, bodyLen: document.body?document.body.innerHTML.length:0};
                    }
                """)
                logline(f"tick={tick}: h1={st.get('h1','')[:40] if st.get('h1') else 'N/A'} prices={st.get('prices',[])} cap={st.get('capType')} body={st.get('bodyLen')}")

                if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
                    rendered = True
                    break
                if st.get("capType") != "none":
                    tmd_found = True
                    captcha_type = st["capType"]
                    break
            except Exception:
                continue

        # 5. Solve captcha
        if tmd_found and not rendered:
            logline(f"Solving captcha: {captcha_type}")
            rendered = col._solve_tmd()
            logline(f"Captcha solved: {rendered}")

        # 6. Extra wait after captcha
        if rendered:
            col._page.wait_for_timeout(3000)

        # 7. DOM extraction
        logline("Extracting DOM...")
        dom_data = {}
        try:
            dom_data = col._page.evaluate("""
                () => {
                    const r = {};
                    const h1 = document.querySelector('h1');
                    r.title = h1 ? h1.textContent.trim() : null;
                    if (!r.title) {
                        const t = document.querySelector('title');
                        if (t) r.title = t.textContent.replace(/\\s*[|\\-\u2013\u2014].*$/,'').trim();
                    }
                    r.prices = [];
                    const pSels = ['[data-pl="price"]','[class*="price--current"]','[class*="Price--current"]',
                        '[class*="pdp-price"]','[class*="price-current"]','span[class*="price"]',
                        '[class*="sku-price"]','[class*="priceRow"]','[class*="price-row"]','[data-pl*="price"]'];
                    for (const s of pSels) {
                        try {
                            document.querySelectorAll(s).forEach(el => {
                                const t = (el.textContent||'').trim();
                                if (t && t.length < 60 && /\\d/.test(t)) r.prices.push(t);
                            });
                        } catch(e) {}
                    }
                    r.prices = [...new Set(r.prices)].slice(0,15);
                    r.images = [];
                    document.querySelectorAll('img').forEach(img => {
                        const src = img.src || img.getAttribute('data-src') || '';
                        if (src && (src.includes('alicdn')||src.includes('aliexpress-media'))
                            && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                            && !src.includes('.svg') && !src.includes('.gif')) {
                            r.images.push(src);
                        }
                    });
                    r.images = [...new Set(r.images)].slice(0,20);
                    r.meta = {};
                    document.querySelectorAll('meta[property]').forEach(m => {
                        const p = m.getAttribute('property')||'';
                        const c = m.getAttribute('content')||'';
                        if (p.includes('og:')) r.meta[p]=c.substring(0,300);
                    });
                    r.jsonld = [];
                    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                        try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                    });
                    r.bodyLen = document.body ? document.body.innerHTML.length : 0;
                    return r;
                }
            """)
            logline(f"DOM: title={dom_data.get('title','')[:50] if dom_data.get('title') else 'N/A'} prices={dom_data.get('prices',[])} imgs={len(dom_data.get('images',[]))} jsonld={len(dom_data.get('jsonld',[]))}")
        except Exception as e:
            logline(f"DOM extraction error: {e}")

        # 8. Save HTML
        try:
            html = col._page.content()
            with open(OUT_HTML, "w", encoding="utf-8") as f:
                f.write(html)
            logline(f"HTML saved: {len(html)} bytes")
        except Exception as e:
            logline(f"HTML save error: {e}")

        # 9. Screenshot
        try:
            shot = os.path.join(BASE, "diag_ali_ru6_shot.png")
            col._page.screenshot(path=shot, full_page=False)
            logline(f"Screenshot: {shot}")
        except Exception:
            pass

        col._page.remove_listener("response", on_resp)

        result["phase"] = "done"
        result["rendered"] = rendered
        result["captcha_type"] = captcha_type
        result["mtop_all"] = col._mtop_bodies
        result["dom"] = dom_data
        result["stats"] = col.stats

    except Exception as e:
        result["phase"] = f"error: {e}"
        logline(f"ERROR: {e}")
    finally:
        col.close()

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logline(f"Result saved: {OUT_JSON}")
    logline(f"PHASE={result['phase']} rendered={result.get('rendered')} cap={result.get('captcha_type')}")

if __name__ == "__main__":
    main()
