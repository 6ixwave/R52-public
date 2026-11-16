#!/usr/bin/env python3
"""diag_multi6.py — быстрая проверка стратегий для 4 площадок."""
import json, re, time, logging, os, sys
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d6")

BASE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}

results = {}
t0 = time.time()

def save(name, data):
    results[name] = data
    log.info("  -> %s: ok=%s %s", name, data.get("ok"), str(data.get("detail",""))[:200])

def extract_prices_html(html):
    """Ищем цену в HTML: JSON-LD, meta, data-атрибуты, классы."""
    prices = []
    # JSON-LD
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            ld = json.loads(m.group(1).strip())
            if isinstance(ld, list):
                for item in ld:
                    _ld_price(item, prices)
            else:
                _ld_price(ld, prices)
        except: pass
    # og:price
    for m in re.finditer(r'property=["\']og:price:amount["\']\s+content=["\']([\d.]+)["\']', html, re.I):
        prices.append(float(m.group(1)))
    # data-price / data-price-value
    for m in re.finditer(r'data-price(?:-value)?=["\']([\d]+)["\']', html, re.I):
        prices.append(float(m.group(1)))
    # class="price" or similar
    for m in re.finditer(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>([\d\s\u2009\u00a0]+)', html, re.I):
        cleaned = re.sub(r'[\s\u2009\u00a0]', '', m.group(1))
        try: prices.append(float(cleaned))
        except: pass
    # "price": 123 in inline JSON
    for m in re.finditer(r'"price"\s*:\s*"?([\d]+)"?', html):
        prices.append(float(m.group(1)))
    # "amount": "123"
    for m in re.finditer(r'"amount"\s*:\s*"?([\d]+)"?', html):
        prices.append(float(m.group(1)))
    return prices

def _ld_price(obj, prices):
    if isinstance(obj, dict):
        t = obj.get("@type","")
        if t in ("Offer","Product"):
            off = obj.get("offers", obj)
            if isinstance(off, dict):
                p = off.get("price") or off.get("lowPrice")
                if p:
                    try: prices.append(float(p))
                    except: pass
            if isinstance(off, list):
                for o in off:
                    p = o.get("price")
                    if p:
                        try: prices.append(float(p))
                        except: pass
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _ld_price(v, prices)
    elif isinstance(obj, list):
        for i in obj:
            _ld_price(i, prices)

# ============================================================
# FARPOST
# ============================================================
log.info("=== FARPOST ===")
FP_ID = "138952176"
FP_URL = f"https://www.farpost.ru/vladivostok/{FP_ID}.html"

# FP1: requests с заголовками, полный HTML
try:
    import requests
    s = requests.Session()
    s.headers.update(HDRS)
    # warm-up
    try:
        s.get("https://www.farpost.ru/", timeout=10)
    except: pass
    r = s.get(FP_URL, timeout=15)
    html = r.text
    prices = extract_prices_html(html)
    # Ищем конкретные паттерны
    has_ld = bool(re.search(r'application/ld\+json', html, re.I))
    has_og = bool(re.search(r'og:price', html, re.I))
    has_data = bool(re.search(r'data-price', html, re.I))
    has_class = bool(re.search(r'class=["\'][^"\']*price', html, re.I))
    # Сохраняем HTML для анализа
    fp_html_path = os.path.join(BASE, "d6_fp_html.html")
    with open(fp_html_path, "w", encoding="utf-8") as f:
        f.write(html)
    save("FP:html_full", {
        "ok": len(prices) > 0,
        "detail": f"HTTP={r.status_code} len={len(html)} prices={prices[:5]} ld={has_ld} og={has_og} data={has_data} cls={has_class}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:html_full", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP2: API farpost
try:
    r2 = s.get(f"https://api.farpost.ru/v1/bulletins/{FP_ID}", timeout=10,
               headers={**HDRS, "Accept": "application/json"})
    j2 = r2.json() if r2.status_code == 200 else {}
    p2 = j2.get("price") or j2.get("data",{}).get("price")
    save("FP:api_v1", {
        "ok": r2.status_code == 200 and p2 is not None,
        "detail": f"HTTP={r2.status_code} price={p2} keys={list(j2.keys())[:8]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:api_v1", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP3: ajax bulletin
try:
    r3 = s.get(f"https://www.farpost.ru/ajax/bulletin/{FP_ID}", timeout=10,
               headers={**HDRS, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript"})
    prices3 = extract_prices_html(r3.text)
    save("FP:ajax", {
        "ok": r3.status_code == 200 and (len(prices3) > 0 or "price" in r3.text.lower()),
        "detail": f"HTTP={r3.status_code} len={len(r3.text)} prices={prices3[:5]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:ajax", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP4: curl_cffi
try:
    from curl_cffi import requests as cffi_req
    r4 = cffi_req.get(FP_URL, impersonate="chrome", timeout=15)
    prices4 = extract_prices_html(r4.text)
    save("FP:cffi", {
        "ok": r4.status_code == 200 and len(prices4) > 0,
        "detail": f"HTTP={r4.status_code} len={len(r4.text)} prices={prices4[:5]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:cffi", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# WILDBERRIES
# ============================================================
log.info("=== WILDBERRIES ===")
WB_NM = "1097975275"

# WB1: card.wb.ru/cards/v2/detail
try:
    r = requests.get(f"https://card.wb.ru/cards/v2/detail?nm={WB_NM}", timeout=15, headers=HDRS)
    j = r.json()
    products = j.get("data",{}).get("products",[])
    prices_wb = []
    if products:
        p = products[0]
        prices_wb = [p.get("price",{}).get("basic",0), p.get("price",{}).get("percent",0),
                     p.get("salepriceU",0), p.get("priceU",0)]
        # Также смотрим price_v2, price_v2_old
        pv2 = p.get("price_v2", 0)
        pv2o = p.get("price_v2_old", 0)
        prices_wb.extend([pv2, pv2o])
    save("WB:card_v2", {
        "ok": r.status_code == 200 and len(prices_wb) > 0 and any(x > 0 for x in prices_wb if x),
        "detail": f"HTTP={r.status_code} n_prod={len(products)} prices={prices_wb[:6]} name={products[0].get('name','')[:50] if products else '?'}",
        "s": round(time.time()-t0,1)
    })
    if products:
        with open(os.path.join(BASE, "d6_wb_card_v2.json"), "w", encoding="utf-8") as f:
            json.dump(products[0], f, ensure_ascii=False, indent=2)
except Exception as e:
    save("WB:card_v2", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB2: basket CDN — ищем vol по nm_id
try:
    # Формула: vol = nm // 100000, остаток = nm % 100000
    vol = int(WB_NM) // 100000
    rest = int(WB_NM) % 100000
    # Пробуем несколько basket host'ов
    for host_num in [42, 10, 20, 30]:
        url = f"https://basket-{host_num}.wbbasket.ru/vol{vol:03d}/part{rest//1000:02d}/{rest//100:02d}/{WB_NM}/info/ru.json"
        try:
            rb = requests.get(url, timeout=8, headers=HDRS)
            if rb.status_code == 200:
                jb = rb.json()
                save(f"WB:basket_{host_num}", {
                    "ok": True,
                    "detail": f"HTTP=200 keys={list(jb.keys())[:10]} len={len(rb.text)}",
                    "s": round(time.time()-t0,1)
                })
                with open(os.path.join(BASE, f"d6_wb_basket_{host_num}.json"), "w", encoding="utf-8") as f:
                    f.write(rb.text)
                break
            else:
                save(f"WB:basket_{host_num}", {
                    "ok": False,
                    "detail": f"HTTP={rb.status_code}",
                    "s": round(time.time()-t0,1)
                })
        except Exception as e2:
            save(f"WB:basket_{host_num}", {"ok": False, "detail": f"err={e2}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("WB:basket", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB3: static-basket
try:
    vol = int(WB_NM) // 100000
    for host_num in [42, 10, 20, 30]:
        url = f"https://static-basket-{host_num}.wbbasket.ru/vol{vol:03d}/info/{WB_NM}.json"
        try:
            rs = requests.get(url, timeout=8, headers=HDRS)
            if rs.status_code == 200:
                js = rs.json()
                save(f"WB:static_{host_num}", {
                    "ok": True,
                    "detail": f"HTTP=200 keys={list(js.keys())[:10]} len={len(rs.text)}",
                    "s": round(time.time()-t0,1)
                })
                break
        except: pass
except Exception as e:
    save("WB:static", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB4: HTML страница WB (SSR)
try:
    r = requests.get(f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx", timeout=15, headers=HDRS)
    prices_wb4 = extract_prices_html(r.text)
    save("WB:html_ssr", {
        "ok": r.status_code == 200 and len(prices_wb4) > 0,
        "detail": f"HTTP={r.status_code} len={len(r.text)} prices={prices_wb4[:5]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("WB:html_ssr", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# OZON
# ============================================================
log.info("=== OZON ===")
OZ_SKU = "259671175"
OZ_SLUG = "malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800"

# OZ1: HTML с browser headers
try:
    oz_hdrs = {
        **HDRS,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    r = requests.get(f"https://www.ozon.ru/product/{OZ_SLUG}/", timeout=15, headers=oz_hdrs)
    prices_oz = extract_prices_html(r.text)
    has_ld = bool(re.search(r'application/ld\+json', r.text, re.I))
    save("OZ:html", {
        "ok": r.status_code == 200 and len(prices_oz) > 0,
        "detail": f"HTTP={r.status_code} len={len(r.text)} prices={prices_oz[:5]} ld={has_ld}",
        "s": round(time.time()-t0,1)
    })
    if r.status_code == 200:
        with open(os.path.join(BASE, "d6_ozon_html.html"), "w", encoding="utf-8") as f:
            f.write(r.text)
except Exception as e:
    save("OZ:html", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# OZ2: Composer API (www.ozon.ru, не api.ozon.ru)
try:
    r = requests.get(
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{OZ_SKU}/",
        timeout=15,
        headers={**HDRS, "Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{OZ_SKU}/"}
    )
    if r.status_code == 200:
        j = r.json()
        # Ищем цену в JSON
        oz_price = None
        def find_price(obj, depth=0):
            if depth > 8: return None
            if isinstance(obj, dict):
                if "price" in obj and isinstance(obj["price"], (int, float)):
                    return obj["price"]
                for v in obj.values():
                    p = find_price(v, depth+1)
                    if p: return p
            elif isinstance(obj, list):
                for i in obj:
                    p = find_price(i, depth+1)
                    if p: return p
            return None
        oz_price = find_price(j)
        save("OZ:composer_www", {
            "ok": oz_price is not None,
            "detail": f"HTTP={r.status_code} price={oz_price} len={len(r.text)}",
            "s": round(time.time()-t0,1)
        })
        with open(os.path.join(BASE, "d6_ozon_composer.json"), "w", encoding="utf-8") as f:
            f.write(r.text[:50000])
    else:
        save("OZ:composer_www", {
            "ok": False,
            "detail": f"HTTP={r.status_code} len={len(r.text)}",
            "s": round(time.time()-t0,1)
        })
except Exception as e:
    save("OZ:composer_www", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# OZ3: curl_cffi (обходит SSL-проблемы requests)
try:
    from curl_cffi import requests as cffi_req
    r = cffi_req.get(f"https://www.ozon.ru/product/{OZ_SLUG}/", impersonate="chrome", timeout=15)
    prices_oz3 = extract_prices_html(r.text)
    save("OZ:cffi_html", {
        "ok": r.status_code == 200 and len(prices_oz3) > 0,
        "detail": f"HTTP={r.status_code} len={len(r.text)} prices={prices_oz3[:5]}",
        "s": round(time.time()-t0,1)
    })
    if r.status_code == 200:
        with open(os.path.join(BASE, "d6_ozon_cffi.html"), "w", encoding="utf-8") as f:
            f.write(r.text)
except Exception as e:
    save("OZ:cffi_html", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# OZ4: curl_cffi composer
try:
    from curl_cffi import requests as cffi_req
    r = cffi_req.get(
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{OZ_SKU}/",
        impersonate="chrome", timeout=15,
        headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{OZ_SKU}/"}
    )
    if r.status_code == 200:
        j = r.json()
        oz_price4 = None
        def find_price4(obj, depth=0):
            if depth > 8: return None
            if isinstance(obj, dict):
                if "price" in obj and isinstance(obj["price"], (int, float)):
                    return obj["price"]
                for v in obj.values():
                    p = find_price4(v, depth+1)
                    if p: return p
            elif isinstance(obj, list):
                for i in obj:
                    p = find_price4(i, depth+1)
                    if p: return p
            return None
        oz_price4 = find_price4(j)
        save("OZ:cffi_composer", {
            "ok": oz_price4 is not None,
            "detail": f"HTTP={r.status_code} price={oz_price4} len={len(r.text)}",
            "s": round(time.time()-t0,1)
        })
    else:
        save("OZ:cffi_composer", {"ok": False, "detail": f"HTTP={r.status_code}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("OZ:cffi_composer", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# AVITO
# ============================================================
log.info("=== AVITO ===")
AV_ID = "1154321987"

# AV1: mobile page
try:
    av_hdrs = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    r = requests.get(f"https://www.avito.ru/vladivostok/{AV_ID}", timeout=15, headers=av_hdrs)
    prices_av = extract_prices_html(r.text)
    save("AV:mobile", {
        "ok": r.status_code == 200 and len(prices_av) > 0,
        "detail": f"HTTP={r.status_code} len={len(r.text)} prices={prices_av[:5]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("AV:mobile", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# AV2: API offer
try:
    r = requests.get(f"https://www.avito.ru/api/offer/{AV_ID}", timeout=10,
                     headers={**HDRS, "Accept": "application/json"})
    if r.status_code == 200:
        j = r.json()
        p = j.get("price") or j.get("data",{}).get("price")
        save("AV:api_offer", {
            "ok": p is not None,
            "detail": f"HTTP=200 price={p} keys={list(j.keys())[:8]}",
            "s": round(time.time()-t0,1)
        })
    else:
        save("AV:api_offer", {"ok": False, "detail": f"HTTP={r.status_code}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("AV:api_offer", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# AV3: curl_cffi desktop
try:
    from curl_cffi import requests as cffi_req
    r = cffi_req.get(f"https://www.avito.ru/vladivostok/{AV_ID}", impersonate="chrome", timeout=15)
    prices_av3 = extract_prices_html(r.text)
    save("AV:cffi_desktop", {
        "ok": r.status_code == 200 and len(prices_av3) > 0,
        "detail": f"HTTP={r.status_code} len={len(r.text)} prices={prices_av3[:5]}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("AV:cffi_desktop", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# AV4: search API (найти живое объявление)
try:
    r = requests.get(
        f"https://www.avito.ru/api/search?query=телефон&city=vladivostok&limit=5",
        timeout=10, headers={**HDRS, "Accept": "application/json"}
    )
    if r.status_code == 200:
        j = r.json()
        items = j.get("items", j.get("data", j.get("offers", [])))
        if isinstance(items, list) and items:
            first = items[0]
            save("AV:search_api", {
                "ok": True,
                "detail": f"HTTP=200 n={len(items)} first_id={first.get('id','?')} price={first.get('price','?')}",
                "s": round(time.time()-t0,1)
            })
        else:
            save("AV:search_api", {"ok": False, "detail": f"HTTP=200 but no items, keys={list(j.keys())[:8]}", "s": round(time.time()-t0,1)})
    else:
        save("AV:search_api", {"ok": False, "detail": f"HTTP={r.status_code}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("AV:search_api", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# SAVE
# ============================================================
out = {
    "time": datetime.now().isoformat(),
    "total_s": round(time.time() - t0, 1),
    "results": results
}
out_path = os.path.join(BASE, "d6_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log.info("Saved to %s", out_path)
log.info("TOTAL: %.1fs", time.time() - t0)
