"""Финальная диагностика AliExpress.ru: search API + HTML product page."""
import json, re, sys, time, os, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag")

OUT = os.path.join(os.path.dirname(__file__), "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

# ─── 1. SEARCH API ───
print("\n" + "="*60)
print("PHASE 1: Search API (aer-webapi/v1/search)")
print("="*60)

try:
    from curl_cffi import requests as cffi_req
    from urllib.parse import quote

    query = "наушники"
    encoded = quote(query, safe="")
    url = (f"https://ru.aliexpress.com/aer-webapi/v1/search"
           f"?SearchText={encoded}&page=1&pageSize=40&sortType=default")

    print(f"URL: {url}")
    t0 = time.monotonic()
    resp = cffi_req.get(
        url,
        impersonate="chrome",
        timeout=(15, 30),
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={encoded}",
            "Origin": "https://ru.aliexpress.com",
        },
    )
    elapsed = time.monotonic() - t0
    print(f"HTTP {resp.status_code}, {len(resp.text)} bytes, {elapsed:.1f}s")

    if resp.status_code == 200:
        raw_path = os.path.join(OUT, "diag_final_search_raw.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"Saved raw → {raw_path}")

        data = resp.json()
        # Explore structure
        print(f"Top keys: {list(data.keys())}")
        if "data" in data:
            d = data["data"]
            print(f"data keys: {list(d.keys())[:20]}")
            # Look for productsFeed
            def find_key(obj, target, path="", depth=0):
                if depth > 8:
                    return []
                found = []
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        p = f"{path}.{k}" if path else k
                        if target.lower() in k.lower():
                            found.append((p, type(v).__name__, len(v) if hasattr(v, '__len__') else v))
                        found.extend(find_key(v, target, p, depth+1))
                elif isinstance(obj, list):
                    for i, item in enumerate(obj[:5]):
                        found.extend(find_key(item, target, f"{path}[{i}]", depth+1))
                return found

            for target in ["productsFeed", "products", "items", "list", "cards"]:
                hits = find_key(data, target)
                if hits:
                    print(f"  '{target}': {hits[:5]}")

        # Try the known path: data.productsFeed.productsV2
        pf = data.get("data", {}).get("productsFeed", {})
        if pf:
            pv2 = pf.get("productsV2", [])
            print(f"productsV2: {len(pv2)} items")
            if pv2:
                first = pv2[0] if isinstance(pv2[0], dict) else {}
                print(f"  first keys: {list(first.keys())[:15]}")
                # Try snippetContainer
                def find_snippets(obj, depth=0):
                    if depth > 12:
                        return []
                    res = []
                    if isinstance(obj, dict):
                        if "snippetContainer" in obj:
                            res.append(obj["snippetContainer"])
                        for v in obj.values():
                            res.extend(find_snippets(v, depth+1))
                    elif isinstance(obj, list):
                        for it in obj:
                            res.extend(find_snippets(it, depth+1))
                    return res
                snippets = find_snippets(data)
                print(f"  snippets found: {len(snippets)}")
                if snippets:
                    s0 = snippets[0]
                    print(f"  snippet[0] keys: {list(s0.keys())[:15]}")
        else:
            print("  No productsFeed at data.productsFeed")
            # Try other paths
            for k in data.get("data", {}):
                v = data["data"][k]
                if isinstance(v, (list, dict)) and len(str(v)) > 1000:
                    print(f"  data.{k}: {type(v).__name__}, len={len(v) if hasattr(v,'__len__') else '?'}")

    else:
        print(f"BODY (first 500): {resp.text[:500]}")

except Exception as e:
    print(f"SEARCH API ERROR: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

# ─── 2. HTML PRODUCT PAGE ───
print("\n" + "="*60)
print("PHASE 2: HTML product page")
print("="*60)

# Use a known product ID from earlier diagnostics
product_id = "1006710909"
prod_url = f"https://ru.aliexpress.com/item/{product_id}.html"
print(f"URL: {prod_url}")

try:
    from curl_cffi import requests as cffi_req
    t0 = time.monotonic()
    resp2 = cffi_req.get(
        prod_url,
        impersonate="chrome",
        timeout=(15, 30),
        headers={
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://ru.aliexpress.com/",
        },
    )
    elapsed = time.monotonic() - t0
    print(f"HTTP {resp2.status_code}, {len(resp2.text)} bytes, {elapsed:.1f}s")

    if resp2.status_code == 200:
        html = resp2.text
        html_path = os.path.join(OUT, "diag_final_product.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved HTML → {html_path}")

        # Check for CAPTCHA
        captcha_markers = ["Пройдите проверку", "verify-human", "captcha", "tmd="]
        has_captcha = any(m in html[:5000] for m in captcha_markers)
        print(f"CAPTCHA detected: {has_captcha}")

        # Look for _dida_config_
        dida_start = html.find("_dida_config_")
        print(f"_dida_config_ at offset: {dida_start}")
        if dida_start > 0:
            # Find the JSON object
            brace_start = html.find("{", dida_start)
            # Try to extract _init_data_
            init_start = html.find("_init_data_", dida_start)
            if init_start > 0:
                brace2 = html.find("{", init_start)
                # Simple brace matching
                depth = 0
                end = brace2
                for i in range(brace2, min(brace2 + 600000, len(html))):
                    if html[i] == '{': depth += 1
                    elif html[i] == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                block = html[brace2:end]
                print(f"_init_data_ block: {len(block)} chars")
                # Check for product data
                for marker in ["itemTitle", "price", "itemId", "sku", "sellerName", "og:title"]:
                    cnt = block.count(marker)
                    if cnt:
                        print(f"  '{marker}': {cnt} occurrences")
                # Save for inspection
                dida_path = os.path.join(OUT, "diag_final_dida.json")
                with open(dida_path, "w", encoding="utf-8") as f:
                    f.write(block)
                print(f"Saved dida → {dida_path}")

        # Also check for JSON-LD
        jsonld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"JSON-LD blocks: {len(jsonld_blocks)}")
        for i, bl in enumerate(jsonld_blocks[:3]):
            print(f"  [{i}] first 200: {bl.strip()[:200]}")

        # Check <title>
        title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        if title_m:
            print(f"<title>: {title_m.group(1).strip()[:150]}")

        # Check for price in HTML
        price_matches = re.findall(r'[\d\s\u00a0\u202f]+(?:\.\d+)?\s*₽', html[:100000])
        print(f"Price patterns in first 100KB: {price_matches[:5]}")

    else:
        print(f"BODY (first 300): {resp2.text[:300]}")

except Exception as e:
    print(f"HTML PAGE ERROR: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

print("\n" + "="*60)
print("DIAG FINAL DONE")
print("="*60)
