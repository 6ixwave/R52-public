"""Диагностика WB v8: скан basket-01..60 для фото.

Цель: найти рабочий host для S800.jpg двух товаров.
"""
import json
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_wb_v8")

from curl_cffi import requests as cffi_req

# Два товара
PRODUCTS = [
    {"nm_id": 199298871, "vol": 1992, "part": 199298},
    {"nm_id": 1097975275, "vol": 10979, "part": 1097975},
]

results = {}

def http_get(url, headers=None, timeout=8, label=""):
    kw = {"timeout": timeout, "impersonate": "chrome"}
    if headers:
        kw["headers"] = headers
    try:
        resp = cffi_req.get(url, **kw)
        clen = len(resp.content) if resp.status_code == 200 else 0
        ct = resp.headers.get("Content-Type", "")
        log.info("[%s] %d %s %dB", label, resp.status_code, ct[:30], clen)
        return resp.status_code, clen, ct
    except Exception as e:
        log.warning("[%s] ERR: %s", label, str(e)[:80])
        return 0, 0, ""

photo_headers = {
    "Accept": "image/*,*/*;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

# ============================================================
# СКАН basket-01..60 для каждого товара
# ============================================================
for prod in PRODUCTS:
    nm = prod["nm_id"]
    vol = prod["vol"]
    part = prod["part"]
    log.info("=" * 60)
    log.info("PRODUCT %d (vol=%d, part=%d)", nm, vol, part)
    log.info("=" * 60)

    found_hosts = []
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        # Паттерн 1: /images/S800/S800.jpg
        url1 = f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800.jpg"
        s1, c1, ct1 = http_get(url1, headers=photo_headers, label=f"b{n:02d}_S800")
        if s1 == 200 and c1 > 500:
            found_hosts.append({"host": host, "pattern": "S800/S800.jpg", "bytes": c1, "ct": ct1})
            log.info("  FOUND: %s (%dB)", host, c1)
            # Проверить S800_2
            url2 = f"https://{host}/vol{vol}/part{part}/{nm}/images/S800/S800_2.jpg"
            s2, c2, ct2 = http_get(url2, headers=photo_headers, label=f"b{n:02d}_S800_2")
            if s2 == 200 and c2 > 500:
                found_hosts.append({"host": host, "pattern": "S800/S800_2.jpg", "bytes": c2, "ct": ct2})
            # S120
            url3 = f"https://{host}/vol{vol}/part{part}/{nm}/images/S120/S120.jpg"
            s3, c3, ct3 = http_get(url3, headers=photo_headers, label=f"b{n:02d}_S120")
            if s3 == 200 and c3 > 500:
                found_hosts.append({"host": host, "pattern": "S120/S120.jpg", "bytes": c3, "ct": ct3})
            break  # нашли host, дальше не ищем
        time.sleep(0.2)

    if not found_hosts:
        log.info("  No image host found for %d", nm)
    results[str(nm)] = found_hosts

# ============================================================
# ДОП: проверить card.json на том же host где нашлось фото
# (подтверждение что card и images могут быть на разных hosts)
# ============================================================
log.info("=" * 60)
log.info("CROSS-CHECK: card.json vs image host")
log.info("=" * 60)

for prod in PRODUCTS:
    nm = prod["nm_id"]
    vol = prod["vol"]
    part = prod["part"]
    hosts = results.get(str(nm), [])
    for h in hosts[:1]:
        host = h["host"]
        card_url = f"https://{host}/vol{vol}/part{part}/{nm}/info/ru/card.json"
        s, c, ct = http_get(card_url, headers={"Accept": "application/json"}, label=f"card_{host}")
        log.info("  card.json on %s: %d %dB", host, s, c)

# ============================================================
# SUMMARY
# ============================================================
log.info("=" * 60)
log.info("SUMMARY")
log.info("=" * 60)
for k, v in results.items():
    log.info("  %s: %d hosts found", k, len(v))
    for h in v:
        log.info("    %s %s (%dB)", h["host"], h["pattern"], h["bytes"])

with open("diag_wb_v8_out.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
log.info("Saved: diag_wb_v8_out.json")
print("DONE")
