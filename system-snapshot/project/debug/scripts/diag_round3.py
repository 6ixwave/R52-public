"""Диагностика 3: Farpost живой товар + OZON Playwright + WB basket."""
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
}

result = {}

# ─── 1. FARPOST: несколько URL поиска ───
print("[1] FARPOST: поиск живого товара …", flush=True)
try:
    import requests
    s = requests.Session()
    s.headers.update(H)
    s.headers["Referer"] = "https://www.farpost.ru/"

    search_urls = [
        "https://www.farpost.ru/vladivostok/",
        "https://www.farpost.ru/vladivostok/telefony",
        "https://www.farpost.ru/vladivostok/elektronika",
        "https://www.farpost.ru/vladivostok/nedvizhimost",
        "https://www.farpost.ru/vladivostok/avto",
    ]
    
    for su in search_urls:
        try:
            r = s.get(su, timeout=12)
            ct = (r.headers.get("Content-Type") or "").lower()
            if "1251" in ct:
                h = r.content.decode("windows-1251", errors="replace")
            else:
                h = r.text
            links = re.findall(r'href="(/vladivostok/\d{6,}\.html)"', h)
            ids = list(dict.fromkeys(links))
            print(f"  {su}: {r.status_code} len={len(h)} links={len(ids)}", flush=True)
            if r.status_code == 200 and len(ids) >= 3:
                # Проверить первые 3 на живость
                for lid in ids[:3]:
                    r2 = s.get(f"https://www.farpost.ru{lid}", timeout=10)
                    ct2 = (r2.headers.get("Content-Type") or "").lower()
                    if "1251" in ct2:
                        h2 = r2.content.decode("windows-1251", errors="replace")
                    else:
                        h2 = r2.text
                    has_ld = 'ld+json' in h2
                    has_price = 'data-bulletin-price' in h2 or 'viewbull-summary-price' in h2
                    has_og = 'og:title' in h2
                    print(f"    {lid}: {r2.status_code} len={len(h2)} ld={has_ld} price={has_price} og={has_og}", flush=True)
                    if r2.status_code == 200 and (has_price or has_ld) and len(h2) > 3000:
                        result["farpost_live"] = {
                            "id": lid.lstrip("/vladivostok/").replace(".html", ""),
                            "url": f"https://www.farpost.ru{lid}",
                            "status": r2.status_code,
                            "html_len": len(h2),
                            "has_jsonld": has_ld,
                            "has_price": has_price,
                            "has_og": has_og,
                            "html_head_3000": h2[:3000],
                        }
                        print("    *** LIVE FOUND ***", flush=True)
                        break
                    time.sleep(0.3)
                if "farpost_live" in result:
                    break
        except Exception as e:
            print(f"  {su}: ERROR {e}", flush=True)
        time.sleep(0.5)
    
    if "farpost_live" not in result:
        result["farpost_live"] = {"error": "no live product found"}
except Exception as e:
    result["farpost_live"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── 2. OZON via Playwright ───
print("\n[2] OZON via Playwright …", flush=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA,
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        t0 = time.monotonic()
        resp = page.goto("https://www.ozon.ru/product/562267/",
                         wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)  # дать JS отработать
        dt = round(time.monotonic() - t0, 2)
        html = page.content()
        status = resp.status if resp else 0
        print(f"  OZON PW: status={status} len={len(html)} dt={dt}s", flush=True)
        
        result["ozon_pw"] = {
            "status": status,
            "dt": dt,
            "html_len": len(html),
            "has_jsonld": 'ld+json' in html,
            "has_og_title": 'og:title' in html,
            "has_webprice": 'webPrice' in html,
            "has_state": '__STATE__' in html or 'data-widget' in html,
            "has_h1": '<h1' in html,
            "has_price_text": bool(re.search(r'\d[\d\s]*₽', html)),
            "html_head_3000": html[:3000],
            "title_tag": (re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) or [None, ""])[1],
        }
        # Извлечь h1
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if h1:
            result["ozon_pw"]["h1"] = re.sub(r'<[^>]+>', '', h1.group(1)).strip()[:100]
        # Извлечь цены
        prices = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html[:50000])
        result["ozon_pw"]["prices_found"] = [p.replace(" ", "").replace("\u00a0", "") for p in prices[:10]]
        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        result["ozon_pw"]["jsonld_count"] = len(ld_blocks)
        for i, lb in enumerate(ld_blocks[:3]):
            try:
                j = json.loads(lb)
                t = j.get("@type", "?") if isinstance(j, dict) else "list"
                result["ozon_pw"][f"jsonld_{i}_type"] = str(t)
                if t == "Product":
                    result["ozon_pw"][f"jsonld_{i}_name"] = str(j.get("name", ""))[:80]
                    result["ozon_pw"][f"jsonld_{i}_offers"] = str(j.get("offers", ""))[:200]
            except Exception:
                result["ozon_pw"][f"jsonld_{i}_parse"] = "error"
        
        browser.close()
except Exception as e:
    result["ozon_pw"] = {"error": str(e)}
    print(f"  ERROR: {e}", flush=True)

# ─── 3. WB basket (несколько формул) ───
print("\n[3] WB basket …", flush=True)
try:
    nm_id = 1097975275
    import requests as req2
    s2 = req2.Session()
    s2.headers.update({**H, "Accept": "application/json"})
    
    # Формула 1: basket-{nm//100000}.wbbasket.ru/vol{nm//10M}/part{nm//1M}/{nm//1000}/info/ru/card.json
    vol = nm_id // 10000000
    part = nm_id // 1000000
    sub = nm_id // 1000
    host1 = nm_id // 100000
    url1 = f"https://basket-{host1}.wbbasket.ru/vol{vol}/part{part}/{sub}/info/ru/card.json"
    
    # Формула 2: basket-{nm//100000}.wbbasket.ru (без vol/part)
    url2 = f"https://basket-{host1}.wbbasket.ru/vol{vol}/part{part}/{sub}/info/ru/card.json"
    
    # Формула 3: static-basket (альтернативный CDN)
    url3 = f"https://static-basket-{host1}.wbbasket.ru/vol{vol}/part{part}/{sub}/info/ru/card.json"
    
    for label, u in [("host1", url1), ("static", url3)]:
        try:
            t0 = time.monotonic()
            rb = s2.get(u, timeout=12, allow_redirects=True)
            dt = round(time.monotonic() - t0, 2)
            print(f"  {label}: {rb.status_code} len={len(rb.text)} dt={dt}s", flush=True)
            result[f"wb_basket_{label}"] = {
                "url": u, "status": rb.status_code, "dt": dt,
                "len": len(rb.text), "head_2000": rb.text[:2000],
            }
            try:
                j = rb.json()
                result[f"wb_basket_{label}"]["has_selling"] = bool(j.get("selling"))
                result[f"wb_basket_{label}"]["selling_price"] = j.get("selling", {}).get("price")
                result[f"wb_basket_{label}"]["imt_name"] = j.get("imt_name", "")[:80]
                result[f"wb_basket_{label}"]["sizes_count"] = len(j.get("sizes", []))
            except Exception as e2:
                result[f"wb_basket_{label}"]["json_err"] = str(e2)
        except Exception as e:
            print(f"  {label}: ERROR {e}", flush=True)
            result[f"wb_basket_{label}"] = {"url": u, "error": str(e)}
        time.sleep(0.5)
except Exception as e:
    result["wb_basket"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── Write ───
out_file = OUT / "diag_round3.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nDone → {out_file}", flush=True)
