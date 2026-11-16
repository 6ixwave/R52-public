"""Диагностика: сырые ответы от Farpost, OZON, WB price-API.
Дамп: заголовки + первые 3000 байт + первые 2000 символов текста → JSON-файл.
"""
import json, time, sys, os
from pathlib import Path

OUT = Path(__file__).parent.parent / "outputs" / "diag_raw"
OUT.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": UA,
}

result = {}

# ─── 1. FARPOST ───
print("[1/3] FARPOST …", flush=True)
try:
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    s.headers["Referer"] = "https://www.farpost.ru/"
    t0 = time.monotonic()
    r = s.get("https://www.farpost.ru/vladivostok/1154321987.html",
              timeout=(10, 30), allow_redirects=True)
    dt = round(time.monotonic() - t0, 2)
    # decode
    ct = (r.headers.get("Content-Type") or "").lower()
    if "1251" in ct or "cp1251" in ct:
        html = r.content.decode("windows-1251", errors="replace")
    else:
        head500 = r.content[:500].decode("ascii", errors="ignore")
        if "1251" in head500.lower():
            html = r.content.decode("windows-1251", errors="replace")
        else:
            html = r.text
    result["farpost"] = {
        "status": r.status_code,
        "elapsed": dt,
        "final_url": str(r.url),
        "headers": dict(r.headers),
        "content_length": len(r.content),
        "html_len": len(html),
        "html_head_3000": html[:3000],
        "has_jsonld": 'application/ld+json' in html,
        "has_og_title": 'og:title' in html,
        "has_data_bulletin_price": 'data-bulletin-price' in html,
        "has_h1_subject": 'class="subject' in html or 'data-field="subject"' in html,
        "has_viewbull_price": 'viewbull-summary-price' in html,
        "has_lightbox": 'lightbox' in html.lower(),
    }
    print(f"  status={r.status_code} len={len(html)} jsonld={result['farpost']['has_jsonld']} "
          f"og={result['farpost']['has_og_title']} dt={dt}s", flush=True)
except Exception as e:
    result["farpost"] = {"error": str(e)}
    print(f"  ERROR: {e}", flush=True)

# ─── 2. OZON (Composer API + HTML) ───
print("[2/3] OZON …", flush=True)
try:
    # 2a. Composer API
    comp_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/562267/"
    t0 = time.monotonic()
    r2 = s.get(comp_url, headers={**HEADERS, "Accept": "application/json"},
               timeout=(10, 20), allow_redirects=True)
    dt2 = round(time.monotonic() - t0, 2)
    comp_body = r2.text[:3000]
    result["ozon_composer"] = {
        "url": comp_url,
        "status": r2.status_code,
        "elapsed": dt2,
        "headers": dict(r2.headers),
        "body_head_3000": comp_body,
    }
    print(f"  composer status={r2.status_code} dt={dt2}s", flush=True)

    # 2b. Entry-point API
    ep_url = "https://www.ozon.ru/api/entrypoint/pdp/562267"
    t0 = time.monotonic()
    r3 = s.get(ep_url, headers={**HEADERS, "Accept": "application/json"},
               timeout=(10, 20), allow_redirects=True)
    dt3 = round(time.monotonic() - t0, 2)
    result["ozon_entrypoint"] = {
        "url": ep_url,
        "status": r3.status_code,
        "elapsed": dt3,
        "body_head_3000": r3.text[:3000],
    }
    print(f"  entrypoint status={r3.status_code} dt={dt3}s", flush=True)

    # 2c. HTML page
    t0 = time.monotonic()
    r4 = s.get("https://www.ozon.ru/product/562267/",
               headers=HEADERS, timeout=(10, 30), allow_redirects=True)
    dt4 = round(time.monotonic() - t0, 2)
    html4 = r4.text
    result["ozon_html"] = {
        "status": r4.status_code,
        "elapsed": dt4,
        "final_url": str(r4.url),
        "headers": dict(r4.headers),
        "html_len": len(html4),
        "html_head_3000": html4[:3000],
        "has_jsonld": 'application/ld+json' in html4,
        "has_og_title": 'og:title' in html4,
        "has_webprice": 'webPrice' in html4,
        "has_state": '__STATE__' in html4 or 'data-widget' in html4,
        "has_error_widget": 'data-widget="error"' in html4,
    }
    print(f"  html status={r4.status_code} len={len(html4)} "
          f"jsonld={result['ozon_html']['has_jsonld']} dt={dt4}s", flush=True)
except Exception as e:
    result["ozon_error"] = str(e)
    print(f"  ERROR: {e}", flush=True)

# ─── 3. WB price endpoints ───
print("[3/3] WB price …", flush=True)
nm_id = 1097975275
wb_endpoints = [
    ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
    ("search_wb", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
]
wb_results = []
for label, url in wb_endpoints:
    try:
        t0 = time.monotonic()
        r5 = s.get(url, headers={**HEADERS, "Accept": "application/json"},
                   timeout=(8, 15), allow_redirects=False)
        dt5 = round(time.monotonic() - t0, 2)
        body = r5.text[:2000]
        has_products = False
        try:
            j = r5.json()
            has_products = bool(j.get("products") or j.get("data"))
        except Exception:
            pass
        wb_results.append({
            "label": label, "status": r5.status_code, "elapsed": dt5,
            "has_data": has_products, "body_head": body,
        })
        print(f"  {label}: {r5.status_code} data={has_products} dt={dt5}s", flush=True)
        time.sleep(0.5)
    except Exception as e:
        wb_results.append({"label": label, "error": str(e)})
        print(f"  {label}: ERROR {e}", flush=True)
result["wb_price"] = wb_results

# ─── 4. WB basket card.json ───
print("  WB basket …", flush=True)
try:
    # basket: nm_id // 100000 → host
    host_num = nm_id // 100000
    basket_url = f"https://basket-{host_num}.wbbasket.ru/vol{nm_id // 10000000}/part{nm_id // 1000000}/{nm_id // 1000}/info/ru/card.json"
    t0 = time.monotonic()
    r6 = s.get(basket_url, headers=HEADERS, timeout=(8, 15), allow_redirects=False)
    dt6 = round(time.monotonic() - t0, 2)
    result["wb_basket"] = {
        "url": basket_url,
        "status": r6.status_code,
        "elapsed": dt6,
        "body_head": r6.text[:2000],
    }
    print(f"  basket: {r6.status_code} dt={dt6}s", flush=True)
except Exception as e:
    result["wb_basket"] = {"error": str(e)}
    print(f"  basket: ERROR {e}", flush=True)

# ─── Write ───
out_file = OUT / "diag_raw_dump.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nDone → {out_file}", flush=True)
