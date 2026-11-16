"""WB v10: поиск цены + доп. изображений.
Тесты:
  A) Price: card.wb.ru (разные параметры), m.wb, catalog.wb, web/api variants
  B) Images: numbered S800_N.jpg, colors array, ir-*.wbbasket.ru
  C) Mobile / alternative endpoints
"""
import json, sys, time, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_wb_v10")

from curl_cffi import requests as cr

H_JSON = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}
H_HTML = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}

NM_ID = 199298871
VOL = NM_ID // 100000   # 1992
PART = NM_ID // 1000     # 199298
BASKET = "basket-13.wbbasket.ru"  # known from prior runs

results = {}

def try_get(label, url, headers=H_JSON, timeout=10):
    t0 = time.monotonic()
    try:
        r = cr.get(url, headers=headers, impersonate="chrome", timeout=timeout)
        dt = round(time.monotonic() - t0, 1)
        status = r.status_code
        body = r.text[:300]
        log.info("%-30s %d  %3.1fs  %s", label, status, dt, body[:120].replace("\n", " "))
        return status, r
    except Exception as e:
        log.info("%-30s ERR %s", label, str(e)[:80])
        return 0, None

# ============================================================
# A) PRICE ENDPOINTS
# ============================================================
log.info("=== A) PRICE ===")

price_urls = [
    # card.wb.ru variants
    (f"card_v2_basic",
     f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    (f"card_v2_nodest",
     f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&nm={NM_ID}"),
    (f"card_v1_nodest",
     f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&nm={NM_ID}"),
    (f"card_v2_batch",
     f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}&extend=1"),
    (f"card_v2_spp0",
     f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=0&nm={NM_ID}"),
    # catalog.wb.ru
    (f"catalog_v2",
     f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    # m.wildberries.ru (mobile)
    (f"m_web_api",
     f"https://m.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    (f"m_card_v2",
     f"https://m.wildberries.ru/cards/v2/detail?appType=1&curr=rub&nm={NM_ID}"),
    # www.wildberries.ru web api variants
    (f"web_api_v1",
     f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    (f"web_api_product",
     f"https://www.wildberries.ru/web/api/catalog/product/{NM_ID}"),
    (f"web_api_v2",
     f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM_ID}"),
    # basket CDN price?
    (f"basket_price",
     f"https://{BASKET}/vol{VOL}/part{PART}/{NM_ID}/info/ru/price.json"),
    (f"basket_price2",
     f"https://{BASKET}/vol{VOL}/part{PART}/{NM_ID}/price/ru.json"),
    # wbx-content (newer CDN)
    (f"wbx_card",
     f"https://wbx-content-v2.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"),
    (f"wbx_price",
     f"https://wbx-content-v2.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/info/ru/price.json"),
]

for label, url in price_urls:
    status, r = try_get(label, url)
    if status == 200 and r:
        try:
            data = r.json()
            results[f"price_{label}"] = data
            log.info("  *** JSON OK, keys: %s", list(data.keys())[:10])
        except Exception:
            log.info("  200 but not JSON: %s", r.text[:100])
    time.sleep(1.5)

# ============================================================
# B) IMAGES
# ============================================================
log.info("=== B) IMAGES ===")

img_results = []

# B1: Numbered images S800_1.jpg .. S800_6.jpg
for i in range(1, 8):
    url = f"https://{BASKET}/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800_{i}.jpg"
    status, r = try_get(f"img_S800_{i}", url, headers=H_HTML, timeout=8)
    if status == 200 and r and len(r.content) > 1000:
        img_results.append({"type": "numbered", "idx": i, "url": url, "bytes": len(r.content)})
        log.info("  *** S800_%d: %d bytes", i, len(r.content))
    time.sleep(0.8)

# B2: Colors array (variant images)
colors = [173121919, 173036660, 199298871, 199298870, 308060721, 308060716, 201304460]
for c in colors[:4]:  # test first 4
    cvol = c // 100000
    cpart = c // 1000
    # Try same basket host first
    url = f"https://{BASKET}/vol{cvol}/part{cpart}/{c}/images/S800/S800.jpg"
    status, r = try_get(f"color_{c}", url, headers=H_HTML, timeout=8)
    if status == 200 and r and len(r.content) > 1000:
        img_results.append({"type": "color", "nm_id": c, "url": url, "bytes": len(r.content)})
        log.info("  *** color %d: %d bytes", c, len(r.content))
    elif status == 404:
        # try other basket hosts
        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            if host == BASKET:
                continue
            url2 = f"https://{host}/vol{cvol}/part{cpart}/{c}/images/S800/S800.jpg"
            status2, r2 = try_get(f"color_{c}_b{n:02d}", url2, headers=H_HTML, timeout=6)
            if status2 == 200 and r2 and len(r2.content) > 1000:
                img_results.append({"type": "color", "nm_id": c, "url": url2, "bytes": len(r2.content)})
                log.info("  *** color %d on %s: %d bytes", c, host, len(r2.content))
                break
            if status2 != 404:
                break
            time.sleep(0.5)
    time.sleep(1)

# B3: ir-*.wbbasket.ru (older CDN, previously HTTP_0)
for prefix in ["ir-13", "ir-01", "ir-05"]:
    url = f"https://{prefix}.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800.jpg"
    status, r = try_get(f"ir_{prefix}", url, headers=H_HTML, timeout=6)
    if status == 200 and r and len(r.content) > 1000:
        img_results.append({"type": "ir", "url": url, "bytes": len(r.content)})
    time.sleep(0.5)

# B4: Primary (already known to work)
url = f"https://{BASKET}/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800.jpg"
status, r = try_get("img_primary", url, headers=H_HTML, timeout=8)
if status == 200 and r:
    img_results.append({"type": "primary", "url": url, "bytes": len(r.content)})

# ============================================================
# C) HTML with JSON-LD (try detail.aspx with Accept: application/ld+json)
# ============================================================
log.info("=== C) HTML / JSON-LD ===")
H_LD = {
    "Accept": "application/ld+json, application/json, text/html",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}
url = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"
status, r = try_get("html_ld", url, headers=H_LD, timeout=15)
if status == 200 and r:
    results["html_ld"] = r.text[:2000]
    # Look for JSON-LD blocks
    import re
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r.text, re.I | re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            results["jsonld"] = ld
            log.info("  *** JSON-LD found: %s", json.dumps(ld, ensure_ascii=False)[:200])
        except Exception:
            pass

# ============================================================
# SAVE
# ============================================================
out = Path(__file__).parent / "diag_wb_v10_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE. %d price results, %d images. Saved: %s ===",
         len([k for k in results if k.startswith("price_")]), len(img_results), out)
