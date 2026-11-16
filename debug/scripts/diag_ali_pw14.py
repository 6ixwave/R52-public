"""Диагностика: парсинг MTOP + тест другого товара/локали."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ─── Часть 1: Парсим MTOP из diag_ali_pw13_result.json ───
print("=" * 60)
print("ЧАСТЬ 1: Парсинг MTOP из diag_ali_pw13_result.json")
print("=" * 60)

diag = json.loads((ROOT / "diag_ali_pw13_result.json").read_text(encoding="utf-8"))
mtop_bodies = diag.get("mtop_bodies", [])
print(f"MTOP bodies: {len(mtop_bodies)}")

# Реальный MTOP — тот, где URL содержит acs.aliexpress.com
real_mtop = None
for mb in mtop_bodies:
    if "acs.aliexpress.com" in mb.get("url", ""):
        real_mtop = mb
        break

if not real_mtop:
    print("РЕАЛЬНЫЙ MTOP НЕ НАЙДЕН! URL'ы:")
    for mb in mtop_bodies:
        print(f"  {mb['url'][:100]}")
    sys.exit(1)

body = real_mtop["body"]
print(f"MTOP URL: {real_mtop['url'][:120]}...")
print(f"MTOP body len: {len(body)}")
print(f"MTOP body preview: {body[:200]}")

# Чистим jsonp
import re
m = re.match(r'mtopjsonp\d*\((.*)\)', body, re.DOTALL)
if m:
    body = m.group(1)
try:
    d = json.loads(body)
except Exception as e:
    print(f"JSON parse error: {e}")
    sys.exit(1)

print(f"ret: {d.get('ret')}")
data = d.get("data", {})
result = data.get("result", data)
print(f"result keys: {list(result.keys())[:30]}")

# Проверяем targetSkuPriceInfo
tspi = result.get("targetSkuPriceInfo")
print(f"\ntargetSkuPriceInfo: {'YES' if tspi else 'NO'}")
if tspi:
    print(f"  keys: {list(tspi.keys())[:20]}")
    print(f"  salePriceLocal: {tspi.get('salePriceLocal')}")
    print(f"  salePriceString: {tspi.get('salePriceString')}")
    print(f"  originalPrice: {tspi.get('originalPrice')}")

# GLOBAL_DATA
gd = result.get("GLOBAL_DATA", {})
gdata = gd.get("globalData", {})
print(f"\nGLOBAL_DATA.globalData keys: {list(gdata.keys())[:20]}")
print(f"  bigBossBan: {gdata.get('bigBossBan')}")
print(f"  errorCode: {gdata.get('errorCode')}")
print(f"  bigBossBanTip: {str(gdata.get('bigBossBanTip',''))[:100]}")

# SKUModule
skm = result.get("SKUModule", {})
print(f"\nSKUModule keys: {list(skm.keys())[:20]}")
if skm:
    print(f"  title: {str(skm.get('title',''))[:100]}")
    print(f"  skuPriceList: {str(skm.get('skuPriceList',''))[:200]}")

# seoInfo
seo = result.get("seoInfo", {})
print(f"\nseoInfo: {str(seo)[:200]}")

# HEADER_IMAGE_PC
hipc = result.get("HEADER_IMAGE_PC", {})
print(f"\nHEADER_IMAGE_PC keys: {list(hipc.keys())[:10]}")
if hipc:
    ci = hipc.get("currentSkuImages", [])
    print(f"  currentSkuImages: {len(ci)} items")

# ─── Парсинг через collector ───
print("\n" + "=" * 60)
print("ПАРСИНГ ЧЕРЕЗ AliExpressCollector._parse_mtop()")
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

# Передаём полный body (с jsonp)
parsed = collector._parse_mtop(real_mtop["body"], obs)
print(f"_parse_mtop returned: {parsed}")
print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:100]}")
print(f"  prices: {len(obs['price_candidates'])}")
for p in obs["price_candidates"][:5]:
    print(f"    {p}")
print(f"  images: {len(obs['media']['items'])}")
print(f"  availability: {obs['availability']}")
print(f"  category: {obs['category_path']}")

# ─── Часть 2: Тест с другим товаром (US locale) ───
print("\n" + "=" * 60)
print("ЧАСТЬ 2: Тест с US locale / другим товаром")
print("=" * 60)

from playwright.sync_api import sync_playwright

PROFILE_DIR = ROOT / "pw_ali_profile"
# Пробуем товар 1005006798847852 (из diag_ali_pw12) и 1005011813323763
TEST_PRODUCTS = [
    ("1005011813323763", "it-IT", "IT", "Europe/Rome"),
    ("1005011813323763", "en-US", "US", "America/New_York"),
]

pw = sync_playwright().start()
for pid, locale, country, tz in TEST_PRODUCTS:
    url = f"https://www.aliexpress.com/item/{pid}.html"
    print(f"\n--- {pid} | locale={locale} | country={country} ---")
    
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale=locale,
        viewport={"width": 1920, "height": 1080},
        timezone_id=tz,
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
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  nav error: {e}")
    
    # Ждём 20 сек
    for tick in range(10):
        page.wait_for_timeout(2000)
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
            if tick in (2, 5, 9):
                print(f"  tick={tick*2}s: h1={str(st.get('h1',''))[:60]!r} prices={len(st.get('prices',[]))} body={st.get('bodyLen',0)}")
        except Exception:
            pass
    
    elapsed = time.monotonic() - t0
    print(f"  Time: {elapsed:.1f}s, MTOP captured: {len(mtop_captured)}")
    
    if mtop_captured:
        mb = mtop_captured[0]
        print(f"  MTOP status: {mb['status']}, body_len: {len(mb['body'])}")
        # Быстрый парсинг
        obs2 = {"fields": {}, "price_candidates": [], "media": {"items": []}, "availability": "UNKNOWN", "category_path": None}
        p2 = collector._parse_mtop(mb["body"], obs2)
        print(f"  _parse_mtop: {p2}")
        print(f"  title: {obs2['fields'].get('title', {}).get('value', 'N/A')[:80]}")
        print(f"  prices: {len(obs2['price_candidates'])}")
        for p in obs2["price_candidates"][:3]:
            print(f"    {p.get('role')}: {p.get('amount')} {p.get('currency')}")
        print(f"  availability: {obs2['availability']}")
    
    ctx.close()

pw.stop()
print("\nDone")
