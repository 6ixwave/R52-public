"""Диагностика AliExpress через Playwright: перехват API, рендер, извлечение."""
import sys
import json
import logging
import re
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

LID = "1005011813323763"
URL = f"https://www.aliexpress.com/item/{LID}.html"

captured = []  # (url, status, body_snippet)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="ru-RU",
        viewport={"width": 1920, "height": 1080},
    )
    page = ctx.new_page()

    # Перехват XHR/fetch
    def on_response(resp):
        try:
            url = resp.url
            status = resp.status
            # Интересуют JSON-ответы и API-эндпоинты
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "api" in url.lower() or "search" in url.lower() or "detail" in url.lower():
                body = resp.text()[:2000]
                captured.append((url, status, ct, body))
                print(f"  [NET] {status} {ct[:30]} {url[:120]}")
                print(f"        body[:200]: {body[:200]}")
        except Exception:
            pass

    page.on("response", on_response)

    print(f"\nLoading: {URL}")
    t0 = time.monotonic()
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    print(f"DOMContentLoaded: {time.monotonic()-t0:.1f}s")

    # Ждём networkidle или конкретный селектор
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
        print(f"networkidle: {time.monotonic()-t0:.1f}s")
    except Exception:
        print(f"networkidle timeout, continuing at {time.monotonic()-t0:.1f}s")

    # Дополнительная пауза для SPA
    page.wait_for_timeout(3000)
    print(f"Total load: {time.monotonic()-t0:.1f}s")

    # --- Извлечение из DOM ---
    html = page.content()
    print(f"\nRendered HTML: {len(html)} bytes")

    # Title
    h1 = page.query_selector("h1")
    title = h1.inner_text().strip() if h1 else None
    title_tag = page.query_selector("title")
    title_tag_txt = title_tag.inner_text().strip() if title_tag else None
    print(f"h1: {title[:100] if title else 'N/A'}")
    print(f"<title>: {title_tag_txt[:100] if title_tag_txt else 'N/A'}")

    # Price - ищем по классам
    price_selectors = [
        "[class*='price-current']",
        "[class*='Price-current']",
        "[class*='price-current-integer']",
        "[class*='price-current-decimal']",
        "[class*='price-current-symbol']",
        "[data-price]",
        "[class*='price']",
        "[class*='Price']",
    ]
    print("\n=== Price elements ===")
    for sel in price_selectors:
        els = page.query_selector_all(sel)
        if els:
            for el in els[:5]:
                txt = el.inner_text().strip()[:80]
                cls = el.get_attribute("class") or ""
                dp = el.get_attribute("data-price") or ""
                print(f"  [{sel}] class={cls[:60]} data-price={dp} text={txt}")
        if els:
            break

    # Broader: все элементы с текстом похожим на цену
    print("\n=== Price-like text ===")
    all_spans = page.query_selector_all("span, div, b, strong")
    price_re = re.compile(r'^[\d,.]+\s*(USD|EUR|GBP|RUB|\$|€|£|₽)?$', re.I)
    found_prices = []
    for el in all_spans[:200]:
        try:
            txt = el.inner_text().strip()
            if price_re.match(txt) and len(txt) < 20:
                found_prices.append(txt)
        except Exception:
            pass
    print(f"  Found {len(found_prices)} price-like elements: {found_prices[:10]}")

    # Images
    print("\n=== Images ===")
    imgs = page.query_selector_all("img")
    alicdn_imgs = []
    for img in imgs:
        src = img.get_attribute("src") or ""
        data_src = img.get_attribute("data-src") or ""
        url = src or data_src
        if "alicdn" in url and "assets" not in url and "s.alicdn" not in url:
            alicdn_imgs.append(url)
    print(f"  Total imgs: {len(imgs)}, alicdn product imgs: {len(alicdn_imgs)}")
    for u in alicdn_imgs[:8]:
        print(f"    {u[:120]}")

    # Availability
    print("\n=== Availability ===")
    avail_text = page.inner_text("body")[:5000]
    for pat in ["In Stock", "Out of Stock", "Pre-order", "Available", "Unavailable",
                "В наличии", "Нет в наличии", "Предзаказ", "Add to Cart", "Buy Now"]:
        if pat.lower() in avail_text.lower():
            print(f"  Found: '{pat}'")

    # window.__INIT_DATA_STATE__ after render
    print("\n=== Post-render JS state ===")
    for var in ["__INIT_DATA_STATE__", "runParams", "__NEXT_DATA__", "__NUXT__"]:
        val = page.evaluate(f"window.{var} || null")
        if val:
            s = json.dumps(val, ensure_ascii=False)
            print(f"  {var}: {s[:500]}")
        else:
            print(f"  {var}: null")

    # window._d_c_
    d_c = page.evaluate("window._d_c_ || null")
    if d_c:
        print(f"  _d_c_: {json.dumps(d_c, ensure_ascii=False)[:500]}")

    # Check for product data in any global
    print("\n=== Global product data search ===")
    product_globals = page.evaluate("""
    () => {
        const results = {};
        for (const key of Object.keys(window)) {
            if (key.startsWith('__') || key.includes('product') || key.includes('detail') || key.includes('item')) {
                try {
                    const v = window[key];
                    if (typeof v === 'object' && v !== null) {
                        const s = JSON.stringify(v);
                        if (s && s.length > 50 && s.length < 50000) {
                            results[key] = s.substring(0, 300);
                        }
                    }
                } catch(e) {}
            }
        }
        return results;
    }
    """)
    for k, v in list(product_globals.items())[:10]:
        print(f"  {k}: {v[:200]}")

    # --- Network captures summary ---
    print(f"\n=== Network captures: {len(captured)} ===")
    for url, status, ct, body in captured:
        if "json" in ct:
            print(f"\n  [{status}] {url[:150]}")
            print(f"  CT: {ct}")
            print(f"  Body[:500]: {body[:500]}")

    browser.close()

print(f"\nDone. Total: {time.monotonic()-t0:.1f}s")
