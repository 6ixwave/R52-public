"""DIAG V125: WB IR-CDN IMAGES + WB PRICE V2/XHR + FARPOST /{city}/item-{id}.html
2026-11-12

Фокус:
1. WB images: ir-XX.wbstatic.net (big/new, extra/new, small/new)
2. WB price: v2 API, XHR headers, mobile UA, antibot challenge parse
3. Farpost: /{city}/item-{id}.html — полный парсинг (price, title, photos, specs)
4. Farpost: разные города
"""

import json
import re
import sys
import time
import traceback
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

OUT = Path(__file__).parent / "diag_v125_result.log"
lines = []

def log(msg=""):
    lines.append(msg)
    print(msg)

def make_session(mobile=False):
    s = requests.Session()
    if mobile:
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
    else:
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
    return s

def main():
    log("=" * 70)
    log("DIAG V125: WB IR-CDN + PRICE V2 + FARPOST ITEM (2026-11-12)")
    log("=" * 70)

    sess = make_session()

    # ================================================================
    # PART 1: WB IMAGES — ir-XX.wbstatic.net CDN
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 1: WB IMAGES — ir-XX.wbstatic.net")
    log("=" * 70)

    test_ids = [259671175, 199298871, 1097975275]
    photo_counts = {259671175: 15, 199298871: 6, 1097975275: 5}

    for nm in test_ids:
        vol = nm // 100000
        pc = photo_counts[nm]
        log(f"\n  --- nm={nm} vol={vol} photos={pc} ---")

        # Pattern A: ir-XX.wbstatic.net/big/new/{vol}/{nm}/images/{nm}-{i}.jpg
        for ir_n in (5, 10, 15, 20, 25, 30, 40, 50):
            base = f"https://ir-{ir_n}.wbstatic.net"
            # Try first photo
            for i in (1, 2):
                url = f"{base}/big/new/{vol}/{nm}/images/{nm}-{i}.jpg"
                try:
                    r = sess.get(url, timeout=6, stream=True)
                    ct = r.headers.get("Content-Type", "?")
                    size = r.headers.get("Content-Length", "?")
                    is_jpeg = r.content[:2] == b'\xff\xd8' if r.status_code == 200 else False
                    log(f"  [ir-{ir_n} big {nm}-{i}] {r.status_code} {size}B {ct} {'JPEG' if is_jpeg else ''}")
                    r.close()
                    if r.status_code == 200:
                        break
                except Exception as e:
                    log(f"  [ir-{ir_n} big {nm}-{i}] ERR {type(e).__name__}")
            else:
                continue
            break
        else:
            # Try extra/new and small/new
            for prefix in ("extra/new", "small/new", "medium/new"):
                for ir_n in (10, 20, 30):
                    url = f"https://ir-{ir_n}.wbstatic.net/{prefix}/{vol}/{nm}/images/{nm}-1.jpg"
                    try:
                        r = sess.get(url, timeout=6, stream=True)
                        is_jpeg = r.content[:2] == b'\xff\xd8' if r.status_code == 200 else False
                        log(f"  [ir-{ir_n} {prefix} -1] {r.status_code} {r.headers.get('Content-Length','?')}B {'JPEG' if is_jpeg else ''}")
                        r.close()
                        if r.status_code == 200:
                            break
                    except Exception:
                        pass
                else:
                    continue
                break

        # Pattern B: basket with different filename
        # We know basket hosts: 259671175->16, 199298871->13, 1097975275->42
        basket_map = {259671175: 16, 199298871: 13, 1097975275: 42}
        bn = basket_map.get(nm)
        if bn:
            host = f"basket-{bn:02d}.wbbasket.ru"
            part = nm // 1000
            # Try: /vol{vol}/part{part}/{nm}/images/{nm}-{i}.jpg (no S800 dir)
            for pattern in [
                f"/vol{vol}/part{part}/{nm}/images/{nm}-1.jpg",
                f"/vol{vol}/part{part}/{nm}/images/big/{nm}-1.jpg",
                f"/vol{vol}/part{part}/{nm}/images/S800/{nm}.jpg",
                f"/vol{vol}/part{part}/{nm}/img/S800/S800.jpg",
                f"/vol{vol}/part{part}/{nm}/photo/S800/S800.jpg",
            ]:
                url = f"https://{host}{pattern}"
                try:
                    r = sess.get(url, timeout=6, stream=True)
                    is_jpeg = r.content[:2] == b'\xff\xd8' if r.status_code == 200 else False
                    log(f"  [basket {pattern[-40:]}] {r.status_code} {r.headers.get('Content-Length','?')}B {'JPEG' if is_jpeg else ''}")
                    r.close()
                except Exception as e:
                    log(f"  [basket {pattern[-40:]}] ERR {type(e).__name__}")
                time.sleep(0.2)

        time.sleep(0.5)

    # ================================================================
    # PART 2: WB PRICE — V2 API, XHR, MOBILE, ANTIBOT
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 2: WB PRICE — V2/XHR/MOBILE/ANTIBOT")
    log("=" * 70)

    nm = 259671175

    # 2A: web API v2
    for ver in ("v1", "v2", "v3"):
        url = f"https://www.wildberries.ru/web/api/catalog/cards/{ver}/detail?nm={nm}"
        try:
            r = sess.get(url, timeout=12, headers={
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
            })
            log(f"\n  [web_api {ver} XHR] {r.status_code} {len(r.content)}B {r.headers.get('Content-Type','?')}")
            if r.status_code == 200:
                log(f"    body[:400]: {r.text[:400]}")
            elif r.status_code == 498:
                log(f"    (antibot challenge)")
        except Exception as e:
            log(f"  [web_api {ver} XHR] ERR {e}")
        time.sleep(0.5)

    # 2B: catalog.wb.ru (different from card.wb.ru)
    for url in [
        f"https://catalog.wb.ru/cards/v2/detail?curr=rub&nm={nm}",
        f"https://catalog.wb.ru/cards/detail?curr=rub&nm={nm}",
        f"https://catalog.wb.ru/cards/v1/detail?curr=rub&nm={nm}",
    ]:
        try:
            r = sess.get(url, timeout=10, headers={"Accept": "application/json"})
            log(f"\n  [catalog.wb {url[-40:]}] {r.status_code} {len(r.content)}B")
            if r.status_code == 200:
                log(f"    body[:300]: {r.text[:300]}")
        except Exception as e:
            log(f"  [catalog.wb {url[-40:]}] ERR {e}")
        time.sleep(0.3)

    # 2C: Mobile UA
    m_sess = make_session(mobile=True)
    m_url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
    try:
        r = m_sess.get(m_url, timeout=15)
        log(f"\n  [mobile detail.aspx] {r.status_code} {len(r.content)}B")
        if r.status_code == 200:
            # Look for price
            prices = re.findall(r'>([\d\s\u00a0]{2,10})\s*(?:₽|руб)', r.text)
            log(f"    prices in HTML: {prices[:5]}")
            m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', r.text, re.I)
            if m:
                log(f"    *** og:price = {m.group(1)} ***")
            # Look for embedded JSON
            m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{)', r.text)
            if m:
                log(f"    *** __INITIAL_STATE__ found ***")
            # data-price
            dp = re.findall(r'data-price=["\'](\d+)["\']', r.text)
            log(f"    data-price: {dp[:5]}")
        elif r.status_code == 498:
            log(f"    (antibot challenge)")
    except Exception as e:
        log(f"  [mobile detail.aspx] ERR {e}")

    # 2D: Antibot challenge — parse the JS
    log("\n  --- Antibot challenge JS ---")
    try:
        r = sess.get(f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}",
                     timeout=12, headers={"Accept": "application/json"})
        if r.status_code == 498:
            # Extract JS URL
            m = re.search(r'src="(/__wbaas/challenges/antibot/__static/v2/index-[^"]+\.js)"', r.text)
            if m:
                js_url = f"https://www.wildberries.ru{m.group(1)}"
                log(f"  JS URL: {js_url}")
                r2 = sess.get(js_url, timeout=15)
                log(f"  JS: {r2.status_code} {len(r2.content)}B")
                # Look for cookie names, challenge logic
                js_text = r2.text
                # Find cookie-setting patterns
                cookies = re.findall(r'(?:document\.cookie|setCookie|cookie)\s*[=,]\s*["\']?(\w+)', js_text)
                log(f"  Cookie patterns: {set(cookies)}")
                # Find fetch/XHR patterns
                fetches = re.findall(r'(?:fetch|XMLHttpRequest|axios)\s*\(', js_text)
                log(f"  Fetch/XHR calls: {len(fetches)}")
                # Look for challenge token
                tokens = re.findall(r'["\']([a-f0-9]{32,})["\']', js_text)
                log(f"  Token-like strings: {tokens[:5]}")
                # First 1000 chars
                log(f"  JS[:600]: {js_text[:600]}")
            # Also check for meta refresh or form
            m = re.search(r'<form[^>]*action="([^"]*)"', r.text)
            if m:
                log(f"  Form action: {m.group(1)}")
    except Exception as e:
        log(f"  Antibot ERR: {e}")

    # 2E: Try with cookies from warmup
    log("\n  --- Warmup + API ---")
    w_sess = make_session()
    try:
        r = w_sess.get("https://www.wildberries.ru/", timeout=15)
        log(f"  Warmup: {r.status_code} {len(r.content)}B cookies={list(w_sess.cookies.keys())}")
        time.sleep(1)
        # Now try API with session cookies
        r = w_sess.get(f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}",
                       timeout=12, headers={
                           "Accept": "application/json, text/plain, */*",
                           "X-Requested-With": "XMLHttpRequest",
                       })
        log(f"  [warmup+api] {r.status_code} {len(r.content)}B")
        if r.status_code == 200:
            log(f"    body[:400]: {r.text[:400]}")
    except Exception as e:
        log(f"  Warmup ERR: {e}")

    # ================================================================
    # PART 3: FARPOST — /{city}/item-{id}.html FULL PARSE
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 3: FARPOST — /{city}/item-{id}.html FULL PARSE")
    log("=" * 70)

    fp = make_session()
    # Warmup
    try:
        r = fp.get("https://www.farpost.ru/", timeout=15)
        log(f"\n  Warmup: {r.status_code} {len(r.content)}B cookies={list(fp.cookies.keys())}")
    except Exception as e:
        log(f"  Warmup ERR: {e}")
    time.sleep(2)

    test_ids = ["130693618", "136342005", "98836529"]

    for bid in test_ids:
        log(f"\n  --- bulletin {bid} ---")
        for city in ("vladivostok", "artem", "nakhodka"):
            url = f"https://www.farpost.ru/{city}/item-{bid}.html"
            try:
                r = fp.get(url, timeout=15, allow_redirects=True)
                final = r.url
                log(f"  [{city}] {r.status_code} {len(r.content)}B final={final[-70:]}")

                if r.status_code == 200 and len(r.content) > 10000:
                    html = r.text

                    # TITLE
                    m = re.search(r'data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
                    title = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else "?"
                    log(f"    title: {title[:80]}")

                    # PRICE
                    m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                    price = m.group(1) if m else "?"
                    log(f"    price: {price} RUB")

                    # JSON-LD
                    for jm in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                        try:
                            ld = json.loads(jm.group(1).strip())
                            if isinstance(ld, dict) and ld.get("@type") == "Product":
                                log(f"    JSON-LD: name={ld.get('name','?')[:50]} price={ld.get('offers',{}).get('price','?')}")
                                break
                        except (json.JSONDecodeError, ValueError):
                            pass

                    # PHOTOS: data-image-info
                    photos = re.findall(r'data-image-info="(\{.*?\})"', html)
                    log(f"    data-image-info: {len(photos)} photos")
                    if photos:
                        try:
                            first = json.loads(photos[0].replace("&quot;", '"').replace("&amp;", "&"))
                            log(f"    first photo: {first.get('msrc', first.get('src','?'))[:80]}")
                        except (json.JSONDecodeError, ValueError):
                            pass

                    # LIGHTBOX
                    lb = re.findall(r'baza\.lightbox\.init\(', html)
                    log(f"    lightbox.init: {len(lb)}")

                    # SPECS
                    specs = re.findall(
                        r'<div\s+class="field\s+viewbull-field__container[^"]*"[^>]*>\s*'
                        r'<div\s+class="label">([^<]+)</div>\s*'
                        r'<div\s+class="value">.*?>(.*?)</span>',
                        html, re.DOTALL
                    )
                    log(f"    specs: {len(specs)} pairs")
                    for s in specs[:5]:
                        val = re.sub(r'<[^>]+>', '', s[1]).strip()[:40]
                        log(f"      {s[0].strip()}: {val}")

                    # SELLER
                    m = re.search(r'class="userNick[^"]*"[^>]*>\s*<a\s+href="(/user/[^/]+/?)">(.*?)</a>', html, re.DOTALL)
                    if m:
                        seller = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                        log(f"    seller: {seller} ({m.group(1)})")

                    # AVAILABILITY
                    m = re.search(r'data-field="goodPresentState"[^>]*>\s*([^<]+)', html)
                    if m:
                        log(f"    availability: {m.group(1).strip()}")

                    # og:image
                    m = re.search(r'og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
                    if m:
                        log(f"    og:image: {m.group(1)[:80]}")

                    # BREAK after first successful city
                    break

                elif r.status_code == 200:
                    log(f"    (200 but short: {len(r.content)}B)")
                else:
                    log(f"    (HTTP {r.status_code})")
            except Exception as e:
                log(f"  [{city}] ERR {e}")
            time.sleep(1.5)

    # ================================================================
    # PART 4: FARPOST — CATEGORY URL PATTERN (from diag_v124)
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 4: FARPOST — REAL CATEGORY URL TEST")
    log("=" * 70)

    # Use a real URL from category page
    real_urls = [
        "/vladivostok/auto/wheel/disc/komplekt-diskov-rays-g25-r17-7.5-j-et35-5-100-450-136342005.html",
        "/ni/vladivostok/zapchasti/porshni-4d35-mitsubishi-canter-me018825-98836529.html",
    ]
    for path in real_urls:
        url = f"https://www.farpost.ru{path}"
        try:
            r = fp.get(url, timeout=15, allow_redirects=True)
            log(f"\n  [{path[-50:]}] {r.status_code} {len(r.content)}B final={r.url[-60:]}")
            if r.status_code == 200 and len(r.content) > 10000:
                m = re.search(r'data-bulletin-price=["\'](\d+)["\']', r.text)
                price = m.group(1) if m else "?"
                m2 = re.search(r'data-field="subject"[^>]*>(.*?)</span>', r.text, re.DOTALL)
                title = re.sub(r'<[^>]+>', '', m2.group(1)).strip()[:60] if m2 else "?"
                photos = len(re.findall(r'data-image-info="(\{.*?\})"', r.text))
                log(f"    title={title} price={price} photos={photos}")
        except Exception as e:
            log(f"  [{path[-50:]}] ERR {e}")
        time.sleep(1.5)

    # ================================================================
    # SUMMARY
    # ================================================================
    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log("DIAG V125 DONE")

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
