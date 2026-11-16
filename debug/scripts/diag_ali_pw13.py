"""Диагностика AliExpress: все сетевые ответы + DOM-состояние после рендера."""
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
PROFILE_DIR = ROOT / "pw_ali_profile"
PRODUCT_ID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
HOME_URL = "https://www.aliexpress.com/"

from playwright.sync_api import sync_playwright

results = {
    "product_id": PRODUCT_ID,
    "url": URL,
    "responses": [],
    "mtop_bodies": [],
    "dom_state": {},
    "errors": [],
}

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    str(PROFILE_DIR),
    headless=True,
    args=[
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ],
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
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

# --- Логгер всех ответов ---
def on_response(resp):
    try:
        u = resp.url
        entry = {"url": u[:300], "status": resp.status}
        # Для MTOP / API — сохраняем body
        lower = u.lower()
        if "mtop" in lower or "api" in lower or "pdp" in lower:
            try:
                body = resp.text()
                entry["body_len"] = len(body)
                if len(body) > 50:
                    entry["body_preview"] = body[:500]
                    if "pdp" in lower or "query" in lower:
                        results["mtop_bodies"].append({"url": u[:400], "status": resp.status, "body": body})
            except Exception as e:
                entry["body_error"] = str(e)
        results["responses"].append(entry)
    except Exception:
        pass

page.on("response", on_response)

# --- Прогрев ---
print("[1] Прогрев сессии...")
try:
    cookies = ctx.cookies()
    if len(cookies) > 5:
        print(f"    Сессия уже тёплая ({len(cookies)} cookies)")
    else:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        for sel in ('button:has-text("Accetta")', 'button:has-text("Accept")', '[class*="cookie"] button'):
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(3000)
        print(f"    Прогрет, {len(ctx.cookies())} cookies")
except Exception as e:
    print(f"    Прогрев ошибка: {e}")
    results["errors"].append(f"warmup: {e}")

# --- Навигация на товар ---
print(f"[2] Навигация: {URL}")
t0 = time.monotonic()
try:
    resp = page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"    HTTP {resp.status if resp else '?'} за {time.monotonic()-t0:.1f}s")
except Exception as e:
    print(f"    Навигация ошибка: {e}")
    results["errors"].append(f"nav: {e}")

# --- Ожидание рендера: 30 сек, каждые 2 сек ---
print("[3] Ожидание рендера (30s)...")
for tick in range(15):
    page.wait_for_timeout(2000)
    try:
        st = page.evaluate("""
            () => {
                const h1 = document.querySelector('h1');
                const prices = [];
                const sels = ['[data-pl="price"]','[class*="pdp-price"]','[class*="price--current"]',
                    '[class*="Price--current"]','span[class*="price"]','[class*="priceRow"]',
                    '[class*="price-row"]','[data-pl*="price"]','[class*="sku-price"]',
                    '[class*="Price"]','[class*="price"]','[class*="PriceWrap"]'];
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
                    h1: h1 ? h1.textContent.trim().substring(0,300) : null,
                    title: document.title ? document.title.substring(0,300) : null,
                    prices: [...new Set(prices)].slice(0,15),
                    iframes,
                    bodyLen: document.body ? document.body.innerHTML.length : 0,
                    scriptCount: document.querySelectorAll('script').length,
                };
            }
        """)
        p_count = len(st.get("prices", []))
        print(f"    tick={tick*2}s: h1={str(st.get('h1',''))[:60]!r} prices={p_count} body={st.get('bodyLen',0)}")
        if tick == 5 or tick == 10 or tick == 14:
            results["dom_state"][f"tick_{tick*2}s"] = st
    except Exception as e:
        print(f"    tick={tick*2}s: eval error {e}")

# --- Глубокий DOM-дамп ---
print("[4] Глубокий DOM-дамп...")
try:
    deep = page.evaluate("""
        () => {
            const r = {};
            
            // Все элементы с "price" в классе
            r.priceElements = [];
            document.querySelectorAll('[class*="price"],[class*="Price"]').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t && t.length < 100) {
                    r.priceElements.push({
                        tag: el.tagName,
                        cls: (el.className||'').substring(0,120),
                        text: t.substring(0,80),
                        dataAttrs: Object.fromEntries(
                            [...el.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value.substring(0,60)])
                        )
                    });
                }
            });
            r.priceElements = r.priceElements.slice(0, 30);
            
            // data-pl, data-price атрибуты
            r.dataPriceAttrs = [];
            document.querySelectorAll('[data-price],[data-activity-price],[data-original-price],[data-pl]').forEach(el => {
                r.dataPriceAttrs.push({
                    tag: el.tagName,
                    cls: (el.className||'').substring(0,80),
                    dataPrice: el.getAttribute('data-price'),
                    dataActivity: el.getAttribute('data-activity-price'),
                    dataOriginal: el.getAttribute('data-original-price'),
                    dataPl: el.getAttribute('data-pl'),
                    text: (el.textContent||'').trim().substring(0,60),
                });
            });
            r.dataPriceAttrs = r.dataPriceAttrs.slice(0, 20);
            
            // JSON-LD
            r.jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { r.jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            
            // Meta tags
            r.meta = {};
            document.querySelectorAll('meta[property],meta[name]').forEach(m => {
                const p = m.getAttribute('property')||m.getAttribute('name')||'';
                const c = m.getAttribute('content')||'';
                if (p.includes('og:')||p.includes('product')||p.includes('price')||p.includes('title'))
                    r.meta[p] = c.substring(0,300);
            });
            
            // __INIT_DATA_STATE__ / runParams
            r.jsState = {};
            try { if (window.__INIT_DATA_STATE__) r.jsState.initStateKeys = Object.keys(window.__INIT_DATA_STATE__).slice(0,30); } catch(e){}
            try { if (window.runParams) r.jsState.runParamsKeys = Object.keys(window.runParams).slice(0,30); } catch(e){}
            
            // h1, title
            const h1 = document.querySelector('h1');
            r.h1 = h1 ? h1.textContent.trim().substring(0,300) : null;
            r.titleTag = document.title ? document.title.substring(0,300) : null;
            
            // Если есть div с id="pdp" или data-pdp
            r.pdpContainer = null;
            const pdp = document.querySelector('[id*="pdp"],[data-pdp],[class*="pdp"],[class*="PDP"]');
            if (pdp) r.pdpContainer = {tag: pdp.tagName, id: pdp.id, cls: (pdp.className||'').substring(0,120)};
            
            // Все iframe
            r.iframes = [];
            document.querySelectorAll('iframe').forEach(f => {
                r.iframes.push({src: (f.src||'').substring(0,300), w: f.width, h: f.height});
            });
            
            // Тело: первые 3000 символов innerHTML (для поиска паттернов)
            r.bodySnippet = document.body ? document.body.innerHTML.substring(0, 3000) : '';
            
            return r;
        }
    """)
    results["dom_state"]["deep"] = deep
    print(f"    priceElements: {len(deep.get('priceElements', []))}")
    print(f"    dataPriceAttrs: {len(deep.get('dataPriceAttrs', []))}")
    print(f"    jsonld: {len(deep.get('jsonld', []))}")
    print(f"    meta keys: {list(deep.get('meta', {}).keys())}")
    print(f"    h1: {str(deep.get('h1',''))[:80]!r}")
    print(f"    iframes: {len(deep.get('iframes', []))}")
except Exception as e:
    print(f"    DOM dump error: {e}")
    results["errors"].append(f"dom_dump: {e}")

# --- Скриншот ---
try:
    shot = ROOT / "diag_ali_pw13.png"
    page.screenshot(path=str(shot), full_page=False)
    print(f"[5] Скриншот → {shot.name}")
except Exception as e:
    print(f"[5] Скриншот ошибка: {e}")

# --- Итог ---
elapsed = time.monotonic() - t0
results["elapsed_sec"] = round(elapsed, 1)
results["total_responses"] = len(results["responses"])
results["mtop_count"] = len(results["mtop_bodies"])

out = ROOT / "diag_ali_pw13_result.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n[6] Результат → {out.name} ({out.stat().st_size} bytes)")
print(f"    Ответов: {len(results['responses'])}, MTOP: {len(results['mtop_bodies'])}, время: {elapsed:.1f}s")

# Печать ключевых price elements
pe = deep.get("priceElements", []) if 'deep' in dir() else []
if pe:
    print(f"\n    PRICE ELEMENTS (первые 10):")
    for p in pe[:10]:
        print(f"      <{p['tag']} class=\"{p['cls'][:60]}\"> {p['text'][:50]!r}")

dpa = deep.get("dataPriceAttrs", []) if 'deep' in dir() else []
if dpa:
    print(f"\n    DATA-PRICE ATTRS:")
    for a in dpa[:10]:
        print(f"      {a}")

ctx.close()
pw.stop()
print("\n[7] Done")
