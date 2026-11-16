"""Диагностика 4: Farpost HTML-структура + OZON Playwright (долгий wait) + WB basket-NN."""
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

# ─── 1. FARPOST: дамп структуры главной ───
print("[1] FARPOST: анализ HTML главной …", flush=True)
try:
    import requests
    s = requests.Session()
    s.headers.update(H)
    r = s.get("https://www.farpost.ru/vladivostok/", timeout=15)
    ct = (r.headers.get("Content-Type") or "").lower()
    if "1251" in ct:
        h = r.content.decode("windows-1251", errors="replace")
    else:
        h = r.text
    print(f"  Main: {r.status_code} len={len(h)}", flush=True)

    # Сохранить кусок для анализа
    chunk = h[2000:12000]
    result["farpost_main"] = {
        "status": r.status_code,
        "len": len(h),
        "chunk_2k_12k": chunk,
    }

    # Попробовать разные regex для ссылок на объявления
    patterns = [
        (r'href="(/vladivostok/\d{6,}\.html)"', "num.html"),
        (r'href="(/vladivostok/\d+)"', "num-plain"),
        (r'href="(/\d+\.html)"', "root-num.html"),
        (r'href="(/vladivostok/[^"]+/\d+\.html)"', "cat-num.html"),
        (r'data-bulletin-id="(\d+)"', "data-bid"),
        (r'class="[^"]*bulletin[^"]*"[^>]*href="([^"]+)"', "class-bull"),
        (r'href="([^"]*farpost\.ru[^"]*/\d{6,}[^"]*)"', "full-url"),
    ]
    for pat, label in patterns:
        found = re.findall(pat, h)
        uniq = list(dict.fromkeys(found))
        print(f"  [{label}] {len(uniq)} unique: {uniq[:3]}", flush=True)
        if label not in result:
            result[f"farpost_links_{label}"] = uniq[:10]
        elif len(uniq) > len(result.get(f"farpost_links_{label}", [])):
            result[f"farpost_links_{label}"] = uniq[:10]

    # Если нашли — проверить первый живой
    best = []
    for label in ["num.html", "num-plain", "cat-num.html", "full-url", "root-num.html"]:
        k = f"farpost_links_{label}"
        if k in result and result[k]:
            best = result[k]
            break

    if best:
        for lid in best[:4]:
            full = lid if lid.startswith("http") else f"https://www.farpost.ru{lid}"
            if not lid.startswith("http"):
                full = f"https://www.farpost.ru{lid}" if lid.startswith("/") else f"https://www.farpost.ru/{lid}"
            try:
                r2 = s.get(full, timeout=10)
                ct2 = (r2.headers.get("Content-Type") or "").lower()
                h2 = r2.content.decode("windows-1251", errors="replace") if "1251" in ct2 else r2.text
                has_ld = 'ld+json' in h2
                has_price = 'data-bulletin-price' in h2 or 'viewbull-summary-price' in h2
                has_og = 'og:title' in h2
                print(f"  CHECK {full}: {r2.status_code} len={len(h2)} ld={has_ld} price={has_price}", flush=True)
                if r2.status_code == 200 and (has_price or has_ld) and len(h2) > 3000:
                    result["farpost_live"] = {
                        "id": re.search(r'(\d+)', lid).group(1) if re.search(r'(\d+)', lid) else lid,
                        "url": full,
                        "status": r2.status_code,
                        "html_len": len(h2),
                        "has_jsonld": has_ld,
                        "has_price": has_price,
                        "has_og": has_og,
                        "html_head_3000": h2[:3000],
                    }
                    print("  *** LIVE FOUND ***", flush=True)
                    break
            except Exception as e:
                print(f"  CHECK {full}: ERR {e}", flush=True)
            time.sleep(0.3)
except Exception as e:
    result["farpost_main"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── 2. OZON Playwright: долгий wait + networkidle ───
print("\n[2] OZON via Playwright (long wait) …", flush=True)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox",
                  "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        # Убрать navigator.webdriver
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()

        t0 = time.monotonic()
        try:
            resp = page.goto("https://www.ozon.ru/product/562267/",
                             wait_until="domcontentloaded", timeout=30000)
        except Exception as ge:
            print(f"  goto error: {ge}", flush=True)
            resp = None

        status = resp.status if resp else 0
        print(f"  After goto: status={status} dt={round(time.monotonic()-t0,2)}s", flush=True)

        # Ждём challenge → redirect
        for wait_s in [5, 10, 15]:
            page.wait_for_timeout(wait_s * 1000)
            cur_url = page.url
            html_now = page.content()
            is_challenge = 'Antibot Challenge' in html_now or 'challenge' in cur_url.lower()
            print(f"  +{wait_s}s: url={cur_url[:80]} len={len(html_now)} challenge={is_challenge}", flush=True)
            if not is_challenge and len(html_now) > 5000:
                break

        dt = round(time.monotonic() - t0, 2)
        html = page.content()
        cur_url = page.url
        print(f"  Final: url={cur_url[:100]} len={len(html)} dt={dt}s", flush=True)

        result["ozon_pw2"] = {
            "final_url": cur_url,
            "dt": dt,
            "html_len": len(html),
            "has_jsonld": 'ld+json' in html,
            "has_og_title": 'og:title' in html,
            "has_webprice": 'webPrice' in html,
            "has_state": '__STATE__' in html or 'data-widget' in html,
            "has_h1": '<h1' in html,
            "is_challenge": 'Antibot Challenge' in html or 'challenge' in cur_url.lower(),
            "title_tag": (re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) or [None, ""])[1],
            "html_head_3000": html[:3000],
        }

        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        result["ozon_pw2"]["jsonld_count"] = len(ld_blocks)
        for i, lb in enumerate(ld_blocks[:3]):
            try:
                j = json.loads(lb)
                t = j.get("@type", "?") if isinstance(j, dict) else "list"
                result["ozon_pw2"][f"jsonld_{i}_type"] = str(t)
                if t == "Product":
                    result["ozon_pw2"][f"jsonld_{i}_name"] = str(j.get("name", ""))[:80]
                    result["ozon_pw2"][f"jsonld_{i}_offers"] = str(j.get("offers", ""))[:200]
            except Exception:
                pass

        # Цены
        prices = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html[:80000])
        result["ozon_pw2"]["prices_found"] = [p.replace(" ", "").replace("\u00a0", "") for p in prices[:10]]

        # H1
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if h1:
            result["ozon_pw2"]["h1"] = re.sub(r'<[^>]+>', '', h1.group(1)).strip()[:100]

        browser.close()
except Exception as e:
    result["ozon_pw2"] = {"error": str(e)}
    print(f"  ERROR: {e}", flush=True)

# ─── 3. WB basket: basket-{NN}.wbbasket.ru (NN = 01..60) ───
print("\n[3] WB basket (NN format) …", flush=True)
try:
    nm_id = 1097975275
    vol = nm_id // 10000000   # 109
    part = nm_id // 1000000   # 1097
    sub = nm_id // 1000       # 1097975
    path = f"/vol{vol}/part{part}/{sub}/info/ru/card.json"
    print(f"  vol={vol} part={part} sub={sub}", flush=True)

    import requests as req2
    s2 = req2.Session()
    s2.headers.update({**H, "Accept": "application/json"})

    found_host = None
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            t0 = time.monotonic()
            rb = s2.get(url, timeout=6, allow_redirects=True)
            dt = round(time.monotonic() - t0, 2)
            if rb.status_code == 200:
                print(f"  *** {host}: 200 len={len(rb.text)} dt={dt}s ***", flush=True)
                found_host = n
                result["wb_basket_found"] = {
                    "host": host, "n": n, "url": url,
                    "status": rb.status_code, "dt": dt,
                    "len": len(rb.text), "head_2000": rb.text[:2000],
                }
                try:
                    j = rb.json()
                    selling = j.get("selling", {})
                    result["wb_basket_found"]["has_selling"] = bool(selling)
                    result["wb_basket_found"]["selling_price"] = selling.get("price")
                    result["wb_basket_found"]["selling_priceU"] = selling.get("priceU")
                    result["wb_basket_found"]["imt_name"] = j.get("imt_name", "")[:80]
                    result["wb_basket_found"]["sizes_count"] = len(j.get("sizes", []))
                except Exception as e2:
                    result["wb_basket_found"]["json_err"] = str(e2)
                break
            elif n <= 5 or n % 10 == 0:
                print(f"  {host}: {rb.status_code} dt={dt}s", flush=True)
        except Exception as e:
            if n <= 3:
                print(f"  {host}: ERR {str(e)[:60]}", flush=True)
        time.sleep(0.2)

    if not found_host:
        result["wb_basket_found"] = {"error": "no basket host 01-60 returned 200"}
        print("  No basket host found in 01-60", flush=True)
except Exception as e:
    result["wb_basket_found"] = {"error": str(e)}
    print(f"  FATAL: {e}", flush=True)

# ─── Write ───
out_file = OUT / "diag_round4.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\nDone → {out_file}", flush=True)
