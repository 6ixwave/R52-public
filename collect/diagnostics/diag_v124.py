"""DIAG V124: WB BASKET IMAGE PATH + WB 498 BODY + FARPOST BULLETIN REDIRECT
2026-11-12

Фокус:
1. WB: полный basket-путь для изображений (как в _build_media)
2. WB: тело HTTP 498 от web API
3. WB: card.wb.ru с разными appType/dest
4. Farpost: /bulletin/{id} redirect + relative URLs из категории
"""

import json
import re
import sys
import time
import traceback
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

OUT = Path(__file__).parent / "diag_v124_result.log"
lines = []

def log(msg=""):
    lines.append(msg)
    print(msg)

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    return s

def try_cffi():
    try:
        from curl_cffi import requests as cffi_req
        return cffi_req
    except ImportError:
        return None

def main():
    log("=" * 70)
    log("DIAG V124: WB BASKET IMG + 498 BODY + FARPOST BULLETIN (2026-11-12)")
    log("=" * 70)

    sess = make_session()
    cffi = try_cffi()

    # ================================================================
    # PART 1: WB BASKET IMAGE — FULL PATH (как в _build_media)
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 1: WB BASKET IMAGE — FULL PATH")
    log("=" * 70)

    test_cards = [
        (259671175, 16),  # basket-16 confirmed
        (199298871, 13),  # basket-13 confirmed
        (1097975275, None),  # need to find host
    ]

    # Find host for 1097975275
    if test_cards[2][1] is None:
        nm = 1097975275
        vol = nm // 100000
        part = nm // 1000
        path = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
        found = None
        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            try:
                r = sess.get(url, timeout=8, headers={"Accept": "application/json"})
                if r.status_code == 200:
                    found = n
                    log(f"  card {nm}: {host} OK ({len(r.content)}B)")
                    break
            except Exception:
                continue
        test_cards[2] = (nm, found)
        if found is None:
            log(f"  card {nm}: HOST NOT FOUND")

    for nm_id, host_num in test_cards:
        if host_num is None:
            continue
        vol = nm_id // 100000
        part = nm_id // 1000
        host = f"basket-{host_num:02d}.wbbasket.ru"

        # Primary image path (S800)
        img_path = f"/vol{vol}/part{part}/{nm_id}/images/S800/S800.jpg"
        img_url = f"https://{host}{img_path}"
        try:
            r = sess.get(img_url, timeout=10)
            ct = r.headers.get("Content-Type", "?")
            log(f"  [{nm_id}] S800: HTTP {r.status_code} {len(r.content)}B {ct} {img_path}")
            if r.status_code == 200 and r.content[:2] == b'\xff\xd8':
                log(f"    *** JPEG CONFIRMED ***")
        except Exception as e:
            log(f"  [{nm_id}] S800: ERR {e}")
        time.sleep(0.5)

        # Try other sizes
        for size in ("S120", "S400", "S100", "big"):
            img_path = f"/vol{vol}/part{part}/{nm_id}/images/{size}/{size}.jpg"
            img_url = f"https://{host}{img_path}"
            try:
                r = sess.get(img_url, timeout=8)
                ct = r.headers.get("Content-Type", "?")
                status = "JPEG" if r.content[:2] == b'\xff\xd8' else ct
                log(f"  [{nm_id}] {size}: HTTP {r.status_code} {len(r.content)}B {status}")
            except Exception as e:
                log(f"  [{nm_id}] {size}: ERR {e}")
            time.sleep(0.3)

        # Try with imt_id instead of nm_id in path
        # First get imt_id from card.json
        card_path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        try:
            r = sess.get(f"https://{host}{card_path}", timeout=10, headers={"Accept": "application/json"})
            if r.status_code == 200:
                card = r.json()
                imt_id = card.get("imt_id", 0)
                photo_count = card.get("media", {}).get("photo_count", 0)
                log(f"  [{nm_id}] imt_id={imt_id}, photo_count={photo_count}")

                # Try imt_id in image path
                for size in ("S800", "S400"):
                    img_path = f"/vol{vol}/part{part}/{imt_id}/images/{size}/{size}.jpg"
                    img_url = f"https://{host}{img_path}"
                    try:
                        r2 = sess.get(img_url, timeout=8)
                        ct = r2.headers.get("Content-Type", "?")
                        status = "JPEG" if r2.content[:2] == b'\xff\xd8' else ct
                        log(f"  [{nm_id}] imt {size}: HTTP {r2.status_code} {len(r2.content)}B {status}")
                    except Exception as e:
                        log(f"  [{nm_id}] imt {size}: ERR {e}")
                    time.sleep(0.3)
        except Exception as e:
            log(f"  [{nm_id}] card.json err: {e}")

        log("")

    # ================================================================
    # PART 2: WB PRICE — HTTP 498 BODY + API VARIANTS
    # ================================================================
    log("=" * 70)
    log("PART 2: WB PRICE — 498 BODY + API VARIANTS")
    log("=" * 70)

    nm = 259671175

    # 2A: web API (gave 498)
    web_url = f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}"
    try:
        r = sess.get(web_url, timeout=12, headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
            "Origin": "https://www.wildberries.ru",
        })
        log(f"\n  [web_api] HTTP {r.status_code} {len(r.content)}B {r.headers.get('Content-Type','?')}")
        if r.status_code == 498:
            body = r.text[:800]
            log(f"  [web_api 498 body]:\n{body}")
            # Check if it's a redirect/challenge page
            if "location" in r.headers:
                log(f"  [web_api Location]: {r.headers['Location']}")
        elif r.status_code == 200:
            log(f"  [web_api JSON]: {r.text[:500]}")
    except Exception as e:
        log(f"  [web_api] ERR: {e}")

    time.sleep(1)

    # 2B: card.wb.ru with various params
    for label, params in [
        ("v2_app1", f"appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("v2_app2", f"appType=2&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("v2_min", f"curr=rub&nm={nm}"),
        ("v1_min", f"curr=rub&nm={nm}"),
        ("v5_min", f"curr=rub&nm={nm}"),
    ]:
        url = f"https://card.wb.ru/cards/v2/detail?{params}" if "v2" in label else f"https://card.wb.ru/cards/{label.split('_')[0]}/detail?{params}"
        try:
            r = sess.get(url, timeout=10, headers={
                "Accept": "application/json",
                "Referer": "https://www.wildberries.ru/",
            })
            log(f"  [{label}] HTTP {r.status_code} {len(r.content)}B")
            if r.status_code == 200:
                log(f"    body[:300]: {r.text[:300]}")
        except Exception as e:
            log(f"  [{label}] ERR: {e}")
        time.sleep(0.5)

    # 2C: basket price endpoints
    vol = nm // 100000
    part = nm // 1000
    host = "basket-16.wbbasket.ru"
    for ep in [
        f"/vol{vol}/part{part}/{nm}/price.json",
        f"/vol{vol}/part{part}/{nm}/info/ru/price.json",
        f"/vol{vol}/part{part}/{nm}/price/ru.json",
        f"/vol{vol}/part{part}/{nm}/data/price.json",
    ]:
        url = f"https://{host}{ep}"
        try:
            r = sess.get(url, timeout=8, headers={"Accept": "application/json"})
            log(f"  [basket {ep[-30:]}] HTTP {r.status_code} {len(r.content)}B")
            if r.status_code == 200:
                log(f"    body[:200]: {r.text[:200]}")
        except Exception as e:
            log(f"  [basket {ep[-30:]}] ERR: {e}")
        time.sleep(0.3)

    # 2D: Try curl_cffi for card.wb.ru (might handle TLS fingerprint better)
    if cffi:
        log("\n  --- curl_cffi for card.wb.ru ---")
        try:
            r = cffi.get(
                f"https://card.wb.ru/cards/v2/detail?curr=rub&nm={nm}",
                impersonate="chrome",
                timeout=12,
                headers={"Accept": "application/json"},
            )
            log(f"  [cffi card_wb] HTTP {r.status_code} {len(r.content)}B")
            if r.status_code == 200:
                log(f"    body[:400]: {r.text[:400]}")
        except Exception as e:
            log(f"  [cffi card_wb] ERR: {e}")

        try:
            r = cffi.get(
                f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}",
                impersonate="chrome",
                timeout=12,
                headers={
                    "Accept": "application/json",
                    "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                },
            )
            log(f"  [cffi web_api] HTTP {r.status_code} {len(r.content)}B")
            if r.status_code in (200, 498):
                log(f"    body[:400]: {r.text[:400]}")
        except Exception as e:
            log(f"  [cffi web_api] ERR: {e}")

    # ================================================================
    # PART 3: FARPOST — /bulletin/{id} REDIRECT + CATEGORY URLs
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 3: FARPOST — BULLETIN REDIRECT + CATEGORY")
    log("=" * 70)

    fp_sess = make_session()
    fp_sess.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    # Warmup
    try:
        r = fp_sess.get("https://www.farpost.ru/", timeout=15)
        log(f"\n  Warmup: HTTP {r.status_code} {len(r.content)}B cookies={list(fp_sess.cookies.keys())}")
    except Exception as e:
        log(f"  Warmup ERR: {e}")
    time.sleep(2)

    # 3A: /bulletin/{id} redirect
    for bid in ("130693618", "129606872"):
        url = f"https://www.farpost.ru/bulletin/{bid}"
        try:
            r = fp_sess.get(url, timeout=15, allow_redirects=True)
            log(f"\n  [bulletin/{bid}] HTTP {r.status_code} {len(r.content)}B")
            log(f"    final_url: {r.url}")
            log(f"    history: {[h.status_code for h in r.history]}")
            if r.status_code == 200 and len(r.content) > 5000:
                # Check for price
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r.text)
                if m:
                    log(f"    *** PRICE: {m.group(1)} RUB ***")
                # Check for title
                m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', r.text, re.DOTALL)
                if m:
                    title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                    log(f"    TITLE: {title[:80]}")
                # Check for photos
                photos = re.findall(r'data-image-info="(\{.*?\})"', r.text)
                log(f"    data-image-info count: {len(photos)}")
                # Check lightbox
                lb = re.findall(r'baza\.lightbox\.init\(', r.text)
                log(f"    lightbox.init count: {len(lb)}")
        except Exception as e:
            log(f"  [bulletin/{bid}] ERR: {e}")
        time.sleep(2)

    # 3B: Category page — extract relative URLs
    log("\n  --- Category page URLs ---")
    cat_url = "https://www.farpost.ru/vladivostok/auto"
    try:
        r = fp_sess.get(cat_url, timeout=20)
        log(f"  Category: HTTP {r.status_code} {len(r.content)}B")
        # Find all href
        hrefs = re.findall(r'href="([^"]+)"', r.text)
        bulletin_hrefs = [h for h in hrefs if re.search(r'\d{6,}', h) and not h.startswith('#')]
        log(f"  Total hrefs: {len(hrefs)}, with numeric: {len(bulletin_hrefs)}")
        for h in bulletin_hrefs[:15]:
            log(f"    {h[:100]}")
        # Check for bulletin links specifically
        bull_links = [h for h in hrefs if '/bulletin/' in h or re.search(r'-(\d{6,})\.html', h)]
        log(f"  Bulletin-specific links: {len(bull_links)}")
        for h in bull_links[:10]:
            log(f"    {h[:100]}")
    except Exception as e:
        log(f"  Category ERR: {e}")

    # 3C: Try known full-slug format
    log("\n  --- Full-slug format test ---")
    # From earlier work, we know format: /{city}/{cat}/{slug}-{id}.html
    # Try a known slug pattern
    test_slugs = [
        f"https://www.farpost.ru/vladivostok/auto/shiny-130693618.html",
        f"https://www.farpost.ru/vladivostok/130693618.html",
        f"https://www.farpost.ru/?u=130693618",
    ]
    for url in test_slugs:
        try:
            r = fp_sess.get(url, timeout=12, allow_redirects=True)
            log(f"  [{url[-50:]}] HTTP {r.status_code} {len(r.content)}B final={r.url[-60:]}")
        except Exception as e:
            log(f"  [{url[-50:]}] ERR: {e}")
        time.sleep(1)

    # ================================================================
    # PART 4: WB HTML PAGE (detail.aspx) — check for embedded JSON/price
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 4: WB HTML detail.aspx — EMBEDDED DATA")
    log("=" * 70)

    wb_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    try:
        r = sess.get(wb_url, timeout=20, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://www.wildberries.ru/",
        })
        log(f"\n  [detail.aspx] HTTP {r.status_code} {len(r.content)}B")
        if r.status_code == 200:
            html = r.text
            # Look for embedded JSON state
            m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
            if m:
                log(f"  *** __INITIAL_STATE__ found ({len(m.group(1))}B) ***")
                log(f"    keys: {list(json.loads(m.group(1)).keys())[:10]}")
            # Look for price in HTML
            prices = re.findall(r'>([\d\s\u00a0]{2,10})\s*(?:₽|руб)', html)
            log(f"  Price patterns in HTML: {prices[:5]}")
            # og:price
            m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
            if m:
                log(f"  *** og:price:amount = {m.group(1)} ***")
            # data-price
            dp = re.findall(r'data-price=["\'](\d+)["\']', html)
            log(f"  data-price attrs: {dp[:5]}")
            # Check for __NEXT_DATA__ or similar
            for pattern in ("__NEXT_DATA__", "__APP_DATA__", "window.__state__", "application/json"):
                m = re.search(pattern, html, re.I)
                if m:
                    log(f"  Found: {pattern} at pos {m.start()}")
            # First 2000 chars for context
            log(f"\n  HTML head[:500]: {html[:500]}")
        elif r.status_code in (403, 429, 498):
            log(f"  [detail.aspx] BLOCKED: {r.status_code}")
            log(f"  body[:300]: {r.text[:300]}")
    except Exception as e:
        log(f"  [detail.aspx] ERR: {e}")

    # ================================================================
    # SUMMARY
    # ================================================================
    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log("DIAG V124 DONE")

    # Write results
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved to {OUT}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"\nFATAL: {e}")
        log(traceback.format_exc())
        OUT.write_text("\n".join(lines), encoding="utf-8")
        sys.exit(1)
    sys.exit(0)
