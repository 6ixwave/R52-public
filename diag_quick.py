"""Быстрая диагностика: FARPOST (статус) + WB (где цены)."""
import json, logging, re, sys, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_quick")

sys.path.insert(0, str(Path(__file__).parent))

# === 1. FARPOST ===
log.info("=== FARPOST TEST ===")
from r52.collectors.farpost import FarpostCollector
fc = FarpostCollector(use_proxies=False)
lid = "116361427"
url = fc.build_url(lid)
log.info("URL: %s", url)
t0 = time.monotonic()
wrapper = fc.collect_product(lid, url)
elapsed = time.monotonic() - t0
if wrapper:
    obs = wrapper["observation"]
    status = obs.get("status")
    title = (obs.get("fields", {}).get("title", {}).get("value") or "")[:60]
    prices = obs.get("price_candidates", [])
    media = obs.get("media", {}).get("items", [])
    log.info("FARPOST: status=%s (%.1fs) title=%r prices=%d media=%d",
             status, elapsed, title, len(prices), len(media))
    for p in prices[:3]:
        log.info("  price: role=%s amount=%s cur=%s label=%s",
                 p.get("role"), p.get("amount"), p.get("currency"), p.get("label"))
else:
    log.warning("FARPOST: wrapper=None (%.1fs)", elapsed)
fc.close()

time.sleep(2)

# === 2. WB ===
log.info("=== WB TEST ===")
from r52.collectors.wb import WBCollector
wc = WBCollector(use_proxies=False)
nm_id = 199298871
url_wb = wc.build_url(str(nm_id))
log.info("URL: %s", url_wb)
t0 = time.monotonic()

# Step 1: card.json
card_data = wc._fetch_card_json(nm_id)
if card_data:
    log.info("WB card.json: OK, keys=%s", list(card_data.keys())[:15])
    selling = card_data.get("selling", {})
    if isinstance(selling, dict):
        log.info("  selling keys: %s", list(selling.keys()))
        for pk in ("price", "priceU", "priceD", "oldPrice", "salePriceU", "salePriceD"):
            v = selling.get(pk)
            if v is not None:
                log.info("  selling.%s = %s (type=%s)", pk, repr(v)[:80], type(v).__name__)
    # top-level price fields
    for pk in ("price", "priceU", "priceD", "oldPrice"):
        v = card_data.get(pk)
        if v is not None:
            log.info("  top.%s = %s (type=%s)", pk, repr(v)[:80], type(v).__name__)
    # sizes
    sizes = card_data.get("sizes", [])
    if sizes:
        log.info("  sizes: %d items, first keys=%s", len(sizes), list(sizes[0].keys()) if sizes else [])
        if isinstance(sizes[0], dict):
            sp = sizes[0].get("price", {})
            log.info("  sizes[0].price = %s", json.dumps(sp, ensure_ascii=False)[:200])
            log.info("  sizes[0].qty = %s", sizes[0].get("qty"))
    # media
    media_info = card_data.get("media", {})
    if isinstance(media_info, dict):
        log.info("  media: photo_count=%s has_video=%s", media_info.get("photo_count"), media_info.get("has_video"))
else:
    log.warning("WB card.json: None")

time.sleep(2)

# Step 2: price API (first 2 endpoints only for speed)
log.info("WB: trying price endpoints...")
endpoints = [
    ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
]
for label, u in endpoints:
    resp = wc._http_get(u, headers={"Accept": "application/json"}, timeout=10, fast=True)
    if resp:
        log.info("  %s: HTTP %d, body[:120]=%s", label, resp.status_code, (resp.text or "")[:120])
    else:
        log.info("  %s: None (error)", label)
    time.sleep(1)

time.sleep(2)

# Step 3: HTML
log.info("WB: fetching HTML...")
html_resp = wc._fetch_html(url_wb)
if html_resp:
    log.info("  HTML: %dB", len(html_resp.content))
    html = html_resp.text
    # Check for price patterns
    og_price = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    log.info("  og:price:amount = %s", og_price.group(1) if og_price else "NOT FOUND")
    
    data_price = re.findall(r'data-price=["\'](\d+)["\']', html[:50000], re.I)
    log.info("  data-price attrs (first 5): %s", data_price[:5])
    
    embedded = re.search(r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)', html)
    log.info("  embedded price.basic = %s", embedded.group(1) if embedded else "NOT FOUND")
    
    priceU = re.search(r'"priceU"\s*:\s*(\d+)', html)
    priceD = re.search(r'"priceD"\s*:\s*(\d+)', html)
    log.info("  embedded priceU=%s priceD=%s", priceU.group(1) if priceU else "?", priceD.group(1) if priceD else "?")
    
    # window.__STATE__ or similar
    state_match = re.search(r'window\.__STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
    if state_match:
        log.info("  window.__STATE__ found, len=%d", len(state_match.group(1)))
    else:
        # Try other embedded state patterns
        for pat in (r'window\.__INITIAL_STATE__', r'window\.__PRELOADED_STATE__', r'window\.state'):
            m = re.search(pat + r'\s*=', html)
            if m:
                log.info("  found %s at pos %d", pat, m.start())
                break
        else:
            log.info("  no window.__STATE__ pattern found")
    
    # Visible price text
    price_texts = re.findall(r'>\s*([\d\s\u00a0]{2,15})\s*(?:₽|руб)\s*<', html[:100000])
    log.info("  visible price texts (first 5): %s", price_texts[:5])
else:
    log.warning("  HTML: None (fetch failed)")

elapsed = time.monotonic() - t0
log.info("WB total: %.1fs", elapsed)
wc.close()

log.info("=== DONE ===")
