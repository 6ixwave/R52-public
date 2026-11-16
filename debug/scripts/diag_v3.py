"""Диагностика v3: ALI deep-parse, OZON via Playwright, живые ID AVITO/FARPOST."""
import sys, time, json, re, os
from curl_cffi import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}

def hit(label, url, headers=None, timeout=15, verify=True):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {url}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    try:
        h = {**H, **(headers or {})}
        r = cffi_req.get(url, impersonate="chrome", headers=h, timeout=timeout, verify=verify)
        dt = time.monotonic() - t0
        print(f"  Status: {r.status_code} | Time: {dt:.1f}s | Len: {len(r.text)}")
        return r
    except Exception as e:
        print(f"  EXCEPTION ({time.monotonic()-t0:.1f}s): {type(e).__name__}: {e}")
        return None

# ============================================================
# 1. ALI EXPRESS — глубокий поиск state
# ============================================================
print("\n" + "#"*60)
print("# 1. ALI EXPRESS DEEP PARSE")
print("#"*60)

# Живой ID из поиска
ali_ids = ["1005011813323763", "1005012619900154", "1005001234567890"]
ali_html = None
for aid in ali_ids:
    url = f"https://www.aliexpress.com/item/{aid}.html"
    r = hit(f"ALI {aid}", url, timeout=20)
    if r and r.status_code == 200 and len(r.text) > 10000:
        ali_html = r.text
        print(f"  >>> Got {len(ali_html)} chars")
        break
    time.sleep(1)

if ali_html:
    # Поиск всех script-блоков с JSON
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', ali_html, re.DOTALL)
    print(f"\n  Total <script> blocks: {len(scripts)}")
    
    # Ищем большие JSON-блоки (>1000 chars)
    for i, s in enumerate(scripts):
        s_stripped = s.strip()
        if len(s_stripped) > 500:
            preview = s_stripped[:200].replace('\n', ' ')
            print(f"  Script[{i}] len={len(s_stripped)}: {preview}")
    
    # Паттерны state
    state_patterns = [
        (r'window\._dida_config\s*=\s*', "_dida_config"),
        (r'window\.__INITIAL_DATA__\s*=\s*', "__INITIAL_DATA__"),
        (r'window\.__INITIAL_STATE__\s*=\s*', "__INITIAL_STATE__"),
        (r'window\.__NEXT_DATA__\s*=\s*', "__NEXT_DATA__"),
        (r'"pageData"\s*:\s*', "pageData"),
        (r'"productInfo"\s*:\s*', "productInfo"),
        (r'"skuPriceList"\s*:\s*', "skuPriceList"),
        (r'"title"\s*:\s*"', "title-field"),
        (r'"price"\s*:\s*\{', "price-obj"),
        (r'"imageList"\s*:\s*\[', "imageList"),
        (r'"mainImage"\s*:', "mainImage"),
        (r'window\.__AE_GLOBAL__', "__AE_GLOBAL__"),
        (r'"seoData"\s*:\s*\{', "seoData"),
        (r'"detailData"\s*:\s*\{', "detailData"),
    ]
    print(f"\n  --- State pattern matches ---")
    for pat, label in state_patterns:
        matches = list(re.finditer(pat, ali_html))
        if matches:
            m = matches[0]
            ctx = ali_html[m.start():m.start()+150].replace('\n', ' ')
            print(f"  [{label}] x{len(matches)}: {ctx[:120]}")
    
    # og: meta (пополненный)
    og = re.findall(r'<meta\s+(?:property|name)=["\']([^"\']+)["\']\s+content=["\']([^"\']*)["\']', ali_html)
    print(f"\n  --- META tags ({len(og)}) ---")
    for k, v in og[:15]:
        print(f"    {k}: {v[:80]}")
    
    # Title
    tm = re.search(r'<title[^>]*>(.*?)</title>', ali_html, re.DOTALL)
    print(f"\n  TITLE: {(tm.group(1).strip()[:100] if tm else 'NOT FOUND')}")
    
    # Все картинки alicdn
    imgs = re.findall(r'(https?://[^"\'\s]*alicdn[^"\'\s]*\.(?:jpg|jpeg|png|webp)[^"\'\s]*)', ali_html)
    imgs2 = re.findall(r'(https?://[^"\'\s]*aliexpress-media[^"\'\s]*\.(?:jpg|jpeg|png|webp)[^"\'\s]*)', ali_html)
    print(f"  IMAGES alicdn: {len(imgs)}, aliexpress-media: {len(imgs2)}")
    for img in (imgs + imgs2)[:5]:
        print(f"    {img[:100]}")
    
    # Цены
    prices = re.findall(r'"(?:price|amount|minPrice|maxPrice)"\s*:\s*"?([\d.]+)"?', ali_html)
    print(f"  PRICE matches: {prices[:10]}")
    
    # Сохраняем HTML для анализа
    with open("ali_dump.html", "w", encoding="utf-8") as f:
        f.write(ali_html)
    print(f"\n  Saved → ali_dump.html ({len(ali_html)} bytes)")

# ============================================================
# 2. OZON — Playwright
# ============================================================
print("\n" + "#"*60)
print("# 2. OZON VIA PLAYWRIGHT")
print("#"*60)

ozon_id = "1688044752"  # iPhone 16 из поиска
ozon_url = f"https://www.ozon.ru/product/{ozon_id}/"

try:
    from playwright.sync_api import sync_playwright
    print(f"  Launching Playwright...")
    t0 = time.monotonic()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=[
        "--no-sandbox", "--disable-blink-features=AutomationControlled"
    ])
    context = browser.new_context(
        user_agent=UA,
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )
    page = context.new_page()
    page.goto(ozon_url, wait_until="domcontentloaded", timeout=30000)
    # Ждём антибот challenge
    time.sleep(8)
    # Пробуем дождаться контента
    try:
        page.wait_for_selector("h1, [data-widget='ProductTitle'], .product-title", timeout=15000)
    except:
        pass
    time.sleep(2)
    
    html = page.content()
    dt = time.monotonic() - t0
    title = page.title()
    print(f"  Time: {dt:.1f}s | Title: {title[:80]} | HTML len: {len(html)}")
    
    # Парсинг
    jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"  JSON-LD blocks: {len(jsonld)}")
    for i, block in enumerate(jsonld[:3]):
        try:
            data = json.loads(block)
            print(f"  JSON-LD[{i}]: type={data.get('@type')}, name={str(data.get('name',''))[:60]}")
            if data.get('offers'):
                print(f"    offers: {json.dumps(data['offers'], ensure_ascii=False)[:200]}")
        except:
            print(f"  JSON-LD[{i}] raw: {block[:200]}")
    
    og = re.findall(r'<meta\s+property=["\']og:([^"\']+)["\']\s+content=["\']([^"\']*)["\']', html)
    print(f"  OG: {og[:8]}")
    
    imgs = re.findall(r'(https?://[^"\'\s]*ir\.ozone\.ru[^"\'\s]*)', html)
    print(f"  OZON CDN images: {len(imgs)}, first 3: {imgs[:3]}")
    
    # State
    state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
    if state_m:
        print(f"  __INITIAL_STATE__: {len(state_m.group(1))} chars")
    
    data_widget = re.findall(r'data-widget=["\']([^"\']+)["\']', html)
    print(f"  data-widget types: {list(set(data_widget))[:15]}")
    
    # Сохраняем
    with open("ozon_dump.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved → ozon_dump.html ({len(html)} bytes)")
    
    context.close()
    browser.close()
    pw.stop()
except Exception as e:
    print(f"  PLAYWRIGHT ERROR: {type(e).__name__}: {e}")

# ============================================================
# 3. AVITO — поиск живых ID через категорию
# ============================================================
print("\n" + "#"*60)
print("# 3. AVITO — LIVE IDS FROM CATEGORY")
print("#"*60)

avito_cat = "https://www.avito.ru/vladivostok/telefony"
r = hit("AVITO CATEGORY", avito_cat, timeout=20)
if r and r.status_code == 200:
    html = r.text
    # ID в href: /vladivostok/.../1234567890
    ids = re.findall(r'avito\.ru/(?:vladivostok|ad)/[^\s"\'<>]*/(\d{6,})', html)
    ids2 = re.findall(r'/ad/(\d{6,})', html)
    all_ids = list(dict.fromkeys(ids + ids2))  # unique, preserve order
    print(f"  Found {len(all_ids)} ad IDs")
    for aid in all_ids[:10]:
        print(f"    {aid}")
    
    # Также пробуем data-атрибуты
    data_ids = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
    if data_ids:
        print(f"  data-ad-id: {data_ids[:10]}")
    
    # JSON-LD на категории
    jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"  JSON-LD: {len(jsonld)}")
    
    # Title
    tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    print(f"  Page title: {(tm.group(1)[:80] if tm else '?')}")

# ============================================================
# 4. FARPOST — поиск живых ID через категорию
# ============================================================
print("\n" + "#"*60)
print("# 4. FARPOST — LIVE IDS FROM CATEGORY")
print("#"*60)

fp_cat = "https://www.farpost.ru/vladivostok/electronics/"
r = hit("FARPOST CATEGORY", fp_cat, timeout=20)
if r and r.status_code == 200:
    html = r.text
    # Farpost URL: /vladivostok/{category}/{slug}-{id}.html
    ids = re.findall(r'farpost\.ru/vladivostok/[^\s"\'<>]*?-(\d{8,})\.html', html)
    ids2 = re.findall(r'/(\d{10,})\.html', html)
    all_ids = list(dict.fromkeys(ids + ids2))
    print(f"  Found {len(all_ids)} bulletin IDs")
    for bid in all_ids[:10]:
        print(f"    {bid}")
    
    # data-атрибуты
    data_ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html)
    if data_ids:
        print(f"  data-bulletin-id: {data_ids[:10]}")
    
    tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    print(f"  Page title: {(tm.group(1)[:80] if tm else '?')}")

# ============================================================
# 5. WB — basket CDN (правильная формула)
# ============================================================
print("\n" + "#"*60)
print("# 5. WB BASKET CDN FIX")
print("#"*60)

lid = "199298871"
# Формула: vol = первые 5 цифр, part = первые 7 цифр
# host = basket-{vol // 100} → 19929//100 = 199 → basket-199 (DNS fail)
# Попробуем: basket-{vol // 1000} → 19 → basket-19 (404)
# Может быть: basket-{vol // 100} но vol другой?
# Или: static-basket-{vol//100}

# Стратегия: пробуем несколько формул
vol = lid[:5]  # 19929
part = lid[:7]  # 1992988

formulas = [
    f"basket-{int(vol)//100}.wbbasket.ru",       # 199
    f"basket-{int(vol)//1000}.wbbasket.ru",      # 19
    f"basket-{int(vol)//10000}.wbbasket.ru",     # 1
    f"static-basket-{int(vol)//100}.wbbasket.ru", # 199
    f"static-basket-{int(vol)//1000}.wbbasket.ru", # 19
]
for host in formulas:
    url = f"https://{host}/vol{vol}/part{part}/{lid}/info/card.json"
    r = hit(f"WB {host}", url, headers={"Accept": "application/json"}, timeout=8)
    if r and r.status_code == 200:
        print(f"  >>> WORKING! {host}")
        data = r.json()
        print(f"  Keys: {list(data.keys())[:10]}")
        break

# WB: пробуем через www.wildberries.ru API
wb_api = f"https://www.wildberries.ru/api/v1/card/{lid}"
hit("WB API v1 card", wb_api, headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}, timeout=10)

# WB: basket через search API
wb_search = f"https://search.wb.ru/exactmatch/ru/common/v9/search?nm={lid}&appType=1&curr=rub&dest=-1257786"
hit("WB SEARCH API", wb_search, headers={"Accept": "application/json"}, timeout=10)

print("\n\nDONE v3.")
