"""
SPEED DIAG — 4 площадки, 2-3 стратегии каждая.
Цель: реальные title + price, а не HTTP 200.
Запуск: C:\\Python315\\python.exe diag_speed.py
"""
import json, re, time, sys
from pathlib import Path

OUT = Path(__file__).parent
RESULTS = {}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def try_cffi(url, headers=None, timeout=15):
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, headers=headers or {}, timeout=timeout, impersonate="chrome")
        return r
    except Exception as e:
        log(f"  cffi err: {e}")
        return None

def try_requests(url, headers=None, timeout=15):
    import requests
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    if headers:
        h.update(headers)
    try:
        return requests.get(url, headers=h, timeout=timeout)
    except Exception as e:
        log(f"  req err: {e}")
        return None

def extract_jsonld(html):
    blocks = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            blocks.append(json.loads(m.group(1).strip()))
        except:
            pass
    return blocks

def find_price(html):
    prices = []
    pats = [
        r'data-bulletin-price=["\'](\d+)["\']',
        r'"price"\s*:\s*"?(\d+)',
        r'"basic"\s*:\s*(\d+)',
        r'([\d\s]{4,})\s*₽',
        r'itemprop=["\']price["\'][^>]*content=["\'](\d+)',
        r'content=["\'](\d+)["\'][^>]*itemprop=["\']price',
        r'class="[^"]*(?:price|Price)[^"]*"[^>]*>\s*([\d\s]+)',
    ]
    for pat in pats:
        for m in re.finditer(pat, html, re.I):
            try:
                p = int(re.sub(r'[^\d]', '', m.group(1)))
                if 50 <= p <= 200_000_000:
                    prices.append(p)
            except:
                pass
    return prices

def find_title(html):
    for pat in [
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
        r'<meta[^>]*name=["\']twitter:title["\'][^>]*content=["\']([^"\']+)',
        r'<title[^>]*>([^<]+)</title>',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            t = m.group(1).strip()
            if len(t) > 3:
                return t
    return None

# ================================================================
# FARPOST
# ================================================================
def test_farpost():
    log("=== FARPOST ===")
    res = []

    # S1: main → find listing links → fetch
    log("FP S1: main → listing link → fetch")
    r = try_cffi("https://www.farpost.ru/vladivostok/", timeout=20)
    if r and r.status_code == 200:
        html = r.text
        log(f"  Main: {len(html)}B")
        # Listing pattern: /vladivostok/{cat}/{slug}-{digits}.html
        links = re.findall(r'href="(/vladivostok/[^/]+/[^"]+?-(\d{5,})\.html)"', html)
        if not links:
            links = re.findall(r'href="(https://www\.farpost\.ru/vladivostok/[^/]+/[^"]+?-(\d{5,})\.html)"', html)
            links = [(l[0].replace("https://www.farpost.ru", ""), l[1]) for l in links]
        log(f"  Links found: {len(links)}")

        if links:
            path, lid = links[0]
            if not path.startswith("/"):
                path = "/" + path
            target = f"https://www.farpost.ru{path}"
            log(f"  → {target}")
            time.sleep(2)
            r2 = try_cffi(target, timeout=20)
            if r2 and r2.status_code == 200:
                h2 = r2.text
                title = find_title(h2)
                prices = find_price(h2)
                jld = extract_jsonld(h2)
                is_listing = any(k in h2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field', 'bulletin_id'])
                log(f"  Listing: {len(h2)}B, title={title!r}, prices={prices[:5]}, jld={len(jld)}, is_listing={is_listing}")
                (OUT / "fp_listing.html").write_text(h2, encoding="utf-8")
                res.append({"s": "main→listing", "code": 200, "title": title, "prices": prices[:5], "jld": len(jld), "is_listing": is_listing, "url": target})
            else:
                res.append({"s": "main→listing", "code": r2.status_code if r2 else "ERR", "url": target})
    else:
        res.append({"s": "main→listing", "code": r.status_code if r else "ERR"})

    # S2: category → listing
    log("FP S2: category → listing")
    time.sleep(2)
    for cat in ["transport/avto", "electro/telefony", "dom/obyasn"]:
        r = try_cffi(f"https://www.farpost.ru/vladivostok/{cat}/", timeout=15)
        if r and r.status_code == 200 and len(r.text) > 3000:
            links = re.findall(r'href="(/vladivostok/[^/]+/[^"]+?-(\d{5,})\.html)"', r.text)
            if links:
                path, lid = links[0]
                target = f"https://www.farpost.ru{path}"
                time.sleep(2)
                r2 = try_cffi(target, timeout=15)
                if r2 and r2.status_code == 200:
                    title = find_title(r2.text)
                    prices = find_price(r2.text)
                    is_listing = any(k in r2.text for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field'])
                    log(f"  Cat {cat}: {len(links)} links → {target}")
                    log(f"    title={title!r}, prices={prices[:3]}, listing={is_listing}")
                    res.append({"s": f"cat:{cat}", "code": 200, "title": title, "prices": prices[:3], "is_listing": is_listing, "url": target})
                    break
        time.sleep(1)

    RESULTS["FARPOST"] = res

# ================================================================
# WILDBERRIES
# ================================================================
def test_wb():
    log("=== WILDBERRIES ===")
    res = []

    # Popular product IDs (large, well-known items)
    test_ids = ["172636515", "169774481", "156901234"]

    # S1: card.wb.ru JSON API
    log("WB S1: card.wb.ru/cards/v2/detail")
    for nid in test_ids:
        url = f"https://card.wb.ru/cards/v2/detail?nm={nid}"
        hdrs = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/", "Origin": "https://www.wildberries.ru"}
        r = try_cffi(url, headers=hdrs, timeout=15)
        if r:
            log(f"  {nid}: {r.status_code}, {len(r.content)}B")
            if r.status_code == 200:
                try:
                    data = r.json()
                    prods = data.get("data", {}).get("products", [])
                    if prods:
                        p = prods[0]
                        name = p.get("name", "?")
                        brand = p.get("brand", "?")
                        sizes = p.get("sizes", [])
                        prices = [s.get("price", {}).get("basic", 0) for s in sizes if s.get("price", {}).get("basic")]
                        log(f"  → {name} | {brand} | {prices[:3]}")
                        res.append({"s": "card-api", "id": nid, "name": name, "brand": brand, "prices": prices[:3]})
                    else:
                        log(f"  → empty products")
                except Exception as e:
                    log(f"  → parse: {e}")
            time.sleep(2)

    # S2: basket CDN
    log("WB S2: basket CDN")
    for nid in test_ids[:2]:
        vol = int(nid) // 100000000
        part = int(nid) // 1000000
        for pat in [
            f"https://basket-{int(nid) % 10}.wbbasket.ru/vol{vol}/part{part}/{nid}/info/card.json",
            f"https://bat.wb.ru/{nid}/card.json",
        ]:
            r = try_cffi(pat, timeout=12)
            if r and r.status_code == 200 and len(r.content) > 50:
                try:
                    d = r.json()
                    name = d.get("name", d.get("imtl", {}).get("name", "?"))
                    log(f"  basket {nid}: {name!r}")
                    res.append({"s": "basket", "id": nid, "name": name, "url": pat})
                    break
                except:
                    pass
            time.sleep(1)

    # S3: HTML page
    log("WB S3: HTML detail.aspx")
    nid = test_ids[0]
    r = try_cffi(f"https://www.wildberries.ru/catalog/{nid}/detail.aspx", timeout=20)
    if r:
        log(f"  {nid}: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 5000:
            title = find_title(r.text)
            prices = find_price(r.text)
            jld = extract_jsonld(r.text)
            log(f"  title={title!r}, prices={prices[:3]}, jld={len(jld)}")
            (OUT / "wb_detail.html").write_text(r.text, encoding="utf-8")
            res.append({"s": "html", "id": nid, "title": title, "prices": prices[:3], "jld": len(jld)})

    RESULTS["WB"] = res

# ================================================================
# OZON
# ================================================================
def test_ozon():
    log("=== OZON ===")
    res = []

    # S1: direct product page
    log("OZON S1: direct product page")
    # Try a few product URLs
    urls = [
        "https://www.ozon.ru/product/279845716/",
        "https://www.ozon.ru/product/1126996368/",
        "https://www.ozon.ru/product/176375183/",
    ]
    for url in urls:
        r = try_cffi(url, timeout=20)
        if r:
            log(f"  {url}: {r.status_code}, {len(r.content)}B")
            if r.status_code == 200 and len(r.content) > 5000:
                html = r.text
                title = find_title(html)
                prices = find_price(html)
                jld = extract_jsonld(html)
                widgets = re.findall(r'data-widget="([^"]+)"', html)
                # Check for "not found"
                not_found = "Такой страницы не существует" in html or 'data-widget="error"' in html
                log(f"  title={title!r}, prices={prices[:5]}, jld={len(jld)}, widgets={widgets[:4]}, nf={not_found}")
                if not not_found:
                    (OUT / "ozon_page.html").write_text(html, encoding="utf-8")
                    res.append({"s": "direct", "url": url, "title": title, "prices": prices[:5], "jld": len(jld), "widgets": widgets[:4]})
            time.sleep(3)

    # S2: search page → find product → fetch
    log("OZON S2: search → product")
    r = try_cffi("https://www.ozon.ru/search/?text=iphone&from_global=true", timeout=20)
    if r and r.status_code == 200:
        html = r.text
        log(f"  Search: {len(html)}B")
        # Find product links
        prods = re.findall(r'href="(/product/[a-z0-9\-]*(\d+)/?)"', html)
        log(f"  Product links: {len(prods)}")
        if prods:
            path, pid = prods[0]
            target = f"https://www.ozon.ru{path}"
            time.sleep(3)
            r2 = try_cffi(target, timeout=20)
            if r2 and r2.status_code == 200:
                title = find_title(r2.text)
                prices = find_price(r2.text)
                log(f"  → {target}: title={title!r}, prices={prices[:3]}")
                res.append({"s": "search→product", "url": target, "title": title, "prices": prices[:3]})

    RESULTS["OZON"] = res

# ================================================================
# AVITO
# ================================================================
def test_avito():
    log("=== AVITO ===")
    res = []

    # S1: www main → find ad → fetch
    log("AVITO S1: www → ad")
    r = try_cffi("https://www.avito.ru/vladivostok", timeout=20)
    if r:
        log(f"  Main: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 3000:
            html = r.text
            # Ad links: /vladivostok/{cat}/{slug}/{9+ digits}
            ads = re.findall(r'href="(/vladivostok/[^"]*/(\d{9,}))"', html)
            log(f"  Ad links: {len(ads)}")
            if ads:
                path, aid = ads[0]
                target = f"https://www.avito.ru{path}"
                time.sleep(3)
                r2 = try_cffi(target, timeout=20)
                if r2:
                    log(f"  Ad: {r2.status_code}, {len(r2.content)}B")
                    if r2.status_code == 200:
                        title = find_title(r2.text)
                        prices = find_price(r2.text)
                        jld = extract_jsonld(r2.text)
                        is_captcha = "captcha" in r2.text.lower() or "smartcaptcha" in r2.text.lower()
                        log(f"  title={title!r}, prices={prices[:3]}, jld={len(jld)}, captcha={is_captcha}")
                        (OUT / "avito_ad.html").write_text(r2.text, encoding="utf-8")
                        res.append({"s": "www→ad", "url": target, "title": title, "prices": prices[:3], "jld": len(jld), "captcha": is_captcha})
                    elif r2.status_code in (429, 439):
                        res.append({"s": "www→ad", "code": r2.status_code, "url": target})

    # S2: m.avito.ru
    log("AVITO S2: m.avito.ru")
    time.sleep(3)
    r = try_cffi("https://m.avito.ru/vladivostok", timeout=20)
    if r:
        log(f"  Mobile: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 3000:
            html = r.text
            ads = re.findall(r'href="([^"]*/(\d{9,}))"', html)
            log(f"  Mobile ads: {len(ads)}")
            if ads:
                path, aid = ads[0]
                target = f"https://m.avito.ru{path}" if not path.startswith("http") else path
                time.sleep(2)
                r2 = try_cffi(target, timeout=20)
                if r2 and r2.status_code == 200:
                    title = find_title(r2.text)
                    prices = find_price(r2.text)
                    log(f"  → {target}: title={title!r}, prices={prices[:3]}")
                    res.append({"s": "m→ad", "url": target, "title": title, "prices": prices[:3]})

    # S3: specific ad URL pattern /ad/{id}
    log("AVITO S3: /ad/{id} pattern")
    time.sleep(2)
    # Try a known-format ID (from main page if we found one, else guess)
    r = try_cffi("https://www.avito.ru/ad/1234567890", timeout=15)
    if r:
        log(f"  /ad/1234567890: {r.status_code}, {len(r.content)}B")
        res.append({"s": "ad-pattern", "code": r.status_code, "size": len(r.content)})

    RESULTS["AVITO"] = res

# ================================================================
if __name__ == "__main__":
    log("SPEED DIAG START")
    t0 = time.time()

    for fn in [test_farpost, test_wb, test_ozon, test_avito]:
        try:
            fn()
        except Exception as e:
            log(f"  FATAL {fn.__name__}: {e}")
            RESULTS[fn.__name__.replace("test_", "").upper()] = [{"error": str(e)}]
        time.sleep(2)

    elapsed = time.time() - t0
    log(f"\nSPEED DIAG DONE in {elapsed:.1f}s")

    out = OUT / "speed_result.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")

    log("\n=== SUMMARY ===")
    for plat, res in RESULTS.items():
        has = any(r.get("title") and r.get("prices") for r in res if isinstance(r, dict))
        detail = ""
        if has:
            good = [r for r in res if r.get("title") and r.get("prices")]
            detail = f" → {good[0].get('title','?')[:40]} | {good[0].get('prices',[])[0]}"
        log(f"  {plat}: {len(res)} strategies, DATA={'YES' if has else 'NO'}{detail}")
