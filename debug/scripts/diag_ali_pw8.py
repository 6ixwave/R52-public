"""diag_ali_pw8.py — AliExpress PDP-MTOP: надёжное извлечение полей товара.

Улучшения vs pw7:
  - Длинный warm-up (10с) + проверка cookie.
  - Точный фильтр MTOP URL (только pdp.pc.query, не bx/punish).
  - Собираем ВСЕ MTOP-ответы, выбираем лучший (крупнейший JSON с success=true).
  - При FAIL_SYS_USER_VALIDATE — перезагрузка страницы (до 3 попыток).
  - DOM-fallback для title/price/images.
  - Сохранение полного MTOP JSON + summary.
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
MAX_ATTEMPTS = 3
WAIT_MS = 20000  # 20 сек ожидания MTOP

result = {
    "listing_id": PRODUCT_ID,
    "url": PRODUCT_URL,
    "attempts": 0,
    "mtop_candidates": [],
    "mtop_chosen_len": 0,
    "mtop_success": False,
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
    "cookies_after_warmup": [],
}

mtop_candidates = []  # list of (url, status, body_str)


def is_pdp_mtop(url: str) -> bool:
    """Только основной PDP query, не bx/punish/health."""
    if "pdp.pc.query" not in url and "mtop.aliexpress.pdp" not in url:
        return False
    # Исключаем punish / tmd / health
    if any(x in url for x in ("punish", "_____tmd_____", "health", "/bx/")):
        return False
    return True


def on_response(resp):
    """Intercept MTOP PDP responses."""
    global mtop_candidates
    try:
        if not is_pdp_mtop(resp.url):
            return
        body = resp.text()
        mtop_candidates.append((resp.url, resp.status, body))
        print(f"  [MTOP] {resp.status} len={len(body)} url={resp.url[:100]}", flush=True)
        print(f"    First 200: {body[:200]}", flush=True)
    except Exception as e:
        result["errors"].append(f"mtop_intercept: {e}")


def pick_best_mtop(candidates):
    """Выбирает лучший MTOP-ответ: крупнейший JSON с success=true."""
    best = None
    best_score = -1
    for url, status, body in candidates:
        if status != 200 or len(body) < 100:
            continue
        # Parse JSONP
        data = None
        m = re.match(r'^\s*mtopjsonp\d+\s*\((\{.*\})\)\s*$', body, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
        else:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue

        if not isinstance(data, dict):
            continue

        d = data.get("data", {})
        success = d.get("success", False) if isinstance(d, dict) else False
        ret = data.get("ret", [])
        has_validate = any("USER_VALIDATE" in r or "RGV587" in r for r in ret if isinstance(r, str))

        score = len(body)
        if success:
            score += 10000
        if has_validate:
            score -= 5000

        result["mtop_candidates"].append({
            "len": len(body),
            "success": success,
            "has_validate": has_validate,
            "ret": ret[:3],
            "top_keys": list(d.keys())[:15] if isinstance(d, dict) else [],
        })

        if score > best_score:
            best_score = score
            best = data

    return best


def extract_fields(data):
    """Walk MTOP data, fill result."""
    if not isinstance(data, dict):
        return

    d = data.get("data", data)
    if isinstance(d, dict):
        result["mtop_success"] = d.get("success", result["mtop_success"])
        d = d.get("result", d)

    if not isinstance(d, dict):
        result["errors"].append(f"mtop result not dict: {type(d)}")
        return

    result["data_top_keys"] = list(d.keys())[:40]
    print(f"  result keys ({len(d)}): {list(d.keys())[:25]}", flush=True)

    # Walk known sections
    for section_name in ("GLOBAL_DATA", "GLOBAL_D", "globalData", "global", "GLOBAL",
                         "productData", "itemInfo", "item", "detailData", "product", "ITEM",
                         "skuInfo", "sku", "SKU", "priceData", "tradeInfo"):
        sec = d.get(section_name)
        if isinstance(sec, dict):
            print(f"  [{section_name}] keys: {list(sec.keys())[:20]}", flush=True)
            _extract_section(sec, section_name)

    # Deep fallbacks
    if not result["title"]:
        found = _deep_str(d, ("title", "subject", "productTitle", "seoTitle", "name", "h1"), 6)
        if found:
            result["title"] = found
            print(f"  [deep] title: {found[:80]}", flush=True)

    if not result["price"]:
        found = _deep_price(d, 6)
        if found:
            result["price"], result["currency"] = found
            print(f"  [deep] price: {found[0]} {found[1]}", flush=True)

    if not result["images"]:
        found = _deep_imgs(d, 6)
        if found:
            result["images"] = found
            print(f"  [deep] images: {len(found)}", flush=True)

    if not result["availability"]:
        found = _deep_stock(d, 6)
        if found:
            result["availability"] = found
            print(f"  [deep] avail: {found}", flush=True)


def _extract_section(sec, label):
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
            if v:
                if _extract_price(v, label, k):
                    break

    if not result["images"]:
        for k in ("images", "imageList", "gallery", "imgUrls", "skuImages", "imageUrls", "picUrls"):
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
        # Original price
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

    # Currency from parent context
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
    for k in ("images", "imageList", "gallery", "imgUrls", "skuImages", "imageUrls", "picUrls"):
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
    )
    page = ctx.new_page()

    # 1. WARM-UP (longer)
    print("[1] WARM-UP (10s)", flush=True)
    page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(10000)
    print(f"  OK title={page.title()[:60]}", flush=True)

    # Check cookies
    cookies = ctx.cookies()
    result["cookies_after_warmup"] = [
        {"name": c["name"], "domain": c["domain"], "path": c["path"]}
        for c in cookies
    ]
    print(f"  Cookies: {len(cookies)}", flush=True)
    for c in cookies[:10]:
        print(f"    {c['name']} @ {c['domain']}", flush=True)

    # 2. PRODUCT PAGE — multiple attempts
    mtop_data = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result["attempts"] = attempt
        mtop_candidates.clear()
        print(f"\n[2.{attempt}] PRODUCT (attempt {attempt}/{MAX_ATTEMPTS})", flush=True)

        page.on("response", on_response)
        resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"  HTTP {resp.status if resp else 'N/A'}", flush=True)

        print(f"  WAIT {WAIT_MS//1000}s...", flush=True)
        page.wait_for_timeout(WAIT_MS)

        # Pick best
        best = pick_best_mtop(mtop_candidates)
        if best:
            mtop_data = best
            result["mtop_chosen_len"] = max(
                (c[2] and len(c[2]) or 0) for c in mtop_candidates
            ) if mtop_candidates else 0
            print(f"  BEST MTOP: success={result['mtop_success']}", flush=True)
            break
        else:
            print(f"  No good MTOP, retrying...", flush=True)
            page.wait_for_timeout(3000)

    # 3. EXTRACT
    print("\n[3] EXTRACT", flush=True)
    if mtop_data:
        extract_fields(mtop_data)

    # 4. DOM FALLBACK
    print("\n[4] DOM FALLBACK", flush=True)
    if not result["title"]:
        try:
            h1 = page.evaluate(
                "() => { const el = document.querySelector('h1'); return el ? el.textContent.trim() : ''; }"
            )
            if h1:
                result["title"] = h1
                print(f"  DOM title: {h1[:80]}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_title: {e}")

    if not result["images"]:
        try:
            imgs = page.evaluate("""() => {
                const urls = [];
                document.querySelectorAll('img').forEach(img => {
                    const s = img.src || (img.dataset && img.dataset.src) || '';
                    if (s.includes('alicdn') || s.includes('aliexpress-media')) urls.push(s);
                });
                return urls.slice(0, 20);
            }""")
            result["images"] = imgs or []
            print(f"  DOM images: {len(result['images'])}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_imgs: {e}")

    if not result["price"]:
        try:
            pi = page.evaluate("""() => {
                const sels = [
                    '[class*="price"] [class*="current"]',
                    '[class*="Price"] [class*="current"]',
                    '.pdp-price', '[data-role="price"]',
                    '[class*="sale-price"]', '[class*="SalePrice"]',
                    '[class*="price-current"]', '[class*="PriceCurrent"]',
                    '[class*="price"] span', '[class*="Price"] span',
                ];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) {
                        const t = el.textContent.trim();
                        const m = t.match(/\\$?\\s*(\\d[\\d,]*\\.?\\d*)/);
                        if (m) return {val: m[1], text: t};
                    }
                }
                // Last resort: any element with "price" in class
                const all = document.querySelectorAll('[class*="price"],[class*="Price"]');
                for (const el of all) {
                    const t = el.textContent.trim();
                    const m = t.match(/\\$?\\s*(\\d[\\d,]*\\.?\\d*)/);
                    if (m && parseFloat(m[1].replace(',','')) > 0.5) return {val: m[1], text: t};
                }
                return null;
            }""")
            if pi:
                result["price"] = pi.get("val")
                print(f"  DOM price: {pi}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_price: {e}")

    if not result["availability"]:
        try:
            av = page.evaluate("""() => {
                const txt = document.body.innerText || '';
                if (/out of stock|sold out|unavailable/i.test(txt)) return 'OUT_OF_STOCK';
                if (/pre-?order/i.test(txt)) return 'PRE_ORDER';
                if (/in stock|available|ready to ship|add to cart|buy now/i.test(txt)) return 'IN_STOCK';
                return null;
            }""")
            if av:
                result["availability"] = av
                print(f"  DOM avail: {av}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_avail: {e}")

    elapsed = time.monotonic() - t0
    result["elapsed"] = round(elapsed, 1)

    # 5. SUMMARY
    print(f"\n[5] DONE {elapsed:.1f}s", flush=True)
    print(f"  attempts = {result['attempts']}", flush=True)
    print(f"  title    = {result['title'][:80] if result['title'] else 'N/A'}", flush=True)
    print(f"  price    = {result['price']} {result.get('currency','')}", flush=True)
    print(f"  orig     = {result.get('original_price','')}", flush=True)
    print(f"  avail    = {result['availability']}", flush=True)
    print(f"  images   = {len(result['images'])}", flush=True)
    print(f"  mtop_cand= {len(result['mtop_candidates'])}", flush=True)
    print(f"  errors   = {result['errors'][:5]}", flush=True)

    browser.close()

# ─── SAVE ───
# Save best MTOP raw
if mtop_data:
    mtop_path = f"{OUT_DIR}\\diag_ali_pw8_mtop.json"
    with open(mtop_path, "w", encoding="utf-8") as f:
        json.dump(mtop_data, f, ensure_ascii=False, indent=2)
    print(f"\nMTOP JSON saved: {mtop_path}", flush=True)

# Save all candidates raw
if mtop_candidates:
    cand_path = f"{OUT_DIR}\\diag_ali_pw8_candidates.json"
    with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(
            [{"url": u, "status": s, "len": len(b), "body": b[:5000]} for u, s, b in mtop_candidates],
            f, ensure_ascii=False, indent=2,
        )
    print(f"Candidates saved: {cand_path}", flush=True)

# Save result
out_path = f"{OUT_DIR}\\diag_ali_pw8_result.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Result saved: {out_path}", flush=True)
