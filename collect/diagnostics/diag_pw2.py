"""
R52 PW2: Fix WB (WBAAS wait), confirm Ozon, Avito direct listing.
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
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"]
        )

        # ================================================================
        # WB: WBAAS challenge → wait for redirect
        # ================================================================
        print(f"\n{'='*60}\n[WB] nm_id=138278567\n{'='*60}")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        nm_id = 138278567
        url_wb = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        try:
            resp = page.goto(url_wb, wait_until="domcontentloaded", timeout=30000)
            print(f"  goto: {resp.status if resp else '?'}")

            # WBAAS: page navigates after JS sets cookie. Wait for it.
            # Strategy: poll until content is large (>50KB = real page) or timeout
            real_content = None
            for i in range(8):
                time.sleep(3)
                try:
                    content = page.content()
                    sz = len(content)
                    is_wbaas = ("wbaas" in content.lower() and sz < 5000)
                    print(f"  poll[{i}]: size={sz} wbaas={is_wbaas} url={page.url[:60]}")
                    if sz > 50000 and not is_wbaas:
                        real_content = content
                        break
                    if sz > 20000 and not is_wbaas:
                        # Might be loading, wait more
                        real_content = content
                except Exception as e:
                    # "navigating" error = WBAAS redirect in progress
                    print(f"  poll[{i}]: navigating ({str(e)[:50]})")
                    time.sleep(2)

            if not real_content:
                # Last try: wait for networkidle
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                    real_content = page.content()
                    print(f"  networkidle: size={len(real_content)}")
                except:
                    real_content = page.content()
                    print(f"  final: size={len(real_content)}")

            content = real_content
            wb_data = {"nm_id": nm_id, "url": page.url, "html_size": len(content),
                       "title": page.title()[:100]}

            # Prices
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
            # WB specific: price in data-state or __NEXT_DATA__
            for pm in re.finditer(r'"price"\s*:\s*\{[^}]*"v"\s*:\s*(\d+)', content):
                prices.append({"source": "price.v", "value": int(pm.group(1))})

            wb_data["prices"] = prices
            wb_data["has_price"] = len(prices) > 0

            m = re.search(r'og:title[^>]*content="([^"]+)"', content)
            if m:
                wb_data["og_title"] = m.group(1)[:80]

            imgs = re.findall(r'(https://[^"\']+\.jpg)', content)
            wb_data["images"] = list(set(imgs))[:5]

            with open(os.path.join(HERE, "wb_pw2.html"), "w", encoding="utf-8") as f:
                f.write(content)

            results["wb"] = wb_data
            print(f"  RESULT: size={len(content)} prices={len(prices)} title={wb_data['title'][:50]}")
            for pr in prices[:5]:
                print(f"    {pr}")
        except Exception as e:
            results["wb"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx.close()

        # ================================================================
        # OZON: Confirm + full data
        # ================================================================
        print(f"\n{'='*60}\n[OZON] pid=1600562267\n{'='*60}")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        pid = "1600562267"
        url_oz = f"https://www.ozon.ru/product/{pid}/"
        try:
            resp = page.goto(url_oz, wait_until="domcontentloaded", timeout=30000)
            print(f"  goto: {resp.status if resp else '?'}")
            # Wait for full render
            time.sleep(5)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            content = page.content()
            oz_data = {"product_id": pid, "url": page.url, "html_size": len(content),
                       "title": page.title()[:100]}

            prices = []
            for pm in re.finditer(r'og:price:amount[^>]*content="([\d.]+)"', content):
                prices.append({"source": "og:price", "value": float(pm.group(1))})
            for pm in re.finditer(r'"price"\s*:\s*"?(\d+)"?', content):
                prices.append({"source": "json-price", "value": int(pm.group(1))})
            for pm in re.finditer(r'"v"\s*:\s*(\d{5,})', content):
                prices.append({"source": "state-v", "value": int(pm.group(1))})

            # JSON-LD
            jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content, re.DOTALL)
            for block in jsonld[:5]:
                try:
                    d = json.loads(block.strip())
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        oz_data["jsonld_name"] = d.get("name", "")[:60]
                        offers = d.get("offers", {})
                        if isinstance(offers, dict):
                            offers = [offers]
                        for o in offers:
                            if isinstance(o, dict) and o.get("price"):
                                prices.append({"source": "jsonld", "value": float(o["price"]),
                                               "cur": o.get("priceCurrency", "RUB"),
                                               "avail": o.get("availability", "")})
                except:
                    pass

            # Media
            imgs = re.findall(r'(https://[^"\']+\.jpg)', content)
            oz_data["images"] = list(set(imgs))[:5]

            oz_data["prices"] = prices
            oz_data["has_price"] = len(prices) > 0

            with open(os.path.join(HERE, "ozon_pw2.html"), "w", encoding="utf-8") as f:
                f.write(content)

            results["ozon"] = oz_data
            print(f"  RESULT: size={len(content)} prices={len(prices)} title={oz_data['title'][:50]}")
            for pr in prices[:5]:
                print(f"    {pr}")
        except Exception as e:
            results["ozon"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx.close()

        # ================================================================
        # AVITO: Direct listing + longer wait + networkidle
        # ================================================================
        print(f"\n{'='*60}\n[AVITO] Direct listing\n{'='*60}")
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        page = ctx.new_page()
        try:
            # Try direct listing first (known ID, even if 404 before)
            # Then try search with longer wait
            targets = [
                ("https://www.avito.ru/ad/7494308705", "direct_7494308705"),
                ("https://www.avito.ru/moskva/noutbuki", "search_noutbuki"),
                ("https://www.avito.ru/moskva/elektronika", "search_electronics"),
            ]

            av_data = {}
            for url, label in targets:
                print(f"  [{label}] {url}")
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    print(f"    goto: {resp.status if resp else '?'}")
                    # Wait for JS to resolve (439 → 200, or WBAAS-like)
                    time.sleep(6)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except:
                        pass
                    content = page.content()
                    sz = len(content)
                    title = page.title()[:80]
                    print(f"    size={sz} title={title}")

                    if sz < 5000:
                        print(f"    [SKIP] too small")
                        continue

                    # Check if it's a "real" page or still blocked
                    is_blocked = any(x in title.lower() for x in ["ограничен", "captcha", "wait", "check"])
                    print(f"    blocked={is_blocked}")

                    # Find ad IDs
                    ad_ids = re.findall(r'avito\.ru/[a-z0-9-]+/(\d{8,})', content)
                    if not ad_ids:
                        ad_ids = re.findall(r'/ad/(\d{8,})', content)
                    if not ad_ids:
                        ad_ids = re.findall(r'"adId"\s*:\s*"?(\d{8,})', content)
                    if not ad_ids:
                        # Try data attributes
                        ad_ids = re.findall(r'data-ad-id=["\']?(\d{8,})', content)
                    print(f"    IDs: {ad_ids[:8]}")

                    if label.startswith("search") and ad_ids:
                        # Found IDs from search, open first one
                        lid = ad_ids[0]
                        print(f"    Opening listing {lid}...")
                        page.goto(f"https://www.avito.ru/ad/{lid}", wait_until="domcontentloaded", timeout=30000)
                        time.sleep(5)
                        try:
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except:
                            pass
                        content = page.content()
                        sz = len(content)
                        title = page.title()[:80]
                        print(f"    listing: size={sz} title={title}")

                    # Extract prices
                    prices = []
                    for pm in re.finditer(r'og:price:amount[^>]*content="([\d.]+)"', content):
                        prices.append({"source": "og:price", "value": float(pm.group(1))})
                    for pm in re.finditer(r'"price"\s*:\s*"?(\d+)"?', content):
                        prices.append({"source": "json-price", "value": int(pm.group(1))})
                    for pm in re.finditer(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]+)\s*(?:₽|руб)', content, re.I):
                        try:
                            val = int(pm.group(1).replace(" ", "").replace("\u00a0", ""))
                            if val > 100:
                                prices.append({"source": "html-price", "value": val})
                        except:
                            pass

                    # JSON-LD
                    jsonld = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', content, re.DOTALL)
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
                                        prices.append({"source": "jsonld", "value": float(o["price"])})
                        except:
                            pass

                    av_data[label] = {
                        "url": page.url, "size": sz, "title": title,
                        "ids": ad_ids[:5], "prices": prices[:5],
                        "has_price": len(prices) > 0
                    }
                    print(f"    prices={len(prices)}")
                    for pr in prices[:3]:
                        print(f"      {pr}")

                    if prices:
                        break  # Got price, stop
                except Exception as e:
                    print(f"    ERR: {str(e)[:60]}")
                time.sleep(2)

            # Save last page
            try:
                with open(os.path.join(HERE, "avito_pw2.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
            except:
                pass

            results["avito"] = av_data
        except Exception as e:
            results["avito"] = {"error": str(e)}
            print(f"  ERROR: {e}")
        finally:
            ctx.close()

        browser.close()

    # Save
    out = os.path.join(HERE, "pw2_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    for plat, data in results.items():
        if "error" in data:
            print(f"  {plat:6s}: ERROR {data['error'][:60]}")
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and "prices" in v:
                    hp = v.get("has_price", False)
                    pc = len(v.get("prices", []))
                    sz = v.get("html_size", v.get("size", "?"))
                    tt = v.get("title", "")[:40]
                    print(f"  {plat:6s}/{k}: price={hp} n={pc} size={sz} '{tt}'")
                elif isinstance(v, dict) and "prices" in v:
                    hp = v.get("has_price", False)
                    pc = len(v.get("prices", []))
                    sz = v.get("html_size", v.get("size", "?"))
                    tt = v.get("title", "")[:40]
                    print(f"  {plat:6s}: price={hp} n={pc} size={sz} '{tt}'")
        print()

    print(f"Saved -> {out}")
    print("DONE")

if __name__ == "__main__":
    main()
