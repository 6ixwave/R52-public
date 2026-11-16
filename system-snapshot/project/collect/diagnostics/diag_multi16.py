"""diag_multi16.py — d16.
FP: полное извлечение + конкретные объявления 138859452/138952176.
WB: sweep basket 01-30 + card.wb.ru v1.
OZ: PW --no-sandbox + warm-up 2 pages.
AV: m.avito.ru + IP-block check.
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
# FARPOST: полное извлечение + конкретные ID
# ============================================================
def test_farpost():
    cffi = get_cffi()
    
    # 1. Конкретные объявления (были 404 раньше — пробуем через /bulletin/ и /vladivostok/...)
    for bid in ["138859452", "138952176"]:
        urls = [
            f"https://www.farpost.ru/bulletin/{bid}",
            f"https://www.farpost.ru/vladivostok/bulletin/{bid}",
            f"https://www.farpost.ru/{bid}.h",
        ]
        found = False
        for u in urls:
            try:
                r = cffi.get(u, headers=H_HTML, impersonate="chrome", timeout=8, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > 5000:
                    html = r.text
                    price = None
                    title = ""
                    # JSON-LD
                    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
                        try:
                            d = json.loads(m.group(1))
                            if isinstance(d, dict):
                                off = d.get("offers") or {}
                                price = off.get("price") or off.get("lowPrice")
                                title = d.get("name", "")
                                if price: break
                        except: pass
                    if not price:
                        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
                        if m: price = m.group(1)
                    if not title:
                        m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                        if m: title = m.group(1).strip()[:80]
                    mark(f"FP:1_id_{bid}", bool(price), f"url={u[:60]} price={price} title={title[:50]} len={len(html)}")
                    found = True
                    break
            except Exception as e:
                continue
        if not found:
            mark(f"FP:1_id_{bid}", False, f"all urls failed for {bid}")
    
    # 2. Полное извлечение из живого объявления (auto category, id=134693091)
    url = "https://www.farpost.ru/vladivostok/auto/misc/vskrytie-avto-bez-kljucha-otkryt-mashinu-vskryt-avtomobil-bagazhnik-24ch-134693091.html"
    r = cffi.get(url, headers=H_HTML, impersonate="chrome", timeout=12)
    if r.status_code == 200:
        html = r.text
        data = {"id": "134693091", "url": url, "len": len(html)}
        
        # Title
        m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        data["title"] = m.group(1).strip()[:120] if m else ""
        
        # Price: JSON-LD
        price = None
        jsonld_data = None
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict):
                    off = d.get("offers") or {}
                    p = off.get("price") or off.get("lowPrice")
                    if p:
                        price = p
                        jsonld_data = d
                        break
                    # @graph
                    if "@graph" in d:
                        for node in d["@graph"]:
                            if isinstance(node, dict) and node.get("@type") in ("Product","Offer"):
                                off2 = node.get("offers") or {}
                                p2 = off2.get("price") or off2.get("lowPrice")
                                if p2:
                                    price = p2
                                    jsonld_data = node
                                    break
            except: pass
        data["price_jsonld"] = price
        
        # Price: data-bulletin-price
        if not price:
            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
            if m: price = int(m.group(1))
        data["price_final"] = price
        
        # Brand / model
        m = re.search(r'data-field="model~[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        data["model"] = m.group(1).strip()[:80] if m else ""
        
        # Description
        m = re.search(r'<div\s+class="bulletinText[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        data["desc_len"] = len(m.group(1)) if m else 0
        
        # Images
        imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*static\.baza\.farpost\.ru/[^"\']+)["\']', html)
        data["images"] = len(set(imgs))
        
        # Specs
        specs = re.findall(r'<div\s+class="label">([^<]+)</div>\s*<div\s+class="value">\s*<span[^>]*>(.*?)</span>', html, re.DOTALL)
        data["specs"] = {k.strip(): v.strip()[:50] for k,v in specs[:10]}
        
        # Seller
        m = re.search(r'class="company-profile[^"]*"[^>]*>.*?>([^<]+)<', html, re.DOTALL)
        data["seller"] = m.group(1).strip()[:50] if m else ""
        
        mark("FP:2_full_extract", bool(price), f"price={price} title={data['title'][:50]} imgs={data['images']} specs={len(data['specs'])} desc={data['desc_len']}b")
        
        # Dump
        with open(os.path.join(os.path.dirname(__file__), "d16_fp_full.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        mark("FP:2_full_extract", False, f"HTTP={r.status_code}")

# ============================================================
# WB: sweep basket hosts 01-30 + card.wb.ru v1
# ============================================================
def test_wb():
    cffi = get_cffi()
    nm_id = 1097975275
    imt_id = 2227481309
    vol = imt_id // 100000
    part = imt_id % 100000
    
    # 1. Sweep basket hosts 01-30 for card.json
    found_host = None
    for h in range(1, 31):
        bh = f"basket-{h:02d}"
        url = f"https://{bh}.wbbasket.ru/vol{vol}/part{part}/{imt_id}/card.json"
        try:
            r = cffi.get(url, headers=H_JSON, impersonate="chrome", timeout=5)
            if r.status_code == 200:
                d = r.json()
                title = d.get("name","")[:50]
                price_keys = [k for k in d.keys() if any(x in k.lower() for x in ('price','cost','sale'))]
                found_host = bh
                mark("WB:1_sweep", True, f"host={bh} title={title} price_keys={price_keys} nkeys={len(d.keys())}")
                with open(os.path.join(os.path.dirname(__file__), "d16_wb_card.json"), "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                break
        except:
            continue
    if not found_host:
        mark("WB:1_sweep", False, "hosts 01-30 all 404/err")
    
    # 2. card.wb.ru/cards/v1/detail (v1!)
    r = cffi.get(f"https://card.wb.ru/cards/v1/detail?nm={nm_id}",
                 headers={**H_JSON, "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"},
                 impersonate="chrome", timeout=8)
    if r.status_code == 200:
        d = r.json()
        prods = d.get("data",{}).get("products",[])
        if prods:
            p = prods[0]
            pk = {k:v for k,v in p.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:2_v1_detail", True, f"n={len(prods)} price={pk} name={str(p.get('name',''))[:40]}")
        else:
            mark("WB:2_v1_detail", False, f"HTTP=200 products=[] head={r.text[:150]}")
    else:
        mark("WB:2_v1_detail", False, f"HTTP={r.status_code} len={len(r.text)}")
    
    # 3. card.wb.ru/cards/v2/detail (retry with delay)
    time.sleep(2)
    r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
                 headers={**H_JSON, "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"},
                 impersonate="chrome", timeout=8)
    if r.status_code == 200:
        d = r.json()
        prods = d.get("data",{}).get("products",[])
        if prods:
            p = prods[0]
            pk = {k:v for k,v in p.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:3_v2_detail", True, f"n={len(prods)} price={pk}")
        else:
            mark("WB:3_v2_detail", False, f"HTTP=200 products=[]")
    else:
        mark("WB:3_v2_detail", False, f"HTTP={r.status_code}")
    
    # 4. basket CDN: try c.json (colors/variants) — может быть цена
    for bh in ["basket-42","basket-01","basket-10"]:
        url = f"https://{bh}.wbbasket.ru/vol{vol}/part{part}/{imt_id}/c.json"
        r = cffi.get(url, headers=H_JSON, impersonate="chrome", timeout=5)
        if r.status_code == 200:
            d = r.json()
            mark("WB:4_basket_c", True, f"host={bh} keys={list(d.keys())[:12]} sample={json.dumps(d,ensure_ascii=False)[:200]}")
            break
    else:
        mark("WB:4_basket_c", False, "c.json all 404")

# ============================================================
# OZON: PW --no-sandbox + 2-page warm-up
# ============================================================
def test_ozon():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=["--no-sandbox","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage","--disable-gpu"]
    )
    ctx = browser.new_context(
        user_agent=UA, viewport={"width":1920,"height":1080}, locale="ru-RU",
        extra_http_headers={"Accept-Language":"ru-RU,ru;q=0.9"},
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});
        Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});
        window.chrome={runtime:{}};
    """)
    page = ctx.new_page()
    
    # Warm-up: 2 pages
    try:
        page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
        page.goto("https://www.ozon.ru/category/smartfony/510/", wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
    except Exception as e:
        mark("OZ:1_warmup", False, f"err={str(e)[:80]}")
    
    # Product
    product_url = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
    try:
        page.goto(product_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(5)
        html = page.content()
        ptitle = page.title()
        
        price = None
        # DOM selector
        try:
            for sel in ['[data-widget*="Price"] [data-widget*="Value"]','.price-value','[class*="tilePrice"] span','[data-widget="Price"] span']:
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
        
        # data-widget state
        if not price:
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
            if m: price = m.group(1)
        
        title = ""
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m: title = m.group(1)
        
        mark("OZ:2_product", bool(price), f"price={price} title={title[:50]} ptitle={ptitle[:50]} len={len(html)}")
        
        if not price and len(html) < 5000:
            # dump small page for analysis
            with open(os.path.join(os.path.dirname(__file__), "d16_ozon.html"), "w", encoding="utf-8") as f:
                f.write(html)
    except Exception as e:
        mark("OZ:2_product", False, f"err={str(e)[:80]}")
    
    browser.close()
    pw.stop()

# ============================================================
# AVITO: m.avito.ru + IP check
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
    
    # 1. m.avito.ru
    try:
        page.goto("https://m.avito.ru/vladivostok/telefony", wait_until="domcontentloaded", timeout=20000)
        time.sleep(4)
        html = page.content()
        ptitle = page.title()
        offers = re.findall(r'href="([^"]*?/p(\d{10,}))"', html)
        mark("AV:1_mobile", len(offers)>0, f"len={len(html)} offers={len(offers)} ptitle={ptitle[:50]}")
    except Exception as e:
        mark("AV:1_mobile", False, f"err={str(e)[:80]}")
    
    # 2. www.avito.ru с longer wait
    try:
        page.goto("https://www.avito.ru/vladivostok/telefony", wait_until="domcontentloaded", timeout=20000)
        time.sleep(6)
        html = page.content()
        ptitle = page.title()
        # IP block?
        ip_blocked = "IP" in ptitle or "ограничен" in ptitle.lower()
        offers = re.findall(r'href="(/vladivostok/[^"]*?/p(\d{10,}))"', html)
        if not offers:
            offers = re.findall(r'data-offer-id="(\d+)"', html)
            offers = [(f"p{o}", o) for o in offers]
        mark("AV:2_www", len(offers)>0, f"len={len(html)} offers={len(offers)} ip_blocked={ip_blocked} ptitle={ptitle[:50]}")
    except Exception as e:
        mark("AV:2_www", False, f"err={str(e)[:80]}")
    
    browser.close()
    pw.stop()

# ============================================================
if __name__ == "__main__":
    print(f"=== diag_multi16 START {time.strftime('%H:%M:%S')} ===")
    for name, fn in [("FP", test_farpost), ("WB", test_wb), ("OZ", test_ozon), ("AV", test_avito)]:
        try:
            fn()
        except Exception as e:
            mark(f"{name}:EXC", False, f"{type(e).__name__}: {str(e)[:100]}")
    
    RESULTS["total_s"] = round(time.monotonic()-T0,1)
    RESULTS["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = os.path.join(os.path.dirname(__file__), "d16_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"\n=== SUMMARY ({RESULTS['total_s']}s) ===")
    for k,v in RESULTS.items():
        if k in ("total_s","time"): continue
        print(f"  {'PASS' if v['ok'] else 'FAIL'} {k}: {v['detail'][:120]}")
    print("EXIT=0")
