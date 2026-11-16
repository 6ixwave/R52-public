"""v134: WB — session-based price, basket CDN price, 498 parse, media deep.

Strategy:
  A) GET wildberries.ru main → grab cookies → use for API calls
  B) Basket CDN: try price.json, info/ru/price.json, cards.json
  C) Parse 498 body fully (might be meta-refresh or JS token)
  D) Media: try nm_id as filename, try 'photo' dir, try different sizes
  E) Mobile API / search.wb.ru with session

Budget: 105s
"""
import json, logging, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("v134")

BUDGET = 105
START = time.monotonic()

def rem():
    return BUDGET - (time.monotonic() - START)

# HTTP with session support
try:
    from curl_cffi import requests as cffi_req
    HAVE_CFFI = True
    log.info("curl_cffi OK")
except ImportError:
    import requests as cffi_req
    HAVE_CFFI = False

# Main session
session = cffi_req.Session(impersonate="chrome") if HAVE_CFFI else cffi_req.Session()

JSON_H = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
HTML_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
IMG_H = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

def get(url, headers=None, timeout=10, use_session=False):
    try:
        if use_session:
            r = session.get(url, headers=headers or {}, timeout=timeout,
                            allow_redirects=True)
        elif HAVE_CFFI:
            r = cffi_req.get(url, headers=headers or {}, timeout=timeout,
                             impersonate="chrome", allow_redirects=True)
        else:
            r = cffi_req.get(url, headers=headers or {}, timeout=timeout)
        return r
    except Exception as e:
        log.warning("HTTP err %s: %s", url[:60], str(e)[:80])
        return None

# ============================================================
# A) SESSION: GET main page, extract cookies
# ============================================================
def build_session():
    log.info("--- A) BUILD SESSION ---")
    url = "https://www.wildberries.ru/"
    r = get(url, HTML_H, 15, use_session=True)
    if r:
        log.info("  Main page: HTTP %d, %dB", r.status_code, len(r.content or b""))
        # Show cookies
        try:
            cookies = session.cookies
            cookie_str = "; ".join(f"{c.name}={c.value[:20]}" for c in cookies)
            log.info("  Cookies (%d): %s", len(cookies), cookie_str[:200])
        except Exception:
            log.info("  Cookies: (no access)")
        # Check for meta-refresh, JS challenge
        body = r.text[:2000] if r.text else ""
        if "meta" in body.lower() and "refresh" in body.lower():
            m = re.search(r'content=["\'](\d+);url=([^"\']+)', body, re.I)
            if m:
                log.info("  META-REFRESH: %s -> %s", m.group(1), m.group(2))
        if "__NEXT_DATA__" in body:
            log.info("  __NEXT_DATA__ found")
        if "data-req-uuid" in body:
            m = re.search(r'data-req-uuid="([^"]+)"', body)
            if m:
                log.info("  req-uuid: %s", m.group(1))
    else:
        log.warning("  Main page fetch failed")
    return r

# ============================================================
# B) BASKET CDN: price endpoints near card.json
# ============================================================
def test_basket_price(nm_id, host_num):
    vol = nm_id // 100000
    part = nm_id // 1000
    base = f"basket-{host_num:02d}.wbbasket.ru"
    
    paths = [
        f"/vol{vol}/part{part}/{nm_id}/info/ru/price.json",
        f"/vol{vol}/part{part}/{nm_id}/info/price.json",
        f"/vol{vol}/part{part}/{nm_id}/price.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/cards.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card_full.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/detail.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card_price.json",
        f"/vol{vol}/part{part}/{nm_id}/price/ru.json",
    ]
    
    log.info("--- B) BASKET CDN PRICE ---")
    for path in paths:
        if rem() < 5:
            break
        url = f"https://{base}{path}"
        r = get(url, JSON_H, 8)
        if r:
            code = r.status_code
            clen = len(r.content or b"")
            log.info("  %s -> %d %dB", path, code, clen)
            if code == 200 and clen > 10:
                try:
                    data = r.json()
                    log.info("    DATA: %s", json.dumps(data, ensure_ascii=False)[:300])
                    return data
                except Exception:
                    log.info("    BODY[:200]: %s", (r.text or "")[:200])
        time.sleep(0.3)
    return None

# ============================================================
# C) 498 HTML DEEP PARSE
# ============================================================
def parse_498(nm_id):
    log.info("--- C) 498 HTML PARSE ---")
    url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    r = get(url, HTML_H, 12, use_session=True)
    if not r:
        log.info("  498 fetch failed")
        return None
    code = r.status_code
    body = r.text or ""
    log.info("  HTTP %d, %dB", code, len(body))
    # Full body if short
    if len(body) < 3000:
        log.info("  FULL BODY:\n%s", body[:2500])
    else:
        log.info("  BODY[:1500]:\n%s", body[:1500])
        log.info("  BODY[-500:]:\n%s", body[-500:])
    
    # Extract all URLs, scripts, meta
    urls = re.findall(r'(?:href|src|content|url)=["\']([^"\']+)["\']', body, re.I)
    log.info("  URLs found: %d", len(urls))
    for u in urls[:10]:
        log.info("    %s", u[:120])
    
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', body, re.I | re.DOTALL)
    for i, s in enumerate(scripts[:5]):
        if s.strip():
            log.info("  script[%d] (%dB): %s", i, len(s), s.strip()[:200])
    
    # Check for token/challenge
    for pat in ["token", "challenge", "captcha", "verify", "cookie", "session",
                "data-req-uuid", "x-wb-", "wb-sid"]:
        if pat.lower() in body.lower():
            log.info("  FOUND pattern: %s", pat)
    
    return {"code": code, "body": body, "urls": urls}

# ============================================================
# D) MEDIA: deeper patterns
# ============================================================
def test_media_deep(nm_id, imt_id, host_num):
    log.info("--- D) MEDIA DEEP ---")
    vol_n = nm_id // 100000
    part_n = nm_id // 1000
    vol_i = imt_id // 100000
    part_i = imt_id // 1000
    host = f"basket-{host_num:02d}.wbbasket.ru"
    
    patterns = []
    # nm-based vol/part (confirmed for card.json)
    for fname in [f"{imt_id}.jpg", f"{nm_id}.jpg", "1.jpg", "0.jpg", "photo.jpg"]:
        for size in ["S800", "S400"]:
            patterns.append((host, f"/vol{vol_n}/part{part_n}/{nm_id}/images/{size}/{fname}"))
    # imt-based vol/part
    for fname in [f"{imt_id}.jpg", f"{nm_id}.jpg", "1.jpg"]:
        patterns.append((host, f"/vol{vol_i}/part{part_i}/{nm_id}/images/S800/{fname}"))
    # Different dir names
    for d in ["photo", "photos", "img", "media", "images"]:
        patterns.append((host, f"/vol{vol_n}/part{part_n}/{nm_id}/{d}/S800/{imt_id}.jpg"))
    # No size dir
    patterns.append((host, f"/vol{vol_n}/part{part_n}/{nm_id}/S800_{imt_id}.jpg"))
    # wbx pattern (different CDN)
    patterns.append(("wbx-static.wbbasket.ru", f"/vol{vol_n}/part{part_n}/{nm_id}/images/S800/{imt_id}.jpg"))
    # basket with different host for images
    patterns.append((f"basket-{(host_num+1)%60+1:02d}.wbbasket.ru",
                     f"/vol{vol_n}/part{part_n}/{nm_id}/images/S800/{imt_id}.jpg"))
    
    found = []
    for h, path in patterns[:20]:
        if rem() < 4:
            break
        url = f"https://{h}{path}"
        r = get(url, IMG_H, 6)
        if r:
            code = r.status_code
            clen = len(r.content or b"")
            log.info("  %s %dB %s", code, clen, f"{h}{path[:50]}")
            if code == 200 and clen > 500:
                found.append(url)
                log.info("  *** HIT: %s (%dB) ***", url, clen)
                break
        time.sleep(0.2)
    
    log.info("  MEDIA found: %d", len(found))
    return found

# ============================================================
# E) API WITH SESSION (post-main-page)
# ============================================================
def test_api_with_session(nm_id):
    log.info("--- E) API WITH SESSION ---")
    endpoints = [
        ("web_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
        ("web_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm_id}"),
        ("cw_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("search_v5", f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&query={nm_id}&nm={nm_id}"),
        ("mobile_card", f"https://card.wb.ru/cards/v1/detail?appType=2&curr=rub&dest=-1257786&nm={nm_id}"),
    ]
    
    results = {}
    for label, url in endpoints:
        if rem() < 5:
            break
        hdrs = {**JSON_H, "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"}
        r = get(url, hdrs, 10, use_session=True)
        if r is None:
            results[label] = "ERR"
            continue
        code = r.status_code
        clen = len(r.content or b"")
        body_preview = (r.text or "")[:150]
        results[label] = f"{code}/{clen}B"
        log.info("  %s -> %d %dB: %s", label, code, clen, body_preview[:100])
        
        if code == 200 and clen > 30:
            try:
                d = r.json()
                items = (d.get("data", {}).get("items") or
                         d.get("products") or
                         d.get("items") or [])
                if items:
                    p = items[0]
                    sizes = p.get("sizes", [])
                    if sizes:
                        sz = sizes[0]
                        pr = sz.get("price", {})
                        log.info("  *** PRICE HIT %s: basic=%s total=%s qty=%s ***",
                                 label, pr.get("basic"), pr.get("total"), sz.get("qty"))
                        results[label] += " HIT"
                    else:
                        log.info("  %s: no sizes, keys=%s", label, list(p.keys())[:10])
                else:
                    log.info("  %s: no items, top keys=%s", label, list(d.keys())[:10])
            except Exception as e:
                log.info("  %s parse: %s", label, str(e)[:60])
        time.sleep(0.5)
    return results

# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=== v134 START budget=%ds ===", BUDGET)
    
    nm = 199298871
    imt = 158033827
    host = 13
    
    # A) Session
    build_session()
    
    # B) Basket CDN price
    basket_price = test_basket_price(nm, host)
    
    # C) 498 parse
    html498 = parse_498(nm)
    
    # D) Media deep
    media = test_media_deep(nm, imt, host)
    
    # E) API with session
    api_res = test_api_with_session(nm)
    
    # Summary
    log.info("=== v134 SUMMARY ===")
    log.info("  Basket price: %s", "OK" if basket_price else "none")
    log.info("  498: HTTP%s, %dB", html498["code"] if html498 else "?",
             len(html498["body"]) if html498 else 0)
    log.info("  Media: %d found", len(media))
    log.info("  API session: %s", {k: v for k, v in list(api_res.items())[:5]})
    log.info("  Total: %.1fs", time.monotonic() - START)
    log.info("=== v134 DONE ===")

if __name__ == "__main__":
    main()
