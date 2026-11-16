"""Debug: что реально лежит в DOM и network при открытии AliExpress товара."""
import sys, os, json, time, re
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright

LISTING_ID = "1005013496905897"
URL = f"https://www.aliexpress.com/item/{LISTING_ID}.html"
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "r52", "aliexpress_pw_profile")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

OUT = os.path.join(ROOT, "debug_ali_out")
os.makedirs(OUT, exist_ok=True)

print(f"[DEBUG] Opening {URL}")
print(f"[DEBUG] Profile: {PROFILE_DIR}")

all_requests = []
all_responses = []
mtop_bodies = []

def on_request(req):
    all_requests.append({"url": req.url[:300], "method": req.method})

def on_response(resp):
    u = resp.url
    all_responses.append({"url": u[:300], "status": resp.status})
    # Capture MTOP / price-related responses
    if any(k in u.lower() for k in ("mtop", "pdp", "price", "sku", "product")):
        try:
            body = resp.text()
            if len(body) > 50:
                all_responses[-1]["body_len"] = len(body)
                all_responses[-1]["body_preview"] = body[:500]
                if "pdp.pc.query" in u or "mtop" in u.lower():
                    mtop_bodies.append({"url": u[:400], "status": resp.status, "body": body})
                    print(f"[DEBUG] MTOP/PDP captured: {u[:120]} ({len(body)} bytes)")
        except Exception:
            pass

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    PROFILE_DIR,
    headless=True,
    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
    user_agent=UA,
    locale="it-IT",
    viewport={"width": 1920, "height": 1080},
    timezone_id="Europe/Rome",
    ignore_https_errors=True,
)
ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['it-IT','it','en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
    window.chrome = {runtime: {}};
""")
page = ctx.pages[0] if ctx.pages else ctx.new_page()
page.on("request", on_request)
page.on("response", on_response)

# Navigate
t0 = time.monotonic()
resp = page.goto(URL, wait_until="domcontentloaded", timeout=30000)
print(f"[DEBUG] Navigate: status={resp.status if resp else '?'} in {time.monotonic()-t0:.1f}s")

# Wait for render (up to 25s)
rendered = False
for tick in range(25):
    page.wait_for_timeout(1000)
    try:
        st = page.evaluate("""
            () => {
                const h1 = document.querySelector('h1');
                const prices = [];
                const sels = ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                    '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]','[class*="price-row"]',
                    '[class*="price"]','[class*="Price"]'];
                for (const s of sels) {
                    try {
                        document.querySelectorAll(s).forEach(el => {
                            const t = (el.textContent||'').trim();
                            if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
                        });
                    } catch(e) {}
                }
                const iframes = [];
                document.querySelectorAll('iframe').forEach(f => {
                    iframes.push((f.src||'').substring(0,300));
                });
                return {
                    h1: h1?h1.textContent.trim().substring(0,300):null,
                    prices:[...new Set(prices)].slice(0,20),
                    iframes,
                    bodyLen: document.body?document.body.innerHTML.length:0,
                    title: document.title
                };
            }
        """)
        print(f"[DEBUG] tick={tick}: h1={st.get('h1','')[:60]!r} prices={st.get('prices',[])[:5]} iframes={len(st.get('iframes',[]))} bodyLen={st.get('bodyLen')}")
        if st.get("prices"):
            rendered = True
            break
        for fr in st.get("iframes", []):
            if "punish" in fr or "tmd" in fr.lower() or "recaptcha" in fr.lower():
                print(f"[DEBUG] TMD iframe found: {fr[:100]}")
                break
    except Exception as e:
        print(f"[DEBUG] tick={tick}: eval error {e}")

# Dump JS state
print("\n[DEBUG] === JS STATE ===")
js_state = page.evaluate("""
    () => {
        const r = {};
        try {
            if (window.__INIT_DATA_STATE__) r.initState = JSON.stringify(window.__INIT_DATA_STATE__).substring(0, 20000);
        } catch(e) { r.initState = 'ERR: '+e.message; }
        try {
            if (window.runParams) r.runParams = JSON.stringify(window.runParams).substring(0, 15000);
        } catch(e) { r.runParams = 'ERR: '+e.message; }
        try {
            if (window.__NEXT_DATA__) r.nextData = JSON.stringify(window.__NEXT_DATA__).substring(0, 10000);
        } catch(e) {}
        try {
            if (window._dida) r.dida = JSON.stringify(window._dida).substring(0, 5000);
        } catch(e) {}
        return r;
    }
""")
for k, v in js_state.items():
    print(f"  {k}: {len(v) if v else 0} chars")
    if v and len(v) > 10:
        with open(os.path.join(OUT, f"js_{k}.txt"), "w", encoding="utf-8") as f:
            f.write(v)
        print(f"    saved to js_{k}.txt")
        # Print first 500 chars
        print(f"    preview: {v[:500]}")

# Dump HTML around price areas
print("\n[DEBUG] === DOM PRICE SCAN ===")
price_html = page.evaluate("""
    () => {
        const results = [];
        // All elements with 'price' in class or data attributes
        const els = document.querySelectorAll('[class*="price"],[class*="Price"],[data-pl*="price"],[data-price]');
        els.forEach((el, i) => {
            if (i > 30) return;
            results.push({
                tag: el.tagName,
                cls: (el.className||'').substring(0,200),
                text: (el.textContent||'').trim().substring(0,100),
                dataPrice: el.getAttribute('data-price')||'',
                dataPl: el.getAttribute('data-pl')||'',
                html: el.outerHTML.substring(0,300)
            });
        });
        return results;
    }
""")
for i, p in enumerate(price_html[:20]):
    print(f"  [{i}] <{p['tag']}> class={p['cls'][:60]} text={p['text'][:50]!r} dataPrice={p['dataPrice']!r}")

# Dump full HTML (for offline analysis)
full_html = page.content()
html_path = os.path.join(OUT, "page_full.html")
with open(html_path, "w", encoding="utf-8") as f:
    f.write(full_html)
print(f"\n[DEBUG] Full HTML saved: {len(full_html)} bytes → page_full.html")

# Dump all responses
resp_path = os.path.join(OUT, "responses.json")
with open(resp_path, "w", encoding="utf-8") as f:
    json.dump(all_responses, f, ensure_ascii=False, indent=2)
print(f"[DEBUG] Responses saved: {len(all_responses)} → responses.json")

# Dump MTOP bodies
if mtop_bodies:
    mtop_path = os.path.join(OUT, "mtop_bodies.json")
    with open(mtop_path, "w", encoding="utf-8") as f:
        json.dump(mtop_bodies, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] MTOP bodies saved: {len(mtop_bodies)} → mtop_bodies.json")
else:
    print(f"[DEBUG] NO MTOP bodies captured!")

# Check iframes in detail
print("\n[DEBUG] === IFRAMES ===")
for i, frame in enumerate(page.frames):
    print(f"  frame[{i}]: url={frame.url[:150]} name={frame.name[:50]}")

# Wait a bit more for late-loading data
print("\n[DEBUG] Waiting 5s more for late API calls...")
page.wait_for_timeout(5000)

# Re-check for new MTOP
new_mtops = [r for r in all_responses if "mtop" in r.get("url","").lower() and r.get("body_len",0) > 100]
print(f"[DEBUG] Total MTOP-like responses: {len(new_mtops)}")
for m in new_mtops[:5]:
    print(f"  {m['url'][:100]} status={m['status']} len={m.get('body_len')}")

# Save final state
page.wait_for_timeout(2000)
final_html = page.content()
if len(final_html) != len(full_html):
    with open(os.path.join(OUT, "page_final.html"), "w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"[DEBUG] Final HTML differs: {len(final_html)} bytes")

ctx.close()
pw.stop()

print(f"\n[DEBUG] Done. Output in: {OUT}")
print(f"[DEBUG] Files: page_full.html, responses.json, js_*.txt, mtop_bodies.json")
