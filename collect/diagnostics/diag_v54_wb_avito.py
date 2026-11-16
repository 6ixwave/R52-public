"""Диагностика v54: WB (SSR HTML + session) и Avito (session + RSS + delays).
Запуск: C:/Python315/python.exe diag_v54_wb_avito.py
"""
import json, re, time, os, sys
from datetime import datetime

OUT = []
def log(msg):
    OUT.append(msg)
    print(msg)

def main():
    log("=" * 60)
    log(f"DIAG v54: WB SSR + AVITO SESSION  |  {datetime.now().isoformat()}")
    log("=" * 60)

    from curl_cffi import requests as cffi_req

    # ============================================================
    # WILDBERRIES — SSR HTML page approach
    # ============================================================
    log("\n" + "=" * 60)
    log("WILDBERRIES")
    log("=" * 60)

    wb_ids = ["199298871", "110835207", "188497640", "12736071"]
    wb_ssr_ok = False
    wb_data_saved = False

    # --- W1: SSR HTML via www.wildberries.ru/catalog/{nm}/detail.aspx ---
    log("\n--- W1: SSR HTML (www.wildberries.ru) ---")
    session = cffi_req.Session(impersonate="chrome")
    # First: hit main page to get cookies
    try:
        r0 = session.get("https://www.wildberries.ru/", timeout=25, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        log(f"  Main page: HTTP {r0.status_code}, len={len(r0.content)}")
        cookies = dict(session.cookies)
        log(f"  Cookies: {list(cookies.keys())[:10]}")
    except Exception as e:
        log(f"  Main page ERR: {e}")
    time.sleep(3)

    for nm in wb_ids:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        try:
            resp = session.get(url, timeout=25, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": "https://www.wildberries.ru/",
            })
            html = resp.text
            log(f"  SSR nm={nm}: HTTP {resp.status_code}, len={len(html)}")

            if resp.status_code == 200 and len(html) > 10000:
                # Check for embedded state
                has_state = "__STATE__" in html or "__PRELOADED_STATE__" in html or "window.__" in html
                has_jsonld = "application/ld+json" in html
                has_price = "price" in html.lower()
                has_title_tag = re.search(r"<title[^>]*>([^<]+)</title>", html)
                title = has_title_tag.group(1)[:80] if has_title_tag else "?"
                log(f"    Title: {title}")
                log(f"    State={has_state}, JSON-LD={has_jsonld}, Price={has_price}")

                # Extract JSON-LD
                ld_blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL)
                if ld_blocks:
                    log(f"    JSON-LD blocks: {len(ld_blocks)}")
                    for i, block in enumerate(ld_blocks[:3]):
                        try:
                            d = json.loads(block.strip())
                            if isinstance(d, dict):
                                t = d.get("@type", "?")
                                name = d.get("name", d.get("headline", "?"))
                                price_info = d.get("offers", {})
                                price = price_info.get("price", "?") if isinstance(price_info, dict) else "?"
                                avail = price_info.get("availability", "?") if isinstance(price_info, dict) else "?"
                                log(f"      LD[{i}] type={t} name={str(name)[:50]} price={price} avail={avail}")
                        except Exception:
                            pass

                # Extract __STATE__ or similar
                state_m = re.search(r"window\.__STATE__\s*=\s*(\{.*?\});?\s*</script>", html, re.DOTALL)
                if not state_m:
                    state_m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>", html, re.DOTALL)
                if state_m:
                    try:
                        state = json.loads(state_m.group(1))
                        log(f"    __STATE__ keys: {list(state.keys())[:10]}")
                        wb_ssr_ok = True
                    except Exception as e:
                        log(f"    State parse ERR: {e}")

                # Save HTML
                with open(f"diag_v54_wb_ssr_{nm}.html", "w", encoding="utf-8") as f:
                    f.write(html)
                log(f"    Saved: diag_v54_wb_ssr_{nm}.html ({len(html)} bytes)")
                wb_ssr_ok = True
                wb_data_saved = True
                break
            elif resp.status_code == 200:
                log(f"    Short page ({len(html)} bytes), title: {html[:200]}")
            else:
                # Check if it's a WBAAS challenge
                if "wbaas" in str(resp.headers).lower() or "challenge" in html.lower()[:2000]:
                    log(f"    WBAAS challenge detected")
                log(f"    Headers: {dict(list(resp.headers.items())[:6])}")
        except Exception as e:
            log(f"  SSR nm={nm}: ERR {type(e).__name__}: {e}")
        time.sleep(4)

    # --- W2: Try search.wb.ru with session cookies ---
    log("\n--- W2: search.wb.ru with session ---")
    try:
        search_url = ("https://search.wb.ru/exactmatch/ru/common/v5/search"
                      "?query=iphone+16&resultset=catalog&sort=popular"
                      "&suppressSpelling=false&order=popular")
        resp = session.get(search_url, timeout=20, headers={
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.wildberries.ru/",
        })
        log(f"  search: HTTP {resp.status_code}, len={len(resp.content)}")
        if resp.status_code == 200 and len(resp.content) > 100:
            data = resp.json()
            products = data.get("data", {}).get("products", [])
            log(f"  products: {len(products)}")
            for p in products[:5]:
                pid = p.get("id", "?")
                pname = p.get("name", "?")[:50]
                log(f"    id={pid} name={pname}")
            if products:
                # Save live IDs
                live_ids = [str(p["id"]) for p in products[:3]]
                log(f"  Live IDs: {live_ids}")
                # Test one via SSR
                lid = live_ids[0]
                ssr_url = f"https://www.wildberries.ru/catalog/{lid}/detail.aspx"
                r2 = session.get(ssr_url, timeout=25, headers={
                    "Accept": "text/html",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                })
                log(f"  SSR live nm={lid}: HTTP {r2.status_code}, len={len(r2.text)}")
                if r2.status_code == 200 and len(r2.text) > 10000:
                    wb_ssr_ok = True
                    with open(f"diag_v54_wb_live_{lid}.html", "w", encoding="utf-8") as f:
                        f.write(r2.text)
                    log(f"    Saved: diag_v54_wb_live_{lid}.html")
    except Exception as e:
        log(f"  W2 ERR: {e}")
    time.sleep(3)

    # --- W3: Try static-basket for images (confirms product exists) ---
    log("\n--- W3: static-basket check ---")
    for nm in wb_ids[:2]:
        try:
            # Try to get image via static-basket
            img_url = f"https://static-basket-01.wbbasket.ru/v20/content/{nm}"
            resp = cffi_req.get(img_url, impersonate="chrome", timeout=15, headers={
                "Referer": "https://www.wildberries.ru/",
            })
            log(f"  basket nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
        except Exception as e:
            log(f"  basket nm={nm}: ERR {e}")
        time.sleep(2)

    # --- W4: Try catalog.wb.ru v1 with session (cookies from main page) ---
    log("\n--- W4: catalog.wb.ru with session cookies ---")
    for nm in wb_ids[:3]:
        try:
            api_url = f"https://catalog.wb.ru/cards/v1/detail?nm={nm}"
            resp = session.get(api_url, timeout=20, headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Origin": "https://www.wildberries.ru",
                "Referer": "https://www.wildberries.ru/",
            })
            log(f"  catalog sess nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
            if resp.status_code == 200 and len(resp.content) > 50:
                data = resp.json()
                prods = data.get("data", {}).get("products", [])
                log(f"    products: {len(prods)}")
                if prods:
                    p = prods[0]
                    log(f"    name={p.get('name','?')[:50]} brand={p.get('brand','?')}")
                    wb_ssr_ok = True
                    with open(f"diag_v54_wb_api_{nm}.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    break
        except Exception as e:
            log(f"  catalog sess nm={nm}: ERR {e}")
        time.sleep(3)

    log(f"\nWB RESULT: ssr_ok={wb_ssr_ok}, saved={wb_data_saved}")

    # ============================================================
    # AVITO — session + RSS + longer delays
    # ============================================================
    log("\n" + "=" * 60)
    log("AVITO")
    log("=" * 60)

    avito_session = cffi_req.Session(impersonate="chrome")
    real_ids = []
    avito_page_ok = False

    # --- A1: Get main page for cookies ---
    log("\n--- A1: Avito main page (session init) ---")
    try:
        r0 = avito_session.get("https://www.avito.ru/", timeout=25, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        log(f"  Main: HTTP {r0.status_code}, len={len(r0.content)}")
        cookies = dict(avito_session.cookies)
        log(f"  Cookies: {list(cookies.keys())[:10]}")
    except Exception as e:
        log(f"  Main ERR: {e}")
    time.sleep(5)

    # --- A2: Try RSS feeds ---
    log("\n--- A2: RSS feeds ---")
    rss_urls = [
        "https://www.avito.ru/rss/moscow/telefony",
        "https://www.avito.ru/rss/moscow/noutbuki",
        "https://www.avito.ru/rss/moscow/avto",
        "https://www.avito.ru/rss/moscow",
    ]
    for ru in rss_urls:
        try:
            resp = avito_session.get(ru, timeout=20, headers={
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.avito.ru/",
            })
            body = resp.text
            log(f"  RSS {ru.split('avito.ru')[1][:30]}: HTTP {resp.status_code}, len={len(body)}")
            if resp.status_code == 200 and len(body) > 500:
                # Extract ad IDs from RSS
                ids_rss = re.findall(r'avito\.ru/[^<]*?/(\d{9,12})', body)
                ids_rss2 = re.findall(r'<link[^>]*>([^<]*?/(\d{9,12}))</link>', body)
                all_rss_ids = list(set(ids_rss + [x[1] for x in ids_rss2]))
                log(f"    IDs: {len(all_rss_ids)}")
                for aid in all_rss_ids[:8]:
                    log(f"      {aid}")
                if all_rss_ids:
                    real_ids.extend(all_rss_ids[:5])
                    with open(f"diag_v54_avito_rss_{ru.split('/')[-1]}.xml", "w", encoding="utf-8") as f:
                        f.write(body)
                    if len(real_ids) >= 3:
                        break
        except Exception as e:
            log(f"  RSS ERR: {e}")
        time.sleep(5)

    # --- A3: Category page with session (longer delay) ---
    log("\n--- A3: Category pages (session, 5s delay) ---")
    cat_urls = [
        "https://www.avito.ru/moscow/telefony",
        "https://www.avito.ru/moscow/noutbuki",
        "https://www.avito.ru/moscow/planshety",
        "https://www.avito.ru/spb/telefony",
    ]
    for cu in cat_urls:
        try:
            resp = avito_session.get(cu, timeout=25, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": "https://www.avito.ru/",
            })
            html = resp.text
            title_m = re.search(r"<title[^>]*>([^<]+)", html)
            log(f"  cat {cu.split('avito.ru')[1][:30]}: HTTP {resp.status_code}, len={len(html)}")
            log(f"    Title: {title_m.group(1)[:60] if title_m else '?'}")

            # Extract IDs
            ids1 = re.findall(r'avito\.ru/[^"]*?/(\d{9,12})/?"', html)
            ids2 = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
            ids3 = re.findall(r'/ad/(\d{9,12})', html)
            ids4 = re.findall(r'"id":(\d{9,12})', html)
            all_ids = list(set(ids1 + ids2 + ids3 + ids4))
            log(f"    IDs: {len(all_ids)} (p1={len(ids1)},p2={len(ids2)},p3={len(ids3)},p4={len(ids4)})")
            for aid in all_ids[:8]:
                log(f"      {aid}")
            if all_ids:
                real_ids.extend(all_ids[:5])
                with open(f"diag_v54_avito_cat_{cu.split('/')[-1]}.html", "w", encoding="utf-8") as f:
                    f.write(html)
                if len(real_ids) >= 5:
                    break
        except Exception as e:
            log(f"  cat ERR: {e}")
        time.sleep(6)

    # --- A4: Test ad pages with found IDs ---
    log("\n--- A4: Test ad pages ---")
    test_ids = real_ids[:5] if real_ids else ["200026938"]
    log(f"  Testing: {test_ids}")
    for aid in test_ids:
        # Try multiple URL patterns
        url_patterns = [
            f"https://www.avito.ru/ad/{aid}",
            f"https://www.avito.ru/moscow/ad/{aid}",
            f"https://www.avito.ru/{aid}",
        ]
        for up in url_patterns[:2]:
            try:
                resp = avito_session.get(up, timeout=25, headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": "https://www.avito.ru/",
                })
                html = resp.text
                title_m = re.search(r"<title[^>]*>([^<]+)", html)
                title = title_m.group(1)[:70] if title_m else "?"
                has_captcha = "captcha" in html.lower()[:3000] or "Доступ ограничен" in html
                has_jsonld = "application/ld+json" in html
                is_404 = "404" in html[:2000] or "не найдена" in html[:2000].lower()
                log(f"  id={aid} {up.split('avito.ru')[1][:25]}: HTTP {resp.status_code}, len={len(html)}")
                log(f"    Title: {title}")
                log(f"    CAPTCHA={has_captcha}, JSON-LD={has_jsonld}, 404={is_404}")

                if not has_captcha and not is_404 and len(html) > 5000:
                    avito_page_ok = True
                    with open(f"diag_v54_avito_ad_{aid}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    log(f"    Saved: diag_v54_avito_ad_{aid}.html ({len(html)} bytes)")

                    # Extract JSON-LD
                    ld_blocks = re.findall(
                        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html, re.DOTALL)
                    log(f"    JSON-LD blocks: {len(ld_blocks)}")
                    for i, block in enumerate(ld_blocks[:3]):
                        try:
                            d = json.loads(block.strip())
                            if isinstance(d, dict):
                                t = d.get("@type", "?")
                                name = d.get("name", d.get("headline", "?"))
                                offers = d.get("offers", {})
                                price = offers.get("price", "?") if isinstance(offers, dict) else "?"
                                log(f"      LD[{i}] type={t} name={str(name)[:50]} price={price}")
                        except Exception:
                            pass
                    break
            except Exception as e:
                log(f"  id={aid}: ERR {e}")
            time.sleep(5)
        if avito_page_ok:
            break

    # --- A5: Try Avito mobile / API endpoints ---
    log("\n--- A5: Avito mobile/API ---")
    try:
        # Avito has a mobile API
        mob_url = "https://api.avito.ru/v1/offers?region=1&category=228&limit=5"
        resp = avito_session.get(mob_url, timeout=20, headers={
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "X-Client-Version": "1.0",
        })
        log(f"  API offers: HTTP {resp.status_code}, len={len(resp.content)}")
        if resp.status_code == 200:
            data = resp.json()
            offers = data.get("offers", data.get("data", []))
            if isinstance(offers, list):
                log(f"    offers: {len(offers)}")
                for o in offers[:3]:
                    log(f"      id={o.get('id','?')} title={str(o.get('title','?'))[:40]}")
                    real_ids.append(str(o.get("id", "")))
    except Exception as e:
        log(f"  A5 ERR: {e}")
    time.sleep(3)

    log(f"\nAVITO RESULT: page_ok={avito_page_ok}, ids={real_ids[:8]}")

    # ============================================================
    # SUMMARY
    # ============================================================
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    log(f"  WB:    ssr_ok={wb_ssr_ok}, saved={wb_data_saved}")
    log(f"  AVITO: page_ok={avito_page_ok}, ids={real_ids[:8]}")
    log(f"  DONE: {datetime.now().isoformat()}")

    if not os.environ.get("AHUB_REDIRECT"):
        with open("diag_v54_out.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\nFull log saved: diag_v54_out.txt ({len(OUT)} lines)")
    else:
        log(f"\nFull log: {len(OUT)} lines (via shell redirect)")

if __name__ == "__main__":
    main()
