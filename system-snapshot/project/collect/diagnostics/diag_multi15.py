"""diag_multi15.py — исправления по d14.
WB: card.json (не n.json) на basket-42.
FP: главная → категории → объявления.
OZ: networkidle + DOM extraction.
AV: dump HTML для анализа структуры.
"""
import json, re, time, os, logging
logging.basicConfig(level=logging.WARNING)

RESULTS = {}
T0 = time.monotonic()

def mark(key, ok, detail):
    RESULTS[key] = {"ok": ok, "detail": detail, "s": round(time.monotonic()-T0,1)}
    print(f"[{key}] {'PASS' if ok else 'FAIL'} | {detail}")

def get_cffi():
    from curl_cffi import requests as cr
    return cr

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
H_HTML = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.9"}
H_JSON = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9", "Referer": "https://www.wildberries.ru/"}

# ============================================================
# WB: card.json на basket-42 (подтверждено d12) + selling + price
# ============================================================
def test_wb():
    cffi = get_cffi()
    nm_id = 1097975275
    imt_id = 2227481309
    vol = imt_id // 100000
    part = imt_id % 100000

    # 1. card.json на basket-42 (РАБОЧАЯ стратегия из d12)
    url_card = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{imt_id}/card.json"
    r = cffi.get(url_card, headers=H_JSON, impersonate="chrome", timeout=10)
    if r.status_code == 200:
        try:
            d = r.json()
            title = d.get("name", "")
            brand = d.get("brand", "")
            # Ищем ВСЕ ключи с price/cost/sale
            all_keys = list(d.keys())
            price_keys = [k for k in all_keys if any(x in k.lower() for x in ('price','cost','sale','discount'))]
            mark("WB:1_card_json", True, f"HTTP=200 title={title[:50]} brand={brand} price_keys={price_keys} all_keys={all_keys[:20]}")
            # Сохраним полный dump
            with open(os.path.join(os.path.dirname(__file__), "d15_wb_card.json"), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            mark("WB:1_card_json", False, f"HTTP=200 json_err={e} head={r.text[:200]}")
    else:
        mark("WB:1_card_json", False, f"HTTP={r.status_code} len={len(r.text)} url={url_card}")

    # 2. selling v1 — проверить все поля
    url_sell = f"https://card.wb.ru/selling/v1/{imt_id}"
    r = cffi.get(url_sell, headers=H_JSON, impersonate="chrome", timeout=10)
    if r.status_code == 200:
        d = r.json()
        mark("WB:2_selling", True, f"HTTP=200 data={json.dumps(d, ensure_ascii=False)[:400]}")
    else:
        mark("WB:2_selling", False, f"HTTP={r.status_code} len={len(r.text)}")

    # 3. card.wb.ru/cards/v2/detail — с Referer на конкретный товар
    url_cw = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
    r = cffi.get(url_cw, headers={**H_JSON, "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"}, impersonate="chrome", timeout=10)
    if r.status_code == 200:
        d = r.json()
        prods = d.get("data", {}).get("products", [])
        if prods:
            p = prods[0]
            price_keys = {k: p[k] for k in p if 'price' in k.lower() or 'sale' in k.lower() or 'cost' in k.lower()}
            mark("WB:3_cardw_v2", True, f"HTTP=200 n={len(prods)} price_fields={price_keys} name={str(p.get('name',''))[:40]}")
        else:
            mark("WB:3_cardw_v2", False, f"HTTP=200 products=[] head={r.text[:200]}")
    else:
        mark("WB:3_cardw_v2", False, f"HTTP={r.status_code} len={len(r.text)}")

    # 4. basket CDN: s.json (size/variants) — может быть цена
    url_s = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{imt_id}/s.json"
    r = cffi.get(url_s, headers=H_JSON, impersonate="chrome", timeout=8)
    if r.status_code == 200:
        d = r.json()
        mark("WB:4_basket_s", True, f"HTTP=200 keys={list(d.keys())[:15]} sample={json.dumps(d, ensure_ascii=False)[:300]}")
    else:
        mark("WB:4_basket_s", False, f"HTTP={r.status_code} len={len(r.text)}")

# ============================================================
# FARPOST: главная → найти категории → живое объявление
# ============================================================
def test_farpost():
    cffi = get_cffi()

    # 1. Главная Владивостока
    r = cffi.get("https://www.farpost.ru/vladivostok/", headers=H_HTML, impersonate="chrome", timeout=12)
    if r.status_code != 200:
        mark("FP:1_main", False, f"HTTP={r.status_code} len={len(r.text)}")
        return
    html = r.text
    # Найти категории: ссылки вида /vladivostok/{cat}/
    cats = re.findall(r'href="(/vladivostok/([a-z\-]+)/?)"', html)
    # Уникальные категории
    seen = set()
    cat_list = []
    for path, cat in cats:
        if cat not in seen and cat not in ('bulletin','user','search','company'):
            seen.add(cat)
            cat_list.append(cat)
    mark("FP:1_main", True, f"HTTP=200 len={len(html)} cats={cat_list[:15]}")

    # 2. Категория "telefony" или "phones" или "elektronika"
    target_cat = None
    for c in cat_list:
        if any(x in c for x in ('telefon','phone','elektron','gadget','mobil')):
            target_cat = c
            break
    if not target_cat and cat_list:
        target_cat = cat_list[0]  # любая категория
    
    if target_cat:
        url_cat = f"https://www.farpost.ru/vladivostok/{target_cat}/"
        r = cffi.get(url_cat, headers=H_HTML, impersonate="chrome", timeout=12)
        if r.status_code == 200:
            html = r.text
            # Объявления: /vladivostok/{cat}/{slug}-{id}.html или /bulletin/{id}
            anns = re.findall(r'href="(/vladivostok/[^"]*?/([a-z0-9\-]+?)(\d{6,})\.html)"', html)
            if not anns:
                anns = re.findall(r'href="(/bulletin/(\d+))"', html)
            if not anns:
                anns = re.findall(r'href="([^"]*?(\d{8,})\.html)"', html)
            mark("FP:2_category", len(anns)>0, f"cat={target_cat} HTTP=200 len={len(html)} anns={len(anns)} sample={anns[:3]}")
            
            # 3. Первое живое объявление
            if anns:
                if len(anns[0]) == 3:
                    ann_url = f"https://www.farpost.ru{anns[0][0]}"
                    ann_id = anns[0][2]
                else:
                    ann_url = f"https://www.farpost.ru{anns[0][0]}"
                    ann_id = anns[0][1]
                r = cffi.get(ann_url, headers=H_HTML, impersonate="chrome", timeout=12)
                if r.status_code == 200:
                    html2 = r.text
                    # Цена: JSON-LD, data-bulletin-price, span.price
                    price = None
                    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html2, re.DOTALL):
                        try:
                            d = json.loads(m.group(1))
                            if isinstance(d, dict):
                                off = d.get("offers") or {}
                                price = off.get("price") or off.get("lowPrice")
                                if price: break
                        except: pass
                    if not price:
                        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html2)
                        if m: price = m.group(1)
                    if not price:
                        m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)', html2)
                        if m: price = m.group(1).replace(" ","")
                    # Название
                    title = ""
                    m = re.search(r'<title>(.*?)</title>', html2, re.DOTALL)
                    if m: title = m.group(1).strip()[:80]
                    mark("FP:3_ann", bool(price), f"id={ann_id} url={ann_url[:60]} price={price} title={title} len={len(html2)}")
                else:
                    mark("FP:3_ann", False, f"id={ann_id} HTTP={r.status_code} len={len(r.text)}")
        else:
            mark("FP:2_category", False, f"cat={target_cat} HTTP={r.status_code} len={len(r.text)}")
    else:
        mark("FP:2_category", False, "no categories found")

# ============================================================
# OZON: Playwright networkidle + DOM
# ============================================================
def test_ozon():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent=UA, viewport={"width":1920,"height":1080}, locale="ru-RU",
    )
    # Anti-detect
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru']});
    """)
    page = ctx.new_page()

    product_url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    
    try:
        page.goto(product_url, wait_until="networkidle", timeout=30000)
        time.sleep(3)
        html = page.content()
        ptitle = page.title()
        
        # DOM: цена
        price = None
        try:
            price_el = page.query_selector('[data-widget*="Price"] [data-widget*="Value"], .price-value, [class*="price"] [class*="value"]')
            if price_el:
                price = price_el.inner_text().strip()
        except: pass
        
        # JSON-LD из HTML
        if not price:
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    d = json.loads(m.group(1))
                    if isinstance(d, dict):
                        off = d.get("offers") or {}
                        price = off.get("price") or off.get("lowPrice")
                        if price: break
                except: pass
        
        # data-widget state
        if not price:
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
            if m: price = m.group(1)
        
        # Title
        title = ""
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1)
        if not title:
            m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
            if m: title = m.group(1).strip()
        
        mark("OZ:1_product", bool(price), f"price={price} title={title[:60]} page_title={ptitle[:60]} len={len(html)}")
        
        # Dump для анализа
        with open(os.path.join(os.path.dirname(__file__), "d15_ozon.html"), "w", encoding="utf-8") as f:
            f.write(html[:50000])
        
    except Exception as e:
        mark("OZ:1_product", False, f"err={e}")
    
    browser.close()
    pw.stop()

# ============================================================
# AVITO: Playwright + dump HTML для анализа
# ============================================================
def test_avito():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent=UA, viewport={"width":1920,"height":1080}, locale="ru-RU",
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    """)
    page = ctx.new_page()

    cat_url = "https://www.avito.ru/vladivostok/telefony"
    try:
        page.goto(cat_url, wait_until="networkidle", timeout=30000)
        time.sleep(4)
        html = page.content()
        ptitle = page.title()
        
        # Dump
        with open(os.path.join(os.path.dirname(__file__), "d15_avito.html"), "w", encoding="utf-8") as f:
            f.write(html[:80000])
        
        # Паттерны для объявлений Avito
        offers = re.findall(r'href="(/vladivostok/[^"]*?/p(\d+))"', html)
        if not offers:
            offers = re.findall(r'href="([^"]*?/p(\d{10,}))"', html)
        if not offers:
            # data-атрибуты
            offers = re.findall(r'data-offer-id="(\d+)"', html)
            offers = [(f"/vladivostok/x/p{o}", o) for o in offers]
        
        mark("AV:1_category", len(offers)>0, f"len={len(html)} offers={len(offers)} sample={offers[:3]} title={ptitle[:50]}")
        
        # Первое объявление
        if offers:
            path = offers[0][0]
            offer_url = f"https://www.avito.ru{path}"
            page.goto(offer_url, wait_until="networkidle", timeout=25000)
            time.sleep(3)
            html2 = page.content()
            
            price = None
            # JSON-LD
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html2, re.DOTALL):
                try:
                    d = json.loads(m.group(1))
                    if isinstance(d, dict):
                        off = d.get("offers") or {}
                        price = off.get("price") or off.get("lowPrice")
                        if price: break
                except: pass
            # itemprop
            if not price:
                m = re.search(r'itemprop="price"[^>]*content="(\d+)"', html2)
                if m: price = m.group(1)
            # class price
            if not price:
                m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)', html2)
                if m: price = m.group(1).replace(" ","")
            
            title = page.title()[:80]
            mark("AV:2_offer", bool(price), f"url={offer_url[:60]} price={price} title={title} len={len(html2)}")
            
            with open(os.path.join(os.path.dirname(__file__), "d15_avito_offer.html"), "w", encoding="utf-8") as f:
                f.write(html2[:50000])
    except Exception as e:
        mark("AV:1_category", False, f"err={e}")
    
    browser.close()
    pw.stop()

# ============================================================
if __name__ == "__main__":
    print(f"=== diag_multi15 START {time.strftime('%H:%M:%S')} ===")
    
    for name, fn in [("WB", test_wb), ("FP", test_farpost), ("OZ", test_ozon), ("AV", test_avito)]:
        try:
            fn()
        except Exception as e:
            mark(f"{name}:EXC", False, f"{type(e).__name__}: {e}")
    
    RESULTS["total_s"] = round(time.monotonic()-T0,1)
    RESULTS["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    out = os.path.join(os.path.dirname(__file__), "d15_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    
    print(f"\n=== SUMMARY ({RESULTS['total_s']}s) ===")
    for k,v in RESULTS.items():
        if k in ("total_s","time"): continue
        print(f"  {'PASS' if v['ok'] else 'FAIL'} {k}: {v['detail'][:120]}")
    print("EXIT=0")
