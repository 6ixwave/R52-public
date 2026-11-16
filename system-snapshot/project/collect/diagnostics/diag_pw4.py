r"""
PW4 — Playwright для 4 площадок. Последовательно, с таймаутами.
Цель: реальные title + price из DOM.
Запуск: C:\Python315\python.exe diag_pw4.py
"""
import json, re, time, sys, traceback
from pathlib import Path

OUT = Path(__file__).parent
RESULTS = {}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def extract_from_dom(page):
    """Извлечь title, price, meta из текущего DOM."""
    data = {"title": None, "prices": [], "meta_desc": None, "og_image": None}
    try:
        title = page.title()
        if title and len(title) > 3:
            data["title"] = title
    except:
        pass
    try:
        og = page.query_selector('meta[property="og:title"]')
        if og:
            data["title"] = og.get_attribute("content")
    except:
        pass
    try:
        # Farpost price
        el = page.query_selector('[data-bulletin-price]')
        if el:
            p = el.get_attribute('data-bulletin-price')
            if p and p.isdigit():
                data["prices"].append(int(p))
    except:
        pass
    try:
        # Generic price patterns in DOM text
        body_text = page.inner_text("body")[:5000]
        price_matches = re.findall(r'(\d[\d\s]{3,})\s*₽', body_text)
        for pm in price_matches[:5]:
            try:
                v = int(re.sub(r'[^\d]', '', pm))
                if 50 <= v <= 200_000_000:
                    data["prices"].append(v)
            except:
                pass
    except:
        pass
    try:
        ogd = page.query_selector('meta[property="og:description"]')
        if ogd:
            data["meta_desc"] = (ogd.get_attribute("content") or "")[:200]
    except:
        pass
    return data

# ================================================================
# FARPOST — Playwright: main → wait JS → listing
# ================================================================
def pw_farpost(browser):
    log("=== FARPOST (Playwright) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # S1: main page → wait for listings
    log("FP S1: main → JS render → listing")
    try:
        page.goto("https://www.farpost.ru/vladivostok/", timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)  # wait for JS
        # Try waiting for listing links
        try:
            page.wait_for_selector('a[href*="-"][href*=".html"]', timeout=10000)
        except:
            pass
        time.sleep(2)
        html = page.content()
        (OUT / "fp_pw_main.html").write_text(html, encoding="utf-8")
        log(f"  Main: {len(html)}B")

        # Find listing links in rendered DOM
        links = page.query_selector_all('a[href*=".html"]')
        listing_links = []
        for a in links:
            href = a.get_attribute("href") or ""
            if re.search(r'-(\d{5,})\.html', href):
                listing_links.append(href)
        log(f"  Listing links: {len(listing_links)} → {listing_links[:3]}")

        if listing_links:
            url = listing_links[0]
            if url.startswith("/"):
                url = "https://www.farpost.ru" + url
            log(f"  → {url}")
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(4)
            html2 = page.content()
            (OUT / "fp_pw_listing.html").write_text(html2, encoding="utf-8")
            data = extract_from_dom(page)
            is_listing = any(k in html2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field'])
            log(f"  Listing: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:5]}, listing={is_listing}")
            res.append({"s": "main→listing", "url": url, **data, "is_listing": is_listing})
        else:
            # S2: try category directly
            log("FP S2: category page")
            page.goto("https://www.farpost.ru/vladivostok/transport/avto/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(5)
            html2 = page.content()
            (OUT / "fp_pw_cat.html").write_text(html2, encoding="utf-8")
            links2 = page.query_selector_all('a[href*=".html"]')
            cat_links = [a.get_attribute("href") for a in links2 if re.search(r'-(\d{5,})\.html', a.get_attribute("href") or "")]
            log(f"  Cat links: {len(cat_links)} → {cat_links[:3]}")
            if cat_links:
                url = cat_links[0]
                if url.startswith("/"):
                    url = "https://www.farpost.ru" + url
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(4)
                html3 = page.content()
                (OUT / "fp_pw_listing2.html").write_text(html3, encoding="utf-8")
                data2 = extract_from_dom(page)
                is_listing = any(k in html3 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field'])
                log(f"  Listing: {len(html3)}B, title={data2['title']!r}, prices={data2['prices'][:5]}, listing={is_listing}")
                res.append({"s": "cat→listing", "url": url, **data2, "is_listing": is_listing})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "main", "error": str(e)})
    finally:
        ctx.close()
    RESULTS["FARPOST"] = res

# ================================================================
# WILDBERRIES — Playwright: detail page
# ================================================================
def pw_wb(browser):
    log("=== WILDBERRIES (Playwright) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()
    test_ids = ["172636515", "169774481"]

    for nid in test_ids:
        url = f"https://www.wildberries.ru/catalog/{nid}/detail.aspx"
        log(f"WB: {url}")
        try:
            page.goto(url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(5)
            html = page.content()
            (OUT / f"wb_pw_{nid}.html").write_text(html, encoding="utf-8")
            data = extract_from_dom(page)
            log(f"  {nid}: {len(html)}B, title={data['title']!r}, prices={data['prices'][:5]}")

            # WB-specific: look for price in specific elements
            try:
                price_els = page.query_selector_all('[class*="price"], [class*="Price"]')
                wb_prices = []
                for pe in price_els[:10]:
                    txt = pe.inner_text().strip()
                    nums = re.findall(r'[\d\s]+', txt)
                    for n in nums:
                        v = int(re.sub(r'[^\d]', '', n))
                        if 50 <= v <= 200_000_000:
                            wb_prices.append(v)
                if wb_prices:
                    data["prices"] = wb_prices
                    log(f"  WB price els: {wb_prices[:5]}")
            except:
                pass

            # Check if it's a real product page (not 498/warmup)
            is_real = len(html) > 10000 and data["title"] and "Wildberries" not in data["title"][:20]
            log(f"  is_real={is_real}")
            res.append({"s": "html", "id": nid, "url": url, **data, "is_real": is_real, "size": len(html)})
        except Exception as e:
            log(f"  ERR: {e}")
            res.append({"s": "html", "id": nid, "error": str(e)})
        time.sleep(3)
    ctx.close()
    RESULTS["WB"] = res

# ================================================================
# OZON — Playwright: product page
# ================================================================
def pw_ozon(browser):
    log("=== OZON (Playwright) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()
    urls = [
        "https://www.ozon.ru/product/279845716/",
        "https://www.ozon.ru/product/1126996368/",
    ]

    for url in urls:
        log(f"OZON: {url}")
        try:
            page.goto(url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(6)
            html = page.content()
            (OUT / "ozon_pw.html").write_text(html, encoding="utf-8")
            data = extract_from_dom(page)
            not_found = "Такой страницы не существует" in html or 'data-widget="error"' in html
            log(f"  {len(html)}B, title={data['title']!r}, prices={data['prices'][:5]}, nf={not_found}")

            # Ozon specific: data-widget price
            try:
                pw_els = page.query_selector_all('[data-widget*="price"], [class*="price-value"], [class*="Price"]')
                oz_prices = []
                for pe in pw_els[:10]:
                    txt = pe.inner_text().strip()
                    nums = re.findall(r'[\d\s]+', txt)
                    for n in nums:
                        v = int(re.sub(r'[^\d]', '', n))
                        if 50 <= v <= 200_000_000:
                            oz_prices.append(v)
                if oz_prices:
                    data["prices"] = oz_prices
            except:
                pass

            res.append({"s": "direct", "url": url, **data, "not_found": not_found, "size": len(html)})
        except Exception as e:
            log(f"  ERR: {e}")
            res.append({"s": "direct", "url": url, "error": str(e)})
        time.sleep(3)
    ctx.close()
    RESULTS["OZON"] = res

# ================================================================
# AVITO — Playwright: category → ad
# ================================================================
def pw_avito(browser):
    log("=== AVITO (Playwright) ===")
    res = []
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 900},
        locale="ru-RU",
    )
    page = ctx.new_page()

    # S1: category
    log("AVITO S1: category → ad")
    try:
        page.goto("https://www.avito.ru/vladivostok/telefony", timeout=35000, wait_until="domcontentloaded")
        time.sleep(6)
        html = page.content()
        (OUT / "avito_pw_cat.html").write_text(html, encoding="utf-8")
        log(f"  Cat: {len(html)}B")

        # Find ad links
        ad_links = page.query_selector_all('a[href*="/vladivostok/"]')
        ad_urls = []
        for a in ad_links:
            href = a.get_attribute("href") or ""
            if re.search(r'/(\d{9,})', href):
                ad_urls.append(href)
        log(f"  Ad links: {len(ad_urls)} → {ad_urls[:3]}")

        if ad_urls:
            target = ad_urls[0]
            if target.startswith("/"):
                target = "https://www.avito.ru" + target
            log(f"  → {target}")
            page.goto(target, timeout=35000, wait_until="domcontentloaded")
            time.sleep(5)
            html2 = page.content()
            (OUT / "avito_pw_ad.html").write_text(html2, encoding="utf-8")
            data = extract_from_dom(page)
            is_captcha = "captcha" in html2.lower() or "smartcaptcha" in html2.lower()
            log(f"  Ad: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:5]}, cap={is_captcha}")
            res.append({"s": "cat→ad", "url": target, **data, "captcha": is_captcha, "size": len(html2)})
        else:
            # S2: try direct ad URL
            log("AVITO S2: direct /ad/ URL")
            page.goto("https://www.avito.ru/vladivostok/telefony/iphone-15/1000000000", timeout=30000, wait_until="domcontentloaded")
            time.sleep(5)
            html2 = page.content()
            data2 = extract_from_dom(page)
            log(f"  Direct: {len(html2)}B, title={data2['title']!r}, prices={data2['prices'][:3]}")
            res.append({"s": "direct", **data2, "size": len(html2)})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "cat", "error": str(e)})

    # S3: mobile
    log("AVITO S3: m.avito.ru")
    try:
        page.goto("https://m.avito.ru/vladivostok/telefony", timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)
        html = page.content()
        (OUT / "avito_pw_m.html").write_text(html, encoding="utf-8")
        ads = page.query_selector_all('a[href*="/"]')
        m_ads = [a.get_attribute("href") for a in ads if re.search(r'/(\d{9,})', a.get_attribute("href") or "")]
        log(f"  Mobile ads: {len(m_ads)} → {m_ads[:3]}")
        if m_ads:
            data = extract_from_dom(page)
            res.append({"s": "mobile", "count": len(m_ads), **data})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "mobile", "error": str(e)})

    ctx.close()
    RESULTS["AVITO"] = res

# ================================================================
if __name__ == "__main__":
    log("PW4 START")
    t0 = time.time()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        for fn in [pw_farpost, pw_wb, pw_ozon, pw_avito]:
            try:
                fn(browser)
            except Exception as e:
                log(f"  FATAL {fn.__name__}: {e}\n{traceback.format_exc()}")
                RESULTS[fn.__name__.replace("pw_", "").upper()] = [{"error": str(e)}]
            time.sleep(2)
        browser.close()

    elapsed = time.time() - t0
    log(f"\nPW4 DONE in {elapsed:.1f}s")

    out = OUT / "pw4_result.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Saved: {out}")

    log("\n=== SUMMARY ===")
    for plat, res in RESULTS.items():
        has = any(r.get("title") and r.get("prices") for r in res if isinstance(r, dict))
        detail = ""
        if has:
            good = [r for r in res if r.get("title") and r.get("prices")]
            detail = f" → {good[0].get('title','?')[:50]} | {good[0].get('prices',[])[0]}"
        log(f"  {plat}: {len(res)} strategies, DATA={'YES' if has else 'NO'}{detail}")
