"""pw59: US + gatewayAdapt + proper nested reCAPTCHA Enterprise solving."""
import asyncio, json, os, sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "pw_ali_profile_pw59")
OUT = os.path.join(BASE, "diag_ali_pw59_result.json")
PDP_OUT = os.path.join(BASE, "mtop_pw59_pdp_474088.json")

PRODUCT_ID = "1005010487474088"
PRODUCT_URL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html?gatewayAdapt=glo2usa"
HOME_URL = "https://www.aliexpress.com/"

US_COOKIES = [
    {"name": "aep_usuc_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "_lang", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "intl_locale", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
    {"name": "xman_us_f", "value": "[REDACTED_AUTH_SECRET]", "domain": ".aliexpress.com", "path": "/", "secure": True},
]

async def dump_all_frames(page, log, label):
    """Лог всех frames с URL."""
    log(f"[{label}] Total frames: {len(page.frames)}")
    for i, f in enumerate(page.frames):
        url_short = f.url[:130] if f.url else "(blank)"
        is_main = "MAIN" if f == page.main_frame else "  "
        log(f"  {is_main} [{i}] {url_short}")
        # child frames
        for j, cf in enumerate(f.child_frames):
            cf_url = cf.url[:130] if cf.url else "(blank)"
            log(f"       child[{j}] {cf_url}")

async def find_google_recaptcha_frame(page, log, label):
    """Находит Google reCAPTCHA Enterprise frame по URL."""
    for f in page.frames:
        url = f.url.lower()
        if "google.com/recaptcha" in url or "google.com/s/recaptcha" in url:
            log(f"[{label}] Found Google reCAPTCHA frame: {f.url[:150]}")
            return f
    return None

async def solve_recaptcha(page, log, stage):
    """
    Полное решение reCAPTCHA Enterprise:
    1. Ждём загрузку всех iframe (10s)
    2. Находим Google reCAPTCHA frame
    3. Клик по чекбоксу через locator
    4. Ждём разрешение
    """
    log(f"[{stage}] Waiting 10s for reCAPTCHA to fully load...")
    await asyncio.sleep(10)

    # Dump frames for debug
    await dump_all_frames(page, log, f"{stage}-before")

    # ── Method 1: frame_locator (most robust for nested iframes) ──
    # TMD iframe might be: iframe[src*='_____tmd_____'] or the page itself
    # reCAPTCHA: iframe[src*='google.com/recaptcha']
    
    # Try: page → iframe(tmd) → iframe(recaptcha) → .rc-button
    clicked = False
    
    # First: find the TMD container
    # If the page itself is TMD (full replacement), the reCAPTCHA iframe is a direct child
    # If TMD is an overlay iframe, we need to go into it first
    
    tmd_iframe_el = None
    try:
        # Look for TMD iframe in main page
        tmd_iframe_el = await page.query_selector("iframe[src*='_____tmd_____'], iframe[src*='punish']")
        if tmd_iframe_el:
            log(f"[{stage}] TMD iframe element found in main page")
    except Exception as e:
        log(f"[{stage}] TMD iframe search: {e}")

    if tmd_iframe_el:
        # reCAPTCHA is inside TMD iframe
        log(f"[{stage}] Using frame_locator: page → tmd_iframe → recaptcha_iframe → checkbox")
        try:
            tmd_fl = page.frame_locator("iframe[src*='_____tmd_____'], iframe[src*='punish']")
            # Inside TMD, find reCAPTCHA iframe
            rc_fl = tmd_fl.frame_locator("iframe[src*='google.com/recaptcha'], iframe[src*='recaptcha/enterprise']")
            # Click checkbox
            for sel in [".rc-button", "span#recaptcha-anchor", "span[role='checkbox']", "label", "input[type='checkbox']"]:
                try:
                    el = rc_fl.locator(sel).first
                    if await el.count() > 0:
                        await el.click(timeout=5000)
                        clicked = True
                        log(f"[{stage}] Clicked '{sel}' via frame_locator")
                        break
                except Exception as e:
                    log(f"[{stage}]   fl sel '{sel}': {e}")
        except Exception as e:
            log(f"[{stage}] frame_locator approach failed: {e}")

    if not clicked:
        # ── Method 2: Direct frame search ──
        log(f"[{stage}] Trying direct frame search...")
        rc_frame = await find_google_recaptcha_frame(page, log, stage)
        if rc_frame:
            for sel in [".rc-button", "span#recaptcha-anchor", "span[role='checkbox']", "label", "input[type='checkbox']", ".rcbxd"]:
                try:
                    el = rc_frame.locator(sel).first
                    if await el.count() > 0:
                        await el.click(timeout=5000)
                        clicked = True
                        log(f"[{stage}] Clicked '{sel}' via frame locator")
                        break
                except Exception as e:
                    log(f"[{stage}]   frame sel '{sel}': {e}")
        else:
            log(f"[{stage}] No Google reCAPTCHA frame found")

    if not clicked:
        # ── Method 3: Coordinate click ──
        # reCAPTCHA Enterprise widget is typically 304x78px, checkbox on the left
        log(f"[{stage}] Trying coordinate click...")
        try:
            # Find the reCAPTCHA container in DOM (might be in main frame or TMD frame)
            rc_container = await page.query_selector(
                ".g-recaptcha, #captcha, [class*='recaptcha'], div[style*='304px']"
            )
            if rc_container:
                box = await rc_container.bounding_box()
                if box:
                    # Checkbox is ~30px from left, centered vertically
                    cx = box["x"] + 30
                    cy = box["y"] + box["height"] / 2
                    log(f"[{stage}] Clicking at ({cx:.0f}, {cy:.0f}) in container {box}")
                    await page.mouse.click(cx, cy)
                    clicked = True
            else:
                # Try in TMD frame
                for f in page.frames:
                    if "_____tmd_____" in f.url.lower():
                        rc_el = await f.query_selector(".g-recaptcha, #captcha, iframe[src*='recaptcha']")
                        if rc_el:
                            box = await rc_el.bounding_box()
                            if box:
                                cx = box["x"] + 30
                                cy = box["y"] + box["height"] / 2
                                log(f"[{stage}] Clicking at ({cx:.0f}, {cy:.0f}) in TMD frame")
                                await page.mouse.click(cx, cy)
                                clicked = True
                                break
        except Exception as e:
            log(f"[{stage}] Coord click failed: {e}")

    if not clicked:
        log(f"[{stage}] WARNING: Could not click reCAPTCHA checkbox")
        return False

    # ── Wait for resolution ──
    log(f"[{stage}] reCAPTCHA clicked, waiting 18s for resolution...")
    await asyncio.sleep(18)

    # Check if TMD is gone
    tmd_gone = True
    for f in page.frames:
        url = f.url.lower()
        if "_____tmd_____" in url and "punish" in url:
            # Could be the main frame if page was replaced
            if f != page.main_frame:
                tmd_gone = False
                break
    # Also check main frame URL
    if "_____tmd_____" in page.url.lower() and "punish" in page.url.lower():
        tmd_gone = False

    if tmd_gone:
        log(f"[{stage}] TMD resolved! Page URL: {page.url[:100]}")
        return True
    else:
        log(f"[{stage}] TMD still present. URL: {page.url[:100]}")
        # Screenshot for debug
        try:
            await page.screenshot(path=os.path.join(BASE, f"diag_ali_pw59_after_rc_{stage}.png"))
        except Exception:
            pass
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
        "tmd_homepage": False,
        "tmd_product": False,
        "tmd_handled": False,
        "mtop_pdp_captured": False,
        "mtop_responses": [],
        "pdp_data": None,
        "dom_data": None,
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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        # ── 1. US cookies ──
        log("Setting US cookies...")
        await page.context.add_cookies(US_COOKIES)
        await asyncio.sleep(1)

        # ── 2. Прогрев на главной (20 сек) ──
        log("Warming up: homepage (20s)...")
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"Homepage goto: {e}")

        await asyncio.sleep(6)
        try:
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(4)
            await page.evaluate("window.scrollBy(0, -300)")
            await asyncio.sleep(4)
            await page.evaluate("window.scrollBy(0, 200)")
            await asyncio.sleep(6)
        except Exception:
            pass
        log("Homepage warm-up done")

        # ── 3. TMD на главной ──
        # Check if TMD appeared
        has_tmd = any("_____tmd_____" in f.url.lower() and "punish" in f.url.lower() for f in page.frames)
        if has_tmd:
            log("TMD detected on homepage, solving...")
            tmd_home = await solve_recaptcha(page, log, "home")
            result["tmd_homepage"] = True
            result["tmd_handled"] = tmd_home
            if tmd_home:
                await asyncio.sleep(5)
        else:
            log("No TMD on homepage")

        # ── 4. Навигация на товар ──
        log(f"Navigating to: {PRODUCT_URL}")

        mtop_bodies = []

        async def on_response(response):
            try:
                url = response.url
                if "pdp.pc.query" in url:
                    body = await response.text()
                    mtop_bodies.append({
                        "url": url[:300],
                        "status": response.status,
                        "body_len": len(body),
                        "body_head": body[:500],
                        "body_full": body,
                    })
                    log(f"MTOP PDP CAPTURED! len={len(body)}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            await page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=45000)
            log("Product page DOM loaded")
        except Exception as e:
            log(f"Product goto: {e}")

        # Ждём MTOP + TMD
        log("Waiting 8s for MTOP/TMD...")
        await asyncio.sleep(8)

        # ── 5. TMD на товаре ──
        has_tmd2 = any("_____tmd_____" in f.url.lower() and "punish" in f.url.lower() for f in page.frames)
        if "_____tmd_____" in page.url.lower():
            has_tmd2 = True

        if has_tmd2:
            log("TMD detected on product page, solving...")
            tmd_prod = await solve_recaptcha(page, log, "product")
            result["tmd_product"] = True
            result["tmd_handled"] = tmd_prod
            if tmd_prod:
                log("Product TMD resolved! Reloading...")
                await asyncio.sleep(3)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    log("Reload done, waiting 15s for MTOP...")
                    await asyncio.sleep(15)
                except Exception as e:
                    log(f"Reload: {e}")
        else:
            log("No TMD on product page")
            log("Waiting 10s more for MTOP...")
            await asyncio.sleep(10)

        # ── 6. Проверка MTOP ──
        result["mtop_pdp_captured"] = len(mtop_bodies) > 0
        result["mtop_responses"] = [
            {k: v for k, v in m.items() if k != "body_full"}
            for m in mtop_bodies
        ]

        if mtop_bodies:
            pdp_body = mtop_bodies[0]["body_full"]
            with open(PDP_OUT, "w", encoding="utf-8") as f:
                f.write(pdp_body)
            log(f"PDP saved → {PDP_OUT} ({len(pdp_body)} bytes)")

            try:
                raw = pdp_body.strip()
                if raw.startswith("mtopjsonp"):
                    s = raw.index("(")
                    e = raw.rindex(")")
                    raw = raw[s+1:e]
                data = json.loads(raw)
                res = data.get("data", {}).get("result", {})
                gd = res.get("GLOBAL_DATA", {}).get("globalData", {})

                result["pdp_data"] = {
                    "api": data.get("api"),
                    "ret": data.get("ret"),
                    "result_keys": list(res.keys()),
                    "globalData": {
                        "bigBossBan": gd.get("bigBossBan"),
                        "errorCode": gd.get("errorCode"),
                        "tip": gd.get("bigBossBanTip"),
                    },
                }

                ts = res.get("targetSkuPriceInfo", {})
                result["pdp_data"]["price"] = {
                    "salePriceString": ts.get("salePriceString"),
                    "originalPrice": ts.get("originalPrice"),
                    "currency": ts.get("currency"),
                    "salePriceLocal": ts.get("salePriceLocal"),
                }

                hip = res.get("HEADER_IMAGE_PC", {})
                imgs = hip.get("currentSkuImages", [])
                result["pdp_data"]["images"] = {
                    "count": len(imgs),
                    "first_3": [i.get("imageUrl", "")[:100] for i in imgs[:3]],
                }

                sku = res.get("SKUModule", {})
                if isinstance(sku, dict):
                    paths = sku.get("skuPaths", [])
                    result["pdp_data"]["sku"] = {
                        "keys": list(sku.keys())[:15],
                        "paths_count": len(paths),
                    }

                result["pdp_data"]["category"] = {
                    "categoryPath": res.get("categoryPath"),
                    "category1": res.get("category1"),
                    "category3": res.get("category3"),
                }
                result["pdp_data"]["title"] = res.get("title") or res.get("subject")
                deliv = res.get("deliveryOptionPanelDisplayList", [])
                result["pdp_data"]["delivery_count"] = len(deliv) if isinstance(deliv, list) else 0

                log(f"PDP parsed. Keys: {result['pdp_data']['result_keys']}")
                log(f"  bigBossBan={gd.get('bigBossBan')}, err={gd.get('errorCode')}")
                log(f"  price={ts.get('salePriceString')}, imgs={len(imgs)}")
            except Exception as e:
                result["errors"].append(f"PDP parse: {e}")
                log(f"PDP parse error: {e}")
        else:
            log("No PDP MTOP. Trying DOM fallback...")
            try:
                title = await page.title()
                h1_el = await page.query_selector("h1")
                h1_text = (await h1_el.inner_text()).strip() if h1_el else None
                price_el = await page.query_selector(
                    "[class*='price'], [class*='Price'], .pdp-price, [class*='price-wrap']"
                )
                price_text = (await price_el.inner_text()).strip() if price_el else None

                # JSON-LD
                jsonld = None
                try:
                    scripts = await page.query_selector_all("script[type='application/ld+json']")
                    for s in scripts:
                        txt = (await s.inner_text()).strip()
                        if txt and len(txt) > 50:
                            jsonld = txt[:2000]
                            break
                except Exception:
                    pass

                # Meta tags
                meta_desc = await page.query_selector("meta[name='description']")
                meta_desc_val = (await meta_desc.get_attribute("content"))[:200] if meta_desc else None

                result["dom_data"] = {
                    "title": title,
                    "h1": h1_text,
                    "price_text": price_text,
                    "jsonld_head": jsonld[:500] if jsonld else None,
                    "meta_description": meta_desc_val,
                    "page_url": page.url,
                }
                log(f"DOM: title={title[:80]!r}, h1={h1_text[:80] if h1_text else None!r}, price={price_text!r}")
            except Exception as e:
                result["errors"].append(f"DOM: {e}")

        # ── 7. Скриншот ──
        try:
            await page.screenshot(path=os.path.join(BASE, "diag_ali_pw59_final.png"))
            log("Final screenshot saved")
        except Exception:
            pass

        # ── 8. Cookies ──
        cookies = await page.context.cookies()
        result["cookies_after"] = {
            c["name"]: c["value"][:100]
            for c in cookies
            if c["name"] in ("aep_usuc_f", "_lang", "intl_locale", "xman_us_f", "country", "xman_us_t")
        }

        await browser.close()

    result["phase"] = "done"
    result["log"] = log_lines[-50:]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[pw59] Done → {OUT}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
