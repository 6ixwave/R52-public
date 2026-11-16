"""PW23: Обход TMD-капчи + MTOP захват + fallback HTTP."""
import json, re, sys, time, random
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

PROFILE = ROOT / "pw_ali_profile"
PRODUCT_ID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"

print(f"Profile: {PROFILE}, exists={PROFILE.exists()}")
print(f"Target: {URL}")
print(f"Strategy: slow entry + TMD captcha bypass + MTOP capture + HTTP fallback")

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
    locale="en-US",
    viewport={"width": 1920, "height": 1080},
    timezone_id="America/New_York",
    ignore_https_errors=True,
)
ctx.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    window.chrome = {runtime: {}};
    // Маскируем headless
    const origQuery = window.navigator.userAgentData;
    if (origQuery) {
        Object.defineProperty(window.navigator, 'userAgentData', {
            get: () => ({...origQuery, mobile: false, platform: 'Windows'})
        });
    }
""")

page = ctx.pages[0] if ctx.pages else ctx.new_page()

# ── Ловим ВСЕ MTOP (both .us and .com) ──
mtops = []
def on_resp(resp):
    try:
        u = resp.url.lower()
        if ("acs.aliexpress" in u) and ("pdp" in u or "query" in u):
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

# ── Функция: human-like scroll/move ──
def human_scroll(page, n=3):
    for _ in range(n):
        y = random.randint(100, 500)
        page.mouse.wheel(0, y)
        page.wait_for_timeout(random.randint(300, 800))

def human_move(page):
    x = random.randint(200, 1600)
    y = random.randint(200, 800)
    page.mouse.move(x, y, steps=random.randint(5, 15))
    page.wait_for_timeout(random.randint(100, 300))

# ── Функция: детект и обход TMD ──
def detect_and_solve_tmd(page, max_attempts=3):
    """Возвращает True если TMD пройдена или её не было."""
    for attempt in range(max_attempts):
        try:
            cur_url = page.url
            if "_____tmd_____" not in cur_url and "punish" not in cur_url:
                print(f"  [TMD] No TMD page (attempt {attempt+1})")
                return True
        except Exception:
            pass

        print(f"  [TMD] Detected TMD page (attempt {attempt+1}): {page.url[:100]}")
        page.wait_for_timeout(3000)

        # Стратегия 1: ищем кнопку "Verify" / "Continue" / "Click here"
        btn_selectors = [
            'button:has-text("Verify")',
            'button:has-text("verify")',
            'button:has-text("Continue")',
            'button:has-text("continue")',
            'button:has-text("Click here")',
            'button:has-text("I am not a robot")',
            'button:has-text("Get Started")',
            'button:has-text("Start")',
            'a:has-text("Verify")',
            'a:has-text("Continue")',
            'div[role="button"]:has-text("Verify")',
            '#nc_1_wrapper',
            '.nc_iconfont',
            '.btn-verify',
            '.tmd-btn',
            '[class*="verify"]',
            '[class*="captcha"] button',
            '[class*="slider"]',
        ]
        clicked = False
        for sel in btn_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    txt = (btn.text_content() or "").strip()
                    print(f"  [TMD] Found button: {sel} -> {txt!r}")
                    btn.click()
                    page.wait_for_timeout(3000)
                    clicked = True
                    break
            except Exception:
                pass

        if clicked:
            # Ждём редирект
            for w in range(8):
                page.wait_for_timeout(2500)
                try:
                    if "_____tmd_____" not in page.url:
                        print(f"  [TMD] Redirected after button click!")
                        return True
                except Exception:
                    pass
            print(f"  [TMD] Still on TMD after button click")
            continue

        # Стратегия 2: Slider captcha (drag)
        print(f"  [TMD] Trying slider drag...")
        try:
            slider = page.locator('[class*="slider"], [class*="drag"], [class*="nc_iconfont"], #nc_1_n1z, .btn_slide, .slide-btn').first
            if slider.is_visible(timeout=2000):
                box = slider.bounding_box()
                if box:
                    sx = box["x"] + box["width"] / 2
                    sy = box["y"] + box["height"] / 2
                    # Drag to the right
                    page.mouse.move(sx, sy)
                    page.wait_for_timeout(200)
                    page.mouse.down()
                    # Move in steps to look human
                    for step in range(20):
                        page.mouse.move(sx + (step + 1) * 15, sy + random.randint(-3, 3))
                        page.wait_for_timeout(random.randint(20, 50))
                    page.mouse.up()
                    page.wait_for_timeout(3000)
                    print(f"  [TMD] Slider dragged")
                    # Check if passed
                    for w in range(6):
                        page.wait_for_timeout(2000)
                        if "_____tmd_____" not in page.url:
                            print(f"  [TMD] Passed after slider!")
                            return True
        except Exception as e:
            print(f"  [TMD] Slider attempt failed: {e}")

        # Стратегия 3: reCAPTCHA iframe
        print(f"  [TMD] Checking for reCAPTCHA iframe...")
        try:
            frames = page.frames
            for fr in frames:
                if "recaptcha" in (fr.url or "").lower():
                    print(f"  [TMD] reCAPTCHA frame: {fr.url[:80]}")
                    # Try clicking the checkbox
                    try:
                        cb = fr.locator('input[type="checkbox"], .recaptcha-checkbox, #recaptcha-anchor').first
                        if cb.is_visible(timeout=2000):
                            cb.click()
                            page.wait_for_timeout(5000)
                            print(f"  [TMD] reCAPTCHA checkbox clicked")
                    except Exception:
                        pass
                    break
        except Exception as e:
            print(f"  [TMD] reCAPTCHA check: {e}")

        # Стратегия 4: Просто подождать и refresh
        print(f"  [TMD] Waiting + refresh...")
        page.wait_for_timeout(5000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
        except Exception:
            pass

    # Финальная проверка
    try:
        return "_____tmd_____" not in page.url
    except Exception:
        return False

# ═══════════════════════════════════════════
# ШАГ 1: Прогрев на главной
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ШАГ 1: Прогрев на главной")
print(f"{'='*60}")

page.wait_for_timeout(3000)
try:
    page.goto("https://www.aliexpress.com/", wait_until="domcontentloaded", timeout=30000)
    print(f"  Home NAV OK")
except Exception as e:
    print(f"  Home NAV ERR: {e}")

# Ждём + human behavior
for tick in range(4):
    page.wait_for_timeout(2500)
    human_move(page)
    human_scroll(page, 2)

page.wait_for_timeout(3000)
try:
    st = page.evaluate("() => ({url: window.location.href, bodyLen: document.body?.innerHTML?.length||0})")
    print(f"  Home: url={st['url'][:80]} body={st['bodyLen']}")
except Exception:
    pass

# ═══════════════════════════════════════════
# ШАГ 2: Навигация на товар
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print(f"ШАГ 2: Навигация на {URL}")
print(f"{'='*60}")

page.wait_for_timeout(2000)
t0 = time.monotonic()
try:
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  NAV OK ({time.monotonic()-t0:.1f}s)")
except Exception as e:
    print(f"  NAV ERR: {e}")

# Ждём + human behavior
for tick in range(6):
    page.wait_for_timeout(2500)
    human_move(page)
    if tick % 2 == 0:
        human_scroll(page, 1)

# ═══════════════════════════════════════════
# ШАГ 3: Обход TMD
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ШАГ 3: Проверка/обход TMD")
print(f"{'='*60}")

tmd_passed = detect_and_solve_tmd(page)
print(f"  TMD passed: {tmd_passed}")

# Если TMD прошла — ждём рендер + MTOP
if tmd_passed:
    for tick in range(8):
        page.wait_for_timeout(2500)
        if tick in (2, 5):
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
        if len(mtops) >= 1 and tick > 3:
            page.wait_for_timeout(2000)
            break

# ═══════════════════════════════════════════
# ШАГ 4: Анализ MTOP
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print(f"ШАГ 4: Анализ MTOP ({len(mtops)} captured)")
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
# ШАГ 5: Fallback — прямой HTTP к MTOP
# ═══════════════════════════════════════════
if not best or "_result" not in best:
    print(f"\n{'='*60}")
    print("ШАГ 5: Fallback — HTTP MTOP с cookies")
    print(f"{'='*60}")

    try:
        import urllib.request
        import urllib.parse

        # Берём cookies из контекста
        cookies = ctx.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        print(f"  Cookies: {len(cookies)}")

        # MTOP endpoint (US)
        mtop_url = (
            f"https://acs.aliexpress.us/h5/mtop.aliexpress.pdp.pc.query/1.0/"
            f"?jsv=2.5.1&appKey=12574478"
            f"&t={int(time.time()*1000)}"
            f"&api=mtop.aliexpress.pdp.pc.query"
            f"&type=originaljsonp&v=1.0&timeout=15000"
            f"&dataType=originaljsonp&callback=mtopjsonp1"
        )
        data_params = {
            "productId": PRODUCT_ID,
            "_lang": "en_US",
            "_currency": "USD",
            "country": "US",
            "pr": "PC",
        }
        mtop_url += "&data=" + urllib.parse.quote(json.dumps(data_params))

        req = urllib.request.Request(mtop_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "Referer": f"https://www.aliexpress.us/item/{PRODUCT_ID}.html",
            "Accept": "*/*",
        })

        print(f"  GET: {mtop_url[:120]}...")
        resp = urllib.request.urlopen(req, timeout=20)
        body = resp.read().decode("utf-8", errors="replace")
        print(f"  HTTP {resp.status}, len={len(body)}")
        print(f"  body[:150]: {body[:150]!r}")

        d, result = parse_mtop(body)
        if d:
            keys = list(result.keys()) if isinstance(result, dict) else []
            print(f"  result keys: {keys[:15]}")
            best = {"idx": 99, "url": "HTTP-fallback", "status": resp.status, "body_len": len(body), "body": body, "_result": result, "_json": d}
        else:
            print(f"  HTTP MTOP parse fail: {result}")
    except Exception as e:
        print(f"  HTTP fallback error: {type(e).__name__}: {e}")

# ═══════════════════════════════════════════
# ШАГ 6: Извлечение + _parse_mtop
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("ШАГ 6: Извлечение + _parse_mtop")
print(f"{'='*60}")

if best and "_result" in best:
    result = best["_result"]

    gd = result.get("GLOBAL_DATA", {}).get("globalData", {})
    print(f"  bigBossBan: {gd.get('bigBossBan')}")
    print(f"  errorCode: {gd.get('errorCode')}")

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
    except Exception as e:
        print(f"  _parse_mtop ERR: {e}")
        import traceback; traceback.print_exc()
else:
    print("  Нет данных для извлечения")

# ── Сохраняем ──
out = ROOT / "diag_ali_pw23_result.json"
save = {
    "product_id": PRODUCT_ID,
    "url": URL,
    "final_url": page.url if 'page' in dir() else "",
    "tmd_passed": tmd_passed,
    "mtop_count": len(mtops),
    "best_source": best.get("url", "none")[:100] if best else "none",
    "best_len": best.get("body_len", 0) if best else 0,
    "elapsed": round(time.monotonic() - t0, 1),
}
out.write_text(json.dumps(save, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved: {out.name} ({out.stat().st_size} bytes)")

ctx.close()
pw.stop()
print("\nDONE")
