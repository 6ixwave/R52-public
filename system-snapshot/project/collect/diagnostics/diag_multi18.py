"""diag_multi18.py — d18.
FP: 5 anns из 3 категорий → стабильность.
WB: sweep 31-60 + другой товар + basket с nm_id.
OZ: api.ozon.ru (другой сабдомен, может без FAB).
AV: RSS + l10.avito.ru + search.
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

def extract_fp_price(html):
    """Извлечь цену из Farpost HTML."""
    price = None
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict):
                off = d.get("offers") or {}
                price = off.get("price") or off.get("lowPrice")
                if price: return price
                if "@graph" in d:
                    for node in d["@graph"]:
                        if isinstance(node, dict) and node.get("@type") in ("Product","Offer"):
                            off2 = node.get("offers") or {}
                            p2 = off2.get("price") or off2.get("lowPrice")
                            if p2: return p2
        except: pass
    m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
    if m: price = int(m.group(1))
    if not price:
        m = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)', html)
        if m: price = m.group(1).replace(" ","")
    return price

# ============================================================
# FARPOST: 5 объявлений из 3 категорий
# ============================================================
def test_farpost():
    cffi = get_cffi()
    cats = ["auto", "home", "hobby"]
    all_success = 0
    all_total = 0
    
    for cat in cats:
        url_cat = f"https://www.farpost.ru/vladivostok/{cat}/"
        r = cffi.get(url_cat, headers=H_HTML, impersonate="chrome", timeout=10)
        if r.status_code != 200:
            mark(f"FP:cat_{cat}", False, f"HTTP={r.status_code}")
            continue
        html = r.text
        anns = re.findall(r'href="(/vladivostok/[^"]*?/([a-z0-9\-]+?)(\d{6,})\.html)"', html)
        if not anns:
            mark(f"FP:cat_{cat}", False, f"HTTP=200 anns=0")
            continue
        mark(f"FP:cat_{cat}", True, f"HTTP=200 anns={len(anns)}")
        
        # 2 объявления из каждой категории
        for i, (path, slug, bid) in enumerate(anns[:2]):
            if all_total >= 6: break
            url = f"https://www.farpost.ru{path}"
            try:
                r2 = cffi.get(url, headers=H_HTML, impersonate="chrome", timeout=8)
            except:
                continue
            if r2.status_code != 200:
                all_total += 1
                continue
            all_total += 1
            h = r2.text
            price = extract_fp_price(h)
            title = ""
            m = re.search(r'<title>(.*?)</title>', h, re.DOTALL)
            if m: title = m.group(1).strip()[:60]
            
            if price:
                all_success += 1
                mark(f"FP:{cat}_{i}", True, f"id={bid} price={price} title={title[:40]}")
            time.sleep(0.5)
    
    mark("FP:stability", all_success >= 3, f"success={all_success}/{all_total}")

# ============================================================
# WB: sweep 31-60 + другой товар + nm_id
# ============================================================
def test_wb():
    cffi = get_cffi()
    nm_id = 1097975275
    imt_id = 2227481309
    vol = imt_id // 100000
    part = imt_id % 100000
    
    # 1. Sweep hosts 31-60
    found = False
    for h in range(31, 61):
        bh = f"basket-{h:02d}"
        url = f"https://{bh}.wbbasket.ru/vol{vol}/part{part}/{imt_id}/card.json"
        try:
            r = cffi.get(url, headers=H_JSON, impersonate="chrome", timeout=4)
            if r.status_code == 200:
                d = r.json()
                pk = [k for k in d.keys() if any(x in k.lower() for x in ('price','cost','sale'))]
                mark("WB:1_sweep31_60", True, f"host={bh} title={d.get('name','')[:40]} price_keys={pk} nkeys={len(d.keys())}")
                with open(os.path.join(os.path.dirname(__file__), "d18_wb_card.json"), "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                found = True
                break
        except:
            continue
    if not found:
        mark("WB:1_sweep31_60", False, "hosts 31-60 all 404/err")
    
    # 2. Другой товар: Samsung Galaxy S24 (nm=1968915452)
    nm2 = 1968915452
    # Сначала найдём imt через card.wb.ru (может работать для другого товара)
    r2 = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm2}",
                  headers={**H_JSON, "Referer": f"https://www.wildberries.ru/catalog/{nm2}/detail.aspx"},
                  impersonate="chrome", timeout=8)
    if r2.status_code == 200:
        d2 = r2.json()
        prods = d2.get("data",{}).get("products",[])
        if prods:
            p2 = prods[0]
            imt2 = p2.get("imt", 0)
            vol2 = imt2 // 100000
            part2 = imt2 % 100000
            pk2 = {k:v for k,v in p2.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:2_other_nm", True, f"nm={nm2} imt={imt2} vol={vol2} part={part2} price={pk2} name={str(p2.get('name',''))[:40]}")
            # Сохраним для basket
            with open(os.path.join(os.path.dirname(__file__), "d18_wb_nm2.json"), "w", encoding="utf-8") as f:
                json.dump(p2, f, ensure_ascii=False, indent=2)
        else:
            mark("WB:2_other_nm", False, f"HTTP=200 products=[]")
    else:
        mark("WB:2_other_nm", False, f"HTTP={r2.status_code} len={r2.text[:100]}")
    
    # 3. basket-42 с nm_id (не imt_id) — альтернативный путь
    url_nm = f"https://basket-42.wbbasket.ru/vol{vol}/part{part}/{nm_id}/card.json"
    r3 = cffi.get(url_nm, headers=H_JSON, impersonate="chrome", timeout=6)
    if r3.status_code == 200:
        d3 = r3.json()
        mark("WB:3_b42_nm", True, f"HTTP=200 name={d3.get('name','')[:40]} keys={list(d3.keys())[:10]}")
    else:
        mark("WB:3_b42_nm", False, f"HTTP={r3.status_code} len={r3.text[:80]}")
    
    # 4. catalog.wb.ru search API (публичный)
    r4 = cffi.get("https://catalog.wb.ru/catalog/18547/search?query=iphone&resultset=catalog&sort=popular&page=1&dest=-1257786",
                  headers={**H_JSON, "Referer": "https://www.wildberries.ru/"},
                  impersonate="chrome", timeout=10)
    if r4.status_code == 200:
        d4 = r4.json()
        prods = d4.get("data",{}).get("products",[])
        if prods:
            p4 = prods[0]
            pk4 = {k:v for k,v in p4.items() if 'price' in k.lower() or 'sale' in k.lower()}
            mark("WB:4_catalog_search", True, f"n={len(prods)} first_price={pk4} name={str(p4.get('name',''))[:40]} imt={p4.get('imt')}")
        else:
            mark("WB:4_catalog_search", False, f"HTTP=200 products=[] head={r4.text[:150]}")
    else:
        mark("WB:4_catalog_search", False, f"HTTP={r4.status_code} len={r4.text[:100]}")

# ============================================================
# OZON: api.ozon.ru (другой сабдомен)
# ============================================================
def test_ozon():
    cffi = get_cffi()
    pid = "3481320800"
    
    # 1. api.ozon.ru composer
    url1 = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/&state="
    r1 = cffi.get(url1, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
    }, impersonate="chrome", timeout=12)
    if r1.status_code == 200:
        try:
            d1 = r1.json()
            # Ищем price в структуре
            txt = json.dumps(d1, ensure_ascii=False)
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', txt)
            price = m.group(1) if m else None
            mark("OZ:1_api_composer", True, f"HTTP=200 len={len(r1.text)} price={price} head={r1.text[:200]}")
        except Exception as e:
            mark("OZ:1_api_composer", False, f"HTTP=200 json_err={e} head={r1.text[:200]}")
    else:
        mark("OZ:1_api_composer", False, f"HTTP={r1.status_code} len={r1.text[:150]}")
    
    # 2. entrypoint-api
    url2 = f"https://api.ozon.ru/entrypoint-api.bx/page/json/v2?url=/product/{pid}/"
    r2 = cffi.get(url2, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.ozon.ru/",
    }, impersonate="chrome", timeout=12)
    if r2.status_code == 200:
        txt2 = r2.text
        m2 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', txt2)
        price2 = m2.group(1) if m2 else None
        mark("OZ:2_entrypoint", True, f"HTTP=200 len={len(txt2)} price={price2} head={txt2[:200]}")
    else:
        mark("OZ:2_entrypoint", False, f"HTTP={r2.status_code} len={r2.text[:150]}")
    
    # 3. www.ozon.ru SSR (curl_cffi, не PW) — может FAB только для PW?
    url3 = f"https://www.ozon.ru/product/{pid}/"
    r3 = cffi.get(url3, headers=H_HTML, impersonate="chrome", timeout=15)
    if r3.status_code == 200:
        html3 = r3.text
        no_conn = "нет соединения" in html3.lower()
        price3 = None
        m3 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html3)
        if m3: price3 = m3.group(1)
        if not price3:
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html3, re.DOTALL):
                try:
                    d = json.loads(m.group(1))
                    if isinstance(d, dict):
                        off = d.get("offers") or {}
                        price3 = off.get("price") or off.get("lowPrice")
                        if price3: break
                except: pass
        mark("OZ:3_ssr_cffi", bool(price3), f"HTTP=200 len={len(html3)} price={price3} no_conn={no_conn}")
    else:
        mark("OZ:3_ssr_cffi", False, f"HTTP={r3.status_code} len={r3.text[:150]}")
    
    # 4. api.ozon.ru с session (warm-up на api.ozon.ru)
    s = cffi.Session(impersonate="chrome")
    try:
        s.get("https://api.ozon.ru/", headers={"User-Agent": UA}, timeout=8)
        time.sleep(1)
        r4 = s.get(f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{pid}/&state=",
                   headers={"Accept":"application/json","Referer":"https://www.ozon.ru/"}, timeout=12)
        if r4.status_code == 200:
            txt4 = r4.text
            m4 = re.search(r'"price"\s*:\s*"?([\d.]+)"?', txt4)
            mark("OZ:4_api_session", True, f"HTTP=200 len={len(txt4)} price={m4.group(1) if m4 else None} head={txt4[:200]}")
        else:
            mark("OZ:4_api_session", False, f"HTTP={r4.status_code} len={r4.text[:150]}")
    except Exception as e:
        mark("OZ:4_api_session", False, f"err={str(e)[:80]}")

# ============================================================
# AVITO: RSS + l10 + search
# ============================================================
def test_avito():
    cffi = get_cffi()
    
    # 1. RSS feed
    url_rss = "https://www.avito.ru/vladivostok/telefony/rss"
    r1 = cffi.get(url_rss, headers={**H_HTML, "Accept": "application/rss+xml, application/xml, text/xml"}, impersonate="chrome", timeout=12)
    if r1.status_code == 200:
        items = re.findall(r'<item>', r1.text)
        titles = re.findall(r'<title>(.*?)</title>', r1.text)
        links = re.findall(r'<link>(.*?)</link>', r1.text)
        mark("AV:1_rss", len(items)>0, f"HTTP=200 items={len(items)} titles={titles[:3]} links={links[:2]}")
    else:
        mark("AV:1_rss", False, f"HTTP={r1.status_code} len={r1.text[:150]}")
    
    # 2. l10.avito.ru (региональный)
    url_l10 = "https://l10.avito.ru/vladivostok/telefony"
    r2 = cffi.get(url_l10, headers=H_HTML, impersonate="chrome", timeout=12)
    if r2.status_code == 200:
        html2 = r2.text
        ip_block = "IP" in html2[:2000] or "ограничен" in html2[:2000].lower()
        offers = re.findall(r'href="(/vladivostok/[^"]*?/p(\d{10,}))"', html2)
        mark("AV:2_l10", len(offers)>0, f"HTTP=200 len={len(html2)} offers={len(offers)} ip_block={ip_block}")
    else:
        mark("AV:2_l10", False, f"HTTP={r2.status_code} len={r2.text[:150]}")
    
    # 3. Search page с query
    url_s = "https://www.avito.ru/vladivostok/telefony?s=1&q=iphone"
    r3 = cffi.get(url_s, headers=H_HTML, impersonate="chrome", timeout=12)
    if r3.status_code == 200:
        html3 = r3.text
        offers3 = re.findall(r'href="(/vladivostok/[^"]*?/p(\d{10,}))"', html3)
        ip_block3 = "IP" in html3[:2000] or "ограничен" in html3[:2000].lower()
        mark("AV:3_search", len(offers3)>0, f"HTTP=200 len={len(html3)} offers={len(offers3)} ip_block={ip_block3}")
    else:
        mark("AV:3_search", False, f"HTTP={r3.status_code} len={r3.text[:150]}")
    
    # 4. Avito mobile API
    url_m = "https://www.avito.ru/api/geo/2/telefony?limit=5"
    r4 = cffi.get(url_m, headers={**H_JSON, "Referer": "https://www.avito.ru/"}, impersonate="chrome", timeout=10)
    if r4.status_code == 200:
        mark("AV:4_mobile_api", True, f"HTTP=200 len={r4.text[:200]}")
    else:
        mark("AV:4_mobile_api", False, f"HTTP={r4.status_code} len={r4.text[:150]}")

# ============================================================
if __name__ == "__main__":
    print(f"=== diag_multi18 START {time.strftime('%H:%M:%S')} ===")
    for name, fn in [("FP", test_farpost), ("WB", test_wb), ("OZ", test_ozon), ("AV", test_avito)]:
        try:
            fn()
        except Exception as e:
            import traceback
            mark(f"{name}:EXC", False, f"{type(e).__name__}: {str(e)[:100]}")
    
    RESULTS["total_s"] = round(time.monotonic()-T0,1)
    RESULTS["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = os.path.join(os.path.dirname(__file__), "d18_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"\n=== SUMMARY ({RESULTS['total_s']}s) ===")
    for k,v in RESULTS.items():
        if k in ("total_s","time"): continue
        print(f"  {'PASS' if v['ok'] else 'FAIL'} {k}: {v['detail'][:120]}")
    print("EXIT=0")
