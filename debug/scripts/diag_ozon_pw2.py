"""Ozon Playwright v2: дамп страницы, длинное ожидание, поиск данных."""
import sys, json, time, re
sys.path.insert(0, '.')
from playwright.sync_api import sync_playwright

# Несколько Ozon product IDs
PRODUCTS = [
    ("2118981797", "Тест 1"),
    ("158843943", "Тест 2 (популярный)"),
    ("206488787", "Тест 3"),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
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
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = { runtime: {} };
    """)
    page = ctx.new_page()

    for lid, label in PRODUCTS:
        url = f"https://www.ozon.ru/product/{lid}/"
        print(f"\n{'='*60}")
        print(f"OZON: {lid} ({label})")
        print(f"URL: {url}")
        print(f"{'='*60}")

        t0 = time.monotonic()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  goto error: {e}")
            continue
        print(f"  DCL: {time.monotonic()-t0:.1f}s")

        # Ждём networkidle
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
            print(f"  networkidle: {time.monotonic()-t0:.1f}s")
        except Exception:
            print(f"  networkidle timeout: {time.monotonic()-t0:.1f}s")

        # Дополнительное ожидание для JS
        page.wait_for_timeout(5000)

        # Проверка: не перенаправили ли?
        current_url = page.url
        if current_url != url:
            print(f"  REDIRECT: {current_url}")

        html = page.content()
        title = page.title()
        print(f"  Title: {title[:100]}")
        print(f"  HTML: {len(html)} bytes")

        # Дамп (очищенный)
        clean = re.sub(r'\s+', ' ', html[:4000])
        print(f"  HTML[:4000]: {clean[:2000]}")
        print()

        # --- Title ---
        m = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            print(f"  og:title: {m.group(1)[:80]}")

        # --- Prices ---
        prices = set()
        for m in re.finditer(r'"price"\s*:\s*(\d+)', html):
            v = int(m.group(1))
            if v > 100:
                prices.add((f"json.price", v))
        for m in re.finditer(r'"value"\s*:\s*(\d{4,})', html):
            v = int(m.group(1))
            if 100 < v < 10000000:
                prices.add((f"json.value", v))
        for m in re.finditer(r'"amount"\s*:\s*(\d+)', html):
            v = int(m.group(1))
            if v > 100:
                prices.add((f"json.amount", v))
        # Text price
        for m in re.finditer(r'>\s*([\d\s\u00a0]{2,10})\s*(?:₽|руб)\s*<', html):
            try:
                v = int(float(m.group(1).replace(" ", "").replace("\u00a0", "")) * 100)
                if v > 100:
                    prices.add((f"text", v))
            except ValueError:
                pass

        print(f"  Prices ({len(prices)}):")
        for label, val in sorted(prices, key=lambda x: x[1])[:10]:
            print(f"    {label}: {val} ({val/100:.0f} RUB)")

        # --- data-widget ---
        widgets = re.findall(r'data-widget=["\']([^"\']+)', html)
        if widgets:
            print(f"  data-widgets: {len(widgets)} (first 8: {widgets[:8]})")

        # --- Availability ---
        if re.search(r'в наличии|купить|добавить в корзину|купить в 1 клик|забронировать', html, re.I):
            print("  Availability: IN_STOCK")
        elif re.search(r'нет в наличии|закончился|ожидание|предзаказ', html, re.I):
            print("  Availability: OUT_OF_STOCK")
        else:
            print("  Availability: UNKNOWN")

        # --- Images ---
        imgs = set()
        for m in re.finditer(r'(?:src|data-src|data-original)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', html, re.I):
            imgs.add(m.group(1))
        for m in re.finditer(r'(?:src|data-src|data-original)=["\']([^"\']*ozoncdn\.net/[^"\']+)["\']', html, re.I):
            imgs.add(m.group(1))
        print(f"  Images: {len(imgs)}")
        for u in list(imgs)[:3]:
            print(f"    {u[:90]}")

        # --- Anti-bot check ---
        if len(html) < 5000:
            print(f"  WARNING: short page — possible challenge/redirect")
            if "challenge" in html.lower() or "captcha" in html.lower():
                print("  Marker: CHALLENGE/CAPTCHA")
            if "cookie" in html.lower()[:2000]:
                print("  Marker: cookie wall")
            if "robot" in html.lower() or "bot" in html.lower()[:3000]:
                print("  Marker: bot detection")

        page.wait_for_timeout(1000)

    browser.close()

print("\nDONE")
