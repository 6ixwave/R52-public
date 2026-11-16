"""Диагностика: wholesale HTML → _init_data_ → cards → products.
Плюс повторный тест search API с вариациями URL."""
import json, re, sys, time, os, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_wholesale")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "collector_output")
os.makedirs(OUT, exist_ok=True)

from curl_cffi import requests as cffi_req
from urllib.parse import quote

# ═══════════════════════════════════════════
# PHASE 1: Wholesale search page
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("PHASE 1: Wholesale page (ru.aliexpress.com/wholesale)")
print("=" * 60)

query = "наушники"
encoded = quote(query, safe="")
ws_url = f"https://ru.aliexpress.com/wholesale?SearchText={encoded}"
print(f"URL: {ws_url}")

t0 = time.monotonic()
resp = cffi_req.get(
    ws_url,
    impersonate="chrome",
    timeout=(15, 30),
    headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://ru.aliexpress.com/",
    },
)
elapsed = time.monotonic() - t0
print(f"HTTP {resp.status_code}, {len(resp.text)} bytes, {elapsed:.1f}s")

html = resp.text
html_path = os.path.join(OUT, "diag_ws_page.html")
with open(html_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Saved → {html_path}")

# CAPTCHA check
captcha_markers = ["Пройдите проверку", "verify-human", "captcha", "tmd=", "punish"]
has_captcha = any(m in html[:5000] for m in captcha_markers)
print(f"CAPTCHA: {has_captcha}")

# ═══════════════════════════════════════════
# PHASE 2: Extract _init_data_
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("PHASE 2: Extract _dida_config_._init_data_")
print("=" * 60)

dida_idx = html.find("_dida_config_")
print(f"_dida_config_ at: {dida_idx}")

init_idx = html.find("_init_data_", dida_idx)
if init_idx < 0:
    # Try alternative: window._init_data_ or just _init_data_
    init_idx = html.find("_init_data_")
    print(f"Alt _init_data_ at: {init_idx}")

if init_idx > 0:
    brace_start = html.find("{", init_idx)
    # Brace matching
    depth = 0
    end = brace_start
    for i in range(brace_start, min(brace_start + 800000, len(html))):
        c = html[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = html[brace_start:end]
    print(f"_init_data_ block: {len(block)} chars")

    # Save raw
    dida_path = os.path.join(OUT, "diag_ws_init_data.json")
    with open(dida_path, "w", encoding="utf-8") as f:
        f.write(block)
    print(f"Saved → {dida_path}")

    # Parse JSON
    try:
        data = json.loads(block)
        print("JSON parsed OK")
        print(f"Top keys: {list(data.keys())[:15]}")

        # Navigate: data.data.root.fields.mods
        # or data.hierarchy + data.data
        # The structure from diag_v5: data.hierarchy.structure + data.data.{moduleId}
        
        # Find "data" key
        d = data.get("data", data)
        if isinstance(d, dict):
            print(f"data keys: {list(d.keys())[:20]}")
            
            # Look for cards module
            # In hierarchy: cards2023_3722
            # In data: should be data["cards2023_3722"] or nested
            cards_key = None
            for k in d:
                if "cards" in k.lower():
                    cards_key = k
                    break
            if cards_key:
                print(f"Found cards module: {cards_key}")
                cards_data = d[cards_key]
                print(f"  type: {type(cards_data).__name__}")
                if isinstance(cards_data, dict):
                    print(f"  keys: {list(cards_data.keys())[:20]}")
                    # Save cards
                    cards_path = os.path.join(OUT, "diag_ws_cards.json")
                    with open(cards_path, "w", encoding="utf-8") as f:
                        json.dump(cards_data, f, ensure_ascii=False, indent=2)
                    print(f"  Saved → {cards_path}")
            else:
                print("No 'cards' key in data")
                # List all keys with sizes
                for k, v in d.items():
                    sz = len(json.dumps(v, ensure_ascii=False)) if isinstance(v, (dict, list)) else len(str(v))
                    if sz > 500:
                        print(f"  {k}: {type(v).__name__}, ~{sz} chars")

        # Also check "hierarchy"
        hier = data.get("hierarchy", {})
        if hier:
            print(f"\nhierarchy keys: {list(hier.keys())}")
            struct = hier.get("structure", {})
            print(f"structure keys: {list(struct.keys())[:15]}")

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        # Try to fix: maybe trailing comma or JS-specific syntax
        # Save first 500 chars for inspection
        print(f"Block start: {block[:500]}")

else:
    print("No _init_data_ found!")
    # Maybe different variable name
    for marker in ["_init_data_", "INIT_DATA", "__INIT_DATA__", "pageData", "searchData"]:
        idx = html.find(marker)
        if idx >= 0:
            print(f"  Found '{marker}' at {idx}")

# ═══════════════════════════════════════════
# PHASE 3: Search API variations
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("PHASE 3: Search API variations")
print("=" * 60)

api_variants = [
    # Original
    f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={encoded}&page=1&pageSize=40&sortType=default",
    # Without sortType
    f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={encoded}&page=1&pageSize=40",
    # With more params (mimicking browser)
    f"https://ru.aliexpress.com/aer-webapi/v1/search?SearchText={encoded}&page=1&pageSize=40&sortType=default&callScene=pcSearch",
    # aliexpress.ru domain
    f"https://aliexpress.ru/aer-webapi/v1/search?SearchText={encoded}&page=1&pageSize=40&sortType=default",
]

for i, api_url in enumerate(api_variants):
    print(f"\n--- Variant {i+1}: {api_url[:100]}...")
    try:
        t0 = time.monotonic()
        r = cffi_req.get(
            api_url,
            impersonate="chrome",
            timeout=(10, 20),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": f"https://ru.aliexpress.com/wholesale?SearchText={encoded}",
                "Origin": "https://ru.aliexpress.com",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        el = time.monotonic() - t0
        ct = r.headers.get("Content-Type", "unknown")
        print(f"  HTTP {r.status_code}, {len(r.text)} bytes, {el:.1f}s, CT={ct}")
        
        is_json = "json" in ct.lower()
        is_html = "html" in ct.lower()
        print(f"  JSON={is_json}, HTML={is_html}")
        
        if is_json or (not is_html and r.text.strip().startswith("{")):
            try:
                jdata = r.json()
                print(f"  Parsed JSON! Top keys: {list(jdata.keys())[:10]}")
                # Save
                save_path = os.path.join(OUT, f"diag_ws_api_v{i+1}.json")
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                print(f"  Saved → {save_path}")
                
                # Quick product count
                def count_snippets(obj, depth=0):
                    if depth > 10:
                        return 0
                    c = 0
                    if isinstance(obj, dict):
                        if "snippetContainer" in obj:
                            c += 1
                        for v in obj.values():
                            c += count_snippets(v, depth + 1)
                    elif isinstance(obj, list):
                        for it in obj:
                            c += count_snippets(it, depth + 1)
                    return c
                n = count_snippets(jdata)
                print(f"  Snippets: {n}")
            except Exception as e:
                print(f"  JSON parse fail: {e}")
                print(f"  First 200: {r.text[:200]}")
        elif is_html:
            # Check if it's a 404 or actual page
            title_m = re.search(r'<title[^>]*>(.*?)</title>', r.text, re.DOTALL)
            og_title = re.search(r'property="og:title"\s+content="([^"]*)"', r.text)
            print(f"  <title>: {title_m.group(1)[:80] if title_m else 'N/A'}")
            print(f"  og:title: {og_title.group(1) if og_title else 'N/A'}")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
    time.sleep(1)

# ═══════════════════════════════════════════
# PHASE 4: Check if wholesale page has inline product data
# ═══════════════════════════════════════════
print("\n" + "=" * 60)
print("PHASE 4: Inline product data in wholesale HTML")
print("=" * 60)

# Look for common product markers in the full HTML
markers = ["itemId", "itemTitle", "finalPrice", "productsFeed", "productsV2",
           "snippetContainer", "aerEvent", "cardData", "itemCard"]
for m in markers:
    cnt = html.count(m)
    if cnt:
        print(f"  '{m}': {cnt} occurrences")

# Look for script tags with data
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
print(f"\nTotal scripts: {len(scripts)}")
for i, s in enumerate(scripts):
    if len(s) > 1000:
        first_line = s.strip()[:100]
        print(f"  [{i}] {len(s)} chars: {first_line}")

print("\n" + "=" * 60)
print("DIAG WHOLESALE DONE")
print("=" * 60)
