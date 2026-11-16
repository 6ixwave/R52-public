"""PW18: Форс US-локаль через cookie + ловим ВСЕ MTOP + обработка диалога."""
import json, re, sys, time, shutil
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

PROFILE = ROOT / "pw_ali_profile_us2"
if PROFILE.exists():
    shutil.rmtree(PROFILE)
    print("Удалён старый профиль")
PROFILE.mkdir(parents=True)

PRODUCT_ID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

# Cookie для US: site=usa, c_tp=USD, region=US, b_locale=en_US
US_COOKIE = "site=usa&c_tp=USD&ups_d=1|1|1|1&ups_u_t=1810045155400&region=US&b_locale=en_US&ae_u_p_s=2"

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

# Ставим cookie ДО навигации
ctx.add_cookies([{
    "name": "aep_usuc_f",
    "value": US_COOKIE,
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
        if "acs.aliexpress.com" in u and "pdp" in u:
            body = resp.text()
            all_mtops.append({
                "idx": len(all_mtops),
                "url": resp.url,
                "status": resp.status,
                "body_len": len(body),
                "body": body,
            })
            print(f"  [MTOP #{len(all_mtops)-1}] status={resp.status} len={len(body)}")
    except Exception as e:
        print(f"  [MTOP] err: {e}")
page.on("response", on_resp)

# ── Навигация ──
print(f"\nNAV: {URL}")
t0 = time.monotonic()
try:
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"  NAV ERR: {e}")

# Ждём рендер
for tick in range(10):
    page.wait_for_timeout(2500)
    if tick in (2, 5, 8):
        try:
            st = page.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    const prices = [];
                    document.querySelectorAll('[class*="price"],[class*="Price"]').forEach(el => {
                        const t = (el.textContent||'').trim();
                        if (t && t.length < 60 && /\\d/.test(t)) prices.push(t);
                    });
                    return {
                        h1: h1 ? h1.textContent.trim().substring(0,200) : null,
                        prices: [...new Set(prices)].slice(0,10),
                        bodyLen: document.body ? document.body.innerHTML.length : 0,
                    };
                }
            """)
            print(f"  tick={tick*2.5:.0f}s: h1={st.get('h1','')!r} prices={st.get('prices',[])} body={st.get('bodyLen',0)}")
        except Exception:
            pass

# ── Обработка диалога (region/cookie banner) ──
print("\n--- Проверка диалогов ---")
try:
    # Алиэкспресс часто показывает "Stay in this region" / "Continue"
    dialog_selectors = [
        'button:has-text("Stay")',
        'button:has-text("Continue")',
        'button:has-text("OK")',
        'button:has-text("Accept")',
        '[class*="dialog"] button',
        '[class*="modal"] button',
        '[role="dialog"] button',
    ]
    for sel in dialog_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                txt = btn.text_content()
                print(f"  Dialog button found: {sel} -> {txt!r}")
                btn.click()
                page.wait_for_timeout(2000)
                break
        except Exception:
            pass
except Exception as e:
    print(f"  Dialog handling: {e}")

# Ещё ждём после диалога
for tick in range(4):
    page.wait_for_timeout(2000)

elapsed = time.monotonic() - t0
print(f"\nTotal time: {elapsed:.1f}s, MTOP total: {len(all_mtops)}")

# ── Анализ всех MTOP ──
print(f"\n{'='*60}")
print(f"АНАЛИЗ {len(all_mtops)} MTOP ОТВЕТОВ")
print(f"{'='*60}")

best_mtop = None
for mt in all_mtops:
    body_s = mt["body"].strip()
    m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', body_s, re.DOTALL)
    inner = m.group(1) if m else body_s
    try:
        d = json.loads(inner)
        data = d.get("data", {})
        result = data.get("result", data)
        keys = list(result.keys()) if isinstance(result, dict) else []
        gd = result.get("GLOBAL_DATA", {}).get("globalData", {}) if isinstance(result, dict) else {}
        ban = gd.get("bigBossBan", False)
        print(f"\n  [MTOP #{mt['idx']}] len={mt['body_len']} keys={keys[:15]} ban={ban}")
        if len(keys) > 2:
            best_mtop = mt
            best_mtop["_parsed"] = d
            best_mtop["_result"] = result
    except json.JSONDecodeError as e:
        print(f"\n  [MTOP #{mt['idx']}] len={mt['body_len']} JSON FAIL: {e}")
        print(f"    preview: {inner[:100]!r}")

# ── Извлекаем из лучшего MTOP ──
if best_mtop and "_result" in best_mtop:
    result = best_mtop["_result"]
    print(f"\n{'='*60}")
    print(f"ЛУЧШИЙ MTOP: #{best_mtop['idx']}")
    print(f"{'='*60}")

    gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
    print(f"  bigBossBan: {gd.get('bigBossBan')}")
    print(f"  errorCode: {gd.get('errorCode')}")

    skm = result.get("SKUModule", {})
    print(f"  SKUModule title: {str(skm.get('title',''))[:150]}")
    print(f"  SKUModule keys: {list(skm.keys())[:15]}")

    tspi = result.get("targetSkuPriceInfo", {})
    print(f"  Price: {tspi.get('salePriceString')} {tspi.get('currency')}")
    print(f"  Original: {tspi.get('originalPrice')}")

    hipc = result.get("HEADER_IMAGE_PC", {})
    ci = hipc.get("currentSkuImages", [])
    print(f"  Images: {len(ci)}")
    for img in ci[:3]:
        print(f"    {str(img)[:90]}")

    seo = result.get("seoInfo", {})
    print(f"  SEO title: {str(seo.get('title',''))[:150]}")

    # _parse_mtop
    from r52.collectors.aliexpress import AliExpressCollector
    collector = AliExpressCollector(data_dir=None, use_proxies=False)
    obs = {"fields": {}, "price_candidates": [], "media": {"items": []}, "availability": "UNKNOWN", "category_path": None}
    try:
        parsed = collector._parse_mtop(best_mtop["body"], obs)
        print(f"\n  _parse_mtop: {parsed}")
        print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:120]}")
        print(f"  prices: {len(obs['price_candidates'])}")
        for p in obs["price_candidates"][:3]:
            print(f"    {p}")
        print(f"  images: {len(obs['media']['items'])}")
        print(f"  availability: {obs['availability']}")
    except Exception as e:
        print(f"  _parse_mtop ERR: {e}")
        import traceback; traceback.print_exc()
else:
    print("\n  Нет MTOP с полными данными!")

# ── Сохраняем ──
out = ROOT / "diag_ali_pw18_result.json"
summary = {
    "product_id": PRODUCT_ID,
    "locale_forced": "en-US/USD/US",
    "mtop_count": len(all_mtops),
    "mtops": [
        {
            "idx": mt["idx"],
            "url": mt["url"][:200],
            "status": mt["status"],
            "body_len": mt["body_len"],
            "body_preview": mt["body"][:200],
        }
        for mt in all_mtops
    ],
    "best_mtop_idx": best_mtop["idx"] if best_mtop else None,
    "elapsed": round(elapsed, 1),
}
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")

ctx.close()
pw.stop()
print("DONE")
