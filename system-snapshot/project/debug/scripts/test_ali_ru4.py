"""AliExpress RU: быстрый диагностик v4 (120s max).
Фокус: TMD solve → reload → MTOP > 5KB → DOM.
"""
import json, os, time, traceback
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=10))
OUT = "diag_ali_ru4_result.json"
PID = "1005013496905897"

R = {"pid": PID, "ts": datetime.now(TZ).isoformat(), "phase": "start",
     "log": [], "mtop_all": [], "rendered": False, "tmd_found": False,
     "tmd_solved": False, "dom": {}, "mtop_big": None}

def log(m):
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    R["log"].append(f"[{ts}] {m}")
    print(f"[{ts}] {m}", flush=True)

def save():
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)

def ev(page, script):
    """Evaluate БЕЗ timeout kwarg (fix)."""
    try:
        return page.evaluate(script)
    except Exception as e:
        log(f"eval err: {str(e)[:100]}")
        return None

try:
    from playwright.sync_api import sync_playwright
    PROFILE = os.path.expanduser("~/.local/share/r52/aliexpress_pw_profile_ru")
    os.makedirs(PROFILE, exist_ok=True)

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        PROFILE, headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
              "--disable-dev-shm-usage"],
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="ru-RU", viewport={"width": 1920, "height": 1080},
        timezone_id="Europe/Moscow", ignore_https_errors=True,
    )
    ctx.set_default_timeout(15000)
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
    """)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    log("Browser OK")

    # Warm (быстро — профиль уже тёплый)
    page.goto("https://www.aliexpress.ru/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    log(f"Warm: {len(ctx.cookies())} cookies")

    # Product
    url = f"https://www.aliexpress.com/item/{PID}.html"
    mtop_all = []
    def on_resp(resp):
        try:
            u = resp.url
            if "mtop" in u.lower() and "pdp" in u.lower() and "punish" not in u and "report" not in u:
                body = ""
                try: body = resp.text()
                except: pass
                mtop_all.append({"url": u[:200], "status": resp.status, "len": len(body),
                                 "body": body[:50000] if len(body) > 5000 else body})
                log(f"MTOP: {len(body)}b status={resp.status}")
        except: pass
    page.on("response", on_resp)

    resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    log(f"Product HTTP {resp.status if resp else '?'}")

    # Render / TMD detection
    rendered = False
    tmd_found = False
    for tick in range(15):
        page.wait_for_timeout(1000)
        st = ev(page, """
            () => {
                const h1 = document.querySelector('h1');
                const prices = [];
                ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                 '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]',
                 '[class*="Price"]'].forEach(s => {
                    try { document.querySelectorAll(s).forEach(el => {
                        const t = (el.textContent||'').trim();
                        if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
                    }); } catch(e) {}
                });
                const ifr = [];
                document.querySelectorAll('iframe').forEach(f => ifr.push((f.src||'').substring(0,200)));
                return {h1: h1?h1.textContent.trim().substring(0,200):null,
                        prices:[...new Set(prices)].slice(0,8), ifr};
            }
        """)
        if not st: continue
        log(f"tick={tick}: h1={str(st.get('h1',''))[:40]} prices={st.get('prices',[])[:3]} ifr={len(st.get('ifr',[]))}")
        if st.get("prices") or (st.get("h1") and len(st["h1"]) > 15):
            rendered = True; log(f"RENDERED tick={tick}"); break
        for fr in st.get("ifr", []):
            if any(k in fr.lower() for k in ("punish","tmd","recaptcha")):
                tmd_found = True; break
        if tmd_found: break

    # TMD solve
    if tmd_found and not rendered:
        log("TMD solve...")
        page.wait_for_timeout(4000)
        solved = False
        for attempt in range(2):
            page.wait_for_timeout(2000)
            for f in page.frames:
                try:
                    fu = f.url or ""
                    if not any(k in fu.lower() for k in ("tmd","punish","recaptcha")): continue
                    f.wait_for_load_state("domcontentloaded", timeout=8000)
                    page.wait_for_timeout(1500)
                    for sel in ('#recaptcha-anchor','input[type="checkbox"]','[role="checkbox"]','.rc-button'):
                        try:
                            cb = f.query_selector(sel)
                            if cb:
                                box = cb.bounding_box()
                                if box and box["width"] > 0:
                                    page.mouse.click(box["x"]+box["width"]/2, box["y"]+box["height"]/2)
                                else:
                                    cb.click()
                                log(f"  Clicked: {sel}"); solved = True; break
                        except Exception as se:
                            log(f"  {sel}: {str(se)[:60]}")
                    if solved: break
                except: pass
            if solved: break

        # After TMD: RELOAD page (fresh MTOP with TMD cookie)
        if solved:
            log("RELOAD after TMD...")
            page.wait_for_timeout(3000)
            page.reload(wait_until="domcontentloaded", timeout=30000)
            # Wait for new MTOP + render
            for w in range(20):
                page.wait_for_timeout(1000)
                st = ev(page, """
                    () => {
                        const h1 = document.querySelector('h1');
                        const prices = [];
                        ['[data-pl="price"]','[class*="pdp-price"]','span[class*="price"]','[class*="Price"]'].forEach(s => {
                            try { document.querySelectorAll(s).forEach(el => {
                                const t = (el.textContent||'').trim();
                                if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
                            }); } catch(e) {}
                        });
                        return {h1: h1?h1.textContent.trim().substring(0,200):null,
                                prices:[...new Set(prices)].slice(0,8)};
                    }
                """)
                if st and (st.get("prices") or (st.get("h1") and len(st["h1"]) > 15)):
                    rendered = True
                    log(f"RENDERED after reload at {w}s: prices={st.get('prices',[])[:3]}")
                    break
                # Check for big MTOP
                big = [m for m in mtop_all if m["len"] > 5000]
                if big:
                    log(f"Big MTOP found: {big[-1]['len']}b")
                    break
            log(f"Post-reload: rendered={rendered}, mtop_all={len(mtop_all)}")

        R["tmd_solved"] = solved

    # Find biggest MTOP
    big_mtops = sorted([m for m in mtop_all if m["len"] > 500], key=lambda x: x["len"], reverse=True)
    if big_mtops:
        R["mtop_big"] = big_mtops[0]
        log(f"MTOP biggest: {big_mtops[0]['len']}b")

    # DOM extraction
    log("DOM extract...")
    dom = ev(page, """
        () => {
            const r = {};
            const h1 = document.querySelector('h1');
            r.title = h1 ? h1.textContent.trim() : null;
            if (!r.title) { const t = document.querySelector('title'); if (t) r.title = t.textContent.replace(/\\s*[|\\-].*$/,'').trim(); }
            r.prices = [];
            ['[data-pl="price"]','[class*="price--current"]','[class*="Price--current"]',
             '[class*="pdp-price"]','span[class*="price"]','[class*="priceRow"]',
             '[class*="Price"]','[class*="price"]'].forEach(s => {
                try { document.querySelectorAll(s).forEach(el => {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 80 && /\\d/.test(t)) r.prices.push(t);
                }); } catch(e) {}
            });
            r.prices = [...new Set(r.prices)].slice(0,15);
            r.images = [];
            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.getAttribute('data-src') || '';
                if (src && (src.includes('alicdn')||src.includes('aliexpress-media'))
                    && !src.includes('placeholder') && !src.includes('logo') && !src.includes('icon')
                    && !src.includes('.svg') && !src.includes('.gif')) r.images.push(src);
            });
            r.images = [...new Set(r.images)].slice(0,20);
            r.jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            r.meta = {};
            document.querySelectorAll('meta[property]').forEach(m => {
                const p = m.getAttribute('property')||'';
                const c = m.getAttribute('content')||'';
                if (p.includes('og:')) r[p]=c.substring(0,300);
            });
            r.bodyLen = document.body ? document.body.innerHTML.length : 0;
            return r;
        }
    """)
    if dom:
        R["dom"] = dom
        log(f"DOM: title={str(dom.get('title',''))[:50]} prices={dom.get('prices',[])[:4]} imgs={len(dom.get('images',[]))} jsonld={len(dom.get('jsonld',[]))}")
    else:
        log("DOM: None")

    # Screenshot
    try:
        page.screenshot(path="diag_ali_ru4_shot.png")
        log("Shot saved")
    except Exception as e:
        log(f"Shot: {e}")

    page.remove_listener("response", on_resp)
    R["mtop_all"] = [{"url": m["url"], "status": m["status"], "len": m["len"]} for m in mtop_all]
    R["rendered"] = rendered
    R["tmd_found"] = tmd_found
    R["phase"] = "done"
    ctx.close(); pw.stop()

except Exception as e:
    R["phase"] = "error"
    R["errors"] = [f"{type(e).__name__}: {e}"]
    R["log"].append(traceback.format_exc()[-500:])
    log(f"ERROR: {e}")

save()
print(f"\n=== DONE: {R['phase']} ===")
print(f"rendered={R.get('rendered')} tmd={R.get('tmd_found')} solved={R.get('tmd_solved')}")
print(f"MTOPs: {len(R.get('mtop_all',[]))}, biggest={R.get('mtop_big',{}).get('len','?')}b")
d = R.get('dom',{})
print(f"DOM: title={str(d.get('title',''))[:50]} prices={d.get('prices',[])[:4]}")
