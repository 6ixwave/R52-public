"""
R52 PW3: WB WBAAS aggressive + Avito live ID hunt + Ozon confirm.
~90s total.
"""
import json, sys, os, time, re

HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    from playwright.sync_api import sync_playwright
    print("[OK] Playwright")
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
            ]
        )

        # ================================================================
        # WB: Aggressive WBAAS
        # ================================================================
        print(f"\n{'='*60}\n[WB] nm_id=138278567 — WBAAS aggressive\n{'='*60}")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        nm_id = 138278567
        url_wb = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        try:
            # Intercept API responses (card.wb.ru)
            api_data = []
            def on_response(resp):
                if "card.wb.ru" in resp.url or "search.wb.ru" in resp.url:
                    try:
                        body = resp.text()
                        api_data.append({"url": resp.url, "status": resp.status, "body": body[:2000]})
                        print(f"  [API] {resp.url[:60]} -> {resp.status} ({len(body)}B)")
                    except:
                        pass
            page.on("response", on_response)

            resp = page.goto(url_wb, wait_until="load", timeout=40000)
            print(f"  goto: {resp.status if resp else '?'}")

            # Phase 1: Wait for WBAAS JS to execute (up to 15s)
            print("  Phase 1: waiting for WBAAS JS...")
            for i in range(5):
                time.sleep(3)
                try:
                    # Check cookies
                    cookies = ctx.cookies()
                    cookie_names = [c['name'] for c in cookies]
                    content = page.content()
                    sz = len(content)
                    print(f"  [{i}] size={sz} cookies={cookie_names[:5]} url={page.url[:50]}")

                    if sz > 50000:
                        print(f"  *** PAGE LOADED ({sz}B) ***")
                        break

                    # Try to evaluate: is WBAAS done?
                    try:
                        wbaas_done = page.evaluate("() => { try { return document.cookie.includes('wbaas') || document.cookie.includes('_wb_'); } catch(e) { return false; } }")
                        print(f"      wbaas_cookie={wbaas_done}")
                    except:
                        pass
                except Exception as e:
                    print(f"  [{i}] navigating: {str(e)[:50]}")

            # Phase 2: If still small, try reload
            try:
                content = page.content()
            except:
                content = ""
            if len(content) < 20000:
                print("  Phase 2: reloading...")
                time.sleep(2)
                page.reload(wait_until="load", timeout=30000)
                time.sleep(5)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except:
                    pass
                content = page.content()
                print(f"  after reload: size={len(content)}")

            # Phase 3: If STILL small, try navigating to main page first
            if len(content) < 20000:
                print("  Phase 3: going to main page first...")
                page.goto("https://www.wildberries.ru/", wait_until="load", timeout=30000)
                time.sleep(5)
                main_sz = len(page.content())
                print(f"  main page: {main_sz}B")
                cookies = ctx.cookies()
                print(f"  cookies after main: {[c['name'] for c in cookies][:8]}")
                # Now go to product
                page.goto(url_wb, wait_until="load", timeout=30000)
                time.sleep(5)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except:
                    pass
                content = page.content()
                print(f"  product after main: size={len(content)}")

            # Extract data
            wb_data = {"nm_id": nm_id, "url": page.url, "html_size": len(content),
                       "title": page.title()[:100]}

            prices = []
            for pat, src in [
                (r'og:price:amount[^>]*content="([\d.]+)"', "og"),
                (r'data-price="(\d+)"', "dp"),
                (r'"basic"\s*:\s*(\d+)', "basic"),
                (r'"priceU"\s*:\s*(\d+)', "priceU"),
                (r'"price"\s*:\s*(\d+)', "price"),
            ]:
                for pm in re.finditer(pat, content):
                    try:
                        prices.append({"source": src, "value": float(pm.group(1))})
                    except:
                        pass

            # API intercept data
            if api_data:
                wb_data["api_calls"] = len(api_data)
                for ad in api_data[:3]:
                    s = ad["body"]
                    for pm in re.finditer(r'"(?:basic|priceU|price)"\s*:\s*(\d+)', s):
                        prices.append({"source": "api", "value": int(pm.group(1))})

            wb_data["prices"] = prices
            wb_data["has_price"] = len(prices) > 0

            m = re.search(r'og:title[^>]*content="([^"]+)"', content)
            if m:
                wb_data["og_title"] = m.group(1)[:80]

            with open(os.path.join(HERE, "wb_pw3.html"), "w", encoding="utf-8") as f:
                f.write(content)

            results["wb"] = wb_data
            print(f"  RESULT: size={len(content)} prices={len(prices)} api={len(api_data)}")
            for pr in prices[:5]:
                print(f"    {pr}")
        except Exception as e:
            results["wb"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx.close()

        # ================================================================
        # AVITO: Find live ID
        # ================================================================
        print(f"\n{'='*60}\n[AVITO] Live ID hunt\n{'='*60}")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        try:
            # Strategy: try IDs from different ranges
            # Avito 2026 IDs are likely 10-11 digits
            # "200026938" from 404 page (9 digits) — try it
            # Recent: try 10-digit and 11-digit ranges

            candidates = [
                "200026938",       # from 404 page
                "1097975275",      # known WB ID (might exist on Avito too? unlikely)
                "1150000000",      # mid-range 10-digit
                "1149999999",
                "1150000001",
                "1100000000",
                "1099999999",
                "1050000000",
                "1025000000",
                "1000000000",
                "999999999",
                "950000000",
                "900000000",
                "850000000",
                "800000000",
                "750000000",
                "7494308705",     # old (was 404)
                "7500000000",     # 10-digit
                "7600000000",
                "7700000000",
                "7800000000",
                "7900000000",
                "8000000000",
            ]

            live_id = None
            live_url = None
            live_content = ""

            for i, lid in enumerate(candidates):
                url = f"https://www.avito.ru/ad/{lid}"
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(3)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except:
                        pass
                    content = page.content()
                    sz = len(content)
                    title = page.title()[:60]
                    is_404 = "404" in title or "не найдена" in title.lower()
                    is_blocked = "ограничен" in title.lower() or "captcha" in title.lower()

                    status_tag = "LIVE" if not is_404 and not is_blocked else ("404" if is_404 else "BLOCKED")
                    print(f"  [{i:2d}] {lid}: {resp.status if resp else '?'} {sz}B {status_tag} '{title}'")

                    if not is_404 and not is_blocked and sz > 10000:
                        live_id = lid
                        live_url = url
                        live_content = content
                        print(f"  *** LIVE: {lid} ***")
                        break
                except Exception as e:
                    print(f"  [{i:2d}] {lid}: ERR {str(e)[:40]}")
                time.sleep(1)

            av_data = {"live_id": live_id, "live_url": live_url, "live_size": len(live_content)}

            if live_id:
                # Extract prices
                prices = []
                for pat, src in [
                    (r'og:price:amount[^>]*content="([\d.]+)"', "og"),
                    (r'"price"\s*:\s*"?(\d+)"?', "json"),
                    (r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)', "html"),
                ]:
                    for pm in re.finditer(pat, live_content):
                        try:
                            val = float(pm.group(1).replace(" ", "").replace("\u00a0", ""))
                            if val > 10:
                                prices.append({"source": src, "value": val})
                        except:
                            pass

                # JSON-LD
                jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', live_content, re.DOTALL)
                for block in jsonld[:3]:
                    try:
                        d = json.loads(block.strip())
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            av_data["jsonld_name"] = d.get("name", "")[:60]
                            offers = d.get("offers", {})
                            if isinstance(offers, dict):
                                offers = [offers]
                            for o in (offers if isinstance(offers, list) else []):
                                if isinstance(o, dict) and o.get("price"):
                                    prices.append({"source": "jsonld", "value": float(o["price"])})
                    except:
                        pass

                av_data["prices"] = prices
                av_data["has_price"] = len(prices) > 0
                av_data["title"] = page.title()[:80]

                with open(os.path.join(HERE, "avito_pw3.html"), "w", encoding="utf-8") as f:
                    f.write(live_content)

                print(f"  RESULT: id={live_id} prices={len(prices)}")
                for pr in prices[:5]:
                    print(f"    {pr}")
            else:
                av_data["error"] = "No live ID found in candidates"
                print(f"  [FAIL] No live ID found")

            results["avito"] = av_data
        except Exception as e:
            results["avito"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx.close()

        # ================================================================
        # OZON: Quick confirm + structured data
        # ================================================================
        print(f"\n{'='*60}\n[OZON] Confirm pid=1600562267\n{'='*60}")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        pid = "1600562267"
        try:
            resp = page.goto(f"https://www.ozon.ru/product/{pid}/",
                             wait_until="domcontentloaded", timeout=30000)
            print(f"  goto: {resp.status if resp else '?'}")
            time.sleep(4)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            content = page.content()

            oz_data = {"product_id": pid, "url": page.url, "html_size": len(content),
                       "title": page.title()[:100]}

            prices = []
            for pat, src in [
                (r'og:price:amount[^>]*content="([\d.]+)"', "og"),
                (r'"price"\s*:\s*"?(\d+)"?', "json"),
                (r'"v"\s*:\s*(\d{5,})', "state"),
            ]:
                for pm in re.finditer(pat, content):
                    try:
                        prices.append({"source": src, "value": float(pm.group(1))})
                    except:
                        pass

            # JSON-LD (full)
            jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content, re.DOTALL)
            for block in jsonld[:5]:
                try:
                    d = json.loads(block.strip())
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        oz_data["jsonld"] = {
                            "name": d.get("name", "")[:80],
                            "sku": d.get("sku", ""),
                            "mpn": d.get("mpn", ""),
                            "brand": d.get("brand", {}).get("name", "") if isinstance(d.get("brand"), dict) else str(d.get("brand", ""))[:40],
                        }
                        offers = d.get("offers", {})
                        if isinstance(offers, dict):
                            offers = [offers]
                        for o in (offers if isinstance(offers, list) else []):
                            if isinstance(o, dict) and o.get("price"):
                                prices.append({"source": "jsonld", "value": float(o["price"]),
                                               "cur": o.get("priceCurrency", "RUB"),
                                               "avail": o.get("availability", "")})
                except:
                    pass

            # Images
            imgs = re.findall(r'(https://ir\.ozone\.ru/[^"\']+\.jpg)', content)
            oz_data["images"] = list(dict.fromkeys(imgs))[:8]  # unique, preserve order

            # Video
            vids = re.findall(r'(https://[^"\']*\.mp4)', content)
            oz_data["videos"] = list(dict.fromkeys(vids))[:3]

            oz_data["prices"] = prices
            oz_data["has_price"] = len(prices) > 0

            results["ozon"] = oz_data
            print(f"  RESULT: size={len(content)} prices={len(prices)} imgs={len(oz_data['images'])}")
            for pr in prices[:5]:
                print(f"    {pr}")
            print(f"  name: {oz_data.get('jsonld', {}).get('name', 'N/A')}")
        except Exception as e:
            results["ozon"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx.close()

        browser.close()

    # Save
    out = os.path.join(HERE, "pw3_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    for plat, data in results.items():
        if "error" in data:
            print(f"  {plat:6s}: ERROR {data['error'][:60]}")
        else:
            hp = data.get("has_price", False)
            pc = len(data.get("prices", []))
            sz = data.get("html_size", data.get("live_size", "?"))
            tt = data.get("title", data.get("og_title", ""))[:40]
            print(f"  {plat:6s}: price={hp} n={pc} size={sz} '{tt}'")

    print(f"\nSaved -> {out}")
    print("DONE")

if __name__ == "__main__":
    main()
