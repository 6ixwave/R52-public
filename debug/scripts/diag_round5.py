"""Диагностика 5: Farpost body + OZON visible + WB basket verify."""
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

# ─── 1. FARPOST: дамп body (15000–50000) + поиск ссылок ───
print("[1] FARPOST: body dump …", flush=True)
try:
    import requests
    s = requests.Session()
    s.headers.update(H)
    r = s.get("https://www.farpost.ru/vladivostok/", timeout=15)
    ct = (r.headers.get("Content-Type") or "").lower()
    h = r.content.decode("windows-1251", errors="replace") if "1251" in ct else r.text
    print(f"  Main: {r.status_code} len={len(h)}", flush=True)

    # Дамп body: ищем <body>
    body_start = h.find("<body")
    if body_start == -1:
        body_start = 15000
    body_chunk = h[body_start:body_start + 25000]
    result["farpost_body"] = {
        "body_start": body_start,
        "chunk_len": len(body_chunk),
        "chunk": body_chunk[:15000],
    }

    # Расширенные паттерны для ссылок на объявления
    patterns = [
        (r'href="([^"]*?/\d{6,}\.html)"', "any-num.html"),
        (r'href="([^"]*?/b/\d+)"', "b-num"),
        (r'data-id="(\d+)"', "data-id"),
        (r'data-bulletin="(\d+)"', "data-bulletin"),
        (r'"bulletin_id"\s*:\s*(\d+)', "json-bid"),
        (r'"id"\s*:\s*"?(\d{6,})"?', "json-id"),
        (r'class="[^"]*(?:bull|item|card|list)[^"]*"[^>]*>', "class-patterns"),
        (r'<a[^>]+href="([^"]+)"[^>]*>\s*<span[^>]*class="[^"]*(?:price|cost)', "price-link"),
        (r'href="(/vladivostok/[^"]+)"', "vladivostok-any"),
    ]
    for pat, label in patterns:
        found = re.findall(pat, h, re.I)
        uniq = list(dict.fromkeys(found))
        print(f"  [{label}] {len(uniq)}: {uniq[:5]}", flush=True)
        if uniq:
            result[f"fp_links_{label}"] = uniq[:15]

    # Если нашли ссылки — проверить первую
    all_links = []
    for label in ["any-num.html", "vladivostok-any", "b-num", "price-link"]:
        k = f"fp_links_{label}"
        if k in result:
            all_links.extend(result[k])
    all_links = list(dict.fromkeys(all_links))

    if all_links:
        for lid in all_links[:5]:
            full = lid if lid.startswith("http") else f"https://www.farpost.ru{lid}"
            try:
                r2 = s.get(full, timeout=10)
                ct2 = (r2.headers.get("Content-Type") or "").lower()
                h2 = r2.content.decode("windows-1251", errors="replace") if "1251" in ct2 else r2.text
                has_ld = 'ld+json' in h2
                has_price = 'data-bulletin-price' in h2 or 'viewbull-summary-price' in h2 or 'price' in h2.lower()
                has_og = 'og:title' in h2
                print(f"  CHECK {full[:60]}: {r2.status_code} len={len(h2)} ld={has_ld} price={has_price}", flush=True)
                if r2.status_code == 200 and len(h2) > 3000:
                    result["farpost_live"] = {
                        "url": full,
                        "status": r2.status_code,
                        "html_len": len(h2),
                        "has_jsonld": has_ld,
                        "has_price": has_price,
                        "has_og": has_og,
                        "html_head_3000": h2[:3000],
                        "html_mid_5000": h2[3000:8000],
                    }
                    print("  *** LIVE FOUND ***", flush=True)
                    break
            except Exception as e:
                print(f"  CHECK ERR: {e}", flush=True)
            time.sleep(0.3)
    else:
        result["farpost_live"] = {"error": "no links found in any pattern"}
except Exception as e:
    result["farpost_body"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── 2. OZON: Playwright VISIBLE (headless=False) ───
print("\n[2] OZON via Playwright (VISIBLE) …", flush=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
            ],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = {runtime: {}};
        """)
        page = ctx.new_page()

        # Перехват: ждём redirect с challenge
        t0 = time.monotonic()
        try:
            resp = page.goto("https://www.ozon.ru/product/562267/",
                             wait_until="domcontentloaded", timeout=45000)
        except Exception as ge:
            print(f"  goto timeout/err: {ge}", flush=True)
            resp = None

        status = resp.status if resp else 0
        print(f"  goto: status={status} dt={round(time.monotonic()-t0,2)}s", flush=True)

        # Ждём: challenge JS должен сделать redirect
        final_html = ""
        final_url = ""
        for i in range(12):  # до 60 сек
            page.wait_for_timeout(5000)
            cur_url = page.url
            final_html = page.content()
            final_url = cur_url
            is_challenge = ('challenge' in cur_url.lower() or
                           'Antibot' in final_html or
                           'Похоже, нет соединения' in final_html or
                           len(final_html) < 5000)
            print(f"  +{(i+1)*5}s: url={cur_url[:80]} len={len(final_html)} challenge={is_challenge}", flush=True)
            if not is_challenge and len(final_html) > 10000:
                print(f"  *** ESCAPED CHALLENGE at +{(i+1)*5}s ***", flush=True)
                break

        dt = round(time.monotonic() - t0, 2)
        print(f"  Final: url={final_url[:100]} len={len(final_html)} dt={dt}s", flush=True)

        result["ozon_visible"] = {
            "final_url": final_url,
            "dt": dt,
            "html_len": len(final_html),
            "has_jsonld": 'ld+json' in final_html,
            "has_og_title": 'og:title' in final_html,
            "has_webprice": 'webPrice' in final_html,
            "has_state": '__STATE__' in final_html or 'data-widget' in final_html,
            "has_h1": '<h1' in final_html,
            "is_challenge": ('challenge' in final_url.lower() or
                            'Antibot' in final_html or
                            'Похоже, нет соединения' in final_html),
            "title_tag": (re.search(r'<title[^>]*>([^<]+)</title>', final_html, re.I) or [None, ""])[1],
            "html_head_3000": final_html[:3000],
        }

        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', final_html, re.DOTALL)
        result["ozon_visible"]["jsonld_count"] = len(ld_blocks)
        for i, lb in enumerate(ld_blocks[:3]):
            try:
                j = json.loads(lb)
                t = j.get("@type", "?") if isinstance(j, dict) else "list"
                result["ozon_visible"][f"jsonld_{i}_type"] = str(t)
                if t == "Product":
                    result["ozon_visible"][f"jsonld_{i}_name"] = str(j.get("name", ""))[:80]
                    result["ozon_visible"][f"jsonld_{i}_offers"] = str(j.get("offers", ""))[:200]
            except Exception:
                pass

        # Цены
        prices = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', final_html[:100000])
        result["ozon_visible"]["prices_found"] = [p.replace(" ", "").replace("\u00a0", "") for p in prices[:10]]

        # H1
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', final_html, re.DOTALL)
        if h1:
            result["ozon_visible"]["h1"] = re.sub(r'<[^>]+>', '', h1.group(1)).strip()[:100]

        browser.close()
except Exception as e:
    result["ozon_visible"] = {"error": str(e)}
    print(f"  ERROR: {e}", flush=True)

# ─── 3. WB: проверить basket с НЕСКОЛЬКИМИ nm_id ───
print("\n[3] WB basket (multiple IDs) …", flush=True)
try:
    import requests as req2
    s2 = req2.Session()
    s2.headers.update({**H, "Accept": "application/json"})

    # Тестовые nm_id: из diag + возможные реальные
    test_ids = [
        1097975275,   # из diag
        109797527,    # без trailing 5
        10979752750,  # +0
    ]
    # Плюс: попробуем найти через WB search API
    # Но сначала: проверим basket-01 с разными путями для первого ID
    nm_id = test_ids[0]
    vol = nm_id // 10000000
    part = nm_id // 1000000
    sub = nm_id // 1000
    path = f"/vol{vol}/part{part}/{sub}/info/ru/card.json"
    print(f"  Primary: nm={nm_id} vol={vol} part={part} sub={sub}", flush=True)
    print(f"  Path: {path}", flush=True)

    # basket-01 с этим путём
    for n in [1, 5, 10, 20, 30, 40, 50]:
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            t0 = time.monotonic()
            rb = s2.get(url, timeout=8, allow_redirects=True)
            dt = round(time.monotonic() - t0, 2)
            print(f"  {host}: {rb.status_code} len={len(rb.text)} dt={dt}s", flush=True)
            if rb.status_code == 200:
                result[f"wb_basket_{n:02d}"] = {
                    "url": url, "status": 200, "dt": dt,
                    "len": len(rb.text), "head_2000": rb.text[:2000],
                }
                break
        except Exception as e:
            print(f"  {host}: ERR {str(e)[:60]}", flush=True)
        time.sleep(0.3)

    # Альтернативный путь: без /info/ru/
    alt_paths = [
        f"/vol{vol}/part{part}/{sub}/info/ru/card.json",
        f"/vol{vol}/part{part}/{sub}/card.json",
        f"/vol{vol}/part{part}/{sub}/info/json",
    ]
    print(f"  Trying alt paths on basket-01 …", flush=True)
    for ap in alt_paths:
        url = f"https://basket-01.wbbasket.ru{ap}"
        try:
            rb = s2.get(url, timeout=8, allow_redirects=True)
            print(f"  {ap}: {rb.status_code} len={len(rb.text)}", flush=True)
            if rb.status_code == 200:
                result[f"wb_basket_alt"] = {"url": url, "status": 200, "head_1000": rb.text[:1000]}
                break
        except Exception as e:
            print(f"  {ap}: ERR {str(e)[:50]}", flush=True)
        time.sleep(0.3)

    # WB search API (для проверки, что ID живой)
    print(f"\n  WB search API …", flush=True)
    try:
        search_url = f"https://search.wb.ru/exactmatch/ru/common/v5/search?query=&nm={nm_id}&appType=1&curr=rub&dest=-1257786"
        rs = s2.get(search_url, timeout=12, headers={**H, "Accept": "application/json"})
        print(f"  Search: {rs.status_code} len={len(rs.text)}", flush=True)
        result["wb_search"] = {"status": rs.status_code, "len": len(rs.text), "head_1000": rs.text[:1000]}
    except Exception as e:
        result["wb_search"] = {"error": str(e)}
except Exception as e:
    result["wb_basket"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── Write ───
out_file = OUT / "diag_round5.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nDone → {out_file}", flush=True)
