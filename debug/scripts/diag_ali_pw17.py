"""PW17: Свежий профиль (без IT-cookies) + 2 товара. Проверяем bigBossBan и полноту данных."""
import json, re, sys, time, shutil
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

# Свежий профиль — без итальянских cookies
PROFILE_US = ROOT / "pw_ali_profile_us"
if PROFILE_US.exists():
    shutil.rmtree(PROFILE_US)
    print("Удалён старый pw_ali_profile_us")

PRODUCTS = [
    ("1005011813323763", "Товар 1 (тот же, что в pw13)"),
    ("1005006798847852", "Товар 2 (из pw12)"),
]

def capture_mtop(page):
    mtop_captured = []
    def on_resp(resp):
        try:
            u = resp.url.lower()
            if "acs.aliexpress.com" in u and "pdp.pc.query" in u:
                body = resp.text()
                mtop_captured.append({"url": resp.url, "status": resp.status, "body": body})
        except Exception:
            pass
    page.on("response", on_resp)
    return mtop_captured

def parse_mtop(body):
    """Возвращает (json_dict, result_dict) или (None, error_str)."""
    body_s = body.strip()
    m = re.match(r'mtopjsonp\d*\((.*)\)\s*;?\s*$', body_s, re.DOTALL)
    inner = m.group(1) if m else body_s
    try:
        d = json.loads(inner)
    except json.JSONDecodeError as e:
        return None, f"JSON error: {e}"
    data = d.get("data", {})
    result = data.get("result", data)
    return d, result

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    str(PROFILE_US),
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

# Прогрев: заходим на главную, ждём cookies
print("Прогрев: главная AliExpress...")
page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(5000)
cookies = ctx.cookies()
print(f"  Cookies после главной: {len(cookies)}")

results_summary = []

for pid, label in PRODUCTS:
    url = f"https://www.aliexpress.com/item/{pid}.html"
    print(f"\n{'='*60}")
    print(f"ТОVAR: {pid} ({label})")
    print(f"URL: {url}")
    print(f"{'='*60}")

    mtop_captured = capture_mtop(page)

    t0 = time.monotonic()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        print(f"  NAV OK")
    except Exception as e:
        print(f"  NAV ERROR: {e}")

    # Ждём MTOP (до 20 сек)
    for tick in range(8):
        page.wait_for_timeout(2500)
        if mtop_captured:
            break
    elapsed = time.monotonic() - t0
    print(f"  Time: {elapsed:.1f}s, MTOP: {len(mtop_captured)}")

    if not mtop_captured:
        print("  FATAL: MTOP не перехвачен")
        results_summary.append({"pid": pid, "label": label, "mtop": False})
        continue

    mb = mtop_captured[0]
    # Проверяем, какой locale ушёл в MTOP
    mt_url = mb["url"]
    lang_match = re.search(r'_lang%22%3A%22(\w+)', mt_url)
    cur_match = re.search(r'_currency%22%3A%22(\w+)', mt_url)
    cty_match = re.search(r'country%22%3A%22(\w+)', mt_url)
    print(f"  MTOP lang: {lang_match.group(1) if lang_match else '?'}, "
          f"currency: {cur_match.group(1) if cur_match else '?'}, "
          f"country: {cty_match.group(1) if cty_match else '?'}")
    print(f"  MTOP status: {mb['status']}, body_len: {len(mb['body'])}")

    d, result = parse_mtop(mb["body"])
    if d is None:
        print(f"  PARSE FAIL: {result}")
        results_summary.append({"pid": pid, "label": label, "mtop": False, "error": result})
        continue

    gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
    ban = gd.get("bigBossBan", False)
    err_code = gd.get("errorCode", "")
    print(f"  bigBossBan: {ban}")
    print(f"  errorCode: {err_code}")
    print(f"  banTip: {str(gd.get('bigBossBanTip',''))[:100]}")
    print(f"  result keys ({len(result)}): {list(result.keys())[:20]}")

    # Если не забанен — извлекаем данные
    if not ban:
        skm = result.get("SKUModule", {})
        tspi = result.get("targetSkuPriceInfo", {})
        hipc = result.get("HEADER_IMAGE_PC", {})
        seo = result.get("seoInfo", {})
        ci = hipc.get("currentSkuImages", [])

        print(f"\n  [SKUModule] title: {str(skm.get('title',''))[:120]}")
        print(f"  [PRICE] salePriceLocal: {tspi.get('salePriceLocal')}")
        print(f"  [PRICE] salePriceString: {tspi.get('salePriceString')}")
        print(f"  [PRICE] originalPrice: {tspi.get('originalPrice')}")
        print(f"  [PRICE] currency: {tspi.get('currency')}")
        print(f"  [IMAGES] count: {len(ci)}")
        for img in ci[:3]:
            print(f"    {str(img)[:90]}")
        print(f"  [SEO] title: {str(seo.get('title',''))[:120]}")

        # Тест _parse_mtop
        from r52.collectors.aliexpress import AliExpressCollector
        collector = AliExpressCollector(data_dir=None, use_proxies=False)
        obs = {"fields": {}, "price_candidates": [], "media": {"items": []}, "availability": "UNKNOWN", "category_path": None}
        try:
            parsed = collector._parse_mtop(mb["body"], obs)
            print(f"\n  _parse_mtop: {parsed}")
            print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:100]}")
            print(f"  prices: {len(obs['price_candidates'])}")
            for p in obs["price_candidates"][:3]:
                print(f"    {p}")
            print(f"  images: {len(obs['media']['items'])}")
            print(f"  availability: {obs['availability']}")
        except Exception as e:
            print(f"  _parse_mtop EXCEPTION: {e}")

        results_summary.append({
            "pid": pid, "label": label, "mtop": True, "ban": False,
            "title": str(skm.get("title", ""))[:80],
            "price": tspi.get("salePriceString"),
            "currency": tspi.get("currency"),
            "images": len(ci),
        })
    else:
        results_summary.append({"pid": pid, "label": label, "mtop": True, "ban": True, "errorCode": err_code})

    # Очистка для следующего товара
    page.wait_for_timeout(1000)

ctx.close()
pw.stop()

print(f"\n{'='*60}")
print("СВОДКА")
print(f"{'='*60}")
for r in results_summary:
    print(f"  {r}")

out = ROOT / "diag_ali_pw17_result.json"
out.write_text(json.dumps({"profile": "fresh_us", "products": results_summary}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out.name}")
print("DONE")
