"""Диагностика AliExpress pw25: прямой HTTP (curl_cffi) + inline state.

Цель: получить реальные товарные данные (title, price, images, category, availability)
без Playwright, через curl_cffi impersonate=chrome.
Проверяем: __INIT_DATA_STATE__, runParams, JSON-LD, og: meta, price-селекторы в HTML.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone

PRODUCTS = [
    "1005011813323763",  # Bluetooth headphones (италия)
    "3256811627009011",  # redirect target (usa)
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

def fetch_html(product_id: str) -> tuple:
    """Возвращает (html, status, elapsed, final_url)."""
    from curl_cffi import requests as cffi_req
    url = f"https://www.aliexpress.com/item/{product_id}.html"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        "Referer": "https://www.aliexpress.com/",
    }
    t0 = time.monotonic()
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 45), headers=headers)
    elapsed = time.monotonic() - t0
    return resp.text, resp.status_code, elapsed, resp.url


def extract_init_state(html: str) -> dict:
    """Достаёт window.__INIT_DATA_STATE__ из HTML."""
    # Вариант 1: window.__INIT_DATA_STATE__ = {...};
    patterns = [
        r'window\.__INIT_DATA_STATE__\s*=\s*(\{.+?\})\s*;\s*(?:</script>|window\.)',
        r'window\.__INIT_DATA_STATE__\s*=\s*(\{.+?\})\s*;',
        r'__INIT_DATA_STATE__\s*=\s*(\{.+?\})\s*;',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            raw = m.group(1)
            # Иногда в конце есть ; или </script>
            raw = raw.rstrip().rstrip(';').strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # Попробовать убрать хвост
                for end in range(len(raw), max(0, len(raw) - 200), -1):
                    try:
                        return json.loads(raw[:end])
                    except json.JSONDecodeError:
                        continue
    return {}


def extract_run_params(html: str) -> dict:
    m = re.search(r'window\.runParams\s*=\s*(\{.+?\})\s*;', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return {}


def extract_jsonld(html: str) -> list:
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    results = []
    for b in blocks:
        try:
            results.append(json.loads(b.strip()))
        except json.JSONDecodeError:
            pass
    return results


def extract_og(html: str) -> dict:
    og = {}
    for m in re.finditer(r'<meta[^>]+property=["\']og:([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']', html, re.I):
        og[m.group(1)] = m.group(2)
    for m in re.finditer(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:([^"\']+)["\']', html, re.I):
        og[m.group(2)] = m.group(1)
    return og


def extract_prices_html(html: str) -> list:
    prices = []
    # data-price, data-activity-price, data-original-price
    for m in re.finditer(r'data-(?:activity-)?price=["\']([\d.]+)["\']', html, re.I):
        prices.append(m.group(1))
    # class-based price spans
    for m in re.finditer(
        r'class="[^"]*price[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([$\u20ac\u00a3]?[\d,]+\.?\d*)',
        html, re.I
    ):
        prices.append(m.group(1))
    # og:price:amount
    m = re.search(r'og:price:amount["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
    if m:
        prices.append(m.group(1))
    return list(dict.fromkeys(prices))[:10]


def extract_images_html(html: str) -> list:
    imgs = []
    for m in re.finditer(
        r'(?:src|data-src|data-lazy-src|data-original)=["\']([^"\']*(?:alicdn|aliexpress-media)[^"\']*)["\']',
        html, re.I
    ):
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        if not re.search(r'(placeholder|logo|icon|\.svg|\.gif|\.ico|blank|spacer|favicon)', url, re.I):
            imgs.append(url)
    return list(dict.fromkeys(imgs))[:20]


def find_product_in_state(state: dict) -> dict:
    """Ищет товарные данные в __INIT_DATA_STATE__."""
    if not state:
        return {}
    # Прямые ключи
    for key in ("productData", "product", "itemInfo", "item", "detailData", "result", "pdpData"):
        if key in state and isinstance(state[key], dict):
            return state[key]
    # Глубокий поиск
    def deep_find(data, depth=0):
        if depth > 6:
            return None
        if isinstance(data, dict):
            has_title = any(k in data for k in ("title", "subject", "name", "productTitle", "seoTitle"))
            has_price = any(k in data for k in ("price", "skuPriceList", "priceInfo", "tradePrice", "targetSkuPriceInfo", "minPrice", "salePrice"))
            if has_title and has_price:
                return data
            for v in data.values():
                if isinstance(v, dict):
                    r = deep_find(v, depth + 1)
                    if r:
                        return r
        elif isinstance(data, list):
            for item in data[:10]:
                if isinstance(item, dict):
                    r = deep_find(item, depth + 1)
                    if r:
                        return r
        return None
    return deep_find(state) or {}


def parse_product_data(pd: dict) -> dict:
    """Извлекает структурированные поля из найденного product data."""
    out = {"title": None, "prices": [], "images": [], "category": None, "availability": None, "sku": {}}

    # Title
    for tkey in ("title", "subject", "name", "productTitle", "seoTitle", "seoInfo"):
        val = pd.get(tkey)
        if isinstance(val, str) and len(val) > 5:
            out["title"] = val[:300]
            break
        if isinstance(val, dict):
            for sub in ("title", "seoTitle", "subject"):
                if val.get(sub) and isinstance(val[sub], str):
                    out["title"] = val[sub][:300]
                    break
            if out["title"]:
                break

    # Price
    for pkey in ("targetSkuPriceInfo", "priceInfo", "skuPriceList", "tradePrice", "price"):
        pi = pd.get(pkey)
        if isinstance(pi, dict):
            for pk in ("salePriceString", "minPrice", "salePrice", "price", "amount", "formattedPrice", "displayPrice", "actvPrice"):
                val = pi.get(pk)
                if val:
                    out["prices"].append({"key": f"{pkey}.{pk}", "value": str(val)[:100]})
            # salePriceLocal: "€|57|06"
            spl = pi.get("salePriceLocal", "")
            if isinstance(spl, str) and "|" in spl:
                parts = spl.split("|")
                if len(parts) >= 3:
                    try:
                        amt = float(f"{parts[1]}.{parts[2]}")
                        out["prices"].append({"key": "salePriceLocal", "value": f"{amt:.2f}", "currency": parts[0]})
                    except ValueError:
                        pass
            for extra in ("originalPrice", "monthlyLowestPrice"):
                val = pi.get(extra)
                if val:
                    out["prices"].append({"key": extra, "value": str(val)[:100]})
            break
        elif isinstance(pi, (int, float, str)):
            out["prices"].append({"key": pkey, "value": str(pi)[:100]})

    # Images
    for ikey in ("HEADER_IMAGE_PC", "images", "imageList", "gallery", "imgUrls", "currentSkuImages", "skuImages"):
        iv = pd.get(ikey)
        if isinstance(iv, dict):
            cur = iv.get("currentSkuImages", [])
            if isinstance(cur, list):
                out["images"] = [x for x in cur[:20] if isinstance(x, str)]
                break
        elif isinstance(iv, list):
            out["images"] = [x for x in iv[:20] if isinstance(x, str)]
            break

    # Category
    for ckey in ("categoryPath", "breadcrumb", "category1", "category3", "categories"):
        cv = pd.get(ckey)
        if cv:
            if isinstance(cv, list):
                out["category"] = " > ".join(str(x) for x in cv if x)
            else:
                out["category"] = str(cv)[:200]
            break

    # Availability
    for akey in ("stock", "inStock", "stockStatus", "available", "inventoryStatus", "stockCount", "buyNow"):
        av = pd.get(akey)
        if av is not None:
            out["availability"] = str(av)[:100]
            break

    # SKU
    sku_mod = pd.get("SKUModule", {})
    if isinstance(sku_mod, dict):
        out["sku"] = {
            "skuId": sku_mod.get("skuId"),
            "skuAttr": str(sku_mod.get("skuAttr", ""))[:200],
        }

    return out


def main():
    results = []
    for pid in PRODUCTS:
        print(f"\n{'='*60}")
        print(f"PRODUCT: {pid}")
        print(f"{'='*60}")

        try:
            html, status, elapsed, final_url = fetch_html(pid)
        except Exception as e:
            results.append({"product_id": pid, "error": str(e)})
            continue

        print(f"  HTTP {status}, {len(html)} bytes, {elapsed:.1f}s")
        print(f"  Final URL: {final_url[:120]}")

        r = {
            "product_id": pid,
            "http_status": status,
            "html_len": len(html),
            "elapsed": round(elapsed, 2),
            "final_url": final_url,
        }

        # 1. __INIT_DATA_STATE__
        init_state = extract_init_state(html)
        r["has_init_state"] = bool(init_state)
        if init_state:
            r["init_state_keys"] = list(init_state.keys())[:30]
            r["init_state_size"] = len(json.dumps(init_state))
            pd = find_product_in_state(init_state)
            if pd:
                r["product_data_keys"] = list(pd.keys())[:40]
                parsed = parse_product_data(pd)
                r["parsed"] = parsed
                # Сохранить полный product data для анализа
                r["product_data_raw"] = json.dumps(pd, ensure_ascii=False)[:5000]
            else:
                r["product_data_raw"] = json.dumps(init_state, ensure_ascii=False)[:3000]
                # Ищем price где угодно
                ds = json.dumps(init_state, ensure_ascii=False)
                pm = re.findall(r'"(?:minPrice|salePrice|price|amount|actvPrice|formattedPrice|displayPrice)"\s*:\s*"?([\d.]+)"?', ds)
                r["price_scan"] = pm[:10]
                tm = re.findall(r'"(?:title|subject|productTitle|seoTitle)"\s*:\s*"([^"]{10,300})"', ds)
                r["title_scan"] = tm[:5]

        # 2. runParams
        run_params = extract_run_params(html)
        r["has_run_params"] = bool(run_params)
        if run_params:
            r["run_params_keys"] = list(run_params.keys())[:30]

        # 3. JSON-LD
        jsonld = extract_jsonld(html)
        r["jsonld_count"] = len(jsonld)
        for j in jsonld:
            if isinstance(j, dict) and j.get("@type") in ("Product", ["Product"]):
                r["jsonld_product"] = {
                    "name": j.get("name", "")[:200],
                    "offers": j.get("offers"),
                    "image": j.get("image"),
                }
                break
            if isinstance(j, dict) and j.get("@graph"):
                for node in j["@graph"]:
                    if isinstance(node, dict) and node.get("@type") == "Product":
                        r["jsonld_product"] = {
                            "name": node.get("name", "")[:200],
                            "offers": node.get("offers"),
                            "image": node.get("image"),
                        }
                        break

        # 4. OG meta
        og = extract_og(html)
        r["og"] = {k: v[:200] for k, v in og.items()}

        # 5. Prices in HTML
        r["prices_html"] = extract_prices_html(html)

        # 6. Images in HTML
        r["images_html"] = extract_images_html(html)

        # 7. H1
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
        r["h1"] = re.sub(r'<[^>]+>', '', m.group(1)).strip()[:200] if m else None

        # 8. Title tag
        m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
        r["title_tag"] = m.group(1).strip()[:200] if m else None

        # Summary
        has_title = r.get("parsed", {}).get("title") or r.get("jsonld_product", {}).get("name") or r.get("og", {}).get("title") or r.get("h1")
        has_price = r.get("parsed", {}).get("prices") or r.get("prices_html") or r.get("jsonld_product", {}).get("offers")
        r["summary"] = {
            "title_found": bool(has_title),
            "price_found": bool(has_price),
            "images_found": bool(r.get("parsed", {}).get("images") or r.get("images_html")),
        }
        print(f"  INIT_STATE: {r['has_init_state']} (keys: {r.get('init_state_keys', [])[:10]})")
        print(f"  Parsed title: {r.get('parsed', {}).get('title', 'N/A')[:80]}")
        print(f"  Parsed prices: {r.get('parsed', {}).get('prices', [])[:3]}")
        print(f"  JSON-LD: {r['jsonld_count']} blocks")
        print(f"  OG title: {r.get('og', {}).get('title', 'N/A')[:80]}")
        print(f"  HTML prices: {r['prices_html'][:5]}")
        print(f"  Summary: {r['summary']}")

        results.append(r)
        time.sleep(3)

    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": "diag_ali_pw25",
        "results": results,
    }
    out_path = r"C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\diag_ali_pw25_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n\nSaved: {out_path} ({len(json.dumps(out))} bytes)")


if __name__ == "__main__":
    main()
