"""WB v13: 75s. Images: 10 basket hosts x sizes + wbx CDN. Price: basket CDN paths."""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("wb13")

from curl_cffi import requests as cr

NM = 199298871
VOL = NM // 100000  # 1992
PART = NM // 1000   # 199298

HJ = {"Accept": "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://www.wildberries.ru/"}
HH = {"Accept": "image/*,*/*", "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://www.wildberries.ru/"}

results = {"images": [], "price": {}, "card_json": None}
t0 = time.monotonic()

def g(label, url, hdrs=HH, to=6):
    el = time.monotonic() - t0
    if el > 70:
        log.info("BUDGET at %s", label)
        return 0, None
    try:
        r = cr.get(url, headers=hdrs, impersonate="chrome", timeout=to)
        log.info("%-32s %d %4.1fs %7dB", label, r.status_code, time.monotonic()-t0, len(r.content))
        return r.status_code, r
    except Exception as e:
        log.info("%-32s ERR %s", label, str(e)[:60])
        return 0, None

# === 1) CARD.JSON (confirm still works) ===
log.info("=== 1) CARD.JSON ===")
u = f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/card.json"
st, r = g("card_json", u, HJ, 8)
if st == 200 and r:
    try:
        d = r.json()
        results["card_json"] = {"keys": list(d.keys()), "media": d.get("media"),
                                "colors_count": len(d.get("colors", [])),
                                "photo_count": d.get("media", {}).get("photo_count", 0)}
        log.info("  *** OK: %d keys, photos=%d, colors=%d",
                 len(d), d.get("media",{}).get("photo_count",0), len(d.get("colors",[])))
    except Exception as e:
        log.info("  parse: %s", str(e)[:60])

# === 2) IMAGES: 10 basket hosts x S800 ===
log.info("=== 2) IMAGES: basket hosts ===")
sizes = ["S800", "S510", "S340", "S240", "S120"]
hosts_to_try = [13, 1, 5, 10, 20, 30, 40, 50, 2, 7]
found_img = False

for h in hosts_to_try:
    if found_img:
        break
    host = f"basket-{h:02d}.wbbasket.ru"
    for sz in sizes[:3]:  # S800, S510, S340
        u = f"https://{host}/vol{VOL}/part{PART}/{NM}/images/{sz}/{sz}.jpg"
        st, r = g(f"img_b{h:02d}_{sz}", u, HH, 5)
        if st == 200 and r and len(r.content) > 500:
            results["images"].append({"host": host, "size": sz, "url": u, "bytes": len(r.content)})
            log.info("  *** FOUND: %s %s %dB", host, sz, len(r.content))
            found_img = True
            break
    time.sleep(0.3)

# === 3) IMAGES: wbx-content CDN ===
log.info("=== 3) IMAGES: wbx CDN ===")
for sz in ["S800", "S510", "S340"]:
    for prefix in ["wbx-content-v2", "wbx-content", "static-basket"]:
        u = f"https://{prefix}.wbbasket.ru/vol{VOL}/part{PART}/{NM}/images/{sz}/{sz}.jpg"
        st, r = g(f"img_wbx_{prefix}_{sz}", u, HH, 5)
        if st == 200 and r and len(r.content) > 500:
            results["images"].append({"host": prefix, "size": sz, "url": u, "bytes": len(r.content)})
            log.info("  *** WBX FOUND: %s %s", prefix, sz)
            break
        if st not in (404, 0):
            break
    time.sleep(0.3)

# === 4) IMAGES: "photo" path (different from "images") ===
log.info("=== 4) IMAGES: photo path ===")
for sz in ["S800", "S510"]:
    u = f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM}/photo/{sz}.jpg"
    st, r = g(f"img_photo_{sz}", u, HH, 5)
    if st == 200 and r and len(r.content) > 500:
        results["images"].append({"path": "photo", "size": sz, "url": u, "bytes": len(r.content)})
        log.info("  *** PHOTO PATH OK: %s", sz)
    time.sleep(0.3)

# === 5) PRICE: basket CDN sub-paths ===
log.info("=== 5) PRICE: basket CDN ===")
price_paths = [
    ("info_ru", f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/"),
    ("price_info", f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM}/price/info.json"),
    ("card_info", f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/card.json"),
    ("common_price", f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM}/common/price.json"),
    ("wbx_price", f"https://wbx-content-v2.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/price.json"),
    ("wbx_card", f"https://wbx-content-v2.wbbasket.ru/vol{VOL}/part{PART}/{NM}/info/ru/card.json"),
]
for lbl, url in price_paths:
    st, r = g(f"price_{lbl}", url, HJ, 6)
    if st == 200 and r and len(r.content) > 50:
        try:
            d = r.json()
            results["price"][lbl] = d
            # Look for price-like keys
            s = json.dumps(d)
            if "price" in s.lower() or "basic" in s.lower():
                log.info("  *** PRICE DATA in %s: %s", lbl, s[:200])
        except Exception:
            results["price"][lbl] = r.text[:300]
    time.sleep(0.5)

# === 6) PRICE: card.wb.ru with different dest ===
log.info("=== 6) PRICE: card.wb.ru variants ===")
for dest in ["-1257786", "1000000", "0", "1"]:
    u = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest={dest}&spp=30&nm={NM}"
    st, r = g(f"card_dest_{dest}", u, HJ, 6)
    if st == 200 and r:
        try:
            d = r.json()
            results["price"][f"card_dest_{dest}"] = d
            log.info("  *** card.wb dest=%s OK", dest)
        except: pass
    time.sleep(0.5)

# === SAVE ===
out = Path(__file__).parent / "diag_wb_v13_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. imgs=%d price_keys=%d ===", time.monotonic()-t0,
         len(results["images"]), len(results["price"]))
