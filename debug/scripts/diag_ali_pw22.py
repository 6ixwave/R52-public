"""PW22: Точечный захват MTOP с aliexpress.us + полный разбор + _parse_mtop."""
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

# ── Ловим MTOP: acs.aliexpress.us И acs.aliexpress.com ──
mtops = []
def on_resp(resp):
    try:
        u = resp.url.lower()
        if ("acs.aliexpress.us" in u or "acs.aliexpress.com" in u) and "pdp" in u:
            body = resp.text()
            mtops.append({
                "idx": len(mtops),
                "url": resp.url,
                "status": resp.status,
                "body_len": len(body),
                "body": body,
            })
            print(f"  [MTOP #{len(mtops)-1}] status={resp.status} len={len(body)}")
            print(f"    url: {resp.url[:120]}")
    except Exception as e:
        print(f"  [MTOP] err: {e}")

page.on("response", on_resp)

# ── Навигация ──
t0 = time.monotonic()
try:
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"NAV ERR: {e}")

# Ждём MTOP до 25 сек
for tick in range(10):
    page.wait_for_timeout(2500)
    t = tick * 2.5
    if t in (5, 10, 15, 20):
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
    if len(mtops) >= 1 and t >= 10:
        page.wait_for_timeout(2000)
        break

elapsed = time.monotonic() - t0
print(f"\nTotal: {elapsed:.1f}s, MTOP: {len(mtops)}")

if not mtops:
    print("FATAL: MTOP не перехвачен")
    ctx.close()
    pw.stop()
    sys.exit(1)

# ── Парсим каждый MTOP ──
def parse_mtop(body):
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

best = None
for mt in mtops:
    d, result = parse_mtop(mt["body"])
    if d is None:
        print(f"\n[MTOP #{mt['idx']}] FAIL: {result}")
        continue
    keys = list(result.keys()) if isinstance(result, dict) else []
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {}) if isinstance(result, dict) else {}
    ban = gd.get("bigBossBan", False)
    print(f"\n[MTOP #{mt['idx']}] len={mt['body_len']} keys={keys[:15]} ban={ban}")
    if len(keys) > 2:
        if best is None or mt["body_len"] > best["body_len"]:
            best = mt
            best["_result"] = result
            best["_json"] = d

if not best or "_result" not in best:
    print("FATAL: нет MTOP с полными данными")
    ctx.close()
    pw.stop()
    sys.exit(1)

result = best["_result"]
print(f"\n{'='*60}")
print(f"ЛУЧШИЙ MTOP: #{best['idx']} len={best['body_len']}")
print(f"{'='*60}")

# ── Извлечение полей ──
gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
print(f"\n[GLOBAL_DATA]")
print(f"  bigBossBan: {gd.get('bigBossBan')}")
print(f"  errorCode: {gd.get('errorCode')}")
print(f"  banTip: {str(gd.get('bigBossBanTip',''))[:120]}")

skm = result.get("SKUModule", {})
print(f"\n[SKUModule] keys: {list(skm.keys())[:20]}")
print(f"  title: {str(skm.get('title',''))[:200]}")
print(f"  subTitle: {str(skm.get('subTitle',''))[:100]}")

tspi = result.get("targetSkuPriceInfo", {})
print(f"\n[targetSkuPriceInfo] keys: {list(tspi.keys())[:20]}")
print(f"  salePriceLocal: {tspi.get('salePriceLocal')}")
print(f"  salePriceString: {tspi.get('salePriceString')}")
print(f"  originalPrice: {tspi.get('originalPrice')}")
print(f"  currency: {tspi.get('currency')}")

hipc = result.get("HEADER_IMAGE_PC", {})
ci = hipc.get("currentSkuImages", [])
print(f"\n[HEADER_IMAGE_PC] keys: {list(hipc.keys())[:15]}")
print(f"  images: {len(ci)}")
for img in ci[:5]:
    print(f"    {str(img)[:120]}")

seo = result.get("seoInfo", {})
print(f"\n[seoInfo]")
print(f"  title: {str(seo.get('title',''))[:200]}")
print(f"  keywords: {str(seo.get('keywords',''))[:100]}")

cat = result.get("CATEGORY", result.get("categoryModule", {}))
print(f"\n[CATEGORY] keys: {list(cat.keys())[:15]}")
if isinstance(cat, dict):
    for k in list(cat.keys())[:8]:
        print(f"  {k}: {str(cat[k])[:100]}")

desc = result.get("DESCRIPTION", result.get("descriptionModule", {}))
print(f"\n[DESCRIPTION] keys: {list(desc.keys())[:10]}")

ship = result.get("SHIP", result.get("shipModule", {}))
print(f"\n[SHIP] keys: {list(ship.keys())[:10]}")

# SKUPriceList
skpl = result.get("SKUPriceList", result.get("skuPriceList", []))
print(f"\n[SKUPriceList] count: {len(skpl) if isinstance(skpl, list) else 0}")
if isinstance(skpl, list) and skpl:
    print(f"  first: {str(skpl[0])[:200]}")

# ── DOM fallback ──
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
            const prices = [];
            document.querySelectorAll('[class*="price"],[class*="Price"],[data-role*="price"]').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
            });
            let jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                try { jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            return {
                og: og,
                h1: h1?.textContent?.trim()?.substring(0,200)||null,
                prices: [...new Set(prices)].slice(0,15),
                jsonld: jsonld,
                bodyLen: document.body?.innerHTML?.length||0,
            };
        }
    """)
    print(f"  og:title = {dom.get('og',{}).get('og:title','')[:120]!r}")
    print(f"  og:image = {dom.get('og',{}).get('og:image','')[:120]!r}")
    print(f"  h1 = {dom.get('h1','')[:100]!r}")
    print(f"  prices = {dom.get('prices',[])}")
    print(f"  jsonld = {len(dom.get('jsonld',[]))} items")
    for j in dom.get('jsonld', [])[:2]:
        print(f"    {str(j)[:200]}")
except Exception as e:
    print(f"  DOM eval err: {e}")
    dom = {}

# ── Тест _parse_mtop ──
print(f"\n{'='*60}")
print("ТЕСТ _parse_mtop")
print(f"{'='*60}")

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
    parsed = collector._parse_mtop(best["body"], obs)
    print(f"  _parse_mtop returned: {parsed}")
except Exception as e:
    print(f"  _parse_mtop EXCEPTION: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:150]}")
print(f"  prices: {len(obs['price_candidates'])}")
for p in obs["price_candidates"][:5]:
    print(f"    {p}")
print(f"  images: {len(obs['media']['items'])}")
for img in obs["media"]["items"][:5]:
    print(f"    {str(img)[:120]}")
print(f"  availability: {obs['availability']}")
print(f"  category: {obs['category_path']}")

# ── Сохраняем ──
out = ROOT / "diag_ali_pw22_result.json"
save = {
    "product_id": PRODUCT_ID,
    "url": URL,
    "final_url": page.url,
    "elapsed": round(elapsed, 1),
    "mtop_count": len(mtops),
    "best_mtop_idx": best["idx"],
    "best_mtop_len": best["body_len"],
    "result_keys": list(result.keys()),
    "bigBossBan": gd.get("bigBossBan"),
    "errorCode": gd.get("errorCode"),
    "title": str(skm.get("title", ""))[:300],
    "salePriceString": tspi.get("salePriceString"),
    "salePriceLocal": tspi.get("salePriceLocal"),
    "originalPrice": tspi.get("originalPrice"),
    "currency": tspi.get("currency"),
    "image_count": len(ci),
    "images": [str(x)[:150] for x in ci[:5]],
    "seo_title": str(seo.get("title", ""))[:300],
    "category": str(cat)[:200] if cat else None,
    "sku_price_count": len(skpl) if isinstance(skpl, list) else 0,
    "dom": dom,
    "parse_mtop": {
        "returned": parsed,
        "title": obs["fields"].get("title", {}).get("value", ""),
        "prices": obs["price_candidates"][:5],
        "images": len(obs["media"]["items"]),
        "availability": obs["availability"],
        "category": obs["category_path"],
    },
}
out.write_text(json.dumps(save, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")

ctx.close()
pw.stop()
print("\nDONE")
