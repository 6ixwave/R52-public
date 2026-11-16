"""AliExpress Playwright v3: сжатый, 90с, Baxia captcha handling."""
import sys, json, re, time, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright

LID = "1005011813323763"
PRODUCT_URL = f"https://www.aliexpress.com/item/{LID}.html"
HOME_URL = "https://www.aliexpress.com/"

mtop_data = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="it-IT",
        viewport={"width": 1920, "height": 1080},
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    """)
    page = ctx.new_page()

    def on_resp(resp):
        try:
            u = resp.url
            if "mtop.aliexpress.pdp" in u or "mtop.aliexpress.product" in u:
                body = resp.text()
                mtop_data.append({"url": u, "status": resp.status, "body": body})
                print(f"  [MTOP] {resp.status} len={len(body)}")
                print(f"    {body[:250]}")
        except: pass
    page.on("response", on_resp)

    # ── Warm-up: 3s ──
    print("WARM-UP...")
    t0 = time.monotonic()
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(3000)
    print(f"  done {time.monotonic()-t0:.1f}s")

    # ── Product page ──
    print("PRODUCT...")
    t1 = time.monotonic()
    page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=15000)
    # Ждём MTOP или timeout 12s
    try:
        page.wait_for_timeout(12000)
    except: pass
    print(f"  done {time.monotonic()-t1:.1f}s, MTOP responses: {len(mtop_data)}")

    # ── Parse MTOP ──
    product_data = None
    for mr in mtop_data:
        clean = re.sub(r'^mtopjsonp\d*\(', '', mr["body"].strip())
        clean = re.sub(r'\)\s*$', '', clean)
        try:
            d = json.loads(clean)
            ret = d.get("ret", [])
            print(f"  MTOP ret: {ret}")
            if any("SUCCESS" in r for r in ret):
                product_data = d.get("data", {})
                break
        except: pass

    # ── Если Baxia: ищем captcha в DOM ──
    if not product_data:
        print("CHECKING CAPTCHA...")
        # Baxia popup / dialog
        captcha_sel = [
            ".baxia-dialog", ".baxia-captcha", "[class*='baxia']",
            ".nc-container", "#nc_1_wrapper", ".J_MIDDLEware_LAYER",
            "[class*='captcha']", "[class*='Captcha']",
            ".punish-dialog", "[class*='punish']",
            ".dialog", ".modal", "[role='dialog']",
        ]
        found_cap = None
        for sel in captcha_sel:
            els = page.query_selector_all(sel)
            if els:
                found_cap = (sel, els[0])
                print(f"  Found captcha element: {sel}")
                break

        if found_cap:
            sel, el = found_cap
            # Попробовать: клик по checkbox / кнопке
            try:
                # Ищем внутри captcha кнопку или checkbox
                btn = el.query_selector("input[type='checkbox'], button, .btn, [class*='verify'], [class*='confirm']")
                if btn:
                    btn.click()
                    print("  Clicked captcha button/checkbox")
                    page.wait_for_timeout(3000)
                else:
                    el.click()
                    print("  Clicked captcha element")
                    page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  Click failed: {e}")

            # После captcha — ждём повторный MTOP
            before = len(mtop_data)
            page.wait_for_timeout(8000)
            new_mt = mtop_data[before:]
            print(f"  New MTOP after captcha: {len(new_mt)}")
            for mr in new_mt:
                clean = re.sub(r'^mtopjsonp\d*\(', '', mr["body"].strip())
                clean = re.sub(r'\)\s*$', '', clean)
                try:
                    d = json.loads(clean)
                    if any("SUCCESS" in r for r in d.get("ret", [])):
                        product_data = d.get("data", {})
                        print("  *** SUCCESS after captcha ***")
                        break
                except: pass

    # ── Если всё ещё нет: DOM extraction ──
    if not product_data:
        print("DOM EXTRACTION...")
        # h1 / title
        h1 = page.query_selector("h1, [class*='title']")
        title = h1.inner_text().strip()[:200] if h1 else None

        # Price
        price_el = page.query_selector("[class*='price-current'], [class*='Price'], [data-price]")
        price_txt = price_el.inner_text().strip() if price_el else None

        # Images
        imgs = page.query_selector_all("img[src*='alicdn']")
        product_imgs = []
        for img in imgs:
            src = img.get_attribute("src") or ""
            if any(x in src for x in ("assets", "s.alicdn", "img.alicdn", "tfs/")):
                continue
            product_imgs.append(src)

        print(f"  Title: {title[:100] if title else 'N/A'}")
        print(f"  Price: {price_txt[:80] if price_txt else 'N/A'}")
        print(f"  Images: {len(product_imgs)}")
        for u in product_imgs[:5]:
            print(f"    {u[:120]}")

        # Save DOM snapshot
        html = page.content()
        with open("diag_ali_pw3_dom.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  DOM saved: {len(html)} bytes")

    # ── Если product_data: извлекаем ──
    if product_data:
        s = json.dumps(product_data, ensure_ascii=False)
        print(f"\nPRODUCT DATA: {len(s)} bytes")
        print(f"  Keys: {list(product_data.keys())[:20]}")

        # Title
        title = None
        for k in ("title","subject","name","productTitle","seoTitle"):
            if k in product_data and isinstance(product_data[k], str):
                title = product_data[k]; break
        if not title:
            m = re.search(r'"(?:title|subject)"\s*:\s*"([^"]{10,300})"', s)
            title = m.group(1) if m else None
        print(f"  Title: {title[:120] if title else 'N/A'}")

        # Price
        prices = re.findall(r'"(?:minPrice|salePrice|price|amount|formattedPrice|displayPrice|priceValue)"\s*:\s*"?([\d.]+)"?', s)
        currs = re.findall(r'"(?:currency|priceCurrency)"\s*:\s*"([^"]+)"', s)
        print(f"  Prices: {prices[:8]}")
        print(f"  Currency: {currs[:5]}")

        # Images
        imgs = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(//[^"]*alicdn[^"]*)"', s)
        imgs2 = re.findall(r'"(?:url|imageUrl|path)"\s*:\s*"(https?://[^"]*alicdn[^"]*)"', s)
        print(f"  Images: {len(imgs)+len(imgs2)}")
        for u in (imgs+imgs2)[:5]:
            print(f"    {u[:120]}")

        # Availability
        avail = re.findall(r'"(?:stock|inStock|stockStatus|available|buyNow|stockCount)"\s*:\s*("?[^,}\]]+)"?', s)
        print(f"  Avail: {avail[:5]}")

        # Save
        with open("diag_ali_pw3_data.json", "w", encoding="utf-8") as f:
            json.dump(product_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved: diag_ali_pw3_data.json")

    browser.close()

print(f"\nTotal: {time.monotonic()-t0:.1f}s")
