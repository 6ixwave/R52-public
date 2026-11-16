"""Диагностика v4: FARPOST live ID, ALI Playwright, AVITO Playwright."""
import sys, time, json, re
from curl_cffi import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

def hit(label, url, headers=None, timeout=15):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {url}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    try:
        h = {**H, **(headers or {})}
        r = cffi_req.get(url, impersonate="chrome", headers=h, timeout=timeout)
        dt = time.monotonic() - t0
        print(f"  Status: {r.status_code} | Time: {dt:.1f}s | Len: {len(r.text)}")
        return r
    except Exception as e:
        print(f"  EXCEPTION ({time.monotonic()-t0:.1f}s): {type(e).__name__}: {e}")
        return None

# ============================================================
# 1. FARPOST — live bulletin ID
# ============================================================
print("\n" + "#"*60)
print("# 1. FARPOST LIVE BULLETIN")
print("#"*60)

# Живые ID из категории: 88005649, 128861033, 85875417
fp_ids = ["88005649", "128861033", "85875417"]
for bid in fp_ids:
    # Farpost URL формат: /vladivostok/{category}/{slug}-{id}.html
    # Но короткий: /bulletin/{id} или /vladivostok/{id}.html
    urls = [
        f"https://www.farpost.ru/bulletin/{bid}",
        f"https://www.farpost.ru/vladivostok/{bid}.html",
    ]
    for u in urls:
        r = hit(f"FARPOST {bid} → {u.split('.ru')[1]}", u, timeout=15)
        if r and r.status_code == 200 and len(r.text) > 5000:
            html = r.text
            # Title
            tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
            print(f"  TITLE: {(tm.group(1).strip()[:80] if tm else '?')}")
            # Price
            price = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
            print(f"  PRICE (data-attr): {price[:3]}")
            # JSON-LD
            jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
            for i, block in enumerate(jsonld[:2]):
                try:
                    d = json.loads(block)
                    print(f"  JSON-LD[{i}]: type={d.get('@type')}, name={str(d.get('name',''))[:60]}")
                    if 'offers' in d:
                        print(f"    offers: {json.dumps(d['offers'], ensure_ascii=False)[:200]}")
                except:
                    print(f"  JSON-LD[{i}] raw: {block[:150]}")
            # Images
            imgs = re.findall(r'(https?://static\.baza\.farpost\.ru/[^"\'\s]+)', html)
            print(f"  IMAGES: {len(imgs)}, first 3: {[i[:60] for i in imgs[:3]]}")
            # Lightbox
            lb = re.search(r'baza\.lightbox\.init\(\s*(\[[\s\S]*?\])\s*\)', html)
            if lb:
                print(f"  LIGHTBOX: {lb.group(1)[:200]}")
            # Specs
            specs = re.findall(r'class="label">([^<]+)</div>\s*<div class="value">\s*<span[^>]*>(.*?)</span>', html, re.DOTALL)
            print(f"  SPECS: {specs[:5]}")
            break
        elif r and r.status_code == 200:
            print(f"  (200 but short: {len(r.text)} chars)")
        time.sleep(1)
    else:
        continue
    break  # первый успешный — хватит

# ============================================================
# 2. ALI EXPRESS — Playwright (CSR → нужен JS)
# ============================================================
print("\n" + "#"*60)
print("# 2. ALI EXPRESS VIA PLAYWRIGHT")
print("#"*60)

ali_id = "1005011813323763"
ali_url = f"https://www.aliexpress.com/item/{ali_id}.html"

try:
    from playwright.sync_api import sync_playwright
    print(f"  Launching Playwright for ALI...")
    t0 = time.monotonic()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        user_agent=UA,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    page = context.new_page()
    page.goto(ali_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(10)  # ждём CSR render
    
    html = page.content()
    dt = time.monotonic() - t0
    title = page.title()
    print(f"  Time: {dt:.1f}s | Title: {title[:80]} | HTML: {len(html)}")
    
    # JSON-LD
    jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"  JSON-LD: {len(jsonld)}")
    for i, block in enumerate(jsonld[:3]):
        try:
            d = json.loads(block)
            print(f"  [{i}] type={d.get('@type')}, name={str(d.get('name',''))[:60]}")
            if 'offers' in d:
                print(f"      offers: {json.dumps(d['offers'], ensure_ascii=False)[:250]}")
            if 'image' in d:
                print(f"      image: {str(d['image'])[:150]}")
        except:
            print(f"  [{i}] raw: {block[:200]}")
    
    # og: meta
    og = re.findall(r'<meta\s+property=["\']og:([^"\']+)["\']\s+content=["\']([^"\']*)["\']', html)
    print(f"  OG: {[(k,v[:60]) for k,v in og[:8]]}")
    
    # Price в DOM
    price_el = page.query_selector('[class*="price"] [class*="current"], .price--current, [data-pl="price"]')
    if price_el:
        print(f"  PRICE DOM: {price_el.inner_text()[:50]}")
    
    # Все цены regex
    prices = re.findall(r'\$\s?([\d,]+\.?\d*)', html)
    print(f"  PRICES regex: {prices[:10]}")
    
    # Images
    imgs = re.findall(r'(https?://[^"\'\s]*aliexpress-media[^"\'\s]*\.(?:jpg|jpeg|png|webp)[^"\'\s]*)', html)
    imgs2 = re.findall(r'(https?://[^"\'\s]*alicdn[^"\'\s]*\.(?:jpg|jpeg|png|webp)[^"\'\s]*)', html)
    all_imgs = list(dict.fromkeys(imgs + imgs2))
    print(f"  IMAGES: {len(all_imgs)}")
    for img in all_imgs[:5]:
        print(f"    {img[:100]}")
    
    # Title from DOM
    h1 = page.query_selector("h1")
    if h1:
        print(f"  H1: {h1.inner_text()[:80]}")
    
    # Specs / attributes
    attrs = re.findall(r'"(?:attributeName|attrName|name)"\s*:\s*"([^"]+)"\s*,\s*"(?:attributeValue|attrValue|value)"\s*:\s*"([^"]+)"', html)
    if attrs:
        print(f"  ATTRS: {attrs[:8]}")
    
    with open("ali_pw_dump.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved → ali_pw_dump.html ({len(html)} bytes)")
    
    context.close()
    browser.close()
    pw.stop()
except Exception as e:
    print(f"  PLAYWRIGHT ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# ============================================================
# 3. AVITO — Playwright (category → live ID → ad)
# ============================================================
print("\n" + "#"*60)
print("# 3. AVITO VIA PLAYWRIGHT")
print("#"*60)

try:
    from playwright.sync_api import sync_playwright
    print(f"  Launching Playwright for AVITO...")
    t0 = time.monotonic()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        user_agent=UA,
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )
    page = context.new_page()
    
    # Step 1: Category page
    page.goto("https://www.avito.ru/vladivostok/telefony", wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)
    html_cat = page.content()
    print(f"  Category: {len(html_cat)} chars, title: {page.title()[:60]}")
    
    # Extract ad IDs from category
    ids = re.findall(r'avito\.ru/(?:vladivostok|ad)/[^\s"\'<>]*/(\d{6,})', html_cat)
    ids2 = re.findall(r'/ad/(\d{6,})', html_cat)
    data_ids = re.findall(r'data-ad-id=["\'](\d+)["\']', html_cat)
    all_ids = list(dict.fromkeys(ids + ids2 + data_ids))
    print(f"  Ad IDs found: {len(all_ids)}, first 5: {all_ids[:5]}")
    
    # Step 2: Open first live ad
    if all_ids:
        ad_id = all_ids[0]
        ad_url = f"https://www.avito.ru/ad/{ad_id}"
        print(f"  Opening ad: {ad_url}")
        page2 = context.new_page()
        page2.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)
        
        html_ad = page2.content()
        print(f"  Ad page: {len(html_ad)} chars, title: {page2.title()[:80]}")
        
        # JSON-LD
        jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_ad, re.DOTALL)
        print(f"  JSON-LD: {len(jsonld)}")
        for i, block in enumerate(jsonld[:3]):
            try:
                d = json.loads(block)
                print(f"  [{i}] type={d.get('@type')}, name={str(d.get('name',''))[:60]}")
                if 'offers' in d:
                    print(f"      offers: {json.dumps(d['offers'], ensure_ascii=False)[:200]}")
            except:
                print(f"  [{i}] raw: {block[:200]}")
        
        # og: meta
        og = re.findall(r'<meta\s+property=["\']og:([^"\']+)["\']\s+content=["\']([^"\']*)["\']', html_ad)
        print(f"  OG: {[(k,v[:60]) for k,v in og[:8]]}")
        
        # Price
        price_el = page2.query_selector('[data-testid="price"], .price, [class*="price"]')
        if price_el:
            print(f"  PRICE DOM: {price_el.inner_text()[:50]}")
        
        # Images
        imgs = re.findall(r'(https?://[^"\'\s]*(?:avatars\.mmedia|l16\.avito|l26\.avito)[^"\'\s]*)', html_ad)
        print(f"  IMAGES: {len(imgs)}, first 3: {[i[:70] for i in imgs[:3]]}")
        
        # Title
        h1 = page2.query_selector("h1")
        if h1:
            print(f"  H1: {h1.inner_text()[:80]}")
        
        with open("avito_ad_dump.html", "w", encoding="utf-8") as f:
            f.write(html_ad)
        print(f"  Saved → avito_ad_dump.html ({len(html_ad)} bytes)")
        
        page2.close()
    
    context.close()
    browser.close()
    pw.stop()
    print(f"  Total time: {time.monotonic()-t0:.1f}s")
except Exception as e:
    print(f"  PLAYWRIGHT ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print("\n\nDONE v4.")
