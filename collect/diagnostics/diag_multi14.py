"""diag_multi14.py — 4 новые гипотезы на каждую площадку.
Цель: найти РАБОЧИЙ публичный источник данных (цена + название).
Быстро: ~60-90 сек максимум.
"""
import json, re, time, sys, os, logging
logging.basicConfig(level=logging.WARNING)

RESULTS = {}
T0 = time.monotonic()

def mark(key, ok, detail):
    RESULTS[key] = {"ok": ok, "detail": detail, "s": round(time.monotonic()-T0,1)}
    print(f"[{key}] {'PASS' if ok else 'FAIL'} | {detail}")

def get_cffi():
    from curl_cffi import requests as cr
    return cr

# ============================================================
# FARPOST: найти ЖИВЫЕ объявления через категорию
# ============================================================
def test_farpost():
    cffi = get_cffi()
    H = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
         "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Accept-Language":"ru-RU,ru;q=0.9"}
    
    # 1. Категория: телефоны владивосток
    url_cat = "https://www.farpost.ru/vladivostok/phones/"
    r = cffi.get(url_cat, headers=H, impersonate="chrome", timeout=10)
    if r.status_code != 200:
        mark("FP:1_category", False, f"HTTP={r.status_code} len={len(r.text)}")
        return None
    # Извлечь ссылки на объявления: /bulletin/\d+
    bulletins = re.findall(r'href="(/(?:\w+/)?bulletin/(\d+)[^"]*)"', r.text)
    if not bulletins:
        # альтернативный паттерн
        bulletins = re.findall(r'href="([^"]*?/(\d{8,}))[^"]*"', r.text)
    mark("FP:1_category", len(bulletins)>0, f"HTTP=200 len={len(r.text)} bulletins={len(bulletins)} sample={bulletins[:3]}")
    return bulletins, cffi, H

def test_farpost_bulletin(bulletins_data):
    if not bulletins_data:
        mark("FP:2_bulletin", False, "no bulletins from category")
        return
    bulletins, cffi, H = bulletins_data
    # Берём первое живое объявление
    # bulletins = [(full_path, id), ...]
    for path, bid in bulletins[:5]:
        url = f"https://www.farpost.ru{path}" if path.startswith("/") else path
        r = cffi.get(url, headers=H, impersonate="chrome", timeout=10)
        if r.status_code != 200:
            continue
        html = r.text
        # Ищем цену
        price = None
        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    off = data.get("offers") or {}
                    price = off.get("price")
                    if price: break
            except: pass
        # data-bulletin-price
        if not price:
            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
            if m: price = m.group(1)
        # price в HTML
        if not price:
            m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)₽', html)
            if m: price = m.group(1).replace(" ","")
        # Название
        title = ""
        m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        if m: title = m.group(1).strip()[:100]
        
        if price:
            mark("FP:2_bulletin", True, f"id={bid} url={url} price={price} title={title[:60]} len={len(html)}")
            return
        else:
            mark("FP:2_bulletin", False, f"id={bid} HTTP=200 len={len(html)} no_price title={title[:60]}")
            break  # одно тестовое объявление достаточно

# ============================================================
# WB: card.wb.ru + vol/part + selling с разными заголовками
# ============================================================
def test_wb():
    cffi = get_cffi()
    nm_id = 1097975275
    imt_id = 2227481309
    vol = imt_id // 100000
    part = imt_id % 100000
    
    # 1. card.wb.ru/cards/v2/detail — основной ценовой API
    r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm_id}", 
                 headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/","Origin":"https://www.wildberries.ru","Accept-Language":"ru-RU,ru;q=0.9"},
                 impersonate="chrome", timeout=10)
    if r.status_code == 200:
        try:
            data = r.json()
            products = data.get("data",{}).get("products",[])
            if products:
                p = products[0]
                # price в WB: priceP (копейки) или salePriceU
                price_kop = p.get("priceP") or p.get("salePriceU") or p.get("priceU")
                price = price_kop/100 if price_kop else None
                mark("WB:1_card_v2", True, f"HTTP=200 price={price} name={str(p.get('name',''))[:50]} brand={p.get('brand','')}")
                return
        except Exception as e:
            mark("WB:1_card_v2", False, f"HTTP=200 json_err={e} len={len(r.text)}")
    else:
        mark("WB:1_card_v2", False, f"HTTP={r.status_code} len={len(r.text)}")
    
    # 2. basket CDN n.json (полная карточка с ценой?)
    # Пробуем basket-42 (известно работает) и basket-22
    for bh in ["basket-42","basket-22","basket-09"]:
        url = f"https://{bh}.wbbasket.ru/vol{vol}/part{part}/{imt_id}/n.json"
        r = cffi.get(url, headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"}, impersonate="chrome", timeout=8)
        if r.status_code == 200:
            try:
                d = r.json()
                # Ищем ценовые ключи
                price_keys = [k for k in d.keys() if 'price' in k.lower() or 'cost' in k.lower() or 'sale' in k.lower()]
                price_val = d.get("price") or d.get("salePrice") or d.get("priceP")
                mark("WB:2_basket_n", True, f"host={bh} HTTP=200 price_keys={price_keys} price={price_val} keys={list(d.keys())[:15]}")
                return
            except Exception as e:
                mark("WB:2_basket_n", False, f"host={bh} HTTP=200 err={e}")
        elif r.status_code == 404:
            continue
        else:
            mark("WB:2_basket_n", False, f"host={bh} HTTP={r.status_code}")
            break
    else:
        mark("WB:2_basket_n", False, "all basket hosts 404")
    
    # 3. selling API — уже работает, но может быть price в другом поле
    r = cffi.get(f"https://card.wb.ru/selling/v1/{imt_id}", 
                 headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"}, impersonate="chrome", timeout=8)
    if r.status_code == 200:
        try:
            d = r.json()
            mark("WB:3_selling", True, f"HTTP=200 keys={list(d.keys())[:20]} full={json.dumps(d)[:300]}")
        except:
            mark("WB:3_selling", False, f"HTTP=200 len={len(r.text)}")
    else:
        mark("WB:3_selling", False, f"HTTP={r.status_code}")
    
    # 4. web API card
    r = cffi.get(f"https://www.wildberries.ru/web/api/catalog/v2/card?nm={nm_id}",
                 headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/","X-Requested-With":"XMLHttpRequest"},
                 impersonate="chrome", timeout=8)
    if r.status_code == 200:
        mark("WB:4_web_card", True, f"HTTP=200 len={len(r.text)} head={r.text[:200]}")
    else:
        mark("WB:4_web_card", False, f"HTTP={r.status_code} len={len(r.text)}")

# ============================================================
# OZON: Playwright warm-up + composer API через браузер
# ============================================================
def test_ozon():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width":1920,"height":1080},
        locale="ru-RU",
    )
    page = ctx.new_page()
    
    # 1. Warm-up: главная
    try:
        page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)
    except Exception as e:
        mark("OZ:1_warmup", False, f"warmup_err={e}")
    
    # 2. Карточка товара
    product_url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    try:
        page.goto(product_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(4)
        html = page.content()
        
        # JSON-LD
        price = None
        title = ""
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict):
                    off = d.get("offers") or {}
                    price = off.get("price") or off.get("lowPrice")
                    title = d.get("name","")
                    if price: break
            except: pass
        
        # data-widget / state
        if not price:
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
            if m: price = m.group(1)
        
        # meta og
        if not title:
            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
            if m: title = m.group(1)
        
        status = page.evaluate("() => document.title")
        mark("OZ:2_product", bool(price), f"HTTP_ok title={title[:60]} price={price} page_title={status[:60]} len={len(html)}")
        
        # 3. Composer API через fetch в странице
        if not price:
            try:
                resp = page.evaluate("""
                    async () => {
                        const r = await fetch('/api/composer-api.bx/page/json/v2?url=/product/3481320800/&state=', {
                            headers: {'Accept':'application/json'},
                            credentials: 'include'
                        });
                        return {status: r.status, body: (await r.text()).slice(0,500)};
                    }
                """)
                mark("OZ:3_composer_fetch", resp.get("status")==200, f"status={resp.get('status')} body={resp.get('body','')[:200]}")
            except Exception as e:
                mark("OZ:3_composer_fetch", False, f"err={e}")
    except Exception as e:
        mark("OZ:2_product", False, f"err={e}")
    
    browser.close()
    pw.stop()

# ============================================================
# AVITO: Playwright категория → живые объявления
# ============================================================
def test_avito():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width":1920,"height":1080},
        locale="ru-RU",
    )
    page = ctx.new_page()
    
    # 1. Категория: телефоны владивосток
    cat_url = "https://www.avito.ru/vladivostok/telefony"
    try:
        page.goto(cat_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(4)
        html = page.content()
        
        # Ссылки на объявления: /vladivostok/.../p\d+
        offers = re.findall(r'href="(/vladivostok/[^"]*/(p\d{10,}))"', html)
        if not offers:
            offers = re.findall(r'href="([^"]*?/(p\d{10,}))"', html)
        
        mark("AV:1_category", len(offers)>0, f"len={len(html)} offers={len(offers)} sample={offers[:3]}")
        
        # 2. Первое объявление
        if offers:
            path = offers[0][0] if isinstance(offers[0], tuple) else offers[0]
            offer_url = f"https://www.avito.ru{path}" if path.startswith("/") else path
            page.goto(offer_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            html2 = page.content()
            
            price = None
            m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)', html2)
            if m: price = m.group(1).replace(" ","")
            if not price:
                m = re.search(r'"price"\s*:\s*"?(\d+)"?', html2)
                if m: price = m.group(1)
            if not price:
                m = re.search(r'itemprop="price"[^>]*content="(\d+)"', html2)
                if m: price = m.group(1)
            
            title = page.title()[:80]
            mark("AV:2_offer", bool(price), f"url={offer_url[:60]} price={price} title={title} len={len(html2)}")
        else:
            mark("AV:2_offer", False, "no offers found")
    except Exception as e:
        mark("AV:1_category", False, f"err={e}")
    
    browser.close()
    pw.stop()

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    print(f"=== diag_multi14 START {time.strftime('%H:%M:%S')} ===")
    
    # Farpost
    try:
        bd = test_farpost()
        if bd: test_farpost_bulletin(bd)
    except Exception as e:
        mark("FP:1_category", False, f"EXC={e}")
    
    # WB
    try:
        test_wb()
    except Exception as e:
        mark("WB:1_card_v2", False, f"EXC={e}")
    
    # Ozon
    try:
        test_ozon()
    except Exception as e:
        mark("OZ:1_warmup", False, f"EXC={e}")
    
    # Avito
    try:
        test_avito()
    except Exception as e:
        mark("AV:1_category", False, f"EXC={e}")
    
    RESULTS["total_s"] = round(time.monotonic()-T0,1)
    RESULTS["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    out = os.path.join(os.path.dirname(__file__), "d14_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    
    # Краткий вывод
    print(f"\n=== SUMMARY ({RESULTS['total_s']}s) ===")
    for k,v in RESULTS.items():
        if k in ("total_s","time"): continue
        status = "PASS" if v["ok"] else "FAIL"
        print(f"  {status} {k}: {v['detail'][:100]}")
    print(f"EXIT=0")
