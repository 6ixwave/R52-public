"""WB: Playwright — загрузить HTML, вытащить цену из embedded state / DOM."""
import sys, json, time, re
sys.path.insert(0, '.')

from playwright.sync_api import sync_playwright

LID = "199298871"
URL = f"https://www.wildberries.ru/catalog/{LID}/detail.aspx"

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )
    # Сбросить navigator.webdriver
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = ctx.new_page()

    print(f"Loading: {URL}")
    t0 = time.monotonic()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"goto error: {e}")
        browser.close()
        sys.exit(1)
    dt = time.monotonic() - t0
    print(f"Loaded in {dt:.1f}s")

    # Дождаться основного контента
    try:
        page.wait_for_selector("h1, [class*='title'], [class*='name']", timeout=10000)
        print("Content selector found")
    except Exception:
        print("Content selector timeout (continuing)")

    # Дополнительная пауза для JS-рендера
    page.wait_for_timeout(3000)

    html = page.content()
    print(f"HTML length: {len(html)}")

    # --- Extract title ---
    title = None
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
        if m:
            title = m.group(1).strip()
    print(f"Title: {title}")

    # --- Extract price from embedded JSON ---
    prices_found = []

    # Pattern 1: "price": {"basic": N, ...}
    for m in re.finditer(r'"price"\s*:\s*\{([^}]+)\}', html):
        block = m.group(1)
        basic = re.search(r'"basic"\s*:\s*(\d+)', block)
        if basic:
            prices_found.append(("embedded.price.basic", int(basic.group(1))))
        product = re.search(r'"product"\s*:\s*(\d+)', block)
        if product:
            prices_found.append(("embedded.price.product", int(product.group(1))))
        card = re.search(r'"card"\s*:\s*(\d+)', block)
        if card:
            prices_found.append(("embedded.price.card", int(card.group(1))))
        old = re.search(r'"old"\s*:\s*(\d+)', block)
        if old:
            prices_found.append(("embedded.price.old", int(old.group(1))))

    # Pattern 2: "priceU": N / "priceD": N
    for pk in ("priceU", "priceD", "price", "oldPrice"):
        for m in re.finditer(rf'"{pk}"\s*:\s*(\d+)', html):
            val = int(m.group(1))
            if val > 100:
                prices_found.append((f"embedded.{pk}", val))

    # Pattern 3: data-price attribute
    for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
        prices_found.append(("data-price", int(m.group(1))))

    # Pattern 4: og:price:amount
    m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
    if m:
        prices_found.append(("og:price:amount", int(round(float(m.group(1)) * 100))))

    # Pattern 5: visible text price (₽)
    for m in re.finditer(r'>\s*([\d\s\u00a0]{2,10})\s*(?:₽|руб)\s*<', html):
        txt = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            val = int(float(txt) * 100)
            if val > 100:
                prices_found.append(("text_price", val))
        except ValueError:
            pass

    # Pattern 6: JSON-LD
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if isinstance(item, dict):
                    off = item.get("offers") or item.get("price")
                    if isinstance(off, dict):
                        pv = off.get("price") or off.get("lowPrice")
                        if pv:
                            prices_found.append(("jsonld.offers.price", int(round(float(pv) * 100))))
                    elif isinstance(off, (int, float)):
                        prices_found.append(("jsonld.price", int(round(off * 100))))
        except (json.JSONDecodeError, ValueError):
            pass

    # Dedupe
    seen = set()
    unique_prices = []
    for label, val in prices_found:
        if val not in seen:
            seen.add(val)
            unique_prices.append((label, val))

    print(f"\nPrices found: {len(unique_prices)}")
    for label, val in unique_prices[:15]:
        print(f"  {label}: {val} ({val/100:.0f} RUB)")

    # --- Availability ---
    avail = "UNKNOWN"
    if re.search(r'в наличии|есть в наличии|купить|добавить в корзину', html, re.I):
        avail = "IN_STOCK"
    elif re.search(r'нет в наличии|закончился|распродан|ожидание|предзаказ', html, re.I):
        avail = "OUT_OF_STOCK"
    print(f"\nAvailability: {avail}")

    # --- Media ---
    imgs = set()
    for m in re.finditer(r'(?:src|data-src|data-original)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']', html, re.I):
        imgs.add(m.group(1))
    m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        imgs.add(m.group(1))
    print(f"Images: {len(imgs)}")
    for u in list(imgs)[:5]:
        print(f"  {u[:100]}")

    # --- Save snippet for debugging ---
    # Find the main product section
    m = re.search(r'(?:class|id)=["\'][^"\']*(?:product|card|item)[^"\']*["\'][^>]*>(.{0,2000})', html, re.I)
    if m:
        print(f"\nProduct section snippet: {m.group(1)[:500]}")

    # --- Check for anti-bot / redirect page ---
    if len(html) < 5000:
        print("\nWARNING: HTML suspiciously short — possible anti-bot page")
        print(f"  First 500 chars: {html[:500]}")

    browser.close()

print("\nDONE")
