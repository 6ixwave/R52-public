"""PW21: Ловим ВСЕ сетевые ответы на aliexpress.us, ищем источник данных товара."""
import json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

PROFILE = ROOT / "pw_ali_profile"
PRODUCT_ID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

print(f"Profile: {PROFILE}, exists={PROFILE.exists()}")
print(f"Target: {URL}")

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    str(PROFILE),
    headless=True,
    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    locale="en-US",
    viewport={"width": 1920, "height": 1080},
    timezone_id="America/New_York",
    ignore_https_errors=True,
)
ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = {runtime: {}};
""")

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# ── Ловим ВСЕ ответы ──
all_responses = []
def on_resp(resp):
    try:
        status = resp.status
        url = resp.url
        ct = resp.headers.get("content-type", "")
        if status != 200:
            return
        # Ловим: JSON, JS с данными, и любые >2KB
        is_json = "json" in ct.lower()
        is_js = "javascript" in ct.lower()
        if not (is_json or is_js):
            return
        body = resp.text()
        if len(body) < 500:
            return
        all_responses.append({
            "url": url,
            "status": status,
            "ct": ct[:50],
            "len": len(body),
            "body": body,
        })
    except Exception:
        pass

page.on("response", on_resp)

# ── Навигация ──
t0 = time.monotonic()
try:
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"NAV ERR: {e}")

# Ждём 30 сек
for tick in range(10):
    page.wait_for_timeout(3000)
    t = tick * 3
    if t in (6, 12, 18, 24, 27):
        try:
            st = page.evaluate("""
                () => ({
                    h1: document.querySelector('h1')?.textContent?.trim()?.substring(0,100) || null,
                    bodyLen: document.body?.innerHTML?.length || 0,
                    url: window.location.href,
                })
            """)
            print(f"  t={t}s: body={st['bodyLen']} h1={st['h1']!r} url={st['url'][:80]}")
        except Exception:
            pass

elapsed = time.monotonic() - t0
print(f"\nTotal: {elapsed:.1f}s, responses captured: {len(all_responses)}")

# ── Фильтруем интересные ──
# Ключевые слова, которые могут быть в данных товара
KEYWORDS = ["title", "price", "sku", "product", "item", "salePrice", "originalPrice",
            "HEADER_IMAGE", "SKUModule", "GLOBAL_DATA", "seoInfo", "categoryPath",
            "description", "shipping", "stock", "availability"]

interesting = []
for r in all_responses:
    body_lower = r["body"].lower()
    hits = [kw for kw in KEYWORDS if kw.lower() in body_lower]
    if len(hits) >= 2:
        r["hits"] = hits
        interesting.append(r)

# Сортируем по размеру (большие = скорее всего данные)
interesting.sort(key=lambda x: x["len"], reverse=True)

print(f"\n{'='*60}")
print(f"ИНТЕРЕСНЫЕ ОТВЕТЫ (>=2 keyword hits): {len(interesting)}")
print(f"{'='*60}")

for i, r in enumerate(interesting[:15]):
    print(f"\n  [{i}] len={r['len']} ct={r['ct']}")
    print(f"      url: {r['url'][:120]}")
    print(f"      hits: {r['hits'][:8]}")
    # Превью
    preview = r["body"][:200].replace("\n", " ")
    print(f"      preview: {preview}")

# ── Ищем MTOP-подобные (jsonp) ──
print(f"\n{'='*60}")
print("MTOP / JSONP ОТВЕТЫ")
print(f"{'='*60}")
mtop_like = []
for r in all_responses:
    body_s = r["body"].strip()
    if re.match(r'mtopjsonp\d*\(', body_s):
        mtop_like.append(r)
        print(f"  JSONP: len={r['len']} url={r['url'][:100]}")

# ── Ищем API-эндпоинты ──
print(f"\n{'='*60}")
print("API ЭНДПОИНТЫ (acs, mtop, api, h5)")
print(f"{'='*60}")
api_responses = []
for r in all_responses:
    u = r["url"].lower()
    if any(x in u for x in ["acs.", "mtop", "/api/", "h5/", "gateway", "pdp"]):
        api_responses.append(r)
        print(f"  len={r['len']} url={r['url'][:120]}")

# ── DOM extraction ──
print(f"\n{'='*60}")
print("DOM EXTRACTION")
print(f"{'='*60}")
try:
    dom = page.evaluate("""
        () => {
            const og = {};
            document.querySelectorAll('meta[property^="og:"]').forEach(m => {
                og[m.getAttribute('property')] = (m.content||'').substring(0,300);
            });
            const h1 = document.querySelector('h1');
            const titleTag = document.querySelector('title');
            const prices = [];
            document.querySelectorAll('[class*="price"],[class*="Price"],[data-role*="price"],[class*="Price"]').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
            });
            let jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            // Ищем window.__INITIAL_STATE__, window.runParams, window._d_h
            let states = {};
            if (window.__INITIAL_STATE__) states.__INITIAL_STATE__ = Object.keys(window.__INITIAL_STATE__).slice(0,20);
            if (window.runParams) states.runParams = Object.keys(window.runParams).slice(0,20);
            if (window._d_h) states._d_h = Object.keys(window._d_h).slice(0,20);
            if (window.__NEXT_DATA__) states.__NEXT_DATA__ = "present";
            
            // Ищем скрипты с данными
            let scriptData = [];
            document.querySelectorAll('script:not([src])').forEach(s => {
                const t = s.textContent || '';
                if (t.length > 500 && (t.includes('title') || t.includes('price') || t.includes('sku'))) {
                    scriptData.push({len: t.length, preview: t.substring(0,150)});
                }
            });
            
            return {
                og, h1: h1?.textContent?.trim()?.substring(0,200)||null,
                titleTag: titleTag?.textContent?.trim()?.substring(0,200)||null,
                prices: [...new Set(prices)].slice(0,15),
                jsonld, states, scriptData: scriptData.slice(0,5),
                bodyLen: document.body?.innerHTML?.length||0,
            };
        }
    """)
    print(f"  og:title = {dom.get('og',{}).get('og:title','')[:100]!r}")
    print(f"  og:image = {dom.get('og',{}).get('og:image','')[:100]!r}")
    print(f"  h1 = {dom.get('h1','')[:100]!r}")
    print(f"  titleTag = {dom.get('titleTag','')[:100]!r}")
    print(f"  prices = {dom.get('prices',[])}")
    print(f"  jsonld = {len(dom.get('jsonld',[]))} items")
    for j in dom.get('jsonld', [])[:2]:
        print(f"    jsonld: {str(j)[:150]}")
    print(f"  states = {dom.get('states',{})}")
    print(f"  scriptData = {len(dom.get('scriptData',[]))} scripts")
    for sd in dom.get('scriptData', [])[:3]:
        print(f"    len={sd['len']}: {sd['preview'][:100]}")
except Exception as e:
    print(f"  DOM eval error: {e}")

# ── Сохраняем ──
out = ROOT / "diag_ali_pw21_result.json"
save = {
    "url": URL,
    "final_url": page.url,
    "elapsed": round(elapsed, 1),
    "total_responses": len(all_responses),
    "interesting_count": len(interesting),
    "mtop_like_count": len(mtop_like),
    "api_count": len(api_responses),
    "interesting": [
        {"url": r["url"][:200], "len": r["len"], "ct": r["ct"], "hits": r["hits"],
         "preview": r["body"][:300]}
        for r in interesting[:20]
    ],
    "mtop_like": [
        {"url": r["url"][:200], "len": r["len"], "preview": r["body"][:300]}
        for r in mtop_like[:10]
    ],
    "api": [
        {"url": r["url"][:200], "len": r["len"], "preview": r["body"][:300]}
        for r in api_responses[:15]
    ],
    "dom": dom if 'dom' in dir() else {},
}
out.write_text(json.dumps(save, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")

ctx.close()
pw.stop()
print("DONE")
