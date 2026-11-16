"""Диагностика WB v9: быстрый скан basket-01..60 для фото.

Оптимизация: timeout=5s, только S800.jpg, 0.1s sleep.
Цель: найти image host для 2 товаров.
"""
import json
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_wb_v9")

from curl_cffi import requests as cffi_req

PRODUCTS = [
    {"nm_id": 199298871, "vol": 1992, "part": 199298, "label": "kokteyl"},
    {"nm_id": 1097975275, "vol": 10979, "part": 1097975, "label": "iphone"},
]

photo_headers = {
    "Accept": "image/*,*/*;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

results = {}
t0 = time.monotonic()

for prod in PRODUCTS:
    nm = prod["nm_id"]
    vol = prod["vol"]
    part = prod["part"]
    label = prod["label"]
    log.info("=== %s (%d) ===", label, nm)

    found = []
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800.jpg"
        try:
            resp = cffi_req.get(url, headers=photo_headers, timeout=5, impersonate="chrome")
            clen = len(resp.content) if resp.status_code == 200 else 0
            if resp.status_code == 200 and clen > 500:
                found.append({"host": host, "n": n, "bytes": clen})
                log.info("  FOUND %s: %dB", host, clen)
                # Доп. фото
                for suffix in ["S800_2", "S800_3", "S120"]:
                    u2 = f"https://{host}/vol{vol}/part{part}/{nm}/images/{suffix.split('_')[0]}/{suffix}.jpg"
                    try:
                        r2 = cffi_req.get(u2, headers=photo_headers, timeout=4, impersonate="chrome")
                        c2 = len(r2.content) if r2.status_code == 200 else 0
                        if r2.status_code == 200 and c2 > 200:
                            found.append({"host": host, "n": n, "pattern": suffix, "bytes": c2})
                            log.info("    + %s: %dB", suffix, c2)
                    except Exception:
                        pass
                    time.sleep(0.1)
                break
        except Exception as e:
            log.debug("  b%02d: %s", n, str(e)[:60])
        time.sleep(0.1)

    if not found:
        log.info("  NOT FOUND for %s", nm)
    results[label] = found

elapsed = time.monotonic() - t0
log.info("Total: %.1fs", elapsed)
log.info("Results: %s", json.dumps(results, ensure_ascii=False, indent=2))

with open("diag_wb_v9_out.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("DONE")
