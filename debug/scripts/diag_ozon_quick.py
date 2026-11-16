"""Ozon: быстрый тест — HTTP + Composer API + Playwright."""
import sys, json, time, re
sys.path.insert(0, '.')

# Известный Ozon product ID (из поиска)
LID = "2118981797"
URL = f"https://www.ozon.ru/product/{LID}/"

print("=" * 60)
print("1. COMPOSER API (HTTP)")
print("=" * 60)

from curl_cffi import requests as cffi_req
H = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

composer_urls = [
    f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{LID}/",
    f"https://www.ozon.ru/api/entrypoint/pdp/{LID}",
]

for i, url in enumerate(composer_urls):
    t0 = time.monotonic()
    try:
        r = cffi_req.get(url, headers=H, impersonate='chrome', timeout=15)
        dt = time.monotonic() - t0
        print(f"  [{i}] {r.status_code} ({dt:.1f}s) len={len(r.text)}")
        if r.status_code == 200:
            try:
                d = r.json()
                print(f"      keys: {list(d.keys())[:10]}")
                # Look for price
                s = json.dumps(d, ensure_ascii=False)
                for pat in [r'"price"\s*:\s*(\d+)', r'"value"\s*:\s*(\d{4,})', r'"amount"\s*:\s*(\d+)']:
                    m = re.search(pat, s)
                    if m:
                        print(f"      price match ({pat[:20]}): {m.group(1)}")
                        break
            except Exception as e:
                print(f"      JSON parse: {e}")
        else:
            print(f"      body[:100]: {r.text[:100]}")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"  [{i}] ERR ({dt:.1f}s) {str(e)[:80]}")
    time.sleep(2)

print("\n" + "=" * 60)
print("2. SSR HTML (curl_cffi)")
print("=" * 60)

H_HTML = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}
t0 = time.monotonic()
try:
    r = cffi_req.get(URL, headers=H_HTML, impersonate='chrome', timeout=20)
    dt = time.monotonic() - t0
    print(f"  HTTP: {r.status_code} ({dt:.1f}s), {len(r.text)} bytes")
    if r.status_code == 200:
        html = r.text
        # og:title
        m = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            print(f"  og:title: {m.group(1)[:80]}")
        # data-widget
        widgets = re.findall(r'data-widget=["\']([^"\']+)', html)
        print(f"  data-widgets: {len(widgets)} (first 5: {widgets[:5]})")
        # price patterns
        for pat in [r'"price"\s*:\s*(\d+)', r'"value"\s*:\s*(\d{4,})', r'"amount"\s*:\s*(\d+)']:
            m = re.search(pat, html)
            if m:
                print(f"  price ({pat[:20]}): {m.group(1)}")
        # images
        imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru[^"\']*)["\']', html, re.I)
        print(f"  images: {len(imgs)} (first: {imgs[:2]})")
    else:
        print(f"  body[:200]: {r.text[:200]}")
except Exception as e:
    dt = time.monotonic() - t0
    print(f"  ERR ({dt:.1f}s) {str(e)[:100]}")

print("\n" + "=" * 60)
print("3. PLAYWRIGHT")
print("=" * 60)

from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = ctx.new_page()

    t0 = time.monotonic()
    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  goto: {e}")
        browser.close()
        sys.exit(1)
    dt = time.monotonic() - t0
    print(f"  Loaded in {dt:.1f}s")
    page.wait_for_timeout(4000)

    html = page.content()
    print(f"  HTML: {len(html)} bytes")

    # Title
    m = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if m:
        print(f"  Title: {m.group(1)[:80]}")

    # Price from data-widget or embedded
    price_vals = set()
    for m in re.finditer(r'"price"\s*:\s*(\d+)', html):
        v = int(m.group(1))
        if v > 100:
            price_vals.add(v)
    for m in re.finditer(r'"value"\s*:\s*(\d{4,})', html):
        v = int(m.group(1))
        if 100 < v < 10000000:
            price_vals.add(v)
    for m in re.finditer(r'"amount"\s*:\s*(\d+)', html):
        v = int(m.group(1))
        if v > 100:
            price_vals.add(v)
    # Visible text
    for m in re.finditer(r'>\s*([\d\s\u00a0]{2,10})\s*(?:₽|руб)\s*<', html):
        try:
            v = int(float(m.group(1).replace(" ", "").replace("\u00a0", "")) * 100)
            if v > 100:
                price_vals.add(v)
        except ValueError:
            pass

    print(f"  Price candidates: {sorted(price_vals)[:10]}")

    # Availability
    if re.search(r'в наличии|купить|добавить в корзину|купить в 1 клик', html, re.I):
        print("  Availability: IN_STOCK")
    elif re.search(r'нет в наличии|закончился|ожидание', html, re.I):
        print("  Availability: OUT_OF_STOCK")
    else:
        print("  Availability: UNKNOWN")

    # Images
    imgs = set()
    for m in re.finditer(r'(?:src|data-src)=["\']([^"\']*ir\.ozone\.ru/[^"\']+)["\']', html, re.I):
        imgs.add(m.group(1))
    print(f"  Images: {len(imgs)}")
    for u in list(imgs)[:3]:
        print(f"    {u[:90]}")

    browser.close()

print("\nDONE")
