"""WB v11: фокус на ЦЕНУ + 4 стратегии изображений. Бюджет ~150с."""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("wb11")

from curl_cffi import requests as cr

NM = 199298871
VOL = NM // 100000
PART = NM // 1000
BASKET = "basket-13.wbbasket.ru"

H = {"Accept": "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9",
     "Referer": "https://www.wildberries.ru/"}
HH = {"Accept": "text/html,*/*", "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://www.wildberries.ru/"}

results = {"price": {}, "images": [], "html": {}}
t_start = time.monotonic()

def g(label, url, hdrs=H, to=8):
    el = time.monotonic() - t_start
    if el > 140:
        log.info("BUDGET exceeded at %s", label); return 0, None
    try:
        r = cr.get(url, headers=hdrs, impersonate="chrome", timeout=to)
        log.info("%-28s %d %3.1fs %dB", label, r.status_code, time.monotonic()-t_start, len(r.content))
        return r.status_code, r
    except Exception as e:
        log.info("%-28s ERR %s", label, str(e)[:60]); return 0, None

# === A) PRICE: 6 best candidates ===
log.info("=== A) PRICE (6 endpoints) ===")
price_eps = [
    ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("card_v2_ext", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}&extend=1"),
    ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM}"),
    ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM}"),
    ("basket_price", f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/info/ru/price.json"),
    ("m_card", f"https://m.wildberries.ru/cards/v2/detail?appType=1&curr=rub&nm={NM}"),
]
for lbl, url in price_eps:
    st, r = g(lbl, url)
    if st == 200 and r:
        try:
            d = r.json()
            results["price"][lbl] = d
            prods = d.get("products") or d.get("data", {}).get("items") or []
            if prods:
                p = prods[0]
                sz = p.get("sizes", [{}])
                if sz:
                    log.info("  *** PRICE: %s sizes[0].price=%s", lbl, sz[0].get("price"))
        except Exception as e:
            log.info("  200 non-JSON: %s", str(e)[:60])
    time.sleep(1)

# === B) IMAGES: 4 strategies ===
log.info("=== B) IMAGES ===")
# B1: numbered S800_1..5
for i in range(1, 6):
    u = f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/images/S800/S800_{i}.jpg"
    st, r = g(f"img_num_{i}", u, HH, 6)
    if st == 200 and r and len(r.content) > 500:
        results["images"].append({"strat": "numbered", "idx": i, "url": u, "bytes": len(r.content)})
    time.sleep(0.5)

# B2: primary (known works)
u = f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/images/S800/S800.jpg"
st, r = g("img_primary", u, HH, 6)
if st == 200 and r and len(r.content) > 500:
    results["images"].append({"strat": "primary", "url": u, "bytes": len(r.content)})

# B3: color variants (first 3 from card.json)
colors = [173121919, 173036660, 199298870]
for c in colors:
    cv, cp = c // 100000, c // 1000
    u = f"https://{BASKET}/vol{cv}/part{cp}/{c}/images/S800/S800.jpg"
    st, r = g(f"img_color_{c}", u, HH, 6)
    if st == 200 and r and len(r.content) > 500:
        results["images"].append({"strat": "color", "nm": c, "url": u, "bytes": len(r.content)})
    time.sleep(0.5)

# B4: ir- CDN
for pfx in ["ir-13", "ir-01"]:
    u = f"https://{pfx}.wbbasket.ru/vol{VOL}/part{PART}/{NM}/images/S800/S800.jpg"
    st, r = g(f"img_ir_{pfx}", u, HH, 5)
    if st == 200 and r and len(r.content) > 500:
        results["images"].append({"strat": "ir", "url": u, "bytes": len(r.content)})
    time.sleep(0.3)

# === C) HTML: look for __NEXT_DATA__ / JSON-LD / price in text ===
log.info("=== C) HTML ===")
u = f"https://www.wildberries.ru/catalog/{NM}/detail.aspx"
st, r = g("html_detail", u, HH, 15)
if st == 200 and r:
    html = r.text
    results["html"]["size"] = len(html)
    # JSON-LD
    for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.I|re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            results["html"]["jsonld"] = ld
            log.info("  *** JSON-LD: %s", json.dumps(ld, ensure_ascii=False)[:200])
        except: pass
    # __NEXT_DATA__
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            nd = json.loads(m.group(1))
            results["html"]["next_data_keys"] = list(nd.keys())[:10]
            log.info("  *** NEXT_DATA keys: %s", list(nd.keys())[:10])
        except: pass
    # Price patterns
    prices = re.findall(r'(\d{3,8}[.,]?\d{0,2})\s*(?:₽|руб\.)', html)
    if prices:
        results["html"]["prices_text"] = prices[:10]
        log.info("  *** prices in text: %s", prices[:5])
    # og:price
    m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    if m:
        results["html"]["og_price"] = m.group(1)

# === SAVE ===
out = Path(__file__).parent / "diag_wb_v11_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. price=%d imgs=%d ===", time.monotonic()-t_start,
         len(results["price"]), len(results["images"]))
