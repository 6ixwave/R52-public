#!/usr/bin/env python3
"""diag_multi8.py — FP verify, WB basket+PW, AV PW+XHR."""
import json, re, time, logging, os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d8")
BASE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.9"}
results = {}
t0 = time.time()

def save(name, data):
    results[name] = data
    log.info("  -> %s: ok=%s %s", name, data.get("ok"), str(data.get("detail",""))[:250])

def extract_prices(html):
    prices = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            ld = json.loads(m.group(1).strip())
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if isinstance(item, dict):
                    off = item.get("offers")
                    if isinstance(off, dict):
                        p = off.get("price") or off.get("lowPrice")
                        if p: prices.append(float(p))
                    elif isinstance(off, list):
                        for o in off:
                            p = o.get("price")
                            if p: prices.append(float(p))
        except: pass
    for m in re.finditer(r'"price"\s*:\s*"?(\d{2,})"?', html):
        prices.append(float(m.group(1)))
    for m in re.finditer(r'og:price:amount["\']\s+content=["\']([\d.]+)', html, re.I):
        prices.append(float(m.group(1)))
    return prices

# ============================================================
# FARPOST — Playwright (обработать verify redirect)
# ============================================================
log.info("=== FARPOST ===")
FP_ID = "138952176"
FP_URL = f"https://www.farpost.ru/vladivostok/{FP_ID}.html"

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="ru-RU", viewport={"width":1366,"height":768})
        page = ctx.new_page()
        
        # Step 1: warm-up (получить cookies ring, ring_session)
        page.goto("https://www.farpost.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        cookies1 = ctx.cookies()
        log.info("FP warmup cookies: %s", [c['name'] for c in cookies1])
        
        # Step 2: перейти на объявление (обработает verify)
        page.goto(FP_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)
        html = page.content()
        prices = extract_prices(html)
        title_m = re.search(r'<title>(.*?)</title>', html, re.S)
        title = title_m.group(1).strip() if title_m else "?"
        
        # Ищем специфичные FP-маркеры
        fp_markers = {
            "bulletinText": bool(re.search(r'bulletinText|bulletin-text', html, re.I)),
            "price_block": bool(re.search(r'class=["\'][^"\']*price', html, re.I)),
            "data-price": bool(re.search(r'data-price', html, re.I)),
            "jsonld": bool(re.search(r'application/ld\+json', html, re.I)),
            "og_price": bool(re.search(r'og:price', html, re.I)),
        }
        
        # Сохраняем
        with open(os.path.join(BASE, "d8_fp_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        # Также ловим XHR
        xhrs = []
        page2 = ctx.new_page()
        def on_resp(resp):
            if "api" in resp.url or "ajax" in resp.url or "bulletin" in resp.url:
                try:
                    body = resp.text()
                    if "price" in body.lower():
                        xhrs.append({"url": resp.url, "len": len(body), "price_match": re.findall(r'"price"\s*:\s*"?(\d+)', body)[:3]})
                except: pass
        page2.on("response", on_resp)
        page2.goto(FP_URL, wait_until="networkidle", timeout=30000)
        page2.wait_for_timeout(2000)
        
        save("FP:playwright", {
            "ok": len(prices) > 0,
            "detail": f"len={len(html)} title={title[:60]} prices={prices[:5]} markers={fp_markers} xhr={xhrs[:3]}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("FP:playwright", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP2: requests с ручным verify
try:
    import requests
    s = requests.Session()
    s.headers.update(HDRS)
    s.get("https://www.farpost.ru/", timeout=10)
    # Прямой запрос к объявлению
    r = s.get(FP_URL, timeout=15, allow_redirects=True)
    html = r.text
    prices = extract_prices(html)
    title_m = re.search(r'<title>(.*?)</title>', html, re.S)
    title = title_m.group(1).strip() if title_m else "?"
    # Проверяем: это verify-страница или реальное объявление?
    is_verify = "verify" in r.url or "Проверка" in html[:2000]
    is_home = "доска объявлений" in title.lower()
    save("FP:requests_verify", {
        "ok": len(prices) > 0 and not is_home,
        "detail": f"HTTP={r.status_code} url={r.url[:80]} len={len(html)} title={title[:50]} prices={prices[:3]} verify={is_verify} home={is_home}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:requests_verify", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# WILDBERRIES
# ============================================================
log.info("=== WILDBERRIES ===")
WB_NM = "1097975275"

# WB1: Playwright — HTML страница с ценой
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="ru-RU", viewport={"width":1366,"height":768})
        page = ctx.new_page()
        page.goto(f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        html = page.content()
        prices = extract_prices(html)
        
        # WB-specific: data-attributes, __INITIAL_STATE__
        wb_state = re.search(r'__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.S)
        wb_price_el = re.findall(r'data-price(?:-value)?=["\']?(\d+)', html)
        wb_sale = re.findall(r'salepriceU["\']?\s*[:=]\s*["\']?(\d+)', html)
        wb_basic = re.findall(r'"basic"\s*:\s*(\d+)', html)
        
        with open(os.path.join(BASE, "d8_wb_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        save("WB:playwright", {
            "ok": len(prices) > 0 or wb_price_el or wb_basic,
            "detail": f"len={len(html)} prices={prices[:5]} data_price={wb_price_el[:3]} sale={wb_sale[:3]} basic={wb_basic[:3]} state={'yes' if wb_state else 'no'}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("WB:playwright", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB2: basket CDN — правильный путь (из d6 file_scan: card.json=200)
try:
    import requests
    ws = requests.Session()
    ws.headers.update(HDRS)
    ws.get("https://www.wildberries.ru/", timeout=10)
    
    nm = int(WB_NM)
    vol = nm // 100000
    part = (nm % 100000) // 1000
    sub = (nm % 100000) // 100
    
    # Пробуем несколько форматов
    paths = [
        f"vol{vol:03d}/part{part:02d}/{sub:02d}/{WB_NM}/info/ru.json",
        f"vol{vol:03d}/part{part:02d}/{sub:02d}/{WB_NM}/card.json",
        f"vol{vol:03d}/info/{WB_NM}.json",
    ]
    for host in [42, 10, 20, 30, 50]:
        for p in paths:
            url = f"https://basket-{host}.wbbasket.ru/{p}"
            try:
                r = ws.get(url, timeout=8)
                if r.status_code == 200:
                    j = r.json()
                    save(f"WB:basket_{host}_{os.path.basename(p)}", {
                        "ok": True,
                        "detail": f"HTTP=200 keys={list(j.keys())[:10]} len={len(r.text)}",
                        "s": round(time.time()-t0,1)
                    })
                    with open(os.path.join(BASE, f"d8_wb_basket_{host}.json"), "w", encoding="utf-8") as f:
                        f.write(r.text)
                    break
            except: pass
        else:
            continue
        break
    else:
        save("WB:basket_scan", {"ok": False, "detail": "all 404", "s": round(time.time()-t0,1)})
except Exception as e:
    save("WB:basket_scan", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB3: card.wb.ru v1 (не v2)
try:
    import requests
    ws2 = requests.Session()
    ws2.headers.update(HDRS)
    ws2.get("https://www.wildberries.ru/", timeout=10)
    r = ws2.get(f"https://card.wb.ru/cards/detail?nm={WB_NM}", timeout=15,
                headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
    if r.status_code == 200 and r.text.startswith("{"):
        j = r.json()
        prods = j.get("data",{}).get("products",[])
        pp = []
        if prods:
            p = prods[0]
            po = p.get("price",{})
            pp = [po.get("basic",0), po.get("percent",0), p.get("salepriceU",0), p.get("priceU",0)]
        save("WB:card_v1", {
            "ok": any(x > 0 for x in pp),
            "detail": f"HTTP=200 n={len(prods)} prices={pp[:6]}",
            "s": round(time.time()-t0,1)
        })
    else:
        save("WB:card_v1", {"ok": False, "detail": f"HTTP={r.status_code} first50={r.text[:50]}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("WB:card_v1", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# AVITO — Playwright + XHR
# ============================================================
log.info("=== AVITO ===")
AV_URL = "https://www.avito.ru/vladivostok/1154321987"

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="ru-RU", viewport={"width":1366,"height":768}
        )
        page = ctx.new_page()
        
        # Ловим XHR с ценой
        xhr_data = []
        def on_resp(resp):
            try:
                if resp.status == 200 and ("api" in resp.url or "offer" in resp.url or "price" in resp.url):
                    ct = resp.headers.get("content-type","")
                    if "json" in ct:
                        body = resp.text()
                        if "price" in body.lower():
                            xhr_data.append({"url": resp.url[:100], "prices": re.findall(r'"price"\s*:\s*"?(\d+)', body)[:5]})
            except: pass
        page.on("response", on_resp)
        
        page.goto(AV_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        html = page.content()
        prices = extract_prices(html)
        
        # Avito-specific patterns
        av_patterns = {
            "data-price": re.findall(r'data-price=["\']?(\d+)', html),
            "meta-og": re.findall(r'og:price:amount["\']\s+content=["\']([\d.]+)', html),
            "price-block": re.findall(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>([^<]{1,20})', html)[:5],
            "json-state": bool(re.search(r'window\.__INITIAL|__PRELOADED|__APP_DATA', html)),
            "captcha": bool(re.search(r'hcaptcha|recaptcha|captcha', html, re.I)),
        }
        
        with open(os.path.join(BASE, "d8_avito_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        save("AV:playwright_v2", {
            "ok": len(prices) > 0 or av_patterns["data-price"] or av_patterns["meta-og"],
            "detail": f"len={len(html)} prices={prices[:5]} pats={av_patterns} xhr={xhr_data[:3]}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("AV:playwright_v2", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# SAVE
# ============================================================
out = {"time": datetime.now().isoformat(), "total_s": round(time.time()-t0,1), "results": results}
out_path = os.path.join(BASE, "d8_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log.info("Saved to %s", out_path)
log.info("TOTAL: %.1fs", time.time()-t0)
