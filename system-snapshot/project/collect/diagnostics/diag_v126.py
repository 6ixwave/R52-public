"""DIAG V126: FOCUSED — WB IMAGES via catalog.wb.ru + FARPOST /{city}/{id}.html
2026-11-12

Быстрый, сфокусированный:
1. WB: catalog.wb.ru/cards/v1/detail — JSON с ценами и изображениями
2. WB: basket card.json → ищем поля с URL
3. WB: curl_cffi HTML detail.aspx → og:image, data-src
4. Farpost: /vladivostok/{id}.html → полный парсинг (title, price, photos, specs)
"""

import json
import re
import sys
import time
import traceback
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

OUT = Path(__file__).parent / "diag_v126_result.log"
lines = []

def log(msg=""):
    lines.append(msg)
    print(msg, flush=True)

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    return s

def main():
    log("=" * 70)
    log("DIAG V126: WB CATALOG API + FARPOST CITY/ID (2026-11-12)")
    log("=" * 70)

    sess = make_session()

    # ================================================================
    # PART 1: WB — catalog.wb.ru/cards/v1/detail (JSON API)
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 1: WB catalog.wb.ru/cards/v1/detail")
    log("=" * 70)

    test_ids = [259671175, 199298871, 1097975275]

    for nm in test_ids:
        log(f"\n  --- nm={nm} ---")
        urls = [
            f"https://catalog.wb.ru/cards/v1/detail?curr=rub&nm={nm}",
            f"https://catalog.wb.ru/cards/v2/detail?curr=rub&nm={nm}",
        ]
        for url in urls:
            try:
                r = sess.get(url, timeout=12, headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.wildberries.ru/",
                })
                log(f"  [{url.split('?')[0][-25:]}] {r.status_code} {len(r.content)}B")
                if r.status_code == 200:
                    data = r.json()
                    # Save full response
                    out_f = Path(__file__).parent / f"v126_wb_catalog_{nm}.json"
                    out_f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    log(f"    saved → {out_f.name}")
                    # Parse structure
                    if "data" in data:
                        d = data["data"]
                        products = d.get("products", [])
                        log(f"    products: {len(products)}")
                        if products:
                            p = products[0]
                            log(f"    name: {p.get('name','?')[:60]}")
                            log(f"    brand: {p.get('brand','?')}")
                            # Price
                            price = p.get("sizes", [])
                            if price:
                                sz = price[0]
                                log(f"    price: basic={sz.get('price',{}).get('basic')} "
                                    f"product={sz.get('price',{}).get('product')} "
                                    f"old={sz.get('price',{}).get('old')}")
                                log(f"    qty: {sz.get('qty')}")
                            # Images
                            imgs = p.get("imgSizes", [])
                            log(f"    imgSizes: {len(imgs)} entries")
                            for img in imgs[:5]:
                                log(f"      {img}")
                            # Also check top-level
                            for key in ("images", "photos", "img", "pictures"):
                                if key in p:
                                    log(f"    {key}: {str(p[key])[:200]}")
                            # Full keys
                            log(f"    product keys: {list(p.keys())}")
                    else:
                        log(f"    top keys: {list(data.keys())}")
                        log(f"    body[:500]: {r.text[:500]}")
                    break  # Only first successful URL
            except Exception as e:
                log(f"  [{url[-30:]}] ERR {type(e).__name__}: {e}")
            time.sleep(1)

    # ================================================================
    # PART 2: WB — basket card.json deep scan for image fields
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 2: WB basket card.json — ALL FIELDS SCAN")
    log("=" * 70)

    nm = 259671175
    vol = nm // 100000
    part = nm // 1000
    basket_num = 16  # from v124

    card_url = f"https://basket-{basket_num:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/card.json"
    try:
        r = sess.get(card_url, timeout=12, headers={"Accept": "application/json"})
        log(f"\n  card.json: {r.status_code} {len(r.content)}B")
        if r.status_code == 200:
            card = r.json()
            # Scan ALL values for URL-like strings
            log(f"  top keys: {list(card.keys())}")
            url_pattern = re.compile(r'https?://\S+')
            found_urls = []
            def scan(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        scan(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:10]):
                        scan(v, f"{path}[{i}]")
                elif isinstance(obj, str) and url_pattern.search(obj):
                    found_urls.append((path, obj[:120]))
            scan(card)
            log(f"  URL-like values: {len(found_urls)}")
            for p, v in found_urls[:20]:
                log(f"    {p}: {v}")
            # media section
            log(f"  media: {json.dumps(card.get('media', {}), ensure_ascii=False)}")
            # colors / full_colors
            log(f"  colors: {card.get('colors', [])}")
            log(f"  full_colors: {json.dumps(card.get('full_colors', [])[:3], ensure_ascii=False)}")
    except Exception as e:
        log(f"  card.json ERR: {e}")

    # ================================================================
    # PART 3: WB — curl_cffi HTML (detail.aspx) for og:image
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 3: WB HTML via curl_cffi")
    log("=" * 70)

    try:
        from curl_cffi import requests as cffi_req
        nm = 259671175
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        r = cffi_req.get(url, impersonate="chrome", timeout=20)
        log(f"\n  [cffi detail.aspx] {r.status_code} {len(r.content)}B")
        if r.status_code == 200:
            html = r.text
            # og:image
            m = re.search(r'og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
            if m:
                log(f"  og:image: {m.group(1)[:100]}")
            # All wbbasket URLs
            wb_imgs = re.findall(r'(https://[^"\']*wbbasket\.ru[^"\']*)', html)
            log(f"  wbbasket URLs in HTML: {len(wb_imgs)}")
            for u in wb_imgs[:10]:
                log(f"    {u[:100]}")
            # All wbstatic URLs
            ws_imgs = re.findall(r'(https://[^"\']*wbstatic\.net[^"\']*)', html)
            log(f"  wbstatic URLs: {len(ws_imgs)}")
            for u in ws_imgs[:10]:
                log(f"    {u[:100]}")
            # data-src, src with images
            src_imgs = re.findall(r'(?:src|data-src|data-original)=["\']([^"\']*\.(?:jpg|jpeg|png|webp))["\']', html, re.I)
            log(f"  img src/data-src: {len(src_imgs)}")
            for u in src_imgs[:10]:
                log(f"    {u[:100]}")
            # Embedded JSON with images
            m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
            if m:
                log(f"  __INITIAL_STATE__ found ({len(m.group(1))}B)")
        elif r.status_code == 498:
            log(f"  (WBAAS antibot challenge)")
        else:
            log(f"  body[:300]: {r.text[:300]}")
    except ImportError:
        log("  curl_cffi not available")
    except Exception as e:
        log(f"  ERR: {type(e).__name__}: {e}")

    # ================================================================
    # PART 4: FARPOST — /vladivostok/{id}.html FULL PARSE
    # ================================================================
    log("\n" + "=" * 70)
    log("PART 4: FARPOST /vladivostok/{id}.html")
    log("=" * 70)

    fp = make_session()
    # Warmup
    try:
        r = fp.get("https://www.farpost.ru/", timeout=15)
        log(f"\n  Warmup: {r.status_code} {len(r.content)}B cookies={list(fp.cookies.keys())}")
    except Exception as e:
        log(f"  Warmup ERR: {e}")
    time.sleep(2)

    test_ids_fp = ["130693618", "136342005", "98836529"]

    for bid in test_ids_fp:
        log(f"\n  --- bulletin {bid} ---")
        url = f"https://www.farpost.ru/vladivostok/{bid}.html"
        try:
            r = fp.get(url, timeout=20, allow_redirects=True)
            final = r.url
            log(f"  [{bid}] {r.status_code} {len(r.content)}B final={final[-70:]}")

            if r.status_code == 200 and len(r.content) > 10000:
                html = r.text
                # Save HTML for reference
                html_f = Path(__file__).parent / f"v126_fp_{bid}.html"
                html_f.write_text(html, encoding="utf-8")
                log(f"    saved → {html_f.name}")

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
                        if isinstance(ld, dict) and ld.get("@type") in ("Product", "Offer"):
                            log(f"    JSON-LD: type={ld.get('@type')} name={str(ld.get('name','?'))[:50]}")
                            offers = ld.get("offers", {})
                            if isinstance(offers, dict):
                                log(f"      price={offers.get('price')} currency={offers.get('priceCurrency')}")
                                log(f"      avail={offers.get('availability','?')[-30:]}")
                            break
                    except (json.JSONDecodeError, ValueError):
                        pass

                # PHOTOS: data-image-info
                photos = re.findall(r'data-image-info="(\{.*?\})"', html)
                log(f"    data-image-info: {len(photos)} photos")
                if photos:
                    try:
                        first = json.loads(photos[0].replace("&quot;", '"').replace("&amp;", "&"))
                        log(f"    first photo keys: {list(first.keys())}")
                        log(f"    first photo msrc: {first.get('msrc', first.get('src','?'))[:80]}")
                    except (json.JSONDecodeError, ValueError):
                        pass

                # LIGHTBOX
                lb_match = re.search(r'baza\.lightbox\.init\(\s*(\[[\s\S]*?\])\s*\)', html)
                if lb_match:
                    try:
                        lb_items = json.loads(lb_match.group(1))
                        log(f"    lightbox: {len(lb_items)} items")
                        if lb_items:
                            log(f"    lb[0]: {json.dumps(lb_items[0], ensure_ascii=False)[:100]}")
                    except json.JSONDecodeError:
                        log(f"    lightbox raw[:100]: {lb_match.group(1)[:100]}")

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

                # og:image
                m = re.search(r'og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
                if m:
                    log(f"    og:image: {m.group(1)[:80]}")

                # All farpost CDN images
                fp_imgs = re.findall(r'(https://[^"\']*farpost\.ru[^"\']*\.(?:jpg|jpeg|png))', html, re.I)
                log(f"    farpost CDN imgs: {len(fp_imgs)}")
                for u in fp_imgs[:5]:
                    log(f"      {u[:90]}")

            elif r.status_code == 200:
                log(f"    (200 but short: {len(r.content)}B)")
            else:
                log(f"    (HTTP {r.status_code})")
                if r.status_code == 404:
                    log(f"    body[:200]: {r.text[:200]}")
        except Exception as e:
            log(f"  [{bid}] ERR {type(e).__name__}: {e}")
        time.sleep(2)

    # ================================================================
    # SUMMARY
    # ================================================================
    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log("DIAG V126 DONE")

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
