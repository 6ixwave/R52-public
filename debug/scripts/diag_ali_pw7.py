"""diag_ali_pw7.py — Извлечение реальных полей товара из PDP-MTOP AliExpress.

Цель:
  1. Playwright: warm-up + страница товара.
  2. Перехват network-ответа mtop.aliexpress.pdp.pc.query.
  3. Разбор JSONP → JSON.
  4. Поиск title / price / currency / images / stock в структуре data.result.
  5. DOM-fallback для недостающих полей.
  6. Сохранение полного MTOP-ответа + извлечённых полей в JSON.
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
    "mtop_raw_len": 0,
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
}

mtop_data = None
mtop_raw = ""


def on_mtop_response(resp):
    """Intercept MTOP PDP response."""
    global mtop_data, mtop_raw
    url = resp.url
    if "pdp.pc.query" not in url and "mtop.aliexpress.pdp" not in url:
        return
    try:
        body = resp.text()
        mtop_raw = body
        result["mtop_raw_len"] = len(body)
        print(f"  [PDP-MTOP] {resp.status} len={len(body)}", flush=True)
        print(f"  First 300: {body[:300]}", flush=True)

        # JSONP: mtopjsonp1({...})  /  mtopjsonp2({...})
        m = re.match(r'^\s*mtopjsonp\d+\s*\((\{.*\})\)\s*$', body, re.DOTALL)
        if m:
            data = json.loads(m.group(1))
        else:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                # Maybe wrapped differently
                m2 = re.search(r'\{.*\}', body, re.DOTALL)
                if m2:
                    data = json.loads(m2.group(0))
                else:
                    result["errors"].append("mtop: cannot parse JSONP/JSON")
                    return

        mtop_data = data
        # success flag
        d = data.get("data", {})
        if isinstance(d, dict):
            result["mtop_success"] = d.get("success", False)
        print(f"  PDP success={result['mtop_success']}", flush=True)

    except Exception as e:
        result["errors"].append(f"mtop_parse: {e}")
        print(f"  MTOP parse error: {e}", flush=True)


def extract_fields(data):
    """Walk MTOP data structure, fill result dict."""
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

    # --- Try GLOBAL_DATA / GLOBAL_D / globalData ---
    gd = None
    for key in ("GLOBAL_DATA", "GLOBAL_D", "globalData", "global", "GLOBAL"):
        if key in d and isinstance(d[key], dict):
            gd = d[key]
            break
    if gd:
        print(f"  GLOBAL keys ({len(gd)}): {list(gd.keys())[:25]}", flush=True)
        _extract_from_section(gd, "GLOBAL")

    # --- Try productData / itemInfo / item / detailData ---
    pd = None
    for key in ("productData", "itemInfo", "item", "detailData", "product", "ITEM"):
        if key in d and isinstance(d[key], dict):
            pd = d[key]
            break
    if pd:
        print(f"  productData keys ({len(pd)}): {list(pd.keys())[:25]}", flush=True)
        _extract_from_section(pd, "productData")

    # --- Try skuInfo / sku ---
    sku = d.get("skuInfo") or d.get("sku") or d.get("SKU")
    if isinstance(sku, dict):
        print(f"  sku keys: {list(sku.keys())[:15]}", flush=True)
        _extract_price_from(sku, "sku")

    # --- Deep search fallbacks ---
    if not result["title"]:
        found = _deep_find_str(d, ("title", "subject", "productTitle", "seoTitle", "name"), 5)
        if found:
            result["title"] = found
            print(f"  [deep] title: {found[:80]}", flush=True)

    if not result["price"]:
        found = _deep_find_price(d, 5)
        if found:
            result["price"], result["currency"] = found
            print(f"  [deep] price: {found[0]} {found[1]}", flush=True)

    if not result["images"]:
        found = _deep_find_imgs(d, 5)
        if found:
            result["images"] = found
            print(f"  [deep] images: {len(found)}", flush=True)

    if not result["availability"]:
        found = _deep_find_stock(d, 5)
        if found:
            result["availability"] = found
            print(f"  [deep] availability: {found}", flush=True)


def _extract_from_section(sec, label):
    """Extract title/price/images/stock from a section dict."""
    # Title
    if not result["title"]:
        for key in ("title", "subject", "productTitle", "seoTitle", "name", "h1"):
            v = sec.get(key)
            if isinstance(v, str) and len(v) > 3:
                result["title"] = v.strip()
                print(f"  [{label}] title: {v[:80]}", flush=True)
                break

    # Price
    if not result["price"]:
        for key in ("price", "priceInfo", "tradePrice", "skuPriceList", "priceData"):
            v = sec.get(key)
            if isinstance(v, dict):
                if _extract_price_from(v, f"{label}.{key}"):
                    break
            elif isinstance(v, (int, float)):
                result["price"] = f"{float(v):.2f}"
                print(f"  [{label}] price (num): {v}", flush=True)
                break
            elif isinstance(v, str):
                m = re.search(r'(\d[\d,]*\.?\d*)', v.replace(",", ""))
                if m:
                    result["price"] = m.group(1)
                    print(f"  [{label}] price (str): {v[:50]}", flush=True)
                    break

    # Images
    if not result["images"]:
        for key in ("images", "imageList", "gallery", "imgUrls", "skuImages", "imageUrls"):
            v = sec.get(key)
            if isinstance(v, list) and len(v) > 0:
                imgs = _normalize_img_list(v)
                if imgs:
                    result["images"] = imgs
                    print(f"  [{label}] images: {len(imgs)}", flush=True)
                    break

    # Stock / availability
    if not result["availability"]:
        for key in ("stock", "stockInfo", "available", "inStock", "stockStatus", "inventory"):
            v = sec.get(key)
            if v is not None:
                avail = _parse_stock(v)
                if avail:
                    result["availability"] = avail
                    print(f"  [{label}] availability: {avail}", flush=True)
                    break


def _extract_price_from(pd, label):
    """Try to extract price+currency from a price dict. Returns True if found."""
    for key in ("minPrice", "salePrice", "activityPrice", "price", "amount",
                "formattedPrice", "currentPrice", "displayPrice", "minSalePrice"):
        val = pd.get(key)
        if val:
            if isinstance(val, (int, float)):
                result["price"] = f"{float(val):.2f}"
            elif isinstance(val, str):
                m = re.search(r'(\d[\d,]*\.?\d*)', val.replace(",", ""))
                if m:
                    result["price"] = m.group(1)
                else:
                    result["price"] = val
            elif isinstance(val, dict):
                # nested: {"value": "12.99", "currency": "USD"}
                for ik in ("value", "amount", "price", "display"):
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
            if result["price"]:
                print(f"  [{label}] price: {result['price']} {result.get('currency','')}", flush=True)
                break

    if not result.get("currency"):
        for key in ("currency", "priceCurrency", "currencyCode"):
            v = pd.get(key)
            if v and isinstance(v, str):
                result["currency"] = v.upper()
                break

    # Original / list price
    for key in ("originalPrice", "listPrice", "subPrice", "minOriginalPrice", "original"):
        v = pd.get(key)
        if v:
            if isinstance(v, (int, float)):
                result["original_price"] = f"{float(v):.2f}"
            elif isinstance(v, str):
                m = re.search(r'(\d[\d,]*\.?\d*)', v.replace(",", ""))
                if m:
                    result["original_price"] = m.group(1)
            break

    return bool(result["price"])


def _normalize_img_list(imgs):
    """Normalize a list of image entries to list of URL strings."""
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
        if "alicdn" in u or "aliexpress-media" in u or "aliexpress.com" in u:
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


def _deep_find_str(data, keys, depth):
    if depth <= 0 or not isinstance(data, dict):
        return None
    for k in keys:
        if k in data:
            v = data[k]
            if isinstance(v, str) and len(v) > 3:
                return v
    for v in data.values():
        if isinstance(v, dict):
            r = _deep_find_str(v, keys, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:8]:
                if isinstance(item, dict):
                    r = _deep_find_str(item, keys, depth - 1)
                    if r:
                        return r
    return None


def _deep_find_price(data, depth):
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
            r = _deep_find_price(v, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    r = _deep_find_price(item, depth - 1)
                    if r:
                        return r
    return None


def _deep_find_imgs(data, depth):
    if depth <= 0 or not isinstance(data, dict):
        return None
    for k in ("images", "imageList", "gallery", "imgUrls", "skuImages", "imageUrls"):
        v = data.get(k)
        if isinstance(v, list) and len(v) > 0:
            imgs = _normalize_img_list(v)
            if imgs:
                return imgs
    for v in data.values():
        if isinstance(v, dict):
            r = _deep_find_imgs(v, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    r = _deep_find_imgs(item, depth - 1)
                    if r:
                        return r
    return None


def _deep_find_stock(data, depth):
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
            r = _deep_find_stock(v, depth - 1)
            if r:
                return r
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    r = _deep_find_stock(item, depth - 1)
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
    )
    page = ctx.new_page()

    # 1. WARM-UP
    print("[1] WARM-UP", flush=True)
    page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    print(f"  OK title={page.title()[:60]}", flush=True)

    # 2. PRODUCT PAGE (with MTOP intercept)
    print("[2] PRODUCT", flush=True)
    page.on("response", on_mtop_response)
    resp = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
    print(f"  HTTP {resp.status if resp else 'N/A'}", flush=True)

    # 3. WAIT for MTOP
    print("[3] WAIT 18s...", flush=True)
    page.wait_for_timeout(18000)

    # 4. If MTOP not caught, try page JS state
    if not mtop_data:
        print("  MTOP not intercepted, trying page state...", flush=True)
        try:
            state = page.evaluate(
                "() => window.__INIT_DATA_STATE__ || window.runParams || "
                "window.__NEXT_DATA__ || null"
            )
            if state and isinstance(state, dict):
                mtop_data = state
                result["mtop_success"] = True
                print(f"  Page state keys: {list(state.keys())[:15]}", flush=True)
        except Exception as e:
            result["errors"].append(f"page_state: {e}")

    # 5. EXTRACT from MTOP
    print("[4] EXTRACT", flush=True)
    if mtop_data:
        extract_fields(mtop_data)

    # 6. DOM FALLBACK
    print("[5] DOM FALLBACK", flush=True)
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
                    const s = img.src || img.dataset.src || '';
                    if (s.includes('alicdn') || s.includes('aliexpress-media')) urls.push(s);
                });
                return urls.slice(0, 20);
            }""")
            result["images"] = imgs
            print(f"  DOM images: {len(imgs)}", flush=True)
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
                ];
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (el) {
                        const t = el.textContent.trim();
                        const m = t.match(/\\$?\\s*(\\d[\\d,]*\\.?\\d*)/);
                        if (m) return {val: m[1], text: t};
                    }
                }
                return null;
            }""")
            if pi:
                result["price"] = pi.get("val")
                print(f"  DOM price: {pi}", flush=True)
        except Exception as e:
            result["errors"].append(f"dom_price: {e}")

    elapsed = time.monotonic() - t0
    result["elapsed"] = round(elapsed, 1)

    # 7. SUMMARY
    print(f"\n[6] DONE {elapsed:.1f}s", flush=True)
    print(f"  title   = {result['title'][:80] if result['title'] else 'N/A'}", flush=True)
    print(f"  price   = {result['price']} {result.get('currency','')}", flush=True)
    print(f"  orig    = {result.get('original_price','')}", flush=True)
    print(f"  avail   = {result['availability']}", flush=True)
    print(f"  images  = {len(result['images'])}", flush=True)
    print(f"  errors  = {result['errors']}", flush=True)

    browser.close()

# ─── SAVE ───
# Save full MTOP raw (for inspection)
if mtop_raw:
    mtop_path = f"{OUT_DIR}\\diag_ali_pw7_mtop_raw.json"
    with open(mtop_path, "w", encoding="utf-8") as f:
        f.write(mtop_raw)
    print(f"\nMTOP raw saved: {mtop_path} ({len(mtop_raw)} bytes)", flush=True)

# Save result
out_path = f"{OUT_DIR}\\diag_ali_pw7_result.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Result saved: {out_path}", flush=True)
