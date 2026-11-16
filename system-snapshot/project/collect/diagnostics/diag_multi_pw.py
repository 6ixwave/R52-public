"""
R52 MULTI-PLATFORM: Playwright for WB + Ozon + Avito.
One browser, 3 contexts, sequential. ~60-90s total.
Goal: get REAL price from each platform.
"""
import json, sys, os, time, re

HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    from playwright.sync_api import sync_playwright
    print("[OK] Playwright imported")

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )

        # ================================================================
        # WILDBERRIES
        # ================================================================
        print(f"\n{'='*60}\n[WB] Wildberries nm_id=138278567\n{'='*60}")
        ctx_wb = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page_wb = ctx_wb.new_page()
        nm_id = 138278567
        url_wb = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        try:
            resp = page_wb.goto(url_wb, wait_until="domcontentloaded", timeout=30000)
            print(f"  goto: {resp.status if resp else '?'}")
            # WBAAS challenge: wait for it to resolve
            for attempt in range(4):
                time.sleep(4)
                content = page_wb.content()
                is_challenge = ("wbaas" in content.lower() and len(content) < 5000)
                print(f"  wait[{attempt}]: size={len(content)} challenge={is_challenge}")
                if not is_challenge:
                    break

            content = page_wb.content()
            wb_data = {
                "nm_id": nm_id,
                "url": url_wb,
                "html_size": len(content),
                "title": page_wb.title()[:100],
            }

            # Extract prices from multiple sources
            prices = []
            for pm in re.finditer(r'og:price:amount[^>]*content="([\d.]+)"', content):
                prices.append({"source": "og:price", "value": float(pm.group(1))})
            for pm in re.finditer(r'data-price="(\d+)"', content):
                prices.append({"source": "data-price", "value": int(pm.group(1))})
            for pm in re.finditer(r'"basic"\s*:\s*(\d+)', content):
                prices.append({"source": "basic", "value": int(pm.group(1))})
            for pm in re.finditer(r'"priceU"\s*:\s*(\d+)', content):
                prices.append({"source": "priceU", "value": int(pm.group(1))})
            for pm in re.finditer(r'"price"\s*:\s*(\d+)', content):
                prices.append({"source": "price", "value": int(pm.group(1))})

            wb_data["prices"] = prices
            wb_data["has_price"] = len(prices) > 0

            # Name from meta or title
            m = re.search(r'og:title[^>]*content="([^"]+)"', content)
            if m:
                wb_data["og_title"] = m.group(1)[:80]

            # Media
            imgs = re.findall(r'(https://[^"\']+\.jpg)', content)
            wb_data["images"] = list(set(imgs))[:5]

            # Save
            with open(os.path.join(HERE, "wb_pw.html"), "w", encoding="utf-8") as f:
                f.write(content)

            results["wb"] = wb_data
            print(f"  RESULT: size={len(content)} prices={len(prices)} title={wb_data['title'][:50]}")
            for pr in prices[:5]:
                print(f"    {pr}")
        except Exception as e:
            results["wb"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx_wb.close()

        # ================================================================
        # OZON
        # ================================================================
        print(f"\n{'='*60}\n[OZON] Ozon pid=1600562267\n{'='*60}")
        ctx_oz = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page_oz = ctx_oz.new_page()
        pid = "1600562267"
        url_oz = f"https://www.ozon.ru/product/{pid}/"
        try:
            resp = page_oz.goto(url_oz, wait_until="domcontentloaded", timeout=30000)
            print(f"  goto: {resp.status if resp else '?'}")
            # Antibot challenge: wait
            for attempt in range(4):
                time.sleep(4)
                content = page_oz.content()
                is_challenge = ("Antibot" in content or "challenge" in content.lower())
                print(f"  wait[{attempt}]: size={len(content)} challenge={is_challenge}")
                if not is_challenge:
                    break

            content = page_oz.content()
            oz_data = {
                "product_id": pid,
                "url": url_oz,
                "html_size": len(content),
                "title": page_oz.title()[:100],
            }

            # Prices
            prices = []
            for pm in re.finditer(r'og:price:amount[^>]*content="([\d.]+)"', content):
                prices.append({"source": "og:price", "value": float(pm.group(1))})
            for pm in re.finditer(r'"price"\s*:\s*"?(\d+)"?', content):
                prices.append({"source": "json-price", "value": int(pm.group(1))})
            for pm in re.finditer(r'"v"\s*:\s*(\d{5,})', content):
                prices.append({"source": "state-v", "value": int(pm.group(1))})

            # data-widget
            widgets = re.findall(r'data-widget="(\w+)"', content)
            oz_data["widgets"] = list(set(widgets))[:10]

            # JSON-LD
            jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content, re.DOTALL)
            for block in jsonld[:3]:
                try:
                    d = json.loads(block.strip())
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        oz_data["jsonld_name"] = d.get("name", "")[:60]
                        offers = d.get("offers", {})
                        if isinstance(offers, dict):
                            offers = [offers]
                        for o in offers:
                            if isinstance(o, dict) and o.get("price"):
                                prices.append({"source": "jsonld", "value": float(o["price"]), "cur": o.get("priceCurrency", "RUB")})
                except:
                    pass

            oz_data["prices"] = prices
            oz_data["has_price"] = len(prices) > 0

            with open(os.path.join(HERE, "ozon_pw.html"), "w", encoding="utf-8") as f:
                f.write(content)

            results["ozon"] = oz_data
            print(f"  RESULT: size={len(content)} prices={len(prices)} title={oz_data['title'][:50]}")
            for pr in prices[:5]:
                print(f"    {pr}")
        except Exception as e:
            results["ozon"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx_oz.close()

        # ================================================================
        # AVITO
        # ================================================================
        print(f"\n{'='*60}\n[AVITO] Avito\n{'='*60}")
        ctx_av = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page_av = ctx_av.new_page()
        try:
            # Step 1: Search page
            print("  [1] Opening search...")
            resp = page_av.goto("https://www.avito.ru/moskva/noutbuki",
                                 wait_until="domcontentloaded", timeout=30000)
            print(f"  search goto: {resp.status if resp else '?'}")
            time.sleep(5)
            content = page_av.content()
            print(f"  search: size={len(content)}")

            av_data = {
                "search_url": "https://www.avito.ru/moskva/noutbuki",
                "search_size": len(content),
                "search_title": page_av.title()[:100],
            }

            # Find ad IDs
            ad_ids = re.findall(r'avito\.ru/[a-z0-9-]+/(\d{8,})', content)
            if not ad_ids:
                ad_ids = re.findall(r'/ad/(\d{8,})', content)
            if not ad_ids:
                ad_ids = re.findall(r'"adId"\s*:\s*"?(\d{8,})', content)
            av_data["found_ids"] = ad_ids[:10]
            print(f"  IDs found: {ad_ids[:5]}")

            # Step 2: Open first listing
            if ad_ids:
                lid = ad_ids[0]
                url_av = f"https://www.avito.ru/ad/{lid}"
                print(f"  [2] Opening listing {lid}...")
                page_av.goto(url_av, wait_until="domcontentloaded", timeout=30000)
                time.sleep(4)
                content2 = page_av.content()

                av_data["listing_id"] = lid
                av_data["listing_url"] = url_av
                av_data["listing_size"] = len(content2)
                av_data["listing_title"] = page_av.title()[:100]
                print(f"  listing: size={len(content2)}")

                # Prices
                prices = []
                for pm in re.finditer(r'og:price:amount[^>]*content="([\d.]+)"', content2):
                    prices.append({"source": "og:price", "value": float(pm.group(1))})
                for pm in re.finditer(r'"price"\s*:\s*"?(\d+)"?', content2):
                    prices.append({"source": "json-price", "value": int(pm.group(1))})

                # JSON-LD
                jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content2, re.DOTALL)
                for block in jsonld[:3]:
                    try:
                        d = json.loads(block.strip())
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            av_data["jsonld_name"] = d.get("name", "")[:60]
                            offers = d.get("offers", {})
                            if isinstance(offers, dict):
                                offers = [offers]
                            for o in offers:
                                if isinstance(o, dict) and o.get("price"):
                                    prices.append({"source": "jsonld", "value": float(o["price"]), "cur": o.get("priceCurrency", "RUB")})
                    except:
                        pass

                av_data["prices"] = prices
                av_data["has_price"] = len(prices) > 0

                with open(os.path.join(HERE, "avito_pw.html"), "w", encoding="utf-8") as f:
                    f.write(content2)

                print(f"  RESULT: prices={len(prices)} title={av_data['listing_title'][:50]}")
                for pr in prices[:5]:
                    print(f"    {pr}")
            else:
                av_data["error"] = "No live IDs found in search page"
                # Save search page for analysis
                with open(os.path.join(HERE, "avito_pw_search.html"), "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"  [WARN] No IDs. Saved search page.")

            results["avito"] = av_data
        except Exception as e:
            results["avito"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx_av.close()

        browser.close()

    # ================================================================
    # SAVE SUMMARY
    # ================================================================
    out_path = os.path.join(HERE, "multi_pw_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    for plat, data in results.items():
        if "error" in data:
            print(f"  {plat:6s}: ERROR {data['error'][:60]}")
        else:
            hp = data.get("has_price", False)
            pc = len(data.get("prices", []))
            sz = data.get("html_size", data.get("listing_size", "?"))
            tt = data.get("title", data.get("listing_title", ""))[:40]
            print(f"  {plat:6s}: price={hp} count={pc} size={sz} title={tt}")

    print(f"\nSaved -> {out_path}")
    print("DONE")

if __name__ == "__main__":
    main()
