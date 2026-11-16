"""Playwright headless=False, direct (без прокси). Проходит ли JS-challenge WAF?
Проверяем OZON search, OZON product, WB search HTML."""
import time
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def probe(pw, tag, url, wait_ms=4000):
    t0 = time.monotonic()
    try:
        with pw.new_context(user_agent=UA, locale="ru-RU",
                            viewport={"width": 1366, "height": 900}) as ctx:
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(wait_ms / 1000.0)
            html = page.content()
            status = resp.status if resp else 0
            # индикаторы WAF/антибота
            low = html[:12000].lower()
            waf = sum(1 for w in ("captcha", "antibot", "challenge", "puzzle",
                                  "slider", "fab_chlg", "abt-challenge") if w in low)
            # товарные индикаторы
            prod = html.count("/product/") + html.count("detail.aspx") + html.count("data-tid")
            print(f"{tag} -> HTTP {status} len={len(html)} waf_hits={waf} prod_markers={prod} ({time.monotonic()-t0:.1f}s)")
            return status, html
    except Exception as e:
        print(f"{tag} -> ERR {type(e).__name__}: {str(e)[:140]} ({time.monotonic()-t0:.1f}s)")
        return None, ""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
    print("browser started headless=False")
    probe(b, "OZON-search-p2", "https://www.ozon.ru/search/?text=butbuki&from_global=true&page=2", 5000)
    probe(b, "OZON-product", "https://www.ozon.ru/product/2748584093/", 5000)
    probe(b, "WB-search-html", "https://www.wildberries.ru/catalog/0/ru/?search=video&page=1", 5000)
    b.close()
print("done")
