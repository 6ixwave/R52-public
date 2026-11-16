"""PW16: Тест того же товара с US locale — проверим, что bigBossBan не глобальный."""
import json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PRODUCT_ID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

from playwright.sync_api import sync_playwright

PROFILE_DIR = ROOT / "pw_ali_profile"

print("=" * 60)
print(f"TEST: {URL}")
print("Locale: en-US | Country: US | TZ: America/New_York")
print("=" * 60)

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    str(PROFILE_DIR),
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

mtop_captured = []
def on_resp(resp):
    try:
        u = resp.url.lower()
        if "acs.aliexpress.com" in u and "pdp.pc.query" in u:
            body = resp.text()
            mtop_captured.append({"url": resp.url[:200], "status": resp.status, "body": body})
    except Exception:
        pass
page.on("response", on_resp)

t0 = time.monotonic()
try:
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  NAV OK, status={page.evaluate('() => document.readyState')}")
except Exception as e:
    print(f"  NAV ERROR: {e}")

# Ждём рендер + MTOP
for tick in range(8):
    page.wait_for_timeout(2500)
    if tick in (2, 4, 6):
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
    if mtop_captured:
        break

elapsed = time.monotonic() - t0
print(f"\n  Time: {elapsed:.1f}s, MTOP captured: {len(mtop_captured)}")

ctx.close()
pw.stop()

if not mtop_captured:
    print("FATAL: MTOP не перехвачен")
    sys.exit(1)

# ── Парсим MTOP ──
mb = mtop_captured[0]
body = mb["body"]
print(f"\nMTOP status: {mb['status']}")
print(f"MTOP body len: {len(body)}")
print(f"body[:100]: {body[:100]!r}")

body_s = body.strip()
m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', body_s, re.DOTALL)
inner = m.group(1) if m else body_s
print(f"After jsonp strip, len: {len(inner)}")

try:
    d = json.loads(inner)
    print(f"JSON OK! ret={d.get('ret')}")
except json.JSONDecodeError as e:
    print(f"JSON FAIL: {e}")
    print(f"inner[:200]: {inner[:200]!r}")
    sys.exit(1)

data = d.get("data", {})
result = data.get("result", data)
print(f"\nresult keys ({len(result)}): {list(result.keys())[:30]}")

# GLOBAL_DATA
gd = result.get("GLOBAL_DATA", {})
gdata = gd.get("globalData", {})
print(f"\n[GLOBAL_DATA]")
print(f"  bigBossBan: {gdata.get('bigBossBan')}")
print(f"  errorCode: {gdata.get('errorCode')}")
print(f"  banTip: {str(gdata.get('bigBossBanTip',''))[:120]}")

# SKUModule
skm = result.get("SKUModule", {})
print(f"\n[SKUModule] keys: {list(skm.keys())[:20]}")
print(f"  title: {str(skm.get('title',''))[:150]}")

# targetSkuPriceInfo
tspi = result.get("targetSkuPriceInfo", {})
print(f"\n[targetSkuPriceInfo]")
print(f"  keys: {list(tspi.keys())[:20]}")
print(f"  salePriceLocal: {tspi.get('salePriceLocal')}")
print(f"  salePriceString: {tspi.get('salePriceString')}")
print(f"  originalPrice: {tspi.get('originalPrice')}")
print(f"  currency: {tspi.get('currency')}")

# HEADER_IMAGE_PC
hipc = result.get("HEADER_IMAGE_PC", {})
ci = hipc.get("currentSkuImages", [])
print(f"\n[HEADER_IMAGE_PC] images: {len(ci)}")
for img in ci[:3]:
    print(f"  {str(img)[:100]}")

# seoInfo
seo = result.get("seoInfo", {})
print(f"\n[seoInfo] title: {str(seo.get('title',''))[:150]}")

# ── Тест _parse_mtop ──
print("\n" + "=" * 60)
print("ТЕСТ _parse_mtop")
print("=" * 60)

from r52.collectors.aliexpress import AliExpressCollector
collector = AliExpressCollector(data_dir=None, use_proxies=False)

obs = {
    "fields": {},
    "price_candidates": [],
    "media": {"items": []},
    "availability": "UNKNOWN",
    "category_path": None,
}

try:
    parsed = collector._parse_mtop(body, obs)
    print(f"_parse_mtop returned: {parsed}")
except Exception as e:
    print(f"_parse_mtop EXCEPTION: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:120]}")
print(f"  prices: {len(obs['price_candidates'])}")
for p in obs["price_candidates"][:5]:
    print(f"    {p}")
print(f"  images: {len(obs['media']['items'])}")
for img in obs["media"]["items"][:3]:
    print(f"    {str(img)[:100]}")
print(f"  availability: {obs['availability']}")
print(f"  category: {obs['category_path']}")

# Сохраняем
out = ROOT / "diag_ali_pw16_result.json"
out.write_text(json.dumps({
    "product_id": PRODUCT_ID,
    "locale": "en-US",
    "mtop_status": mb["status"],
    "mtop_body_len": len(body),
    "result_keys": list(result.keys()),
    "bigBossBan": gdata.get("bigBossBan"),
    "errorCode": gdata.get("errorCode"),
    "obs_fields": obs["fields"],
    "price_candidates": obs["price_candidates"],
    "media_count": len(obs["media"]["items"]),
    "availability": obs["availability"],
    "category_path": obs["category_path"],
    "elapsed": round(elapsed, 1),
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")
print("\nDONE")
