r"""
PW5 — Playwright: реальные URL через search, долгие ожидания.
Оzon: search → product link → fetch
Farpost: category → wait PoW → listing
WB: networkidle + redirect wait
Avito: different entry + longer wait
"""
import json, re, time, traceback
from pathlib import Path

OUT = Path(__file__).parent
RESULTS = {}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def extract_dom(page):
    d = {"title": None, "prices": [], "og_desc": None}
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
        body = page.inner_text("body")[:8000]
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
            d["og_desc"] = (ogd.get_attribute("content") or "")[:200]
    except: pass
    return d

# ================================================================
# OZON: search → real product → verify
# ================================================================
def pw_ozon(browser):
    log("=== OZON ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # Step 1: Search page
    log("OZON: search page")
    try:
        page.goto("https://www.ozon.ru/search/?text=iphone+15&from_global=true", timeout=40000, wait_until="domcontentloaded")
        time.sleep(8)  # wait for SSR + JS
        html = page.content()
        (OUT / "ozon_pw5_search.html").write_text(html, encoding="utf-8")
        log(f"  Search: {len(html)}B")

        # Find product links in rendered DOM
        prod_links = []
        for a in page.query_selector_all('a[href*="/product/"]'):
            href = a.get_attribute("href") or ""
            m = re.search(r'/product/([a-z0-9\-]*(\d+)/?)"', href)
            if m:
                prod_links.append(href)
        # Also try data attributes
        if not prod_links:
            for el in page.query_selector_all('[data-widget*="product"], [class*="tile"]'):
                href = el.get_attribute("href") or el.get_attribute("data-link") or ""
                if "/product/" in href:
                    prod_links.append(href)
        log(f"  Product links: {len(prod_links)} → {prod_links[:3]}")

        if prod_links:
            # Step 2: Navigate to first product
            target = prod_links[0]
            if target.startswith("/"):
                target = "https://www.ozon.ru" + target
            log(f"  → {target}")
            page.goto(target, timeout=40000, wait_until="domcontentloaded")
            time.sleep(8)
            html2 = page.content()
            (OUT / "ozon_pw5_product.html").write_text(html2, encoding="utf-8")
            data = extract_dom(page)
            not_found = "Такой страницы не существует" in html2

            # Ozon-specific price extraction
            try:
                for sel in ['[class*="price-value"]', '[class*="Price"]', '[data-widget*="price"]', '[class*="c-price"]']:
                    els = page.query_selector_all(sel)
                    for el in els[:5]:
                        txt = el.inner_text().strip()
                        nums = re.findall(r'[\d\s]+', txt)
                        for n in nums:
                            v = int(re.sub(r'[^\d]', '', n))
                            if 50 <= v <= 200_000_000:
                                if v not in data["prices"]:
                                    data["prices"].append(v)
            except: pass

            log(f"  Product: {len(html2)}B, title={data['title']!r}")
            log(f"  Prices: {data['prices'][:5]}, not_found={not_found}")
            res.append({"s": "search→product", "url": target, **data, "not_found": not_found, "size": len(html2)})
        else:
            # Fallback: try known search result pattern
            log("  No links found, trying direct product with slug")
            for slug_url in [
                "https://www.ozon.ru/product/iphone-15-256-gb-chernyi/279845716/",
                "https://www.ozon.ru/product/smartfon-iphone-15/1126996368/",
            ]:
                page.goto(slug_url, timeout=35000, wait_until="domcontentloaded")
                time.sleep(7)
                html2 = page.content()
                data = extract_dom(page)
                not_found = "Такой страницы не существует" in html2
                log(f"  {slug_url}: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:3]}, nf={not_found}")
                if not not_found and data["title"]:
                    (OUT / "ozon_pw5_product.html").write_text(html2, encoding="utf-8")
                    res.append({"s": "slug", "url": slug_url, **data, "not_found": not_found, "size": len(html2)})
                    break
    except Exception as e:
        log(f"  ERR: {e}\n{traceback.format_exc()}")
        res.append({"s": "search", "error": str(e)})
    ctx.close()
    RESULTS["OZON"] = res

# ================================================================
# FARPOST: category → wait for PoW → listing
# ================================================================
def pw_farpost(browser):
    log("=== FARPOST ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # Try category page with LONG wait for PoW
    log("FP: category page (long wait)")
    try:
        page.goto("https://www.farpost.ru/vladivostok/transport/avto/", timeout=40000, wait_until="domcontentloaded")
        # Wait up to 20s for JS/PoW to render listings
        time.sleep(12)
        # Check if page changed (PoW solved)
        try:
            page.wait_for_selector('a[href*="-"][href$=".html"]', timeout=15000)
            log("  PoW solved, listings appeared!")
        except:
            log("  No listing links after 15s wait")
        time.sleep(3)

        html = page.content()
        (OUT / "fp_pw5_cat.html").write_text(html, encoding="utf-8")
        log(f"  Cat: {len(html)}B")

        # Find listing links
        links = []
        for a in page.query_selector_all('a[href]'):
            href = a.get_attribute("href") or ""
            if re.search(r'-(\d{5,})\.html', href):
                links.append(href)
        log(f"  Listing links: {len(links)} → {links[:5]}")

        if links:
            url = links[0]
            if url.startswith("/"):
                url = "https://www.farpost.ru" + url
            log(f"  → {url}")
            page.goto(url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(6)
            html2 = page.content()
            (OUT / "fp_pw5_listing.html").write_text(html2, encoding="utf-8")
            data = extract_dom(page)
            is_listing = any(k in html2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field', 'bulletin_id'])
            log(f"  Listing: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:5]}, listing={is_listing}")
            res.append({"s": "cat→listing", "url": url, **data, "is_listing": is_listing, "size": len(html2)})
        else:
            # Try direct known listing pattern
            log("  Trying direct listing URLs...")
            # Farpost pattern: /vladivostok/{cat}/{slug}-{id}.html
            # Try some plausible IDs
            for test_url in [
                "https://www.farpost.ru/vladivostok/transport/avto/prodam-avto/123456789.html",
                "https://www.farpost.ru/vladivostok/electro/telefony/iphone/987654321.html",
            ]:
                page.goto(test_url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(6)
                html2 = page.content()
                data = extract_dom(page)
                is_listing = any(k in html2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field'])
                log(f"  {test_url}: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:3]}, listing={is_listing}")
                if is_listing and data["title"]:
                    (OUT / "fp_pw5_listing.html").write_text(html2, encoding="utf-8")
                    res.append({"s": "direct", "url": test_url, **data, "is_listing": is_listing, "size": len(html2)})
                    break
    except Exception as e:
        log(f"  ERR: {e}\n{traceback.format_exc()}")
        res.append({"s": "cat", "error": str(e)})
    ctx.close()
    RESULTS["FARPOST"] = res

# ================================================================
# WB: networkidle + longer wait
# ================================================================
def pw_wb(browser):
    log("=== WILDBERRIES ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # First: warmup on main page with networkidle
    log("WB: warmup main")
    try:
        page.goto("https://www.wildberries.ru/", timeout=40000, wait_until="domcontentloaded")
        time.sleep(5)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass
        html_main = page.content()
        log(f"  Main: {len(html_main)}B, title={page.title()!r}")
    except Exception as e:
        log(f"  Main ERR: {e}")

    # Then: product page with networkidle
    for nid in ["172636515", "169774481"]:
        url = f"https://www.wildberries.ru/catalog/{nid}/detail.aspx"
        log(f"WB: {url}")
        try:
            page.goto(url, timeout=40000, wait_until="domcontentloaded")
            time.sleep(4)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except:
                pass
            time.sleep(3)
            html = page.content()
            (OUT / f"wb_pw5_{nid}.html").write_text(html, encoding="utf-8")
            data = extract_dom(page)

            # WB-specific: look for price elements
            try:
                for sel in ['[class*="price"]', '[class*="Price"]', '[data-testid*="price"]']:
                    els = page.query_selector_all(sel)
                    for el in els[:8]:
                        txt = el.inner_text().strip()
                        nums = re.findall(r'[\d\s]+', txt)
                        for n in nums:
                            v = int(re.sub(r'[^\d]', '', n))
                            if 50 <= v <= 200_000_000:
                                if v not in data["prices"]:
                                    data["prices"].append(v)
            except: pass

            is_real = len(html) > 15000 and data["title"] and "Wildberries" not in (data["title"] or "")[:15]
            log(f"  {nid}: {len(html)}B, title={data['title']!r}, prices={data['prices'][:5]}, real={is_real}")
            res.append({"s": "html", "id": nid, "url": url, **data, "is_real": is_real, "size": len(html)})
        except Exception as e:
            log(f"  ERR: {e}")
            res.append({"s": "html", "id": nid, "error": str(e)})
        time.sleep(3)
    ctx.close()
    RESULTS["WB"] = res

# ================================================================
# AVITO: different entry, longer wait
# ================================================================
def pw_avito(browser):
    log("=== AVITO ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # S1: Main page first (get cookies), then category
    log("AVITO: main → category → ad")
    try:
        page.goto("https://www.avito.ru/", timeout=40000, wait_until="domcontentloaded")
        time.sleep(6)
        html_main = page.content()
        log(f"  Main: {len(html_main)}B, title={page.title()!r}")
        is_ip_block = "Доступ ограничен" in html_main or "проблема с IP" in html_main
        log(f"  IP block: {is_ip_block}")

        if not is_ip_block:
            time.sleep(3)
            page.goto("https://www.avito.ru/vladivostok/telefony", timeout=40000, wait_until="domcontentloaded")
            time.sleep(8)
            html = page.content()
            (OUT / "avito_pw5_cat.html").write_text(html, encoding="utf-8")
            log(f"  Cat: {len(html)}B, title={page.title()!r}")

            # Find ad links
            ad_urls = []
            for a in page.query_selector_all('a[href]'):
                href = a.get_attribute("href") or ""
                if re.search(r'/(\d{9,})', href) and "vladivostok" in href:
                    ad_urls.append(href)
            log(f"  Ad links: {len(ad_urls)} → {ad_urls[:3]}")

            if ad_urls:
                target = ad_urls[0]
                if target.startswith("/"):
                    target = "https://www.avito.ru" + target
                page.goto(target, timeout=40000, wait_until="domcontentloaded")
                time.sleep(6)
                html2 = page.content()
                (OUT / "avito_pw5_ad.html").write_text(html2, encoding="utf-8")
                data = extract_dom(page)
                is_captcha = "captcha" in html2.lower() or "smartcaptcha" in html2.lower()
                log(f"  Ad: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:3]}, cap={is_captcha}")
                res.append({"s": "main→cat→ad", "url": target, **data, "captcha": is_captcha, "size": len(html2)})
        else:
            res.append({"s": "main", "ip_block": True, "size": len(html_main)})
    except Exception as e:
        log(f"  ERR: {e}\n{traceback.format_exc()}")
        res.append({"s": "main", "error": str(e)})

    # S2: Try without city (federal)
    log("AVITO: federal search")
    try:
        page.goto("https://www.avito.ru/search?query=iphone+15", timeout=35000, wait_until="domcontentloaded")
        time.sleep(7)
        html = page.content()
        (OUT / "avito_pw5_search.html").write_text(html, encoding="utf-8")
        log(f"  Search: {len(html)}B, title={page.title()!r}")
        ads = []
        for a in page.query_selector_all('a[href]'):
            href = a.get_attribute("href") or ""
            if re.search(r'/(\d{9,})', href):
                ads.append(href)
        log(f"  Search ads: {len(ads)} → {ads[:3]}")
        if ads:
            res.append({"s": "search", "count": len(ads), "sample": ads[0]})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "search", "error": str(e)})

    ctx.close()
    RESULTS["AVITO"] = res

# ================================================================
if __name__ == "__main__":
    log("PW5 START")
    t0 = time.time()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        # Order: Ozon (most promising) → Farpost → WB → Avito
        for fn in [pw_ozon, pw_farpost, pw_wb, pw_avito]:
            try:
                fn(browser)
            except Exception as e:
                log(f"  FATAL {fn.__name__}: {e}\n{traceback.format_exc()}")
                RESULTS[fn.__name__.replace("pw_", "").upper()] = [{"error": str(e)}]
            time.sleep(2)
        browser.close()

    elapsed = time.time() - t0
    log(f"\nPW5 DONE in {elapsed:.1f}s")

    out = OUT / "pw5_result.json"
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
