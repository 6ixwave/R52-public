#!/usr/bin/env python3
"""v131: WB PRICE focus (cached host, minimal headers, alt dest) + MEDIA (imt_id, colors).

Бюджет: 120 сек. Host cached (basket-13 for vol 1992).
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
log = logging.getLogger("v131")

OUT_DIR = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
DEADLINE = time.monotonic() + 115

# Pre-cache basket hosts (from v128/v129/v130)
from r52.collectors import wb as wb_mod
wb_mod._basket_host_cache[1992] = 13  # vol 1992 -> basket-13
wb_mod._basket_host_cache[2596] = 16  # vol 2596 -> basket-16


def remaining():
    return max(0, DEADLINE - time.monotonic())


# ================================================================
# WB PRICE — primary focus
# ================================================================
def test_wb_price():
    log.info("=" * 60)
    log.info("WB PRICE: minimal headers, alt dest, mobile, basket")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000
    host = f"basket-{wb_mod._basket_host_cache[vol]:02d}.wbbasket.ru"
    base = f"https://{host}/vol{vol}/part{part}/{nm_id}"

    # Minimal headers — no Referer/Origin (might trigger anti-bot)
    MIN_H = {
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    # Standard JSON headers (for comparison)
    STD_H = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/",
        "Origin": "https://www.wildberries.ru",
    }

    # Build URL list
    urls = []

    # 1. card.wb.ru with various dest
    for dest in ["-1257786", "1", "-1", "25", "77"]:
        urls.append((f"card_v1_d{dest}",
                     f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest={dest}&spp=30&nm={nm_id}",
                     MIN_H))

    # 2. card.wb.ru mobile
    urls.append(("card_v1_mobile",
                 f"https://card.wb.ru/cards/v1/detail?appType=2&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
                 MIN_H))

    # 3. catalog.wb.ru minimal (no appType)
    urls.append(("catalog_min",
                 f"https://catalog.wb.ru/cards/v1/detail?curr=rub&nm={nm_id}",
                 MIN_H))

    # 4. catalog.wb.ru with appType=1
    urls.append(("catalog_v1",
                 f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
                 MIN_H))

    # 5. Web API v1
    urls.append(("web_v1",
                 f"https://www.wildberries.ru/web/api/v1/catalog/cards/v1/detail?nm={nm_id}",
                 MIN_H))

    # 6. Web API v2
    urls.append(("web_v2",
                 f"https://www.wildberries.ru/web/api/v2/card/{nm_id}",
                 MIN_H))

    # 7. basket CDN price.json
    urls.append(("basket_price", f"{base}/info/ru/price.json", MIN_H))

    # 8. basket CDN with /cards/ path
    urls.append(("basket_cards", f"{base}/info/ru/cards.json", MIN_H))

    # 9. card.wb.ru with STD headers (comparison)
    urls.append(("card_v1_std",
                 f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
                 STD_H))

    results = []
    for label, url, hdrs in urls:
        if remaining() < 10:
            log.info("  budget low, stopping")
            break
        log.info("\n  --- %s ---", label)
        try:
            r = wb._http_get(url, headers=hdrs, timeout=10, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                log.info("  HTTP %d, %dB", sc, sz)
                if sc == 200:
                    try:
                        data = r.json()
                        ds = json.dumps(data)
                        prices = re.findall(
                            r'"(?:price|priceU|priceD|basic|product|old|sale|card|wallet|bankCard|priceTotal)"\s*:\s*(\d+)',
                            ds)
                        log.info("  keys: %s", list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__)
                        if prices:
                            log.info("  PRICES: %s", prices[:10])
                        prods = data.get("products") or data.get("data", {}).get("items", [])
                        if prods:
                            p = prods[0]
                            sizes = p.get("sizes", [])
                            if sizes:
                                log.info("  sizes[0].price: %s",
                                         json.dumps(sizes[0].get("price"), ensure_ascii=False)[:200])
                                log.info("  sizes[0].qty: %s", sizes[0].get("qty"))
                        results.append({"label": label, "sc": sc, "prices": prices[:5], "sz": sz})
                    except Exception as e:
                        log.info("  parse err: %s", e)
                        results.append({"label": label, "sc": sc, "err": str(e)[:40]})
                else:
                    results.append({"label": label, "sc": sc})
            else:
                results.append({"label": label, "sc": "NONE"})
        except Exception as e:
            log.info("  ERR: %s", e)
            results.append({"label": label, "sc": f"ERR"})

    log.info("\n  === PRICE SUMMARY ===")
    for r in results:
        log.info("  %-18s -> %s", r["label"], r.get("sc"))
    return results


# ================================================================
# WB MEDIA — imt_id, colors, alt paths
# ================================================================
def test_wb_media():
    log.info("\n" + "=" * 60)
    log.info("WB MEDIA: imt_id, colors, alt CDN paths")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    nm_id = 199298871
    imt_id = 2227481309  # from card_199298871 (need to verify)
    vol = nm_id // 100000
    part = nm_id // 1000
    host = f"basket-{wb_mod._basket_host_cache[vol]:02d}.wbbasket.ru"
    base = f"https://{host}/vol{vol}/part{part}/{nm_id}"

    IMG_H = {
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    # First: fetch card.json to get imt_id and colors
    card_url = f"{base}/info/ru/card.json"
    r = wb._http_get(card_url, headers={"Accept": "application/json"}, timeout=10, fast=True)
    imt_id = nm_id  # fallback
    colors = [nm_id]
    if r and r.status_code == 200:
        try:
            cd = r.json()
            imt_id = cd.get("imt_id", nm_id)
            colors = cd.get("colors", [nm_id])
            fc = cd.get("full_colors", [])
            fc_ids = [c.get("nm_id") for c in fc if isinstance(c, dict)]
            log.info("  card: imt_id=%s colors=%s full_colors=%s", imt_id, colors, fc_ids)
        except:
            pass

    patterns = [
        # imt_id based
        f"{base}/images/S800/{imt_id}.jpg",
        f"{base}/images/S800/{imt_id}/1.jpg",
        # color nm_id based (subfolder)
        f"{base}/images/S800/{colors[0]}/1.jpg",
        f"{base}/images/S800/{colors[0]}/{colors[0]}.jpg",
        # Standard with /S800/ and index
        f"{base}/images/S800/0.jpg",
        f"{base}/images/S800/00.jpg",
        # Different size dirs
        f"{base}/images/S200/{nm_id}.jpg",
        f"{base}/images/S100/{nm_id}.jpg",
        # /img/ instead of /images/
        f"{base}/img/S800/{nm_id}.jpg",
        f"{base}/img/{nm_id}.jpg",
        # /media/
        f"{base}/media/S800/{nm_id}.jpg",
        # No part in path
        f"https://{host}/vol{vol}/{nm_id}/images/S800/{nm_id}.jpg",
        # Flat: vol/nm_id
        f"https://{host}/{nm_id}/images/S800/{nm_id}.jpg",
    ]

    found = []
    for i, url in enumerate(patterns):
        if remaining() < 10:
            break
        try:
            r = wb._http_get(url, headers=IMG_H, timeout=8, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                log.info("  [%2d] HTTP %d %7dB %s", i, sc, sz, url[-60:])
                if sc == 200 and sz > 500:
                    found.append({"url": url, "size": sz})
            else:
                log.info("  [%2d] None %s", i, url[-60:])
        except Exception as e:
            log.info("  [%2d] ERR %s", i, str(e)[:50])

    log.info("  MEDIA FOUND: %d", len(found))
    for f in found:
        log.info("    %s (%dB)", f["url"][-60:], f["size"])
    return found


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    t0 = time.monotonic()
    log.info("v131 started, budget=115s")

    price_res = []
    media_res = []

    # PRICE first (higher priority)
    try:
        price_res = test_wb_price()
    except Exception as e:
        log.exception("PRICE error: %s", e)

    if remaining() > 25:
        try:
            media_res = test_wb_media()
        except Exception as e:
            log.exception("MEDIA error: %s", e)

    total = time.monotonic() - t0
    log.info("\n" + "=" * 60)
    log.info("v131 DONE in %.1fs", total)
    log.info("PRICE: %d endpoints tested", len(price_res))
    for p in price_res:
        log.info("  %-18s -> %s %s", p["label"], p.get("sc"), p.get("prices", ""))
    log.info("MEDIA: %d found", len(media_res))
    for m in media_res:
        log.info("  %s (%dB)", m["url"][-60:], m["size"])
    log.info("=" * 60)
