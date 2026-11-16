"""
diag_v7d.py — Пагинация AE_RU (p1+p2) + AE_COM product page price trace.
Запуск: python diag_v7d.py
"""
import sys, os, time, re, logging
from html import unescape as html_unescape

os.environ.setdefault("R52_HOME", os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Temp"), "R52"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7d")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_v7d_output.txt")
lines = []

def emit(s=""):
    print(s)
    lines.append(s)

def flush():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

QUERY = "наушники"
emit(f"=== diag_v7d — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
emit(f"Query: {QUERY!r}")
emit()

# ═══════════════════════════════════════════════════
# PART 1: AE_RU pagination (p1 + p2)
# ═══════════════════════════════════════════════════
emit("═══ PART 1: AE_RU pagination ═══")
emit()

try:
    from r52.collectors.aliexpress import AliExpressRUCollector
    a = AliExpressRUCollector(use_proxies=False)
    a.source = "ALIEXPRESS_RU"

    all_items = []
    for pg in (1, 2):
        t0 = time.monotonic()
        items = a.collect_by_search(QUERY, page=pg, page_size=10)
        el = round(time.monotonic() - t0, 1)
        all_items.extend(items)
        emit(f"  p{pg}: {len(items)} items {el}s")
        if pg == 1:
            time.sleep(3)

    # Quality
    if all_items:
        none_ids = 0
        empty_titles = 0
        no_price = 0
        bad_price = 0
        no_image = 0
        for it in all_items:
            obs = it.get("observation", it)
            iid = str(obs.get("id", ""))
            title = obs.get("fields", {}).get("title", {}).get("value", "")
            pc = obs.get("price_candidates", [])
            media = obs.get("media", {}).get("items", [])
            if iid in ("None", "", "null"):
                none_ids += 1
            if len(str(title)) < 10:
                empty_titles += 1
            if not pc:
                no_price += 1
            elif float(pc[0].get("amount", 0)) <= 1.0:
                bad_price += 1
            if not media:
                no_image += 1

        emit(f"  ── Quality ({len(all_items)} total) ──")
        emit(f"  None-ID: {none_ids}/{len(all_items)}")
        emit(f"  Empty-title: {empty_titles}/{len(all_items)}")
        emit(f"  No-price: {no_price}/{len(all_items)}")
        emit(f"  Bad-price (<=1.00): {bad_price}/{len(all_items)}")
        emit(f"  No-image: {no_image}/{len(all_items)}")

        # Pagination overlap
        p1_ids = set(str(it.get("observation", it).get("id", "")) for it in all_items[:10])
        p2_ids = set(str(it.get("observation", it).get("id", "")) for it in all_items[10:])
        if p1_ids and p2_ids:
            overlap = p1_ids & p2_ids
            emit(f"  ── Pagination ──")
            emit(f"  p1: {len(p1_ids)} unique IDs")
            emit(f"  p2: {len(p2_ids)} unique IDs")
            emit(f"  overlap: {len(overlap)}")
            if overlap:
                emit(f"  overlap IDs: {list(overlap)[:5]}")

        # Sample prices
        emit(f"  ── Sample prices (first 6) ──")
        for i, it in enumerate(all_items[:6]):
            obs = it.get("observation", it)
            pc = obs.get("price_candidates", [])
            pstr = " | ".join(f"{c.get('role','?')} {c.get('currency','?')} {c.get('amount','?')}" for c in pc) if pc else "NONE"
            emit(f"  [{i}] {pstr}")

    a.close()
    emit(f"  Stats: {a.stats}")
except Exception as e:
    import traceback
    emit(f"  FATAL: {type(e).__name__}: {e}")
    emit(traceback.format_exc())

emit()

# ═══════════════════════════════════════════════════
# PART 2: AE_COM product page — why no prices?
# ═══════════════════════════════════════════════════
emit("═══ PART 2: AE_COM product page trace ═══")
emit()

from curl_cffi import requests as cffi_req

# Use a known item from diag_v7a
test_id = "1005013565057281"
test_url = f"https://www.aliexpress.com/item/{test_id}.html"
emit(f"URL: {test_url}")

t0 = time.monotonic()
resp = cffi_req.get(
    test_url, impersonate="chrome", timeout=(15, 30),
    headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.aliexpress.com/",
    },
)
el = round(time.monotonic() - t0, 1)
emit(f"HTTP {resp.status_code}, {len(resp.text)} bytes, {el}s")
emit(f"Final URL: {resp.url}")
html = resp.text

# Check for redirect shell indicators
emit(f"  ── Shell detection ──")
emit(f"  <title>: {re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL).group(1).strip()[:100] if re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL) else 'NONE'}")

# Look for redirect meta
redir = re.findall(r'<meta[^>]*http-equiv="refresh"[^>]*>', html, re.I)
emit(f"  meta refresh: {redir[:2]}")

# Look for JS redirect
js_redir = re.findall(r'location\.(?:href|replace)\s*=\s*["\']([^"\']+)', html)
emit(f"  JS redirects: {js_redir[:3]}")

# __INIT_DATA_STATE__
init_m = re.search(r'window\.__INIT_DATA_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
emit(f"  __INIT_DATA_STATE__: {'found ' + str(len(init_m.group(1))) + ' chars' if init_m else 'NOT FOUND'}")

# runParams
rp_m = re.search(r'window\.runParams\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
if rp_m:
    emit(f"  runParams: {rp_m.group(1)[:200]}")

# Price in any form
emit(f"  ── Price search ──")
# All price-like numbers in the page
price_patterns = [
    (r'"(?:salePrice|originalPrice|displayPrice|price)"\s*:\s*("?[^,}\n]{1,50}"?)', "JSON price fields"),
    (r'(?:price|Price)[^>]*>\s*[^<]*?([\d,]+\.\d{2})', "DOM price"),
    (r'"(?:amount|value)"\s*:\s*"?([\d.]+)"?', "amount/value"),
    (r'(?:€|\$|£|RUB|EUR|USD)\s*([\d,]+\.?\d*)', "currency symbol"),
]
for pat, label in price_patterns:
    found = re.findall(pat, html)
    emit(f"  {label}: {found[:6]}")

# Check if it's a full product page or a shell
emit(f"  ── Page structure ──")
emit(f"  <h1>: {re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL).group(1).strip()[:100] if re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL) else 'NONE'}")
emit(f"  product title meta: {re.findall(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)[:1]}")
emit(f"  product:price:amount: {re.findall(r'product:price:amount[^>]*content="([^"]+)"', html)}")
emit(f"  product:price:currency: {re.findall(r'product:price:currency[^>]*content="([^"]+)"', html)}")

# Check for "new--price" or "us--price" classes (search page style)
new_price = re.findall(r'class="[^"]*(?:new--price|us--price)[^"]*"', html)
emit(f"  new--price/us--price classes: {len(new_price)} found")

# Check for SKU/price module
sku_mod = re.search(r'SKUModule|skuModule|priceModule', html)
emit(f"  SKU/price module: {'found' if sku_mod else 'NOT FOUND'}")

# First 500 chars of body for structure
body_m = re.search(r'<body[^>]*>(.{0,500})', html, re.DOTALL)
if body_m:
    body_snip = re.sub(r'\s+', ' ', body_m.group(1))[:400]
    emit(f"  <body> start: {body_snip}")

# Check if the page has a "redirecting" or "loading" indicator
loading = re.search(r'(?:redirect|loading|please wait|перенаправлен)', html, re.I)
emit(f"  redirect/loading indicator: {'found' if loading else 'none'}")

emit()
emit("=== DONE ===")
flush()
log.info("diag_v7d complete")
