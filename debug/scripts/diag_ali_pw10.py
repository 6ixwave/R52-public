"""diag_ali_pw10.py — AliExpress: recaptcha + direct MTOP + DOM.

Стратегия:
  1. Warm-up 8с + human-like actions.
  2. Product page → ждём MTOP (5с).
  3. Если FAIL_SYS_USER_VALIDATE → ищем recaptcha-iframe → кликаем.
  4. После recaptcha → ждём новый MTOP (12с).
  5. Если MTOP не пришёл → page.evaluate: прямой fetch к MTOP API.
  6. DOM-fallback: h1, price, images, availability.
  7. Сохраняем всё в JSON.
"""

import json
import re
import time
import sys

from playwright.sync_api import sync_playwright

PRODUCT_ID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
WARMUP_URL = "https://www.aliexpress.com/"
OUT_DIR = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12"

result = {
    "listing_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "phase": "",
    "mtop_responses": [],
    "mtop_chosen_len": 0,
    "mtop_success": False,
    "recaptcha_found": False,
    "recaptcha_solved": False,
    "direct_mtop_used": False,
    "title": "",
    "price": None,
    "currency": None,
    "original_price": None,
    "availability": None,
    "images": [],
    "category": None,
    "data_top_keys": [],
    "elapsed": 0,
    "errors": [],
}

all_mtop = []  # (url, status, body)


# ─── MTOP INTERCEPT ───
def on_response(resp):
    try:
        url = resp.url
        if "pdp.pc.query" not in url and "mtop.aliexpress.pdp" not in url:
            return
        # Skip report/health
        if "/report?" in url or "/health" in url:
            return
        body = resp.text()
        all_mtop.append((url, resp.status, body))
        print(f"  [MTOP] {resp.status} len={len(body)}", flush=True)
        print(f"    url={url[:130]}", flush=True)
        print(f"    head={body[:180]}", flush=True)
    except Exception as e:
        result["errors"].append(f"mtop_intercept: {e}")


def pick_best_mtop():
    """Лучший MTOP: >5000 байт, success=true, без validate."""
    best = None
    best_len = 0
    for url, status, body in all_mtop:
        if status != 200 or len(body) < 1000:
            continue
        data = None
        m = re.match(r'^\s*mtopjsonp\d+\s*\((\{.*\})\)\s*$', body, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        if not data:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                pass
        if not isinstance(data, dict):
            continue
        d = data.get("data", {})
        success = d.get("success", False) if isinstance(d, dict) else False
        ret = data.get("ret", [])
        has_validate = any("USER_VALIDATE" in r or "RGV587" in r for r in ret if isinstance(r, str))

        result["mtop_responses"].append({
            "len": len(body), "success": success, "has_validate": has_validate,
            "ret": [r for r in ret[:3] if isinstance(r, str)],
        })

        if has_validate:
            continue
        if success and len(body) > best_len:
            best_len = len(body)
            best = data
    return best, best_len


# ─── RECAPTCHA SOLVER ───
def try_solve_recaptcha(page):
    """Находит recaptcha-iframe и пытается решить."""
    print("  [RECAPTCHA] Looking for iframe...", flush=True)
    time.sleep(2)

    for frame in page.frames:
        furl = frame.url or ""
        if not any(x in furl for x in ("recaptcha", "tmd", "punish", "x5sec")):
            continue
        print(f"  [RECAPTCHA] Frame found: {furl[:100]}", flush=True)
        result["recaptcha_found"] = True

        try:
            # Try checkbox first
            for sel in (
                '#recaptcha-anchor',
                '.recaptcha-checkbox',
                'input[type="checkbox"]',
                '#sliderspace',
                '.nc_iconfont',
                '[class*="verify"]',
                '[class*="check"]',
                'button',
                '[class*="slider"]',
                '[class*="drag"]',
            ):
                el = frame.query_selector(sel)
                if el and el.is_visible():
                    print(f"  [RECAPTCHA] Clicking: {sel}", flush=True)
                    el.click()
                    result["recaptcha_solved"] = True
                    return True
        except Exception as e:
            print(f"  [RECAPTCHA] Click error: {e}", flush=True)

    # If no iframe, maybe recaptcha is in main page
    for sel in (
        '#recaptcha-anchor', '.recaptcha-checkbox', 'iframe[src*="recaptcha"]',
        '[class*="tmd"]', '[class*="captcha"]',
    ):
        el = page.query_selector(sel)
        if el and el.is_visible():
            print(f"  [RECAPTCHA] Main page element: {sel}", flush=True)
            el.click()
            result["recaptcha_solved"] = True
            return True

    print("  [RECAPTCHA] No element found", flush=True)
    return False


# ─── DIRECT MTOP via page.evaluate ───
def direct_mtop_call(page):
    """Вызывает MTOP API из контекста страницы (cookies уже есть)."""
    print("  [DIRECT-MTOP] Calling from page context...", flush=True)
    try:
        data = page.evaluate("""(productId) => {
            return new Promise((resolve) => {
                const t = Date.now();
                const url = 'https://acs.aliexpress.com/h5/mtop.aliexpress.pdp.pc.query/1.0/'
                    + '?jsv=2.5.1&appKey=12574478&t=' + t
                    + '&api=mtop.aliexpress.pdp.pc.query&v=1.0&type=originaljson'
                    + '&dataType=json&timeout=20000'
                    + '&data=' + encodeURIComponent(JSON.stringify({productId: productId}));
                fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}})
                    .then(r => r.text())
                    .then(txt => resolve(txt))
                    .catch(e => resolve(JSON.stringify({error: e.message})));
            });
        }""", PRODUCT_ID)
        if data and len(data) > 100:
            print(f"  [DIRECT-MTOP] Got {len(data)} bytes", flush=True)
            print(f"  [DIRECT-MTOP] head={data[:200]}", flush=True)
            # Parse
            m = re.match(r'^\s*mtopjsonp\d+\s*\((\{.*\})\)\s*$', data, re.DOTALL)
            if m:
                parsed = json.loads(m.group(1))
            else:
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    parsed = None
            if parsed:
                result["direct_mtop_used"] = True
                return parsed
    except Exception as e:
        result["errors"].append(f"direct_mtop: {e}")
        print(f"  [DIRECT-MTOP] Error: {e}", flush=True)
    return None


# ─── EXTRACT ───
def extract_fields(data):
    if not isinstance(data, dict):
        return
    d = data.get("data", data)
    if isinstance(d, dict):
        result["mtop_success"] = d.get("success", result["mtop_success"])
        d = d.get("result", d)
    if not isinstance(d, dict):
        result["errors"].append(f"result not dict: {type(d)}")
        return

    result["data_top_keys"] = list(d.keys())[:50]
    print(f"  result keys ({len(d)}): {list(d.keys())[:30]}", flush=True)

    for sec_name in ("GLOBAL_DATA", "GLOBAL_D", "globalData", "global", "GLOBAL",
                     "productData", "itemInfo", "item", "detailData", "product",
                     "skuInfo", "sku", "priceData", "tradeInfo", "ITEM"):
        sec = d.get(sec_name)
        if isinstance(sec, dict):
            print(f"  [{sec_name}] keys: {list(sec.keys())[:20]}", flush=True)
            _extract_sec(sec, sec_name)

    if not result["title"]:
        f = _deep_str(d, ("title", "subject", "productTitle", "seoTitle", "name", "h1"), 6)
        if f:
            result["title"] = f
            print(f"  [deep] title: {f[:80]}", flush=True)
    if not result["price"]:
        f = _deep_price(d, 6)
        if f:
            result["price"], result["currency"] = f
            print(f"  [deep] price: {f[0]} {f[1]}", flush=True)
    if not result["images"]:
        f = _deep_imgs(d, 6)
        if f:
            result["images"] = f
            print(f"  [deep] images: {len(f)}", flush=True)
    if not result["availability"]:
        f = _deep_stock(d, 6)
        if f:
            result["availability"] = f
            print(f"  [deep] avail: {f}", flush=True)


def _extract_sec(sec, label):
    if not result["title"]:
        for k in ("title", "subject", "productTitle", "seoTitle", "name", "h1"):
            v = sec.get(k)
            if isinstance(v, str) and len(v) > 3:
                result["title"] = v.strip()
                print(f"  [{label}] title: {v[:80]}", flush=True)
                break
    if not result["price"]:
        for k in ("price", "priceInfo", "tradePrice", "skuPriceList", "priceData",
                  "minPrice", "salePrice", "activityPrice", "formattedPrice"):
            v = sec.get(k)
            if v and _extract_price(v, label, k):
                break
    if not result["images"]:
        for k in ("images", "imageList", "gallery", "imgUrls", "skuImages",
                  "imageUrls", "picUrls", "mainImages"):
            v = sec.get(k)
            if isinstance(v, list) and v:
                imgs = _norm_imgs(v)
                if imgs:
                    result["images"] = imgs
                    print(f"  [{label}] images: {len(imgs)}", flush=True)
                    break
    if not result["availability"]:
        for k in ("stock", "stockInfo", "available", "inStock", "stockStatus", "inventory"):
            v = sec.get(k)
            if v is not None:
                a = _parse_stock(v)
                if a:
                    result["availability"] = a
                    print(f"  [{label}] avail: {a}", flush=True)
                    break


def _extract_price(val, label, key):
    if isinstance(val, (int, float)):
        result["price"] = f"{float(val):.2f}"
    elif isinstance(val, str):
        m = re.search(r'(\d[\d,]*\.?\d*)', val.replace(",", ""))
        if m:
            result["price"] = m.group(1)
    elif isinstance(val, dict):
        for ik in ("value", "amount", "price", "display", "formatted", "minPrice", "salePrice"):
            iv = val.get(ik)
            if iv:
                if isinstance(iv, (int, float)):
                    result["price"] = f"{float(iv):.2f}"
                elif isinstance(iv, str):
                    m = re.search(r'(\d[\d,]*\.?\d*)', iv.replace(",", ""))
                    if m:
                        result["price"] = m.group(1)
                break
        for ik in ("currency", "currencyCode", "currencySymbol"):
            iv = val.get(ik)
            if iv and isinstance(iv, str):
                result["currency"] = iv.upper()
                break
        for ik in ("originalPrice", "listPrice", "subPrice", "minOriginalPrice", "original"):
            iv = val.get(ik)
            if iv:
                if isinstance(iv, (int, float)):
                    result["original_price"] = f"{float(iv):.2f}"
                elif isinstance(iv, str):
                    m = re.search(r'(\d[\d,]*\.?\d*)', iv.replace(",", ""))
                    if m:
                        result["original_price"] = m.group(1)
                break
    if result["price"] and not result["currency"]:
        result["currency"] = "USD"
    if result["price"]:
        print(f"  [{label}.{key}] price={result['price']} {result.get('currency','')}", flush=True)
        return True
    return False


def _norm_imgs(imgs):
    out = []
    for img in imgs[:25]:
        if isinstance(img, str):
            u = img
        elif isinstance(img, dict):
            u = img.get("url") or img.get("imageUrl") or img.get("path") or img.get("src") or ""
        else:
            continue
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = "https://ae01.alicdn.com" + u
        if any(h in u for h in ("alicdn", "aliexpress-media", "aliexpress.com")):
            out.append(u)
    return out


def _parse_stock(v):
    if isinstance(v, (int, float)):
        return "IN_STOCK" if v > 0 else "OUT_OF_STOCK"
    if isinstance(v, bool):
        return "IN_STOCK" if v else "OUT_OF_STOCK"
    if isinstance(v, str):
        s = v.lower()
        if any(w in s for w in ("in stock", "available", "ready", "в наличии")):
            return "IN_STOCK"
        if any(w in s for w in ("out of stock", "unavailable", "sold out", "нет в наличии")):
            return "OUT_OF_STOCK"
        if "pre" in s and "order" in s:
            return "PRE_ORDER"
    if isinstance(v, dict):
        for k in ("status", "state", "type", "value"):
            if k in v:
                return _parse_stock(v[k])
    return None


def _deep_str(data, keys, depth):
    if depth <= 0 or not isinstance(data, dict):
        return None
    for k in keys:
        if k in data:
            v = data[k]
            if isinstance(v, str) and len(v) > 3:
                return v
    for v in data.values():
        if isinstance(v, dict):
            r = _deep_str(v, keys, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:8]:
                if isinstance(item, dict):
                    r = _deep_str(item, keys, depth - 1)
                    if r:
                        return r
    return None


def _deep_price(data, depth):
    if depth <= 0 or not isinstance(data, dict):
        return None
    for k in ("minPrice", "salePrice", "activityPrice", "price", "formattedPrice", "currentPrice"):
        v = data.get(k)
        if v:
            if isinstance(v, (int, float)):
                return (f"{float(v):.2f}", data.get("currency", "USD"))
            if isinstance(v, str):
                m = re.search(r'(\d[\d,]*\.?\d*)', v.replace(",", ""))
                if m:
                    return (m.group(1), data.get("currency", "USD"))
            if isinstance(v, dict):
                for ik in ("value", "amount", "price"):
                    iv = v.get(ik)
                    if iv:
                        if isinstance(iv, (int, float)):
                            return (f"{float(iv):.2f}", v.get("currency", "USD"))
                        if isinstance(iv, str):
                            m = re.search(r'(\d[\d,]*\.?\d*)', iv.replace(",", ""))
                            if m:
                                return (m.group(1), v.get("currency", "USD"))
    for v in data.values():
        if isinstance(v, dict):
            r = _deep_price(v, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    r = _deep_price(item, depth - 1)
                    if r:
                        return r
    return None


def _deep_imgs(data, depth):
    if depth <= 0 or not isinstance(data, dict):
        return None
    for k in ("images", "imageList", "gallery", "imgUrls", "skuImages", "imageUrls", "picUrls", "mainImages"):
        v = data.get(k)
        if isinstance(v, list) and v:
            imgs = _norm_imgs(v)
            if imgs:
                return imgs
    for v in data.values():
        if isinstance(v, dict):
            r = _deep_imgs(v, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    r = _deep_imgs(item, depth - 1)
                    if r:
                        return r
    return None


def _deep_stock(data, depth):
    if depth <= 0 or not isinstance(data, dict):
        return None
    for k in ("stock", "stockInfo", "available", "inStock", "stockStatus", "inventory"):
        v = data.get(k)
        if v is not None:
            a = _parse_stock(v)
            if a:
                return a
    for v in data.values():
        if isinstance(v, dict):
            r = _deep_stock(v, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    r = _deep_stock(item, depth - 1)
                    if r:
                        return r
    return None


# ─── MAIN ───
t0 = time.monotonic()

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        timezone_id="Asia/Vladivostok",
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en']});"
        "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        "window.chrome = { runtime: {} };"
        "Object.defineProperty(navigator,'connection',{get:()=>({downlink:10,rtt:50,effectiveType:'4g'})});"
    )
    page = ctx.new_page()
    page.set_default_timeout(30000)

    # ── 1. WARM-UP (8с) ──
    result["phase"] = "warmup"
    print("[1] WARM-UP (8s)", flush=True)
    page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    # Cookie consent
    try:
        for sel in ('button:has-text("Accept")', 'button:has-text("OK")',
                    '#onetrust-accept-btn-handler', '[class*="cookie"] button'):
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                print(f"  Cookie: {sel}", flush=True)
                page.wait_for_timeout(1500)
                break
    except Exception:
        pass

    # Human-like
    page.mouse.move(400, 300)
    page.wait_for_timeout(500)
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(800)
    page.mouse.wheel(0, -200)
    page.wait_for_timeout(500)
    page.mouse.move(800, 500)
    page.wait_for_timeout(3000)

    print(f"  OK title={page.title()[:60]}", flush=True)

    # ── 2. PRODUCT PAGE ──
    result["phase"] = "product"
    print("[2] PRODUCT", flush=True)
    page.on("response", on_response)
    resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  HTTP {resp.status if resp else 'N/A'}", flush=True)

    # Wait 6s for initial MTOP
    print("  WAIT 6s...", flush=True)
    page.wait_for_timeout(6000)

    # Check: got good MTOP?
    mtop_data, mtop_len = pick_best_mtop()
    if mtop_data:
        print(f"  [MTOP] Got good response: {mtop_len} bytes", flush=True)
    else:
        # ── 3. RECAPTCHA ──
        result["phase"] = "recaptcha"
        print("[3] RECAPTCHA PHASE", flush=True)
        # Wait a bit more for iframe to appear
        page.wait_for_timeout(3000)
        solved = try_solve_recaptcha(page)
        if solved:
            print("  Recaptcha clicked, waiting 12s for new MTOP...", flush=True)
            page.wait_for_timeout(12000)
            # Pick again
            mtop_data, mtop_len = pick_best_mtop()
            if mtop_data:
                print(f"  [MTOP] After recaptcha: {mtop_len} bytes", flush=True)

    # ── 4. DIRECT MTOP ──
    if not mtop_data:
        result["phase"] = "direct_mtop"
        print("[4] DIRECT MTOP CALL", flush=True)
        mtop_data = direct_mtop_call(page)
        if mtop_data:
            result["mtop_chosen_len"] = len(json.dumps(mtop_data))
            print(f"  Direct MTOP: {result['mtop_chosen_len']} bytes", flush=True)

    # ── 5. EXTRACT ──
    result["phase"] = "extract"
    print("[5] EXTRACT", flush=True)
    if mtop_data:
        extract_fields(mtop_data)

    # ── 6. DOM FALLBACK ──
    result["phase"] = "dom"
    print("[6] DOM FALLBACK", flush=True)

    # Title
    if not result["title"]:
        try:
            page.wait_for_selector("h1", timeout=10000)
            h1 = page.evaluate(
                "() => { const el = document.querySelector('h1'); return el ? el.textContent.trim() : ''; }"
            )
            if h1:
                result["title"] = h1
                print(f"  DOM title: {h1[:80]}", flush=True)
        except Exception as e:
            # Maybe h1 is in a different structure
            try:
                t = page.evaluate("""() => {
                    const els = document.querySelectorAll('[class*="title"],[class*="Title"],h1,h2');
                    for (const el of els) {
                        const t = el.textContent.trim();
                        if (t.length > 10 && t.length < 300) return t;
                    }
                    return '';
                }""")
                if t:
                    result["title"] = t
                    print(f"  DOM title (scan): {t[:80]}", flush=True)
            except Exception as e2:
                result["errors"].append(f"dom_title: {e2}")
        except Exception as e:
            result["errors"].append(f"dom_title: {e}")

    # Price
    if not result["price"]:
        try:
            pi = page.evaluate("""() => {
                const sels = [
                    '[class*="price-current"]', '[class*="PriceCurrent"]',
                    '[class*="pdp-price"]', '[data-role="price"]',
                    '[class*="sale-price"]', '[class*="SalePrice"]',
                    '[class*="price"] [class*="current"]',
                    '[class*="Price"] [class*="current"]',
                    '[class*="price-current"] span',
                ];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) {
                        const t = el.textContent.trim();
                        const m = t.match(/(\\d[\\d,]*\\.?\\d+)/);
                        if (m && parseFloat(m[1].replace(/,/g,'')) > 0.5)
                            return {val: m[1], text: t, sel: s};
                    }
                }
                // Scan all price-like elements
                const all = document.querySelectorAll('[class*="price"],[class*="Price"]');
                for (const el of all) {
                    const t = el.textContent.trim();
                    if (t.length < 40) {
                        const m = t.match(/(\\d[\\d,]*\\.?\\d+)/);
                        if (m && parseFloat(m[1].replace(/,/g,'')) > 0.5)
                            return {val: m[1], text: t, sel: el.className.substring(0,50)};
                    }
                }
                return null;
            }""")
            if pi:
                result["price"] = pi.get("val")
                print(f"  DOM price: {pi}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_price: {e}")

    # Images
    if not result["images"]:
        try:
            imgs = page.evaluate("""() => {
                const urls = [];
                const seen = new Set();
                document.querySelectorAll('img').forEach(img => {
                    const s = img.src || (img.dataset && img.dataset.src) || '';
                    if ((s.includes('alicdn') || s.includes('aliexpress-media')) && !seen.has(s)) {
                        seen.add(s);
                        urls.push(s);
                    }
                });
                return urls.slice(0, 20);
            }""")
            result["images"] = imgs or []
            print(f"  DOM images: {len(result['images'])}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_imgs: {e}")

    # Availability
    if not result["availability"]:
        try:
            av = page.evaluate("""() => {
                const txt = (document.body && document.body.innerText) || '';
                if (/out of stock|sold out|unavailable|нет в наличии/i.test(txt)) return 'OUT_OF_STOCK';
                if (/pre-?order|предзаказ/i.test(txt)) return 'PRE_ORDER';
                if (/in stock|available|ready to ship|add to cart|buy now|купить|добавить/i.test(txt)) return 'IN_STOCK';
                return null;
            }""")
            if av:
                result["availability"] = av
                print(f"  DOM avail: {av}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_avail: {e}")

    # ── 7. SUMMARY ──
    elapsed = time.monotonic() - t0
    result["elapsed"] = round(elapsed, 1)
    result["mtop_chosen_len"] = mtop_len or result.get("mtop_chosen_len", 0)

    print(f"\n[7] DONE {elapsed:.1f}s", flush=True)
    print(f"  phase      = {result['phase']}", flush=True)
    print(f"  title      = {result['title'][:80] if result['title'] else 'N/A'}", flush=True)
    print(f"  price      = {result['price']} {result.get('currency','')}", flush=True)
    print(f"  orig       = {result.get('original_price','')}", flush=True)
    print(f"  avail      = {result['availability']}", flush=True)
    print(f"  images     = {len(result['images'])}", flush=True)
    print(f"  mtop_len   = {result['mtop_chosen_len']}", flush=True)
    print(f"  mtop_resp  = {len(result['mtop_responses'])}", flush=True)
    print(f"  recaptcha  = found={result['recaptcha_found']} solved={result['recaptcha_solved']}", flush=True)
    print(f"  direct_mtop= {result['direct_mtop_used']}", flush=True)
    print(f"  errors     = {result['errors'][:5]}", flush=True)

    browser.close()

# ─── SAVE ───
mtop_data = pick_best_mtop()[0]  # re-pick for saving
if mtop_data:
    mtop_path = f"{OUT_DIR}\\diag_ali_pw10_mtop.json"
    with open(mtop_path, "w", encoding="utf-8") as f:
        json.dump(mtop_data, f, ensure_ascii=False, indent=2)
    print(f"\nMTOP saved: {mtop_path}", flush=True)

if all_mtop:
    cand_path = f"{OUT_DIR}\\diag_ali_pw10_all_mtop.json"
    with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(
            [{"url": u, "status": s, "len": len(b), "body_head": b[:5000]} for u, s, b in all_mtop],
            f, ensure_ascii=False, indent=2,
        )
    print(f"All MTOP saved: {cand_path}", flush=True)

out_path = f"{OUT_DIR}\\diag_ali_pw10_result.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Result saved: {out_path}", flush=True)
