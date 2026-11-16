"""diag_multi19.py — d19.
FP: полный пайплайн (все поля) + ID 138859452/138952176 + 5 anns.
WB: session main→product + basket 61-100 + свежий iPhone 16.
OZ: curl_cffi verify=False на api.ozon.ru + m.ozon.ru + PW ignore_https.
AV: 15s warmup + 5s между запросами + l10.
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

def fp_extract(html):
    """Полное извлечение Farpost."""
    d = {}
    # Title
    m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    d["title"] = m.group(1).strip()[:150] if m else ""
    # Price
    price = None
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            j = json.loads(m.group(1))
            if isinstance(j, dict):
                off = j.get("offers") or {}
                price = off.get("price") or off.get("lowPrice")
                if price:
                    d["price"] = price
                    d["currency"] = off.get("priceCurrency","RUB")
                    d["availability"] = off.get("availability","")
                    break
                if "@graph" in j:
                    for n in j["@graph"]:
                        if isinstance(n, dict) and n.get("@type")=="Product":
                            o2 = n.get("offers") or {}
                            price = o2.get("price") or o2.get("lowPrice")
                            if price:
                                d["price"] = price
                                d["currency"] = o2.get("priceCurrency","RUB")
                                break
        except: pass
    if not price:
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m: d["price"] = int(m.group(1))
    # Images
    imgs = set(re.findall(r'(?:src|data-src)=["\']([^"\']*static\.baza\.farpost\.ru/[^"\']+)["\']', html))
    d["images"] = list(imgs)[:20]
    # Specs
    specs = re.findall(r'<div\s+class="label">([^<]+)</div>\s*<div\s+class="value">\s*<span[^>]*>(.*?)</span>', html, re.DOTALL)
    d["specs"] = {k.strip(): re.sub(r'<[^>]+>','',v).strip()[:80] for k,v in specs[:15]}
    # Description
    m = re.search(r'<div\s+class="bulletinText[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    d["desc"] = re.sub(r'<[^>]+>',' ',m.group(1)).strip()[:500] if m else ""
    # Seller
    m = re.search(r'class="company-profile[^"]*"[^>]*>.*?<a[^>]*>([^<]+)<', html, re.DOTALL)
    d["seller"] = m.group(1).strip()[:60] if m else ""
    # Location
    m = re.search(r'class="[^"]*location[^"]*"[^>]*>([^<]+)<', html, re.I)
    d["location"] = m.group(1).strip()[:60] if m else ""
    return d

# ============================================================
# FARPOST
# ============================================================
def test_farpost():
    cffi = get_cffi()
    
    # 1. Конкретные ID (были 404 — пробуем /bulletin/ и прямой)
    for bid in ["138859452","138952176"]:
        urls = [
            f"https://www.farpost.ru/bulletin/{bid}",
            f"https://www.farpost.ru/vladivostok/bulletin/{bid}",
            f"https://www.farpost.ru/{bid}.h",
            f"https://www.farpost.ru/vladivostok/{bid}.h",
        ]
        found = False
        for u in urls:
            try:
                r = cffi.get(u, headers=H_HTML, impersonate="chrome", timeout=8, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > 5000:
                    data = fp_extract(r.text)
                    data["url"] = u
                    mark(f"FP:id_{bid}", bool(data.get("price")), f"url={u[-40:]} price={data.get('price')} title={data['title'][:40]} imgs={len(data['images'])} specs={len(data['specs'])}")
                    found = True
                    break
            except: continue
        if not found:
            mark(f"FP:id_{bid}", False, f"all 4 urls failed for {bid}")
        time.sleep(1)
    
    # 2. 5 anns из разных категорий (стабильность + полный пайплайн)
    cats = ["auto","home","hobby","sport","clothes"]
    success = 0
    total = 0
    for cat in cats:
        r = cffi.get(f"https://www.farpost.ru/vladivostok/{cat}/", headers=H_HTML, impersonate="chrome", timeout=10)
        if r.status_code != 200: continue
        anns = re.findall(r'href="(/vladivostok/[^"]*?/([a-z0-9\-]+?)(\d{6,})\.html)"', r.text)
        if not anns: continue
        path, slug, bid = anns[0]
        total += 1
        r2 = cffi.get(f"https://www.farpost.ru{path}", headers=H_HTML, impersonate="chrome", timeout=8)
        if r2.status_code != 200: continue
        data = fp_extract(r2.text)
        if data.get("price"):
            success += 1
            mark(f"FP:full_{cat}", True, f"id={bid} price={data['price']} title={data['title'][:35]} imgs={len(data['images'])} specs={len(data['specs'])} desc={len(data['desc'])}b")
            if cat == "auto":
                with open(os.path.join(os.path.dirname(__file__), "d19_fp_sample.json"), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        time.sleep(0.5)
    mark("FP:stability", success >= 4, f"success={success}/{total}")

# ============================================================
# WB: session + fresh product + basket 61-100
# ============================================================
def test_wb():
    cffi = get_cffi()
    
    # 1. Session: main page → cookies → product
    s = cffi.Session(impersonate="chrome")
    try:
        r_main = s.get("https://www.wildberries.ru/", headers=H_HTML, timeout=12)
        time.sleep(2)
        # Продукт
        nm_id = 1097975275
        r_prod = s.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx", headers=H_HTML, timeout=12)
        mark("WB:1_session_html", r_prod.status_code==200, f"main={r_main.status_code} prod={r_prod.status_code} len={len(r_prod.text)} head={r_prod.text[:100]}")
        
        # Если HTML 200 — ищем basket host
        if r_prod.status_code == 200 and len(r_prod.text) > 5000:
            hosts = set(re.findall(r'(basket-\d+\.wbbasket\.ru)', r_prod.text))
            vol_m = re.search(r'vol(\d+)/part(\d+)', r_prod.text)
            mark("WB:2_html_hosts", bool(hosts), f"hosts={hosts} vol={vol_m.groups() if vol_m else None}")
    except Exception as e:
        mark("WB:1_session_html", False, f"err={str(e)[:80]}")
    
    # 2. Свежий популярный товар: iPhone 16 (nm=2134567890 — пример)
    # Лучше: Samsung Galaxy S25 Ultra
    fresh_nm = 2134567890  # placeholder, попробуем
    # Реальный: ищем через search
    r_s = cffi.get(f"https://catalog.wb.ru/catalog/18547/search?query=iphone+16&resultset=catalog&sort=popular&page=1&dest=-1257786",
                   headers={**H_JSON, "Referer":"https://www.wildberries.ru/"}, impersonate="chrome", timeout=10)
    if r_s.status_code == 200:
        d_s = r_s.json()
        prods = d_s.get("data",{}).get("products",[])
        if prods:
            p = prods[0]
            imt = p.get("imt",0)
            vol = imt//100000
            part = imt%100000
            pk = {k:v for k,v in p.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:3_fresh_search", True, f"n={len(prods)} imt={imt} price={pk} name={str(p.get('name',''))[:40]}")
            # Basket для этого товара
            for h in range(1, 15):
                bh = f"basket-{h:02d}"
                url = f"https://{bh}.wbbasket.ru/vol{vol}/part{part}/{imt}/card.json"
                r_b = cffi.get(url, headers=H_JSON, impersonate="chrome", timeout=4)
                if r_b.status_code == 200:
                    d_b = r_b.json()
                    mark("WB:4_fresh_basket", True, f"host={bh} name={d_b.get('name','')[:30]} keys={len(d_b.keys())}")
                    with open(os.path.join(os.path.dirname(__file__), "d19_wb_fresh.json"), "w", encoding="utf-8") as f:
                        json.dump(d_b, f, ensure_ascii=False, indent=2)
                    break
            else:
                mark("WB:4_fresh_basket", False, f"hosts 01-14 404 for imt={imt}")
        else:
            mark("WB:3_fresh_search", False, f"HTTP=200 products=[]")
    else:
        mark("WB:3_fresh_search", False, f"HTTP={r_s.status_code} len={r_s.text[:100]}")
    
    # 3. Basket 61-100 для старого товара
    imt_id = 2227481309
    vol = imt_id // 100000
    part = imt_id % 100000
    found = False
    for h in range(61, 101):
        bh = f"basket-{h:02d}"
        url = f"https://{bh}.wbbasket.ru/vol{vol}/part{part}/{imt_id}/card.json"
        try:
            r = cffi.get(url, headers=H_JSON, impersonate="chrome", timeout=3)
            if r.status_code == 200:
                d = r.json()
                mark("WB:5_sweep61_100", True, f"host={bh} name={d.get('name','')[:30]} nkeys={len(d.keys())}")
                found = True
                break
        except: continue
    if not found:
        mark("WB:5_sweep61_100", False, "hosts 61-100 all 404/err")
    
    # 4. card.wb.ru с session (main → card)
    s2 = cffi.Session(impersonate="chrome")
    s2.get("https://www.wildberries.ru/", headers=H_HTML, timeout=8)
    time.sleep(1)
    r_cw = s2.get(f"https://card.wb.ru/cards/v2/detail?nm={nm_id}",
                  headers={**H_JSON, "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"}, timeout=8)
    if r_cw.status_code == 200:
        d_cw = r_cw.json()
        prods = d_cw.get("data",{}).get("products",[])
        if prods:
            p = prods[0]
            pk = {k:v for k,v in p.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:6_cardw_session", True, f"n={len(prods)} price={pk} name={str(p.get('name',''))[:35]}")
        else:
            mark("WB:6_cardw_session", False, f"HTTP=200 products=[]")
    else:
        mark("WB:6_cardw_session", False, f"HTTP={r_cw.status_code} len={r_cw.text[:80]}")

# ============================================================
# OZON: verify=False + m.ozon + PW ignore_https
# ============================================================
def test_ozon():
    cffi = get_cffi()
    pid = "3481320800"
    
    # 1. api.ozon.ru verify=False
    try:
        r1 = cffi.get(f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/&state=",
                      headers={"User-Agent":UA,"Accept":"application/json","Referer":"https://www.ozon.ru/","Origin":"https://www.ozon.ru"},
                      impersonate="chrome", timeout=12, verify=False)
        if r1.status_code == 200:
            txt = r1.text
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', txt)
            mark("OZ:1_api_noverify", True, f"HTTP=200 len={len(txt)} price={m.group(1) if m else None} head={txt[:150]}")
        else:
            mark("OZ:1_api_noverify", False, f"HTTP={r1.status_code} len={r1.text[:150]}")
    except Exception as e:
        mark("OZ:1_api_noverify", False, f"err={str(e)[:100]}")
    
    # 2. m.ozon.ru (mobile)
    try:
        r2 = cffi.get(f"https://m.ozon.ru/product/{pid}/", headers=H_HTML, impersonate="chrome", timeout=12, verify=False)
        if r2.status_code == 200:
            html2 = r2.text
            no_conn = "нет соединения" in html2.lower()
            m2 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html2)
            mark("OZ:2_mobile", bool(m2) and not no_conn, f"HTTP=200 len={len(html2)} price={m2.group(1) if m2 else None} no_conn={no_conn}")
        else:
            mark("OZ:2_mobile", False, f"HTTP={r2.status_code} len={r2.text[:100]}")
    except Exception as e:
        mark("OZ:2_mobile", False, f"err={str(e)[:80]}")
    
    # 3. www.ozon.ru SSR с verify=False (FAB может быть только для PW?)
    try:
        r3 = cffi.get(f"https://www.ozon.ru/product/{pid}/", headers=H_HTML, impersonate="chrome", timeout=15, verify=False)
        if r3.status_code == 200:
            html3 = r3.text
            no_conn = "нет соединения" in html3.lower()
            m3 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html3)
            # JSON-LD
            if not m3:
                for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html3, re.DOTALL):
                    try:
                        j = json.loads(m.group(1))
                        if isinstance(j, dict):
                            off = j.get("offers") or {}
                            if off.get("price"): m3 = type("M",(),{"group":lambda s,i:off["price"]})()
                            break
                    except: pass
            mark("OZ:3_ssr_noverify", bool(m3) and not no_conn, f"HTTP=200 len={len(html3)} price={m3.group(1) if m3 else None} no_conn={no_conn}")
        else:
            mark("OZ:3_ssr_noverify", False, f"HTTP={r3.status_code} len={r3.text[:100]}")
    except Exception as e:
        mark("OZ:3_ssr_noverify", False, f"err={str(e)[:80]}")
    
    # 4. Playwright ignore_https_errors + m.ozon.ru
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=UA, viewport={"width":390,"height":844}, locale="ru-RU",
            ignore_https_errors=True,
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.goto(f"https://m.ozon.ru/product/{pid}/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(4)
        html4 = page.content()
        ptitle = page.title()
        no_conn4 = "нет соединения" in ptitle.lower()
        price4 = None
        try:
            el = page.query_selector('[class*="price"] [class*="value"], [data-widget*="Price"]')
            if el: price4 = el.inner_text().strip()
        except: pass
        if not price4:
            m4 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html4)
            if m4: price4 = m4.group(1)
        mark("OZ:4_pw_mobile", bool(price4) and not no_conn4, f"price={price4} ptitle={ptitle[:40]} no_conn={no_conn4} len={len(html4)}")
        browser.close()
        pw.stop()
    except Exception as e:
        mark("OZ:4_pw_mobile", False, f"err={str(e)[:80]}")

# ============================================================
# AVITO: 15s warmup + delays
# ============================================================
def test_avito():
    cffi = get_cffi()
    
    # 1. Warmup: main page (15s wait)
    r0 = cffi.get("https://www.avito.ru/", headers=H_HTML, impersonate="chrome", timeout=15)
    warmup_ok = r0.status_code == 200
    time.sleep(5)
    
    # 2. Category
    r1 = cffi.get("https://www.avito.ru/vladivostok/telefony", headers=H_HTML, impersonate="chrome", timeout=15)
    if r1.status_code == 200:
        html = r1.text
        ip_block = "IP" in html[:3000] or "ограничен" in html[:3000].lower()
        offers = re.findall(r'href="(/vladivostok/[^"]*?/p(\d{10,}))"', html)
        mark("AV:1_category", len(offers)>0 and not ip_block, f"warmup={r0.status_code} HTTP=200 len={len(html)} offers={len(offers)} ip_block={ip_block}")
        
        # 3. First offer
        if offers:
            time.sleep(5)
            path = offers[0][0]
            r2 = cffi.get(f"https://www.avito.ru{path}", headers=H_HTML, impersonate="chrome", timeout=15)
            if r2.status_code == 200:
                h2 = r2.text
                price = None
                m = re.search(r'itemprop="price"[^>]*content="(\d+)"', h2)
                if m: price = m.group(1)
                if not price:
                    m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)', h2)
                    if m: price = m.group(1).replace(" ","")
                if not price:
                    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', h2, re.DOTALL):
                        try:
                            j = json.loads(m.group(1))
                            if isinstance(j, dict):
                                off = j.get("offers") or {}
                                price = off.get("price")
                                if price: break
                        except: pass
                title = ""
                m = re.search(r'<title>(.*?)</title>', h2, re.DOTALL)
                if m: title = m.group(1).strip()[:60]
                mark("AV:2_offer", bool(price), f"price={price} title={title} len={len(h2)}")
    else:
        mark("AV:1_category", False, f"warmup={r0.status_code} HTTP={r1.status_code} len={r1.text[:100]}")
    
    # 4. l10.avito.ru
    time.sleep(3)
    r3 = cffi.get("https://l10.avito.ru/vladivostok/telefony", headers=H_HTML, impersonate="chrome", timeout=15)
    if r3.status_code == 200:
        html3 = r3.text
        offers3 = re.findall(r'href="(/vladivostok/[^"]*?/p(\d{10,}))"', html3)
        ip3 = "IP" in html3[:3000] or "ограничен" in html3[:3000].lower()
        mark("AV:3_l10", len(offers3)>0 and not ip3, f"HTTP=200 len={len(html3)} offers={len(offers3)} ip={ip3}")
    else:
        mark("AV:3_l10", False, f"HTTP={r3.status_code} len={r3.text[:100]}")

# ============================================================
if __name__ == "__main__":
    print(f"=== diag_multi19 START {time.strftime('%H:%M:%S')} ===")
    for name, fn in [("FP", test_farpost), ("WB", test_wb), ("OZ", test_ozon), ("AV", test_avito)]:
        try:
            fn()
        except Exception as e:
            import traceback
            mark(f"{name}:EXC", False, f"{type(e).__name__}: {str(e)[:100]}")
    
    RESULTS["total_s"] = round(time.monotonic()-T0,1)
    RESULTS["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = os.path.join(os.path.dirname(__file__), "d19_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"\n=== SUMMARY ({RESULTS['total_s']}s) ===")
    for k,v in RESULTS.items():
        if k in ("total_s","time"): continue
        print(f"  {'PASS' if v['ok'] else 'FAIL'} {k}: {v['detail'][:120]}")
    print("EXIT=0")
