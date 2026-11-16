#!/usr/bin/env python3
"""v129: WB media patterns + price endpoints (фокусный).

Цели:
1. Найти реальные URL фото на basket CDN (не только S800/S800.jpg).
2. Проверить альтернативные price endpoints.
3. Проверить HTML с curl_cffi (impersonate=chrome) для цен.
4. Бюджет: 120 сек.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("v129")

OUT_DIR = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
DEADLINE = time.monotonic() + 115


def remaining():
    return max(0, DEADLINE - time.monotonic())


def save_json(data, fname):
    path = os.path.join(OUT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("  saved -> %s", fname)


# ================================================================
# WB MEDIA PATTERNS
# ================================================================
def test_wb_media():
    log.info("=" * 60)
    log.info("WB MEDIA: find real photo URLs on basket CDN")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector, JSON_HEADERS
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    # Test card: 199298871 (photo_count=6)
    nm_id = 199298871
    vol = nm_id // 100000  # 1992
    part = nm_id // 1000   # 199298

    host = wb._find_basket_host(nm_id, vol,
                                f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json")
    if not host:
        log.info("  no host, abort")
        return []

    base = f"https://{host}/vol{vol}/part{part}/{nm_id}"
    log.info("  base: %s", base)

    # Try various image URL patterns
    patterns = [
        # Classic S800
        f"{base}/images/S800/S800.jpg",
        # Numbered photos (common WB pattern)
        f"{base}/images/S800/1.jpg",
        f"{base}/images/S800/2.jpg",
        f"{base}/images/S800/3.jpg",
        f"{base}/images/S800/4.jpg",
        f"{base}/images/S800/5.jpg",
        f"{base}/images/S800/6.jpg",
        # S1200
        f"{base}/images/S1200/S1200.jpg",
        f"{base}/images/S1200/1.jpg",
        f"{base}/images/S1200/2.jpg",
        # S500
        f"{base}/images/S500/S500.jpg",
        f"{base}/images/S500/1.jpg",
        # Without size prefix
        f"{base}/images/1.jpg",
        f"{base}/images/2.jpg",
        # wbstatic pattern
        f"https://ir.wbstatic.net/big/{nm_id}.jpg",
        f"https://ir.wbstatic.net/preview/{nm_id}.jpg",
        f"https://ir.wbstatic.net/big2/{nm_id}.jpg",
    ]

    found = []
    for i, url in enumerate(patterns):
        if remaining() < 20:
            break
        try:
            r = wb._http_get(url, headers=JSON_HEADERS, timeout=8, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                ct = r.headers.get("Content-Type", "?") if sc == 200 else "?"
                log.info("  [%2d] HTTP %d %6dB %-20s %s", i, sc, sz, ct, url[-60:])
                if sc == 200 and sz > 500:
                    found.append({"url": url, "size": sz, "ct": ct})
        except Exception as e:
            log.info("  [%2d] ERR %s", i, e)

    log.info("  FOUND: %d real images", len(found))
    for f in found:
        log.info("    %s (%dB)", f["url"][-60:], f["size"])

    # Also check card.json media field for hints
    log.info("\n  --- card.json media field ---")
    card_url = f"{base}/info/ru/card.json"
    r = wb._http_get(card_url, headers=JSON_HEADERS, timeout=10, fast=True)
    if r and r.status_code == 200:
        cd = r.json()
        media = cd.get("media", {})
        log.info("  media: %s", json.dumps(media, ensure_ascii=False))
        # Check for any image URLs in the full card
        card_str = json.dumps(cd)
        import re
        img_urls = re.findall(r'https://[^\s"\'\\]+?\.(?:jpg|jpeg|png|webp)', card_str)
        if img_urls:
            log.info("  image URLs in card.json: %d", len(img_urls))
            for u in img_urls[:5]:
                log.info("    %s", u)
        # Check colors/full_colors for image hints
        fc = cd.get("full_colors", [])
        if fc:
            log.info("  full_colors: %s", json.dumps(fc[:3], ensure_ascii=False))

    return found


# ================================================================
# WB PRICE ENDPOINTS
# ================================================================
def test_wb_price():
    log.info("\n" + "=" * 60)
    log.info("WB PRICE: try alternative endpoints")
    log.info("=" * 60)

    from r52.collectors.wb import WBCollector, JSON_HEADERS
    wb = WBCollector(data_dir=Path(OUT_DIR), use_proxies=False)

    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000

    # Alternative price endpoints
    price_urls = [
        # basket CDN price (same host as card.json)
        ("basket_price", None),  # fill after host found
        # catalog.wb.ru with different params
        ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        # card.wb.ru with appType=2 (mobile)
        ("card_mobile", f"https://card.wb.ru/cards/v1/detail?appType=2&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        # web API v2
        ("web_v2", f"https://www.wildberries.ru/web/api/v2/card/{nm_id}"),
        # Price via basket CDN (price.json or similar)
        ("basket_price_json", None),
        # HTML with curl_cffi (impersonate)
        ("html_detail", f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"),
    ]

    # Find basket host first
    host = wb._find_basket_host(nm_id, vol,
                                f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json")
    if host:
        base = f"https://{host}/vol{vol}/part{part}/{nm_id}"
        price_urls[0] = ("basket_price", f"{base}/info/ru/price.json")
        price_urls[4] = ("basket_price_json", f"{base}/info/ru/price.json")

    results = []
    for label, url in price_urls:
        if not url or remaining() < 15:
            continue
        log.info("\n  --- %s ---", label)
        try:
            r = wb._http_get(url, headers=JSON_HEADERS, timeout=12, fast=True)
            if r:
                sc = r.status_code
                sz = len(r.content) if sc == 200 else 0
                log.info("  HTTP %d, %dB", sc, sz)
                if sc == 200:
                    ct = r.headers.get("Content-Type", "")
                    if "json" in ct:
                        data = r.json()
                        log.info("  JSON keys: %s", list(data.keys())[:10] if isinstance(data, dict) else type(data))
                        # Look for price fields
                        ds = json.dumps(data)
                        import re
                        prices = re.findall(r'"(?:price|priceU|priceD|basic|product|discount|sale)"\s*:\s*(\d+)', ds)
                        if prices:
                            log.info("  PRICES found: %s", prices[:10])
                        results.append({"label": label, "status": sc, "prices": prices[:5]})
                    elif "html" in ct:
                        text = r.text[:5000]
                        # Look for price in HTML
                        import re
                        price_matches = re.findall(r'(\d[\d\s]{2,12})\s*(?:руб|₽|RUB)', text)
                        log.info("  HTML prices: %s", price_matches[:5])
                        # Look for JSON-LD
                        ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
                        if ld:
                            for block in ld:
                                try:
                                    ld_data = json.loads(block)
                                    log.info("  JSON-LD: %s", json.dumps(ld_data, ensure_ascii=False)[:300])
                                except:
                                    pass
                        # Look for data attributes
                        data_price = re.findall(r'data-[^=]*price[^=]*="([^"]+)"', text, re.I)
                        if data_price:
                            log.info("  data-price attrs: %s", data_price[:5])
                        results.append({"label": label, "status": sc, "prices": price_matches[:5]})
                else:
                    results.append({"label": label, "status": sc})
            else:
                log.info("  None (timeout/error)")
                results.append({"label": label, "status": "NONE"})
        except Exception as e:
            log.info("  ERR: %s", e)
            results.append({"label": label, "status": f"ERR:{e}"})

    log.info("\n  PRICE RESULTS: %s", results)
    return results


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    t0 = time.monotonic()
    log.info("v129 started, budget=115s")

    media_res = []
    price_res = []

    try:
        media_res = test_wb_media()
    except Exception as e:
        log.exception("MEDIA section error: %s", e)

    if remaining() > 30:
        try:
            price_res = test_wb_price()
        except Exception as e:
            log.exception("PRICE section error: %s", e)

    total = time.monotonic() - t0
    log.info("\n" + "=" * 60)
    log.info("v129 DONE in %.1fs", total)
    log.info("MEDIA: %d found", len(media_res))
    log.info("PRICE: %s", price_res)
    log.info("=" * 60)
