#!/usr/bin/env python3
"""v130: WB media (correct headers + patterns) + price (minimal headers, alt dest).

Цели:
1. Фото: правильные Accept-заголовки, nm_id как имя, alt CDN hosts.
2. Цены: минимальные заголовки, разные dest, mobile API.
3. Бюджет: 120 сек.
"""
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("v130")

OUT_DIR = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
DEADLINE = time.monotonic() + 115


def remaining():
    return max(0, DEADLINE - time.monotonic())


# ================================================================
# WB MEDIA
# ================================================================
def test_wb_media():
    log.info("=" * 60)
    log.info("WB MEDIA: correct headers + alt patterns")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000

    host = wb._find_basket_host(nm_id, vol,
                                f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json")
    if not host:
        log.info("  no host")
        return []

    base = f"https://{host}/vol{vol}/part{part}/{nm_id}"
    log.info("  host=%s base=%s", host, base)

    # Proper image headers
    IMG_HEADERS = {
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.wildberries.ru/",
    }

    # Patterns to try
    patterns = [
        # S800 with nm_id as filename
        f"{base}/images/S800/{nm_id}.jpg",
        # S800 with color nm_ids
        f"{base}/images/S800/173121919.jpg",
        f"{base}/images/S800/173036660.jpg",
        # S800 classic
        f"{base}/images/S800/S800.jpg",
        # S1200
        f"{base}/images/S1200/{nm_id}.jpg",
        f"{base}/images/S1200/S1200.jpg",
        # S500
        f"{base}/images/S500/{nm_id}.jpg",
        # No size dir
        f"{base}/images/{nm_id}.jpg",
        f"{base}/images/1.jpg",
        # wbstatic variants (domain might be different now)
        f"https://static.wbstatic.net/big/{nm_id}.jpg",
        f"https://static.wbstatic.net/preview/{nm_id}.jpg",
        f"https://ir.wbstatic.net/big/{nm_id}.jpg",
        # wbx2.ru
        f"https://wbx2.ru/{nm_id}/{nm_id}.jpg",
        f"https://wbx2.ru/{nm_id}/1.jpg",
        # Direct wildberries.ru
        f"https://www.wildberries.ru/images/{nm_id}/{nm_id}.jpg",
        # basket CDN with /info/ prefix
        f"{base}/info/ru/images/{nm_id}.jpg",
    ]

    found = []
    for i, url in enumerate(patterns):
        if remaining() < 15:
            break
        try:
            r = wb._http_get(url, headers=IMG_HEADERS, timeout=8, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                ct = ""
                try:
                    ct = r.headers.get("Content-Type", "?")
                except:
                    pass
                log.info("  [%2d] HTTP %d %7dB %-25s %s", i, sc, sz, ct[:25], url[-65:])
                if sc == 200 and sz > 500:
                    found.append({"url": url, "size": sz, "ct": ct})
            else:
                log.info("  [%2d] None (timeout/err) %s", i, url[-65:])
        except Exception as e:
            log.info("  [%2d] ERR %s: %s", i, type(e).__name__, str(e)[:60])

    log.info("  MEDIA FOUND: %d", len(found))
    for f in found:
        log.info("    %s (%dB)", f["url"][-65:], f["size"])

    # Try images.json on basket CDN
    log.info("\n  --- images.json / media.json probes ---")
    for probe in [
        f"{base}/info/ru/images.json",
        f"{base}/info/ru/media.json",
        f"{base}/images.json",
        f"{base}/info/ru/gallery.json",
    ]:
        try:
            r = wb._http_get(probe, headers={"Accept": "application/json"}, timeout=8, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                log.info("  %s -> HTTP %d %dB", probe[-50:], sc, sz)
                if sc == 200 and sz > 10:
                    try:
                        d = r.json()
                        log.info("    JSON: %s", json.dumps(d, ensure_ascii=False)[:300])
                    except:
                        log.info("    body[:200]: %s", r.text[:200])
        except Exception as e:
            log.info("  %s ERR: %s", probe[-50:], e)

    return found


# ================================================================
# WB PRICE
# ================================================================
def test_wb_price():
    log.info("\n" + "=" * 60)
    log.info("WB PRICE: minimal headers + alt dest + mobile")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000

    # Minimal headers (no Referer/Origin that might trigger anti-bot)
    MIN_HEADERS = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    # Different dest values (region codes)
    dests = ["-1257786", "1", "-1", "25"]

    price_urls = []
    for dest in dests:
        price_urls.append((
            f"card_v1_d{dest}",
            f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest={dest}&spp=30&nm={nm_id}",
        ))
    # Mobile app type
    price_urls.append((
        "card_v1_mobile",
        f"https://card.wb.ru/cards/v1/detail?appType=2&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
    ))
    # catalog.wb.ru minimal
    price_urls.append((
        "catalog_min",
        f"https://catalog.wb.ru/cards/v1/detail?curr=rub&nm={nm_id}",
    ))
    # Web API v1 (different path)
    price_urls.append((
        "web_v1",
        f"https://www.wildberries.ru/web/api/v1/catalog/cards/v1/detail?nm={nm_id}",
    ))
    # basket CDN price
    host = wb._find_basket_host(nm_id, vol,
                                f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json")
    if host:
        base = f"https://{host}/vol{vol}/part{part}/{nm_id}"
        price_urls.append(("basket_price", f"{base}/info/ru/price.json"))
        price_urls.append(("basket_price2", f"{base}/price.json"))

    results = []
    for label, url in price_urls:
        if remaining() < 12:
            break
        log.info("\n  --- %s ---", label)
        try:
            r = wb._http_get(url, headers=MIN_HEADERS, timeout=12, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                log.info("  HTTP %d, %dB", sc, sz)
                if sc == 200:
                    try:
                        data = r.json()
                        ds = json.dumps(data)
                        # Find price-like numbers
                        prices = re.findall(r'"(?:price|priceU|priceD|basic|product|old|sale|card|wallet|bankCard)"\s*:\s*(\d+)', ds)
                        log.info("  JSON keys: %s", list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__)
                        if prices:
                            log.info("  PRICES: %s", prices[:10])
                        # Also look for sizes[].price
                        prods = data.get("products") or data.get("data", {}).get("items", [])
                        if prods:
                            p = prods[0]
                            sizes = p.get("sizes", [])
                            if sizes:
                                log.info("  sizes[0].price: %s", json.dumps(sizes[0].get("price"), ensure_ascii=False)[:200])
                        results.append({"label": label, "status": sc, "prices": prices[:5]})
                    except Exception as e:
                        log.info("  parse: %s", e)
                        results.append({"label": label, "status": sc, "err": str(e)[:50]})
                else:
                    results.append({"label": label, "status": sc})
            else:
                results.append({"label": label, "status": "NONE"})
        except Exception as e:
            log.info("  ERR: %s", e)
            results.append({"label": label, "status": f"ERR:{str(e)[:40]}"})

    log.info("\n  PRICE RESULTS: %s", json.dumps(results, ensure_ascii=False)[:500])
    return results


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    t0 = time.monotonic()
    log.info("v130 started, budget=115s")

    media_res = []
    price_res = []

    try:
        media_res = test_wb_media()
    except Exception as e:
        log.exception("MEDIA error: %s", e)

    if remaining() > 30:
        try:
            price_res = test_wb_price()
        except Exception as e:
            log.exception("PRICE error: %s", e)

    total = time.monotonic() - t0
    log.info("\n" + "=" * 60)
    log.info("v130 DONE in %.1fs", total)
    log.info("MEDIA: %d found", len(media_res))
    for m in media_res:
        log.info("  %s (%dB)", m["url"][-60:], m["size"])
    log.info("PRICE: %s", json.dumps(price_res, ensure_ascii=False)[:400])
    log.info("=" * 60)
