"""PW20: Тёплый профиль (без US-cookie) + www-редирект + 2 товара + DOM fallback."""
import json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

PROFILE = ROOT / "pw_ali_profile"
print(f"Profile: {PROFILE}, exists={PROFILE.exists()}")

PRODUCTS = [
    "1005011813323763",
    "1005006798847852",
]

def parse_mtop_body(body):
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
    out = {}
    if not isinstance(result, dict):
        return out
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
    out["bigBossBan"] = gd.get("bigBossBan", False)
    out["errorCode"] = gd.get("errorCode", "")
    out["banTip"] = str(gd.get("bigBossBanTip", ""))[:150]

    skm = result.get("SKUModule", {})
    out["title"] = str(skm.get("title", ""))[:200]
    out["skuKeys"] = list(skm.keys())[:20]

    tspi = result.get("targetSkuPriceInfo", {})
    out["salePriceLocal"] = tspi.get("salePriceLocal")
    out["salePriceString"] = tspi.get("salePriceString")
    out["originalPrice"] = tspi.get("originalPrice")
    out["currency"] = tspi.get("currency")

    hipc = result.get("HEADER_IMAGE_PC", {})
    ci = hipc.get("currentSkuImages", [])
    out["imageCount"] = len(ci)
    out["images"] = [str(x)[:120] for x in ci[:5]]

    seo = result.get("seoInfo", {})
    out["seoTitle"] = str(seo.get("title", ""))[:200]

    # SKUPriceList
    skpl = result.get("SKUPriceList", result.get("skuPriceList", []))
    out["skuPriceCount"] = len(skpl) if isinstance(skpl, list) else 0

    return out

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    str(PROFILE),
    headless=True,
    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    locale="it-IT",
    viewport={"width": 1920, "height": 1080},
    timezone_id="Europe/Rome",
    ignore_https_errors=True,
)
ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = {runtime: {}};
""")

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# Прогрев: главная
print("\nПрогрев: www.aliexpress.com...")
page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(5000)
cookies = ctx.cookies()
print(f"  Cookies: {len(cookies)}")
# Проверяем locale cookie
for c in cookies:
    if "usuc" in c["name"].lower() or "locale" in c["name"].lower():
        print(f"  Cookie {c['name']}: {c['value'][:80]}")

all_results = {}

for pid in PRODUCTS:
    url = f"https://www.aliexpress.com/item/{pid}.html"
    print(f"\n{'='*60}")
    print(f"PRODUCT: {pid}")
    print(f"URL: {url}")
    print(f"{'='*60}")

    mtops = []
    def on_resp(resp):
        try:
            u = resp.url.lower()
            if "acs.aliexpress.com" in u:
                body = resp.text()
                mtops.append({
                    "idx": len(mtops),
                    "url": resp.url,
                    "status": resp.status,
                    "body_len": len(body),
                    "body": body,
                })
        except Exception:
            pass
    page.on("response", on_resp)

    t0 = time.monotonic()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        print(f"  NAV OK ({time.monotonic()-t0:.1f}s)")
    except Exception as e:
        print(f"  NAV ERR: {e}")

    # Ждём до 35 сек, проверяем каждые 3 сек
    for tick in range(12):
        page.wait_for_timeout(3000)
        t = tick * 3
        if t in (6, 12, 18, 24, 30):
            try:
                st = page.evaluate("""
                    () => {
                        const h1 = document.querySelector('h1');
                        const og = document.querySelector('meta[property="og:title"]');
                        const ogImg = document.querySelector('meta[property="og:image"]');
                        return {
                            h1: h1 ? h1.textContent.trim().substring(0,150) : null,
                            ogTitle: og ? og.content.substring(0,150) : null,
                            ogImage: ogImg ? ogImg.content.substring(0,100) : null,
                            bodyLen: document.body ? document.body.innerHTML.length : 0,
                            url: window.location.href,
                        };
                    }
                """)
                print(f"  t={t}s: body={st.get('bodyLen',0)} h1={st.get('h1','')[:50]!r} og={st.get('ogTitle','')[:50]!r}")
                print(f"         url={st.get('url','')[:80]}")
            except Exception:
                pass
        # Если MTOP уже есть и прошло >10 сек — даём ещё 3 сек на доп. MTOP
        if len(mtops) >= 1 and t >= 12:
            page.wait_for_timeout(3000)
            break

    elapsed = time.monotonic() - t0
    print(f"  Time: {elapsed:.1f}s, MTOP: {len(mtops)}")

    # Анализ MTOP
    best = None
    for mt in mtops:
        d, result = parse_mtop_body(mt["body"])
        if d is None:
            print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} FAIL: {result}")
            continue
        keys = list(result.keys()) if isinstance(result, dict) else []
        gd = result.get("GLOBAL_DATA", {}).get("globalData", {}) if isinstance(result, dict) else {}
        ban = gd.get("bigBossBan", False)
        print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} keys={keys[:12]} ban={ban}")
        # Лучший = самый большой body с >2 keys
        if len(keys) > 2 and (best is None or mt["body_len"] > best["body_len"]):
            best = mt
            best["_result"] = result

    prod_data = {}
    if best and "_result" in best:
        prod_data = extract_product(best["_result"])
        print(f"\n  EXTRACTED: ban={prod_data.get('bigBossBan')} title={prod_data.get('title','')[:80]!r}")
        print(f"            price={prod_data.get('salePriceString')} cur={prod_data.get('currency')} imgs={prod_data.get('imageCount')}")

    # DOM fallback
    try:
        dom = page.evaluate("""
            () => {
                const og = {};
                document.querySelectorAll('meta[property^="og:"]').forEach(m => {
                    og[m.getAttribute('property')] = (m.content||'').substring(0,200);
                });
                const h1 = document.querySelector('h1');
                const prices = [];
                document.querySelectorAll('[class*="price"],[class*="Price"],[data-role*="price"]').forEach(el => {
                    const t = (el.textContent||'').trim();
                    if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
                });
                // JSON-LD
                let jsonld = [];
                document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                    try { jsonld.push(JSON.parse(s.textContent)); } catch(e) {}
                });
                return {
                    og: og,
                    h1: h1 ? h1.textContent.trim().substring(0,200) : null,
                    prices: [...new Set(prices)].slice(0,10),
                    jsonld: jsonld,
                    bodyLen: document.body ? document.body.innerHTML.length : 0,
                };
            }
        """)
        print(f"\n  DOM: og:title={dom.get('og',{}).get('og:title','')[:80]!r}")
        print(f"       og:image={dom.get('og',{}).get('og:image','')[:80]!r}")
        print(f"       h1={dom.get('h1','')[:60]!r}")
        print(f"       prices={dom.get('prices',[])}")
        print(f"       jsonld={len(dom.get('jsonld',[]))} items")
        prod_data["dom"] = dom
    except Exception as e:
        print(f"  DOM eval err: {e}")

    all_results[pid] = {
        "url": url,
        "mtop_count": len(mtops),
        "mtop_bodies": [{"idx": m["idx"], "len": m["body_len"], "status": m["status"]} for m in mtops],
        "best_mtop_idx": best["idx"] if best else None,
        "product": prod_data,
        "elapsed": round(elapsed, 1),
    }

    # Очистка listeners для следующего товара
    page.remove_listener("response", on_resp)
    page.wait_for_timeout(2000)

ctx.close()
pw.stop()

# ── Тест _parse_mtop с лучшим body ──
print(f"\n{'='*60}")
print("ТЕСТ _parse_mtop")
print(f"{'='*60}")

from r52.collectors.aliexpress import AliExpressCollector
collector = AliExpressCollector(data_dir=None, use_proxies=False)

for pid in PRODUCTS:
    r = all_results.get(pid, {})
    best_idx = r.get("best_mtop_idx")
    if best_idx is None:
        print(f"\n  {pid}: нет MTOP для _parse_mtop")
        continue

    # Перечитываем body из mtops (нужно сохранить)
    # Для простоты — гоняем через _parse_mtop с сохранённым body
    print(f"\n  {pid}: best MTOP #{best_idx}")

out = ROOT / "diag_ali_pw20_result.json"
out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")
print("DONE")
