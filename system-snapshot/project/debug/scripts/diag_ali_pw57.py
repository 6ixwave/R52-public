"""pw57: US-регион + новый товар + перехват PDP MTOP + TMD handling."""
import asyncio, json, os, re, sys, time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "pw_ali_profile_pw57")
OUT = os.path.join(BASE, "diag_ali_pw57_result.json")
PDP_OUT = os.path.join(BASE, "mtop_pw57_pdp_474088.json")

PRODUCT_ID = "1005010487474088"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
HOME_URL = "https://www.aliexpress.com/"

US_COOKIES = [
    {"name": "aep_usuc_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "_lang", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "intl_locale", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "xman_us_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
]

async def handle_tmd(page, log):
    """Обработка TMD/CAPTCHA: ищем iframe, кликаем checkbox, ждём."""
    log("Checking for TMD...")
    for attempt in range(3):
        try:
            # Ищем TMD iframe или overlay
            tmd_frame = None
            for frame in page.frames:
                if "tmd" in frame.url.lower() or "punish" in frame.url.lower():
                    tmd_frame = frame
                    break
            if not tmd_frame:
                # Проверка по DOM
                tmd_el = await page.query_selector("#tmd-page, .tmd-overlay, [id*='punish']")
                if tmd_el:
                    log(f"TMD element found (attempt {attempt+1})")
                    await asyncio.sleep(3)
                    continue
                else:
                    log("No TMD detected")
                    return False

            log(f"TMD frame found: {tmd_frame.url[:100]} (attempt {attempt+1})")
            await asyncio.sleep(2)

            # Пытаемся найти и кликнуть checkbox / button
            clicked = False
            selectors = [
                "#sliding", ".nc_iconfont", "#nc_1_wrapper", 
                "input[type='checkbox']", ".recaptcha-checkbox",
                "#recaptcha-checkbox", ".tmd-btn", "button[type='submit']",
                ".nc-container", "#nc_1_scale"
            ]
            for sel in selectors:
                try:
                    el = await tmd_frame.query_selector(sel)
                    if el:
                        await el.click()
                        log(f"Clicked '{sel}' in TMD frame")
                        clicked = True
                        break
                except Exception:
                    pass

            if not clicked:
                # Попробовать через page (не frame)
                for sel in selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            await el.click()
                            log(f"Clicked '{sel}' in page")
                            clicked = True
                            break
                    except Exception:
                        pass

            if not clicked:
                # Drag slider
                try:
                    slider = await tmd_frame.query_selector("#sliding, .nc_iconfont, #nc_1_scale .nc_iconfont")
                    if slider:
                        box = await slider.bounding_box()
                        if box:
                            await page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                            await asyncio.sleep(0.3)
                            await page.mouse.down()
                            await asyncio.sleep(0.2)
                            for i in range(20):
                                await page.mouse.move(box["x"] + box["width"]/2 + i*15, box["y"] + box["height"]/2, steps=2)
                                await asyncio.sleep(0.05)
                            await page.mouse.up()
                            log("Slider drag performed")
                            clicked = True
                except Exception as e:
                    log(f"Slider drag failed: {e}")

            # Ждём disappearance TMD
            await asyncio.sleep(5)
            tmd_gone = True
            for frame in page.frames:
                if "tmd" in frame.url.lower() or "punish" in frame.url.lower():
                    tmd_gone = False
                    break
            if tmd_gone:
                log("TMD resolved!")
                return True
        except Exception as e:
            log(f"TMD handling error: {e}")
        await asyncio.sleep(3)
    return False

async def main():
    log_lines = []
    def log(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log_lines.append(line)

    from playwright.async_api import async_playwright

    result = {
        "product_id": PRODUCT_ID,
        "url": PRODUCT_URL,
        "timestamp": datetime.now().isoformat(),
        "phase": "init",
        "region": "US",
        "cookies_set": US_COOKIES,
        "tmd_detected": False,
        "tmd_handled": False,
        "mtop_pdp_captured": False,
        "mtop_responses": [],
        "pdp_data": None,
        "errors": [],
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        # ── Step 1: Set US cookies BEFORE navigation ──
        log("Setting US cookies...")
        await page.context.add_cookies(US_COOKIES)
        await asyncio.sleep(1)

        # ── Step 2: Warm-up on homepage ──
        log("Warming up: navigating to homepage...")
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            # Scroll to trigger lazy content
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(2)
            await page.evaluate("window.scrollBy(0, -500)")
            log("Homepage loaded, cookies warmed")
        except Exception as e:
            log(f"Homepage load issue: {e}")

        # ── Step 3: Handle TMD on homepage if present ──
        tmd_hit = await handle_tmd(page, log)
        if tmd_hit:
            result["tmd_detected"] = True
            result["tmd_handled"] = True
            await asyncio.sleep(3)

        # ── Step 4: Navigate to product page ──
        log(f"Navigating to product: {PRODUCT_URL}")
        
        # MTOP interceptor
        mtop_bodies = []
        
        async def on_response(response):
            try:
                url = response.url
                if "mtop.aliexpress.pdp.pc.query" in url or "pdp.pc.query" in url:
                    body = await response.text()
                    mtop_bodies.append({
                        "url": url[:200],
                        "status": response.status,
                        "body_len": len(body),
                        "body_head": body[:500],
                        "body_full": body,
                    })
                    log(f"MTOP PDP captured! len={len(body)}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            await page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
            log("Product page DOM loaded")
        except Exception as e:
            log(f"Product page load: {e}")

        # Wait for MTOP to fire
        log("Waiting 12s for MTOP PDP...")
        await asyncio.sleep(12)

        # ── Step 5: Check TMD on product page ──
        tmd_hit2 = await handle_tmd(page, log)
        if tmd_hit2:
            result["tmd_detected"] = True
            result["tmd_handled"] = True
            log("TMD on product page handled, waiting for reload...")
            await asyncio.sleep(5)
            # Try reload
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(10)
            except Exception as e:
                log(f"Reload after TMD: {e}")

        # ── Step 6: Check MTOP responses ──
        result["mtop_pdp_captured"] = len(mtop_bodies) > 0
        result["mtop_responses"] = [
            {k: v for k, v in m.items() if k != "body_full"} 
            for m in mtop_bodies
        ]

        if mtop_bodies:
            # Save the PDP body
            pdp_body = mtop_bodies[0]["body_full"]
            with open(PDP_OUT, "w", encoding="utf-8") as f:
                f.write(pdp_body)
            log(f"PDP saved → {PDP_OUT} ({len(pdp_body)} bytes)")

            # Parse
            try:
                raw = pdp_body.strip()
                if raw.startswith("mtopjsonp"):
                    s = raw.index("(")
                    e = raw.rindex(")")
                    raw = raw[s+1:e]
                data = json.loads(raw)
                result["pdp_data"] = {
                    "api": data.get("api"),
                    "ret": data.get("ret"),
                    "result_keys": list(data.get("data", {}).get("result", {}).keys()),
                }
                # Deep extract
                res = data.get("data", {}).get("result", {})
                gd = res.get("GLOBAL_DATA", {}).get("globalData", {})
                result["pdp_data"]["globalData"] = {
                    "bigBossBan": gd.get("bigBossBan"),
                    "errorCode": gd.get("errorCode"),
                    "tip": gd.get("bigBossBanTip"),
                }
                # Price
                ts = res.get("targetSkuPriceInfo", {})
                result["pdp_data"]["price"] = {
                    "salePriceString": ts.get("salePriceString"),
                    "originalPrice": ts.get("originalPrice"),
                    "currency": ts.get("currency"),
                }
                # Images
                hip = res.get("HEADER_IMAGE_PC", {})
                imgs = hip.get("currentSkuImages", [])
                result["pdp_data"]["images_count"] = len(imgs)
                # SKU
                sku = res.get("SKUModule", {})
                result["pdp_data"]["sku_keys"] = list(sku.keys())[:10] if isinstance(sku, dict) else []
                # Category
                result["pdp_data"]["categoryPath"] = res.get("categoryPath")
                # Title
                result["pdp_data"]["title"] = res.get("title") or res.get("subject")
                # Delivery
                deliv = res.get("deliveryOptionPanelDisplayList", [])
                result["pdp_data"]["delivery_count"] = len(deliv) if isinstance(deliv, list) else 0
                log(f"PDP parsed. Keys: {result['pdp_data']['result_keys']}")
            except Exception as e:
                result["errors"].append(f"PDP parse: {e}")
                log(f"PDP parse error: {e}")
        else:
            log("No PDP MTOP captured. Trying DOM fallback...")
            # DOM fallback: extract what we can
            try:
                title = await page.title()
                h1 = await page.query_selector("h1")
                h1_text = await h1.inner_text() if h1 else None
                price_el = await page.query_selector("[class*='price'], [class*='Price']")
                price_text = await price_el.inner_text() if price_el else None
                result["pdp_data"] = {
                    "source": "DOM_fallback",
                    "title": title,
                    "h1": h1_text,
                    "price_text": price_text,
                    "result_keys": ["DOM_ONLY"],
                }
                log(f"DOM: title={title[:80]}, h1={h1_text[:80] if h1_text else None}, price={price_text}")
            except Exception as e:
                result["errors"].append(f"DOM fallback: {e}")

        # ── Step 7: Screenshot ──
        try:
            await page.screenshot(path=os.path.join(BASE, "diag_ali_pw57_final.png"))
            log("Screenshot saved")
        except Exception:
            pass

        # ── Step 8: Save cookies for debugging ──
        cookies = await page.context.cookies()
        cookie_summary = {c["name"]: c["value"][:80] for c in cookies if c["name"] in ("aep_usuc_f", "_lang", "intl_locale", "xman_us_f", "country")}
        result["cookies_after"] = cookie_summary

        await browser.close()

    result["phase"] = "done"
    result["log"] = log_lines[-30:]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[pw57] Done → {OUT}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
