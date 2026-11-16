r"""
PW6 — Финальный: Ozon (verify+extract), Farpost (30s PoW), WB (content wait), Avito (last try).
"""
import json, re, time, traceback
from pathlib import Path

OUT = Path(__file__).parent
RESULTS = {}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def extract_dom(page):
    d = {"title": None, "prices": [], "og_desc": None, "images": []}
    try:
        t = page.title()
        if t and len(t) > 3:
            d["title"] = t
    except: pass
    try:
        og = page.query_selector('meta[property="og:title"]')
        if og:
            c = og.get_attribute("content")
            if c and len(c) > 3:
                d["title"] = c
    except: pass
    try:
        body = page.inner_text("body")[:10000]
        for m in re.finditer(r'(\d[\d\s]{3,})\s*₽', body):
            v = int(re.sub(r'[^\d]', '', m.group(1)))
            if 50 <= v <= 200_000_000:
                d["prices"].append(v)
    except: pass
    try:
        el = page.query_selector('[data-bulletin-price]')
        if el:
            p = el.get_attribute('data-bulletin-price')
            if p and p.isdigit():
                d["prices"].append(int(p))
    except: pass
    try:
        ogd = page.query_selector('meta[property="og:description"]')
        if ogd:
            d["og_desc"] = (ogd.get_attribute("content") or "")[:300]
    except: pass
    return d

# ================================================================
# OZON — verify product, extract structured price + images
# ================================================================
def pw_ozon(browser):
    log("=== OZON (verify) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # Use the URL that worked in PW5
    url = "https://www.ozon.ru/product/apple-smartfon-iphone-15-sim-esim-novyy-global-6-128-gb-nano-sim-siniy-1681720398/"
    log(f"OZON: {url}")
    try:
        page.goto(url, timeout=40000, wait_until="domcontentloaded")
        time.sleep(8)
        html = page.content()
        (OUT / "ozon_pw6_product.html").write_text(html, encoding="utf-8")
        log(f"  Product: {len(html)}B")

        data = extract_dom(page)
        not_found = "Такой страницы не существует" in html

        # Ozon price: look for specific price elements
        oz_prices = []
        try:
            # Method 1: data-widget price
            for sel in ['[data-widget*="price"]', '[class*="price-value"]', '[class*="Price"]', '[class*="c-price"]', '[class*="price_"]']:
                els = page.query_selector_all(sel)
                for el in els[:8]:
                    txt = el.inner_text().strip()
                    nums = re.findall(r'[\d\s]+', txt)
                    for n in nums:
                        v = int(re.sub(r'[^\d]', '', n))
                        if 50 <= v <= 200_000_000:
                            if v not in oz_prices:
                                oz_prices.append(v)
        except: pass

        # Method 2: aria-label with price
        try:
            for el in page.query_selector_all('[aria-label*="₽"], [aria-label*="руб"]'):
                txt = el.get_attribute("aria-label") or ""
                nums = re.findall(r'(\d[\d\s]{2,})', txt)
                for n in nums:
                    v = int(re.sub(r'[^\d]', '', n))
                    if 50 <= v <= 200_000_000:
                        if v not in oz_prices:
                            oz_prices.append(v)
        except: pass

        # Method 3: JSON state in HTML
        try:
            state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
            if state_m:
                state = json.loads(state_m.group(1))
                # Navigate to product price
                def find_prices(obj, depth=0):
                    found = []
                    if depth > 8:
                        return found
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("price", "basic", "current") and isinstance(v, (int, float)) and 50 <= v <= 200_000_000:
                                found.append(int(v))
                            elif isinstance(v, (dict, list)):
                                found.extend(find_prices(v, depth + 1))
                    elif isinstance(obj, list):
                        for item in obj[:20]:
                            if isinstance(item, (dict, list)):
                                found.extend(find_prices(item, depth + 1))
                    return found
                state_prices = find_prices(state)
                log(f"  State prices: {state_prices[:10]}")
                for p in state_prices:
                    if p not in oz_prices:
                        oz_prices.append(p)
        except Exception as e:
            log(f"  State parse: {e}")

        # Method 4: images
        try:
            imgs = page.query_selector_all('img[src*="ir.ozone.ru"], img[src*="ozoncdn"]')
            for img in imgs[:10]:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src and len(src) > 20:
                    data["images"].append(src)
        except: pass

        data["prices"] = oz_prices[:10]
        log(f"  title={data['title']!r}")
        log(f"  prices={oz_prices[:8]}")
        log(f"  images={len(data['images'])}")
        log(f"  not_found={not_found}")
        res.append({"s": "product", "url": url, **data, "not_found": not_found, "size": len(html)})

        # Also try a second product for reliability
        log("OZON: 2nd product")
        url2 = "https://www.ozon.ru/product/apple-smartfon-iphone-15-pro-max-256-gb-nano-sim-esim-seryy-4835500723/"
        page.goto(url2, timeout=40000, wait_until="domcontentloaded")
        time.sleep(8)
        html2 = page.content()
        (OUT / "ozon_pw6_product2.html").write_text(html2, encoding="utf-8")
        data2 = extract_dom(page)
        not_found2 = "Такой страницы не существует" in html2
        oz_prices2 = []
        try:
            for sel in ['[class*="price-value"]', '[class*="Price"]', '[data-widget*="price"]']:
                els = page.query_selector_all(sel)
                for el in els[:6]:
                    txt = el.inner_text().strip()
                    nums = re.findall(r'[\d\s]+', txt)
                    for n in nums:
                        v = int(re.sub(r'[^\d]', '', n))
                        if 50 <= v <= 200_000_000:
                            if v not in oz_prices2:
                                oz_prices2.append(v)
        except: pass
        data2["prices"] = oz_prices2[:8]
        log(f"  2nd: {len(html2)}B, title={data2['title']!r}, prices={oz_prices2[:5]}, nf={not_found2}")
        res.append({"s": "product2", "url": url2, **data2, "not_found": not_found2, "size": len(html2)})

    except Exception as e:
        log(f"  ERR: {e}\n{traceback.format_exc()}")
        res.append({"s": "product", "error": str(e)})
    ctx.close()
    RESULTS["OZON"] = res

# ================================================================
# FARPOST — 30s wait for PoW, then check AJAX
# ================================================================
def pw_farpost(browser):
    log("=== FARPOST (30s PoW) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # S1: Category with 30s wait
    log("FP: category 30s wait")
    try:
        page.goto("https://www.farpost.ru/vladivostok/transport/avto/", timeout=45000, wait_until="domcontentloaded")
        # Long wait for PoW + AJAX
        time.sleep(15)
        # Check for listing links
        links = []
        for a in page.query_selector_all('a[href]'):
            href = a.get_attribute("href") or ""
            if re.search(r'-(\d{5,})\.html', href):
                links.append(href)
        log(f"  After 15s: {len(links)} links")

        if not links:
            # Wait more
            time.sleep(15)
            for a in page.query_selector_all('a[href]'):
                href = a.get_attribute("href") or ""
                if re.search(r'-(\d{5,})\.html', href):
                    links.append(href)
            log(f"  After 30s: {len(links)} links")

        if not links:
            # Check if there's a "load more" or AJAX indicator
            html = page.content()
            (OUT / "fp_pw6_cat.html").write_text(html, encoding="utf-8")
            log(f"  Cat: {len(html)}B")
            # Look for embedded JSON with listings
            json_blocks = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
            for jb in json_blocks:
                if 'bulletin' in jb.lower() or 'listing' in jb.lower() or 'price' in jb.lower():
                    log(f"  JS block with content: {jb[:200]}")
                    break
            # Check for data attributes
            data_items = page.query_selector_all('[data-bulletin-id], [data-id], [data-item-id]')
            log(f"  data-id elements: {len(data_items)}")
        else:
            url = links[0]
            if url.startswith("/"):
                url = "https://www.farpost.ru" + url
            log(f"  → {url}")
            page.goto(url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(8)
            html2 = page.content()
            (OUT / "fp_pw6_listing.html").write_text(html2, encoding="utf-8")
            data = extract_dom(page)
            is_listing = any(k in html2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field'])
            log(f"  Listing: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:5]}, listing={is_listing}")
            res.append({"s": "cat→listing", "url": url, **data, "is_listing": is_listing})

    except Exception as e:
        log(f"  ERR: {e}\n{traceback.format_exc()}")
        res.append({"s": "cat", "error": str(e)})

    # S2: Try direct known URL pattern (from Farpost structure)
    log("FP: direct listing attempt")
    try:
        # Farpost URLs: /vladivostok/{cat}/{subcat}/{slug}-{id}.html
        # Try a few plausible ones
        for test_url in [
            "https://www.farpost.ru/vladivostok/transport/avto/kupi-prodaj-avto/555123456.html",
            "https://www.farpost.ru/vladivostok/electro/telefony/iphone-15/777654321.html",
            "https://www.farpost.ru/vladivostok/dom/kvartiry/snyat-prodat/444999888.html",
        ]:
            page.goto(test_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(8)
            html = page.content()
            data = extract_dom(page)
            is_listing = any(k in html for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field', 'bulletin_id'])
            log(f"  {test_url.split('/')[-1]}: {len(html)}B, title={data['title']!r}, prices={data['prices'][:3]}, listing={is_listing}")
            if is_listing and data["title"] and "Фарпост - доска" not in data["title"]:
                (OUT / "fp_pw6_listing.html").write_text(html, encoding="utf-8")
                res.append({"s": "direct", "url": test_url, **data, "is_listing": is_listing})
                break
            time.sleep(2)
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "direct", "error": str(e)})

    ctx.close()
    RESULTS["FARPOST"] = res

# ================================================================
# WB — wait for actual content, not just DOM
# ================================================================
def pw_wb(browser):
    log("=== WILDBERRIES (content wait) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # Warmup: main page, wait for full load
    log("WB: warmup")
    try:
        page.goto("https://www.wildberries.ru/", timeout=45000, wait_until="domcontentloaded")
        time.sleep(5)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except: pass
        time.sleep(3)
        # Check if redirected or still loading
        cur_url = page.url
        log(f"  Main: {cur_url}, {len(page.content())}B")
    except Exception as e:
        log(f"  Main ERR: {e}")

    # Product page: wait for specific elements
    for nid in ["172636515", "169774481"]:
        url = f"https://www.wildberries.ru/catalog/{nid}/detail.aspx"
        log(f"WB: {url}")
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            # Wait for actual product content
            time.sleep(5)
            # Try waiting for price element specifically
            try:
                page.wait_for_selector('[class*="price"], [class*="Price"], [data-testid*="price"]', timeout=20000)
                log(f"  Price element appeared!")
            except:
                log(f"  No price element after 20s")
            time.sleep(3)

            html = page.content()
            (OUT / f"wb_pw6_{nid}.html").write_text(html, encoding="utf-8")
            data = extract_dom(page)

            # WB price extraction
            wb_prices = []
            try:
                for sel in ['[class*="price"]', '[class*="Price"]', '[data-testid*="price"]', '[class*="card-price"]']:
                    els = page.query_selector_all(sel)
                    for el in els[:10]:
                        txt = el.inner_text().strip()
                        nums = re.findall(r'[\d\s]+', txt)
                        for n in nums:
                            v = int(re.sub(r'[^\d]', '', n))
                            if 50 <= v <= 200_000_000:
                                if v not in wb_prices:
                                    wb_prices.append(v)
            except: pass
            data["prices"] = wb_prices[:8]

            # Check for real content markers
            is_real = (len(html) > 20000 and data["title"] and
                      "Wildberries" not in (data["title"] or "")[:15] and
                      "Загрузка" not in (data["title"] or ""))
            log(f"  {nid}: {len(html)}B, title={data['title']!r}, prices={wb_prices[:5]}, real={is_real}")
            res.append({"s": "html", "id": nid, "url": url, **data, "is_real": is_real, "size": len(html)})
        except Exception as e:
            log(f"  ERR: {e}")
            res.append({"s": "html", "id": nid, "error": str(e)})
        time.sleep(3)
    ctx.close()
    RESULTS["WB"] = res

# ================================================================
# AVITO — last try: different approach
# ================================================================
def pw_avito(browser):
    log("=== AVITO (last try) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # S1: Try m.avito.ru (mobile might have less IP blocking)
    log("AVITO: m.avito.ru")
    try:
        page.goto("https://m.avito.ru/vladivostok/telefony", timeout=40000, wait_until="domcontentloaded")
        time.sleep(8)
        html = page.content()
        (OUT / "avito_pw6_m.html").write_text(html, encoding="utf-8")
        title = page.title()
        is_block = "Доступ ограничен" in html or "проблема с IP" in html or "captcha" in html.lower()
        log(f"  Mobile: {len(html)}B, title={title!r}, block={is_block}")

        if not is_block:
            ads = []
            for a in page.query_selector_all('a[href]'):
                href = a.get_attribute("href") or ""
                if re.search(r'/(\d{9,})', href):
                    ads.append(href)
            log(f"  Ads: {len(ads)} → {ads[:3]}")
            if ads:
                target = ads[0]
                if target.startswith("/"):
                    target = "https://m.avito.ru" + target
                page.goto(target, timeout=35000, wait_until="domcontentloaded")
                time.sleep(6)
                html2 = page.content()
                (OUT / "avito_pw6_ad.html").write_text(html2, encoding="utf-8")
                data = extract_dom(page)
                log(f"  Ad: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:3]}")
                res.append({"s": "m→ad", "url": target, **data, "size": len(html2)})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "m", "error": str(e)})

    # S2: Try www with longer initial wait
    log("AVITO: www with 10s initial")
    try:
        page.goto("https://www.avito.ru/vladivostok/telefony", timeout=45000, wait_until="domcontentloaded")
        time.sleep(10)
        html = page.content()
        title = page.title()
        is_block = "Доступ ограничен" in html or "проблема с IP" in html
        log(f"  WWW: {len(html)}B, title={title!r}, block={is_block}")
        if not is_block:
            ads = []
            for a in page.query_selector_all('a[href]'):
                href = a.get_attribute("href") or ""
                if re.search(r'/(\d{9,})', href) and "vladivostok" in href:
                    ads.append(href)
            log(f"  Ads: {len(ads)}")
            if ads:
                res.append({"s": "www", "count": len(ads), "sample": ads[0]})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "www", "error": str(e)})

    ctx.close()
    RESULTS["AVITO"] = res

# ================================================================
if __name__ == "__main__":
    log("PW6 START")
    t0 = time.time()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        # Ozon first (closest to working), then others
        for fn in [pw_ozon, pw_farpost, pw_wb, pw_avito]:
            try:
                fn(browser)
            except Exception as e:
                log(f"  FATAL {fn.__name__}: {e}\n{traceback.format_exc()}")
                RESULTS[fn.__name__.replace("pw_", "").upper()] = [{"error": str(e)}]
            time.sleep(2)
        browser.close()

    elapsed = time.time() - t0
    log(f"\nPW6 DONE in {elapsed:.1f}s")

    out = OUT / "pw6_result.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")

    log("\n=== SUMMARY ===")
    for plat, res in RESULTS.items():
        has = any(r.get("title") and r.get("prices") and not r.get("not_found") for r in res if isinstance(r, dict))
        detail = ""
        if has:
            good = [r for r in res if r.get("title") and r.get("prices") and not r.get("not_found")]
            detail = f" → {good[0].get('title','?')[:50]} | {good[0].get('prices',[])[0]}"
        log(f"  {plat}: {len(res)} strategies, DATA={'YES' if has else 'NO'}{detail}")
