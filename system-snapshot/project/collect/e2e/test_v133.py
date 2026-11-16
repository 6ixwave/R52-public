"""v133: WB — contents field, HTML 498, focused price, alt CDN patterns.

Budget: 100s. Focus:
  A) card.json 'contents' field (may have image URLs)
  B) HTML detail.aspx (498 might still have content)
  C) 5 focused price endpoints with XHR headers
  D) 6 alt image CDN patterns (wbx, static, img subdomains)
"""
import json, logging, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("v133")

BUDGET = 100
START = time.monotonic()

def rem():
    return BUDGET - (time.monotonic() - START)

# HTTP
try:
    from curl_cffi import requests as cffi_req
    HAVE_CFFI = True
    log.info("curl_cffi OK")
except ImportError:
    import requests as cffi_req
    HAVE_CFFI = False

JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
    "X-Requested-With": "XMLHttpRequest",
}
HTML_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}
IMG_H = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.wildberries.ru/",
}

def get(url, headers=None, timeout=10):
    try:
        if HAVE_CFFI:
            r = cffi_req.get(url, headers=headers or {}, timeout=timeout,
                             impersonate="chrome", allow_redirects=True)
        else:
            r = cffi_req.get(url, headers=headers or {}, timeout=timeout)
        return r
    except Exception as e:
        log.warning("HTTP err %s: %s", url[:50], e)
        return None

# ============================================================
# A) CARD.JSON — full dump of 'contents' and other fields
# ============================================================
def fetch_card(nm_id):
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    for n in [13, 1, 5, 10, 20, 30, 40, 50]:
        host = f"basket-{n:02d}.wbbasket.ru"
        r = get(f"https://{host}{path}", JSON_H, 8)
        if r and r.status_code == 200:
            try:
                d = r.json()
                if d.get("nm_id"):
                    log.info("card nm=%d host=%02d", nm_id, n)
                    return d, n
            except Exception:
                pass
        if rem() < 10:
            break
    return None, None

def dump_card_fields(card, nm_id):
    """Print all top-level keys and focus on contents/media/selling."""
    log.info("  keys: %s", list(card.keys()))
    for key in ["contents", "media", "selling", "compositions", "colors", "full_colors"]:
        if key in card:
            val = card[key]
            s = json.dumps(val, ensure_ascii=False)
            log.info("  [%s] (%dB): %s", key, len(s), s[:300])

# ============================================================
# B) HTML detail.aspx — handle 498
# ============================================================
def fetch_html(nm_id):
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    r = get(url, HTML_H, 15)
    if r is None:
        log.info("  HTML nm=%d: ERR", nm_id)
        return None
    code = r.status_code
    body = r.text if r.text else ""
    log.info("  HTML nm=%d: HTTP %d, %dB", nm_id, code, len(body))
    if code in (200, 498) and len(body) > 500:
        # Extract images
        imgs = []
        # og:image
        m = re.search(r'og:image[^>]*content=["\']([^"\']+)', body, re.I)
        if m:
            imgs.append(("og:image", m.group(1)))
        # data-src / src with wbbasket
        for m in re.finditer(r'(?:data-src|src|data-original)=["\']([^"\']*wbbasket[^"\']*)["\']', body, re.I):
            imgs.append(("html", m.group(1)))
        # Any image URL in JSON blobs
        for m in re.finditer(r'"(?:photo|image|img|url)"\s*:\s*"([^"]*wbbasket[^"]*)"', body, re.I):
            imgs.append(("json_blob", m.group(1)))
        # Price
        prices = []
        m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', body, re.I)
        if m:
            prices.append(("og:price", m.group(1)))
        for m in re.finditer(r'data-price=["\'](\d+)["\']', body, re.I):
            prices.append(("data-price", m.group(1)))
        # __INITIAL_STATE__ or __NEXT_DATA__
        m = re.search(r'window\.__(?:INITIAL_STATE|NEXT_DATA__)\s*=\s*(\{.+?\})\s*;\s*(?:</script>|$)', body, re.DOTALL)
        if m:
            log.info("  HTML: __STATE__ found %dB", len(m.group(1)))
            state_str = m.group(1)[:500]
            log.info("  HTML state[:500]: %s", state_str)
        log.info("  HTML imgs: %d, prices: %s", len(imgs), prices[:5])
        for i, (src, u) in enumerate(imgs[:8]):
            log.info("    img[%d] (%s): %s", i, src, u[:120])
        return {"code": code, "body": body, "imgs": imgs, "prices": prices}
    elif code == 498:
        log.info("  HTML 498 body[:200]: %s", body[:200])
    return None

# ============================================================
# C) PRICE — 5 focused endpoints
# ============================================================
def test_price(nm_id):
    endpoints = [
        ("cw_v2_xhr", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
         {**JSON_H, "X-Requested-With": "XMLHttpRequest"}),
        ("cw_v2_dest0", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=0&spp=30&nm={nm_id}", JSON_H),
        ("web_v1_xhr", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}",
         {**JSON_H, "X-Requested-With": "XMLHttpRequest"}),
        ("search_api", f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&query=test&nm={nm_id}", JSON_H),
        ("card_v1_app2", f"https://card.wb.ru/cards/v1/detail?appType=2&curr=rub&dest=-1257786&spp=30&nm={nm_id}", JSON_H),
    ]
    results = {}
    for label, url, hdrs in endpoints:
        if rem() < 6:
            break
        r = get(url, hdrs, 10)
        if r is None:
            results[label] = "ERR"
            continue
        code = r.status_code
        clen = len(r.content or b"")
        body_preview = (r.text or "")[:150]
        results[label] = f"{code}/{clen}B"
        log.info("  PRICE %s -> %d %dB: %s", label, code, clen, body_preview[:100])
        if code == 200 and clen > 30:
            try:
                d = r.json()
                items = d.get("data", {}).get("items") or d.get("products") or d.get("items") or []
                if items:
                    p = items[0]
                    sz = p.get("sizes", [{}])[0]
                    pr = sz.get("price", {})
                    log.info("  PRICE HIT %s: basic=%s total=%s qty=%s",
                             label, pr.get("basic"), pr.get("total"), sz.get("qty"))
                    results[label] += f" HIT"
            except Exception as e:
                log.info("  PRICE %s parse: %s", label, e)
        time.sleep(0.5)
    return results

# ============================================================
# D) ALT IMAGE CDN PATTERNS
# ============================================================
def test_images(nm_id, imt_id, basket_host):
    vol_i = imt_id // 100000
    part_i = imt_id // 1000
    vol_n = nm_id // 100000
    part_n = nm_id // 1000

    patterns = [
        # Standard: imt-based vol/part
        (f"basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{imt_id}.jpg"),
        # nm-based vol/part, imt filename
        (f"basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_n}/part{part_n}/{nm_id}/images/S800/{imt_id}.jpg"),
        # wbx CDN
        ("wbx-static.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{imt_id}.jpg"),
        # img subdomain
        (f"img-basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{imt_id}.jpg"),
        # static-basket
        (f"static-basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{imt_id}.jpg"),
        # No 'images' dir
        (f"basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/S800/{imt_id}.jpg"),
        # S800 as filename
        (f"basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/S800.jpg"),
        # imt in path
        (f"basket-{basket_host:02d}.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{imt_id}/images/S800/{imt_id}.jpg"),
        # wbstatic.net
        ("wbstatic.net", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{imt_id}.jpg"),
        # basket with imt vol/part but nm in path (already tried but try host 1)
        ("basket-01.wbbasket.ru", f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{imt_id}.jpg"),
    ]

    found = []
    for host, path in patterns:
        if rem() < 5:
            break
        url = f"https://{host}{path}"
        r = get(url, IMG_H, 6)
        if r:
            code = r.status_code
            clen = len(r.content or b"")
            log.info("  IMG %s %dB %s", code, clen, f"{host}{path[:60]}")
            if code == 200 and clen > 500:
                found.append(url)
                log.info("  *** IMAGE HIT: %s (%dB) ***", url, clen)
        time.sleep(0.3)
    log.info("  IMAGES found: %d", len(found))
    return found

# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== v133 START budget=%ds ===", BUDGET)

    # Product: 199298871, imt_id=158033827, host=13
    nm = 199298871
    imt = 158033827

    # A) Card.json full dump
    log.info("--- A) CARD.JSON FULL ---")
    card, host = fetch_card(nm)
    if card:
        dump_card_fields(card, nm)
        # Check if contents has image URLs
        contents = card.get("contents")
        if isinstance(contents, list):
            for i, c in enumerate(contents[:5]):
                log.info("  contents[%d]: %s", i, json.dumps(c, ensure_ascii=False)[:200])
        elif isinstance(contents, dict):
            log.info("  contents dict: %s", json.dumps(contents, ensure_ascii=False)[:300])
    else:
        log.warning("  card.json fetch failed, using known imt=%d host=13", imt)
        host = 13

    # B) HTML
    log.info("--- B) HTML detail.aspx ---")
    html_data = fetch_html(nm)
    if html_data and html_data.get("imgs"):
        # Test first image URL
        for src, u in html_data["imgs"][:3]:
            if rem() < 5:
                break
            log.info("  Testing HTML img: %s", u[:100])
            r = get(u, IMG_H, 8)
            if r:
                log.info("    -> %d %dB", r.status_code, len(r.content or b""))

    # C) Price
    log.info("--- C) PRICE (5 endpoints) ---")
    price_res = test_price(nm)

    # D) Alt image patterns
    log.info("--- D) ALT IMAGE PATTERNS ---")
    imgs = test_images(nm, imt, host)

    # Second product quick check
    if rem() > 25:
        nm2 = 259671175
        imt2 = 236868886
        log.info("--- PRODUCT2: nm=%d imt=%d ---", nm2, imt2)
        card2, host2 = fetch_card(nm2)
        if card2:
            dump_card_fields(card2, nm2)
            imt2 = card2.get("imt_id", imt2)
        else:
            host2 = 13
        imgs2 = test_images(nm2, imt2, host2)
        log.info("  P2 images: %d", len(imgs2))

    # Summary
    log.info("=== v133 SUMMARY ===")
    log.info("  P1 card: %s, html: %s, price: %s, imgs: %d",
             "OK" if card else "FAIL",
             f"HTTP{html_data['code']}" if html_data else "FAIL",
             {k: v for k, v in list(price_res.items())[:3]},
             len(imgs))
    log.info("  Total: %.1fs", time.monotonic() - START)
    log.info("=== v133 DONE ===")

if __name__ == "__main__":
    main()
