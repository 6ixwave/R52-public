"""WB Playwright v2: популярный товар, networkidle, дамп HTML, поиск цен."""
import sys, json, time, re
sys.path.insert(0, '.')
from playwright.sync_api import sync_playwright

# Несколько товаров для проверки
PRODUCTS = [
    ("199298871", "Овсяный коктейль (тот же)"),
    ("181752683", "Популярный товар (проверка)"),
    ("174756364", "Ещё один"),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size=1920,1080",
        ],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        timezone_id="Asia/Vladivostok",
        extra_http_headers={
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    # Анти-детект
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = { runtime: {} };
    """)
    page = ctx.new_page()

    for lid, label in PRODUCTS:
        url = f"https://www.wildberries.ru/catalog/{lid}/detail.aspx"
        print(f"\n{'='*60}")
        print(f"PRODUCT: {lid} ({label})")
        print(f"URL: {url}")
        print(f"{'='*60}")

        t0 = time.monotonic()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  goto error: {e}")
            continue
        dt_load = time.monotonic() - t0
        print(f"  DCL in {dt_load:.1f}s")

        # Ждём networkidle (JS-рендер)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            print(f"  networkidle OK ({time.monotonic()-t0:.1f}s)")
        except Exception:
            print(f"  networkidle timeout ({time.monotonic()-t0:.1f}s), continuing")

        # Дополнительная пауза
        page.wait_for_timeout(2000)

        html = page.content()
        title = page.title()
        print(f"  Title: {title[:80]}")
        print(f"  HTML: {len(html)} bytes")

        # Дамп структуры (первые 3000 символов, очищенный)
        clean = re.sub(r'\s+', ' ', html[:3000])
        print(f"  HTML[:3000]: {clean[:1500]}")
        print()

        # --- Поиск цен ---
        prices = []

        # 1. Embedded JSON: "price": {"basic": N}
        for m in re.finditer(r'"price"\s*:\s*\{([^}]+)\}', html):
            block = m.group(1)
            for pk in ("basic", "product", "card", "wallet", "old", "crossed"):
                pm = re.search(rf'"{pk}"\s*:\s*(\d+)', block)
                if pm and int(pm.group(1)) > 100:
                    prices.append((f"json.price.{pk}", int(pm.group(1))))

        # 2. priceU / priceD
        for pk in ("priceU", "priceD"):
            for m in re.finditer(rf'"{pk}"\s*:\s*(\d+)', html):
                v = int(m.group(1))
                if v > 100:
                    prices.append((f"json.{pk}", v))

        # 3. data-price
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
            v = int(m.group(1))
            if v > 100:
                prices.append(("data-price", v))

        # 4. og:price
        m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            prices.append(("og:price", int(round(float(m.group(1)) * 100))))

        # 5. Text: NNN ₽
        for m in re.finditer(r'>\s*([\d\s\u00a0]{2,10})\s*(?:₽|руб)\s*<', html):
            txt = m.group(1).replace(" ", "").replace("\u00a0", "")
            try:
                v = int(float(txt) * 100)
                if v > 100:
                    prices.append(("text_rub", v))
            except ValueError:
                pass

        # 6. JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.DOTALL):
            try:
                ld = json.loads(m.group(1))
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if isinstance(item, dict):
                        off = item.get("offers")
                        if isinstance(off, dict):
                            pv = off.get("price") or off.get("lowPrice")
                            if pv:
                                prices.append(("jsonld.price", int(round(float(pv) * 100))))
            except (json.JSONDecodeError, ValueError):
                pass

        # Dedupe
        seen = set()
        unique = []
        for label, val in prices:
            if val not in seen:
                seen.add(val)
                unique.append((label, val))

        print(f"  PRICES ({len(unique)}):")
        for label, val in unique[:10]:
            print(f"    {label}: {val} ({val/100:.0f} RUB)")

        # --- Availability ---
        avail = "UNKNOWN"
        if re.search(r'в наличии|есть в наличии|купить|добавить в корзину|купить в 1 клик', html, re.I):
            avail = "IN_STOCK"
        elif re.search(r'нет в наличии|закончился|распродан|ожидание|предзаказ', html, re.I):
            avail = "OUT_OF_STOCK"
        print(f"  Availability: {avail}")

        # --- Media ---
        imgs = set()
        for m in re.finditer(r'(?:src|data-src|data-original)=["\']([^"\']*wbbasket\.ru[^"\']*)["\']', html, re.I):
            imgs.add(m.group(1))
        m = re.search(r'og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            imgs.add(m.group(1))
        print(f"  Images: {len(imgs)}")

        # --- Check if it's an error/anti-bot page ---
        if len(html) < 10000:
            print(f"  WARNING: short HTML — possible anti-bot/error page")
            # Look for specific markers
            if "Что-то не так" in html or "error" in html.lower()[:2000]:
                print("  Marker: error page detected")
            if "captcha" in html.lower() or "captcha" in title.lower():
                print("  Marker: CAPTCHA detected")
            if "cookie" in html.lower()[:3000]:
                print("  Marker: cookie wall")

        page.wait_for_timeout(1000)

    browser.close()

print("\nDONE")
