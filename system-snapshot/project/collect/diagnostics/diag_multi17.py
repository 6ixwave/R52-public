"""diag_multi17.py — d17.
WB: HTML-страница → discovery basket host + retry basket-42 + другой товар.
OZ: channel=chrome (реальный Chrome) + curl_cffi SSR.
AV: 10s delay + search API.
FP: 3 объявления для стабильности.
"""
import json, re, time, os, logging
logging.basicConfig(level=logging.WARNING)

RESULTS = {}
T0 = time.monotonic()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
H_HTML = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.9"}
H_JSON = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9", "Referer": "https://www.wildberries.ru/"}

def mark(key, ok, detail):
    RESULTS[key] = {"ok": ok, "detail": detail, "s": round(time.monotonic()-T0,1)}
    print(f"[{key}] {'PASS' if ok else 'FAIL'} | {detail}")

def get_cffi():
    from curl_cffi import requests as cr
    return cr

# ============================================================
# WB: 3 стратегии
# ============================================================
def test_wb():
    cffi = get_cffi()
    nm_id = 1097975275
    imt_id = 2227481309
    vol = imt_id // 100000
    part = imt_id % 100000

    # 1. HTML-страница (detail.aspx) → ищем basket host в скриптах
    url_html = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    r = cffi.get(url_html, headers=H_HTML, impersonate="chrome", timeout=12)
    if r.status_code == 200:
        html = r.text
        # Ищем basket-хост: basket-XX.wbbasket.ru
        hosts = set(re.findall(r'(basket-\d+\.wbbasket\.ru)', html))
        # Ищем vol/part
        vol_match = re.search(r'vol(\d+)/part(\d+)', html)
        mark("WB:1_html", True, f"HTTP=200 len={len(html)} hosts={hosts} vol_match={vol_match.groups() if vol_match else None}")
        
        # 2. Если нашли хост — пробуем card.json
        if hosts:
            bh = list(hosts)[0]
            v, p = vol_match.groups() if vol_match else (vol, part)
            url_card = f"https://{bh}/vol{v}/part{p}/{imt_id}/card.json"
            r2 = cffi.get(url_card, headers=H_JSON, impersonate="chrome", timeout=8)
            if r2.status_code == 200:
                d = r2.json()
                pk = [k for k in d.keys() if any(x in k.lower() for x in ('price','cost','sale'))]
                mark("WB:2_discovered", True, f"host={bh} vol={v} part={p} price_keys={pk} nkeys={len(d.keys())}")
                with open(os.path.join(os.path.dirname(__file__), "d17_wb_card.json"), "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
            else:
                mark("WB:2_discovered", False, f"host={bh} HTTP={r2.status_code} len={len(r2.text)}")
        else:
            mark("WB:2_discovered", False, "no basket host in HTML")
    else:
        mark("WB:1_html", False, f"HTTP={r.status_code} len={len(r.text)}")
        # Fallback: basket-42 retry
        url_card = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{imt_id}/card.json"
        r2 = cffi.get(url_card, headers=H_JSON, impersonate="chrome", timeout=8)
        mark("WB:2_b42_retry", r2.status_code==200, f"HTTP={r2.status_code} len={len(r2.text)}")

    # 3. Другой товар (iPhone 15, nm=1968915452) — проверить не product-specific
    nm2 = 1968915452
    # Найти imt_id через card.wb.ru
    r3 = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm2}",
                  headers={**H_JSON, "Referer": f"https://www.wildberries.ru/catalog/{nm2}/detail.aspx"},
                  impersonate="chrome", timeout=8)
    if r3.status_code == 200:
        d3 = r3.json()
        prods = d3.get("data",{}).get("products",[])
        if prods:
            p3 = prods[0]
            imt2 = p3.get("imt", 0)
            vol2 = imt2 // 100000
            part2 = imt2 % 100000
            pk3 = {k:v for k,v in p3.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:3_other_nm", True, f"nm={nm2} imt={imt2} price={pk3} name={str(p3.get('name',''))[:40]}")
        else:
            mark("WB:3_other_nm", False, f"HTTP=200 products=[] head={r3.text[:150]}")
    else:
        mark("WB:3_other_nm", False, f"HTTP={r3.status_code} len={len(r3.text)}")

    # 4. basket-42 card.json retry (был 200 в d12)
    time.sleep(3)
    url_b42 = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{imt_id}/card.json"
    r4 = cffi.get(url_b42, headers=H_JSON, impersonate="chrome", timeout=8)
    if r4.status_code == 200:
        d4 = r4.json()
        mark("WB:4_b42_retry", True, f"HTTP=200 nkeys={len(d4.keys())} name={d4.get('name','')[:40]}")
    else:
        mark("WB:4_b42_retry", False, f"HTTP={r4.status_code} len={len(r4.text)} url={url_b42}")

# ============================================================
# OZON: channel=chrome (реальный Chrome, не headless-shell)
# ============================================================
def test_ozon():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    
    # Попробовать с channel="chrome" — использует установленный Chrome
    try:
        browser = pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled","--disable-dev-shm-usage"]
        )
        src = "chrome_channel"
    except Exception as e:
        # Fallback: обычный chromium
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage","--window-size=1920,1080"]
        )
        src = "chromium_fallback"
    
    ctx = browser.new_context(
        user_agent=UA, viewport={"width":1920,"height":1080}, locale="ru-RU",
        extra_http_headers={"Accept-Language":"ru-RU,ru;q=0.9","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});
        Object.defineProperty(navigator,'plugins',{get:()=>[{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}]});
        window.chrome={runtime:{onMessage:{addListener:()=>{}}}};
    """)
    page = ctx.new_page()
    
    product_url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    
    try:
        # Warm-up
        page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)
        
        # Product
        page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        html = page.content()
        ptitle = page.title()
        
        # Проверка: "нет соединения"?
        no_conn = "нет соединения" in ptitle.lower() or "no connection" in ptitle.lower()
        
        # Price: DOM
        price = None
        try:
            for sel in ['[data-widget*="Price"] [data-widget*="Value"]', '.tilePrice span', '[class*="price"] [class*="value"]', '[data-widget="Price"] span', '.price-value']:
                el = page.query_selector(sel)
                if el:
                    txt = el.inner_text().strip()
                    if txt and any(c.isdigit() for c in txt):
                        price = txt
                        break
        except: pass
        
        # JSON-LD
        if not price:
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                try:
                    d = json.loads(m.group(1))
                    if isinstance(d, dict):
                        off = d.get("offers") or {}
                        price = off.get("price") or off.get("lowPrice")
                        if price: break
                except: pass
        
        # state / data-widget
        if not price:
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
            if m: price = m.group(1)
        
        title = ""
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1)
        
        mark("OZ:1_product", bool(price), f"src={src} price={price} title={title[:50]} ptitle={ptitle[:50]} no_conn={no_conn} len={len(html)}")
        
        # Dump если мало
        if len(html) < 8000:
            with open(os.path.join(os.path.dirname(__file__), "d17_ozon.html"), "w", encoding="utf-8") as f:
                f.write(html)
    except Exception as e:
        mark("OZ:1_product", False, f"src={src} err={str(e)[:100]}")
    
    browser.close()
    pw.stop()

# ============================================================
# AVITO: search API + delay
# ============================================================
def test_avito():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=["--no-sandbox","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage"]
    )
    ctx = browser.new_context(
        user_agent=UA, viewport={"width":1920,"height":1080}, locale="ru-RU",
    )
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.new_page()
    
    # 1. Search API (JSON)
    try:
        # Avito search API
        api_url = "https://www.avito.ru/api/search?query=iphone&city=2&limit=5"
        resp = page.evaluate("""
            async (url) => {
                try {
                    const r = await fetch(url, {headers:{'Accept':'application/json'}});
                    return {status: r.status, body: (await r.text()).slice(0,500)};
                } catch(e) { return {status:0, body: e.message}; }
            }
        """, api_url)
        mark("AV:1_search_api", resp["status"]==200, f"status={resp['status']} body={resp['body'][:200]}")
    except Exception as e:
        mark("AV:1_search_api", False, f"err={str(e)[:80]}")
    
    # 2. Главная с delay
    time.sleep(5)
    try:
        page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=25000)
        time.sleep(5)
        html = page.content()
        ptitle = page.title()
        ip_blocked = "IP" in ptitle or "ограничен" in ptitle.lower()
        mark("AV:2_main", not ip_blocked, f"len={len(html)} ptitle={ptitle[:60]} ip_blocked={ip_blocked}")
        
        if not ip_blocked:
            # Навигация в категорию
            page.goto("https://www.avito.ru/vladivostok/telefony", wait_until="domcontentloaded", timeout=25000)
            time.sleep(5)
            html2 = page.content()
            offers = re.findall(r'href="(/vladivostok/[^"]*?/p(\d{10,}))"', html2)
            mark("AV:3_category", len(offers)>0, f"len={len(html2)} offers={len(offers)} sample={offers[:2]}")
    except Exception as e:
        mark("AV:2_main", False, f"err={str(e)[:80]}")
    
    browser.close()
    pw.stop()

# ============================================================
# FARPOST: 3 объявления для стабильности
# ============================================================
def test_farpost():
    cffi = get_cffi()
    
    # Категория: auto (подтверждена d15)
    url_cat = "https://www.farpost.ru/vladivostok/auto/"
    r = cffi.get(url_cat, headers=H_HTML, impersonate="chrome", timeout=12)
    if r.status_code != 200:
        mark("FP:1_cat", False, f"HTTP={r.status_code}")
        return
    html = r.text
    anns = re.findall(r'href="(/vladivostok/[^"]*?/([a-z0-9\-]+?)(\d{6,})\.html)"', html)
    if not anns:
        mark("FP:1_cat", False, f"HTTP=200 len={len(html)} anns=0")
        return
    mark("FP:1_cat", True, f"HTTP=200 anns={len(anns)}")
    
    # 3 разных объявления
    success = 0
    for i, (path, slug, bid) in enumerate(anns[:3]):
        url = f"https://www.farpost.ru{path}"
        r = cffi.get(url, headers=H_HTML, impersonate="chrome", timeout=10)
        if r.status_code != 200:
            continue
        h = r.text
        price = None
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', h, re.DOTALL):
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict):
                    off = d.get("offers") or {}
                    price = off.get("price") or off.get("lowPrice")
                    if price: break
            except: pass
        if not price:
            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', h)
            if m: price = int(m.group(1))
        title = ""
        m = re.search(r'<title>(.*?)</title>', h, re.DOTALL)
        if m: title = m.group(1).strip()[:60]
        
        if price:
            success += 1
            mark(f"FP:2_ann{i}", True, f"id={bid} price={price} title={title[:40]}")
        time.sleep(1)
    
    mark("FP:3_stability", success >= 2, f"success={success}/3")

# ============================================================
if __name__ == "__main__":
    print(f"=== diag_multi17 START {time.strftime('%H:%M:%S')} ===")
    for name, fn in [("WB", test_wb), ("FP", test_farpost), ("OZ", test_ozon), ("AV", test_avito)]:
        try:
            fn()
        except Exception as e:
            mark(f"{name}:EXC", False, f"{type(e).__name__}: {str(e)[:100]}")
    
    RESULTS["total_s"] = round(time.monotonic()-T0,1)
    RESULTS["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = os.path.join(os.path.dirname(__file__), "d17_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"\n=== SUMMARY ({RESULTS['total_s']}s) ===")
    for k,v in RESULTS.items():
        if k in ("total_s","time"): continue
        print(f"  {'PASS' if v['ok'] else 'FAIL'} {k}: {v['detail'][:120]}")
    print("EXIT=0")
