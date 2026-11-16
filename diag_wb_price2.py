"""WB price: basket CDN paths + Playwright fallback."""
import json, time, logging, sys, re
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("wb_p2")

NM_ID = 199298871
VOL = NM_ID // 100000   # 1992
PART = NM_ID // 1000    # 199298
HOST = "basket-13.wbbasket.ru"  # known working host from main diag

try:
    from curl_cffi import requests as creq
    HAVE_CFFI = True
except ImportError:
    import requests as creq
    HAVE_CFFI = False

def cget(url, timeout=12):
    try:
        if HAVE_CFFI:
            r = creq.get(url, impersonate="chrome", timeout=timeout)
        else:
            r = creq.get(url, timeout=timeout)
        return r
    except Exception as e:
        log.warning("  ERR: %s", e)
        return None

print(f"=== WB PRICE v2 (nm={NM_ID}, host={HOST}) ===")

# --- Phase 1: Basket CDN price paths ---
base = f"https://{HOST}/vol{VOL}/part{PART}/{NM_ID}"
cdn_paths = [
    ("cdn_price", f"{base}/price/ru/price.json"),
    ("cdn_price2", f"{base}/price/price.json"),
    ("cdn_info_price", f"{base}/info/ru/price.json"),
    ("cdn_card_alt", f"{base}/info/ru/card_price.json"),
    ("cdn_data", f"{base}/data/ru/data.json"),
    ("cdn_meta", f"{base}/meta/ru/meta.json"),
    # Also try the img dir to confirm host works
    ("cdn_img_test", f"{base}/img/S800/1.jpg"),
]

print("\n--- Phase 1: Basket CDN ---")
for label, url in cdn_paths:
    log.info("TRY %s: %s", label, url)
    r = cget(url)
    if r is None:
        continue
    log.info("  -> %d, %dB", r.status_code, len(r.content))
    if r.status_code == 200:
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            try:
                data = r.json()
                s = json.dumps(data, ensure_ascii=False)
                log.info("  JSON keys: %s", list(data.keys())[:15] if isinstance(data, dict) else type(data))
                # Look for price
                for k in ["price", "priceU", "priceD", "salePriceU", "salePriceD", "oldPrice", "amount"]:
                    if k in s:
                        log.info("  *** FOUND '%s' ***", k)
                        # Extract context
                        idx = s.find(k)
                        log.info("  ctx: ...%s...", s[max(0,idx-20):idx+80])
            except Exception as e:
                log.info("  parse: %s, body[:200]=%s", e, r.text[:200])
        elif "image" in ct or "octet" in ct:
            log.info("  (binary/image OK — host confirmed)")
        else:
            log.info("  body[:200]=%s", r.text[:200])
    elif r.status_code == 404:
        log.info("  (404 — path doesn't exist)")
    else:
        log.info("  body[:100]=%s", r.text[:100])
    time.sleep(0.3)

# --- Phase 2: Playwright ---
print("\n--- Phase 2: Playwright ---")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        # Set locale
        page.set_extra_http_headers({"Accept-Language": "ru-RU,ru;q=0.9"})

        wb_url = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"
        log.info("Loading %s ...", wb_url)
        t0 = time.time()
        page.goto(wb_url, wait_until="domcontentloaded", timeout=30000)
        # Wait a bit for JS to render
        page.wait_for_timeout(4000)
        elapsed = time.time() - t0
        log.info("Page loaded in %.1fs, title=%s", elapsed, page.title()[:60])

        # Extract price from multiple sources
        price_found = False

        # 1. Meta tags
        meta_price = page.query_selector('meta[property="product:price:amount"], meta[property="og:price:amount"], meta[name="price"]')
        if meta_price:
            val = meta_price.get_attribute("content")
            log.info("META price: %s", val)
            price_found = True

        # 2. JSON-LD structured data
        scripts = page.query_selector_all("script[type='application/ld+json']")
        for sc in scripts:
            txt = sc.inner_text() or ""
            if "price" in txt.lower():
                try:
                    ld = json.loads(txt)
                    items = ld if isinstance(ld, list) else [ld]
                    for item in items:
                        if isinstance(item, dict) and "price" in json.dumps(item).lower():
                            log.info("JSON-LD: %s", json.dumps(item, ensure_ascii=False)[:400])
                            price_found = True
                            break
                except Exception:
                    pass

        # 3. DOM: common price selectors
        selectors = [
            '[data-testid="price"]',
            '.price',
            '[class*="price"]',
            '[class*="Price"]',
            '.c-price',
            '[data-auto="price"]',
            'span[class*="price"]',
        ]
        for sel in selectors:
            try:
                els = page.query_selector_all(sel)
                for el in els[:3]:
                    txt = (el.inner_text() or "").strip()
                    if txt and any(c.isdigit() for c in txt):
                        log.info("DOM %s: '%s'", sel, txt[:80])
                        price_found = True
            except Exception:
                pass

        # 4. Look in page source for embedded JSON with price
        content = page.content()
        # Common patterns
        patterns = [
            r'"price"\s*:\s*\{[^}]+\}',
            r'"priceU"\s*:\s*\d+',
            r'"salePriceU"\s*:\s*\d+',
            r'"amount"\s*:\s*[\d.]+',
            r'itemprop="price"[^>]*content="([\d.]+)"',
        ]
        for pat in patterns:
            m = re.search(pat, content)
            if m:
                log.info("SRC pattern '%s': %s", pat[:30], m.group(0)[:100])
                price_found = True

        # 5. Check if we got redirected to captcha/anti-bot
        cur_url = page.url
        if "captcha" in cur_url.lower() or "validate" in cur_url.lower():
            log.warning("REDIRECTED to: %s", cur_url)
        elif "498" in (page.inner_text("body") or "")[:500]:
            log.warning("Body contains 498 marker")

        if not price_found:
            log.warning("No price found via Playwright. Body[:500]=%s",
                        (page.inner_text("body") or "")[:500])

        browser.close()
    print("\n=== PLAYWRIGHT DONE ===")
except Exception as e:
    log.error("Playwright failed: %s", e, exc_info=True)

print("\n=== ALL DONE ===")
