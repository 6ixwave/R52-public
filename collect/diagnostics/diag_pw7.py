r"""
PW7 — Финальный фикс: Ozon (merge prices + JS), Farpost (XHR), WB (nav wait), Avito (quick).
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
    # Body text prices (wide range)
    try:
        body = page.inner_text("body")[:20000]
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
    try:
        imgs = page.query_selector_all('img[src*="ir.ozone.ru"], img[src*="ozoncdn"], img[src*="avatars.mmedia"], img[src*="l16.avito"]')
        for img in imgs[:12]:
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            if src and len(src) > 20:
                d["images"].append(src)
    except: pass
    return d

# ================================================================
# OZON — fix: merge prices, JS evaluate for structured data
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

    urls = [
        "https://www.ozon.ru/product/apple-smartfon-iphone-15-sim-esim-novyy-global-6-128-gb-nano-sim-siniy-1681720398/",
        "https://www.ozon.ru/product/apple-smartfon-iphone-15-pro-max-256-gb-nano-sim-esim-seryy-4835500723/",
    ]

    for url in urls:
        log(f"OZON: {url.split('/')[-2][:50]}")
        try:
            page.goto(url, timeout=40000, wait_until="domcontentloaded")
            time.sleep(7)
            html = page.content()
            (OUT / "ozon_pw7.html").write_text(html, encoding="utf-8")
            log(f"  {len(html)}B")

            data = extract_dom(page)
            not_found = "Такой страницы не существует" in html

            # JS evaluate: try to get price from page's JS state
            try:
                js_price = page.evaluate("""() => {
                    // Try multiple JS state locations
                    const states = [
                        window.__INITIAL_STATE__,
                        window.__PRELOADED_STATE__,
                        window.__NEXT_DATA__
                    ];
                    for (const st of states) {
                        if (!st) continue;
                        const s = JSON.stringify(st);
                        // Find price patterns in serialized state
                        const matches = s.match(/"price"\\s*:\\s*\\{[^}]*"basic"\\s*:\\s*(\\d+)/g);
                        if (matches) return matches.map(m => parseInt(m.match(/(\\d+)$/)[1]));
                    }
                    // Fallback: DOM price elements
                    const els = document.querySelectorAll('[class*="price"], [class*="Price"]');
                    const prices = [];
                    for (const el of els) {
                        const txt = el.innerText || '';
                        const m = txt.match(/(\\d[\\d\\s]{3,})\\s*₽/);
                        if (m) {
                            const v = parseInt(m[1].replace(/[^\\d]/g, ''));
                            if (v > 50 && v < 200000000) prices.push(v);
                        }
                    }
                    return prices;
                }""")
                if js_price:
                    log(f"  JS prices: {js_price[:8]}")
                    for p in js_price:
                        if p not in data["prices"]:
                            data["prices"].append(p)
            except Exception as e:
                log(f"  JS eval: {e}")

            # Also: aria-label prices
            try:
                for el in page.query_selector_all('[aria-label]'):
                    al = el.get_attribute("aria-label") or ""
                    if "₽" in al or "руб" in al.lower():
                        nums = re.findall(r'(\d[\d\s]{2,})', al)
                        for n in nums:
                            v = int(re.sub(r'[^\d]', '', n))
                            if 50 <= v <= 200_000_000 and v not in data["prices"]:
                                data["prices"].append(v)
            except: pass

            log(f"  title={data['title']!r}")
            log(f"  prices={data['prices'][:8]}")
            log(f"  images={len(data['images'])}, nf={not_found}")
            res.append({"s": "product", "url": url, **data, "not_found": not_found, "size": len(html)})
        except Exception as e:
            log(f"  ERR: {e}")
            res.append({"s": "product", "url": url, "error": str(e)})
        time.sleep(3)
    ctx.close()
    RESULTS["OZON"] = res

# ================================================================
# FARPOST — XHR intercept + longer wait
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

    # Intercept XHR to catch listing data
    xhr_data = []
    def on_response(resp):
        try:
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "javascript" in ct:
                url = resp.url
                if any(k in url for k in ["bulletin", "listing", "search", "ajax", "api", "load"]):
                    body = resp.text()
                    if len(body) > 100:
                        xhr_data.append({"url": url, "size": len(body), "body": body[:2000]})
                        log(f"  XHR: {url[:80]} ({len(body)}B)")
        except: pass

    page.on("response", on_response)

    log("FP: category with XHR intercept")
    try:
        page.goto("https://www.farpost.ru/vladivostok/transport/avto/", timeout=45000, wait_until="domcontentloaded")
        # Wait for PoW + AJAX
        time.sleep(18)
        log(f"  XHR captured: {len(xhr_data)}")

        # Check DOM for listings
        links = []
        for a in page.query_selector_all('a[href]'):
            href = a.get_attribute("href") or ""
            if re.search(r'-(\d{5,})\.html', href):
                links.append(href)
        log(f"  DOM links: {len(links)} → {links[:3]}")

        html = page.content()
        (OUT / "fp_pw7_cat.html").write_text(html, encoding="utf-8")
        log(f"  Cat: {len(html)}B")

        # If XHR has data, parse it
        listing_url = None
        if xhr_data:
            for xd in xhr_data:
                # Try to find bulletin URLs in JSON
                found = re.findall(r'(/vladivostok/[^"]+?-\d{5,}\.html)', xd["body"])
                if found:
                    listing_url = "https://www.farpost.ru" + found[0]
                    log(f"  XHR listing: {listing_url}")
                    break
                # Or find data with id + title
                ids = re.findall(r'"id"\s*:\s*(\d{5,})', xd["body"])
                if ids:
                    log(f"  XHR IDs: {ids[:5]}")

        if not listing_url and links:
            listing_url = links[0] if links[0].startswith("http") else "https://www.farpost.ru" + links[0]

        if listing_url:
            log(f"  → {listing_url}")
            page.goto(listing_url, timeout=35000, wait_until="domcontentloaded")
            time.sleep(7)
            html2 = page.content()
            (OUT / "fp_pw7_listing.html").write_text(html2, encoding="utf-8")
            data = extract_dom(page)
            is_listing = any(k in html2 for k in ['bulletinText', 'data-bulletin-price', 'viewbull-field', 'bulletin_id', 'og:title'])
            log(f"  Listing: {len(html2)}B, title={data['title']!r}, prices={data['prices'][:5]}, listing={is_listing}")
            res.append({"s": "cat→listing", "url": listing_url, **data, "is_listing": is_listing, "size": len(html2)})
        else:
            log("  No listing found, trying search page")
            page.goto("https://www.farpost.ru/vladivostok/search/?q=iphone", timeout=35000, wait_until="domcontentloaded")
            time.sleep(12)
            html3 = page.content()
            (OUT / "fp_pw7_search.html").write_text(html3, encoding="utf-8")
            links3 = []
            for a in page.query_selector_all('a[href]'):
                href = a.get_attribute("href") or ""
                if re.search(r'-(\d{5,})\.html', href):
                    links3.append(href)
            log(f"  Search links: {len(links3)} → {links3[:3]}")
            if links3:
                res.append({"s": "search", "count": len(links3), "sample": links3[0]})
    except Exception as e:
        log(f"  ERR: {e}\n{traceback.format_exc()}")
        res.append({"s": "cat", "error": str(e)})
    ctx.close()
    RESULTS["FARPOST"] = res

# ================================================================
# WB — wait_for_url + headless=new
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

    for nid in ["172636515", "169774481"]:
        url = f"https://www.wildberries.ru/catalog/{nid}/detail.aspx"
        log(f"WB: {nid}")
        try:
            page.goto(url, timeout=50000, wait_until="domcontentloaded")
            # Wait for URL to stabilize (WB does JS redirect)
            time.sleep(5)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except: pass
            time.sleep(3)

            cur_url = page.url
            html = page.content()
            (OUT / f"wb_pw7_{nid}.html").write_text(html, encoding="utf-8")
            data = extract_dom(page)

            # WB: look for specific price patterns
            wb_prices = []
            try:
                # Method: page.evaluate for WB price
                js_p = page.evaluate("""() => {
                    const els = document.querySelectorAll('[class*="price"], [class*="Price"], [data-testid]');
                    const prices = [];
                    for (const el of els) {
                        const txt = (el.innerText || '').trim();
                        const m = txt.match(/(\\d[\\d\\s]{3,})\\s*₽?/);
                        if (m) {
                            const v = parseInt(m[1].replace(/[^\\d]/g, ''));
                            if (v > 50 && v < 200000000) prices.push(v);
                        }
                    }
                    return prices;
                }""")
                if js_p:
                    wb_prices = js_p
            except: pass

            # Also check body text
            try:
                body = page.inner_text("body")[:15000]
                for m in re.finditer(r'(\d[\d\s]{3,})\s*₽', body):
                    v = int(re.sub(r'[^\d]', '', m.group(1)))
                    if 50 <= v <= 200_000_000 and v not in wb_prices:
                        wb_prices.append(v)
            except: pass

            data["prices"] = wb_prices[:8]
            is_real = len(html) > 20000 and data["title"] and "Wildberries" not in (data["title"] or "")[:15]
            log(f"  {nid}: {len(html)}B, url={cur_url[:60]}")
            log(f"  title={data['title']!r}, prices={wb_prices[:5]}, real={is_real}")
            res.append({"s": "html", "id": nid, "url": url, **data, "is_real": is_real, "size": len(html)})
        except Exception as e:
            log(f"  ERR: {e}")
            res.append({"s": "html", "id": nid, "error": str(e)})
        time.sleep(3)
    ctx.close()
    RESULTS["WB"] = res

# ================================================================
# AVITO — quick check (IP block likely)
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

    # Quick: main page, 8s wait
    try:
        page.goto("https://www.avito.ru/vladivostok/telefony", timeout=35000, wait_until="domcontentloaded")
        time.sleep(8)
        html = page.content()
        title = page.title()
        is_block = "Доступ ограничен" in html or "проблема с IP" in html or "captcha" in html.lower()
        log(f"  {len(html)}B, title={title!r}, block={is_block}")

        if not is_block:
            data = extract_dom(page)
            ads = []
            for a in page.query_selector_all('a[href]'):
                href = a.get_attribute("href") or ""
                if re.search(r'/(\d{9,})', href):
                    ads.append(href)
            log(f"  Ads: {len(ads)}")
            if ads:
                target = ads[0]
                if target.startswith("/"):
                    target = "https://www.avito.ru" + target
                page.goto(target, timeout=30000, wait_until="domcontentloaded")
                time.sleep(6)
                html2 = page.content()
                data2 = extract_dom(page)
                log(f"  Ad: {len(html2)}B, title={data2['title']!r}, prices={data2['prices'][:3]}")
                res.append({"s": "cat→ad", "url": target, **data2, "size": len(html2)})
            else:
                res.append({"s": "cat", **data, "size": len(html)})
        else:
            res.append({"s": "cat", "ip_block": True, "title": title, "size": len(html)})
    except Exception as e:
        log(f"  ERR: {e}")
        res.append({"s": "cat", "error": str(e)})
    ctx.close()
    RESULTS["AVITO"] = res

# ================================================================
if __name__ == "__main__":
    log("PW7 START")
    t0 = time.time()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        for fn in [pw_ozon, pw_farpost, pw_wb, pw_avito]:
            try:
                fn(browser)
            except Exception as e:
                log(f"  FATAL {fn.__name__}: {e}\n{traceback.format_exc()}")
                RESULTS[fn.__name__.replace("pw_", "").upper()] = [{"error": str(e)}]
            time.sleep(2)
        browser.close()

    elapsed = time.time() - t0
    log(f"\nPW7 DONE in {elapsed:.1f}s")

    out = OUT / "pw7_result.json"
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
