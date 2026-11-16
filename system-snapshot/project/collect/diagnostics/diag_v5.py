"""DIAG v5: WB цена (3 способа) + OZON Playwright + поиск живых AVITO/FARPOST."""
import json, time, sys, re, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from curl_cffi import requests as cffi_req

def get(url, **kw):
    kw.setdefault("impersonate", "chrome")
    kw.setdefault("timeout", 20)
    return cffi_req.get(url, **kw)

t0 = time.monotonic()
print("DIAG v5 START", flush=True)
WORK = ROOT / "e2e-final-work"
WORK.mkdir(exist_ok=True)

# ============ WB: 3 способа цены ============
print("\n" + "=" * 60, flush=True)
print("[WB] nm_id=1097975275 — поиск цены", flush=True)
nm_id = 1097975275
vol = nm_id // 1000
part = (nm_id // 100) % 10000

wb_price_found = False

# Способ 1: card.wb.ru API (curl_cffi chrome)
print("\n  [WB-1] card.wb.ru/cards/v2/detail", flush=True)
try:
    r = get(f"https://card.wb.ru/cards/v2/detail?nm={nm_id}")
    print(f"  status={r.status_code} len={len(r.text)}", flush=True)
    if r.status_code == 200:
        data = r.json()
        prods = data.get("data", {}).get("products", [])
        if prods:
            p = prods[0]
            print(f"  name: {p.get('name','')[:80]}", flush=True)
            print(f"  priceU: {p.get('priceU')}", flush=True)
            print(f"  priceD: {p.get('priceD')}", flush=True)
            sizes = p.get("sizes", [])
            if sizes:
                sp = sizes[0].get("price", {})
                print(f"  sizes[0].price: {json.dumps(sp, ensure_ascii=False)[:300]}", flush=True)
                for k in ("basic","product","card","wallet","old","crossed"):
                    if k in sp and isinstance(sp[k], (int,float)) and sp[k] > 0:
                        print(f"  FOUND price: {k}={sp[k]}", flush=True)
                        wb_price_found = True
            # Сохраняем
            (WORK / "wb_api_v2.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"  data: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
    else:
        print(f"  body: {r.text[:200]}", flush=True)
except Exception as e:
    print(f"  ERR: {e}", flush=True)

# Способ 2: basket-42 card.json (повторный, может быть транзиентный 404)
print("\n  [WB-2] basket-42 card.json (retry)", flush=True)
try:
    url = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm_id}/card.json"
    r = get(url)
    print(f"  status={r.status_code} len={len(r.text)}", flush=True)
    if r.status_code == 200:
        data = r.json()
        print(f"  top keys: {list(data.keys())[:20]}", flush=True)
        # Рекурсивный поиск цен
        def find_prices(obj, path=""):
            found = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("price","priceU","priceD","oldPrice","basic","product","card","wallet","old","crossed","bankCard") and isinstance(v,(int,float)) and v > 100:
                        found.append(f"{path}.{k}={v}")
                    elif isinstance(v,(dict,list)):
                        found.extend(find_prices(v, f"{path}.{k}"))
            elif isinstance(obj, list):
                for i, v in enumerate(obj[:10]):
                    found.extend(find_prices(v, f"{path}[{i}]"))
            return found
        prices = find_prices(data)
        print(f"  prices found: {len(prices)}", flush=True)
        for p in prices[:15]:
            print(f"    {p}", flush=True)
        if prices:
            wb_price_found = True
        (WORK / "wb_card_full.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"  body: {r.text[:200]}", flush=True)
except Exception as e:
    print(f"  ERR: {e}", flush=True)

# Способ 3: HTML detail.aspx
print("\n  [WB-3] HTML detail.aspx", flush=True)
try:
    r = get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx")
    print(f"  status={r.status_code} len={len(r.text)}", flush=True)
    html = r.text
    # Ищем цену в HTML
    price_patterns = [
        r'data-auto="price-value"[^>]*>([\d\s]+)',
        r'class="c-price__value"[^>]*>([\d\s]+)',
        r'"price"\s*:\s*(\d+)',
        r'"priceU"\s*:\s*(\d+)',
        r'"priceD"\s*:\s*(\d+)',
        r'"sizes"\s*:\s*\[.*?"price"\s*:\s*\{([^}]+)\}',
    ]
    for pat in price_patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            print(f"  pattern '{pat[:40]}...': {m.group(1)[:100]}", flush=True)
            wb_price_found = True
    # JSON-LD
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            ld = json.loads(m.group(1).strip())
            if isinstance(ld, dict) and ld.get("@type") == "Product":
                offers = ld.get("offers", {})
                print(f"  JSON-LD Product offers: {json.dumps(offers, ensure_ascii=False)[:200]}", flush=True)
                if offers.get("price"):
                    wb_price_found = True
        except Exception:
            pass
    # __NEXT_DATA__ или state
    sm = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;', html, re.DOTALL)
    if sm:
        print(f"  __INITIAL_STATE__ len={len(sm.group(1))}", flush=True)
    (WORK / "wb_detail.html").write_text(html[:100000], encoding="utf-8")
    print(f"  saved wb_detail.html", flush=True)
except Exception as e:
    print(f"  ERR: {e}", flush=True)

print(f"\n  WB PRICE FOUND: {wb_price_found}", flush=True)

# ============ OZON: Playwright ============
print("\n" + "=" * 60, flush=True)
print("[OZON] Playwright fetch", flush=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        # Пробуем несколько URL
        ozon_urls = [
            "https://www.ozon.ru/product/562267/",
            "https://www.ozon.ru/product/1705353494/",
        ]
        for url in ozon_urls:
            tag = url.split("/")[-2]
            try:
                print(f"\n  [ozon_pw_{tag}] {url}", flush=True)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                html = page.content()
                print(f"  html len={len(html)}", flush=True)
                markers = {
                    "json_ld": 'application/ld+json' in html,
                    "og_title": 'og:title' in html,
                    "widget": 'data-widget' in html,
                    "h1": '<h1' in html.lower(),
                    "price": bool(re.search(r'[\d\s]{3,}\s*[₽руб]', html)),
                    "404": 'Такой страницы' in html or 'не существует' in html.lower()[:3000],
                    "antibot": 'Antibot' in html[:3000] or 'antibot' in html[:3000],
                }
                print(f"  markers: {markers}", flush=True)
                # Title
                tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
                if tm:
                    print(f"  <title>: {tm.group(1).strip()[:200]}", flush=True)
                # h1
                hm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
                if hm:
                    print(f"  <h1>: {re.sub(r'<[^>]+>','',hm.group(1)).strip()[:200]}", flush=True)
                # JSON-LD
                for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
                    try:
                        ld = json.loads(m.group(1).strip())
                        if isinstance(ld, dict) and ld.get("@type") == "Product":
                            print(f"  JSON-LD: name={ld.get('name','')[:80]}", flush=True)
                            offers = ld.get("offers", {})
                            print(f"  offers: {json.dumps(offers, ensure_ascii=False)[:300]}", flush=True)
                    except Exception:
                        pass
                # og
                for m in re.finditer(r'property=["\']og:(\w+)["\'][^>]*content=["\']([^"\']*)["\']', html):
                    print(f"  og:{m.group(1)} = {m.group(2)[:120]}", flush=True)
                # Price from HTML
                pm = re.findall(r'([\d\s]{3,})\s*(?:₽|руб)', html)
                if pm:
                    print(f"  price hints: {pm[:5]}", flush=True)
                (WORK / f"ozon_pw_{tag}.html").write_text(html[:100000], encoding="utf-8")
                print(f"  saved ozon_pw_{tag}.html", flush=True)
            except Exception as e:
                print(f"  ERR: {e}", flush=True)
        browser.close()
except Exception as e:
    print(f"  PLAYWRIGHT ERR: {e}", flush=True)

# ============ AVITO: поиск живых ============
print("\n" + "=" * 60, flush=True)
print("[AVITO] Поиск живых объявлений", flush=True)
avito_urls = [
    "https://www.avito.ru/",
    "https://www.avito.ru/vladivostok",
    "https://www.avito.ru/vladivostok/telefony-i-gadzhety",
]
for url in avito_urls:
    tag = url.rstrip("/").split("/")[-1] or "home"
    try:
        r = get(url)
        text = r.text
        print(f"\n  [avito_{tag}] {url}", flush=True)
        print(f"  status={r.status_code} len={len(text)}", flush=True)
        # Ищем ссылки на объявления
        ad_ids = re.findall(r'href="[^"]*?/(\d{7,12})["\?]', text)
        if not ad_ids:
            ad_ids = re.findall(r'/ad/(\d+)', text)
        if not ad_ids:
            ad_ids = re.findall(r'"id":(\d{7,12})', text)
        print(f"  ad IDs: {len(ad_ids)}, first 5: {ad_ids[:5]}", flush=True)
        # Title
        tm = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
        if tm:
            print(f"  <title>: {tm.group(1).strip()[:150]}", flush=True)
        # JSON-LD (для списка)
        jld_count = len(re.findall(r'application/ld\+json', text))
        print(f"  JSON-LD blocks: {jld_count}", flush=True)
        (WORK / f"avito_{tag}.html").write_text(text[:80000], encoding="utf-8")
    except Exception as e:
        print(f"  ERR: {e}", flush=True)

# ============ FARPOST: поиск живых ============
print("\n" + "=" * 60, flush=True)
print("[FARPOST] Поиск живых объявлений", flush=True)
farpost_urls = [
    "https://www.farpost.ru/vladivostok/",
    "https://www.farpost.ru/",
    "https://www.farpost.ru/vladivostok/telefony-i-gadzhety/",
]
for url in farpost_urls:
    tag = url.rstrip("/").split("/")[-1] or "home"
    try:
        r = get(url)
        ct = r.headers.get("content-type", "")
        if 'windows-1251' in ct.lower():
            text = r.content.decode('windows-1251', errors='replace')
        else:
            text = r.text
        print(f"\n  [farpost_{tag}] {url}", flush=True)
        print(f"  status={r.status_code} ct={ct} len={len(text)}", flush=True)
        # Ищем ссылки на объявления: /vladivostok/NNNN.html
        links = re.findall(r'href="(/vladivostok/\d+\.html)"', text)
        if not links:
            links = re.findall(r'href="([^"]*?\d{6,}\.html)"', text)
        print(f"  listing links: {len(links)}, first 5: {links[:5]}", flush=True)
        # Title
        tm = re.search(r'<title[^>]*>(.*?)</title>', text, re.DOTALL)
        if tm:
            print(f"  <title>: {tm.group(1).strip()[:150]}", flush=True)
        # og
        for m in re.finditer(r'property=["\']og:(\w+)["\'][^>]*content=["\']([^"\']*)["\']', text):
            print(f"  og:{m.group(1)} = {m.group(2)[:100]}", flush=True)
        (WORK / f"farpost_{tag}.html").write_text(text[:80000], encoding="utf-8")
    except Exception as e:
        print(f"  ERR: {e}", flush=True)

print(f"\nDIAG v5 DONE {time.monotonic()-t0:.1f}s", flush=True)
