"""PW24: Cooldown + IT-поддомен + улучшенный TMD/reCAPTCHA + MTOP."""
import json, re, sys, time, random
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

PROFILE = ROOT / "pw_ali_profile"
PRODUCT_ID = "1005011813323763"
# IT-поддомен (меньше TMD, в PW13 работал)
URL = f"https://it.aliexpress.com/item/{PRODUCT_ID}.html"

print(f"Profile: {PROFILE}, exists={PROFILE.exists()}")
print(f"Target: {URL}")
print(f"Strategy: 40s cooldown -> IT subdomain -> slow entry -> TMD/reCAPTCHA -> MTOP")

# ── COOLDOWN ──
print("\n[COOLDOWN] 40 сек до старта (TMD reset)...")
time.sleep(40)
print("[COOLDOWN] Done.")

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    str(PROFILE),
    headless=True,
    args=[
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-features=IsolateOrigins,site-per-process",
    ],
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    locale="it-IT",
    viewport={"width": 1920, "height": 1080},
    timezone_id="Europe/Rome",
    ignore_https_errors=True,
)
ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['it-IT', 'it', 'en-US', 'en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    window.chrome = {runtime: {}};
""")

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# ── Ловим ВСЕ MTOP (both .us and .com) ──
mtops = []
def on_resp(resp):
    try:
        u = resp.url.lower()
        if "acs.aliexpress" in u and ("pdp" in u or "query" in u):
            body = resp.text()
            mtops.append({
                "idx": len(mtops),
                "url": resp.url,
                "status": resp.status,
                "body_len": len(body),
                "body": body,
            })
            print(f"  [MTOP #{len(mtops)-1}] status={resp.status} len={len(body)}")
            print(f"    {resp.url[:130]}")
    except Exception as e:
        print(f"  [MTOP] err: {e}")

page.on("response", on_resp)

# ── Human behavior helpers ──
def human_scroll(n=2):
    for _ in range(n):
        page.mouse.wheel(0, random.randint(100, 400))
        page.wait_for_timeout(random.randint(300, 700))

def human_move():
    page.mouse.move(random.randint(200, 1600), random.randint(200, 700), steps=random.randint(5, 12))
    page.wait_for_timeout(random.randint(100, 300))

# ── TMD / reCAPTCHA solver ──
def solve_tmd(max_attempts=3):
    """Возвращает True если TMD пройдена или её нет."""
    for attempt in range(max_attempts):
        try:
            cur = page.url
        except Exception:
            return True
        if "_____tmd_____" not in cur and "punish" not in cur:
            print(f"  [TMD] No TMD (attempt {attempt+1})")
            return True

        print(f"  [TMD] Page detected (attempt {attempt+1}): {cur[:100]}")
        page.wait_for_timeout(4000)

        # 1) Кнопка Verify / Click / Continue
        btn_sels = [
            'button:has-text("Verify")', 'button:has-text("verify")',
            'button:has-text("Continue")', 'button:has-text("continue")',
            'button:has-text("Click here")', 'button:has-text("Get Started")',
            'a:has-text("Verify")', 'a:has-text("Continue")',
            '[class*="verify"]', '[class*="tmd-btn"]',
        ]
        clicked = False
        for sel in btn_sels:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    txt = (btn.text_content() or "").strip()
                    print(f"  [TMD] Button: {sel} -> {txt!r}")
                    btn.click()
                    page.wait_for_timeout(4000)
                    clicked = True
                    break
            except Exception:
                pass

        if clicked:
            for w in range(8):
                page.wait_for_timeout(2500)
                try:
                    if "_____tmd_____" not in page.url:
                        print(f"  [TMD] Passed after button!")
                        return True
                except Exception:
                    pass
            continue

        # 2) reCAPTCHA iframe — НАЙТИ ЧЕКБОКС ВНУТРИ
        print(f"  [TMD] Looking for reCAPTCHA iframe...")
        solved = False
        for fr in page.frames:
            fr_url = (fr.url or "").lower()
            if "recaptcha" in fr_url or "google.com/recaptcha" in fr_url:
                print(f"  [TMD] Frame: {fr.url[:100]}")
                try:
                    # Стандартный reCAPTCHA v2 checkbox
                    cb_sels = [
                        '#recaptcha-anchor',
                        'input[type="checkbox"]',
                        '.recaptcha-checkbox',
                        '#slsrsp',
                        'span[role="checkbox"]',
                        'div[aria-label="recaptcha"]',
                    ]
                    for csel in cb_sels:
                        try:
                            cb = fr.locator(csel).first
                            if cb.is_visible(timeout=2000):
                                print(f"  [TMD] reCAPTCHA checkbox: {csel}")
                                cb.click()
                                page.wait_for_timeout(6000)
                                solved = True
                                break
                        except Exception:
                            pass
                    # Fallback: клик по центру iframe
                    if not solved:
                        try:
                            box = fr.locator("body").bounding_box()
                            if box:
                                cx = box["x"] + 30
                                cy = box["y"] + box["height"] / 2
                                page.mouse.click(cx, cy)
                                print(f"  [TMD] Clicked iframe center ({cx},{cy})")
                                page.wait_for_timeout(6000)
                                solved = True
                        except Exception:
                            pass
                except Exception as e:
                    print(f"  [TMD] Frame solve err: {e}")
                break

        if solved:
            for w in range(8):
                page.wait_for_timeout(2500)
                try:
                    if "_____tmd_____" not in page.url:
                        print(f"  [TMD] Passed after reCAPTCHA!")
                        return True
                except Exception:
                    pass

        # 3) Slider
        print(f"  [TMD] Trying slider...")
        try:
            slider = page.locator('[class*="slider"], [class*="drag"], #nc_1_n1z, .btn_slide, .slide-btn, [class*="nc_iconfont"]').first
            if slider.is_visible(timeout=2000):
                box = slider.bounding_box()
                if box:
                    sx = box["x"] + box["width"] / 2
                    sy = box["y"] + box["height"] / 2
                    page.mouse.move(sx, sy)
                    page.wait_for_timeout(300)
                    page.mouse.down()
                    for step in range(25):
                        page.mouse.move(sx + (step + 1) * 12, sy + random.randint(-2, 2))
                        page.wait_for_timeout(random.randint(15, 40))
                    page.mouse.up()
                    page.wait_for_timeout(4000)
                    print(f"  [TMD] Slider dragged")
                    for w in range(6):
                        page.wait_for_timeout(2000)
                        if "_____tmd_____" not in page.url:
                            print(f"  [TMD] Passed after slider!")
                            return True
        except Exception as e:
            print(f"  [TMD] Slider err: {e}")

        # 4) Refresh
        print(f"  [TMD] Refresh...")
        page.wait_for_timeout(5000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
        except Exception:
            pass

    try:
        return "_____tmd_____" not in page.url
    except Exception:
        return False

# ═══════════════════════════════════════════
# ШАГ 1: Прогрев (IT-главная)
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ШАГ 1: Прогрев it.aliexpress.com")
print(f"{'='*60}")

page.wait_for_timeout(2000)
try:
    page.goto("https://it.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
    print(f"  Home OK")
except Exception as e:
    print(f"  Home ERR: {e}")

for tick in range(5):
    page.wait_for_timeout(2000)
    human_move()
    if tick % 2 == 0:
        human_scroll(2)

page.wait_for_timeout(2000)
try:
    st = page.evaluate("() => ({url: window.location.href, bodyLen: document.body?.innerHTML?.length||0})")
    print(f"  Home: url={st['url'][:80]} body={st['bodyLen']}")
except Exception:
    pass

# ═══════════════════════════════════════════
# ШАГ 2: Навигация на товар
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print(f"ШАГ 2: {URL}")
print(f"{'='*60}")

page.wait_for_timeout(3000)
t0 = time.monotonic()
try:
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"  NAV ERR: {e}")

# Ждём рендер + human behavior
for tick in range(8):
    page.wait_for_timeout(2500)
    human_move()
    if tick % 2 == 0:
        human_scroll(1)
    if tick in (3, 6):
        try:
            st = page.evaluate("""
                () => ({
                    h1: document.querySelector('h1')?.textContent?.trim()?.substring(0,100)||null,
                    bodyLen: document.body?.innerHTML?.length||0,
                    url: window.location.href,
                })
            """)
            print(f"  t={tick*2.5:.0f}s: body={st['bodyLen']} h1={st['h1']!r} url={st['url'][:80]}")
        except Exception:
            pass

# ═══════════════════════════════════════════
# ШАГ 3: TMD check
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ШАГ 3: TMD check")
print(f"{'='*60}")

tmd_ok = solve_tmd(max_attempts=3)
print(f"  TMD passed: {tmd_ok}")

if tmd_ok:
    # После TMD — ждём MTOP
    for tick in range(8):
        page.wait_for_timeout(2500)
        if len(mtops) >= 1 and tick > 3:
            page.wait_for_timeout(2000)
            break

# ═══════════════════════════════════════════
# ШАГ 4: Анализ MTOP
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print(f"ШАГ 4: MTOP analysis ({len(mtops)} captured)")
print(f"{'='*60}")

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
        print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} FAIL: {result}")
        continue
    keys = list(result.keys()) if isinstance(result, dict) else []
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {}) if isinstance(result, dict) else {}
    ban = gd.get("bigBossBan", False)
    print(f"  [MTOP #{mt['idx']}] len={mt['body_len']} keys={keys[:12]} ban={ban}")
    if len(keys) > 2 and (best is None or mt["body_len"] > best["body_len"]):
        best = mt
        best["_result"] = result
        best["_json"] = d

# ═══════════════════════════════════════════
# ШАГ 5: Извлечение + _parse_mtop
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ШАГ 5: Extract + _parse_mtop")
print(f"{'='*60}")

parse_result = {}
if best and "_result" in best:
    result = best["_result"]
    gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
    print(f"  bigBossBan: {gd.get('bigBossBan')}")
    print(f"  errorCode: {gd.get('errorCode')}")
    print(f"  banTip: {str(gd.get('bigBossBanTip',''))[:120]}")

    skm = result.get("SKUModule", {})
    print(f"  SKUModule keys: {list(skm.keys())[:15]}")
    print(f"  title: {str(skm.get('title',''))[:200]}")

    tspi = result.get("targetSkuPriceInfo", {})
    print(f"  Price: {tspi.get('salePriceString')} {tspi.get('currency')}")
    print(f"  Original: {tspi.get('originalPrice')}")

    hipc = result.get("HEADER_IMAGE_PC", {})
    ci = hipc.get("currentSkuImages", [])
    print(f"  Images: {len(ci)}")
    for img in ci[:3]:
        print(f"    {str(img)[:100]}")

    seo = result.get("seoInfo", {})
    print(f"  SEO: {str(seo.get('title',''))[:150]}")

    # _parse_mtop
    from r52.collectors.aliexpress import AliExpressCollector
    collector = AliExpressCollector(data_dir=None, use_proxies=False)
    obs = {"fields": {}, "price_candidates": [], "media": {"items": []}, "availability": "UNKNOWN", "category_path": None}
    try:
        parsed = collector._parse_mtop(best["body"], obs)
        print(f"\n  _parse_mtop: {parsed}")
        print(f"  title: {obs['fields'].get('title', {}).get('value', 'N/A')[:150]}")
        print(f"  prices: {len(obs['price_candidates'])}")
        for p in obs["price_candidates"][:5]:
            print(f"    {p}")
        print(f"  images: {len(obs['media']['items'])}")
        print(f"  availability: {obs['availability']}")
        print(f"  category: {obs['category_path']}")
        parse_result = {
            "returned": parsed,
            "title": obs["fields"].get("title", {}).get("value", ""),
            "prices": obs["price_candidates"][:5],
            "images": len(obs["media"]["items"]),
            "availability": obs["availability"],
            "category": obs["category_path"],
        }
    except Exception as e:
        print(f"  _parse_mtop ERR: {e}")
        import traceback; traceback.print_exc()

# ── DOM fallback ──
print(f"\n--- DOM ---")
try:
    dom = page.evaluate("""
        () => {
            const og = {};
            document.querySelectorAll('meta[property^="og:"]').forEach(m => {
                og[m.getAttribute('property')] = (m.content||'').substring(0,300);
            });
            const h1 = document.querySelector('h1');
            const prices = [];
            document.querySelectorAll('[class*="price"],[class*="Price"]').forEach(el => {
                const t = (el.textContent||'').trim();
                if (t && t.length < 80 && /\\d/.test(t)) prices.push(t);
            });
            return {
                og: og,
                h1: h1?.textContent?.trim()?.substring(0,200)||null,
                prices: [...new Set(prices)].slice(0,10),
                bodyLen: document.body?.innerHTML?.length||0,
                url: window.location.href,
            };
        }
    """)
    print(f"  og:title = {dom.get('og',{}).get('og:title','')[:100]!r}")
    print(f"  og:image = {dom.get('og',{}).get('og:image','')[:100]!r}")
    print(f"  h1 = {dom.get('h1','')[:80]!r}")
    print(f"  prices = {dom.get('prices',[])}")
    print(f"  body = {dom.get('bodyLen',0)}")
except Exception as e:
    print(f"  DOM err: {e}")
    dom = {}

# ── Сохраняем ──
elapsed = time.monotonic() - t0
out = ROOT / "diag_ali_pw24_result.json"
save = {
    "product_id": PRODUCT_ID,
    "url": URL,
    "final_url": page.url if 'page' in dir() else "",
    "tmd_passed": tmd_ok,
    "mtop_count": len(mtops),
    "best_source": best.get("url", "none")[:120] if best else "none",
    "best_len": best.get("body_len", 0) if best else 0,
    "result_keys": list(best["_result"].keys())[:20] if best and "_result" in best else [],
    "parse_mtop": parse_result,
    "dom": dom,
    "elapsed": round(elapsed, 1),
}
out.write_text(json.dumps(save, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")

ctx.close()
pw.stop()
print("\nDONE")
