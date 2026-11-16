"""Диагностика v56: WB (headless=new + persistent + 65s WBAAS) + Avito (proxy + mobile).
Запуск: C:/Python315/python.exe diag_v56_wb_avito.py
"""
import json, re, time, os, sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
LOG = []

def log(msg):
    print(msg, flush=True)
    LOG.append(msg)

def analyze(html: str) -> dict:
    title_m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.I)
    title = title_m.group(1).strip() if title_m else ""
    return {
        "title": title[:120], "bytes": len(html),
        "jsonld": "application/ld+json" in html,
        "state": any(k in html for k in ("__STATE__","__INITIAL_STATE__","__PRELOADED_STATE__","window.__")),
        "og": 'og:title' in html or 'og:image' in html,
        "wbaas": "wbaas" in html.lower() or "browser-check" in html.lower(),
        "ip_limit": "Доступ ограничен" in html or "проблема с IP" in html,
        "404": "404" in html[:2000] or "не найдена" in html[:2000].lower(),
    }

def extract_jsonld(html: str) -> list:
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL)
    out = []
    for b in blocks:
        try:
            out.append(json.loads(b.strip()))
        except Exception:
            pass
    return out

def main():
    t0 = time.time()
    log("=" * 60)
    log(f"DIAG v56: WB headless=new + AVITO proxy | {datetime.now().isoformat()}")
    log("=" * 60)

    from playwright.sync_api import sync_playwright

    wb_ok = False
    avito_ok = False
    avito_ids = []

    with sync_playwright() as pw:
        # ============================================================
        # WILDBERRIES
        # ============================================================
        log("\n" + "=" * 60)
        log("WILDBERRIES (headless=new + persistent + 65s)")
        log("=" * 60)

        user_data = str(BASE / "pw_wb_profile")
        Path(user_data).mkdir(exist_ok=True)

        browser = pw.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=True,
            args=[
                "--headless=new",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                "--disable-infobars",
            ],
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            color_scheme="light",
        )
        page = browser.new_page()

        # Override navigator.webdriver
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # W1: Main page — pass WBAAS
        log("\n--- W1: WB main (WBAAS 65s wait) ---")
        try:
            page.goto("https://www.wildberries.ru/", timeout=40000, wait_until="domcontentloaded")
            # Wait for WBAAS JS to resolve
            for attempt in range(13):  # 13 x 5s = 65s max
                time.sleep(5)
                try:
                    html = page.content()
                except Exception:
                    continue
                info = analyze(html)
                log(f"  t+{(attempt+1)*5}s: {info['bytes']}B wbaas={info['wbaas']} title={info['title'][:50]}")
                if info["bytes"] > 30000 and not info["wbaas"]:
                    log(f"  WBAAS PASSED at t+{(attempt+1)*5}s!")
                    break
                if info["bytes"] > 50000:
                    log(f"  Large page (maybe content), continuing...")
                    break
        except Exception as e:
            log(f"  Main ERR: {type(e).__name__}: {str(e)[:200]}")

        # Check cookies
        cookies = browser.cookies()
        cookie_names = [c["name"] for c in cookies]
        log(f"  Cookies ({len(cookies)}): {cookie_names[:15]}")
        wbaas_cookie = [c for c in cookies if "wbaas" in c["name"].lower() or "challenge" in c["name"].lower()]
        if wbaas_cookie:
            log(f"  WBAAS cookies: {[c['name'] for c in wbaas_cookie]}")

        # W2: Product pages
        log("\n--- W2: WB product pages ---")
        wb_ids = ["199298871", "314337853", "110835207"]
        for nm in wb_ids:
            url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
            try:
                page.goto(url, timeout=35000, wait_until="domcontentloaded")
                time.sleep(12)
                html = page.content()
                info = analyze(html)
                log(f"  nm={nm}: {info['bytes']}B wbaas={info['wbaas']} jsonld={info['jsonld']} title={info['title'][:60]}")

                if info["bytes"] > 30000 and not info["wbaas"]:
                    wb_ok = True
                    ld = extract_jsonld(html)
                    log(f"    JSON-LD: {len(ld)}")
                    for i, d in enumerate(ld[:3]):
                        if isinstance(d, dict):
                            t = d.get("@type", "?")
                            name = d.get("name", d.get("headline", "?"))
                            offers = d.get("offers", {})
                            price = offers.get("price", "?") if isinstance(offers, dict) else "?"
                            log(f"      LD[{i}] {t} {str(name)[:50]} price={price}")
                    fpath = BASE / f"diag_v56_wb_{nm}.html"
                    fpath.write_text(html, encoding="utf-8")
                    log(f"    Saved: {fpath.name} ({info['bytes']}B)")
                    break
                elif info["bytes"] > 15000 and not info["wbaas"]:
                    wb_ok = True
                    fpath = BASE / f"diag_v56_wb_{nm}.html"
                    fpath.write_text(html, encoding="utf-8")
                    log(f"    Saved (thin): {fpath.name}")
                    break
            except Exception as e:
                log(f"  nm={nm}: ERR {type(e).__name__}: {str(e)[:150]}")
            time.sleep(5)

        # W3: Try catalog.wb.ru API with session cookies
        log("\n--- W3: catalog.wb.ru API (session) ---")
        from curl_cffi import requests as cffi_req
        api_session = cffi_req.Session(impersonate="chrome")
        # Transfer cookies from Playwright
        for c in cookies:
            api_session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        for nm in wb_ids[:3]:
            try:
                api_url = f"https://catalog.wb.ru/cards/v1/detail?nm={nm}"
                resp = api_session.get(api_url, timeout=20, headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.wildberries.ru/",
                    "Origin": "https://www.wildberries.ru",
                })
                log(f"  API nm={nm}: HTTP {resp.status_code}, len={len(resp.content)}")
                if resp.status_code == 200 and len(resp.content) > 50:
                    data = resp.json()
                    prods = data.get("data", {}).get("products", [])
                    log(f"    products: {len(prods)}")
                    if prods:
                        p = prods[0]
                        log(f"    name={p.get('name','?')[:50]} brand={p.get('brand','?')}")
                        wb_ok = True
                        fpath = BASE / f"diag_v56_wb_api_{nm}.json"
                        fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        log(f"    Saved: {fpath.name}")
                        break
            except Exception as e:
                log(f"  API nm={nm}: ERR {str(e)[:150]}")
            time.sleep(3)

        # W4: Search page for live IDs
        log("\n--- W4: WB search ---")
        try:
            page.goto("https://www.wildberries.ru/catalog/0/search.aspx?search=iphone",
                      timeout=30000, wait_until="domcontentloaded")
            time.sleep(12)
            html = page.content()
            info = analyze(html)
            log(f"  search: {info['bytes']}B wbaas={info['wbaas']} title={info['title'][:50]}")
            nm_ids = list(set(re.findall(r'/catalog/(\d{6,12})/detail', html)))
            log(f"  Live IDs: {nm_ids[:10]}")
            if info["bytes"] > 20000:
                fpath = BASE / "diag_v56_wb_search.html"
                fpath.write_text(html, encoding="utf-8")
        except Exception as e:
            log(f"  search ERR: {str(e)[:150]}")

        browser.close()
        log(f"\nWB RESULT: ok={wb_ok}")

        # ============================================================
        # AVITO — proxy + mobile
        # ============================================================
        log("\n" + "=" * 60)
        log("AVITO (proxy + mobile)")
        log("=" * 60)

        PROXY = "http://198.51.100.20:80"

        # A1: Desktop via proxy
        log("\n--- A1: Avito desktop (proxy) ---")
        try:
            browser2 = pw.chromium.launch(
                headless=True,
                proxy={"server": PROXY},
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
            page2.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page2.goto("https://www.avito.ru/", timeout=35000, wait_until="domcontentloaded")
            time.sleep(8)
            html = page2.content()
            info = analyze(html)
            log(f"  Main (proxy): {info['bytes']}B ip_limit={info['ip_limit']} title={info['title'][:60]}")

            ids_m = list(set(re.findall(r'avito\.ru/[^"\'<>]*?/(\d{9,12})', html)))
            ids_m2 = list(set(re.findall(r'"id":(\d{9,12})', html)))
            avito_ids.extend(ids_m[:5] + ids_m2[:5])
            log(f"  IDs: {avito_ids[:10]}")

            # Category
            time.sleep(10)
            page2.goto("https://www.avito.ru/moscow/elektronika/telefony", timeout=35000, wait_until="domcontentloaded")
            time.sleep(8)
            html = page2.content()
            info = analyze(html)
            log(f"  Cat telefony: {info['bytes']}B ip_limit={info['ip_limit']}")
            ids_c = list(set(re.findall(r'avito\.ru/[^"\'<>]*?/(\d{9,12})', html)))
            avito_ids.extend(ids_c[:5])
            log(f"  Cat IDs: {ids_c[:10]}")

            # Ad page
            test_ids = list(set(avito_ids))[:3]
            if not test_ids:
                test_ids = ["1154321987"]
            for aid in test_ids:
                time.sleep(10)
                try:
                    page2.goto(f"https://www.avito.ru/ad/{aid}", timeout=35000, wait_until="domcontentloaded")
                    time.sleep(8)
                    html = page2.content()
                    info = analyze(html)
                    log(f"  Ad {aid}: {info['bytes']}B ip_limit={info['ip_limit']} 404={info['404']} title={info['title'][:50]}")
                    if info["bytes"] > 20000 and not info["ip_limit"] and not info["404"]:
                        avito_ok = True
                        ld = extract_jsonld(html)
                        log(f"    JSON-LD: {len(ld)}")
                        for i, d in enumerate(ld[:3]):
                            if isinstance(d, dict):
                                t = d.get("@type", "?")
                                name = d.get("name", d.get("headline", "?"))
                                offers = d.get("offers", {})
                                price = offers.get("price", "?") if isinstance(offers, dict) else "?"
                                log(f"      LD[{i}] {t} {str(name)[:50]} price={price}")
                        fpath = BASE / f"diag_v56_avito_ad_{aid}.html"
                        fpath.write_text(html, encoding="utf-8")
                        log(f"    Saved: {fpath.name}")
                        break
                except Exception as e:
                    log(f"  Ad {aid}: ERR {str(e)[:150]}")
            browser2.close()
        except Exception as e:
            log(f"  A1 ERR: {type(e).__name__}: {str(e)[:200]}")

        # A2: Mobile via proxy
        log("\n--- A2: Avito mobile (proxy) ---")
        try:
            browser3 = pw.chromium.launch(
                headless=True,
                proxy={"server": PROXY},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                      "--disable-dev-shm-usage"])
            ctx3 = browser3.new_context(
                user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                            "Version/17.0 Mobile/15E148 Safari/604.1"),
                viewport={"width": 414, "height": 896},
                locale="ru-RU",
                is_mobile=True,
                has_touch=True,
            )
            page3 = ctx3.new_page()

            page3.goto("https://www.avito.ru/", timeout=35000, wait_until="domcontentloaded")
            time.sleep(8)
            html = page3.content()
            info = analyze(html)
            log(f"  Mobile main: {info['bytes']}B ip_limit={info['ip_limit']} title={info['title'][:60]}")
            ids_mob = list(set(re.findall(r'avito\.ru/[^"\'<>]*?/(\d{9,12})', html)))
            avito_ids.extend(ids_mob[:5])
            log(f"  Mobile IDs: {ids_mob[:10]}")

            # Mobile ad
            time.sleep(10)
            aid = list(set(avito_ids))[0] if avito_ids else "1154321987"
            page3.goto(f"https://www.avito.ru/ad/{aid}", timeout=35000, wait_until="domcontentloaded")
            time.sleep(8)
            html = page3.content()
            info = analyze(html)
            log(f"  Mobile ad {aid}: {info['bytes']}B ip_limit={info['ip_limit']} 404={info['404']}")
            if info["bytes"] > 15000 and not info["ip_limit"] and not info["404"]:
                avito_ok = True
                fpath = BASE / f"diag_v56_avito_mob_{aid}.html"
                fpath.write_text(html, encoding="utf-8")
                log(f"    Saved: {fpath.name}")
            browser3.close()
        except Exception as e:
            log(f"  A2 ERR: {type(e).__name__}: {str(e)[:200]}")

        log(f"\nAVITO RESULT: ok={avito_ok}, ids={list(set(avito_ids))[:10]}")

    # ============================================================
    # SUMMARY
    # ============================================================
    elapsed = time.time() - t0
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    log(f"  WB:    ok={wb_ok}")
    log(f"  AVITO: ok={avito_ok}, ids={list(set(avito_ids))[:10]}")
    log(f"  Time:  {elapsed:.1f}s")
    log(f"  DONE:  {datetime.now().isoformat()}")

    out_path = BASE / "diag_v56_out.txt"
    out_path.write_text("\n".join(LOG), encoding="utf-8")
    log(f"\nLog: {out_path.name} ({len(LOG)} lines)")

if __name__ == "__main__":
    main()
