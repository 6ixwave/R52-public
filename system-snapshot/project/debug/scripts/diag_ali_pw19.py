"""PW19: Тёплый профиль + US-поддомен + cookie + dialog handling + fallback IT."""
import json, re, sys, time, shutil
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

# Используем тёплый профиль из pw13 (17 cookies)
PROFILE = ROOT / "pw_ali_profile"
print(f"Profile: {PROFILE}, exists={PROFILE.exists()}")

PRODUCT_ID = "1005011813323763"
US_URL = f"https://us.aliexpress.com/item/{PRODUCT_ID}.html"
IT_URL = f"https://it.aliexpress.com/item/{PRODUCT_ID}.html"

# US cookie
US_COOKIE_VAL = "site=usa&c_tp=USD&ups_d=1|1|1|1&ups_u_t=1810045155400&region=US&b_locale=en_US&ae_u_p_s=2"

def parse_mtop_body(body):
    """Возвращает (dict, result_dict) или (None, err)."""
    body_s = body.strip()
    m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', body_s, re.DOTALL)
    inner = m.group(1) if m else body_s
    try:
        d = json.loads(inner)
    except json.JSONDecodeError as e:
        return None, f"JSON: {e}"
    data = d.get("data", {})
    result = data.get("result", data) if isinstance(data, dict) else data
    return d, result

def extract_product(result):
    """Извлекает ключевые поля из result dict."""
    out = {}
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
    out["bigBossBan"] = gd.get("bigBossBan", False)
    out["errorCode"] = gd.get("errorCode", "")
    out["banTip"] = str(gd.get("bigBossBanTip", ""))[:150]

    skm = result.get("SKUModule", {})
    out["title"] = str(skm.get("title", ""))[:200]
    out["subTitle"] = str(skm.get("subTitle", ""))[:100]
    out["skuKeys"] = list(skm.keys())[:20]

    tspi = result.get("targetSkuPriceInfo", {})
    out["salePriceLocal"] = tspi.get("salePriceLocal")
    out["salePriceString"] = tspi.get("salePriceString")
    out["originalPrice"] = tspi.get("originalPrice")
    out["currency"] = tspi.get("currency")

    hipc = result.get("HEADER_IMAGE_PC", {})
    ci = hipc.get("currentSkuImages", [])
    out["images"] = [str(x)[:120] for x in ci[:5]]
    out["imageCount"] = len(ci)

    seo = result.get("seoInfo", {})
    out["seoTitle"] = str(seo.get("title", ""))[:200]

    cat = result.get("CATEGORY", result.get("categoryModule", {}))
    out["category"] = str(cat)[:150] if cat else None

    return out

def handle_dialogs(page, label=""):
    """Агрессивно ищет и кликает кнопки диалога."""
    selectors = [
        'button:has-text("Stay")',
        'button:has-text("Continue")',
        'button:has-text("OK")',
        'button:has-text("Accept")',
        'button:has-text("Confirm")',
        'button:has-text("Yes")',
        '[class*="dialog"] button',
        '[class*="modal"] button',
        '[class*="Dialog"] button',
        '[class*="Modal"] button',
        '[role="dialog"] button',
        '[class*="region"] button',
        '[class*="Region"] button',
        'a:has-text("Stay")',
        'a:has-text("Continue")',
        'button[class*="primary"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                txt = (btn.text_content() or "").strip()
                print(f"  [DIALOG{label}] clicked: {sel} -> {txt!r}")
                btn.click()
                page.wait_for_timeout(2000)
                return True
        except Exception:
            pass
    return False

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

# Ставим US cookie
ctx.add_cookies([{
    "name": "aep_usuc_f",
    "value": US_COOKIE_VAL,
    "domain": ".aliexpress.com",
    "path": "/",
    "httpOnly": False,
    "secure": True,
    "sameSite": "Lax",
}])

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# ── Ловим ВСЕ MTOP ──
all_mtops = []
def on_resp(resp):
    try:
        u = resp.url.lower()
        if "acs.aliexpress.com" in u:
            body = resp.text()
            all_mtops.append({
                "idx": len(all_mtops),
                "url": resp.url,
                "status": resp.status,
                "body_len": len(body),
                "body": body,
            })
    except Exception:
        pass
page.on("response", on_resp)

results = {}

# ═══════════════════════════════════════════
# АТТАКА 1: US поддомен
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print(f"АТТАКА 1: US поддомен")
print(f"URL: {US_URL}")
print(f"{'='*60}")

all_mtops.clear()
t0 = time.monotonic()
try:
    page.goto(US_URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"  NAV ERR: {e}")

# Ждём + ловим диалог
for tick in range(12):
    page.wait_for_timeout(2500)
    if tick == 2:
        handle_dialogs(page, " US-2s")
    if tick == 4:
        handle_dialogs(page, " US-4s")
    if tick in (2, 5, 8, 11):
        try:
            st = page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    return {
                        h1: h1 ? h1.textContent.trim().substring(0,150) : null,
                        bodyLen: document.body ? document.body.innerHTML.length : 0,
                        url: window.location.href,
                    };
                }
            """)
            print(f"  t={tick*2.5:.0f}s: h1={st.get('h1')!r} body={st.get('bodyLen',0)} url={st.get('url','')[:80]}")
        except Exception:
            pass
    if len(all_mtops) >= 1 and tick > 4:
        # Дали время на 2-й и 3-й MTOP
        page.wait_for_timeout(3000)
        break

elapsed = time.monotonic() - t0
print(f"  Time: {elapsed:.1f}s, MTOP: {len(all_mtops)}")

# Анализ MTOP
best_result = None
for mt in all_mtops:
    d, result = parse_mtop_body(mt["body"])
    if d is None:
        print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} FAIL: {result}")
        continue
    keys = list(result.keys()) if isinstance(result, dict) else []
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {}) if isinstance(result, dict) else {}
    ban = gd.get("bigBossBan", False)
    print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} keys={keys[:10]} ban={ban}")
    if len(keys) > 2 and not ban:
        best_result = result
        best_result["_mtop_idx"] = mt["idx"]
        best_result["_mtop_body"] = mt["body"]

if best_result:
    prod = extract_product(best_result)
    results["US"] = prod
    print(f"\n  US RESULT: title={prod['title'][:80]!r} price={prod['salePriceString']} imgs={prod['imageCount']}")
else:
    # Может быть и с ban — сохраняем
    for mt in all_mtops:
        d, result = parse_mtop_body(mt["body"])
        if d and isinstance(result, dict) and len(result.keys()) > 2:
            prod = extract_product(result)
            results["US_ban"] = prod
            break
    if "US" not in results:
        print("  US: нет полных данных")

# ═══════════════════════════════════════════
# АТТАКА 2: IT поддомен (fallback, знаем что рендерит)
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print(f"АТТАКА 2: IT поддомен (fallback)")
print(f"URL: {IT_URL}")
print(f"{'='*60}")

all_mtops.clear()
t0 = time.monotonic()
try:
    page.goto(IT_URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"  NAV ERR: {e}")

for tick in range(12):
    page.wait_for_timeout(2500)
    if tick == 2:
        handle_dialogs(page, " IT-2s")
    if tick in (2, 5, 8, 11):
        try:
            st = page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    const og = document.querySelector('meta[property="og:title"]');
                    return {
                        h1: h1 ? h1.textContent.trim().substring(0,150) : null,
                        ogTitle: og ? og.content.substring(0,150) : null,
                        bodyLen: document.body ? document.body.innerHTML.length : 0,
                    };
                }
            """)
            print(f"  t={tick*2.5:.0f}s: h1={st.get('h1')!r} og={st.get('ogTitle','')[:60]!r} body={st.get('bodyLen',0)}")
        except Exception:
            pass
    if len(all_mtops) >= 1 and tick > 5:
        page.wait_for_timeout(3000)
        break

elapsed = time.monotonic() - t0
print(f"  Time: {elapsed:.1f}s, MTOP: {len(all_mtops)}")

# Анализ
for mt in all_mtops:
    d, result = parse_mtop_body(mt["body"])
    if d is None:
        continue
    keys = list(result.keys()) if isinstance(result, dict) else []
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {}) if isinstance(result, dict) else {}
    ban = gd.get("bigBossBan", False)
    print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} keys={keys[:10]} ban={ban}")
    if len(keys) > 2:
        prod = extract_product(result)
        results["IT"] = prod
        if mt["idx"] == 0 or mt["body_len"] > 5000:
            results["IT_body"] = mt["body"]
        break

# ── DOM fallback: meta og + JS state ──
print("\n--- DOM fallback (meta/og + script) ---")
try:
    dom_data = page.evaluate("""
        () => {
            const og = {};
            document.querySelectorAll('meta[property^="og:"]').forEach(m => {
                og[m.getAttribute('property')] = m.content;
            });
            // Ищем window.runParams или __INITIAL_STATE__
            let jsState = null;
            if (window.runParams) jsState = window.runParams;
            if (window.__INITIAL_STATE__) jsState = window.__INITIAL_STATE__;
            
            // h1 или title
            const h1 = document.querySelector('h1');
            const titleTag = document.querySelector('title');
            
            // Цены из DOM
            const prices = [];
            document.querySelectorAll('[class*="price"],[class*="Price"],[data-role*="price"]').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
            });
            
            return {
                og: og,
                h1: h1 ? h1.textContent.trim() : null,
                titleTag: titleTag ? titleTag.textContent.trim() : null,
                prices: [...new Set(prices)].slice(0, 10),
                jsStateKeys: jsState ? Object.keys(jsState).slice(0, 20) : [],
                bodyLen: document.body ? document.body.innerHTML.length : 0,
            };
        }
    """)
    print(f"  og:title = {dom_data.get('og',{}).get('og:title','')[:100]!r}")
    print(f"  og:image = {dom_data.get('og',{}).get('og:image','')[:100]!r}")
    print(f"  h1 = {dom_data.get('h1','')[:100]!r}")
    print(f"  titleTag = {dom_data.get('titleTag','')[:100]!r}")
    print(f"  prices = {dom_data.get('prices',[])}")
    print(f"  jsStateKeys = {dom_data.get('jsStateKeys',[])}")
    results["DOM"] = dom_data
except Exception as e:
    print(f"  DOM eval error: {e}")

# ═══════════════════════════════════════════
# ТЕСТ _parse_mtop с лучшим body
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ТЕСТ _parse_mtop (IT body)")
print(f"{'='*60}")

mtop_body = results.get("IT_body", "")
if mtop_body:
    from r52.collectors.aliexpress import AliExpressCollector
    collector = AliExpressCollector(data_dir=None, use_proxies=False)
    obs = {"fields": {}, "price_candidates": [], "media": {"items": []}, "availability": "UNKNOWN", "category_path": None}
    try:
        parsed = collector._parse_mtop(mtop_body, obs)
        print(f"  _parse_mtop: {parsed}")
        print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:120]}")
        print(f"  prices: {len(obs['price_candidates'])}")
        for p in obs["price_candidates"][:5]:
            print(f"    {p}")
        print(f"  images: {len(obs['media']['items'])}")
        print(f"  availability: {obs['availability']}")
        results["parse_mtop"] = {
            "returned": parsed,
            "title": obs["fields"].get("title", {}).get("value", ""),
            "prices": obs["price_candidates"][:5],
            "images": len(obs["media"]["items"]),
            "availability": obs["availability"],
        }
    except Exception as e:
        print(f"  _parse_mtop ERR: {e}")
        import traceback; traceback.print_exc()
else:
    print("  Нет IT body для _parse_mtop")

# ── Сохраняем ──
out = ROOT / "diag_ali_pw19_result.json"
# Убираем body из JSON (слишком большой)
save = {k: v for k, v in results.items() if k != "IT_body"}
save["IT_body_len"] = len(mtop_body)
out.write_text(json.dumps(save, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")

ctx.close()
pw.stop()
print("\nDONE")
