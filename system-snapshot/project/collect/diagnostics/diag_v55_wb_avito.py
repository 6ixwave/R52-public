"""Диагностика v55: WB (Playwright WBAAS solve) + Avito (mobile + delays).
Запуск: C:/Python315/python.exe diag_v55_wb_avito.py
"""
import json, re, time, os, sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
results = []

def log(msg):
    print(msg, flush=True)
    results.append(msg)

def analyze(html: str) -> dict:
    title_m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.I)
    title = title_m.group(1).strip() if title_m else ""
    has_jsonld = "application/ld+json" in html
    has_state = any(k in html for k in ("__STATE__", "__INITIAL_STATE__", "__PRELOADED_STATE__", "window.__"))
    has_og = 'og:title' in html or 'og:image' in html
    has_wbaas = "wbaas" in html.lower() or "browser-check" in html.lower()
    has_429 = "Доступ ограничен" in html or "rate" in html.lower()[:3000]
    has_404 = "404" in html[:2000] or "не найдена" in html[:2000].lower()
    return {"title": title[:120], "bytes": len(html), "jsonld": has_jsonld,
            "state": has_state, "og": has_og, "wbaas": has_wbaas,
            "ip_limit": has_429, "404": has_404}

def extract_jsonld(html: str) -> list:
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL)
    out = []
    for b in blocks:
        try:
            d = json.loads(b.strip())
            out.append(d)
        except Exception:
            pass
    return out

def main():
    log("=" * 60)
    log(f"DIAG v55: WB WBAAS + AVITO MOBILE | {datetime.now().isoformat()}")
    log("=" * 60)

    from playwright.sync_api import sync_playwright

    wb_ok = False
    avito_ok = False
    avito_real_ids = []

    with sync_playwright() as pw:
        # ============================================================
        # WILDBERRIES — Playwright WBAAS
        # ============================================================
        log("\n" + "=" * 60)
        log("WILDBERRIES (Playwright + WBAAS)")
        log("=" * 60)

        wb_ids = ["199298871", "314337853", "110835207"]
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = ctx.new_page()

        # W1: Main page to get initial cookies / pass WBAAS
        log("\n--- W1: WB main page (WBAAS init) ---")
        try:
            page.goto("https://www.wildberries.ru/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(8)  # Wait for WBAAS JS
            html = page.content()
            info = analyze(html)
            log(f"  Main: {info['bytes']}B, title={info['title'][:60]}, wbaas={info['wbaas']}")
            cookies = ctx.cookies()
            log(f"  Cookies: {[c['name'] for c in cookies][:10]}")

            # If still WBAAS, wait more
            if info["wbaas"] and info["bytes"] < 20000:
                log("  WBAAS still active, waiting 15s...")
                time.sleep(15)
                html = page.content()
                info = analyze(html)
                log(f"  After wait: {info['bytes']}B, title={info['title'][:60]}, wbaas={info['wbaas']}")
        except Exception as e:
            log(f"  Main ERR: {type(e).__name__}: {str(e)[:200]}")
        time.sleep(3)

        # W2: Product pages
        log("\n--- W2: WB product pages ---")
        for nm in wb_ids:
            url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                time.sleep(10)  # WBAAS + render
                html = page.content()
                info = analyze(html)
                log(f"  nm={nm}: {info['bytes']}B, title={info['title'][:70]}")
                log(f"    wbaas={info['wbaas']}, jsonld={info['jsonld']}, og={info['og']}, state={info['state']}")

                if info["wbaas"] and info["bytes"] < 20000:
                    log("    WBAAS challenge, waiting 12s...")
                    time.sleep(12)
                    html = page.content()
                    info = analyze(html)
                    log(f"    Retry: {info['bytes']}B, title={info['title'][:70]}, wbaas={info['wbaas']}")

                if info["bytes"] > 30000 and not info["wbaas"]:
                    wb_ok = True
                    ld = extract_jsonld(html)
                    log(f"    JSON-LD: {len(ld)} blocks")
                    for i, d in enumerate(ld[:3]):
                        if isinstance(d, dict):
                            t = d.get("@type", "?")
                            name = d.get("name", d.get("headline", "?"))
                            offers = d.get("offers", {})
                            price = offers.get("price", "?") if isinstance(offers, dict) else "?"
                            log(f"      LD[{i}] type={t} name={str(name)[:50]} price={price}")
                    # Save
                    fpath = BASE / f"diag_v55_wb_{nm}.html"
                    fpath.write_text(html, encoding="utf-8")
                    log(f"    Saved: {fpath.name} ({info['bytes']}B)")
                    break
                elif info["bytes"] > 15000:
                    # Maybe thin content
                    wb_ok = True
                    fpath = BASE / f"diag_v55_wb_{nm}.html"
                    fpath.write_text(html, encoding="utf-8")
                    log(f"    Saved (thin): {fpath.name}")
                    break
            except Exception as e:
                log(f"  nm={nm}: ERR {type(e).__name__}: {str(e)[:200]}")
            time.sleep(5)

        # W3: Mobile WB
        log("\n--- W3: m.wildberries.ru ---")
        for nm in wb_ids[:2]:
            url = f"https://m.wildberries.ru/catalog/{nm}/detail.aspx"
            try:
                page.goto(url, timeout=25000, wait_until="domcontentloaded")
                time.sleep(8)
                html = page.content()
                info = analyze(html)
                log(f"  mobile nm={nm}: {info['bytes']}B, title={info['title'][:60]}, wbaas={info['wbaas']}")
                if info["bytes"] > 20000 and not info["wbaas"]:
                    wb_ok = True
                    fpath = BASE / f"diag_v55_wb_mob_{nm}.html"
                    fpath.write_text(html, encoding="utf-8")
                    log(f"    Saved: {fpath.name}")
                    break
            except Exception as e:
                log(f"  mobile nm={nm}: ERR {str(e)[:150]}")
            time.sleep(4)

        # W4: Try search page to find live IDs
        log("\n--- W4: WB search (find live IDs) ---")
        try:
            page.goto("https://www.wildberries.ru/catalog/0/search.aspx?search=iphone",
                      timeout=25000, wait_until="domcontentloaded")
            time.sleep(10)
            html = page.content()
            info = analyze(html)
            log(f"  search: {info['bytes']}B, title={info['title'][:60]}")
            # Extract nm IDs from links
            nm_ids = re.findall(r'/catalog/(\d{6,12})/detail', html)
            log(f"  Live nm IDs: {list(set(nm_ids))[:10]}")
            if nm_ids and info["bytes"] > 20000:
                fpath = BASE / "diag_v55_wb_search.html"
                fpath.write_text(html, encoding="utf-8")
        except Exception as e:
            log(f"  search ERR: {str(e)[:150]}")
        time.sleep(3)

        browser.close()

        log(f"\nWB RESULT: ok={wb_ok}")

        # ============================================================
        # AVITO — mobile + desktop with delays
        # ============================================================
        log("\n" + "=" * 60)
        log("AVITO (mobile + desktop)")
        log("=" * 60)

        browser2 = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080"])
        ctx2 = browser2.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page2 = ctx2.new_page()

        # A1: Avito main page
        log("\n--- A1: Avito main ---")
        try:
            page2.goto("https://www.avito.ru/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(6)
            html = page2.content()
            info = analyze(html)
            log(f"  Main: {info['bytes']}B, title={info['title'][:60]}, ip_limit={info['ip_limit']}")
            cookies = ctx2.cookies()
            log(f"  Cookies: {[c['name'] for c in cookies][:10]}")

            # Extract ad IDs from main page
            ids_main = re.findall(r'avito\.ru/[^"\'<>]*?/(\d{9,12})', html)
            ids_main2 = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
            ids_main3 = re.findall(r'"id":(\d{9,12})', html)
            all_main = list(set(ids_main + ids_main2 + ids_main3))
            log(f"  IDs from main: {all_main[:10]}")
            avito_real_ids.extend(all_main[:5])
        except Exception as e:
            log(f"  Main ERR: {str(e)[:200]}")
        time.sleep(8)

        # A2: Category page (desktop)
        log("\n--- A2: Avito category ---")
        cat_urls = [
            "https://www.avito.ru/moscow/elektronika/telefony",
            "https://www.avito.ru/moscow/transport/avto",
            "https://www.avito.ru/moscow/elektronika/noutbuki",
        ]
        for cu in cat_urls:
            try:
                page2.goto(cu, timeout=30000, wait_until="domcontentloaded")
                time.sleep(8)
                html = page2.content()
                info = analyze(html)
                log(f"  {cu.split('avito.ru')[1][:35]}: {info['bytes']}B, ip_limit={info['ip_limit']}")
                log(f"    title={info['title'][:60]}")

                ids_cat = re.findall(r'avito\.ru/[^"\'<>]*?/(\d{9,12})', html)
                ids_cat2 = re.findall(r'data-ad-id=["\'](\d+)["\']', html)
                ids_cat3 = re.findall(r'"id":(\d{9,12})', html)
                all_cat = list(set(ids_cat + ids_cat2 + ids_cat3))
                log(f"    IDs: {all_cat[:10]}")
                avito_real_ids.extend(all_cat[:5])

                if info["bytes"] > 30000 and not info["ip_limit"]:
                    fpath = BASE / f"diag_v55_avito_cat_{cu.split('/')[-1]}.html"
                    fpath.write_text(html, encoding="utf-8")
                    if len(avito_real_ids) >= 3:
                        break
            except Exception as e:
                log(f"  cat ERR: {str(e)[:150]}")
            time.sleep(8)

        # A3: Mobile Avito (m.avito.ru or different UA)
        log("\n--- A3: Avito mobile ---")
        ctx2.close()
        ctx3 = browser2.new_context(
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"),
            viewport={"width": 414, "height": 896},
            locale="ru-RU",
            is_mobile=True,
            has_touch=True,
        )
        page3 = ctx3.new_page()

        # Try mobile main
        try:
            page3.goto("https://www.avito.ru/", timeout=30000, wait_until="domcontentloaded")
            time.sleep(6)
            html = page3.content()
            info = analyze(html)
            log(f"  Mobile main: {info['bytes']}B, title={info['title'][:60]}, ip_limit={info['ip_limit']}")
            ids_mob = re.findall(r'avito\.ru/[^"\'<>]*?/(\d{9,12})', html)
            ids_mob2 = re.findall(r'"id":(\d{9,12})', html)
            all_mob = list(set(ids_mob + ids_mob2))
            log(f"  Mobile IDs: {all_mob[:10]}")
            avito_real_ids.extend(all_mob[:5])
        except Exception as e:
            log(f"  Mobile main ERR: {str(e)[:150]}")
        time.sleep(6)

        # A4: Test ad pages with found IDs
        log("\n--- A4: Avito ad pages ---")
        test_ids = list(set(avito_real_ids))[:5]
        if not test_ids:
            test_ids = ["1154321987", "200026938"]
        log(f"  Testing: {test_ids}")

        for aid in test_ids:
            url_patterns = [
                f"https://www.avito.ru/ad/{aid}",
                f"https://www.avito.ru/moscow/ad/{aid}",
            ]
            for up in url_patterns:
                try:
                    page2 = ctx2.new_page() if not ctx2.pages else ctx2.pages[0]
                    page2.goto(up, timeout=30000, wait_until="domcontentloaded")
                    time.sleep(8)
                    html = page2.content()
                    info = analyze(html)
                    log(f"  id={aid} {up.split('avito.ru')[1][:30]}: {info['bytes']}B")
                    log(f"    title={info['title'][:60]}, ip_limit={info['ip_limit']}, 404={info['404']}")

                    if info["bytes"] > 20000 and not info["ip_limit"] and not info["404"]:
                        avito_ok = True
                        ld = extract_jsonld(html)
                        log(f"    JSON-LD: {len(ld)} blocks")
                        for i, d in enumerate(ld[:3]):
                            if isinstance(d, dict):
                                t = d.get("@type", "?")
                                name = d.get("name", d.get("headline", "?"))
                                offers = d.get("offers", {})
                                price = offers.get("price", "?") if isinstance(offers, dict) else "?"
                                log(f"      LD[{i}] type={t} name={str(name)[:50]} price={price}")
                        fpath = BASE / f"diag_v55_avito_ad_{aid}.html"
                        fpath.write_text(html, encoding="utf-8")
                        log(f"    Saved: {fpath.name} ({info['bytes']}B)")
                        break
                except Exception as e:
                    log(f"  id={aid}: ERR {str(e)[:150]}")
                time.sleep(6)
            if avito_ok:
                break

        browser2.close()
        log(f"\nAVITO RESULT: ok={avito_ok}, ids={list(set(avito_real_ids))[:10]}")

    # ============================================================
    # SUMMARY
    # ============================================================
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    log(f"  WB:    ok={wb_ok}")
    log(f"  AVITO: ok={avito_ok}, ids={list(set(avito_real_ids))[:10]}")
    log(f"  DONE: {datetime.now().isoformat()}")

    # Save results
    out_path = BASE / "diag_v55_out.txt"
    out_path.write_text("\n".join(results), encoding="utf-8")
    log(f"\nLog saved: {out_path.name} ({len(results)} lines)")

if __name__ == "__main__":
    main()
