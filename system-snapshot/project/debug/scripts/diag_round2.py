"""Диагностика 2: живой Farpost + OZON via curl_cffi + WB basket."""
import json, time, re, sys
from pathlib import Path

OUT = Path(__file__).parent.parent / "outputs" / "diag_raw"
OUT.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": UA,
    "Referer": "https://www.farpost.ru/",
}

result = {}

# ─── 1. FARPOST: найти живой товар ───
print("[1] FARPOST: поиск живого товара …", flush=True)
try:
    import requests
    s = requests.Session()
    s.headers.update(H)
    # Категория "Телефоны" — много живых объявлений
    r = s.get("https://www.farpost.ru/vladivostok/phone", timeout=15)
    print(f"  Search page: {r.status_code} len={len(r.text)}", flush=True)
    # Ищем bulletin-ссылки: /vladivostok/123456789.html
    links = re.findall(r'href="(/vladivostok/\d{6,}\.html)"', r.text)
    ids = list(dict.fromkeys(links))  # unique, preserve order
    print(f"  Found {len(ids)} bulletin links", flush=True)
    
    live_found = None
    for lid in ids[:8]:
        r2 = s.get(f"https://www.farpost.ru{lid}", timeout=10)
        ct = (r2.headers.get("Content-Type") or "").lower()
        if "1251" in ct:
            h = r2.content.decode("windows-1251", errors="replace")
        else:
            h = r2.text
        has_ld = 'ld+json' in h
        has_price = 'data-bulletin-price' in h or 'viewbull-summary-price' in h
        has_og = 'og:title' in h
        is_404 = r2.status_code == 404
        print(f"  {lid}: {r2.status_code} len={len(h)} ld={has_ld} price={has_price} og={has_og}", flush=True)
        if r2.status_code == 200 and (has_price or has_ld) and len(h) > 3000:
            live_found = lid
            result["farpost_live"] = {
                "id": lid.lstrip("/vladivostok/").replace(".html", ""),
                "url": f"https://www.farpost.ru{lid}",
                "status": r2.status_code,
                "html_len": len(h),
                "has_jsonld": has_ld,
                "has_price": has_price,
                "has_og": has_og,
                "html_head_2000": h[:2000],
            }
            break
        time.sleep(0.5)
    
    if not live_found:
        result["farpost_live"] = {"error": "no live product found in first 8"}
except Exception as e:
    result["farpost_live"] = {"error": str(e)}
    print(f"  ERROR: {e}", flush=True)

# ─── 2. OZON via curl_cffi ───
print("\n[2] OZON via curl_cffi …", flush=True)
try:
    from curl_cffi import requests as cffi_req
    ozon_ids = ["562267"]  # текущий тестовый
    for oid in ozon_ids:
        # 2a. Composer API
        comp_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
        t0 = time.monotonic()
        rc = cffi_req.get(comp_url, impersonate="chrome", timeout=20,
                          headers={**H, "Accept": "application/json"}, verify=False)
        dt = round(time.monotonic() - t0, 2)
        print(f"  Composer {oid}: {rc.status_code} len={len(rc.text)} dt={dt}s", flush=True)
        result[f"ozon_composer_{oid}"] = {
            "status": rc.status_code, "dt": dt, "len": len(rc.text),
            "head_2000": rc.text[:2000],
        }
        time.sleep(1)
        
        # 2b. Entry-point
        ep_url = f"https://www.ozon.ru/api/entrypoint/pdp/{oid}"
        t0 = time.monotonic()
        re_ = cffi_req.get(ep_url, impersonate="chrome", timeout=20,
                           headers={**H, "Accept": "application/json"}, verify=False)
        dt2 = round(time.monotonic() - t0, 2)
        print(f"  Entrypoint {oid}: {re_.status_code} len={len(re_.text)} dt={dt2}s", flush=True)
        result[f"ozon_entrypoint_{oid}"] = {
            "status": re_.status_code, "dt": dt2, "len": len(re_.text),
            "head_2000": re_.text[:2000],
        }
        time.sleep(1)
        
        # 2c. HTML page
        t0 = time.monotonic()
        rh = cffi_req.get(f"https://www.ozon.ru/product/{oid}/", impersonate="chrome",
                          timeout=30, headers=H, verify=False)
        dt3 = round(time.monotonic() - t0, 2)
        html = rh.text
        print(f"  HTML {oid}: {rh.status_code} len={len(html)} dt={dt3}s", flush=True)
        result[f"ozon_html_{oid}"] = {
            "status": rh.status_code, "dt": dt3, "len": len(html),
            "has_jsonld": 'ld+json' in html,
            "has_og_title": 'og:title' in html,
            "has_webprice": 'webPrice' in html,
            "has_state": '__STATE__' in html or 'data-widget' in html,
            "has_error_widget": 'data-widget="error"' in html,
            "has_title_tag": '<title' in html,
            "head_3000": html[:3000],
        }
except Exception as e:
    result["ozon_cffi_error"] = str(e)
    print(f"  ERROR: {e}", flush=True)

# ─── 3. WB basket (правильный host) ───
print("\n[3] WB basket …", flush=True)
try:
    nm_id = 1097975275
    # Правильная формула: vol = nm_id // 10000000, part = nm_id // 1000000
    vol = nm_id // 10000000
    part = nm_id // 1000000
    sub = nm_id // 1000
    # basket host: basket-{nm_id // 100000}
    host = nm_id // 100000
    basket_url = f"https://basket-{host}.wbbasket.ru/vol{vol}/part{part}/{sub}/info/ru/card.json"
    print(f"  URL: {basket_url}", flush=True)
    
    import requests as req2
    s2 = req2.Session()
    s2.headers.update({**H, "Accept": "application/json"})
    t0 = time.monotonic()
    rb = s2.get(basket_url, timeout=15, allow_redirects=True)
    dt = round(time.monotonic() - t0, 2)
    print(f"  Basket: {rb.status_code} len={len(rb.text)} dt={dt}s", flush=True)
    result["wb_basket"] = {
        "url": basket_url, "status": rb.status_code, "dt": dt,
        "len": len(rb.text), "head_2000": rb.text[:2000],
    }
    # Проверка: есть selling.price?
    try:
        j = rb.json()
        selling = j.get("selling", {})
        result["wb_basket"]["has_selling"] = bool(selling)
        result["wb_basket"]["selling_price"] = selling.get("price")
        result["wb_basket"]["selling_priceU"] = selling.get("priceU")
        result["wb_basket"]["imt_name"] = j.get("imt_name", "")[:80]
        result["wb_basket"]["sizes_count"] = len(j.get("sizes", []))
    except Exception as e2:
        result["wb_basket"]["json_parse_error"] = str(e2)
except Exception as e:
    result["wb_basket"] = {"error": str(e)}
    print(f"  ERROR: {e}", flush=True)

# ─── Write ───
out_file = OUT / "diag_round2.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nDone → {out_file}", flush=True)
