"""WB v12: 60s budget. Check if card.json still works, primary image, 3 new price paths."""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("wb12")

from curl_cffi import requests as cr

NM = 199298871
VOL = NM // 100000  # 1992
PART = NM // 1000   # 199298
BASKET = "basket-13.wbbasket.ru"

HJ = {"Accept": "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://www.wildberries.ru/"}
HH = {"Accept": "text/html,image/*,*/*", "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://www.wildberries.ru/"}

results = {}
t0 = time.monotonic()

def g(label, url, hdrs=HJ, to=8):
    el = time.monotonic() - t0
    if el > 55:
        log.info("BUDGET at %s", label)
        return 0, None
    try:
        r = cr.get(url, headers=hdrs, impersonate="chrome", timeout=to)
        log.info("%-30s %d %4.1fs %7dB", label, r.status_code, time.monotonic()-t0, len(r.content))
        return r.status_code, r
    except Exception as e:
        log.info("%-30s ERR %s", label, str(e)[:70])
        return 0, None

# === 1) CARD.JSON — does it still work? ===
log.info("=== 1) CARD.JSON ===")
u = f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/info/ru/card.json"
st, r = g("card_json", u)
if st == 200 and r:
    try:
        d = r.json()
        results["card_json"] = {"keys": list(d.keys()), "nm_id": d.get("nm_id"),
                                "imt_name": d.get("imt_name", "")[:80],
                                "media": d.get("media"), "selling": d.get("selling")}
        log.info("  *** card.json OK: %d keys", len(d))
    except Exception as e:
        log.info("  parse err: %s", str(e)[:60])

# === 2) PRIMARY IMAGE — still 200? ===
log.info("=== 2) PRIMARY IMAGE ===")
u = f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/images/S800/S800.jpg"
st, r = g("img_primary", u, HH, 6)
if st == 200 and r:
    results["img_primary"] = {"bytes": len(r.content), "ct": r.headers.get("Content-Type","")}
    log.info("  *** PRIMARY OK: %dB %s", len(r.content), r.headers.get("Content-Type",""))
elif st == 404:
    results["img_primary"] = {"status": 404, "body": r.text[:200] if r else ""}
    log.info("  *** PRIMARY 404: %s", (r.text[:100] if r else ""))

# Also try S100 (smaller) and S120
for sz in ["S100", "S120"]:
    u = f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/images/{sz}/{sz}.jpg"
    st, r = g(f"img_{sz}", u, HH, 5)
    if st == 200 and r and len(r.content) > 200:
        results[f"img_{sz}"] = {"bytes": len(r.content)}
        log.info("  *** %s OK: %dB", sz, len(r.content))

# === 3) PRICE — 3 NEW approaches ===
log.info("=== 3) PRICE (new) ===")
price_eps = [
    # 3a: basket CDN with different path patterns
    ("b_info_price", f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/info/ru/price.json"),
    ("b_price_ru", f"https://{BASKET}/vol{VOL}/part{PART}/{NM}/price/ru.json"),
    ("b_static", f"https://static-basket-{VOL//100}.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/card.json"),
    # 3b: web api v2 (not v1)
    ("web_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={NM}"),
    # 3c: basket CDN "cards" endpoint (some WB CDNs expose /cards/)
    ("b_cards", f"https://{BASKET}/cards/v2/detail?nm={NM}&curr=rub"),
    # 3d: Try the "common" basket host pattern
    ("b_common", f"https://common.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/card.json"),
]
for lbl, url in price_eps:
    st, r = g(lbl, url)
    if st == 200 and r:
        try:
            d = r.json()
            results[f"price_{lbl}"] = d
            # Check for price fields
            prods = d.get("products") or d.get("data", {}).get("items") or []
            if prods:
                p = prods[0]
                sz = p.get("sizes", [])
                if sz and sz[0].get("price"):
                    log.info("  *** PRICE FOUND via %s: %s", lbl, sz[0]["price"])
            elif "price" in str(d).lower()[:500]:
                log.info("  *** price-like data in %s", lbl)
        except Exception:
            results[f"price_{lbl}_raw"] = r.text[:300]
    time.sleep(0.5)

# === 4) HTML with different Accept (maybe 498 goes away) ===
log.info("=== 4) HTML ALT ===")
HH2 = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
       "Referer": "https://www.wildberries.ru/",
       "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
       "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
       "Upgrade-Insecure-Requests": "1"}
u = f"https://www.wildberries.ru/catalog/{NM}/detail.aspx"
st, r = g("html_alt", u, HH2, 12)
if st == 200 and r:
    html = r.text
    results["html_alt"] = {"size": len(html)}
    # Quick price check
    prices = re.findall(r'(\d{3,8}[.,]?\d{0,2})\s*₽', html)
    if prices:
        results["html_alt"]["prices"] = prices[:5]
    # JSON-LD
    for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.I|re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            results["html_alt"]["jsonld"] = ld
            break
        except: pass
elif st in (403, 498):
    results["html_alt"] = {"status": st, "body": r.text[:300] if r else ""}

# === SAVE ===
out = Path(__file__).parent / "diag_wb_v12_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. %d keys ===", time.monotonic()-t0, len(results))
