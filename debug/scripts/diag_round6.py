"""Диагностика 6: Farpost bulletin links + OZON mobile + WB real ID."""
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

# ─── 1. FARPOST: /vladivostok/auto/ — найти bulletin-ссылки ───
print("[1] FARPOST: category page bulletin links …", flush=True)
try:
    import requests
    s = requests.Session()
    s.headers.update(H)
    r = s.get("https://www.farpost.ru/vladivostok/auto/", timeout=20)
    ct = (r.headers.get("Content-Type") or "").lower()
    h = r.content.decode("windows-1251", errors="replace") if "1251" in ct else r.text
    print(f"  Auto: {r.status_code} len={len(h)}", flush=True)

    # Сохранить большой чанк body
    body_start = h.find("<body")
    if body_start == -1:
        body_start = 5000
    body_chunk = h[body_start:body_start + 40000]
    result["fp_auto_body"] = {
        "body_start": body_start,
        "chunk": body_chunk[:20000],
    }

    # Паттерны для bulletin-ссылок
    patterns = [
        (r'href="(/vladivostok/\d{6,}\.html)"', "vlad-num.html"),
        (r'href="(/vladivostok/[^/]+/\d{6,}\.html)"', "vlad-cat-num.html"),
        (r'href="(/[^"]*?\d{6,}\.html)"', "any-num.html"),
        (r'href="(/[^"]*?/b/\d+)"', "b-num"),
        (r'data-bulletin-id="(\d+)"', "data-bid"),
        (r'"bulletin_id"\s*:\s*(\d+)', "json-bid"),
        (r'"id"\s*:\s*(\d{8,})', "json-id-long"),
        (r'class="[^"]*viewbull[^"]*"', "viewbull-class"),
        (r'class="[^"]*bull[^"]*"[^>]*href="([^"]+)"', "bull-href"),
        (r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*(?:title|name|subject)[^"]*"', "title-link"),
    ]
    for pat, label in patterns:
        found = re.findall(pat, h, re.I)
        uniq = list(dict.fromkeys(found))
        print(f"  [{label}] {len(uniq)}: {uniq[:5]}", flush=True)
        if uniq:
            result[f"fp_auto_{label}"] = uniq[:20]

    # Если нашли — проверить 3 первых
    all_links = []
    for label in ["vlad-num.html", "vlad-cat-num.html", "any-num.html", "bull-href", "title-link"]:
        k = f"fp_auto_{label}"
        if k in result:
            all_links.extend(result[k])
    all_links = list(dict.fromkeys(all_links))
    print(f"  Total unique links: {len(all_links)}", flush=True)

    if all_links:
        for lid in all_links[:5]:
            full = lid if lid.startswith("http") else f"https://www.farpost.ru{lid}"
            try:
                r2 = s.get(full, timeout=12)
                ct2 = (r2.headers.get("Content-Type") or "").lower()
                h2 = r2.content.decode("windows-1251", errors="replace") if "1251" in ct2 else r2.text
                has_ld = 'ld+json' in h2
                has_price = ('data-bulletin-price' in h2 or
                             'viewbull-summary-price' in h2 or
                             'price' in h2.lower()[:5000])
                has_og = 'og:title' in h2
                is_404 = r2.status_code == 404
                print(f"  CHECK {full[:70]}: {r2.status_code} len={len(h2)} ld={has_ld} price={has_price} og={has_og}", flush=True)
                if r2.status_code == 200 and (has_ld or has_price) and len(h2) > 5000:
                    result["farpost_live_bulletin"] = {
                        "url": full,
                        "status": r2.status_code,
                        "html_len": len(h2),
                        "has_jsonld": has_ld,
                        "has_price": has_price,
                        "has_og": has_og,
                        "html_head_4000": h2[:4000],
                        "html_price_area": h2[4000:9000],
                    }
                    # Извлечь ID
                    id_match = re.search(r'(\d{6,})', lid)
                    if id_match:
                        result["farpost_live_bulletin"]["bulletin_id"] = id_match.group(1)
                    print("  *** BULLETIN LIVE ***", flush=True)
                    break
            except Exception as e:
                print(f"  CHECK ERR: {e}", flush=True)
            time.sleep(0.5)
    else:
        result["farpost_live_bulletin"] = {"error": "no bulletin links found"}
except Exception as e:
    result["fp_auto_body"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── 2. OZON: мобильный API + desktop HTML с другим товаром ───
print("\n[2] OZON: mobile + alternative …", flush=True)
try:
    from curl_cffi import requests as cffi_req

    # 2a. Mobile API (m.ozon.ru)
    mobile_urls = [
        "https://m.ozon.ru/product/562267/",
        "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/562267/&state=%7B%22isMobile%22%3Atrue%7D",
    ]
    for mu in mobile_urls:
        try:
            t0 = time.monotonic()
            rm = cffi_req.get(mu, impersonate="chrome", timeout=20,
                             headers={**H, "Accept": "application/json, text/html"},
                             verify=False)
            dt = round(time.monotonic() - t0, 2)
            print(f"  Mobile {mu[:50]}: {rm.status_code} len={len(rm.text)} dt={dt}s", flush=True)
            result[f"ozon_mobile_{mu[:30]}"] = {
                "url": mu, "status": rm.status_code, "dt": dt,
                "len": len(rm.text), "head_2000": rm.text[:2000],
            }
        except Exception as e:
            print(f"  Mobile ERR: {e}", flush=True)
        time.sleep(1)

    # 2b. Другой товар (популярный, чтобы не 404)
    # Попробуем iPhone 15 (большой SKU)
    alt_ids = ["251213073", "312345678"]  # известные крупные товары
    for aid in alt_ids[:2]:
        try:
            t0 = time.monotonic()
            ra = cffi_req.get(f"https://www.ozon.ru/product/{aid}/",
                             impersonate="chrome", timeout=25, headers=H, verify=False)
            dt = round(time.monotonic() - t0, 2)
            html = ra.text
            print(f"  Alt {aid}: {ra.status_code} len={len(html)} dt={dt}s "
                  f"jsonld={'ld+json' in html} og={'og:title' in html}", flush=True)
            result[f"ozon_alt_{aid}"] = {
                "status": ra.status_code, "dt": dt, "len": len(html),
                "has_jsonld": 'ld+json' in html,
                "has_og_title": 'og:title' in html,
                "has_webprice": 'webPrice' in html,
                "head_2000": html[:2000],
            }
        except Exception as e:
            print(f"  Alt {aid} ERR: {e}", flush=True)
        time.sleep(1)

    # 2c. Playwright: intercept XHR (Composer API из браузера)
    print("  Playwright XHR intercept …", flush=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=UA, locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        # Перехват XHR к composer-api
        xhr_data = []
        def on_response(resp):
            if 'composer-api' in resp.url or 'entrypoint' in resp.url:
                try:
                    body = resp.text()
                    xhr_data.append({"url": resp.url[:100], "status": resp.status, "len": len(body), "head": body[:1000]})
                except:
                    pass
        page.on("response", on_response)

        t0 = time.monotonic()
        try:
            page.goto("https://www.ozon.ru/product/562267/",
                     wait_until="domcontentloaded", timeout=30000)
        except:
            pass
        # Ждём 20 сек
        page.wait_for_timeout(20000)
        dt = round(time.monotonic() - t0, 2)
        html = page.content()
        print(f"  PW XHR: dt={dt}s html_len={len(html)} xhrs={len(xhr_data)}", flush=True)

        result["ozon_pw_xhr"] = {
            "dt": dt, "html_len": len(html),
            "xhr_count": len(xhr_data),
            "xhrs": xhr_data[:5],
            "is_challenge": 'challenge' in html.lower() or len(html) < 5000,
            "title": (re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) or [None, ""])[1],
        }
        browser.close()
except Exception as e:
    result["ozon_mobile_err"] = str(e)
    print(f"  FATAL: {e}", flush=True)

# ─── 3. WB: найти живой nm_id через HTML-страницу ───
print("\n[3] WB: find live nm_id …", flush=True)
try:
    import requests as req2
    s2 = req2.Session()
    s2.headers.update({**H, "Accept": "text/html"})

    # Попробуем WB search page (HTML, не API)
    search_urls = [
        "https://www.wildberries.ru/catalog/1097975275/detail.aspx",
        "https://www.wildberries.ru/catalog/123456789/detail.aspx",
        "https://www.wildberries.ru/catalog/456789012/detail.aspx",
    ]
    for su in search_urls:
        try:
            t0 = time.monotonic()
            rs = s2.get(su, timeout=15, allow_redirects=True)
            dt = round(time.monotonic() - t0, 2)
            html = rs.text
            has_state = '__STATE__' in html or 'STATE' in html
            has_jsonld = 'ld+json' in html
            has_og = 'og:title' in html
            has_price = 'price' in html.lower()[:10000]
            print(f"  {su[:60]}: {rs.status_code} len={len(html)} state={has_state} ld={has_jsonld} og={has_og} dt={dt}s", flush=True)
            result[f"wb_html_{su.split('/')[-2]}"] = {
                "url": su, "status": rs.status_code, "dt": dt,
                "len": len(html), "has_state": has_state,
                "has_jsonld": has_jsonld, "has_og": has_og,
                "head_3000": html[:3000],
            }
            # Если нашли STATE — извлечь basket host
            m = re.search(r'basket-(\d+)', html)
            if m:
                result[f"wb_html_{su.split('/')[-2]}"]["basket_host"] = m.group(1)
                print(f"    basket host: {m.group(1)}", flush=True)
        except Exception as e:
            print(f"  {su[:50]} ERR: {e}", flush=True)
        time.sleep(1)

    # WB: basket-01..60 с живым ID (если нашли)
    # Попробуем известный популярный товар: 123456789 (iPhone-подобный)
    for nm in [123456789, 456789012]:
        vol = nm // 10000000
        part = nm // 1000000
        sub = nm // 1000
        path = f"/vol{vol}/part{part}/{sub}/info/ru/card.json"
        print(f"  Basket test nm={nm} path={path}", flush=True)
        for n in range(1, 11):
            host = f"basket-{n:02d}.wbbasket.ru"
            url = f"https://{host}{path}"
            try:
                rb = s2.get(url, timeout=6, allow_redirects=True)
                if rb.status_code == 200:
                    print(f"  *** {host}: 200 len={len(rb.text)} ***", flush=True)
                    result[f"wb_basket_nm{nm}"] = {
                        "host": host, "url": url, "status": 200,
                        "len": len(rb.text), "head_2000": rb.text[:2000],
                    }
                    break
                elif n <= 3:
                    print(f"  {host}: {rb.status_code}", flush=True)
            except Exception as e:
                if n <= 2:
                    print(f"  {host}: ERR {str(e)[:50]}", flush=True)
            time.sleep(0.2)
except Exception as e:
    result["wb_html_err"] = str(e)
    print(f"  FATAL: {e}", flush=True)

# ─── Write ───
out_file = OUT / "diag_round6.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nDone → {out_file}", flush=True)
