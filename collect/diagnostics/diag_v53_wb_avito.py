"""Диагностика v53: WB (catalog.wb.ru + curl_cffi) и Avito (реальные ID).
Фокус: WB API через curl_cffi (обход 403), Avito — поиск живых объявлений.
Запуск: C:/Python315/python.exe diag_v53_wb_avito.py
"""
import json, re, time, os, sys
from datetime import datetime

OUT = []
def log(msg):
    OUT.append(msg)
    print(msg)

def main():
    log("=" * 60)
    log(f"DIAG v53: WB + AVITO  |  {datetime.now().isoformat()}")
    log("=" * 60)

    # ============================================================
    # WILDBERRIES — focus on catalog.wb.ru API
    # ============================================================
    log("\n" + "=" * 60)
    log("WILDBERRIES")
    log("=" * 60)

    # Реальные ID (проверить: 199298871, 110835207 — могут быть устаревшими)
    wb_ids = ["199298871", "110835207", "188497640", "12736071",
              "189316415", "7610591", "155269788"]

    wb_api_ok = False
    wb_api_data = None

    # --- W1: catalog.wb.ru через curl_cffi (обход 403) ---
    log("\n--- W1: catalog.wb.ru via curl_cffi ---")
    for nm in wb_ids[:4]:
        url = f"https://catalog.wb.ru/cards/v1/detail?nm={nm}"
        try:
            from curl_cffi import requests as cffi_req
            resp = cffi_req.get(url, impersonate="chrome", timeout=20, headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Origin": "https://www.wildberries.ru",
                "Referer": "https://www.wildberries.ru/",
            })
            log(f"  cffi nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
            if resp.status_code == 200 and len(resp.content) > 100:
                data = resp.json()
                products = data.get("data", {}).get("products", [])
                log(f"    products: {len(products)}")
                if products:
                    p = products[0]
                    name = p.get("name", "?")
                    brand = p.get("brand", "?")
                    imts = p.get("imts", [])
                    sizes = p.get("sizes", [])
                    price = sizes[0].get("price", {}).get("basic", "?") if sizes else "?"
                    log(f"    name={name[:60]}")
                    log(f"    brand={brand}")
                    log(f"    price={price}")
                    log(f"    images={len(imts)}")
                    wb_api_ok = True
                    wb_api_data = data
                    with open(f"diag_v53_wb_api_{nm}.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    log(f"    Saved: diag_v53_wb_api_{nm}.json")
                    break
                else:
                    log(f"    data keys: {list(data.get('data',{}).keys())}")
                    log(f"    Body[:200]: {resp.text[:200]}")
            elif resp.status_code == 200:
                log(f"    Short body: {resp.text[:200]}")
            else:
                log(f"    Headers: {dict(list(resp.headers.items())[:8])}")
        except Exception as e:
            log(f"  cffi nm={nm}: ERR {type(e).__name__}: {e}")
        time.sleep(2)

    # --- W2: catalog.wb.ru через requests (просто, без TLS-fingerprint) ---
    log("\n--- W2: catalog.wb.ru via requests ---")
    for nm in wb_ids[:3]:
        url = f"https://catalog.wb.ru/cards/v1/detail?nm={nm}"
        try:
            import requests
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Origin": "https://www.wildberries.ru",
                "Referer": "https://www.wildberries.ru/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
            }, timeout=15)
            log(f"  req nm={nm}: HTTP {r.status_code}, len={len(r.content)}")
            if r.status_code == 200 and len(r.content) > 100:
                data = r.json()
                products = data.get("data", {}).get("products", [])
                log(f"    products: {len(products)}")
                if products:
                    wb_api_ok = True
                    wb_api_data = data
                    with open(f"diag_v53_wb_req_{nm}.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    log(f"    Saved: diag_v53_wb_req_{nm}.json")
                    break
        except Exception as e:
            log(f"  req nm={nm}: ERR {e}")
        time.sleep(2)

    # --- W3: card.wb.ru v2 (другой хост) ---
    log("\n--- W3: card.wb.ru/cards/v2/detail ---")
    for nm in wb_ids[:3]:
        url = f"https://card.wb.ru/cards/v2/detail?nm={nm}"
        try:
            from curl_cffi import requests as cffi_req
            resp = cffi_req.get(url, impersonate="chrome", timeout=20, headers={
                "Accept": "application/json",
                "Referer": "https://www.wildberries.ru/",
            })
            log(f"  card v2 nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
            if resp.status_code == 200 and len(resp.content) > 50:
                log(f"    Body[:200]: {resp.text[:200]}")
                wb_api_ok = True
        except Exception as e:
            log(f"  card v2 nm={nm}: ERR {e}")
        time.sleep(2)

    # --- W4: WB search API (найти живые ID) ---
    log("\n--- W4: WB search API for live IDs ---")
    try:
        from curl_cffi import requests as cffi_req
        search_url = "https://search.wb.ru/exactmatch/ru/common/v5/search?query=iphone&resultset=catalog&sort=popular&suppressSpelling=false&order=popular"
        resp = cffi_req.get(search_url, impersonate="chrome", timeout=20, headers={
            "Accept": "application/json",
            "Referer": "https://www.wildberries.ru/",
        })
        log(f"  search: HTTP {resp.status_code}, len={len(resp.content)}")
        if resp.status_code == 200:
            data = resp.json()
            products = data.get("data", {}).get("products", [])
            log(f"  products found: {len(products)}")
            for p in products[:5]:
                nm = p.get("id", "?")
                name = p.get("name", "?")[:50]
                log(f"    id={nm} name={name}")
            if products:
                # Save first 3 IDs for API test
                live_ids = [str(p["id"]) for p in products[:3]]
                log(f"  Live IDs: {live_ids}")
                # Test one via catalog API
                for lid in live_ids[:1]:
                    api_url = f"https://catalog.wb.ru/cards/v1/detail?nm={lid}"
                    r2 = cffi_req.get(api_url, impersonate="chrome", timeout=15, headers={
                        "Accept": "application/json",
                        "Referer": "https://www.wildberries.ru/",
                    })
                    log(f"  catalog nm={lid}: HTTP {r2.status_code}, len={len(r2.content)}")
                    if r2.status_code == 200:
                        wb_api_ok = True
                        with open(f"diag_v53_wb_live_{lid}.json", "w", encoding="utf-8") as f:
                            f.write(r2.text)
        else:
            log(f"  search body[:100]: {resp.text[:100]}")
    except Exception as e:
        log(f"  W4 ERR: {e}")

    log(f"\nWB RESULT: api_ok={wb_api_ok}")

    # ============================================================
    # AVITO — find REAL ad IDs
    # ============================================================
    log("\n" + "=" * 60)
    log("AVITO")
    log("=" * 60)

    real_ids = []

    # --- A1: Find real IDs via Google (web search simulation) ---
    log("\n--- A1: Find real Avito ad IDs ---")
    # Use known patterns: avito.ru/{city}/{category}/{slug}-{id}
    # Try to scrape a working category page
    try:
        from curl_cffi import requests as cffi_req
        # Try different city/category combos
        cat_urls = [
            "https://www.avito.ru/moscow/telefony",
            "https://www.avito.ru/moscow/noutbuki",
            "https://www.avito.ru/moscow/avto",
            "https://www.avito.ru/spb/telefony",
            "https://www.avito.ru/moscow/kvartiry",
        ]
        for cu in cat_urls:
            try:
                resp = cffi_req.get(cu, impersonate="chrome", timeout=20)
                html = resp.text
                log(f"  cat {cu.split('avito.ru')[1][:30]}: HTTP {resp.status_code}, len={len(html)}")
                # Extract ad IDs from various patterns
                ids1 = re.findall(r'avito\.ru/[^"]*?/(\d{9,12})/?"', html)
                ids2 = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
                ids3 = re.findall(r'/ad/(\d{9,12})', html)
                ids4 = re.findall(r'"id":(\d{9,12})', html)
                all_ids = list(set(ids1 + ids2 + ids3 + ids4))
                log(f"    IDs: {len(all_ids)} (p1={len(ids1)}, p2={len(ids2)}, p3={len(ids3)}, p4={len(ids4)})")
                for aid in all_ids[:8]:
                    log(f"      {aid}")
                if all_ids:
                    real_ids.extend(all_ids[:5])
                    with open(f"diag_v53_avito_cat_{cu.split('/')[-1]}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    if len(real_ids) >= 3:
                        break
            except Exception as e:
                log(f"  cat ERR: {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A1 ERR: {e}")

    # --- A2: Try Avito "all" search page ---
    log("\n--- A2: Avito search page ---")
    try:
        from curl_cffi import requests as cffi_req
        search_urls = [
            "https://www.avito.ru/search?q=iphone&f=0",
            "https://www.avito.ru/moscow/search?q=noutbuk&f=0",
            "https://www.avito.ru/search?query=телефон",
        ]
        for su in search_urls:
            try:
                resp = cffi_req.get(su, impersonate="chrome", timeout=20)
                html = resp.text
                log(f"  search {su.split('avito.ru')[1][:40]}: HTTP {resp.status_code}, len={len(html)}")
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                log(f"    Title: {title_m.group(1)[:60] if title_m else '?'}")
                ids = re.findall(r'avito\.ru/[^"]*?/(\d{9,12})/?"', html)
                ids2 = re.findall(r'"id":(\d{9,12})', html)
                all_ids = list(set(ids + ids2))
                log(f"    IDs: {len(all_ids)}")
                for aid in all_ids[:5]:
                    log(f"      {aid}")
                if all_ids:
                    real_ids.extend(all_ids[:5])
            except Exception as e:
                log(f"  search ERR: {e}")
            time.sleep(3)
    except Exception as e:
        log(f"  A2 ERR: {e}")

    # --- A3: Test real IDs on ad pages ---
    log("\n--- A3: Test ad pages with real IDs ---")
    avito_page_ok = False
    test_ids = real_ids[:5] if real_ids else ["1154321987"]
    log(f"  Testing IDs: {test_ids}")
    for aid in test_ids:
        for url_pat in [
            f"https://www.avito.ru/ad/{aid}",
            f"https://www.avito.ru/{aid}",
        ]:
            try:
                from curl_cffi import requests as cffi_req
                resp = cffi_req.get(url_pat, impersonate="chrome", timeout=20)
                html = resp.text
                title_m = re.search(r'<title[^>]*>([^<]+)', html)
                title = title_m.group(1)[:70] if title_m else "?"
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                is_404 = "404" in html[:2000] or "не найдена" in html[:2000].lower()
                log(f"  id={aid} {url_pat.split('avito.ru')[1][:20]}: HTTP {resp.status_code}, len={len(html)}")
                log(f"    Title: {title}")
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}, 404={is_404}")
                if not has_captcha and not is_404 and len(html) > 5000:
                    avito_page_ok = True
                    with open(f"diag_v53_avito_ad_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    log(f"    Saved: diag_v53_avito_ad_{aid}.html")
                    # Extract JSON-LD
                    ld_blocks = re.findall(
                        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html, re.DOTALL)
                    log(f"    JSON-LD blocks: {len(ld_blocks)}")
                    for i, block in enumerate(ld_blocks[:3]):
                        try:
                            d = json.loads(block.strip())
                            t = d.get("@type", "?") if isinstance(d, dict) else "?"
                            log(f"      LD[{i}] type={t}")
                        except Exception:
                            pass
                    break
            except Exception as e:
                log(f"  id={aid}: ERR {e}")
            time.sleep(3)
        if avito_page_ok:
            break

    # --- A4: Avito via "www.avito.ru" with full browser session ---
    log("\n--- A4: Full browser session ---")
    try:
        from curl_cffi import requests as cffi_req
        # Step 1: main page
        r0 = cffi_req.get("https://www.avito.ru/", impersonate="chrome", timeout=20)
        log(f"  Main: HTTP {r0.status_code}, len={len(r0.content)}")
        time.sleep(2)
        # Step 2: ad page (using first real ID or fallback)
        aid = real_ids[0] if real_ids else "1154321987"
        r1 = cffi_req.get(f"https://www.avito.ru/ad/{aid}", impersonate="chrome", timeout=20)
        html = r1.text
        title_m = re.search(r'<title[^>]*>([^<]+)', html)
        log(f"  Ad {aid}: HTTP {r1.status_code}, len={len(html)}")
        log(f"    Title: {title_m.group(1)[:60] if title_m else '?'}")
        has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
        has_jsonld = "application/ld+json" in html
        is_404 = "404" in html[:2000] or "не найдена" in html[:2000].lower()
        log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}, 404={is_404}")
        if not has_captcha and not is_404 and len(html) > 5000:
            avito_page_ok = True
            with open(f"diag_v53_avito_sess_{aid}.html", "w", encoding="utf-8") as f:
                f.write(html)
    except Exception as e:
        log(f"  A4 ERR: {e}")

    log(f"\nAVITO RESULT: page_ok={avito_page_ok}, real_ids={real_ids[:5]}")

    # ============================================================
    # SUMMARY
    # ============================================================
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    log(f"  WB:    api_ok={wb_api_ok}")
    log(f"  AVITO: page_ok={avito_page_ok}, ids={real_ids[:5]}")
    log(f"  DONE: {datetime.now().isoformat()}")

    if not os.environ.get("AHUB_REDIRECT"):
        with open("diag_v53_out.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\nFull log saved: diag_v53_out.txt ({len(OUT)} lines)")
    else:
        log(f"\nFull log: {len(OUT)} lines (via shell redirect)")

if __name__ == "__main__":
    main()
