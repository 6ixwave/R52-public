"""v132: WB media с imt_id-based vol/part + новые price-подходы.

Ключевое исправление:
  OLD: vol = nm_id // 100000, part = nm_id // 1000
  NEW: vol = imt_id // 100000, part = imt_id // 1000
  URL: basket-N.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/S800/{imt_id}.jpg

Бюджет: 110 сек.
"""
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("v132")

BUDGET = 110
START = time.monotonic()

def elapsed():
    return time.monotonic() - START

def remaining():
    return BUDGET - elapsed()

# ============================================================
# HTTP
# ============================================================
try:
    from curl_cffi import requests as cffi_req
    HAVE_CFFI = True
    log.info("curl_cffi OK")
except ImportError:
    HAVE_CFFI = False
    import requests as cffi_req
    log.warning("curl_cffi нет, requests")

JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}
HTML_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}

def http_get(url, headers=None, timeout=12, fast=False):
    if HAVE_CFFI:
        try:
            r = cffi_req.get(url, headers=headers or {}, timeout=timeout,
                             impersonate="chrome", allow_redirects=True)
            return r
        except Exception as e:
            log.warning("cffi err %s: %s", url[:60], e)
    try:
        r = cffi_req.get(url, headers=headers or {}, timeout=timeout,
                         allow_redirects=True)
        return r
    except Exception as e:
        log.warning("req err %s: %s", url[:60], e)
    return None

# ============================================================
# 1. FETCH CARD.JSON (confirmed working)
# ============================================================
def fetch_card_json(nm_id):
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        r = http_get(url, headers=JSON_H, timeout=8)
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("nm_id"):
                    log.info("card.json nm=%d host=basket-%02d imt_id=%s",
                             nm_id, n, data.get("imt_id"))
                    return data, n
            except Exception:
                pass
        if remaining() < 15:
            break
    return None, None

# ============================================================
# 2. MEDIA: imt_id-based vol/part (THE FIX)
# ============================================================
def test_media(nm_id, imt_id, basket_host_num):
    """Build media URLs using imt_id for vol/part."""
    vol = imt_id // 100000
    part = imt_id // 1000
    log.info("MEDIA nm=%d imt_id=%d -> vol=%d part=%d", nm_id, imt_id, vol, part)

    # Try the known basket host first, then scan
    hosts_to_try = [basket_host_num] + [n for n in range(1, 61) if n != basket_host_num]
    hosts_to_try = hosts_to_try[:30]  # limit to 30 hosts

    sizes = ["S800", "S400", "S200"]
    found = []

    for host_n in hosts_to_try:
        if remaining() < 10:
            log.info("MEDIA: time budget low, stopping host scan")
            break
        host = f"basket-{host_n:02d}.wbbasket.ru"
        for size in sizes:
            if remaining() < 5:
                break
            url = (f"https://{host}/vol{vol}/part{part}/{nm_id}"
                   f"/images/{size}/{imt_id}.jpg")
            r = http_get(url, headers=HTML_H, timeout=8)
            if r is not None:
                code = r.status_code
                clen = len(r.content) if r.content else 0
                log.info("  [%02d] %s %d %dB  %s", host_n, code, clen, clen,
                         f"/vol{vol}/part{part}/{nm_id}/images/{size}/{imt_id}.jpg")
                if code == 200 and clen > 500:
                    found.append({"host": host, "size": size, "url": url,
                                  "bytes": clen})
                    # Found one for this size, move to next
                    break
            time.sleep(0.3)

    # Also try without imt_id in filename (just S800.jpg)
    if not found:
        log.info("  Trying alt filename patterns...")
        for host_n in hosts_to_try[:10]:
            if remaining() < 8:
                break
            host = f"basket-{host_n:02d}.wbbasket.ru"
            for fname in [f"{imt_id}.jpg", "S800.jpg", "0.jpg"]:
                url = (f"https://{host}/vol{vol}/part{part}/{nm_id}"
                       f"/images/S800/{fname}")
                r = http_get(url, headers=HTML_H, timeout=6)
                if r is not None and r.status_code == 200 and len(r.content or b"") > 500:
                    log.info("  ALT HIT: %s (%dB)", url, len(r.content))
                    found.append({"host": host, "size": "S800", "url": url,
                                  "bytes": len(r.content)})
                    break
            time.sleep(0.3)

    log.info("MEDIA nm=%d: %d found", nm_id, len(found))
    return found

# ============================================================
# 3. PRICE: new approaches
# ============================================================
def test_price(nm_id):
    """Try multiple price endpoints with varied headers."""
    results = {}

    endpoints = [
        # card.wb.ru v2 with full params
        ("cw_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        # card.wb.ru v4
        ("cw_v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        # Web API v2
        ("web_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm_id}"),
        # Web API v1
        ("web_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
        # Search API (newer)
        ("search", f"https://www.wildberries.ru/web/api/catalog/search?query={nm_id}&nm={nm_id}"),
        # Mobile card
        ("mobile", f"https://card.wb.ru/cards/v1/detail?appType=2&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        # card.wb.ru with different dest
        ("cw_dest0", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=0&spp=30&nm={nm_id}"),
        # card.wb.ru without spp
        ("cw_nospp", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={nm_id}"),
    ]

    for label, url in endpoints:
        if remaining() < 8:
            log.info("PRICE: time low, skipping rest")
            break
        r = http_get(url, headers=JSON_H, timeout=10)
        if r is None:
            results[label] = "ERR"
            continue
        code = r.status_code
        body = r.text[:200] if r.text else ""
        clen = len(r.content or b"")
        results[label] = f"{code} {clen}B"
        log.info("  PRICE %s -> %d %dB  %s", label, code, clen, body[:100])

        if code == 200 and clen > 50:
            try:
                data = r.json()
                products = data.get("data", {}).get("items") or data.get("products") or []
                if products:
                    p = products[0]
                    sizes = p.get("sizes", [])
                    if sizes:
                        sz = sizes[0]
                        price = sz.get("price", {})
                        log.info("  PRICE HIT %s: priceU=%s priceD=%s qty=%s",
                                 label, price.get("basic"), price.get("total"), sz.get("qty"))
                        results[label] += f" priceU={price.get('basic')}"
            except Exception as e:
                log.info("  PRICE %s parse: %s", label, e)
        time.sleep(0.5)

    # Also check card.json for embedded price fields
    log.info("  Checking card.json for price fields...")
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== v132 START budget=%ds ===", BUDGET)

    # --- Product 1: 199298871 (known imt_id=158033827) ---
    nm1 = 199298871
    log.info("--- PRODUCT 1: nm_id=%d ---", nm1)
    card1, host1 = fetch_card_json(nm1)
    if card1:
        imt1 = card1.get("imt_id", 0)
        log.info("  imt_id=%s, keys=%s", imt1, list(card1.keys())[:15])
        # Check for price in card.json
        for k in ["price", "priceU", "priceD", "media", "photo_count", "has_video"]:
            if k in card1:
                log.info("  card1[%s] = %s", k, str(card1[k])[:120])
    else:
        imt1 = 158033827  # known from v131
        host1 = 13
        log.warning("  card1 fetch failed, using known imt_id=%d", imt1)

    # Media test
    media1 = test_media(nm1, imt1, host1)

    # Price test
    price1 = test_price(nm1)

    # --- Product 2: 1097975275 (iPhone, imt_id=2227481309) ---
    if remaining() > 30:
        nm2 = 1097975275
        log.info("--- PRODUCT 2: nm_id=%d ---", nm2)
        card2, host2 = fetch_card_json(nm2)
        if card2:
            imt2 = card2.get("imt_id", 0)
            log.info("  imt_id=%s", imt2)
            for k in ["price", "media", "photo_count"]:
                if k in card2:
                    log.info("  card2[%s] = %s", k, str(card2[k])[:120])
        else:
            imt2 = 2227481309
            host2 = 13

        media2 = test_media(nm2, imt2, host2)
        log.info("  PRODUCT2 media: %d found", len(media2))

    # --- HTML price fallback ---
    if remaining() > 15:
        log.info("--- HTML PRICE FALLBACK ---")
        for nm in [nm1, nm2]:
            if remaining() < 8:
                break
            url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
            r = http_get(url, headers=HTML_H, timeout=12)
            if r and r.status_code == 200:
                html = r.text
                log.info("  HTML nm=%d: %dB", nm, len(html))
                # og:price
                import re
                m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
                if m:
                    log.info("  og:price:amount = %s", m.group(1))
                m = re.search(r'data-price=["\'](\d+)["\']', html, re.I)
                if m:
                    log.info("  data-price = %s", m.group(1))
                # JSON-LD price
                for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.I | re.DOTALL):
                    try:
                        ld = json.loads(m.group(1))
                        if isinstance(ld, dict) and "price" in ld:
                            log.info("  JSON-LD price = %s", ld["price"])
                    except Exception:
                        pass
                # Check for embedded state
                m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
                if m:
                    log.info("  __INITIAL_STATE__ found: %dB", len(m.group(1)))
            else:
                code = r.status_code if r else "ERR"
                log.info("  HTML nm=%d: HTTP %s", nm, code)
            time.sleep(1)

    # --- Summary ---
    log.info("=== v132 SUMMARY ===")
    log.info("  P1 media: %d, P1 price: %s", len(media1),
             {k: v for k, v in list(price1.items())[:4]})
    log.info("  Total time: %.1fs", elapsed())
    log.info("=== v132 DONE ===")

if __name__ == "__main__":
    main()
