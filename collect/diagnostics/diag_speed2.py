"""
SPEED2 — инспекция реального HTML + новые стратегии.
Фокус: Farpost (найти паттерн ссылок), WB (новые CDN), Ozon (search HTML), Avito (категория).
"""
import json, re, time, sys
from pathlib import Path

OUT = Path(__file__).parent
RESULTS = {}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def cffi_get(url, headers=None, timeout=20):
    try:
        from curl_cffi import requests as cr
        return cr.get(url, headers=headers or {}, timeout=timeout, impersonate="chrome")
    except Exception as e:
        log(f"  cffi err: {e}")
        return None

def find_price(html):
    prices = []
    pats = [
        r'data-bulletin-price=["\'](\d+)["\']',
        r'"price"\s*:\s*"?(\d+)',
        r'"basic"\s*:\s*(\d+)',
        r'([\d\s]{4,})\s*₽',
        r'itemprop=["\']price["\'][^>]*content=["\'](\d+)',
        r'class="[^"]*(?:price|Price)[^"]*"[^>]*>\s*([\d\s]+)',
        r'"price"\s*:\s*\{[^}]*"basic"\s*:\s*(\d+)',
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
# FARPOST — инспекция + парсинг
# ================================================================
def test_farpost():
    log("=== FARPOST ===")
    res = []

    r = cffi_get("https://www.farpost.ru/vladivostok/", timeout=25)
    if not r or r.status_code != 200:
        log(f"  Main: {r.status_code if r else 'ERR'}")
        RESULTS["FARPOST"] = [{"s": "main", "code": r.status_code if r else "ERR"}]
        return

    html = r.text
    log(f"  Main: {len(html)}B")
    (OUT / "fp_main2.html").write_text(html, encoding="utf-8")

    # Инспекция: все href с цифрами
    all_hrefs = re.findall(r'href="([^"]+)"', html)
    log(f"  Total hrefs: {len(all_hrefs)}")

    # Паттерны ссылок на объявления
    patterns = {
        "slug-digits.html": r'href="([^"]*?-\d{5,}\.html)"',
        "bulletin": r'href="([^"]*bulletin[^"]*)"',
        "viewbull": r'href="([^"]*viewbull[^"]*)"',
        "digits-only": r'href="([^"]*/(\d{5,}))"',
        "any-digits": r'href="([^"]*(\d{6,})[^"]*)"',
    }
    found_links = {}
    for name, pat in patterns.items():
        matches = re.findall(pat, html)
        found_links[name] = matches[:5]
        log(f"  Pattern '{name}': {len(matches)} → {matches[:3]}")

    # Также ищем data-атрибуты
    data_ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html)
    log(f"  data-bulletin-id: {len(data_ids)} → {data_ids[:5]}")

    data_urls = re.findall(r'data-url=["\']([^"\']+)["\']', html)
    log(f"  data-url: {len(data_urls)} → {data_urls[:5]}")

    # JSON-LD
    jld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    log(f"  JSON-LD blocks: {len(jld)}")

    # Ищем ItemList (список объявлений)
    itemlist = re.findall(r'"@type"\s*:\s*"ItemList"', html)
    log(f"  ItemList: {len(itemlist)}")

    # Попробуем найти конкретные URL объявлений из любого паттерна
    target_url = None
    for name in ["slug-digits.html", "any-digits", "digits-only", "bulletin"]:
        if found_links.get(name):
            raw = found_links[name][0]
            if isinstance(raw, tuple):
                raw = raw[0]
            if raw.startswith("/"):
                target_url = f"https://www.farpost.ru{raw}"
            elif raw.startswith("http"):
                target_url = raw
            else:
                target_url = f"https://www.farpost.ru/vladivostok/{raw}"
            log(f"  Target from '{name}': {target_url}")
            break

    if not target_url:
        # Последний шанс: ищем любой .html с 5+ цифрами
        m = re.search(r'href="(/vladivostok/[^"]*?-\d{5,}\.html)"', html)
        if m:
            target_url = f"https://www.farpost.ru{m.group(1)}"
            log(f"  Target (fallback): {target_url}")

    if target_url:
        time.sleep(2)
        r2 = cffi_get(target_url, timeout=25)
        if r2 and r2.status_code == 200:
            h2 = r2.text
            log(f"  Listing: {len(h2)}B")
            (OUT / "fp_listing2.html").write_text(h2, encoding="utf-8")
            title = find_title(h2)
            prices = find_price(h2)
            is_listing = any(k in h2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field', 'bulletin_id', 'og:title'])
            log(f"  title={title!r}")
            log(f"  prices={prices[:5]}")
            log(f"  is_listing={is_listing}")
            res.append({"s": "listing", "url": target_url, "title": title, "prices": prices[:5], "is_listing": is_listing, "size": len(h2)})
        else:
            log(f"  Listing: {r2.status_code if r2 else 'ERR'}")
            res.append({"s": "listing", "url": target_url, "code": r2.status_code if r2 else "ERR"})

    RESULTS["FARPOST"] = res

# ================================================================
# WILDBERRIES — новые CDN + API
# ================================================================
def test_wb():
    log("=== WILDBERRIES ===")
    res = []
    test_ids = ["172636515", "169774481"]

    # S1: wbx-content-v2.wbbasket.ru (новый CDN)
    log("WB S1: wbx-content-v2")
    for nid in test_ids:
        vol = int(nid) // 100000000
        part = int(nid) // 1000000
        url = f"https://wbx-content-v2.wbbasket.ru/vol{vol}/part{part}/{nid}/info/card.json"
        r = cffi_get(url, timeout=12)
        if r:
            log(f"  {nid} vol{vol}/part{part}: {r.status_code}, {len(r.content)}B")
            if r.status_code == 200 and len(r.content) > 50:
                try:
                    d = r.json()
                    name = d.get("name", "?")
                    brand = d.get("brand", "?")
                    log(f"  → {name} | {brand}")
                    res.append({"s": "wbx-v2", "id": nid, "name": name, "brand": brand, "url": url})
                except Exception as e:
                    log(f"  → parse: {e}")
        time.sleep(1)

    # S2: static-basket.wbbasket.ru
    log("WB S2: static-basket")
    for nid in test_ids[:1]:
        vol = int(nid) // 100000000
        part = int(nid) // 1000000
        url = f"https://static-basket-{int(nid) % 10}.wbbasket.ru/vol{vol}/part{part}/{nid}/info/card.json"
        r = cffi_get(url, timeout=12)
        if r:
            log(f"  static-basket-{int(nid)%10}: {r.status_code}, {len(r.content)}B")
            if r.status_code == 200:
                try:
                    d = r.json()
                    res.append({"s": "static-basket", "id": nid, "name": d.get("name","?"), "url": url})
                except:
                    pass
        time.sleep(1)

    # S3: card.wb.ru с разными заголовками
    log("WB S3: card.wb.ru (new headers)")
    nid = test_ids[0]
    hdrs = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://www.wildberries.ru/catalog/{nid}/detail.aspx",
        "Origin": "https://www.wildberries.ru",
        "X-Requested-With": "XMLHttpRequest",
    }
    for api in [
        f"https://card.wb.ru/cards/v2/detail?nm={nid}",
        f"https://card.wb.ru/cards/v1/detail?nm={nid}",
        f"https://card.wb.ru/cards/v1/detail?nm={nid}&spp=30",
    ]:
        r = cffi_get(api, headers=hdrs, timeout=12)
        if r:
            log(f"  {api.split('?')[0]}: {r.status_code}, {len(r.content)}B")
            if r.status_code == 200:
                try:
                    data = r.json()
                    prods = data.get("data", {}).get("products", [])
                    if prods:
                        p = prods[0]
                        sizes = p.get("sizes", [])
                        prices = [s.get("price", {}).get("basic", 0) for s in sizes if s.get("price", {}).get("basic")]
                        log(f"  → {p.get('name','?')} | {prices[:3]}")
                        res.append({"s": "card-api", "id": nid, "name": p.get("name","?"), "prices": prices[:3]})
                except Exception as e:
                    log(f"  → {e}")
        time.sleep(2)

    # S4: HTML с cookie warmup
    log("WB S4: HTML warmup")
    r = cffi_get("https://www.wildberries.ru/", timeout=15)
    if r:
        log(f"  Warmup: {r.status_code}, cookies={list(r.cookies.keys()) if hasattr(r,'cookies') else 'N/A'}")
    time.sleep(2)
    r = cffi_get(f"https://www.wildberries.ru/catalog/{nid}/detail.aspx", timeout=20)
    if r:
        log(f"  Detail: {r.status_code}, {len(r.content)}B")
        if r.status_code in (200, 498) and len(r.content) > 3000:
            title = find_title(r.text)
            prices = find_price(r.text)
            log(f"  title={title!r}, prices={prices[:3]}")
            (OUT / "wb_detail2.html").write_text(r.text, encoding="utf-8")
            res.append({"s": "html", "id": nid, "code": r.status_code, "title": title, "prices": prices[:3]})

    RESULTS["WB"] = res

# ================================================================
# OZON — search + mobile
# ================================================================
def test_ozon():
    log("=== OZON ===")
    res = []

    # S1: search page — сохранить и инспектировать
    log("OZON S1: search page")
    r = cffi_get("https://www.ozon.ru/search/?text=iphone+15&from_global=true", timeout=25)
    if r:
        log(f"  Search: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 3000:
            html = r.text
            (OUT / "ozon_search.html").write_text(html, encoding="utf-8")
            # Product links
            prods = re.findall(r'href="(/product/[^"]+?/)"', html)
            prods2 = re.findall(r'"link"\s*:\s*"(?:/product/|https://www\.ozon\.ru/product/)([^"]+)"', html)
            prods3 = re.findall(r'data-widget="t-pc-product-card[^"]*"[^>]*data-id="(\d+)"', html)
            log(f"  Links: {len(prods)}, data-link: {len(prods2)}, widgets: {len(prods3)}")
            log(f"  Sample: {prods[:3] or prods2[:3] or prods3[:3]}")

            # JSON state
            state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
            log(f"  __INITIAL_STATE__: {'YES' if state_m else 'NO'}")

            # Find first product
            target = None
            if prods:
                target = f"https://www.ozon.ru{prods[0]}"
            elif prods2:
                target = f"https://www.ozon.ru/product/{prods2[0]}"
            if target:
                time.sleep(3)
                r2 = cffi_get(target, timeout=20)
                if r2 and r2.status_code == 200:
                    title = find_title(r2.text)
                    prices = find_price(r2.text)
                    log(f"  → {target}: {r2.status_code}, {len(r2.content)}B")
                    log(f"    title={title!r}, prices={prices[:3]}")
                    res.append({"s": "search→product", "url": target, "title": title, "prices": prices[:3]})

    # S2: mobile ozon
    log("OZON S2: mobile")
    time.sleep(2)
    r = cffi_get("https://m.ozon.ru/search/?text=iphone", timeout=20)
    if r:
        log(f"  Mobile: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 2000:
            prods = re.findall(r'href="([^"]*product[^"]*?/(\d+)/?)"', r.text)
            log(f"  Mobile products: {len(prods)}")
            if prods:
                res.append({"s": "mobile-search", "count": len(prods), "sample": prods[0][0]})

    # S3: direct with mobile UA
    log("OZON S3: direct mobile UA")
    time.sleep(2)
    hdrs = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    r = cffi_get("https://www.ozon.ru/product/279845716/", headers=hdrs, timeout=20)
    if r:
        log(f"  Direct mobile: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 5000:
            title = find_title(r.text)
            prices = find_price(r.text)
            log(f"  title={title!r}, prices={prices[:3]}")
            res.append({"s": "direct-mobile", "title": title, "prices": prices[:3]})

    RESULTS["OZON"] = res

# ================================================================
# AVITO — категория + задержка
# ================================================================
def test_avito():
    log("=== AVITO ===")
    res = []

    # S1: категория с задержкой
    log("AVITO S1: category page")
    time.sleep(3)
    r = cffi_get("https://www.avito.ru/vladivostok/telefony", timeout=25)
    if r:
        log(f"  Cat: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 5000:
            html = r.text
            (OUT / "avito_cat.html").write_text(html, encoding="utf-8")
            ads = re.findall(r'href="(/vladivostok/[^"]*/(\d{9,}))"', html)
            ads2 = re.findall(r'href="([^"]*?/(\d{9,})(?:\?[^"]*)?)"', html)
            log(f"  Ads: {len(ads)}, ads2: {len(ads2)}")
            log(f"  Sample: {(ads or ads2)[:3]}")

            if ads:
                path, aid = ads[0]
                target = f"https://www.avito.ru{path}"
                time.sleep(4)
                r2 = cffi_get(target, timeout=20)
                if r2 and r2.status_code == 200:
                    title = find_title(r2.text)
                    prices = find_price(r2.text)
                    jld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r2.text, re.DOTALL)
                    is_captcha = "captcha" in r2.text.lower()
                    log(f"  Ad: {len(r2.content)}B, title={title!r}, prices={prices[:3]}, jld={len(jld)}, cap={is_captcha}")
                    (OUT / "avito_ad2.html").write_text(r2.text, encoding="utf-8")
                    res.append({"s": "cat→ad", "url": target, "title": title, "prices": prices[:3], "jld": len(jld), "captcha": is_captcha})

    # S2: m.avito.ru категория
    log("AVITO S2: mobile category")
    time.sleep(4)
    r = cffi_get("https://m.avito.ru/vladivostok/telefony", timeout=25)
    if r:
        log(f"  Mobile cat: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 3000:
            html = r.text
            (OUT / "avito_mcat.html").write_text(html, encoding="utf-8")
            ads = re.findall(r'href="([^"]*?/(\d{9,}))"', html)
            log(f"  Mobile ads: {len(ads)}")
            if ads:
                path, aid = ads[0]
                target = f"https://m.avito.ru{path}" if not path.startswith("http") else path
                time.sleep(3)
                r2 = cffi_get(target, timeout=20)
                if r2 and r2.status_code == 200:
                    title = find_title(r2.text)
                    prices = find_price(r2.text)
                    log(f"  → {target}: title={title!r}, prices={prices[:3]}")
                    res.append({"s": "m-cat→ad", "url": target, "title": title, "prices": prices[:3]})

    # S3: search API (публичный)
    log("AVITO S3: search")
    time.sleep(3)
    r = cffi_get("https://www.avito.ru/vladivostok/search?query=iphone", timeout=20)
    if r:
        log(f"  Search: {r.status_code}, {len(r.content)}B")
        if r.status_code == 200 and len(r.content) > 3000:
            ads = re.findall(r'href="(/vladivostok/[^"]*/(\d{9,}))"', r.text)
            log(f"  Search ads: {len(ads)}")
            if ads:
                res.append({"s": "search", "count": len(ads), "sample": ads[0][0]})

    RESULTS["AVITO"] = res

# ================================================================
if __name__ == "__main__":
    log("SPEED2 START")
    t0 = time.time()

    for fn in [test_farpost, test_wb, test_ozon, test_avito]:
        try:
            fn()
        except Exception as e:
            import traceback
            log(f"  FATAL {fn.__name__}: {e}\n{traceback.format_exc()}")
            RESULTS[fn.__name__.replace("test_", "").upper()] = [{"error": str(e)}]
        time.sleep(2)

    elapsed = time.time() - t0
    log(f"\nSPEED2 DONE in {elapsed:.1f}s")

    out = OUT / "speed2_result.json"
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
