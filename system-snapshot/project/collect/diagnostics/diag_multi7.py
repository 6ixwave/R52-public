#!/usr/bin/env python3
"""diag_multi7.py — FP live listing + WB curl_cffi + Ozon/Avito Playwright."""
import json, re, time, logging, os, sys
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d7")

BASE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

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
                    if "price" in item:
                        try: prices.append(float(item["price"]))
                        except: pass
        except: pass
    for m in re.finditer(r'(?:og:price:amount|og:price)["\']\s+content=["\']([\d.]+)["\']', html, re.I):
        prices.append(float(m.group(1)))
    for m in re.finditer(r'"price"\s*:\s*"?(\d{2,})"?', html):
        prices.append(float(m.group(1)))
    return prices

# ============================================================
# FARPOST — найти живое объявление
# ============================================================
log.info("=== FARPOST ===")
import requests

s = requests.Session()
s.headers.update(HDRS)

# FP1: Поиск по категории (найти живое объявление с ценой)
try:
    # Поиск: электроника, Владивосток
    r = s.get("https://www.farpost.ru/vladivostok/search/?q=телефон&sort=date", timeout=15)
    log.info("FP search: HTTP=%d len=%d", r.status_code, len(r.text))
    # Извлекаем ссылки на объявления
    links = re.findall(r'href=["\'](/vladivostok/(\d+)\.html)["\']', r.text)
    # Фильтруем: берём первые 5 уникальных
    seen = set()
    active_ids = []
    for path, bid in links:
        if bid not in seen:
            seen.add(bid)
            active_ids.append(bid)
            if len(active_ids) >= 5:
                break
    log.info("FP search: found %d active IDs: %s", len(active_ids), active_ids[:5])
    
    # Пробуем первое живое объявление
    fp_price_found = False
    fp_listing_data = None
    if active_ids:
        fp_id = active_ids[0]
        fp_url = f"https://www.farpost.ru/vladivostok/{fp_id}.html"
        r2 = s.get(fp_url, timeout=15)
        html2 = r2.text
        prices2 = extract_prices(html2)
        title_m = re.search(r'<title>(.*?)</title>', html2, re.S)
        title = title_m.group(1).strip() if title_m else "?"
        has_ld = bool(re.search(r'application/ld\+json', html2, re.I))
        has_og_price = bool(re.search(r'og:price', html2, re.I))
        
        # Сохраняем HTML
        with open(os.path.join(BASE, "d7_fp_live.html"), "w", encoding="utf-8") as f:
            f.write(html2)
        
        fp_listing_data = {
            "id": fp_id, "url": fp_url, "title": title[:80],
            "prices": prices2[:5], "ld": has_ld, "og_price": has_og_price,
            "http": r2.status_code, "len": len(html2)
        }
        fp_price_found = len(prices2) > 0
        save("FP:live_search", {
            "ok": fp_price_found,
            "detail": f"ID={fp_id} HTTP={r2.status_code} len={len(html2)} title={title[:60]} prices={prices2[:3]} ld={has_ld} og={has_og_price}",
            "s": round(time.time()-t0,1)
        })
    else:
        save("FP:live_search", {"ok": False, "detail": f"HTTP={r.status_code} no links found in search", "s": round(time.time()-t0,1)})
except Exception as e:
    save("FP:live_search", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP2: Прямой доступ к известному ID (138952176) — проверяем redirect
try:
    r = s.get("https://www.farpost.ru/vladivostok/138952176.html", timeout=15, allow_redirects=False)
    loc = r.headers.get("Location", "")
    title_m = re.search(r'<title>(.*?)</title>', r.text, re.S) if r.status_code == 200 else None
    title = title_m.group(1).strip() if title_m else "?"
    prices = extract_prices(r.text) if r.status_code == 200 else []
    save("FP:direct_138952176", {
        "ok": r.status_code == 200 and len(prices) > 0,
        "detail": f"HTTP={r.status_code} loc={loc[:80]} title={title[:60]} prices={prices[:3]} len={len(r.text)}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:direct_138952176", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP3: curl_cffi для Farpost
try:
    from curl_cffi import requests as cffi_req
    fp_id = active_ids[0] if active_ids else "138952176"
    r = cffi_req.get(f"https://www.farpost.ru/vladivostok/{fp_id}.html", impersonate="chrome", timeout=15)
    prices = extract_prices(r.text)
    title_m = re.search(r'<title>(.*?)</title>', r.text, re.S)
    title = title_m.group(1).strip() if title_m else "?"
    save("FP:cffi", {
        "ok": r.status_code == 200 and len(prices) > 0,
        "detail": f"ID={fp_id} HTTP={r.status_code} len={len(r.text)} title={title[:60]} prices={prices[:3]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:cffi", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# WILDBERRIES — curl_cffi card_v2
# ============================================================
log.info("=== WILDBERRIES ===")
WB_NM = "1097975275"

# WB1: curl_cffi card_v2
try:
    from curl_cffi import requests as cffi_req
    r = cffi_req.get(
        f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}",
        impersonate="chrome", timeout=15,
        headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
    )
    if r.status_code == 200:
        txt = r.text
        if txt.startswith("{"):
            j = json.loads(txt)
            products = j.get("data",{}).get("products",[])
            wb_prices = []
            if products:
                p = products[0]
                price_obj = p.get("price",{})
                wb_prices = [
                    price_obj.get("basic",0), price_obj.get("percent",0),
                    p.get("salepriceU",0), p.get("priceU",0),
                    p.get("price_v2",0), p.get("price_v2_old",0)
                ]
            save("WB:cffi_card_v2", {
                "ok": len(wb_prices) > 0 and any(x > 0 for x in wb_prices),
                "detail": f"HTTP=200 n={len(products)} prices={wb_prices[:6]} name={products[0].get('name','')[:50] if products else '?'}",
                "s": round(time.time()-t0,1)
            })
            if products:
                with open(os.path.join(BASE, "d7_wb_card.json"), "w", encoding="utf-8") as f:
                    json.dump(products[0], f, ensure_ascii=False, indent=2)
        else:
            save("WB:cffi_card_v2", {
                "ok": False,
                "detail": f"HTTP=200 but not JSON, first100={txt[:100]}",
                "s": round(time.time()-t0,1)
            })
    else:
        save("WB:cffi_card_v2", {"ok": False, "detail": f"HTTP={r.status_code} len={len(r.text)}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("WB:cffi_card_v2", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB2: requests card_v2 с cookie warmup
try:
    ws = requests.Session()
    ws.headers.update(HDRS)
    ws.get("https://www.wildberries.ru/", timeout=10)
    r = ws.get(f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}", timeout=15,
               headers={"Accept": "application/json"})
    if r.status_code == 200 and r.text.startswith("{"):
        j = r.json()
        products = j.get("data",{}).get("products",[])
        wb_prices2 = []
        if products:
            p = products[0]
            po = p.get("price",{})
            wb_prices2 = [po.get("basic",0), po.get("percent",0), p.get("salepriceU",0), p.get("priceU",0)]
        save("WB:requests_card_v2", {
            "ok": len(wb_prices2) > 0 and any(x > 0 for x in wb_prices2),
            "detail": f"HTTP=200 n={len(products)} prices={wb_prices2[:6]}",
            "s": round(time.time()-t0,1)
        })
    else:
        save("WB:requests_card_v2", {
            "ok": False,
            "detail": f"HTTP={r.status_code} first100={r.text[:100]}",
            "s": round(time.time()-t0,1)
        })
except Exception as e:
    save("WB:requests_card_v2", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB3: basket CDN card.json (уже работал в dfv3)
try:
    r = requests.get(
        f"https://basket-42.wbbasket.ru/vol10979/part1097/1097975275/info/ru.json",
        timeout=10, headers=HDRS
    )
    if r.status_code == 200:
        j = r.json()
        selling = j.get("selling",{})
        save("WB:basket_card", {
            "ok": True,
            "detail": f"HTTP=200 keys={list(j.keys())[:8]} selling={str(selling)[:100]} len={len(r.text)}",
            "s": round(time.time()-t0,1)
        })
    else:
        save("WB:basket_card", {"ok": False, "detail": f"HTTP={r.status_code}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("WB:basket_card", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# OZON — Playwright
# ============================================================
log.info("=== OZON (Playwright) ===")
OZ_URL = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=UA,
            locale="ru-RU",
            viewport={"width": 1366, "height": 768},
        )
        page = ctx.new_page()
        page.goto(OZ_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        html = page.content()
        prices = extract_prices(html)
        has_ld = bool(re.search(r'application/ld\+json', html, re.I))
        
        # JSON-LD specifically
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S|re.I)
        ld_info = []
        for lb in ld_blocks:
            try:
                d = json.loads(lb.strip())
                items = d if isinstance(d, list) else [d]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        off = item.get("offers",{})
                        ld_info.append({
                            "name": item.get("name","")[:60],
                            "price": off.get("price") if isinstance(off,dict) else None,
                            "currency": off.get("priceCurrency") if isinstance(off,dict) else None,
                            "avail": off.get("availability","")[-20:] if isinstance(off,dict) else ""
                        })
            except: pass
        
        with open(os.path.join(BASE, "d7_ozon_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        save("OZ:playwright", {
            "ok": len(prices) > 0,
            "detail": f"len={len(html)} prices={prices[:5]} ld={has_ld} ld_info={ld_info[:2]}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("OZ:playwright", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# AVITO — Playwright mobile
# ============================================================
log.info("=== AVITO (Playwright) ===")
AV_URL = "https://www.avito.ru/vladivostok/1154321987"
AV_MOBILE_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=AV_MOBILE_UA,
            locale="ru-RU",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
        )
        page = ctx.new_page()
        page.goto(AV_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        html = page.content()
        prices = extract_prices(html)
        
        # Avito-specific: data-attribute, meta
        av_price_m = re.search(r'data-price=["\']?(\d+)', html, re.I)
        av_meta_m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        
        with open(os.path.join(BASE, "d7_avito_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        save("AV:playwright_mobile", {
            "ok": len(prices) > 0 or av_price_m or av_meta_m,
            "detail": f"len={len(html)} prices={prices[:5]} data_price={av_price_m.group(1) if av_price_m else None} meta={av_meta_m.group(1) if av_meta_m else None}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("AV:playwright_mobile", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# SAVE
# ============================================================
out = {"time": datetime.now().isoformat(), "total_s": round(time.time()-t0,1), "results": results}
out_path = os.path.join(BASE, "d7_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log.info("Saved to %s", out_path)
log.info("TOTAL: %.1fs", time.time()-t0)
